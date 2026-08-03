import os
import json

from adapters.opencode_adapter import OpenCodeAdapter
from core.job_manager import JobManager
from core.executor import Executor
from core.prompt_builder import build_prompt
from core.workflow_runtime import ensure_workflow_workspace
from utils.parser import clean_opencode_output, extract_opencode_session_id
import check
import main
import tempfile
import threading
import time
from pathlib import Path


class FakeOpenCodeAdapter:
    def __init__(self) -> None:
        self.calls = []
        self.fail_next = False

    def run(self, prompt: str, session: dict, model: str | None = None, work_dir: str | None = None) -> dict:
        self.calls.append({"prompt": prompt, "session": dict(session), "model": model, "work_dir": work_dir})
        if self.fail_next:
            self.fail_next = False
            return {"ok": False, "content": "simulated opencode failure", "meta": {"simulated": True}}
        content = """
INFO  2026-05-09T12:10:24 +1ms service=session.prompt session.id=ses_test123 step=0 loop
> build · gpt-5.3-codex
[EVIDENCE]
findings:
- entry point at app/main.py
[DIGEST]
summary: OpenCode response.
INFO  2026-05-09T12:10:28 +0ms service=session.idle publishing
""".strip()
        return {
            "ok": True,
            "content": clean_opencode_output(content),
            "meta": {
                "simulated": True,
                "opencode_session_id": extract_opencode_session_id(content),
                "args": ["opencode", "run", prompt],
            },
        }


class RecordingOpenCodeAdapter(OpenCodeAdapter):
    def __init__(self) -> None:
        super().__init__(command="opencode")
        self.init_calls = []
        self.run_calls = []

    def init_session(
        self,
        model: str | None = None,
        work_dir: str | None = None,
        workflow_session_id: str | None = None,
    ) -> tuple[str | None, dict]:
        args = ["opencode", "run", "Initialize session. Reply READY."]
        if model:
            args.extend(["-m", model])
        args.extend(["--print-logs", "--log-level", "INFO"])
        self.init_calls.append(
            {"args": args, "model": model, "work_dir": work_dir, "workflow_session_id": workflow_session_id}
        )
        return "ses_boot123", {
            "args": args,
            "opencode_session_id": "ses_boot123",
            "returncode": 0,
        }

    def _run_args(self, args: list[str], work_dir: str | None = None) -> dict:
        self.run_calls.append({"args": args, "work_dir": work_dir})
        return {
            "ok": True,
            "content": "OpenCode response",
            "meta": {"args": args, "opencode_session_id": None, "cwd": work_dir},
        }


class FakeJobProcess:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _test_facts_concurrency() -> None:
    """Concurrent ingest must not lose updates and must leave valid JSONL.

    N threads each ingest one distinct, file:line-anchored fact into the same store. The
    _FactLock read-modify-write serialises them, so all N must survive (an unlocked store
    would let the last writer clobber earlier additions), and every line must still parse.
    """
    import json as _json
    import shutil as _shutil

    from core import fact_store
    from core.workflow_runtime import workflow_paths

    root = Path(tempfile.mkdtemp(prefix="facts-conc-"))
    try:
        wf = workflow_paths(root)["workflow_dir"]
        wf.mkdir(parents=True, exist_ok=True)
        src = root / "src.py"
        src.write_text(
            "\n".join(f"line_{i} = {i}" for i in range(20)) + "\n", encoding="utf-8"
        )

        n = 8
        errors: list[Exception] = []

        def worker(i: int) -> None:
            try:
                fact_store.ingest(
                    root,
                    f"durable_facts:\n- [config] fact number {i} distinct value [src.py:{i + 1}]\n",
                    f"sess-{i}",
                )
            except Exception as exc:  # noqa: BLE001 - surfaced via errors list
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert_true(not errors, f"concurrent ingest raised: {errors}")
        facts = fact_store._load_facts(root)
        assert_true(
            len(facts) == n,
            f"concurrent ingest lost updates: expected {n} facts, got {len(facts)}",
        )
        for line in (wf / "facts.jsonl").read_text(encoding="utf-8").splitlines():
            if line.strip():
                _json.loads(line)  # raises if the atomic rewrite ever left a torn line
    finally:
        _shutil.rmtree(root, ignore_errors=True)


def _test_evidence_reuse() -> None:
    """Reuse must read immutable, hash-checked artifacts and preserve concurrent rows."""
    import shutil as _shutil

    from core import evidence_store
    from core.workflow_runtime import workflow_paths, write_response_snapshot

    root = Path(tempfile.mkdtemp(prefix="evidence-reuse-"))
    try:
        (root / "src.py").write_text(
            "\n".join(f"anchor_{i} = {i}" for i in range(1, 51)) + "\n",
            encoding="utf-8",
        )
        session_id = "evidence-session"
        first = "FIRST [src.py:1]"
        evidence_context = {"model": "provider/model-a", "agent": "plan"}
        write_response_snapshot(root, first, "run-a", session_id)
        paths = workflow_paths(root, session_id)
        first_artifact = paths["logs_dir"] / "run-a" / "output.raw.md"
        evidence_store.record(
            root,
            "analyze",
            "same query",
            session_id,
            {"summary": "first"},
            first_artifact,
            first,
            context=evidence_context,
        )

        write_response_snapshot(root, "UNRELATED SECOND", "run-b", session_id)
        hit = evidence_store.find_fresh(
            root, "analyze", "same query", context=evidence_context
        )
        assert_true(hit is not None, "fresh immutable evidence must be reusable")
        assert_true(
            evidence_store.read_artifact(root, hit) == first,
            "reuse must return the indexed run, never mutable response.last.md",
        )
        assert_true(
            evidence_store.find_fresh(
                root,
                "analyze",
                "same query",
                context={"model": "provider/model-b", "agent": "plan"},
            )
            is None
            and evidence_store.find_fresh(
                root, "analyze", "Same query", context=evidence_context
            )
            is None,
            "evidence reuse must distinguish effective model context and task casing",
        )

        errors: list[Exception] = []

        def record_one(index: int) -> None:
            try:
                content = f"claim {index} [src.py:1]"
                prompt_id = f"run-{index + 10}"
                write_response_snapshot(root, content, prompt_id, f"session-{index}")
                artifact = (
                    workflow_paths(root, f"session-{index}")["logs_dir"]
                    / prompt_id
                    / "output.raw.md"
                )
                evidence_store.record(
                    root,
                    "explore",
                    f"query {index}",
                    f"session-{index}",
                    {"summary": str(index)},
                    artifact,
                    content,
                )
            except Exception as exc:  # noqa: BLE001 - surfaced below
                errors.append(exc)

        threads = [threading.Thread(target=record_one, args=(i,)) for i in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert_true(not errors, f"concurrent evidence record failed: {errors}")
        index_path = workflow_paths(root)["workflow_dir"] / "evidence.jsonl"
        rows = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines()]
        assert_true(len(rows) == 7, f"concurrent evidence writes lost rows: {len(rows)}")

        outside = root.parent / f"{root.name}-outside.py"
        outside.write_text("outside_secret = 1\n", encoding="utf-8")
        try:
            traversal_content = f"claim [../{outside.name}:1]"
            write_response_snapshot(
                root, traversal_content, "run-traversal", session_id
            )
            traversal_entry = evidence_store.record(
                root,
                "analyze",
                "outside anchor",
                session_id,
                {"summary": "outside"},
                paths["logs_dir"] / "run-traversal" / "output.raw.md",
                traversal_content,
            )
            assert_true(
                traversal_entry.get("anchors_complete") is False
                and evidence_store.find_fresh(root, "analyze", "outside anchor") is None,
                "an anchor outside the project must never be certifiable or reusable",
            )
        finally:
            outside.unlink(missing_ok=True)

        many = "\n".join(f"claim [src.py:{line}]" for line in range(1, 42))
        write_response_snapshot(root, many, "run-many", session_id)
        many_artifact = paths["logs_dir"] / "run-many" / "output.raw.md"
        many_entry = evidence_store.record(
            root,
            "analyze",
            "many anchors",
            session_id,
            {"summary": "many"},
            many_artifact,
            many,
        )
        assert_true(
            many_entry.get("anchors_complete") is False
            and evidence_store.find_fresh(root, "analyze", "many anchors") is None,
            "evidence with more anchors than the validation cap must never be reused",
        )

        first_artifact.write_text("tampered", encoding="utf-8")
        assert_true(
            evidence_store.find_fresh(
                root, "analyze", "same query", context=evidence_context
            )
            is None,
            "artifact hash mismatch must invalidate reuse",
        )
    finally:
        _shutil.rmtree(root, ignore_errors=True)


