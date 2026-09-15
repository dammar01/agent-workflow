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


class _Locator:
    def __init__(self, page, predicate) -> None:
        self.page = page
        self.predicate = predicate

    def _matches(self) -> list[dict]:
        return [el for el in self.page.elements if self.predicate(el)]

    def count(self) -> int:
        return len(self._matches())

    def is_visible(self) -> bool:
        return self._matches()[0].get("visible", True)

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
        if not self.settled:
            raise FakeTimeout("networkidle not reached")

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
        guarded, _, _ = _session(_Page(), **config)
        guarded.guard(hop, SimpleNamespace(url=url, method=method, resource_type=resource_type,
                                           is_navigation_request=lambda: navigation, frame=object()))
        return seen, guarded

    for method in ("GET", "HEAD", "OPTIONS", "get"):
        assert_true(call(method, BASE + "/api/items")[0] == ["continue"], f"{method} is a read and passes")
    for method in ("POST", "PUT", "PATCH", "DELETE"):
        seen, guarded = call(method, BASE + "/api/items/7?token=abc")
        assert_true(seen == ["abort:blockedbyclient"], f"{method} is refused while allow_side_effects is false: {seen}")
        assert_true(
            guarded.mutations_blocked == [{"method": method, "origin": BASE, "resource_type": "fetch", "attributed": False}],
            f"the record keeps the origin, never the path or query: {guarded.mutations_blocked}",
        )
    assert_true(call("POST", "http://localhost:9000/api/items")[0] == ["abort:blockedbyclient"], "an API on another port is still refused")
    assert_true(call("POST", "https://analytics.example/collect", resource_type="ping")[0] == ["abort:blockedbyclient"], "a third-party beacon is refused too")
    assert_true(call("POST", BASE + "/api/items", allow_side_effects=True)[0] == ["continue"], "allow_side_effects true lifts the guard")
    assert_true(call("POST", BASE + "/login", navigation=True, allowed_mutation_paths=["/login"])[0] == ["continue"], "an allow-listed path on base_url passes")
    assert_true(call("POST", BASE + "/login?next=/home", allowed_mutation_paths=["/login"])[0] == ["continue"], "the query does not take part in the match")
    assert_true(call("POST", "http://localhost:80/session", allowed_mutation_paths=["http://localhost/session"])[0] == ["continue"], "a default port matches its implicit form")
    assert_true(call("POST", BASE + "/login/delete", allowed_mutation_paths=["/login"])[0] == ["abort:blockedbyclient"], "an allow-list entry is exact, not a prefix")
    assert_true(call("POST", "http://localhost:9000/login", allowed_mutation_paths=["/login"])[0] == ["abort:blockedbyclient"], "a bare path belongs to base_url's origin only")
    assert_true(call("POST", "http://localhost:9000/login", allowed_mutation_paths=["http://localhost:9000/login"])[0] == ["continue"], "a full URL entry covers another origin")

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
            assert_true(launch_calls[-1] == {"headless": True, "slow_mo": expected}, f"slow_mo_ms={given!r} launches with {launch_calls[-1]}")
    finally:
        shutil.rmtree(art, ignore_errors=True)
