"""Which provider CLI release a call ran on, and whether this workflow version was tested on it.

Each provider bundle in config/providers.py names the release it was tested against
(`stable_version`). A delegated call and doctor read the installed release with
`<cli> --version` and compare: `stable` when it matches, `untested` when it does not,
`unreadable` when the CLI did not answer. Advisory only: an untested release still runs,
because refusing would block a user on a release that may work; the status is what a failure
report has to name, and the stable release is the one to pin back to (CASE-015).
"""

from __future__ import annotations

import re

from config.providers import provider_stable_version
from utils import osutil

_CACHE: dict[str, str | None] = {}
_SEMVER = re.compile(r"\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?")


def read_version(command: str) -> str | None:
    """First line of `<command> --version`, read once per binary per process; None on failure.

    Bounded and tree-killed: on Windows these CLIs are `.cmd` shims over node, and a plain
    run(timeout=) waits unbounded on pipes the node child still holds.
    """
    exe = osutil.resolve_exe(command)
    if exe not in _CACHE:
        version = None
        try:
            code, stdout, _ = osutil.run_bounded([exe, "--version"], 15)
            lines = (stdout or "").strip().splitlines()
            if code == 0 and lines:
                version = lines[0].strip()[:80] or None
        except Exception:
            version = None
        _CACHE[exe] = version
    return _CACHE[exe]


def release(version: str | None) -> str | None:
    """The semantic version inside a `--version` line (`codex-cli 0.160.0` -> `0.160.0`)."""
    match = _SEMVER.search(version or "")
    return match.group(0) if match else None


def status(provider: str, version: str | None) -> str:
    """`stable`, `untested`, or `unreadable` for `version` of `provider`'s CLI."""
    found = release(version)
    if found is None:
        return "unreadable"
    stable = provider_stable_version(provider)
    return "stable" if stable and found == stable else "untested"


def describe(provider: str, command: str) -> dict:
    """What doctor and a call record: the version line, its status, and the stable release."""
    version = read_version(command)
    return {
        "provider": provider,
        "version": version,
        "status": status(provider, version),
        "stable": provider_stable_version(provider),
    }
