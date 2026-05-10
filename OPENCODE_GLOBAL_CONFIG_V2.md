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

    Default behavior yang dikonfigurasi:
      - Output: caveman ultra (ultra-terse). Verbose hanya jika user request.
      - Graphify: primary source default untuk codebase understanding.
      - Context7: MCP tool untuk library/framework docs terkini.

    Prompt natural tetap valid. Skill dipakai saat cocok, bukan wajib untuk semua task.

---

## STEP 0 — Install & Configure Prerequisites

Tiga komponen dikonfigurasi sebelum setup utama. Eksekusi berurutan. Stop di komponen pertama yang gagal fatal.

---

### 0A — Caveman (Plugin Install)

Caveman adalah token-compression plugin untuk 30+ AI agents termasuk OpenCode.
Source: https://github.com/JuliusBrussee/caveman
Modes: `lite` | `full` | `ultra` | `wenyan`

**Install untuk OpenCode:**

```bash
npx skills add JuliusBrussee/caveman -a opencode
```

Setelah install, verifikasi dengan menjalankan `/caveman ultra` di session berikutnya.

Jika install gagal → output warning dan lanjut (bukan fatal):

```text
[PREREQ 0A] Caveman → FAILED to install.
Action: install manually dari https://github.com/JuliusBrussee/caveman
Status: lanjut tanpa plugin, output style dikonfigurasi via AGENTS.md saja.
```

Jika berhasil:

```text
[PREREQ 0A] Caveman → installed. Aktifkan ultra mode: /caveman ultra
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
0A Caveman plugin : <installed — /caveman ultra | FAILED — install manually>
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
# OpenCode — Personal Global Config V2

# Skills: ~/.config/opencode/skills/

# Commands: ~/.config/opencode/commands/

# Memory: ~/.config/opencode/memory/

# Mode: standalone OpenCode, flexible workflow, graphify-assisted when useful

## Core Behavior

- Concise. Direct. No over-explanation.
- Single user. Optimize for workflow only.
- Clarify only when ambiguity affects safety, architecture, or irreversible work.
- Do not expand scope silently.
- Default output: ringkas, faktual, terstruktur.
- Sertakan confidence + uncertainties untuk plan/analysis formal atau saat risk tinggi.
- Boleh edit file saat user jelas meminta perubahan. Untuk aksi sensitif, wajib izin eksplisit.

## Output Style — Caveman Ultra (Default)

Powered by caveman plugin (github.com/JuliusBrussee/caveman). Mode: ultra.

Aktifkan di awal session jika belum auto-active:

```
/caveman ultra
```

Switch mode jika perlu:

- `/caveman lite` — professional tapi concise
- `/caveman full` — default caveman
- `/caveman ultra` — maximum compression (~65–75% token reduction)

Saat ultra mode aktif: single fragment per item, drop filler, code as-is, error 1 line.
Confidence block + uncertainties: hanya jika plan/analysis formal atau user eksplisit minta.

Jika user minta verbose/detail: switch ke `/caveman lite`, lalu balik `/caveman ultra` setelah selesai.

## Startup Protocol

Setiap session untuk code task:

1. Aktifkan caveman ultra jika belum auto-active: `/caveman ultra`.
2. **WAJIB cek `graphify-out/` di project root sebelum eksplorasi apa pun.**
   - Jika ada → baca `GRAPH_REPORT.md` dan/atau `graph.json` sebagai primary evidence. Supplement dengan direct file read hanya jika graph data tidak cukup spesifik.
   - Jika tidak ada → jalankan Graphify Missing Protocol untuk task eksplorasi/analisis; untuk task sederhana lanjut file/search langsung.
3. Gunakan Context7 MCP saat butuh dokumentasi library/framework terkini sebelum menjawab pertanyaan API.
4. Baca `~/.config/opencode/memory/PERSONAL_MEMORY.md` jika relevan dan tidak kosong.
5. Generate `[SESSION_ID]` hanya saat command workflow formal pertama dipakai: `<project>-<YYYYMMDD_HHMMss>`.

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
````

Jangan pakai pesan invalid untuk prompt natural tanpa slash.

## NL Map

- cek logic → `/.analyze`
- gimana flow → `/.explore`
- tambah fitur → `/.plan`
- implement → `/.execute -y`
- rapikan → `/.refactor`
- catat → `/.memory`
- docs library / versi terbaru → Context7 MCP
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

## Skill Command Enforcement

**WAJIB invoke agent-workflow untuk setiap skill command (`/.explore`, `/.plan`, `/.analyze`, `/.execute -y`, `/.verify`, `/.refactor`).**
Detection flow:

1. Detect apakah user prompt adalah skill command → cek prefix `/.` + match command registry.
2. Jika match skill command:
   - **TIDAK BOLEH** langsung jalankan logic lokal (search/read/edit).
   - **WAJIB** invoke agent-workflow via `AGENT_PATH` dengan command mapping.
   - Jalankan multi-layer check (L1-L5) sebelum invoke.
   - Parse response JSON, extract `content`, tampilkan ke user.
3. Jika bukan skill command (prompt natural tanpa `/.`):
   - Boleh pilih antara invoke agent-workflow atau langsung lokal sesuai efisiensi.
     Command mapping:
     | User Command | Agent `-c` arg |
     | --------------- | -------------- |
     | `/.explore` | `explore` |
     | `/.plan` | `plan` |
     | `/.analyze` | `analyze` |
     | `/.execute -y` | `execute` |
     | `/.verify` | `verify` |
     | `/.refactor` | (map to `plan` + `execute` sequence) |
     Error bila user pakai skill command tapi agent-workflow unavailable (L1-L5 gagal) → inform user, STOP.
     Natural prompt tanpa `/.` → optional invoke agent-workflow (agent judgment).

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

Dari Python subprocess (jika perlu invoke programatik):

```python
import os, subprocess

