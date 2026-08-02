"""Output contract. Every result is `ok:true` or a structured error.

Shape stays `{ok, content, meta}` (backward compatible). Errors carry
`error_type` + mandatory `next_action` inside `meta`. Optional `digest`
rides at the top level for main_agent to relay.
"""
import re

ERROR_TYPES = {
    "permission_denied",
    "path_out_of_scope",
    "empty_output",
    "job_already_running",
    "session_capture_failed",
    "invalid_evidence",
    "timeout",
    "command_not_found",
    "routing_error",
    "worker_died",
    "worker_stalled",  # PID alive, no progress — probe before judging
    "rate_limited",  # provider refused on quota: waiting fixes it, retrying does not
    "prompt_too_long",  # the shell rejected the command line before opencode ran
    # The provider stream died mid-answer. The opposite advice to rate_limited: this one
    # IS worth retrying, and waiting does nothing for it. Left as `unknown` it collected
    # the useless "inspect the logs and rerun" next_action.
    "streaming_failed",
    "second_agent_unavailable",  # probe in a FRESH session could not get an answer either
    "job_expired",  # ran past the hard runtime ceiling (OOM backstop)
    "task_truncated",  # the instruction lost too much to trust the answer to it
    "fact_ingest_failed",
    "workflow_init_error",
    "job_submit_error",
    "runtime_lock",
    "unknown",
}

# Required fields per command, checked by validate_fields.
REQUIRED_FIELDS = {
    "explore": ("entry_points", "uncertainties"),
    "analyze": ("grounded", "uncertainties"),
    "plan": ("grounded", "uncertainties"),
}


def make_ok(content: str, meta: dict | None = None, digest: dict | None = None) -> dict:
    payload = {"ok": True, "content": content or "", "meta": meta or {}}
    if digest is not None:
        payload["digest"] = digest
    return payload


def make_error(error_type: str, message: str, next_action: str, meta: dict | None = None, **fields) -> dict:
    if error_type not in ERROR_TYPES:
        raise ValueError(f"unknown error_type: {error_type}")
    if not next_action or not str(next_action).strip():
        raise ValueError(f"next_action is required for error_type {error_type}")
    merged = dict(meta or {})
    merged["error_type"] = error_type
    merged["next_action"] = next_action
    merged.update(fields)
    return {"ok": False, "content": message or error_type, "meta": merged}


