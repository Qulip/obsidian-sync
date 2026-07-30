package watch

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"testing"
	"time"
)

// waitForSync blocks until a sync outcome arrives on calls or the timeout
// elapses, failing the test in the latter case.
func waitForSync(t *testing.T, calls <-chan SyncOutcome, timeout time.Duration, msg string) {
	t.Helper()
	select {
	case <-calls:
	case <-time.After(timeout):
		t.Fatal(msg)
	}
}

// assertNoSync fails the test if a sync outcome arrives on calls before the
// window elapses.
func assertNoSync(t *testing.T, calls <-chan SyncOutcome, window time.Duration, msg string) {
	t.Helper()
	select {
	case <-calls:
		t.Fatal(msg)
	case <-time.After(window):
	}
}

// startRun launches Run in the background against a real filesystem and
// real fsnotify, returning a channel fed with one SyncOutcome per sync
// call and a stop function that cancels the watch and waits for Run to
// return.
func startRun(t *testing.T, opts RunOptions) (calls chan SyncOutcome, stop func()) {
	t.Helper()
	calls = make(chan SyncOutcome, 32)
	opts.Sync = func(context.Context) (SyncOutcome, error) {
		outcome := SyncOutcome{}
		calls <- outcome
		return outcome, nil
	}
	if opts.Logger == nil {
		opts.Logger = discardLogger()
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	go func() {
		defer close(done)
		if err := Run(ctx, opts); err != nil {
			t.Errorf("Run() error = %v", err)
		}
	}()
	// Give the background goroutine time to establish the recursive
	// fsnotify watch before the test starts writing files.
	time.Sleep(150 * time.Millisecond)
	return calls, func() {
		cancel()
		select {
		case <-done:
		case <-time.After(5 * time.Second):
			t.Fatal("Run did not return after context cancellation")
		}
	}
}

func TestRun_fileChangeTriggersSyncAfterDebounce(t *testing.T) {
	// Given
	root := t.TempDir()
	calls, stop := startRun(t, RunOptions{VaultRoot: root, DebounceInterval: 50 * time.Millisecond})
	defer stop()

	// When
	if err := os.WriteFile(filepath.Join(root, "note.md"), []byte("hello"), 0o644); err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}

	// Then
	waitForSync(t, calls, 3*time.Second, "sync was not triggered after a relevant file change")
}

func TestRun_burstOfChangesCollapsesToOneSync(t *testing.T) {
	// Given
	root := t.TempDir()
	calls, stop := startRun(t, RunOptions{VaultRoot: root, DebounceInterval: 150 * time.Millisecond})
	defer stop()

	// When: a burst of writes to the same file, each well inside the
	// debounce window.
	path := filepath.Join(root, "note.md")
	for i := 0; i < 5; i++ {
		if err := os.WriteFile(path, []byte(fmt.Sprintf("v%d", i)), 0o644); err != nil {
			t.Fatalf("WriteFile() error = %v", err)
		}
		time.Sleep(20 * time.Millisecond)
	}

	// Then: exactly one sync, not five.
	waitForSync(t, calls, 3*time.Second, "sync was not triggered for the burst")
	assertNoSync(t, calls, 500*time.Millisecond, "sync was triggered a second time for a single burst")
}

func TestRun_ignoredPathDoesNotTriggerSync(t *testing.T) {
	// Given
	root := t.TempDir()
	calls, stop := startRun(t, RunOptions{VaultRoot: root, DebounceInterval: 50 * time.Millisecond})
	defer stop()

	// When: writes that the sync engine itself would produce or otherwise
	// exclude -- the manifest directory and a conflict file.
	hiddenDir := filepath.Join(root, ".obsidian-sync-agent")
	if err := os.MkdirAll(hiddenDir, 0o755); err != nil {
		t.Fatalf("MkdirAll() error = %v", err)
	}
	if err := os.WriteFile(filepath.Join(hiddenDir, "manifest.json"), []byte("{}"), 0o644); err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}
	if err := os.WriteFile(filepath.Join(root, "note.conflict.dev.20260101-000000.md"), []byte("x"), 0o644); err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}

	// Then: none of that triggers a sync.
	assertNoSync(t, calls, 400*time.Millisecond, "sync was triggered for an ignored path")

	// And: the watcher is still alive -- a real change still triggers.
	if err := os.WriteFile(filepath.Join(root, "note.md"), []byte("hello"), 0o644); err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}
	waitForSync(t, calls, 3*time.Second, "sync was not triggered after a real change following ignored ones")
}

