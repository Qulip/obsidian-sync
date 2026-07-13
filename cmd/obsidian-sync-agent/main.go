package main

import (
	"context"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"

	"github.com/Qulip/obsidian-sync/internal/syncagent/config"
	"github.com/Qulip/obsidian-sync/internal/syncagent/engine"
)

const (
	commandName  = "obsisync"
	exitOK       = 0
	exitConflict = 1
	exitError    = 2
	exitObsidian = 3
)

func main() {
	os.Exit(run(os.Args[1:], os.Stdout, os.Stderr))
}

func run(args []string, stdout io.Writer, stderr io.Writer) int {
	if len(args) == 0 {
		fmt.Fprintln(stderr, "the following arguments are required: command")
		return exitError
	}
	if args[0] == "--help" || args[0] == "-h" {
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
	vaultRoot                 string
	vaultID                   string
	server                    string
	deviceID                  string
	verbose                   bool
	dryRun                    bool
	requireObsidianRefresh    bool
	syncAttachments           bool
	noSyncAttachments         bool
	attachmentMaxBytes        int64
	hasAttachmentMaxBytesFlag bool
	helpRequested             bool
}

type commandSpec struct {
	name             string
	includeSyncFlags bool
}

type commandIO struct {
	stdout io.Writer
	stderr io.Writer
}

func runStatus(args []string, stdout io.Writer, stderr io.Writer) int {
	options, ok := parseCommand(commandSpec{name: "status"}, args, commandIO{
		stdout: stdout,
		stderr: stderr,
	})
	if !ok {
		return exitError
	}
	if options.helpRequested {
		return exitOK
	}
	agentConfig, err := config.Load(options.overrides(false))
	if err != nil {
		return writeCommandError(stderr, "configuration error", err)
	}
	report, err := engine.RunStatus(context.Background(), agentConfig, engine.StatusOptions{})
	if err != nil {
		return writeCommandError(stderr, "sync failed", err)
	}
	printStatus(stdout, report)
	return exitOK
}

func runSync(args []string, stdout io.Writer, stderr io.Writer) int {
	options, ok := parseCommand(commandSpec{name: "sync", includeSyncFlags: true}, args, commandIO{
		stdout: stdout,
		stderr: stderr,
	})
	if !ok {
		return exitError
	}
	if options.helpRequested {
		return exitOK
	}
	agentConfig, err := config.Load(options.overrides(options.requireObsidianRefresh))
	if err != nil {
		return writeCommandError(stderr, "configuration error", err)
	}
	summary, err := engine.RunSync(context.Background(), agentConfig, engine.Options{
		DryRun: options.dryRun,
	})
	if err != nil {
		return writeCommandError(stderr, "sync failed", err)
	}
	printSummary(stderr, summary)
	if summary.DryRun {
		return exitOK
	}
	if agentConfig.RequireObsidianRefresh && !summary.ObsidianOK {
		fmt.Fprintf(stderr, "obsidian refresh required but failed: %s\n", summary.ObsidianMessage)
		return exitObsidian
	}
	if len(summary.Conflicts) > 0 {
		fmt.Fprintf(stderr, "sync completed with %d conflict(s); review the .conflict files\n", len(summary.Conflicts))
		return exitConflict
	}
	fmt.Fprintln(stderr, "sync completed successfully")
	return exitOK
}

func parseCommand(spec commandSpec, args []string, stdio commandIO) (commandOptions, bool) {
	var options commandOptions
	flags := flag.NewFlagSet(spec.name, flag.ContinueOnError)
	flags.SetOutput(stdio.stderr)
	flags.Usage = func() {
		printCommandHelp(stdio.stderr, spec)
	}
	flags.StringVar(&options.vaultRoot, "vault-root", "", "local vault directory")
	flags.StringVar(&options.vaultID, "vault-id", "", "server vault id")
	flags.StringVar(&options.server, "server", "", "server base url")
	flags.StringVar(&options.deviceID, "device-id", "", "override the device id")
	flags.BoolVar(&options.verbose, "verbose", false, "enable debug logging")
	if spec.includeSyncFlags {
		flags.BoolVar(&options.dryRun, "dry-run", false, "print planned actions without writing or pushing")
		flags.BoolVar(&options.requireObsidianRefresh, "require-obsidian-refresh", false, "exit non-zero if the Obsidian refresh step fails")
		flags.BoolVar(&options.syncAttachments, "sync-attachments", false, "enable attachment (image/PDF) sync")
		flags.BoolVar(&options.noSyncAttachments, "no-sync-attachments", false, "disable attachment sync even if enabled via config/env")
		flags.Int64Var(&options.attachmentMaxBytes, "attachment-max-bytes", 0, "local attachment size filter in bytes")
	}
	for _, arg := range args {
		if arg == "--help" || arg == "-h" || arg == "-help" {
			printCommandHelp(stdio.stdout, spec)
			options.helpRequested = true
			return options, true
		}
	}
	if err := flags.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			options.helpRequested = true
			return options, true
		}
		return commandOptions{}, false
	}
	if flags.NArg() != 0 {
		fmt.Fprintf(stdio.stderr, "%s %s: unexpected argument %q\n", commandName, spec.name, flags.Arg(0))
		return commandOptions{}, false
	}
	flags.Visit(func(f *flag.Flag) {
		if f.Name == "attachment-max-bytes" {
			options.hasAttachmentMaxBytesFlag = true
		}
	})
	return options, true
}

