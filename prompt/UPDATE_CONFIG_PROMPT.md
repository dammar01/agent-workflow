
# Update Config Prompt

Gunakan prompt ini saat perlu memperbarui global config OpenCode dan template prompt `agent-workflow` agar sinkron.

Sumber kebenaran utama: `~/.config/opencode/AGENTS.md` yang aktif. Jika template `agent-workflow` drift, samakan wording/aturan inti ke config aktif itu.

## Scope Update Wajib

1. Perbarui `~/.config/opencode/AGENTS.md`.
2. Perbarui `E:\Work\project\agent-workflow\prompt\v3.0.1.md`.
3. Jaga agar keduanya sinkron untuk aturan berikut:
   - wording inti mengikuti `AGENTS.md` aktif
   - definisi `READ-ONLY`
   - `Plan Mode Recognition`
   - `Graphify Missing Protocol`
   - aturan bahwa `[WORKFLOW_AGENT]` **tidak** diinject oleh OpenCode saat membuat command

## Perubahan Yang Harus Ada

### 1. Definisi READ-ONLY

- `READ-ONLY` = dilarang write/install/mutate state.
- `READ-ONLY` **bukan** larangan untuk `glob`, `grep`, `read`, `Test-Path`, dan observasi read-only lain.
- Tambahkan larangan eksplisit untuk edit file, install dependency, config/env mutation, commit, network side effect, dan perubahan state lain.

### 2. Plan Mode Recognition

- Tambahkan section khusus yang mengenali signal `plan mode`, `read-only phase`, atau instruksi user seperti `jangan execute dulu`.
- Saat mode ini aktif:
  - lakukan evidence gathering read-only
  - jangan mutate state
  - jangan keluarkan hasil berbasis asumsi

### 3. Graphify Missing Protocol

- Jika `graphify-out/` tidak ada dan mode read-only aktif:
  - jangan buat `.graphifyignore`
  - tampilkan template `.graphifyignore` sebagai teks
  - minta user menjalankan `graphify update`
  - jika task tidak harus hard graphify-first, boleh lanjut dengan direct code inspection
- Jika mode write aktif:
  - boleh buat `.graphifyignore`
  - tetap arahkan user untuk `graphify update`

### 4. `[WORKFLOW_AGENT]`

- Tegaskan bahwa `[WORKFLOW_AGENT]` adalah concern internal python side.
- OpenCode **tidak** inject tag itu saat compose command/prompt.

## Output Requirement

- Setelah update, laporkan file mana yang berubah.
- Ringkas perubahan sinkronisasi antar file.
- Sebutkan jika ada drift yang ditemukan antara template dan config aktif, lalu nyatakan sudah diselaraskan ke config aktif.
