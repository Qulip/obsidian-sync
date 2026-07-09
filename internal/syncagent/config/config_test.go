package config

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestLoadConfig_usesFileValues_whenNoEnvOrCLI(t *testing.T) {
	// Given
	root := t.TempDir()
	writeConfigFile(t, root, map[string]any{
		"server_base_url": "https://file.example/",
		"vault_id":        "file-vault",
		"device_id":       "file device!",
		"device_name":     "File Device",
		"obsidian": map[string]any{
			"enabled":        true,
			"base_url":       "https://obsidian.local",
			"verify_tls":     true,
			"reload_command": true,
		},
		"require_obsidian_refresh": true,
	})
	clearConfigEnv(t)

	// When
	got, err := Load(CLIOverrides{VaultRoot: root})

	// Then
	if err != nil {
		t.Fatalf("Load() error = %v", err)
	}
	if got.ServerBaseURL != "https://file.example" {
		t.Fatalf("ServerBaseURL = %q", got.ServerBaseURL)
	}
	if got.VaultID != "file-vault" {
		t.Fatalf("VaultID = %q", got.VaultID)
	}
	if got.DeviceID != "file-device" {
		t.Fatalf("DeviceID = %q", got.DeviceID)
	}
	if got.DeviceName != "File Device" {
		t.Fatalf("DeviceName = %q", got.DeviceName)
	}
	if !got.RequireObsidianRefresh {
		t.Fatal("RequireObsidianRefresh = false")
	}
	if !got.Obsidian.Enabled || got.Obsidian.BaseURL != "https://obsidian.local" || !got.Obsidian.VerifyTLS || !got.Obsidian.ReloadCommand {
		t.Fatalf("Obsidian = %#v", got.Obsidian)
	}
}

func TestLoadConfig_envOverridesFileAndTokenIsEnvOnly(t *testing.T) {
	// Given
	root := t.TempDir()
	writeConfigFile(t, root, map[string]any{
		"server_base_url": "https://file.example",
		"vault_id":        "file-vault",
		"device_id":       "file-device",
		"api_token":       "file-token-ignored",
		"obsidian": map[string]any{
			"api_key": "file-obsidian-key",
		},
	})
	clearConfigEnv(t)
	t.Setenv(ServerEnv, "https://env.example/")
	t.Setenv(VaultIDEnv, "env-vault")
	t.Setenv(DeviceIDEnv, "env device!")
	t.Setenv(DeviceNameEnv, "Env Device")
	t.Setenv(TokenEnv, "env-token")
	t.Setenv(ObsidianKeyEnv, "env-obsidian-key")

	// When
	got, err := Load(CLIOverrides{VaultRoot: root})

	// Then
	if err != nil {
		t.Fatalf("Load() error = %v", err)
	}
	if got.ServerBaseURL != "https://env.example" {
		t.Fatalf("ServerBaseURL = %q", got.ServerBaseURL)
	}
	if got.VaultID != "env-vault" {
		t.Fatalf("VaultID = %q", got.VaultID)
	}
	if got.DeviceID != "env-device" {
		t.Fatalf("DeviceID = %q", got.DeviceID)
	}
	if got.DeviceName != "Env Device" {
		t.Fatalf("DeviceName = %q", got.DeviceName)
	}
	if got.APIToken != "env-token" {
		t.Fatalf("APIToken = %q", got.APIToken)
	}
	if got.Obsidian.APIKey != "env-obsidian-key" {
		t.Fatalf("Obsidian.APIKey = %q", got.Obsidian.APIKey)
	}
}

func TestLoadConfig_ignoresFileToken_whenEnvTokenMissing(t *testing.T) {
	// Given
	root := t.TempDir()
	writeConfigFile(t, root, map[string]any{
		"server_base_url": "https://file.example",
		"vault_id":        "file-vault",
		"api_token":       "file-token-ignored",
	})
	clearConfigEnv(t)

	// When
	got, err := Load(CLIOverrides{VaultRoot: root})

	// Then
	if err != nil {
		t.Fatalf("Load() error = %v", err)
	}
	if got.APIToken != "" {
		t.Fatalf("APIToken = %q", got.APIToken)
	}
}

