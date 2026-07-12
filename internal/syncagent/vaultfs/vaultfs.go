package vaultfs

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"strings"
)

var (
	ErrUnsafePath = errors.New("vault path is unsafe")
	ErrSymlink    = errors.New("vault path contains symlink")
)

func LexicalPath(root string, rel string) (string, bool) {
	clean, ok := cleanRelative(rel)
	if !ok {
		return "", false
	}
	absoluteRoot, err := filepath.Abs(root)
	if err != nil {
		return "", false
	}
	return filepath.Join(absoluteRoot, clean), true
}

func SafePath(root string, rel string) (string, error) {
	clean, ok := cleanRelative(rel)
	if !ok {
		return "", fmt.Errorf("%w: %s", ErrUnsafePath, rel)
	}
	absoluteRoot, err := filepath.Abs(root)
	if err != nil {
		return "", fmt.Errorf("resolve vault root: %w", err)
	}
	if err := rejectSymlinkComponents(absoluteRoot, clean); err != nil {
		return "", err
	}
	return filepath.Join(absoluteRoot, clean), nil
}

func cleanRelative(rel string) (string, bool) {
	if filepath.IsAbs(rel) {
		return "", false
	}
	clean := filepath.Clean(filepath.FromSlash(rel))
	if clean == "." || clean == ".." || strings.HasPrefix(clean, ".."+string(filepath.Separator)) {
		return "", false
	}
	return clean, true
}

func rejectSymlinkComponents(root string, clean string) error {
	current := root
	for _, part := range strings.Split(clean, string(filepath.Separator)) {
		if part == "" {
			continue
		}
		current = filepath.Join(current, part)
		info, err := os.Lstat(current)
		if errors.Is(err, os.ErrNotExist) {
			return nil
		}
		if err != nil {
			return fmt.Errorf("inspect vault path %s: %w", current, err)
		}
		if info.Mode()&os.ModeSymlink != 0 {
			return fmt.Errorf("%w: %s", ErrSymlink, current)
		}
	}
	return nil
}
