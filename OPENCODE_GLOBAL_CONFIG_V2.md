Lakukan setup Personal Workflow Skills V2 untuk OpenCode secara lengkap. Ikuti setiap step secara berurutan. Jangan skip. Jangan tanya — langsung eksekusi.

---

## PRE-CONDITION — Informasikan ke user

Sebelum mulai, output instruksi berikut:

    [OPENCODE GLOBAL SETUP]
    Setup ini membuat konfigurasi global OpenCode di:

      ~/.config/opencode/AGENTS.md
      ~/.config/opencode/skills/
      ~/.config/opencode/commands/
      ~/.config/opencode/memory/

    Jika OpenCode memakai nama file global instruction berbeda di environment ini,
    tetap buat semua file di path tersebut dan laporkan di final report.

    Workflow command valid memakai prefix "/.":
      /.explore /.plan /.execute /.verify /.refactor /.analyze /.memory /.help

---

## STEP 1 — Buat direktori struktur

Buat direktori berikut jika belum ada:

- `~/.config/opencode/`
- `~/.config/opencode/skills/`
- `~/.config/opencode/commands/`
- `~/.config/opencode/memory/`

Jangan hapus file existing di direktori tersebut.

---

## STEP 2 — Buat/update global agent config

### FILE: `~/.config/opencode/AGENTS.md`

Tulis ulang file dengan konten berikut:

```markdown
# OpenCode — Personal Global Config V2
# Skills:   ~/.config/opencode/skills/
# Commands: ~/.config/opencode/commands/
# Memory:   ~/.config/opencode/memory/
# Mode:     standalone OpenCode, graphify-first workflow

## Core Behavior

- Concise. Direct. No over-explanation.
- Single user. Optimize for workflow only.
- Never assume. Never expand scope silently.
- Default output: ringkas, faktual, terstruktur.
- WAJIB sertakan confidence + uncertainties di setiap plan/analysis.
- Jangan ubah file tanpa instruksi eksplisit.

## Output Style

- Gunakan caveman terse: drop articles, filler, pleasantries.
- Fragments OK.
- Technical terms exact.
- Code blocks unchanged.
- Satu kalimat per temuan jika memungkinkan.

## Startup Protocol

Setiap session untuk code task:
1. Cek apakah `graphify-out/` ada di project aktif.
2. Jika ada → gunakan sebagai primary source untuk explore/analyze/plan evidence.
3. Jika tidak ada → buat `.graphifyignore` sesuai framework, output instruksi `graphify update`, lalu STOP eksplorasi.
4. Baca `~/.config/opencode/memory/PERSONAL_MEMORY.md` jika ada dan tidak kosong.
5. Generate `[SESSION_ID]` saat command workflow pertama dipakai: `<project>-<YYYYMMDD_HHMMss>`.

## Command Registry V2

Valid:
- `/.explore <hint>`
- `/.plan <task>`
- `/.execute -y`
- `/.verify`
- `/.refactor <scope>`
- `/.analyze <topic>`
- `/.memory <note>`
- `/.help`

Invalid:
- command tanpa prefix `/.`
- slash command biasa seperti `/plan`, `/execute`, `/analyze`

Jika command invalid, output EXACT:

```text
[INVALID COMMAND]
Gunakan prefix "/."
Contoh: /.plan
STOP.
```

## NL Map

- cek logic → `/.analyze`
- gimana flow → `/.explore`
- tambah fitur → `/.plan`
- implement → `/.execute -y`
- rapikan → `/.refactor`
- catat → `/.memory`
- help → `/.help`

NEVER suggest `/` commands tanpa titik.

## Workflow

Default safe flow:

```text
/.explore -> /.plan -> /.execute -y -> /.verify
```

For refactor:

```text
/.refactor <scope> -> auto /.verify
```

## Structured Output Rule

Setiap plan atau analysis HARUS mengandung:

```yaml
confidence:
  problem_understanding: low | medium | high
  root_cause: low | medium | high
  solution_path: low | medium | high

