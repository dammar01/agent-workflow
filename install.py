"""Install the agent-workflow config onto this machine.

Consumes `dist/` (produced by tools/extract_config.py) and applies it to the local
agent directories.

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
import hashlib
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
# Install target root. AGENT_HOME lets a fresh machine reproduce the environment into a
# chosen location instead of the real profile; unset falls back to the actual home.
HOME = (
    Path(os.environ["AGENT_HOME"]).expanduser()
    if os.environ.get("AGENT_HOME")
    else Path.home()
)

MARKERS = {
    "claude/CLAUDE.md": ("WORKFLOW-MAIN-AGENT:START", "WORKFLOW-MAIN-AGENT:END"),
    "opencode/AGENTS.md": ("WORKFLOW-SECOND-AGENT:START", "WORKFLOW-SECOND-AGENT:END"),
}

# settings.json keys required by the workflow; other template values stay optional.
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


_ENV_CACHE: dict | None = None
_MISSING_ENV: set[str] = set()
_ENV_PLACEHOLDER = re.compile(r"\{\{ENV:([A-Za-z0-9_]+)\}\}")


def _load_env_file() -> dict:
    """KEY=VALUE pairs from the install .env (no python-dotenv dependency).

    Location precedence: $AGENT_ENV_FILE, else <repo>/.env, else <HOME>/.claude/.env.
    First existing file wins; a missing file is not an error (returns {}). Values may be
    wrapped in single or double quotes. This file holds the machine's secrets and must
    NEVER be committed — only .env.example ships in the repo.
    """
    candidates: list[Path] = []
    explicit = os.environ.get("AGENT_ENV_FILE")
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates.append(REPO_ROOT / ".env")
    candidates.append(HOME / ".claude" / ".env")
    for path in candidates:
        if not path.is_file():
            continue
        data: dict = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            key, _, value = stripped.partition("=")
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key:
                data.setdefault(key, value)
        return data
    return {}


def _env_values() -> dict:
    """Resolution table for {{ENV:NAME}}: the real environment wins over the .env file."""
    global _ENV_CACHE
    if _ENV_CACHE is None:
        _ENV_CACHE = {**_load_env_file(), **os.environ}
    return _ENV_CACHE


def _resolve_placeholders(text: str, project_root: Path | None) -> str:
    text = text.replace("{{HOME}}", str(HOME))
    if project_root:
        text = text.replace("{{PROJECT_ROOT}}", str(project_root))
    env = _env_values()

    def _sub(match: "re.Match") -> str:
        name = match.group(1)
        if name in env:
            return env[name]
        _MISSING_ENV.add(name)  # left intact; main() aborts before any write
        return match.group(0)

    return _ENV_PLACEHOLDER.sub(_sub, text)


def _scan_missing_env() -> set[str]:
    """{{ENV:NAME}} names referenced by shipped files that don't resolve.

    A preflight so a missing secret aborts the WHOLE install before the first write —
    a half-applied second_agent config (some files env-substituted, some not) is worse
    than none. Every source that _targets/settings install would write is scanned.
    """
    env = _env_values()
    missing: set[str] = set()
    sources = [src for src, _dest, _key in _targets()]
    settings_src = DIST_CONFIG / "claude" / "settings.template.json"
    if settings_src.exists():
        sources.append(settings_src)
    for source in sources:
        try:
            text = source.read_text(encoding="utf-8")
        except OSError:
            continue
        for match in _ENV_PLACEHOLDER.finditer(text):
            if match.group(1) not in env:
                missing.add(match.group(1))
    return missing


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


def _hook_script_ids(entry: dict) -> set[str]:
    """Filenames of the hook scripts an entry invokes (e.g. intent-gate-check.ps1).

    These identify an entry as OURS: we ship these scripts, so an entry that runs one is a
    shipped hook we may refresh — not a user's foreign hook to preserve.
    """
    ids: set[str] = set()
    hooks = entry.get("hooks", []) if isinstance(entry, dict) else []
    for h in hooks if isinstance(hooks, list) else []:
        cmd = h.get("command", "") if isinstance(h, dict) else ""
        ids.update(m.lower() for m in re.findall(r"[\w.-]+\.(?:ps1|sh)", cmd))
    return ids


def _merge_hook_entries(cur_entries: list, tmpl_entries: list) -> tuple[list, int]:
    """Refresh OUR shipped hook entries (identified by the script they call), append any
    shipped entry we don't yet have, and leave every foreign entry untouched.

    This is what lets a shipped matcher change (e.g. adding Bash to the Pre-flight gate)
    reach an existing install: the old policy kept the whole event whenever it differed,
    which froze our own hook at its previous matcher. Ownership is by script filename, so a
    hook the user added that runs none of our scripts is never modified.
    """
    result = list(cur_entries)
    updated = 0
    for tmpl_entry in tmpl_entries:
        tids = _hook_script_ids(tmpl_entry)
        if not tids:
            if tmpl_entry not in result:
                result.append(tmpl_entry)
                updated += 1
            continue
        idx = next(
            (i for i, e in enumerate(result) if _hook_script_ids(e) & tids), None
        )
        if idx is None:
            result.append(tmpl_entry)
            updated += 1
        elif result[idx] != tmpl_entry:
            result[idx] = tmpl_entry
            updated += 1
    return result, updated


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

    # Merge hooks per event AND per entry: unrelated user events/entries stay untouched, but
    # OUR OWN shipped hook entries (identified by the script they call) are refreshed so a
    # shipped matcher change actually reaches an existing install.
    hook_changes: list[str] = []
    merged_hooks: dict | None = None
    if "hooks" in differing:
        differing.remove("hooks")
        tmpl_hooks = template.get("hooks") if isinstance(template.get("hooks"), dict) else {}
        cur_hooks = current.get("hooks") if isinstance(current.get("hooks"), dict) else {}
        merged_hooks = dict(cur_hooks or {})
        for event, tmpl_entries in (tmpl_hooks or {}).items():
            if event not in (cur_hooks or {}):
                merged_hooks[event] = tmpl_entries
                hook_changes.append(f"hooks.{event} (added)")
                continue
            if not isinstance(tmpl_entries, list) or not isinstance(cur_hooks[event], list):
                # Non-list shape we do not understand: keep the user's, report it.
                if cur_hooks[event] != tmpl_entries:
                    plan.warn(
                        f"settings.json[hooks.{event}] differs from the shipped template — "
                        "kept yours (your hook wins)"
                    )
                continue
            new_entries, updated = _merge_hook_entries(cur_hooks[event], tmpl_entries)
            if updated:
                merged_hooks[event] = new_entries
                hook_changes.append(
                    f"hooks.{event} (refreshed {updated} shipped entr"
                    f"{'y' if updated == 1 else 'ies'})"
                )
        if merged_hooks == (cur_hooks or {}):
            merged_hooks = None

    for key in differing:
        level = "REQUIRED" if key in SETTINGS_REQUIRED else "kept"
        plan.warn(
            f"settings.json[{key}] differs from the shipped template — {level} yours "
            f"({'the workflow may not bind sessions without the shipped hook' if key in SETTINGS_REQUIRED else 'your value wins'})"
        )

    if not added and not hook_changes:
        plan.add("unchanged", dest, "no missing keys")
        return

    _backup(dest, backup_root, plan, apply)
    detail = ", ".join([*added, *hook_changes])
    plan.add("merge", dest, f"add {detail}")
    if apply:
        current.update({k: template[k] for k in added})
        if merged_hooks is not None:
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


def _strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments from JSONC, string-aware so a `//` inside a URL
    (e.g. "https://...") or a `/*` inside a value is preserved. Trailing commas dropped."""
    out: list[str] = []
    i, n = 0, len(text)
    in_str = esc = False
    while i < n:
        char = text[i]
        if in_str:
            out.append(char)
            if esc:
                esc = False
            elif char == "\\":
                esc = True
            elif char == '"':
                in_str = False
            i += 1
            continue
        if char == '"':
            in_str = True
            out.append(char)
            i += 1
            continue
        if char == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                i += 1
            continue
        if char == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(char)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


