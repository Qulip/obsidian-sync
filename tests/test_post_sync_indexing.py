import asyncio
import logging
from collections.abc import Callable

import pytest

from obsidian_sync.core.config import Settings
from obsidian_sync.services.post_sync_indexing import AsyncPostSyncIndexWorker


class FakeSession:
    def __init__(self, session_id: int) -> None:
        self.session_id = session_id
        self.commits = 0
        self.rollbacks = 0

    async def commit(self) -> None:
        self.commits += 1

    async def rollback(self) -> None:
        self.rollbacks += 1


class FakeSessionContext:
    def __init__(self, session: FakeSession, *, fail_enter: bool = False) -> None:
        self.session = session
        self.fail_enter = fail_enter

    async def __aenter__(self) -> FakeSession:
        if self.fail_enter:
            raise RuntimeError(f'cannot enter session {self.session.session_id}')
        return self.session

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: object,
    ) -> None:
        return None


class FakeSessionmaker:
    def __init__(self, *, fail_enters: int = 0) -> None:
        self.sessions: list[FakeSession] = []
        self.fail_enters = fail_enters

    def __call__(self) -> FakeSessionContext:
        session = FakeSession(len(self.sessions) + 1)
        self.sessions.append(session)
        fail_enter = len(self.sessions) <= self.fail_enters
        return FakeSessionContext(session, fail_enter=fail_enter)


class FakeReindexService:
    def __init__(
        self,
        session: FakeSession,
        calls: list[tuple[int, str, str]],
        fail_paths: set[str] | None = None,
    ) -> None:
        self.session = session
        self.calls = calls
        self.fail_paths = fail_paths or set()

    async def reindex_file(
        self,
        *,
        vault_id: str,
        source_path: str,
        content: str | None,
    ) -> None:
        assert content is None
        self.calls.append((self.session.session_id, vault_id, source_path))
        if source_path in self.fail_paths:
            raise RuntimeError(f'boom: {source_path}')


class BlockingReindexService:
    def __init__(
        self,
        session: FakeSession,
        calls: list[tuple[int, str, str]],
        first_call_started: asyncio.Event,
        release_first_call: asyncio.Event,
    ) -> None:
        self.session = session
        self.calls = calls
        self.first_call_started = first_call_started
        self.release_first_call = release_first_call

    async def reindex_file(
        self,
        *,
        vault_id: str,
        source_path: str,
        content: str | None,
    ) -> None:
        assert content is None
        self.calls.append((self.session.session_id, vault_id, source_path))
        if len(self.calls) == 1:
            self.first_call_started.set()
            await self.release_first_call.wait()


def service_factory(
    calls: list[tuple[int, str, str]],
    fail_paths: set[str] | None = None,
) -> Callable[[FakeSession, Settings], FakeReindexService]:
    def create(session: FakeSession, settings: Settings) -> FakeReindexService:
        assert settings.post_sync_indexing_enabled
        return FakeReindexService(session, calls, fail_paths)

    return create


async def test_worker_processes_deduplicated_jobs_with_fresh_sessions() -> None:
    calls: list[tuple[int, str, str]] = []
    sessionmaker = FakeSessionmaker()
    worker = AsyncPostSyncIndexWorker(
        settings=Settings(),
        service_factory=service_factory(calls),
    )

    worker.start(sessionmaker)
    worker.enqueue_file(vault_id='vault-a', source_path='notes/a.md')
    worker.enqueue_file(vault_id='vault-a', source_path='notes/a.md')
    worker.enqueue_file(vault_id='vault-a', source_path='notes/b.md')
    await worker.join()
    await worker.stop()

    assert calls == [
        (1, 'vault-a', 'notes/a.md'),
        (2, 'vault-a', 'notes/b.md'),
    ]
    assert [session.commits for session in sessionmaker.sessions] == [1, 1]
    assert [session.rollbacks for session in sessionmaker.sessions] == [0, 0]


async def test_worker_rolls_back_failed_job_and_keeps_processing() -> None:
    calls: list[tuple[int, str, str]] = []
    sessionmaker = FakeSessionmaker()
    worker = AsyncPostSyncIndexWorker(
        settings=Settings(),
        service_factory=service_factory(calls, {'notes/fail.md'}),
    )

    worker.start(sessionmaker)
    worker.enqueue_file(vault_id='vault-a', source_path='notes/fail.md')
    worker.enqueue_file(vault_id='vault-a', source_path='notes/ok.md')
    await worker.join()
    await worker.stop()

    assert calls == [
        (1, 'vault-a', 'notes/fail.md'),
        (2, 'vault-a', 'notes/ok.md'),
    ]
    assert sessionmaker.sessions[0].commits == 0
    assert sessionmaker.sessions[0].rollbacks == 1
    assert sessionmaker.sessions[1].commits == 1
    assert sessionmaker.sessions[1].rollbacks == 0


