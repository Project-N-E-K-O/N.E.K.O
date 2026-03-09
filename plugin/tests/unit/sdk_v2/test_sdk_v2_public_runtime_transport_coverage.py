from __future__ import annotations

import platform

import pytest

from plugin.sdk_v2.public.runtime import memory as public_memory
from plugin.sdk_v2.public.runtime import system_info as public_system_info
from plugin.sdk_v2.public.transport import message_plane as public_message_plane
from plugin.sdk_v2.shared.models import Err, Ok


class _CtxQueryAsync:
    async def query_memory_async(self, bucket_id: str, query: str, timeout: float = 5.0):
        return {"bucket": bucket_id, "query": query, "timeout": timeout}


class _AwaitableResult:
    def __init__(self, value):
        self._value = value

    def __await__(self):
        async def _inner():
            return self._value
        return _inner().__await__()


class _CtxQueryAwaitable:
    def query_memory(self, bucket_id: str, query: str, timeout: float = 5.0):
        return _AwaitableResult([bucket_id, query, timeout])


class _CtxQueryRaises:
    async def query_memory_async(self, bucket_id: str, query: str, timeout: float = 5.0):
        raise RuntimeError("boom")


class _Dumpable:
    @staticmethod
    def dump_records():
        return [{"id": 1}, "skip"]


class _MemoryBusAsync:
    @staticmethod
    async def get_async(bucket_id: str, limit: int = 20, timeout: float = 5.0):
        return _Dumpable()


class _MemoryBusAwaitable:
    @staticmethod
    def get(bucket_id: str, limit: int = 20, timeout: float = 5.0):
        return _AwaitableResult([{"bucket": bucket_id}, "skip"])


class _CtxBusAsync:
    class bus:
        memory = _MemoryBusAsync()


class _CtxBusAwaitable:
    class bus:
        memory = _MemoryBusAwaitable()


class _CtxBusRaises:
    class bus:
        class memory:
            @staticmethod
            async def get_async(bucket_id: str, limit: int = 20, timeout: float = 5.0):
                raise RuntimeError("boom")


class _CtxSystem:
    async def get_system_config(self, timeout: float = 5.0):
        return {"config": {"plugin_dir": "/tmp/demo"}}


class _CtxSystemNonDict:
    async def get_system_config(self, timeout: float = 5.0):
        return "x"


class _CtxSystemRaises:
    async def get_system_config(self, timeout: float = 5.0):
        raise RuntimeError("boom")


class _CtxSystemData:
    async def get_system_config(self, timeout: float = 5.0):
        return {"data": {"config": {"plugin_dir": "/tmp/demo-data"}}}


class _CtxPlane:
    def __init__(self) -> None:
        self.pushed: list[tuple[str, str, dict, float]] = []

    async def push_message_async(self, *, text: str, description: str, metadata: dict, timeout: float = 5.0):
        self.pushed.append((text, description, metadata, timeout))
        return {"ok": True}

    async def message_plane_request_async(self, *, topic: str, payload: dict, timeout: float = 10.0):
        return {"topic": topic, "payload": payload, "timeout": timeout}


class _CtxPlaneObject:
    async def message_plane_request_async(self, *, topic: str, payload: dict, timeout: float = 10.0):
        return object()


class _CtxPlaneRaises:
    async def message_plane_request_async(self, *, topic: str, payload: dict, timeout: float = 10.0):
        raise RuntimeError("boom")

    async def push_message_async(self, *, text: str, description: str, metadata: dict, timeout: float = 5.0):
        raise RuntimeError("boom")


@pytest.mark.asyncio
async def test_public_memory_client_branches() -> None:
    assert public_memory.MemoryClient._coerce_query_result(object()) == {"result": str(object())} or isinstance(public_memory.MemoryClient._coerce_query_result(object()), dict)
    assert public_memory.MemoryClient._coerce_records(_Dumpable()) == [{"id": 1}]
    assert public_memory.MemoryClient._coerce_records([{"id": 2}, "skip"]) == [{"id": 2}]
    assert public_memory.MemoryClient._coerce_records(object()) == []

    mem = public_memory.MemoryClient(_CtxQueryAsync())
    assert (await mem.query("b", "q")).is_ok()

    mem = public_memory.MemoryClient(_CtxQueryAwaitable())
    assert (await mem.query("b", "q")).unwrap() == ["b", "q", 5.0]

    mem = public_memory.MemoryClient(object())
    assert (await mem.query("b", "q")).is_err()

    mem = public_memory.MemoryClient(_CtxQueryRaises())
    assert (await mem.query("b", "q")).is_err()

    mem = public_memory.MemoryClient(_CtxBusAsync())
    assert (await mem.get("b")).unwrap() == [{"id": 1}]

    mem = public_memory.MemoryClient(_CtxBusAwaitable())
    assert (await mem.get("b")).unwrap() == [{"bucket": "b"}]

    mem = public_memory.MemoryClient(object())
    assert (await mem.get("b")).is_err()

    mem = public_memory.MemoryClient(_CtxBusRaises())
    assert (await mem.get("b")).is_err()


