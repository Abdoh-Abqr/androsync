"""Tests for androsync.gui.logic (task 31b): pure helpers, no tkinter needed."""

from __future__ import annotations

from androsync.gui.logic import close_window_decision, format_pair_row, service_panel_view, shutdown_steps
from androsync.service_state import ServiceState


def test_service_panel_view_none_offers_run_here_and_start_background():
    view = service_panel_view(ServiceState(mode="none", autostart=False))
    assert view.status_text == "Not running"
    assert view.buttons == {"run_here", "start_background", "autostart"}
    assert view.autostart_checked is False


def test_service_panel_view_inprocess_offers_stop_not_start():
    view = service_panel_view(ServiceState(mode="inprocess", pid=1234, autostart=True))
    assert "here" in view.status_text.lower()
    assert view.buttons == {"stop", "autostart", "show_qr"}
    assert "run_here" not in view.buttons
    assert "start_background" not in view.buttons


def test_service_panel_view_systemd_shows_autostart_state_in_text():
    on_state = service_panel_view(ServiceState(mode="systemd", autostart=True, since="Sun 08:00"))
    off_state = service_panel_view(ServiceState(mode="systemd", autostart=False))
    assert "autostart on" in on_state.status_text
    assert "autostart off" in off_state.status_text
    assert on_state.buttons == {"stop", "autostart", "show_qr"}


def test_service_panel_view_other_process_offers_neither_stop_nor_start():
    view = service_panel_view(ServiceState(mode="other", pid=999))
    assert "999" in view.status_text
    assert view.buttons == {"autostart", "show_qr"}


def test_service_panel_view_shows_device_when_pinged():
    view = service_panel_view(
        ServiceState(mode="systemd", autostart=True, ping={"device": "SERIAL1", "transport": "usb"})
    )
    assert "SERIAL1" in view.detail_text
    assert "usb" in view.detail_text


def test_service_panel_view_shows_no_device_message_when_ping_has_none():
    view = service_panel_view(ServiceState(mode="systemd", ping={"device": None, "transport": None}))
    assert "no device" in view.detail_text.lower()


def test_format_pair_row_includes_all_fields():
    row = format_pair_row(
        {
            "name": "vault",
            "direction": "two-way",
            "last_run": "2026-09-13T12:00:00",
            "last_result": "copied_to_local=2",
            "enabled": True,
        }
    )
    assert row == ("vault", "two-way", "2026-09-13T12:00:00", "copied_to_local=2", "yes")


def test_format_pair_row_defaults_for_a_pair_never_synced():
    row = format_pair_row({"name": "fresh", "direction": "two-way", "enabled": True})
    assert row == ("fresh", "two-way", "never", "", "yes")


def test_format_pair_row_disabled_pair_shows_enabled_no():
    row = format_pair_row({"name": "paused-pair", "direction": "two-way", "enabled": False})
    assert row[0] == "paused-pair"
    assert row[-1] == "no"


def test_close_window_decision_asks_only_when_running_in_this_process():
    assert close_window_decision("inprocess") == "ask_stop_or_background"
    assert close_window_decision("systemd") == "just_close"
    assert close_window_decision("other") == "just_close"
    assert close_window_decision("none") == "just_close"


def test_shutdown_steps_non_inprocess_modes_just_destroy_regardless_of_answer():
    for mode in ("systemd", "other", "none"):
        for answer in (True, False, None):
            assert shutdown_steps(mode, answer) == ["destroy"]


def test_shutdown_steps_inprocess_cancelled_does_nothing():
    assert shutdown_steps("inprocess", None) == []


def test_shutdown_steps_inprocess_yes_stops_then_destroys():
    assert shutdown_steps("inprocess", True) == ["stop_daemon", "destroy"]


def test_shutdown_steps_inprocess_no_starts_background_before_stopping():
    # Order matters: start the background copy before stopping this one, so the
    # port is never briefly held by neither.
    assert shutdown_steps("inprocess", False) == ["start_background", "stop_daemon", "destroy"]


# ---------------------------------------------------------------------------
# the running sync, wherever it was started (task 36b, issue #7)
# ---------------------------------------------------------------------------

import pytest  # noqa: E402
from androsync.gui.logic import should_attach, sync_bar_view  # noqa: E402


def test_bar_says_no_sync_running_when_idle():
    view = sync_bar_view(None, running_directly=False)
    assert view.text == "No sync running" and view.can_stop is False and view.job_id is None


