Lakukan setup Personal Workflow Skills V3 untuk OpenCode secara lengkap. Ikuti setiap step secara berurutan. Jangan skip. Jangan tanya — langsung eksekusi.

V3 vs V2 — major changes:

- Caveman ultra sebagai real skill injection (bukan hanya plugin) dengan hard caps per-block.
- Skill /.audit baru — cross-model audit independen untuk git diff hasil /.execute.
- Exploration cache contract di /.plan & /.analyze — reuse hasil /.explore dalam session yang sama.
- Skill /.verify-quick baru — lightweight verify (syntax/lint) untuk task kecil.
- Pre-execute contract sanity check di /.execute — flag DTO/async/config touchpoints.
- Token budget awareness — soft warn 50k, hard gate 100k.
- Caveman sub-skills: /.commit, /.review, /.compress.

---

## PRE-CONDITION — Informasikan ke user

Sebelum mulai, output instruksi berikut:

    [OPENCODE GLOBAL SETUP V3]
    Setup ini membuat konfigurasi global OpenCode di:

      ~/.config/opencode/AGENTS.md
      ~/.config/opencode/skills/
      ~/.config/opencode/commands/
      ~/.config/opencode/memory/

    Jika OpenCode memakai nama file global instruction berbeda di environment ini,
    tetap buat semua file di path tersebut dan laporkan di final report.

    Workflow command opsional memakai prefix "/.":
      /.explore /.plan /.execute /.verify /.verify-quick /.refactor /.analyze
      /.audit /.memory /.help
    Caveman family: /.commit /.review /.compress

    Default behavior yang dikonfigurasi:
      - Output: caveman ultra dengan hard caps per-block (real skill injection).
      - Graphify: primary source default untuk codebase understanding.
      - Context7: MCP tool untuk library/framework docs terkini.
      - Agent-workflow: dipanggil via AGENT_PATH env. Response contract: {ok, content, meta}.
      - Fallback mode: untuk evidence commands, jika AGENT_PATH tidak tersedia,
        tanya user dahulu, baru lanjut dengan graphify-out + reasoning lokal.
      - Exploration cache: hasil /.explore dipass sebagai context ke /.plan & /.analyze
        dalam session yang sama (mengurangi reread).
      - Cross-model audit: /.audit invoke model berbeda dari /.execute untuk
        independent review.
      - Token budget: soft warn 50k, hard gate 100k cumulative per session.

    Prompt natural tetap valid. Skill dipakai saat cocok, bukan wajib untuk semua task.

---

## STEP 0 — Install & Configure Prerequisites

Tiga komponen dikonfigurasi sebelum setup utama. Eksekusi berurutan. Stop di komponen pertama yang gagal fatal.

---

### 0A — Caveman (Plugin Install)

Caveman plugin tetap diinstall untuk style/output formatting baseline.
**Catatan**: di V3, hard caps dan execution policy ada di `skills/caveman.md`. Plugin = soft style; skill = hard rules.

Source: https://github.com/JuliusBrussee/caveman
Modes: `lite` | `full` | `ultra` | `wenyan`

**Install untuk OpenCode:**

```bash
npx skills add JuliusBrussee/caveman -a opencode
```

Setelah install, verifikasi dengan menjalankan `/caveman ultra` di session berikutnya.

Jika install gagal → output warning dan lanjut (bukan fatal):

```text
[PREREQ 0A] Caveman plugin → FAILED to install.
Action: install manually dari https://github.com/JuliusBrussee/caveman
Status: lanjut tanpa plugin, hard rules tetap aktif via skills/caveman.md.
```

Jika berhasil:

```text
[PREREQ 0A] Caveman plugin → installed. Aktifkan ultra mode: /caveman ultra
Note: hard caps per-block tetap dari skills/caveman.md (V3 real injection).
```

---

### 0B — Graphify CLI

**PENTING: PyPI package name adalah `graphifyy` (double-y). CLI command tetap `graphify`.**
Source: https://github.com/safishamsi/graphify
Requires: Python 3.10+

Cek ketersediaan:

```bash
graphify --version
```

Jika tersedia → output:

```text
[PREREQ 0B] Graphify → found: <version>
```

Jika tidak tersedia → install. Pilih metode:

```bash
# Direkomendasikan (jika uv tersedia):
uv tool install graphifyy && graphify install

# Alternatif:
pipx install graphifyy && graphify install

# Fallback:
pip install graphifyy && graphify install
```

Jika tidak ada Python runtime → output warning dan lanjut (bukan fatal):

```text
[PREREQ 0B] Graphify → NOT installed. Python/uv/pipx unavailable.
Action: install manually → pip install graphifyy && graphify install
Source: https://github.com/safishamsi/graphify
Status: skipped, lanjut setup tanpa graphify.
```

Jika install berhasil → verifikasi `graphify --version` dan output:

```text
[PREREQ 0B] Graphify → installed: <version>
```

---

### 0C — Context7 MCP

Context7 dikonfigurasi sebagai MCP server di `~/.config/opencode/config.json`.

**STEP 0C-1** — Cek file existing dan backup:

- Jika `~/.config/opencode/config.json` ada → backup dulu, lalu baca isi dan lanjut ke merge:
  ```bash
  cp ~/.config/opencode/config.json ~/.config/opencode/config.json.bak
  ```
- Jika tidak ada → buat file baru dengan content minimal (skip backup).

**STEP 0C-2** — Tambahkan atau merge entry Context7:

Jika key `"mcp"` sudah ada di file → tambahkan `"context7"` ke dalam object `mcp` existing tanpa overwrite key lain.

Jika key `"mcp"` belum ada → tambahkan object `"mcp"` baru ke root config.

Entry yang ditambahkan:

```json
"context7": {
  "type": "local",
  "command": "npx",
  "args": ["-y", "@upstash/context7-mcp@latest"]
}
```

Contoh hasil file minimal jika sebelumnya kosong:

```json
{
  "mcp": {
    "context7": {
      "type": "local",
      "command": "npx",
      "args": ["-y", "@upstash/context7-mcp@latest"]
    }
  }
}
```

**STEP 0C-3** — Validasi JSON setelah tulis. Jika JSON invalid → output error dan minta user fix manual:

```text
[PREREQ 0C] Context7 → FAILED: config.json tidak valid JSON setelah edit.
Path: ~/.config/opencode/config.json
Action: perbaiki manual, lalu jalankan ulang setup.
STOP.
```

Jika valid → output:

```text
[PREREQ 0C] Context7 → configured in ~/.config/opencode/config.json
```

---

### 0D — Prerequisites Summary

Setelah 0A–0C selesai, output:

```text
[PREREQ SUMMARY]
0A Caveman plugin : <installed — /caveman ultra | FAILED — hard rules via skill>
0B Graphify CLI   : <found <ver> | installed <ver> | skipped — install manually>
0C Context7 MCP   : <configured | FAILED>
```

Lanjut ke STEP 1 hanya jika 0C tidak FAILED.

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

````markdown
# OpenCode — Personal Global Config V3

# Skills: ~/.config/opencode/skills/

# Commands: ~/.config/opencode/commands/

# Memory: ~/.config/opencode/memory/

# Mode: standalone OpenCode, flexible workflow, graphify-assisted, real caveman injection

## Core Behavior

- Concise. Direct. No over-explanation.
- Single user. Optimize for workflow only.
- Clarify only when ambiguity affects safety, architecture, or irreversible work.
- Do not expand scope silently.
- Default output: caveman ultra dengan hard caps per-block (lihat skills/caveman.md).
- Sertakan confidence + uncertainties untuk plan/analysis formal atau saat risk tinggi.
- Boleh edit file saat user jelas meminta perubahan. Untuk aksi sensitif, wajib izin eksplisit.

## Output Style — Caveman Ultra (Default, Real Injection)

Caveman ultra **bukan hanya plugin output style**. Di V3, caveman adalah **execution policy** dengan hard caps per-block, di-inject ke setiap skill workflow.

Detail lengkap rules + caps + sub-skills: lihat `~/.config/opencode/skills/caveman.md`.

**Caveman ultra WAJIB aktif di SEMUA jalur:**

- Jalur python (AGENT_PATH valid + python invoke).
- Jalur fallback (no AGENT_PATH, agent utama eksekusi sendiri).
- Dengan atau tanpa `[WORKFLOW_AGENT]` tag.
- Untuk evidence output, reasoning output, dan plan output.

Hard caps per-block (ringkasan — detail di skills/caveman.md):

| Block           | Max items / lines | Max chars/line |
| --------------- | ----------------- | -------------- |
| `[REASONING]`   | 10 baris          | 80             |
| `findings`      | 6 items           | 80             |
| `uncertainties` | 5 items           | 80             |
| `steps` (plan)  | 7 items           | 120            |
| `risks`         | 5 items           | 80             |
| evidence list   | 8 items           | 80             |

Exceed cap → trim, prioritize highest-severity. Sub-skills: `/.commit`, `/.review`, `/.compress`.

Switch mode jika perlu (plugin-level): `/caveman lite | full | ultra`.

Off toggle (per session): "normal mode" atau "stop caveman" → balik ke verbose lite.

## Startup Protocol

Setiap session untuk code task:

