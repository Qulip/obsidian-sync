package obsidian

import (
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"strings"
	"time"
)

const (
	ReloadCommandID        = "app:reload"
	RequireRefreshExitCode = 3

	apiKeyEnv = "OBSIDIAN_LOCAL_REST_API_KEY"
)

type Config struct {
	Enabled       bool
	BaseURL       string
	APIKey        string
	VerifyTLS     bool
	ReloadCommand bool
	Client        *http.Client
}

type Result struct {
	OK      bool
	Message string
}

func (r Result) RequireRefreshExitCode(requireRefresh bool) int {
	if requireRefresh && !r.OK {
		return RequireRefreshExitCode
	}
	return 0
}

func Refresh(ctx context.Context, config Config) Result {
	if !config.Enabled {
		return Result{OK: true, Message: "obsidian integration disabled"}
	}

	baseURL, err := parseBaseURL(config.BaseURL)
	if err != nil {
		return Result{OK: false, Message: fmt.Sprintf("obsidian is not reachable: %v", err)}
	}
	client := config.Client
	ownsClient := client == nil
	if client == nil {
		client = newHTTPClient(config.VerifyTLS)
	}
	if ownsClient {
		defer client.CloseIdleConnections()
	}
	apiKey := config.APIKey
	if apiKey == "" {
		apiKey = os.Getenv(apiKeyEnv)
	}

	authenticated, result := health(ctx, client, baseURL, apiKey)
	if !result.OK {
		return result
	}
	if !config.ReloadCommand {
		return Result{OK: true, Message: "obsidian is reachable; relying on its file watcher"}
	}
	if !authenticated {
		return Result{OK: false, Message: "obsidian reload requires an authenticated API key"}
	}
	return reload(ctx, client, baseURL, apiKey)
}

func parseBaseURL(raw string) (*url.URL, error) {
	if raw == "" {
		raw = "https://127.0.0.1:27124"
	}
	parsed, err := url.Parse(strings.TrimRight(raw, "/"))
	if err != nil {
		return nil, fmt.Errorf("parse base url: %w", err)
	}
	if parsed.Scheme == "" || parsed.Host == "" {
		return nil, fmt.Errorf("base url must include scheme and host")
	}
	return parsed, nil
}

func newHTTPClient(verifyTLS bool) *http.Client {
	transport := http.DefaultTransport.(*http.Transport).Clone()
	if !verifyTLS {
		transport.TLSClientConfig = &tls.Config{InsecureSkipVerify: true}
	}
	return &http.Client{
		Timeout:   5 * time.Second,
		Transport: transport,
	}
}

func health(ctx context.Context, client *http.Client, baseURL *url.URL, apiKey string) (bool, Result) {
	var payload struct {
		Authenticated bool `json:"authenticated"`
	}
	if err := doJSON(ctx, client, http.MethodGet, baseURL.JoinPath("/").String(), apiKey, &payload); err != nil {
		return false, Result{OK: false, Message: fmt.Sprintf("obsidian is not reachable: %v", err)}
	}
	return payload.Authenticated, Result{OK: true, Message: "obsidian is reachable"}
}

func reload(ctx context.Context, client *http.Client, baseURL *url.URL, apiKey string) Result {
	var payload struct {
		Commands []struct {
			ID string `json:"id"`
		} `json:"commands"`
	}
	if err := doJSON(ctx, client, http.MethodGet, endpoint(baseURL, "/commands/"), apiKey, &payload); err != nil {
		return Result{OK: false, Message: fmt.Sprintf("obsidian command list failed: %v", err)}
	}
	hasReload := false
	for _, command := range payload.Commands {
		if command.ID == ReloadCommandID {
			hasReload = true
			break
		}
	}
	if !hasReload {
		return Result{OK: false, Message: fmt.Sprintf("obsidian command %s is unavailable", ReloadCommandID)}
	}
	if err := doJSON(ctx, client, http.MethodPost, endpoint(baseURL, "/commands/"+ReloadCommandID+"/"), apiKey, nil); err != nil {
		return Result{OK: false, Message: fmt.Sprintf("obsidian reload failed: %v", err)}
	}
	return Result{OK: true, Message: "obsidian reloaded"}
}

func endpoint(baseURL *url.URL, suffix string) string {
	next := *baseURL
	next.Path = strings.TrimRight(baseURL.Path, "/") + suffix
	return next.String()
}

func doJSON(ctx context.Context, client *http.Client, method string, endpoint string, apiKey string, out any) error {
	req, err := http.NewRequestWithContext(ctx, method, endpoint, nil)
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Accept", "application/json")
	if apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+apiKey)
	}
	resp, err := client.Do(req)
	if err != nil {
		return fmt.Errorf("%s %s: %w", method, endpoint, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		return fmt.Errorf("%s %s: status %d", method, endpoint, resp.StatusCode)
	}
	if out == nil {
		_, err := io.Copy(io.Discard, resp.Body)
		if err != nil {
			return fmt.Errorf("discard response body: %w", err)
		}
		return nil
	}
	if err := json.NewDecoder(resp.Body).Decode(out); err != nil {
		return fmt.Errorf("decode response: %w", err)
	}
	if _, err := io.Copy(io.Discard, resp.Body); err != nil {
		return fmt.Errorf("discard response body: %w", err)
	}
	return nil
}
