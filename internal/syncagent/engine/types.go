package engine

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/Qulip/obsidian-sync/internal/syncagent/client"
	"github.com/Qulip/obsidian-sync/internal/syncagent/config"
	"github.com/Qulip/obsidian-sync/internal/syncagent/obsidian"
)

var ErrSync = errors.New("sync engine error")

type SyncClient interface {
	RegisterDevice(context.Context, string, client.RegisterDeviceRequest) (client.RegisterDeviceData, error)
	GetChanges(context.Context, string, client.ChangesRequest) (client.SyncChangesData, error)
	GetStatus(context.Context, string, client.StatusRequest) (client.SyncStatusData, error)
	GetFile(context.Context, client.FileRef) (client.FileContentData, error)
	PutFile(context.Context, client.FileRef, client.PutFileRequest) (client.PutFileData, error)
	DeleteFile(context.Context, client.FileRef, client.DeleteFileRequest) (client.DeleteFileData, error)
}

type Options struct {
	DryRun          bool
	Client          SyncClient
	Now             func() time.Time
	RefreshObsidian func(context.Context, obsidian.Config) obsidian.Result
}

type StatusOptions struct {
	Client SyncClient
}

type Summary struct {
	Pulled          int
	Applied         int
	LocallyDeleted  int
	Pushed          int
	RemotelyDeleted int
	Conflicts       []string
	Warnings        []string
	ObsidianOK      bool
	ObsidianMessage string
	DryRun          bool
}

type StatusReport struct {
	Server              client.SyncStatusData
	LocalManifestCursor int
	TrackedLocalFiles   int
}

func clientFor(cfg config.AgentConfig, provided SyncClient) (SyncClient, error) {
	if provided != nil {
		return provided, nil
	}
	syncClient, err := client.New(cfg.ServerBaseURL, cfg.APIToken, client.DefaultTimeout)
	if err != nil {
		return nil, fmt.Errorf("create sync client: %w", err)
	}
	return syncClient, nil
}

func nowFunc(options Options) func() time.Time {
	if options.Now != nil {
		return options.Now
	}
	return func() time.Time {
		return time.Now().UTC()
	}
}
