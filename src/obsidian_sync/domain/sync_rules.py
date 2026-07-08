from fnmatch import fnmatchcase
from pathlib import PurePosixPath

CONFLICT_FILE_PATTERNS = ('*.conflict.*.md', '*.sync-conflict*.md')
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


def is_vectorizable_path(path: str) -> bool:
    if not path.endswith('.md'):
        return False
    if is_conflict_file(path):
        return False
    return not is_ignored_path(path)
