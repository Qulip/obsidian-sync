import logging
import threading
from pathlib import Path
from unittest import TestCase

from obsidian_sync.sync_agent.engine import SyncSummary
from obsidian_sync.sync_agent.watch import (
    SyncGate,
    VaultEventHandler,
    WatchDebouncer,
    _watch_loop,
    is_relevant_path,
)

_LOGGER = logging.getLogger('test.watch')
_LOGGER.addHandler(logging.NullHandler())


class WatchDebouncerTests(TestCase):
    """Debounce must batch bursts into exactly one trigger."""

    def test_no_run_before_any_event(self) -> None:
        debouncer = WatchDebouncer(debounce_seconds=2.0)
        self.assertFalse(debouncer.should_run(100.0))

    def test_no_run_while_events_keep_arriving(self) -> None:
        debouncer = WatchDebouncer(debounce_seconds=2.0)
        # A burst of events, each within the quiet window of the previous one.
        for t in (0.0, 0.5, 1.0, 1.5, 1.9):
            debouncer.record_event(t)
            self.assertFalse(debouncer.should_run(t + 0.1))

    def test_run_once_burst_goes_quiet(self) -> None:
        debouncer = WatchDebouncer(debounce_seconds=2.0)
        debouncer.record_event(0.0)
        debouncer.record_event(1.0)
        debouncer.record_event(1.9)
        self.assertFalse(debouncer.should_run(3.5))
        self.assertTrue(debouncer.should_run(3.9))

    def test_reset_clears_pending_state(self) -> None:
        debouncer = WatchDebouncer(debounce_seconds=2.0)
        debouncer.record_event(0.0)
        self.assertTrue(debouncer.should_run(5.0))
        debouncer.reset()
        self.assertFalse(debouncer.should_run(5.0))
        self.assertFalse(debouncer.has_pending)

    def test_new_event_after_reset_starts_a_fresh_window(self) -> None:
        debouncer = WatchDebouncer(debounce_seconds=2.0)
        debouncer.record_event(0.0)
        debouncer.reset()
        debouncer.record_event(10.0)
        self.assertFalse(debouncer.should_run(11.0))
        self.assertTrue(debouncer.should_run(12.0))


class SyncGateTests(TestCase):
    """The gate must block events during sync and drain latent ones after."""

    def test_ignores_nothing_before_any_sync(self) -> None:
        gate = SyncGate()
        self.assertFalse(gate.should_ignore(0.0))

    def test_ignores_events_while_running(self) -> None:
        gate = SyncGate()
        gate.begin()
        self.assertTrue(gate.is_running)
        self.assertTrue(gate.should_ignore(0.0))
        self.assertTrue(gate.should_ignore(1000.0))

    def test_drains_events_for_a_window_after_sync_ends(self) -> None:
        gate = SyncGate()
        gate.begin()
        gate.end(now=10.0, drain_seconds=2.0)
        self.assertFalse(gate.is_running)
        self.assertTrue(gate.should_ignore(10.0))
        self.assertTrue(gate.should_ignore(11.9))
        self.assertFalse(gate.should_ignore(12.0))

    def test_zero_drain_reopens_immediately(self) -> None:
        gate = SyncGate()
        gate.begin()
        gate.end(now=10.0, drain_seconds=0.0)
        self.assertFalse(gate.should_ignore(10.0))


class IsRelevantPathTests(TestCase):
    """Watch filtering must match the engine's own should_sync rules."""

    def test_markdown_file_is_relevant(self) -> None:
        self.assertTrue(
            is_relevant_path('Notes/a.md', is_directory=False, sync_attachments=False)
        )

    def test_attachment_is_relevant_only_when_enabled(self) -> None:
        self.assertFalse(
            is_relevant_path(
                'Notes/img.png', is_directory=False, sync_attachments=False
            )
        )
        self.assertTrue(
            is_relevant_path('Notes/img.png', is_directory=False, sync_attachments=True)
        )

    def test_conflict_file_is_never_relevant(self) -> None:
        self.assertFalse(
            is_relevant_path(
                'Notes/a.conflict.dev.20260707-000000.md',
                is_directory=False,
                sync_attachments=False,
            )
        )

    def test_state_directory_is_never_relevant(self) -> None:
        self.assertFalse(
            is_relevant_path(
                '.obsidian-sync-agent/manifest.json',
                is_directory=False,
                sync_attachments=False,
            )
        )

    def test_hidden_directory_is_never_relevant(self) -> None:
        self.assertFalse(
            is_relevant_path('.git/HEAD', is_directory=False, sync_attachments=False)
        )

    def test_plain_directory_event_is_relevant(self) -> None:
        self.assertTrue(
            is_relevant_path('Notes', is_directory=True, sync_attachments=False)
        )

    def test_ignored_directory_event_is_not_relevant(self) -> None:
        self.assertFalse(
            is_relevant_path(
                '.obsidian-sync-agent', is_directory=True, sync_attachments=False
            )
        )

    def test_root_event_is_not_relevant(self) -> None:
        self.assertFalse(
            is_relevant_path('', is_directory=True, sync_attachments=False)
        )


