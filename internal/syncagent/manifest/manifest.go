package manifest

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"

	"github.com/Qulip/obsidian-sync/internal/syncagent/atomicfile"
)

const (
	configDirName = ".obsidian-sync-agent"
	fileName      = "manifest.json"
)

type Entry struct {
	ServerRevision int    `json:"server_revision"`
	ContentHash    string `json:"content_hash"`
	LastSyncedAt   string `json:"last_synced_at"`
}

type Conflict struct {
	ServerRevision    int     `json:"server_revision"`
	ServerContentHash *string `json:"server_content_hash"`
	LocalContentHash  string  `json:"local_content_hash"`
	ServerDeleted     bool    `json:"server_deleted"`
}

type Manifest struct {
	VaultID        string              `json:"vault_id"`
	DeviceID       string              `json:"device_id"`
	LastSyncCursor int                 `json:"last_sync_cursor"`
	Files          map[string]Entry    `json:"files"`
	Conflicts      map[string]Conflict `json:"conflicts"`
}

func Path(vaultRoot string) string {
	return filepath.Join(vaultRoot, configDirName, fileName)
}

func Load(vaultRoot string) (Manifest, error) {
	data, err := os.ReadFile(Path(vaultRoot))
	if err != nil {
		if os.IsNotExist(err) {
			return New(), nil
		}
		return Manifest{}, err
	}
	trimmed := bytes.TrimSpace(data)
	if len(trimmed) == 0 {
		return Manifest{}, json.Unmarshal(data, &Manifest{})
	}
	var raw json.RawMessage
	if err := json.Unmarshal(trimmed, &raw); err != nil {
		return Manifest{}, err
	}
	if raw[0] != '{' {
		return New(), nil
	}
	var loaded Manifest
	if err := json.Unmarshal(data, &loaded); err != nil {
		return Manifest{}, err
	}
	loaded.ensureMaps()
	return loaded, nil
}

func Save(vaultRoot string, state Manifest) error {
	state.ensureMaps()
	data, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	return atomicfile.WriteBytes(Path(vaultRoot), data)
}

func New() Manifest {
	return Manifest{
		Files:     map[string]Entry{},
		Conflicts: map[string]Conflict{},
	}
}

func (m *Manifest) ensureMaps() {
	if m.Files == nil {
		m.Files = map[string]Entry{}
	}
	if m.Conflicts == nil {
		m.Conflicts = map[string]Conflict{}
	}
}
