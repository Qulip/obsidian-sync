package engine

import (
	"context"
	"testing"

	"github.com/Qulip/obsidian-sync/internal/syncagent/client"
	"github.com/Qulip/obsidian-sync/internal/syncagent/manifest"
)

func TestRunStatusReturnsServerAndManifestState(t *testing.T) {
	// Given
	root := t.TempDir()
	saveManifest(t, root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 4,
		Files: map[string]manifest.Entry{
			"a.md": {ServerRevision: 4, ContentHash: testHashText("a"), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	seen := 3
	fake := newFakeClient()
	fake.status = client.SyncStatusData{
		VaultID:                "vault",
		DeviceID:               stringPtr("dev"),
		ServerRevision:         7,
		DeviceLastSeenRevision: &seen,
		PendingChanges:         2,
		OpenConflicts:          1,
		PendingVectorizingJobs: 5,
	}

	// When
	report, err := RunStatus(context.Background(), testConfig(root), StatusOptions{Client: fake})

	// Then
	requireNoError(t, err)
	if report.Server.VaultID != "vault" || report.LocalManifestCursor != 4 || report.TrackedLocalFiles != 1 {
		t.Fatalf("report = %#v", report)
	}
	if fake.statusDeviceID != "dev" {
		t.Fatalf("status device id = %q", fake.statusDeviceID)
	}
}
