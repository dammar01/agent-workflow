# Main Agent — v3.3.0 Setup Prompt (Agent-Agnostic Orchestrator)

> Paste prompt ini ke agent yang kamu pakai (Claude Code, Codex, Kimi, dll).
> Agent menulis config + skill files ke direktori agent terdeteksi.
> Main agent = orchestrator + user interface + direct executor.

---

## DESIGN NOTES (v3.3.0)

**Arsitektur:**
- `main_agent` = agent ini (agent-agnostic). Orchestrate, synthesize, execute langsung.
- `second_agent` = OpenCode, read-only evidence. Bukan final answer.
- **1-call interface**: main_agent panggil `.workflow/run.<ps1|sh> <command> "<task>"` → blocking → JSON `{ok, content, meta, digest}`. Tak ada karang command manual, tak ada check.py polling, tak ada resolusi session.
- Session lifecycle via SessionStart hook (Claude Code): `startup`/`clear`/`compact` → thread BARU; `resume` → LANJUT. Lihat STEP 5b.

**Perubahan kunci vs v3.2.1 (tercermin di kode):**
- `.workflow/` **self-contained**: `init` copy `opencode.json` + simpan path absolut `main.py`/`check.py` di `config.json` + generate `run.ps1`/`run.sh`/`inspect.*`. `$AGENT_PATH` cuma fallback.
- **1-call `await`** ganti dance `main.py`→`check.py`. Poll-timeout 0 (nunggu selesai) + reaper anti-deadlock.
- **Structured errors**: tiap gagal = `{error_type, next_action}`. Tak pernah output kosong-diam.
- **Digest code-side**: hasil bawa `digest` siap-relay. Main_agent relay digest, buka `content` bila perlu — **bukan** rebuild 11-field.
- Session: **satu otoritas** = hook `MAIN_SESSION_ID`. Tak rekonsiliasi 3-sumber.
- opencode.json: `role` diturunkan dari command di kode; route `execute` dibuang (domain main_agent).

**Command split:**
```
LOCAL (main_agent langsung):    /.execute -y /.init /.refactor /.commit /.review /.compress /.memory /.caveman /.local /.help
DELEGATED (1-call run script):  /.explore /.plan /.analyze /.verify /.sweep /.doctor
```

---

## STEP 0 — Deteksi agent & tentukan path

Cek berurutan, stop di sinyal valid pertama.

| # | Sinyal | Agent | AGENT_DIR | CONFIG_FILE |
|---|--------|-------|-----------|-------------|
| 1 | `~/.claude/` | Claude Code | `~/.claude/` | `CLAUDE.md` |
| 2 | `~/.codex/` / `CODEX_*` | Codex | `~/.codex/` | `AGENTS.md` |
| 3 | `~/.cursor/` | Cursor | `~/.cursor/` | `rules/workflow.md` |
| 4 | `~/.windsurf/` | Windsurf | `~/.windsurf/` | `rules/workflow.md` |
| 5 | `~/.gemini/` | Gemini CLI | `~/.gemini/` | `GEMINI.md` |
| 6 | `~/.github-copilot/` | Copilot | `~/.github-copilot/` | `instructions.md` |
| 7 | `~/.config/opencode/` | OpenCode | `~/.config/opencode/` | `AGENTS.md` |
| 8 | none | Unknown | `~/.workflow/` | `WORKFLOW.md` |

```text
[AGENT DETECTED]
agent: <nama> | AGENT_DIR: <path> | CONFIG_FILE: <path> | confidence: high|medium|low
```
Resolve semua `{AGENT_DIR}`/`{CONFIG_FILE}` ke nilai nyata sebelum tulis. Unknown → tanya "Pakai `~/.workflow/`? (yes/no)".

---

## STEP 1 — Prereq bootstrap

Tool `agent-workflow` butuh path ke `main.py` untuk **init pertama**. Setelahnya `.workflow/config.json` simpan path absolut → `$AGENT_PATH` tak wajib lagi.

