from collections.abc import Callable
from typing import Any
from unittest import TestCase

import httpx

from obsidian_sync.sync_agent.client import SyncApiError, SyncClient, SyncConflictError

BASE_URL = 'https://sync.example'


class _DelayRecorder:
    """Stand-in for ``time.sleep`` that records requested delays instead of
    actually waiting, so retry tests run instantly."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def _success_response(data: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={'success': True, 'data': data})


def _registered_device_response() -> httpx.Response:
    return _success_response(
        {'vault_id': 'v1', 'device_id': 'dev1', 'registered': True}
    )


def _conflict_response() -> httpx.Response:
    return httpx.Response(
        409,
        json={
            'success': False,
            'error': {
                'code': 'SYNC_CONFLICT',
                'message': 'revision mismatch',
                'details': {'server_revision': 3, 'client_base_revision': 1},
            },
        },
    )


def _server_error_response(
    status_code: int, *, retry_after: str | None = None
) -> httpx.Response:
    headers = {'Retry-After': retry_after} if retry_after else {}
    return httpx.Response(
        status_code,
        headers=headers,
        json={'success': False, 'error': {'code': 'INTERNAL_ERROR', 'message': 'boom'}},
    )


def _make_client(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    max_retries: int = 3,
    retry_base_delay: float = 1.0,
    retry_max_delay: float = 30.0,
    sleep: Callable[[float], None] | None = None,
) -> SyncClient:
    return SyncClient(
        BASE_URL,
        None,
        max_retries=max_retries,
        retry_base_delay=retry_base_delay,
        retry_max_delay=retry_max_delay,
        sleep=sleep or (lambda _delay: None),
        transport=httpx.MockTransport(handler),
    )


class TransientNetworkErrorRetryTests(TestCase):
    def test_retries_after_transient_network_error_then_succeeds(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if len(calls) == 1:
                raise httpx.ConnectError('connection refused', request=request)
            return _registered_device_response()

        recorder = _DelayRecorder()
        with _make_client(handler, sleep=recorder) as client:
            result = client.register_device('v1', device_id='dev1', device_name=None)

        self.assertTrue(result.registered)
        self.assertEqual(len(calls), 2)
        self.assertEqual(recorder.delays, [1.0])


class SyncConflictIsNotRetriedTests(TestCase):
    def test_409_sync_conflict_propagates_immediately(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return _conflict_response()

        recorder = _DelayRecorder()
        with _make_client(handler, sleep=recorder) as client:
            with self.assertRaises(SyncConflictError):
                client.register_device('v1', device_id='dev1', device_name=None)

        self.assertEqual(len(calls), 1)
        self.assertEqual(recorder.delays, [])


class MaxRetriesExhaustedTests(TestCase):
    def test_persistent_5xx_status_fails_after_max_retries(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return _server_error_response(503)

        recorder = _DelayRecorder()
        with _make_client(handler, max_retries=2, sleep=recorder) as client:
            with self.assertRaises(SyncApiError):
                client.register_device('v1', device_id='dev1', device_name=None)

        # initial attempt + 2 retries, with exponential backoff (1s, 2s).
        self.assertEqual(len(calls), 3)
        self.assertEqual(recorder.delays, [1.0, 2.0])

    def test_persistent_network_error_fails_after_max_retries(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            raise httpx.ConnectTimeout('timed out', request=request)

        recorder = _DelayRecorder()
        with _make_client(handler, max_retries=1, sleep=recorder) as client:
            with self.assertRaises(SyncApiError):
                client.register_device('v1', device_id='dev1', device_name=None)

        self.assertEqual(len(calls), 2)
        self.assertEqual(recorder.delays, [1.0])


class RetryAfterHeaderTests(TestCase):
    def test_retry_after_overrides_computed_backoff(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if len(calls) == 1:
                return _server_error_response(429, retry_after='5')
            return _registered_device_response()

        recorder = _DelayRecorder()
        with _make_client(handler, retry_base_delay=1.0, sleep=recorder) as client:
            client.register_device('v1', device_id='dev1', device_name=None)

        self.assertEqual(recorder.delays, [5.0])

    def test_retry_after_is_capped_by_retry_max_delay(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            if len(calls) == 1:
                return _server_error_response(503, retry_after='120')
            return _registered_device_response()

        recorder = _DelayRecorder()
        with _make_client(handler, retry_max_delay=10.0, sleep=recorder) as client:
            client.register_device('v1', device_id='dev1', device_name=None)

        self.assertEqual(recorder.delays, [10.0])
