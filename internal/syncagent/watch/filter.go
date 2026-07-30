package watch

import (
	"strings"

	"github.com/Qulip/obsidian-sync/internal/syncagent/rules"
)

// IsRelevantPath reports whether a vault-relative filesystem event should
// wake sync. It reuses the same exclusion rules as the sync engine itself
// (rules.ShouldSync / rules.IsIgnoredDir) so watch mode never treats a path
// as relevant that the engine would then refuse to push or pull — in
// particular hidden directories, .obsidian-sync-agent/, and conflict/backup
// files are always excluded. Mirrors
// obsidian_sync.sync_agent.watch.is_relevant_path.
func IsRelevantPath(relPath string, isDirectory bool, syncAttachments bool) bool {
	segments := pathSegments(relPath)
	if len(segments) == 0 {
		return false
	}
	if isDirectory {
		for _, segment := range segments {
			if rules.IsIgnoredDir(segment) {
				return false
			}
		}
		return true
	}
	return rules.ShouldSync(relPath, syncAttachments)
}

func pathSegments(relPath string) []string {
	var segments []string
	for _, segment := range strings.Split(relPath, "/") {
		if segment != "" {
			segments = append(segments, segment)
		}
	}
	return segments
}
