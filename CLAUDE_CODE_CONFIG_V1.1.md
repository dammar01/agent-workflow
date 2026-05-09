Lakukan setup Personal Workflow Skills V1.1 (Proxy Mode) secara lengkap. Ikuti setiap step secara berurutan. Jangan skip. Jangan tanya — langsung eksekusi.

---

## PRE-CONDITION — Informasikan ke user

Sebelum mulai, output instruksi berikut:

    [PRE-SETUP REQUIRED]
    Set env var berikut sebelum menggunakan proxy skills:

    Windows PowerShell:
      $env:AI_PROXY = "E:\path\to\ai-proxy\main.py"

    Contoh:
      $env:AI_PROXY = "E:\Work\project\ai-proxy\main.py"

    Verifikasi (multi-layer — jalankan semua, minimal 1 return path valid):
      CMD/Batch : echo %AI_PROXY%
      PowerShell: $env:AI_PROXY
      Python    : python -c "import os; print(os.environ.get('AI_PROXY',''))"
      .NET Env  : [Environment]::GetEnvironmentVariable('AI_PROXY','Process')

    Timeout default: 300 detik (5 menit). Override jika perlu:
      $env:AI_PROXY_TIMEOUT_SECONDS = "600"

    Session naming: gunakan nama konsisten per project + fitur.
    Contoh: "finance-auth", "bangai-api"

    Jika $AI_PROXY tidak di-set: skills otomatis fallback ke graphify + Claude.

---

## STEP 1 — Buat direktori struktur

Buat direktori berikut jika belum ada:

- ~/.claude/
- ~/.claude/skills/
- ~/.claude/memory/

---

## STEP 2 — Buat skill files

---

### FILE: ~/.claude/skills/explore.md

Tulis konten berikut ke file:

    # Skill: explore
    description: Kimi-powered exploration via proxy. Graphify sebagai fallback lokal.

    ## Trigger
    /.explore <hint>

    ## Pre-condition
    - Cek $AI_PROXY tersedia (execute in order, stop at first valid path):
      1. Run: echo %AI_PROXY%
      2. Run: $env:AI_PROXY
      3. Run: python -c "import os; print(os.environ.get('AI_PROXY',''))"
      4. Run: [Environment]::GetEnvironmentVariable('AI_PROXY','Process')
      Jika semua output kosong, literal "%AI_PROXY%", atau error → Output EXACT:
        "[PROXY TIDAK TERSEDIA] $AI_PROXY belum di-set. Lanjut tanpa Kimi? (yes/no)"
        - yes → lanjut STEP 3F (claude direct)
        - no  → STOP. Selesai.
      Jika salah satu output = valid path → proceed

    ## STEP 0 — Intent Check
    Jika hint luas atau ambigu → output [ASUMSI INTENT]:
      Hint     : <user hint>
      Inferred : <intent yang disimpulkan>
      Scope    : <scope sempit>
    → Tunggu koreksi atau lanjut jika tidak ada respons

    ## Execution

    STEP 1 — Tentukan session dan work dir:
    - work_dir = absolute path project aktif
    - session_id (1 Claude session = 1 Kimi session):
      Jika [SESSION_ID] sudah ada di context → reuse nilai tersebut
      Jika belum → generate: <project>-<YYYYMMDD_HHMMss> (waktu sekarang)
      Simpan sebagai [SESSION_ID] untuk reuse selama session ini

    STEP 2 — Invoke proxy via terminal:
      Output EXACT ke user: "Sedang menunggu response Kimi..."
      Jalankan via Bash dengan run_in_background: true:
        python $AI_PROXY -c explore -p "<hint>" -s "<session_id>" -w "<work_dir>" --pretty
      WAJIB tunggu notifikasi completion — JANGAN lanjut sebelum notifikasi diterima.
      Setelah notifikasi diterima → lanjut STEP 3.

    STEP 3 — Parse response:
    - status == success → lanjut STEP 4
    - status == error   → tampilkan error → lanjut STEP 3F (fallback)

    STEP 3F — Proxy gagal:
    STOP semua proses. Output EXACT:
    "[PROXY GAGAL] Kimi tidak tersedia. Lanjut eksekusi langsung tanpa Kimi? (yes/no)"
    - yes → Claude explore dari context + graphify-out/ jika tersedia. Tandai: source: claude (direct)
    - no  → STOP. Selesai.

    STEP 4 — Output structured result:

    [EXPLORATION RESULT]
    source:         kimi (via proxy) | claude (direct)
    session:        <session_id>
    confidence:     <dari response.meta.confidence>

    findings:
    <content dari response>

    uncertainties:
    <unknown_area atau bagian low confidence>

    ## End
    "Lanjut plan, atau cukup informasinya?"

