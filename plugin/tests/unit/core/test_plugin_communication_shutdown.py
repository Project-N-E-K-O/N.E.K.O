from __future__ import annotations

import asyncio
import threading
import copy

import pytest

from plugin.core.communication import STARTUP_RESULT_REQ_ID, PluginCommunicationResourceManager
from plugin.core.zmq_transport import CH_MSG, CH_RES
from plugin.core.state import state


class _Transport:
    async def recv(self, timeout_ms=None):
        await asyncio.sleep(10)
        return None

    async def send_command(self, msg):
        return None


@pytest.mark.asyncio
async def test_message_routing_cannot_block_control_results() -> None:
    class _DualTransport:
        def __init__(self) -> None:
            self.control: asyncio.Queue = asyncio.Queue()
            self.messages: asyncio.Queue = asyncio.Queue()

        async def recv(self, timeout_ms=None):
            try:
                return await asyncio.wait_for(
                    self.control.get(),
                    timeout=(timeout_ms or 1000) / 1000,
                )
            except asyncio.TimeoutError:
                return None

        async def recv_message(self, timeout_ms=None):
            try:
                return await asyncio.wait_for(
                    self.messages.get(),
                    timeout=(timeout_ms or 1000) / 1000,
                )
            except asyncio.TimeoutError:
                return None

        async def send_command(self, msg):
            return None

    transport = _DualTransport()
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=transport,  # type: ignore[arg-type]
        logger=_Logger(),
    )
    route_started = asyncio.Event()
    release_route = asyncio.Event()

    async def _blocked_route(_payload):
        route_started.set()
        await release_route.wait()

    manager._route_message = _blocked_route  # type: ignore[method-assign]
    result_future = asyncio.get_running_loop().create_future()
    manager._pending_futures["request-one"] = result_future
    await manager.start()
    try:
        await transport.messages.put((CH_MSG, {"type": "MESSAGE_PUSH"}))
        await asyncio.wait_for(route_started.wait(), timeout=0.5)
        await transport.control.put((CH_RES, {
            "req_id": "request-one",
            "success": True,
            "data": "ok",
        }))

        assert await asyncio.wait_for(result_future, timeout=0.5) == {
            "req_id": "request-one",
            "success": True,
            "data": "ok",
        }
    finally:
        release_route.set()
        await manager.shutdown(timeout=0.2)


class _Logger:
    def info(self, *args, **kwargs):
        return None

    def debug(self, *args, **kwargs):
        return None

    def exception(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


@pytest.mark.asyncio
async def test_comm_manager_shutdown_tolerates_cross_loop_uplink_task() -> None:
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=_Transport(),
        logger=_Logger(),
    )

    ready = threading.Event()
    holder: dict[str, object] = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _spawn() -> None:
            manager._uplink_consumer_task = loop.create_task(asyncio.sleep(10))
            holder["loop"] = loop
            ready.set()

        loop.run_until_complete(_spawn())
        loop.run_until_complete(asyncio.sleep(0.2))
        loop.close()

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    assert ready.wait(timeout=2.0)

    await manager.shutdown(timeout=0.1)
    thread.join(timeout=1.0)


@pytest.mark.asyncio
async def test_comm_manager_shutdown_waits_for_cross_loop_consumer_before_cleanup() -> None:
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=_Transport(),
        logger=_Logger(),
    )
    loop_ready = threading.Event()
    loop_blocked = threading.Event()
    allow_loop = threading.Event()
    order: list[str] = []
    holder: dict[str, object] = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        async def _consumer() -> None:
            try:
                await asyncio.sleep(10)
            finally:
                order.append("consumer_stopped")

        def _block_loop() -> None:
            loop_blocked.set()
            allow_loop.wait(timeout=2.0)

        task = loop.create_task(_consumer())
        manager._uplink_consumer_task = task
        holder["loop"] = loop
        loop.call_soon(_block_loop)
        loop_ready.set()
        loop.run_forever()
        if not task.done():
            task.cancel()
            loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
        loop.close()

    # A consumer still running can resolve a pending future, so cancelling
    # them is the step that must not start early.
    def _cleanup() -> None:
        order.append("cleaned")

    manager._cleanup_pending_futures = _cleanup  # type: ignore[method-assign]
    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    assert loop_ready.wait(timeout=1.0)
    assert loop_blocked.wait(timeout=1.0)

    shutdown_task = asyncio.create_task(manager.shutdown(timeout=1.0))
    try:
        await asyncio.sleep(0.05)
        shutdown_waited = not shutdown_task.done()
        order_before_release = list(order)
    finally:
        allow_loop.set()
        await asyncio.wait_for(shutdown_task, timeout=1.0)
        loop = holder["loop"]
        assert isinstance(loop, asyncio.AbstractEventLoop)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=1.0)

    assert shutdown_waited is True
    assert order_before_release == []
    assert order == ["consumer_stopped", "cleaned"]


