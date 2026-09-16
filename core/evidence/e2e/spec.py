"""The `[E2E SPEC]` contract: parse it out of stage-1 evidence, validate the scenario.

The section rides INSIDE standard exploration output — `[EVIDENCE]` … `[E2E SPEC]` …
`[DIGEST]` — so the executor's generic evidence guard and its one bounded continuation
apply to stage 1 unchanged. What this module adds is the part that guard cannot see: a
reply that is perfectly good evidence and still carries no scenario to run.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from core.evidence.e2e.preflight import safe_base_url, same_origin

SPEC_MARKER = "[E2E SPEC]"
_NEXT_MARKERS = ("[DIGEST]", "[VERIFICATION]", "[EVIDENCE]")

ALLOWED_ACTIONS = frozenset(
    {
        "goto",
        "click",
        "fill",
        "select",
        "press",
        "wait_dom",
        "expect_dom",
        "expect_url",
        "expect_title",
        "probe",
    }
)
ASSERTION_ACTIONS = frozenset({"expect_dom", "expect_url", "expect_title"})
SELECTOR_KEYS = frozenset({"role", "name", "label", "testid", "text", "css"})
PROVENANCE = frozenset({"source", "existing_test", "runtime_probe", "heuristic"})
SEVERITIES = frozenset({"blocking", "non_blocking"})
SIDE_EFFECTS = frozenset({"none", "creates_test_data", "modifies_test_data", "deletes_test_data"})
# Plan §10 preference order. A candidate list must not rank a weaker selector above a
# stronger one: the player tries candidates in order, so the order IS the fallback rule.
SELECTOR_RANK = ("role", "label", "testid", "text", "css")
MAX_SELECTOR_CANDIDATES = 5
# Readiness a step waits for before its action (after navigation, for goto). Declared per
# step from the app's own indicators — a spinner gone, a button enabled, fetched rows shown
# — because `load` and `networkidle` say nothing on a page that polls.
READY_SELECTOR_KEYS = ("hidden", "visible", "enabled")
READY_TEXT_KEYS = ("text", "url")
READY_KEYS = (*READY_SELECTOR_KEYS, *READY_TEXT_KEYS)
MAX_READY_CONDITIONS = 5
WRITE_METHODS = ("POST", "PUT", "PATCH", "DELETE")
_ACTIONS_WITH_SELECTOR = frozenset({"click", "fill", "select", "press", "wait_dom", "expect_dom"})
_ACTIONS_WITH_REQUEST = frozenset({"goto", "click", "fill", "select", "press"})
_SIDE_EFFECT_FIELDS = ("test_environment_required", "test_data", "no_cleanup_reason")
_STEP_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_REQUEST_PATH = re.compile(r"^/[^\s?#*\\]*$")
# `path[:line[-line]]` relative to the project, or `req:<id>` for a written requirement.
_SOURCE_REF = re.compile(r"^(?:req:\S.{0,198}|[^\s|,:\\]{1,200}(?::\d+(?:-\d+)?)?)$")
_REF_PARTS = re.compile(r"^(?P<path>[^:]+?)(?::(?P<start>\d+)(?:-(?P<end>\d+))?)?$")
_UNSAFE_URL_CHARS = re.compile(r"[\\\s\x00-\x1f]")
_CLAIM_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_ENV_REF = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")
# Anything shaped like a placeholder, so `${e2e_user}` or `${E2E USER}` is named as a
# mistake instead of travelling to the player as literal text.
_ANY_ENV_REF = re.compile(r"\$\{([^}]*)\}")
_JSON_FENCE = re.compile(r"```(?:json)?\s*\n(.*?)\n\s*```", re.DOTALL | re.IGNORECASE)
_NONE = re.compile(r"^(?:none|n/?a)\b", re.IGNORECASE)


def spec_block(content: str) -> str | None:
    """The text of the `[E2E SPEC]` section, or None when the marker is absent."""
    body = content or ""
    start = body.find(SPEC_MARKER)
    if start < 0:
        return None
    rest = body[start + len(SPEC_MARKER) :]
    end = len(rest)
    for marker in _NEXT_MARKERS:
        pos = rest.find(marker)
        if 0 <= pos < end:
            end = pos
    return rest[:end]


def _section_lines(block: str, name: str) -> list[str]:
    """Bullet items under `name:` up to the next `word:` heading or a code fence."""
    lines = block.splitlines()
    out: list[str] = []
    collecting = False
    for line in lines:
        stripped = line.strip()
        head = re.match(r"^([a-z_]+)\s*:\s*$", stripped)
        if head:
            if collecting:
                break
            collecting = head.group(1) == name
            continue
        if collecting:
            if stripped.startswith("```"):
                break
            if stripped.startswith("-"):
                out.append(stripped.lstrip("-").strip())
    return out


def _pipe_fields(item: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for part in item.split("|"):
        key, sep, value = part.partition(":")
        if sep:
            fields[key.strip().lower()] = value.strip()
    return fields


def parse_spec(content: str) -> dict:
    """Parse stage-1 output. Never raises: shape problems land in `errors`.

    Returns {"present", "claims", "existing_tests", "coverage_gap", "read_only_requests",
    "scenario", "uncertainties", "errors"}. `present` false means the section marker itself
    is missing — the case that earns a targeted continuation rather than a verdict.
    """
    block = spec_block(content)
    out = {
        "present": block is not None,
        "claims": [],
        "existing_tests": [],
        "coverage_gap": [],
        "read_only_requests": [],
        "scenario": None,
        "uncertainties": [],
        "errors": [],
    }
    if block is None:
        out["errors"].append("[E2E SPEC] section missing")
        return out

    for item in _section_lines(block, "claims"):
        if _NONE.match(item):
            continue
        fields = _pipe_fields(item)
        if "id" not in fields:
            out["errors"].append(f"claim without id: {item[:80]}")
            continue
        out["claims"].append(
            {
                "id": fields["id"],
                "severity": fields.get("severity", "blocking"),
                "description": fields.get("description", ""),
                "source_refs": [
                    ref.strip()
                    for ref in fields.get("source_refs", "").split(",")
                    if ref.strip()
                ],
            }
        )
    for item in _section_lines(block, "existing_tests"):
        if _NONE.match(item):
            continue
        fields = _pipe_fields(item)
        if "path" in fields:
            out["existing_tests"].append(
                {
                    "path": fields["path"],
                    "covers": [c.strip() for c in fields.get("covers", "").split(",") if c.strip()],
                    "confidence": fields.get("confidence", "low"),
                }
            )
    out["coverage_gap"] = [
        item for item in _section_lines(block, "coverage_gap") if not _NONE.match(item)
    ]
    for item in _section_lines(block, "read_only_requests"):
        if _NONE.match(item):
            continue
        fields = _pipe_fields(item)
        out["read_only_requests"].append(
            {
                "method": fields.get("method", ""),
                "endpoint": fields.get("endpoint", ""),
                "source_refs": [ref.strip() for ref in fields.get("source_refs", "").split(",") if ref.strip()],
                "reason": fields.get("reason", ""),
            }
        )
    out["uncertainties"] = [
        item for item in _section_lines(block, "spec_uncertainties") if not _NONE.match(item)
    ]

    fence = _JSON_FENCE.search(block)
    if not fence:
        out["errors"].append("scenario_json: no JSON code fence")
        return out
    try:
        scenario = json.loads(fence.group(1))
    except ValueError as exc:
        out["errors"].append(f"scenario_json: invalid JSON ({exc})")
        return out
    if not isinstance(scenario, dict):
        out["errors"].append("scenario_json: top level is not an object")
        return out
    # The prose claims list and the JSON one describe the same thing; the JSON wins
    # because the player reads it, but a claim named only in prose is still a claim.
    if not scenario.get("claims") and out["claims"]:
        scenario["claims"] = list(out["claims"])
    elif isinstance(scenario.get("claims"), list):
        # Models tend to put source_refs in the prose line only. Same id, same claim:
        # the reference is carried over rather than failing the spec on a formality.
        prose_refs = {claim["id"]: claim["source_refs"] for claim in out["claims"] if claim["source_refs"]}
        for claim in scenario["claims"]:
            if isinstance(claim, dict) and not claim.get("source_refs") and claim.get("id") in prose_refs:
                claim["source_refs"] = list(prose_refs[claim["id"]])
    out["scenario"] = scenario
    return out


def selector_rank(selector: Mapping) -> int | None:
    """Position of the selector's strongest key in SELECTOR_RANK; None when it has none."""
    for rank, key in enumerate(SELECTOR_RANK):
        if key in selector:
            return rank
    return None


