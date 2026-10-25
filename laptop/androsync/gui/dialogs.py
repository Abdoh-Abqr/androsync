"""Modal dialogs for the Tk GUI (task 31b): pair editor, phone folder browser, QR code.

Kept thin: business logic lives in `browse.py`/`config_edit.py`/`service.py`
(reused directly, not reimplemented) - these classes only arrange widgets
and turn button clicks into calls on those.
"""

from __future__ import annotations

import queue
import threading
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from androsync.browse import BrowseError, BrowseResult
from androsync import service
from androsync.config import DIRECTIONS, CONFLICT_POLICIES, TRANSPORT_MODES, PairConfig
from androsync.phone_path import PhonePathError, check_remote, normalize_remote, validate_remote

_FETCH_POLL_MS = 80


class FolderBrowserDialog(tk.Toplevel):
    """Modal directory browser over any `list_fn(path) -> BrowseResult` (local or remote).

    ``list_fn`` for the phone side shells out to ``adb`` (see `App._remote_list`),
    which can take a long time or hang outright against a stale/dropped
    connection - `Adb.run`'s own timeout defaults to 600s, sized for large
    file transfers, not a folder listing. Every call to it here runs on a
    background thread and reports back through a queue polled via
    ``self.after`` (same pattern as `gui/backend.py`), so a slow or stuck
    phone freezes this dialog's listing, never the whole window.
    """

    def __init__(self, master: tk.Widget, *, title: str, start: str, list_fn: Callable[[str], BrowseResult]) -> None:
        super().__init__(master)
        self.title(title)
        self.transient(master)
        # grab_set() can raise "grab failed: window not viewable" if called
        # before the window manager has actually mapped this Toplevel yet
        # (seen as a rare flake even under the test suite's own rapid
        # create/destroy cycle) - wait_visibility() blocks until it has.
        self.wait_visibility()
        self.grab_set()
        self.geometry("420x360")

        self._list_fn = list_fn
        self.result: str | None = None
        self._path = start
        self._events: queue.Queue[tuple[str, object]] = queue.Queue()

        self._path_var = tk.StringVar(value=start)
        ttk.Label(self, textvariable=self._path_var).pack(fill="x", padx=8, pady=(8, 0))

        self._listbox = tk.Listbox(self)
        self._listbox.pack(fill="both", expand=True, padx=8, pady=8)
        self._listbox.bind("<Double-Button-1>", lambda _evt: self._enter_selected())

        buttons = ttk.Frame(self)
        buttons.pack(fill="x", padx=8, pady=(0, 8))
        self._up_button = ttk.Button(buttons, text="Up", command=self._go_up)
        self._up_button.pack(side="left")
        self._choose_button = ttk.Button(buttons, text="Choose this folder", command=self._choose)
        self._choose_button.pack(side="right")
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=4)

        self._current: BrowseResult | None = None
        self._refresh(start)

    def _refresh(self, path: str) -> None:
        self._up_button.state(["disabled"])
        self._choose_button.state(["disabled"])
        self._path_var.set(f"Loading {path or '(roots)'}...")
        self._listbox.delete(0, "end")
        threading.Thread(target=self._fetch, args=(path,), daemon=True).start()
        self.after(_FETCH_POLL_MS, self._poll)

    def _fetch(self, path: str) -> None:
        try:
            self._events.put(("ok", self._list_fn(path)))
        except BrowseError as exc:
            self._events.put(("error", str(exc)))

    def _poll(self) -> None:
        if not self.winfo_exists():
            return  # cancelled (destroy()) while the background fetch was still in flight
        try:
            kind, payload = self._events.get_nowait()
        except queue.Empty:
            self.after(_FETCH_POLL_MS, self._poll)
            return

        self._up_button.state(["!disabled"])
        self._choose_button.state(["!disabled"])
        if kind == "error":
            messagebox.showerror("Browse failed", str(payload), parent=self)
            return

        self._current = payload
        self._path = self._current.path
        self._path_var.set(self._path or "(roots)")
        self._listbox.delete(0, "end")
        for entry in self._current.entries:
            if entry.is_dir:
                self._listbox.insert("end", entry.name + "/")

    def _enter_selected(self) -> None:
        selection = self._listbox.curselection()
        if not selection or self._current is None:
            return
        name = self._listbox.get(selection[0]).rstrip("/")
        entry = next((e for e in self._current.entries if e.name == name), None)
        if entry is None:
            return
        path = f"{self._path.rstrip('/')}/{name}" if self._path else name
        self._refresh(path)

    def _go_up(self) -> None:
        if self._current is not None and self._current.parent is not None:
            self._refresh(self._current.parent)

    def _choose(self) -> None:
        self.result = self._path
        self.destroy()


