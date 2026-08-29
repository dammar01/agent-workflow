"""The prompt still asks for the shape the runtime knows how to read back.

`build_prompt` is one end of a contract whose other end is a parser. Nothing connected
them: the executor decides a reply is evidence by looking for markers in
`_EVIDENCE_MARKERS`, the verify path reads a severity-tagged structure, and the prompt
that asks for both was assembled by a function with no test at all. Rename a section
header here and every call starts failing its contract check for a reason no traceback
names.

That failure mode has already shipped once in this repo, in `messages.py`'s case: a
regex rewrite hit prose, four `next_action` lines told users to run `_main().run`, and
211 behavioural assertions passed because none of them read the words. Text is the part
behaviour tests cannot see.

So these assertions compare the prompt against the CONSUMER's own constants rather than
against strings copied from prompt_builder. A test that pastes the expected header only
locks itself: change both and it stays green while the parser breaks.
"""

from config.roles import ROLE_EXPLORATION, ROLE_REASONING, ROLE_VERIFICATION
from config.settings import DEFAULT_MAX_TASK_CHARS
from core.provider.continuation import _EVIDENCE_MARKERS
from core.prompt.prompt_builder import build_prompt
from tests.checks.support import assert_true

# Accessor syntax that is code, never prose. Same list as messages.py, applied to text
# this module BUILDS rather than text it stores — a leak can arrive either way.
_LEAKS = ("_main().", "self.adapter.", "self.opencode.")

_BASE = {
    "session_id": "sid-1",
    "project_root": "/repo",
}


def _build(role: str, command: str, **kwargs) -> str:
    return build_prompt(role=role, task="do the thing", command=command, **_BASE, **kwargs)


# Branches whose replies the executor holds to the evidence contract. The gate at
# core/executor.py:267 keys on ROLE, so this list must be derived the same way — a role
# added to that check and not to this one is a contract nobody asks for.
_EVIDENCE_BRANCHES = (
    (ROLE_EXPLORATION, "explore"),
    (ROLE_REASONING, "plan"),
    (ROLE_REASONING, "analyze"),
)

# Branches with their own structured contract, checked by result_shaping rather than by
# the evidence markers.
_STRUCTURED_BRANCHES = (
    (ROLE_VERIFICATION, "verify"),
    (ROLE_VERIFICATION, "sweep"),
)

# Everything else falls through to a terse default that deliberately asks for no
# contract at all — `doctor` and friends are answered in prose and parsed by nobody.
_FALLBACK_BRANCH = (ROLE_VERIFICATION, "doctor")

_ALL_BRANCHES = (*_EVIDENCE_BRANCHES, *_STRUCTURED_BRANCHES, _FALLBACK_BRANCH)


def _test_prompt_contract_blocks() -> None:
    # --- properties every branch shares, whatever contract it carries ---------------
    for role, command in _ALL_BRANCHES:
        prompt = _build(role, command)
        label = f"role={role} command={command}"

        for required in ("[WORKFLOW_AGENT]", f"command: {command}", f"role: {role}"):
            assert_true(
                required in prompt,
                f"{label}: header lost {required!r} — the reply's provenance is read "
                "back from these lines",
            )

        assert_true(
            "[TASK]" in prompt and "do the thing" in prompt,
            f"{label}: the task itself did not survive into the prompt",
        )

        for leak in _LEAKS:
            assert_true(
                leak not in prompt,
                f"{label}: internal accessor syntax {leak!r} reached the prompt",
            )

    # --- the evidence roles must ask for what the executor will demand back ---------
    for role, command in _EVIDENCE_BRANCHES:
        prompt = _build(role, command)
        label = f"role={role} command={command}"
        lowered = prompt.lower()
        assert_true(
            any(marker in lowered for marker in _EVIDENCE_MARKERS),
            f"{label}: prompt asks for no section in core.provider.continuation._EVIDENCE_MARKERS, so "
            "a perfectly obedient reply would be rejected as conversation",
        )
        assert_true(
            "[DIGEST]" in prompt,
            f"{label}: no [DIGEST] requested — the executor's continuation path keys on "
            "that marker and would ask for a block it never asked for first",
        )

    # --- the structured branches carry a digest too ---------------------------------
    for role, command in _STRUCTURED_BRANCHES:
        prompt = _build(role, command)
        assert_true(
            "[DIGEST]" in prompt,
            f"role={role} command={command}: no [DIGEST] requested, so a reply that "
            "stalls mid-answer cannot be told from one that finished",
        )

    # --- and the fallback deliberately carries neither -------------------------------
    fallback = _build(*_FALLBACK_BRANCH)
    assert_true(
        "[DIGEST]" not in fallback and "[OUTPUT_FORMAT]" not in fallback,
        "the terse fallback grew a contract. It is reached by commands the executor "
        "never contract-checks (the gate at core/executor.py keys on role), so asking "
        "for a structure here promises a check that does not exist",
    )