def _test_redaction_boundary() -> None:
    """Secrets and raw prompt argv must not survive any adapter result path."""
    secret = "sk-" + "A" * 36
    adapter = OpenCodeAdapter(command="opencode", timeout_seconds=5)

    def outcome(*, returncode: int, timed_out: bool) -> dict:
        return {
            "stdout": "safe response" if not timed_out else "",
            "stderr": f"provider diagnostic {secret}",
            "returncode": returncode,
            "timed_out": timed_out,
            "duration_seconds": 0.1,
            "idle_seconds": 0.0,
            "kill": None,
        }

    for returncode, timed_out in ((0, False), (1, False), (1, True)):
        adapter._popen_capture = (  # type: ignore[method-assign]
            lambda *args, rc=returncode, to=timed_out, **kwargs: outcome(
                returncode=rc, timed_out=to
            )
        )
        result = adapter._run_args(["opencode", "run", f"prompt {secret}"])
        serialized = json.dumps(result)
        assert_true(secret not in serialized, f"secret leaked from adapter result: {result}")
        assert_true(
            "\"args\"" not in serialized and "prompt " not in serialized,
            f"raw prompt argv leaked from adapter metadata: {result}",
        )
        assert_true(
            (result.get("meta") or {}).get("redaction_count", 0) > 0,
            f"redaction must remain auditable on every result path: {result}",
        )

    existing = RecordingOpenCodeAdapter()
    callback_calls: list[str] = []
    existing.on_session_created = callback_calls.append
    existing_result = existing.run(
        "prompt", {"session_id": "workflow", "opencode_session_id": "ses_existing"}
    )
    assert_true(
        existing_result["ok"] and not callback_calls,
        "an existing OpenCode session must not invoke the new-session persistence callback",
    )

    bootstrap = OpenCodeAdapter(command="opencode", timeout_seconds=5)
    bootstrap._popen_capture = lambda *args, **kwargs: outcome(  # type: ignore[method-assign]
        returncode=1, timed_out=False
    )
    _session_id, bootstrap_meta = bootstrap.init_session()
    assert_true(
        secret not in json.dumps(bootstrap_meta)
        and bool(bootstrap_meta.get("stderr_tail")),
        f"bootstrap failures must retain sanitized diagnostics: {bootstrap_meta}",
    )


def _test_quick_verify_gaps() -> None:
    """Unavailable, deleted, and name-check failures cannot produce a pass verdict."""
    import shutil as _shutil

    from core import quick_verify

    root = Path(tempfile.mkdtemp(prefix="quick-verify-"))
    original_discover = quick_verify._discover_changed_files
    original_which = quick_verify.shutil.which
    original_run = quick_verify._run
    try:
        (root / "sample.php").write_text("<?php echo 1;\n", encoding="utf-8")
        quick_verify._discover_changed_files = lambda _root: (["sample.php"], [])
        quick_verify.shutil.which = lambda _tool: None
        result = quick_verify.run(root, "verify-gaps")
        assert_true(
            result.get("meta", {}).get("verdict") == "incomplete",
            f"missing toolchain must be incomplete: {result}",
        )

        quick_verify._discover_changed_files = lambda _root: (["deleted.py"], [])
        result = quick_verify.run(root, "verify-deleted")
        assert_true(
            result.get("meta", {}).get("verdict") == "incomplete",
            f"deleted changed files must be incomplete: {result}",
        )

        (root / "names.py").write_text("print(missing_name)\n", encoding="utf-8")
        quick_verify._discover_changed_files = lambda _root: (["names.py"], [])
        quick_verify.shutil.which = lambda _tool: None
        result = quick_verify.run(root, "verify-name-tool-missing")
        assert_true(
            result.get("meta", {}).get("verdict") == "incomplete",
            f"missing Python name checker must be incomplete: {result}",
        )

        quick_verify.shutil.which = lambda _tool: "pyflakes"
        quick_verify._run = lambda _argv, _root: (1, "undefined name 'missing_name'")
        result = quick_verify.run(root, "verify-names")
        assert_true(
            result.get("meta", {}).get("verdict") == "fail",
            f"name findings must fail verification: {result}",
        )

        quick_verify._discover_changed_files = lambda _root: (
            [],
            ["git diff --name-only --: not a git repository"],
        )
        result = quick_verify.run(root, "verify-discovery-error")
        assert_true(
            result.get("meta", {}).get("verdict") == "incomplete"
            and result.get("meta", {}).get("discovery_errors"),
            f"change-discovery failures must be incomplete: {result}",
        )

        quick_verify._discover_changed_files = original_discover
        quick_verify._run = original_run
        quick_verify.shutil.which = original_which
        unborn = Path(tempfile.mkdtemp(prefix="quick-unborn-"))
        try:
            initialized = main.subprocess.run(
                ["git", "init", "-q", str(unborn)], capture_output=True, text=True
            )
            assert_true(initialized.returncode == 0, "quick verify git init failed")
            (unborn / "staged.json").write_text('{"ok": true}\n', encoding="utf-8")
            staged = main.subprocess.run(
                ["git", "add", "staged.json"], cwd=unborn, capture_output=True, text=True
            )
            assert_true(staged.returncode == 0, f"quick verify git add failed: {staged.stderr}")
            result = quick_verify.run(unborn, "verify-unborn-staged")
            assert_true(
                result.get("meta", {}).get("verdict") == "pass"
                and "staged.json" in result.get("meta", {}).get("quick_verify", {}).get("passed", []),
                f"unborn staged files must be verified: {result}",
            )
        finally:
            _shutil.rmtree(unborn, ignore_errors=True)
    finally:
        quick_verify._discover_changed_files = original_discover
        quick_verify.shutil.which = original_which
        quick_verify._run = original_run
        _shutil.rmtree(root, ignore_errors=True)


