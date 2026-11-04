"""EXP-017 install matrix for one platform (written for macOS; the steps are POSIX).

    python tools/sim/install_matrix.py                  # every arm
    python tools/sim/install_matrix.py --arms uv,pip    # a subset
    python tools/sim/install_matrix.py --list           # print arms and steps, run nothing
    python tools/sim/install_matrix.py --keep --out result.json

Installs Exoma (default: the PyPI release exoma==1.0.0b1; `--source pinned` for the
commit in pyproject.toml) into a throwaway HOME per arm, so nothing
under the real ~/.claude, ~/.gemini, ~/.codex or the real pipx/uv tool homes is read or
written. No provider process is started and no quota is used. The network is needed
(the package, and playwright for the e2e step).

It records what each step returned. It never patches Exoma: a failing step is a finding for
EXP-017 / H-024 / CASE-035, not something this script works around.

Steps per arm
  install, version, init, host-install, hook-launchers, doctor, upgrade-twice
CASE-035 steps, each recorded on its own
  a python-alias       doctor with only `python3` on PATH: is `python_callable` an issue
  b install-route      which of uv / pipx / pip an externally managed Python leaves usable
  c e2e-extra          `exoma install --with-e2e` from a state where only playwright is missing
  d agy-spaced-home    `exoma install all` (agy among the hosts) under a tool home with a space (arm pipx-spaced-home)
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ARMS = ("uv", "pipx", "pip", "pipx-spaced-home")
TIMEOUT = 900
DEFAULT_SOURCE = "exoma==1.0.0b1"


def pinned_source() -> str:
    text = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'"(exoma @ git\+https://[^"]+)"', text)
    if not m:
        sys.exit("pyproject.toml has no pinned exoma line")
    return m.group(1)


class Arm:
    def __init__(self, name: str, root: Path, source: str):
        self.name, self.source = name, source
        self.root = root
        self.home = root / "home"
        self.exoma_home = root / "exoma-home"
        self.project = root / "project"
        spaced = name == "pipx-spaced-home"
        self.tool_home = root / ("with space" if spaced else "plain") / "tools"
        self.bin = root / "bin"
        self.shims = root / "shims"
        for d in (self.home, self.exoma_home, self.project, self.tool_home, self.bin, self.shims):
            d.mkdir(parents=True, exist_ok=True)
        # Only python3 is shimmed, never `python`, so step (a) sees the alias as absent.
        for tool in ("python3", "git", "uv", "pipx", "bash", "node", "opencode", "codex"):
            found = shutil.which(tool)
            if found and not (self.shims / tool).exists():
                (self.shims / tool).symlink_to(found)
        self.path = os.pathsep.join([str(self.bin), str(self.shims), "/usr/bin", "/bin", "/usr/sbin", "/sbin"])
        self.env = {
            "HOME": str(self.home),
            "EXOMA_HOME": str(self.exoma_home),
            "PATH": self.path,
            "PIPX_HOME": str(self.tool_home / "pipx"),
            "PIPX_BIN_DIR": str(self.bin),
            "UV_TOOL_DIR": str(self.tool_home / "uv"),
            "UV_TOOL_BIN_DIR": str(self.bin),
            "XDG_CONFIG_HOME": str(self.home / ".config"),
            "XDG_DATA_HOME": str(self.home / ".local" / "share"),
            "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
            "LANG": "C.UTF-8",
        }
        self.venv = root / "venv"

    def run(self, argv, cwd=None, stdin=None, timeout=TIMEOUT):
        try:
            p = subprocess.run(
                [str(a) for a in argv], cwd=str(cwd or self.project), env=self.env,
                input=stdin, capture_output=True, text=True, timeout=timeout,
            )
            return p.returncode, (p.stdout + p.stderr)
        except subprocess.TimeoutExpired:
            return 124, f"timeout after {timeout}s"
        except OSError as exc:
            return 127, str(exc)

    @property
    def exoma(self) -> str:
        return str(self.venv / "bin" / "exoma") if self.name == "pip" else "exoma"


def tail(text: str, n: int = 6) -> str:
    return "\n".join(text.strip().splitlines()[-n:])


def step(results, name, ok, detail=""):
    results.append({"step": name, "ok": bool(ok), "detail": detail})
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail.splitlines()[0]}" if detail else ""))
    return ok


def install(arm: Arm):
    if arm.name == "uv":
        return arm.run(["uv", "tool", "install", arm.source])
    if arm.name in ("pipx", "pipx-spaced-home"):
        return arm.run(["pipx", "install", arm.source])
    rc, out = arm.run(["python3", "-m", "venv", arm.venv])
    if rc:
        return rc, out
    return arm.run([arm.venv / "bin" / "pip", "install", arm.source])


def doctor(arm: Arm):
    """doctor through the project's own runner; returns (rc, parsed-or-None, raw)."""
    rc, out = arm.run(["bash", arm.project / ".workflow" / "run.sh", "doctor", "--session", "matrix"])
    try:
        data = json.loads(out[out.index("{"):])
    except (ValueError, json.JSONDecodeError):
        data = None
    return rc, data, out


