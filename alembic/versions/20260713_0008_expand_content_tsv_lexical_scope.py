"""expand content_tsv lexical index scope to source_path and tags

Revision ID: 20260713_0008
Revises: 20260712_0007
Create Date: 2026-07-13 00:00:00.000000

`content_tsv` (added in 20260712_0006) only indexed `title` and `content`,
so lexical/hybrid search could not match a query against a note's file
name, path segments, or tags. This migration widens the generated column
to also cover `source_path` and `tags`.

`array_to_string()` is STABLE, not IMMUTABLE (see pg_proc.provolatile), so
it cannot be referenced directly inside a generated column expression.
`immutable_tags_text()` wraps it in a SQL function explicitly marked
IMMUTABLE (safe here: for a fixed array input and fixed separator, the
output is always the same) so it is usable in the generated expression.

`source_path` segments are separated by `/` and file extensions by `.`
(e.g. "projects/alpha-notes.md"). Verified via psql that
`to_tsvector('simple', 'projects/alpha-notes.md')` produces a single,
indivisible lexeme -- the `simple` dictionary's default parser treats a
slash- and dot-containing string as one "file/host"-like token, so a
query for "alpha-notes" or "projects" would never match it. Replacing
both `/` and `.` with spaces before tokenizing splits it into
'projects', 'alpha', 'notes', and 'md', so path/filename-fragment
queries can match. `-` is left alone: the `simple` parser already emits
both the hyphenated compound and its parts (e.g. 'alpha-notes' AND
'alpha' AND 'notes' both appear) for hyphenated words that aren't also
followed by a dot-extension, so no extra preprocessing is needed there.
"""

from collections.abc import Sequence

from alembic import op
from obsidian_sync.db.base import DB_SCHEMA

revision: str = '20260713_0008'
down_revision: str | None = '20260712_0007'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_INDEX_NAME = 'idx_knowledge_chunks_content_tsv'
_FUNCTION_NAME = f'{DB_SCHEMA}.immutable_tags_text'

_OLD_EXPRESSION = "to_tsvector('simple', coalesce(title, '') || ' ' || content)"

_NEW_EXPRESSION = f"""
    to_tsvector(
        'simple',
        coalesce(title, '') || ' ' ||
        replace(replace(coalesce(source_path, ''), '/', ' '), '.', ' ') || ' ' ||
        {_FUNCTION_NAME}(tags) || ' ' ||
        content
    )
""".strip()


def upgrade() -> None:
    op.execute(f'DROP INDEX IF EXISTS {DB_SCHEMA}.{_INDEX_NAME}')
    op.execute(
        f"""
        ALTER TABLE {DB_SCHEMA}.knowledge_chunks
        DROP COLUMN IF EXISTS content_tsv
        """
    )
    op.execute(
        f"""
        CREATE FUNCTION {_FUNCTION_NAME}(text[])
        RETURNS text
        LANGUAGE sql
        IMMUTABLE
        PARALLEL SAFE
        AS $$
            SELECT array_to_string(coalesce($1, ARRAY[]::text[]), ' ')
        $$
        """
    )
    op.execute(
        f"""
        ALTER TABLE {DB_SCHEMA}.knowledge_chunks
        ADD COLUMN content_tsv tsvector
        GENERATED ALWAYS AS ({_NEW_EXPRESSION}) STORED
        """
    )
    op.execute(
        f"""
        CREATE INDEX {_INDEX_NAME}
        ON {DB_SCHEMA}.knowledge_chunks
        USING gin (content_tsv)
        """
    )


def downgrade() -> None:
    op.execute(f'DROP INDEX IF EXISTS {DB_SCHEMA}.{_INDEX_NAME}')
    op.execute(
        f"""
        ALTER TABLE {DB_SCHEMA}.knowledge_chunks
        DROP COLUMN IF EXISTS content_tsv
        """
    )
    op.execute(f'DROP FUNCTION IF EXISTS {_FUNCTION_NAME}(text[])')
    op.execute(
        f"""
        ALTER TABLE {DB_SCHEMA}.knowledge_chunks
        ADD COLUMN content_tsv tsvector
        GENERATED ALWAYS AS ({_OLD_EXPRESSION}) STORED
        """
    )
    op.execute(
        f"""
        CREATE INDEX {_INDEX_NAME}
        ON {DB_SCHEMA}.knowledge_chunks
        USING gin (content_tsv)
        """
    )
