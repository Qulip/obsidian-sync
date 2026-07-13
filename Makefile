.PHONY: build-agent build-agent-all clean-agent-builds

AGENT_PKG := ./cmd/obsidian-sync-agent
BUILD_DIR := dist/obsisync
GO_BUILD_FLAGS := -trimpath -ldflags "-s -w"

build-agent:
	mkdir -p $(BUILD_DIR)
	go build $(GO_BUILD_FLAGS) -o $(BUILD_DIR)/obsisync $(AGENT_PKG)

build-agent-all:
	mkdir -p $(BUILD_DIR)
	CGO_ENABLED=0 GOOS=darwin GOARCH=arm64 go build $(GO_BUILD_FLAGS) -o $(BUILD_DIR)/obsisync-darwin-arm64 $(AGENT_PKG)
	CGO_ENABLED=0 GOOS=darwin GOARCH=amd64 go build $(GO_BUILD_FLAGS) -o $(BUILD_DIR)/obsisync-darwin-amd64 $(AGENT_PKG)
	CGO_ENABLED=0 GOOS=linux GOARCH=amd64 go build $(GO_BUILD_FLAGS) -o $(BUILD_DIR)/obsisync-linux-amd64 $(AGENT_PKG)
	CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go build $(GO_BUILD_FLAGS) -o $(BUILD_DIR)/obsisync-linux-arm64 $(AGENT_PKG)
	CGO_ENABLED=0 GOOS=windows GOARCH=amd64 go build $(GO_BUILD_FLAGS) -o $(BUILD_DIR)/obsisync-windows-amd64.exe $(AGENT_PKG)

clean-agent-builds:
	rm -rf $(BUILD_DIR)
