"""What has to be true before a browser run is worth starting.

Each check names the `incomplete` reason it produces, so a run that cannot happen says
why in one word the user can act on. None of this touches Playwright at import time;
`browser_available` imports it inside the call and only after `playwright_available`
said it was there.
"""

from __future__ import annotations

import importlib.util
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0"})
_SCHEMES = frozenset({"http", "https"})
# The installer's pin (install.py --with-e2e). Read, never imported: doctor reports it next
# to what is actually installed so a drifted Playwright is visible before a run misbehaves.
E2E_REQUIREMENTS = Path(__file__).resolve().parents[3] / "requirements-e2e.txt"


def safe_base_url(url: str, config: dict) -> tuple[bool, str]:
    """Local by default; anything else needs `allow_remote` AND an allowed origin."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False, f"base_url '{url}' does not parse"
    if parts.scheme not in _SCHEMES or not parts.hostname:
        return False, f"base_url '{url}' must be http(s)://host[:port]"
    host = parts.hostname.lower()
    if host in _LOOPBACK or host.endswith(".localhost"):
        return True, "loopback"
    if not config.get("allow_remote"):
        return False, f"base_url host '{host}' is not loopback and settings.allow_remote is false"
    origin = f"{parts.scheme}://{parts.netloc}".lower()
    allowed = {str(o).lower().rstrip("/") for o in config.get("allowed_origins") or []}
    if origin not in allowed:
        return False, f"origin '{origin}' is not in settings.allowed_origins"
    return True, "remote origin allow-listed"


def same_origin(url: str, base_url: str) -> bool:
    try:
        a, b = urlsplit(url), urlsplit(base_url)
    except ValueError:
        return False
    return (a.scheme, a.netloc.lower()) == (b.scheme, b.netloc.lower())


def playwright_available() -> tuple[bool, str]:
    try:
        spec = importlib.util.find_spec("playwright")
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        return False, "python package 'playwright' is not installed (pip install playwright)"
    return True, "playwright importable"


def browser_available(browser: str) -> tuple[bool, str]:
    """Whether the named browser's binary is provisioned for this Playwright install."""
    try:
        from playwright.sync_api import sync_playwright  # lazy: optional dependency
    except ImportError as exc:
        return False, f"playwright.sync_api import failed: {exc}"
    try:
        with sync_playwright() as pw:
            kind = getattr(pw, browser, None)
            if kind is None:
                return False, f"browser '{browser}' is not a Playwright browser type"
            path = getattr(kind, "executable_path", None)
            if path and not Path(str(path)).exists():
                return False, f"{browser} binary missing at {path} (playwright install {browser})"
    except Exception as exc:  # any driver failure is a provisioning failure here
        return False, f"{type(exc).__name__}: {exc}"
    return True, f"{browser} provisioned"


def base_url_reachable(url: str, timeout_s: float = 3.0) -> tuple[bool, str]:
    """Any HTTP response counts — a 404 is a server, no server is the failure."""
    request = urllib.request.Request(url, method="GET", headers={"User-Agent": "agent-workflow-e2e-preflight"})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:  # noqa: S310 - loopback/allow-listed only
            return True, f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return True, f"HTTP {exc.code}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {getattr(exc, 'reason', exc)}"


def pinned_playwright() -> str | None:
    try:
        text = E2E_REQUIREMENTS.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in text.splitlines():
        match = re.match(r"^\s*playwright\s*==\s*([\w.]+)\s*$", line)
        if match:
            return match.group(1)
    return None


def installed_playwright() -> str | None:
    try:
        from importlib.metadata import version

        return version("playwright")
    except Exception:  # PackageNotFoundError, or a broken dist-info: both mean "not usable"
        return None


def readiness() -> dict:
    """What `doctor` reports about /.verify-browser. Metadata only, never blocking.

    The browser binary and the app URL are checked by each run's own preflight, against
    the settings that run was given: doctor has no URL to check, and starting a Playwright
    driver here would slow doctor down for every project that never opens a browser.
    """
    python = sys.executable
    installed = installed_playwright()
    pinned = pinned_playwright()
    out = {
        "python": python,
        "playwright_installed": installed,
        "playwright_pinned": pinned,
        "detail": "installed; browser and base URL are checked by each /.verify-browser run",
        "fix": None,
        "blocking": False,
    }
    if not installed:
        out["detail"] = (
            "playwright is not installed; /.verify-browser stops at preflight with playwright_missing "
            f"until `python install.py --apply --with-e2e` installs it into {python}"
        )
    elif pinned and installed != pinned:
        out["fix"] = (
            f"playwright {installed} is installed but {pinned} is pinned; "
            "rerun `python install.py --apply --with-e2e` if browser runs misbehave"
        )
    return out


def preflight(config: dict, *, fake: bool = False) -> dict:
    """Run every check in order; stop at the first failure.

    Returns {"ok", "reason", "detail", "checks": [{"name", "ok", "detail"}]}. With
    `fake=True` (a fake player under test) the dependency and URL checks are skipped:
    the URL policy still applies, because it is a policy, not an environment fact.
    """
    checks: list[dict] = []

    def fail(reason: str, detail: str) -> dict:
        checks.append({"name": reason, "ok": False, "detail": detail})
        return {"ok": False, "reason": reason, "detail": detail, "checks": checks}

    ok, detail = safe_base_url(str(config.get("base_url") or ""), config)
    checks.append({"name": "base_url_policy", "ok": ok, "detail": detail})
    if not ok:
        return {"ok": False, "reason": "spec_invalid", "detail": detail, "checks": checks}
    if fake:
        return {"ok": True, "reason": None, "detail": "fake player: dependency checks skipped", "checks": checks}

    ok, detail = playwright_available()
    if not ok:
        return fail("playwright_missing", detail)
    checks.append({"name": "playwright", "ok": True, "detail": detail})

    ok, detail = browser_available(str(config.get("browser") or "chromium"))
    if not ok:
        return fail("browser_missing", detail)
    checks.append({"name": "browser", "ok": True, "detail": detail})

    ok, detail = base_url_reachable(str(config.get("base_url")))
    if not ok:
        return fail("base_url_unreachable", detail)
    checks.append({"name": "base_url", "ok": True, "detail": detail})
    return {"ok": True, "reason": None, "detail": "ready", "checks": checks}
