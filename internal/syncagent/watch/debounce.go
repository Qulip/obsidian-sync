package watch

import (
	"sync"
	"time"
)

// Debouncer collapses a burst of filesystem events into a single sync
// trigger. Every recorded event pushes the "quiet since" clock forward;
// ShouldRun only returns true once the debounce interval has elapsed with
// no new events, so a large paste/rename burst converges to exactly one
// sync. Safe for concurrent use: the fsnotify event listener and the sync
// loop run on separate goroutines. Mirrors
// obsidian_sync.sync_agent.watch.WatchDebouncer.
type Debouncer struct {
	interval time.Duration

	mu          sync.Mutex
	pending     bool
	lastEventAt time.Time
}

// NewDebouncer returns a Debouncer that waits interval after the last event
// before ShouldRun reports true. interval must be greater than zero.
func NewDebouncer(interval time.Duration) *Debouncer {
	return &Debouncer{interval: interval}
}

// RecordEvent marks a pending sync trigger and resets the quiet-period
// clock to now.
func (d *Debouncer) RecordEvent(now time.Time) {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.pending = true
	d.lastEventAt = now
}

// ShouldRun reports whether a sync is due: there is a pending event and the
// debounce interval has elapsed since the last one.
func (d *Debouncer) ShouldRun(now time.Time) bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	if !d.pending {
		return false
	}
	return now.Sub(d.lastEventAt) >= d.interval
}

// Reset clears the pending state after a sync has been triggered.
func (d *Debouncer) Reset() {
	d.mu.Lock()
	defer d.mu.Unlock()
	d.pending = false
}

// HasPending reports whether an event is currently pending.
func (d *Debouncer) HasPending() bool {
	d.mu.Lock()
	defer d.mu.Unlock()
	return d.pending
}
