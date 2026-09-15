"""Real Chromium against a local fixture app, through the whole e2e runner (plan §22).

Opt-in: runs only with WORKFLOW_E2E_SMOKE=1, and even then skips cleanly when Playwright
or its browser is not installed, so a default `tests/run.py` never needs a browser.
Every rule the pipeline applies is proven elsewhere without one; this is the check that
the real player, the real browser, and the real process tree behave the way those
checks assume.

No spec provider is involved: every case is a `run` request carrying its scenario. The
hybrid review always runs, so a stand-in reviewer answers it with a clean review.
"""

from __future__ import annotations

import http.server
import json
import os
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote, quote_plus

from core.evidence.e2e import preflight as e2e_preflight
from core.evidence.e2e.request import request_path
from core.evidence.e2e.runner import FAKE_ENV
from core.evidence.result_shaping import _finalize_verify_result, _verify_exit_code
from core.provider.executor import Executor
from core.runtime.state import ensure_workflow_workspace
from tests.checks.e2e_routing import _REVIEW_CLEAN
from tests.checks.support import assert_true

SMOKE_ENV = "WORKFLOW_E2E_SMOKE"
FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "e2e_app"
THIRD_PARTY_TOKEN = "{{THIRD_PARTY}}"
USER = "smoke.operator@internal.example"
PASSWORD = "Sm0ke pass&word"
_CLAIMS = [{"id": "login", "severity": "blocking", "source_refs": ["req:SMOKE-LOGIN"]}]  # runs in a temp project: no files to cite


class _Reviewer:
    """Answers the stage-3 review; a spec request from this suite is a bug in the runner."""

    last_call_meta = None

    def run(self, prompt, session, model=None, work_dir=None):
        if "command: e2e_spec" in prompt:
            raise AssertionError("a run request carries its scenario; the smoke suite must never ask for a spec")
        self.last_call_meta = {"returncode": 0, "duration_seconds": 0.01}
        return {"ok": True, "content": _REVIEW_CLEAN, "meta": {"provider_session_id": "ses_smoke"}}


def _send(handler: http.server.BaseHTTPRequestHandler, status: int, body: bytes, content_type: str) -> None:
    try:
        handler.send_response(status)
        handler.send_header("Content-Type", content_type)
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)
    except (BrokenPipeError, ConnectionResetError):
        pass  # the player was killed mid-request; nothing to answer


class _AppHandler(http.server.BaseHTTPRequestHandler):
    third_party = ""
    writes: list[str] = []

    def log_message(self, *args) -> None:
        pass

    def do_POST(self) -> None:
        # Counted before anything else: the write guard's proof is that this list stays empty.
        path = self.path.split("?", 1)[0]
        self.writes.append(path)
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        if path == "/items/delete":
            return _send(self, 200, b"<!doctype html><title>Deleted</title><p>deleted</p>", "text/html; charset=utf-8")
        return _send(self, 404, b"not found", "text/plain")

    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path == "/boom":
            return _send(self, 503, b"down", "text/plain")
        if path == "/hang":
            time.sleep(20)
            return _send(self, 200, b"late", "text/plain")
        page = FIXTURE_DIR / path.lstrip("/")
        if path.endswith(".html") and page.parent == FIXTURE_DIR and page.is_file():
            body = page.read_text(encoding="utf-8").replace(THIRD_PARTY_TOKEN, self.third_party)
            return _send(self, 200, body.encode("utf-8"), "text/html; charset=utf-8")
        return _send(self, 404, b"not found", "text/plain")


class _ThirdPartyHandler(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def log_message(self, *args) -> None:
        pass

    def do_GET(self) -> None:
        self.hits.append(self.path)
        if self.path.startswith("/widget.js"):
            return _send(self, 200, b"throw new Error('third-party widget failed');", "application/javascript")
        if self.path.startswith("/phish"):
            return _send(self, 200, b"<title>Phish</title>", "text/html")
        return _send(self, 503, b"down", "text/plain")


def _serve(handler) -> tuple[http.server.ThreadingHTTPServer, str]:
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}"


