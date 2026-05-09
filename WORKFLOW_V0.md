Lakukan setup Personal Workflow Skills V0.3 secara lengkap.
Ikuti setiap step secara berurutan. Jangan skip. Jangan tanya — langsung eksekusi.

---

## STEP 0 — Deteksi agent & tentukan path

Deteksi agent yang sedang menjalankan dokumen ini berdasarkan sinyal yang tersedia.
Jalankan pengecekan secara berurutan — stop di sinyal pertama yang valid.

| Prioritas | Sinyal | Agent | AGENT_DIR | CONFIG_FILE |
|-----------|--------|-------|-----------|-------------|
| 1 | ~/.claude/ exists | Claude Code | ~/.claude/ | CLAUDE.md |
| 2 | ~/.codex/ exists ATAU env CODEX_* set | Codex | ~/.codex/ | AGENTS.md |
| 3 | ~/.cursor/ exists | Cursor | ~/.cursor/ | rules/workflow-skills.md |
| 4 | ~/.windsurf/ exists | Windsurf | ~/.windsurf/ | rules/workflow-skills.md |
| 5 | ~/.gemini/ exists | Gemini CLI | ~/.gemini/ | GEMINI.md |
| 6 | ~/.github-copilot/ exists | GitHub Copilot | ~/.github-copilot/ | instructions.md |
| 7 | ~/.cody/ exists | Cody | ~/.cody/ | WORKFLOW.md |
| 8 | Tidak ada sinyal | Unknown | ~/.workflow/ | WORKFLOW.md |

Output SEBELUM lanjut:

    [AGENT DETECTED]
    agent:       <nama agent>
    AGENT_DIR:   <path absolut>
    CONFIG_FILE: <AGENT_DIR/CONFIG_FILE>
    confidence:  high | medium | low

Jika confidence = low (Unknown fallback):
→ Output: "Agent tidak terdeteksi. Pakai ~/.workflow/ sebagai fallback. Lanjut? (yes/no)"
- yes → lanjut dengan AGENT_DIR = ~/.workflow/
- no  → STOP. Output: "Set AGENT_DIR manual, lalu jalankan ulang."

Semua referensi {AGENT_DIR} dan {CONFIG_FILE} di seluruh step berikutnya = nilai dari STEP 0.

---

## STEP 1 — Idempotency check

Cek apakah setup sudah pernah dijalankan di agent ini:

1. Apakah {AGENT_DIR}/skills/ EXISTS?
2. List semua .md file di {AGENT_DIR}/skills/ (jika ada)
3. Apakah {CONFIG_FILE} mengandung marker `<!-- WORKFLOW-SKILLS:START -->`?

Output:

    [SETUP STATUS]
    mode:          fresh | update
    skills_found:  <list file atau "none">
    config_marker: found | not found

IF mode = update:
→ Output EXACT: "Setup sebelumnya ditemukan di {AGENT_DIR}. Mode: UPDATE — skill files diperbarui, memory dipertahankan, config di-merge. Lanjut? (yes/no)"
- yes → proceed mode UPDATE (skill overwrite, memory skip, config merge)
- no  → STOP.

IF mode = fresh:
→ Proceed langsung ke STEP 2.

---

## STEP 2 — Buat/update skill files

Buat direktori jika belum ada:
- {AGENT_DIR}/skills/

Untuk setiap skill file: SELALU overwrite (skill adalah template — bukan data user).

---

