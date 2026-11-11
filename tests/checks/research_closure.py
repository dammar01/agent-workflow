"""Closure harness integrity; expensive designed measurements are explicitly selected."""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time

from tests.checks.support import assert_true
from tools.maintain.research_closure import OUTPUT, REPO, aggregate, bounded_worker, confined, measure, preflight_observations, collect_human_events, redact_event_text, prepare_human_judge, check_human_judgement, decode_human_judge_response, human_event_public_report, compare_human_judges, _json_objects


def _test_research_closure_harness() -> None:
    _test_plan_linked_human_events()
    good = {"case_id": "a", "status": "pass", "evidence_kind": "designed-fixture",
            "provenance": {"exoma_pin": "fixture"}, "checks": [{"passed": True}]}
    assert_true(aggregate([good], ["a"])["status"] == "pass", "complete evidence is a technical pass")
    for status, expected in (("fail", "fail"), ("blocked", "incomplete"), ("not_run", "incomplete")):
        changed = {**good, "status": status}
        assert_true(aggregate([changed], ["a"])["status"] == expected, f"{status} cannot become pass")
    for name, rows in (("missing", []), ("duplicate", [good, good]), ("unexpected", [{**good, "case_id": "b"}]),
                       ("no provenance", [{**good, "provenance": {}}]),
                       ("unsupported pass", [{**good, "checks": []}]),
                       ("failed assertion", [{**good, "checks": [{"passed": False}]}])):
        assert_true(aggregate(rows, ["a"])["status"] == "fail", f"{name} fails closed")
    original = copy.deepcopy(good)
    aggregate([good], ["a"])
    assert_true(good == original, "aggregation does not rewrite recorded evidence")
    selected = preflight_observations([good], ["browser"], "fixture-python")
    assert_true(selected["browser_selected"] and not selected["browser_launch_exercised"], "selecting browser is not a launch")
    assert_true(not selected["dependency_revision_checked_in_workers"], "a missing revision observation is not a successful check")
    observed = {**good, "case_id": "browser", "provenance": {"expected_revision_verified": True},
                "checks": [{"name": "real browser verdict passes", "passed": True}]}
    ready = preflight_observations([observed], ["browser"], "fixture-python")
    assert_true(ready["browser_launch_exercised"] and ready["dependency_revision_checked_in_workers"], "actual observations establish readiness")
    blocked = {"case_id": "parser", "status": "blocked", "provenance": {}, "error": "missing runtime"}
    assert_true(not preflight_observations([observed, blocked], ["browser", "parser"], "p")["dependency_revision_checked_in_workers"], "partial startup cannot claim all workers checked identity")

    from exoma.utils.osutil import process_alive
    code = "import subprocess,sys,time; p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(5)']); print(p.pid,flush=True); time.sleep(30)"
    started = time.monotonic()
    exit_code, stdout, _ = bounded_worker([sys.executable, "-B", "-c", code], dict(os.environ), timeout=0.7)
    assert_true(exit_code is None and time.monotonic() - started < 5, "hung child/tree cannot hold the measurement pipe indefinitely")
    descendant = int(stdout.strip().splitlines()[0])
    deadline = time.monotonic() + 2
    while process_alive(descendant) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert_true(not process_alive(descendant), "timeout cleans the owned descendant, not only the direct child")
    assert_true(confined(OUTPUT / "fixture") == (OUTPUT / "fixture").resolve(), "fixture output is accepted")
    for path in (REPO / "outside.json", OUTPUT / "../escape.json"):
        try:
            confined(path)
        except ValueError:
            pass
        else:
            raise AssertionError("output outside fixture boundary must be refused")


