# ai-proxy v2

OpenCode-only proxy CLI untuk workflow personal.

## Ringkas

`ai-proxy` menerima command workflow, membangun prompt terstruktur, menjalankan `opencode run`, menyimpan session OpenCode, membersihkan log, lalu mengembalikan JSON contract stabil.

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
python main.py -c explore -p "cari entry point auth" -s "finance-auth" -w "E:\Work\project\target-app" --pretty
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
