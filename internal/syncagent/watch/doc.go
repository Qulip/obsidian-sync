// Package watch implements the `obsisync watch` foreground daemon: it
// recursively watches the vault for filesystem changes with fsnotify,
// debounces bursts of events into a single sync trigger, and runs
// engine.RunSync after each debounced burst (plus, optionally, on a
// periodic safety-net interval).
//
// This is a Go port of obsidian_sync.sync_agent.watch. See that module's
// docstring for the self-loop-prevention design (a sync's own writes —
// pull-applied files, conflict/backup files — must not re-trigger another
// sync). The same two guards are used here:
//
//  1. Gate.Begin() is called immediately before a sync runs and makes
//     Gate.ShouldIgnore report true for the whole call, so any event
//     delivered while a sync is running (including the sync's own writes)
//     is dropped before it reaches the debouncer.
//  2. Because filesystem watchers can deliver an event with real latency —
//     sometimes after the write that caused it has already completed —
//     Gate.End() opens a short drain window (reusing the debounce interval,
//     for the same reasoning as the Python agent) during which
//     Gate.ShouldIgnore keeps returning true.
//
// Most of a sync's own writes never reach this guard in the first place:
// the manifest lives under .obsidian-sync-agent/, and conflict files match
// rules.IsConflictFile — both are already excluded by IsRelevantPath
// regardless of the gate. The gate exists for the remaining case: a pull
// writing a legitimate note/attachment path.
package watch