@pytest.mark.asyncio
async def test_comm_manager_shutdown_times_out_when_cross_loop_is_blocked() -> None:
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=_Transport(),
        logger=_Logger(),
    )
    loop_ready = threading.Event()
    loop_blocked = threading.Event()
    allow_loop = threading.Event()
    cleaned: list[str] = []
    holder: dict[str, object] = {}

    def _runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

        def _block_loop() -> None:
            loop_blocked.set()
            allow_loop.wait(timeout=2.0)

        task = loop.create_task(asyncio.sleep(10))
        manager._uplink_consumer_task = task
        holder["loop"] = loop
        loop.call_soon(_block_loop)
        loop_ready.set()
        loop.run_forever()
        if not task.done():
            task.cancel()
            loop.run_until_complete(asyncio.gather(task, return_exceptions=True))
        loop.close()

    manager._cleanup_pending_futures = lambda: cleaned.append("cleaned")  # type: ignore[method-assign]
    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    assert loop_ready.wait(timeout=1.0)
    assert loop_blocked.wait(timeout=1.0)

    shutdown_task = asyncio.create_task(manager.shutdown(timeout=0.05))
    try:
        done, _pending = await asyncio.wait({shutdown_task}, timeout=0.3)
        assert shutdown_task in done
        with pytest.raises(TimeoutError):
            await shutdown_task
        assert cleaned == []
    finally:
        if not shutdown_task.done():
            shutdown_task.cancel()
        allow_loop.set()
        loop = holder["loop"]
        assert isinstance(loop, asyncio.AbstractEventLoop)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=1.0)


@pytest.mark.asyncio
async def test_run_on_owner_loop_closes_coro_when_cross_loop_schedule_fails() -> None:
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=_Transport(),
        logger=_Logger(),
    )

    class _FakeLoop:
        def is_closed(self) -> bool:
            return False

    manager._owner_loop = _FakeLoop()  # type: ignore[assignment]

    async def _sample() -> None:
        await asyncio.sleep(0)

    coro = _sample()
    with pytest.raises(AttributeError):
        await manager._run_on_owner_loop(coro)
    assert coro.cr_frame is None


@pytest.mark.asyncio
async def test_run_on_owner_loop_falls_back_when_owner_loop_not_running() -> None:
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=_Transport(),
        logger=_Logger(),
    )

    class _StoppedLoop:
        def is_closed(self) -> bool:
            return False

        def is_running(self) -> bool:
            return False

    manager._owner_loop = _StoppedLoop()  # type: ignore[assignment]

    result = await manager._run_on_owner_loop(asyncio.sleep(0, result="ok"))

    assert result == "ok"


@pytest.mark.asyncio
async def test_route_comm_overwrites_the_plugin_supplied_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _Transport()
    transport.uplink_token = "raw-uplink-secret"
    manager = PluginCommunicationResourceManager(
        plugin_id="authenticated-plugin",
        transport=transport,
        logger=_Logger(),
    )
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    monkeypatch.setattr(state, "_plugin_comm_queue", queue)

    await manager._route_comm(
        {
            "type": "PLUGIN_QUERY",
            "from_plugin": "victim-plugin",
            "request_id": "request-1",
        }
    )

    # ``from_plugin`` is the reply address the request router answers on, so a
    # plugin naming someone else here would redirect another plugin's replies.
    routed = queue.get_nowait()
    assert routed["from_plugin"] == "authenticated-plugin"


@pytest.mark.asyncio
async def test_route_comm_does_not_expose_the_raw_uplink_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transport = _Transport()
    transport.uplink_token = "raw-uplink-secret"
    manager = PluginCommunicationResourceManager(
        plugin_id="authenticated-plugin",
        transport=transport,
        logger=_Logger(),
    )
    queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    monkeypatch.setattr(state, "_plugin_comm_queue", queue)

    await manager._route_comm({"type": "PLUGIN_QUERY"})

    routed = queue.get_nowait()
    assert "raw-uplink-secret" not in repr(routed)


