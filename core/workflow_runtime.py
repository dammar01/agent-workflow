import hashlib
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

from utils import osutil


WORKFLOW_DIRNAME = ".workflow"
LOCK_TTL_SECONDS = 300
JSON_INDENT = 2
ARCHIVE_KEEP = 20


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write_text(path: Path, content: str) -> None:
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.parent.mkdir(parents=True, exist_ok=True)
    temp.write_text(content, encoding="utf-8")
    temp.replace(path)


def atomic_write_json(path: Path, payload: dict) -> None:
    atomic_write_text(path, json.dumps(payload, indent=JSON_INDENT))


def detect_project_root(work_dir: str | None = None) -> Path:
    start = Path(work_dir).resolve() if work_dir else Path.cwd().resolve()
    current = start

    for candidate in [current, *current.parents]:
        if (candidate / ".git").exists():
            return candidate
    return start


def slugify_project_name(name: str) -> str:
    safe = []
    for char in name.lower():
        safe.append(char if char.isalnum() else "-")
    slug = "".join(safe).strip("-")
    return slug or "project"


def workflow_paths(project_root: Path) -> dict[str, Path]:
    workflow_dir = project_root / WORKFLOW_DIRNAME
    runtime_dir = workflow_dir / "runtime"
    reports_dir = workflow_dir / "reports"
    logs_dir = workflow_dir / "logs"
    return {
        "project_root": project_root,
        "workflow_dir": workflow_dir,
        "config": workflow_dir / "config.json",
        "state": workflow_dir / "state.json",
        "scope": workflow_dir / "scope.json",
        "command_cache": workflow_dir / "command-cache.json",
        "gitignore": workflow_dir / ".gitignore",
        "runtime_dir": runtime_dir,
        "prompt": runtime_dir / "prompt.txt",
        "prompt_meta": runtime_dir / "prompt.meta.json",
        "response_last": runtime_dir / "response.last.md",
        "lock": runtime_dir / "lock",
        "reports_dir": reports_dir,
        "doctor_report": reports_dir / "doctor.json",
        "sweep_report": reports_dir / "sweep.last.md",
        "logs_dir": logs_dir,
    }


def _tool_paths(agent_workflow_path: str | None) -> dict:
    """Resolve absolute tool paths (main.py/check.py) so .workflow is self-contained."""
    from config.settings import CHECK_PY, MAIN_PY, TOOL_VERSION

    main_py = Path(agent_workflow_path).resolve() if agent_workflow_path else MAIN_PY
    tool_dir = main_py.parent
    check_py = tool_dir / "check.py"
    if not check_py.exists():
        check_py = CHECK_PY
    return {
        "main_py_path": str(main_py),
        "check_py_path": str(check_py),
        "tool_dir": str(tool_dir),
        "tool_version": TOOL_VERSION,
    }


def default_config(project_root: Path, agent_workflow_path: str | None) -> dict:
    project_name = project_root.name
    tool = _tool_paths(agent_workflow_path)
    return {
        "version": "3.3.0",
        "project": {
            "name": project_name,
            "slug": slugify_project_name(project_name),
            "root": str(project_root),
        },
        "runtime": {
            "agent_workflow_path": agent_workflow_path,
            "main_py_path": tool["main_py_path"],
            "check_py_path": tool["check_py_path"],
            "tool_dir": tool["tool_dir"],
            "tool_version": tool["tool_version"],
            "second_agent": "opencode",
            "main_agent": "agnostic",
            "opencode_config": ".workflow/opencode.json",
            "prompt_file": ".workflow/runtime/prompt.txt",
            "response_file": ".workflow/runtime/response.last.md",
            "meta_file": ".workflow/runtime/prompt.meta.json",
            "lock_file": ".workflow/runtime/lock",
            "logs_dir": ".workflow/logs",
        },
        "commands": {
            "allow_analyze_to_plan": True,
            "allow_explore_to_plan": True,
            "auto_sweep_after_execute": True,
        },
        "policies": {
            "workflow_prefix": "/.",
            "chat_mode_for_plain_text": True,
            "fallback_requires_confirmation": True,
            "max_active_job_per_session": 1,
        },
    }


