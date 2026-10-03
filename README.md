<div align="center">

# agent-workflow

**An external control plane for coding agents.**

Delegate repository exploration, evidence gathering, and workflow control to a secondary
agent, while reasoning and implementation stay with your primary coding agent.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#requirements)
[![Version](https://img.shields.io/badge/version-3.8.0-informational.svg)](CHANGELOG.md)

<br>

<img src="docs/assets/architecture.png" alt="agent-workflow architecture. The developer briefs the primary agent (Claude Code) with an objective and constraints and receives the plan, verdict, and questions back. The primary agent delegates to the runtime control plane, which handles job, session, and lock, routing, context, and prompt, then sends a bounded task to the secondary agent. The secondary agent reads, searches, and traces the repository and returns candidate evidence; its intended role is read-only, and enforcement varies by provider. The runtime redacts it, checks the contract, shapes a digest with file:line anchors for the primary agent, and reads and writes sessions, facts, evidence, usage, audit, and response artifacts in .workflow/data/ with source anchors and hashes. Evidence candidates are not ground truth." width="900">

</div>

<sub>The primary agent reasons and writes the code; the developer answers open questions and
approves implementation. "Read-only" is the secondary agent's role, and how strictly it is
enforced depends on the provider (see [Security](#security)). Text version, full verified
flow, side paths, and storage: [docs/architecture](docs/architecture/README.md).</sub>

---

## What is agent-workflow?

`agent-workflow` is an external runtime for AI-assisted software development. It separates
work that needs **broad repository inspection** from work that needs the **primary agent's
reasoning and implementation context**.

A coding agent on a mature repository spends much of its effort locating modules, tracing
call sites, and reading conventions before it changes anything. `agent-workflow` moves that
inspection into a separate execution context and returns structured evidence: a digest,
`file:line` anchors, and a reference to the full evidence on disk.

It is **not a replacement** for Claude Code or any other coding agent. It is an additional
control layer around them.

Delegated results are treated as **evidence candidates**, not truth. Every delegated call
returns the same envelope:

```json
{ "ok": true, "content": "...", "meta": {}, "digest": {}, "evidence_ref": {} }
```

The primary agent decides what the evidence means and whether to act on it. A secondary
agent can be incomplete or wrong even when its output looks plausible.

---

## Core responsibilities

| Role | Who | Responsibility |
| --- | --- | --- |
| **Primary agent** | Claude Code (the integration target) | Reasoning, decisions, code changes, and final review |
| **Workflow runtime** | This repository, Python standard library only | Routing, sessions and locks, policy, prompt building, redaction, evidence contracts, digests, persistence, recovery |
| **Secondary agent** | `opencode`, `codex`, or `agy` | Repository exploration, analysis, planning, and verification evidence |

The design keeps code-writing with the primary agent. The secondary agent's role is
read-only; the degree to which each provider **enforces** that differs, and the runtime
states it rather than assuming it. See [Security](#security).

The principle: let the model do the semantic reasoning, and let deterministic software
enforce state, contracts, policy, provenance, and execution control.

---

## When it is useful

The benefit grows with the **breadth** of a task — many files, many touch points.

| Situation | Example question | Command |
| --- | --- | --- |
| Unfamiliar codebase | "Where is the authentication logic?" | `explore` |
| Root-cause investigation | "Why is this endpoint slow?" | `analyze` |
| Cross-module change | "I want to add feature X — what are the steps?" | `plan` |
| Blast radius | "What does the current working tree touch?" | `sweep` |
| Post-change verification | "Is the change that was just made correct?" | `verify` |

It is less attractive when the change is already localized, the relevant file is known,
the task is very small, or you need an instant answer: a delegated call takes from under a
minute to several minutes for broad analysis or planning, because the secondary agent
actually reads the code. These are usage heuristics, not measured thresholds; the
[team guide](docs/team-guide/when-to-use.md) covers choosing per task and how to phrase
requests so the workflow can help.

---

## Install

Steps 1–2 run once per machine; steps 3–4 once per project.

### 1. Clone

```bash
git clone https://github.com/dammar01/agent-workflow.git
cd agent-workflow
```

Keep this directory somewhere permanent — the runtime records its absolute path.

### 2. Install the global configuration

```bash
python install.py --apply --set-env
```

Installs the Claude Code skills and hooks, the **permission block for the delegated agent**
(write/edit denials and the shell allowlist), and persists `AGENT_PATH`. Drop `--apply` for
a dry run; drop `--set-env` to set the variable yourself; pass `--provider`/`--model` to
skip the interactive choice. Reopen the terminal so `AGENT_PATH` takes effect.

> **Skipping this step leaves the delegated agent running without write restrictions.**
> The restrictions it installs are fully enforced only for `opencode`; see [Security](#security).

### 3. Enable it in your project

```bash
python "$AGENT_PATH" --command init --work-dir /path/to/your-project --pretty
```

```powershell
python $env:AGENT_PATH --command init --work-dir "C:/path/to/your-project" --pretty
```

This creates `.workflow/` in the project — your config overrides, provider selection,
entry-point scripts (`run`, `inspect`, `check`), live progress in `current/`, and internal
data (sessions, evidence, facts, usage, audit) in `data/` — plus an `opencode.json` that
denies secret-file reads. `.workflow/` is added to `.gitignore` automatically.

> **Do not commit `.workflow/`.** The generated scripts bake in absolute paths from the
> machine where `init` ran. Each team member runs this step themselves.

### 4. Verify

```bash
cd /path/to/your-project
.workflow/run.sh doctor          # Windows: .workflow\run.ps1 doctor
```

`doctor` must report **`READY`**. `NOT_READY` means an entry point is broken — read
`recommended_fixes`. `python install.py --check` audits the global bundle for drift.

### Quick start

Ask in natural language; the primary agent routes it:

```text
you:          where is the authentication logic in this project?
Claude Code:  [INTENT] explore — location question
              (runs .workflow/run.ps1 explore "...")
```

Or call the runtime directly:

```bash
.workflow/run.sh explore "find the authentication entry point" "<SESSION_ID>"
```

The generated runner refuses a delegated call without a session id: without one,
concurrent sessions would share an identifier and overwrite each other's state.

| Command | Type | Purpose |
| --- | --- | --- |
| `init` / `upgrade` | local | Enable the runtime in a project / refresh it after the tool is updated |
| `doctor` | local | Readiness check; writes a report |
| `sweep` | local | Scan the working tree for changes |
| `clean` | local | Prune jobs, stale facts, and old sessions |
| `explore` | delegated | Code map, entry points, ownership |
| `analyze` | delegated | Causal analysis; no code changes |
| `plan` | delegated | Evidence-backed implementation steps |
| `verify` | delegated | Verdict on completed work, with evidence — produced by the secondary agent, not an independent check |
| `verify-browser` | delegated | Verify a running app in a real browser: the secondary agent drafts the scenario and reviews the evidence; a runtime player drives Playwright |
| `promote-validate` → `promote-verify` → `promote-write` | local | Turn verified evidence into a Git-tracked knowledge document |

Implementation itself (`/.execute` in Claude Code) is a primary-agent skill, not a runtime
command. Asynchronous job commands and the remaining local commands are in the
[full reference](docs/reference.md#command).

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `doctor` reports `NOT_READY` / `run_script_drift` | Tool updated, workspace scripts not | `.workflow/run.sh upgrade` |
| Provider not found | The agent CLI is not on `PATH` | Install it, then re-check `--version` |
| Commands fail after moving the repository | Baked absolute paths are stale | Run `upgrade` from the new location |

---

## Security

The secondary agent reads your source code. How strictly it is confined depends on the
provider you select.

| | `opencode` | `codex` | `agy` |
| --- | --- | --- | --- |
| Write/edit denied by config | **Yes** | Not enforceable | No |
| Secret-file reads denied | **Yes** | Declared, not enforced | No |
| Shell commands restricted | **Yes**, read-only git allowlist | No | No |
| Workspace mutation handling | Prevented | Prevented for writes | Detected after the fact |

1. **The write boundary lives in the global configuration** installed by step 2. The
   project-local `opencode.json` covers secret-file *reads* only.
2. **`codex` passes filesystem permission flags on every call, but their runtime effect is
   unverified** against the current CLI. Treat the boundary as unproven.
3. **`agy` runs with permissions skipped**, guarded by detection rather than prevention: it
   diffs `git status` around each call, so `.gitignore`d files — including `.env` — are
   invisible to it.

For projects holding secrets the secondary agent must not read, use `opencode`.

## Requirements

| Requirement | Required | Notes |
| --- | --- | --- |
| **Python 3.10+** | Yes | The runtime needs no third-party packages. Optional browser verification uses Playwright (`requirements-e2e.txt`) |
| **A secondary-agent CLI** | Yes | `opencode` (recommended), `codex`, or `agy`, on `PATH` |
| **git** | Recommended | Used by `sweep`, `syntax` verify mode, and the workspace guard |
| **Claude Code** | Recommended | The primary integration target |

---

## Research & evaluation

> `agent-workflow` explores whether externalizing repository exploration, evidence
> handling, and execution control from a primary coding agent can improve
> software-engineering workflows on complex real-world repositories.

It is developed as an open engineering and research artifact. Design
decisions, hypotheses, experiments, and sanitized real-world observations are recorded in
[docs/research/](docs/research/), with explicit provenance: a related paper is not presented
as the origin of an implementation unless that is known, and an implementation working in
one project is not presented as a general result.

The project does not assume that more orchestration makes a coding agent better. The
controlled benchmark and the maintainer's usage telemetry are kept apart in
[docs/evaluation/](docs/evaluation/), and telemetry is read as evidence for specific
conditions, not as a performance claim.

---

## Documentation

Documentation is layered: the team guide says **how and when**, research says **why**, and
the reference says **exactly what the runtime does** — see [docs/](docs/README.md).

| Document | Contents |
| --- | --- |
| [docs/team-guide](docs/team-guide/README.md) | For developers: getting started, when to use it, task framing, examples, troubleshooting |
| [docs/architecture](docs/architecture/README.md) | Verified request flow, components, and storage |
| [docs/reference.md](docs/reference.md) | Complete technical reference: configuration, jobs, fact store, evidence reuse, verify modes, sessions, tests, CI *(Bahasa Indonesia)* |
| [docs/runtime-contracts.md](docs/runtime-contracts.md) | Which `.workflow/config.json` keys the runtime obeys, and which it cannot enforce |
| [docs/limitations.md](docs/limitations.md) | Known limitations |
| [docs/research](docs/research/README.md) | Research method, contract, and records |
| [docs/evaluation](docs/evaluation/README.md) | Benchmark and observed usage |
| [CHANGELOG.md](CHANGELOG.md) / [RELEASE.md](RELEASE.md) | Release history and procedure |

[ci.yml](.github/workflows/ci.yml) runs the version stamp, manifest, stdlib-only gate, and
test suites on every push on Linux and Windows, spending no provider quota;
[e2e-full.yml](.github/workflows/e2e-full.yml) is manual because it calls a real secondary
agent.

---

## Limitations

`agent-workflow` adds an execution layer, and with it additional latency, additional
provider calls and compute, new failure modes, provider-specific security limits, and
maintenance complexity. It is not preferable for every task; the project is exploring
**when** external orchestration pays off. Full list: [docs/limitations.md](docs/limitations.md).

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