1. Aktifkan caveman ultra jika belum auto-active: `/caveman ultra`. Hard rules dari `skills/caveman.md` selalu aktif.
2. **WAJIB cek `graphify-out/` di project root sebelum eksplorasi apa pun. Gunakan `Test-Path` sebagai sumber kebenaran utama untuk eksistensi folder.**
   - Jika `Test-Path` true → OpenCode WAJIB baca `GRAPH_REPORT.md` dan/atau `graph.json` langsung dari project sebagai primary evidence.
   - Jangan delegasikan pengecekan ini ke workflow agent. Workflow boleh dipakai setelah evidence graph lokal sudah dicek.
   - Supplement dengan direct file read hanya jika graph data tidak cukup spesifik.
   - Jika `Test-Path` false → jalankan Graphify Missing Protocol untuk task eksplorasi/analisis; untuk task sederhana lanjut file/search langsung.
3. Gunakan Context7 MCP saat butuh dokumentasi library/framework terkini sebelum menjawab pertanyaan API.
4. Baca `~/.config/opencode/memory/PERSONAL_MEMORY.md` jika relevan dan tidak kosong.
5. **WAJIB generate `MAIN_SESSION_ID` di awal setiap sesi chat agent utama**: `main_YYYYMMDD_HHMMSS`.
   - Simpan `MAIN_SESSION_ID` di context/memory sesi chat agent utama.
   - **Dalam 1 sesi chat agent utama, hanya boleh ada 1 `MAIN_SESSION_ID`.**
   - Jangan regenerate session ID di tengah sesi kecuali user eksplisit minta reset.
   - Semua invoke ke workflow agent dalam sesi yang sama WAJIB pakai `MAIN_SESSION_ID` yang identik.
   - Hubungan: **1 sesi chat agent utama = 1 session workflow agent.**

## Session Handling Rule (WAJIB)

**1 sesi chat agent utama = 1 session workflow agent.**

- Di awal setiap sesi chat, agent utama WAJIB generate `MAIN_SESSION_ID` (`main_YYYYMMDD_HHMMSS`) dan simpan di context/memory.
- Sebelum setiap invoke ke workflow agent, WAJIB cek apakah `MAIN_SESSION_ID` sudah ada di context.
  - Jika sudah ada → reuse ID tersebut.
  - Jika belum ada → generate baru, simpan, dan pakai.
- **Jangan pernah generate session ID baru di tengah sesi chat agent utama.**
- Session baru workflow agent hanya dibuat kalau agent utama memulai chat baru.
- Semua skill commands dalam satu sesi chat WAJIB pakai `MAIN_SESSION_ID` yang identik.

## Session Context Cache (V3 — Exploration Cache Contract)

OpenCode simpan hasil command evidence terakhir di context sesi untuk reuse antar skill:

| Key                   | Diisi oleh  | Dipakai oleh           |
| --------------------- | ----------- | ---------------------- |
| `LAST_EXPLORE_RESULT` | `/.explore` | `/.plan`, `/.analyze`  |
| `LAST_PLAN_RESULT`    | `/.plan`    | `/.execute`, `/.audit` |
| `LAST_EXECUTE_DIFF`   | `/.execute` | `/.verify`, `/.audit`  |
| `LAST_AUDIT_RESULT`   | `/.audit`   | `/.plan` (re-fix loop) |

**Aturan reuse:**

- Saat invoke `/.plan` atau `/.analyze`, cek `LAST_EXPLORE_RESULT` di context.
- Jika ada → embed summary di prompt python:
  `"<task>\n\n[PRIOR_EVIDENCE]\n<LAST_EXPLORE_RESULT content>"`
- Tujuan: python tidak re-explore subsystem yang sudah dipetakan.
- Cache valid hanya dalam `MAIN_SESSION_ID` yang sama. Reset session = reset cache.

## Command Registry V3

Workflow commands (evidence):

- `/.explore <hint>` — evidence gathering, graphify-first
- `/.plan <task>` — plan dengan reasoning layer (reuse LAST_EXPLORE)
- `/.analyze <topic>` — analysis tanpa code changes (reuse LAST_EXPLORE)

Workflow commands (action):

- `/.execute -y` — local implementation dari plan aktif dengan contract sanity check
- `/.verify` — full verification
- `/.verify-quick` — lightweight verify (syntax/lint only)
- `/.refactor <scope>` — plan + execute sequence
- `/.audit [scope]` — cross-model audit (preferensi model berbeda dari executor)

Utility:

- `/.memory <note>` — propose memory update
- `/.help` — show command guide

Caveman family:

- `/.commit` — caveman commit message (≤50 char subject)
- `/.review` — one-line per issue review
- `/.compress <file>` — compress prose ke caveman-speak

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
- audit perubahan → `/.audit`
- catat → `/.memory`
- commit message → `/.commit`
- review PR → `/.review`
- compress dokumen → `/.compress <file>`
- docs library / versi terbaru → Context7 MCP
- help → `/.help`

Jangan suggest `/` commands tanpa titik.

## Workflow

Default safe flow untuk task besar atau berisiko:

```text
/.explore → /.plan → /.execute -y → /.verify → /.audit (opsional, cross-model)
```

For refactor:

```text
/.refactor <scope> → auto /.verify
```

For quick fix (task kecil):

```text
prompt natural → /.execute -y → /.verify-quick
```

## Skill Command Enforcement

**WAJIB invoke agent-workflow untuk skill command evidence. Action command tertentu bisa lokal sesuai mapping.**

### Detection flow

1. Detect apakah user prompt adalah skill command → cek prefix `/.` + match command registry.
2. Jika match skill command:
   - Ikuti routing per command mapping. Jangan asumsi semua skill command wajib python.
   - Untuk command yang mapped local, boleh langsung jalankan logic lokal (search/read/edit).
   - Untuk command yang mapped python, **WAJIB** jalankan multi-layer check (L1–L5) AGENT_PATH dahulu.
   - Berdasarkan hasil check, tentukan jalur:
     - **Evidence commands** (`/.explore`, `/.plan`, `/.analyze`):
       - OpenCode WAJIB cek dan baca `graphify-out/` langsung dulu sebelum invoke python. Cek eksistensi folder wajib pakai `Test-Path`; `glob` tidak cukup sebagai bukti tidak ada.
       - L1–L5 semua pass + `Test-Path` graphify-out true → **PYTHON INVOKE** (lihat invocation protocol).
       - Ada layer fail → tanya user: lanjut tanpa AGENT_PATH (fallback)? Jika yes → **MODE FALLBACK**. Jika no → STOP.
       - `Test-Path` graphify-out false → STOP, suruh `graphify update`.
     - **Local action command** (`/.execute -y`):
       - Implement lokal berbasis `LAST_PLAN_RESULT` jika ada.
       - Jika plan cache tidak ada atau tidak cukup, boleh baca source yang relevan lalu implement minimal.
       - Setelah execute, **WAJIB** lanjut verify (`/.verify` atau `/.verify-quick`) secara lokal.
     - **Python action commands** (`/.verify`, `/.verify-quick`, `/.refactor`, `/.audit`):
       - L1–L5 semua pass → **PYTHON INVOKE**.
       - Ada layer fail → **HARD STOP**. Tidak ada fallback.
   - Parse response JSON {ok, content, meta}, **WAJIB tunggu field `ok` ter-parse** sebelum proses dianggap selesai.
3. Jika bukan skill command (prompt natural tanpa `/.`):
   - Boleh pilih antara invoke agent-workflow atau langsung lokal sesuai efisiensi.

### Command mapping

| User Command     | Agent `-c` arg   | Type     | Fallback? | Notes                            |
| ---------------- | ---------------- | -------- | --------- | -------------------------------- |
| `/.explore`      | `explore`        | evidence | yes       | Set `LAST_EXPLORE_RESULT`        |
| `/.plan`         | `plan`           | evidence | yes       | Reuse `LAST_EXPLORE_RESULT`      |
| `/.analyze`      | `analyze`        | evidence | yes       | Reuse `LAST_EXPLORE_RESULT`      |
| `/.execute -y`   | local_execute    | action   | n/a       | Implement local dari plan cache  |
| `/.verify`       | `verify`         | action   | no        | Full check                       |
| `/.verify-quick` | `verify_quick`   | action   | no        | Lightweight: syntax/lint only    |
| `/.refactor`     | (plan + execute) | action   | no        | Sequence                         |
| `/.audit`        | `audit`          | action   | no        | Cross-model preferred            |

### Caveman family (no python invoke — local skill execution)

| User Command     | Skill file          | Notes                        |
| ---------------- | ------------------- | ---------------------------- |
| `/.commit`       | `skills/caveman.md` | Sub-skill: commit message    |
| `/.review`       | `skills/caveman.md` | Sub-skill: one-line review   |
| `/.compress <f>` | `skills/caveman.md` | Sub-skill: prose compression |

Error bila user pakai python action command tapi agent-workflow unavailable (L1–L5 gagal) → inform user, STOP. `/.execute -y` tidak pakai workflow.

Natural prompt tanpa `/.` → optional invoke agent-workflow (agent judgment).

## Evidence Gathering + Reasoning Layer Rule

`[WORKFLOW_AGENT]` tag adalah konsep INTERNAL python (workflow agent inject ke prompt-nya sendiri saat menjalankan command evidence). **OpenCode TIDAK PERLU inject `[WORKFLOW_AGENT]` saat memanggil python.**

### Semantics `[WORKFLOW_AGENT]`

