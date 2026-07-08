from unittest import TestCase

from obsidian_sync.domain.sync_rules import (
    is_conflict_file,
    is_ignored_path,
    is_vectorizable_path,
)


class IsConflictFileTests(TestCase):
    def test_dotted_conflict_pattern_matches(self) -> None:
        self.assertTrue(
            is_conflict_file('notes/JPA.conflict.laptop.20260707-000000.md')
        )

    def test_sync_conflict_pattern_matches(self) -> None:
        self.assertTrue(is_conflict_file('notes/JPA.sync-conflict-20260707.md'))

    def test_plain_markdown_is_not_a_conflict_file(self) -> None:
        self.assertFalse(is_conflict_file('notes/JPA.md'))

    def test_conflict_substring_in_directory_does_not_match(self) -> None:
        self.assertFalse(is_conflict_file('conflict-notes/JPA.md'))


class IsIgnoredPathTests(TestCase):
    def test_obsidian_directory_at_root_is_ignored(self) -> None:
        self.assertTrue(is_ignored_path('.obsidian/workspace.json'))

    def test_sync_agent_directory_is_ignored(self) -> None:
        self.assertTrue(is_ignored_path('.obsidian-sync-agent/config.json'))

    def test_trash_directory_nested_is_ignored(self) -> None:
        self.assertTrue(is_ignored_path('projects/.trash/old.md'))

    def test_obsidian_directory_deeply_nested_is_ignored(self) -> None:
        self.assertTrue(is_ignored_path('a/b/.obsidian/plugins/x.js'))

    def test_regular_path_is_not_ignored(self) -> None:
        self.assertFalse(is_ignored_path('notes/deep/JPA.md'))


class IsVectorizablePathTests(TestCase):
    def test_plain_markdown_is_vectorizable(self) -> None:
        self.assertTrue(is_vectorizable_path('notes/JPA.md'))

    def test_conflict_file_is_not_vectorizable(self) -> None:
        self.assertFalse(
            is_vectorizable_path('notes/JPA.conflict.dev.20260707-000000.md')
        )

    def test_ignored_path_is_not_vectorizable(self) -> None:
        self.assertFalse(is_vectorizable_path('.obsidian/config.md'))

    def test_non_markdown_is_not_vectorizable(self) -> None:
        self.assertFalse(is_vectorizable_path('notes/image.png'))
