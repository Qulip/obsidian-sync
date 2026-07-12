from fnmatch import fnmatchcase
from pathlib import PurePosixPath

from obsidian_sync.domain.files import ALLOWED_EXTENSIONS

# Conflict/backup filenames keep the original extension (e.g.
# ``img.conflict.dev.20260707-000000.png``) so the pattern is generated per
# allowed extension rather than hardcoded to ``.md``. This lets attachment
# conflict/backup files be recognized and excluded from scanning, pushing,
# and vectorizing the same way markdown ones always have been.
CONFLICT_FILE_PATTERNS = tuple(
    pattern
    for extension in sorted(ALLOWED_EXTENSIONS)
    for pattern in (f'*.conflict.*{extension}', f'*.sync-conflict*{extension}')
)
IGNORED_DIRECTORIES = ('.obsidian/', '.obsidian-sync-agent/', '.trash/')

_IGNORED_SEGMENTS = frozenset(
    directory.rstrip('/') for directory in IGNORED_DIRECTORIES
)


def is_conflict_file(path: str) -> bool:
    name = PurePosixPath(path).name
    return any(fnmatchcase(name, pattern) for pattern in CONFLICT_FILE_PATTERNS)


def is_ignored_path(path: str) -> bool:
    segments = [segment for segment in path.split('/') if segment]
    return any(segment in _IGNORED_SEGMENTS for segment in segments)


def is_markdown_path(path: str) -> bool:
    return path.endswith('.md')


def is_vectorizable_path(path: str) -> bool:
    if not is_markdown_path(path):
        return False
    if is_conflict_file(path):
        return False
    return not is_ignored_path(path)
