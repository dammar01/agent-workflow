<div align="center">

# agent-workflow

**Research and staging for [Exoma](https://github.com/dammar01/Exoma).**

Hypotheses, experiments, decision records, and the measurement harnesses that study when
delegating work from a primary coding agent to a secondary one is worth its cost.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)

**[Research records](docs/research/README.md)** · [Evaluation](docs/evaluation/README.md) · [Staging](RELEASE.md) · [Exoma](https://github.com/dammar01/Exoma)

</div>

---

## What this repository is

The runtime, CLI, installer, bundled skills and hooks live in
[Exoma](https://github.com/dammar01/Exoma), published to PyPI as `exoma`. Every runtime
change lands there. This repository holds what studies it:

| Here | What it is |
| --- | --- |
| [docs/research/](docs/research/README.md) | Charter, contract, schema, and the tracked records: decisions (`DEC`), hypotheses (`H`), experiments (`EXP`), real cases (`CASE`), research questions (`RQ`) |
| [docs/evaluation/](docs/evaluation/README.md) | The controlled benchmark and observed real use, kept apart |
| [tools/maintain/](tools/maintain/) | The research tooling: record checker, real-use and task-outcome measurement, fact replay (EXP-013), blind-sample export, graph-hook timing |
| [tools/sim/](tools/sim/) | Whole-pipeline flow simulation with a faked provider, no quota |
| [tools/e2e/](tools/e2e/) | The staging run: real delegated calls against the pinned Exoma |
| [tests/](tests/) | Checks for the records and the harnesses, including that every harness still imports against the pin |

Exoma began as a fork of agent-workflow 3.9.0. Code flows one way from there: this
repository depends on Exoma ([pyproject.toml](pyproject.toml) pins one commit) and carries no
copy of the runtime. A failure found here is fixed in Exoma.

---

## What the research measures

Agent delegation is studied as resource allocation: when one more delegated execution is
still worth its tokens, latency, human intervention, and coordination. The boundary is in
[docs/research/CHARTER.md](docs/research/CHARTER.md).

Measured on the maintainer's real use: 97 coding tasks from 167 Claude Code sessions on nine
private projects (Laravel, Next.js and Python codebases), over the two most used stable
releases. A task is solved when the code its session left behind entered `main`, and clean when the
developer did not change that code again within 24 hours.

| Delegated calls per task | Tasks | Solved | Clean | Prompts (median) | Active time (median) | Main-agent final context | Context kept out of the main agent |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 0 | 15 | 8 (53%) | 8 | 5 | 19 min | 108k | 0 |
| 1–2 | 20 | 14 (70%) | 12 | 6 | 33 min | 152k | 170k |
| 3–5 | 28 | 23 (82%) | 15 | 9 | 53 min | 201k | 671k |
| 6+ | 34 | 23 (68%) | 12 | 16.5 | 101 min | 354k | 1.4M |
| **All** | **97** | **68 (70%)** | **47 (48%)** | **9** | **53 min** | **210k** | **574k** |

Features were solved in 46 of 68 tasks and bugs in 19 of 26. Larger tasks drew more calls,
so the table shows where delegation was used, not what it caused; it is one developer, with
no run without the workflow to compare against, and acceptance on `main` is not a correctness
check. Per release, per project, prompt kinds and method:
[real-use benchmark](docs/evaluation/real-use-benchmark.md).

Records carry explicit provenance: a related paper is not presented as the origin of an
implementation unless that is known, and an implementation working in one project is not
presented as a general result. The project does not assume that more orchestration makes a
coding agent better.

---

## Working here

```bash
uv sync                                        # the pinned Exoma plus PyYAML
uv run python tests/run.py                     # records and harnesses
uv run python tools/maintain/check_research.py --git
uv run python tools/e2e/e2e.py --full          # staging: real delegated calls, spends quota
```

Staging a candidate Exoma commit, step by step: [RELEASE.md](RELEASE.md).
[ci.yml](.github/workflows/ci.yml) runs the record check and the suites on Linux and Windows
against the pinned commit, spending no provider quota;
[e2e-full.yml](.github/workflows/e2e-full.yml) is manual because it calls a real secondary
agent.

Using the runtime in a project (install, commands, configuration, security per provider):
[Exoma's documentation](https://github.com/dammar01/Exoma/tree/main/docs).

## License

Apache License 2.0 — see [LICENSE](LICENSE) for the authoritative text.

Use, modification, and distribution are permitted, including commercially, provided the
copyright notice and licence are retained and modified files are marked as changed. The
licence includes an express patent grant that terminates for any party initiating patent
litigation over the work. The software is provided without warranty of any kind. This
summary is not legal advice.

```text
Copyright 2026 dammar01
```
