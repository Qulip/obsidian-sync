package atomicfile

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestWriteBytes_createsTargetAndLeavesNoTempFiles(t *testing.T) {
	// Given
	path := filepath.Join(t.TempDir(), "nested", "manifest.json")

	// When
	err := WriteBytes(path, []byte(`{"ok":true}`))

	// Then
	if err != nil {
		t.Fatalf("WriteBytes returned error: %v", err)
	}
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("read target: %v", err)
	}
	if string(got) != `{"ok":true}` {
		t.Fatalf("target content = %q", string(got))
	}
	entries, err := os.ReadDir(filepath.Dir(path))
	if err != nil {
		t.Fatalf("read target dir: %v", err)
	}
	for _, entry := range entries {
		if strings.HasPrefix(entry.Name(), ".tmp-") {
			t.Fatalf("unexpected temp leftover %q", entry.Name())
		}
	}
}