- Cek `AGENT_PATH` (Windows `$env:AGENT_PATH` / POSIX `echo $AGENT_PATH`).
- Set → `[PREREQ] OK: <path>`, lanjut.
- Belum set → tawarkan set (`SetEnvironmentVariable`/`export`), atau jalankan `python <path>/main.py --command init --work-dir <project>` manual sekali. Lanjut? (yes/no).

---

## STEP 2 — Idempotency check

`{AGENT_DIR}/skills/` ada? Marker `<!-- WORKFLOW-MAIN-AGENT:START -->` di `{CONFIG_FILE}`?

```text
[SETUP STATUS] mode: fresh | update | skills_found: <list|none> | config_marker: found|not found
```
mode=update → "Setup ditemukan. UPDATE: skill overwrite, memory dipertahankan, config di-merge. Lanjut? (yes/no)". fresh → STEP 3.

---

## STEP 3 — Buat/update skill files

Buat `{AGENT_DIR}/skills/`. Skill = template → SELALU overwrite. Substitusi `{AGENT_DIR}` ke nilai nyata.

**Pola bersama semua skill DELEGATED (explore/analyze/sweep/doctor/plan-evidence):**
1 panggilan `run` script. Session otomatis (hook). Hasil `{ok, content, meta, digest}`. `ok:false` → tampilkan `meta.error_type` + `meta.next_action`, ikuti/STOP. Relay `digest`; buka `content` kalau butuh detail.

---

### FILE: {AGENT_DIR}/skills/explore.md

    # Skill: explore
    description: Codebase evidence via second_agent (1-call). Fallback: /.local.

    ## Trigger
    /.explore <hint>

    ## Run (1-call)
    Windows:   & "<work_dir>\.workflow\run.ps1" explore "<hint>"
    mac/linux: "<work_dir>/.workflow/run.sh" explore "<hint>"
    - Blocking sampai selesai. Return JSON {ok, content, meta, digest}.
    - Session otomatis (hook MAIN_SESSION_ID). Tak karang command, tak check.py, tak AGENT_PATH.
    - .workflow belum ada / run script hilang → /.init dulu. Atau /.local on (fallback lokal).
    - ok:false → tampilkan meta.error_type + meta.next_action → ikuti next_action atau STOP.

    ## Output
    Relay digest: summary, key_findings, risk_level, recommended_next_action, confidence.
    Butuh detail → buka content (contract_detail). JANGAN rebuild field manual.
    digest absen (fallback) → ringkas content sendiri: entry_points, related_modules, uncertainties.

    ## End
    "Lanjut /.plan, atau cukup?"

---

### FILE: {AGENT_DIR}/skills/plan.md

    # Skill: plan
    description: Structured planning — evidence (1-call) + reasoning main_agent. Confidence + decision gate.

    ## Trigger
    /.plan <task>

    ## STEP 1 — Evidence
    Reuse LAST_EXPLORE_RESULT jika ada di context. Else 1-call:
      Windows:   & "<work_dir>\.workflow\run.ps1" plan "<task>"
      mac/linux: "<work_dir>/.workflow/run.sh" plan "<task>"
    - ok:false → tampilkan error_type + next_action → tawarkan lanjut tanpa evidence (yes/no) atau STOP.
    - [LOCAL_MODE]=true → skip run, pakai /.local flow sebagai evidence.

    ## STEP 2 — Output [PLAN]
    Bangun dari digest + content. Confidence + uncertainties WAJIB. Field kosong → tulis alasan.

    [PLAN]
    task:            <restatement>
    evidence_source: second_agent (1-call) | graphify+claude (local) | none
    assumptions:     - <statement, bukan pertanyaan> | (tidak ada: alasan)
    open_questions:  - <max 5, hanya keputusan arch/impl yg tak bisa diasumsikan> | (tidak ada: alasan)
    steps:           1. <concrete> 2. <concrete>
    files_affected:  <list>
    risks:           - <breakage/side-effect> | (tidak ada: alasan)
    confidence:
      problem_understanding: low|medium|high — <alasan>
      root_cause:            low|medium|high — <alasan>
      solution_path:         low|medium|high — <alasan>
    uncertainties:   - <tak terkonfirmasi> | (tidak ada)
    decision:        proceed | clarify (open_questions blocking) | re-explore (root_cause rendah)

    ## STEP 3 — Tunggu approval. JANGAN auto-execute.
    "Setuju? Jalankan /.execute -y"