class PairEditorDialog(tk.Toplevel):
    """Add/edit a pair: name, both folders, direction, conflict policy, propagate deletes.

    ``remote_list_fn`` browses the phone (``browse_remote`` bound to a live
    ``AdbTransport``, same as the daemon's own ``/v1/browse``); the local
    side uses the plain OS folder picker, matching PLAN.md's design note.
    """

    def __init__(
        self,
        master: tk.Widget,
        *,
        existing: PairConfig | None,
        remote_list_fn: Callable[[str], BrowseResult] | None,
        default_transport: str = "adb",
    ) -> None:
        super().__init__(master)
        #: Set when a new pair's dialog is saved with "use this for new pairs": the caller stores it in the config.
        self.make_default_transport = False
        self.title("Edit pair" if existing else "Add pair")
        self.transient(master)
        # grab_set() can raise "grab failed: window not viewable" if called
        # before the window manager has actually mapped this Toplevel yet
        # (seen as a rare flake even under the test suite's own rapid
        # create/destroy cycle) - wait_visibility() blocks until it has.
        self.wait_visibility()
        self.grab_set()
        self.resizable(False, False)

        self._remote_list_fn = remote_list_fn
        self.result: dict | None = None

        # Suffixed `_var`: a plain `self._name = tk.StringVar(...)` here used to shadow
        # tkinter.Widget's own internal `_name` attribute (the Tcl widget path every
        # widget needs for bookkeeping) - harmless until something tried to destroy
        # this dialog, at which point `Misc.destroy()`'s `self._name in self.master.children`
        # raised `TypeError: unhashable type: 'StringVar'` and the whole window got
        # stuck undestroyable (task 32a: a real stuck-open-window bug this caused).
        self._name_var = tk.StringVar(value=existing.name if existing else "")
        self._local_var = tk.StringVar(value=str(existing.local) if existing else "")
        self._remote_var = tk.StringVar(value=existing.remote if existing else "")
        self._direction_var = tk.StringVar(value=existing.direction if existing else "two-way")
        self._conflict_var = tk.StringVar(value=existing.conflict if existing else "newest-wins")
        self._transport_var = tk.StringVar(value=existing.transport if existing else default_transport)
        self._make_default_var = tk.BooleanVar(value=False)
        self._propagate_deletes_var = tk.BooleanVar(value=existing.propagate_deletes if existing else True)

        row = 0
        ttk.Label(self, text="Name").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        # Renaming an existing pair is supported (task 29c), so this field is
        # never disabled - unlike the Android app's editor before that task.
        ttk.Entry(self, textvariable=self._name_var, width=36).grid(
            row=row, column=1, columnspan=2, sticky="we", padx=8, pady=4
        )

        row += 1
        ttk.Label(self, text="Laptop folder").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(self, textvariable=self._local_var, width=36).grid(row=row, column=1, sticky="we", padx=8, pady=4)
        ttk.Button(self, text="Browse...", command=self._browse_local).grid(row=row, column=2, padx=8, pady=4)

        row += 1
        ttk.Label(self, text="Phone folder").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        ttk.Entry(self, textvariable=self._remote_var, width=36).grid(row=row, column=1, sticky="we", padx=8, pady=4)
        browse_remote_btn = ttk.Button(self, text="Browse...", command=self._browse_remote)
        browse_remote_btn.grid(row=row, column=2, padx=8, pady=4)
        if remote_list_fn is None:
            browse_remote_btn.state(["disabled"])

        row += 1
        ttk.Label(self, text="Direction").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        ttk.Combobox(self, textvariable=self._direction_var, values=DIRECTIONS, state="readonly").grid(
            row=row, column=1, columnspan=2, sticky="we", padx=8, pady=4
        )

        row += 1
        ttk.Label(self, text="Reach the phone via").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        ttk.Combobox(self, textvariable=self._transport_var, values=TRANSPORT_MODES, state="readonly").grid(
            row=row, column=1, columnspan=2, sticky="we", padx=8, pady=4
        )

        if existing is None:
            row += 1
            ttk.Checkbutton(
                self, text="Use this for new pairs from now on", variable=self._make_default_var
            ).grid(row=row, column=1, columnspan=2, sticky="w", padx=8)

        row += 1
        ttk.Label(self, text="Conflict policy").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        ttk.Combobox(self, textvariable=self._conflict_var, values=CONFLICT_POLICIES, state="readonly").grid(
            row=row, column=1, columnspan=2, sticky="we", padx=8, pady=4
        )

        row += 1
        ttk.Checkbutton(self, text="Propagate deletes", variable=self._propagate_deletes_var).grid(
            row=row, column=1, sticky="w", padx=8, pady=4
        )

        row += 1
        buttons = ttk.Frame(self)
        buttons.grid(row=row, column=0, columnspan=3, sticky="e", padx=8, pady=8)
        ttk.Button(buttons, text="Cancel", command=self.destroy).pack(side="right", padx=4)
        ttk.Button(buttons, text="Save", command=self._save).pack(side="right")

    def _browse_local(self) -> None:
        chosen = filedialog.askdirectory(parent=self, initialdir=self._local_var.get() or None)
        if chosen:
            self._local_var.set(chosen)

    def _browse_remote(self) -> None:
        if self._remote_list_fn is None:
            return
        typed = normalize_remote(self._remote_var.get())
        start = typed if typed and validate_remote(typed) is None else "/sdcard"
        dialog = FolderBrowserDialog(
            self, title="Phone folder", start=start, list_fn=self._remote_list_fn
        )
        self.wait_window(dialog)
        if dialog.result:
            self._remote_var.set(dialog.result)

    def _save(self) -> None:
        name = self._name_var.get().strip()
        local = self._local_var.get().strip()
        remote = self._remote_var.get().strip()
        if not name:
            messagebox.showerror("Missing name", "Name is required.", parent=self)
            return
        if not local:
            messagebox.showerror("Missing folder", "Laptop folder is required.", parent=self)
            return
        try:
            remote = check_remote(remote)
        except PhonePathError as exc:
            messagebox.showerror("Bad phone folder", str(exc), parent=self)
            return

        self.make_default_transport = bool(self._make_default_var.get())
        self.result = {
            "name": name,
            "local": local,
            "remote": remote,
            "direction": self._direction_var.get(),
            "conflict": self._conflict_var.get(),
            "transport": self._transport_var.get(),
            "propagate_deletes": self._propagate_deletes_var.get(),
        }
        self.destroy()


