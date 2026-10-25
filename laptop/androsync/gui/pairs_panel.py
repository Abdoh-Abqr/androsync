"""Pairs panel widget (task 31b): a table of configured pairs and their actions."""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from androsync.gui.logic import format_pair_row, transport_label

_COLUMNS = ("direction", "via", "last_run", "last_result", "enabled")
_HEADINGS = {"direction": "Direction", "via": "Via", "last_run": "Last run", "last_result": "Last result", "enabled": "Enabled"}


class PairsPanel(ttk.LabelFrame):
    def __init__(
        self,
        master: tk.Widget,
        *,
        on_preview: Callable[[str | None], None],
        on_sync: Callable[[str | None], None],
        on_undo: Callable[[str], None],
        on_add: Callable[[], None],
        on_edit: Callable[[str], None],
        on_remove: Callable[[str], None],
    ) -> None:
        super().__init__(master, text="Pairs")

        self.tree = ttk.Treeview(self, columns=_COLUMNS, show="tree headings", selectmode="browse", height=8)
        self.tree.heading("#0", text="Name")
        for col in _COLUMNS:
            self.tree.heading(col, text=_HEADINGS[col])
            self.tree.column(col, width=110)
        # task 32c: a pair whose last attempt failed (state.save_pair_meta writes
        # `last_result: "FAILED: ..."`, see daemon/jobs.py) shows red, matching
        # the Android app's Pairs screen.
        self.tree.tag_configure("failed", foreground="#c0392b")
        self.tree.grid(row=0, column=0, columnspan=7, sticky="nsew", padx=8, pady=8)

        actions: list[tuple[str, Callable[[], None]]] = [
            ("Preview", lambda: self._with_selection(on_preview)),
            ("Sync", lambda: self._with_selection(on_sync)),
            ("Sync all", lambda: on_sync(None)),
            ("Undo last...", lambda: self._with_required_selection(on_undo)),
            ("Add...", on_add),
            ("Edit...", lambda: self._with_required_selection(on_edit)),
            ("Remove...", lambda: self._with_required_selection(on_remove)),
        ]
        for col, (label, command) in enumerate(actions):
            ttk.Button(self, text=label, command=command).grid(row=1, column=col, padx=4, pady=(0, 8), sticky="w")

        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(0, weight=1)

    def _selected_name(self) -> str | None:
        selection = self.tree.selection()
        return selection[0] if selection else None

    def _with_selection(self, callback: Callable[[str | None], None]) -> None:
        callback(self._selected_name())

    def _with_required_selection(self, callback: Callable[[str], None]) -> None:
        name = self._selected_name()
        if name is not None:
            callback(name)

    def set_pairs(self, pairs: list[dict]) -> None:
        """Replace the table's contents, keeping the current selection if it still exists."""
        selected = self._selected_name()
        self.tree.delete(*self.tree.get_children())
        for pair in pairs:
            name, direction, last_run, last_result, enabled = format_pair_row(pair)
            tags = ("failed",) if last_result.startswith("FAILED:") else ()
            self.tree.insert(
                "",
                "end",
                iid=pair["name"],
                text=name,
                values=(direction, transport_label(pair), last_run, last_result, enabled),
                tags=tags,
            )
        if selected is not None and self.tree.exists(selected):
            self.tree.selection_set(selected)
