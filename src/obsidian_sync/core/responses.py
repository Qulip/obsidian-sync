from typing import Any

from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict

from obsidian_sync.core.exceptions import ErrorCode


class ErrorEnvelope(BaseModel):
    code: ErrorCode
    message: str
    details: dict[str, Any]


class ResponseEnvelope[DataT](BaseModel):
    model_config = ConfigDict(use_enum_values=True)

    success: bool
    data: DataT | None
    error: ErrorEnvelope | None


def ok[DataT](data: DataT) -> ResponseEnvelope[DataT]:
    return ResponseEnvelope(success=True, data=data, error=None)


def error_response(
    *,
    code: ErrorCode,
    message: str,
    status_code: int,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    payload = ResponseEnvelope[Any](
        success=False,
        data=None,
        error=ErrorEnvelope(
            code=code,
            message=message,
            details=details or {},
        ),
    )
    return JSONResponse(
        status_code=status_code,
        content=payload.model_dump(mode='json'),
    )
