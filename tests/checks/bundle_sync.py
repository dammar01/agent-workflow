"""The bundle's two hand-maintained bijections must hold.

`tools/sync_skills.py` and `tools/sync_intent_map.py` were both written to be run "for
CI / doctor" — their own docstrings say so — and neither had a caller. `.github/workflows/
ci.yml` gates `stamp_version` and `gen_manifest` and nothing else, so a skill file added
without its registry entry, or an intent-map pattern added without the matching NL-map
trigger, merged green. `tools/e2e_installer.py` checks that skills and `intent-map.json`
are *installed*, not that they *agree* with CLAUDE.md, which reads like coverage without
being it.

This is the caller. It runs both tools through their real entry points rather than
re-implementing the comparison: a check that reimplements the tool proves the
reimplementation, and the tool is what CI and a maintainer actually invoke.

The tools write nothing under `--check`, so this stays a read-only check with no fixture.
"""

from __future__ import annotations

import contextlib
import io
import sys
from pathlib import Path

from tests.checks.support import assert_true

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def _run_check(module_name: str) -> tuple[int, str]:
    """Invoke one sync tool's `main()` under `--check`. Returns (exit code, its output).

    `main()` reads `sys.argv` and prints its report, both of which belong to a CLI rather
    than to a test. argv is swapped for the duration and stdout captured so the tool's own
    wording — including the `Fix:` line it prints — reaches the failure message instead of
    being swallowed. `SystemExit` is the tool's hard path (a source file missing entirely)
    and is reported as a failure rather than allowed to abort the run.
    """
    module = __import__(f"tools.{module_name}", fromlist=["main"])
    buffer = io.StringIO()
    original_argv = sys.argv
    sys.argv = [f"{module_name}.py", "--check"]
    try:
        with contextlib.redirect_stdout(buffer):
            code = module.main()
    except SystemExit as exc:
        return 1, f"{buffer.getvalue()}{exc}".strip()
    finally:
        sys.argv = original_argv
    return int(code), buffer.getvalue().strip()


def _test_bundle_registry_bijection() -> None:
    """skills/ <-> CLAUDE.md registry, and intent-map.json <-> CLAUDE.md NL-map."""
    # Reported one at a time. A skills mismatch and an intent-map drift have different
    # fixes, and collapsing them into one assertion would hide the second behind the first.
    for module_name, subject in (
        ("sync_skills", "skills/ and the CLAUDE.md command registry"),
        ("sync_intent_map", "intent-map.json and the CLAUDE.md NL-map"),
    ):
        code, output = _run_check(module_name)
        assert_true(
            code == 0,
            f"{subject} disagree — the runtime gate and the prompt would classify "
            f"commands differently.\n{output}\n"
            f"Run: python tools/{module_name}.py --check",
        )
