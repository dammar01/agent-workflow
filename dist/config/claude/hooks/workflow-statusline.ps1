[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ErrorActionPreference = 'SilentlyContinue'

# Claude Code statusline.
# Renders: <project> | Second Agent <tokens> (<cached>) / <calls> | Saved <tokens>
# Every figure is scoped to the CURRENT session.
#
# Second Agent = every token the provider processed for calls bound to this session's
#                MAIN_SESSION_ID, cache read included, falling back to the char-derived
#                estimate on rows no provider counted. The cached share is printed in
#                brackets: on an agentic run it is ~95% of the figure, and a number that
#                large with no explanation beside it reads as a bug rather than a fact.
# Saved        = what the second agent handled that NEVER reached this context: the files
#                and tool output it ingested (fresh input) plus the reasoning it spent
#                (inside output, never emitted as text). Both are facts about where the
#                tokens went, not estimates.
#                Excluded on purpose: cache read, which is the same context re-sent at
#                every internal step rather than new material, and the answer text itself,
#                which DOES arrive here. Claiming the answer as saved would credit the
#                main agent for not reading what it just read.
#                Rows no provider counted cannot answer this — the estimate there measures
#                the prompt, not what was read — so they fall back to
#                premium_context_avoided_tokens and the figure carries `~`, the same
#                measured-else-estimated rule as billable_input/billable_output.
#
# Both numbers carry `~` while any part of them fell back to a char-derived estimate. A
# session whose rows were all provider-counted carries no mark, because nothing in it is
# a guess.
#
# Input: statusline JSON on stdin. Output: single line on stdout.

$raw = [Console]::In.ReadToEnd()
$ctx = $null
if ($raw) { try { $ctx = $raw | ConvertFrom-Json } catch {} }

$ClaudeDir = if ($env:CLAUDE_CONFIG_DIR) { $env:CLAUDE_CONFIG_DIR } else { Join-Path $HOME '.claude' }
$Esc = [char]27
function C([string]$code, [string]$text) { "$Esc[38;5;${code}m$text$Esc[0m" }

function Format-Tok([int64]$n) {
    if ($n -ge 1000000) { return ('{0:0.0}M' -f ($n / 1000000)) }
    if ($n -ge 1000)    { return ('{0:0.0}k' -f ($n / 1000)) }
    return "$n"
}

$segments = @()

# ---------- context fields
$projPath = $null
if ($ctx -and $ctx.workspace -and $ctx.workspace.project_dir) { $projPath = [string]$ctx.workspace.project_dir }
elseif ($ctx -and $ctx.workspace -and $ctx.workspace.current_dir) { $projPath = [string]$ctx.workspace.current_dir }
elseif ($ctx -and $ctx.cwd) { $projPath = [string]$ctx.cwd }
else { $projPath = (Get-Location).Path }

$claudeSid = if ($ctx -and $ctx.session_id) { [string]$ctx.session_id } else { $null }

# ---------- 1. active project name
$projName = Split-Path -Leaf ($projPath.TrimEnd('\', '/'))
if ($projName) { $segments += (C '39' $projName) }

# ---------- 2. second_agent usage
# Claude session id -> MAIN_SESSION_ID mapping lives in session_registry.json,
# written by hooks\session-bind.ps1.
function Get-MainSessionId([string]$sid) {
    if (-not $sid) { return $null }
    $reg = Join-Path $ClaudeDir 'session_registry.json'
    if (-not (Test-Path -LiteralPath $reg)) { return $null }
    try {
        $j = Get-Content -LiteralPath $reg -Raw -ErrorAction Stop | ConvertFrom-Json
        $entry = $j.$sid
        if ($entry -and $entry.main_session_id) { return [string]$entry.main_session_id }
    } catch {}
    return $null
}

function Get-SecondAgentTokens([string]$root, [string]$mainId) {
    if (-not $root) { return $null }
    $usage = Join-Path $root '.workflow\usage.jsonl'
    if (-not (Test-Path -LiteralPath $usage)) { return $null }
    # one pass over the stream, everything scoped to this session
    # input/output kept apart so the headline can exclude cache read, which lives inside
    # the input count and never inside output.
    $res = @{ input = [int64]0; output = [int64]0; cached = [int64]0; calls = 0;
              measured = $true; saved = [int64]0; savedMeasured = $true }
    # A continuation writes one row per provider invocation, all sharing the command's
    # prompt_id. Counting rows would make this number jump whenever a retry happened,
    # which is not a second call from where the user is sitting.
    $seen = @{}
    $reader = $null
    try { $reader = [System.IO.File]::OpenText($usage) } catch { return $null }
    try {
        while ($null -ne ($line = $reader.ReadLine())) {
            if ($line.Length -lt 2) { continue }
            $rec = $null
            try { $rec = $line | ConvertFrom-Json } catch { continue }

            if (-not $mainId -or [string]$rec.session_id -ne $mainId) { continue }

            # This session only, like the token count beside it. A project-lifetime
            # figure next to a session one reads as a ratio that was never measured:
            # right after /clear the bar said "Second Agent 0 | Saved 129.5k", which
            # invites exactly the wrong conclusion and cost nothing to avoid.
            if ($null -ne $rec.actual_input_tokens) {
                $cachedHere = [int64]0
                if ($null -ne $rec.actual_cached_input_tokens) {
                    $cachedHere = [int64]$rec.actual_cached_input_tokens
                }
                $freshHere = [int64]$rec.actual_input_tokens - $cachedHere
                if ($freshHere -lt 0) { $freshHere = [int64]0 }
                $res.saved += $freshHere
                if ($null -ne $rec.actual_reasoning_tokens) {
                    $res.saved += [int64]$rec.actual_reasoning_tokens
                }
            } else {
                if ($null -ne $rec.premium_context_avoided_tokens) {
                    $res.saved += [int64]$rec.premium_context_avoided_tokens
                }
                $res.savedMeasured = $false
            }

            # Provider-reported first, char estimate only where nothing was reported.
            # Mirrors billable_input/billable_output: a measured count wins over the
            # estimate beside it, and the breakdowns are never added to the total.
            if ($null -ne $rec.actual_input_tokens) {
                $res.input += [int64]$rec.actual_input_tokens
            } elseif ($null -ne $rec.estimated_input_tokens) {
                $res.input += [int64]$rec.estimated_input_tokens
            }
            if ($null -ne $rec.actual_output_tokens) {
                $res.output += [int64]$rec.actual_output_tokens
            } elseif ($null -ne $rec.estimated_output_tokens) {
                $res.output += [int64]$rec.estimated_output_tokens
            }
            # Subtracted from the headline, never added to it: cache read is already
            # inside the input count above.
            if ($null -ne $rec.actual_cached_input_tokens) {
                $res.cached += [int64]$rec.actual_cached_input_tokens
            }
            if ([string]$rec.token_source -ne 'provider') { $res.measured = $false }

            if ($rec.prompt_id) {
                $key = [string]$rec.prompt_id
                if (-not $seen.ContainsKey($key)) { $seen[$key] = $true; $res.calls += 1 }
            } else {
                # A reuse hit never built a prompt, so it has no id to group by and is
                # its own piece of work.
                $res.calls += 1
            }
        }
    } catch {} finally { if ($reader) { $reader.Close() } }
    return $res
}

# ---------- compute (cached 30s, keyed by claude session id)
$cacheFile = Join-Path $ClaudeDir '.statusline-tokens.json'
$sa = $null
$fresh = $true
if (Test-Path $cacheFile) {
    try {
        $c = Get-Content -LiteralPath $cacheFile -Raw -ErrorAction Stop | ConvertFrom-Json
        # `v` guards the cache against a schema change. Each bump changed what a stored
        # field MEANS, not just what it holds: v2 kept a project-lifetime `saved` where v3
        # keeps a session one, v3 kept a combined `total` where v4 keeps input and output
        # apart, and v4's `saved` was answer-minus-digest where v5's is what never reached
        # this context at all. Reading an older entry would put a stale number on screen
        # for 30s with nothing to mark it.
        if ($c.v -eq 5 -and $c.sid -eq $claudeSid -and $c.proj -eq $projPath -and
            ((Get-Date) - [datetime]::Parse($c.at, [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::RoundtripKind)).TotalSeconds -lt 30) {
            if ($null -ne $c.sa_input) {
                $sa = @{ input = [int64]$c.sa_input; output = [int64]$c.sa_output;
                         cached = [int64]$c.sa_cached; calls = [int]$c.sa_calls;
                         measured = [bool]$c.sa_measured; saved = [int64]$c.sa_saved;
                         savedMeasured = [bool]$c.sa_saved_measured }
                $fresh = $false
            }
        }
    } catch { $fresh = $true }
}

if ($fresh) {
    $sa = Get-SecondAgentTokens $projPath (Get-MainSessionId $claudeSid)
    try {
        $entry = [ordered]@{ v = 5; sid = $claudeSid; proj = $projPath; at = (Get-Date).ToString('o') }
        if ($sa) {
            $entry['sa_input'] = $sa.input; $entry['sa_output'] = $sa.output
            $entry['sa_cached'] = $sa.cached
            $entry['sa_calls'] = $sa.calls; $entry['sa_measured'] = $sa.measured
            $entry['sa_saved'] = $sa.saved
            $entry['sa_saved_measured'] = $sa.savedMeasured
        }
        [pscustomobject]$entry | ConvertTo-Json -Compress | Set-Content -LiteralPath $cacheFile -Encoding utf8
    } catch {}
}

if ($sa) {
    $mark = ''
    if (-not $sa.measured) { $mark = '~' }
    # Everything the provider processed for this session, cache read included. The cached
    # share follows in brackets because it is usually most of the figure and, unexplained,
    # a number this large reads as a mistake rather than as a fact.
    $text = 'Second Agent ' + $mark + (Format-Tok ($sa.input + $sa.output)) + ' tok'
    if ($sa.cached -gt 0) { $text += ' (' + (Format-Tok $sa.cached) + ' cached)' }
    $text += ' / ' + $sa.calls + ' calls'
    $segments += (C '214' $text)
    # Rendered at zero too, like the count beside it. A segment that disappears reads as
    # broken rather than as empty, and the bar changing shape between sessions is exactly
    # what makes a reader distrust the numbers that remain.
    # `~` only where a row fell back to the char-derived estimate. Provider-counted rows
    # make this a measurement, so marking it an estimate unconditionally — as an earlier
    # version did — would understate what is actually known.
    $savedMark = ''
    if (-not $sa.savedMeasured) { $savedMark = '~' }
    $segments += (C '77' ('Saved ' + $savedMark + (Format-Tok $sa.saved) + ' tok'))
}

[Console]::Write(($segments -join (C '240' ' | ')))
