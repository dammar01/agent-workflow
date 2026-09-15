"""Turn player events into a browser verdict with an origin per failure.

Three origins, not two. `app` means the evidence says the application broke the claim;
`harness` means the evidence says the scenario or the runtime broke; `unknown` means
the evidence cannot tell — and `unknown` is never promoted to `harness`, because that
is exactly the move that would hide a regression behind a flaky-selector story.

Player event protocol (one JSON object per stdout line):
  {"type": "progress", "step": n, "action": str, "status": "passed|failed|skipped",
   "claim_id": str|None, "expected": any, "actual": any, "url_after": str|None,
   "duration_ms": int, "error": {"kind": str, "detail": str}|None,
   "selector_provenance": str|None, "page_stable": bool|None}
  {"type": "observation", "kind": "http_5xx|page_error|console_error|mutation_blocked", "url": str,
   "status": int|None, "detail": str, "same_origin": bool, "main_request": bool,
   "enforced": bool}   # console_error only: settings.fail_on_console_error
  {"type": "harness", "reason": str, "detail": str}
  {"type": "artifact", "kind": "html|screenshot|trace|skipped|error", "name": str|None,
   "step": int|None, "detail": str|None, "bytes": int|None, "pruned": bool}
  {"type": "heartbeat"}
  {"type": "result", "status": "finished|aborted"}
"""

from __future__ import annotations

ORIGIN_APP = "app"
ORIGIN_HARNESS = "harness"
ORIGIN_UNKNOWN = "unknown"

# Reasons a run ends without a verdict. Every one of them maps to `incomplete`; the
# reason is what the user needs to fix, so it travels verbatim into `not_verified`.
INCOMPLETE_REASONS = frozenset(
    {
        "playwright_missing",
        "browser_missing",
        "base_url_unreachable",
        "spec_invalid",
        "harness_error",
        "unknown_origin",
        "stuck",
        "timeout",
        "launch_failed",
        "output_truncated",
        "player_unavailable",
        "env_missing",
    }
)

_GROUNDED = frozenset({"source", "existing_test", "runtime_probe"})
_SELECTOR_ERRORS = frozenset({"selector_missing", "selector_ambiguous", "not_visible"})


def classify_step(event: dict) -> str:
    """Origin of one failed step."""
    error = event.get("error") or {}
    kind = str(error.get("kind") or "")
    action = str(event.get("action") or "")
    provenance = event.get("selector_provenance")
    stable = event.get("page_stable")

    # mutation_blocked: the player's own write guard refused a request (allow_side_effects
    # false). The application did nothing wrong; the scenario asked for a write it may not do.
    if kind in {"launch_failed", "browser_missing", "navigation_blocked", "mutation_blocked", "harness_error"}:
        return ORIGIN_HARNESS
    if kind in _SELECTOR_ERRORS:
        if provenance == "heuristic":
            return ORIGIN_HARNESS
        if provenance in _GROUNDED:
            # A selector the codebase itself named is gone. On a page that finished
            # loading that is the application changing; on one that never settled it
            # could be either, and guessing costs a hidden regression.
            return ORIGIN_APP if stable is True else ORIGIN_UNKNOWN
        return ORIGIN_UNKNOWN
    if kind == "timeout":
        return ORIGIN_UNKNOWN
    if action in {"expect_url", "expect_title"}:
        return ORIGIN_APP
    if action == "expect_dom":
        return ORIGIN_APP if provenance != "heuristic" else ORIGIN_UNKNOWN
    if kind in {"http_5xx", "page_error"}:
        return ORIGIN_APP
    return ORIGIN_UNKNOWN


