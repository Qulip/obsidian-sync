"""Foreground watch mode: debounce filesystem events into `run_sync()` calls.

Design notes (self-loop prevention)
------------------------------------
`run_sync()` can write to the vault (pull applies server changes, conflict
resolution writes ``*.conflict.*``/``*.local-backup.conflict.*`` files). On a
plain OS a write shows up as a filesystem event a moment later; without a
guard that event would re-enter the debouncer and trigger another sync,
which could in turn write again, looping forever.

Two structural guards close this hole, mirroring the "external sync in
progress" / "internal write in progress" duality used by
obsidian-auto-note-importer's FileWatcher, but adapted to this codebase's
"one `run_sync()` call does everything" engine (there is no per-write hook to
flag individually):

1. ``SyncGate.begin()`` is called immediately before `run_sync()` and makes
   `should_ignore()` return True for the whole duration of the call, so any
   event delivered while a sync is running (including the sync's own writes)
   is dropped before it ever reaches the debouncer.
2. Because filesystem watchers (FSEvents on macOS in particular) can deliver
   an event with real latency -- sometimes after the write that caused it
   has already completed and `run_sync()` has returned -- `SyncGate.end()`
   opens a short *drain* window (`quiet_until`) during which `should_ignore()`
   keeps returning True. The drain length reuses ``watch_debounce_seconds``:
   it is already the signal for "how long this vault stays quiet after a
   burst", so no new config knob is needed, and it comfortably covers
   observed FSEvents latency (typically well under a second).

Trade-off: an event for a *genuine* external edit that lands inside the
drain window is dropped, not queued, so it will not by itself trigger the
next sync. In practice this is rare (the window is a couple of seconds) and
is bounded by `watch_interval_seconds`, the periodic safety net -- operators
who need a hard upper bound on staleness should set it to a small positive
value instead of leaving it at the default (0 = disabled).

Note that most of `run_sync()`'s own writes never reach this guard in the
first place: the manifest lives under `.obsidian-sync-agent/`, and conflict
files match `is_conflict_file()` -- both are already excluded by
`should_sync()` / `is_relevant_path()` regardless of the gate. The gate exists
for the remaining case: a pull writing a legitimate note/attachment path.
"""

import logging
import signal
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType
from typing import Any

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from obsidian_sync.sync_agent.config import AgentConfig
from obsidian_sync.sync_agent.engine import SyncSummary, run_sync
from obsidian_sync.sync_agent.ignore import is_ignored_dir, should_sync

DEFAULT_POLL_INTERVAL_SECONDS = 0.5
MIN_POLL_INTERVAL_SECONDS = 0.1


@dataclass(slots=True)
class WatchDebouncer:
    """Collapses a burst of filesystem events into a single sync trigger.

    Every recorded event pushes the "quiet since" clock forward; `should_run`
    only returns True once ``debounce_seconds`` have elapsed with no new
    events, so a large paste/rename burst converges to exactly one sync.
    """

    debounce_seconds: float
    _pending: bool = field(default=False, init=False)
    _last_event_at: float = field(default=0.0, init=False)

    def record_event(self, now: float) -> None:
        self._pending = True
        self._last_event_at = now

    def should_run(self, now: float) -> bool:
        if not self._pending:
            return False
        return (now - self._last_event_at) >= self.debounce_seconds

    def reset(self) -> None:
        self._pending = False

    @property
    def has_pending(self) -> bool:
        return self._pending


@dataclass(slots=True)
class SyncGate:
    """Tracks "a sync is running" plus a post-sync drain window.

    See the module docstring for why both states are needed to prevent a
    sync's own writes from re-triggering another sync.
    """

    _running: bool = field(default=False, init=False)
    _quiet_until: float = field(default=0.0, init=False)

    def begin(self) -> None:
        self._running = True

    def end(self, now: float, drain_seconds: float) -> None:
        self._running = False
        self._quiet_until = now + max(drain_seconds, 0.0)

    def should_ignore(self, now: float) -> bool:
        return self._running or now < self._quiet_until

    @property
    def is_running(self) -> bool:
        return self._running


def is_relevant_path(
    rel_path: str, *, is_directory: bool, sync_attachments: bool
) -> bool:
    """Return True when a vault-relative filesystem event should wake sync.

    Reuses the same exclusion rules as the sync engine itself
    (`should_sync` / `is_ignored_dir`) so watch mode never treats a path as
    relevant that the engine would then refuse to push or pull -- in
    particular hidden directories, `.obsidian-sync-agent/`, and
    conflict/backup files are always excluded.
    """
    segments = [segment for segment in rel_path.split('/') if segment]
    if not segments:
        return False
    if is_directory:
        return not any(is_ignored_dir(segment) for segment in segments)
    return should_sync(rel_path, sync_attachments=sync_attachments)


