package client

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strconv"
	"strings"
	"time"
)

const DefaultTimeout = 30 * time.Second

type Client struct {
	baseURL string
	token   string
	http    *http.Client
}

func New(baseURL string, token string, timeout time.Duration) (*Client, error) {
	parsed, err := url.Parse(strings.TrimRight(baseURL, "/"))
	if err != nil {
		return nil, fmt.Errorf("parse sync base url: %w", err)
	}
	if parsed.Scheme == "" || parsed.Host == "" {
		return nil, fmt.Errorf("parse sync base url: %w", ErrAPI)
	}
	if timeout == 0 {
		timeout = DefaultTimeout
	}
	return &Client{
		baseURL: parsed.String(),
		token:   token,
		http:    &http.Client{Timeout: timeout},
	}, nil
}

func EncodeVaultPath(path string) string {
	segments := strings.Split(path, "/")
	for index, segment := range segments {
		segments[index] = url.PathEscape(segment)
	}
	return strings.Join(segments, "/")
}

func (c *Client) RegisterDevice(
	ctx context.Context,
	vaultID string,
	body RegisterDeviceRequest,
) (RegisterDeviceData, error) {
	path := "/vaults/" + url.PathEscape(vaultID) + "/sync/devices"
	request := bodyRequest[RegisterDeviceRequest]{c, requestSpec{method: http.MethodPost, path: path}, body}
	return sendWithBody[RegisterDeviceData](ctx, request)
}

func (c *Client) GetChanges(
	ctx context.Context,
	vaultID string,
	query ChangesRequest,
) (SyncChangesData, error) {
	values := url.Values{}
	values.Set("since", strconv.Itoa(query.Since))
	limit := query.Limit
	if limit == 0 {
		limit = DefaultPageLimit
	}
	values.Set("limit", strconv.Itoa(limit))
	if query.DeviceID != "" {
		values.Set("device_id", query.DeviceID)
	}
	path := "/vaults/" + url.PathEscape(vaultID) + "/sync/changes"
	return sendNoBody[SyncChangesData](ctx, c, requestSpec{
		method: http.MethodGet,
		path:   path,
		query:  values,
	})
}

func (c *Client) GetStatus(
	ctx context.Context,
	vaultID string,
	query StatusRequest,
) (SyncStatusData, error) {
	values := url.Values{}
	if query.DeviceID != "" {
		values.Set("device_id", query.DeviceID)
	}
	path := "/vaults/" + url.PathEscape(vaultID) + "/sync/status"
	return sendNoBody[SyncStatusData](ctx, c, requestSpec{
		method: http.MethodGet,
		path:   path,
		query:  values,
	})
}

func (c *Client) GetFile(ctx context.Context, ref FileRef) (FileContentData, error) {
	path := filePath(ref)
	return sendNoBody[FileContentData](ctx, c, requestSpec{
		method: http.MethodGet,
		path:   path,
	})
}

func (c *Client) PutFile(
	ctx context.Context,
	ref FileRef,
	body PutFileRequest,
) (PutFileData, error) {
	request := bodyRequest[PutFileRequest]{c, requestSpec{method: http.MethodPut, path: filePath(ref)}, body}
	return sendWithBody[PutFileData](ctx, request)
}

func (c *Client) DeleteFile(
	ctx context.Context,
	ref FileRef,
	body DeleteFileRequest,
) (DeleteFileData, error) {
	request := bodyRequest[DeleteFileRequest]{c, requestSpec{method: http.MethodDelete, path: filePath(ref)}, body}
	return sendWithBody[DeleteFileData](ctx, request)
}

func filePath(ref FileRef) string {
	return "/vaults/" + url.PathEscape(ref.VaultID) + "/files/" + EncodeVaultPath(ref.Path)
}

type requestSpec struct {
	method string
	path   string
	query  url.Values
}

type bodyRequest[B any] struct {
	client *Client
	spec   requestSpec
	value  B
}