def build_report(events: list[dict], scenario: dict | None, *, run_meta: dict | None = None) -> dict:
    """Fold the event stream into one report the normaliser can read.

    Returns:
      browser_verdict: pass | fail | incomplete
      reason: an INCOMPLETE_REASONS value when incomplete, else None
      claims: {id: {"severity", "status": proven|failed|unproven, "origin", "step", "detail"}}
      steps: {"total", "passed", "failed", "skipped"}
      failures: [{step, action, claim_id, origin, expected, actual, detail}]
      observations: [{kind, url, status, detail, same_origin, main_request}]
      harness: [{reason, detail}]
    """
    run_meta = run_meta or {}
    claims: dict[str, dict] = {}
    for claim in (scenario or {}).get("claims") or []:
        if isinstance(claim, dict) and claim.get("id"):
            claims[str(claim["id"])] = {
                "severity": claim.get("severity", "blocking"),
                "status": "unproven",
                "origin": None,
                "step": None,
                "detail": None,
            }

    steps = {"total": 0, "passed": 0, "failed": 0, "skipped": 0}
    failures: list[dict] = []
    observations: list[dict] = []
    harness: list[dict] = []
    artifacts: list[dict] = []
    probes: list[dict] = []
    finished = False

    for event in events:
        kind = event.get("type")
        if kind == "progress":
            steps["total"] += 1
            status = event.get("status")
            cid = event.get("claim_id")
            if status == "passed":
                steps["passed"] += 1
                if event.get("action") == "probe":
                    probes.append({"step": event.get("step"), "probe": (event.get("actual") or {}).get("probe")})
                if cid in claims and claims[cid]["status"] == "unproven":
                    claims[cid]["status"] = "proven"
                    claims[cid]["step"] = event.get("step")
            elif status == "failed":
                steps["failed"] += 1
                origin = classify_step(event)
                detail = (event.get("error") or {}).get("detail") or ""
                failures.append(
                    {
                        "step": event.get("step"),
                        "action": event.get("action"),
                        "claim_id": cid,
                        "origin": origin,
                        "expected": event.get("expected"),
                        "actual": event.get("actual"),
                        "detail": detail,
                        "probe": event.get("probe"),
                    }
                )
                if cid in claims:
                    claims[cid].update({"status": "failed", "origin": origin, "step": event.get("step"), "detail": detail})
            else:
                steps["skipped"] += 1
        elif kind == "observation":
            observations.append(
                {
                    "kind": event.get("kind"),
                    "url": event.get("url"),
                    "status": event.get("status"),
                    "detail": event.get("detail"),
                    "same_origin": bool(event.get("same_origin")),
                    "main_request": bool(event.get("main_request")),
                    "enforced": bool(event.get("enforced")),
                }
            )
        elif kind == "artifact":
            artifacts.append({key: event.get(key) for key in ("kind", "name", "step", "detail", "bytes", "pruned")})
        elif kind == "harness":
            harness.append({"reason": event.get("reason") or "harness_error", "detail": event.get("detail") or ""})
        elif kind == "result":
            finished = event.get("status") == "finished"

    # Existing project tests (run_meta["existing_tests"], from existing_tests.run): a pass
    # proves the claims the spec says it covers, unless the browser already failed them; a
    # failure or timeout makes them unknown; a runner that never started is the harness's.
    existing = run_meta.get("existing_tests") or {}
    tested_files = ", ".join(existing.get("files") or [])
    for cid in existing.get("covers") or []:
        claim = claims.get(cid)
        if claim is None or claim["status"] == "failed":
            continue
        status = existing.get("status")
        if status == "passed":
            claim.update({"status": "proven", "detail": f"existing test passed: {tested_files}"})
            continue
        kind = {"failed": "existing_test_failed", "timeout": "timeout"}.get(str(status), "harness_error")
        origin = classify_step({"action": "existing_test", "error": {"kind": kind}})
        detail = f"existing test {status}: {tested_files}"
        failures.append(
            {
                "step": None,
                "action": "existing_test",
                "claim_id": cid,
                "origin": origin,
                "expected": {"exit": 0},
                "actual": {"status": status, "returncode": existing.get("returncode")},
                "detail": detail,
                "probe": None,
            }
        )
        claim.update({"status": "failed", "origin": origin, "step": None, "detail": detail})

    # A same-origin 5xx on the main request or an uncaught same-origin page error is an
    # application failure even when every assertion passed — the assertion may simply
    # not have looked. A same-origin console error joins them only when the project
    # opted in (fail_on_console_error). Third-party and background noise stays a warning.
    app_errors = [
        obs
        for obs in observations
        if obs["same_origin"]
        and (
            (obs["kind"] == "http_5xx" and obs["main_request"])
            or obs["kind"] == "page_error"
            or (obs["kind"] == "console_error" and obs["enforced"])
        )
    ]

    reason = None
    if run_meta.get("timed_out"):
        reason = "timeout"
    elif run_meta.get("stalled"):
        reason = "stuck"
    elif run_meta.get("truncated"):
        reason = "output_truncated"
    elif run_meta.get("malformed"):
        reason = "harness_error"
    elif harness:
        reason = harness[0]["reason"] if harness[0]["reason"] in INCOMPLETE_REASONS else "harness_error"
    elif not finished:
        reason = "harness_error"

    app_failures = [f for f in failures if f["origin"] == ORIGIN_APP]
    undecided = [f for f in failures if f["origin"] != ORIGIN_APP]
    blocking_app = [
        f for f in app_failures if claims.get(f["claim_id"] or "", {}).get("severity", "blocking") == "blocking"
    ]

    if blocking_app or app_errors:
        verdict = "fail"
        reason = None
    elif reason is not None:
        verdict = "incomplete"
    elif undecided:
        verdict = "incomplete"
        reason = "unknown_origin" if any(f["origin"] == ORIGIN_UNKNOWN for f in undecided) else "harness_error"
    elif any(c["status"] != "proven" for c in claims.values() if c["severity"] == "blocking"):
        verdict = "incomplete"
        reason = "harness_error"
    else:
        verdict = "pass"

    return {
        "browser_verdict": verdict,
        "reason": reason,
        "claims": claims,
        "steps": steps,
        "failures": failures,
        "app_errors": app_errors,
        "observations": observations,
        "harness": harness,
        "artifacts": artifacts,
        "probes": probes,
        "finished": finished,
    }