---

### FILE: {AGENT_DIR}/skills/analyze.md

    # Skill: analyze
    description: Deep analysis via second_agent (1-call). --local: Claude only.

    ## Trigger
    /.analyze <topic>          → 1-call
    /.analyze --local <topic>  → Claude langsung (skip proxy)

    ## Run (1-call)
    Windows:   & "<work_dir>\.workflow\run.ps1" analyze "<topic>"
    mac/linux: "<work_dir>/.workflow/run.sh" analyze "<topic>"
    Reuse LAST_EXPLORE_RESULT jika relevan. ok:false → error_type + next_action.
    --local atau [LOCAL_MODE]=true → skip run, /.local flow.

    ## Output [ANALYSIS RESULT]
    Relay digest + isi dari content. confidence (3 sub) + uncertainties WAJIB.
    source: second_agent (1-call) | claude (local)
    confidence: { problem_understanding, root_cause, solution_path } — masing low|medium|high — <alasan>
    findings: <dari content> | (kosong: alasan)
    implications: <dampak> | (kosong: alasan)
    uncertainties: <tak terkonfirmasi> | (tidak ada)

    ## Rules: zero code changes, zero file mods.

---

### FILE: {AGENT_DIR}/skills/sweep.md

    # Skill: sweep
    description: Git diff scan → impact evidence (1-call). Fallback: git diff langsung.

    ## Trigger
    /.sweep

    ## Run (1-call)
    Windows:   & "<work_dir>\.workflow\run.ps1" sweep "scan git diff, identify impact"
    mac/linux: "<work_dir>/.workflow/run.sh" sweep "scan git diff, identify impact"
    ok:false / run script hilang → fallback: `git diff HEAD`, `git status` langsung, source: claude (direct).

    ## Output [SWEEP RESULT]
    Relay digest. changed_files | impact | risks | uncertainties (kosong → alasan).

    ## End
    "Impact selesai. Lanjut /.verify atau /.plan?"

---

### FILE: {AGENT_DIR}/skills/doctor.md

    # Skill: doctor
    description: .workflow readiness check (1-call). Fallback: local check.

    ## Trigger
    /.doctor

    ## Run (1-call)
    Windows:   & "<work_dir>\.workflow\run.ps1" doctor "check .workflow readiness"
    mac/linux: "<work_dir>/.workflow/run.sh" doctor "check .workflow readiness"
    run script hilang → fallback local: .workflow/ ada? .gitignore ignore .workflow/? config.json valid? graphify-out/ ada?

    ## Output [DOCTOR REPORT]
    Relay digest + checks. status: READY | NEEDS SETUP. actions bila ada masalah.
    NEEDS SETUP → "Jalankan /.init".

---

### FILE: {AGENT_DIR}/skills/execute.md

    # Skill: execute
    description: Controlled implementation dengan approval gate.

    ## Trigger
    /.execute -y → PROCEED | /.execute → GATE only

    ## Gate
    Tanpa -y → output [EXECUTION SCOPE] → "Tambah -y untuk konfirmasi" → STOP.

    ## Pre-Execution
    [EXECUTION SCOPE] allowed: <files> | forbidden: <files> | reason: <batasan>

    ## During
    ONLY sentuh allowed. Butuh forbidden → STOP → report conflict → minta instruksi.

    ## Post
    [EXECUTION RESULT] files_changed | confidence | uncertainties | status: done|partial|blocked
    → Auto-trigger /.verify. JANGAN declare done sebelum /.verify selesai.

---

### FILE: {AGENT_DIR}/skills/verify.md

    # Skill: verify
    description: 3-step verification — logic, falsification, reality.

    ## Trigger
    /.verify (auto setelah /.execute -y atau /.refactor)

    ## Protocol
    1. Logic: solve problem? assumptions valid? konsisten pola codebase? → PASS/FAIL + reason
    2. Falsification: kondisi gagal? edge case? malformed input? → list
    3. Reality: test suite → run → simulate → "not executable". Actual vs expected.

    ## Output
    [VERIFICATION] logic: PASS|FAIL — <reason> | failure: <list> | reality: <actual>|not executable | verdict: DONE|NEEDS FIX
    NEEDS FIX → fix → re-run /.verify. JANGAN output final sebelum done.

