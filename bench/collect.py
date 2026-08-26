"""Finished unit records + harvested cost data -> `bench/ledger.jsonl`.

The ledger schema is `BENCHMARK-PLAN.md` §7. This file is its only writer;
`aggregate.py` is its only reader.

Two rules shape everything here.

**Missing is not zero.** `aggregate._spend` coerces a missing cost to `0.0`, which is
correct arithmetic and a terrible default for a harvester: an arm whose tokenburn export
never arrived would read as the cheapest arm in the study. So a row with no premium cost is
refused by default. `--allow-missing-cost` writes it anyway, with the gap named in
`premium_cost_source`, because there are honest reasons to want the non-cost columns early
— but it has to be asked for.

**The tokenburn shape is not guessed.** This harness has never inspected a real tokenburn
export. Rather than pattern-match hopefully and quietly produce nulls, `_index_sessions`
states the keys it looked for and prints the keys it actually found. A wrong guess that
runs is worse than a clear failure.

Run before `driver.py teardown`: arm C's worker numbers live in the worktree's `.workflow`,
and teardown removes it.

    python bench/collect.py --raw bench/raw --out bench/ledger.jsonl
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import policy  # noqa: E402
from driver import REQUIRED_UNIT_KEYS  # noqa: E402
from utils.path_guard import safe_path_component  # noqa: E402

BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent
UNITS_DIR = BENCH_DIR / "units"
DEFAULT_RAW_DIR = BENCH_DIR / "raw"
DEFAULT_LEDGER = BENCH_DIR / "ledger.jsonl"

STATUS_FINISHED = "finished"
STATUS_TORN_DOWN = "torn_down"
HARVESTABLE = (STATUS_FINISHED, STATUS_TORN_DOWN)

# Aliases accepted for the session identifier in a tokenburn export. Listed rather than
# sniffed so a shape change surfaces as "none of these keys were present, here is what was"
# instead of a row of silent nulls.
SESSION_KEYS = ("sessionId", "session_id", "session")

COST_KEYS = ("costUsd", "cost_usd", "cost")
TOKEN_KEYS = {
    "premium_input_tokens": ("inputTokens", "input_tokens", "input"),
    "premium_output_tokens": ("outputTokens", "output_tokens", "output"),
    "premium_cache_read_tokens": ("cacheReadTokens", "cache_read_tokens", "cacheRead"),
    "premium_cache_write_tokens": ("cacheWriteTokens", "cache_write_tokens", "cacheWrite"),
}

LEDGER_FIELDS = (
    "task_id", "arm", "repeat", "base_sha", "session_id", "worktree",
    "t_start", "t_first_submit", "t_accepted", "t_end",
    "premium_cache_read_tokens", "premium_cache_write_tokens",
    "premium_output_tokens", "premium_input_tokens", "premium_cost_usd",
    "worker_input_tokens", "worker_output_tokens", "worker_cost_usd", "worker_token_source",
    "delegated_calls", "evidence_reused_hits",
    "first_pass_accepted", "rework_cycles",
    "oracle_stage_failed", "verdict",
    "main_agent_rewrote", "files_touched",
    "scan_findings",
    # Extensions to the plan's §7 schema, added with W7. Each one answers a question the
    # original list left unanswerable from the ledger alone: whether the unit ran past its
    # cap, how long it took at all, and whether it was graded against a reduced gate.
    "unit_seconds", "timed_out", "quarantined_suites", "over_unit_budget",
)


class CollectError(RuntimeError):
    pass


def _load_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError as exc:
        raise CollectError(f"{path} is not readable JSON: {exc}")


def _first(mapping: dict, keys) -> object:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _rows_of(payload) -> list[dict]:
    """Accept the shapes a `--json` report plausibly takes, reject the rest loudly."""
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("sessions", "rows", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _index_sessions(raw_dir: Path) -> tuple[dict, list[str]]:
    """Map session id -> premium totals, from every report_*.json in raw_dir."""
    index: dict[str, dict] = {}
    notes: list[str] = []
    reports = sorted(raw_dir.glob("report_*.json"))
    if not reports:
        notes.append(f"no report_*.json under {raw_dir}; every premium column will be null")
        return index, notes

    for path in reports:
        rows = _rows_of(_load_json(path))
        if not rows:
            notes.append(f"{path.name}: no list of session objects found")
            continue
        matched = 0
        for row in rows:
            session = _first(row, SESSION_KEYS)
            if session is None:
                continue
            matched += 1
            entry = {"premium_cost_usd": _first(row, COST_KEYS)}
            for field, aliases in TOKEN_KEYS.items():
                entry[field] = _first(row, aliases)
            entry["_source_file"] = path.name
            index[str(session)] = entry
        if matched == 0:
            seen = sorted({key for row in rows[:5] for key in row})[:20]
            notes.append(
                f"{path.name}: none of {SESSION_KEYS} present. Keys seen: {seen}"
            )
    return index, notes


def _scan_findings(raw_dir: Path) -> tuple[dict, list[str]]:
    """Map session id -> list of tokenburn scan finding names."""
    findings: dict[str, list] = {}
    notes: list[str] = []
    for path in sorted(raw_dir.glob("scan_*.json")):
        rows = _rows_of(_load_json(path))
        if not rows:
            notes.append(f"{path.name}: no list of finding objects found")
            continue
        for row in rows:
            session = _first(row, SESSION_KEYS)
            if session is None:
                continue
            name = _first(row, ("rule", "name", "finding", "kind"))
            findings.setdefault(str(session), []).append(name)
    return findings, notes


def _worker_totals(record: dict) -> dict:
    """Arm C's worker side, from call.meta.json inside the unit's worktree."""
    if not record.get("workflow_installed"):
        return {
            "worker_input_tokens": None,
            "worker_output_tokens": None,
            "worker_cost_usd": None,
            "worker_token_source": "not_applicable",
        }
    worktree = Path(record.get("worktree") or "")
    # The runtime does not use the session id verbatim as a directory name: it passes it
    # through `safe_path_component` (core/workspace_paths.py:99), which appends a hash to
    # anything it considers non-portable — and any uppercase letter qualifies, so
    # `bench_T01_C_1` becomes `bench_T01_C_1--24cc82773233`. Looking for the raw name finds
    # nothing, forever, and reports it as "no logs" rather than as a bug.
    logs = (
        worktree
        / ".workflow"
        / "sessions"
        / safe_path_component(str(record.get("session_id")))
        / "logs"
    )
    if not logs.is_dir():
        return {
            "worker_input_tokens": None,
            "worker_output_tokens": None,
            "worker_cost_usd": None,
            # Named rather than blank: a torn-down worktree is a different situation from a
            # unit that made no delegated calls, and the two must not read alike.
            "worker_token_source": (
                "worktree_removed"
                if record.get("status") == STATUS_TORN_DOWN
                else "no_logs_found"
            ),
        }
    inp = out = 0
    seen = 0
    sources: set[str] = set()
    for meta_path in sorted(logs.glob("*/call.meta.json")):
        meta = _load_json(meta_path)
        if not isinstance(meta, dict):
            continue
        seen += 1
        # Field names taken from a real call.meta.json, not guessed: the runtime writes
        # `estimated_input_tokens` / `estimated_output_tokens` at the top level, with no
        # `usage` wrapper (core/executor.py:1001-1017). The earlier aliases matched nothing,
        # and `int(None or 0)` turned that into a confident zero.
        inp += int(_first(meta, ("estimated_input_tokens", "input_tokens")) or 0)
        out += int(_first(meta, ("estimated_output_tokens", "output_tokens")) or 0)
        sources.add(str(meta.get("token_source") or "estimated"))
    if seen == 0:
        return {
            "worker_input_tokens": None,
            "worker_output_tokens": None,
            "worker_cost_usd": None,
            "worker_token_source": "no_call_meta",
        }
    return {
        "worker_input_tokens": inp,
        "worker_output_tokens": out,
        # The opencode worker is free under the locked decisions (BENCHMARK-PLAN.md §2), so
        # zero here is a measurement, not a missing value. `worker_token_source` comes from
        # the runtime's own `token_source` field rather than being asserted here.
        "worker_cost_usd": 0.0,
        "worker_token_source": "+".join(sorted(sources)) if sources else "estimated",
    }


def _relative_worktree(value) -> str | None:
    if not value:
        return None
    path = Path(value)
    try:
        return path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        # Outside the repo: keep the leaf only rather than publishing the whole path.
        return path.name


def build_row(record: dict, premium: dict | None, scan: list | None) -> dict:
    row = {
        "task_id": record.get("task_id"),
        "arm": record.get("arm"),
        "repeat": record.get("repeat"),
        "base_sha": record.get("base_sha"),
        "session_id": record.get("session_id"),
        # Relative to the repo root, forward slashes. The unit record keeps the absolute
        # path because the harness needs it; the ledger is committed, and an absolute path
        # publishes the operator's directory layout to everyone who clones the repo. It
        # also makes two runs on two machines look like different data when they are not.
        "worktree": _relative_worktree(record.get("worktree")),
        "t_start": record.get("t_start"),
        "t_first_submit": record.get("t_first_submit"),
        "t_accepted": record.get("t_accepted"),
        "t_end": record.get("t_end"),
        "delegated_calls": record.get("delegated_calls"),
        "evidence_reused_hits": record.get("evidence_reused_hits"),
        "first_pass_accepted": record.get("first_pass_accepted"),
        "rework_cycles": record.get("rework_cycles"),
        "oracle_stage_failed": record.get("oracle_stage_failed"),
        "verdict": record.get("verdict"),
        "main_agent_rewrote": record.get("main_agent_rewrote"),
        "files_touched": record.get("files_touched"),
        "scan_findings": scan if scan is not None else [],
        "unit_seconds": record.get("unit_seconds"),
        "timed_out": record.get("timed_out"),
        # A unit graded with suites excluded is not comparable to one graded with all of
        # them. Carried per row so the report can say which, instead of a footnote saying
        # "some".
        "quarantined_suites": (record.get("last_oracle") or {}).get("quarantined", []),
    }
    if premium:
        row["premium_cost_usd"] = premium.get("premium_cost_usd")
        for field in TOKEN_KEYS:
            row[field] = premium.get(field)
        row["premium_cost_source"] = premium.get("_source_file")
    else:
        row["premium_cost_usd"] = None
        for field in TOKEN_KEYS:
            row[field] = None
        row["premium_cost_source"] = "missing"
    row.update(_worker_totals(record))
    row["over_unit_budget"] = policy.over_budget_unit(row["premium_cost_usd"])
    return row


def collect(raw_dir: Path, allow_missing_cost: bool) -> tuple[list[dict], list[str]]:
    if not UNITS_DIR.is_dir():
        raise CollectError(f"no unit records under {UNITS_DIR}")

    sessions, notes = _index_sessions(raw_dir)
    scans, scan_notes = _scan_findings(raw_dir)
    notes.extend(scan_notes)

    rows: list[dict] = []
    skipped: list[str] = []
    for path in sorted(UNITS_DIR.glob("*.json")):
        try:
            record = _load_json(path)
        except CollectError as exc:
            # One torn record must not cost the other 134. `_load_json` raises so that a
            # malformed *tokenburn export* is loud; a malformed unit record is a different
            # situation — it is one row, and the harvest can name it and carry on.
            notes.append(f"{path.name}: unreadable, skipped ({exc})")
            skipped.append(f"{path.stem} (unreadable)")
            continue
        if not isinstance(record, dict):
            notes.append(f"{path.name}: not a JSON object, skipped")
            skipped.append(f"{path.stem} (not an object)")
            continue
        absent = [key for key in REQUIRED_UNIT_KEYS if key not in record]
        if absent:
            # Every field here is read with .get(), so a half record would not crash — it
            # would harvest as a row of nulls. That is the worse failure: a row carrying an
            # arm but no verdict still counts in `per_arm`'s denominator and drags
            # first_pass_correctness and cost_per_accepted down for a unit nobody ran.
            notes.append(f"{path.name}: missing {', '.join(absent)}, skipped")
            skipped.append(f"{path.stem} (incomplete record)")
            continue
        status = record.get("status")
        if status not in HARVESTABLE:
            # A unit that never reached `finish` has null operator fields. Writing it would
            # put un-stamped rework and rewrite columns into the ledger as if observed.
            skipped.append(f"{record.get('unit_id')} ({status})")
            continue
        session_id = str(record.get("session_id"))
        row = build_row(record, sessions.get(session_id), scans.get(session_id))
        if row["premium_cost_usd"] is None and not allow_missing_cost:
            skipped.append(f"{record.get('unit_id')} (no premium cost for {session_id})")
            continue
        rows.append(row)

    if skipped:
        notes.append(f"skipped {len(skipped)} unit(s): {', '.join(skipped)}")
    return rows, notes


def write_ledger(rows: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, default=str) + "\n")


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="unit records -> ledger.jsonl")
    parser.add_argument("--raw", default=str(DEFAULT_RAW_DIR), help="tokenburn export dir")
    parser.add_argument("--out", default=str(DEFAULT_LEDGER))
    parser.add_argument(
        "--allow-missing-cost",
        action="store_true",
        help="write rows whose premium cost was never harvested (they aggregate as $0)",
    )
    parser.add_argument("--dry-run", action="store_true", help="report, write nothing")
    args = parser.parse_args()

    try:
        rows, notes = collect(Path(args.raw), args.allow_missing_cost)
    except CollectError as exc:
        print(f"collect: {exc}", file=sys.stderr)
        return 1

    for note in notes:
        print(f"  note: {note}")

    if not args.dry_run:
        write_ledger(rows, Path(args.out))
        print(f"wrote {len(rows)} row(s) to {args.out}")
    else:
        print(f"dry run: {len(rows)} row(s) would be written to {args.out}")

    over = [str(row["task_id"]) for row in rows if row.get("over_unit_budget")]
    if over:
        print(
            f"  WARNING: {len(over)} unit(s) over the ${policy.UNIT_BUDGET_USD:.2f} unit "
            f"budget: {', '.join(sorted(set(over)))}"
        )
    total = sum(float(row.get("premium_cost_usd") or 0.0) for row in rows)
    total += sum(float(row.get("worker_cost_usd") or 0.0) for row in rows)
    if policy.over_budget_run(total):
        print(
            f"  WARNING: harvested spend ${total:.2f} is over the ${policy.RUN_BUDGET_USD:.2f} "
            "run budget. Nothing was stopped — cost arrives after the fact, so this is a "
            "report, not a cutoff."
        )
    timed_out = [str(row["task_id"]) for row in rows if row.get("timed_out")]
    if timed_out:
        print(
            f"  WARNING: {len(timed_out)} unit(s) ran past the "
            f"{policy.UNIT_TIMEOUT_SECONDS}s cap: {', '.join(sorted(set(timed_out)))}"
        )
    quarantined = {name for row in rows for name in (row.get("quarantined_suites") or [])}
    if quarantined:
        print(
            f"  WARNING: stage 2 excluded {sorted(quarantined)} for some units; those rows "
            "were graded against a reduced acceptance gate."
        )

    missing = [
        str(row["task_id"]) for row in rows if row["premium_cost_usd"] is None
    ]
    if missing:
        print(
            f"  WARNING: {len(missing)} row(s) carry no premium cost and will aggregate "
            f"as $0: {', '.join(sorted(set(missing)))}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
