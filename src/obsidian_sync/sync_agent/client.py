import logging
import time
from collections.abc import Callable
from types import TracebackType
from typing import Any
from urllib.parse import quote

import httpx

from obsidian_sync.core.exceptions import ErrorCode
from obsidian_sync.schemas.sync import (
    DeleteFileData,
    FileContentData,
    PutFileData,
    RegisterDeviceData,
    SyncChangesData,
    SyncStatusData,
)

DEFAULT_TIMEOUT = 30.0
DEFAULT_PAGE_LIMIT = 500
DEFAULT_MAX_RETRIES = 3
DEFAULT_RETRY_BASE_DELAY = 1.0
DEFAULT_RETRY_MAX_DELAY = 30.0

AGENT_LOGGER_NAME = 'obsidian_sync.agent'

# HTTP statuses treated as transient: request timeout, rate limiting, and any
# server-side error. All other 4xx statuses (in particular 409 SYNC_CONFLICT)
# must fail immediately so the caller's conflict handling can run.
_TRANSIENT_STATUS_CODES = frozenset({408, 429}) | frozenset(range(500, 600))

# `Retry-After` is only meaningful on responses that explicitly document it.
_RETRY_AFTER_STATUS_CODES = frozenset({429, 503})

# Whitelist of known-transient network failures (connection reset/refused,
# DNS failure, timeouts). Any other httpx.RequestError (bad URL, unsupported
# protocol, decoding error, ...) is treated as permanent and fails fast.
_TRANSIENT_NETWORK_ERRORS: tuple[type[httpx.RequestError], ...] = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.ReadError,
    httpx.WriteError,
    httpx.CloseError,
    httpx.RemoteProtocolError,
)