def default_state(project_root: Path) -> dict:
    project_name = project_root.name
    return {
        "version": 1,
        "project": {
            "name": project_name,
            "root": str(project_root),
            "slug": slugify_project_name(project_name),
        },
        "session": None,
        "workflow": {
            "stage": "initialized",
            "last_command": None,
            "objective": None,
            "plan_readiness": "unknown",
        },
        "context": {
            "evidence_summary": [],
            "affected_files": [],
            "affected_symbols": [],
            "assumptions": [],
            "risks": [],
            "open_questions": [],
        },
        "guards": {
            "state_version": 1,
            "scope_version": 0,
            "last_prompt_id": None,
        },
    }


def default_scope() -> dict:
    return {
        "session_id": None,
        "editable_scope": [],
        "readable_scope": [],
        "impact_radius": [],
        "out_of_scope": [],
        "verification_targets": [],
        "post_edit_checks": [
            "git_diff",
            "changed_symbol_reference_search",
            "caller_callee_check",
            "type_contract_check",
            "config_flag_check",
            "ui_api_consumer_check",
        ],
    }


def default_command_cache() -> dict:
    return {
        "session_id": None,
        "last_explore_result": None,
        "last_analyze_result": None,
        "last_plan_result": None,
        "last_execute_diff": None,
        "last_sweep_result": None,
    }


def read_json_file(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"invalid JSON object in {path}")
    return data


def ensure_valid_json_or_create(path: Path, factory) -> tuple[str, dict]:
    if path.exists():
        return "existing", read_json_file(path)
    payload = factory()
    atomic_write_json(path, payload)
    return "created", payload


def _copy_opencode_config(project_root: Path, tool_dir: str) -> str | None:
    """Copy the tool's opencode.json into .workflow so it is project-local and overridable."""
    dest = project_root / WORKFLOW_DIRNAME / "opencode.json"
    if dest.exists():
        return str(dest)  # already project-local; never overwrite user edits
    src = Path(tool_dir) / "config" / "opencode.json"
    if not src.exists():
        src = Path(tool_dir) / "config" / "opencode.example.json"
    if src.exists():
        shutil.copyfile(src, dest)
        return str(dest)
    return None


def _generate_run_scripts(project_root: Path, main_py: str) -> list[str]:
    """Emit run.ps1 + run.sh + inspect.ps1 + inspect.sh so main_agent calls one script.

    Each script uses a python resolvable on ITS OWN platform: the current-OS script
    gets the exact interpreter; the cross-OS script gets a generic name (python/python3)
    resolved via PATH on the target machine — so a project copied across OSes still runs.
    """
    ps_py = osutil.python_exe() if osutil.IS_WINDOWS else "python"
    sh_py = osutil.python_exe() if not osutil.IS_WINDOWS else "python3"
    root = str(project_root)
    workflow_dir = project_root / WORKFLOW_DIRNAME
    written: list[str] = []

    # Background (job) commands go through await+job-command; the rest run directly.
    run_ps1 = (
        'param([Parameter(Mandatory=$true)][string]$Command,'
        '[string]$Task="",'
        '[string]$Session=$env:MAIN_SESSION_ID)\n'
        'if (-not $Session) { $Session = "default" }\n'
        "$bg = @('explore','plan','analyze','verify','sweep')\n"
        'if ($bg -contains $Command) {\n'
        f'  & "{ps_py}" "{main_py}" --command await --job-command $Command '
        f'--prompt $Task --session $Session --work-dir "{root}" --pretty\n'
        '} else {\n'
        f'  & "{ps_py}" "{main_py}" --command $Command --work-dir "{root}" --pretty\n'
        '}\n'
    )
    run_sh = (
        '#!/usr/bin/env bash\n'
        'set -euo pipefail\n'
        'COMMAND="${1:?command required}"\n'
        'TASK="${2:-}"\n'
        'SESSION="${3:-${MAIN_SESSION_ID:-default}}"\n'
        'case " explore plan analyze verify sweep " in\n'
        '  *" $COMMAND "*)\n'
        f'    exec "{sh_py}" "{main_py}" --command await --job-command "$COMMAND" '
        f'--prompt "$TASK" --session "$SESSION" --work-dir "{root}" --pretty ;;\n'
        '  *)\n'
        f'    exec "{sh_py}" "{main_py}" --command "$COMMAND" --work-dir "{root}" --pretty ;;\n'
        'esac\n'
    )
    inspect_ps1 = f'& "{ps_py}" "{main_py}" --command inspect --work-dir "{root}" --pretty\n'
    inspect_sh = (
        '#!/usr/bin/env bash\n'
        'set -euo pipefail\n'
        f'exec "{sh_py}" "{main_py}" --command inspect --work-dir "{root}" --pretty\n'
    )

    for name, content in (
        ("run.ps1", run_ps1),
        ("run.sh", run_sh),
        ("inspect.ps1", inspect_ps1),
        ("inspect.sh", inspect_sh),
    ):
        path = workflow_dir / name
        atomic_write_text(path, content)
        if name.endswith(".sh"):
            osutil.make_executable(path)
        written.append(str(path))
    return written


