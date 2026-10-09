import sys

import pytest

# 模块级导入是有意的：``launcher_core.bootstrap`` 在 import 时可能往 stdout 打
# ssl 预导入的 Warning，放到收集期才不会落进下面断言 stdout 的 capsys 窗口。
from launcher_core import bootstrap


@pytest.fixture
def windows7(monkeypatch):
    monkeypatch.setattr(bootstrap, "_is_windows7", lambda: True)
    monkeypatch.setattr(bootstrap, "IS_FROZEN", False)
    # setenv before delenv so monkeypatch records an undo even when the
    # variable is absent; otherwise the marker set by _warn_if_windows7()
    # would leak into the rest of the pytest process.
    for name in ("NEKO_WIN7_SILENT", "_NEKO_WIN7_BANNER"):
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)


@pytest.mark.parametrize(
    ("platform", "version", "expected"),
    [
        ("win32", (6, 1, 7601), True),
        ("win32", (6, 3, 9600), False),
        ("win32", (10, 0, 19045), False),
        ("linux", (6, 1, 7601), False),
    ],
)
def test_is_windows7_matches_nt_6_1_only(monkeypatch, platform, version, expected):
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(sys, "getwindowsversion", lambda: version, raising=False)

    assert bootstrap._is_windows7() is expected


def test_is_windows7_tolerates_version_probe_failure(monkeypatch):
    def broken():
        raise OSError("probe failed")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(sys, "getwindowsversion", broken, raising=False)

    assert bootstrap._is_windows7() is False


def test_banner_prints_once_per_process_tree(windows7, capsys):
    bootstrap._warn_if_windows7()
    first = capsys.readouterr().out
    bootstrap._warn_if_windows7()

    assert "Windows 7" in first
    assert "setup_win7.bat" in first
    assert "docs/zh-CN/guide/windows-7.md" in first
    assert capsys.readouterr().out == ""
    # The marker is what re-executed launcher processes inherit.
    assert bootstrap.os.environ["_NEKO_WIN7_BANNER"] == "1"


def test_banner_respects_silent_env(windows7, monkeypatch, capsys):
    monkeypatch.setenv("NEKO_WIN7_SILENT", "1")

    bootstrap._warn_if_windows7()

    assert capsys.readouterr().out == ""


def test_banner_skipped_in_frozen_builds(windows7, monkeypatch, capsys):
    monkeypatch.setattr(bootstrap, "IS_FROZEN", True)

    bootstrap._warn_if_windows7()

    assert capsys.readouterr().out == ""


def test_banner_skipped_off_windows7(windows7, monkeypatch, capsys):
    monkeypatch.setattr(bootstrap, "_is_windows7", lambda: False)

    bootstrap._warn_if_windows7()

    assert capsys.readouterr().out == ""
