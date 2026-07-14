package main

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestRunStatus_returnsConfigError_whenServerMissing(t *testing.T) {
	// Given
	root := t.TempDir()
	clearCommandEnv(t)
	var stdout bytes.Buffer
	var stderr bytes.Buffer

	// When
	code := run([]string{"status", "--vault-root", root, "--vault-id", "vault"}, &stdout, &stderr)

	// Then
	if code != exitError {
		t.Fatalf("exit code = %d, want %d", code, exitError)
	}
	if stdout.Len() != 0 {
		t.Fatalf("stdout = %q", stdout.String())
	}
	if !strings.Contains(stderr.String(), "configuration error") {
		t.Fatalf("stderr = %q", stderr.String())
	}
	if !strings.Contains(stderr.String(), "server base url") {
		t.Fatalf("stderr = %q", stderr.String())
	}
}

func TestRunCommand_returnsErrorOnStderr_whenUnknownFlag(t *testing.T) {
	// Given
	clearCommandEnv(t)
	var stdout bytes.Buffer
	var stderr bytes.Buffer

	// When
	code := run([]string{"sync", "--not-a-real-flag"}, &stdout, &stderr)

	// Then
	if code != exitError {
		t.Fatalf("exit code = %d, want %d, stderr = %q", code, exitError, stderr.String())
	}
	if stdout.Len() != 0 {
		t.Fatalf("stdout = %q", stdout.String())
	}
	if !strings.Contains(stderr.String(), "flag provided but not defined") {
		t.Fatalf("stderr = %q", stderr.String())
	}
}

func TestRunSync_returnsConflictExit_whenEngineReportsConflict(t *testing.T) {
	// Given
	root := t.TempDir()
	clearCommandEnv(t)
	if err := os.WriteFile(filepath.Join(root, "note.md"), []byte("local edit"), 0o644); err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}
	server := syncConflictServer(t)
	defer server.Close()
	var stdout bytes.Buffer
	var stderr bytes.Buffer

	// When
	code := run([]string{
		"sync",
		"--vault-root", root,
		"--server", server.URL,
		"--vault-id", "vault",
		"--device-id", "dev",
	}, &stdout, &stderr)

	// Then
	if code != 1 {
		t.Fatalf("exit code = %d, want 1, stderr = %q", code, stderr.String())
	}
	if !strings.Contains(stderr.String(), "sync completed with 1 conflict") {
		t.Fatalf("stderr = %q", stderr.String())
	}
	conflicts, err := filepath.Glob(filepath.Join(root, "note.conflict.dev.*.md"))
	if err != nil {
		t.Fatalf("Glob() error = %v", err)
	}
	if len(conflicts) != 1 {
		t.Fatalf("conflicts = %v", conflicts)
	}
}

func TestRunSync_returnsRefreshExit_whenRequiredObsidianRefreshFails(t *testing.T) {
	// Given
	root := t.TempDir()
	clearCommandEnv(t)
	obsidianServer := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeCommandJSON(t, w, http.StatusOK, `{"authenticated": false}`)
	}))
	defer obsidianServer.Close()
	writeCommandConfigFile(t, root, map[string]any{
		"server_base_url": "http://file.example",
		"vault_id":        "file-vault",
		"device_id":       "file-device",
		"obsidian": map[string]any{
			"enabled":        true,
			"base_url":       obsidianServer.URL,
			"reload_command": true,
		},
	})
	server := emptySyncServer(t)
	defer server.Close()
	var stdout bytes.Buffer
	var stderr bytes.Buffer

	// When
	code := run([]string{
		"sync",
		"--vault-root", root,
		"--server", server.URL,
		"--vault-id", "vault",
		"--device-id", "dev",
		"--require-obsidian-refresh",
	}, &stdout, &stderr)

	// Then
	if code != 3 {
		t.Fatalf("exit code = %d, want 3, stderr = %q", code, stderr.String())
	}
	if !strings.Contains(stderr.String(), "obsidian refresh required but failed") {
		t.Fatalf("stderr = %q", stderr.String())
	}
}

