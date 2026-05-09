import json
import os
import subprocess
from pathlib import Path

from config.settings import DEFAULT_TIMEOUT_SECONDS, KIMI_COMMAND, KIMI_JSON_PATH, KIMI_SESSION_FLAG, KIMI_WORK_DIR
from utils.parser import first_non_empty, ensure_text


class KimiAdapter:
    model = "kimi"

    def __init__(
        self,
        command: str = KIMI_COMMAND,
        timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS,
        session_flag: str = KIMI_SESSION_FLAG,
        work_dir: str = KIMI_WORK_DIR,
        kimi_json_path: Path = KIMI_JSON_PATH,
    ) -> None:
        self.command = command
        self.timeout_seconds = timeout_seconds
        self.session_flag = session_flag
        self.work_dir = work_dir
        self.kimi_json_path = kimi_json_path

    def run(self, prompt: str, session: dict, work_dir: str | None = None) -> dict:
        effective_work_dir = work_dir or self.work_dir
        args = [self.command, "--quiet", "-w", effective_work_dir, "-p", prompt]
        kimi_session_id = session.get("kimi_session_id")
        if self.session_flag and kimi_session_id:
            args.extend([self.session_flag, kimi_session_id])

        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"

        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout_seconds,
                check=False,
                env=env,
            )
        except FileNotFoundError as exc:
            return self._error(f"command not found: {self.command}", {"error": str(exc)})
        except subprocess.TimeoutExpired as exc:
            raw = first_non_empty(exc.stderr, exc.stdout, f"timeout after {self.timeout_seconds}s")
            return self._error(raw, {"timeout_seconds": self.timeout_seconds})
        except OSError as exc:
            return self._error(str(exc), {"error": type(exc).__name__})

        raw = first_non_empty(completed.stdout, completed.stderr)
        meta = {
            "returncode": completed.returncode,
            "stderr": ensure_text(completed.stderr).strip(),
        }

        if completed.returncode != 0:
            return self._error(raw, meta)

        return {"ok": True, "content": ensure_text(completed.stdout).strip(), "meta": meta}

    def read_last_session_id(self, work_dir: str | None = None) -> str | None:
        effective_work_dir = work_dir or self.work_dir
        try:
            with self.kimi_json_path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            for entry in data.get("work_dirs", []):
                if Path(entry.get("path", "")).resolve() == Path(effective_work_dir).resolve():
                    return entry.get("last_session_id") or None
        except (OSError, json.JSONDecodeError, KeyError):
            pass
        return None

    @staticmethod
    def _error(content: str, meta: dict) -> dict:
        return {"ok": False, "content": ensure_text(content), "meta": meta}
