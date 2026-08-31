package engine

import (
	"fmt"
	"os"
	"time"

	"github.com/Qulip/obsidian-sync/internal/syncagent/atomicfile"
	"github.com/Qulip/obsidian-sync/internal/syncagent/client"
	"github.com/Qulip/obsidian-sync/internal/syncagent/config"
	"github.com/Qulip/obsidian-sync/internal/syncagent/conflict"
	"github.com/Qulip/obsidian-sync/internal/syncagent/manifest"
	"github.com/Qulip/obsidian-sync/internal/syncagent/rules"
	"github.com/Qulip/obsidian-sync/internal/syncagent/vaultfs"
)

const deleteEvent = "DELETE"

type pullChange struct {
	item        client.SyncChangeItem
	destination string
	entry       manifest.Entry
	tracked     bool
}

func (r *syncRun) pull(deviceID string) error {
	cursor := r.state.LastSyncCursor
	// A cursor-zero pull without a device ID is the historical bootstrap;
	// apply only the final event for each path after all pages are fetched.
	bootstrap := deviceID == "" && cursor == 0
	var pending []client.SyncChangeItem
	for {
		page, err := r.syncClient.GetChanges(r.ctx, r.cfg.VaultID, client.ChangesRequest{
			Since:    cursor,
			DeviceID: deviceID,
			Limit:    client.DefaultPageLimit,
		})
		if err != nil {
			if bootstrap {
				if applyErr := r.applyChanges(coalesceChanges(pending)); applyErr != nil {
					return applyErr
				}
			}
			return fmt.Errorf("get changes: %w", err)
		}
		if bootstrap {
			r.summary.Pulled += len(page.Changes)
			pending = append(pending, page.Changes...)
		} else {
			for _, item := range page.Changes {
				r.summary.Pulled++
				if err := r.applyChange(item); err != nil {
					return err
				}
			}
		}
		if len(page.Changes) == 0 || page.ToCursor <= cursor {
			if page.ToCursor > cursor {
				cursor = page.ToCursor
			}
			break
		}
		cursor = page.ToCursor
	}
	if bootstrap {
		if err := r.applyChanges(coalesceChanges(pending)); err != nil {
			return err
		}
	}
	if cursor > r.state.LastSyncCursor && len(r.unsafePullPaths) == 0 {
		r.state.LastSyncCursor = cursor
		return r.saveManifest()
	}
	return nil
}

func (r *syncRun) applyChanges(changes []client.SyncChangeItem) error {
	for _, item := range changes {
		if err := r.applyChange(item); err != nil {
			return err
		}
	}
	return nil
}

func coalesceChanges(changes []client.SyncChangeItem) []client.SyncChangeItem {
	latestIndex := make(map[string]int, len(changes))
	for index, item := range changes {
		latestIndex[item.Path] = index
	}
	coalesced := make([]client.SyncChangeItem, 0, len(latestIndex))
	for index, item := range changes {
		if latestIndex[item.Path] == index {
			coalesced = append(coalesced, item)
		}
	}
	return coalesced
}

func (r *syncRun) applyChange(item client.SyncChangeItem) error {
	if !r.cfg.SyncAttachments && rules.IsAttachmentPath(item.Path) {
		// Attachment sync is opt-in (docs/sync-agent.md); this is expected
		// and not worth a warning on every run when it's disabled.
		return nil
	}
	_, ok := vaultPath(r.cfg.VaultRoot, item.Path, r.cfg.SyncAttachments)
	if !ok {
		r.summary.Warnings = append(r.summary.Warnings, "rejected unsafe server path: "+item.Path)
		return nil
	}
	safeDestination, err := vaultfs.SafePath(r.cfg.VaultRoot, item.Path)
	if err != nil {
		r.summary.Warnings = append(r.summary.Warnings, "rejected unsafe server path: "+item.Path+"; skipped")
		r.markUnsafePullPath(item.Path)
		return nil
	}
	entry, tracked := r.state.Files[item.Path]
	isDelete := item.Deleted || item.EventType == deleteEvent
	if !isDelete && tracked && item.ContentHash != nil && entry.ContentHash == *item.ContentHash {
		entry.ServerRevision = item.Revision
		r.state.Files[item.Path] = entry
		return r.saveManifest()
	}
	change := pullChange{
		item:        item,
		destination: safeDestination,
		entry:       entry,
		tracked:     tracked,
	}
	if isDelete {
		return r.applyDelete(change)
	}
	return r.applyWrite(change)
}

