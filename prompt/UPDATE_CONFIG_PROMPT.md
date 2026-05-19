# v3.1.2 OpenCode Config — Incremental Update Prompt

> Paste prompt ini ke agent yang akan meng-update global config yang SUDAH berada di v3.1.1.
> Tujuan prompt ini hanya migrasi incremental v3.1.1 -> v3.1.2.
> Jangan reinstall full config. Jangan rewrite file yang tidak perlu.

---

## Tujuan Migrasi

Update global config agar sinkron dengan perubahan runtime terbaru:

- `AGENT_PATH` tetap dipakai dan tetap menunjuk ke `main.py`
- heavy workflow commands sekarang queue-first dan boleh return `job_id`
- waiting/polling dilakukan via `check.py --wait`
- `check.py --result --wait` mengembalikan cleaned output only saat completed
- prompt/config tidak boleh lagi mengasumsikan semua heavy command selesai sinkron di initial invoke

---

## Instruksi Ke Agent

Lakukan update targeted pada file global config di `~/.config/opencode/` yang sudah ada.

Rules:

1. Backup file yang diubah ke `.bak` dulu.
2. Jangan rewrite file yang tidak terdampak.
3. Jangan install dependency baru.
4. Jangan ubah env vars.
5. Fokus hanya pada migrasi contract v3.1.1 -> v3.1.2.

---

## File Yang Wajib Diupdate

- `AGENTS.md`
- `skills/agent-workflow.md`
- `skills/explore.md`
- `skills/plan.md`
- `skills/analyze.md`
- `reference/invocation-examples.md`
- `reference/json-contract.md`
- `skills/help.md` jika command guide masih mengasumsikan invoke sinkron

Update optional bila ada referensi invocation sinkron lama:

- `skills/workflow.md`
- `reference/errors.md`

---

## Delta Yang Wajib Diterapkan

### 1. Version labels

Ganti semua label setup/migrasi yang masih menyebut `v3.1.1` sebagai target active config menjadi `v3.1.2` bila konteksnya adalah versi tujuan setelah update.

### 2. AGENT_PATH tetap dipakai

Pertahankan rule bahwa `AGENT_PATH` tetap digunakan.
Jangan ubah narasi menjadi "AGENT_PATH dihapus".

Wording target:

- `AGENT_PATH` menunjuk ke `main.py`
- OpenCode tidak mengubah env ini
- command berat dipanggil melalui `python $env:AGENT_PATH --command ...`

### 3. Invocation contract berubah

Hapus asumsi lama berikut:

- all workflow agent invocation synchronous
- initial python invoke selalu return final `content`
- agent utama harus manual loop `sleep -> status -> sleep`

Ganti dengan contract baru:

- initial invoke ke `main.py` boleh return payload submit:
  - `ok`
  - `job_id`
  - `status`
  - `submitted_at`
  - `meta`
- jika ada `job_id`, agent utama WAJIB lanjut ke `check.py`
- default waiting path: `check.py --result --wait`
- jika timeout habis, agent utama boleh invoke `check.py` lagi

### 4. check.py contract

Tambahkan dokumentasi eksplisit:

- `python check.py <job_id>` -> single status JSON
- `python check.py <job_id> --wait` -> polling internal sampai selesai/timeout
- `python check.py <job_id> --result --wait` -> cleaned output only saat completed
- incomplete/failed/not_found -> JSON

Exit code contract:

- `0` completed
- `1` failed
- `2` pending/running
- `3` not_found

### 5. Command examples

Ganti contoh lama:

```powershell
python $env:AGENT_PATH -c plan --prompt-file "$promptFile" -s "..." -w "..." --pretty
```

menjadi:

```powershell
python $env:AGENT_PATH --command plan --prompt-file "$promptFile" --session "..." --work-dir "..." --pretty
python ".\check.py" "<job_id>" --result --wait --poll-interval 2 --poll-timeout 60
```

### 6. Ownership rules tetap

Jangan ubah ownership semantics:

- `/.explore` final answer tetap milik OpenCode agent utama
- `/.plan` final structured plan tetap milik OpenCode agent utama
- `/.analyze` final framing tetap milik OpenCode agent utama

Yang berubah hanya lifecycle invocation/result retrieval.

---

## Verification Checklist

Setelah update file, agent WAJIB cek bahwa:

1. Tidak ada kalimat aktif yang menyatakan semua invocation heavy command synchronous.
2. `AGENT_PATH` tetap disebut sebagai path ke `main.py`.
3. Ada referensi `job_id` dan `check.py --wait`.
4. `reference/invocation-examples.md` memakai `--command`, `--session`, `--work-dir`.
5. `reference/json-contract.md` memuat submit payload + `check.py` contract.
6. Tidak ada instruksi yang menyuruh manual polling loop di agent utama bila `check.py --wait` tersedia.

---

## Output Yang Diminta

Setelah selesai, tampilkan ringkas:

```text
[V3.1.2 UPDATE STATUS]
updated_files:
- <path>

verified:
- AGENT_PATH retained
- queue-first main.py contract documented
- check.py wait contract documented
- sync-only assumptions removed from targeted files
```
