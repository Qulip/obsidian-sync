from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from obsidian_sync.sync_agent.config import CONFIG_DIRNAME
from obsidian_sync.sync_agent.manifest import (
    Manifest,
    ManifestEntry,
    load_manifest,
    manifest_path,
    save_manifest,
)


class ManifestTests(TestCase):
    def test_load_missing_manifest_returns_empty(self) -> None:
        with TemporaryDirectory() as directory:
            manifest = load_manifest(Path(directory))

        self.assertEqual(manifest, Manifest())
        self.assertEqual(manifest.files, {})

    def test_save_and_load_round_trip(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            original = Manifest(
                vault_id='main',
                device_id='laptop',
                last_sync_cursor=7,
                files={
                    'notes/JPA.md': ManifestEntry(
                        server_revision=3,
                        content_hash='a' * 64,
                        last_synced_at='2026-07-07T00:00:00+00:00',
                    )
                },
            )
            save_manifest(root, original)
            loaded = load_manifest(root)

        self.assertEqual(loaded, original)

    def test_atomic_save_leaves_no_temp_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            save_manifest(root, Manifest(vault_id='main', device_id='laptop'))

            config_dir = root / CONFIG_DIRNAME
            leftovers = [
                child.name
                for child in config_dir.iterdir()
                if child.name.startswith('.tmp-')
            ]

            self.assertEqual(leftovers, [])
            self.assertTrue(manifest_path(root).exists())
