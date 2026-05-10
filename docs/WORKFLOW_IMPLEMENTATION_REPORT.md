# Laporan Implementasi: agent-workflow Workflow

**Project:** kiara-proxy (agent-workflow)  
**Tanggal:** 2026-05-09  
**Status Codebase:** v2 OpenCode-only (2026-05-09)

---

## Ringkasan Eksekutif

Dokumen ini merekam evolusi sistem workflow engineering pribadi. Kondisi saat ini: v2 OpenCode-only, config user via JSON, session OpenCode dipersist, output logs dibersihkan.

Tiga versi utama:

| Versi | Pendekatan                                                | File Utama                                             | Tanggal        |
| ----- | --------------------------------------------------------- | ------------------------------------------------------ | -------------- |
| v0    | Single agent, graphify-first, no proxy                    | `WORKFLOW_V0.md`                                       | Pre-2026-04-28 |
| v1    | Claude Code + Kimi via subprocess, session per project    | proxy code v0.1.0                                      | 2026-05-06     |
| v1.1  | Multi-layer env check, 1:1 session, background invocation | proxy code v0.2.0 + `CLAUDE_CODE_CONFIG_V1.1.md`       | 2026-05-07     |
| v2    | OpenCode-only, JSON config, session resume via `-s`       | `adapters/opencode_adapter.py`, `config/opencode.json` | 2026-05-09     |

---

## v2 — OpenCode-Only Proxy

### Deskripsi

V2 menghapus adapter Kimi/Claude. Semua command workflow dijalankan lewat `opencode run`.

### File Implementasi

- `config/opencode.json` — route command, default model, timeout, command binary
- `adapters/opencode_adapter.py` — subprocess wrapper OpenCode
- `core/executor.py` — prompt build + OpenCode dispatch + session capture
- `utils/parser.py` — parse `session.id=ses_...` dan cleanup log OpenCode

### Session

Run pertama:

```text
opencode run <prompt> --print-logs
```

Run berikutnya:

```text
opencode run <prompt> -s <session_id>
```

### Model

`model: null` memakai default OpenCode aktif. Override model via CLI:

```text
python main.py -c analyze -p "cek logic" -s "session" -m "provider/model_key"
```

### Status Legacy

Bagian v0 sampai v1.1 di bawah bersifat historis.

---

## v0 — Single Agent Mode (Graphify-First)

### Deskripsi

Implementasi awal. Claude Code (atau agent apapun) bekerja sendiri tanpa proxy eksternal. Eksplorasi dilakukan oleh agent itu sendiri dengan graphify sebagai sumber utama pengganti file reading langsung.

### File Implementasi

- `WORKFLOW_V0.md` — setup script yang mendeteksi agent aktif dan menginstall skill secara otomatis
- Diinstall ke direktori masing-masing agent: `~/.claude/`, `~/.codex/`, `~/.cursor/`, dll.

### Skill yang Tersedia

```
/.explore → graphify-out/ (primary) | agent direct (fallback, perlu konfirmasi)
/.plan    → graphify evidence + agent reasoning
/.execute → agent (wajib -y)
/.verify  → agent (auto-triggered setelah execute/refactor)
/.refactor → agent + auto /.verify
/.analyze → graphify-out/ (primary)
/.memory  → agent (proposal → konfirmasi user)
/.help    → command reference
```

Workflow utama: `/.explore → /.plan → /.execute -y → /.verify`

### Karakteristik

- **Agent-agnostic**: auto-detect Claude Code, Codex, Cursor, Windsurf, Gemini CLI, GitHub Copilot
- **Idempotent setup**: setup ulang aman (skill overwrite, memory dipertahankan)
- **Structured output mandatory**: setiap plan/analysis wajib mengandung `confidence` + `uncertainties`
- **Graphify state check**: jika `graphify-out/` tidak ada → stop → generate `.graphifyignore` → minta `graphify update` manual

### Kelemahan

| #   | Kelemahan                       | Detail                                                                                                |
| --- | ------------------------------- | ----------------------------------------------------------------------------------------------------- |
| 1   | Token usage file reading tinggi | Agent harus membuka file langsung saat `graphify-out/` tidak tersedia                                 |
| 2   | Execute re-reads code           | Step execute tidak memanfaatkan context dari explore, sehingga potensial membaca ulang file yang sama |
| 3   | Single agent bottleneck         | Satu agent menanggung eksplorasi dan reasoning — optimal hanya jika graphify aktif                    |

