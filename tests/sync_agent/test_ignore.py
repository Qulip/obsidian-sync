from unittest import TestCase

from obsidian_sync.sync_agent.ignore import is_ignored_dir, should_sync


class ShouldSyncTests(TestCase):
    def test_plain_markdown_is_synced(self) -> None:
        self.assertTrue(should_sync('notes/JPA.md'))

    def test_non_markdown_is_not_synced(self) -> None:
        self.assertFalse(should_sync('notes/image.png'))

    def test_ds_store_is_not_synced(self) -> None:
        self.assertFalse(should_sync('notes/.DS_Store'))

    def test_thumbs_db_is_not_synced(self) -> None:
        self.assertFalse(should_sync('Thumbs.db'))

    def test_hidden_file_is_not_synced(self) -> None:
        self.assertFalse(should_sync('notes/.secret.md'))

    def test_hidden_directory_segment_is_not_synced(self) -> None:
        self.assertFalse(should_sync('.obsidian/plugins/note.md'))

    def test_conflict_file_is_not_synced(self) -> None:
        self.assertFalse(should_sync('notes/JPA.conflict.dev.20260707-000000.md'))

    def test_ignored_directory_is_not_synced(self) -> None:
        self.assertFalse(should_sync('.trash/old.md'))


class IsIgnoredDirTests(TestCase):
    def test_dot_directory_is_ignored(self) -> None:
        self.assertTrue(is_ignored_dir('.obsidian'))

    def test_hidden_directory_is_ignored(self) -> None:
        self.assertTrue(is_ignored_dir('.git'))

    def test_regular_directory_is_not_ignored(self) -> None:
        self.assertFalse(is_ignored_dir('notes'))
