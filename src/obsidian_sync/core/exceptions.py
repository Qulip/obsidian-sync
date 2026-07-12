from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    UNAUTHORIZED = 'UNAUTHORIZED'
    FORBIDDEN = 'FORBIDDEN'
    VALIDATION_ERROR = 'VALIDATION_ERROR'
    NOT_FOUND = 'NOT_FOUND'
    CONFLICT_DETECTED = 'CONFLICT_DETECTED'
    SYNC_CONFLICT = 'SYNC_CONFLICT'
    UNSUPPORTED_FILE_TYPE = 'UNSUPPORTED_FILE_TYPE'
    INDEXING_FAILED = 'INDEXING_FAILED'
    EMBEDDING_FAILED = 'EMBEDDING_FAILED'
    GENERATION_FAILED = 'GENERATION_FAILED'
    DATABASE_ERROR = 'DATABASE_ERROR'
    INTERNAL_ERROR = 'INTERNAL_ERROR'


class AppError(Exception):
    def __init__(
        self,
        code: ErrorCode,
        message: str,
        *,
        status_code: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or {}
        super().__init__(message)
