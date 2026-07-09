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

func IsConflictFile(relPath string) bool {
	name := path.Base(relPath)
	return matchPattern("*.conflict.*.md", name) ||
		matchPattern("*.sync-conflict*.md", name)
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

func ShouldSync(relPath string) bool {
	if !strings.HasSuffix(relPath, ".md") {
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
