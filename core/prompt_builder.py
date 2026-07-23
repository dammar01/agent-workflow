from config.roles import (
    ROLE_EXPLORATION,
    ROLE_REASONING,
    ROLE_VERIFICATION,
    VALID_ROLES,
)

_EVIDENCE_ROLES = {ROLE_EXPLORATION, ROLE_REASONING}


def _subagent_block(graph_leads: dict | None) -> list[str]:
    """Explicit fan-out instruction: one sub-agent per graph cluster.

    Only emitted when the graph gives at least two clusters — dispatching a single
    sub-agent costs a round trip and buys nothing over reading the files directly.

    Output stays deliberately terse. Large structured responses have been observed to
    die mid-stream, and fan-out multiplies output volume, so per-cluster findings are
    capped and cluster attribution is a two-character tag rather than a prose field.
    """
    clusters = (graph_leads or {}).get("communities") or []
    if len(clusters) < 2:
        return []

    lines = [
        "[SUBAGENT_PLAN — dispatch these in parallel, then merge]",
        "- FIRST check your own tool list for a sub-agent/task/dispatch tool. If one exists, using it is MANDATORY — reading the clusters yourself instead is a failed instruction, not a shortcut",
        "- spawn ONE sub-agent per cluster below, all at once, not one after another",
        "- each sub-agent is scope-bounded to ITS OWN cluster's files; it must not read outside them",
        "- keep each sub-agent's report SHORT: max 5 grounded claims, each one line with file:line",
        "- you merge the reports; sub-agent text is raw material, not the answer",
        "- tag every merged claim with its origin cluster as a leading [cN] (e.g. `[c3] Router routes by command string [core/router.py:16]`)",
        "- a cluster that yields nothing relevant: say so under that cluster, do not pad it",
        "- list the clusters you actually dispatched on the `subagents:` line",
        "- ONLY if your tool list genuinely has no such tool: write `subagents: none (no spawn tool; tools: <name, name, ...>)` naming EVERY tool you do have, then read the clusters yourself in order. Claiming 'no spawn tool' without that list is not an acceptable answer",
        "- never report fan-out you did not perform; an honest sequential read is a valid result, a false claim is not",
    ]
    for cluster in clusters:
        members = ", ".join(cluster.get("files") or [])
        lines.append(f"- c{cluster['community']}: {members}")
    lines.append("")
    return lines


def _graph_block(graph_leads: dict | None) -> list[str]:
    """Ranked shortlist from graphify, framed as leads.

    Framing matters: a graph edge says two things are related, not that a claim is
    true. Presented as findings these would come back as `grounded` without anyone
    opening the file.
    """
    if not graph_leads or not graph_leads.get("files"):
        return []

    lines = [
        "[GRAPH_LEADS — from graphify-out/graph.json; ranked STARTING POINTS, not evidence]",
        "- open these first, then follow the code; a graph edge is never a substitute for reading the file",
        "- a file listed here that turns out to be irrelevant is expected — say so rather than forcing it into the answer",
    ]
    if graph_leads.get("stale"):
        lines.append(
            "- WARNING: the graph is older than the current sources; treat every lead as possibly outdated"
        )

    lines.append("candidate_files:")
    for row in graph_leads["files"]:
        community = row.get("community")
        suffix = f" [community {community}]" if community is not None else ""
        lines.append(f"- {row['file']}{suffix}")

    if graph_leads.get("communities"):
        lines.append("clusters:")
        for cluster in graph_leads["communities"]:
            members = ", ".join(cluster.get("files") or [])
            lines.append(f"- community {cluster['community']}: {members}")

    lines.append("")
    return lines


