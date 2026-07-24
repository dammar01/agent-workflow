#!/usr/bin/env python3
"""Generate standalone skill files from the CLAUDE.md embedded blocks.

`dist/config/claude/CLAUDE.md` is the single source of truth for skill bodies: it
carries one `### FILE: {AGENT_DIR}/skills/<name>.md` block per skill. Claude Code also
reads the standalone `dist/config/claude/skills/<name>.md` files (a slash command opens
its own file), so the two copies must never drift. This tool derives the standalone
files from the embedded blocks so they cannot.

Usage:
  python tools/sync_skills.py            # regenerate skills/*.md from CLAUDE.md
  python tools/sync_skills.py --check    # exit 1 if any skill file is out of sync

Run --check in CI / doctor to catch a hand-edit that touched only one copy.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CLAUDE_MD = REPO_ROOT / "dist" / "config" / "claude" / "CLAUDE.md"
SKILLS_DIR = REPO_ROOT / "dist" / "config" / "claude" / "skills"

# The embedded skill blocks live between the first "### FILE:" marker and the
# "## STEP 4" memory section. Everything past STEP 4 is orchestrator prose, not a skill.
_REGION_START = "### FILE:"
_REGION_END = "## STEP 4"
_FILE_RE = re.compile(r"skills/([A-Za-z0-9_-]+\.md)")


def _parse_blocks(claude_text: str) -> dict[str, str]:
    """Return {filename: body} for every embedded skill block in CLAUDE.md."""
    start = claude_text.find(_REGION_START)
    if start == -1:
        raise SystemExit(f"[sync_skills] no '{_REGION_START}' block found in {CLAUDE_MD}")
    end = claude_text.find(_REGION_END, start)
    if end == -1:
        end = len(claude_text)
    region = claude_text[start:end]

    blocks: dict[str, str] = {}
    for chunk in region.split(_REGION_START):
        chunk = chunk.strip()
        if not chunk:
            continue
        header, _, rest = chunk.partition("\n")
        match = _FILE_RE.search(header)
        if not match:
            continue
        name = match.group(1)
        body = rest.strip()
        # Drop the trailing horizontal rule that separates blocks in CLAUDE.md.
        if body.endswith("---"):
            body = body[: -len("---")].rstrip()
        blocks[name] = body + "\n"
    return blocks


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    claude_text = CLAUDE_MD.read_text(encoding="utf-8")
    blocks = _parse_blocks(claude_text)
    if not blocks:
        raise SystemExit("[sync_skills] parsed zero skill blocks — refusing to touch skills/")

    drifted: list[str] = []
    written: list[str] = []
    for name, body in sorted(blocks.items()):
        target = SKILLS_DIR / name
        current = target.read_text(encoding="utf-8") if target.exists() else None
        if current == body:
            continue
        if check_only:
            drifted.append(name)
        else:
            target.write_text(body, encoding="utf-8", newline="\n")
            written.append(name)

    if check_only:
        if drifted:
            print("[sync_skills] DRIFTED — skills out of sync with CLAUDE.md:")
            for name in drifted:
                print(f"  - skills/{name}")
            print("Run: python tools/sync_skills.py")
            return 1
        print(f"[sync_skills] OK — {len(blocks)} skills in sync with CLAUDE.md")
        return 0

    if written:
        print(f"[sync_skills] regenerated {len(written)} skill(s) from CLAUDE.md:")
        for name in written:
            print(f"  - skills/{name}")
    else:
        print(f"[sync_skills] already in sync — {len(blocks)} skills unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
