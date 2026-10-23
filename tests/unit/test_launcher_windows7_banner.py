import runpy
import sys
import types
from pathlib import Path

import pytest

# 模块级导入是有意的：``launcher_core.bootstrap`` 在 import 时可能往 stdout 打
# ssl 预导入的 Warning，放到收集期才不会落进下面断言 stdout 的 capsys 窗口。
from launcher_core import bootstrap


PROJECT_ROOT = Path(__file__).resolve().parents[2]


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


def test_source_launcher_entry_warns_before_importing_runtime(monkeypatch):
    # Run the real launcher.py entry: the banner has to be printed before the
    # runtime chain is imported, so it still shows if a native dependency of
    # that chain fails to load on Windows 7.
    events = []

    class _FakeRuntime(types.ModuleType):
        def __getattr__(self, name):
            # Only `from launcher_core.runtime import start_launcher` reaches
            # here; anything else (e.g. the importer's __path__ probe) is absent.
            if name != "start_launcher":
                raise AttributeError(name)
            events.append("runtime import")
            return lambda: events.append("start_launcher") or 0

    monkeypatch.setitem(
        sys.modules, "launcher_core.runtime", _FakeRuntime("launcher_core.runtime")
    )
    monkeypatch.setattr(bootstrap, "_ensure_utf8_filesystem_encoding", lambda: None)
    monkeypatch.setattr(bootstrap, "_pin_project_root_first", lambda: None)
    monkeypatch.setattr(bootstrap, "_warn_if_windows7", lambda: events.append("banner"))
    for name in (
        "NEKO_WAKE_WORD_RELEASE_SMOKE",
        "NEKO_MEDIA_RELEASE_SMOKE",
        "NEKO_VOICE_IDENTITY_RELEASE_SMOKE",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(sys, "argv", ["launcher.py"])

    with pytest.raises(SystemExit) as exit_info:
        runpy.run_path(str(PROJECT_ROOT / "launcher.py"), run_name="__main__")

    assert exit_info.value.code == 0
    assert events == ["banner", "runtime import", "start_launcher"]
