package rules

import (
	"path"
	"strings"
)

var ignoredSegments = map[string]struct{}{
	".obsidian":            {},
	".obsidian-sync-agent": {},
	".trash":               {},
}

// attachmentExtensions mirrors the server's domain/files.py allow-list for
// non-markdown content (images and PDFs).
var attachmentExtensions = map[string]struct{}{
	".png":  {},
	".jpg":  {},
	".jpeg": {},
	".gif":  {},
	".webp": {},
	".pdf":  {},
}

// conflictExtensions covers every extension a conflict file can be written
// with: markdown conflicts embed both versions in one .md file, attachment
// conflicts preserve the original extension since binary content can't be
// shown inline (see docs/sync-agent.md, "충돌 파일 (확장자 유지)").
var conflictExtensions = []string{".md", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf"}

func IsAttachmentPath(relPath string) bool {
	_, ok := attachmentExtensions[strings.ToLower(path.Ext(relPath))]
	return ok
}

func IsConflictFile(relPath string) bool {
	name := path.Base(relPath)
	for _, ext := range conflictExtensions {
		if matchPattern("*.conflict.*"+ext, name) || matchPattern("*.sync-conflict*"+ext, name) {
			return true
		}
	}
	return false
}

func IsIgnoredPath(relPath string) bool {
	for _, segment := range strings.Split(relPath, "/") {
		if segment == "" {
			continue
		}
		if _, ok := ignoredSegments[segment]; ok {
			return true
		}
	}
	return false
}

func IsVectorizablePath(relPath string) bool {
	return strings.HasSuffix(relPath, ".md") &&
		!IsConflictFile(relPath) &&
		!IsIgnoredPath(relPath)
}

func IsIgnoredDir(name string) bool {
	return strings.HasPrefix(name, ".") || IsIgnoredPath(name+"/")
}

func ShouldSync(relPath string, syncAttachments bool) bool {
	if !strings.HasSuffix(relPath, ".md") && !(syncAttachments && IsAttachmentPath(relPath)) {
		return false
	}
	segments := strings.Split(relPath, "/")
	hasSegment := false
	for _, segment := range segments {
		if segment == "" {
			continue
		}
		hasSegment = true
		if strings.HasPrefix(segment, ".") {
			return false
		}
	}
	return hasSegment && !IsConflictFile(relPath) && !IsIgnoredPath(relPath)
}

func matchPattern(pattern string, name string) bool {
	matched, err := path.Match(pattern, name)
	return err == nil && matched
}