def _selector_errors(where: str, selector: object) -> list[str]:
    if not isinstance(selector, dict) or not selector:
        return [f"{where}: needs a selector object"]
    unknown = set(selector) - SELECTOR_KEYS
    if unknown:
        return [f"{where}: selector keys {sorted(unknown)} not allowed"]
    if selector_rank(selector) is None:
        return [f"{where}: selector needs one of {'|'.join(SELECTOR_RANK)} (name only qualifies role)"]
    return []


def _provenance_errors(where: str, provenance: object) -> list[str]:
    if provenance is None:
        return []
    kind = provenance.get("type") if isinstance(provenance, dict) else None
    if kind not in PROVENANCE:
        return [f"{where}: selector_provenance '{kind}' not in {sorted(PROVENANCE)}"]
    ref = provenance.get("ref")
    if ref is not None and (not isinstance(ref, str) or not _SOURCE_REF.match(ref.strip())):
        return [f"{where}: selector_provenance.ref must be `path[:line]`, the source or test the selector comes from"]
    return []


def _ready_errors(where: str, ready: object) -> list[str]:
    if ready is None:
        return []
    if not isinstance(ready, list) or not 1 <= len(ready) <= MAX_READY_CONDITIONS:
        return [f"{where}: ready must list 1..{MAX_READY_CONDITIONS} conditions"]
    errors: list[str] = []
    for index, condition in enumerate(ready):
        at = f"{where}.ready[{index}]"
        if not isinstance(condition, dict) or len(condition) != 1 or next(iter(condition)) not in READY_KEYS:
            errors.append(f"{at}: an object with exactly one of {'|'.join(READY_KEYS)}")
            continue
        key, value = next(iter(condition.items()))
        if key in READY_SELECTOR_KEYS:
            errors.extend(_selector_errors(f"{at}.{key}", value))
        elif not isinstance(value, str) or not value.strip():
            errors.append(f"{at}.{key}: a non-empty string")
    return errors


