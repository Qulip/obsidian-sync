package client

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestClientGetChanges_readsFastAPINaiveTimestamp(t *testing.T) {
	// Given
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(t, w, http.StatusOK, `{
			"success": true,
			"data": {
				"vault_id": "vault",
				"from_cursor": 0,
				"to_cursor": 1,
				"changes": [{
					"revision": 1,
					"path": "notes/a.md",
					"event_type": "CREATE",
					"content_hash": "hash",
					"deleted": false,
					"created_at": "2026-07-10T08:11:12.123456"
				}]
			}
		}`)
	}))
	defer server.Close()
	c := newTestClient(t, server.URL, "")

	// When
	got, err := c.GetChanges(context.Background(), "vault", ChangesRequest{})

	// Then
	if err != nil {
		t.Fatalf("GetChanges returned error: %v", err)
	}
	if len(got.Changes) != 1 || got.Changes[0].CreatedAt.IsZero() {
		t.Fatalf("changes = %#v", got.Changes)
	}
}
