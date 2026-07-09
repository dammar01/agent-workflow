from config.roles import (
    ROLE_EXPLORATION,
    ROLE_REASONING,
    ROLE_VERIFICATION,
    VALID_ROLES,
)

_EVIDENCE_ROLES = {ROLE_EXPLORATION, ROLE_REASONING}


def build_prompt(
    *,
    role: str,
    task: str,
    session_id: str,
    command: str,
    project_root: str,
    known_facts: list[str] | None = None,
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

    if role in _EVIDENCE_ROLES:
        return "\n".join(
            [
                *header,
                *facts_block,
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