---

### FILE: ~/.claude/skills/plan.md

Tulis konten berikut ke file:

    # Skill: plan
    description: Structured planning dengan confidence model, decision gate, proxy evidence

    ## Trigger
    /.plan <task>

    ## Execution

    STEP 1 — Tentukan session dan work dir:
    - work_dir = absolute path project aktif
    - session_id (1 Claude session = 1 Kimi session):
      Jika [SESSION_ID] sudah ada di context → reuse nilai tersebut
      Jika belum → generate: <project>-<YYYYMMDD_HHMMss> (waktu sekarang)
      Simpan sebagai [SESSION_ID] untuk reuse selama session ini

    STEP 2 — Cek $AI_PROXY (execute in order, stop at first valid path):
      1. Run: echo %AI_PROXY%
      2. Run: $env:AI_PROXY
      3. Run: python -c "import os; print(os.environ.get('AI_PROXY',''))"
      4. Run: [Environment]::GetEnvironmentVariable('AI_PROXY','Process')
      Jika semua output kosong, literal, atau error → lanjut FALLBACK (graphify-out/ + Claude)
      Jika salah satu output = valid path → proceed

    STEP 3 — Collect evidence via Kimi:
      Output EXACT ke user: "Sedang menunggu response Kimi..."
      Jalankan via Bash dengan run_in_background: true:
        python $AI_PROXY -c explore -p "<ringkasan task>" -s "<session_id>" -w "<work_dir>" --pretty
      WAJIB tunggu notifikasi completion — JANGAN lanjut sebelum notifikasi diterima.
      Setelah notifikasi diterima → proses hasilnya.
    - Jika proxy gagal → STOP. Output EXACT:
      "[PROXY GAGAL] Kimi tidak tersedia. Lanjut plan tanpa evidence Kimi? (yes/no)"
      - yes → Claude plan dari context. Tandai: evidence_source: none (proxy gagal)
      - no  → STOP. Selesai.

    RULE: Claude DILARANG Read/Glob/Grep selama plan phase.
    Semua informasi HARUS dari Kimi evidence.

    STEP 4 — Output structured plan:

    [PLAN]
    task:           <restatement>
    evidence_source: kimi (via proxy) | graphify+claude (fallback) | none

    assumptions:
      - <statement — BUKAN pertanyaan>

    open_questions:
      - <max 5, harus impact impl/arch>

    steps:
      1. <concrete step>
      2. <concrete step>

    files_affected: <list>

    risks: <list>

    confidence:
      problem_understanding: low | medium | high
      root_cause:            low | medium | high
      solution_path:         low | medium | high

    uncertainties:
      - <hal yang tidak bisa dikonfirmasi>

    decision:
      proceed    → confidence cukup
      clarify    → open_questions harus dijawab
      re-explore → root_cause confidence rendah

    STEP 5 — Tunggu approval user. JANGAN auto-proceed ke execute.

    ## End
    "Setuju? Jalankan /.execute -y"

---

### FILE: ~/.claude/skills/execute.md

Tulis konten berikut ke file:

    # Skill: execute
    description: Controlled implementation dengan explicit approval gate

    ## Trigger
    /.execute -y     → PROCEED
    /.execute        → GATE only

    ## Gate Check
    Tanpa -y:
    → Output [EXECUTION SCOPE] only
    → "Tambahkan -y untuk konfirmasi eksekusi"
    → STOP

    Dengan -y → proceed

    ## Pre-Execution
    Output sebelum menyentuh file apapun:

    [EXECUTION SCOPE]
    allowed:   <files boleh diubah>
    forbidden: <files tidak boleh disentuh>
    reason:    <alasan batasan>

    ## During Execution
    - ONLY touch files in allowed list
    - Jika butuh forbidden file → STOP → report conflict → minta instruksi

    ## Post-Execution

    [EXECUTION RESULT]
    files_changed: <list>
    confidence:    low | medium | high
    uncertainties: <list>
    status:        done | partial | blocked

