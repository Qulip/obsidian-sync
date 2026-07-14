package watch

import (
	"context"
	"fmt"
	"log"
	"time"
)

// Default backoff bounds for retrying a failed sync cycle inside Run. The
// Python agent reuses its configurable HTTP retry_base_delay/
// retry_max_delay for this; the Go client's HTTP-level retry delay is a
// fixed 100ms (see client.retryDelay) with no equivalent user-facing knob,
// so Run's between-cycle backoff uses its own fixed defaults instead of
// adding new config surface for a single caller. The values match the
// Python agent's retry_base_delay/retry_max_delay defaults.
const (
	DefaultRetryBaseDelay = 1 * time.Second
	DefaultRetryMaxDelay  = 30 * time.Second

	defaultPollInterval = 500 * time.Millisecond
	minPollInterval     = 100 * time.Millisecond
)

// RunOptions configures Run.
type RunOptions struct {
	// VaultRoot is the absolute local vault directory to watch.
	VaultRoot string
	// SyncAttachments mirrors config.AgentConfig.SyncAttachments; it must
	// match the config passed to Sync so watch filtering and the sync
	// engine agree on which paths are relevant.
	SyncAttachments bool
	// DebounceInterval is the quiet period after the last change before a
	// sync runs. Must be greater than zero.
	DebounceInterval time.Duration
	// SafetyNetInterval, if greater than zero, forces a sync at least this
	// often even without filesystem events.
	SafetyNetInterval time.Duration
	// RetryBaseDelay/RetryMaxDelay bound the exponential backoff applied
	// between failed sync cycles. Zero values fall back to
	// DefaultRetryBaseDelay/DefaultRetryMaxDelay.
	RetryBaseDelay time.Duration
	RetryMaxDelay  time.Duration
	// Logger receives watch diagnostics. Defaults to log.Default().
	Logger *log.Logger
	// Sync runs one sync cycle. Required.
	Sync SyncFunc
}

// Run watches opts.VaultRoot for filesystem changes and runs opts.Sync
// after each debounced burst (plus, if enabled, every SafetyNetInterval as
// a periodic backstop). It blocks until ctx is cancelled, then stops
// watching and returns nil — Run itself never returns a non-nil error for
// sync failures, only for setup failures (e.g. the vault root cannot be
// watched). Mirrors obsidian_sync.sync_agent.watch.run_watch, minus signal
// handling: callers convert OS signals to ctx cancellation (e.g. via
// signal.NotifyContext) before calling Run, so shutdown is expressed
// uniformly through context cancellation rather than a second in-package
// signal path.
func Run(ctx context.Context, opts RunOptions) error {
	if opts.Sync == nil {
		return fmt.Errorf("watch: Sync is required")
	}
	if opts.DebounceInterval <= 0 {
		return fmt.Errorf("watch: DebounceInterval must be greater than zero")
	}

	logger := opts.Logger
	if logger == nil {
		logger = log.Default()
	}

	rw, err := newRecursiveWatcher(opts.VaultRoot)
	if err != nil {
		return fmt.Errorf("watch vault: %w", err)
	}
	defer func() {
		_ = rw.Close()
	}()

	gate := &Gate{}
	debouncer := NewDebouncer(opts.DebounceInterval)

	retryBaseDelay := opts.RetryBaseDelay
	if retryBaseDelay <= 0 {
		retryBaseDelay = DefaultRetryBaseDelay
	}
	retryMaxDelay := opts.RetryMaxDelay
	if retryMaxDelay <= 0 {
		retryMaxDelay = DefaultRetryMaxDelay
	}

	pollInterval := opts.DebounceInterval / 4
	if pollInterval > defaultPollInterval {
		pollInterval = defaultPollInterval
	}
	if pollInterval < minPollInterval {
		pollInterval = minPollInterval
	}

	logger.Printf(
		"watch: watching %s (debounce=%s, interval=%s)",
		opts.VaultRoot, opts.DebounceInterval, opts.SafetyNetInterval,
	)

	eventsDone := make(chan struct{})
	go func() {
		defer close(eventsDone)
		consumeEvents(ctx, rw, gate, debouncer, opts.SyncAttachments, logger)
	}()

	Loop(ctx, gate, debouncer, LoopConfig{
		DebounceInterval:  opts.DebounceInterval,
		SafetyNetInterval: opts.SafetyNetInterval,
		DrainInterval:     opts.DebounceInterval,
		PollInterval:      pollInterval,
		RetryBaseDelay:    retryBaseDelay,
		RetryMaxDelay:     retryMaxDelay,
	}, opts.Sync, Deps{Logger: logger})

	<-eventsDone
	logger.Println("watch: stopped cleanly")
	return nil
}

func consumeEvents(ctx context.Context, rw *recursiveWatcher, gate *Gate, debouncer *Debouncer, syncAttachments bool, logger *log.Logger) {
	for {
		select {
		case <-ctx.Done():
			return
		case event, ok := <-rw.watcher.Events:
			if !ok {
				return
			}
			rw.handleEvent(event, gate, debouncer, syncAttachments, time.Now(), logger)
		case watchErr, ok := <-rw.watcher.Errors:
			if !ok {
				return
			}
			if watchErr != nil {
				logger.Printf("watch: fsnotify error: %v", watchErr)
			}
		}
	}
}