uncertainties:
  - <hal yang tidak bisa dikonfirmasi>
```

Output tanpa `confidence` dan `uncertainties` = INCOMPLETE.

## Graphify Rules

- `graphify-out/` adalah primary source untuk codebase exploration.
- NEVER run: `graphify init`, `graphify build`, `graphify watch`.
- Auto-run `graphify update` setelah setiap code change.
- Error mengandung `too large for HTML viz` atau `Graph has too many nodes` → IGNORE, jangan retry.
- Error lain → retry once, jika tetap gagal → inform singkat, lanjut tanpa blocking.

## Graphify Missing Protocol

Jika `graphify-out/` tidak ada:
1. Detect framework dari sinyal minimal:
   - Laravel: `artisan`, `composer.json`
   - Python/FastAPI: `requirements.txt`, `pyproject.toml`
   - NestJS: `nest-cli.json`
   - Next.js: `next.config.*`
   - React: `package.json` tanpa framework marker lain
   - Rust: `Cargo.toml`
   - Flutter: `pubspec.yaml`
   - Default: unknown
2. Buat `.graphifyignore` sesuai template.
3. Output:

```text
.graphifyignore
<content>

Run this in your terminal:
graphify update
```

4. STOP. Jangan lanjut task.

## Execution Safety

- `/.execute` tanpa `-y` → gate only, output `[EXECUTION SCOPE]`, STOP.
- `/.execute -y` → boleh edit hanya file dalam execution scope.
- Jangan modify file di luar scope.
- Jangan revert user changes.
- Jangan destructive git command kecuali user eksplisit.
- Jangan commit kecuali user eksplisit minta commit.
- Memory write wajib confirmation user.

## Verify Rules

- Setelah `/.execute -y` atau `/.refactor`, auto-trigger verify.
- Jangan claim done sebelum verify selesai.
- Verify harus menjalankan test/build/check relevan jika tersedia.
- Jika verify tidak bisa dijalankan, jelaskan alasan.

## Memory Rules

- Memory hanya untuk insight jangka panjang.
- Jangan tulis memory tanpa confirmation.
- Proposal harus tampil dulu sebagai `[MEMORY PROPOSAL]`.

## Global Forbidden

- Modifikasi file di luar `[EXECUTION SCOPE]`.
- Proceed `/.execute` tanpa `-y`.
- Output plan/analysis tanpa confidence + uncertainties.
- Interpret command tanpa prefix `/.`.
- Auto-expand scope.
- Run `graphify init`, `graphify build`, `graphify watch`.
- Claim success sebelum verify selesai.
```

---

## STEP 3 — Buat skill files

Untuk setiap skill file: overwrite isi file. Skill adalah template, bukan data user.

---

### FILE: `~/.config/opencode/skills/explore.md`

```markdown
# Skill: explore
description: Graphify-first codebase exploration dengan bounded scope

## Trigger

`/.explore <hint>`

## Execution

STEP 1 — Pre-check:
- Cek `graphify-out/` di project aktif.
- Jika tidak ada → jalankan Graphify Missing Protocol dari global config, lalu STOP.

STEP 2 — Intent check:
Jika hint luas atau ambigu, output:

```text
[ASUMSI INTENT]
Hint     : <user hint>
Inferred : <intent yang disimpulkan>
Scope    : <scope sempit>
```

STEP 3 — Tentukan session:
- Jika `[SESSION_ID]` sudah ada → reuse.
- Jika belum → generate `<project>-<YYYYMMDD_HHMMss>`.

STEP 4 — Output exploration plan:

```text
[EXPLORATION PLAN]
session:        <session_id>
target:         <derived from hint>
stop_condition: <kapan eksplorasi berhenti>
max_scope:      max 5 file, max 2 flow path
```

STEP 5 — Explore:
- Baca `graphify-out/GRAPH_REPORT.md` dan/atau `graphify-out/graph.json`.
- Buka file source hanya jika graph data tidak cukup.
- Stop saat stop_condition terpenuhi.

STEP 6 — Output:

```text
[EXPLORATION RESULT]
session:       <session_id>
source:        graphify | direct file fallback
confidence:    low | medium | high

