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
- settings.json gains missing keys and refreshes only workflow-owned hook entries;
  unrelated user hooks and values are preserved
- rollback verifies destination and backup hashes before restoring or deleting anything
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


# What --apply actually did, written next to the backups so --rollback does not have to
# guess. A backup directory alone cannot be undone safely: it holds the files that existed
# BEFORE, with no record of which files the install created, and those must be deleted
# rather than restored.
_RECEIPT_SCHEMA_VERSION = 2
_RECEIPT: list[dict] = []


def _file_sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _record(
    action: str,
    dest: Path,
    key: str,
    backup: Path | None,
    pre_sha256: str | None,
) -> None:
    post_sha256 = _file_sha256(dest)
    if post_sha256 is None:
        raise OSError(f"installed destination is not a file: {dest}")
    _RECEIPT.append(
        {
            "action": action,
            "key": key,
            "dest": str(dest),
            "backup": str(backup) if backup else None,
            "pre_sha256": pre_sha256,
            "post_sha256": post_sha256,
        }
    )


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


_SECTION_RE = "<!--\\s*{name}:START\\s*-->.*?<!--\\s*{name}:END\\s*-->\\s*"


def _strip_section(text: str, name: str) -> str:
    return re.sub(_SECTION_RE.format(name=re.escape(name)), "", text, flags=re.DOTALL)


def _apply_intent_mode(text: str, only_command: bool) -> str:
    """Keep exactly one of the two mutually exclusive intent stanzas.

    Both live in the same shipped file rather than in two variants of it: two files drift,
    and the drift is invisible until an install ships a stanza nobody reviewed.
    """
    text = (
        _strip_section(text, "AUTO-INTENT")
        if only_command
        else _strip_section(text, "COMMAND-ONLY")
    )
    # The surviving stanza's own markers are scaffolding for this switch, not content —
    # leaving them in ships selector plumbing into the agent's prompt.
    return re.sub(r"[ \t]*<!--\s*(?:AUTO-INTENT|COMMAND-ONLY):(?:START|END)\s*-->\n?", "", text)


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


def _backup(
    path: Path, backup_root: Path, plan: Plan, apply: bool, key: str | None = None
) -> Path | None:
    """Copy `path` under `backup_root`, mirroring its manifest key as the sub-path.

    Keyed rather than flat: `claude/skills/plan.md` and `claude/commands/plan.md` share a
    filename, and a flat backup dir silently kept only one of them.
    """
    if not path.exists():
        return None
    target = backup_root / (key or path.name)
    plan.add("backup", target, f"from {path}")
    if apply:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
    return target


def _read_text_lenient(path: Path) -> str:
    """Read a possibly non-UTF-8 dest file without crashing.

    An older install — or a PowerShell `Set-Content` in the machine's ANSI codepage — could
    have written our em-dash-heavy docs as Windows-1252, which strict UTF-8 refuses (byte
    0x97 is the cp1252 em dash, invalid as a UTF-8 start byte). Try the encodings we actually
    emit, then fall back to latin-1, which maps every byte and never raises.
    """
    data = path.read_bytes()
    text: str | None = None
    for enc in ("utf-8-sig", "utf-8", "cp1252"):
        try:
            text = data.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = data.decode("latin-1")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _install_text(
    src: Path,
    dest: Path,
    key: str,
    plan: Plan,
    apply: bool,
    backup_root: Path,
    project_root: Path | None,
    only_command: bool = False,
) -> None:
    incoming = _resolve_placeholders(src.read_text(encoding="utf-8"), project_root)
    if key == "claude/CLAUDE.md":
        incoming = _apply_intent_mode(incoming, only_command)

    if key in MARKERS and dest.exists():
        start, end = MARKERS[key]
        existing = _read_text_lenient(dest)
        merged, how = _merge_managed(existing, incoming, start, end)
        if merged == existing:
            plan.add("unchanged", dest)
            return
        pre_sha256 = _file_sha256(dest)
        saved = _backup(dest, backup_root, plan, apply, key)
        plan.add("merge", dest, how)
        if apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(merged, encoding="utf-8")
            _record("merge", dest, key, saved, pre_sha256)
        return

    if dest.exists() and _read_text_lenient(dest) == incoming:
        plan.add("unchanged", dest)
        return

    pre_sha256 = _file_sha256(dest)
    if dest.exists():
        saved = _backup(dest, backup_root, plan, apply, key)
        plan.add("replace", dest)
    else:
        saved = None
        plan.add("create", dest)
    if apply:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(incoming, encoding="utf-8")
        _record("replace" if saved else "create", dest, key, saved, pre_sha256)


