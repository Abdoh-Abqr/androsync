"""Service panel widget (task 31b): status line + buttons.

Purely presentational - `service_panel_view` (gui/logic.py) decides what to
show and which buttons are enabled; this class only draws that and forwards
clicks to the callbacks `app.py` wires up.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Callable

from androsync.gui.logic import ButtonId, service_panel_view
from androsync.service_state import ServiceState


class ServicePanel(ttk.LabelFrame):
    def __init__(
        self,
        master: tk.Widget,
        *,
        on_run_here: Callable[[], None],
        on_start_background: Callable[[], None],
        on_stop: Callable[[], None],
        on_autostart_toggle: Callable[[bool], None],
        on_show_qr: Callable[[], None],
    ) -> None:
        super().__init__(master, text="Service")
        self.status_var = tk.StringVar(value="Checking...")
        self.detail_var = tk.StringVar(value="")

        ttk.Label(self, textvariable=self.status_var, font=("", 10, "bold")).grid(
            row=0, column=0, sticky="w", padx=8, pady=(8, 0)
        )
        ttk.Label(self, textvariable=self.detail_var).grid(row=1, column=0, sticky="w", padx=8, pady=(0, 8))

        buttons = ttk.Frame(self)
        buttons.grid(row=0, column=1, rowspan=2, sticky="e", padx=8, pady=8)

        self._autostart_var = tk.BooleanVar(value=False)
        self._suppress_autostart_callback = False
        self._on_autostart_toggle = on_autostart_toggle

        self._buttons: dict[ButtonId, ttk.Widget] = {
            "run_here": ttk.Button(buttons, text="Run here", command=on_run_here),
            "start_background": ttk.Button(buttons, text="Start in background", command=on_start_background),
            "stop": ttk.Button(buttons, text="Stop", command=on_stop),
            "autostart": ttk.Checkbutton(
                buttons, text="Start at boot", variable=self._autostart_var, command=self._autostart_changed
            ),
            "show_qr": ttk.Button(buttons, text="Show QR", command=on_show_qr),
        }
        for widget in self._buttons.values():
            widget.pack(side="left", padx=4)

        self.grid_columnconfigure(0, weight=1)

    def _autostart_changed(self) -> None:
        if not self._suppress_autostart_callback:
            self._on_autostart_toggle(self._autostart_var.get())

    def set_closing(self) -> None:
        """Disable every button and show "Stopping..." while window-close shutdown runs (task 32a)."""
        self.status_var.set("Stopping...")
        self.detail_var.set("")
        for widget in self._buttons.values():
            widget.state(["disabled"])

    def update_state(self, state: ServiceState) -> None:
        view = service_panel_view(state)
        self.status_var.set(view.status_text)
        self.detail_var.set(view.detail_text)

        self._suppress_autostart_callback = True
        try:
            self._autostart_var.set(view.autostart_checked)
        finally:
            self._suppress_autostart_callback = False

        for name, widget in self._buttons.items():
            widget.state(["!disabled"] if name in view.buttons else ["disabled"])
