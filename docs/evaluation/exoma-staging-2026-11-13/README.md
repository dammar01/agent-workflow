# Exoma b39cc7a staging, 2026-11-13

Candidate: `b39cc7acbaaefc206b997d12aaab061def49ba3a` (Exoma `1.0.2`).
The manifest, generated lockfile and project installation identify this commit.
The installed dependency was not patched.

Technical CLI staging passed on Windows with Codex `gpt-6.1-sol`: 11 checks
passed, zero failed and zero skipped. The initial sandbox attempt timed out;
the retry with network access outside the sandbox passed. Both receipts are
retained. These observations do not isolate the cause of the first timeout.

| Check | Observed result | Boundary |
| --- | --- | --- |
| Lock and installation | `uv lock` and `uv sync --locked`: exit 0 | Full Git SHA pinned in both manifest and lock |
| Default suites | `python tests/run.py --keep-going`: exit 0, seven suites passed | Harnesses, offline E2E, research, research closure, fact replay, real use, blind sample |
| Research integrity | `python tools/maintain/check_research.py --git`: exit 0, 137 records, zero warnings | Tracked research records |
| Local research drafts | `python tools/maintain/check_research.py --git --drafts`: exit 1, one error and six warnings | Existing ignored draft `EXP-021`: `threats_to_validity[3]` parses as a mapping instead of a string; drafts were not changed |
| Exoma CI | [Run 43392423160](https://github.com/dammar01/Exoma/actions/runs/43392423160): completed, success | GitHub API reports this exact SHA on `dev`; individual jobs were not inspected |
| Candidate on main | Not established | `git ls-remote` reported `main` at `38fb1df4f7d053d7cad1abe1a38e0f0cb9707b41`; this receipt does not satisfy the main-branch prerequisite in `RELEASE.md` |
| Initial Codex E2E | [codex-e2e-sandbox.json](codex-e2e-sandbox.json): exit 1, six checks passed, one failed, zero skipped | `explore` timed out at 900 seconds; setup and local sweep passed |
| Codex E2E retry | [codex-e2e.json](codex-e2e.json): exit 0, 11 checks passed, zero failed/skipped | Network access outside the sandbox; fresh delegated `explore` and local sweep, with a 300-second delegated timeout |

Commands used `uv run --no-sync` with the project `.venv` after `uv sync --locked`.
The staging model is Codex `gpt-6.1-sol`, matching the previous receipt.

The successful retry used:

```text
.venv/Scripts/python.exe -I tools/e2e/e2e.py --full --provider codex --model gpt-6.1-sol --session main_agent-workflow_20261113_08344284_0f89 --delegated-timeout 300 --report-json <receipt>
```

The public receipts preserve outcomes and timestamps; their three absolute
installation paths are replaced with `<project>` paths. They contain no raw
provider output or inherited environment.

CLI staging covers delegated `explore` and local `sweep`; it does not establish
deployed hooks, the complete live
plan/approval/execute/verify lifecycle, or scientific validation.
