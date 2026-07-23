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

## Post
[EXECUTION RESULT] files_changed | confidence | uncertainties | status: done|partial|blocked
→ Auto-trigger /.verify. JANGAN declare done sebelum /.verify selesai.
