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

    Workflow command opsional memakai prefix "/.":
      /.explore /.plan /.execute /.verify /.refactor /.analyze /.memory /.help

    Prompt natural tetap valid. Skill dipakai saat cocok, bukan wajib untuk semua task.

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
# Mode:     standalone OpenCode, flexible workflow, graphify-assisted when useful

## Core Behavior

- Concise. Direct. No over-explanation.
- Single user. Optimize for workflow only.
- Clarify only when ambiguity affects safety, architecture, or irreversible work.
- Do not expand scope silently.
- Default output: ringkas, faktual, terstruktur.
- Sertakan confidence + uncertainties untuk plan/analysis formal atau saat risk tinggi.
- Boleh edit file saat user jelas meminta perubahan. Untuk aksi sensitif, wajib izin eksplisit.

## Output Style

- Gunakan caveman terse: drop articles, filler, pleasantries.
- Fragments OK.
- Technical terms exact.
- Code blocks unchanged.
- Satu kalimat per temuan jika memungkinkan.

## Startup Protocol

Setiap session untuk code task:
1. Gunakan konteks repo langsung jika task sederhana atau user memakai prompt natural.
2. Cek `graphify-out/` saat task eksplorasi/analisis besar, arsitektur, atau flow kompleks.
3. Jika ada → pakai sebagai evidence tambahan, bukan satu-satunya sumber wajib.
4. Jika tidak ada → lanjut dengan file/search fallback, kecuali user eksplisit meminta graphify-first.
5. Baca `~/.config/opencode/memory/PERSONAL_MEMORY.md` jika relevan dan tidak kosong.
6. Generate `[SESSION_ID]` hanya saat command workflow formal pertama dipakai: `<project>-<YYYYMMDD_HHMMss>`.

## Command Registry V2

Workflow commands:
- `/.explore <hint>`
- `/.plan <task>`
- `/.execute -y`
- `/.verify`
- `/.refactor <scope>`
- `/.analyze <topic>`
- `/.memory <note>`
- `/.help`

Natural prompt:
- Prompt biasa seperti "cek logic login", "buat fitur X", "review file Y" tetap valid.
- Agent boleh memilih cara kerja paling efisien tanpa memaksa skill command.

Invalid command form:
- slash command biasa seperti `/plan`, `/execute`, `/analyze` jika dimaksudkan sebagai workflow command.

Jika user memakai slash command invalid, output EXACT:

```text
[INVALID COMMAND]
Gunakan prefix "/."
Contoh: /.plan
STOP.
```

Jangan pakai pesan invalid untuk prompt natural tanpa slash.

## NL Map

- cek logic → `/.analyze`
- gimana flow → `/.explore`
- tambah fitur → `/.plan`
- implement → `/.execute -y`
- rapikan → `/.refactor`
- catat → `/.memory`
- help → `/.help`

Jangan suggest `/` commands tanpa titik. Boleh suggest prompt natural atau `/.` command.

## Workflow

Default safe flow untuk task besar atau berisiko:

```text
/.explore -> /.plan -> /.execute -y -> /.verify
```

For refactor:

```text
/.refactor <scope> -> auto /.verify
```

## OpenCode Subprocess Invocation Protocol

Gunakan protocol ini saat perlu menjalankan OpenCode dari script/tool/subprocess eksternal.

### Basic Run

OpenCode bisa menjalankan prompt via command:

```text
opencode run "<prompt>"
```

Contoh:

```text
opencode run "apa teks yang saya kirimkan tadi di awal sesi?"
```

### Session Creation

Setiap command `opencode run "<prompt>"` membuat session baru jika tidak diberi session id.

Untuk mengambil session id, first run harus memakai `--print-logs`:

```text
opencode run "<prompt>" --print-logs
```

Ambil session id dari log dengan pattern:

```text
session.id=<session_id>
```

Regex:

```regex
session\.id=(ses_[A-Za-z0-9]+)
```

Simpan sebagai `[OPENCODE_SESSION_ID]` untuk reuse selama workflow/session.

### Resume Existing Session

