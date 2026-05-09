# Module Reference — ai-proxy

Dokumentasi per-file. Urutan: entry → core → adapters → config → utils → test.

---

## `main.py`

**Tanggung jawab:** Entry point dan orkestrator utama.

**Fungsi utama:**

| Fungsi | Deskripsi |
|--------|-----------|
| `run(command, task, session_id, work_dir)` | Orkestrasi penuh: session → cache → executor → cache write → session record |

**Alur:**
1. Load/create session
2. Cek cache — return jika HIT
3. Delegate ke `Executor.execute()`
4. Tulis cache jika success
5. Record run ke session
6. Return output dict

**Entrypoint CLI:**
```
python main.py -c <command> -p <prompt> -s <session> [-w <work_dir>] [--pretty]
```

**Dependensi:** `core/contract`, `core/executor`, `core/session_manager`, `utils/cache`

---

## `core/contract.py`

**Tanggung jawab:** Standardisasi schema output — satu-satunya tempat shape response didefinisikan.

**Fungsi utama:**

| Fungsi | Input | Output |
|--------|-------|--------|
| `normalize_output(*, status, model, role, session_id, content, confidence, notes)` | Raw values | Dict `{status, model, role, session_id, content, meta}` |
| `mark_cache_hit(output, session_id)` | Output dict | Deepcopy + tambah `"cache_hit"` ke `meta.notes` |

**Validasi:**
- `status` harus `"success"` atau `"error"` — invalid → paksa ke `"error"`
- `model` harus `"kimi"` atau `"claude"` — invalid → paksa ke `"error"`
- `role` harus `"exploration"` atau `"reasoning"` — invalid → paksa ke `"error"`
- `confidence` harus `"low"`, `"medium"`, atau `"high"` — invalid → paksa ke `"low"`

**Dependensi:** `config/roles`

---

## `core/executor.py`

**Tanggung jawab:** Dispatch request ke adapter yang tepat, normalize hasilnya.

**Class:** `Executor`

| Method | Deskripsi |
|--------|-----------|
| `__init__(router, kimi, claude, session_manager)` | Dependency injection — semua parameter opsional (default ke instance baru) |
| `execute(command, task, session, work_dir)` | Dispatch utama: route → adapter → normalize |
| `_run_kimi(task, session, work_dir)` | Build prompt exploration → KimiAdapter.run() → link session |
| `_run_claude(task, session)` | Build prompt reasoning → ClaudeAdapter.run() |
| `_contract_from_adapter(result, model, role, session_id, notes)` | Konversi adapter result `{ok, content, meta}` ke contract output |
| `_maybe_link_kimi_session(session, work_dir)` | Baca kimi_session_id dari adapter dan simpan ke session (sekali saja) |

**Dependensi:** `adapters/kimi_adapter`, `adapters/claude_adapter`, `config/roles`, `core/contract`, `core/prompt_builder`, `core/router`

---

## `core/prompt_builder.py`

**Tanggung jawab:** Konstruksi prompt terstruktur dengan template `[WORKFLOW_CONTEXT]` + `[TASK]`.

**Fungsi utama:**

| Fungsi | Input | Output |
|--------|-------|--------|
| `build_prompt(*, role, task, session_id, evidence)` | Role, task string, optional evidence | Prompt string multi-bagian |

**Template structure:**
```
[WORKFLOW_CONTEXT]
source: proxy
role: <role>
session_id: <id>
rules: ...

[EVIDENCE]          ← hanya jika evidence != None
<evidence>

[TASK]
<task>

Return bounded evidence only.   ← exploration
Return the reasoning result only.  ← reasoning
```

**Dependensi:** `config/roles`

---

## `core/router.py`

**Tanggung jawab:** Map command string ke target model.

**Class:** `Router`

| Method | Input | Output |
|--------|-------|--------|
| `route(command)` | Command string | Tuple model: `(MODEL_KIMI,)` atau `(MODEL_CLAUDE,)` |

**Error:** Raise `ValueError` untuk command tidak dikenal.

**Dependensi:** `config/routing`

