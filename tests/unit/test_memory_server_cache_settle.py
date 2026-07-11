"""Regression tests for the /cache + /settle persistence contract.

History — commit cba377c5 (2026-03-29 "Fix/memory hotswap timing") introduced
the /settle endpoint to cover the "cross_server cached everything → renew
session arrives with msgs=0" case, but only the review LLM was wired into the
msgs=0 path. ``store_conversation`` and ``_spawn_outbox_post_turn_signals`` were
gated behind ``if input_history``, so:

  - ``time_indexed.db`` was never written (time perception broken — gap
    always None → trigger_greeting silently skipped).
  - ``outbox.ndjson`` / ``events.ndjson`` / ``facts.json`` were never created
    (fact extraction + evidence-RFC pipeline totally idle).

These tests pin down the new contract on /cache (turn-end "light
persistence" — recent.json + time_indexed.db + outbox extract spawn), so any
future refactor that re-introduces the gap fails loudly here instead of in
the field 46 days later.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _build_history_request_payload(messages: list[dict]) -> str:
    """Serialise a list of role/content dicts to the payload /cache expects.

    Mirrors the cross_server-side ``messages_to_dict`` shape — see
    ``cache_conversation`` → ``convert_to_messages(json.loads(...))``.
    """
    payload = []
    for msg in messages:
        payload.append({"type": msg["role"], "data": {"content": msg["content"]}})
    return json.dumps(payload)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_endpoint_writes_time_indexed_db():
    """/cache 端点必须把消息落到 ``time_indexed.db``（通过 astore_conversation）。

    Regression: commit cba377c5 之后 cache 只 update_history，store 全靠
    /settle——而 cross_server 标准节奏让 settle 永远拿 msgs=0，db 永不被建。
    """
    from app import memory_server

    fake_time_manager = MagicMock()
    fake_time_manager.astore_conversation = AsyncMock(return_value=None)
    fake_recent_history_manager = MagicMock()
    fake_recent_history_manager.update_history = AsyncMock(return_value=None)
    fake_spawn_outbox = AsyncMock(return_value=None)

    payload = _build_history_request_payload([
        {"role": "human", "content": "你好"},
        {"role": "ai", "content": "你好喵~"},
    ])
    request = memory_server.HistoryRequest(input_history=payload)

    with patch.object(memory_server.runtime, "time_manager", fake_time_manager), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent_history_manager), \
         patch.object(memory_server.post_turn, "_spawn_outbox_post_turn_signals", fake_spawn_outbox), \
         patch.object(memory_server.gates, "_aclear_review_clean", AsyncMock(return_value=None)):
        result = await memory_server.cache_conversation(request, "测试角色")

    assert result["status"] == "cached"
    assert result["count"] == 2
    fake_time_manager.astore_conversation.assert_awaited_once()
    awaited_args = fake_time_manager.astore_conversation.await_args
    # astore_conversation(uid, messages, lanlan_name) — 顺序由 store_conversation 签名定
    assert awaited_args.args[2] == "测试角色"
    assert len(awaited_args.args[1]) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_endpoint_spawns_outbox_post_turn_signals():
    """/cache 端点必须登记 outbox op，让 events.ndjson / outbox.ndjson 这条
    链能动起来——op handler 跑 counter bump + 复读嗅探 + check_feedback。

    注：``OP_POST_TURN_SIGNALS`` 的字符串值仍是 ``"extract_facts"``——
    outbox.ndjson wire-format 不可变（见 memory/outbox.py 注释）。Stage-1
    per-turn 抽取已按 RFC §3.4.3 迁到 ``_periodic_signal_extraction_loop``，
    ON-mode 不再 per-turn 跑——见
    ``test_run_post_turn_signals_skips_stage1_when_powerful_memory_on``。

    Regression: 旧 cache 完全跳过 outbox，evidence-RFC 链路全空转。
    """
    from app import memory_server

    fake_time_manager = MagicMock()
    fake_time_manager.astore_conversation = AsyncMock(return_value=None)
    fake_recent_history_manager = MagicMock()
    fake_recent_history_manager.update_history = AsyncMock(return_value=None)
    fake_spawn_outbox = AsyncMock(return_value=None)

    payload = _build_history_request_payload([
        {"role": "human", "content": "我喜欢吃草莓"},
        {"role": "ai", "content": "记下来啦~"},
    ])
    request = memory_server.HistoryRequest(input_history=payload)

    with patch.object(memory_server.runtime, "time_manager", fake_time_manager), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent_history_manager), \
         patch.object(memory_server.post_turn, "_spawn_outbox_post_turn_signals", fake_spawn_outbox), \
         patch.object(memory_server.gates, "_aclear_review_clean", AsyncMock(return_value=None)):
        await memory_server.cache_conversation(request, "测试角色")

    fake_spawn_outbox.assert_awaited_once()
    spawn_args = fake_spawn_outbox.await_args
    assert spawn_args.args[0] == "测试角色"
    assert len(spawn_args.args[1]) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_post_turn_signals_skips_stage1_when_powerful_memory_on():
    """powerful_memory ON 模式：Stage-1 per-turn fact_extract 已按 RFC §3.4.3
    迁到 ``_periodic_signal_extraction_loop`` 做 batch 抽取，per-turn 主路径
    不应再调 ``fact_store.extract_facts``。

    Pin 这条不变量：任何后续 refactor 把 ON-mode 的 Stage-1 加回 per-turn
    主路径（出于"保留 PR-1 时 facts.json 每轮及时更新"理由），都会被这个用例
    抓到——每 turn 浪费一次 yield 极低、无上下文的 LLM 抽取（详见 RFC
    §3.4.3 + 3.4.5 cost 估算）。

    本用例仍允许 counter bump + 复读嗅探 + check_feedback——它们是 RFC
    设计内明确保留的 per-turn 操作。
    """
    from app import memory_server

    fake_fact_store = MagicMock()
    fake_fact_store.extract_facts = AsyncMock(return_value=[])
    fake_persona_manager = MagicMock()
    fake_persona_manager.arecord_mentions = AsyncMock(return_value=None)
    fake_reflection_engine = MagicMock()
    fake_reflection_engine.arecord_mentions = AsyncMock(return_value=None)
    fake_reflection_engine.aload_surfaced = AsyncMock(return_value=[])  # no pending → check_feedback 跳过

    from utils.llm_client import HumanMessage, AIMessage
    payload_messages = [
        HumanMessage(content="测试用户消息"),
        AIMessage(content="测试回复"),
    ]

    with patch.object(memory_server.runtime, "fact_store", fake_fact_store), \
         patch.object(memory_server.runtime, "persona_manager", fake_persona_manager), \
         patch.object(memory_server.runtime, "reflection_engine", fake_reflection_engine), \
         patch.object(memory_server.signal_extraction, "_signal_check_record_turn", MagicMock(return_value=None)), \
         patch.object(memory_server.gates, "_ais_powerful_memory_enabled", AsyncMock(return_value=True)):
        await memory_server._run_post_turn_signals(payload_messages, "测试角色")

    # ON-mode 下 Stage-1 per-turn fact_extract 一定不能被调（交给 batch loop）
    fake_fact_store.extract_facts.assert_not_awaited()
    # 但复读嗅探 + surfaced 检查仍必须 per-turn 跑
    fake_persona_manager.arecord_mentions.assert_awaited()
    fake_reflection_engine.arecord_mentions.assert_awaited()
    fake_reflection_engine.aload_surfaced.assert_awaited_once_with("测试角色")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_post_turn_signals_keeps_stage1_when_powerful_memory_off():
    """powerful_memory OFF 模式：``_periodic_signal_extraction_loop`` 整段停
    （见 ``if not powerful_enabled: continue``），per-turn Stage-1 是 fact
    extraction 的唯一兜底路径，必须保留——否则 OFF 模式用户的 facts.json
    完全停止更新（chatgpt-codex-connector PR #1346 抓到的 regression）。

    本用例钉住 ON/OFF 不对称：ON 委托给 batch loop，OFF 跑 legacy per-turn。
    """
    from app import memory_server

    fake_fact_store = MagicMock()
    fake_fact_store.extract_facts = AsyncMock(return_value=[])
    fake_persona_manager = MagicMock()
    fake_persona_manager.arecord_mentions = AsyncMock(return_value=None)
    fake_reflection_engine = MagicMock()
    fake_reflection_engine.arecord_mentions = AsyncMock(return_value=None)
    fake_reflection_engine.aload_surfaced = AsyncMock(return_value=[])

    from utils.llm_client import HumanMessage, AIMessage
    payload_messages = [
        HumanMessage(content="测试用户消息"),
        AIMessage(content="测试回复"),
    ]

    with patch.object(memory_server.runtime, "fact_store", fake_fact_store), \
         patch.object(memory_server.runtime, "persona_manager", fake_persona_manager), \
         patch.object(memory_server.runtime, "reflection_engine", fake_reflection_engine), \
         patch.object(memory_server.signal_extraction, "_signal_check_record_turn", MagicMock(return_value=None)), \
         patch.object(memory_server.gates, "_ais_powerful_memory_enabled", AsyncMock(return_value=False)):
        await memory_server._run_post_turn_signals(payload_messages, "测试角色")

    # OFF-mode 下 batch loop 不跑——per-turn Stage-1 必须 fallback
    fake_fact_store.extract_facts.assert_awaited_once()
    # 复读嗅探仍 per-turn 跑（与 ON-mode 同款）
    fake_persona_manager.arecord_mentions.assert_awaited()
    fake_reflection_engine.arecord_mentions.assert_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_endpoint_empty_payload_short_circuits():
    """空 payload 直接返回，不调任何 persistence 路径——避免空 outbox op 污染。"""
    from app import memory_server

    fake_time_manager = MagicMock()
    fake_time_manager.astore_conversation = AsyncMock(return_value=None)
    fake_recent_history_manager = MagicMock()
    fake_recent_history_manager.update_history = AsyncMock(return_value=None)
    fake_spawn_outbox = AsyncMock(return_value=None)

    request = memory_server.HistoryRequest(input_history=json.dumps([]))

    with patch.object(memory_server.runtime, "time_manager", fake_time_manager), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent_history_manager), \
         patch.object(memory_server.post_turn, "_spawn_outbox_post_turn_signals", fake_spawn_outbox):
        result = await memory_server.cache_conversation(request, "测试角色")

    assert result == {"status": "cached", "count": 0}
    fake_time_manager.astore_conversation.assert_not_awaited()
    fake_spawn_outbox.assert_not_awaited()
    fake_recent_history_manager.update_history.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_endpoint_serialises_recent_and_store_under_settle_lock():
    """``update_history`` 和 ``astore_conversation`` 必须在 ``_get_settle_lock``
    持锁内串行——和 /process / /renew / /settle 对偶，避免并发 cache 把
    db 写顺序打乱（同时也防止 cache 和 settle 抢着写同一份 recent.json）。

    显式校验 lock observability：patch ``_get_settle_lock`` 成可观测的 async
    context manager，断言 lock-enter 在 update_history / astore_conversation
    之前发生、lock-exit 在它们之后但在 spawn_outbox 之前发生。否则未来如果
    有人把前两步移到 ``async with`` 外面但保留顺序，纯顺序断言会漏检。
    """
    from app import memory_server

    order: list[str] = []

    class _ObservableLock:
        async def __aenter__(self):
            order.append("lock_enter")
            return self

        async def __aexit__(self, exc_type, exc, tb):
            order.append("lock_exit")
            return None

    observable_lock = _ObservableLock()

    async def _fake_update_history(*args, **kwargs):
        order.append("update_history")

    async def _fake_astore(*args, **kwargs):
        order.append("astore_conversation")

    async def _fake_spawn(*args, **kwargs):
        order.append("spawn_outbox")

    fake_time_manager = MagicMock()
    fake_time_manager.astore_conversation = AsyncMock(side_effect=_fake_astore)
    fake_recent_history_manager = MagicMock()
    fake_recent_history_manager.update_history = AsyncMock(side_effect=_fake_update_history)

    payload = _build_history_request_payload([
        {"role": "human", "content": "test"},
        {"role": "ai", "content": "ok"},
    ])
    request = memory_server.HistoryRequest(input_history=payload)

    with patch.object(memory_server.runtime, "time_manager", fake_time_manager), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent_history_manager), \
         patch.object(memory_server.post_turn, "_spawn_outbox_post_turn_signals", AsyncMock(side_effect=_fake_spawn)), \
         patch.object(memory_server.gates, "_aclear_review_clean", AsyncMock(return_value=None)), \
         patch.object(memory_server.runtime, "_get_settle_lock", MagicMock(return_value=observable_lock)):
        await memory_server.cache_conversation(request, "测试角色")

    # 严格契约：lock-enter → update_history → astore_conversation → lock-exit → spawn_outbox
    # 前 4 步必须夹在 enter/exit 之间（串行 + lock 内），spawn_outbox 在 lock 外。
    assert order == [
        "lock_enter",
        "update_history",
        "astore_conversation",
        "lock_exit",
        "spawn_outbox",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_settle_endpoint_msgs_zero_still_runs_review():
    """/settle msgs=0 时仍需触发 ``update_history([], detailed=True)`` 跑 review
    LLM——这是 /settle 在新分工下的剩余职责（cache 已经负责 store + outbox）。

    不变量：不管 msgs 是否为空，settle 必须调一次 update_history([], detailed=True)。
    """
    from app import memory_server

    fake_time_manager = MagicMock()
    fake_time_manager.astore_conversation = AsyncMock(return_value=None)
    fake_recent_history_manager = MagicMock()
    fake_recent_history_manager.update_history = AsyncMock(return_value=None)
    fake_spawn_outbox = AsyncMock(return_value=None)
    fake_maybe_spawn_review = AsyncMock(return_value=None)

    request = memory_server.HistoryRequest(input_history=json.dumps([]))

    with patch.object(memory_server.runtime, "time_manager", fake_time_manager), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent_history_manager), \
         patch.object(memory_server.post_turn, "_spawn_outbox_post_turn_signals", fake_spawn_outbox), \
         patch.object(memory_server.gates, "_aclear_review_clean", AsyncMock(return_value=None)), \
         patch.object(memory_server.review, "maybe_spawn_review", fake_maybe_spawn_review):
        result = await memory_server.settle_conversation(request, "测试角色")

    assert result["status"] == "settled"
    # msgs=0：review LLM 仍跑，但 store / outbox 不重复跑（因为 cache 已经做了）
    fake_recent_history_manager.update_history.assert_awaited_once()
    call = fake_recent_history_manager.update_history.await_args
    assert call.args[0] == []
    assert call.kwargs.get("detailed") is True
    fake_time_manager.astore_conversation.assert_not_awaited()
    fake_spawn_outbox.assert_not_awaited()
    fake_maybe_spawn_review.assert_awaited_once_with("测试角色")
