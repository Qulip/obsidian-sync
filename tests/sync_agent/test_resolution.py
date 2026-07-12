import json
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import TestCase

import httpx

from obsidian_sync.sync_agent.client import SyncClient
from obsidian_sync.sync_agent.config import AgentConfig
from obsidian_sync.sync_agent.resolution import (
    LOCAL_WINS_MAX_ATTEMPTS,
    resolve_local_wins_delete,
    resolve_local_wins_upsert,
)

BASE_URL = 'https://sync.example'
_LOGGER = logging.getLogger('test.resolution')


def _success_response(data: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={'success': True, 'data': data})


def _conflict_response(server_revision: int) -> httpx.Response:
    return httpx.Response(
        409,
        json={
            'success': False,
            'error': {
                'code': 'SYNC_CONFLICT',
                'message': 'revision mismatch',
                'details': {
                    'server_revision': server_revision,
                    'client_base_revision': 0,
                },
            },
        },
    )


def _make_client(handler: Callable[[httpx.Request], httpx.Response]) -> SyncClient:
    return SyncClient(
        BASE_URL,
        None,
        sleep=lambda _delay: None,
        transport=httpx.MockTransport(handler),
    )


def _config() -> AgentConfig:
    return AgentConfig(
        server_base_url=BASE_URL,
        vault_id='v1',
        vault_root=Path('/tmp/does-not-matter'),
        device_id='laptop',
    )


class ResolveLocalWinsUpsertTests(TestCase):
    def test_resolves_on_first_attempt(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return _success_response(
                {
                    'vault_id': 'v1',
                    'path': 'notes/JPA.md',
                    'revision': 5,
                    'content_hash': 'abc',
                }
            )

        with _make_client(handler) as client:
            outcome = resolve_local_wins_upsert(
                client,
                _config(),
                path='notes/JPA.md',
                content=b'LOCAL',
                base_revision=4,
                logger=_LOGGER,
            )

        self.assertTrue(outcome.resolved)
        self.assertEqual(outcome.revision, 5)
        self.assertEqual(outcome.content_hash, 'abc')
        self.assertEqual(len(calls), 1)

    def test_retries_once_then_resolves_with_updated_base_revision(self) -> None:
        calls: list[httpx.Request] = []
        seen_base_revisions: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            body = json.loads(request.content)
            seen_base_revisions.append(body['base_revision'])
            if len(calls) == 1:
                return _conflict_response(server_revision=9)
            return _success_response(
                {
                    'vault_id': 'v1',
                    'path': 'notes/JPA.md',
                    'revision': 10,
                    'content_hash': 'def',
                }
            )

        with _make_client(handler) as client:
            outcome = resolve_local_wins_upsert(
                client,
                _config(),
                path='notes/JPA.md',
                content=b'LOCAL',
                base_revision=4,
                logger=_LOGGER,
            )

        self.assertTrue(outcome.resolved)
        self.assertEqual(outcome.revision, 10)
        self.assertEqual(len(calls), 2)
        # The retry must use the server_revision reported by the 409, not
        # the original stale base_revision.
        self.assertEqual(seen_base_revisions, [4, 9])

    def test_falls_back_to_manual_after_exhausting_attempts(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return _conflict_response(server_revision=100 + len(calls))

        with _make_client(handler) as client:
            outcome = resolve_local_wins_upsert(
                client,
                _config(),
                path='notes/JPA.md',
                content=b'LOCAL',
                base_revision=4,
                logger=_LOGGER,
            )

        self.assertFalse(outcome.resolved)
        # Bounded: never loops forever on a path that keeps changing.
        self.assertEqual(len(calls), LOCAL_WINS_MAX_ATTEMPTS)


class ResolveLocalWinsDeleteTests(TestCase):
    def test_resolves_on_first_attempt(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return _success_response(
                {
                    'vault_id': 'v1',
                    'path': 'notes/JPA.md',
                    'revision': 5,
                    'deleted': True,
                }
            )

        with _make_client(handler) as client:
            resolved = resolve_local_wins_delete(
                client,
                _config(),
                path='notes/JPA.md',
                base_revision=4,
                logger=_LOGGER,
            )

        self.assertTrue(resolved)
        self.assertEqual(len(calls), 1)

    def test_falls_back_to_manual_after_exhausting_attempts(self) -> None:
        calls: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            calls.append(request)
            return _conflict_response(server_revision=100 + len(calls))

        with _make_client(handler) as client:
            resolved = resolve_local_wins_delete(
                client,
                _config(),
                path='notes/JPA.md',
                base_revision=4,
                logger=_LOGGER,
            )

        self.assertFalse(resolved)
        self.assertEqual(len(calls), LOCAL_WINS_MAX_ATTEMPTS)