def _request_errors(where: str, spec: object) -> list[str]:
    """`request`: the write a step is expected to send, matched against what it actually sent."""
    if spec is None:
        return []
    if not isinstance(spec, dict) or set(spec) - {"method", "path"}:
        return [f"{where}: request is an object with method and path only"]
    errors: list[str] = []
    if spec.get("method") not in WRITE_METHODS:
        errors.append(f"{where}: request.method one of {'|'.join(WRITE_METHODS)}")
    path = spec.get("path")
    if not isinstance(path, str) or not _REQUEST_PATH.match(path):
        errors.append(f"{where}: request.path starts with '/', no query, wildcard or whitespace (':name' matches one segment)")
    return errors


def request_path_matches(template: str, path: str) -> bool:
    """`/items/:id` matches `/items/7`; every other segment matches exactly."""
    want = (template or "/").rstrip("/").split("/")
    got = (path or "/").rstrip("/").split("/")
    return len(want) == len(got) and all((w.startswith(":") and bool(g)) or w == g for w, g in zip(want, got))


def _id_errors(where: str, step: Mapping, seen: set[str]) -> list[str]:
    sid = step.get("id")
    if not isinstance(sid, str) or not _STEP_ID.match(sid):
        return [f"{where}: id '{sid}' must be a kebab identifier, stable across edits of the draft"]
    if sid in seen:
        return [f"{where}: duplicate step id '{sid}'"]
    seen.add(sid)
    return []


def step_selectors(step: Mapping) -> list[dict]:
    """The selectors a step may use, in the order the player must try them.

    One shape for both spellings, so the player never re-implements the choice:
    `[{"selector": {...}, "provenance": "source" | ... | None, "ref": "path:line" | None}, ...]`.

    `ref` travels with the candidate rather than being looked up later: which candidate won
    is only known at runtime, and the tagging pass needs the address of THAT one.
    """
    candidates = step.get("selector_candidates")
    if isinstance(candidates, list):
        pairs = [
            (item.get("selector"), item.get("selector_provenance"))
            for item in candidates
            if isinstance(item, dict)
        ]
    else:
        pairs = [(step.get("selector"), step.get("selector_provenance"))]
    return [
        {
            "selector": selector,
            "provenance": (provenance or {}).get("type") if isinstance(provenance, dict) else None,
            "ref": (provenance or {}).get("ref") if isinstance(provenance, dict) else None,
        }
        for selector, provenance in pairs
        if isinstance(selector, dict) and selector
    ]


