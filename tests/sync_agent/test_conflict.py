from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase

from obsidian_sync.domain.sync_rules import is_conflict_file
from obsidian_sync.sync_agent.conflict import conflict_filename, write_conflict_file


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
