package engine

import (
	"context"
	"os"
	"path/filepath"
	"testing"

	"github.com/Qulip/obsidian-sync/internal/syncagent/client"
	"github.com/Qulip/obsidian-sync/internal/syncagent/manifest"
)

func TestRunSyncPullWriteRejectsSymlinkedParent_whenTargetIsOutsideVault(t *testing.T) {
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
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 2, path: "linked/new.md", eventType: "CREATE", contentHash: testHashPtr("server")}),
	}

	// When
	_, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	if err == nil {
		t.Fatal("RunSync() error = nil, want symlink rejection")
	}
	if exists(filepath.Join(outsideRoot, "new.md")) {
		t.Fatal("server pull wrote through symlinked parent outside vault")
	}
}

func TestRunSyncPullDeleteRejectsSymlinkedParent_whenTargetIsOutsideVault(t *testing.T) {
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
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 2, path: "linked/old.md", eventType: "DELETE", deleted: true}),
	}

	// When
	_, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	if err == nil {
		t.Fatal("RunSync() error = nil, want symlink rejection")
	}
	if !exists(outsidePath) {
		t.Fatal("server pull deleted outside-vault file through symlinked parent")
	}
}

func TestRunSyncPullDeleteRejectsSymlinkedFile_whenTargetIsOutsideVault(t *testing.T) {
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
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 2, path: "old.md", eventType: "DELETE", deleted: true}),
	}

	// When
	_, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	if err == nil {
		t.Fatal("RunSync() error = nil, want symlink rejection")
	}
	if !exists(outsidePath) {
		t.Fatal("server pull deleted outside-vault symlink target")
	}
	if !exists(linkPath) {
		t.Fatal("server pull removed symlink instead of rejecting it")
	}
}