def _load_json_or_jsonc(path: Path) -> dict:
    """Parse a config that may carry comments (opencode.jsonc) or be plain JSON."""
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return json.loads(_strip_jsonc(text))


def _opencode_config_path() -> Path:
    """The native opencode config file to merge into. Prefer an existing .jsonc (the
    common opencode default), else .json. opencode reads either from this directory."""
    directory = HOME / ".config" / "opencode"
    jsonc = directory / "opencode.jsonc"
    if jsonc.exists():
        return jsonc
    return directory / "opencode.json"


def _deep_merge_additive(
    base: dict, incoming: dict, path: str, plan: Plan
) -> tuple[dict, int]:
    """Recursively add keys from `incoming` absent in `base`; report scalar conflicts.

    Same posture as _install_settings: a value the user already set is their decision,
    never silently overwritten — reported instead. Nested dicts recurse so unrelated
    sibling keys (the user's MCP servers, providers) are preserved untouched.
    """
    added = 0
    for key, value in incoming.items():
        sub_path = f"{path}.{key}"
        if key not in base:
            base[key] = value
            added += 1
        elif isinstance(base[key], dict) and isinstance(value, dict):
            base[key], sub = _deep_merge_additive(base[key], value, sub_path, plan)
            added += sub
        elif base[key] != value:
            plan.warn(
                f"opencode.json[{sub_path}] differs from the workflow default — kept "
                f"yours ({base[key]!r}); workflow wants {value!r} (read-only enforcement)"
            )
    return base, added


