package main

import (
	"errors"
	"flag"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"time"

	"github.com/Qulip/obsidian-sync/internal/syncagent/config"
)

const (
	commandName = "obsidian-sync-agent"
	exitOK      = 0
	exitError   = 2
)

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(args []string, stdout io.Writer, stderr io.Writer) int {
	if len(args) == 0 || args[0] == "--help" || args[0] == "-h" {
		printHelp(stdout)
		return exitOK
	}
	switch args[0] {
	case "status":
		return runStatus(args[1:], stdout, stderr)
	case "sync":
		return runSync(args[1:], stdout, stderr)
	default:
		fmt.Fprintf(stderr, "%s: unknown command %q\n", commandName, args[0])
		return exitError
	}
}

func printHelp(output io.Writer) {
	fmt.Fprintf(output, "%s\n\n", commandName)
	fmt.Fprintln(output, "Usage:")
	fmt.Fprintf(output, "  %s --help\n", commandName)
	fmt.Fprintf(output, "  %s <command> [flags]\n", commandName)
	fmt.Fprintln(output)
	fmt.Fprintln(output, "Commands:")
	fmt.Fprintln(output, "  sync")
	fmt.Fprintln(output, "  status")
}

type commandOptions struct {
	vaultRoot              string
	vaultID                string
	server                 string
	deviceID               string
	verbose                bool
	dryRun                 bool
	requireObsidianRefresh bool
}

func runStatus(args []string, stdout io.Writer, stderr io.Writer) int {
	options, ok := parseCommand("status", args, stderr, false)
	if !ok {
		return exitError
	}
	agentConfig, err := config.Load(options.overrides(false))
	if err != nil {
		return writeCommandError(stderr, "configuration error", err)
	}
	if err := probeStatus(agentConfig); err != nil {
		return writeCommandError(stderr, "sync failed", err)
	}
	fmt.Fprintln(stdout, "status ok")
	return exitOK
}

func runSync(args []string, _ io.Writer, stderr io.Writer) int {
	options, ok := parseCommand("sync", args, stderr, true)
	if !ok {
		return exitError
	}
	_, err := config.Load(options.overrides(options.requireObsidianRefresh))
	if err != nil {
		return writeCommandError(stderr, "configuration error", err)
	}
	return writeCommandError(stderr, "sync failed", errors.New("sync engine is not implemented yet"))
}

func parseCommand(name string, args []string, stderr io.Writer, includeSyncFlags bool) (commandOptions, bool) {
	var options commandOptions
	flags := flag.NewFlagSet(name, flag.ContinueOnError)
	flags.SetOutput(stderr)
	flags.StringVar(&options.vaultRoot, "vault-root", "", "local vault directory")
	flags.StringVar(&options.vaultID, "vault-id", "", "server vault id")
	flags.StringVar(&options.server, "server", "", "server base url")
	flags.StringVar(&options.deviceID, "device-id", "", "override the device id")
	flags.BoolVar(&options.verbose, "verbose", false, "enable debug logging")
	if includeSyncFlags {
		flags.BoolVar(&options.dryRun, "dry-run", false, "print planned actions without writing or pushing")
		flags.BoolVar(&options.requireObsidianRefresh, "require-obsidian-refresh", false, "exit non-zero if the Obsidian refresh step fails")
	}
	if err := flags.Parse(args); err != nil {
		return commandOptions{}, false
	}
	if flags.NArg() != 0 {
		fmt.Fprintf(stderr, "%s %s: unexpected argument %q\n", commandName, name, flags.Arg(0))
		return commandOptions{}, false
	}
	return options, true
}

func (o commandOptions) overrides(requireRefreshSet bool) config.CLIOverrides {
	return config.CLIOverrides{
		VaultRoot:                 o.vaultRoot,
		VaultID:                   o.vaultID,
		ServerBaseURL:             o.server,
		DeviceID:                  o.deviceID,
		RequireObsidianRefresh:    o.requireObsidianRefresh,
		HasRequireRefreshOverride: requireRefreshSet,
	}
}

func probeStatus(agentConfig config.AgentConfig) error {
	statusURL, err := url.JoinPath(agentConfig.ServerBaseURL, "api", "v1", "sync", "vaults", agentConfig.VaultID, "status")
	if err != nil {
		return fmt.Errorf("build status url: %w", err)
	}
	parsed, err := url.Parse(statusURL)
	if err != nil {
		return fmt.Errorf("parse status url: %w", err)
	}
	query := parsed.Query()
	query.Set("device_id", agentConfig.DeviceID)
	parsed.RawQuery = query.Encode()

	client := http.Client{Timeout: 2 * time.Second}
	req, err := http.NewRequest(http.MethodGet, parsed.String(), nil)
	if err != nil {
		return fmt.Errorf("create status request for %s: %w", parsed.String(), err)
	}
	if agentConfig.APIToken != "" {
		req.Header.Set("Authorization", "Bearer "+agentConfig.APIToken)
	}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("get status from %s for vault %s device %s: %w", agentConfig.ServerBaseURL, agentConfig.VaultID, agentConfig.DeviceID, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		return fmt.Errorf("get status from %s for vault %s device %s: http %d", agentConfig.ServerBaseURL, agentConfig.VaultID, agentConfig.DeviceID, resp.StatusCode)
	}
	return nil
}

func writeCommandError(stderr io.Writer, prefix string, err error) int {
	fmt.Fprintf(stderr, "%s: %v\n", prefix, err)
	return exitError
}
