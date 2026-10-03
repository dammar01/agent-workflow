# task-events.ps1 - PostToolUse hook, and PreToolUse for Bash (DEC-015)
#
# Matcher (settings.json): PostToolUse Read|Skill|Edit|Write|MultiEdit|NotebookEdit|Bash;
# PreToolUse Bash.
# Records what the runtime never sees into .workflow/data/tasks.jsonl:
#   - a local workflow skill being loaded (Read of ~/.claude/skills/<name>.md, or the Skill tool)
#   - a file edited inside the project (not .workflow/ or .git/)
#   - a commit just made: PreToolUse saves HEAD before a Bash `git commit`, PostToolUse
#     records the new HEAD only if it moved. A failed commit, or one with no saved HEAD,
#     records nothing.
# Delegated skills are left out: the runtime records them with their verdict. No prompt
# change is needed; every event is a tool call that already happened. Only appends: task
# state is derived at report time (core/audit/task_telemetry.py). Same contract as
# task-events.sh. Never blocks: every path exits 0.

$ErrorActionPreference = 'Stop'

$LocalSkills = @('execute', 'init', 'upgrade', 'doctor', 'sweep', 'refactor', 'commit', 'review',
    'compress', 'memory', 'caveman', 'local', 'provider', 'promote', 'help')
$GitCommit = '\bgit\b[^\n|;&]*\bcommit\b'

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

function Get-SnapshotPath($Payload, [string]$Root) {
    $key = [string]$Payload.tool_use_id
    if ([string]::IsNullOrWhiteSpace($key)) { $key = [string]$Payload.session_id }
    if ([string]::IsNullOrWhiteSpace($key)) { return $null }
    $key = [regex]::Replace($key, '[^\w-]', '_')
    return (Join-Path $Root ('.workflow\data\task-hook\' + $key + '.head'))
}

function Save-Head($Payload, [string]$Root) {
    $path = Get-SnapshotPath $Payload $Root
    if (-not $path) { return }
    New-Item -ItemType Directory -Force -Path (Split-Path -Parent $path) | Out-Null
    $head = & git -C $Root rev-parse --verify -q HEAD 2>$null
    if ($LASTEXITCODE -ne 0) { $head = '' }
    [System.IO.File]::WriteAllText($path, ([string]$head).Trim(), (New-Object System.Text.UTF8Encoding($false)))
}

function Get-Event($Payload, [string]$Root) {
    $tool = [string]$Payload.tool_name
    $ti = $Payload.tool_input
    if ($tool -eq 'Read') {
        $m = [regex]::Match([string]$ti.file_path, '[\\/]\.claude[\\/]skills[\\/]\.?([\w-]+)\.md$')
        if ($m.Success -and $LocalSkills -contains $m.Groups[1].Value) {
            return [ordered]@{ kind = 'skill'; skill = $m.Groups[1].Value }
        }
        return $null
    }
    if ($tool -eq 'Skill') {
        $name = ([string]$ti.skill).TrimStart('.')
        if ($LocalSkills -contains $name) { return [ordered]@{ kind = 'skill'; skill = $name } }
        return $null
    }
    if (@('Edit', 'Write', 'MultiEdit', 'NotebookEdit') -contains $tool) {
        $target = [string]$ti.file_path
        if ([string]::IsNullOrWhiteSpace($target)) { $target = [string]$ti.notebook_path }
        $rel = Get-InsidePath $Root $target
        if ($rel) { return [ordered]@{ kind = 'edit'; path = $rel } }
        return $null
    }
    if ($tool -eq 'Bash' -and [regex]::IsMatch([string]$ti.command, $GitCommit)) {
        $path = Get-SnapshotPath $Payload $Root
        if (-not $path -or -not (Test-Path -LiteralPath $path -PathType Leaf)) { return $null }
        $before = ([System.IO.File]::ReadAllText($path)).Trim()
        Remove-Item -LiteralPath $path -Force
        $out = & git -C $Root log -1 '--format=%H' --name-only --relative 2>$null
        if ($LASTEXITCODE -ne 0) { return $null }
        $lines = @($out | ForEach-Object { ([string]$_).Trim() } | Where-Object { $_ })
        if ($lines.Count -lt 1 -or $lines[0] -eq $before) { return $null }
        $paths = @()
        if ($lines.Count -gt 1) { $paths = @($lines[1..($lines.Count - 1)]) }
        return [ordered]@{ kind = 'commit'; commit = $lines[0]; paths = $paths }
    }
    return $null
}

try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }
    $payload = $raw | ConvertFrom-Json
    $root = Find-ProjectRoot ([string]$payload.cwd)
    if (-not $root) { exit 0 }
    if ([string]$payload.hook_event_name -eq 'PreToolUse') {
        if ([string]$payload.tool_name -eq 'Bash' -and [regex]::IsMatch([string]$payload.tool_input.command, $GitCommit)) {
            Save-Head $payload $root
        }
        exit 0
    }
    $taskEvent = Get-Event $payload $root
    if (-not $taskEvent) { exit 0 }
    $row = [ordered]@{
        v          = 1
        at         = (Get-Date).ToUniversalTime().ToString('o')
        source     = 'hook'
        session_id = (Get-MainSession ([string]$payload.session_id))
    }
    foreach ($key in $taskEvent.Keys) { $row[$key] = $taskEvent[$key] }
    $line = ($row | ConvertTo-Json -Compress -Depth 4) + "`n"
    [System.IO.File]::AppendAllText((Join-Path $root '.workflow\data\tasks.jsonl'), $line, (New-Object System.Text.UTF8Encoding($false)))
} catch { }
exit 0