- Saat workflow agent (python side) melihat tag `[WORKFLOW_AGENT]` di context-nya → fokus utama **evidence gathering**, bukan reasoning.
- Workflow agent WAJIB manfaatkan `graphify-out/` sebagai sumber struktur utama.
- Caveman ultra **WAJIB aktif** di sisi workflow agent saat menghasilkan evidence (output `content` harus terkompresi sesuai caps di `skills/caveman.md`).
- Aturan ini berlaku dengan/tanpa `[WORKFLOW_AGENT]` tag — caveman ultra tetap nyala.
- Ini TIDAK menggantikan kewajiban OpenCode untuk cek dan baca `graphify-out/` langsung di project terlebih dulu.

### OpenCode Side (reading response)

Saat menerima response JSON dari python:

1. **WAJIB tunggu parse field `ok`** sebelum proses dianggap selesai.
2. `ok: false` → output error dari `content`, STOP.
3. `ok: true` → `content` adalah evidence/result. Lakukan:
   - **Untuk `/.explore`**: tampilkan evidence langsung. **Set `LAST_EXPLORE_RESULT` di context cache.**
   - **Untuk `/.plan`, `/.analyze`**: lakukan **REASONING LAYER** di atas evidence — sesuai caps caveman ultra. **Set `LAST_PLAN_RESULT` (untuk plan).**
   - **Untuk `/.execute`**: set `LAST_EXECUTE_DIFF` di context (untuk reuse oleh `/.audit`).
   - **Untuk `/.audit`**: lakukan **REASONING LAYER** untuk prioritize findings.
4. Reasoning layer WAJIB ikut caps caveman ultra: max 10 baris, telegraphic.

### Fallback Mode (no AGENT_PATH)

Saat user setuju lanjut tanpa AGENT_PATH (hanya untuk evidence commands):

- Tidak ada python call → tidak ada `[WORKFLOW_AGENT]` tag.
- OpenCode lakukan **gabungan**: evidence gathering + reasoning langsung sebagai agent utama.
- WAJIB tetap pakai `graphify-out/` sebagai struktur awal.
- Caveman ultra tetap aktif dengan caps yang sama.
- Output tidak wajib pakai format `[EVIDENCE]` block — ringkas, ikut caps.
- Tetap set context cache (`LAST_EXPLORE_RESULT`, dst) untuk reuse skill berikutnya.

## Agent-Workflow Invocation via Env Variable

`AGENT_PATH` adalah env variable yang di-set user setelah clone project `agent-workflow`.
Config ini hanya menggunakannya — tidak pernah men-setup atau mengubah nilainya.

### Precondition

`AGENT_PATH` harus sudah di-set oleh user sebelum OpenCode dipakai.
Cara setup ada di README project `agent-workflow` — bukan tugas config ini.

### Invocation Pattern

Dari PowerShell:

```powershell
python $env:AGENT_PATH -c <command> -p "<prompt>" -s "<session>" -w "<work_dir>" --pretty
```

Dari Python subprocess:

```python
import os, subprocess, json

script = os.environ.get("AGENT_PATH")
args = ["python", script, "-c", command, "-p", prompt, "-s", session, "-w", work_dir, "--pretty"]
result = subprocess.run(args, capture_output=True, text=True)
# WAJIB parse JSON dan cek field "ok" sebelum proses dianggap selesai.
data = json.loads(result.stdout)
if "ok" not in data:
    # belum selesai — handle error
    ...
```

**JANGAN inject `[WORKFLOW_AGENT]` di prompt yang dikirim ke python.** Python handle internal.

### Prompt Composition (V3 — exploration cache + contract hint)

Saat invoke `/.plan` atau `/.analyze`, prompt python diaugmentasi:

```text
<task>

[PRIOR_EVIDENCE]
<LAST_EXPLORE_RESULT content jika ada>
```

Saat menjalankan `/.execute` secara lokal, instruction implementasi diaugmentasi dengan contract hint berdasarkan plan:

```text
<execute instruction>

[CONTRACT_AWARENESS]
- DTO/serialization touched: <yes/no>
- Async/queue touched: <yes/no>
- Config/env touched: <yes/no>
```

Hint ini memaksa executor lokal untuk eksplisit consider runtime contract sebelum modify.

### Contoh Invocation

```powershell
python $env:AGENT_PATH -c explore -p "cari entry point auth" -s "main_20260511_080000" -w "E:\Work\project" --pretty
python $env:AGENT_PATH -c analyze -p "cek logic auth\n\n[PRIOR_EVIDENCE]\n..." -s "main_20260511_080000" -w "E:\Work\project" --pretty
python $env:AGENT_PATH -c plan -p "buat fitur payment\n\n[PRIOR_EVIDENCE]\n..." -s "main_20260511_080000" -w "E:\Work\project" --pretty
python $env:AGENT_PATH -c audit -p "review last execute git diff" -s "main_20260511_080000" -w "E:\Work\project" --pretty
```

Override model (deviasi dari `config/opencode.json`):

```powershell
python $env:AGENT_PATH -c plan -p "..." -s "..." -w "..." -m "anthropic/claude-sonnet-4-5" --pretty
```

Cross-model audit (pakai model berbeda dari executor untuk independen review):

```powershell
python $env:AGENT_PATH -c audit -p "..." -s "..." -w "..." -m "moonshot/kimi-k2.6" --pretty
```

### Response Format (Contract V3)

Contract JSON yang dikembalikan agent-workflow:

```python
def normalize_output(*, ok: bool, content: str, meta: dict | None = None) -> dict:
    return {
        "ok": ok,
        "content": content,
        "meta": meta or {},
    }
```

| Field     | Type   | Description                                                                                |
| --------- | ------ | ------------------------------------------------------------------------------------------ |
| `ok`      | bool   | `true` = sukses, `false` = error. WAJIB ter-parse sebelum proses dianggap selesai.         |
| `content` | string | Evidence (untuk evidence commands) atau result (untuk action commands). Atau error string. |
| `meta`    | object | Metadata: confidence, model, session_id, opencode_session_id, token_usage, dll.            |

### WAJIB: Wait For `ok` Flag (NON-NEGOTIABLE)

- **Proses python invoke BELUM SELESAI sampai field `ok` ter-parse dari JSON output.**
- Sebelum kasih hasil final ke user, OpenCode WAJIB tunggu output JSON valid dengan key `ok` (bool).
- Jika output bukan JSON valid → treat sebagai error, STOP. Jangan lanjut.
- `ok: false` → tampilkan `content` sebagai error message, STOP.
- `ok: true` → lanjut: tampilkan evidence (untuk /.explore) atau reasoning layer (untuk /.plan, /.analyze, /.audit) atau result (untuk /.execute, /.verify, /.verify-quick).

### Token Budget Tracking

Jika `meta.token_usage` tersedia di response, OpenCode akumulasi per `MAIN_SESSION_ID`:

- **Soft warn** (cumulative ≥ 50k): tampilkan:
  ```text
  [TOKEN BUDGET WARN]
  Session usage: <total>k token (threshold 50k tercapai).
  Saran: pertimbangkan /.compress di file panjang, atau wrap up task aktif.
  ```
- **Hard gate** (cumulative ≥ 100k): minta permission:
  ```text
  [TOKEN BUDGET GATE]
  Session usage: <total>k token (threshold 100k).
  Lanjut walau sudah 100k? (yes/no)
  ```
  User "no" → STOP. User "yes" → lanjut, threshold berikutnya 150k (strong recommend reset session).

User boleh override: "ignore token budget for this session".

### Multi-Layer Check (Pre-Invoke)

Jalankan semua layer secara berurutan. Setiap layer dicoba — stop di layer pertama yang gagal.

**Layer 1 — Env variable exists**

```powershell
if (-not $env:AGENT_PATH) { ... }
```

Gagal → output `[CHECK FAILED — L1: ENV NOT SET]`.

**Layer 2 — Path exists on disk**

```powershell
if (-not (Test-Path $env:AGENT_PATH)) { ... }
```

Gagal → output `[CHECK FAILED — L2: FILE NOT FOUND]`.

**Layer 3 — File adalah Python script**

```powershell
if (-not $env:AGENT_PATH.EndsWith(".py")) { ... }
```

Gagal → output `[CHECK FAILED — L3: INVALID FILE TYPE]`.

**Layer 4 — Python runtime tersedia**

```powershell
python --version 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { ... }
```

Gagal → output `[CHECK FAILED — L4: PYTHON NOT FOUND]`.

**Layer 5 — Script callable (smoke test)**

```powershell
python $env:AGENT_PATH --help 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { ... }
```

Gagal → output `[CHECK FAILED — L5: SCRIPT NOT CALLABLE]`.

### Post-Check Routing

Setelah multi-layer check:

- **Semua layer PASS** → AGENT_PATH valid. Lanjut cek graphify-out dengan `Test-Path` (untuk evidence commands). Python action commands boleh invoke python; `/.execute -y` tetap lokal sesuai mapping.
- **Ada layer FAIL** → AGENT_PATH tidak tersedia:
  - **Evidence commands** → output error layer + tanya user opsi fallback (yes/no).
  - **Python action commands** → output error layer + **HARD STOP**.

### Rules

