#!/usr/bin/env bash

set -euo pipefail

readonly SKILL_NAME='knowledge-management'
readonly RELEASE_TAG='v1.0.0'
readonly RELEASE_URL="https://github.com/Qulip/obsidian-sync/releases/download/$RELEASE_TAG"
readonly SKILLS_ARCHIVE='obsidian-sync-skills.tar.gz'

INSTALLED_SKILLS=()
MCP_URL=''
MCP_TOKEN=''
SKILL_SOURCE=''
TEMP_DIR=''

usage() {
  cat <<'EOF'
Usage: ./install.sh

Downloads and installs obsisync from release v1.0.0 for the current user,
optionally installs the knowledge-management skill, and collects MCP settings.

Environment:
  OBSIDIAN_SYNC_AGENT_INSTALL_DIR  Override the obsisync install directory.
  OBSIDIAN_SYNC_URL                Default MCP server URL.
  KNOWLEDGE_API_TOKEN              Default MCP DB API token.
EOF
}

fatal() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

confirm() {
  local response
  read -r -p "$1 [y/N] > " response
  [[ "$response" == 'y' || "$response" == 'Y' || "$response" == 'yes' || "$response" == 'YES' ]]
}

require_release_tools() {
  command -v curl >/dev/null 2>&1 || fatal 'curl is required to download the release.'
  command -v tar >/dev/null 2>&1 || fatal 'tar is required to extract the release.'
  command -v sha256sum >/dev/null 2>&1 || command -v shasum >/dev/null 2>&1 || \
    fatal 'sha256sum or shasum is required to verify the release.'
}

release_asset() {
  local os architecture
  case "$(uname -s)" in
    Darwin) os='darwin' ;;
    Linux) os='linux' ;;
    *) fatal "unsupported operating system: $(uname -s)" ;;
  esac
  case "$(uname -m)" in
    arm64|aarch64) architecture='arm64' ;;
    x86_64|amd64) architecture='amd64' ;;
    *) fatal "unsupported architecture: $(uname -m)" ;;
  esac
  printf 'obsisync-%s-%s.tar.gz' "$os" "$architecture"
}

download_release_asset() {
  local asset="$1"
  local destination="$2"
  curl --fail --location --silent --show-error --output "$destination" "$RELEASE_URL/$asset"
}

verify_checksum() {
  local asset="$1"
  local file="$2"
  local expected actual
  case "$asset" in
    obsidian-sync-skills.tar.gz) expected='a30b9a9cd57a328c6d9f2d66535287a9b3c7e4f6dae7171bb608ff04dae380a8' ;;
    obsisync-darwin-amd64.tar.gz) expected='55901f3fc6ed4446a7512ff62cf28825d2578a8e8034e3d89eec3e81b1a95179' ;;
    obsisync-darwin-arm64.tar.gz) expected='5e709e126b5288417c8d21325ffbd50e1bb53ac0603d3e2988ba0c7b4ded468a' ;;
    obsisync-linux-amd64.tar.gz) expected='93e70170d4ef284e9abc66219b0709dfeb0ea5d0fa2936b2d2295486a488b4ee' ;;
    obsisync-linux-arm64.tar.gz) expected='b8eac8f800a2e93152f9ff0c1aa6fb72ff53fc4fc8713d93f4c6c8566c4e05cf' ;;
    *) fatal "checksum was not configured for $asset" ;;
  esac

  if command -v sha256sum >/dev/null 2>&1; then
    actual="$(sha256sum "$file" | awk '{ print $1 }')"
  else
    actual="$(shasum -a 256 "$file" | awk '{ print $1 }')"
  fi
  [[ "$actual" == "$expected" ]] || fatal "checksum verification failed for $asset"
}

ensure_skill_source() {
  [[ -n "$SKILL_SOURCE" ]] && return

  local archive="$TEMP_DIR/$SKILLS_ARCHIVE"
  local skill_root="$TEMP_DIR/skills"
  download_release_asset "$SKILLS_ARCHIVE" "$archive"
  verify_checksum "$SKILLS_ARCHIVE" "$archive"
  mkdir -p "$skill_root"
  tar -xzf "$archive" -C "$skill_root"
  SKILL_SOURCE="$skill_root/SKILLS/$SKILL_NAME"
  [[ -f "$SKILL_SOURCE/SKILL.md" ]] || fatal "skill source was not found in $SKILLS_ARCHIVE"
}

