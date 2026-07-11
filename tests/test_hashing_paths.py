from unittest import TestCase
from urllib.parse import unquote

from obsidian_sync.domain.errors import DomainValidationError
from obsidian_sync.domain.hashing import (
    normalize_sha256,
    sha256_bytes,
    sha256_text,
    verify_sha256,
)
from obsidian_sync.domain.paths import normalize_source_path


class HashingTests(TestCase):
    def test_sha256_round_trip_matches_verify(self) -> None:
        content = b'hello world'
        digest = sha256_bytes(content)

        self.assertEqual(len(digest), 64)
        self.assertEqual(verify_sha256(content, digest), digest)
        self.assertEqual(sha256_text('hello world'), digest)

    def test_normalize_sha256_lowercases_and_strips(self) -> None:
        digest = sha256_text('sample')
        self.assertEqual(normalize_sha256(f'  {digest.upper()}  '), digest)

    def test_normalize_sha256_rejects_prefixed_hash(self) -> None:
        digest = sha256_text('sample')
        with self.assertRaises(DomainValidationError):
            normalize_sha256(f'sha256:{digest}')

    def test_normalize_sha256_rejects_short_hash(self) -> None:
        with self.assertRaises(DomainValidationError):
            normalize_sha256('abc123')

    def test_verify_sha256_rejects_mismatch(self) -> None:
        with self.assertRaises(DomainValidationError):
            verify_sha256(b'actual', sha256_text('expected'))


class NormalizeSourcePathTests(TestCase):
    def test_valid_relative_path_is_returned(self) -> None:
        self.assertEqual(normalize_source_path('notes/JPA.md'), 'notes/JPA.md')

    def test_parent_traversal_is_rejected(self) -> None:
        with self.assertRaises(DomainValidationError):
            normalize_source_path('../x.md')

    def test_absolute_path_is_rejected(self) -> None:
        with self.assertRaises(DomainValidationError):
            normalize_source_path('/abs/x.md')

    def test_backslash_path_is_rejected(self) -> None:
        with self.assertRaises(DomainValidationError):
            normalize_source_path('a\\b.md')

    def test_empty_segment_is_rejected(self) -> None:
        with self.assertRaises(DomainValidationError):
            normalize_source_path('a//b.md')

    def test_empty_path_is_rejected(self) -> None:
        with self.assertRaises(DomainValidationError):
            normalize_source_path('   ')

    def test_url_decoded_traversal_is_rejected(self) -> None:
        decoded = unquote('..%2Fsecret.md')
        with self.assertRaises(DomainValidationError):
            normalize_source_path(decoded)

    def test_windows_reserved_bare_name_is_rejected(self) -> None:
        with self.assertRaises(DomainValidationError):
            normalize_source_path('CON')

    def test_windows_reserved_name_with_extension_is_rejected(self) -> None:
        with self.assertRaises(DomainValidationError):
            normalize_source_path('con.md')

    def test_windows_reserved_name_is_case_insensitive(self) -> None:
        with self.assertRaises(DomainValidationError):
            normalize_source_path('notes/NuL.txt')

    def test_windows_reserved_com_and_lpt_ports_are_rejected(self) -> None:
        for name in ('COM1', 'com9', 'LPT1', 'lpt9'):
            with self.assertRaises(DomainValidationError):
                normalize_source_path(f'notes/{name}.md')

    def test_windows_reserved_name_in_nested_segment_is_rejected(self) -> None:
        with self.assertRaises(DomainValidationError):
            normalize_source_path('notes/PRN/file.md')

    def test_com10_is_not_a_reserved_name(self) -> None:
        self.assertEqual(normalize_source_path('notes/COM10.md'), 'notes/COM10.md')

    def test_console_is_not_a_reserved_name(self) -> None:
        self.assertEqual(normalize_source_path('notes/console.md'), 'notes/console.md')

    def test_segment_of_255_bytes_is_accepted(self) -> None:
        segment = 'a' * 255
        self.assertEqual(normalize_source_path(segment), segment)

    def test_segment_of_254_bytes_is_accepted(self) -> None:
        segment = 'a' * 254
        self.assertEqual(normalize_source_path(segment), segment)

    def test_segment_of_256_bytes_is_rejected(self) -> None:
        with self.assertRaises(DomainValidationError):
            normalize_source_path('a' * 256)

    def test_segment_length_is_measured_in_utf8_bytes_not_characters(self) -> None:
        # Each '가' encodes to 3 UTF-8 bytes, so 90 characters is 270 bytes
        # (over the 255-byte limit) even though the character count (90) is
        # well under 255. Filesystem limits (ext4, APFS, most SMB shares) are
        # byte-based, so the check must be too, or it would under-protect
        # multi-byte filenames.
        over_limit_by_bytes = '가' * 90
        with self.assertRaises(DomainValidationError):
            normalize_source_path(over_limit_by_bytes)

        within_limit_by_bytes = '가' * 80
        self.assertEqual(
            normalize_source_path(within_limit_by_bytes), within_limit_by_bytes
        )

    def test_trailing_dot_is_rejected(self) -> None:
        with self.assertRaises(DomainValidationError):
            normalize_source_path('notes/note.')

    def test_trailing_space_is_rejected(self) -> None:
        with self.assertRaises(DomainValidationError):
            normalize_source_path('notes/note ')

    def test_trailing_dot_and_space_is_rejected(self) -> None:
        with self.assertRaises(DomainValidationError):
            normalize_source_path('notes/note. ')

    def test_whitespace_only_segment_is_rejected(self) -> None:
        with self.assertRaises(DomainValidationError):
            normalize_source_path('notes/   /file.md')

    def test_korean_filename_is_accepted(self) -> None:
        self.assertEqual(
            normalize_source_path('notes/한글 파일명.md'), 'notes/한글 파일명.md'
        )

    def test_filename_with_spaces_is_accepted(self) -> None:
        self.assertEqual(
            normalize_source_path('notes/hello world.md'), 'notes/hello world.md'
        )

    def test_emoji_filename_is_accepted(self) -> None:
        self.assertEqual(normalize_source_path('notes/😀emoji.md'), 'notes/😀emoji.md')
