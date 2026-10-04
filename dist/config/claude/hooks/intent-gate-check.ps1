# intent-gate-check.ps1 - PreToolUse hook (Pre-flight gate: CHECK side)
#
# Fires for gather tools and file writes (matcher in settings.json:
# mcp__.*|Read|Grep|Glob|Bash|PowerShell|Write|Edit|MultiEdit|NotebookEdit).
# If a DELEGATED marker is pending (set by intent-gate-set.ps1, not yet cleared by
# .workflow/run) -> HARD-block the tool: exit 2 with the reason on stderr (Claude Code
# feeds stderr back to the agent as the block reason).
#
# Bash and PowerShell are matched too so `cat`/`rg`/`git show`/`Get-Content` cannot bypass
# the gate. The runner scripts (.workflow/run|check|inspect) ARE invoked through them and
# stay allowed, but ONLY as one plain command that is parsed, not pattern-matched:
#   - Bash: POSIX words, no shell metacharacter; the first word is the runner, or
#     powershell/pwsh with only -NoProfile/-NonInteractive/-ExecutionPolicy Bypass and then
#     -File <runner>, or bash/sh <runner> with no flag. -c, -Command, -EncodedCommand and
#     any other flag refuse: they hand the rest of the line to an interpreter.
#   - PowerShell: the real parser; one command, no pipe/redirect, `&` or no operator, and
#     every element a constant string or a switch without an argument.
# Either way the runner word must resolve to exactly <root>/.workflow/{run,check,inspect}
# .{ps1,sh}: a runner in another directory, `..` out of the root, or `runx.sh` is refused.
#
# Writes are not gather and pass, except a Write/Edit/MultiEdit/NotebookEdit of the runner
# scripts themselves: rewriting run.sh and then running it would turn the allowlist into an
# arbitrary command.
#
# A pending verify marker opens one narrow lane, for what skills/verify.md asks before the
# run (pick tests from the diff): a clean `git diff --name-only|--name-status|--stat ...`
# (no patch, no file output), a Read of .workflow/config.json or of this session's
# verify/tests.json, and a Write of that tests.json. Everything else stays blocked.
#
# Under any pending marker a Read of a skill definition passes: an existing .md whose real
# path lies inside ~/.claude/skills. The skill says how to dispatch; it is not evidence.
#
# Escape hatches (allow despite marker):
#   - env  WORKFLOW_LOCAL_MODE=1
#   - file .workflow/data/sessions/<MAIN_SESSION_ID>/runtime/local_mode.flag exists
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

function Get-WorkflowDataDir([string]$root) {
    # Same rule as core/workspace/workspace_paths.data_dir: .workflow/data once it exists,
    # the .workflow root while a v3.5.x-layout workspace still keeps its data there, data/ otherwise.
    $wf = Join-Path $root ".workflow"
    $data = Join-Path $wf "data"
    if (Test-Path -LiteralPath $data -PathType Container) { return $data }
    foreach ($name in @('sessions','provider-sessions','reports','audit.jsonl','usage.jsonl','quality.jsonl','facts.jsonl','evidence.jsonl','redactions.jsonl')) {
        if (Test-Path -LiteralPath (Join-Path $wf $name)) { return $wf }
    }
    return $data
}

