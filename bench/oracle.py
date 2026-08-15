"""The frozen verdict function for one benchmark unit.

Frozen means what it says: this file must not change once the first unit has been
harvested. An oracle tuned after seeing results is not an oracle, and the whole
quality-adjusted claim rests on `accepted` meaning the same thing in every arm.

Stage order is the plan's (BENCHMARK-PLAN.md §6, Fase 3) and it stops at the first
failure. Ordering is deliberate rather than incidental: syntax before tests because a
file that will not parse makes every later stage report a failure it did not cause, and
the harness needs to know which stage the unit actually died at.

`not_checked` and `skipped` are NOT passes. That is the single rule most likely to be
softened by accident later, so it is enforced here rather than trusted to a caller: a
stage that did not run leaves the verdict `incomplete`, which is not accepted.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

STAGE_SYNTAX = "syntax"
STAGE_SUITE = "suite"
STAGE_TASK_TESTS = "task_tests"
STAGE_CHECKS = "checks"
STAGES = (STAGE_SYNTAX, STAGE_SUITE, STAGE_TASK_TESTS, STAGE_CHECKS)

VERDICT_ACCEPTED = "accepted"
VERDICT_REJECTED = "rejected"
VERDICT_INCOMPLETE = "incomplete"


def _run(args: list[str], cwd: Path, timeout: int = 900) -> dict:
    try:
        done = subprocess.run(
            args, cwd=str(cwd), capture_output=True, text=True, timeout=timeout
        )
    except subprocess.TimeoutExpired:
        return {"ran": True, "ok": False, "reason": f"timeout after {timeout}s"}
    except OSError as exc:
        # Could not even start. Distinct from a failing command: nothing was judged,
        # so this must not be allowed to read as a rejection.
        return {"ran": False, "ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    return {
        "ran": True,
        "ok": done.returncode == 0,
        "returncode": done.returncode,
        "stderr_tail": (done.stderr or "")[-400:],
    }


def _syntax(worktree: Path) -> dict:
    """Every Python file in the worktree still parses."""
    bad: list[str] = []
    for path in sorted(worktree.rglob("*.py")):
        parts = set(path.relative_to(worktree).parts)
        if parts & {"__pycache__", ".git", "bench", ".workflow"}:
            continue
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
        except (SyntaxError, ValueError) as exc:
            bad.append(f"{path.relative_to(worktree).as_posix()}: {exc}")
        except OSError as exc:
            return {"ran": False, "ok": False, "reason": f"unreadable: {exc}"}
    return {"ran": True, "ok": not bad, "failures": bad[:10]}


def judge(worktree: str | Path, task: dict) -> dict:
    """Run the four stages in order and return the unit's verdict.

    `task` is one corpus entry; `oracle_tests` may be empty, in which case the
    task-specific stage is honestly reported as not run — and the verdict is capped at
    `incomplete`, never promoted to accepted on the strength of the stages that did run.
    """
    root = Path(worktree)
    stages: dict[str, dict] = {}

    stages[STAGE_SYNTAX] = _syntax(root)
    if not stages[STAGE_SYNTAX]["ok"]:
        return _verdict(stages, STAGE_SYNTAX)

    stages[STAGE_SUITE] = _run([sys.executable, "tests/run.py"], root)
    if not stages[STAGE_SUITE]["ok"]:
        return _verdict(stages, STAGE_SUITE)

    selected = task.get("oracle_tests") or []
    if not selected:
        stages[STAGE_TASK_TESTS] = {"ran": False, "ok": False, "reason": "no oracle_tests in corpus entry"}
        return _verdict(stages, None)
    for command in selected:
        outcome = _run([sys.executable, *str(command).split()], root)
        stages[STAGE_TASK_TESTS] = outcome
        if not outcome["ok"]:
            return _verdict(stages, STAGE_TASK_TESTS)

    stages[STAGE_CHECKS] = _run(
        [sys.executable, "tests/run.py", "--only", "registry", "--only", "redaction",
         "--only", "governance", "--only", "contracts", "--keep-going"],
        root,
    )
    if not stages[STAGE_CHECKS]["ok"]:
        return _verdict(stages, STAGE_CHECKS)
    return _verdict(stages, None)


def _verdict(stages: dict, failed_at: str | None) -> dict:
    unrun = [name for name in STAGES if not stages.get(name, {}).get("ran")]
    if failed_at:
        verdict = VERDICT_REJECTED
    elif unrun:
        # Every stage that DID run passed, and at least one never ran. That is not a pass:
        # promoting it would let a unit be accepted on the strength of checks nobody
        # performed, which is exactly how a benchmark starts flattering its subject.
        verdict = VERDICT_INCOMPLETE
    else:
        verdict = VERDICT_ACCEPTED
    return {
        "verdict": verdict,
        "accepted": verdict == VERDICT_ACCEPTED,
        "failed_at": failed_at,
        "stages_not_run": unrun,
        "stages": stages,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="benchmark oracle for one unit")
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--task", required=True, help="path to a single corpus entry JSON")
    args = parser.parse_args()
    entry = json.loads(Path(args.task).read_text(encoding="utf-8"))
    print(json.dumps(judge(args.worktree, entry), indent=2))
