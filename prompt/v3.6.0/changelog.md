# Changelog — v3.6.0

Satu tema besar dan satu kecil. Yang besar: verifikasi lewat browser sungguhan, sebagai
command terpisah `/.verify-browser`, bukan mode baru `/.verify`. Yang kecil: sisi premium
akhirnya bisa diukur dari transcript Claude Code, bukan hanya sisi second_agent.

## Status rilis

Nomor sudah di-stamp dan manifest sudah regenerasi; `stamp_version --check` dan
`gen_manifest --check` lolos. Gerbang `RELEASE.md` langkah 5 dijalankan semuanya, ditambah smoke
browser yang khusus rilis ini:

| Gerbang | Hasil |
|---|---|
| `python tests/run.py` | `scenario` PASS (42 s); smoke dilewati tanpa flag, sesuai desain |
| `python tools/e2e/e2e.py` | 137 passed, 0 failed, 1 skipped (delegated, opt-in `--full`) |
| `WORKFLOW_E2E_SMOKE=1 python tests/run.py --only e2e-smoke` | PASS (49,6 s) — Chromium sungguhan, Playwright 1.60.0, lawan `tests/fixtures/e2e_app/`, termasuk POST read-only yang dikonfirmasi |
| `python tools/e2e/e2e.py --full` | 143 passed, 0 failed, 0 skipped (101 s) — opencode sungguhan (`openrouter/deepseek/deepseek-v4-flash-0731`) |
| ganti provider di satu sesi, provider sungguhan | codex lalu opencode di sesi `a2-switch-…`: opencode membuka thread sendiri (`ses_…`), thread codex tetap tersimpan terpisah, nol `Session not found` |

Batas yang tetap berlaku: `--full` membuktikan `explore` dan `sweep` terhadap provider nyata,
bukan tahap `draft` dan review `/.verify-browser`. Kedua tahap itu diuji dengan provider palsu
dan player palsu; smoke membuktikan player dan guard di Chromium nyata, tanpa second_agent.

**Tag.** v3.6.0 adalah tag pertama sejak `v3.5.1`. v3.5.2 dan v3.5.3 ditulis, di-stamp dan
dicatat, tetapi **tidak pernah di-tag dan tidak akan di-tag**: isinya dirilis bersama tag ini.
Siapa pun yang memasang dari tag melompat langsung dari 3.5.1 ke 3.6.0, jadi catatan
`prompt/v3.5.2/` dan `prompt/v3.5.3/` tetap bagian dari yang diterima. Untuk menelusuri tree
yang dimaksud nomor lama, commit terakhir yang masih membawa nomor itu adalah `e7a2ded` (3.5.2;
bump ada di `5617dbd`, tetapi statusline yang dicatat changelog 3.5.2 baru masuk sesudahnya) dan
`7771baa` (3.5.3) — referensi, bukan rilis yang bisa dipasang.

## `/.verify-browser`

Alur per run: wawancara user di main_agent → draft spec oleh second_agent (`e2e_spec`, route
internal) → user konfirmasi → player Playwright lokal menjalankan skenario → review hasil oleh
second_agent → `[VERIFICATION]` kanonik. Vonis fail-closed: browser `fail` tetap `fail` apa pun
kata reviewer; browser `pass` dengan reviewer tak tersedia menjadi `incomplete`, bukan `pass`.

- **Tanpa config.** Semua setting hidup di request per sesi
  (`.workflow/sessions/<session>/e2e/request.json`). Key tak dikenal atau tipe salah ditolak
  sebagai `request_invalid`, bukan fallback diam-diam.
- **Credential hanya sebagai nama.** Skenario memakai `${NAMA}`; nilainya dari satu profil
  `.workflow/e2e/secrets.json` (`{"default": ..., "profiles": {"qa": {...}, "admin": {...}}}`)
  atau environment, tak pernah lewat chat, prompt, atau artifact. Request memilih profil
  dengan nama (`secrets_profile`), jadi mencoba akun lain = run lain dengan nama lain;
  beberapa profil tanpa `default` dan tanpa pilihan ditolak, tidak ditebak. Draft membuat
  file itu dengan slot kosong bila belum ada — sebelumnya tak ada apa pun yang membuatnya,
  sehingga user yang diminta "mengisi" tak punya titik mulai dan run berakhir `env_missing`.
  Error menyebut profil, posisi key, atau baris JSON, tak pernah nilai.
  Nilai yang ter-resolve di-scrub dari event dan artifact teks (raw, URL-encoded, HTML- dan
  JSON-escaped, rekursif di direktori `e2e/`); run yang memakai credential tidak mengambil
  screenshot maupun trace, karena capture biner tak bisa di-scrub.
