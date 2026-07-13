package conflict

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"os"
	"path"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/Qulip/obsidian-sync/internal/syncagent/vaultfs"
)

const (
	ServerDeletedPlaceholder = "(file is deleted on the server)"
	LocalDeletedPlaceholder  = "(file was deleted locally)"
)

const serverRevisionPrefix = "- Server revision:"

type Request struct {
	VaultRoot          string
	Path               string
	DeviceID           string
	ClientBaseRevision int
	ServerRevision     int
	LocalContent       string
	ServerContent      string
	Now                time.Time
}

func Filename(filePath string, deviceID string, moment time.Time) string {
	stem := strings.TrimSuffix(path.Base(filePath), path.Ext(filePath))
	timestamp := moment.Format("20060102-150405")
	return fmt.Sprintf("%s.conflict.%s.%s.md", stem, deviceID, timestamp)
}

// AttachmentFilename mirrors Filename but keeps the original extension:
// binary content can't show both versions inline like a markdown conflict
// doc does, so the "other" version is written out as its own file (see
// docs/sync-agent.md, "충돌 파일 (확장자 유지)").
func AttachmentFilename(filePath string, deviceID string, moment time.Time) string {
	stem := strings.TrimSuffix(path.Base(filePath), path.Ext(filePath))
	timestamp := moment.Format("20060102-150405")
	return fmt.Sprintf("%s.conflict.%s.%s%s", stem, deviceID, timestamp, path.Ext(filePath))
}

type BinaryRequest struct {
	VaultRoot string
	Path      string
	DeviceID  string
	// Content is the "other" version's raw bytes: the server's bytes on a
	// write conflict, or the local bytes on a server-deleted conflict.
	Content []byte
	Now     time.Time
}

// WriteBinaryFile writes an attachment conflict copy, deduplicated by
// content hash since binary files have no text marker to parse the way
// WriteFile's "- Server revision:" line does for markdown.
func WriteBinaryFile(req BinaryRequest) (string, error) {
	moment := req.Now
	if moment.IsZero() {
		moment = time.Now().Local()
	}

	destination, err := resolveConflictDestination(req.VaultRoot, req.Path, AttachmentFilename(req.Path, req.DeviceID, moment))
	if err != nil {
		return "", err
	}

	contentHash := hashBytes(req.Content)
	existing, err := findExistingBinary(existingBinaryConflictQuery{
		directory:   filepath.Dir(destination),
		stem:        strings.TrimSuffix(path.Base(req.Path), path.Ext(req.Path)),
		ext:         path.Ext(req.Path),
		deviceID:    req.DeviceID,
		contentHash: contentHash,
	})
	if err != nil {
		return "", err
	}
	if existing != "" {
		return existing, nil
	}

	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return "", fmt.Errorf("create conflict directory: %w", err)
	}
	if err := writeAtomic(destination, req.Content); err != nil {
		return "", err
	}
	return destination, nil
}

type existingBinaryConflictQuery struct {
	directory   string
	stem        string
	ext         string
	deviceID    string
	contentHash string
}

func findExistingBinary(query existingBinaryConflictQuery) (string, error) {
	entries, err := os.ReadDir(query.directory)
	if os.IsNotExist(err) {
		return "", nil
	}
	if err != nil {
		return "", fmt.Errorf("read conflict directory: %w", err)
	}
	prefix := fmt.Sprintf("%s.conflict.%s.", query.stem, query.deviceID)
	for _, entry := range entries {
		if entry.IsDir() || entry.Type()&os.ModeSymlink != 0 {
			continue
		}
		name := entry.Name()
		if !strings.HasPrefix(name, prefix) || !strings.HasSuffix(name, query.ext) {
			continue
		}
		candidate := filepath.Join(query.directory, name)
		data, err := os.ReadFile(candidate)
		if err != nil {
			continue
		}
		if hashBytes(data) == query.contentHash {
			return candidate, nil
		}
	}
	return "", nil
}

