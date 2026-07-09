package client

import (
	"encoding/json"
	"errors"
	"fmt"
)

const syncConflictCode = "SYNC_CONFLICT"

var (
	ErrAPI      = errors.New("sync api error")
	ErrConflict = errors.New("sync conflict")
)

type APIError struct {
	Message    string
	Code       string
	StatusCode int
	Details    map[string]json.RawMessage
}

func (e *APIError) Error() string {
	if e.Code == "" {
		return e.Message
	}
	return fmt.Sprintf("%s: %s", e.Code, e.Message)
}

func (e *APIError) Is(target error) bool {
	return target == ErrAPI
}

type ConflictError struct {
	Message    string
	StatusCode int
	Details    map[string]json.RawMessage
}

func (e *ConflictError) Error() string {
	return e.Message
}

func (e *ConflictError) Is(target error) bool {
	return target == ErrConflict
}
