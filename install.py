"""Install the agent-workflow config onto this machine.

Consumes `dist/` (produced by tools/maintain/extract_config.py) and applies it to the local
agent directories.

    python install.py              # DRY RUN — show every change, write nothing
    python install.py --apply      # actually write

Project scope is detected, not passed: run it from anywhere inside a directory that has a
.workflow/ and that workspace is refreshed in place too (scripts regenerated, new config
keys backfilled, opencode.json boundary re-enforced, sessions/ untouched). Scaffolding a
NEW workspace belongs to init — `python main.py --command init --work-dir DIR` — so there
is one bootstrap path rather than a flag that half-duplicates it.

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
import json
import os
from datetime import datetime, timezone

# Split out in v3.4.3; re-exported so `main()` below and any external caller keep
# addressing install.py exactly as before.
from installer.base import (  # noqa: E402,F401
    DIST,
    DIST_CONFIG,
    HOME,
    MANIFEST,
    MARKERS,
    REPO_ROOT,
    SETTINGS_REQUIRED,
    Plan,
    _RECEIPT,
    _RECEIPT_SCHEMA_VERSION,
    _apply_intent_mode,
    _backup,
    _env_values,
    _file_sha256,
    _hash,
    _install_text,
    _load_env_file,
    _managed_block,
    _merge_managed,
    _read_text_lenient,
    _record,
    _resolve_in_json,
    _resolve_placeholders,
    _scan_missing_env,
    _strip_section,
    _targets,
)

# After installer.base: it is what puts REPO_ROOT on sys.path when install.py is imported
# rather than run directly (tools/e2e/e2e.py does exactly that).
from config.providers import PROVIDER_BUNDLES  # noqa: E402
from installer.check import (  # noqa: E402,F401
    _detect_project_root,
    _provider_config_would_change,
    _run_check,
    _settings_would_change,
)
from installer.rollback import (  # noqa: E402,F401
    _backup_dirs,
    _run_rollback,
    _store_only_command,
    _stored_only_command,
)
from installer.settings import (  # noqa: E402,F401
    _drop_intent_hook,
    _hook_script_ids,
    _install_deps,
    _install_provider_config,
    _install_settings,
    _merge_hook_entries,
    _provider_config_path,
    _remove_intent_hook_entries,
    _rewrite_hooks_for_posix,
)


_ENV_BLOCK_MARKER = "# >>> agent-workflow AGENT_PATH >>>"
_ENV_BLOCK_END = "# <<< agent-workflow AGENT_PATH <<<"


def _persist_agent_path(agent_path, apply: bool) -> None:
    """Set AGENT_PATH for future shells. Opt-in, because it writes outside the repo.

    Printing the command and leaving the user to paste it is where `init` most often
    fails later, with a symptom that points at .workflow/ rather than at an env var that
    was never set. But this is the one part of the installer that outlives the repo — a
    registry value and a shell profile survive deleting the checkout — so it stays behind
    an explicit flag and says exactly what it wrote.

    Deliberately NOT recorded in the install receipt: every receipt entry is a file, and
    an entry without a backup is DELETED on rollback. A profile path in that list would
    make `--rollback` erase the user's .bashrc. The undo is printed instead.
    """
    target = str(agent_path)
    if os.name == "nt":
        print()
        print(f"  set AGENT_PATH (user environment) = {target}")
        print(r"    location: HKCU\Environment  — persists across sessions")
        print(
            '    undo: [Environment]::SetEnvironmentVariable("AGENT_PATH",$null,"User")'
        )
        if not apply:
            print("    DRY RUN — not written.")
            return
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE
        ) as key:
            winreg.SetValueEx(key, "AGENT_PATH", 0, winreg.REG_SZ, target)
        try:
            # Without the broadcast the value is live only for processes started after the
            # next logon; explorer-spawned shells pick it up immediately with it.
            import ctypes

            ctypes.windll.user32.SendMessageTimeoutW(
                0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, None
            )
        except Exception as exc:  # pragma: no cover - cosmetic refresh only
            print(f"    (written; environment broadcast skipped: {exc})")
        print("    written.")
        return

    shell = os.environ.get("SHELL", "")
    profile = HOME / (".zshrc" if shell.endswith("zsh") else ".bashrc")
    existing = ""
    if profile.exists():
        existing = profile.read_text(encoding="utf-8")
    line = f'export AGENT_PATH="{target}"'
    print()
    print(f"  set AGENT_PATH (shell profile) = {target}")
    print(f"    location: {profile}  — appended, nothing overwritten")
    print(f"    undo: delete the block between {_ENV_BLOCK_MARKER} and {_ENV_BLOCK_END}")
    if line in existing:
        print("    already present — nothing to do.")
        return
    if not apply:
        print("    DRY RUN — not written.")
        return
    block = f"\n{_ENV_BLOCK_MARKER}\n{line}\n{_ENV_BLOCK_END}\n"
    with profile.open("a", encoding="utf-8") as handle:
        handle.write(block)
    print(f"    appended:\n      {line}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Install agent-workflow config")
    parser.add_argument(
        "--apply", action="store_true", help="write changes (default: dry run)"
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
        "--set-env",
        action="store_true",
        help="also persist AGENT_PATH for future shells (Windows: HKCU\\Environment; "
        "POSIX: appended to ~/.bashrc or ~/.zshrc). Opt-in because it writes outside "
        "the repo and outlives it. Dry run unless --apply is also given",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="report drift (installed config vs bundle, dist vs manifest, and the "
        "project-scoped files when run from a directory that has a .workflow/)",
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
        print("[INSTALL] dist/manifest.json missing — run tools/maintain/gen_manifest.py first")
        return 1
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    project_root = _detect_project_root()
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

    for source, dest, key in _targets():
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
    for provider, bundle in PROVIDER_BUNDLES.items():
        provider_src = DIST_CONFIG / provider / bundle["global_config"][0]
        if provider_src.exists():
            _install_provider_config(
                provider,
                provider_src,
                _provider_config_path(provider),
                plan,
                apply,
                backup_root,
                project_root,
            )
    # <project_root>/opencode.json (the secret-file boundary) is NOT installed here. It
    # belongs to a workspace, so init/upgrade owns it — see
    # core.runtime.workflow_runtime._install_project_boundary. The upgrade call below reaches it.
    agent_path = REPO_ROOT / "main.py"
    if os.environ.get("AGENT_PATH") != str(agent_path):
        plan.add(
            "env", "AGENT_PATH", f"set to {agent_path} (shell command printed below)"
        )

    if project_root:
        # project_root is only set when a .workflow/ already exists here, so this is always
        # an in-place upgrade. Scaffolding a NEW workspace is `init`'s job (main.py
        # --command init --work-dir DIR) — one bootstrap path instead of two.
        from core.runtime.upgrade import upgrade_workflow_workspace, workspace_versions

        versions = workspace_versions(project_root)
        plan.add(
            "upgrade",
            project_root / ".workflow",
            f"tool {versions['installed_tool_version']} -> {versions['current_tool_version']}"
            " (regenerate scripts, backfill config keys, refresh opencode.json, keep sessions/)",
        )
        if apply:
            try:
                upgrade_workflow_workspace(project_root, str(agent_path))
            except ValueError as exc:
                # Refuses while a delegated job is live. Reported, not raised: the
                # global config install above already succeeded, and aborting here
                # would leave the user unsure which half of the run took effect.
                plan.warn(f"workspace not upgraded: {exc}")

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

    if args.set_env:
        _persist_agent_path(agent_path, apply)
    elif os.environ.get("AGENT_PATH") != str(agent_path):
        print()
        print("  set AGENT_PATH so `init` can bootstrap new projects:")
        if os.name == "nt":
            print(
                f'    [Environment]::SetEnvironmentVariable("AGENT_PATH","{agent_path}","User")'
            )
        else:
            print(f'    export AGENT_PATH="{agent_path}"')
        print("    or re-run this installer with --set-env to have it written for you")

    if not apply:
        print()
        print("  DRY RUN — nothing was written. Re-run with --apply to install.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