- Jalankan semua 5 layer check sebelum setiap invocation pertama dalam session. Untuk invocation berikutnya dalam session yang sama, cukup Layer 1–2.
- Jangan hardcode path script. Selalu baca dari env.
- Jangan modify env variable dari dalam skill atau command.
- Jangan inject `[WORKFLOW_AGENT]` ke prompt python.
- Invocation yang mengirim data ke external API tetap wajib Permission Gate.

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

Caveman caps tetap berlaku: `uncertainties` max 5 items, 80 char/line.

## Graphify Rules

`graphify-out/` adalah default primary source untuk codebase understanding. **WAJIB cek lebih dulu sebelum setiap eksplorasi. Cek eksistensi folder wajib pakai `Test-Path`.**

### Default Behavior

- **Setiap task eksplorasi, analisis, atau planning → cek `graphify-out/` pertama dengan `Test-Path -LiteralPath "<root>\\graphify-out"`.**
- `glob` boleh dipakai setelahnya untuk cari isi atau file turunan, tapi tidak boleh jadi satu-satunya dasar menyimpulkan folder tidak ada.
- OpenCode sendiri WAJIB baca `GRAPH_REPORT.md` untuk summary; `graph.json` untuk detail node/edge.
- Jangan andalkan workflow agent sebagai satu-satunya pembaca graph.
- Supplement dengan direct file read hanya jika graph data tidak cukup spesifik.
- Untuk evidence commands, graphify-out **WAJIB ada** di kedua jalur (python invoke maupun fallback). Jika `Test-Path` false → STOP, suruh `graphify update`.

### Official Commands

- `graphify update` — build/refresh graph. Wajib permission gate sebelum run.
- NEVER run: `graphify init`, `graphify build`, `graphify watch`.

### Error Handling

- `too large for HTML viz` / `Graph has too many nodes` → IGNORE viz error, tetap baca JSON data.
- Error lain → retry once. Masih gagal → inform 1 line, lanjut tanpa graph.

## Graphify Missing Protocol

Jika user meminta workflow graphify-first dan `Test-Path` untuk `graphify-out/` false:

1. Detect framework dari sinyal minimal (Laravel/Python/NestJS/Next.js/React/Rust/Flutter).
2. Buat `.graphifyignore` sesuai template.
3. Output `.graphifyignore` content + suruh user run `graphify update`.
4. STOP untuk skill commands evidence.

## Context7

MCP tool untuk dokumentasi library/framework terkini. Default: gunakan sebelum menjawab pertanyaan API/method jika versi mungkin berbeda dari training knowledge.

### When to Use

- User tanya API, method, config, atau signature library spesifik.
- Perlu verifikasi penggunaan library yang sering update.

### MCP Tools

- `resolve-library-id` — resolve nama library ke Context7 library ID.
- `get-library-docs` — ambil docs untuk library ID + topic.

### Rules

- Gunakan Context7 SEBELUM menjawab jika tidak yakin apakah API/method sudah berubah.
- Jangan hallucinate method/signature.
- Context7 unavailable → inform 1 line, lanjut dari training knowledge.

## Execution Safety

- `/.execute` tanpa `-y` → gate only, output `[EXECUTION SCOPE]`, STOP.
- `/.execute -y` → implement lokal berbasis plan aktif; boleh edit hanya file dalam execution scope.
- Sebelum local execute → contract sanity check (DTO/async/config flags).
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

Sensitive actions: bulk delete, git remote mutation, git history destructive, dependency install, config/env/secret changes, network side effects, permission changes, large generated changes.

Read-only safe actions tidak perlu izin (search, list, read, git status/diff/log, local tests).

## Verify Rules

- Setelah `/.execute -y` atau `/.refactor`, auto-trigger verify (`/.verify` atau `/.verify-quick` tergantung scope).
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
- Output verbose/bertele-tele — caveman ultra dengan caps selalu aktif.
- Jawab pertanyaan API library spesifik dengan hallucinated signature tanpa cek Context7.
- Skip cek `graphify-out/` dengan `Test-Path` sebelum eksplorasi codebase.
- Skip read langsung `graphify-out/` oleh OpenCode sebelum invoke workflow evidence.
- Inject `[WORKFLOW_AGENT]` tag ke prompt python — python handle internal.
- Anggap proses python invoke selesai sebelum parse field `ok` dari JSON.
- Lanjut ke reasoning layer atau output final saat `ok: false` — WAJIB STOP.
- Fallback ke mode lokal untuk python action commands — hanya boleh fallback untuk evidence.
- Skip cek graphify-out/ di mode fallback evidence commands — graphify-out tetap wajib.
- **Output reasoning melebihi caps caveman ultra** (lihat skills/caveman.md tabel caps).
- **Skip exploration cache reuse** — jika `LAST_EXPLORE_RESULT` ada di session, WAJIB pass sebagai PRIOR_EVIDENCE ke `/.plan` dan `/.analyze`.
- **Skip contract sanity check pre-execute** — minimal flag DTO/async/config touchpoints.
- **Pakai model yang sama dengan executor untuk `/.audit`** — tujuan audit adalah independent review, harus cross-model.
- **Abaikan token budget threshold** kecuali user eksplisit override.
````

---

## STEP 3 — Buat skill files

Untuk setiap skill file: overwrite isi file. Skill adalah template, bukan data user.

---

### FILE: `~/.config/opencode/skills/caveman.md` (NEW — TULIS PERTAMA)

````markdown
# Skill: caveman

version: ultra
description: Token compression — execution policy, real injection, not just plugin style

## Status

ALWAYS ON. Default sejak first message. Hard rules + caps di bawah — non-negotiable.

## Hard Rules (NON-NEGOTIABLE)

1. Drop: artikel, filler (`just`/`really`/`basically`/`sure`/`happy to`/`I'll`/`feel free`), pleasantries, hedging, preamble.
2. Fragments OK. Short synonyms. Abbreviasi maksimal.
3. Pattern: `[thing] [action] [reason]. [next step].`
4. Code/paths/commands/filenames: **TIDAK BERUBAH.** Teknis exact.
5. Structured block labels (`[PLAN]`, `[EVIDENCE]`, dst): **TETAP** (parsability).
6. Confidence/uncertainty values: **TETAP** (data, bukan prose).

## Per-Section Caps (HARD LIMITS)

| Block             | Max items / lines | Max chars/line | Notes                           |
| ----------------- | ----------------- | -------------- | ------------------------------- |
| `[REASONING]`     | 10 baris          | 80             | Telegraphic, no narrative prose |
| `findings`        | 6 items           | 80             | One concept per line            |
| `uncertainties`   | 5 items           | 80             |                                 |
| `steps` (plan)    | 7 items           | 120            | 1-2 lines max per step          |
| `risks`           | 5 items           | 80             |                                 |
| `entry_points`    | 8 items           | 80             |                                 |
| `related_modules` | 8 items           | 80             |                                 |
| `evidence list`   | 8 items           | 80             |                                 |
| audit findings    | 10 items total    | 80             | Prioritize P0 > P1 > P2 > P3    |

**Exceed cap** → trim. Prioritize: highest severity, highest impact, most specific.
Drop generic / restate / obvious items.

## Output Pattern Examples

```text
BAD:  "The reason your component re-renders is because you're creating a new object reference inside the render function."
GOOD: "Inline obj → new ref → re-render. Wrap useMemo."

BAD:  "I'd be happy to help you understand the authentication flow."
GOOD: "Auth flow: token check → middleware → route guard."

BAD:  "Based on my analysis of the codebase, it appears that the ChatService class is the primary entry point for handling chat requests."
GOOD: "ChatService = chat entry. Routes via Orchestrator."
```

## Integration dengan Skills

- Reasoning prose di `[PLAN]`, `[ANALYSIS]`, `[AUDIT]`: compressed per caps.
- Structured block labels: tetap (untuk parsability).
- Code snippets: tidak berubah.
- Setiap skill (`plan`, `analyze`, `audit`) WAJIB cap `[REASONING]` max 10 baris.

## Inject Rule (V3 — Real Injection)

Untuk skill yang invoke python: tambahkan hint caveman di prompt body:

```text
<task or instruction>

[OUTPUT_STYLE]
caveman ultra. Reasoning max 10 baris telegraphic. Findings max 6 items.
```

Ini bukan inject `[WORKFLOW_AGENT]` (itu python-internal). Ini hint output style.

## Off Toggle

User ketik: `"normal mode"` atau `"stop caveman"` → switch ke lite mode untuk session itu saja.
Default selalu ultra di session berikutnya kecuali user eksplisit.

## Sub-Skills

### /.commit

Trigger: `/.commit`
Output: Conventional Commits format. ≤50 char subject line. Why > what.
Format: `<type>(<scope>): <subject>`

Example:

```text
fix(auth): token expiry use <= not <
feat(orchestrator): add cross-model audit step
refactor(dto): split serialization boundary
```

### /.review

Trigger: `/.review <file or PR ref>`
Output: one-line per issue.
Format: `L{n}: {severity_emoji} {type}: {problem}. {fix}.`
Severity: 🔴 P0 (critical), 🟡 P1 (high), 🟢 P2 (medium), ⚪ P3 (low).

Example:

```text
L42: 🔴 null-deref: user unguarded. Add null check.
L87: 🟡 race: shared state no lock. Use Mutex.
L120: 🟢 perf: O(n²) loop. Use Set lookup.
```

### /.compress

