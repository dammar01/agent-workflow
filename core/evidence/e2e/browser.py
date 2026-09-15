"""The real browser player: one resolved scenario through Playwright, reported as the
`classify.py` event protocol.

Split in two on purpose. `run_scenario` imports Playwright lazily (an optional extra —
tests/checks/deps.py), launches, and always closes. `Session` drives a page that is
already open, so the step semantics — selector fallback, polling assertions, origin
guard, observers — are checked against a stand-in page without a browser.

What never leaves this process: typed values (fill/select/press echo no input), full
DOM, request or response bodies. What does leave is scrubbed again by the runner
before anything is stored or prompted.
"""

from __future__ import annotations

import re
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from core.evidence.e2e.preflight import same_origin
from core.evidence.e2e.spec import SELECTOR_RANK, navigation_error, selector_rank, step_selectors

HEARTBEAT_EVERY_S = 2.0
POLL_S = 0.1
DETAIL_CHARS = 300
STABLE_WAIT_MS = 2000
_BROWSERS = ("chromium", "firefox", "webkit")
_ELEMENT_ACTIONS = frozenset({"click", "fill", "select", "press"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
_DEFAULT_PORTS = {"http": 80, "https": 443}
_SLOW_MO_MAX_MS = 5000


def _slow_mo_ms(config: dict) -> int:
    """settings.slow_mo_ms clamped to 0.._SLOW_MO_MAX_MS; an unreadable value runs at full speed."""
    try:
        return min(_SLOW_MO_MAX_MS, max(0, int(config.get("slow_mo_ms") or 0)))
    except (TypeError, ValueError):
        return 0
_SELECTOR_ERRORS = frozenset({"selector_missing", "selector_ambiguous", "not_visible"})
_STACK_URL = re.compile(r"https?://[^\s()]+")
MAX_HTML_BYTES = 512 * 1024
SECRET_CAPTURE_NOTE = "the scenario resolved ${ENV} values and a rendered page may show them"

# Before any capture, typed values leave the DOM: the serialized HTML and the screenshot
# would otherwise carry what `fill` typed. Hidden inputs go too (CSRF tokens).
_CLEAR_INPUTS_JS = r"""() => {
  document.querySelectorAll("input,textarea").forEach((el) => {
    const type = (el.getAttribute("type") || "").toLowerCase();
    if (["submit", "button", "checkbox", "radio", "image", "reset"].includes(type)) return;
    el.value = "";
    el.removeAttribute("value");
    if (el.tagName === "TEXTAREA") el.textContent = "";
  });
}"""

# Bounded DOM probe (plan §11): visible elements only, text cut, no input values, capped
# at the request's settings.probe_max_elements across all buckets.
_PROBE_JS = r"""(limit) => {
  const cut = (s) => (s || "").replace(/\s+/g, " ").trim().slice(0, 80);
  const visible = (el) => !!(el.offsetWidth || el.offsetHeight || el.getClientRects().length);
  const out = {url: location.href, title: cut(document.title), headings: [], buttons: [], inputs: [], links: [], truncated: false};
  let used = 0;
  const take = (bucket, el, item) => {
    if (!visible(el)) return;
    if (used >= limit) { out.truncated = true; return; }
    used += 1;
    out[bucket].push(item);
  };
  document.querySelectorAll("h1,h2,h3").forEach((el) => take("headings", el, cut(el.innerText)));
  document.querySelectorAll("button,[role=button],input[type=submit],input[type=button]").forEach((el) =>
    take("buttons", el, {role: "button", name: cut(el.getAttribute("aria-label") || el.innerText || el.value), testid: el.getAttribute("data-testid")}));
  document.querySelectorAll("input:not([type=hidden]):not([type=submit]):not([type=button]),textarea,select").forEach((el) =>
    take("inputs", el, {
      role: el.tagName === "SELECT" ? "combobox" : "textbox",
      label: cut((el.labels && el.labels[0] && el.labels[0].innerText) || el.getAttribute("aria-label") || el.getAttribute("placeholder")),
      type: el.getAttribute("type") || el.tagName.toLowerCase(),
      testid: el.getAttribute("data-testid"),
    }));
  document.querySelectorAll("a[href]").forEach((el) =>
    take("links", el, {name: cut(el.innerText || el.getAttribute("aria-label")), href: cut(el.getAttribute("href")), testid: el.getAttribute("data-testid")}));
  return out;
}"""


def _cut(text: object, limit: int = DETAIL_CHARS) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 1] + "…"


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}" if parts.scheme else url[:80]