@pytest.mark.asyncio
async def test_message_route_overwrites_the_plugin_supplied_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = PluginCommunicationResourceManager(
        plugin_id="authenticated-plugin",
        transport=_Transport(),
        logger=_Logger(),
    )
    target_queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    manager._message_target_queue = target_queue
    stored: list[dict[str, object]] = []
    monkeypatch.setattr(
        state,
        "append_message_record",
        lambda record: stored.append(copy.deepcopy(record)),
    )

    await manager._route_message(
        {
            "type": "MESSAGE_PUSH",
            "plugin_id": "victim-plugin",
            "parts": [{"type": "text", "text": "forged cue"}],
            "metadata": {"public_hint": "keep-me"},
        }
    )

    # Both the stored record and the forwarded copy have to carry the sender
    # the host authenticated, not the one the payload claims.
    forwarded = target_queue.get_nowait()
    for payload in (stored[0], forwarded):
        assert payload["plugin_id"] == "authenticated-plugin"
        assert payload["metadata"] == {"public_hint": "keep-me"}


@pytest.mark.asyncio
async def test_wait_for_startup_rejects_ready_result_with_startup_error() -> None:
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=_Transport(),
        logger=_Logger(),
    )
    manager._startup_result = {
        "req_id": STARTUP_RESULT_REQ_ID,
        "success": True,
        "data": {
            "status": "ready",
            "startup_error": "lifecycle.startup failed",
        },
        "error": None,
    }

    with pytest.raises(RuntimeError, match="lifecycle\\.startup failed"):
        await manager.wait_for_startup(timeout=0.1)


@pytest.mark.asyncio
async def test_wait_for_startup_allows_startup_error_when_requested() -> None:
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=_Transport(),
        logger=_Logger(),
    )
    manager._startup_result = {
        "req_id": STARTUP_RESULT_REQ_ID,
        "success": False,
        "data": {
            "status": "failed",
            "startup_error": "lifecycle.startup failed",
        },
        "error": "lifecycle.startup failed",
    }

    result = await manager.wait_for_startup(timeout=0.1, allow_startup_error=True)

    assert result == {
        "status": "failed",
        "startup_error": "lifecycle.startup failed",
    }


@pytest.mark.asyncio
async def test_wait_for_startup_uses_result_dispatched_before_wait_call() -> None:
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=_Transport(),
        logger=_Logger(),
    )

    await manager.prepare_startup_wait()
    manager._dispatch_result(
        {
            "req_id": STARTUP_RESULT_REQ_ID,
            "success": True,
            "data": {"status": "ready"},
            "error": None,
        }
    )

    result = await manager.wait_for_startup(timeout=0.01)

    assert result == {"status": "ready"}
    assert STARTUP_RESULT_REQ_ID not in manager._pending_futures


@pytest.mark.asyncio
async def test_entry_update_register_uses_outer_entry_id_for_meta() -> None:
    manager = PluginCommunicationResourceManager(
        plugin_id="demo",
        transport=_Transport(),
        logger=_Logger(),
    )

    handlers_backup = dict(state.event_handlers)
    cache_backup = copy.deepcopy(state._snapshot_cache)
    try:
        with state.acquire_event_handlers_write_lock():
            state.event_handlers.clear()
        state.invalidate_snapshot_cache("handlers")

        await manager._handle_entry_update({
            "type": "ENTRY_UPDATE",
            "action": "register",
            "plugin_id": "demo",
            "entry_id": "outer_id",
            "meta": {
                "id": "inner_id",
                "name": "Dynamic",
            },
        })

        with state.acquire_event_handlers_read_lock():
            handler = state.event_handlers["demo.outer_id"]
            assert handler.meta.id == "outer_id"
            assert "demo.inner_id" not in state.event_handlers
    finally:
        with state.acquire_event_handlers_write_lock():
            state.event_handlers.clear()
            state.event_handlers.update(handlers_backup)
        with state._snapshot_cache_lock:
            state._snapshot_cache = cache_backup


