import argparse
import json
import sys
import time

from config.settings import DEFAULT_JOB_POLL_INTERVAL_SECONDS, DEFAULT_JOB_POLL_TIMEOUT_SECONDS
from core.job_manager import JobManager


JOB_MANAGER = JobManager()


def _status_payload(job_id: str) -> dict:
    job = JOB_MANAGER.get_job(job_id)
    if not job:
        return {"ok": False, "job_id": job_id, "status": "not_found", "done": True}

    status = job.get("status") or "unknown"
    return {
        "ok": status == "completed",
        "job_id": job_id,
        "status": status,
        "done": status in {"completed", "failed"},
    }


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

    while True:
        payload = _status_payload(job_id)
        if payload["status"] not in {"pending", "running"}:
            return payload

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

    while True:
        ok, payload = _result_payload(job_id)
        if ok:
            return True, payload
        if payload["status"] not in {"pending", "running"}:
            return False, payload

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