class VaultEventHandlerTests(TestCase):
    """The watchdog adapter must gate + filter before recording an event."""

    def _make_handler(
        self, *, sync_attachments: bool = False, clock: list[float] | None = None
    ) -> tuple[VaultEventHandler, SyncGate, WatchDebouncer]:
        gate = SyncGate()
        debouncer = WatchDebouncer(debounce_seconds=2.0)
        times = clock if clock is not None else [0.0]

        def now_fn() -> float:
            return times[0]

        handler = VaultEventHandler(
            vault_root=Path('/vault'),
            sync_attachments=sync_attachments,
            gate=gate,
            debouncer=debouncer,
            logger=_LOGGER,
            now_fn=now_fn,
        )
        return handler, gate, debouncer

    def test_relevant_change_is_recorded(self) -> None:
        handler, _gate, debouncer = self._make_handler()
        handler.handle_path('/vault/Notes/a.md', False)
        self.assertTrue(debouncer.has_pending)

    def test_irrelevant_change_is_not_recorded(self) -> None:
        handler, _gate, debouncer = self._make_handler()
        handler.handle_path('/vault/.obsidian-sync-agent/manifest.json', False)
        self.assertFalse(debouncer.has_pending)

    def test_path_outside_vault_is_ignored(self) -> None:
        handler, _gate, debouncer = self._make_handler()
        handler.handle_path('/somewhere/else/a.md', False)
        self.assertFalse(debouncer.has_pending)

    def test_event_is_dropped_while_gate_is_running(self) -> None:
        handler, gate, debouncer = self._make_handler()
        gate.begin()
        handler.handle_path('/vault/Notes/a.md', False)
        self.assertFalse(debouncer.has_pending)

    def test_event_is_dropped_during_post_sync_drain(self) -> None:
        clock = [0.0]
        handler, gate, debouncer = self._make_handler(clock=clock)
        gate.begin()
        gate.end(now=0.0, drain_seconds=2.0)
        clock[0] = 1.0  # still inside the drain window
        handler.handle_path('/vault/Notes/a.md', False)
        self.assertFalse(debouncer.has_pending)

    def test_event_after_drain_window_is_recorded(self) -> None:
        clock = [0.0]
        handler, gate, debouncer = self._make_handler(clock=clock)
        gate.begin()
        gate.end(now=0.0, drain_seconds=2.0)
        clock[0] = 2.5  # drain window has closed
        handler.handle_path('/vault/Notes/a.md', False)
        self.assertTrue(debouncer.has_pending)


