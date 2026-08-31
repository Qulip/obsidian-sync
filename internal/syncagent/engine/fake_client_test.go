package engine

import (
	"context"
	"encoding/json"
	"errors"

	"github.com/Qulip/obsidian-sync/internal/syncagent/client"
)

type fakeClient struct {
	changes             []client.SyncChangeItem
	files               map[string]client.FileContentData
	getFileErrors       map[string]error
	getFileCalls        map[string]int
	status              client.SyncStatusData
	putConflict         map[string]map[string]json.RawMessage
	deleteConflict      map[string]map[string]json.RawMessage
	registerCalls       int
	nextRevision        int
	getChangesCalls     []client.ChangesRequest
	getChangesErrOnCall int
	getChangesErr       error
	secondPullDeviceID  string
	statusDeviceID      string
	puts                []putCall
	deletes             []deleteCall

	// putConflictAttempts/deleteConflictAttempts optionally bound how many
	// times the corresponding *Conflict entry fires before the call starts
	// succeeding, so tests can exercise a local-wins retry that resolves on
	// a later attempt. A path absent from these maps (the default) conflicts
	// on every call, matching the original unconditional behavior.
	putConflictAttempts    map[string]int
	deleteConflictAttempts map[string]int
}

type putCall struct {
	path         string
	baseRevision int
	content      string
}

type deleteCall struct {
	path         string
	baseRevision int
}

func newFakeClient() *fakeClient {
	return &fakeClient{
		files:                  map[string]client.FileContentData{},
		getFileErrors:          map[string]error{},
		getFileCalls:           map[string]int{},
		putConflict:            map[string]map[string]json.RawMessage{},
		deleteConflict:         map[string]map[string]json.RawMessage{},
		putConflictAttempts:    map[string]int{},
		deleteConflictAttempts: map[string]int{},
		nextRevision:           1,
		status: client.SyncStatusData{
			VaultID: "vault",
		},
	}
}

func (f *fakeClient) RegisterDevice(_ context.Context, _ string, _ client.RegisterDeviceRequest) (client.RegisterDeviceData, error) {
	f.registerCalls++
	return client.RegisterDeviceData{VaultID: "vault", DeviceID: "dev", Registered: true}, nil
}

func (f *fakeClient) GetChanges(_ context.Context, _ string, query client.ChangesRequest) (client.SyncChangesData, error) {
	f.getChangesCalls = append(f.getChangesCalls, query)
	if f.getChangesErrOnCall > 0 && len(f.getChangesCalls) == f.getChangesErrOnCall {
		if f.getChangesErr != nil {
			return client.SyncChangesData{}, f.getChangesErr
		}
		return client.SyncChangesData{}, errors.New("forced get changes failure")
	}
	if query.DeviceID != "" {
		f.secondPullDeviceID = query.DeviceID
	}
	var changes []client.SyncChangeItem
	for _, item := range f.changes {
		if item.Revision > query.Since {
			changes = append(changes, item)
		}
	}
	toCursor := query.Since
	for _, item := range changes {
		if item.Revision > toCursor {
			toCursor = item.Revision
		}
	}
	return client.SyncChangesData{VaultID: "vault", FromCursor: query.Since, ToCursor: toCursor, Changes: changes}, nil
}

func (f *fakeClient) GetStatus(_ context.Context, _ string, query client.StatusRequest) (client.SyncStatusData, error) {
	f.statusDeviceID = query.DeviceID
	return f.status, nil
}

func (f *fakeClient) GetFile(_ context.Context, ref client.FileRef) (client.FileContentData, error) {
	f.getFileCalls[ref.Path]++
	if err, ok := f.getFileErrors[ref.Path]; ok {
		return client.FileContentData{}, err
	}
	data, ok := f.files[ref.Path]
	if !ok {
		return client.FileContentData{}, errors.New("missing fake file")
	}
	return data, nil
}

func (f *fakeClient) PutFile(_ context.Context, ref client.FileRef, body client.PutFileRequest) (client.PutFileData, error) {
	if details, ok := f.putConflict[ref.Path]; ok && f.consumeConflictAttempt(f.putConflictAttempts, ref.Path) {
		return client.PutFileData{}, &client.ConflictError{Message: "conflict", StatusCode: 409, Details: details}
	}
	f.puts = append(f.puts, putCall{path: ref.Path, baseRevision: body.BaseRevision, content: body.Content})
	revision := f.nextRevision
	f.nextRevision++
	f.files[ref.Path] = fileData(revision, ref.Path, body.Content)
	return client.PutFileData{VaultID: ref.VaultID, Path: ref.Path, Revision: revision, ContentHash: body.ContentHash}, nil
}

func (f *fakeClient) DeleteFile(_ context.Context, ref client.FileRef, body client.DeleteFileRequest) (client.DeleteFileData, error) {
	if details, ok := f.deleteConflict[ref.Path]; ok && f.consumeConflictAttempt(f.deleteConflictAttempts, ref.Path) {
		return client.DeleteFileData{}, &client.ConflictError{Message: "conflict", StatusCode: 409, Details: details}
	}
	f.deletes = append(f.deletes, deleteCall{path: ref.Path, baseRevision: body.BaseRevision})
	delete(f.files, ref.Path)
	revision := f.nextRevision
	f.nextRevision++
	return client.DeleteFileData{VaultID: ref.VaultID, Path: ref.Path, Revision: revision, Deleted: true}, nil
}

// consumeConflictAttempt reports whether a conflict should still fire for
// path. A path absent from attempts (the default) conflicts unconditionally;
// a path present with a remaining count > 0 conflicts and decrements it.
func (f *fakeClient) consumeConflictAttempt(attempts map[string]int, path string) bool {
	remaining, hasLimit := attempts[path]
	if !hasLimit {
		return true
	}
	if remaining <= 0 {
		return false
	}
	attempts[path] = remaining - 1
	return true
}
