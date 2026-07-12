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


class ShouldSyncAttachmentsTests(TestCase):
    """`sync_attachments` gates attachment extensions; markdown is always on."""

    def test_image_is_not_synced_by_default(self) -> None:
        self.assertFalse(should_sync('notes/image.png'))

    def test_image_is_synced_when_attachments_enabled(self) -> None:
        self.assertTrue(should_sync('notes/image.png', sync_attachments=True))

    def test_pdf_is_synced_when_attachments_enabled(self) -> None:
        self.assertTrue(should_sync('notes/doc.pdf', sync_attachments=True))

    def test_markdown_is_synced_regardless_of_attachments_flag(self) -> None:
        self.assertTrue(should_sync('notes/JPA.md', sync_attachments=True))
        self.assertTrue(should_sync('notes/JPA.md', sync_attachments=False))

    def test_disallowed_extension_is_not_synced_even_with_attachments_enabled(
        self,
    ) -> None:
        self.assertFalse(should_sync('notes/archive.zip', sync_attachments=True))

    def test_hidden_attachment_is_not_synced(self) -> None:
        self.assertFalse(should_sync('notes/.secret.png', sync_attachments=True))

    def test_attachment_conflict_file_is_not_synced(self) -> None:
        self.assertFalse(
            should_sync(
                'notes/image.conflict.dev.20260707-000000.png',
                sync_attachments=True,
            )
        )

    def test_ignored_directory_attachment_is_not_synced(self) -> None:
        self.assertFalse(should_sync('.trash/old.png', sync_attachments=True))


class IsIgnoredDirTests(TestCase):
    def test_dot_directory_is_ignored(self) -> None:
        self.assertTrue(is_ignored_dir('.obsidian'))

    def test_hidden_directory_is_ignored(self) -> None:
        self.assertTrue(is_ignored_dir('.git'))

    def test_regular_directory_is_not_ignored(self) -> None:
        self.assertFalse(is_ignored_dir('notes'))
