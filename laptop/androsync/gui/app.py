"""Tk GUI main window (`androsync gui`, task 31b).

Wires the three panels (service_panel/pairs_panel/log_panel) to
service_state.py for run-state detection and gui/backend.py for actions,
polling both on a plain `root.after` loop - there is no other event source
Tk needs to know about. See PLAN.md's Phase J design note for the full
button/state table this implements.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import tkinter as tk
from tkinter import messagebox

from androsync import config_edit, service, service_state
from androsync.browse import BrowseError, browse_remote
from androsync.config import Config, ConfigError, default_config_path, load_config
from androsync.daemon.watcher import Daemon
from androsync.gui.backend import Backend, reachable_client
from androsync.gui.dialogs import AboutDialog, PairEditorDialog, QrDialog
from androsync.gui.log_panel import LogPanel
from androsync.gui.logic import (
    close_window_decision,
    folder_change_prompt,
    should_attach,
    shutdown_steps,
    suggest_transport,
    sync_bar_view,
)
from androsync.gui.pairs_panel import PairsPanel
from androsync.gui.service_panel import ServicePanel
from androsync.transport.adb import Adb, AdbError, AdbTransport, resolve_device

_LOG = logging.getLogger(__name__)

_STATE_POLL_MS = 2000
_EVENT_POLL_MS = 150


class App:
    def __init__(self, root: tk.Tk, *, version: str) -> None:
        self._root = root
        self._version = version
        self._daemon: Daemon | None = None
        self._backend = Backend()
        self._state: service_state.ServiceState = service_state.ServiceState(mode="none")

        root.title("AndroSync")
        root.geometry("760x560")
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        # A Tk callback that raises (a button command, an `after` tick, ...) would
        # otherwise only ever print to stderr and vanish - log it like everything
        # else so it ends up in daemon.log too when "Run here" set that handler up.
        root.report_callback_exception = _log_tk_callback_exception

        menubar = tk.Menu(root)
        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="About AndroSync", command=lambda: AboutDialog(self._root, version=self._version))
        menubar.add_cascade(label="Help", menu=help_menu)
        root.config(menu=menubar)

        self._service_panel = ServicePanel(
            root,
            on_run_here=self._on_run_here,
            on_start_background=self._on_start_background,
            on_stop=self._on_stop,
            on_autostart_toggle=self._on_autostart_toggle,
            on_show_qr=self._on_show_qr,
        )
        self._service_panel.pack(fill="x", padx=8, pady=8)

        self._pairs_panel = PairsPanel(
            root,
            on_preview=self._on_preview,
            on_sync=self._on_sync,
            on_undo=self._on_undo,
            on_add=self._on_add,
            on_edit=self._on_edit,
            on_remove=self._on_remove,
        )
        self._pairs_panel.pack(fill="x", padx=8, pady=(0, 8))

        self._log_panel = LogPanel(root, on_stop=self._on_stop_sync)
        self._log_panel.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        self._poll_state()
        self._poll_events()

    # -- config loading -----------------------------------------------------

    def _config(self) -> Config | None:
        try:
            return load_config(default_config_path())
        except (ConfigError, OSError) as exc:
            self._log_panel.append(f"error: could not load config: {exc}")
            return None

    # -- periodic polling -----------------------------------------------------

    def _poll_state(self) -> None:
        config = self._config()
        self._state = service_state.detect_state(config)
        self._service_panel.update_state(self._state)
        if config is not None:
            self._pairs_panel.set_pairs(self._backend_safe_list_pairs(config))
            self._update_sync_bar(config)
        self._root.after(_STATE_POLL_MS, self._poll_state)

    def _update_sync_bar(self, config: Config) -> None:
        """Show the running sync, whoever started it, and follow its log if it is not ours (task 36b, issue #7)."""
        current_job = (self._state.ping or {}).get("current_job")
        self._log_panel.set_view(sync_bar_view(current_job, self._backend.running_directly))
        job_id = should_attach(current_job, self._backend.following_job_id, self._backend.busy)
        if job_id is not None:
            client = reachable_client(config, self._state)
            if client is not None:
                self._log_panel.append(f"-- following the sync that is already running ({job_id}) --")
                self._backend.follow(client, job_id)

    def _on_stop_sync(self) -> None:
        """The Stop button next to the log: stops the daemon's running job, or a sync running in this window."""
        config = self._config()
        if config is None:
            return
        current_job = (self._state.ping or {}).get("current_job")
        if not self._backend.running_directly:
            if current_job is None:
                return
            if not messagebox.askyesno("Stop the sync?", "Stop the running sync now?\n\nFiles already copied stay copied."):
                return
        self._backend.stop(reachable_client(config, self._state), (current_job or {}).get("id"))

    def _backend_safe_list_pairs(self, config: Config) -> list[dict]:
        from androsync.gui.backend import list_pairs

        try:
            return list_pairs(config)
        except Exception as exc:  # noqa: BLE001 - shown in the log, list just stays as-is
            self._log_panel.append(f"error: could not list pairs: {exc}")
            return []

    def _poll_events(self) -> None:
        try:
            while True:
                event = self._backend.events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self._root.after(_EVENT_POLL_MS, self._poll_events)

    def _handle_event(self, event: tuple) -> None:
        kind = event[0]
        if kind == "log":
            self._log_panel.append(event[1])
        elif kind == "done":
            _ok, message = event[1], event[2]
            self._log_panel.append(f"-- {message} --")
        elif kind == "run_here_result":
            daemon, error = event[1], event[2]
            if error is not None:
                messagebox.showerror("Could not start", error)
            else:
                self._daemon = daemon
                self._log_panel.append("started the daemon in this process")
        elif kind == "confirm":
            self._offer_large_change(event[1], event[2])
        elif kind == "close_done":
            error = event[1]
            if error is not None:
                messagebox.showerror("Could not hand over", error)
                self._service_panel.update_state(self._state)  # undo set_closing()'s disabled buttons
            else:
                self._safe_destroy()
        # "pairs_changed" needs no action here - the next state poll re-reads pairs.

    def _offer_large_change(self, pairs: list[str], problems: list[str]) -> None:
        """A sync was stopped because it would delete or copy a lot: show why and let the user decide (task 36d)."""
        text = "\n\n".join(problems) + "\n\nNothing was changed. Go ahead anyway?"
        if messagebox.askyesno("Large change - please confirm", text):
            self._run_confirmed(pairs)

    def _run_confirmed(self, pairs: list[str], tries: int = 0) -> None:
        # The stopped run's worker thread may still be finishing; wait for it instead of silently doing nothing.
        if self._backend.busy and tries < 30:
            self._root.after(200, lambda: self._run_confirmed(pairs, tries + 1))
            return
        config = self._config()
        if config is not None:
            self._backend.run(config, self._state, None, pairs=pairs, confirm_large=True)

    # -- service panel actions -----------------------------------------------

    def _on_run_here(self) -> None:
        config = self._config()
        if config is None:
            return
        if not config.service.token:
            messagebox.showerror("Not set up", "Run `androsync service install` first to generate a token.")
            return
        existing = service_state.ping(config.service.port, config.service.token)
        if existing is not None:
            messagebox.showinfo("Already running", "Something is already answering on this port.")
            return
        self._log_panel.append("starting the daemon in this process...")
        self._run_in_thread(lambda: self._run_here_impl(config))

    def _run_here_impl(self, config: Config) -> None:
        # Daemon.start() can block for up to 30s (Daemon._retry_missing_binds) if a
        # configured listen address - typically a Tailscale IP - is not up yet, so
        # this must never run on the Tk main thread (see FolderBrowserDialog's
        # docstring for the same rule, learned the hard way from a real hang).
        try:
            from androsync import __version__
            from androsync.cli import setup_logging

            setup_logging(file=True)
            daemon = Daemon(lambda: self._config() or config, __version__, config_path=default_config_path())
            daemon.start()
        except Exception as exc:  # noqa: BLE001 - reported to the log panel, not raised into Tk
            self._backend.events.put(("run_here_result", None, str(exc)))
            return
        self._backend.events.put(("run_here_result", daemon, None))

    def _run_in_thread(self, target) -> None:
        threading.Thread(target=target, daemon=True).start()

    def _on_start_background(self) -> None:
        self._run_in_thread(self._start_background_impl)

    def _start_background_impl(self) -> None:
        result = service_state.start_background(log=lambda line: self._backend.events.put(("log", line)))
        if result.returncode != 0:
            self._backend.events.put(
                ("log", "could not start the background service - run `androsync service install` first")
            )

    def _on_stop(self) -> None:
        if self._daemon is not None:
            self._daemon.stop()
            self._daemon = None
            self._log_panel.append("stopped the in-process daemon")
            return
        self._run_in_thread(
            lambda: service_state.stop_background(log=lambda line: self._backend.events.put(("log", line)))
        )

    def _on_autostart_toggle(self, on: bool) -> None:
        self._run_in_thread(
            lambda: service_state.set_autostart(on, log=lambda line: self._backend.events.put(("log", line)))
        )

    def _on_show_qr(self) -> None:
        config = self._config()
        if config is None or not config.service.token:
            messagebox.showerror("Not set up", "Run `androsync service install` first to generate a token.")
            return
        QrDialog(self._root, token=config.service.token, urls=service.build_urls(config))

    # -- pairs panel actions --------------------------------------------------

    def _on_preview(self, name: str | None) -> None:
        config = self._config()
        if config is None or self._backend.busy:
            return
        self._log_panel.append(f"-- preview {name or 'all pairs'} --")
        self._backend.run(config, self._state, name, dry_run=True)

    def _on_sync(self, name: str | None) -> None:
        config = self._config()
        if config is None or self._backend.busy:
            return
        self._log_panel.append(f"-- sync {name or 'all pairs'} --")
        self._backend.run(config, self._state, name, dry_run=False)

    def _on_undo(self, name: str) -> None:
        config = self._config()
        if config is None or self._backend.busy:
            return
        if not messagebox.askyesno("Undo last sync", f"Undo the most recent sync of {name!r}?"):
            return
        self._log_panel.append(f"-- undo {name} --")
        self._backend.undo(config, self._state, name, None)

    def _remote_list(self, config: Config, path: str):
        try:
            serial = resolve_device(config.device, wifi_target=config.wifi_target)
        except AdbError as exc:
            raise BrowseError(str(exc)) from exc
        return browse_remote(AdbTransport(Adb(serial=serial)), path)

    def _on_add(self) -> None:
        config = self._config()
        if config is None:
            return
        dialog = PairEditorDialog(
            self._root,
            existing=None,
            remote_list_fn=lambda p: self._remote_list(config, p),
            default_transport=suggest_transport(config.defaults.get("transport"), self._state.ping),
        )
        self._root.wait_window(dialog)
        if dialog.result is None:
            return
        self._save_pair(dialog.result, is_new=True)
        if dialog.make_default_transport:
            self._remember_default_transport(dialog.result["transport"])

    def _remember_default_transport(self, transport: str) -> None:
        config_path = default_config_path()
        try:
            config_edit.save_raw(config_path, config_edit.set_default_transport(config_edit.load_raw(config_path), transport))
        except ConfigError as exc:
            messagebox.showerror("Could not save the default", str(exc))
            return
        self._log_panel.append(f"new pairs will reach the phone via {transport} by default")

    def _on_edit(self, name: str) -> None:
        config = self._config()
        if config is None:
            return
        pair = next((p for p in config.pairs if p.name == name), None)
        if pair is None:
            return
        dialog = PairEditorDialog(
            self._root, existing=pair, remote_list_fn=lambda p: self._remote_list(config, p)
        )
        self._root.wait_window(dialog)
        if dialog.result is None:
            return
        old_folders = {"local": str(pair.local), "remote": pair.remote}
        if config_edit.folders_changed(old_folders, dialog.result) and not self._confirm_folder_change(name):
            self._log_panel.append(f"not saved: kept the old folders of {name!r}")
            return
        self._save_pair(dialog.result, is_new=False, original_name=name)

    def _confirm_folder_change(self, name: str) -> bool:
        """Ask before saving a pair whose folders changed, because saving resets its history (task 36d, issue #9)."""
        title, text = folder_change_prompt(name)
        return messagebox.askokcancel(title, text, icon="warning")

    def _save_pair(self, pair_dict: dict, *, is_new: bool, original_name: str | None = None) -> None:
        config_path = default_config_path()
        try:
            raw = config_edit.load_raw(config_path)
            if is_new:
                raw = config_edit.add_pair(raw, pair_dict)
                config_edit.save_raw(config_path, raw)
                state_reset = False
            else:
                # The dialog only knows some settings; keep the rest of the saved pair (ignore patterns, enabled,
                # the phone it is bound to, ...) instead of replacing the whole record with the dialog's few keys.
                saved = next((p for p in raw.get("pairs") or [] if p.get("name") == original_name), {})
                pair_dict = {**saved, **pair_dict}
                state_reset = config_edit.update_pair_and_state(config_path, original_name, pair_dict)
        except ConfigError as exc:
            messagebox.showerror("Could not save", str(exc))
            return
        self._log_panel.append(f"saved pair {pair_dict['name']!r}")
        if state_reset:
            self._log_panel.append(
                "folders changed: history reset, the next sync only adds files and deletes nothing"
            )

    def _on_remove(self, name: str) -> None:
        if not messagebox.askyesno("Remove pair", f"Remove {name!r}? Sync history is kept unless you ask otherwise."):
            return
        config_path = default_config_path()
        try:
            raw = config_edit.load_raw(config_path)
            raw = config_edit.remove_pair(raw, name)
            config_edit.save_raw(config_path, raw)
        except ConfigError as exc:
            messagebox.showerror("Could not remove", str(exc))
            return
        self._log_panel.append(f"removed pair {name!r}")

    # -- window close -----------------------------------------------------------

    def _on_close(self) -> None:
        decision = close_window_decision(self._state.mode)
        answer: bool | None = None
        if decision == "ask_stop_or_background":
            answer = messagebox.askyesnocancel(
                "Close AndroSync",
                "The service is running in this window. Stop it now?\n\n"
                "Yes: stop it.\nNo: keep it running in the background instead.\nCancel: keep the window open.",
            )

        steps = shutdown_steps(self._state.mode, answer)
        if not steps:
            return  # cancelled
        if steps == ["destroy"]:
            self._safe_destroy()
            return

        self._service_panel.set_closing()
        self._run_in_thread(lambda: self._run_shutdown_steps(steps))

    def _run_shutdown_steps(self, steps: list[str]) -> None:
        error: str | None = None
        for step in steps:
            if step == "destroy":
                continue
            try:
                if step == "start_background":
                    result = service_state.start_background(log=lambda line: self._backend.events.put(("log", line)))
                    if result.returncode != 0:
                        error = "Could not start the background service - run `androsync service install` first."
                        break
                elif step == "stop_daemon" and self._daemon is not None:
                    self._daemon.stop()
                    self._daemon = None
            except Exception as exc:  # noqa: BLE001 - reported on the main thread, not raised here
                error = str(exc)
                break
        self._backend.events.put(("close_done", error))

    def _safe_destroy(self) -> None:
        """`root.destroy()` must always actually end the process (task 32a: a shadowed
        `_name` attribute on a child widget once made this raise, leaving a dead window
        stuck open forever - `root.quit()` unblocks `mainloop()` regardless of why
        `destroy()` failed)."""
        try:
            self._root.destroy()
        except Exception:  # noqa: BLE001 - this is itself the last-resort fallback
            _LOG.exception("androsync gui: root.destroy() failed, forcing quit instead")
            self._root.quit()


def _log_tk_callback_exception(exc, val, tb) -> None:  # noqa: ANN001 - Tk's own callback signature
    _LOG.error("androsync gui: unhandled exception in a Tk callback", exc_info=(exc, val, tb))


def _log_uncaught_exception(exc_type, exc_value, exc_tb) -> None:  # noqa: ANN001
    _LOG.error("androsync gui: unhandled exception", exc_info=(exc_type, exc_value, exc_tb))
    sys.__excepthook__(exc_type, exc_value, exc_tb)


def main(version: str) -> int:
    """Build and run the window. `cli.cmd_gui` is what handles tkinter being missing."""
    sys.excepthook = _log_uncaught_exception
    root = tk.Tk()
    App(root, version=version)
    root.mainloop()
    return 0
