# Changelog — Agent Workflow Prompt

---

## v3.2.1 — Output Contract Enforcement

**Tanggal:** 2026-06-03
**Scope:** main_agent.md (skill templates + behavior section)
**Breaking:** Tidak — backward compatible. Perubahan behavior (bukan API/schema).

### Problem yang Diperbaiki

Skill `/.explore`, `/.plan`, `/.analyze`, `/.sweep` tidak konsisten menampilkan semua field output yang sudah didefinisikan dalam template. Field seperti `assumptions`, `open_questions`, `risks`, `decision` (untuk plan), dan `ownership_hints`, `related_modules` (untuk explore) sering di-drop saat main_agent melakukan synthesis dari evidence second_agent.

**Root cause:** Template output sudah ada di skill files, tapi tidak ada instruksi eksplisit bahwa template adalah kontrak yang tidak boleh di-skip. Main_agent cenderung mengikuti struktur evidence second_agent alih-alih mengisi template.

### Perubahan

#### `main_agent.md` — Skill Templates (STEP 3)

Setiap skill yang melakukan synthesis dari second_agent mendapat tambahan **SYNTHESIS RULE** sebelum template output:

**`explore.md` template:**
- +SYNTHESIS RULE (NON-NEGOTIABLE): instruksi bahwa template adalah kontrak, bukan ikut struktur evidence
- +Checklist 7 fields: `source`, `session`, `confidence`, `entry_points`, `ownership_hints`, `related_modules`, `uncertainties`
- +Contoh output kosong per field (cara menulis alasan jika field tidak tersedia)

**`plan.md` template:**
- +SYNTHESIS RULE (NON-NEGOTIABLE)
- +Checklist 11 fields: `task`, `session`, `evidence_source`, `assumptions`, `open_questions`, `steps`, `files_affected`, `risks`, `confidence` (3 sub-fields), `uncertainties`, `decision`
- +Contoh output kosong per field
- Field `assumptions` dan `open_questions` diperjelas: assumptions = statement bukan pertanyaan; open_questions = max 5, hanya jika blocking keputusan arch/impl

**`analyze.md` template:**
- +SYNTHESIS RULE (NON-NEGOTIABLE)
- +Checklist 6 fields: `source`, `session`, `confidence` (3 sub-fields), `findings`, `implications`, `uncertainties`
- +Contoh output kosong per field

**`sweep.md` template:**
- +SYNTHESIS RULE (NON-NEGOTIABLE)
- +Checklist 6 fields: `source`, `session`, `changed_files`, `impact`, `risks`, `uncertainties`
- +**Output Contract Rule** — sebelumnya TIDAK ADA di sweep template (bug)
- +Contoh output kosong per field

#### `main_agent.md` — Behavior Section (Evidence Output Ownership)

**"Format synthesis minimal"** diperluas:
- `/.plan` sebelumnya hanya: `"PLAN (scope, files, steps, risks, verification, confidence)"`
- Sekarang: full field list untuk semua 4 synthesis skills
- +**SYNTHESIS HARD RULE** dengan master checklist per skill:
  - `/.explore` → 7 fields
  - `/.plan` → 11 fields
  - `/.analyze` → 6 fields
  - `/.sweep` → 6 fields

### Yang TIDAK Berubah

- second_agent.md — tidak ada perubahan behavior (hanya versi bump)
- Schema/API/command registry — tidak berubah
- Semua skill local (execute, verify, refactor, commit, memory, help, local, caveman, doctor) — tidak diubah
- Session handling, AGENT_PATH check, proxy invocation flow — tidak berubah

---

## v3.2.1 — Addendum: Session Lifecycle Binding (hook-driven)

**Tanggal:** 2026-06-03
**Scope:** main_agent.md (STEP 5 managed block + STEP 5b hook setup + skill session-resolution), settings.json, hooks/session-bind.ps1
**Breaking:** Tidak — backward compatible. Hook absent → fallback ke state.json + context (perilaku v3.2.0).

### Problem yang Diperbaiki

