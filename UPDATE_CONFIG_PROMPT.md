# Update Config Prompt — Evidence-Only vs Reasoning Rule

**Tanggal:** 2026-05-10  
**Perubahan:** Klarifikasi evidence-only constraint hanya berlaku saat ada `[WORKFLOW_AGENT]` tag.

---

## Ringkasan Perubahan

**Sebelum:**

- Evidence-only constraint diterapkan terlalu luas, termasuk saat agent utama memproses response dari agent-workflow.
- Agent utama tidak melakukan reasoning layer setelah menerima evidence dari agent-workflow.
- Output planning hanya berisi evidence block (findings + implications) tanpa reasoning narrative atau plan konkret.

**Sesudah:**

- **Evidence-only constraint hanya berlaku untuk agent-workflow** (yang dipanggil dengan `[WORKFLOW_AGENT]` tag).
- **Agent utama (OpenCode) WAJIB melakukan reasoning layer** setelah menerima evidence dari agent-workflow.
- Output final ke user: **evidence + reasoning + plan/analysis konkret**.

---

## Aturan Baru

### 1. Evidence-Only Scope

**Evidence-only hanya berlaku saat ada `[WORKFLOW_AGENT]` tag dalam prompt.**

- Jika prompt mengandung `[WORKFLOW_AGENT]` → agent-workflow dibatasi: evidence-only, no reasoning beyond evidence.
- Jika prompt TIDAK mengandung `[WORKFLOW_AGENT]` → agent bebas reasoning penuh.

### 2. Agent Utama (OpenCode) Responsibility

Saat agent utama invoke agent-workflow:

1. **Invoke** agent-workflow dengan `[WORKFLOW_AGENT]` tag.
2. **Terima** response JSON dengan evidence block.
3. **Parse** evidence dari `content` field.
4. **Lakukan reasoning layer sendiri**:
   - Analisis evidence.
   - Trade-off analysis.
   - Bottleneck assessment.
   - Risk evaluation.
   - Syntesis plan konkret atau analisis mendalam.
5. **Output** final ke user: evidence + reasoning + plan/analysis.

### 3. Workflow Skill Command (Contoh: `/.plan`)

**Step-by-step:**

1. User: `/.plan buat fitur adaptive escalation`
2. OpenCode invoke agent-workflow:
   - Command: `python $env:AGENT_PATH -c plan -p "..." -s "..." --pretty`
   - Prompt mengandung `[WORKFLOW_AGENT]` tag.
3. Agent-workflow return:
   - Evidence block: findings, implications, uncertainties.
   - NO reasoning narrative.
4. OpenCode terima response:
   - Parse evidence.
   - **Lakukan reasoning layer:**
     - Kenapa findings X adalah bottleneck?
     - Trade-off antara approach A vs B?
     - Risiko regresi jika refactor Y?
     - Solusi konkret step-by-step.
5. OpenCode output final:
   ```text
   [PLAN]
   
   [REASONING]
   Evidence menunjukkan ChatService 4.029 baris monolithic.
   Refactor langsung = risiko regresi tinggi karena pipeline stages tracked di Redis.
   Trade-off: decorator pattern (low intrusion) vs extract service (clean separation).
   Pilihan: decorator pattern untuk adaptive escalation, extract HistoryPruner untuk context optimization.
   
   [STEPS]
   1. Buat ModelRouter decorator yang wrap resolveModel()
   2. Inject latency tracking ke Redis markStage()
   3. Buat HistoryPruner service untuk dynamic history pruning
   ...
   
   confidence: high
   uncertainties:
   - Belum dilihat apakah BangAIService microservice expose latency metadata
   ```

### 4. Natural Prompt (No Skill Command)

**Contoh:** User: "cek logic login"

- OpenCode boleh pilih:
  - **Invoke agent-workflow** → ikuti workflow di atas (evidence → reasoning → output).
  - **Langsung lokal** → reasoning penuh tanpa batasan evidence-only.

---

## Implementasi di Config

Tambahkan section baru di `~/.config/opencode/AGENTS.md`:

```markdown
## Evidence-Only vs Reasoning Rule

**Evidence-only constraint hanya berlaku saat ada `[WORKFLOW_AGENT]` tag dalam prompt.**

### When `[WORKFLOW_AGENT]` present (invoke agent-workflow):

- Agent-workflow dibatasi: evidence-only, no reasoning beyond evidence.
- Output dari agent-workflow berformat evidence block.
- Agent utama (OpenCode) yang membaca response wajib:
  - Parse evidence block dari `content` field JSON response.
  - **Lakukan reasoning layer sendiri** berdasarkan evidence yang diterima.
  - Syntesis plan konkret atau analisis mendalam dengan reasoning narrative.
  - Output final ke user: evidence + reasoning + plan/analysis.

### When no `[WORKFLOW_AGENT]` (standalone/local execution):

- Agent bebas melakukan reasoning penuh.
- Tidak ada batasan evidence-only.
- Boleh langsung syntesis plan, analisis, atau solusi dengan reasoning mendalam.
```

---

## Checklist Verification

Setelah update config:

- [ ] `~/.config/opencode/AGENTS.md` mengandung section "Evidence-Only vs Reasoning Rule"
- [ ] Section menjelaskan evidence-only hanya untuk `[WORKFLOW_AGENT]`
- [ ] Section menjelaskan agent utama wajib reasoning layer setelah terima evidence
- [ ] Skill files (`plan.md`, `analyze.md`) updated dengan STEP reasoning layer
- [ ] Test `/.plan` command → output harus ada reasoning narrative + plan konkret

---

## Migration Note

**Untuk session yang sudah jalan:**

- Tidak perlu regenerate session ID.
- Config baru berlaku untuk invoke berikutnya dalam session yang sama.
- Agent utama langsung apply reasoning layer saat baca evidence dari agent-workflow.

**Untuk setup baru:**

- Jalankan `OPENCODE_GLOBAL_CONFIG_V2.md` setup dengan file ini sebagai reference.
- Verify section "Evidence-Only vs Reasoning Rule" ada di `AGENTS.md`.

---

**Status:** READY  
**Action Required:** Update `~/.config/opencode/AGENTS.md` dengan section baru di atas.
