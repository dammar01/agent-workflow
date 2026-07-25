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


# Shared by both fan-out shapes. Kept in one place so the graph and no-graph plans
# cannot drift into giving the second agent two different sets of rules.
_SUBAGENT_RULES = [
    "- FIRST check your own tool list for a sub-agent/task/dispatch tool. If one exists, using it is MANDATORY — reading the slices yourself instead is a failed instruction, not a shortcut",
    "- spawn ONE sub-agent per slice below, all at once, not one after another",
    "- each sub-agent is scope-bounded to ITS OWN slice; it must not read outside it",
    "- keep each sub-agent's report SHORT: max 5 grounded claims, each one line with file:line",
    "- you merge the reports; sub-agent text is raw material, not the answer",
    "- tag every merged claim with its origin slice as a leading [cN] (e.g. `[c3] Router routes by command string [core/router.py:16]`)",
    "- a slice that yields nothing relevant: say so under that slice, do not pad it",
    "- list the slices you actually dispatched on the `subagents:` line",
    "- ONLY if your tool list genuinely has no such tool: write `subagents: none (no spawn tool; tools: <name, name, ...>)` naming EVERY tool you do have, then read the slices yourself in order. Claiming 'no spawn tool' without that list is not an acceptable answer",
    "- never report fan-out you did not perform; an honest sequential read is a valid result, a false claim is not",
]

# Graph-free partition. Deliberately by INVESTIGATION ANGLE rather than by directory:
# the second agent does not know the layout before it reads, so "src/ vs lib/" is a
# guess, while "entry points vs callers vs config" is answerable in any repo.
_BLIND_SLICES = [
    "c1: entry points and command/request routing — how execution starts and where it is dispatched",
    "c2: the core modules that do the work for this task, and the data they pass between them",
    "c3: callers and consumers of those modules — who would break if they changed (reverse dependencies)",
    "c4: configuration, defaults, and tests that pin the behaviour under discussion",
]


def _subagent_block(graph_leads: dict | None) -> list[str]:
    """Explicit fan-out instruction: one sub-agent per slice of the codebase.

    Two shapes. With at least two graph clusters, the clusters ARE the slices — that is
    the better partition, because it comes from the actual import graph. Without them
    the work is split by investigation angle instead: a repo with no graphify output is
    the one where a serial read costs the most, so falling back to no fan-out at all
    optimised the wrong case.

    Output stays terse because fan-out multiplies response volume; per-slice findings
    are capped and attribution uses compact tags.
    """
    clusters = (
        []
        if (graph_leads or {}).get("stale")
        else ((graph_leads or {}).get("communities") or [])
    )

    if len(clusters) >= 2:
        lines = [
            "[SUBAGENT_PLAN — dispatch these in parallel, then merge]",
            *_SUBAGENT_RULES,
        ]
        for cluster in clusters:
            members = ", ".join(cluster.get("files") or [])
            lines.append(f"- c{cluster['community']}: {members}")
        lines.append("")
        return lines

    return [
        "[SUBAGENT_PLAN — dispatch these in parallel, then merge]",
        "- no dependency graph is available for this project, so the slices below are by"
        " investigation angle, not by file. Each sub-agent finds its own files.",
        *_SUBAGENT_RULES,
        *(f"- {slice_}" for slice_ in _BLIND_SLICES),
        "- a slice that turns out not to apply to this task: report it empty, do not"
        " invent scope for it",
        "",
    ]


def _graph_block(graph_leads: dict | None) -> list[str]:
    """Ranked shortlist from graphify, framed as leads.

    Framing matters: a graph edge says two things are related, not that a claim is
    true. Presented as findings these would come back as `grounded` without anyone
    opening the file.
    """
    if not graph_leads or not graph_leads.get("files"):
        return []

    stale = graph_leads.get("stale")
    if stale:
        lines = [
            "[GRAPH_HINT — from graphify-out/graph.json, but the graph is OLDER than the current sources]",
            "- treat these as a WEAK hint only, NOT the current structure: files may be renamed, moved, or deleted",
            "- confirm each still exists and is relevant by reading it; if the graph looks wrong, ignore it and traverse from the task directly",
        ]
    else:
        lines = [
            "[GRAPH_LEADS — from graphify-out/graph.json; ranked STARTING POINTS, not evidence]",
            "- open these first, then follow the code; a graph edge is never a substitute for reading the file",
            "- a file listed here that turns out to be irrelevant is expected — say so rather than forcing it into the answer",
        ]

    lines.append("candidate_files:")
    for row in graph_leads["files"]:
        community = row.get("community")
        # A stale graph's community numbers are as suspect as its edges — omit them.
        suffix = "" if stale or community is None else f" [community {community}]"
        lines.append(f"- {row['file']}{suffix}")

    if not stale and graph_leads.get("communities"):
        lines.append("clusters:")
        for cluster in graph_leads["communities"]:
            members = ", ".join(cluster.get("files") or [])
            lines.append(f"- community {cluster['community']}: {members}")

    lines.append("")
    return lines