class VaultEventHandler(FileSystemEventHandler):
    """Thin watchdog adapter: converts events to a relative path + delegates.

    The actual decision logic lives in `handle_path`, a plain method taking
    strings, so tests can drive it without constructing real watchdog event
    objects or touching the filesystem.
    """

    def __init__(
        self,
        *,
        vault_root: Path,
        sync_attachments: bool,
        gate: SyncGate,
        debouncer: WatchDebouncer,
        logger: logging.Logger,
        now_fn: Callable[[], float] = time.monotonic,
    ) -> None:
        self._vault_root = vault_root
        self._sync_attachments = sync_attachments
        self._gate = gate
        self._debouncer = debouncer
        self._logger = logger
        self._now_fn = now_fn

    def on_created(self, event: FileSystemEvent) -> None:
        self.handle_path(_event_path(event.src_path), event.is_directory)

    def on_modified(self, event: FileSystemEvent) -> None:
        self.handle_path(_event_path(event.src_path), event.is_directory)

    def on_deleted(self, event: FileSystemEvent) -> None:
        self.handle_path(_event_path(event.src_path), event.is_directory)

    def on_moved(self, event: FileSystemEvent) -> None:
        self.handle_path(_event_path(event.src_path), event.is_directory)
        dest_path = getattr(event, 'dest_path', None)
        if dest_path:
            self.handle_path(_event_path(dest_path), event.is_directory)

    def handle_path(self, absolute_path: str, is_directory: bool) -> None:
        rel_path = self._to_relative(absolute_path)
        if rel_path is None:
            return
        now = self._now_fn()
        if self._gate.should_ignore(now):
            self._logger.debug('watch: ignoring self-triggered event %s', rel_path)
            return
        if not is_relevant_path(
            rel_path,
            is_directory=is_directory,
            sync_attachments=self._sync_attachments,
        ):
            return
        self._logger.debug('watch: relevant change detected: %s', rel_path)
        self._debouncer.record_event(now)

    def _to_relative(self, absolute_path: str) -> str | None:
        try:
            rel = Path(absolute_path).relative_to(self._vault_root)
        except ValueError:
            return None
        return rel.as_posix()


def _event_path(raw_path: str | bytes) -> str:
    return raw_path.decode() if isinstance(raw_path, bytes) else raw_path


def _backoff_delay(failures: int, base_delay: float, max_delay: float) -> float:
    multiplier = float(2 ** (failures - 1))
    return min(base_delay * multiplier, max_delay)


def _watch_loop(
    *,
    gate: SyncGate,
    debouncer: WatchDebouncer,
    stop_event: threading.Event,
    sync_fn: Callable[[], SyncSummary],
    watch_interval_seconds: float,
    poll_interval: float,
    drain_seconds: float,
    retry_base_delay: float,
    retry_max_delay: float,
    logger: logging.Logger,
    now_fn: Callable[[], float] = time.monotonic,
    sleep_fn: Callable[[float], object] | None = None,
) -> None:
    sleep = sleep_fn if sleep_fn is not None else stop_event.wait
    consecutive_failures = 0
    last_run = now_fn()

    while not stop_event.is_set():
        now = now_fn()
        due_to_events = debouncer.should_run(now)
        due_to_interval = (
            watch_interval_seconds > 0 and (now - last_run) >= watch_interval_seconds
        )
        if not (due_to_events or due_to_interval):
            sleep(poll_interval)
            continue

        debouncer.reset()
        gate.begin()
        try:
            summary = sync_fn()
        except Exception as exc:  # noqa: BLE001 - watch must never crash
            consecutive_failures += 1
            logger.error(
                'watch: sync failed (%d consecutive failure(s)): %s',
                consecutive_failures,
                exc,
            )
        else:
            consecutive_failures = 0
            logger.info(
                'watch: sync ok (pulled=%d applied=%d pushed=%d conflicts=%d)',
                summary.pulled,
                summary.applied,
                summary.pushed,
                len(summary.conflicts),
            )
        finally:
            finish_time = now_fn()
            gate.end(finish_time, drain_seconds)
            last_run = finish_time

        if consecutive_failures:
            delay = _backoff_delay(
                consecutive_failures, retry_base_delay, retry_max_delay
            )
            logger.info('watch: backing off %.1fs before retrying', delay)
            sleep(delay)


def run_watch(config: AgentConfig, logger: logging.Logger) -> int:
    """Run the foreground watch daemon until SIGINT/SIGTERM. Always exits 0.

    Failures during individual sync cycles are logged and retried with
    backoff (see `_watch_loop`); they never terminate the process. Only a
    clean shutdown signal stops `watch`.
    """
    stop_event = threading.Event()
    previous_handlers: dict[int, Any] = {}

    def _handle_signal(signum: int, _frame: FrameType | None) -> None:
        logger.info(
            'watch: received signal %s, finishing current sync then stopping',
            signum,
        )
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        previous_handlers[sig] = signal.signal(sig, _handle_signal)

    gate = SyncGate()
    debouncer = WatchDebouncer(debounce_seconds=config.watch_debounce_seconds)
    handler = VaultEventHandler(
        vault_root=config.vault_root,
        sync_attachments=config.sync_attachments,
        gate=gate,
        debouncer=debouncer,
        logger=logger,
    )

    observer = Observer()
    observer.schedule(handler, str(config.vault_root), recursive=True)
    observer.start()

    logger.info(
        'watch: watching %s (debounce=%.1fs, interval=%.1fs)',
        config.vault_root,
        config.watch_debounce_seconds,
        config.watch_interval_seconds,
    )

    poll_interval = max(
        MIN_POLL_INTERVAL_SECONDS,
        min(DEFAULT_POLL_INTERVAL_SECONDS, config.watch_debounce_seconds / 4),
    )

    try:
        _watch_loop(
            gate=gate,
            debouncer=debouncer,
            stop_event=stop_event,
            sync_fn=lambda: run_sync(config, dry_run=False, logger=logger),
            watch_interval_seconds=config.watch_interval_seconds,
            poll_interval=poll_interval,
            drain_seconds=config.watch_debounce_seconds,
            retry_base_delay=config.retry_base_delay,
            retry_max_delay=config.retry_max_delay,
            logger=logger,
        )
    finally:
        observer.stop()
        observer.join()
        for signum, previous in previous_handlers.items():
            signal.signal(signum, previous)

    logger.info('watch: stopped cleanly')
    return 0
