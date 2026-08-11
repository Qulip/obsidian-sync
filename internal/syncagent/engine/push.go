package engine

import (
	"encoding/base64"
	"errors"
	"fmt"
	"net/http"
	"os"
	"time"

	"github.com/Qulip/obsidian-sync/internal/syncagent/atomicfile"
	"github.com/Qulip/obsidian-sync/internal/syncagent/client"
	"github.com/Qulip/obsidian-sync/internal/syncagent/config"
	"github.com/Qulip/obsidian-sync/internal/syncagent/conflict"
	"github.com/Qulip/obsidian-sync/internal/syncagent/manifest"
	"github.com/Qulip/obsidian-sync/internal/syncagent/rules"
	"github.com/Qulip/obsidian-sync/internal/syncagent/scanner"
)

type pushFile struct {
	path         string
	base         int
	localContent []byte // raw local bytes; nil when this conflict came from a delete
	isDelete     bool
	conflictErr  *client.ConflictError
}

func (r *syncRun) push(local scanner.LocalChanges, skipPaths map[string]struct{}) error {
	if err := r.resolveTrackedConflicts(skipPaths); err != nil {
		return err
	}
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

// resolveTrackedConflicts resolves conflicts left over from an earlier run
// under local-wins/remote-wins before the normal New/Modified/Deleted scan
// runs. pushConflict (below) only ever fires on a *fresh* 409, so a conflict
// that was already recorded in r.state.Conflicts on a prior run is otherwise
// never revisited: modifiedBase keeps refusing to push it as long as the
// local file still hashes to the tracked LocalContentHash, leaving it stuck
// forever even though the configured policy says how to resolve it. Manual
// policy is exempt by design: a conflict is explicit, never an automatic
// merge, so it must wait for the user to edit the file.
func (r *syncRun) resolveTrackedConflicts(skipPaths map[string]struct{}) error {
	// Guard by allow-list, not by excluding ConflictPolicyManual: an
	// AgentConfig built without going through config resolution (as in
	// several existing tests) leaves ConflictPolicy at its zero value, which
	// must behave like manual, not like an unrecognized policy that falls
	// through this function's switch and gets treated as resolved.
	if r.cfg.ConflictPolicy != config.ConflictPolicyLocalWins && r.cfg.ConflictPolicy != config.ConflictPolicyRemoteWins {
		return nil
	}
	for _, path := range sortedConflictPaths(r.state.Conflicts) {
		if _, skip := skipPaths[path]; skip {
			continue
		}
		tracked := r.state.Conflicts[path]
		destination, ok := vaultPath(r.cfg.VaultRoot, path, r.cfg.SyncAttachments)
		if !ok {
			return fmt.Errorf("%w: unsafe local path %s", ErrSync, path)
		}
		localExists := existsPath(destination)
		if tracked.ServerDeleted && !localExists {
			// Both sides already agree the file is gone; nothing to push.
			delete(r.state.Files, path)
			delete(r.state.Conflicts, path)
			if err := r.saveManifest(); err != nil {
				return err
			}
			skipPaths[path] = struct{}{}
			continue
		}
		file := pushFile{path: path, isDelete: !localExists}
		if localExists {
			content, err := os.ReadFile(destination)
			if err != nil {
				return fmt.Errorf("read local file %s: %w", path, err)
			}
			file.localContent = content
		}
		switch r.cfg.ConflictPolicy {
		case config.ConflictPolicyLocalWins:
			resolved, err := r.pushConflictLocalWins(file, tracked.ServerRevision)
			if err != nil {
				return err
			}
			if !resolved {
				// Retries exhausted; leave the tracked conflict in place for
				// manual resolution rather than clearing it silently.
				r.summary.Warnings = append(r.summary.Warnings, "local-wins could not resolve tracked conflict on "+path+"; leaving for manual resolution")
				continue
			}
		case config.ConflictPolicyRemoteWins:
			if err := r.pushConflictRemoteWins(file); err != nil {
				return err
			}
		}
		skipPaths[path] = struct{}{}
	}
	return nil
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
			return r.pushConflict(pushFile{path: file.path, localContent: content, conflictErr: conflictErr})
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
	return r.saveManifest()
}

func (r *syncRun) pushDelete(path string) error {
	entry, tracked := r.state.Files[path]
	trackedConflict, hasConflict := r.state.Conflicts[path]
	if hasConflict && trackedConflict.ServerDeleted {
		delete(r.state.Files, path)
		delete(r.state.Conflicts, path)
		return r.saveManifest()
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
			return r.pushConflict(pushFile{path: path, isDelete: true, conflictErr: conflictErr})
		}
		return fmt.Errorf("delete file %s: %w", path, err)
	}
	delete(r.state.Files, path)
	delete(r.state.Conflicts, path)
	r.summary.RemotelyDeleted++
	return r.saveManifest()
}

// pushConflict resolves a 409 on our own PUT/DELETE according to
// cfg.ConflictPolicy, mirroring conflict_resolution.resolve_push_conflict.
// local-wins and remote-wins apply uniformly to markdown and attachments;
// only the manual fallback needs to special-case attachments, since binary
// content can't be embedded in a text conflict report the way markdown can.
func (r *syncRun) pushConflict(file pushFile) error {
	switch r.cfg.ConflictPolicy {
	case config.ConflictPolicyLocalWins:
		serverRevision := intDetail(file.conflictErr.Details["server_revision"])
		resolved, err := r.pushConflictLocalWins(file, serverRevision)
		if err != nil {
			return err
		}
		if resolved {
			return nil
		}
		r.summary.Warnings = append(r.summary.Warnings, "local-wins could not resolve push conflict on "+file.path+"; falling back to manual resolution")
	case config.ConflictPolicyRemoteWins:
		return r.pushConflictRemoteWins(file)
	}
	if rules.IsAttachmentPath(file.path) {
		return r.pushAttachmentConflict(file.path)
	}
	return r.pushManualConflict(file)
}

