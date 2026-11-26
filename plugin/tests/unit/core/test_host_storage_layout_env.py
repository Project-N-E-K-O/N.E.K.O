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

    # 8. Uncertain cached draft from timed-out write must re-hydrate authoritative disk config before host patch.
    async def _case_uncertain_cached_draft_rehydrates_before_host_patch() -> None:
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_uncertain_rehydrate" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance = _RecordingInstance()
        ctx._instance = instance
        ctx._set_effective_config_cache({"base": 1})

        async def _timeout_write(**_kwargs: object) -> dict[str, object]:
            raise TimeoutError("write ack timed out")

        disk_config: dict[str, object] = {"base": 1}

        async def _local_payload(**_kwargs: object) -> dict[str, object]:
            return {"plugin_id": "demo", "config": dict(disk_config)}

        ctx._send_request_and_wait_async = _timeout_write  # type: ignore[method-assign]
        ctx._get_local_config_payload = _local_payload  # type: ignore[method-assign]

        res = await ctx.update_own_config({"draft": 1})
        assert res["persisted"] is None
        assert ctx._effective_config == {"base": 1, "draft": 1}
        assert ctx._effective_config_uncertain is True

        sender = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-uncertain-rehydrate",
                "config": {"live": 2},
                "mode": "temporary",
                "replace": False,
            },
            ctx=ctx,
            events_by_type={},
            plugin_id="demo",
            res_sender=sender,
            logger=_FakeLogger(),
        )
        assert sender.payloads[-1]["success"] is True
        assert ctx._effective_config == {"base": 1, "live": 2}
        assert "draft" not in ctx._effective_config
        assert ctx._effective_config_uncertain is False
        assert instance.refreshed[-1] == {"base": 1, "live": 2}

    asyncio.run(_case_uncertain_cached_draft_rehydrates_before_host_patch())

    # 9. Failed hydration must abort temporary patch instead of overwriting existing runtime config with {}.
    async def _case_failed_hydration_aborts_temporary_patch_instead_of_overwriting_with_empty_base() -> None:
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_hydrate_fail" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance = _RecordingInstance()
        ctx._instance = instance
        ctx._set_effective_config_cache({"keep": 1, "mode": "old"})
        ctx._invalidate_effective_config_for_profile_refresh()
        assert ctx._effective_config is None

        handler_calls = 0

        def _should_not_run(**_kwargs: object) -> None:
            nonlocal handler_calls
            handler_calls += 1

        async def _broken_get_own_config(*, timeout: float = 5.0) -> dict[str, object]:
            del timeout
            raise RuntimeError("disk offline")

        ctx.get_own_config = _broken_get_own_config  # type: ignore[method-assign]
        sender = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-hydrate-fail",
                "config": {"live": 2},
                "mode": "temporary",
                "replace": False,
            },
            ctx=ctx,
            events_by_type={"lifecycle": {"config_change": _should_not_run}},
            plugin_id="demo",
            res_sender=sender,
            logger=_FakeLogger(),
        )
        assert sender.payloads[-1]["success"] is False
        assert "Failed to hydrate" in str(sender.payloads[-1]["error"])
        assert ctx._effective_config is None
        assert instance.refreshed[-1] == {"keep": 1, "mode": "old"}
        assert handler_calls == 0

        # Control case: genuine empty baseline {"config": {}} still applies normally.
        async def _empty_get_own_config(*, timeout: float = 5.0) -> dict[str, object]:
            del timeout
            return {"config": {}}

        ctx.get_own_config = _empty_get_own_config  # type: ignore[method-assign]
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-hydrate-empty-ok",
                "config": {"live": 2},
                "mode": "temporary",
                "replace": False,
            },
            ctx=ctx,
            events_by_type={},
            plugin_id="demo",
            res_sender=sender,
            logger=_FakeLogger(),
        )
        assert sender.payloads[-1]["success"] is True
        assert ctx._effective_config == {"live": 2}
        assert instance.refreshed[-1] == {"live": 2}

    asyncio.run(_case_failed_hydration_aborts_temporary_patch_instead_of_overwriting_with_empty_base())

    # 10. Effective disk read inside failing config_change handler must not steal rollback ownership.
    async def _case_effective_read_inside_failing_config_change_preserves_rollback_ownership() -> None:
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_effective_read_in_cb" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance = _RecordingInstance()
        ctx._instance = instance
        ctx._set_effective_config_cache({"base": 1, "keep": 2})

        async def _local_payload(**_kwargs: object) -> dict[str, object]:
            return {"plugin_id": "demo", "config": {"base": 1}}

        ctx._get_local_config_payload = _local_payload  # type: ignore[method-assign]
        observed_reads: list[dict[str, object]] = []

        async def _read_then_reject(**_kwargs: object) -> None:
            loaded = await ctx.get_own_effective_config(None)
            observed_reads.append(dict(loaded["config"]))
            raise RuntimeError("reject extra after disk read")

        sender = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-read-in-cb",
                "config": {"extra": 3},
                "mode": "temporary",
                "replace": False,
            },
            ctx=ctx,
            events_by_type={"lifecycle": {"config_change": _read_then_reject}},
            plugin_id="demo",
            res_sender=sender,
            logger=_FakeLogger(),
        )
        assert observed_reads == [{"base": 1}]
        assert sender.payloads[-1]["success"] is False
        assert ctx._effective_config == {"base": 1, "keep": 2}
        assert instance.refreshed[-1] == {"base": 1, "keep": 2}
        assert ctx._host_config_change_pending == 0
        assert not getattr(ctx, "_pending_host_config_owner_tokens", {})

    asyncio.run(_case_effective_read_inside_failing_config_change_preserves_rollback_ownership())

    # 11. CONFIG_UPDATE respects shorter caller timeout and PluginProcessHost.send_config_update forwards it.
    async def _case_host_merge_respects_shorter_caller_timeout_from_command_msg() -> None:
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_short_caller_timeout" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance = _RecordingInstance()
        ctx._instance = instance
        received_timeouts: list[float] = []

        async def _recording_get_own_config(*, timeout: float = 5.0) -> dict[str, object]:
            received_timeouts.append(timeout)
            return {"config": {"base": 1}}

        ctx.get_own_config = _recording_get_own_config  # type: ignore[method-assign]
        sender = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-short-timeout",
                "config": {"host": 2},
                "mode": "temporary",
                "replace": False,
                "timeout": 1.25,
            },
            ctx=ctx,
            events_by_type={},
            plugin_id="demo",
            res_sender=sender,
            logger=_FakeLogger(),
        )
        assert len(received_timeouts) == 1
        assert 0.0 < received_timeouts[0] <= 1.25
        assert sender.payloads[-1]["success"] is True
        assert ctx._effective_config == {"base": 1, "host": 2}

        # Expired caller timeout (0.0s) must abort immediately without hydrating or applying patch.
        ctx_zero = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_zero_caller_timeout" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        zero_calls = 0

        async def _unexpected_get_own_config(*, timeout: float = 5.0) -> dict[str, object]:
            nonlocal zero_calls
            del timeout
            zero_calls += 1
            return {"config": {"base": 1}}

        ctx_zero.get_own_config = _unexpected_get_own_config  # type: ignore[method-assign]
        sender_zero = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-zero-timeout",
                "config": {"host": 9},
                "mode": "temporary",
                "replace": False,
                "timeout": 0.0,
            },
            ctx=ctx_zero,
            events_by_type={},
            plugin_id="demo",
            res_sender=sender_zero,
            logger=_FakeLogger(),
        )
        assert sender_zero.payloads[-1]["success"] is False
        assert "Timed out" in str(sender_zero.payloads[-1]["error"])
        assert zero_calls == 0
        assert ctx_zero._effective_config is None

        # Verify PluginProcessHost.send_config_update includes caller timeout in IPC cmd.
        sent_cmds: list[dict[str, object]] = []

        class _FakeComm:
            async def _send_command_and_wait(
                self,
                req_id: str,
                cmd: dict[str, object],
                timeout: float,
                command_type: str,
            ) -> dict[str, object]:
                del req_id, timeout, command_type
                sent_cmds.append(dict(cmd))
                return {"success": True, "config_applied": True, "handler_called": False}

        fake_host = host_module.PluginProcessHost.__new__(host_module.PluginProcessHost)
        fake_host.comm_manager = _FakeComm()  # type: ignore[assignment]
        await fake_host.send_config_update({"x": 1}, timeout=1.5)
        assert len(sent_cmds) == 1
        assert sent_cmds[0]["timeout"] == 1.5

    asyncio.run(_case_host_merge_respects_shorter_caller_timeout_from_command_msg())

    # 12. Profile switch during config_change must still install and flush fresh settings.
    async def _case_profile_switch_inside_or_concurrent_with_config_change() -> None:
        profiles = {
            "home": {"profile": "home", "store": {"enabled": False}},
            "work": {"profile": "work", "store": {"enabled": True}},
        }

        async def _null_local_payload(**_kwargs: object) -> None:
            return None

        # 12a. Callback calls set_own_active_profile() directly.
        ctx_cb = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_profile_switch_in_cb" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance_cb = _RecordingInstance()
        ctx_cb._instance = instance_cb
        ctx_cb._set_effective_config_cache(dict(profiles["home"]))
        active_cb = {"name": "home"}

        async def _rpc_cb(
            *,
            request_type: str,
            request_data: dict[str, object],
            **_kwargs: object,
        ) -> dict[str, object]:
            if request_type == "PLUGIN_CONFIG_PROFILE_ACTIVATE":
                target = str(request_data["profile_name"])
                active_cb["name"] = target
                return {"success": True, "active_profile": target}
            if request_type == "PLUGIN_CONFIG_GET":
                return {"plugin_id": "demo", "config": dict(profiles[active_cb["name"]])}
            raise AssertionError(f"Unexpected RPC request: {request_type}")

        ctx_cb._get_local_config_payload = _null_local_payload  # type: ignore[method-assign]
        ctx_cb._send_request_and_wait_async = _rpc_cb  # type: ignore[method-assign]
        inside_verified: list[bool] = []

        async def _switch_inside_cb(**_kwargs: object) -> None:
            await ctx_cb.set_own_active_profile("work")
            assert ctx_cb._effective_config == {"profile": "work", "store": {"enabled": True}}
            assert instance_cb.refreshed[-1] == {"profile": "work", "store": {"enabled": True}}
            inside_verified.append(True)

        sender_cb = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-profile-in-cb",
                "config": {"extra": 1},
                "mode": "temporary",
                "replace": False,
            },
            ctx=ctx_cb,
            events_by_type={"lifecycle": {"config_change": _switch_inside_cb}},
            plugin_id="demo",
            res_sender=sender_cb,
            logger=_FakeLogger(),
        )
        assert inside_verified == [True]
        assert sender_cb.payloads[-1]["success"] is True
        assert ctx_cb._effective_config == {"profile": "work", "store": {"enabled": True}}
        assert instance_cb.refreshed[-1] == {"profile": "work", "store": {"enabled": True}}
        assert ctx_cb._host_config_change_pending == 0
        assert not getattr(ctx_cb, "_pending_host_config_owner_tokens", {})

        # 12b. Concurrent timer/coroutine switches profile while config_change is awaiting.
        for callback_fails in (False, True):
            ctx_conc = PluginContext(
                plugin_id="demo",
                config_path=tmp_path / f"demo_profile_switch_conc_{callback_fails}" / "plugin.toml",
                logger=_FakeLogger(),  # type: ignore[arg-type]
                status_queue=None,
            )
            instance_conc = _RecordingInstance()
            ctx_conc._instance = instance_conc
            ctx_conc._set_effective_config_cache(dict(profiles["home"]))
            active_conc = {"name": "home"}

            async def _rpc_conc(
                *,
                request_type: str,
                request_data: dict[str, object],
                _active: dict[str, str] = active_conc,
                **_kwargs: object,
            ) -> dict[str, object]:
                if request_type == "PLUGIN_CONFIG_PROFILE_ACTIVATE":
                    target = str(request_data["profile_name"])
                    _active["name"] = target
                    return {"success": True, "active_profile": target}
                if request_type == "PLUGIN_CONFIG_GET":
                    return {"plugin_id": "demo", "config": dict(profiles[_active["name"]])}
                raise AssertionError(f"Unexpected RPC request: {request_type}")

            ctx_conc._get_local_config_payload = _null_local_payload  # type: ignore[method-assign]
            ctx_conc._send_request_and_wait_async = _rpc_conc  # type: ignore[method-assign]

            handler_entered = asyncio.Event()
            release_handler = asyncio.Event()

            async def _waiting_cb(
                *,
                _entered: asyncio.Event = handler_entered,
                _release: asyncio.Event = release_handler,
                _fail: bool = callback_fails,
                **_kwargs: object,
            ) -> None:
                _entered.set()
                await _release.wait()
                if _fail:
                    raise RuntimeError("cb failed after external profile switch")

            sender_conc = _FakeResponseSender()
            cmd_task = asyncio.create_task(
                host_module._handle_config_update_command(
                    msg={
                        "req_id": f"req-profile-conc-{callback_fails}",
                        "config": {"extra": 2},
                        "mode": "temporary",
                        "replace": False,
                    },
                    ctx=ctx_conc,
                    events_by_type={"lifecycle": {"config_change": _waiting_cb}},
                    plugin_id="demo",
                    res_sender=sender_conc,
                    logger=_FakeLogger(),
                )
            )
            try:
                await _wait_for_checkpoint_or_task_failure(handler_entered, cmd_task)
                await ctx_conc.set_own_active_profile("work")
                assert ctx_conc._effective_config == {"profile": "work", "store": {"enabled": True}}
                assert instance_conc.refreshed[-1] == {"profile": "work", "store": {"enabled": True}}

                release_handler.set()
                await cmd_task
                assert sender_conc.payloads[-1]["success"] is (not callback_fails)
                assert ctx_conc._effective_config == {"profile": "work", "store": {"enabled": True}}
                assert instance_conc.refreshed[-1] == {"profile": "work", "store": {"enabled": True}}
                assert ctx_conc._host_config_change_pending == 0
                assert not getattr(ctx_conc, "_pending_host_config_owner_tokens", {})
            finally:
                release_handler.set()
                if not cmd_task.done():
                    cmd_task.cancel()
                await asyncio.gather(cmd_task, return_exceptions=True)

    asyncio.run(_case_profile_switch_inside_or_concurrent_with_config_change())

    # 13. In-memory writes (persisted=False) inside config_change must keep read protection,
    #     while persisted writes and profile switches cleanly detach and allow reads.
    async def _case_in_memory_writes_preserve_host_read_guard_until_authoritative_change() -> None:
        # 13a. Chained persisted=False writes + get_own_effective_config(None) inside config_change.
        ctx_mem = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_in_memory_guard" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance_mem = _RecordingInstance()
        ctx_mem._instance = instance_mem
        ctx_mem._set_effective_config_cache({"base": 1})

        async def _stale_disk_local_payload(**_kwargs: object) -> dict[str, object]:
            return {"plugin_id": "demo", "config": {"base": 1}}

        async def _non_persisted_rpc(
            *,
            request_type: str,
            **_kwargs: object,
        ) -> dict[str, object]:
            assert request_type == "PLUGIN_CONFIG_UPDATE"
            return {"success": True, "persisted": False, "config": {}}

        ctx_mem._get_local_config_payload = _stale_disk_local_payload  # type: ignore[method-assign]
        ctx_mem._send_request_and_wait_async = _non_persisted_rpc  # type: ignore[method-assign]
        observed_reads_mem: list[dict[str, object]] = []

        async def _mem_writes_then_read(**_kwargs: object) -> None:
            res1 = await ctx_mem.update_own_config({"timer": 3})
            assert res1["persisted"] is False
            res2 = await ctx_mem.update_own_config({"timer2": 4})
            assert res2["persisted"] is False
            loaded = await ctx_mem.get_own_effective_config(None)
            observed_reads_mem.append(dict(loaded["config"]))
            assert ctx_mem._effective_config == {"base": 1, "extra": 3, "timer": 3, "timer2": 4}
            assert instance_mem.refreshed[-1] == {"base": 1, "extra": 3, "timer": 3, "timer2": 4}

        sender_mem = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-mem-guard",
                "config": {"extra": 3},
                "mode": "temporary",
                "replace": False,
            },
            ctx=ctx_mem,
            events_by_type={"lifecycle": {"config_change": _mem_writes_then_read}},
            plugin_id="demo",
            res_sender=sender_mem,
            logger=_FakeLogger(),
        )
        assert observed_reads_mem == [{"base": 1}]
        assert sender_mem.payloads[-1]["success"] is True
        assert ctx_mem._effective_config == {"base": 1, "extra": 3, "timer": 3, "timer2": 4}
        assert instance_mem.refreshed[-1] == {"base": 1, "extra": 3, "timer": 3, "timer2": 4}
        assert ctx_mem._host_config_change_pending == 0
        assert not getattr(ctx_mem, "_pending_host_config_owner_tokens", {})
        assert not getattr(ctx_mem, "_owner_token_host_change_root", {})

        # 13b. Control: persisted write detaches from pending host guard so subsequent read installs.
        ctx_persisted = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_persisted_detach" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance_persisted = _RecordingInstance()
        ctx_persisted._instance = instance_persisted
        ctx_persisted._set_effective_config_cache({"base": 1})
        disk_persisted: dict[str, object] = {"base": 1}

        async def _persisted_local_payload(**_kwargs: object) -> dict[str, object]:
            return {"plugin_id": "demo", "config": dict(disk_persisted)}

        async def _mixed_persist_rpc(
            *,
            request_type: str,
            request_data: dict[str, object],
            **_kwargs: object,
        ) -> dict[str, object]:
            assert request_type == "PLUGIN_CONFIG_UPDATE"
            updates = request_data["updates"]
            assert isinstance(updates, dict)
            if "timer" in updates:
                return {"success": True, "persisted": False, "config": {}}
            disk_persisted.clear()
            disk_persisted.update({"base": 1, "saved": 9, "from_disk": True})
            return {"success": True, "persisted": True, "config": {"base": 1, "saved": 9}}

        ctx_persisted._get_local_config_payload = _persisted_local_payload  # type: ignore[method-assign]
        ctx_persisted._send_request_and_wait_async = _mixed_persist_rpc  # type: ignore[method-assign]

        async def _mem_then_persisted_then_read(**_kwargs: object) -> None:
            await ctx_persisted.update_own_config({"timer": 3})
            await ctx_persisted.update_own_config({"saved": 9})
            loaded = await ctx_persisted.get_own_effective_config(None)
            assert loaded["config"] == {"base": 1, "saved": 9, "from_disk": True}
            assert ctx_persisted._effective_config == {"base": 1, "saved": 9, "from_disk": True}
            assert instance_persisted.refreshed[-1] == {"base": 1, "saved": 9, "from_disk": True}

        sender_persisted = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-persisted-detach",
                "config": {"extra": 3},
                "mode": "temporary",
                "replace": False,
            },
            ctx=ctx_persisted,
            events_by_type={"lifecycle": {"config_change": _mem_then_persisted_then_read}},
            plugin_id="demo",
            res_sender=sender_persisted,
            logger=_FakeLogger(),
        )
        assert sender_persisted.payloads[-1]["success"] is True
        assert ctx_persisted._effective_config == {"base": 1, "saved": 9, "from_disk": True}
        assert instance_persisted.refreshed[-1] == {"base": 1, "saved": 9, "from_disk": True}
        assert ctx_persisted._host_config_change_pending == 0
        assert not getattr(ctx_persisted, "_pending_host_config_owner_tokens", {})
        assert not getattr(ctx_persisted, "_owner_token_host_change_root", {})

        # 13c. Control: profile switch after persisted=False write inside config_change still refreshes.
        ctx_prof = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_mem_then_profile" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance_prof = _RecordingInstance()
        ctx_prof._instance = instance_prof
        profiles = {
            "home": {"profile": "home", "store": {"enabled": False}},
            "work": {"profile": "work", "store": {"enabled": True}},
        }
        ctx_prof._set_effective_config_cache(dict(profiles["home"]))
        active_prof = {"name": "home"}

        async def _null_local_payload(**_kwargs: object) -> None:
            return None

        async def _mem_then_profile_rpc(
            *,
            request_type: str,
            request_data: dict[str, object],
            **_kwargs: object,
        ) -> dict[str, object]:
            if request_type == "PLUGIN_CONFIG_UPDATE":
                return {"success": True, "persisted": False, "config": {}}
            if request_type == "PLUGIN_CONFIG_PROFILE_ACTIVATE":
                target = str(request_data["profile_name"])
                active_prof["name"] = target
                return {"success": True, "active_profile": target}
            if request_type == "PLUGIN_CONFIG_GET":
                return {"plugin_id": "demo", "config": dict(profiles[active_prof["name"]])}
            raise AssertionError(f"Unexpected RPC request: {request_type}")

        ctx_prof._get_local_config_payload = _null_local_payload  # type: ignore[method-assign]
        ctx_prof._send_request_and_wait_async = _mem_then_profile_rpc  # type: ignore[method-assign]

        async def _mem_then_switch_profile(**_kwargs: object) -> None:
            await ctx_prof.update_own_config({"timer": 3})
            await ctx_prof.set_own_active_profile("work")
            assert ctx_prof._effective_config == {"profile": "work", "store": {"enabled": True}}
            assert instance_prof.refreshed[-1] == {"profile": "work", "store": {"enabled": True}}

        sender_prof = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-mem-then-profile",
                "config": {"extra": 3},
                "mode": "temporary",
                "replace": False,
            },
            ctx=ctx_prof,
            events_by_type={"lifecycle": {"config_change": _mem_then_switch_profile}},
            plugin_id="demo",
            res_sender=sender_prof,
            logger=_FakeLogger(),
        )
        assert sender_prof.payloads[-1]["success"] is True
        assert ctx_prof._effective_config == {"profile": "work", "store": {"enabled": True}}
        assert instance_prof.refreshed[-1] == {"profile": "work", "store": {"enabled": True}}
        assert ctx_prof._host_config_change_pending == 0
        assert not getattr(ctx_prof, "_pending_host_config_owner_tokens", {})
        assert not getattr(ctx_prof, "_owner_token_host_change_root", {})

    asyncio.run(_case_in_memory_writes_preserve_host_read_guard_until_authoritative_change())

    # 14. Timed-out / cancelled optimistic writes retain host read protection for the
    #     retained draft while config_change runs, then allow re-anchoring afterward.
    async def _case_uncertain_writes_preserve_host_guard_while_draft_current() -> None:
        # 14a. TimeoutError (persisted=None) inside config_change preserves host+draft until handler exits.
        ctx_to = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_timeout_guard" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance_to = _RecordingInstance()
        ctx_to._instance = instance_to
        ctx_to._set_effective_config_cache({"base": 1})

        async def _disk_local_payload(**_kwargs: object) -> dict[str, object]:
            return {"plugin_id": "demo", "config": {"base": 1}}

        async def _timeout_rpc(**_kwargs: object) -> dict[str, object]:
            raise TimeoutError("ack timed out")

        ctx_to._get_local_config_payload = _disk_local_payload  # type: ignore[method-assign]
        ctx_to._send_request_and_wait_async = _timeout_rpc  # type: ignore[method-assign]

        async def _timeout_then_read(**_kwargs: object) -> None:
            res = await ctx_to.update_own_config({"timer": 3})
            assert res["persisted"] is None
            loaded = await ctx_to.get_own_effective_config(None)
            assert loaded["config"] == {"base": 1}
            assert ctx_to._effective_config == {"base": 1, "extra": 3, "timer": 3}
            assert ctx_to._effective_config_uncertain is True
            assert instance_to.refreshed[-1] == {"base": 1, "extra": 3, "timer": 3}

        sender_to = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-timeout-guard",
                "config": {"extra": 3},
                "mode": "temporary",
                "replace": False,
            },
            ctx=ctx_to,
            events_by_type={"lifecycle": {"config_change": _timeout_then_read}},
            plugin_id="demo",
            res_sender=sender_to,
            logger=_FakeLogger(),
        )
        assert sender_to.payloads[-1]["success"] is True
        assert ctx_to._effective_config == {"base": 1, "extra": 3, "timer": 3}
        assert ctx_to._effective_config_uncertain is True
        assert instance_to.refreshed[-1] == {"base": 1, "extra": 3, "timer": 3}
        assert ctx_to._host_config_change_pending == 0
        assert not getattr(ctx_to, "_pending_host_config_owner_tokens", {})
        assert not getattr(ctx_to, "_owner_token_host_change_root", {})

        post_loaded_to = await ctx_to.get_own_effective_config(None)
        assert post_loaded_to["config"] == {"base": 1}
        assert ctx_to._effective_config == {"base": 1}
        assert ctx_to._effective_config_uncertain is False
        assert instance_to.refreshed[-1] == {"base": 1}

        # 14b. CancelledError subtask inside config_change preserves host+draft until handler exits.
        ctx_cancel = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_cancel_guard" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance_cancel = _RecordingInstance()
        ctx_cancel._instance = instance_cancel
        ctx_cancel._set_effective_config_cache({"base": 1})

        cancel_rpc_started = asyncio.Event()
        release_cancel_rpc = asyncio.Event()

        async def _cancellable_rpc(**_kwargs: object) -> dict[str, object]:
            cancel_rpc_started.set()
            await release_cancel_rpc.wait()
            return {"success": True, "config": {"base": 1, "extra": 3, "timer": 3}}

        ctx_cancel._get_local_config_payload = _disk_local_payload  # type: ignore[method-assign]
        ctx_cancel._send_request_and_wait_async = _cancellable_rpc  # type: ignore[method-assign]

        async def _cancel_then_read(**_kwargs: object) -> None:
            write_task = asyncio.create_task(ctx_cancel.update_own_config({"timer": 3}))
            try:
                await _wait_for_checkpoint_or_task_failure(cancel_rpc_started, write_task)
                write_task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await write_task
            finally:
                release_cancel_rpc.set()
                if not write_task.done():
                    write_task.cancel()
                await asyncio.gather(write_task, return_exceptions=True)

            loaded = await ctx_cancel.get_own_effective_config(None)
            assert loaded["config"] == {"base": 1}
            assert ctx_cancel._effective_config == {"base": 1, "extra": 3, "timer": 3}
            assert ctx_cancel._effective_config_uncertain is True
            assert instance_cancel.refreshed[-1] == {"base": 1, "extra": 3, "timer": 3}

        sender_cancel = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-cancel-guard",
                "config": {"extra": 3},
                "mode": "temporary",
                "replace": False,
            },
            ctx=ctx_cancel,
            events_by_type={"lifecycle": {"config_change": _cancel_then_read}},
            plugin_id="demo",
            res_sender=sender_cancel,
            logger=_FakeLogger(),
        )
        assert sender_cancel.payloads[-1]["success"] is True
        assert ctx_cancel._effective_config == {"base": 1, "extra": 3, "timer": 3}
        assert ctx_cancel._effective_config_uncertain is True
        assert instance_cancel.refreshed[-1] == {"base": 1, "extra": 3, "timer": 3}
        assert ctx_cancel._host_config_change_pending == 0
        assert not getattr(ctx_cancel, "_pending_host_config_owner_tokens", {})
        assert not getattr(ctx_cancel, "_owner_token_host_change_root", {})

        post_loaded_cancel = await ctx_cancel.get_own_effective_config(None)
        assert post_loaded_cancel["config"] == {"base": 1}
        assert ctx_cancel._effective_config == {"base": 1}
        assert ctx_cancel._effective_config_uncertain is False
        assert instance_cancel.refreshed[-1] == {"base": 1}

        # 14c. Control: superseded draft before TimeoutError does not inherit host guard.
        ctx_sup = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_superseded_timeout" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance_sup = _RecordingInstance()
        ctx_sup._instance = instance_sup
        ctx_sup._set_effective_config_cache({"base": 1})

        sup_rpc_started = asyncio.Event()
        release_sup_rpc = asyncio.Event()

        async def _sup_local_payload(**_kwargs: object) -> dict[str, object]:
            return {"plugin_id": "demo", "config": {"base": 1, "superseded": True}}

        async def _sup_timeout_rpc(**_kwargs: object) -> dict[str, object]:
            sup_rpc_started.set()
            await release_sup_rpc.wait()
            raise TimeoutError("superseded timeout")

        ctx_sup._get_local_config_payload = _sup_local_payload  # type: ignore[method-assign]
        ctx_sup._send_request_and_wait_async = _sup_timeout_rpc  # type: ignore[method-assign]

        async def _superseded_then_read(**_kwargs: object) -> None:
            write_task = asyncio.create_task(ctx_sup.update_own_config({"timer": 3}))
            try:
                await _wait_for_checkpoint_or_task_failure(sup_rpc_started, write_task)
                ctx_sup._invalidate_effective_config_for_profile_refresh()
                release_sup_rpc.set()
                res = await write_task
                assert res["persisted"] is None
            finally:
                release_sup_rpc.set()
                if not write_task.done():
                    write_task.cancel()
                await asyncio.gather(write_task, return_exceptions=True)

            root_map = getattr(ctx_sup, "_owner_token_host_change_root", {})
            assert ctx_sup._effective_config_owner_token not in root_map
            loaded = await ctx_sup.get_own_effective_config(None)
            assert loaded["config"] == {"base": 1, "superseded": True}
            assert ctx_sup._effective_config == {"base": 1, "superseded": True}
            assert ctx_sup._effective_config_uncertain is False
            assert instance_sup.refreshed[-1] == {"base": 1, "superseded": True}

        sender_sup = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-sup-timeout",
                "config": {"extra": 3},
                "mode": "temporary",
                "replace": False,
            },
            ctx=ctx_sup,
            events_by_type={"lifecycle": {"config_change": _superseded_then_read}},
            plugin_id="demo",
            res_sender=sender_sup,
            logger=_FakeLogger(),
        )
        assert sender_sup.payloads[-1]["success"] is True
        assert ctx_sup._host_config_change_pending == 0
        assert not getattr(ctx_sup, "_pending_host_config_owner_tokens", {})
        assert not getattr(ctx_sup, "_owner_token_host_change_root", {})

    asyncio.run(_case_uncertain_writes_preserve_host_guard_while_draft_current())

    # 15. Superseded optimistic write timing out inside host replace config_change
    #     must not steal host owner token for reads or host rollback.
    async def _case_superseded_stale_write_timeout_preserves_host_ownership(
        *,
        raises_in_cb: bool,
    ) -> None:
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / f"demo_stale_timeout_rb_{raises_in_cb}" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance = _RecordingInstance()
        ctx._instance = instance
        ctx._set_effective_config_cache({"base": 1})

        rpc_started = asyncio.Event()
        release_rpc = asyncio.Event()

        async def _disk_local_payload(**_kwargs: object) -> dict[str, object]:
            return {"plugin_id": "demo", "config": {"base": 1}}

        async def _slow_timeout_rpc(**_kwargs: object) -> dict[str, object]:
            rpc_started.set()
            await release_rpc.wait()
            raise TimeoutError("stale write timed out")

        ctx._get_local_config_payload = _disk_local_payload  # type: ignore[method-assign]
        ctx._send_request_and_wait_async = _slow_timeout_rpc  # type: ignore[method-assign]

        write_task = asyncio.create_task(ctx.update_own_config({"draft": 1}))
        try:
            await _wait_for_checkpoint_or_task_failure(rpc_started, write_task)

            async def _config_change(**_kwargs: object) -> None:
                release_rpc.set()
                res = await write_task
                assert res["persisted"] is None
                loaded = await ctx.get_own_effective_config(None)
                assert loaded["config"] == {"base": 1}
                assert ctx._effective_config == {"host": 9}
                assert instance.refreshed[-1] == {"host": 9}
                assert ctx._effective_config_uncertain is True
                if raises_in_cb:
                    raise RuntimeError("host update rejected after stale timeout")

            sender = _FakeResponseSender()
            await host_module._handle_config_update_command(
                msg={
                    "req_id": f"req-stale-to-{raises_in_cb}",
                    "config": {"host": 9},
                    "mode": "temporary",
                    "replace": True,
                },
                ctx=ctx,
                events_by_type={"lifecycle": {"config_change": _config_change}},
                plugin_id="demo",
                res_sender=sender,
                logger=_FakeLogger(),
            )
            assert sender.payloads[-1]["success"] is (not raises_in_cb)
            if raises_in_cb:
                assert "host update rejected after stale timeout" in str(
                    sender.payloads[-1].get("error", "")
                )
            expected_cfg = {"base": 1} if raises_in_cb else {"host": 9}
            assert ctx._effective_config == expected_cfg
            assert instance.refreshed[-1] == expected_cfg
            assert ctx._effective_config_uncertain is True
            assert ctx._host_config_change_pending == 0
            assert not getattr(ctx, "_pending_host_config_owner_tokens", {})
            assert not getattr(ctx, "_owner_token_host_change_root", {})

            post_loaded = await ctx.get_own_effective_config(None)
            assert post_loaded["config"] == {"base": 1}
            assert ctx._effective_config == {"base": 1}
            assert ctx._effective_config_uncertain is False
            assert instance.refreshed[-1] == {"base": 1}
        finally:
            release_rpc.set()
            if not write_task.done():
                write_task.cancel()
            await asyncio.gather(write_task, return_exceptions=True)

    asyncio.run(_case_superseded_stale_write_timeout_preserves_host_ownership(raises_in_cb=False))
    asyncio.run(_case_superseded_stale_write_timeout_preserves_host_ownership(raises_in_cb=True))

    # 16. Host publish paths must register read protection atomically before flushing/returning,
    #     across replace, cached merge, and uncached hydrated merge, without leaking when no handler runs.
    async def _case_host_publish_registers_read_guard_before_flush() -> None:
        class _ProbingInstance(_RecordingInstance):
            def __init__(self, target_ctx: PluginContext, probe: dict[str, object]) -> None:
                super().__init__()
                self._target_ctx = target_ctx
                self._probe = probe

            def refresh_runtime_config(self, effective_config: dict[str, object]) -> None:
                super().refresh_runtime_config(effective_config)
                if effective_config.get("host") == 9 and not self._probe["probed"]:
                    self._probe["probed"] = True
                    self._probe["owner_during_flush"] = self._target_ctx._effective_config_owner_token
                    self._probe["pending_during_flush"] = dict(
                        getattr(self._target_ctx, "_pending_host_config_owner_tokens", {})
                    )
                    gen_before, read_id = self._target_ctx._begin_config_read()
                    self._probe["read_installed_during_flush"] = (
                        self._target_ctx._cache_effective_config_from_read(
                            {"base": 1, "stale_disk": True},
                            gen_before=gen_before,
                            read_id=read_id,
                        )
                    )

        for mode_label in ("replace", "cached_merge", "hydrated_merge"):
            probe: dict[str, object] = {"probed": False}
            ctx = PluginContext(
                plugin_id="demo",
                config_path=tmp_path / f"demo_atomic_guard_{mode_label}" / "plugin.toml",
                logger=_FakeLogger(),  # type: ignore[arg-type]
                status_queue=None,
            )
            instance = _ProbingInstance(ctx, probe)
            ctx._instance = instance
            if mode_label == "hydrated_merge":
                ctx._set_effective_config_cache({"base": 1})
                ctx._invalidate_effective_config_for_profile_refresh()

                async def _hydrate_get_own_config(*, timeout: float = 5.0) -> dict[str, object]:
                    del timeout
                    return {"config": {"base": 1}}

                ctx.get_own_config = _hydrate_get_own_config  # type: ignore[method-assign]
            else:
                ctx._set_effective_config_cache({"base": 1})

            def _reject(**_kwargs: object) -> None:
                raise RuntimeError("reject after atomic guard")

            sender = _FakeResponseSender()
            await host_module._handle_config_update_command(
                msg={
                    "req_id": f"req-atomic-{mode_label}",
                    "config": {"host": 9},
                    "mode": "temporary",
                    "replace": mode_label == "replace",
                },
                ctx=ctx,
                events_by_type={"lifecycle": {"config_change": _reject}},
                plugin_id="demo",
                res_sender=sender,
                logger=_FakeLogger(),
            )
            assert probe["probed"] is True
            assert probe["read_installed_during_flush"] is False
            owner_during_flush = probe["owner_during_flush"]
            pending_during_flush = probe["pending_during_flush"]
            assert isinstance(pending_during_flush, dict)
            assert pending_during_flush.get(owner_during_flush) == 1
            assert sender.payloads[-1]["success"] is False
            if mode_label == "hydrated_merge":
                assert ctx._effective_config is None
            else:
                assert ctx._effective_config == {"base": 1}
            assert instance.refreshed[-1] == {"base": 1}
            assert ctx._host_config_change_pending == 0
            assert not getattr(ctx, "_pending_host_config_owner_tokens", {})
            assert not getattr(ctx, "_owner_token_host_change_root", {})

        # Control: without config_change handler, no pending guard is registered or leaked.
        probe_no_cb: dict[str, object] = {"probed": False}
        ctx_no_cb = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_atomic_guard_no_cb" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        ctx_no_cb._instance = _ProbingInstance(ctx_no_cb, probe_no_cb)
        ctx_no_cb._set_effective_config_cache({"base": 1})
        sender_no_cb = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-atomic-no-cb",
                "config": {"host": 9},
                "mode": "temporary",
                "replace": False,
            },
            ctx=ctx_no_cb,
            events_by_type={},
            plugin_id="demo",
            res_sender=sender_no_cb,
            logger=_FakeLogger(),
        )
        assert probe_no_cb["probed"] is True
        assert probe_no_cb["pending_during_flush"] == {}
        assert probe_no_cb["read_installed_during_flush"] is True
        assert sender_no_cb.payloads[-1]["success"] is True
        assert int(getattr(ctx_no_cb, "_host_config_change_pending", 0) or 0) == 0
        assert not getattr(ctx_no_cb, "_pending_host_config_owner_tokens", {})

    asyncio.run(_case_host_publish_registers_read_guard_before_flush())

    # 17. Profile request timing out inside config_change must preserve host read
    #     protection and rollback ownership until the callback exits.
    async def _case_profile_write_timeout_inside_config_change_preserves_host_guard(
        *,
        raises_in_cb: bool,
    ) -> None:
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / f"demo_profile_timeout_guard_{raises_in_cb}" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance = _RecordingInstance()
        ctx._instance = instance
        ctx._set_effective_config_cache({"base": 1})

        async def _disk_local_payload(**_kwargs: object) -> dict[str, object]:
            return {"plugin_id": "demo", "config": {"base": 1}}

        async def _profile_timeout_rpc(**_kwargs: object) -> dict[str, object]:
            raise TimeoutError("profile upsert timed out")

        ctx._get_local_config_payload = _disk_local_payload  # type: ignore[method-assign]
        ctx._send_request_and_wait_async = _profile_timeout_rpc  # type: ignore[method-assign]

        async def _config_change(**_kwargs: object) -> None:
            with pytest.raises(TimeoutError):
                await ctx.upsert_own_profile_config("inactive", {"k": 1})
            loaded = await ctx.get_own_effective_config(None)
            assert loaded["config"] == {"base": 1}
            assert ctx._effective_config == {"base": 1, "extra": 3}
            assert instance.refreshed[-1] == {"base": 1, "extra": 3}
            assert ctx._effective_config_uncertain is True
            if raises_in_cb:
                raise RuntimeError("reject after profile timeout")

        sender = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": f"req-profile-to-guard-{raises_in_cb}",
                "config": {"extra": 3},
                "mode": "temporary",
                "replace": False,
            },
            ctx=ctx,
            events_by_type={"lifecycle": {"config_change": _config_change}},
            plugin_id="demo",
            res_sender=sender,
            logger=_FakeLogger(),
        )
        assert sender.payloads[-1]["success"] is (not raises_in_cb)
        if raises_in_cb:
            assert "reject after profile timeout" in str(
                sender.payloads[-1].get("error", "")
            )
        expected_cfg = {"base": 1} if raises_in_cb else {"base": 1, "extra": 3}
        assert ctx._effective_config == expected_cfg
        assert instance.refreshed[-1] == expected_cfg
        assert ctx._effective_config_uncertain is True
        assert ctx._host_config_change_pending == 0
        assert not getattr(ctx, "_pending_host_config_owner_tokens", {})
        assert not getattr(ctx, "_owner_token_host_change_root", {})

        post_loaded = await ctx.get_own_effective_config(None)
        assert post_loaded["config"] == {"base": 1}
        assert ctx._effective_config == {"base": 1}
        assert ctx._effective_config_uncertain is False
        assert instance.refreshed[-1] == {"base": 1}

    asyncio.run(_case_profile_write_timeout_inside_config_change_preserves_host_guard(raises_in_cb=False))
    asyncio.run(_case_profile_write_timeout_inside_config_change_preserves_host_guard(raises_in_cb=True))

    # 18. Superseded committed write whose gen mismatches host replace snapshot
    #     must preserve host read guard and rollback ownership inside config_change.
    async def _case_superseded_commit_inside_host_replace_preserves_host_guard(
        *,
        raises_in_cb: bool,
    ) -> None:
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / f"demo_superseded_commit_{raises_in_cb}" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance = _RecordingInstance()
        ctx._instance = instance
        ctx._set_effective_config_cache({"base": 1})

        rpc_started = asyncio.Event()
        release_rpc = asyncio.Event()

        async def _disk_local_payload(**_kwargs: object) -> dict[str, object]:
            return {"plugin_id": "demo", "config": {"base": 1, "draft": 1}}

        async def _slow_commit_rpc(**_kwargs: object) -> dict[str, object]:
            rpc_started.set()
            await release_rpc.wait()
            return {"success": True, "persisted": True, "config": {"base": 1, "draft": 1}}

        ctx._get_local_config_payload = _disk_local_payload  # type: ignore[method-assign]
        ctx._send_request_and_wait_async = _slow_commit_rpc  # type: ignore[method-assign]

        write_task = asyncio.create_task(ctx.update_own_config({"draft": 1}))
        try:
            await _wait_for_checkpoint_or_task_failure(rpc_started, write_task)

            async def _config_change(**_kwargs: object) -> None:
                release_rpc.set()
                res = await write_task
                assert res["persisted"] is True
                loaded = await ctx.get_own_effective_config(None)
                assert loaded["config"] == {"base": 1, "draft": 1}
                assert ctx._effective_config == {"host": 9}
                assert instance.refreshed[-1] == {"host": 9}
                assert ctx._effective_config_uncertain is True
                if raises_in_cb:
                    raise RuntimeError("reject after superseded commit")

            sender = _FakeResponseSender()
            await host_module._handle_config_update_command(
                msg={
                    "req_id": f"req-sup-commit-{raises_in_cb}",
                    "config": {"host": 9},
                    "mode": "temporary",
                    "replace": True,
                },
                ctx=ctx,
                events_by_type={"lifecycle": {"config_change": _config_change}},
                plugin_id="demo",
                res_sender=sender,
                logger=_FakeLogger(),
            )
            assert sender.payloads[-1]["success"] is (not raises_in_cb)
            if raises_in_cb:
                assert "reject after superseded commit" in str(
                    sender.payloads[-1].get("error", "")
                )
            expected_cfg = {"base": 1} if raises_in_cb else {"host": 9}
            assert ctx._effective_config == expected_cfg
            assert instance.refreshed[-1] == expected_cfg
            assert ctx._effective_config_uncertain is True
            assert ctx._host_config_change_pending == 0
            assert not getattr(ctx, "_pending_host_config_owner_tokens", {})
            assert not getattr(ctx, "_owner_token_host_change_root", {})

            post_loaded = await ctx.get_own_effective_config(None)
            assert post_loaded["config"] == {"base": 1, "draft": 1}
            assert ctx._effective_config == {"base": 1, "draft": 1}
            assert ctx._effective_config_uncertain is False
            assert instance.refreshed[-1] == {"base": 1, "draft": 1}
        finally:
            release_rpc.set()
            if not write_task.done():
                write_task.cancel()
            await asyncio.gather(write_task, return_exceptions=True)

    asyncio.run(_case_superseded_commit_inside_host_replace_preserves_host_guard(raises_in_cb=False))
    asyncio.run(_case_superseded_commit_inside_host_replace_preserves_host_guard(raises_in_cb=True))

    # 19. Completed in-memory-only (persisted=False) and timed-out (persisted=None) writes inside a
    #     rejecting config_change handler must rebase local patches onto the prechange snapshot.
    async def _case_non_persisted_writes_rebase_on_host_rollback() -> None:
        # 19a. Single & chained persisted=False update_own_config calls with replace=False and replace=True.
        for replace in (False, True):
            ctx = PluginContext(
                plugin_id="demo",
                config_path=tmp_path / f"demo_mem_rb_{replace}" / "plugin.toml",
                logger=_FakeLogger(),  # type: ignore[arg-type]
                status_queue=None,
            )
            instance = _RecordingInstance()
            ctx._instance = instance
            initial_cfg: dict[str, object] = {
                "base": 1,
                "keep": {"a": 1, "b": 2},
                "drop_me": "keep",
                "scalar_sec": 1,
            }
            ctx._set_effective_config_cache(initial_cfg)

            orig_local_payload = ctx._get_local_config_payload

            async def _disk_local_payload(
                *,
                payload_type: str = "config",
                **kwargs: object,
            ) -> dict[str, object]:
                if (
                    payload_type == "config"
                    and isinstance(ctx._effective_config, dict)
                    and not ctx._effective_config_uncertain
                ):
                    res = await orig_local_payload(payload_type="config", **kwargs)
                    if isinstance(res, dict):
                        return res
                return {"plugin_id": "demo", "config": dict(initial_cfg)}

            async def _non_persisted_rpc(**_kwargs: object) -> dict[str, object]:
                return {"success": True, "persisted": False, "config": {}}

            ctx._get_local_config_payload = _disk_local_payload  # type: ignore[method-assign]
            ctx._send_request_and_wait_async = _non_persisted_rpc  # type: ignore[method-assign]
            observed_reads: list[dict[str, object]] = []

            async def _mem_writes_then_reject(
                *,
                _replace: bool = replace,
                _ctx: PluginContext = ctx,
                _reads: list[dict[str, object]] = observed_reads,
                **_kwargs: object,
            ) -> None:
                res1 = await _ctx.update_own_config(
                    {
                        "timer": 3,
                        "keep": {"c": 3},
                        "drop_me": "__DELETE__",
                        "temp_section": {"obsolete": "__DELETE__", "enabled": True},
                        "scalar_sec": {"obsolete": "__DELETE__", "ok": True},
                    }
                )
                assert res1["persisted"] is False
                res2 = await _ctx.update_own_config({"timer2": 4, "keep": {"b": 20}})
                assert res2["persisted"] is False
                loaded = await _ctx.get_own_effective_config(None)
                _reads.append(dict(loaded["config"]))
                raise RuntimeError(f"reject after mem writes replace={_replace}")

            sender = _FakeResponseSender()
            await host_module._handle_config_update_command(
                msg={
                    "req_id": f"req-mem-rb-{replace}",
                    "config": {
                        "host": 9,
                        "keep": {"a": 99, "host_nested": True},
                        "temp_section": {"obsolete": 1},
                    },
                    "mode": "temporary",
                    "replace": replace,
                },
                ctx=ctx,
                events_by_type={"lifecycle": {"config_change": _mem_writes_then_reject}},
                plugin_id="demo",
                res_sender=sender,
                logger=_FakeLogger(),
            )
            assert observed_reads == [initial_cfg]
            assert sender.payloads[-1]["success"] is False
            assert f"reject after mem writes replace={replace}" in str(
                sender.payloads[-1].get("error", "")
            )
            expected_rebased = {
                "base": 1,
                "keep": {"a": 1, "b": 20, "c": 3},
                "temp_section": {"enabled": True},
                "scalar_sec": {"ok": True},
                "timer": 3,
                "timer2": 4,
            }
            assert ctx._effective_config == expected_rebased
            assert ctx._effective_config_uncertain is False
            assert instance.refreshed[-1] == expected_rebased
            dumped = await ctx.get_own_config()
            assert dumped["config"] == expected_rebased
            assert ctx._host_config_change_pending == 0
            assert not getattr(ctx, "_pending_host_config_owner_tokens", {})
            assert not getattr(ctx, "_owner_token_host_change_root", {})
            assert getattr(ctx, "_non_persisted_host_lineage", None) is None

        # 19b. Uncached (had_cached=False) host replace=True + persisted=False write restores None.
        ctx_uncached = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_mem_rb_uncached" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance_uncached = _RecordingInstance()
        ctx_uncached._instance = instance_uncached
        ctx_uncached._set_effective_config_cache({"base": 1, "mode": "old"})
        ctx_uncached._invalidate_effective_config_for_profile_refresh()
        assert ctx_uncached._effective_config is None

        async def _uncached_non_persisted_rpc(**_kwargs: object) -> dict[str, object]:
            return {"success": True, "persisted": False, "config": {}}

        ctx_uncached._send_request_and_wait_async = _uncached_non_persisted_rpc  # type: ignore[method-assign]

        async def _uncached_mem_then_reject(**_kwargs: object) -> None:
            res = await ctx_uncached.update_own_config({"timer": 3})
            assert res["persisted"] is False
            raise RuntimeError("reject uncached replace after mem write")

        sender_uncached = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-mem-rb-uncached",
                "config": {"host": 9},
                "mode": "temporary",
                "replace": True,
            },
            ctx=ctx_uncached,
            events_by_type={"lifecycle": {"config_change": _uncached_mem_then_reject}},
            plugin_id="demo",
            res_sender=sender_uncached,
            logger=_FakeLogger(),
        )
        assert sender_uncached.payloads[-1]["success"] is False
        assert "reject uncached replace after mem write" in str(
            sender_uncached.payloads[-1].get("error", "")
        )
        assert ctx_uncached._effective_config is None
        assert ctx_uncached._effective_config_uncertain is False
        assert instance_uncached.refreshed[-1] == {"base": 1, "mode": "old"}
        assert ctx_uncached._host_config_change_pending == 0
        assert not getattr(ctx_uncached, "_pending_host_config_owner_tokens", {})
        assert not getattr(ctx_uncached, "_owner_token_host_change_root", {})
        assert getattr(ctx_uncached, "_non_persisted_host_lineage", None) is None

        # 19c(i). Chained persisted=False write + subsequent failed update_own_config + rejecting callback.
        ctx_chain_fail = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_mem_rb_chain_fail" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance_chain_fail = _RecordingInstance()
        ctx_chain_fail._instance = instance_chain_fail
        ctx_chain_fail._set_effective_config_cache({"base": 1})

        async def _base_local_payload(**_kwargs: object) -> dict[str, object]:
            return {"plugin_id": "demo", "config": {"base": 1}}

        async def _first_mem_second_fail_rpc(
            *,
            request_data: dict[str, object],
            **_kwargs: object,
        ) -> dict[str, object]:
            updates = request_data["updates"]
            assert isinstance(updates, dict)
            if "bad" in updates:
                raise RuntimeError("second write rpc failed")
            return {"success": True, "persisted": False, "config": {}}

        ctx_chain_fail._get_local_config_payload = _base_local_payload  # type: ignore[method-assign]
        ctx_chain_fail._send_request_and_wait_async = _first_mem_second_fail_rpc  # type: ignore[method-assign]

        async def _mem_then_fail_write_then_reject(**_kwargs: object) -> None:
            res1 = await ctx_chain_fail.update_own_config({"timer": 3})
            assert res1["persisted"] is False
            with pytest.raises(RuntimeError, match="second write rpc failed"):
                await ctx_chain_fail.update_own_config({"bad": 1})
            raise RuntimeError("reject after second write fail")

        sender_chain_fail = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-mem-rb-chain-fail",
                "config": {"host": 9},
                "mode": "temporary",
                "replace": False,
            },
            ctx=ctx_chain_fail,
            events_by_type={"lifecycle": {"config_change": _mem_then_fail_write_then_reject}},
            plugin_id="demo",
            res_sender=sender_chain_fail,
            logger=_FakeLogger(),
        )
        assert sender_chain_fail.payloads[-1]["success"] is False
        assert "reject after second write fail" in str(
            sender_chain_fail.payloads[-1].get("error", "")
        )
        assert ctx_chain_fail._effective_config == {"base": 1, "timer": 3}
        assert ctx_chain_fail._effective_config_uncertain is False
        assert instance_chain_fail.refreshed[-1] == {"base": 1, "timer": 3}
        assert getattr(ctx_chain_fail, "_non_persisted_host_lineage", None) is None

        # 19c(ii). Chained persisted=False write + overlapping in-flight persisted=False write.
        ctx_overlap = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_mem_rb_overlap" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance_overlap = _RecordingInstance()
        ctx_overlap._instance = instance_overlap
        ctx_overlap._set_effective_config_cache({"base": 1})

        second_write_started = asyncio.Event()
        release_second_write = asyncio.Event()

        async def _first_fast_second_slow_mem_rpc(
            *,
            request_data: dict[str, object],
            **_kwargs: object,
        ) -> dict[str, object]:
            updates = request_data["updates"]
            assert isinstance(updates, dict)
            if "timer2" in updates:
                second_write_started.set()
                await release_second_write.wait()
            return {"success": True, "persisted": False, "config": {}}

        ctx_overlap._get_local_config_payload = _base_local_payload  # type: ignore[method-assign]
        ctx_overlap._send_request_and_wait_async = _first_fast_second_slow_mem_rpc  # type: ignore[method-assign]
        overlap_tasks: list[asyncio.Task[dict[str, object]]] = []

        async def _mem_then_overlapping_mem_then_reject(**_kwargs: object) -> None:
            res1 = await ctx_overlap.update_own_config({"timer": 3})
            assert res1["persisted"] is False
            write2 = asyncio.create_task(ctx_overlap.update_own_config({"timer2": 4}))
            overlap_tasks.append(write2)
            await _wait_for_checkpoint_or_task_failure(second_write_started, write2)
            raise RuntimeError("reject while second mem write pending")

        sender_overlap = _FakeResponseSender()
        try:
            await host_module._handle_config_update_command(
                msg={
                    "req_id": "req-mem-rb-overlap",
                    "config": {"host": 9},
                    "mode": "temporary",
                    "replace": False,
                },
                ctx=ctx_overlap,
                events_by_type={
                    "lifecycle": {"config_change": _mem_then_overlapping_mem_then_reject}
                },
                plugin_id="demo",
                res_sender=sender_overlap,
                logger=_FakeLogger(),
            )
            assert sender_overlap.payloads[-1]["success"] is False
            assert "reject while second mem write pending" in str(
                sender_overlap.payloads[-1].get("error", "")
            )
            release_second_write.set()
            res2 = await overlap_tasks[0]
            assert res2["persisted"] is False
            assert res2["config"] == {"base": 1, "timer": 3, "timer2": 4}
            assert ctx_overlap._effective_config == {"base": 1, "timer": 3, "timer2": 4}
            assert ctx_overlap._effective_config_uncertain is False
            assert instance_overlap.refreshed[-1] == {"base": 1, "timer": 3, "timer2": 4}
            assert ctx_overlap._host_config_change_pending == 0
            assert not getattr(ctx_overlap, "_pending_host_config_owner_tokens", {})
            assert not getattr(ctx_overlap, "_owner_token_host_change_root", {})
            assert getattr(ctx_overlap, "_non_persisted_host_lineage", None) is None
        finally:
            release_second_write.set()
            for pending in overlap_tasks:
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(*overlap_tasks, return_exceptions=True)

        # 19d(i). Completed TimeoutError (persisted=None) inside rejecting config_change rebases.
        ctx_timeout = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_mem_rb_timeout" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance_timeout = _RecordingInstance()
        ctx_timeout._instance = instance_timeout
        ctx_timeout._set_effective_config_cache({"base": 1})

        async def _timeout_write_rpc(**_kwargs: object) -> dict[str, object]:
            raise TimeoutError("write timed out")

        ctx_timeout._get_local_config_payload = _base_local_payload  # type: ignore[method-assign]
        ctx_timeout._send_request_and_wait_async = _timeout_write_rpc  # type: ignore[method-assign]

        async def _timeout_then_reject(**_kwargs: object) -> None:
            res = await ctx_timeout.update_own_config({"timer": 3})
            assert res["persisted"] is None
            raise RuntimeError("reject after timed out write")

        sender_timeout = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-mem-rb-timeout",
                "config": {"host": 9},
                "mode": "temporary",
                "replace": False,
            },
            ctx=ctx_timeout,
            events_by_type={"lifecycle": {"config_change": _timeout_then_reject}},
            plugin_id="demo",
            res_sender=sender_timeout,
            logger=_FakeLogger(),
        )
        assert sender_timeout.payloads[-1]["success"] is False
        assert "reject after timed out write" in str(
            sender_timeout.payloads[-1].get("error", "")
        )
        assert ctx_timeout._effective_config == {"base": 1, "timer": 3}
        assert instance_timeout.refreshed[-1] == {"base": 1, "timer": 3}
        assert ctx_timeout._effective_config_uncertain is True
        post_to = await ctx_timeout.get_own_effective_config(None)
        assert post_to["config"] == {"base": 1}
        assert ctx_timeout._effective_config == {"base": 1}
        assert ctx_timeout._effective_config_uncertain is False
        assert instance_timeout.refreshed[-1] == {"base": 1}

        # 19d(ii). In-flight write timing out after config_change already rejected also rebases.
        ctx_to_overlap = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_mem_rb_timeout_overlap" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance_to_overlap = _RecordingInstance()
        ctx_to_overlap._instance = instance_to_overlap
        ctx_to_overlap._set_effective_config_cache({"base": 1})

        to_started = asyncio.Event()
        release_to = asyncio.Event()

        async def _slow_timeout_rpc(**_kwargs: object) -> dict[str, object]:
            to_started.set()
            await release_to.wait()
            raise TimeoutError("overlap write timed out")

        ctx_to_overlap._get_local_config_payload = _base_local_payload  # type: ignore[method-assign]
        ctx_to_overlap._send_request_and_wait_async = _slow_timeout_rpc  # type: ignore[method-assign]
        to_tasks: list[asyncio.Task[dict[str, object]]] = []

        async def _spawn_slow_timeout_and_reject(**_kwargs: object) -> None:
            wt = asyncio.create_task(ctx_to_overlap.update_own_config({"timer": 3}))
            to_tasks.append(wt)
            await _wait_for_checkpoint_or_task_failure(to_started, wt)
            raise RuntimeError("reject before overlap timeout")

        sender_to_overlap = _FakeResponseSender()
        try:
            await host_module._handle_config_update_command(
                msg={
                    "req_id": "req-mem-rb-to-overlap",
                    "config": {"host": 9},
                    "mode": "temporary",
                    "replace": False,
                },
                ctx=ctx_to_overlap,
                events_by_type={"lifecycle": {"config_change": _spawn_slow_timeout_and_reject}},
                plugin_id="demo",
                res_sender=sender_to_overlap,
                logger=_FakeLogger(),
            )
            assert sender_to_overlap.payloads[-1]["success"] is False
            assert "reject before overlap timeout" in str(
                sender_to_overlap.payloads[-1].get("error", "")
            )
            release_to.set()
            res_to = await to_tasks[0]
            assert res_to["persisted"] is None
            assert res_to["config"] == {"base": 1, "timer": 3}
            assert ctx_to_overlap._effective_config == {"base": 1, "timer": 3}
            assert instance_to_overlap.refreshed[-1] == {"base": 1, "timer": 3}
            assert ctx_to_overlap._effective_config_uncertain is True
        finally:
            release_to.set()
            for pending in to_tasks:
                if not pending.done():
                    pending.cancel()
            await asyncio.gather(*to_tasks, return_exceptions=True)

        # 19d(iii). Timed-out first write followed by a failing or persisted=False second write while
        #           config_change rejects must keep _effective_config_uncertain=True across rebase.
        for second_outcome in ("fail", "persisted_false"):
            ctx_u = PluginContext(
                plugin_id="demo",
                config_path=tmp_path / f"demo_mem_rb_uncertain_{second_outcome}" / "plugin.toml",
                logger=_FakeLogger(),  # type: ignore[arg-type]
                status_queue=None,
            )
            instance_u = _RecordingInstance()
            ctx_u._instance = instance_u
            ctx_u._set_effective_config_cache({"base": 1})

            second_started_u = asyncio.Event()
            release_second_u = asyncio.Event()
            orig_local_u = ctx_u._get_local_config_payload

            async def _uncertain_local_payload(
                *,
                payload_type: str = "config",
                _orig: object = orig_local_u,
                _ctx: PluginContext = ctx_u,
                **kwargs: object,
            ) -> dict[str, object] | None:
                if (
                    payload_type == "config"
                    and isinstance(_ctx._effective_config, dict)
                    and not _ctx._effective_config_uncertain
                ):
                    res = await _orig(payload_type="config", **kwargs)  # type: ignore[operator]
                    if isinstance(res, dict):
                        return res
                return None

            async def _timeout_then_second_rpc(
                *,
                request_type: str,
                request_data: dict[str, object],
                _outcome: str = second_outcome,
                _started: asyncio.Event = second_started_u,
                _release: asyncio.Event = release_second_u,
                **_kwargs: object,
            ) -> dict[str, object]:
                if request_type == "PLUGIN_CONFIG_GET":
                    return {"plugin_id": "demo", "config": {"base": 1}}
                assert request_type == "PLUGIN_CONFIG_UPDATE"
                updates = request_data["updates"]
                assert isinstance(updates, dict)
                if "timer" in updates:
                    raise TimeoutError("first write timed out")
                _started.set()
                await _release.wait()
                if _outcome == "fail":
                    raise RuntimeError("second write failed")
                return {"success": True, "persisted": False, "config": {}}

            ctx_u._get_local_config_payload = _uncertain_local_payload  # type: ignore[method-assign]
            ctx_u._send_request_and_wait_async = _timeout_then_second_rpc  # type: ignore[method-assign]
            u_tasks: list[asyncio.Task[dict[str, object]]] = []

            async def _timeout_then_second_then_reject(
                *,
                _ctx: PluginContext = ctx_u,
                _started: asyncio.Event = second_started_u,
                _tasks: list[asyncio.Task[dict[str, object]]] = u_tasks,
                _outcome: str = second_outcome,
                **_kwargs: object,
            ) -> None:
                res1 = await _ctx.update_own_config({"timer": 3})
                assert res1["persisted"] is None
                assert _ctx._effective_config_uncertain is True
                write2 = asyncio.create_task(_ctx.update_own_config({"timer2": 4}))
                _tasks.append(write2)
                await _wait_for_checkpoint_or_task_failure(_started, write2)
                raise RuntimeError(f"reject after timeout + {_outcome}")

            sender_u = _FakeResponseSender()
            try:
                await host_module._handle_config_update_command(
                    msg={
                        "req_id": f"req-mem-rb-uncertain-{second_outcome}",
                        "config": {"host": 9},
                        "mode": "temporary",
                        "replace": False,
                    },
                    ctx=ctx_u,
                    events_by_type={
                        "lifecycle": {"config_change": _timeout_then_second_then_reject}
                    },
                    plugin_id="demo",
                    res_sender=sender_u,
                    logger=_FakeLogger(),
                )
                assert sender_u.payloads[-1]["success"] is False
                assert f"reject after timeout + {second_outcome}" in str(
                    sender_u.payloads[-1].get("error", "")
                )
                release_second_u.set()
                if second_outcome == "fail":
                    with pytest.raises(RuntimeError, match="second write failed"):
                        await u_tasks[0]
                    expected_after_u = {"base": 1, "timer": 3}
                else:
                    res2 = await u_tasks[0]
                    assert res2["persisted"] is False
                    expected_after_u = {"base": 1, "timer": 3, "timer2": 4}
                    assert res2["config"] == expected_after_u
                assert ctx_u._effective_config == expected_after_u
                assert instance_u.refreshed[-1] == expected_after_u
                assert ctx_u._effective_config_uncertain is True

                dumped_u = await ctx_u.get_own_config()
                assert dumped_u["config"] == {"base": 1}
                assert ctx_u._effective_config == {"base": 1}
                assert ctx_u._effective_config_uncertain is False
                assert instance_u.refreshed[-1] == {"base": 1}
                assert ctx_u._host_config_change_pending == 0
                assert not getattr(ctx_u, "_pending_host_config_owner_tokens", {})
                assert not getattr(ctx_u, "_owner_token_host_change_root", {})
                assert getattr(ctx_u, "_non_persisted_host_lineage", None) is None
            finally:
                release_second_u.set()
                for pending in u_tasks:
                    if not pending.done():
                        pending.cancel()
                await asyncio.gather(*u_tasks, return_exceptions=True)

        # 19e. Control: persisted=False followed by persisted=True detaches lineage and preserves commit.
        ctx_commit = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_mem_rb_persisted_detach" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance_commit = _RecordingInstance()
        ctx_commit._instance = instance_commit
        ctx_commit._set_effective_config_cache({"base": 1})

        async def _mem_then_persisted_rpc(
            *,
            request_data: dict[str, object],
            **_kwargs: object,
        ) -> dict[str, object]:
            updates = request_data["updates"]
            assert isinstance(updates, dict)
            if "timer" in updates:
                return {"success": True, "persisted": False, "config": {}}
            return {"success": True, "persisted": True, "config": {"base": 1, "saved": 9}}

        ctx_commit._get_local_config_payload = _base_local_payload  # type: ignore[method-assign]
        ctx_commit._send_request_and_wait_async = _mem_then_persisted_rpc  # type: ignore[method-assign]

        async def _mem_then_commit_then_reject(**_kwargs: object) -> None:
            res1 = await ctx_commit.update_own_config({"timer": 3})
            assert res1["persisted"] is False
            res2 = await ctx_commit.update_own_config({"saved": 9})
            assert res2["persisted"] is True
            raise RuntimeError("reject after persisted commit")

        sender_commit = _FakeResponseSender()
        await host_module._handle_config_update_command(
            msg={
                "req_id": "req-mem-rb-commit",
                "config": {"host": 9},
                "mode": "temporary",
                "replace": False,
            },
            ctx=ctx_commit,
            events_by_type={"lifecycle": {"config_change": _mem_then_commit_then_reject}},
            plugin_id="demo",
            res_sender=sender_commit,
            logger=_FakeLogger(),
        )
        assert sender_commit.payloads[-1]["success"] is False
        assert "reject after persisted commit" in str(
            sender_commit.payloads[-1].get("error", "")
        )
        assert ctx_commit._effective_config == {"base": 1, "saved": 9}
        assert ctx_commit._effective_config_uncertain is False
        assert instance_commit.refreshed[-1] == {"base": 1, "saved": 9}
        assert getattr(ctx_commit, "_non_persisted_host_lineage", None) is None

        # 19f. Caller mutating updates/config dict while queued behind _config_write_lock must not
        #      cause RPC payload to diverge from optimistic and rebased snapshots.
        ctx_snap = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / "demo_snapshot_isolation" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance_snap = _RecordingInstance()
        ctx_snap._instance = instance_snap
        ctx_snap._set_effective_config_cache({"base": 1})

        first_started = asyncio.Event()
        release_first = asyncio.Event()
        seen_rpcs: list[tuple[str, dict[str, object]]] = []

        async def _snap_rpc(
            *,
            request_type: str,
            request_data: dict[str, object],
            **_kwargs: object,
        ) -> dict[str, object]:
            if request_type == "PLUGIN_CONFIG_UPDATE":
                updates_arg = request_data["updates"]
                assert isinstance(updates_arg, dict)
                seen_rpcs.append((request_type, dict(updates_arg)))
                if "first" in updates_arg:
                    first_started.set()
                    await release_first.wait()
                    return {"success": True, "persisted": True, "config": {"base": 1, "first": 1}}
                return {"success": True, "persisted": False, "config": {}}
            if request_type == "PLUGIN_CONFIG_REPLACE":
                cfg_arg = request_data["config"]
                assert isinstance(cfg_arg, dict)
                seen_rpcs.append((request_type, dict(cfg_arg)))
                return {"success": True, "persisted": True, "config": dict(cfg_arg)}
            raise AssertionError(f"Unexpected RPC: {request_type}")

        ctx_snap._get_local_config_payload = _base_local_payload  # type: ignore[method-assign]
        ctx_snap._send_request_and_wait_async = _snap_rpc  # type: ignore[method-assign]

        first_task = asyncio.create_task(ctx_snap.update_own_config({"first": 1}))
        queued_update: asyncio.Task[dict[str, object]] | None = None
        queued_replace: asyncio.Task[dict[str, object]] | None = None
        try:
            await _wait_for_checkpoint_or_task_failure(first_started, first_task)
            mutable_updates: dict[str, object] = {"timer": 3}
            queued_update = asyncio.create_task(ctx_snap.update_own_config(mutable_updates))
            mutable_cfg: dict[str, object] = {"base": 1, "mode": "orig"}
            queued_replace = asyncio.create_task(ctx_snap.replace_own_config(mutable_cfg))
            await asyncio.sleep(0)
            mutable_updates["timer"] = 99
            mutable_cfg["mode"] = "mutated"

            release_first.set()
            await first_task
            res_upd = await queued_update
            assert res_upd["persisted"] is False
            assert res_upd["config"] == {"base": 1, "first": 1, "timer": 3}
            res_rep = await queued_replace
            assert res_rep["config"] == {"base": 1, "mode": "orig"}
            assert seen_rpcs == [
                ("PLUGIN_CONFIG_UPDATE", {"first": 1}),
                ("PLUGIN_CONFIG_UPDATE", {"timer": 3}),
                ("PLUGIN_CONFIG_REPLACE", {"base": 1, "mode": "orig"}),
            ]
            assert ctx_snap._effective_config == {"base": 1, "mode": "orig"}
            assert instance_snap.refreshed[-1] == {"base": 1, "mode": "orig"}
        finally:
            release_first.set()
            for pending in (first_task, queued_update, queued_replace):
                if pending is not None and not pending.done():
                    pending.cancel()
            await asyncio.gather(
                *(t for t in (first_task, queued_update, queued_replace) if t is not None),
                return_exceptions=True,
            )

    asyncio.run(_case_non_persisted_writes_rebase_on_host_rollback())


