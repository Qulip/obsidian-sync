package updater

import (
	"context"
	"crypto/sha256"
	"crypto/subtle"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"os"
	"strings"
)

type release struct {
	TagName    string  `json:"tag_name"`
	Draft      bool    `json:"draft"`
	Prerelease bool    `json:"prerelease"`
	Assets     []asset `json:"assets"`
}

type asset struct {
	Name        string `json:"name"`
	Digest      string `json:"digest"`
	DownloadURL string `json:"browser_download_url"`
}

func fetchRelease(ctx context.Context, client *http.Client, endpoint string) (release, error) {
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, endpoint, nil)
	if err != nil {
		return release{}, fmt.Errorf("create release request: %w", err)
	}
	request.Header.Set("Accept", "application/vnd.github+json")
	response, err := client.Do(request)
	if err != nil {
		return release{}, fmt.Errorf("request latest release: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return release{}, fmt.Errorf("request latest release: unexpected status %s", response.Status)
	}
	var latest release
	if err := json.NewDecoder(response.Body).Decode(&latest); err != nil {
		return release{}, fmt.Errorf("decode latest release: %w", err)
	}
	if latest.TagName == "" {
		return release{}, fmt.Errorf("decode latest release: tag name is empty")
	}
	return latest, nil
}

func (r release) findAsset(platform Platform) (asset, error) {
	if platform.OS == "windows" && platform.Architecture != "amd64" {
		return asset{}, fmt.Errorf("unsupported platform %s/%s", platform.OS, platform.Architecture)
	}
	want := archiveName(platform)
	for _, candidate := range r.Assets {
		if candidate.Name == want {
			if candidate.DownloadURL == "" || candidate.Digest == "" {
				return asset{}, fmt.Errorf("release asset %q is incomplete", want)
			}
			return candidate, nil
		}
	}
	return asset{}, fmt.Errorf("release %q has no asset for %s/%s", r.TagName, platform.OS, platform.Architecture)
}

func archiveName(platform Platform) string {
	if platform.OS == "windows" {
		return "obsisync-windows-amd64.zip"
	}
	return fmt.Sprintf("obsisync-%s-%s.tar.gz", platform.OS, platform.Architecture)
}

func downloadAsset(ctx context.Context, client *http.Client, releaseAsset asset, destination *os.File) error {
	expected, err := digestBytes(releaseAsset.Digest)
	if err != nil {
		return fmt.Errorf("parse release asset digest: %w", err)
	}
	request, err := http.NewRequestWithContext(ctx, http.MethodGet, releaseAsset.DownloadURL, nil)
	if err != nil {
		return fmt.Errorf("create asset request: %w", err)
	}
	response, err := client.Do(request)
	if err != nil {
		return fmt.Errorf("download release asset: %w", err)
	}
	defer response.Body.Close()
	if response.StatusCode != http.StatusOK {
		return fmt.Errorf("download release asset: unexpected status %s", response.Status)
	}
	hash := sha256.New()
	if _, err := io.Copy(io.MultiWriter(destination, hash), response.Body); err != nil {
		return fmt.Errorf("write release asset: %w", err)
	}
	if subtle.ConstantTimeCompare(hash.Sum(nil), expected) != 1 {
		return fmt.Errorf("verify release asset checksum: mismatch")
	}
	return nil
}

func digestBytes(digest string) ([]byte, error) {
	const prefix = "sha256:"
	if !strings.HasPrefix(digest, prefix) {
		return nil, fmt.Errorf("unsupported digest %q", digest)
	}
	decoded, err := hex.DecodeString(strings.TrimPrefix(digest, prefix))
	if err != nil || len(decoded) != sha256.Size {
		return nil, fmt.Errorf("invalid sha256 digest")
	}
	return decoded, nil
}
