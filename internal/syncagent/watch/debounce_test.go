package watch

import (
	"testing"
	"time"
)

func at(seconds float64) time.Time {
	return time.Unix(0, 0).Add(time.Duration(seconds * float64(time.Second)))
}

func TestDebouncer_noRunBeforeAnyEvent(t *testing.T) {
	// Given
	debouncer := NewDebouncer(2 * time.Second)

	// Then
	if debouncer.ShouldRun(at(100)) {
		t.Fatal("ShouldRun() = true before any event")
	}
}

func TestDebouncer_noRunWhileEventsKeepArriving(t *testing.T) {
	// Given
	debouncer := NewDebouncer(2 * time.Second)

	// A burst of events, each within the quiet window of the previous one.
	for _, ts := range []float64{0.0, 0.5, 1.0, 1.5, 1.9} {
		debouncer.RecordEvent(at(ts))
		if debouncer.ShouldRun(at(ts + 0.1)) {
			t.Fatalf("ShouldRun() = true mid-burst at t=%v", ts)
		}
	}
}

func TestDebouncer_runsOnceBurstGoesQuiet(t *testing.T) {
	// Given
	debouncer := NewDebouncer(2 * time.Second)
	debouncer.RecordEvent(at(0.0))
	debouncer.RecordEvent(at(1.0))
	debouncer.RecordEvent(at(1.9))

	// Then
	if debouncer.ShouldRun(at(3.5)) {
		t.Fatal("ShouldRun() = true before debounce interval elapsed")
	}
	if !debouncer.ShouldRun(at(3.9)) {
		t.Fatal("ShouldRun() = false after debounce interval elapsed")
	}
}

func TestDebouncer_resetClearsPendingState(t *testing.T) {
	// Given
	debouncer := NewDebouncer(2 * time.Second)
	debouncer.RecordEvent(at(0.0))
	if !debouncer.ShouldRun(at(5.0)) {
		t.Fatal("ShouldRun() = false, want true before reset")
	}

	// When
	debouncer.Reset()

	// Then
	if debouncer.ShouldRun(at(5.0)) {
		t.Fatal("ShouldRun() = true after Reset()")
	}
	if debouncer.HasPending() {
		t.Fatal("HasPending() = true after Reset()")
	}
}

func TestDebouncer_newEventAfterResetStartsFreshWindow(t *testing.T) {
	// Given
	debouncer := NewDebouncer(2 * time.Second)
	debouncer.RecordEvent(at(0.0))
	debouncer.Reset()

	// When
	debouncer.RecordEvent(at(10.0))

	// Then
	if debouncer.ShouldRun(at(11.0)) {
		t.Fatal("ShouldRun() = true before new window elapsed")
	}
	if !debouncer.ShouldRun(at(12.0)) {
		t.Fatal("ShouldRun() = false after new window elapsed")
	}
}

func TestGate_ignoresNothingBeforeAnySync(t *testing.T) {
	// Given
	gate := &Gate{}

	// Then
	if gate.ShouldIgnore(at(0.0)) {
		t.Fatal("ShouldIgnore() = true before any sync")
	}
}

func TestGate_ignoresEventsWhileRunning(t *testing.T) {
	// Given
	gate := &Gate{}

	// When
	gate.Begin()

	// Then
	if !gate.IsRunning() {
		t.Fatal("IsRunning() = false after Begin()")
	}
	if !gate.ShouldIgnore(at(0.0)) {
		t.Fatal("ShouldIgnore() = false while running")
	}
	if !gate.ShouldIgnore(at(1000.0)) {
		t.Fatal("ShouldIgnore() = false while running, far in the future")
	}
}

func TestGate_drainsEventsForWindowAfterSyncEnds(t *testing.T) {
	// Given
	gate := &Gate{}
	gate.Begin()

	// When
	gate.End(at(10.0), 2*time.Second)

	// Then
	if gate.IsRunning() {
		t.Fatal("IsRunning() = true after End()")
	}
	if !gate.ShouldIgnore(at(10.0)) {
		t.Fatal("ShouldIgnore() = false at drain start")
	}
	if !gate.ShouldIgnore(at(11.9)) {
		t.Fatal("ShouldIgnore() = false still inside drain window")
	}
	if gate.ShouldIgnore(at(12.0)) {
		t.Fatal("ShouldIgnore() = true after drain window closed")
	}
}

func TestGate_zeroDrainReopensImmediately(t *testing.T) {
	// Given
	gate := &Gate{}
	gate.Begin()

	// When
	gate.End(at(10.0), 0)

	// Then
	if gate.ShouldIgnore(at(10.0)) {
		t.Fatal("ShouldIgnore() = true with zero drain")
	}
}

func TestIsRelevantPath(t *testing.T) {
	tests := []struct {
		name            string
		relPath         string
		isDirectory     bool
		syncAttachments bool
		want            bool
	}{
		{"markdown file", "Notes/a.md", false, false, true},
		{"attachment disabled", "Notes/img.png", false, false, false},
		{"attachment enabled", "Notes/img.png", false, true, true},
		{"conflict file", "Notes/a.conflict.dev.20260707-000000.md", false, false, false},
		{"state directory file", ".obsidian-sync-agent/manifest.json", false, false, false},
		{"hidden directory file", ".git/HEAD", false, false, false},
		{"plain directory", "Notes", true, false, true},
		{"ignored directory", ".obsidian-sync-agent", true, false, false},
		{"root event", "", true, false, false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := IsRelevantPath(tt.relPath, tt.isDirectory, tt.syncAttachments)
			if got != tt.want {
				t.Fatalf("IsRelevantPath(%q, dir=%v, attachments=%v) = %v, want %v", tt.relPath, tt.isDirectory, tt.syncAttachments, got, tt.want)
			}
		})
	}
}