---

### FILE: {AGENT_DIR}/skills/refactor.md

    # Skill: refactor
    description: Structural improvement — zero behavior change.

    ## Trigger
    /.refactor <scope>

    ## Rules
    Struktural ONLY, behavior TIDAK BERUBAH. Jangan expand scope.
    [REFACTOR SCOPE] scope | allowed | forbidden | goal
    Post: `graphify update` → auto-trigger /.verify.

---

### FILE: {AGENT_DIR}/skills/commit.md

    # Skill: commit
    description: Generate commit message (Conventional Commits). Local.

    ## Trigger
    /.commit

    ## Execution
    1. `git status` + `git diff --staged`. Tak ada staged → "[COMMIT] Stage dulu: git add <files>", STOP.
    2. Tentukan type (feat|fix|refactor|chore|docs|test|perf|style|build|ci), scope, subject (≤50, imperative, no period), body (hanya jika why non-obvious).
    3. Output [COMMIT MESSAGE] block. "Jalankan? (yes/no)" → yes: `git commit -m`. JANGAN commit tanpa konfirmasi.

---

### FILE: {AGENT_DIR}/skills/memory.md

    # Skill: memory
    description: Propose memory update ke personal knowledge files.

    ## Trigger
    /.memory <note>

    ## Execution
    1. Evaluasi: berdampak keputusan masa depan? ownership/arch baru? recurring landmine?
    2. [MEMORY PROPOSAL] file | action (add|update|overwrite) | content. "Confirm? (yes/no/edit)"
    3. yes → write + update {AGENT_DIR}/memory/MEMORY.md index. no → discard. edit → tunggu koreksi.
    4. Append SESSION_LOG.md: [tanggal] task | domain | memory written.
    JANGAN write memory mid-session tanpa konfirmasi.

---

### FILE: {AGENT_DIR}/skills/help.md

    # Skill: help
    description: Command reference v3.3.0

    ## Trigger
    /.help

    ## Output
    [COMMAND GUIDE — v3.3.0]

    LOCAL (main_agent langsung):
      /.execute -y      implement code (wajib -y)
      /.init            buat/regenerate .workflow/ (scripts, opencode.json, config abs-path)
      /.refactor <s>    structural, zero behavior change
      /.commit          commit message (Conventional Commits)
      /.review <f>      one-line per issue review
      /.compress <f>    compress prose ke caveman
      /.memory <note>   simpan insight
      /.caveman [lite|full|ultra]  toggle compression
      /.local [on|off|status]      toggle no-proxy
      /.help            panduan ini

    DELEGATED (1-call .workflow/run script → second_agent):
      /.explore <hint>  evidence gathering
      /.plan <task>     evidence + rencana terstruktur
      /.analyze <topic> deep analysis | --local: Claude only
      /.verify          3-step verification (auto setelah /.execute)
      /.sweep           git diff impact
      /.doctor          .workflow readiness

    [WORKFLOW] /.explore → /.plan → /.execute -y → /.verify
    [SESSION CACHE] LAST_EXPLORE_RESULT → /.plan,/.analyze | LAST_PLAN_RESULT → /.execute | LAST_EXECUTE_DIFF → /.verify,/.sweep
    Prefix "/." wajib. Tanpa prefix → INVALID.

---

### FILE: {AGENT_DIR}/skills/caveman.md

    # Skill: caveman
    description: Token compression — default ultra untuk respons non-code.

    ## Status
    ALWAYS ON sejak pesan pertama. Off: "normal mode" | "stop caveman".

    ## Rules
    Drop artikel/filler/pleasantries/hedging. Fragments OK, synonyms pendek.
    Pattern: [thing] [action] [reason]. [next step].
    Code/paths/commands/file names TIDAK BERUBAH. Structured block labels TETAP.
    Levels: ultra (default) | full | lite | normal.

