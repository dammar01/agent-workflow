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
# The refresh itself is detached (DEC-012). On a large Laravel graph `graphify update`
# takes longer than any wait a Stop hook can afford: bounded at 45 s it was killed before
# writing, so the turn paid 47 s and the graph never refreshed (CASE-008). The hook now
# scans, starts this same script as a worker (-WorkerRoot) and returns; the worker runs
# graphify with no time limit (DEC-042), beating the lock while it runs, records one row,
# and removes graphify-out/.refresh.lock.
# graphify rewrites graph.json in place, so a reader that finds the lock treats the graph
# as stale rather than parsing a half-written file.
#
# Never blocks. A Stop hook CAN block the response (decision:"block" / exit 2); this one
# must never do that -- a stale graph is a degraded lead list, not a reason to withhold
# an answer the user is waiting for. Every path exits 0.

param(
    [string]$WorkerRoot = '',
    [long]$HookMs = 0,
    [long]$ScanMs = 0,
    [long]$Visited = 0,
    [long]$Skipped = 0,
    [string]$Token = ''
)

$ErrorActionPreference = 'Stop'

# Sources whose change should invalidate the graph. Deliberately narrow: docs and lock
# files churn constantly and none of them move an import edge.
$SourceExtensions = @('.py', '.js', '.mjs', '.cjs', '.ts', '.tsx', '.jsx', '.php', '.go', '.rs', '.java', '.rb')

# Directories that are never the subject of a graph. They are skipped before the walk
# enters them: walking vendor/ or node_modules/ and filtering afterwards cost 2 s on every
# implementing turn whatever the source size (CASE-008).
$SkipDirs = @('node_modules', '.git', '.venv', 'venv', '__pycache__', 'vendor', 'dist', 'build', '.next', 'coverage', 'target', 'graphify-out', '.workflow')

# graphify runs without a time limit (DEC-042): a large graph takes as long as it takes, and
# the worker never kills it. The lock is held while the worker keeps rewriting `beat`, every
# $BeatIntervalSeconds; a lock whose beat is $BeatStaleSeconds old has no worker behind it. A
# lock without `beat` was written before this rule and keeps the old one: $LegacyMaxAgeSeconds
# and a live pid. core.graph.graph_index and graph-refresh.sh use the same numbers (a test
# parses all three). GRAPH_REFRESH_BEAT_S shortens the interval for tests; a value that is not
# a positive number below the stale window keeps the default, so a bad one cannot stop the hook.
$BeatIntervalSeconds = 15
$BeatStaleSeconds = 120
$LegacyMaxAgeSeconds = 900
$beatOverride = 0.0
if ($env:GRAPH_REFRESH_BEAT_S -and [double]::TryParse($env:GRAPH_REFRESH_BEAT_S.Trim(), [System.Globalization.NumberStyles]::Float, [System.Globalization.CultureInfo]::InvariantCulture, [ref]$beatOverride) -and $beatOverride -gt 0 -and $beatOverride -lt $BeatStaleSeconds) {
    $BeatIntervalSeconds = $beatOverride
}

function Write-RefreshRow([string]$Root, [hashtable]$Fields) {
    # One row in the project's quality stream, beside test and e2e rows. Fail-open: a
    # workspace without .workflow/data gets no row, and a write error is swallowed.
    try {
        $dataDir = Join-Path $Root '.workflow\data'
        if (-not (Test-Path -LiteralPath $dataDir -PathType Container)) { return }
        $row = [ordered]@{
            kind        = 'graph_refresh'
            recorded_at = (Get-Date).ToUniversalTime().ToString('o')
            hook        = 'ps1'
        }
        foreach ($key in $Fields.Keys) { $row[$key] = $Fields[$key] }
        $line = ($row | ConvertTo-Json -Compress) + "`n"
        [System.IO.File]::AppendAllText((Join-Path $dataDir 'quality.jsonl'), $line, (New-Object System.Text.UTF8Encoding($false)))
    } catch { }
}

function Read-LockText([string]$LockPath) {
    # The lock as written, or $null when there is none (or it cannot be read).
    try { return [System.IO.File]::ReadAllText($LockPath) } catch { return $null }
}

function Test-JsonFile([string]$Path) {
    # Whether the file is one complete JSON document. Streamed through the framework's JSON
    # reader rather than ConvertFrom-Json: a large graph would be built in memory just to be
    # dropped, and this runs on every refresh.
    try {
        Add-Type -AssemblyName System.Runtime.Serialization
        $stream = [System.IO.File]::OpenRead($Path)
        try {
            $reader = [System.Runtime.Serialization.Json.JsonReaderWriterFactory]::CreateJsonReader($stream, [System.Xml.XmlDictionaryReaderQuotas]::Max)
            try {
                if ($reader.MoveToContent() -ne [System.Xml.XmlNodeType]::Element) { return $false }
                $reader.Skip()
                while ($reader.Read()) { }
            } finally { $reader.Close() }
        } finally { $stream.Dispose() }
        return $true
    } catch { return $false }
}

