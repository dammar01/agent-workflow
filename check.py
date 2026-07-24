import argparse
import json
import sys
import time

from config.settings import (
    DEFAULT_JOB_POLL_INTERVAL_SECONDS,
    DEFAULT_JOB_POLL_TIMEOUT_SECONDS,
    DEFAULT_PROBE_RECHECK_SECONDS,
)
from core.job_manager import JobManager


JOB_MANAGER = JobManager()
# Attach-path cadence for the stall checks. Fixed rather than per-project: check.py is
# handed a job id and nothing else, so there is no config to read before the first poll.
_PROBE_RECHECK_SECONDS = float(DEFAULT_PROBE_RECHECK_SECONDS)


def _status_payload(job_id: str) -> dict:
    # Routed through get_result, not the raw record: get_result is where liveness runs
    # and where a dead or over-budget worker gets reaped. Reading the record directly
    # meant an attached `--wait` polled a corpse forever, because nothing in this path
    # ever asked whether the worker was still alive.
    result = JOB_MANAGER.get_result(job_id)
    status = result.get("status") or "unknown"
    payload = {
        "ok": status == "completed",
        "job_id": job_id,
        "status": status,
        # not_found is terminal: there is no record and none is coming. Leaving it out
        # told a caller polling on `done` to keep waiting for a job that does not exist.
        "done": status in {"completed", "failed", "not_found"},
    }
    error_type = (result.get("meta") or {}).get("error_type")
    if error_type:
        payload["error_type"] = error_type
    if result.get("content") and status != "completed":
        payload["content"] = result["content"]
    return payload


def _reap_if_stalled(job_id: str, payload: dict) -> dict | None:
    """Run the same PID + fresh-session probe checks `await` runs, and reap on failure.

    Imported lazily: check.py is the attach path and must stay usable even when the
    heavier main module cannot be imported for an unrelated reason.
    """
    if payload.get("error_type") != "worker_stalled":
        return None
    job = JOB_MANAGER.get_job(job_id) or {}
    try:
        from main import check_stalled_job
    except Exception:
        return None
    reaped = check_stalled_job(job_id, job.get("work_dir"), job.get("model"))
    if reaped is None:
        return None
    return _status_payload(job_id)


def _result_payload(job_id: str):
    result = JOB_MANAGER.get_result(job_id)
    if result.get("status") == "completed":
        output = result.get("output") or {}
        return True, output.get("content") or ""

    return False, {
        "ok": False,
        "job_id": job_id,
        "status": result.get("status", "not_found"),
        "done": result.get("status") in {"completed", "failed", "not_found"},
        **({"content": result.get("content")} if result.get("content") else {}),
    }


def _wait_for_status(job_id: str, poll_interval: float, poll_timeout: int) -> dict:
    started_at = time.monotonic()
    interval = poll_interval if poll_interval > 0 else DEFAULT_JOB_POLL_INTERVAL_SECONDS
    last_probe_at: float | None = None

    while True:
        payload = _status_payload(job_id)
        if payload["status"] not in {"pending", "running"}:
            return payload

        now = time.monotonic()
        if last_probe_at is None or (now - last_probe_at) >= _PROBE_RECHECK_SECONDS:
            last_probe_at = now
            reaped = _reap_if_stalled(job_id, payload)
            if reaped is not None:
                return reaped

        if poll_timeout > 0 and (time.monotonic() - started_at) >= poll_timeout:
            return {
                "ok": False,
                "job_id": job_id,
                "status": payload["status"],
                "done": False,
                "timed_out": True,
            }

        time.sleep(interval)


def _wait_for_result(job_id: str, poll_interval: float, poll_timeout: int):
    started_at = time.monotonic()
    interval = poll_interval if poll_interval > 0 else DEFAULT_JOB_POLL_INTERVAL_SECONDS
    last_probe_at: float | None = None

    while True:
        ok, payload = _result_payload(job_id)
        if ok:
            return True, payload
        if payload["status"] not in {"pending", "running"}:
            return False, payload

        now = time.monotonic()
        if last_probe_at is None or (now - last_probe_at) >= _PROBE_RECHECK_SECONDS:
            last_probe_at = now
            if _reap_if_stalled(job_id, _status_payload(job_id)) is not None:
                return False, _status_payload(job_id)

        if poll_timeout > 0 and (time.monotonic() - started_at) >= poll_timeout:
            payload["timed_out"] = True
            return False, payload

        time.sleep(interval)


def _write_text(payload: str) -> None:
    try:
        sys.stdout.write(payload)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(payload.encode("utf-8", errors="replace"))


def _exit_code_for_status(status: str) -> int:
    if status == "completed":
        return 0
    if status == "failed":
        return 1
    if status in {"pending", "running"}:
        return 2
    if status == "not_found":
        return 3
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Check workflow job status or result")
    parser.add_argument("job_id")
    parser.add_argument("--result", action="store_true", help="return final cleaned output only when completed")
    parser.add_argument("--wait", action="store_true", help="poll internally until completion or timeout")
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_JOB_POLL_INTERVAL_SECONDS,
        help="seconds between job status polls while waiting",
    )
    parser.add_argument(
        "--poll-timeout",
        type=int,
        default=DEFAULT_JOB_POLL_TIMEOUT_SECONDS,
        help="max seconds to wait; 0 means wait forever",
    )
    args = parser.parse_args()

    if args.result:
        ok, payload = (
            _wait_for_result(args.job_id, args.poll_interval, args.poll_timeout)
            if args.wait
            else _result_payload(args.job_id)
        )
        if ok:
            _write_text(payload)
            return 0
        print(json.dumps(payload))
        return _exit_code_for_status(payload.get("status"))

    payload = (
        _wait_for_status(args.job_id, args.poll_interval, args.poll_timeout)
        if args.wait
        else _status_payload(args.job_id)
    )
    print(json.dumps(payload))
    return _exit_code_for_status(payload.get("status"))


if __name__ == "__main__":
    raise SystemExit(main())