---

### FILE: {AGENT_DIR}/skills/local.md

    # Skill: local
    description: Toggle no-proxy. Claude mirror second_agent via graphify + Read/Glob/Grep. Affects /.explore /.plan /.analyze.

    ## Trigger
    /.local [on|off|status] (tanpa arg = toggle)

    ## State
    [LOCAL_MODE] = true (no-proxy, graphify-mirrored) | false (default, 1-call proxy). Reset false tiap session baru.

    ## Graphify-Mirrored Protocol ([LOCAL_MODE]=true, untuk explore/plan/analyze)
    1. Load structure: graphify-out/ (graph.json/nodes/edges) atau Glob("**/*") fallback.
    2. Scope: filter nodes relevan, shortlist max 10 file (entry points → callers → deps).
    3. Deep dive: Read + Grep per file, trace dep penting, stop saat confidence cukup.
    4. Output IDENTIK format skill terkait (source: graphify+claude (local)) agar interop /.plan.
    Read/Glob/Grep DIIZINKAN saat local mode (exception Global Forbidden).

    ## Output toggle
    [LOCAL MODE — ON|OFF] Proxy: off|on | Coverage: /.explore /.plan /.analyze | Graphify: active|glob

    ## Rules
    Zero code/file changes. /.execute & /.memory tak terpengaruh.

---

## STEP 4 — Memory files

Buat `{AGENT_DIR}/memory/`. Memory = data user → JANGAN overwrite jika ada.

- `PERSONAL_MEMORY.md` (SKIP jika ada): Architecture Decisions, Module Ownership, Known Landmines, Patterns, Things I Forget.
- `DOMAIN_MAP.md` (SKIP jika ada): Entry Points, Cross-Team Boundaries, Dead Code Suspects.
- `SESSION_LOG.md` (SKIP jika ada): log per session.
- `MEMORY.md` (UPDATE index, append jika belum ada):
    - [Personal Memory](PERSONAL_MEMORY.md) — arch decisions, ownership, landmines
    - [Domain Map](DOMAIN_MAP.md) — entry points, boundaries, dead code
    - [Session Log](SESSION_LOG.md) — aktivitas per session

---

## STEP 5 — Tulis/merge config file (MANAGED BLOCK — dikirim tiap turn, jaga ramping)

