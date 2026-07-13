package engine

import (
	"encoding/base64"
	"errors"
	"fmt"
	"net/http"
	"os"
	"time"

	"github.com/Qulip/obsidian-sync/internal/syncagent/client"
	"github.com/Qulip/obsidian-sync/internal/syncagent/conflict"
	"github.com/Qulip/obsidian-sync/internal/syncagent/manifest"
	"github.com/Qulip/obsidian-sync/internal/syncagent/rules"
	"github.com/Qulip/obsidian-sync/internal/syncagent/scanner"
)

type pushFile struct {
	path        string
	base        int
	localText   string
	conflictErr *client.ConflictError
}

func (r *syncRun) push(local scanner.LocalChanges, skipPaths map[string]struct{}) error {
	for _, path := range local.New {
		if _, skip := skipPaths[path]; skip {
			continue
		}
		if err := r.pushUpsert(pushFile{path: path}); err != nil {
			return err
		}
	}
	for _, path := range local.Modified {
		if _, skip := skipPaths[path]; skip {
			continue
		}
		base, shouldPush, err := r.modifiedBase(path)
		if err != nil {
			return err
		}
		if shouldPush {
			if err := r.pushUpsert(pushFile{path: path, base: base}); err != nil {
				return err
			}
		}
	}
	for _, path := range local.Deleted {
		if _, skip := skipPaths[path]; skip {
			continue
		}
		if err := r.pushDelete(path); err != nil {
			return err
		}
	}
	return nil
}

func (r *syncRun) modifiedBase(path string) (int, bool, error) {
	entry, tracked := r.state.Files[path]
	trackedConflict, hasConflict := r.state.Conflicts[path]
	if !hasConflict {
		return baseRevision(entry, tracked), true, nil
	}
	destination, ok := vaultPath(r.cfg.VaultRoot, path, r.cfg.SyncAttachments)
	if !ok {
		return 0, false, fmt.Errorf("%w: unsafe local path %s", ErrSync, path)
	}
	localHash, err := hashFile(destination)
	if err != nil {
		return 0, false, fmt.Errorf("hash local file %s: %w", path, err)
	}
	if localHash == trackedConflict.LocalContentHash {
		return 0, false, nil
	}
	return trackedConflict.ServerRevision, true, nil
}

func (r *syncRun) pushUpsert(file pushFile) error {
	destination, ok := vaultPath(r.cfg.VaultRoot, file.path, r.cfg.SyncAttachments)
	if !ok {
		return fmt.Errorf("%w: unsafe local path %s", ErrSync, file.path)
	}
	content, err := os.ReadFile(destination)
	if err != nil {
		return fmt.Errorf("read local file %s: %w", file.path, err)
	}
	contentHash := hashBytes(content)
	putRequest := client.PutFileRequest{
		DeviceID:     r.cfg.DeviceID,
		BaseRevision: file.base,
		ContentHash:  contentHash,
	}
	if rules.IsAttachmentPath(file.path) {
		putRequest.Content = base64.StdEncoding.EncodeToString(content)
		putRequest.Encoding = "base64"
	} else {
		putRequest.Content = string(content)
	}
	result, err := r.syncClient.PutFile(r.ctx, client.FileRef{VaultID: r.cfg.VaultID, Path: file.path}, putRequest)
	if err != nil {
		var conflictErr *client.ConflictError
		if errors.As(err, &conflictErr) {
			return r.pushConflict(pushFile{path: file.path, localText: string(content), conflictErr: conflictErr})
		}
		return fmt.Errorf("put file %s: %w", file.path, err)
	}
	r.state.Files[file.path] = manifest.Entry{
		ServerRevision: result.Revision,
		ContentHash:    result.ContentHash,
		LastSyncedAt:   r.now().UTC().Format(time.RFC3339),
	}
	delete(r.state.Conflicts, file.path)
	r.summary.Pushed++
	return nil
}

