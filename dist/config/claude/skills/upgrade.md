# Skill: upgrade
description: Refresh .workflow in place while preserving project state and sessions.

## Trigger
/.upgrade

## Run (local)
Resolve the workflow entry point in this order:
1. Existing `.workflow/config.json` → `runtime.main_py_path`.
2. `$AGENT_PATH` / `$env:AGENT_PATH` when the config is missing.
3. If neither points to a file, stop and ask for the agent-workflow repository path.

Windows: python "<main.py>" --command upgrade --work-dir "<work_dir>" --pretty
POSIX:   python3 "<main.py>" --command upgrade --work-dir "<work_dir>" --pretty

Upgrade refuses while delegated jobs are active. It regenerates runner scripts, repoints
tool paths, and backfills config additively. Existing values and `sessions/` are preserved.
It does not call second_agent or run verification.

## Output
[UPGRADE]
from: <installed tool/config versions>
to: <current tool/config versions>
scripts: regenerated | unchanged
config: <keys added | unchanged>
sessions: preserved
status: READY | BLOCKED (<active job/error>)
