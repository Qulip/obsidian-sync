import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from obsidian_sync.clients.ollama import OllamaClient
from obsidian_sync.core.config import Settings
from obsidian_sync.repositories.indexing import IndexingRepository
from obsidian_sync.services.indexing import ReindexService

logger = logging.getLogger(__name__)


class PostSyncIndexDispatcher(Protocol):
    def enqueue_file(self, *, vault_id: str, source_path: str) -> None: ...


class NoopPostSyncIndexDispatcher:
    def enqueue_file(self, *, vault_id: str, source_path: str) -> None:
        return None


class _ReindexFileService(Protocol):
    async def reindex_file(
        self,
        *,
        vault_id: str,
        source_path: str,
        content: str | None,
    ) -> object: ...


@dataclass(frozen=True, slots=True)
class _IndexJob:
    vault_id: str
    source_path: str


ServiceFactory = Callable[[Any, Settings], _ReindexFileService]
Sessionmaker = Callable[[], Any]


class AsyncPostSyncIndexWorker:
    def __init__(
        self,
        *,
        settings: Settings,
        service_factory: ServiceFactory | None = None,
    ) -> None:
        self.settings = settings
        self._service_factory = service_factory or _default_service_factory
        self._queue: asyncio.Queue[_IndexJob | None] = asyncio.Queue()
        self._dedupe: set[_IndexJob] = set()
        self._running: set[_IndexJob] = set()
        self._dirty: set[_IndexJob] = set()
        self._sessionmaker: Sessionmaker | None = None
        self._task: asyncio.Task[None] | None = None
        self._accepting = False

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, sessionmaker: Sessionmaker) -> None:
        if self.running:
            return
        self._sessionmaker = sessionmaker
        self._accepting = True
        self._task = asyncio.create_task(self._run(), name='post-sync-index-worker')

    def enqueue_file(self, *, vault_id: str, source_path: str) -> None:
        if not self._accepting or not self.running:
            return
        job = _IndexJob(vault_id=vault_id, source_path=source_path)
        if job in self._running:
            self._dirty.add(job)
            return
        if job in self._dedupe:
            return
        self._dedupe.add(job)
        self._queue.put_nowait(job)

    async def join(self) -> None:
        await self._queue.join()

    async def stop(self) -> None:
        self._accepting = False
        task = self._task
        if task is None:
            return
        await self._queue.join()
        self._queue.put_nowait(None)
        await task
        self._task = None
        self._sessionmaker = None

    async def _run(self) -> None:
        while True:
            job = await self._queue.get()
            try:
                if job is None:
                    return
                self._running.add(job)
                await self._process_job(job)
            finally:
                if job is not None:
                    self._running.discard(job)
                    if job in self._dirty:
                        self._dirty.discard(job)
                        self._queue.put_nowait(job)
                    else:
                        self._dedupe.discard(job)
                self._queue.task_done()

    async def _process_job(self, job: _IndexJob) -> None:
        if self._sessionmaker is None:
            logger.error('post-sync index worker started without a sessionmaker')
            return
        try:
            async with self._sessionmaker() as session:
                try:
                    service = self._service_factory(session, self.settings)
                    await service.reindex_file(
                        vault_id=job.vault_id,
                        source_path=job.source_path,
                        content=None,
                    )
                    await session.commit()
                except Exception:
                    await session.rollback()
                    logger.exception(
                        'post-sync indexing failed',
                        extra={
                            'vault_id': job.vault_id,
                            'source_path': job.source_path,
                        },
                    )
        except Exception:
            logger.exception(
                'post-sync indexing session failed',
                extra={
                    'vault_id': job.vault_id,
                    'source_path': job.source_path,
                },
            )


def _default_service_factory(session: Any, settings: Settings) -> ReindexService:
    return ReindexService(
        repository=IndexingRepository(session),
        ollama_client=OllamaClient(
            base_url=settings.ollama_base_url,
            model=settings.embedding_model,
            timeout_seconds=settings.ollama_timeout_seconds,
        ),
        settings=settings,
    )
