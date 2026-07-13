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
			if stderr.Len() != 0 {
				t.Fatalf("stderr = %q", stderr.String())
			}
		})
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