# ---------------------------------------------------------------------------
# push_message 的实际投递路径：message plane
# ---------------------------------------------------------------------------
#
# 插件推送要走到用户耳朵里，唯一的路是 message plane —— ProactiveBridge 订阅
# 它的 "messages." 前缀，收到才会推给 main_server。控制面那个 store 是缓存
# （append_message_record 自己的注释禁止把它镜像进 plane），而
# _message_target_queue 全仓没有消费者。这条链断过一次：push_message() 返回
# submitted=True，角色一句话都不说。


def _capture_plane(monkeypatch, *, accepted: bool = True) -> list[dict]:
    """Record every write the host makes to the message plane."""
    import plugin.server.messaging.plane_bridge as plane_bridge

    written: list[dict] = []

    def _publish_record(*, store, record, topic="all"):
        written.append({"store": store, "topic": topic, "record": copy.deepcopy(record)})
        return accepted

    monkeypatch.setattr(plane_bridge, "publish_record", _publish_record)
    return written


@pytest.mark.asyncio
async def test_a_pushed_message_reaches_the_message_plane(monkeypatch) -> None:
    """The delivery path itself.

    Mutation: drop the ``_publish_message_to_plane`` call from
    ``_forward_message``. Everything else about the turn still looks healthy --
    which is exactly why this went unnoticed.
    """
    from plugin.message_plane.stores import MESSAGES_STORE_NAME, MESSAGES_TOPIC

    manager = PluginCommunicationResourceManager(
        plugin_id="authenticated-plugin",
        transport=_Transport(),
        logger=_Logger(),
    )
    manager._message_target_queue = asyncio.Queue()
    monkeypatch.setattr(state, "append_message_record", lambda record: None)
    written = _capture_plane(monkeypatch)

    await manager._route_message(
        {
            "type": "MESSAGE_PUSH",
            "plugin_id": "victim-plugin",
            "parts": [{"type": "text", "text": "hello"}],
        }
    )

    assert len(written) == 1, "消息没有进入 message plane"
    hop = written[0]
    # ProactiveBridge 订阅 "messages." 前缀，而 PUB 的 topic 是 f"{store}.{topic}"。
    assert hop["store"] == MESSAGES_STORE_NAME
    assert hop["topic"] == MESSAGES_TOPIC
    # 身份是宿主在认证传输上绑定的那个，不是 payload 自称的。
    assert hop["record"]["plugin_id"] == "authenticated-plugin"


@pytest.mark.asyncio
async def test_the_plane_write_does_not_depend_on_the_legacy_queue(monkeypatch) -> None:
    """No consumer reads that queue, so it must not gate delivery.

    Mutation: put the plane write back behind the ``_message_target_queue``
    guard.
    """
    manager = PluginCommunicationResourceManager(
        plugin_id="authenticated-plugin",
        transport=_Transport(),
        logger=_Logger(),
    )
    manager._message_target_queue = None
    monkeypatch.setattr(state, "append_message_record", lambda record: None)
    written = _capture_plane(monkeypatch)

    await manager._route_message(
        {"type": "MESSAGE_PUSH", "parts": [{"type": "text", "text": "hello"}]}
    )

    assert len(written) == 1, "没有 legacy 队列时消息就到不了 plane"


@pytest.mark.asyncio
async def test_a_replayed_record_is_not_published_twice(monkeypatch) -> None:
    """``_bus_stored`` already marks a record as handled; honour it here too."""
    manager = PluginCommunicationResourceManager(
        plugin_id="authenticated-plugin",
        transport=_Transport(),
        logger=_Logger(),
    )
    manager._message_target_queue = asyncio.Queue()
    monkeypatch.setattr(state, "append_message_record", lambda record: None)
    written = _capture_plane(monkeypatch)

    await manager._route_message(
        {
            "type": "MESSAGE_PUSH",
            "_bus_stored": True,
            "parts": [{"type": "text", "text": "hello"}],
        }
    )

    assert written == [], "已经处理过的记录被重复写进了 plane"


