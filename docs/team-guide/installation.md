# Installation

How to put agent-workflow on your machine, choose how it behaves, and take it off again. The
exact flags, what each file becomes, and the hooks it registers are in the
[reference](../reference.md) ("Install" section); this page is about the decisions.

## What an install touches

agent-workflow lives in two places:

- **Your machine, once.** `install.py` adds the workflow to Claude Code and to the secondary
  agent you picked (opencode, codex or agy): one marked block in each instruction file, the
  workflow's skills, commands and hooks, and a status line.
- **Each project, once per person.** `init` creates `.workflow/` in the project. It holds paths
  from your machine, so it is never committed.

The install is additive. Your own text in `~/.claude/CLAUDE.md` and in each provider's
`AGENTS.md` stays where it is; the workflow owns only the block between its markers. Your own
hooks and settings stay too. Nothing is written until you add `--apply`; without it the
installer prints what it would do.

## Steps

1. Clone the repository somewhere permanent. Projects point back at it, so moving it later
   means running `init` again in each project.
2. Run the installer without `--apply` and read the plan. Then run it again with `--apply`.
   On a terminal it asks which secondary agent to use; if unsure, choose opencode, which
   enforces read-only access. ([Security in the README](../../README.md#security) explains
   what each provider enforces.)
3. Make `AGENT_PATH` point at `main.py` permanently. The installer prints the line for your
   shell, or does it for you if asked.
4. In each project: run `init`, then `doctor`, and continue only when it reports `READY`.
5. Optional: install the browser extra only if you will use `/.verify-browser`. It downloads
   a browser and is not needed for anything else.

## Choose a mode: always on, or only when called

This is the one decision that changes how Claude Code feels day to day.

| | Auto-intent (default) | Command-only |
| --- | --- | --- |
| How you start a workflow command | Plain language ("where is…", "why does…") or `/.<command>` | `/.<command>` only |
| Ordinary chat | Follows the workflow's rules (style, output format, next-step hints) | Follows **your own** instructions; the workflow stays out of it |
| Evidence gate | Enforced by a hook as well as by the instructions | Applies once a delegated command is called |
| Good for | People who use the workflow for most coding work | People with their own Claude Code setup who want the workflow as a tool they pick up |

Pick command-only if you already have a CLAUDE.md you rely on, or if you share the machine's
Claude Code with work that has nothing to do with this workflow. You can switch at any time;
the installer remembers your choice across upgrades.

The secondary agent's instructions are scoped the same way in both modes: they apply only to
calls the workflow makes. Using codex or opencode directly in your own terminal is unaffected.

## Upgrading

Pull the repository and run the installer with `--apply` again. It refreshes the workflow's
block, files and hooks, keeps your choices (mode, provider), removes files an older release
installed that are no longer shipped (unless you edited them), and upgrades the project you
run it from. Run `init`'s upgrade in other projects as the reference describes.

## Undoing

- **The last install went wrong:** roll it back. Every install keeps backups and a receipt,
  and rollback refuses to run if anything changed since, instead of overwriting your edits.
- **You want the workflow gone:** uninstall. It removes the workflow's block from each
  instruction file and keeps your text, removes only the files it installed and you did not
  edit, and takes its hooks and status line out of `settings.json`. An uninstall can itself be
  rolled back. Project `.workflow/` folders, the secondary agent seed and a persisted
  `AGENT_PATH` are left for you to remove.

## Good practice

- Always read the dry run first. The installer writes to configuration every project on the
  machine reads.
- Run `install.py --check` after an upgrade, and `doctor` in a project before relying on it.
- Keep your personal rules outside the workflow's markers; anything inside is replaced on the
  next install.
- One clone per machine. Two clones installing over each other leave hooks pointing at
  whichever ran last.
- When something misbehaves, see [troubleshooting](troubleshooting.md) before reinstalling.
