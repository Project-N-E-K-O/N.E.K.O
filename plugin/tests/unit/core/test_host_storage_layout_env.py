from __future__ import annotations

from pathlib import Path

import pytest

from plugin.core import host as host_module


class _FakeCommManager:
    def send_plugin_response(self, *args, **kwargs) -> None:
        self.response = (args, kwargs)

    async def start(self, message_target_queue=None) -> None:
        self.message_target_queue = message_target_queue

    async def shutdown(self, timeout: float) -> None:
        self.shutdown_timeout = timeout

    async def send_stop_command(self) -> None:
        self.stop_sent = True


class _FakeProcess:
    pid = 1234
    exitcode = None

    def __init__(self) -> None:
        self.started = False

    def is_alive(self) -> bool:
        return self.started

    def start(self) -> None:
        self.started = True


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_plugin_process_start_refreshes_storage_layout_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    selected_root = tmp_path / "selected-root"
    calls: list[dict[str, object]] = []

    monkeypatch.delenv("NEKO_STORAGE_SELECTED_ROOT", raising=False)
    monkeypatch.setattr(host_module.state, "register_downlink_sender", lambda *_args, **_kwargs: None)

    class _FakeTransport:
        downlink_endpoint = "ipc://down"
        uplink_endpoint = "ipc://up"

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(host_module, "HostTransport", _FakeTransport)
    monkeypatch.setattr(host_module, "PluginCommunicationResourceManager", lambda **_kwargs: _FakeCommManager())
    monkeypatch.setattr(host_module.multiprocessing, "Event", lambda: object())
    monkeypatch.setattr(host_module.multiprocessing, "Process", lambda **_kwargs: _FakeProcess())

    monkeypatch.setattr(
        host_module,
        "_resolve_current_storage_layout",
        lambda: {"selected_root": str(selected_root), "anchor_root": str(tmp_path / "anchor")},
    )

    def _export(layout: dict[str, object]) -> None:
        calls.append(layout)
        host_module.os.environ["NEKO_STORAGE_SELECTED_ROOT"] = str(layout["selected_root"])

    monkeypatch.setattr(host_module, "export_storage_layout_to_env", _export)

    plugin_host = host_module.PluginProcessHost(
        plugin_id="demo",
        entry_point="plugins.demo:DemoPlugin",
        config_path=tmp_path / "demo" / "plugin.toml",
    )

    await plugin_host.start()

    assert calls == [{"selected_root": str(selected_root), "anchor_root": str(tmp_path / "anchor")}]
    assert host_module.os.environ["NEKO_STORAGE_SELECTED_ROOT"] == str(selected_root)
