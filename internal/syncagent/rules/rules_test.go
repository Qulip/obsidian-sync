package rules

import "testing"

func TestShouldSync_matchesPythonIgnoreRules(t *testing.T) {
	tests := []struct {
		name            string
		path            string
		syncAttachments bool
		want            bool
	}{
		{name: "plain markdown", path: "notes/JPA.md", want: true},
		{name: "non markdown, attachments disabled", path: "notes/image.png", want: false},
		{name: "ds store", path: "notes/.DS_Store", want: false},
		{name: "thumbs db", path: "Thumbs.db", want: false},
		{name: "hidden file", path: "notes/.secret.md", want: false},
		{name: "hidden dir", path: ".obsidian/plugins/note.md", want: false},
		{name: "dotted conflict", path: "notes/JPA.conflict.dev.20260707-000000.md", want: false},
		{name: "sync conflict", path: "notes/JPA.sync-conflict-20260707.md", want: false},
		{name: "ignored trash", path: ".trash/old.md", want: false},
		{name: "markdown unaffected by attachments flag", path: "notes/JPA.md", syncAttachments: true, want: true},
		{name: "png, attachments enabled", path: "Images/photo.png", syncAttachments: true, want: true},
		{name: "jpg, attachments enabled", path: "Images/photo.jpg", syncAttachments: true, want: true},
		{name: "pdf, attachments enabled", path: "Docs/paper.pdf", syncAttachments: true, want: true},
		{name: "png, attachments disabled", path: "Images/photo.png", want: false},
		{name: "unsupported extension even with attachments enabled", path: "notes/archive.zip", syncAttachments: true, want: false},
		{name: "hidden dir attachment ignored", path: ".obsidian/icon.png", syncAttachments: true, want: false},
		{name: "attachment conflict file excluded", path: "Images/photo.conflict.dev.20260707-000000.png", syncAttachments: true, want: false},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// When
			got := ShouldSync(tt.path, tt.syncAttachments)

			// Then
			if got != tt.want {
				t.Fatalf("ShouldSync(%q, %v) = %v, want %v", tt.path, tt.syncAttachments, got, tt.want)
			}
		})
	}
}

func TestIgnoredAndConflictPredicates_matchPythonSyncRules(t *testing.T) {
	tests := []struct {
		name string
		got  bool
		want bool
	}{
		{name: "dot directory ignored", got: IsIgnoredDir(".obsidian"), want: true},
		{name: "hidden directory ignored", got: IsIgnoredDir(".git"), want: true},
		{name: "regular directory kept", got: IsIgnoredDir("notes"), want: false},
		{name: "root obsidian ignored", got: IsIgnoredPath(".obsidian/workspace.json"), want: true},
		{name: "sync agent ignored", got: IsIgnoredPath(".obsidian-sync-agent/config.json"), want: true},
		{name: "nested trash ignored", got: IsIgnoredPath("projects/.trash/old.md"), want: true},
		{name: "regular path not ignored", got: IsIgnoredPath("notes/deep/JPA.md"), want: false},
		{name: "dotted conflict matches", got: IsConflictFile("notes/JPA.conflict.laptop.20260707-000000.md"), want: true},
		{name: "sync conflict matches", got: IsConflictFile("notes/JPA.sync-conflict-20260707.md"), want: true},
		{name: "conflict dir only not conflict", got: IsConflictFile("conflict-notes/JPA.md"), want: false},
		{name: "plain markdown vectorizable", got: IsVectorizablePath("notes/JPA.md"), want: true},
		{name: "conflict not vectorizable", got: IsVectorizablePath("notes/JPA.conflict.dev.20260707-000000.md"), want: false},
		{name: "ignored not vectorizable", got: IsVectorizablePath(".obsidian/config.md"), want: false},
		{name: "png is attachment", got: IsAttachmentPath("Images/photo.png"), want: true},
		{name: "pdf is attachment", got: IsAttachmentPath("Docs/paper.pdf"), want: true},
		{name: "md is not attachment", got: IsAttachmentPath("notes/JPA.md"), want: false},
		{name: "zip is not attachment", got: IsAttachmentPath("notes/archive.zip"), want: false},
		{name: "attachment conflict matches", got: IsConflictFile("Images/photo.conflict.laptop.20260707-000000.png"), want: true},
		{name: "attachment sync-conflict matches", got: IsConflictFile("Images/photo.sync-conflict-20260707.jpg"), want: true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			// Then
			if tt.got != tt.want {
				t.Fatalf("got %v, want %v", tt.got, tt.want)
			}
		})
	}
}
