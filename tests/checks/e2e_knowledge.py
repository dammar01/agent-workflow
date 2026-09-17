"""Browser knowledge: what a run proved is kept, reused by the next draft, and aged out.

The store sits at the fact store's level — written automatically, anchored, never in Git —
so the checks mirror that one: only proof is recorded, credentials never, a failure
weakens and two in a row retire, a moved anchor survives and a vanished one does not, and
only well-proven anchored entries are offered to /.promote.
"""

from __future__ import annotations
from core.workspace.workspace_paths import data_dir

import json
import shutil
import tempfile
from pathlib import Path

from core.evidence.e2e import knowledge
from core.evidence.e2e.classify import build_report
from tests.checks.support import assert_true

BASE = "http://app.test"

_SCENARIO = {
    "version": 1,
    "claims": [{"id": "session", "severity": "blocking"}],
    "steps": [
        {"id": "open-login", "action": "goto", "url": "/login", "ready": [{"visible": {"role": "button", "name": "Masuk"}}]},
        {"id": "user", "action": "fill", "selector": {"label": "Email"}, "value": "${E2E_USER}"},
        {"id": "pass", "action": "fill", "selector": {"label": "Password"}, "value": "${E2E_PASS}"},
        {"id": "note", "action": "fill", "selector": {"label": "Note"}, "value": "typed test data"},
        {"id": "submit", "action": "click", "selector": {"role": "button", "name": "Masuk"},
         "selector_provenance": {"type": "source", "ref": "src/Login.vue:3"}},
        {"id": "at-dashboard", "action": "expect_url", "contains": "/dashboard", "claim_id": "session"},
        {"id": "logout", "action": "click", "selector": {"role": "button", "name": "Keluar"}},
        {"id": "back-at-login", "action": "expect_url", "contains": "/login", "claim_id": "session"},
    ],
}


def _report(statuses: dict[str, str] | None = None) -> dict:
    events = []
    for index, step in enumerate(_SCENARIO["steps"], start=1):
        status = (statuses or {}).get(step["id"], "passed")
        event = {"type": "progress", "step": index, "step_id": step["id"], "action": step["action"],
                 "status": status, "claim_id": step.get("claim_id")}
        if step["id"] == "submit":
            event["selection"] = {"candidate": 0, "match_counts": [1], "fallback_used": False,
                                  "source_ref": "src/Login.vue:3", "selector_keys": ["name", "role"]}
        if status == "failed":
            event["error"] = {"kind": "selector_missing"}
            event["selector_provenance"] = "source"
            event["page_stable"] = True
        events.append(event)
    events.append({"type": "result", "status": "finished"})
    return build_report(events, _SCENARIO)


def _project() -> Path:
    root = Path(tempfile.mkdtemp(prefix="aw-e2e-knowledge-"))
    (root / ".workflow").mkdir()
    (root / "src").mkdir()
    (root / "src" / "Login.vue").write_text("<template>\n<form>\n<button>Masuk</button>\n</form>\n</template>\n", encoding="utf-8")
    return root


def _by_kind(root: Path) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for entry in knowledge.load(root):
        grouped.setdefault(entry["kind"], []).append(entry)
    return grouped


def _check_a_run_records_what_it_proved(root: Path) -> None:
    summary = knowledge.ingest(root, _report(), _SCENARIO, BASE, "sid-1", clean_first_attempt=True)
    kinds = _by_kind(root)
    assert_true(summary["added"] > 0 and not summary["weakened"], f"a passing run adds entries: {summary}")
    login = kinds.get("auth.login") or []
    assert_true(
        len(login) == 1 and login[0]["route"] == "/login"
        and [s["action"] for s in login[0]["steps"]] == ["goto", "fill", "fill", "fill", "click", "expect_url"],
        f"the login flow is the goto through the first assertion after the password: {login}",
    )
    assert_true(
        (kinds.get("auth.logout") or [{}])[0].get("steps", [{}])[0].get("selector") == {"role": "button", "name": "Keluar"},
        f"the step followed by a return to the login page is the logout: {kinds.get('auth.logout')}",
    )
    assert_true({"navigation", "page_ready", "selector"} <= set(kinds), f"routes, readiness and selectors are kept: {sorted(kinds)}")
    stored = json.dumps(knowledge.load(root))
    assert_true("${E2E_PASS}" in stored and "typed test data" not in stored,
                "placeholders are kept; a literal typed value is not application knowledge and is dropped")
    submit = next(e for e in kinds["selector"] if e.get("step", {}).get("selector") == {"role": "button", "name": "Masuk"})
    assert_true(submit.get("file") == "src/Login.vue" and submit.get("line") == 3 and submit.get("anchor_hash"),
                f"a selector the codebase named is anchored to its line: {submit}")
    assert_true(knowledge.origin_of(BASE) == "http://app.test" and all(e["origin"] == "http://app.test" for e in knowledge.load(root)),
                "entries belong to the application origin")


def _check_retry_passes_are_not_proof(root: Path) -> None:
    other = _project()
    try:
        summary = knowledge.ingest(other, _report(), _SCENARIO, BASE, "sid-1", clean_first_attempt=False)
        assert_true(summary["added"] == 0 and knowledge.load(other) == [], f"a pass reached by a retry records nothing: {summary}")
    finally:
        shutil.rmtree(other, ignore_errors=True)


