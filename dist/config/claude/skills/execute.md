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

### Baca key ini SETIAP kali, jangan dari ingatan
`commands.auto_verify_after_execute` dibaca dari `.workflow/config.json` di akhir TIAP
/.execute. Nilai dari sesi lain, project lain, atau dari ingatanmu TIDAK berlaku.
`true` → chain ke /.verify adalah bagian dari /.execute, bukan langkah opsional sesudahnya.
Berhenti sebelum verify selesai = /.execute yang belum selesai, apa pun isi diff-nya.

### Kalimat yang DILARANG saat verification: not_run
"done" | "selesai" | "sudah jalan" | "berhasil" | "fixed" | "works now" | "aman sekarang"
| "test lolos" (kalau kamu tak menjalankannya) | ✅ sebagai penanda selesai.
Yang BOLEH: "implemented, belum diverifikasi". Perbedaannya bukan gaya bahasa — user
memutuskan langkah berikutnya dari kata itu.

### Kenapa aturan ini prompt-only
`core/runtime/config_defaults.py:22,217` menyatakannya langsung: /.execute nol jalur Python, jadi runtime
tak punya proses yang hidup untuk menegakkan apa pun di sini. Nol exit code, nol marker,
nol gate — pengecekan yang bisa ditulis di Python sudah ditulis, dan yang ini tidak bisa.
Yang berdiri di antara "belum diverifikasi" dan user yang mengira sudah, cuma kamu.