script = os.environ.get("AGENT_PATH")
args = ["python", script, "-c", command, "-p", prompt, "-s", session, "-w", work_dir, "--pretty"]
result = subprocess.run(args, capture_output=True, text=True)
```

### Command Mapping

| Workflow Command | `-c` arg  | Response Type |
| ---------------- | --------- | ------------- |
| `/.explore`      | `explore` | evidence      |
| `/.plan`         | `plan`    | evidence      |
| `/.analyze`      | `analyze` | evidence      |
| `/.execute -y`   | `execute` | action        |
| `/.verify`       | `verify`  | action        |

### Contoh Invocation

Model otomatis dibaca dari `config/opencode.json` per-route. Tidak perlu pass `-m` kecuali ingin override ad-hoc.

```powershell
python $env:AGENT_PATH -c explore -p "cari entry point auth" -s "finance-auth" -w "E:\Work\project\target-app" --pretty
python $env:AGENT_PATH -c analyze -p "cek logic auth" -s "finance-auth" --pretty
python $env:AGENT_PATH -c plan -p "buat fitur payment" -s "finance" --pretty
```

Override model (deviasi dari `config/opencode.json`):

```powershell
python $env:AGENT_PATH -c plan -p "buat fitur payment" -s "finance" -m "anthropic/claude-sonnet-4-5" --pretty
```

### Response Format

Contract JSON yang dikembalikan agent-workflow:

| Field                 | Type           | Value                                                    |
| --------------------- | -------------- | -------------------------------------------------------- |
| `status`              | string         | `success` \| `error`                                     |
| `content`             | string         | Response content                                         |
| `role`                | string         | Role yang dieksekusi                                     |
| `model`               | string \| null | Model yang dipakai                                       |
| `session_id`          | string         | Main session ID                                          |
| `opencode_session_id` | string \| null | OpenCode session ID — simpan dan pass ke call berikutnya |
| `confidence`          | string         | `low` \| `medium` \| `high`                              |

**Evidence commands** (`explore`, `plan`, `analyze`) menginstruksikan workflow agent untuk mengumpulkan evidence tanpa reasoning. `content` berformat:

Untuk `explore`:

```text
[EVIDENCE]
confidence: low | medium | high

entry_points:
- <list>

related_modules:
- <list>

ownership_hints:
- <list>

uncertainties:
- <list>
```

Untuk `plan` / `analyze`:

```text
[EVIDENCE]
confidence: low | medium | high

