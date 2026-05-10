from config.roles import ROLE_EXECUTION, ROLE_EXPLORATION, ROLE_REASONING, ROLE_VERIFICATION, VALID_ROLES

_EVIDENCE_ROLES = {ROLE_EXPLORATION, ROLE_REASONING}


def build_prompt(*, role: str, task: str, session_id: str) -> str:
    if role not in VALID_ROLES:
        raise ValueError(f"unsupported role: {role}")

    header = [
        "[WORKFLOW_AGENT]",
        "source: proxy",
        f"role: {role}",
        f"session_id: {session_id}",
        "",
    ]

    if role in _EVIDENCE_ROLES:
        return "\n".join([
            *header,
            "[CONSTRAINTS]",
            "- evidence-only: do not reason beyond what evidence shows",
            "- do not implement, do not plan, do not modify files",
            "- flag all uncertainties explicitly",
            "- bounded scope only — no expansion",
            "",
            "[TASK]",
            task.strip(),
            "",
            "[OUTPUT_FORMAT]",
            "Return ONLY this structure:",
            "",
            *(_exploration_format() if role == ROLE_EXPLORATION else _reasoning_format()),
        ])

    return "\n".join([
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
    ])


def _exploration_format() -> list[str]:
    return [
        "[EVIDENCE]",
        "confidence: low | medium | high",
        "",
        "entry_points:",
        "- <list or none>",
        "",
        "related_modules:",
        "- <list or none>",
        "",
        "ownership_hints:",
        "- <list or none>",
        "",
        "uncertainties:",
        "- <list>",
    ]


def _reasoning_format() -> list[str]:
    return [
        "[EVIDENCE]",
        "confidence: low | medium | high",
        "",
        "findings:",
        "- <list>",
        "",
        "implications:",
        "- <list>",
        "",
        "uncertainties:",
        "- <list>",
    ]
