"""The runtime imports nothing it does not ship with.

This is the check that replaces a lockfile rather than the check that postpones one.
"No third-party dependencies" is what makes a missing requirements.txt correct instead
of careless — but it was only ever an observation someone made once, and a single
`import requests` added in good faith would have quietly converted this repo into one
that needs pinning, with nothing to say so. A constraints file listing nothing would
document the claim without testing it.

Scope is the shipped runtime. tests/ may import whatever it likes: a test dependency
does not travel to a user's machine, and pretending otherwise would just push real test
tooling out of reach for no gain.
"""

import ast
import sys
from pathlib import Path

from tests.checks.support import assert_true

REPO_ROOT = Path(__file__).resolve().parents[2]

# Everything a user actually runs. `tools/` is included because init/upgrade shell out to
# it, and `installer/` because it runs on the machine being set up.
_SHIPPED = ("main.py", "check.py", "core", "config", "adapters", "utils", "tools", "installer")

# First-party package roots. Absolute imports of these are internal, not external.
_INTERNAL = {
    "main", "check", "core", "config", "adapters", "utils", "tools", "installer", "tests",
}

_SKIP_PARTS = {"__pycache__", ".git", ".workflow", "graphify-out", "dist", "build"}


def _python_files() -> list[Path]:
    found: list[Path] = []
    for entry in _SHIPPED:
        target = REPO_ROOT / entry
        if target.is_file():
            found.append(target)
            continue
        for path in target.rglob("*.py"):
            if set(path.relative_to(REPO_ROOT).parts) & _SKIP_PARTS:
                continue
            found.append(path)
    return sorted(found)


def _imported_roots(path: Path) -> set[str]:
    """Top-level module names this file imports, by any import form."""
    roots: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".", 1)[0])
        elif isinstance(node, ast.ImportFrom):
            # A relative import resolves inside this repo by definition.
            if node.level:
                continue
            if node.module:
                roots.add(node.module.split(".", 1)[0])
    return roots


def _test_runtime_is_stdlib_only() -> None:
    files = _python_files()
    assert_true(
        len(files) > 20,
        f"only {len(files)} shipped Python files found under {REPO_ROOT} — the scan is "
        "broken, and a broken scan passes for the wrong reason",
    )

    stdlib = set(sys.stdlib_module_names)
    offenders: list[str] = []
    for path in files:
        for root in sorted(_imported_roots(path)):
            if root in stdlib or root in _INTERNAL:
                continue
            offenders.append(f"{path.relative_to(REPO_ROOT).as_posix()}: {root}")

    assert_true(
        not offenders,
        "shipped code imports a third-party module, so the runtime is no longer pure "
        "stdlib and the absence of a lockfile has stopped being correct:\n  "
        + "\n  ".join(offenders)
        + "\n\nEither drop the import or add a pinned dependency file and a CI install step.",
    )