def test_bar_shows_a_sync_started_on_the_phone_with_its_latest_line_and_allows_stop():
    job = {"id": "ab12", "pairs": ["vault"], "kind": "sync", "state": "running", "last_line": "batch 2: 50 files, 12.0 MB"}
    view = sync_bar_view(job, running_directly=False)
    assert view.text == "Syncing vault: batch 2: 50 files, 12.0 MB"
    assert view.can_stop is True and view.stop_label == "Stop" and view.job_id == "ab12"


def test_bar_says_all_pairs_when_the_job_has_no_pair_names_and_undoing_for_undo_jobs():
    assert sync_bar_view({"id": "x", "pairs": [], "state": "running"}, False).text == "Syncing all pairs"
    assert sync_bar_view({"id": "x", "pairs": ["p"], "kind": "undo", "state": "running"}, False).text == "Undoing p"


def test_bar_shows_stopping_and_disables_the_button_while_cancelling():
    view = sync_bar_view({"id": "ab12", "pairs": ["vault"], "state": "cancelling"}, False)
    assert view.stop_label == "Stopping..." and view.can_stop is False


def test_bar_allows_stop_for_a_sync_running_in_this_window_without_a_daemon():
    view = sync_bar_view(None, running_directly=True)
    assert view.can_stop is True and view.job_id is None


@pytest.mark.parametrize(
    "job, following, busy, expected",
    [
        (None, None, False, None),  # nothing running
        ({"id": "a"}, None, False, "a"),  # started elsewhere: follow it
        ({"id": "a"}, "a", False, None),  # already following this one
        ({"id": "a"}, None, True, None),  # the window is busy with its own action
        ({"id": "b"}, "a", False, "b"),  # a different job than the one followed before
        ({}, None, False, None),  # a malformed entry without an id
    ],
)
def test_should_attach_decision_table(job, following, busy, expected):
    assert should_attach(job, following, busy) == expected


def test_the_folder_change_question_names_the_pair_and_says_what_saving_does():
    from androsync.gui.logic import folder_change_prompt

    title, text = folder_change_prompt("vault")
    assert title == "Folders changed"
    assert "'vault'" in text
    assert "forgets what this pair knew" in text and "deletes nothing" in text
    assert text.rstrip().endswith("Save anyway?")


def test_transport_label_names_how_the_pair_reaches_the_phone():
    from androsync.gui.logic import transport_label

    assert transport_label({"name": "a", "transport": "agent"}) == "phone app"
    assert transport_label({"name": "b", "transport": "adb"}) == "adb"
    assert transport_label({"name": "c"}) == "adb"  # an older daemon sends no transport


def test_suggest_transport_prefers_the_saved_default():
    from androsync.gui.logic import suggest_transport

    assert suggest_transport("agent", {"device": "ABC"}) == "agent"
    assert suggest_transport("adb", {"agent": "connected"}) == "adb"


def test_suggest_transport_follows_what_is_available_when_there_is_no_saved_default():
    from androsync.gui.logic import suggest_transport

    assert suggest_transport(None, {"device": "ABC", "devices": [], "agent": "connected"}) == "adb"
    assert suggest_transport(None, {"devices": [{"serial": "x", "state": "device"}], "agent": "absent"}) == "adb"
    assert suggest_transport(None, {"device": None, "devices": [], "agent": "connected"}) == "agent"
    assert suggest_transport(None, {"device": None, "devices": [{"serial": "x", "state": "unauthorized"}], "agent": "absent"}) == "adb"
    assert suggest_transport(None, None) == "adb"
    assert suggest_transport("nonsense", {"agent": "connected"}) == "agent"


def test_auto_label_says_what_automatic_sync_is_doing():
    from androsync.gui.logic import auto_label

    assert auto_label({"name": "a"}) == "off"
    assert auto_label({"name": "a", "auto": False}) == "off"
    assert auto_label({"name": "a", "auto": True, "auto_state": {"state": "watching"}}) == "on"
    assert auto_label({"name": "a", "auto": True, "auto_state": {"state": "waiting"}}) == "waiting"
    assert auto_label({"name": "a", "auto": True, "auto_state": {"state": "paused", "reason": "x"}}) == "paused"
    assert auto_label({"name": "a", "auto": True}) == "on"  # an older daemon sends no state
