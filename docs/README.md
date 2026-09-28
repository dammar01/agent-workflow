# Documentation

`agent-workflow` documentation is split into three layers. Each answers a different
question, and each has one job.

| Layer | Answers | Audience | Where |
| --- | --- | --- | --- |
| **Team guide** | How and when do I use this? What does a good request look like? | Developers using agent-workflow day to day | [team-guide/](team-guide/README.md) |
| **Research** | Why was it built this way? What evidence and hypotheses stand behind it? | Maintainers, researchers, reviewers of design decisions | [research/](research/README.md) |
| **Reference** | Exactly what does the runtime do? | Anyone who needs precise behavior: commands, config keys, contracts, storage | [reference.md](reference.md), [runtime-contracts.md](runtime-contracts.md), [architecture/](architecture/README.md) |

Supporting documents: [limitations.md](limitations.md), [evaluation/](evaluation/README.md)
(benchmark and observed usage), and the release history: the index in
[CHANGELOG.md](../CHANGELOG.md), per-version notes in [releases/](releases/).

## Rules between layers

- **Reference is authoritative.** When the team guide or research records disagree with the
  reference, the reference describes what the code does; fix the other document.
- **The team guide links, it does not copy.** Command flags, config keys, provider security
  details, and storage layouts stay in the reference. The team guide explains when and why
  to use them and links there.
- **Research records carry provenance and status.** They may say "proposed" or "unknown";
  the team guide and reference describe only what exists. Records are YAML checked against
  [research/schema.yaml](research/schema.yaml) ([research/CONTRACT.md](research/CONTRACT.md) §16).
- **Release notes say what changed, not how it works.** They link to the reference for
  behavior instead of restating it, so there is one description to keep current.
- **Research drafts are local only.** Proposed or untested research lives in the git-ignored
  `docs/research-drafts/`; tracked documents never link there. See
  [research/CONTRACT.md](research/CONTRACT.md) §15.
- **Neither layer is runtime input.** The runtime reads `.workflow/` and
  `docs/project-knowledge/` (written only by `promote-write`), never these documents.