def build_prompt(
    *,
    role: str,
    task: str,
    session_id: str,
    command: str,
    project_root: str,
    known_facts: list[str] | None = None,
    graph_leads: dict | None = None,
    subagent_fanout: bool = False,
) -> str:
    if role not in VALID_ROLES:
        raise ValueError(f"unsupported role: {role}")

    header = [
        "[WORKFLOW_AGENT]",
        "source: second_agent",
        f"command: {command}",
        f"role: {role}",
        f"session_id: {session_id}",
        f"project_root: {project_root}",
        "",
    ]

    facts_block: list[str] = []
    if known_facts:
        facts_block = [
            "[KNOWN_FACTS — cached from prior runs; treat as LEADS to verify, NOT ground truth]",
            *(f"- {fact}" for fact in known_facts),
            "",
        ]

    graph_block = _graph_block(graph_leads)
    subagent_block = _subagent_block(graph_leads) if subagent_fanout else []

    if role in _EVIDENCE_ROLES:
        return "\n".join(
            [
                *header,
                *facts_block,
                *graph_block,
                *subagent_block,
                "[CONSTRAINTS]",
                "- do not implement, do not plan, do not modify files",
                "- flag all uncertainties explicitly",
                "- bounded scope only — no expansion",
                "- you are the primary worker for this command; do most of the exploration work",
                "- read graphify output first, then inspect only the most relevant source files",
                "- provide scoped reasoning grounded in evidence, not just file lists",
                "- if evidence conflicts, say so clearly instead of guessing",
                "- do NOT emit open_questions or any question to the user; that is main_agent's domain",
                "- tag every claim: put it under `grounded` ONLY with a file:line reference; anything without direct evidence (numbers, dependencies, guesses) goes under `assumptions`",
                "- a number/metric with no basis in the code is an assumption — mark it `[needs-calibration]`, never state it as fact",
                "- a dependency A->B belongs in `grounded` only with the file:line that proves the coupling; otherwise `assumptions` as `[unverified]`",
                "- findings from external tools/MCP (context7, docs, web) go under `external` with the source tag — never mix them into codebase `grounded`",
                "- state `scope_covered` vs `scope_not_covered` explicitly; cross-system surfaces you were not asked to inspect are `scope_not_covered`, not silent gaps",
                "- for plan/analyze: trace REVERSE dependencies of the change target (grep the symbol/module project-wide for callers/consumers) and list them under `dependents` with file:line — this is the blast radius the main_agent turns into risks",
                "- under `durable_facts`, promote ONLY facts that persist across code changes — config values, code patterns, architectural invariants — each tagged [config|pattern|invariant] with file:line; skip volatile line-level details (they seed a reusable fact store, so wrong/transient ones poison it)",
                "- for plan/analyze: if the task touches an external library/framework/SDK/API (detect from imports or the package manifest), read its docs FIRST via context7 (resolve-library-id -> query-docs), capture the library version, and put doc-derived facts under `external` as [EXTERNAL:context7 <lib>@<version>] — do NOT guess a library API from memory. No external lib involved -> skip (do not fetch docs for internal-only work).",
                "",
                "[TASK]",
                task.strip(),
                "",
                "[OUTPUT_FORMAT]",
                "Return ONLY this structure:",
                "",
                *(
                    _exploration_format()
                    if role == ROLE_EXPLORATION
                    else _reasoning_format()
                ),
                *(
                    [
                        "",
                        "subagents: c<N>, c<N> (clusters you actually dispatched) | none (<reason>)",
                    ]
                    if subagent_block
                    else []
                ),
                "",
                *_digest_format(),
            ]
        )

    # ROLE_VERIFICATION also covers init/doctor/sweep/submit, which want the terse
    # fallback — only /.verify gets the severity-tiered contract.
    if command == "verify":
        return "\n".join(
            [
                *header,
                "[CONSTRAINTS]",
                "- do not implement, do not modify files; report only",
                "- no scope expansion beyond the change under verification",
                "- every finding MUST carry ALL THREE tags — severity, origin, scope_relation:",
                "    severity:       critical | high | medium | low",
                "    origin:         introduced | regression | pre_existing | unknown",
                "    scope_relation: in_scope | out_of_scope",
                "- severity scale (apply literally, do not inflate to draw attention nor deflate to pass):",
                "    critical = data loss, security hole, silently wrong result, or every command broken",
                "    high     = normal path of a feature broken, existing caller regressed, stated contract violated",
                "    medium   = edge case, degraded behaviour, or a real defect with an available workaround",
                "    low      = naming/style/doc drift, or a hypothetical with no demonstrated trigger",
                "- origin scale: introduced = this change created it; regression = this change broke"
                " something that used to work; pre_existing = present beforehand, this change did not"
                " touch it; unknown = you could not establish which",
                "- scope_relation: in_scope = inside what this change was meant to touch;"
                " out_of_scope = outside it (an out_of_scope `introduced` finding IS a scope violation,"
                " report it as such)",
                "- severity ALONE does not decide blocking. Route every finding by this table:",
                "    introduced/regression + in_scope      + critical|high -> blocking_findings",
                "    introduced/regression + out_of_scope  + critical|high -> blocking_findings (+ scope violation)",
                "    introduced/regression + out_of_scope  + medium|low    -> escalations",
                "    unknown               + any           + critical|high -> blocking_findings (fail closed)",
                "    pre_existing          + any           + critical|high -> escalations",
                "    anything else                                        -> notes",
                "- `unknown` is not an escape hatch: to move a finding off unknown, cite the evidence"
                " (diff, git history, prior version). If you cannot, it stays unknown and it blocks",
                "- `escalations` do NOT change the verdict, but they are NOT notes: they are real"
                " critical/high problems the user must decide about. Never bury one in notes",
                "- a finding without a file:line and a concrete failing scenario is NOT critical/high;"
                " demote it to a note and say what evidence is missing",
                "- that rule is about evidence quality, NOT about suppressing systemic problems:"
                " a defect spanning many sites stays critical/high — cite representative file:line"
                " occurrences and state how widespread it is",
                "- state what you actually ran or read under `checks_run`, and what you could NOT"
                " verify under `not_verified` — an unrun check is never a pass",
                "- do NOT ask the user questions; that is main_agent's domain",
                "",
                "[TASK]",
                task.strip(),
                "",
                "[OUTPUT_FORMAT]",
                "Return ONLY this structure:",
                "",
                *_verification_format(),
                "",
                *_digest_format(),
            ]
        )

    return "\n".join(
        [
            *header,
            "[CONSTRAINTS]",
            "- follow assigned role strictly",
            "- no scope expansion",
            "- no unnecessary explanation",
            "- output must be structured if possible",
            "",
            "[TASK]",
            task.strip(),
            "",
            "Return the requested result only.",
        ]
    )


