from __future__ import annotations


import pytest

from plugin.sdk import message_plane_transport as module


@pytest.mark.plugin_unit
def test_message_plane_rpc_client_request_dispatch_sync(monkeypatch: pytest.MonkeyPatch) -> None:
    client = module.MessagePlaneRpcClient.__new__(module.MessagePlaneRpcClient)
    monkeypatch.setattr(client, "_is_in_event_loop", lambda: False)
    monkeypatch.setattr(client, "request_sync", lambda **kwargs: {"ok": True, "mode": "sync"})

    out = client.request(op="x", args={}, timeout=1.0)
    assert out["mode"] == "sync"


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_message_plane_rpc_client_request_dispatch_async(monkeypatch: pytest.MonkeyPatch) -> None:
    client = module.MessagePlaneRpcClient.__new__(module.MessagePlaneRpcClient)
    monkeypatch.setattr(client, "_is_in_event_loop", lambda: True)

    async def _fake_async(**kwargs):
        return {"ok": True, "mode": "async"}

    monkeypatch.setattr(client, "request_async", _fake_async)

    coro = client.request(op="x", args={}, timeout=1.0)
    assert hasattr(coro, "__await__")
    out = await coro
    assert out["mode"] == "async"


import asyncio
import builtins
import importlib
import json
import sys
from contextlib import suppress
from types import ModuleType, SimpleNamespace

import ormsgpack
import pytest

from plugin.sdk import message_plane_transport as mpt


class _FakeFrame:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def __bytes__(self) -> bytes:
        return self._data