---

## `core/session_manager.py`

**Tanggung jawab:** Persistensi sesi ke file JSON di `storage/sessions/`.

**Class:** `SessionManager`

| Method | Deskripsi |
|--------|-----------|
| `load_or_create(session_id)` | Load file jika ada, buat baru jika tidak |
| `update_kimi_session_id(session, kimi_id)` | Set `kimi_session_id` dan simpan |
| `record_run(session, command, cache_hit)` | Append run ke `history.runs`, update `updated_at` |
| `_path_for(session_id)` | Sanitasi session_id (karakter tidak aman → `_`) → return Path |

**Session file format:** `storage/sessions/<safe_session_id>.json`

**Catatan:** Session ID di-sanitasi dengan regex `[^A-Za-z0-9_.-]` → `_` untuk nama file aman.

**Dependensi:** `config/settings`

---

## `adapters/kimi_adapter.py`

**Tanggung jawab:** Wrapper subprocess ke Kimi CLI.

**Class:** `KimiAdapter`

| Method | Deskripsi |
|--------|-----------|
| `run(prompt, session, work_dir)` | Jalankan `kimi --quiet -w <dir> -p <prompt> [--session <id>]` |
| `read_last_session_id(work_dir)` | Baca `~/.kimi/kimi.json` untuk ambil `last_session_id` per work_dir |

**Return format adapter:**
- Success: `{"ok": True, "content": "...", "meta": {"returncode": 0, "stderr": "..."}}`
- Error: `{"ok": False, "content": "...", "meta": {...}}`

**Error yang ditangani:** `FileNotFoundError`, `subprocess.TimeoutExpired`, `OSError`

**Env vars dibaca:** `KIMI_COMMAND`, `KIMI_SESSION_FLAG`, `KIMI_WORK_DIR`, `AI_PROXY_TIMEOUT_SECONDS`

**Catatan:** Set `PYTHONUTF8=1` dan `PYTHONIOENCODING=utf-8` di env subprocess untuk output encoding konsisten.

**Dependensi:** `config/settings`, `utils/parser`

---

## `adapters/claude_adapter.py`

**Tanggung jawab:** Wrapper subprocess ke Claude CLI, dengan placeholder mode jika CLI tidak dikonfigurasi.

**Class:** `ClaudeAdapter`

| Method | Deskripsi |
|--------|-----------|
| `run(prompt, session)` | Jalankan `claude -p <prompt>` — atau return placeholder jika `CLAUDE_COMMAND` kosong |

**Placeholder mode:** Jika `CLAUDE_COMMAND` env var kosong (default), adapter return `ok: True` dengan content placeholder. Tidak error.

**Return format:** Sama dengan KimiAdapter (`ok`, `content`, `meta`).

**Error yang ditangani:** `FileNotFoundError`, `subprocess.TimeoutExpired`, `OSError`

**Env vars dibaca:** `CLAUDE_COMMAND`, `AI_PROXY_TIMEOUT_SECONDS`

**Dependensi:** `config/settings`, `utils/parser`

---

## `config/roles.py`

**Tanggung jawab:** Konstanta domain — nama model dan role.

| Konstanta | Nilai |
|-----------|-------|
| `ROLE_EXPLORATION` | `"exploration"` |
| `ROLE_REASONING` | `"reasoning"` |
| `MODEL_KIMI` | `"kimi"` |
| `MODEL_CLAUDE` | `"claude"` |
| `VALID_ROLES` | `{ROLE_EXPLORATION, ROLE_REASONING}` |
| `VALID_MODELS` | `{MODEL_KIMI, MODEL_CLAUDE}` |
| `MODEL_ROLES` | `{MODEL_KIMI: ROLE_EXPLORATION, MODEL_CLAUDE: ROLE_REASONING}` |

**Tidak ada dependensi.**

---

## `config/routing.py`

**Tanggung jawab:** Definisi peta command → target model.

