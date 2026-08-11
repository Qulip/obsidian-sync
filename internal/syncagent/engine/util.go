package engine

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"os"
	"sort"

	"github.com/Qulip/obsidian-sync/internal/syncagent/manifest"
	"github.com/Qulip/obsidian-sync/internal/syncagent/rules"
	"github.com/Qulip/obsidian-sync/internal/syncagent/vaultfs"
)

func vaultPath(root string, rel string, syncAttachments bool) (string, bool) {
	if !rules.ShouldSync(rel, syncAttachments) {
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
	return hashBytes(data), nil
}

func hashBytes(data []byte) string {
	sum := sha256.Sum256(data)
	return hex.EncodeToString(sum[:])
}

func intDetail(raw json.RawMessage) int {
	var value int
	if err := json.Unmarshal(raw, &value); err != nil {
		return 0
	}
	return value
}

// sortedConflictPaths returns manifest conflict paths in deterministic order
// so resolving conflicts left over from an earlier run doesn't depend on Go's
// randomized map iteration.
func sortedConflictPaths(conflicts map[string]manifest.Conflict) []string {
	paths := make([]string, 0, len(conflicts))
	for path := range conflicts {
		paths = append(paths, path)
	}
	sort.Strings(paths)
	return paths
}
