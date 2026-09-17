# Skill: upgrade
description: Refresh .workflow in place while preserving project state and sessions.

## Trigger
/.upgrade

## Run (local)
Resolve the workflow entry point in this order:
1. `$AGENT_PATH` / `$env:AGENT_PATH` — the build the user points at.
2. Existing `.workflow/config.json` → `runtime.main_py_path`, only when AGENT_PATH is unset.
   It names the build that WROTE the workspace, which may be an old clone.
3. If neither points to a file, stop and ask for the agent-workflow repository path.
The runtime then prefers the build actually running the command, so an upgrade always
repoints the workspace at the code doing the upgrade.

Windows: python "<main.py>" --command upgrade --work-dir "<work_dir>" --pretty
POSIX:   python3 "<main.py>" --command upgrade --work-dir "<work_dir>" --pretty

Upgrade refuses while delegated jobs are active. On a workspace still on the 3.6 layout it
first MIGRATES: backup to `.workflow/data/backups/<stamp>/`, internal files moved into
`.workflow/data/`, old leftovers removed, `config.json` stripped to overrides (values equal to
a default and retired keys removed), a pre-list `e2e/secrets.json` converted. A failure puts
everything back (copy kept as `.workflow/migration-backup-<stamp>/`). Then it regenerates
runner scripts, repoints tool paths and restamps config. Sessions and history are preserved.
It does not call second_agent or run verification.

## Output
[UPGRADE]
from: <installed tool/config versions>
to: <current tool/config versions>
scripts: regenerated | unchanged
migration: <moved N items into .workflow/data/, backup <path> | not needed>
config: <overrides kept; removed: <keys> | unchanged>
secrets: <converted | unchanged>
stray: <unrecognised files left at .workflow root | none>
sessions: preserved
status: READY | BLOCKED (<active job/error>)
