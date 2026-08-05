"""settings.json and the OpenCode config: hook merging and policy enforcement.

Kept apart from the check layer because these WRITE. The distinction matters for
rollback: everything here appends to the receipt, and a check must not.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from core.opencode_policy import load_json_or_jsonc, merge_opencode_policy
from installer.base import (
    HOME,
    REPO_ROOT,
    SETTINGS_REQUIRED,
    Plan,
    _backup,
    _file_sha256,
    _read_text_lenient,
    _record,
    _resolve_in_json,
    _resolve_placeholders,
)

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


def _opencode_config_path() -> Path:
    """The native opencode config file to merge into. Prefer an existing .jsonc (the
    common opencode default), else .json. opencode reads either from this directory."""
    directory = HOME / ".config" / "opencode"
    jsonc = directory / "opencode.jsonc"
    if jsonc.exists():
        return jsonc
    return directory / "opencode.json"


def _install_opencode(
    src: Path,
    dest: Path,
    plan: Plan,
    apply: bool,
    backup_root: Path,
    project_root: Path | None,
    key: str = "opencode/opencode.json",
) -> None:
    """Preserve unrelated OpenCode config while enforcing workflow permissions.

    MCP servers, providers, and other agents remain additive. Environment placeholders
    are resolved after preflight proves the required values exist.

    `key` names the receipt/backup slot. The global and the project config both come
    through here, and a shared key would have them overwrite each other's backup — the
    second install would then be unrollbackable.
    """
    incoming = json.loads(
        _resolve_placeholders(src.read_text(encoding="utf-8"), project_root)
    )
    current: dict = {}
    if dest.exists():
        try:
            current = load_json_or_jsonc(dest)
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
    merged, added, enforced = merge_opencode_policy(current, incoming, plan.warn)
    if merged == current and enforced == 0:
        plan.add("unchanged", dest)
        return
    if dest.exists():
        pre_sha256 = _file_sha256(dest)
        saved = _backup(dest, backup_root, plan, apply, key)
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
        _record("merge" if saved else "create", dest, key, saved, pre_sha256)


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