def _test_submit_admission() -> None:
    """Initial dispatch and global capacity must admit exactly one worker."""
    import shutil as _shutil

    root = Path(tempfile.mkdtemp(prefix="submit-admission-"))
    original_manager = main.JOB_MANAGER
    original_check_manager = check.JOB_MANAGER
    original_spawn = main._spawn_worker
    manager = JobManager(root / "jobs", max_global_workers=1)
    main.JOB_MANAGER = manager
    check.JOB_MANAGER = manager
    calls: list[str] = []
    entered = threading.Event()
    release = threading.Event()

    def blocking_spawn(job_id: str, work_dir: str | None = None) -> dict:
        calls.append(job_id)
        entered.set()
        release.wait(timeout=5)
        return {"ok": True, "content": "worker started", "meta": {"pid": os.getpid()}}

    try:
        assert_true(
            manager.request_hash("analyze", "same", "hash-session", str(root), True, "m1")
            != manager.request_hash("analyze", "same", "hash-session", str(root), False, "m1")
            and manager.request_hash(
                "analyze", "same", "hash-session", str(root), True, "m1"
            )
            != manager.request_hash(
                "analyze", "same", "hash-session", str(root), True, "m2"
            ),
            "job idempotency must distinguish fresh-session reuse policy and model override",
        )
        main._spawn_worker = blocking_spawn
        first_result: list[dict] = []
        first = threading.Thread(
            target=lambda: first_result.append(
                main.submit("analyze", "same", "same-session", str(root), None)
            )
        )
        first.start()
        assert_true(entered.wait(timeout=5), "first submit never reached worker spawn")
        attached = main.submit("analyze", "same", "same-session", str(root), None)
        assert_true(
            attached.get("ok")
            and attached.get("meta", {}).get("reused")
            and not attached.get("meta", {}).get("recovery")
            and len(calls) == 1,
            f"a fresh pending dispatch must attach, never recover/spawn twice: {attached}",
        )
        release.set()
        first.join(timeout=5)
        assert_true(first_result and first_result[0].get("ok"), "first submit failed")
        manager.fail_job(first_result[0]["job_id"], "cleanup")

        calls.clear()
        main._spawn_worker = lambda job_id, work_dir=None: (
            calls.append(job_id)
            or {"ok": True, "content": "worker started", "meta": {"pid": os.getpid()}}
        )
        results: list[dict] = []
        start = threading.Barrier(3)

        def submit_distinct(index: int) -> None:
            start.wait()
            results.append(
                main.submit(
                    "analyze", f"task-{index}", f"capacity-{index}", str(root), None
                )
            )

        threads = [threading.Thread(target=submit_distinct, args=(i,)) for i in range(2)]
        for thread in threads:
            thread.start()
        start.wait()
        for thread in threads:
            thread.join(timeout=5)
        assert_true(
            sum(bool(result.get("ok")) for result in results) == 1
            and sum(
                result.get("meta", {}).get("error_type") == "worker_capacity"
                for result in results
            )
            == 1
            and len(calls) == 1,
            f"capacity=1 must admit one submit and reject one: {results}, calls={calls}",
        )
        for result in results:
            if result.get("ok"):
                manager.fail_job(result["job_id"], "cleanup")

        live = manager.create_job("analyze", "live", "live-capacity", str(root), None)
        manager.set_worker_pid(live["job_id"], os.getpid())
        manager.mark_running(live["job_id"])
        dead = manager.create_job("analyze", "dead", "dead-capacity", str(root), None)
        manager.set_worker_pid(dead["job_id"], 999999999)
        manager.mark_running(dead["job_id"])
        blocked_recovery = main.submit(
            "analyze", "dead", "dead-capacity", str(root), None
        )
        assert_true(
            not blocked_recovery.get("ok")
            and blocked_recovery.get("meta", {}).get("error_type")
            == "worker_capacity"
            and (manager.get_job(dead["job_id"]) or {}).get("recovery_attempt") == 0,
            f"recovery must obey the global capacity limit: {blocked_recovery}",
        )
        manager.fail_job(live["job_id"], "cleanup")
        manager.fail_job(dead["job_id"], "cleanup")

        startup_crash = manager.create_job(
            "analyze", "startup-crash", "startup-crash-session", str(root), None
        )
        manager.set_worker_pid(startup_crash["job_id"], 999999999)
        calls.clear()
        recovered_startup = main.submit(
            "analyze", "startup-crash", "startup-crash-session", str(root), None
        )
        recovered_record = manager.get_job(startup_crash["job_id"]) or {}
        assert_true(
            recovered_startup.get("ok")
            and recovered_startup.get("meta", {}).get("recovery")
            and recovered_record.get("recovery_attempt") == 1
            and calls == [startup_crash["job_id"]],
            f"a pending dead worker must not consume the slot needed for its recovery: {recovered_startup}",
        )
        manager.fail_job(startup_crash["job_id"], "cleanup")

        reused_worker = manager.create_job(
            "analyze", "reused-worker", "reused-worker-session", str(root), None
        )
        manager.set_worker_pid(reused_worker["job_id"], os.getpid())
        manager.mark_running(reused_worker["job_id"])
        reused_worker_record = manager.get_job(reused_worker["job_id"])
        reused_worker_record["worker_identity"] = "different-generation"
        manager._save(reused_worker_record)
        calls.clear()
        recovered_reused_worker = main.submit(
            "analyze", "reused-worker", "reused-worker-session", str(root), None
        )
        assert_true(
            recovered_reused_worker.get("ok")
            and recovered_reused_worker.get("meta", {}).get("recovery")
            and calls == [reused_worker["job_id"]],
            f"a reused worker PID must reach recovery instead of attaching forever: {recovered_reused_worker}",
        )
        manager.fail_job(reused_worker["job_id"], "cleanup")

        owned = manager.create_job(
            "analyze", "slow-spawn", "slow-owner-session", str(root), None
        )
        owned_record = manager.get_job(owned["job_id"])
        owned_record["created_at"] = "2020-01-01T00:00:00+00:00"
        manager._save(owned_record)
        calls.clear()
        still_owned = main.submit(
            "analyze", "slow-spawn", "slow-owner-session", str(root), None
        )
        assert_true(
            still_owned.get("ok")
            and still_owned.get("meta", {}).get("reused")
            and not calls
            and (manager.get_job(owned["job_id"]) or {}).get("recovery_attempt") == 0,
            f"a live submit owner must protect a slow pre-spawn reservation: {still_owned}",
        )
        manager.fail_job(owned["job_id"], "cleanup")

        reused_owner = manager.create_job(
            "analyze", "reused-owner", "reused-owner-session", str(root), None
        )
        reused_record = manager.get_job(reused_owner["job_id"])
        reused_record["reservation_owner_pid"] = os.getpid()
        reused_record["reservation_owner_create_time"] = None
        reused_record["reservation_owner_identity"] = "different-generation"
        reused_record["created_at"] = "2020-01-01T00:00:00+00:00"
        manager._save(reused_record)
        reused_claim = manager.claim_recovery(
            reused_owner["job_id"], stale_after_seconds=0
        )
        manager.release_recovery_claim(reused_owner["job_id"])
        assert_true(
            reused_claim.get("action") == "recover",
            f"a reused reservation-owner PID must not block recovery: {reused_claim}",
        )
        manager.fail_job(reused_owner["job_id"], "cleanup")

        legacy_owner = manager.create_job(
            "analyze", "legacy-owner", "legacy-owner-session", str(root), None
        )
        legacy_record = manager.get_job(legacy_owner["job_id"])
        legacy_record["reservation_owner_pid"] = os.getpid()
        legacy_record["reservation_owner_create_time"] = None
        legacy_record.pop("reservation_owner_identity", None)
        legacy_record["created_at"] = "2020-01-01T00:00:00+00:00"
        manager._save(legacy_record)
        legacy_claim = manager.claim_recovery(
            legacy_owner["job_id"], stale_after_seconds=0
        )
        manager.release_recovery_claim(legacy_owner["job_id"])
        assert_true(
            legacy_claim.get("action") == "recover",
            f"an unverifiable legacy reservation must become reclaimable after grace: {legacy_claim}",
        )
        manager.fail_job(legacy_owner["job_id"], "cleanup")

        orphan = manager.create_job(
            "analyze", "orphan", "orphan-session", str(root), None
        )
        orphan_record = manager.get_job(orphan["job_id"])
        orphan_record["reservation_owner_pid"] = 999999999
        orphan_record["reservation_owner_create_time"] = None
        manager._save(orphan_record)
        original_claim = manager.claim_recovery
        claim_calls = 0

        def staged_claim(job_id: str, max_attempts: int = 1, stale_after_seconds: float = 30.0):
            nonlocal claim_calls
            claim_calls += 1
            effective_stale = 999.0 if claim_calls == 1 else 0.0
            return original_claim(job_id, max_attempts, effective_stale)

        manager.claim_recovery = staged_claim  # type: ignore[method-assign]

        def complete_spawn(job_id: str, work_dir: str | None = None) -> dict:
            calls.append(job_id)
            manager.complete_job(
                job_id,
                {"ok": True, "content": "recovered orphan", "meta": {}},
            )
            return {"ok": True, "content": "worker started", "meta": {"pid": os.getpid()}}

        calls.clear()
        main._spawn_worker = complete_spawn
        recovered = main.await_job(
            "analyze",
            "orphan",
            "orphan-session",
            str(root),
            None,
            poll_interval=0.01,
            poll_timeout=1,
        )
        assert_true(
            recovered.get("ok")
            and recovered.get("content") == "recovered orphan"
            and claim_calls >= 2
            and calls == [orphan["job_id"]]
            and (manager.get_job(orphan["job_id"]) or {}).get("recovery_attempt") == 0,
            f"await must reclaim an orphaned pre-spawn reservation after grace: {recovered}",
        )
        manager.claim_recovery = original_claim  # type: ignore[method-assign]

        attach_orphan = manager.create_job(
            "analyze", "attach-orphan", "attach-orphan-session", str(root), None
        )
        from datetime import datetime, timedelta, timezone

        stale = manager.get_job(attach_orphan["job_id"])
        stale["reservation_owner_pid"] = 999999999
        stale["reservation_owner_create_time"] = None
        stale["created_at"] = (
            datetime.now(timezone.utc) - timedelta(seconds=31)
        ).isoformat()
        manager._save(stale)
        calls.clear()
        attached_status = check._wait_for_status(
            attach_orphan["job_id"], poll_interval=0.01, poll_timeout=1
        )
        assert_true(
            attached_status.get("status") == "completed"
            and calls == [attach_orphan["job_id"]],
            f"check --wait must reclaim an orphaned pre-spawn reservation: {attached_status}",
        )
    finally:
        release.set()
        main._spawn_worker = original_spawn
        main.JOB_MANAGER = original_manager
        check.JOB_MANAGER = original_check_manager
        _shutil.rmtree(root, ignore_errors=True)


