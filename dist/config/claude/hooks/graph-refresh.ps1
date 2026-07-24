# graph-refresh.ps1 - Stop hook
# Regenerates graphify-out/ after main_agent has actually changed code.
#
# Why a Stop hook and not PostToolUse: PostToolUse fires per tool call, so a single
# /.execute of eight edits would queue eight regenerations of the same graph. Stop fires
# once per turn and carries `last_assistant_message`, which is what makes it possible to
# regenerate after an EXECUTE specifically rather than after every turn.
#
# Two gates, cheapest first:
#   1. Did this turn actually implement something? ([EXECUTION RESULT] / [REFACTOR RESULT])
#   2. Is the graph actually older than the sources? (mtime compare)
# Both must pass. Gate 1 alone would still fire on an execute that changed nothing;
# gate 2 alone would fire on any turn that happened to follow an edit by other means.
#
# Never blocks. A Stop hook CAN block the response (decision:"block" / exit 2); this one
# must never do that -- a stale graph is a degraded lead list, not a reason to withhold
# an answer the user is waiting for. Every path exits 0.

$ErrorActionPreference = 'Stop'

# Sources whose change should invalidate the graph. Deliberately narrow: docs and lock
# files churn constantly and none of them move an import edge.
$SourceExtensions = @(
    '*.py', '*.js', '*.mjs', '*.cjs', '*.ts', '*.tsx', '*.jsx',
    '*.php', '*.go', '*.rs', '*.java', '*.rb'
)

# Directories that are never the subject of a graph and are large enough that walking
# them is the slow part of this hook.
$SkipDirPattern = '[\\/](node_modules|\.git|\.venv|venv|__pycache__|vendor|dist|build|\.next|coverage|graphify-out|\.workflow)[\\/]'

try {
    $raw = [Console]::In.ReadToEnd()
    if ([string]::IsNullOrWhiteSpace($raw)) { exit 0 }

    $payload = $raw | ConvertFrom-Json
    $message = [string]$payload.last_assistant_message
    $cwd     = [string]$payload.cwd
    if ([string]::IsNullOrWhiteSpace($cwd)) { $cwd = (Get-Location).Path }

    # --- Gate 1: did this turn implement anything? ---------------------------------
    # Matching the block headers /.execute and /.refactor are contractually required to
    # emit, not loose words like "implemented": the headers are a format the main agent
    # owes, prose is a coincidence.
    if ($message -notmatch '\[EXECUTION RESULT\]' -and $message -notmatch '\[REFACTOR RESULT\]') {
        exit 0
    }

    $root = $cwd
    try { $root = (Resolve-Path -LiteralPath $cwd -ErrorAction Stop).Path } catch { }

    $graphPath = Join-Path $root 'graphify-out\graph.json'
    if (-not (Test-Path -LiteralPath $graphPath)) {
        # No graph in this project. `graphify init` is explicitly never run automatically
        # -- creating one uninvited is a decision for the user, not for a hook.
        exit 0
    }

    # --- Gate 2: is the graph actually behind the sources? --------------------------
    $graphTime = (Get-Item -LiteralPath $graphPath).LastWriteTimeUtc
    $newest    = $null
    try {
        $newest = Get-ChildItem -LiteralPath $root -Recurse -File -Include $SourceExtensions -ErrorAction SilentlyContinue |
                  Where-Object { $_.FullName -notmatch $SkipDirPattern } |
                  Sort-Object LastWriteTimeUtc -Descending |
                  Select-Object -First 1
    } catch { exit 0 }

    if ($null -eq $newest -or $newest.LastWriteTimeUtc -le $graphTime) {
        exit 0  # graph is current; nothing to do
    }

    # --- Regenerate ----------------------------------------------------------------
    $graphify = $null
    try { $graphify = (Get-Command graphify -ErrorAction Stop).Source } catch { exit 0 }
    if ([string]::IsNullOrWhiteSpace($graphify)) { exit 0 }

    # Output goes to temp files, never to 'NUL'. Start-Process treats a redirect target as
    # a literal path, so -RedirectStandardOutput 'NUL' does not discard anything: it
    # creates a file called NUL in the working directory. A hook that litters the user's
    # repository as a side effect of succeeding is worse than one that does not run.
    $outFile = Join-Path ([System.IO.Path]::GetTempPath()) ("graphify-{0}.out" -f [guid]::NewGuid())
    $errFile = Join-Path ([System.IO.Path]::GetTempPath()) ("graphify-{0}.err" -f [guid]::NewGuid())

    try {
        # `update` only. init/build/watch are never run from here: they can rewrite config
        # and take minutes, and this is a background side effect the user did not ask for.
        $proc = Start-Process -FilePath $graphify -ArgumentList 'update' `
                              -WorkingDirectory $root -NoNewWindow -PassThru `
                              -RedirectStandardOutput $outFile -RedirectStandardError $errFile

        # Bounded on purpose. The hook's own timeout (settings.json) is the outer bound;
        # this inner one exists so a graphify that hangs does not leave a process running
        # after the hook is already gone. A graph too large to index is a known, expected
        # outcome -- not retried and not reported, because there is nothing the user could
        # act on about it mid-turn.
        if (-not $proc.WaitForExit(45000)) {
            try { $proc.Kill() } catch { }
        }
    }
    finally {
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
    }
    exit 0
}
catch {
    # A failed refresh must never cost the user their response.
    exit 0
}
