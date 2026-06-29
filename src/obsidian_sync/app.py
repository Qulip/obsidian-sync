from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import cast

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from sqlalchemy.ext.asyncio import AsyncEngine
from starlette.exceptions import HTTPException
from starlette.types import ExceptionHandler

from obsidian_sync.api.router import admin_router, api_router
from obsidian_sync.core.config import Settings, get_settings
from obsidian_sync.core.exceptions import AppError
from obsidian_sync.core.handlers import (
    app_error_handler,
    http_exception_handler,
    validation_exception_handler,
)
from obsidian_sync.db.session import build_async_engine, build_sessionmaker


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved_settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.settings = resolved_settings
        engine: AsyncEngine | None = None
        app.state.sessionmaker = None
        if resolved_settings.database_url:
            engine = build_async_engine(resolved_settings.database_url)
            app.state.sessionmaker = build_sessionmaker(engine)
        try:
            yield
        finally:
            if engine is not None:
                await engine.dispose()

    app = FastAPI(
        title=resolved_settings.app_name,
        version=resolved_settings.app_version,
        lifespan=lifespan,
    )
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
    return app


app = create_app()
