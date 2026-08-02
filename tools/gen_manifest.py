#!/usr/bin/env python3
"""Regenerate dist/manifest.json from the dist/ tree.

dist/ is the canonical, Git-reviewed bundle; ~/.claude is an install target downstream of
it. tools/extract_config.py still exists to *bootstrap* dist/ from a maintainer's ~/.claude,
but once dist/ is edited in-repo the manifest must be rebuilt from dist/ itself — not from
~/.claude, which would drag stale home-dir content back in. This tool does exactly that,
hashing the same way extract_config.py does so the two stay interchangeable:

  sha256(read_text().encode("utf-8"))   # read_text normalises CRLF->LF (universal newlines)

Usage:
  python tools/gen_manifest.py            # rewrite dist/manifest.json
  python tools/gen_manifest.py --check    # exit 1 if manifest is stale vs dist/
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST_DIR = REPO_ROOT / "dist"
DIST_CONFIG = DIST_DIR / "config"
MANIFEST = DIST_DIR / "manifest.json"

# Same one-time target map extract_config.py writes, so a rebuilt manifest is byte-identical
# in structure to a bootstrapped one.
TARGETS = {
    "claude/CLAUDE.md": "{{HOME}}/.claude/CLAUDE.md",
    "claude/skills": "{{HOME}}/.claude/skills",
    "claude/commands": "{{HOME}}/.claude/commands",
    "claude/hooks": "{{HOME}}/.claude/hooks",
    "claude/settings.template.json": "{{HOME}}/.claude/settings.json",
    "opencode/AGENTS.md": "{{HOME}}/.config/opencode/AGENTS.md",
    # Subagents install per-project by default: opencode reads .opencode/agents/ in the
    # worktree, so shipping them there leaves the user's own ~/.config/opencode untouched.
    # Falls back to the global dir when no project root is known (plain global install).
    "opencode/agents": "{{PROJECT_ROOT}}/.opencode/agents",
    "opencode/opencode.template.json": "{{HOME}}/.config/opencode/opencode.json",
}


def _dist_files() -> list[str]:
    """dist-relative paths in the same order extract_config.py emits them."""
    paths: list[str] = ["claude/CLAUDE.md"]
    skills = DIST_CONFIG / "claude" / "skills"
    if skills.is_dir():
        paths += [
            f"claude/skills/{p.name}"
            for p in sorted(skills.glob("*.md"))
            if p.is_file()
        ]
    commands = DIST_CONFIG / "claude" / "commands"
    if commands.is_dir():
        paths += [
            f"claude/commands/{p.name}"
            for p in sorted(commands.glob("*.md"))
            if p.is_file()
        ]
    hooks = DIST_CONFIG / "claude" / "hooks"
    if hooks.is_dir():
        found = sorted(
            p for p in hooks.iterdir() if p.suffix in {".ps1", ".sh"} and p.is_file()
        )
        paths += [f"claude/hooks/{p.name}" for p in found]
    if (DIST_CONFIG / "opencode" / "AGENTS.md").is_file():
        paths.append("opencode/AGENTS.md")
    agents = DIST_CONFIG / "opencode" / "agents"
    if agents.is_dir():
        paths += [
            f"opencode/agents/{p.name}"
            for p in sorted(agents.glob("*.md"))
            if p.is_file()
        ]
    if (DIST_CONFIG / "claude" / "settings.template.json").is_file():
        paths.append("claude/settings.template.json")
    if (DIST_CONFIG / "opencode" / "opencode.template.json").is_file():
        paths.append("opencode/opencode.template.json")
    return paths


def _merge_kind(rel: str) -> str:
    return (
        "replace"
        if "/skills/" in rel
        or "/commands/" in rel
        or "/agents/" in rel
        or rel.endswith((".ps1", ".sh"))
        else "merge"
    )


def _component(rel: str) -> str:
    """Which upgrade component a shipped file belongs to (P0.7).

    prompt_bundle = the LLM-facing contract (CLAUDE.md, skills, AGENTS.md); runtime =
    everything that is machine wiring (hooks, settings template).
    """
    if (
        "/skills/" in rel
        or "/commands/" in rel
        or "/agents/" in rel
        or rel in ("claude/CLAUDE.md", "opencode/AGENTS.md")
    ):
        return "prompt_bundle"
    return "runtime"


def _build() -> dict:
    sys.path.insert(0, str(REPO_ROOT))
    from config.settings import COMPONENT_VERSIONS, TOOL_VERSION

    files_meta = []
    for rel in _dist_files():
        text = (DIST_CONFIG / rel).read_text(encoding="utf-8")
        blob = text.encode("utf-8")
        files_meta.append(
            {
                "path": rel,
                "sha256": hashlib.sha256(blob).hexdigest(),
                "bytes": len(blob),
                "merge": _merge_kind(rel),
                "component": _component(rel),
            }
        )
    return {
        "version": TOOL_VERSION,
        "versions": dict(COMPONENT_VERSIONS),
        "files": files_meta,
        "targets": TARGETS,
    }


def main() -> int:
    check_only = "--check" in sys.argv[1:]
    built = json.dumps(_build(), indent=2) + "\n"

    if check_only:
        current = MANIFEST.read_text(encoding="utf-8") if MANIFEST.exists() else ""
        if current == built:
            print("[gen_manifest] OK — manifest in sync with dist/")
            return 0
        print(
            "[gen_manifest] STALE — manifest does not match dist/. Run: python tools/gen_manifest.py"
        )
        return 1

    MANIFEST.write_text(built, encoding="utf-8", newline="\n")
    print(f"[gen_manifest] wrote {MANIFEST.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
