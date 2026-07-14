package engine

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Qulip/obsidian-sync/internal/syncagent/client"
	"github.com/Qulip/obsidian-sync/internal/syncagent/manifest"
)

func TestRunSyncPullWriteSkipsSymlinkedParent_whenTargetIsOutsideVault(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	outsideRoot := t.TempDir()
	if err := os.Symlink(outsideRoot, filepath.Join(vault.root, "linked")); err != nil {
		t.Skipf("symlink unavailable: %v", err)
	}
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 1,
		Files:          map[string]manifest.Entry{},
		Conflicts:      map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.files["linked/new.md"] = fileData(2, "linked/new.md", "server")
	fake.files["safe.md"] = fileData(3, "safe.md", "safe server")
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 2, path: "linked/new.md", eventType: "CREATE", contentHash: testHashPtr("server")}),
		syncChange(changeSpec{revision: 3, path: "safe.md", eventType: "CREATE", contentHash: testHashPtr("safe server")}),
	}

	// When
	summary, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	requireWarning(t, summary, "linked/new.md")
	if exists(filepath.Join(outsideRoot, "new.md")) {
		t.Fatal("server pull wrote through symlinked parent outside vault")
	}
	vault.requireFileContent("safe.md", "safe server")
	state := loadManifest(t, vault.root)
	if state.LastSyncCursor != 1 {
		t.Fatalf("LastSyncCursor = %d, want 1 after unsafe skip", state.LastSyncCursor)
	}
	if _, ok := state.Files["linked/new.md"]; ok {
		t.Fatal("skipped unsafe symlink path was added to manifest")
	}
}

func TestRunSyncKeepsSkippingSymlinkPathOnLaterRuns(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	outsideRoot := t.TempDir()
	outsidePath := filepath.Join(outsideRoot, "old.md")
	if err := os.WriteFile(outsidePath, []byte("old"), 0o644); err != nil {
		t.Fatalf("write outside file: %v", err)
	}
	if err := os.Symlink(outsideRoot, filepath.Join(vault.root, "linked")); err != nil {
		t.Skipf("symlink unavailable: %v", err)
	}
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 1,
		Files: map[string]manifest.Entry{
			"linked/old.md": {ServerRevision: 1, ContentHash: testHashText("old"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	changes := []client.SyncChangeItem{
		syncChange(changeSpec{revision: 2, path: "linked/old.md", eventType: "DELETE", deleted: true}),
	}

	// When
	for range 2 {
		fake := newFakeClient()
		fake.changes = changes
		summary, err := RunSync(context.Background(), testConfig(vault.root), Options{
			Client: fake,
			Now:    fixedNow,
		})

		// Then
		requireNoError(t, err)
		requireWarning(t, summary, "linked/old.md")
		if len(fake.deletes) != 0 {
			t.Fatalf("skipped unsafe symlink path was pushed as remote delete: %#v", fake.deletes)
		}
	}
	if !exists(outsidePath) {
		t.Fatal("server pull deleted outside-vault file through symlinked parent")
	}
	state := loadManifest(t, vault.root)
	if state.LastSyncCursor != 1 {
		t.Fatalf("LastSyncCursor = %d, want 1 so unsafe change is retried", state.LastSyncCursor)
	}
	if _, ok := state.Files["linked/old.md"]; !ok {
		t.Fatal("skipped unsafe symlink path was removed from manifest on a later run")
	}
}

func TestRunSyncPullDeleteSkipsSymlinkedParent_whenTargetIsOutsideVault(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	outsideRoot := t.TempDir()
	outsidePath := filepath.Join(outsideRoot, "old.md")
	if err := os.WriteFile(outsidePath, []byte("old"), 0o644); err != nil {
		t.Fatalf("write outside file: %v", err)
	}
	if err := os.Symlink(outsideRoot, filepath.Join(vault.root, "linked")); err != nil {
		t.Skipf("symlink unavailable: %v", err)
	}
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 1,
		Files: map[string]manifest.Entry{
			"linked/old.md": {ServerRevision: 1, ContentHash: testHashText("old"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	fake.files["safe.md"] = fileData(3, "safe.md", "safe server")
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 2, path: "linked/old.md", eventType: "DELETE", deleted: true}),
		syncChange(changeSpec{revision: 3, path: "safe.md", eventType: "CREATE", contentHash: testHashPtr("safe server")}),
	}

	// When
	summary, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	requireWarning(t, summary, "linked/old.md")
	if !exists(outsidePath) {
		t.Fatal("server pull deleted outside-vault file through symlinked parent")
	}
	vault.requireFileContent("safe.md", "safe server")
	state := loadManifest(t, vault.root)
	if _, ok := state.Files["safe.md"]; !ok {
		t.Fatal("safe server change was not added to manifest after symlink skip")
	}
	if _, ok := state.Files["linked/old.md"]; !ok {
		t.Fatal("skipped unsafe symlink path was removed from manifest during same run")
	}
	if len(fake.deletes) != 0 {
		t.Fatalf("skipped unsafe symlink path was pushed as remote delete: %#v", fake.deletes)
	}
}

func TestRunSyncPullDeleteSkipsSymlinkedFile_whenTargetIsOutsideVault(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	outsideRoot := t.TempDir()
	outsidePath := filepath.Join(outsideRoot, "old.md")
	if err := os.WriteFile(outsidePath, []byte("old"), 0o644); err != nil {
		t.Fatalf("write outside file: %v", err)
	}
	linkPath := filepath.Join(vault.root, "old.md")
	if err := os.Symlink(outsidePath, linkPath); err != nil {
		t.Skipf("symlink unavailable: %v", err)
	}
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
	fake.files["safe.md"] = fileData(3, "safe.md", "safe server")
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 2, path: "old.md", eventType: "DELETE", deleted: true}),
		syncChange(changeSpec{revision: 3, path: "safe.md", eventType: "CREATE", contentHash: testHashPtr("safe server")}),
	}

	// When
	summary, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	requireWarning(t, summary, "old.md")
	if !exists(outsidePath) {
		t.Fatal("server pull deleted outside-vault symlink target")
	}
	if !exists(linkPath) {
		t.Fatal("server pull removed symlink instead of rejecting it")
	}
	vault.requireFileContent("safe.md", "safe server")
	state := loadManifest(t, vault.root)
	if _, ok := state.Files["old.md"]; !ok {
		t.Fatal("skipped unsafe symlink path was removed from manifest during same run")
	}
	if len(fake.deletes) != 0 {
		t.Fatalf("skipped unsafe symlink path was pushed as remote delete: %#v", fake.deletes)
	}
}

func requireWarning(t *testing.T, summary Summary, path string) {
	t.Helper()
	for _, warning := range summary.Warnings {
		if strings.Contains(warning, path) {
			return
		}
	}
	t.Fatalf("summary warnings %q do not include %q", summary.Warnings, path)
}
