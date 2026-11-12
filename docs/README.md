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

The current research pin is Exoma `1.0.2` at `38fb1df`. The
[2026-11-12 staging receipt](evaluation/exoma-staging-2026-11-12/README.md) records its
technical acceptance and a subsequent installed-host recheck: both doctor and the
installation check report READY. Its later live-session record confirms an active
Codex preflight hook, a successful delegated documentation review and a runtime-run check. The
complete formal plan/approval/execute/verify lifecycle remains unverified.

The [Exoma regression handoff](evaluation/exoma-handoff-2026-11-10/README.md) separates
historical parser/gate probes from reported native source results for the earlier
`a965cf20` pin. Its public receipt records the 16-test portable result and native
suite outcomes; real-provider E2E and deployed hooks on that SHA remain unverified
by those records. It does not establish real-task effectiveness.

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
