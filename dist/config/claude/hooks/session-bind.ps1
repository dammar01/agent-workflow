# session-bind.ps1 - SessionStart hook
# Maps Claude Code session lifecycle -> second_agent (opencode) MAIN_SESSION_ID.
#
# source mapping (user decision):
#   startup | clear | compact  -> NEW  MAIN_SESSION_ID  -> second_agent thread NEW
#   resume                     -> REUSE MAIN_SESSION_ID  -> second_agent thread CONTINUE
#
# Registry: %USERPROFILE%\.claude\session_registry.json  (key = claude session_id)
# Output: JSON hookSpecificOutput.additionalContext -> injects MAIN_SESSION_ID into context.
# Never blocks session start (always exit 0).

$ErrorActionPreference = 'Stop'

function Write-NoBom([string]$Path, [string]$Content) {
    $enc = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Content, $enc)
}

try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }

    $payload   = $raw | ConvertFrom-Json
    $source    = $payload.source
    $claudeSid = $payload.session_id
    $cwd       = $payload.cwd
    if ([string]::IsNullOrWhiteSpace($cwd)) { $cwd = (Get-Location).Path }

    $root = $cwd
    try { $rp = Resolve-Path -LiteralPath $cwd -ErrorAction Stop; $root = $rp.Path } catch { }
    $slug = Split-Path -Leaf $root

    $registryPath = Join-Path $env:USERPROFILE '.claude\session_registry.json'

    # load registry
    $registry = @{}
    if (Test-Path -LiteralPath $registryPath) {
        try {
            $j = Get-Content -LiteralPath $registryPath -Raw | ConvertFrom-Json
            foreach ($p in $j.PSObject.Properties) { $registry[$p.Name] = $p.Value }
        } catch { $registry = @{} }
    }

    # decide reuse vs new
    $reuse  = $false
    $mainId = $null
    if ($source -eq 'resume' -and $claudeSid -and $registry.ContainsKey($claudeSid)) {
        $mainId = $registry[$claudeSid].main_session_id
        $reuse  = $true
    }
    if ([string]::IsNullOrWhiteSpace($mainId)) {
        $ts     = Get-Date -Format 'yyyyMMdd_HHmmssfff'   # ms-resolution avoids same-second collision
        $rand   = -join (((48..57) + (97..102)) | Get-Random -Count 4 | ForEach-Object { [char]$_ })
        $mainId = "main_${slug}_${ts}_${rand}"
        $reuse  = $false
    }

    $boundAt = (Get-Date).ToUniversalTime().ToString('o')
    if ($claudeSid) {
        $registry[$claudeSid] = [PSCustomObject]@{
            main_session_id = $mainId
            cwd             = $root
            bound_at        = $boundAt
            source          = $source
        }
    }

    # prune to newest 50 by bound_at
    try {
        $rows = foreach ($k in $registry.Keys) { [PSCustomObject]@{ key = $k; val = $registry[$k] } }
        $kept = $rows | Sort-Object { try { [datetime]$_.val.bound_at } catch { [datetime]::MinValue } } -Descending |
                Select-Object -First 50
        $pruned = @{}
        foreach ($e in $kept) { $pruned[$e.key] = $e.val }
        $registry = $pruned
    } catch { }

    Write-NoBom $registryPath (($registry | ConvertTo-Json -Depth 6))

    $verb = if ($reuse) { 'REUSE (continue)' } else { 'NEW' }
    $ctx  = @"
[SESSION BINDING - authoritative]
MAIN_SESSION_ID=$mainId
MAIN_SESSION_PROJECT_ROOT=$root
source=$source
second_agent_thread=$verb
Use this MAIN_SESSION_ID for all /.explore /.plan /.analyze /.verify /.sweep invocations. Overrides .workflow/state.json session.id.
"@

    $out = [PSCustomObject]@{
        hookSpecificOutput = [PSCustomObject]@{
            hookEventName     = 'SessionStart'
            additionalContext = $ctx
        }
    }
    Write-Output ($out | ConvertTo-Json -Depth 5 -Compress)
    exit 0
}
catch {
    # never block session start
    exit 0
}
