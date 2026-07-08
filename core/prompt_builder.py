from config.roles import (
    ROLE_EXPLORATION,
    ROLE_REASONING,
    ROLE_VERIFICATION,
    VALID_ROLES,
)

_EVIDENCE_ROLES = {ROLE_EXPLORATION, ROLE_REASONING}


def build_prompt(*, role: str, task: str, session_id: str, command: str, project_root: str) -> str:
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

    if role in _EVIDENCE_ROLES:
        return "\n".join(
            [
                *header,
                "[CONSTRAINTS]",
                "- do not implement, do not plan, do not modify files",
                "- flag all uncertainties explicitly",
                "- bounded scope only — no expansion",
                "- you are the primary worker for this command; do most of the exploration work",
                "- read graphify output first, then inspect only the most relevant source files",
                "- provide scoped reasoning grounded in evidence, not just file lists",
                "- if evidence conflicts, say so clearly instead of guessing",
                "- do NOT emit open_questions or any question to the user; that is main_agent's domain",
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
        "- <list or none>",
        "",
        "flow:",
        "- <list or none>",
        "",
        "related_modules:",
        "- <list or none>",
        "",
        "behavior_hints:",
        "- <list or none>",
        "",
        "ownership_hints:",
        "- <list or none>",
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
        "risk_level: low | medium | high",
        "recommended_next_action: <one concrete next step>",
        "confidence: low | medium | high",
    ]


def _reasoning_format() -> list[str]:
    return [
        "[EVIDENCE]",
        "confidence: low | medium | high",
        "",
        "findings:",
        "- <list>",
        "",
        "reasoning:",
        "- <list>",
        "",
        "implications:",
        "- <list>",
        "",
        "uncertainties:",
        "- <list>",
    ]