def _hook_script_ids(entry: dict) -> set[str]:
    """Stems of the hook scripts an entry invokes (e.g. `intent-gate-check`).

    Script stems define workflow ownership. Extensions are ignored so `.ps1` and `.sh`
    variants collapse to one logical hook during a cross-platform install.
    """
    ids: set[str] = set()
    hooks = entry.get("hooks", []) if isinstance(entry, dict) else []
    for h in hooks if isinstance(hooks, list) else []:
        cmd = h.get("command", "") if isinstance(h, dict) else ""
        ids.update(m.lower() for m in re.findall(r"([\w.-]+)\.(?:ps1|sh)", cmd))
    return ids


def _merge_hook_entries(cur_entries: list, tmpl_entries: list) -> tuple[list, int]:
    """Refresh OUR shipped hook entries (identified by the script they call), append any
    shipped entry we don't yet have, and leave every foreign entry untouched.

    Every entry sharing a shipped script stem collapses into one current entry. A user hook
    that runs none of those scripts is never modified.
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
        matches = [i for i, e in enumerate(result) if _hook_script_ids(e) & tids]
        if not matches:
            result.append(tmpl_entry)
            updated += 1
            continue
        first = matches[0]
        already_current = len(matches) == 1 and result[first] == tmpl_entry
        preserved: list[dict] = []
        for i in matches:
            entry = result[i]
            if not isinstance(entry, dict):
                continue
            kept_hooks = [
                hook
                for hook in entry.get("hooks", [])
                if not (_hook_script_ids({"hooks": [hook]}) & tids)
            ]
            if kept_hooks:
                kept_entry = json.loads(json.dumps(entry))
                kept_entry["hooks"] = kept_hooks
                preserved.append(kept_entry)
        for i in reversed(matches):
            del result[i]
        result[first:first] = [tmpl_entry, *preserved]
        if not already_current:
            updated += 1
    return result, updated


def _rewrite_hooks_for_posix(template: dict) -> dict:
    """On POSIX, point our shipped hook commands at their .sh siblings via bash.

    The template ships Windows-native commands (`powershell ... -File "...ps1"`) — those
    cannot run on mac/linux. For each entry that calls one of OUR scripts, swap the
    interpreter to `bash`, the extension to `.sh`, and normalise the backslash path the
    Windows template embedded. A user's foreign hook (no shipped script) is left untouched.
    Windows (`os.name == "nt"`) is returned unchanged.
    """
    if os.name == "nt":
        return template
    hooks = template.get("hooks")
    if not isinstance(hooks, dict):
        return template
    for entries in hooks.values():
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict) or not _hook_script_ids(entry):
                continue
            for hook in entry.get("hooks", []):
                if not isinstance(hook, dict):
                    continue
                cmd = hook.get("command", "")
                cmd = cmd.replace(
                    'powershell -NoProfile -ExecutionPolicy Bypass -File "', 'bash "'
                )
                cmd = cmd.replace('.ps1"', '.sh"')
                cmd = cmd.replace("\\", "/")
                hook["command"] = cmd
    return template


def _remove_intent_hook_entries(settings: dict) -> tuple[dict, int]:
    out = json.loads(json.dumps(settings))
    hooks = out.get("hooks")
    if not isinstance(hooks, dict):
        return out, 0
    entries = hooks.get("UserPromptSubmit")
    if not isinstance(entries, list):
        return out, 0
    kept: list = []
    removed = 0
    for entry in entries:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        hooks_in = entry.get("hooks")
        if not isinstance(hooks_in, list):
            kept.append(entry)
            continue
        hooks_out = []
        for hook in hooks_in:
            if "intent-gate-set" in _hook_script_ids({"hooks": [hook]}):
                removed += 1
            else:
                hooks_out.append(hook)
        if hooks_out:
            retained = json.loads(json.dumps(entry))
            retained["hooks"] = hooks_out
            kept.append(retained)
    if kept:
        hooks["UserPromptSubmit"] = kept
    else:
        hooks.pop("UserPromptSubmit", None)
    return out, removed


def _drop_intent_hook(template: dict, plan: Plan) -> dict:
    """Remove the UserPromptSubmit entries that run intent-gate-set from the template.

    Only entries that invoke OUR script go: a user's own UserPromptSubmit hook on the same
    event is theirs, and an installer that removed it would be doing exactly what this
    flag exists to prevent.
    """
    out, removed = _remove_intent_hook_entries(template)
    if not removed:
        return template
    plan.warn(
        "only-command: UserPromptSubmit intent-gate-set hook not registered "
        "(auto-intent runtime gate stays off)"
    )
    return out


def _install_settings(
    src: Path,
    dest: Path,
    plan: Plan,
    apply: bool,
    backup_root: Path,
    only_command: bool = False,
) -> None:
    """Add missing keys only. An existing value is the user's decision, not a conflict
    for this script to resolve — except `hooks`, without which the workflow cannot bind
    a session at all, and which is therefore reported loudly when it differs."""
    template = _resolve_in_json(json.loads(src.read_text(encoding="utf-8")), None)
    template = _rewrite_hooks_for_posix(template)
    if only_command:
        # The runtime half of auto-intent. Dropping the prompt stanza while leaving this
        # hook registered would keep the gate blocking gather tools on a classification
        # the prompt no longer performs.
        template = _drop_intent_hook(template, plan)
    if not dest.exists():
        plan.add("create", dest, f"{len(template)} key(s)")
        if apply:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(json.dumps(template, indent=2) + "\n", encoding="utf-8")
            _record("create", dest, "claude/settings.json", None, None)
        return

    try:
        current = json.loads(_read_text_lenient(dest))
    except json.JSONDecodeError as exc:
        plan.warn(f"{dest} is not valid JSON ({exc}); left untouched")
        return
    if not isinstance(current, dict):
        plan.warn(f"{dest} root is not a JSON object; left untouched")
        return

    added = [k for k in template if k not in current]
    differing = [k for k in template if k in current and current[k] != template[k]]

    # Refresh shipped hook entries while preserving hooks owned by the user.
    hook_changes: list[str] = []
    merged_hooks: dict | None = None
    if "hooks" in differing:
        differing.remove("hooks")
        tmpl_hooks = (
            template.get("hooks") if isinstance(template.get("hooks"), dict) else {}
        )
        cur_hooks = (
            current.get("hooks") if isinstance(current.get("hooks"), dict) else {}
        )
        merged_hooks = dict(cur_hooks or {})
        if only_command:
            cleaned, removed = _remove_intent_hook_entries({"hooks": merged_hooks})
            merged_hooks = cleaned.get("hooks", {})
            if removed:
                hook_changes.append(
                    f"hooks.UserPromptSubmit (removed {removed} shipped intent entr"
                    f"{'y' if removed == 1 else 'ies'})"
                )
        for event, tmpl_entries in (tmpl_hooks or {}).items():
            if event not in merged_hooks:
                merged_hooks[event] = tmpl_entries
                hook_changes.append(f"hooks.{event} (added)")
                continue
            current_entries = merged_hooks[event]
            if not isinstance(tmpl_entries, list) or not isinstance(
                current_entries, list
            ):
                # Non-list shape we do not understand: keep the user's, report it.
                if current_entries != tmpl_entries:
                    plan.warn(
                        f"settings.json[hooks.{event}] differs from the shipped template — "
                        "kept yours (your hook wins)"
                    )
                continue
            new_entries, updated = _merge_hook_entries(current_entries, tmpl_entries)
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

    pre_sha256 = _file_sha256(dest)
    saved = _backup(dest, backup_root, plan, apply, "claude/settings.json")
    detail = ", ".join([*added, *hook_changes])
    plan.add("merge", dest, f"update {detail}")
    if apply:
        current.update({k: template[k] for k in added})
        if merged_hooks is not None:
            current["hooks"] = merged_hooks
        dest.write_text(json.dumps(current, indent=2) + "\n", encoding="utf-8")
        _record("merge", dest, "claude/settings.json", saved, pre_sha256)


def _strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments from JSONC, string-aware so a `//` inside a URL
    (e.g. "https://...") or a `/*` inside a value is preserved. Trailing commas dropped.
    """
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


