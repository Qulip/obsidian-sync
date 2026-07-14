package engine

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/Qulip/obsidian-sync/internal/syncagent/client"
	"github.com/Qulip/obsidian-sync/internal/syncagent/config"
	"github.com/Qulip/obsidian-sync/internal/syncagent/conflict"
	"github.com/Qulip/obsidian-sync/internal/syncagent/manifest"
)

// conflictGlob asserts exactly one file matches pattern and returns its
// content, distinguishing manual conflict files ("*.conflict.*.md") from
// remote-wins local backups ("*.local-backup.conflict.*.md") the way the
// pattern itself (not the shared conflictFiles() substring helper) does.
func conflictGlob(t *testing.T, pattern string) string {
	t.Helper()
	matches, err := filepath.Glob(pattern)
	if err != nil {
		t.Fatalf("Glob(%q) error = %v", pattern, err)
	}
	if len(matches) != 1 {
		t.Fatalf("matches for %q = %v, want exactly 1", pattern, matches)
	}
	return readFile(t, matches[0])
}

func conflictGlobCount(t *testing.T, pattern string) int {
	t.Helper()
	matches, err := filepath.Glob(pattern)
	if err != nil {
		t.Fatalf("Glob(%q) error = %v", pattern, err)
	}
	return len(matches)
}

// --- Pull write conflict (server + local both changed) ---

