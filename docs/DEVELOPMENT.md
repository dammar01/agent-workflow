# Development Guide — ai-proxy

## Prasyarat

- Python 3.10+ (gunakan union type `X | Y`)
- `kimi` CLI tersedia di PATH
- Claude CLI opsional

---

## Setup Environment

### 1. Clone & verify

```bash
cd ai-proxy
python main.py --help
```

### 2. Set env vars wajib

**Windows PowerShell (session saja):**
```powershell
$env:AI_PROXY = "E:\Work\project\ai-proxy\main.py"
```

**Windows PowerShell (permanen):**
```powershell
[Environment]::SetEnvironmentVariable("AI_PROXY", "E:\Work\project\ai-proxy\main.py", "User")
```

**Bash/WSL:**
```bash
export AI_PROXY="/mnt/e/Work/project/ai-proxy/main.py"
```

### 3. Env vars opsional

| Var | Default | Kapan diset |
|-----|---------|-------------|
| `KIMI_COMMAND` | `kimi` | Kimi CLI bukan di PATH default |
| `KIMI_SESSION_FLAG` | `--session` | Kimi CLI pakai flag berbeda |
| `KIMI_WORK_DIR` | `cwd` | Default work dir untuk Kimi |
| `CLAUDE_COMMAND` | `""` (kosong) | Pakai Claude CLI sungguhan (bukan placeholder) |
| `AI_PROXY_TIMEOUT_SECONDS` | `300` | Naikkan jika Kimi sering timeout |

---

## Menjalankan Proxy

```bash
python main.py -c explore -p "cari auth middleware" -s "my-project" -w "/path/to/project"
python main.py -c analyze -p "apa fungsi cache?" -s "my-project" --pretty
python main.py -c execute -p "buat plan refactor" -s "my-project"
```

### Arguments

| Flag | Alias | Wajib | Deskripsi |
|------|-------|-------|-----------|
| `--command` | `-c` | Ya | `explore`, `plan`, `analyze`, `execute`, `verify` |
| `--prompt` | `-p` | Ya | Task atau pertanyaan |
| `--session` | `-s` | Tidak | Nama sesi (default: `"default"`) |
| `--work-dir` | `-w` | Tidak | Path project target untuk Kimi |
| `--pretty` | — | Tidak | Pretty-print JSON output |

---

## Menambah Command Baru

1. Buka `config/routing.py`
2. Tambah entry ke `COMMAND_ROUTES`:

```python
COMMAND_ROUTES = {
    # ...existing entries...
    "summarize": (MODEL_KIMI,),   # atau MODEL_CLAUDE
}
```

3. Tambah ke choices di `main.py` argparse:

```python
parser.add_argument("--command", "-c", required=True,
    choices=["explore", "plan", "analyze", "execute", "verify", "summarize"])
```

Tidak ada perubahan lain yang diperlukan — Router dan Executor sudah handle command baru secara otomatis.

---

## Menambah Adapter Baru

Untuk menambah model AI selain Kimi dan Claude:

1. Buat `adapters/<model>_adapter.py`:

```python
class MyModelAdapter:
    model = "mymodel"

    def run(self, prompt: str, session: dict) -> dict:
        # Return format HARUS:
        # Success: {"ok": True, "content": "...", "meta": {...}}
        # Error:   {"ok": False, "content": "...", "meta": {...}}
        ...
```

2. Daftarkan model di `config/roles.py`:

```python
MODEL_MYMODEL = "mymodel"
VALID_MODELS = {MODEL_KIMI, MODEL_CLAUDE, MODEL_MYMODEL}
```

3. Update `config/routing.py` untuk command yang menggunakan model baru.

4. Update `Executor.__init__()` di `core/executor.py` untuk inject adapter baru.

5. Tambah dispatch branch di `Executor.execute()`.

---

## Struktur Storage

```
storage/
├── sessions/
│   └── <session_id>.json     ← auto-created saat pertama kali session dipakai
└── cache.json                ← auto-created saat pertama kali cache diisi
```

Kedua direktori dan file dibuat otomatis — tidak perlu setup manual.

---

## Menjalankan Test

```bash
python test_scenario.py
```

**Perhatian:** Test menulis ke `storage/` (tidak isolated). Bersihkan sebelum/sesudah jika diperlukan:

```bash
rm -rf storage/sessions/
rm storage/cache.json
```

---

## Troubleshooting

| Error | Penyebab | Solusi |
|-------|----------|--------|
| `command not found: kimi` | Kimi CLI tidak di PATH | Install Kimi CLI atau set `KIMI_COMMAND` |
| Output Claude masih placeholder | `CLAUDE_COMMAND` kosong | Set `CLAUDE_COMMAND=claude` (atau path ke binary) |
| Kimi baca project yang salah | `work_dir` tidak diset | Gunakan flag `-w /path/to/target` |
| `TimeoutExpired` | Request terlalu lama | Naikkan `AI_PROXY_TIMEOUT_SECONDS` |
| Output corrupt / JSON error | Encoding issue | Sudah ada PYTHONUTF8=1 di subprocess env; cek versi Kimi CLI |