def _endpoint(url: str) -> tuple | None:
    """(scheme, host, port, path) with the default port filled in and the query dropped —
    what one allowed_mutation_paths entry has to match exactly."""
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        return scheme, (parts.hostname or "").lower(), parts.port or _DEFAULT_PORTS.get(scheme), parts.path or "/"
    except ValueError:  # an unparseable port
        return None


def _mutation_allow_list(config: dict, base_url: str) -> frozenset:
    allowed = set()
    for entry in config.get("allowed_mutation_paths") or []:
        if not isinstance(entry, str) or not entry:
            continue
        key = _endpoint(urljoin(base_url, entry) if entry.startswith("/") else entry)
        if key is not None:
            allowed.add(key)
    return frozenset(allowed)


def _error_kind(exc: BaseException) -> str:
    """Playwright's own classes are not importable here (tests use a stand-in page), so
    the kind is read from the class name and the network error code in the message."""
    message = str(exc)
    if "ERR_BLOCKED_BY_CLIENT" in message:
        return "navigation_blocked"
    if type(exc).__name__ == "TimeoutError":
        return "timeout"
    if "net::ERR_" in message:
        return "navigation_failed"
    return "action_failed"


class Session:
    """Drives one open page through a scenario and emits progress/observation events."""

    def __init__(
        self,
        page,
        config: dict,
        emit,
        *,
        clock=time.monotonic,
        sleep=time.sleep,
        artifacts_dir: str = "",
        has_secrets: bool = False,
    ) -> None:
        self.page = page
        self.artifacts_dir = Path(artifacts_dir) if artifacts_dir else None
        self.has_secrets = has_secrets
        self.failed = False
        self.config = config
        self._emit = emit
        self.clock = clock
        self.sleep = sleep
        self.base_url = str(config.get("base_url") or "")
        self.step_timeout_ms = int(config.get("step_timeout_ms") or 8000)
        self.step_timeout_s = max(0.1, self.step_timeout_ms / 1000)
        self.nav_timeout_ms = int(config.get("nav_timeout_ms") or 15000)
        self.probe_limit = max(1, int(config.get("probe_max_elements") or 200))
        self.fail_on_console_error = bool(config.get("fail_on_console_error"))
        self.blocked: list[str] = []
        self.allow_side_effects = bool(config.get("allow_side_effects"))
        self.mutation_allow = _mutation_allow_list(config, self.base_url)
        # {method, origin, resource_type, attributed}. Origin only: a path or query can
        # carry an id or a token, and this list ends up in events.
        self.mutations_blocked: list[dict] = []
        self._last_emit = clock()

    # ---- wire -----------------------------------------------------------------------
    def emit(self, event: dict) -> None:
        self._emit(event)
        self._last_emit = self.clock()

    def beat(self) -> None:
        """Long polls still talk: the supervisor's idle timer resets on every line."""
        if self.clock() - self._last_emit >= HEARTBEAT_EVERY_S:
            self.emit({"type": "heartbeat"})

    def _page_url(self) -> str:
        try:
            return str(self.page.url)
        except Exception:
            return ""

    def _same(self, url: str) -> bool:
        return bool(self.base_url) and same_origin(url, self.base_url)

    # ---- observers and the origin guard ---------------------------------------------
    def on_console(self, message) -> None:
        if getattr(message, "type", None) != "error":
            return
        location = getattr(message, "location", None) or {}
        url = str(location.get("url") or self._page_url()) if isinstance(location, dict) else self._page_url()
        self.emit(
            {
                "type": "observation",
                "kind": "console_error",
                "url": url,
                "status": None,
                "detail": _cut(getattr(message, "text", "")),
                "same_origin": self._same(url),
                "main_request": False,
                "enforced": self.fail_on_console_error,
            }
        )

    def on_page_error(self, error) -> None:
        # The error's own stack names the script that threw. Attributing it to the page URL
        # made a third-party widget failing on our page a same-origin application `fail`.
        stack = str(getattr(error, "stack", None) or "")
        match = _STACK_URL.search(stack)
        url = re.sub(r"(?::\d+){1,2}$", "", match.group(0)) if match else self._page_url()
        self.emit(
            {
                "type": "observation",
                "kind": "page_error",
                "url": url,
                "status": None,
                "detail": _cut(getattr(error, "message", None) or error),
                "same_origin": self._same(url),
                "main_request": False,
            }
        )

    def on_response(self, response) -> None:
        status = int(getattr(response, "status", 0) or 0)
        if status < 500:
            return
        try:
            main = bool(response.request.is_navigation_request()) and response.frame == self.page.main_frame
        except Exception:  # service-worker and detached-frame responses have no frame
            main = False
        url = str(response.url)
        self.emit(
            {
                "type": "observation",
                "kind": "http_5xx",
                "url": url,
                "status": status,
                "detail": f"HTTP {status}",
                "same_origin": self._same(url),
                "main_request": main,
            }
        )

    def guard(self, route, request) -> None:
        """Refuse top-level navigation the spec policy would refuse — including redirects
        and clicks, which `validate_scenario` cannot see. Subresources and iframes pass:
        third-party noise is observed, not blocked.

        Then refuse writes. A step's `side_effect` is only what the scenario declares, so
        with allow_side_effects false every non-GET/HEAD/OPTIONS request is aborted, on any
        origin (an API on another port is still the app's data), unless its exact endpoint
        is in allowed_mutation_paths. Method-only: a GET that mutates, a WebSocket message,
        or a service worker's own fetch is not seen here."""
        url = str(request.url)
        try:
            top_level = bool(request.is_navigation_request()) and request.frame == self.page.main_frame
        except Exception:
            top_level = False
        if top_level and navigation_error(url, self.config):
            self.blocked.append(url)
            route.abort("blockedbyclient")
            return
        method = str(getattr(request, "method", None) or "GET").upper()
        if not self.allow_side_effects and method not in _SAFE_METHODS and _endpoint(url) not in self.mutation_allow:
            self.mutations_blocked.append(
                {
                    "method": method,
                    "origin": _origin(url),
                    "resource_type": str(getattr(request, "resource_type", None) or ""),
                    "attributed": False,
                }
            )
            route.abort("blockedbyclient")
            return
        route.continue_()

    # ---- selectors ------------------------------------------------------------------
    def locator(self, selector: dict):
        key = SELECTOR_RANK[selector_rank(selector)]
        if key == "role":
            if "name" in selector:
                return self.page.get_by_role(selector["role"], name=selector["name"])
            return self.page.get_by_role(selector["role"])
        if key == "label":
            return self.page.get_by_label(selector["label"])
        if key == "testid":
            return self.page.get_by_test_id(selector["testid"])
        if key == "text":
            return self.page.get_by_text(selector["text"])
        return self.page.locator(selector["css"])

    def resolve(self, step: dict, *, visible: bool = False):
        """Poll candidates strongest-first until one matches exactly one element.

        Returns (locator, provenance, error). The failure provenance is the strongest
        candidate's: if the codebase named a selector and it is gone, that is what the
        classifier must weigh, not the heuristic fallback that also missed.
        """
        candidates = step_selectors(step)
        if not candidates:
            return None, None, {"kind": "harness_error", "detail": "step has no usable selector"}
        deadline = self.clock() + self.step_timeout_s
        ambiguous = hidden = None
        while True:
            for candidate in candidates:
                loc = self.locator(candidate["selector"])
                count = loc.count()
                if count == 1:
                    if visible and not loc.is_visible():
                        hidden = hidden or candidate
                        continue
                    return loc, candidate["provenance"], None
                if count > 1:
                    ambiguous = ambiguous or candidate
            if self.clock() >= deadline:
                break
            self.beat()
            self.sleep(POLL_S)
        waited = f"within {self.step_timeout_s:g}s"
        if hidden:
            return None, hidden["provenance"], {"kind": "not_visible", "detail": f"matched element stayed hidden {waited}"}
        if ambiguous:
            return None, ambiguous["provenance"], {"kind": "selector_ambiguous", "detail": f"selector matched more than one element {waited}"}
        return None, candidates[0]["provenance"], {
            "kind": "selector_missing",
            "detail": f"no element matched {len(candidates)} candidate(s) {waited}",
        }

    # ---- assertions -----------------------------------------------------------------
    def _text_matches(self, actual: str, step: dict) -> bool:
        if "equals" in step:
            target = str(step["equals"])
            return actual == target or (bool(self.base_url) and actual == urljoin(self.base_url, target))
        if "contains" in step:
            return str(step["contains"]) in actual
        if "matches" in step:
            return re.search(str(step["matches"]), actual) is not None
        return False

    def _poll_text(self, read, step: dict) -> tuple[bool, str]:
        deadline = self.clock() + self.step_timeout_s
        while True:
            actual = read()
            if self._text_matches(actual, step):
                return True, actual
            if self.clock() >= deadline:
                return False, actual
            self.beat()
            self.sleep(POLL_S)

    def stable(self) -> bool:
        """Whether the page had settled when a step failed — the classifier's app/unknown split."""
        try:
            self.page.wait_for_load_state("networkidle", timeout=STABLE_WAIT_MS)
            return self.page.evaluate("document.readyState") == "complete"
        except Exception:
            return False

    # ---- steps ----------------------------------------------------------------------
    def perform(self, step: dict) -> dict:
        action = step.get("action")
        expectation = {k: step[k] for k in ("contains", "equals", "matches") if k in step}

        if action == "goto":
            url = urljoin(self.base_url, str(step.get("url"))) if self.base_url else str(step.get("url"))
            refused = navigation_error(url, self.config)
            if refused:
                return {"status": "failed", "error": {"kind": "navigation_blocked", "detail": refused}}
            response = self.page.goto(url, wait_until="load", timeout=self.nav_timeout_ms)
            return {"status": "passed", "actual": {"url": self._page_url(), "status": getattr(response, "status", None)}}

        if action == "expect_url":
            ok, actual = self._poll_text(self._page_url, step)
            if ok:
                return {"status": "passed", "expected": expectation, "actual": {"url": actual}}
            return {"status": "failed", "expected": expectation, "actual": {"url": actual},
                    "error": {"kind": "assertion", "detail": "url did not match"}}

        if action == "expect_title":
            ok, actual = self._poll_text(lambda: str(self.page.title()), step)
            if ok:
                return {"status": "passed", "expected": expectation, "actual": {"title": _cut(actual, 120)}}
            return {"status": "failed", "expected": expectation, "actual": {"title": _cut(actual, 120)},
                    "error": {"kind": "assertion", "detail": "title did not match"}}

        if action == "probe":
            data = self.page.evaluate(_PROBE_JS, self.probe_limit)
            return {"status": "passed", "actual": {"probe": data}}

        visible = action in ("wait_dom", "expect_dom")
        loc, provenance, error = self.resolve(step, visible=visible)
        selector_view = {"selector_provenance": provenance, "expected": {"selector": step.get("selector") or [c["selector"] for c in step_selectors(step)]}}
        if error:
            return {"status": "failed", "error": error, **selector_view}
        timeout = self.step_timeout_ms
        try:
            if action == "click":
                loc.click(timeout=timeout)
            elif action == "fill":
                loc.fill(str(step.get("value") or ""), timeout=timeout)
            elif action == "select":
                if step.get("value") is not None:
                    loc.select_option(value=str(step["value"]), timeout=timeout)
                else:
                    loc.select_option(label=str(step.get("label") or ""), timeout=timeout)
            elif action == "press":
                loc.press(str(step.get("key") or ""), timeout=timeout)
        except Exception as exc:
            kind = _error_kind(exc)
            if kind == "timeout" and action in _ELEMENT_ACTIONS:
                kind = "not_visible"  # resolved, but never actionable: covered, disabled, detached
            return {"status": "failed", "error": {"kind": kind, "detail": _cut(str(exc).splitlines()[0] if str(exc) else kind)}, **selector_view}
        if action == "expect_dom" and "text" in step:
            wanted = str(step["text"])
            ok, actual = self._poll_text(lambda: str(loc.inner_text()), {"contains": wanted})
            view = {**selector_view, "expected": {**selector_view["expected"], "text": wanted}, "actual": {"text": _cut(actual, 120)}}
            if not ok:
                return {"status": "failed", "error": {"kind": "assertion", "detail": "element text did not match"}, **view}
            return {"status": "passed", **view}
        return {"status": "passed", **selector_view}

    def capture(self, index: int, outcome: dict) -> list[dict]:
        """Failure evidence per plan §16, returned as artifact events.

        A selector miss gets a bounded probe first; a heuristic miss stops there — the
        scenario guessed, and a screenshot adds nothing the probe does not. Any other
        failure on the page keeps bounded HTML and, unless the scenario resolved `${ENV}`
        values, one screenshot. A blocked navigation never touched the page: nothing.
        """
        kind = (outcome.get("error") or {}).get("kind")
        if kind == "navigation_blocked":
            return []
        if kind in _SELECTOR_ERRORS:
            try:
                outcome["probe"] = self.page.evaluate(_PROBE_JS, self.probe_limit)
            except Exception:
                pass
            if outcome.get("selector_provenance") == "heuristic":
                return []
        if self.artifacts_dir is None:
            return []
        stem = f"step{index:02d}"
        events: list[dict] = []
        try:
            self.page.evaluate(_CLEAR_INPUTS_JS)
        except Exception:
            pass
        try:
            raw = str(self.page.content()).encode("utf-8")
            self.artifacts_dir.mkdir(parents=True, exist_ok=True)
            (self.artifacts_dir / f"{stem}.html").write_bytes(raw[:MAX_HTML_BYTES])
            events.append(
                {"type": "artifact", "kind": "html", "name": f"{stem}.html", "step": index,
                 "bytes": min(len(raw), MAX_HTML_BYTES), "truncated": len(raw) > MAX_HTML_BYTES}
            )
        except Exception as exc:
            events.append({"type": "artifact", "kind": "error", "name": None, "step": index, "detail": _cut(f"html: {exc}")})
        if self.has_secrets:
            events.append(
                {"type": "artifact", "kind": "skipped", "name": f"{stem}.png", "step": index,
                 "detail": f"screenshot skipped: {SECRET_CAPTURE_NOTE}"}
            )
            return events
        try:
            path = self.artifacts_dir / f"{stem}.png"
            self.page.screenshot(path=str(path))
            events.append(
                {"type": "artifact", "kind": "screenshot", "name": path.name, "step": index,
                 "bytes": path.stat().st_size if path.exists() else None}
            )
        except Exception as exc:
            events.append({"type": "artifact", "kind": "error", "name": None, "step": index, "detail": _cut(f"screenshot: {exc}")})
        return events

    def run(self, scenario: dict) -> None:
        """Every step emits exactly one progress event; after the first failure the rest
        are `skipped` — a later step on a page that already diverged proves nothing."""
        failed = False
        for index, step in enumerate(scenario.get("steps") or [], start=1):
            event = {
                "type": "progress",
                "step": index,
                "action": step.get("action"),
                "claim_id": step.get("claim_id"),
                "selector_provenance": None,
                "page_stable": None,
                "expected": None,
                "actual": None,
                "error": None,
            }
            if failed:
                self.emit({**event, "status": "skipped", "duration_ms": 0, "url_after": self._page_url()})
                continue
            started = self.clock()
            blocked_before = len(self.blocked)
            mutations_before = len(self.mutations_blocked)
            try:
                outcome = self.perform(step)
            except Exception as exc:
                kind = _error_kind(exc)
                if kind == "action_failed" and step.get("action") in ("expect_url", "expect_title"):
                    kind = "harness_error"  # e.g. an invalid `matches` pattern: the spec, not the app
                outcome = {"status": "failed", "error": {"kind": kind, "detail": _cut(str(exc).splitlines()[0] if str(exc) else kind)}}
            if len(self.blocked) > blocked_before and outcome.get("status") != "failed":
                origin = _origin(self.blocked[-1])
                outcome = {**outcome, "status": "failed",
                           "error": {"kind": "navigation_blocked", "detail": f"navigation to {origin} blocked by the /.verify-browser origin policy"}}
            elif len(self.blocked) == blocked_before and (
                fresh := [m for m in self.mutations_blocked[mutations_before:] if m["resource_type"] != "ping"]
            ):
                # Overrides any outcome, a failed one included: a write the guard refused
                # explains whatever this step then saw, and leaving an `assertion` failure in
                # place would blame the application for the harness's own refusal. A beacon
                # (`ping`) never decides a step; it is reported after the run instead.
                for mutation in fresh:
                    mutation["attributed"] = True
                first = fresh[0]
                outcome = {**outcome, "status": "failed",
                           "error": {"kind": "mutation_blocked",
                                     "detail": f"{first['method']} to {first['origin']} blocked: settings.allow_side_effects is false"
                                               " and the endpoint is not in allowed_mutation_paths"}}
            duration_ms = int((self.clock() - started) * 1000)
            url_after = self._page_url()
            artifacts: list[dict] = []
            if outcome.get("status") == "failed":
                failed = self.failed = True
                if outcome.get("page_stable") is None:
                    outcome["page_stable"] = self.stable()
                artifacts = self.capture(index, outcome)
            self.emit({**event, **outcome, "duration_ms": duration_ms, "url_after": url_after})
            for artifact in artifacts:
                self.emit(artifact)
        self.report_unattributed_mutations()

    def report_unattributed_mutations(self) -> None:
        """Writes refused outside any step's window (beacons, or a request fired after the
        last step returned) still happened and were still stopped: say so, as a warning."""
        for mutation in self.mutations_blocked:
            if mutation["attributed"]:
                continue
            mutation["attributed"] = True
            self.emit(
                {
                    "type": "observation",
                    "kind": "mutation_blocked",
                    "url": mutation["origin"],
                    "status": None,
                    "detail": f"{mutation['method']} {mutation['resource_type'] or 'request'} blocked: settings.allow_side_effects is false",
                    "same_origin": self._same(mutation["origin"]),
                    "main_request": False,
                }
            )


