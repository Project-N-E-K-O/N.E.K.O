from __future__ import annotations

import copy
import threading
from pathlib import Path
from typing import Any

import pytest

from plugin.plugins import app_launcher
import plugin.plugins.app_launcher as app_launcher_mod
from plugin.plugins.app_launcher import AppLauncherPlugin
from plugin.sdk.plugin import SdkError

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


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def info(self, *args: object, **kwargs: object) -> None:
        pass

    def warning(self, msg: str, *args: object) -> None:
        self.warnings.append(msg.format(*args))

    def error(self, msg: str, *args: object) -> None:
        self.errors.append(msg.format(*args))


class _MemoryStore:
    enabled = True

    def __init__(self, initial: list[dict[str, Any]] | None = None) -> None:
        self.data: dict[str, Any] = {app_launcher_mod._STORE_KEY: copy.deepcopy(list(initial or []))}
        self.fail_writes = False

    def _read_value(self, key: str, default: Any = None) -> Any:
        return copy.deepcopy(self.data.get(key, default))

    def _write_value(self, key: str, value: Any) -> None:
        if self.fail_writes:
            raise RuntimeError("sqlite disk full")
        self.data[key] = copy.deepcopy(value)


def _make_plugin(initial_apps: list[dict[str, Any]] | None = None) -> AppLauncherPlugin:
    plugin = AppLauncherPlugin.__new__(AppLauncherPlugin)
    plugin.logger = _Logger()
    plugin.file_logger = plugin.logger
    plugin.store = _MemoryStore(initial_apps)
    plugin._apps_lock = threading.Lock()
    plugin._recent_lock = threading.Lock()
    return plugin


@pytest.mark.asyncio
async def test_remove_app_cleans_registry_even_when_stored_autostart_is_false(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        app_launcher_mod,
        "_set_autostart_windows",
        lambda app_id, name, path, enabled: (calls.append((app_id, enabled)) or (True, "ok")),
    )
    plugin = _make_plugin(
        [{"id": "app1", "name": "Demo", "path": "C:/Demo.exe", "autostart": False}]
    )

    result = await plugin.remove_app(app_id="app1")

    assert result.is_ok()
    assert calls == [("app1", False)]
    assert plugin.store.data[app_launcher_mod._STORE_KEY] == []


@pytest.mark.asyncio
async def test_remove_app_aborts_and_preserves_record_when_registry_cleanup_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        app_launcher_mod,
        "_set_autostart_windows",
        lambda *args: (False, "PermissionError"),
    )
    initial = [{"id": "app1", "name": "Demo", "path": "C:/Demo.exe", "autostart": True}]
    plugin = _make_plugin(initial)

    result = await plugin.remove_app(app_id="app1")

    assert result.is_err()
    assert "PermissionError" in str(result.error)
    assert plugin.store.data[app_launcher_mod._STORE_KEY] == initial


@pytest.mark.asyncio
async def test_remove_app_succeeds_on_non_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app_launcher_mod, "_is_windows", lambda: False)
    plugin = _make_plugin(
        [{"id": "app1", "name": "Demo", "path": "/tmp/demo", "autostart": True}]
    )

    result = await plugin.remove_app(app_id="app1")

    assert result.is_ok()
    assert plugin.store.data[app_launcher_mod._STORE_KEY] == []


@pytest.mark.asyncio
async def test_set_autostart_enable_rolls_back_registry_when_store_persist_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        app_launcher_mod,
        "_set_autostart_windows",
        lambda app_id, name, path, enabled: (calls.append((app_id, enabled)) or (True, "ok")),
    )
    plugin = _make_plugin(
        [{"id": "app1", "name": "Demo", "path": "C:/Demo.exe", "autostart": False}]
    )
    plugin.store.fail_writes = True

    result = await plugin.set_autostart(app_id="app1", enabled=True)

    assert result.is_err()
    assert calls == [("app1", True), ("app1", False)]


@pytest.mark.asyncio
async def test_set_autostart_disable_returns_err_on_persist_failure_without_reenabling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, bool]] = []
    monkeypatch.setattr(
        app_launcher_mod,
        "_set_autostart_windows",
        lambda app_id, name, path, enabled: (calls.append((app_id, enabled)) or (True, "ok")),
    )
    plugin = _make_plugin(
        [{"id": "app1", "name": "Demo", "path": "C:/Demo.exe", "autostart": True}]
    )
    plugin.store.fail_writes = True

    result = await plugin.set_autostart(app_id="app1", enabled=False)

    assert result.is_err()
    assert calls == [("app1", False)]


@pytest.mark.asyncio
async def test_add_app_rolls_back_autostart_registry_if_secondary_persist_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exe = tmp_path / "demo.exe"
    exe.write_bytes(b"MZ")
    calls: list[bool] = []
    monkeypatch.setattr(
        app_launcher_mod,
        "_set_autostart_windows",
        lambda app_id, name, path, enabled: (calls.append(enabled) or (True, "ok")),
    )
    plugin = _make_plugin([])

    real_save = plugin._save_apps_unlocked
    save_count = 0

    def _flaky_save(apps: list[dict[str, Any]]) -> None:
        nonlocal save_count
        save_count += 1
        if save_count == 2:
            raise SdkError("persist failed")
        real_save(apps)

    monkeypatch.setattr(plugin, "_save_apps_unlocked", _flaky_save)

    result = await plugin.add_app(name="Demo", path=str(exe), autostart=True)

    assert result.is_ok()
    assert "持久化失败" in result.value["message"]
    assert calls == [True, False]
    assert plugin.store.data[app_launcher_mod._STORE_KEY][0]["autostart"] is False
