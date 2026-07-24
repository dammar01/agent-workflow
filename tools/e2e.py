"""End-to-end checks for agent-workflow.

Two tiers, because they cost very different things:

    python tools/e2e.py           # local only: no opencode, no quota, seconds
    python tools/e2e.py --full    # also runs delegated commands: minutes + real quota

`--full` is opt-in on purpose. A delegated command spends the same rate-limited budget
the workflow itself needs; a test suite that quietly burns it is worse than no suite.

Checks assert the CONTRACT, not prose: `ok`, the evidence markers, and the fields
`core/contract.py` declares required. A check that could not run is reported as SKIPPED
and never counted as a pass.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIPPED"


class Report:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []

    def record(self, name: str, status: str, detail: str = "") -> None:
        self.rows.append((name, status, detail))
        print(f"  {status:8} {name}{'  — ' + detail if detail else ''}")

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        self.record(name, PASS if condition else FAIL, detail)
        return condition

    @property
    def failed(self) -> int:
        return sum(1 for _, status, _ in self.rows if status == FAIL)

    @property
    def skipped(self) -> int:
        return sum(1 for _, status, _ in self.rows if status == SKIP)


def _run_cli(*args: str, cwd: Path | None = None) -> tuple[int, str]:
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "main.py"), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=str(cwd or REPO_ROOT),
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT), "PYTHONUTF8": "1"},
    )
    return result.returncode, (result.stdout or "") + (result.stderr or "")


def _json_from(output: str) -> dict | None:
    start = output.find("{")
    if start < 0:
        return None
    try:
        return json.loads(output[start:])
    except json.JSONDecodeError:
        return None


def local_checks(report: Report) -> None:
    print("\n[LOCAL] no opencode, no quota")

    project = Path(tempfile.mkdtemp(prefix="e2e-project-"))
    (project / ".git").mkdir()
    try:
        code, out = _run_cli("--command", "init", "--work-dir", str(project), "--pretty")
        payload = _json_from(out) or {}
        report.check("init returns ok", payload.get("ok") is True, f"rc={code}")
        report.check("init creates .workflow", (project / ".workflow").is_dir())
        report.check(
            "init generates run scripts",
            any((project / ".workflow").glob("run.*")),
        )
        config_path = project / ".workflow" / "config.json"
        if config_path.exists():
            config = json.loads(config_path.read_text(encoding="utf-8"))
            from config.settings import TOOL_VERSION

            report.check(
                "config carries current version",
                config.get("version") == TOOL_VERSION,
                f"{config.get('version')} vs {TOOL_VERSION}",
            )
        else:
            report.record("config carries current version", SKIP, "config.json absent")

        code, out = _run_cli("--command", "doctor", "--work-dir", str(project), "--pretty")
        payload = _json_from(out) or {}
        report.check(
            "doctor reports a status",
            payload.get("meta", {}).get("status") in {"READY", "NEEDS SETUP"},
            payload.get("meta", {}).get("status", "?"),
        )

        code, out = _run_cli("--command", "inspect", "--work-dir", str(project), "--pretty")
        report.check("inspect returns ok", (_json_from(out) or {}).get("ok") is True)

        code, out = _run_cli("--command", "clean", "--work-dir", str(project), "--pretty")
        report.check("clean returns ok", (_json_from(out) or {}).get("ok") is True)
    finally:
        shutil.rmtree(project, ignore_errors=True)

    # The shipped example is what a fresh clone actually installs.
    from config.settings import load_opencode_config
    from core.router import Router

    example = REPO_ROOT / "config" / "opencode.example.json"
    if example.exists():
        routes = Router(load_opencode_config(example))
        models = [routes.route(c)["model"] for c in ("explore", "plan", "analyze")]
        report.check(
            "shipped example has no placeholder models",
            all(m is None or "provider_id" not in str(m) for m in models),
            str(models),
        )
    else:
        report.record("shipped example has no placeholder models", SKIP, "example missing")


def integrity_checks(report: Report) -> None:
    """Cover the data-integrity + upgrade code that "0 failures" otherwise never touches:
    fact-store provenance, config validation, and the lazy runtime-upgrade gate."""
    print("\n[INTEGRITY] fact store, config validation, lazy upgrade")

    from config.settings import COMPONENT_VERSIONS
    from core import fact_store
    from core.workflow_runtime import (
        ensure_workflow_workspace,
        upgrade_workflow_workspace,
        validate_config,
        workflow_paths,
    )

    # --- config validation (pure, no workspace needed) ---
    from core.workflow_runtime import default_commands, default_policies

    clean = {"commands": default_commands(), "policies": default_policies()}
    report.check("validate_config: clean config has no warnings",
                 validate_config(clean) == [], str(validate_config(clean)[:2]))
    dirty = {
        "commands": {"verify_mode": 123, "typo_key": True},
        "policies": {"fact_relevant_limit": "3"},
    }
    warns = validate_config(dirty)
    report.check(
        "validate_config: flags unknown key + wrong types",
        any("typo_key" in w for w in warns)
        and any("verify_mode" in w for w in warns)
        and any("fact_relevant_limit" in w for w in warns),
        f"{len(warns)} warning(s)",
    )

    project = Path(tempfile.mkdtemp(prefix="e2e-integrity-"))
    (project / ".git").mkdir()
    try:
        ensure_workflow_workspace(project, str(REPO_ROOT / "main.py"))

        # --- fact-store provenance + lock plumbing ---
        (project / "sample.py").write_text("anchor_line = 1\n", encoding="utf-8")
        content = "durable_facts:\n- [config] sample defines anchor_line [sample.py:1]\n"
        added = fact_store.ingest(project, content, "e2e-integrity")
        report.check("fact_store: ingest promotes a durable fact", added == 1, f"added={added}")
        facts_path = workflow_paths(project)["workflow_dir"] / "facts.jsonl"
        stored = []
        if facts_path.exists():
            stored = [json.loads(l) for l in facts_path.read_text(encoding="utf-8").splitlines() if l.strip()]
        report.check(
            "fact_store: stored fact carries provenance origin",
            bool(stored) and stored[0].get("origin") == "discovered",
            str(stored[0].get("origin") if stored else "no fact"),
        )
        report.check("fact_store: recurrence threshold lowered to 3",
                     fact_store.RECURRENCE_THRESHOLD == 3, str(fact_store.RECURRENCE_THRESHOLD))
        # lock is re-entrant per call and releases cleanly
        with fact_store._FactLock(project):
            lock_held = (facts_path.with_name("facts.jsonl.lock")).exists()
        lock_freed = not (facts_path.with_name("facts.jsonl.lock")).exists()
        report.check("fact_store: lock acquires and releases", lock_held and lock_freed)

        # --- lazy upgrade gate (P0.7) ---
        cfg_path = workflow_paths(project)["config"]
        config = json.loads(cfg_path.read_text(encoding="utf-8"))
        report.check(
            "config: runtime_version stamped",
            config.get("runtime", {}).get("runtime_version") == COMPONENT_VERSIONS["runtime"],
            str(config.get("runtime", {}).get("runtime_version")),
        )
        # nothing changed since init -> scripts must NOT be rewritten (lazy skip)
        r1 = upgrade_workflow_workspace(project, str(REPO_ROOT / "main.py"))
        report.check("upgrade: skips script regen when runtime unchanged",
                     r1["regenerated_scripts"] == [], f"{len(r1['regenerated_scripts'])} script(s)")
        # simulate an older build -> gate must regen and restamp
        config["runtime"]["runtime_version"] = "0.0.0"
        cfg_path.write_text(json.dumps(config, indent=2), encoding="utf-8")
        r2 = upgrade_workflow_workspace(project, str(REPO_ROOT / "main.py"))
        report.check("upgrade: regenerates scripts when runtime bumped",
                     len(r2["regenerated_scripts"]) > 0, f"{len(r2['regenerated_scripts'])} script(s)")

        # --- fan-out capability probe (P1.6) ---
        from core.contract import reported_no_spawn_tool
        from core.workflow_runtime import fanout_capability, set_fanout_capability

        report.check(
            "P1.6: detects opencode 'no spawn tool' self-report",
            reported_no_spawn_tool("subagents: none (no spawn tool; tools: read, grep)")
            and not reported_no_spawn_tool("subagents: c1, c2"),
        )
        report.check("P1.6: capability unprobed is None", fanout_capability(project) is None)
        set_fanout_capability(project, False)
        report.check("P1.6: capability persists OFF once learned",
                     fanout_capability(project) is False)
    finally:
        shutil.rmtree(project, ignore_errors=True)

    # --- graph-lead framing (P1.7), pure functions ---
    from core.prompt_builder import _graph_block, _subagent_block

    leads = {
        "files": [{"file": "a.py", "community": 1}],
        "communities": [
            {"community": 1, "files": ["a.py", "b.py"]},
            {"community": 2, "files": ["c.py"]},
        ],
    }
    fresh_block = "\n".join(_graph_block({**leads, "stale": False}))
    stale_block = "\n".join(_graph_block({**leads, "stale": True}))
    report.check("P1.7: fresh graph framed as GRAPH_LEADS with clusters",
                 "GRAPH_LEADS" in fresh_block and "clusters:" in fresh_block)
    report.check(
        "P1.7: stale graph downgraded to weak GRAPH_HINT, clusters dropped",
        "GRAPH_HINT" in stale_block and "GRAPH_LEADS" not in stale_block
        and "clusters:" not in stale_block,
    )
    fresh_fan = "\n".join(_subagent_block({**leads, "stale": False}))
    stale_fan = "\n".join(_subagent_block({**leads, "stale": True}))
    report.check(
        "P1.7: stale graph falls back to blind fan-out slices",
        "investigation angle" in stale_fan and "investigation angle" not in fresh_fan,
    )

    # --- Option A: CONSTRAINTS deduped to AGENTS.md, OUTPUT_FORMAT kept ---
    from config.roles import ROLE_EXPLORATION
    from core.prompt_builder import build_prompt

    explore_prompt = build_prompt(
        role=ROLE_EXPLORATION,
        task="find the router",
        session_id="e2e",
        command="explore",
        project_root=str(REPO_ROOT),
    )
    verify_prompt = build_prompt(
        role="verification",
        task="verify the change",
        session_id="e2e",
        command="verify",
        project_root=str(REPO_ROOT),
    )
    # The verbose routing table moved OUT of the per-call prompt into AGENTS.md; only
    # the anchor + a pointer to AGENTS.md remain. If the full table leaks back into the
    # prompt the dedupe has regressed.
    report.check(
        "OptionA: verify prompt drops the full routing table, points to AGENTS.md",
        "introduced/regression + in_scope" not in verify_prompt
        and "AGENTS.md" in verify_prompt,
    )
    report.check(
        "OptionA: evidence prompt drops the verbose rulebook, points to AGENTS.md",
        "AGENTS.md" in explore_prompt
        and "never mix them into codebase" not in explore_prompt,
    )
    # OUTPUT_FORMAT is the structural contract executor.py enforces (marker scan). It
    # must survive the trim, or every delegated call fails invalid_evidence.
    report.check(
        "OptionA: OUTPUT_FORMAT skeleton kept (executor marker contract intact)",
        "[EVIDENCE]" in explore_prompt and "[DIGEST]" in explore_prompt
        and "[VERIFICATION]" in verify_prompt,
    )
    report.check(
        "OptionA: verify prompt stays under the Windows 8191-char argv cap",
        len(verify_prompt) < 8191,
        f"{len(verify_prompt)} chars",
    )

    # --- stdout slimming: drop heavy diagnostic meta on success only ---
    from main import _slim_result

    success = {
        "ok": True,
        "content": "evidence",
        "digest": {"summary": "x"},
        "meta": {
            "args": ["opencode", "run", "huge prompt"],
            "stderr": "kilobytes of opencode logs",
            "bootstrap": {"args": ["..."]},
            "policy": {"verify_mode": "delegated"},
            "session_reset": False,
            "job_id": "job_1",
        },
    }
    slim = _slim_result(success)
    report.check(
        "slim: success drops heavy meta (args/stderr/bootstrap)",
        all(k not in slim["meta"] for k in ("args", "stderr", "bootstrap")),
    )
    report.check(
        "slim: success keeps content, digest, and actionable meta (policy/session_reset)",
        slim["content"] == "evidence" and "digest" in slim
        and slim["meta"]["policy"]["verify_mode"] == "delegated"
        and slim["meta"]["job_id"] == "job_1",
    )
    failure = {"ok": False, "content": "boom", "meta": {"stderr": "trace", "args": ["x"]}}
    report.check(
        "slim: failure passes through untouched (stderr/args = diagnosis)",
        _slim_result(failure) == failure,
    )


def installer_checks(report: Report) -> None:
    """Install into a throwaway HOME. Installing onto the machine that produced dist/
    only ever proves idempotency — it never exercises the create path a new user hits."""
    print("\n[INSTALLER] against a throwaway HOME")

    if not (REPO_ROOT / "dist" / "manifest.json").exists():
        report.record("installer dry run", SKIP, "dist/ not extracted")
        return

    fake_home = Path(tempfile.mkdtemp(prefix="e2e-home-"))
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT), "PYTHONUTF8": "1",
           "USERPROFILE": str(fake_home), "HOME": str(fake_home)}
    try:
        dry = subprocess.run(
            [sys.executable, str(REPO_ROOT / "install.py")],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        report.check("dry run exits clean", dry.returncode == 0, f"rc={dry.returncode}")
        report.check("dry run writes nothing", not (fake_home / ".claude").exists())
        report.check("dry run says so", "DRY RUN" in dry.stdout)

        applied = subprocess.run(
            [sys.executable, str(REPO_ROOT / "install.py"), "--apply"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        report.check("apply exits clean", applied.returncode == 0, f"rc={applied.returncode}")

        claude_md = fake_home / ".claude" / "CLAUDE.md"
        report.check("CLAUDE.md installed", claude_md.exists())
        skills = list((fake_home / ".claude" / "skills").glob("*.md"))
        report.check("skills installed", len(skills) >= 10, f"{len(skills)} file(s)")
        settings = fake_home / ".claude" / "settings.json"
        report.check("settings.json installed", settings.exists())

        if settings.exists():
            try:
                loaded = json.loads(settings.read_text(encoding="utf-8"))
                report.check("settings.json is valid JSON", True, f"{len(loaded)} key(s)")
                hook_text = json.dumps(loaded.get("hooks", {}))
                report.check(
                    "hook path resolved to this HOME",
                    "{{HOME}}" not in hook_text,
                    "placeholder left unresolved" if "{{HOME}}" in hook_text else "",
                )
            except json.JSONDecodeError as exc:
                report.check("settings.json is valid JSON", False, str(exc))

        leftover = [
            str(p.relative_to(fake_home))
            for p in fake_home.rglob("*")
            if p.is_file() and "{{HOME}}" in p.read_text(encoding="utf-8", errors="ignore")
        ]
        report.check("no unresolved placeholders", not leftover, str(leftover[:3]))

        second = subprocess.run(
            [sys.executable, str(REPO_ROOT / "install.py"), "--apply"],
            capture_output=True, text=True, encoding="utf-8", errors="replace", env=env,
        )
        report.check(
            "re-install is idempotent",
            "replace: " not in second.stdout and "create: " not in second.stdout,
            "second apply reported writes" if "create: " in second.stdout else "",
        )
    finally:
        shutil.rmtree(fake_home, ignore_errors=True)


def delegated_checks(report: Report, session_id: str) -> None:
    """Real opencode calls. Minutes and real quota — only via --full."""
    print("\n[DELEGATED] real opencode — costs quota")
    from core.contract import REQUIRED_FIELDS

    script = REPO_ROOT / ".workflow" / ("run.ps1" if os.name == "nt" else "run.sh")
    if not script.exists():
        report.record("delegated commands", SKIP, "no .workflow/run script")
        return

    # sweep is local and returns a git-diff report, not delegated evidence.
    cases = [
        ("explore", "Sebutkan entry point utama. Maksimal 3 baris.", "evidence"),
        ("sweep", "scan git diff, identify impact", "local-report"),
    ]
    for command, task, contract in cases:
        runner = (["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
                  if os.name == "nt" else [str(script)])
        result = subprocess.run(
            [*runner, command, task, session_id],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            cwd=str(REPO_ROOT),
        )
        payload = _json_from(result.stdout or "")
        if payload is None:
            report.record(f"{command} returns JSON", FAIL, "no JSON in output")
            continue
        if not report.check(f"{command} returns ok", payload.get("ok") is True,
                            str(payload.get("meta", {}).get("error_type", ""))):
            continue
        content = (payload.get("content") or "").lower()

        if contract == "evidence":
            report.check(f"{command} carries evidence markers",
                         any(m in content for m in ("[evidence]", "[digest]", "grounded:")))
            for field in REQUIRED_FIELDS.get(command, ()):
                report.check(f"{command} has required field '{field}'", field in content)
        else:
            report.check(
                f"{command} reports changed files",
                any(k in content for k in ("changed", "diff", "modified", "no changes")),
                "local git report, not an evidence block",
            )


def main() -> int:
    parser = argparse.ArgumentParser(description="agent-workflow end-to-end checks")
    parser.add_argument("--full", action="store_true",
                        help="also run delegated commands (minutes + real quota)")
    parser.add_argument("--session", default="e2e-session", help="session id for delegated runs")
    args = parser.parse_args()

    report = Report()
    local_checks(report)
    integrity_checks(report)
    installer_checks(report)
    if args.full:
        delegated_checks(report, args.session)
    else:
        report.record("delegated commands", SKIP, "opt-in via --full")

    total = len(report.rows)
    passed = total - report.failed - report.skipped
    print("\n[E2E REPORT]")
    print(f"  {passed} passed | {report.failed} failed | {report.skipped} skipped "
          f"({total} checks)")
    if report.skipped:
        print("  skipped checks are NOT passes — see the reasons above")
    return 1 if report.failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