function Test-SamePath([string]$Candidate, [string]$Expected, [string]$Base) {
    # True when $Candidate names $Expected. A relative path resolves against the project
    # root; case is ignored, as the Windows filesystem ignores it.
    if ([string]::IsNullOrWhiteSpace($Candidate)) { return $false }
    try {
        if (-not [System.IO.Path]::IsPathRooted($Candidate)) { $Candidate = Join-Path $Base $Candidate }
        $a = [System.IO.Path]::GetFullPath($Candidate).TrimEnd('\', '/')
        $b = [System.IO.Path]::GetFullPath($Expected).TrimEnd('\', '/')
        return [string]::Equals($a, $b, [System.StringComparison]::OrdinalIgnoreCase)
    } catch { return $false }
}

function Test-DiffWords([string[]]$Words) {
    # `git diff` that names files only: --name-only, --name-status or --stat, every other
    # option from a short list that can neither print a patch nor write a file; refs and
    # paths are free. The caller has already rejected shell metacharacters.
    if ($Words.Count -lt 3 -or $Words[0] -ne 'git' -or $Words[1] -ne 'diff') { return $false }
    $summary = $false
    foreach ($tok in $Words[2..($Words.Count - 1)]) {
        if (@('--name-only', '--name-status') -contains $tok -or $tok -match '^--stat(=\d+(,\d+)*)?$') { $summary = $true; continue }
        if (@('--cached', '--staged', '--relative', '--no-renames', '--no-color', '--') -contains $tok) { continue }
        if ($tok.StartsWith('-')) { return $false }
    }
    return $summary
}

function Test-DiffSummary([string]$Command) {
    return (Test-DiffWords @($Command.Trim() -split '\s+'))
}

function Test-RunnerTarget([string]$Candidate, [string]$Root) {
    # True when $Candidate names exactly <root>/.workflow/{run,check,inspect}.{ps1,sh}.
    $wf = Join-Path $Root '.workflow'
    foreach ($name in @('run.ps1', 'run.sh', 'check.ps1', 'check.sh', 'inspect.ps1', 'inspect.sh')) {
        if (Test-SamePath $Candidate (Join-Path $wf $name) $Root) { return $true }
    }
    return $false
}

function Test-RunnerFile([string]$Candidate, [string]$Root) {
    # True when a file write lands on a runner script: <root>/.workflow/{run,check,inspect}
    # with any extension. An NTFS stream suffix (`run.ps1::$DATA` writes run.ps1) is cut
    # off first; a path carrying an 8.3 short name (`WORKFL~1`) cannot be told apart from
    # .workflow without the filesystem, so a runner name under one counts. Unsure -> true.
    if ([string]::IsNullOrWhiteSpace($Candidate)) { return $false }
    try {
        if ([System.IO.Path]::DirectorySeparatorChar -eq '\' -and $Candidate.Length -gt 2) {
            $colon = $Candidate.IndexOf(':', 2)
            if ($colon -ge 0) { $Candidate = $Candidate.Substring(0, $colon) }
        }
        if (-not [System.IO.Path]::IsPathRooted($Candidate)) { $Candidate = Join-Path $Root $Candidate }
        $full = [System.IO.Path]::GetFullPath($Candidate).TrimEnd('\', '/')
        if ([System.IO.Path]::GetFileName($full) -notmatch '^(run|check|inspect)(\..*)?$') { return $false }
        $parent = [System.IO.Path]::GetDirectoryName($full)
        if (Test-SamePath $parent (Join-Path $Root '.workflow') $Root) { return $true }
        $leaf = [System.IO.Path]::GetFileName($parent)
        return ($parent.Contains('~') -and ($leaf -eq '.workflow' -or $leaf.Contains('~')))
    } catch { return $true }
}

function Get-RealPath([string]$Path) {
    # $Path with every symlink or junction along it followed, as os.path.realpath gives it in
    # the .sh flavour; $null when a link cannot be read or the chain runs past 40 hops.
    # 5.1 has no ResolveLinkTarget, so the walk is by hand, one component at a time.
    $full = [System.IO.Path]::GetFullPath($Path)
    for ($hop = 0; $hop -lt 40; $hop++) {
        $drive = [System.IO.Path]::GetPathRoot($full)
        $parts = @($full.Substring($drive.Length).Split([char[]]@('\', '/'), [System.StringSplitOptions]::RemoveEmptyEntries))
        $cur = $drive
        $next = $null
        for ($i = 0; $i -lt $parts.Count; $i++) {
            $cur = Join-Path $cur $parts[$i]
            $item = Get-Item -LiteralPath $cur -Force -ErrorAction SilentlyContinue
            if ($null -eq $item) { return $full }
            if (@('SymbolicLink', 'Junction') -notcontains [string]$item.LinkType) { continue }
            $target = [string](@($item.Target)[0])
            if ([string]::IsNullOrWhiteSpace($target)) { return $null }
            if (-not [System.IO.Path]::IsPathRooted($target)) { $target = Join-Path (Split-Path -Parent $cur) $target }
            if ($i + 1 -lt $parts.Count) { $target = Join-Path $target ($parts[($i + 1)..($parts.Count - 1)] -join [System.IO.Path]::DirectorySeparatorChar) }
            $next = [System.IO.Path]::GetFullPath($target)
            break
        }
        if ($null -eq $next) { return $full.TrimEnd('\', '/') }
        $full = $next
    }
    return $null
}

function Test-SkillRead([string]$Candidate) {
    # True when a Read names a skill definition: an existing .md file whose real path (every
    # link followed) lies inside the real ~/.claude/skills. A skill is the instruction the
    # agent must read to know how to dispatch, not evidence, so blocking it before the run
    # locked the two together. An absolute path only, no `..` in it; a link may point
    # anywhere inside the skills folder, never out of it. Same rule as skill_read in the .sh.
    if ([string]::IsNullOrWhiteSpace($Candidate) -or -not [System.IO.Path]::IsPathRooted($Candidate)) { return $false }
    if (@($Candidate -split '[\\/]') -contains '..') { return $false }
    if (-not $Candidate.EndsWith('.md', [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
    try {
        $skills = Get-RealPath (Join-Path (Join-Path $env:USERPROFILE '.claude') 'skills')
        $full = Get-RealPath $Candidate
        if ($null -eq $skills -or $null -eq $full) { return $false }
        if (-not $full.EndsWith('.md', [System.StringComparison]::OrdinalIgnoreCase)) { return $false }
        if (-not (Test-Path -LiteralPath $full -PathType Leaf)) { return $false }
        # The separator of the running OS: Get-RealPath rebuilds with Join-Path, which spells
        # a pwsh path on Linux with `/`, so a hardcoded `\` refused every skill there.
        return $full.StartsWith($skills.TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
    } catch { return $false }
}

function ConvertFrom-MsysPath([string]$Path) {
    # Git Bash spells E:\x as /e/x (or /cygdrive/e/x); read it the way that shell does.
    if ([System.IO.Path]::DirectorySeparatorChar -eq '\' -and $Path -match '^/(?:cygdrive/)?([A-Za-z])(/.*)?$') {
        $rest = if ($Matches[2]) { $Matches[2] } else { '/' }
        return ($Matches[1] + ':' + $rest)
    }
    return $Path
}

function Split-PosixWords([string]$Text) {
    # The words a POSIX shell runs, as shlex.split gives them in the .sh flavour: quotes
    # removed, a backslash escapes the next character outside quotes and only " or \ inside
    # double quotes. $null when a quote or an escape is left open.
    $words = New-Object System.Collections.Generic.List[string]
    $sb = New-Object System.Text.StringBuilder
    $inWord = $false
    $quote = ''
    $i = 0
    while ($i -lt $Text.Length) {
        $c = [string]$Text[$i]
        if ($quote -ceq "'") {
            if ($c -ceq "'") { $quote = '' } else { [void]$sb.Append($c) }
        } elseif ($quote -ceq '"') {
            if ($c -ceq '"') { $quote = '' }
            elseif ($c -ceq '\' -and $i + 1 -lt $Text.Length -and @('"', '\') -ccontains [string]$Text[$i + 1]) { $i++; [void]$sb.Append($Text[$i]) }
            else { [void]$sb.Append($c) }
        } elseif (@(' ', "`t", "`r") -ccontains $c) {
            if ($inWord) { $words.Add($sb.ToString()); [void]$sb.Clear(); $inWord = $false }
        } elseif ($c -ceq '\') {
            $i++
            if ($i -ge $Text.Length) { return $null }
            [void]$sb.Append($Text[$i]); $inWord = $true
        } elseif ($c -ceq "'" -or $c -ceq '"') {
            $quote = $c; $inWord = $true
        } else {
            [void]$sb.Append($c); $inWord = $true
        }
        $i++
    }
    if ($quote) { return $null }
    if ($inWord) { $words.Add($sb.ToString()) }
    return ,$words.ToArray()
}

function Test-BashRunnerCall([string]$Command, [string]$Root) {
    # The runner is the command itself: the first word, or behind powershell/pwsh (only
    # -NoProfile, -NonInteractive, -ExecutionPolicy Bypass, then -File) or behind bash/sh
    # with no flag at all. Anything else in front can run code of its own: powershell
    # without -File evaluates the rest as PowerShell, and `bash -ExecutionPolicy` reads as
    # a bundle holding -c. Same rule as bash_runner_call in the .sh flavour.
    try {
        $words = Split-PosixWords $Command
        if ($null -eq $words -or $words.Count -eq 0) { return $false }
        $i = 0
        if ($words[0] -match '^(?i)(powershell|pwsh)(\.exe)?$') {
            $i = 1
            $file = $false
            while ($i -lt $words.Count -and -not $file) {
                $w = $words[$i]
                if ($w -eq '-NoProfile' -or $w -eq '-NonInteractive') { $i++ }
                elseif ($w -eq '-ExecutionPolicy' -and $i + 1 -lt $words.Count -and $words[$i + 1] -eq 'Bypass') { $i += 2 }
                elseif ($w -eq '-File') { $i++; $file = $true }
                else { return $false }
            }
            if (-not $file) { return $false }
        } elseif ($words[0] -match '^(?i)(bash|sh)(\.exe)?$') {
            $i = 1
        }
        if ($i -ge $words.Count) { return $false }
        return (Test-RunnerTarget (ConvertFrom-MsysPath $words[$i]) $Root)
    } catch { return $false }
}

function Get-PsPlainCommand([string]$Source) {
    # The words of PowerShell source that is one plain command and nothing else, or $null:
    # one statement, one pipeline element, no redirection, `&` or no invocation operator,
    # and every element a constant string (bare, '..' or ".." with nothing to expand) or a
    # switch without an argument. A paren, $(..), $var, @{..}, a pipe or `;` all refuse.
    # Raw newlines, --% (stop-parsing) and typographic quotes refuse before parsing, as in
    # the .sh flavour, which has no parser to lean on.
    try {
        if ($Source -match "[`r`n]|--%|[\u2018-\u201E]") { return $null }
        $tokens = $null; $errors = $null
        $ast = [System.Management.Automation.Language.Parser]::ParseInput($Source, [ref]$tokens, [ref]$errors)
        if ($errors.Count -gt 0) { return $null }
        if ($ast.ParamBlock -or $ast.BeginBlock -or $ast.ProcessBlock -or $ast.DynamicParamBlock -or -not $ast.EndBlock) { return $null }
        $statements = $ast.EndBlock.Statements
        if ($statements.Count -ne 1) { return $null }
        $pipe = $statements[0]
        if ($pipe -isnot [System.Management.Automation.Language.PipelineAst] -or $pipe.PipelineElements.Count -ne 1) { return $null }
        if ($pipe.PSObject.Properties['Background'] -and $pipe.Background) { return $null }
        $command = $pipe.PipelineElements[0]
        if ($command -isnot [System.Management.Automation.Language.CommandAst] -or $command.Redirections.Count -gt 0) { return $null }
        if (@('Ampersand', 'Unknown') -notcontains [string]$command.InvocationOperator) { return $null }
        if ($command.CommandElements[0] -isnot [System.Management.Automation.Language.StringConstantExpressionAst]) { return $null }
        $words = @()
        foreach ($el in $command.CommandElements) {
            if ($el -is [System.Management.Automation.Language.StringConstantExpressionAst]) { $words += [string]$el.Value; continue }
            if ($el -is [System.Management.Automation.Language.CommandParameterAst] -and $null -eq $el.Argument) { $words += [string]$el.Extent.Text; continue }
            return $null
        }
        return ,$words
    } catch { return $null }
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
    # session-bind writes UTF-8 without a BOM; 5.1 would decode a non-ASCII root as ANSI.
    $reg = Get-Content -LiteralPath $registryPath -Raw -Encoding UTF8 | ConvertFrom-Json
    $entry = $reg.$claudeSid
    if (-not $entry) { exit 0 }
    $mainId = [string]$entry.main_session_id
    $root   = [string]$entry.cwd
    if ([string]::IsNullOrWhiteSpace($mainId)) { exit 0 }
    if ([string]::IsNullOrWhiteSpace($root)) { $root = $cwd }
    if ([string]::IsNullOrWhiteSpace($root)) { exit 0 }

    $runtimeDir = Join-Path (Get-WorkflowDataDir $root) "sessions\$mainId\runtime"
    $marker     = Join-Path $runtimeDir "delegated.marker"
    $localFlag  = Join-Path $runtimeDir "local_mode.flag"

    # file escape
    if (Test-Path -LiteralPath $localFlag) { exit 0 }

    # no pending delegation -> allow
    if (-not (Test-Path -LiteralPath $marker)) { exit 0 }

    $cmd = "?"
    try { $cmd = ([string]((Get-Content -LiteralPath $marker -Raw -Encoding UTF8 | ConvertFrom-Json).command)) }
    catch { Write-HookWarning 'marker_unreadable' $_.Exception.Message }

    # File writes: only the runner scripts are refused. Rewriting run.sh and then running it
    # would make the runner allowlist below an arbitrary command.
    if (@('Write', 'Edit', 'MultiEdit', 'NotebookEdit') -contains $toolName) {
        $target = [string]$payload.tool_input.file_path
        if ([string]::IsNullOrWhiteSpace($target)) { $target = [string]$payload.tool_input.notebook_path }
        if (Test-RunnerFile $target $root) {
            $reason = @"
[PRE-FLIGHT GATE] intent=DELEGATED ($cmd) is pending and '$toolName' targets a runner script: $target
The runner is the one command this gate lets through, so it cannot be rewritten while the gate is armed.
Do this instead: .workflow/run.ps1 $cmd "<task>" "$mainId" as shipped.
False positive? Escapes: set `$env:WORKFLOW_LOCAL_MODE=1, create $localFlag, or delete $marker.
"@
            [Console]::Error.WriteLine($reason)
            exit 2
        }
        exit 0
    }

    # Bash allowlist: permit ONLY a clean .workflow/{run,check,inspect} invocation. A command
    # carrying &, ;, |, backtick, $(...), redirect, or a newline could smuggle a gather step
    # past the gate, so any of those forces the block path below even for a runner call.
    if ($toolName -eq 'Bash') {
        $bashCmd = [string]$payload.tool_input.command
        $chained = ($bashCmd -match '[&;|`]') -or ($bashCmd -match '\$\(') -or ($bashCmd -match '[<>]') -or ($bashCmd -match "[`r`n]")
        # The runner must be the command itself, anchored to this project's .workflow, not a
        # word anywhere in it: `python -c "..." x/.workflow/run.sh` is not a runner call.
        if ((-not $chained) -and (Test-BashRunnerCall $bashCmd $root)) { exit 0 }
        # verify: the diff that tells main_agent which tests to pick (skills/verify.md).
        if ($cmd -eq 'verify' -and (-not $chained) -and (Test-DiffSummary $bashCmd)) { exit 0 }
    }

    # PowerShell tool: its command is PowerShell source, so it is held to the parser's view
    # of one plain command; the same runner anchor and verify diff lane then apply.
    if ($toolName -eq 'PowerShell') {
        $words = Get-PsPlainCommand ([string]$payload.tool_input.command)
        if ($null -ne $words) {
            if (Test-RunnerTarget $words[0] $root) { exit 0 }
            if ($cmd -eq 'verify' -and (Test-DiffWords $words)) { exit 0 }
        }
    }

    # Any command: a Read of a skill definition, the instruction that says how to dispatch.
    if ($toolName -eq 'Read' -and (Test-SkillRead ([string]$payload.tool_input.file_path))) { exit 0 }

    # verify: the test allowlist and this session's test request, nothing else.
    if ($cmd -eq 'verify' -and @('Read', 'Write') -contains $toolName) {
        $target = [string]$payload.tool_input.file_path
        $testsJson = Join-Path (Split-Path -Parent $runtimeDir) 'verify\tests.json'
        if (Test-SamePath $target $testsJson $root) { exit 0 }
        if ($toolName -eq 'Read' -and (Test-SamePath $target (Join-Path $root '.workflow\config.json') $root)) { exit 0 }
    }

    # pending DELEGATED + gather tool -> HARD block

    $what = if (@('Bash', 'PowerShell') -contains $toolName) { "a shell read (cat/rg/grep/git show) -- reading the codebase is second_agent's job" } else { "a bulk-gather tool" }
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
