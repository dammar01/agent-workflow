from config.roles import (
    ROLE_EXPLORATION,
    ROLE_REASONING,
    ROLE_VERIFICATION,
    VALID_ROLES,
)
from config.settings import DEFAULT_MAX_TASK_CHARS

_EVIDENCE_ROLES = {ROLE_EXPLORATION, ROLE_REASONING}


def _cap_task(task: str) -> tuple[str, dict | None]:
    """Cap the task string before it becomes part of the one-arg CLI prompt.

    Scaffolding is fixed cost; the task is the only caller-controlled size. Truncating
    it visibly here turns a would-be `prompt_too_long` call failure into a degraded-but-
    delivered prompt. The in-band marker is explicit, but it lands INSIDE the truncated
    task; the returned info dict is the out-of-band signal so a caller can surface
    `task_truncated` in the response meta BEFORE main_agent reads the (silently cut) task.

    Returns (capped_task, info) where info is None when no truncation happened.
    """
    task = task.strip()
    original = len(task)
    if original <= DEFAULT_MAX_TASK_CHARS:
        return task, None
    keep = DEFAULT_MAX_TASK_CHARS
    capped = (
        task[:keep]
        + f"\n…[task truncated: {original - keep} chars over {DEFAULT_MAX_TASK_CHARS}-char cap;"
        " split into narrower delegated calls if detail was lost]"
    )
    return capped, {
        "task_truncated": True,
        "task_original_chars": original,
        "task_kept_chars": keep,
    }


# Bounds, not preferences. The whole prompt is one command-line argument capped at 8191
# characters on Windows, so an unbounded file list would push a verification prompt past
# the shell limit and fail before opencode ran.
_CHANGED_FILES_MAX = 25


def _changed_files_block(project_root: str | None) -> list[str]:
    """The files under verification, resolved from git instead of asked for.

    Resolving changed files here gives the verifier an explicit scope immediately.

    Silent on failure by design: no git, no repo, or a detached worktree just means the
    verifier falls back to reading. Announcing an empty list would be worse than saying
    nothing, since "nothing changed" is a claim this cannot support.
    """
    if not project_root:
        return []
    try:
        from pathlib import Path

        from core.evidence.quick_verify import _run, changed_files

        root = Path(project_root)
        files = changed_files(root)
        if not files:
            return []
        shown = files[:_CHANGED_FILES_MAX]
        lines = [
            "[CHANGED_FILES — resolved from git; THIS is the change under verification]",
            *(f"- {rel}" for rel in shown),
        ]
        if len(files) > len(shown):
            lines.append(
                f"- ...and {len(files) - len(shown)} more (list truncated, not the change)"
            )
        # Only the summary line of --stat: it carries the magnitude of the change in one
        # line, where the per-file breakdown would repeat the list above at length.
        code, out = _run(["git", "diff", "--shortstat", "HEAD"], root)
        if code == 0 and out.strip():
            lines.append(f"scale: {out.strip().splitlines()[0].strip()}")
        lines.append("")
        return lines
    except Exception:
        return []


def _sidecar_block(
    runtime_dir: str | None,
    *,
    has_leads: bool,
    has_facts: bool,
    fanout: bool,
) -> list[str]:
    """Anchor pointing the second agent at the runtime evidence files.

    Ranked leads and cached facts live in runtime files because the command-line prompt
    is capped at 8191 characters on Windows. This block only names those sidecars; the
    reading and fan-out protocol lives in AGENTS.md.
    """
    if not runtime_dir:
        return []
    lead_note = (
        "task-ranked graph starting points (files + communities); WEAK hints, verify by reading"
        if has_leads
        else "null/empty this call → traverse from the task directly, no graph shortlist"
    )
    fact_note = (
        "cached claims from prior runs; treat as LEADS to verify, NOT ground truth"
        if has_facts
        else "empty this call → no cached facts to reuse"
    )
    lines = [
        "[EVIDENCE_SIDECARS — READ these files yourself before answering; full protocol in AGENTS.md]",
        f"- leads: {runtime_dir}/leads.json — {lead_note}",
        f"- facts: {runtime_dir}/facts.json — {fact_note}",
    ]
    if fanout:
        lines.append(
            "- FAN-OUT call: dispatch one sub-agent per community in leads.json, then merge;"
            " fan-out rules in AGENTS.md"
        )
    lines.append("")
    return lines