def _merge_opencode_policy(
    current: dict, incoming: dict, plan: Plan
) -> tuple[dict, int, int]:
    """Merge general config additively, but enforce workflow-owned plan permissions."""
    incoming_copy = json.loads(json.dumps(incoming))
    incoming_plan = (incoming_copy.get("agent") or {}).get("plan")
    shipped_permissions = None
    if isinstance(incoming_plan, dict):
        shipped_permissions = incoming_plan.pop("permission", None)

    merged = json.loads(json.dumps(current))
    merged, added = _deep_merge_additive(merged, incoming_copy, "opencode", plan)
    enforced = 0
    if isinstance(shipped_permissions, dict):
        agent = merged.get("agent")
        if not isinstance(agent, dict):
            plan.warn("opencode agent config was not an object; replaced for workflow policy")
            agent = {}
            merged["agent"] = agent
        plan_agent = agent.get("plan")
        if not isinstance(plan_agent, dict):
            plan.warn("opencode plan agent config was not an object; replaced")
            plan_agent = {}
            agent["plan"] = plan_agent
        permissions = plan_agent.get("permission")
        if not isinstance(permissions, dict):
            permissions = {}
            plan_agent["permission"] = permissions
        for key, value in shipped_permissions.items():
            current_value = permissions.get(key)
            same_value = current_value == value
            if key == "bash" and isinstance(current_value, dict) and isinstance(value, dict):
                same_value = list(current_value.items()) == list(value.items())
            if same_value:
                continue
            if key in permissions:
                plan.warn(
                    f"opencode plan permission {key!r} replaced by the workflow's "
                    "read-only policy"
                )
            permissions[key] = json.loads(json.dumps(value))
            enforced += 1
    return merged, added, enforced