func (r *syncRun) markUnsafePullPath(path string) {
	if r.unsafePullPaths == nil {
		r.unsafePullPaths = map[string]struct{}{}
	}
	r.unsafePullPaths[path] = struct{}{}
}

func (r *syncRun) applyDelete(change pullChange) error {
	if !existsPath(change.destination) {
		delete(r.state.Files, change.item.Path)
		return r.saveManifest()
	}
	localHash, err := hashFile(change.destination)
	if err != nil {
		return fmt.Errorf("hash local file %s: %w", change.item.Path, err)
	}
	if change.tracked && localHash == change.entry.ContentHash {
		if err := os.Remove(change.destination); err != nil {
			return fmt.Errorf("delete local file %s: %w", change.item.Path, err)
		}
		delete(r.state.Files, change.item.Path)
		delete(r.state.Conflicts, change.item.Path)
		r.summary.LocallyDeleted++
		return r.saveManifest()
	}
	return r.resolvePullDeleteConflict(change, localHash)
}

// resolvePullDeleteConflict resolves a pull-side delete conflict (server
// deleted, local diverged) according to cfg.ConflictPolicy, mirroring
// conflict_resolution.resolve_pull_delete_conflict.
func (r *syncRun) resolvePullDeleteConflict(change pullChange, localHash string) error {
	switch r.cfg.ConflictPolicy {
	case config.ConflictPolicyLocalWins:
		resolved, err := r.pullDeleteConflictLocalWins(change)
		if err != nil {
			return err
		}
		if resolved {
			return nil
		}
		r.summary.Warnings = append(r.summary.Warnings, "local-wins could not resolve delete conflict on "+change.item.Path+"; falling back to manual resolution")
	case config.ConflictPolicyRemoteWins:
		return r.pullDeleteConflictRemoteWins(change)
	}
	if err := r.writeDeleteConflict(change); err != nil {
		return err
	}
	r.state.Conflicts[change.item.Path] = manifest.Conflict{
		ServerRevision:    change.item.Revision,
		ServerContentHash: change.item.ContentHash,
		LocalContentHash:  localHash,
		ServerDeleted:     true,
	}
	r.summary.Conflicts = append(r.summary.Conflicts, change.item.Path)
	return r.saveManifest()
}

// pullDeleteConflictLocalWins recreates the file on the server from local
// content, since the server has soft-deleted it (base_revision=0).
func (r *syncRun) pullDeleteConflictLocalWins(change pullChange) (bool, error) {
	localContent, err := os.ReadFile(change.destination)
	if err != nil {
		return false, fmt.Errorf("read local file %s: %w", change.item.Path, err)
	}
	outcome, err := r.resolveLocalWinsUpsert(change.item.Path, localContent, 0)
	if err != nil || !outcome.resolved {
		return false, err
	}
	r.state.Files[change.item.Path] = manifest.Entry{
		ServerRevision: outcome.revision,
		ContentHash:    outcome.contentHash,
		LastSyncedAt:   r.now().UTC().Format(time.RFC3339),
	}
	delete(r.state.Conflicts, change.item.Path)
	r.summary.Pushed++
	if err := r.saveManifest(); err != nil {
		return true, err
	}
	return true, nil
}

// pullDeleteConflictRemoteWins backs up the local content, then accepts the
// server's delete.
func (r *syncRun) pullDeleteConflictRemoteWins(change pullChange) error {
	localContent, err := os.ReadFile(change.destination)
	if err != nil {
		return fmt.Errorf("read local file %s: %w", change.item.Path, err)
	}
	if _, err := r.writeLocalBackup(change.item.Path, localContent, change.item.Revision); err != nil {
		return err
	}
	if err := os.Remove(change.destination); err != nil {
		return fmt.Errorf("delete local file %s: %w", change.item.Path, err)
	}
	delete(r.state.Files, change.item.Path)
	delete(r.state.Conflicts, change.item.Path)
	r.summary.LocallyDeleted++
	return r.saveManifest()
}

