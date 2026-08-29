# agent-workflow

Bikin Claude Code berhenti membakar context (dan tagihan) untuk membaca kode.

Baca kode dan pencarian di codebase didelegasikan ke agent lain yang jauh lebih murah. Claude Code hanya menerima ringkasannya, lalu berpikir dan menulis kode.

[Dokumentasi teknis lengkap →](docs/reference.md)

---

## Daftar isi

1. [Apa ini](#1-apa-ini)
2. [Kenapa dipakai](#2-kenapa-dipakai)
3. [Untuk siapa](#3-untuk-siapa)
4. [Efektif untuk apa](#4-efektif-untuk-apa)
5. [Instalasi](#5-instalasi)
6. [Lisensi](#6-lisensi)

---

## 1. Apa ini

`agent-workflow` adalah runtime yang duduk **di antara dua AI agent** dan mengatur pembagian kerja di antara keduanya.

| Peran | Siapa | Tugasnya |
|---|---|---|
| **main_agent** | Claude Code (atau Codex, Cursor) | Berpikir, mengambil keputusan, **satu-satunya yang boleh menulis file** |
| **second_agent** | OpenCode / Codex / Agy — model murah | Membaca dan mencari di codebase, **read-only**, tidak pernah menulis |

Idenya sederhana: dari semua pekerjaan yang Claude Code lakukan, sebagian besar bukan "berpikir". Itu **membaca file, grep, menelusuri siapa memanggil siapa** — pekerjaan mekanis yang tidak butuh model mahal. Pekerjaan itulah yang dipindahkan.

### Alurnya

```text
Kamu bertanya ke Claude Code
        ↓
Claude Code memanggil satu script:  .workflow/run.ps1 explore "cari entry point auth"
        ↓
Runtime merakit prompt terstruktur, menjalankan agent murah
        ↓
Agent murah membaca 40 file, menelusuri import, mengumpulkan bukti
        ↓
Runtime memvalidasi bentuk jawabannya, menyimpan bukti ke disk
        ↓
Claude Code menerima RINGKASAN + anchor file:line — bukan isi 40 file itu
        ↓
Claude Code berpikir dan menulis kode
```

Yang dikembalikan selalu berbentuk sama, jadi Claude Code tidak perlu menebak:

```json
{ "ok": true, "content": "...", "meta": {}, "digest": {} }
```

Tanpa dependency pihak ketiga. Seluruh runtime memakai standard library Python.

---

## 2. Kenapa dipakai

### Alasan 1 — menekan biaya

Membaca kode adalah pekerjaan bervolume tinggi bernilai rendah. Menelusuri satu modul bisa berarti membaca puluhan file. Dilakukan Claude Code, tiap file itu masuk sebagai input token dengan tarif premium.

Dipindahkan ke agent murah, biaya pekerjaan yang sama turun drastis — dan sebagian provider punya model gratis. Claude Code tetap mengerjakan bagian yang memang butuh dia: analisis sebab-akibat, keputusan desain, menulis kode.

### Alasan 2 — dan ini yang sering lebih penting: menjaga context

Claude Code punya jendela context terbatas. Semakin penuh jendela itu dalam satu sesi, semakin turun performanya:

- detail dari awal percakapan mulai terlewat;
- instruksi awal tergeser oleh isi file yang menumpuk;
- sesi berakhir lebih cepat karena context habis, dan kamu harus memulai dari nol.

Membaca 40 file secara langsung bisa menghabiskan puluhan ribu token — dan **isi file itu tinggal di context sampai sesi berakhir**, padahal yang kamu butuhkan cuma tiga baris darinya.

Dengan pola ini, yang masuk ke context Claude Code cuma digest dan anchor `file:line`. Bukti lengkapnya disimpan ke disk di `.workflow/`, dan dibuka hanya kalau memang dibutuhkan.

> **Efeknya:** satu sesi Claude Code bertahan jauh lebih lama, dan kualitasnya tidak merosot di tengah jalan.

### Alasan 3 — jawaban yang bisa diaudit

Tiap klaim dari agent murah wajib membawa anchor `file:line`. Runtime memeriksa bentuk jawabannya; jawaban yang berhenti sebelum kontrak selesai diminta ulang **satu kali**, lalu ditandai gagal. Tidak ada retry tanpa akhir.

Bukti disimpan sebagai artifact permanen, jadi pertanyaan yang sama di sesi berikutnya bisa dilayani dari disk tanpa memanggil provider lagi.

---

## 3. Untuk siapa

Cocok kalau kamu:

- **memakai Claude Code sebagai code agent utama** setiap hari, bukan sesekali;
- **bekerja di codebase besar** — ratusan sampai ribuan file, di mana "cari di mana fungsi ini dipakai" berarti menyapu banyak direktori;
- sering **kehabisan context di tengah sesi** dan terpaksa mengulang penjelasan;
- ingin biaya per sesi turun tanpa menurunkan kualitas keputusan.

Kurang cocok kalau:

- codebase-mu kecil (di bawah ~50 file) — Claude Code bisa memuat semuanya sendiri, lapisan ini cuma menambah rumit;
- kamu tidak mau memasang CLI agent kedua di mesinmu;
- kamu butuh jawaban instan — tiap panggilan terdelegasi butuh puluhan detik sampai beberapa menit, karena agent murah benar-benar membaca kode.

---

## 4. Efektif untuk apa

Paling terasa pada pekerjaan yang **luas** — banyak file, banyak titik sentuh:

| Pekerjaan | Contoh pertanyaan | Command |
|---|---|---|
| **Memetakan kode asing** | "Di mana logic autentikasi?" "Alur request dari route sampai DB gimana?" | `explore` |
| **Mencari sebab** | "Kenapa endpoint ini lambat?" "Aman gak kalau kolom ini dihapus?" | `analyze` |
| **Menyusun rencana** | "Mau tambah fitur X, langkahnya apa saja?" | `plan` |
| **Blast radius** | "Perubahan di working tree ini menyentuh apa saja?" | `sweep` |
| **Membuktikan hasil** | "Yang tadi dikerjakan sudah benar belum?" | `verify` |

Kurang efektif untuk perubahan satu file yang sudah kamu tahu letaknya. Kalau kamu sudah tahu file dan barisnya, minta Claude Code langsung mengeditnya — jangan lewat lapisan ini.

> **Menulis kode tidak pernah didelegasikan.** Agent murah hanya membaca. Semua perubahan file tetap dikerjakan Claude Code, di bawah pengawasanmu. Ini disengaja, bukan keterbatasan.

---

## 5. Instalasi

### 5.1 Syarat

| Kebutuhan | Wajib? | Catatan |
|---|---|---|
| **Python 3.10+** | Wajib | Nol dependency — cukup Python-nya saja |
| **CLI agent kedua** | Wajib | Pilih satu: `opencode` (disarankan), `codex`, atau `agy` — harus ada di `PATH` |
| **git** | Disarankan | Dipakai `sweep`, mode verify `syntax`, dan penjaga workspace |
| **Claude Code** | Disarankan | Ini main_agent yang dituju. Agent lain juga bisa |

Cek dulu sebelum lanjut:

```bash
python --version      # harus 3.10 atau lebih baru
opencode --version    # atau: codex --version / agy --version
git --version
```

> **Pilih `opencode` kalau ragu.** Dari tiga provider, hanya OpenCode yang punya penegakan izin sungguhan — agent murahnya dilarang menulis file, dilarang membaca `.env` dan file kunci, dan perintah shell-nya dibatasi ke git read-only. Pada `codex` dan `agy`, batas itu tidak ditegakkan mesin. Rinciannya di [dokumentasi provider](docs/reference.md#memilih-provider--dan-konsekuensi-keamanannya).

### 5.2 Langkah instalasi

Empat langkah. Langkah 1–3 sekali per mesin, langkah 4 sekali per project.

#### Langkah 1 — ambil tool-nya

```bash
git clone <repo-url>
cd agent-workflow
```

Simpan folder ini di tempat permanen. Runtime akan menyimpan path absolutnya; memindahkannya nanti berarti menjalankan `upgrade` ulang.

#### Langkah 2 — pasang config agent global

Lihat dulu apa yang akan diubah, tanpa menulis apa pun:

```bash
python install.py
```

Kalau sudah cocok, baru terapkan:

```bash
python install.py --apply
```

Ini memasang config Claude Code (skill, hook) dan **blok izin untuk agent murah** — larangan tulis/edit dan allowlist perintah shell. Langkah ini yang membuat batas keamanan aktif; melewatkannya berarti agent murah berjalan tanpa larangan tulis.

#### Langkah 3 — beritahu letak `main.py`

Runtime perlu tahu di mana tool-nya untuk init pertama.

**Windows (permanen, jalankan sekali):**

```powershell
[Environment]::SetEnvironmentVariable("AGENT_PATH", "C:/path/to/agent-workflow/main.py", "User")
```

Tutup terminal, buka lagi supaya aktif.

**macOS / Linux (permanen):**

```bash
echo 'export AGENT_PATH="$HOME/path/to/agent-workflow/main.py"' >> ~/.bashrc   # atau ~/.zshrc
source ~/.bashrc
```

Cek:

```bash
python "$AGENT_PATH" --help
```

```powershell
python $env:AGENT_PATH --help
```

#### Langkah 4 — aktifkan di project kamu

Jalankan sekali untuk tiap project yang mau dipakai:

```bash
python "$AGENT_PATH" --command init --work-dir /path/to/project-kamu --pretty
```

```powershell
python $env:AGENT_PATH --command init --work-dir "C:/path/to/project-kamu" --pretty
```

Yang dibuat di project kamu:

- `.workflow/config.json` — pengaturan, plus path absolut ke tool
- `.workflow/second_agent.json` — pilihan provider dan model, boleh kamu ubah
- `.workflow/run.*`, `inspect.*`, `check.*` — script pintu masuk (`.ps1` di Windows, `.sh` di POSIX)
- `opencode.json` — daftar file rahasia yang dilarang dibaca
- `.workflow/` otomatis masuk `.gitignore`

> **`.workflow/` jangan di-commit.** Script di dalamnya memanggang path absolut mesin tempat `init` dijalankan. Tiap anggota tim menjalankan langkah 4 sendiri.

### 5.3 Pastikan sudah benar

```bash
python install.py --check
```

```bash
cd /path/to/project-kamu
.workflow/run.sh doctor          # Windows: .workflow\run.ps1 doctor
```

`doctor` harus melaporkan **`READY`** dengan nol issue. Kalau `NOT_READY`, baca `recommended_fixes` — itu berarti ada pintu masuk yang benar-benar rusak, bukan sekadar catatan gaya.

### 5.4 Pemakaian pertama

Claude Code memanggilnya sendiri lewat bahasa natural:

```text
kamu:  di mana logic autentikasi di project ini?

Claude Code:  [INTENT] explore — pertanyaan lokasi
              (menjalankan .workflow/run.ps1 explore "...")
              ...
```

Mau memanggil manual? Bisa:

```bash
.workflow/run.sh explore "cari entry point autentikasi" "<SESSION_ID>"
```

```powershell
& ".workflow\run.ps1" explore "cari entry point autentikasi" "<SESSION_ID>"
```

Argumen ketiga adalah session id, dan **wajib diisi** — tanpa itu dua sesi bisa saling menimpa state.

### 5.5 Command yang tersedia

| Command | Jalan di mana | Untuk apa |
|---|---|---|
| `init` | lokal | Aktifkan di satu project |
| `upgrade` | lokal | Segarkan workspace setelah tool diperbarui |
| `doctor` | lokal | Cek kesiapan, tulis laporan |
| `sweep` | lokal | Pindai perubahan di working tree |
| `clean` | lokal | Bersihkan job, fakta usang, sesi lama |
| `explore` | terdelegasi | Peta kode, entry point, pemilik |
| `analyze` | terdelegasi | Analisis sebab, nol perubahan kode |
| `plan` | terdelegasi | Rencana langkah berbasis bukti |
| `verify` | terdelegasi | Buktikan hasil kerja |

Daftar lengkap termasuk job asinkron: [docs/reference.md](docs/reference.md#command).

### 5.6 Kalau ada masalah

| Gejala | Kemungkinan sebab | Tindakan |
|---|---|---|
| `doctor` bilang `NOT_READY` | script atau config melenceng | `.workflow/run.sh upgrade` |
| `run script drift` | tool diperbarui, script belum | `upgrade` dari mesin itu |
| Provider tidak ditemukan | CLI agent tak ada di `PATH` | Pasang, lalu `provider --version` |
| Command gagal setelah repo dipindah | path absolut sudah basi | `upgrade` dari lokasi baru |

---

## 6. Lisensi

Apache License 2.0 — open source. Teks lengkap ada di [LICENSE](LICENSE).

Artinya, singkatnya:

- **boleh** dipakai, diubah, dan didistribusikan, termasuk untuk keperluan komersial;
- **boleh** dipakai di produk tertutup — kode turunanmu tidak wajib ikut open source;
- **wajib** menyertakan notice hak cipta dan salinan lisensi ini;
- **wajib** menandai file yang kamu ubah;
- **dapat hibah paten eksplisit** dari kontributor, dan hibah itu berakhir bila kamu menuntut paten atas karya ini;
- **tanpa garansi** — dipakai atas risiko sendiri.

Ringkasan ini bukan nasihat hukum; yang mengikat adalah teks di [LICENSE](LICENSE).

---

## Bacaan lanjutan

| Dokumen | Isi |
|---|---|
| [docs/reference.md](docs/reference.md) | Referensi teknis lengkap: schema config, job asinkron, fact store, evidence reuse, mode verify, sesi, test |
| [CHANGELOG.md](CHANGELOG.md) | Riwayat perubahan tiap versi |
| [RELEASE.md](RELEASE.md) | Prosedur rilis |
| [docs/reference.md#batasan-yang-diketahui](docs/reference.md#batasan-yang-diketahui) | Batasan yang diketahui — dibaca sebelum mengandalkan runtime ini untuk jaminan keamanan |
