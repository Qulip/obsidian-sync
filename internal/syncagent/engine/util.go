package engine

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"

	"github.com/Qulip/obsidian-sync/internal/syncagent/rules"
	"github.com/Qulip/obsidian-sync/internal/syncagent/vaultfs"
)

func vaultPath(root string, rel string) (string, bool) {
	if !rules.ShouldSync(rel) {
		return "", false
	}
	return vaultfs.LexicalPath(root, rel)
}

func existsPath(path string) bool {
	_, err := os.Lstat(path)
	return err == nil
}

func hashFile(path string) (string, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return hashText(string(data)), nil
}

func hashText(content string) string {
	sum := sha256.Sum256([]byte(content))
	return hex.EncodeToString(sum[:])
}

func intDetail(raw json.RawMessage) int {
	var value int
	if err := json.Unmarshal(raw, &value); err != nil {
		return 0
	}
	return value
}
