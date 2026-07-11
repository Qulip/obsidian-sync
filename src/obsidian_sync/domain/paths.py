from pathlib import Path, PurePosixPath

from obsidian_sync.domain.errors import DomainValidationError

_MAX_SEGMENT_BYTES = 255
_WINDOWS_RESERVED_NAMES = frozenset(
    {'CON', 'PRN', 'AUX', 'NUL'}
    | {f'COM{digit}' for digit in range(1, 10)}
    | {f'LPT{digit}' for digit in range(1, 10)}
)


def normalize_source_path(raw_path: str) -> str:
    path = raw_path.strip()
    if not path:
        raise DomainValidationError('source path is required')
    if '\x00' in path:
        raise DomainValidationError('source path cannot contain NUL bytes')
    if '\\' in path:
        raise DomainValidationError('source path must use forward slashes')
    if path.startswith('/'):
        raise DomainValidationError('source path must be relative')

    # Validated against the raw (pre-strip) segments so a trailing space on
    # the final segment is not silently absorbed by the outer strip() above
    # before the degenerate-segment checks below get a chance to see it.
    segments = raw_path.split('/')
    if any(part in {'.', '..'} or not part.strip() for part in segments):
        raise DomainValidationError(
            'source path cannot contain empty or traversal parts'
        )
    if any(segment != segment.rstrip('. ') for segment in segments):
        raise DomainValidationError(
            'source path segments cannot end with a dot or space'
        )
    if any(len(segment.encode('utf-8')) > _MAX_SEGMENT_BYTES for segment in segments):
        raise DomainValidationError('source path segments cannot exceed 255 bytes')
    if any(
        segment.split('.', 1)[0].upper() in _WINDOWS_RESERVED_NAMES
        for segment in segments
    ):
        raise DomainValidationError(
            'source path cannot contain a reserved Windows filename'
        )
    if ':' in PurePosixPath(path).parts[0]:
        raise DomainValidationError('source path cannot include a drive prefix')

    normalized = PurePosixPath(path)
    normalized_text = normalized.as_posix()
    if normalized_text in {'.', ''}:
        raise DomainValidationError('source path is required')
    return normalized_text


def safe_vault_destination(vault_root: Path, source_path: str) -> Path:
    normalized = normalize_source_path(source_path)
    root = vault_root.resolve()
    destination = (root / normalized).resolve(strict=False)
    if destination != root and root not in destination.parents:
        raise DomainValidationError('source path escapes vault root')
    return destination
