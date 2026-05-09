from config.roles import ROLE_EXPLORATION, ROLE_REASONING, VALID_ROLES


def build_prompt(*, role: str, task: str, session_id: str, evidence: str | None = None) -> str:
    if role not in VALID_ROLES:
        raise ValueError(f"unsupported role: {role}")

    normalized_task = " ".join(task.strip().split())
    parts = [normalized_task, ""]

    if evidence is not None:
        parts.extend(["Evidence:", evidence.strip(), ""])

    parts.extend(
        [
            f"Workflow metadata: source=proxy role={role} session_id={session_id}",
            "",
            "Rules:",
            "- follow assigned role strictly",
            "- no scope expansion",
            "- no unnecessary explanation",
            "- output must be structured if possible",
        ]
    )

    if role == ROLE_EXPLORATION:
        parts.extend(["", "Return bounded evidence only."])

    if role == ROLE_REASONING:
        parts.extend(["", "Return the reasoning result only."])

    if role not in {ROLE_EXPLORATION, ROLE_REASONING}:
        parts.extend(["", "Return the requested result only."])

    return "\n".join(parts)
