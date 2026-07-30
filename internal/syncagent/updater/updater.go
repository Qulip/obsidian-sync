package updater

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

const DefaultReleaseEndpoint = "https://api.github.com/repos/Qulip/obsidian-sync/releases/latest"

type State string

const (
	StateUpToDate State = "up_to_date"
	StateDeclined State = "declined"
	StateUpdated  State = "updated"
)

type Platform struct {
	OS           string
	Architecture string
}

type Options struct {
	CurrentVersion  string
	ExecutablePath  string
	ReleaseEndpoint string
	Input           io.Reader
	Output          io.Writer
	Platform        Platform
	HTTPClient      *http.Client
}

type Result struct {
	State   State
	Current string
	Latest  string
}

func Update(ctx context.Context, options Options) (Result, error) {
	options = withDefaults(options)
	if options.ExecutablePath == "" {
		return Result{}, fmt.Errorf("find current executable: path is empty")
	}
	if options.Platform.OS == "" || options.Platform.Architecture == "" {
		return Result{}, fmt.Errorf("detect platform: operating system and architecture are required")
	}

	latest, err := fetchRelease(ctx, options.HTTPClient, options.ReleaseEndpoint)
	if err != nil {
		return Result{}, err
	}
	if latest.Draft || latest.Prerelease {
		return Result{}, fmt.Errorf("latest release %q is not a stable release", latest.TagName)
	}
	if !isNewer(latest.TagName, options.CurrentVersion) {
		fmt.Fprintf(options.Output, "obsisync is up to date (%s)\n", options.CurrentVersion)
		return Result{State: StateUpToDate, Current: options.CurrentVersion, Latest: latest.TagName}, nil
	}

	fmt.Fprintf(options.Output, "Update available: %s -> %s\n", options.CurrentVersion, latest.TagName)
	fmt.Fprint(options.Output, "Install update? [y/N] ")
	confirmed, err := confirm(options.Input)
	if err != nil {
		return Result{}, fmt.Errorf("read update confirmation: %w", err)
	}
	if !confirmed {
		fmt.Fprintln(options.Output, "Update cancelled.")
		return Result{State: StateDeclined, Current: options.CurrentVersion, Latest: latest.TagName}, nil
	}

	releaseAsset, err := latest.findAsset(options.Platform)
	if err != nil {
		return Result{}, err
	}
	target, err := filepath.EvalSymlinks(options.ExecutablePath)
	if err != nil {
		return Result{}, fmt.Errorf("resolve executable path: %w", err)
	}
	archive, err := os.CreateTemp(filepath.Dir(target), ".obsisync-update-archive-*")
	if err != nil {
		return Result{}, fmt.Errorf("create update archive: %w", err)
	}
	archivePath := archive.Name()
	defer os.Remove(archivePath)

	if err := downloadAsset(ctx, options.HTTPClient, releaseAsset, archive); err != nil {
		archive.Close()
		return Result{}, err
	}
	if err := archive.Close(); err != nil {
		return Result{}, fmt.Errorf("close update archive: %w", err)
	}

	replacement, err := os.CreateTemp(filepath.Dir(target), ".obsisync-update-binary-*")
	if err != nil {
		return Result{}, fmt.Errorf("create replacement binary: %w", err)
	}
	replacementPath := replacement.Name()
	if err := replacement.Close(); err != nil {
		os.Remove(replacementPath)
		return Result{}, fmt.Errorf("close replacement binary: %w", err)
	}
	if err := extractBinary(archivePath, replacementPath, releaseAsset.Name, options.Platform); err != nil {
		os.Remove(replacementPath)
		return Result{}, err
	}
	if err := os.Chmod(replacementPath, 0o755); err != nil {
		os.Remove(replacementPath)
		return Result{}, fmt.Errorf("set replacement permissions: %w", err)
	}
	if err := replaceExecutable(replacementPath, target); err != nil {
		os.Remove(replacementPath)
		return Result{}, err
	}

	fmt.Fprintf(options.Output, "Updated obsisync to %s. Run obsisync again to use it.\n", latest.TagName)
	return Result{State: StateUpdated, Current: options.CurrentVersion, Latest: latest.TagName}, nil
}

func withDefaults(options Options) Options {
	if options.CurrentVersion == "" {
		options.CurrentVersion = "dev"
	}
	if options.ReleaseEndpoint == "" {
		options.ReleaseEndpoint = DefaultReleaseEndpoint
	}
	if options.Input == nil {
		options.Input = os.Stdin
	}
	if options.Output == nil {
		options.Output = os.Stdout
	}
	if options.Platform.OS == "" {
		options.Platform.OS = runtime.GOOS
	}
	if options.Platform.Architecture == "" {
		options.Platform.Architecture = runtime.GOARCH
	}
	if options.HTTPClient == nil {
		options.HTTPClient = &http.Client{Timeout: 30 * time.Second}
	}
	return options
}

func confirm(input io.Reader) (bool, error) {
	answer, err := bufio.NewReader(input).ReadString('\n')
	if err != nil && err != io.EOF {
		return false, err
	}
	return strings.EqualFold(strings.TrimSpace(answer), "y") || strings.EqualFold(strings.TrimSpace(answer), "yes"), nil
}