class SyncApiError(Exception):
    """Raised when the sync server returns an error or is unreachable."""

    def __init__(
        self,
        message: str,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.details = details or {}


class SyncConflictError(Exception):
    """Raised when the server reports a SYNC_CONFLICT (HTTP 409)."""

    def __init__(self, *, details: dict[str, Any], message: str) -> None:
        super().__init__(message)
        self.details = details


def encode_vault_path(path: str) -> str:
    return '/'.join(quote(segment, safe='') for segment in path.split('/'))


def _parse_retry_after(response: httpx.Response) -> float | None:
    """Parse a numeric-seconds `Retry-After` header on 429/503 responses."""
    if response.status_code not in _RETRY_AFTER_STATUS_CODES:
        return None
    raw = response.headers.get('Retry-After')
    if raw is None:
        return None
    try:
        return float(int(raw.strip()))
    except ValueError:
        return None


class SyncClient:
    def __init__(
        self,
        base_url: str,
        token: str | None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        max_retries: int = DEFAULT_MAX_RETRIES,
        retry_base_delay: float = DEFAULT_RETRY_BASE_DELAY,
        retry_max_delay: float = DEFAULT_RETRY_MAX_DELAY,
        logger: logging.Logger | None = None,
        sleep: Callable[[float], None] | None = None,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {'Accept': 'application/json'}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        self._client = httpx.Client(
            base_url=base_url.rstrip('/'),
            headers=headers,
            timeout=timeout,
            transport=transport,
        )
        self._max_retries = max_retries
        self._retry_base_delay = retry_base_delay
        self._retry_max_delay = retry_max_delay
        self._logger = logger or logging.getLogger(AGENT_LOGGER_NAME)
        self._sleep = sleep or time.sleep

    def __enter__(self) -> SyncClient:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def register_device(
        self,
        vault_id: str,
        *,
        device_id: str,
        device_name: str | None,
    ) -> RegisterDeviceData:
        body: dict[str, Any] = {'device_id': device_id}
        if device_name is not None:
            body['device_name'] = device_name
        response = self._send(
            'POST',
            f'/vaults/{quote(vault_id, safe="")}/sync/devices',
            json=body,
        )
        return RegisterDeviceData.model_validate(self._success_data(response))

    def get_changes(
        self,
        vault_id: str,
        *,
        since: int,
        device_id: str | None = None,
        limit: int = DEFAULT_PAGE_LIMIT,
    ) -> SyncChangesData:
        params: dict[str, Any] = {'since': since, 'limit': limit}
        if device_id is not None:
            params['device_id'] = device_id
        response = self._send(
            'GET',
            f'/vaults/{quote(vault_id, safe="")}/sync/changes',
            params=params,
        )
        return SyncChangesData.model_validate(self._success_data(response))

    def get_status(
        self,
        vault_id: str,
        *,
        device_id: str | None = None,
    ) -> SyncStatusData:
        params: dict[str, Any] = {}
        if device_id is not None:
            params['device_id'] = device_id
        response = self._send(
            'GET',
            f'/vaults/{quote(vault_id, safe="")}/sync/status',
            params=params,
        )
        return SyncStatusData.model_validate(self._success_data(response))

    def get_file(self, vault_id: str, path: str) -> FileContentData:
        response = self._send(
            'GET',
            f'/vaults/{quote(vault_id, safe="")}/files/{encode_vault_path(path)}',
        )
        return FileContentData.model_validate(self._success_data(response))

    def put_file(
        self,
        vault_id: str,
        path: str,
        *,
        device_id: str,
        base_revision: int,
        content_hash: str,
        content: str,
    ) -> PutFileData:
        response = self._send(
            'PUT',
            f'/vaults/{quote(vault_id, safe="")}/files/{encode_vault_path(path)}',
            json={
                'device_id': device_id,
                'base_revision': base_revision,
                'content_hash': content_hash,
                'content': content,
            },
        )
        return PutFileData.model_validate(self._success_data(response))

    def delete_file(
        self,
        vault_id: str,
        path: str,
        *,
        device_id: str,
        base_revision: int,
    ) -> DeleteFileData:
        response = self._send(
            'DELETE',
            f'/vaults/{quote(vault_id, safe="")}/files/{encode_vault_path(path)}',
            json={'device_id': device_id, 'base_revision': base_revision},
        )
        return DeleteFileData.model_validate(self._success_data(response))

    def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        attempt = 0
        while True:
            try:
                response = self._client.request(method, url, **kwargs)
            except _TRANSIENT_NETWORK_ERRORS as exc:
                if attempt >= self._max_retries:
                    raise SyncApiError(
                        f'could not reach sync server at {self._client.base_url} '
                        f'after {attempt + 1} attempt(s): {exc}'
                    ) from exc
                self._wait_before_retry(
                    method, url, attempt, retry_after=None, reason=str(exc)
                )
                attempt += 1
                continue
            except httpx.RequestError as exc:
                raise SyncApiError(
                    f'could not reach sync server at {self._client.base_url}: {exc}'
                ) from exc

            if (
                response.status_code in _TRANSIENT_STATUS_CODES
                and attempt < self._max_retries
            ):
                self._wait_before_retry(
                    method,
                    url,
                    attempt,
                    retry_after=_parse_retry_after(response),
                    reason=f'HTTP {response.status_code}',
                )
                attempt += 1
                continue
            return response

    def _wait_before_retry(
        self,
        method: str,
        url: str,
        attempt: int,
        *,
        retry_after: float | None,
        reason: str,
    ) -> None:
        delay = self._compute_delay(attempt, retry_after)
        self._logger.warning(
            'retrying %s %s (attempt %d/%d) in %.1fs after %s',
            method,
            url,
            attempt + 1,
            self._max_retries,
            delay,
            reason,
        )
        self._sleep(delay)

    def _compute_delay(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return max(0.0, min(retry_after, self._retry_max_delay))
        delay = self._retry_base_delay * (2.0**attempt)
        return min(delay, self._retry_max_delay)

    def _success_data(self, response: httpx.Response) -> Any:
        try:
            payload = response.json()
        except ValueError as exc:
            raise SyncApiError(
                f'server returned a non-JSON response (status {response.status_code})'
            ) from exc
        if not isinstance(payload, dict):
            raise SyncApiError('server returned an unexpected response shape')
        if payload.get('success') is True:
            return payload.get('data')

        error = payload.get('error')
        error = error if isinstance(error, dict) else {}
        code = error.get('code')
        message = error.get('message') or 'sync request failed'
        details = error.get('details')
        details = details if isinstance(details, dict) else {}
        if code == ErrorCode.SYNC_CONFLICT.value:
            raise SyncConflictError(details=details, message=message)
        raise SyncApiError(
            message,
            code=code,
            status_code=response.status_code,
            details=details,
        )