---

### FILE: ~/.claude/skills/analyze.md

Tulis konten berikut ke file:

    # Skill: analyze
    description: Deep analysis via proxy (default Kimi). Fallback: graphify + Claude. --local: Claude only.

    ## Trigger
    /.analyze <topic>           → via proxy (Kimi), fallback graphify+Claude
    /.analyze --local <topic>   → Claude langsung (skip proxy dan graphify)

    ## Default Flow (via proxy)

    STEP 1 — Tentukan session dan work dir:
    - work_dir = absolute path project aktif
    - session_id (1 Claude session = 1 Kimi session):
      Jika [SESSION_ID] sudah ada di context → reuse nilai tersebut
      Jika belum → generate: <project>-<YYYYMMDD_HHMMss> (waktu sekarang)
      Simpan sebagai [SESSION_ID] untuk reuse selama session ini
    - Cek $AI_PROXY (execute in order, stop at first valid path):
      1. Run: echo %AI_PROXY%
      2. Run: $env:AI_PROXY
      3. Run: python -c "import os; print(os.environ.get('AI_PROXY',''))"
      4. Run: [Environment]::GetEnvironmentVariable('AI_PROXY','Process')
      Jika semua output kosong, literal, atau error → lanjut STEP 3F (fallback)
      Jika salah satu output = valid path → proceed

    STEP 2 — Invoke proxy:
      Output EXACT ke user: "Sedang menunggu response Kimi..."
      Jalankan via Bash dengan run_in_background: true:
        python $AI_PROXY -c explore -p "<topic>" -s "<session_id>" -w "<work_dir>" --pretty
      WAJIB tunggu notifikasi completion — JANGAN lanjut sebelum notifikasi diterima.
      Setelah notifikasi diterima → lanjut STEP 3.

    RULE: Claude DILARANG Read/Glob/Grep selama analyze phase.
    Semua informasi HARUS dari Kimi evidence.

    STEP 3 — Parse response:
    - status == success → output STEP 4
    - status == error   → tampilkan error → lanjut STEP 3F (fallback)

    STEP 3F — Proxy gagal:
    STOP semua proses. Output EXACT:
    "[PROXY GAGAL] Kimi tidak tersedia. Lanjut eksekusi langsung tanpa Kimi? (yes/no)"
    - yes → Claude analyze dari context + graphify-out/ jika tersedia. Tandai: source: claude (direct)
    - no  → STOP. Selesai.

    STEP 4 — Output:

    [ANALYSIS RESULT]
    source:         kimi (via proxy) | claude (direct) | claude (local)
    confidence:     <dari response>

    findings:
    <content>

    implications:
    <dampak ke codebase atau keputusan>

    uncertainties:
    <area yang tidak bisa dikonfirmasi>

    ## Local Flow (--local)
    - Skip proxy dan graphify
    - Claude langsung analyze dari context yang tersedia
    - Tandai: source: claude (local)

    ## Rules
    - Zero code changes
    - Zero file modifications

---

### FILE: ~/.claude/skills/memory.md

Tulis konten berikut ke file:

    # Skill: memory
    description: Propose memory update ke personal knowledge files

    ## Trigger
    /.memory <note>

    ## Execution

    STEP 1 — Evaluasi note:
    - Berdampak ke keputusan masa depan?
    - Info ownership atau arsitektur baru?
    - Recurring issue atau landmine?

    STEP 2 — Output proposal:

    [MEMORY PROPOSAL]
    file:    <~/.claude/memory/PERSONAL_MEMORY.md atau DOMAIN_MAP.md>
    action:  <add | update | overwrite>
    content:
      <proposed content>

    Confirm? (yes / no / edit)

    STEP 3 — Tunggu respons:
    - yes  → write ke file target, lalu update entry di ~/.claude/memory/MEMORY.md jika belum ada
    - no   → discard
    - edit → tunggu koreksi, write

