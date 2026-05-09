# ai-proxy v1.1

Proxy CLI untuk workflow personal Claude/Kimi.

## Dokumentasi

- [Business Logic Report](docs/BUSINESS_LOGIC_REPORT.md) — alur bisnis, temuan, dan gap
- [Architecture](docs/ARCHITECTURE.md) — arsitektur sistem, layer, dependency graph
- [Module Reference](docs/MODULES.md) — dokumentasi per-file
- [Development Guide](docs/DEVELOPMENT.md) — setup, tambah command/adapter, troubleshooting
- [Changelog](CHANGELOG.md)

Routing utama:

- `explore` -> Kimi
- `plan` -> Kimi untuk evidence, lalu Claude untuk reasoning
- `analyze`, `execute`, `verify` -> Claude

## Prasyarat

- Python 3.10+
- `kimi` CLI tersedia di `PATH`
- Opsional: Claude CLI jika command Claude ingin benar-benar dijalankan via proxy
- Tidak ada dependency third-party. `requirements.txt` hanya marker.

## Setup environment

Set `AI_PROXY` ke path `main.py` repo ini.

Windows PowerShell, hanya untuk session aktif:

```powershell
$env:AI_PROXY = "E:\Work\project\ai-proxy\main.py"
```

Windows PowerShell, permanen untuk user:

```powershell
[Environment]::SetEnvironmentVariable("AI_PROXY", "E:\Work\project\ai-proxy\main.py", "User")
```

Bash/WSL:

```bash
export AI_PROXY="/mnt/e/Work/project/ai-proxy/main.py"
```

Env var opsional:

```bash
export KIMI_COMMAND="kimi"
export KIMI_SESSION_FLAG="--session"
export KIMI_WORK_DIR="/path/to/default/project"
export CLAUDE_COMMAND="claude"
export AI_PROXY_TIMEOUT_SECONDS="120"
```

Catatan:

- `KIMI_COMMAND` default: `kimi`
- `KIMI_SESSION_FLAG` default: `--session`
- `KIMI_WORK_DIR` default: current working directory
- `CLAUDE_COMMAND` default kosong. Jika kosong, Claude adapter memakai placeholder.
- `AI_PROXY_TIMEOUT_SECONDS` default: `300`

## Setup Claude Code (Proxy Mode)

1. Buka `CLAUDE_CODE_CONFIG_V1.1.md`.
2. Copy seluruh isi file.
3. Paste ke Claude Code.
4. Jalankan instruksi setup yang ada di file tersebut.

Jika config sudah pernah di-setup dan hanya ingin update protocol terbaru, gunakan `UPDATE_CONFIG_PROMPT.md` — tidak perlu re-install penuh.

Setelah setup, gunakan command workflow dari Claude Code:

```text
/.explore <hint>
/.plan <task>
/.execute -y
/.verify
/.analyze <topic>
/.memory <note>
```

`/.explore` akan memanggil proxy ke Kimi. `/.plan` mengumpulkan evidence via proxy lalu reasoning di Claude.

## Setup Agent-Agnostic (Single Agent, No Proxy)

Untuk workflow tanpa proxy — bekerja dengan Claude Code, Codex, Cursor, Windsurf, Gemini CLI, dan GitHub Copilot:

1. Buka `WORKFLOW_V0.md`.
2. Copy seluruh isi file.
3. Paste ke agent yang ingin dikonfigurasi.
4. Jalankan instruksi setup yang ada di file tersebut.

Agent akan otomatis mendeteksi dirinya dan menyimpan config ke direktori yang sesuai:

| Agent | Direktori |
|-------|-----------|
| Claude Code | `~/.claude/` |
| Codex | `~/.codex/` |
| Cursor | `~/.cursor/` |
| Windsurf | `~/.windsurf/` |
| Gemini CLI | `~/.gemini/` |
| GitHub Copilot | `~/.github-copilot/` |

Menjalankan `WORKFLOW_V0.md` dua kali aman — setup mendeteksi instalasi sebelumnya dan hanya mengupdate skill files, memory dipertahankan.

Workflow command yang tersedia:

```text
/.explore <hint>
/.plan <task>
/.execute -y
/.verify
/.refactor <scope>
/.analyze <topic>
/.memory <note>
```

Eksplorasi menggunakan `graphify-out/` sebagai sumber utama. Pastikan `graphify update` sudah dijalankan di project target.

## Setup Kimi

1. Buka `KIMI_CODE_CONFIG_V1.1.md`.
2. Copy seluruh isi file.
3. Paste ke Kimi.
4. Jalankan instruksi setup yang ada di file tersebut.

Kimi workflow memakai `graphify-out/` sebagai sumber eksplorasi utama. Jika project target belum punya `graphify-out/`, buat `.graphifyignore` sesuai framework lalu jalankan `graphify update` manual di terminal.

## Penggunaan CLI langsung

PowerShell:

```powershell
python "$env:AI_PROXY" -c explore -p "cari entry point auth" -s "finance-auth" -w "E:\Work\project\target-app" --pretty
```

Bash/WSL:

```bash
python3 "$AI_PROXY" -c explore -p "cari entry point auth" -s "finance-auth" -w "/mnt/e/Work/project/target-app" --pretty
```

Format command:

```bash
python3 main.py \
  --command explore \
  --prompt "cari entry point auth" \
  --session "finance-auth" \
  --work-dir "/path/to/project" \
  --pretty
```

Shortcut arg:

- `-c`, `--command`: `explore`, `plan`, `analyze`, `execute`, atau `verify`
- `-p`, `--prompt`: task/prompt
- `-s`, `--session`: nama session
- `-w`, `--work-dir`: project directory untuk Kimi
- `--pretty`: JSON output dengan indentasi

## Session dan cache

Gunakan nama session konsisten per project + fitur.

Contoh:

```text
finance-auth
bangai-api
checkout-refactor
```

Data runtime:

- Session: `storage/sessions/*.json`
- Cache: `storage/cache.json`

Request dengan command + prompt yang sama bisa kena cache.

## Verifikasi

Cek CLI:

```bash
python3 main.py --help
```

Jalankan scenario test jika memang ingin memvalidasi behavior proxy:

```bash
python3 test_scenario.py
```

Catatan: `test_scenario.py` menyentuh `storage/` karena menguji session dan cache.

## Troubleshooting

- `command not found: kimi` -> pastikan Kimi CLI terinstall dan `KIMI_COMMAND` benar.
- Output Claude masih placeholder -> set `CLAUDE_COMMAND`.
- Kimi membaca project yang salah -> isi `-w/--work-dir` dengan path target project.
- Timeout -> naikkan `AI_PROXY_TIMEOUT_SECONDS`.
