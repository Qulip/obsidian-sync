package engine

import (
	"context"
	"path/filepath"
	"testing"

	"github.com/Qulip/obsidian-sync/internal/syncagent/client"
	"github.com/Qulip/obsidian-sync/internal/syncagent/manifest"
)

func TestRunSyncDryRunDoesNotWriteOrPush(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	vault.writeNote("local.md", "local")
	fake := newFakeClient()
	fake.changes = []client.SyncChangeItem{
		syncChange(changeSpec{revision: 3, path: "remote.md", eventType: "CREATE", contentHash: testHashPtr("remote")}),
	}

	// When
	summary, err := RunSync(context.Background(), testConfig(vault.root), Options{
		DryRun: true,
		Client: fake,
	})

	// Then
	requireNoError(t, err)
	if summary.Pulled != 1 || summary.Pushed != 1 {
		t.Fatalf("summary = %#v", summary)
	}
	if fake.registerCalls != 0 || len(fake.puts) != 0 || len(fake.deletes) != 0 {
		t.Fatalf("dry-run performed server writes: register=%d puts=%d deletes=%d", fake.registerCalls, len(fake.puts), len(fake.deletes))
	}
	if exists(filepath.Join(vault.root, "remote.md")) {
		t.Fatal("dry-run wrote pulled file")
	}
	if exists(manifest.Path(vault.root)) {
		t.Fatal("dry-run saved manifest")
	}
}