def navigation_error(url: str, policy: Mapping | None) -> str | None:
    """Why a `goto` target is refused, or None when it may be opened.

    Relative paths resolve against `base_url`, which preflight has already vetted. An
    absolute URL must share that origin, or be listed in `allowed_origins` AND still pass
    the base-URL policy (so a remote origin also needs `allow_remote`). Backslashes and
    whitespace are refused outright: a browser reads `/\\host` as another host even though
    `urlsplit` sees a path.
    """
    policy = policy or {}
    if _UNSAFE_URL_CHARS.search(url):
        return f"url '{url[:80]}' contains a backslash, whitespace, or control character"
    try:
        parts = urlsplit(url)
    except ValueError:
        return f"url '{url[:80]}' does not parse"
    if not parts.scheme and not url.startswith("//"):
        return None
    base = str(policy.get("base_url") or "")
    absolute = url if parts.scheme else f"{urlsplit(base).scheme or 'http'}:{url}"
    target = urlsplit(absolute)
    if target.scheme.lower() not in ("http", "https") or not target.hostname:
        return f"url '{url[:80]}': only http(s) navigation is allowed"
    if base and same_origin(absolute, base):
        return None
    origin = f"{target.scheme}://{target.netloc}".lower()
    allowed = {str(o).lower().rstrip("/") for o in policy.get("allowed_origins") or []}
    if origin not in allowed:
        return f"cross-origin navigation to '{origin}' refused: not base_url's origin and not in settings.allowed_origins"
    ok, detail = safe_base_url(absolute, policy)
    return None if ok else f"cross-origin navigation to '{origin}' refused: {detail}"


def _side_effect_errors(where: str, step: Mapping, policy: Mapping) -> list[str]:
    effect = step.get("side_effect")
    if "cleanup" in step:
        return [
            f"{where}: cleanup is no longer a description on the step; declare executable steps in "
            "scenario.cleanup, each with cleans: '<this step id>'"
        ]
    if effect is None:
        stray = [field for field in _SIDE_EFFECT_FIELDS if field in step]
        return [f"{where}: {', '.join(stray)} declared without side_effect"] if stray else []
    if effect not in SIDE_EFFECTS:
        return [f"{where}: side_effect '{effect}' not in {sorted(SIDE_EFFECTS)}"]
    if effect == "none":
        return []
    errors = []
    if step.get("test_environment_required") is not True:
        errors.append(f"{where}: side_effect '{effect}' needs test_environment_required: true")
    test_data = step.get("test_data")
    if not isinstance(test_data, dict) or not isinstance(test_data.get("marker"), str) or not test_data["marker"].strip():
        errors.append(f"{where}: side_effect '{effect}' needs test_data.marker, the value that tells this run's data apart from existing data")
    reason = step.get("no_cleanup_reason")
    if reason is not None and (effect == "creates_test_data" or not isinstance(reason, str) or not reason.strip()):
        errors.append(f"{where}: no_cleanup_reason is a non-empty string, for modifies/deletes only (created data is always cleaned up)")
    if not policy.get("allow_side_effects"):
        errors.append(f"{where}: side_effect '{effect}' refused: settings.allow_side_effects is false")
    return errors


