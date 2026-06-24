import hashlib
from pathlib import Path

from obsidian_sync.domain.errors import DomainValidationError

SHA256_HEX_LENGTH = 64


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def sha256_text(content: str) -> str:
    return sha256_bytes(content.encode('utf-8'))


def sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for chunk in iter(lambda: file.read(chunk_size), b''):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_sha256(raw_hash: str) -> str:
    content_hash = raw_hash.strip().lower()
    if len(content_hash) != SHA256_HEX_LENGTH:
        raise DomainValidationError('sha256 hash must be 64 hex characters')
    if any(character not in '0123456789abcdef' for character in content_hash):
        raise DomainValidationError('sha256 hash must be hexadecimal')
    return content_hash


def verify_sha256(content: bytes, expected_hash: str) -> str:
    normalized = normalize_sha256(expected_hash)
    actual = sha256_bytes(content)
    if actual != normalized:
        raise DomainValidationError(
            'content hash does not match expected sha256',
            {'expected_hash': normalized, 'actual_hash': actual},
        )
    return actual