def _test_plan_linked_human_events() -> None:
    """Identity/copy traps that would materially bias the intervention denominator."""
    project = str(REPO)
    assert_true(not list(_json_objects(json.dumps({"ok": False, "previous_result": {"ok": True, "plan_id": "a" * 16}}))),
                "a failed plan-record cannot borrow an embedded previous success")
    def record(uid, role, content, second, session="s1", **extra):
        return {"uuid": uid, "type": role, "sessionId": session, "cwd": project,
                "timestamp": f"2026-10-01T00:00:{second:02d}Z", "message": {"role": role, "content": content}, **extra}
    def call(uid, tool_id, command, second, parent, session="s1"):
        return record(uid, "assistant", [{"type": "tool_use", "id": tool_id, "name": "Bash", "input": {"command": command}}], second, session, parentUuid=parent)
    def result(uid, tool_id, plan, second, session="s1", ok=True):
        return record(uid, "user", [{"type": "tool_result", "tool_use_id": tool_id, "content": json.dumps({"ok": ok, "meta": {"plan_id": plan}})}], second, session)
    p, q = "a" * 16, "b" * 16
    rows = [record("u1", "user", "Do the task", 1),
        call("a1", "t1", "bash .workflow/run.sh plan-record", 2, "u1"), result("r1", "t1", p, 3),
        record("u2", "user", "same text", 4, promptId="shared"),
        record("aq", "assistant", [{"type": "tool_use", "id": "ask", "name": "AskUserQuestion", "input": {"questions": [{"question": "A?"}, {"question": "B?"}]}}], 5),
        record("answer", "user", [{"type": "tool_result", "tool_use_id": "ask", "content": "answered"}], 6, promptId="shared", toolUseResult={"answers": {"A?": "yes", "B?": "no"}}),
        record("u3", "user", "same text", 7, promptId="shared"),
        record("automatic", "user", [{"type": "tool_result", "tool_use_id": "grep", "content": "error", "is_error": True}], 8),
        record("system", "user", "<task-notification>agent done</task-notification>", 9, isMeta=True),
        record("u4", "user", "new objective", 10), call("a2", "t2", "bash .workflow/run.sh plan-record", 11, "u4"), result("r2", "t2", q, 12),
        record("u5", "user", "continuation", 13, session="s2"),
        call("a3", "t3", "bash .workflow/run.sh plan-record", 14, "u5", session="s2"), result("r3", "t3", p, 15, session="s2"),
        call("af", "tf", "bash .workflow/run.sh plan-record", 16, "u5", session="s2"), result("rf", "tf", "c" * 16, 17, session="s2", ok=False),
        record("side", "user", "copied context", 18, isSidechain=True),
        record("clear", "user", "/clear", 19),
        record("denial-request", "assistant", [{"type": "tool_use", "id": "denied-ask", "name": "AskUserQuestion", "input": {"questions": [{"question": "Proceed?"}]}}], 20),
        record("denial", "user", [{"type": "tool_result", "tool_use_id": "denied-ask", "is_error": True,
            "content": "The user doesn't want to proceed with this tool use. The tool use was rejected (example)."}], 21,
            toolUseResult="User denied tool use"),
        call("denied-bash", "denied-command", "echo fixture", 22, "u4"),
        record("bash-denial", "user", [{"type": "tool_result", "tool_use_id": "denied-command", "is_error": True,
            "content": "The user doesn't want to proceed with this tool use. The tool use was rejected (example)."}], 23),
        record("automatic-ask-error", "user", [{"type": "tool_result", "tool_use_id": "denied-ask", "is_error": True,
            "content": "Permission denied: system policy disallows this operation"}], 24)]
    with tempfile.TemporaryDirectory(prefix="human-events-test-") as tmp:
        roots = [Path(tmp) / name for name in ("active", "backup")]
        for root in roots:
            folder = root / "project"; folder.mkdir(parents=True)
            (folder / "session.jsonl").write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        data = collect_human_events(roots)
        assert_true(not data["integrity_errors"], "identical archive copies are not UUID conflicts")
        counts = data["summary"]["counts"]
        assert_true(counts["human_message"] == 5 and counts["question_answer"] == 1 and counts["question_request"] == 2,
                    "same-text/different-UUID and shared promptId answers survive, backup copies do not")
        assert_true(counts["administrative_command"] == 1 and data["summary"]["answer_fields"] == 2, "administration and multi-answer submission remain separate")
        refusals = [e for e in data["events"] if e["kind"] == "explicit_tool_refusal"]
        assert_true(len(refusals) == 2 and {r["request_tool_name"] for r in refusals} == {"AskUserQuestion", "Bash"},
                    "explicit question/tool refusals survive and deduplicate archive copies; automatic denial does not become human rejection")
        multi_rows = [record("multi-call", "assistant", [
            {"type": "tool_use", "id": "multi-a", "name": "Bash", "input": {"command": "echo fixture"}},
            {"type": "tool_use", "id": "multi-b", "name": "Read", "input": {"file_path": "fixture.txt"}}], 25),
            record("multi-denial", "user", [{"type": "tool_result", "tool_use_id": tool_id, "is_error": True,
                "content": "The user doesn't want to proceed with this tool use. The tool use was rejected (example)."}
                for tool_id in ("multi-a", "multi-b")], 26)]
        (roots[0] / "project/multi-refusal.jsonl").write_text("\n".join(json.dumps(r) for r in multi_rows), encoding="utf-8")
        multi = collect_human_events(roots)
        multi_events = [e for e in multi["events"] if e.get("uuid") == "multi-denial"]
        assert_true(not multi["integrity_errors"] and len(multi_events) == 2 and len({e["id"] for e in multi_events}) == 2,
                    "two refused tools in one user UUID retain distinct identities without an integrity conflict")
        assert_true(len(data["plan_receipts"]) == 3 and len(data["units"]) == 2, "failed receipts excluded; two plans in one session stay separate")
        assert_true(data["summary"]["cross_session_plan_units"] == 1, "one plan across two sessions forms one candidate plan unit")
        assert_true(next(e for e in data["events"] if e.get("uuid") == "u1")["plan_id"] == p, "pre-plan user request linked through actual ancestry")
        assert_true(not any(e.get("uuid") in ("automatic", "system", "side") for e in data["events"]), "tool errors, metadata and sidechain are not human rejections")
        prepared = prepare_human_judge(data)
        assert_true(prepared["sampled_events"] == 5, "semantic sample excludes commands, question calls and structured answers")
        packet = prepared["packets"][0]
        assert_true(all(set(unit) == {"id", "preceding_assistant_text", "user_text", "user_text_truncated"} for unit in packet["units"]),
                    "judge receives no source identity, plan, original category or previous verdict")
        body = {"judgements": [{"id": unit["id"], "labels": ["insufficient_context"], "rationale": "Synthetic fixture lacks task context."} for unit in packet["units"]]}
        assert_true(len(check_human_judgement(packet, body)) == 5, "complete uncertain judgement is retained without inventing a semantic label")
        terminal = {"type": "result", "subtype": "success", "structured_output": body}
        envelope, initial = decode_human_judge_response(json.dumps([{"type": "system", "subtype": "init", "tools": ["StructuredOutput"]}, terminal]))
        assert_true(envelope == terminal and initial["tools"] == ["StructuredOutput"], "Claude event-array output preserves terminal judgement and isolation metadata")
        assert_true(decode_human_judge_response(json.dumps(terminal))[0] == terminal, "single-object Claude output remains accepted")
        try:
            decode_human_judge_response(json.dumps([terminal, terminal]))
        except ValueError:
            pass
        else:
            raise AssertionError("two terminal results are ambiguous, not a judgement")
        def hashed(value):
            return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
        fake_judge = {"launch": {"data_sha256": "a" * 64, "rubric_sha256": hashed(prepared["rubric"])},
            "status": "observed", "observed_units": 5, "expected_units": 5,
            "packets": [{"packet_id": packet["packet_id"], "packet_sha256": hashed(packet), "status": "observed",
                         "session_id": "fresh-fixture", "judgements": body["judgements"]}]}
        public, units = human_event_public_report(data, fake_judge, "a" * 64)
        encoded = json.dumps([public, units])
        assert_true("Do the task" not in encoded and project not in encoded and "sources" not in public,
                    "public report contains no raw conversation, source paths or identity key")
        assert_true(public["scientific_closure"] == "incomplete", "observed calibration is not scientific closure")
        original_bytes = json.dumps(data, ensure_ascii=False, indent=2).encode()
        original_sha = hashlib.sha256(original_bytes).hexdigest()
        bridge_judge = copy.deepcopy(fake_judge); bridge_judge["launch"]["data_sha256"] = original_sha
        bridged, _ = human_event_public_report(data, bridge_judge, "b" * 64, original_bytes)
        assert_true(bridged["retained_judgement_bridge"]["masked_packets_and_event_mapping_identical"],
                    "retained judgements can transfer only through verified original bytes and identical masked inputs")
        altered = copy.deepcopy(data)
        next(e for e in altered["events"] if e["kind"] == "human_message")["text"] = "Changed judge-visible request"
        try:
            human_event_public_report(altered, bridge_judge, "c" * 64, original_bytes)
        except ValueError:
            pass
        else:
            raise AssertionError("changed judge-visible data reused an original judgement")
        peer = copy.deepcopy(fake_judge); peer["packets"][0]["session_id"] = "another-fresh-fixture"
        peer["packets"][0]["judgements"][0]["labels"] = ["initial_request"]
        agreement = compare_human_judges(data, fake_judge, peer, "a" * 64)
        assert_true(agreement["label_set_disagreements"] == 1 and agreement["exact_label_set_agreement"] == 4,
                    "cross-model disagreement is retained without choosing a favourable label")
        try:
            compare_human_judges(data, fake_judge, fake_judge, "a" * 64)
        except ValueError:
            pass
        else:
            raise AssertionError("same-session responses passed as independent model judgements")
        for stale in ({**fake_judge, "launch": {**fake_judge["launch"], "data_sha256": "b" * 64}},
                      {**fake_judge, "observed_units": 60}):
            try:
                human_event_public_report(data, stale, "a" * 64)
            except ValueError:
                pass
            else:
                raise AssertionError("stale census or invented reviewed count accepted")
        evidence_packet = {"units": [{"id": "one", "user_text": "No, I meant Y", "preceding_assistant_text": "You asked for X"}]}
        evidence_body = {"judgements": [{"id": "one", "labels": ["reasoning_correction"], "rationale": "User explicitly corrects X to Y.",
            "user_quote": "I meant Y", "assistant_quote": "You asked for X"}]}
        check_human_judgement(evidence_packet, evidence_body, require_spans=True)
        invalid = copy.deepcopy(evidence_body); invalid["judgements"][0]["assistant_quote"] = "invented agent claim"
        try:
            check_human_judgement(evidence_packet, invalid, require_spans=True)
        except ValueError:
            pass
        else:
            raise AssertionError("invented correction evidence accepted")
        for bad in ({"judgements": body["judgements"][:-1]}, {"judgements": [body["judgements"][0]] * 5},
                    {"judgements": [{**row, "labels": ["made_up"]} for row in body["judgements"]]}):
            try:
                check_human_judgement(packet, bad)
            except ValueError:
                pass
            else:
                raise AssertionError("incomplete, duplicate or invented judge labels accepted")
        changed = copy.deepcopy(rows); changed[0]["message"]["content"] = "conflicting copy"
        (roots[1] / "project/session.jsonl").write_text("\n".join(json.dumps(r) for r in changed), encoding="utf-8")
        assert_true(bool(collect_human_events(roots)["integrity_errors"]), "conflicting UUID content fails closed")
        (roots[1] / "project/session.jsonl").write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
        other = Path(tmp) / "other-project"; (other / ".git").mkdir(parents=True)
        extra = copy.deepcopy(rows[:3])
        for row in extra:
            row["cwd"] = str(other)
        folder = roots[0] / "another-project"; folder.mkdir()
        (folder / "session.jsonl").write_text("\n".join(json.dumps(r) for r in extra), encoding="utf-8")
        expanded = collect_human_events(roots)
        assert_true(len(expanded["units"]) == 3 and expanded["summary"]["counts"]["human_message"] == 6,
                    "identical plan ID or event UUID in a different project does not merge workloads")
        corrupt = roots[0] / "project/corrupt.jsonl"
        corrupt.write_text('{broken JSON\n[1, 2, 3]\n', encoding="utf-8")
        broken = collect_human_events(roots)
        gaps = {g["kind"] for g in broken["integrity_errors"]}
        assert_true({"invalid_json_record", "non_object_json_record"} <= gaps,
                    "malformed JSON and non-object records retain source/line integrity gaps")
        for operation in (lambda: prepare_human_judge(broken), lambda: human_event_public_report(broken, {})):
            try:
                operation()
            except ValueError:
                pass
            else:
                raise AssertionError("partial corrupt archive can still reach judging or observed publication")
    clean = redact_event_text("ANTHROPIC_API_KEY=sk-private123456789012345 email a@example.com https://example.com/key C:/Users/private/file")
    assert_true("sk-private" not in clean and "a@example.com" not in clean and "https://" not in clean and "C:/Users" not in clean,
                "judge packet minimises credentials and direct identifiers")


def _measure_research_closure() -> None:
    """Opt-in measurement; fixture failures remain visible as a nonzero test result."""
    result = measure()
    failed = {case["case_id"]: {
        "status": case["status"], "error": case.get("error"),
        "failed_checks": [row for row in case.get("checks", []) if not row["passed"]],
    } for case in result["cases"] if case["status"] != "pass"}
    assert_true(result["summary"]["status"] == "pass", f"designed measurement failures (not a product patch request): {failed}; artifacts: {result['artifact_root']}")
