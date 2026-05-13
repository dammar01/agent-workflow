# Update Config Prompt: v3.0.0 → v3.0.1

Prompt untuk update konfigurasi global OpenCode dari v3.0.0 ke v3.0.1.

---

## Changelog v3.0.1

**Breaking Changes:**
- Hard caps per-block **DIHAPUS** — output scale to complexity
- Reasoning **DINAMIS** — menyesuaikan scope, tidak rigid
- Multi-layer check **disederhanakan** — L1-L5 pertama kali, L1-L2 session berikutnya
- **WAJIB output hasil** setelah eksplorasi/analisis/plan — tidak boleh diam

**Rationale:**
- Hard caps 6 items/10 baris terlalu sempit untuk subsystem kompleks
- Reasoning rigid menghambat analisis mendalam
- Multi-layer check berlebihan untuk invocation session lanjutan
- Agent sering "membaca tanpa output" — user menunggu tanpa hasil

---

## Update Instructions

Jalankan update berikut pada file konfigurasi global Anda.

### 1. Update `~/.config/opencode/AGENTS.md`

#### 1.1 Core Behavior — Tambah output wajib, hapus hard caps

**FIND:**
```markdown
- Default output: caveman ultra dengan hard caps per-block (lihat skills/caveman.md).
- Sertakan confidence + uncertainties untuk plan/analysis formal atau saat risk tinggi.
- Boleh edit file saat user jelas meminta perubahan. Untuk aksi sensitif, wajib izin eksplisit.
```

**REPLACE WITH:**
```markdown
- Default output: caveman ultra — telegraphic, drop filler. No hard caps; scale to complexity.
- Sertakan confidence + uncertainties untuk plan/analysis formal atau saat risk tinggi.
- Boleh edit file saat user jelas meminta perubahan. Untuk aksi sensitif, wajib izin eksplisit.
- **WAJIB output hasil setelah eksplorasi/analisis/plan selesai. Tidak boleh diam tanpa hasil.**
```

---

#### 1.2 Output Style — Ganti hard caps dengan reasoning dinamis

**FIND:**
```markdown
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
```

**REPLACE WITH:**
```markdown
## Output Style — Caveman Ultra (Default, Real Injection)

Caveman ultra **bukan hanya plugin output style**. Di V3, caveman adalah **execution policy** di-inject ke setiap skill workflow.

Detail lengkap rules + sub-skills: lihat `~/.config/opencode/skills/caveman.md`.

**Caveman ultra WAJIB aktif di SEMUA jalur:**

- Jalur python (AGENT_PATH valid + python invoke).
- Jalur fallback (no AGENT_PATH, agent utama eksekusi sendiri).
- Dengan atau tanpa `[WORKFLOW_AGENT]` tag.
- Untuk evidence output, reasoning output, dan plan output.

**Reasoning Dinamis:**

- Reasoning **menyesuaikan kedalaman dan struktur** berdasarkan:
  - Kompleksitas scope — subsystem sederhana = reasoning singkat, kompleks = reasoning mendalam
  - Tujuan task — eksplorasi, analisis, atau plan = format reasoning berbeda
  - Evidence yang ditemukan — jika banyak edge case atau uncertainty, reasoning harus refleksikan itu
  - Kebutuhan user — tidak dipaksakan ke template rigid
- **Tidak ada hard caps.** Output scale to complexity.
- Reasoning adalah **alat**, bukan **beban**. Gunakan seperlunya.

Sub-skills: `/.commit`, `/.review`, `/.compress`.

Switch mode jika perlu (plugin-level): `/caveman lite | full | ultra`.

Off toggle (per session): "normal mode" atau "stop caveman" → balik ke verbose lite.
```

---

#### 1.3 Multi-layer Check Rules — Eksplisit L1-L5 pertama, L1-L2 berikutnya