function Test-LockHeld([string]$LockPath) {
    # Same rule as core.graph.graph_index.refresh_lock_state. A lock that exists but cannot
    # be read is held while its mtime is inside the beat window: failing open there would
    # read or refresh the graph exactly while its owner is writing the lock, and every
    # writer replaces the file whole, so a live one keeps it young.
    if (-not (Test-Path -LiteralPath $LockPath)) { return $false }
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    try {
        # -ErrorAction Stop: pwsh 7 reports bad JSON as a non-terminating error, which would
        # skip the catch below and read a torn lock as abandoned.
        $lock = Get-Content -LiteralPath $LockPath -Raw -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        if ($null -eq $lock -or $null -eq $lock.started) { throw 'unreadable lock' }
        if ($null -ne $lock.PSObject.Properties['beat']) {
            return (($now - [long]$lock.beat) -lt $BeatStaleSeconds)
        }
        return ((($now - [long]$lock.started) -lt $LegacyMaxAgeSeconds) -and
                [bool](Get-Process -Id ([int]$lock.pid) -ErrorAction SilentlyContinue))
    } catch {
        try {
            # -Force: off Windows a dot-file is hidden, and Get-Item without it finds nothing.
            $age = ([DateTime]::UtcNow - (Get-Item -LiteralPath $LockPath -Force).LastWriteTimeUtc).TotalSeconds
            return ($age -lt $BeatStaleSeconds)
        } catch { }
    }
    return $false
}

function Update-LockBeat([string]$LockPath, [string]$LockToken, [int]$GraphifyPid) {
    # Rewrite `beat` while the lock is still this worker's, staged and swapped in so a
    # reader never sees half a lock. The pid stays whatever the lock names.
    $staged = "$LockPath.beat.$LockToken"
    try {
        $lock = [System.IO.File]::ReadAllText($LockPath) | ConvertFrom-Json -ErrorAction Stop
        if ([string]$lock.token -cne $LockToken) { return }
        $body = '{"pid": ' + [long]$lock.pid + ', "token": "' + $LockToken + '", "started": ' + [long]$lock.started +
                ', "beat": ' + [DateTimeOffset]::UtcNow.ToUnixTimeSeconds() + ', "graphify_pid": ' + $GraphifyPid + '}'
        [System.IO.File]::WriteAllBytes($staged, [System.Text.Encoding]::UTF8.GetBytes($body))
        [System.IO.File]::Replace($staged, $LockPath, [NullString]::Value)
    } catch { }
    finally { Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue }
}

