package updater

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestUpdate_replacesExecutable_whenUserConfirms(t *testing.T) {
	// Given
	archive := unixArchive(t, "obsisync-linux-amd64", []byte("new executable"))
	server := releaseServer(t, "v1.0.1", "obsisync-linux-amd64.tar.gz", archive)
	defer server.Close()
	executable := writeExecutable(t, []byte("old executable"))
	var output bytes.Buffer

	// When
	result, err := Update(context.Background(), Options{
		CurrentVersion:  "v1.0.0",
		ExecutablePath:  executable,
		ReleaseEndpoint: server.URL + "/latest",
		Input:           strings.NewReader("y\n"),
		Output:          &output,
		Platform:        Platform{OS: "linux", Architecture: "amd64"},
	})

	// Then
	if err != nil {
		t.Fatalf("Update() error = %v", err)
	}
	if result.State != StateUpdated {
		t.Fatalf("result.State = %q, want %q", result.State, StateUpdated)
	}
	contents, err := os.ReadFile(executable)
	if err != nil {
		t.Fatalf("ReadFile() error = %v", err)
	}
	if string(contents) != "new executable" {
		t.Fatalf("executable contents = %q", contents)
	}
	if !strings.Contains(output.String(), "Update available: v1.0.0 -> v1.0.1") {
		t.Fatalf("output = %q", output.String())
	}
}

func TestUpdate_keepsExecutable_whenUserDeclines(t *testing.T) {
	// Given
	archive := unixArchive(t, "obsisync-linux-amd64", []byte("new executable"))
	server := releaseServer(t, "v1.0.1", "obsisync-linux-amd64.tar.gz", archive)
	defer server.Close()
	executable := writeExecutable(t, []byte("old executable"))

	// When
	result, err := Update(context.Background(), Options{
		CurrentVersion:  "v1.0.0",
		ExecutablePath:  executable,
		ReleaseEndpoint: server.URL + "/latest",
		Input:           strings.NewReader("n\n"),
		Output:          io.Discard,
		Platform:        Platform{OS: "linux", Architecture: "amd64"},
	})

	// Then
	if err != nil {
		t.Fatalf("Update() error = %v", err)
	}
	if result.State != StateDeclined {
		t.Fatalf("result.State = %q, want %q", result.State, StateDeclined)
	}
	contents, err := os.ReadFile(executable)
	if err != nil {
		t.Fatalf("ReadFile() error = %v", err)
	}
	if string(contents) != "old executable" {
		t.Fatalf("executable contents = %q", contents)
	}
}

func TestUpdate_reportsUpToDate_whenLatestVersionMatches(t *testing.T) {
	// Given
	server := releaseServer(t, "v1.0.0", "obsisync-linux-amd64.tar.gz", []byte("unused"))
	defer server.Close()
	var output bytes.Buffer

	// When
	result, err := Update(context.Background(), Options{
		CurrentVersion:  "v1.0.0",
		ExecutablePath:  filepath.Join(t.TempDir(), "obsisync"),
		ReleaseEndpoint: server.URL + "/latest",
		Input:           strings.NewReader("y\n"),
		Output:          &output,
		Platform:        Platform{OS: "linux", Architecture: "amd64"},
	})

	// Then
	if err != nil {
		t.Fatalf("Update() error = %v", err)
	}
	if result.State != StateUpToDate {
		t.Fatalf("result.State = %q, want %q", result.State, StateUpToDate)
	}
	if !strings.Contains(output.String(), "obsisync is up to date (v1.0.0)") {
		t.Fatalf("output = %q", output.String())
	}
}