@pytest.mark.asyncio
async def test_public_system_info_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    info = public_system_info.SystemInfo(_CtxSystem())
    assert (await info.get_system_config()).is_ok()
    assert (await info.get_server_settings()).unwrap() == {"plugin_dir": "/tmp/demo"}

    info = public_system_info.SystemInfo(_CtxSystemNonDict())
    assert (await info.get_system_config()).unwrap() == {"result": "x"}
    assert (await info.get_server_settings()).unwrap() == {}

    info = public_system_info.SystemInfo(_CtxSystemData())
    assert (await info.get_server_settings()).unwrap() == {"plugin_dir": "/tmp/demo-data"}

    info = public_system_info.SystemInfo(object())
    assert (await info.get_system_config()).is_err()
    assert (await info.get_server_settings()).is_err()

    info = public_system_info.SystemInfo(_CtxSystemRaises())
    assert (await info.get_system_config()).is_err()
    assert (await info.get_server_settings()).is_err()

    original = public_system_info.SystemInfo.get_system_config
    async def _boom(self, *, timeout: float = 5.0):
        raise RuntimeError("boom")
    public_system_info.SystemInfo.get_system_config = _boom  # type: ignore[assignment]
    try:
        assert (await public_system_info.SystemInfo(object()).get_server_settings()).is_err()
    finally:
        public_system_info.SystemInfo.get_system_config = original  # type: ignore[assignment]

    original_uname = platform.uname
    original_arch = platform.architecture
    original_platform = platform.platform
    monkeypatch.setattr(platform, "uname", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(platform, "architecture", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(platform, "platform", lambda: "test-platform")
    env = await public_system_info.SystemInfo(object()).get_python_env()
    assert env.is_ok()
    assert env.unwrap()["os"]["system"] is None
    monkeypatch.setattr(platform, "uname", original_uname)
    monkeypatch.setattr(platform, "architecture", original_arch)
    monkeypatch.setattr(platform, "platform", original_platform)


@pytest.mark.asyncio
async def test_public_message_plane_branches() -> None:
    plane = public_message_plane.MessagePlaneTransport(ctx=_CtxPlane())
    assert (await plane.request("t", {"x": 1})).is_ok()
    assert (await plane.notify("t", {"x": 1})).is_ok()

    async def ok_handler(payload: dict):
        return Ok(None)

    async def err_handler(payload: dict):
        return Err(RuntimeError("bad"))

    assert (await plane.subscribe("t", ok_handler)).is_ok()
    assert (await plane.subscribe("t", err_handler)).is_ok()
    assert (await plane.publish("t", {"x": 1})).is_err()
    assert (await plane.unsubscribe("t", ok_handler)).unwrap() == 1
    assert (await plane.unsubscribe("t")).unwrap() == 1

    plane = public_message_plane.MessagePlaneTransport(ctx=_CtxPlaneObject())
    request = await plane.request("t", {"x": 1})
    assert request.is_ok()
    assert isinstance(request.unwrap(), dict)

    plane = public_message_plane.MessagePlaneTransport(ctx=object())
    assert (await plane.request("t", {"x": 1})).is_err()

    plane = public_message_plane.MessagePlaneTransport(ctx=_CtxPlaneRaises())
    assert (await plane.request("t", {"x": 1})).is_err()
    assert (await plane.publish("t", {"x": 1})).is_err()


@pytest.mark.asyncio
async def test_public_system_info_total_exception_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(platform, "python_implementation", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    env = await public_system_info.SystemInfo(object()).get_python_env()
    assert env.is_err()


@pytest.mark.asyncio
async def test_public_message_plane_remaining_branches() -> None:
    plane = public_message_plane.MessagePlaneTransport()

    async def only_handler(payload: dict):
        return Ok(None)

    await plane.subscribe("solo", only_handler)
    assert (await plane.unsubscribe("solo", only_handler)).unwrap() == 1

    class _BadHandlers(dict):
        def __getitem__(self, key):
            raise RuntimeError("boom")

        def get(self, key, default=None):
            raise RuntimeError("boom")

    plane = public_message_plane.MessagePlaneTransport()
    plane._handlers = _BadHandlers()
    assert (await plane.subscribe("t", only_handler)).is_err()
    assert (await plane.unsubscribe("t", only_handler)).is_err()
