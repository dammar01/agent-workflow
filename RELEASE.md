# Staging procedure

This repository releases nothing. The runtime is released from
[Exoma](https://github.com/dammar01/Exoma) (its `RELEASE.md`); before an Exoma version is
tagged, its candidate commit is staged here (Exoma `RELEASE.md` step 6b). Every step below
is a command that already exists in the repo — this file only fixes their order and makes
skipping one visible.

## What pins the runtime

`pyproject.toml` names one Exoma commit as a git dependency, and `uv.lock` records what it
resolved to. That pin is the only place the runtime under study is chosen: the suites, the
harnesses and the staging run all import or invoke that Exoma, never a copy in this tree.

Current candidate: `b39cc7acbaaefc206b997d12aaab061def49ba3a` (Exoma `1.0.2`).
The [2026-11-13 staging receipt](docs/evaluation/exoma-staging-2026-11-13/README.md)
records checks against this exact pin and any remaining staging limits.

Previous candidate: `38fb1df4f7d053d7cad1abe1a38e0f0cb9707b41` (Exoma `1.0.2`).
The [2026-11-12 staging receipt](docs/evaluation/exoma-staging-2026-11-12/README.md)
records seven passing default suites, research checks, candidate bundle integrity,
successful Exoma CI and a real Codex E2E run. The pin and receipt were committed in
`066dbfdeefdcab3303f904e183275c7f21f6dc7b`. A subsequent
[installed-host recheck](docs/evaluation/exoma-staging-2026-11-12/README.md#installed-host-recheck-2026-11-12)
reports Exoma `1.0.2`, doctor READY and installation READY, including the installed
Codex auto-review context. The later
[live-session observations](docs/evaluation/exoma-staging-2026-11-12/README.md#live-session-observations-2026-11-12)
confirm an active Codex preflight hook, a successful delegated documentation
review and a runtime-run research check. The complete formal
plan/approval/execute/verify lifecycle remains unverified.
[DEC-086](docs/research/decisions/DEC-086-exoma-a965cf20-has-identity-specific-staging-evidence.yaml)
preserves source results for `a965cf20`; DEC-084 concerns the historical `1071c2f` pin.

## Staging a candidate

1. **Candidate is green in Exoma.** The commit is on Exoma `main` and its CI (including the
   wheel install in the `dist` job) passed.

2. **Move the pin.** Edit the commit in the `exoma @ git+https://...@<commit>` line of
   `pyproject.toml`, then:

   ```
   uv lock
   uv sync --locked
   ```

3. **Run the suites.** The harness check comes first by design: the measurement tools reach
   into Exoma internals, and an import that broke names the harness instead of failing
   halfway through a measurement.

   ```
   uv run python tests/run.py --keep-going
   uv run python tools/maintain/check_research.py --git
   ```

   When `docs/research-drafts/` exists locally, also run `check_research.py --drafts`: CI has
   no drafts to check.

4. **Delegated end-to-end run.** Real provider, real quota:

   ```
   uv run python -I tools/e2e/e2e.py --full --provider <opencode|codex|agy|claude> --model <id> --report-json staging-report.json
   ```

   One provider is the gate; running every provider the pin ships is recommended, since a
   pin can change each adapter. The model is per provider, so that run takes pairs, and a
   provider left without one fails before any call:

   ```
   uv run python -I tools/e2e/e2e.py --full --all --model opencode=<id>,codex=<id>,agy=<id>,claude=<id> --report-json staging-report.json
   ```

   The model is required: the seed Exoma ships leaves it unset, and the run refuses before
   any call rather than failing at the provider.

   Skipping it means the candidate never ran a real delegated call here — say so in the
   report back to Exoma rather than leaving it implied.

   `-I` avoids inheriting a caller's `PYTHONPATH` or user-site package. The harness still
   requires the manifest, lock and loaded installation metadata to name the same Git pin
   before dispatch. This is source-identity metadata, not a byte-level package audit.
   Every requested provider/model is checked before any dispatch.

   Full success requires git setup, init, provider selection, delegated `explore` and
   local `sweep` for each requested provider. A skipped/missing required stage, malformed
   payload, nonzero process or timeout fails the run. Cached evidence does not satisfy
   the delegated check. Without `--full` the output is informational `not_run` and spends
   no quota; it is not provider acceptance.

   `--setup-timeout` defaults to 120 seconds per setup process and
   `--delegated-timeout` to 900 seconds per runner process. These configurable engineering
   limits are not measured latency claims. A timeout bounds the owned process tree;
   cleanup of detached runtime jobs is not independently established.

   The optional JSON receipt names expected/observed pin, interpreter/package location,
   requested stages, attempted outcomes, exit codes, timeout categories and coverage
   limits. It does not export inherited environment or raw provider output.

5. **Report back.** A failure is fixed in Exoma and the candidate is re-staged from step 1.
   Runtime defects are fixed in Exoma; harness defects are fixed here and the checks
   rerun. The installed dependency is never patched to make a candidate pass. When everything
   passes, commit the moved pin; Exoma then tags.

## Experiments and the pin

An experiment records the Exoma version it ran against in its own `version_under_test`
(`docs/research/schema.yaml`), not this repository's pin at the time it is read. Records
that ran before the move to Exoma name an agent-workflow version (3.9.0 and earlier) and
stay as they are. Research validation is not a staging gate: a candidate may be staged with
records `observed` or `not_validated`, provided their disposition says so honestly
(`docs/research/CONTRACT.md` §14).

## What CI already covers

`.github/workflows/ci.yml` runs step 3 on every push and pull request, on Linux and Windows,
against the commit `uv.lock` records. The delegated run (`.github/workflows/e2e-full.yml`) is
manual: it exists to spend quota deliberately, not on a schedule.

The seven default suites include quota-free `e2e` staging regressions. The test entry point
restarts in isolated Python mode so a calling Exoma runtime's `PYTHONPATH` cannot substitute
its global package for the project's installed dependency. Local passes do not establish
remote CI outcomes or a real provider run.

## Separate host acceptance

CLI staging covers delegated exploration and local sweep. It does not deliver host events
or verify the complete plan/approval/execute/verify lifecycle. For the actual host to be
used, record a separate disposable-project session:

1. Identify the package and installed hook, and observe that the host loaded/trusted it.
2. Record a plan and observe rejection of execution without approval.
3. Obtain a genuine user approval event, then observe scoped execution.
4. Record execute-end and verification outcomes with sanitized evidence references.

A checklist, fixture-written token or portable policy test is not proof of live user
approval. Runtime/host defects go to Exoma. Host identity and loading not observed remain
`not_verified`. The [engineering checkpoint](docs/evaluation/README.md#engineering-readiness-checkpoint-2026-11-11)
keeps these results separate from local regression and scientific evidence.