def ensure_workflow_workspace(project_root: Path, agent_workflow_path: str | None) -> dict:
    paths = workflow_paths(project_root)
    paths["workflow_dir"].mkdir(parents=True, exist_ok=True)
    paths["runtime_dir"].mkdir(parents=True, exist_ok=True)
    paths["reports_dir"].mkdir(parents=True, exist_ok=True)
    paths["logs_dir"].mkdir(parents=True, exist_ok=True)

    created_files: list[str] = []
    existing_files: list[str] = []

    mapping = {
        paths["config"]: lambda: default_config(project_root, agent_workflow_path),
        paths["state"]: lambda: default_state(project_root),
        paths["scope"]: default_scope,
        paths["command_cache"]: default_command_cache,
    }
    for path, factory in mapping.items():
        status, _payload = ensure_valid_json_or_create(path, factory)
        (created_files if status == "created" else existing_files).append(str(path))

    text_defaults = {
        paths["gitignore"]: "*\n",
        paths["prompt"]: "",
        paths["response_last"]: "",
        paths["sweep_report"]: "",
    }
    for path, content in text_defaults.items():
        if path.exists():
            existing_files.append(str(path))
        else:
            atomic_write_text(path, content)
            created_files.append(str(path))

    if paths["prompt_meta"].exists():
        read_json_file(paths["prompt_meta"])
        existing_files.append(str(paths["prompt_meta"]))
    else:
        atomic_write_json(
            paths["prompt_meta"],
            {
                "prompt_id": None,
                "session_id": None,
                "project_root": str(project_root),
                "command": None,
                "state_version": 1,
                "scope_version": 0,
                "created_at": now_iso(),
                "status": "idle",
            },
        )
        created_files.append(str(paths["prompt_meta"]))

    tool = _tool_paths(agent_workflow_path)
    opencode_copied = _copy_opencode_config(project_root, tool["tool_dir"])
    generated_scripts = _generate_run_scripts(project_root, tool["main_py_path"])

    gitignore_updated = ensure_root_gitignore_entry(project_root)
    return {
        "project_root": str(project_root),
        "workflow_dir": str(paths["workflow_dir"]),
        "created_files": created_files,
        "existing_files": existing_files,
        "gitignore_updated": gitignore_updated,
        "opencode_config": opencode_copied,
        "generated_scripts": generated_scripts,
        "tool": tool,
    }


def ensure_root_gitignore_entry(project_root: Path) -> bool:
    gitignore_path = project_root / ".gitignore"
    desired = ".workflow/"
    if not gitignore_path.exists():
        atomic_write_text(gitignore_path, f"{desired}\n")
        return True

    content = gitignore_path.read_text(encoding="utf-8")
    lines = [line.strip() for line in content.splitlines()]
    if desired in lines:
        return False
    suffix = "" if not content or content.endswith("\n") else "\n"
    atomic_write_text(gitignore_path, f"{content}{suffix}{desired}\n")
    return True


