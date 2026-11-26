from __future__ import annotations

import threading
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.core import host as host_module
from plugin.sdk.shared.core.events import EventHandler, EventMeta
from utils import storage_layout as storage_layout_module


class _FakeCommManager:
    def send_plugin_response(self, *args, **kwargs) -> None:
        self.response = (args, kwargs)

    async def start(self) -> None:
        return None

    async def shutdown(self, timeout: float) -> None:
        self.shutdown_timeout = timeout

    async def send_stop_command(self) -> None:
        self.stop_sent = True


class _StartupErrorCommManager(_FakeCommManager):
    async def prepare_startup_wait(self) -> None:
        self.startup_wait_prepared = True

    async def wait_for_startup(self, timeout: float, allow_startup_error: bool = False) -> dict[str, object]:
        self.startup_timeout = timeout
        self.allow_startup_error = allow_startup_error
        if allow_startup_error:
            return {"status": "failed", "startup_error": "lifecycle.startup failed"}
        raise RuntimeError("lifecycle.startup failed")


class _FakeProcess:
    pid = 1234
    exitcode = None

    def __init__(self) -> None:
        self.started = False

    def is_alive(self) -> bool:
        return self.started

    def start(self) -> None:
        self.started = True


def _patch_host_thread(monkeypatch: pytest.MonkeyPatch, thread_cls: type) -> None:
    """Replace ``threading.Thread`` as seen by the host module only.

    Patching the attribute on the real ``threading`` module would also catch
    the threads asyncio itself starts (e.g. when ``asyncio.run`` shuts down
    the loop's default executor).
    """
    patched = SimpleNamespace(**vars(threading))
    patched.Thread = thread_cls
    monkeypatch.setattr(host_module, "threading", patched)


