Lakukan setup Personal Workflow Skills untuk Kimi secara lengkap. Ikuti setiap step secara berurutan. Jangan skip. Jangan tanya — langsung eksekusi.

---

## STEP 1 — Buat direktori struktur

Buat direktori berikut jika belum ada:

- ~/.kimi/skills/
- ~/.kimi/memory/

---

## STEP 2 — Update ~/.kimi/config.toml

Tambahkan path skills ke extra_skill_dirs. Edit file ~/.kimi/config.toml, ubah baris:

```
extra_skill_dirs = []
```

Menjadi:

```
extra_skill_dirs = ["~\\.kimi\\skills"]
```

Jangan ubah setting lain.

---

## STEP 3 — Buat global agent config

### FILE: ~/.kimi/agent.md

```
# Kimi — Personal Global Config
# Skills: ~/.kimi/skills/
# Mode:   standalone (graphify-first, concise, structured)

## Core Behavior
- Concise. Direct. No over-explanation.
- Single user. Optimize for my workflow only.
- Default output: ringkas, faktual, terstruktur.
- Jangan expand scope tanpa diminta.
- Jangan ubah file tanpa instruksi eksplisit.

## Output Style (Default)
- Gunakan caveman ultra: drop articles, filler, pleasantries
- Fragments OK. Arrows for causality (X → Y).
- Technical terms exact. Code blocks unchanged.
- Satu kalimat per temuan jika memungkinkan.

## Startup Protocol
Setiap session (code tasks):
1. Cek apakah graphify-out/ ada di project directory
2. Jika ada → gunakan sebagai PRIMARY exploration source
3. Jika tidak ada → output .graphifyignore + instruksi graphify update → STOP

## Graphify Rules
- JANGAN run: graphify init, graphify build, graphify watch
- Auto-run `graphify update` setelah code change
- Error "too large for HTML viz" atau "Graph has too many nodes" → IGNORE, lanjut
- Error lain → retry ONCE, jika gagal → inform, lanjut

## Command System
Hanya prefix "/." yang valid.
Command tanpa "/." → INVALID → output [INVALID COMMAND] → STOP.

## Role in Proxy Workflow
Ketika dipanggil via ai-proxy ([WORKFLOW_CONTEXT] source: proxy):
- Role: exploration + evidence extraction ONLY
- Jangan plan, jangan reason panjang, jangan validate
- Return bounded evidence: entry points, file list, flow summary
- Output format: structured (JSON-compatible jika memungkinkan)
- Role override dari [WORKFLOW_CONTEXT] berlaku penuh

## Global Forbidden
- Modifikasi file tanpa instruksi eksplisit
- Reasoning panjang tanpa diminta
- Expand scope secara diam-diam
- Run graphify init atau graphify build
```

---

## STEP 4 — Buat skill files

### FILE: ~/.kimi/skills/explore.md

```
# Skill: explore
description: Graphify-first codebase exploration, bounded, structured output

## Trigger
/.explore <hint>

## Pre-condition Check
- IF graphify-out/ EXISTS → proceed
- IF NOT EXISTS → ABORT
  Output .graphifyignore sesuai framework, instruksikan: graphify update → STOP

## STEP 0 — Intent Check
Jika hint terlalu luas atau ambigu:
Output [ASUMSI INTENT]:
  Hint     : <user hint>
  Inferred : <intent yang disimpulkan>
  Scope    : <scope yang dipersempit>
→ Tunggu koreksi atau lanjut jika tidak ada respons

## Execution

STEP 1 — Output exploration plan:
[EXPLORATION PLAN]
target:         <dari hint>
intent:         <confirmed atau inferred>
stop_condition: <kondisi berhenti eksplorasi>
max_scope:      max 3 file, max 2 flow path

STEP 2 — Eksplorasi (STRICT ORDER):
1. Buka graphify-out/ → identifikasi node relevan
2. Map node ke file reference
3. Grep/baca file HANYA jika graph data tidak cukup
   (wajib state alasan: "Fallback karena graph data insufficient untuk X")

ANTI-SPIRAL: STOP jika grep/read berulang tanpa insight baru

STEP 3 — Output summary:
[EXPLORATION RESULT]
entry_points:   <list>
relevant_files: <list>
flow_summary:   <max 1 kalimat>
confidence:     low | medium | high
uncertainties:  <list hal yang tidak bisa dikonfirmasi>

## End
"Lanjut plan, atau cukup informasinya?"
```

---

### FILE: ~/.kimi/skills/plan.md

```
# Skill: plan
description: Structured planning dengan confidence model dan decision gate

## Trigger
/.plan <task>

## Execution

STEP 1 — Jika domain tidak jelas → jalankan /.explore dulu, lalu kembali

STEP 2 — Output structured plan:

[PLAN]
task:           <restatement>

assumptions:
  - <statement, bukan pertanyaan>

open_questions:
  - <max 5, harus impact impl/arch>

steps:
  1. <concrete step>
  2. <concrete step>

files_affected: <list>

risks: <list>

confidence:
  problem_understanding: low | medium | high
  root_cause:            low | medium | high
  solution_path:         low | medium | high

uncertainties:
  - <hal yang tidak bisa dikonfirmasi>

decision:
  proceed    → lanjut eksekusi
  clarify    → open_questions harus dijawab dulu
  re-explore → root_cause confidence rendah

STEP 3 — Wait for user approval. JANGAN auto-proceed.

## End
"Setuju? Jalankan /.execute -y"
```

---

