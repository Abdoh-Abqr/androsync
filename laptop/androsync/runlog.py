"""Plain-text run log: ``~/.local/state/androsync/last-run.log`` (leftover L1).

``androsync run`` (the real sync, not ``status``'s dry-run preview) and
``androsync undo``, plus every job the daemon runs, append their log lines
here too - so the most recent run's full log survives even after the CLI
process exits or the daemon's in-memory ``LogBuffer`` (``daemon/jobs.py``)
trims it. Truncated at the start of each run: this is always just the
*last* one, not a growing history.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Callable

LogFn = Callable[[str], None]


def log_path() -> Path:
    """``$XDG_STATE_HOME/androsync/last-run.log``, or the ``~/.local/state`` fallback."""
    xdg = os.environ.get("XDG_STATE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "state"
    return base / "androsync" / "last-run.log"


class RunLog:
    """Truncates ``last-run.log`` on construction, then appends each line written to it."""

    def __init__(self) -> None:
        path = log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = open(path, "w", encoding="utf-8")

    def write(self, line: str) -> None:
        self._file.write(line + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def tee(primary: LogFn | None, run_log: RunLog) -> LogFn:
    """Log callback that writes to ``run_log`` and, if given, also calls ``primary``."""

    def combined(line: str) -> None:
        run_log.write(line)
        if primary is not None:
            primary(line)

    return combined