install_agent() {
  local install_dir="${OBSIDIAN_SYNC_AGENT_INSTALL_DIR:-$HOME/.local/bin}"
  local agent_path="$install_dir/obsisync"
  local asset binary archive temporary_path

  asset="$(release_asset)"
  binary="${asset%.tar.gz}"
  archive="$TEMP_DIR/$asset"
  download_release_asset "$asset" "$archive"
  verify_checksum "$asset" "$archive"
  tar -xzf "$archive" -C "$TEMP_DIR"
  [[ -f "$TEMP_DIR/$binary" ]] || fatal "release archive did not contain $binary"

  mkdir -p "$install_dir"
  temporary_path="$(mktemp "$install_dir/.obsisync.XXXXXX")"
  trap 'rm -f "$temporary_path"' RETURN
  cp "$TEMP_DIR/$binary" "$temporary_path"
  chmod +x "$temporary_path"
  mv -f "$temporary_path" "$agent_path"
  trap - RETURN

  printf '  obsisync installed: %s\n' "$agent_path"
  case ":$PATH:" in
    *":$install_dir:"*) ;;
    *) printf '  Add this directory to PATH if needed: %s\n' "$install_dir" ;;
  esac
}

select_agents() {
  cat <<'EOF'

Install the knowledge-management skill for coding agents (comma-separated):
  1. Claude Code
  2. Gemini CLI
  3. Codex CLI (OpenAI)
  4. Antigravity
  5. Cursor
  6. Windsurf
Leave blank to skip skill installation.
EOF

  local raw choice
  read -r -p 'Selection > ' raw
  [[ -n "$raw" ]] || return 0
  IFS=',' read -r -a SELECTED_AGENTS <<< "$raw"
  for choice in "${SELECTED_AGENTS[@]}"; do
    choice="${choice//[[:space:]]/}"
    [[ -z "$choice" ]] && continue
    install_skill "$choice"
  done
}

install_skill() {
  local choice="$1"
  local target=''
  local name=''

  case "$choice" in
    1) name='Claude Code'; target="$HOME/.claude/skills/$SKILL_NAME" ;;
    2) name='Gemini CLI'; target="$HOME/.gemini/skills/$SKILL_NAME" ;;
    3) name='Codex CLI (OpenAI)'; target="$HOME/.codex/skills/$SKILL_NAME" ;;
    4) name='Antigravity'; target="$HOME/.gemini/antigravity/skills/$SKILL_NAME" ;;
    5) name='Cursor'; target="$HOME/.cursor/skills/$SKILL_NAME" ;;
    6) name='Windsurf'; target="$HOME/.windsurf/skills/$SKILL_NAME" ;;
    *) printf '  warning: ignoring unknown selection: %s\n' "$choice" >&2; return ;;
  esac

  ensure_skill_source
  rm -rf "$target"
  mkdir -p "$(dirname "$target")"
  cp -R "$SKILL_SOURCE" "$target"
  INSTALLED_SKILLS+=("$name: $target")
  printf '  Skill installed for %s: %s\n' "$name" "$target"
}

collect_mcp_settings() {
  local value
  printf '\nMCP connection settings (leave blank to retain an existing environment value).\n'

  read -r -p '  OBSIDIAN_SYNC_URL (for example, https://sync.example.com) > ' value
  MCP_URL="${value:-${OBSIDIAN_SYNC_URL:-}}"

  read -r -s -p '  KNOWLEDGE_API_TOKEN (DB API token; not an admin token) > ' value
  printf '\n'
  MCP_TOKEN="${value:-${KNOWLEDGE_API_TOKEN:-}}"
}

