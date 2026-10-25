"""Smoke test for the Tk GUI (task 31b): does the window even build?

Skipped outright if tkinter isn't importable, or if there is no display to
create a real ``Tk()`` root on (no X server, and no ``xvfb-run`` wrapping
the test run) - this only proves construction does not raise, not that any
interaction with the window works; that is what test_gui_logic.py's pure
helpers are unit-tested for.
"""

from __future__ import annotations

import gc
import subprocess

import pytest

tk = pytest.importorskip("tkinter")


def _make_root():
    try:
        return tk.Tk()
    except tk.TclError:
        pytest.skip("no display available to create a Tk root")


@pytest.fixture(autouse=True)
def _free_tk_objects_on_the_main_thread():
    """Free every Tk object here, on the thread that made it, as soon as each test is over.

    Python's garbage collector may otherwise free a leftover Tk variable or window later from ANY thread, for
    example a sync job thread of a later test, and Tcl then aborts the whole interpreter with "Tcl_AsyncDelete:
    async handler deleted by the wrong thread" - a rare, random crash of the test run.
    """
    yield
    gc.collect()


@pytest.fixture(autouse=True)
def _xdg_dirs(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "xdg-state"))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "xdg-runtime"))


@pytest.fixture(autouse=True)
def _no_real_systemctl(monkeypatch):
    """Never let a test touch the real systemd user session, even read-only."""
    from androsync import service_state

    monkeypatch.setattr(
        service_state.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 1, stdout="", stderr=""),
    )


def test_app_window_builds_without_a_config_file():
    from androsync.gui.app import App

    root = _make_root()
    try:
        App(root, version="0.1.0-test")
    finally:
        root.destroy()


def _make_pair_config():
    from pathlib import Path

    from androsync.config import PairConfig

    return PairConfig(
        name="p",
        local=Path("/tmp/does-not-need-to-exist"),
        remote="/sdcard/p",
        direction="two-way",
        conflict="newest-wins",
        keep_conflict_copies=True,
        propagate_deletes=True,
        mtime_tolerance_s=2,
        ignore=[],
    )


# task 32a: every dialog used to be at risk of this class of bug - a plain
# `self._name = tk.StringVar(...)` (or similar) shadows tkinter.Widget's own
# `_name` attribute, which `destroy()` relies on; the shadowed dialog then
# can never be destroyed (`TypeError: unhashable type: 'StringVar'`), which
# is exactly what left a real window stuck open. Assert the *real* internal
# `_name` (a Tcl widget path string) survived construction, and that
# destroy() actually works, for each dialog class.
def test_pair_editor_dialog_does_not_shadow_tk_internal_name_add_mode():
    from androsync.gui.dialogs import PairEditorDialog

    root = _make_root()
    try:
        dialog = PairEditorDialog(root, existing=None, remote_list_fn=None)
        assert isinstance(dialog._name, str)
        dialog.destroy()
    finally:
        root.destroy()


def test_pair_editor_dialog_does_not_shadow_tk_internal_name_edit_mode():
    from androsync.gui.dialogs import PairEditorDialog

    root = _make_root()
    try:
        dialog = PairEditorDialog(root, existing=_make_pair_config(), remote_list_fn=lambda p: None)
        assert isinstance(dialog._name, str)
        dialog.destroy()
    finally:
        root.destroy()


def test_folder_browser_dialog_does_not_shadow_tk_internal_name():
    from androsync.browse import BrowseResult
    from androsync.gui.dialogs import FolderBrowserDialog

    root = _make_root()
    try:
        dialog = FolderBrowserDialog(
            root, title="t", start="", list_fn=lambda p: BrowseResult(path=p, parent=None, entries=[], writable=False)
        )
        assert isinstance(dialog._name, str)
        dialog.destroy()
    finally:
        root.destroy()


def test_qr_dialog_does_not_shadow_tk_internal_name():
    from androsync.gui.dialogs import QrDialog

    root = _make_root()
    try:
        dialog = QrDialog(root, token="tok", urls=["http://127.0.0.1:8765"])
        assert isinstance(dialog._name, str)
        dialog.destroy()
    finally:
        root.destroy()