class _FakeSyncSock:
    def __init__(self, responses: list[object] | None = None, poll_events: list[int] | None = None) -> None:
        self.responses = list(responses or [])
        self.poll_events = list(poll_events or [1] * 10)
        self.sent: list[bytes] = []
        self.closed = False

    def setsockopt(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        return None

    def connect(self, endpoint: str) -> None:
        self.endpoint = endpoint

    def send(self, raw: bytes, **kwargs) -> None:
        self.sent.append(raw)

    def poll(self, timeout: int, flags: int) -> int:
        return self.poll_events.pop(0) if self.poll_events else 0


    def recv(self, **kwargs):  # noqa: ANN003
        if not self.responses:
            raise RuntimeError("no response")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeFrame(item if isinstance(item, bytes) else bytes(item))


class _FakeAsyncSock(_FakeSyncSock):
    async def send(self, raw: bytes, **kwargs) -> None:  # type: ignore[override]
        self.sent.append(raw)

    async def poll(self, timeout: int, flags: int) -> int:  # type: ignore[override]
        return self.poll_events.pop(0) if self.poll_events else 0

    async def recv(self, **kwargs):  # type: ignore[override]  # noqa: ANN003
        if not self.responses:
            raise RuntimeError("no response")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeFrame(item if isinstance(item, bytes) else bytes(item))


class _RaisingSock(_FakeSyncSock):
    def setsockopt(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        raise RuntimeError("setopt")


class _SideEffectLock:
    def __init__(self, fn) -> None:  # noqa: ANN001
        self._fn = fn

    def __enter__(self):
        self._fn()
        return self

    def __exit__(self, exc_type, exc, tb):  # noqa: ANN001
        return False


def _fake_zmq(sock: _FakeSyncSock | _FakeAsyncSock) -> SimpleNamespace:
    class _Context:
        def __init__(self) -> None:
            self.sock = sock

        def socket(self, typ: int) -> _FakeSyncSock | _FakeAsyncSock:
            return self.sock

        @classmethod
        def instance(cls):  # noqa: ANN206
            return cls()

    return SimpleNamespace(
        DEALER=5,
        IDENTITY=1,
        LINGER=2,
        TCP_NODELAY=3,
        RCVBUF=4,
        SNDBUF=5,
        RCVHWM=6,
        SNDHWM=7,
        POLLIN=8,
        SNDMORE=9,
        Context=_Context,
    )


def _fake_zmq_with_async(sock: _FakeSyncSock | _FakeAsyncSock) -> SimpleNamespace:
    z = _fake_zmq(sock)
    z.asyncio = SimpleNamespace(Context=lambda: SimpleNamespace(socket=lambda typ: sock))
    return z


@pytest.mark.plugin_unit
def test_transport_import_fallback_and_init_branch(monkeypatch: pytest.MonkeyPatch) -> None:
    orig_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "threading":
            raise ImportError("blocked")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(mpt, "zmq", _fake_zmq(_FakeSyncSock()))
    monkeypatch.setattr(builtins, "__import__", _fake_import)
    c = mpt.MessagePlaneRpcClient(plugin_id="p", endpoint="ipc://x")
    assert c._tls is None and c._lock is None
    monkeypatch.setattr(builtins, "__import__", orig_import)


@pytest.mark.plugin_unit
def test_transport_sync_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    req_id = "p:1"
    resp = {"req_id": req_id, "v": 1, "ok": True, "result": {"x": 1}}
    sock = _FakeSyncSock(responses=[ormsgpack.packb(resp)])
    monkeypatch.setattr(mpt, "zmq", _fake_zmq(sock))
    c = mpt.MessagePlaneRpcClient(plugin_id="p", endpoint="ipc://x")
    out = c.request_sync(op="x", args={"a": 1}, timeout=1.0)
    assert out and out["ok"] is True

    # zmq None path
    monkeypatch.setattr(mpt, "zmq", None)
    assert c.request_sync(op="x", args={}, timeout=0.01) is None


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_transport_async_and_batch_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    c = mpt.MessagePlaneRpcClient(plugin_id="p", endpoint="ipc://x")
    req_id = "p:1"
    sock = _FakeAsyncSock(
        responses=[ormsgpack.packb({"req_id": req_id, "v": 1, "ok": True, "result": {}})],
        poll_events=[1, 1, 1],
    )
    monkeypatch.setattr(mpt, "zmq", _fake_zmq(sock))
    monkeypatch.setattr(c, "_next_req_id", lambda: req_id)
    monkeypatch.setattr(c, "_get_async_sock", lambda: asyncio.sleep(0, result=sock))

    out = await c.request_async(op="x", args={}, timeout=1.0)
    assert out and out["ok"] is True

    # batch happy path + empty path
    req_ids = iter(["r1", "r2"])
    batch_sock = _FakeAsyncSock(
        responses=[
            ormsgpack.packb({"req_id": "r1", "v": 1, "ok": True}),
            ormsgpack.packb({"req_id": "r2", "v": 1, "ok": True}),
        ],
        poll_events=[1, 1, 1],
    )
    monkeypatch.setattr(c, "_get_async_sock", lambda: asyncio.sleep(0, result=batch_sock))
    monkeypatch.setattr(c, "_next_req_id", lambda: next(req_ids))
    rs = await c.batch_request_async([{"op": "a", "args": {}}, {"op": "b", "args": {}}], timeout=1.0)
    assert len(rs) == 2 and all(isinstance(x, dict) for x in rs)
    assert await c.batch_request_async([], timeout=1.0) == []


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_transport_error_and_dispatch_and_format(monkeypatch: pytest.MonkeyPatch) -> None:
    # request dispatch branches
    c = mpt.MessagePlaneRpcClient.__new__(mpt.MessagePlaneRpcClient)
    monkeypatch.setattr(c, "_is_in_event_loop", lambda: False)
    monkeypatch.setattr(c, "request_sync", lambda **kwargs: {"mode": "sync"})
    assert c.request(op="x", args={}, timeout=1.0)["mode"] == "sync"

    async def _fake_async(**kwargs):
        return {"mode": "async"}

    monkeypatch.setattr(c, "_is_in_event_loop", lambda: True)
    monkeypatch.setattr(c, "request_async", _fake_async)
    assert (await c.request(op="x", args={}, timeout=1.0))["mode"] == "async"

    # request_async import-failure branch
    client = mpt.MessagePlaneRpcClient(plugin_id="p", endpoint="ipc://x")
    orig_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "zmq.asyncio":
            raise ImportError("no")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    assert await client.request_async(op="x", args={}, timeout=0.1) is None
    assert await client.batch_request_async([{"op": "x", "args": {}}], timeout=0.1) == [None]
    monkeypatch.setattr(builtins, "__import__", orig_import)

    # format_rpc_error fallbacks
    class _BadStr:
        def __str__(self) -> str:
            raise RuntimeError("x")

    assert mpt.format_rpc_error(None) == "message_plane error"
    assert mpt.format_rpc_error("x") == "x"
    assert mpt.format_rpc_error({"code": "E", "message": "m"}) == "E: m"
    assert mpt.format_rpc_error({"message": "m"}) == "m"
    assert mpt.format_rpc_error(_BadStr()) == "message_plane error"


@pytest.mark.plugin_unit
def test_transport_low_level_sock_and_reqid_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    # init without zmq
    monkeypatch.setattr(mpt, "zmq", None)
    with pytest.raises(RuntimeError):
        mpt.MessagePlaneRpcClient(plugin_id="p", endpoint="ipc://x")

    sock = _FakeSyncSock()
    monkeypatch.setattr(mpt, "zmq", _fake_zmq(sock))
    c = mpt.MessagePlaneRpcClient(plugin_id="p", endpoint="ipc://x")

    # cached sock fast path
    c._tls.sock = sock
    assert c._get_sock() is sock

    # lock path with existing sock after lock
    c._tls.sock = None
    c._tls.sock = sock
    assert c._get_sock() is sock

    # no lock path
    c._tls = None
    c._lock = None
    assert c._get_sock() is sock

    # req id fallback to uuid path
    class _BadTLS:
        def __getattr__(self, name):  # noqa: ANN001
            raise RuntimeError("x")

        def __setattr__(self, name, value):  # noqa: ANN001, ANN202
            raise RuntimeError("x")

    c._tls = _BadTLS()
    assert isinstance(c._next_req_id(), str)
    assert c._is_in_event_loop() is False


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_transport_async_sock_and_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = _FakeAsyncSock()
    z = _fake_zmq_with_async(sock)
    monkeypatch.setattr(mpt, "zmq", z)
    c = mpt.MessagePlaneRpcClient(plugin_id="p", endpoint="ipc://x")
    c._async_sock_cache = sock
    assert await c._get_async_sock() is sock

    # request_async: sock none / pack fail / send fail / timeout
    monkeypatch.setattr(c, "_get_async_sock", lambda: asyncio.sleep(0, result=None))
    assert await c.request_async(op="x", args={}, timeout=0.1) is None

    monkeypatch.setattr(c, "_get_async_sock", lambda: asyncio.sleep(0, result=sock))
    monkeypatch.setattr(mpt.ormsgpack, "packb", lambda obj: (_ for _ in ()).throw(RuntimeError("pack")))
    assert await c.request_async(op="x", args={}, timeout=0.1) is None

    monkeypatch.setattr(mpt.ormsgpack, "packb", lambda obj: b"x")
    monkeypatch.setattr(sock, "send", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("send")))
    assert await c.request_async(op="x", args={}, timeout=0.1) is None

    async def _send_ok(*a, **k):  # noqa: ANN002, ANN003
        return None

    sock.send = _send_ok  # type: ignore[assignment]
    sock.poll_events = [0, 0]
    assert await c.request_async(op="x", args={}, timeout=0.001) is None


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_transport_sync_and_batch_error_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = _FakeSyncSock()
    monkeypatch.setattr(mpt, "zmq", _fake_zmq(sock))
    c = mpt.MessagePlaneRpcClient(plugin_id="p", endpoint="ipc://x")

    # request_sync: sock none path
    monkeypatch.setattr(c, "_get_sock", lambda: None)
    assert c.request_sync(op="x", args={}, timeout=0.1) is None

    monkeypatch.setattr(c, "_get_sock", lambda: sock)
    monkeypatch.setattr(mpt.ormsgpack, "packb", lambda obj: (_ for _ in ()).throw(RuntimeError("pack")))
    assert c.request_sync(op="x", args={}, timeout=0.1) is None

    monkeypatch.setattr(mpt.ormsgpack, "packb", lambda obj: b"x")
    monkeypatch.setattr(sock, "send", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("send")))
    assert c.request_sync(op="x", args={}, timeout=0.1) is None

    sock.send = lambda *a, **k: None  # type: ignore[assignment]
    sock.poll_events = [0, 0]
    assert c.request_sync(op="x", args={}, timeout=0.001) is None

    # batch_request_async: sock none and recv/decode failures
    async_sock = _FakeAsyncSock()
    monkeypatch.setattr(c, "_get_async_sock", lambda: asyncio.sleep(0, result=None))
    assert await c.batch_request_async([{"op": "x", "args": {}}], timeout=0.1) == [None]

    monkeypatch.setattr(c, "_get_async_sock", lambda: asyncio.sleep(0, result=async_sock))
    monkeypatch.setattr(mpt.ormsgpack, "packb", lambda obj: b"x")
    async_sock.poll_events = [1, 1]
    async_sock.responses = [RuntimeError("recv")]
    rs = await c.batch_request_async([{"op": "a", "args": {}}], timeout=0.1)
    assert rs == [None]