**FIND:**
```markdown
### Rules

- Jalankan semua 5 layer check sebelum setiap invocation pertama dalam session. Untuk invocation berikutnya dalam session yang sama, cukup Layer 1-2.
- Jangan hardcode path script. Selalu baca dari env.
- Jangan modify env variable dari dalam skill atau command.
- Jangan inject `[WORKFLOW_AGENT]` ke prompt python.
- Invocation yang mengirim data ke external API tetap wajib Permission Gate.
```

**REPLACE WITH:**
```markdown
### Rules

- **Pertama kali dalam session: jalankan semua L1-L5 check.**
- **Invocation berikutnya dalam session yang sama: cukup L1-L2.**
- Jangan hardcode path script. Selalu baca dari env.
- Jangan modify env variable dari dalam skill atau command.
- Jangan inject `[WORKFLOW_AGENT]` ke prompt python.
- Invocation yang mengirim data ke external API tetap wajib Permission Gate.
```

---

#### 1.4 Structured Output Rule — Hapus caveman caps mention

**FIND:**
```markdown
Untuk jawaban cepat, bug kecil, atau task sederhana, format ini opsional.

Caveman caps tetap berlaku: `uncertainties` max 5 items, 80 char/line.
```

**REPLACE WITH:**
```markdown
Untuk jawaban cepat, bug kecil, atau task sederhana, format ini opsional.
```

---

#### 1.5 OpenCode Side (reading response) — Reasoning dinamis

**FIND:**
```markdown
### OpenCode Side (reading response)

Saat menerima response JSON dari python:

1. **WAJIB tunggu parse field `ok`** sebelum proses dianggap selesai.
2. `ok: false` → output error dari `content`, STOP.
3. `ok: true` → `content` adalah evidence/result. Lakukan:
   - **Untuk `/.explore`**: treat hasil workflow sebagai deliverable utama. Lakukan spot-check minimal hanya bila ada mismatch kuat. **Set `LAST_EXPLORE_RESULT` di context cache.**
   - **Untuk `/.plan`, `/.analyze`**: anggap reasoning utama datang dari workflow. OpenCode hanya tambah consistency check, koreksi bila perlu, dan summary ringkas sesuai caps caveman ultra. **Set `LAST_PLAN_RESULT` (untuk plan).**
   - **Untuk `/.execute`**: set `LAST_EXECUTE_DIFF` di context (untuk reuse oleh `/.audit`).
   - **Untuk `/.audit`**: lakukan **REASONING LAYER** untuk prioritize findings.
4. Reasoning layer OpenCode untuk evidence commands harus ringan: max 10 baris, telegraphic, fokus mismatch/risk only.
```

**REPLACE WITH:**
```markdown
### OpenCode Side (reading response)

Saat menerima response JSON dari python:

1. **WAJIB tunggu parse field `ok`** sebelum proses dianggap selesai.
2. `ok: false` → output error dari `content`, STOP.
3. `ok: true` → `content` adalah evidence/result. Lakukan:
   - **Untuk `/.explore`**: treat hasil workflow sebagai deliverable utama. Lakukan spot-check minimal hanya bila ada mismatch kuat. **Set `LAST_EXPLORE_RESULT` di context cache.**
   - **Untuk `/.plan`, `/.analyze`**: anggap reasoning utama datang dari workflow. OpenCode hanya tambah consistency check, koreksi bila perlu, dan summary ringkas. **Set `LAST_PLAN_RESULT` (untuk plan).**
   - **Untuk `/.execute`**: set `LAST_EXECUTE_DIFF` di context (untuk reuse oleh `/.audit`).
   - **Untuk `/.audit`**: lakukan **REASONING LAYER** untuk prioritize findings.
4. Reasoning layer OpenCode untuk evidence commands harus dinamis sesuai scope complexity, tidak rigid.
```

---

#### 1.6 Fallback Mode — Dynamic reasoning