func TestLoadConfig_cliOverridesEnvAndFile(t *testing.T) {
	// Given
	root := t.TempDir()
	writeConfigFile(t, root, map[string]any{
		"server_base_url":              "https://file.example",
		"vault_id":                     "file-vault",
		"device_id":                    "file-device",
		"require_obsidian_refresh":     true,
		"unexpected_future_safe_field": "ignored",
	})
	clearConfigEnv(t)
	t.Setenv(ServerEnv, "https://env.example")
	t.Setenv(VaultIDEnv, "env-vault")
	t.Setenv(DeviceIDEnv, "env-device")

	// When
	got, err := Load(CLIOverrides{
		VaultRoot:                 root,
		ServerBaseURL:             "https://cli.example/",
		VaultID:                   "cli-vault",
		DeviceID:                  "cli device!",
		RequireObsidianRefresh:    true,
		HasRequireRefreshOverride: true,
	})

	// Then
	if err != nil {
		t.Fatalf("Load() error = %v", err)
	}
	if got.ServerBaseURL != "https://cli.example" {
		t.Fatalf("ServerBaseURL = %q", got.ServerBaseURL)
	}
	if got.VaultID != "cli-vault" {
		t.Fatalf("VaultID = %q", got.VaultID)
	}
	if got.DeviceID != "cli-device" {
		t.Fatalf("DeviceID = %q", got.DeviceID)
	}
	if !got.RequireObsidianRefresh {
		t.Fatal("RequireObsidianRefresh = false")
	}
}

func TestLoadConfig_requireRefreshFlagDoesNotOverrideConfigFalseWhenUnset(t *testing.T) {
	// Given
	root := t.TempDir()
	writeConfigFile(t, root, map[string]any{
		"server_base_url":          "https://file.example",
		"vault_id":                 "file-vault",
		"require_obsidian_refresh": true,
	})
	clearConfigEnv(t)

	// When
	got, err := Load(CLIOverrides{VaultRoot: root})

	// Then
	if err != nil {
		t.Fatalf("Load() error = %v", err)
	}
	if !got.RequireObsidianRefresh {
		t.Fatal("RequireObsidianRefresh = false")
	}
}

func TestLoadConfig_rejectsMissingRequiredValues(t *testing.T) {
	// Given
	root := t.TempDir()
	clearConfigEnv(t)

	// When
	_, err := Load(CLIOverrides{VaultRoot: root})

	// Then
	if err == nil {
		t.Fatal("Load() error = nil")
	}
	if !IsConfigError(err) {
		t.Fatalf("Load() error type = %T", err)
	}
}

func TestLoadConfig_rejectsMissingVaultID(t *testing.T) {
	// Given
	root := t.TempDir()
	clearConfigEnv(t)
	t.Setenv(ServerEnv, "https://env.example")

	// When
	_, err := Load(CLIOverrides{VaultRoot: root})

	// Then
	if err == nil {
		t.Fatal("Load() error = nil")
	}
	if !IsConfigError(err) {
		t.Fatalf("Load() error type = %T", err)
	}
}

func TestLoadConfig_usesEnvVaultRootAndDefaultObsidianValues(t *testing.T) {
	// Given
	root := t.TempDir()
	writeConfigFile(t, root, map[string]any{
		"server_base_url": "https://file.example",
		"vault_id":        "file-vault",
	})
	clearConfigEnv(t)
	t.Setenv(VaultRootEnv, root)

	// When
	got, err := Load(CLIOverrides{})

	// Then
	if err != nil {
		t.Fatalf("Load() error = %v", err)
	}
	if got.VaultRoot != cleanPath(root) {
		t.Fatalf("VaultRoot = %q, want %q", got.VaultRoot, cleanPath(root))
	}
	if got.Obsidian.BaseURL != DefaultObsidianBaseURL {
		t.Fatalf("Obsidian.BaseURL = %q", got.Obsidian.BaseURL)
	}
	if got.Obsidian.VerifyTLS {
		t.Fatal("Obsidian.VerifyTLS = true")
	}
	if got.Obsidian.ReloadCommand {
		t.Fatal("Obsidian.ReloadCommand = true")
	}
	if got.DeviceID == "" {
		t.Fatal("DeviceID is empty")
	}
}

func writeConfigFile(t *testing.T, root string, data map[string]any) {
	t.Helper()
	configDir := filepath.Join(root, ConfigDirName)
	if err := os.MkdirAll(configDir, 0o755); err != nil {
		t.Fatalf("MkdirAll() error = %v", err)
	}
	encoded, err := json.Marshal(data)
	if err != nil {
		t.Fatalf("Marshal() error = %v", err)
	}
	if err := os.WriteFile(filepath.Join(configDir, ConfigFileName), encoded, 0o644); err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}
}

func clearConfigEnv(t *testing.T) {
	t.Helper()
	for _, name := range []string{
		ServerEnv,
		VaultIDEnv,
		VaultRootEnv,
		DeviceIDEnv,
		DeviceNameEnv,
		TokenEnv,
		ObsidianKeyEnv,
	} {
		t.Setenv(name, "")
	}
}

func cleanPath(path string) string {
	if runtime.GOOS == "windows" {
		return filepath.Clean(path)
	}
	cleaned, err := filepath.Abs(path)
	if err != nil {
		return filepath.Clean(path)
	}
	return cleaned
}