def _compact_leads(graph_leads: dict | None, max_files: int = 6) -> dict | None:
    """A shorter lead list, clusters dropped.

    The whole prompt travels as one command-line argument and the Windows shell caps
    that at 8191 characters. Verification prompts are already the longest scaffolding
    in this file (the severity/origin/routing contract), so the leads they carry have
    to be the short form — and clusters are the part verification does not use, since
    it is not fanning out.
    """
    if not graph_leads or not graph_leads.get("files"):
        return graph_leads
    return {
        **graph_leads,
        "files": graph_leads["files"][:max_files],
        "communities": [],
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

        from core.quick_verify import _run, changed_files

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
                "[CONSTRAINTS — full evidence protocol in AGENTS.md; anchors only here]",
                "- read-only evidence; no implement/plan/file-writes; you are the PRIMARY worker — do most of the exploration yourself",
                "- grounded needs file:line; numbers/dependencies without proof go to `assumptions` ([needs-calibration]/[unverified]); external-tool/MCP findings go to `external`, never mixed into codebase `grounded`",
                "- for plan/analyze: trace REVERSE deps (grep the symbol project-wide) into `dependents` = blast radius; if the task touches an external lib, read context7 docs FIRST and put findings under `external`",
                "- task needs DATA/DB evidence (rows, schema, counts, live config) AND a read-only DB MCP is available (laravel-boost or similar) → USE it: query via a READ-ONLY tool, put findings under `external` tagged [EXTERNAL:mcp:<server:tool>|db:<table.column>]. NEVER call write/exec tools (tinker/migrate/seed/eval). DB evidence is your job, not a limitation",
                "- `durable_facts` = only [config|pattern|invariant] that persist across changes, with file:line; skip volatile line-level detail",
                "- flag uncertainties; state `scope_covered` vs `scope_not_covered`; if evidence conflicts, say so — do NOT emit open_questions (main_agent's domain)",
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
                *_changed_files_block(project_root),
                *_graph_block(_compact_leads(graph_leads)),
                "[CONSTRAINTS — full severity defs + routing table in AGENTS.md 'Verify Routing'; anchors only here]",
                "- report only: no file writes, no user questions (main_agent's domain)",
                "- verify ONLY the changed files above and their consumers (blast radius); no scope expansion; graph leads locate consumers, they do not widen scope",
                "- tag EVERY finding severity/origin/scope_relation and route per the Verify Routing table in AGENTS.md — severity ALONE does not decide blocking; `unknown` blocks until evidence moves it off it; do not inflate or deflate",
                "- EVIDENCE = file:line (source) OR non-code ref (db:/mcp:/runtime:/cmd:); no ref of any kind + no concrete failing scenario => NOT critical/high (demote to note, say what's missing)",
                "- `checks_run` = what you actually ran/read; `not_verified` = what you couldn't check + why; an unrun check is never a pass",
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
                *_graph_block(_compact_leads(graph_leads)),
                "[CONSTRAINTS — sweep = blast-radius EVIDENCE gathering, not judgement]",
                "- read-only; no file writes, no user questions (main_agent's domain)",
                "- for EACH changed file/symbol above: grep the symbol project-wide → list who CONSUMES/CALLS it (reverse deps) = blast radius. This is bulk gathering — cover breadth, do NOT stop at the first hit",
                "- flag risk files by name/content (config, auth, payment, schema, migration, env, secret) — these amplify blast radius",
                "- DB-touching change (migration/model/schema) + laravel-boost or similar DB MCP available → inspect affected table/column via a READ-ONLY tool, put under `external`; never call write/exec tools",
                "- gather evidence only; main_agent judges severity/blocking. State what you could NOT reach in `scope_not_covered`",
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
        "verdict: DONE | NEEDS FIX",
        "  (NEEDS FIX only when blocking_findings is non-empty —"
        " escalations and notes never change this)",
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