---

### FILE: ~/.claude/skills/help.md

Tulis konten berikut ke file:

    # Skill: help
    description: Command reference V1.1 proxy workflow

    ## Trigger
    /.help

    ## Output

    [COMMAND GUIDE — V1.1 PROXY MODE]

    /.explore <hint>
    → eksplorasi via Kimi proxy | fallback: graphify+Claude
    contoh: /.explore cari alur auth middleware

    /.plan <task>
    → collect Kimi evidence otomatis → buat rencana | fallback: graphify evidence
    contoh: /.plan tambah fitur refresh token

    /.execute -y
    → implementasi (wajib -y)
    contoh: /.execute -y

    /.analyze <topic>
    → analisis via Kimi | fallback: graphify+Claude | --local: Claude only
    contoh: /.analyze apakah pattern ini thread-safe

    /.memory <note>
    → simpan insight (auto-update MEMORY.md index)
    contoh: /.memory auth owned by backend team

    /.local [on|off|status]
    → toggle no-proxy mode (Claude only, Read/Glob/Grep aktif)
    contoh: /.local on

    ---
    [WORKFLOW V1.1]
    /.explore → /.plan → /.execute -y

    [ROUTING]
    explore  → Kimi (via $AI_PROXY) | fallback: graphify+Claude
    plan     → Claude + evidence Kimi (auto-collect) | fallback: graphify evidence
    execute  → Claude
    analyze  → Kimi default | fallback: graphify+Claude | --local: Claude only
    local    → Claude only (toggle session-level, graphify-mirrored flow)

    [ORPHANED — file ada, command tidak valid]
    /.verify, /.refactor → dihapus dari V1.1, file tetap di disk

    Prefix "/." wajib. Tanpa prefix → INVALID.

---

### FILE: ~/.claude/skills/local.md

Tulis konten berikut ke file:

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

---

### FILE: ~/.claude/commands/local.md

Buat direktori ~/.claude/commands/ jika belum ada, lalu tulis file baru:

    ---
    description: Toggle no-proxy mode — Claude mirrors Kimi traversal via graphify + Read/Glob/Grep. Covers /.explore /.plan /.analyze for current session. Usage: /local [on|off|status]
    ---

    Read ~/.claude/skills/local.md and follow its protocol exactly.

---

## STEP 3 — Buat memory files (jangan overwrite jika sudah ada)

### FILE: ~/.claude/memory/PERSONAL_MEMORY.md (skip jika exists)

    # Personal Memory
    Last updated: 2026-05-05

    ## Architecture Decisions
    - (belum ada)

    ## Module Ownership
    | Module | Team | Notes |
    |--------|------|-------|
    | -      | -    | -     |

    ## Known Landmines
    - (belum ada)

    ## Proxy Config
    - Session naming: <project>-<feature>
    - Fallback: graphify + Claude (tanpa Kimi) jika proxy gagal

### FILE: ~/.claude/memory/DOMAIN_MAP.md (skip jika exists)

    # Domain Map
    Last updated: 2026-05-05

    ## Entry Points
    | Domain | Entry File | Key Function |
    |--------|------------|--------------|
    | -      | -          | -            |

    ## Cross-Team Boundaries
    - (belum ada)

### FILE: ~/.claude/memory/MEMORY.md (update — tambah entry jika belum ada)

Baca isi ~/.claude/memory/MEMORY.md saat ini.
Untuk setiap entry berikut, cek apakah sudah ada. Jika belum → append ke file:

    - [Personal Memory](PERSONAL_MEMORY.md) — arsitektur decisions, module ownership, landmines, proxy config
    - [Domain Map](DOMAIN_MAP.md) — entry points, cross-team boundaries, dead code suspects

Jangan hapus entry yang sudah ada. Jangan duplikasi.

---

