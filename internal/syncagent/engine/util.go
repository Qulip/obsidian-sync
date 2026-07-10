package engine

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"

	"github.com/Qulip/obsidian-sync/internal/syncagent/rules"
)

func vaultPath(root string, rel string) (string, bool) {
	if !rules.ShouldSync(rel) || filepath.IsAbs(rel) {
		return "", false
	}
	clean := filepath.Clean(filepath.FromSlash(rel))
	if clean == "." || clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
		return "", false
	}
	absoluteRoot, err := filepath.Abs(root)
	if err != nil {
		return "", false
	}
	return filepath.Join(absoluteRoot, clean), true
}

func existsPath(path string) bool {
	_, err := os.Stat(path)
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
