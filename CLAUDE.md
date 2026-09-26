# agent-workflow — repository development contract

Applies only when developing this repository. It is not shipped: the global bundle
installed into user projects is `dist/config/claude/CLAUDE.md`, and nothing here belongs
in it.

## Research and decision records

Full contract: `docs/research/CONTRACT.md`. Scope of "significant change" is defined there
(runtime architecture, orchestration, routing, providers, evidence, facts/knowledge, graph,
verification, policy, prompt contracts, recovery, performance).

For a significant architecture or behavior change:

1. Inspect `docs/research/` before implementation.
2. Determine whether an existing hypothesis or decision applies, and follow or revise it.
3. Do not invent literature provenance. Unknown origin is recorded as `provenance: unknown`.
4. After implementation, create or update the corresponding record (`DEC`, `H`, `EXP`,
   `CASE`, or `ARC`) from the `_TEMPLATE.md` in its directory.
5. Record validation status and limitations. Implemented is not validated.
6. Do not include confidential real-world information: no transcripts, company names,
   internal URLs, customer data, or proprietary code. Abstract and sanitize.

Research records stay in `docs/research/`. Never write them into `docs/project-knowledge/`:
the runtime injects that directory into exploration and reasoning prompts, and only `promote-write` may
write there.

## Documentation layers

Put content in the right layer (index: `docs/README.md`):

- `docs/team-guide/` — how and when to use it, for developers. Links to the reference;
  never copies command flags, config keys, provider security details, or storage layouts.
- `docs/research/` — why: evidence, hypotheses, decisions, with provenance and status.
- `docs/reference.md`, `docs/runtime-contracts.md`, `docs/architecture/` — exact behavior.
  Authoritative: when another layer disagrees, fix the other layer.

A behavior change updates the reference first, then any team-guide page that describes its
use.

## Release surface

- Version lives only in `config/settings.py` `TOOL_VERSION`. Follow `RELEASE.md`; never
  hand-edit a stamped banner or `dist/manifest.json`.
- `README.md` carries the version only in its shields.io badge. Any other version string in
  its prose, or in `docs/reference.md`, fails `tools/maintain/stamp_version.py --check`.
- `bench/` is frozen at SUT v3.4.5. Do not update its paths or version to match HEAD.
- Release notes: `prompt/v<version>/changelog.md` plus a row in `CHANGELOG.md`.
