"""/.verify-browser end to end, without a browser.

What these prove: the executor dispatches `verify-browser` to the request-driven runner;
a draft runs stage 1 only (the internal `e2e_spec` route) and hands back a validated spec
without starting a browser or recording a run; a run executes the confirmed scenario and
always ends in stage 3 (`verify` with the compact evidence), both reached through
`_run_delegated` under the ONE lock; the result is a canonical `[VERIFICATION]` the shared
validator agrees with; every way the run can fail short ends `incomplete` with a named
reason; config.json's `e2e` section supplies the defaults a request may override and is
never written by a run; an environmental incomplete is retried and an application failure
is not; and `/.verify` itself is back to delegated | syntax.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

from core.evidence.contract import validate_verification_contract
from core.evidence.e2e import preflight as e2e_preflight
from core.evidence.e2e import runner as e2e_runner
from core.evidence.e2e.request import draft_path, ensure_secrets_template, load_secrets, request_path, secrets_path, settings_from
from core.evidence.e2e.runner import FAKE_ENV
from core.evidence.e2e.spec import parse_spec
from core.evidence.result_shaping import _finalize_verify_result, _verify_exit_code
from core.provider.executor import Executor
from core.runtime.state import ensure_workflow_workspace
from core.workspace.workspace_paths import read_json_file, workflow_paths
from tests.checks.support import assert_true

_SOURCE_MARKER = "SOURCE-MARKER-e2e-7f3a"
_SESSION_ID = "e2e-session"
_TASK = "verify the login change"
_SPEC_REPLY = """[EVIDENCE]
confidence: high

entry_points:
- src/pages/Login.tsx:12

grounded:
- login form posts to /api/login and redirects to /dashboard [src/routes/auth.ts:40]

assumptions:
- none

scope_covered:
- src/pages, src/routes

scope_not_covered:
- none

uncertainties:
- none

[E2E SPEC]
claims:
- id: login-valid-user | severity: blocking | description: valid login opens dashboard | source_refs: src/pages/Login.tsx:12

existing_tests:
- none

coverage_gap:
- login-valid-user

scenario_json:
```json
{"version": 1, "feature": "login",
 "claims": [{"id": "login-valid-user", "severity": "blocking", "description": "valid login opens dashboard"}],
 "steps": [
   {"id": "open-login", "action": "goto", "url": "/login"},
   {"id": "fill-email", "action": "fill", "selector": {"role": "textbox", "name": "Email"}, "selector_provenance": {"type": "source"}, "value": "${E2E_USER}"},
   {"id": "submit", "action": "click", "selector": {"role": "button", "name": "Masuk"}, "selector_provenance": {"type": "source"}},
   {"id": "assert-dashboard", "action": "expect_url", "contains": "/dashboard", "claim_id": "login-valid-user"}
 ]}
```

spec_uncertainties:
- none

[DIGEST]
summary: one blocking claim, no existing coverage.
key_findings:
- login redirect must be proven at runtime
risk_level: medium
confidence: high
"""

_EVIDENCE_ONLY = _SPEC_REPLY.split("[E2E SPEC]")[0] + "[DIGEST]\nsummary: evidence without a spec.\nconfidence: high\n"
_SPEC_ONLY = (
    "[E2E SPEC]"
    + _SPEC_REPLY.split("[E2E SPEC]")[1].split("[DIGEST]")[0]
    + "[DIGEST]\nsummary: one claim, no existing coverage.\nconfidence: high\n"
)

_REVIEW_CLEAN = """[VERIFICATION]
verdict: DONE

blocking_findings:
- none

escalations:
- none

notes:
- none

checks_run:
- read src/pages/Login.tsx and src/routes/auth.ts against the runtime claims

not_verified:
- none

confidence: high — claims cover the change and the assertions are strong
"""

_REVIEW_PASS_DESPITE_FAIL = _REVIEW_CLEAN  # a reviewer that ignores the browser fail

_REVIEW_BLOCKING = """[VERIFICATION]
verdict: NEEDS FIX

blocking_findings:
- severity: high | origin: introduced | scope_relation: in_scope
  problem: the redirect target is hard-coded to /dashboard for every role [src/routes/auth.ts:44]
  trigger: an admin logs in
  impact: admins land on the user dashboard
  fix: branch on role

escalations:
- none

notes:
- none

checks_run:
- read src/routes/auth.ts

not_verified:
- none