func printCommandHelp(output io.Writer, spec commandSpec) {
	fmt.Fprintf(output, "Usage of %s:\n", spec.name)
	fmt.Fprintln(output, "  --vault-root string")
	fmt.Fprintln(output, "    \tlocal vault directory")
	fmt.Fprintln(output, "  --vault-id string")
	fmt.Fprintln(output, "    \tserver vault id")
	fmt.Fprintln(output, "  --server string")
	fmt.Fprintln(output, "    \tserver base url")
	fmt.Fprintln(output, "  --device-id string")
	fmt.Fprintln(output, "    \toverride the device id")
	fmt.Fprintln(output, "  --verbose")
	fmt.Fprintln(output, "    \tenable debug logging")
	if spec.includeSyncFlags {
		fmt.Fprintln(output, "  --dry-run")
		fmt.Fprintln(output, "    \tprint planned actions without writing or pushing")
		fmt.Fprintln(output, "  --require-obsidian-refresh")
		fmt.Fprintln(output, "    \texit non-zero if the Obsidian refresh step fails")
		fmt.Fprintln(output, "  --sync-attachments")
		fmt.Fprintln(output, "    \tenable attachment (image/PDF) sync")
		fmt.Fprintln(output, "  --no-sync-attachments")
		fmt.Fprintln(output, "    \tdisable attachment sync even if enabled via config/env")
		fmt.Fprintln(output, "  --attachment-max-bytes int")
		fmt.Fprintln(output, "    \tlocal attachment size filter in bytes")
	}
}

func (o commandOptions) overrides(requireRefreshSet bool) config.CLIOverrides {
	return config.CLIOverrides{
		VaultRoot:                     o.vaultRoot,
		VaultID:                       o.vaultID,
		ServerBaseURL:                 o.server,
		DeviceID:                      o.deviceID,
		RequireObsidianRefresh:        o.requireObsidianRefresh,
		HasRequireRefreshOverride:     requireRefreshSet,
		SyncAttachments:               o.syncAttachments && !o.noSyncAttachments,
		HasSyncAttachmentsOverride:    o.syncAttachments || o.noSyncAttachments,
		AttachmentMaxBytes:            o.attachmentMaxBytes,
		HasAttachmentMaxBytesOverride: o.hasAttachmentMaxBytesFlag,
	}
}

func writeCommandError(stderr io.Writer, prefix string, err error) int {
	fmt.Fprintf(stderr, "%s: %v\n", prefix, err)
	return exitError
}

func printSummary(output io.Writer, summary engine.Summary) {
	prefix := ""
	if summary.DryRun {
		prefix = "DRY RUN: "
	}
	fmt.Fprintf(
		output,
		"%spulled=%d applied=%d locally_deleted=%d pushed=%d remotely_deleted=%d conflicts=%d warnings=%d\n",
		prefix,
		summary.Pulled,
		summary.Applied,
		summary.LocallyDeleted,
		summary.Pushed,
		summary.RemotelyDeleted,
		len(summary.Conflicts),
		len(summary.Warnings),
	)
	for _, path := range summary.Conflicts {
		fmt.Fprintf(output, "conflict: %s (a .conflict copy was written)\n", path)
	}
	for _, warning := range summary.Warnings {
		fmt.Fprintf(output, "warning: %s\n", warning)
	}
}

func printStatus(output io.Writer, report engine.StatusReport) {
	lastSeen := "unknown"
	if report.Server.DeviceLastSeenRevision != nil {
		lastSeen = fmt.Sprintf("%d", *report.Server.DeviceLastSeenRevision)
	}
	fmt.Fprintf(output, "vault_id=%s\n", report.Server.VaultID)
	fmt.Fprintf(output, "server_revision=%d\n", report.Server.ServerRevision)
	fmt.Fprintf(output, "device_last_seen_revision=%s\n", lastSeen)
	fmt.Fprintf(output, "pending_changes=%d\n", report.Server.PendingChanges)
	fmt.Fprintf(output, "open_conflicts=%d\n", report.Server.OpenConflicts)
	fmt.Fprintf(output, "pending_vectorizing_jobs=%d\n", report.Server.PendingVectorizingJobs)
	fmt.Fprintf(output, "local_manifest_cursor=%d\n", report.LocalManifestCursor)
	fmt.Fprintf(output, "tracked_local_files=%d\n", report.TrackedLocalFiles)
}
