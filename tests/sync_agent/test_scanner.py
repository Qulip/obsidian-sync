from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from obsidian_sync.domain.hashing import sha256_text
from obsidian_sync.sync_agent.manifest import Manifest, ManifestEntry
from obsidian_sync.sync_agent.scanner import classify_local_changes, scan_vault


def _write(root: Path, rel: str, content: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


class ScanVaultTests(TestCase):
    def test_scan_excludes_ignored_and_non_markdown(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root, 'notes/JPA.md', 'kept')
            _write(root, 'notes/image.png', 'binary')
            _write(root, '.obsidian/workspace.json', '{}')
            _write(root, '.trash/old.md', 'trashed')
            _write(root, 'notes/.DS_Store', 'junk')

            scanned = scan_vault(root)

        self.assertEqual(set(scanned), {'notes/JPA.md'})
        self.assertEqual(scanned['notes/JPA.md'].content_hash, sha256_text('kept'))


class ClassifyLocalChangesTests(TestCase):
    def test_classifies_new_modified_deleted_and_unchanged(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            _write(root, 'new.md', 'new file')
            _write(root, 'modified.md', 'modified now')
            _write(root, 'unchanged.md', 'stable')

            scanned = scan_vault(root)

        manifest = Manifest(
            files={
                'modified.md': ManifestEntry(
                    server_revision=1,
                    content_hash=sha256_text('old content'),
                    last_synced_at='',
                ),
                'unchanged.md': ManifestEntry(
                    server_revision=1,
                    content_hash=sha256_text('stable'),
                    last_synced_at='',
                ),
                'deleted.md': ManifestEntry(
                    server_revision=1,
                    content_hash=sha256_text('gone'),
                    last_synced_at='',
                ),
            }
        )

        changes = classify_local_changes(scanned, manifest)

        self.assertEqual(changes.new, ['new.md'])
        self.assertEqual(changes.modified, ['modified.md'])
        self.assertEqual(changes.deleted, ['deleted.md'])
