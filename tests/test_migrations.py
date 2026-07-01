from pathlib import Path
from unittest import TestCase


class MigrationTests(TestCase):
    def test_hnsw_embedding_index_migration_exists(self) -> None:
        migration = Path(
            'alembic/versions/20260701_0003_add_chunks_embedding_hnsw.py'
        ).read_text(encoding='utf-8')

        self.assertIn('USING hnsw (embedding vector_cosine_ops)', migration)
        self.assertIn('WHERE embedding IS NOT NULL', migration)
