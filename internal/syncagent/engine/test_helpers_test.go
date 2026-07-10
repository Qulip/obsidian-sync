package engine

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"testing"
	"time"

	"github.com/Qulip/obsidian-sync/internal/syncagent/client"
	"github.com/Qulip/obsidian-sync/internal/syncagent/config"
	"github.com/Qulip/obsidian-sync/internal/syncagent/manifest"
)

const fixedNowString = "2026-07-09T00:00:00Z"

func fixedNow() time.Time {
	return time.Date(2026, 7, 9, 0, 0, 0, 0, time.UTC)
}

func testConfig(root string) config.AgentConfig {
	return config.AgentConfig{
		ServerBaseURL: "http://127.0.0.1:1",
		VaultID:       "vault",
		VaultRoot:     root,
		DeviceID:      "dev",
	}
}

type changeSpec struct {
	revision    int
	path        string
	eventType   string
	deleted     bool
	contentHash *string
}

func syncChange(spec changeSpec) client.SyncChangeItem {
	return client.SyncChangeItem{
		Revision:    spec.revision,
		Path:        spec.path,
		EventType:   spec.eventType,
		ContentHash: spec.contentHash,
		Deleted:     spec.deleted,
		CreatedAt:   client.APITime{Time: fixedNow()},
	}
}

func fileData(revision int, path string, content string) client.FileContentData {
	return client.FileContentData{
		VaultID:     "vault",
		Path:        path,
		Revision:    revision,
		ContentHash: testHashText(content),
		Content:     content,
		Deleted:     false,
	}
}

func conflictDetails(serverRevision int, clientBaseRevision int) map[string]json.RawMessage {
	return map[string]json.RawMessage{
		"server_revision":      rawInt(serverRevision),
		"client_base_revision": rawInt(clientBaseRevision),
	}
}

func rawInt(value int) json.RawMessage {
	return json.RawMessage(strconv.Itoa(value))
}

type vaultFixture struct {
	t    *testing.T
	root string
}

func newVaultFixture(t *testing.T) vaultFixture {
	t.Helper()
	return vaultFixture{t: t, root: t.TempDir()}
}

func (v vaultFixture) writeNote(path string, content string) {
	v.t.Helper()
	fullPath := filepath.Join(v.root, filepath.FromSlash(path))
	if err := os.MkdirAll(filepath.Dir(fullPath), 0o755); err != nil {
		v.t.Fatalf("MkdirAll() error = %v", err)
	}
	if err := os.WriteFile(fullPath, []byte(content), 0o644); err != nil {
		v.t.Fatalf("WriteFile() error = %v", err)
	}
}

func readFile(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatalf("ReadFile() error = %v", err)
	}
	return string(data)
}

func (v vaultFixture) requireFileContent(path string, want string) {
	v.t.Helper()
	got := readFile(v.t, filepath.Join(v.root, filepath.FromSlash(path)))
	if got != want {
		v.t.Fatalf("%s content = %q, want %q", path, got, want)
	}
}

func saveManifest(t *testing.T, root string, state manifest.Manifest) {
	t.Helper()
	if err := manifest.Save(root, state); err != nil {
		t.Fatalf("Save() error = %v", err)
	}
}

func loadManifest(t *testing.T, root string) manifest.Manifest {
	t.Helper()
	state, err := manifest.Load(root)
	if err != nil {
		t.Fatalf("Load() error = %v", err)
	}
	return state
}

func testHashText(content string) string {
	sum := sha256.Sum256([]byte(content))
	return hex.EncodeToString(sum[:])
}

func testHashPtr(content string) *string {
	value := testHashText(content)
	return &value
}

func stringPtr(value string) *string {
	return &value
}

func exists(path string) bool {
	_, err := os.Stat(path)
	return err == nil
}

func conflictFiles(t *testing.T, root string) []string {
	t.Helper()
	var paths []string
	err := filepath.WalkDir(root, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.IsDir() || !strings.Contains(entry.Name(), ".conflict.") {
			return nil
		}
		paths = append(paths, path)
		return nil
	})
	if err != nil {
		t.Fatalf("WalkDir() error = %v", err)
	}
	return paths
}

func containsAll(body string, parts ...string) bool {
	for _, part := range parts {
		if !strings.Contains(body, part) {
			return false
		}
	}
	return true
}

func (f *fakeClient) requirePut(t *testing.T, want putCall) {
	t.Helper()
	for _, put := range f.puts {
		if put == want {
			return
		}
	}
	t.Fatalf("missing put %#v in %#v", want, f.puts)
}

func (f *fakeClient) requireDelete(t *testing.T, want deleteCall) {
	t.Helper()
	for _, deleteCall := range f.deletes {
		if deleteCall == want {
			return
		}
	}
	t.Fatalf("missing delete %#v in %#v", want, f.deletes)
}

func requireNoError(t *testing.T, err error) {
	t.Helper()
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
}