function Set-RefreshLock([string]$LockPath, [int]$HolderPid, [string]$LockToken, [bool]$CreateNew) {
    # The token says who owns the lock; the pid only says whether its holder still runs.
    $now = [DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    $body = '{"pid": ' + $HolderPid + ', "token": "' + $LockToken + '", "started": ' + $now + ', "beat": ' + $now + '}'
    $bytes = [System.Text.Encoding]::UTF8.GetBytes($body)
    if ($CreateNew) {
        $stream = [System.IO.File]::Open($LockPath, [System.IO.FileMode]::CreateNew, [System.IO.FileAccess]::Write)
        try { $stream.Write($bytes, 0, $bytes.Length) } finally { $stream.Dispose() }
        return
    }
    # Replacing a held lock, and only while it is still this hook's: a worker that already
    # finished has removed it, and a lock written back then would name a dead pid with no
    # one left to remove it. Written beside it and swapped in, so a reader never sees it
    # empty or half written. File.Replace needs the lock to exist, which closes the window
    # between the token check and the swap.
    $staged = "$LockPath.$LockToken"
    try {
        [System.IO.File]::WriteAllBytes($staged, $bytes)
        $current = $null
        try { $current = ([System.IO.File]::ReadAllText($LockPath) | ConvertFrom-Json).token } catch { }
        if ($current -cne $LockToken) { return }
        # [NullString]::Value, not $null: PowerShell passes $null to a .NET string as "", which
        # Replace refuses as an illegal backup path.
        [System.IO.File]::Replace($staged, $LockPath, [NullString]::Value)
    }
    finally { Remove-Item -LiteralPath $staged -Force -ErrorAction SilentlyContinue }
}

# --- Worker: run graphify detached from the turn --------------------------------------
if ($WorkerRoot) {
    $graphPath = Join-Path $WorkerRoot 'graphify-out\graph.json'
    $lockPath  = Join-Path $WorkerRoot 'graphify-out\.refresh.lock'
    $outFile = Join-Path ([System.IO.Path]::GetTempPath()) ("graphify-{0}.out" -f [guid]::NewGuid())
    $errFile = Join-Path ([System.IO.Path]::GetTempPath()) ("graphify-{0}.err" -f [guid]::NewGuid())
    $outcome = 'error'; $exitCode = $null; $graphifyMs = 0; $rewritten = $false
    try {
        $before = (Get-Item -LiteralPath $graphPath).LastWriteTimeUtc
        $graphify = (Get-Command graphify -ErrorAction Stop).Source
        $sw = [System.Diagnostics.Stopwatch]::StartNew()
        # `update` only. init/build/watch are never run from here: they can rewrite config
        # and take minutes, and this is a background side effect the user did not ask for.
        # Output goes to temp files, never to 'NUL': Start-Process treats a redirect target
        # as a literal path and would create a file called NUL in the project.
        $start = @{
            FilePath = $graphify; ArgumentList = 'update'; WorkingDirectory = $WorkerRoot; PassThru = $true
            RedirectStandardOutput = $outFile; RedirectStandardError = $errFile
        }
        # -WindowStyle exists only on Windows; pwsh elsewhere refuses the parameter.
        if (($null -eq $IsWindows) -or $IsWindows) { $start.WindowStyle = 'Hidden' }
        $proc = Start-Process @start
        # Holding the handle is what makes ExitCode readable after exit; without it
        # Start-Process -PassThru reports $null.
        $null = $proc.Handle
        Update-LockBeat $lockPath $Token $proc.Id
        # No limit: wait as long as graphify runs, beating so readers know a writer is alive.
        $beatMs = [int]([double]$BeatIntervalSeconds * 1000)
        while (-not $proc.WaitForExit($beatMs)) { Update-LockBeat $lockPath $Token $proc.Id }
        $exitCode = $proc.ExitCode
        $graphifyMs = [long]$sw.Elapsed.TotalMilliseconds
        $rewritten = (Get-Item -LiteralPath $graphPath).LastWriteTimeUtc -ne $before
        # graphify exits 1 on a large graph when only its HTML view fails, having written
        # graph.json (CASE-008): a rewritten graph is the success signal, not the exit code.
        # A rewrite that does not parse is 'corrupt', never a refresh.
        if ($rewritten) {
            $outcome = if (Test-JsonFile $graphPath) { 'refreshed' } else { 'corrupt' }
        }
    } catch {
    } finally {
        Remove-Item -LiteralPath $outFile, $errFile -Force -ErrorAction SilentlyContinue
        Write-RefreshRow $WorkerRoot @{
            outcome = $outcome; hook_ms = $HookMs; scan_ms = $ScanMs; files_visited = $Visited
            dirs_skipped = $Skipped; graphify_ms = $graphifyMs; graphify_exit = $exitCode
            graph_rewritten = $rewritten
        }
        try {
            if ((Get-Content -LiteralPath $lockPath -Raw | ConvertFrom-Json).token -eq $Token) {
                Remove-Item -LiteralPath $lockPath -Force
            }
        } catch { }
    }
    exit 0
}

# --- Hook -----------------------------------------------------------------------------
try {
    $hookClock = [System.Diagnostics.Stopwatch]::StartNew()
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

    $lockPath = Join-Path $root 'graphify-out\.refresh.lock'
    $seenLock = Read-LockText $lockPath
    # A lock that changed since it was judged belongs to a hook that just took it: deleting
    # it as stale would start a second graphify beside the first. Re-read right before the
    # delete, and only the lock judged stale is removed.
    if ((Test-LockHeld $lockPath) -or ($null -ne $seenLock -and (Read-LockText $lockPath) -cne $seenLock)) {
        # A refresh from an earlier turn is still running; it will pick up this change or
        # the next implementing turn will.
        Write-RefreshRow $root @{ outcome = 'skipped_running'; hook_ms = [long]$hookClock.Elapsed.TotalMilliseconds }
        exit 0
    }
    if ($null -ne $seenLock) { Remove-Item -LiteralPath $lockPath -Force -ErrorAction SilentlyContinue }

    # --- Gate 2: is the graph actually behind the sources? --------------------------
    $graphTime = (Get-Item -LiteralPath $graphPath).LastWriteTimeUtc
    $scanClock = [System.Diagnostics.Stopwatch]::StartNew()
    $newest = $null; $visited = 0; $skipped = 0
    $stack = New-Object System.Collections.Generic.Stack[string]
    $stack.Push($root)
    while ($stack.Count -gt 0) {
        $dir = $stack.Pop()
        try { $entries = [System.IO.Directory]::EnumerateFileSystemEntries($dir) } catch { continue }
        foreach ($entry in $entries) {
            $name = [System.IO.Path]::GetFileName($entry)
            if ([System.IO.Directory]::Exists($entry)) {
                if ($SkipDirs -contains $name) { $skipped++ } else { $stack.Push($entry) }
                continue
            }
            if ($SourceExtensions -notcontains [System.IO.Path]::GetExtension($name).ToLower()) { continue }
            $visited++
            $t = [System.IO.File]::GetLastWriteTimeUtc($entry)
            if ($null -eq $newest -or $t -gt $newest) { $newest = $t }
        }
    }
    $scanMs = [long]$scanClock.Elapsed.TotalMilliseconds

    if ($null -eq $newest -or $newest -le $graphTime) {
        Write-RefreshRow $root @{
            outcome = 'skipped_fresh'; hook_ms = [long]$hookClock.Elapsed.TotalMilliseconds
            scan_ms = $scanMs; files_visited = $visited; dirs_skipped = $skipped
        }
        exit 0  # graph is current; nothing to do
    }

    try { $null = Get-Command graphify -ErrorAction Stop } catch {
        Write-RefreshRow $root @{
            outcome = 'no_graphify'; hook_ms = [long]$hookClock.Elapsed.TotalMilliseconds
            scan_ms = $scanMs; files_visited = $visited; dirs_skipped = $skipped
        }
        exit 0
    }

    # --- Regenerate, detached --------------------------------------------------------
    # CreateNew makes the lock the tie-breaker between two hooks that both found the graph
    # stale; the loser leaves the refresh to the winner. The hook rewrites it with the
    # worker's pid once the worker is started.
    $token = [guid]::NewGuid().ToString('N')
    try { Set-RefreshLock $lockPath $PID $token $true } catch { exit 0 }
    $hookMs = [long]$hookClock.Elapsed.TotalMilliseconds
    $shell = (Get-Process -Id $PID).Path
    $workerArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`" -WorkerRoot `"$root`" " +
                  "-HookMs $hookMs -ScanMs $scanMs -Visited $visited -Skipped $skipped -Token $token"
    # The worker is created through WMI, not Start-Process. Start-Process -WindowStyle
    # Hidden asks for SW_HIDE, which Windows 11 ignores when it hands a new console to
    # Windows Terminal: a terminal flashes on every refresh. CreateNoWindow would avoid the
    # console but needs inherited handles, and inheriting the pipes Claude Code reads the
    # hook through makes it wait for graphify. A WMI-created process inherits nothing from
    # this hook, gets a hidden console, and is outside the hook's job, so it outlives it.
    $workerPid = 0
    # $IsWindows is absent on Windows PowerShell 5.1, which only runs on Windows.
    $onWindows = ($null -eq $IsWindows) -or $IsWindows
    if (-not $onWindows) {
        # pwsh off Windows: no WMI, and -WindowStyle throws, which left the refresh unstarted
        # behind a lock naming this exiting hook. Output goes to temp files so the worker does
        # not inherit the pipes the hook is read through, which would make the caller wait
        # for graphify.
        $workerPid = (Start-Process -FilePath $shell -PassThru -ArgumentList $workerArgs `
            -RedirectStandardOutput ([System.IO.Path]::GetTempFileName()) `
            -RedirectStandardError ([System.IO.Path]::GetTempFileName())).Id
    }
    else { try {
        $envList = [string[]]@(Get-ChildItem env: | ForEach-Object { "$($_.Name)=$($_.Value)" })
        $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{
            ShowWindow = [uint16]0; EnvironmentVariables = $envList
        }
        $created = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
            CommandLine = "`"$shell`" $workerArgs"; CurrentDirectory = $root; ProcessStartupInformation = $startup
        }
        if ($created.ReturnValue -eq 0) { $workerPid = [int]$created.ProcessId }
    } catch { } }
    if (-not $workerPid -and $onWindows) {
        # No WMI (service stopped, policy): a hidden window that may flash beats no refresh.
        $workerPid = (Start-Process -FilePath $shell -WindowStyle Hidden -PassThru -ArgumentList $workerArgs).Id
    }
    # Hand the lock to the worker before this hook exits: a lock naming the hook's own pid
    # would read as abandoned the moment it returns, while graphify is still writing.
    # Only while it is still this hook's (Set-RefreshLock checks the token).
    try { Set-RefreshLock $lockPath $workerPid $token $false } catch { }
    exit 0
}
catch {
    # A failed refresh must never cost the user their response.
    exit 0
}
