package main

import (
	"bytes"
	"strings"
	"testing"
)

func TestRun_returnsError_whenNoCommandSupplied(t *testing.T) {
	// Given
	clearCommandEnv(t)
	var stdout bytes.Buffer
	var stderr bytes.Buffer

	// When
	code := run([]string{}, &stdout, &stderr)

	// Then
	if code != exitError {
		t.Fatalf("exit code = %d, want %d", code, exitError)
	}
	if !strings.Contains(stderr.String(), "the following arguments are required: command") {
		t.Fatalf("stderr = %q", stderr.String())
	}
}

func TestRun_returnsOK_whenRootHelpRequested(t *testing.T) {
	// Given
	tests := [][]string{{"--help"}, {"-h"}}
	for _, args := range tests {
		t.Run(args[0], func(t *testing.T) {
			clearCommandEnv(t)
			var stdout bytes.Buffer
			var stderr bytes.Buffer

			// When
			code := run(args, &stdout, &stderr)

			// Then
			if code != exitOK {
				t.Fatalf("exit code = %d, want %d", code, exitOK)
			}
			if !strings.Contains(stdout.String(), "Usage:") {
				t.Fatalf("stdout = %q", stdout.String())
			}
			if !strings.Contains(stdout.String(), "obsisync") {
				t.Fatalf("stdout = %q", stdout.String())
			}
			if !strings.Contains(stdout.String(), "--version") {
				t.Fatalf("stdout = %q, want a --version usage line", stdout.String())
			}
			if !strings.Contains(stdout.String(), "version") {
				t.Fatalf("stdout = %q, want version listed in commands", stdout.String())
			}
			if stderr.Len() != 0 {
				t.Fatalf("stderr = %q", stderr.String())
			}
		})
	}
}

func TestRun_printsVersion_whenVersionFlagOrCommandRequested(t *testing.T) {
	// Given
	tests := [][]string{{"--version"}, {"-v"}, {"version"}}
	for _, args := range tests {
		t.Run(args[0], func(t *testing.T) {
			clearCommandEnv(t)
			var stdout bytes.Buffer
			var stderr bytes.Buffer

			// When
			code := run(args, &stdout, &stderr)

			// Then
			if code != exitOK {
				t.Fatalf("exit code = %d, want %d, stderr = %q", code, exitOK, stderr.String())
			}
			if !strings.Contains(stdout.String(), "obsisync "+version) {
				t.Fatalf("stdout = %q, want it to contain %q", stdout.String(), "obsisync "+version)
			}
			if stderr.Len() != 0 {
				t.Fatalf("stderr = %q", stderr.String())
			}
		})
	}
}

func TestRun_rejectsExtraArgument_whenVersionCommandGivenOne(t *testing.T) {
	// Given
	clearCommandEnv(t)
	var stdout bytes.Buffer
	var stderr bytes.Buffer

	// When
	code := run([]string{"version", "extra"}, &stdout, &stderr)

	// Then
	if code != exitError {
		t.Fatalf("exit code = %d, want %d, stderr = %q", code, exitError, stderr.String())
	}
	if stdout.Len() != 0 {
		t.Fatalf("stdout = %q", stdout.String())
	}
	if !strings.Contains(stderr.String(), "unexpected argument") {
		t.Fatalf("stderr = %q", stderr.String())
	}
}

func TestRun_printsDoubleDashHelp_whenCommandHelpRequested(t *testing.T) {
	// Given
	tests := []struct {
		name      string
		args      []string
		wantFlags []string
	}{
		{
			name: "sync",
			args: []string{"sync", "--help"},
			wantFlags: []string{
				"--vault-root",
				"--vault-id",
				"--server",
				"--device-id",
				"--verbose",
				"--dry-run",
				"--require-obsidian-refresh",
				"--sync-attachments",
				"--no-sync-attachments",
				"--attachment-max-bytes",
				"--conflict-policy",
			},
		},
		{
			name: "status",
			args: []string{"status", "--help"},
			wantFlags: []string{
				"--vault-root",
				"--vault-id",
				"--server",
				"--device-id",
				"--verbose",
			},
		},
		{
			name: "watch",
			args: []string{"watch", "--help"},
			wantFlags: []string{
				"--vault-root",
				"--vault-id",
				"--server",
				"--device-id",
				"--verbose",
				"--require-obsidian-refresh",
				"--sync-attachments",
				"--no-sync-attachments",
				"--attachment-max-bytes",
				"--conflict-policy",
				"--watch-debounce-seconds",
				"--watch-interval-seconds",
			},
		},
		{
			name: "update",
			args: []string{"update", "--help"},
			wantFlags: []string{
				"Check for and install the latest obsisync release.",
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			clearCommandEnv(t)
			var stdout bytes.Buffer
			var stderr bytes.Buffer

			// When
			code := run(tt.args, &stdout, &stderr)

			// Then
			if code != exitOK {
				t.Fatalf("exit code = %d, want %d", code, exitOK)
			}
			help := stdout.String()
			for _, flag := range tt.wantFlags {
				if !strings.Contains(help, flag) {
					t.Fatalf("help for %s missing %s:\n%s", tt.name, flag, help)
				}
			}
			if stderr.Len() != 0 {
				t.Fatalf("stderr = %q", stderr.String())
			}
		})
	}
}
