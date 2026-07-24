#!/usr/bin/env python3
"""Validate that the standalone skill files and CLAUDE.md's command registry agree.

Direction inverted in v3.4.0 (P1.5): `dist/config/claude/skills/<name>.md` is now the
SINGLE source of truth for every skill body. CLAUDE.md no longer embeds skill bodies —
it carries only the orchestrator contract plus a command registry — so there is nothing
to generate anymore. What can still drift is the SET of skills: a new skills/<name>.md
added without registering `/.name` in CLAUDE.md (or a registry entry with no file).

This tool checks that bijection. It writes nothing.

  python tools/sync_skills.py            # report mismatches
  python tools/sync_skills.py --check    # exit 1 if the sets differ (for CI / doctor)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLAUDE_MD = REPO_ROOT / "dist" / "config" / "claude" / "CLAUDE.md"
SKILLS_DIR = REPO_ROOT / "dist" / "config" / "claude" / "skills"

# Registry lines list every command as `/.name` (with an optional trailing flag like -y).
_REGISTRY_RE = re.compile(r"^(?:LOCAL|DELEGATED):", re.MULTILINE)
_COMMAND_RE = re.compile(r"/\.([a-z][a-z0-9_-]*)")


def _registry_commands(claude_text: str) -> set[str]:
    """Command names drawn from the `LOCAL:` / `DELEGATED:` registry lines only."""
    commands: set[str] = set()
    for line in claude_text.splitlines():
        if line.startswith(("LOCAL:", "DELEGATED:")):
            commands.update(_COMMAND_RE.findall(line))
    return commands


def _skill_files() -> set[str]:
    return {p.stem for p in SKILLS_DIR.glob("*.md") if p.is_file()}


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    claude_text = CLAUDE_MD.read_text(encoding="utf-8")
    commands = _registry_commands(claude_text)
    files = _skill_files()
    if not commands:
        raise SystemExit(
            "[sync_skills] no LOCAL:/DELEGATED: registry lines found in CLAUDE.md"
        )
    if not files:
        raise SystemExit("[sync_skills] no skills/*.md files found")

    missing_files = sorted(commands - files)      # registered command, no skill file
    unregistered = sorted(files - commands)       # skill file, not in the registry

    if not missing_files and not unregistered:
        print(f"[sync_skills] OK — {len(files)} skills match the command registry")
        return 0

    print("[sync_skills] MISMATCH between skills/ and CLAUDE.md command registry:")
    for name in missing_files:
        print(f"  - /.{name} is in the registry but skills/{name}.md is missing")
    for name in unregistered:
        print(f"  - skills/{name}.md exists but /.{name} is not in the registry")
    if check_only:
        return 1
    print("Fix: add the missing skill file, or register/unregister the command in CLAUDE.md")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