@pytest.mark.plugin_unit
def test_transport_get_sock_exception_setopt_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = _RaisingSock()
    monkeypatch.setattr(mpt, "zmq", _fake_zmq(sock))
    c = mpt.MessagePlaneRpcClient(plugin_id="p", endpoint="ipc://x")
    c._tls.sock = None
    out = c._get_sock()
    assert out is sock

    # else-branch with no lock
    c._lock = None
    c._tls = None
    out2 = c._get_sock()
    assert out2 is sock


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_transport_request_loop_error_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    sock = _FakeAsyncSock(poll_events=[1, 1, 1], responses=[b"bad-json", RuntimeError("recv")])
    monkeypatch.setattr(mpt, "zmq", _fake_zmq_with_async(sock))
    c = mpt.MessagePlaneRpcClient(plugin_id="p", endpoint="ipc://x")
    monkeypatch.setattr(c, "_get_async_sock", lambda: asyncio.sleep(0, result=sock))
    monkeypatch.setattr(mpt.ormsgpack, "packb", lambda obj: b"x")

    # unpack fail then recv fail -> None
    assert await c.request_async(op="x", args={}, timeout=0.1) is None

    # sync loop poll exception / recv exception / unpack fail paths
    sync_sock = _FakeSyncSock(poll_events=[1], responses=[RuntimeError("recv")])
    monkeypatch.setattr(c, "_get_sock", lambda: sync_sock)
    assert c.request_sync(op="x", args={}, timeout=0.1) is None

    sync_sock2 = _FakeSyncSock(poll_events=[1], responses=[b"\x80"])
    monkeypatch.setattr(c, "_get_sock", lambda: sync_sock2)
    assert c.request_sync(op="x", args={}, timeout=0.1) is None


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_transport_remaining_branches(monkeypatch: pytest.MonkeyPatch) -> None:
    # _get_sock with zmq None
    monkeypatch.setattr(mpt, "zmq", _fake_zmq(_FakeSyncSock()))
    c = mpt.MessagePlaneRpcClient(plugin_id="p", endpoint="ipc://x")
    c._tls.sock = None
    monkeypatch.setattr(mpt, "zmq", None)
    assert c._get_sock() is None

    # _get_sock line 55 via lock side effect
    sock = _FakeSyncSock()
    monkeypatch.setattr(mpt, "zmq", _fake_zmq(sock))
    c2 = mpt.MessagePlaneRpcClient(plugin_id="p", endpoint="ipc://x")
    c2._tls.sock = None
    c2._lock = _SideEffectLock(lambda: setattr(c2._tls, "sock", sock))
    assert c2._get_sock() is sock

    # _get_sock line 64 (tls None path) and 103-104 assignment failure path
    class _TLSNoSet:
        conn = None

        def __getattr__(self, name):  # noqa: ANN001
            return None

        def __setattr__(self, name, value):  # noqa: ANN001, ANN202
            if name == "sock":
                raise RuntimeError("x")
            object.__setattr__(self, name, value)

    c3 = mpt.MessagePlaneRpcClient(plugin_id="p", endpoint="ipc://x")
    c3._tls = _TLSNoSet()
    c3._lock = _SideEffectLock(lambda: None)
    assert c3._get_sock() is not None

    c4 = mpt.MessagePlaneRpcClient(plugin_id="p", endpoint="ipc://x")
    c4._tls = None
    assert c4._get_sock() is not None
    assert c4._is_in_event_loop() is True

    # _get_async_sock creation path lines 145-188
    async_sock = _RaisingSock()
    z = _fake_zmq_with_async(async_sock)
    monkeypatch.setattr(mpt, "zmq", z)
    c5 = mpt.MessagePlaneRpcClient(plugin_id="p", endpoint="ipc://x")
    c5._async_sock_cache = None
    c5._async_ctx_cache = None
    out_sock = await c5._get_async_sock()
    assert out_sock is not None
    with suppress(Exception):
        close = getattr(out_sock, "close", None)
        if callable(close):
            close(0)
    with suppress(Exception):
        term = getattr(c5._async_ctx_cache, "term", None)
        if callable(term):
            term()

    # request_async poll exception path 236-239 and unpack fail 251-252
    class _PollErrSock(_FakeAsyncSock):
        async def poll(self, timeout: int, flags: int) -> int:  # type: ignore[override]
            raise RuntimeError("poll")

    p_sock = _PollErrSock()
    monkeypatch.setattr(c5, "_get_async_sock", lambda: asyncio.sleep(0, result=p_sock))
    monkeypatch.setattr(mpt.ormsgpack, "packb", lambda obj: b"x")
    assert await c5.request_async(op="x", args={}, timeout=0.1) is None

    u_sock = _FakeAsyncSock(poll_events=[1], responses=[b"not-msgpack"])
    monkeypatch.setattr(c5, "_get_async_sock", lambda: asyncio.sleep(0, result=u_sock))
    assert await c5.request_async(op="x", args={}, timeout=0.01) is None

    # request_sync poll/ unpack exception paths
    s_sock = _FakeSyncSock()
    s_sock.poll = lambda timeout, flags: (_ for _ in ()).throw(RuntimeError("poll"))  # type: ignore[assignment]
    monkeypatch.setattr(c5, "_get_sock", lambda: s_sock)
    assert c5.request_sync(op="x", args={}, timeout=0.1) is None

    s_sock2 = _FakeSyncSock(poll_events=[1], responses=[b"not-msgpack"])
    monkeypatch.setattr(c5, "_get_sock", lambda: s_sock2)
    assert c5.request_sync(op="x", args={}, timeout=0.01) is None

    # batch_request_async remaining error branches
    b_sock = _FakeAsyncSock(poll_events=[1], responses=[b"not-msgpack"])
    monkeypatch.setattr(c5, "_get_async_sock", lambda: asyncio.sleep(0, result=b_sock))
    req_ids = iter(["r1"])
    monkeypatch.setattr(c5, "_next_req_id", lambda: next(req_ids))
    monkeypatch.setattr(mpt.ormsgpack, "packb", lambda obj: b"x")
    assert await c5.batch_request_async([{"op": "a", "args": {}}], timeout=0.01) == [None]


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_transport_remaining_uncovered_edges(monkeypatch: pytest.MonkeyPatch) -> None:
    # _get_async_sock import failure
    c = mpt.MessagePlaneRpcClient(plugin_id="p", endpoint="ipc://x")
    orig_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):  # type: ignore[no-untyped-def]
        if name == "zmq.asyncio":
            raise ImportError("no-asyncio")
        return orig_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    assert await c._get_async_sock() is None
    monkeypatch.setattr(builtins, "__import__", orig_import)

    # _get_async_sock setopt exception lines via fake ctx/sock
    sock = _RaisingSock()
    monkeypatch.setattr(mpt, "zmq", _fake_zmq_with_async(sock))
    c2 = mpt.MessagePlaneRpcClient(plugin_id="p", endpoint="ipc://x")
    c2._async_ctx_cache = SimpleNamespace(socket=lambda typ: sock)
    c2._async_sock_cache = None
    got = await c2._get_async_sock()
    assert got is sock

    # request_async timeout/unpack-fail branches
    class _SlowPollSock(_FakeAsyncSock):
        async def poll(self, timeout: int, flags: int) -> int:  # type: ignore[override]
            await asyncio.sleep(0.02)
            return 1

    slow = _SlowPollSock()
    monkeypatch.setattr(c2, "_get_async_sock", lambda: asyncio.sleep(0, result=slow))
    monkeypatch.setattr(mpt.ormsgpack, "packb", lambda obj: b"x")
    assert await c2.request_async(op="x", args={}, timeout=0.001) is None

    bad_unpack = _FakeAsyncSock(poll_events=[1], responses=[b"not-msgpack"])
    monkeypatch.setattr(c2, "_get_async_sock", lambda: asyncio.sleep(0, result=bad_unpack))
    monkeypatch.setattr(mpt.ormsgpack, "unpackb", lambda b: (_ for _ in ()).throw(ValueError("bad")))
    assert await c2.request_async(op="x", args={}, timeout=0.01) is None

    # request_sync unpack-fail branch
    s = _FakeSyncSock(poll_events=[1], responses=[b"not-msgpack"])
    monkeypatch.setattr(c2, "_get_sock", lambda: s)
    assert c2.request_sync(op="x", args={}, timeout=0.01) is None

    # batch pack/send/poll/decode error branches
    b = _FakeAsyncSock(poll_events=[1], responses=[b"not-msgpack"])
    monkeypatch.setattr(c2, "_get_async_sock", lambda: asyncio.sleep(0, result=b))
    monkeypatch.setattr(c2, "_next_req_id", lambda: "r1")
    monkeypatch.setattr(mpt.ormsgpack, "packb", lambda obj: (_ for _ in ()).throw(RuntimeError("pack")))
    assert await c2.batch_request_async([{"op": "a", "args": {}}], timeout=0.01) == [None]

    async def _bad_send(*a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("send")

    b.send = _bad_send  # type: ignore[assignment]
    monkeypatch.setattr(mpt.ormsgpack, "packb", lambda obj: b"x")
    assert await c2.batch_request_async([{"op": "a", "args": {}}], timeout=0.01) == [None]

    class _PollErrAsync(_FakeAsyncSock):
        async def poll(self, timeout: int, flags: int) -> int:  # type: ignore[override]
            raise RuntimeError("poll")

    p = _PollErrAsync()
    monkeypatch.setattr(c2, "_get_async_sock", lambda: asyncio.sleep(0, result=p))
    assert await c2.batch_request_async([{"op": "a", "args": {}}], timeout=0.01) == [None]

    # batch timeout and decode-fail branches
    b2 = _FakeAsyncSock(poll_events=[1], responses=[b"not-msgpack"])
    monkeypatch.setattr(c2, "_get_async_sock", lambda: asyncio.sleep(0, result=b2))
    monkeypatch.setattr(c2, "_next_req_id", lambda: "r1")
    monkeypatch.setattr(mpt.ormsgpack, "packb", lambda obj: b"x")
    async def _raise_timeout(coro, timeout):  # noqa: ANN001
        with suppress(Exception):
            coro.close()
        raise asyncio.TimeoutError()

    monkeypatch.setattr(asyncio, "wait_for", _raise_timeout)  # type: ignore[assignment]
    assert await c2.batch_request_async([{"op": "a", "args": {}}], timeout=0.01) == [None]