@pytest.mark.plugin_unit
@pytest.mark.parametrize("write_kind", ["update", "replace"])
@pytest.mark.parametrize("failure_order", ["rpc_first", "handler_first"])
def test_replace_config_update_overtaking_in_flight_write_retains_accepted_baseline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    write_kind: str,
    failure_order: str,
) -> None:
    """Host replace=True overtaking an in-flight write must roll back to the pre-write baseline."""
    import asyncio
    import copy
    from plugin.core.context import PluginContext
    from plugin.server.application import config as config_application

    local_query_calls: list[str] = []

    class _UnexpectedQueryService:
        async def get_plugin_config(self, *, plugin_id: str) -> dict[str, object]:
            local_query_calls.append(plugin_id)
            raise AssertionError(f"Unexpected local disk query for {plugin_id}")

    monkeypatch.setattr(config_application, "ConfigQueryService", _UnexpectedQueryService)

    class _RecordingInstance:
        def __init__(self) -> None:
            self.refreshed: list[dict[str, object]] = []

        def refresh_runtime_config(self, effective_config: dict[str, object]) -> None:
            self.refreshed.append(copy.deepcopy(effective_config))

    async def _run() -> None:
        ctx = PluginContext(
            plugin_id="demo",
            config_path=tmp_path / f"demo_rb_{write_kind}_{failure_order}" / "plugin.toml",
            logger=_FakeLogger(),  # type: ignore[arg-type]
            status_queue=None,
        )
        instance = _RecordingInstance()
        ctx._instance = instance
        ctx._set_effective_config_cache({"k": "accepted"})

        rpc_started = asyncio.Event()
        release_rpc = asyncio.Event()
        read_rpcs: list[str] = []

        async def _fake_rpc(
            *,
            method_name: str = "",
            request_type: str = "",
            **_kwargs: object,
        ) -> dict[str, object]:
            if request_type == "PLUGIN_CONFIG_GET" or method_name == "get_own_config":
                read_rpcs.append(request_type or method_name)
                return {"plugin_id": "demo", "config": {"k": "accepted"}}
            rpc_started.set()
            await release_rpc.wait()
            raise RuntimeError("write rpc rejected")

        ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

        if write_kind == "update":
            write_coro = ctx.update_own_config({"k": "draft"})
        else:
            write_coro = ctx.replace_own_config({"k": "draft"})
        write_task = asyncio.create_task(write_coro)

        handler_entered = asyncio.Event()
        allow_handler_fail = asyncio.Event()

        async def _config_change(**_kwargs: object) -> None:
            handler_entered.set()
            await allow_handler_fail.wait()
            raise RuntimeError("host replace rejected")

        sender = _FakeResponseSender()
        cmd_task: asyncio.Task[None] | None = None
        try:
            await _wait_for_checkpoint_or_task_failure(rpc_started, write_task)
            assert ctx._effective_config == {"k": "draft"}

            cmd_task = asyncio.create_task(
                host_module._handle_config_update_command(
                    msg={
                        "req_id": f"req-overtake-{write_kind}-{failure_order}",
                        "config": {"k": "host"},
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
            await _wait_for_checkpoint_or_task_failure(handler_entered, cmd_task)
            assert ctx._effective_config == {"k": "host"}

            if failure_order == "rpc_first":
                release_rpc.set()
                with pytest.raises(RuntimeError, match="write rpc rejected"):
                    await write_task
                allow_handler_fail.set()
                await cmd_task
            else:
                allow_handler_fail.set()
                await cmd_task
                release_rpc.set()
                with pytest.raises(RuntimeError, match="write rpc rejected"):
                    await write_task

            assert sender.payloads[-1]["success"] is False
            assert ctx._effective_config == {"k": "accepted"}
            assert ctx._effective_config_uncertain is False
            assert instance.refreshed[-1] == {"k": "accepted"}
            assert getattr(ctx, "_in_flight_write_baseline", None) is None

            loaded = await ctx.get_own_config()
            assert loaded["config"] == {"k": "accepted"}
            assert read_rpcs == []
            assert local_query_calls == []
        finally:
            release_rpc.set()
            allow_handler_fail.set()
            pending = [t for t in (write_task, cmd_task) if t is not None and not t.done()]
            for t in pending:
                t.cancel()
            await asyncio.gather(
                *(t for t in (write_task, cmd_task) if t is not None),
                return_exceptions=True,
            )

    asyncio.run(_run())


@pytest.mark.plugin_unit
def test_replace_config_update_overtaking_in_flight_write_preserves_uncertainty_and_commit_invalidation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify uncertain pre-write baseline preservation and committed-write cache invalidation."""
    import asyncio
    import copy
    from plugin.core.context import PluginContext
    from plugin.server.application import config as config_application

    local_query_calls: list[str] = []

    class _UnavailableQueryService:
        async def get_plugin_config(self, *, plugin_id: str) -> dict[str, object]:
            local_query_calls.append(plugin_id)
            raise RuntimeError(f"local config query unavailable for {plugin_id}")

    monkeypatch.setattr(config_application, "ConfigQueryService", _UnavailableQueryService)

    class _RecordingInstance:
        def __init__(self) -> None:
            self.refreshed: list[dict[str, object]] = []

        def refresh_runtime_config(self, effective_config: dict[str, object]) -> None:
            self.refreshed.append(copy.deepcopy(effective_config))

    # Subcase A: initial uncertain baseline must remain uncertain after dual failure.
    async def _case_uncertain_baseline() -> None:
        for write_kind in ("update", "replace"):
            for failure_order in ("rpc_first", "handler_first"):
                ctx = PluginContext(
                    plugin_id="demo",
                    config_path=(
                        tmp_path / f"demo_uncertain_{write_kind}_{failure_order}" / "plugin.toml"
                    ),
                    logger=_FakeLogger(),  # type: ignore[arg-type]
                    status_queue=None,
                )
                instance = _RecordingInstance()
                ctx._instance = instance
                ctx._set_effective_config_cache({"k": "accepted"})
                ctx._effective_config_uncertain = True

                rpc_started = asyncio.Event()
                release_rpc = asyncio.Event()
                read_rpcs: list[str] = []

                async def _fake_rpc(
                    *,
                    method_name: str = "",
                    request_type: str = "",
                    _rpc_started: asyncio.Event = rpc_started,
                    _release_rpc: asyncio.Event = release_rpc,
                    _read_rpcs: list[str] = read_rpcs,
                    **_kwargs: object,
                ) -> dict[str, object]:
                    if request_type == "PLUGIN_CONFIG_GET" or method_name == "get_own_config":
                        _read_rpcs.append(request_type or method_name)
                        return {"plugin_id": "demo", "config": {"k": "disk"}}
                    _rpc_started.set()
                    await _release_rpc.wait()
                    raise RuntimeError("write rpc rejected")

                ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]

                write_coro = (
                    ctx.update_own_config({"k": "draft"})
                    if write_kind == "update"
                    else ctx.replace_own_config({"k": "draft"})
                )
                write_task = asyncio.create_task(write_coro)

                handler_entered = asyncio.Event()
                allow_handler_fail = asyncio.Event()

                async def _config_change(
                    _entered: asyncio.Event = handler_entered,
                    _allow: asyncio.Event = allow_handler_fail,
                    **_kwargs: object,
                ) -> None:
                    _entered.set()
                    await _allow.wait()
                    raise RuntimeError("host replace rejected")

                sender = _FakeResponseSender()
                cmd_task: asyncio.Task[None] | None = None
                try:
                    await _wait_for_checkpoint_or_task_failure(rpc_started, write_task)
                    cmd_task = asyncio.create_task(
                        host_module._handle_config_update_command(
                            msg={
                                "req_id": f"req-uncertain-{write_kind}-{failure_order}",
                                "config": {"k": "host"},
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
                    await _wait_for_checkpoint_or_task_failure(handler_entered, cmd_task)

                    if failure_order == "rpc_first":
                        release_rpc.set()
                        with pytest.raises(RuntimeError, match="write rpc rejected"):
                            await write_task
                        allow_handler_fail.set()
                        await cmd_task
                    else:
                        allow_handler_fail.set()
                        await cmd_task
                        release_rpc.set()
                        with pytest.raises(RuntimeError, match="write rpc rejected"):
                            await write_task

                    assert sender.payloads[-1]["success"] is False
                    assert ctx._effective_config == {"k": "accepted"}
                    assert ctx._effective_config_uncertain is True
                    assert getattr(ctx, "_in_flight_write_baseline", None) is None

                    local_query_calls.clear()
                    loaded = await ctx.get_own_config()
                    assert local_query_calls == ["demo"]
                    assert loaded["config"] == {"k": "disk"}
                    assert len(read_rpcs) == 1
                finally:
                    release_rpc.set()
                    allow_handler_fail.set()
                    for t in (write_task, cmd_task):
                        if t is not None and not t.done():
                            t.cancel()
                    await asyncio.gather(
                        *(t for t in (write_task, cmd_task) if t is not None),
                        return_exceptions=True,
                    )

    # Subcase B: committed write overtaken by failed host replace invalidates cache for disk read.
    async def _case_committed_write_invalidation() -> None:
        for order in ("commit_first", "handler_first"):
            ctx = PluginContext(
                plugin_id="demo",
                config_path=tmp_path / f"demo_commit_inv_{order}" / "plugin.toml",
                logger=_FakeLogger(),  # type: ignore[arg-type]
                status_queue=None,
            )
            instance = _RecordingInstance()
            ctx._instance = instance
            ctx._set_effective_config_cache({"k": "accepted"})

            rpc_started = asyncio.Event()
            release_rpc = asyncio.Event()
            read_rpcs: list[str] = []

            async def _fake_rpc(
                *,
                method_name: str = "",
                request_type: str = "",
                _rpc_started: asyncio.Event = rpc_started,
                _release_rpc: asyncio.Event = release_rpc,
                _read_rpcs: list[str] = read_rpcs,
                **_kwargs: object,
            ) -> dict[str, object]:
                if request_type == "PLUGIN_CONFIG_GET" or method_name == "get_own_config":
                    _read_rpcs.append(request_type or method_name)
                    return {"plugin_id": "demo", "config": {"k": "committed"}}
                _rpc_started.set()
                await _release_rpc.wait()
                return {
                    "success": True,
                    "persisted": True,
                    "plugin_id": "demo",
                    "config": {"k": "committed"},
                }

            ctx._send_request_and_wait_async = _fake_rpc  # type: ignore[method-assign]
            write_task = asyncio.create_task(ctx.update_own_config({"k": "committed"}))

            handler_entered = asyncio.Event()
            allow_handler_fail = asyncio.Event()

            async def _config_change(
                _entered: asyncio.Event = handler_entered,
                _allow: asyncio.Event = allow_handler_fail,
                **_kwargs: object,
            ) -> None:
                _entered.set()
                await _allow.wait()
                raise RuntimeError("host replace rejected")

            sender = _FakeResponseSender()
            cmd_task: asyncio.Task[None] | None = None
            try:
                await _wait_for_checkpoint_or_task_failure(rpc_started, write_task)
                cmd_task = asyncio.create_task(
                    host_module._handle_config_update_command(
                        msg={
                            "req_id": f"req-commit-inv-{order}",
                            "config": {"k": "host"},
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
                await _wait_for_checkpoint_or_task_failure(handler_entered, cmd_task)

                if order == "commit_first":
                    release_rpc.set()
                    res = await write_task
                    allow_handler_fail.set()
                    await cmd_task
                else:
                    allow_handler_fail.set()
                    await cmd_task
                    release_rpc.set()
                    res = await write_task

                assert res["persisted"] is True
                assert res["config"] == {"k": "committed"}
                assert sender.payloads[-1]["success"] is False
                assert ctx._effective_config_uncertain is True
                assert getattr(ctx, "_in_flight_write_baseline", None) is None

                local_query_calls.clear()
                loaded = await ctx.get_own_config()
                assert local_query_calls == ["demo"]
                assert loaded["config"] == {"k": "committed"}
                assert len(read_rpcs) == 1
            finally:
                release_rpc.set()
                allow_handler_fail.set()
                for t in (write_task, cmd_task):
                    if t is not None and not t.done():
                        t.cancel()
                await asyncio.gather(
                    *(t for t in (write_task, cmd_task) if t is not None),
                    return_exceptions=True,
                )

    asyncio.run(_case_uncertain_baseline())
    asyncio.run(_case_committed_write_invalidation())
