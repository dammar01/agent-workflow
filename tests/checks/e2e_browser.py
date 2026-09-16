"""The real player's step semantics, against a stand-in page — no browser, no Playwright.

What is pinned here is the contract the classifier depends on: one progress event per
step, the right error kind, the strongest candidate's provenance on a miss, page
stability on failure, observations shaped like classify.py expects, and the origin
guard refusing what the spec policy refuses. A real Chromium run lives in the smoke
suite; this proves the logic that run relies on.
"""

from __future__ import annotations

import contextlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

from core.evidence.e2e import browser as e2e_browser
from core.evidence.e2e.browser import Session, run_scenario
from core.evidence.e2e.classify import ORIGIN_APP, ORIGIN_HARNESS, ORIGIN_UNKNOWN, build_report, classify_step
from core.evidence.e2e.normalize import evidence_block
from tests.checks.support import assert_true

BASE = "http://localhost:8000"
FakeTimeout = type("TimeoutError", (Exception,), {})


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def _flag(page, element: dict, key: str) -> bool:
    """An element flag, or a callable of the page for one that changes over (fake) time."""
    value = element.get(key, True)
    return bool(value(page) if callable(value) else value)


class _Locator:
    def __init__(self, page, predicate) -> None:
        self.page = page
        self.predicate = predicate

    def _matches(self) -> list[dict]:
        return [el for el in self.page.elements if self.predicate(el)]

    def count(self) -> int:
        return len(self._matches())

    def nth(self, index: int) -> "_Locator":
        return _Locator(self.page, lambda el, index=index: el is self._matches()[index])

    def is_visible(self) -> bool:
        return _flag(self.page, self._matches()[0], "visible")

    def is_enabled(self) -> bool:
        return _flag(self.page, self._matches()[0], "enabled")

    def _inside(self, predicate) -> "_Locator":
        # A child is an element whose "in" names a matched container.
        return _Locator(self.page, lambda el: predicate(el) and any(el.get("in") is not None and el.get("in") == c.get("name") for c in self._matches()))

    def get_by_role(self, role, name=None):
        return self._inside(lambda el: el.get("role") == role and (name is None or el.get("name") == name))

    def get_by_label(self, label):
        return self._inside(lambda el: el.get("label") == label)

    def get_by_test_id(self, testid):
        return self._inside(lambda el: el.get("testid") == testid)

    def get_by_text(self, text):
        return self._inside(lambda el: el.get("text") == text)

    def locator(self, css):
        return self._inside(lambda el: el.get("css") == css)

    def _one(self) -> dict:
        element = self._matches()[0]
        if not element.get("actionable", True):
            raise FakeTimeout("Timeout 8000ms exceeded waiting for element to be enabled")
        return element

    def click(self, timeout=None) -> None:
        element = self._one()
        self.page.clicks.append(element.get("testid") or element.get("name"))
        if element.get("on_click"):
            element["on_click"](self.page)

    def fill(self, value, timeout=None) -> None:
        self._one()["value"] = value

    def select_option(self, value=None, label=None, timeout=None) -> None:
        self._one()["selected"] = value if value is not None else label

    def press(self, key, timeout=None) -> None:
        self._one()["pressed"] = key

    def inner_text(self) -> str:
        return self._matches()[0].get("inner_text", "")


class _Page:
    def __init__(self, elements: list[dict] | None = None, *, title: str = "Home", settled: bool = True) -> None:
        self.elements = elements or []
        self.url = "about:blank"
        self._title = title
        self.settled = settled
        self.clicks: list = []
        self.gotos: list[str] = []
        self.main_frame = object()
        self.goto_error: Exception | None = None
        self.calls: list[str] = []
        self.html = "<html><body><p>page</p></body></html>"
        self.handlers: dict = {}
        self.load_states: list[str] = []

    def goto(self, url, wait_until=None, timeout=None):
        self.gotos.append(url)
        if self.goto_error:
            raise self.goto_error
        self.url = url
        return SimpleNamespace(status=200)

    def title(self) -> str:
        return self._title

    def evaluate(self, script, arg=None):
        if "readyState" in script:
            return "complete"
        if "removeAttribute" in script:
            self.calls.append("clear_inputs")
            return None
        self.calls.append("probe")
        return {"url": self.url, "limit": arg, "headings": ["Masuk"], "buttons": [{"name": "Masuk", "testid": "login-submit"}], "inputs": [], "links": []}

    def content(self) -> str:
        self.calls.append("content")
        return self.html

    def screenshot(self, path=None, **_kwargs) -> None:
        self.calls.append("screenshot")
        Path(path).write_bytes(b"\x89PNG fake")

    def on(self, name, handler) -> None:
        self.handlers[name] = handler

    def wait_for_load_state(self, state, timeout=None) -> None:
        self.load_states.append(state)
        if not self.settled:
            raise FakeTimeout(f"{state} not reached")

    def get_by_role(self, role, name=None):
        return _Locator(self, lambda el: el.get("role") == role and (name is None or el.get("name") == name))

    def get_by_label(self, label):
        return _Locator(self, lambda el: el.get("label") == label)

    def get_by_test_id(self, testid):
        return _Locator(self, lambda el: el.get("testid") == testid)

    def get_by_text(self, text):
        return _Locator(self, lambda el: el.get("text") == text)

    def locator(self, css):
        return _Locator(self, lambda el: el.get("css") == css)


def _session(page: _Page, **config) -> tuple[Session, list[dict], _Clock]:
    events: list[dict] = []
    clock = _Clock()
    merged = {"base_url": BASE, "step_timeout_ms": 500, "nav_timeout_ms": 1000, "probe_max_elements": 7, **config}
    return Session(page, merged, events.append, clock=clock, sleep=clock.advance), events, clock


def _progress(events: list[dict]) -> list[dict]:
    return [e for e in events if e["type"] == "progress"]


def _login_page() -> _Page:
    def to_dashboard(page: _Page) -> None:
        page.url = BASE + "/dashboard"
        page._title = "Dashboard"

    return _Page(
        [
            {"role": "textbox", "label": "Email"},
            {"role": "button", "name": "Masuk", "testid": "login-submit", "on_click": to_dashboard},
            {"testid": "banner", "inner_text": "Welcome back, operator"},
        ]
    )


_CLAIMS = [{"id": "login", "severity": "blocking", "source_refs": ["src/Login.tsx:1"]}]


