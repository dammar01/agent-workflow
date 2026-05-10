Update konfigurasi global OpenCode yang sudah ada. Ini adalah delta update. Jangan hapus konten existing. Ikuti setiap step secara berurutan. Jangan skip.

---

## PRE-CONDITION — Informasikan ke user

Sebelum mulai, output:

    [OPENCODE CONFIG UPDATE — AGENT-WORKFLOW DELTA]
    Target: ~/.config/opencode/AGENTS.md

    Perubahan yang akan diterapkan:
      1. Command Mapping → tambah kolom Response Type (evidence / action)
      2. Contoh Invocation → klarifikasi model dari config/opencode.json, bukan dari -m
      3. Response Format → tambah section baru (contract JSON + format evidence/action)

    File lain (skills/ commands/ memory/) TIDAK disentuh.

---

## STEP 1 — Backup check

Baca `~/.config/opencode/AGENTS.md`. Konfirmasi file ada sebelum lanjut.

Jika tidak ada:
```text
[UPDATE FAILED]
~/.config/opencode/AGENTS.md tidak ditemukan.
Jalankan setup awal terlebih dahulu menggunakan OPENCODE_GLOBAL_CONFIG_V2.md, lalu ulangi update ini.
STOP.
```

---

## STEP 2 — Update: Command Mapping

Cari section `### Command Mapping` di dalam `## Agent-Workflow Invocation via Env Variable`.

**Ganti seluruh tabel** dengan:

```markdown
| Workflow Command | `-c` arg  | Response Type |
| ---------------- | --------- | ------------- |
| `/.explore`      | `explore` | evidence      |
| `/.plan`         | `plan`    | evidence      |
| `/.analyze`      | `analyze` | evidence      |
| `/.execute -y`   | `execute` | action        |
| `/.verify`       | `verify`  | action        |
```

---

## STEP 3 — Update: Contoh Invocation

Cari section `### Contoh Invocation` di dalam `## Agent-Workflow Invocation via Env Variable`.

**Ganti seluruh section ini** (dari `### Contoh Invocation` sampai sebelum `### Multi-Layer Check`) dengan:

```markdown
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
```

---

## STEP 4 — Tambah: Response Format

Cari section `### Multi-Layer Check` di dalam `## Agent-Workflow Invocation via Env Variable`.

**Sisipkan section berikut TEPAT SEBELUM `### Multi-Layer Check`:**

```markdown
### Response Format

Contract JSON yang dikembalikan agent-workflow:

| Field | Type | Value |
| ----- | ---- | ----- |
| `status` | string | `success` \| `error` |
| `content` | string | Response content |
| `role` | string | Role yang dieksekusi |
| `model` | string \| null | Model yang dipakai |
| `session_id` | string | Main session ID |
| `opencode_session_id` | string \| null | OpenCode session ID — simpan dan pass ke call berikutnya |
| `confidence` | string | `low` \| `medium` \| `high` |

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
```

---

## STEP 5 — Verifikasi

1. Baca kembali `~/.config/opencode/AGENTS.md`.
2. Konfirmasi:
   - `### Command Mapping` memiliki kolom `Response Type`
   - `### Contoh Invocation` mengandung kalimat "Model otomatis dibaca dari `config/opencode.json`"
   - `### Response Format` ada di antara `### Contoh Invocation` dan `### Multi-Layer Check`

---

## STEP 6 — Final Report

Tampilkan PERSIS:

```text
[CONFIG UPDATE COMPLETE — AGENT-WORKFLOW DELTA]

Files updated:
  ~/.config/opencode/AGENTS.md ✓

Changes applied:
  Command Mapping  → Response Type column added ✓
  Contoh Invocation → model auto dari config/opencode.json ✓
  Response Format  → new section added ✓

Files NOT touched: skills/ commands/ memory/

Status: READY
```
