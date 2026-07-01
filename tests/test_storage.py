from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from obsidian_sync.services.storage import VaultStorage


class VaultStorageTests(TestCase):
    def test_staged_replace_rollback_restores_existing_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            storage = VaultStorage(root / 'vaults', root / 'archives')
            path = storage.vault_path('main', 'notes/example.md')
            path.parent.mkdir(parents=True)
            path.write_text('old content', encoding='utf-8')

            staged = storage.stage_replace(
                'main',
                'notes/example.md',
                b'new content',
            )
            staged.promote()
            storage.rollback_replace(staged)

            self.assertEqual(path.read_text(encoding='utf-8'), 'old content')
            self.assertFalse(staged.temp_path.exists())
            self.assertFalse(staged.holding_path.exists())

    def test_staged_replace_cleanup_keeps_promoted_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            storage = VaultStorage(root / 'vaults', root / 'archives')

            staged = storage.stage_replace(
                'main',
                'notes/example.md',
                b'new content',
            )
            staged.promote()
            storage.finish_replace(staged)

            path = storage.vault_path('main', 'notes/example.md')
            self.assertEqual(path.read_text(encoding='utf-8'), 'new content')
            self.assertFalse(staged.temp_path.exists())
            self.assertFalse(staged.holding_path.exists())

    def test_archive_stage_failure_keeps_existing_archive(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            storage = VaultStorage(root / 'vaults', root / 'archives')
            source = storage.vault_path('main', 'notes/example.md')
            source.parent.mkdir(parents=True)
            source.write_text('current content', encoding='utf-8')

            archive = root / 'archives' / 'main' / 'notes' / 'example.md'
            archive.parent.mkdir(parents=True)
            archive.write_text('archived content', encoding='utf-8')
            moves = storage.prepare_archive_moves('main', ['notes/example.md'])

            with patch(
                'obsidian_sync.services.storage.shutil.copy2',
                side_effect=OSError('copy failed'),
            ):
                with self.assertRaises(OSError):
                    storage.stage_archive_moves(moves)

            self.assertEqual(source.read_text(encoding='utf-8'), 'current content')
            self.assertEqual(archive.read_text(encoding='utf-8'), 'archived content')