Trigger: `/.compress <filepath>`
Action: rewrite prose di file ke caveman-speak. Code/paths/commands tidak diubah.
Output: compressed file + backup `<filename>.original.md`.
Use case: memory files, CLAUDE.md, config docs — kurangi input token tiap session.

Execution:

1. Read original file.
2. Create backup `.original.md`.
3. Rewrite prose paragraphs sesuai hard rules + caps.
4. Preserve code blocks, paths, commands, structured labels.
5. Write compressed version.
````

---

### FILE: `~/.config/opencode/skills/explore.md`

````markdown
# Skill: explore

description: Codebase exploration — graphify-first, agent-workflow + fallback option

Skill ini optional. Gunakan saat user memakai `/.explore` atau intent eksplorasi besar/flow kompleks. Untuk prompt natural sederhana, boleh pakai search/read langsung.

## Trigger

`/.explore <hint>`

## Pre-Flow Check

### STEP A — Multi-layer check AGENT_PATH

Jalankan L1–L5 check per protocol global config:

- **Semua layer PASS** → AGENT_PATH valid, lanjut STEP B.
- **Ada layer FAIL** → output error layer + tanya user:

  ```text
  [AGENT_PATH UNAVAILABLE]
  <error dari layer yang gagal>

  Lanjut /.explore tanpa AGENT_PATH (mode fallback: graphify-out + search/read lokal)? (yes/no)
  ```

  - User "no" → STOP.
  - User "yes" → lanjut ke **MODE FALLBACK**.

### STEP B — Cek graphify-out/ exists

Gunakan PowerShell berikut sebagai cek utama:

```powershell
Test-Path -LiteralPath "<project_root>\graphify-out"
```

Jika hasil `False`, baru perlakukan graphify-out tidak ada. Jangan pakai `glob` sebagai satu-satunya bukti negatif.

- Ada → lanjut **PYTHON INVOKE** (STEP C).
- Tidak ada → STOP, suruh `graphify update`.

## STEP C — Python Invoke

### STEP C1 — Tentukan session

Reuse `MAIN_SESSION_ID` atau generate baru.

### STEP C2 — Invoke python dengan caveman hint

```powershell
python $env:AGENT_PATH -c explore -p "<hint>

[OUTPUT_STYLE]
caveman ultra. entry_points max 8, related_modules max 8, uncertainties max 5." -s "<session_id>" -w "<workspace_root>" --pretty
```

Tanpa inject `[WORKFLOW_AGENT]`. Tetap kirim `[OUTPUT_STYLE]` hint.

### STEP C3 — WAJIB tunggu JSON {ok, content, meta}

Parse JSON. **Proses BELUM SELESAI sampai `ok` ter-parse.**

- Output bukan JSON valid → STOP, tampilkan raw + error.
- `ok: false` → output `[EXPLORE FAILED]` dengan content, STOP.
- `ok: true` → lanjut STEP C4.

### STEP C4 — Output evidence + Set Cache

Tampilkan content (caveman-compressed dari workflow agent).

```text
[EXPLORE RESULT]
session: <session_id>
source:  agent-workflow (python)

<content dari JSON>

confidence: <dari meta>
```

**WAJIB**: Set `LAST_EXPLORE_RESULT` di context cache sesi untuk reuse oleh `/.plan` & `/.analyze`.

### STEP C5 — Token budget check

Jika `meta.token_usage` ada, akumulasi cumulative session. Trigger warn/gate sesuai threshold (50k/100k).

End with: `Lanjut /.plan atau /.analyze?`

## MODE FALLBACK — Tanpa AGENT_PATH

Caveman ultra **tetap aktif** dengan caps. Graphify-out **wajib ada**.

### STEP F1 — Cek graphify-out/

Gunakan `Test-Path -LiteralPath "<project_root>\graphify-out"` sebagai gate utama.

- Tidak ada → STOP, suruh `graphify update`.

### STEP F2 — Eksplorasi sebagai agent utama

1. Baca `GRAPH_REPORT.md` + `graph.json` untuk struktur awal.
2. Identifikasi entry_points (max 8), related_modules (max 8), ownership.
3. Search/read file spesifik HANYA jika graph data tidak cukup.

### STEP F3 — Output evidence (caveman caps)

```text
[EXPLORE RESULT]
session: <session_id>
source:  fallback (no AGENT_PATH) — graphify-out

entry_points:    <max 8>
related_modules: <max 8>
flow:            <summary 1-2 baris>
uncertainties:   <max 5>
```

**WAJIB**: Set `LAST_EXPLORE_RESULT` di context cache.

End with: `Lanjut /.plan atau /.analyze?`
````

---

### FILE: `~/.config/opencode/skills/plan.md`

````markdown
# Skill: plan

description: Structured planning — evidence gathering + reasoning layer dengan caveman caps

Skill ini optional. Gunakan saat user meminta plan, task besar, banyak file, arsitektur, atau risk tinggi.

## Trigger

`/.plan <task>`

## Pre-Flow Check

### STEP A — Multi-layer check AGENT_PATH

Jalankan L1–L5 check. Ada FAIL → tanya fallback (yes/no).

### STEP B — Cek graphify-out/

Gunakan `Test-Path -LiteralPath "<project_root>\graphify-out"` sebagai gate utama.

- Ada → lanjut **PYTHON INVOKE** (STEP C).
- Tidak ada → STOP, suruh `graphify update`.

### STEP B.5 — Cek exploration cache (V3)

Cek `LAST_EXPLORE_RESULT` di context cache sesi:

- **Ada** → embed sebagai `[PRIOR_EVIDENCE]` di prompt python (lihat STEP C2).
- **Tidak ada** → invoke normal. Workflow agent eksplorasi sendiri.

Tujuan: hindari re-explore subsystem yang sudah dipetakan `/.explore` sebelumnya.

## STEP C — Python Invoke

### STEP C1 — Session

Reuse `MAIN_SESSION_ID` atau generate baru.

### STEP C2 — Invoke dengan PRIOR_EVIDENCE + caveman hint

```powershell
python $env:AGENT_PATH -c plan -p "<task>

[PRIOR_EVIDENCE]
<LAST_EXPLORE_RESULT summary, jika ada>

[OUTPUT_STYLE]
caveman ultra. findings max 6, steps max 7, risks max 5, reasoning max 10 baris." -s "<session_id>" -w "<workspace_root>" --pretty
```

### STEP C3 — WAJIB tunggu JSON {ok, content, meta}

- `ok: false` → output `[PLAN FAILED]` + content, STOP.
- `ok: true` → lanjut STEP C4.

### STEP C4 — Reasoning Layer (WAJIB, dengan caveman caps)

Agent utama lakukan reasoning di atas evidence + `LAST_EXPLORE_RESULT` (jika ada).

**Cap**: max 10 baris, telegraphic, no narrative prose.

Reasoning checklist (compressed):

- Root cause / bottleneck (1-2 baris)
- Trade-off A vs B (1-2 baris)
- Risk + mitigation (2-3 baris)
- Solusi rationale (2-3 baris)

Jangan elaborasi panjang. Cap eksplisit. Drop generic statements.

### STEP C5 — Output `[PLAN]` block

```text
[PLAN]
task:            <restate, ≤1 baris>
session:         <session_id>
evidence_source: agent-workflow (python) | fallback (graphify-out)
prior_explore:   yes | no

[REASONING]
<max 10 baris telegraphic>

[STEPS]
1. <step, 1-2 baris>
... (max 7)

files_affected: <list, ≤8>
risks: <max 5>

confidence:
  problem_understanding: low | medium | high
  root_cause:            low | medium | high
  solution_path:         low | medium | high

uncertainties: <max 5>

decision: proceed | clarify | re-explore
```

### STEP C6 — Set cache + token check

Set `LAST_PLAN_RESULT` di context cache. Cek token budget cumulative.

STOP dan tunggu approval. **JANGAN auto-proceed ke execute.**

End with: `Setuju? /.execute -y`

## MODE FALLBACK — Tanpa AGENT_PATH

Caveman caps tetap. Graphify-out wajib.

### STEP F1 — Cek graphify-out/

Gunakan `Test-Path -LiteralPath "<project_root>\graphify-out"` sebagai gate utama.

Tidak ada → STOP.

### STEP F2 — Evidence gathering sebagai agent utama

1. Baca `GRAPH_REPORT.md` + `graph.json`.
2. Reuse `LAST_EXPLORE_RESULT` jika ada di context cache.
3. Read file spesifik HANYA jika graph data tidak cukup.

### STEP F3 — Reasoning langsung (caveman caps)

Sama checklist STEP C4. Max 10 baris.

### STEP F4 — Output `[PLAN]` block

Sama format STEP C5, `evidence_source: fallback (no AGENT_PATH) — graphify-out`.

Set `LAST_PLAN_RESULT` di cache.

End with: `Setuju? /.execute -y`
````

---

### FILE: `~/.config/opencode/skills/execute.md`

````markdown
# Skill: execute

description: Controlled local implementation — plan-driven, contract-aware, bounded execution

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

Dengan `-y` → proceed. Dengan prompt natural eksplisit → proceed jika low-risk.

## Execution

### STEP 1 — Tentukan session

Reuse `MAIN_SESSION_ID`.

### STEP 2 — Contract Sanity Check (V3 — pre-execute)

Sebelum local execute, klasifikasi perubahan dari `LAST_PLAN_RESULT` atau scope:

| Touchpoint        | Sinyal                                                       |
| ----------------- | ------------------------------------------------------------ |
| DTO/serialization | edit kelas DTO, JSON encoder, Redis serialize, queue payload |
| Async/queue       | edit job, queue, worker, coroutine, await boundary           |
| Config/env        | edit `.env`, config file, feature flag, environment var      |

Generate `[CONTRACT_AWARENESS]` block untuk instruction implementasi lokal:

```text
[CONTRACT_AWARENESS]
- DTO/serialization touched: yes | no
- Async/queue touched: yes | no
- Config/env touched: yes | no
- Note: verify contract before commit untuk yang `yes`.
```

Heuristik ringan — bukan formal schema validation. Tujuan: paksa executor lokal eksplisit consider runtime contract.

### STEP 3 — Implement lokal berbasis plan aktif

1. Reuse `LAST_PLAN_RESULT` jika ada.
2. Jika plan cache tidak cukup, baca source relevan seminimal mungkin.
3. Edit hanya file dalam execution scope.
4. Jangan revert user changes.

### STEP 4 — Output + Set cache

```text
[EXECUTION RESULT]
session: <session_id>
files_changed: <list>
summary: implementasi lokal selesai

status: success
contract_flags: <yang ditandai yes di STEP 2>
```

**WAJIB**: set `LAST_EXECUTE_DIFF` di context cache (gunakan untuk `/.audit` & `/.verify`).

### STEP 5 — Auto-trigger verify

Pilih `/.verify` (full) atau `/.verify-quick` (lightweight) berdasarkan scope:

- Touchpoints sensitive (DTO/async/config yes) → `/.verify` full.
- Touchpoints semua no, perubahan kecil → `/.verify-quick`.

Cek token budget cumulative.

### STEP 6 — Suggest `/.audit` (V3)

Setelah verify pass, suggest cross-model audit:

```text
[NEXT]
- /.audit untuk cross-model review independent terhadap perubahan ini.
- Optional tapi recommended untuk task dengan contract_flags = yes.
```
````

---

### FILE: `~/.config/opencode/skills/verify.md`

````markdown
# Skill: verify

description: Full verification after execute/refactor — syntax, lint, build, integration

## Trigger

`/.verify` (auto-triggered setelah `/.execute -y` atau `/.refactor` dengan scope sensitif)

## Execution

### STEP 1 — Multi-layer check AGENT_PATH (HARD STOP on fail)

Jalankan L1–L5. Ada FAIL → **HARD STOP**, no fallback.

### STEP 2 — Tentukan session

Reuse `MAIN_SESSION_ID`.

### STEP 3 — Invoke python dengan caveman hint

```powershell
python $env:AGENT_PATH -c verify -p "verifikasi perubahan terakhir dengan test/lint/build yang relevan.

[OUTPUT_STYLE]
caveman ultra. findings max 6." -s "<session_id>" -w "<workspace_root>" --pretty
```

### STEP 4 — WAJIB tunggu JSON {ok, content, meta}

- `ok: false` → output `[VERIFY FAILED]` + content, STOP.
- `ok: true` → lanjut.

### STEP 5 — Output

```text
[VERIFY RESULT]
session: <session_id>
<content dari JSON>

status: success | fail
```

Cek token budget cumulative.

### STEP 6 — Suggest `/.audit` (V3)

Jika ada `LAST_EXECUTE_DIFF` di cache → suggest `/.audit` untuk cross-model independent review.
````

---

### FILE: `~/.config/opencode/skills/verify-quick.md` (NEW V3)

````markdown
# Skill: verify-quick

description: Lightweight verification — syntax/lint/type check only, untuk task kecil

Trade-off: lebih cepat, tidak verifikasi async lifecycle / queue serialization / runtime contract.
Gunakan untuk: typo fix, rename, format, comment update, single-line change.
Jangan gunakan untuk: DTO change, config change, async/queue change → pakai `/.verify` full.

## Trigger

`/.verify-quick` (auto-triggered setelah `/.execute -y` dengan scope kecil non-sensitive)

## Execution

### STEP 1 — Multi-layer check AGENT_PATH (HARD STOP on fail)

Jalankan L1–L5. Ada FAIL → **HARD STOP**, no fallback.

### STEP 2 — Tentukan session

Reuse `MAIN_SESSION_ID`.

### STEP 3 — Invoke python dengan lightweight mode

```powershell
python $env:AGENT_PATH -c verify_quick -p "lightweight verify: syntax + lint + type check only. Skip integration test, skip runtime contract validation.

[OUTPUT_STYLE]
caveman ultra. findings max 4." -s "<session_id>" -w "<workspace_root>" --pretty
```

### STEP 4 — WAJIB tunggu JSON {ok, content, meta}

- `ok: false` → output `[VERIFY-QUICK FAILED]` + content, STOP.
- `ok: true` → lanjut.

### STEP 5 — Output

```text
[VERIFY-QUICK RESULT]
session: <session_id>
mode:    lightweight (syntax/lint/type only)
<content dari JSON>

status: success | fail
note: untuk verification penuh (async/contract/integration), jalankan /.verify
```

### STEP 6 — Suggest upgrade jika risiko terdeteksi

Jika content mengandung sinyal risiko async/contract/integration → suggest `/.verify` full:

```text
[NEXT]
- /.verify untuk full check (async lifecycle, runtime contract, integration).
- /.audit untuk cross-model independent review.
```
````

---

### FILE: `~/.config/opencode/skills/audit.md` (NEW V3)

````markdown
# Skill: audit

description: Cross-model audit of recent changes — independent review, bukan self-verification

Background: hasil benchmark riset menunjukkan **audit eksternal jauh lebih efektif menemukan runtime issue dibanding self-verification**. Self-verify model punya blind spot besar (DTO serialization, type mismatch, config inconsistency). `/.audit` mengisi gap ini dengan invoke model **berbeda** dari executor.

## Trigger

`/.audit [scope]`

- Scope optional. Default: `LAST_EXECUTE_DIFF` dari session cache.
- Jika tidak ada cache dan user tidak provide scope → tanya user scope.

## Rules

- Zero code changes (read-only).
- Cross-model preferred: workflow agent route ke model berbeda dari `/.execute`.
- Output: findings P0-P3 dengan relevance terhadap recent changes.
- Reasoning layer: prioritize, suggest fixes.

## Execution

### STEP 1 — Multi-layer check AGENT_PATH (HARD STOP on fail)

Jalankan L1–L5. Ada FAIL → **HARD STOP**. Tidak ada fallback (audit fallback ke same-model = lose the point).

### STEP 2 — Identify scope

- User provide scope → pakai itu.
- Cek `LAST_EXECUTE_DIFF` di context cache → pakai itu.
- Keduanya tidak ada → tanya user:

  ```text
  [AUDIT SCOPE NEEDED]
  Tidak ada execute recent di session ini.
  Provide scope (mis. file path, commit range, "uncommitted")?
  ```

### STEP 3 — Tentukan session

Reuse `MAIN_SESSION_ID`.

### STEP 4 — Invoke python dengan model override hint + caveman

```powershell
python $env:AGENT_PATH -c audit -p "review git diff / scope berikut. Fokus runtime issue: type mismatch, serialization, async contract, config consistency, state transition.

scope: <git diff atau file list>

[OUTPUT_STYLE]
caveman ultra. findings max 10 total (prioritize P0 > P1 > P2 > P3). reasoning max 10 baris." -s "<session_id>" -w "<workspace_root>" --pretty
```

**Catatan untuk workflow agent**: routing rule di `config/opencode.json` SHOULD route `-c audit` ke model berbeda dari `-c execute`. Jika user override via `-m` flag, hormati override.

### STEP 5 — WAJIB tunggu JSON {ok, content, meta}

- `ok: false` → output `[AUDIT FAILED]` + content, STOP.
- `ok: true` → lanjut STEP 6.

### STEP 6 — Reasoning Layer (WAJIB, caveman caps)

Agent utama:

- Prioritize findings by severity (P0 > P1 > P2 > P3).
- Cross-check vs `LAST_PLAN_RESULT` jika ada → finding mana yang lolos planning.
- Identifikasi blind spot dari executor.
- Recommend mitigation per finding (1 baris).

Cap: max 10 baris reasoning.

### STEP 7 — Output `[AUDIT]` block

```text
[AUDIT]
scope:        <git diff atau scope>
session:      <session_id>
source:       agent-workflow (cross-model preferred)
auditor_model: <dari meta jika tersedia>
executor_model: <dari LAST_EXECUTE meta jika tersedia>

findings:
- P0: <critical issue, 1 baris>
- P0: <...>
- P1: <high impact>
- P2: <medium>
- P3: <low>
(max 10 total)

[REASONING]
<max 10 baris: prioritization, blind spots, cross-check vs plan>

uncertainties: <max 5>

recommendation: ship | fix_p0_first | re-execute | revert
```

### STEP 8 — Set cache + token budget

Set `LAST_AUDIT_RESULT` di context cache. Token budget check.

### STEP 9 — Suggest next action

```text
[NEXT]
- Jika ada P0/P1: /.plan untuk fix path, lalu /.execute -y.
- Jika clean: ship.
- Jika ragu: jalankan /.audit ulang dengan model berbeda lagi.
```
````

---

### FILE: `~/.config/opencode/skills/refactor.md`

