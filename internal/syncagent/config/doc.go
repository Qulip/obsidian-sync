package config

import (
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"unicode"
)

const (
	ConfigDirName          = ".obsidian-sync-agent"
	ConfigFileName         = "config.json"
	DefaultObsidianBaseURL = "https://127.0.0.1:27124"

	TokenEnv       = "OBSIDIAN_SYNC_AGENT_TOKEN"
	ServerEnv      = "OBSIDIAN_SYNC_AGENT_SERVER"
	VaultIDEnv     = "OBSIDIAN_SYNC_AGENT_VAULT_ID"
	VaultRootEnv   = "OBSIDIAN_SYNC_AGENT_VAULT_ROOT"
	DeviceIDEnv    = "OBSIDIAN_SYNC_AGENT_DEVICE_ID"
	DeviceNameEnv  = "OBSIDIAN_SYNC_AGENT_DEVICE_NAME"
	ObsidianKeyEnv = "OBSIDIAN_LOCAL_REST_API_KEY"
)

var errConfig = errors.New("config")

type ConfigError struct {
	message string
}

func (e *ConfigError) Error() string {
	return e.message
}

func (e *ConfigError) Is(target error) bool {
	return target == errConfig
}

type ObsidianConfig struct {
	Enabled       bool
	BaseURL       string
	APIKey        string
	VerifyTLS     bool
	ReloadCommand bool
}

type AgentConfig struct {
	ServerBaseURL          string
	VaultID                string
	VaultRoot              string
	DeviceID               string
	APIToken               string
	DeviceName             string
	Obsidian               ObsidianConfig
	RequireObsidianRefresh bool
}

type CLIOverrides struct {
	VaultRoot                 string
	VaultID                   string
	ServerBaseURL             string
	DeviceID                  string
	RequireObsidianRefresh    bool
	HasRequireRefreshOverride bool
}

type fileConfig struct {
	ServerBaseURL          string              `json:"server_base_url"`
	VaultID                string              `json:"vault_id"`
	DeviceID               string              `json:"device_id"`
	DeviceName             string              `json:"device_name"`
	RequireObsidianRefresh *bool               `json:"require_obsidian_refresh"`
	Obsidian               *fileObsidianConfig `json:"obsidian"`
}

type fileObsidianConfig struct {
	Enabled       bool   `json:"enabled"`
	BaseURL       string `json:"base_url"`
	APIKey        string `json:"api_key"`
	VerifyTLS     bool   `json:"verify_tls"`
	ReloadCommand bool   `json:"reload_command"`
}

func Load(overrides CLIOverrides) (AgentConfig, error) {
	vaultRoot, err := pickVaultRoot(overrides.VaultRoot)
	if err != nil {
		return AgentConfig{}, err
	}
	fileData, err := loadFile(vaultRoot)
	if err != nil {
		return AgentConfig{}, err
	}

	server := pickString(overrides.ServerBaseURL, os.Getenv(ServerEnv), fileData.ServerBaseURL)
	if server == "" {
		return AgentConfig{}, newConfigError(
			"server base url is required (use --server, %s, or config file server_base_url)",
			ServerEnv,
		)
	}
	vaultID := pickString(overrides.VaultID, os.Getenv(VaultIDEnv), fileData.VaultID)
	if vaultID == "" {
		return AgentConfig{}, newConfigError(
			"vault id is required (use --vault-id, %s, or config file vault_id)",
			VaultIDEnv,
		)
	}

	deviceID := pickString(overrides.DeviceID, os.Getenv(DeviceIDEnv), fileData.DeviceID)
	if deviceID == "" {
		deviceID = defaultDeviceID()
	}

	return AgentConfig{
		ServerBaseURL:          strings.TrimRight(server, "/"),
		VaultID:                vaultID,
		VaultRoot:              vaultRoot,
		DeviceID:               SanitizeDeviceID(deviceID),
		APIToken:               os.Getenv(TokenEnv),
		DeviceName:             pickString(os.Getenv(DeviceNameEnv), fileData.DeviceName),
		Obsidian:               obsidianFromFile(fileData),
		RequireObsidianRefresh: requireObsidianRefresh(overrides, fileData),
	}, nil
}

