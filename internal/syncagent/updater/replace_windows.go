//go:build windows

package updater

import (
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
)

func replaceExecutable(replacementPath string, targetPath string) error {
	script, err := os.CreateTemp(filepath.Dir(targetPath), ".obsisync-update-*.cmd")
	if err != nil {
		return fmt.Errorf("create replacement script: %w", err)
	}
	scriptPath := script.Name()
	contents := "@echo off\r\n:retry\r\nmove /Y " + batchQuote(replacementPath) + " " + batchQuote(targetPath) + " >nul 2>nul\r\nif errorlevel 1 (\r\n  timeout /t 1 /nobreak >nul\r\n  goto retry\r\n)\r\ndel \"%~f0\"\r\n"
	if _, err := script.WriteString(contents); err != nil {
		script.Close()
		os.Remove(scriptPath)
		return fmt.Errorf("write replacement script: %w", err)
	}
	if err := script.Close(); err != nil {
		os.Remove(scriptPath)
		return fmt.Errorf("close replacement script: %w", err)
	}
	if err := exec.Command("cmd.exe", "/c", scriptPath).Start(); err != nil {
		os.Remove(scriptPath)
		return fmt.Errorf("start replacement script: %w", err)
	}
	return nil
}

func batchQuote(value string) string {
	return "\"" + strings.ReplaceAll(strings.ReplaceAll(value, "%", "%%"), "\"", "\"\"") + "\""
}
