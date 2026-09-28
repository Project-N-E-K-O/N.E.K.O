from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from plugin.server.application.plugins import hot_reload_service as module
from plugin.server.application.plugins.operation_lock import PluginOperationBusy


pytestmark = pytest.mark.plugin_unit


class _FakeLifecycleService:
    def __init__(self, fail_first_with: Exception | None = None) -> None:
        self.reload_calls: list[str] = []
        self._fail_first_with = fail_first_with

    async def reload_plugin(self, plugin_id: str) -> dict[str, object]:
        self.reload_calls.append(plugin_id)
        if self._fail_first_with is not None:
            error, self._fail_first_with = self._fail_first_with, None
            raise error
        return {"success": True, "plugin_id": plugin_id}


def _write_plugin_source(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "plugin.toml").write_text(
        "\n".join(
            [
                "[plugin]",
                'id = "demo"',
                'name = "Demo"',
                'version = "0.1.0"',
                'type = "plugin"',
                'entry = "demo:DemoPlugin"',
            ]
        ),
        encoding="utf-8",
    )
    (root / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")


def _make_service(
    tmp_path: Path,
    lifecycle: _FakeLifecycleService,
    monkeypatch: pytest.MonkeyPatch,
    *,
    running: bool = True,
) -> module.PluginHotReloadService:
    source_dir = tmp_path / "demo"
    _write_plugin_source(source_dir)
    monkeypatch.setattr(module, "PLUGIN_HOT_RELOAD", True)
    monkeypatch.setattr(module, "PLUGIN_HOT_RELOAD_INTERVAL", 0.05)
    monkeypatch.setattr(module, "PLUGIN_HOT_RELOAD_DEBOUNCE", 0.1)
    monkeypatch.setattr(module, "_plugin_is_running_sync", lambda plugin_id: running)
    service = module.PluginHotReloadService(lifecycle_service=lifecycle)
    monkeypatch.setattr(
        service,
        "_collect_targets_sync",
        lambda: [
            module._WatchTarget(plugin_id="demo", root=source_dir, is_development=False)
        ],
    )
    return service


async def _wait_for(predicate, timeout: float = 5.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


async def _stop(service: module.PluginHotReloadService) -> None:
    await service.stop(timeout=1.0)


async def test_start_is_noop_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module, "PLUGIN_HOT_RELOAD", False)
    service = module.PluginHotReloadService(lifecycle_service=_FakeLifecycleService())
    assert service.start() is False
    assert service.is_running is False
    await _stop(service)


async def test_change_triggers_reload_after_debounce(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifecycle = _FakeLifecycleService()
    service = _make_service(tmp_path, lifecycle, monkeypatch)
    source_dir = tmp_path / "demo"

    assert service.start() is True
    try:
        # 基线建立：首轮扫描只记录签名，不触发 reload。
        assert await _wait_for(lambda: bool(service._signatures))
        assert lifecycle.reload_calls == []

        (source_dir / "__init__.py").write_text(
            "VALUE = 2  # changed\n", encoding="utf-8"
        )
        assert await _wait_for(lambda: lifecycle.reload_calls == ["demo"])
    finally:
        await _stop(service)
    assert not service.is_running


async def test_first_scan_does_not_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifecycle = _FakeLifecycleService()
    service = _make_service(tmp_path, lifecycle, monkeypatch)
    assert service.start() is True
    try:
        await asyncio.sleep(0.4)
        assert lifecycle.reload_calls == []
    finally:
        await _stop(service)


async def test_syntax_error_blocks_reload_and_recovers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifecycle = _FakeLifecycleService()
    service = _make_service(tmp_path, lifecycle, monkeypatch)
    source_dir = tmp_path / "demo"

    assert service.start() is True
    try:
        assert await _wait_for(lambda: bool(service._signatures))
        (source_dir / "__init__.py").write_text(
            "def broken(:\n    pass\n", encoding="utf-8"
        )
        await asyncio.sleep(0.5)
        assert lifecycle.reload_calls == []

        (source_dir / "__init__.py").write_text(
            "VALUE = 3  # fixed\n", encoding="utf-8"
        )
        assert await _wait_for(lambda: lifecycle.reload_calls == ["demo"])
    finally:
        await _stop(service)


async def test_broken_manifest_blocks_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifecycle = _FakeLifecycleService()
    service = _make_service(tmp_path, lifecycle, monkeypatch)
    source_dir = tmp_path / "demo"

    assert service.start() is True
    try:
        assert await _wait_for(lambda: bool(service._signatures))
        (source_dir / "plugin.toml").write_text(
            "[plugin\nbroken toml", encoding="utf-8"
        )
        await asyncio.sleep(0.5)
        assert lifecycle.reload_calls == []
    finally:
        await _stop(service)


async def test_stopped_plugin_is_not_started_by_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifecycle = _FakeLifecycleService()
    service = _make_service(tmp_path, lifecycle, monkeypatch, running=False)
    source_dir = tmp_path / "demo"

    assert service.start() is True
    try:
        assert await _wait_for(lambda: bool(service._signatures))
        (source_dir / "__init__.py").write_text(
            "VALUE = 4  # changed\n", encoding="utf-8"
        )
        await asyncio.sleep(0.5)
        assert lifecycle.reload_calls == []
    finally:
        await _stop(service)


async def test_busy_reload_is_retried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifecycle = _FakeLifecycleService(fail_first_with=PluginOperationBusy())
    service = _make_service(tmp_path, lifecycle, monkeypatch)
    source_dir = tmp_path / "demo"

    assert service.start() is True
    try:
        assert await _wait_for(lambda: bool(service._signatures))
        (source_dir / "__init__.py").write_text(
            "VALUE = 5  # changed\n", encoding="utf-8"
        )
        assert await _wait_for(lambda: len(lifecycle.reload_calls) >= 2)
    finally:
        await _stop(service)


async def test_stop_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = _make_service(tmp_path, _FakeLifecycleService(), monkeypatch)
    await _stop(service)  # 未启动也能停
    assert service.start() is True
    await _stop(service)
    await _stop(service)
    assert not service.is_running


async def test_restart_rebaselines_without_spurious_reload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lifecycle = _FakeLifecycleService()
    service = _make_service(tmp_path, lifecycle, monkeypatch)
    source_dir = tmp_path / "demo"

    assert service.start() is True
    assert await _wait_for(lambda: bool(service._signatures))
    await _stop(service)

    # 停机窗口内的变更：重启后只重建基线，不应触发 reload。
    (source_dir / "__init__.py").write_text(
        "VALUE = 6  # changed while down\n", encoding="utf-8"
    )
    assert service.start() is True
    try:
        await asyncio.sleep(0.4)
        assert lifecycle.reload_calls == []
        assert await _wait_for(lambda: bool(service._signatures))
    finally:
        await _stop(service)


def test_signature_tracks_python_and_manifest_only(tmp_path: Path) -> None:
    source_dir = tmp_path / "demo"
    _write_plugin_source(source_dir)
    (source_dir / "asset.png").write_bytes(b"png")
    cache_dir = source_dir / "__pycache__"
    cache_dir.mkdir()
    (cache_dir / "demo.cpython-311.pyc").write_bytes(b"pyc")

    signature = module._signature_sync(source_dir)
    assert set(signature.keys()) == {"plugin.toml", "__init__.py"}


def test_preflight_compile_sync(tmp_path: Path) -> None:
    source_dir = tmp_path / "good"
    _write_plugin_source(source_dir)
    assert module._preflight_compile_sync(source_dir) is None

    broken = tmp_path / "broken"
    _write_plugin_source(broken)
    (broken / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    error = module._preflight_compile_sync(broken)
    assert error is not None and "bad.py" in error

    bad_manifest = tmp_path / "bad_manifest"
    _write_plugin_source(bad_manifest)
    (bad_manifest / "plugin.toml").write_text("[plugin\n", encoding="utf-8")
    error = module._preflight_compile_sync(bad_manifest)
    assert error is not None and "plugin.toml" in error
