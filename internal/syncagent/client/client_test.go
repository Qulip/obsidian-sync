package client

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"
)

func TestClientRegisterDevice_readsSuccessEnvelopeAndAuthHeader(t *testing.T) {
	// Given
	var gotAuth string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		if r.Method != http.MethodPost {
			t.Fatalf("method = %s, want POST", r.Method)
		}
		if r.URL.EscapedPath() != "/vaults/vault%20one/sync/devices" {
			t.Fatalf("path = %s", r.URL.EscapedPath())
		}
		var body RegisterDeviceRequest
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatalf("decode request: %v", err)
		}
		if body.DeviceID != "dev1" || body.DeviceName == nil || *body.DeviceName != "Laptop" {
			t.Fatalf("body = %#v", body)
		}
		writeJSON(t, w, http.StatusOK, `{
			"success": true,
			"data": {"vault_id": "vault one", "device_id": "dev1", "registered": true}
		}`)
	}))
	defer server.Close()
	c := newTestClient(t, server.URL, "secret")
	name := "Laptop"

	// When
	got, err := c.RegisterDevice(context.Background(), "vault one", RegisterDeviceRequest{
		DeviceID:   "dev1",
		DeviceName: &name,
	})

	// Then
	if err != nil {
		t.Fatalf("RegisterDevice returned error: %v", err)
	}
	if got.VaultID != "vault one" || got.DeviceID != "dev1" || !got.Registered {
		t.Fatalf("data = %#v", got)
	}
	if gotAuth != "Bearer secret" {
		t.Fatalf("Authorization = %q", gotAuth)
	}
}

func TestClientGetChanges_omitsAuthorizationHeaderWhenTokenEmpty(t *testing.T) {
	// Given
	var gotAuth string
	var gotQuery string
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		gotAuth = r.Header.Get("Authorization")
		gotQuery = r.URL.RawQuery
		writeJSON(t, w, http.StatusOK, `{
			"success": true,
			"data": {
				"vault_id": "vault",
				"from_cursor": 4,
				"to_cursor": 5,
				"changes": [{
					"revision": 5,
					"path": "notes/a.md",
					"event_type": "modified",
					"content_hash": "hash",
					"deleted": false,
					"created_at": "2026-07-09T00:00:00Z"
				}]
			}
		}`)
	}))
	defer server.Close()
	c := newTestClient(t, server.URL, "")

	// When
	got, err := c.GetChanges(context.Background(), "vault", ChangesRequest{
		Since:    4,
		DeviceID: "dev1",
		Limit:    25,
	})

	// Then
	if err != nil {
		t.Fatalf("GetChanges returned error: %v", err)
	}
	if gotAuth != "" {
		t.Fatalf("Authorization = %q", gotAuth)
	}
	if gotQuery != "device_id=dev1&limit=25&since=4" {
		t.Fatalf("query = %q", gotQuery)
	}
	if got.ToCursor != 5 || len(got.Changes) != 1 || got.Changes[0].CreatedAt.IsZero() {
		t.Fatalf("changes = %#v", got)
	}
}

