package engine

import (
	"context"
	"errors"
	"path/filepath"
	"testing"

	"github.com/Qulip/obsidian-sync/internal/syncagent/client"
	"github.com/Qulip/obsidian-sync/internal/syncagent/manifest"
)

func TestRunSyncPullWriteDeleteAndCursor(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	vault.writeNote("old.md", "old")
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 1,
		Files: map[string]manifest.Entry{
			"old.md": {ServerRevision: 1, ContentHash: testHashText("old"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.files["new.md"] = fileData(2, "new.md", "new")
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 2, path: "new.md", eventType: "CREATE", contentHash: testHashPtr("new")}),
		syncChange(changeSpec{revision: 3, path: "old.md", eventType: "DELETE", deleted: true}),
	}

	// When
	summary, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if summary.Applied != 1 || summary.LocallyDeleted != 1 || summary.Pulled != 2 {
		t.Fatalf("summary = %#v", summary)
	}
	vault.requireFileContent("new.md", "new")
	if exists(filepath.Join(vault.root, "old.md")) {
		t.Fatal("old.md still exists")
	}
	state := loadManifest(t, vault.root)
	if state.LastSyncCursor != 3 {
		t.Fatalf("LastSyncCursor = %d", state.LastSyncCursor)
	}
	if _, ok := state.Files["old.md"]; ok {
		t.Fatal("old.md still tracked")
	}
	if state.Files["new.md"].ServerRevision != 2 {
		t.Fatalf("new.md revision = %d", state.Files["new.md"].ServerRevision)
	}
	if fake.secondPullDeviceID != "dev" {
		t.Fatalf("second pull device id = %q", fake.secondPullDeviceID)
	}
}

func TestRunSyncFreshBootstrapCoalescesDeleteWithoutFetchingCurrentFile(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 0,
		Files:          map[string]manifest.Entry{},
		Conflicts:      map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 1, path: "_sync_verify.md", eventType: "CREATE", contentHash: testHashPtr("stale")}),
		syncChange(changeSpec{revision: 2, path: "_sync_verify.md", eventType: "UPDATE", contentHash: testHashPtr("superseded")}),
		syncChange(changeSpec{revision: 3, path: "_sync_verify.md", eventType: "DELETE", deleted: true}),
	}

	// When
	summary, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if summary.Pulled != 3 {
		t.Fatalf("summary.Pulled = %d, want 3", summary.Pulled)
	}
	if fake.getFileCalls["_sync_verify.md"] != 0 {
		t.Fatalf("current-file GET count = %d, want 0", fake.getFileCalls["_sync_verify.md"])
	}
	if exists(filepath.Join(vault.root, "_sync_verify.md")) {
		t.Fatal("_sync_verify.md exists after final remote DELETE")
	}
	if _, ok := loadManifest(t, vault.root).Files["_sync_verify.md"]; ok {
		t.Fatal("_sync_verify.md remains tracked after final remote DELETE")
	}
}

func TestRunSyncFreshBootstrapCoalescesWriteWithSingleCurrentFileFetch(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 0,
		Files:          map[string]manifest.Entry{},
		Conflicts:      map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.files["_sync_verify.md"] = fileData(3, "_sync_verify.md", "final")
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 1, path: "_sync_verify.md", eventType: "CREATE", contentHash: testHashPtr("stale")}),
		syncChange(changeSpec{revision: 2, path: "_sync_verify.md", eventType: "UPDATE", contentHash: testHashPtr("superseded")}),
		syncChange(changeSpec{revision: 3, path: "_sync_verify.md", eventType: "UPDATE", contentHash: testHashPtr("final")}),
	}

	// When
	summary, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if summary.Pulled != 3 || summary.Applied != 1 {
		t.Fatalf("summary = %#v, want pulled 3 and applied 1", summary)
	}
	if fake.getFileCalls["_sync_verify.md"] != 1 {
		t.Fatalf("current-file GET count = %d, want 1", fake.getFileCalls["_sync_verify.md"])
	}
	vault.requireFileContent("_sync_verify.md", "final")
	state := loadManifest(t, vault.root)
	if state.Files["_sync_verify.md"].ServerRevision != 3 {
		t.Fatalf("_sync_verify.md server revision = %d, want 3", state.Files["_sync_verify.md"].ServerRevision)
	}
}