func (r *syncRun) pushDelete(path string) error {
	entry, tracked := r.state.Files[path]
	trackedConflict, hasConflict := r.state.Conflicts[path]
	if hasConflict && trackedConflict.ServerDeleted {
		delete(r.state.Files, path)
		delete(r.state.Conflicts, path)
		return nil
	}
	base := baseRevision(entry, tracked)
	if hasConflict {
		base = trackedConflict.ServerRevision
	}
	_, err := r.syncClient.DeleteFile(r.ctx, client.FileRef{VaultID: r.cfg.VaultID, Path: path}, client.DeleteFileRequest{
		DeviceID:     r.cfg.DeviceID,
		BaseRevision: base,
	})
	if err != nil {
		var conflictErr *client.ConflictError
		if errors.As(err, &conflictErr) {
			return r.pushConflict(pushFile{path: path, localText: conflict.LocalDeletedPlaceholder, conflictErr: conflictErr})
		}
		return fmt.Errorf("delete file %s: %w", path, err)
	}
	delete(r.state.Files, path)
	delete(r.state.Conflicts, path)
	r.summary.RemotelyDeleted++
	return nil
}

func (r *syncRun) pushConflict(file pushFile) error {
	if rules.IsAttachmentPath(file.path) {
		return r.pushAttachmentConflict(file.path)
	}
	serverRevision := intDetail(file.conflictErr.Details["server_revision"])
	clientBase := intDetail(file.conflictErr.Details["client_base_revision"])
	serverContent, err := r.serverContentAfterConflict(file.path)
	if err != nil {
		return err
	}
	if _, err := conflict.WriteFile(conflict.Request{
		VaultRoot:          r.cfg.VaultRoot,
		Path:               file.path,
		DeviceID:           r.cfg.DeviceID,
		ClientBaseRevision: clientBase,
		ServerRevision:     serverRevision,
		LocalContent:       file.localText,
		ServerContent:      serverContent,
		Now:                r.now(),
	}); err != nil {
		return fmt.Errorf("write conflict file %s: %w", file.path, err)
	}
	r.summary.Conflicts = append(r.summary.Conflicts, file.path)
	return nil
}

// pushAttachmentConflict handles a 409 on an attachment PUT/DELETE. Unlike
// markdown, there's no text placeholder for "the server copy is gone"; when
// the server has nothing to show, we still surface the conflict but can't
// write a comparison file.
func (r *syncRun) pushAttachmentConflict(path string) error {
	serverFile, err := r.syncClient.GetFile(r.ctx, client.FileRef{VaultID: r.cfg.VaultID, Path: path})
	if err != nil {
		var apiErr *client.APIError
		if !errors.As(err, &apiErr) || apiErr.StatusCode != http.StatusNotFound {
			return fmt.Errorf("%w: get server file %s after conflict: %w", ErrSync, path, err)
		}
		r.summary.Warnings = append(r.summary.Warnings, "push conflict on deleted server attachment "+path+"; resolve manually")
		r.summary.Conflicts = append(r.summary.Conflicts, path)
		return nil
	}
	if serverFile.Deleted {
		r.summary.Warnings = append(r.summary.Warnings, "push conflict on deleted server attachment "+path+"; resolve manually")
		r.summary.Conflicts = append(r.summary.Conflicts, path)
		return nil
	}
	contentBytes, err := serverFile.DecodedContent()
	if err != nil {
		return fmt.Errorf("decode server file %s after conflict: %w", path, err)
	}
	if _, err := conflict.WriteBinaryFile(conflict.BinaryRequest{
		VaultRoot: r.cfg.VaultRoot,
		Path:      path,
		DeviceID:  r.cfg.DeviceID,
		Content:   contentBytes,
		Now:       r.now(),
	}); err != nil {
		return fmt.Errorf("write conflict file %s: %w", path, err)
	}
	r.summary.Conflicts = append(r.summary.Conflicts, path)
	return nil
}

func (r *syncRun) serverContentAfterConflict(path string) (string, error) {
	serverFile, err := r.syncClient.GetFile(r.ctx, client.FileRef{VaultID: r.cfg.VaultID, Path: path})
	if err == nil {
		if serverFile.Deleted {
			return conflict.ServerDeletedPlaceholder, nil
		}
		return serverFile.Content, nil
	}
	var apiErr *client.APIError
	if errors.As(err, &apiErr) && apiErr.StatusCode == http.StatusNotFound {
		return conflict.ServerDeletedPlaceholder, nil
	}
	return "", fmt.Errorf("%w: get server file %s after conflict: %w", ErrSync, path, err)
}
