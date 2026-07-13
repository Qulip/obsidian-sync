package engine

import (
	"context"
	"errors"
	"net/http"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Qulip/obsidian-sync/internal/syncagent/client"
	"github.com/Qulip/obsidian-sync/internal/syncagent/conflict"
	"github.com/Qulip/obsidian-sync/internal/syncagent/manifest"
)

func TestRunSyncPushConflictFails_whenServerFileFetchReturnsNonNotFound(t *testing.T) {
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
	fake.getFileErrors["note.md"] = &client.APIError{
		Message:    "unauthorized",
		StatusCode: http.StatusUnauthorized,
	}

	// When
	_, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	if !errors.Is(err, ErrSync) {
		t.Fatalf("error = %v, want ErrSync", err)
	}
	if !strings.Contains(err.Error(), "get server file note.md after conflict") {
		t.Fatalf("error = %v", err)
	}
	if conflicts := conflictFiles(t, vault.root); len(conflicts) != 0 {
		t.Fatalf("conflict files = %#v", conflicts)
	}
}

func TestRunSyncPushConflictWritesDeletedPlaceholder_whenServerFileIsNotFound(t *testing.T) {
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
	fake.getFileErrors["note.md"] = &client.APIError{
		Message:    "not found",
		StatusCode: http.StatusNotFound,
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
	conflicts := conflictFiles(t, vault.root)
	if len(conflicts) != 1 {
		t.Fatalf("conflict files = %#v", conflicts)
	}
	body := readFile(t, conflicts[0])
	if !strings.Contains(body, conflict.ServerDeletedPlaceholder) {
		t.Fatalf("conflict body missing deleted placeholder:\n%s", body)
	}
}

func TestRunSyncPushConflictWritesServerBytes_forAttachment(t *testing.T) {
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
	fake.putConflict["Images/diagram.png"] = conflictDetails(3, 1)
	fake.files["Images/diagram.png"] = fileDataBase64(3, "Images/diagram.png", serverRaw)

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
	vault.requireFileContent("Images/diagram.png", string(localRaw))
}

func TestRunSyncPushConflictWarnsWithoutFile_whenServerAttachmentIsNotFound(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	vault.writeNote("Images/diagram.png", "local-bytes")
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
	fake.putConflict["Images/diagram.png"] = conflictDetails(3, 1)
	fake.getFileErrors["Images/diagram.png"] = &client.APIError{
		Message:    "not found",
		StatusCode: http.StatusNotFound,
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
	if len(summary.Warnings) != 1 || !strings.Contains(summary.Warnings[0], "Images/diagram.png") {
		t.Fatalf("warnings = %#v", summary.Warnings)
	}
	matches, err := filepath.Glob(filepath.Join(vault.root, "Images", "*.conflict.*.png"))
	if err != nil {
		t.Fatalf("Glob() error = %v", err)
	}
	if len(matches) != 0 {
		t.Fatalf("conflict files = %#v, want none since server has nothing to compare", matches)
	}
}