def e2e_state(arm: Arm):
    """playwright_installed from the doctor report file; `run.sh doctor` stdout carries no checks."""
    try:
        report = json.loads((arm.project / ".workflow" / "data" / "reports" / "doctor.json").read_text())
        return (report.get("checks", report).get("e2e_readiness") or {}).get("playwright_installed")
    except (OSError, ValueError):
        return "unreadable"


def run_arm(arm: Arm, only_core: bool = False):
    res = []
    print(f"\n== arm {arm.name} (root {arm.root})")
    subprocess.run(["git", "init", "-q", str(arm.project)], check=False, env=arm.env)
    (arm.project / "README.md").write_text("matrix\n")
    subprocess.run(["git", "-C", str(arm.project), "add", "."], check=False, env=arm.env)
    subprocess.run(
        ["git", "-C", str(arm.project), "-c", "user.name=m", "-c", "user.email=m@example.invalid",
         "commit", "-qm", "init"], check=False, env=arm.env,
    )

    rc, out = install(arm)
    if not step(res, "install", rc == 0, tail(out)):
        return res
    rc, out = arm.run([arm.exoma, "--version"])
    step(res, "version", rc == 0 and "exoma" in out.lower(), tail(out, 2))
    rc, out = arm.run([arm.exoma, "--command", "init", "--work-dir", arm.project])
    step(res, "init", rc == 0 and (arm.project / ".workflow" / "run.sh").exists(), tail(out))

    rc, out = arm.run([arm.exoma, "install", "claude", "--no-input"])
    step(res, "host-install claude", rc == 0, tail(out))

    hooks = sorted((arm.home / ".claude" / "hooks").glob("*.sh")) if (arm.home / ".claude").exists() else []
    bad = []
    for h in hooks:
        rc, out = arm.run(["bash", h], stdin=json.dumps({"session_id": "matrix", "cwd": str(arm.project)}), timeout=60)
        if rc not in (0, 2) or "Traceback" in out:  # 2 is a deliberate gate refusal
            bad.append(f"{h.name} rc={rc}")
    step(res, "hook-launchers", hooks and not bad, f"{len(hooks)} launchers" + (f"; bad: {bad}" if bad else ""))

    rc, data, raw = doctor(arm)
    status = (data or {}).get("meta", {}).get("status", "unparsed")
    issues = (data or {}).get("meta", {}).get("issues", [])
    step(res, "doctor", status == "READY", f"status={status}; issues={issues}")

    counts = []
    for _ in range(2):
        rc, out = arm.run([arm.exoma, "--command", "upgrade", "--work-dir", arm.project])
        try:
            counts.append(len(json.loads(out[out.index("{"):])["meta"].get("regenerated_scripts", [])))
        except (ValueError, KeyError, json.JSONDecodeError):
            counts.append(-1)
    step(res, "upgrade-twice", counts == [counts[0], 0] or counts[1:] == [0], f"regenerated_scripts per run: {counts}")

    if only_core:
        return res

    # (a) the alias: only python3 is on PATH
    has_alias = shutil.which("python", path=arm.path) is not None
    raw_issues = " ".join(issues)
    step(res, "a python-alias", not has_alias and "python not callable" not in raw_issues,
         f"`python` on PATH={has_alias}; doctor issues mention it={'python not callable' in raw_issues}")

    # (b) the install route: what an externally managed Python leaves usable
    rc_pip, out_pip = arm.run(["python3", "-m", "pip", "--version"])
    rc_ext = arm.run(["python3", "-c",
                      "import sysconfig,os;print(os.path.exists(os.path.join(sysconfig.get_path('stdlib'),'EXTERNALLY-MANAGED')))"])[1]
    step(res, "b install-route", True,
         f"system `python3 -m pip` rc={rc_pip}; EXTERNALLY-MANAGED={rc_ext.strip()}; "
         f"uv={bool(shutil.which('uv'))} pipx={bool(shutil.which('pipx'))}  (observation, not a pass/fail)")

    # (d) agy under this arm's tool home (matters for pipx-spaced-home)
    # `install agy` alone narrows the chosen hosts to agy and takes Exoma's files out of the
    # others (claude hooks included); `all` keeps every host, so issues after this step are
    # comparable with the steps before it.
    rc, out = arm.run([arm.exoma, "install", "all", "--no-input"])
    # "refused" alone also matches an unrelated opencode model warning; use agy's own wording.
    refused = any(m in out for m in ("cannot be installed", "cannot carry unquoted", "NOT INSTALLABLE"))
    rc2, out2 = arm.run([arm.exoma, "install", "--check"])
    refused = refused or "NOT INSTALLABLE" in out2
    rc, data, raw = doctor(arm)
    status = (data or {}).get("meta", {}).get("status", "unparsed")
    spaced = " " in str(arm.tool_home)
    step(res, "d agy-spaced-home", (not refused) or status == "READY",
         f"tool home has space={spaced}; agy refused={refused}; doctor status after={status}; "
         f"issues after={(data or {}).get('meta', {}).get('issues', [])}")

    # (c) e2e extra last, it changes the environment
    doctor(arm)
    before = e2e_state(arm)
    rc, out = arm.run([arm.exoma, "install", "--with-e2e", "--no-input"], timeout=TIMEOUT)
    doctor(arm)
    pw = e2e_state(arm)
    warns = [l.strip() for l in out.splitlines() if l.strip().startswith("!") and ("pip" in l or "playwright" in l)]
    step(res, "c e2e-extra", rc == 0 and bool(pw) and pw != "unreadable",
         f"install rc={rc}; playwright_installed before={before} after={pw}; warnings={warns}")
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--arms", default=",".join(ARMS))
    ap.add_argument("--source", default=DEFAULT_SOURCE,
                    help="requirement to install (default: the PyPI release under test); "
                         "'pinned' uses the commit in pyproject.toml")
    ap.add_argument("--core-only", action="store_true", help="skip the CASE-035 steps a-d")
    ap.add_argument("--keep", action="store_true", help="keep the temp roots")
    ap.add_argument("--out", help="write the JSON results here")
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    arms = [a.strip() for a in args.arms.split(",") if a.strip()]
    unknown = [a for a in arms if a not in ARMS]
    if unknown:
        sys.exit(f"unknown arm(s): {unknown}; known: {list(ARMS)}")
    if args.list:
        print("arms:", ", ".join(arms))
        print("source:", pinned_source() if args.source == "pinned" else args.source)
        return 0
    if sys.platform != "darwin":
        print(f"note: written for macOS, running on {sys.platform}; Linux needs its own image (EXP-017)")
    for tool in ("git", "bash", "python3") + (("uv",) if "uv" in arms else ()) + (("pipx",) if any(a.startswith("pipx") for a in arms) else ()):
        if not shutil.which(tool):
            sys.exit(f"missing on the host: {tool}")

    source = pinned_source() if args.source == "pinned" else args.source
    all_results, roots = {}, []
    for name in arms:
        root = Path(tempfile.mkdtemp(prefix=f"exp017-{name}-"))
        roots.append(root)
        all_results[name] = run_arm(Arm(name, root, source), only_core=args.core_only)

    print("\n== summary")
    failed = 0
    for name, res in all_results.items():
        bad = [r["step"] for r in res if not r["ok"]]
        failed += len(bad)
        print(f"  {name}: {len(res) - len(bad)}/{len(res)} steps" + (f"  FAIL: {', '.join(bad)}" if bad else ""))
    if args.out:
        Path(args.out).write_text(json.dumps({"source": source, "platform": sys.platform, "arms": all_results}, indent=2))
    if args.keep:
        print("kept:", *roots, sep="\n  ")
    else:
        for r in roots:
            shutil.rmtree(r, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