func TestRun_watchesNewlyCreatedSubdirectory(t *testing.T) {
	// Given
	root := t.TempDir()
	calls, stop := startRun(t, RunOptions{VaultRoot: root, DebounceInterval: 50 * time.Millisecond})
	defer stop()

	// When: a new subdirectory appears after Run has started, and a file
	// is written into it shortly after.
	subdir := filepath.Join(root, "Sub")
	if err := os.Mkdir(subdir, 0o755); err != nil {
		t.Fatalf("Mkdir() error = %v", err)
	}
	time.Sleep(100 * time.Millisecond)
	if err := os.WriteFile(filepath.Join(subdir, "note.md"), []byte("hi"), 0o644); err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}

	// Then: the recursive watch picked up the new directory.
	waitForSync(t, calls, 3*time.Second, "sync was not triggered for a file in a newly created subdirectory")
}

func TestRun_stopsCleanlyOnContextCancellation(t *testing.T) {
	// Given
	root := t.TempDir()
	calls := make(chan SyncOutcome, 1)
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	var runErr error
	go func() {
		defer close(done)
		runErr = Run(ctx, RunOptions{
			VaultRoot:        root,
			DebounceInterval: 50 * time.Millisecond,
			Logger:           discardLogger(),
			Sync: func(context.Context) (SyncOutcome, error) {
				calls <- SyncOutcome{}
				return SyncOutcome{}, nil
			},
		})
	}()
	time.Sleep(150 * time.Millisecond)

	// When
	cancel()

	// Then
	select {
	case <-done:
	case <-time.After(5 * time.Second):
		t.Fatal("Run did not return after context cancellation")
	}
	if runErr != nil {
		t.Fatalf("Run() error = %v, want nil on clean shutdown", runErr)
	}
}

func TestRun_returnsErrorForMissingVaultRoot(t *testing.T) {
	// Given: Run must fail fast here rather than silently watching nothing
	// -- a regression previously made this hang forever (see
	// newRecursiveWatcher's explicit root-existence check), so this test
	// bounds its own wait instead of trusting Run() to return promptly.
	missing := filepath.Join(t.TempDir(), "does-not-exist")
	errCh := make(chan error, 1)

	// When
	go func() {
		errCh <- Run(context.Background(), RunOptions{
			VaultRoot:        missing,
			DebounceInterval: time.Second,
			Logger:           discardLogger(),
			Sync:             func(context.Context) (SyncOutcome, error) { return SyncOutcome{}, nil },
		})
	}()

	// Then
	select {
	case err := <-errCh:
		if err == nil {
			t.Fatal("Run() error = nil, want error for a vault root that does not exist")
		}
	case <-time.After(3 * time.Second):
		t.Fatal("Run() did not return for a missing vault root")
	}
}

func TestRun_requiresSyncAndPositiveDebounce(t *testing.T) {
	root := t.TempDir()
	noopSync := func(context.Context) (SyncOutcome, error) { return SyncOutcome{}, nil }

	if err := Run(context.Background(), RunOptions{VaultRoot: root, DebounceInterval: time.Second}); err == nil {
		t.Fatal("Run() error = nil, want error when Sync is nil")
	}
	if err := Run(context.Background(), RunOptions{VaultRoot: root, DebounceInterval: 0, Sync: noopSync}); err == nil {
		t.Fatal("Run() error = nil, want error when DebounceInterval <= 0")
	}
}