def _check_failures_retire_and_prune(root: Path) -> None:
    for session in ("sid-2", "sid-3"):
        knowledge.ingest(root, _report({"submit": "failed"}), _SCENARIO, BASE, session, clean_first_attempt=True)
    submit = [e for e in knowledge.load(root) if e["kind"] == "selector" and e.get("file") == "src/Login.vue"]
    assert_true(submit and submit[0]["stale"] and submit[0]["consecutive_fails"] == 2,
                f"two failures in a row retire the entry: {submit}")
    offered = knowledge.relevant(root, BASE)
    assert_true(not any(e.get("file") == "src/Login.vue" and e["kind"] == "selector" for e in offered),
                "a retired entry is never offered to a draft")
    assert_true(all(e["origin"] == "http://app.test" for e in offered) and knowledge.relevant(root, "http://other.test") == [],
                "and a draft for another origin is offered nothing from this one")
    before = len(knowledge.load(root))
    result = knowledge.prune(root)
    assert_true(result["removed"] >= 1 and len(knowledge.load(root)) == before - result["removed"], f"prune drops retired entries: {result}")


def _check_anchor_moves_and_vanishes(root: Path) -> None:
    other = _project()
    try:
        knowledge.ingest(other, _report(), _SCENARIO, BASE, "sid-1", clean_first_attempt=True)
        source = other / "src" / "Login.vue"
        source.write_text("<!-- moved -->\n" + source.read_text(encoding="utf-8"), encoding="utf-8")
        knowledge.prune(other)
        moved = [e for e in knowledge.load(other) if e.get("file") == "src/Login.vue"]
        assert_true(moved and moved[0]["line"] == 4, f"a line that only moved keeps its entry at the new line: {moved}")
        source.write_text(source.read_text(encoding="utf-8").replace("<button>Masuk</button>", "<button>Login</button>"), encoding="utf-8")
        knowledge.prune(other)
        assert_true(not [e for e in knowledge.load(other) if e.get("file") == "src/Login.vue"],
                    "a line whose content is gone takes its entry with it")
    finally:
        shutil.rmtree(other, ignore_errors=True)


def _check_promotable_after_repeated_proof() -> None:
    other = _project()
    try:
        for n in range(knowledge.PROMOTE_AFTER_PASSES):
            assert_true(knowledge.promotable_claims(other, BASE) == [] or n > 0, "nothing is promotable before it is proven")
            knowledge.ingest(other, _report(), _SCENARIO, BASE, f"sid-{n}", clean_first_attempt=True)
        claims = knowledge.promotable_claims(other, BASE)
        assert_true(
            len(claims) == 1 and claims[0]["sources"][0]["path"] == "src/Login.vue" and claims[0]["sources"][0]["anchor"],
            f"only an anchored entry proven {knowledge.PROMOTE_AFTER_PASSES} times becomes a /.promote claim: {claims}",
        )
    finally:
        shutil.rmtree(other, ignore_errors=True)


def _check_the_next_draft_is_offered_it() -> None:
    from tests.checks.e2e_routing import _adapter, _flow, _workspace

    root = _workspace("e2e-knowledge-flow-")
    try:
        adapter = _adapter()
        draft, result = _flow(root, adapter, "pass", {"E2E_USER": "user@example.test"})
        first_prompt = next(c["prompt"] for c in adapter.calls if c["command"] == "e2e_spec")
        assert_true(
            "e2e_knowledge.json" not in first_prompt and (draft["meta"]["e2e"].get("knowledge") or {}).get("offered") == 0,
            "an empty store offers nothing, and the prompt does not name a sidecar with nothing in it",
        )
        ingested = result["meta"]["e2e"].get("knowledge") or {}
        assert_true(ingested.get("added", 0) > 0, f"the run fed the store: {ingested}")

        adapter = _adapter()
        second, _ = _flow(root, adapter, "pass", {"E2E_USER": "user@example.test"})
        prompt = next(c["prompt"] for c in adapter.calls if c["command"] == "e2e_spec")
        offered = (second["meta"]["e2e"].get("knowledge") or {}).get("offered")
        sidecar = data_dir(root) / "sessions" / "e2e-session" / "runtime" / knowledge.SIDECAR_NAME
        assert_true(offered and "e2e_knowledge.json" in prompt and sidecar.exists(),
                    f"the next draft is pointed at what the run proved: offered={offered}")
        assert_true("user@example.test" not in sidecar.read_text(encoding="utf-8"),
                    "the sidecar holds placeholders, never a resolved credential")
    finally:
        shutil.rmtree(root, ignore_errors=True)


def _test_e2e_knowledge() -> None:
    root = _project()
    try:
        _check_a_run_records_what_it_proved(root)
        _check_retry_passes_are_not_proof(root)
        _check_failures_retire_and_prune(root)
        _check_anchor_moves_and_vanishes(root)
        _check_promotable_after_repeated_proof()
        _check_the_next_draft_is_offered_it()
    finally:
        shutil.rmtree(root, ignore_errors=True)