def _step_errors(where: str, step: Mapping, policy: Mapping, claim_ids: set[str], *, cleanup: bool) -> tuple[list[str], str | None]:
    """One step's rules. Returns (errors, the claim id it asserts or None).

    A cleanup step shares every rule a test step has, except that it proves the cleanup, not
    a claim, and its write is covered by the side effect of the step it cleans.
    """
    errors: list[str] = []
    asserted = None
    action = step.get("action")
    if action not in ALLOWED_ACTIONS:
        return [f"{where}: action '{action}' not allowed"], None
    if action in ASSERTION_ACTIONS:
        cid = step.get("claim_id")
        if cleanup:
            if cid is not None:
                errors.append(f"{where}: a cleanup assertion proves the cleanup, not a claim; drop claim_id")
        elif not cid:
            errors.append(f"{where}: {action} needs a claim_id")
        elif cid not in claim_ids:
            errors.append(f"{where}: claim_id '{cid}' names no claim")
        else:
            asserted = cid
    if action == "goto":
        url = step.get("url")
        if not isinstance(url, str) or not url:
            errors.append(f"{where}: goto needs a url")
        else:
            refused = navigation_error(url, policy)
            if refused:
                errors.append(f"{where}: {refused}")
    if cleanup:
        declared = [field for field in ("side_effect", "cleanup", *_SIDE_EFFECT_FIELDS) if field in step]
        if declared:
            errors.append(f"{where}: a cleanup step declares no {', '.join(declared)} of its own")
    else:
        errors.extend(_side_effect_errors(where, step, policy))
    if action in _ACTIONS_WITH_SELECTOR:
        candidates = step.get("selector_candidates")
        if candidates is not None and "selector" in step:
            errors.append(f"{where}: use selector or selector_candidates, not both")
        elif candidates is not None:
            if not isinstance(candidates, list) or not 1 <= len(candidates) <= MAX_SELECTOR_CANDIDATES:
                errors.append(f"{where}: selector_candidates must list 1..{MAX_SELECTOR_CANDIDATES} entries")
            else:
                ranks: list[int] = []
                seen: list[dict] = []
                for position, item in enumerate(candidates):
                    at = f"{where}.selector_candidates[{position}]"
                    if not isinstance(item, dict):
                        errors.append(f"{at}: not an object")
                        continue
                    found = _selector_errors(at, item.get("selector"))
                    found += _provenance_errors(at, item.get("selector_provenance"))
                    errors.extend(found)
                    if found:
                        continue
                    if item["selector"] in seen:
                        errors.append(f"{at}: duplicate selector")
                    seen.append(item["selector"])
                    ranks.append(selector_rank(item["selector"]))
                if ranks != sorted(ranks):
                    errors.append(
                        f"{where}: selector_candidates must run strongest first ({' > '.join(SELECTOR_RANK)})"
                    )
        else:
            errors.extend(_selector_errors(where, step.get("selector")))
            errors.extend(_provenance_errors(where, step.get("selector_provenance")))
    if "within" in step:
        if action not in _ACTIONS_WITH_SELECTOR:
            errors.append(f"{where}: within scopes a selector, and {action} has none")
        else:
            errors.extend(_selector_errors(f"{where}.within", step["within"]))
    errors.extend(_ready_errors(where, step.get("ready")))
    if "request" in step:
        if action not in _ACTIONS_WITH_REQUEST:
            errors.append(f"{where}: request names the write an action sends; {action} sends none")
        else:
            errors.extend(_request_errors(where, step.get("request")))
    if action == "fill" and not isinstance(step.get("value"), str):
        errors.append(f"{where}: fill needs a string value")
    if action == "expect_url" and not any(k in step for k in ("contains", "equals", "matches")):
        errors.append(f"{where}: expect_url needs contains|equals|matches")
    return errors, asserted


def _cleanup_errors(scenario: Mapping, step_ids: dict[str, tuple[int, Mapping]], policy: Mapping, claim_ids: set[str], seen_ids: set[str]) -> list[str]:
    """scenario.cleanup: executable steps, grouped by the side-effect step each one cleans."""
    errors: list[str] = []
    cleanup = scenario.get("cleanup")
    cleaned: dict[str, list[Mapping]] = {}
    if cleanup is not None and not isinstance(cleanup, list):
        errors.append("cleanup: a list of steps")
        cleanup = []
    for index, step in enumerate(cleanup or []):
        where = f"cleanup[{index}]"
        if not isinstance(step, dict):
            errors.append(f"{where}: not an object")
            continue
        errors.extend(_id_errors(where, step, seen_ids))
        found, _ = _step_errors(where, step, policy, claim_ids, cleanup=True)
        errors.extend(found)
        target = step.get("cleans")
        if target not in step_ids:
            errors.append(f"{where}: cleans '{target}' names no step")
        elif step_ids[target][1].get("side_effect") in (None, "none"):
            errors.append(f"{where}: cleans '{target}', a step that declares no side_effect")
        else:
            cleaned.setdefault(str(target), []).append(step)
    for target, group in cleaned.items():
        if not any(s.get("action") in ASSERTION_ACTIONS for s in group):
            errors.append(
                f"cleanup: the steps cleaning '{target}' need an assertion (expect_dom|expect_url|expect_title) "
                "showing the data is gone or restored"
            )
    for sid, (index, step) in step_ids.items():
        effect = step.get("side_effect")
        if effect not in SIDE_EFFECTS or effect == "none":
            continue
        if sid in cleaned:
            if step.get("no_cleanup_reason") is not None:
                errors.append(f"steps[{index}]: has cleanup steps and a no_cleanup_reason; keep one")
            continue
        if effect == "creates_test_data":
            errors.append(f"steps[{index}]: side_effect '{effect}' needs a cleanup step (scenario.cleanup with cleans: '{sid}')")
        elif step.get("no_cleanup_reason") is None:
            errors.append(f"steps[{index}]: side_effect '{effect}' needs a cleanup step (scenario.cleanup with cleans: '{sid}') or no_cleanup_reason")
    return errors


