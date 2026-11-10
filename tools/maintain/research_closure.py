"""Isolated, opt-in evidence for the November research-closure review.

This stages the pinned Exoma; it never patches it. Browser/graph/state fixtures are
designed checks, not maintainer direct-use runs. Importing this module does no work.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import http.server
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[2]
OUTPUT = REPO / "docs/research-drafts/_data/research-closure-2026-11-10"
GROUPS = {
    "browser": ["DEC-004"], "repeat": ["DEC-005", "DEC-008"],
    "redaction": ["DEC-009"], "graph": ["DEC-012"], "guards": ["DEC-013"],
    "research": ["DEC-017", "DEC-019"], "parser": ["DEC-018"],
}


class Blocked(RuntimeError):
    """An explicitly identified environment prerequisite is unavailable."""


def confined(path: Path, parent: Path = OUTPUT) -> Path:
    resolved, boundary = path.resolve(), parent.resolve()
    if not resolved.is_relative_to(boundary):
        raise ValueError(f"output outside research fixture: {resolved}")
    return resolved


def save(path: Path, value) -> None:
    path = confined(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def aggregate(cases: list[dict], expected: list[str]) -> dict:
    """Missing, duplicate and unknown outcomes cannot silently become a pass."""
    ids = [case.get("case_id") for case in cases]
    valid = {"pass", "fail", "blocked", "not_run"}
    defects = [f"missing:{name}" for name in expected if name not in ids]
    defects += [f"unexpected:{name}" for name in ids if name not in expected]
    defects += [f"duplicate:{name}" for name in set(ids) if ids.count(name) > 1]
    for case in cases:
        if case.get("status") not in valid:
            defects.append(f"invalid-status:{case.get('case_id')}")
        if not case.get("evidence_kind") or not case.get("provenance"):
            defects.append(f"missing-provenance:{case.get('case_id')}")
        if case.get("status") == "pass" and (
            not case.get("checks") or any(not c.get("passed") for c in case["checks"])
        ):
            defects.append(f"unsupported-pass:{case.get('case_id')}")
    counts = {status: sum(c.get("status") == status for c in cases) for status in sorted(valid)}
    status = "fail" if defects or counts["fail"] else (
        "incomplete" if counts["blocked"] or counts["not_run"] else "pass"
    )
    return {"status": status, "counts": counts, "integrity_errors": defects}


class Case:
    def __init__(self, name: str, root: Path):
        self.name, self.root = name, confined(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.checks: list[dict] = []

    def check(self, name: str, condition: bool, actual=None) -> None:
        self.checks.append({"name": name, "passed": bool(condition), "actual": actual})

    def write(self, name: str, value) -> None:
        save(self.root / name, value)


def hidden() -> dict:
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def child_environment(root: Path) -> dict:
    root = confined(root)
    env = dict(os.environ)
    for name in ("home", "tmp", "uv-cache"):
        (root / name).mkdir(parents=True, exist_ok=True)
    env.update({
        "EXOMA_HOME": str(root / "home"), "TMP": str(root / "tmp"),
        "TEMP": str(root / "tmp"), "TMPDIR": str(root / "tmp"),
        "UV_CACHE_DIR": str(root / "uv-cache"), "PYTHONDONTWRITEBYTECODE": "1",
        "UV_NO_SYNC": "1", "PYTHONUTF8": "1",
        "PYTHONPATH": os.pathsep.join([str(REPO / ".venv/Lib/site-packages"), str(REPO)]),
    })
    # An inherited simulation switch must not turn a real browser check into a fake.
    env.pop("EXOMA_E2E_FAKE", None)
    env.pop("WORKFLOW_E2E_FAKE", None)
    return env


def case_browser(c: Case) -> None:
    from exoma.core.evidence.e2e import browser, request, spec
    from exoma.core.evidence.e2e.classify import build_report

    html = b'''<!doctype html><meta charset="utf-8"><title>Closure fixture</title>
<div data-e2e="target" onclick="document.querySelector('[data-e2e=result]').textContent='clicked'">Activate</div>
<div data-e2e="result">idle</div><label for="label-control">Control</label><input id="label-control">
<span data-e2e="duplicate">a</span><span data-e2e="duplicate">b</span>'''
    (c.root / "target.html").write_bytes(html)

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(html)

        def log_message(self, *_args):
            pass

    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except OSError as exc:
        raise Blocked(f"loopback bind: {exc}") from exc
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = request.default_settings()
    config.update(base_url=f"http://127.0.0.1:{server.server_port}", headless=True,
                  step_timeout_ms=1000, navigation_timeout_ms=5000)
    provenance = {"type": "heuristic", "reason": "deliberate local research fixture"}

    def candidate(selector):
        return {"selector": selector, "selector_provenance": provenance}

    scenario = {
        "version": 1, "claims": [{"id": "e2e-action", "source_refs": ["req:closure"], "severity": "blocking"}],
        "steps": [
            {"id": "open", "action": "goto", "url": "/"},
            {"id": "activate", "action": "click", "selector_candidates": [
                candidate({"role": "button", "name": "Absent role"}),
                candidate({"label": "Absent label"}), candidate({"e2e": "target"})]},
            {"id": "result", "action": "expect_dom", "claim_id": "e2e-action",
             "selector": {"e2e": "result"}, "selector_provenance": provenance, "text": "clicked"},
        ],
    }
    c.write("scenario.json", scenario)
    c.write("config.json", config)
    errors = spec.validate_scenario(scenario, config)
    c.check("scenario is valid", not errors, errors)

    def drive(name, value):
        events: list[dict] = []
        code = browser.run_scenario(value, config, str(c.root / name), events.append)
        c.write(f"{name}-events.json", events)
        report = build_report(events, value)
        c.write(f"{name}-report.json", report)
        return code, events, report

    try:
        if errors:
            return
        code, events, report = drive("fallback", scenario)
        selections = [e for e in events if e.get("step_id") == "activate" and e.get("selection")]
        c.check("real browser verdict passes", code == 0 and report.get("browser_verdict") == "pass", report)
        c.check("e2e fallback is the candidate actually used", any(
            e["selection"].get("candidate") == 2 and e["selection"].get("fallback_used") is True
            for e in selections), selections)
        for name, selector, expected in (
            ("missing", {"e2e": "absent"}, "selector_missing"),
            ("ambiguous", {"e2e": "duplicate"}, "selector_ambiguous"),
        ):
            changed = copy.deepcopy(scenario)
            changed["steps"][1].pop("selector_candidates")
            changed["steps"][1].update(selector=selector, selector_provenance=provenance)
            bad_code, bad_events, bad_report = drive(name, changed)
            kinds = [(e.get("error") or {}).get("kind") for e in bad_events]
            c.check(name + " does not pass", bad_report.get("browser_verdict") != "pass" and expected in kinds, {"exit": bad_code, "error_kinds": kinds})
        control = copy.deepcopy(scenario)
        control["steps"][1]["selector_candidates"] = [
            candidate({"label": "Control"}), candidate({"e2e": "target"})]
        control["steps"][1]["action"] = "expect_dom"
        control["steps"][1]["claim_id"] = "e2e-action"
        control["steps"] = control["steps"][:2]
        control_code, control_events, _ = drive("label-control", control)
        c.check("valid label wins", control_code == 0 and any(
            e.get("step_id") == "activate" and e.get("selection", {}).get("candidate") == 0
            and not e["selection"].get("fallback_used") for e in control_events), control_events)
        retired = copy.deepcopy(scenario)
        retired["steps"][2]["selector"] = {"testid": "result"}
        refused = spec.validate_scenario(retired, config)
        c.check("retired testid is refused", any("e2e" in e for e in refused), refused)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        c.check("loopback server stopped", not thread.is_alive())


def case_repeat(c: Case) -> None:
    from exoma.core.evidence.e2e import runner, request
    from exoma.core.evidence.e2e.classify import failure_signature
    from exoma.core.evidence.e2e.fingerprint import project_fingerprint
    from exoma.core.runtime.state import note_e2e_outcome, e2e_repeat_state

    project = c.root / "project"
    project.mkdir()
    subprocess.run(["git", "init", "--quiet", str(project)], check=True, capture_output=True, **hidden())
    (project / ".workflow").mkdir()
    (project / ".workflow/config.json").write_text("{}", encoding="utf-8")
    source = project / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    sid, origin = "closure-repeat", "http://127.0.0.1:8089"
    config = request.default_settings()
    config["base_url"] = origin
    loaded = {"phase": "run", "settings": config, "scenario": {}, "existing_tests": [], "spec_notes": []}
    pre = {"ok": False, "reason": "playwright_missing", "detail": "synthetic preflight; no browser or provider", "checks": []}

    class NoProvider:
        def __getattr__(self, name):
            raise AssertionError(f"unexpected provider access: {name}")

    def invoke():
        with patch.object(runner.e2e_request, "load_request", return_value=(loaded, None, [])), \
             patch.object(runner, "preflight", return_value=pre):
            return runner.run(NoProvider(), project_root=project, session_id=sid, session={},
                              task="closure fixture", work_dir=str(project), on_progress=None,
                              session_manager=None, lock_claim=None, verify_route={})

    def seed(identity=None):
        note_e2e_outcome(project, sid, origin, None)
        for _ in range(runner.E2E_REPEAT_LIMIT):
            note_e2e_outcome(project, sid, origin, "app", signature="fixture-signature",
                             fingerprint=project_fingerprint(project), runtime=identity, source="browser")

    actual_identity = runner.runtime_identity()
    seed(actual_identity)
    before = e2e_repeat_state(project, sid)
    unchanged = invoke()
    c.write("unchanged.json", unchanged)
    c.check("unchanged at limit is braked", unchanged.get("meta", {}).get("e2e", {}).get("reason") == "repeat_failure", unchanged)
    fingerprint = project_fingerprint(project)
    (project / ".workflow/scenario.json").write_text('{"changed": true}', encoding="utf-8")
    c.check("scenario edit preserves fingerprint", project_fingerprint(project) == fingerprint)
    scenario_only = invoke()
    c.check("scenario edit does not release", not scenario_only.get("meta", {}).get("e2e", {}).get("repeat_released"), scenario_only)
    source.write_text("value = 2\n", encoding="utf-8")
    edited = invoke()
    c.write("source-edit.json", edited)
    c.check("source edit releases", edited.get("meta", {}).get("e2e", {}).get("repeat_released", {}).get("by") == "change_detected", edited)
    for _ in range(runner.E2E_REPEAT_LIMIT):
        again = invoke()
    c.check("failures after release can brake again", again.get("meta", {}).get("e2e", {}).get("reason") == "repeat_failure", again)
    seed("synthetic:older-runtime")
    older = invoke()
    c.write("runtime-release.json", older)
    c.check("old runtime releases", older.get("meta", {}).get("e2e", {}).get("repeat_released", {}).get("by") == "runtime_changed", older)
    seed(None)
    legacy = invoke()
    c.check("legacy identity is not a runtime release", not legacy.get("meta", {}).get("e2e", {}).get("repeat_released"), legacy)
    note_e2e_outcome(project, sid, origin, None)
    report = {"browser_verdict": "fail", "failures": [{"step_id": "field", "url_after": "/page", "action": "expect_text", "error_kind": "assertion"}]}
    def signature(text):
        return failure_signature(report, {"steps": [{"id": "field", "action": "expect_text", "selector": {"e2e": text}, "contains": "ready"}]})
    sig_a, sig_b = signature("a"), signature("b")
    streaks = [note_e2e_outcome(project, sid, origin, "app", signature=s) for s in (sig_a, sig_a, sig_b)]
    c.check("same signature increments; different resets", streaks == [1, 2, 1] and sig_a != sig_b, streaks)
    note_e2e_outcome(project, sid, origin, None)
    coarse = [note_e2e_outcome(project, sid, origin, "harness", reason=r, signature=s) for r, s in (("first", sig_a), ("second", sig_b))]
    c.check("harness remains coarse", coarse == [1, 2], coarse)
    note_e2e_outcome(project, sid, origin, None)
    c.check("pass clears state", not e2e_repeat_state(project, sid))
    c.write("state-and-identities.json", {"seed_before": before, "actual_runtime": actual_identity, "synthetic_old_runtime": "synthetic:older-runtime", "signatures": [sig_a, sig_b]})


def case_redaction(c: Case) -> None:
    from exoma.core.evidence.e2e.redact import scrub_literals
    from exoma.core.evidence.e2e import request, spec
    lookup = {"E2E_USER": "closure@example.test", "E2E_PASS": "password", "E2E_SHORT": "ab"}
    payload = {"task": "login as closure@example.test", "steps": [
        {"id": "fill-password", "action": "fill", "selector": {"css": "#password"}, "value": "password"},
        {"id": "short", "action": "fill", "value": "ab"}], "status": "ab"}
    clean, found = scrub_literals(payload, lookup)
    c.check("lookup triggers", set(found) == set(lookup), found)
    c.check("literal values become placeholders", clean["steps"][0]["value"] == "${E2E_PASS}" and clean["steps"][1]["value"] == "${E2E_SHORT}", clean)
    c.check("structure and selectors survive", clean["steps"][0]["id"] == "fill-password" and clean["steps"][0]["selector"]["css"] == "#password" and clean["status"] == "ab", clean)
    c.check("JSON round trip", json.loads(json.dumps(clean)) == clean)
    again, repeated = scrub_literals(clean, lookup)
    c.check("placeholder scrub is idempotent", again == clean and not repeated)
    replaced = json.dumps(clean)
    for key, value in lookup.items():
        replaced = replaced.replace("${" + key + "}", value)
    c.check("synthetic placeholder round trip", json.loads(replaced) == payload)
    project = c.root / "project"
    path = request.request_path(project, "redaction")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")
    written = request.scrub_request_file(project, "redaction", lookup)
    c.check("request writer actually scrubs", set(written) == set(lookup) and json.loads(path.read_text(encoding="utf-8")) == clean, written)
    scenario = {"version": 1, "claims": [{"id": "password", "source_refs": ["req:fixture"]}], "steps": [
        {"id": "fill-password", "action": "fill", "selector": {"css": "#password"}, "selector_provenance": {"type": "heuristic", "reason": "fixture"}, "value": "unknown-literal"},
        {"id": "check", "action": "expect_dom", "claim_id": "password", "selector": {"css": "#password"}, "selector_provenance": {"type": "heuristic", "reason": "fixture"}}]}
    refused = spec.validate_scenario(scenario)
    c.check("unknown password literal refused", any("never a literal" in e for e in refused), refused)
    scenario["steps"][0]["value"] = "${E2E_PASS}"
    accepted = spec.validate_scenario(scenario)
    c.check("placeholder scenario accepted", not accepted, accepted)
    c.write("synthetic-only.json", {"input": payload, "output": clean, "lookup_key_names": sorted(lookup), "validator_errors": refused})


def case_guards(c: Case) -> None:
    from exoma.core.workspace.runtime_lock import prune_runtime_guards, remove_orphan_guard
    sessions = c.root / "sessions"
    for name in ("dead", "live", "held"):
        folder = sessions / name / "runtime"
        folder.mkdir(parents=True)
        (folder / "lock.guard").write_bytes(b"0")
    save(sessions / "live/runtime/lock", {"pid": os.getpid()})
    shared = c.root / "evidence/shared.lock.guard"
    shared.parent.mkdir()
    shared.write_bytes(b"0")
    helper = "from exoma.utils import osutil; from pathlib import Path; import sys; h=osutil.open_lock_file(Path(sys.argv[1])); osutil.lock_file(h); print('ready',flush=True); sys.stdin.readline(); osutil.unlock_file(h); h.close()"
    proc = subprocess.Popen([sys.executable, "-B", "-c", helper, str(sessions / "held/runtime/lock.guard")], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, **hidden())
    try:
        # communicate with timeout is used below; readiness has a bounded poll on a file-free pipe.
        ready: list[str] = []
        reader = threading.Thread(target=lambda: ready.append(proc.stdout.readline().strip()), daemon=True)
        reader.start()
        reader.join(timeout=10)
        if reader.is_alive() or ready != ["ready"]:
            raise Blocked("byte-lock helper did not become ready")
        before = sorted(str(p.relative_to(c.root)) for p in c.root.rglob("*.guard"))
        first, second = prune_runtime_guards(sessions), prune_runtime_guards(sessions)
        c.check("orphan removed; live and held kept", first == {"removed": 1, "kept": 2}, first)
        c.check("second cleanup idempotent", second == {"removed": 0, "kept": 2}, second)
        c.check("shared evidence guard preserved", shared.exists())
        c.check("missing guard is absent", remove_orphan_guard(sessions / "absent/runtime/lock") == "absent")
        c.write("cleanup.json", {"before": before, "after": sorted(str(p.relative_to(c.root)) for p in c.root.rglob("*.guard")), "first": first, "second": second, "helper_pid": proc.pid})
    finally:
        if proc.poll() is None:
            try:
                helper_stdout, helper_stderr = proc.communicate("release\n", timeout=10)
                c.write("helper-exit.json", {"stdout": helper_stdout, "stderr": helper_stderr, "returncode": proc.returncode})
            except subprocess.TimeoutExpired:
                proc.terminate()
                proc.communicate(timeout=10)
        c.check("lock helper reaped", proc.poll() is not None)


def case_parser(c: Case) -> None:
    from exoma.core.evidence.contract import validate_verification_contract, verify_exit_status
    from exoma.core.evidence.e2e import normalize

    def text(item):
        return ("[VERIFICATION]\nverdict: DONE\nconfidence: high\nblocking_findings:\n- " + item +
                "\nescalations:\n- none\nnotes:\n- none\nchecks_run:\n- read fixture.py:1\nnot_verified:\n- none\n")

    rows = []
    for item, expected in [("none", "pass"), ("none.", "pass"), ("tidak ada", "pass"),
                           ("none - clean", "pass"), ("none; all clean", "pass"),
                           ("none - caller X fails", "fail"),
                           ("[severity:high] [origin:introduced] [scope_relation:in_scope] caller fails [fixture.py:1]", "fail"),
                           ("none.\n  caller X fails [fixture.py:1]", "fail"),
                           ("none (caller X fails)", "fail"),
                           ("none\n  caller X fails [fixture.py:1]", "fail")]:
        content = text(item)
        assessment = validate_verification_contract(content)
        actual = assessment.get("verdict")
        exit_code = verify_exit_status(actual, assessment)
        c.check(item, actual == expected and (expected != "pass" or exit_code == 0), {"verdict": actual, "exit": exit_code})
        rows.append({"input": content, "expected": expected, "actual": assessment, "exit_status": exit_code})
    for item in ("none.\n  caller X fails [fixture.py:1]", "none\n  caller X fails [fixture.py:1]"):
        sections = normalize._reviewer_sections(text(item))
        c.check("browser normalizer preserves " + item, bool(sections["blocking_findings"]), sections)
    c.write("classifications.json", rows)


def case_research(c: Case) -> None:
    import yaml
    from tools.maintain import check_research as checker
    from tests.checks.research import _DEC, _RQ, _HYP, _EXP
    root = c.root / "corpus"
    research = root / "docs/research"
    research.mkdir(parents=True)
    shutil.copyfile(REPO / "docs/research/schema.yaml", research / "schema.yaml")
    schema = yaml.safe_load((research / "schema.yaml").read_text(encoding="utf-8"))
    for value in schema["types"].values():
        target = research / value["directory"]
        target.mkdir()
        shutil.copyfile(REPO / "docs/research" / value["directory"] / "_TEMPLATE.yaml", target / "_TEMPLATE.yaml")

    def write(folder, name, record):
        path = research / folder / name
        path.write_text(yaml.safe_dump(record, sort_keys=False), encoding="utf-8")
        return path

    dec = {**copy.deepcopy(_DEC), "schema_version": 3, "questions": ["RQ-90"]}
    rq = {**copy.deepcopy(_RQ), "records": ["DEC-900"]}
    decpath = write("decisions", "DEC-900-fixture.yaml", dec)
    rqpath = write("questions", "RQ-90-fixture.yaml", rq)
    snapshots = []
    targets = {"REPO_ROOT": root, "RESEARCH": research, "DRAFTS": root / "docs/research-drafts",
               "SCHEMA": research / "schema.yaml", "README": research / "README.md", "QUESTIONS": research / "questions.md",
               "FROZEN_V1": frozenset(), "FROZEN_V2": frozenset()}

    def inspect(name, expected=None, *, drafts=False):
        report = checker.validate(strict=False, git=False, drafts=drafts)
        errors = list(report.errors)
        c.check(name, not errors if expected is None else any(expected in e for e in errors), errors)
        snapshots.append({"name": name, "expected_error_contains": expected, "errors": errors})

    with patch.multiple(checker, **targets):
        inspect("valid reciprocal corpus")
        write("questions", rqpath.name, {**rq, "records": []})
        inspect("one-way link rejected", "records do not list")
        write("questions", rqpath.name, rq)
        dup = write("decisions", "DEC-900-duplicate.yaml", dec)
        inspect("duplicate id rejected", "duplicate")
        dup.unlink()
        write("questions", rqpath.name, {**rq, "status": "partial"})
        inspect("RQ partial rejected", "rq_status")
        write("questions", rqpath.name, {**rq, "records": ["H-777"]})
        inspect("draft id in tracked RQ rejected", "not a tracked record")
        write("questions", rqpath.name, rq)
        full = copy.deepcopy(dec)
        full["disposition"] = {"implementation_status": "implemented", "validation_status": "validated", "validated_by": "fixture-maintainer", "validated_on": "2026-11-10"}
        full["validation"].update(uses=2, consistent=True, maintainer_decision="synthetic fixture authority, not a real decision", measurements=[{"source": "fixture telemetry", "value": "two synthetic uses"}])
        write("decisions", decpath.name, full)
        inspect("complete validated decision accepted")
        for key, expected in (("uses", "validation.uses"), ("consistent", "validation.consistent"), ("maintainer_decision", "validation.maintainer_decision"), ("measurements", "measured")):
            changed = copy.deepcopy(full)
            changed["validation"].pop(key)
            write("decisions", decpath.name, changed)
            inspect("missing " + key, expected)
        for value in (1, True):
            changed = copy.deepcopy(full)
            changed["validation"]["uses"] = value
            write("decisions", decpath.name, changed)
            inspect("uses=" + str(value), "validation.uses")
        changed = copy.deepcopy(full)
        changed["disposition"].pop("validated_on")
        write("decisions", decpath.name, changed)
        inspect("missing authority date", "validated_by and validated_on")
        write("decisions", decpath.name, full)
        answered = {**rq, "status": "answered", "decision": {"date": "2026-11-10", "method": "critical_interview", "summary": "synthetic fixture"}}
        write("questions", rqpath.name, answered)
        inspect("answered with validated record and decision")
        write("questions", rqpath.name, {k: v for k, v in answered.items() if k != "decision"})
        inspect("answered without decision", "decision")
        write("decisions", decpath.name, dec)
        write("questions", rqpath.name, answered)
        inspect("answered without validated record", "validated")
        write("questions", rqpath.name, rq)
        hyp = {**copy.deepcopy(_HYP), "schema_version": 3, "questions": ["RQ-90"], "experiments": ["EXP-900"]}
        exp = {**copy.deepcopy(_EXP), "schema_version": 3, "questions": ["RQ-90"]}
        hp = write("hypotheses", "H-900-fixture.yaml", hyp)
        ep = write("experiments", "EXP-900-fixture.yaml", exp)
        write("questions", rqpath.name, {**rq, "records": ["DEC-900", "H-900", "EXP-900"]})
        inspect("reciprocal hypothesis experiment accepted")
        write("hypotheses", hp.name, {**hyp, "experiments": []})
        inspect("one-way H EXP rejected", "experiments")
        hp.unlink()
        ep.unlink()
        write("questions", rqpath.name, rq)
        archive = targets["DRAFTS"] / "_archive/decisions"
        archive.mkdir(parents=True)
        (archive / "DEC-999-ignored.yaml").write_text("invalid: [", encoding="utf-8")
        inspect("nested archive ignored", drafts=True)
    c.write("validator-cases.json", snapshots)


def case_graph(c: Case) -> None:
    import exoma
    from exoma.core.graph.graph_index import load_graph
    hook = Path(exoma.__file__).parent / "_bundle/config/hosts/claude/hooks/graph-refresh.py"
    project = c.root / "project"
    (project / "graphify-out").mkdir(parents=True)
    (project / ".workflow/data").mkdir(parents=True)
    graph = project / "graphify-out/graph.json"
    lock = graph.with_name(".refresh.lock")
    graph.write_text('{"nodes": [], "links": []}', encoding="utf-8")
    source = project / "source.py"
    source.write_text("value = 1\n", encoding="utf-8")
    os.utime(graph, (time.time() - 10, time.time() - 10))
    bindir = c.root / "bin"
    bindir.mkdir()
    stub = bindir / "stub.py"
    stub.write_text("import json,time; from pathlib import Path; p=Path.cwd(); (p/'.workflow/data/stub-started').write_text('started'); time.sleep(1.5); (p/'graphify-out/graph.json').write_text(json.dumps({'nodes': [], 'links': [], 'closure_fixture': True}))\n", encoding="utf-8")
    if os.name == "nt":
        launcher = bindir / "graphify.cmd"
        launcher.write_text(f'@"{sys.executable}" -B "{stub}" %*\n', encoding="utf-8")
    else:
        launcher = bindir / "graphify"
        launcher.write_text(f'#!{sys.executable}\nexec(compile(open({str(stub)!r}).read(),{str(stub)!r},"exec"))\n', encoding="utf-8")
        launcher.chmod(0o700)
    env = dict(os.environ, PATH=str(bindir) + os.pathsep + os.environ.get("PATH", ""), GRAPH_REFRESH_BEAT_S="0.1")

    def invoke(message):
        payload = {"cwd": str(project), "last_assistant_message": message}
        runenv = dict(env, CLAUDE_HOOK_RAW=json.dumps(payload))
        started = time.monotonic()
        result = subprocess.run([sys.executable, "-B", str(hook)], env=runenv, capture_output=True, text=True, timeout=15, **hidden())
        return {"returncode": result.returncode, "elapsed_s": time.monotonic() - started, "stdout": result.stdout, "stderr": result.stderr}

    c.check("message without execution marker starts no worker", invoke("analysis only")["returncode"] == 0 and not lock.exists())
    first = invoke("[EXECUTION RESULT] fixture change")
    snapshots = []
    quality = project / ".workflow/data/quality.jsonl"
    try:
        c.check("hook returns while worker lock exists", first["returncode"] == 0 and lock.exists(), first)
        if lock.exists():
            snapshots.append(json.loads(lock.read_text(encoding="utf-8")))
        c.check("reader refuses active refresh", load_graph(project) is None)
        second = invoke("[EXECUTION RESULT] second fixture change")
        deadline = time.monotonic() + 15
        while lock.exists() and time.monotonic() < deadline:
            try:
                snapshots.append(json.loads(lock.read_text(encoding="utf-8")))
            except (OSError, ValueError):
                pass
            time.sleep(0.05)
        rows = [json.loads(s) for s in quality.read_text(encoding="utf-8").splitlines()] if quality.exists() else []
        c.check("worker completes and releases lock", not lock.exists(), rows)
        c.check("exactly one worker refreshed graph", sum(r.get("outcome") == "refreshed" for r in rows) == 1 and graph.exists() and json.loads(graph.read_text(encoding="utf-8")).get("closure_fixture") is True, rows)
        c.check("second hook observes held lock", any(r.get("outcome") == "skipped_running" for r in rows), second)
        c.check("heartbeat names graphify child", any(s.get("graphify_pid") for s in snapshots), snapshots)
        c.check("reader reads completed graph", load_graph(project) is not None)
        invoke("[EXECUTION RESULT] fresh fixture")
        fresh_rows = [json.loads(s) for s in quality.read_text(encoding="utf-8").splitlines()]
        c.check("fresh graph skips worker", fresh_rows[-1].get("outcome") == "skipped_fresh", fresh_rows[-1])
        # Execute only the actual worker body in a tracked child, swapping lock owner after
        # its stub starts. This tests token cleanup without modifying packaged source.
        scope = {}
        code = hook.read_text(encoding="utf-8").split("\ntry:\n    started =", 1)[0]
        exec(compile(code, str(hook), "exec"), scope)
        (project / ".workflow/data/stub-started").unlink(missing_ok=True)
        save(lock, {"token": "original", "beat": int(time.time()), "pid": os.getpid()})
        proc = subprocess.Popen([sys.executable, "-B", "-c", scope["WORKER"], str(project), "0", "0", "1", "0", "original", "0.1"], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **hidden())
        try:
            deadline = time.monotonic() + 10
            while not (project / ".workflow/data/stub-started").exists() and proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            save(lock, {"token": "replacement-owner", "beat": int(time.time()), "pid": os.getpid()})
            proc.wait(timeout=15)
            c.check("worker does not remove another token", lock.exists() and json.loads(lock.read_text(encoding="utf-8")).get("token") == "replacement-owner")
        finally:
            if proc.poll() is None:
                proc.terminate()
                proc.wait(timeout=5)
            lock.unlink(missing_ok=True)
        c.write("lifecycle.json", {"first": first, "second": second, "lock_snapshots": snapshots, "quality_rows": rows, "graphify": "synthetic local stub; no real graphify benchmark"})
    finally:
        # The detached hook worker is bounded by its finite stub; report any remaining lock.
        c.check("fixture leaves no refresh lock", not lock.exists())


def worker(name: str, root: Path) -> int:
    c = Case(name, root)
    provenance = {"timestamp": datetime.now(timezone.utc).isoformat(), "interpreter": sys.executable}
    baseline_path = OUTPUT / "baseline.json"
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        provenance.update(tool_commit=baseline["tool_commit"], tool_dirty=True)
    provenance["harness_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    try:
        import exoma
        provenance.update(exoma_source=str(Path(exoma.__file__).resolve()), exoma_version=importlib.metadata.version("exoma"))
        direct = importlib.metadata.distribution("exoma").read_text("direct_url.json")
        provenance["exoma_pin"] = json.loads(direct or "{}").get("vcs_info", {}).get("commit_id")
        expected_pin = json.loads((OUTPUT / "environment.json").read_text(encoding="utf-8"))["exoma_pin"]
        if provenance["exoma_pin"] != expected_pin:
            raise Blocked("installed Exoma revision differs from the pre-registered environment")
        globals()["case_" + name](c)
        status = "pass" if c.checks and all(row["passed"] for row in c.checks) else "fail"
        error = None
    except Blocked as exc:
        status, error = "blocked", str(exc)
    except Exception:
        status, error = "fail", traceback.format_exc()
    result = {"case_id": name, "record_ids": GROUPS[name], "status": status,
              "evidence_kind": "real-browser-designed-fixture" if name == "browser" else "designed-fixture",
              "provenance": provenance, "checks": c.checks, "error": error,
              "limitation": "Not a maintainer direct-use run; no automatic disposition or authority change."}
    c.write("result.json", result)
    print(json.dumps(result, ensure_ascii=False, default=str))
    return 0 if status == "pass" else (2 if status == "blocked" else 1)


def measure(*, browser_python: str | None = None, groups: list[str] | None = None) -> dict:
    groups = groups or list(GROUPS)
    invalid = set(groups) - set(GROUPS)
    if invalid:
        raise ValueError(f"unknown groups: {sorted(invalid)}")
    environment_path = OUTPUT / "environment.json"
    observed = json.loads(environment_path.read_text(encoding="utf-8")) if environment_path.exists() else {}
    interpreter = browser_python or observed.get("browser_python") or sys.executable
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    runroot = confined(OUTPUT / "runs" / run_id)
    runroot.mkdir(parents=True)
    source = Path(__file__).read_bytes()
    (runroot / "harness.py").write_bytes(source)
    protocol = {"groups": groups, "records": GROUPS, "statuses": ["pass", "fail", "blocked", "not_run"],
                "authority": "fixture evidence only; maintainer disposition pending", "created_before_measurement": True,
                "harness_sha256": hashlib.sha256(source).hexdigest(),
                "expected": "Each registered assertion in the archived harness is expected to hold; product counterexamples stay failures.",
                "method": "Opt-in designed fixtures. Changes to fixture setup retain earlier runs and get a new source hash."}
    save(runroot / "protocol.json", protocol)
    results = []
    for name in groups:
        folder = runroot / name
        folder.mkdir()
        env = child_environment(folder)
        try:
            done = subprocess.run([str(interpreter), "-B", str(Path(__file__).resolve()), "--worker", name, "--case-root", str(folder)], env=env, capture_output=True, text=True, encoding="utf-8", timeout=90, **hidden())
            (folder / "stdout.txt").write_text(done.stdout, encoding="utf-8")
            (folder / "stderr.txt").write_text(done.stderr, encoding="utf-8")
            result = json.loads((folder / "result.json").read_text(encoding="utf-8"))
            result["process_exit"] = done.returncode
            if result["status"] == "pass" and done.returncode != 0:
                result.update(status="fail", error="worker claimed pass but process exited nonzero")
        except Exception as exc:
            result = {"case_id": name, "record_ids": GROUPS[name], "status": "blocked" if isinstance(exc, FileNotFoundError) else "fail", "error": str(exc), "checks": [], "evidence_kind": "designed-fixture", "provenance": {"timestamp": datetime.now(timezone.utc).isoformat()}}
            save(folder / "result.json", result)
        results.append(result)
        print(f"  closure {name}: {result['status']} ({len(result.get('checks', []))} checks)", flush=True)
    result = {"run_id": run_id, "artifact_root": str(runroot.relative_to(REPO)),
              "protocol": protocol, "cases": results, "summary": aggregate(results, groups)}
    save(runroot / "result-manifest.json", result)
    save(OUTPUT / "result-manifest.json", result)
    save(OUTPUT / "preflight.json", {
        "interpreter": str(interpreter), "dependency_revision_checked_in_workers": True,
        "browser_launch_exercised": "browser" in groups,
        "case_environment_blocks": [{"case_id": c["case_id"], "reason": c.get("error")} for c in results if c["status"] == "blocked"],
        "limitation": "An environment readiness result is not direct-use evidence or validation authority.",
    })
    return result


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--measure", action="store_true", help="opt-in designed measurements, including real local browser")
    parser.add_argument("--browser-python", help="interpreter with Playwright; Exoma comes from the pinned project environment")
    parser.add_argument("--group", action="append", choices=sorted(GROUPS))
    parser.add_argument("--worker", choices=sorted(GROUPS), help=argparse.SUPPRESS)
    parser.add_argument("--case-root", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.worker:
        if args.case_root is None:
            parser.error("--worker requires --case-root")
        return worker(args.worker, args.case_root)
    if not args.measure:
        parser.error("measurements require explicit --measure")
    result = measure(browser_python=args.browser_python, groups=args.group)
    return 0 if result["summary"]["status"] == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