def _exploration_format() -> list[str]:
    return [
        "[EVIDENCE]",
        "confidence: low | medium | high",
        "",
        "entry_points:",
        "- <file:line or none>",
        "",
        "flow:",
        "- <list or none>",
        "",
        "related_modules:",
        "- <list or none>",
        "",
        "grounded:",
        "- <claim + file:line> (only claims backed by code you read)",
        "",
        "durable_facts:",
        "- [config|pattern|invariant] <fact that persists across code changes> [file:line] | none",
        "",
        "assumptions:",
        "- <inference/guess without direct evidence> [unverified]",
        "",
        "external:",
        "- [EXTERNAL:<source>] <finding from MCP/docs, not this codebase> | none",
        "",
        "scope_covered:",
        "- <files/areas actually inspected>",
        "",
        "scope_not_covered:",
        "- <requested-but-unreachable or cross-system surfaces not inspected> | none",
        "",
        "uncertainties:",
        "- <list>",
    ]


def _verification_format() -> list[str]:
    return [
        "[VERIFICATION]",
        "verdict: DONE | NEEDS FIX",
        "  (NEEDS FIX only when blocking_findings is non-empty —"
        " escalations and notes never change this)",
        "",
        "blocking_findings:   # routed here by the table above, not by severity alone",
        "- severity: <critical|high> | origin: <introduced|regression|unknown>"
        " | scope_relation: <in_scope|out_of_scope>",
        "  problem: <what is wrong> [file:line]",
        "  trigger: <concrete input/state that makes it fail>",
        "  impact: <what breaks for the user>",
        "  fix: <specific change>",
        "- none (say why: what you checked that came back clean)",
        "",
        "escalations:         # critical/high that does NOT block this verdict — user decides",
        "- severity: <critical|high> | origin: <pre_existing|introduced|regression>"
        " | scope_relation: <in_scope|out_of_scope>",
        "  problem: <what is wrong> [file:line]",
        "  why_not_blocking: <which routing rule sent it here>",
        "- none",
        "",
        "notes:               # medium | low — informational",
        "- severity: <medium|low> | origin: <...> | scope_relation: <...> —"
        " <problem> [file:line] — <why it does not block>",
        "- none",
        "",
        "checks_run:",
        "- <command executed / file read / scenario traced + outcome>",
        "",
        "not_verified:",
        "- <area or claim you could NOT check + the reason> | none",
        "",
        "confidence: low | medium | high — <reason>",
    ]


def _digest_format() -> list[str]:
    return [
        "[DIGEST]",
        "summary: <1-2 plain sentences, what main_agent needs to know>",
        "key_findings:",
        "- <max 3 bullets, most important first>",
        "evidence_basis: grounded | mixed | mostly-assumption",
        "risk_level: low | medium | high",
        "recommended_next_action: <one concrete next step>",
        "confidence: low | medium | high",
    ]


def _reasoning_format() -> list[str]:
    return [
        "[EVIDENCE]",
        "confidence: low | medium | high",
        "",
        "grounded:",
        "- <claim + file:line> (WAJIB evidence; no file:line → do not put here)",
        "",
        "durable_facts:",
        "- [config|pattern|invariant] <fact that persists across code changes> [file:line] | none",
        "",
        "assumptions:",
        "- <claim/number/dependency without direct evidence> [unverified|needs-calibration]",
        "",
        "dependencies:",
        "- A->B [proof:file:line] | A->B [assumption-unverified] | none",
        "",
        "dependents:",
        "- <other feature/module that CONSUMES or CALLS the change target — grep the symbol across the codebase> [file:line] | none",
        "",
        "external:",
        "- [EXTERNAL:<source>] <finding from MCP/docs, not this codebase> | none",
        "",
        "scope_covered:",
        "- <files/areas actually inspected>",
        "",
        "scope_not_covered:",
        "- <requested-but-unreachable or cross-system surfaces not inspected> | none",
        "",
        "implications:",
        "- <list>",
        "",
        "uncertainties:",
        "- <list>",
    ]
