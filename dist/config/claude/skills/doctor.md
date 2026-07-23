# Skill: doctor
description: .workflow readiness check. 1-call bila .workflow ada, else local check. Local jangan gagal.

## Trigger
/.doctor

## STEP 1 — Bootstrap check
- .workflow/run.ps1|sh ADA → 1-call:
    Windows:   & "<work_dir>\.workflow\run.ps1" doctor "check .workflow readiness"
    mac/linux: "<work_dir>/.workflow/run.sh" doctor "check .workflow readiness"
- .workflow belum ada → STEP 2 (local check). JANGAN gagal, JANGAN simpulkan package missing.

## STEP 2 — Local check (fallback, cek langsung)
.workflow/          : EXISTS | MISSING
.workflow/run.*     : EXISTS | MISSING
.gitignore          : CONTAINS .workflow/ | MISSING
$AGENT_PATH         : SET (<path>, exists) | NOT SET
.workflow/config.json : v3.3.0 (main_py_path set) | old | MISSING
graphify-out/       : EXISTS | MISSING
second_agent MCP    : SAFE | RISK (<server>) | REVIEW (<server>) | NONE — scan opencode config mcp (context7=safe read-only; write/exec/fs/db/browser=risk)

## Output
[DOCTOR REPORT]
source: second_agent (1-call) | claude (local)
checks: <semua item STEP 2 + status>
mcp_second_agent: <verdict + daftar server + classification> — RISK/REVIEW = second_agent lampaui read-only, WAJIB tampil + alasan
status: READY | NEEDS SETUP
actions: <fix per item MISSING/NOT SET + disable/confirm MCP risky> (kosong → "tidak ada — semua OK")
NEEDS SETUP → "Jalankan /.init". $AGENT_PATH NOT SET → set dulu (lihat /.init STEP 1).
