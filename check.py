import argparse
import json
import sys

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


def main() -> int:
    parser = argparse.ArgumentParser(description="Check workflow job status or result")
    parser.add_argument("job_id")
    parser.add_argument("--result", action="store_true", help="return final cleaned output only when completed")
    args = parser.parse_args()

    if args.result:
        ok, payload = _result_payload(args.job_id)
        if ok:
            sys.stdout.write(payload)
            return 0
        print(json.dumps(payload))
        status = payload.get("status")
        if status == "failed":
            return 1
        if status in {"pending", "running"}:
            return 2
        if status == "not_found":
            return 3
        return 1

    payload = _status_payload(args.job_id)
    print(json.dumps(payload))
    status = payload.get("status")
    if status == "completed":
        return 0
    if status == "failed":
        return 1
    if status in {"pending", "running"}:
        return 2
    if status == "not_found":
        return 3
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
