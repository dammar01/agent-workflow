# intent-gate-check.ps1 - PreToolUse hook (Pre-flight gate: CHECK side)
#
# Fires for gather tools (matcher in settings.json: mcp__.*|Read|Grep|Glob|Bash).
# If a DELEGATED marker is pending (set by intent-gate-set.ps1, not yet cleared by
# .workflow/run) -> HARD-block the tool: exit 2 with the reason on stderr (Claude Code
# feeds stderr back to the agent as the block reason).
#
# Bash is matched too so `cat`/`rg`/`git show` cannot bypass the gate. The runner scripts
# (.workflow/run|check|inspect) ARE invoked via Bash and stay allowed, but ONLY when the
# command line is a clean runner call with no shell metacharacters that could chain a
# gather command onto it (e.g. `.workflow/run ... && cat x`).
#
# Escape hatches (allow despite marker):
#   - env  WORKFLOW_LOCAL_MODE=1
#   - file .workflow/sessions/<MAIN_SESSION_ID>/runtime/local_mode.flag exists
# Marker absent, or session unresolved -> allow (fail-open). Always resolves via the
# registry written by session-bind.ps1. A clean .workflow/run call remains allowlisted so
# it can clear the marker while the gate is active.

$ErrorActionPreference = 'Stop'

# Fail-open leaves no trace, and that is the problem: a hook that dies on a malformed
# registry exits 0 exactly like a hook that found nothing to block, so the enforcement
# layer can be dead for an entire session with nothing to show for it. This records the
# fault and still exits 0 — the gate stays non-wedging, it just stops being silent about
# breaking. Written ONLY on real faults, never on the normal allow/block paths, and
# overwritten rather than appended so it cannot grow.
$RuntimeDir = $null
function Write-HookWarning([string]$Kind, [string]$Message) {
    try {
        # Session dir when it is known. The fault most worth recording — an unparseable
        # registry — happens BEFORE that dir can be resolved, so a session-only location
        # would miss exactly the case this exists for; ~/.claude is the fallback.
        $dir = $RuntimeDir
        if ([string]::IsNullOrWhiteSpace($dir)) {
            $dir = Join-Path $env:USERPROFILE '.claude'
        }
        if ([string]::IsNullOrWhiteSpace($dir)) { return }
        New-Item -ItemType Directory -Force -Path $dir | Out-Null
        $payload = [ordered]@{
            hook      = 'intent-gate-check'
            kind      = $Kind
            message   = $Message
            timestamp = (Get-Date).ToUniversalTime().ToString('o')
        }
        [System.IO.File]::WriteAllText(
            (Join-Path $dir 'hook-warning.json'),
            ($payload | ConvertTo-Json -Depth 3)
        )
    } catch { }
}

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

    # Bash allowlist: permit ONLY a clean .workflow/{run,check,inspect} invocation. A command
    # carrying &, ;, |, backtick, $(...), redirect, or a newline could smuggle a gather step
    # past the gate, so any of those forces the block path below even for a runner call.
    if ($toolName -eq 'Bash') {
        $bashCmd = [string]$payload.tool_input.command
        $chained = ($bashCmd -match '[&;|`]') -or ($bashCmd -match '\$\(') -or ($bashCmd -match '[<>]') -or ($bashCmd -match "`n")
        if ((-not $chained) -and ($bashCmd -match '(^|[\\/])\.workflow[\\/](run|check|inspect)\.(ps1|sh)\b')) {
            exit 0
        }
    }

    # pending DELEGATED + gather tool -> HARD block
    $cmd = "?"
    try { $cmd = ([string]((Get-Content -LiteralPath $marker -Raw | ConvertFrom-Json).command)) }
    catch { Write-HookWarning 'marker_unreadable' $_.Exception.Message }

    $what = if ($toolName -eq 'Bash') { "a shell read (cat/rg/grep/git show) -- reading the codebase is second_agent's job" } else { "a bulk-gather tool" }
    $reason = @"
[PRE-FLIGHT GATE] intent=DELEGATED ($cmd) but .workflow/run has NOT run this turn.
Tool '$toolName' is $what -- FORBIDDEN before delegation (Division of Labor: gather = second_agent).
Do this instead: .workflow/run.ps1 $cmd "<task>" "$mainId"
That routes evidence to second_agent AND clears this gate.
False positive? Escapes: set `$env:WORKFLOW_LOCAL_MODE=1, create $localFlag, or delete $marker.
"@
    [Console]::Error.WriteLine($reason)
    exit 2
}
catch {
    # on any hook error, fail-open (never wedge the agent) — but leave the reason behind
    Write-HookWarning 'hook_error' $_.Exception.Message
    exit 0
}