def _finish_trace(context, session: Session, tracing: bool, crashed: bool, artifacts_dir: str, has_secrets: bool, emit) -> None:
    """Keep the trace only when the run failed or crashed (plan §16); discard it otherwise.
    With resolved `${ENV}` values no trace was recorded — its snapshots hold typed input."""
    kept = session.failed or crashed
    if tracing and context is not None:
        try:
            if kept:
                path = Path(artifacts_dir) / "trace.zip"
                context.tracing.stop(path=str(path))
                emit({"type": "artifact", "kind": "trace", "name": "trace.zip", "step": None,
                      "bytes": path.stat().st_size if path.exists() else None})
            else:
                context.tracing.stop()
        except Exception as exc:
            emit({"type": "artifact", "kind": "error", "name": None, "step": None, "detail": _cut(f"trace: {exc}")})
    elif kept and has_secrets and artifacts_dir:
        emit({"type": "artifact", "kind": "skipped", "name": "trace.zip", "step": None,
              "detail": f"trace skipped: {SECRET_CAPTURE_NOTE}"})


def run_scenario(scenario: dict, config: dict, artifacts_dir: str, emit, *, has_secrets: bool = False) -> int:
    """Launch, run, close. Returns the player exit code; always ends with a `result` event."""
    def abort(reason: str, detail: str) -> int:
        emit({"type": "harness", "reason": reason, "detail": _cut(detail)})
        emit({"type": "result", "status": "aborted"})
        return 2

    browser_name = str(config.get("browser") or "chromium")
    if browser_name not in _BROWSERS:
        return abort("launch_failed", f"settings.browser '{browser_name}' is not one of {', '.join(_BROWSERS)}")
    try:
        from playwright.sync_api import sync_playwright  # lazy: optional extra
    except ImportError as exc:
        return abort("playwright_missing", f"playwright.sync_api import failed: {exc}")

    try:
        with sync_playwright() as pw:
            try:
                browser = getattr(pw, browser_name).launch(headless=bool(config.get("headless", True)), slow_mo=_slow_mo_ms(config))
            except Exception as exc:
                first = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
                reason = "browser_missing" if "Executable doesn't exist" in str(exc) else "launch_failed"
                return abort(reason, first)
            context = None
            session = Session(None, config, emit, artifacts_dir=artifacts_dir, has_secrets=has_secrets)
            tracing = crashed = False
            try:
                context = browser.new_context()
                context.set_default_timeout(session.step_timeout_ms)
                context.set_default_navigation_timeout(session.nav_timeout_ms)
                if artifacts_dir and not has_secrets:
                    context.tracing.start(screenshots=True, snapshots=True)
                    tracing = True
                session.page = context.new_page()
                context.route("**/*", session.guard)
                session.page.on("console", session.on_console)
                session.page.on("pageerror", session.on_page_error)
                session.page.on("response", session.on_response)
                session.run(scenario)
            except Exception:
                crashed = True
                raise
            finally:
                _finish_trace(context, session, tracing, crashed, artifacts_dir, has_secrets, emit)
                for closable in (context, browser):
                    try:
                        if closable is not None:
                            closable.close()
                    except Exception:
                        pass
    except Exception as exc:  # driver crash, closed pipe: the run cannot vouch for anything
        return abort("harness_error", f"{type(exc).__name__}: {exc}")
    emit({"type": "result", "status": "finished"})
    return 0
