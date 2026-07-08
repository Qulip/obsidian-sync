from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from mcp.server.fastmcp import FastMCP
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.exceptions import HTTPException
from starlette.types import ExceptionHandler

from obsidian_sync.api.mcp_router import mcp_router
from obsidian_sync.api.router import admin_router, api_router
from obsidian_sync.core.config import Settings, get_settings
from obsidian_sync.core.exceptions import AppError, ErrorCode
from obsidian_sync.core.handlers import (
    app_error_handler,
    http_exception_handler,
    validation_exception_handler,
)
from obsidian_sync.core.responses import error_response
from obsidian_sync.db.session import build_async_engine, build_sessionmaker
from obsidian_sync.mcp_server import create_mcp_app, create_mcp_server

# Allow the JSON envelope (base64/escaping, device_id, hashes) around the raw
# content bytes to exceed the content limit without a false rejection.
_REQUEST_SIZE_OVERHEAD_BYTES = 64 * 1024


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()
    mcp_server: FastMCP | None = None

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved_settings
        engine: AsyncEngine | None = None
        app.state.sessionmaker = None
        if resolved_settings.database_url:
            engine = build_async_engine(resolved_settings.database_url)
            app.state.sessionmaker = build_sessionmaker(engine)
        try:
            if mcp_server is None:
                yield
            else:
                async with mcp_server.session_manager.run():
                    yield
        finally:
            if engine is not None:
                await engine.dispose()

    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        lifespan=lifespan,
    )
    max_request_bytes = (
        resolved_settings.sync_max_content_bytes + _REQUEST_SIZE_OVERHEAD_BYTES
    )

    @app.middleware('http')
    async def limit_request_size(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        content_length = request.headers.get('content-length')
        if content_length is not None:
            try:
                length = int(content_length)
            except ValueError:
                length = None
            if length is not None and length > max_request_bytes:
                return error_response(
                    code=ErrorCode.VALIDATION_ERROR,
                    message='request body too large',
                    status_code=413,
                    details={
                        'max_bytes': max_request_bytes,
                        'content_length': length,
                    },
                )
        return await call_next(request)

    app.add_exception_handler(AppError, cast(ExceptionHandler, app_error_handler))
    app.add_exception_handler(
        RequestValidationError,
        cast(ExceptionHandler, validation_exception_handler),
    )
    app.add_exception_handler(
        HTTPException, cast(ExceptionHandler, http_exception_handler)
    )
    app.include_router(api_router)
    app.include_router(admin_router)
    app.include_router(mcp_router)

    mcp_server = create_mcp_server(app)
    app.mount('', create_mcp_app(mcp_server))

    return app


app = create_app()
