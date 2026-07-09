package conflict

import (
	"fmt"
	"os"
	"path"
	"path/filepath"
	"strconv"
	"strings"
	"time"
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

func WriteFile(req Request) (string, error) {
	moment := req.Now
	if moment.IsZero() {
		moment = time.Now().Local()
	}

	destination, err := destinationPath(req, moment)
	if err != nil {
		return "", err
	}

	existing, err := findExisting(
		filepath.Dir(destination),
		strings.TrimSuffix(path.Base(req.Path), path.Ext(req.Path)),
		req.DeviceID,
		req.ServerRevision,
	)
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
	parent := path.Dir(req.Path)
	name := Filename(req.Path, req.DeviceID, moment)
	relative := name
	if parent != "." && parent != "/" {
		relative = path.Join(parent, name)
	}
	cleanRelative := filepath.Clean(filepath.FromSlash(relative))
	if filepath.IsAbs(cleanRelative) || cleanRelative == ".." || strings.HasPrefix(cleanRelative, ".."+string(filepath.Separator)) {
		return "", fmt.Errorf("conflict path escapes vault: %s", req.Path)
	}
	root, err := filepath.Abs(req.VaultRoot)
	if err != nil {
		return "", fmt.Errorf("resolve vault root: %w", err)
	}
	return filepath.Join(root, cleanRelative), nil
}

func findExisting(directory string, stem string, deviceID string, serverRevision int) (string, error) {
	entries, err := os.ReadDir(directory)
	if os.IsNotExist(err) {
		return "", nil
	}
	if err != nil {
		return "", fmt.Errorf("read conflict directory: %w", err)
	}
	prefix := fmt.Sprintf("%s.conflict.%s.", stem, deviceID)
	for _, entry := range entries {
		if entry.IsDir() {
			continue
		}
		name := entry.Name()
		if !strings.HasPrefix(name, prefix) || !strings.HasSuffix(name, ".md") {
			continue
		}
		candidate := filepath.Join(directory, name)
		revision, ok := parseServerRevision(candidate)
		if ok && revision == serverRevision {
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

func writeTextAtomic(destination string, content string) (err error) {
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
	if _, err := temp.WriteString(content); err != nil {
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
