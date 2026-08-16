"""Every adapter normalises errors and redaction accounting the same way.

The check exists because they did not. opencode kept the redaction hits its own
`_sanitize_meta` collected; codex and agy carried a byte-similar function that discarded
them with `clean, _ = redact_value(...)`. Redaction still happened in all three — the
secret was scrubbed — but two of three providers then reported `redactions: 0` for the
call that scrubbed it, and `.workflow/audit.jsonl` is the record someone reads after an
incident.

Nothing caught it. The provider suite asserts the seam and the selection path, the
redaction suite asserts opencode's boundary, and no test ever put the three adapters
side by side and compared. A divergence between implementations of one contract is
invisible to any test that only ever looks at one of them.

So the assertions below are deliberately written as a loop over ALL registered adapters
rather than three near-copies: a fourth provider joins this check by existing, which is
the same reason the accounting itself now lives in adapters/redaction.py.
"""

from adapters import agy_adapter, codex_adapter, opencode_adapter
from core.contract import ERROR_TYPES
from tests.checks.support import assert_true

# A key shaped like the real thing, kept obviously fake. Two distinct occurrences so a
# per-kind tally that silently collapses duplicates is visible as a wrong count.
_SECRET = "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH"

_ADAPTERS = (
    ("opencode", opencode_adapter),
    ("codex", codex_adapter),
    ("agy", agy_adapter),
)


def _redaction_count(payload: dict) -> int:
    return int((payload.get("meta") or {}).get("redaction_count") or 0)


def _test_adapter_error_normalization() -> None:
    shapes: dict[str, tuple] = {}

    for name, module in _ADAPTERS:
        # --- make_error: every string field must be scrubbed AND counted -------------
        error = module.make_error(
            "worker_died",
            f"worker died holding {_SECRET}",
            f"rotate {_SECRET} then retry",
            meta={"note": _SECRET},
            detail=_SECRET,
        )

        assert_true(
            _SECRET not in repr(error),
            f"{name}: make_error leaked the secret verbatim into the returned payload",
        )
        assert_true(
            _redaction_count(error) > 0,
            f"{name}: make_error scrubbed the secret but recorded redaction_count 0 — "
            "the adapter redacts before the executor sees the result, so an adapter that "
            "drops its own hits makes the audit trail understate exposure as nothing",
        )
        assert_true(
            error.get("ok") is False and error["meta"].get("error_type") in ERROR_TYPES,
            f"{name}: make_error must return ok=False with an error_type from the closed set",
        )

        # --- make_ok: same contract on the success path ------------------------------
        ok = module.make_ok(
            f"evidence mentioning {_SECRET}",
            meta={"note": _SECRET},
            digest={"summary": _SECRET},
        )
        assert_true(
            _SECRET not in repr(ok),
            f"{name}: make_ok leaked the secret verbatim into the returned payload",
        )
        assert_true(
            _redaction_count(ok) > 0,
            f"{name}: make_ok scrubbed the secret but recorded redaction_count 0",
        )

        # --- clean calls must not invent redactions ----------------------------------
        clean = module.make_ok("nothing sensitive here", meta={"note": "plain"})
        assert_true(
            _redaction_count(clean) == 0,
            f"{name}: a call with no secrets reported redactions — a false positive here "
            "is as bad as a miss, because it makes a clean history look like an incident",
        )

        shapes[name] = (tuple(sorted(error.keys())), tuple(sorted(ok.keys())))

    # --- the three must agree on shape, not merely each be self-consistent -----------
    reference = shapes["opencode"]
    for name in ("codex", "agy"):
        assert_true(
            shapes[name] == reference,
            f"{name} returns a different payload shape than opencode "
            f"({shapes[name]} vs {reference}) — the executor consumes all three through "
            "one code path, so a key present for one provider and absent for another is "
            "a branch nobody wrote",
        )


def _test_adapter_redaction_is_shared() -> None:
    """All three must resolve to the SAME function object, not three equal ones.

    Asserting behaviour alone would pass again the moment someone re-copies the helper
    and then edits one copy — which is exactly how the original divergence appeared.
    Identity is the property that actually prevents a recurrence.
    """
    from adapters import redaction

    for name, module in _ADAPTERS:
        assert_true(
            module.make_error is redaction.make_error,
            f"{name}.make_error is a local copy rather than adapters.redaction.make_error; "
            "three copies of one rule is the shape the divergence grew in",
        )
        assert_true(
            module.make_ok is redaction.make_ok,
            f"{name}.make_ok is a local copy rather than adapters.redaction.make_ok",
        )
