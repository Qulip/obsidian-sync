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
from obsidian_sync.schemas.search import KnowledgeSearchResponse
from obsidian_sync.schemas.sync import (
    FileContentData,
    McpGetNoteRequest,
    McpSyncStatusData,
)
from obsidian_sync.schemas.vaults import ListVaultsData, SyncFileData
from obsidian_sync.services.indexing import ReindexService
from obsidian_sync.services.revision_sync import RevisionSyncService
from obsidian_sync.services.search import KnowledgeSearchService
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
        different content; pass `overwrite=True` to explicitly replace it.
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
        """Search personal learning notes using semantic vector search."""
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
                        token_id=metadata.token_id,
                        client_ip=metadata.client_ip,
                        user_agent=metadata.user_agent,
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
        | FileContentData
        | McpSyncStatusData
    ],
) -> dict[str, Any]:
    return response.model_dump(mode='json')
