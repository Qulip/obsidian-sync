package conflict

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestFilename_matchesConflictPattern_whenPathHasDirectory(t *testing.T) {
	// Given
	moment := time.Date(2026, 7, 7, 12, 30, 45, 0, time.UTC)

	// When
	got := Filename("notes/JPA.md", "laptop", moment)

	// Then
	want := "JPA.conflict.laptop.20260707-123045.md"
	if got != want {
		t.Fatalf("Filename() = %q, want %q", got, want)
	}
}

func TestWriteConflictFile_writesExpectedSections_whenConflictExists(t *testing.T) {
	// Given
	root := t.TempDir()
	moment := time.Date(2026, 7, 7, 12, 30, 45, 0, time.UTC)

	// When
	got, err := WriteFile(Request{
		VaultRoot:          root,
		Path:               "notes/JPA.md",
		DeviceID:           "laptop",
		ClientBaseRevision: 2,
		ServerRevision:     3,
		LocalContent:       "LOCAL BODY",
		ServerContent:      "SERVER BODY",
		Now:                moment,
	})

	// Then
	if err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}
	wantPath := filepath.Join(root, "notes", "JPA.conflict.laptop.20260707-123045.md")
	if got != wantPath {
		t.Fatalf("WriteFile() path = %q, want %q", got, wantPath)
	}
	body, err := os.ReadFile(got)
	if err != nil {
		t.Fatalf("read conflict file: %v", err)
	}
	text := string(body)
	for _, want := range []string{
		"# Sync Conflict",
		"- Path: notes/JPA.md",
		"- Device: laptop",
		"- Client base revision: 2",
		"- Server revision: 3",
		"- Created at: 2026-07-07T12:30:45Z",
		"## Local Version",
		"LOCAL BODY",
		"## Server Version",
		"SERVER BODY",
	} {
		if !strings.Contains(text, want) {
			t.Fatalf("conflict body missing %q:\n%s", want, text)
		}
	}
	if strings.Index(text, "## Local Version") > strings.Index(text, "## Server Version") {
		t.Fatalf("local section appears after server section:\n%s", text)
	}
	t.Log("verified conflict content includes Local Version and Server Version")
}

func TestWriteConflictFile_reusesExistingFile_whenSameDeviceAndServerRevision(t *testing.T) {
	// Given
	root := t.TempDir()
	firstMoment := time.Date(2026, 7, 7, 12, 30, 45, 0, time.UTC)
	secondMoment := time.Date(2026, 7, 7, 12, 31, 45, 0, time.UTC)
	req := Request{
		VaultRoot:          root,
		Path:               "notes/JPA.md",
		DeviceID:           "laptop",
		ClientBaseRevision: 2,
		ServerRevision:     3,
		LocalContent:       "LOCAL A",
		ServerContent:      "SERVER A",
		Now:                firstMoment,
	}
	first, err := WriteFile(req)
	if err != nil {
		t.Fatalf("first WriteFile() error = %v", err)
	}

	// When
	req.LocalContent = "LOCAL B"
	req.ServerContent = "SERVER B"
	req.Now = secondMoment
	second, err := WriteFile(req)

	// Then
	if err != nil {
		t.Fatalf("second WriteFile() error = %v", err)
	}
	if second != first {
		t.Fatalf("second WriteFile() path = %q, want reused %q", second, first)
	}
	matches, err := filepath.Glob(filepath.Join(root, "notes", "*.conflict.*.md"))
	if err != nil {
		t.Fatalf("glob conflicts: %v", err)
	}
	if len(matches) != 1 {
		t.Fatalf("conflict file count = %d, want 1 (%v)", len(matches), matches)
	}
	body, err := os.ReadFile(first)
	if err != nil {
		t.Fatalf("read conflict file: %v", err)
	}
	if !strings.Contains(string(body), "LOCAL A") {
		t.Fatalf("original conflict content was overwritten:\n%s", string(body))
	}
}

func TestWriteConflictFile_writesDeletedPlaceholders_whenContentUsesPlaceholders(t *testing.T) {
	// Given
	root := t.TempDir()

	// When
	got, err := WriteFile(Request{
		VaultRoot:          root,
		Path:               "note.md",
		DeviceID:           "laptop",
		ClientBaseRevision: 2,
		ServerRevision:     3,
		LocalContent:       LocalDeletedPlaceholder,
		ServerContent:      ServerDeletedPlaceholder,
		Now:                time.Date(2026, 7, 7, 12, 30, 45, 0, time.UTC),
	})

	// Then
	if err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}
	body, err := os.ReadFile(got)
	if err != nil {
		t.Fatalf("read conflict file: %v", err)
	}
	text := string(body)
	if !strings.Contains(text, LocalDeletedPlaceholder) {
		t.Fatalf("conflict body missing local deleted placeholder:\n%s", text)
	}
	if !strings.Contains(text, ServerDeletedPlaceholder) {
		t.Fatalf("conflict body missing server deleted placeholder:\n%s", text)
	}
}