def _playwright_browser_processes() -> int | None:
    """Browser processes started from Playwright's own browser cache; None when unknown."""
    if os.name == "nt":
        command = [
            "powershell", "-NoProfile", "-Command",
            "@(Get-Process -ErrorAction SilentlyContinue | Where-Object { $_.Path -like '*ms-playwright*' }).Count",
        ]
    else:
        command = ["sh", "-c", "ps -eo args | grep -c '[m]s-playwright'"]
    try:
        out = subprocess.run(command, capture_output=True, text=True, timeout=30)
        return int((out.stdout or "").strip().splitlines()[-1])
    except (OSError, subprocess.SubprocessError, ValueError, IndexError):
        return None


def _leaks(result: dict, directory: Path) -> list[str]:
    needles = {form.encode("utf-8") for value in (USER, PASSWORD) for form in (value, quote(value, safe=""), quote_plus(value))}
    found = [p.name for p in directory.iterdir() if p.is_file() and any(n in p.read_bytes() for n in needles)]
    rendered = json.dumps(result, ensure_ascii=False)
    if any(n.decode("utf-8") in rendered for n in needles):
        found.append("result")
    return found


def _test_e2e_real_browser_smoke() -> None:
    if os.environ.get(SMOKE_ENV) != "1":
        print(f"  e2e-smoke: skipped (set {SMOKE_ENV}=1 to drive real Chromium)")
        return
    ok, detail = e2e_preflight.playwright_available()
    if ok:
        ok, detail = e2e_preflight.browser_available("chromium")
    if not ok:
        print(f"  e2e-smoke: skipped ({detail})")
        return

    saved = {name: os.environ.get(name) for name in (FAKE_ENV, "E2E_SMOKE_USER", "E2E_SMOKE_PASS")}
    third, third_url = _serve(_ThirdPartyHandler)
    app, base = _serve(type("FixtureApp", (_AppHandler,), {"third_party": third_url}))
    roots: list[Path] = []
    try:
        os.environ.pop(FAKE_ENV, None)
        os.environ["E2E_SMOKE_USER"] = USER
        os.environ["E2E_SMOKE_PASS"] = PASSWORD
        before = _playwright_browser_processes()

        def case(steps: list[dict], **e2e) -> tuple[dict, dict, set[str], Path]:
            root = Path(tempfile.mkdtemp(prefix="e2e-smoke-"))
            roots.append(root)
            ensure_workflow_workspace(root, os.getenv("AGENT_PATH"))
            request = request_path(root, "e2e-smoke")
            request.parent.mkdir(parents=True, exist_ok=True)
            request.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "phase": "run",
                        "settings": {"base_url": base, "step_timeout_ms": 3000, "nav_timeout_ms": 10000, **e2e},
                        "scenario": {"version": 1, "feature": "smoke", "claims": _CLAIMS, "steps": steps},
                    }
                ),
                encoding="utf-8",
            )
            result = Executor(adapter=_Reviewer()).execute("verify-browser", "smoke", {"session_id": "e2e-smoke"}, str(root))
            assert_true(result["meta"].get("command") == "verify", f"a run request is judged as a verification: {result['meta']}")
            result = _finalize_verify_result("verify", result)
            directory = Path(result["meta"]["e2e"]["artifacts"])
            report = json.loads((directory / "report.json").read_text(encoding="utf-8"))
            return result, report, {p.name for p in directory.iterdir() if p.is_file()}, directory

        login = [
            {"action": "goto", "url": "/login.html"},
            {"action": "fill", "selector": {"label": "Email"}, "selector_provenance": {"type": "source"}, "value": "${E2E_SMOKE_USER}"},
            {"action": "fill", "selector": {"label": "Password"}, "selector_provenance": {"type": "source"}, "value": "${E2E_SMOKE_PASS}"},
            {"action": "click", "selector_candidates": [
                {"selector": {"role": "button", "name": "Sign in"}, "selector_provenance": {"type": "heuristic"}},
                {"selector": {"role": "button", "name": "Masuk"}, "selector_provenance": {"type": "source"}},
            ]},
        ]

        # --- a clean flow passes; the page echoes the typed address and nothing keeps it ----------
        result, report, files, directory = case([
            *login,
            {"action": "expect_url", "contains": "/dashboard.html", "claim_id": "login"},
            {"action": "expect_dom", "selector": {"testid": "banner"}, "text": "Welcome back", "claim_id": "login"},
        ])
        assert_true(report["browser_verdict"] == "pass", f"pass: {report['reason']} {report['failures']}")
        assert_true(result["meta"]["verdict"] == "pass" and _verify_exit_code("verify", result) == 0, f"browser pass + clean review: pass, exit 0: {result['meta']['verdict']}")
        assert_true("trace.zip" not in files and not any(name.endswith(".png") for name in files), f"a pass keeps no screenshot or trace: {sorted(files)}")
        assert_true(_leaks(result, directory) == [], f"no raw or encoded credential anywhere: {_leaks(result, directory)}")

        # --- the same flow failing: HTML kept and scrubbed, screenshot and trace withheld ---------
        result, report, files, directory = case([*login, {"action": "expect_url", "contains": "/settings", "claim_id": "login"}])
        assert_true(report["browser_verdict"] == "fail" and _verify_exit_code("verify", result) == 2, f"a failed assertion fails: {report['reason']}")
        assert_true("step05.html" in files and "step05.png" not in files and "trace.zip" not in files, f"with credentials in play only text is kept: {sorted(files)}")
        assert_true("${E2E_SMOKE_USER}" in (directory / "step05.html").read_text(encoding="utf-8"), "the rendered address is a placeholder in the kept HTML")
        assert_true(_leaks(result, directory) == [], f"no raw or encoded credential anywhere: {_leaks(result, directory)}")
        assert_true("screenshot skipped" in (directory / "evidence.md").read_text(encoding="utf-8"), "the skip is stated in the evidence")

        # --- DOM assertion fails without credentials: screenshot and trace kept ---------------------
        result, report, files, _ = case([
            {"action": "goto", "url": "/wrong_text.html"},
            {"action": "expect_dom", "selector": {"testid": "banner"}, "selector_provenance": {"type": "source"}, "text": "Welcome back", "claim_id": "login"},
        ])
        assert_true(report["browser_verdict"] == "fail" and report["claims"]["login"]["origin"] == "app", f"wrong text is the app's: {report['claims']}")
        assert_true({"step02.html", "step02.png", "trace.zip"} <= files, f"page evidence and trace kept on failure: {sorted(files)}")

        # --- wrong redirect ---------------------------------------------------------------------------
        _, report, _, _ = case([
            {"action": "goto", "url": "/broken_login.html"},
            {"action": "click", "selector": {"role": "button", "name": "Masuk"}, "selector_provenance": {"type": "source"}},
            {"action": "expect_url", "contains": "/dashboard.html", "claim_id": "login"},
        ])
        assert_true(
            report["browser_verdict"] == "fail" and report["failures"][0]["action"] == "expect_url" and "error=1" in json.dumps(report["failures"][0]["actual"]),
            f"a wrong redirect fails with the URL it landed on: {report['failures']}",
        )

        # --- a guessed selector that misses is the harness's; the probe shows what was there --------
        result, report, files, directory = case([
            {"action": "goto", "url": "/login.html"},
            {"action": "click", "selector": {"css": "#does-not-exist"}, "selector_provenance": {"type": "heuristic"}},
            {"action": "expect_url", "contains": "/dashboard.html", "claim_id": "login"},
        ])
        evidence = (directory / "evidence.md").read_text(encoding="utf-8")
        assert_true(report["browser_verdict"] == "incomplete" and report["failures"][0]["origin"] == "harness", f"heuristic miss: {report['failures']}")
        assert_true("probe:" in evidence and "Masuk" in evidence and not any(name.endswith(".png") for name in files), f"probe instead of a screenshot:\n{evidence}")

        # --- an uncaught same-origin page error fails a run whose assertions passed -----------------
        _, report, _, _ = case([
            {"action": "goto", "url": "/page_error.html"},
            {"action": "expect_title", "equals": "Page error", "claim_id": "login"},
        ])
        assert_true(report["browser_verdict"] == "fail" and [e["kind"] for e in report["app_errors"]] == ["page_error"], f"page error: {report['app_errors']}")

        # --- main-request 5xx ---------------------------------------------------------------------------
        _, report, _, _ = case([
            {"action": "goto", "url": "/boom"},
            {"action": "expect_title", "contains": "", "claim_id": "login"},
        ])
        assert_true(
            report["browser_verdict"] == "fail" and any(e["kind"] == "http_5xx" and e["main_request"] for e in report["app_errors"]),
            f"a 5xx main request fails: {report['app_errors']}",
        )

        # --- third-party widget error and beacon 503 stay warnings ----------------------------------
        _, report, _, _ = case([
            {"action": "goto", "url": "/third_party.html"},
            {"action": "wait_dom", "selector": {"testid": "beacon-done"}, "selector_provenance": {"type": "source"}},
            {"action": "expect_dom", "selector": {"testid": "greeting"}, "selector_provenance": {"type": "source"}, "text": "Hello", "claim_id": "login"},
        ])
        noise = {(o["kind"], o["same_origin"]) for o in report["observations"]}
        assert_true(report["browser_verdict"] == "pass", f"third-party noise must not fail the run: {report['app_errors']}")
        assert_true(("page_error", False) in noise and ("http_5xx", False) in noise, f"and it is still observed: {noise}")

        # --- a click that leaves the origin is stopped before the request is sent -------------------
        _ThirdPartyHandler.hits.clear()
        _, report, _, _ = case([
            {"action": "goto", "url": "/offsite.html"},
            {"action": "click", "selector": {"role": "button", "name": "Continue"}, "selector_provenance": {"type": "source"}},
            {"action": "expect_url", "contains": "/phish", "claim_id": "login"},
        ])
        assert_true(
            report["browser_verdict"] == "incomplete" and "blocked" in report["failures"][0]["detail"],
            f"off-origin navigation is a harness stop: {report['failures']}",
        )
        assert_true("/phish" not in _ThirdPartyHandler.hits, f"the blocked origin never received the request: {_ThirdPartyHandler.hits}")

        # --- a form POST is refused while allow_side_effects is false: the server never sees it -----
        delete = [
            {"action": "goto", "url": "/mutate.html"},
            {"action": "click", "selector": {"role": "button", "name": "Delete item"}, "selector_provenance": {"type": "source"}},
            {"action": "expect_title", "equals": "Deleted", "claim_id": "login"},
        ]
        _AppHandler.writes.clear()
        _, report, _, _ = case(delete)
        assert_true(_AppHandler.writes == [], f"the refused write never reached the app: {_AppHandler.writes}")
        assert_true(
            report["browser_verdict"] == "incomplete" and report["failures"] and report["failures"][0]["origin"] == "harness"
            and "allow_side_effects" in report["failures"][0]["detail"],
            f"a refused write is a harness stop, not an app failure: {report['browser_verdict']} {report['failures']}",
        )

        # --- the same POST passes when its exact path is allow-listed (the login-form case) ----------
        _AppHandler.writes.clear()
        _, report, _, _ = case(delete, allowed_mutation_paths=["/items/delete"])
        assert_true(_AppHandler.writes == ["/items/delete"], f"an allow-listed write is sent once: {_AppHandler.writes}")
        assert_true(report["browser_verdict"] == "pass", f"and the flow completes: {report['reason']} {report['failures']}")

        # --- or when side effects are allowed outright ----------------------------------------------
        _AppHandler.writes.clear()
        _, report, _, _ = case(delete, allow_side_effects=True)
        assert_true(_AppHandler.writes == ["/items/delete"] and report["browser_verdict"] == "pass", f"allow_side_effects lifts the guard: {_AppHandler.writes} {report['reason']}")

        # --- a hung navigation: idle timeout, process tree killed, no browser left behind -----------
        _, report, _, _ = case([
            {"action": "goto", "url": "/hang"},
            {"action": "expect_url", "contains": "/hang", "claim_id": "login"},
        ], idle_timeout_s=4, nav_timeout_ms=25000)
        assert_true(report["browser_verdict"] == "incomplete" and report["reason"] == "stuck", f"a hung page is stuck: {report['reason']}")
        time.sleep(2)
        after = _playwright_browser_processes()
        if before is not None and after is not None:
            assert_true(after <= before, f"the killed player left no browser behind: before={before} after={after}")
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        for server in (app, third):
            server.shutdown()
            server.server_close()
        for root in roots:
            shutil.rmtree(root, ignore_errors=True)