def placeholder_errors(scenario: object) -> list[str]:
    """Every placeholder must be a registered credential name, spelled exactly."""
    from core.evidence.e2e.request import CREDENTIAL_KEYS

    errors: list[str] = []
    seen: set[str] = set()

    def visit(item):
        if isinstance(item, str):
            for name in _ANY_ENV_REF.findall(item):
                if name in CREDENTIAL_KEYS or name in seen:
                    continue
                seen.add(name)
                shown = name[:40]
                if name.upper() in CREDENTIAL_KEYS:
                    errors.append(f"placeholder '${{{shown}}}' must be written '${{{name.upper()}}}': names are case-sensitive")
                else:
                    errors.append(f"placeholder '${{{shown}}}' is not a registered credential ({', '.join(CREDENTIAL_KEYS)})")
        elif isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(scenario)
    return errors


def validate_scenario(scenario: object, policy: Mapping | None = None, *, covered: set[str] | frozenset = frozenset()) -> list[str]:
    """Structural and safety rules the player relies on. Empty list means runnable.

    `policy` is the request's settings (base_url, allowed_origins, allow_remote,
    allow_side_effects). Without it the strictest reading applies: no absolute
    navigation, no side effects. `covered` names the claims an existing test the project
    will actually run stands in for: those need no assertion, and when every claim is
    covered the scenario may have no steps at all.
    """
    policy = policy or {}
    errors: list[str] = []
    if not isinstance(scenario, dict):
        return ["scenario is not an object"]
    if scenario.get("version") != 1:
        errors.append("version must be 1")

    claims = scenario.get("claims")
    claim_ids: set[str] = set()
    if not isinstance(claims, list) or not claims:
        errors.append("claims: at least one claim is required")
    else:
        for index, claim in enumerate(claims):
            if not isinstance(claim, dict):
                errors.append(f"claims[{index}]: not an object")
                continue
            cid = str(claim.get("id") or "")
            if not _CLAIM_ID.match(cid):
                errors.append(f"claims[{index}]: id '{cid}' is not a kebab identifier")
            elif cid in claim_ids:
                errors.append(f"claims[{index}]: duplicate id '{cid}'")
            claim_ids.add(cid)
            severity = claim.get("severity", "blocking")
            if severity not in SEVERITIES:
                errors.append(f"claims[{index}]: severity '{severity}' not in {sorted(SEVERITIES)}")
            refs = claim.get("source_refs")
            if (
                not isinstance(refs, list)
                or not refs
                or not all(isinstance(ref, str) and _SOURCE_REF.match(ref.strip()) for ref in refs)
            ):
                errors.append(
                    f"claims[{index}]: source_refs must list at least one `path[:line]` or `req:<id>` reference"
                )

    steps = scenario.get("steps")
    asserted: set[str] = set()
    if not isinstance(steps, list):
        errors.append("steps: at least one step is required")
        steps = []
    elif not steps and not (claim_ids and claim_ids <= set(covered)):
        errors.append("steps: at least one step is required (only claims covered by a runnable existing test may go without)")
    seen_ids: set[str] = set()
    step_ids: dict[str, tuple[int, Mapping]] = {}
    for index, step in enumerate(steps):
        where = f"steps[{index}]"
        if not isinstance(step, dict):
            errors.append(f"{where}: not an object")
            continue
        id_problems = _id_errors(where, step, seen_ids)
        errors.extend(id_problems)
        if not id_problems:
            step_ids[step["id"]] = (index, step)
        found, cid = _step_errors(where, step, policy, claim_ids, cleanup=False)
        errors.extend(found)
        if cid:
            asserted.add(cid)
    errors.extend(_cleanup_errors(scenario, step_ids, policy, claim_ids, seen_ids))
    errors.extend(placeholder_errors(scenario))
    unasserted = claim_ids - asserted - set(covered)
    if unasserted and not errors:
        # A claim no assertion points at can never be proven, so a run that ends clean
        # would report pass on a claim nobody tested. Refused up front, not at verdict.
        errors.append(
            f"claims never asserted: {', '.join(sorted(unasserted))} (assert them in steps, or cover them with an "
            "existing test the project runs through settings.existing_test_command)"
        )
    return errors