## STEP 4 — Tulis ulang ~/.claude/CLAUDE.md (OVERWRITE)

    # Claude Code — Personal Global Config V1.1
    # Skills:  ~/.claude/skills/
    # Memory:  ~/.claude/memory/
    # Mode:    PROXY (Kimi explore, Claude reason) + Graphify fallback

    ---

    ## Core Behavior
    - Concise. Direct. No over-explanation.
    - Single user. Optimize for workflow only.
    - Never assume. Never expand scope silently.
    - WAJIB sertakan confidence + uncertainties di setiap plan/analysis.

    ---

    ## Proxy Architecture
    - /.explore  → Kimi via $AI_PROXY | fallback: graphify + Claude
    - /.analyze  → Kimi via $AI_PROXY (default) | fallback: graphify + Claude | --local: Claude only
    - /.plan     → Claude + evidence Kimi (auto-collect jika proxy ada) | fallback: graphify evidence
    - /.execute  → Claude

    Fallback rule: jika proxy gagal → STOP → tanya user → lanjut HANYA jika user konfirmasi.
    JANGAN auto-fallback ke Claude atau graphify tanpa konfirmasi user.
    Graphify hooks (SessionStart/Stop) tetap aktif untuk viz — bukan auto-fallback.

    Proxy Invocation Protocol (WAJIB untuk semua skill):
    1. Output EXACT ke user: "Sedang menunggu response Kimi..."
    2. Jalankan proxy via Bash dengan run_in_background: true
    3. WAJIB tunggu notifikasi completion — JANGAN lanjut sebelum notifikasi diterima
    4. Setelah notifikasi → parse response → lanjut ke step berikutnya

    ---

    ## Structured Output Rule (NON-NEGOTIABLE)
    Setiap plan atau analysis HARUS mengandung:
    - confidence: { problem_understanding, root_cause, solution_path }
    - uncertainties: [ list hal yang tidak bisa dikonfirmasi ]
    Output tanpa keduanya = INCOMPLETE.

    ---

    ## Startup Protocol
    Setiap session (code tasks):
    1. Cek $AI_PROXY set (execute in order, stop at first valid path):
       1. Run: echo %AI_PROXY%
       2. Run: $env:AI_PROXY
       3. Run: python -c "import os; print(os.environ.get('AI_PROXY',''))"
       4. Run: [Environment]::GetEnvironmentVariable('AI_PROXY','Process')
       → jika semua kosong/literal/error, warning sekali (fallback ke graphify aktif)
    2. Baca PERSONAL_MEMORY.md jika ada konten (skip jika kosong)
    3. session_id di-generate saat skill pertama kali diinvoke (bukan di startup)
    (Graphify hooks berjalan otomatis via settings.json — tidak perlu manual trigger)

    ---

    ## Default Behavior
    Task unclear → suggest /.explore via proxy
    Task clear   → jawab langsung

    ---

    ## Command Registry V1.1
    Valid:
    - /.explore  <hint>
    - /.plan     <task>
    - /.execute  -y
    - /.analyze  <topic>  [--local]
    - /.memory   <note>
    - /.help
    - /.local    [on|off|status]

    Orphaned (file ada, TIDAK valid dipanggil):
    - /.verify   → dihapus dari V1.1
    - /.refactor → dihapus dari V1.1

    ---

    ## Command Validation (STRICT)
    1. Hanya prefix "/." yang valid
    2. Tanpa "/." → INVALID
    3. Jangan interpret, auto-correct, atau fallback

    Output EXACT jika invalid:
    [INVALID COMMAND]
    Gunakan prefix "/."
    Contoh: /.plan
    STOP.

    ---

    ## NL Map (intent → command)
    cek logic→/.analyze | gimana flow→/.explore | tambah fitur→/.plan | langsung→/.execute -y | catat→/.memory | help→/.help | tanpa proxy→/.local on | kembali proxy→/.local off
    NEVER suggest "/" commands.

    ---

    ## Graphify Rules
    - Tersedia sebagai sumber tambahan HANYA jika user konfirmasi lanjut tanpa proxy
    - NEVER run: graphify init, graphify build, graphify watch
    - Auto-run `graphify update` after ANY code change

    ### Error Handling (strict)
    - Error contains "too large for HTML viz" OR "Graph has too many nodes" → IGNORE, DO NOT retry, continue
    - Other errors → retry ONCE: `graphify update` → if still failing: inform briefly, continue without blocking

    ## Graphify State
    ### IF graphify-out/ EXISTS
    → Graphify ACTIVE. Available as fallback. Proceed with task.

    ### IF graphify-out/ NOT EXISTS
    → Fallback unavailable. Proxy-only mode.
    → Allowed actions ONLY if proxy also unavailable:
      1. Detect framework (artisan/composer.json → Laravel, pyproject.toml → FastAPI, nest-cli.json → NestJS,
         next.config.* → Next.js, pubspec.yaml → Flutter, Cargo.toml → Rust, package.json → React)
      2. Generate .graphifyignore → lihat ~/.claude/skills/graphify-templates.md
      3. Output EXACTLY:
         ```
         .graphifyignore
         <content>

         Run this in your terminal:
         graphify update
         ```
      4. STOP. Do not continue task.

    ---

    ## Global Forbidden
    - Modifikasi file di luar [EXECUTION SCOPE]
    - Proceed /.execute tanpa -y
    - Output plan tanpa confidence + uncertainties
    - Interpret "/" commands (tanpa ".")
    - Invoke /.verify atau /.refactor (orphaned di V1.1)
    - Plan tanpa collect evidence dulu (jika proxy tersedia DAN [LOCAL_MODE] = false)
    - Gunakan Kimi sebagai fallback — fallback HANYA jika user konfirmasi
    - Auto-fallback ke Claude/graphify tanpa tanya user saat proxy gagal
    - Read/Glob/Grep langsung saat /.plan atau /.analyze aktif DAN [LOCAL_MODE] = false
    - Write ke memory files mid-session tanpa user confirmation
    - Ignore [LOCAL_MODE] state — selalu cek sebelum invoke proxy

