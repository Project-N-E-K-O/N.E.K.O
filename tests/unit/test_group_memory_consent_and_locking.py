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
    """The smallest group session that can really run the drain."""
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

    assert len(recorder.calls) == 6
    assert all(kwargs.get("timeout") for _m, _u, kwargs in recorder.calls)
    by_endpoint = {
        url.rsplit("/", 1)[-1]: kwargs.get("timeout")
        for _method, url, kwargs in recorder.calls
    }
    assert by_endpoint["scoped_history"] == 30.0
    assert by_endpoint["scoped_context"] == 5.0
    assert by_endpoint["scoped_mentions"] == 5.0
    # /new_dialog/{name}、/query_memory/{name}、/cache/{name} 都以角色名结尾
    assert by_endpoint["Neko"] == 5.0


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
