"""Read-only drift detection: what WOULD change if an install ran now.

Depends on the write layers on purpose — the only honest way to answer "would this
change?" is to ask the same code that performs the change.
"""

from pathlib import Path

from installer.base import (
    DIST_CONFIG,
    HOME,
    MARKERS,
    Plan,
    _apply_intent_mode,
    _hash,
    _managed_block,
    _read_text_lenient,
    _resolve_placeholders,
    _targets,
)
from installer.rollback import _stored_only_command
from installer.settings import (
    _install_opencode,
    _install_settings,
    _opencode_config_path,
)

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


def _detect_project_root() -> Path | None:
    """The workspace this install run is standing in, or None.

    Derived from an existing `.workflow/config.json` walking up from the cwd, never from a
    flag. A flag let `--check` run with a narrower scope than doctor uses and report READY
    while the project-scoped boundary was missing — the exact state a check exists to catch.
    Requiring the marker also means running the installer from an unrelated directory never
    writes project files into it.
    """
    for candidate in [Path.cwd().resolve(), *Path.cwd().resolve().parents]:
        if (candidate / ".workflow" / "config.json").exists():
            return candidate
    return None


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

    checks = list(_targets())
    settings_src = DIST_CONFIG / "claude" / "settings.template.json"
    if settings_src.exists():
        # settings.json is a key-wise JSON merge, not a copy — bundle-check only.
        checks.append((settings_src, None, "claude/settings.template.json"))
    opencode_src = DIST_CONFIG / "opencode" / "opencode.template.json"
    if opencode_src.exists():
        # opencode.json is merged, not copied, so its installed state is checked below.
        checks.append((opencode_src, None, "opencode/opencode.template.json"))
    project_src = DIST_CONFIG / "opencode" / "opencode.project.json"
    if project_src.exists():
        checks.append((project_src, None, "opencode/opencode.project.json"))

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

    # The project config carries the secret-file denial. Checking only the global one let
    # --check report OK while the boundary was missing or had been edited away — the exact
    # state a check exists to catch.
    project_scope_note = None
    if project_src.exists():
        if project_root:
            project_dest = project_root / "opencode.json"
            if not project_dest.exists():
                installed_missing.append("opencode/opencode.project.json")
            elif _opencode_would_change(project_src, project_dest, project_root):
                installed_drift.append("opencode/opencode.project.json")
        else:
            # Say so rather than pass quietly: an unchecked boundary is not a clean one.
            project_scope_note = (
                "no .workflow/ found from this directory — the project boundary "
                "(<project_root>/opencode.json) was NOT checked. Run init there first."
            )

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
    if project_scope_note:
        print(f"  project scope: SKIPPED — {project_scope_note}")

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