func hashBytes(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func WriteFile(req Request) (string, error) {
	moment := req.Now
	if moment.IsZero() {
		moment = time.Now().Local()
	}

	destination, err := destinationPath(req, moment)
	if err != nil {
		return "", err
	}

	existing, err := findExisting(existingConflictQuery{
		directory:      filepath.Dir(destination),
		stem:           strings.TrimSuffix(path.Base(req.Path), path.Ext(req.Path)),
		deviceID:       req.DeviceID,
		serverRevision: req.ServerRevision,
	})
	if err != nil {
		return "", err
	}
	if existing != "" {
		return existing, nil
	}

	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return "", fmt.Errorf("create conflict directory: %w", err)
	}
	body := formatBody(req, moment)
	if err := writeTextAtomic(destination, body); err != nil {
		return "", err
	}
	return destination, nil
}

func destinationPath(req Request, moment time.Time) (string, error) {
	return resolveConflictDestination(req.VaultRoot, req.Path, Filename(req.Path, req.DeviceID, moment))
}

func resolveConflictDestination(vaultRoot string, filePath string, name string) (string, error) {
	parent := path.Dir(filePath)
	relative := name
	if parent != "." && parent != "/" {
		relative = path.Join(parent, name)
	}
	cleanRelative := filepath.Clean(filepath.FromSlash(relative))
	if filepath.IsAbs(cleanRelative) || cleanRelative == ".." || strings.HasPrefix(cleanRelative, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("conflict path escapes vault: %s", filePath)
	}
	destination, err := vaultfs.SafePath(vaultRoot, relative)
	if err != nil {
		return "", fmt.Errorf("resolve conflict path: %w", err)
	}
	return destination, nil
}

type existingConflictQuery struct {
	directory      string
	stem           string
	deviceID       string
	serverRevision int
}

func findExisting(query existingConflictQuery) (string, error) {
	entries, err := os.ReadDir(query.directory)
	if os.IsNotExist(err) {
		return "", nil
	}
	if err != nil {
		return "", fmt.Errorf("read conflict directory: %w", err)
	}
	prefix := fmt.Sprintf("%s.conflict.%s.", query.stem, query.deviceID)
	for _, entry := range entries {
		if entry.IsDir() || entry.Type()&os.ModeSymlink != 0 {
			continue
		}
		name := entry.Name()
		if !strings.HasPrefix(name, prefix) || !strings.HasSuffix(name, ".md") {
			continue
		}
		candidate := filepath.Join(query.directory, name)
		revision, ok := parseServerRevision(candidate)
		if ok && revision == query.serverRevision {
			return candidate, nil
		}
	}
	return "", nil
}

func parseServerRevision(filePath string) (int, bool) {
	body, err := os.ReadFile(filePath)
	if err != nil {
		return 0, false
	}
	for _, line := range strings.Split(string(body), "\n") {
		if !strings.HasPrefix(line, serverRevisionPrefix) {
			continue
		}
		value := strings.TrimSpace(strings.TrimPrefix(line, serverRevisionPrefix))
		revision, err := strconv.Atoi(value)
		if err != nil {
			return 0, false
		}
		return revision, true
	}
	return 0, false
}

func formatBody(req Request, moment time.Time) string {
	return fmt.Sprintf(
		"# Sync Conflict\n\n"+
			"- Path: %s\n"+
			"- Device: %s\n"+
			"- Client base revision: %d\n"+
			"- Server revision: %d\n"+
			"- Created at: %s\n\n"+
			"## Local Version\n\n"+
			"%s\n\n"+
			"## Server Version\n\n"+
			"%s\n",
		req.Path,
		req.DeviceID,
		req.ClientBaseRevision,
		req.ServerRevision,
		moment.Format(time.RFC3339),
		req.LocalContent,
		req.ServerContent,
	)
}

func writeTextAtomic(destination string, content string) error {
	return writeAtomic(destination, []byte(content))
}

func writeAtomic(destination string, data []byte) (err error) {
	temp, err := os.CreateTemp(filepath.Dir(destination), "."+filepath.Base(destination)+".tmp-*")
	if err != nil {
		return fmt.Errorf("create conflict temp file: %w", err)
	}
	tempName := temp.Name()
	defer func() {
		if err != nil {
			_ = os.Remove(tempName)
		}
	}()
	if _, err := temp.Write(data); err != nil {
		_ = temp.Close()
		return fmt.Errorf("write conflict temp file: %w", err)
	}
	if err := temp.Close(); err != nil {
		return fmt.Errorf("close conflict temp file: %w", err)
	}
	if err := os.Rename(tempName, destination); err != nil {
		return fmt.Errorf("replace conflict file: %w", err)
	}
	return nil
}
