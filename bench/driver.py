"""Per-unit harness: scaffold a worktree, judge it, stamp what only a human knows.

One unit is one (task, arm, repeat). This file owns the parts a machine can do the same
way every time — creating the worktree at the frozen base commit, proving the worktree
cannot see the answer commit, allocating a session id, taking the clock readings, running
the oracle, and removing the worktree afterwards.

It deliberately does NOT claim to run the agents.

`BENCHMARK-PLAN.md:223` defines arm C as a Claude session with `.workflow` installed, and
arms A and B as Claude sessions without it. Automating `main.py` alone would be the worker
half of arm C with no main agent above it — a different thing wearing the same label.
`delegate` is offered for the delegated calls of arm C because those genuinely repeat
identically; the session that decides when to make them is still driven by a person.

So the split is stated rather than implied:

    prepare   machine   worktree, leak check, session id, t_start
    (agent)   operator  the actual Claude session, in the worktree
    delegate  machine   optional: one `main.py` delegated call, arm C only
    judge     machine   oracle verdict, t_accepted, files_touched
    finish    operator  rework_cycles, main_agent_rewrote, t_end
    teardown  machine   worktree removed

Unit records land in `bench/units/<unit_id>.json`. `collect.py` turns finished ones into
ledger rows. A record that never reached `finish` is not a ledger row: a unit whose
operator-stamped fields were never filled would otherwise arrive as zeros, and zero rework
on an abandoned unit is a claim nobody made.
"""

import json
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import oracle  # noqa: E402
import policy  # noqa: E402
from corpus import verify_no_leak  # noqa: E402

BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent
CORPUS_PATH = BENCH_DIR / "corpus.json"
UNITS_DIR = BENCH_DIR / "units"
WORKTREES_DIR = BENCH_DIR / "worktrees"

ARMS = ("A", "B", "C")

# task_id becomes a directory name, a unit-record filename, and a session id. Validated
# rather than sanitised: `utils.path_guard.safe_path_component` would rewrite `T01` to
# `T01--<hash>` (it treats any uppercase as non-portable), and silently renaming an
# operator's hand-written task id is worse than telling them it is not usable. The corpus
# is authored by hand, so a clear rejection is the right answer.
_SAFE_TASK_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

STATUS_PREPARING = "preparing"
STATUS_PREPARED = "prepared"
STATUS_JUDGED = "judged"
STATUS_FINISHED = "finished"
STATUS_TORN_DOWN = "torn_down"

# Arms A and B run without `.workflow` in the worktree (BENCHMARK-PLAN.md:222). Installing
# it to make the driver uniform would change what those arms are.
WORKFLOW_ARMS = ("C",)


class DriverError(RuntimeError):
    """Something the operator has to fix before the unit can continue."""


def _git(args: list[str], cwd: Path = REPO_ROOT) -> str:
    done = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True
    )
    if done.returncode != 0:
        raise DriverError(f"git {' '.join(args)} failed: {(done.stderr or '').strip()}")
    return done.stdout


def check_task_id(task_id: str) -> str:
    """Reject a task id that would not survive being used as a path component."""
    if not _SAFE_TASK_ID.match(str(task_id or "")):
        raise DriverError(
            f"task_id {task_id!r} is not usable: allow letters, digits, underscore and "
            "hyphen only, starting with a letter or digit, at most 64 characters. It "
            "becomes a directory name, a file name, and a session id."
        )
    return task_id


def unit_id(task_id: str, arm: str, repeat: int) -> str:
    return f"{check_task_id(task_id)}_{arm}_{repeat}"


def _contained(path: Path, parent: Path) -> Path:
    """Resolve `path` and prove it stays under `parent`.

    Second line of defence behind `check_task_id`. The id check is what should catch a bad
    value; this is what catches the day someone adds a new way to build these paths and
    forgets the first check.
    """
    resolved = path.resolve()
    if parent.resolve() not in resolved.parents:
        raise DriverError(f"refusing to touch {resolved}: outside {parent.resolve()}")
    return resolved


def unit_path(uid: str) -> Path:
    return _contained(UNITS_DIR / f"{uid}.json", UNITS_DIR)


