"""Modal dialogs for the Tk GUI (task 31b): pair editor, phone folder browser, QR code.

Kept thin: business logic lives in `browse.py`/`config_edit.py`/`service.py`
(reused directly, not reimplemented) - these classes only arrange widgets
and turn button clicks into calls on those.
"""

from __future__ import annotations

import queue
import threading
from pathlib import Path
import tkinter as tk
import webbrowser
from tkinter import filedialog, messagebox, ttk
from typing import Callable

from androsync.browse import BrowseError, BrowseResult
from androsync import service, support
from androsync.config import DIRECTIONS, CONFLICT_POLICIES, TRANSPORT_MODES, PairConfig
from androsync.gui.logic import ANY_PHONE, ANY_PHONE_LABEL, direction_label
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
        laptop_name: str = "Laptop",
        phones: list[tuple[str, str]] | None = None,
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
        self._laptop_name = laptop_name
        # (id, label) per phone, "any" first; a pair bound to a phone that is not listed keeps it (Phase V2).
        self._phone_options = [(ANY_PHONE, ANY_PHONE_LABEL), *(phones or [])]
        bound = existing.device if existing and existing.device else ANY_PHONE
        if bound not in dict(self._phone_options):
            self._phone_options.append((bound, f"{bound} (not connected)"))
        self._phone_var = tk.StringVar(value=dict(self._phone_options)[bound])
        self._direction = existing.direction if existing else "two-way"
        self._direction_var = tk.StringVar()
        self._conflict_var = tk.StringVar(value=existing.conflict if existing else "newest-wins")
        self._transport_var = tk.StringVar(value=existing.transport if existing else default_transport)
        self._auto_var = tk.BooleanVar(value=existing.auto if existing else False)
        conditions = existing.run_conditions if existing else None
        self._wifi_only_var = tk.BooleanVar(value=bool(conditions and conditions.wifi_only))
        self._charging_only_var = tk.BooleanVar(value=bool(conditions and conditions.charging_only))
        # The dialog has no field for the time window; keep one that is already set instead of dropping it on save.
        self._schedule = (
            f"{conditions.schedule[0]:%H:%M}-{conditions.schedule[1]:%H:%M}" if conditions and conditions.schedule else None
        )
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
        ttk.Label(self, text="Phone").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        phone_box = ttk.Combobox(
            self, textvariable=self._phone_var, values=[label for _, label in self._phone_options], state="readonly"
        )
        phone_box.grid(row=row, column=1, columnspan=2, sticky="we", padx=8, pady=4)
        phone_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh_direction_labels())

        row += 1
        ttk.Label(self, text="Direction").grid(row=row, column=0, sticky="w", padx=8, pady=4)
        self._direction_box = ttk.Combobox(self, textvariable=self._direction_var, state="readonly")
        self._direction_box.grid(row=row, column=1, columnspan=2, sticky="we", padx=8, pady=4)
        self._direction_box.bind("<<ComboboxSelected>>", lambda _event: self._read_direction())
        self._refresh_direction_labels()

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
        ttk.Checkbutton(
            self,
            text="Sync automatically when files change (after you have synced this pair once by hand)",
            variable=self._auto_var,
        ).grid(row=row, column=0, columnspan=3, sticky="w", padx=8, pady=4)
        row += 1
        ttk.Checkbutton(self, text="...only on Wi-Fi", variable=self._wifi_only_var).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=28
        )
        row += 1
        ttk.Checkbutton(self, text="...only while the phone is charging", variable=self._charging_only_var).grid(
            row=row, column=0, columnspan=3, sticky="w", padx=28
        )

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

    def _selected_phone_id(self) -> str:
        label = self._phone_var.get()
        return next((pid for pid, text in self._phone_options if text == label), ANY_PHONE)

    def _phone_name_or_none(self) -> str | None:
        phone_id = self._selected_phone_id()
        return None if phone_id == ANY_PHONE else dict(self._phone_options)[phone_id]

    def _refresh_direction_labels(self) -> None:
        """Show the three directions with the names of this computer and the chosen phone (Phase V2)."""
        phone = self._phone_name_or_none()
        self._direction_box.configure(values=[direction_label(d, self._laptop_name, phone) for d in DIRECTIONS])
        self._direction_var.set(direction_label(self._direction, self._laptop_name, phone))

    def _read_direction(self) -> None:
        phone = self._phone_name_or_none()
        chosen = self._direction_var.get()
        self._direction = next((d for d in DIRECTIONS if direction_label(d, self._laptop_name, phone) == chosen), self._direction)

    def _conditions_dict(self) -> dict:
        """The run conditions the dialog sets; empty when none, so the saved pair stays tidy (Phase T3)."""
        conditions = {}
        if self._wifi_only_var.get():
            conditions["wifi_only"] = True
        if self._charging_only_var.get():
            conditions["charging_only"] = True
        if self._schedule:
            conditions["schedule"] = self._schedule
        return {"run_conditions": conditions} if conditions else {}

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
            "direction": self._direction,
            "device": self._selected_phone_id(),
            "conflict": self._conflict_var.get(),
            "transport": self._transport_var.get(),
            "auto": self._auto_var.get(),
            **self._conditions_dict(),
            "propagate_deletes": self._propagate_deletes_var.get(),
        }
        self.destroy()