func TestRunSyncPullWriteConflict_localWinsPushesLocalContentAndKeepsFile(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	vault.writeNote("note.md", "LOCAL DIRTY CONTENT")
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 4,
		Files: map[string]manifest.Entry{
			"note.md": {ServerRevision: 4, ContentHash: testHashText("OLD SYNCED CONTENT"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.nextRevision = 6
	fake.files["note.md"] = fileData(5, "note.md", "SERVER CONTENT")
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 5, path: "note.md", eventType: "UPDATE", contentHash: testHashPtr("SERVER CONTENT")}),
	}

	// When
	summary, err := RunSync(context.Background(), testConfigWithPolicy(vault.root, config.ConflictPolicyLocalWins), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if len(summary.Conflicts) != 0 || summary.Pushed != 1 {
		t.Fatalf("summary = %#v", summary)
	}
	vault.requireFileContent("note.md", "LOCAL DIRTY CONTENT")
	fake.requirePut(t, putCall{path: "note.md", baseRevision: 5, content: "LOCAL DIRTY CONTENT"})
	state := loadManifest(t, vault.root)
	if state.Files["note.md"].ServerRevision != 6 {
		t.Fatalf("note.md revision = %d, want 6", state.Files["note.md"].ServerRevision)
	}
	if _, ok := state.Conflicts["note.md"]; ok {
		t.Fatal("note.md still tracked as a conflict")
	}
	if conflictGlobCount(t, filepath.Join(vault.root, "note.conflict.dev.*.md")) != 0 {
		t.Fatal("manual conflict file written, want none")
	}
}

func TestRunSyncPullWriteConflict_localWinsFallsBackToManual_afterExhaustingRetries(t *testing.T) {
	// Given: PutFile conflicts on every attempt, so local-wins must exhaust
	// its bounded retries and fall back to a manual conflict file.
	vault := newVaultFixture(t)
	vault.writeNote("note.md", "LOCAL DIRTY CONTENT")
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 4,
		Files: map[string]manifest.Entry{
			"note.md": {ServerRevision: 4, ContentHash: testHashText("OLD SYNCED CONTENT"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.files["note.md"] = fileData(5, "note.md", "SERVER CONTENT")
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 5, path: "note.md", eventType: "UPDATE", contentHash: testHashPtr("SERVER CONTENT")}),
	}
	fake.putConflict["note.md"] = conflictDetails(5, 0)

	// When
	summary, err := RunSync(context.Background(), testConfigWithPolicy(vault.root, config.ConflictPolicyLocalWins), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if len(summary.Conflicts) != 1 || summary.Conflicts[0] != "note.md" {
		t.Fatalf("conflicts = %#v", summary.Conflicts)
	}
	if len(summary.Warnings) != 1 {
		t.Fatalf("warnings = %#v, want a fallback-to-manual warning", summary.Warnings)
	}
	vault.requireFileContent("note.md", "LOCAL DIRTY CONTENT")
	body := conflictGlob(t, filepath.Join(vault.root, "note.conflict.dev.*.md"))
	if !containsAll(body, "LOCAL DIRTY CONTENT", "SERVER CONTENT") {
		t.Fatalf("conflict body missing expected content:\n%s", body)
	}
}

func TestRunSyncPullWriteConflict_remoteWinsBacksUpLocalAndAdoptsServerContent(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	vault.writeNote("note.md", "LOCAL DIRTY CONTENT")
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 4,
		Files: map[string]manifest.Entry{
			"note.md": {ServerRevision: 4, ContentHash: testHashText("OLD SYNCED CONTENT"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.files["note.md"] = fileData(5, "note.md", "SERVER CONTENT")
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 5, path: "note.md", eventType: "UPDATE", contentHash: testHashPtr("SERVER CONTENT")}),
	}

	// When
	summary, err := RunSync(context.Background(), testConfigWithPolicy(vault.root, config.ConflictPolicyRemoteWins), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if len(summary.Conflicts) != 0 || summary.Applied != 1 {
		t.Fatalf("summary = %#v", summary)
	}
	vault.requireFileContent("note.md", "SERVER CONTENT")
	state := loadManifest(t, vault.root)
	if state.Files["note.md"].ServerRevision != 5 {
		t.Fatalf("note.md revision = %d, want 5", state.Files["note.md"].ServerRevision)
	}
	body := conflictGlob(t, filepath.Join(vault.root, "note.local-backup.conflict.dev.*.md"))
	if !containsAll(body, "remote-wins", "LOCAL DIRTY CONTENT") {
		t.Fatalf("backup body missing expected content:\n%s", body)
	}
	if conflictGlobCount(t, filepath.Join(vault.root, "note.conflict.dev.*.md")) != 0 {
		t.Fatal("manual conflict file written, want none")
	}
}

// --- Pull delete conflict (server deleted, local diverged) ---

func TestRunSyncPullDeleteConflict_localWinsRecreatesFileOnServer(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	vault.writeNote("note.md", "LOCAL DIRTY CONTENT")
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 4,
		Files: map[string]manifest.Entry{
			"note.md": {ServerRevision: 4, ContentHash: testHashText("OLD SYNCED CONTENT"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.nextRevision = 6
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 5, path: "note.md", eventType: "DELETE", deleted: true}),
	}

	// When
	summary, err := RunSync(context.Background(), testConfigWithPolicy(vault.root, config.ConflictPolicyLocalWins), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if len(summary.Conflicts) != 0 || summary.Pushed != 1 {
		t.Fatalf("summary = %#v", summary)
	}
	vault.requireFileContent("note.md", "LOCAL DIRTY CONTENT")
	// Recreating a server-deleted file always uses base_revision=0.
	fake.requirePut(t, putCall{path: "note.md", baseRevision: 0, content: "LOCAL DIRTY CONTENT"})
	state := loadManifest(t, vault.root)
	if state.Files["note.md"].ServerRevision != 6 {
		t.Fatalf("note.md revision = %d, want 6", state.Files["note.md"].ServerRevision)
	}
}

func TestRunSyncPullDeleteConflict_remoteWinsBacksUpAndDeletesLocally(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	vault.writeNote("note.md", "LOCAL DIRTY CONTENT")
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 4,
		Files: map[string]manifest.Entry{
			"note.md": {ServerRevision: 4, ContentHash: testHashText("OLD SYNCED CONTENT"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 5, path: "note.md", eventType: "DELETE", deleted: true}),
	}

	// When
	summary, err := RunSync(context.Background(), testConfigWithPolicy(vault.root, config.ConflictPolicyRemoteWins), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if len(summary.Conflicts) != 0 || summary.LocallyDeleted != 1 {
		t.Fatalf("summary = %#v", summary)
	}
	if exists(filepath.Join(vault.root, "note.md")) {
		t.Fatal("note.md still exists, want it deleted")
	}
	state := loadManifest(t, vault.root)
	if _, ok := state.Files["note.md"]; ok {
		t.Fatal("note.md still tracked")
	}
	body := conflictGlob(t, filepath.Join(vault.root, "note.local-backup.conflict.dev.*.md"))
	if !containsAll(body, "LOCAL DIRTY CONTENT") {
		t.Fatalf("backup body missing local content:\n%s", body)
	}
}

func TestRunSyncPullDeleteConflict_manualWritesConflictFileAndLeavesLocalUntouched(t *testing.T) {
	// Given: default (manual) policy.
	vault := newVaultFixture(t)
	vault.writeNote("note.md", "LOCAL DIRTY CONTENT")
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 4,
		Files: map[string]manifest.Entry{
			"note.md": {ServerRevision: 4, ContentHash: testHashText("OLD SYNCED CONTENT"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 5, path: "note.md", eventType: "DELETE", deleted: true}),
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
	vault.requireFileContent("note.md", "LOCAL DIRTY CONTENT")
	body := conflictGlob(t, filepath.Join(vault.root, "note.conflict.dev.*.md"))
	if !containsAll(body, "LOCAL DIRTY CONTENT", conflict.ServerDeletedPlaceholder) {
		t.Fatalf("conflict body missing expected content:\n%s", body)
	}
	state := loadManifest(t, vault.root)
	if !state.Conflicts["note.md"].ServerDeleted {
		t.Fatal("conflict not tracked as server-deleted")
	}
}

// --- Push upsert conflict (our own PUT was rejected with 409) ---

func TestRunSyncPushConflict_localWinsResolvesViaRetry(t *testing.T) {
	// Given: the first PUT attempt conflicts, the retry (using the
	// server-reported revision) succeeds.
	vault := newVaultFixture(t)
	vault.writeNote("note.md", "LOCAL NEW CONTENT")
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 4,
		Files: map[string]manifest.Entry{
			"note.md": {ServerRevision: 4, ContentHash: testHashText("OLD CONTENT"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.nextRevision = 8
	fake.putConflict["note.md"] = conflictDetails(7, 0)
	fake.putConflictAttempts["note.md"] = 1

	// When
	summary, err := RunSync(context.Background(), testConfigWithPolicy(vault.root, config.ConflictPolicyLocalWins), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if len(summary.Conflicts) != 0 || summary.Pushed != 1 {
		t.Fatalf("summary = %#v", summary)
	}
	fake.requirePut(t, putCall{path: "note.md", baseRevision: 7, content: "LOCAL NEW CONTENT"})
	state := loadManifest(t, vault.root)
	if state.Files["note.md"].ServerRevision != 8 {
		t.Fatalf("note.md revision = %d, want 8", state.Files["note.md"].ServerRevision)
	}
}

func TestRunSyncPushConflict_localWinsFallsBackToManual_afterExhaustingRetries(t *testing.T) {
	// Given: PutFile conflicts on every attempt.
	vault := newVaultFixture(t)
	vault.writeNote("note.md", "LOCAL NEW CONTENT")
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 4,
		Files: map[string]manifest.Entry{
			"note.md": {ServerRevision: 4, ContentHash: testHashText("OLD CONTENT"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.putConflict["note.md"] = conflictDetails(7, 0)
	fake.files["note.md"] = fileData(7, "note.md", "SERVER CONTENT")

	// When
	summary, err := RunSync(context.Background(), testConfigWithPolicy(vault.root, config.ConflictPolicyLocalWins), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if len(summary.Conflicts) != 1 || summary.Conflicts[0] != "note.md" {
		t.Fatalf("conflicts = %#v", summary.Conflicts)
	}
	if len(summary.Warnings) != 1 {
		t.Fatalf("warnings = %#v, want a fallback-to-manual warning", summary.Warnings)
	}
	vault.requireFileContent("note.md", "LOCAL NEW CONTENT")
	if conflictGlobCount(t, filepath.Join(vault.root, "note.conflict.dev.*.md")) != 1 {
		t.Fatal("want exactly one manual conflict file")
	}
}

func TestRunSyncPushConflict_remoteWinsAdoptsServerContentAndBacksUpLocal(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	vault.writeNote("note.md", "LOCAL NEW CONTENT")
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 4,
		Files: map[string]manifest.Entry{
			"note.md": {ServerRevision: 4, ContentHash: testHashText("OLD CONTENT"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.putConflict["note.md"] = conflictDetails(7, 0)
	fake.files["note.md"] = fileData(7, "note.md", "SERVER CONTENT")

	// When
	summary, err := RunSync(context.Background(), testConfigWithPolicy(vault.root, config.ConflictPolicyRemoteWins), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if len(summary.Conflicts) != 0 || summary.Applied != 1 {
		t.Fatalf("summary = %#v", summary)
	}
	vault.requireFileContent("note.md", "SERVER CONTENT")
	state := loadManifest(t, vault.root)
	if state.Files["note.md"].ServerRevision != 7 {
		t.Fatalf("note.md revision = %d, want 7", state.Files["note.md"].ServerRevision)
	}
	body := conflictGlob(t, filepath.Join(vault.root, "note.local-backup.conflict.dev.*.md"))
	if !containsAll(body, "LOCAL NEW CONTENT") {
		t.Fatalf("backup body missing local content:\n%s", body)
	}
}

// --- Push delete conflict (our own DELETE was rejected with 409) ---

func TestRunSyncPushDeleteConflict_localWinsResolvesViaRetry(t *testing.T) {
	// Given: note.md was removed locally (no vault.writeNote call), so the
	// scanner classifies it as a local delete; the first DELETE attempt
	// conflicts, the retry succeeds.
	vault := newVaultFixture(t)
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 4,
		Files: map[string]manifest.Entry{
			"note.md": {ServerRevision: 4, ContentHash: testHashText("SYNCED CONTENT"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.deleteConflict["note.md"] = conflictDetails(7, 0)
	fake.deleteConflictAttempts["note.md"] = 1

	// When
	summary, err := RunSync(context.Background(), testConfigWithPolicy(vault.root, config.ConflictPolicyLocalWins), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if len(summary.Conflicts) != 0 || summary.RemotelyDeleted != 1 {
		t.Fatalf("summary = %#v", summary)
	}
	fake.requireDelete(t, deleteCall{path: "note.md", baseRevision: 7})
	state := loadManifest(t, vault.root)
	if _, ok := state.Files["note.md"]; ok {
		t.Fatal("note.md still tracked")
	}
}

func TestRunSyncPushDeleteConflict_remoteWinsRestoresServerContentLocally(t *testing.T) {
	// Given: the server still has content for the path (a concurrent edit),
	// so remote-wins recreates the file locally instead of leaving it
	// deleted. There is no local content to back up since it was already
	// removed locally before the push was attempted.
	vault := newVaultFixture(t)
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 4,
		Files: map[string]manifest.Entry{
			"note.md": {ServerRevision: 4, ContentHash: testHashText("SYNCED CONTENT"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.deleteConflict["note.md"] = conflictDetails(7, 0)
	fake.files["note.md"] = fileData(7, "note.md", "SERVER CONTENT")

	// When
	summary, err := RunSync(context.Background(), testConfigWithPolicy(vault.root, config.ConflictPolicyRemoteWins), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if len(summary.Conflicts) != 0 || summary.Applied != 1 {
		t.Fatalf("summary = %#v", summary)
	}
	vault.requireFileContent("note.md", "SERVER CONTENT")
	state := loadManifest(t, vault.root)
	if state.Files["note.md"].ServerRevision != 7 {
		t.Fatalf("note.md revision = %d, want 7", state.Files["note.md"].ServerRevision)
	}
	if conflictGlobCount(t, filepath.Join(vault.root, "note.local-backup.conflict.dev.*.md")) != 0 {
		t.Fatal("backup file written, want none since there was no local content to discard")
	}
}

func TestRunSyncPushDeleteConflict_manualWritesConflictFile(t *testing.T) {
	// Given: default (manual) policy.
	vault := newVaultFixture(t)
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 4,
		Files: map[string]manifest.Entry{
			"note.md": {ServerRevision: 4, ContentHash: testHashText("SYNCED CONTENT"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.deleteConflict["note.md"] = conflictDetails(7, 0)
	fake.files["note.md"] = fileData(7, "note.md", "SERVER CONTENT")

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
	if exists(filepath.Join(vault.root, "note.md")) {
		t.Fatal("note.md was recreated locally, want manual policy to leave it deleted")
	}
	body := conflictGlob(t, filepath.Join(vault.root, "note.conflict.dev.*.md"))
	if !containsAll(body, conflict.LocalDeletedPlaceholder, "SERVER CONTENT") {
		t.Fatalf("conflict body missing expected content:\n%s", body)
	}
}