def _test_verify_branch_carries_routing_contract() -> None:
    """The severity/origin/scope triple is the verify contract, not decoration."""
    prompt = _build(ROLE_VERIFICATION, "verify")
    for token in ("severity:", "origin:", "scope_relation:", "blocking_findings", "escalations"):
        assert_true(
            token in prompt,
            f"verify prompt lost {token!r} — result_shaping routes findings by these tags, "
            "and a reply that was never asked for them cannot be routed at all",
        )

    # The terse fallback must NOT carry it: asking every command for a severity table
    # would make `doctor` answer in a shape nothing reads.
    fallback = _build(ROLE_VERIFICATION, "doctor")
    assert_true(
        "blocking_findings" not in fallback,
        "the terse fallback grew the verify contract — every non-verify command would "
        "start returning a structure no caller parses",
    )


def _test_permitted_tools_line() -> None:
    with_tools = _build(ROLE_EXPLORATION, "explore", declared_tools=["read", "grep"])
    assert_true(
        "permitted_tools: read, grep" in with_tools,
        "declared_tools did not reach the prompt — the per-command tool policy is a "
        "declaration, and a declaration nobody is shown declares nothing",
    )

    without = _build(ROLE_EXPLORATION, "explore")
    assert_true(
        "permitted_tools:" not in without,
        "an empty tool policy still emitted the line, which reads as 'permitted: nothing' "
        "rather than 'unspecified'",
    )


def _test_task_cap_is_visible_in_and_out_of_band() -> None:
    sink: dict = {}
    long_task = "x" * (DEFAULT_MAX_TASK_CHARS + 500)
    prompt = build_prompt(
        role=ROLE_EXPLORATION,
        task=long_task,
        command="explore",
        meta_sink=sink,
        **_BASE,
    )

    assert_true(
        sink.get("task_truncated") is True,
        "an over-cap task was truncated without telling the caller: main_agent would read "
        "a silently shortened task and never learn detail was dropped",
    )
    assert_true(
        sink.get("task_original_chars") == len(long_task)
        and sink.get("task_kept_chars") == DEFAULT_MAX_TASK_CHARS,
        f"truncation reported the wrong sizes: {sink}",
    )
    assert_true(
        "task truncated" in prompt,
        "the in-band marker is missing, so the second agent cannot tell a cut task from a "
        "complete one",
    )

    short_sink: dict = {}
    build_prompt(
        role=ROLE_EXPLORATION,
        task="short",
        command="explore",
        meta_sink=short_sink,
        **_BASE,
    )
    assert_true(
        not short_sink,
        "a task under the cap reported truncation — a false `task_truncated` sends "
        "main_agent splitting work that was never too long",
    )


def _test_unknown_role_is_refused() -> None:
    try:
        _build("auditor", "explore")
    except ValueError:
        return
    raise AssertionError(
        "an unknown role built a prompt instead of raising: the role selects the output "
        "contract, so an unrecognised one produces a reply nothing knows how to parse"
    )
