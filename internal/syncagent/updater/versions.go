package updater

import (
	"strconv"
	"strings"
)

func isNewer(latest string, current string) bool {
	if current == "dev" {
		return true
	}
	latestParts, latestOK := versionParts(latest)
	currentParts, currentOK := versionParts(current)
	if !latestOK || !currentOK {
		return latest != current
	}
	for index := range latestParts {
		if latestParts[index] != currentParts[index] {
			return latestParts[index] > currentParts[index]
		}
	}
	return false
}

func versionParts(value string) ([3]int, bool) {
	var parts [3]int
	segments := strings.Split(strings.TrimPrefix(value, "v"), ".")
	if len(segments) != len(parts) {
		return parts, false
	}
	for index, segment := range segments {
		parsed, err := strconv.Atoi(segment)
		if err != nil || parsed < 0 {
			return parts, false
		}
		parts[index] = parsed
	}
	return parts, true
}
