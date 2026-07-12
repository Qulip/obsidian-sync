package obsidian

import (
	"context"
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestRefresh_returnsOK_whenDisabled(t *testing.T) {
	// Given
	ctx := context.Background()

	// When
	got := Refresh(ctx, Config{Enabled: false})

	// Then
	if !got.OK {
		t.Fatalf("Refresh() OK = false, message = %q", got.Message)
	}
}

func TestRefresh_returnsOK_whenHealthSucceedsWithoutReload(t *testing.T) {
	// Given
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			t.Fatalf("path = %q, want /", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"authenticated":false}`))
	}))
	defer server.Close()

	// When
	got := Refresh(context.Background(), Config{Enabled: true, BaseURL: server.URL})

	// Then
	if !got.OK {
		t.Fatalf("Refresh() OK = false, message = %q", got.Message)
	}
}

func TestRefresh_returnsNotOK_whenHealthFails(t *testing.T) {
	// Given
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		http.Error(w, "nope", http.StatusServiceUnavailable)
	}))
	defer server.Close()

	// When
	got := Refresh(context.Background(), Config{Enabled: true, BaseURL: server.URL})

	// Then
	if got.OK {
		t.Fatalf("Refresh() OK = true, want false")
	}
	if got.RequireRefreshExitCode(true) != RequireRefreshExitCode {
		t.Fatalf("RequireRefreshExitCode(true) = %d, want %d", got.RequireRefreshExitCode(true), RequireRefreshExitCode)
	}
	if got.RequireRefreshExitCode(false) != 0 {
		t.Fatalf("RequireRefreshExitCode(false) = %d, want 0", got.RequireRefreshExitCode(false))
	}
}

func TestReloadCommandWithoutAPIKey_returnsNotOK(t *testing.T) {
	// Given
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/" {
			t.Fatalf("unexpected reload path without api key: %s", r.URL.Path)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"authenticated":false}`))
	}))
	defer server.Close()

	// When
	got := Refresh(context.Background(), Config{
		Enabled:       true,
		BaseURL:       server.URL,
		ReloadCommand: true,
	})

	// Then
	if got.OK {
		t.Fatalf("Refresh() OK = true, want false")
	}
	t.Logf("reload without API key returned not ok: %s", got.Message)
}

func TestRefresh_reloads_whenAuthenticatedAndCommandAvailable(t *testing.T) {
	// Given
	var reloaded bool
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		switch r.URL.Path {
		case "/":
			if got := r.Header.Get("Authorization"); got != "Bearer secret" {
				t.Fatalf("Authorization = %q, want Bearer secret", got)
			}
			_, _ = w.Write([]byte(`{"authenticated":true}`))
		case "/commands/":
			_, _ = w.Write([]byte(`{"commands":[{"id":"app:reload"}]}`))
		case "/commands/app:reload/":
			if r.Method != http.MethodPost {
				t.Fatalf("reload method = %s, want POST", r.Method)
			}
			reloaded = true
			_, _ = w.Write([]byte(`{}`))
		default:
			http.NotFound(w, r)
		}
	}))
	defer server.Close()

	// When
	got := Refresh(context.Background(), Config{
		Enabled:       true,
		BaseURL:       server.URL,
		APIKey:        "secret",
		ReloadCommand: true,
	})

	// Then
	if !got.OK {
		t.Fatalf("Refresh() OK = false, message = %q", got.Message)
	}
	if !reloaded {
		t.Fatalf("reload command was not posted")
	}
}

func TestRefresh_usesEnvironmentAPIKey_whenConfigKeyIsEmpty(t *testing.T) {
	// Given
	t.Setenv("OBSIDIAN_LOCAL_REST_API_KEY", "env-secret")
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer env-secret" {
			t.Fatalf("Authorization = %q, want Bearer env-secret", got)
		}
		w.Header().Set("Content-Type", "application/json")
		_, _ = w.Write([]byte(`{"authenticated":false}`))
	}))
	defer server.Close()

	// When
	got := Refresh(context.Background(), Config{Enabled: true, BaseURL: server.URL})

	// Then
	if !got.OK {
		t.Fatalf("Refresh() OK = false, message = %q", got.Message)
	}
}