```python
COMMAND_ROUTES = {
    "explore": (MODEL_KIMI,),
    "plan":    (MODEL_KIMI,),
    "analyze": (MODEL_KIMI,),
    "execute": (MODEL_CLAUDE,),
    "verify":  (MODEL_CLAUDE,),
}
```

Untuk menambah command baru: tambah entry di dict ini.

**Dependensi:** `config/roles`

---

## `config/settings.py`

**Tanggung jawab:** Konfigurasi runtime dari environment variables dan path default.

| Variabel | Default | Env var |
|----------|---------|---------|
| `BASE_DIR` | Parent dir dari `config/` | — |
| `SESSION_DIR` | `BASE_DIR/storage/sessions` | — |
| `CACHE_FILE` | `BASE_DIR/storage/cache.json` | — |
| `DEFAULT_TIMEOUT_SECONDS` | `300` | `AI_PROXY_TIMEOUT_SECONDS` |
| `KIMI_COMMAND` | `"kimi"` | `KIMI_COMMAND` |
| `KIMI_SESSION_FLAG` | `"--session"` | `KIMI_SESSION_FLAG` |
| `KIMI_WORK_DIR` | `Path.cwd()` | `KIMI_WORK_DIR` |
| `KIMI_JSON_PATH` | `~/.kimi/kimi.json` | — |
| `CLAUDE_COMMAND` | `""` (kosong) | `CLAUDE_COMMAND` |

**Tidak ada dependensi internal.**

---

## `utils/cache.py`

**Tanggung jawab:** Cache file-based dengan key SHA-256 dan optional TTL.

**Class:** `SimpleCache`

| Method | Deskripsi |
|--------|-----------|
| `make_key(command, task, work_dir)` | Buat cache key: `"<cmd>:<dir_hash12>:<task_sha256>"` |
| `get(key)` | Return cached response atau `None` (cek TTL jika set) |
| `set(key, response)` | Simpan response dengan `created_at` timestamp |
| `clear()` | Hapus semua cache entries |

**Cache file:** `storage/cache.json` — semua entries dalam satu JSON object.

**Key structure:**
- Dengan `work_dir`: `"explore:a1b2c3d4e5f6:<sha256>"`
- Tanpa `work_dir`: `"explore:<sha256>"`

**Dependensi:** `config/settings`

---

## `utils/logger.py`

**Tanggung jawab:** Factory logger dengan format konsisten.

**Fungsi:**

| Fungsi | Deskripsi |
|--------|-----------|
| `get_logger(name)` | Return `logging.Logger` dengan basicConfig INFO format |

**Format log:** `"%(asctime)s %(levelname)s %(name)s: %(message)s"`

**Tidak ada dependensi internal.**

---

## `utils/parser.py`

**Tanggung jawab:** Safe text/JSON coercion dan cleaning untuk output subprocess.

**Fungsi:**

| Fungsi | Deskripsi |
|--------|-----------|
| `ensure_text(value)` | Konversi bytes/None/any ke string — tidak pernah raise |
| `first_non_empty(*values)` | Return value pertama yang tidak kosong setelah `ensure_text()` |
| `clean_json_string(s)` | Bersihkan karakter kontrol dan trailing comma dari JSON string |
| `extract_json_from_text(text)` | Ekstrak JSON object/array pertama yang valid dari teks bebas |
| `safe_parse_json(value)` | Parse JSON dengan 4-level fallback: direct → clean → extract → key-value |
| `clean_structured_output(text)` | Ekstrak JSON terpanjang dari teks (strip markdown code fences) |

**Catatan:** `safe_parse_json()` tidak pernah raise — selalu return dict. Worst-case: `{"raw_content": <original>}`.

**Tidak ada dependensi internal.**

---

## `test_scenario.py`

**Tanggung jawab:** Integration test end-to-end untuk semua command.

**Catatan penting:**
- Menulis ke `storage/` — **tidak isolated**
- Jalankan di environment terpisah atau bersihkan `storage/` sebelum/sesudah
- Mengharapkan `kimi` CLI tersedia di PATH
- Test `plan` mengharapkan 2-step flow (Kimi → Claude) yang belum diimplementasi di `config/routing.py`
