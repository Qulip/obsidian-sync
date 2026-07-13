[CmdletBinding()]
param(
    [switch]$Help
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$SkillName = 'knowledge-management'
$ReleaseTag = 'v1.0.0'
$ReleaseUrl = "https://github.com/Qulip/obsidian-sync/releases/download/$ReleaseTag"
$SkillsArchive = 'obsidian-sync-skills.tar.gz'
$ReleaseChecksums = @{
    'obsidian-sync-skills.tar.gz' = 'a30b9a9cd57a328c6d9f2d66535287a9b3c7e4f6dae7171bb608ff04dae380a8'
    'obsisync-darwin-amd64.tar.gz' = '55901f3fc6ed4446a7512ff62cf28825d2578a8e8034e3d89eec3e81b1a95179'
    'obsisync-darwin-arm64.tar.gz' = '5e709e126b5288417c8d21325ffbd50e1bb53ac0603d3e2988ba0c7b4ded468a'
    'obsisync-linux-amd64.tar.gz' = '93e70170d4ef284e9abc66219b0709dfeb0ea5d0fa2936b2d2295486a488b4ee'
    'obsisync-linux-arm64.tar.gz' = 'b8eac8f800a2e93152f9ff0c1aa6fb72ff53fc4fc8713d93f4c6c8566c4e05cf'
    'obsisync-windows-amd64.zip' = '3e664a50ab4d414c2ebee2261a4dda4a3aef974eccc91e68102644f041564ca8'
}
$SkillSource = $null
$TemporaryDirectory = $null
$InstalledSkills = [System.Collections.Generic.List[string]]::new()

function Show-Usage {
    @'
Usage: .\install.ps1

Downloads and installs obsisync from release v1.0.0 for the current user,
optionally installs the knowledge-management skill, and collects MCP settings.

Environment:
  OBSIDIAN_SYNC_AGENT_INSTALL_DIR  Override the obsisync install directory.
  OBSIDIAN_SYNC_URL                Default MCP server URL.
  KNOWLEDGE_API_TOKEN              Default MCP DB API token.
'@ | Write-Output
}

function Get-SecretInput([string]$Prompt) {
    $secure = Read-Host -Prompt $Prompt -AsSecureString
    $pointer = [IntPtr]::Zero
    try {
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        if ($pointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
    }
}

function Confirm([string]$Prompt) {
    $response = Read-Host -Prompt "$Prompt [y/N]"
    return $response -match '^(?i:y|yes)$'
}

function Download-ReleaseAsset([string]$Asset, [string]$Destination) {
    Invoke-WebRequest -Uri "$ReleaseUrl/$Asset" -OutFile $Destination
}

function Test-ReleaseChecksum([string]$Asset, [string]$Path) {
    $expected = $script:ReleaseChecksums[$Asset]
    if ($null -eq $expected -or $expected -notmatch '^[A-Fa-f0-9]{64}$') {
        throw "Checksum format is invalid for $Asset"
    }
    $actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash
    if ($actual -ne $expected.ToUpperInvariant()) {
        throw "Checksum verification failed for $Asset"
    }
}

function Get-SkillSource {
    if ($null -ne $script:SkillSource) {
        return $script:SkillSource
    }

    $tar = Get-Command tar -ErrorAction SilentlyContinue
    if ($null -eq $tar) {
        throw 'tar is required to extract the skills archive.'
    }
    $archive = Join-Path $script:TemporaryDirectory $script:SkillsArchive
    $skillsDirectory = Join-Path $script:TemporaryDirectory 'skills'
    Download-ReleaseAsset $script:SkillsArchive $archive
    Test-ReleaseChecksum $script:SkillsArchive $archive
    New-Item -ItemType Directory -Force -Path $skillsDirectory | Out-Null
    & $tar.Source -xzf $archive -C $skillsDirectory
    if ($LASTEXITCODE -ne 0) {
        throw 'Skills archive extraction failed.'
    }
    $script:SkillSource = Join-Path $skillsDirectory "SKILLS\\$script:SkillName"
    if (-not (Test-Path -LiteralPath (Join-Path $script:SkillSource 'SKILL.md'))) {
        throw "Skill source was not found in $script:SkillsArchive"
    }
    return $script:SkillSource
}

function Install-Agent {
    $installDirectory = if ($env:OBSIDIAN_SYNC_AGENT_INSTALL_DIR) {
        $env:OBSIDIAN_SYNC_AGENT_INSTALL_DIR
    }
    else {
        Join-Path $env:LOCALAPPDATA 'Programs\obsisync'
    }
    if ([Runtime.InteropServices.RuntimeInformation]::OSArchitecture -ne [Runtime.InteropServices.Architecture]::X64) {
        throw 'Release v1.0.0 supports Windows x64 only.'
    }
    $installDirectory = [IO.Path]::GetFullPath($installDirectory)
    if ($installDirectory.Contains([IO.Path]::PathSeparator)) {
        throw 'OBSIDIAN_SYNC_AGENT_INSTALL_DIR must not contain a PATH separator.'
    }
    $agentPath = Join-Path $installDirectory 'obsisync.exe'
    $temporaryPath = Join-Path $installDirectory "obsisync.$PID.tmp.exe"
    $asset = 'obsisync-windows-amd64.zip'
    $archive = Join-Path $script:TemporaryDirectory $asset
    $extractDirectory = Join-Path $script:TemporaryDirectory 'agent'

    New-Item -ItemType Directory -Force -Path $installDirectory | Out-Null
    try {
        Download-ReleaseAsset $asset $archive
        Test-ReleaseChecksum $asset $archive
        Expand-Archive -LiteralPath $archive -DestinationPath $extractDirectory -Force
        $source = Join-Path $extractDirectory 'obsisync-windows-amd64.exe'
        if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
            throw 'Release archive did not contain obsisync-windows-amd64.exe.'
        }
        Copy-Item -LiteralPath $source -Destination $temporaryPath
        Move-Item -Force -Path $temporaryPath -Destination $agentPath
    }
    finally {
        Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath $temporaryPath
    }

    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    $pathEntries = @($userPath -split ';' | Where-Object { $_ })
    if ($pathEntries -notcontains $installDirectory) {
        [Environment]::SetEnvironmentVariable('Path', ($pathEntries + $installDirectory -join ';'), 'User')
    }
    if (($env:Path -split ';') -notcontains $installDirectory) {
        $env:Path = "$installDirectory;$env:Path"
    }

    Write-Output "  obsisync installed: $agentPath"
    Write-Output '  The user PATH was updated. Open a new terminal to use the command globally.'
}

function Install-Skill([string]$Choice) {
    $targets = @{
        '1' = @('Claude Code', (Join-Path $HOME ".claude\skills\$SkillName"))
        '2' = @('Gemini CLI', (Join-Path $HOME ".gemini\skills\$SkillName"))
        '3' = @('Codex CLI (OpenAI)', (Join-Path $HOME ".codex\skills\$SkillName"))
        '4' = @('Antigravity', (Join-Path $HOME ".gemini\antigravity\skills\$SkillName"))
        '5' = @('Cursor', (Join-Path $HOME ".cursor\skills\$SkillName"))
        '6' = @('Windsurf', (Join-Path $HOME ".windsurf\skills\$SkillName"))
    }
    if (-not $targets.ContainsKey($Choice)) {
        Write-Warning "Ignoring unknown selection: $Choice"
        return
    }

    $name, $target = $targets[$Choice]
    $skillSource = Get-SkillSource
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue -LiteralPath $target
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $target) | Out-Null
    Copy-Item -Recurse -Force -LiteralPath $skillSource -Destination $target
    $InstalledSkills.Add("$name: $target")
    Write-Output "  Skill installed for ${name}: $target"
}

