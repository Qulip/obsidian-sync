package scanner

import (
	"crypto/sha256"
	"encoding/hex"
	"os"
	"path/filepath"
	"strings"
	"testing"

	"github.com/Qulip/obsidian-sync/internal/syncagent/manifest"
)

const testAttachmentMaxBytes = int64(30_000_000)

func TestScanVault_hashesOnlySyncableMarkdownFiles(t *testing.T) {
	// Given
	vaultRoot := t.TempDir()
	writeFile(t, vaultRoot, "notes/a.md", "kept")
	writeFile(t, vaultRoot, "notes/image.png", "binary")
	writeFile(t, vaultRoot, ".obsidian/config.md", "ignored")
	writeFile(t, vaultRoot, ".obsidian-sync-agent/config.json", "{}")
	writeFile(t, vaultRoot, ".trash/old.md", "trashed")
	writeFile(t, vaultRoot, "notes/a.conflict.dev.20260707-000000.md", "conflict")

	// When
	got, _, warnings, err := ScanVault(vaultRoot, false, testAttachmentMaxBytes)

	// Then
	if err != nil {
		t.Fatalf("ScanVault returned error: %v", err)
	}
	if len(warnings) != 0 {
		t.Fatalf("warnings = %#v", warnings)
	}
	if len(got) != 1 {
		t.Fatalf("scanned = %#v", got)
	}
	file, ok := got["notes/a.md"]
	if !ok {
		t.Fatalf("notes/a.md missing from scan: %#v", got)
	}
	if file.ContentHash != sha256Text("kept") {
		t.Fatalf("content hash = %q", file.ContentHash)
	}
}

func TestScanVault_skipsSymlinkedMarkdownFile_whenTargetIsOutsideVault(t *testing.T) {
	// Given
	vaultRoot := t.TempDir()
	outsideRoot := t.TempDir()
	outsidePath := filepath.Join(outsideRoot, "outside.md")
	if err := os.WriteFile(outsidePath, []byte("outside secret"), 0o644); err != nil {
		t.Fatalf("write outside file: %v", err)
	}
	if err := os.Symlink(outsidePath, filepath.Join(vaultRoot, "linked.md")); err != nil {
		t.Skipf("symlink unavailable: %v", err)
	}
	writeFile(t, vaultRoot, "kept.md", "kept")

	// When
	got, _, _, err := ScanVault(vaultRoot, false, testAttachmentMaxBytes)

	// Then
	if err != nil {
		t.Fatalf("ScanVault returned error: %v", err)
	}
	if _, ok := got["linked.md"]; ok {
		t.Fatalf("symlinked file was scanned: %#v", got)
	}
	if got["kept.md"].ContentHash != sha256Text("kept") {
		t.Fatalf("kept.md hash = %q", got["kept.md"].ContentHash)
	}
}

func TestScanVault_includesAttachments_whenEnabled(t *testing.T) {
	// Given
	vaultRoot := t.TempDir()
	writeFile(t, vaultRoot, "notes/a.md", "kept")
	writeFile(t, vaultRoot, "Images/photo.png", "binary")

	// When
	got, _, warnings, err := ScanVault(vaultRoot, true, testAttachmentMaxBytes)

	// Then
	if err != nil {
		t.Fatalf("ScanVault returned error: %v", err)
	}
	if len(warnings) != 0 {
		t.Fatalf("warnings = %#v", warnings)
	}
	if len(got) != 2 {
		t.Fatalf("scanned = %#v", got)
	}
	if got["Images/photo.png"].ContentHash != sha256Text("binary") {
		t.Fatalf("Images/photo.png hash = %q", got["Images/photo.png"].ContentHash)
	}
}

func TestScanVault_skipsOversizedAttachmentWithWarning(t *testing.T) {
	// Given
	vaultRoot := t.TempDir()
	writeFile(t, vaultRoot, "Images/small.png", "ok")
	writeFile(t, vaultRoot, "Images/big.png", "too big for the limit")

	// When
	got, _, warnings, err := ScanVault(vaultRoot, true, int64(len("too big for the limit"))-1)

	// Then
	if err != nil {
		t.Fatalf("ScanVault returned error: %v", err)
	}
	if _, ok := got["Images/big.png"]; ok {
		t.Fatalf("oversized attachment was scanned: %#v", got)
	}
	if _, ok := got["Images/small.png"]; !ok {
		t.Fatalf("small.png missing from scan: %#v", got)
	}
	if len(warnings) != 1 || !strings.Contains(warnings[0], "Images/big.png") {
		t.Fatalf("warnings = %#v", warnings)
	}
}

func TestClassifyLocalChanges_sortsNewModifiedDeleted(t *testing.T) {
	// Given
	scanned := map[string]ScannedFile{
		"z-new.md":      {Path: "z-new.md", ContentHash: sha256Text("new")},
		"a-new.md":      {Path: "a-new.md", ContentHash: sha256Text("new")},
		"modified.md":   {Path: "modified.md", ContentHash: sha256Text("modified now")},
		"unchanged.md":  {Path: "unchanged.md", ContentHash: sha256Text("stable")},
		"b-modified.md": {Path: "b-modified.md", ContentHash: sha256Text("changed")},
	}
	state := manifest.Manifest{
		Files: map[string]manifest.Entry{
			"modified.md":   {ServerRevision: 1, ContentHash: sha256Text("old content")},
			"b-modified.md": {ServerRevision: 1, ContentHash: sha256Text("old b")},
			"unchanged.md":  {ServerRevision: 1, ContentHash: sha256Text("stable")},
			"z-deleted.md":  {ServerRevision: 1, ContentHash: sha256Text("gone")},
			"a-deleted.md":  {ServerRevision: 1, ContentHash: sha256Text("gone")},
		},
	}

	// When
	got := ClassifyLocalChanges(scanned, nil, state, true)

	// Then
	wantNew := []string{"a-new.md", "z-new.md"}
	wantModified := []string{"b-modified.md", "modified.md"}
	wantDeleted := []string{"a-deleted.md", "z-deleted.md"}
	if !equalStrings(got.New, wantNew) {
		t.Fatalf("new = %#v, want %#v", got.New, wantNew)
	}
	if !equalStrings(got.Modified, wantModified) {
		t.Fatalf("modified = %#v, want %#v", got.Modified, wantModified)
	}
	if !equalStrings(got.Deleted, wantDeleted) {
		t.Fatalf("deleted = %#v, want %#v", got.Deleted, wantDeleted)
	}
}

func writeFile(t *testing.T, root string, rel string, content string) {
	t.Helper()
	path := filepath.Join(root, filepath.FromSlash(rel))
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		t.Fatalf("mkdir %s: %v", filepath.Dir(path), err)
	}
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("write %s: %v", rel, err)
	}
}

func sha256Text(text string) string {
	sum := sha256.Sum256([]byte(text))
	return hex.EncodeToString(sum[:])
}

func equalStrings(left []string, right []string) bool {
	if len(left) != len(right) {
		return false
	}
	for index := range left {
		if left[index] != right[index] {
			return false
		}
	}
	return true
}
