# Prompt Update Config — Tambah Skill /.local (No-Proxy + Graphify-Mirrored Mode)

Tambahkan skill `/.local` + custom command `/local` ke global config aktif di `~/.claude/`.

> **Perubahan ini:** Menambahkan skill `/.local` dengan graphify-mirrored execution protocol — saat aktif, Claude menggantikan Kimi dengan traversal berbasis graphify + Read/Glob/Grep, menghasilkan output format yang identik. Ditambahkan juga sebagai custom command `/local` agar muncul di command palette Claude Code.
> Config yang sudah ada tidak di-overwrite — hanya tambah file baru + patch bagian yang berubah.

---

## PROMPT (copy dari sini)

```
Tambahkan skill /.local dan custom command /local ke Claude Code global config.
Ikuti setiap step berurutan. Jangan skip.

## STEP 1 — Buat file ~/.claude/skills/local.md

Tulis file baru berikut (jika sudah ada tapi konten berbeda — overwrite):

    # Skill: local
    description: Toggle no-proxy mode. Claude mirrors Kimi traversal via graphify + Read/Glob/Grep. Affects /.explore /.plan /.analyze.

    ## Trigger
    /.local           → toggle on/off + tampilkan status
    /.local on        → aktifkan no-proxy mode
    /.local off       → nonaktifkan, kembali ke proxy mode
    /.local status    → tampilkan status tanpa toggle

    ## Session State
    Simpan sebagai [LOCAL_MODE] di context:
    - [LOCAL_MODE] = true  → no-proxy aktif, graphify-mirrored flow
    - [LOCAL_MODE] = false → default (proxy mode)
    Default: false

    ## Toggle Logic
    Saat /.local (tanpa arg):
      Jika [LOCAL_MODE] = false → set true → output [LOCAL MODE — ON]
      Jika [LOCAL_MODE] = true  → set false → output [LOCAL MODE — OFF]

    ---

    ## GRAPHIFY-MIRRORED EXECUTION PROTOCOL
    (Aktif saat [LOCAL_MODE] = true. Berlaku untuk /.explore, /.plan, /.analyze)

    Protocol ini menggantikan proxy invocation — Claude menjalankan traversal
    yang setara dengan Kimi, menggunakan graphify sebagai sumber struktur utama.

    ### Tahap 1 — Load Structure Map
    Cek graphify-out/ di work_dir:
    a. ADA → baca file JSON index di graphify-out/ (graph.json / nodes.json / index.json)
       Extract:
       - nodes   : file/fungsi/class beserta path
       - clusters: pengelompokan modul/domain
       - edges   : dependency, import, call chain
       Tandai: graphify_source = active
    b. TIDAK ADA → jalankan Glob("**/*", work_dir) untuk map struktur top-level
       Tandai: graphify_source = unavailable (direct glob)

    ### Tahap 2 — Scope Identification (mirip Kimi intent parsing)
    Dari structure map + hint/task:
    - Filter nodes relevan berdasarkan nama, cluster, keyword
    - Prioritas traversal: entry points → caller chain → dependency files
    - Buat shortlist max 10 file target

    ### Tahap 3 — Deep Dive (mirip Kimi codebase traversal)
    Untuk setiap file di shortlist:
    - Read file (batasi per section jika besar)
    - Grep untuk symbol/pattern kunci yang relevan dengan task
    - Trace dependency ke file lain jika referensi penting ditemukan
    - Hentikan traversal jika confidence cukup (jangan exhaustive)

    ### Tahap 4 — Synthesize & Output
    Format output IDENTIK dengan proxy response (agar /.plan bisa consume hasilnya):

    [EXPLORATION/ANALYSIS RESULT]
    source:          graphify + claude (local mode) | claude (local mode)
    graphify_source: active | unavailable
    session:         <session_id>
    confidence:      low | medium | high

    findings:
    <structured findings — sama detail dengan Kimi output>

    uncertainties:
    <area yang tidak bisa dikonfirmasi dari file yang tersedia>

    ---

    ## Behavior per Skill saat [LOCAL_MODE] = true

    ### /.explore (local mode)
    - Skip $AI_PROXY check dan proxy invocation
    - Jalankan GRAPHIFY-MIRRORED EXECUTION PROTOCOL (Tahap 1–4)
    - Lanjut ke output STEP 4 skill explore

    ### /.plan (local mode)
    - Skip $AI_PROXY check dan STEP collect Kimi evidence
    - Jalankan GRAPHIFY-MIRRORED EXECUTION PROTOCOL sebagai pengganti Kimi evidence
    - Read/Glob/Grep DIIZINKAN (exception dari Global Forbidden karena proxy skip)
    - Hasil Tahap 4 dijadikan evidence untuk plan
    - Tandai: evidence_source: graphify + claude (local mode)
    - confidence + uncertainties TETAP wajib di output plan

    ### /.analyze (local mode)
    - Skip $AI_PROXY check dan proxy invocation
    - Jalankan GRAPHIFY-MIRRORED EXECUTION PROTOCOL (Tahap 1–4)
    - Lanjut ke output STEP 4 skill analyze

    ---

    ## Output Format saat Toggle

    /.local on (atau toggle → on):
    [LOCAL MODE — ON]
    Proxy:         dinonaktifkan untuk session ini
    Flow:          graphify-mirrored (Kimi equiv via graphify + Read/Glob/Grep)
    Coverage:      /.explore /.plan /.analyze
    Graphify:      active jika graphify-out/ ada | direct glob jika tidak ada
    Kembali proxy: /.local off

    /.local off (atau toggle → off):
    [LOCAL MODE — OFF]
    Proxy:    aktif kembali
    Coverage: /.explore /.plan /.analyze

    /.local status:
    [LOCAL MODE STATUS]
    State:          ON | OFF
    Flow:           graphify-mirrored | proxy
    Graphify out:   exists | not found
    Coverage:       /.explore /.plan /.analyze

    ---

    ## Rules
    - Zero code changes
    - Zero file modifications
    - /.execute tidak terpengaruh (sudah Claude by default)
    - /.memory tidak terpengaruh
    - [LOCAL_MODE] reset ke false saat session baru
    - Output format HARUS identik dengan proxy output agar interop dengan /.plan

## STEP 2 — Buat file ~/.claude/commands/local.md

Buat direktori ~/.claude/commands/ jika belum ada.
Tulis file baru berikut:

    ---
    description: Toggle no-proxy mode — Claude mirrors Kimi traversal via graphify + Read/Glob/Grep. Covers /.explore /.plan /.analyze for current session. Usage: /local [on|off|status]
    ---

    Read ~/.claude/skills/local.md and follow its protocol exactly.

## STEP 3 — Update ~/.claude/CLAUDE.md

JANGAN overwrite file. Lakukan 4 patch berikut:

### Patch A — Command Registry
Cari section "## Command Registry V1.1" → cari baris "- /.help"
Tambahkan TEPAT setelah baris tersebut:

    - /.local    [on|off|status]

### Patch B — NL Map
Cari baris yang diawali "cek logic→/.analyze"
Append ke akhir baris tersebut (sebelum newline):

     | tanpa proxy→/.local on | kembali proxy→/.local off

### Patch C — Global Forbidden (2 baris)
Cari baris:
    - Read/Glob/Grep langsung saat /.plan atau /.analyze aktif (gunakan Kimi evidence)
Ganti dengan:
    - Read/Glob/Grep langsung saat /.plan atau /.analyze aktif DAN [LOCAL_MODE] = false

Tambahkan baris baru setelahnya:
    - Ignore [LOCAL_MODE] state — selalu cek sebelum invoke proxy

Cari baris:
    - Plan tanpa collect evidence dulu (jika proxy tersedia)
Ganti dengan:
    - Plan tanpa collect evidence dulu (jika proxy tersedia DAN [LOCAL_MODE] = false)

### Patch D — Proxy Architecture routing
Cari baris yang mengandung "Graphify hooks (SessionStart/Stop)"
Tambahkan setelah paragraph tersebut (sebelum section berikutnya):

    Local Mode (saat [LOCAL_MODE] = true):
    - /.explore → GRAPHIFY-MIRRORED EXECUTION PROTOCOL (graphify + Claude)
    - /.plan    → GRAPHIFY-MIRRORED EXECUTION PROTOCOL sebagai evidence → Claude plan
    - /.analyze → GRAPHIFY-MIRRORED EXECUTION PROTOCOL (graphify + Claude)
    Output format identik dengan proxy response agar interop antar skill.

## STEP 4 — Verifikasi

1. Baca ~/.claude/skills/local.md — pastikan mengandung "GRAPHIFY-MIRRORED EXECUTION PROTOCOL"
2. Baca ~/.claude/commands/local.md — pastikan mengandung "description:" dan "skills/local.md"
3. Baca ~/.claude/CLAUDE.md — pastikan:
   a. "/.local    [on|off|status]" ada di Command Registry
   b. "tanpa proxy→/.local on" ada di NL Map
   c. "[LOCAL_MODE] = false" ada di Global Forbidden (2 baris)
   d. "Ignore [LOCAL_MODE] state" ada di Global Forbidden
   e. "GRAPHIFY-MIRRORED EXECUTION PROTOCOL" ada di Proxy Architecture

## STEP 5 — Final Report

Tampilkan PERSIS:

    [CONFIG UPDATE — SKILL /.local + CUSTOM COMMAND /local]

    File baru:
      ~/.claude/skills/local.md    ✓  (graphify-mirrored protocol, 4-tahap traversal)
      ~/.claude/commands/local.md  ✓  (custom command /local, description set)

    Config patched:
      ~/.claude/CLAUDE.md  ✓
        - Command Registry: /.local [on|off|status] ditambahkan
        - NL Map: tanpa proxy→/.local on | kembali proxy→/.local off
        - Global Forbidden: [LOCAL_MODE] condition (2 baris) + Ignore rule
        - Proxy Architecture: Local Mode routing ditambahkan

    Skills tidak berubah:
      ~/.claude/skills/explore.md
      ~/.claude/skills/plan.md
      ~/.claude/skills/analyze.md
      ~/.claude/skills/execute.md
      ~/.claude/skills/memory.md
      ~/.claude/skills/help.md

    Status: UPDATED
    Skill baru:    /.local [on|off|status]
    Custom cmd:    /local (muncul di command palette Claude Code)
    Flow saat ON:  graphify-mirrored (graphify-out/ → scope → deep dive → output)
    Fallback flow: direct Glob jika graphify-out/ tidak ada

    Usage:
      /.local on     → aktifkan, Claude gunakan graphify-out/ sebagai peta struktur
      /.local off    → kembali ke proxy (Kimi)
      /.local status → cek state + apakah graphify-out/ tersedia
      /.local        → toggle
```

## PROMPT (sampai sini)

---

## Cara Penggunaan

1. Buka Claude Code session
2. Pastikan working directory adalah project ini (berisi `CLAUDE_CODE_CONFIG_V1.1.md`)
3. Copy seluruh block PROMPT di atas
4. Paste ke Claude Code
5. Tekan Enter

## Catatan

- **`/local`** muncul di command palette Claude Code (dari `~/.claude/commands/`)
- **`/.local`** digunakan dalam workflow skill (enforce `/.` prefix convention)
- Keduanya menjalankan protokol yang sama via `skills/local.md`
- Graphify-mirrored flow: graphify-out/ JSON → scope filter → Read/Grep → output format Kimi
- Jika `graphify-out/` tidak ada → fallback ke Glob langsung (no graphify, Claude only)
- State `[LOCAL_MODE]` reset ke false di setiap session baru
- Skill `/.execute` dan `/.memory` tidak terpengaruh — keduanya sudah Claude-only