def load_workspace_state(project_root: Path) -> dict:
    paths = workflow_paths(project_root)
    return {
        "config": read_json_file(paths["config"]),
        "state": read_json_file(paths["state"]),
        "scope": read_json_file(paths["scope"]),
        "command_cache": read_json_file(paths["command_cache"]),
        "paths": paths,
    }


def reset_active_workflow_state(project_root: Path, session_id: str) -> dict:
    loaded = load_workspace_state(project_root)
    state = default_state(project_root)
    state["session"] = {"id": session_id, "bound_at": now_iso()}
    scope = default_scope()
    scope["session_id"] = session_id
    command_cache = default_command_cache()
    command_cache["session_id"] = session_id
    atomic_write_json(loaded["paths"]["state"], state)
    atomic_write_json(loaded["paths"]["scope"], scope)
    atomic_write_json(loaded["paths"]["command_cache"], command_cache)
    return {"state": state, "scope": scope, "command_cache": command_cache, "paths": loaded["paths"]}


def bind_session(project_root: Path, session_id: str) -> dict:
    loaded = load_workspace_state(project_root)
    state = loaded["state"]
    current = state.get("session") or {}
    current_id = current.get("id") if isinstance(current, dict) else None
    if not current_id:
        state["session"] = {"id": session_id, "bound_at": now_iso()}
        atomic_write_json(loaded["paths"]["state"], state)
        scope = loaded["scope"]
        scope["session_id"] = session_id
        atomic_write_json(loaded["paths"]["scope"], scope)
        command_cache = loaded["command_cache"]
        command_cache["session_id"] = session_id
        atomic_write_json(loaded["paths"]["command_cache"], command_cache)
        loaded["state"] = state
        loaded["scope"] = scope
        loaded["command_cache"] = command_cache
        loaded["session_reset"] = False
        return loaded
    if current_id != session_id:
        reset = reset_active_workflow_state(project_root, session_id)
        reset["session_reset"] = True
        return reset
    loaded["session_reset"] = False
    return loaded


def extract_lines_by_prefix(text: str, prefixes: tuple[str, ...]) -> list[str]:
    results: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line.startswith("-"):
            continue
        value = line[1:].strip()
        for prefix in prefixes:
            if value.startswith(prefix):
                results.append(value[len(prefix):].strip())
                break
    return [item for item in results if item]


def maybe_extract_plan_readiness(text: str) -> str:
    lowered = text.lower()
    if "ready" in lowered and "not ready" not in lowered:
        return "ready"
    if "partial" in lowered:
        return "partial"
    if "not ready" in lowered:
        return "not_ready"
    return "unknown"


def update_state_from_agent_output(project_root: Path, command: str, objective: str, content: str, session_id: str) -> dict:
    loaded = bind_session(project_root, session_id=session_id)
    state = loaded["state"]
    state["workflow"]["stage"] = command
    state["workflow"]["last_command"] = command
    state["workflow"]["objective"] = objective
    state["workflow"]["plan_readiness"] = maybe_extract_plan_readiness(content)
    state["context"]["evidence_summary"] = [line.strip() for line in content.splitlines() if line.strip()][:10]
    state["context"]["affected_files"] = extract_lines_by_prefix(content, ("file:", "path:"))
    state["context"]["affected_symbols"] = extract_lines_by_prefix(content, ("symbol:",))
    state["context"]["assumptions"] = extract_lines_by_prefix(content, ("assumption:",))
    state["context"]["risks"] = extract_lines_by_prefix(content, ("risk:",))
    state["context"]["open_questions"] = extract_lines_by_prefix(content, ("question:", "uncertainty:"))
    state["guards"]["state_version"] = int(state["guards"].get("state_version", 0)) + 1
    atomic_write_json(loaded["paths"]["state"], state)
    return state


