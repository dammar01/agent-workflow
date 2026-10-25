# agent-workflow — repository development contract

Applies only when developing this repository. This repository is research and staging;
the runtime it studies is Exoma (https://github.com/dammar01/Exoma), pinned by commit in
`pyproject.toml`. It carries no copy of the runtime: a runtime change, or a failure found
while staging, is made in Exoma, never patched here. Nothing here ships to user projects.

## Research boundary

`docs/research/CHARTER.md` is the research boundary: agent delegation is studied as resource
allocation (when one more delegated execution is still worth its tokens, latency, human
intervention and coordination), not as maximizing agents, autonomy or feature parity with
another harness. Before proposing a major architectural addition, state the fields of its §12
(research question, hypothesis, variable changed, experiment enabled, expected measurement,
why existing mechanisms are insufficient, complexity introduced). A change that cannot answer
them is not research-driven development.

## Research and decision records

Full contract: `docs/research/CONTRACT.md`. Scope of "significant change" is defined there
(runtime architecture, orchestration, routing, providers, evidence, facts/knowledge, graph,
verification, policy, prompt contracts, recovery, performance).

For a significant architecture or behavior change:

1. Inspect `docs/research/` before implementation.
2. Determine whether an existing hypothesis or decision applies, and follow or revise it.
3. Do not invent literature provenance. Unknown origin is recorded as `provenance: unknown`.
4. After implementation, create or update the corresponding record (`DEC`, `H`, `EXP`,
   `CASE`, or `ARC`) as YAML from the `_TEMPLATE.yaml` in its directory
   (`docs/research/schema.yaml`, CONTRACT.md §16). A `DEC` names its commit; `H`/`EXP` never
   do. Run `python tools/maintain/check_research.py --write-inventory` before committing.
5. Record validation status and limitations. Implemented is not validated.
6. Do not include confidential real-world information: no transcripts, company names,
   internal URLs, customer data, or proprietary code. Abstract and sanitize.

Only records that were carried out and have a recorded result are tracked in
`docs/research/`; they are frozen except to add a result or correct a fact against its
source. Proposed, planned, or untested records go to the git-ignored
`docs/research-drafts/` and are referenced from tracked files by ID only, never linked
(`docs/research/CONTRACT.md` §15).

Research records stay in `docs/research/` (or `docs/research-drafts/`). Never write them into `docs/project-knowledge/`:
the runtime injects that directory into exploration and reasoning prompts, and only `promote-write` may
write there.

## Documentation layers

Put content in the right layer (index: `docs/README.md`):

- `docs/research/` — why: evidence, hypotheses, decisions, with provenance and status.
- `docs/evaluation/` — the controlled benchmark and observed real use, kept apart.
- How the runtime behaves (reference, runtime contracts, architecture, installation, team
  guide) is Exoma's documentation. Link there; never restate command flags, config keys,
  provider security details, or storage layouts here.

## Staging surface

- No releases, no version stamp, no manifest: `pyproject.toml` `version = "0"` is required
  by the `[project]` table and never bumped.
- The runtime under study is the Exoma commit in `pyproject.toml`, resolved in `uv.lock`.
  Moving it follows `RELEASE.md` (staging procedure); harnesses run through `uv run`.
- Harnesses import Exoma internals (`exoma.core.*`, `exoma.utils.*`). A new harness goes in
  `tests/checks/harnesses.py` `HARNESSES`, so a moved pin that breaks it fails by name.
- An experiment pins the Exoma version it ran against in `version_under_test`; records that
  ran before the move name an agent-workflow version and stay as they are.
- `CHANGELOG.md` is the closed runtime history up to 3.9.0; it gains no rows. `prompt/` is a
  historical archive ending at v3.7.2.
- Benchmark method is an `H` + `EXP` record in `docs/research/`; there is no `bench/`
  harness any more (removed in 3.7.3, DEC-011).