func TestRunStatus_usesCLIValuesBeforeNetworkStatus(t *testing.T) {
	// Given
	root := t.TempDir()
	writeCommandConfigFile(t, root, map[string]any{
		"server_base_url": "http://file.example",
		"vault_id":        "file-vault",
		"device_id":       "file-device",
	})
	clearCommandEnv(t)
	t.Setenv("OBSIDIAN_SYNC_AGENT_SERVER", "http://env.example")
	t.Setenv("OBSIDIAN_SYNC_AGENT_VAULT_ID", "env-vault")
	t.Setenv("OBSIDIAN_SYNC_AGENT_DEVICE_ID", "env-device")

	var gotPath string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotPath = r.URL.EscapedPath()
		if r.URL.Query().Get("device_id") != "cli-device" {
			t.Fatalf("device_id query = %q", r.URL.Query().Get("device_id"))
		}
		writeCommandJSON(t, w, http.StatusOK, `{
			"success": true,
			"data": {
				"vault_id": "cli-vault",
				"device_id": "cli-device",
				"server_revision": 1,
				"device_last_seen_revision": 1,
				"pending_changes": 0,
				"open_conflicts": 0,
				"pending_vectorizing_jobs": 0
			}
		}`)
	}))
	defer server.Close()

	var stdout bytes.Buffer
	var stderr bytes.Buffer

	// When
	code := run([]string{
		"status",
		"--vault-root", root,
		"--server", server.URL,
		"--vault-id", "cli-vault",
		"--device-id", "cli device!",
	}, &stdout, &stderr)

	// Then
	if code != exitOK {
		t.Fatalf("exit code = %d, stderr = %q", code, stderr.String())
	}
	if !strings.Contains(gotPath, "cli-vault") {
		t.Fatalf("request path = %q", gotPath)
	}
}

func TestCommandOptions_overrides_syncAttachments(t *testing.T) {
	tests := []struct {
		name                string
		options             commandOptions
		wantSyncAttachments bool
		wantHasOverride     bool
	}{
		{
			name:                "neither flag set",
			options:             commandOptions{},
			wantSyncAttachments: false,
			wantHasOverride:     false,
		},
		{
			name:                "sync-attachments set",
			options:             commandOptions{syncAttachments: true},
			wantSyncAttachments: true,
			wantHasOverride:     true,
		},
		{
			name:                "no-sync-attachments set",
			options:             commandOptions{noSyncAttachments: true},
			wantSyncAttachments: false,
			wantHasOverride:     true,
		},
		{
			name:                "both set, no wins",
			options:             commandOptions{syncAttachments: true, noSyncAttachments: true},
			wantSyncAttachments: false,
			wantHasOverride:     true,
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got := tt.options.overrides(false)
			if got.SyncAttachments != tt.wantSyncAttachments {
				t.Fatalf("SyncAttachments = %v, want %v", got.SyncAttachments, tt.wantSyncAttachments)
			}
			if got.HasSyncAttachmentsOverride != tt.wantHasOverride {
				t.Fatalf("HasSyncAttachmentsOverride = %v, want %v", got.HasSyncAttachmentsOverride, tt.wantHasOverride)
			}
		})
	}
}

func TestParseCommand_setsAttachmentMaxBytesOverride_onlyWhenFlagPassed(t *testing.T) {
	var stdout, stderr bytes.Buffer

	// When flag is passed
	options, ok := parseCommand(commandSpec{name: "sync", includeSyncFlags: true}, []string{"--attachment-max-bytes", "42"}, commandIO{stdout: &stdout, stderr: &stderr})

	// Then
	if !ok {
		t.Fatalf("parseCommand() ok = false, stderr = %q", stderr.String())
	}
	overrides := options.overrides(false)
	if !overrides.HasAttachmentMaxBytesOverride || overrides.AttachmentMaxBytes != 42 {
		t.Fatalf("overrides = %#v", overrides)
	}

	// When flag is omitted
	options, ok = parseCommand(commandSpec{name: "sync", includeSyncFlags: true}, []string{}, commandIO{stdout: &stdout, stderr: &stderr})

	// Then
	if !ok {
		t.Fatalf("parseCommand() ok = false, stderr = %q", stderr.String())
	}
	overrides = options.overrides(false)
	if overrides.HasAttachmentMaxBytesOverride {
		t.Fatalf("overrides = %#v, want HasAttachmentMaxBytesOverride = false", overrides)
	}
}

