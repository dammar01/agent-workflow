# prompt/ — release notes and historical prompt snapshots

Nothing in this directory is read by the runtime, the installer, or the release tooling.
It is an archive, indexed by [`CHANGELOG.md`](../CHANGELOG.md).

| Path | Contents |
| --- | --- |
| `v<version>/changelog.md` | Release notes, one directory per version from 3.2.1 through 3.7.2. Closed: from 3.7.3 on, release notes are written in `CHANGELOG.md` (`RELEASE.md`, step 4). |
| `v0.0.0.md` … `v3.1.2.md` | Full setup-prompt snapshots from before release notes were split out. |
| `v3.2.0/` … `v3.3.1/` `main_agent.md`, `second_agent.md` | Prompt snapshots for the main and second agent at that version. |
| `UPDATE_CONFIG_PROMPT.md` | Incremental migration prompt from v3.1.1 to v3.1.2, referenced by those two snapshots. |

These files describe the project **as it was at that version**. Module paths inside them
(for example `core/workflow_runtime.py`, `core/fact_store.py`, `core/executor.py`) refer to
the source layout of that release and are intentionally left unchanged.

The prompts that are actually shipped and installed today live in `dist/config/`:
`dist/config/claude/` for the primary agent and `dist/config/{opencode,codex,agy}/` for the
secondary agent.
