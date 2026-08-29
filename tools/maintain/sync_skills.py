#!/usr/bin/env python3
"""Validate that the skill files, the command wrappers, and CLAUDE.md's registry agree.

`dist/config/claude/skills/<name>.md` is the single source of truth for every skill body.
CLAUDE.md carries only the orchestrator contract plus a command registry, so there is
nothing to generate. What can still drift is the set of skills: a new skills/<name>.md
added without registering `/.name` in CLAUDE.md, or a registry entry with no file.

Three artifacts have to agree, not two. `commands/.<name>.md` is the wrapper that makes
`/.name` typeable at all, and it is the leg this tool used to skip: /.promote shipped with
a registry entry and a skill body and no wrapper, so the command existed everywhere except
where a user could invoke it, and this check stayed green through the whole release.

This tool checks that bijection. It writes nothing.

  python tools/maintain/sync_skills.py            # report mismatches
  python tools/maintain/sync_skills.py --check    # exit 1 if the sets differ (for CI / doctor)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
CLAUDE_MD = REPO_ROOT / "dist" / "config" / "claude" / "CLAUDE.md"
SKILLS_DIR = REPO_ROOT / "dist" / "config" / "claude" / "skills"
COMMANDS_DIR = REPO_ROOT / "dist" / "config" / "claude" / "commands"

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


def _command_files() -> set[str]:
    """Wrapper names, without the leading dot the filename carries (`.plan.md` -> `plan`)."""
    return {
        p.stem.lstrip(".")
        for p in COMMANDS_DIR.glob(".*.md")
        if p.is_file() and p.stem.lstrip(".")
    }


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    claude_text = CLAUDE_MD.read_text(encoding="utf-8")
    commands = _registry_commands(claude_text)
    files = _skill_files()
    wrappers = _command_files()
    if not commands:
        raise SystemExit(
            "[sync_skills] no LOCAL:/DELEGATED: registry lines found in CLAUDE.md"
        )
    if not files:
        raise SystemExit("[sync_skills] no skills/*.md files found")
    if not wrappers:
        raise SystemExit("[sync_skills] no commands/.*.md wrapper files found")

    missing_files = sorted(commands - files)      # registered command, no skill file
    unregistered = sorted(files - commands)       # skill file, not in the registry
    missing_wrappers = sorted(commands - wrappers)   # registered command, not typeable
    orphan_wrappers = sorted(wrappers - commands)    # wrapper for nothing in the registry

    if not (missing_files or unregistered or missing_wrappers or orphan_wrappers):
        print(
            f"[sync_skills] OK — {len(files)} skills and {len(wrappers)} command wrappers "
            "match the command registry"
        )
        return 0

    print("[sync_skills] MISMATCH between skills/, commands/ and CLAUDE.md registry:")
    for name in missing_files:
        print(f"  - /.{name} is in the registry but skills/{name}.md is missing")
    for name in unregistered:
        print(f"  - skills/{name}.md exists but /.{name} is not in the registry")
    for name in missing_wrappers:
        print(
            f"  - /.{name} is in the registry but commands/.{name}.md is missing "
            "— the command cannot be invoked"
        )
    for name in orphan_wrappers:
        print(f"  - commands/.{name}.md exists but /.{name} is not in the registry")
    if check_only:
        return 1
    print(
        "Fix: add the missing skill file or command wrapper, "
        "or register/unregister the command in CLAUDE.md"
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