def _install_opencode(
    src: Path,
    dest: Path,
    plan: Plan,
    apply: bool,
    backup_root: Path,
    project_root: Path | None,
) -> None:
    """Preserve unrelated OpenCode config while enforcing plan-agent permissions.

    MCP servers, providers, and other agents remain additive. Environment placeholders
    are resolved after preflight proves the required values exist.
    """
    incoming = json.loads(
        _resolve_placeholders(src.read_text(encoding="utf-8"), project_root)
    )
    current: dict = {}
    if dest.exists():
        try:
            current = _load_json_or_jsonc(dest)
        except json.JSONDecodeError:
            plan.warn(
                f"{dest} is not valid JSON/JSONC — skipped (fix or remove it, then rerun)"
            )
            return
        if not isinstance(current, dict):
            plan.warn(
                f"{dest} root is not a JSON object; skipped (replace it with an object, then rerun)"
            )
            return
    merged, added, enforced = _merge_opencode_policy(current, incoming, plan)
    if merged == current and enforced == 0:
        plan.add("unchanged", dest)
        return
    if dest.exists():
        pre_sha256 = _file_sha256(dest)
        saved = _backup(dest, backup_root, plan, apply, "opencode/opencode.json")
        plan.add(
            "merge",
            dest,
            f"add {added} workflow key(s), enforce {enforced} permission key(s)",
        )
    else:
        pre_sha256 = None
        saved = None
        plan.add("create", dest)
    if apply:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
        _record(
            "merge" if saved else "create",
            dest,
            "opencode/opencode.json",
            saved,
            pre_sha256,
        )


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


_MODE_FILE = HOME / ".claude" / ".workflow-install-mode.json"


def _stored_only_command() -> bool:
    """The persisted intent mode used when an upgrade supplies no mode flag."""
    try:
        return bool(
            json.loads(_MODE_FILE.read_text(encoding="utf-8")).get("only_command")
        )
    except (OSError, ValueError):
        return False