entry_points:
- <list>

related_modules:
- <list>

flow_summary:
<max 3 kalimat>

uncertainties:
- <hal yang tidak bisa dikonfirmasi>
```

End with: `Lanjut plan, atau cukup informasinya?`
```

---

### FILE: `~/.config/opencode/skills/plan.md`

```markdown
# Skill: plan
description: Structured planning dengan confidence model dan decision gate

## Trigger

`/.plan <task>`

## Execution

STEP 1 — Collect evidence:
- Primary: `graphify-out/`.
- Jika graph tidak cukup, baca file relevan langsung dan catat alasan.
- Jika domain tidak jelas, suggest `/.explore <hint>`.

STEP 2 — Output plan:

```text
[PLAN]
task:            <restatement>
session:         <session_id>
evidence_source: graphify | direct file fallback | context only

assumptions:
- <statement, bukan pertanyaan>

open_questions:
- <max 5, harus impact implementation/architecture>

steps:
1. <concrete step>
2. <concrete step>

files_affected:
- <list>

risks:
- <list>

confidence:
  problem_understanding: low | medium | high
  root_cause: low | medium | high
  solution_path: low | medium | high

uncertainties:
- <hal yang tidak bisa dikonfirmasi>

decision: proceed | clarify | re-explore
```

STEP 3 — Stop. Tunggu approval user. Jangan auto-execute.

End with: `Setuju? Jalankan /.execute -y`
```

---

### FILE: `~/.config/opencode/skills/execute.md`

```markdown
# Skill: execute
description: Controlled implementation dengan explicit approval gate

## Trigger

- `/.execute -y` → proceed
- `/.execute` → gate only, stop

## Gate Check

Tanpa `-y`:

```text
[EXECUTION SCOPE]
allowed:   <files boleh diubah>
forbidden: <files tidak boleh disentuh>
reason:    <alasan scope>

Tambahkan -y untuk konfirmasi eksekusi.
```

STOP.

Dengan `-y` → proceed.

## Execution

STEP 1 — Output `[EXECUTION SCOPE]` sebelum edit.

STEP 2 — Edit hanya file allowed.

STEP 3 — Jika butuh file forbidden, STOP dan minta instruksi eksplisit.

STEP 4 — Jalankan verification relevan.

STEP 5 — Output:

```text
[EXECUTION RESULT]
files_changed:
- <list>

verification:
- <command/result>

confidence: low | medium | high
uncertainties:
- <list>
status: done | partial | blocked
```

STEP 6 — Auto-trigger `/.verify`.
```

---

### FILE: `~/.config/opencode/skills/verify.md`

```markdown
# Skill: verify
description: Verification after execute/refactor

## Trigger

`/.verify`

## Execution

STEP 1 — Identify relevant checks:
- tests
- lint
- typecheck
- build
- CLI smoke test

STEP 2 — Run feasible checks.

STEP 3 — Output:

```text
[VERIFY RESULT]
status: pass | fail | partial

checks:
- <command>: <result>

failures:
- <file/line/error if any>

confidence:
  problem_understanding: low | medium | high
  root_cause: low | medium | high
  solution_path: low | medium | high

uncertainties:
- <hal yang tidak bisa dikonfirmasi>
```
```

---

### FILE: `~/.config/opencode/skills/refactor.md`

```markdown
# Skill: refactor
description: Safe scoped refactor with automatic verification

## Trigger

`/.refactor <scope>`

## Execution

STEP 1 — Restate scope.

STEP 2 — Output `[EXECUTION SCOPE]`.

STEP 3 — Apply minimal behavior-preserving changes.

STEP 4 — Run relevant verification.

STEP 5 — Auto-trigger `/.verify`.

STEP 6 — Output confidence + uncertainties.
```