func TestRunSync_pushesAttachment_whenSyncAttachmentsFlagEnabled(t *testing.T) {
	// Given
	root := t.TempDir()
	clearCommandEnv(t)
	if err := os.WriteFile(filepath.Join(root, "photo.png"), []byte("binary-bytes"), 0o644); err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}
	var gotContent, gotEncoding string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/vaults/vault/sync/devices":
			writeCommandJSON(t, w, http.StatusOK, `{"success": true, "data": {"vault_id": "vault", "device_id": "dev", "registered": true}}`)
		case r.Method == http.MethodGet && r.URL.Path == "/vaults/vault/sync/changes":
			writeCommandJSON(t, w, http.StatusOK, `{"success": true, "data": {"vault_id": "vault", "from_cursor": 0, "to_cursor": 0, "changes": []}}`)
		case r.Method == http.MethodPut && r.URL.Path == "/vaults/vault/files/photo.png":
			var body map[string]any
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Fatalf("decode PUT body: %v", err)
			}
			gotContent, _ = body["content"].(string)
			gotEncoding, _ = body["encoding"].(string)
			writeCommandJSON(t, w, http.StatusOK, `{"success": true, "data": {"vault_id": "vault", "path": "photo.png", "revision": 1, "content_hash": "h"}}`)
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()
	var stdout bytes.Buffer
	var stderr bytes.Buffer

	// When
	code := run([]string{
		"sync",
		"--vault-root", root,
		"--server", server.URL,
		"--vault-id", "vault",
		"--device-id", "dev",
		"--sync-attachments",
	}, &stdout, &stderr)

	// Then
	if code != exitOK {
		t.Fatalf("exit code = %d, want %d, stderr = %q", code, exitOK, stderr.String())
	}
	if gotEncoding != "base64" {
		t.Fatalf("PUT encoding = %q, want base64", gotEncoding)
	}
	wantContent := base64.StdEncoding.EncodeToString([]byte("binary-bytes"))
	if gotContent != wantContent {
		t.Fatalf("PUT content = %q, want %q", gotContent, wantContent)
	}
}

func TestRunSync_conflictPolicyFlagOverridesConfigFile(t *testing.T) {
	// Given
	root := t.TempDir()
	clearCommandEnv(t)
	if err := os.WriteFile(filepath.Join(root, "note.md"), []byte("local edit"), 0o644); err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}
	writeCommandConfigFile(t, root, map[string]any{
		"conflict_policy": "manual",
	})
	server := syncConflictServer(t)
	defer server.Close()
	var stdout bytes.Buffer
	var stderr bytes.Buffer

	// When: --conflict-policy remote-wins overrides the config file's manual
	code := run([]string{
		"sync",
		"--vault-root", root,
		"--server", server.URL,
		"--vault-id", "vault",
		"--device-id", "dev",
		"--conflict-policy", "remote-wins",
	}, &stdout, &stderr)

	// Then
	if code != exitOK {
		t.Fatalf("exit code = %d, want %d, stderr = %q", code, exitOK, stderr.String())
	}
	if !strings.Contains(stderr.String(), "conflicts=0") {
		t.Fatalf("stderr = %q, want conflicts=0", stderr.String())
	}
	got, err := os.ReadFile(filepath.Join(root, "note.md"))
	if err != nil {
		t.Fatalf("ReadFile() error = %v", err)
	}
	if string(got) != "server edit" {
		t.Fatalf("note.md content = %q, want server content adopted", string(got))
	}
	backups, err := filepath.Glob(filepath.Join(root, "note.local-backup.conflict.dev.*.md"))
	if err != nil {
		t.Fatalf("Glob() error = %v", err)
	}
	if len(backups) != 1 {
		t.Fatalf("local backup files = %v, want exactly 1", backups)
	}
}

func TestRunSync_conflictPolicyLocalWinsFallsBackToManual_afterExhaustingRetries(t *testing.T) {
	// Given: syncConflictServer always 409s the PUT, so local-wins retries
	// exhaust and must fall back to writing a manual conflict file.
	root := t.TempDir()
	clearCommandEnv(t)
	if err := os.WriteFile(filepath.Join(root, "note.md"), []byte("local edit"), 0o644); err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}
	server := syncConflictServer(t)
	defer server.Close()
	var stdout bytes.Buffer
	var stderr bytes.Buffer

	// When
	code := run([]string{
		"sync",
		"--vault-root", root,
		"--server", server.URL,
		"--vault-id", "vault",
		"--device-id", "dev",
		"--conflict-policy", "local-wins",
	}, &stdout, &stderr)

	// Then
	if code != 1 {
		t.Fatalf("exit code = %d, want 1, stderr = %q", code, stderr.String())
	}
	conflicts, err := filepath.Glob(filepath.Join(root, "note.conflict.dev.*.md"))
	if err != nil {
		t.Fatalf("Glob() error = %v", err)
	}
	if len(conflicts) != 1 {
		t.Fatalf("conflicts = %v", conflicts)
	}
	got, err := os.ReadFile(filepath.Join(root, "note.md"))
	if err != nil {
		t.Fatalf("ReadFile() error = %v", err)
	}
	if string(got) != "local edit" {
		t.Fatalf("note.md content = %q, want local content left untouched", string(got))
	}
}

