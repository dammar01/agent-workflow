"""Install the agent-workflow config onto this machine.

Consumes `dist/` (produced by tools/extract_config.py) and applies it to the local
agent directories. Replaces the old prompt-driven setup: no pasting a 45KB prompt into
an agent and hoping it writes the right files.

    python install.py              # DRY RUN — show every change, write nothing
    python install.py --apply      # actually write
    python install.py --apply --init-project .   # ...and scaffold/upgrade .workflow/ here

Upgrading is the same command: point --init-project at a directory that already has a
.workflow/ and it is refreshed in place (scripts regenerated, new config keys backfilled,
sessions/ untouched) instead of being left on the build that first created it.

Dry run is the default on purpose. This writes into the user's global agent config,
which every project on the machine reads; a mistake here is not contained to one repo.

Safety:
- everything it would overwrite is backed up first, under a timestamped folder
- managed blocks are replaced BETWEEN markers, so hand-written config around them survives
- settings.json only gains keys it is missing; existing values are reported, never
  silently replaced
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DIST = REPO_ROOT / "dist"
DIST_CONFIG = DIST / "config"
MANIFEST = DIST / "manifest.json"
HOME = Path.home()

MARKERS = {
    "claude/CLAUDE.md": ("WORKFLOW-MAIN-AGENT:START", "WORKFLOW-MAIN-AGENT:END"),
    "opencode/AGENTS.md": ("WORKFLOW-SECOND-AGENT:START", "WORKFLOW-SECOND-AGENT:END"),
}

# settings.json keys the workflow actually needs to function. Everything else in the
# template is the maintainer's taste and is only offered when the user has no value.
SETTINGS_REQUIRED = ("hooks",)


class Plan:
    """Collected actions, so a dry run can print exactly what --apply would do."""

    def __init__(self) -> None:
        self.actions: list[tuple[str, str, str]] = []
        self.warnings: list[str] = []

    def add(self, verb: str, target: Path | str, detail: str = "") -> None:
        self.actions.append((verb, str(target), detail))

    def warn(self, message: str) -> None:
        self.warnings.append(message)


def _resolve_placeholders(text: str, project_root: Path | None) -> str:
    text = text.replace("{{HOME}}", str(HOME))
    if project_root:
        text = text.replace("{{PROJECT_ROOT}}", str(project_root))
    return text


def _resolve_in_json(value, project_root: Path | None):
    """Substitute placeholders in already-parsed JSON values.

    Doing it on the raw text instead corrupts the file on Windows: `{{HOME}}` expands to
    `C:\\Users\\name`, whose single backslashes are illegal JSON escapes. Parse first,
    substitute inside the string values, then re-serialize.
    """
    if isinstance(value, str):
        return _resolve_placeholders(value, project_root)
    if isinstance(value, dict):
        return {k: _resolve_in_json(v, project_root) for k, v in value.items()}
    if isinstance(value, list):
        return [_resolve_in_json(v, project_root) for v in value]
    return value


def _managed_block(text: str, start: str, end: str) -> str | None:
    """The marker-delimited region, markers included. None when absent."""
    match = re.search(
        rf"<!--\s*{re.escape(start)}.*?-->.*?<!--\s*{re.escape(end)}\s*-->",
        text,
        re.DOTALL,
    )
    return match.group(0) if match else None


def _merge_managed(
    existing: str, incoming: str, start: str, end: str
) -> tuple[str, str]:
    """Splice the incoming managed block into `existing`. Returns (result, how)."""
    block = _managed_block(incoming, start, end)
    if block is None:
        block = incoming.strip()

    current = _managed_block(existing, start, end)
    if current is not None:
        return existing.replace(current, block), "replaced managed block"
    separator = "" if existing.endswith("\n\n") or not existing else "\n\n"
    return existing + separator + block + "\n", "appended managed block"


def _backup(path: Path, backup_root: Path, plan: Plan, apply: bool) -> None:
    if not path.exists():
        return
    target = backup_root / path.name
    plan.add("backup", target, f"from {path}")
    if apply:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)


def _install_text(
    src: Path,
    dest: Path,
    key: str,
    plan: Plan,
    apply: bool,
    backup_root: Path,
    project_root: Path | None,
) -> None:
    incoming = _resolve_placeholders(src.read_text(encoding="utf-8"), project_root)

    if key in MARKERS and dest.exists():
        start, end = MARKERS[key]
        existing = dest.read_text(encoding="utf-8")
        merged, how = _merge_managed(existing, incoming, start, end)
        if merged == existing:
            plan.add("unchanged", dest)
            return
        _backup(dest, backup_root, plan, apply)
        plan.add("merge", dest, how)
        if apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(merged, encoding="utf-8")
        return

    if dest.exists() and dest.read_text(encoding="utf-8") == incoming:
        plan.add("unchanged", dest)
        return

    if dest.exists():
        _backup(dest, backup_root, plan, apply)
        plan.add("replace", dest)
    else:
        plan.add("create", dest)
    if apply:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(incoming, encoding="utf-8")


def _install_settings(
    src: Path, dest: Path, plan: Plan, apply: bool, backup_root: Path
) -> None:
    """Add missing keys only. An existing value is the user's decision, not a conflict
    for this script to resolve — except `hooks`, without which the workflow cannot bind
    a session at all, and which is therefore reported loudly when it differs."""
    template = _resolve_in_json(json.loads(src.read_text(encoding="utf-8")), None)
    if not dest.exists():
        plan.add("create", dest, f"{len(template)} key(s)")
        if apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
        return

    try:
        current = json.loads(dest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        plan.warn(f"{dest} is not valid JSON ({exc}); left untouched")
        return

    added = [k for k in template if k not in current]
    differing = [k for k in template if k in current and current[k] != template[k]]

    # `hooks` gets a nested additive merge instead of the whole-key treatment. A shipped
    # hook EVENT (Stop, PreToolUse, ...) must be able to land on a machine that already
    # has a hooks block for some OTHER event (SessionStart). Treating hooks as one opaque
    # value froze the entire block the moment it differed, so every new hook this tool
    # shipped was copied to disk as a file and then never registered — installed and inert.
    hook_events_added: list[str] = []
    if "hooks" in differing:
        differing.remove("hooks")
        tmpl_hooks = template.get("hooks") if isinstance(template.get("hooks"), dict) else {}
        cur_hooks = current.get("hooks") if isinstance(current.get("hooks"), dict) else {}
        for event, spec in (tmpl_hooks or {}).items():
            if event not in (cur_hooks or {}):
                hook_events_added.append(event)
            elif cur_hooks[event] != spec:
                # An event the user already customised is their decision, per the same
                # rule that governs the top-level keys — reported, never overwritten.
                plan.warn(
                    f"settings.json[hooks.{event}] differs from the shipped template — "
                    "kept yours (your hook wins)"
                )

    for key in differing:
        level = "REQUIRED" if key in SETTINGS_REQUIRED else "kept"
        plan.warn(
            f"settings.json[{key}] differs from the shipped template — {level} yours "
            f"({'the workflow may not bind sessions without the shipped hook' if key in SETTINGS_REQUIRED else 'your value wins'})"
        )

    if not added and not hook_events_added:
        plan.add("unchanged", dest, "no missing keys")
        return

    _backup(dest, backup_root, plan, apply)
    detail = ", ".join(
        [*added, *(f"hooks.{e}" for e in hook_events_added)]
    )
    plan.add("merge", dest, f"add {detail}")
    if apply:
        current.update({k: template[k] for k in added})
        if hook_events_added:
            merged_hooks = dict(current.get("hooks") or {})
            for event in hook_events_added:
                merged_hooks[event] = template["hooks"][event]
            current["hooks"] = merged_hooks
        dest.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")


def _enable_context_mode(plan: Plan, apply: bool, backup_root: Path) -> None:
    """Register the `context-mode` plugin in the opencode CLI's own config.

    Opt-in, never part of a plain install: this writes to the user's global opencode
    config, which every opencode session on the machine reads — including ones that
    have nothing to do with this workflow. Additive only; an existing plugin list keeps
    all of its entries.

    Note the file. `.workflow/opencode.json` is THIS tool's adapter config (command,
    timeouts, routes) and the plugin means nothing there — it belongs to the opencode
    CLI, whose config lives under ~/.config/opencode/.
    """
    dest = HOME / ".config" / "opencode" / "opencode.json"
    plugin = "context-mode"

    if not dest.exists():
        payload = {"$schema": "https://opencode.ai/config.json", "plugin": [plugin]}
        plan.add("create", dest, f"register {plugin} plugin")
        if apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        return

    try:
        current = json.loads(dest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        plan.warn(f"{dest} is not valid JSON ({exc}); context-mode not registered")
        return
    if not isinstance(current, dict):
        plan.warn(f"{dest} is not a JSON object; context-mode not registered")
        return

    plugins = current.get("plugin")
    if not isinstance(plugins, list):
        plugins = [] if plugins is None else [plugins]
    if plugin in plugins:
        plan.add("unchanged", dest, f"{plugin} already registered")
        return

    _backup(dest, backup_root, plan, apply)
    plan.add("merge", dest, f"add {plugin} to plugin[]")
    if apply:
        current["plugin"] = [*plugins, plugin]
        dest.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")


def _install_deps(plan: Plan, apply: bool) -> None:
    requirements = REPO_ROOT / "requirements.txt"
    if not requirements.exists():
        return
    body = [
        line.strip()
        for line in requirements.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    if not body:
        plan.add("skip", "pip install", "no runtime dependencies declared")
        return
    plan.add("run", f"pip install -r {requirements}", f"{len(body)} package(s)")
    if apply:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-r", str(requirements)],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            plan.warn(f"pip install failed: {result.stderr.strip()[-300:]}")


def _targets() -> list[tuple[Path, Path, str]]:
    """(source, destination, manifest-key) for everything in dist/config."""
    mapping = [
        (
            DIST_CONFIG / "claude" / "CLAUDE.md",
            HOME / ".claude" / "CLAUDE.md",
            "claude/CLAUDE.md",
        ),
        (
            DIST_CONFIG / "opencode" / "AGENTS.md",
            HOME / ".config" / "opencode" / "AGENTS.md",
            "opencode/AGENTS.md",
        ),
    ]
    for sub, dest_dir in (
        ("skills", HOME / ".claude" / "skills"),
        ("hooks", HOME / ".claude" / "hooks"),
    ):
        source_dir = DIST_CONFIG / "claude" / sub
        if source_dir.is_dir():
            for path in sorted(source_dir.iterdir()):
                if path.is_file():
                    mapping.append(
                        (path, dest_dir / path.name, f"claude/{sub}/{path.name}")
                    )
    return [(s, d, k) for s, d, k in mapping if s.exists()]


def main() -> int:
    parser = argparse.ArgumentParser(description="Install agent-workflow config")
    parser.add_argument(
        "--apply", action="store_true", help="write changes (default: dry run)"
    )
    parser.add_argument(
        "--init-project",
        metavar="DIR",
        help="also scaffold .workflow/ in DIR (upgrades it in place when it already exists)",
    )
    parser.add_argument(
        "--enable-context-mode",
        action="store_true",
        help="register the context-mode plugin in the opencode CLI config (global; opt-in)",
    )
    args = parser.parse_args()
    apply = args.apply

    if not MANIFEST.exists():
        print(
            "[INSTALL] dist/manifest.json missing — run tools/extract_config.py first"
        )
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    project_root = Path(args.init_project).resolve() if args.init_project else None
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup_root = HOME / ".claude" / "backups" / f"install_{stamp}"

    plan = Plan()
    print(
        f"[INSTALL] agent-workflow v{manifest.get('version')} "
        f"({'APPLY' if apply else 'DRY RUN'})"
    )
    print(f"  home:   {HOME}")
    print(f"  backup: {backup_root}")
    print()

    _install_deps(plan, apply)

    for source, dest, key in _targets():
        _install_text(source, dest, key, plan, apply, backup_root, project_root)

    settings_src = DIST_CONFIG / "claude" / "settings.template.json"
    if settings_src.exists():
        _install_settings(
            settings_src, HOME / ".claude" / "settings.json", plan, apply, backup_root
        )
    if args.enable_context_mode:
        _enable_context_mode(plan, apply, backup_root)

    agent_path = REPO_ROOT / "main.py"
    if os.environ.get("AGENT_PATH") != str(agent_path):
        plan.add(
            "env", "AGENT_PATH", f"set to {agent_path} (shell command printed below)"
        )

    if project_root:
        # An existing .workflow/ is upgraded, not re-scaffolded. ensure_workflow_workspace
        # only writes files that are MISSING, so on a workspace from an older build it
        # leaves stale run scripts and never backfills new opencode.json keys — the exact
        # drift that made every fix in this repo stop at the repo it was written in.
        existing = (project_root / ".workflow").exists()
        sys.path.insert(0, str(REPO_ROOT))
        from core.workflow_runtime import (
            ensure_workflow_workspace,
            upgrade_workflow_workspace,
            workspace_versions,
        )

        if existing:
            versions = workspace_versions(project_root)
            plan.add(
                "upgrade",
                project_root / ".workflow",
                f"tool {versions['installed_tool_version']} -> {versions['current_tool_version']}"
                " (regenerate scripts, backfill config keys, keep sessions/)",
            )
            if apply:
                try:
                    upgrade_workflow_workspace(project_root, str(agent_path))
                except ValueError as exc:
                    # Refuses while a delegated job is live. Reported, not raised: the
                    # global config install above already succeeded, and aborting here
                    # would leave the user unsure which half of the run took effect.
                    plan.warn(f"workspace not upgraded: {exc}")
        else:
            plan.add("init", project_root / ".workflow", "scaffold workspace")
            if apply:
                ensure_workflow_workspace(project_root, str(agent_path))

    counts: dict[str, int] = {}
    for verb, target, detail in plan.actions:
        counts[verb] = counts.get(verb, 0) + 1
        suffix = f"  — {detail}" if detail else ""
        print(f"  {verb:9} {target}{suffix}")

    print()
    print("[INSTALL REPORT]")
    print(
        "  " + " | ".join(f"{verb}: {n}" for verb, n in sorted(counts.items()))
        or "  nothing to do"
    )
    if plan.warnings:
        print("  warnings:")
        for warning in plan.warnings:
            print(f"    ! {warning}")
    else:
        print("  warnings: none")

    if os.environ.get("AGENT_PATH") != str(agent_path):
        print()
        print("  set AGENT_PATH so `init` can bootstrap new projects:")
        if os.name == "nt":
            print(
                f'    [Environment]::SetEnvironmentVariable("AGENT_PATH","{agent_path}","User")'
            )
        else:
            print(f'    export AGENT_PATH="{agent_path}"')

    if not apply:
        print()
        print("  DRY RUN — nothing was written. Re-run with --apply to install.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
