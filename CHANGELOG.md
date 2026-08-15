# Changelog

Full release notes live one directory per version under `prompt/`. This file is the index
and the place to look first; it does not duplicate the notes.

Release procedure: `RELEASE.md`.

| Version | Notes | Theme |
|---------|-------|-------|
| 3.4.4 | [prompt/v3.4.4/changelog.md](prompt/v3.4.4/changelog.md) | Second provider: codex adapter, provider registry, read-boundary findings |
| 3.4.2 | [prompt/v3.4.2/changelog.md](prompt/v3.4.2/changelog.md) | — |
| 3.4.1 | [prompt/v3.4.1/changelog.md](prompt/v3.4.1/changelog.md) | — |
| 3.4.0 | [prompt/v3.4.0/changelog.md](prompt/v3.4.0/changelog.md) | — |
| 3.3.1 | [prompt/v3.3.1/changelog.md](prompt/v3.3.1/changelog.md) | — |
| 3.3.0 | [prompt/v3.3.0/changelog.md](prompt/v3.3.0/changelog.md) | — |
| 3.2.1 | [prompt/v3.2.1/changelog.md](prompt/v3.2.1/changelog.md) | — |
| 3.2.0 | [prompt/v3.2.0/](prompt/v3.2.0/) | — |
| 3.1.2 | [prompt/v3.1.2.md](prompt/v3.1.2.md) | — |
| 3.1.1 | [prompt/v3.1.1.md](prompt/v3.1.1.md) | — |
| 3.1.0 | [prompt/v3.1.0.md](prompt/v3.1.0.md) | — |
| 3.0.1 | [prompt/v3.0.1.md](prompt/v3.0.1.md) | — |
| 3.0.0 | [prompt/v3.0.0.md](prompt/v3.0.0.md) | — |
| 2.0.0 | [prompt/v2.0.0.md](prompt/v2.0.0.md) | — |
| 0.0.0 | [prompt/v0.0.0.md](prompt/v0.0.0.md) | — |

v3.4.3 was built but never released; its changes are described inside the v3.4.4 notes.

## Unreleased

Work on `dev` that is not covered by any released version's notes. `TOOL_VERSION` still
reads `3.4.4`, so these changes currently ship under a number whose notes do not describe
them — decide the next version before tagging.

- **agy provider.** `adapters/agy_adapter.py`, its bundle in `config/providers.py`, and
  `core/agy_guard.py`. agy has no enforceable read-only mode: `--sandbox` and `--mode plan`
  were both probed and left every write tool enabled, so the adapter passes
  `--dangerously-skip-permissions` and the guard detects writes by diffing the working tree
  instead of preventing them.
- **Interactive provider selection.** `/.provider` and `core/provider_select.py`.
- **Anchor relocation** for facts and evidence staleness checks.
- **Continuation no longer discards the reply it was continuing.** A second agent whose
  answer stopped before `[DIGEST]` was re-prompted for the missing block, and the follow-up
  replaced the first reply wholesale — evidence body and all anchors were thrown away while
  the run still reported `ok:true` and `continuation_recovered:true`.
  `_merge_continuation()` in `core/executor.py` now joins the two.
- **`await` cannot hang indefinitely.** `poll_timeout=0` (the default) left the wait loop
  with no exit when a job stopped advancing without reaching a terminal status. It now
  falls back to the job's own hard runtime ceiling.
- **Failed commands exit nonzero.** `_verify_exit_code` returned 0 for every non-verify
  command regardless of `ok`, so a failed `explore` was indistinguishable from a clean one
  to any caller reading the exit status.
- **A crashed worker returns a structured error.** `run_worker` returned a plain dict with
  no `error_type` and no `next_action`.
- **`dist/config/agy/AGENTS.md`.** The agy bundle declared an instruction file that was
  never written, so installing agy shipped no second-agent contract at all — for the one
  provider whose read boundary is prose and nothing else.
- **Tests moved to `tests/`.** `test_scenario.py` → `tests/scenario.py`, `checks/` →
  `tests/checks/` (they were imported by nothing else), plus `tests/run.py` with a suite
  registry: `--list`, `--only <name>`, `--keep-going`. The scenario suite stays one
  sequence because its assertions genuinely share state; the fourteen standalone checks
  each build their own workspace and can now be run alone. `tests/` rather than `test/`
  because a top-level `test` package shadows CPython's own.
- **CI.** `.github/workflows/ci.yml` runs the version stamp, manifest, test, and e2e gates
  on Linux and Windows. `.github/workflows/e2e-full.yml` runs the paid delegated path on
  manual dispatch only, against a self-hosted runner that has a provider CLI installed.
