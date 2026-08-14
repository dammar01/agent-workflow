# Skill: provider
description: Pilih second_agent (provider → model → effort) lewat pertanyaan interaktif, lalu terapkan.

## Trigger
/.provider

## Batas
LOCAL. Nol second_agent, nol job, nol lock. Runtime yang menulis config — kamu TIDAK pernah menulis
`.workflow/second_agent.json` sendiri. Kamu cuma merender pilihan dan meneruskan satu string.

## STEP 1 — Baca katalog
    Windows:   & "<work_dir>\.workflow\run.ps1" provider "" "<MAIN_SESSION_ID>"
    mac/linux: "<work_dir>/.workflow/run.sh" provider "" "<MAIN_SESSION_ID>"

Balasannya `meta.providers[]` (`name`, `installed`, `detail`, `models[] {id, efforts}`) plus
`meta.current` (provider/model/effort yang aktif) dan `meta.source`.

`.workflow/run.*` MISSING → /.init dulu. Jangan panggil `python main.py` langsung.

## STEP 2 — Tanya, tiga langkah berurutan
Renderer = AskUserQuestion, satu pertanyaan per call, jawaban sebelumnya memotong opsi berikutnya.

1. **Provider** — opsi dari `providers[]`. `installed:false` → tetap tampilkan, tulis di `description`
   bahwa CLI-nya tak ada di PATH; jangan jadikan opsi utama.
2. **Model** — opsi dari `models[]` provider terpilih. Lebih dari 4 → tampilkan 4 paling relevan;
   user selalu punya "Other" untuk mengetik id lain.
3. **Effort** — opsi dari `efforts` MILIK MODEL yang barusan dipilih, bukan milik provider.
   Nilai berbeda per model, jadi memakai daftar provider akan menawarkan yang upstream tolak.
   - `efforts` KOSONG (`[]`) → model itu tak menerima effort sama sekali. LEWATI pertanyaan
     ketiga, langsung apply tanpa field effort. Memaksakan nilai akan ditolak runtime.
   - Model dari "Other" → `efforts` tak diketahui; tanyakan effort sebagai pertanyaan terbuka
     dan sebutkan bahwa nilainya tak bisa divalidasi.

Batas keras tool: MAX 4 pertanyaan per call, 2-4 opsi per pertanyaan, `header` MAX 12 karakter.
Jangan bikin opsi "lainnya" sendiri — "Other" sudah otomatis.

## STEP 3 — Terapkan
    & "<work_dir>\.workflow\run.ps1" provider "<provider>|<model>|<effort>" "<MAIN_SESSION_ID>"

Pemisah `|` — bukan `/`, karena id model opencode mengandung `/`.
Model atau effort dikosongkan → field itu di-clear, provider kembali ke default-nya sendiri.

Runtime menulis lima field sekaligus (`provider`, `provider_command`, `provider_agent`,
`default_model`, `effort`) dan menyinkronkan hint `runtime.second_agent` di config.json.
`ok:false` → NOL yang ditulis; relay `content` + `meta.next_action` apa adanya, jangan coba
memperbaiki sendiri, jangan menulis file.

## Output
[PROVIDER]
selected: <provider> / <model | provider default> / effort <effort | provider default>
installed: <daftar provider terpasang>
written: <lima field dari meta.written>
config_hint: <updated true|false + alasan>
foreign_values: <isi meta.foreign_values, atau "kosong">  — TIDAK kosong = config menyebut provider
  lain di `provider_command`/`provider_agent`/model; tampilkan penuh, jangan diringkas hilang.
warnings: <meta.warnings, atau "tidak ada">
status: APPLIED | REFUSED

REFUSED → sebut alasan + next_action, jangan tawarkan lanjut ke command lain.
APPLIED → efeknya mulai panggilan delegated berikutnya; job yang sedang jalan tak berubah.
