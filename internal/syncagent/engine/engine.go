package engine

import (
	"context"
	"fmt"
	"time"

	"github.com/Qulip/obsidian-sync/internal/syncagent/client"
	"github.com/Qulip/obsidian-sync/internal/syncagent/config"
	"github.com/Qulip/obsidian-sync/internal/syncagent/manifest"
	"github.com/Qulip/obsidian-sync/internal/syncagent/obsidian"
	"github.com/Qulip/obsidian-sync/internal/syncagent/scanner"
)

func RunSync(ctx context.Context, cfg config.AgentConfig, options Options) (Summary, error) {
	summary := Summary{DryRun: options.DryRun, ObsidianOK: true}
	state, err := loadAndValidateManifest(cfg)
	if err != nil {
		return summary, err
	}
	syncClient, err := clientFor(cfg, options.Client)
	if err != nil {
		return summary, err
	}
	runner := syncRun{
		ctx:        ctx,
		cfg:        cfg,
		state:      &state,
		syncClient: syncClient,
		summary:    &summary,
		now:        nowFunc(options),
	}
	if options.DryRun {
		return runner.plan()
	}
	if err := runner.run(); err != nil {
		return summary, err
	}
	if err := manifest.Save(cfg.VaultRoot, state); err != nil {
		return summary, fmt.Errorf("save manifest: %w", err)
	}
	runner.runObsidian(options)
	return summary, nil
}

func RunStatus(ctx context.Context, cfg config.AgentConfig, options StatusOptions) (StatusReport, error) {
	syncClient, err := clientFor(cfg, options.Client)
	if err != nil {
		return StatusReport{}, err
	}
	status, err := syncClient.GetStatus(ctx, cfg.VaultID, client.StatusRequest{DeviceID: cfg.DeviceID})
	if err != nil {
		return StatusReport{}, fmt.Errorf("get sync status: %w", err)
	}
	state, err := manifest.Load(cfg.VaultRoot)
	if err != nil {
		return StatusReport{}, fmt.Errorf("load manifest: %w", err)
	}
	return StatusReport{
		Server:              status,
		LocalManifestCursor: state.LastSyncCursor,
		TrackedLocalFiles:   len(state.Files),
	}, nil
}

type syncRun struct {
	ctx        context.Context
	cfg        config.AgentConfig
	state      *manifest.Manifest
	syncClient SyncClient
	summary    *Summary
	now        func() time.Time
}

func (r *syncRun) run() error {
	deviceName := r.cfg.DeviceName
	var deviceNamePtr *string
	if deviceName != "" {
		deviceNamePtr = &deviceName
	}
	_, err := r.syncClient.RegisterDevice(r.ctx, r.cfg.VaultID, client.RegisterDeviceRequest{
		DeviceID:   r.cfg.DeviceID,
		DeviceName: deviceNamePtr,
	})
	if err != nil {
		return fmt.Errorf("register device: %w", err)
	}
	if err := r.pull(""); err != nil {
		return err
	}
	skipPaths := map[string]struct{}{}
	for _, path := range r.summary.Conflicts {
		skipPaths[path] = struct{}{}
	}
	scanned, excluded, scanWarnings, err := scanner.ScanVault(r.cfg.VaultRoot, r.cfg.SyncAttachments, r.cfg.AttachmentMaxBytes)
	if err != nil {
		return fmt.Errorf("scan vault: %w", err)
	}
	r.summary.Warnings = append(r.summary.Warnings, scanWarnings...)
	local := scanner.ClassifyLocalChanges(scanned, excluded, *r.state, r.cfg.SyncAttachments)
	if err := r.push(local, skipPaths); err != nil {
		return err
	}
	return r.pull(r.cfg.DeviceID)
}

func (r *syncRun) plan() (Summary, error) {
	cursor := r.state.LastSyncCursor
	for {
		page, err := r.syncClient.GetChanges(r.ctx, r.cfg.VaultID, client.ChangesRequest{
			Since: cursor,
			Limit: client.DefaultPageLimit,
		})
		if err != nil {
			return *r.summary, fmt.Errorf("get changes: %w", err)
		}
		r.summary.Pulled += len(page.Changes)
		if len(page.Changes) == 0 || page.ToCursor <= cursor {
			break
		}
		cursor = page.ToCursor
	}
	scanned, excluded, scanWarnings, err := scanner.ScanVault(r.cfg.VaultRoot, r.cfg.SyncAttachments, r.cfg.AttachmentMaxBytes)
	if err != nil {
		return *r.summary, fmt.Errorf("scan vault: %w", err)
	}
	r.summary.Warnings = append(r.summary.Warnings, scanWarnings...)
	local := scanner.ClassifyLocalChanges(scanned, excluded, *r.state, r.cfg.SyncAttachments)
	r.summary.Pushed = len(local.New) + len(local.Modified)
	r.summary.RemotelyDeleted = len(local.Deleted)
	return *r.summary, nil
}

func loadAndValidateManifest(cfg config.AgentConfig) (manifest.Manifest, error) {
	state, err := manifest.Load(cfg.VaultRoot)
	if err != nil {
		return manifest.Manifest{}, fmt.Errorf("load manifest: %w", err)
	}
	if state.VaultID != "" && state.VaultID != cfg.VaultID {
		return manifest.Manifest{}, fmt.Errorf("%w: manifest vault_id %q does not match configured vault_id %q", ErrSync, state.VaultID, cfg.VaultID)
	}
	state.VaultID = cfg.VaultID
	state.DeviceID = cfg.DeviceID
	return state, nil
}

func (r *syncRun) runObsidian(options Options) {
	refresh := options.RefreshObsidian
	if refresh == nil {
		refresh = obsidian.Refresh
	}
	result := refresh(r.ctx, obsidian.Config{
		Enabled:       r.cfg.Obsidian.Enabled,
		BaseURL:       r.cfg.Obsidian.BaseURL,
		APIKey:        r.cfg.Obsidian.APIKey,
		VerifyTLS:     r.cfg.Obsidian.VerifyTLS,
		ReloadCommand: r.cfg.Obsidian.ReloadCommand,
	})
	r.summary.ObsidianOK = result.OK
	r.summary.ObsidianMessage = result.Message
	if !result.OK && !r.cfg.RequireObsidianRefresh {
		r.summary.Warnings = append(r.summary.Warnings, result.Message)
	}
}