findings:
- <list>

implications:
- <list>

uncertainties:
- <list>
```

**Action commands** (`execute`, `verify`) — `content` free-form sesuai hasil eksekusi/verifikasi.

### Multi-Layer Check

Jalankan semua layer secara berurutan sebelum invoke. Stop di layer pertama yang gagal.

**Layer 1 — Env variable exists**

```powershell
if (-not $env:AGENT_PATH) { ... }
```

```python
script = os.environ.get("AGENT_PATH")
if not script:
    ...
```

Gagal → output:

```text
[CHECK FAILED — L1: ENV NOT SET]
AGENT_PATH belum di-set.
Ikuti setup di README project agent-workflow, lalu restart terminal / OpenCode.
STOP.
```

**Layer 2 — Path exists on disk**

```powershell
if (-not (Test-Path $env:AGENT_PATH)) { ... }
```

```python
if not os.path.isfile(script):
    ...
```

Gagal → output:

```text
[CHECK FAILED — L2: FILE NOT FOUND]
AGENT_PATH disetel ke: <nilai>
File tidak ditemukan di path tersebut.
Periksa path atau clone ulang project agent-workflow.
STOP.
```

**Layer 3 — File adalah Python script**

```powershell
if (-not $env:AGENT_PATH.EndsWith(".py")) { ... }
```

```python
if not script.endswith(".py"):
    ...
```

Gagal → output:

```text
[CHECK FAILED — L3: INVALID FILE TYPE]
AGENT_PATH harus menunjuk ke file .py.
Nilai saat ini: <nilai>
STOP.
```

**Layer 4 — Python runtime tersedia**

```powershell
python --version 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { ... }
```

```python
result = subprocess.run(["python", "--version"], capture_output=True)
if result.returncode != 0:
    ...
```

Gagal → output:

```text
[CHECK FAILED — L4: PYTHON NOT FOUND]
`python` tidak tersedia di PATH.
Pastikan Python 3.10+ terinstall dan tersedia di PATH.
STOP.
```

**Layer 5 — Script callable (smoke test)**

```powershell
python $env:AGENT_PATH --help 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { ... }
```

```python
result = subprocess.run(["python", script, "--help"], capture_output=True)
if result.returncode != 0:
    ...
```

Gagal → output:

```text
[CHECK FAILED — L5: SCRIPT NOT CALLABLE]
python <path> --help gagal.
Script mungkin corrupt atau dependensi hilang.
Coba: python <path> --help secara manual untuk detail error.
STOP.
```

Semua layer pass → lanjut invocation.

### Rules

- Jalankan semua 5 layer check sebelum setiap invocation pertama dalam session. Untuk invocation berikutnya dalam session yang sama, cukup Layer 1–2.
- Jangan hardcode path script. Selalu baca dari `$env:AGENT_PATH` / `os.environ.get("AGENT_PATH")`.
- Jangan modify env variable dari dalam skill atau command.
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

## Graphify Rules

`graphify-out/` adalah default primary source untuk codebase understanding. **WAJIB cek lebih dulu sebelum setiap eksplorasi.**

### Default Behavior

- **Setiap task eksplorasi, analisis, atau planning → cek `graphify-out/` pertama.** Tidak boleh skip.
- Baca `graphify-out/GRAPH_REPORT.md` untuk summary; `graphify-out/graph.json` untuk detail node/edge.
- Supplement dengan direct file read hanya jika graph data tidak cukup spesifik.
- Jika tidak ada → jalankan Graphify Missing Protocol untuk task eksplorasi/analisis; fallback file/search untuk task sederhana.
- **JANGAN pernah asumsikan `graphify-out/` tidak ada tanpa verifikasi langsung (read directory atau glob).**

### Official Commands

- `graphify update` — build/refresh graph. Wajib permission gate sebelum run.
- NEVER run: `graphify init`, `graphify build`, `graphify watch`.
- Jangan auto-run `graphify update` kecuali user meminta atau task butuh fresh graph.

### Error Handling

- `too large for HTML viz` / `Graph has too many nodes` → IGNORE viz error, tetap baca JSON data.
- Error lain → retry once. Masih gagal → inform 1 line, lanjut tanpa graph.

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

## Context7

MCP tool untuk dokumentasi library/framework terkini. Default: gunakan sebelum menjawab pertanyaan API/method jika versi mungkin berbeda dari training knowledge.

### When to Use

- User tanya API, method, config, atau signature library spesifik.
- Perlu verifikasi penggunaan library yang benar — terutama library yang sering update.
- Contoh: React hooks, Next.js routing, FastAPI dependencies, Laravel Eloquent, dll.

### MCP Tools

- `resolve-library-id` — resolve nama library ke Context7 library ID.
- `get-library-docs` — ambil docs untuk library ID + topic spesifik.

### Usage Pattern

```text
1. resolve-library-id: "<library-name>"
2. get-library-docs: library_id=<id>, topic="<topic>", tokens=<budget>
```

### Rules

- Gunakan Context7 SEBELUM menjawab jika tidak yakin apakah API/method sudah berubah.
- Jangan hallucinate method/signature — cek Context7 dulu untuk library yang aktif berkembang.
- Context7 unavailable (MCP off / error) → inform 1 line, lanjut dari training knowledge.
- Jangan block task untuk Context7 — always fallback ke knowledge jika MCP unavailable.

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
- Output verbose/bertele-tele secara default — caveman ultra selalu aktif kecuali user minta detail.
- Jawab pertanyaan API library spesifik dengan hallucinated signature tanpa cek Context7 terlebih dahulu.
- Skip/lewati cek `graphify-out/` sebelum eksplorasi codebase.

````

---

## STEP 3 — Buat skill files

Untuk setiap skill file: overwrite isi file. Skill adalah template, bukan data user.

---

### FILE: `~/.config/opencode/skills/explore.md`

```markdown
# Skill: explore
description: Agent-workflow powered exploration untuk arsitektur kompleks