---

## v1 — Proxy Mode Initial (Claude Code + Kimi)

### Deskripsi

Pengenalan proxy Python yang merutekan perintah ke dua agent berbeda. Kimi menangani eksplorasi (membaca codebase), Claude menangani reasoning dan eksekusi. Token usage Claude berkurang drastis karena pembacaan file dipindahkan ke Kimi.

### File Implementasi

- `main.py` — entry point CLI
- `core/`: router, executor, prompt_builder, contract, session_manager
- `adapters/`: kimi_adapter, claude_adapter
- `config/`: roles, routing, settings
- `utils/`: cache, parser, logger
- Proxy code changelog: v0.1.0 (2026-05-06)

### Routing Command

```python
COMMAND_ROUTES = {
    "explore": (MODEL_KIMI,),
    "plan":    (MODEL_KIMI,),   # evidence only — reasoning tetap di Claude Code skill
    "analyze": (MODEL_KIMI,),
    "execute": (MODEL_CLAUDE,),
    "verify":  (MODEL_CLAUDE,),
}
```

### Session Management

Kimi session di-link secara otomatis per `work_dir` dengan membaca `~/.kimi/kimi.json`. Session proxy disimpan ke `storage/sessions/<session_id>.json` dengan skema:

```json
{
  "session_id": "finance-auth",
  "kimi_session_id": "a2d0c19d-...",
  "history": {
    "created_at": "...",
    "updated_at": "...",
    "runs": [{ "command": "explore", "cache_hit": false, "timestamp": "..." }]
  }
}
```

Session key di v1 terikat ke **project/fitur** (nama session ditentukan user).

### Caching

Key = `SHA-256(command + work_dir_hash + task_hash)`. Cache file tunggal: `storage/cache.json`.

### Kelemahan

| #   | Kelemahan                             | Detail                                                                                                                              |
| --- | ------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| 1   | Session staleness                     | Session terikat ke nama project/fitur — informasi lama dari sesi sebelumnya bisa masuk dan tidak relevan                            |
| 2   | Timeout handling manual               | Claude perlu "add to background" secara manual; tanpa itu Claude bisa memanggil Kimi dua kali                                       |
| 3   | Single env check                      | Pengecekan `$AI_PROXY` hanya dengan satu metode (`echo %AI_PROXY%`) — jika gagal, langsung auto-fallback ke Claude tanpa peringatan |
| 4   | Plan 2-step belum terotomasi di proxy | `plan` di proxy hanya route ke Kimi; langkah reasoning Claude masih manual di sisi Claude Code skill                                |
| 5   | Tidak ada fallback antar model        | Kimi gagal → error langsung, tidak ada retry atau model alternatif                                                                  |

---

## v1.1 — Enhanced Proxy (Current State)

### Deskripsi

Fokus utama: eliminasi kelemahan v1. Multi-layer env check, session 1:1 antara Claude dan Kimi, dan protokol invokasi background yang terstruktur. Versi ini juga menambahkan `WORKFLOW_V0.md` sebagai alternatif single-agent tanpa proxy.

### File Implementasi

- Proxy code v0.2.0 (2026-05-07)
- `CLAUDE_CODE_CONFIG_V1.1.md` — setup guide Claude Code (789 baris)
- `KIMI_CODE_CONFIG_V1.1.md` — setup guide Kimi standalone (427 baris)
- `UPDATE_CONFIG_PROMPT.md` — update incremental tanpa re-install penuh
- `WORKFLOW_V0.md` — fallback single-agent mode (V0.3)

### Perubahan dari v1

#### 1. Multi-Layer Env Check

Dari satu `echo %AI_PROXY%` menjadi 4-layer check (stop di layer pertama yang valid):

```
Layer 1: CMD/Batch     → echo %AI_PROXY%
Layer 2: PowerShell    → $env:AI_PROXY
Layer 3: Python        → python -c "import os; print(os.environ.get('AI_PROXY',''))"
Layer 4: .NET          → [Environment]::GetEnvironmentVariable('AI_PROXY','Process')
```