def ground_claims(scenario: object, project_root: Path) -> list[str]:
    """Every file reference in a claim must name a file — and line — that exists (G8).

    `req:<id>` references are requirements, not files, and pass. Validation already fixed
    the reference format; this is the half that needs the project on disk: a claim the
    spec cannot tie to real code is a claim the spec invented.
    """
    errors: list[str] = []
    root = Path(project_root).resolve()
    claims = scenario.get("claims") if isinstance(scenario, dict) else None
    for index, claim in enumerate(claims if isinstance(claims, list) else []):
        if not isinstance(claim, dict):
            continue
        for ref in claim.get("source_refs") or []:
            ref = str(ref).strip()
            if ref.startswith("req:"):
                continue
            problem = _file_ref_problem(ref, root)
            if problem:
                errors.append(f"claims[{index}]: source_ref '{ref[:80]}' {problem}")
    return errors


def _file_ref_problem(ref: str, root: Path) -> str | None:
    """Why `path[:line[-line]]` names no real place in the project, or None."""
    match = _REF_PARTS.match(ref)
    if not match:
        return "is not `path[:line]`"
    target = (root / match.group("path").replace("\\", "/")).resolve()
    if not target.is_relative_to(root):
        return "points outside the project"
    if not target.is_file():
        return "names no file in the project"
    if match.group("start"):
        start = int(match.group("start"))
        end = int(match.group("end") or start)
        try:
            count = len(target.read_bytes().splitlines())
        except OSError:
            count = 0
        if start < 1 or end < start or end > count:
            return f"is outside the file ({count} lines)"
    return None


def validate_read_only_requests(entries: object, project_root: Path) -> list[str]:
    """A proposed read-only POST must name an exact endpoint AND the handler that proves it.

    The proposal is second_agent's word about someone else's code, and admitting it lets
    a request past the write guard. So the reference has to be a real file and line — a
    `req:` requirement is not accepted here, because a requirement says what a request
    should do, not what its handler does.
    """
    from core.evidence.e2e.request import read_only_request_errors

    if not isinstance(entries, list):
        return ["read_only_requests: not a list"]
    root = Path(project_root).resolve()
    errors: list[str] = []
    for index, item in enumerate(entries):
        where = f"read_only_requests[{index}]"
        if not isinstance(item, dict):
            errors.append(f"{where}: not an object")
            continue
        entry = f"{str(item.get('method') or '').strip()} {str(item.get('endpoint') or '').strip()}"
        errors += [error.replace("settings.allowed_read_only_requests[0]", where) for error in read_only_request_errors([entry])]
        refs = [str(ref).strip() for ref in item.get("source_refs") or [] if str(ref).strip()]
        if not refs:
            errors.append(f"{where}: source_refs must name the handler (`path:line`) that shows the request writes nothing")
        for ref in refs:
            problem = "is a requirement, not the handler's file" if ref.startswith("req:") else _file_ref_problem(ref, root)
            if problem:
                errors.append(f"{where}: source_ref '{ref[:80]}' {problem}")
    return errors