func sendNoBody[T any](ctx context.Context, c *Client, spec requestSpec) (T, error) {
	var zero T
	req, err := http.NewRequestWithContext(ctx, spec.method, c.requestURL(spec), nil)
	if err != nil {
		return zero, fmt.Errorf("build sync request: %w", err)
	}
	return do[T](c, req)
}

func sendWithBody[T any, B any](ctx context.Context, request bodyRequest[B]) (T, error) {
	var zero T
	body, err := encodeBody(request.value)
	if err != nil {
		return zero, err
	}
	req, err := http.NewRequestWithContext(
		ctx,
		request.spec.method,
		request.client.requestURL(request.spec),
		body,
	)
	if err != nil {
		return zero, fmt.Errorf("build sync request: %w", err)
	}
	req.Header.Set("Content-Type", "application/json")
	return do[T](request.client, req)
}

func do[T any](c *Client, req *http.Request) (T, error) {
	req.Header.Set("Accept", "application/json")
	if c.token != "" {
		req.Header.Set("Authorization", "Bearer "+c.token)
	}
	var zero T
	resp, err := c.http.Do(req)
	if err != nil {
		return zero, fmt.Errorf("send sync request: %w", err)
	}
	defer closeBody(resp.Body)
	return parseEnvelope[T](resp)
}

func closeBody(body io.ReadCloser) {
	_, _ = io.Copy(io.Discard, body)
	_ = body.Close()
}

func encodeBody[T any](bodyValue T) (*bytes.Buffer, error) {
	var body bytes.Buffer
	if err := json.NewEncoder(&body).Encode(bodyValue); err != nil {
		return nil, fmt.Errorf("encode sync request body: %w", err)
	}
	return &body, nil
}

func (c *Client) requestURL(spec requestSpec) string {
	if len(spec.query) == 0 {
		return c.baseURL + spec.path
	}
	return c.baseURL + spec.path + "?" + spec.query.Encode()
}

type envelope struct {
	Success bool            `json:"success"`
	Data    json.RawMessage `json:"data"`
	Error   *errorEnvelope  `json:"error"`
}

type errorEnvelope struct {
	Code    string                     `json:"code"`
	Message string                     `json:"message"`
	Details map[string]json.RawMessage `json:"details"`
}

func parseEnvelope[T any](resp *http.Response) (T, error) {
	var out T
	var payload envelope
	if err := json.NewDecoder(resp.Body).Decode(&payload); err != nil {
		return out, &APIError{
			Message:    fmt.Sprintf("server returned a non-JSON response (status %d)", resp.StatusCode),
			StatusCode: resp.StatusCode,
			Details:    map[string]json.RawMessage{},
		}
	}
	if payload.Success {
		if len(payload.Data) == 0 || string(payload.Data) == "null" {
			return out, nil
		}
		if err := json.Unmarshal(payload.Data, &out); err != nil {
			return out, &APIError{
				Message:    "server returned an unexpected response shape",
				StatusCode: resp.StatusCode,
				Details:    map[string]json.RawMessage{},
			}
		}
		return out, nil
	}
	return out, envelopeError(resp.StatusCode, payload.Error)
}

func envelopeError(statusCode int, payload *errorEnvelope) error {
	if payload == nil {
		return &APIError{
			Message:    "sync request failed",
			StatusCode: statusCode,
			Details:    map[string]json.RawMessage{},
		}
	}
	message := payload.Message
	if message == "" {
		message = "sync request failed"
	}
	details := payload.Details
	if details == nil {
		details = map[string]json.RawMessage{}
	}
	if payload.Code == syncConflictCode {
		return &ConflictError{
			Message:    message,
			StatusCode: statusCode,
			Details:    details,
		}
	}
	return &APIError{
		Message:    message,
		Code:       payload.Code,
		StatusCode: statusCode,
		Details:    details,
	}
}
