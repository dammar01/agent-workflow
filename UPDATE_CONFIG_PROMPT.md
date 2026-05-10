# UPDATE_CONFIG_PROMPT.md

Template 1-shot prompt untuk update konfigurasi global OpenCode setelah setiap perubahan behavior.

## Usage

Copy block di bawah, isi `[CHANGELOG_ENTRY]`, lalu kirim ke OpenCode.

---

```text
[CONFIG UPDATE REQUEST]

Target file: E:\Work\project\agent-workflow\OPENCODE_GLOBAL_CONFIG_V2.md

Changelog entry:
[CHANGELOG_ENTRY]

Instruksi:
1. Baca OPENCODE_GLOBAL_CONFIG_V2.md secara keseluruhan.
2. Terapkan perubahan dari changelog entry di atas.
3. Pastikan konsistensi dengan section lain (Startup Protocol, Graphify Rules, Global Forbidden, dll).
4. Jika ada section baru, taruh di tempat yang logis.
5. Jika ada section lama yang conflict, resolve dengan preferensi changelog entry.
6. Setelah edit selesai, tampilkan diff ringkas (section yang berubah saja).
7. Jangan tanya konfirmasi — langsung eksekusi.

Format output akhir:
```text
[CONFIG UPDATED]
file: OPENCODE_GLOBAL_CONFIG_V2.md
changes:
- <section>: <what changed>
- <section>: <what changed>

confidence: low | medium | high
```
```

---

## Contoh Changelog Entry

### Contoh 1: Menambahkan rule baru

```text
[CHANGELOG_ENTRY]
Tambahkan rule di Global Forbidden:
"Jalankan query ke production database tanpa WHERE clause atau tanpa backup."

Tambahkan di Permission Gate:
"Database query di environment production: wajib EXPLAIN + backup snapshot."
```

### Contoh 2: Mengubah Startup Protocol

```text
[CHANGELOG_ENTRY]
Ubah Startup Protocol step 2:
Dari: "Cek graphify-out/ di project root"
Jadi: "WAJIB cek graphify-out/ di project root sebelum eksplorasi. Jika ada, baca GRAPH_REPORT.md dahulu. Jangan asumsikan tidak ada tanpa verifikasi."
```

### Contoh 3: Menambahkan workflow command baru

```text
[CHANGELOG_ENTRY]
Tambahkan workflow command `/.deploy <environment>` di Command Registry V2.
Trigger: deploy ke staging/production.
Rules: wajib permission gate, wajib verify tests pass, wajib backup DB.
```

### Contoh 4: Update model default

```text
[CHANGELOG_ENTRY]
Update Agent-Workflow Model Override table:
- explore: ganti dari kimi-k26 ke kimi-k2.6
- plan: ganti dari cc-sonnet45 ke claude-sonnet-4-5
```

---

## Checklist Sebelum Kirim Prompt

- [ ] Changelog entry spesifik (section, before, after).
- [ ] Tidak ambigu — satu interpretasi saja.
- [ ] Scope terbatas (max 3 section per update).
- [ ] Tidak conflict dengan Global Forbidden.

---

Last updated: 2026-05-10
