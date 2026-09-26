# Getting started

## One-time setup (each team member)

Follow [Install in the README](../../README.md#install). In short:

1. Clone the repository to a permanent location.
2. Run the global install once per machine. Do not skip it: it installs the Claude Code
   skills and hooks **and** the permission block for the secondary agent. How much that
   block actually enforces depends on the provider: with `opencode` writes are prevented;
   with `codex` the boundary is unproven; with `agy` changes are only detected afterwards.
3. Run `init` once per project, on your own machine. The generated `.workflow/` contains
   absolute paths from your machine, so it is never committed or shared.
4. Run `doctor` and check that it reports `READY`.

Which secondary agent to choose, and what each one enforces: [Security in the
README](../../README.md#security). If unsure, use `opencode`.

## Day to day

You do not type runtime commands. You talk to Claude Code normally, and it recognizes what
kind of request you are making:

| You ask… | Claude Code runs | What comes back |
| --- | --- | --- |
| "Where is …", "which files handle …" | `explore` | A map: entry points, files, ownership |
| "Why does …", "is it safe to …" | `analyze` | A causal explanation, no code changes |
| "I want to add …", "plan how to …" | `plan` | Steps, risks, open questions, options |
| "Implement it", "go ahead" | `/.execute -y` | Code changes, made by Claude Code itself |
| "Is it correct now?", "check what was just done" | `verify` | A verdict with evidence |

Before running a delegated command, Claude Code prints one line such as
`[INTENT] explore — location question`. If it guessed wrong, press Esc and rephrase, or use
the explicit form (`/.explore`, `/.analyze`, `/.plan`, `/.verify`). The full list of trigger
phrases lives in the shipped Claude Code configuration (`dist/config/claude/CLAUDE.md`,
"Command registry").

## Three things that surprise new users

1. **Delegated requests take time.** The secondary agent actually reads your code. Expect
   anything from under a minute to several minutes for broad analysis or planning. See
   [when-to-use.md](when-to-use.md) for when that is worth it.
2. **Nothing is implemented without your go-ahead.** `/.execute` requires `-y`. A plan with
   open questions stops and asks you first.
3. **Implemented is not verified.** By default `/.execute` does not run verification by
   itself; it reports `implemented` and offers `/.verify`. Whether it chains automatically is
   the `commands.auto_verify_after_execute` setting ([reference](../reference.md)).
