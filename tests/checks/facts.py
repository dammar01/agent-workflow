"""Fact store concurrency and evidence artifact reuse."""

import json
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

import check
import main
from adapters.providers.opencode_adapter import OpenCodeAdapter
from core.provider.executor import Executor
from core.jobs.job_manager import JobManager
from core.prompt.prompt_builder import build_prompt
from core.runtime.state import ensure_workflow_workspace

from tests.checks.support import (
    FakeJobProcess,
    FakeOpenCodeAdapter,
    RecordingOpenCodeAdapter,
    assert_true,
    clean_output,
    extract_session_id,
)

def _test_facts_concurrency() -> None:
    """Concurrent ingest must not lose updates and must leave valid JSONL.

    N threads each ingest one distinct, file:line-anchored fact into the same store. The
    _FactLock read-modify-write serialises them, so all N must survive (an unlocked store
    would let the last writer clobber earlier additions), and every line must still parse.
    """
    import json as _json
    import shutil as _shutil

    from core.evidence import fact_store
    from core.workspace.workspace_paths import workflow_paths

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


def _test_anchor_relocation() -> None:
    """A fact whose anchor line MOVED survives; one whose content changed still does not.

    Inserting a block at the top of a file used to invalidate every fact anchored below it:
    lookup was by line NUMBER, so line 40 now held different text and hashed differently.
    Nothing about those claims had changed. Relocation closes that, and the assertions here
    pin the boundary — moved survives, edited drops, ambiguous drops.
    """
    import shutil as _shutil

    from core.evidence import fact_store
    from core.workspace.workspace_paths import workflow_paths

    root = Path(tempfile.mkdtemp(prefix="facts-reloc-"))
    try:
        workflow_paths(root)["workflow_dir"].mkdir(parents=True, exist_ok=True)
        src = root / "src.py"
        # Lines 2 and 3 are deliberately identical: their shared hash is what an ambiguous
        # anchor looks like, and no relocation may pick between them.
        src.write_text(
            "alpha_anchor = 1\ndup_line = 0\ndup_line = 0\nbeta_anchor = 2\n",
            encoding="utf-8",
        )
        fact_store.ingest(
            root,
            "durable_facts:\n"
            "- [config] alpha setting governs alpha behaviour [src.py:1]\n"
            "- [config] ambiguous setting sits on a repeated line [src.py:3]\n"
            "- [config] beta setting governs beta behaviour [src.py:4]\n",
            "sess-reloc",
        )
        assert_true(
            len(fact_store._load_facts(root)) == 3,
            f"fixture must start with 3 facts: {fact_store._load_facts(root)}",
        )

        # Shift everything down by four lines without touching any anchored text.
        src.write_text(
            "# header\n# header two\n# header three\n# header four\n"
            + src.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

        task = "alpha ambiguous beta setting"
        placed = {f["claim"]: f["line"] for f in fact_store.load_relevant(root, task, limit=10)}
        alpha = next((c for c in placed if "alpha" in c), None)
        beta = next((c for c in placed if "beta" in c), None)
        ambiguous = next((c for c in placed if "ambiguous" in c), None)
        assert_true(
            alpha is not None and placed[alpha] == 5,
            f"a fact whose anchor only moved must be served at its new line: {placed}",
        )
        assert_true(
            beta is not None and placed[beta] == 8,
            f"relocation must track each anchor independently: {placed}",
        )
        assert_true(
            ambiguous is None,
            "an anchor matching more than one line identifies nothing — it must stay "
            f"dropped rather than be relocated to a guess: {placed}",
        )

        # prune persists the move: the lock is held and the file is rewritten anyway.
        report = fact_store.prune(root)
        assert_true(
            report["relocated"] == 2 and report["removed"] == 1,
            f"prune must relocate the two movable facts and drop the ambiguous one: {report}",
        )
        stored = {f["claim"]: f["line"] for f in fact_store._load_facts(root)}
        assert_true(
            sorted(stored.values()) == [5, 8],
            f"relocated line numbers must be written back to facts.jsonl: {stored}",
        )

        # Content change is a different event and must still invalidate.
        src.write_text(
            src.read_text(encoding="utf-8").replace("alpha_anchor = 1", "alpha_anchor = 99"),
            encoding="utf-8",
        )
        after = [f["claim"] for f in fact_store.load_relevant(root, task, limit=10)]
        assert_true(
            not any("alpha" in c for c in after),
            f"an anchor whose CONTENT changed must still be dropped: {after}",
        )
    finally:
        _shutil.rmtree(root, ignore_errors=True)


def _test_evidence_anchor_relocation() -> None:
    """Evidence survives a shifted citation. Freshness demands EVERY anchor hold, so one
    insertion above one cited line used to discard a whole artifact and force a re-run."""
    import shutil as _shutil

    from core.evidence import evidence_store
    from core.evidence.runtime_io import write_response_snapshot
    from core.workspace.workspace_paths import workflow_paths

    root = Path(tempfile.mkdtemp(prefix="evidence-reloc-"))
    try:
        src = root / "src.py"
        src.write_text(
            "\n".join(f"anchor_{i} = {i}" for i in range(1, 21)) + "\n", encoding="utf-8"
        )
        session_id = "evidence-reloc-session"
        content = "FINDING [src.py:3]"
        context = {"model": "provider/model-a", "agent": "plan"}
        write_response_snapshot(root, content, "run-a", session_id)
        evidence_store.record(
            root,
            "analyze",
            "shifted query",
            session_id,
            {"summary": "first"},
            workflow_paths(root, session_id)["logs_dir"] / "run-a" / "output.raw.md",
            content,
            context=context,
        )
        assert_true(
            evidence_store.find_fresh(root, "analyze", "shifted query", context=context)
            is not None,
            "fixture must be reusable before the file is touched",
        )

        src.write_text("# added\n# added two\n" + src.read_text(encoding="utf-8"), encoding="utf-8")
        assert_true(
            evidence_store.find_fresh(root, "analyze", "shifted query", context=context)
            is not None,
            "an artifact whose only change is a shifted anchor line must stay fresh",
        )

        src.write_text(
            src.read_text(encoding="utf-8").replace("anchor_3 = 3", "anchor_3 = 300"),
            encoding="utf-8",
        )
        assert_true(
            evidence_store.find_fresh(root, "analyze", "shifted query", context=context)
            is None,
            "an artifact citing a line whose CONTENT changed must not be reused",
        )
    finally:
        _shutil.rmtree(root, ignore_errors=True)


def _test_evidence_reuse() -> None:
    """Reuse must read immutable, hash-checked artifacts and preserve concurrent rows."""
    import shutil as _shutil

    from core.evidence import evidence_store
    from core.evidence.runtime_io import write_response_snapshot
    from core.workspace.workspace_paths import workflow_paths

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
