from config.roles import (
    ROLE_EXPLORATION,
    ROLE_REASONING,
    ROLE_VERIFICATION,
    VALID_ROLES,
)

_EVIDENCE_ROLES = {ROLE_EXPLORATION, ROLE_REASONING}


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

    Output stays deliberately terse. Large structured responses have been observed to
    die mid-stream, and fan-out multiplies output volume, so per-slice findings are
    capped and slice attribution is a two-character tag rather than a prose field.
    """
    clusters = (graph_leads or {}).get("communities") or []

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

    Verification used to open with "verify the change" and no statement of what the
    change WAS, so the second agent spent its first tool calls rediscovering it — on one
    observed run, 18 reads before it reached the actual question. Every one of those is
    time on a provider stream that has been seen to drop mid-answer, so this is a
    reliability fix as much as a clarity one.

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
                *_changed_files_block(project_root),
                *_graph_block(_compact_leads(graph_leads)),
                "[CONSTRAINTS]",
                "- report only: do not implement, do not modify files, do not ask the user"
                " questions (questions are main_agent's domain)",
                "- verify the changed files above and who consumes them; no scope expansion past that",
                "- graph leads are for finding CONSUMERS of the change (blast radius), NOT for"
                " widening what you verify",
                "- tag EVERY finding with all three, then route it by the table below. Severity"
                " ALONE does not decide blocking:",
                "    severity: critical (data loss | security hole | silently wrong result | every"
                " command broken) | high (feature's normal path broken | existing caller regressed |"
                " stated contract violated) | medium (edge case | degraded | real defect with a"
                " workaround) | low (naming/style/doc drift | hypothetical with no demonstrated trigger)",
                "    origin: introduced (this change created it) | regression (this change broke what"
                " used to work) | pre_existing (already there, untouched by this change) | unknown"
                " (could not establish which)",
                "    scope_relation: in_scope (inside what this change meant to touch) | out_of_scope"
                " (outside it — an out_of_scope `introduced` finding IS a scope violation, report it as such)",
                "    introduced/regression + in_scope      + critical|high -> blocking_findings",
                "    introduced/regression + out_of_scope  + critical|high -> blocking_findings (+ scope violation)",
                "    introduced/regression + out_of_scope  + medium|low    -> escalations",
                "    unknown               + any           + critical|high -> blocking_findings (fail closed)",
                "    pre_existing          + any           + critical|high -> escalations",
                "    anything else                                        -> notes",
                "- do not inflate severity to draw attention, nor deflate it to pass",
                "- `unknown` is not an escape hatch: cite the evidence (diff, git history, prior"
                " version) to move a finding off it, otherwise it stays unknown and it blocks",
                "- `escalations` do NOT change the verdict but are NOT notes: real critical/high"
                " problems the user must decide about. Never bury one in notes",
                "- no file:line + concrete failing scenario => NOT critical/high; demote to a note and"
                " say what evidence is missing. That is about evidence quality, NOT about suppressing"
                " systemic problems — a defect spanning many sites stays critical/high, cite"
                " representative file:line occurrences and state how widespread it is",
                "- `checks_run`: what you actually ran or read. `not_verified`: what you could not"
                " check and why. An unrun check is never a pass",
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