def test_about_dialog_shows_version_license_and_the_support_link():
    from androsync.gui.dialogs import SUPPORT_URL, AboutDialog

    root = _make_root()
    try:
        dialog = AboutDialog(root, version="9.9.9")
        assert isinstance(dialog._name, str)  # must not shadow Tk's internal name (task 32a)
        texts = []

        def collect(widget):
            for child in widget.winfo_children():
                try:
                    texts.append(str(child.cget("text")))
                except Exception:  # noqa: BLE001 - not every widget has text
                    pass
                collect(child)

        collect(dialog)
        joined = "\n".join(texts)
        assert "AndroSync 9.9.9" in joined and "GPL-3.0-or-later" in joined and SUPPORT_URL in joined
        dialog.destroy()
    finally:
        root.destroy()


def test_safe_destroy_falls_back_to_quit_when_destroy_raises():
    """task 32a: whatever breaks `root.destroy()` next time, the process must still exit."""
    from androsync.gui.app import App

    root = _make_root()
    try:
        app = App(root, version="0.1.0-test")

        quit_calls = []
        root.destroy = lambda: (_ for _ in ()).throw(TypeError("unhashable type: 'StringVar'"))
        root.quit = lambda: quit_calls.append(True)

        app._safe_destroy()
        assert quit_calls == [True]
    finally:
        try:
            root.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# editing a pair's folders asks BEFORE saving, because saving resets its history (task 36d, issue #9)
# ---------------------------------------------------------------------------


def _app_with_one_pair(tmp_path, monkeypatch):
    from androsync import state
    from androsync.config import default_config_path
    from androsync.gui import app as app_module
    from androsync.model import SnapshotEntry

    config_path = default_config_path()
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        "service: {token: t}\n"
        f"pairs:\n  - {{name: p, local: {tmp_path / 'inbox'}, remote: /sdcard/Inbox, direction: two-way}}\n"
    )
    state.save_snapshot("p", {"a.txt": SnapshotEntry(size=1, mtime_local=1.0, mtime_remote=1.0)})
    root = _make_root()
    app = app_module.App(root, version="0.1.0-test")
    return root, app, config_path


class _FakeDialog:
    """Stands in for PairEditorDialog: 'the user changed the fields and pressed Save'."""

    def __init__(self, result):
        self.result = result


def _edit(app, monkeypatch, result, answer):
    """Run App._on_edit with a scripted editor result and a scripted answer to the question."""
    from androsync.gui import app as app_module

    questions = []
    monkeypatch.setattr(app_module, "PairEditorDialog", lambda *a, **k: _FakeDialog(result))
    monkeypatch.setattr(app._root, "wait_window", lambda widget: None)
    monkeypatch.setattr(
        app_module.messagebox, "askokcancel", lambda title, text, **kw: questions.append((title, text)) or answer
    )
    app._on_edit("p")
    return questions


def test_changing_a_folder_asks_first_and_cancel_keeps_config_and_history(tmp_path, monkeypatch):
    from androsync import state

    root, app, config_path = _app_with_one_pair(tmp_path, monkeypatch)
    try:
        before = config_path.read_text()
        result = {"name": "p", "local": str(tmp_path / "inbox"), "remote": "/sdcard/Elsewhere", "direction": "two-way"}
        questions = _edit(app, monkeypatch, result, answer=False)

        assert [title for title, _ in questions] == ["Folders changed"]
        assert config_path.read_text() == before
        assert set(state.load_snapshot("p")) == {"a.txt"}
    finally:
        root.destroy()


def test_changing_a_folder_and_saying_ok_saves_and_resets_the_history(tmp_path, monkeypatch):
    import yaml

    from androsync import state

    root, app, config_path = _app_with_one_pair(tmp_path, monkeypatch)
    try:
        result = {"name": "p", "local": str(tmp_path / "inbox"), "remote": "/sdcard/Elsewhere", "direction": "two-way"}
        questions = _edit(app, monkeypatch, result, answer=True)

        assert len(questions) == 1
        assert yaml.safe_load(config_path.read_text())["pairs"][0]["remote"] == "/sdcard/Elsewhere"
        assert state.load_snapshot("p") == {}
    finally:
        root.destroy()


def test_editing_something_else_does_not_ask(tmp_path, monkeypatch):
    from androsync import state

    root, app, config_path = _app_with_one_pair(tmp_path, monkeypatch)
    try:
        result = {"name": "p", "local": str(tmp_path / "inbox"), "remote": "/sdcard/Inbox", "direction": "laptop-to-phone"}
        questions = _edit(app, monkeypatch, result, answer=False)

        assert questions == []
        assert "laptop-to-phone" in config_path.read_text()
        assert set(state.load_snapshot("p")) == {"a.txt"}  # history kept
    finally:
        root.destroy()
