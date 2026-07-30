package engine

import (
	"encoding/base64"
	"errors"
	"fmt"

	"github.com/Qulip/obsidian-sync/internal/syncagent/client"
	"github.com/Qulip/obsidian-sync/internal/syncagent/conflict"
	"github.com/Qulip/obsidian-sync/internal/syncagent/rules"
)

// localWinsMaxAttempts bounds the *additional* retries local-wins performs
// after the first PUT/DELETE attempt (the one that produced the conflict
// being resolved) already failed, so a path that keeps changing underneath
// us cannot loop forever. Mirrors resolution.LOCAL_WINS_MAX_ATTEMPTS in the
// Python agent.
const localWinsMaxAttempts = 2

type localWinsUpsertOutcome struct {
	resolved    bool
	revision    int
	contentHash string
}

// resolveLocalWinsUpsert retries pushing local content as the winner, up to
// a bounded attempt count. baseRevision is the server revision to try first
// (0 to recreate a file the server has soft-deleted). On another 409, the
// reported server_revision becomes the next attempt's base so a single
// concurrent update in between does not immediately doom the retry. Mirrors
// resolution.resolve_local_wins_upsert.
func (r *syncRun) resolveLocalWinsUpsert(path string, content []byte, baseRevision int) (localWinsUpsertOutcome, error) {
	contentHash := hashBytes(content)
	revision := baseRevision
	for attempt := 0; attempt < localWinsMaxAttempts; attempt++ {
		putRequest := client.PutFileRequest{
			DeviceID:     r.cfg.DeviceID,
			BaseRevision: revision,
			ContentHash:  contentHash,
		}
		if rules.IsAttachmentPath(path) {
			putRequest.Content = base64.StdEncoding.EncodeToString(content)
			putRequest.Encoding = "base64"
		} else {
			putRequest.Content = string(content)
		}
		result, err := r.syncClient.PutFile(r.ctx, client.FileRef{VaultID: r.cfg.VaultID, Path: path}, putRequest)
		if err != nil {
			var conflictErr *client.ConflictError
			if errors.As(err, &conflictErr) {
				revision = intDetail(conflictErr.Details["server_revision"])
				continue
			}
			return localWinsUpsertOutcome{}, fmt.Errorf("local-wins retry put file %s: %w", path, err)
		}
		return localWinsUpsertOutcome{resolved: true, revision: result.Revision, contentHash: result.ContentHash}, nil
	}
	return localWinsUpsertOutcome{}, nil
}

// resolveLocalWinsDelete retries deleting on the server as the winner, up to
// a bounded attempt count. Mirrors resolution.resolve_local_wins_delete.
func (r *syncRun) resolveLocalWinsDelete(path string, baseRevision int) (bool, error) {
	revision := baseRevision
	for attempt := 0; attempt < localWinsMaxAttempts; attempt++ {
		_, err := r.syncClient.DeleteFile(r.ctx, client.FileRef{VaultID: r.cfg.VaultID, Path: path}, client.DeleteFileRequest{
			DeviceID:     r.cfg.DeviceID,
			BaseRevision: revision,
		})
		if err != nil {
			var conflictErr *client.ConflictError
			if errors.As(err, &conflictErr) {
				revision = intDetail(conflictErr.Details["server_revision"])
				continue
			}
			return false, fmt.Errorf("local-wins retry delete file %s: %w", path, err)
		}
		return true, nil
	}
	return false, nil
}

// writeLocalBackup preserves local content discarded by a remote-wins
// auto-resolution before it is overwritten or deleted: the server's own
// version history makes the server side always recoverable already; this
// file is what makes the local side recoverable too. Dispatches to the
// markdown or attachment writer based on path, mirroring the
// write_local_backup_file / write_attachment_local_backup_file split in
// sync_agent.conflict_resolution.
func (r *syncRun) writeLocalBackup(path string, content []byte, serverRevision int) (string, error) {
	if rules.IsAttachmentPath(path) {
		return conflict.WriteBinaryLocalBackupFile(conflict.BinaryLocalBackupRequest{
			VaultRoot: r.cfg.VaultRoot,
			Path:      path,
			DeviceID:  r.cfg.DeviceID,
			Content:   content,
			Now:       r.now(),
		})
	}
	return conflict.WriteLocalBackupFile(conflict.LocalBackupRequest{
		VaultRoot:      r.cfg.VaultRoot,
		Path:           path,
		DeviceID:       r.cfg.DeviceID,
		ServerRevision: serverRevision,
		LocalContent:   string(content),
		Now:            r.now(),
	})
}
