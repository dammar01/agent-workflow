"""Generation and drift-detection for the .workflow/ run scripts."""

from core.workspace.workspace_paths import WORKFLOW_DIRNAME
from core.workspace.workspace_paths import atomic_write_text
from pathlib import Path
from utils import osutil


def _build_run_scripts(project_root: Path, main_py: str) -> list[tuple[Path, str]]:
    """Compose run/inspect/check scripts so main_agent calls one script.

    Building is separated from writing so doctor can compare what is on disk against what
    this function would produce — a script that drifted out of step with the generator is
    invisible to a check that only asks whether the file exists.

    Each script uses a python resolvable on ITS OWN platform: the current-OS script
    gets the exact interpreter; the cross-OS script gets a generic name (python/python3)
    resolved via PATH on the target machine — so a project copied across OSes still runs.
    """
    from config.settings import DEFAULT_MAX_TASK_CHARS

    ps_py = osutil.python_exe() if osutil.IS_WINDOWS else "python"
    sh_py = osutil.python_exe() if not osutil.IS_WINDOWS else "python3"
    check_py = str(Path(main_py).parent / "check.py")
    root = str(project_root)
    workflow_dir = project_root / WORKFLOW_DIRNAME

    # Background (job) commands go through await+job-command; the rest run directly.
    run_ps1 = (
        "param([Parameter(Mandatory=$true)][string]$Command,"
        '[string]$Task="",'
        "[string]$Session=$env:MAIN_SESSION_ID)\n"
        'if (-not $Session) { $Session = "default" }\n'
        "$bg = @('explore','plan','analyze','verify')\n"
        # session=default on a DELEGATED command is not a warning-shaped problem: two main
        # agents on one project silently share a lock, state and logs, and each overwrites
        # the other's evidence. This used to print to stderr and proceed — but the caller
        # runs this as a background task and never reads that stream, so the one safeguard
        # was invisible exactly when it fired. Refuse instead. Local commands are unaffected:
        # a shared default session costs them nothing.
        'if ($Session -eq "default" -and ($bg -contains $Command) -and -not $env:AI_PROXY_ALLOW_DEFAULT_SESSION) {\n'
        '  [Console]::Error.WriteLine("[workflow] ERROR: session=default on delegated command \'$Command\'. Concurrent main agents on this project would share one lock, state and log directory and overwrite each other. Pass MAIN_SESSION_ID as argument 3 (the value from the [SESSION BINDING] block). To override deliberately: set AI_PROXY_ALLOW_DEFAULT_SESSION=1.")\n'
        "  exit 2\n"
        "}\n"
        # Pre-dispatch task-size warning: the runtime truncates the task at
        # DEFAULT_MAX_TASK_CHARS, silently. Surface it BEFORE dispatch so main_agent shortens
        # the instruction instead of blindly pre-splitting into two calls.
        f'if ($Task.Length -gt {DEFAULT_MAX_TASK_CHARS}) {{ [Console]::Error.WriteLine("[workflow] WARN: task is $($Task.Length) chars > {DEFAULT_MAX_TASK_CHARS}-char cap; it WILL be truncated. Shorten the instruction (do not paste evidence into the task) rather than pre-splitting into multiple calls.") }}\n'
        "if ($bg -contains $Command) {\n"
        # Pre-flight gate: dispatching a delegated run satisfies the gate -> clear the marker
        # so the PreToolUse hook stops blocking gather tools for the rest of this turn.
        f'  $mk = Join-Path "{root}" ".workflow\\sessions\\$Session\\runtime\\delegated.marker"\n'
        "  if (Test-Path -LiteralPath $mk) { Remove-Item -LiteralPath $mk -Force -ErrorAction SilentlyContinue }\n"
        f'  $a = @("{main_py}", "--command", "await", "--job-command", $Command)\n'
        "} else {\n"
        f'  $a = @("{main_py}", "--command", $Command)\n'
        "}\n"
        # PowerShell drops empty-string arguments on their way to a native exe, so a literal
        # `--prompt $Task` with no task reaches argparse as a bare `--prompt` and it errors
        # with "expected one argument". Local commands do not need a prompt at all, so the
        # flag is only appended when there is something to put after it.
        'if ($Task) { $a += @("--prompt", $Task) }\n'
        f'$a += @("--session", $Session, "--work-dir", "{root}", "--pretty")\n'
        f'& "{ps_py}" @a\n'
    )
    run_sh = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'COMMAND="${1:?command required}"\n'
        'TASK="${2:-}"\n'
        'SESSION="${3:-${MAIN_SESSION_ID:-default}}"\n'
        # Same refusal as the PowerShell branch, same reasoning: a shared default session
        # is fatal for delegated commands and harmless for local ones.
        'if [ "$SESSION" = "default" ] && [ -z "${AI_PROXY_ALLOW_DEFAULT_SESSION:-}" ]; then\n'
        '  case " explore plan analyze verify " in\n'
        '    *" $COMMAND "*)\n'
        '      echo "[workflow] ERROR: session=default on delegated command \'$COMMAND\'. Concurrent main agents on this project would share one lock, state and log directory and overwrite each other. Pass MAIN_SESSION_ID as argument 3 (the value from the [SESSION BINDING] block). To override deliberately: set AI_PROXY_ALLOW_DEFAULT_SESSION=1." >&2\n'
        "      exit 2 ;;\n"
        "  esac\n"
        "fi\n"
        f'[ "${{#TASK}}" -gt {DEFAULT_MAX_TASK_CHARS} ] && echo "[workflow] WARN: task is ${{#TASK}} chars > {DEFAULT_MAX_TASK_CHARS}-char cap; it WILL be truncated. Shorten the instruction rather than pre-splitting." >&2\n'
        'case " explore plan analyze verify " in\n'
        '  *" $COMMAND "*)\n'
        # Pre-flight gate: clear the marker before dispatching (delegation satisfies the gate).
        f'    MK="{root}/.workflow/sessions/$SESSION/runtime/delegated.marker"\n'
        '    [ -f "$MK" ] && rm -f "$MK"\n'
        f'    ARGS=("{main_py}" --command await --job-command "$COMMAND") ;;\n'
        "  *)\n"
        f'    ARGS=("{main_py}" --command "$COMMAND") ;;\n'
        "esac\n"
        # Kept in step with the PowerShell branch: no task, no --prompt. Local commands do
        # not take one, and an empty value buys nothing on either platform.
        'if [ -n "$TASK" ]; then ARGS+=(--prompt "$TASK"); fi\n'
        f'ARGS+=(--session "$SESSION" --work-dir "{root}" --pretty)\n'
        f'exec "{sh_py}" "${{ARGS[@]}}"\n'
    )
    inspect_ps1 = (
        f'& "{ps_py}" "{main_py}" --command inspect --work-dir "{root}" --pretty\n'
    )
    inspect_sh = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'exec "{sh_py}" "{main_py}" --command inspect --work-dir "{root}" --pretty\n'
    )
    # Attach to an existing job by id (recovery after a foreground timeout). Passes through
    # flags like --wait --result to check.py, which polls without spawning a new run.
    check_ps1 = (
        "param([Parameter(Mandatory=$true)][string]$JobId,"
        "[Parameter(ValueFromRemainingArguments=$true)]$Rest)\n"
        f'& "{ps_py}" "{check_py}" $JobId @Rest\n'
    )
    check_sh = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'exec "{sh_py}" "{check_py}" "$@"\n'
    )

    # Generate only the current OS's flavour: Windows gets .ps1, POSIX gets .sh. The other
    # flavour is dead weight on this machine and only confuses the Bash-allowlist matcher.
    want_ext = osutil.script_ext()
    return [
        (workflow_dir / name, content)
        for name, content in (
            ("run.ps1", run_ps1),
            ("run.sh", run_sh),
            ("inspect.ps1", inspect_ps1),
            ("inspect.sh", inspect_sh),
            ("check.ps1", check_ps1),
            ("check.sh", check_sh),
        )
        if name.rsplit(".", 1)[-1] == want_ext
    ]