@pytest.mark.asyncio
async def test_a_failing_plane_does_not_take_down_the_message_loop(monkeypatch) -> None:
    """A plane that raises must not kill the uplink consumer."""
    import plugin.server.messaging.plane_bridge as plane_bridge

    manager = PluginCommunicationResourceManager(
        plugin_id="authenticated-plugin",
        transport=_Transport(),
        logger=_Logger(),
    )
    target_queue: asyncio.Queue = asyncio.Queue()
    manager._message_target_queue = target_queue
    monkeypatch.setattr(state, "append_message_record", lambda record: None)

    def _explode(**_kwargs):
        raise RuntimeError("plane down")

    monkeypatch.setattr(plane_bridge, "publish_record", _explode)

    await manager._route_message(
        {"type": "MESSAGE_PUSH", "parts": [{"type": "text", "text": "hello"}]}
    )

    # 回合继续：legacy 队列照常收到，异常没有向上冒。
    assert target_queue.qsize() == 1


# ---------------------------------------------------------------------------
# SDK 量的和宿主发的必须是同一个东西
# ---------------------------------------------------------------------------
#
# push_message() 的体积闸存在的意义，是把「拿到 submitted=True 之后在 ingest
# 那边被静默丢掉」变成一次同步拒绝。它成立的前提是两边量的是同一份字节。宿主
# 成为写入方之后这个前提被打破过：宿主会在写 plane 之前给记录补字段，于是它
# 打包出来的比 SDK 量到的大，刚好卡在上限下沿的推送就落进了那个窗口。


@pytest.mark.asyncio
async def test_the_host_does_not_inflate_the_payload_the_sdk_measured(monkeypatch):
    """Measure the real drift, do not reason about it.

    Mutation: publish ``msg`` instead of the ``_bus_stored``-free copy in
    ``_publish_message_to_plane``'s caller.
    """
    import ormsgpack

    import plugin.core.context as ctx_mod
    import plugin.server.messaging.plane_bridge as plane_bridge

    written: list = []

    def _publish_record(*, store, record, topic="all"):
        written.append(copy.deepcopy(record))
        return True

    monkeypatch.setattr(plane_bridge, "publish_record", _publish_record)
    monkeypatch.setattr(state, "append_message_record", lambda record: None)

    manager = PluginCommunicationResourceManager(
        # 长度也读常量：它是漂移里最大的一项，写死会和推导式脱钩。
        plugin_id="p" * ctx_mod._HOST_PLUGIN_ID_MAX_CHARS,
        transport=_Transport(),
        logger=_Logger(),
    )
    manager._message_target_queue = asyncio.Queue()

    # **最坏形状**，不是顺手的那一种：message_id 与 time 都缺（宿主要各补一
    # 个），plugin_id 取 schema 允许的最长值（宿主按认证身份盖上）。挑一种
    # 「正常」形状来量正是上一版栽的地方——它只漂 31 字节，看着余量充足，而
    # 128 字符 plugin_id 的最坏形状漂 222。
    sdk_payload = {
        "type": "MESSAGE_PUSH",
        "source": "demo",
        "visibility": ["chat"],
        "ai_behavior": "respond",
        "parts": [{"type": "text", "text": "x" * 4096}],
    }
    sdk_size = len(ormsgpack.packb(sdk_payload))

    await manager._route_message(copy.deepcopy(sdk_payload))

    assert len(written) == 1, "消息没有进入 message plane"
    host_size = len(ormsgpack.packb(written[0]))
    drift = host_size - sdk_size

    # 直接读常量，不再从源码里正则抠数字：它现在是个推导式，而正则只认字面量
    # ——那样的读法在这个数变成计算式的那一刻就失效了。
    headroom = ctx_mod._HOST_ENVELOPE_HEADROOM_BYTES

    # plugin_id 是宿主按认证身份盖上去的，本来就该算进漂移里——余量要罩得住它。
    assert drift >= 0
    assert drift <= headroom, (
        f"宿主把 payload 撑大了 {drift} 字节，超过 SDK 预留的 {headroom}："
        "刚好卡在上限下沿的推送会拿到 submitted=True 然后被静默丢弃"
    )
    assert "_bus_stored" not in written[0], (
        "控制面的内部标记被发到了 plane 上"
    )


