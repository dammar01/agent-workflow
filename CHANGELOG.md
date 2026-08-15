# Changelog

Full release notes live one directory per version under `prompt/`. This file is the index
and the place to look first; it does not duplicate the notes.

Release procedure: `RELEASE.md`.

| Version | Notes | Theme |
|---------|-------|-------|
| 3.4.5 | [prompt/v3.4.5/changelog.md](prompt/v3.4.5/changelog.md) | Release stability: CI, test runner, release procedure; agy provider behind an explicit opt-in |
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

Work on `dev` that no released version's notes describe yet.

**Workflow contracts** (`core/contracts.py`). `TaskSpec`, `RouteDecision`, `EvidenceBundle`,
`VerificationReport`, `UsageRecord` as stdlib dataclasses with `to_dict()`/`from_dict()`.
Additive: `make_ok`/`make_error` still return the same plain dicts, so no adapter, call
site, or test fixture changed shape. `correlation_id` ties a plan, its execute, and its
verify together — without it, "was this right first time" had no subject to be true of.

**Telemetry** (`core/telemetry.py`, `python main.py --command report`). Every delegated
call appends a `UsageRecord` to `.workflow/usage.jsonl`; metrics are derived at read time
rather than counted at write time, so a better definition re-reads history instead of
invalidating it. Reports cost per accepted task (accepted = a verify whose derived verdict
is `pass`), premium context avoided, time to completion, first-pass correctness, rework,
security and test pass rates. Every rate carries its own denominator, and unmeasured calls
are named rather than averaged away.

**Governance** (`core/governance.py`). Provider allowlist enforced in `Router.route()`,
before anything spawns. Per-session token ceiling (`session_token_budget`, off by default)
refusing with a new `budget_exceeded` error type. Per-command tool policy, declared in the
prompt and recorded in the audit trail — a declaration, not a sandbox; the enforcing
boundary is still the provider's own permission config. Audit trail at
`.workflow/audit.jsonl`, covering delegated calls only.

**Verified Graphify** (`core/graph_meta.py`, `python main.py --command graph-meta`).
Graphify is an external CLI, so provenance rides in a sidecar keyed by node id rather than
in `graph.json`. Records commit SHA and a per-node anchor hash; verification distinguishes
a line that MOVED from a line that CHANGED, which whole-graph mtime staleness could never
do. `graph_index.subgraph()` returns a confidence-ordered n-hop neighbourhood instead of
the whole graph, and `leads()` now surfaces the edge confidence mix that has always driven
its ranking.

**Fixed: duplicated contract warnings.** `_finalize_verify_result` runs twice on the same
payload by design — the worker finalises what it produced, and `await` finalises the stored
output again on the way back — and its `extend` meant every warning appeared once per pass.
A verify with one declared gap reported it twice, which reads as two problems. Warnings are
now merged on identity, so a second pass adds nothing while entries from other producers
(evidence contract misses, task truncation) survive.

**Reproducibility.** `tests/checks/deps.py` fails the build if shipped code imports
anything outside the stdlib — the point at which a lockfile would become necessary. A
constraints file listing nothing would have documented the claim without testing it.

**Benchmark harness** (`bench/oracle.py`, `bench/aggregate.py`, `bench/corpus.py`). The
parts of BENCHMARK-PLAN.md that this repo can build. Arms A and B still need a driver that
can operate Claude Code sessions, and P0.1 (tokenburn's missing `claude-opus-5` price)
remains an external blocker.
