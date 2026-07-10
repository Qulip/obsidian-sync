.PHONY: build-agent build-agent-all clean-agent-builds

AGENT_PKG := ./cmd/obsidian-sync-agent
BUILD_DIR := .omo/evidence/builds
GO_BUILD_FLAGS := -trimpath -ldflags "-s -w"

build-agent:
	mkdir -p $(BUILD_DIR)
	go build $(GO_BUILD_FLAGS) -o $(BUILD_DIR)/obsidian-sync-agent $(AGENT_PKG)

build-agent-all:
	mkdir -p $(BUILD_DIR)
	CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 go build $(GO_BUILD_FLAGS) -o $(BUILD_DIR)/obsidian-sync-agent-darwin-arm64 $(AGENT_PKG)
	CGO_ENABLED=0 GOOS=darwin GOARCH=amd64 go build $(GO_BUILD_FLAGS) -o $(BUILD_DIR)/obsidian-sync-agent-darwin-amd64 $(AGENT_PKG)
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build $(GO_BUILD_FLAGS) -o $(BUILD_DIR)/obsidian-sync-agent-linux-amd64 $(AGENT_PKG)
	CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build $(GO_BUILD_FLAGS) -o $(BUILD_DIR)/obsidian-sync-agent-linux-arm64 $(AGENT_PKG)
	CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build $(GO_BUILD_FLAGS) -o $(BUILD_DIR)/obsidian-sync-agent-windows-amd64.exe $(AGENT_PKG)

clean-agent-builds:
	rm -rf $(BUILD_DIR)