function Select-Agents {
    @'

Install the knowledge-management skill for coding agents (comma-separated):
  1. Claude Code
  2. Gemini CLI
  3. Codex CLI (OpenAI)
  4. Antigravity
  5. Cursor
  6. Windsurf
Leave blank to skip skill installation.
'@ | Write-Output
    $selection = Read-Host -Prompt 'Selection'
    foreach ($choice in $selection -split ',') {
        if ($choice.Trim()) {
            Install-Skill $choice.Trim()
        }
    }
}

function Get-McpSettings {
    Write-Output "`nMCP connection settings (leave blank to retain an existing environment value)."
    $urlInput = Read-Host -Prompt '  OBSIDIAN_SYNC_URL (for example, https://sync.example.com)'
    $tokenInput = Get-SecretInput '  KNOWLEDGE_API_TOKEN (DB API token; not an admin token)'

    return @{
        Url = if ($urlInput) { $urlInput } else { $env:OBSIDIAN_SYNC_URL }
        Token = if ($tokenInput) { $tokenInput } else { $env:KNOWLEDGE_API_TOKEN }
    }
}

function Save-McpSettings($McpSettings) {
    if (-not $McpSettings.Url -and -not $McpSettings.Token) {
        return
    }

    Write-Output 'MCP settings should normally be injected as secrets by the MCP client.'
    if (-not (Confirm 'Save the supplied values as plaintext user environment variables?')) {
        return
    }

    if ($McpSettings.Url) {
        [Environment]::SetEnvironmentVariable('OBSIDIAN_SYNC_URL', $McpSettings.Url, 'User')
    }
    if ($McpSettings.Token) {
        [Environment]::SetEnvironmentVariable('KNOWLEDGE_API_TOKEN', $McpSettings.Token, 'User')
    }
    Write-Output '  MCP settings saved for the current user. Restart the MCP client before using them.'
}

function Show-Summary($McpSettings) {
    Write-Output "`nInstallation complete."
    if ($InstalledSkills.Count -gt 0) {
        Write-Output 'Installed skills:'
        $InstalledSkills | ForEach-Object { Write-Output "  $_" }
    }

    $urlStatus = if ($McpSettings.Url) { 'provided' } else { 'not provided' }
    $tokenStatus = if ($McpSettings.Token) { 'provided' } else { 'not provided' }
    Write-Output "MCP settings: OBSIDIAN_SYNC_URL=$urlStatus, KNOWLEDGE_API_TOKEN=$tokenStatus"
    if ($McpSettings.Url) {
        Write-Output "MCP endpoint: $($McpSettings.Url.TrimEnd('/'))/mcp"
        Write-Output 'MCP header: Authorization: Bearer $KNOWLEDGE_API_TOKEN'
    }
}

if ($Help) {
    Show-Usage
    exit 0
}

$TemporaryDirectory = Join-Path ([IO.Path]::GetTempPath()) "obsisync-$PID-$([Guid]::NewGuid().ToString('N'))"
New-Item -ItemType Directory -Path $TemporaryDirectory | Out-Null
try {
    Install-Agent
    Select-Agents
    $mcpSettings = Get-McpSettings
    Save-McpSettings $mcpSettings
    Show-Summary $mcpSettings
}
finally {
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue -LiteralPath $TemporaryDirectory
}
