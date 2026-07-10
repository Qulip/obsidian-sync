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

	session := obsidianSession{
		client:  client,
		baseURL: baseURL,
		apiKey:  apiKey,
	}
	authenticated, result := session.health(ctx)
	if !result.OK {
		return result
	}
	if !config.ReloadCommand {
		return Result{OK: true, Message: "obsidian is reachable; relying on its file watcher"}
	}
	if !authenticated {
		return Result{OK: false, Message: "obsidian reload requires an authenticated API key"}
	}
	return session.reload(ctx)
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

type obsidianSession struct {
	client  *http.Client
	baseURL *url.URL
	apiKey  string
}

type jsonRequest struct {
	method   string
	endpoint string
}

func (s obsidianSession) health(ctx context.Context) (bool, Result) {
	var payload struct {
		Authenticated bool `json:"authenticated"`
	}
	err := s.doJSON(ctx, jsonRequest{
		method:   http.MethodGet,
		endpoint: s.baseURL.JoinPath("/").String(),
	}, &payload)
	if err != nil {
		return false, Result{OK: false, Message: fmt.Sprintf("obsidian is not reachable: %v", err)}
	}
	return payload.Authenticated, Result{OK: true, Message: "obsidian is reachable"}
}

func (s obsidianSession) reload(ctx context.Context) Result {
	var payload struct {
		Commands []struct {
			ID string `json:"id"`
		} `json:"commands"`
	}
	err := s.doJSON(ctx, jsonRequest{
		method:   http.MethodGet,
		endpoint: endpoint(s.baseURL, "/commands/"),
	}, &payload)
	if err != nil {
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
	err = s.doJSON(ctx, jsonRequest{
		method:   http.MethodPost,
		endpoint: endpoint(s.baseURL, "/commands/"+ReloadCommandID+"/"),
	}, nil)
	if err != nil {
		return Result{OK: false, Message: fmt.Sprintf("obsidian reload failed: %v", err)}
	}
	return Result{OK: true, Message: "obsidian reloaded"}
}

func endpoint(baseURL *url.URL, suffix string) string {
	next := *baseURL
	next.Path = strings.TrimRight(baseURL.Path, "/") + suffix
	return next.String()
}

func (s obsidianSession) doJSON(ctx context.Context, spec jsonRequest, out any) error {
	req, err := http.NewRequestWithContext(ctx, spec.method, spec.endpoint, nil)
	if err != nil {
		return fmt.Errorf("create request: %w", err)
	}
	req.Header.Set("Accept", "application/json")
	if s.apiKey != "" {
		req.Header.Set("Authorization", "Bearer "+s.apiKey)
	}
	resp, err := s.client.Do(req)
	if err != nil {
		return fmt.Errorf("%s %s: %w", spec.method, spec.endpoint, err)
	}
	defer resp.Body.Close()
	if resp.StatusCode < http.StatusOK || resp.StatusCode >= http.StatusMultipleChoices {
		return fmt.Errorf("%s %s: status %d", spec.method, spec.endpoint, resp.StatusCode)
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