def validate_existing_tests(existing_tests: object, claim_ids: set[str]) -> list[str]:
    """`existing_tests` from the spec: project-relative paths covering known claims."""
    errors: list[str] = []
    if not isinstance(existing_tests, list):
        return ["existing_tests: not a list"]
    for index, test in enumerate(existing_tests):
        where = f"existing_tests[{index}]"
        if not isinstance(test, dict):
            errors.append(f"{where}: not an object")
            continue
        path = str(test.get("path") or "")
        normalized = path.replace("\\", "/")
        if not path or normalized.startswith("/") or re.match(r"^[A-Za-z]:", path) or ".." in normalized.split("/"):
            errors.append(f"{where}: path '{path}' must be project-relative")
        unknown = [cid for cid in test.get("covers") or [] if cid not in claim_ids]
        if unknown:
            errors.append(f"{where}: covers unknown claim(s) {', '.join(unknown)}")
    return errors


def env_references(scenario: object) -> list[str]:
    """Every `${NAME}` placeholder in the scenario, in first-seen order."""
    found: list[str] = []

    def visit(item):
        if isinstance(item, str):
            for name in _ENV_REF.findall(item):
                if name not in found:
                    found.append(name)
        elif isinstance(item, dict):
            for child in item.values():
                visit(child)
        elif isinstance(item, list):
            for child in item:
                visit(child)

    visit(scenario)
    return found


def substitute_env(scenario: object, env: Mapping[str, str]) -> tuple[object, list[str]]:
    """Resolve placeholders from `env`. Returns (resolved, missing names).

    The resolved copy is what the PLAYER gets; it must never be written to a log,
    a prompt, or an artifact — those keep the placeholder form.
    """
    missing: list[str] = []

    def visit(item):
        if isinstance(item, str):
            def sub(match):
                name = match.group(1)
                if name in env:
                    return env[name]
                if name not in missing:
                    missing.append(name)
                return match.group(0)
            return _ENV_REF.sub(sub, item)
        if isinstance(item, dict):
            return {key: visit(value) for key, value in item.items()}
        if isinstance(item, list):
            return [visit(value) for value in item]
        return item

    return visit(scenario), missing


def spec_gap(content: str) -> dict | None:
    """Why the stage-1 reply cannot proceed, or None when a scenario is present.

    Distinguishes the shape a continuation can fix (evidence arrived, section did not)
    from one it cannot (the section is there but its scenario is broken).
    """
    parsed = parse_spec(content)
    if parsed["scenario"] is not None and not parsed["errors"]:
        return None
    if not parsed["present"]:
        return {"reason": "e2e spec section missing", "missing": [SPEC_MARKER], "recoverable": True}
    return {
        "reason": "e2e spec present but unusable",
        "missing": list(parsed["errors"]),
        "recoverable": False,
    }


def spec_continuation_prompt(gap: dict) -> str:
    """The follow-up sent in the same provider thread when only the section is missing.

    Closes with a short `[DIGEST]` on purpose: the reply travels through the same
    evidence guard as any exploration output, and that guard wants one of the evidence
    markers present. A digest is the cheapest one that is also useful.
    """
    return "\n".join(
        [
            "[CONTINUATION]",
            "Your previous reply carried the evidence but no [E2E SPEC] section.",
            "Return ONLY the missing section, in this exact shape, grounded in the evidence you already gathered:",
            "",
            "[E2E SPEC]",
            "claims:",
            "- id: <kebab-id> | severity: <blocking|non_blocking> | description: <behaviour to prove> | source_refs: <file:line, ...>",
            "",
            "existing_tests:",
            "- path: <test file> | covers: <claim ids> | confidence: <low|medium|high> | none",
            "",
            "coverage_gap:",
            "- <claim ids not covered by existing tests> | none",
            "",
            "read_only_requests:",
            "- method: POST | endpoint: </path or http(s)://host/path> | source_refs: <handler file:line> | reason: <why it writes nothing> | none",
            "",
            "scenario_json:",
            "```json",
            '{"version": 1, "feature": "<name>", "claims": [...], "steps": [{"id": "<kebab-id>", "action": "...", ...}], "cleanup": [...]}',
            "```",
            "",
            "spec_uncertainties:",
            "- <selector or flow you could not ground> | none",
            "",
            "[DIGEST]",
            "summary: <one line: how many claims, what existing tests cover>",
            "confidence: low | medium | high",
        ]
    )