Jika semua kosong → warning sekali → tidak auto-fallback.

#### 2. Session 1:1

Session Claude Code = Session Kimi. Setiap session Claude Code baru menghasilkan session Kimi baru (`kimi_session_id` unik per session). Ini menghilangkan risiko informasi stale dari session project sebelumnya.

#### 3. Proxy Invocation Protocol

```
1. Output ke user: "Sedang menunggu response Kimi..."
2. Jalankan proxy via Bash dengan run_in_background: true
3. WAJIB tunggu notifikasi completion → JANGAN lanjut sebelum notifikasi
4. Parse response → lanjut ke step berikutnya
```

Mengeliminasi masalah double-call Kimi karena timeout.

#### 4. Enhanced Plan Flow

`/.plan` kini terdiri dari dua langkah eksplisit:

1. Kimi dikumpulkan evidence-nya via proxy (`-c plan`)
2. Claude menggunakan evidence tersebut untuk reasoning dan menyusun plan terstruktur

#### 5. Local Mode (`/.local`)

Toggle no-proxy mode. Saat aktif, eksplorasi menggunakan **Graphify-Mirrored Execution Protocol** (graphify + Claude) — output format identik dengan proxy response.

### Kondisi Skill di CLAUDE.md

```
/.explore  → Kimi via proxy | fallback: graphify + Claude (perlu konfirmasi)
/.plan     → Kimi evidence via proxy → Claude reasoning
/.analyze  → Kimi via proxy | --local: Claude only
/.execute  → Claude (selalu)
/.memory   → Claude proposal → konfirmasi user
/.help     → built-in
/.local    → toggle no-proxy mode
/.verify   → ORPHANED (dihapus dari V1.1)
/.refactor → ORPHANED (dihapus dari V1.1)
```

### Timeout Default

Naik dari 120s (v0.1.0) ke 300s (v0.2.0) — menyesuaikan estimasi response Kimi realistis.

### Kelemahan

| #   | Kelemahan                      | Detail                                                                                                                                                                               |
| --- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 1   | Locked ke Claude + Kimi        | Ketika usage Kimi habis, tidak ada model alternatif yang bisa handle exploration                                                                                                     |
| 2   | Plan gap di proxy              | `plan` di `routing.py` masih route ke Kimi saja; 2-step flow dikelola di sisi Claude Code skill, bukan di proxy — membuat proxy API tidak konsisten dengan workflow yang ditampilkan |
| 3   | README vs routing.py mismatch  | README menyebut `analyze` → Claude, tapi `routing.py` dan `BUSINESS_LOGIC_REPORT.md` menunjukkan `analyze` → Kimi                                                                    |
| 4   | Test tidak isolated            | `test_scenario.py` menulis ke `storage/` — test mencemari data runtime                                                                                                               |
| 5   | ClaudeAdapter placeholder mode | `execute`/`verify` tidak benar-benar memanggil Claude jika `CLAUDE_COMMAND` kosong — tidak ada error, hanya placeholder                                                              |

---

## Kondisi Codebase Saat Ini

### Arsitektur Sistem

```
┌────────────────────────────────────────────────────┐
│  CLAUDE CODE (skill layer)                         │
│  ~/.claude/skills/: explore, plan, analyze, ...    │
│  ~/.claude/CLAUDE.md: config + rules               │
└──────────────────────┬─────────────────────────────┘
                       │ python $AI_PROXY -c <cmd> -p <prompt>
                       ▼
┌────────────────────────────────────────────────────┐
│  PROXY (kiara-proxy / agent-workflow)                    │
│  main.py → SessionManager → Cache → Executor      │
│            → Router → KimiAdapter / ClaudeAdapter  │
└──────────────┬──────────────────────┬──────────────┘
               │ subprocess           │ subprocess
               ▼                      ▼
         ┌──────────┐          ┌──────────────┐
         │ kimi CLI │          │ claude CLI   │
         └──────────┘          └──────────────┘
```

### Statistik Codebase