**FIND:**
```markdown
### Fallback Mode (no AGENT_PATH)

Saat user setuju lanjut tanpa AGENT_PATH (hanya untuk evidence commands):

- Tidak ada python call → tidak ada `[WORKFLOW_AGENT]` tag.
- OpenCode lakukan **gabungan**: evidence gathering + reasoning langsung sebagai fallback penuh.
- WAJIB tetap pakai `graphify-out/` sebagai struktur awal.
- Caveman ultra tetap aktif dengan caps yang sama.
- Output tidak wajib pakai format `[EVIDENCE]` block — ringkas, ikut caps.
- Tetap set context cache (`LAST_EXPLORE_RESULT`, dst) untuk reuse skill berikutnya.
- Sebelum takeover lokal karena hasil workflow lemah atau ambigu, default-nya minta workflow second-pass dulu dengan prompt lebih sempit + prior evidence, kecuali ada blocker jelas.
```

**REPLACE WITH:**
```markdown
### Fallback Mode (no AGENT_PATH)

Saat user setuju lanjut tanpa AGENT_PATH (hanya untuk evidence commands):

- Tidak ada python call → tidak ada `[WORKFLOW_AGENT]` tag.
- OpenCode lakukan **gabungan**: evidence gathering + reasoning langsung sebagai fallback penuh.
- WAJIB tetap pakai `graphify-out/` sebagai struktur awal.
- Caveman ultra tetap aktif.
- Output tidak wajib pakai format `[EVIDENCE]` block — ringkas, dynamic reasoning.
- Tetap set context cache (`LAST_EXPLORE_RESULT`, dst) untuk reuse skill berikutnya.
- Sebelum takeover lokal karena hasil workflow lemah atau ambigu, default-nya minta workflow second-pass dulu dengan prompt lebih sempit + prior evidence, kecuali ada blocker jelas.
```

---

#### 1.7 Global Forbidden — Cleanup caps mention

**FIND:**
```markdown
- Output verbose/bertele-tele — caveman ultra dengan caps selalu aktif.
```

**REPLACE WITH:**
```markdown
- Output verbose/bertele-tele — caveman ultra selalu aktif.
```

**FIND:**
```markdown
- Skip cek graphify-out/ di mode fallback evidence commands — graphify-out tetap wajib.
- **Output reasoning melebihi caps caveman ultra** (lihat skills/caveman.md tabel caps).
- **Skip exploration cache reuse** — jika `LAST_EXPLORE_RESULT` ada di session, WAJIB pass sebagai PRIOR_EVIDENCE ke `/.plan` dan `/.analyze`.
```

**REPLACE WITH:**
```markdown
- Skip cek graphify-out/ di mode fallback evidence commands — graphify-out tetap wajib.
- **Skip exploration cache reuse** — jika `LAST_EXPLORE_RESULT` ada di session, WAJIB pass sebagai PRIOR_EVIDENCE ke `/.plan` dan `/.analyze`.
```

---

### 2. TIDAK PERLU update `~/.config/opencode/skills/caveman.md`

Skill caveman.md tetap berisi hard caps table sebagai **reference** untuk compression style, bukan enforcement.

V3.0.1 reasoning dinamis override hard caps untuk agent output, tapi skill file tetap relevan untuk sub-skill `/.commit`, `/.review`, `/.compress`.

---

## Verification

Setelah update:

1. Baca ulang `~/.config/opencode/AGENTS.md` untuk verifikasi perubahan tersimpan
2. Cek tidak ada typo atau syntax markdown rusak
3. Test dengan prompt: `"Initialize session. Reply READY."` lalu jalankan eksplorasi sederhana

Expected behavior:
- Agent output hasil eksplorasi tanpa diam
- Reasoning menyesuaikan scope (tidak fixed 10 baris)
- Session berikutnya hanya check L1-L2 (tidak L1-L5 ulang)

---

## Rollback

Jika ada masalah, restore dari backup:

```bash
cp ~/.config/opencode/AGENTS.md.bak ~/.config/opencode/AGENTS.md
```

Atau copy ulang dari `E:\Work\project\agent-workflow\prompt\v3.0.0.md` (STEP 2).
