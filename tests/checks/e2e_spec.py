"""The [E2E SPEC] contract: parsing, validation, env placeholders, the missing-section gap."""

from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path

from core.evidence.e2e.spec import (
    env_references,
    ground_claims,
    parse_spec,
    spec_continuation_prompt,
    spec_gap,
    step_selectors,
    substitute_env,
    validate_existing_tests,
    validate_read_only_requests,
    validate_scenario,
)
from tests.checks.support import assert_true

_SCENARIO = {
    "version": 1,
    "feature": "login",
    "claims": [{"id": "login-valid-user", "severity": "blocking", "description": "login opens dashboard", "source_refs": ["src/pages/Login.tsx:12"]}],
    "steps": [
        {"action": "goto", "url": "/login"},
        {"action": "fill", "selector": {"role": "textbox", "name": "Email"}, "selector_provenance": {"type": "source"}, "value": "${E2E_USER}"},
        {"action": "fill", "selector": {"testid": "password"}, "value": "${E2E_PASS}"},
        {"action": "click", "selector": {"role": "button", "name": "Masuk"}},
        {"action": "expect_url", "contains": "/dashboard", "claim_id": "login-valid-user"},
    ],
}


def _reply(scenario: dict, *, with_section: bool = True) -> str:
    head = "[EVIDENCE]\nconfidence: high\n\ngrounded:\n- form posts to /api/login [src/routes/auth.ts:40]\n\n"
    if not with_section:
        return head + "[DIGEST]\nsummary: no spec.\nconfidence: high\n"
    body = (
        "[E2E SPEC]\n"
        "claims:\n"
        "- id: login-valid-user | severity: blocking | description: login opens dashboard | source_refs: src/pages/Login.tsx:12, src/routes/auth.ts:40\n"
        "\nexisting_tests:\n- path: e2e/login.spec.ts | covers: login-valid-user | confidence: medium\n"
        "\ncoverage_gap:\n- none\n"
        "\nscenario_json:\n```json\n" + json.dumps(scenario) + "\n```\n"
        "\nspec_uncertainties:\n- password field label is inferred\n\n"
    )
    return head + body + "[DIGEST]\nsummary: one claim.\nconfidence: high\n"


