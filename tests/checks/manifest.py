"""`dist/manifest.json` must describe the dist/ tree that ships.

CI ran `gen_manifest --check` and the local suite did not, so a manifest left stale by an
in-repo edit to dist/ stayed green on the maintainer's machine and only failed after push.
This runs the tool's real entry point, the same way the stamp_version check does: a check
that reimplements the hashing would prove its own copy.
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


def _test_manifest_matches_dist() -> None:
    from tools.maintain import gen_manifest

    buffer = io.StringIO()
    original_argv = sys.argv
    sys.argv = ["gen_manifest.py", "--check"]
    try:
        with contextlib.redirect_stdout(buffer):
            code = gen_manifest.main()
    finally:
        sys.argv = original_argv
    assert_true(
        code == 0,
        f"dist/manifest.json is stale:\n{buffer.getvalue().strip()}\n"
        "Run: python tools/maintain/gen_manifest.py",
    )