def _store_only_command(
    value: bool, plan: Plan, apply: bool, backup_root: Path
) -> None:
    content = json.dumps({"only_command": bool(value)}, indent=2) + "\n"
    detail = "command-only (prefix /. required)" if value else "auto-intent"
    if _MODE_FILE.is_file() and _MODE_FILE.read_bytes() == content.encode("utf-8"):
        plan.add("unchanged", _MODE_FILE, detail)
        return

    pre_sha256 = _file_sha256(_MODE_FILE)
    saved = (
        _backup(
            _MODE_FILE,
            backup_root,
            plan,
            apply,
            "claude/workflow-install-mode.json",
        )
        if _MODE_FILE.exists()
        else None
    )
    plan.add("mode", _MODE_FILE, detail)
    if apply:
        _MODE_FILE.parent.mkdir(parents=True, exist_ok=True)
        _MODE_FILE.write_text(content, encoding="utf-8")
        _record("mode", _MODE_FILE, "claude/workflow-install-mode.json", saved, pre_sha256)


def _backup_dirs() -> list[Path]:
    """Timestamped install backup dirs, newest first."""
    root = HOME / ".claude" / "backups"
    if not root.is_dir():
        return []
    return sorted(
        (p for p in root.iterdir() if p.is_dir() and p.name.startswith("install_")),
        key=lambda p: p.name,
        reverse=True,
    )


def _run_rollback(which: str | None, apply: bool) -> int:
    """Undo one install from its receipt. Dry run by default, like install itself."""
    dirs = _backup_dirs()
    if not dirs:
        print("[ROLLBACK] no install backups found under ~/.claude/backups/")
        return 1
    if which:
        chosen = next(
            (d for d in dirs if d.name == which or d.name == f"install_{which}"), None
        )
        if chosen is None:
            print(f"[ROLLBACK] no backup named {which}. Available:")
            for d in dirs[:10]:
                print(f"  {d.name}")
            return 1
    else:
        chosen = dirs[0]

    receipt_path = chosen / "install_receipt.json"
    if not receipt_path.is_file():
        # Pre-receipt installs left only the backup files. Restoring those blindly would
        # not undo the files the install CREATED, leaving a half-rolled-back config that
        # looks complete — refuse instead, and say what can be done by hand.
        print(f"[ROLLBACK] {chosen.name} has no install_receipt.json (installed by an")
        print("  older build). Its backups are still there and can be copied back by hand:")
        print(f"  {chosen}")
        return 1

    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[ROLLBACK] invalid receipt: {exc}")
        return 1
    entries = receipt.get("entries", [])
    if (
        receipt.get("schema_version") != _RECEIPT_SCHEMA_VERSION
        or not isinstance(entries, list)
    ):
        print(f"[ROLLBACK] {chosen.name} uses an unsupported receipt schema.")
        print("  Refusing an unverified rollback; restore its backups manually if needed.")
        return 1

    print(f"[ROLLBACK] {chosen.name} ({'APPLY' if apply else 'DRY RUN'})")
    conflicts: list[str] = []
    for item in entries:
        if not isinstance(item, dict) or not item.get("dest"):
            conflicts.append("receipt contains an invalid entry")
            continue
        dest = Path(item["dest"])
        backup = Path(item["backup"]) if item.get("backup") else None
        expected_post = item.get("post_sha256")
        actual_post = _file_sha256(dest)
        if not isinstance(expected_post, str) or actual_post != expected_post:
            conflicts.append(
                f"{dest}: destination changed "
                f"(expected {expected_post or 'missing hash'}, found {actual_post or 'missing'})"
            )
        expected_pre = item.get("pre_sha256")
        if backup is not None:
            backup_hash = _file_sha256(backup)
            if not isinstance(expected_pre, str) or backup_hash != expected_pre:
                conflicts.append(
                    f"{backup}: backup changed or missing "
                    f"(expected {expected_pre or 'missing hash'}, found {backup_hash or 'missing'})"
                )
        elif expected_pre is not None:
            conflicts.append(f"{dest}: receipt has a pre-install hash but no backup")

    if conflicts:
        print("  ABORTED: rollback preflight found conflicts; nothing was changed.")
        for conflict in conflicts:
            print(f"  !! {conflict}")
        return 2

    restored = deleted = 0
    for item in entries:
        dest = Path(item["dest"])
        backup = Path(item["backup"]) if item.get("backup") else None
        if backup is not None:
            print(f"  restore  {dest}")
            restored += 1
            if apply:
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(backup, dest)
        else:
            print(f"  delete   {dest} — created by that install")
            deleted += 1
            if apply:
                dest.unlink()
    print(f"\n  restore {restored}, delete {deleted}")
    if not apply:
        print("  dry run — rerun with --apply to write")
    return 0


