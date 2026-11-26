from __future__ import annotations

from pathlib import Path

import pytest

from plugin.plugins import app_launcher

pytestmark = pytest.mark.plugin_unit


def test_file_exists_supports_macos_app_bundles_only_on_darwin(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_bundle = tmp_path / "Calculator.app"
    app_bundle.mkdir()
    plain_dir = tmp_path / "plain_folder"
    plain_dir.mkdir()
    regular_file = tmp_path / "notes.txt"
    regular_file.write_text("hi", encoding="utf-8")

    monkeypatch.setattr(app_launcher.sys, "platform", "darwin")
    assert app_launcher._file_exists(str(app_bundle)) is True
    assert app_launcher._file_exists(f"{app_bundle}/") is True
    assert app_launcher._file_exists(str(plain_dir)) is False
    assert app_launcher._file_exists(str(regular_file)) is True

    monkeypatch.setattr(app_launcher.sys, "platform", "linux")
    assert app_launcher._file_exists(str(app_bundle)) is False
    assert app_launcher._file_exists(str(regular_file)) is True


def test_launch_file_uses_open_on_darwin_and_xdg_open_on_linux(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app_bundle = tmp_path / "MyApp.app"
    app_bundle.mkdir()
    regular_file = tmp_path / "doc.txt"
    regular_file.write_text("doc", encoding="utf-8")

    launched: list[list[str]] = []
    monkeypatch.delattr(app_launcher.os, "startfile", raising=False)
    monkeypatch.setattr(
        app_launcher.subprocess,
        "Popen",
        lambda cmd, **_kw: launched.append(list(cmd)),
    )

    monkeypatch.setattr(app_launcher.sys, "platform", "darwin")
    ok, err = app_launcher._launch_file(str(app_bundle))
    assert (ok, err) == (True, "")
    assert launched[-1] == ["open", str(app_bundle)]

    monkeypatch.setattr(app_launcher.sys, "platform", "linux")
    ok2, err2 = app_launcher._launch_file(str(regular_file))
    assert (ok2, err2) == (True, "")
    assert launched[-1] == ["xdg-open", str(regular_file)]
