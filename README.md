# agent-workflow v2

OpenCode-only proxy CLI untuk workflow personal.

## Ringkas

`agent-workflow` menerima command workflow, membangun prompt terstruktur, menjalankan `opencode run`, menyimpan session OpenCode, membersihkan log, lalu mengembalikan JSON contract stabil.

Command utama:

```text
explore   -> role exploration
plan      -> role reasoning
analyze   -> role reasoning
execute   -> role execution
verify    -> role verification
```

## Prasyarat

- Python 3.10+
- `opencode` tersedia di `PATH`
- Tidak ada dependency third-party

## Setup setelah clone

### 1. Set env variable `AGENT_PATH`

`AGENT_PATH` harus menunjuk ke `main.py` di folder project ini.
Env variable ini dipakai oleh OpenCode global config untuk invoke agent-workflow tanpa hardcode path.

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

- `-c`, `--command`: `explore`, `plan`, `analyze`, `execute`, `verify`
- `-p`, `--prompt`: task/prompt
- `-s`, `--session`: proxy session id
- `-w`, `--work-dir`: project context untuk cache key
- `-m`, `--model`: override model OpenCode
- `--pretty`: JSON indent

## Session

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

## Storage

Runtime data:

- Session: `storage/sessions/*.json`
- Cache: `storage/cache.json`

Cache key include command, model, work dir hash, prompt hash.

## Verifikasi

```bash
python test_scenario.py
python main.py --help
```