def _targets(project_root: Path | None = None) -> list[tuple[Path, Path, str]]:
    """(source, destination, manifest-key) for everything in dist/config.

    `project_root` only affects the opencode subagents: they install into the worktree's
    .opencode/agents/ so a project gets them without the installer writing into the user's
    ~/.config/opencode at all. Without a project root there is nowhere else to put them,
    so they fall back to the global dir.
    """
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
    # Ship only the current OS's script flavour: Windows gets .ps1, POSIX gets .sh. Non-script
    # files (e.g. intent-map.json) carry no ext filter and install on both.
    want_ext = "ps1" if os.name == "nt" else "sh"
    for family, sub, dest_dir in (
        ("claude", "skills", HOME / ".claude" / "skills"),
        ("claude", "commands", HOME / ".claude" / "commands"),
        ("claude", "hooks", HOME / ".claude" / "hooks"),
        # The file stem becomes the custom OpenCode agent name. Project scope is the
        # default: opencode reads
        # <worktree>/.opencode/agents/, so the roster ships without the installer touching
        # the user's global opencode config.
        (
            "opencode",
            "agents",
            (project_root / ".opencode" / "agents")
            if project_root
            else HOME / ".config" / "opencode" / "agents",
        ),
    ):
        source_dir = DIST_CONFIG / family / sub
        if source_dir.is_dir():
            for path in sorted(source_dir.iterdir()):
                if path.is_file():
                    ext = path.suffix.lstrip(".").lower()
                    if ext in ("ps1", "sh") and ext != want_ext:
                        continue
                    mapping.append(
                        (path, dest_dir / path.name, f"{family}/{sub}/{path.name}")
                    )
    return [(s, d, k) for s, d, k in mapping if s.exists()]


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _settings_would_change(src: Path, dest: Path, only_command: bool) -> bool:
    plan = Plan()
    _install_settings(src, dest, plan, False, Path("."), only_command)
    writes = {"create", "merge", "replace", "mode"}
    return any(action in writes for action, _target, _detail in plan.actions) or any(
        "not valid JSON" in warning or "root is not a JSON object" in warning
        for warning in plan.warnings
    )


def _opencode_would_change(
    src: Path, dest: Path, project_root: Path | None
) -> bool:
    plan = Plan()
    _install_opencode(src, dest, plan, False, Path("."), project_root)
    writes = {"create", "merge", "replace"}
    return any(action in writes for action, _target, _detail in plan.actions) or any(
        "not valid JSON/JSONC" in warning or "root is not a JSON object" in warning
        for warning in plan.warnings
    )