- **Guard navigasi.** Navigasi top-level yang keluar dari origin `base_url` (atau
  `allowed_origins`) diblokir sebagai `navigation_blocked`.
- **Guard tulis.** Selama `allow_side_effects` false, player membatalkan setiap request selain
  `GET`/`HEAD`/`OPTIONS` ke origin mana pun, kecuali endpoint-nya cocok persis dengan entri
  `allowed_mutation_paths`. Write yang ditolak di dalam step menggagalkan step itu sebagai
  `mutation_blocked` (harness → `incomplete`); beacon atau write di luar step menjadi observasi.
  Batasnya dinyatakan terbuka: `GET` yang mengubah data, pesan WebSocket, dan request service
  worker tidak terlihat.
- **POST yang hanya membaca.** Search, filter, atau query GraphQL sering berupa POST. Tahap
  draft kini boleh mengusulkannya di `read_only_requests` dengan bukti handler `path:line`
  yang benar-benar ada (usulan tanpa bukti membuat draft `invalid`); user mengonfirmasi per
  entri, dan hanya yang tertulis di `allowed_read_only_requests` yang lolos. Body tetap
  dibaca: GraphQL `mutation`/`subscription`, persisted query, atau body yang tak terbaca
  sebagai teks tetap diblok. Yang lolos dilaporkan sebagai observasi
  `read_only_request_allowed` dengan hitungan, bukan diam-diam.
- **Klasifikasi kegagalan** `app | harness | unknown`. `unknown` tak pernah dipromosikan
  menjadi `harness`.
- **Test yang sudah ada** bisa ikut membuktikan klaim lewat `existing_test_command` (argv tanpa
  shell, allowlist file).
- **Metrik.** Setiap run menambah satu baris `kind: e2e_run` di `quality.jsonl`;
  `--command report` menurunkan bagian `e2e` darinya.

### Perhatikan: `allowed_mutation_paths` dengan URL penuh

Entri `/path` terikat ke origin `base_url`. Entri `http(s)://host/path` **mengizinkan write ke
origin lain** — itu disengaja (login ke host auth terpisah), tapi berarti entri itu memberi
wewenang tulis lintas origin. Tulis hanya endpoint yang memang dikonfirmasi user.

### Dependensi opsional

Playwright dipin di `requirements-e2e.txt` dan hanya dipasang oleh
`python install.py --apply --with-e2e` (pip, lalu `playwright install chromium`). Runtime tetap
stdlib-only; `/.doctor` melaporkan `e2e_readiness` dari metadata paket saja, tak pernah
blocking. Browser dan URL diperiksa di preflight tiap run.

### Migrasi

Rancangan awal memakai `commands.verify_mode = "e2e"` dan blok `commands.e2e`. Keduanya
**tidak pernah dirilis dan tidak berlaku**: `verify_mode` hanya menerima `delegated` dan
`syntax` (nilai lain memberi warning lalu jatuh ke `delegated`), dan `commands.e2e` dilaporkan
sebagai key tak dikenal. Workspace yang sempat menulisnya dari build dev cukup menghapus blok
itu dan memakai `/.verify-browser`.

## Transcript sisi premium

`core/audit/transcript.py` membaca transcript Claude Code di `~/.claude/projects/` menjadi
jumlah turn manusia, turn sampai edit pertama, dan total token konteks per sesi. Hanya membaca,
tak pernah menulis. Tiga aturan parsing: schema milik vendor jadi setiap akses defensif; usage
dijumlahkan per pesan, tak pernah diambil yang terakhir; turn user hanya yang benar-benar
diketik manusia (tool result, notifikasi, dan teks sistem yang disuntik harness tidak dihitung).
Record sidechain subagent dihitung terpisah, tidak dibuang.

- Hook `session-bind` kini merekam `transcript_path` yang diberikan Claude Code, sehingga
  transcript sesi bisa ditemukan tanpa menebak nama direktori.
- `project_slug` men-slug path yang sudah absolut apa adanya. Sebelumnya path Windows yang dibaca
  di Linux di-resolve sebagai relatif dan mendapat prefix direktori kerja, menghasilkan slug yang
  tak menunjuk direktori mana pun.