@pytest.mark.plugin_unit
def test_plugin_host_uses_default_process_context_without_shared_response_proxies(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    accessed_response_proxies: list[str] = []
    default_event = object()
    default_process = _FakeProcess()

    class _FakeTransport:
        downlink_endpoint = "ipc://down"
        uplink_endpoint = "ipc://up"
        message_uplink_endpoint = "ipc://msg"

    def _reject_explicit_context(method: str) -> object:
        raise AssertionError(
            f"PluginHost must use the default start method, not {method!r}"
        )

    monkeypatch.setattr(host_module, "HostTransport", _FakeTransport)
    monkeypatch.setattr(host_module, "PluginCommunicationResourceManager", lambda **_kwargs: _FakeCommManager())
    monkeypatch.setattr(host_module.multiprocessing, "Manager", lambda: accessed_response_proxies.append("manager"))
    monkeypatch.setattr(host_module.multiprocessing, "get_start_method", lambda: "spawn")
    monkeypatch.setattr(host_module.multiprocessing, "get_context", _reject_explicit_context)
    monkeypatch.setattr(host_module.multiprocessing, "Event", lambda: default_event)
    monkeypatch.setattr(host_module.multiprocessing, "Process", lambda **_kwargs: default_process)

    plugin_host = host_module.PluginHost(
        plugin_id="demo",
        entry_point="plugins.demo:DemoPlugin",
        config_path=tmp_path / "demo" / "plugin.toml",
    )

    assert accessed_response_proxies == []
    assert plugin_host._process_stop_event is default_event
    assert plugin_host.process is default_process


class _FakeLogger:
    def info(self, *_args, **_kwargs) -> None:
        pass

    def error(self, *_args, **_kwargs) -> None:
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
def test_plugin_process_runner_rejects_missing_uplink_token(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "demo" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("[plugin]\nid='demo'\ntype='plugin'\n", encoding="utf-8")
    monkeypatch.setattr(
        host_module,
        "_setup_plugin_logger",
        lambda *args, **kwargs: _FakeLogger(),
    )

    with pytest.raises(ValueError, match="uplink token"):
        host_module._plugin_process_runner(
            plugin_id="demo",
            entry_point="tests.fake:DemoPlugin",
            config_path=config_path,
            downlink_endpoint="ipc://down",
            uplink_endpoint="ipc://up",
            message_uplink_endpoint="ipc://msg",
        )


@pytest.mark.plugin_unit
def test_plugin_router_entry_closes_context_and_transport_before_returning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    closed: list[str] = []
    payloads: list[dict[str, object]] = []
    contexts: list[dict[str, object]] = []
    config_path = tmp_path / "demo" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("[plugin]\nid='demo'\ntype='plugin'\n", encoding="utf-8")

    class _Router(host_module.PluginRouter):
        pass

    class _Sender:
        def put_nowait(self, payload: dict[str, object]) -> None:
            payloads.append(payload)

    class _ChildTransport:
        def __init__(
            self,
            downlink_endpoint: str,
            uplink_endpoint: str,
            uplink_token: str,
            *,
            message_uplink_endpoint: str,
        ) -> None:
            del downlink_endpoint, uplink_endpoint, uplink_token, message_uplink_endpoint

        def channel_sender(self, channel: str) -> _Sender:
            del channel
            return _Sender()

        def close(self) -> None:
            closed.append("transport")

    class _Context:
        def __init__(self, **kwargs: object) -> None:
            self.__dict__.update(kwargs)
            contexts.append(kwargs)

        def close(self) -> None:
            closed.append("context")

    monkeypatch.setattr(host_module, "_setup_plugin_logger", lambda *args, **kwargs: _FakeLogger())
    monkeypatch.setattr(host_module, "_prepare_child_plugin_import_roots", lambda *args, **kwargs: None)
    monkeypatch.setattr(host_module, "_prepare_child_current_plugin_import_root", lambda *args, **kwargs: None)
    monkeypatch.setattr(host_module, "_prepare_child_plugin_vendor_path", lambda *args, **kwargs: None)
    monkeypatch.setattr(host_module, "_import_plugin_module", lambda *args, **kwargs: SimpleNamespace(DemoRouter=_Router))
    monkeypatch.setattr(host_module, "ChildTransport", _ChildTransport)
    monkeypatch.setattr(host_module, "PluginContext", _Context)

    model_gateway_options = {
        "base_url": "http://127.0.0.1:49001/api/models/v1",
        "token": "this-instance-token",
    }
    host_module._plugin_process_runner(
        plugin_id="demo",
        entry_point="tests.fake:DemoRouter",
        config_path=config_path,
        downlink_endpoint="ipc://down",
        uplink_endpoint="ipc://up",
        uplink_token="test-uplink-token",
        message_uplink_endpoint="ipc://msg",
        model_gateway_options=model_gateway_options,
    )

    assert closed == ["context", "transport"]
    assert payloads[-1]["status"] == "error"
    assert contexts[0]["_model_gateway_base_url"] == "http://127.0.0.1:49001/api/models/v1"
    assert contexts[0]["_model_gateway_token"] == "this-instance-token"
    assert model_gateway_options == {}


@pytest.mark.plugin_unit
@pytest.mark.parametrize("startup_warning", [False, True])
@pytest.mark.parametrize("resolve_failure", [False, True])
def test_plugin_process_runner_sends_startup_ready_before_auto_custom_events(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    startup_warning: bool,
    resolve_failure: bool,
) -> None:
    order: list[str] = []
    payloads: list[dict[str, object]] = []
    effective_configs: list[dict[str, object] | None] = []
    config_path = tmp_path / "demo" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("[plugin]\nid='demo'\ntype='adapter'\n", encoding="utf-8")

    startup_meta = EventMeta(event_type="lifecycle", id="startup")
    auto_meta = EventMeta(
        event_type="custom_demo",
        id="boot",
        auto_start=True,
        extra={"trigger_method": "auto"},
    )

    async def _startup() -> None:
        order.append("startup")
        if startup_warning:
            raise RuntimeError("startup warning")

    async def _auto_custom() -> None:
        order.append("auto_custom")

    setattr(_startup, host_module.EVENT_META_ATTR, startup_meta)
    setattr(_auto_custom, host_module.EVENT_META_ATTR, auto_meta)

    class _Plugin:
        def __init__(self, ctx) -> None:
            self.ctx = ctx
            effective_configs.append(ctx._effective_config)

            async def dump(timeout: float = 3.0) -> dict[str, object]:
                del timeout
                return {"plugin_state": {"persist_mode": "manual"}}

            self.config = SimpleNamespace(dump=dump)

        def collect_entries(self, wrap_with_hooks: bool = True) -> dict[str, EventHandler]:
            return {
                "startup": EventHandler(meta=startup_meta, handler=_startup),
                "boot": EventHandler(meta=auto_meta, handler=_auto_custom),
            }

        async def _on_command_loop_start(self) -> None:
            order.append(self.ctx.handler_ctx)

    class _Sender:
        def __init__(self, channel: str) -> None:
            self.channel = channel

        def put(self, payload: dict[str, object], *, timeout: float | None = None) -> None:
            payloads.append(payload)
            if (
                self.channel == host_module.CH_RES
                and payload.get("req_id") == host_module.STARTUP_RESULT_REQ_ID
            ):
                order.append("ready")

        def put_nowait(self, payload: dict[str, object]) -> None:
            self.put(payload)

    class _ChildTransport:
        def __init__(
            self,
            downlink_endpoint: str,
            uplink_endpoint: str,
            uplink_token: str,
            *,
            message_uplink_endpoint: str,
        ) -> None:
            del downlink_endpoint, uplink_endpoint, uplink_token, message_uplink_endpoint
            self.stopped = False

        def channel_sender(self, channel: str) -> _Sender:
            return _Sender(channel)

        async def recv_downlink(self, timeout_ms: int = 1000):
            if not self.stopped:
                self.stopped = True
                return (host_module.CH_CMD, {"type": "STOP"})
            return None

        def close(self) -> None:
            return

    class _ImmediateThread:
        def __init__(self, target, args=(), kwargs=None, daemon: bool | None = None) -> None:
            self.target = target
            self.args = args
            self.kwargs = kwargs or {}

        def start(self) -> None:
            self.target(*self.args, **self.kwargs)

    monkeypatch.setattr(host_module, "_setup_plugin_logger", lambda *args, **kwargs: _FakeLogger())
    monkeypatch.setattr(host_module, "_prepare_child_plugin_import_roots", lambda *args, **kwargs: None)
    monkeypatch.setattr(host_module, "_prepare_child_current_plugin_import_root", lambda *args, **kwargs: None)
    monkeypatch.setattr(host_module, "_prepare_child_plugin_vendor_path", lambda *args, **kwargs: None)
    monkeypatch.setattr(host_module, "_import_plugin_module", lambda *args, **kwargs: SimpleNamespace(DemoPlugin=_Plugin))
    monkeypatch.setattr(host_module, "ChildTransport", _ChildTransport)
    _patch_host_thread(monkeypatch, _ImmediateThread)
    if resolve_failure:
        from plugin.server.infrastructure import config_resolver

        def _failed_resolve(*_args, **_kwargs):
            raise RuntimeError("child resolver unavailable")

        monkeypatch.setattr(config_resolver, "resolve_plugin_config_from_path", _failed_resolve)

    host_module._plugin_process_runner(
        plugin_id="demo",
        entry_point="tests.fake:DemoPlugin",
        config_path=config_path,
        downlink_endpoint="ipc://down",
        uplink_endpoint="ipc://up",
        uplink_token="test-uplink-token",
        message_uplink_endpoint="ipc://msg",
    )

    assert order == [
        "startup",
        "ready",
        "auto_custom",
        "lifecycle.command_loop_start",
    ]
    startup_payload = next(payload for payload in payloads if payload.get("req_id") == host_module.STARTUP_RESULT_REQ_ID)
    assert startup_payload["success"] is (not startup_warning)
    assert startup_payload["data"]["config_fingerprint"]
    if resolve_failure:
        from plugin.server.infrastructure.config_fingerprint import fingerprint_config

        assert startup_payload["data"]["config_fingerprint"] == fingerprint_config(
            {"plugin_state": {"persist_mode": "manual"}},
        )
    else:
        assert isinstance(effective_configs[0], dict)
        assert effective_configs[0]["plugin"]["id"] == "demo"


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_plugin_host_records_applied_config_only_after_ready_handshake(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _ReadyCommManager(_FakeCommManager):
        async def prepare_startup_wait(self) -> None:
            return

        async def wait_for_startup(self, timeout: float, allow_startup_error: bool = False) -> dict[str, object]:
            del timeout, allow_startup_error
            return {"status": "ready", "config_fingerprint": "sha256:ready"}

    comm_manager = _ReadyCommManager()

    class _FakeTransport:
        downlink_endpoint = "ipc://down"
        uplink_endpoint = "ipc://up"
        message_uplink_endpoint = "ipc://msg"

        def close(self) -> None:
            return

    monkeypatch.setattr(host_module, "HostTransport", _FakeTransport)
    monkeypatch.setattr(host_module, "PluginCommunicationResourceManager", lambda **_kwargs: comm_manager)
    monkeypatch.setattr(host_module.multiprocessing, "Event", lambda: SimpleNamespace(set=lambda: None))
    monkeypatch.setattr(host_module.multiprocessing, "Process", lambda **_kwargs: _FakeProcess())
    monkeypatch.setattr(host_module, "_refresh_child_storage_layout_env", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(host_module.state, "register_downlink_sender", lambda *_args, **_kwargs: None)

    plugin_host = host_module.PluginProcessHost(
        plugin_id="demo",
        entry_point="plugins.demo:DemoPlugin",
        config_path=tmp_path / "demo" / "plugin.toml",
    )

    startup_result = await plugin_host.start(startup_timeout=1.0)

    assert startup_result == {"status": "ready", "config_fingerprint": "sha256:ready"}
    assert plugin_host.applied_config_fingerprint == "sha256:ready"
    assert plugin_host.applied_config_loaded_at


@pytest.mark.plugin_unit
@pytest.mark.asyncio
@pytest.mark.parametrize("policy", ["warn", "ignore"])
async def test_plugin_host_records_loaded_config_for_tolerated_startup_warning(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    policy: str,
) -> None:
    class _WarningCommManager(_StartupErrorCommManager):
        async def wait_for_startup(self, timeout: float, allow_startup_error: bool = False) -> dict[str, object]:
            del timeout, allow_startup_error
            return {
                "status": "failed",
                "startup_error": "lifecycle.startup failed",
                "config_fingerprint": "sha256:loaded",
            }

    comm_manager = _WarningCommManager()

    class _FakeTransport:
        downlink_endpoint = "ipc://down"
        uplink_endpoint = "ipc://up"
        message_uplink_endpoint = "ipc://msg"

        def close(self) -> None:
            return

    monkeypatch.setattr(host_module, "HostTransport", _FakeTransport)
    monkeypatch.setattr(host_module, "PluginCommunicationResourceManager", lambda **_kwargs: comm_manager)
    monkeypatch.setattr(host_module.multiprocessing, "Event", lambda: SimpleNamespace(set=lambda: None))
    monkeypatch.setattr(host_module.multiprocessing, "Process", lambda **_kwargs: _FakeProcess())
    monkeypatch.setattr(host_module, "_refresh_child_storage_layout_env", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(host_module.state, "register_downlink_sender", lambda *_args, **_kwargs: None)

    plugin_host = host_module.PluginProcessHost(
        plugin_id="demo",
        entry_point="plugins.demo:DemoPlugin",
        config_path=tmp_path / "demo" / "plugin.toml",
    )

    startup_result = await plugin_host.start(startup_timeout=1.0, startup_failure=policy)

    assert startup_result["startup_error"] == "lifecycle.startup failed"
    assert plugin_host.applied_config_fingerprint == "sha256:loaded"
    assert plugin_host.applied_config_loaded_at


@pytest.mark.plugin_unit
def test_plugin_process_runner_cancels_trigger_without_run_id_before_closing_models(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import asyncio

    order: list[str] = []
    config_path = tmp_path / "demo" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("[plugin]\nid='demo'\ntype='plugin'\n", encoding="utf-8")

    class _Plugin:
        def __init__(self, ctx) -> None:
            self.ctx = ctx

            async def dump(timeout: float = 3.0) -> dict[str, object]:
                del timeout
                return {}

            self.config = SimpleNamespace(dump=dump)

        def collect_entries(self, wrap_with_hooks: bool = True) -> dict[str, EventHandler]:
            return {}

        async def hang(self) -> None:
            order.append("started")
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                order.append("cancelled")
                raise

    class _Sender:
        def put(self, payload: dict[str, object], *, timeout: float | None = None) -> None:
            return

        def put_nowait(self, payload: dict[str, object]) -> None:
            return

    class _ChildTransport:
        def __init__(
            self,
            downlink_endpoint: str,
            uplink_endpoint: str,
            uplink_token: str,
            *,
            message_uplink_endpoint: str,
        ) -> None:
            del downlink_endpoint, uplink_endpoint, uplink_token, message_uplink_endpoint
            self.sent_trigger = False

        def channel_sender(self, channel: str) -> _Sender:
            return _Sender()

        async def recv_downlink(self, timeout_ms: int = 1000):
            if not self.sent_trigger:
                self.sent_trigger = True
                return (host_module.CH_CMD, {"type": "TRIGGER", "entry_id": "hang", "args": {}, "req_id": "r1"})
            for _ in range(500):
                if "started" in order:
                    break
                await asyncio.sleep(0.01)
            return (host_module.CH_CMD, {"type": "STOP"})

        def close(self) -> None:
            return

    original_cleanup = host_module._run_with_model_client_cleanup

    async def _recording_cleanup(ctx, awaitable):
        try:
            return await original_cleanup(ctx, awaitable)
        finally:
            order.append("models_closed")

    monkeypatch.setattr(host_module, "_setup_plugin_logger", lambda *args, **kwargs: _FakeLogger())
    monkeypatch.setattr(host_module, "_prepare_child_plugin_import_roots", lambda *args, **kwargs: None)
    monkeypatch.setattr(host_module, "_prepare_child_current_plugin_import_root", lambda *args, **kwargs: None)
    monkeypatch.setattr(host_module, "_prepare_child_plugin_vendor_path", lambda *args, **kwargs: None)
    monkeypatch.setattr(host_module, "_import_plugin_module", lambda *args, **kwargs: SimpleNamespace(DemoPlugin=_Plugin))
    monkeypatch.setattr(host_module, "ChildTransport", _ChildTransport)
    monkeypatch.setattr(host_module, "_run_with_model_client_cleanup", _recording_cleanup)

    host_module._plugin_process_runner(
        plugin_id="demo",
        entry_point="tests.fake:DemoPlugin",
        config_path=config_path,
        downlink_endpoint="ipc://down",
        uplink_endpoint="ipc://up",
        uplink_token="test-uplink-token",
        message_uplink_endpoint="ipc://msg",
    )

    # A trigger without _ctx.run_id must still be unwound before model clients close.
    assert order[:3] == ["started", "cancelled", "models_closed"]


@pytest.mark.plugin_unit
def test_plugin_process_runner_uses_timeout_when_reporting_crash(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    payloads: list[dict[str, object]] = []
    timeouts: list[float | None] = []
    config_path = tmp_path / "demo" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("[plugin]\nid='demo'\ntype='adapter'\n", encoding="utf-8")

    class _Sender:
        def __init__(self, channel: str) -> None:
            self.channel = channel

        def put(self, payload: dict[str, object], *, timeout: float | None = None) -> None:
            payloads.append(payload)
            timeouts.append(timeout)

        def put_nowait(self, payload: dict[str, object]) -> None:
            self.put(payload)

    class _ChildTransport:
        def __init__(
            self,
            downlink_endpoint: str,
            uplink_endpoint: str,
            uplink_token: str,
            *,
            message_uplink_endpoint: str,
        ) -> None:
            del downlink_endpoint, uplink_endpoint, uplink_token, message_uplink_endpoint

        def channel_sender(self, channel: str) -> _Sender:
            return _Sender(channel)

        def close(self) -> None:
            return

    monkeypatch.setattr(host_module, "_setup_plugin_logger", lambda *args, **kwargs: _FakeLogger())
    monkeypatch.setattr(host_module, "_prepare_child_plugin_import_roots", lambda *args, **kwargs: None)
    monkeypatch.setattr(host_module, "_prepare_child_current_plugin_import_root", lambda *args, **kwargs: None)
    monkeypatch.setattr(host_module, "_prepare_child_plugin_vendor_path", lambda *args, **kwargs: None)
    monkeypatch.setattr(host_module, "ChildTransport", _ChildTransport)

    def _raise_import_error(*_args, **_kwargs):
        raise RuntimeError("import exploded")

    monkeypatch.setattr(host_module, "_import_plugin_module", _raise_import_error)

    with pytest.raises(RuntimeError, match="import exploded"):
        host_module._plugin_process_runner(
            plugin_id="demo",
            entry_point="tests.fake:DemoPlugin",
            config_path=config_path,
            downlink_endpoint="ipc://down",
            uplink_endpoint="ipc://up",
            uplink_token="test-uplink-token",
            message_uplink_endpoint="ipc://msg",
        )

    result_payloads = [
        payload
        for payload in payloads
        if payload.get("req_id") in {"CRASH", host_module.STARTUP_RESULT_REQ_ID}
    ]
    assert [payload["req_id"] for payload in result_payloads] == ["CRASH", host_module.STARTUP_RESULT_REQ_ID]
    assert timeouts == [10.0, 10.0]


@pytest.mark.plugin_unit
def test_plugin_process_runner_skips_auto_work_after_failed_startup_in_fail_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    started_threads: list[str] = []
    payloads: list[dict[str, object]] = []
    config_path = tmp_path / "demo" / "plugin.toml"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text("[plugin]\nid='demo'\ntype='adapter'\n", encoding="utf-8")

    startup_meta = EventMeta(event_type="lifecycle", id="startup")
    timer_meta = EventMeta(
        event_type="timer",
        id="tick",
        auto_start=True,
        extra={"mode": "interval", "seconds": 1},
    )
    auto_meta = EventMeta(
        event_type="custom_demo",
        id="boot",
        auto_start=True,
        extra={"trigger_method": "auto"},
    )

    async def _startup() -> None:
        raise RuntimeError("startup exploded")

    async def _timer() -> None:
        raise AssertionError("timer auto-start should not run")

    async def _auto_custom() -> None:
        raise AssertionError("custom auto-start should not run")

    setattr(_startup, host_module.EVENT_META_ATTR, startup_meta)
    setattr(_timer, host_module.EVENT_META_ATTR, timer_meta)
    setattr(_auto_custom, host_module.EVENT_META_ATTR, auto_meta)

    class _Plugin:
        def __init__(self, ctx) -> None:
            self.ctx = ctx

            async def dump(timeout: float = 3.0) -> dict[str, object]:
                del timeout
                return {}

            self.config = SimpleNamespace(dump=dump)

        def collect_entries(self, wrap_with_hooks: bool = True) -> dict[str, EventHandler]:
            return {
                "startup": EventHandler(meta=startup_meta, handler=_startup),
                "tick": EventHandler(meta=timer_meta, handler=_timer),
                "boot": EventHandler(meta=auto_meta, handler=_auto_custom),
            }

    class _Sender:
        def __init__(self, channel: str) -> None:
            self.channel = channel

        def put(self, payload: dict[str, object], *, timeout: float | None = None) -> None:
            payloads.append(payload)

        def put_nowait(self, payload: dict[str, object]) -> None:
            self.put(payload)

    class _ChildTransport:
        def __init__(
            self,
            downlink_endpoint: str,
            uplink_endpoint: str,
            uplink_token: str,
            *,
            message_uplink_endpoint: str,
        ) -> None:
            del downlink_endpoint, uplink_endpoint, uplink_token, message_uplink_endpoint

        def channel_sender(self, channel: str) -> _Sender:
            return _Sender(channel)

        async def recv_downlink(self, timeout_ms: int = 1000):
            return (host_module.CH_CMD, {"type": "STOP"})

        def close(self) -> None:
            return

    class _RecordingThread:
        def __init__(self, target, args=(), kwargs=None, daemon: bool | None = None) -> None:
            self.target = target

        def start(self) -> None:
            started_threads.append(getattr(self.target, "__name__", repr(self.target)))

    monkeypatch.setattr(host_module, "_setup_plugin_logger", lambda *args, **kwargs: _FakeLogger())
    monkeypatch.setattr(host_module, "_prepare_child_plugin_import_roots", lambda *args, **kwargs: None)
    monkeypatch.setattr(host_module, "_prepare_child_current_plugin_import_root", lambda *args, **kwargs: None)
    monkeypatch.setattr(host_module, "_prepare_child_plugin_vendor_path", lambda *args, **kwargs: None)
    monkeypatch.setattr(host_module, "_import_plugin_module", lambda *args, **kwargs: SimpleNamespace(DemoPlugin=_Plugin))
    monkeypatch.setattr(host_module, "ChildTransport", _ChildTransport)
    _patch_host_thread(monkeypatch, _RecordingThread)

    host_module._plugin_process_runner(
        plugin_id="demo",
        entry_point="tests.fake:DemoPlugin",
        config_path=config_path,
        downlink_endpoint="ipc://down",
        uplink_endpoint="ipc://up",
        uplink_token="test-uplink-token",
        message_uplink_endpoint="ipc://msg",
        startup_options={"startup_failure": "fail"},
    )

    startup_payload = next(payload for payload in payloads if payload.get("req_id") == host_module.STARTUP_RESULT_REQ_ID)
    assert startup_payload["success"] is False
    assert "startup exploded" in str(startup_payload["error"])
    assert started_threads == []


@pytest.mark.plugin_unit
def test_plugin_host_keeps_active_layout_when_default_policy_differs(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    from utils import config_manager
    from utils.storage_policy import save_storage_policy

    selected_root = tmp_path / "isolated" / "data"
    anchor_root = tmp_path / "isolated" / "anchor"
    default_parent = tmp_path / "default"
    manager = SimpleNamespace(
        app_name="N.E.K.O",
        app_docs_dir=selected_root,
        anchor_root=anchor_root,
        _get_standard_data_directory_candidates=lambda: [default_parent],
    )
    save_storage_policy(manager, selected_root=default_parent / manager.app_name, selection_source="default")
    monkeypatch.setattr(config_manager, "get_config_manager", lambda **_kwargs: manager)

    layout = host_module._resolve_current_storage_layout()

    assert layout["selected_root"] == str(selected_root)
    assert layout["anchor_root"] == str(anchor_root)
    assert layout["cloudsave_root"] == str(anchor_root / "cloudsave")


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
        message_uplink_endpoint = "ipc://msg"

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
async def test_plugin_process_start_removes_downlink_sender_when_spawn_fails(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    registered: list[str] = []
    removed: list[str] = []

    class _FakeTransport:
        downlink_endpoint = "ipc://down"
        uplink_endpoint = "ipc://up"
        message_uplink_endpoint = "ipc://msg"

        def close(self) -> None:
            self.closed = True

    class _FailingProcess:
        pid = None
        exitcode = None

        def is_alive(self) -> bool:
            return False

        def start(self) -> None:
            raise RuntimeError("spawn boom")

    comm_manager = _FakeCommManager()
    monkeypatch.setattr(host_module, "HostTransport", _FakeTransport)
    monkeypatch.setattr(host_module, "PluginCommunicationResourceManager", lambda **_kwargs: comm_manager)
    monkeypatch.setattr(host_module.multiprocessing, "Event", lambda: SimpleNamespace(set=lambda: None))
    monkeypatch.setattr(host_module.multiprocessing, "Process", lambda **_kwargs: _FailingProcess())
    monkeypatch.setattr(host_module, "_refresh_child_storage_layout_env", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(host_module.state, "register_downlink_sender", lambda plugin_id, _sender: registered.append(plugin_id))
    monkeypatch.setattr(host_module.state, "remove_downlink_sender", lambda plugin_id: removed.append(plugin_id))

    plugin_host = host_module.PluginProcessHost(
        plugin_id="demo",
        entry_point="plugins.demo:DemoPlugin",
        config_path=tmp_path / "demo" / "plugin.toml",
    )

    with pytest.raises(RuntimeError, match="spawn boom"):
        await plugin_host.start()

    assert registered == ["demo"]
    assert removed == ["demo"]
    assert getattr(plugin_host.transport, "closed", False) is True
    assert comm_manager.shutdown_timeout == host_module.PLUGIN_SHUTDOWN_TIMEOUT


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_abort_startup_after_failure_removes_downlink_sender(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    removed: list[str] = []

    class _FakeTransport:
        downlink_endpoint = "ipc://down"
        uplink_endpoint = "ipc://up"
        message_uplink_endpoint = "ipc://msg"

        def close(self) -> None:
            self.closed = True

    comm_manager = _FakeCommManager()
    monkeypatch.setattr(host_module, "HostTransport", _FakeTransport)
    monkeypatch.setattr(host_module, "PluginCommunicationResourceManager", lambda **_kwargs: comm_manager)
    monkeypatch.setattr(host_module.multiprocessing, "Event", lambda: SimpleNamespace(set=lambda: None))
    monkeypatch.setattr(host_module.multiprocessing, "Process", lambda **_kwargs: _FakeProcess())
    monkeypatch.setattr(host_module.state, "remove_downlink_sender", lambda plugin_id: removed.append(plugin_id))

    plugin_host = host_module.PluginProcessHost(
        plugin_id="demo",
        entry_point="plugins.demo:DemoPlugin",
        config_path=tmp_path / "demo" / "plugin.toml",
    )

    await plugin_host._abort_startup_after_failure(timeout=0.01)

    assert removed == ["demo"]
    assert comm_manager.stop_sent is True
    assert comm_manager.shutdown_timeout == 0.01
    assert getattr(plugin_host.transport, "closed", False) is True


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_plugin_process_start_keeps_running_on_startup_error_by_default(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    removed: list[str] = []

    class _FakeTransport:
        downlink_endpoint = "ipc://down"
        uplink_endpoint = "ipc://up"
        message_uplink_endpoint = "ipc://msg"

        def close(self) -> None:
            self.closed = True

    comm_manager = _StartupErrorCommManager()
    monkeypatch.setattr(host_module, "HostTransport", _FakeTransport)
    monkeypatch.setattr(host_module, "PluginCommunicationResourceManager", lambda **_kwargs: comm_manager)
    monkeypatch.setattr(host_module.multiprocessing, "Event", lambda: SimpleNamespace(set=lambda: None))
    monkeypatch.setattr(host_module.multiprocessing, "Process", lambda **_kwargs: _FakeProcess())
    monkeypatch.setattr(host_module, "_refresh_child_storage_layout_env", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(host_module.state, "register_downlink_sender", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(host_module.state, "remove_downlink_sender", lambda plugin_id: removed.append(plugin_id))

    plugin_host = host_module.PluginProcessHost(
        plugin_id="demo",
        entry_point="plugins.demo:DemoPlugin",
        config_path=tmp_path / "demo" / "plugin.toml",
    )

    startup_result = await plugin_host.start(startup_timeout=1.0)

    assert startup_result == {"status": "failed", "startup_error": "lifecycle.startup failed"}
    assert comm_manager.allow_startup_error is True
    assert getattr(comm_manager, "stop_sent", False) is False
    assert getattr(comm_manager, "shutdown_timeout", None) is None
    assert getattr(plugin_host.transport, "closed", False) is False
    assert removed == []
    assert plugin_host.applied_config_fingerprint is None
    assert plugin_host.applied_config_loaded_at is None


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_plugin_process_start_aborts_on_startup_error_in_fail_mode(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    removed: list[str] = []

    class _FakeTransport:
        downlink_endpoint = "ipc://down"
        uplink_endpoint = "ipc://up"
        message_uplink_endpoint = "ipc://msg"

        def close(self) -> None:
            self.closed = True

    comm_manager = _StartupErrorCommManager()
    monkeypatch.setattr(host_module, "HostTransport", _FakeTransport)
    monkeypatch.setattr(host_module, "PluginCommunicationResourceManager", lambda **_kwargs: comm_manager)
    monkeypatch.setattr(host_module.multiprocessing, "Event", lambda: SimpleNamespace(set=lambda: None))
    monkeypatch.setattr(host_module.multiprocessing, "Process", lambda **_kwargs: _FakeProcess())
    monkeypatch.setattr(host_module, "_refresh_child_storage_layout_env", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(host_module.state, "register_downlink_sender", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(host_module.state, "remove_downlink_sender", lambda plugin_id: removed.append(plugin_id))

    plugin_host = host_module.PluginProcessHost(
        plugin_id="demo",
        entry_point="plugins.demo:DemoPlugin",
        config_path=tmp_path / "demo" / "plugin.toml",
    )

    with pytest.raises(host_module.PluginLifecycleError, match="lifecycle\\.startup failed"):
        await plugin_host.start(startup_timeout=1.0, startup_failure="fail")

    assert comm_manager.allow_startup_error is False
    assert comm_manager.stop_sent is True
    assert comm_manager.shutdown_timeout == host_module.PLUGIN_SHUTDOWN_TIMEOUT
    assert getattr(plugin_host.transport, "closed", False) is True
    assert removed == ["demo"]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_plugin_process_start_passes_startup_failure_policy_to_child(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured_startup_options: list[dict[str, object]] = []

    class _FakeTransport:
        downlink_endpoint = "ipc://down"
        uplink_endpoint = "ipc://up"
        message_uplink_endpoint = "ipc://msg"

        def close(self) -> None:
            self.closed = True

    class _CapturingProcess(_FakeProcess):
        def __init__(self, **kwargs) -> None:
            super().__init__()
            self.args = kwargs["args"]

        def start(self) -> None:
            # startup_options is the eighth positional argument of
            # _plugin_process_runner; the endpoints after it travel as kwargs.
            startup_options = self.args[7]
            captured_startup_options.append(
                dict(startup_options)
                if isinstance(startup_options, dict)
                else startup_options
            )
            super().start()

    comm_manager = _StartupErrorCommManager()
    monkeypatch.setattr(host_module, "HostTransport", _FakeTransport)
    monkeypatch.setattr(host_module, "PluginCommunicationResourceManager", lambda **_kwargs: comm_manager)
    monkeypatch.setattr(host_module.multiprocessing, "Event", lambda: SimpleNamespace(set=lambda: None))
    monkeypatch.setattr(host_module.multiprocessing, "Process", lambda **kwargs: _CapturingProcess(**kwargs))
    monkeypatch.setattr(host_module, "_refresh_child_storage_layout_env", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(host_module.state, "register_downlink_sender", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(host_module.state, "remove_downlink_sender", lambda _plugin_id: None)

    plugin_host = host_module.PluginProcessHost(
        plugin_id="demo",
        entry_point="plugins.demo:DemoPlugin",
        config_path=tmp_path / "demo" / "plugin.toml",
    )

    with pytest.raises(host_module.PluginLifecycleError, match="lifecycle\\.startup failed"):
        await plugin_host.start(startup_timeout=1.0, startup_failure="fail")

    assert captured_startup_options == [{"startup_failure": "fail"}]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_config_update_rolls_back_runtime_helpers_when_config_change_fails() -> None:
    class _Ctx:
        def __init__(self) -> None:
            self._effective_config = {"plugin": {"store": {"enabled": False}}}
            self.refreshed: list[dict[str, object]] = []
            self.handler_contexts: list[str] = []

        def _refresh_instance_runtime_config(self, effective_config: dict[str, object]) -> None:
            self.refreshed.append(host_module.copy.deepcopy(effective_config))

        @contextmanager
        def _handler_scope(self, handler_ctx: str):
            self.handler_contexts.append(handler_ctx)
            yield

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
    assert ctx.handler_contexts == ["lifecycle.config_change"]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_config_update_replace_removes_deleted_keys_and_temporary_hydrates_missing_cache() -> None:
    class _Ctx:
        def __init__(self, initial: dict[str, object] | None, disk: dict[str, object]) -> None:
            self._effective_config = initial
            self._effective_config_uncertain = True
            self._disk = disk
            self.refreshed: list[dict[str, object]] = []

        async def get_own_config(self) -> dict[str, object]:
            return {"config": dict(self._disk)}

        def _refresh_instance_runtime_config(self, effective_config: dict[str, object]) -> None:
            self.refreshed.append(host_module.copy.deepcopy(effective_config))

    # 1. replace=True replaces snapshot instead of deep-merging, removing keys omitted from active profile.
    ctx_replace = _Ctx({"feature": {"keep": 1, "remove_me": 2}}, {})
    sender = _FakeResponseSender()
    await host_module._handle_config_update_command(
        msg={
            "req_id": "req-replace",
            "config": {"feature": {"keep": 1}},
            "mode": "temporary",
            "replace": True,
        },
        ctx=ctx_replace,
        events_by_type={},
        plugin_id="demo",
        res_sender=sender,
        logger=_FakeLogger(),
    )
    assert sender.payloads[-1]["success"] is True
    assert ctx_replace._effective_config == {"feature": {"keep": 1}}
    assert ctx_replace._effective_config_uncertain is False

    # 2. Temporary partial update with _effective_config=None hydrates via get_own_config() before merging.
    ctx_hydrate = _Ctx(None, {"memo": {"max_reminders": 42, "timezone": "Asia/Tokyo"}})
    sender2 = _FakeResponseSender()
    await host_module._handle_config_update_command(
        msg={
            "req_id": "req-hydrate",
            "config": {"other": {"enabled": True}},
            "mode": "temporary",
            "replace": False,
        },
        ctx=ctx_hydrate,
        events_by_type={},
        plugin_id="demo",
        res_sender=sender2,
        logger=_FakeLogger(),
    )
    assert sender2.payloads[-1]["success"] is True
    assert ctx_hydrate._effective_config == {
        "memo": {"max_reminders": 42, "timezone": "Asia/Tokyo"},
        "other": {"enabled": True},
    }


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_config_update_replace_rollback_restores_uncertain_flag_and_deep_merge_strips_nested_delete() -> None:
    class _Ctx:
        def __init__(self) -> None:
            self._effective_config = {"feature": {"old": 1}}
            self._effective_config_uncertain = True
            self.refreshed: list[dict[str, object]] = []

        def _refresh_instance_runtime_config(self, effective_config: dict[str, object]) -> None:
            self.refreshed.append(host_module.copy.deepcopy(effective_config))

        @contextmanager
        def _handler_scope(self, _handler_ctx: str):
            yield

    def _boom(**_kwargs: object) -> None:
        raise RuntimeError("boom")

    ctx = _Ctx()
    sender = _FakeResponseSender()
    await host_module._handle_config_update_command(
        msg={
            "req_id": "req-rb",
            "config": {"feature": {"new": 2}},
            "mode": "temporary",
            "replace": True,
        },
        ctx=ctx,
        events_by_type={"lifecycle": {"config_change": _boom}},
        plugin_id="demo",
        res_sender=sender,
        logger=_FakeLogger(),
    )
    assert sender.payloads[-1]["success"] is False
    assert ctx._effective_config == {"feature": {"old": 1}}
    assert ctx._effective_config_uncertain is True

    merged: dict[str, object] = {}
    host_module._deep_merge(
        merged,
        {"section": {"obsolete": "__DELETE__", "enabled": True}},
    )
    assert merged == {"section": {"enabled": True}}


@pytest.mark.plugin_unit
def test_config_update_command_uses_plugin_context_ordered_publisher_and_rollback(
    tmp_path: Path,
) -> None:
    import asyncio
    from plugin.core.context import PluginContext

    first_notify_entered = threading.Event()
    release_first_notify = threading.Event()
    second_waiting_on_notify_lock = threading.Event()

    class _OrderedInstance:
        def __init__(self) -> None:
            self.refreshed: list[dict[str, object]] = []
            self.pause_next = True

        def refresh_runtime_config(self, effective_config: dict[str, object]) -> None:
            if self.pause_next:
                self.pause_next = False
                first_notify_entered.set()
                release_first_notify.wait(timeout=2.0)
            self.refreshed.append(host_module.copy.deepcopy(effective_config))

    ctx = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo" / "plugin.toml",
        logger=_FakeLogger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    instance = _OrderedInstance()
    ctx._instance = instance

    inner_notify_lock = ctx._get_config_notify_lock()
    cmd_thread_holder: list[threading.Thread] = []

    class _HookedLock:
        def acquire(self, *args: object, **kwargs: object) -> bool:
            if cmd_thread_holder and threading.current_thread() is cmd_thread_holder[0]:
                second_waiting_on_notify_lock.set()
            return bool(inner_notify_lock.acquire(*args, **kwargs))

        def release(self) -> None:
            inner_notify_lock.release()

        def __enter__(self) -> object:
            if cmd_thread_holder and threading.current_thread() is cmd_thread_holder[0]:
                second_waiting_on_notify_lock.set()
            return inner_notify_lock.__enter__()

        def __exit__(self, *exc: object) -> object:
            return inner_notify_lock.__exit__(*exc)

    ctx._config_notify_lock = _HookedLock()

    sender = _FakeResponseSender()

    def _run_cmd_update() -> None:
        asyncio.run(
            host_module._handle_config_update_command(
                msg={
                    "req_id": "req-ordered",
                    "config": {"v": 2},
                    "mode": "temporary",
                    "replace": True,
                },
                ctx=ctx,
                events_by_type={},
                plugin_id="demo",
                res_sender=sender,
                logger=_FakeLogger(),
            )
        )

    t_first = threading.Thread(target=ctx._set_effective_config_cache, args=({"v": 1},))
    t_cmd = threading.Thread(target=_run_cmd_update)
    cmd_thread_holder.append(t_cmd)

    t_first.start()
    assert first_notify_entered.wait(timeout=1.0)
    t_cmd.start()
    assert second_waiting_on_notify_lock.wait(timeout=1.0)
    release_first_notify.set()
    t_first.join(timeout=2.0)
    t_cmd.join(timeout=2.0)
    assert not t_first.is_alive()
    assert not t_cmd.is_alive()
    assert sender.payloads[-1]["success"] is True
    assert ctx._effective_config == {"v": 2}
    assert instance.refreshed == [{"v": 1}, {"v": 2}]

    # Verify rollback on uncached PluginContext restores uncertain state and notifies old_config.
    ctx_rb = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo_rb" / "plugin.toml",
        logger=_FakeLogger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    rb_instance = _OrderedInstance()
    rb_instance.pause_next = False
    ctx_rb._instance = rb_instance
    ctx_rb._effective_config = None
    ctx_rb._effective_config_uncertain = True

    async def _get_own_config() -> dict[str, object]:
        return {"config": {"base": 1}}

    ctx_rb.get_own_config = _get_own_config  # type: ignore[method-assign]

    def _boom(**_kwargs: object) -> None:
        raise RuntimeError("boom")

    rb_sender = _FakeResponseSender()
    asyncio.run(
        host_module._handle_config_update_command(
            msg={
                "req_id": "req-rb-real",
                "config": {"extra": 2},
                "mode": "temporary",
                "replace": False,
            },
            ctx=ctx_rb,
            events_by_type={"lifecycle": {"config_change": _boom}},
            plugin_id="demo",
            res_sender=rb_sender,
            logger=_FakeLogger(),
        )
    )
    assert rb_sender.payloads[-1]["success"] is False
    assert ctx_rb._effective_config is None
    assert ctx_rb._effective_config_uncertain is True
    assert rb_instance.refreshed == [{"base": 1, "extra": 2}, {"base": 1}]

    # Verify rollback notification does not overwrite a newer snapshot published before notify drains.
    race_first_entered = threading.Event()
    race_release_first = threading.Event()
    race_rollback_recorded = threading.Event()

    class _RaceInstance:
        def __init__(self) -> None:
            self.refreshed: list[dict[str, object]] = []
            self._first = True

        def refresh_runtime_config(self, effective_config: dict[str, object]) -> None:
            if self._first:
                self._first = False
                race_first_entered.set()
                race_release_first.wait(timeout=2.0)
            self.refreshed.append(host_module.copy.deepcopy(effective_config))

    ctx_race = PluginContext(
        plugin_id="demo",
        config_path=tmp_path / "demo_race" / "plugin.toml",
        logger=_FakeLogger(),  # type: ignore[arg-type]
        status_queue=None,
    )
    race_instance = _RaceInstance()
    ctx_race._instance = race_instance

    race_notify_lock = ctx_race._get_config_notify_lock()
    rb_thread_holder: list[threading.Thread] = []

    class _RaceHookLock:
        def acquire(self, *args: object, **kwargs: object) -> bool:
            if rb_thread_holder and threading.current_thread() is rb_thread_holder[0]:
                race_rollback_recorded.set()
            return bool(race_notify_lock.acquire(*args, **kwargs))

        def release(self) -> None:
            race_notify_lock.release()

        def __enter__(self) -> object:
            if rb_thread_holder and threading.current_thread() is rb_thread_holder[0]:
                race_rollback_recorded.set()
            return race_notify_lock.__enter__()

        def __exit__(self, *exc: object) -> object:
            return race_notify_lock.__exit__(*exc)

    ctx_race._config_notify_lock = _RaceHookLock()

    t_hold = threading.Thread(target=ctx_race._set_effective_config_cache, args=({"v": 10},))
    t_rb = threading.Thread(
        target=lambda: ctx_race._set_effective_config_cache(
            None,
            uncertain=True,
            fallback_notify_config={"v": 0},
        )
    )
    rb_thread_holder.append(t_rb)
    t_hold.start()
    assert race_first_entered.wait(timeout=1.0)
    t_rb.start()
    assert race_rollback_recorded.wait(timeout=1.0)
    # Publish a newer config snapshot directly to state before releasing the notify lock.
    with ctx_race._get_config_state_lock():
        ctx_race._bump_effective_config_gen()
        ctx_race._record_effective_config_locked({"v": 99}, uncertain=False)
    race_release_first.set()
    t_hold.join(timeout=2.0)
    t_rb.join(timeout=2.0)
    assert ctx_race._effective_config == {"v": 99}
    assert race_instance.refreshed == [{"v": 10}, {"v": 99}]


async def _wait_for_checkpoint_or_task_failure(
    event: object,
    task: object,
    *,
    timeout: float = 5.0,
) -> None:
    import asyncio

    waiter = asyncio.create_task(event.wait())  # type: ignore[attr-defined]
    try:
        done, _ = await asyncio.wait(
            {waiter, task},  # type: ignore[arg-type]
            timeout=timeout,
            return_when=asyncio.FIRST_COMPLETED,
        )
        if not done:
            raise TimeoutError("Timed out waiting for async checkpoint")
        if task in done and not event.is_set():  # type: ignore[attr-defined]
            await task  # type: ignore[misc]
            raise AssertionError("Task finished before reaching checkpoint")
        await waiter
    finally:
        if not waiter.done():
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)


@pytest.mark.plugin_unit
def test_config_update_command_hydrated_merge_preserves_concurrent_save_and_guarded_rollback(
    tmp_path: Path,
) -> None:
    import asyncio
    from plugin.core.context import PluginContext

    class _RecordingInstance:
        def __init__(self) -> None:
            self.refreshed: list[dict[str, object]] = []

        def refresh_runtime_config(self, effective_config: dict[str, object]) -> None:
            self.refreshed.append(host_module.copy.deepcopy(effective_config))

    # 1. Hydrating an empty cache while a concurrent save finishes must merge into the newer cache.
    async def _case_hydrated_concurrent_save() -> None:
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_hydrate" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance = _RecordingInstance()
        ctx._instance = instance

        read_started = asyncio.Event()
        save_done = asyncio.Event()

        async def _stale_get_own_config() -> dict[str, object]:
            gen_before, read_id = ctx._begin_config_read()
            read_started.set()
            await save_done.wait()
            stale_cfg = {"base": 1}
            ctx._cache_effective_config_from_read(stale_cfg, gen_before=gen_before, read_id=read_id)
            return {"config": stale_cfg}

        ctx.get_own_config = _stale_get_own_config  # type: ignore[method-assign]
        sender = _FakeResponseSender()
        cmd_task = asyncio.create_task(
            host_module._handle_config_update_command(
                msg={
                    "req_id": "req-hydrate-race",
                    "config": {"host": 2},
                    "mode": "temporary",
                    "replace": False,
                },
                ctx=ctx,
                events_by_type={},
                plugin_id="demo",
                res_sender=sender,
                logger=_FakeLogger(),
            )
        )
        try:
            await _wait_for_checkpoint_or_task_failure(read_started, cmd_task)
            ctx._set_effective_config_cache({"base": 1, "timer": "saved"})
            save_done.set()
            await cmd_task
            assert sender.payloads[-1]["success"] is True
            assert ctx._effective_config == {"base": 1, "timer": "saved", "host": 2}
            assert ctx._effective_config_uncertain is False
            assert instance.refreshed[-1] == {"base": 1, "timer": "saved", "host": 2}
        finally:
            save_done.set()
            if not cmd_task.done():
                cmd_task.cancel()
            await asyncio.gather(cmd_task, return_exceptions=True)

    asyncio.run(_case_hydrated_concurrent_save())

    # 2. Failed config_change after a concurrent newer snapshot is published must not clobber newer config.
    async def _case_rollback_guarded_when_superseded(*, initial_cached: bool) -> None:
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / f"demo_rb_guard_{initial_cached}" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance = _RecordingInstance()
        ctx._instance = instance
        if initial_cached:
            ctx._set_effective_config_cache({"v": 1})
        else:
            async def _hydrate() -> dict[str, object]:
                return {"config": {"v": 1}}

            ctx.get_own_config = _hydrate  # type: ignore[method-assign]

        handler_entered = asyncio.Event()
        allow_fail = asyncio.Event()

        async def _slow_failing_handler(**_kwargs: object) -> None:
            handler_entered.set()
            await allow_fail.wait()
            raise RuntimeError("boom after superseding write")

        sender = _FakeResponseSender()
        cmd_task = asyncio.create_task(
            host_module._handle_config_update_command(
                msg={
                    "req_id": f"req-rb-guard-{initial_cached}",
                    "config": {"v": 2},
                    "mode": "temporary",
                    "replace": initial_cached,
                },
                ctx=ctx,
                events_by_type={"lifecycle": {"config_change": _slow_failing_handler}},
                plugin_id="demo",
                res_sender=sender,
                logger=_FakeLogger(),
            )
        )
        try:
            await _wait_for_checkpoint_or_task_failure(handler_entered, cmd_task)
            ctx._set_effective_config_cache({"v": 99})
            allow_fail.set()
            await cmd_task
            assert sender.payloads[-1]["success"] is False
            assert ctx._effective_config == {"v": 99}
            assert instance.refreshed[-1] == {"v": 99}
        finally:
            allow_fail.set()
            if not cmd_task.done():
                cmd_task.cancel()
            await asyncio.gather(cmd_task, return_exceptions=True)

    asyncio.run(_case_rollback_guarded_when_superseded(initial_cached=True))
    asyncio.run(_case_rollback_guarded_when_superseded(initial_cached=False))

    # 3. replace=True rollback after invalidation restores last runtime snapshot while keeping cache None.
    async def _case_replace_rollback_restores_last_runtime_snapshot() -> None:
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_replace_rb_snap" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance = _RecordingInstance()
        ctx._instance = instance
        ctx._set_effective_config_cache({"plugin": {"state": {"backend": "sqlite"}}, "keep": 1})
        ctx._invalidate_effective_config_for_profile_refresh()
        assert ctx._effective_config is None

        def _reject(**_kwargs: object) -> None:
            raise RuntimeError("rejected replace")

        sender_fail = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-replace-rb-snap",
                "config": {"plugin": {"state": {"backend": "json"}}},
                "mode": "temporary",
                "replace": True,
            },
            ctx=ctx,
            events_by_type={"lifecycle": {"config_change": _reject}},
            plugin_id="demo",
            res_sender=sender_fail,
            logger=_FakeLogger(),
        )
        assert sender_fail.payloads[-1]["success"] is False
        assert ctx._effective_config is None
        assert instance.refreshed[-1] == {"plugin": {"state": {"backend": "sqlite"}}, "keep": 1}

        # Subsequent successful replace still drops omitted keys cleanly.
        sender_ok = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-replace-ok",
                "config": {"only": "new"},
                "mode": "temporary",
                "replace": True,
            },
            ctx=ctx,
            events_by_type={},
            plugin_id="demo",
            res_sender=sender_ok,
            logger=_FakeLogger(),
        )
        assert sender_ok.payloads[-1]["success"] is True
        assert ctx._effective_config == {"only": "new"}
        assert instance.refreshed[-1] == {"only": "new"}

    asyncio.run(_case_replace_rollback_restores_last_runtime_snapshot())


@pytest.mark.plugin_unit
def test_config_update_command_coordinates_with_in_flight_writes_and_profile_switches(
    tmp_path: Path,
) -> None:
    import asyncio
    from plugin.core.context import PluginContext

    class _RecordingInstance:
        def __init__(self, *, fail_backend: str | None = None) -> None:
            self.refreshed: list[dict[str, object]] = []
            self.fail_backend = fail_backend

        def refresh_runtime_config(self, effective_config: dict[str, object]) -> None:
            backend = (
                effective_config.get("plugin", {}).get("state", {}).get("backend")
                if isinstance(effective_config.get("plugin"), dict)
                and isinstance(effective_config["plugin"].get("state"), dict)
                else None
            )
            if self.fail_backend is not None and backend == self.fail_backend:
                raise RuntimeError(f"failed refresh for {backend}")
            self.refreshed.append(host_module.copy.deepcopy(effective_config))

    # 1. Failed concurrent write rolling back to host's tentative config must NOT block host rollback.
    async def _case_failed_concurrent_write_allows_host_rollback() -> None:
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_failed_write_allows_host_rb" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance = _RecordingInstance()
        ctx._instance = instance
        ctx._set_effective_config_cache({"base": 1})

        async def _no_local(**_kwargs: object) -> None:
            return None

        async def _reject_write(**_kwargs: object) -> dict[str, object]:
            raise RuntimeError("timer write rejected")

        ctx._get_local_config_payload = _no_local  # type: ignore[method-assign]
        ctx._send_request_and_wait_async = _reject_write  # type: ignore[method-assign]

        handler_entered = asyncio.Event()
        allow_fail = asyncio.Event()

        async def _config_change(**_kwargs: object) -> None:
            handler_entered.set()
            await allow_fail.wait()
            raise RuntimeError("host update rejected")

        sender = _FakeResponseSender()
        cmd_task = asyncio.create_task(
            host_module._handle_config_update_command(
                msg={
                    "req_id": "req-failed-write-rb",
                    "config": {"base": 1, "host": 2},
                    "mode": "temporary",
                    "replace": True,
                },
                ctx=ctx,
                events_by_type={"lifecycle": {"config_change": _config_change}},
                plugin_id="demo",
                res_sender=sender,
                logger=_FakeLogger(),
            )
        )
        try:
            await _wait_for_checkpoint_or_task_failure(handler_entered, cmd_task)
            with pytest.raises(RuntimeError, match="timer write rejected"):
                await ctx.update_own_config({"timer": 3})
            # Cache gen & seq advanced due to failed write's optimistic install + rollback,
            # yet owner token stayed with host's tentative config, so host rollback still restores {"base": 1}.
            allow_fail.set()
            await cmd_task
            assert sender.payloads[-1]["success"] is False
            assert ctx._effective_config == {"base": 1}
            assert instance.refreshed[-1] == {"base": 1}
        finally:
            allow_fail.set()
            if not cmd_task.done():
                cmd_task.cancel()
            await asyncio.gather(cmd_task, return_exceptions=True)

    asyncio.run(_case_failed_concurrent_write_allows_host_rollback())

    # 2. Overlapping in-flight write when host config_change fails:
    #    - If write also fails, rejected host config must NOT remain active.
    #    - If write commits, committed result is preserved.
    async def _case_host_and_overlapping_write_coordination(*, write_outcome: str) -> None:
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / f"demo_coord_{write_outcome}" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance = _RecordingInstance()
        ctx._instance = instance
        ctx._set_effective_config_cache({"base": 1})

        async def _no_local(**_kwargs: object) -> None:
            return None

        write_rpc_entered = asyncio.Event()
        release_write_rpc = asyncio.Event()

        async def _write_rpc(**_kwargs: object) -> dict[str, object]:
            write_rpc_entered.set()
            await release_write_rpc.wait()
            if write_outcome == "fail":
                raise RuntimeError("rpc failed")
            if write_outcome == "persisted_false":
                return {
                    "success": False,
                    "plugin_id": "demo",
                    "config": {"base": 1, "host": 2, "timer": 3},
                    "requires_reload": False,
                    "persisted": False,
                    "message": "persisted=False",
                }
            return {"success": True, "config": {"base": 1, "timer": 3}}

        ctx._get_local_config_payload = _no_local  # type: ignore[method-assign]
        ctx._send_request_and_wait_async = _write_rpc  # type: ignore[method-assign]

        handler_entered = asyncio.Event()
        allow_host_fail = asyncio.Event()

        async def _config_change(**_kwargs: object) -> None:
            handler_entered.set()
            await allow_host_fail.wait()
            raise RuntimeError("host rejected")

        sender = _FakeResponseSender()
        cmd_task = asyncio.create_task(
            host_module._handle_config_update_command(
                msg={
                    "req_id": f"req-coord-{write_outcome}",
                    "config": {"base": 1, "host": 2},
                    "mode": "temporary",
                    "replace": True,
                },
                ctx=ctx,
                events_by_type={"lifecycle": {"config_change": _config_change}},
                plugin_id="demo",
                res_sender=sender,
                logger=_FakeLogger(),
            )
        )
        write_task: asyncio.Task[dict[str, object]] | None = None
        try:
            await _wait_for_checkpoint_or_task_failure(handler_entered, cmd_task)
            write_task = asyncio.create_task(ctx.update_own_config({"timer": 3}))
            await _wait_for_checkpoint_or_task_failure(write_rpc_entered, write_task)
            allow_host_fail.set()
            await cmd_task
            assert sender.payloads[-1]["success"] is False

            release_write_rpc.set()
            if write_outcome == "fail":
                with pytest.raises(RuntimeError, match="rpc failed"):
                    await write_task
                assert ctx._effective_config == {"base": 1}
                assert instance.refreshed[-1] == {"base": 1}
            elif write_outcome == "persisted_false":
                res_write = await write_task
                assert res_write["config"] == {"base": 1, "timer": 3}
                assert ctx._effective_config == {"base": 1, "timer": 3}
                assert instance.refreshed[-1] == {"base": 1, "timer": 3}
            else:
                await write_task
                assert ctx._effective_config == {"base": 1, "timer": 3}
                assert instance.refreshed[-1] == {"base": 1, "timer": 3}
        finally:
            allow_host_fail.set()
            release_write_rpc.set()
            for pending in (write_task, cmd_task):
                if pending is not None and not pending.done():
                    pending.cancel()
            await asyncio.gather(
                *(t for t in (write_task, cmd_task) if t is not None),
                return_exceptions=True,
            )

    asyncio.run(_case_host_and_overlapping_write_coordination(write_outcome="fail"))
    asyncio.run(_case_host_and_overlapping_write_coordination(write_outcome="persisted_false"))
    asyncio.run(_case_host_and_overlapping_write_coordination(write_outcome="commit"))

    # 3. Host hydration waits for in-flight optimistic write appearing mid-read instead of merging unconfirmed draft.
    async def _case_host_waits_for_in_flight_draft(*, write_succeeds: bool) -> None:
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / f"demo_wait_draft_{write_succeeds}" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance = _RecordingInstance()
        ctx._instance = instance

        async def _local_payload(**_kwargs: object) -> dict[str, object]:
            return {"plugin_id": "demo", "config": {"base": 1}}

        read_started = asyncio.Event()
        release_read = asyncio.Event()
        write_rpc_started = asyncio.Event()
        release_write_rpc = asyncio.Event()

        async def _host_get_own_config() -> dict[str, object]:
            read_started.set()
            await release_read.wait()
            return {"config": {"base": 1}}

        async def _write_rpc(**_kwargs: object) -> dict[str, object]:
            write_rpc_started.set()
            await release_write_rpc.wait()
            if not write_succeeds:
                raise RuntimeError("draft rejected")
            return {"success": True, "config": {"base": 1, "draft": 1}}

        ctx._get_local_config_payload = _local_payload  # type: ignore[method-assign]
        ctx._send_request_and_wait_async = _write_rpc  # type: ignore[method-assign]
        ctx.get_own_config = _host_get_own_config  # type: ignore[method-assign]

        sender = _FakeResponseSender()
        # Start host temporary update while effective cache is empty so hydration starts first.
        cmd_task = asyncio.create_task(
            host_module._handle_config_update_command(
                msg={
                    "req_id": f"req-wait-draft-{write_succeeds}",
                    "config": {"host": 2},
                    "mode": "temporary",
                    "replace": False,
                },
                ctx=ctx,
                events_by_type={},
                plugin_id="demo",
                res_sender=sender,
                logger=_FakeLogger(),
            )
        )
        write_task: asyncio.Task[dict[str, object]] | None = None
        try:
            await _wait_for_checkpoint_or_task_failure(read_started, cmd_task)
            # Start an overlapping plugin config write while host hydration is in flight.
            ctx._install_effective_config_value({"base": 1})
            write_task = asyncio.create_task(ctx.update_own_config({"draft": 1}))
            await _wait_for_checkpoint_or_task_failure(write_rpc_started, write_task)
            assert ctx._effective_config == {"base": 1, "draft": 1}

            # Release host hydration read -> _apply_hydrated_merge_for_host must see _config_write_in_progress
            # and wait for the in-flight write to settle before publishing host merge.
            release_read.set()
            await asyncio.sleep(0.01)
            assert not cmd_task.done()

            release_write_rpc.set()
            if not write_succeeds:
                with pytest.raises(RuntimeError, match="draft rejected"):
                    await write_task
                await cmd_task
                assert sender.payloads[-1]["success"] is True
                assert ctx._effective_config == {"base": 1, "host": 2}
                assert "draft" not in ctx._effective_config
                assert instance.refreshed[-1] == {"base": 1, "host": 2}
            else:
                await write_task
                await cmd_task
                assert sender.payloads[-1]["success"] is True
                assert ctx._effective_config == {"base": 1, "draft": 1, "host": 2}
                assert instance.refreshed[-1] == {"base": 1, "draft": 1, "host": 2}
        finally:
            release_read.set()
            release_write_rpc.set()
            for pending in (cmd_task, write_task):
                if pending is not None and not pending.done():
                    pending.cancel()
            await asyncio.gather(
                *(t for t in (cmd_task, write_task) if t is not None),
                return_exceptions=True,
            )

    asyncio.run(_case_host_waits_for_in_flight_draft(write_succeeds=False))
    asyncio.run(_case_host_waits_for_in_flight_draft(write_succeeds=True))

    # 4. Stale hydration read rejected by read guard on profile switch must retry and not publish stale home.
    async def _case_stale_hydrate_retried_after_profile_switch() -> None:
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_switch_retry" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance = _RecordingInstance()
        ctx._instance = instance

        first_read_started = asyncio.Event()
        release_first_read = asyncio.Event()
        read_calls = 0

        async def _switching_get_own_config() -> dict[str, object]:
            nonlocal read_calls
            read_calls += 1
            gen_before, read_id = ctx._begin_config_read()
            if read_calls == 1:
                first_read_started.set()
                await release_first_read.wait()
                stale_cfg = {"profile": "home"}
                ctx._cache_effective_config_from_read(
                    stale_cfg,
                    gen_before=gen_before,
                    read_id=read_id,
                )
                return {"config": stale_cfg}
            fresh_cfg = {"profile": "work"}
            ctx._cache_effective_config_from_read(
                fresh_cfg,
                gen_before=gen_before,
                read_id=read_id,
            )
            return {"config": fresh_cfg}

        ctx.get_own_config = _switching_get_own_config  # type: ignore[method-assign]
        sender = _FakeResponseSender()
        cmd_task = asyncio.create_task(
            host_module._handle_config_update_command(
                msg={
                    "req_id": "req-switch-retry",
                    "config": {"host": 2},
                    "mode": "temporary",
                    "replace": False,
                },
                ctx=ctx,
                events_by_type={},
                plugin_id="demo",
                res_sender=sender,
                logger=_FakeLogger(),
            )
        )
        try:
            await _wait_for_checkpoint_or_task_failure(first_read_started, cmd_task)
            ctx._invalidate_effective_config_for_profile_refresh()
            release_first_read.set()
            await cmd_task
            assert read_calls == 2
            assert sender.payloads[-1]["success"] is True
            assert ctx._effective_config == {"profile": "work", "host": 2}
            assert not any(snap.get("profile") == "home" for snap in instance.refreshed)
            assert instance.refreshed[-1] == {"profile": "work", "host": 2}
        finally:
            release_first_read.set()
            if not cmd_task.done():
                cmd_task.cancel()
            await asyncio.gather(cmd_task, return_exceptions=True)

    asyncio.run(_case_stale_hydrate_retried_after_profile_switch())

    # 5. Failed refresh_runtime_config must not overwrite _last_runtime_config_snapshot.
    async def _case_failed_runtime_refresh_preserves_last_good_snapshot() -> None:
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_failed_rt_snap" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance = _RecordingInstance(fail_backend="json")
        ctx._instance = instance

        ctx._set_effective_config_cache({"plugin": {"state": {"backend": "sqlite"}}})
        ctx._set_effective_config_cache({"plugin": {"state": {"backend": "json"}}})
        ctx._invalidate_effective_config_for_profile_refresh()
        assert ctx._effective_config is None

        def _reject(**_kwargs: object) -> None:
            raise RuntimeError("rejected")

        sender = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-rt-snap-rb",
                "config": {"plugin": {"state": {"backend": "file"}}},
                "mode": "temporary",
                "replace": True,
            },
            ctx=ctx,
            events_by_type={"lifecycle": {"config_change": _reject}},
            plugin_id="demo",
            res_sender=sender,
            logger=_FakeLogger(),
        )
        assert sender.payloads[-1]["success"] is False
        assert ctx._effective_config is None
        assert instance.refreshed[-1] == {"plugin": {"state": {"backend": "sqlite"}}}

    asyncio.run(_case_failed_runtime_refresh_preserves_last_good_snapshot())

    # 6. Host hydration shares the 8s merge deadline and aborts without applying if timed out.
    async def _case_host_hydration_shares_deadline_and_aborts_on_timeout() -> None:
        from unittest.mock import patch

        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_host_deadline" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance = _RecordingInstance()
        ctx._instance = instance
        handler_calls = 0

        def _should_not_run(**_kwargs: object) -> None:
            nonlocal handler_calls
            handler_calls += 1

        clock = {"now": 100.0}
        received_timeouts: list[float] = []

        async def _slow_hydrating_get_own_config(*, timeout: float = 5.0) -> dict[str, object]:
            received_timeouts.append(timeout)
            clock["now"] = 109.0  # Exceed 8.0s deadline before returning.
            return {"config": {"base": 1}}

        ctx.get_own_config = _slow_hydrating_get_own_config  # type: ignore[method-assign]
        sender = _FakeResponseSender()
        with patch.object(host_module.time, "monotonic", side_effect=lambda: clock["now"]):
            await host_module._handle_config_update_command(
                msg={
                    "req_id": "req-deadline-expired",
                    "config": {"host": 2},
                    "mode": "temporary",
                    "replace": False,
                },
                ctx=ctx,
                events_by_type={"lifecycle": {"config_change": _should_not_run}},
                plugin_id="demo",
                res_sender=sender,
                logger=_FakeLogger(),
            )
        assert len(received_timeouts) == 1
        assert received_timeouts[0] == pytest.approx(8.0)
        assert sender.payloads[-1]["success"] is False
        assert "Timed out" in str(sender.payloads[-1]["error"])
        assert ctx._effective_config is None
        assert handler_calls == 0

        async def _timeout_get_own_config(*, timeout: float = 5.0) -> dict[str, object]:
            received_timeouts.append(timeout)
            raise TimeoutError("hydrated get timed out")

        ctx.get_own_config = _timeout_get_own_config  # type: ignore[method-assign]
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-hydrate-timeout-err",
                "config": {"host": 3},
                "mode": "temporary",
                "replace": False,
            },
            ctx=ctx,
            events_by_type={"lifecycle": {"config_change": _should_not_run}},
            plugin_id="demo",
            res_sender=sender,
            logger=_FakeLogger(),
        )
        assert len(received_timeouts) == 2
        assert 0.0 < received_timeouts[1] <= 8.0
        assert sender.payloads[-1]["success"] is False
        assert "Timed out" in str(sender.payloads[-1]["error"])
        assert ctx._effective_config is None
        assert handler_calls == 0

    asyncio.run(_case_host_hydration_shares_deadline_and_aborts_on_timeout())

    # 7. Mutating old_config inside a failing config_change handler must not corrupt rollback snapshot.
    async def _case_failing_config_change_mutating_old_config_preserves_rollback() -> None:
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_mutated_old_cfg" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance = _RecordingInstance()
        ctx._instance = instance
        ctx._set_effective_config_cache({"keep": "orig", "nested": {"x": 1}})

        def _mutate_and_reject(
            *,
            old_config: dict[str, object],
            new_config: dict[str, object],
            mode: str,
        ) -> None:
            del mode
            old_config["keep"] = "CORRUPTED"
            nested = old_config["nested"]
            assert isinstance(nested, dict)
            nested["x"] = 999
            new_nested = new_config["nested"]
            assert isinstance(new_nested, dict)
            new_nested["x"] = 888
            raise RuntimeError("handler failed after mutating inputs")

        sender = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-mutate-rb",
                "config": {"nested": {"x": 2}},
                "mode": "temporary",
                "replace": False,
            },
            ctx=ctx,
            events_by_type={"lifecycle": {"config_change": _mutate_and_reject}},
            plugin_id="demo",
            res_sender=sender,
            logger=_FakeLogger(),
        )
        assert sender.payloads[-1]["success"] is False
        assert ctx._effective_config == {"keep": "orig", "nested": {"x": 1}}
        assert instance.refreshed[-1] == {"keep": "orig", "nested": {"x": 1}}

    asyncio.run(_case_failing_config_change_mutating_old_config_preserves_rollback())
