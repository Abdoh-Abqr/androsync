"""Tests for androsync.browse (local os.scandir browsing + remote AdbTransport.ls)."""

from __future__ import annotations

import pytest

from androsync.browse import (
    BrowseForbidden,
    browse_local,
    browse_remote,
    mkdir_remote,
    mkdir_local,
)


def test_browse_local_empty_path_lists_roots(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    result = browse_local("", [str(root)])
    assert result.path == ""
    assert result.parent is None
    assert [e.name for e in result.entries] == [str(root)]
    assert result.entries[0].is_dir is True


def test_browse_local_skips_roots_that_do_not_exist(tmp_path):
    missing = tmp_path / "missing"
    result = browse_local("", [str(missing)])
    assert result.entries == []


def test_browse_local_lists_directory_dirs_first_case_insensitive(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "b.txt").write_text("x")
    (root / "A dir").mkdir()
    (root / "a.txt").write_text("x")

    result = browse_local(str(root), [str(root)])

    names = [e.name for e in result.entries]
    assert names == ["A dir", "a.txt", "b.txt"]
    assert result.entries[0].is_dir is True
    assert result.entries[1].is_dir is False


def test_browse_local_flags_hidden_entries(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / ".obsidian").mkdir()
    (root / "notes.md").write_text("x")

    result = browse_local(str(root), [str(root)])

    hidden = {e.name: e.hidden for e in result.entries}
    assert hidden[".obsidian"] is True
    assert hidden["notes.md"] is False


def test_browse_local_outside_roots_raises_forbidden(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    with pytest.raises(BrowseForbidden):
        browse_local(str(outside), [str(allowed)])


def test_browse_local_root_itself_is_allowed(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    result = browse_local(str(root), [str(root)])
    assert result.path == str(root)


def test_browse_local_subdir_of_root_is_allowed(tmp_path):
    root = tmp_path / "vault"
    sub = root / "notes"
    sub.mkdir(parents=True)
    result = browse_local(str(sub), [str(root)])
    assert result.path == str(sub)


def test_browse_local_parent_is_none_at_filesystem_root(tmp_path):
    result = browse_local("/", ["/"])
    assert result.parent is None


def test_browse_local_parent_is_set_for_a_subdirectory(tmp_path):
    root = tmp_path / "vault"
    sub = root / "notes"
    sub.mkdir(parents=True)
    result = browse_local(str(sub), [str(root.parent)])
    assert result.parent == str(root)


def test_mkdir_local_creates_directory_within_roots(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    new_dir = root / "new" / "nested"

    mkdir_local(str(new_dir), [str(root)])

    assert new_dir.is_dir()


def test_mkdir_local_outside_roots_raises_forbidden(tmp_path):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside" / "new"

    with pytest.raises(BrowseForbidden):
        mkdir_local(str(outside), [str(allowed)])


class _FakeAdbTransport:
    def __init__(self, listing: dict[str, list[tuple[str, bool, int]]]) -> None:
        self._listing = listing

    def ls(self, path: str) -> list[tuple[str, bool, int]]:
        return self._listing[path]


def test_browse_remote_defaults_to_sdcard_when_path_empty():
    transport = _FakeAdbTransport({"/sdcard": [("DCIM", True, 4096), ("a.txt", False, 10)]})
    result = browse_remote(transport, "")
    assert result.path == "/sdcard"
    assert [e.name for e in result.entries] == ["DCIM", "a.txt"]


def test_browse_remote_computes_parent():
    transport = _FakeAdbTransport({"/sdcard/DCIM": [("Camera", True, 4096)]})
    result = browse_remote(transport, "/sdcard/DCIM")
    assert result.parent == "/sdcard"


def test_browse_remote_sdcard_is_the_top():
    transport = _FakeAdbTransport({"/sdcard": []})
    result = browse_remote(transport, "/sdcard")
    assert result.parent is None


def test_browse_remote_sd_card_root_is_the_top_and_subfolders_go_up():
    transport = _FakeAdbTransport({"/storage/ABCD-1234": [], "/storage/ABCD-1234/Music": []})
    assert browse_remote(transport, "/storage/ABCD-1234").parent is None
    assert browse_remote(transport, "/storage/ABCD-1234/Music").parent == "/storage/ABCD-1234"


@pytest.mark.parametrize("path", ["/", "/data", "/system/app", "/storage", "/sdcard/Android/data", "/etc", "/sdcard/../data"])
def test_browse_remote_refuses_paths_outside_shared_storage(path):
    transport = _FakeAdbTransport({})
    with pytest.raises(BrowseForbidden):
        browse_remote(transport, path)


def test_browse_remote_maps_storage_labels_to_sdcard():
    transport = _FakeAdbTransport({"/sdcard/DCIM": []})
    result = browse_remote(transport, "/Internal storage/DCIM/")
    assert result.path == "/sdcard/DCIM"
    assert result.parent == "/sdcard"


class _RecordingTransport:
    def __init__(self):
        self.made = []

    def mkdirs(self, root, rel):
        self.made.append((root, rel))


def test_mkdir_remote_normalizes_and_refuses_outside_storage():
    transport = _RecordingTransport()
    assert mkdir_remote(transport, "/Internal storage/New") == "/sdcard/New"
    assert transport.made == [("/sdcard/New", "")]
    with pytest.raises(BrowseForbidden):
        mkdir_remote(transport, "/data/x")