func (r *syncRun) pushManualConflict(file pushFile) error {
	serverRevision := intDetail(file.conflictErr.Details["server_revision"])
	clientBase := intDetail(file.conflictErr.Details["client_base_revision"])
	serverContent, err := r.serverContentAfterConflict(file.path)
	if err != nil {
		return err
	}
	localText := conflict.LocalDeletedPlaceholder
	if !file.isDelete {
		localText = string(file.localContent)
	}
	if _, err := conflict.WriteFile(conflict.Request{
		VaultRoot:          r.cfg.VaultRoot,
		Path:               file.path,
		DeviceID:           r.cfg.DeviceID,
		ClientBaseRevision: clientBase,
		ServerRevision:     serverRevision,
		LocalContent:       localText,
		ServerContent:      serverContent,
		Now:                r.now(),
	}); err != nil {
		return fmt.Errorf("write conflict file %s: %w", file.path, err)
	}
	r.summary.Conflicts = append(r.summary.Conflicts, file.path)
	return nil
}

// pushConflictLocalWins retries pushing local content (or the local delete)
// as the winner, bounded by localWinsMaxAttempts. Mirrors
// conflict_resolution._resolve_push_conflict_local_wins. serverRevision is
// passed explicitly rather than read off file.conflictErr because a tracked
// conflict resolved by resolveTrackedConflicts has no fresh ConflictError to
// dig it out of.
func (r *syncRun) pushConflictLocalWins(file pushFile, serverRevision int) (bool, error) {
	if file.isDelete {
		resolved, err := r.resolveLocalWinsDelete(file.path, serverRevision)
		if err != nil || !resolved {
			return false, err
		}
		delete(r.state.Files, file.path)
		delete(r.state.Conflicts, file.path)
		r.summary.RemotelyDeleted++
		if err := r.saveManifest(); err != nil {
			return true, err
		}
		return true, nil
	}
	outcome, err := r.resolveLocalWinsUpsert(file.path, file.localContent, serverRevision)
	if err != nil || !outcome.resolved {
		return false, err
	}
	r.state.Files[file.path] = manifest.Entry{
		ServerRevision: outcome.revision,
		ContentHash:    outcome.contentHash,
		LastSyncedAt:   r.now().UTC().Format(time.RFC3339),
	}
	delete(r.state.Conflicts, file.path)
	r.summary.Pushed++
	if err := r.saveManifest(); err != nil {
		return true, err
	}
	return true, nil
}

// pushConflictRemoteWins adopts the server's state locally, preserving any
// discarded local content in a backup file first. Mirrors
// conflict_resolution._resolve_push_conflict_remote_wins, except that a
// server-file fetch failure only falls back to the "server has nothing"
// path on a 404; any other error propagates instead of being silently
// treated as a deletion (matching the existing manual-policy behavior in
// serverContentAfterConflict).
func (r *syncRun) pushConflictRemoteWins(file pushFile) error {
	destination, ok := vaultPath(r.cfg.VaultRoot, file.path, r.cfg.SyncAttachments)
	if !ok {
		return fmt.Errorf("%w: unsafe local path %s", ErrSync, file.path)
	}
	serverFile, err := r.syncClient.GetFile(r.ctx, client.FileRef{VaultID: r.cfg.VaultID, Path: file.path})
	if err != nil {
		var apiErr *client.APIError
		if !errors.As(err, &apiErr) || apiErr.StatusCode != http.StatusNotFound {
			return fmt.Errorf("%w: get server file %s after conflict: %w", ErrSync, file.path, err)
		}
		// The server has no content for this path either (e.g. it was
		// deleted concurrently too); adopt that locally.
		if existsPath(destination) {
			if !file.isDelete && file.localContent != nil {
				if _, err := r.writeLocalBackup(file.path, file.localContent, 0); err != nil {
					return err
				}
			}
			if err := os.Remove(destination); err != nil {
				return fmt.Errorf("delete local file %s: %w", file.path, err)
			}
		}
		delete(r.state.Files, file.path)
		delete(r.state.Conflicts, file.path)
		r.summary.RemotelyDeleted++
		return r.saveManifest()
	}

	if !file.isDelete && file.localContent != nil {
		if _, err := r.writeLocalBackup(file.path, file.localContent, serverFile.Revision); err != nil {
			return err
		}
	}
	contentBytes, err := serverFile.DecodedContent()
	if err != nil {
		return fmt.Errorf("decode server file %s after conflict: %w", file.path, err)
	}
	if err := atomicfile.WriteBytes(destination, contentBytes); err != nil {
		return fmt.Errorf("write local file %s: %w", file.path, err)
	}
	r.state.Files[file.path] = manifest.Entry{
		ServerRevision: serverFile.Revision,
		ContentHash:    serverFile.ContentHash,
		LastSyncedAt:   r.now().UTC().Format(time.RFC3339),
	}
	delete(r.state.Conflicts, file.path)
	r.summary.Applied++
	return r.saveManifest()
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