def _read_script(path: Path) -> str | None:
    """Current on-disk text, or None when it is missing or unreadable.

    utf-8-sig strips a BOM if present and is harmless when absent, so every comparison
    against generated content is about the content, never about how a previous writer
    (or an editor, or PowerShell) chose to encode it.
    """
    try:
        return path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None

def _foreign_os_scripts(project_root: Path) -> list[Path]:
    """Entry scripts for the OTHER platform that are still sitting in .workflow/.

    Earlier builds wrote both flavours, so a workspace can carry a .sh that no generator
    has touched since. Nothing on this machine runs it, so nothing notices when it falls
    out of step — and a copy of the project handed to a colleague on Linux would run that
    stale file. Left for the caller to delete rather than silently repaired here.
    """
    want_ext = osutil.script_ext()
    other = "sh" if want_ext == "ps1" else "ps1"
    workflow_dir = project_root / WORKFLOW_DIRNAME
    return [
        path
        for path in (
            workflow_dir / f"{stem}.{other}" for stem in ("run", "inspect", "check")
        )
        if path.exists()
    ]

def script_drift(project_root: Path, main_py: str) -> list[dict]:
    """Scripts on disk that no longer match what the generator produces.

    Each entry is {'script', 'state'} with state 'missing', 'content_differs', or
    'foreign_os_leftover'. A drifted script keeps working right up until the CLI it calls
    changes shape underneath it — the on-disk run.sh routed `sweep` through `--job-command`
    for a whole release cycle after the generator stopped doing so, because nothing
    compared the two.
    """
    drifted: list[dict] = []
    for path, content in _build_run_scripts(project_root, main_py):
        current = _read_script(path)
        if current is None:
            drifted.append({"script": path.name, "state": "missing"})
        elif current != content:
            drifted.append({"script": path.name, "state": "content_differs"})
    drifted.extend(
        {"script": path.name, "state": "foreign_os_leftover"}
        for path in _foreign_os_scripts(project_root)
    )
    return drifted

def _generate_run_scripts(project_root: Path, main_py: str) -> list[str]:
    """Write the scripts _build_run_scripts composes; return the paths actually rewritten.

    Also deletes leftovers for the other platform: keeping a script no generator maintains
    is worse than not having one, because it looks usable.
    """
    written: list[str] = []
    for path in _foreign_os_scripts(project_root):
        try:
            path.unlink()
            written.append(f"removed {path}")
        except OSError:
            pass  # not ours to force; doctor keeps reporting it
    for path, content in _build_run_scripts(project_root, main_py):
        if _read_script(path) == content:
            continue
        if path.suffix == ".ps1":
            # UTF-8 BOM: Windows PowerShell 5.1 reads a no-BOM file as ANSI/Win-1252,
            # which corrupts any non-ASCII byte (em-dash, accented path) -> parse error.
            atomic_write_text(path, content, encoding="utf-8-sig")
        else:
            atomic_write_text(
                path, content
            )  # .sh stays plain UTF-8 (BOM breaks the shebang)
            osutil.make_executable(path)
        written.append(str(path))
    return written
