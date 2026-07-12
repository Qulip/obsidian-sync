from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from mcp.server.fastmcp import Context, FastMCP
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.requests import Request
from starlette.types import ASGIApp

from obsidian_sync.api.deps import RequestMetadata
from obsidian_sync.clients.ollama import OllamaClient
from obsidian_sync.core.auth import AuthContext, authenticate_bearer_token
from obsidian_sync.core.config import Settings
from obsidian_sync.core.exceptions import AppError, ErrorCode
from obsidian_sync.core.responses import ResponseEnvelope, ok
from obsidian_sync.repositories.indexing import IndexingRepository
from obsidian_sync.repositories.search import SearchRepository
from obsidian_sync.schemas.indexing import ReindexResult, ReindexVaultRequest
from obsidian_sync.schemas.mcp import McpKnowledgeSearchRequest, McpSyncFileRequest
from obsidian_sync.schemas.search import (
    KnowledgeSearchResponse,
    SearchFeedbackRequest,
    SearchFeedbackResponse,
)
from obsidian_sync.schemas.sync import (
    FileContentData,
    McpGetNoteRequest,
    McpSyncStatusData,
)
from obsidian_sync.schemas.vaults import ListVaultsData, SyncFileData
from obsidian_sync.services.indexing import ReindexService
from obsidian_sync.services.revision_sync import RevisionSyncService
from obsidian_sync.services.search import KnowledgeSearchService, SearchLogService
from obsidian_sync.services.storage import VaultStorage
from obsidian_sync.services.vault_sync import VaultSyncService


