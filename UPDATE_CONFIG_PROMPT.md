Update konfigurasi global OpenCode yang sudah ada. Ini adalah delta update. Jangan hapus konten existing. Ikuti setiap step secara berurutan. Jangan skip.

---

## PRE-CONDITION — Informasikan ke user

Sebelum mulai, output:

    [OPENCODE CONFIG UPDATE — V2 DELTA]
    Target: ~/.config/opencode/AGENTS.md + config.json

    Perubahan yang akan diterapkan:
      0. Install prerequisites: Caveman plugin + Graphify CLI + Context7 MCP
      1. Output style → Caveman Ultra sebagai default
      2. Graphify → primary source default (bukan opsional)
      3. Context7 → MCP tool untuk library/framework docs
      4. Startup Protocol → diupdate untuk graphify + context7
      5. Global Forbidden → tambah 2 rule baru

    File lain (skills/, commands/, memory/) TIDAK disentuh.

---

## STEP 0 — Install & Configure Prerequisites

### 0A — Caveman (Plugin Install)

Caveman adalah token-compression plugin untuk 30+ AI agents termasuk OpenCode.
Source: https://github.com/JuliusBrussee/caveman
Modes: `lite` | `full` | `ultra` | `wenyan`

**Install via one-liner (auto-detects agent):**

Windows PowerShell:
```powershell
irm https://raw.githubusercontent.com/JuliusBrussee/caveman/main/install.ps1 | iex
```

macOS/Linux/WSL:
```bash
curl -fsSL https://raw.githubusercontent.com/JuliusBrussee/caveman/main/install.sh | bash
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
Status: skipped, lanjut update tanpa graphify.
```

Jika install berhasil → verifikasi `graphify --version` dan output:
```text
[PREREQ 0B] Graphify → installed: <version>
```

### 0C — Context7 MCP

Buat atau update `~/.config/opencode/config.json`.

**Cek file existing dan backup:**
- Jika ada → backup dulu, lalu baca isi dan lanjut ke merge:
  ```bash
  cp ~/.config/opencode/config.json ~/.config/opencode/config.json.bak
  ```
- Jika tidak ada → buat file baru dengan content minimal (skip backup).

**Tambahkan atau merge entry Context7:**

Jika key `"mcp"` sudah ada → tambahkan `"context7"` ke dalam object `mcp` existing tanpa overwrite key lain.

Jika key `"mcp"` belum ada → tambahkan object `"mcp"` baru ke root config.

Entry yang ditambahkan:
```json
"context7": {
  "type": "local",
  "command": "npx",
  "args": ["-y", "@upstash/context7-mcp@latest"]
}
```

Contoh file minimal jika sebelumnya kosong:
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

Validasi JSON setelah tulis. Jika invalid:
```text
[PREREQ 0C] Context7 → FAILED: config.json tidak valid JSON setelah edit.
Path: ~/.config/opencode/config.json
Action: perbaiki manual, lalu jalankan ulang update ini.
STOP.
```

Jika valid:
```text
[PREREQ 0C] Context7 → configured in ~/.config/opencode/config.json
```

### 0D — Prerequisites Summary

```text
[PREREQ SUMMARY]
0A Caveman plugin : <installed — /caveman ultra | FAILED — install manually>
0B Graphify CLI   : <found <ver> | installed <ver> | skipped — install manually>
0C Context7 MCP   : <configured | FAILED>
```

Lanjut ke STEP 1 hanya jika 0C tidak FAILED.

---

## STEP 1 — Backup check

Baca `~/.config/opencode/AGENTS.md`. Konfirmasi file ada sebelum lanjut.

Jika tidak ada:
```text
[UPDATE FAILED]
~/.config/opencode/AGENTS.md tidak ditemukan.
Jalankan setup awal terlebih dahulu, lalu ulangi update ini.
STOP.
```

---

## STEP 2 — Update: Output Style

Cari section `## Output Style` di AGENTS.md.

**Ganti seluruh section ini** (dari `## Output Style` sampai sebelum `## Startup Protocol`) dengan:

```markdown
## Output Style — Caveman Ultra (Default)

Powered by caveman plugin (github.com/JuliusBrussee/caveman). Mode: ultra.

Aktifkan di awal session jika belum auto-active:
/caveman ultra

Switch mode jika perlu:
- /caveman lite  — professional tapi concise
- /caveman full  — default caveman
- /caveman ultra — maximum compression (~65–75% token reduction)

Saat ultra mode aktif: single fragment per item, drop filler, code as-is, error 1 line.
Confidence block + uncertainties: hanya jika plan/analysis formal atau user eksplisit minta.

Jika user minta verbose/detail: switch ke /caveman lite, lalu balik /caveman ultra setelah selesai.
```

---

## STEP 3 — Update: Startup Protocol

Cari section `## Startup Protocol` di AGENTS.md.

**Ganti isi numbered list** (poin 1–7) dengan:

