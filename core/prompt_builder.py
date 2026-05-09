from config.roles import ROLE_EXPLORATION, ROLE_REASONING, VALID_ROLES


def build_prompt(*, role: str, task: str, session_id: str, evidence: str | None = None) -> str:
    if role not in VALID_ROLES:
        raise ValueError(f"unsupported role: {role}")

    parts = [
        "[WORKFLOW_CONTEXT]",
        "source: proxy",
        f"role: {role}",
        f"session_id: {session_id}",
        "",
        "rules:",
        "- follow assigned role strictly",
        "- no scope expansion",
        "- no unnecessary explanation",
        "- output must be structured if possible",
        "",
    ]

    if evidence is not None:
        parts.extend(["[EVIDENCE]", evidence.strip(), ""])

    parts.extend(["[TASK]", task.strip()])

    if role == ROLE_EXPLORATION:
        parts.extend(["", "Return bounded evidence only."])

    if role == ROLE_REASONING:
        parts.extend(["", "Return the reasoning result only."])

    if role not in {ROLE_EXPLORATION, ROLE_REASONING}:
        parts.extend(["", "Return the requested result only."])

    return "\n".join(parts)
