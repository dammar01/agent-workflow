"""Every check in tests/checks/ must actually be reachable.

Written after `_test_agy_provider` shipped with ~230 lines of assertions and zero
callsites: it was absent from `SUITES` and absent from `run_tests()`, so `python
tests/run.py` never touched it. agy was the newest provider and the only one whose
tests were dead, which is the worst possible pairing — the suite reported success
while the least-proven code path was the one being skipped.

The drift is structural rather than careless. The suite names live in `tests/run.py`
and the execution order lives in `tests/scenario.py`, so adding a check means editing
two files that nothing forces to agree. This check is that force.
"""

import ast
from pathlib import Path

from tests.checks.support import assert_true

REPO_ROOT = Path(__file__).resolve().parents[2]
CHECKS_DIR = REPO_ROOT / "tests" / "checks"
RUN_PY = REPO_ROOT / "tests" / "run.py"
SCENARIO_PY = REPO_ROOT / "tests" / "scenario.py"

# This module's own check is registered in SUITES like any other; nothing here is exempt.
# `support.py` holds fakes rather than checks, so it defines no `_test_*` to begin with.
_IGNORED_FILES = {"__init__.py"}


def _defined_checks() -> dict[str, str]:
    """`_test_*` function name -> the file that defines it."""
    found: dict[str, str] = {}
    for path in sorted(CHECKS_DIR.glob("*.py")):
        if path.name in _IGNORED_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in tree.body:
            # Top-level only. A nested helper named `_test_` something is an
            # implementation detail of the check around it, not a check itself.
            if isinstance(node, ast.FunctionDef) and node.name.startswith("_test_"):
                found[node.name] = f"{path.name}:{node.lineno}"
    return found


def _calls_inside(path: Path, function: str) -> set[str]:
    """Every `name()` called within one named top-level function.

    Scoped to that function rather than the whole file on purpose. Scanning the module
    would count a call sitting in a helper nobody invokes, which is exactly the state
    this check exists to reject: the name is present, the check never runs, and the
    guard reports green.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    body = next(
        (
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == function
        ),
        None,
    )
    assert_true(
        body is not None,
        f"{path.name} defines no top-level `{function}()` — the entry point this check "
        "reads was renamed or moved, so it can no longer prove anything",
    )
    return {
        node.func.id
        for node in ast.walk(body)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _registered_in_suites() -> set[str]:
    """Names appearing as the callable in the SUITES dict of tests/run.py."""
    tree = ast.parse(RUN_PY.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        targets = getattr(node, "targets", []) or [getattr(node, "target", None)]
        named_suites = any(
            isinstance(target, ast.Name) and target.id == "SUITES"
            for target in targets
            if target is not None
        )
        if not named_suites or not isinstance(node.value, ast.Dict):
            continue
        return {
            element.id
            for entry in node.value.values
            if isinstance(entry, ast.Tuple)
            for element in entry.elts
            if isinstance(element, ast.Name)
        }
    return set()


def _test_every_check_is_registered() -> None:
    defined = _defined_checks()
    assert_true(
        defined,
        f"no _test_* functions found under {CHECKS_DIR} — the scan itself is broken, "
        "which would make this check pass for the wrong reason",
    )

    suites = _registered_in_suites()
    assert_true(
        suites,
        f"SUITES in {RUN_PY.name} parsed to nothing — the registry moved or changed "
        "shape, and this check would silently stop guarding anything",
    )
    executed = _calls_inside(SCENARIO_PY, "run_tests")

    # Two separate failures, reported separately: one means `--only <name>` cannot reach
    # the check, the other means a default `python tests/run.py` never runs it. A check
    # can be in SUITES and still be dead in CI, which is the case that got missed.
    unregistered = sorted(
        f"{name} ({defined[name]})" for name in defined if name not in suites
    )
    assert_true(
        not unregistered,
        "check(s) defined but absent from SUITES in tests/run.py, so `--only` cannot "
        "reach them:\n  " + "\n  ".join(unregistered),
    )

    uncalled = sorted(
        f"{name} ({defined[name]})" for name in defined if name not in executed
    )
    assert_true(
        not uncalled,
        "check(s) never called by run_tests() in tests/scenario.py — a default "
        "`python tests/run.py` runs `scenario` only, so these never execute in CI:\n  "
        + "\n  ".join(uncalled),
    )