def update_plan_scope(project_root: Path, content: str, session_id: str) -> dict:
    loaded = load_workspace_state(project_root)
    scope = loaded["scope"]
    before = json.dumps(scope, sort_keys=True)
    scope["session_id"] = session_id
    scope["editable_scope"] = extract_lines_by_prefix(content, ("editable:",))
    scope["readable_scope"] = extract_lines_by_prefix(content, ("readable:",))
    scope["impact_radius"] = extract_lines_by_prefix(content, ("impact:",))
    scope["out_of_scope"] = extract_lines_by_prefix(content, ("out_of_scope:",))
    scope["verification_targets"] = extract_lines_by_prefix(content, ("verify:",))
    after = json.dumps(scope, sort_keys=True)

    state = loaded["state"]
    if before != after:
        state["guards"]["scope_version"] = int(state["guards"].get("scope_version", 0)) + 1
        atomic_write_json(loaded["paths"]["state"], state)
    atomic_write_json(loaded["paths"]["scope"], scope)
    return scope


def update_command_cache(project_root: Path, key: str, value, session_id: str) -> dict:
    loaded = load_workspace_state(project_root)
    command_cache = loaded["command_cache"]
    command_cache["session_id"] = session_id
    command_cache[key] = value
    atomic_write_json(loaded["paths"]["command_cache"], command_cache)
    return command_cache


def resolve_agent_workflow_path(project_root: Path) -> dict:
    paths = workflow_paths(project_root)
    config_path = paths["config"]
    candidates = []

    config_candidate = None
    if config_path.exists():
        try:
            config = read_json_file(config_path)
            runtime = config.get("runtime") if isinstance(config, dict) else None
            if isinstance(runtime, dict):
                config_candidate = runtime.get("agent_workflow_path")
        except ValueError:
            config_candidate = None
    if config_candidate:
        candidates.append({"source": "workflow_config", "path": config_candidate})

    env_candidate = os.getenv("AGENT_PATH")
    if env_candidate:
        candidates.append({"source": "env", "path": env_candidate})

    for candidate in candidates:
        path = Path(candidate["path"]).expanduser()
        if path.exists() and path.suffix == ".py":
            return {"ok": True, "path": str(path.resolve()), "source": candidate["source"], "candidates": candidates}
    return {"ok": False, "path": None, "source": None, "candidates": candidates}


def acquire_runtime_lock(lock_path: Path, command: str, session_id: str) -> dict:
    if lock_path.exists():
        try:
            payload = read_json_file(lock_path)
        except ValueError:
            payload = None
        if payload:
            created_at = payload.get("created_at")
            try:
                created = datetime.fromisoformat(created_at)
            except (TypeError, ValueError):
                created = None
            if created and datetime.now(timezone.utc) - created <= timedelta(seconds=LOCK_TTL_SECONDS):
                return {"ok": False, "stale": False, "payload": payload}
    stale_payload = None
    if lock_path.exists():
        try:
            stale_payload = read_json_file(lock_path)
        except ValueError:
            stale_payload = {"invalid": True}
    atomic_write_json(lock_path, {"command": command, "session_id": session_id, "created_at": now_iso()})
    return {"ok": True, "stale": bool(stale_payload), "payload": stale_payload}


def release_runtime_lock(lock_path: Path) -> None:
    try:
        lock_path.unlink()
    except FileNotFoundError:
        pass


def _prune_archive(logs_dir: Path, keep: int = ARCHIVE_KEEP) -> None:
    """Keep only the newest `keep` per-run archive folders."""
    if not logs_dir.exists():
        return
    runs = sorted((p for p in logs_dir.iterdir() if p.is_dir()), key=lambda p: p.name, reverse=True)
    for stale in runs[keep:]:
        shutil.rmtree(stale, ignore_errors=True)


def _archive_prompt(logs_dir: Path, prompt_id: str, prompt: str) -> None:
    run_dir = logs_dir / prompt_id
    run_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_text(run_dir / "prompt.md", prompt)
    atomic_write_text(run_dir / "prompt.sha256", hashlib.sha256(prompt.encode("utf-8")).hexdigest())
    _prune_archive(logs_dir)


