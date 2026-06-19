from __future__ import annotations

from pathlib import Path

import pytest

from plugin.core import host as host_module
from utils import storage_layout as storage_layout_module


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


class _FakeLogger:
    def info(self, *_args, **_kwargs) -> None:
        pass

    def debug(self, *_args, **_kwargs) -> None:
        pass

    def warning(self, *_args, **_kwargs) -> None:
        pass

    def exception(self, *_args, **_kwargs) -> None:
        pass


class _FakeResponseSender:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def put(self, payload: dict[str, object], timeout: float) -> None:
        self.payloads.append(payload)
        self.timeout = timeout


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

    monkeypatch.setattr(storage_layout_module, "export_storage_layout_to_env", _export)

    plugin_host = host_module.PluginProcessHost(
        plugin_id="demo",
        entry_point="plugins.demo:DemoPlugin",
        config_path=tmp_path / "demo" / "plugin.toml",
    )

    await plugin_host.start()

    assert calls == [{"selected_root": str(selected_root), "anchor_root": str(tmp_path / "anchor")}]
    assert host_module.os.environ["NEKO_STORAGE_SELECTED_ROOT"] == str(selected_root)


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_config_update_rolls_back_runtime_helpers_when_config_change_fails() -> None:
    class _Ctx:
        def __init__(self) -> None:
            self._effective_config = {"plugin": {"store": {"enabled": False}}}
            self.refreshed: list[dict[str, object]] = []

        def _refresh_instance_runtime_config(self, effective_config: dict[str, object]) -> None:
            self.refreshed.append(host_module.copy.deepcopy(effective_config))

    def _config_change(**_kwargs: object) -> None:
        raise RuntimeError("boom")

    ctx = _Ctx()
    sender = _FakeResponseSender()
    await host_module._handle_config_update_command(
        msg={
            "req_id": "req-1",
            "config": {"plugin": {"store": {"enabled": True}}},
            "mode": "temporary",
        },
        ctx=ctx,
        events_by_type={"lifecycle": {"config_change": _config_change}},
        plugin_id="demo",
        res_sender=sender,
        logger=_FakeLogger(),
    )

    assert ctx._effective_config == {"plugin": {"store": {"enabled": False}}}
    assert ctx.refreshed == [
        {"plugin": {"store": {"enabled": True}}},
        {"plugin": {"store": {"enabled": False}}},
    ]
    assert sender.payloads[-1]["success"] is False
    assert "config_change handler failed" in str(sender.payloads[-1]["error"])
