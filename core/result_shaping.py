"""Shaping a delegated result for its consumer: slimming, previewing, exit codes.

Pure transforms over the result dict — no process state and no singletons, with one
exception: the two slimming thresholds are read through the main module rather than
imported by value, because the harness lowers them at runtime to exercise the
ref_only path against a short fixture. Importing them here by value would freeze the
defaults, and the test would assert against knobs nothing reads.
"""

import os
import sys
from pathlib import Path

from config.settings import SLIM_CONTENT_ENV
from core.contract import validate_verification_contract, verify_exit_status
from utils.redact import redact_value


def _main():
    """The main module object, whichever way the process was started."""
    entry = sys.modules.get("__main__")
    if Path(getattr(entry, "__file__", "") or "").name == "main.py":
        return entry
    import main as main_module

    return main_module


def _finalize_verify_result(command: str, result: dict) -> dict:
    if command.strip().lower() != "verify" or not isinstance(result, dict):
        return result
    if not result.get("ok"):
        return result

    meta = result.setdefault("meta", {})
    if meta.get("mode") == "quick" or "quick_verify" in meta:
        return result

    assessment = validate_verification_contract(result.get("content") or "")
    meta["verdict"] = assessment["verdict"]
    meta["verify_contract"] = assessment
    warnings = assessment.get("warnings") or []
    if warnings:
        meta.setdefault("contract_warnings", []).extend(warnings)
    return result



_HEAVY_META_KEYS = frozenset(
    {"args", "stderr", "stderr_tail", "stdout", "cwd", "raw", "bootstrap"}
)


def _without_raw_args(value):
    if isinstance(value, dict):
        raw_args = value.get("args")
        clean = {
            key: _without_raw_args(child)
            for key, child in value.items()
            if key != "args"
        }
        if isinstance(raw_args, (list, tuple)):
            clean.setdefault("argv_count", len(raw_args))
            clean.setdefault("argv_chars", sum(len(str(arg)) for arg in raw_args))
        return clean
    if isinstance(value, list):
        return [_without_raw_args(child) for child in value]
    if isinstance(value, tuple):
        return tuple(_without_raw_args(child) for child in value)
    return value


def _as_ref_only(result: dict) -> dict:
    """Swap the evidence text for a preview plus the path it is already archived at.

    Refuses in the two cases where the trade stops paying:
      - no artifact on disk — the payload is then the ONLY copy, and trimming it destroys
        the evidence instead of relocating it;
      - content short enough that the preview reclaims nothing worth the loss.
    """
    content = result.get("content")
    if not isinstance(content, str) or len(content) <= _main().DEFAULT_SLIM_CONTENT_MIN_CHARS:
        return result
    ref = result.get("evidence_ref")
    artifact = str((ref or {}).get("artifact_path") or "") if isinstance(ref, dict) else ""
    if not artifact:
        return result
    meta = result.get("meta")
    return {
        **result,
        "content": (
            f"{content[:_main().DEFAULT_CONTENT_PREVIEW_CHARS].rstrip()}\n\n"
            f"[content truncated — full evidence at {artifact}]"
        ),
        "meta": {
            **(meta if isinstance(meta, dict) else {}),
            "content_mode": "ref_only",
            "content_full_chars": len(content),
        },
    }


def _slim_result(result: dict, slim_content: bool | None = None) -> dict:
    """Redact every payload and trim bulky success-only diagnostics.

    `slim_content` additionally drops the evidence text in favour of the artifact pointer
    (see _as_ref_only). Left to the AI_PROXY_SLIM_CONTENT environment variable when not
    passed; tests pass it directly so they need no environment of their own.
    """
    if slim_content is None:
        slim_content = os.getenv(SLIM_CONTENT_ENV, "") not in ("", "0", "false", "False")
    clean, redactions = redact_value(_without_raw_args(result))
    if not isinstance(clean, dict):
        return clean
    meta = clean.get("meta")
    if not isinstance(meta, dict):
        return clean
    if redactions:
        meta["boundary_redactions"] = redactions
        meta["boundary_redaction_count"] = sum(
            int(hit.get("count") or 0) for hit in redactions
        )
    if not clean.get("ok"):
        # An error message is never archived to an artifact, so there is no pointer that
        # could stand in for it. Failures keep their text whatever the flag says.
        return clean
    slim_meta = {k: v for k, v in meta.items() if k not in _HEAVY_META_KEYS}
    clean = {**clean, "meta": slim_meta}
    return _as_ref_only(clean) if slim_content else clean


def _verify_exit_code(
    command: str, result: dict, job_command: str | None = None
) -> int:
    """Return nonzero when a completed verification is not a clean pass."""
    effective_command = job_command if command in {"await", "result"} else command
    # `provider` refuses by writing nothing, and a caller that only checks the exit
    # status would read that refusal as a successful apply. Its own branch rather than a
    # general "ok:false -> 2" rule: every other command here has a settled exit contract
    # that a blanket change would rewrite.
    if effective_command == "provider":
        return 0 if isinstance(result, dict) and result.get("ok") else 2
    if effective_command != "verify":
        return 0
    verification = result
    if command == "result" and isinstance(result, dict):
        stored_output = result.get("output")
        if isinstance(stored_output, dict):
            verification = stored_output
    if not isinstance(verification, dict) or not verification.get("ok"):
        return 2
    if command == "verify" and verification.get("status") in {"pending", "running"}:
        return 0
    meta = verification.get("meta") or {}
    verdict = meta.get("verdict")
    assessment = meta.get("verify_contract")
    if verdict is None:
        assessment = validate_verification_contract(verification.get("content") or "")
        verdict = assessment["verdict"]
    return verify_exit_status(verdict, assessment)