persist_mcp_settings() {
  if [[ -z "$MCP_URL" && -z "$MCP_TOKEN" ]]; then
    return
  fi

  printf '\nMCP settings should normally be injected as secrets by the MCP client.\n'
  if ! confirm 'Save the supplied values in a user-only environment file?'; then
    return
  fi

  local config_dir="$HOME/.config/obsidian-sync"
  local env_file="$config_dir/mcp.env"
  local temporary_env
  local profile="$HOME/.bashrc"
  local source_line="[ -f \"$env_file\" ] && . \"$env_file\""
  case "${SHELL:-}" in
    */zsh) profile="$HOME/.zshrc" ;;
    */fish)
      profile="$HOME/.config/fish/config.fish"
      env_file="$config_dir/mcp.fish"
      source_line="test -f \"$env_file\"; and source \"$env_file\""
      ;;
    *) [[ -e "$profile" || ! -e "$HOME/.bash_profile" ]] || profile="$HOME/.bash_profile" ;;
  esac

  [[ ! -L "$config_dir" ]] || fatal "refusing to use symbolic-link config directory: $config_dir"
  mkdir -p -m 700 "$config_dir"
  chmod 700 "$config_dir"
  [[ ! -L "$env_file" ]] || fatal "refusing to use symbolic-link environment file: $env_file"
  temporary_env="$(mktemp "$config_dir/.mcp.env.XXXXXX")"
  trap 'rm -f "$temporary_env"' RETURN
  {
    if [[ "${SHELL:-}" == */fish ]]; then
      command -v fish >/dev/null 2>&1 || fatal 'fish must be installed to persist settings for a fish shell.'
      [[ -z "$MCP_URL" ]] || printf 'set -gx OBSIDIAN_SYNC_URL %s\n' "$(printf '%s\0' "$MCP_URL" | fish -c 'read -z value; string escape -- "$value"')"
      [[ -z "$MCP_TOKEN" ]] || printf 'set -gx KNOWLEDGE_API_TOKEN %s\n' "$(printf '%s\0' "$MCP_TOKEN" | fish -c 'read -z value; string escape -- "$value"')"
    else
      [[ -z "$MCP_URL" ]] || printf 'export OBSIDIAN_SYNC_URL=%q\n' "$MCP_URL"
      [[ -z "$MCP_TOKEN" ]] || printf 'export KNOWLEDGE_API_TOKEN=%q\n' "$MCP_TOKEN"
    fi
  } > "$temporary_env"
  chmod 600 "$temporary_env"
  mv -f "$temporary_env" "$env_file"
  trap - RETURN
  mkdir -p "$(dirname "$profile")"
  if ! grep -Fqx "$source_line" "$profile" 2>/dev/null; then
    printf '\n%s\n' "$source_line" >> "$profile"
  fi
  printf '  MCP settings saved to %s and loaded by %s. Start a new shell before using them.\n' \
    "$env_file" "$profile"
}

print_summary() {
  printf '\nInstallation complete.\n'
  if (( ${#INSTALLED_SKILLS[@]} > 0 )); then
    printf 'Installed skills:\n'
    printf '  %s\n' "${INSTALLED_SKILLS[@]}"
  fi

  printf 'MCP settings: OBSIDIAN_SYNC_URL=%s, KNOWLEDGE_API_TOKEN=%s\n' \
    "$([[ -n "$MCP_URL" ]] && printf 'provided' || printf 'not provided')" \
    "$([[ -n "$MCP_TOKEN" ]] && printf 'provided' || printf 'not provided')"

  if [[ -n "$MCP_URL" ]]; then
    printf 'MCP endpoint: %s/mcp\n' "${MCP_URL%/}"
    printf 'MCP header: Authorization: Bearer $KNOWLEDGE_API_TOKEN\n'
  fi
}

main() {
  case "${1:-}" in
    '') ;;
    -h|--help) usage; return ;;
    *) fatal "unknown option: $1" ;;
  esac

  require_release_tools
  TEMP_DIR="$(mktemp -d)"
  trap 'rm -rf "$TEMP_DIR"' EXIT
  install_agent
  select_agents
  collect_mcp_settings
  persist_mcp_settings
  print_summary
}

main "$@"