MAIN_SESSION_ID lama diikat ke project root saja: `.workflow/state.json` match → SELALU reuse. Akibat:
- `claude` baru / `/clear` di project sama → tetap reuse thread second_agent lama → context main_agent fresh tapi thread second_agent penuh (mismatch).
- Thread second_agent numpuk tanpa batas reset → risiko context limit second_agent.

**Root cause:** tidak ada signal lifecycle chat main_agent. State.json hanya tahu project root, bukan "ini sesi pertama atau lanjutan".

### Solusi

Ikat lifecycle thread second_agent ke lifecycle chat main_agent via SessionStart hook field `source`:
- `startup` | `clear` | `compact` → MAIN_SESSION_ID BARU → second_agent thread BARU
- `resume` → REUSE MAIN_SESSION_ID → thread LANJUT

### Perubahan

- **+`hooks/session-bind.ps1`** (Claude Code) — baca `source` dari stdin, kelola registry `session_registry.json` (key = agent session_id), inject blok `[SESSION BINDING - authoritative]` ke context. Id format `main_<slug>_<yyyyMMdd>_<HHmmssfff>_<rand4>` (ms + 4-hex random, anti-collision).
- **+`settings.json`** — daftar hook `SessionStart` matcher `startup|resume|clear|compact`.
- **`main_agent.md` STEP 5 managed block** — Startup Protocol 3b/5 → hook-driven; +section "Session Lifecycle Rule (AUTHORITATIVE)"; Session Handling Rule diberi precedence note (hook > context > state.json).
- **`main_agent.md` +STEP 5b** — setup hook (Claude Code only; agent lain SKIP + fallback note).
- **`main_agent.md` skill session-resolution** (explore/plan/analyze/sweep/doctor) — +step 0 cek `[SESSION BINDING]` (authoritative) sebelum context/state.json.
- **`main_agent.md` DESIGN NOTES + STEP 6/7** — dokumentasi + verifikasi hook.

### Catatan Desain

- Pilihan: `compact` → thread BARU. `autoCompactEnabled=true` → tiap auto-compact reset thread second_agent mid-session. Ubah `compact`=reuse: di `session-bind.ps1` ganti `$source -eq 'resume'` → `$source -in 'resume','compact'`.
- Hook = Claude Code specific (SessionStart + settings.json). Agent lain (Codex/Cursor/Gemini/dst) belum punya padanan → fallback state.json + context (perilaku v3.2.0).
- **Koreksi** terhadap "Yang TIDAK Berubah" di v3.2.1 awal: session handling SEKARANG berubah (hook-driven). Sub-rule lain (AGENT_PATH check, proxy invocation, Output Contract) tetap.

---

## v3.2.1 — Addendum: second_agent open_questions Leak Fix

**Tanggal:** 2026-06-03
**Scope:** second_agent.md (FILE block `~/.config/opencode/AGENTS.md`)
**Breaking:** Tidak — backward compatible. Penambahan constraint, tidak ubah schema/command.

### Problem yang Diperbaiki

second_agent kadang emit `open_questions` di output evidence. `open_questions` adalah field eksklusif main_agent (PLAN contract) yang ditujukan ke user dan dijawab main_agent — BUKAN dihasilkan antar-agent. Output second_agent harusnya hanya `uncertainties` (gap fakta setelah search).

**Root cause:** Output contract second_agent ([EVIDENCE], [EXPLORATION RESULT]) hanya mendefinisikan `uncertainties`, tidak pernah memforbid `open_questions`. Saat `command=plan`, LLM meniru struktur PLAN template main_agent → leak `open_questions`.

### Perubahan

`second_agent.md` — 3 titik dalam FILE block `AGENTS.md`:
- **`[SECOND_AGENT CONSTRAINT]`** — +`DO NOT emit open_questions atau pertanyaan apa pun ke user` + penegasan domain split (open_questions = main_agent; second_agent = uncertainties).
- **`[WORKFLOW_AGENT]` EVIDENCE format** — field `uncertainties` diperjelas: statement gap fakta, BUKAN pertanyaan user, jangan diformat sebagai open_questions.
- **`[WORKFLOW_AGENT]` Forbidden** — +`Emit open_questions / pertanyaan ke user — itu domain main_agent`.