### FILE: {AGENT_DIR}/skills/explore.md

    # Skill: explore
    description: Graphify-first codebase exploration dengan bounded objective dan intent check
    agent_dir: {AGENT_DIR}

    ## Trigger
    /.explore <hint>

    ## Pre-condition
    Cek apakah graphify-out/ EXISTS di project aktif:
    - EXISTS     → proceed ke STEP 0
    - NOT EXISTS → STOP eksplorasi
      → Deteksi framework dari sinyal minimal:
          Laravel → artisan / composer.json
          FastAPI → requirements.txt / pyproject.toml
          NestJS  → nest-cli.json
          Next.js → next.config.*
          Flutter → pubspec.yaml
          Rust    → Cargo.toml
          React   → package.json (tanpa framework marker lain)
          Default → unknown
      → Generate .graphifyignore sesuai framework (lihat template di {CONFIG_FILE})
      → Output EXACTLY:
          ```
          .graphifyignore
          <content>

          Run this in your terminal:
          graphify update
          ```
      → STOP. Jangan lanjut task.

    ## STEP 0 — Intent Check
    Jika hint luas atau ambigu → output [ASUMSI INTENT]:
      Hint     : <user hint>
      Inferred : <intent yang disimpulkan>
      Scope    : <scope sempit yang akan dieksplorasi>
    → Tunggu koreksi atau lanjut jika tidak ada respons

    ## Execution

    STEP 1 — Tentukan session:
    - Jika [SESSION_ID] sudah ada di context → reuse
    - Jika belum → generate: <project>-<YYYYMMDD_HHMMss>
    - Simpan [SESSION_ID] untuk reuse selama session ini

    STEP 2 — Output exploration plan sebelum mulai:
    [EXPLORATION PLAN]
    session:        <session_id>
    target:         <derived from hint>
    stop_condition: <kondisi eksak kapan eksplorasi berhenti>

    STEP 3 — Explore via graphify-out/:
    - Map node relevan via graphify-out/
    - Buka file HANYA jika referensi graph tidak cukup
    - Tandai pola tidak familiar: "possibly team-specific, needs verification"

    STEP 4 — Stop ketika stop_condition terpenuhi

    STEP 5 — Output structured result:

    [EXPLORATION RESULT]
    session:       <session_id>
    source:        graphify | agent (direct)
    confidence:    low | medium | high

    entry_points:
    <list>

    ownership_hints:
    <list>

    related_modules:
    <list>

    uncertainties:
    <area yang tidak bisa dikonfirmasi dari graph>

    ## End
    "Lanjut plan, atau cukup informasinya?"

---

### FILE: {AGENT_DIR}/skills/plan.md

    # Skill: plan
    description: Structured planning dengan confidence model, decision gate, graphify evidence
    agent_dir: {AGENT_DIR}

    ## Trigger
    /.plan <task>

    ## Execution

    STEP 1 — Tentukan session:
    - Jika [SESSION_ID] sudah ada di context → reuse
    - Jika belum → generate: <project>-<YYYYMMDD_HHMMss>

    STEP 2 — Collect evidence:
    - Primary: baca graphify-out/ untuk pahami konteks task
    - Jika graphify-out/ tidak ada:
      → Output EXACT: "[GRAPHIFY TIDAK TERSEDIA] Lanjut plan dari file langsung? (yes/no)"
      - yes → baca file relevan langsung. Tandai: evidence_source: agent (direct)
      - no  → STOP.

    STEP 3 — Output structured plan:

    [PLAN]
    task:            <restatement>
    session:         <session_id>
    evidence_source: graphify | agent (direct)

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
      clarify    → open_questions harus dijawab dulu
      re-explore → root_cause confidence rendah

    STEP 4 — Tunggu approval user. JANGAN auto-proceed ke execute.

    ## End
    "Setuju? Jalankan /.execute -y"

---

### FILE: {AGENT_DIR}/skills/execute.md

    # Skill: execute
    description: Controlled implementation dengan explicit approval gate
    agent_dir: {AGENT_DIR}

    ## Trigger
    /.execute -y  → PROCEED
    /.execute     → GATE only

    ## Gate Check
    Tanpa -y → output [EXECUTION SCOPE] only → "Tambahkan -y" → STOP
    Dengan -y → proceed

    ## Pre-Execution
    Output sebelum menyentuh file apapun:

    [EXECUTION SCOPE]
    allowed:   <files boleh diubah>
    forbidden: <files tidak boleh disentuh>
    reason:    <alasan batasan>

    ## During Execution
    - ONLY touch files in allowed list
    - Jika butuh forbidden file → STOP → report conflict → minta instruksi explicit

    ## Post-Execution

    [EXECUTION RESULT]
    files_changed: <list>
    confidence:    low | medium | high
    uncertainties: <list>
    status:        done | partial | blocked

    → Auto-trigger /.verify
    → JANGAN declare done sebelum /.verify selesai

---

### FILE: {AGENT_DIR}/skills/verify.md

    # Skill: verify
    description: 3-step verification — logic, falsification, reality check
    agent_dir: {AGENT_DIR}

    ## Trigger
    /.verify
    Auto-triggered setelah setiap /.execute -y atau /.refactor

    ## Protocol

    Step 1 — Logical Validation
    - Solve stated problem?
    - Assumptions masih valid?
    - Konsisten dengan pola codebase?
    Output: PASS / FAIL + reason

    Step 2 — Falsification
    - Kondisi apa yang bikin gagal?
    - Edge case yang tidak ter-cover?
    - Apa yang break jika input malformed?
    Output: list failure conditions

    Step 3 — Reality Check
    Priority: test suite → run code → simulate → state "not executable"
    Output: actual vs expected

    ## Final Output

    [VERIFICATION]
    Step 1 (Logic):   PASS / FAIL — <reason>
    Step 2 (Failure): <condition list>
    Step 3 (Reality): <actual output> | not executable — <reason>
    Verdict:          DONE / NEEDS FIX — <detail>

    ## If NEEDS FIX
    → Fix → re-run /.verify otomatis → JANGAN output final sebelum selesai