func (r *syncRun) writeDeleteConflict(change pullChange) error {
	localContent, err := os.ReadFile(change.destination)
	if err != nil {
		return fmt.Errorf("read local file %s: %w", change.item.Path, err)
	}
	if rules.IsAttachmentPath(change.item.Path) {
		if _, err := conflict.WriteBinaryFile(conflict.BinaryRequest{
			VaultRoot: r.cfg.VaultRoot,
			Path:      change.item.Path,
			DeviceID:  r.cfg.DeviceID,
			Content:   localContent,
			Now:       r.now(),
		}); err != nil {
			return fmt.Errorf("write conflict file %s: %w", change.item.Path, err)
		}
		return nil
	}
	if _, err := conflict.WriteFile(conflict.Request{
		VaultRoot:          r.cfg.VaultRoot,
		Path:               change.item.Path,
		DeviceID:           r.cfg.DeviceID,
		ClientBaseRevision: baseRevision(change.entry, change.tracked),
		ServerRevision:     change.item.Revision,
		LocalContent:       string(localContent),
		ServerContent:      conflict.ServerDeletedPlaceholder,
		Now:                r.now(),
	}); err != nil {
		return fmt.Errorf("write conflict file %s: %w", change.item.Path, err)
	}
	return nil
}

func (r *syncRun) applyWrite(change pullChange) error {
	serverFile, err := r.syncClient.GetFile(r.ctx, client.FileRef{VaultID: r.cfg.VaultID, Path: change.item.Path})
	if err != nil {
		return fmt.Errorf("get server file %s: %w", change.item.Path, err)
	}
	contentBytes, err := serverFile.DecodedContent()
	if err != nil {
		r.summary.Warnings = append(r.summary.Warnings, "invalid server content encoding for "+change.item.Path+"; skipped")
		return nil
	}
	if hashBytes(contentBytes) != serverFile.ContentHash {
		r.summary.Warnings = append(r.summary.Warnings, "server content hash mismatch for "+change.item.Path+"; skipped")
		return nil
	}
	if existsPath(change.destination) {
		localHash, err := hashFile(change.destination)
		if err != nil {
			return fmt.Errorf("hash local file %s: %w", change.item.Path, err)
		}
		if localDirty(localHash, serverFile.ContentHash, change) {
			return r.writePullConflict(change, localHash, serverFile)
		}
	}
	if err := atomicfile.WriteBytes(change.destination, contentBytes); err != nil {
		return fmt.Errorf("write local file %s: %w", change.item.Path, err)
	}
	r.state.Files[change.item.Path] = manifest.Entry{
		ServerRevision: serverFile.Revision,
		ContentHash:    serverFile.ContentHash,
		LastSyncedAt:   r.now().UTC().Format(time.RFC3339),
	}
	delete(r.state.Conflicts, change.item.Path)
	r.summary.Applied++
	return r.saveManifest()
}

// writePullConflict resolves a pull-side write conflict (server + local both
// changed) according to cfg.ConflictPolicy, mirroring
// conflict_resolution.resolve_pull_write_conflict.
func (r *syncRun) writePullConflict(change pullChange, localHash string, serverFile client.FileContentData) error {
	switch r.cfg.ConflictPolicy {
	case config.ConflictPolicyLocalWins:
		resolved, err := r.pullWriteConflictLocalWins(change, serverFile)
		if err != nil {
			return err
		}
		if resolved {
			return nil
		}
		r.summary.Warnings = append(r.summary.Warnings, "local-wins could not resolve conflict on "+change.item.Path+"; falling back to manual resolution")
	case config.ConflictPolicyRemoteWins:
		return r.pullWriteConflictRemoteWins(change, serverFile)
	}
	return r.writePullConflictManual(change, localHash, serverFile)
}

