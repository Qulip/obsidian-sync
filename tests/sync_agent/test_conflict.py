from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from obsidian_sync.domain.hashing import sha256_bytes
from obsidian_sync.domain.sync_rules import is_conflict_file
from obsidian_sync.sync_agent.conflict import (
    attachment_conflict_filename,
    attachment_local_backup_filename,
    conflict_filename,
    local_backup_filename,
    write_attachment_conflict_file,
    write_attachment_local_backup_file,
    write_conflict_file,
    write_local_backup_file,
)


class ConflictFilenameTests(TestCase):
    def test_filename_pattern_matches_conflict_rule(self) -> None:
        moment = datetime(2026, 7, 7, 12, 30, 45)
        name = conflict_filename('notes/JPA.md', device_id='laptop', moment=moment)

        self.assertEqual(name, 'JPA.conflict.laptop.20260707-123045.md')
        self.assertTrue(is_conflict_file(name))


class WriteConflictFileTests(TestCase):
    def test_written_file_has_expected_sections_and_is_conflict_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            destination = write_conflict_file(
                root,
                'notes/JPA.md',
                device_id='laptop',
                client_base_revision=2,
                server_revision=3,
                local_content='LOCAL BODY',
                server_content='SERVER BODY',
            )
            body = destination.read_text(encoding='utf-8')
            rel = destination.relative_to(root.resolve()).as_posix()

        self.assertTrue(rel.startswith('notes/'))
        self.assertTrue(is_conflict_file(rel))
        self.assertIn('# Sync Conflict', body)
        self.assertIn('## Local Version', body)
        self.assertIn('## Server Version', body)
        self.assertIn('LOCAL BODY', body)
        self.assertIn('SERVER BODY', body)
        self.assertLess(body.index('## Local Version'), body.index('## Server Version'))


class ConflictDedupTests(TestCase):
    def _conflicts(self, root: Path) -> list[Path]:
        return [
            path
            for path in root.rglob('*.md')
            if is_conflict_file(path.relative_to(root).as_posix())
        ]

    def test_same_server_revision_reuses_existing_file(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first = write_conflict_file(
                root,
                'notes/JPA.md',
                device_id='laptop',
                client_base_revision=2,
                server_revision=3,
                local_content='LOCAL A',
                server_content='SERVER A',
            )
            second = write_conflict_file(
                root,
                'notes/JPA.md',
                device_id='laptop',
                client_base_revision=2,
                server_revision=3,
                local_content='LOCAL B',
                server_content='SERVER B',
            )

            self.assertEqual(first, second)
            self.assertEqual(len(self._conflicts(root)), 1)
            # The original file is preserved; no second copy is written.
            self.assertIn('LOCAL A', first.read_text(encoding='utf-8'))


class LocalBackupFilenameTests(TestCase):
    def test_filename_pattern_is_excluded_from_sync(self) -> None:
        moment = datetime(2026, 7, 11, 12, 30, 45)
        name = local_backup_filename('notes/JPA.md', device_id='laptop', moment=moment)

        self.assertEqual(name, 'JPA.local-backup.conflict.laptop.20260711-123045.md')
        # remote-wins backups must be excluded from sync/push/vectorizing
        # the same way manual conflict files are.
        self.assertTrue(is_conflict_file(name))


class WriteLocalBackupFileTests(TestCase):
    def test_written_file_preserves_discarded_local_content(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            destination = write_local_backup_file(
                root,
                'notes/JPA.md',
                device_id='laptop',
                server_revision=7,
                local_content='DISCARDED LOCAL BODY',
            )
            body = destination.read_text(encoding='utf-8')
            rel = destination.relative_to(root.resolve()).as_posix()

        self.assertTrue(rel.startswith('notes/'))
        self.assertTrue(is_conflict_file(rel))
        self.assertIn('remote-wins', body)
        self.assertIn('Server revision applied: 7', body)
        self.assertIn('DISCARDED LOCAL BODY', body)

    def test_does_not_collide_with_manual_conflict_dedup(self) -> None:
        with TemporaryDirectory() as directory:
            root = Path(directory)
            conflict = write_conflict_file(
                root,
                'notes/JPA.md',
                device_id='laptop',
                client_base_revision=2,
                server_revision=3,
                local_content='LOCAL',
                server_content='SERVER',
            )
            backup = write_local_backup_file(
                root,
                'notes/JPA.md',
                device_id='laptop',
                server_revision=3,
                local_content='LOCAL',
            )

            # A manual conflict written afterwards for the same (path,
            # device, revision) must still reuse the original conflict
            # file, not the backup written in between.
            second_conflict = write_conflict_file(
                root,
                'notes/JPA.md',
                device_id='laptop',
                client_base_revision=2,
                server_revision=3,
                local_content='LOCAL B',
                server_content='SERVER B',
            )

        self.assertNotEqual(conflict, backup)
        self.assertEqual(conflict, second_conflict)


class AttachmentConflictFilenameTests(TestCase):
    def test_filename_preserves_extension(self) -> None:
        moment = datetime(2026, 7, 7, 12, 30, 45)
        name = attachment_conflict_filename(
            'img/photo.png', device_id='laptop', moment=moment
        )

        self.assertEqual(name, 'photo.conflict.laptop.20260707-123045.png')
        self.assertTrue(is_conflict_file(name))


class WriteAttachmentConflictFileTests(TestCase):
    def test_writes_raw_bytes_and_leaves_extension_intact(self) -> None:
        raw = b'\x89PNG\r\n\x1a\nserver-bytes'
        with TemporaryDirectory() as directory:
            root = Path(directory)
            destination = write_attachment_conflict_file(
                root,
                'img/photo.png',
                device_id='laptop',
                content=raw,
                content_hash=sha256_bytes(raw),
            )
            body = destination.read_bytes()
            rel = destination.relative_to(root.resolve()).as_posix()

        self.assertTrue(rel.startswith('img/'))
        self.assertTrue(rel.endswith('.png'))
        self.assertTrue(is_conflict_file(rel))
        self.assertEqual(body, raw)

    def test_same_content_hash_reuses_existing_file(self) -> None:
        raw = b'server-bytes-a'
        with TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            first = write_attachment_conflict_file(
                root,
                'img/photo.png',
                device_id='laptop',
                content=raw,
                content_hash=sha256_bytes(raw),
            )
            second = write_attachment_conflict_file(
                root,
                'img/photo.png',
                device_id='laptop',
                content=raw,
                content_hash=sha256_bytes(raw),
            )

        self.assertEqual(first, second)


class AttachmentLocalBackupFilenameTests(TestCase):
    def test_filename_preserves_extension_and_is_excluded_from_sync(self) -> None:
        moment = datetime(2026, 7, 11, 12, 30, 45)
        name = attachment_local_backup_filename(
            'img/photo.png', device_id='laptop', moment=moment
        )

        self.assertEqual(name, 'photo.local-backup.conflict.laptop.20260711-123045.png')
        self.assertTrue(is_conflict_file(name))


class WriteAttachmentLocalBackupFileTests(TestCase):
    def test_written_file_preserves_discarded_local_bytes(self) -> None:
        raw = b'discarded-local-bytes'
        with TemporaryDirectory() as directory:
            root = Path(directory)
            destination = write_attachment_local_backup_file(
                root,
                'img/photo.png',
                device_id='laptop',
                local_content=raw,
            )
            body = destination.read_bytes()
            rel = destination.relative_to(root.resolve()).as_posix()

        self.assertTrue(is_conflict_file(rel))
        self.assertEqual(body, raw)
