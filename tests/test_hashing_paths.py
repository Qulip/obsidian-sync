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