```markdown
1. Aktifkan caveman ultra jika belum auto-active: /caveman ultra.
2. Cek `graphify-out/` di project root — default primary source untuk codebase understanding.
3. Jika ada → pakai sebagai primary evidence. Supplement dengan direct file read jika perlu.
4. Jika tidak ada → jalankan Graphify Missing Protocol untuk task eksplorasi/analisis; untuk task sederhana lanjut file/search langsung.
5. Gunakan Context7 MCP saat butuh dokumentasi library/framework terkini sebelum menjawab pertanyaan API.
6. Baca `~/.config/opencode/memory/PERSONAL_MEMORY.md` jika relevan dan tidak kosong.
7. Generate `[SESSION_ID]` hanya saat command workflow formal pertama dipakai: `<project>-<YYYYMMDD_HHMMss>`.
```

---

## STEP 4 — Update: Graphify Rules

Cari section `## Graphify Rules` di AGENTS.md.

**Ganti seluruh section ini** (dari `## Graphify Rules` sampai sebelum `## Graphify Missing Protocol`) dengan:

```markdown
## Graphify Rules

`graphify-out/` adalah default primary source untuk codebase understanding. Selalu cek lebih dulu.

### Default Behavior

- Setiap task eksplorasi, analisis, atau planning → cek `graphify-out/` pertama.
- Baca `graphify-out/GRAPH_REPORT.md` untuk summary; `graphify-out/graph.json` untuk detail node/edge.
- Supplement dengan direct file read hanya jika graph data tidak cukup spesifik.
- Jika tidak ada → jalankan Graphify Missing Protocol untuk task eksplorasi/analisis; fallback file/search untuk task sederhana.

### Official Commands

- `graphify update` — build/refresh graph. Wajib permission gate sebelum run.
- NEVER run: `graphify init`, `graphify build`, `graphify watch`.
- Jangan auto-run `graphify update` kecuali user meminta atau task butuh fresh graph.

### Error Handling

- `too large for HTML viz` / `Graph has too many nodes` → IGNORE viz error, tetap baca JSON data.
- Error lain → retry once. Masih gagal → inform 1 line, lanjut tanpa graph.
```

---

## STEP 5 — Tambah: Context7 section

Cari section `## Execution Safety` di AGENTS.md.

**Sisipkan section berikut TEPAT SEBELUM `## Execution Safety`:**

```markdown
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
```

---

## STEP 6 — Update: NL Map

Cari section `## NL Map` di AGENTS.md.

**Tambahkan 1 baris baru** sebelum `- help → \`/.help\``:

```markdown
- docs library / versi terbaru → Context7 MCP
```

---

## STEP 7 — Update: Global Forbidden

Cari section `## Global Forbidden` di AGENTS.md.

**Tambahkan 2 baris baru** di akhir list:

```markdown
- Output verbose/bertele-tele secara default — caveman ultra selalu aktif kecuali user minta detail.
- Jawab pertanyaan API library spesifik dengan hallucinated signature tanpa cek Context7 terlebih dahulu.
```

---

## STEP 8 — Verifikasi

1. Konfirmasi prerequisites:
   - `~/.config/opencode/config.json` mengandung key `"context7"` → valid JSON.
   - `graphify --version` tersedia atau sudah dicatat sebagai skipped.
2. Baca kembali `~/.config/opencode/AGENTS.md`.
3. Konfirmasi section berikut ada dan sudah diupdate:
   - `## Output Style — Caveman Ultra (Default)`
   - Startup Protocol poin 1 menyebut `/caveman ultra`
   - Startup Protocol poin 2 menyebut `graphify-out/` sebagai "default primary source"
   - Startup Protocol poin 5 menyebut Context7
   - `## Graphify Rules` memiliki subsection `### Default Behavior`, `### Official Commands`, `### Error Handling`
   - `## Context7` ada sebelum `## Execution Safety`
   - `## NL Map` memiliki entry Context7
   - `## Global Forbidden` memiliki 2 rule baru

---

## STEP 9 — Final Report

Tampilkan PERSIS:

```text
[CONFIG UPDATE COMPLETE — OPENCODE GLOBAL WORKFLOW V2]

Prerequisites installed:
  Caveman plugin : <installed — /caveman ultra | FAILED — install manually>
  Graphify CLI   : <found <ver> | installed <ver> | skipped — install manually>
  Context7 MCP   : ~/.config/opencode/config.json ✓

Files updated:
  ~/.config/opencode/AGENTS.md      ✓
  ~/.config/opencode/config.json    ✓

Changes applied to AGENTS.md:
  Output Style    → Caveman Ultra default ✓
  Startup Protocol → graphify primary + Context7 step ✓
  Graphify Rules  → promoted to default primary source ✓
  Context7        → new section added ✓
  NL Map          → Context7 entry added ✓
  Global Forbidden → 2 new rules added ✓

Files NOT touched: skills/ commands/ memory/

Status: READY
Output default: CAVEMAN ULTRA (verbose on user request only)
Graphify: PRIMARY SOURCE (default setiap session)
Context7: MCP DOCS TOOL (aktif untuk library/framework queries)
```
