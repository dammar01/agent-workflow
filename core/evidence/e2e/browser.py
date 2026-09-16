"""The real browser player: one resolved scenario through Playwright, reported as the
`classify.py` event protocol.

Split in two on purpose. `run_scenario` imports Playwright lazily (an optional extra —
tests/checks/deps.py), launches, and always closes. `Session` drives a page that is
already open, so the step semantics — selector fallback, polling assertions, origin
guard, observers — are checked against a stand-in page without a browser.

What never leaves this process: typed values (fill/select/press echo no input), full
DOM, request or response bodies, queries, cookies. What does leave is scrubbed again by
the runner before anything is stored or prompted.

A write action runs once. A timeout after the click was dispatched may still mean the
server received the request, so nothing here re-sends it; the step fails with evidence.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

from core.evidence.e2e.preflight import is_loopback_host, same_origin
from core.evidence.e2e.redact import sanitize_endpoint
from core.evidence.e2e.request import read_only_request_target
from core.evidence.e2e.spec import SELECTOR_RANK, navigation_error, request_path_matches, selector_rank, step_selectors

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
    what one allowed_read_only_requests entry has to match exactly."""
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        return scheme, (parts.hostname or "").lower(), parts.port or _DEFAULT_PORTS.get(scheme), parts.path or "/"
    except ValueError:  # an unparseable port
        return None


# Why a write was refused, as the step detail and the warning observation phrase it.
_REFUSAL_TEXT = {
    "side_effects_off": "settings.allow_side_effects is false and the endpoint is not in allowed_read_only_requests",
    "non_loopback": "writes go only to a loopback host, and this one is not",
}
_LEDGER_UNFINISHED = "no response before the run ended"
MAX_HIDDEN_CHECK = 20


def _read_only_allow_list(config: dict, base_url: str) -> frozenset:
    """(method, scheme, host, port, path) for every settings.allowed_read_only_requests entry."""
    allowed = set()
    for entry in config.get("allowed_read_only_requests") or []:
        parsed = read_only_request_target(entry) if isinstance(entry, str) else None
        if parsed is None:
            continue
        method, target = parsed
        key = _endpoint(urljoin(base_url, target) if target.startswith("/") else target)
        if key is not None:
            allowed.add((method, *key))
    return frozenset(allowed)


# GraphQL serves reads and writes on ONE endpoint, so admitting the endpoint as a read
# would admit its mutations too. The operation type is read from the body instead: string
# literals and comments are blanked first so a search term cannot spell `mutation {`.
# Conservative on purpose — a field literally named `mutation` taking arguments is refused
# as well, and a refused read costs a retry where an admitted write costs data.
_GRAPHQL_NOISE = re.compile(r'"""[\s\S]*?"""|"(?:\\.|[^"\\\n])*"|#[^\n]*')
_GRAPHQL_WRITE = re.compile(r"\b(?:mutation|subscription)\b\s*(?:[_A-Za-z]\w*)?\s*[({@]")
_GRAPHQL_REFUSAL = "a GraphQL mutation or subscription"


def _graphql_write(query: str) -> bool:
    return bool(_GRAPHQL_WRITE.search(_GRAPHQL_NOISE.sub(" ", query)))