# Keys every phase after `prepare` reads. Checked on load so a half-written record fails
# with a sentence instead of a KeyError traceback three frames deep: valid JSON missing a
# key is not a bug report the operator can act on unless it says which key.
REQUIRED_UNIT_KEYS = (
    "unit_id", "task_id", "arm", "repeat", "worktree", "session_id",
    "workflow_installed", "status", "t_start",
)


def load_unit(uid: str) -> dict:
    path = unit_path(uid)
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DriverError(f"no unit record at {path} — run `prepare` first")
    except ValueError as exc:
        raise DriverError(f"unit record at {path} is not readable JSON: {exc}")
    if not isinstance(record, dict):
        raise DriverError(f"unit record at {path} is not a JSON object")
    missing = [key for key in REQUIRED_UNIT_KEYS if key not in record]
    if missing:
        raise DriverError(
            f"unit record at {path} is missing {', '.join(missing)}. It was not written by "
            "`prepare`, or it was edited by hand."
        )
    return record


def save_unit(record: dict) -> Path:
    UNITS_DIR.mkdir(parents=True, exist_ok=True)
    path = unit_path(record["unit_id"])
    path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return path


def load_corpus() -> list[dict]:
    try:
        entries = json.loads(CORPUS_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise DriverError(
            f"no corpus at {CORPUS_PATH} — run `python bench/corpus.py --write`, then fill "
            "prompt and oracle_tests by hand"
        )
    except ValueError as exc:
        raise DriverError(f"corpus is not readable JSON: {exc}")
    if not isinstance(entries, list):
        raise DriverError("corpus must be a list of entries")
    return entries


def find_task(task_id: str) -> dict:
    for entry in load_corpus():
        if entry.get("task_id") == task_id:
            if not (entry.get("prompt") or "").strip():
                raise DriverError(
                    f"task {task_id} has an empty prompt. The corpus generator leaves it "
                    "blank on purpose; fill it by hand before running the unit."
                )
            return entry
    raise DriverError(f"task {task_id} is not in {CORPUS_PATH}")


def _now() -> float:
    return time.time()


def prepare(task_id: str, arm: str, repeat: int) -> dict:
    """Create the worktree and open the unit record. Machine half, start of unit."""
    if arm not in ARMS:
        raise DriverError(f"arm must be one of {ARMS}, got {arm!r}")
    if repeat < 1:
        # Repeats are 1-based and appear in the unit id. A negative one mints a unit named
        # `T01_A_-1`, which sorts oddly and reads as a mistake nobody made on purpose.
        raise DriverError(f"repeat must be 1 or greater, got {repeat}")
    task = find_task(task_id)
    uid = unit_id(task_id, arm, repeat)

    if unit_path(uid).exists():
        raise DriverError(
            f"unit {uid} already has a record. Remove it deliberately if you mean to "
            "re-run: overwriting silently would drop the previous attempt's timings."
        )

    WORKTREES_DIR.mkdir(parents=True, exist_ok=True)
    worktree = _contained(WORKTREES_DIR / uid, WORKTREES_DIR)
    if worktree.exists():
        raise DriverError(f"worktree already exists at {worktree}")

    base_sha = task["base_sha"]

    record = {
        "unit_id": uid,
        "task_id": task_id,
        "arm": arm,
        "repeat": repeat,
        "base_sha": base_sha,
        "answer_sha": task["answer_sha"],
        "worktree": str(worktree),
        # One session id per unit. Costs are attributed by session downstream, so two units
        # sharing an id are two units whose spend can no longer be separated.
        "session_id": f"bench_{uid}",
        "workflow_installed": arm in WORKFLOW_ARMS,
        "prompt": task["prompt"],
        "oracle_tests": task.get("oracle_tests") or [],
        "t_start": _now(),
        # Snapshotted, not looked up later. A unit judged under one set of limits and read
        # back under another is a unit whose result cannot be interpreted.
        "policy": policy.summary(),
        "timed_out": False,
        "unit_seconds": None,
        "t_first_submit": None,
        "t_accepted": None,
        "t_end": None,
        "delegated_calls": 0,
        "evidence_reused_hits": 0,
        "delegated_log": [],
        "verdict": None,
        "oracle_stage_failed": None,
        "first_pass_accepted": None,
        "judge_runs": 0,
        "rework_cycles": None,
        "main_agent_rewrote": None,
        "files_touched": None,
        "status": STATUS_PREPARING,
    }
    # The record is written BEFORE the worktree exists, on purpose. The other order leaves
    # an orphan on any interruption between `worktree add` and the first save: a directory
    # nothing points at, which `list` cannot show and `teardown` cannot reach. This way the
    # worst interruption leaves a record marked `preparing`, which names its own worktree.
    save_unit(record)

    try:
        _git(["worktree", "add", "--detach", str(worktree), base_sha])
    except DriverError:
        unit_path(uid).unlink(missing_ok=True)
        raise

    leak = verify_no_leak(worktree, task["answer_sha"])
    if not leak["ok"]:
        # Tear the worktree back down rather than leaving a poisoned one on disk for
        # someone to pick up later without re-reading this check.
        _git(["worktree", "remove", "--force", str(worktree)])
        unit_path(uid).unlink(missing_ok=True)
        raise DriverError(
            f"unit {uid} aborted: {leak['reason'] or 'leak check failed'}"
        )

    if arm in WORKFLOW_ARMS:
        # Arm C is "a Claude session WITH `.workflow` installed" (BENCHMARK-PLAN.md:223).
        # A fresh worktree has none, and the first delegated call dies on
        # `.workflow/config.json` before anything can lazily create it — so every arm C unit
        # would fail in two seconds. Installing it here is what makes the arm the arm; the
        # flag below then records what happened rather than asserting it.
        init = subprocess.run(
            [sys.executable, str(REPO_ROOT / "main.py"), "--command", "init",
             "--work-dir", str(worktree)],
            cwd=str(REPO_ROOT), capture_output=True, text=True,
        )
        installed = (worktree / ".workflow" / "config.json").exists()
        record["workflow_installed"] = installed
        record["init_returncode"] = init.returncode
        if not installed:
            _git(["worktree", "remove", "--force", str(worktree)])
            unit_path(uid).unlink(missing_ok=True)
            raise DriverError(
                f"unit {uid}: `init` left no .workflow/config.json in {worktree} "
                f"(rc={init.returncode}). {(init.stderr or init.stdout or '')[-300:]}"
            )

    record["status"] = STATUS_PREPARED
    record["t_start"] = _now()
    save_unit(record)
    return record


def delegate(uid: str, command: str, prompt: str | None = None) -> dict:
    """One delegated `main.py` call inside the unit's worktree. Arm C only."""
    record = load_unit(uid)
    if record["arm"] not in WORKFLOW_ARMS:
        raise DriverError(
            f"unit {uid} is arm {record['arm']}, which runs without `.workflow` "
            f"(BENCHMARK-PLAN.md:222). Delegated calls belong to arm(s) {WORKFLOW_ARMS}."
        )
    if record["status"] not in (STATUS_PREPARED, STATUS_JUDGED):
        raise DriverError(f"unit {uid} is {record['status']}; nothing more to delegate")
    if record["delegated_calls"] >= policy.MAX_DELEGATED_CALLS:
        raise DriverError(
            f"unit {uid} already made {record['delegated_calls']} delegated call(s), the "
            f"cap is {policy.MAX_DELEGATED_CALLS} (bench/policy.py). A session looping on "
            "the second agent charges the arm for the loop."
        )
    if policy.over_time(_now() - record["t_start"]):
        raise DriverError(
            f"unit {uid} passed its {policy.UNIT_TIMEOUT_SECONDS}s deadline; run `judge` "
            "and close it rather than spending more on it."
        )

    # `--command await --job-command X`, not `--command X`. explore/plan/analyze/verify are
    # BACKGROUND_COMMANDS (main.py:51): calling them directly dispatches `submit()`, which
    # returns `{ok: true, status: "submitted", job_id: ...}` the instant the worker is
    # spawned. That reply has no `evidence_ref` and its `ok` describes the submission, not
    # the work — so the unit would record a successful delegated call with zero reuse hits
    # no matter what the worker actually did. `await` blocks and returns the real result.
    remaining = int(
        max(60.0, policy.UNIT_TIMEOUT_SECONDS - (_now() - record["t_start"]))
    )
    args = [
        sys.executable,
        str(REPO_ROOT / "main.py"),
        "--command",
        "await",
        "--job-command",
        command,
        "--poll-timeout",
        str(remaining),
        "--prompt",
        prompt if prompt is not None else record["prompt"],
        # The unit's own session id, NOT --fresh-session. A generated id would be unknown
        # to the harvester, and the unit's premium cost would land under a session nobody
        # can map back to this row.
        "--session",
        record["session_id"],
        "--work-dir",
        record["worktree"],
    ]
    started = _now()
    done = subprocess.run(args, cwd=str(REPO_ROOT), capture_output=True, text=True)
    finished = _now()

    payload: dict = {}
    try:
        payload = json.loads(done.stdout or "{}")
    except ValueError:
        payload = {}

    reused = bool((payload.get("evidence_ref") or {}).get("reused"))
    call = {
        "command": command,
        "returncode": done.returncode,
        "ok": bool(payload.get("ok")) if payload else False,
        "started": started,
        "finished": finished,
        "seconds": round(finished - started, 3),
        "evidence_reused": reused,
        "job_id": (payload.get("meta") or {}).get("job_id"),
        "error_type": (payload.get("meta") or {}).get("error_type"),
        # Kept when the CLI printed something that was not JSON: an unparseable reply is a
        # fact about the run, and discarding it would make the failure look like silence.
        "stdout_tail": None if payload else (done.stdout or "")[-800:],
        "stderr_tail": (done.stderr or "")[-400:] or None,
    }
    record["delegated_log"].append(call)
    record["delegated_calls"] = len(record["delegated_log"])
    record["evidence_reused_hits"] = sum(
        1 for entry in record["delegated_log"] if entry["evidence_reused"]
    )
    if record["t_first_submit"] is None:
        record["t_first_submit"] = started
    save_unit(record)
    return call


def _files_touched(worktree: Path) -> int:
    out = _git(["status", "--porcelain"], cwd=worktree)
    return len([line for line in out.splitlines() if line.strip()])


def judge(uid: str) -> dict:
    """Run the frozen oracle over the worktree and stamp the verdict."""
    record = load_unit(uid)
    if record["status"] == STATUS_PREPARING:
        raise DriverError(
            f"unit {uid} never finished preparing; its worktree may not exist. Run "
            "`teardown --force` and prepare it again."
        )
    worktree = Path(record["worktree"])
    if not worktree.exists():
        raise DriverError(f"worktree {worktree} is gone; cannot judge unit {uid}")

    task = {"task_id": record["task_id"], "oracle_tests": record["oracle_tests"]}
    result = oracle.judge(worktree, task)

    record["judge_runs"] += 1
    record["unit_seconds"] = round(_now() - record["t_start"], 1)
    record["timed_out"] = policy.over_time(record["unit_seconds"])
    record["verdict"] = result["verdict"]
    record["oracle_stage_failed"] = result["failed_at"]
    record["files_touched"] = _files_touched(worktree)
    if result["accepted"]:
        record["t_accepted"] = _now()
        # First pass means accepted on the first judging, not accepted at all. A unit the
        # operator sent back and re-judged is by definition not first-pass, and the
        # distinction is the whole point of the metric.
        if record["first_pass_accepted"] is None:
            record["first_pass_accepted"] = record["judge_runs"] == 1
    elif record["first_pass_accepted"] is None and record["judge_runs"] == 1:
        record["first_pass_accepted"] = False
    record["status"] = STATUS_JUDGED
    record["last_oracle"] = {
        "verdict": result["verdict"],
        "failed_at": result["failed_at"],
        "stages_not_run": result["stages_not_run"],
        # Which suites stage 2 skipped, if any. Empty for an unquarantined run.
        "quarantined": (result["stages"].get(oracle.STAGE_SUITE) or {}).get(
            "quarantined", []
        ),
    }
    save_unit(record)
    return result


def finish(uid: str, rework_cycles: int, main_agent_rewrote: bool) -> dict:
    """Stamp the two fields only the operator saw, and close the unit.

    `main_agent_rewrote` is stamped, never inferred from the diff — the plan says so
    (§7) and it is the honest reading: whether the main agent rewrote the delegate's work
    is a fact about the session, not a shape in the final patch.
    """
    record = load_unit(uid)
    if record["status"] in (STATUS_PREPARING, STATUS_PREPARED):
        raise DriverError(f"unit {uid} was never judged — run `judge` first")
    if rework_cycles < 0:
        raise DriverError("rework_cycles cannot be negative")
    if rework_cycles > policy.MAX_REWORK_CYCLES:
        raise DriverError(
            f"rework_cycles={rework_cycles} exceeds the cap of "
            f"{policy.MAX_REWORK_CYCLES} (bench/policy.py). Reworking until an arm passes "
            "erases the difference the study is measuring; close the unit at its cap and "
            "let the verdict stand."
        )
    record["rework_cycles"] = rework_cycles
    record["main_agent_rewrote"] = bool(main_agent_rewrote)
    record["t_end"] = _now()
    record["unit_seconds"] = round(record["t_end"] - record["t_start"], 1)
    record["timed_out"] = policy.over_time(record["unit_seconds"])
    record["status"] = STATUS_FINISHED
    save_unit(record)
    return record


def teardown(uid: str, force: bool = False) -> dict:
    record = load_unit(uid)
    if record["status"] != STATUS_FINISHED and not force:
        raise DriverError(
            f"unit {uid} is {record['status']}, not finished. Tearing the worktree down "
            "now would destroy state the harvester still needs; pass --force if that is "
            "what you mean."
        )
    worktree = Path(record["worktree"])
    if worktree.exists():
        _git(["worktree", "remove", "--force", str(worktree)])
    record["status"] = STATUS_TORN_DOWN
    save_unit(record)
    return record


def _print(payload) -> None:
    print(json.dumps(payload, indent=2, default=str))


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="benchmark unit driver")
    sub = parser.add_subparsers(dest="phase", required=True)

    p_prepare = sub.add_parser("prepare", help="create the worktree and open the unit")
    p_prepare.add_argument("--task", required=True)
    p_prepare.add_argument("--arm", required=True, choices=list(ARMS))
    p_prepare.add_argument("--repeat", type=int, default=1)

    p_delegate = sub.add_parser("delegate", help="one main.py delegated call (arm C)")
    p_delegate.add_argument("--unit", required=True)
    p_delegate.add_argument(
        "--command", default="explore", choices=["explore", "plan", "analyze", "verify"]
    )
    p_delegate.add_argument("--prompt", default=None, help="override the corpus prompt")

    p_judge = sub.add_parser("judge", help="run the frozen oracle over the worktree")
    p_judge.add_argument("--unit", required=True)

    p_finish = sub.add_parser("finish", help="stamp the operator-observed fields")
    p_finish.add_argument("--unit", required=True)
    p_finish.add_argument("--rework-cycles", type=int, required=True)
    p_finish.add_argument("--main-agent-rewrote", action="store_true")

    p_teardown = sub.add_parser("teardown", help="remove the unit's worktree")
    p_teardown.add_argument("--unit", required=True)
    p_teardown.add_argument("--force", action="store_true")

    sub.add_parser("list", help="show every unit record and its status")

    args = parser.parse_args()
    try:
        if args.phase == "prepare":
            record = prepare(args.task, args.arm, args.repeat)
            _print(
                {
                    "unit_id": record["unit_id"],
                    "worktree": record["worktree"],
                    "session_id": record["session_id"],
                    "workflow_installed": record["workflow_installed"],
                    "next": "run the agent session in the worktree, then `judge`",
                }
            )
        elif args.phase == "delegate":
            _print(delegate(args.unit, args.command, args.prompt))
        elif args.phase == "judge":
            result = judge(args.unit)
            _print(
                {
                    "verdict": result["verdict"],
                    "failed_at": result["failed_at"],
                    "stages_not_run": result["stages_not_run"],
                }
            )
        elif args.phase == "finish":
            record = finish(args.unit, args.rework_cycles, args.main_agent_rewrote)
            _print({"unit_id": record["unit_id"], "status": record["status"]})
        elif args.phase == "teardown":
            record = teardown(args.unit, args.force)
            _print({"unit_id": record["unit_id"], "status": record["status"]})
        elif args.phase == "list":
            rows = []
            for path in sorted(UNITS_DIR.glob("*.json")):
                try:
                    entry = json.loads(path.read_text(encoding="utf-8"))
                except ValueError:
                    rows.append({"unit_id": path.stem, "status": "UNREADABLE"})
                    continue
                rows.append(
                    {
                        "unit_id": entry.get("unit_id"),
                        "arm": entry.get("arm"),
                        "status": entry.get("status"),
                        "verdict": entry.get("verdict"),
                    }
                )
            _print(rows)
    except DriverError as exc:
        print(f"driver: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