def _install_opencode(
    src: Path,
    dest: Path,
    plan: Plan,
    apply: bool,
    backup_root: Path,
    project_root: Path | None,
) -> None:
    """Merge the workflow's opencode.json fragment (read-only second_agent permission)
    into the user's native config additively — MCP servers, providers, and other agents
    are preserved. Env placeholders are resolved (preflight guarantees they exist)."""
    incoming = json.loads(_resolve_placeholders(src.read_text(encoding="utf-8"), project_root))
    current: dict = {}
    if dest.exists():
        try:
            current = _load_json_or_jsonc(dest)
        except json.JSONDecodeError:
            plan.warn(f"{dest} is not valid JSON/JSONC — skipped (fix or remove it, then rerun)")
            return
    merged = json.loads(json.dumps(current))  # deep copy
    merged, added = _deep_merge_additive(merged, incoming, "opencode", plan)
    if merged == current:
        plan.add("unchanged", dest)
        return
    if dest.exists():
        _backup(dest, backup_root, plan, apply)
        plan.add("merge", dest, f"add {added} workflow key(s)")
    else:
        plan.add("create", dest)
    if apply:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")


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
        ("commands", HOME / ".claude" / "commands"),
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


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run_check(manifest: dict) -> int:
    """Report drift without writing anything.

    Two independent questions: (1) does dist/ still match its manifest — catches a dist
    edit that skipped `python tools/gen_manifest.py`; (2) does the installed ~/.claude match
    dist/ — catches a stale or hand-edited install. Full-overwrite targets (skills, hooks)
    compare whole-file; managed targets (CLAUDE.md, AGENTS.md) compare only the marker block,
    since the rest of those files is legitimately the user's own content.
    """
    by_path = {f["path"]: f for f in manifest.get("files", [])}
    bundle_stale: list[str] = []
    installed_drift: list[str] = []
    installed_missing: list[str] = []

    checks = list(_targets())
    settings_src = DIST_CONFIG / "claude" / "settings.template.json"
    if settings_src.exists():
        # settings.json is a key-wise JSON merge, not a copy — bundle-check only.
        checks.append((settings_src, None, "claude/settings.template.json"))
    opencode_src = DIST_CONFIG / "opencode" / "opencode.template.json"
    if opencode_src.exists():
        # opencode.json is a deep additive JSON merge, not a copy — bundle-check only.
        checks.append((opencode_src, None, "opencode/opencode.template.json"))

    for source, dest, key in checks:
        dist_text = source.read_text(encoding="utf-8")
        entry = by_path.get(key)
        if entry and _hash(dist_text) != entry.get("sha256"):
            bundle_stale.append(key)
        if dest is None:
            continue
        resolved = _resolve_placeholders(dist_text, None)
        if not dest.exists():
            installed_missing.append(key)
            continue
        installed = dest.read_text(encoding="utf-8")
        if key in MARKERS:
            start, end = MARKERS[key]
            want = _managed_block(resolved, start, end)
            have = _managed_block(installed, start, end)
            if want is None or have is None or want != have:
                installed_drift.append(key)
        elif installed != resolved:
            installed_drift.append(key)

    print("[INSTALL CHECK]")
    print(
        f"  bundle (dist vs manifest): {'OK' if not bundle_stale else f'STALE ({len(bundle_stale)})'}"
    )
    for key in bundle_stale:
        print(f"    - {key}")
    installed_issues = len(installed_drift) + len(installed_missing)
    if installed_issues == 0:
        print("  installed (~/.claude vs dist): READY")
    else:
        print(
            f"  installed (~/.claude vs dist): DRIFTED "
            f"(drift {len(installed_drift)}, missing {len(installed_missing)})"
        )
        for key in installed_drift:
            print(f"    - DRIFTED {key}")
        for key in installed_missing:
            print(f"    - MISSING {key}")

    # Per-component rollup (P0.7): which component drifted, not just how many files.
    versions = manifest.get("versions") or {}
    if versions:
        changed_keys = set(bundle_stale) | set(installed_drift) | set(installed_missing)
        print("  components:")
        for comp, ver in versions.items():
            comp_keys = [k for k, e in by_path.items() if e.get("component") == comp]
            drifted = [k for k in comp_keys if k in changed_keys]
            state = "OK" if not drifted else f"CHANGED ({len(drifted)})"
            print(f"    {comp} v{ver}: {state}")

    if bundle_stale:
        status = "STALE"
    elif installed_issues:
        status = "DRIFTED"
    else:
        status = "READY"
    print(f"  status: {status}")
    if status != "READY":
        print("  fix: python tools/gen_manifest.py (bundle) | python install.py --apply (installed)")
    return 0 if status == "READY" else 1


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
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift (installed ~/.claude vs bundle, dist vs manifest); no writes",
    )
    args = parser.parse_args()
    apply = args.apply

    if not MANIFEST.exists():
        print(
            "[INSTALL] dist/manifest.json missing — run tools/gen_manifest.py first"
        )
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    if args.check:
        return _run_check(manifest)

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

    # Env-secret preflight: every {{ENV:NAME}} a shipped file references must resolve
    # before we touch anything. Missing on --apply = abort whole run (no partial config).
    missing_env = _scan_missing_env()
    if missing_env:
        if apply:
            print("[INSTALL] ABORTED — required environment values are missing:")
            for name in sorted(missing_env):
                print(f"  !! {name}")
            print(
                "Set them in .env (see dist/.env.example) or the environment, then rerun."
                " Nothing was written."
            )
            return 5
        for name in sorted(missing_env):
            plan.warn(f"env value not set (dry run, would block --apply): {name}")

    _install_deps(plan, apply)

    for source, dest, key in _targets():
        _install_text(source, dest, key, plan, apply, backup_root, project_root)

    settings_src = DIST_CONFIG / "claude" / "settings.template.json"
    if settings_src.exists():
        _install_settings(
            settings_src, HOME / ".claude" / "settings.json", plan, apply, backup_root
        )
    opencode_src = DIST_CONFIG / "opencode" / "opencode.template.json"
    if opencode_src.exists():
        _install_opencode(
            opencode_src,
            _opencode_config_path(),
            plan,
            apply,
            backup_root,
            project_root,
        )
    if args.enable_context_mode:
        _enable_context_mode(plan, apply, backup_root)

    agent_path = REPO_ROOT / "main.py"
    if os.environ.get("AGENT_PATH") != str(agent_path):
        plan.add(
            "env", "AGENT_PATH", f"set to {agent_path} (shell command printed below)"
        )

    if project_root:
        # Upgrade an existing workspace; scaffold only when .workflow/ is absent.
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