```markdown
# Skill: refactor

description: Safe scoped refactor with automatic verification — action command

Refactor di-map ke sequence `plan` + `execute` (lihat command mapping di global config).

## Trigger

`/.refactor <scope>`

## Execution

STEP 1 — Restate scope (caveman, ≤2 baris).

STEP 2 — Output `[EXECUTION SCOPE]` (allowed/forbidden/reason).

STEP 3 — Run `/.plan <scope>` workflow:

- L1–L5 check + opsi fallback.
- Reuse `LAST_EXPLORE_RESULT` jika ada.

STEP 4 — Run `/.execute -y` workflow:

- L1–L5 check, HARD STOP on fail.
- Pre-invoke contract sanity check.

STEP 5 — Auto-trigger `/.verify` atau `/.verify-quick` (tergantung scope sensitivity).

STEP 6 — Suggest `/.audit` jika contract_flags = yes.

STEP 7 — Output confidence + uncertainties dari hasil verify (caveman caps).
```

---

### FILE: `~/.config/opencode/skills/analyze.md`

````markdown
# Skill: analyze

description: Analysis with zero code changes — evidence + reasoning layer dengan caveman caps

## Trigger

`/.analyze <topic>`

## Rules

- Zero code changes. Zero file modifications.
- Findings first. Include confidence + uncertainties.
- Reuse `LAST_EXPLORE_RESULT` jika tersedia (V3 exploration cache).

## Pre-Flow Check

### STEP A — Multi-layer check AGENT_PATH

Jalankan L1–L5. Ada FAIL → tanya fallback (yes/no).

### STEP B — Cek graphify-out/

Gunakan `Test-Path -LiteralPath "<project_root>\graphify-out"` sebagai gate utama.

Tidak ada → STOP, suruh `graphify update`.

### STEP B.5 — Cek exploration cache

Cek `LAST_EXPLORE_RESULT`. Ada → embed sebagai PRIOR_EVIDENCE.

## STEP C — Python Invoke

### STEP C1 — Session

Reuse `MAIN_SESSION_ID`.

### STEP C2 — Invoke dengan PRIOR_EVIDENCE + caveman

```powershell
python $env:AGENT_PATH -c analyze -p "<topic>

[PRIOR_EVIDENCE]
<LAST_EXPLORE_RESULT, jika ada>

[OUTPUT_STYLE]
caveman ultra. findings max 6, reasoning max 10 baris." -s "<session_id>" -w "<workspace_root>" --pretty
```

### STEP C3 — WAJIB tunggu JSON {ok, content, meta}

- `ok: false` → output `[ANALYZE FAILED]` + content, STOP.
- `ok: true` → lanjut STEP C4.

### STEP C4 — Reasoning Layer (caveman caps)

Cap: max 10 baris reasoning.

Checklist (compressed):

- Root cause / pattern (2-3 baris)
- Impact assessment (2-3 baris)
- Recommendation (2-3 baris)

### STEP C5 — Output `[ANALYSIS]` block

```text
[ANALYSIS]
topic:         <topic>
session:       <session_id>
source:        agent-workflow (python) | fallback
prior_explore: yes | no

[REASONING]
<max 10 baris>

findings: <max 6>

implications: <max 5>

confidence: <dari meta>

uncertainties: <max 5>
```

Cek token budget cumulative.

## MODE FALLBACK

Caveman caps tetap. Graphify-out wajib.

### STEP F1 — Cek graphify-out/. Tidak ada → STOP.

Tidak ada = `Test-Path -LiteralPath "<project_root>\graphify-out"` mengembalikan `False`.

### STEP F2 — Analisis sebagai agent utama

1. Baca `GRAPH_REPORT.md` + `graph.json`.
2. Reuse `LAST_EXPLORE_RESULT` jika ada.
3. Search/read file spesifik HANYA jika graph data tidak cukup.
4. Reasoning checklist (max 10 baris).

### STEP F3 — Output `[ANALYSIS]` block

Sama format STEP C5, `source: fallback (no AGENT_PATH) — graphify-out`.
````

---

### FILE: `~/.config/opencode/skills/memory.md`

````markdown
# Skill: memory

description: Propose memory update to personal knowledge files

Skill ini hanya untuk memory jangka panjang. Natural prompt yang berisi catatan tetap butuh confirmation sebelum write.

## Trigger

`/.memory <note>`

## Execution

STEP 1 — Evaluate note (caveman):

- Affect future decisions?
- Architecture/ownership/landmine?
- Recurring issue?

STEP 2 — Output proposal:

```text
[MEMORY PROPOSAL]
file:    ~/.config/opencode/memory/PERSONAL_MEMORY.md | DOMAIN_MAP.md
action:  add | update
content:
<proposed content, caveman compressed>

Confirm? (yes / no / edit)
```

STEP 3 — Wait for user confirmation.

STEP 4 — Only write after `yes` or explicit edited content.
````

---

### FILE: `~/.config/opencode/skills/help.md`

````markdown
# Skill: help

description: Command reference OpenCode personal workflow V3

Commands adalah shortcut opsional. Prompt natural tetap didukung.

## Trigger

`/.help`

## Output

```text
[COMMAND GUIDE — OPENCODE GLOBAL WORKFLOW V3]

Output default: caveman ultra dengan hard caps per-block (lihat skills/caveman.md).
Graphify: default primary source untuk codebase understanding.
Context7: default MCP untuk library/framework docs terkini.
JSON contract: {ok, content, meta} — wajib tunggu parse `ok` sebelum proses selesai.

Natural prompts are valid. Use commands only when you want structured workflow.

=== EVIDENCE COMMANDS (fallback option available) ===

/.explore <hint>
→ explore codebase, graphify-first. Set LAST_EXPLORE_RESULT cache.
→ AGENT_PATH unavailable → opsi fallback (graphify-out + search lokal)

/.plan <task>
→ structured plan + reasoning layer. Reuse LAST_EXPLORE_RESULT.
→ AGENT_PATH unavailable → opsi fallback

/.analyze <topic>
→ analysis tanpa code changes. Reuse LAST_EXPLORE_RESULT.
→ AGENT_PATH unavailable → opsi fallback

=== ACTION COMMANDS (HARD STOP without AGENT_PATH) ===

/.execute -y
→ implement dengan contract sanity check pre-invoke
→ auto-trigger /.verify atau /.verify-quick

/.verify
→ full check: syntax, lint, build, integration, runtime contract

/.verify-quick
→ lightweight: syntax + lint + type only. Skip integration/contract.
→ untuk task kecil non-sensitive (typo, rename, format)

/.refactor <scope>
→ plan + execute sequence + auto verify

/.audit [scope]
→ CROSS-MODEL audit independen terhadap LAST_EXECUTE_DIFF
→ findings P0-P3 + reasoning prioritization
→ recommended setelah /.execute dengan contract_flags = yes

=== UTILITY ===

/.memory <note>
→ propose memory update, requires confirmation

/.help
→ show this guide

=== CAVEMAN FAMILY (lokal, tidak invoke python) ===

/.commit
→ caveman commit message (Conventional Commits, ≤50 char subject)

/.review <file or PR>
→ one-line per issue (🔴 P0, 🟡 P1, 🟢 P2, ⚪ P3)

/.compress <filepath>
→ compress prose file ke caveman-speak (backup .original.md)

=== WORKFLOW PATTERNS ===

Large/risky task:
/.explore → /.plan → /.execute -y → /.verify → /.audit

Quick fix (small task):
prompt natural → /.execute -y → /.verify-quick

Refactor:
/.refactor <scope> → auto /.verify → /.audit (optional)

=== TOKEN BUDGET ===

Soft warn:  50k cumulative per session.
Hard gate:  100k cumulative (minta permission).
Override:   "ignore token budget for this session".

=== NOTES ===

Prefix "/." wajib only for workflow commands. Natural prompts need no prefix.
Verbose mode: balas "normal mode" atau "stop caveman" untuk session itu saja.
Caveman caps WAJIB di semua output reasoning/findings/steps.
```
````

---

## STEP 4 — Buat custom command files

Buat direktori `~/.config/opencode/commands/` jika belum ada. Untuk setiap command file: overwrite isi file.

### FILE: `~/.config/opencode/commands/explore.md`

```markdown
---
description: Agent-workflow powered codebase exploration with fallback option. Sets LAST_EXPLORE_RESULT cache. Usage: /.explore <hint>
---

Read `~/.config/opencode/skills/explore.md` and follow it. Reject `/explore` only when used as slash command; natural prompts remain valid.
```

### FILE: `~/.config/opencode/commands/plan.md`

```markdown
---
description: Plan with reasoning layer (caveman caps). Reuses LAST_EXPLORE_RESULT. Fallback option. Usage: /.plan <task>
---

Read `~/.config/opencode/skills/plan.md` and follow it. Reject `/plan` only when used as slash command; natural prompts remain valid.
```

### FILE: `~/.config/opencode/commands/execute.md`

```markdown
---
description: Local implementation from active plan with contract sanity check. No workflow invoke. Usage: /.execute -y
---

Read `~/.config/opencode/skills/execute.md` and follow it. Reject formal `/.execute` without `-y`. Sensitive actions require Permission Gate.
```

### FILE: `~/.config/opencode/commands/verify.md`

```markdown
---
description: Full verification (syntax, lint, build, integration, contract). AGENT_PATH wajib. Usage: /.verify
---

Read `~/.config/opencode/skills/verify.md` and follow it.
```

