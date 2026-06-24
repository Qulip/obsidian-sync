from dataclasses import dataclass
from enum import StrEnum
from pathlib import PurePosixPath

from obsidian_sync.domain.errors import DomainValidationError
from obsidian_sync.domain.paths import normalize_source_path


class FileKind(StrEnum):
    MARKDOWN = 'markdown'
    IMAGE = 'image'
    PDF = 'pdf'


ALLOWED_EXTENSIONS = frozenset(
    {
        '.md',
        '.png',
        '.jpg',
        '.jpeg',
        '.gif',
        '.webp',
        '.pdf',
    }
)
EXCLUDED_EXTENSIONS = frozenset(
    {
        '.env',
        '.key',
        '.pem',
        '.p12',
        '.zip',
        '.7z',
        '.mp4',
        '.mov',
        '.exe',
        '.dll',
        '.db',
        '.sqlite',
    }
)
MARKDOWN_MAX_BYTES = 2 * 1024 * 1024
IMAGE_MAX_BYTES = 10 * 1024 * 1024
PDF_MAX_BYTES = 30 * 1024 * 1024

_IMAGE_EXTENSIONS = frozenset({'.png', '.jpg', '.jpeg', '.gif', '.webp'})
_MAX_BYTES_BY_KIND = {
    FileKind.MARKDOWN: MARKDOWN_MAX_BYTES,
    FileKind.IMAGE: IMAGE_MAX_BYTES,
    FileKind.PDF: PDF_MAX_BYTES,
}


@dataclass(frozen=True, slots=True)
class FilePolicy:
    source_path: str
    extension: str
    kind: FileKind
    max_bytes: int
    vectorize: bool


def classify_file(source_path: str) -> FilePolicy:
    normalized = normalize_source_path(source_path)
    extension = PurePosixPath(normalized).suffix.lower()
    if extension in EXCLUDED_EXTENSIONS:
        raise DomainValidationError(
            'file extension is explicitly excluded',
            {'extension': extension},
        )
    if extension not in ALLOWED_EXTENSIONS:
        raise DomainValidationError(
            'file extension is not allowed',
            {'extension': extension},
        )

    if extension == '.md':
        kind = FileKind.MARKDOWN
    elif extension in _IMAGE_EXTENSIONS:
        kind = FileKind.IMAGE
    else:
        kind = FileKind.PDF

    return FilePolicy(
        source_path=normalized,
        extension=extension,
        kind=kind,
        max_bytes=_MAX_BYTES_BY_KIND[kind],
        vectorize=kind is FileKind.MARKDOWN,
    )


def validate_file_size(source_path: str, size_bytes: int) -> FilePolicy:
    if size_bytes < 0:
        raise DomainValidationError('file size cannot be negative')

    policy = classify_file(source_path)
    if size_bytes > policy.max_bytes:
        raise DomainValidationError(
            'file exceeds size limit',
            {
                'source_path': policy.source_path,
                'size_bytes': size_bytes,
                'max_bytes': policy.max_bytes,
            },
        )
    return policy