func TestRunSyncPersistsPullApplyBeforeLaterPullFailure(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 0,
		Files:          map[string]manifest.Entry{},
		Conflicts:      map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.files["new.md"] = fileData(2, "new.md", "new")
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 2, path: "new.md", eventType: "CREATE", contentHash: testHashPtr("new")}),
	}
	fake.getChangesErrOnCall = 2
	fake.getChangesErr = errors.New("interrupted after apply")

	// When
	_, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	if err == nil {
		t.Fatal("RunSync() error = nil, want forced interruption")
	}
	state := loadManifest(t, vault.root)
	if state.Files["new.md"].ServerRevision != 2 {
		t.Fatalf("new.md revision = %d, want 2", state.Files["new.md"].ServerRevision)
	}
	if state.LastSyncCursor != 0 {
		t.Fatalf("LastSyncCursor = %d, want 0 before pull page completion", state.LastSyncCursor)
	}
	vault.requireFileContent("new.md", "new")
}

func TestRunSyncPullsBase64Attachment(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 0,
		Files:          map[string]manifest.Entry{},
		Conflicts:      map[string]manifest.Conflict{},
	})
	raw := []byte{0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x01, 0xff}
	fake := newFakeClient()
	fake.files["image.png"] = fileDataBase64(2, "image.png", raw)
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 2, path: "image.png", eventType: "CREATE", contentHash: stringPtr(testHashBytes(raw))}),
	}

	// When
	summary, err := RunSync(context.Background(), testConfigWithAttachments(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if summary.Applied != 1 {
		t.Fatalf("summary.Applied = %d, want 1", summary.Applied)
	}
	if len(summary.Warnings) != 0 {
		t.Fatalf("summary.Warnings = %#v, want none", summary.Warnings)
	}
	vault.requireFileContent("image.png", string(raw))
	state := loadManifest(t, vault.root)
	if state.Files["image.png"].ContentHash != testHashBytes(raw) {
		t.Fatalf("image.png tracked hash = %q, want %q", state.Files["image.png"].ContentHash, testHashBytes(raw))
	}
}

func TestRunSyncCreateUpdateDelete(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	vault.writeNote("new.md", "new")
	vault.writeNote("edited.md", "edited v2")
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 7,
		Files: map[string]manifest.Entry{
			"edited.md":  {ServerRevision: 8, ContentHash: testHashText("edited v1"), LastSyncedAt: fixedNowString},
			"removed.md": {ServerRevision: 9, ContentHash: testHashText("removed"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.nextRevision = 10

	// When
	summary, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if summary.Pushed != 2 || summary.RemotelyDeleted != 1 {
		t.Fatalf("summary = %#v", summary)
	}
	fake.requirePut(t, putCall{path: "new.md", content: "new"})
	fake.requirePut(t, putCall{path: "edited.md", baseRevision: 8, content: "edited v2"})
	fake.requireDelete(t, deleteCall{path: "removed.md", baseRevision: 9})
	if _, ok := loadManifest(t, vault.root).Files["removed.md"]; ok {
		t.Fatal("removed.md still tracked")
	}
}

func TestRunSyncPersistsPushAndDeleteBeforeFinalPullFailure(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	vault.writeNote("new.md", "new")
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 7,
		Files: map[string]manifest.Entry{
			"removed.md": {ServerRevision: 8, ContentHash: testHashText("removed"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.nextRevision = 10
	fake.getChangesErrOnCall = 2
	fake.getChangesErr = errors.New("interrupted after push")

	// When
	_, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	if err == nil {
		t.Fatal("RunSync() error = nil, want forced interruption")
	}
	state := loadManifest(t, vault.root)
	if state.Files["new.md"].ServerRevision != 10 {
		t.Fatalf("new.md revision = %d, want 10", state.Files["new.md"].ServerRevision)
	}
	if _, ok := state.Files["removed.md"]; ok {
		t.Fatal("removed.md still tracked after successful delete")
	}
	fake.requirePut(t, putCall{path: "new.md", content: "new"})
	fake.requireDelete(t, deleteCall{path: "removed.md", baseRevision: 8})
}

func TestRunSyncConflictDoesNotAutoMerge(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	vault.writeNote("note.md", "local v2")
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 1,
		Files: map[string]manifest.Entry{
			"note.md": {ServerRevision: 1, ContentHash: testHashText("local v1"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.files["note.md"] = fileData(2, "note.md", "server v2")
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 2, path: "note.md", eventType: "UPDATE", contentHash: testHashPtr("server v2")}),
	}

	// When
	summary, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if len(summary.Conflicts) != 1 || summary.Conflicts[0] != "note.md" {
		t.Fatalf("conflicts = %#v", summary.Conflicts)
	}
	vault.requireFileContent("note.md", "local v2")
	conflicts := conflictFiles(t, vault.root)
	if len(conflicts) != 1 {
		t.Fatalf("conflict file count = %d", len(conflicts))
	}
	body := readFile(t, conflicts[0])
	if !containsAll(body, "local v2", "server v2", "- Server revision: 2") {
		t.Fatalf("conflict body missing expected content:\n%s", body)
	}
}

func TestRunSyncAttachmentConflictDoesNotAutoMerge(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	localRaw := []byte{0x01, 0x02, 0x03}
	serverRaw := []byte{0x09, 0x08, 0x07}
	vault.writeNote("Images/diagram.png", string(localRaw))
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 1,
		Files: map[string]manifest.Entry{
			"Images/diagram.png": {ServerRevision: 1, ContentHash: testHashBytes([]byte{0x00}), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.files["Images/diagram.png"] = fileDataBase64(2, "Images/diagram.png", serverRaw)
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 2, path: "Images/diagram.png", eventType: "UPDATE", contentHash: stringPtr(testHashBytes(serverRaw))}),
	}

	// When
	summary, err := RunSync(context.Background(), testConfigWithAttachments(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if len(summary.Conflicts) != 1 || summary.Conflicts[0] != "Images/diagram.png" {
		t.Fatalf("conflicts = %#v", summary.Conflicts)
	}
	vault.requireFileContent("Images/diagram.png", string(localRaw))
	matches, err := filepath.Glob(filepath.Join(vault.root, "Images", "*.conflict.*.png"))
	if err != nil {
		t.Fatalf("Glob() error = %v", err)
	}
	if len(matches) != 1 {
		t.Fatalf("conflict file count = %d, want 1 (%v)", len(matches), matches)
	}
	if readFile(t, matches[0]) != string(serverRaw) {
		t.Fatalf("conflict file content = %q, want server bytes %q", readFile(t, matches[0]), string(serverRaw))
	}
}

func TestRunSyncAttachmentDeleteConflictBacksUpLocalBytes(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	localRaw := []byte{0x01, 0x02, 0x03}
	vault.writeNote("Images/diagram.png", string(localRaw))
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 1,
		Files: map[string]manifest.Entry{
			"Images/diagram.png": {ServerRevision: 1, ContentHash: testHashBytes([]byte{0x00}), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 2, path: "Images/diagram.png", eventType: "DELETE", deleted: true}),
	}

	// When
	summary, err := RunSync(context.Background(), testConfigWithAttachments(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if len(summary.Conflicts) != 1 || summary.Conflicts[0] != "Images/diagram.png" {
		t.Fatalf("conflicts = %#v", summary.Conflicts)
	}
	if !exists(filepath.Join(vault.root, "Images", "diagram.png")) {
		t.Fatal("original diagram.png was removed, want it kept")
	}
	matches, err := filepath.Glob(filepath.Join(vault.root, "Images", "*.conflict.*.png"))
	if err != nil {
		t.Fatalf("Glob() error = %v", err)
	}
	if len(matches) != 1 {
		t.Fatalf("conflict file count = %d, want 1 (%v)", len(matches), matches)
	}
	if readFile(t, matches[0]) != string(localRaw) {
		t.Fatalf("conflict file content = %q, want local bytes %q", readFile(t, matches[0]), string(localRaw))
	}
}

func TestRunSyncSkipsAttachmentChanges_whenSyncAttachmentsDisabled(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 0,
		Files:          map[string]manifest.Entry{},
		Conflicts:      map[string]manifest.Conflict{},
	})
	raw := []byte{0x01, 0x02}
	fake := newFakeClient()
	fake.files["Images/diagram.png"] = fileDataBase64(2, "Images/diagram.png", raw)
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 2, path: "Images/diagram.png", eventType: "CREATE", contentHash: stringPtr(testHashBytes(raw))}),
	}

	// When
	summary, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if summary.Applied != 0 {
		t.Fatalf("summary.Applied = %d, want 0", summary.Applied)
	}
	if len(summary.Warnings) != 0 {
		t.Fatalf("summary.Warnings = %#v, want none (disabled attachments should be silently skipped)", summary.Warnings)
	}
	if exists(filepath.Join(vault.root, "Images", "diagram.png")) {
		t.Fatal("diagram.png was written even though sync_attachments is disabled")
	}
}

func TestRunSyncSkipsPullConflictPushInSameRun(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	vault.writeNote("note.md", "local v2")
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 1,
		Files: map[string]manifest.Entry{
			"note.md": {ServerRevision: 1, ContentHash: testHashText("local v1"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.files["note.md"] = fileData(2, "note.md", "server v2")
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 2, path: "note.md", eventType: "UPDATE", contentHash: testHashPtr("server v2")}),
	}

	// When
	_, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if len(fake.puts) != 0 {
		t.Fatalf("same-run conflict was pushed: %#v", fake.puts)
	}
	if len(conflictFiles(t, vault.root)) != 1 {
		t.Fatalf("conflict files = %#v", conflictFiles(t, vault.root))
	}
}

func TestRunSyncPushConflictWritesConflictFile(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	vault.writeNote("note.md", "local v2")
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 1,
		Files: map[string]manifest.Entry{
			"note.md": {ServerRevision: 1, ContentHash: testHashText("local v1"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.putConflict["note.md"] = conflictDetails(3, 1)
	fake.files["note.md"] = fileData(3, "note.md", "server v3")

	// When
	summary, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if len(summary.Conflicts) != 1 || summary.Conflicts[0] != "note.md" {
		t.Fatalf("conflicts = %#v", summary.Conflicts)
	}
	if len(conflictFiles(t, vault.root)) != 1 {
		t.Fatalf("conflict files = %#v", conflictFiles(t, vault.root))
	}
	vault.requireFileContent("note.md", "local v2")
}

func TestRunSyncPushesResolvedConflictWithConflictRevision(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	vault.writeNote("note.md", "resolved")
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 2,
		Files: map[string]manifest.Entry{
			"note.md": {ServerRevision: 1, ContentHash: testHashText("local v1"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{
			"note.md": {
				ServerRevision:    2,
				ServerContentHash: testHashPtr("server v2"),
				LocalContentHash:  testHashText("local v2"),
				ServerDeleted:     false,
			},
		},
	})
	fake := newFakeClient()
	fake.nextRevision = 3

	// When
	summary, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if summary.Pushed != 1 {
		t.Fatalf("summary = %#v", summary)
	}
	fake.requirePut(t, putCall{path: "note.md", baseRevision: 2, content: "resolved"})
	if _, ok := loadManifest(t, vault.root).Conflicts["note.md"]; ok {
		t.Fatal("resolved conflict still tracked")
	}
}

func TestRunSyncAcceptsServerDeleteWhenResolvedByLocalDelete(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 4,
		Files: map[string]manifest.Entry{
			"gone.md": {ServerRevision: 2, ContentHash: testHashText("local edit"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{
			"gone.md": {
				ServerRevision:   5,
				LocalContentHash: testHashText("local edit"),
				ServerDeleted:    true,
			},
		},
	})
	fake := newFakeClient()

	// When
	summary, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if summary.RemotelyDeleted != 0 || len(fake.deletes) != 0 {
		t.Fatalf("server delete acceptance pushed delete: summary=%#v deletes=%#v", summary, fake.deletes)
	}
	state := loadManifest(t, vault.root)
	if _, ok := state.Files["gone.md"]; ok {
		t.Fatal("gone.md still tracked")
	}
	if _, ok := state.Conflicts["gone.md"]; ok {
		t.Fatal("gone.md conflict still tracked")
	}
}
