"""Optional per-pair version control (``versioning: git``, PLAN.md task 26).

After a successful non-dry run, commits the local side of a pair into a
plain local git repo - a safety net independent of the sync itself (e.g.
to recover a note some other sync accidentally overwrote), never pushed
anywhere. ``.git/**`` is already in the default ignore list, so the repo
itself never syncs to the phone.
"""

from __future__ import annotations

import subprocess
from datetime import datetime
from pathlib import Path

from androsync.config import PairConfig
from androsync.model import Summary

_GITIGNORE_LINES = [".obsidian/workspace*", ".trash/"]


class VersioningError(Exception):
    """Raised when a git command fails. Callers should log and continue - a
    versioning failure must never turn an otherwise-successful sync into a
    failed one."""


def _run(argv: list[str], cwd: Path) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise VersioningError(f"`{' '.join(argv)}` failed: {exc}") from exc


def _ensure_repo(local: Path) -> None:
    if (local / ".git").exists():
        return
    result = _run(["git", "init", "-q"], local)
    if result.returncode != 0:
        raise VersioningError(f"git init failed: {result.stderr.strip()}")
    gitignore = local / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("\n".join(_GITIGNORE_LINES) + "\n")


def commit_if_configured(pair: PairConfig, summary: Summary, when: datetime) -> bool:
    """Commit ``pair.local`` into its git repo if ``pair.versioning == "git"``.

    No-op (returns False) for any other ``versioning`` value, or if there
    is nothing staged to commit. Initializes the repo (and a starter
    ``.gitignore``) on first use. Never pushes. Raises
    :class:`VersioningError` on any git failure - callers decide whether
    that should just be logged (it must never turn an otherwise-successful
    sync into a failed one).
    """
    if pair.versioning != "git":
        return False

    local = Path(pair.local)
    _ensure_repo(local)

    add_result = _run(["git", "add", "-A"], local)
    if add_result.returncode != 0:
        raise VersioningError(f"git add failed: {add_result.stderr.strip()}")

    staged = _run(["git", "diff", "--cached", "--quiet"], local)
    if staged.returncode == 0:
        return False  # nothing staged: no changes since the last commit

    message = f"androsync {when.isoformat(timespec='seconds')}: {summary}"
    commit_result = _run(["git", "commit", "-q", "-m", message], local)
    if commit_result.returncode != 0:
        raise VersioningError(f"git commit failed: {commit_result.stderr.strip()}")
    return True


def history(local: Path, limit: int = 20) -> str:
    """``git log --oneline -n <limit>`` output for ``local``, or "" if it has no repo."""
    if not (local / ".git").exists():
        return ""
    result = _run(["git", "log", "--oneline", "-n", str(limit)], local)
    if result.returncode != 0:
        raise VersioningError(f"git log failed: {result.stderr.strip()}")
    return result.stdout
