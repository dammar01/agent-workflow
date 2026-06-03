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