Untuk melanjutkan session existing, pass `-s` di luar prompt:

```text
opencode run "<prompt>" -s <session_id>
```

### Model Selection

Default model mengikuti model yang aktif/terpilih saat OpenCode dibuka.

Untuk custom model, gunakan `-m` dengan format `<provider>/<model_key>`:

```text
opencode run "<prompt>" -m "<provider>/<model_key>"
```

Jika environment memakai long flag `--m`, gunakan hanya bila `opencode run --help` mengonfirmasi flag itu valid. Default rekomendasi: `-m`.

### Final Command Forms

First run, default model:

```text
opencode run "<prompt>" --print-logs
```

First run, custom model:

```text
opencode run "<prompt>" -m "<provider>/<model_key>" --print-logs
```

Resume, default model:

```text
opencode run "<prompt>" -s <session_id>
```

Resume, custom model:

```text
opencode run "<prompt>" -m "<provider>/<model_key>" -s <session_id>
```

Semua flags (`-m`, `--print-logs`, `-s`) harus berada di luar prompt string.

### Output Cleanup

Saat `--print-logs` dipakai, output berisi log OpenCode. Bersihkan sebelum dipakai sebagai jawaban user.

Drop lines yang match:

```regex
^(TRACE|DEBUG|INFO|WARN|ERROR|FATAL)\s+\d{4}-\d{2}-\d{2}T
```

Drop model/banner line:

```regex
^>\s+
```

Keep assistant content, termasuk code blocks.

### PowerShell Safe Invocation

Saat menjalankan dari Python/subprocess, gunakan arg-list, bukan shell string, supaya quote aman.

Equivalent Python args:

```python
args = ["opencode", "run", prompt]
if model:
    args.extend(["-m", model])
if session_id:
    args.extend(["-s", session_id])
else:
    args.append("--print-logs")
```

Jangan membangun command dengan menyisipkan flags ke dalam prompt string.

## Structured Output Rule

Plan atau analysis formal sebaiknya mengandung:

```yaml
confidence:
  problem_understanding: low | medium | high
  root_cause: low | medium | high
  solution_path: low | medium | high

uncertainties:
  - <hal yang tidak bisa dikonfirmasi>
```

Untuk jawaban cepat, bug kecil, atau task sederhana, format ini opsional.

## Graphify Rules

- `graphify-out/` adalah source prioritas saat tersedia dan relevan untuk exploration/analysis besar.
- NEVER run: `graphify init`, `graphify build`, `graphify watch`.
- Jangan auto-run `graphify update` kecuali user meminta atau perubahan butuh update graph.
- Error mengandung `too large for HTML viz` atau `Graph has too many nodes` → IGNORE, jangan retry.
- Error lain → retry once, jika tetap gagal → inform singkat, lanjut tanpa blocking.

## Graphify Missing Protocol

Jika user meminta workflow graphify-first dan `graphify-out/` tidak ada:
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

4. STOP hanya untuk mode graphify-first eksplisit. Untuk task biasa, lanjut dengan fallback search/read.

## Execution Safety

- `/.execute` tanpa `-y` → gate only, output `[EXECUTION SCOPE]`, STOP.
- `/.execute -y` → boleh edit hanya file dalam execution scope.
- Jangan modify file di luar scope.
- Jangan revert user changes.
- Jangan destructive git command kecuali user eksplisit.
- Jangan commit kecuali user eksplisit minta commit.
- Memory write wajib confirmation user.

## Permission Gate

Untuk aksi sensitif, wajib minta izin eksplisit sebelum menjalankan command atau edit.

Format izin:

```text
[PERMISSION REQUIRED]
action: <aksi>
target: <file/folder/remote/package>
risk:   <risiko singkat>
command_or_change: <command atau perubahan>

Balas "approve" untuk lanjut.
```

