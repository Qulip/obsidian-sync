package manifest

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestLoad_returnsEmptyManifest_whenManifestIsMissing(t *testing.T) {
	// Given
	vaultRoot := t.TempDir()

	// When
	got, err := Load(vaultRoot)

	// Then
	if err != nil {
		t.Fatalf("Load returned error: %v", err)
	}
	if got.VaultID != "" || got.DeviceID != "" || got.LastSyncCursor != 0 {
		t.Fatalf("metadata = %#v", got)
	}
	if len(got.Files) != 0 {
		t.Fatalf("files = %#v", got.Files)
	}
	if len(got.Conflicts) != 0 {
		t.Fatalf("conflicts = %#v", got.Conflicts)
	}
}

func TestLoad_returnsEmptyManifest_whenManifestIsNotObject(t *testing.T) {
	// Given
	vaultRoot := t.TempDir()
	path := Path(vaultRoot)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir manifest dir: %v", err)
	}
	if err := os.WriteFile(path, []byte(`[]`), 0o644); err != nil {
		t.Fatalf("write manifest: %v", err)
	}

	// When
	got, err := Load(vaultRoot)

	// Then
	if err != nil {
		t.Fatalf("Load returned error: %v", err)
	}
	if len(got.Files) != 0 || len(got.Conflicts) != 0 {
		t.Fatalf("manifest = %#v", got)
	}
}

func TestLoad_returnsError_whenManifestIsCorruptJSON(t *testing.T) {
	// Given
	vaultRoot := t.TempDir()
	path := Path(vaultRoot)
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir manifest dir: %v", err)
	}
	if err := os.WriteFile(path, []byte(`not-json`), 0o644); err != nil {
		t.Fatalf("write manifest: %v", err)
	}

	// When
	_, err := Load(vaultRoot)

	// Then
	if err == nil {
		t.Fatal("Load returned nil error")
	}
}

func TestSaveAndLoad_roundTripsManifestShape(t *testing.T) {
	// Given
	vaultRoot := t.TempDir()
	original := Manifest{
		VaultID:        "main",
		DeviceID:       "laptop",
		LastSyncCursor: 7,
		Files: map[string]Entry{
			"notes/JPA.md": {
				ServerRevision: 3,
				ContentHash:    strings.Repeat("a", 64),
				LastSyncedAt:   "2026-07-07T00:00:00+00:00",
			},
		},
		Conflicts: map[string]Conflict{
			"notes/JPA.md": {
				ServerRevision:    4,
				ServerContentHash: ptrString(strings.Repeat("b", 64)),
				LocalContentHash:  strings.Repeat("c", 64),
				ServerDeleted:     true,
			},
		},
	}

	// When
	if err := Save(vaultRoot, original); err != nil {
		t.Fatalf("Save returned error: %v", err)
	}
	loaded, err := Load(vaultRoot)

	// Then
	if err != nil {
		t.Fatalf("Load returned error: %v", err)
	}
	if loaded.VaultID != original.VaultID ||
		loaded.DeviceID != original.DeviceID ||
		loaded.LastSyncCursor != original.LastSyncCursor {
		t.Fatalf("loaded metadata = %#v", loaded)
	}
	if loaded.Files["notes/JPA.md"] != original.Files["notes/JPA.md"] {
		t.Fatalf("loaded files = %#v", loaded.Files)
	}
	gotConflict := loaded.Conflicts["notes/JPA.md"]
	if gotConflict.ServerContentHash == nil || *gotConflict.ServerContentHash != strings.Repeat("b", 64) {
		t.Fatalf("server_content_hash = %#v", gotConflict.ServerContentHash)
	}
	if gotConflict.ServerRevision != 4 ||
		gotConflict.LocalContentHash != strings.Repeat("c", 64) ||
		!gotConflict.ServerDeleted {
		t.Fatalf("loaded conflict = %#v", gotConflict)
	}
}

func TestSave_writesManifestPathAndLeavesNoTempFiles(t *testing.T) {
	// Given
	vaultRoot := t.TempDir()

	// When
	err := Save(vaultRoot, Manifest{VaultID: "main", DeviceID: "laptop"})

	// Then
	if err != nil {
		t.Fatalf("Save returned error: %v", err)
	}
	if _, err := os.Stat(Path(vaultRoot)); err != nil {
		t.Fatalf("manifest path missing: %v", err)
	}
	entries, err := os.ReadDir(filepath.Join(vaultRoot, ".obsidian-sync-agent"))
	if err != nil {
		t.Fatalf("read config dir: %v", err)
	}
	for _, entry := range entries {
		if strings.HasPrefix(entry.Name(), ".tmp-") {
			t.Fatalf("unexpected temp leftover %q", entry.Name())
		}
	}
}

func ptrString(value string) *string {
	return &value
}