_SUBAGENT_LINE = re.compile(r"^\s*subagents\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)
_CLUSTER_TAG = re.compile(r"\[c(\d+)\]")
# The honest-fallback line the fan-out instruction asks for when opencode has no spawn tool:
# `subagents: none (no spawn tool; tools: ...)`.
_NO_SPAWN_RE = re.compile(
    r"^\s*subagents\s*:\s*none\b.*no\s+spawn\s+tool", re.IGNORECASE | re.MULTILINE
)
# A refusal is not an absence. `task` can be present and still be denied — opencode
# blocks a read-only primary agent from spawning a write-capable subagent. Left
# undistinguished, that permission wall reads as "opencode cannot fan out" and latches
# the capability off forever, so the one fix that would work is never attempted.
_DENIED_RE = re.compile(
    r"^\s*subagents\s*:\s*none\b.*\b(denied|refus|not\s+permitted|permission)",
    re.IGNORECASE | re.MULTILINE,
)
_DECLINED_RE = re.compile(
    r"^\s*subagents\s*:\s*none\b", re.IGNORECASE | re.MULTILINE
)

# What actually happened to the fan-out instruction, for meta.fanout_mode.
FANOUT_PARALLEL = "parallel"
FANOUT_DENIED = "denied_by_permission"
FANOUT_INCAPABLE = "incapable"
FANOUT_DECLINED = "declined"
FANOUT_UNREPORTED = "unreported"
FANOUT_MISMATCH = "claimed_unconfirmed"


def reported_no_spawn_tool(content: str) -> bool:
    """True when the second agent explicitly reported it has no sub-agent/spawn tool.

    A capability signal, not a contract miss: observed once, it means opencode here cannot
    fan out, so the runtime can stop paying prompt space for a plan it will never run.
    """
    return bool(_NO_SPAWN_RE.search(content or ""))


def detect_subagent_usage(content: str) -> dict:
    """Did the second agent actually fan out, or just say it did?

    Two independent signals: the declared `subagents:` line and the [cN] tags that
    should appear on merged claims. Agreement is what makes the answer trustworthy —
    a declaration with no tagged claims is a claim of work, not evidence of it.

    `fanout_clusters` and `covered_clusters` are deliberately separate. Claims can be
    cluster-tagged whether or not any sub-agent ran, so collapsing the two produced a
    populated "clusters" field on runs that never fanned out — accurate content under a
    name that read like proof of fan-out.

    Returns {'used', 'fanout_clusters', 'covered_clusters', 'mismatch'}.
    """
    declared: list[str] = []
    match = _SUBAGENT_LINE.search(content or "")
    raw = (match.group(1).strip() if match else "")
    if raw and not raw.lower().startswith("none"):
        declared = sorted({f"c{n}" for n in re.findall(r"c(\d+)", raw)})

    tagged = sorted({f"c{n}" for n in _CLUSTER_TAG.findall(content or "")})
    used = bool(declared) and bool(tagged)

    # Why fan-out did not happen decides what to do about it, and the three reasons need
    # opposite responses: a permission wall is fixable config, no tool is permanent, a
    # decline is the agent's judgement call. Collapsing them into "not used" threw that
    # away — and an omitted line was indistinguishable from a decline.
    if used:
        mode = FANOUT_PARALLEL
    elif declared:
        mode = FANOUT_MISMATCH
    elif _NO_SPAWN_RE.search(content or ""):
        mode = FANOUT_INCAPABLE
    elif _DENIED_RE.search(content or ""):
        mode = FANOUT_DENIED
    elif _DECLINED_RE.search(content or ""):
        mode = FANOUT_DECLINED
    else:
        mode = FANOUT_UNREPORTED

    return {
        "used": used,
        "mode": mode,
        # Clusters a sub-agent was actually dispatched to — empty unless BOTH signals agree.
        "fanout_clusters": declared if used else [],
        # Clusters the answer draws on, fan-out or not.
        "covered_clusters": tagged,
        # Declared fan-out with nothing tagged: report it rather than counting it as
        # success. Silent acceptance is how an unperformed step starts looking done.
        "mismatch": bool(declared) and not tagged,
    }


def normalize_output(*, ok: bool, content: str, meta: dict | None = None) -> dict:
    """Legacy shim — kept for callers still passing raw ok/content/meta."""
    return {"ok": ok, "content": content, "meta": meta or {}}


def validate_fields(command: str, content: str) -> list[str]:
    """Return names of required fields missing from an evidence payload."""
    required = REQUIRED_FIELDS.get(command, ())
    lowered = (content or "").lower()
    return [field for field in required if field not in lowered]


_FILE_LINE = re.compile(r"[\w./\\-]+\.\w+:\d+")
_SECTION_HEAD = re.compile(r"^\s*([a-z_]+)\s*:\s*$", re.MULTILINE)


def _section(content: str, name: str) -> list[str]:
    """Bullet lines under a `name:` heading, up to the next heading."""
    lines = (content or "").splitlines()
    out: list[str] = []
    collecting = False
    for line in lines:
        head = _SECTION_HEAD.match(line)
        if head:
            if collecting:
                break
            collecting = head.group(1).lower() == name
            continue
        if collecting and line.strip().startswith("-"):
            out.append(line.strip().lstrip("-").strip())
    return out


_BRACKET_REF = re.compile(r"\[([^\[\]]+)\]")


def split_claim(claim: str) -> dict:
    """Separate what a claim SAYS from the identifiers that back it.

    The two are written on one line because that is how the agent must emit them — an
    anchor detached from its claim cannot be checked. But a reader drowning in
    `[core/router.py:16]` mid-sentence is reading machine bookkeeping, so hand callers a
    prose `text` and a separate `refs` list rather than making each of them re-derive it.

    Returns {'text', 'refs'}. A claim with no brackets comes back unchanged with refs=[].
    """
    body = claim or ""
    refs = [r.strip() for r in _BRACKET_REF.findall(body) if r.strip()]
    text = _BRACKET_REF.sub("", body)
    # Collapse the whitespace and dangling punctuation the removal leaves behind.
    text = re.sub(r"\s{2,}", " ", text).strip().rstrip(" ,;")
    return {"text": text, "refs": refs}


def readable_claims(content: str, section: str = "grounded") -> list[dict]:
    """Every claim in `section` as {'text', 'refs'} — prose first, anchors beside it.

    Detail stays available for audit; it just stops being the thing the eye lands on.
    """
    return [split_claim(claim) for claim in _section(content, section)]


def contract_warnings(command: str, content: str) -> list[dict]:
    """Where the second agent's output does not match the contract it was given.

    Warnings only. Nothing here fails a call: the runtime can see the shape of this
    output, but it cannot see whether the CLAIMS are right, and rejecting a usable
    result over a formatting miss trades a real answer for a clean one.

    Scope is honest about what is checkable here. The contracts main_agent owns —
    [OPTIONS], per-claim attribution, the confidence triple, intent detection — are
    written in ITS output, which this process never sees. They stay prompt-only, and
    listing them here would only make enforcement look broader than it is.
    """
    warnings: list[dict] = []
    body = content or ""

    missing = validate_fields(command, body)
    if missing:
        warnings.append(
            {
                "kind": "missing_fields",
                "detail": f"required section(s) absent: {', '.join(missing)}",
            }
        )

    grounded = _section(body, "grounded")
    unbacked = [
        claim
        for claim in grounded
        if claim.lower() not in {"none", "(none)"} and not _FILE_LINE.search(claim)
    ]
    if unbacked:
        # `grounded` is the one section the prompt defines by its evidence, not by its
        # topic: a claim there without a file:line is an assumption wearing the label
        # that makes main_agent trust it.
        warnings.append(
            {
                "kind": "grounded_without_evidence",
                "detail": f"{len(unbacked)} grounded claim(s) carry no file:line",
                "samples": unbacked[:3],
            }
        )

    return warnings


_CONFIDENCE_RANK = {"low": 0, "medium": 1, "high": 2}
_RANK_CONFIDENCE = {0: "low", 1: "medium", 2: "high"}


def cap_confidence(digest: dict | None, reasons: list[tuple[str, str]]) -> dict | None:
    """Lower a reported confidence to what the run's own conditions support.

    The second agent grades its own confidence, and it grades on the analysis it did —
    not on whether the inputs to that analysis were sound. A stale dependency graph, a
    truncated instruction, or a `grounded` section with no file:line all leave the prose
    just as assured as a clean run. Every one of those signals is already computed
    elsewhere in this pipeline; the only missing step was letting them reach the number
    main_agent actually reads.

    `reasons` is a list of (cap_level, why). The lowest cap wins. The original value is
    kept as `confidence_reported` so the downgrade is auditable, never silent.
    """
    if not digest:
        return digest
    reported = (digest.get("confidence") or "unknown").lower()
    if reported not in _CONFIDENCE_RANK or not reasons:
        return digest

    ceiling = min(_CONFIDENCE_RANK[level] for level, _ in reasons if level in _CONFIDENCE_RANK)
    if _CONFIDENCE_RANK[reported] <= ceiling:
        return digest

    digest["confidence_reported"] = reported
    digest["confidence"] = _RANK_CONFIDENCE[ceiling]
    digest["confidence_capped_by"] = [why for _, why in reasons]
    return digest


def extract_digest(content: str) -> dict | None:
    """Parse a [DIGEST] block from second_agent output. None if absent/unusable.

    Graceful: main_agent falls back to the full content (contract_detail) when
    this returns None.
    """
    import re

    if not content or "[DIGEST]" not in content:
        return None
    block = content.split("[DIGEST]", 1)[1]
    # Stop at the next bracketed section, if any.
    block = re.split(r"\n\[[A-Z_]+\]", block, 1)[0]

    def _field(name: str) -> str:
        match = re.search(rf"{name}\s*:\s*(.+)", block, re.IGNORECASE)
        return match.group(1).strip() if match else ""

    summary = _field("summary")
    if not summary:
        return None  # a digest without a summary is not worth relaying

    findings = []
    fm = re.search(r"key_findings\s*:\s*(.*?)(?:\nrisk_level|\nrecommended_next_action|\nconfidence|$)", block, re.IGNORECASE | re.DOTALL)
    if fm:
        for line in fm.group(1).splitlines():
            line = line.strip().lstrip("-").strip()
            if line:
                findings.append(line)

    risk = _field("risk_level").lower()
    return {
        "summary": summary,
        "key_findings": findings[:3],
        "risk_level": risk if risk in {"low", "medium", "high"} else "unknown",
        "recommended_next_action": _field("recommended_next_action"),
        "confidence": _field("confidence").lower() or "unknown",
    }
