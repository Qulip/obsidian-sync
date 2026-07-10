package engine

import (
	"context"
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
