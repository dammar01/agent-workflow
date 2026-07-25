# intent-gate-check.ps1 - PreToolUse hook (Pre-flight gate: CHECK side)
#
# Fires for gather tools (matcher in settings.json: mcp__.*|Read|Grep|Glob).
# If a DELEGATED marker is pending (set by intent-gate-set.ps1, not yet cleared by
# .workflow/run) -> HARD-block the tool: exit 2 with the reason on stderr (Claude Code
# feeds stderr back to the agent as the block reason).
#
# Escape hatches (allow despite marker):
#   - env  WORKFLOW_LOCAL_MODE=1
#   - file .workflow/sessions/<MAIN_SESSION_ID>/runtime/local_mode.flag exists
# Marker absent, or session unresolved -> allow (fail-open). Always resolves via the
# registry written by session-bind.ps1. Bash is NOT matched, so .workflow/run can run
# to clear the marker even while the gate is active.

$ErrorActionPreference = 'Stop'

try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }

    $payload   = $raw | ConvertFrom-Json
    $claudeSid = $payload.session_id
    $toolName  = [string]$payload.tool_name
    $cwd       = $payload.cwd
    if ([string]::IsNullOrWhiteSpace($claudeSid)) { exit 0 }

    # global env escape
    if ($env:WORKFLOW_LOCAL_MODE -eq '1') { exit 0 }

    # resolve MAIN_SESSION_ID + root from registry
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
    $localFlag  = Join-Path $runtimeDir "local_mode.flag"

    # file escape
    if (Test-Path -LiteralPath $localFlag) { exit 0 }

    # no pending delegation -> allow
    if (-not (Test-Path -LiteralPath $marker)) { exit 0 }

    # pending DELEGATED + gather tool -> HARD block
    $cmd = "?"
    try { $cmd = ([string]((Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json).command)) } catch { }

    $reason = @"
[PRE-FLIGHT GATE] intent=DELEGATED ($cmd) but .workflow/run has NOT run this turn.
Tool '$toolName' is a bulk-gather tool -- FORBIDDEN before delegation (Division of Labor: gather = second_agent).
Do this instead: .workflow/run.ps1 $cmd "<task>" "$mainId"
That routes evidence to second_agent AND clears this gate.
False positive? Escapes: set `$env:WORKFLOW_LOCAL_MODE=1, create $localFlag, or delete $marker.
"@
    [Console]::Error.WriteLine($reason)
    exit 2
}
catch {
    # on any hook error, fail-open (never wedge the agent)
    exit 0
}