---

## STEP 5 — Verifikasi seluruh setup

Setelah semua file dibuat:

1. List semua file di ~/.claude/skills/
2. List semua file di ~/.claude/memory/
3. Tampilkan ukuran (byte) tiap file
4. Konfirmasi ~/.claude/CLAUDE.md telah diperbarui
5. Tampilkan isi ~/.claude/memory/MEMORY.md untuk verifikasi entries
6. Konfirmasi ~/.claude/skills/local.md ada dan berisi GRAPHIFY-MIRRORED EXECUTION PROTOCOL
7. Konfirmasi ~/.claude/commands/local.md ada dan berisi description + skill delegation

---

## STEP 6 — Final Report

Tampilkan PERSIS:

    [SETUP COMPLETE — CLAUDE CODE V1.1 PROXY MODE]

    Config:
      ~/.claude/CLAUDE.md ✓

    Skills:
      ~/.claude/skills/explore.md  ✓  (→ Kimi via proxy | fallback: graphify+Claude)
      ~/.claude/skills/plan.md     ✓  (→ Claude + evidence Kimi | fallback: graphify)
      ~/.claude/skills/execute.md  ✓  (→ Claude)
      ~/.claude/skills/analyze.md  ✓  (→ Kimi default | fallback: graphify+Claude | --local: Claude)
      ~/.claude/skills/memory.md   ✓  (→ Claude, auto-update MEMORY.md index)
      ~/.claude/skills/help.md     ✓
      ~/.claude/skills/local.md    ✓  (→ toggle no-proxy, graphify-mirrored flow)
      ~/.claude/commands/local.md  ✓  (→ custom command /local, delegates to skill)

    Orphaned (file ada, command tidak valid):
      ~/.claude/skills/verify.md   (orphaned)
      ~/.claude/skills/refactor.md (orphaned)

    Memory:
      ~/.claude/memory/PERSONAL_MEMORY.md ✓
      ~/.claude/memory/DOMAIN_MAP.md      ✓
      ~/.claude/memory/MEMORY.md          ✓ (index updated)

    Status: READY
    Proxy:  set $AI_PROXY sebelum pakai skills
    Fallback: graphify + Claude aktif otomatis jika proxy gagal

    Workflow: /.explore → /.plan → /.execute -y

    Active:   /.explore /.plan /.execute /.analyze /.memory /.help /.local
    Orphaned: /.verify /.refactor (file ada, tidak valid)
    Invalid:  /explore /plan /execute /analyze /local — REJECTED
