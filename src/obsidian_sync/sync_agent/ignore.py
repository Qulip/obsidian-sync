from obsidian_sync.domain.sync_rules import is_conflict_file, is_ignored_path

IGNORED_FILE_NAMES = frozenset({'.DS_Store', 'Thumbs.db'})


def is_ignored_dir(name: str) -> bool:
    """Return True for directories that should never be descended into."""
    if name.startswith('.'):
        return True
    return is_ignored_path(f'{name}/')


def should_sync(rel_path: str) -> bool:
    """Return True when a vault-relative path is a syncable markdown file.

    v1 only syncs ``.md`` files; everything else is ignored. Hidden files and
    directories (any segment starting with ``.``), conflict files, and the
    ignored directories from ``domain.sync_rules`` are excluded.
    """
    if not rel_path.endswith('.md'):
        return False
    segments = [segment for segment in rel_path.split('/') if segment]
    if not segments:
        return False
    if segments[-1] in IGNORED_FILE_NAMES:
        return False
    if any(segment.startswith('.') for segment in segments):
        return False
    if is_conflict_file(rel_path):
        return False
    if is_ignored_path(rel_path):
        return False
    return True