def build_prompt(
    *,
    role: str,
    task: str,
    session_id: str,
    command: str,
    project_root: str,
    runtime_dir: str | None = None,
    has_facts: bool = False,
    has_leads: bool = False,
    subagent_fanout: bool = False,
    declared_tools: list | None = None,
    meta_sink: dict | None = None,
) -> str:
    if role not in VALID_ROLES:
        raise ValueError(f"unsupported role: {role}")

    task, trunc_info = _cap_task(task)
    if trunc_info and meta_sink is not None:
        meta_sink.update(trunc_info)

    header = [
        "[WORKFLOW_AGENT]",
        "source: second_agent",
        f"command: {command}",
        f"role: {role}",
        f"session_id: {session_id}",
        f"project_root: {project_root}",
        # What this command is permitted to need. A declaration the agent can read and a
        # line the audit trail can quote — NOT the enforcement boundary, which is the
        # provider's own permission config. Written here so the narrower per-command
        # policy is visible to the thing being asked to honour it.
        *(
            [f"permitted_tools: {', '.join(declared_tools)}"]
            if declared_tools
            else []
        ),
        "",
    ]

    sidecar_block = _sidecar_block(
        runtime_dir,
        has_leads=has_leads,
        has_facts=has_facts,
        fanout=subagent_fanout and role in _EVIDENCE_ROLES,
    )

    if role in _EVIDENCE_ROLES:
        return "\n".join(
            [
                *header,
                *sidecar_block,
                "[CONSTRAINTS — full evidence protocol in AGENTS.md; anchors only here]",
                "- read-only evidence; grounded claims need file:line; unproven numbers/deps → `assumptions`; DB/MCP findings → `external`",
                "- you are the PRIMARY worker: do the exploration yourself, trace reverse deps into `dependents`, state `scope_covered` vs `scope_not_covered`",
                "",
                "[TASK]",
                task,
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
                        "subagents: c<N>, c<N> (clusters you actually dispatched)"
                        " | none (no spawn tool; tools: ...) | none (denied: <rule/error>)"
                        " | none (declined: <reason>)   [REQUIRED — never omit this line]",
                    ]
                    if (subagent_fanout and role in _EVIDENCE_ROLES)
                    else []
                ),
                "",
                *_digest_format(),
            ]
        )

    # Only /.verify gets the severity-tiered contract; other routes use the terse fallback.
    if command == "verify":
        return "\n".join(
            [
                *header,
                *_changed_files_block(project_root),
                *sidecar_block,
                "[CONSTRAINTS — severity definitions in AGENTS.md 'Verify Routing'; the routing table itself is inline below]",
                "- report only: no file writes, no user questions (main_agent's domain)",
                "- verify ONLY the changed files above and their consumers (blast radius); no scope expansion",
                "- tag EVERY finding on its FIRST line: `severity: <critical|high|medium|low> | origin: <introduced|regression|pre_existing|unknown> | scope_relation: <in_scope|out_of_scope>` — a finding whose opening line lacks all three is rejected",
                "- ROUTE BY TAGS, never by severity alone: introduced|regression + critical|high => blocking_findings; unknown + critical|high => blocking_findings (fail closed, until evidence moves it off `unknown`); pre_existing + critical|high => escalations; introduced|regression + out_of_scope + medium|low => escalations; everything else => notes",
                "- EVIDENCE = file:line OR non-code ref (db:/mcp:/runtime:/cmd:); no ref + no concrete failing scenario => NOT critical/high",
                "- `checks_run` = what you actually ran/read; an unrun check is never a pass",
                "",
                "[TASK]",
                task,
                "",
                "[OUTPUT_FORMAT]",
                "Return ONLY this structure:",
                "",
                *_verification_format(),
                "",
                *_digest_format(),
            ]
        )

    if command == "sweep":
        return "\n".join(
            [
                *header,
                *_changed_files_block(project_root),
                *sidecar_block,
                "[CONSTRAINTS — sweep = blast-radius EVIDENCE gathering, not judgement]",
                "- read-only; no file writes, no user questions (main_agent's domain)",
                "- for EACH changed file/symbol above: grep the symbol project-wide → list who CONSUMES/CALLS it = blast radius; cover breadth, do NOT stop at the first hit",
                "- flag risk files (config, auth, payment, schema, migration, env, secret) — they amplify blast radius",
                "- DB-touching change + read-only DB MCP available → inspect affected table/column, put under `external`; never call write/exec tools",
                "- gather evidence only; main_agent judges severity. State what you could NOT reach in `scope_not_covered`",
                "",
                "[TASK]",
                task,
                "",
                "[OUTPUT_FORMAT]",
                "Return ONLY this structure:",
                "",
                *_sweep_format(),
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
            task,
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
        "- [EXTERNAL:<source>] <finding from MCP/docs/DB, not this codebase — e.g. mcp:laravel-boost:database-query, db:<table.column>, context7> | none",
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
        "verdict: DONE | NEEDS FIX | INCOMPLETE",
        "  (NEEDS FIX only when blocking_findings is non-empty —"
        " escalations and notes never change this."
        " INCOMPLETE when you could not finish the verification at all;"
        " gaps you CAN name belong in not_verified with the verdict still DONE)",
        "",
        "blocking_findings:   # routed here by the table above, not by severity alone",
        "- severity: <critical|high> | origin: <introduced|regression|unknown>"
        " | scope_relation: <in_scope|out_of_scope>",
        "  problem: <what is wrong> [file:line | db:<obj> | mcp:<server:tool> | runtime:<key> | cmd:<output>]",
        "  trigger: <concrete input/state that makes it fail>",
        "  impact: <what breaks for the user>",
        "  fix: <specific change>",
        "- none (say why: what you checked that came back clean)",
        "",
        "escalations:         # critical/high that does NOT block this verdict — user decides",
        "- severity: <critical|high> | origin: <pre_existing|introduced|regression>"
        " | scope_relation: <in_scope|out_of_scope>",
        "  problem: <what is wrong> [file:line | db:<obj> | mcp:<server:tool> | runtime:<key> | cmd:<output>]",
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


def _sweep_format() -> list[str]:
    return [
        "[SWEEP IMPACT]",
        "confidence: low | medium | high — <reason>",
        "",
        "changed_files:",
        "- <file from CHANGED_FILES + one-line what changed> | none",
        "",
        "blast_radius:",
        "- <consumer/caller that would break if the change is wrong> [file:line] (grep the symbol; list breadth, not one example) | none",
        "",
        "risk_hits:",
        "- <changed file matching config/auth/payment/schema/migration/env/secret + why it amplifies impact> | none",
        "",
        "external:",
        "- [EXTERNAL:mcp:<server:tool>|db:<table.column>] <DB/data-inspection finding for a schema/data change, read-only> | none",
        "",
        "scope_covered:",
        "- <files/symbols actually traced>",
        "",
        "scope_not_covered:",
        "- <changed surface not traced + why> | none",
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
        # Bracketed, matching `durable_facts` below. The two lines used to disagree — this
        # one asked for a bare `claim + file:line` — and the anchor extractor only ever
        # read brackets, so the section the contract defines BY its evidence was the one
        # section whose evidence never reached `meta.claims[].refs`.
        "- <claim> [file:line] (WAJIB evidence; no file:line → do not put here)",
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
        "- [EXTERNAL:<source>] <finding from MCP/docs/DB, not this codebase — e.g. mcp:laravel-boost:database-query, db:<table.column>, context7> | none",
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