- **File Python**: 14 (stdlib only, no third-party)
- **File skill/config (markdown)**: 5 (`CLAUDE_CODE_CONFIG_V1.1.md`, `KIMI_CODE_CONFIG_V1.1.md`, `WORKFLOW_V0.md`, `UPDATE_CONFIG_PROMPT.md`, `README.md`)
- **Storage sessions aktif**: 12 session JSON
- **Graphify graph**: 71 nodes, 122 edges, 11 komunitas
- **Test coverage**: end-to-end scenario test (FakeKimiAdapter + FakeClaudeAdapter)

### Node Kritikal (dari Graphify)

| Node             | Edges | Peran                                              |
| ---------------- | ----- | -------------------------------------------------- |
| `Executor`       | 12    | Dispatcher utama — semua routing melewati sini     |
| `run()`          | 8     | Entry point, orkestrasi cache + session + executor |
| `SimpleCache`    | 8     | Shared cache instance                              |
| `SessionManager` | 7     | Lifecycle session                                  |

`Executor` memiliki betweenness centrality 0.325 — titik kritis, perubahan di sini berdampak luas.

### Gap yang Diketahui

| #   | Gap                                            | Lokasi                                  | Status                         |
| --- | ---------------------------------------------- | --------------------------------------- | ------------------------------ |
| 1   | `plan` proxy hanya route ke Kimi, tidak 2-step | `core/executor.py`, `config/routing.py` | Known, by design di sisi skill |
| 3   | Tidak ada fallback model jika Kimi gagal/habis | `core/executor.py`                      | Open issue                     |

---

## Perbandingan Antar Versi

| Aspek               | v0                                    | v1                             | v1.1                                      |
| ------------------- | ------------------------------------- | ------------------------------ | ----------------------------------------- |
| Agent               | Single (Claude/Codex/dll)             | Claude Code + Kimi             | Claude Code + Kimi                        |
| Eksplorasi          | graphify-first, agent direct fallback | Kimi via subprocess            | Kimi via subprocess + background protocol |
| Token Claude        | Tinggi (file reading langsung)        | Rendah (Kimi yang baca)        | Rendah + lebih efisien                    |
| Session             | Tidak ada (per invocation)            | Per project/fitur (risk stale) | 1:1 dengan Claude session                 |
| Env check           | N/A (tidak ada proxy)                 | Single `echo %AI_PROXY%`       | 4-layer check                             |
| Timeout handling    | N/A                                   | Manual "add to background"     | Protokol `run_in_background` + wait gate  |
| Fallback Kimi gagal | N/A                                   | Error langsung                 | Error langsung (belum ada alternatif)     |
| Skill verify        | Aktif (auto-triggered)                | Aktif                          | Orphaned (dihapus V1.1)                   |
| Skill refactor      | Aktif                                 | Aktif                          | Orphaned (dihapus V1.1)                   |
| Multi-agent support | Ya (deteksi otomatis)                 | Tidak (Claude Code only)       | Tidak (Claude Code only)                  |

---

## Catatan Implementasi

### Kenapa /.verify dan /.refactor Dihapus di v1.1?

Tidak ada dokumentasi eksplisit di codebase. Berdasarkan konfigurasi `CLAUDE.md` dan `CLAUDE_CODE_CONFIG_V1.1.md`, kedua skill ini ditandai sebagai "orphaned" di V1.1 tanpa penjelasan. Kemungkinan: scope dipersempit untuk mengurangi kompleksitas workflow, atau fungsi keduanya dianggap sudah terwakili oleh Claude Code built-in behavior.

### Plan 2-Step: Proxy vs Skill Level

Implementasi plan 2-step (Kimi evidence → Claude reasoning) tidak diimplementasikan di level proxy (`main.py`). Proxy hanya route `plan` ke Kimi. Langkah reasoning Claude dilakukan di level Claude Code skill (`plan.md`) yang memanggil proxy untuk evidence, lalu Claude sendiri yang melakukan reasoning. Ini desain yang valid tapi menciptakan gap antara proxy API (`-c plan` → hanya Kimi) dan workflow yang diharapkan user.

### Session Linking Mekanisme

Kimi session ID tidak di-set oleh proxy — proxy membacanya dari `~/.kimi/kimi.json` yang dikelola oleh Kimi CLI sendiri. Proxy hanya mencatat `kimi_session_id` yang sudah ada per `work_dir`. Ini berarti jika Kimi CLI tidak mendukung `~/.kimi/kimi.json`, session linking tidak akan berfungsi.

---