class QrDialog(tk.Toplevel):
    """Shows the daemon token + every URL the phone app could use (`androsync qr`, but in a window)."""

    def __init__(
        self, master: tk.Widget, *, token: str, urls: list[str], on_new_token: Callable[[], None] | None = None
    ) -> None:
        super().__init__(master)
        self._on_new_token = on_new_token
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
            ttk.Label(self, text=f"{service.url_label(url)}: {url}").grid(row=2 + i, column=0, sticky="w", padx=8, pady=(4 if i == 0 else 0, 0))

        self._draw_qr(token, urls, row=2 + len(urls))

        buttons = ttk.Frame(self)
        buttons.grid(row=3 + len(urls), column=0, pady=8)
        if on_new_token is not None:
            ttk.Button(buttons, text="Make a new token...", command=self._ask_new_token).pack(side="left", padx=4)
        ttk.Button(buttons, text="Close", command=self.destroy).pack(side="left", padx=4)

    def _ask_new_token(self) -> None:
        if messagebox.askyesno(
            "Make a new token?",
            "The old token stops working at once. Every phone that has it must scan the new QR code again.",
            parent=self,
        ):
            self.destroy()
            if self._on_new_token is not None:
                self._on_new_token()

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


class SupportDialog(tk.Toplevel):
    """Help, "Report a problem": make the redacted log file and open the problem form (Phase S1)."""

    def __init__(self, master: tk.Widget, *, config_path: Path, version: str) -> None:
        super().__init__(master)
        self.title("Report a problem")
        self.transient(master)
        self.resizable(False, False)
        self._config_path = config_path
        self._version = version
        self._hide_names = tk.BooleanVar(value=True)

        ttk.Label(
            self,
            justify="left",
            text=(
                "1. Save the log file. It holds versions, the service state and the last log lines.\n"
                "   The token, addresses, phone serials and your name are replaced by placeholders.\n"
                "   Nothing is sent anywhere: you read the file and attach it yourself.\n"
                "2. Open the problem form, describe what happened, and attach the file."
            ),
        ).grid(row=0, column=0, columnspan=3, padx=16, pady=(16, 8), sticky="w")
        ttk.Checkbutton(
            self, text="Hide my pair names and folders (recommended)", variable=self._hide_names
        ).grid(row=1, column=0, columnspan=3, padx=16, sticky="w")
        ttk.Button(self, text="Save the log file...", command=self._save).grid(row=2, column=0, padx=16, pady=12)
        ttk.Button(self, text="Open the problem form", command=self._open_form).grid(row=2, column=1, pady=12)
        column = 2
        if support.SUPPORT_EMAIL:
            ttk.Button(self, text="Email us", command=self._email).grid(row=2, column=column, padx=8, pady=12)
            column += 1
        ttk.Button(self, text="Close", command=self.destroy).grid(row=3, column=0, columnspan=3, pady=(0, 16))

    def _save(self) -> None:
        path = filedialog.asksaveasfilename(
            parent=self,
            title="Save the log file",
            initialfile=support.default_bundle_path().name,
            defaultextension=".zip",
            filetypes=[("Zip file", "*.zip")],
        )
        if not path:
            return
        files = support.collect(self._config_path, hide_names=self._hide_names.get())
        out = support.write_bundle(files, Path(path))
        messagebox.showinfo(
            "Saved",
            f"Saved {out}\n\n{support.describe(files)}\n\nOpen it and read it before you attach it.",
            parent=self,
        )

    def _open_form(self) -> None:
        webbrowser.open(support.issue_url_with(self._version))

    def _email(self) -> None:
        webbrowser.open(f"mailto:{support.SUPPORT_EMAIL}?subject=AndroSync%20{self._version}%20problem")

