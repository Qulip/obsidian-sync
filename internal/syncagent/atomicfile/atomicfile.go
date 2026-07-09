package atomicfile

import (
	"os"
	"path/filepath"
)

func WriteBytes(path string, data []byte) error {
	dir := filepath.Dir(path)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return err
	}
	temp, err := os.CreateTemp(dir, ".tmp-*"+filepath.Ext(path))
	if err != nil {
		return err
	}
	tempName := temp.Name()
	keepTemp := false
	defer func() {
		if !keepTemp {
			_ = os.Remove(tempName)
		}
	}()
	if _, err := temp.Write(data); err != nil {
		_ = temp.Close()
		return err
	}
	if err := temp.Close(); err != nil {
		return err
	}
	if err := os.Rename(tempName, path); err != nil {
		return err
	}
	keepTemp = true
	return nil
}

func WriteText(path string, text string) error {
	return WriteBytes(path, []byte(text))
}
