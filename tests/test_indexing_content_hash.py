# pyright: reportMissingImports=false

from typing import cast

from obsidian_sync.clients.ollama import OllamaClient
from obsidian_sync.core.config import Settings
from obsidian_sync.domain.hashing import sha256_text
from obsidian_sync.repositories.indexing import (
    ChunkWrite,
    IndexingRepository,
    VaultFileRecord,
    VaultRecord,
)
from obsidian_sync.services.indexing import ReindexService

VAULT_ID = 'hash-vault'
SOURCE_PATH = 'notes/fresh.md'
STALE_HASH = '0' * 64


class FakeIndexingRepository:
    def __init__(self) -> None:
        self.writes: list[ChunkWrite] = []
        self.index_statuses: list[tuple[int, str, str | None, bool]] = []
        self.current_content_hash = STALE_HASH
        self.get_file_calls = 0

    async def get_vault(self, vault_id: str) -> VaultRecord | None:
        if vault_id != VAULT_ID:
            return None
        return VaultRecord(id=1, vault_id=vault_id, default_visibility='personal')

    async def get_file(
        self,
        *,
        vault_id: str,
        source_path: str,
    ) -> VaultFileRecord | None:
        if vault_id != VAULT_ID or source_path != SOURCE_PATH:
            return None
        self.get_file_calls += 1
        content_hash = (
            STALE_HASH if self.get_file_calls == 1 else self.current_content_hash
        )
        return VaultFileRecord(
            id=2,
            vault_pk=1,
            vault_id=vault_id,
            source_path=source_path,
            content_hash=content_hash,
            vectorize=True,
            status='current',
            index_status='pending',
            deleted=False,
        )

    async def replace_chunks(self, chunks: list[ChunkWrite]) -> tuple[int, int, int]:
        self.writes = chunks
        return (len(chunks), 0, 0)

    async def delete_chunks(self, *, vault_id: str, source_path: str) -> int:
        return 0

    async def set_file_index_status(
        self,
        *,
        file_id: int,
        index_status: str,
        index_error: str | None,
        indexed: bool,
    ) -> None:
        self.index_statuses.append((file_id, index_status, index_error, indexed))

    async def log_failure(
        self,
        *,
        vault_pk: int | None,
        vault_id: str,
        source_path: str,
        content_hash: str | None,
        phase: str,
        error_code: str,
        error_message: str,
        error_details: dict[str, object] | None = None,
    ) -> None:
        raise AssertionError(error_message)


class FakeOllamaClient:
    async def embed(self, text: str) -> list[float]:
        return [0.1, 0.2, 0.3]


async def test_reindex_file_writes_chunks_with_fresh_markdown_hash() -> None:
    markdown = """---
title: Fresh Note
type: reference
project: Sync
domain: Indexing
status: current
priority: medium
visibility: personal
tags: [fresh]
vectorize: true
created: 2026-07-14
updated: 2026-07-14
---
# Fresh Note

Fresh markdown body that differs from the stale vault file hash.
"""
    repository = FakeIndexingRepository()
    repository.current_content_hash = sha256_text(markdown)
    service = ReindexService(
        repository=cast(IndexingRepository, repository),
        ollama_client=cast(OllamaClient, FakeOllamaClient()),
        settings=Settings(embedding_dimension=3),
    )

    result = await service.reindex_file(
        vault_id=VAULT_ID,
        source_path=SOURCE_PATH,
        content=markdown,
    )

    assert result.indexed_files == 1
    assert len(repository.writes) == 1
    assert repository.writes[0].content_hash == sha256_text(markdown)
    assert repository.writes[0].content_hash != STALE_HASH
    assert repository.index_statuses[-1] == (2, 'indexed', None, True)


async def test_reindex_file_leaves_file_pending_when_hash_changes_again() -> None:
    markdown = """---
title: Fresh Note
type: reference
project: Sync
domain: Indexing
status: current
priority: medium
visibility: personal
tags: [fresh]
vectorize: true
created: 2026-07-14
updated: 2026-07-14
---
# Fresh Note

Fresh markdown body that races with another update.
"""
    repository = FakeIndexingRepository()
    repository.current_content_hash = sha256_text('newer markdown')
    service = ReindexService(
        repository=cast(IndexingRepository, repository),
        ollama_client=cast(OllamaClient, FakeOllamaClient()),
        settings=Settings(embedding_dimension=3),
    )

    result = await service.reindex_file(
        vault_id=VAULT_ID,
        source_path=SOURCE_PATH,
        content=markdown,
    )

    assert result.indexed_files == 0
    assert len(repository.writes) == 1
    assert repository.writes[0].content_hash == sha256_text(markdown)
    assert repository.index_statuses[-1] == (2, 'pending', None, False)
