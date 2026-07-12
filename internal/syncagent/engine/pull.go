package engine

import (
	"fmt"
	"os"
	"time"

	"github.com/Qulip/obsidian-sync/internal/syncagent/atomicfile"
	"github.com/Qulip/obsidian-sync/internal/syncagent/client"
	"github.com/Qulip/obsidian-sync/internal/syncagent/conflict"
	"github.com/Qulip/obsidian-sync/internal/syncagent/manifest"
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
	for {
		page, err := r.syncClient.GetChanges(r.ctx, r.cfg.VaultID, client.ChangesRequest{
			Since:    cursor,
			DeviceID: deviceID,
			Limit:    client.DefaultPageLimit,
		})
		if err != nil {
			return fmt.Errorf("get changes: %w", err)
		}
		for _, item := range page.Changes {
			r.summary.Pulled++
			if err := r.applyChange(item); err != nil {
				return err
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
	if cursor > r.state.LastSyncCursor {
		r.state.LastSyncCursor = cursor
	}
	return nil
}

func (r *syncRun) applyChange(item client.SyncChangeItem) error {
	_, ok := vaultPath(r.cfg.VaultRoot, item.Path)
	if !ok {
		r.summary.Warnings = append(r.summary.Warnings, "rejected unsafe server path: "+item.Path)
		return nil
	}
	safeDestination, err := vaultfs.SafePath(r.cfg.VaultRoot, item.Path)
	if err != nil {
		return fmt.Errorf("validate local file %s: %w", item.Path, err)
	}
	entry, tracked := r.state.Files[item.Path]
	isDelete := item.Deleted || item.EventType == deleteEvent
	if !isDelete && tracked && item.ContentHash != nil && entry.ContentHash == *item.ContentHash {
		entry.ServerRevision = item.Revision
		r.state.Files[item.Path] = entry
		return nil
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

func (r *syncRun) applyDelete(change pullChange) error {
	if !existsPath(change.destination) {
		delete(r.state.Files, change.item.Path)
		return nil
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
		return nil
	}
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
		ServerContent:      conflict.ServerDeletedPlaceholder,
		Now:                r.now(),
	}); err != nil {
		return fmt.Errorf("write conflict file %s: %w", change.item.Path, err)
	}
	r.state.Conflicts[change.item.Path] = manifest.Conflict{
		ServerRevision:    change.item.Revision,
		ServerContentHash: change.item.ContentHash,
		LocalContentHash:  localHash,
		ServerDeleted:     true,
	}
	r.summary.Conflicts = append(r.summary.Conflicts, change.item.Path)
	return nil
}

func (r *syncRun) applyWrite(change pullChange) error {
	serverFile, err := r.syncClient.GetFile(r.ctx, client.FileRef{VaultID: r.cfg.VaultID, Path: change.item.Path})
	if err != nil {
		return fmt.Errorf("get server file %s: %w", change.item.Path, err)
	}
	if hashText(serverFile.Content) != serverFile.ContentHash {
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
	if err := atomicfile.WriteText(change.destination, serverFile.Content); err != nil {
		return fmt.Errorf("write local file %s: %w", change.item.Path, err)
	}
	r.state.Files[change.item.Path] = manifest.Entry{
		ServerRevision: serverFile.Revision,
		ContentHash:    serverFile.ContentHash,
		LastSyncedAt:   r.now().UTC().Format(time.RFC3339),
	}
	delete(r.state.Conflicts, change.item.Path)
	r.summary.Applied++
	return nil
}

func (r *syncRun) writePullConflict(change pullChange, localHash string, serverFile client.FileContentData) error {
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
	r.state.Conflicts[change.item.Path] = manifest.Conflict{
		ServerRevision:    serverFile.Revision,
		ServerContentHash: &serverFile.ContentHash,
		LocalContentHash:  localHash,
		ServerDeleted:     false,
	}
	r.summary.Conflicts = append(r.summary.Conflicts, change.item.Path)
	return nil
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