### Distinksi yang Ditegakkan

- `uncertainties` (second_agent) = fakta yang tak bisa dikonfirmasi setelah exhaustive search. Statement.
- `open_questions` (main_agent) = keputusan arch/impl yang butuh konfirmasi user, diturunkan dari uncertainties/implications/assumptions second_agent.

### Catatan

- Fix aktif HANYA setelah re-run setup prompt second_agent (overwrite `~/.config/opencode/AGENTS.md`). AGENTS.md existing tidak auto-update.
- **Koreksi** terhadap "Yang TIDAK Berubah" di v3.2.1 awal: second_agent.md SEKARANG berubah (constraint baru).

---

## Migrasi dari v3.2.0 → v3.2.1

### Cara Update

**Opsi A — Re-run setup prompt (recommended):**
1. Paste `main_agent.md` v3.2.1 ke agent
2. Agent akan deteksi mode UPDATE (skill files diperbarui, memory dipertahankan)
3. Confirm `yes` → skill files di `{AGENT_DIR}/skills/` dioverwrite dengan template baru
4. Config file di-merge (marker `<!-- WORKFLOW-MAIN-AGENT:START -->` diganti)

**Opsi B — Manual patch (jika tidak mau re-run full setup):**

Update 4 skill files di `{AGENT_DIR}/skills/`:

Untuk masing-masing `explore.md`, `plan.md`, `analyze.md`, `sweep.md` — tambahkan blok berikut **sebelum** template output `[SKILL RESULT]`:

```
### SYNTHESIS RULE (NON-NEGOTIABLE)
Template [SKILL RESULT] adalah kontrak output — bukan suggestion.
JANGAN ikut struktur evidence dari second_agent. Isi setiap field dari evidence.
Field tidak ada di evidence → tetap tampilkan + tulis alasan.

Checklist sebelum output:
<field list sesuai skill — lihat v3.2.1 main_agent.md>
```

Dan update `{CONFIG_FILE}` (CLAUDE.md / AGENTS.md / dst) — ganti konten antara marker `<!-- WORKFLOW-MAIN-AGENT:START -->` dan `<!-- WORKFLOW-MAIN-AGENT:END -->` dengan versi baru dari `main_agent.md` v3.2.1 STEP 5.

**Opsi C — Hanya update behavior (minimal):**

Tambahkan ke `{CONFIG_FILE}` dalam section `### Evidence Output Ownership`:

```
SYNTHESIS HARD RULE (berlaku untuk SEMUA skill yang synthesis dari second_agent):
Template output adalah KONTRAK — bukan suggestion. Evidence dari second_agent adalah bahan baku.
JANGAN ikut struktur evidence. Isi setiap field template dari evidence.
Field yang tidak ada di evidence → tetap tampilkan + tulis alasan.
Berlaku untuk: /.explore /.plan /.analyze /.sweep
Checklist:
- /.explore  → source, session, confidence, entry_points, ownership_hints, related_modules, uncertainties
- /.plan     → task, session, evidence_source, assumptions, open_questions, steps, files_affected, risks, confidence (3 sub), uncertainties, decision
- /.analyze  → source, session, confidence (3 sub), findings, implications, uncertainties
- /.sweep    → source, session, changed_files, impact, risks, uncertainties
```

---

## v3.2.0 — Baseline

Versi awal arsitektur dual-agent:
- main_agent = orchestrator + executor (Claude Code / Codex / dll)
- second_agent = read-only evidence gatherer (OpenCode via python main.py)
- Command split: LOCAL (execute, init, refactor, commit, review, compress, memory, caveman, local, help) + DELEGATED (explore, plan, analyze, verify, sweep, doctor)
- Session handling: 1 MAIN_SESSION_ID per project root
- Graphify integration sebagai primary codebase context
- Output Contract Rule: terdefinisi tapi tidak di-enforce saat synthesis
