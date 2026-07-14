//go:build !windows

package updater

import (
	"fmt"
	"os"
)

func replaceExecutable(replacementPath string, targetPath string) error {
	if err := os.Rename(replacementPath, targetPath); err != nil {
		return fmt.Errorf("replace executable: %w", err)
	}
	return nil
}