def _test_e2e_browser_session() -> None:
    # --- a passing flow: every action, one event per step, no typed value on the wire --------------
    page = _login_page()
    session, events, _ = _session(page)
    scenario = {
        "claims": _CLAIMS,
        "steps": [
            {"action": "goto", "url": "/login"},
            {"action": "fill", "selector": {"label": "Email"}, "selector_provenance": {"type": "source"}, "value": "typed-secret-value"},
            {"action": "click", "selector_candidates": [
                {"selector": {"role": "button", "name": "Sign in"}, "selector_provenance": {"type": "source"}},
                {"selector": {"testid": "login-submit"}, "selector_provenance": {"type": "existing_test"}},
            ]},
            {"action": "expect_url", "contains": "/dashboard", "claim_id": "login"},
            {"action": "expect_title", "equals": "Dashboard", "claim_id": "login"},
            {"action": "expect_dom", "selector": {"testid": "banner"}, "text": "Welcome back", "claim_id": "login"},
            {"action": "probe"},
        ],
    }
    session.run(scenario)
    progress = _progress(events)
    assert_true([e["status"] for e in progress] == ["passed"] * 7, f"every step passes: {[(e['action'], e['status'], e.get('error')) for e in progress]}")
    assert_true(page.gotos == [BASE + "/login"], f"a relative goto resolves under base_url: {page.gotos}")
    assert_true(progress[2]["selector_provenance"] == "existing_test", "the candidate that matched reports its own provenance")
    assert_true(
        progress[2]["selection"] == {"candidate": 1, "match_counts": [0, 1], "fallback_used": True},
        f"the runtime mapping names the candidate used, each candidate's match count, and the fallback: {progress[2]['selection']}",
    )
    assert_true([e["step_id"] for e in progress][:2] == ["step-1", "step-2"], "a step without an id still gets a stable one in events")
    assert_true("typed-secret-value" not in json.dumps(events), "a typed value never comes back on the wire")
    assert_true(progress[6]["actual"]["probe"]["limit"] == 7, "the probe is capped by settings.probe_max_elements")
    report = build_report([*events, {"type": "result", "status": "finished"}], scenario)
    assert_true(report["browser_verdict"] == "pass", f"the classifier reads the stream as pass: {report['reason']}")

    # --- a grounded selector gone from a settled page is the app's; the rest is skipped ----------
    page = _login_page()
    session, events, _ = _session(page)
    session.run({"steps": [
        {"action": "click", "selector": {"role": "button", "name": "Logout"}, "selector_provenance": {"type": "source"}},
        {"action": "expect_url", "contains": "/", "claim_id": "login"},
    ]})
    first, second = _progress(events)
    assert_true(first["error"]["kind"] == "selector_missing" and first["page_stable"] is True, f"missing + settled: {first}")
    assert_true(classify_step(first) == ORIGIN_APP, "grounded + settled classifies as app")
    assert_true(second["status"] == "skipped", "a step after a failure proves nothing and is skipped")

    # --- every candidate missing: the failure carries the STRONGEST candidate's provenance -------
    session, events, _ = _session(_login_page())
    session.run({"steps": [{"action": "click", "selector_candidates": [
        {"selector": {"role": "button", "name": "Logout"}, "selector_provenance": {"type": "source"}},
        {"selector": {"css": "#logout"}, "selector_provenance": {"type": "heuristic"}},
    ]}]})
    missed = _progress(events)[0]
    assert_true(missed["selector_provenance"] == "source" and classify_step(missed) == ORIGIN_APP, f"a grounded selector that vanished is not excused by a heuristic fallback: {missed}")

    # --- the same miss on a heuristic selector is the harness's; on an unsettled page, unknown ----
    session, events, _ = _session(_login_page())
    session.run({"steps": [{"action": "click", "selector": {"css": "#logout"}, "selector_provenance": {"type": "heuristic"}}]})
    assert_true(classify_step(_progress(events)[0]) == ORIGIN_HARNESS, "a heuristic miss is the harness's")
    session, events, _ = _session(_Page([{"role": "button", "name": "Masuk"}], settled=False))
    session.run({"steps": [{"action": "click", "selector": {"role": "button", "name": "Logout"}, "selector_provenance": {"type": "source"}}]})
    assert_true(_progress(events)[0]["page_stable"] is False and classify_step(_progress(events)[0]) == ORIGIN_UNKNOWN, "unsettled page: unknown")

    # --- ambiguity, hidden elements, non-actionable elements --------------------------------------
    session, events, _ = _session(_Page([{"role": "button", "name": "Save"}, {"role": "button", "name": "Save"}]))
    session.run({"steps": [{"action": "click", "selector": {"role": "button", "name": "Save"}}]})
    assert_true(_progress(events)[0]["error"]["kind"] == "selector_ambiguous", "two matches are ambiguous, not a coin flip")
    assert_true(_progress(events)[0]["selection"] == {"candidate": None, "match_counts": [2], "fallback_used": False}, f"and the report says how many matched: {_progress(events)[0]['selection']}")

    # --- `within` scopes a selector: the modal's Simpan, not the page's ---------------------------
    page = _Page([
        {"role": "button", "name": "Simpan"},
        {"role": "dialog", "name": "Tambah barang"},
        {"role": "button", "name": "Simpan", "in": "Tambah barang", "testid": "modal-save"},
    ])
    session, events, _ = _session(page)
    session.run({"steps": [{"id": "save", "action": "click", "selector": {"role": "button", "name": "Simpan"}, "within": {"role": "dialog", "name": "Tambah barang"}}]})
    assert_true(_progress(events)[0]["status"] == "passed" and page.clicks == ["modal-save"], f"the scoped selector clicks the modal's button only: {page.clicks} {_progress(events)[0].get('error')}")

    # --- a timed-out write action is never sent twice ----------------------------------------------
    def dispatched_then_timeout(p: _Page) -> None:
        raise FakeTimeout("Timeout 500ms exceeded waiting for navigation after click")

    page = _Page([{"role": "button", "name": "Kirim", "on_click": dispatched_then_timeout}])
    session, events, _ = _session(page)
    session.run({"steps": [{"id": "send", "action": "click", "selector": {"role": "button", "name": "Kirim"}}, {"id": "after", "action": "expect_url", "contains": "/", "claim_id": "login"}]})
    assert_true(page.clicks == ["Kirim"] and _progress(events)[0]["status"] == "failed", f"one dispatch, then a failed step with evidence: {page.clicks}")
    assert_true(_progress(events)[1]["status"] == "skipped", "and nothing after it runs")

    # --- readiness: wait for the app's own indicator, then act once --------------------------------
    page = _Page()
    session, events, clock = _session(page, step_timeout_ms=2000)
    page.elements = [
        {"testid": "loader", "visible": lambda p: clock.now < 0.5},
        {"role": "button", "name": "Simpan", "enabled": lambda p: clock.now >= 0.3},
        {"text": "3 items"},
    ]
    button = {"role": "button", "name": "Simpan"}
    session.run({"steps": [
        {"id": "open", "action": "goto", "url": "/items", "ready": [{"text": "3 items"}]},
        {"id": "save", "action": "click", "selector": button, "ready": [{"hidden": {"testid": "loader"}}, {"enabled": button}]},
    ]})
    opened, saved = _progress(events)
    assert_true(opened["status"] == "passed" and opened["ready"]["unmet"] == [], f"a goto waits for its content after navigating: {opened}")
    assert_true(
        saved["status"] == "passed" and saved["ready"]["waited_ms"] >= 500 and saved["ready"]["unmet"] == [] and page.clicks == ["Simpan"],
        f"the click waited for the loader to go and the button to enable, then ran once: {saved.get('ready')} {page.clicks}",
    )
    page = _Page()
    session, events, clock = _session(page, step_timeout_ms=1000)
    page.elements = [{"testid": "loader"}, {"role": "button", "name": "Simpan"}]
    session.run({"steps": [{"id": "save", "action": "click", "selector": button, "ready": [{"hidden": {"testid": "loader"}}]}]})
    stuck = _progress(events)[0]
    assert_true(
        stuck["error"]["kind"] == "not_ready" and "hidden" in stuck["error"]["detail"] and stuck["page_stable"] is False and page.clicks == [],
        f"a loader that never goes fails the step before any action, naming the condition: {stuck}",
    )
    assert_true(classify_step(stuck) == ORIGIN_UNKNOWN, "a page that never became ready is unknown, not the app's")
    assert_true("networkidle" not in page.load_states, f"stability never waits for network idle (a polling page never reaches it): {page.load_states}")
    session, events, _ = _session(_Page([{"testid": "toast", "visible": False}]))
    session.run({"steps": [{"action": "expect_dom", "selector": {"testid": "toast"}, "claim_id": "login"}]})
    assert_true(_progress(events)[0]["error"]["kind"] == "not_visible", "an element that stays hidden fails expect_dom as not_visible")
    session, events, _ = _session(_Page([{"role": "button", "name": "Pay", "actionable": False}]))
    session.run({"steps": [{"action": "click", "selector": {"role": "button", "name": "Pay"}, "selector_provenance": {"type": "source"}}]})
    assert_true(_progress(events)[0]["error"]["kind"] == "not_visible", "resolved but never actionable reports not_visible")

    # --- assertions poll, then fail with expected vs actual; long polls keep the wire alive ------
    page = _Page()
    page.url = BASE + "/login?error=1"
    session, events, _ = _session(page, step_timeout_ms=5000)
    session.run({"steps": [{"action": "expect_url", "contains": "/dashboard", "claim_id": "login"}]})
    failed = _progress(events)[0]
    assert_true(failed["error"]["kind"] == "assertion" and failed["actual"] == {"url": BASE + "/login?error=1"}, f"url assertion: {failed}")
    assert_true(failed["expected"] == {"contains": "/dashboard"} and classify_step(failed) == ORIGIN_APP, "expected is carried, origin app")
    assert_true(any(e["type"] == "heartbeat" for e in events), "a 5s poll emits heartbeats so the idle timeout does not fire")
    session, events, _ = _session(_Page())
    session.run({"steps": [{"action": "expect_title", "matches": "([unclosed", "claim_id": "login"}]})
    assert_true(classify_step(_progress(events)[0]) == ORIGIN_HARNESS, "an invalid pattern is the spec's fault, not the app's")
    page = _Page([{"testid": "banner", "inner_text": "Goodbye"}])
    session, events, _ = _session(page)
    session.run({"steps": [{"action": "expect_dom", "selector": {"testid": "banner"}, "text": "Welcome", "claim_id": "login"}]})
    assert_true(_progress(events)[0]["error"]["kind"] == "assertion" and _progress(events)[0]["actual"] == {"text": "Goodbye"}, "element text mismatch is an assertion")

    # --- navigation: timeouts are unknown; the origin guard is harness, before and after goto -----
    page = _Page()
    page.goto_error = FakeTimeout("Timeout 1000ms exceeded")
    session, events, _ = _session(page)
    session.run({"steps": [{"action": "goto", "url": "/slow"}]})
    assert_true(_progress(events)[0]["error"]["kind"] == "timeout" and classify_step(_progress(events)[0]) == ORIGIN_UNKNOWN, "a navigation timeout is unknown")
    page = _Page()
    session, events, _ = _session(page)
    session.run({"steps": [{"action": "goto", "url": "https://evil.example/"}]})
    assert_true(page.gotos == [] and _progress(events)[0]["error"]["kind"] == "navigation_blocked", "an off-origin goto never reaches the page")

    routes: list[str] = []
    route = SimpleNamespace(abort=lambda code: routes.append(f"abort:{code}"), continue_=lambda: routes.append("continue"))
    page = _Page()
    session, events, _ = _session(page)

    def request(url, *, navigation=True, main=True):
        return SimpleNamespace(url=url, is_navigation_request=lambda: navigation, frame=page.main_frame if main else object())

    session.guard(route, request("https://evil.example/phish"))
    session.guard(route, request(BASE + "/dashboard"))
    session.guard(route, request("https://cdn.example/app.js", navigation=False))
    session.guard(route, request("https://video.example/embed", main=False))
    assert_true(routes == ["abort:blockedbyclient", "continue", "continue", "continue"], f"only top-level off-origin navigation is refused: {routes}")

    def redirect_off_origin(p: _Page) -> None:
        session.guard(route, request("https://sso.evil.example/login"))

    page.elements = [{"role": "button", "name": "Continue", "on_click": redirect_off_origin}]
    session.run({"steps": [{"action": "click", "selector": {"role": "button", "name": "Continue"}, "selector_provenance": {"type": "source"}}]})
    blocked = _progress(events)[0]
    assert_true(blocked["error"]["kind"] == "navigation_blocked" and classify_step(blocked) == ORIGIN_HARNESS, f"a click that navigates off-origin fails as harness: {blocked}")
    assert_true("sso.evil.example" in blocked["error"]["detail"] and "/login" not in blocked["error"]["detail"], "the detail names the origin, not the full URL")

    # --- write guard: allow_side_effects false refuses non-GET/HEAD/OPTIONS on any origin -------------
    def call(method, url, *, resource_type="fetch", navigation=False, **config):
        seen: list[str] = []
        hop = SimpleNamespace(abort=lambda code: seen.append(f"abort:{code}"), continue_=lambda: seen.append("continue"))
        guarded, emitted, _ = _session(_Page(), **config)
        guarded.guard(hop, SimpleNamespace(url=url, method=method, resource_type=resource_type,
                                           is_navigation_request=lambda: navigation, frame=object()))
        guarded.ledger = emitted
        return seen, guarded

    for method in ("GET", "HEAD", "OPTIONS", "get"):
        seen, guarded = call(method, BASE + "/api/items")
        assert_true(seen == ["continue"] and guarded.ledger == [], f"{method} is a read, passes, and is not a ledger entry")
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        seen, guarded = call(method, BASE + "/api/items/7?token=abc")
        assert_true(seen == ["abort:blockedbyclient"], f"{method} is refused while allow_side_effects is false: {seen}")
        assert_true(
            guarded.mutations_blocked == [{"method": method, "origin": BASE, "resource_type": "fetch", "attributed": False, "reason": "side_effects_off"}],
            f"the record keeps the origin, never the path or query: {guarded.mutations_blocked}",
        )
        record = guarded.ledger[0]
        assert_true(
            record["type"] == "request" and record["blocked"] and record["endpoint"] == BASE + "/api/items/:id" and "token" not in json.dumps(record)
            and record["attribution"] == "uncertain" and record["planned"] is None and record["duration_ms"] == 0,
            f"a refused write enters the ledger at once, route kept, id and query gone: {record}",
        )
    assert_true(call("POST", "http://localhost:9000/api/items")[0] == ["abort:blockedbyclient"], "an API on another port is still refused")
    assert_true(call("POST", "https://analytics.example/collect", resource_type="ping")[0] == ["abort:blockedbyclient"], "a third-party beacon is refused too")
    assert_true(call("POST", BASE + "/api/items", allow_side_effects=True)[0] == ["continue"], "allow_side_effects true sends a write to a loopback host")
    for url in ("http://127.0.0.1:9000/api/items", "http://[::1]:8080/x", "http://app.localhost/x", "http://127.0.0.2/x"):
        assert_true(call("DELETE", url, allow_side_effects=True)[0] == ["continue"], f"every loopback form counts: {url}")
    seen, guarded = call("POST", "https://api.example/items", allow_side_effects=True)
    assert_true(
        seen == ["abort:blockedbyclient"] and guarded.mutations_blocked[0]["reason"] == "non_loopback" and "loopback" in guarded.ledger[0]["failure"],
        f"with side effects on, a write to a remote host is still refused — the request's host decides, not base_url's: {seen} {guarded.mutations_blocked}",
    )
    assert_true(call("POST", BASE + "/login", allowed_mutation_paths=["/login"])[0] == ["abort:blockedbyclient"], "a leftover allowed_mutation_paths opens nothing")
    # A host preflight resolved to a private address and pinned the browser to may take a
    # write. Only that host: the approval is a list of names, not a relaxed rule.
    assert_true(
        call("POST", "http://app.test/api/items", allow_side_effects=True, write_hosts=["app.test"])[0] == ["continue"],
        "a write host preflight approved and pinned may take a write",
    )
    seen, guarded = call("POST", "http://other.test/api/items", allow_side_effects=True, write_hosts=["app.test"])
    assert_true(
        seen == ["abort:blockedbyclient"] and guarded.mutations_blocked[0]["reason"] == "non_loopback",
        f"a sibling name nobody approved is not covered by it: {seen}",
    )
    assert_true(
        call("POST", "http://app.test/api/items", allow_side_effects=True)[0] == ["abort:blockedbyclient"],
        "and without the approval the same name is refused: the guard trusts the decision, not the suffix",
    )

    # --- confirmed reads: a POST the user confirmed as read-only passes; a GraphQL write never does ---
    import json as _json

    from core.evidence.e2e.browser import read_only_refusal

    def post(url, body=None, **config):
        seen: list[str] = []
        hop = SimpleNamespace(abort=lambda code: seen.append(f"abort:{code}"), continue_=lambda: seen.append("continue"))
        guarded, _, _ = _session(_Page(), **config)
        guarded.guard(hop, SimpleNamespace(url=url, method="POST", resource_type="fetch", post_data=body,
                                           is_navigation_request=lambda: False, frame=object()))
        return seen, guarded

    reads = {"allowed_read_only_requests": ["POST /api/search", "POST /graphql"]}
    seen, guarded = post(BASE + "/api/search?page=2", '{"q": "laptop"}', **reads)
    assert_true(
        seen == ["continue"] and guarded.read_only_allowed == [{"method": "POST", "origin": BASE}] and guarded.mutations_blocked == [],
        f"a confirmed read passes and is recorded as a read, origin only: {seen} {guarded.read_only_allowed}",
    )
    assert_true(post(BASE + "/api/search/export", "{}", **reads)[0] == ["abort:blockedbyclient"], "a confirmed read is exact, not a prefix")
    assert_true(post("http://localhost:9000/api/search", "{}", **reads)[0] == ["abort:blockedbyclient"], "a bare path belongs to base_url's origin only")
    assert_true(post(BASE + "/api/search", "{}")[0] == ["abort:blockedbyclient"], "nothing is a read until the request settings name it")
    assert_true(post(BASE + "/graphql", _json.dumps({"query": "query Items { items { id } }"}), **reads)[0] == ["continue"], "a GraphQL query is a read")
    seen, guarded = post(BASE + "/graphql", _json.dumps({"query": "mutation Drop { deleteItem(id: 7) { id } }"}), **reads)
    assert_true(
        seen == ["abort:blockedbyclient"] and guarded.mutations_blocked[0].get("refusal") == "a GraphQL mutation or subscription" and guarded.read_only_allowed == [],
        f"a GraphQL mutation on a confirmed read endpoint is still refused: {seen} {guarded.mutations_blocked}",
    )

    class _BinaryBody:
        url = BASE + "/api/search"
        method = "POST"
        resource_type = "fetch"
        frame = object()

        def is_navigation_request(self):
            return False

        @property
        def post_data(self):
            raise UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

    binary_hop: list[str] = []
    binary_session, _, _ = _session(_Page(), **reads)
    binary_session.guard(SimpleNamespace(abort=lambda code: binary_hop.append(code), continue_=lambda: binary_hop.append("continue")), _BinaryBody())
    assert_true(binary_hop == ["blockedbyclient"] and "cannot be read" in binary_session.mutations_blocked[0]["refusal"], f"an unreadable body is refused, not guessed: {binary_hop}")

    graphql_write = "a GraphQL mutation or subscription"
    for body, expected in (
        (None, None),
        ('{"q": "mutation { x }"}', None),
        ('{"query": "{ items(filter: \\"mutation {\\") { id } }"}', None),
        ('{"query": "# mutation {\\n{ items { id } }"}', None),
        ('[{"query": "{ a }"}, {"query": "mutation { b }"}]', graphql_write),
        ('{"query": "subscription OnItem { item { id } }"}', graphql_write),
        ('{"query": "mutation($id: ID!) { drop(id: $id) }"}', graphql_write),
        ('{"extensions": {"persistedQuery": {"sha256Hash": "abc"}}}', "a persisted GraphQL query, whose operation cannot be read"),
        ("query=mutation%20%7B%20x%20%7D", graphql_write),
        ("mutation { x }", graphql_write),
        ("q=laptop&page=2", None),
    ):
        assert_true(read_only_refusal(body) == expected, f"read_only_refusal({body!r}) = {read_only_refusal(body)!r}, expected {expected!r}")

    # A confirmed read inside a step passes the step and surfaces once, as a counted observation.
    page = _Page()
    session, events, _ = _session(page, **reads)

    def search(p: _Page) -> None:
        for _ in range(2):
            session.guard(route, SimpleNamespace(url=BASE + "/api/search?q=secret-term", method="POST", resource_type="fetch", post_data="{}",
                                                 is_navigation_request=lambda: False, frame=object()))

    page.elements = [{"role": "button", "name": "Search", "on_click": search}]
    session.run({"steps": [{"action": "click", "selector": {"role": "button", "name": "Search"}, "claim_id": "login"}]})
    assert_true(_progress(events)[0]["status"] == "passed", f"a confirmed read does not fail its step: {_progress(events)[0]}")
    allowed = [e for e in events if e["type"] == "observation" and e["kind"] == "read_only_request_allowed"]
    assert_true(
        len(allowed) == 1 and allowed[0]["url"] == BASE and allowed[0]["detail"].startswith("2 POST") and "secret-term" not in _json.dumps(events),
        f"one counted observation per method and origin, no path or query: {allowed}",
    )
    report = build_report([*events, {"type": "result", "status": "finished"}], {"claims": _CLAIMS})
    assert_true(report["browser_verdict"] == "pass" and report["app_errors"] == [], f"a confirmed read is a warning-class record, never an app error: {report['browser_verdict']}")

    page = _Page()
    session, events, _ = _session(page, **reads)

    def graphql_drop(p: _Page) -> None:
        session.guard(route, SimpleNamespace(url=BASE + "/graphql", method="POST", resource_type="fetch", post_data=_json.dumps({"query": "mutation { drop }"}),
                                             is_navigation_request=lambda: False, frame=object()))

    page.elements = [{"role": "button", "name": "Drop", "on_click": graphql_drop}]
    session.run({"steps": [{"action": "click", "selector": {"role": "button", "name": "Drop"}, "claim_id": "login"}]})
    dropped = _progress(events)[0]
    assert_true(
        dropped["error"]["kind"] == "mutation_blocked" and "allowed_read_only_requests covers the endpoint" in dropped["error"]["detail"] and "GraphQL" in dropped["error"]["detail"],
        f"the step names why a confirmed endpoint was still refused: {dropped['error']}",
    )

    # A click whose write is refused fails that step as harness, whatever the step saw after it.
    page = _Page()
    session, events, _ = _session(page)

    def submit_delete(p: _Page) -> None:
        session.guard(route, SimpleNamespace(url=BASE + "/api/items/7", method="DELETE", resource_type="fetch",
                                             is_navigation_request=lambda: False, frame=object()))

    page.elements = [{"role": "button", "name": "Delete", "on_click": submit_delete}]
    session.run({"steps": [{"action": "click", "selector": {"role": "button", "name": "Delete"}, "claim_id": "login"},
                           {"action": "expect_url", "contains": "/items"}]})
    refused = _progress(events)[0]
    assert_true(refused["status"] == "failed" and refused["error"]["kind"] == "mutation_blocked", f"the refused write fails its step: {refused}")
    assert_true(classify_step(refused) == ORIGIN_HARNESS, "a refused write is the harness's, never the app's")
    assert_true("DELETE" in refused["error"]["detail"] and "/api/items" not in refused["error"]["detail"], f"the detail names method and origin only: {refused['error']}")
    assert_true(_progress(events)[1]["status"] == "skipped", "the steps after it prove nothing and are skipped")
    report = build_report([*events, {"type": "result", "status": "finished"}], {"claims": _CLAIMS})
    assert_true(report["browser_verdict"] == "incomplete" and report["reason"] == "harness_error", f"a refused write leaves the run incomplete, not failed: {report['browser_verdict']} {report['reason']}")
    assert_true(not [e for e in events if e.get("kind") == "mutation_blocked" and e["type"] == "observation"], "an attributed write is not reported twice")

    # A beacon never decides a step; it surfaces after the run as a warning observation.
    page = _Page()
    session, events, _ = _session(page)

    def beacon(p: _Page) -> None:
        session.guard(route, SimpleNamespace(url="https://analytics.example/collect?uid=9", method="POST", resource_type="ping",
                                             is_navigation_request=lambda: False, frame=object()))

    page.elements = [{"role": "button", "name": "Open", "on_click": beacon}]
    session.run({"steps": [{"action": "click", "selector": {"role": "button", "name": "Open"}, "claim_id": "login"}]})
    assert_true(_progress(events)[0]["status"] == "passed", f"a refused beacon does not fail the step: {_progress(events)[0]}")
    warnings = [e for e in events if e["type"] == "observation" and e["kind"] == "mutation_blocked"]
    assert_true(len(warnings) == 1 and warnings[0]["url"] == "https://analytics.example" and not warnings[0]["same_origin"], f"one observation, origin only: {warnings}")
    report = build_report([*events, {"type": "result", "status": "finished"}], {"claims": _CLAIMS})
    assert_true(report["browser_verdict"] == "pass" and report["app_errors"] == [], f"the beacon stays a warning: {report['browser_verdict']} {report['app_errors']}")

    # --- request ledger: attributed to the step's window, status and duration from the lifecycle ----
    from core.evidence.e2e.redact import sanitize_endpoint

    for url, expected in (
        ("http://localhost:8000/items/42?token=abc#x", "http://localhost:8000/items/:id"),
        ("https://user:pw@api.example:443/v1/reset/3f2a9c1e0b7d4e5f6a7b8c9d", "https://api.example/v1/reset/:id"),
        ("http://127.0.0.1/users/someone%40internal.example/avatar", "http://127.0.0.1/users/:id/avatar"),
        ("http://[::1]:9000/orders/0b8e6a5c-2f4d-4e1a-9c3b-7d6e5f4a3b2c", "http://[::1]:9000/orders/:id"),
        ("http://localhost/api/v2/items", "http://localhost/api/v2/items"),
        ("http://localhost:bad/x", "[unparseable url]"),
    ):
        assert_true(sanitize_endpoint(url) == expected, f"sanitize_endpoint({url!r}) = {sanitize_endpoint(url)!r}, expected {expected!r}")

    page = _Page()
    session, events, clock = _session(page, allow_side_effects=True)
    created = SimpleNamespace(url=BASE + "/items/42?csrf=abc", method="POST", resource_type="fetch", is_navigation_request=lambda: False, frame=object(), failure=None)
    extra = SimpleNamespace(url=BASE + "/audit", method="POST", resource_type="fetch", is_navigation_request=lambda: False, frame=object(), failure=None)
    late = SimpleNamespace(url=BASE + "/autosave", method="PATCH", resource_type="xhr", is_navigation_request=lambda: False, frame=object(), failure="net::ERR_ABORTED")
    hanging = SimpleNamespace(url=BASE + "/export", method="POST", resource_type="fetch", is_navigation_request=lambda: False, frame=object(), failure=None)

    def save(p: _Page) -> None:
        for sent, status in ((created, 201), (extra, 204)):
            session.guard(route, sent)
            clock.advance(0.25)
            session.on_response(SimpleNamespace(status=status, url=sent.url, request=sent, frame=p.main_frame))
            session.on_request_finished(sent)

    page.elements = [{"role": "button", "name": "Simpan", "on_click": save}]
    session.run({"steps": [{"id": "create-item", "action": "click", "selector": {"role": "button", "name": "Simpan"},
                            "request": {"method": "POST", "path": "/items/:id"}}]})
    session.guard(route, late)
    session.on_request_failed(late)
    session.guard(route, hanging)
    session.flush_requests()
    ledger = [e for e in events if e["type"] == "request"]
    first, second, third, fourth = ledger
    assert_true(
        first["step_id"] == "create-item" and first["attribution"] == "step" and first["planned"] is True
        and first["status"] == 201 and first["duration_ms"] == 250 and first["endpoint"] == BASE + "/items/:id" and "csrf" not in json.dumps(ledger),
        f"the planned write: its step, planned, status, duration, sanitised endpoint: {first}",
    )
    assert_true(second["planned"] is False and second["status"] == 204, f"an extra write in the same window is recorded as not planned: {second}")
    assert_true(
        third["attribution"] == "uncertain" and third["step_id"] is None and third["after_step"] == "create-item" and third["planned"] is None and "ERR_ABORTED" in third["failure"],
        f"a write after the window is uncertain, never pinned to the last step: {third}",
    )
    assert_true(fourth["failure"] == "no response before the run ended", f"a write still waiting at the end is reported, not dropped: {fourth}")
    report = build_report([*events, {"type": "result", "status": "finished"}], {"claims": []})
    assert_true(
        report["trail"][0]["requests"] == [first["id"], second["id"]] and len(report["requests"]) == 4,
        f"the trail ties the step to its requests: {report['trail']}",
    )
    block = evidence_block(report)
    assert_true("trail:" in block and "requests:" in block and "HTTP 201" in block and "uncertain (after create-item)" in block, f"the reviewer sees the trail and the ledger:\n{block}")

    # --- cleanup: runs after the steps, pass or fail, reported apart ---------------------------------
    from core.evidence.e2e.normalize import to_verification

    def cleanup_page(*, delete_button: bool = True) -> _Page:
        page = _Page()

        def to_items(p: _Page) -> None:
            p.url = BASE + "/items"

        page.elements = [{"role": "button", "name": "Simpan", "on_click": to_items}, {"testid": "row", "inner_text": "e2e-item-1"}]
        if delete_button:
            page.elements.append({"role": "button", "name": "Hapus e2e-item-1", "on_click": to_items})
        return page

    crud = {
        "claims": _CLAIMS,
        "steps": [
            {"id": "create-item", "action": "click", "selector": {"role": "button", "name": "Simpan"}, "side_effect": "creates_test_data"},
            {"id": "assert-row", "action": "expect_dom", "selector": {"testid": "row"}, "text": "e2e-item-1", "claim_id": "login"},
        ],
        "cleanup": [
            {"id": "delete-item", "cleans": "create-item", "action": "click", "selector": {"role": "button", "name": "Hapus e2e-item-1"}},
            {"id": "assert-gone", "cleans": "create-item", "action": "expect_url", "contains": "/items"},
        ],
    }
    clean_review = "[VERIFICATION]\nverdict: DONE\n\nblocking_findings:\n- none\n\nescalations:\n- none\n\nnotes:\n- none\n\nchecks_run:\n- read the code\n\nnot_verified:\n- none\n\nconfidence: high — fine\n"

    def crud_run(page: _Page, scenario: dict = crud, **config) -> tuple[list[dict], dict]:
        session, events, _ = _session(page, **config)
        session.run(scenario)
        return events, build_report([*events, {"type": "result", "status": "finished"}], scenario)

    events, report = crud_run(cleanup_page())
    cleanup_events = [e for e in events if e["type"] == "cleanup"]
    assert_true([e["status"] for e in cleanup_events] == ["passed", "passed"] and report["cleanup"]["status"] == "passed", f"a clean cleanup: {cleanup_events}")
    assert_true(report["browser_verdict"] == "pass" and to_verification(report, reviewer_content=clean_review)["verdict"] == "pass", "test and cleanup both clean: pass")
    assert_true(events.index(next(e for e in events if e["type"] == "cleanup")) > max(i for i, e in enumerate(events) if e["type"] == "progress"), "cleanup runs after every test step")

    events, report = crud_run(cleanup_page(delete_button=False))
    assert_true(
        [e["status"] for e in events if e["type"] == "cleanup"] == ["failed", "skipped"] and report["cleanup"]["status"] == "failed",
        f"a failed cleanup step stops its group: {[e['status'] for e in events if e['type'] == 'cleanup']}",
    )
    assert_true(report["browser_verdict"] == "pass" and report["claims"]["login"]["status"] == "proven", "the test's own result is untouched by its cleanup")
    from core.evidence.contract import validate_verification_contract, verify_exit_status

    norm = to_verification(report, reviewer_content=clean_review)
    assert_true(
        norm["verdict"] == "incomplete" and norm["declared"] == "INCOMPLETE" and "e2e cleanup: 'create-item' failed" in norm["content"]
        and verify_exit_status(norm["verdict"], validate_verification_contract(norm["content"])) != 0,
        f"a failed cleanup makes a passing test incomplete with a non-zero exit: {norm['verdict']}/{norm['declared']}\n{norm['content']}",
    )
    assert_true("e2e:login: pass" in norm["content"], "and the proven claim is still reported as proven")

    failing_test = json.loads(json.dumps(crud))
    failing_test["steps"][1]["text"] = "something else"
    events, report = crud_run(cleanup_page(), failing_test)
    assert_true(report["browser_verdict"] == "fail" and report["cleanup"]["status"] == "passed", f"a failed test still cleans up: {report['browser_verdict']} {report['cleanup']['status']}")
    assert_true(to_verification(report, reviewer_content=clean_review)["verdict"] == "fail", "and a fail stays a fail")

    never = json.loads(json.dumps(crud))
    never["steps"].insert(0, {"id": "gate", "action": "expect_url", "contains": "/nowhere", "claim_id": "login"})
    events, report = crud_run(cleanup_page(), never)
    assert_true(report["cleanup"]["status"] == "not_needed", f"nothing was created, nothing to clean: {report['cleanup']}")

    killed = [e for e in crud_run(cleanup_page())[0] if e["type"] != "cleanup"]
    report = build_report([*killed, {"type": "result", "status": "finished"}], crud)
    assert_true(report["cleanup"]["status"] == "not_run" and to_verification(report, reviewer_content=clean_review)["verdict"] == "incomplete", "a cleanup the player never reached is not_run, never a pass")

    slow = cleanup_page()
    slow.elements[0]["on_click"] = lambda p: session_clock.advance(6)
    session, events, session_clock = _session(slow, total_timeout_s=10, step_timeout_ms=2000)
    session.run(crud)
    steps_seen = _progress(events)
    assert_true(
        steps_seen[1]["status"] == "skipped" and "for cleanup" in steps_seen[1].get("detail", "") and [e["status"] for e in events if e["type"] == "cleanup"] == ["passed", "passed"],
        f"test steps stop early to leave cleanup its share of total_timeout_s: {[(e['step_id'], e['status']) for e in steps_seen]}",
    )

    # --- observers: shapes the classifier reads, third-party kept apart ----------------------------
    page = _Page()
    page.url = BASE + "/dashboard"
    session, events, _ = _session(page)
    same = SimpleNamespace(is_navigation_request=lambda: True)
    session.on_response(SimpleNamespace(status=503, url=BASE + "/dashboard", request=same, frame=page.main_frame))
    session.on_response(SimpleNamespace(status=503, url="https://analytics.example/beacon", request=SimpleNamespace(is_navigation_request=lambda: False), frame=page.main_frame))
    session.on_response(SimpleNamespace(status=404, url=BASE + "/missing", request=same, frame=page.main_frame))
    session.on_page_error(SimpleNamespace(message="TypeError: x is undefined"))
    session.on_console(SimpleNamespace(type="error", text="boom", location={"url": BASE + "/app.js"}))
    session.on_console(SimpleNamespace(type="log", text="hello", location={}))
    kinds = [(e["kind"], e["same_origin"], e["main_request"]) for e in events]
    assert_true(
        kinds == [("http_5xx", True, True), ("http_5xx", False, False), ("page_error", True, False), ("console_error", True, False)],
        f"observations: 5xx main + third-party, page error, console error; 404 and logs ignored: {kinds}",
    )
    report = build_report([*events, {"type": "result", "status": "finished"}], {"claims": []})
    assert_true(report["browser_verdict"] == "fail" and len(report["app_errors"]) == 2, "same-origin main 5xx and page error fail the run")

    # A page error is attributed to the script that threw (its stack), not to the page URL:
    # a third-party widget failing on our page is noise. Found against real Chromium.
    session, events, _ = _session(page)
    session.on_page_error(SimpleNamespace(message="widget failed", stack="Uncaught Error: widget failed\n    at widget (https://widgets.example/w.js:0:29)"))
    session.on_page_error(SimpleNamespace(message="own boom", stack=f"Error: own boom\n    at own ({BASE}/app.js:3:31)"))
    assert_true(
        [(e["url"], e["same_origin"]) for e in events] == [("https://widgets.example/w.js", False), (BASE + "/app.js", True)],
        f"page errors carry the throwing script's URL and origin: {[(e['url'], e['same_origin']) for e in events]}",
    )
    assert_true(build_report([events[0], {"type": "result", "status": "finished"}], {"claims": []})["browser_verdict"] == "pass", "a third-party page error does not fail the run")

    def console_only(enforced: bool) -> str:
        session, events, _ = _session(_Page(), fail_on_console_error=enforced)
        session.on_console(SimpleNamespace(type="error", text="boom", location={"url": BASE + "/app.js"}))
        session.on_console(SimpleNamespace(type="error", text="third party", location={"url": "https://ads.example/x.js"}))
        return build_report([*events, {"type": "result", "status": "finished"}], {"claims": []})["browser_verdict"]

    assert_true(console_only(False) == "pass", "console errors are warnings by default")
    assert_true(console_only(True) == "fail", "fail_on_console_error turns a same-origin console error into a failure")

    # --- no Playwright: the player says so instead of crashing ------------------------------------
    emitted: list[dict] = []
    saved = sys.modules.get("playwright.sync_api", "absent")
    sys.modules["playwright.sync_api"] = None  # import now raises ImportError
    try:
        code = run_scenario({"steps": []}, {"browser": "chromium"}, "", emitted.append)
    finally:
        if saved == "absent":
            sys.modules.pop("playwright.sync_api", None)
        else:
            sys.modules["playwright.sync_api"] = saved
    assert_true(code == 2 and emitted[0]["reason"] == "playwright_missing" and emitted[-1] == {"type": "result", "status": "aborted"}, f"missing package: {emitted}")
    emitted.clear()
    assert_true(run_scenario({"steps": []}, {"browser": "netscape"}, "", emitted.append) == 2 and emitted[0]["reason"] == "launch_failed", "an unknown browser name is refused")
    assert_true(e2e_browser.HEARTBEAT_EVERY_S < 30, "heartbeats come well inside the default idle timeout")

    # --- failure capture (plan §16): probe on selector misses, HTML + screenshot on page failures --
    art = Path(tempfile.mkdtemp(prefix="e2e-capture-"))
    finished = {"type": "result", "status": "finished"}
    try:
        def captured(page: _Page, steps: list[dict], *, secrets: bool = False):
            events: list[dict] = []
            clock = _Clock()
            config = {"base_url": BASE, "step_timeout_ms": 300, "nav_timeout_ms": 500, "probe_max_elements": 5}
            session = Session(page, config, events.append, clock=clock, sleep=clock.advance, artifacts_dir=str(art), has_secrets=secrets)
            session.run({"steps": steps})
            return session, events

        page = _Page()
        page.url = BASE + "/login?error=1"
        session, failure_events = captured(page, [{"action": "expect_url", "contains": "/dashboard", "claim_id": "login"}])
        kinds = [(e["kind"], e["name"]) for e in failure_events if e["type"] == "artifact"]
        assert_true(kinds == [("html", "step01.html"), ("screenshot", "step01.png")], f"an assertion failure keeps bounded HTML and one screenshot: {kinds}")
        assert_true((art / "step01.html").read_text(encoding="utf-8") == page.html and (art / "step01.png").exists(), "the files are where the events say")
        assert_true(
            page.calls.index("clear_inputs") < page.calls.index("content") < page.calls.index("screenshot"),
            f"typed values are cleared before anything is captured: {page.calls}",
        )
        assert_true(session.failed, "the session remembers it failed; the trace decision reads this")
        order = [e["type"] for e in failure_events]
        assert_true(order.index("progress") < order.index("artifact"), "artifacts follow the step they document")
        report = build_report([*failure_events, finished], {"claims": _CLAIMS})
        assert_true([a["name"] for a in report["artifacts"]] == ["step01.html", "step01.png"], f"the report lists artifacts: {report['artifacts']}")
        block = evidence_block(dict(report, artifacts=[*report["artifacts"], {"kind": "trace", "name": "trace.zip", "step": None, "pruned": True}]), artifacts=str(art))
        assert_true("- step01.png (screenshot, step 1)" in block and "- trace.zip (trace) — pruned" in block, f"the evidence block names kept and pruned files:\n{block}")

        page = _Page()
        page.url = BASE + "/login?error=1"
        _, events = captured(page, [{"action": "expect_url", "contains": "/dashboard", "claim_id": "login"}], secrets=True)
        artifacts = [e for e in events if e["type"] == "artifact"]
        assert_true([a["kind"] for a in artifacts] == ["html", "skipped"] and "screenshot" not in page.calls, "with resolved ${ENV} values no screenshot is taken")
        assert_true("${ENV}" in artifacts[1]["detail"], "and the skip says why")

        page = _Page()
        page.url = BASE + "/login?error=1"
        short_events: list[dict] = []
        short_clock = _Clock()
        Session(page, {"base_url": BASE, "step_timeout_ms": 300, "capture_html": False}, short_events.append, clock=short_clock, sleep=short_clock.advance,
                artifacts_dir=str(art), has_secrets=True).run({"steps": [{"action": "expect_url", "contains": "/dashboard", "claim_id": "login"}]})
        assert_true(
            [a["kind"] for a in short_events if a["type"] == "artifact"] == ["skipped", "skipped"] and "content" not in page.calls,
            "a value too short to scrub keeps no HTML either",
        )

        page = _login_page()
        _, events = captured(page, [{"action": "click", "selector": {"css": "#nope"}, "selector_provenance": {"type": "heuristic"}}])
        miss = _progress(events)[0]
        assert_true(miss["probe"]["buttons"][0]["name"] == "Masuk" and not [e for e in events if e["type"] == "artifact"], "a heuristic miss gets a probe and nothing else")
        block = evidence_block(build_report([*events, finished], {"claims": _CLAIMS}))
        assert_true("probe: headings: Masuk | buttons: Masuk" in block, f"the probe reaches the reviewer as one bounded line:\n{block}")

        page = _login_page()
        _, events = captured(page, [{"action": "click", "selector": {"role": "button", "name": "Logout"}, "selector_provenance": {"type": "source"}}])
        assert_true(
            "probe" in _progress(events)[0] and [e["kind"] for e in events if e["type"] == "artifact"] == ["html", "screenshot"],
            "a grounded miss gets the probe and the page evidence",
        )

        page = _login_page()
        _, events = captured(page, [{"action": "goto", "url": "https://evil.example/"}])
        assert_true(not [e for e in events if e["type"] == "artifact"] and "content" not in page.calls, "a blocked navigation never touched the page and keeps nothing")

        page = _login_page()
        _, events = captured(page, [{"action": "goto", "url": "/login"}, {"action": "probe"}])
        assert_true(not [e for e in events if e["type"] == "artifact"] and "content" not in page.calls, "a passing run captures nothing")
        probe_report = build_report([*events, finished], {"claims": []})
        assert_true(probe_report["probes"][0]["step"] == 2 and "probes:\n- step 2: headings: Masuk" in evidence_block(probe_report), "a probe step lands in the report and the evidence block")

        # --- trace policy through run_scenario, with a stand-in Playwright module -------------------
        tracing_calls: list[tuple] = []

        class _Tracing:
            def start(self, **kwargs):
                tracing_calls.append(("start", kwargs))

            def stop(self, path=None):
                tracing_calls.append(("stop", path))
                if path:
                    Path(path).write_bytes(b"PK fake trace")

        class _Context:
            tracing = _Tracing()

            def set_default_timeout(self, ms):
                pass

            def set_default_navigation_timeout(self, ms):
                pass

            def new_page(self):
                return _login_page()

            def route(self, pattern, handler):
                pass

            def close(self):
                tracing_calls.append(("close_context", None))

        class _Browser:
            def new_context(self):
                return _Context()

            def close(self):
                tracing_calls.append(("close_browser", None))

        launch_calls: list[dict] = []

        @contextlib.contextmanager
        def fake_sync_playwright():
            def launch(**kwargs):
                launch_calls.append(kwargs)
                return _Browser()

            yield SimpleNamespace(chromium=SimpleNamespace(launch=launch))

        def real_run(steps: list[dict], *, secrets: bool, **config) -> tuple[int, list[dict]]:
            tracing_calls.clear()
            emitted: list[dict] = []
            saved_module = sys.modules.get("playwright.sync_api", "absent")
            sys.modules["playwright.sync_api"] = SimpleNamespace(sync_playwright=fake_sync_playwright)
            try:
                code = run_scenario({"steps": steps}, {"browser": "chromium", "base_url": BASE, "step_timeout_ms": 200, **config}, str(art), emitted.append, has_secrets=secrets)
            finally:
                if saved_module == "absent":
                    sys.modules.pop("playwright.sync_api", None)
                else:
                    sys.modules["playwright.sync_api"] = saved_module
            return code, emitted

        failing = [{"action": "goto", "url": "/login"}, {"action": "expect_url", "contains": "/dashboard", "claim_id": "login"}]
        code, emitted = real_run(failing, secrets=False)
        assert_true(tracing_calls[0][0] == "start" and ("stop", str(art / "trace.zip")) in tracing_calls, f"a failed run keeps its trace: {tracing_calls}")
        assert_true(
            code == 0 and any(e.get("kind") == "trace" for e in emitted) and emitted[-1] == finished,
            "the kept trace is announced before the result",
        )
        assert_true(tracing_calls[-2:] == [("close_context", None), ("close_browser", None)], f"context and browser close after the trace is saved: {tracing_calls}")
        code, emitted = real_run([{"action": "goto", "url": "/login"}], secrets=False)
        assert_true(("stop", None) in tracing_calls and not any(e.get("type") == "artifact" for e in emitted), "a passing run discards its trace")
        code, emitted = real_run(failing, secrets=True)
        assert_true(not any(call[0] == "start" for call in tracing_calls), f"no trace is recorded once ${{ENV}} values resolved: {tracing_calls}")
        assert_true(any(e.get("kind") == "skipped" and e.get("name") == "trace.zip" for e in emitted), "and the skipped trace is named")

        # slow_mo_ms reaches the launch, clamped; absent or unreadable runs at full speed
        for given, expected in ((None, 0), (700, 700), (-5, 0), (99999, 5000), ("slow", 0)):
            real_run([{"action": "goto", "url": "/login"}], secrets=False, **({} if given is None else {"slow_mo_ms": given}))
            assert_true(
                launch_calls[-1] == {"headless": False, "slow_mo": expected, "args": []},
                f"slow_mo_ms={given!r} launches with {launch_calls[-1]}",
            )
        # headed is the shipped default; a config that asks for headless still gets it
        real_run([{"action": "goto", "url": "/login"}], secrets=False, headless=True)
        assert_true(launch_calls[-1]["headless"] is True, f"headless: true reaches the launch: {launch_calls[-1]}")

        # preflight's write decision reaches the browser as a resolver pin, so the name it
        # approved cannot come back pointing somewhere else mid-run
        real_run([{"action": "goto", "url": "/login"}], secrets=False, host_pins={"app.test": "192.168.1.40"})
        assert_true(
            launch_calls[-1]["args"] == ["--host-resolver-rules=MAP app.test 192.168.1.40"],
            f"an approved write host is pinned at launch: {launch_calls[-1]}",
        )
        from core.evidence.e2e.browser import _launch_args

        assert_true(
            _launch_args({"host_pins": {"app.test": "192.168.1.40"}, "browser": "firefox"}) == [] and _launch_args({"browser": "chromium"}) == [],
            "a browser that cannot take the flag is never handed it, and no pin means no flag",
        )
    finally:
        shutil.rmtree(art, ignore_errors=True)