async def test_worker_logs_session_entry_failure_and_keeps_processing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    calls: list[tuple[int, str, str]] = []
    sessionmaker = FakeSessionmaker(fail_enters=1)
    worker = AsyncPostSyncIndexWorker(
        settings=Settings(),
        service_factory=service_factory(calls),
    )
    logger = logging.getLogger('obsidian_sync.services.post_sync_indexing')
    was_disabled = logger.disabled
    logger.addHandler(caplog.handler)

    try:
        logger.disabled = False
        with caplog.at_level(logging.ERROR, logger=logger.name):
            worker.start(sessionmaker)
            worker.enqueue_file(
                vault_id='vault-a',
                source_path='notes/session-fails.md',
            )
            worker.enqueue_file(vault_id='vault-a', source_path='notes/ok.md')
            await worker.join()
            await worker.stop()
    finally:
        logger.disabled = was_disabled
        logger.removeHandler(caplog.handler)

    assert calls == [(2, 'vault-a', 'notes/ok.md')]
    assert [session.commits for session in sessionmaker.sessions] == [0, 1]
    assert [session.rollbacks for session in sessionmaker.sessions] == [0, 0]
    assert 'post-sync indexing session failed' in caplog.text


async def test_worker_coalesces_enqueue_while_job_is_running() -> None:
    calls: list[tuple[int, str, str]] = []
    first_call_started = asyncio.Event()
    release_first_call = asyncio.Event()
    sessionmaker = FakeSessionmaker()

    def create(session: FakeSession, settings: Settings) -> BlockingReindexService:
        assert settings.post_sync_indexing_enabled
        return BlockingReindexService(
            session,
            calls,
            first_call_started,
            release_first_call,
        )

    worker = AsyncPostSyncIndexWorker(
        settings=Settings(),
        service_factory=create,
    )

    worker.start(sessionmaker)
    worker.enqueue_file(vault_id='vault-a', source_path='notes/a.md')
    await first_call_started.wait()
    worker.enqueue_file(vault_id='vault-a', source_path='notes/a.md')
    worker.enqueue_file(vault_id='vault-a', source_path='notes/a.md')
    release_first_call.set()
    await worker.join()
    await worker.stop()

    assert calls == [
        (1, 'vault-a', 'notes/a.md'),
        (2, 'vault-a', 'notes/a.md'),
    ]
    assert [session.commits for session in sessionmaker.sessions] == [1, 1]
    assert [session.rollbacks for session in sessionmaker.sessions] == [0, 0]


async def test_worker_stop_drains_pending_jobs_and_rejects_new_work() -> None:
    calls: list[tuple[int, str, str]] = []
    sessionmaker = FakeSessionmaker()
    worker = AsyncPostSyncIndexWorker(
        settings=Settings(),
        service_factory=service_factory(calls),
    )

    worker.start(sessionmaker)
    worker.enqueue_file(vault_id='vault-a', source_path='notes/a.md')
    worker.enqueue_file(vault_id='vault-a', source_path='notes/b.md')
    await worker.stop()
    worker.enqueue_file(vault_id='vault-a', source_path='notes/c.md')
    await asyncio.sleep(0)

    assert calls == [
        (1, 'vault-a', 'notes/a.md'),
        (2, 'vault-a', 'notes/b.md'),
    ]
    assert len(sessionmaker.sessions) == 2


async def test_worker_stop_before_start_is_clean() -> None:
    worker = AsyncPostSyncIndexWorker(settings=Settings())

    await worker.stop()

    assert not worker.running


def test_app_lifespan_starts_worker_after_sessionmaker_and_stops_before_dispose(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    import obsidian_sync.app as app_module

    events: list[str] = []

    class FakeEngine:
        async def dispose(self) -> None:
            events.append('dispose')

    class FakeWorker:
        def __init__(self, *, settings: Settings) -> None:
            assert settings.post_sync_indexing_enabled
            events.append('worker-init')

        def start(self, sessionmaker: Callable[[], str]) -> None:
            assert sessionmaker() == 'fresh-session'
            events.append('worker-start')

        async def stop(self) -> None:
            events.append('worker-stop')

    def build_fake_engine(database_url: str) -> FakeEngine:
        assert database_url
        events.append('engine')
        return FakeEngine()

    def build_fake_sessionmaker(engine: FakeEngine) -> Callable[[], str]:
        events.append('sessionmaker')
        return lambda: 'fresh-session'

    monkeypatch.setattr(app_module, 'build_async_engine', build_fake_engine)
    monkeypatch.setattr(app_module, 'build_sessionmaker', build_fake_sessionmaker)
    monkeypatch.setattr(app_module, 'AsyncPostSyncIndexWorker', FakeWorker)

    app = app_module.create_app(
        Settings(database_url='postgresql+asyncpg://localhost/test')
    )

    with TestClient(app):
        assert events == [
            'engine',
            'sessionmaker',
            'worker-init',
            'worker-start',
        ]

    assert events == [
        'engine',
        'sessionmaker',
        'worker-init',
        'worker-start',
        'worker-stop',
        'dispose',
    ]
