# intent-gate-set.ps1 - UserPromptSubmit hook (Pre-flight gate: SET side)
#
# Classifies the raw user prompt against the DELEGATED NL-map (intent-map.json).
# If the prompt resolves to a DELEGATED command (explore/plan/analyze/verify/sweep),
# it writes a marker:  .workflow/sessions/<MAIN_SESSION_ID>/runtime/delegated.marker
# The PreToolUse hook (intent-gate-check.ps1) reads that marker to HARD-block gather
# tools until .workflow/run has executed (which clears the marker).
#
# Non-delegated prompt -> delete any stale marker (turn reset).
# MAIN_SESSION_ID is resolved from ~/.claude/session_registry.json (keyed by claude session_id,
# written by session-bind.ps1). No registry entry -> cannot gate -> exit 0 (fail-open).
# NEVER blocks the prompt. Always exit 0.

$ErrorActionPreference = 'Stop'

function Write-NoBom([string]$Path, [string]$Content) {
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $enc)
}

try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }

    $payload   = $raw | ConvertFrom-Json
    $prompt    = [string]$payload.user_prompt
    $claudeSid = $payload.session_id
    $cwd       = $payload.cwd
    if ([string]::IsNullOrWhiteSpace($claudeSid)) { exit 0 }

    # resolve MAIN_SESSION_ID + project root from registry (source of truth: session-bind.ps1)
    $registryPath = Join-Path $env:USERPROFILE '.claude\session_registry.json'
    if (-not (Test-Path -LiteralPath $registryPath)) { exit 0 }
    $reg = Get-Content -LiteralPath $registryPath -Raw | ConvertFrom-Json
    $entry = $reg.$claudeSid
    if (-not $entry) { exit 0 }
    $mainId = [string]$entry.main_session_id
    $root   = [string]$entry.cwd
    if ([string]::IsNullOrWhiteSpace($mainId)) { exit 0 }
    if ([string]::IsNullOrWhiteSpace($root)) { $root = $cwd }
    if ([string]::IsNullOrWhiteSpace($root)) { exit 0 }

    $runtimeDir = Join-Path $root ".workflow\sessions\$mainId\runtime"
    $marker     = Join-Path $runtimeDir "delegated.marker"

    # load NL-map (co-located with this hook)
    $mapPath = Join-Path $PSScriptRoot "intent-map.json"
    if (-not (Test-Path -LiteralPath $mapPath)) { exit 0 }
    $map = Get-Content -LiteralPath $mapPath -Raw | ConvertFrom-Json

    $resolved = $null

    # 1. explicit "/.cmd" prefix wins
    if ($prompt -match [string]$map.prefix_regex) {
        $resolved = $Matches[1]
    } else {
        # 2. NL-map: first delegated command whose any pattern matches
        foreach ($cmd in $map.delegated) {
            foreach ($pat in $map.patterns.$cmd) {
                if ($prompt -imatch $pat) { $resolved = $cmd; break }
            }
            if ($resolved) { break }
        }
    }

    if ($resolved) {
        if (-not (Test-Path -LiteralPath $runtimeDir)) {
            New-Item -ItemType Directory -Force -Path $runtimeDir | Out-Null
        }
        $excerpt = $prompt
        if ($excerpt.Length -gt 160) { $excerpt = $excerpt.Substring(0, 160) }
        $obj = [PSCustomObject]@{
            command   = $resolved
            set_at    = (Get-Date).ToUniversalTime().ToString('o')
            claude_sid = $claudeSid
            prompt_excerpt = $excerpt
        }
        Write-NoBom $marker ($obj | ConvertTo-Json -Compress)
    } else {
        # non-delegated turn: clear stale marker
        if (Test-Path -LiteralPath $marker) {
            Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
        }
    }
    exit 0
}
catch {
    # never block prompt submission
    exit 0
}
