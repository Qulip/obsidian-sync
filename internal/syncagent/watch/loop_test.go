package watch

import (
	"context"
	"errors"
	"io"
	"log"
	"testing"
	"time"
)

// discardLogger swallows Loop's diagnostic output so test runs stay quiet.
func discardLogger() *log.Logger {
	return log.New(io.Discard, "", 0)
}

// runLoop drives Loop with a fake clock: preEvents seed the debouncer at
// the given offsets (seconds from epoch), and the fake Wait implementation
// advances the clock by the requested duration on every call, cancelling
// the context once `iterations` calls have been made. Mirrors the
// `_run_loop` test helper in obsidian_sync.sync_agent.watch's test suite.
func runLoop(t *testing.T, cfg LoopConfig, sync SyncFunc, iterations int, preEvents []float64) []time.Duration {
	t.Helper()
	gate := &Gate{}
	debouncer := NewDebouncer(cfg.DebounceInterval)
	for _, ts := range preEvents {
		debouncer.RecordEvent(at(ts))
	}

	clock := at(0)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	var waitCalls []time.Duration
	count := 0
	waitFn := func(waitCtx context.Context, d time.Duration) bool {
		waitCalls = append(waitCalls, d)
		clock = clock.Add(d)
		count++
		if count >= iterations {
			cancel()
		}
		return waitCtx.Err() != nil
	}

	Loop(ctx, gate, debouncer, cfg, sync, Deps{
		Now:    func() time.Time { return clock },
		Wait:   waitFn,
		Logger: discardLogger(),
	})
	return waitCalls
}

func TestLoop_burstOfEventsTriggersExactlyOneSync(t *testing.T) {
	// Given: a single pending event recorded at t=0; the loop should wait
	// out the debounce window and run sync exactly once, even though the
	// pending flag represents an entire burst of paths.
	count := 0
	sync := func(context.Context) (SyncOutcome, error) {
		count++
		return SyncOutcome{}, nil
	}
	cfg := LoopConfig{DebounceInterval: 2 * time.Second, PollInterval: 500 * time.Millisecond, DrainInterval: 2 * time.Second}

	// When
	runLoop(t, cfg, sync, 6, []float64{0.0})

	// Then
	if count != 1 {
		t.Fatalf("sync called %d times, want 1", count)
	}
}

func TestLoop_noEventsAndNoIntervalNeverSyncs(t *testing.T) {
	// Given
	count := 0
	sync := func(context.Context) (SyncOutcome, error) {
		count++
		return SyncOutcome{}, nil
	}
	cfg := LoopConfig{DebounceInterval: 2 * time.Second, PollInterval: 500 * time.Millisecond, DrainInterval: 2 * time.Second}

	// When
	runLoop(t, cfg, sync, 5, nil)

	// Then
	if count != 0 {
		t.Fatalf("sync called %d times, want 0", count)
	}
}

func TestLoop_intervalSafetyNetTriggersWithoutEvents(t *testing.T) {
	// Given
	count := 0
	sync := func(context.Context) (SyncOutcome, error) {
		count++
		return SyncOutcome{}, nil
	}
	cfg := LoopConfig{
		DebounceInterval:  2 * time.Second,
		SafetyNetInterval: 1 * time.Second,
		PollInterval:      500 * time.Millisecond,
		DrainInterval:     2 * time.Second,
	}

	// When
	runLoop(t, cfg, sync, 6, nil)

	// Then
	if count < 1 {
		t.Fatalf("sync called %d times, want >= 1", count)
	}
}

func TestLoop_gateIsOpenDuringSyncAndDrainsAfter(t *testing.T) {
	// Given
	type observation struct {
		running      bool
		shouldIgnore bool
	}
	var observed []observation
	gate := &Gate{}
	debouncer := NewDebouncer(2 * time.Second)
	debouncer.RecordEvent(at(0.0))

	sync := func(context.Context) (SyncOutcome, error) {
		observed = append(observed, observation{
			running:      gate.IsRunning(),
			shouldIgnore: gate.ShouldIgnore(at(0.0)),
		})
		return SyncOutcome{}, nil
	}

	clock := at(2.0)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	count := 0
	waitFn := func(waitCtx context.Context, d time.Duration) bool {
		clock = clock.Add(d)
		count++
		if count >= 3 {
			cancel()
		}
		return waitCtx.Err() != nil
	}

	// When
	Loop(ctx, gate, debouncer, LoopConfig{
		DebounceInterval: 2 * time.Second,
		PollInterval:     500 * time.Millisecond,
		DrainInterval:    2 * time.Second,
	}, sync, Deps{
		Now:    func() time.Time { return clock },
		Wait:   waitFn,
		Logger: discardLogger(),
	})

	// Then
	if len(observed) != 1 {
		t.Fatalf("observed = %#v, want exactly one sync call", observed)
	}
	if !observed[0].running || !observed[0].shouldIgnore {
		t.Fatalf("observed[0] = %#v, want gate running and ignoring during sync", observed[0])
	}
	if gate.IsRunning() {
		t.Fatal("IsRunning() = true after the loop stopped")
	}
}

