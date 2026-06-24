from pathlib import Path, PurePosixPath

from obsidian_sync.domain.errors import DomainValidationError


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
    if any(part in {'', '.', '..'} for part in path.split('/')):
        raise DomainValidationError(
            'source path cannot contain empty or traversal parts'
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