def write_prompt_handoff(project_root: Path, command: str, session_id: str, prompt: str) -> dict:
    loaded = load_workspace_state(project_root)
    lock_result = acquire_runtime_lock(loaded["paths"]["lock"], command, session_id)
    if not lock_result["ok"]:
        return {
            "ok": False,
            "content": f"runtime lock active for session {lock_result['payload'].get('session_id')}",
            "meta": {"lock": lock_result["payload"], "lock_path": str(loaded["paths"]["lock"])}
        }

    state = loaded["state"]
    prompt_id = f"{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{command}"
    meta = {
        "prompt_id": prompt_id,
        "session_id": session_id,
        "project_root": str(project_root),
        "command": command,
        "state_version": state.get("guards", {}).get("state_version", 1),
        "scope_version": state.get("guards", {}).get("scope_version", 0),
        "created_at": now_iso(),
        "status": "ready",
    }
    if lock_result["stale"]:
        meta["stale_lock_replaced"] = True

    prompt_tmp = loaded["paths"]["prompt"].with_suffix(".tmp")
    prompt_meta_tmp = loaded["paths"]["prompt_meta"].with_suffix(".tmp")
    prompt_tmp.write_text(prompt, encoding="utf-8")
    prompt_meta_tmp.write_text(json.dumps(meta, indent=JSON_INDENT), encoding="utf-8")
    prompt_tmp.replace(loaded["paths"]["prompt"])
    prompt_meta_tmp.replace(loaded["paths"]["prompt_meta"])

    # Per-run archive (rolling): prompt + checksum, keyed by prompt_id.
    _archive_prompt(loaded["paths"]["logs_dir"], prompt_id, prompt)

    state["guards"]["last_prompt_id"] = prompt_id
    atomic_write_json(loaded["paths"]["state"], state)
    return {"ok": True, "meta": meta, "paths": loaded["paths"]}


def write_response_snapshot(project_root: Path, content: str, prompt_id: str | None = None) -> None:
    paths = workflow_paths(project_root)
    atomic_write_text(paths["response_last"], content)
    if prompt_id:
        run_dir = paths["logs_dir"] / prompt_id
        run_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(run_dir / "output.raw.md", content)


def check_writable(path: Path) -> tuple[bool, str | None]:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8"):
            pass
        return True, None
    except OSError as exc:
        return False, str(exc)


def python_callable() -> tuple[bool, str]:
    try:
        completed = subprocess.run([shutil.which("python") or "python", "--version"], capture_output=True, text=True, check=False)
    except OSError as exc:
        return False, str(exc)
    output = (completed.stdout or completed.stderr or "").strip()
    return completed.returncode == 0, output


def opencode_callable(command_name: str) -> tuple[bool, str]:
    # Presence check ONLY — do not invoke. `opencode --help` can resolve to a native
    # .exe shim that false-negatives ("not compatible with this Windows version"),
    # while `opencode.cmd run` (what the workflow actually uses) works fine.
    resolved = shutil.which(f"{command_name}.cmd") or shutil.which(command_name)
    if resolved:
        return True, resolved
    return False, f"{command_name} not found in PATH"


