# agent-workflow v3.1.1

OpenCode-oriented workflow runtime with project-local workspace support.

## Ringkas

`agent-workflow` menerima command workflow, membangun prompt terstruktur, menjalankan `opencode run` untuk command agent-backed, menyimpan session OpenCode, menulis handoff ke `.workflow/runtime/`, menjaga state project-local, lalu mengembalikan JSON contract stabil.

Command utama:

```text
init      -> local workspace bootstrap
doctor    -> local readiness check
explore   -> role exploration
plan      -> role reasoning
analyze   -> role reasoning
execute   -> role execution
verify    -> role verification
sweep     -> local post-edit impact check
```

`audit` belum diimplementasikan di v3.1.1.

## Prasyarat

- Python 3.10+
- `opencode` tersedia di `PATH`
- Tidak ada dependency third-party

## OpenCode Skills — Caveman

Caveman adalah skill token-compression untuk OpenCode. Install sekali secara global oleh user:

```bash
npx skills add JuliusBrussee/caveman -a opencode
```

Saat prompt muncul, pilih scope **global**.

Setelah install, aktifkan ultra mode di session OpenCode:

```text
/caveman ultra
```

Referensi: https://github.com/JuliusBrussee/caveman

---

## Setup setelah clone

### 1. Optional: set env variable `AGENT_PATH`

`AGENT_PATH` masih didukung dan boleh menunjuk ke `main.py` di folder project ini.
Namun v3.1.1 menambah resolver project-local: runtime akan membaca `.workflow/config.json.runtime.agent_workflow_path` lebih dulu, lalu fallback ke `AGENT_PATH`.

**Persistent (bertahan setelah restart) — jalankan sekali:**

```powershell
[Environment]::SetEnvironmentVariable("AGENT_PATH", "path\to\agent-workflow\main.py", "User")
```

Ganti path sesuai lokasi clone di mesin kamu.

**Session-only (hanya sesi PowerShell aktif):**

```powershell
$env:AGENT_PATH = "path\to\agent-workflow\main.py"
```

### 2. Restart terminal

Setelah set persistent, tutup dan buka kembali terminal agar env variable aktif.

### 3. Verifikasi multi-layer

Jalankan check berikut secara berurutan. Semua harus pass sebelum OpenCode bisa invoke agent-workflow.

**L1 — Env variable terbaca:**

```powershell
echo $env:AGENT_PATH
# Output harus berupa path ke main.py, bukan kosong
```

**L2 — File ada di disk:**

```powershell
Test-Path $env:AGENT_PATH
# Output harus: True
```

**L3 — File adalah .py:**

```powershell
$env:AGENT_PATH.EndsWith(".py")
# Output harus: True
```

**L4 — Python tersedia:**

```powershell
python --version
# Output harus: Python 3.10.x atau lebih tinggi
```

**L5 — Script callable:**

```powershell
python $env:AGENT_PATH --help
# Output harus menampilkan daftar argumen CLI
```

Jika semua pass, setup selesai.

## Config

Config user ada di `config/opencode.json`.

```json
{
  "opencode_command": "opencode",
  "default_model": null,
  "timeout_seconds": 300,
  "routes": {
    "explore": { "role": "exploration", "model": null },
    "plan": { "role": "reasoning", "model": null },
    "analyze": { "role": "reasoning", "model": null },
    "execute": { "role": "execution", "model": null },
    "verify": { "role": "verification", "model": null }
  }
}
```

`model: null` berarti OpenCode memakai model default yang aktif. Custom model pakai format `<provider>/<model_key>`.

## CLI

```bash
python main.py -c explore -p "cari entry point auth" -s "finance-auth" -w "path\to\target-app" --pretty
```

Override model:

```bash
python main.py -c analyze -p "cek logic auth" -s "finance-auth" -m "9router-sdi/gpt-5.3-codex" --pretty
```

Arg:

- `-c`, `--command`: `init`, `doctor`, `explore`, `plan`, `analyze`, `execute`, `verify`, `sweep`
- `-p`, `--prompt`: task/prompt
- `-s`, `--session`: proxy session id
- `-w`, `--work-dir`: project context untuk cache key
- `-m`, `--model`: override model OpenCode
- `--pretty`: JSON indent

`init`, `doctor`, dan `sweep` adalah command lokal dan tidak memerlukan `--prompt`.

## Session

`--session` adalah source of truth untuk binding session workflow.

- `storage/sessions/*.json` tetap menyimpan mapping runtime ke session OpenCode.
- `.workflow/state.json` hanya snapshot state aktif project-local, bukan pemilik session utama.
- Jika `state.session.id` berbeda dengan `--session` saat command jalan, runtime akan reset active state, scope, dan command cache sebelum lanjut.

Run pertama untuk proxy session memanggil:

```text
opencode run <prompt> --print-logs
```

Proxy parse `session.id=ses_...`, lalu simpan ke `storage/sessions/<session>.json` sebagai `opencode_session_id`.

Run berikutnya memakai:

```text
opencode run <prompt> -s <opencode_session_id>
```

## Output Cleanup

OpenCode logs seperti ini dibuang:

```text
INFO  2026-05-09T12:10:24 +1ms service=session.prompt session.id=ses_x step=0 loop
```

Banner model seperti ini juga dibuang:

```text
> build · gpt-5.3-codex
```

Konten assistant dipertahankan.

## Project-local Workspace

`init` membuat struktur berikut di project target:

```text
.workflow/
├─ config.json
├─ state.json
├─ scope.json
├─ command-cache.json
├─ .gitignore
├─ runtime/
│  ├─ prompt.txt
│  ├─ prompt.meta.json
│  ├─ response.last.md
│  └─ lock
└─ reports/
   ├─ doctor.json
   └─ sweep.last.md
```

Root `.gitignore` project target juga akan di-update agar meng-ignore `.workflow/`.

## Storage

Runtime data:

- Session: `storage/sessions/*.json`
- Cache: `storage/cache.json`
- Project-local workflow state: `<target-project>/.workflow/*`

## Typical Flow

```bash
python main.py -c init -w "path\to\target-app" --pretty
python main.py -c doctor -w "path\to\target-app" --pretty
python main.py -c explore -p "cari entry point auth" -s "main_target_20260517_101010" -w "path\to\target-app" --pretty
python main.py -c execute -p "implementasikan plan aktif" -s "main_target_20260517_101010" -w "path\to\target-app" --pretty
python main.py -c sweep -w "path\to\target-app" --pretty
```

`execute` tetap mengedit lewat OpenCode, tetapi runtime lokal akan:

- bind session ke `--session`
- menulis prompt handoff ke `.workflow/runtime/prompt.txt`
- menyimpan response terakhir ke `.workflow/runtime/response.last.md`
- auto-run `sweep` setelah execute sukses

## Verifikasi

```bash
python test_scenario.py
python main.py --help
python main.py -c init -w . --pretty
python main.py -c doctor -w . --pretty
python main.py -c sweep -w . --pretty
```
