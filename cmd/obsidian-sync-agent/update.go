package main

import (
	"context"
	"fmt"
	"io"
	"os"

	"github.com/Qulip/obsidian-sync/internal/syncagent/updater"
)

func runUpdate(args []string, stdout io.Writer, stderr io.Writer) int {
	if len(args) == 1 && (args[0] == "--help" || args[0] == "-h" || args[0] == "-help") {
		printUpdateHelp(stdout)
		return exitOK
	}
	if len(args) > 0 {
		fmt.Fprintf(stderr, "%s update: unexpected argument %q\n", commandName, args[0])
		return exitError
	}
	executable, err := os.Executable()
	if err != nil {
		return writeCommandError(stderr, "update failed", fmt.Errorf("find executable: %w", err))
	}
	if _, err := updater.Update(context.Background(), updater.Options{
		CurrentVersion: version,
		ExecutablePath: executable,
		Input:          os.Stdin,
		Output:         stdout,
	}); err != nil {
		return writeCommandError(stderr, "update failed", err)
	}
	return exitOK
}

func printUpdateHelp(output io.Writer) {
	fmt.Fprintln(output, "Check for and install the latest obsisync release.")
	fmt.Fprintf(output, "Usage: %s update\n", commandName)
}
