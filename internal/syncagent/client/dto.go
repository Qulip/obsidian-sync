package client

import "time"

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
	Revision    int       `json:"revision"`
	Path        string    `json:"path"`
	EventType   string    `json:"event_type"`
	ContentHash *string   `json:"content_hash"`
	Deleted     bool      `json:"deleted"`
	CreatedAt   time.Time `json:"created_at"`
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
	Deleted     bool   `json:"deleted"`
}

type PutFileRequest struct {
	DeviceID     string `json:"device_id"`
	BaseRevision int    `json:"base_revision"`
	ContentHash  string `json:"content_hash"`
	Content      string `json:"content"`
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
