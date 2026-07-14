package updater

import (
	"archive/tar"
	"archive/zip"
	"compress/gzip"
	"fmt"
	"io"
	"os"
	"path/filepath"
)

func extractBinary(archivePath string, destinationPath string, archive string, platform Platform) error {
	want := binaryName(platform)
	if filepath.Ext(archive) == ".zip" {
		return extractZipBinary(archivePath, destinationPath, want)
	}
	return extractTarBinary(archivePath, destinationPath, want)
}

func binaryName(platform Platform) string {
	name := fmt.Sprintf("obsisync-%s-%s", platform.OS, platform.Architecture)
	if platform.OS == "windows" {
		return name + ".exe"
	}
	return name
}

func extractTarBinary(archivePath string, destinationPath string, want string) error {
	archive, err := os.Open(archivePath)
	if err != nil {
		return fmt.Errorf("open tar archive: %w", err)
	}
	defer archive.Close()
	gzipReader, err := gzip.NewReader(archive)
	if err != nil {
		return fmt.Errorf("open gzip archive: %w", err)
	}
	defer gzipReader.Close()
	tarReader := tar.NewReader(gzipReader)
	header, err := tarReader.Next()
	if err != nil {
		return fmt.Errorf("read tar archive: %w", err)
	}
	if header.Name != want || !header.FileInfo().Mode().IsRegular() {
		return fmt.Errorf("tar archive does not contain expected binary %q", want)
	}
	if err := writeExtractedBinary(destinationPath, tarReader); err != nil {
		return err
	}
	if _, err := tarReader.Next(); err != io.EOF {
		if err == nil {
			return fmt.Errorf("tar archive contains unexpected extra files")
		}
		return fmt.Errorf("read tar archive: %w", err)
	}
	return nil
}

func extractZipBinary(archivePath string, destinationPath string, want string) error {
	archive, err := zip.OpenReader(archivePath)
	if err != nil {
		return fmt.Errorf("open zip archive: %w", err)
	}
	defer archive.Close()
	if len(archive.File) != 1 || archive.File[0].Name != want || !archive.File[0].Mode().IsRegular() {
		return fmt.Errorf("zip archive does not contain only expected binary %q", want)
	}
	reader, err := archive.File[0].Open()
	if err != nil {
		return fmt.Errorf("open zip binary: %w", err)
	}
	defer reader.Close()
	return writeExtractedBinary(destinationPath, reader)
}

func writeExtractedBinary(destinationPath string, source io.Reader) error {
	destination, err := os.OpenFile(destinationPath, os.O_WRONLY|os.O_TRUNC, 0o755)
	if err != nil {
		return fmt.Errorf("open replacement binary: %w", err)
	}
	defer destination.Close()
	if _, err := io.Copy(destination, source); err != nil {
		return fmt.Errorf("extract replacement binary: %w", err)
	}
	return nil
}
