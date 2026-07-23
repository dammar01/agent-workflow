# Skill: sweep
description: Git diff scan → impact evidence (1-call). Fallback: git diff langsung.

## Trigger
/.sweep

## Run (1-call)
Windows:   & "<work_dir>\.workflow\run.ps1" sweep "scan git diff, identify impact" "<MAIN_SESSION_ID>"
mac/linux: "<work_dir>/.workflow/run.sh" sweep "scan git diff, identify impact" "<MAIN_SESSION_ID>"
<MAIN_SESSION_ID> dari [SESSION BINDING], WAJIB arg ke-3 (isolasi concurrent).
ok:false / run script hilang → fallback: `git diff HEAD`, `git status` langsung, source: claude (direct).

## Output [SWEEP RESULT]
Relay digest. changed_files | impact | risks | uncertainties (kosong → alasan).

## End
"Impact selesai. Lanjut /.verify atau /.plan?"