confidence: high — reproduced by reading the code
"""


class _StageAdapter:
    """Answers stage 1 with a spec and stage 3 with a review; records every call.

    Sets `last_call_meta` with a `returncode` like the real adapters do, so the executor
    treats each call as one that reached a provider and snapshots it for usage rows.
    """

    def __init__(self, replies: dict[str, list[str]], fail: set[str] | None = None) -> None:
        self.replies = {k: list(v) for k, v in replies.items()}
        self.calls: list[dict] = []
        self.fail = set(fail or ())
        self.last_call_meta = None

    def run(self, prompt, session, model=None, work_dir=None) -> dict:
        command = "e2e_spec" if "command: e2e_spec" in prompt else ("verify" if "command: verify" in prompt else "continuation")
        if command == "continuation":
            command = "e2e_spec" if "[E2E SPEC]" in prompt else "verify"
        self.calls.append({"command": command, "prompt": prompt, "session": dict(session)})
        if command in self.fail:
            self.last_call_meta = {"returncode": 1, "duration_seconds": 0.01}
            return {"ok": False, "content": "simulated provider failure", "meta": {"error_type": "streaming_failed", "next_action": "retry"}}
        self.last_call_meta = {"returncode": 0, "duration_seconds": 0.01}
        queue = self.replies.get(command) or [""]
        content = queue.pop(0) if len(queue) > 1 else queue[0]
        return {"ok": True, "content": content, "meta": {"provider_session_id": "ses_e2e"}}


def _adapter(spec: str | list[str] = _SPEC_REPLY, review: str = _REVIEW_CLEAN, fail: set[str] | None = None) -> _StageAdapter:
    return _StageAdapter({"e2e_spec": spec if isinstance(spec, list) else [spec], "verify": [review]}, fail=fail)


def _commands(adapter: _StageAdapter) -> list[str]:
    return [c["command"] for c in adapter.calls]


def _e2e_rows(root: Path) -> list[dict]:
    path = workflow_paths(root)["data_dir"] / "quality.jsonl"
    if not path.is_file():
        return []
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return [row for row in rows if row.get("kind") == "e2e_run"]


def _usage_rows(root: Path) -> list[dict]:
    path = workflow_paths(root)["data_dir"] / "usage.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _workspace(prefix: str, *, mode: str | None = None) -> Path:
    root = Path(tempfile.mkdtemp(prefix=prefix))
    ensure_workflow_workspace(root, os.getenv("AGENT_PATH"))
    if mode:
        config_path = workflow_paths(root)["config"]
        config = read_json_file(config_path)
        config.setdefault("commands", {})["verify_mode"] = mode
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
    # The file the stage-1 fixture cites: grounding refuses a spec whose references do not
    # exist. Its first line is a marker no prompt, payload, or artifact may ever carry.
    source = root / "src" / "pages" / "Login.tsx"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("\n".join([f"// {_SOURCE_MARKER}", *(f"export const line{n} = {n};" for n in range(2, 21))]) + "\n", encoding="utf-8")
    return root


def _session(session_id: str = _SESSION_ID) -> dict:
    return {"session_id": session_id, "provider_session_id": "ses_e2e"}


def _write_request(root: Path, phase: str, *, settings: dict | None = None, scenario=None, existing_tests=None, spec_notes=None, raw=None, session_id: str = _SESSION_ID) -> Path:
    path = request_path(root, session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = raw
    if body is None:
        body = {"version": 1, "phase": phase, "settings": settings or {}}
        for key, value in (("scenario", scenario), ("existing_tests", existing_tests), ("spec_notes", spec_notes)):
            if value is not None:
                body[key] = value
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


@contextlib.contextmanager
def _env(fake: str | None, env: dict | None):
    saved = {k: os.environ.get(k) for k in [FAKE_ENV, *(env or {})]}
    try:
        if fake:
            os.environ[FAKE_ENV] = fake
        else:
            os.environ.pop(FAKE_ENV, None)
        for key, value in (env or {}).items():
            os.environ[key] = value
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _execute(root: Path, adapter, command: str, *, fake: str | None = "pass", env: dict | None = None, task: str = _TASK, session_id: str = _SESSION_ID) -> dict:
    with _env(fake, env):
        result = Executor(adapter=adapter).execute(command, task, _session(session_id), str(root))
    # main.run finalises a browser result under the command the runner stamped on it.
    return _finalize_verify_result(str((result.get("meta") or {}).get("command") or command), result)


def _draft(root: Path, adapter, *, fake: str | None = "pass", env: dict | None = None, settings: dict | None = None, task: str = _TASK) -> dict:
    _write_request(root, "draft", settings=settings)
    return _execute(root, adapter, "verify-browser", fake=fake, env=env, task=task)


def _draft_info(result: dict) -> dict:
    return ((result.get("meta") or {}).get("e2e") or {}).get("draft") or {}


def _run(root: Path, adapter, scenario, *, fake: str | None = "pass", env: dict | None = None, settings: dict | None = None, existing_tests=None, spec_notes=None, task: str = _TASK) -> dict:
    _write_request(root, "run", settings=settings, scenario=scenario, existing_tests=existing_tests, spec_notes=spec_notes)
    return _execute(root, adapter, "verify-browser", fake=fake, env=env, task=task)


def _flow(root: Path, adapter, fake: str | None, env: dict | None = None, *, settings: dict | None = None, task: str = _TASK) -> tuple[dict, dict]:
    """draft → the user confirms the draft as-is → run. Returns (draft, run or draft)."""
    draft = _draft(root, adapter, fake=fake, env=env, settings=settings, task=task)
    info = _draft_info(draft)
    if not draft.get("ok") or info.get("status") != "ready":
        return draft, draft
    result = _run(
        root, adapter, info["scenario"], fake=fake, env=env, settings=settings,
        existing_tests=info["existing_tests"], spec_notes=info["spec_notes"], task=task,
    )
    return draft, result


def _scenario(reply: str = _SPEC_REPLY) -> dict:
    return parse_spec(reply)["scenario"]


def _test_e2e_routing() -> None:
    roots: list[Path] = []

    def workspace(prefix: str, **kwargs) -> Path:
        root = _workspace(prefix, **kwargs)
        roots.append(root)
        return root

    # These checks read `headless` back out of the run's meta. On a Linux box with no
    # X11/Wayland (CI) the runner forces headless and rewrites that value, which would make
    # them assert against the machine instead of the config. The no-display fallback has
    # its own coverage in e2e_hardening.
    real_display = e2e_runner.display_available
    e2e_runner.display_available = lambda: True
    try:
        # --- pass: draft (stage 1) → confirmed run → fake player → stage 3 clean → pass ---
        root = workspace("e2e-pass-")
        config_before = workflow_paths(root)["config"].read_bytes()
        adapter = _adapter()
        draft, result = _flow(root, adapter, "pass", {"E2E_USER": "user@example.test"})
        info = _draft_info(draft)
        dmeta = draft.get("meta") or {}
        assert_true(dmeta.get("command") == "verify-browser" and dmeta.get("phase") == "draft" and "verdict" not in dmeta, f"a draft is not a verification: {dmeta}")
        assert_true(draft["content"].startswith("[E2E DRAFT]") and info.get("status") == "ready", f"a usable spec drafts ready: {draft['content'][:200]}")
        assert_true(_verify_exit_code("verify-browser", draft) == 0, "a draft exits like a non-verify command")
        saved_draft = draft_path(root, _SESSION_ID)
        assert_true(saved_draft.is_file() and "${E2E_USER}" in saved_draft.read_text(encoding="utf-8"), "draft.json keeps placeholders for the run to copy")
        assert_true(info.get("env") == ["E2E_USER"] and info.get("missing_env") == [], f"the draft names its placeholders: {info.get('env')} {info.get('missing_env')}")
        meta = result.get("meta") or {}
        # G14: source reaches no stage as content — the reviewer gets evidence, the player a
        # scenario, the artifacts neither. Only the second agent reads code, by itself.
        assert_true(all(_SOURCE_MARKER not in call["prompt"] for call in adapter.calls), "no prompt carries source content")
        leaked = [p.name for p in Path(meta["e2e"]["artifacts"]).iterdir() if _SOURCE_MARKER in p.read_text(encoding="utf-8", errors="ignore")]
        assert_true(not leaked, f"no artifact carries source content: {leaked}")
        runs = _e2e_rows(root)
        assert_true(len(runs) == 1, f"one e2e_run row per run, none for the draft: {len(runs)}")
        row = runs[0]
        assert_true(
            row["run_kind"] == "fake" and row["verdict"] == "pass" and row["spec_source"] == "request" and row["claims"] == {"total": 1, "proven": 1, "failed": 0, "unproven": 0},
            f"the row describes the run: {row}",
        )
        verify_prompts = {u.get("prompt_id") for u in _usage_rows(root) if u.get("command") == "verify"}
        assert_true(
            len(row["provider_prompt_ids"]) == 1 and verify_prompts and verify_prompts <= set(row["provider_prompt_ids"]) and row["browser_runs"] == 1,
            f"the run's usage row joins through its review prompt id: {row['provider_prompt_ids']} vs {verify_prompts}",
        )
        assert_true(row["scenario_hash"] and row["screenshots_sent_to_model"] == 0, "scenario hash for reproducibility; no screenshot reaches a model")
        assert_true(result.get("ok"), f"a pass run must succeed: {result}")
        assert_true(meta.get("command") == "verify" and meta.get("invocation") == "verify-browser" and meta.get("phase") == "run", f"a run is judged as a verification: {meta}")
        assert_true(_commands(adapter) == ["e2e_spec", "verify"], f"draft asks for the spec, run for the review, nothing else: {_commands(adapter)}")
        assert_true("[E2E EVIDENCE]" in adapter.calls[1]["prompt"], "stage 3 prompt must carry the compact evidence block")
        assert_true("browser_verdict: pass" in adapter.calls[1]["prompt"], "stage 3 prompt must state the browser verdict")
        assert_true("user@example.test" not in adapter.calls[1]["prompt"], "resolved env values must never reach a prompt")
        assert_true(meta.get("verdict") == "pass", f"effective verdict must be pass: {meta.get('verdict')}")
        assessment = validate_verification_contract(result.get("content") or "")
        assert_true(assessment["verdict"] == "pass", f"the shared validator must agree: {assessment}")
        assert_true(_verify_exit_code("verify", result) == 0, "a clean browser pass exits 0")
        assert_true("e2e:login-valid-user: pass" in result["content"], "proven claims are listed under checks_run")
        e2e_dir = Path(meta["e2e"]["artifacts"])
        assert_true((e2e_dir / "events.jsonl").is_file() and (e2e_dir / "spec.json").is_file(), "player artifacts are archived under the run")
        assert_true("user@example.test" not in (e2e_dir / "spec.json").read_text(encoding="utf-8"), "archived spec keeps placeholders, not values")
        assert_true("[omitted]" in (e2e_dir / "events.jsonl").read_text(encoding="utf-8") or "E2E_USER" not in (e2e_dir / "events.jsonl").read_text(encoding="utf-8"), "typed values are not archived")
        stage_cmds = [s.get("command") for s in meta["e2e"]["stages"]]
        assert_true(stage_cmds == [None, None, "verify"], f"run stage trace is request/player/review: {stage_cmds}")
        assert_true(
            _finalize_verify_result("verify", json.loads(json.dumps(result))).get("meta", {}).get("verdict") == "pass",
            "finalising twice (worker + await) must not change the verdict",
        )
        assert_true(workflow_paths(root)["config"].read_bytes() == config_before, "a run never writes config.json")

        # --- config.json e2e section: the project's pinned defaults ---------------------
        root = workspace("e2e-config-")
        config_path = workflow_paths(root)["config"]
        shipped = json.loads(config_path.read_text(encoding="utf-8"))
        assert_true(
            "e2e" not in shipped,
            f"config.json holds overrides only: a fresh workspace pins nothing for the browser: {shipped.get('e2e')}",
        )
        from core.runtime.config_defaults import effective_section

        effective = effective_section(shipped, "e2e")
        assert_true(
            effective["headless"] is False and effective["max_retries"] == 2,
            f"and the shipped defaults it runs on are headed with a retry budget: {effective}",
        )

        def _pin(**values) -> None:
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["e2e"] = {**config.get("e2e", {}), **values}
            config_path.write_text(json.dumps(config), encoding="utf-8")

        _pin(headless=True, slow_mo_ms=250)
        result = _run(root, _adapter(), _scenario(), env={"E2E_USER": "u"})
        pinned = result["meta"]["e2e"]["config"]
        assert_true(
            pinned["headless"] is True and pinned["slow_mo_ms"] == 250,
            f"a request with no settings inherits the pinned section: {pinned}",
        )
        result = _run(root, _adapter(), _scenario(), settings={"headless": False}, env={"E2E_USER": "u"})
        pinned = result["meta"]["e2e"]["config"]
        assert_true(
            pinned["headless"] is False and pinned["slow_mo_ms"] == 250,
            f"the request overrides one knob without discarding the rest: {pinned}",
        )
        # A bad knob in config.json warns and falls back; the same knob in a request is an
        # error. config.json is shared by every command, so one typo here must not be able
        # to stop work that never reads it.
        _pin(headless="no", nope=1)
        result = _run(root, _adapter(), _scenario(), env={"E2E_USER": "u"})
        warnings = result["meta"]["e2e"].get("config_warnings") or []
        assert_true(
            result["meta"].get("verdict") == "pass" and result["meta"]["e2e"]["config"]["headless"] is False,
            f"a malformed pinned value falls back to the shipped default instead of failing the run: {result['meta']['e2e']['config']}",
        )
        assert_true(
            any("e2e.headless: str, expected bool" in w for w in warnings) and any("e2e.nope: unknown key" in w for w in warnings),
            f"and both the wrong type and the unknown key are named, not silent: {warnings}",
        )
        _pin(headless=True, nope=None, allowed_read_only_requests=["PUT /api/items"])
        config = json.loads(config_path.read_text(encoding="utf-8"))
        del config["e2e"]["nope"]
        config_path.write_text(json.dumps(config), encoding="utf-8")
        result = _run(root, _adapter(), _scenario(), env={"E2E_USER": "u"})
        warnings = result["meta"]["e2e"].get("config_warnings") or []
        assert_true(
            any("e2e.allowed_read_only_requests" in w for w in warnings),
            f"a structurally wrong pinned list is dropped with its reason: {warnings}",
        )

        # --- tag proposals: recorded by the run, applied by nobody -----------------------
        root = workspace("e2e-tags-")
        template = root / "templates" / "login.html"
        template.parent.mkdir(parents=True, exist_ok=True)
        template.write_text('<form>\n  <input name="email">\n</form>\n', encoding="utf-8")
        cited = _scenario()
        cited["steps"][1]["selector_provenance"] = {"type": "source", "ref": "templates/login.html:2"}
        result = _run(root, _adapter(), cited, env={"E2E_USER": "u"})
        tags = result["meta"]["e2e"].get("tags") or {}
        assert_true(tags.get("ready") == 1, f"a step whose selector the draft cited becomes one ready proposal: {tags}")
        events = (Path(result["meta"]["e2e"]["artifacts"]) / "events.jsonl").read_text(encoding="utf-8")
        assert_true(
            '"selector_keys"' in events and '"selector":' not in events,
            "the event stream records which KIND of selector won, never its values — a value can be a credential too short to scrub",
        )
        recorded = json.loads(Path(tags["file"]).read_text(encoding="utf-8"))
        entry = next(e for e in recorded if e["status"] == "ready")
        assert_true(
            entry["path"] == "templates/login.html" and entry["line"] == 2 and 'data-e2e="fill-email"' in entry["new_line"],
            f"the plan names the exact line and the exact replacement: {entry}",
        )
        assert_true(
            template.read_text(encoding="utf-8") == '<form>\n  <input name="email">\n</form>\n',
            "and the runner changes nothing: the user has not been asked yet",
        )
        uncited = _run(workspace("e2e-tags-none-"), _adapter(), _scenario(), env={"E2E_USER": "u"})
        assert_true(
            not (uncited["meta"]["e2e"].get("tags") or {}).get("ready"),
            "a scenario that cites no line proposes no tag rather than guessing one",
        )

        # --- retry: environmental incomplete only, never an app failure -----------------
        root = workspace("e2e-retry-")
        result = _run(root, _adapter(), _scenario(), fake="harness_fail", env={"E2E_USER": "u"})
        meta = result["meta"]["e2e"]
        attempts = meta.get("attempts") or []
        assert_true(
            result["meta"].get("verdict") == "incomplete" and len(attempts) == 2 and attempts[-1].get("stable") is True,
            f"a harness incomplete is retried once and stops when it repeats step for step: {attempts}",
        )
        assert_true(
            Path(attempts[1]["artifacts"]).name == "retry1" and Path(attempts[0]["artifacts"]) == Path(meta["artifacts"]),
            f"the retry gets its own directory instead of overwriting what it is retrying: {attempts}",
        )
        result = _run(root, _adapter(review=_REVIEW_PASS_DESPITE_FAIL), _scenario(), fake="app_fail", env={"E2E_USER": "u"})
        assert_true(
            result["meta"].get("verdict") == "fail" and not (result["meta"]["e2e"].get("attempts") or []),
            f"an application failure is never re-rolled: {result['meta']['e2e'].get('attempts')}",
        )
        result = _run(root, _adapter(), _scenario(), fake="launch_fail", env={"E2E_USER": "u"})
        assert_true(
            result["meta"]["e2e"].get("reason") == "browser_missing" and not (result["meta"]["e2e"].get("attempts") or []),
            f"a missing browser is not a flake: rerunning it changes nothing: {result['meta']['e2e'].get('attempts')}",
        )
        result = _run(root, _adapter(), _scenario(), fake="harness_fail", settings={"max_retries": 0}, env={"E2E_USER": "u"})
        assert_true(not (result["meta"]["e2e"].get("attempts") or []), "max_retries 0 turns retries off")
        writes = _run(
            workspace("e2e-retry-writes-"), _adapter(), _scenario(), fake="harness_fail",
            settings={"base_url": "http://127.0.0.1:8000", "allow_side_effects": True, "max_retries": 2},
            env={"E2E_USER": "u"},
        )
        assert_true(
            not (writes["meta"]["e2e"].get("attempts") or []),
            "allow_side_effects turns retries off outright: the first attempt's write may already have landed",
        )

        # --- app fail: browser fail outranks a reviewer that says DONE -------------------
        root = workspace("e2e-fail-")
        _, result = _flow(root, _adapter(review=_REVIEW_PASS_DESPITE_FAIL), "app_fail", {"E2E_USER": "u"})
        meta = result.get("meta") or {}
        assert_true(meta.get("verdict") == "fail", f"app failure must be fail: {meta.get('verdict')}")
        assert_true("verdict: NEEDS FIX" in result["content"], "declared verdict must be NEEDS FIX")
        assert_true("evidence_source: e2e_runtime" in result["content"], "runtime findings carry evidence_source")
        assert_true("origin: unknown" in result["content"] and "origin: app" not in result["content"], "app/harness never enters the origin tag")
        assert_true(validate_verification_contract(result["content"])["verdict"] == "fail", "validator must see the fail")
        assert_true(_verify_exit_code("verify", result) == 2, "a browser fail exits non-zero")
        assert_true(
            any(w.get("kind") == "reviewer_verdict_overridden" for w in meta.get("contract_warnings") or []),
            "a reviewer pass over a browser fail is recorded as overridden",
        )

        # --- browser pass, reviewer blocking → fail ---------------------------------------
        root = workspace("e2e-review-")
        _, result = _flow(root, _adapter(review=_REVIEW_BLOCKING), "pass", {"E2E_USER": "u"})
        assert_true(result["meta"].get("verdict") == "fail", "a reviewer blocking finding fails a browser pass")
        assert_true("hard-coded to /dashboard" in result["content"], "the reviewer's finding is carried verbatim")

        # --- harness: heuristic selector missing → incomplete, no reviewer call ------------
        root = workspace("e2e-harness-")
        adapter = _adapter()
        _, result = _flow(root, adapter, "harness_fail", {"E2E_USER": "u"})
        assert_true(result["meta"].get("verdict") == "incomplete", f"harness failure is incomplete: {result['meta'].get('verdict')}")
        assert_true(_commands(adapter) == ["e2e_spec"], "no reviewer call when the browser could not finish")
        assert_true("harness origin" in result["content"], "harness origin is stated under not_verified")
        assert_true(_verify_exit_code("verify", result) == 2, "incomplete declared INCOMPLETE exits non-zero")

        # --- unknown origin stays unknown, never promoted -----------------------------------
        root = workspace("e2e-unknown-")
        _, result = _flow(root, _adapter(), "unknown", {"E2E_USER": "u"})
        assert_true(result["meta"].get("verdict") == "incomplete" and "unknown origin" in result["content"], "unknown origin is incomplete, not harness")

        # --- launch failure from the player → browser_missing -------------------------------
        root = workspace("e2e-launch-")
        _, result = _flow(root, _adapter(), "launch_fail", {"E2E_USER": "u"})
        assert_true(result["meta"]["e2e"].get("reason") == "browser_missing", f"player harness reason must surface: {result['meta']['e2e'].get('reason')}")

        # --- Playwright absent (no fake): draft blocked, run incomplete, no provider call ----
        root = workspace("e2e-nodep-")
        adapter = _adapter()
        # The machine running this check may well have Playwright installed; the case
        # under test is the machine that does not, so the probe is pinned to "absent".
        real_probe = e2e_preflight.playwright_available
        e2e_preflight.playwright_available = lambda: (False, "pinned absent for the test")
        try:
            draft = _draft(root, adapter, fake=None)
            result = _run(root, adapter, _scenario(), fake=None, env={"E2E_USER": "u"})
        finally:
            e2e_preflight.playwright_available = real_probe
        assert_true(_draft_info(draft).get("status") == "blocked" and draft["meta"]["e2e"].get("reason") == "playwright_missing", f"a draft stops at preflight: {_draft_info(draft)}")
        meta = result.get("meta") or {}
        assert_true(meta.get("verdict") == "incomplete" and meta["e2e"].get("reason") == "playwright_missing", f"missing dependency is incomplete: {meta}")
        assert_true("playwright_missing" in result["content"] and "verdict: INCOMPLETE" in result["content"], "the reason reaches the contract")
        assert_true(adapter.calls == [], "no provider call is spent when preflight fails")

        # --- spec section missing → one targeted continuation, then an invalid draft --------
        root = workspace("e2e-nospec-")
        adapter = _adapter(spec=[_EVIDENCE_ONLY, _EVIDENCE_ONLY])
        draft = _draft(root, adapter)
        assert_true(_draft_info(draft).get("status") == "invalid" and draft["meta"]["e2e"].get("reason") == "spec_invalid", "evidence without a spec is an invalid draft")
        assert_true(len(adapter.calls) == 2 and "[E2E SPEC]" in adapter.calls[1]["prompt"], f"exactly one targeted continuation: {len(adapter.calls)}")
        assert_true(_e2e_rows(root) == [], "an invalid draft records no run")

        # --- continuation that supplies the section recovers the draft ------------------------
        root = workspace("e2e-recover-")
        _, result = _flow(root, _adapter(spec=[_EVIDENCE_ONLY, _SPEC_ONLY]), "pass", {"E2E_USER": "u"})
        assert_true(result["meta"].get("verdict") == "pass", f"a recovered spec runs to a verdict: {result['meta'].get('verdict')}")

        # --- env placeholder unset: named in the draft, env_missing at run --------------------
        root = workspace("e2e-env-")
        os.environ.pop("E2E_USER", None)
        draft, result = _flow(root, _adapter(), "pass")
        assert_true(_draft_info(draft).get("missing_env") == ["E2E_USER"] and "E2E_USER (not set)" in draft["content"], "an unset placeholder is flagged in the draft, not refused")
        assert_true(result["meta"]["e2e"].get("reason") == "env_missing" and "secrets.json" in result["content"], f"an unset ${{ENV}} is env_missing: {result['meta']['e2e'].get('reason')}")
        # Nothing used to create the file the user was told to fill. The draft now does.
        template = json.loads(secrets_path(root).read_text(encoding="utf-8"))
        assert_true(
            template == {"default": "default", "profiles": [{"name": "default", "credentials": {"E2E_USER": "", "E2E_PASS": ""}}]},
            f"the draft writes the registry's shape and names, whatever the scenario referenced: {template}",
        )
        assert_true("created now with empty slots" in draft["content"] and draft["meta"]["e2e"]["secrets"].get("template_created") is True, "and says so in the draft")
        assert_true(_draft(root, _adapter())["meta"]["e2e"]["secrets"].get("template_created") is None, "an existing file is never rewritten")
        assert_true(_draft_info(_draft(root, _adapter())).get("missing_env") == ["E2E_USER"], "an empty slot is unfilled, not an empty credential")

        # --- secrets.json supplies one profile's values, which never leave the run --------------
        root = workspace("e2e-secrets-")
        from_file = "file.operator@internal.example"
        other = "admin.operator@internal.example"
        target = secrets_path(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"default": "qa", "profiles": [
            {"name": "qa", "credentials": {"E2E_USER": from_file, "E2E_PASS": ""}},
            {"name": "admin", "credentials": {"E2E_USER": other}},
        ]}), encoding="utf-8")
        adapter = _adapter()
        draft, result = _flow(root, adapter, "pass")
        assert_true(_draft_info(draft).get("missing_env") == [], "a key in the default profile counts as set")
        assert_true(result["meta"].get("verdict") == "pass" and result["meta"]["e2e"]["secrets"].get("profile") == "qa", f"the run resolves from the default profile: {result['meta']['e2e'].get('secrets')}")
        dumped = json.dumps(result)
        assert_true(from_file not in dumped and other not in dumped and all(from_file not in c["prompt"] and other not in c["prompt"] for c in adapter.calls), "no profile's value reaches a prompt or the result")
        _, chosen = _flow(root, _adapter(), "pass", settings={"secrets_profile": "admin"})
        assert_true(chosen["meta"]["e2e"]["secrets"].get("profile") == "admin" and chosen["meta"].get("verdict") == "pass", f"the request picks another account by name: {chosen['meta']['e2e'].get('secrets')}")
        absent = _run(root, _adapter(), _scenario(), settings={"secrets_profile": "nobody"})
        assert_true(absent["meta"]["e2e"].get("reason") == "secrets_invalid" and "no profile 'nobody'" in absent["content"], f"an unknown profile is refused by name: {absent['content'][:400]}")
        bad_name = settings_from({"secrets_profile": "a b"})[1]
        assert_true(bad_name and "secrets_profile" in bad_name[0], f"a profile name is validated as a name: {bad_name}")

        # --- read-only POSTs: validated as settings, proposed by the draft, confirmed by the user ----
        bad_reads = settings_from({"allowed_read_only_requests": ["PUT /api/items", "POST /api/*", "POST"]})[1]
        assert_true(len(bad_reads) == 3 and all("allowed_read_only_requests" in e for e in bad_reads), f"each malformed read-only entry is named: {bad_reads}")
        assert_true(
            settings_from({"allowed_read_only_requests": ["POST /api/search", "POST http://localhost:9000/graphql"]})[1] == [],
            "a POST to an exact endpoint, on base_url or another origin, validates",
        )
        root = workspace("e2e-readonly-")
        proposal = _SPEC_REPLY.replace(
            "coverage_gap:\n- login-valid-user\n",
            "coverage_gap:\n- login-valid-user\n\nread_only_requests:\n- method: POST | endpoint: /api/search | source_refs: src/pages/Login.tsx:3 | reason: the search handler only reads\n",
        )
        assert_true(proposal != _SPEC_REPLY, "fixture assumption: coverage_gap is where the replace expects it")
        draft = _draft(root, _adapter(spec=proposal))
        info = _draft_info(draft)
        assert_true(
            info.get("status") == "ready"
            and info.get("read_only_requests") == [{"method": "POST", "endpoint": "/api/search", "source_refs": ["src/pages/Login.tsx:3"], "reason": "the search handler only reads"}],
            f"a grounded proposal reaches the draft: {info.get('status')} {info.get('errors')} {info.get('read_only_requests')}",
        )
        assert_true("- POST /api/search | source_refs: src/pages/Login.tsx:3" in draft["content"] and "confirm each" in draft["content"], "and is listed for the user to confirm")
        request_settings = json.loads(request_path(root, _SESSION_ID).read_text(encoding="utf-8")).get("settings") or {}
        assert_true("allowed_read_only_requests" not in request_settings, "a proposal never writes itself into the request settings")
        draft = _draft(root, _adapter(spec=proposal.replace("src/pages/Login.tsx:3 | reason", "src/routes/gone.ts:3 | reason")))
        assert_true(
            _draft_info(draft).get("status") == "invalid" and draft["meta"]["e2e"].get("reason") == "spec_invalid"
            and any(e.startswith("read_only_requests[0]") for e in _draft_info(draft).get("errors") or []),
            f"an ungrounded proposal makes the draft invalid: {_draft_info(draft).get('errors')}",
        )

        root = workspace("e2e-secrets-bad-")
        target = secrets_path(root)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps({"profiles": [{"name": "qa", "credentials": {"E2E_USER": 12345, "hunter2-secret as key": "x", "e2e_pass": "y"}}]}), encoding="utf-8")
        result = _run(root, _adapter(), _scenario())
        assert_true(
            result["meta"]["e2e"].get("reason") == "secrets_invalid" and "profile 'qa': E2E_USER must be a string" in result["content"]
            and "credential #2: not a registered name" in result["content"],
            f"a malformed entry is named by location: {result['content'][:600]}",
        )
        assert_true(
            any("credential #3" in e and "did you mean E2E_PASS" in e for e in load_secrets(root)[1]),
            f"a mis-cased name is pointed at its registered spelling: {load_secrets(root)[1]}",
        )
        assert_true("hunter2-secret" not in json.dumps(result), "and neither a value nor a mistyped key is ever echoed")
        target.write_text('{"profiles": [{"name": "qa", "credentials": {"E2E_USER": "hunter2-secret",}}]}', encoding="utf-8")
        result = _run(root, _adapter(), _scenario())
        assert_true("not valid JSON (line 1)" in result["content"] and "hunter2-secret" not in json.dumps(result), f"broken JSON is named by line only: {result['content'][:400]}")
        target.write_text(json.dumps({"profiles": [{"name": "qa", "credentials": {"E2E_USER": "a"}}, {"name": "admin", "credentials": {"E2E_USER": "b"}}]}), encoding="utf-8")
        result = _run(root, _adapter(), _scenario())
        assert_true(result["meta"]["e2e"].get("reason") == "secrets_invalid" and "set settings.secrets_profile" in result["content"], "two profiles and no default must be chosen explicitly, never guessed")
        old_format = json.dumps({"default": "qa", "profiles": {"qa": {"E2E_USER": "hunter2-secret"}}})
        target.write_text(old_format, encoding="utf-8")
        result = _run(root, _adapter(), _scenario())
        assert_true(
            result["meta"]["e2e"].get("reason") == "secrets_invalid" and "the old format" in result["content"] and '"credentials"' in result["content"]
            and "hunter2-secret" not in json.dumps(result),
            f"the old object format is refused with the new shape, never read: {result['content'][:500]}",
        )
        assert_true(target.read_text(encoding="utf-8") == old_format, "and the file is left exactly as the user wrote it")
        for body, fragment in (
            ({"profiles": [{"name": "qa", "credentials": {}}, {"name": "qa", "credentials": {}}]}, "duplicate profile name 'qa'"),
            ({"default": "prod", "profiles": [{"name": "qa", "credentials": {}}]}, "default names no profile"),
            ({"profiles": [{"name": "q a", "credentials": {}}]}, "name must match"),
            ({"profiles": [{"name": "qa", "credentials": ["E2E_USER"]}]}, "credentials must be an object"),
            ({"profiles": "qa"}, "must be a list"),
        ):
            target.write_text(json.dumps(body), encoding="utf-8")
            values, errors, _ = load_secrets(root)
            assert_true(values == {} and any(fragment in e for e in errors), f"{fragment}: {errors}")

        # The generator: registry shape, profile names from the settings, never a second writer.
        root = workspace("e2e-secrets-template-")
        target = secrets_path(root)
        assert_true(ensure_secrets_template(root, ["qa", "admin"]) is True, "a missing file is created")
        written = json.loads(target.read_text(encoding="utf-8"))
        assert_true(
            written["default"] == "qa" and [p["name"] for p in written["profiles"]] == ["qa", "admin"]
            and all(p["credentials"] == {"E2E_USER": "", "E2E_PASS": ""} for p in written["profiles"]),
            f"one profile per requested name, first is default, every registered name empty: {written}",
        )
        target.write_text("user's own file", encoding="utf-8")
        assert_true(ensure_secrets_template(root, ["other"]) is False and target.read_text(encoding="utf-8") == "user's own file", "an existing file is never rewritten")
        assert_true(not [p for p in target.parent.iterdir() if p.suffix == ".tmp"], "no temporary file is left behind")
        target.unlink()
        import threading

        outcomes: list[bool] = []
        barrier = threading.Barrier(8)

        def race(name: str) -> None:
            barrier.wait()
            outcomes.append(ensure_secrets_template(root, [name]))

        threads = [threading.Thread(target=race, args=(f"p{i}",)) for i in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        survivor = json.loads(target.read_text(encoding="utf-8"))
        assert_true(
            outcomes.count(True) == 1 and len(survivor["profiles"]) == 1 and survivor["default"] == survivor["profiles"][0]["name"],
            f"eight concurrent drafts: exactly one creates the file, the rest leave it alone: {outcomes} {survivor}",
        )
        assert_true(not [p for p in target.parent.iterdir() if p.suffix == ".tmp"], "and none leaves a temporary file")
        assert_true(
            settings_from({"secrets_template_profiles": ["qa", "qa", "a b"]})[1] == [
                "settings.secrets_template_profiles[1]: duplicate profile 'qa'",
                "settings.secrets_template_profiles[2]: a profile name, [A-Za-z0-9_-], at most 64 characters",
            ],
            "template profile names are validated as names",
        )
        from core.evidence.e2e.request import template_profiles

        assert_true(
            template_profiles({"secrets_profile": "admin", "secrets_template_profiles": ["qa"]}) == ["admin", "qa"] and template_profiles({}) == ["default"],
            "the selected profile leads the template; with nothing named, one `default`",
        )

        # --- request missing, malformed, or for another session -------------------------------
        root = workspace("e2e-norequest-")
        adapter = _adapter()
        result = _execute(root, adapter, "verify-browser")
        assert_true(result["meta"]["e2e"].get("reason") == "request_missing" and result["meta"].get("verdict") == "incomplete", f"no request is incomplete: {result['meta']}")
        assert_true(adapter.calls == [] and _e2e_rows(root) == [] and _verify_exit_code("verify", result) == 2, "nothing spent, nothing recorded, non-zero exit")

        _write_request(root, "draft", settings={"spec_path": ".workflow/e2e/spec.json", "headless": "no"})
        result = _execute(root, adapter, "verify-browser")
        assert_true(
            result["meta"]["e2e"].get("reason") == "request_invalid" and "settings.spec_path: unknown key" in result["content"] and "settings.headless: str, expected bool" in result["content"],
            f"a typo or a wrong type stops the run: {result['content'][:400]}",
        )
        settings, errors = settings_from({"allowed_mutation_paths": ["/login"]})
        assert_true(
            len(errors) == 1 and "settings.allowed_mutation_paths: removed" in errors[0] and "loopback" in errors[0] and "allowed_read_only_requests" in errors[0],
            f"the removed allow-list is a migration error naming the way forward, not a silent no-op: {errors}",
        )
        assert_true("allowed_mutation_paths" not in settings_from(None)[0], "and it is gone from the defaults")
        _write_request(root, "draft", settings={"allowed_mutation_paths": ["/login"]})
        result = _execute(root, adapter, "verify-browser")
        assert_true(result["meta"]["e2e"].get("reason") == "request_invalid" and "allowed_mutation_paths: removed" in result["content"], "a request still using it stops before anything runs")
        _write_request(root, "run")
        result = _execute(root, adapter, "verify-browser")
        assert_true(result["meta"]["e2e"].get("reason") == "request_invalid" and "needs the confirmed scenario" in result["content"], "a run without a scenario is refused")
        _write_request(root, "draft")
        result = _execute(root, adapter, "verify-browser", session_id="another-session")
        assert_true(result["meta"]["e2e"].get("reason") == "request_missing" and adapter.calls == [], "one session never reads another session's request")

        # --- base_url policy: a real domain needs the opt-in, a .test name does not --------------
        root = workspace("e2e-remote-")
        adapter = _adapter()
        draft = _draft(root, adapter, settings={"base_url": "http://staging.example.com"})
        assert_true(draft["meta"]["e2e"].get("reason") == "spec_invalid" and adapter.calls == [], "a real domain is refused before any call")
        draft = _draft(root, adapter, settings={"base_url": "http://staging.example.com", "allow_remote": True})
        assert_true(draft["meta"]["e2e"].get("reason") == "spec_invalid" and adapter.calls == [], "allow_remote alone is not enough")
        remote = {"base_url": "http://staging.example.com", "allow_remote": True, "allowed_origins": ["http://staging.example.com"]}
        draft = _draft(root, adapter, settings=remote)
        policy = next(c for c in draft["meta"]["e2e"]["preflight"] if c["name"] == "base_url_policy")
        assert_true(_draft_info(draft).get("status") == "ready" and policy["detail"] == "remote origin allow-listed", f"a confirmed remote origin is admitted: {policy}")
        virtual = _draft(root, adapter, settings={"base_url": "http://app.test"})
        policy = next(c for c in virtual["meta"]["e2e"]["preflight"] if c["name"] == "base_url_policy")
        assert_true(
            _draft_info(virtual).get("status") == "ready" and policy["detail"] == "virtual dev host",
            f"a .test name is admitted on its own: {policy}",
        )

        # --- writes: loopback and .test by name, every other host by ADDRESS, pinned -------
        import core.evidence.e2e.preflight as _pf

        saved_resolver = _pf.host_addresses
        try:
            # `.test` is local development by definition, exactly like localhost: it takes
            # writes with no lookup and no pin. The resolver raising proves nothing asked it.
            _pf.host_addresses = lambda host: (_ for _ in ()).throw(AssertionError(f"looked up {host}"))
            dev = _draft(root, adapter, settings={"base_url": "http://app.test", "allow_side_effects": True})
            dev_network = dev["meta"]["e2e"].get("network") or {}
            assert_true(
                _draft_info(dev).get("status") == "ready"
                and dev_network.get("write_hosts") == ["app.test"]
                and not dev_network.get("pins"),
                f"a .test app takes writes like localhost, without a lookup or a pin: {_draft_info(dev).get('errors')} {dev_network}",
            )
            dev_firefox = _draft(root, adapter, settings={"base_url": "http://app.test", "allow_side_effects": True, "browser": "firefox"})
            assert_true(
                _draft_info(dev_firefox).get("status") == "ready",
                "and with nothing to pin, any browser may write to it",
            )

            # Every other name is judged by what it resolves to.
            calls_before = len(adapter.calls)
            _pf.host_addresses = lambda host: ["93.184.216.34"]
            writes = {
                "base_url": "http://devbox.lan",
                "allow_remote": True,
                "allowed_origins": ["http://devbox.lan"],
                "allow_side_effects": True,
            }
            draft = _draft(root, adapter, settings=writes)
            assert_true(
                draft["meta"]["e2e"].get("reason") == "spec_invalid"
                and "resolves to public address 93.184.216.34" in draft["content"]
                and len(adapter.calls) == calls_before,
                f"a write host that resolves to a public address is refused before any call: {draft['content'][:300]}",
            )
            _pf.host_addresses = lambda host: ["192.168.1.40", "8.8.8.8"]
            draft = _draft(root, adapter, settings=writes)
            assert_true(
                draft["meta"]["e2e"].get("reason") == "spec_invalid" and "public address 8.8.8.8" in draft["content"],
                "one public address among private ones still refuses the whole name",
            )
            _pf.host_addresses = lambda host: ["169.254.169.254"]
            draft = _draft(root, adapter, settings=writes)
            assert_true(
                draft["meta"]["e2e"].get("reason") == "spec_invalid" and "169.254.169.254" in draft["content"],
                "link-local is not 'private enough': that address is the cloud metadata service",
            )
            _pf.host_addresses = lambda host: ["192.168.1.40"]
            draft = _draft(root, adapter, settings=writes)
            assert_true(_draft_info(draft).get("status") == "ready", f"a private address admits the write: {_draft_info(draft).get('errors')}")
            network = draft["meta"]["e2e"].get("network") or {}
            assert_true(
                network.get("pins") == {"devbox.lan": "192.168.1.40"} and network.get("write_hosts") == ["devbox.lan"],
                f"and the decision travels as a pin, so the guard never asks DNS again: {network}",
            )
            # A name that resolves to loopback TODAY is still a name its owner can repoint:
            # it is pinned, unlike a loopback name, which resolves nowhere else.
            _pf.host_addresses = lambda host: ["127.0.0.1"]
            rebinding = {**writes, "base_url": "http://127.0.0.1.nip.io", "allowed_origins": ["http://127.0.0.1.nip.io"]}
            draft = _draft(root, adapter, settings=rebinding)
            assert_true(
                _draft_info(draft).get("status") == "ready"
                and (draft["meta"]["e2e"].get("network") or {}).get("pins") == {"127.0.0.1.nip.io": "127.0.0.1"},
                f"a name resolving to loopback is pinned, so it cannot rebind mid-run: {draft['meta']['e2e'].get('network')}",
            )
            _pf.host_addresses = lambda host: ["192.168.1.40"]
            draft = _draft(root, adapter, settings={**writes, "browser": "firefox"})
            assert_true(
                draft["meta"]["e2e"].get("reason") == "spec_invalid" and "resolver pinned" in draft["content"],
                f"a browser whose resolver cannot be pinned may not write off loopback: {draft['content'][:300]}",
            )
            _pf.host_addresses = lambda host: (_ for _ in ()).throw(OSError("Name or service not known"))
            draft = _draft(root, adapter, settings=writes)
            assert_true(draft["meta"]["e2e"].get("reason") == "spec_invalid" and "OSError" in draft["content"], "a name that does not resolve is not approved by default")
        finally:
            _pf.host_addresses = saved_resolver

        _pf.host_addresses = lambda host: ["93.184.216.34"]
        try:
            result = _run(
                root,
                adapter,
                _scenario(),
                settings={"base_url": "http://devbox.lan", "allow_remote": True, "allowed_origins": ["http://devbox.lan"], "allow_side_effects": True},
                env={"E2E_USER": "u"},
            )
        finally:
            _pf.host_addresses = saved_resolver
        assert_true(result["meta"].get("verdict") == "incomplete" and result["meta"]["e2e"].get("reason") == "spec_invalid", "a run is judged by the same check")
        local_writes = _draft(workspace("e2e-local-writes-"), _adapter(), settings={"base_url": "http://127.0.0.1:8000", "allow_side_effects": True})
        assert_true(_draft_info(local_writes).get("status") == "ready", f"a loopback app may take writes: {_draft_info(local_writes).get('errors')}")
        assert_true(
            not ((local_writes["meta"]["e2e"].get("network") or {}).get("pins")),
            "and a loopback name needs no lookup and no pin",
        )

        # --- a stage-1 spec that navigates off-origin: invalid draft, refused run -------------------
        root = workspace("e2e-offorigin-")
        off_origin = _SPEC_REPLY.replace('{"id": "open-login", "action": "goto", "url": "/login"}', '{"id": "open-login", "action": "goto", "url": "https://evil.example/login"}')
        assert_true(off_origin != _SPEC_REPLY, "fixture assumption: the goto step is where the replace expects it")
        adapter = _adapter(spec=off_origin)
        draft = _draft(root, adapter)
        assert_true(_draft_info(draft).get("status") == "invalid" and draft["meta"]["e2e"].get("reason") == "spec_invalid", "off-origin navigation is an invalid draft")
        result = _run(root, adapter, _scenario(off_origin), env={"E2E_USER": "u"})
        assert_true(result["meta"]["e2e"].get("reason") == "spec_invalid" and result["meta"].get("verdict") == "incomplete", "and a run that ignores the draft is refused again")
        assert_true(_commands(adapter) == ["e2e_spec"], "no player run and no reviewer after a refused spec")
        assert_true("artifacts" not in result["meta"]["e2e"], "nothing was archived for a run that never started")

        # --- grounding: a claim citing a file that does not exist ---------------------------------
        root = workspace("e2e-ungrounded-")
        ungrounded = _SPEC_REPLY.replace("source_refs: src/pages/Login.tsx:12", "source_refs: src/pages/Gone.tsx:12")
        assert_true(ungrounded != _SPEC_REPLY, "fixture assumption: the prose ref is where the replace expects it")
        adapter = _adapter(spec=ungrounded)
        draft = _draft(root, adapter)
        assert_true(_draft_info(draft).get("status") == "invalid" and "names no file" in draft["content"], f"an ungrounded claim is an invalid draft: {draft['content'][:300]}")
        result = _run(root, adapter, _scenario(ungrounded), env={"E2E_USER": "u"})
        refused = _e2e_rows(root)
        assert_true(
            len(refused) == 1 and refused[0]["verdict"] == "incomplete" and refused[0]["reason"] == "spec_invalid" and refused[0]["browser_runs"] == 0,
            f"a run refused before the browser still counts, with its reason: {refused}",
        )

        # --- existing tests: only through the user's command; a pass proves the claim it covers ---
        covered_spec = _SPEC_REPLY.replace(
            "existing_tests:\n- none", "existing_tests:\n- path: e2e/login.spec.ts | covers: login-valid-user | confidence: high"
        ).replace(
            '"source"}},\n   {"id": "assert-dashboard", "action": "expect_url", "contains": "/dashboard", "claim_id": "login-valid-user"}\n ]}',
            '"source"}}\n ]}',
        )
        assert_true(covered_spec.count("existing_tests:\n- path:") == 1 and '"expect_url"' not in covered_spec, "fixture assumption: existing test listed, assertion removed")
        typed = "existing.user@internal.example"
        runner_code = "import os, sys; print('ran', sys.argv[1:], os.environ.get('E2E_USER')); sys.exit(int(os.environ.get('E2E_EXISTING_EXIT', '0')))"
        existing_settings = {
            "existing_test_command": [sys.executable, "-c", runner_code, "{files}"],
            "existing_test_allowlist": ["e2e/*.spec.ts"],
            "existing_test_timeout_s": 60,
        }

        def with_existing_test(prefix: str) -> Path:
            root = workspace(prefix)
            (root / "e2e").mkdir()
            (root / "e2e" / "login.spec.ts").write_text("// the project's own test\n", encoding="utf-8")
            return root

        root = with_existing_test("e2e-existing-")
        _, result = _flow(root, _adapter(spec=covered_spec), "pass", {"E2E_USER": typed, "E2E_EXISTING_EXIT": "0"}, settings=existing_settings)
        e2e_meta = result["meta"]["e2e"]
        log = (Path(e2e_meta["artifacts"]) / "existing_tests.log").read_text(encoding="utf-8")
        assert_true(e2e_meta.get("existing_tests", {}).get("status") == "passed" and result["meta"].get("verdict") == "pass", f"a passing existing test proves its claim: {e2e_meta.get('existing_tests')} {result['meta'].get('verdict')}")
        assert_true("e2e:existing_test: e2e/login.spec.ts covers login-valid-user — pass" in result["content"], "the contract records the run and its result")
        assert_true("e2e/login.spec.ts" in log and typed not in log and "${E2E_USER}" in log, f"the log shows the command ran, scrubbed: {log}")

        root = with_existing_test("e2e-existing-fail-")
        adapter = _adapter(spec=covered_spec)
        _, result = _flow(root, adapter, "pass", {"E2E_USER": typed, "E2E_EXISTING_EXIT": "1"}, settings=existing_settings)
        assert_true(
            result["meta"].get("verdict") == "incomplete" and result["meta"]["e2e"].get("reason") == "unknown_origin",
            f"a failing existing test is unknown, not a pass and not an app failure: {result['meta']['e2e'].get('reason')}",
        )
        assert_true(_commands(adapter) == ["e2e_spec"], "an incomplete run skips the reviewer")

        root = with_existing_test("e2e-existing-off-")
        draft = _draft(root, _adapter(spec=covered_spec), env={"E2E_USER": typed})
        assert_true(
            draft["meta"]["e2e"].get("reason") == "spec_invalid" and "never asserted" in draft["content"],
            "with no command in the settings, a claim covered only by an existing test is refused",
        )

        # --- failure artifacts: player text scrubbed (raw + URL-encoded), size budget enforced ------
        root = workspace("e2e-artifacts-")
        typed = "artifact.user@internal.example"
        _, result = _flow(root, _adapter(), "artifacts", {"E2E_USER": typed}, settings={"artifact_max_mb": 1})
        e2e_meta = result["meta"]["e2e"]
        e2e_dir = Path(e2e_meta["artifacts"])
        html = (e2e_dir / "step02.html").read_text(encoding="utf-8")
        assert_true(
            typed not in html and quote(typed, safe="") not in html and html.count("${E2E_USER}") == 2,
            f"the echoed value is scrubbed in both forms: {html}",
        )
        assert_true(
            not (e2e_dir / "trace.zip").exists() and e2e_meta.get("artifacts_pruned") == ["trace.zip"],
            f"a trace over artifact_max_mb is pruned: {e2e_meta.get('artifacts_pruned')}",
        )
        evidence = (e2e_dir / "evidence.md").read_text(encoding="utf-8")
        assert_true(
            "- step02.html (html, step 2)" in evidence and "- trace.zip (trace) — pruned" in evidence,
            f"the evidence block lists what was kept and what was pruned:\n{evidence}",
        )
        assert_true(typed not in json.dumps(result) and result["meta"].get("verdict") == "fail", "nothing leaks into the result; the app failure still fails")

        # --- resolved ${ENV} values echoed by the page never leave the run ---------------------------
        root = workspace("e2e-secret-")
        secret = "operator.login@internal.example"
        echo_spec = _SPEC_REPLY.replace('"contains": "/dashboard"', '"contains": "/dashboard?as=${E2E_USER}"')
        adapter = _adapter(spec=echo_spec)
        _, result = _flow(root, adapter, "app_fail", {"E2E_USER": secret})
        assert_true(_commands(adapter) == ["e2e_spec", "verify"], "the echo case reaches the reviewer")
        assert_true(secret not in adapter.calls[1]["prompt"], "a resolved value echoed in `expected` must not reach the stage-3 prompt")
        assert_true("${E2E_USER}" in adapter.calls[1]["prompt"], "the placeholder stands in for it")
        assert_true(secret not in json.dumps(result), "nor the result content or meta")
        e2e_dir = Path(result["meta"]["e2e"]["artifacts"])
        leaked = [p.name for p in e2e_dir.iterdir() if secret in p.read_text(encoding="utf-8")]
        assert_true(not leaked, f"nor any archived artifact: {leaked}")

        # --- usage: every provider invocation recorded exactly once ---------------------------------
        root = workspace("e2e-usage-")
        adapter = _adapter()
        _flow(root, adapter, "pass", {"E2E_USER": "u"})
        rows = _usage_rows(root)
        assert_true(len(rows) == len(adapter.calls) == 2, f"draft + run: one row per invocation: rows={len(rows)} calls={len(adapter.calls)}")
        assert_true(sorted(r.get("command") for r in rows) == ["verify", "verify-browser"], f"the draft is billed as verify-browser, the run as verify: {[r.get('command') for r in rows]}")

        root = workspace("e2e-usage-reviewfail-")
        adapter = _adapter(fail={"verify"})
        _, result = _flow(root, adapter, "pass", {"E2E_USER": "u"})
        rows = _usage_rows(root)
        assert_true(len(rows) == len(adapter.calls) == 2, f"a failed reviewer is not billed twice: rows={len(rows)} calls={len(adapter.calls)}")
        assert_true(result["meta"].get("verdict") == "incomplete" and "hybrid review: not obtained" in result["content"], "a failed reviewer is a declared gap")

        root = workspace("e2e-usage-specfail-")
        adapter = _adapter(fail={"e2e_spec"})
        draft = _draft(root, adapter)
        rows = _usage_rows(root)
        assert_true(not draft.get("ok") and len(rows) == len(adapter.calls) == 1, f"a failed spec stage is returned and billed once: ok={draft.get('ok')} rows={len(rows)}")

        # --- /.verify stays delegated | syntax: the browser is only ever /.verify-browser ----------
        root = workspace("e2e-syntax-", mode="syntax")
        adapter = _adapter()
        result = _execute(root, adapter, "verify")
        assert_true(result["meta"].get("verify_mode") == "syntax" and adapter.calls == [], "syntax mode still never calls a provider")
    finally:
        e2e_runner.display_available = real_display
        for root in roots:
            shutil.rmtree(root, ignore_errors=True)