---

### FILE: {AGENT_DIR}/skills/refactor.md

    # Skill: refactor
    description: Structural improvement tanpa behavior change
    agent_dir: {AGENT_DIR}

    ## Trigger
    /.refactor <scope>

    ## Rules
    - Structural improvement ONLY — behavior TIDAK BOLEH berubah
    - JANGAN perluas scope
    - Requires /.verify setelah selesai

    ## Pre-Execution

    [REFACTOR SCOPE]
    scope:     <area>
    allowed:   <files>
    forbidden: <files>
    goal:      <tujuan struktural>

    ## Post-Refactor
    → Auto-trigger /.verify

---

### FILE: {AGENT_DIR}/skills/analyze.md

    # Skill: analyze
    description: Deep reasoning — zero code changes. Graphify sebagai sumber utama.
    agent_dir: {AGENT_DIR}

    ## Trigger
    /.analyze <topic>

    ## Execution

    STEP 1 — Session: reuse [SESSION_ID] atau generate baru

    STEP 2 — Collect evidence:
    - Primary: graphify-out/
    - Buka file spesifik HANYA jika graph tidak cukup
    - Flag area yang tidak bisa dikonfirmasi

    STEP 3 — Output:

    [ANALYSIS RESULT]
    session:  <session_id>
    source:   graphify | agent (direct)

    confidence:
      problem_understanding: low | medium | high
      root_cause:            low | medium | high
      solution_path:         low | medium | high

    findings:
    <content>

    implications:
    <dampak ke codebase atau keputusan>

    uncertainties:
    <area yang tidak bisa dikonfirmasi>

    ## Rules
    - Zero code changes. Zero file modifications.

---

### FILE: {AGENT_DIR}/skills/memory.md

    # Skill: memory
    description: Propose memory update ke personal knowledge files
    agent_dir: {AGENT_DIR}

    ## Trigger
    /.memory <note>

    ## Execution

    STEP 1 — Evaluasi note:
    - Berdampak ke keputusan masa depan?
    - Info ownership atau arsitektur baru?
    - Recurring issue atau landmine?

    STEP 2 — Output proposal:

    [MEMORY PROPOSAL]
    file:    <{AGENT_DIR}/memory/PERSONAL_MEMORY.md atau DOMAIN_MAP.md>
    action:  <add | update | overwrite>
    content:
      <proposed content>

    Confirm? (yes / no / edit)

    STEP 3 — Tunggu respons:
    - yes  → write ke file → update {AGENT_DIR}/memory/MEMORY.md jika belum ada entry
    - no   → discard
    - edit → tunggu koreksi → write

    STEP 4 — Append ke SESSION_LOG.md:
    [YYYY-MM-DD]
    task:           <what was done>
    domain:         <module/area>
    memory written: <yes — which file | no — declined>

---

### FILE: {AGENT_DIR}/skills/help.md

    # Skill: help
    description: Command reference V0.3 Single Agent Mode
    agent_dir: {AGENT_DIR}

    ## Trigger
    /.help

    ## Output

    [COMMAND GUIDE — V0.3 SINGLE AGENT MODE]

    /.explore <hint>   → eksplorasi via graphify | fallback: agent direct (konfirmasi dulu)
    /.plan <task>      → collect graphify evidence → buat rencana struktural
    /.execute -y       → implementasi (wajib -y)
    /.verify           → validasi logic + falsification + reality check
    /.refactor <scope> → perbaikan struktural tanpa ubah behavior (auto-trigger verify)
    /.analyze <topic>  → analisis mendalam tanpa ubah kode
    /.memory <note>    → simpan insight (auto-update MEMORY.md index)

    [WORKFLOW]
    /.explore → /.plan → /.execute -y → /.verify

    [SOURCE ROUTING]
    explore  → graphify-out/ (primary) | agent direct (fallback, perlu konfirmasi)
    plan     → graphify evidence + agent reasoning
    analyze  → graphify-out/ (primary) | agent direct (fallback)
    execute  → agent
    verify   → agent
    refactor → agent + auto /.verify

    Prefix "/." wajib. Tanpa prefix → INVALID.