class QrDialog(tk.Toplevel):
    """Shows the daemon token + every URL the phone app could use (`androsync qr`, but in a window)."""

    def __init__(self, master: tk.Widget, *, token: str, urls: list[str]) -> None:
        super().__init__(master)
        self.title("Connect the phone app")
        self.transient(master)
        # grab_set() can raise "grab failed: window not viewable" if called
        # before the window manager has actually mapped this Toplevel yet
        # (seen as a rare flake even under the test suite's own rapid
        # create/destroy cycle) - wait_visibility() blocks until it has.
        self.wait_visibility()
        self.grab_set()
        self.resizable(False, False)

        ttk.Label(self, text="Token:").grid(row=0, column=0, sticky="w", padx=8, pady=(8, 0))
        token_entry = ttk.Entry(self, width=48)
        token_entry.insert(0, token)
        token_entry.configure(state="readonly")
        token_entry.grid(row=1, column=0, sticky="we", padx=8)

        for i, url in enumerate(urls):
            ttk.Label(self, text=f"URL: {url}").grid(row=2 + i, column=0, sticky="w", padx=8, pady=(4 if i == 0 else 0, 0))

        self._draw_qr(token, urls, row=2 + len(urls))

        ttk.Button(self, text="Close", command=self.destroy).grid(row=3 + len(urls), column=0, pady=8)

    def _draw_qr(self, token: str, urls: list[str], *, row: int) -> None:
        try:
            import json

            import qrcode
        except ImportError:
            ttk.Label(self, text="(install androsync[qr] for a scannable code)").grid(
                row=row, column=0, padx=8, pady=8
            )
            return

        payload = service.qr_payload(urls, token)
        qr = qrcode.QRCode(border=1, box_size=6)
        qr.add_data(payload)
        qr.make(fit=True)
        matrix = qr.get_matrix()
        size = len(matrix) * 6
        canvas = tk.Canvas(self, width=size, height=size, background="white")
        canvas.grid(row=row, column=0, padx=8, pady=8)
        for y, cells in enumerate(matrix):
            for x, cell in enumerate(cells):
                if cell:
                    canvas.create_rectangle(x * 6, y * 6, x * 6 + 6, y * 6 + 6, fill="black", width=0)


SUPPORT_URL = "https://buymeacoffee.com/abqr"
REPO_URL = "https://github.com/Abdoh-Abqr/androsync"


class AboutDialog(tk.Toplevel):
    """Version, license and the optional donation link (task 35e, issue #25)."""

    def __init__(self, master: tk.Widget, *, version: str) -> None:
        super().__init__(master)
        self.title("About AndroSync")
        self.transient(master)
        self.resizable(False, False)

        ttk.Label(self, text=f"AndroSync {version}", font=("TkDefaultFont", 12, "bold")).grid(
            row=0, column=0, padx=16, pady=(16, 4)
        )
        ttk.Label(
            self,
            text="Keeps folders on your laptop and your Android phone in sync.\n"
            "Free software, licensed GPL-3.0-or-later. Nothing leaves your own devices.",
            justify="center",
        ).grid(row=1, column=0, padx=16, pady=4)
        ttk.Label(
            self, text="If it saves you time you can buy the developer a coffee. This is optional."
        ).grid(row=2, column=0, padx=16, pady=(8, 2))
        link = ttk.Label(self, text=SUPPORT_URL, foreground="#1a5fb4", cursor="hand2")
        link.grid(row=3, column=0, padx=16)
        link.bind("<Button-1>", lambda _event: webbrowser.open(SUPPORT_URL))
        repo = ttk.Label(self, text=f"Source code and guides: {REPO_URL}", foreground="#1a5fb4", cursor="hand2")
        repo.grid(row=4, column=0, padx=16, pady=(8, 0))
        repo.bind("<Button-1>", lambda _event: webbrowser.open(REPO_URL))
        ttk.Button(self, text="Close", command=self.destroy).grid(row=5, column=0, pady=16)

