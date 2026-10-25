"""Log panel widget (task 31b): a scrolled, read-only text area fed by `app.py`'s queue poller."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from tkinter.scrolledtext import ScrolledText
from typing import Callable

from androsync.gui.logic import SyncBarView


class LogPanel(ttk.LabelFrame):
    """The log, with a strip above it that shows the running sync and its Stop button (task 36b)."""

    def __init__(self, master: tk.Widget, *, on_stop: Callable[[], None] | None = None) -> None:
        super().__init__(master, text="Log")
        bar = ttk.Frame(self)
        bar.pack(fill="x", padx=8, pady=(8, 0))
        self._bar_text = tk.StringVar(value="No sync running")
        self._stop_label = tk.StringVar(value="Stop")
        ttk.Label(bar, textvariable=self._bar_text, anchor="w").pack(side="left", fill="x", expand=True)
        self._stop_button = ttk.Button(bar, textvariable=self._stop_label, command=on_stop or (lambda: None))
        self._stop_button.pack(side="right")
        self._stop_button.state(["disabled"])
        self.text = ScrolledText(self, height=12, state="disabled", wrap="word")
        self.text.pack(fill="both", expand=True, padx=8, pady=8)

    def set_view(self, view: SyncBarView) -> None:
        """Show the running sync (or "No sync running") and enable Stop only when there is something to stop."""
        shown = view.text if len(view.text) <= 110 else view.text[:107] + "..."
        self._bar_text.set(shown)
        self._stop_label.set(view.stop_label)
        self._stop_button.state(["!disabled"] if view.can_stop else ["disabled"])

    def append(self, line: str) -> None:
        self.text.configure(state="normal")
        self.text.insert("end", line + "\n")
        self.text.see("end")
        self.text.configure(state="disabled")

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.configure(state="disabled")