- Konsumen: `bench/observe.py` (metrik *observasi* sesi nyata — tidak diacak, tidak dipasangkan,
  dan dilaporkan sebagai observasi, bukan kausalitas), `bench/driver.py`, `bench/aggregate.py`.

## Thread second_agent per provider

Record sesi dulu menyimpan satu id thread, siapa pun penerbitnya. Setelah `/.provider`
mengganti codex ke opencode di tengah sesi, panggilan berikutnya menyerahkan id thread codex
ke opencode untuk dilanjutkan, dan gagal dalam dua detik (`Session not found`) — terekam di
`usage.jsonl` sebelum perbaikan ini ada. Kini `provider_sessions` menyimpan thread per
provider; executor mengikat record ke provider terpilih tepat sebelum setiap panggilan,
jadi provider baru membuka thread sendiri dan kembali ke provider lama melanjutkan thread
lamanya. `provider_session_id` tetap ada sebagai entri provider aktif untuk semua pembaca
yang sudah memakainya. Record lama tanpa peta diadopsi sekali berdasarkan bentuk id (opencode
`ses_…`); yang tak cocok dibuang dan thread baru dibuat. `/.doctor` kini membaca store sesi
project-local yang sebenarnya dipakai runtime, bukan fallback global.

## Arti `~` di statusline

`~` di statusline dimaksudkan sebagai "angka ini estimasi". Nyatanya ia menyala begitu ada
satu baris yang tidak dihitung provider — termasuk panggilan yang gagal sebelum provider
menghitung apa pun (rate limit, resume yang ditolak). Satu kegagalan membuat sesi yang
seluruh panggilan suksesnya terukur tampak "tidak terukur", dan terbaca sebagai "provider ini
tidak bisa diukur". Panggilan gagal tanpa hitungan provider kini tampil sebagai `N failed` di
samping jumlah calls dan tidak masuk total maupun Saved; `~` tetap menyala untuk panggilan
sukses yang hanya punya estimasi. Kedua flavor (`.ps1`, `.sh`) diubah bersama dan diuji
(`tests/checks/statusline.py`).

## Perbaikan alat rilis

`tools/maintain/stamp_version.py --check` gagal di CI karena pola semver `\d+\.\d+\.\d+` membaca
`127.0.0.1` di skill `verify-browser` sebagai versi "127.0.0". Pola kini dibatasi di kedua sisi
(`(?<![\d.])\d+\.\d+\.\d+(?!\.?\d)`). Lookahead untuk `.digit` saja tidak cukup: digit terakhir
bisa mundur, sehingga `10.10.10.10` masih terbaca "10.10.1". Test regresi baru
`tests/checks/stamp_version.py` (suite `stamp-version`) mengunci kedua arah: alamat IP tidak
cocok, bentuk versi yang di-stamp tetap cocok, dan `--check` lolos di tree.

## Yang tidak diverifikasi

- Guard tulis bersifat method-only (lihat di atas); `GET` yang mengubah data tidak tertangkap.
- Write asinkron bisa tercatat pada step sesudah step yang memicunya.
- Entri `allowed_mutation_paths` berupa URL penuh dengan port yang salah bentuk lolos validasi
  request; di runtime ia gagal tertutup (tak pernah cocok), bukan terbuka.
- Adopsi id thread lama hanya bisa membedakan opencode dari yang lain: id codex dan agy sama-sama
  tanpa prefix, jadi record lama milik salah satunya bisa ditawarkan sekali ke yang lain.
- Deteksi GraphQL membaca bentuk body yang umum (JSON, batch, form, raw). Operasi yang
  disamarkan di luar bentuk itu tidak terbaca; pengamannya tetap konfirmasi user atas bukti
  handler.
- **Perbandingan token codex vs opencode belum dilakukan.** Task identik dijalankan di kedua
  provider pada sesi baru masing-masing, tetapi codex menjawab `rate_limited` di kedua
  percobaan, sehingga tidak ada baris codex terukur untuk dibandingkan. Sisi opencode terukur
  (`token_source: provider`): input 35.452 termasuk cache 16.128, output 989 termasuk reasoning
  170 — semantik yang sama dengan codex (cache di dalam input, reasoning di dalam output)
  menurut adapter dan fixture, belum menurut pengukuran berdampingan. Ulangi saat kuota codex
  tersedia.