func IsConfigError(err error) bool {
	return errors.Is(err, errConfig)
}

func ConfigPath(vaultRoot string) string {
	return filepath.Join(vaultRoot, ConfigDirName, ConfigFileName)
}

func SanitizeDeviceID(raw string) string {
	var builder strings.Builder
	for _, character := range raw {
		if unicode.IsLetter(character) || unicode.IsDigit(character) || character == '-' || character == '_' || character == '.' {
			builder.WriteRune(character)
			continue
		}
		builder.WriteRune('-')
	}
	cleaned := strings.Trim(builder.String(), "-_.")
	if cleaned == "" {
		return "device"
	}
	return cleaned
}

func pickVaultRoot(cliRoot string) (string, error) {
	root := pickString(cliRoot, os.Getenv(VaultRootEnv))
	if root == "" {
		var err error
		root, err = os.Getwd()
		if err != nil {
			return "", fmt.Errorf("get current directory: %w", err)
		}
	}
	expanded, err := expandUser(root)
	if err != nil {
		return "", err
	}
	absolute, err := filepath.Abs(expanded)
	if err != nil {
		return "", newConfigError("resolve vault root %q: %v", root, err)
	}
	return filepath.Clean(absolute), nil
}

func expandUser(path string) (string, error) {
	if path != "~" && !strings.HasPrefix(path, "~/") {
		return path, nil
	}
	home, err := os.UserHomeDir()
	if err != nil {
		return "", newConfigError("resolve home directory: %v", err)
	}
	if path == "~" {
		return home, nil
	}
	return filepath.Join(home, strings.TrimPrefix(path, "~/")), nil
}

func loadFile(vaultRoot string) (fileConfig, error) {
	path := ConfigPath(vaultRoot)
	raw, err := os.ReadFile(path)
	if err != nil {
		if errors.Is(err, os.ErrNotExist) {
			return fileConfig{}, nil
		}
		return fileConfig{}, newConfigError("could not read config file %s: %v", path, err)
	}
	var data fileConfig
	if err := json.Unmarshal(raw, &data); err != nil {
		return fileConfig{}, newConfigError("could not read config file %s: %v", path, err)
	}
	return data, nil
}

func obsidianFromFile(data fileConfig) ObsidianConfig {
	config := ObsidianConfig{BaseURL: DefaultObsidianBaseURL}
	if data.Obsidian != nil {
		config = ObsidianConfig{
			Enabled:       data.Obsidian.Enabled,
			BaseURL:       pickString(data.Obsidian.BaseURL, DefaultObsidianBaseURL),
			APIKey:        data.Obsidian.APIKey,
			VerifyTLS:     data.Obsidian.VerifyTLS,
			ReloadCommand: data.Obsidian.ReloadCommand,
		}
	}
	config.APIKey = pickString(os.Getenv(ObsidianKeyEnv), config.APIKey)
	return config
}

func requireObsidianRefresh(overrides CLIOverrides, data fileConfig) bool {
	if overrides.HasRequireRefreshOverride {
		return overrides.RequireObsidianRefresh
	}
	if data.RequireObsidianRefresh != nil {
		return *data.RequireObsidianRefresh
	}
	return false
}

func defaultDeviceID() string {
	hostname, err := os.Hostname()
	if err != nil {
		return "device"
	}
	return SanitizeDeviceID(hostname)
}

func pickString(candidates ...string) string {
	for _, candidate := range candidates {
		if candidate != "" {
			return candidate
		}
	}
	return ""
}

func newConfigError(format string, args ...any) error {
	return &ConfigError{message: fmt.Sprintf(format, args...)}
}