// pullWriteConflictLocalWins retries pushing the local content as the
// winner, using the server's latest revision as the base.
func (r *syncRun) pullWriteConflictLocalWins(change pullChange, serverFile client.FileContentData) (bool, error) {
	localContent, err := os.ReadFile(change.destination)
	if err != nil {
		return false, fmt.Errorf("read local file %s: %w", change.item.Path, err)
	}
	outcome, err := r.resolveLocalWinsUpsert(change.item.Path, localContent, serverFile.Revision)
	if err != nil || !outcome.resolved {
		return false, err
	}
	r.state.Files[change.item.Path] = manifest.Entry{
		ServerRevision: outcome.revision,
		ContentHash:    outcome.contentHash,
		LastSyncedAt:   r.now().UTC().Format(time.RFC3339),
	}
	delete(r.state.Conflicts, change.item.Path)
	r.summary.Pushed++
	if err := r.saveManifest(); err != nil {
		return true, err
	}
	return true, nil
}

// pullWriteConflictRemoteWins backs up the local content, then overwrites it
// with the server's content.
func (r *syncRun) pullWriteConflictRemoteWins(change pullChange, serverFile client.FileContentData) error {
	localContent, err := os.ReadFile(change.destination)
	if err != nil {
		return fmt.Errorf("read local file %s: %w", change.item.Path, err)
	}
	if _, err := r.writeLocalBackup(change.item.Path, localContent, serverFile.Revision); err != nil {
		return err
	}
	contentBytes, err := serverFile.DecodedContent()
	if err != nil {
		return fmt.Errorf("decode server file %s: %w", change.item.Path, err)
	}
	if err := atomicfile.WriteBytes(change.destination, contentBytes); err != nil {
		return fmt.Errorf("write local file %s: %w", change.item.Path, err)
	}
	r.state.Files[change.item.Path] = manifest.Entry{
		ServerRevision: serverFile.Revision,
		ContentHash:    serverFile.ContentHash,
		LastSyncedAt:   r.now().UTC().Format(time.RFC3339),
	}
	delete(r.state.Conflicts, change.item.Path)
	r.summary.Applied++
	return r.saveManifest()
}

func (r *syncRun) writePullConflictManual(change pullChange, localHash string, serverFile client.FileContentData) error {
	if rules.IsAttachmentPath(change.item.Path) {
		contentBytes, err := serverFile.DecodedContent()
		if err != nil {
			return fmt.Errorf("decode server file %s: %w", change.item.Path, err)
		}
		if _, err := conflict.WriteBinaryFile(conflict.BinaryRequest{
			VaultRoot: r.cfg.VaultRoot,
			Path:      change.item.Path,
			DeviceID:  r.cfg.DeviceID,
			Content:   contentBytes,
			Now:       r.now(),
		}); err != nil {
			return fmt.Errorf("write conflict file %s: %w", change.item.Path, err)
		}
	} else {
		localContent, err := os.ReadFile(change.destination)
		if err != nil {
			return fmt.Errorf("read local file %s: %w", change.item.Path, err)
		}
		if _, err := conflict.WriteFile(conflict.Request{
			VaultRoot:          r.cfg.VaultRoot,
			Path:               change.item.Path,
			DeviceID:           r.cfg.DeviceID,
			ClientBaseRevision: baseRevision(change.entry, change.tracked),
			ServerRevision:     change.item.Revision,
			LocalContent:       string(localContent),
			ServerContent:      serverFile.Content,
			Now:                r.now(),
		}); err != nil {
			return fmt.Errorf("write conflict file %s: %w", change.item.Path, err)
		}
	}
	r.state.Conflicts[change.item.Path] = manifest.Conflict{
		ServerRevision:    serverFile.Revision,
		ServerContentHash: &serverFile.ContentHash,
		LocalContentHash:  localHash,
		ServerDeleted:     false,
	}
	r.summary.Conflicts = append(r.summary.Conflicts, change.item.Path)
	return r.saveManifest()
}

func localDirty(localHash string, serverHash string, change pullChange) bool {
	if change.tracked {
		return localHash != change.entry.ContentHash
	}
	return localHash != serverHash
}

func baseRevision(entry manifest.Entry, tracked bool) int {
	if !tracked {
		return 0
	}
	return entry.ServerRevision
}
