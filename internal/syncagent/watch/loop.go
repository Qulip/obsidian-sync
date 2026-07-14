package watch

import (
	"context"
	"log"
	"time"
)

// SyncOutcome carries the pieces of a sync summary the watch loop logs.
type SyncOutcome struct {
	Pulled    int
	Applied   int
	Pushed    int
	Conflicts int
}

// SyncFunc runs one sync cycle. A returned error is logged and retried with
// backoff; it never stops the loop (mirrors run_sync's "watch must never
// crash" contract in the Python agent).
type SyncFunc func(ctx context.Context) (SyncOutcome, error)

// LoopConfig configures loop timing. DebounceInterval and PollInterval must
// be greater than zero. SafetyNetInterval of zero disables the periodic
// safety net.
type LoopConfig struct {
	DebounceInterval  time.Duration
	SafetyNetInterval time.Duration
	DrainInterval     time.Duration
	PollInterval      time.Duration
	RetryBaseDelay    time.Duration
	RetryMaxDelay     time.Duration
}

// Deps holds the loop's injectable dependencies. Tests provide fake Now and
// Wait implementations to drive the loop deterministically without real
// sleeping, mirroring the now_fn/sleep_fn injection in
// obsidian_sync.sync_agent.watch._watch_loop. A nil field falls back to the
// real-time default.
type Deps struct {
	// Now returns the current time. Defaults to time.Now.
	Now func() time.Time
	// Wait blocks for d or until ctx is cancelled, and reports whether ctx
	// was the reason it returned (true) as opposed to the duration elapsing
	// (false). Defaults to a real timer/ctx.Done select.
	Wait func(ctx context.Context, d time.Duration) bool
	// Logger receives loop diagnostics. Defaults to log.Default().
	Logger *log.Logger
}

func (d Deps) withDefaults() Deps {
	if d.Now == nil {
		d.Now = time.Now
	}
	if d.Wait == nil {
		d.Wait = realWait
	}
	if d.Logger == nil {
		d.Logger = log.Default()
	}
	return d
}

func realWait(ctx context.Context, d time.Duration) bool {
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return true
	case <-timer.C:
		return false
	}
}

// Loop runs sync cycles until ctx is cancelled: it fires when the debouncer
// has gone quiet, or (if enabled) when the periodic safety-net interval has
// elapsed since the last run. Sync failures are logged and retried with
// exponential backoff; they never stop the loop. Only ctx cancellation
// (SIGINT/SIGTERM converted upstream) stops Loop. Mirrors
// obsidian_sync.sync_agent.watch._watch_loop.
func Loop(ctx context.Context, gate *Gate, debouncer *Debouncer, cfg LoopConfig, sync SyncFunc, deps Deps) {
	deps = deps.withDefaults()
	consecutiveFailures := 0
	lastRun := deps.Now()

	for {
		if ctx.Err() != nil {
			return
		}

		now := deps.Now()
		dueToEvents := debouncer.ShouldRun(now)
		dueToInterval := cfg.SafetyNetInterval > 0 && now.Sub(lastRun) >= cfg.SafetyNetInterval
		if !dueToEvents && !dueToInterval {
			if deps.Wait(ctx, cfg.PollInterval) {
				return
			}
			continue
		}

		debouncer.Reset()
		gate.Begin()
		outcome, err := sync(ctx)
		finish := deps.Now()
		gate.End(finish, cfg.DrainInterval)
		lastRun = finish

		if err != nil {
			consecutiveFailures++
			deps.Logger.Printf("watch: sync failed (%d consecutive failure(s)): %v", consecutiveFailures, err)
		} else {
			consecutiveFailures = 0
			deps.Logger.Printf(
				"watch: sync ok (pulled=%d applied=%d pushed=%d conflicts=%d)",
				outcome.Pulled, outcome.Applied, outcome.Pushed, outcome.Conflicts,
			)
		}

		if consecutiveFailures > 0 {
			delay := backoffDelay(consecutiveFailures, cfg.RetryBaseDelay, cfg.RetryMaxDelay)
			deps.Logger.Printf("watch: backing off %s before retrying", delay)
			if deps.Wait(ctx, delay) {
				return
			}
		}
	}
}

// backoffDelay computes base * 2^(failures-1), capped at max. Guards
// against shift overflow for pathologically long failure streaks (e.g. a
// watch process left running for days against a downed server) by clamping
// to max once the shift would no longer change the (already-capped)
// result — the Python agent computes this in floating point, where the
// same large exponent simply saturates instead of overflowing an int64.
func backoffDelay(failures int, base, max time.Duration) time.Duration {
	if max <= 0 {
		return 0
	}
	shift := failures - 1
	if shift < 0 {
		shift = 0
	}
	if shift > 62 {
		return max
	}
	delay := base * time.Duration(int64(1)<<uint(shift))
	if delay <= 0 || delay > max {
		return max
	}
	return delay
}
