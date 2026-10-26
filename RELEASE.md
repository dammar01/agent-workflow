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
   uv run python tools/e2e/e2e.py --full --provider <opencode|codex|agy> --model <id>
   ```

   The model is required: the seed Exoma ships leaves it unset, and the run refuses before
   any call rather than failing at the provider.

   Skipping it means the candidate never ran a real delegated call here — say so in the
   report back to Exoma rather than leaving it implied.

5. **Report back.** A failure is fixed in Exoma and the candidate is re-staged from step 1.
   Nothing is patched inside this repository to make a candidate pass. When everything
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
