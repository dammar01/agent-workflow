# Benchmark

There is no benchmark harness in the tree. The 3-arm harness that used to live in `bench/`
(direct Claude, native sub-agents, agent-workflow; system under test frozen at tag
`v3.4.5`) was removed in 3.7.3 (DEC-011).

What it produced before removal, from its own `bench/STATE.md`: a corpus generator whose
15 tasks were never hand-filled and locked, a frozen oracle opened once, one arm-C unit run
end to end, and aggregation code — but no `ledger.jsonl` data. There are no benchmark
results to summarize, and none are reported anywhere in this documentation.

The last tree that contains it is commit `dd70afd`:

```
git show dd70afd:bench/BENCHMARK-PLAN.md
git show dd70afd:bench/STATE.md
git checkout dd70afd -- bench/     # to run it again, outside a release
```

## Where benchmark method lives now

A benchmark is an experiment against a hypothesis, so it is written as one: an `H-XXX`
record states the claim and its success criteria, and an `EXP-XXX` record states the arms,
the metrics with their units, the setup held constant, the version under test, the threats
to validity, and — once run — the results with a data source that can be re-counted
(`docs/research/schema.yaml`, `EXP`). Until an experiment has run it stays a draft in
`docs/research-drafts/` (`docs/research/CONTRACT.md` §15).

Uncontrolled usage telemetry is a different kind of evidence and stays in
[observed-usage.md](observed-usage.md).