### FILE: `~/.config/opencode/commands/verify-quick.md`

```markdown
---
description: Lightweight verification (syntax/lint/type only). Untuk task kecil non-sensitive. Usage: /.verify-quick
---

Read `~/.config/opencode/skills/verify-quick.md` and follow it. Untuk DTO/async/config change pakai /.verify full.
```

### FILE: `~/.config/opencode/commands/refactor.md`

```markdown
---
description: Safe scoped refactor (plan + execute + verify). AGENT_PATH wajib. Usage: /.refactor <scope>
---

Read `~/.config/opencode/skills/refactor.md` and follow it.
```

### FILE: `~/.config/opencode/commands/analyze.md`

```markdown
---
description: Analysis tanpa code changes, reuses LAST_EXPLORE_RESULT, fallback option. Usage: /.analyze <topic>
---

Read `~/.config/opencode/skills/analyze.md` and follow it.
```

### FILE: `~/.config/opencode/commands/audit.md`

```markdown
---
description: Cross-model audit independen terhadap LAST_EXECUTE_DIFF. Findings P0-P3. AGENT_PATH wajib. Usage: /.audit [scope]
---

Read `~/.config/opencode/skills/audit.md` and follow it. Audit fallback to same-model defeats the purpose — HARD STOP without AGENT_PATH.
```

### FILE: `~/.config/opencode/commands/memory.md`

```markdown
---
description: Propose memory update, requires confirmation. Usage: /.memory <note>
---

Read `~/.config/opencode/skills/memory.md` and follow it. Never write memory without confirmation.
```

### FILE: `~/.config/opencode/commands/help.md`

```markdown
---
description: Show OpenCode global workflow command guide V3. Usage: /.help
---

Read `~/.config/opencode/skills/help.md` and output the command guide.
```

### FILE: `~/.config/opencode/commands/commit.md`

```markdown
---
description: Caveman commit message (Conventional Commits, ≤50 char subject). Usage: /.commit
---

Read `~/.config/opencode/skills/caveman.md` section "Sub-Skills → /.commit" and follow it.
```

### FILE: `~/.config/opencode/commands/review.md`

```markdown
---
description: One-line per issue review (🔴 P0, 🟡 P1, 🟢 P2, ⚪ P3). Usage: /.review <file or PR>
---

Read `~/.config/opencode/skills/caveman.md` section "Sub-Skills → /.review" and follow it.
```

### FILE: `~/.config/opencode/commands/compress.md`

```markdown
---
description: Compress prose file ke caveman-speak. Backup .original.md. Usage: /.compress <filepath>
---

Read `~/.config/opencode/skills/caveman.md` section "Sub-Skills → /.compress" and follow it.
```

---

## STEP 5 — Buat memory files

Jangan overwrite memory file jika sudah ada, kecuali `MEMORY.md` index perlu append missing entry.

### FILE: `~/.config/opencode/memory/PERSONAL_MEMORY.md` (skip jika exists)

```markdown
# Personal Memory — OpenCode

Last updated: 2026-05-11

## Architecture Decisions

- (belum ada)

## Module Ownership

| Module | Team | Notes |
| ------ | ---- | ----- |
| -      | -    | -     |

## Known Landmines

- (belum ada)

## Things I Always Forget

- (belum ada)
```

### FILE: `~/.config/opencode/memory/DOMAIN_MAP.md` (skip jika exists)

```markdown
# Domain Map — OpenCode

Last updated: 2026-05-11

## Entry Points

| Domain | Entry File | Key Function |
| ------ | ---------- | ------------ |
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

1. Konfirmasi prerequisites dari STEP 0:
   - Caveman plugin: terinstall atau warning manual.
   - Graphify: `graphify --version` tersedia atau skipped.
   - Context7: `~/.config/opencode/config.json` mengandung key `"context7"`.
2. List `~/.config/opencode/` dan subdirektori.
3. Tampilkan ukuran byte file penting:
   - `AGENTS.md`
   - `config.json`
   - semua skill files (termasuk `caveman.md`, `audit.md`, `verify-quick.md`)
   - semua command files (termasuk `audit.md`, `verify-quick.md`, `commit.md`, `review.md`, `compress.md`)
   - memory index
4. Konfirmasi `AGENTS.md` mengandung:
   - `Command Registry V3`
   - `Session Context Cache (V3 — Exploration Cache Contract)`
   - `Response Format (Contract V3)` dengan `{ok, content, meta}`
   - `WAJIB: Wait For \`ok\` Flag`
   - `Token Budget Tracking`
   - Hard caps per-block table reference
5. Konfirmasi `skills/caveman.md` mengandung:
   - Hard rules + Per-Section Caps table
   - Inject Rule section
   - Sub-skills `/.commit`, `/.review`, `/.compress`
6. Konfirmasi `skills/audit.md` mengandung:
   - Cross-model preference note
   - HARD STOP rule (no fallback)
   - `[AUDIT]` block format
7. Konfirmasi command files mengandung frontmatter `description`.

---

## STEP 7 — Final Report

Tampilkan PERSIS:

```text
[SETUP COMPLETE — OPENCODE GLOBAL WORKFLOW V3]

Prerequisites:
  Caveman plugin → <installed — activate: /caveman ultra | FAILED — hard rules via skill>
  Graphify CLI   → <found <ver> | installed <ver> | skipped — install manually>
  Context7 MCP   → ~/.config/opencode/config.json ✓

Config:
  ~/.config/opencode/AGENTS.md      ✓
  ~/.config/opencode/config.json    ✓

Skills:
  ~/.config/opencode/skills/caveman.md     ✓  ← NEW V3 (real injection + caps)
  ~/.config/opencode/skills/explore.md     ✓  (updated: set cache)
  ~/.config/opencode/skills/plan.md        ✓  (updated: reuse cache + caps)
  ~/.config/opencode/skills/execute.md     ✓  (updated: contract sanity)
  ~/.config/opencode/skills/verify.md      ✓
  ~/.config/opencode/skills/verify-quick.md ✓  ← NEW V3 (lightweight)
  ~/.config/opencode/skills/refactor.md    ✓
  ~/.config/opencode/skills/analyze.md     ✓  (updated: reuse cache + caps)
  ~/.config/opencode/skills/audit.md       ✓  ← NEW V3 (cross-model)
  ~/.config/opencode/skills/memory.md      ✓
  ~/.config/opencode/skills/help.md        ✓  (updated: V3 commands)

Commands:
  ~/.config/opencode/commands/explore.md      ✓
  ~/.config/opencode/commands/plan.md         ✓
  ~/.config/opencode/commands/execute.md      ✓
  ~/.config/opencode/commands/verify.md       ✓
  ~/.config/opencode/commands/verify-quick.md ✓  ← NEW V3
  ~/.config/opencode/commands/refactor.md     ✓
  ~/.config/opencode/commands/analyze.md      ✓
  ~/.config/opencode/commands/audit.md        ✓  ← NEW V3
  ~/.config/opencode/commands/memory.md       ✓
  ~/.config/opencode/commands/help.md         ✓
  ~/.config/opencode/commands/commit.md       ✓  ← NEW V3 (caveman)
  ~/.config/opencode/commands/review.md       ✓  ← NEW V3 (caveman)
  ~/.config/opencode/commands/compress.md     ✓  ← NEW V3 (caveman)

Memory:
  ~/.config/opencode/memory/PERSONAL_MEMORY.md ✓ (new | kept)
  ~/.config/opencode/memory/DOMAIN_MAP.md      ✓ (new | kept)
  ~/.config/opencode/memory/MEMORY.md          ✓ (index updated)

Status: READY

V3 Capabilities:
  Caveman: REAL INJECTION (skills/caveman.md hard rules + per-block caps)
  Exploration cache: LAST_EXPLORE_RESULT reused in /.plan & /.analyze
  Cross-model audit: /.audit invoke model berbeda dari /.execute
  Lightweight verify: /.verify-quick (syntax/lint/type only)
  Contract sanity: pre-invoke DTO/async/config flag di /.execute
  Token budget: soft warn 50k, hard gate 100k cumulative

Workflow patterns:
  Large/risky : /.explore → /.plan → /.execute -y → /.verify → /.audit
  Quick fix   : natural → /.execute -y → /.verify-quick
  Refactor    : /.refactor → auto verify → /.audit (optional)

Active commands (workflow): /.explore /.plan /.execute /.verify /.verify-quick
                            /.refactor /.analyze /.audit /.memory /.help
Active commands (caveman):  /.commit /.review /.compress
Invalid slash commands: /explore /plan /execute /verify /analyze /audit — REJECTED
Natural prompts: VALID
Output default: CAVEMAN ULTRA with per-block caps (skills/caveman.md)
Graphify: PRIMARY SOURCE (wajib ada untuk evidence commands)
Context7: MCP DOCS TOOL (aktif untuk library/framework queries)
JSON contract: {ok, content, meta} — wajib tunggu parse `ok` sebelum proses selesai
[WORKFLOW_AGENT]: python-internal — OpenCode tidak inject
Fallback mode: evidence commands only (/.explore /.plan /.analyze), graphify-out wajib
Action commands: AGENT_PATH wajib valid, no fallback

🪨 WHY USE MANY TOKEN WHEN FEW DO TRICK
```
