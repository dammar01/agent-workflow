# Skill: execute
description: Controlled implementation dengan approval gate.

## Trigger
/.execute -y → PROCEED | /.execute → GATE only

## Gate
Tanpa -y → output [EXECUTION SCOPE] → "Tambah -y untuk konfirmasi" → STOP.

## Pre-Execution
[EXECUTION SCOPE] allowed: <files> | forbidden: <files> | reason: <batasan>

## During
ONLY sentuh allowed. Butuh forbidden → STOP → report conflict → minta instruksi.

## Post (baca .workflow/config.json → commands.auto_verify_after_execute)
[EXECUTION RESULT] files_changed | confidence | uncertainties | verification | status
true  → auto-trigger /.verify. status: done|partial|blocked. JANGAN declare done sebelum verify selesai.
false (default) → JANGAN auto-jalankan /.verify (hindari test berat tak diminta). WAJIB:
  verification: not_run
  status: implemented | partial | blocked  ← DILARANG pakai "done"
  lalu tawarkan "/.verify sekarang?"
"implemented" ≠ "done". Tanpa verifikasi kamu TAK TAHU ini bekerja — jangan bilang tahu.
Key ini prompt-only: runtime nol jalur untuk /.execute, tak ada yang menegakkan selain kamu.