def _test_e2e_spec_contract() -> None:
    parsed = parse_spec(_reply(_SCENARIO))
    assert_true(parsed["present"] and not parsed["errors"], f"a well-formed reply parses cleanly: {parsed['errors']}")
    assert_true(parsed["claims"][0]["source_refs"] == ["src/pages/Login.tsx:12", "src/routes/auth.ts:40"], "source_refs split on commas")
    assert_true(parsed["existing_tests"][0]["covers"] == ["login-valid-user"], "existing_tests carry the claim ids they cover")
    assert_true(parsed["uncertainties"] == ["password field label is inferred"], "spec_uncertainties are kept for the evidence block")
    assert_true(parsed["scenario"]["steps"][4]["claim_id"] == "login-valid-user", "the JSON scenario is the one the player gets")
    assert_true(validate_scenario(parsed["scenario"]) == [], "the sample scenario validates")
    assert_true(parsed["read_only_requests"] == [], "no proposal section, no read-only proposals")

    # Read-only POST proposals: parsed from the section, validated as endpoint + handler reference.
    proposal = _reply(_SCENARIO).replace(
        "\ncoverage_gap:\n- none\n",
        "\ncoverage_gap:\n- none\n\nread_only_requests:\n- method: POST | endpoint: http://localhost:8000/api/search | source_refs: src/routes/search.ts:3 | reason: runs a SELECT only\n",
    )
    assert_true(proposal != _reply(_SCENARIO), "fixture assumption: coverage_gap is where the replace expects it")
    proposed = parse_spec(proposal)["read_only_requests"]
    assert_true(
        proposed == [{"method": "POST", "endpoint": "http://localhost:8000/api/search", "source_refs": ["src/routes/search.ts:3"], "reason": "runs a SELECT only"}],
        f"a proposal keeps method, full endpoint, handler reference and reason: {proposed}",
    )
    handler_root = Path(tempfile.mkdtemp(prefix="e2e-readonly-spec-"))
    try:
        handler = handler_root / "src" / "routes" / "search.ts"
        handler.parent.mkdir(parents=True)
        handler.write_text("export async function search(req) {\n  const q = req.body.q;\n  return db.select(q);\n}\n", encoding="utf-8")
        assert_true(validate_read_only_requests(proposed, handler_root) == [], "a grounded proposal validates")
        for change, fragment in (
            ({"method": "PUT"}, "METHOD one of POST"),
            ({"endpoint": "/api/*"}, "no wildcard"),
            ({"source_refs": []}, "must name the handler"),
            ({"source_refs": ["req:SEARCH-1"]}, "is a requirement"),
            ({"source_refs": ["src/routes/gone.ts:3"]}, "names no file"),
            ({"source_refs": ["src/routes/search.ts:40"]}, "outside the file"),
        ):
            errors = validate_read_only_requests([{**proposed[0], **change}], handler_root)
            assert_true(errors and all(e.startswith("read_only_requests[0]") for e in errors) and any(fragment in e for e in errors), f"{change} is refused ({fragment}): {errors}")
        assert_true(validate_read_only_requests("POST /x", handler_root) == ["read_only_requests: not a list"], "a non-list is refused")
    finally:
        shutil.rmtree(handler_root, ignore_errors=True)

    # Prose claims fill in when the JSON forgot them.
    bare = dict(_SCENARIO, claims=[])
    parsed = parse_spec(_reply(bare))
    assert_true(parsed["scenario"]["claims"][0]["id"] == "login-valid-user", "prose claims backfill an empty JSON claims list")

    # Missing section vs unusable section: only the first is a continuation's job.
    gap = spec_gap(_reply(_SCENARIO, with_section=False))
    assert_true(gap is not None and gap["recoverable"] and "[E2E SPEC]" in gap["missing"], f"a missing section is a recoverable gap: {gap}")
    prompt = spec_continuation_prompt(gap)
    assert_true("[E2E SPEC]" in prompt and "[DIGEST]" in prompt, "the follow-up asks for the section and closes with a digest marker")
    broken = _reply(_SCENARIO).replace("```json\n", "```json\n{not json ")
    gap = spec_gap(broken)
    assert_true(gap is not None and not gap["recoverable"], f"a broken scenario is not recoverable by re-asking: {gap}")
    assert_true(spec_gap(_reply(_SCENARIO)) is None, "no gap when the spec is usable")

    # Validation rules, one failing input each.
    def errors(**changes) -> list[str]:
        scenario = json.loads(json.dumps(_SCENARIO))
        scenario.update(changes)
        return validate_scenario(scenario)

    assert_true(any("version" in e for e in errors(version=2)), "version is pinned")
    assert_true(any("at least one claim" in e for e in errors(claims=[])), "claims are required")
    assert_true(any("not allowed" in e for e in errors(steps=[{"action": "evaluate", "script": "1"}])), "actions outside the allowlist are refused")
    assert_true(
        any("needs a claim_id" in e for e in errors(steps=[{"action": "goto", "url": "/"}, {"action": "expect_url", "contains": "/"}])),
        "an assertion without a claim is refused",
    )
    assert_true(
        any("names no claim" in e for e in errors(steps=[{"action": "goto", "url": "/"}, {"action": "expect_url", "contains": "/", "claim_id": "ghost"}])),
        "an assertion pointing at an unknown claim is refused",
    )
    assert_true(
        any("never asserted" in e for e in errors(claims=[*_SCENARIO["claims"], {"id": "logout", "severity": "blocking", "source_refs": ["src/pages/Logout.tsx:3"]}])),
        "a claim nothing asserts cannot be proven and is refused up front",
    )
    assert_true(
        any("selector keys" in e for e in errors(steps=[{"action": "click", "selector": {"xpath": "//a"}}, *_SCENARIO["steps"][4:]])),
        "unknown selector keys are refused",
    )
    assert_true(
        any("selector_provenance" in e for e in errors(steps=[{"action": "click", "selector": {"css": "a"}, "selector_provenance": {"type": "guess"}}, *_SCENARIO["steps"][4:]])),
        "provenance is an enum",
    )
    assert_true(any("kebab" in e for e in errors(claims=[{"id": "Login Valid!", "severity": "blocking"}])), "claim ids are identifiers")

    # --- Policy (plan §9, §10, §18): navigation origin, side effects, selector candidates ---------
    local = {"base_url": "http://localhost:8000"}

    def step_errors(steps: list[dict], policy: dict = local) -> list[str]:
        scenario = json.loads(json.dumps(_SCENARIO))
        scenario["steps"] = [*steps, _SCENARIO["steps"][4]]
        return [e for e in validate_scenario(scenario, policy) if e.startswith("steps[0]")]

    assert_true(step_errors([{"action": "goto", "url": "/login"}]) == [], "a relative path resolves under base_url")
    assert_true(step_errors([{"action": "goto", "url": "http://localhost:8000/login"}]) == [], "an absolute URL on base_url's origin is fine")
    for url, label in (
        ("https://evil.example/login", "another host"),
        ("http://localhost:9000/", "another loopback port is still another origin"),
        ("//evil.example/x", "a protocol-relative URL"),
        ("/\\evil.example", "a backslash a browser reads as a host"),
        ("javascript:alert(1)", "a non-http scheme"),
        ("/login page", "whitespace inside a URL"),
    ):
        assert_true(step_errors([{"action": "goto", "url": url}]) != [], f"{label} is refused: {url!r}")
    no_policy = json.loads(json.dumps(_SCENARIO))
    no_policy["steps"][0]["url"] = "http://localhost:8000/login"
    assert_true(any("cross-origin" in e for e in validate_scenario(no_policy)), "without a policy no absolute URL is trusted")
    listed = {**local, "allowed_origins": ["https://sso.example.test"]}
    assert_true(
        any("allow_remote" in e for e in step_errors([{"action": "goto", "url": "https://sso.example.test/login"}], listed)),
        "an allow-listed remote origin still needs allow_remote",
    )
    assert_true(
        step_errors([{"action": "goto", "url": "https://sso.example.test/login"}], {**listed, "allow_remote": True}) == [],
        "allow_remote + allowed_origins opens exactly that origin",
    )
    assert_true(
        step_errors([{"action": "goto", "url": "http://localhost:9000/"}], {**local, "allowed_origins": ["http://localhost:9000"]}) == [],
        "a second local origin can be allow-listed",
    )

    submit = {"action": "click", "selector": {"role": "button", "name": "Simpan"}}
    declared = dict(submit, side_effect="creates_test_data", test_environment_required=True, cleanup="fixture reset between runs")
    assert_true(any("allow_side_effects" in e for e in step_errors([declared])), "a declared side effect is refused while the config forbids it")
    assert_true(step_errors([declared], {**local, "allow_side_effects": True}) == [], "declared and enabled: runnable")
    half = step_errors([dict(submit, side_effect="deletes_test_data")], {**local, "allow_side_effects": True})
    assert_true(any("test_environment_required" in e for e in half) and any("cleanup" in e for e in half), f"the metadata is required, not decorative: {half}")
    assert_true(any("not in" in e for e in step_errors([dict(submit, side_effect="drops_tables")])), "side_effect is an enum")
    assert_true(step_errors([dict(submit, side_effect="none")]) == [], "side_effect none is the explicit read-only marker")
    assert_true(any("without side_effect" in e for e in step_errors([dict(submit, cleanup="x")])), "cleanup alone is a half-declared side effect")

    strong_first = [
        {"selector": {"role": "button", "name": "Masuk"}, "selector_provenance": {"type": "source"}},
        {"selector": {"testid": "login-submit"}, "selector_provenance": {"type": "existing_test"}},
        {"selector": {"css": "form button"}, "selector_provenance": {"type": "heuristic"}},
    ]
    candidates_step = {"action": "click", "selector_candidates": strong_first}
    assert_true(step_errors([candidates_step]) == [], "ordered candidates validate")
    assert_true(
        [s["provenance"] for s in step_selectors(candidates_step)] == ["source", "existing_test", "heuristic"],
        "the player receives candidates in declared order, provenance attached",
    )
    assert_true(
        step_selectors(_SCENARIO["steps"][1]) == [{"selector": {"role": "textbox", "name": "Email"}, "provenance": "source"}],
        "a single selector is a one-candidate list",
    )
    assert_true(any("strongest first" in e for e in step_errors([{"action": "click", "selector_candidates": strong_first[::-1]}])), "a css candidate may not outrank a role")
    assert_true(any("not both" in e for e in step_errors([dict(candidates_step, selector={"css": "a"})])), "selector and selector_candidates are exclusive")
    assert_true(any("1..5" in e for e in step_errors([{"action": "click", "selector_candidates": []}])), "an empty candidate list is refused")
    assert_true(any("duplicate" in e for e in step_errors([{"action": "click", "selector_candidates": [strong_first[0], strong_first[0]]}])), "a repeated candidate is refused")
    assert_true(any("name only" in e for e in step_errors([{"action": "click", "selector": {"name": "Masuk"}}])), "a bare accessible name identifies nothing")
    assert_true(any("selector_provenance" in e for e in step_errors([{"action": "click", "selector_candidates": [dict(strong_first[0], selector_provenance={"type": "guess"})]}])), "candidate provenance is an enum too")

    # --- Grounding: every claim points at code or a requirement; existing tests at known claims ---
    def claim_errors(claim: dict) -> list[str]:
        scenario = json.loads(json.dumps(_SCENARIO))
        scenario["claims"] = [claim]
        return validate_scenario(scenario, local)

    claim = _SCENARIO["claims"][0]
    assert_true(any("source_refs" in e for e in claim_errors({k: v for k, v in claim.items() if k != "source_refs"})), "a claim without a reference is refused")
    assert_true(any("source_refs" in e for e in claim_errors(dict(claim, source_refs=["see the login page"]))), "prose is not a reference")
    assert_true(claim_errors(dict(claim, source_refs=["req:AUTH-12", "src/routes/auth.ts:40-52"])) == [], "requirement ids and line ranges are references")
    unreferenced = json.loads(json.dumps(_SCENARIO))
    unreferenced["claims"][0].pop("source_refs")
    parsed = parse_spec(_reply(unreferenced))
    assert_true(
        parsed["scenario"]["claims"][0]["source_refs"] == ["src/pages/Login.tsx:12", "src/routes/auth.ts:40"]
        and validate_scenario(parsed["scenario"], local) == [],
        "prose source_refs carry over to the JSON claim with the same id",
    )
    ids = {"login-valid-user"}
    assert_true(validate_existing_tests(parse_spec(_reply(_SCENARIO))["existing_tests"], ids) == [], "the sample existing test validates")
    assert_true(any("unknown claim" in e for e in validate_existing_tests([{"path": "e2e/a.spec.ts", "covers": ["ghost"]}], ids)), "coverage of an unknown claim is refused")
    for bad in ("../outside/a.spec.ts", "/abs/a.spec.ts", "C:/tests/a.spec.ts", ""):
        assert_true(any("project-relative" in e for e in validate_existing_tests([{"path": bad, "covers": []}], ids)), f"existing test path must be project-relative: {bad!r}")

    # --- Grounding against the project tree (G8) ---------------------------------------------------
    project = Path(tempfile.mkdtemp(prefix="e2e-ground-"))
    try:
        (project / "src").mkdir()
        (project / "src" / "Login.tsx").write_text("\n".join(f"line {n}" for n in range(1, 21)) + "\n", encoding="utf-8")

        def grounded(*refs: str) -> list[str]:
            return ground_claims({"claims": [dict(_SCENARIO["claims"][0], source_refs=list(refs))]}, project)

        assert_true(grounded("src/Login.tsx", "src/Login.tsx:12", "src/Login.tsx:5-20", "req:AUTH-1") == [], "files, lines, ranges, and requirements that exist ground")
        assert_true(any("names no file" in e for e in grounded("src/Gone.tsx:3")), "a missing file does not ground")
        assert_true(any("outside the file" in e for e in grounded("src/Login.tsx:21")), "a line past the end does not ground")
        assert_true(any("outside the file" in e for e in grounded("src/Login.tsx:9-4")), "a reversed range does not ground")
        assert_true(any("outside the project" in e for e in grounded("../escape.txt")), "a reference out of the project does not ground")
    finally:
        shutil.rmtree(project, ignore_errors=True)

    # --- A runnable existing test stands in for assertions on the claims it covers ----------------
    unasserted = json.loads(json.dumps(_SCENARIO))
    unasserted["steps"] = _SCENARIO["steps"][:4]
    assert_true(any("never asserted" in e and "existing_test_command" in e for e in validate_scenario(unasserted, local)), "an unasserted claim is refused, and the message names the way out")
    assert_true(validate_scenario(unasserted, local, covered={"login-valid-user"}) == [], "covered by a runnable existing test: no assertion needed")
    no_steps = dict(json.loads(json.dumps(_SCENARIO)), steps=[])
    assert_true(validate_scenario(no_steps, local, covered={"login-valid-user"}) == [], "every claim covered: the scenario may have no steps")
    assert_true(any("at least one step" in e for e in validate_scenario(no_steps, local)), "otherwise a scenario without steps is refused")

    # Placeholders: found, substituted for the player only, missing names reported.
    names = env_references(_SCENARIO)
    assert_true(names == ["E2E_USER", "E2E_PASS"], f"placeholders are listed in order: {names}")
    resolved, missing = substitute_env(_SCENARIO, {"E2E_USER": "u@example.test"})
    assert_true(resolved["steps"][1]["value"] == "u@example.test", "a known placeholder is substituted")
    assert_true(resolved["steps"][2]["value"] == "${E2E_PASS}" and missing == ["E2E_PASS"], "an unknown placeholder stays and is reported")
    assert_true(_SCENARIO["steps"][1]["value"] == "${E2E_USER}", "substitution never mutates the archived form")
