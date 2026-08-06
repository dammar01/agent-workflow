#!/usr/bin/env python3
"""Stamp the shipped docs with the one version number the code actually uses.

`config.settings.TOOL_VERSION` is the single source. Everything else — README headings,
the CLAUDE.md / AGENTS.md banners, the skill files that quote a version back at the user —
is a copy, and copies drift. v3.4.2 shipped with thirteen places still saying v3.4.1,
including the `/.help` output a new teammate reads first.

Only versions on anchored lines are touched. A bare semver anywhere else is left alone:
`utils/path_guard.py` legitimately says "removed in v3.4.1", and a changelog written for
a past release must keep saying what it said.

  python tools/stamp_version.py            # rewrite the stale ones
  python tools/stamp_version.py --check    # exit 1 if any are stale (CI / e2e)
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from config.providers import PROVIDER_BUNDLES  # noqa: E402
from config.settings import TOOL_VERSION  # noqa: E402

# path -> substrings that mark a line as carrying the CURRENT version. A line is rewritten
# only when it contains one of them, so adding a new mention means adding it here too —
# deliberate: silent stamping of an unrelated version is how the drift started.
TARGETS: dict[str, tuple[str, ...]] = {
    "README.md": (
        "# agent-workflow v",
        "## Install (v",
        "Kunci reliability (v",
        "### Liveness worker (v",
        "Catatan rilis:",
    ),
    "dist/config/claude/CLAUDE.md": (
        "# Claude Code — Personal Global Config (v",
        "WORKFLOW-MAIN-AGENT:START",
        "## Workflow Main Agent —",
    ),
    "dist/config/claude/skills/doctor.md": (".workflow/config.json : v",),
    "dist/config/claude/skills/help.md": (
        "description: Command reference v",
        "[COMMAND GUIDE — v",
    ),
    "dist/config/claude/skills/init.md": ("generated: run/inspect/check",),
}

# Each provider's instruction file carries the same banner. Derived rather than listed so
# a second provider is stamped by existing, not by someone remembering to add a line here.
# The title anchor stays loose ("Second Agent —" matches "# OpenCode Second Agent — v…")
# because the provider's own name is in it.
for _name, _bundle in PROVIDER_BUNDLES.items():
    TARGETS[f"dist/config/{_name}/{_bundle['instructions'][0]}"] = (
        "WORKFLOW-SECOND-AGENT:START",
        "Second Agent —",
    )

_SEMVER = re.compile(r"\d+\.\d+\.\d+")


def _restamp(text: str, anchors: tuple[str, ...]) -> tuple[str, int]:
    """Rewrite the semver on every anchored line. Returns (text, lines_changed)."""
    changed = 0
    lines = text.splitlines(keepends=True)
    for index, line in enumerate(lines):
        if not any(anchor in line for anchor in anchors):
            continue
        stamped = _SEMVER.sub(TOOL_VERSION, line)
        if stamped != line:
            lines[index] = stamped
            changed += 1
    return "".join(lines), changed


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    stale: list[str] = []
    missing: list[str] = []

    for relative, anchors in TARGETS.items():
        path = REPO_ROOT / relative
        if not path.exists():
            missing.append(relative)
            continue
        original = path.read_text(encoding="utf-8")
        stamped, changed = _restamp(original, anchors)
        if not changed:
            continue
        stale.append(f"{relative} ({changed} line(s))")
        if not check_only:
            path.write_text(stamped, encoding="utf-8")

    if missing:
        print("[stamp_version] target file(s) missing:")
        for relative in missing:
            print(f"  - {relative}")
        return 1
    if not stale:
        print(f"[stamp_version] OK — every target already says v{TOOL_VERSION}")
        return 0
    verb = "stale" if check_only else "restamped"
    print(f"[stamp_version] {verb} at v{TOOL_VERSION}:")
    for entry in stale:
        print(f"  - {entry}")
    if check_only:
        print("Fix: python tools/stamp_version.py")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
