package watch

import (
	"sync"
	"time"
)

// Gate tracks "a sync is running" plus a post-sync drain window, so a
// sync's own writes never re-trigger another sync. See the package doc
// comment for the full rationale. Safe for concurrent use. Mirrors
// obsidian_sync.sync_agent.watch.SyncGate.
type Gate struct {
	mu         sync.Mutex
	running    bool
	quietUntil time.Time
}

// Begin marks a sync as running; ShouldIgnore reports true until End is
// called.
func (g *Gate) Begin() {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.running = true
}

// End marks the sync as finished and opens a drain window of length drain
// (clamped to zero or more) starting at now, during which ShouldIgnore
// keeps reporting true.
func (g *Gate) End(now time.Time, drain time.Duration) {
	g.mu.Lock()
	defer g.mu.Unlock()
	g.running = false
	if drain < 0 {
		drain = 0
	}
	g.quietUntil = now.Add(drain)
}

// ShouldIgnore reports whether an event arriving at now should be dropped:
// either a sync is currently running, or now is still inside the post-sync
// drain window.
func (g *Gate) ShouldIgnore(now time.Time) bool {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.running || now.Before(g.quietUntil)
}

// IsRunning reports whether a sync is currently in progress.
func (g *Gate) IsRunning() bool {
	g.mu.Lock()
	defer g.mu.Unlock()
	return g.running
}
