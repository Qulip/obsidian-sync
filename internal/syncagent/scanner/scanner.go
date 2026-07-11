package scanner

import (
	"crypto/sha256"
	"encoding/hex"
	"io"
	"os"
	"path/filepath"
	"sort"

	"github.com/Qulip/obsidian-sync/internal/syncagent/manifest"
	"github.com/Qulip/obsidian-sync/internal/syncagent/rules"
)

type ScannedFile struct {
	Path        string
	Size        int64
	MTime       float64
	ContentHash string
}

type LocalChanges struct {
	New      []string
	Modified []string
	Deleted  []string
}

func ScanVault(vaultRoot string) (map[string]ScannedFile, error) {
	root, err := filepath.Abs(vaultRoot)
	if err != nil {
		return nil, err
	}
	scanned := map[string]ScannedFile{}
	err = filepath.WalkDir(root, func(path string, entry os.DirEntry, walkErr error) error {
		if walkErr != nil {
			return walkErr
		}
		if entry.Type()&os.ModeSymlink != 0 {
			return nil
		}
		if entry.IsDir() {
			if path != root && rules.IsIgnoredDir(entry.Name()) {
				return filepath.SkipDir
			}
			return nil
		}
		rel, err := filepath.Rel(root, path)
		if err != nil {
			return err
		}
		rel = filepath.ToSlash(rel)
		if !rules.ShouldSync(rel) {
			return nil
		}
		info, err := entry.Info()
		if err != nil {
			return err
		}
		contentHash, err := hashFile(path)
		if err != nil {
			return err
		}
		scanned[rel] = ScannedFile{
			Path:        rel,
			Size:        info.Size(),
			MTime:       float64(info.ModTime().UnixNano()) / 1e9,
			ContentHash: contentHash,
		}
		return nil
	})
	if err != nil {
		return nil, err
	}
	return scanned, nil
}

func ClassifyLocalChanges(scanned map[string]ScannedFile, state manifest.Manifest) LocalChanges {
	changes := LocalChanges{}
	for path, file := range scanned {
		entry, ok := state.Files[path]
		if !ok {
			changes.New = append(changes.New, path)
			continue
		}
		if entry.ContentHash != file.ContentHash {
			changes.Modified = append(changes.Modified, path)
		}
	}
	for path := range state.Files {
		if _, ok := scanned[path]; !ok {
			changes.Deleted = append(changes.Deleted, path)
		}
	}
	sort.Strings(changes.New)
	sort.Strings(changes.Modified)
	sort.Strings(changes.Deleted)
	return changes
}

func hashFile(path string) (string, error) {
	file, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer file.Close()
	hasher := sha256.New()
	if _, err := io.Copy(hasher, file); err != nil {
		return "", err
	}
	return hex.EncodeToString(hasher.Sum(nil)), nil
}
