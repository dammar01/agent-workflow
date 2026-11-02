"""The real-use harness: verify tests split into run and reused, older rows kept unknown."""

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from tests.checks.support import assert_true


def _row(**fields) -> dict:
    return {"command": "verify", **fields}


def _test_real_use_verify_tests() -> None:
    from exoma.core.audit.transcript import project_slug
    from tools.maintain import measure_real_use as real_use

    rows = [
        _row(tests_requested=True, tests_run=4, tests_reused=1, tests_failed=1, tests_seconds=12.5),
        _row(tests_requested=True, tests_run=2, tests_reused=2, tests_failed=0, tests_seconds=0.0),
        _row(tests_requested=True, tests_run=3, tests_reused=0, tests_failed=0, tests_seconds=7.5),
        _row(tests_requested=True, tests_run=0, tests_reused=0, tests_failed=0, tests_seconds=0.0),
        # Before the field existed, and a row that recorded null for it.
        _row(tests_requested=True, tests_run=5, tests_failed=2, tests_seconds=30.0),
        _row(tests_requested=True, tests_run=1, tests_reused=None, tests_failed=0),
        # Recorded nothing at all; then pairs that cannot be read.
        _row(),
        _row(tests_requested=False, tests_run=1, tests_reused=2),
        _row(tests_requested="yes", tests_run=True, tests_reused=0, tests_seconds=-1),
        # Not /.verify: its test values must not count. A browser review is recorded as
        # `verify` with e2e_stage `run`, its draft as `verify-browser` with `draft`.
        {"command": "verify", "e2e_stage": "run", "tests_requested": True, "tests_run": 9,
         "tests_reused": 9, "tests_failed": 9, "tests_seconds": 99},
        {"command": "verify-browser", "e2e_stage": "draft", "tests_run": 9, "tests_reused": 9, "tests_seconds": 99},
        {"command": "explore", "tests_run": 9, "tests_reused": 0},
    ]
    report = real_use.verify_tests(rows)
    split = report["run_vs_reused"]
    assert_true(report["rows"] == 9, f"only /.verify rows are read, not a browser review: {report['rows']}")
    assert_true(
        split["rows_known"] == 4 and split["rows_unknown"] == 5
        and split["executed"] == 6 and split["reused"] == 3 and split["reused_share"] == 0.333,
        f"run = tests_run - tests_reused per valid row; older, null and invalid rows stay out: {split}",
    )
    assert_true(
        report["commands_run"] == {"total": 16, "rows_known": 7, "rows_unknown": 2},
        f"tests_run sums the rows that recorded it, a bool is not a count: {report['commands_run']}",
    )
    assert_true(
        report["commands_failed"] == {"total": 3, "rows_known": 6, "rows_unknown": 3}
        and report["seconds"] == {"total": 50.0, "rows_known": 5, "rows_unknown": 4},
        f"failures and seconds keep their own coverage; a negative time is unknown: {report}",
    )
    assert_true(
        report["requested"] == {"yes": 6, "no": 1, "unknown": 2},
        f"a request flag is yes, no or unknown, not a count: {report['requested']}",
    )

    legacy = real_use.verify_tests([_row(tests_requested=True, tests_run=5, tests_seconds=0.0), _row()])
    assert_true(
        legacy["run_vs_reused"] == {"rows_known": 0, "rows_unknown": 2, "executed": None,
                                    "reused": None, "reused_share": None},
        f"rows without tests_reused give no split, not zero reuse, even at 0 s: {legacy['run_vs_reused']}",
    )
    zero = real_use.verify_tests([_row(tests_run=0, tests_reused=0)])["run_vs_reused"]
    assert_true(
        zero["executed"] == 0 and zero["reused"] == 0 and zero["reused_share"] is None,
        f"a recorded zero is a known zero: {zero}",
    )
    empty = real_use.verify_tests([])
    assert_true(
        empty["commands_run"]["total"] is None and empty["run_vs_reused"]["executed"] is None,
        f"no verify rows gives null totals: {empty}",
    )

    # Through main: only rows bound to a transcript of the requested version count, and the
    # block reaches both stdout and the export.
    root = Path(tempfile.mkdtemp(prefix="aw-real-use-"))
    try:
        projects, transcripts = root / "projects", root / "transcripts"
        project = projects / "shop"
        (project / ".workflow" / "data").mkdir(parents=True)
        tdir = transcripts / project_slug(os.path.abspath(project))
        tdir.mkdir(parents=True)
        bound, other = "main_shop_1", "main_shop_2"
        (tdir / "t.jsonl").write_text(
            json.dumps({"type": "system", "content": f"Workflow Main Agent — v9.9.9 MAIN_SESSION_ID={bound}"},
                       ensure_ascii=False) + "\n"
            + json.dumps({"type": "user", "timestamp": "2026-11-01T10:00:00Z",
                          "message": {"role": "user", "content": "check it"}}) + "\n",
            encoding="utf-8",
        )
        (project / ".workflow" / "data" / "usage.jsonl").write_text(
            json.dumps(_row(session_id=bound, ok=True, tests_requested=True, tests_run=3, tests_reused=1)) + "\n"
            + json.dumps(_row(session_id=other, ok=True, tests_requested=True, tests_run=8, tests_reused=8)) + "\n",
            encoding="utf-8",
        )
        export = root / "real-use.json"
        argv, out = sys.argv, io.StringIO()
        sys.argv = ["measure_real_use.py", "--projects-root", str(projects), "--transcripts", str(transcripts),
                    "--version", "9.9.9", "--export", str(export)]
        try:
            with contextlib.redirect_stdout(out):
                code = real_use.main()
        finally:
            sys.argv = argv
        printed = json.loads(out.getvalue())["delegated_runtime"]["verify_tests"]
        exported = json.loads(export.read_text(encoding="utf-8"))["delegated_runtime"]["verify_tests"]
        assert_true(code == 0, f"main succeeds on one bound session: {code}")
        assert_true(
            printed["rows"] == 1 and printed["run_vs_reused"]["executed"] == 2
            and printed["run_vs_reused"]["reused"] == 1,
            f"an unbound session's row does not count: {printed}",
        )
        assert_true(exported == printed, "the export carries the same block the report prints")
    finally:
        shutil.rmtree(root, ignore_errors=True)