func TestLoop_consecutiveFailuresBackOffButDoNotPanic(t *testing.T) {
	// Given: the pending event is already "stale" (recorded well before
	// t=0), so the very first loop iteration runs sync immediately. Sync
	// always fails; the loop must not panic and must back off afterward.
	sync := func(context.Context) (SyncOutcome, error) {
		return SyncOutcome{}, errors.New("boom")
	}
	cfg := LoopConfig{
		DebounceInterval: 2 * time.Second,
		PollInterval:     500 * time.Millisecond,
		DrainInterval:    2 * time.Second,
		RetryBaseDelay:   1 * time.Second,
		RetryMaxDelay:    8 * time.Second,
	}

	// When
	calls := runLoop(t, cfg, sync, 4, []float64{-10.0})

	// Then
	if len(calls) == 0 {
		t.Fatal("no waits recorded")
	}
	if calls[0] != 1*time.Second {
		t.Fatalf("first wait = %v, want 1s (the backoff delay, not the poll interval)", calls[0])
	}
}

func TestLoop_successResetsFailureCounter(t *testing.T) {
	// Given: a failed sync backs off; the next sync (via the interval
	// safety net) succeeds and must not be treated as another failure.
	attempts := 0
	sync := func(context.Context) (SyncOutcome, error) {
		attempts++
		if attempts == 1 {
			return SyncOutcome{}, errors.New("transient")
		}
		return SyncOutcome{}, nil
	}

	gate := &Gate{}
	debouncer := NewDebouncer(1 * time.Second)
	debouncer.RecordEvent(at(0.0))

	clock := at(1.0)
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	count := 0
	waitFn := func(waitCtx context.Context, d time.Duration) bool {
		clock = clock.Add(d)
		count++
		if count >= 2 {
			cancel()
		}
		return waitCtx.Err() != nil
	}

	// When
	Loop(ctx, gate, debouncer, LoopConfig{
		DebounceInterval:  1 * time.Second,
		SafetyNetInterval: 1 * time.Second,
		PollInterval:      1 * time.Second,
		DrainInterval:     0,
		RetryBaseDelay:    1 * time.Second,
		RetryMaxDelay:     8 * time.Second,
	}, sync, Deps{
		Now:    func() time.Time { return clock },
		Wait:   waitFn,
		Logger: discardLogger(),
	})

	// Then
	if attempts != 2 {
		t.Fatalf("attempts = %d, want 2", attempts)
	}
}

func TestLoop_stopsOnContextCancellation(t *testing.T) {
	// Given: no events, no safety net -- the loop only has a way out via
	// ctx cancellation. This is the graceful-shutdown contract Run() relies
	// on when converting SIGINT/SIGTERM to context cancellation.
	syncCalls := 0
	sync := func(context.Context) (SyncOutcome, error) {
		syncCalls++
		return SyncOutcome{}, nil
	}
	gate := &Gate{}
	debouncer := NewDebouncer(2 * time.Second)
	ctx, cancel := context.WithCancel(context.Background())

	waitCount := 0
	done := make(chan struct{})
	waitFn := func(waitCtx context.Context, d time.Duration) bool {
		waitCount++
		if waitCount == 2 {
			cancel()
		}
		select {
		case <-waitCtx.Done():
			return true
		default:
			return false
		}
	}

	go func() {
		Loop(ctx, gate, debouncer, LoopConfig{
			DebounceInterval: 2 * time.Second,
			PollInterval:     time.Millisecond,
			DrainInterval:    2 * time.Second,
		}, sync, Deps{
			Now:    time.Now,
			Wait:   waitFn,
			Logger: discardLogger(),
		})
		close(done)
	}()

	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("Loop did not return after context cancellation")
	}
	if syncCalls != 0 {
		t.Fatalf("sync called %d times, want 0 (no events, no safety net)", syncCalls)
	}
}

func TestBackoffDelay(t *testing.T) {
	tests := []struct {
		name     string
		failures int
		base     time.Duration
		max      time.Duration
		want     time.Duration
	}{
		{"first failure", 1, time.Second, 30 * time.Second, time.Second},
		{"second failure doubles", 2, time.Second, 30 * time.Second, 2 * time.Second},
		{"third failure quadruples", 3, time.Second, 30 * time.Second, 4 * time.Second},
		{"caps at max", 10, time.Second, 30 * time.Second, 30 * time.Second},
		{"pathologically long failure streak does not overflow", 100000, time.Second, 30 * time.Second, 30 * time.Second},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := backoffDelay(tt.failures, tt.base, tt.max)
			if got != tt.want {
				t.Fatalf("backoffDelay(%d, %v, %v) = %v, want %v", tt.failures, tt.base, tt.max, got, tt.want)
			}
		})
	}
}