Skill ini optional. Gunakan saat user memakai `/.explore` atau intent eksplorasi besar/flow kompleks. Untuk prompt natural sederhana, boleh pakai search/read langsung.

## Trigger

`/.explore <hint>`

## Execution

STEP 1 — Multi-layer check AGENT_PATH:

Jalankan 5-layer check per protocol global config sebelum invoke. Jika gagal, output error sesuai layer dan STOP.

STEP 2 — Tentukan session:

- Jika `[SESSION_ID]` sudah ada → reuse.
- Jika belum → generate `<project>-<YYYYMMDD_HHMMss>`.

STEP 3 — Invoke agent-workflow:

```powershell
python $env:AGENT_PATH -c explore -p "<hint>" -s "<session_id>" -w "<workspace_root>" --pretty
```

STEP 4 — Parse response JSON:

Extract field:
- `status`: success | error
- `content`: evidence block
- `confidence`: low | medium | high
- `opencode_session_id`: simpan untuk call berikutnya

STEP 5 — Output evidence:

Tampilkan `content` langsung (sudah format evidence block). Tambahkan:

```text
confidence: <from JSON>
```

End with: `Lanjut plan, atau cukup informasinya?`
````

---

### FILE: `~/.config/opencode/skills/plan.md`

````markdown
# Skill: plan

description: Agent-workflow powered planning dengan confidence model dan decision gate

Skill ini optional. Gunakan saat user meminta plan, task besar, banyak file, arsitektur, atau risk tinggi. Untuk perubahan kecil, boleh langsung eksekusi jika user jelas meminta.

## Trigger

`/.plan <task>`

## Execution

STEP 1 — Multi-layer check AGENT_PATH:

Jalankan 5-layer check per protocol global config sebelum invoke. Jika gagal, output error sesuai layer dan STOP.

STEP 2 — Tentukan session:

- Jika `[SESSION_ID]` sudah ada → reuse.
- Jika belum → generate `<project>-<YYYYMMDD_HHMMss>`.

STEP 3 — Invoke agent-workflow:

```powershell
python $env:AGENT_PATH -c plan -p "<task>" -s "<session_id>" -w "<workspace_root>" --pretty
```
````

STEP 4 — Parse response JSON:

Extract field:

- `status`: success | error
- `content`: evidence block (findings + implications + uncertainties)
- `confidence`: low | medium | high
- `opencode_session_id`: simpan untuk call berikutnya