def create_mcp_server(app: FastAPI) -> FastMCP:
    mcp = FastMCP(
        name=app.title,
        streamable_http_path='/mcp',
        stateless_http=True,
    )

    @mcp.tool(name='list_vaults_mcp_vaults_get')
    async def list_vaults(ctx: Context[Any, Any, Request]) -> dict[str, Any]:
        """List all available personal knowledge bases."""
        async with _session(app) as session:
            settings = _settings(app)
            metadata = await _metadata(ctx, session)
            service = _vault_service(
                session=session,
                settings=settings,
                metadata=metadata,
            )
            return _dump(ok(await service.list_vaults()))

    @mcp.tool(name='sync_file_mcp_vaults__vault_id__sync_file_post')
    async def sync_file(
        vault_id: str,
        payload: McpSyncFileRequest,
        ctx: Context[Any, Any, Request],
    ) -> dict[str, Any]:
        """Upload markdown content to a personal knowledge base.

        Fails closed with a 409 conflict if `path` already exists with
        different content. Two opt-in ways to write anyway, mutually
        exclusive (400 if both are set):
        - `overwrite=True`: force-replace the existing content. Requires a
          token with overwrite permission, or the call fails with 403 --
          this permission check fires as soon as `overwrite=True` is set,
          even if `path` does not exist yet.
        - `base_revision=<n>`: strict optimistic-concurrency write, same
          semantics as the revision sync API. The write only applies if the
          server's current revision for `path` equals `n`; otherwise it
          returns 409 SYNC_CONFLICT. `base_revision=0` means "create a new
          file" and requires no special token permission.
        """
        async with _session(app) as session:
            settings = _settings(app)
            metadata = await _metadata(ctx, session)
            service = _vault_service(
                session=session,
                settings=settings,
                metadata=metadata,
            )
            return _dump(ok(await service.force_sync_file(vault_id, payload)))

    @mcp.tool(name='reindex_vault_mcp_vaults__vault_id__reindex_post')
    async def reindex_vault(
        vault_id: str,
        payload: ReindexVaultRequest,
        ctx: Context[Any, Any, Request],
    ) -> dict[str, Any]:
        """Re-embed uploaded files so they appear in future search results."""
        async with _session(app) as session:
            await _metadata(ctx, session)
            settings = _settings(app)
            service = _reindex_service(session=session, settings=settings)
            return _dump(
                ok(await service.reindex_vault(vault_id=vault_id, mode=payload.mode))
            )

    @mcp.tool(name='search_knowledge_mcp_knowledge_search_post')
    async def search_knowledge(
        payload: McpKnowledgeSearchRequest,
        ctx: Context[Any, Any, Request],
    ) -> dict[str, Any]:
        """Search personal learning notes using hybrid search (semantic vector
        similarity + PostgreSQL full-text keyword matching, merged via
        Reciprocal Rank Fusion).

        The response includes `pending_vectorizing_jobs`,
        `failed_vectorizing_jobs`, and `index_fresh`. `index_fresh` is
        False when either count is greater than 0: `pending_vectorizing_jobs`
        means some files have not finished indexing yet, and
        `failed_vectorizing_jobs` means some files failed indexing and are
        missing from results entirely -- in both cases call
        `reindex_vault(mode=changed_only)` and search again once it
        completes. `min_score` (0.0-1.0) filters out chunks
        below that cosine-similarity threshold; when the filter removes all
        candidates, `low_confidence=True` and `results` is empty. When there
        were no vector or lexical candidates at all (nothing to filter),
        `no_candidates=True` instead and `results` is also empty. In both
        cases treat an empty `results` as no supporting evidence for the
        query -- never assume a match exists just because `results` is
        empty without checking why.

        The response includes `request_id` -- pass it to
        `submit_search_feedback` afterward to record which result (if any)
        was actually useful.

        If the server has an optional LLM-based rerank step enabled,
        top candidates are reordered by relevance before `top_k` is
        applied and `reranked=True` is reported; it falls back silently
        to the original ranking on any failure.
        """
        async with _session(app) as session:
            settings = _settings(app)
            metadata = await _metadata(ctx, session)
            filters = (
                payload.filters.model_dump(exclude_none=True)
                if payload.filters
                else None
            )
            service = KnowledgeSearchService(
                repository=SearchRepository(session),
                ollama_client=OllamaClient(
                    base_url=settings.ollama_base_url,
                    model=settings.embedding_model,
                    timeout_seconds=settings.ollama_timeout_seconds,
                ),
                settings=settings,
            )
            return _dump(
                ok(
                    await service.search(
                        vault_id=payload.vault_id,
                        query=payload.query,
                        filters=filters,
                        top_k=payload.top_k,
                        project=payload.project,
                        domain=payload.domain,
                        min_score=payload.min_score,
                        token_id=metadata.token_id,
                        client_ip=metadata.client_ip,
                        user_agent=metadata.user_agent,
                    )
                )
            )

    @mcp.tool(name='submit_search_feedback_mcp_knowledge_search_feedback_post')
    async def submit_search_feedback(
        payload: SearchFeedbackRequest,
        ctx: Context[Any, Any, Request],
    ) -> dict[str, Any]:
        """Record whether a search_knowledge result was adopted or irrelevant.

        Attach `request_id` from a prior `search_knowledge` response along
        with `helpful`, `selected_source_path` / `selected_chunk_rank` (the
        result that was actually used), `expected_missing` (a note you
        expected was not returned), and/or `comment`. At least one feedback
        field must be set. This data feeds threshold/ranking tuning for
        search quality -- it does not change the current response.
        """
        async with _session(app) as session:
            await _metadata(ctx, session)
            service = SearchLogService(repository=SearchRepository(session))
            return _dump(
                ok(
                    await service.record_feedback(
                        request_id=payload.request_id,
                        vault_id=payload.vault_id,
                        helpful=payload.helpful,
                        selected_source_path=payload.selected_source_path,
                        selected_chunk_rank=payload.selected_chunk_rank,
                        expected_missing=payload.expected_missing,
                        comment=payload.comment,
                    )
                )
            )

    @mcp.tool(name='get_note_mcp_vaults_note_post')
    async def get_note(
        payload: McpGetNoteRequest,
        ctx: Context[Any, Any, Request],
    ) -> dict[str, Any]:
        """Read the latest content of a note with revision and content hash."""
        async with _session(app) as session:
            settings = _settings(app)
            await _metadata(ctx, session)
            service = _revision_sync_service(session=session, settings=settings)
            return _dump(ok(await service.get_file(payload.vault_id, payload.path)))

    @mcp.tool(name='get_sync_status_mcp_vaults__vault_id__sync_status_get')
    async def get_sync_status(
        vault_id: str,
        ctx: Context[Any, Any, Request],
    ) -> dict[str, Any]:
        """Report the vault's server revision, open conflicts, and pending jobs."""
        async with _session(app) as session:
            settings = _settings(app)
            await _metadata(ctx, session)
            service = _revision_sync_service(session=session, settings=settings)
            status = await service.get_status(vault_id, device_id=None)
            return _dump(
                ok(
                    McpSyncStatusData(
                        vault_id=status.vault_id,
                        server_revision=status.server_revision,
                        open_conflicts=status.open_conflicts,
                        pending_vectorizing_jobs=status.pending_vectorizing_jobs,
                    )
                )
            )

    return mcp


