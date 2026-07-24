# Skill: verify
description: 3-step verification — logic, falsification, reality. Kedalaman dari commands.verify_mode.

## Trigger
/.verify (auto setelah /.execute -y atau /.refactor)

## Mode (runtime baca .workflow/config.json → commands.verify_mode)
delegated (default) → verifikasi penuh second_agent, Protocol di bawah.
syntax → QUICK: runtime check parse lokal file berubah (git diff HEAD + untracked). NOL test, NOL second_agent.
  Output [QUICK VERIFY] sudah final. pass = file parse, BUKAN fitur bekerja — sebut batas ini.
  not_checked/skipped relay apa adanya, JANGAN dihitung pass.
  DILARANG verdict DONE untuk masalah runtime/behavior; maksimal "syntax OK, behavior belum diverifikasi" + saran set verify_mode=delegated.

## Protocol (verify_mode=delegated)
1. Logic: solve problem? assumptions valid? konsisten pola codebase? → PASS/FAIL + reason
2. Falsification: kondisi gagal? edge case? malformed input? → list
3. Reality: test suite → run → simulate → "not executable". Actual vs expected.

## Gate 3-dimensi (verify_mode=delegated — WAJIB)
TIAP temuan bawa TIGA tag:
severity:       critical | high | medium | low
origin:         introduced | regression | pre_existing | unknown
scope_relation: in_scope | out_of_scope

critical = data loss | security | hasil salah diam-diam | semua command rusak
high     = jalur normal fitur rusak | caller existing regresi | kontrak dilanggar
medium   = edge case | degradasi | defect dgn workaround
low      = naming/style/doc drift | hipotetis tanpa trigger

SEVERITY SENDIRIAN TAK MENENTUKAN BLOCKING. Rute pakai tabel:
introduced/regression + in_scope     + critical|high → BLOCKING
introduced/regression + out_of_scope + critical|high → BLOCKING (+ pelanggaran scope)
introduced/regression + out_of_scope + medium|low    → ESCALATION
unknown               + apa pun      + critical|high → BLOCKING (fail closed)
pre_existing          + apa pun      + critical|high → ESCALATION
selain itu                                          → NOTE

`unknown` bukan pintu keluar: turun dari unknown WAJIB sebut bukti (diff/git history/versi sebelum). Tak bisa → tetap memblokir.
ESCALATION tak ubah verdict TAPI BUKAN note — critical/high nyata yang user harus putuskan. DILARANG disembunyikan di notes.
Tanpa file:line + skenario gagal konkret → TAK BOLEH critical/high, turunkan jadi note + sebut evidence kurang.
Aturan itu soal MUTU EVIDENCE, bukan meredam masalah sistemik: defect tersebar banyak tempat TETAP critical/high — kutip file:line perwakilan + sebut luasnya.
DILARANG naikkan sever biar diperhatikan / turunkan biar lolos.

## Output
[VERIFICATION]
mode: delegated | syntax          ← kamu yang isi; second_agent tak tahu mode (syntax nol lewat dia)
verdict: DONE | NEEDS FIX  ← NEEDS FIX HANYA bila blocking_findings ada
blocking_findings: - severity|origin|scope_relation — <problem> [file:line] — trigger — impact — fix | (tidak ada: apa yg dicek bersih)
escalations: - severity|origin|scope_relation — <problem> [file:line] — kenapa tak memblokir | (tidak ada)
notes: - severity|origin|scope_relation — <problem> [file:line] | (tidak ada)
checks_run: <yang dijalankan/dibaca + hasil>
not_verified: <tak bisa dicek + alasan> | (tidak ada)
confidence: low|medium|high — <alasan>
NEEDS FIX → fix blocking dulu → re-run /.verify. JANGAN output final sebelum done.
escalations + notes JANGAN dibuang — tampilkan, user yang putuskan garap sekarang/nanti.
