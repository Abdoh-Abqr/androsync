"""Rules for folders on the phone (task 36c, issue #8)."""
import pytest

from androsync.phone_path import PhonePathError, check_remote, normalize_remote, remote_parent, validate_remote


@pytest.mark.parametrize(
    "given, expected",
    [
        ("/Internal storage/Inbox/", "/sdcard/Inbox"),
        ("/internal storage", "/sdcard"),
        ("/Internal Shared Storage/DCIM", "/sdcard/DCIM"),
        ("/storage/emulated/0/Documents", "/sdcard/Documents"),
        ("/storage/self/primary", "/sdcard"),
        ("/mnt/sdcard/x", "/sdcard/x"),
        ("//sdcard//a///b/", "/sdcard/a/b"),
        ("  /sdcard/x  ", "/sdcard/x"),
        ("/sdcard/", "/sdcard"),
        ("/", "/"),
        ("Documents", "Documents"),
        ("/storage/emulated/10/x", "/storage/emulated/10/x"),
        ("/storage/ABCD-1234/Music", "/storage/ABCD-1234/Music"),
    ],
)
def test_normalize_remote(given, expected):
    assert normalize_remote(given) == expected


@pytest.mark.parametrize(
    "path",
    ["/sdcard", "/sdcard/DCIM", "/storage/ABCD-1234", "/storage/ABCD-1234/Music", "/storage/emulated/10/Documents"],
)
def test_valid_phone_folders(path):
    assert validate_remote(path) is None


@pytest.mark.parametrize(
    "path, fragment",
    [
        ("/", "Pick a folder under /sdcard"),
        ("/data", "Pick a folder under /sdcard"),
        ("/system/app", "Pick a folder under /sdcard"),
        ("/storage", "Pick a folder under /sdcard"),
        ("/storage/emulated", "Pick a folder under /sdcard"),
        ("/storage/self", "Pick a folder under /sdcard"),
        ("/sdcard/Android/data", "belongs to other apps"),
        ("/sdcard/Android/data/com.example.app", "belongs to other apps"),
        ("/sdcard/Android/obb", "belongs to other apps"),
        ("/Documents/Obsidian", "Did you mean /sdcard/Documents/Obsidian?"),
        ("/sdcardx/foo", "Did you mean /sdcard/sdcardx/foo?"),
        ("Documents", "absolute path starting with /"),
        ("/sdcard/../data", "'..'"),
    ],
)
def test_refused_phone_folders_say_why(path, fragment):
    message = validate_remote(path)
    assert message is not None and fragment in message


def test_the_label_path_from_the_incident_is_fixed():
    assert check_remote("/Internal storage/Master_Inbox/") == "/sdcard/Master_Inbox"


def test_check_remote_raises_with_a_hint_for_the_filesystem_root():
    with pytest.raises(PhonePathError, match="internal storage"):
        check_remote("/")


@pytest.mark.parametrize(
    "path, parent",
    [
        ("/sdcard", None),
        ("/sdcard/DCIM", "/sdcard"),
        ("/sdcard/a/b", "/sdcard/a"),
        ("/storage/ABCD-1234", None),
        ("/storage/ABCD-1234/Music", "/storage/ABCD-1234"),
        ("/storage/emulated/10", None),
        ("/storage/emulated/10/Docs", "/storage/emulated/10"),
    ],
)
def test_remote_parent_stops_at_the_top_of_a_volume(path, parent):
    assert remote_parent(path) == parent
