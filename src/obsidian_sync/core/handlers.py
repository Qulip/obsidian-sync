from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from obsidian_sync.core.exceptions import AppError, ErrorCode
from obsidian_sync.core.responses import error_response


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return error_response(
        code=exc.code,
        message=exc.message,
        status_code=exc.status_code,
        details=exc.details,
    )


async def validation_exception_handler(
    _: Request,
    exc: RequestValidationError,
) -> JSONResponse:
    return error_response(
        code=ErrorCode.VALIDATION_ERROR,
        message='Request validation failed.',
        status_code=422,
        details={'errors': exc.errors()},
    )


async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    message = detail if isinstance(detail, str) else 'HTTP request failed.'
    details: dict[str, Any] = {}
    if not isinstance(detail, str):
        details['detail'] = detail

    code = ErrorCode.NOT_FOUND if exc.status_code == 404 else ErrorCode.INTERNAL_ERROR
    return error_response(
        code=code,
        message=message,
        status_code=exc.status_code,
        details=details,
    )
