package client

import (
	"context"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestClientNonJSONResponse_returnsAPIError(t *testing.T) {
	// Given
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusBadGateway)
		_, _ = w.Write([]byte("bad gateway"))
	}))
	defer server.Close()
	c := newTestClient(t, server.URL, "")

	// When
	_, err := c.GetStatus(context.Background(), "vault", StatusRequest{})

	// Then
	var apiErr *APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("error = %v, want APIError", err)
	}
	if apiErr.StatusCode != http.StatusBadGateway || !strings.Contains(apiErr.Error(), "non-JSON") {
		t.Fatalf("api error = %#v", apiErr)
	}
}

func TestClientErrorEnvelope_returnsAPIError(t *testing.T) {
	// Given
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(t, w, http.StatusBadRequest, `{
			"success": false,
			"error": {
				"code": "VALIDATION_ERROR",
				"message": "bad path",
				"details": {"field": "path"}
			}
		}`)
	}))
	defer server.Close()
	c := newTestClient(t, server.URL, "")

	// When
	_, err := c.GetStatus(context.Background(), "vault", StatusRequest{})

	// Then
	var apiErr *APIError
	if !errors.As(err, &apiErr) {
		t.Fatalf("error = %v, want APIError", err)
	}
	if apiErr.Code != "VALIDATION_ERROR" || apiErr.StatusCode != http.StatusBadRequest {
		t.Fatalf("api error = %#v", apiErr)
	}
	if string(apiErr.Details["field"]) != `"path"` {
		t.Fatalf("details = %#v", apiErr.Details)
	}
}

func TestClientSyncConflict(t *testing.T) {
	// Given
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		writeJSON(t, w, http.StatusConflict, `{
			"success": false,
			"error": {
				"code": "SYNC_CONFLICT",
				"message": "revision conflict",
				"details": {
					"client_base_revision": 1,
					"server_revision": 2,
					"server_content_hash": "abc"
				}
			}
		}`)
	}))
	defer server.Close()
	c := newTestClient(t, server.URL, "")

	// When
	_, err := c.PutFile(context.Background(), FileRef{VaultID: "vault", Path: "a.md"}, PutFileRequest{
		DeviceID:     "dev1",
		BaseRevision: 1,
		ContentHash:  "hash",
		Content:      "body",
	})

	// Then
	var conflictErr *ConflictError
	if !errors.As(err, &conflictErr) {
		t.Fatalf("error = %v, want ConflictError", err)
	}
	if conflictErr.Message != "revision conflict" {
		t.Fatalf("message = %q", conflictErr.Message)
	}
	if string(conflictErr.Details["server_revision"]) != "2" {
		t.Fatalf("details = %#v", conflictErr.Details)
	}
}
