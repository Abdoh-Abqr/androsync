"""``androsync share`` and the Share dialog helpers (Phase V5)."""

from androsync import cli, share


def test_share_text_names_the_project_and_has_no_personal_data():
    assert share.SHARE_TEXT.startswith("AndroSync keeps folders identical")
    assert share.SHARE_TEXT.endswith(share.REPO_URL)
    for word in ("token", "100.", "192.168."):
        assert word not in share.SHARE_TEXT


def test_the_share_command_prints_the_text_and_does_not_need_a_clipboard(capsys, monkeypatch):
    monkeypatch.setattr(share, "copy_to_clipboard", lambda text: None)
    assert cli.main(["share"]) == 0
    out = capsys.readouterr().out
    assert share.SHARE_TEXT in out
    assert "No clipboard tool found" in out


def test_no_copy_skips_the_clipboard(capsys, monkeypatch):
    def boom(text):
        raise AssertionError("must not copy")

    monkeypatch.setattr(share, "copy_to_clipboard", boom)
    assert cli.main(["share", "--no-copy"]) == 0
    assert share.SHARE_TEXT in capsys.readouterr().out


def test_the_qr_is_shown_when_the_package_is_installed():
    lines = share.qr_lines(share.REPO_URL)
    if lines is None:  # the optional extra is not installed here
        return
    assert lines and all(len(line) == len(lines[0]) for line in lines)
