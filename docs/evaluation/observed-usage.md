# Observed usage (maintainer telemetry)

A usage log from one machine. It is an observation, not a benchmark: nothing here compares
the runtime against doing the same work without it. For the controlled study, see
[benchmark.md](benchmark.md).

## Status line

While you work, the status line reports two numbers:

```text
Second Agent <tokens> / <calls> calls | Saved <tokens>
```

**Second Agent** is everything the delegate read and wrote. **Saved** is the part of that
which never entered the primary agent's context window: the delegate's fresh input (input
minus cache read) plus its reasoning, as the status line computes it. The real-use
benchmark reports the net figure — that fresh input less the answers that came back, without
reasoning — which runs about 3% below the badge
([real-use-benchmark.md](real-use-benchmark.md#main-agent-context)). The snapshot below is
older and counted "Saved" from the digest (answer minus digest), a much smaller quantity;
its figures are not comparable with either.

## Snapshot

Source: the maintainer's usage stream under `.workflow/data/`. It is not committed (`init`
gitignores `.workflow/`), so these numbers cannot be recomputed from the repository.

| Metric | Value |
| --- | --- |
| Delegated calls (work units) | 107 |
| Usage rows (one per provider invocation) | 112 |
| Tokens handled by the second agent | 61,939,405 (≈ 62.0M) |
| ↳ of which cache reads | 58,468,608 |
| Kept out of the primary context ("Saved") | 3,203,403 (≈ 3.2M) |
| Evidence produced by the second agent | 836,812 chars |
| Digest handed to the primary agent | 92,192 chars |
| Share that entered the primary context | 11.0% — 9.1× smaller |
| Mix by command | `verify` 49, `explore` 30, `analyze` 17, `plan` 16 |

What real work produced — tasks that reached `main`, fixes, prompts, and main-agent context
across nine projects — is in [real-use-benchmark.md](real-use-benchmark.md).

## Units

The runtime distinguishes delegated calls (work units) from provider invocations (usage
rows); one call can produce more than one row. The mix by command sums to 112, which
matches the row count rather than the 107 calls, so it is most likely counted per row. The
original breakdown is not in the repository, so this cannot be confirmed here.

## Caveats

- **34 of the 112 rows carry provider-reported token counts.** The rest are `chars // 4`
  estimates, and each row records which in `token_source`. A total mixing the two is partly
  made of estimates.
- **Cache reads are counted, not netted off.** A call that re-read 200k cached tokens still
  handled 200k tokens. That is why the second-agent figure is large and "Saved" is the
  smaller, stricter one.
- **"Saved" is telemetry, not a comparison.** It measures how much text the digest contract
  kept out of the window. It does not say what the same work would have cost without the
  runtime, because nobody ran it that way.
- **One machine, one operator, uncontrolled tasks.** Do not generalize these numbers.