def run_doctor(project_root: Path, opencode_command: str) -> dict:
    paths = workflow_paths(project_root)
    issues: list[str] = []
    recommended_fixes: list[str] = []
    checks: dict[str, object] = {}

    checks["project_root_valid"] = project_root.exists()
    if not paths["workflow_dir"].exists():
        issues.append(".workflow directory missing")
        recommended_fixes.append("Run init first")

    json_targets = {
        "config_json_valid": paths["config"],
        "state_json_valid": paths["state"],
        "scope_json_valid": paths["scope"],
        "command_cache_json_valid": paths["command_cache"],
    }
    for key, path in json_targets.items():
        try:
            checks[key] = path.exists() and isinstance(read_json_file(path), dict)
        except ValueError as exc:
            checks[key] = False
            issues.append(str(exc))
            recommended_fixes.append(f"Fix invalid JSON at {path}")

    runtime_writable, runtime_error = check_writable(paths["runtime_dir"] / ".touch")
    try:
        (paths["runtime_dir"] / ".touch").unlink()
    except FileNotFoundError:
        pass
    checks["runtime_folder_writable"] = runtime_writable
    if runtime_error:
        issues.append(f"runtime folder not writable: {runtime_error}")

    prompt_writable, prompt_error = check_writable(paths["prompt"])
    checks["prompt_writable"] = prompt_writable
    if prompt_error:
        issues.append(f"prompt.txt not writable: {prompt_error}")

    try:
        if paths["prompt_meta"].exists():
            read_json_file(paths["prompt_meta"])
        checks["prompt_meta_valid_or_creatable"] = True
    except ValueError as exc:
        checks["prompt_meta_valid_or_creatable"] = False
        issues.append(str(exc))

    lock_state = "missing"
    if paths["lock"].exists():
        try:
            lock_data = read_json_file(paths["lock"])
            created = datetime.fromisoformat(lock_data.get("created_at"))
            lock_state = "active" if datetime.now(timezone.utc) - created <= timedelta(seconds=LOCK_TTL_SECONDS) else "stale"
        except Exception:
            lock_state = "stale"
    checks["lock_state"] = lock_state

    reports_writable, reports_error = check_writable(paths["doctor_report"])
    checks["reports_folder_writable"] = reports_writable
    if reports_error:
        issues.append(f"reports folder not writable: {reports_error}")

    gitignore_ok = False
    gitignore_path = project_root / ".gitignore"
    if gitignore_path.exists():
        gitignore_ok = ".workflow/" in [line.strip() for line in gitignore_path.read_text(encoding="utf-8").splitlines()]
    checks["root_gitignore_ignores_workflow"] = gitignore_ok
    if not gitignore_ok:
        issues.append("root .gitignore does not ignore .workflow/")
        recommended_fixes.append("Add .workflow/ to root .gitignore or rerun init")

    resolver = resolve_agent_workflow_path(project_root)
    checks["agent_workflow_resolver"] = resolver
    configured_path = None
    try:
        configured_path = read_json_file(paths["config"]).get("runtime", {}).get("agent_workflow_path")
    except Exception:
        configured_path = None
    checks["runtime_agent_workflow_path_valid"] = bool(configured_path and Path(configured_path).exists()) if configured_path else None
    env_agent_path = os.getenv("AGENT_PATH")
    checks["env_agent_path_valid"] = bool(env_agent_path and Path(env_agent_path).exists() and Path(env_agent_path).suffix == ".py") if env_agent_path else None

    python_ok, python_output = python_callable()
    checks["python_callable"] = {"ok": python_ok, "output": python_output}
    if not python_ok:
        issues.append("python not callable")
        recommended_fixes.append("Ensure python is installed and available in PATH")

    opencode_ok, opencode_output = opencode_callable(opencode_command)
    checks["opencode_callable"] = {"ok": opencode_ok, "output": opencode_output}
    if not opencode_ok:
        issues.append("opencode not callable")
        recommended_fixes.append("Ensure opencode CLI is installed and available in PATH")

    checks["graphify_out_exists"] = (project_root / "graphify-out").exists()

    # Session continuation: is the current main session linked to an opencode session?
    # An unlinked session re-bootstraps opencode every call (breaks 1 main = 1 second).
    import re as _re
    from config.settings import SESSION_DIR

    session_id = None
    try:
        session_block = read_json_file(paths["state"]).get("session")
        if isinstance(session_block, dict):
            session_id = session_block.get("id")
    except (ValueError, OSError):
        session_id = None

    if not session_id:
        checks["session_continuation"] = "no active session (state.json)"
    else:
        safe = _re.sub(r"[^A-Za-z0-9_.-]", "_", session_id)
        session_file = Path(SESSION_DIR) / f"{safe}.json"
        if not session_file.exists():
            checks["session_continuation"] = f"no session record for {session_id} (first delegated call will bootstrap)"
        else:
            try:
                opencode_id = read_json_file(session_file).get("opencode_session_id")
            except (ValueError, OSError):
                opencode_id = None
            if opencode_id:
                checks["session_continuation"] = f"linked: {session_id} -> {opencode_id}"
            else:
                checks["session_continuation"] = f"BROKEN: {session_id} has no opencode_session_id — continuation re-bootstraps each call"
                issues.append("session continuation broken: opencode_session_id not captured for active session")
                recommended_fixes.append("Re-run a delegated command; if it keeps failing, opencode session capture is failing (check opencode `run` output for a ses_ id)")

    status = "READY" if not issues else "NOT_READY"
    payload = {
        "status": status,
        "checked_at": now_iso(),
        "project_root": str(project_root),
        "issues": issues,
        "recommended_fixes": recommended_fixes,
        "checks": checks,
    }
    atomic_write_json(paths["doctor_report"], payload)
    return {
        "ok": status == "READY",
        "content": f"{status}: {len(issues)} issue(s), {len(recommended_fixes)} recommended fix(es)",
        "meta": {
            "status": status,
            "issues": issues,
            "recommended_fixes": recommended_fixes,
            "doctor_report": str(paths["doctor_report"]),
            "project_root": str(project_root),
        },
    }