Target `{CONFIG_FILE}`. Marker ada → ganti antara START/END. Tidak ada → append. Substitusi `{AGENT_DIR}`.

    <!-- WORKFLOW-MAIN-AGENT:START — v3.3.0, do not edit manually -->

    ## Workflow Main Agent — v3.3.0

    role: orchestrator + user interface + direct executor. Kamu BUKAN second_agent.
    second_agent: OpenCode (read-only evidence), dipanggil via .workflow/run script.

    ### Identity & Behavior
    - Interface user↔agent. Route perintah, delegasi evidence, synthesize, eksekusi aksi write.
    - Concise. Direct. Single user. Never assume, never expand scope silently.
    - Caveman ultra DEFAULT dari pesan pertama (off: "normal mode"). Code/paths exact.
    - WAJIB output hasil setelah evidence. Tidak boleh diam.

    ### Session (satu otoritas)
    MAIN_SESSION_ID dari blok [SESSION BINDING] hook (STEP 5b) — AUTHORITATIVE, override semua.
    Hook absent → fallback .workflow/state.json (root match) → else generate main_<slug>_<ts>.
    Jangan reuse session lintas project root. Detail lifecycle: skill/hook, bukan sini.

    ### Delegated commands — 1-call (NON-NEGOTIABLE)
    Panggil: .workflow/run.ps1 (Windows) | .workflow/run.sh (mac/linux) <command> "<task>".
    - Blocking, return {ok, content, meta, digest}. Session otomatis. Tak karang command, tak check.py, tak $AGENT_PATH.
    - ok:false → baca meta.error_type + meta.next_action → ikuti next_action. Jangan retry buta, jangan lanjut synthesis.
    - Relay `digest` (summary, key_findings, risk_level, recommended_next_action, confidence). Buka `content` HANYA jika butuh detail. JANGAN rebuild field manual.
    - .workflow/run script hilang → /.init. Proxy gagal → tawarkan /.local (fallback lokal).

    ### Command registry
    LOCAL:     /.execute -y /.init /.refactor /.commit /.review /.compress /.memory /.caveman /.local /.help
    DELEGATED: /.explore /.plan /.analyze /.verify /.sweep /.doctor
    NL map: cek logic→analyze | flow→explore | tambah fitur→plan | implement→execute -y | impact→sweep | readiness→doctor | catat→memory | tanpa proxy→local on.
    Prefix "/." wajib. Command tanpa "/." → INVALID, jangan interpret.

    ### Plan/analysis output (structured)
    WAJIB: confidence {problem_understanding, root_cause, solution_path} (low|medium|high — alasan) + uncertainties.
    Field kosong → tetap tampilkan + alasan. Bangun dari digest+content, bukan struktur evidence mentah.

    ### Execution rules
    /.execute -y: ada plan aktif (LAST_PLAN_RESULT) → edit HANYA execution scope → auto /.verify → jangan declare done sebelum verify. Jangan commit kecuali user minta.
    /.init: .workflow/run.ps1 init (atau python main.py --command init) → regenerate scripts + config. Ensure .gitignore punya .workflow/.

    ### Session cache (valid dalam MAIN_SESSION_ID + project root sama)
    LAST_EXPLORE_RESULT→plan,analyze | LAST_PLAN_RESULT→execute | LAST_EXECUTE_DIFF→verify,sweep | LAST_SWEEP_RESULT→context.

    ### Graphify
    Cek graphify-out/ sebelum codebase task. Ada → primary context. Tidak ada → offer .graphifyignore + `graphify update`.
    Never run: graphify init/build/watch. Auto `graphify update` setelah code change. Error "too large"/"too many nodes" → IGNORE.

    ### Global Forbidden
    Modif file luar scope | /.execute tanpa -y | plan tanpa confidence+uncertainties | auto-expand scope |
    claim success sebelum verify | lanjut synthesis saat ok:false | delegate /.execute atau /.init ke second_agent |
    reuse session lintas project | interpret "/" tanpa "." | write memory mid-session tanpa konfirmasi | ignore [LOCAL_MODE].

    <!-- WORKFLOW-MAIN-AGENT:END -->

---

## STEP 5b — Session-binding hook (Claude Code only)

HANYA jika agent (STEP 0) = Claude Code. Agent lain → SKIP, output:
"[SESSION HOOK] Agent <nama> belum punya padanan SessionStart hook. Fallback: state.json + context."