def _test_workspace_release_guards() -> None:
    """Runtime generations, upgrade corruption, and unborn staged diffs fail safely."""
    import shutil as _shutil

    from core.workflow_runtime import (
        acquire_runtime_lock,
        release_runtime_lock,
        run_sweep,
        upgrade_workflow_workspace,
        workflow_paths,
        write_evidence_sidecars,
        write_prompt_handoff,
    )

    root = Path(tempfile.mkdtemp(prefix="workspace-release-"))
    try:
        subprocess_result = main.subprocess.run(
            ["git", "init", "-q", str(root)], capture_output=True, text=True
        )
        assert_true(subprocess_result.returncode == 0, "workspace fixture git init failed")
        ensure_workflow_workspace(root, os.getenv("AGENT_PATH"))
        paths = workflow_paths(root, "lock-session")
        paths["runtime_dir"].mkdir(parents=True, exist_ok=True)

        old_lock = acquire_runtime_lock(paths["lock"], "analyze", "lock-session")
        new_token = "new-generation-token"
        paths["lock"].write_text(
            json.dumps(
                {
                    "command": "analyze",
                    "session_id": "lock-session",
                    "token": new_token,
                    "created_at": "2026-08-02T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        release_runtime_lock(
            paths["lock"], "lock-session", old_lock.get("token")
        )
        assert_true(
            paths["lock"].exists(),
            "a late runtime finalizer must not remove a newer lock generation",
        )
        release_runtime_lock(paths["lock"], "lock-session", new_token)

        live_owner = acquire_runtime_lock(paths["lock"], "analyze", "lock-session")
        live_payload = json.loads(paths["lock"].read_text(encoding="utf-8"))
        live_payload["created_at"] = "2020-01-01T00:00:00+00:00"
        paths["lock"].write_text(json.dumps(live_payload), encoding="utf-8")
        blocked_live_owner = acquire_runtime_lock(
            paths["lock"], "analyze", "lock-session"
        )
        assert_true(
            live_owner.get("ok") and not blocked_live_owner.get("ok"),
            "a living lock owner must remain active after the legacy TTL",
        )
        release_runtime_lock(paths["lock"], "lock-session", live_owner.get("token"))

        reused_owner = acquire_runtime_lock(paths["lock"], "analyze", "lock-session")
        reused_payload = json.loads(paths["lock"].read_text(encoding="utf-8"))
        reused_payload["process_identity"] = "different-process-generation"
        paths["lock"].write_text(json.dumps(reused_payload), encoding="utf-8")
        replacement = acquire_runtime_lock(paths["lock"], "analyze", "lock-session")
        assert_true(
            reused_owner.get("ok") and replacement.get("ok"),
            "a live but identity-mismatched PID must be treated as reused",
        )
        release_runtime_lock(paths["lock"], "lock-session", replacement.get("token"))

        legacy_owner = acquire_runtime_lock(paths["lock"], "analyze", "lock-session")
        legacy_payload = json.loads(paths["lock"].read_text(encoding="utf-8"))
        legacy_payload.pop("process_identity", None)
        legacy_payload["pid_create_time"] = None
        legacy_payload["created_at"] = "2020-01-01T00:00:00+00:00"
        paths["lock"].write_text(json.dumps(legacy_payload), encoding="utf-8")
        legacy_replacement = acquire_runtime_lock(
            paths["lock"], "analyze", "lock-session"
        )
        assert_true(
            legacy_owner.get("ok") and legacy_replacement.get("ok"),
            "legacy POSIX locks without process identity must remain TTL-recoverable",
        )
        release_runtime_lock(
            paths["lock"], "lock-session", legacy_replacement.get("token")
        )

        contention_results: list[dict] = []
        contention_guard = threading.Lock()
        contention_start = threading.Barrier(9)
        release_owner = threading.Event()

        def contend() -> None:
            contention_start.wait()
            claim = acquire_runtime_lock(paths["lock"], "analyze", "lock-session")
            with contention_guard:
                contention_results.append(claim)
            if claim.get("ok"):
                release_owner.wait(timeout=5)
                release_runtime_lock(
                    paths["lock"], "lock-session", claim.get("token")
                )

        contenders = [threading.Thread(target=contend) for _ in range(8)]
        for contender in contenders:
            contender.start()
        contention_start.wait()
        deadline = time.time() + 5
        while len(contention_results) < len(contenders) and time.time() < deadline:
            time.sleep(0.01)
        assert_true(
            sum(bool(result.get("ok")) for result in contention_results) == 1,
            f"runtime lock contention must produce exactly one owner: {contention_results}",
        )
        release_owner.set()
        for contender in contenders:
            contender.join(timeout=5)

        unsafe_a = workflow_paths(root, "a/b")["session_dir"]
        unsafe_b = workflow_paths(root, "a?b")["session_dir"]
        dot_session = workflow_paths(root, "..")["session_dir"]
        sessions_root = workflow_paths(root)["workflow_dir"] / "sessions"
        assert_true(
            unsafe_a != unsafe_b
            and dot_session.resolve().parent == sessions_root.resolve(),
            "workflow session paths must resist traversal and sanitizer collisions",
        )

        write_evidence_sidecars(
            root,
            "sidecar-lock",
            {"files": ["owner.py"]},
            ["owner fact"],
        )
        sidecar_paths = workflow_paths(root, "sidecar-lock")
        before_leads = sidecar_paths["leads"].read_text(encoding="utf-8")
        sidecar_owner = acquire_runtime_lock(
            sidecar_paths["lock"], "analyze", "sidecar-lock"
        )
        blocked = Executor(opencode=FakeOpenCodeAdapter()).execute(
            "analyze",
            "different task",
            {"session_id": "sidecar-lock", "opencode_session_id": None},
            str(root),
        )
        assert_true(
            not blocked.get("ok")
            and blocked.get("meta", {}).get("error_type") == "runtime_lock"
            and sidecar_paths["leads"].read_text(encoding="utf-8") == before_leads,
            f"a denied caller must not mutate the active owner's sidecars: {blocked}",
        )
        release_runtime_lock(
            sidecar_paths["lock"], "sidecar-lock", sidecar_owner.get("token")
        )

        first = write_prompt_handoff(root, "analyze", "lock-session", "one")
        release_runtime_lock(
            first["paths"]["lock"],
            "lock-session",
            first["meta"]["lock_token"],
        )
        second = write_prompt_handoff(root, "analyze", "lock-session", "two")
        release_runtime_lock(
            second["paths"]["lock"],
            "lock-session",
            second["meta"]["lock_token"],
        )
        assert_true(
            first["meta"]["prompt_id"] != second["meta"]["prompt_id"],
            "same-second prompt archives must remain immutable and unique",
        )

        config_path = workflow_paths(root)["config"]
        config_path.write_text("{not-json", encoding="utf-8")
        try:
            upgrade_workflow_workspace(root, os.getenv("AGENT_PATH"))
        except ValueError:
            pass
        else:
            raise AssertionError("upgrade must refuse corrupt JSON instead of overwriting it")
        assert_true(
            config_path.read_text(encoding="utf-8") == "{not-json",
            "refused upgrade must preserve corrupt config for manual recovery",
        )
        config_path.write_text(json.dumps({"version": "3.4.0", "runtime": "bad"}), encoding="utf-8")
        try:
            upgrade_workflow_workspace(root, os.getenv("AGENT_PATH"))
        except ValueError:
            pass
        else:
            raise AssertionError("upgrade must reject a non-object runtime section")

        root2 = Path(tempfile.mkdtemp(prefix="sweep-unborn-"))
        try:
            init = main.subprocess.run(
                ["git", "init", "-q", str(root2)], capture_output=True, text=True
            )
            assert_true(init.returncode == 0, "unborn sweep fixture git init failed")
            ensure_workflow_workspace(root2, os.getenv("AGENT_PATH"))
            (root2 / "staged.py").write_text("value = 1\n", encoding="utf-8")
            added = main.subprocess.run(
                ["git", "add", "staged.py"], cwd=root2, capture_output=True, text=True
            )
            assert_true(added.returncode == 0, f"git add failed: {added.stderr}")
            swept = run_sweep(root2, "unborn")
            assert_true(
                swept.get("ok")
                and "staged.py" in swept.get("meta", {}).get("changed_files", []),
                f"sweep must include staged files before the first commit: {swept}",
            )
        finally:
            _shutil.rmtree(root2, ignore_errors=True)

        not_repo = Path(tempfile.mkdtemp(prefix="sweep-not-repo-"))
        try:
            failed = run_sweep(not_repo, "bad-git")
            assert_true(
                not failed.get("ok")
                and failed.get("meta", {}).get("error_type") == "sweep_git_error"
                and failed.get("meta", {}).get("next_action"),
                f"Git failures must use the structured sweep contract: {failed}",
            )
        finally:
            _shutil.rmtree(not_repo, ignore_errors=True)
    finally:
        _shutil.rmtree(root, ignore_errors=True)


def _test_project_session_isolation() -> None:
    """Default and provider sessions must not cross project or sanitized-ID boundaries."""
    import shutil as _shutil

    from config import settings

    root = Path(tempfile.mkdtemp(prefix="session-isolation-"))
    project_a = root / "a"
    project_b = root / "b"
    project_a.mkdir()
    project_b.mkdir()
    initialized = main.subprocess.run(
        ["git", "init", "-q", str(project_a)], capture_output=True, text=True
    )
    assert_true(initialized.returncode == 0, "session isolation git init failed")
    project_a_subdir = project_a / "src"
    project_a_subdir.mkdir()
    original_cache = settings.CACHE_FILE
    original_manager = main.SESSION_MANAGER
    original_executor = main.EXECUTOR
    try:
        settings.CACHE_FILE = root / "cache.json"
        session_a = main.resolve_session_id("default", project_root=project_a)
        session_a_again = main.resolve_session_id("default", project_root=project_a)
        session_a_subdir = main.resolve_session_id(
            "default", project_root=project_a_subdir
        )
        session_b = main.resolve_session_id("default", project_root=project_b)
        fresh_explicit = main.resolve_session_id(
            "explicit-session", fresh=True, project_root=project_a
        )
        assert_true(
            session_a == session_a_again == session_a_subdir
            and session_a != session_b
            and fresh_explicit != "explicit-session"
            and main.resolve_session_id("default", project_root=project_a) == session_a,
            "session cache must use canonical project root and fresh must replace explicit IDs",
        )

        main.SESSION_MANAGER = main._DEFAULT_SESSION_MANAGER
        manager_a = main._session_manager_for(project_a)
        manager_b = main._session_manager_for(project_b)
        logical_id = "same/session"
        storage_id = main._session_storage_id(logical_id)
        provider_a = manager_a.load_or_create(storage_id)
        manager_a.update_opencode_session_id(provider_a, "provider-project-a")
        provider_b = manager_b.load_or_create(storage_id)
        assert_true(
            manager_a.session_dir != manager_b.session_dir
            and provider_b.get("opencode_session_id") is None,
            "provider session IDs must not leak across project roots",
        )
        unsafe_ids = [
            "a/b",
            "a?b",
            "..",
            "foo",
            "Foo",
            "foo.",
            "con",
            "con.txt",
            "NUL",
        ]
        safe_ids = [main._session_storage_id(value) for value in unsafe_ids]
        encoded_id = main._session_storage_id("a/b")
        assert_true(
            len(set(safe_ids)) == len(safe_ids)
            and all(
                value not in {"", ".", "..", "con", "con.txt", "nul"}
                for value in safe_ids
            )
            and main._session_storage_id(encoded_id) != encoded_id,
            "sanitized logical session IDs must remain collision-resistant",
        )

        safe_manager = JobManager(root / "safe-jobs")
        assert_true(
            len({safe_manager._safe(value) for value in unsafe_ids}) == len(unsafe_ids),
            "job/session lock filenames must remain collision-resistant",
        )

        ensure_workflow_workspace(project_a, os.getenv("AGENT_PATH"))
        entered = threading.Event()
        release = threading.Event()

        class BlockingAdapter(FakeOpenCodeAdapter):
            def run(self, *args, **kwargs):
                entered.set()
                release.wait(timeout=5)
                return super().run(*args, **kwargs)

        main.EXECUTOR = Executor(opencode=BlockingAdapter())
        first_result: list[dict] = []
        first = threading.Thread(
            target=lambda: first_result.append(
                main.run(
                    "analyze",
                    "trace provider session isolation",
                    "provider-race-session",
                    str(project_a),
                )
            )
        )
        first.start()
        assert_true(entered.wait(timeout=5), "provider race fixture did not enter adapter")
        blocked = main.run(
            "analyze",
            "competing provider session call",
            "provider-race-session",
            str(project_a),
        )
        release.set()
        first.join(timeout=5)
        persisted = manager_a.load_or_create("provider-race-session")
        assert_true(
            first_result
            and first_result[0].get("ok")
            and blocked.get("meta", {}).get("error_type") == "runtime_lock"
            and persisted.get("opencode_session_id") == "ses_test123"
            and len((persisted.get("history") or {}).get("runs") or []) == 1,
            f"provider session load/update/history must remain inside the runtime lock: {blocked}",
        )
    finally:
        if "release" in locals():
            release.set()
        settings.CACHE_FILE = original_cache
        main.SESSION_MANAGER = original_manager
        main.EXECUTOR = original_executor
        _shutil.rmtree(root, ignore_errors=True)


def run_tests() -> None:
    fake_opencode = FakeOpenCodeAdapter()
    temp_root = Path(tempfile.mkdtemp(prefix="agent-workflow-test-"))
    original_popen = main.subprocess.Popen
    main.SESSION_MANAGER = main.SessionManager(temp_root / "sessions")
    main.JOB_MANAGER = JobManager(temp_root / "jobs")
    main.EXECUTOR = Executor(opencode=fake_opencode, session_manager=main.SESSION_MANAGER)
    check.JOB_MANAGER = main.JOB_MANAGER
    work_dir = str(temp_root)
    git_init = main.subprocess.run(
        ["git", "init", "-q", str(temp_root)], capture_output=True, text=True
    )
    assert_true(git_init.returncode == 0, f"test fixture git init failed: {git_init.stderr}")
    ensure_workflow_workspace(temp_root, os.getenv("AGENT_PATH"))

    try:
        # 1. Prompt constraints
        prompt = build_prompt(
            role="exploration",
            task="cek graph",
            session_id="prompt-test",
            command="explore",
            project_root=str(temp_root),
        )
        assert_true("[WORKFLOW_AGENT]" in prompt, "prompt must tag workflow-agent context")
        # The prompt stays within the Windows argv limit by anchoring the full protocol
        # in AGENTS.md.
        assert_true(
            "full evidence protocol in AGENTS.md" in prompt,
            "evidence prompt must anchor the full protocol (graphify-first) to AGENTS.md",
        )
        claude_contract = (
            Path(__file__).resolve().parent
            / "dist"
            / "config"
            / "claude"
            / "CLAUDE.md"
        ).read_text(encoding="utf-8")
        assert_true(
            "run_in_background: true" in claude_contract,
            "Claude delegated runner must use the background-task tool mode",
        )
        assert_true(
            "meta.reason=recovery_exhausted" in claude_contract,
            "Claude contract must stop bounded recovery after the second death",
        )

        # 2. Bootstrap flow
        recording_opencode = RecordingOpenCodeAdapter()
        bootstrapped = recording_opencode.run(
            "real prompt",
            {"session_id": "bootstrap-test", "opencode_session_id": None},
            "provider/model",
            work_dir,
        )
        assert_true(bootstrapped["ok"], "bootstrap flow must succeed")
        assert_true(recording_opencode.init_calls[0]["work_dir"] == work_dir, "init must receive work_dir")
        assert_true("--print-logs" in recording_opencode.init_calls[0]["args"], "init must use --print-logs")
        assert_true("stderr" not in bootstrapped["meta"]["bootstrap"], "init logs must not be exposed in bootstrap meta")
        assert_true(recording_opencode.run_calls[0]["work_dir"] == work_dir, "agent run must receive work_dir")
        run_args = recording_opencode.run_calls[0]["args"]
        assert_true("--print-logs" not in run_args, "agent run must not use --print-logs")
        assert_true("--log-level" not in run_args, "agent run must not use --log-level after init")
        assert_true(run_args[-2:] == ["-s", "ses_boot123"], "agent run must resume captured OpenCode session")

        # 3. Explore command
        explore = main.run("explore", "cari entry point module auth", "test-session-v2", work_dir=work_dir)
        assert_true(explore["ok"], "explore must succeed")
        assert_true(explore["meta"]["opencode_session_id"] == "ses_test123", "first run must capture OpenCode session")
        assert_true(fake_opencode.calls[-1]["work_dir"] == work_dir, "work_dir must reach adapter")
        assert_true("INFO  2026" not in explore["content"], "OpenCode logs must be stripped")
        assert_true("> build" not in explore["content"], "OpenCode model banner must be stripped")
        assert_true(
            "args" not in explore.get("meta", {}),
            "Executor must remove raw argv from injected adapter results",
        )

        # 4. Analyze with model override and session reuse
        analyze = main.run(
            "analyze",
            "cek logic auth",
            "test-session-v2",
            work_dir=work_dir,
            model="9router-sdi/gpt-5.3-codex",
        )
        assert_true(analyze["ok"], "analyze must succeed")
        assert_true(fake_opencode.calls[-1]["model"] == "9router-sdi/gpt-5.3-codex", "model override must reach adapter")
        assert_true(
            fake_opencode.calls[-1]["session"].get("opencode_session_id") == "ses_test123",
            "second run must reuse OpenCode session",
        )

        # 5. Failure simulation
        fake_opencode.fail_next = True
        failure = main.run("verify", "simulate opencode failure", "test-session-v2", work_dir=work_dir)
        assert_true(not failure["ok"], "OpenCode failure must return error")

        # 6. Job manager lock + result states
        job = main.JOB_MANAGER.create_job("execute", "do async thing", "async-session", work_dir, None)
        assert_true(job["status"] == "pending", "job must start pending")
        duplicate_error = None
        try:
            main.JOB_MANAGER.create_job("execute", "second async thing", "async-session", work_dir, None)
        except ValueError as exc:
            duplicate_error = str(exc)
        assert_true(duplicate_error is not None, "second active job on same session must fail")
        running = main.JOB_MANAGER.mark_running(job["job_id"])
        assert_true(running["status"] == "running", "job must move to running")
        completed = main.JOB_MANAGER.complete_job(job["job_id"], {"ok": True, "content": "done", "meta": {}})
        assert_true(completed["status"] == "completed", "job must complete")
        result = main.get_result(job["job_id"])
        assert_true(result["ok"] and result["status"] == "completed", "result must expose completed output")
        missing = main.get_status("missing-job")
        assert_true(not missing["ok"] and missing["status"] == "not_found", "missing status must be structured")

        old_owner = main.JOB_MANAGER.create_job(
            "analyze", "old", "owner-session", work_dir, None
        )
        main.JOB_MANAGER.complete_job(old_owner["job_id"], {"ok": True})
        new_owner = main.JOB_MANAGER.create_job(
            "analyze", "new", "owner-session", work_dir, None
        )
        main.JOB_MANAGER._release_session_lock(old_owner)
        active_owner = main.JOB_MANAGER.active_job_for_session("owner-session")
        assert_true(
            active_owner and active_owner["job_id"] == new_owner["job_id"],
            "a late job must not release a newer owner's session lock",
        )
        main.JOB_MANAGER.fail_job(new_owner["job_id"], "cleanup")

        # 7. Identical requests attach while alive and recover once after worker death.
        def fake_popen(*args, **kwargs):
            return FakeJobProcess(os.getpid())

        main.subprocess.Popen = fake_popen
        submitted = main.submit("analyze", "long task", "submit-session", work_dir, None)
        assert_true(submitted["ok"], "submit must succeed")
        assert_true(submitted["status"] == "pending", "submit must return pending")
        attached = main.submit("analyze", "long task", "submit-session", work_dir, None)
        assert_true(
            attached["job_id"] == submitted["job_id"]
            and attached["meta"]["reused"]
            and not attached["meta"]["recovery"],
            "same request with a live worker must attach",
        )
        blocked = main.submit("analyze", "different task", "submit-session", work_dir, None)
        assert_true(
            not blocked["ok"] and blocked["meta"]["error_type"] == "job_already_running",
            "different request on the same locked session must be rejected",
        )
        main.JOB_MANAGER.set_worker_pid(submitted["job_id"], 999999999)
        recovered = main.submit("analyze", "long task", "submit-session", work_dir, None)
        assert_true(
            recovered["ok"]
            and recovered["job_id"] == submitted["job_id"]
            and recovered["meta"]["recovery"],
            "dead worker must restart the same job once",
        )
        main.JOB_MANAGER.set_worker_pid(submitted["job_id"], 999999999)
        exhausted = main.submit("analyze", "long task", "submit-session", work_dir, None)
        assert_true(
            not exhausted["ok"]
            and exhausted["meta"].get("reason") == "recovery_exhausted",
            "a second worker death must fail terminal instead of looping",
        )
        assert_true(
            main.JOB_MANAGER.active_job_for_session("submit-session") is None,
            "recovery exhaustion must release the session lock",
        )

        queued = main.JOB_MANAGER.create_job(
            "explore", "inspect queued flow", "queued-session", work_dir, None
        )
        assert_true(main.should_run_in_background("explore"), "explore must be marked as background command")
        main.subprocess.Popen = original_popen

        # 8. Worker path updates job state on success
        worker_job = main.JOB_MANAGER.create_job("explore", "inspect async flow", "worker-session", work_dir, None)
        worker_output = main.run_worker(worker_job["job_id"])
        assert_true(worker_output["ok"], "worker must execute queued command")
        worker_status = main.get_status(worker_job["job_id"])
        assert_true(worker_status["status"] == "completed", "worker must persist completed state")

        recovery_session = main.SESSION_MANAGER.load_or_create("recover-worker-session")
        main.SESSION_MANAGER.update_opencode_session_id(
            recovery_session, "ses_recover123"
        )
        recovery_job = main.JOB_MANAGER.create_job(
            "explore",
            "finish interrupted mapping",
            "recover-worker-session",
            work_dir,
            None,
        )
        main.JOB_MANAGER.set_worker_pid(recovery_job["job_id"], 999999999)
        main.JOB_MANAGER.mark_running(recovery_job["job_id"])
        recovery_claim = main.JOB_MANAGER.claim_recovery(
            recovery_job["job_id"], stale_after_seconds=0
        )
        main.JOB_MANAGER.release_recovery_claim(recovery_job["job_id"])
        assert_true(
            recovery_claim["action"] == "recover",
            "dead/unstarted job must enter bounded recovery",
        )
        recovery_output = main.run_worker(recovery_job["job_id"])
        assert_true(recovery_output["ok"], "recovery worker must execute")
        assert_true(
            "Continue the interrupted task" in fake_opencode.calls[-1]["prompt"]
            and "finish interrupted mapping" in fake_opencode.calls[-1]["prompt"],
            "recovery prompt must carry job context and original task",
        )
        assert_true(
            fake_opencode.calls[-1]["session"].get("opencode_session_id")
            == "ses_recover123",
            "recovery must reuse the captured OpenCode session",
        )

        pre_spawn_job = main.JOB_MANAGER.create_job(
            "explore",
            "start dispatch that lost its submit owner",
            "pre-spawn-orphan-session",
            work_dir,
            None,
        )
        pre_spawn_record = main.JOB_MANAGER.get_job(pre_spawn_job["job_id"])
        pre_spawn_record["reservation_owner_pid"] = 999999999
        pre_spawn_record["reservation_owner_create_time"] = None
        main.JOB_MANAGER._save(pre_spawn_record)
        pre_spawn_claim = main.JOB_MANAGER.claim_recovery(
            pre_spawn_job["job_id"], stale_after_seconds=0
        )
        main.JOB_MANAGER.release_recovery_claim(pre_spawn_job["job_id"])
        claimed_job = pre_spawn_claim.get("job") or {}
        assert_true(
            pre_spawn_claim.get("action") == "recover"
            and claimed_job.get("recovery_attempt") == 0
            and claimed_job.get("recovery_reason") == "pre_spawn_orphan",
            f"pre-spawn orphan must resume initial dispatch without a recovery attempt: {pre_spawn_claim}",
        )
        pre_spawn_output = main.run_worker(pre_spawn_job["job_id"])
        assert_true(
            pre_spawn_output.get("ok")
            and "Continue the interrupted task" not in fake_opencode.calls[-1]["prompt"],
            f"pre-spawn orphan must run the original task without captured provider session: {pre_spawn_output}",
        )

        # 9. check.py status/result payloads
        # A freshly queued job has no worker PID yet and remains attachable.
        pending_status = check._status_payload(queued["job_id"])
        assert_true(
            pending_status["status"] == "pending",
            f"queued job must remain pending before dispatch: {pending_status}",
        )

        # ...and a job whose worker IS alive still reports as running. Use this very
        # process as the stand-in worker: it is guaranteed alive for the assertion.
        live_job = main.JOB_MANAGER.create_job("explore", "live worker", "live-session", work_dir, None)
        main.JOB_MANAGER.set_worker_pid(live_job["job_id"], os.getpid())
        main.JOB_MANAGER.mark_running(live_job["job_id"])
        main.JOB_MANAGER.touch_heartbeat(live_job["job_id"], {"phase": "agent", "idle_seconds": 0})
        live_status = check._status_payload(live_job["job_id"])
        assert_true(
            live_status["status"] == "running" and live_status["done"] is False,
            f"a job with a live worker and a fresh beat must not be reaped: {live_status}",
        )

        complete_job = main.JOB_MANAGER.create_job("execute", "done task", "result-session", work_dir, None)
        main.JOB_MANAGER.complete_job(complete_job["job_id"], {"ok": True, "content": "clean output", "meta": {}})
        result_ok, result_payload = check._result_payload(complete_job["job_id"])
        assert_true(result_ok, "completed job must return output-only payload")
        assert_true(result_payload == "clean output", "completed result must expose cleaned content only")

        failed_job = main.JOB_MANAGER.create_job("execute", "bad task", "failed-session", work_dir, None)
        main.JOB_MANAGER.fail_job(failed_job["job_id"], "boom")
        failed_ok, failed_payload = check._result_payload(failed_job["job_id"])
        assert_true(not failed_ok, "failed job must not return plain output")
        assert_true(failed_payload["status"] == "failed", "failed result must keep failed status")

        missing_status = check._status_payload("missing-job")
        assert_true(missing_status["status"] == "not_found", "missing job must report not_found in check.py")

        # 10. check.py internal wait loop and timeout contract
        wait_job = main.JOB_MANAGER.create_job("execute", "wait task", "wait-session", work_dir, None)

        def complete_later() -> None:
            time.sleep(0.1)
            main.JOB_MANAGER.complete_job(wait_job["job_id"], {"ok": True, "content": "waited output", "meta": {}})

        waiter = threading.Thread(target=complete_later)
        waiter.start()
        wait_ok, wait_payload = check._wait_for_result(wait_job["job_id"], 0.05, 2)
        waiter.join()
        assert_true(wait_ok, "wait loop must eventually return completed result")
        assert_true(wait_payload == "waited output", "wait loop must return cleaned output only on success")

        timeout_job = main.JOB_MANAGER.create_job("execute", "timeout task", "timeout-session", work_dir, None)
        timeout_ok, timeout_payload = check._result_payload(timeout_job["job_id"])
        assert_true(not timeout_ok, "non-wait result lookup must stay incomplete")

        timeout_status = check._wait_for_status(timeout_job["job_id"], 0.05, 1)
        assert_true(timeout_status["status"] == "pending", "timed out status must preserve current job state")
        assert_true(timeout_status.get("timed_out") is True, "timed out wait must mark timed_out")

        # 11. Structured errors, idempotency, reaper, digest, guard, router
        from core.contract import extract_digest, make_error, validate_verification_contract
        from core.router import Router
        from utils import osutil, path_guard

        # next_action mandatory
        enforced = False
        try:
            make_error("permission_denied", "x", next_action="")
        except ValueError:
            enforced = True
        assert_true(enforced, "make_error must require next_action")

        # role code-authoritative; local command rejected from delegation
        assert_true(Router().route("explore")["role"] == "exploration", "router derives role from code")
        execute_rejected = False
        try:
            Router().route("execute")
        except ValueError:
            execute_rejected = True
        assert_true(execute_rejected, "execute must not be delegable")

        # idempotency: identical request reuses the same job
        idem_a = main.JOB_MANAGER.create_job("explore", "same task", "idem-session", work_dir, None)
        idem_b = main.JOB_MANAGER.create_job("explore", "same task", "idem-session", work_dir, None)
        assert_true(idem_a["job_id"] == idem_b["job_id"], "identical request must reuse job")
        main.JOB_MANAGER.complete_job(idem_a["job_id"], {"ok": True, "content": "x", "meta": {}})

        # A first dead worker remains locked and advertises one bounded recovery.
        dead = main.JOB_MANAGER.create_job("explore", "dead worker", "reaper-session", work_dir, None)
        main.JOB_MANAGER.set_worker_pid(dead["job_id"], 999999999)
        main.JOB_MANAGER.mark_running(dead["job_id"])
        reaped = main.get_result(dead["job_id"])
        assert_true(
            reaped["status"] == "running"
            and reaped["meta"].get("recoverable") is True,
            "first dead worker must stay recoverable instead of releasing its lock",
        )
        main.JOB_MANAGER.fail_job(dead["job_id"], "test cleanup")

        # digest extraction + fallback
        digest = extract_digest("findings:\n- a\n[DIGEST]\nsummary: s\nkey_findings:\n- k\nrisk_level: high\nrecommended_next_action: go\nconfidence: low")
        assert_true(digest and digest["summary"] == "s" and digest["risk_level"] == "high", "digest must parse")
        assert_true(extract_digest("no digest here") is None, "missing digest must fall back to None")

        valid_verify = """[VERIFICATION]
verdict: DONE
blocking_findings:
- none
escalations:
- none
notes:
- none
checks_run:
- python -B test_scenario.py: pass
not_verified:
- none
confidence: high — all requested checks ran
"""
        assert_true(
            validate_verification_contract(valid_verify)["verdict"] == "pass",
            "well-formed complete verification must pass",
        )
        incomplete_verify = valid_verify.replace(
            "- python -B test_scenario.py: pass", "- none"
        )
        assert_true(
            validate_verification_contract(incomplete_verify)["verdict"] == "incomplete",
            "verification without an executed check must be incomplete",
        )
        blocking_verify = valid_verify.replace(
            "- none\nescalations:",
            "- severity: high | origin: introduced | scope_relation: in_scope\n"
            "  problem: broken [a.py:1]\nescalations:",
            1,
        )
        assert_true(
            validate_verification_contract(blocking_verify)["verdict"] == "fail",
            "a blocking finding must fail even when the declared verdict says DONE",
        )
        misrouted_verify = valid_verify.replace(
            "notes:\n- none",
            "notes:\n- severity: high | origin: introduced | "
            "scope_relation: in_scope — broken [a.py:1]",
        )
        misrouted_assessment = validate_verification_contract(misrouted_verify)
        assert_true(
            misrouted_assessment["verdict"] == "fail"
            and any(
                item.get("kind") == "finding_misrouted"
                for item in misrouted_assessment["warnings"]
            ),
            f"a blocking-class finding cannot pass from notes: {misrouted_assessment}",
        )
        assert_true(
            main._verify_exit_code(
                "await", {"ok": True, "meta": {"verdict": "fail"}}, "verify"
            )
            == 2,
            "await must propagate a failed verify exit code",
        )
        assert_true(
            main._verify_exit_code(
                "await", {"ok": True, "meta": {"verdict": "incomplete"}}, "verify"
            )
            == 2,
            "await must propagate an incomplete verify exit code",
        )
        assert_true(
            main._verify_exit_code(
                "await", {"ok": True, "meta": {"verdict": "pass"}}, "verify"
            )
            == 0,
            "await must preserve a clean verify exit code",
        )
        assert_true(
            main._verify_exit_code(
                "result",
                {"ok": True, "status": "completed", "output": {"ok": True, "meta": {"verdict": "fail"}}},
                "verify",
            )
            == 2
            and main._verify_exit_code(
                "result",
                {"ok": True, "status": "completed", "output": {"ok": True, "meta": {"verdict": "pass"}}},
                "verify",
            )
            == 0,
            "result must derive verify exit status from the stored output",
        )

        # Secret access is denied on the TOOL CALL, not by scanning the task text. The
        # text scan blocked an audit for naming the files it audited while leaving the
        # files themselves reachable, so the rules now ship as opencode permissions.
        assert_true(
            not hasattr(path_guard, "validate_scope"),
            "the task-text scope guard must stay removed; enforcement is opencode's",
        )
        project_policy = json.loads(
            (
                Path(__file__).resolve().parent
                / "dist"
                / "config"
                / "opencode"
                / "opencode.project.json"
            ).read_text(encoding="utf-8")
        )
        policy_read = project_policy["permission"]["read"]
        policy_grep = project_policy["permission"]["grep"]
        for pattern in ("*.env", "*.env.*", "*id_rsa*", "*.pem", "*.key", "*.ssh/*"):
            assert_true(
                policy_read.get(pattern) == "deny",
                f"project policy must deny reading {pattern}",
            )
        assert_true(
            policy_grep.get("*.env") == "deny",
            "grep must be denied too: it returns file CONTENTS, so read-only denial alone leaks",
        )
        assert_true(
            policy_read.get("*.env.example") == "allow",
            "example env files carry no secret and must stay readable",
        )
        assert_true(
            list(policy_read).index("*.env") < list(policy_read).index("*.env.example"),
            "the allow exception must come after the deny it narrows; order decides the winner",
        )
        assert_true(
            not any(p in policy_read for p in ("*secret*", "*credential*")),
            "word-shaped patterns are what made the old guard block ordinary source files",
        )

        # cross-OS primitive
        assert_true(osutil.process_alive(999999999) is False, "process_alive must report dead pid")

        # invalid_evidence: menu/refusal (ok:true but no evidence) must be rejected as proxy failure
        class MenuAdapter:
            command = "opencode"
            timeout_seconds = 0
            no_timeout = True

            def run(self, prompt, session, model=None, work_dir=None):
                return {"ok": True, "content": "Specify command: explore, plan, analyze, verify, sweep, doctor.", "meta": {"opencode_session_id": "ses_menu"}}

        menu_exec = Executor(opencode=MenuAdapter(), session_manager=main.SESSION_MANAGER)
        menu_session = main.SESSION_MANAGER.load_or_create("menu-session")
        menu_res = menu_exec.execute("analyze", "do analysis", menu_session, work_dir)
        assert_true(not menu_res["ok"], "menu/refusal response must be rejected, not treated as success")
        assert_true(menu_res["meta"]["error_type"] == "invalid_evidence", "non-evidence must flag invalid_evidence")

        # rich error (error_type + next_action) must survive the job path, not collapse to a string
        rich_job = main.JOB_MANAGER.create_job("analyze", "rich err", "rich-session", work_dir, None)
        main.JOB_MANAGER.fail_job(
            rich_job["job_id"],
            "non-evidence",
            output={"ok": False, "content": "non-evidence", "meta": {"error_type": "invalid_evidence", "next_action": "STOP, ask user"}},
        )
        rich_res = main.get_result(rich_job["job_id"])
        assert_true(rich_res["meta"].get("error_type") == "invalid_evidence", "job path must preserve error_type")
        assert_true(rich_res["meta"].get("next_action") == "STOP, ask user", "job path must preserve next_action")

        # 12. Liveness tri-state, heartbeat, runtime ceiling, probe
        from core import fact_store, graph_index, job_manager as jm_mod

        # Stall detection and the runtime ceiling are separate managers on purpose: the
        # ceiling outranks stall in get_result (a hard backstop must win over a probe
        # hint), so sharing one tiny threshold would mask the stall path entirely.
        watchdog = JobManager(
            temp_root / "jobs", stall_threshold_seconds=1, max_runtime_seconds=3600
        )
        live_job = watchdog.create_job("explore", "watchdog", "watchdog-session", work_dir, None)
        job_id = live_job["job_id"]

        assert_true(
            watchdog.liveness(watchdog.get_job(job_id)) is None,
            "a job with no worker pid yet must not be classified (nothing to reap)",
        )
        watchdog.set_worker_pid(job_id, 999999999)
        assert_true(
            watchdog.liveness(watchdog.get_job(job_id)) == jm_mod.DEAD,
            "a gone pid must classify as dead",
        )

        # Live pid + fresh heartbeat = progressing; the SAME pid with a stale heartbeat
        # must classify as stalled. That difference is the whole point of the heartbeat:
        # pid liveness alone reports both cases identically.
        watchdog.mark_running(job_id)
        watchdog.set_worker_pid(job_id, os.getpid())
        watchdog.touch_heartbeat(job_id, {"phase": "agent", "elapsed_seconds": 3})
        beat = watchdog.read_heartbeat(job_id)
        assert_true(
            beat and beat.get("at") and beat["progress"]["phase"] == "agent",
            "heartbeat must record its timestamp and progress payload",
        )
        beating = watchdog.get_job(job_id)
        assert_true(
            watchdog.liveness(beating) == jm_mod.ALIVE_PROGRESSING,
            "live pid with a fresh heartbeat must be progressing",
        )
        time.sleep(1.2)  # exceed the 1s stall threshold configured above
        assert_true(
            watchdog.liveness(watchdog.get_job(job_id)) == jm_mod.ALIVE_STALLED,
            "live pid with a stale heartbeat must be stalled, not dead",
        )

        stalled = watchdog.get_result(job_id)
        assert_true(
            stalled["meta"].get("error_type") == "worker_stalled",
            "a stalled worker must be reported as stalled",
        )
        assert_true(
            watchdog.get_job(job_id)["status"] == "running",
            "a stalled worker must NOT be reaped on suspicion — its work may still land",
        )

        probed = watchdog.record_probe(job_id, {"alive": False, "reason": "probe_timeout"})
        assert_true(
            probed["liveness"] == "stalled_on_limit",
            "a probe that cannot reach opencode means rate/usage limit, not a hang",
        )
        assert_true(
            watchdog.read_probe(job_id)["liveness"] == "stalled_on_limit",
            "the probe verdict must survive a round-trip through its side file",
        )
        assert_true(
            watchdog.record_probe(job_id, {"alive": True, "reason": "probe_ok"})["liveness"]
            == "stalled_no_progress",
            "a probe that answers means opencode is healthy and this session is hung",
        )

        # A late beat must NOT resurrect a job another process already ended. Heartbeats
        # arrive every couple of seconds and get_result runs in a different process, so
        # folding the beat into the job record made this a routine collision, not a rare one.
        raced = watchdog.create_job("explore", "race", "race-session", work_dir, None)
        watchdog.set_worker_pid(raced["job_id"], os.getpid())
        watchdog.mark_running(raced["job_id"])
        watchdog.fail_job(raced["job_id"], "worker process died before completing (reaped)")
        watchdog.touch_heartbeat(raced["job_id"], {"phase": "agent"})  # beat arrives late
        settled = watchdog.get_job(raced["job_id"])
        assert_true(
            settled["status"] == "failed" and settled["error"],
            "a late heartbeat must never revert a terminal job or erase its error",
        )

        # Runtime ceiling: the OOM backstop, where the pid can look alive but the job is lost.
        ceiling = JobManager(
            temp_root / "jobs", stall_threshold_seconds=3600, max_runtime_seconds=1
        )
        expired = ceiling.create_job("plan", "expired", "expired-session", work_dir, None)
        # os.getpid() is a guaranteed-alive pid so the ceiling takes the job_expired branch
        # (a dead pid would classify as worker_died instead). _kill_worker's self-pid guard
        # skips the actual kill, so reaping this job cannot take the test process down.
        ceiling.set_worker_pid(expired["job_id"], os.getpid())
        ceiling.mark_running(expired["job_id"])
        ceiling.touch_heartbeat(expired["job_id"], {"phase": "agent"})
        time.sleep(1.2)
        expired_res = ceiling.get_result(expired["job_id"])
        assert_true(
            expired_res["meta"].get("error_type") == "job_expired",
            "a job past the runtime ceiling must fail as expired, distinct from worker_died",
        )

        # Tolerant fact parsing preserves blank lines, nested bullets, and continuations.
        messy = (
            "grounded:\n\n- claim A [main.py:1]\n  * nested detail\n"
            "- claim B [core/x.py:2]\n  wrapped tail\n\nassumptions:\n- guess\n"
        )
        grounded = fact_store._parse_block(messy, "grounded")
        assert_true(len(grounded) == 3, f"tolerant parser must keep blank/nested bullets, got {grounded}")
        assert_true(
            "wrapped tail" in grounded[-1],
            "a wrapped continuation line must join its bullet, not be dropped",
        )
        assert_true(
            fact_store._parse_block(messy, "assumptions") == ["guess"],
            "the next section header must end the block",
        )
        assert_true(
            fact_store._parse_block("grounded:\n- a [x.py:1]\n- b [y.py:2]\nassumptions:\n- c\n", "grounded")
            == ["a [x.py:1]", "b [y.py:2]"],
            "the pre-3.4.0 flat format must keep parsing identically",
        )
        # A top-level `key: value` line ends the current block.
        assert_true(
            fact_store._parse_block(
                "dependents:\n- calls X [a.py:1]\nexternal: none (no external libs)\n", "dependents"
            )
            == ["calls X [a.py:1]"],
            "a `key: value` line at column 0 must end the section, not extend the last bullet",
        )
        assert_true(
            fact_store._parse_block("grounded:\n- claim [a.py:1]\nconfidence: high\n", "grounded")
            == ["claim [a.py:1]"],
            "a trailing confidence line must not be absorbed into a claim",
        )

        # Graph leads: absent graph degrades to None, never an exception.
        assert_true(
            graph_index.leads(temp_root, "anything") is None,
            "a project without graphify-out must yield no leads instead of failing",
        )
        assert_true(
            graph_index.load_graph(temp_root) is None,
            "a missing graph.json must return None, not raise",
        )

        with tempfile.TemporaryDirectory(prefix="graph-cache-") as graph_tmp:
            graph_root = Path(graph_tmp)
            source = graph_root / "a.py"
            removed_source = graph_root / "b.py"
            source.write_text("value = 1\n", encoding="utf-8")
            removed_source.write_text("other = 2\n", encoding="utf-8")
            graph_file = graph_root / "graphify-out" / "graph.json"
            graph_file.parent.mkdir(parents=True)
            graph_file.write_text(json.dumps({"nodes": [], "edges": []}), encoding="utf-8")
            base_ns = time.time_ns()
            os.utime(source, ns=(base_ns, base_ns))
            os.utime(removed_source, ns=(base_ns, base_ns))
            os.utime(graph_file, ns=(base_ns + 10_000_000, base_ns + 10_000_000))
            graph_index._STALE_CACHE.clear()
            assert_true(graph_index.is_stale(graph_root) is False, "new graph must be fresh")
            removed_source.unlink()
            graph_index._STALE_CACHE.clear()
            assert_true(
                graph_index.is_stale(graph_root) is True,
                "deleting a source must make an unchanged graph stale",
            )
            removed_source.write_text("other = 2\n", encoding="utf-8")
            os.utime(removed_source, ns=(base_ns, base_ns))
            os.utime(graph_file, ns=(base_ns + 30_000_000, base_ns + 30_000_000))
            graph_index._STALE_CACHE.clear()
            assert_true(graph_index.is_stale(graph_root) is False, "refreshed graph must be fresh")
            os.utime(source, ns=(base_ns + 40_000_000, base_ns + 40_000_000))
            graph_index._STALE_CACHE.clear()
            assert_true(
                graph_index.is_stale(graph_root) is True,
                "disk cache must invalidate when a source changes",
            )
        repo_root = Path(__file__).resolve().parent
        repo_leads = graph_index.leads(repo_root, "session manager")
        # Guarded on the graph EXISTING, not on leads being truthy: keying the guard on
        # the result itself would let a leads() that silently returns None skip its own
        # assertions and still report success.
        assert_true(
            (graph_index.graph_path(repo_root).exists()) == (repo_leads is not None),
            "leads must be produced whenever graph.json exists, and only then",
        )
        if repo_leads:
            assert_true(
                all("\\" not in row["file"] for row in repo_leads["files"]),
                "lead paths must be repo-relative POSIX so they mean the same on any machine",
            )
            # Leads travel through a sidecar so the prompt only needs a bounded anchor.
            leads_prompt = build_prompt(
                role="reasoning",
                task="session manager",
                session_id="leads",
                command="plan",
                project_root=str(repo_root),
                runtime_dir=str(repo_root / ".workflow" / "sessions" / "leads" / "runtime"),
                has_leads=True,
            )
            assert_true("[EVIDENCE_SIDECARS" in leads_prompt, "prompt must anchor to the leads sidecar")
            assert_true("leads.json" in leads_prompt, "leads must reach the agent via the sidecar file")
            assert_true(
                "WEAK hints" in leads_prompt,
                "leads must be framed as starting points, never as findings",
            )

        # Delegated runs have a finite default timeout.
        default_adapter = OpenCodeAdapter()
        assert_true(
            not default_adapter.no_timeout and default_adapter.timeout_seconds > 0,
            "the default adapter must carry a real timeout",
        )
        assert_true(
            default_adapter.bootstrap_timeout_seconds > 0
            and default_adapter.bootstrap_timeout_seconds < default_adapter.timeout_seconds,
            "bootstrap must have its own, shorter budget than a full task",
        )
        # Side files must not outlive their job — nothing rewrites them once it ends.
        pruned_id = raced["job_id"]
        assert_true(
            watchdog._beat_path(pruned_id).exists(),
            "precondition: the raced job still has its beat file",
        )
        watchdog.log_path(pruned_id).write_text("worker log", encoding="utf-8")
        watchdog.prune_jobs(ttl_days=0, keep_last=0)
        assert_true(
            not watchdog._beat_path(pruned_id).exists()
            and not watchdog._probe_path(pruned_id).exists()
            and not watchdog.log_path(pruned_id).exists(),
            "pruning a job must take heartbeat, probe, and worker log side files",
        )

        # 13. Sub-agent fan-out: instruction gating and honest usage detection
        from core.contract import detect_subagent_usage
        from core.workflow_runtime import subagent_fanout_enabled

        def _p(has_leads, fanout):
            return build_prompt(
                role="exploration", task="t", session_id="s", command="explore",
                project_root=str(temp_root),
                runtime_dir=str(temp_root / ".workflow" / "sessions" / "s" / "runtime"),
                has_leads=has_leads, subagent_fanout=fanout,
            )

        # The prompt carries only the sidecar anchor; AGENTS.md owns the fan-out rules.
        fan = _p(True, True)
        assert_true("FAN-OUT call" in fan, "a fan-out call must be flagged in the prompt")
        assert_true("leads.json" in fan, "fan-out must point the agent at the leads sidecar")
        assert_true(
            "subagents:" in fan,
            "the output format must ask which clusters were dispatched",
        )
        assert_true(
            "SUBAGENT_PLAN" not in fan and "no dependency graph is available" not in fan,
            "the fan-out plan body must live in AGENTS.md, not inline in the prompt",
        )
        assert_true(
            "FAN-OUT call" not in _p(True, False),
            "fan-out must stay off when the policy is off",
        )

        # Detection needs BOTH signals to agree — a declaration alone is a claim of work,
        # not evidence of it.
        real = "subagents: c1, c2\ngrounded:\n- [c1] X [a.py:1]\n- [c2] Y [b.py:2]\n"
        got = detect_subagent_usage(real)
        assert_true(
            got["used"] and got["fanout_clusters"] == ["c1", "c2"],
            f"real fan-out must register: {got}",
        )
        assert_true(not got["mismatch"], "matching declaration and tags must not warn")

        disjoint = "subagents: c1\ngrounded:\n- [c2] X [a.py:1]\n"
        got = detect_subagent_usage(disjoint)
        assert_true(
            not got["used"] and got["mismatch"],
            f"disjoint declaration and coverage must be rejected: {got}",
        )
        assert_true(
            got["fanout_clusters"] == [] and got["covered_clusters"] == ["c2"],
            f"disjoint coverage must remain visible without proving fan-out: {got}",
        )

        one_empty = "subagents: c1, c2\ngrounded:\n- [c1] X [a.py:1]\n"
        got = detect_subagent_usage(one_empty)
        assert_true(
            got["used"] and got["fanout_clusters"] == ["c1", "c2"],
            f"a dispatched empty slice must not invalidate confirmed fan-out: {got}",
        )

        lying = "subagents: c1, c2\ngrounded:\n- X [a.py:1]\n"
        got = detect_subagent_usage(lying)
        assert_true(
            not got["used"] and got["mismatch"] and got["fanout_clusters"] == [],
            "a declared fan-out with no tagged claims must be flagged, not counted as success",
        )

        # Tagged cluster coverage alone is not proof that fan-out occurred.
        honest = (
            "subagents: none (no spawn tool; tools: read, grep)\n"
            "grounded:\n- [c1] X [a.py:1]\n- [c3] Y [b.py:2]\n"
        )
        got = detect_subagent_usage(honest)
        assert_true(
            not got["used"] and not got["mismatch"],
            "an honest 'no spawn tool' answer must be neither used nor a mismatch",
        )
        assert_true(
            got["fanout_clusters"] == [] and got["covered_clusters"] == ["c1", "c3"],
            f"tagged-but-not-fanned-out must report coverage, never fan-out: {got}",
        )
        assert_true(
            not detect_subagent_usage("grounded:\n- X [a.py:1]\n")["used"],
            "output with no subagents line must not register as fan-out",
        )

        # Fan-out defaults to on, including when config is unreadable.
        assert_true(
            subagent_fanout_enabled(temp_root) is True,
            "fan-out must default to on",
        )
        assert_true(
            subagent_fanout_enabled(temp_root / "does-not-exist") is True,
            "an unreadable config must fall back to the default, not to off",
        )

        # Fresh init uses the example config; null delegates model selection to OpenCode.
        import json as _json

        example = Path(__file__).resolve().parent / "config" / "opencode.example.json"
        shipped = _json.loads(example.read_text(encoding="utf-8"))
        models = [shipped.get("default_model")] + [
            r.get("model") for r in (shipped.get("routes") or {}).values()
        ]
        assert_true(
            all(m is None for m in models),
            f"shipped example must not carry placeholder models: {models}",
        )
        assert_true(
            shipped.get("timeout_seconds", 0) > 0,
            "shipped example must carry a real timeout, not an unbounded wait",
        )

        calls_before_sweep = len(fake_opencode.calls)
        local_sweep = main.run("sweep", "", "local-sweep", work_dir)
        assert_true(local_sweep["ok"], f"local sweep must return a report: {local_sweep}")
        assert_true(
            len(fake_opencode.calls) == calls_before_sweep,
            "sweep must not consume an OpenCode call",
        )

        # JobManager still accepts an empty task for compatibility with stored sweep jobs.
        taskless = main.JOB_MANAGER.create_job("sweep", None, "taskless-session", work_dir, None)
        assert_true(
            taskless["task"] == "" and taskless["request_hash"],
            "a job with no task must be created normally, not crash",
        )
        main.JOB_MANAGER.fail_job(taskless["job_id"], "cleanup")

        _test_facts_concurrency()
        _test_evidence_reuse()
        _test_redaction_boundary()
        _test_quick_verify_gaps()
        _test_submit_admission()
        _test_workspace_release_guards()
        _test_project_session_isolation()

        print("test_scenario: success")
    finally:
        main.subprocess.Popen = original_popen


if __name__ == "__main__":
    run_tests()
