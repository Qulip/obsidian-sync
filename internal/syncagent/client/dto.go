package client

import (
	"encoding/base64"
	"encoding/json"
	"fmt"
	"time"
)

const DefaultPageLimit = 500

type FileRef struct {
	VaultID string
	Path    string
}

type RegisterDeviceRequest struct {
	DeviceID   string  `json:"device_id"`
	DeviceName *string `json:"device_name,omitempty"`
}

type RegisterDeviceData struct {
	VaultID    string `json:"vault_id"`
	DeviceID   string `json:"device_id"`
	Registered bool   `json:"registered"`
}

type ChangesRequest struct {
	Since    int
	DeviceID string
	Limit    int
}

type SyncChangeItem struct {
	Revision    int     `json:"revision"`
	Path        string  `json:"path"`
	EventType   string  `json:"event_type"`
	ContentHash *string `json:"content_hash"`
	Deleted     bool    `json:"deleted"`
	CreatedAt   APITime `json:"created_at"`
}

type SyncChangesData struct {
	VaultID    string           `json:"vault_id"`
	FromCursor int              `json:"from_cursor"`
	ToCursor   int              `json:"to_cursor"`
	Changes    []SyncChangeItem `json:"changes"`
}

type StatusRequest struct {
	DeviceID string
}

type SyncStatusData struct {
	VaultID                string  `json:"vault_id"`
	DeviceID               *string `json:"device_id"`
	ServerRevision         int     `json:"server_revision"`
	DeviceLastSeenRevision *int    `json:"device_last_seen_revision"`
	PendingChanges         int     `json:"pending_changes"`
	OpenConflicts          int     `json:"open_conflicts"`
	PendingVectorizingJobs int     `json:"pending_vectorizing_jobs"`
}

type FileContentData struct {
	VaultID     string `json:"vault_id"`
	Path        string `json:"path"`
	Revision    int    `json:"revision"`
	ContentHash string `json:"content_hash"`
	Content     string `json:"content"`
	Encoding    string `json:"encoding"`
	Deleted     bool   `json:"deleted"`
}

// DecodedContent returns the file content as raw bytes, decoding it first
// when the server sent it as base64 (attachments such as images/PDFs). The
// server hashes content_hash over these raw bytes, so callers must hash and
// write this return value rather than the raw Content string.
func (f FileContentData) DecodedContent() ([]byte, error) {
	if f.Encoding == "base64" {
		decoded, err := base64.StdEncoding.DecodeString(f.Content)
		if err != nil {
			return nil, fmt.Errorf("decode base64 content: %w", err)
		}
		return decoded, nil
	}
	return []byte(f.Content), nil
}

type PutFileRequest struct {
	DeviceID     string `json:"device_id"`
	BaseRevision int    `json:"base_revision"`
	ContentHash  string `json:"content_hash"`
	Content      string `json:"content"`
	// Encoding is "base64" for attachments, or empty/"utf8" for markdown.
	// omitempty lets markdown pushes omit the field entirely so the server
	// applies its own "utf8" default rather than rejecting an explicit "".
	Encoding string `json:"encoding,omitempty"`
}

type PutFileData struct {
	VaultID     string `json:"vault_id"`
	Path        string `json:"path"`
	Revision    int    `json:"revision"`
	ContentHash string `json:"content_hash"`
}

type DeleteFileRequest struct {
	DeviceID     string `json:"device_id"`
	BaseRevision int    `json:"base_revision"`
}

type DeleteFileData struct {
	VaultID  string `json:"vault_id"`
	Path     string `json:"path"`
	Revision int    `json:"revision"`
	Deleted  bool   `json:"deleted"`
}

type APITime struct {
	time.Time
}

func (t *APITime) UnmarshalJSON(data []byte) error {
	var raw string
	if err := json.Unmarshal(data, &raw); err != nil {
		return fmt.Errorf("decode api timestamp: %w", err)
	}
	for _, layout := range []string{
		time.RFC3339Nano,
		"2006-01-02T15:04:05.999999",
		"2006-01-02T15:04:05",
	} {
		parsed, err := time.Parse(layout, raw)
		if err == nil {
			t.Time = parsed
			return nil
		}
	}
	return fmt.Errorf("decode api timestamp %q: %w", raw, ErrAPI)
}