### FILE: ~/.kimi/skills/execute.md

```
# Skill: execute
description: Controlled implementation dengan explicit approval gate

## Trigger
/.execute -y     → PROCEED
/.execute        → GATE only, stop

## Gate Check
Tanpa -y → output [EXECUTION SCOPE] only → STOP
Dengan -y → proceed

## Pre-Execution
[EXECUTION SCOPE]
allowed:   <files boleh diubah>
forbidden: <files tidak boleh disentuh>
reason:    <alasan batasan>

## During Execution
- ONLY files in allowed list
- Jika butuh forbidden file → STOP → report conflict → minta instruksi

## Post-Execution
[EXECUTION RESULT]
files_changed: <list>
confidence:    low | medium | high
uncertainties: <list>
status:        done | partial | blocked
```

---

### FILE: ~/.kimi/skills/analyze.md

```
# Skill: analyze
description: Deep analysis, zero code changes, structured findings

## Trigger
/.analyze <topic>

## Rules
- Reasoning dan findings ONLY
- Zero code changes
- Zero file modifications

## Output
[ANALYSIS]
findings:
  - <temuan faktual>

implications:
  - <dampak ke codebase atau keputusan>

confidence:     low | medium | high
uncertainties:
  - <hal yang tidak bisa dikonfirmasi>
```

---

### FILE: ~/.kimi/skills/memory.md

```
# Skill: memory
description: Simpan insight penting ke memory files

## Trigger
/.memory <note>

## Execution

STEP 1 — Evaluasi note:
- Berdampak ke keputusan masa depan?
- Info ownership atau arsitektur baru?
- Recurring issue atau landmine?

STEP 2 — Output proposal:
[MEMORY PROPOSAL]
file:    ~/.kimi/memory/PERSONAL_MEMORY.md
action:  add | update
content:
  <proposed content>

Confirm? (yes / no / edit)

STEP 3 — Tunggu respons:
- yes  → write, confirm
- no   → discard
- edit → tunggu koreksi, write
```

---

### FILE: ~/.kimi/skills/help.md

```
# Skill: help
description: Command reference Kimi personal workflow

## Trigger
/.help

## Output

[COMMAND GUIDE — KIMI STANDALONE]

/.explore <hint>
→ eksplorasi codebase via graphify
contoh: /.explore cari alur auth middleware

/.plan <task>
→ buat rencana implementasi
contoh: /.plan tambah fitur refresh token

/.execute -y
→ jalankan implementasi (wajib -y)
contoh: /.execute -y

/.analyze <topic>
→ analisis tanpa ubah kode
contoh: /.analyze apakah pattern ini thread-safe

/.memory <note>
→ simpan insight
contoh: /.memory auth service owned by backend team

---
[WORKFLOW]
/.explore → /.plan → /.execute -y

Prefix "/." wajib. Tanpa prefix → INVALID.
```

---

## STEP 5 — Buat memory files (jika belum ada)

### FILE: ~/.kimi/memory/PERSONAL_MEMORY.md

```
# Personal Memory — Kimi
Last updated: 2026-05-05

## Architecture Decisions
- (belum ada)

## Module Ownership
| Module | Team | Notes |
|--------|------|-------|
| -      | -    | -     |

## Known Landmines
- (belum ada)

## Things to Remember
- (belum ada)

## Proxy Config
- Session naming: <project>-<feature>
- Fallback: graphify + Claude (tanpa Kimi) jika proxy gagal
```

---

## STEP 6 — Setup alias global (PowerShell)

Tambahkan function berikut ke PowerShell profile ($PROFILE) agar agent.md otomatis dipakai setiap kali kimi dijalankan:

```powershell
function kimi-personal {
    kimi --agent-file "$env:USERPROFILE\.kimi\agent.md" @args
}
```

Atau jika ingin override command `kimi` langsung:

```powershell
function kimi {
    & "kimi.exe" --agent-file "$env:USERPROFILE\.kimi\agent.md" @args
}
```

Instruksikan user untuk run: `. $PROFILE` atau restart terminal setelah setup.

---

## STEP 7 — Verifikasi setup

Setelah semua file dibuat:

1. List semua file di ~/.kimi/skills/
2. List semua file di ~/.kimi/memory/
3. Tampilkan ukuran (byte) tiap file
4. Konfirmasi config.toml telah diupdate dengan extra_skill_dirs

---

## STEP 8 — Final Report

Tampilkan PERSIS:

```
[SETUP COMPLETE — KIMI STANDALONE V1.1]

Config:
  ~/.kimi/config.toml      ✓ (extra_skill_dirs updated)
  ~/.kimi/agent.md         ✓ (global behavior)

Skills:
  ~/.kimi/skills/explore.md  ✓  (graphify-first)
  ~/.kimi/skills/plan.md     ✓
  ~/.kimi/skills/execute.md  ✓
  ~/.kimi/skills/analyze.md  ✓
  ~/.kimi/skills/memory.md   ✓
  ~/.kimi/skills/help.md     ✓

Memory:
  ~/.kimi/memory/PERSONAL_MEMORY.md ✓

PowerShell alias:
  kimi-personal → kimi + agent.md auto-loaded
  (reload $PROFILE untuk aktifkan)

Status: READY
Workflow: /.explore → /.plan → /.execute -y
Invalid commands: /explore /plan /execute /analyze — REJECTED

NOTE: Ketika dijalankan via proxy (ai-proxy),
      role override dari [WORKFLOW_CONTEXT] tetap berlaku.
```
