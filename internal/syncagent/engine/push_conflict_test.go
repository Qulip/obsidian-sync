package engine

import (
	"context"
	"errors"
	"net/http"
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