def read_only_refusal(body: str | None) -> str | None:
    """Why a POST to a confirmed read-only endpoint must still be refused, or None.

    Only the GraphQL shapes are inspected: a JSON document (or batch) with `query`, a form
    or query-string `query=`, or a raw GraphQL body. A persisted query carries a hash where
    the operation should be, so what it does cannot be read — refused. Any other body is
    the endpoint's own business, which the user confirmed from the handler reference.
    """
    if not body:
        return None
    try:
        data = json.loads(body)
    except ValueError:
        queries = parse_qs(body).get("query")
        candidates = queries if queries else [body]
        return _GRAPHQL_REFUSAL if any(_graphql_write(query) for query in candidates) else None
    for document in data if isinstance(data, list) else [data]:
        if not isinstance(document, dict):
            continue
        query = document.get("query")
        if isinstance(query, str):
            if _graphql_write(query):
                return _GRAPHQL_REFUSAL
        elif isinstance(document.get("extensions"), dict) and "persistedQuery" in document["extensions"]:
            return "a persisted GraphQL query, whose operation cannot be read"
    return None


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
        # False when a resolved value is too short to scrub by substring (the runner decides):
        # a page's HTML could carry it, so no HTML is kept.
        self.capture_html = config.get("capture_html", True) is not False
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
        # Hosts preflight approved for writes, by name. It resolved each one and found only
        # loopback or private addresses, and pinned the browser's resolver to what it found.
        # Nothing here asks DNS again: a second question could get a second answer, and the
        # gap between the check and the write is the whole rebinding move.
        self.write_hosts = {str(h).lower() for h in config.get("write_hosts") or []}
        self.read_only_allow = _read_only_allow_list(config, self.base_url)
        try:
            self.total_timeout_s = float(config.get("total_timeout_s") or 0)
        except (TypeError, ValueError):
            self.total_timeout_s = 0.0
        # {method, origin, resource_type, attributed, reason[, refusal]}. Origin only: a path
        # or query can carry an id or a token, and this list ends up in events.
        self.mutations_blocked: list[dict] = []
        # {method, origin} of confirmed reads that went through; reported as a count.
        self.read_only_allowed: list[dict] = []
        # The request ledger: every non-GET/HEAD/OPTIONS request, with the step whose window
        # it was sent in. Open records wait for their response, keyed by the request object.
        self.phase = "steps"
        self.current_step: dict | None = None
        self.current_step_id: str | None = None
        self.last_step_id: str | None = None
        self._request_seq = 0
        self._open_requests: dict[int, tuple[dict, float, object]] = {}
        self.run_started = clock()
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
        opened = self._open_requests.get(id(getattr(response, "request", None)))
        if opened is not None:
            opened[0]["status"] = status
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

        Then refuse writes. A step's `side_effect` is only what the scenario declares, so the
        player does not rely on it. With allow_side_effects true a non-GET/HEAD/OPTIONS
        request goes through only to a host preflight approved for writes — the request's
        own host, not base_url's, so an API on some other host is refused even from a local
        page unless that host too resolved private and was pinned. With it
        false every such request is aborted, on any origin, unless it is a POST to an
        endpoint the user confirmed as a read (allowed_read_only_requests) whose body is not
        a GraphQL write. Method-only otherwise: a GET that mutates, a WebSocket message, or a
        service worker's own fetch is not seen here. Every write, sent or refused, enters
        the request ledger."""
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
        if method in _SAFE_METHODS:
            route.continue_()
            return
        endpoint = _endpoint(url)
        refusal = None
        if self.allow_side_effects:
            if endpoint is not None and (is_loopback_host(endpoint[1]) or endpoint[1] in self.write_hosts):
                self._ledger_open(request, url, method)
                route.continue_()
                return
            reason = "non_loopback"
        else:
            reason = "side_effects_off"
            if endpoint is not None and (method, *endpoint) in self.read_only_allow:
                try:
                    body = getattr(request, "post_data", None)
                except Exception:  # a body Playwright cannot decode as text (multipart upload)
                    refusal = "a request body that cannot be read as text"
                else:
                    refusal = read_only_refusal(body if isinstance(body, str) else None)
                if refusal is None:
                    self.read_only_allowed.append({"method": method, "origin": _origin(url)})
                    self._ledger_open(request, url, method, read_only=True)
                    route.continue_()
                    return
        record = {
            "method": method,
            "origin": _origin(url),
            "resource_type": str(getattr(request, "resource_type", None) or ""),
            "attributed": False,
            "reason": reason,
        }
        if refusal:
            record["refusal"] = refusal
        self.mutations_blocked.append(record)
        self._ledger_open(request, url, method, blocked=refusal or _REFUSAL_TEXT[reason])
        route.abort("blockedbyclient")

    # ---- the request ledger ---------------------------------------------------------
    def _ledger_open(self, request, url: str, method: str, *, blocked: str | None = None, read_only: bool = False) -> None:
        """Start one ledger record. A refused request is complete at once; a sent one waits
        for `requestfinished` / `requestfailed`.

        Attribution is the step whose window was open when the request was sent. Outside
        any window (a debounce, a poll, a write fired after the step returned) the record
        says `uncertain` and names the step that ran last — never assigned to it.
        """
        self._request_seq += 1
        step = self.current_step
        try:
            path = urlsplit(url).path or "/"
        except ValueError:
            path = ""
        spec = step.get("request") if isinstance(step, dict) else None
        if step is None:
            planned = None
        elif isinstance(spec, dict):
            planned = str(spec.get("method") or "").upper() == method and request_path_matches(str(spec.get("path") or ""), path)
        else:
            planned = False
        record = {
            "type": "request",
            "id": f"r{self._request_seq}",
            "phase": self.phase,
            "step_id": self.current_step_id if step is not None else None,
            "after_step": None if step is not None else self.last_step_id,
            "attribution": "step" if step is not None else "uncertain",
            "method": method,
            "endpoint": sanitize_endpoint(url),
            "resource_type": str(getattr(request, "resource_type", None) or ""),
            "read_only": read_only,
            "blocked": blocked is not None,
            "planned": planned,
            "status": None,
            "failure": f"blocked: {blocked}" if blocked else None,
            "duration_ms": 0 if blocked else None,
        }
        if blocked is not None:
            self.emit(record)
            return
        self._open_requests[id(request)] = (record, self.clock(), request)

    def _ledger_close(self, request, failure: str | None) -> None:
        opened = self._open_requests.pop(id(request), None)
        if opened is None:
            return  # not a write, or refused and already reported
        record, started, _ = opened
        record["duration_ms"] = int((self.clock() - started) * 1000)
        if failure:
            record["failure"] = _cut(failure, 120)
        self.emit(record)

    def on_request_finished(self, request) -> None:
        # HTTP 404 or 500 still "finishes": the status the response event recorded says how.
        self._ledger_close(request, None)

    def on_request_failed(self, request) -> None:
        failure = getattr(request, "failure", None)
        self._ledger_close(request, str(failure) if failure else "request failed")

    def flush_requests(self) -> None:
        """Writes still waiting when the run ends are reported as such, not dropped."""
        for key in list(self._open_requests):
            record, started, _ = self._open_requests.pop(key)
            record["duration_ms"] = int((self.clock() - started) * 1000)
            record["failure"] = _LEDGER_UNFINISHED
            self.emit(record)

    # ---- selectors ------------------------------------------------------------------
    def locator(self, selector: dict, within: dict | None = None):
        """A locator for `selector`, searched inside `within` when the step scopes it — so a
        "Simpan" in a modal is not confused with a "Simpan" on the page behind it."""
        root = self.locator(within) if within else self.page
        key = SELECTOR_RANK[selector_rank(selector)]
        if key == "role":
            if "name" in selector:
                return root.get_by_role(selector["role"], name=selector["name"])
            return root.get_by_role(selector["role"])
        if key == "label":
            return root.get_by_label(selector["label"])
        if key == "testid":
            return root.get_by_test_id(selector["testid"])
        if key == "text":
            return root.get_by_text(selector["text"])
        return root.locator(selector["css"])

    def resolve(self, step: dict, *, visible: bool = False):
        """Poll candidates strongest-first until one matches exactly one element.

        Returns (locator, provenance, error, selection). The failure provenance is the
        strongest candidate's: if the codebase named a selector and it is gone, that is what
        the classifier must weigh, not the heuristic fallback that also missed. `selection`
        is the runtime half of the element mapping — which candidate matched, how many
        elements each tried candidate matched, whether a fallback was used, and for a
        candidate the codebase named, the `path:line` it came from and its selector's key
        names — never the selector's values, which can be resolved credentials. Only the
        draft's candidates are ever tried.
        """
        candidates = step_selectors(step)
        within = step.get("within") if isinstance(step.get("within"), dict) else None
        counts: list[int | None] = [None] * len(candidates)
        if not candidates:
            return None, None, {"kind": "harness_error", "detail": "step has no usable selector"}, {"candidate": None, "match_counts": [], "fallback_used": False}
        deadline = self.clock() + self.step_timeout_s
        ambiguous = hidden = None
        while True:
            for position, candidate in enumerate(candidates):
                loc = self.locator(candidate["selector"], within)
                count = loc.count()
                counts[position] = count
                if count == 1:
                    if visible and not loc.is_visible():
                        hidden = hidden or candidate
                        continue
                    selection = {"candidate": position, "match_counts": counts[: position + 1], "fallback_used": position > 0}
                    # Only for a candidate the codebase named: this is what the tagging pass
                    # writes back to, and it may only write to an address it was given.
                    if candidate["provenance"] == "source" and candidate.get("ref"):
                        selection["source_ref"] = str(candidate["ref"]).strip()
                        # Key names, never values. A selector may hold a resolved ${ENV}
                        # value, and one shorter than redact.MIN_SCRUB_CHARS cannot be put
                        # back to its placeholder by substring — it would ride out in the
                        # events, the report and the tag proposals. `role+name` says which
                        # kind of selector won, which is all the tagging pass reads.
                        selection["selector_keys"] = sorted(candidate["selector"])
                    return loc, candidate["provenance"], None, selection
                if count > 1:
                    ambiguous = ambiguous or candidate
            if self.clock() >= deadline:
                break
            self.beat()
            self.sleep(POLL_S)
        waited = f"within {self.step_timeout_s:g}s"
        selection = {"candidate": None, "match_counts": counts, "fallback_used": False}
        if hidden:
            return None, hidden["provenance"], {"kind": "not_visible", "detail": f"matched element stayed hidden {waited}"}, selection
        if ambiguous:
            return None, ambiguous["provenance"], {"kind": "selector_ambiguous", "detail": f"selector matched more than one element {waited}"}, selection
        return None, candidates[0]["provenance"], {
            "kind": "selector_missing",
            "detail": f"no element matched {len(candidates)} candidate(s) {waited}",
        }, selection

    # ---- readiness ------------------------------------------------------------------
    def _holds(self, condition: dict) -> bool:
        """Whether one declared readiness condition is true right now."""
        try:
            key, value = next(iter(condition.items()))
            if key == "url":
                return str(value) in self._page_url()
            if key == "text":
                loc = self.page.get_by_text(str(value))
                return loc.count() >= 1 and bool(loc.nth(0).is_visible())
            loc = self.locator(value)
            count = loc.count()
            if key == "hidden":
                # A loader that is gone, or still in the DOM but hidden, is gone.
                return all(not loc.nth(i).is_visible() for i in range(min(count, MAX_HIDDEN_CHECK)))
            if count < 1 or not loc.nth(0).is_visible():
                return False
            return key == "visible" or bool(loc.nth(0).is_enabled())
        except Exception:
            return False

    def wait_ready(self, conditions: list) -> dict:
        """Poll the step's readiness conditions until all hold or the step timeout passes.
        Returns {conditions, waited_ms, unmet}; `unmet` names what never became true."""
        started = self.clock()
        deadline = started + self.step_timeout_s
        while True:
            unmet = [c for c in conditions if isinstance(c, dict) and c and not self._holds(c)]
            if not unmet or self.clock() >= deadline:
                break
            self.beat()
            self.sleep(POLL_S)
        return {
            "conditions": len(conditions),
            "waited_ms": int((self.clock() - started) * 1000),
            "unmet": [_cut(f"{k}: {json.dumps(v, ensure_ascii=False)}", 120) for c in unmet for k, v in c.items()],
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

    def stable(self, step: dict | None = None) -> bool:
        """Whether the page had settled when a step failed — the classifier's app/unknown split.

        Loaded, and every readiness condition the step declared holding. Not `networkidle`:
        a page that polls never reaches it, which made every failure there `unknown`."""
        try:
            self.page.wait_for_load_state("load", timeout=STABLE_WAIT_MS)
            if self.page.evaluate("document.readyState") != "complete":
                return False
            ready = (step or {}).get("ready")
            return all(self._holds(c) for c in ready if isinstance(c, dict) and c) if isinstance(ready, list) else True
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
        loc, provenance, error, selection = self.resolve(step, visible=visible)
        selector_view = {
            "selector_provenance": provenance,
            "selection": selection,
            "expected": {"selector": step.get("selector") or [c["selector"] for c in step_selectors(step)]},
        }
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
        if not self.capture_html:
            events.append(
                {"type": "artifact", "kind": "skipped", "name": f"{stem}.html", "step": index,
                 "detail": "html skipped: a resolved value is too short to be scrubbed from the page"}
            )
        else:
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

    def _execute(self, step: dict, step_id: str) -> dict:
        """One step, test or cleanup: readiness, the action once, then what the guards saw.

        The step's window — the time its requests are attributed to it — is exactly this
        call. `ready` is awaited before the action, or after navigation for a goto.
        """
        blocked_before = len(self.blocked)
        mutations_before = len(self.mutations_blocked)
        self.current_step, self.current_step_id = step, step_id
        ready = step.get("ready") if isinstance(step.get("ready"), list) else None
        readiness = None
        try:
            if ready and step.get("action") != "goto":
                readiness = self.wait_ready(ready)
            if readiness and readiness["unmet"]:
                outcome = {"status": "failed", "page_stable": False, "error": {
                    "kind": "not_ready",
                    "detail": f"not ready within {self.step_timeout_s:g}s: {'; '.join(readiness['unmet'])}"}}
            else:
                outcome = self.perform(step)
                if ready and step.get("action") == "goto" and outcome.get("status") == "passed":
                    readiness = self.wait_ready(ready)
                    if readiness["unmet"]:
                        outcome = {**outcome, "status": "failed", "page_stable": False, "error": {
                            "kind": "not_ready",
                            "detail": f"not ready within {self.step_timeout_s:g}s after navigation: {'; '.join(readiness['unmet'])}"}}
        except Exception as exc:
            kind = _error_kind(exc)
            if kind == "action_failed" and step.get("action") in ("expect_url", "expect_title"):
                kind = "harness_error"  # e.g. an invalid `matches` pattern: the spec, not the app
            outcome = {"status": "failed", "error": {"kind": kind, "detail": _cut(str(exc).splitlines()[0] if str(exc) else kind)}}
        finally:
            self.current_step = self.current_step_id = None
            self.last_step_id = step_id
        if readiness is not None:
            outcome["ready"] = readiness
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
            if first.get("refusal"):
                detail = (f"{first['method']} to {first['origin']} blocked: allowed_read_only_requests covers the endpoint,"
                          f" but the body is {first['refusal']}")
            else:
                detail = f"{first['method']} to {first['origin']} blocked: {_REFUSAL_TEXT[first['reason']]}"
            outcome = {**outcome, "status": "failed", "error": {"kind": "mutation_blocked", "detail": detail}}
        return outcome

    def cleanup_reserve_s(self, count: int) -> float:
        """Time held back from the test steps so cleanup can still run inside total_timeout_s:
        a readiness wait plus an action per cleanup step, never more than half the run. A
        starting point, not a calibrated figure."""
        if not count or self.total_timeout_s <= 0:
            return 0.0
        return min(self.total_timeout_s / 2, count * 2 * self.step_timeout_s)

    def run(self, scenario: dict) -> None:
        """Every step emits exactly one progress event; after the first failure the rest
        are `skipped` — a later step on a page that already diverged proves nothing. Then
        the cleanup steps run, whether the test passed or not, and finally the records
        that belong to the whole run: refused writes outside any step, confirmed reads,
        and writes still waiting for a response."""
        self.run_started = self.clock()
        cleanup = [s for s in scenario.get("cleanup") or [] if isinstance(s, dict)]
        reserve_s = self.cleanup_reserve_s(len(cleanup))
        failed = False
        stop_detail = None
        statuses: dict[str, str] = {}
        for index, step in enumerate(scenario.get("steps") or [], start=1):
            step_id = str(step.get("id") or f"step-{index}")
            event = {
                "type": "progress",
                "step": index,
                "step_id": step_id,
                "action": step.get("action"),
                "claim_id": step.get("claim_id"),
                "selector_provenance": None,
                "page_stable": None,
                "expected": None,
                "actual": None,
                "error": None,
            }
            if not failed and stop_detail is None and reserve_s and self.clock() - self.run_started >= self.total_timeout_s - reserve_s:
                stop_detail = f"stopped to keep {reserve_s:g}s of settings.total_timeout_s for cleanup"
            if failed or stop_detail:
                statuses[step_id] = "skipped"
                self.emit({**event, "status": "skipped", "duration_ms": 0, "url_after": self._page_url(),
                           **({"detail": stop_detail} if stop_detail else {})})
                continue
            started = self.clock()
            outcome = self._execute(step, step_id)
            duration_ms = int((self.clock() - started) * 1000)
            url_after = self._page_url()
            artifacts: list[dict] = []
            if outcome.get("status") == "failed":
                failed = self.failed = True
                if outcome.get("page_stable") is None:
                    outcome["page_stable"] = self.stable(step)
                artifacts = self.capture(index, outcome)
            statuses[step_id] = str(outcome.get("status"))
            self.emit({**event, **outcome, "duration_ms": duration_ms, "url_after": url_after})
            for artifact in artifacts:
                self.emit(artifact)
        self.run_cleanup(cleanup, statuses)
        self.report_unattributed_mutations()
        self.report_read_only_requests()
        self.flush_requests()

    def run_cleanup(self, cleanup: list[dict], statuses: dict[str, str]) -> None:
        """Cleanup steps, one `cleanup` event each, reported apart from the test.

        A step is `not_needed` when the step it cleans never ran (nothing was written); it is
        tried when that step passed or failed (a failed create may still have created), and
        `skipped` once an earlier step cleaning the same target failed. A cleanup failure
        never changes a test step's result — the report weighs it separately.
        """
        if not cleanup:
            return
        self.phase = "cleanup"
        broken: set[str] = set()
        for index, step in enumerate(cleanup, start=1):
            step_id = str(step.get("id") or f"cleanup-{index}")
            target = str(step.get("cleans") or "")
            event = {"type": "cleanup", "step_id": step_id, "cleans": target, "action": step.get("action"),
                     "expected": None, "actual": None, "error": None, "duration_ms": 0}
            if statuses.get(target) in (None, "skipped"):
                self.emit({**event, "status": "not_needed", "detail": f"'{target}' never ran, so it wrote nothing"})
                continue
            if target in broken:
                self.emit({**event, "status": "skipped", "detail": f"an earlier cleanup step for '{target}' failed"})
                continue
            started = self.clock()
            outcome = self._execute(step, step_id)
            if outcome.get("status") == "failed":
                broken.add(target)
            self.emit({**event, **outcome, "duration_ms": int((self.clock() - started) * 1000), "url_after": self._page_url()})
        self.phase = "steps"

    def report_read_only_requests(self) -> None:
        """Confirmed reads that went past the write guard: one observation per method and
        origin, with a count. A warning-class record, so the run shows what it let through."""
        counts: dict[tuple[str, str], int] = {}
        for item in self.read_only_allowed:
            key = (item["method"], item["origin"])
            counts[key] = counts.get(key, 0) + 1
        self.read_only_allowed = []
        for (method, origin), count in counts.items():
            self.emit(
                {
                    "type": "observation",
                    "kind": "read_only_request_allowed",
                    "url": origin,
                    "status": None,
                    "detail": f"{count} {method} request(s) passed as confirmed reads (settings.allowed_read_only_requests)",
                    "same_origin": self._same(origin),
                    "main_request": False,
                }
            )

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
                    "detail": f"{mutation['method']} {mutation['resource_type'] or 'request'} blocked: {mutation.get('refusal') or _REFUSAL_TEXT[mutation['reason']]}",
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


def _launch_args(config: dict) -> list[str]:
    """Chromium flags that hold every approved write host at the address preflight saw.

    `--host-resolver-rules` replaces the browser's own lookup, so a name cannot come back
    pointing somewhere else mid-run. Preflight already refused the run if a pin was needed
    and the browser could not take one, so an empty list here means nothing needed pinning.
    """
    pins = config.get("host_pins") or {}
    if not pins or str(config.get("browser") or "chromium") != "chromium":
        return []
    rules = ", ".join(f"MAP {host} {address}" for host, address in sorted(pins.items()))
    return [f"--host-resolver-rules={rules}"]


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
                browser = getattr(pw, browser_name).launch(headless=bool(config.get("headless", False)), slow_mo=_slow_mo_ms(config), args=_launch_args(config))
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
                session.page.on("requestfinished", session.on_request_finished)
                session.page.on("requestfailed", session.on_request_failed)
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