def create_mcp_app(mcp: FastMCP) -> ASGIApp:
    return mcp.streamable_http_app()


@asynccontextmanager
async def _session(app: FastAPI) -> AsyncIterator[AsyncSession]:
    sessionmaker = getattr(app.state, 'sessionmaker', None)
    if not isinstance(sessionmaker, async_sessionmaker):
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            'Database is not configured.',
            status_code=500,
        )

    async with sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


def _settings(app: FastAPI) -> Settings:
    settings = getattr(app.state, 'settings', None)
    if not isinstance(settings, Settings):
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            'Application settings are not configured.',
            status_code=500,
        )
    return settings


async def _metadata(
    ctx: Context[Any, Any, Request],
    session: AsyncSession,
) -> RequestMetadata:
    request = ctx.request_context.request
    if request is None:
        raise AppError(
            ErrorCode.INTERNAL_ERROR,
            'MCP request context is not available.',
            status_code=500,
        )

    auth = await _authenticate(request, session)
    return RequestMetadata(
        client_ip=request.client.host if request.client else None,
        user_agent=request.headers.get('user-agent'),
        token_id=auth.token_id,
        allow_overwrite=auth.allow_overwrite,
    )


async def _authenticate(request: Request, session: AsyncSession) -> AuthContext:
    raw_token = _extract_bearer_token(request)
    return await authenticate_bearer_token(raw_token, session)


def _extract_bearer_token(request: Request) -> str:
    authorization = request.headers.get('authorization')
    if authorization is None:
        raise AppError(
            ErrorCode.UNAUTHORIZED,
            'Bearer token is required.',
            status_code=401,
        )

    scheme, _, token = authorization.partition(' ')
    if scheme.lower() != 'bearer' or not token:
        raise AppError(
            ErrorCode.UNAUTHORIZED,
            'Bearer token is required.',
            status_code=401,
        )
    return token


def _vault_service(
    *,
    session: AsyncSession,
    settings: Settings,
    metadata: RequestMetadata,
) -> VaultSyncService:
    return VaultSyncService(
        session,
        VaultStorage(settings.vault_storage_root, settings.vault_archive_root),
        archived_by=metadata.token_id,
        settings=settings,
        allow_overwrite=metadata.allow_overwrite,
    )


def _revision_sync_service(
    *,
    session: AsyncSession,
    settings: Settings,
) -> RevisionSyncService:
    return RevisionSyncService(
        session,
        VaultStorage(settings.vault_storage_root, settings.vault_archive_root),
        settings,
    )


def _reindex_service(
    *,
    session: AsyncSession,
    settings: Settings,
) -> ReindexService:
    return ReindexService(
        repository=IndexingRepository(session),
        ollama_client=OllamaClient(
            base_url=settings.ollama_base_url,
            model=settings.embedding_model,
            timeout_seconds=settings.ollama_timeout_seconds,
        ),
        settings=settings,
    )


def _dump(
    response: ResponseEnvelope[
        ListVaultsData
        | SyncFileData
        | ReindexResult
        | KnowledgeSearchResponse
        | SearchFeedbackResponse
        | FileContentData
        | McpSyncStatusData
    ],
) -> dict[str, Any]:
    return response.model_dump(mode='json')