func TestRunSync_rejectsInvalidConflictPolicyFlag(t *testing.T) {
	// Given
	root := t.TempDir()
	clearCommandEnv(t)
	var stdout bytes.Buffer
	var stderr bytes.Buffer

	// When
	code := run([]string{
		"sync",
		"--vault-root", root,
		"--server", "http://127.0.0.1:1",
		"--vault-id", "vault",
		"--conflict-policy", "not-a-policy",
	}, &stdout, &stderr)

	// Then
	if code != exitError {
		t.Fatalf("exit code = %d, want %d, stderr = %q", code, exitError, stderr.String())
	}
	if !strings.Contains(stderr.String(), "conflict_policy") {
		t.Fatalf("stderr = %q, want mention of conflict_policy", stderr.String())
	}
}

func writeCommandConfigFile(t *testing.T, root string, data map[string]any) {
	t.Helper()
	configDir := filepath.Join(root, ".obsidian-sync-agent")
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		t.Fatalf("MkdirAll() error = %v", err)
	}
	encoded, err := json.Marshal(data)
	if err != nil {
		t.Fatalf("Marshal() error = %v", err)
	}
	if err := os.WriteFile(filepath.Join(configDir, "config.json"), encoded, 0o644); err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}
}

func emptySyncServer(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/vaults/vault/sync/devices":
			writeCommandJSON(t, w, http.StatusOK, `{"success": true, "data": {"vault_id": "vault", "device_id": "dev", "registered": true}}`)
		case r.Method == http.MethodGet && r.URL.Path == "/vaults/vault/sync/changes":
			writeCommandJSON(t, w, http.StatusOK, `{"success": true, "data": {"vault_id": "vault", "from_cursor": 0, "to_cursor": 0, "changes": []}}`)
		default:
			http.NotFound(w, r)
		}
	}))
}

func syncConflictServer(t *testing.T) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPost && r.URL.Path == "/vaults/vault/sync/devices":
			writeCommandJSON(t, w, http.StatusOK, `{"success": true, "data": {"vault_id": "vault", "device_id": "dev", "registered": true}}`)
		case r.Method == http.MethodGet && r.URL.Path == "/vaults/vault/sync/changes":
			writeCommandJSON(t, w, http.StatusOK, `{"success": true, "data": {"vault_id": "vault", "from_cursor": 0, "to_cursor": 0, "changes": []}}`)
		case r.Method == http.MethodPut && r.URL.Path == "/vaults/vault/files/note.md":
			writeCommandJSON(t, w, http.StatusConflict, `{
				"success": false,
				"error": {
					"code": "SYNC_CONFLICT",
					"message": "conflict",
					"details": {"server_revision": 2, "client_base_revision": 0}
				}
			}`)
		case r.Method == http.MethodGet && r.URL.Path == "/vaults/vault/files/note.md":
			writeCommandJSON(t, w, http.StatusOK, `{
				"success": true,
				"data": {
					"vault_id": "vault",
					"path": "note.md",
					"revision": 2,
					"content_hash": "server-hash",
					"content": "server edit",
					"deleted": false
				}
			}`)
		default:
			http.NotFound(w, r)
		}
	}))
}

func writeCommandJSON(t *testing.T, w http.ResponseWriter, status int, body string) {
	t.Helper()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if _, err := fmt.Fprintln(w, body); err != nil {
		t.Fatalf("Fprintln() error = %v", err)
	}
}

func clearCommandEnv(t *testing.T) {
	t.Helper()
	for _, name := range []string{
		"OBSIDIAN_SYNC_AGENT_SERVER",
		"OBSIDIAN_SYNC_AGENT_VAULT_ID",
		"OBSIDIAN_SYNC_AGENT_VAULT_ROOT",
		"OBSIDIAN_SYNC_AGENT_DEVICE_ID",
		"OBSIDIAN_SYNC_AGENT_DEVICE_NAME",
		"OBSIDIAN_SYNC_AGENT_TOKEN",
		"OBSIDIAN_LOCAL_REST_API_KEY",
	} {
		t.Setenv(name, "")
	}
}