class WatchLoopTests(TestCase):
    """End-to-end loop behaviour, driven by a fake clock (no real sleeping)."""

    def _run_loop(
        self,
        *,
        sync_fn,
        iterations: int,
        watch_interval_seconds: float = 0.0,
        debounce_seconds: float = 2.0,
        drain_seconds: float = 2.0,
        pre_events: list[float] | None = None,
    ) -> list[float]:
        gate = SyncGate()
        debouncer = WatchDebouncer(debounce_seconds=debounce_seconds)
        for event_time in pre_events or []:
            debouncer.record_event(event_time)

        stop_event = threading.Event()
        clock = [0.0]
        sleep_calls: list[float] = []
        call_count = [0]

        def now_fn() -> float:
            return clock[0]

        def sleep_fn(seconds: float) -> None:
            sleep_calls.append(seconds)
            clock[0] += seconds
            call_count[0] += 1
            if call_count[0] >= iterations:
                stop_event.set()

        _watch_loop(
            gate=gate,
            debouncer=debouncer,
            stop_event=stop_event,
            sync_fn=sync_fn,
            watch_interval_seconds=watch_interval_seconds,
            poll_interval=0.5,
            drain_seconds=drain_seconds,
            retry_base_delay=1.0,
            retry_max_delay=8.0,
            logger=_LOGGER,
            now_fn=now_fn,
            sleep_fn=sleep_fn,
        )
        return sleep_calls

    def test_burst_of_events_triggers_exactly_one_sync(self) -> None:
        calls = {'count': 0}

        def sync_fn() -> SyncSummary:
            calls['count'] += 1
            return SyncSummary()

        # Pending event recorded "now" (t=0); the loop should wait out the
        # debounce window and then run sync exactly once, even though the
        # pending flag represents an entire burst of paths.
        self._run_loop(sync_fn=sync_fn, iterations=6, pre_events=[0.0])
        self.assertEqual(calls['count'], 1)

    def test_no_events_and_no_interval_never_syncs(self) -> None:
        calls = {'count': 0}

        def sync_fn() -> SyncSummary:
            calls['count'] += 1
            return SyncSummary()

        self._run_loop(sync_fn=sync_fn, iterations=5)
        self.assertEqual(calls['count'], 0)

    def test_interval_safety_net_triggers_without_events(self) -> None:
        calls = {'count': 0}

        def sync_fn() -> SyncSummary:
            calls['count'] += 1
            return SyncSummary()

        self._run_loop(
            sync_fn=sync_fn,
            iterations=6,
            watch_interval_seconds=1.0,
        )
        self.assertGreaterEqual(calls['count'], 1)

    def test_gate_is_open_during_sync_and_drains_after(self) -> None:
        observed: list[tuple[bool, float]] = []
        gate_holder: dict[str, SyncGate] = {}

        def sync_fn() -> SyncSummary:
            gate = gate_holder['gate']
            observed.append((gate.is_running, gate.should_ignore(0.0)))
            return SyncSummary()

        gate = SyncGate()
        gate_holder['gate'] = gate
        debouncer = WatchDebouncer(debounce_seconds=2.0)
        debouncer.record_event(0.0)
        stop_event = threading.Event()
        clock = [2.0]
        call_count = [0]

        def now_fn() -> float:
            return clock[0]

        def sleep_fn(seconds: float) -> None:
            clock[0] += seconds
            call_count[0] += 1
            if call_count[0] >= 3:
                stop_event.set()

        _watch_loop(
            gate=gate,
            debouncer=debouncer,
            stop_event=stop_event,
            sync_fn=sync_fn,
            watch_interval_seconds=0.0,
            poll_interval=0.5,
            drain_seconds=2.0,
            retry_base_delay=1.0,
            retry_max_delay=8.0,
            logger=_LOGGER,
            now_fn=now_fn,
            sleep_fn=sleep_fn,
        )
        self.assertEqual(observed, [(True, True)])
        # After the loop, the gate must no longer be running.
        self.assertFalse(gate.is_running)

    def test_consecutive_failures_back_off_but_do_not_raise(self) -> None:
        def always_fails() -> SyncSummary:
            raise RuntimeError('boom')

        # The pending event is already "stale" (recorded well before t=0), so
        # the very first loop iteration runs sync_fn immediately. Should not
        # raise even though sync_fn always fails, and must back off after.
        calls = self._run_loop(sync_fn=always_fails, iterations=4, pre_events=[-10.0])
        self.assertGreaterEqual(len(calls), 1)
        # The first sleep after a failure is the backoff delay, not the
        # regular poll interval.
        self.assertEqual(calls[0], 1.0)

    def test_success_resets_failure_counter(self) -> None:
        """A failed sync backs off; the next sync (via the interval safety
        net) succeeds and must not be treated as another failure."""
        attempts = {'count': 0}

        def flaky() -> SyncSummary:
            attempts['count'] += 1
            if attempts['count'] == 1:
                raise RuntimeError('transient')
            return SyncSummary()

        gate = SyncGate()
        debouncer = WatchDebouncer(debounce_seconds=1.0)
        debouncer.record_event(0.0)
        stop_event = threading.Event()
        clock = [1.0]
        call_count = [0]

        def now_fn() -> float:
            return clock[0]

        def sleep_fn(seconds: float) -> None:
            clock[0] += seconds
            call_count[0] += 1
            if call_count[0] >= 2:
                stop_event.set()

        _watch_loop(
            gate=gate,
            debouncer=debouncer,
            stop_event=stop_event,
            sync_fn=flaky,
            watch_interval_seconds=1.0,
            poll_interval=1.0,
            drain_seconds=0.0,
            retry_base_delay=1.0,
            retry_max_delay=8.0,
            logger=_LOGGER,
            now_fn=now_fn,
            sleep_fn=sleep_fn,
        )
        self.assertEqual(attempts['count'], 2)
