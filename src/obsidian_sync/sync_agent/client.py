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


class SyncClient:
    def __init__(
        self,
        base_url: str,
        token: str | None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        headers = {'Accept': 'application/json'}
        if token:
            headers['Authorization'] = f'Bearer {token}'
        self._client = httpx.Client(
            base_url=base_url.rstrip('/'),
            headers=headers,
            timeout=timeout,
        )

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
        try:
            return self._client.request(method, url, **kwargs)
        except httpx.RequestError as exc:
            raise SyncApiError(
                f'could not reach sync server at {self._client.base_url}: {exc}'
            ) from exc

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