---

### FILE: `~/.config/opencode/skills/analyze.md`

```markdown
# Skill: analyze
description: Deep analysis, zero code changes, structured findings

## Trigger

`/.analyze <topic>`

## Rules

- Zero code changes.
- Zero file modifications.
- Findings first.
- Include confidence + uncertainties.

## Output

```text
[ANALYSIS]
topic: <topic>
source: graphify | direct file fallback | context only

findings:
- <ordered by severity/importance>

implications:
- <impact>

confidence:
  problem_understanding: low | medium | high
  root_cause: low | medium | high
  solution_path: low | medium | high

uncertainties:
- <hal yang tidak bisa dikonfirmasi>
```
```

---

### FILE: `~/.config/opencode/skills/memory.md`

```markdown
# Skill: memory
description: Propose memory update to personal knowledge files

## Trigger

`/.memory <note>`

## Execution

STEP 1 — Evaluate note:
- Does it affect future decisions?
- Architecture/ownership/landmine?
- Recurring issue?

STEP 2 — Output proposal:

```text
[MEMORY PROPOSAL]
file:    ~/.config/opencode/memory/PERSONAL_MEMORY.md | DOMAIN_MAP.md
action:  add | update
content:
<proposed content>

Confirm? (yes / no / edit)
```

STEP 3 — Wait for user confirmation.

STEP 4 — Only write after `yes` or explicit edited content.
```

---

### FILE: `~/.config/opencode/skills/help.md`

```markdown
# Skill: help
description: Command reference OpenCode personal workflow V2

## Trigger

`/.help`

## Output

```text
[COMMAND GUIDE — OPENCODE GLOBAL WORKFLOW V2]

/.explore <hint>
→ explore codebase via graphify-first flow

/.plan <task>
→ structured plan, decision gate, no auto-execute

/.execute -y
→ implement approved scope, then verify

/.verify
→ run checks and report pass/fail

/.refactor <scope>
→ behavior-preserving refactor, then verify

/.analyze <topic>
→ analysis only, zero code changes

/.memory <note>
→ propose memory update, requires confirmation

/.help
→ show this guide

Workflow: /.explore -> /.plan -> /.execute -y -> /.verify
Prefix "/." wajib. Commands without dot are invalid.
```
```

---

## STEP 4 — Buat custom command files

Buat direktori `~/.config/opencode/commands/` jika belum ada. Untuk setiap command file: overwrite isi file.

### FILE: `~/.config/opencode/commands/explore.md`

```markdown
---
description: Explore codebase with graphify-first bounded workflow. Usage: /.explore <hint>
---

Read `~/.config/opencode/skills/explore.md` and follow it exactly. Reject `/explore`; valid command is `/.explore`.
```

### FILE: `~/.config/opencode/commands/plan.md`

```markdown
---
description: Create structured plan with confidence, uncertainties, and decision gate. Usage: /.plan <task>
---

Read `~/.config/opencode/skills/plan.md` and follow it exactly. Reject `/plan`; valid command is `/.plan`.
```

### FILE: `~/.config/opencode/commands/execute.md`

```markdown
---
description: Execute approved implementation scope. Requires -y. Usage: /.execute -y
---

Read `~/.config/opencode/skills/execute.md` and follow it exactly. Reject execution without `-y`.
```

### FILE: `~/.config/opencode/commands/verify.md`

```markdown
---
description: Verify current changes with relevant checks. Usage: /.verify
---

Read `~/.config/opencode/skills/verify.md` and follow it exactly.
```

### FILE: `~/.config/opencode/commands/refactor.md`

```markdown
---
description: Run safe scoped refactor and verify. Usage: /.refactor <scope>
---

Read `~/.config/opencode/skills/refactor.md` and follow it exactly.
```

### FILE: `~/.config/opencode/commands/analyze.md`

```markdown
---
description: Analyze topic with zero code changes. Usage: /.analyze <topic>
---

Read `~/.config/opencode/skills/analyze.md` and follow it exactly.
```