---

## STEP 3 — Buat/pertahankan memory files

Buat direktori jika belum ada: {AGENT_DIR}/memory/

ATURAN: Memory adalah data user — JANGAN overwrite jika sudah ada.

### FILE: {AGENT_DIR}/memory/PERSONAL_MEMORY.md — SKIP JIKA EXISTS

    # Personal Memory
    Last updated: <tanggal hari ini>

    ## Architecture Decisions
    - (belum ada)

    ## Module Ownership
    | Module | Team | Notes |
    |--------|------|-------|
    | -      | -    | -     |

    ## Known Landmines
    - (belum ada)

    ## Patterns Per Team
    - (belum ada)

    ## Things I Always Forget
    - (belum ada)

### FILE: {AGENT_DIR}/memory/DOMAIN_MAP.md — SKIP JIKA EXISTS

    # Domain Map
    Last updated: <tanggal hari ini>

    ## Entry Points
    | Domain | Entry File | Key Function |
    |--------|------------|--------------|
    | -      | -          | -            |

    ## Cross-Team Boundaries
    - (belum ada)

    ## Dead Code Suspects
    - (belum ada)

### FILE: {AGENT_DIR}/memory/SESSION_LOG.md — SKIP JIKA EXISTS

    # Session Log

    ## <tanggal hari ini>
    task:           initial skill setup V0.3
    domain:         global config
    memory written: no

### FILE: {AGENT_DIR}/memory/MEMORY.md — UPDATE (tambah entry jika belum ada, jangan hapus yang lama)

Entry yang harus ada (cek satu per satu, append jika belum ada):

    - [Personal Memory](PERSONAL_MEMORY.md) — arsitektur decisions, module ownership, landmines
    - [Domain Map](DOMAIN_MAP.md) — entry points, cross-team boundaries, dead code suspects
    - [Session Log](SESSION_LOG.md) — log aktivitas per session

---

## STEP 4 — Buat/merge config file

Target: {CONFIG_FILE}

### Jika {CONFIG_FILE} TIDAK EXISTS → buat baru dengan konten lengkap di bawah.

### Jika {CONFIG_FILE} SUDAH EXISTS → cari marker <!-- WORKFLOW-SKILLS:START --> :
- Marker DITEMUKAN  → ganti konten antara marker START dan END dengan versi baru
- Marker TIDAK ADA  → APPEND blok berikut ke akhir file (jangan hapus konten lama)