STEP 5 — Syntesis plan dari evidence:

Bangun plan block dengan format:

```text
[PLAN]
task:            <restatement>
session:         <session_id>
evidence_source: agent-workflow

assumptions:
- <dari evidence findings>

open_questions:
- <dari evidence uncertainties>

steps:
1. <concrete step>
2. <concrete step>

files_affected:
- <list>

risks:
- <dari evidence implications>

confidence: <dari JSON>

uncertainties:
- <dari evidence>

decision: proceed | clarify | re-explore
```

STEP 6 — Untuk `/.plan`, stop dan tunggu approval user. Untuk prompt natural, boleh lanjut eksekusi jika user sudah meminta perubahan dan risk rendah.

End with: `Setuju? Jalankan /.execute -y`

````

---

### FILE: `~/.config/opencode/skills/execute.md`

```markdown
# Skill: execute
description: Agent-workflow powered execution dengan explicit approval gate

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
````

STOP.

Dengan `-y` → proceed. Dengan prompt natural eksplisit → proceed jika low-risk dan scope jelas.

## Execution

STEP 1 — Multi-layer check AGENT_PATH:

Jalankan 5-layer check per protocol global config sebelum invoke. Jika gagal, output error sesuai layer dan STOP.

STEP 2 — Tentukan session:

- Jika `[SESSION_ID]` sudah ada → reuse.
- Jika belum → generate `<project>-<YYYYMMDD_HHMMss>`.

STEP 3 — Invoke agent-workflow:

```powershell
python $env:AGENT_PATH -c execute -p "lakukan perubahan sesuai plan yang sudah disetujui" -s "<session_id>" -w "<workspace_root>" --pretty
```

STEP 4 — Parse response JSON:

Extract field:

- `status`: success | error
- `content`: free-form execution result
- `opencode_session_id`: simpan untuk call berikutnya

STEP 5 — Output:

```text
[EXECUTION RESULT]
<content dari agent-workflow>

status: <dari JSON status>
```

STEP 6 — Auto-trigger verify yang relevan setelah execution. Tidak perlu command literal `/.verify` jika prompt natural.

````

---

### FILE: `~/.config/opencode/skills/verify.md`

```markdown
# Skill: verify
description: Agent-workflow powered verification after execute/refactor

Skill ini optional. Gunakan setelah perubahan code, atau saat user meminta verify. Pilih check relevan, jangan jalankan check sensitif tanpa izin.

## Trigger

`/.verify`

## Execution

STEP 1 — Multi-layer check AGENT_PATH:

Jalankan 5-layer check per protocol global config sebelum invoke. Jika gagal, output error sesuai layer dan STOP.

STEP 2 — Tentukan session:

- Jika `[SESSION_ID]` sudah ada → reuse.
- Jika belum → generate `<project>-<YYYYMMDD_HHMMss>`.

STEP 3 — Invoke agent-workflow:

```powershell
python $env:AGENT_PATH -c verify -p "verifikasi perubahan terakhir dengan test/lint/build yang relevan" -s "<session_id>" -w "<workspace_root>" --pretty
```

STEP 4 — Parse response JSON:

Extract field:
- `status`: success | error
- `content`: free-form verification result
- `opencode_session_id`: simpan untuk call berikutnya

STEP 5 — Output:

```text
[VERIFY RESULT]
<content dari agent-workflow>

status: <dari JSON status>
```
````

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

````markdown
# Skill: analyze

description: Agent-workflow powered analysis, zero code changes, structured findings

Skill ini optional. Gunakan saat user meminta analisis/review/diagnosis tanpa perubahan, atau saat risk tinggi.

## Trigger

`/.analyze <topic>`

## Rules

- Zero code changes.
- Zero file modifications.
- Findings first.
- Include confidence + uncertainties.

## Execution

STEP 1 — Multi-layer check AGENT_PATH:

Jalankan 5-layer check per protocol global config sebelum invoke. Jika gagal, output error sesuai layer dan STOP.

STEP 2 — Tentukan session:

- Jika `[SESSION_ID]` sudah ada → reuse.
- Jika belum → generate `<project>-<YYYYMMDD_HHMMss>`.