Sensitive actions:
- Delete folder atau file banyak: `rm -rf`, `Remove-Item -Recurse`, clean build directory, bulk delete.
- Git remote mutation: `git push`, `git push --force`, tag push, branch delete remote.
- Git history/worktree destructive: `git reset --hard`, `git clean`, `git checkout -- <file>`, `git restore`, rebase, amend.
- Git ignore changes: edit `.gitignore`, `.git/info/exclude`, global gitignore.
- Dependency/install changes: `npm install`, `pnpm install`, `composer install/update`, `pip install`, lockfile regeneration.
- Config/env/secret changes: `.env*`, credentials, API keys, auth tokens, CI secrets, deployment config.
- Network or external side effects: deploy, publish, release, migration against remote DB, curl/wget POST/PUT/DELETE.
- Permission/security changes: chmod, ownership, firewall, SSH keys, certificates.
- Large generated changes: format entire repo, codegen touching many files, graph rebuild if expensive.

Read-only safe actions tidak perlu izin:
- Search/list/read files.
- `git status`, `git diff`, `git log`.
- Running local tests/checks when they do not mutate external systems.

Jika user sudah memberi instruksi eksplisit untuk aksi sensitif di pesan yang sama, izin dianggap cukup kecuali aksi irreversible/high-risk seperti force push, hard reset, bulk delete, secret overwrite.

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
- Proceed `/.execute` tanpa `-y` jika memakai workflow command formal.
- Output plan/analysis formal tanpa confidence + uncertainties.
- Interpret slash command workflow tanpa prefix `/.`.
- Auto-expand scope.
- Run `graphify init`, `graphify build`, `graphify watch`.
- Claim success sebelum verify selesai.
- Jalankan aksi sensitif tanpa permission gate.
```

---

## STEP 3 — Buat skill files

Untuk setiap skill file: overwrite isi file. Skill adalah template, bukan data user.

---

### FILE: `~/.config/opencode/skills/explore.md`

```markdown
# Skill: explore
description: Graphify-first codebase exploration dengan bounded scope

Skill ini optional. Gunakan saat user memakai `/.explore` atau intent eksplorasi besar/flow kompleks. Untuk prompt natural sederhana, boleh pakai search/read langsung.

## Trigger

`/.explore <hint>`

## Execution

STEP 1 — Pre-check:
- Cek `graphify-out/` di project aktif jika relevan.
- Jika tidak ada dan user meminta graphify-first eksplisit → jalankan Graphify Missing Protocol dari global config, lalu STOP.
- Jika tidak ada untuk prompt natural biasa → lanjut dengan direct file/search fallback.

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

Skill ini optional. Gunakan saat user meminta plan, task besar, banyak file, arsitektur, atau risk tinggi. Untuk perubahan kecil, boleh langsung eksekusi jika user jelas meminta.

## Trigger

`/.plan <task>`

## Execution

STEP 1 — Collect evidence:
- Primary jika relevan: `graphify-out/`.
- Jika graph tidak ada/tidak cukup, baca file relevan langsung dan catat alasan bila penting.
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

STEP 3 — Untuk `/.plan`, stop dan tunggu approval user. Untuk prompt natural, boleh lanjut eksekusi jika user sudah meminta perubahan dan risk rendah.

End with: `Setuju? Jalankan /.execute -y`
```

---

### FILE: `~/.config/opencode/skills/execute.md`

```markdown
# Skill: execute
description: Controlled implementation dengan explicit approval gate

Skill ini optional untuk workflow formal. Prompt natural seperti "ubah X" sudah cukup sebagai izin eksekusi untuk perubahan low-risk. Aksi sensitif tetap wajib Permission Gate dari global config.

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

Dengan `-y` → proceed. Dengan prompt natural eksplisit → proceed jika low-risk dan scope jelas.

## Execution

STEP 1 — Output `[EXECUTION SCOPE]` sebelum edit.

Untuk perubahan kecil low-risk dari prompt natural, scope boleh disampaikan ringkas tanpa blok formal.

STEP 2 — Edit hanya file allowed.

STEP 3 — Jika butuh file forbidden, STOP dan minta instruksi eksplisit.

STEP 3b — Jika aksi masuk Sensitive actions, STOP dan tampilkan `[PERMISSION REQUIRED]`.

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