def _run_check(manifest: dict, project_root: Path | None = None) -> int:
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

    checks = list(_targets(project_root))
    settings_src = DIST_CONFIG / "claude" / "settings.template.json"
    if settings_src.exists():
        # settings.json is a key-wise JSON merge, not a copy — bundle-check only.
        checks.append((settings_src, None, "claude/settings.template.json"))
    opencode_src = DIST_CONFIG / "opencode" / "opencode.template.json"
    if opencode_src.exists():
        # opencode.json is merged, not copied, so its installed state is checked below.
        checks.append((opencode_src, None, "opencode/opencode.template.json"))

    for source, dest, key in checks:
        dist_text = source.read_text(encoding="utf-8")
        entry = by_path.get(key)
        if not entry or _hash(dist_text) != entry.get("sha256"):
            bundle_stale.append(key)
        if dest is None:
            continue
        resolved = _resolve_placeholders(dist_text, project_root)
        if key == "claude/CLAUDE.md":
            resolved = _apply_intent_mode(resolved, _stored_only_command())
        if not dest.exists():
            installed_missing.append(key)
            continue
        installed = _read_text_lenient(dest)
        if key in MARKERS:
            start, end = MARKERS[key]
            want = _managed_block(resolved, start, end)
            have = _managed_block(installed, start, end)
            if want is None or have is None or want != have:
                installed_drift.append(key)
        elif installed != resolved:
            installed_drift.append(key)

    only_command = _stored_only_command()
    settings_dest = HOME / ".claude" / "settings.json"
    if settings_src.exists():
        if not settings_dest.exists():
            installed_missing.append("claude/settings.json")
        elif _settings_would_change(settings_src, settings_dest, only_command):
            installed_drift.append("claude/settings.json")

    opencode_dest = _opencode_config_path()
    if opencode_src.exists():
        if not opencode_dest.exists():
            installed_missing.append("opencode/opencode.json")
        elif _opencode_would_change(opencode_src, opencode_dest, project_root):
            installed_drift.append("opencode/opencode.json")

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

    # Report component ownership so the required version bump is explicit.
    versions = manifest.get("versions") or {}
    if versions:
        changed_keys = set(bundle_stale) | set(installed_drift) | set(installed_missing)
        if "claude/settings.json" in changed_keys:
            changed_keys.add("claude/settings.template.json")
        if "opencode/opencode.json" in changed_keys:
            changed_keys.add("opencode/opencode.template.json")
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
        print(
            "  fix: python tools/gen_manifest.py (bundle) | python install.py --apply (installed)"
        )
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
        "--only-command",
        action="store_true",
        help="install the main-agent block WITHOUT natural-language auto-intent: commands "
        "must be invoked by their /. prefix, and the UserPromptSubmit intent hook is not "
        "registered",
    )
    parser.add_argument(
        "--auto-intent",
        action="store_true",
        help="undo --only-command: restore natural-language auto-intent on the next install",
    )
    parser.add_argument(
        "--rollback",
        nargs="?",
        const="",
        metavar="BACKUP_ID",
        help="undo an install from its receipt (default: the most recent). Dry run unless "
        "--apply is also given",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift (installed config vs bundle, dist vs manifest); combine with "
        "--init-project DIR to check project-scoped OpenCode agents",
    )
    args = parser.parse_args()
    apply = args.apply
    _RECEIPT.clear()

    if args.rollback is not None:
        return _run_rollback(args.rollback or None, apply)

    if args.only_command and args.auto_intent:
        print("[INSTALL] --only-command and --auto-intent are opposites; pick one")
        return 2
    # No flag = keep whatever the last install chose. An upgrade must not change a
    # deliberate choice just because it was not restated.
    if not args.only_command and not args.auto_intent:
        args.only_command = _stored_only_command()

    if not MANIFEST.exists():
        print("[INSTALL] dist/manifest.json missing — run tools/gen_manifest.py first")
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    project_root = Path(args.init_project).resolve() if args.init_project else None
    if args.check:
        return _run_check(manifest, project_root)

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
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

    for source, dest, key in _targets(project_root):
        _install_text(
            source, dest, key, plan, apply, backup_root, project_root, args.only_command
        )

    settings_src = DIST_CONFIG / "claude" / "settings.template.json"
    if settings_src.exists():
        _install_settings(
            settings_src,
            HOME / ".claude" / "settings.json",
            plan,
            apply,
            backup_root,
            args.only_command,
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

    _store_only_command(args.only_command, plan, apply, backup_root)

    # Receipt goes down with the backups, not beside the code: it is only meaningful
    # paired with them, and --rollback refuses to act without it.
    if apply and _RECEIPT:
        backup_root.mkdir(parents=True, exist_ok=True)
        receipt_path = backup_root / "install_receipt.json"
        receipt_tmp = receipt_path.with_suffix(".tmp")
        receipt_tmp.write_text(
            json.dumps(
                {
                    "schema_version": _RECEIPT_SCHEMA_VERSION,
                    "installed_at": datetime.now(timezone.utc).isoformat(),
                    "version": manifest.get("version"),
                    "only_command": bool(args.only_command),
                    "project_root": str(project_root) if project_root else None,
                    "entries": _RECEIPT,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        os.replace(receipt_tmp, receipt_path)

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
