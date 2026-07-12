from pathlib import Path
from unittest import TestCase


class MigrationTests(TestCase):
    def test_hnsw_embedding_index_migration_exists(self) -> None:
        migration = Path(
            'alembic/versions/20260701_0003_add_chunks_embedding_hnsw.py'
        ).read_text(encoding='utf-8')

        self.assertIn('USING hnsw (embedding vector_cosine_ops)', migration)
        self.assertIn('WHERE embedding IS NOT NULL', migration)

    def test_content_tsv_hybrid_search_migration_exists(self) -> None:
        migration = Path(
            'alembic/versions/20260712_0006_add_content_tsv_hybrid_search.py'
        ).read_text(encoding='utf-8')

        self.assertIn('content_tsv tsvector', migration)
        self.assertIn('GENERATED ALWAYS AS', migration)
        self.assertIn("to_tsvector('simple'", migration)
        self.assertIn('STORED', migration)
        self.assertIn('USING gin (content_tsv)', migration)
        self.assertIn('DROP COLUMN IF EXISTS content_tsv', migration)

    def test_search_feedback_migration_exists(self) -> None:
        migration = Path(
            'alembic/versions/20260712_0007_add_search_feedback.py'
        ).read_text(encoding='utf-8')

        for column in (
            'feedback_helpful',
            'feedback_selected_source_path',
            'feedback_selected_chunk_rank',
            'feedback_expected_missing',
            'feedback_comment',
            'feedback_at',
        ):
            self.assertIn(column, migration)
        self.assertIn('nullable=True', migration)
        self.assertIn("op.drop_column('search_logs', 'feedback_at'", migration)
