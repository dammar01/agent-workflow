# Documentation

`agent-workflow` is research and staging for [Exoma](https://github.com/dammar01/Exoma). Its
documentation answers why and how well; what the runtime does is Exoma's documentation.

Start with the page that matches your task:

- **Install or use Exoma:** [official runtime documentation](https://github.com/dammar01/Exoma/tree/main/docs).
- **Assess the evidence:** [evaluation](evaluation/README.md), including measurement limits.
- **Check research status:** [record inventory](research/README.md#record-inventory) and [research questions](research/questions.md).
- **Stage a runtime revision:** [staging guide](../RELEASE.md).

| Layer | Answers | Audience | Where |
| --- | --- | --- | --- |
| **Research** | Why was it built this way? What evidence and hypotheses stand behind it? | Maintainers, researchers, reviewers of design decisions | [research/](research/README.md) |
| **Evaluation** | What did it cost and deliver, in a controlled benchmark and in real use? | The same readers, and anyone weighing the results | [evaluation/](evaluation/README.md) |
| **Runtime** | Exactly what does Exoma do, and how is it installed and used? | Anyone using or changing the runtime | [Exoma docs](https://github.com/dammar01/Exoma/tree/main/docs) |

Staging a candidate Exoma commit here: [RELEASE.md](../RELEASE.md). The runtime's release
history up to 3.9.0: [CHANGELOG.md](../CHANGELOG.md).

The [Exoma regression handoff](evaluation/exoma-handoff-2026-11-10/README.md) preserves the
public parser/gate recheck and synthetic fixtures; it does not establish a later
runtime fix or real-task effectiveness.

Internal campaign administration, review packets and deferred experiments remain local
research drafts. The public record retains sanitized findings, methods and their limits.

## Rules between layers

- **Exoma's reference is authoritative for behavior.** When a document here disagrees with
  it about what the runtime does, the reference describes the code; fix the document here.
- **Link, do not copy.** Command flags, config keys, provider security details, and storage
  layouts stay in Exoma's reference.
- **Research records carry provenance and status.** They may say "proposed" or "unknown".
  Records are YAML checked against [research/schema.yaml](research/schema.yaml)
  ([research/CONTRACT.md](research/CONTRACT.md) §16).
- **Research drafts are local only.** Proposed or untested research lives in the git-ignored
  `docs/research-drafts/`; tracked documents never link there. See
  [research/CONTRACT.md](research/CONTRACT.md) §15.
- **Nothing here is runtime input.** The runtime reads `.workflow/` and
  `docs/project-knowledge/` (written only by `promote-write`), never these documents.
