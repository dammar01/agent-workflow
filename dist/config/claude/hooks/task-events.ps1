# task-events.ps1 - task telemetry hook (DEC-015)
#
# Registered on three events (settings.json):
#   UserPromptSubmit                                     -> a local skill invoked as /.<name>
#   PostToolUse  Skill|Edit|Write|MultiEdit|NotebookEdit -> the Skill tool, a file edited
#   Stop                                                 -> commits made since the last look
# Records what the runtime never sees into .workflow/data/tasks.jsonl. Delegated skills are
# left out: the runtime records them with their verdict.
#
# Why these events and not Read/Bash: each run is a fresh PowerShell, ~450 ms on Windows, and
# the 3.8.0 draft hooked every Read and every Bash call (twice for Bash) to catch the rare
# skill load and commit. A skill is invoked from the prompt, and a commit is a HEAD that
# moved between two turns, so both are read once per turn instead.
#
# Commits: the first event of a session snapshots HEAD under .workflow/data/task-hook/; each
# Stop records the commits between that snapshot and HEAD (first-parent, so a merge names
# what it brought in) and moves the snapshot. A commit made outside Claude between turns is
# recorded at the next Stop; task_telemetry claims it for a task only when its paths match
# that task's edits.
#
# Only appends: task state is derived at report time (core/audit/task_telemetry.py). Same
# contract as task-events.sh. Never blocks: every path exits 0.

$ErrorActionPreference = 'Stop'

$LocalSkills = @('execute', 'init', 'upgrade', 'doctor', 'sweep', 'refactor', 'commit', 'review',
    'compress', 'memory', 'caveman', 'local', 'provider', 'promote', 'help')
$EventVersion = 2

function Find-ProjectRoot([string]$Start) {
    if ([string]::IsNullOrWhiteSpace($Start)) { $Start = (Get-Location).Path }
    $current = [System.IO.Path]::GetFullPath($Start)
    while ($current) {
        if (Test-Path -LiteralPath (Join-Path $current '.workflow\data') -PathType Container) { return $current }
        $parent = [System.IO.Path]::GetDirectoryName($current)
        if (-not $parent -or $parent -eq $current) { return $null }
        $current = $parent
    }
    return $null
}

function Get-MainSession([string]$ClaudeSid) {
    try {
        $path = Join-Path $env:USERPROFILE '.claude\session_registry.json'
        $registry = Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json
        $entry = $registry.PSObject.Properties[$ClaudeSid]
        if ($entry -and $entry.Value.main_session_id) { return [string]$entry.Value.main_session_id }
    } catch { }
    return $null
}