### FILE: `~/.config/opencode/commands/memory.md`

```markdown
---
description: Propose memory update, requiring confirmation. Usage: /.memory <note>
---

Read `~/.config/opencode/skills/memory.md` and follow it exactly. Never write memory without confirmation.
```

### FILE: `~/.config/opencode/commands/help.md`

```markdown
---
description: Show OpenCode global workflow command guide. Usage: /.help
---

Read `~/.config/opencode/skills/help.md` and output the command guide.
```

---

## STEP 5 — Buat memory files

Jangan overwrite memory file jika sudah ada, kecuali `MEMORY.md` index perlu append missing entry.

### FILE: `~/.config/opencode/memory/PERSONAL_MEMORY.md` (skip jika exists)

```markdown
# Personal Memory — OpenCode
Last updated: 2026-05-09

## Architecture Decisions
- (belum ada)

## Module Ownership
| Module | Team | Notes |
|--------|------|-------|
| -      | -    | -     |

## Known Landmines
- (belum ada)

## Things I Always Forget
- (belum ada)
```

### FILE: `~/.config/opencode/memory/DOMAIN_MAP.md` (skip jika exists)

```markdown
# Domain Map — OpenCode
Last updated: 2026-05-09

## Entry Points
| Domain | Entry File | Key Function |
|--------|------------|--------------|
| -      | -          | -            |

## Cross-Team Boundaries
- (belum ada)
```

### FILE: `~/.config/opencode/memory/MEMORY.md`

Baca isi saat ini. Tambah entry berikut jika belum ada. Jangan duplikasi.

```markdown
- [Personal Memory](PERSONAL_MEMORY.md) — architecture decisions, ownership, landmines
- [Domain Map](DOMAIN_MAP.md) — entry points, boundaries, domain notes
```

---

## STEP 6 — Verifikasi setup

Setelah semua file dibuat:

1. List `~/.config/opencode/`.
2. List `~/.config/opencode/skills/`.
3. List `~/.config/opencode/commands/`.
4. List `~/.config/opencode/memory/`.
5. Tampilkan ukuran byte file penting:
   - `AGENTS.md`
   - semua skill files
   - semua command files
   - memory index
6. Konfirmasi `AGENTS.md` mengandung `Command Registry V2`.
7. Konfirmasi command files mengandung frontmatter `description`.

---

## STEP 7 — Final Report

Tampilkan PERSIS:

```text
[SETUP COMPLETE — OPENCODE GLOBAL WORKFLOW V2]

Config:
  ~/.config/opencode/AGENTS.md ✓

Skills:
  ~/.config/opencode/skills/explore.md  ✓
  ~/.config/opencode/skills/plan.md     ✓
  ~/.config/opencode/skills/execute.md  ✓
  ~/.config/opencode/skills/verify.md   ✓
  ~/.config/opencode/skills/refactor.md ✓
  ~/.config/opencode/skills/analyze.md  ✓
  ~/.config/opencode/skills/memory.md   ✓
  ~/.config/opencode/skills/help.md     ✓

Commands:
  ~/.config/opencode/commands/explore.md  ✓
  ~/.config/opencode/commands/plan.md     ✓
  ~/.config/opencode/commands/execute.md  ✓
  ~/.config/opencode/commands/verify.md   ✓
  ~/.config/opencode/commands/refactor.md ✓
  ~/.config/opencode/commands/analyze.md  ✓
  ~/.config/opencode/commands/memory.md   ✓
  ~/.config/opencode/commands/help.md     ✓

Memory:
  ~/.config/opencode/memory/PERSONAL_MEMORY.md ✓
  ~/.config/opencode/memory/DOMAIN_MAP.md      ✓
  ~/.config/opencode/memory/MEMORY.md          ✓

Status: READY
Workflow: /.explore -> /.plan -> /.execute -y -> /.verify
Invalid: /explore /plan /execute /verify /analyze — REJECTED
```