def run_sweep(project_root: Path) -> dict:
    paths = workflow_paths(project_root)
    changed_files: list[str] = []
    diff_summary = ""
    try:
        names = subprocess.run(
            ["git", "diff", "--name-only"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
        )
        changed_files = [line.strip() for line in names.stdout.splitlines() if line.strip()]
        summary = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=project_root,
            capture_output=True,
            text=True,
            check=False,
        )
        diff_summary = summary.stdout.strip()
    except OSError as exc:
        return {"ok": False, "content": str(exc), "meta": {"error_type": type(exc).__name__}}

    loaded = load_workspace_state(project_root)
    scope = loaded["scope"]
    impact_radius = scope.get("impact_radius") or []
    risk_hits = []
    for file_path in changed_files:
        lower = file_path.lower()
        if any(token in lower for token in ("config", "auth", "payment", "schema", "migration")):
            risk_hits.append(file_path)
        if impact_radius and any(target and target in file_path for target in impact_radius):
            risk_hits.append(file_path)

    if not changed_files:
        verdict = "skipped"
        reason = "no file changes detected"
    elif risk_hits:
        verdict = "repair_required"
        reason = f"risk indicators found in {len(set(risk_hits))} changed file(s)"
    else:
        verdict = "pass"
        reason = "no obvious impact issues detected"

    lines = [
        f"# Sweep Report",
        "",
        f"- verdict: {verdict}",
        f"- reason: {reason}",
        f"- checked_at: {now_iso()}",
        "",
        "## Changed Files",
    ]
    if changed_files:
        lines.extend(f"- {item}" for item in changed_files)
    else:
        lines.append("- none")
    lines.extend(["", "## Diff Summary", diff_summary or "(empty)"])
    if impact_radius:
        lines.extend(["", "## Scope Impact Radius", *[f"- {item}" for item in impact_radius]])
    if risk_hits:
        lines.extend(["", "## Risk Signals", *[f"- {item}" for item in sorted(set(risk_hits))]])
    report = "\n".join(lines).strip() + "\n"
    atomic_write_text(paths["sweep_report"], report)
    update_command_cache(
        project_root,
        "last_sweep_result",
        {"verdict": verdict, "reason": reason, "changed_files": changed_files, "diff_summary": diff_summary},
        (loaded["state"].get("session") or {}).get("id"),
    )
    return {
        "ok": True,
        "content": f"sweep {verdict}: {reason}",
        "meta": {
            "verdict": verdict,
            "reason": reason,
            "changed_files": changed_files,
            "report": str(paths["sweep_report"]),
            "project_root": str(project_root),
        },
    }