function Get-InsidePath([string]$Root, [string]$Path) {
    if ([string]::IsNullOrWhiteSpace($Path)) { return $null }
    if (-not [System.IO.Path]::IsPathRooted($Path)) { $Path = Join-Path $Root $Path }
    $full = [System.IO.Path]::GetFullPath($Path)
    $base = $Root.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    if (-not $full.StartsWith($base, [System.StringComparison]::OrdinalIgnoreCase)) { return $null }
    $rel = $full.Substring($base.Length).Replace('\', '/')
    $first = $rel.Split('/')[0]
    if ($first -eq '.workflow' -or $first -eq '.git') { return $null }
    return $rel
}

$GitTimeoutMs = 5000
$script:GitExit = 0

function ConvertTo-ProcessArg([string]$Arg) {
    # Windows command-line quoting for ProcessStartInfo.Arguments (5.1 has no ArgumentList):
    # backslashes double only where they precede a quote or the closing quote.
    if ($Arg -ne '' -and $Arg -notmatch '[\s"]') { return $Arg }
    $escaped = [regex]::Replace($Arg, '(\\*)"', { param($m) $m.Groups[1].Value * 2 + '\"' })
    $escaped = [regex]::Replace($escaped, '(\\+)$', { param($m) $m.Groups[1].Value * 2 })
    return '"' + $escaped + '"'
}

function Invoke-Git([string[]]$GitArgs) {
    # Git runs as a bare process, not `& git`, for two reasons. Windows PowerShell 5.1 turns a
    # native command's stderr into an error record that terminates the script under 'Stop'
    # even with 2>$null ("fatal: not a git repository" lost every event of a project without
    # git, DEC-030); here stderr is drained and dropped. And `& git` cannot be bounded: a git
    # stuck on a lock would hold the hook until Claude Code's own timeout killed it. Same
    # 5-second ceiling as git_out in task-events.sh. The caller judges by $script:GitExit.
    $psi = New-Object System.Diagnostics.ProcessStartInfo
    $psi.FileName = 'git'
    $psi.Arguments = (@($GitArgs | ForEach-Object { ConvertTo-ProcessArg $_ }) -join ' ')
    $psi.UseShellExecute = $false
    $psi.CreateNoWindow = $true
    $psi.RedirectStandardOutput = $true
    $psi.RedirectStandardError = $true
    $psi.StandardOutputEncoding = New-Object System.Text.UTF8Encoding($false)
    $proc = [System.Diagnostics.Process]::Start($psi)
    $stdout = $proc.StandardOutput.ReadToEndAsync()
    $null = $proc.StandardError.ReadToEndAsync()
    if (-not $proc.WaitForExit($GitTimeoutMs)) {
        try { $proc.Kill() } catch { }
        throw "git timed out after $GitTimeoutMs ms"
    }
    $script:GitExit = $proc.ExitCode
    return ,@($stdout.Result -split "`r?`n")
}

function Get-Head([string]$Root) {
    try { $head = Invoke-Git @('-C', $Root, 'rev-parse', '--verify', '-q', 'HEAD') } catch { return '' }
    if ($script:GitExit -ne 0) { return '' }
    return ([string]($head -join '')).Trim()
}

function Get-SnapshotPath($Payload, [string]$Root) {
    $key = [string]$Payload.session_id
    if ([string]::IsNullOrWhiteSpace($key)) { return $null }
    $key = [regex]::Replace($key, '[^\w-]', '_')
    return (Join-Path $Root ('.workflow\data\task-hook\' + $key + '.head'))
}

function Write-Snapshot([string]$Path, [string]$Head) {
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Path) | Out-Null
    [System.IO.File]::WriteAllText($Path, $Head, (New-Object System.Text.UTF8Encoding($false)))
}

function Get-Commits($Payload, [string]$Root) {
    # The commits since this session's snapshot, oldest first, each with its paths.
    $path = Get-SnapshotPath $Payload $Root
    if (-not $path) { return @() }
    $now = Get-Head $Root
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
        if ($now) { Write-Snapshot $path $now }
        return @()
    }
    $before = ([System.IO.File]::ReadAllText($path)).Trim()
    if (-not $now -or $now -eq $before) { return @() }
    Write-Snapshot $path $now
    $range = if ($before) { "$before..$now" } else { $now }
    try { $out = Invoke-Git @('-C', $Root, 'log', '--reverse', '--first-parent', '-m', '--format=commit:%H', '--name-only', '--relative', $range) } catch { return @() }
    if ($script:GitExit -ne 0) { return @() }
    $commits = @()
    $current = $null
    foreach ($line in @($out)) {
        $text = ([string]$line).Trim()
        if (-not $text) { continue }
        if ($text.StartsWith('commit:')) {
            if ($current) { $commits += $current }
            $current = [ordered]@{ kind = 'commit'; commit = $text.Substring(7); paths = @() }
        } elseif ($current) {
            $current.paths += $text
        }
    }
    if ($current) { $commits += $current }
    return $commits
}

function Get-Events($Payload, [string]$Root) {
    $hook = [string]$Payload.hook_event_name
    if ($hook -eq 'UserPromptSubmit') {
        # Snapshots HEAD on a session's first prompt; later, records commits made between
        # turns (in a terminal, say) before the snapshot moves past them.
        $found = @(Get-Commits $Payload $Root)
        $m = [regex]::Match([string]$Payload.prompt, '^\s*/\.([\w-]+)')
        if ($m.Success -and $LocalSkills -contains $m.Groups[1].Value) {
            $found += [ordered]@{ kind = 'skill'; skill = $m.Groups[1].Value }
        }
        return $found
    }
    if ($hook -eq 'Stop') { return @(Get-Commits $Payload $Root) }
    if ($hook -ne 'PostToolUse') { return @() }
    $tool = [string]$Payload.tool_name
    $ti = $Payload.tool_input
    if ($tool -eq 'Skill') {
        $name = ([string]$ti.skill).TrimStart('.')
        if ($LocalSkills -contains $name) { return @([ordered]@{ kind = 'skill'; skill = $name }) }
        return @()
    }
    if (@('Edit', 'Write', 'MultiEdit', 'NotebookEdit') -contains $tool) {
        $target = [string]$ti.file_path
        if ([string]::IsNullOrWhiteSpace($target)) { $target = [string]$ti.notebook_path }
        $rel = Get-InsidePath $Root $target
        if ($rel) { return @([ordered]@{ kind = 'edit'; path = $rel }) }
    }
    return @()
}

try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }
    $payload = $raw | ConvertFrom-Json
    $root = Find-ProjectRoot ([string]$payload.cwd)
    if (-not $root) { exit 0 }
    $taskEvents = @(Get-Events $payload $root)
    if ($taskEvents.Count -eq 0) { exit 0 }
    $session = Get-MainSession ([string]$payload.session_id)
    $lines = New-Object System.Text.StringBuilder
    foreach ($taskEvent in $taskEvents) {
        $row = [ordered]@{
            v          = $EventVersion
            at         = (Get-Date).ToUniversalTime().ToString('o')
            source     = 'hook'
            session_id = $session
        }
        foreach ($key in $taskEvent.Keys) { $row[$key] = $taskEvent[$key] }
        if ($row.Contains('paths')) { $row['paths'] = @($row['paths']) }
        [void]$lines.Append(($row | ConvertTo-Json -Compress -Depth 4) + "`n")
    }
    [System.IO.File]::AppendAllText((Join-Path $root '.workflow\data\tasks.jsonl'), $lines.ToString(), (New-Object System.Text.UTF8Encoding($false)))
} catch { }
exit 0