### 5b.1 — Buat `{AGENT_DIR}/hooks/session-bind.ps1` (Windows — overwrite)

    # session-bind.ps1 — SessionStart hook. Maps chat lifecycle → MAIN_SESSION_ID.
    #   startup|clear|compact → NEW | resume → REUSE. Registry: ~/.claude/session_registry.json. Never blocks (exit 0).
    $ErrorActionPreference = 'Stop'
    function Write-NoBom([string]$Path,[string]$Content){ $enc=New-Object System.Text.UTF8Encoding($false); [System.IO.File]::WriteAllText($Path,$Content,$enc) }
    try {
        $raw=[Console]::In.ReadToEnd(); if([string]::IsNullOrWhiteSpace($raw)){exit 0}
        $p=$raw|ConvertFrom-Json; $source=$p.source; $sid=$p.session_id; $cwd=$p.cwd
        if([string]::IsNullOrWhiteSpace($cwd)){$cwd=(Get-Location).Path}
        $root=$cwd; try{$root=(Resolve-Path -LiteralPath $cwd -ErrorAction Stop).Path}catch{}
        $slug=Split-Path -Leaf $root
        $reg="$env:USERPROFILE\.claude\session_registry.json"; $r=@{}
        if(Test-Path $reg){try{$j=Get-Content $reg -Raw|ConvertFrom-Json; foreach($x in $j.PSObject.Properties){$r[$x.Name]=$x.Value}}catch{$r=@{}}}
        $reuse=$false; $mid=$null
        if($source -eq 'resume' -and $sid -and $r.ContainsKey($sid)){$mid=$r[$sid].main_session_id; $reuse=$true}
        if([string]::IsNullOrWhiteSpace($mid)){$ts=Get-Date -Format 'yyyyMMdd_HHmmssfff'; $rnd=-join(((48..57)+(97..102))|Get-Random -Count 4|%{[char]$_}); $mid="main_${slug}_${ts}_${rnd}"}
        $bt=(Get-Date).ToUniversalTime().ToString('o')
        if($sid){$r[$sid]=[PSCustomObject]@{main_session_id=$mid; cwd=$root; bound_at=$bt; source=$source}}
        try{$rows=foreach($k in $r.Keys){[PSCustomObject]@{key=$k;val=$r[$k]}}; $keep=$rows|Sort-Object {try{[datetime]$_.val.bound_at}catch{[datetime]::MinValue}} -Descending|Select-Object -First 50; $pr=@{}; foreach($e in $keep){$pr[$e.key]=$e.val}; $r=$pr}catch{}
        Write-NoBom $reg (($r|ConvertTo-Json -Depth 6))
        $verb=if($reuse){'REUSE (continue)'}else{'NEW'}
        $ctx="[SESSION BINDING - authoritative]`nMAIN_SESSION_ID=$mid`nMAIN_SESSION_PROJECT_ROOT=$root`nsource=$source`nsecond_agent_thread=$verb`nUse this MAIN_SESSION_ID for all delegated commands. Overrides .workflow/state.json session.id."
        Write-Output (([PSCustomObject]@{hookSpecificOutput=[PSCustomObject]@{hookEventName='SessionStart'; additionalContext=$ctx}})|ConvertTo-Json -Depth 5 -Compress)
        exit 0
    } catch { exit 0 }

> Saat tulis ke file: hapus indentasi 4-spasi. (Indentasi di prompt = penanda code block.)

**POSIX (mac/linux) — `{AGENT_DIR}/hooks/session-bind.sh`** bila harness non-Windows dukung SessionStart. Paritas logika: baca stdin JSON, map source, kelola registry, inject `[SESSION BINDING]`. Bila harness tak dukung → fallback state.json (perilaku tetap jalan).

### 5b.2 — Register di `{AGENT_DIR}/settings.json` (MERGE)

    "hooks": { "SessionStart": [ { "matcher": "startup|resume|clear|compact",
      "hooks": [ { "type": "command", "command": "powershell -NoProfile -ExecutionPolicy Bypass -File \"{AGENT_DIR}\\hooks\\session-bind.ps1\"" } ] } ] }

Catatan: `compact` → thread BARU. autoCompact reset thread mid-session. Reuse: ganti `$source -eq 'resume'` → `$source -in 'resume','compact'`.

---

## STEP 6 — Verifikasi
1. List `{AGENT_DIR}/skills/` + byte size (konfirmasi skill ramping)
2. List `{AGENT_DIR}/memory/` + byte size
3. 5 baris pertama `{CONFIG_FILE}` (marker ada)
4. Byte size managed block (target < ~4KB — jaga ramping)
5. (Claude Code) `session-bind.ps1` ada + `settings.json` punya `hooks.SessionStart`

---

## STEP 7 — Final Report

    [SETUP COMPLETE — v3.3.0 DUAL AGENT MODE]
    agent: <nama> | dir: {AGENT_DIR} | config: {CONFIG_FILE} | mode: fresh|update

    Skills (overwritten, ringan): explore plan analyze sweep doctor execute verify refactor commit memory help caveman local
    Memory (preserved): PERSONAL_MEMORY DOMAIN_MAP SESSION_LOG MEMORY(index)
    Config: {CONFIG_FILE} (managed block v3.3.0, ramping) | Session hook: session-bind.ps1 (Claude Code) | .sh (POSIX bila didukung)

    Interface: .workflow/run.<ps1|sh> <command> "<task>" → {ok, content, meta, digest}
    Workflow:  /.explore → /.plan → /.execute -y → /.verify
    Status: READY
