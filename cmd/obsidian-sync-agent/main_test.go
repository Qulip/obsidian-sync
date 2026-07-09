package main

import (
	"bytes"
	"encoding/json"
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
	if !strings.Contains(stderr.String(), "configuration error") {
		t.Fatalf("stderr = %q", stderr.String())
	}
	if !strings.Contains(stderr.String(), "server base url") {
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
		w.WriteHeader(http.StatusOK)
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
