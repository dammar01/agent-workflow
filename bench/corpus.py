"""Build corpus.json from this repo's own history (BENCHMARK-PLAN.md §6, Fase 1).

Candidate commits are proposed, not chosen. The plan fixes difficulty labels BEFORE any
run, and a label derived from diff size is a starting point for that judgement rather
than the judgement itself — so this writes `difficulty` as a suggestion and leaves
`prompt` and `oracle_tests` empty for a human to fill. A generated prompt would be a
prompt written by something that had already seen the answer, which is the one thing the
corpus must not contain.

The leak check is the part worth being strict about. A worktree cut at `<sha>^` that
still contains the answer commit invalidates the unit silently — the agent can simply
read the fix — so `verify_no_leak` exists to be run per worktree before the unit starts.
"""

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS_PATH = Path(__file__).resolve().parent / "corpus.json"

MIN_FILES = 1
MAX_FILES = 3


def _git(args: list[str], cwd: Path = REPO_ROOT) -> str:
    done = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=30
    )
    if done.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {(done.stderr or '').strip()}")
    return done.stdout


def _changed_files(sha: str) -> list[str]:
    out = _git(["show", "--name-only", "--pretty=format:", sha])
    return [line.strip() for line in out.splitlines() if line.strip()]


def _difficulty(files: list[str]) -> str:
    """A suggestion from diff shape, to be confirmed by a human before the run.

    Deliberately crude and deliberately labelled as crude. The hard bucket is defined by
    WHICH files were touched rather than how many, because a one-line change inside
    executor.py is harder than a three-file change in tests.
    """
    hard_surfaces = ("core/executor.py", "core/job_manager.py", "adapters/")
    if any(name.startswith(hard_surfaces) or name in hard_surfaces for name in files):
        return "hard"
    return "easy" if len(files) == 1 else "medium"


def candidates(limit: int = 200) -> list[dict]:
    """Commits that fit the corpus criteria, newest first."""
    log = _git(
        ["log", "--no-merges", f"-n{limit}", "--pretty=format:%H|%h|%s|%ad", "--date=short"]
    )
    found: list[dict] = []
    for line in log.splitlines():
        parts = line.split("|", 3)
        if len(parts) != 4:
            continue
        sha, short, subject, date = parts
        try:
            files = [name for name in _changed_files(sha) if name.endswith(".py")]
        except RuntimeError:
            continue
        if not (MIN_FILES <= len(files) <= MAX_FILES):
            continue
        # Test-only commits make poor tasks: the oracle would be judging the very files
        # the task asked to change.
        if all(name.startswith("tests/") for name in files):
            continue
        found.append(
            {
                "answer_sha": sha,
                "short_sha": short,
                "base_sha": f"{sha}^",
                "subject": subject,
                "date": date,
                "files_expected": files,
                "difficulty_suggested": _difficulty(files),
            }
        )
    return found


def to_entries(rows: list[dict]) -> list[dict]:
    """Corpus entries in the plan's schema, with the human-authored fields left blank."""
    entries: list[dict] = []
    for index, row in enumerate(rows, 1):
        entries.append(
            {
                "task_id": f"T{index:02d}",
                "base_sha": row["base_sha"],
                "answer_sha": row["answer_sha"],
                # Empty on purpose: a prompt generated from the commit message is written
                # by something that has already seen the fix.
                "prompt": "",
                "files_expected": row["files_expected"],
                "difficulty": row["difficulty_suggested"],
                "oracle_tests": [],
                "_source_subject": row["subject"],
                "_source_date": row["date"],
            }
        )
    return entries


def verify_no_leak(worktree, answer_sha: str) -> dict:
    """True when the worktree genuinely predates the answer commit.

    Run per unit before it starts. A worktree that can see the fix turns the whole unit
    into a reading-comprehension test, and nothing downstream would notice.
    """
    try:
        log = _git(["log", "--pretty=format:%H"], cwd=Path(worktree))
    except (RuntimeError, OSError) as exc:
        return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
    leaked = answer_sha in log.split()
    return {
        "ok": not leaked,
        "leaked": leaked,
        "reason": "worktree contains the answer commit" if leaked else "",
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="propose a benchmark corpus from git history")
    parser.add_argument("--limit", type=int, default=200, help="commits to scan")
    parser.add_argument("--take", type=int, default=15, help="entries to write")
    parser.add_argument("--write", action="store_true", help="write bench/corpus.json")
    args = parser.parse_args()

    rows = candidates(args.limit)[: args.take]
    entries = to_entries(rows)
    if args.write:
        CORPUS_PATH.write_text(json.dumps(entries, indent=2), encoding="utf-8")
        print(
            f"wrote {len(entries)} candidate task(s) to {CORPUS_PATH}\n"
            "prompt and oracle_tests are EMPTY by design — fill them by hand before any run"
        )
    else:
        print(json.dumps(entries, indent=2))
