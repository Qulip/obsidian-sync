package engine

import (
	"context"
	"testing"

	"github.com/Qulip/obsidian-sync/internal/syncagent/manifest"
)

func TestRunSync_keepsTrackedAttachment_whenSyncAttachmentsDisabled(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	raw := []byte{0x01, 0x02}
	vault.writeNote("Images/diagram.png", string(raw))
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 1,
		Files: map[string]manifest.Entry{
			"Images/diagram.png": {ServerRevision: 1, ContentHash: testHashBytes(raw), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()

	// When
	summary, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if summary.RemotelyDeleted != 0 || len(fake.deletes) != 0 {
		t.Fatalf("attachment was deleted: summary=%#v deletes=%#v", summary, fake.deletes)
	}
	if _, ok := loadManifest(t, vault.root).Files["Images/diagram.png"]; !ok {
		t.Fatal("tracked attachment was removed from the manifest")
	}
}

func TestRunSync_keepsRemovedTrackedAttachment_whenSyncAttachmentsDisabled(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 1,
		Files: map[string]manifest.Entry{
			"Images/diagram.png": {ServerRevision: 1, ContentHash: testHashBytes([]byte{0x01, 0x02}), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()

	// When
	summary, err := RunSync(context.Background(), testConfig(vault.root), Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if summary.RemotelyDeleted != 0 || len(fake.deletes) != 0 {
		t.Fatalf("removed attachment was deleted: summary=%#v deletes=%#v", summary, fake.deletes)
	}
	if _, ok := loadManifest(t, vault.root).Files["Images/diagram.png"]; !ok {
		t.Fatal("removed attachment was deleted from the manifest")
	}
}

func TestRunSync_keepsTrackedAttachment_whenItExceedsSizeLimit(t *testing.T) {
	// Given
	vault := newVaultFixture(t)
	raw := []byte{0x01, 0x02}
	vault.writeNote("Images/diagram.png", string(raw))
	saveManifest(t, vault.root, manifest.Manifest{
		VaultID:        "vault",
		DeviceID:       "dev",
		LastSyncCursor: 1,
		Files: map[string]manifest.Entry{
			"Images/diagram.png": {ServerRevision: 1, ContentHash: testHashBytes(raw), LastSyncedAt: fixedNowString},
		},
		Conflicts: map[string]manifest.Conflict{},
	})
	fake := newFakeClient()
	cfg := testConfigWithAttachments(vault.root)
	cfg.AttachmentMaxBytes = int64(len(raw) - 1)

	// When
	summary, err := RunSync(context.Background(), cfg, Options{
		Client: fake,
		Now:    fixedNow,
	})

	// Then
	requireNoError(t, err)
	if summary.RemotelyDeleted != 0 || len(fake.deletes) != 0 {
		t.Fatalf("oversized attachment was deleted: summary=%#v deletes=%#v", summary, fake.deletes)
	}
	if _, ok := loadManifest(t, vault.root).Files["Images/diagram.png"]; !ok {
		t.Fatal("tracked oversized attachment was removed from the manifest")
	}
}