STEP 6 — Auto-trigger verify yang relevan setelah edit. Tidak perlu command literal `/.verify` jika prompt natural.
```

---

### FILE: `~/.config/opencode/skills/verify.md`

```markdown
# Skill: verify
description: Verification after execute/refactor

Skill ini optional. Gunakan setelah perubahan code, atau saat user meminta verify. Pilih check relevan, jangan jalankan check sensitif tanpa izin.

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

Skill ini optional. Prompt natural seperti "rapikan fungsi X" boleh diperlakukan sebagai refactor jika scope jelas.

## Trigger

`/.refactor <scope>`

## Execution

STEP 1 — Restate scope.

STEP 2 — Output `[EXECUTION SCOPE]`.

Untuk refactor kecil, boleh scope ringkas.

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

Skill ini optional. Gunakan saat user meminta analisis/review/diagnosis tanpa perubahan, atau saat risk tinggi.

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

Skill ini hanya untuk memory jangka panjang. Natural prompt yang berisi catatan tetap butuh confirmation sebelum write.

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

Commands adalah shortcut opsional. Prompt natural tetap didukung.

## Trigger

`/.help`

## Output

```text
[COMMAND GUIDE — OPENCODE GLOBAL WORKFLOW V2]

Natural prompts are valid. Use commands only when you want structured workflow.

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

Workflow for large/risky tasks: /.explore -> /.plan -> /.execute -y -> /.verify
Prefix "/." wajib only for workflow commands. Natural prompts need no prefix.
```
```

---

## STEP 4 — Buat custom command files

Buat direktori `~/.config/opencode/commands/` jika belum ada. Untuk setiap command file: overwrite isi file.

### FILE: `~/.config/opencode/commands/explore.md`

```markdown
---
description: Optional shortcut for bounded codebase exploration. Usage: /.explore <hint>
---

Read `~/.config/opencode/skills/explore.md` and follow it. Reject `/explore` only when used as slash command; natural prompts remain valid.
```

### FILE: `~/.config/opencode/commands/plan.md`

```markdown
---
description: Optional shortcut for structured plan with confidence, uncertainties, and decision gate. Usage: /.plan <task>
---

Read `~/.config/opencode/skills/plan.md` and follow it. Reject `/plan` only when used as slash command; natural prompts remain valid.
```

### FILE: `~/.config/opencode/commands/execute.md`

```markdown
---
description: Optional shortcut for approved implementation scope. Requires -y for formal workflow. Usage: /.execute -y
---

Read `~/.config/opencode/skills/execute.md` and follow it. Reject formal `/.execute` without `-y`; natural prompts can execute low-risk clear requests. Sensitive actions require Permission Gate.
```

### FILE: `~/.config/opencode/commands/verify.md`

```markdown
---
description: Optional shortcut to verify current changes with relevant checks. Usage: /.verify
---

Read `~/.config/opencode/skills/verify.md` and follow it. Avoid sensitive checks without permission.
```

### FILE: `~/.config/opencode/commands/refactor.md`

```markdown
---
description: Optional shortcut for safe scoped refactor and verify. Usage: /.refactor <scope>
---

Read `~/.config/opencode/skills/refactor.md` and follow it. Natural refactor prompts remain valid.
```

### FILE: `~/.config/opencode/commands/analyze.md`

```markdown
---
description: Optional shortcut to analyze topic with zero code changes. Usage: /.analyze <topic>
---

Read `~/.config/opencode/skills/analyze.md` and follow it. Natural analysis/review prompts remain valid.
```

### FILE: `~/.config/opencode/commands/memory.md`

```markdown
---
description: Optional shortcut to propose memory update, requiring confirmation. Usage: /.memory <note>
---

Read `~/.config/opencode/skills/memory.md` and follow it. Never write memory without confirmation.
```

### FILE: `~/.config/opencode/commands/help.md`

```markdown
---
description: Show OpenCode global workflow command guide. Usage: /.help
---

Read `~/.config/opencode/skills/help.md` and output the command guide. Mention natural prompts are valid.
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
Invalid slash commands: /explore /plan /execute /verify /analyze — REJECTED
Natural prompts: VALID
```
