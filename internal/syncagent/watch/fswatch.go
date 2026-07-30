package watch

import (
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"
	"time"

	"github.com/fsnotify/fsnotify"

	"github.com/Qulip/obsidian-sync/internal/syncagent/rules"
)

// recursiveWatcher wraps fsnotify.Watcher to recursively watch a directory
// tree, adding a watch for every newly created subdirectory as it appears.
// fsnotify does not watch recursively on any platform; this mirrors what
// watchdog's OS-native recursive observers (FSEvents/inotify) do
// automatically for the Python agent.
type recursiveWatcher struct {
	watcher *fsnotify.Watcher
	root    string
	dirs    map[string]struct{}
}

func newRecursiveWatcher(root string) (*recursiveWatcher, error) {
	// Checked explicitly (rather than left to addTree's WalkDir) because
	// addTree tolerates a missing directory for nested calls -- see its
	// comment -- and that tolerance must not extend to the vault root
	// itself: a missing root has to surface as a setup error, not silently
	// produce a watcher with zero watches that then blocks forever.
	info, err := os.Stat(root)
	if err != nil {
		return nil, fmt.Errorf("stat vault root: %w", err)
	}
	if !info.IsDir() {
		return nil, fmt.Errorf("vault root is not a directory: %s", root)
	}

	fsWatcher, err := fsnotify.NewWatcher()
	if err != nil {
		return nil, err
	}
	rw := &recursiveWatcher{watcher: fsWatcher, root: root, dirs: map[string]struct{}{}}
	if err := rw.addTree(root); err != nil {
		_ = fsWatcher.Close()
		return nil, err
	}
	return rw, nil
}

// addTree walks dir and adds a watch for every subdirectory not excluded by
// rules.IsIgnoredDir (hidden dirs, .obsidian-sync-agent/, .trash/, ...). If
// dir itself is ignored, nothing is watched.
func (rw *recursiveWatcher) addTree(dir string) error {
	return filepath.WalkDir(dir, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			// A directory may have been removed between the fsnotify Create
			// event and this walk (e.g. a very short-lived temp dir); skip
			// it rather than failing the whole watch. newRecursiveWatcher
			// checks the vault root's existence separately, so this only
			// ever applies to nested addTree calls triggered by a Create
			// event.
			if os.IsNotExist(walkErr) {
				return nil
			}
			return walkErr
		}
		if !entry.IsDir() {
			return nil
		}
		if path != rw.root && rules.IsIgnoredDir(entry.Name()) {
			return filepath.SkipDir
		}
		if err := rw.watcher.Add(path); err != nil {
			return err
		}
		rw.dirs[path] = struct{}{}
		return nil
	})
}

func (rw *recursiveWatcher) isTrackedDir(path string) bool {
	_, ok := rw.dirs[path]
	return ok
}

func (rw *recursiveWatcher) forget(path string) {
	delete(rw.dirs, path)
}

func (rw *recursiveWatcher) Close() error {
	return rw.watcher.Close()
}

// relativePath converts an absolute event path to a vault-relative,
// slash-separated path. It returns ok=false for the vault root itself and
// for any path outside the vault (which should not normally occur, but
// fsnotify event paths are OS-reported strings and are treated as
// untrusted input here).
func (rw *recursiveWatcher) relativePath(absPath string) (string, bool) {
	rel, err := filepath.Rel(rw.root, absPath)
	if err != nil {
		return "", false
	}
	relSlash := filepath.ToSlash(rel)
	if relSlash == "." || relSlash == ".." || strings.HasPrefix(relSlash, "../") {
		return "", false
	}
	return relSlash, true
}

// handleEvent converts a raw fsnotify event into a vault-relative path,
// grows the recursive watch tree on new directories, and — subject to the
// gate — records a debounce event when the path is relevant.
func (rw *recursiveWatcher) handleEvent(event fsnotify.Event, gate *Gate, debouncer *Debouncer, syncAttachments bool, now time.Time, logger *log.Logger) {
	relPath, ok := rw.relativePath(event.Name)
	if !ok {
		return
	}

	wasTrackedDir := rw.isTrackedDir(event.Name)
	isDir := wasTrackedDir
	if !isDir {
		if info, statErr := os.Stat(event.Name); statErr == nil && info.IsDir() {
			isDir = true
		}
	}

	if event.Op&(fsnotify.Remove|fsnotify.Rename) != 0 && wasTrackedDir {
		_ = rw.watcher.Remove(event.Name)
		rw.forget(event.Name)
	}

	if event.Op&fsnotify.Create != 0 && isDir {
		// Also picks up any files already inside the new directory (e.g. a
		// directory created and populated in one burst, such as a git
		// checkout), closing the race between the Create event and the
		// watcher.Add call above.
		if err := rw.addTree(event.Name); err != nil {
			logger.Printf("watch: failed to add watch for new directory %s: %v", relPath, err)
		}
	}

	// Per-event tracing (which path was ignored by the gate, which one was
	// judged relevant) is deliberately not logged here: the Python agent
	// only emits it at DEBUG level, and the Go CLI's --verbose flag does not
	// yet gate log verbosity anywhere in this codebase (see cmd/main.go),
	// so logging it unconditionally would be noisy compared to Python's
	// default (non-verbose) output.
	if gate.ShouldIgnore(now) {
		return
	}
	if !IsRelevantPath(relPath, isDir, syncAttachments) {
		return
	}
	debouncer.RecordEvent(now)
}