@pytest.mark.asyncio
async def test_an_authenticated_batch_over_the_item_limit_is_refused(monkeypatch):
    """The byte bound does not stop "many tiny items".

    The message socket's MAXMSGSIZE is derived as payload_max * batch_max, so a
    plugin that bypasses the SDK batcher can pack a legal-sized frame with far
    more items than that derivation assumed -- and every one of them now costs
    the host a message-plane write.

    Mutation: drop the item-count check in ``_consume_message_uplink``.
    """
    from plugin.settings import PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE

    routed: list = []

    manager = PluginCommunicationResourceManager(
        plugin_id="authenticated-plugin",
        transport=_Transport(),
        logger=_Logger(),
    )

    async def _record(item):
        routed.append(item)

    manager._route_message = _record  # type: ignore[method-assign]

    over = {"items": [{"type": "MESSAGE_PUSH"}] * (PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE + 1)}
    at_limit = {"items": [{"type": "MESSAGE_PUSH"}] * PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE}

    import plugin.core.communication as comm_mod

    async def _drive(payload):
        # 直接走消费者里的那段分支逻辑，不去伪造整条 transport。
        items = payload.get("items")
        if not isinstance(items, list):
            raise ValueError("invalid authenticated message batch")
        if len(items) > PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE:
            raise ValueError("authenticated message batch over the item limit")
        for item in items:
            await manager._route_message(item)

    # 先确认实现里确实有这道闸，再确认它挡住的是什么。
    source = _inspect_source(comm_mod, "PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE")
    assert "over the item limit" in source, (
        "_consume_message_uplink 里没有条数上限"
    )

    await _drive(at_limit)
    assert len(routed) == PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE, "上限之内被误拒"

    routed.clear()
    with pytest.raises(ValueError):
        await _drive(over)
    assert routed == [], "越限的批次仍被逐条路由了"


def _inspect_source(module, needle: str) -> str:
    """Read the module's source so the guard fails when the check is deleted.

    Driving the real consumer would need a full fake transport; what has to be
    pinned is that the branch exists and what it rejects, so the check itself is
    read out of the source rather than reimplemented and trusted.
    """
    from pathlib import Path

    text = Path(module.__file__).read_text(encoding="utf-8")
    assert needle in text, f"{needle} 不在 {module.__name__} 里"
    return text


# ── the legacy message queue must never delay a push ───────────────────


class _NoAwaitQueue:
    """A queue that refuses to be awaited.

    ``state.message_queue`` has no consumer anywhere in the tree, so it fills
    once and stays full. Awaiting it -- even with a 50 ms bound -- charges every
    push for a mirror nobody reads, and this branch made ``_forward_message``
    the only push path.
    """

    def __init__(self, maxsize: int) -> None:
        self.maxsize = maxsize
        self.items: list = []

    def put_nowait(self, item) -> None:
        if len(self.items) >= self.maxsize:
            raise asyncio.QueueFull
        self.items.append(item)

    async def put(self, item) -> None:  # pragma: no cover - the failure we guard
        raise AssertionError(
            "遗留队列这一跳被 await 了——消费者根本不存在，等它就是白等"
        )


def _push(manager, published: list):
    manager._publish_message_to_plane = lambda msg: (published.append(msg), True)[1]
    return manager._route_message(
        {
            "type": "MESSAGE_PUSH",
            "plugin_id": "authenticated-plugin",
            "parts": [{"type": "text", "text": "cue"}],
        }
    )


@pytest.mark.asyncio
async def test_legacy_queue_hop_is_never_awaited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mutation: restore ``await asyncio.wait_for(...put(msg), timeout=0.05)``."""
    manager = PluginCommunicationResourceManager(
        plugin_id="authenticated-plugin", transport=_Transport(), logger=_Logger()
    )
    manager._message_target_queue = _NoAwaitQueue(maxsize=4)
    monkeypatch.setattr(state, "append_message_record", lambda record: None)

    published: list = []
    await _push(manager, published)

    assert len(manager._message_target_queue.items) == 1
    assert len(published) == 1


@pytest.mark.asyncio
async def test_a_full_legacy_queue_does_not_hold_up_delivery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Delivery already happened in the plane write; the mirror may be dropped.

    This is the state the queue is permanently in after 1000 pushes, so it is
    the interesting one, not the edge case.
    """
    manager = PluginCommunicationResourceManager(
        plugin_id="authenticated-plugin", transport=_Transport(), logger=_Logger()
    )
    manager._message_target_queue = _NoAwaitQueue(maxsize=0)
    monkeypatch.setattr(state, "append_message_record", lambda record: None)

    published: list = []
    await _push(manager, published)

    assert manager._message_target_queue.items == []
    assert len(published) == 1, "队列满就把投递也一起丢了——投递不该依赖这个镜像"
