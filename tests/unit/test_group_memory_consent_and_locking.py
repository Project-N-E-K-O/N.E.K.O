"""Guards for three QQ memory-pipeline defects.

- A silent turn (the model chose not to reply) still runs memory
  housekeeping; a non-admin private chat must never reach the owner's
  legacy corpus. The gate lives in the callee, ``cache_session_delta`` —
  the silent path carries no caller-side check at all.
- The member-bucket drain holds the session lock only to take and return
  its snapshot; the scoped POSTs always run outside it, and turns that
  arrive mid-drain are neither lost nor queued twice.
- ``QQMemoryBridge`` reuses one httpx client while each endpoint still
  passes its own timeout per request (scoped history 30s, the rest 5s).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def _msg(msg_type: str, text: str):
    return SimpleNamespace(type=msg_type, content=text)


def _session_lock_runner():
    """A session lock that really serializes (not a passthrough double)."""
    locks: dict[str, asyncio.Lock] = {}

    async def _run_with_session_lock(session_key, coro_factory):
        lock = locks.setdefault(session_key, asyncio.Lock())
        async with lock:
            return await coro_factory()

    return _run_with_session_lock, locks


# ── 静默轮越权写入 ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_silent_turn_never_caches_unauthorized_private_history():
    """A non-admin private chat plus a silent model wrote a friend's
    messages into the owner's legacy corpus.

    The silent turn calls ``_run_memory_housekeeping`` ->
    ``_cache_session_delta`` unconditionally, bypassing the success path's
    ``if user_data.get("memory_enabled")``. A non-admin private chat has
    memory_enabled permanently False (prompt_builder returns False for any
    permission level other than admin), so every silent turn wrote data it
    had no authorization for.
    """
    from plugin.plugins.qq_auto_reply.reply_generation_service import (
        QQReplyGenerationService,
    )
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    history = [_msg("human", "好友说的话"), _msg("ai", "回复")]
    user_data = {
        "is_group": False,
        "memory_enabled": False,
        "her_name": "Neko",
        "session": SimpleNamespace(_conversation_history=history),
        "last_synced_index": 0,
    }
    bridge = SimpleNamespace(
        post_memory_history=AsyncMock(return_value={"status": "ok"}),
    )
    plugin = SimpleNamespace(
        _user_sessions={"private:2046": user_data},
        memory_bridge=bridge,
        logger=MagicMock(),
    )
    memory_service = QQSessionMemoryService(plugin)
    plugin.session_memory_service = memory_service
    plugin._cache_session_delta = memory_service.cache_session_delta
    generation = QQReplyGenerationService(plugin)

    await generation._run_memory_housekeeping("private:2046", user_data)

    bridge.post_memory_history.assert_not_awaited()
    assert user_data["last_synced_index"] == 0
    assert not user_data.get("has_cached_memory")
    # _run_memory_housekeeping 把异常吞成一条 warning：如果这条测试是靠
    # 崩掉才没发出请求，断言就毫无意义了。
    assert not [
        call for call in plugin.logger.warning.call_args_list
        if "记忆管家调度失败" in str(call)
    ]

    # 对照组：已授权（admin）私聊照常入库——闸不是把整条路封死。
    user_data["memory_enabled"] = True
    await generation._run_memory_housekeeping("private:2046", user_data)
    bridge.post_memory_history.assert_awaited_once()
    assert bridge.post_memory_history.await_args.args[0] == "cache"
    sent = bridge.post_memory_history.await_args.args[2]
    assert [m["content"][0]["text"] for m in sent] == ["好友说的话"]


# ── 成员桶排空的锁语义 ──────────────────────────────────────────────


def _group_drain_harness(post_scoped):
    """The smallest group session that can really run the drain.

    ``post_scoped`` may be None when the caller rebinds
    ``plugin.memory_bridge.post_scoped_memory_history`` itself."""
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )

    user_data = {
        "is_group": True,
        "memory_enabled": True,
        "group_id": "7788",
        "her_name": "Neko",
        "group_member_memory_messages": {
            "2046": [{"role": "user", "content": [{"type": "text", "text": "旧发言"}]}],
        },
        "group_member_memory_labels": {"2046": "阿离(2046)"},
        "member_drain_in_flight": True,
    }
    run_with_session_lock, locks = _session_lock_runner()
    plugin = SimpleNamespace(
        _user_sessions={"group:7788": user_data},
        _qq_settings={
            "group_memory_enabled": True,
            "group_member_memory_enabled": True,
        },
        _run_with_session_lock=run_with_session_lock,
        logger=MagicMock(),
        permission_mgr=SimpleNamespace(get_nickname=lambda *a, **k: None),
        memory_bridge=SimpleNamespace(
            post_scoped_memory_history=post_scoped,
            group_participant_subject=(
                lambda gid, sid: {
                    "subject_kind": "group_participant",
                    "subject_id": f"qq:{gid}:{sid}",
                }
            ),
        ),
    )
    return QQSessionMemoryService(plugin), plugin, user_data, locks


@pytest.mark.asyncio
async def test_member_drain_frees_session_lock_while_posting():
    """Message handling for the same group must still get the lock.

    One sweep is at worst two waves of four concurrent requests at 30s
    each. Keeping that inside the session lock stalls the whole group, and
    the handlers waiting on it each hold a slot of the global semaphore.
    """
    released = asyncio.Event()
    in_flight = asyncio.Event()

    async def _post_scoped(her_name, messages, **kwargs):
        in_flight.set()
        await released.wait()
        return {"status": "ok"}

    service, plugin, user_data, _locks = _group_drain_harness(_post_scoped)

    drain = asyncio.create_task(service._drain_member_buckets("group:7788"))
    await asyncio.wait_for(in_flight.wait(), timeout=2.0)

    # 请求在飞 —— 此刻会话锁必须是空闲的。
    handled = []

    async def _competing_handler():
        await plugin._run_with_session_lock(
            "group:7788", lambda: _record_handled(handled),
        )

    await asyncio.wait_for(_competing_handler(), timeout=2.0)
    assert handled == ["handled"]

    released.set()
    await asyncio.wait_for(drain, timeout=2.0)
    assert "member_drain_in_flight" not in user_data


async def _record_handled(sink: list) -> None:
    sink.append("handled")


@pytest.mark.asyncio
async def test_turns_arriving_during_drain_are_neither_lost_nor_resent():
    """Turns arriving mid-drain belong to a fresh generation: they must
    not join the in-flight payload (the whole bucket is popped on success,
    so they would vanish) nor be resubmitted on the next sweep."""
    released = asyncio.Event()
    in_flight = asyncio.Event()
    sent_payloads: list[list] = []

    async def _post_scoped(her_name, messages, **kwargs):
        sent_payloads.append(list(messages))
        in_flight.set()
        await released.wait()
        return {"status": "ok"}

    service, plugin, user_data, _locks = _group_drain_harness(_post_scoped)
    context = SimpleNamespace(
        is_group=True, sender_id="2046", member_memory_enabled=True,
        source_kind="incoming_group", group_facing=False,
        group_scene_mode="", message="冲刷期间的新发言",
        user_nickname="阿离",
    )

    drain = asyncio.create_task(service._drain_member_buckets("group:7788"))
    await asyncio.wait_for(in_flight.wait(), timeout=2.0)

    # 名额已经腾空，新发言进得来。
    service.record_group_member_turn(user_data, context)

    released.set()
    await asyncio.wait_for(drain, timeout=2.0)

    # 在飞的那一批只含快照时刻的旧发言。
    assert len(sent_payloads) == 1
    assert [
        part["text"]
        for message in sent_payloads[0]
        for part in message["content"]
    ] == ["旧发言"]
    # 新发言留在队列里等下一轮，一条不多一条不少。
    remaining = user_data["group_member_memory_messages"]["2046"]
    assert [
        part["text"] for message in remaining for part in message["content"]
    ] == ["冲刷期间的新发言"]


@pytest.mark.asyncio
async def test_failed_drain_returns_buckets_ahead_of_newer_turns():
    """A failed bucket returns to the queue, ahead of the turns that
    arrived while it was in flight, so the order stays chronological."""
    in_flight = asyncio.Event()
    released = asyncio.Event()

    async def _post_scoped(her_name, messages, **kwargs):
        in_flight.set()
        await released.wait()
        return {"status": "error", "message": "memory server down"}

    service, plugin, user_data, _locks = _group_drain_harness(_post_scoped)
    context = SimpleNamespace(
        is_group=True, sender_id="2046", member_memory_enabled=True,
        source_kind="incoming_group", group_facing=False,
        group_scene_mode="", message="冲刷期间的新发言",
        user_nickname="阿离",
    )

    drain = asyncio.create_task(service._drain_member_buckets("group:7788"))
    await asyncio.wait_for(in_flight.wait(), timeout=2.0)
    service.record_group_member_turn(user_data, context)
    released.set()
    await asyncio.wait_for(drain, timeout=2.0)

    remaining = user_data["group_member_memory_messages"]["2046"]
    assert [
        part["text"] for message in remaining for part in message["content"]
    ] == ["旧发言", "冲刷期间的新发言"]
    # 调度器已经把 due 标消费掉了，失败必须重新举起来。
    assert user_data["member_flush_due"] is True
    assert "member_drain_in_flight" not in user_data
    # label 也要回到 live 映射，否则下一轮的 speaker_label 会退化成 QQ 号。
    assert user_data["group_member_memory_labels"]["2046"] == "阿离(2046)"


@pytest.mark.asyncio
async def test_drain_drops_failed_buckets_when_consent_revoked_mid_flight():
    """Member memory switched off mid-flight: failed buckets are dropped
    fail-closed rather than queued for another attempt at the server."""
    in_flight = asyncio.Event()
    released = asyncio.Event()

    async def _post_scoped(her_name, messages, **kwargs):
        in_flight.set()
        await released.wait()
        raise RuntimeError("boom")

    service, plugin, user_data, _locks = _group_drain_harness(_post_scoped)

    drain = asyncio.create_task(service._drain_member_buckets("group:7788"))
    await asyncio.wait_for(in_flight.wait(), timeout=2.0)
    plugin._qq_settings["group_member_memory_enabled"] = False
    released.set()
    await asyncio.wait_for(drain, timeout=2.0)

    assert not user_data.get("group_member_memory_messages")
    assert not user_data.get("member_flush_due")
    assert "member_drain_in_flight" not in user_data


# ── 共享 http client + per-request timeout ──────────────────────────


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload
        self.text = "记忆正文"

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _RecordingClient:
    """Records every request the bridge makes, with its kwargs."""

    def __init__(self):
        self.is_closed = False
        self.calls: list[tuple[str, str, dict]] = []

    async def get(self, url, **kwargs):
        self.calls.append(("GET", url, kwargs))
        return _FakeResponse({})

    async def post(self, url, **kwargs):
        self.calls.append(("POST", url, kwargs))
        return _FakeResponse({"results": [], "elapsed_ms": 1.0})


async def _drive_every_endpoint(bridge) -> None:
    subject = {"subject_kind": "group_chat", "subject_id": "qq:7788"}
    await bridge.fetch_bootstrap_memory("Neko")
    await bridge.fetch_scoped_bootstrap_memory("Neko", subjects=[subject])
    await bridge.post_scoped_mentions("Neko", "回复正文", subjects=[subject])
    await bridge.query_relevant_memory("Neko", "查询")
    await bridge.post_memory_history("cache", "Neko", [{"role": "user"}])
    await bridge.post_scoped_memory_history(
        "Neko", [{"role": "user"}],
        subject={"subject_kind": "group_participant", "subject_id": "qq:7788:2046"},
    )


@pytest.mark.asyncio
async def test_memory_bridge_keeps_per_endpoint_timeouts_on_shared_client(
    monkeypatch,
):
    """The timeout moved from a per-call client to each request.

    Scoped history waits on an LLM extraction (30s) while the rest are
    local reads (5s); the shared client carries an unrelated default, so a
    request that forgets to state its own would silently take that one.
    """
    from plugin.plugins.qq_auto_reply.memory_bridge import QQMemoryBridge

    recorder = _RecordingClient()
    monkeypatch.setattr(QQMemoryBridge, "_client", staticmethod(lambda: recorder))
    await _drive_every_endpoint(QQMemoryBridge(SimpleNamespace(logger=MagicMock())))

    # 按完整路径建 key：三条 /xxx/{name} 端点的最后一段都是角色名，用
    # 末段做 key 会让它们相互覆盖，只剩最后写入的那条真被断言到。
    assert {
        url.split("/", 3)[3]: kwargs.get("timeout")
        for _method, url, kwargs in recorder.calls
    } == {
        "new_dialog/Neko": 5.0,
        "query_memory/Neko": 5.0,
        "cache/Neko": 5.0,
        "internal/memory/Neko/scoped_context": 5.0,
        "internal/memory/Neko/scoped_mentions": 5.0,
        "internal/memory/Neko/scoped_history": 30.0,
    }


@pytest.mark.asyncio
async def test_memory_bridge_uses_the_shared_internal_client(monkeypatch):
    """The bridge must not own an httpx client.

    utils/http/internal_client.py is the sanctioned pool for 127.0.0.1
    services and is closed once by main_server's shutdown hook. A plugin-
    owned client would be torn down by plugin shutdown while the memory
    settlement tasks it deliberately does not cancel are still posting.
    """
    from plugin.plugins.qq_auto_reply import memory_bridge as bridge_module
    from utils.http import internal_client

    recorder = _RecordingClient()
    handed_out: list = []

    def _fake_get_internal_http_client():
        handed_out.append(recorder)
        return recorder

    monkeypatch.setattr(
        internal_client, "get_internal_http_client", _fake_get_internal_http_client,
    )
    bridge = bridge_module.QQMemoryBridge(SimpleNamespace(logger=MagicMock()))
    await _drive_every_endpoint(bridge)

    assert len(handed_out) == 6 and all(c is recorder for c in handed_out)
    # 插件侧没有任何自有 client 生命周期可言。
    assert not hasattr(bridge, "aclose")
    assert not hasattr(bridge_module, "httpx")


@pytest.mark.asyncio
async def test_concurrent_flush_does_not_clear_the_other_flushs_in_flight_mark():
    """Two member flushes can now overlap, so the mark has to be counted.

    The cap drain runs its POSTs with the session lock released, so an
    idle/shutdown finalize can start its own flush on the live mapping
    meanwhile. With a boolean, whichever finished first cleared the mark
    while the other still owned an in-flight mapping — and an opt-out
    landing in that window copies that very mapping into the settlement
    snapshot, submitting the same messages a second time.
    """
    drain_in_flight = asyncio.Event()
    finalize_in_flight = asyncio.Event()
    drain_released = asyncio.Event()
    finalize_released = asyncio.Event()
    posted: list[str] = []

    async def _post_scoped(her_name, messages, **kwargs):
        subject_id = (kwargs.get("subject") or {}).get("subject_id", "")
        posted.append(subject_id)
        if subject_id.endswith(":2046"):
            drain_in_flight.set()
            await drain_released.wait()
        else:
            finalize_in_flight.set()
            await finalize_released.wait()
        return {"status": "ok"}

    service, plugin, user_data, _locks = _group_drain_harness(_post_scoped)
    drain = asyncio.create_task(service._drain_member_buckets("group:7788"))
    await asyncio.wait_for(drain_in_flight.wait(), timeout=2.0)

    # 冲刷期间又攒了一代，finalize 拿到锁后对**活映射**跑自己那趟冲刷。
    live = user_data.setdefault("group_member_memory_messages", {})
    live["3057"] = [{"role": "user", "content": [{"type": "text", "text": "另一代"}]}]
    finalize = asyncio.create_task(service._flush_member_buckets(
        user_data, group_id="7788", her_name="Neko", reason="idle_timeout",
    ))
    await asyncio.wait_for(finalize_in_flight.wait(), timeout=2.0)

    # 排空整趟收尾，但 finalize 那趟还在飞：标记必须还立着。
    drain_released.set()
    await asyncio.wait_for(drain, timeout=2.0)
    assert user_data.get("member_flush_in_progress"), (
        "先结束的那趟把标记清掉了，opt-out 会把另一趟的在途载荷复制走"
    )

    # 此刻 opt-out 落下（settings_service 关成员记忆那段的判据）。
    if user_data.get("member_flush_in_progress"):
        user_data["member_snapshot_due"] = True
        user_data["pending_member_settle"] = True
    else:
        fresh = user_data.pop("group_member_memory_messages", None) or {}
        pending = user_data.setdefault("pending_settle_buckets", {})
        for sender, messages in fresh.items():
            pending.setdefault(sender, []).extend(messages)
        user_data["pending_member_settle"] = True

    # 在途载荷没有被搬走 —— 搬走就等于排队第二次提交。
    assert user_data.get("group_member_memory_messages") is live
    assert not user_data.get("pending_settle_buckets")

    finalize_released.set()
    await asyncio.wait_for(finalize, timeout=2.0)

    # 两批各发一次，一条不重。
    assert sorted(posted) == ["qq:7788:2046", "qq:7788:3057"]
    assert "member_flush_in_progress" not in user_data
    assert "member_snapshot_due" not in user_data
    assert not (user_data.get("pending_settle_buckets") or {})


@pytest.mark.asyncio
async def test_session_settled_mid_drain_reports_what_it_lost():
    """A session finalized and popped mid-flight strands the drain's data.

    That is the standing cost of not holding the lock, so it must be
    loud rather than silent — and the orphaned mapping must not leave a
    flush counter behind for a dict that could be rebound later.
    """
    released = asyncio.Event()
    in_flight = asyncio.Event()

    async def _post_scoped(her_name, messages, **kwargs):
        in_flight.set()
        await released.wait()
        return {"status": "error", "message": "memory server down"}

    service, plugin, user_data, _locks = _group_drain_harness(_post_scoped)
    drain = asyncio.create_task(service._drain_member_buckets("group:7788"))
    await asyncio.wait_for(in_flight.wait(), timeout=2.0)

    # finalize 在飞行期间结算并弹出会话；此后到达的一代滞留在孤儿 dict 上。
    user_data.setdefault("group_member_memory_messages", {})["3057"] = [
        {"role": "user", "content": [{"type": "text", "text": "滞留"}]},
    ]
    plugin._user_sessions.pop("group:7788")

    released.set()
    await asyncio.wait_for(drain, timeout=2.0)

    errors = " ".join(str(call) for call in plugin.logger.error.call_args_list)
    warnings = " ".join(str(call) for call in plugin.logger.warning.call_args_list)
    assert "会话已结算并弹出" in errors
    assert "1 个滞留队列" in errors, "滞留的一代没救，按 error 记"
    # 快照还有末次重试的机会，此刻不该按「丢失」报 error；重试也失败之后
    # 才落 error。
    assert "转末次重试" in warnings
    assert "末次重试后仍有 1 个成员队列未能入库" in errors
    # 计数放掉了，孤儿 dict 不会永远看起来"冲刷中"。
    assert "member_flush_in_progress" not in user_data


@pytest.mark.asyncio
async def test_drain_in_flight_blocks_session_teardown():
    """The drain must be registered as this session's settlement work.

    Its POSTs run with the session lock released, so the lock is no longer
    the barrier that used to keep discard_session away. Without the
    registration, teardown ejects the session while the drain still holds
    the popped snapshot, and the buckets it failed to flush land in a
    user_data nobody consumes any more.
    """
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService,
    )
    from plugin.plugins.qq_auto_reply.session_runtime_service import (
        QQSessionRuntimeService,
    )

    released = asyncio.Event()
    in_flight = asyncio.Event()

    async def _post_scoped(her_name, messages, **kwargs):
        in_flight.set()
        await released.wait()
        return {"status": "ok"}

    service, plugin, user_data, _locks = _group_drain_harness(_post_scoped)
    user_data.pop("member_drain_in_flight", None)
    user_data["member_flush_due"] = True
    plugin.session_memory_service = service
    plugin.reply_buffer_service = None
    plugin._session_settle_tasks = {}
    plugin._group_memory_sync_tasks = set()
    plugin._spawn_memory_sync_task = (
        lambda coro, *, session_key=None: _session_keyed_task(
            plugin, coro, session_key,
        )
    )
    plugin._has_pending_session_settlement = (
        lambda key: any(
            not task.done()
            for task in (plugin._session_settle_tasks.get(key) or ())
        )
    )
    runtime = QQSessionRuntimeService(plugin)

    # 每轮的记忆管家钩子把排空排到后台。
    await service.cache_session_delta("group:7788", user_data)
    await asyncio.wait_for(in_flight.wait(), timeout=2.0)

    discarded = await runtime.discard_session("group:7788", reason="test")
    assert discarded is False, "排空还攥着快照时不能把会话弹掉"
    assert "group:7788" in plugin._user_sessions

    released.set()
    for task in list(plugin._group_memory_sync_tasks):
        await asyncio.wait_for(task, timeout=2.0)
    # 排空收尾后，会话才可以正常销毁。
    assert not plugin._has_pending_session_settlement("group:7788")


def _session_keyed_task(plugin, coro, session_key):
    task = asyncio.ensure_future(coro)
    plugin._group_memory_sync_tasks.add(task)
    task.add_done_callback(plugin._group_memory_sync_tasks.discard)
    if session_key:
        bucket = plugin._session_settle_tasks.setdefault(session_key, set())
        bucket.add(task)
        task.add_done_callback(bucket.discard)
    return task


@pytest.mark.asyncio
async def test_failed_buckets_never_land_in_a_replacement_session():
    """A same-key replacement session must not inherit the old snapshot.

    Teardown can eject the session mid-flight and a queued group turn can
    rebuild one under the same key — possibly for a different character.
    The snapshot belongs to the old user_data; merging it into the
    replacement means the next flush writes those turns under the new
    session's her_name, i.e. into someone else's memory store.
    """
    released = asyncio.Event()
    in_flight = asyncio.Event()

    async def _post_scoped(her_name, messages, **kwargs):
        in_flight.set()
        await released.wait()
        return {"status": "error", "message": "memory server down"}

    service, plugin, user_data, _locks = _group_drain_harness(_post_scoped)
    drain = asyncio.create_task(service._drain_member_buckets("group:7788"))
    await asyncio.wait_for(in_flight.wait(), timeout=2.0)

    # 旧会话被结算弹出，排队中的群消息用同一个 key 建了新会话（换了角色）。
    plugin._user_sessions.pop("group:7788")
    replacement = {
        "is_group": True,
        "memory_enabled": True,
        "group_id": "7788",
        "her_name": "另一个角色",
        "group_member_memory_messages": {},
        "group_member_memory_labels": {},
    }
    plugin._user_sessions["group:7788"] = replacement

    released.set()
    await asyncio.wait_for(drain, timeout=2.0)

    assert replacement["group_member_memory_messages"] == {}, (
        "旧会话的成员发言挂到了顶替者身上，下一轮会用它的 her_name 写库"
    )
    assert not replacement.get("member_flush_due")
    # 授权还在（两个开关都开着），所以快照转末次重试而不是当场判丢。
    warnings = " ".join(str(call) for call in plugin.logger.warning.call_args_list)
    assert "并已被新会话顶替" in warnings


@pytest.mark.asyncio
async def test_shutdown_flush_waits_for_an_in_flight_drain():
    """The shutdown/idle finalizer pops the session, so it has to let a
    registered drain land first.

    Shutdown joins the sync tasks for one second and then proceeds on
    purpose; a scoped member POST runs up to 30s, so the drain routinely
    survives that join. discard_session has its own deferral, this path
    does not — and the drain holds the only copy of the buckets it popped
    out of the live mapping.
    """
    order: list[str] = []
    released = asyncio.Event()
    in_flight = asyncio.Event()

    async def _post_scoped(her_name, messages, **kwargs):
        in_flight.set()
        await released.wait()
        order.append("drain-done")
        return {"status": "ok"}

    service, plugin, user_data, _locks = _group_drain_harness(_post_scoped)
    plugin._session_settle_tasks = {}

    async def _finalize(session_key, reason):
        order.append(f"finalize:{reason}")
        plugin._user_sessions.pop(session_key, None)
        return True

    service.finalize_user_memory_session = _finalize

    drain = asyncio.create_task(service._drain_member_buckets("group:7788"))
    plugin._session_settle_tasks["group:7788"] = {drain}
    await asyncio.wait_for(in_flight.wait(), timeout=2.0)

    flush = asyncio.create_task(service.flush_all_memory_sessions("shutdown"))
    await asyncio.sleep(0)
    assert order == [], "结算不能在排空还攥着快照时就把会话弹掉"

    released.set()
    await asyncio.wait_for(flush, timeout=3.0)
    await asyncio.wait_for(drain, timeout=2.0)
    assert order == ["drain-done", "finalize:shutdown"]


@pytest.mark.asyncio
async def test_idle_sweep_skips_instead_of_ejecting_a_draining_session():
    """Idle settlement waits, and when the wait runs out it skips.

    The process keeps running, so the next sweep retries: an idle session
    costs nothing by waiting one more round, while ejecting it leaves the
    drain with nowhere to hand its failed buckets back to. Shutdown is the
    other way round (last chance, group digest would go too) and is
    covered by test_shutdown_flush_waits_for_an_in_flight_drain.
    """
    finalized: list[str] = []
    released = asyncio.Event()
    in_flight = asyncio.Event()

    async def _post_scoped(her_name, messages, **kwargs):
        in_flight.set()
        await released.wait()
        return {"status": "ok"}

    service, plugin, user_data, _locks = _group_drain_harness(_post_scoped)
    service.SETTLE_JOIN_TIMEOUT_SECONDS = 0.05
    plugin._session_settle_tasks = {}
    plugin.SESSION_IDLE_TIMEOUT_SECONDS = 0
    user_data["last_activity_at"] = 0

    async def _finalize(session_key, reason):
        finalized.append(reason)
        plugin._user_sessions.pop(session_key, None)
        return True

    service.finalize_user_memory_session = _finalize

    drain = asyncio.create_task(service._drain_member_buckets("group:7788"))
    plugin._session_settle_tasks["group:7788"] = {drain}
    await asyncio.wait_for(in_flight.wait(), timeout=2.0)

    await asyncio.wait_for(service.flush_idle_memory_sessions(), timeout=3.0)
    assert finalized == [], "排空还在途时不该弹掉会话，等下一轮 sweep 就行"
    assert "group:7788" in plugin._user_sessions

    released.set()
    await asyncio.wait_for(drain, timeout=2.0)

    # 排空落地之后，下一轮 sweep 照常结算。
    await asyncio.wait_for(service.flush_idle_memory_sessions(), timeout=3.0)
    assert finalized == ["idle_timeout"]


@pytest.mark.asyncio
async def test_opt_out_settlement_waits_for_the_generation_the_drain_promotes():
    """The opt-out settlement must not run ahead of an in-flight drain.

    The session lock is no longer a barrier, so the settlement can grab it
    first, see a `pending_settle_buckets` that has not been promoted yet,
    and finish having done nothing. The generation the drain promotes
    afterwards then has no consumer left and lingers past the opt-out
    until some unrelated idle/finalize — which an always-busy group may
    never reach.
    """
    posted: list[str] = []
    released = asyncio.Event()
    in_flight = asyncio.Event()

    async def _post_scoped(her_name, messages, **kwargs):
        subject_id = (kwargs.get("subject") or {}).get("subject_id", "")
        posted.append(subject_id)
        if subject_id.endswith(":2046"):
            in_flight.set()
            await released.wait()
        return {"status": "ok"}

    service, plugin, user_data, _locks = _group_drain_harness(_post_scoped)
    plugin._session_settle_tasks = {}

    drain = asyncio.create_task(service._drain_member_buckets("group:7788"))
    plugin._session_settle_tasks["group:7788"] = {drain}
    await asyncio.wait_for(in_flight.wait(), timeout=2.0)

    # 冲刷期间又攒了一代，然后用户关掉成员记忆（设置侧看到有冲刷在飞，
    # 只记待办，不动活映射）。
    user_data.setdefault("group_member_memory_messages", {})["3057"] = [
        {"role": "user", "content": [{"type": "text", "text": "关开关前说的"}]},
    ]
    user_data["member_snapshot_due"] = True
    user_data["pending_member_settle"] = True
    plugin._qq_settings["group_member_memory_enabled"] = False

    settle = asyncio.create_task(service.settle_member_buckets_on_disable())
    await asyncio.sleep(0)
    released.set()
    await asyncio.wait_for(settle, timeout=3.0)
    await asyncio.wait_for(drain, timeout=2.0)

    assert "qq:7788:3057" in posted, (
        "排空提升出来的那一代没人消费，opt-out 之后一直滞留"
    )
    assert not (user_data.get("pending_settle_buckets") or {})
    assert "pending_member_settle" not in user_data


def test_shutdown_waits_longer_for_a_drain_than_the_idle_sweep():
    """The two bounds are not the same price, so they are not the same number.

    An idle sweep that gives up costs one more sweep interval; a shutdown
    that gives up costs the drain's failed buckets outright. And the scoped
    history endpoint runs an LLM extraction (its own request timeout is
    30s), so "no answer in five seconds" is routine there rather than a
    sign of a wedged drain.
    """
    from plugin.plugins.qq_auto_reply.session_memory_service import (
        QQSessionMemoryService as Service,
    )

    assert Service.SETTLE_JOIN_TIMEOUT_LONG_SECONDS > (
        Service.SETTLE_JOIN_TIMEOUT_SECONDS
    )
    # 必须覆盖**整趟**排空（波数 x 单发超时），不是一次请求：只覆盖一次的
    # 话，第二波还攥着快照时等待就到点了。派生而不是写死，三个参数任何一个
    # 改了这里都跟着走。
    waves = -(
        -Service.GROUP_MEMBER_MAX_PARTICIPANTS // Service.MEMBER_FLUSH_CONCURRENCY
    )
    assert waves >= 2
    # 严格大于理论用时：恰好相等会在排空正要返回的那一刻判它还在途。
    assert Service.SETTLE_JOIN_TIMEOUT_LONG_SECONDS > (
        Service.SCOPED_HISTORY_TIMEOUT_SECONDS * waves
    )
    assert Service.SETTLE_JOIN_TIMEOUT_LONG_SECONDS == (
        Service.SCOPED_HISTORY_TIMEOUT_SECONDS * waves
        + Service.SETTLE_JOIN_SLACK_SECONDS
    )


@pytest.mark.asyncio
async def test_opt_out_settlement_skips_rather_than_clearing_its_own_marker():
    """Giving up on the wait must not consume the marker.

    `_settle_one` clears `pending_member_settle` on its way out. If it runs
    before the drain promotes its generation, that generation arrives to
    find its consumer already gone and lingers past the opt-out — the very
    thing this settlement exists to prevent. A scoped-history POST is an
    LLM extraction, so timing out the wait is routine, not exceptional.
    """
    released = asyncio.Event()
    in_flight = asyncio.Event()

    async def _post_scoped(her_name, messages, **kwargs):
        in_flight.set()
        await released.wait()
        return {"status": "ok"}

    service, plugin, user_data, _locks = _group_drain_harness(_post_scoped)
    service.SETTLE_JOIN_TIMEOUT_LONG_SECONDS = 0.05
    plugin._session_settle_tasks = {}

    drain = asyncio.create_task(service._drain_member_buckets("group:7788"))
    plugin._session_settle_tasks["group:7788"] = {drain}
    await asyncio.wait_for(in_flight.wait(), timeout=2.0)

    user_data.setdefault("group_member_memory_messages", {})["3057"] = [
        {"role": "user", "content": [{"type": "text", "text": "关开关前说的"}]},
    ]
    user_data["member_snapshot_due"] = True
    user_data["pending_member_settle"] = True
    plugin._qq_settings["group_member_memory_enabled"] = False

    await asyncio.wait_for(
        service.settle_member_buckets_on_disable(), timeout=3.0,
    )
    assert user_data.get("member_snapshot_due") is True, (
        "等不到就跳过，不能把待提升的标记消费掉"
    )
    assert user_data.get("pending_member_settle") is True

    released.set()
    await asyncio.wait_for(drain, timeout=2.0)
    # 排空落地后那一代被提升出来，标记仍在，后续结算还能消费它。
    assert user_data.get("pending_settle_buckets", {}).get("3057")
    assert user_data.get("pending_member_settle") is True


@pytest.mark.asyncio
async def test_returning_a_failed_snapshot_reapplies_the_hard_limit():
    """The snapshot and the fresh generation are each bounded; their
    concatenation is not.

    With the memory server down and messages still arriving, every failed
    round hands the previous batch back on top of the new one. Without
    re-trimming, the queue the hard limit exists to bound grows without
    limit — which is exactly the server-is-down scenario it was written
    for.
    """
    released = asyncio.Event()
    in_flight = asyncio.Event()

    async def _post_scoped(her_name, messages, **kwargs):
        in_flight.set()
        await released.wait()
        return {"status": "error", "message": "memory server down"}

    service, plugin, user_data, _locks = _group_drain_harness(_post_scoped)
    limit = service.GROUP_MEMBER_HARD_LIMIT
    user_data["group_member_memory_messages"]["2046"] = [
        {"role": "user", "content": [{"type": "text", "text": f"旧-{i}"}]}
        for i in range(limit)
    ]

    drain = asyncio.create_task(service._drain_member_buckets("group:7788"))
    await asyncio.wait_for(in_flight.wait(), timeout=2.0)
    user_data.setdefault("group_member_memory_messages", {})["2046"] = [
        {"role": "user", "content": [{"type": "text", "text": f"新-{i}"}]}
        for i in range(limit)
    ]
    released.set()
    await asyncio.wait_for(drain, timeout=2.0)

    merged = user_data["group_member_memory_messages"]["2046"]
    assert len(merged) == limit, "归还没有重新压硬顶，队列会无界增长"
    # 丢的是最早的，留下的尾部是冲刷期间新到的那一批。
    assert merged[-1]["content"][0]["text"] == f"新-{limit - 1}"
    assert any(
        "超过硬顶" in str(call) for call in plugin.logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_group_invalidate_also_waits_for_an_in_flight_drain():
    """The linked group+member opt-out tears the session down right after.

    `_sync_memory_transitions` runs the member settlement and then
    `invalidate_group_sessions`, whose OFF branch finalizes and pops the
    same session. Skipping only the settlement therefore protects nothing:
    the drain's snapshot is orphaned by the very next step.
    """
    order: list[str] = []
    released = asyncio.Event()
    in_flight = asyncio.Event()

    async def _post_scoped(her_name, messages, **kwargs):
        in_flight.set()
        await released.wait()
        order.append("drain-done")
        return {"status": "ok"}

    service, plugin, user_data, _locks = _group_drain_harness(_post_scoped)
    plugin._session_settle_tasks = {}
    user_data["session"] = SimpleNamespace(_conversation_history=[])
    # OFF 分支只结算带转变标记的会话（没标 = opt-out 之后才建的）。
    user_data["pending_disable_settle"] = True

    async def _finalize(session_key, reason, retain_session=False):
        order.append(f"finalize:{reason}")
        plugin._user_sessions.pop(session_key, None)
        return True

    service.finalize_user_memory_session = _finalize

    drain = asyncio.create_task(service._drain_member_buckets("group:7788"))
    plugin._session_settle_tasks["group:7788"] = {drain}
    await asyncio.wait_for(in_flight.wait(), timeout=2.0)

    invalidate = asyncio.create_task(
        service.invalidate_group_sessions(enabled=False)
    )
    await asyncio.sleep(0)
    assert order == [], "群记忆转变不能在排空还攥着快照时就把会话弹掉"

    released.set()
    await asyncio.wait_for(invalidate, timeout=3.0)
    await asyncio.wait_for(drain, timeout=2.0)
    assert order == ["drain-done", "finalize:group_memory_disabled"]


@pytest.mark.asyncio
async def test_a_single_drain_never_exceeds_one_participant_quota():
    """One sweep carries at most a quota's worth of buckets.

    Returning a failed snapshot may leave up to twice the quota on purpose
    (dropping a whole authorized speaker is worse than briefly exceeding
    it). Draining all of that in one go would take four waves while the
    settlement-side wait is sized for two — the wait would expire with
    later waves still holding the snapshot. Bounding the work beats
    stretching the wait: the remainder goes out on the next round.
    """
    posted: list[str] = []

    async def _post_scoped(her_name, messages, **kwargs):
        posted.append((kwargs.get("subject") or {}).get("subject_id", ""))
        return {"status": "ok"}

    service, plugin, user_data, _locks = _group_drain_harness(_post_scoped)
    quota = service.GROUP_MEMBER_MAX_PARTICIPANTS
    user_data["group_member_memory_messages"] = {
        str(6000 + i): [{"role": "user", "content": [{"type": "text", "text": "x"}]}]
        for i in range(quota * 2)
    }
    user_data["group_member_memory_labels"] = {}

    await asyncio.wait_for(
        service._drain_member_buckets("group:7788"), timeout=3.0,
    )

    assert len(posted) == quota, "一趟排空带走的桶数超过了等待上限所依据的波数"
    # 剩下的还在队列里，并且已经重新举起 due 标等下一轮。
    assert len(user_data["group_member_memory_messages"]) == quota
    assert user_data.get("member_flush_due") is True


@pytest.mark.asyncio
async def test_orphaned_buckets_get_one_last_try_while_consent_holds():
    """Losing the session must not skip the retry the old code gave.

    Before the lock was released, teardown queued behind the drain and the
    finalize that followed retried its failed buckets once. With the lock
    gone nobody picks them up, so the drain retries them itself — outside
    the session lock, since a same-key replacement must not wait on it.
    """
    attempts: list[str] = []
    lock_holders: list[str] = []
    released = asyncio.Event()
    in_flight = asyncio.Event()

    service, plugin, user_data, _locks = _group_drain_harness(None)
    original_lock = plugin._run_with_session_lock

    async def _tracking_lock(session_key, coro_factory):
        lock_holders.append("held")
        try:
            return await original_lock(session_key, coro_factory)
        finally:
            lock_holders.pop()

    plugin._run_with_session_lock = _tracking_lock

    async def _post_scoped(her_name, messages, **kwargs):
        attempts.append((kwargs.get("subject") or {}).get("subject_id", ""))
        if len(attempts) == 1:
            in_flight.set()
            await released.wait()
            return {"status": "error", "message": "memory server hiccup"}
        # 末次重试必须在锁外发：会话已经没了，占着这把锁只会挡住同 key
        # 建起来的新会话。
        assert not lock_holders, "末次重试是在会话锁里发的"
        return {"status": "ok"}

    plugin.memory_bridge.post_scoped_memory_history = _post_scoped

    drain = asyncio.create_task(service._drain_member_buckets("group:7788"))
    await asyncio.wait_for(in_flight.wait(), timeout=2.0)
    # 结算把会话弹掉了，而两个开关都还开着（不是 opt-out）。
    plugin._user_sessions.pop("group:7788")
    released.set()
    await asyncio.wait_for(drain, timeout=3.0)

    assert attempts == ["qq:7788:2046", "qq:7788:2046"], (
        "会话没了但授权还在，失败的桶应当再试一次"
    )


@pytest.mark.asyncio
async def test_opt_out_drops_are_warnings_not_errors():
    """One rule for the level: error means "meant to keep it and didn't".

    A fail-closed discard after opt-out is the design, not a failure, and
    the session-alive path already logged it as a warning. Logging the
    session-ejected variant of the same policy discard as an error buries
    the genuinely unintended losses in noise.
    """
    released = asyncio.Event()
    in_flight = asyncio.Event()

    async def _post_scoped(her_name, messages, **kwargs):
        in_flight.set()
        await released.wait()
        return {"status": "error", "message": "memory server down"}

    service, plugin, user_data, _locks = _group_drain_harness(_post_scoped)
    drain = asyncio.create_task(service._drain_member_buckets("group:7788"))
    await asyncio.wait_for(in_flight.wait(), timeout=2.0)

    # 会话被结算弹出，同时用户关掉了成员记忆：这批按 opt-out 丢是设计。
    user_data.setdefault("group_member_memory_messages", {})["3057"] = [
        {"role": "user", "content": [{"type": "text", "text": "滞留"}]},
    ]
    plugin._user_sessions.pop("group:7788")
    plugin._qq_settings["group_member_memory_enabled"] = False
    released.set()
    await asyncio.wait_for(drain, timeout=2.0)

    warnings = " ".join(str(c) for c in plugin.logger.warning.call_args_list)
    assert "个滞留队列丢失" in warnings
    assert "未冲成功的成员队列丢失" in warnings
    assert not [
        c for c in plugin.logger.error.call_args_list
        if "丢失" in str(c)
    ], "opt-out 的 fail-closed 丢弃是设计，不该按 error 报"
    # 撤销授权之后不再重试。
    assert not any(
        "orphan_retry" in str(c) or "末次重试" in str(c)
        for c in plugin.logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_orphan_retry_rechecks_consent_before_posting():
    """Consent is sampled under the lock; the retry runs outside it.

    An opt-out landing in that window must still be honoured — otherwise
    the last-chance retry becomes the one path that pushes member turns
    to the server after the switch went off, which is exactly what the
    fail-closed rule forbids.
    """
    attempts: list[str] = []
    released = asyncio.Event()
    in_flight = asyncio.Event()

    service, plugin, user_data, _locks = _group_drain_harness(None)
    original_lock = plugin._run_with_session_lock

    async def _revoke_after_snapshot(session_key, coro_factory):
        result = await original_lock(session_key, coro_factory)
        if in_flight.is_set():
            # 锁一放（快照已采样、重试已排上）就撤销授权。
            plugin._qq_settings["group_member_memory_enabled"] = False
        return result

    async def _post_scoped(her_name, messages, **kwargs):
        attempts.append((kwargs.get("subject") or {}).get("subject_id", ""))
        if len(attempts) == 1:
            in_flight.set()
            await released.wait()
            return {"status": "error", "message": "memory server hiccup"}
        return {"status": "ok"}

    plugin.memory_bridge.post_scoped_memory_history = _post_scoped

    drain = asyncio.create_task(service._drain_member_buckets("group:7788"))
    await asyncio.wait_for(in_flight.wait(), timeout=2.0)
    plugin._user_sessions.pop("group:7788")
    plugin._run_with_session_lock = _revoke_after_snapshot
    released.set()
    await asyncio.wait_for(drain, timeout=3.0)

    assert attempts == ["qq:7788:2046"], "撤销授权之后不该再把这批发言推上去"
    assert any(
        "末次重试前授权已撤销" in str(c)
        for c in plugin.logger.warning.call_args_list
    )


@pytest.mark.asyncio
async def test_per_request_consent_check_is_opt_in():
    """The call-site check is not the last suspension point.

    Between it and the actual POST sit the gather's task scheduling and the
    semaphore handoff, so a settings flip can still land in between —
    hence a check immediately before each request. It has to be opt-in:
    the opt-out settlement reuses this same function *after* the switch is
    already off, and checking there would gut it.
    """
    attempts: list[str] = []

    service, plugin, user_data, _locks = _group_drain_harness(None)

    async def _post_scoped(her_name, messages, **kwargs):
        attempts.append((kwargs.get("subject") or {}).get("subject_id", ""))
        return {"status": "ok"}

    plugin.memory_bridge.post_scoped_memory_history = _post_scoped
    plugin._qq_settings["group_member_memory_enabled"] = False

    # 孤儿末次重试：授权已撤销，一个请求都不该发出去。
    failed = await asyncio.wait_for(
        service._flush_member_buckets(
            user_data, group_id="7788", her_name="Neko",
            reason="member_bucket_orphan_retry",
            buckets={"2046": [{"role": "user"}]}, labels={},
            require_consent=True,
        ),
        timeout=2.0,
    )
    assert attempts == [], "发出前授权已撤销，这一批不该再推上去"
    assert failed == ["2046"]
    assert any(
        "发出前授权已撤销" in str(c)
        for c in plugin.logger.warning.call_args_list
    )

    # 而 opt-out 结算走的是默认（不复检）：它的职责正是在开关关掉之后把
    # 已授权期间收集的结算掉，加复检等于把这条路径整个废掉。
    await asyncio.wait_for(
        service._flush_member_buckets(
            user_data, group_id="7788", her_name="Neko",
            reason="member_memory_disabled",
            buckets={"2046": [{"role": "user"}]}, labels={},
        ),
        timeout=2.0,
    )
    assert attempts == ["qq:7788:2046"]


@pytest.mark.asyncio
async def test_second_drain_wave_sees_an_opt_out_from_the_first():
    """A sweep is two waves; the second must not outrun the switch.

    Up to eight buckets pass through a four-slot semaphore, so the later
    wave starts after the earlier requests returned — long enough for an
    opt-out to land. Those buckets come back as failures and the snapshot
    return then discards them fail-closed.
    """
    posted: list[str] = []
    first_wave_done = 0

    service, plugin, user_data, _locks = _group_drain_harness(None)
    quota = service.GROUP_MEMBER_MAX_PARTICIPANTS
    concurrency = service.MEMBER_FLUSH_CONCURRENCY
    user_data["group_member_memory_messages"] = {
        str(7000 + i): [{"role": "user", "content": [{"type": "text", "text": "x"}]}]
        for i in range(quota)
    }
    user_data["group_member_memory_labels"] = {}

    async def _post_scoped(her_name, messages, **kwargs):
        nonlocal first_wave_done
        posted.append((kwargs.get("subject") or {}).get("subject_id", ""))
        first_wave_done += 1
        if first_wave_done == concurrency:
            # 第一波刚跑完，用户关掉了成员记忆。
            plugin._qq_settings["group_member_memory_enabled"] = False
        return {"status": "ok"}

    plugin.memory_bridge.post_scoped_memory_history = _post_scoped

    await asyncio.wait_for(
        service._drain_member_buckets("group:7788"), timeout=3.0,
    )

    assert len(posted) == concurrency, (
        "第二波在 opt-out 之后照样发了出去"
    )
    # 撤销之后剩下的按 fail-closed 丢弃，没有回到队列。
    assert not (user_data.get("group_member_memory_messages") or {})


@pytest.mark.asyncio
async def test_one_request_cannot_outlive_the_wave_budget():
    """The join bound is derived from waves x per-request timeout, so the
    per-request part has to be a real wall clock.

    httpx applies its ``timeout=`` to connect / read / write / pool
    separately rather than to the whole call, and the client is now shared
    process-wide — a saturated pool can burn one budget waiting for a
    connection and another reading the response. An outer bound keeps the
    derivation honest.
    """
    service, plugin, user_data, _locks = _group_drain_harness(None)
    service.SCOPED_HISTORY_TIMEOUT_SECONDS = 0.05

    async def _post_scoped(her_name, messages, **kwargs):
        await asyncio.Event().wait()  # 永远不返回（连接池饿死的极端形态）

    plugin.memory_bridge.post_scoped_memory_history = _post_scoped

    failed = await asyncio.wait_for(
        service._flush_member_buckets(
            user_data, group_id="7788", her_name="Neko", reason="test",
            buckets={"2046": [{"role": "user"}]}, labels={},
        ),
        timeout=2.0,
    )
    assert failed == ["2046"], "单发请求没有被墙钟封顶，波数推导就不成立"


@pytest.mark.asyncio
async def test_orphan_branch_releases_both_in_flight_marks():
    """Both marks are set together, so both have to be released together.

    The live path drops `member_drain_in_flight` in its finally; the
    orphan branch returns early. Leaving it behind means that if the dict
    is ever bound back into `_user_sessions`, the scheduler's
    `not member_drain_in_flight` guard reads false forever and no member
    drain is ever queued again — the queue only empties by hitting its
    hard limit.
    """
    released = asyncio.Event()
    in_flight = asyncio.Event()

    async def _post_scoped(her_name, messages, **kwargs):
        in_flight.set()
        await released.wait()
        return {"status": "ok"}

    service, plugin, user_data, _locks = _group_drain_harness(_post_scoped)
    drain = asyncio.create_task(service._drain_member_buckets("group:7788"))
    await asyncio.wait_for(in_flight.wait(), timeout=2.0)
    plugin._user_sessions.pop("group:7788")
    released.set()
    await asyncio.wait_for(drain, timeout=2.0)

    assert "member_flush_in_progress" not in user_data
    assert "member_drain_in_flight" not in user_data, (
        "调度标记留在孤儿 dict 上，重新绑回去之后排空永远排不上"
    )
