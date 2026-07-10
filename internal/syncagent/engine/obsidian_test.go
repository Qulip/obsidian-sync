package engine

import (
	"context"
	"testing"

	"github.com/Qulip/obsidian-sync/internal/syncagent/manifest"
	"github.com/Qulip/obsidian-sync/internal/syncagent/obsidian"
)

func TestRunSyncReportsObsidianRefreshWarning(t *testing.T) {
	// Given
	root := t.TempDir()
	saveManifest(t, root, manifest.Manifest{
		VaultID:   "vault",
		DeviceID:  "dev",
		Files:     map[string]manifest.Entry{},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()

	// When
	summary, err := RunSync(context.Background(), testConfig(root), Options{
		Client: fake,
		Now:    fixedNow,
		RefreshObsidian: func(context.Context, obsidian.Config) obsidian.Result {
			return obsidian.Result{OK: false, Message: "obsidian reload failed"}
		},
	})

	// Then
	requireNoError(t, err)
	if summary.ObsidianOK {
		t.Fatalf("ObsidianOK = true, want false")
	}
	if summary.ObsidianMessage != "obsidian reload failed" {
		t.Fatalf("ObsidianMessage = %q", summary.ObsidianMessage)
	}
	if len(summary.Warnings) != 1 || summary.Warnings[0] != "obsidian reload failed" {
		t.Fatalf("Warnings = %#v", summary.Warnings)
	}
}