func TestClientPutDeleteAndStatus(t *testing.T) {
	// Given
	var sawPut bool
	var sawDelete bool
	var sawStatus bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch {
		case r.Method == http.MethodPut:
			sawPut = true
			if r.URL.EscapedPath() != "/vaults/vault%2Fid/files/folder%20one/note/slash.md" {
				t.Fatalf("put path = %s", r.URL.EscapedPath())
			}
			if r.Header.Get("Authorization") != "Bearer secret" {
				t.Fatalf("put Authorization = %q", r.Header.Get("Authorization"))
			}
			var body PutFileRequest
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Fatalf("decode put request: %v", err)
			}
			if body.DeviceID != "dev1" || body.BaseRevision != 2 || body.ContentHash != "hash" || body.Content != "body" {
				t.Fatalf("put body = %#v", body)
			}
			writeJSON(t, w, http.StatusOK, `{
				"success": true,
				"data": {"vault_id": "vault/id", "path": "folder one/note/slash.md", "revision": 3, "content_hash": "hash"}
			}`)
		case r.Method == http.MethodDelete:
			sawDelete = true
			if r.URL.EscapedPath() != "/vaults/vault%2Fid/files/folder%20one/note/slash.md" {
				t.Fatalf("delete path = %s", r.URL.EscapedPath())
			}
			if r.Header.Get("Content-Type") != "application/json" {
				t.Fatalf("delete Content-Type = %q", r.Header.Get("Content-Type"))
			}
			var body DeleteFileRequest
			if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
				t.Fatalf("decode delete request: %v", err)
			}
			if body.DeviceID != "dev1" || body.BaseRevision != 3 {
				t.Fatalf("delete body = %#v", body)
			}
			writeJSON(t, w, http.StatusOK, `{
				"success": true,
				"data": {"vault_id": "vault/id", "path": "folder one/note/slash.md", "revision": 4, "deleted": true}
			}`)
		case r.Method == http.MethodGet && r.URL.EscapedPath() == "/vaults/vault%2Fid/sync/status":
			sawStatus = true
			if r.URL.RawQuery != "device_id=dev1" {
				t.Fatalf("status query = %q", r.URL.RawQuery)
			}
			writeJSON(t, w, http.StatusOK, `{
				"success": true,
				"data": {
					"vault_id": "vault/id",
					"device_id": "dev1",
					"server_revision": 4,
					"device_last_seen_revision": 3,
					"pending_changes": 1,
					"open_conflicts": 0,
					"pending_vectorizing_jobs": 2
				}
			}`)
		default:
			t.Fatalf("unexpected request %s %s", r.Method, r.URL.String())
		}
	}))
	defer server.Close()
	c := newTestClient(t, server.URL, "secret")
	ref := FileRef{VaultID: "vault/id", Path: "folder one/note/slash.md"}

	// When
	put, putErr := c.PutFile(context.Background(), ref, PutFileRequest{
		DeviceID:     "dev1",
		BaseRevision: 2,
		ContentHash:  "hash",
		Content:      "body",
	})
	del, delErr := c.DeleteFile(context.Background(), ref, DeleteFileRequest{
		DeviceID:     "dev1",
		BaseRevision: 3,
	})
	status, statusErr := c.GetStatus(context.Background(), "vault/id", StatusRequest{DeviceID: "dev1"})

	// Then
	if putErr != nil {
		t.Fatalf("PutFile returned error: %v", putErr)
	}
	if delErr != nil {
		t.Fatalf("DeleteFile returned error: %v", delErr)
	}
	if statusErr != nil {
		t.Fatalf("GetStatus returned error: %v", statusErr)
	}
	if put.Revision != 3 || del.Revision != 4 || !del.Deleted || status.PendingVectorizingJobs != 2 {
		t.Fatalf("responses = %#v %#v %#v", put, del, status)
	}
	if !sawPut || !sawDelete || !sawStatus {
		t.Fatalf("requests seen: put=%t delete=%t status=%t", sawPut, sawDelete, sawStatus)
	}
}

func TestClientGetFile_encodesEachPathSegmentAndParsesEnvelope(t *testing.T) {
	// Given
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.EscapedPath() != "/vaults/vault/files/dir%20one/name%23tag.md" {
			t.Fatalf("path = %s", r.URL.EscapedPath())
		}
		writeJSON(t, w, http.StatusOK, `{
			"success": true,
			"data": {
				"vault_id": "vault",
				"path": "dir one/name#tag.md",
				"revision": 7,
				"content_hash": "hash",
				"content": "# note",
				"deleted": false
			}
		}`)
	}))
	defer server.Close()
	c := newTestClient(t, server.URL, "")

	// When
	got, err := c.GetFile(context.Background(), FileRef{
		VaultID: "vault",
		Path:    "dir one/name#tag.md",
	})

	// Then
	if err != nil {
		t.Fatalf("GetFile returned error: %v", err)
	}
	if got.Path != "dir one/name#tag.md" || got.Content != "# note" || got.Revision != 7 {
		t.Fatalf("file = %#v", got)
	}
	if EncodeVaultPath("a/b c/#tag") != "a/b%20c/%23tag" {
		t.Fatalf("EncodeVaultPath did not preserve slash-separated segments")
	}
}

func newTestClient(t *testing.T, baseURL string, token string) *Client {
	t.Helper()
	c, err := New(baseURL, token, time.Second)
	if err != nil {
		t.Fatalf("New returned error: %v", err)
	}
	return c
}

func writeJSON(t *testing.T, w http.ResponseWriter, status int, body string) {
	t.Helper()
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if _, err := w.Write([]byte(body)); err != nil {
		t.Fatalf("write response: %v", err)
	}
}
