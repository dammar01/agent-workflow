<div align="center">

# agent-workflow

**Stop burning premium context on reading code.**

A two-agent orchestration runtime that delegates codebase reading and search to a
cheaper agent, so Claude Code spends its context window on reasoning instead of raw
file contents.

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen.svg)](#requirements)
[![Version](https://img.shields.io/badge/version-3.5.3-informational.svg)](CHANGELOG.md)

<br>

<img src="docs/assets/flow.png" alt="agent-workflow architecture: the user asks a question; the main agent detects intent and calls the runtime; the runtime builds context, launches the read-only second agent, validates and redacts the response, then returns a digest with file:line anchors; the main agent reasons, writes the code, verifies, and commits. Only the main agent has write access." width="900">

</div>

---

## What it does

Two agents, one strict division of labour:

| Role | Who | Responsibility |
| --- | --- | --- |
| **main_agent** | Claude Code, Codex, Cursor | Reasoning, decisions, and the **only** party allowed to write files |
| **second_agent** | OpenCode, Codex, or Agy — a cheaper model | Reading and searching the codebase, strictly **read-only** |

Most of what a coding agent does is not reasoning. It is reading files, grepping, and
tracing call sites — mechanical work that does not need a frontier model. That work gets
delegated. The cheaper agent reads forty files; your agent receives a digest plus
`file:line` anchors, and the full evidence stays on disk in `.workflow/` until it is
actually needed.

Every delegated call returns the same envelope, so the caller never has to guess:

```json
{ "ok": true, "content": "...", "meta": {}, "digest": {} }
```

Zero third-party dependencies — the Python standard library is the whole runtime.

**Writing code is never delegated.** All file modifications stay with the main agent,
under your review. That is a design constraint, not a gap.

---

## Measured

From 99 delegated calls logged on the maintainer's machine
(`.workflow/usage.jsonl`; not committed, since `init` gitignores `.workflow/`):

| Metric | Value |
| --- | --- |
| Evidence produced by the second agent | 740,341 chars |
| Digest handed to the main agent | 84,918 chars |
| **Share that entered the premium context** | **11.5% — 8.7× smaller** |
| Premium-context tokens avoided | 152,362 across 93 calls |
| Call mix | `explore` 27, `analyze` 17, `plan` 14, `verify` 41 |

That is a usage log from one machine, not a controlled benchmark: it shows how much text
the digest contract keeps out of the window, not a cost or quality comparison against
working without the runtime. The three-arm benchmark is specified in
[bench/BENCHMARK-PLAN.md](bench/BENCHMARK-PLAN.md) and is **not finished** —
`bench/ledger.jsonl` is still empty.

---

## When it pays off

The benefit scales with the **breadth** of the task — many files, many touch points.

| Task | Example question | Command |
| --- | --- | --- |
| **Mapping unfamiliar code** | "Where is the authentication logic?" | `explore` |
| **Root-cause analysis** | "Why is this endpoint slow?" | `analyze` |
| **Change planning** | "I want to add feature X — what are the steps?" | `plan` |
| **Blast radius** | "What does the current working tree touch?" | `sweep` |
| **Proving results** | "Is the change that was just made correct?" | `verify` |

Good fit: Claude Code daily, a large codebase, sessions that keep hitting the context
limit. Poor fit: under ~50 files (the agent can just hold it), a single-file change whose
location you already know, or anything needing an instant answer — a delegated call takes
tens of seconds to a few minutes, because the second agent genuinely reads the code.

---

## Requirements

| Requirement | Required | Notes |
| --- | --- | --- |
| **Python 3.10+** | Yes | No dependencies to install |
| **A second-agent CLI** | Yes | One of `opencode` (recommended), `codex`, or `agy`, on `PATH` |
| **git** | Recommended | Used by `sweep`, `syntax` verify mode, and the workspace guard |
| **Claude Code** | Recommended | The intended main agent; others work as well |

> **Choose `opencode` if you are unsure.** Only OpenCode has mechanically enforced
> permissions: write and edit tools denied, `.env` and key material unreadable, shell
> restricted to a read-only git allowlist. On `codex` and `agy` those boundaries are not
> machine-enforced — see [Security](#security).

---

## Install

Four steps. Steps 1–2 run once per machine; steps 3–4 once per project.

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

Installs the Claude Code skills and hooks, the **permission block for the delegated
agent** (write/edit denials and the shell allowlist), and persists `AGENT_PATH` for
future shells. Drop `--apply` for a dry run that writes nothing; drop `--set-env` to
skip the environment variable and set it yourself.

On a fresh clone it also seeds `config/second_agent.json` — the machine-wide default every
later `init` copies — and asks which provider and model to put in it. Answer with
`--provider`/`--model` instead to skip the questions; a non-interactive run copies the
shipped example unchanged.

Skipping this step leaves the delegated agent running without write restrictions.
Reopen the terminal so `AGENT_PATH` takes effect.

### 3. Enable it in your project

```bash
python "$AGENT_PATH" --command init --work-dir /path/to/your-project --pretty
```

```powershell
python $env:AGENT_PATH --command init --work-dir "C:/path/to/your-project" --pretty
```

Creates, inside the project:

| Path | Purpose |
| --- | --- |
| `.workflow/config.json` | Settings, plus absolute paths back to the tool |
| `.workflow/second_agent.json` | Provider and model selection; safe to edit |
| `.workflow/run.*`, `inspect.*`, `check.*` | Entry-point scripts (`.ps1` on Windows, `.sh` on POSIX) |
| `opencode.json` | Deny-list of secret files the delegated agent may not read |

`.workflow/` is added to the project's `.gitignore` automatically.

> **Do not commit `.workflow/`.** The generated scripts bake in absolute paths from the
> machine where `init` ran. Each team member runs this step themselves.

### 4. Verify

```bash
cd /path/to/your-project
.workflow/run.sh doctor          # Windows: .workflow\run.ps1 doctor
```

`doctor` must report **`READY`** with zero issues. `NOT_READY` means an entry point is
genuinely broken — read `recommended_fixes` before proceeding. Add
`python install.py --check` to audit the global bundle for drift.

---

## Usage

The main agent invokes the runtime for you from natural language:

```text
you:  where is the authentication logic in this project?

Claude Code:  [INTENT] explore — location question
              (runs .workflow/run.ps1 explore "...")
```

Directly:

```bash
.workflow/run.sh explore "find the authentication entry point" "<SESSION_ID>"
```

```powershell
& ".workflow\run.ps1" explore "find the authentication entry point" "<SESSION_ID>"
```

The session id is **required**. Without it, concurrent sessions fall back to a shared
identifier and overwrite each other's state.

| Symptom | Likely cause | Action |
| --- | --- | --- |
| `doctor` reports `NOT_READY` | Scripts or config have drifted | `.workflow/run.sh upgrade` |
| `run_script_drift` | Tool was updated, scripts were not | Run `upgrade` from that machine |
| Provider not found | The agent CLI is not on `PATH` | Install it, then re-check `--version` |
| Commands fail after moving the repository | Baked absolute paths are stale | Run `upgrade` from the new location |

---

## Commands

| Command | Type | Purpose |
| --- | --- | --- |
| `init` | local | Enable the runtime in a project |
| `upgrade` | local | Refresh a workspace after the tool is updated |
| `doctor` | local | Readiness check; writes a report |
| `sweep` | local | Scan the working tree for changes |
| `clean` | local | Prune jobs, stale facts, and old sessions |
| `explore` | delegated | Code map, entry points, ownership |
| `analyze` | delegated | Causal analysis; no code changes |
| `plan` | delegated | Evidence-backed implementation steps |
| `verify` | delegated | Prove that completed work is correct |
| `promote-*` | local | Turn verified evidence into a Git-tracked knowledge document |

Asynchronous job commands (`submit`, `await`, `status`, `result`), the remaining local
ones (`inspect`, `provider`, `report`, `audit`, `graph-meta`), and the three `promote-*`
stages are documented in the [full reference](docs/reference.md#command).

---

## Security

The delegated agent reads your source code. How strictly it is confined depends entirely
on the provider you select.

| | `opencode` | `codex` | `agy` |
| --- | --- | --- | --- |
| Write/edit denied by config | **Yes** | Not enforceable | No |
| Secret-file reads denied | **Yes** | Declared, not enforced | No |
| Shell commands restricted | **Yes**, read-only git allowlist | No | No |
| Workspace mutation handling | Prevented | Prevented for writes | Detected after the fact |

1. **The write boundary lives in the global configuration** installed by step 2. A project
   initialised on a machine where that step never ran has no write restriction; the
   project-local `opencode.json` covers secret-file *reads* only.
2. **`codex` passes filesystem permission flags on every call, but their runtime effect is
   unverified** against the current CLI. Treat the boundary as unproven.
3. **`agy` runs with permissions skipped**, guarded by detection rather than prevention:
   it diffs `git status` around each call, so `.gitignore`d files — including `.env` — are
   invisible to it.

For projects holding secrets the delegated agent must not read, use `opencode`. Known
limitations: [reference](docs/reference.md#batasan-yang-diketahui).

---

## Documentation

| Document | Contents |
| --- | --- |
| [docs/reference.md](docs/reference.md) | Complete technical reference: configuration schema, asynchronous jobs, fact store, evidence reuse, verify modes, sessions, tests, CI *(written in Bahasa Indonesia)* |
| [docs/runtime-contracts.md](docs/runtime-contracts.md) | Which `.workflow/config.json` keys the runtime actually obeys, and which it structurally cannot enforce |
| [bench/BENCHMARK-PLAN.md](bench/BENCHMARK-PLAN.md) | Three-arm benchmark design; in progress |
| [CHANGELOG.md](CHANGELOG.md) | Release history |
| [RELEASE.md](RELEASE.md) | Release procedure, and which steps CI already covers |

Two GitHub Actions workflows guard the repository:
[ci.yml](.github/workflows/ci.yml) runs the version stamp, manifest, stdlib-only gate and
test suites on every push across both operating systems, spending no provider quota;
[e2e-full.yml](.github/workflows/e2e-full.yml) is `workflow_dispatch` only, because it
calls a real second agent. Neither bumps, tags, nor publishes.

---

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