func TestUpdate_returnsError_whenAssetDigestDoesNotMatch(t *testing.T) {
	// Given
	archive := unixArchive(t, "obsisync-linux-amd64", []byte("new executable"))
	server := releaseServerWithDigest(t, "v1.0.1", "obsisync-linux-amd64.tar.gz", archive, strings.Repeat("0", 64))
	defer server.Close()
	executable := writeExecutable(t, []byte("old executable"))

	// When
	_, err := Update(context.Background(), Options{
		CurrentVersion:  "v1.0.0",
		ExecutablePath:  executable,
		ReleaseEndpoint: server.URL + "/latest",
		Input:           strings.NewReader("y\n"),
		Output:          io.Discard,
		Platform:        Platform{OS: "linux", Architecture: "amd64"},
	})

	// Then
	if err == nil || !strings.Contains(err.Error(), "checksum") {
		t.Fatalf("Update() error = %v, want checksum error", err)
	}
	contents, readErr := os.ReadFile(executable)
	if readErr != nil {
		t.Fatalf("ReadFile() error = %v", readErr)
	}
	if string(contents) != "old executable" {
		t.Fatalf("executable contents = %q", contents)
	}
}

func TestUpdate_returnsError_whenPlatformIsUnsupported(t *testing.T) {
	// Given
	server := releaseServer(t, "v1.0.1", "obsisync-windows-amd64.zip", []byte("unused"))
	defer server.Close()

	// When
	_, err := Update(context.Background(), Options{
		CurrentVersion:  "v1.0.0",
		ExecutablePath:  filepath.Join(t.TempDir(), "obsisync.exe"),
		ReleaseEndpoint: server.URL + "/latest",
		Input:           strings.NewReader("y\n"),
		Output:          io.Discard,
		Platform:        Platform{OS: "windows", Architecture: "arm64"},
	})

	// Then
	if err == nil || !strings.Contains(err.Error(), "unsupported platform") {
		t.Fatalf("Update() error = %v, want unsupported platform error", err)
	}
}

func releaseServer(t *testing.T, version string, assetName string, asset []byte) *httptest.Server {
	t.Helper()
	digest := sha256.Sum256(asset)
	return releaseServerWithDigest(t, version, assetName, asset, hex.EncodeToString(digest[:]))
}

func releaseServerWithDigest(t *testing.T, version string, assetName string, asset []byte, digest string) *httptest.Server {
	t.Helper()
	return httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		switch r.URL.Path {
		case "/latest":
			w.Header().Set("Content-Type", "application/json")
			if _, err := fmt.Fprintf(w, `{"tag_name":%q,"draft":false,"prerelease":false,"assets":[{"name":%q,"digest":%q,"browser_download_url":%q}]}`,
				version,
				assetName,
				"sha256:"+digest,
				"http://"+r.Host+"/asset",
			); err != nil {
				t.Fatalf("Fprintf() error = %v", err)
			}
		case "/asset":
			if _, err := w.Write(asset); err != nil {
				t.Fatalf("Write() error = %v", err)
			}
		default:
			http.NotFound(w, r)
		}
	}))
}

func unixArchive(t *testing.T, name string, contents []byte) []byte {
	t.Helper()
	var output bytes.Buffer
	gzipWriter := gzip.NewWriter(&output)
	tarWriter := tar.NewWriter(gzipWriter)
	if err := tarWriter.WriteHeader(&tar.Header{Name: name, Mode: 0o755, Size: int64(len(contents))}); err != nil {
		t.Fatalf("WriteHeader() error = %v", err)
	}
	if _, err := tarWriter.Write(contents); err != nil {
		t.Fatalf("Write() error = %v", err)
	}
	if err := tarWriter.Close(); err != nil {
		t.Fatalf("Close() error = %v", err)
	}
	if err := gzipWriter.Close(); err != nil {
		t.Fatalf("Close() error = %v", err)
	}
	return output.Bytes()
}

func writeExecutable(t *testing.T, contents []byte) string {
	t.Helper()
	path := filepath.Join(t.TempDir(), "obsisync")
	if err := os.WriteFile(path, contents, 0o755); err != nil {
		t.Fatalf("WriteFile() error = %v", err)
	}
	return path
}