Konten yang ditulis (selalu dibungkus marker):

    <!-- WORKFLOW-SKILLS:START — managed by WORKFLOW_V0.3, do not edit manually -->

    ## Workflow Skills V0.3
    agent_dir: {AGENT_DIR}
    skills:    {AGENT_DIR}/skills/
    memory:    {AGENT_DIR}/memory/

    ### Core Behavior
    - Concise. Direct. No over-explanation.
    - Single user. Optimize for workflow only.
    - Never assume. Never expand scope silently.
    - WAJIB sertakan confidence + uncertainties di setiap plan/analysis.

    ### Skill Routing
    - /.explore  → graphify-out/ (primary) | agent direct (fallback, perlu konfirmasi user)
    - /.analyze  → graphify-out/ (primary) | agent direct (fallback, perlu konfirmasi user)
    - /.plan     → graphify evidence + agent reasoning
    - /.execute  → agent (wajib -y)
    - /.verify   → agent (auto-triggered setelah execute/refactor)
    - /.refactor → agent + auto /.verify

    ### Structured Output Rule (NON-NEGOTIABLE)
    Setiap plan atau analysis HARUS mengandung:
    - confidence: { problem_understanding, root_cause, solution_path }
    - uncertainties: [ list hal yang tidak bisa dikonfirmasi ]
    Output tanpa keduanya = INCOMPLETE.

    ### Startup Protocol
    Setiap session (code tasks):
    1. Cek graphify-out/ di project aktif — jika tidak ada: warning sekali
    2. Baca {AGENT_DIR}/memory/PERSONAL_MEMORY.md jika ada konten
    3. session_id di-generate saat skill pertama diinvoke

    ### Default Behavior
    Task unclear → suggest /.explore
    Task clear   → jawab langsung

    ### Command Registry V0.3
    Valid   : /.explore /.plan /.execute /.verify /.refactor /.analyze /.memory /.help
    Invalid : tanpa prefix "/." → REJECTED

    Jika command invalid:
    [INVALID COMMAND]
    Gunakan prefix "/."
    Contoh: /.plan
    STOP.

    ### NL Map
    cek logic → /.analyze | gimana flow → /.explore | tambah fitur → /.plan
    implement → /.execute -y | rapikan → /.refactor | catat → /.memory | help → /.help
    NEVER suggest "/" commands (tanpa titik).

    ### Auto Command Suggestion
    Di akhir setiap respons (code tasks), append max 3 command relevan:
    [AVAILABLE NEXT COMMANDS]
    - /.explore | /.plan | /.execute | /.verify | /.analyze  (pilih yang relevan)

    ### Graphify Rules
    - NEVER run: graphify init, graphify build, graphify watch
    - Auto-run `graphify update` setelah SETIAP code change

    Error handling:
    - "too large for HTML viz" OR "Graph has too many nodes" → IGNORE, DO NOT retry
    - Error lain → retry ONCE → jika masih gagal: inform briefly, continue

    Graphify state:
    - graphify-out/ EXISTS     → ACTIVE, gunakan sebagai primary source
    - graphify-out/ NOT EXISTS → STOP, generate .graphifyignore, output "graphify update", STOP

    .graphifyignore templates:
    Laravel:  vendor/ node_modules/ public/build/ storage/ bootstrap/cache/ *.log .env .env.* .cache/ tmp/
    Python:   venv/ .venv/ __pycache__/ *.pyc build/ dist/ *.log .env .env.* .cache/ tmp/
    NestJS:   node_modules/ dist/ build/ coverage/ *.log .env .env.* .cache/ tmp/
    Next.js:  node_modules/ .next/ out/ dist/ build/ coverage/ *.log .env .env.* .cache/ tmp/
    React:    node_modules/ dist/ build/ coverage/ *.log .env .env.* .cache/ tmp/
    Rust:     target/ debug/ release/ *.log .env .env.* .cache/ tmp/
    Flutter:  .build/ .dart_tool/ build/ ios/Pods/ android/.gradle/ *.log .env .env.* .cache/ tmp/
    Default:  node_modules/ dist/ build/ *.log .env .env.* .cache/ tmp/

    ### Global Forbidden
    - Modifikasi file di luar [EXECUTION SCOPE]
    - Proceed /.execute tanpa -y
    - Output plan tanpa confidence + uncertainties
    - Claim success sebelum /.verify selesai
    - Write ke memory files tanpa user confirmation
    - Auto-fallback ke agent direct tanpa tanya user
    - Run graphify init / graphify build / graphify watch
    - Expand scope tanpa instruksi eksplisit

    <!-- WORKFLOW-SKILLS:END -->

---

## STEP 5 — Verifikasi seluruh setup

1. List semua file di {AGENT_DIR}/skills/ + ukuran byte
2. List semua file di {AGENT_DIR}/memory/ + ukuran byte
3. Tampilkan 5 baris pertama {CONFIG_FILE} untuk konfirmasi marker ada
4. Tampilkan isi {AGENT_DIR}/memory/MEMORY.md

---

## STEP 6 — Final Report

Tampilkan PERSIS:

    [SETUP COMPLETE — V0.3 SINGLE AGENT MODE]
    agent:   <nama agent>
    dir:     {AGENT_DIR}
    config:  {CONFIG_FILE}
    mode:    fresh install | update

    Skills (overwritten):
      {AGENT_DIR}/skills/explore.md  ✓
      {AGENT_DIR}/skills/plan.md     ✓
      {AGENT_DIR}/skills/execute.md  ✓
      {AGENT_DIR}/skills/verify.md   ✓
      {AGENT_DIR}/skills/refactor.md ✓
      {AGENT_DIR}/skills/analyze.md  ✓
      {AGENT_DIR}/skills/memory.md   ✓
      {AGENT_DIR}/skills/help.md     ✓

    Memory (preserved if existed):
      {AGENT_DIR}/memory/PERSONAL_MEMORY.md ✓ (new | kept)
      {AGENT_DIR}/memory/DOMAIN_MAP.md      ✓ (new | kept)
      {AGENT_DIR}/memory/SESSION_LOG.md     ✓ (new | kept)
      {AGENT_DIR}/memory/MEMORY.md          ✓ (index updated)

    Config:
      {CONFIG_FILE} ✓ (created | merged)
      marker: <!-- WORKFLOW-SKILLS:START --> found

    Status: READY
    Workflow: /.explore → /.plan → /.execute -y → /.verify
    Active:   /.explore /.plan /.execute /.verify /.refactor /.analyze /.memory /.help
    Invalid:  /explore /plan /execute /verify — REJECTED
