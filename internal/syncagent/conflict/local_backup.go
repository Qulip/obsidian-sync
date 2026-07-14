package conflict

import (
	"fmt"
	"os"
	"path"
	"path/filepath"
	"strings"
	"time"
)

// LocalBackupRequest describes a markdown local-backup write: the local
// content a remote-wins auto-resolution is about to discard, preserved
// before the file is overwritten with (or deleted in favor of) the server's
// content. Mirrors sync_agent.conflict.write_local_backup_file.
type LocalBackupRequest struct {
	VaultRoot      string
	Path           string
	DeviceID       string
	ServerRevision int
	LocalContent   string
	Now            time.Time
}

// LocalBackupFilename mirrors sync_agent.conflict.local_backup_filename: the
// name deliberately contains the `.conflict.` marker used by
// rules.IsConflictFile so the backup is excluded from scanning, pushing, and
// server-side vectorizing just like a manual conflict file, while the
// `local-backup` prefix keeps it distinguishable from one.
func LocalBackupFilename(filePath string, deviceID string, moment time.Time) string {
	stem := strings.TrimSuffix(path.Base(filePath), path.Ext(filePath))
	timestamp := moment.Format("20060102-150405")
	return fmt.Sprintf("%s.local-backup.conflict.%s.%s.md", stem, deviceID, timestamp)
}

// WriteLocalBackupFile preserves local content discarded by a remote-wins
// auto-resolution. Called instead of overwriting or deleting local content
// silently: the server keeps a full version history so the server side is
// always recoverable, but the locally-discarded content only survives here.
// Unlike WriteFile, there is no dedup: each remote-wins resolution is a
// distinct event worth preserving on its own.
func WriteLocalBackupFile(req LocalBackupRequest) (string, error) {
	moment := req.Now
	if moment.IsZero() {
		moment = time.Now().Local()
	}

	destination, err := resolveConflictDestination(req.VaultRoot, req.Path, LocalBackupFilename(req.Path, req.DeviceID, moment))
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return "", fmt.Errorf("create conflict directory: %w", err)
	}
	body := fmt.Sprintf(
		"# Sync Conflict (auto-resolved: remote-wins)\n\n"+
			"- Path: %s\n"+
			"- Device: %s\n"+
			"- Server revision applied: %d\n"+
			"- Created at: %s\n\n"+
			"## Local Version (discarded)\n\n"+
			"%s\n",
		req.Path,
		req.DeviceID,
		req.ServerRevision,
		moment.Format(time.RFC3339),
		req.LocalContent,
	)
	if err := writeTextAtomic(destination, body); err != nil {
		return "", err
	}
	return destination, nil
}

// BinaryLocalBackupRequest is the attachment counterpart of
// LocalBackupRequest: binary content cannot be embedded in a text report, so
// the discarded local bytes are written out as their own file, extension
// preserved.
type BinaryLocalBackupRequest struct {
	VaultRoot string
	Path      string
	DeviceID  string
	Content   []byte
	Now       time.Time
}

// AttachmentLocalBackupFilename mirrors
// sync_agent.conflict.attachment_local_backup_filename.
func AttachmentLocalBackupFilename(filePath string, deviceID string, moment time.Time) string {
	stem := strings.TrimSuffix(path.Base(filePath), path.Ext(filePath))
	timestamp := moment.Format("20060102-150405")
	return fmt.Sprintf("%s.local-backup.conflict.%s.%s%s", stem, deviceID, timestamp, path.Ext(filePath))
}

// WriteBinaryLocalBackupFile is the attachment counterpart of
// WriteLocalBackupFile. No dedup, matching WriteLocalBackupFile.
func WriteBinaryLocalBackupFile(req BinaryLocalBackupRequest) (string, error) {
	moment := req.Now
	if moment.IsZero() {
		moment = time.Now().Local()
	}

	destination, err := resolveConflictDestination(req.VaultRoot, req.Path, AttachmentLocalBackupFilename(req.Path, req.DeviceID, moment))
	if err != nil {
		return "", err
	}
	if err := os.MkdirAll(filepath.Dir(destination), 0o755); err != nil {
		return "", fmt.Errorf("create conflict directory: %w", err)
	}
	if err := writeAtomic(destination, req.Content); err != nil {
		return "", err
	}
	return destination, nil
}