STEP 3 — Invoke agent-workflow:

```powershell
python $env:AGENT_PATH -c analyze -p "<topic>" -s "<session_id>" -w "<workspace_root>" --pretty
```

STEP 4 — Parse response JSON:

Extract field:

- `status`: success | error
- `content`: evidence block (findings + implications + uncertainties)
- `confidence`: low | medium | high
- `opencode_session_id`: simpan untuk call berikutnya

STEP 5 — Output analysis:

```text
[ANALYSIS]
topic: <topic>
source: agent-workflow

findings:
<dari content>

implications:
<dari content>

confidence: <dari JSON>

uncertainties:
<dari content>
```
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
````

STEP 3 — Wait for user confirmation.

STEP 4 — Only write after `yes` or explicit edited content.

````

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

Output default: caveman ultra — single fragment per item, no filler.
Graphify: default primary source untuk codebase understanding.
Context7: default MCP untuk library/framework docs terkini.

Natural prompts are valid. Use commands only when you want structured workflow.

/.explore <hint>
→ explore codebase, graphify-first by default

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
Verbose mode: balas "verbose" atau "detail" untuk full explanation sesi itu saja.
````

````

---

## STEP 4 — Buat custom command files

Buat direktori `~/.config/opencode/commands/` jika belum ada. Untuk setiap command file: overwrite isi file.

### FILE: `~/.config/opencode/commands/explore.md`

```markdown
---
description: Agent-workflow powered codebase exploration. Usage: /.explore <hint>
---

Read `~/.config/opencode/skills/explore.md` and follow it. Reject `/explore` only when used as slash command; natural prompts remain valid.
````

### FILE: `~/.config/opencode/commands/plan.md`

```markdown
---
description: Agent-workflow powered structured plan with confidence, uncertainties, and decision gate. Usage: /.plan <task>
---

Read `~/.config/opencode/skills/plan.md` and follow it. Reject `/plan` only when used as slash command; natural prompts remain valid.
```

### FILE: `~/.config/opencode/commands/execute.md`

```markdown
---
description: Agent-workflow powered implementation. Requires -y for formal workflow. Usage: /.execute -y
---

Read `~/.config/opencode/skills/execute.md` and follow it. Reject formal `/.execute` without `-y`; natural prompts can execute low-risk clear requests. Sensitive actions require Permission Gate.
```

### FILE: `~/.config/opencode/commands/verify.md`

```markdown
---
description: Agent-workflow powered verification with relevant checks. Usage: /.verify
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
description: Agent-workflow powered analysis with zero code changes. Usage: /.analyze <topic>
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

Last updated: 2026-05-09

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
   - Caveman Ultra: `AGENTS.md` mengandung `Caveman Ultra (Default)`.
   - Graphify: `graphify --version` tersedia atau sudah dicatat sebagai skipped.
   - Context7: `~/.config/opencode/config.json` mengandung key `"context7"`.
2. List `~/.config/opencode/`.
3. List `~/.config/opencode/skills/`.
4. List `~/.config/opencode/commands/`.
5. List `~/.config/opencode/memory/`.
6. Tampilkan ukuran byte file penting:
   - `AGENTS.md`
   - `config.json`
   - semua skill files
   - semua command files
   - memory index
7. Konfirmasi `AGENTS.md` mengandung `Command Registry V2`.
8. Konfirmasi command files mengandung frontmatter `description`.

---

## STEP 7 — Final Report

Tampilkan PERSIS:

```text
[SETUP COMPLETE — OPENCODE GLOBAL WORKFLOW V2]

Prerequisites:
  Caveman plugin → <installed — activate: /caveman ultra | FAILED — install manually>
  Graphify CLI   → <found <ver> | installed <ver> | skipped — install manually>
  Context7 MCP   → ~/.config/opencode/config.json ✓

Config:
  ~/.config/opencode/AGENTS.md      ✓
  ~/.config/opencode/config.json    ✓

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
Output default: CAVEMAN ULTRA (verbose on user request only)
Graphify: PRIMARY SOURCE (default setiap session)
Context7: MCP DOCS TOOL (aktif untuk library/framework queries)
```
