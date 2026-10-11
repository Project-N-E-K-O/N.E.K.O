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
async def test_cache_assigns_locale_order_before_thread_offload():
    """The event-loop admission order must not depend on worker scheduling."""
    from app import memory_server
    from app.memory_server import routes as memory_routes

    allocated = MagicMock(return_value=314)
    real_to_thread = memory_routes.asyncio.to_thread

    async def reject_threaded_allocation(func, *args, **kwargs):
        assert func is not allocated
        return await real_to_thread(func, *args, **kwargs)

    request = memory_server.HistoryRequest(input_history="[]", language="zh-TW")
    with patch.object(
        memory_server.locale_state,
        "allocate_character_prompt_locale_order",
        allocated,
    ), patch.object(memory_routes.asyncio, "to_thread", reject_threaded_allocation):
        result = await memory_server.cache_conversation(request, "测试角色")

    assert result == {"status": "cached", "count": 0}
    allocated.assert_called_once_with("测试角色")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_endpoint_writes_time_indexed_db():
    """/cache 端点必须把消息落到 ``time_indexed.db``（通过 astore_conversation）。

    Regression: commit cba377c5 之后 cache 只 update_history，store 全靠
    /settle——而 cross_server 标准节奏让 settle 永远拿 msgs=0，db 永不被建。
    """
    from app import memory_server

    events = []
    allocate_locale_order = MagicMock(
        side_effect=lambda _name: events.append("allocate") or 314
    )
    fake_time_manager = MagicMock()
    fake_time_manager.astore_conversation = AsyncMock(
        side_effect=lambda *_args: events.append("time-indexed")
    )
    fake_recent_history_manager = MagicMock()
    fake_recent_history_manager.update_history = AsyncMock(
        side_effect=lambda *_args, **_kwargs: events.append("recent")
    )
    fake_spawn_outbox = AsyncMock(return_value=None)

    payload = _build_history_request_payload([
        {"role": "human", "content": "你好"},
        {"role": "ai", "content": "你好喵~"},
    ])
    request = memory_server.HistoryRequest(input_history=payload, language="zh-CN")

    with patch.object(memory_server.runtime, "time_manager", fake_time_manager), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent_history_manager), \
         patch.object(memory_server.post_turn, "_spawn_outbox_post_turn_signals", fake_spawn_outbox), \
         patch.object(memory_server.locale_state, "allocate_character_prompt_locale_order", allocate_locale_order), \
         patch.object(memory_server.gates, "_aclear_review_clean", AsyncMock(return_value=None)):
        result = await memory_server.cache_conversation(request, "测试角色")

    assert result["status"] == "cached"
    assert result["count"] == 2
    assert events == ["allocate", "recent", "time-indexed"]
    fake_time_manager.astore_conversation.assert_awaited_once()
    awaited_args = fake_time_manager.astore_conversation.await_args
    # astore_conversation(uid, messages, lanlan_name) — 顺序由 store_conversation 签名定
    assert awaited_args.args[2] == "测试角色"
    assert len(awaited_args.args[1]) == 2
    assert fake_spawn_outbox.await_args.kwargs["locale_admission_order"] == 314


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_preserves_theater_metadata_in_recent_and_time_index():
    """剧场来源与片段类型必须同时进入近期记忆和时间索引。"""  # noqa: DOCSTRING_CJK
    from app import memory_server
    from memory.message_sources import is_theater_memory_message

    metadata = {
        "source": "theater_numeric_v2",
        "session_id": "theater_session",
        "story_title": "雨夜合租",
        "parts": [
            {"kind": "scene_narration", "phase": "opening", "text": "雨点敲在窗沿。"},
        ],
    }
    payload = json.dumps([
        {"role": "assistant", "content": "雨点敲在窗沿。", "metadata": metadata},
        {"role": "user", "content": "把合同递过去。", "metadata": metadata},
    ], ensure_ascii=False)
    fake_time_manager = MagicMock()
    fake_time_manager.astore_conversation = AsyncMock(return_value=None)
    fake_recent_history_manager = MagicMock()
    fake_recent_history_manager.update_history = AsyncMock(return_value=None)
    fake_spawn_outbox = AsyncMock(return_value=None)
    clear_review = AsyncMock(return_value=None)

    with patch.object(memory_server.runtime, "time_manager", fake_time_manager), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent_history_manager), \
         patch.object(memory_server.post_turn, "_spawn_outbox_post_turn_signals", fake_spawn_outbox), \
         patch.object(memory_server.gates, "_aclear_review_clean", clear_review):
        result = await memory_server.cache_conversation(
            memory_server.HistoryRequest(input_history=payload),
            "测试角色",
        )

    recent_messages = fake_recent_history_manager.update_history.await_args.args[0]
    indexed_messages = fake_time_manager.astore_conversation.await_args.args[1]
    assert result == {"status": "cached", "count": 2}
    assert all(is_theater_memory_message(message) for message in recent_messages)
    assert all(is_theater_memory_message(message) for message in indexed_messages)
    assert recent_messages[0].metadata["parts"][0]["kind"] == "scene_narration"
    # 虚构玩家发言不应让普通对话 review 重新进入待审状态。
    clear_review.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_upserts_theater_episode_summary_instead_of_appending():
    """剧场单集胶囊必须走 Session upsert，并把周目元数据同步进时间索引。"""  # noqa: DOCSTRING_CJK

    from app import memory_server
    from utils.llm_client import SystemMessage

    metadata = {
        "source": "theater_numeric_v2",
        "memory_tier": "episode_summary",
        "message_kind": "episode_summary",
        "story_id": "story_rain",
        "session_id": "theater_session",
        "story_title": "雨夜合租",
        "episode_status": "completed",
        "ending_title": "雨停之后",
        "episode_summary": "两人保住了共同的住处。",
    }
    payload = json.dumps([{
        "role": "system",
        "content": "两人保住了共同的住处。",
        "metadata": metadata,
    }], ensure_ascii=False)
    stored = SystemMessage(
        content="两人保住了共同的住处。",
        metadata={**metadata, "run_index": 2, "story_run_count": 2},
    )
    fake_time_manager = MagicMock()
    fake_time_manager.areconcile_theater_conversations = AsyncMock(
        return_value={"removed": 0, "stored": 1}
    )
    fake_recent_history_manager = MagicMock()
    fake_recent_history_manager.upsert_theater_episode = AsyncMock(return_value=stored)
    fake_recent_history_manager.aget_recent_history = AsyncMock(return_value=[stored])
    fake_recent_history_manager.update_history = AsyncMock(return_value=None)
    fake_spawn_outbox = AsyncMock(return_value=None)

    with patch.object(memory_server.runtime, "time_manager", fake_time_manager), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent_history_manager), \
         patch.object(memory_server.post_turn, "_spawn_outbox_post_turn_signals", fake_spawn_outbox):
        result = await memory_server.cache_conversation(
            memory_server.HistoryRequest(input_history=payload),
            "测试角色",
        )

    assert result == {"status": "cached", "count": 1}
    fake_recent_history_manager.upsert_theater_episode.assert_awaited_once()
    fake_recent_history_manager.update_history.assert_not_awaited()
    fake_spawn_outbox.assert_not_awaited()
    events = fake_time_manager.areconcile_theater_conversations.await_args.args[0]
    event_id, indexed = events["story_rain"]
    assert indexed[0].metadata["run_index"] == 2
    assert indexed[0].metadata["story_run_count"] == 2
    assert event_id.startswith("theater-story-")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_reports_theater_episode_persist_failure():
    """剧场摘要未写盘时不能继续更新时间索引或返回 cached。"""  # noqa: DOCSTRING_CJK

    from app import memory_server

    payload = json.dumps([{
        "role": "system",
        "content": "这一周目仍在继续。",
        "metadata": {
            "source": "theater_numeric_v2",
            "memory_tier": "episode_summary",
            "message_kind": "episode_summary",
            "story_id": "story_write_failure",
            "session_id": "session_write_failure",
        },
    }], ensure_ascii=False)
    fake_recent = MagicMock()
    fake_recent.aget_recent_history = AsyncMock(return_value=[])
    fake_recent.upsert_theater_episode = AsyncMock(
        side_effect=RuntimeError("theater_episode_persist_failed")
    )
    fake_time = MagicMock()
    fake_time.areconcile_theater_conversations = AsyncMock()
    fake_spawn_outbox = AsyncMock()

    with patch.object(memory_server.runtime, "recent_history_manager", fake_recent), \
         patch.object(memory_server.runtime, "time_manager", fake_time), \
         patch.object(
             memory_server.post_turn,
             "_spawn_outbox_post_turn_signals",
             fake_spawn_outbox,
         ):
        result = await memory_server.cache_conversation(
            memory_server.HistoryRequest(input_history=payload),
            "测试角色",
        )

    assert result == {
        "status": "error",
        "message": "theater_episode_persist_failed",
    }
    fake_time.areconcile_theater_conversations.assert_not_awaited()
    fake_spawn_outbox.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_restores_recent_when_theater_index_update_fails():
    from app import memory_server
    from utils.llm_client import SystemMessage

    metadata = {
        "source": "theater_numeric_v2",
        "memory_tier": "episode_summary",
        "message_kind": "episode_summary",
        "story_id": "story_rain",
        "session_id": "session_rain",
    }
    previous = [SystemMessage(content="暂停摘要", metadata=metadata)]
    updated = [SystemMessage(content="完成摘要", metadata=metadata)]
    fake_recent = MagicMock()
    fake_recent.aget_recent_history = AsyncMock(side_effect=[previous, updated])
    fake_recent.upsert_theater_episode = AsyncMock(return_value=updated[0])
    fake_recent.restore_theater_cache_snapshot = AsyncMock()
    fake_time = MagicMock()
    fake_time.areconcile_theater_conversations = AsyncMock(side_effect=OSError("index unavailable"))
    fake_spawn_outbox = AsyncMock()
    payload = json.dumps([{
        "role": "system", "content": "完成摘要", "metadata": metadata,
    }], ensure_ascii=False)

    with patch.object(memory_server.runtime, "recent_history_manager", fake_recent), \
         patch.object(memory_server.runtime, "time_manager", fake_time), \
         patch.object(memory_server.post_turn, "_spawn_outbox_post_turn_signals", fake_spawn_outbox):
        result = await memory_server.cache_conversation(
            memory_server.HistoryRequest(input_history=payload), "测试角色",
        )

    assert result["status"] == "error"
    fake_recent.restore_theater_cache_snapshot.assert_awaited_once_with(
        "测试角色", previous, updated,
    )
    fake_spawn_outbox.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_forget_theater_memory_rebuilds_remaining_story_index():
    """忘记一个剧本后，其他剧本的新胶囊和旧正文索引都必须保留。"""  # noqa: DOCSTRING_CJK

    from app import memory_server
    from utils.llm_client import AIMessage, HumanMessage, SystemMessage

    remaining = SystemMessage(content="另一个剧本摘要", metadata={
        "source": "theater_numeric_v2",
        "memory_tier": "episode_summary",
        "message_kind": "episode_summary",
        "story_id": "story_keep",
        "session_id": "session_keep",
    })
    legacy_remaining = [
        HumanMessage(content="旧版玩家输入", metadata={
            "source": "theater_numeric_v2",
            "story_id": "story_legacy_keep",
            "session_id": "legacy_session_keep",
        }),
        AIMessage(content="旧版猫娘回复", metadata={
            "source": "theater_numeric_v2",
            "story_id": "story_legacy_keep",
            "session_id": "legacy_session_keep",
        }),
    ]
    fake_recent = MagicMock()
    fake_recent.record_theater_story_forget = AsyncMock(return_value="marker_1")
    fake_recent.forget_theater_story = AsyncMock(return_value=2)
    fake_recent.aget_recent_history = AsyncMock(
        return_value=[remaining, *legacy_remaining]
    )
    fake_time = MagicMock()
    fake_time.areconcile_theater_conversations = AsyncMock(
        return_value={"removed": 77, "stored": 1}
    )

    with patch.object(memory_server.runtime, "recent_history_manager", fake_recent), \
         patch.object(memory_server.runtime, "time_manager", fake_time):
        result = await memory_server.forget_theater_memory(
            "测试角色",
            memory_server.TheaterMemoryForgetRequest(story_id="story_forget"),
        )

    assert result == {
        "ok": True,
        "removed_recent": 2,
        "removed_time_index": 77,
        "forget_marker": "marker_1",
    }
    events = fake_time.areconcile_theater_conversations.await_args.args[0]
    assert set(events) == {"story_keep", "story_legacy_keep"}
    assert events["story_legacy_keep"][1] == legacy_remaining


@pytest.mark.unit
@pytest.mark.asyncio
async def test_forget_theater_memory_keeps_recent_when_index_update_fails():
    from app import memory_server
    from fastapi import HTTPException
    from utils.llm_client import SystemMessage

    current = SystemMessage(content="仍需保留", metadata={
        "source": "theater_numeric_v2",
        "memory_tier": "episode_summary",
        "message_kind": "episode_summary",
        "story_id": "story_forget",
        "session_id": "session_forget",
    })
    fake_recent = MagicMock()
    fake_recent.record_theater_story_forget = AsyncMock(return_value="marker_1")
    fake_recent.aget_recent_history = AsyncMock(return_value=[current])
    fake_recent.forget_theater_story = AsyncMock()
    fake_time = MagicMock()
    fake_time.areconcile_theater_conversations = AsyncMock(side_effect=OSError("index unavailable"))

    with patch.object(memory_server.runtime, "recent_history_manager", fake_recent), \
         patch.object(memory_server.runtime, "time_manager", fake_time):
        with pytest.raises(HTTPException) as exc:
            await memory_server.forget_theater_memory(
                "测试角色",
                memory_server.TheaterMemoryForgetRequest(story_id="story_forget"),
            )

    assert exc.value.status_code == 500
    fake_recent.forget_theater_story.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_forget_theater_memory_reports_original_error_when_rollback_fails():
    from app import memory_server
    from app.memory_server import routes
    from fastapi import HTTPException

    recent_error = OSError("recent delete failed")
    fake_recent = MagicMock()
    fake_recent.record_theater_story_forget = AsyncMock(return_value="marker_1")
    fake_recent.aget_recent_history = AsyncMock(return_value=[])
    fake_recent.forget_theater_story = AsyncMock(side_effect=recent_error)
    fake_time = MagicMock()
    fake_time.areconcile_theater_conversations = AsyncMock(side_effect=[
        {"removed": 1}, OSError("index rollback failed"),
    ])
    fake_logger = MagicMock()

    with patch.object(memory_server.runtime, "recent_history_manager", fake_recent), \
         patch.object(memory_server.runtime, "time_manager", fake_time), \
         patch.object(routes, "logger", fake_logger):
        with pytest.raises(HTTPException) as exc:
            await memory_server.forget_theater_memory(
                "测试角色",
                memory_server.TheaterMemoryForgetRequest(story_id="story_forget"),
            )

    assert exc.value.status_code == 500
    assert fake_time.areconcile_theater_conversations.await_count == 2
    fake_logger.exception.assert_called_once()
    assert fake_logger.error.call_args.args[3] is recent_error


def _theater_summary(story_id: str):
    from utils.llm_client import SystemMessage

    return SystemMessage(content=f"{story_id} 摘要", metadata={
        "source": "theater_numeric_v2",
        "memory_tier": "episode_summary",
        "message_kind": "episode_summary",
        "story_id": story_id,
        "session_id": f"session_{story_id}",
    })


@pytest.mark.unit
@pytest.mark.asyncio
async def test_forget_rollback_reindexes_what_recent_actually_holds():
    """A forget that persisted before raising must not resurrect the story in the index."""
    from app import memory_server
    from fastapi import HTTPException

    forgotten = _theater_summary("story_forget")
    kept = _theater_summary("story_keep")
    fake_recent = MagicMock()
    fake_recent.record_theater_story_forget = AsyncMock(return_value="marker_1")
    # Before: both stories. After the partially persisted delete: only the kept one.
    fake_recent.aget_recent_history = AsyncMock(side_effect=[[forgotten, kept], [kept]])
    fake_recent.forget_theater_story = AsyncMock(side_effect=OSError("pending write failed"))
    fake_time = MagicMock()
    fake_time.areconcile_theater_conversations = AsyncMock(return_value={"removed": 1})

    with patch.object(memory_server.runtime, "recent_history_manager", fake_recent), \
         patch.object(memory_server.runtime, "time_manager", fake_time):
        with pytest.raises(HTTPException):
            await memory_server.forget_theater_memory(
                "测试角色",
                memory_server.TheaterMemoryForgetRequest(story_id="story_forget"),
            )

    assert fake_time.areconcile_theater_conversations.await_count == 2
    rollback_events = fake_time.areconcile_theater_conversations.await_args_list[1].args[0]
    assert set(rollback_events) == {"story_keep"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_forget_rollback_falls_back_to_snapshot_when_reread_fails():
    from app import memory_server
    from app.memory_server import routes
    from fastapi import HTTPException

    forgotten = _theater_summary("story_forget")
    kept = _theater_summary("story_keep")
    recent_error = OSError("recent delete failed")
    fake_recent = MagicMock()
    fake_recent.record_theater_story_forget = AsyncMock(return_value="marker_1")
    fake_recent.aget_recent_history = AsyncMock(side_effect=[
        [forgotten, kept], OSError("recent unreadable"),
    ])
    fake_recent.forget_theater_story = AsyncMock(side_effect=recent_error)
    fake_time = MagicMock()
    fake_time.areconcile_theater_conversations = AsyncMock(return_value={"removed": 1})
    fake_logger = MagicMock()

    with patch.object(memory_server.runtime, "recent_history_manager", fake_recent), \
         patch.object(memory_server.runtime, "time_manager", fake_time), \
         patch.object(routes, "logger", fake_logger):
        with pytest.raises(HTTPException):
            await memory_server.forget_theater_memory(
                "测试角色",
                memory_server.TheaterMemoryForgetRequest(story_id="story_forget"),
            )

    rollback_events = fake_time.areconcile_theater_conversations.await_args_list[1].args[0]
    assert set(rollback_events) == {"story_forget", "story_keep"}
    fake_logger.exception.assert_called_once()
    assert fake_logger.error.call_args.args[3] is recent_error


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retract_theater_episode_removes_only_the_declined_capsule():
    """A declined archive's capsule is dropped from recent and the story index is rebuilt."""
    from app import memory_server
    from utils.llm_client import SystemMessage

    def capsule(session_id: str, through: int):
        return SystemMessage(content=f"{session_id}@{through}", metadata={
            "source": "theater_numeric_v2",
            "memory_tier": "episode_summary",
            "message_kind": "episode_summary",
            "story_id": "story_rain",
            "session_id": session_id,
            "archive_through_revision": through,
        })

    declined = capsule("session_a", 9)
    kept = capsule("session_b", 9)
    fake_recent = MagicMock()
    fake_recent.aget_recent_history = AsyncMock(return_value=[declined, kept])
    fake_recent.retract_theater_episode = AsyncMock(return_value=1)
    fake_time = MagicMock()
    fake_time.areconcile_theater_conversations = AsyncMock(return_value={"removed": 1})
    request = memory_server.TheaterEpisodeRetractRequest(
        story_id="story_rain", session_id="session_a", archive_through_revision=9,
    )

    with patch.object(memory_server.runtime, "recent_history_manager", fake_recent), \
         patch.object(memory_server.runtime, "time_manager", fake_time):
        result = await memory_server.retract_theater_episode("测试角色", request)

    assert result == {"ok": True, "removed_recent": 1, "removed_time_index": 1}
    events = fake_time.areconcile_theater_conversations.await_args.args[0]
    assert events["story_rain"][1] == [kept]
    fake_recent.retract_theater_episode.assert_awaited_once_with(
        "story_rain", "session_a", 9, "测试角色",
    )

    # Nothing written for that range (the timed-out write never landed): no-op.
    fake_recent.aget_recent_history = AsyncMock(return_value=[kept])
    fake_recent.retract_theater_episode.reset_mock()
    fake_time.areconcile_theater_conversations.reset_mock()
    with patch.object(memory_server.runtime, "recent_history_manager", fake_recent), \
         patch.object(memory_server.runtime, "time_manager", fake_time):
        result = await memory_server.retract_theater_episode("测试角色", request)

    assert result == {"ok": True, "removed_recent": 0, "removed_time_index": 0}
    fake_recent.retract_theater_episode.assert_not_awaited()
    fake_time.areconcile_theater_conversations.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_reports_error_when_theater_index_and_rollback_both_fail():
    from app import memory_server
    from app.memory_server import routes
    from utils.llm_client import SystemMessage

    metadata = {
        "source": "theater_numeric_v2",
        "memory_tier": "episode_summary",
        "message_kind": "episode_summary",
        "story_id": "story_rain",
        "session_id": "session_rain",
    }
    previous = [SystemMessage(content="暂停摘要", metadata=metadata)]
    updated = [SystemMessage(content="完成摘要", metadata=metadata)]
    fake_recent = MagicMock()
    fake_recent.aget_recent_history = AsyncMock(side_effect=[previous, updated])
    fake_recent.upsert_theater_episode = AsyncMock(return_value=updated[0])
    fake_recent.restore_theater_cache_snapshot = AsyncMock(
        side_effect=RuntimeError("theater_recent_history_changed"),
    )
    fake_time = MagicMock()
    fake_time.areconcile_theater_conversations = AsyncMock(side_effect=OSError("index unavailable"))
    fake_spawn_outbox = AsyncMock()
    fake_logger = MagicMock()
    payload = json.dumps([{
        "role": "system", "content": "完成摘要", "metadata": metadata,
    }], ensure_ascii=False)

    with patch.object(memory_server.runtime, "recent_history_manager", fake_recent), \
         patch.object(memory_server.runtime, "time_manager", fake_time), \
         patch.object(routes, "logger", fake_logger), \
         patch.object(memory_server.post_turn, "_spawn_outbox_post_turn_signals", fake_spawn_outbox):
        result = await memory_server.cache_conversation(
            memory_server.HistoryRequest(input_history=payload), "测试角色",
        )

    assert result["status"] == "error"
    fake_recent.restore_theater_cache_snapshot.assert_awaited_once()
    fake_logger.exception.assert_called_once()
    fake_spawn_outbox.assert_not_awaited()


@pytest.mark.unit
def test_theater_episode_upsert_merges_session_and_caps_story_runs():
    """同 Session 只留一份，重复游玩只保留同剧本最近三个周目胶囊。"""  # noqa: DOCSTRING_CJK

    from memory.recent import _merge_theater_episode_summary
    from utils.llm_client import AIMessage, SystemMessage, message_metadata

    history = []
    for run in range(1, 5):
        metadata = {
            "source": "theater_numeric_v2",
            "memory_tier": "episode_summary",
            "message_kind": "episode_summary",
            "story_id": "story_rain",
            "session_id": f"session_{run}",
            "story_title": "雨夜合租",
            "episode_status": "completed",
            "ending_title": f"结局{run}",
            "episode_summary": f"第{run}次演绎摘要。",
        }
        history, _ = _merge_theater_episode_summary(
            history,
            SystemMessage(content=f"第{run}次演绎摘要。", metadata=metadata),
        )

    assert len(history) == 3
    assert [message_metadata(message)["session_id"] for message in history] == [
        "session_2",
        "session_3",
        "session_4",
    ]
    assert [message_metadata(message)["run_index"] for message in history] == [2, 3, 4]
    assert message_metadata(history[-1])["story_run_count"] == 4
    assert message_metadata(history[-1])["ending_titles_seen"] == [
        "结局1",
        "结局2",
        "结局3",
        "结局4",
    ]

    # 兼容迁移：同一 Session 的旧版多条正文会被一条最新完成胶囊替换，且不增加周目数。
    legacy = [
        AIMessage(content="旧开场", metadata={
            "source": "theater_numeric_v2",
            "story_id": "story_legacy",
            "session_id": "legacy_session",
            "story_title": "旧剧本",
            "episode_status": "paused",
        }),
        AIMessage(content="旧结局正文", metadata={
            "source": "theater_numeric_v2",
            "story_id": "story_legacy",
            "session_id": "legacy_session",
            "story_title": "旧剧本",
            "episode_status": "completed",
            "ending_title": "旧结局",
            "ending_summary": "旧剧本已经完成。",
        }),
    ]
    incoming = SystemMessage(content="更新后的摘要。", metadata={
        "source": "theater_numeric_v2",
        "memory_tier": "episode_summary",
        "message_kind": "episode_summary",
        "story_id": "story_legacy",
        "session_id": "legacy_session",
        "story_title": "旧剧本",
        "episode_status": "completed",
        "ending_title": "旧结局",
        "episode_summary": "更新后的摘要。",
    })
    migrated, _ = _merge_theater_episode_summary(legacy, incoming)
    assert len(migrated) == 1
    assert message_metadata(migrated[0])["run_index"] == 1
    assert message_metadata(migrated[0])["story_run_count"] == 1


@pytest.mark.unit
def test_theater_episode_upsert_caps_all_stories_to_thirty():
    """剧本数量增长时，剧场胶囊不能无上限挤压普通对话。"""  # noqa: DOCSTRING_CJK

    from memory.recent import _merge_theater_episode_summary
    from utils.llm_client import HumanMessage, SystemMessage, message_metadata

    normal_message = HumanMessage(content="普通对话必须保留。")
    history = [normal_message]
    for index in range(35):
        history, _ = _merge_theater_episode_summary(
            history,
            SystemMessage(content=f"剧本 {index} 摘要", metadata={
                "source": "theater_numeric_v2",
                "memory_tier": "episode_summary",
                "message_kind": "episode_summary",
                "story_id": f"story_{index}",
                "session_id": f"session_{index}",
                "story_title": f"剧本 {index}",
                "episode_summary": f"剧本 {index} 摘要",
            }),
        )

    theater_messages = [
        message for message in history if message_metadata(message).get("source") == "theater_numeric_v2"
    ]
    assert len(theater_messages) == 30
    assert message_metadata(theater_messages[0])["story_id"] == "story_5"
    assert normal_message in history


def _wire_theater_capsule(story_id: str, session_id: str, summary: str, **extra):
    """Build a capsule exactly as /cache receives it from build_numeric_v2_memory_messages."""
    from utils.llm_client import convert_to_messages

    metadata = {
        "source": "theater_numeric_v2",
        "memory_tier": "episode_summary",
        "message_kind": "episode_summary",
        "story_id": story_id,
        "session_id": session_id,
        "story_title": f"title {story_id}",
        "episode_status": "completed",
        "ending_summary": summary,
        "episode_summary": summary,
        **extra,
    }
    return convert_to_messages([{
        "role": "system",
        "content": [{"type": "text", "text": summary}],
        "metadata": metadata,
    }])[0]


@pytest.mark.unit
def test_theater_upsert_keeps_other_stories_capsules_byte_identical():
    """Archiving story B must not rewrite story A's capsule (content shape or long summary)."""
    from memory.recent import _compute_review_capacity, _merge_theater_episode_summary
    from utils.llm_client import HumanMessage, messages_to_dict

    # An authored ending summary is token-bounded, not 360-char-bounded.
    long_ending = "Story A ended with a long authored epilogue. " * 12
    assert len(long_ending) > 360
    history, _ = _merge_theater_episode_summary(
        [HumanMessage(content="ordinary chat")],
        _wire_theater_capsule("story_a", "session_a", long_ending),
    )
    history.append(HumanMessage(content="more ordinary chat"))
    before = messages_to_dict(history)
    snapshot = list(history)

    merged, _ = _merge_theater_episode_summary(
        history, _wire_theater_capsule("story_b", "session_b", "Story B ended."),
    )

    assert messages_to_dict(merged)[:len(before)] == before
    assert isinstance(before[1]["data"]["content"], list)
    capacity, cutoff = _compute_review_capacity(snapshot, merged)
    assert (capacity, cutoff) == (len(snapshot), len(snapshot) - 1)


@pytest.mark.unit
def test_theater_run_counters_share_one_parser_for_storage_and_prompt():
    """Stored and rendered run_index / story_run_count must use the same parser."""
    from app.memory_server import routes
    from memory import recent

    assert routes._positive_metadata_int is recent._positive_metadata_int


@pytest.mark.unit
def test_theater_upsert_retry_keeps_slot_and_changed_capsule_moves_last():
    """An unchanged retry keeps its index; a real update is the newest event."""
    from memory.recent import _compute_review_capacity, _merge_theater_episode_summary
    from utils.llm_client import HumanMessage, messages_to_dict

    paused = _wire_theater_capsule(
        "story_a", "session_a", "Paused mid-scene.",
        episode_status="paused", ending_summary="",
    )
    history, _ = _merge_theater_episode_summary(
        [HumanMessage(content="before")], paused,
    )
    history, _ = _merge_theater_episode_summary(
        history, _wire_theater_capsule("story_b", "session_b", "Story B ended."),
    )
    history = history + [HumanMessage(content="after one"), HumanMessage(content="after two")]
    before = messages_to_dict(history)
    snapshot = list(history)

    retried, _ = _merge_theater_episode_summary(history, paused)

    assert messages_to_dict(retried) == before
    capacity, cutoff = _compute_review_capacity(snapshot, retried)
    assert (capacity, cutoff) == (len(snapshot), len(snapshot) - 1)

    completed, stored = _merge_theater_episode_summary(
        history, _wire_theater_capsule("story_a", "session_a", "Story A ended."),
    )
    assert completed[-1] is stored
    assert [message.content for message in completed[:-1]] == [
        message.content for message in history if message is not history[1]
    ]


@pytest.mark.unit
def test_time_index_reconcile_migrates_legacy_theater_rows_atomically(tmp_path, monkeypatch):
    """时间索引重建应删除旧剧场全文，同时保留普通对话。"""  # noqa: DOCSTRING_CJK

    from datetime import datetime

    from sqlalchemy import create_engine, text

    from config import TIME_ORIGINAL_TABLE_NAME
    from memory.timeindex import TimeIndexedMemory
    from utils.llm_client import AIMessage, HumanMessage, SystemMessage
    from utils.llm_client.history import SQLChatMessageHistory

    db_path = tmp_path / "time_indexed.db"
    connection_string = f"sqlite:///{db_path}"
    normal = HumanMessage(content="普通对话")
    legacy = AIMessage(content="旧版完整演绎正文", metadata={
        "source": "theater_numeric_v2",
        "story_id": "story_legacy",
        "session_id": "legacy_session",
    })
    unchanged = SystemMessage(content="未变化的旧剧本摘要", metadata={
        "source": "theater_numeric_v2",
        "memory_tier": "episode_summary",
        "message_kind": "episode_summary",
        "story_id": "story_unchanged",
        "session_id": "unchanged_session",
    })
    SQLChatMessageHistory(
        connection_string=connection_string,
        session_id="normal_event",
        table_name=TIME_ORIGINAL_TABLE_NAME,
    ).add_message(normal)
    SQLChatMessageHistory(
        connection_string=connection_string,
        session_id="legacy_event",
        table_name=TIME_ORIGINAL_TABLE_NAME,
    ).add_message(legacy)
    SQLChatMessageHistory(
        connection_string=connection_string,
        session_id="theater-story-unchanged",
        table_name=TIME_ORIGINAL_TABLE_NAME,
    ).add_message(unchanged)
    with create_engine(connection_string).begin() as connection:
        connection.execute(text(
            f"ALTER TABLE {TIME_ORIGINAL_TABLE_NAME} ADD COLUMN timestamp DATETIME"
        ))
        connection.execute(
            text(
                f"UPDATE {TIME_ORIGINAL_TABLE_NAME} SET timestamp = :timestamp "
                "WHERE session_id = :session_id"
            ),
            {
                "timestamp": datetime(2020, 1, 2, 3, 4, 5),
                "session_id": "theater-story-unchanged",
            },
        )

    manager = TimeIndexedMemory(recent_history_manager=None)
    manager.engines["测试角色"] = create_engine(connection_string)
    manager.db_paths["测试角色"] = str(db_path)
    monkeypatch.setattr(manager, "_assert_timeindex_writable", lambda _name: None)
    monkeypatch.setattr(manager, "_ensure_engine_exists", lambda *_args, **_kwargs: True)
    summary = SystemMessage(content="有界摘要", metadata={
        "source": "theater_numeric_v2",
        "memory_tier": "episode_summary",
        "message_kind": "episode_summary",
        "story_id": "story_legacy",
        "session_id": "new_session",
    })

    result = manager.reconcile_theater_conversations(
        {
            "story_legacy": ("theater-story-stable", [summary]),
            "story_unchanged": ("theater-story-unchanged", [unchanged]),
        },
        "测试角色",
        timestamp=datetime(2030, 5, 6, 7, 8, 9),
    )

    with manager.engines["测试角色"].connect() as connection:
        rows = connection.execute(text(
            f"SELECT session_id, message, timestamp FROM {TIME_ORIGINAL_TABLE_NAME} ORDER BY id"
        )).fetchall()
    assert result == {"removed": 2, "stored": 2}
    assert [row[0] for row in rows] == [
        "normal_event",
        "theater-story-stable",
        "theater-story-unchanged",
    ]
    assert "普通对话" in rows[0][1]
    assert "有界摘要" in rows[1][1]
    assert str(rows[1][2]).startswith("2030-05-06 07:08:09")
    assert str(rows[2][2]).startswith("2020-01-02 03:04:05")


def test_time_index_reconcile_parses_each_candidate_row_once(tmp_path, monkeypatch):
    """Reconcile holds the SQLite write lock; each marker-matched row is
    deserialized exactly once to both classify and canonicalize it."""
    import json as real_json
    from datetime import datetime

    from sqlalchemy import create_engine, text

    from config import TIME_ORIGINAL_TABLE_NAME
    from memory import timeindex
    from memory.timeindex import TimeIndexedMemory
    from utils.llm_client import HumanMessage, SystemMessage
    from utils.llm_client.history import SQLChatMessageHistory

    db_path = tmp_path / "time_indexed.db"
    connection_string = f"sqlite:///{db_path}"

    def add(session_id, message):
        SQLChatMessageHistory(
            connection_string=connection_string,
            session_id=session_id,
            table_name=TIME_ORIGINAL_TABLE_NAME,
        ).add_message(message)

    def capsule(story, text_):
        return SystemMessage(content=text_, metadata={
            "source": "theater_numeric_v2",
            "memory_tier": "episode_summary",
            "story_id": story,
            "session_id": f"{story}_session",
        })

    add("plain", HumanMessage(content="普通对话"))
    # Mentions the marker, so SQL selects it, but it is not a theater message.
    add("mention", HumanMessage(content="I like theater_numeric_v2 a lot"))
    add("theater-story-a", capsule("a", "A 的旧摘要"))
    add("theater-story-b", capsule("b", "B 的摘要"))
    with create_engine(connection_string).begin() as connection:
        connection.execute(text(
            f"ALTER TABLE {TIME_ORIGINAL_TABLE_NAME} ADD COLUMN timestamp DATETIME"
        ))
        # Malformed row carrying the marker: parse fails, so it is kept.
        connection.execute(
            text(
                f"INSERT INTO {TIME_ORIGINAL_TABLE_NAME} (session_id, message) "
                "VALUES ('broken', '{theater_numeric_v2')"
            )
        )

    manager = TimeIndexedMemory(recent_history_manager=None)
    manager.engines["测试角色"] = create_engine(connection_string)
    manager.db_paths["测试角色"] = str(db_path)
    monkeypatch.setattr(manager, "_assert_timeindex_writable", lambda _name: None)
    monkeypatch.setattr(manager, "_ensure_engine_exists", lambda *_args, **_kwargs: True)

    loads_calls = []
    from_dict_calls = []

    class _CountingJson:
        def __getattr__(self, name):
            return getattr(real_json, name)

        @staticmethod
        def loads(value, *args, **kwargs):
            loads_calls.append(value)
            return real_json.loads(value, *args, **kwargs)

    real_from_dict = timeindex.messages_from_dict

    def counting_from_dict(dicts):
        from_dict_calls.append(dicts)
        return real_from_dict(dicts)

    monkeypatch.setattr(timeindex, "json", _CountingJson())
    monkeypatch.setattr(timeindex, "messages_from_dict", counting_from_dict)

    result = manager.reconcile_theater_conversations(
        {
            "a": ("theater-story-a", [capsule("a", "A 的新摘要")]),
            "b": ("theater-story-b", [capsule("b", "B 的摘要")]),
        },
        "测试角色",
        timestamp=datetime(2030, 5, 6, 7, 8, 9),
    )

    # Four marker-matched rows (mention, a, b, broken): one parse each.
    assert len(loads_calls) == 4
    assert len(from_dict_calls) == 3  # the malformed row never reaches from_dict
    assert result == {"removed": 2, "stored": 2}
    with manager.engines["测试角色"].connect() as connection:
        session_ids = [row[0] for row in connection.execute(text(
            f"SELECT session_id FROM {TIME_ORIGINAL_TABLE_NAME} ORDER BY id"
        )).fetchall()]
    assert session_ids == [
        "plain", "mention", "broken", "theater-story-a", "theater-story-b",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_rejects_idempotency_key_on_ordinary_batch():
    """Only theater episode writes dedupe by key; an ordinary batch must not
    accept one and then append a retry twice."""
    from app import memory_server

    fake_time_manager = MagicMock()
    fake_time_manager.astore_conversation = AsyncMock()
    fake_recent_history_manager = MagicMock()
    fake_recent_history_manager.update_history = AsyncMock()
    fake_spawn_outbox = AsyncMock()
    request = memory_server.HistoryRequest(
        input_history=_build_history_request_payload([
            {"role": "human", "content": "你好"},
        ]),
        idempotency_key="ordinary-retry",
    )

    with patch.object(memory_server.runtime, "time_manager", fake_time_manager), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent_history_manager), \
         patch.object(memory_server.post_turn, "_spawn_outbox_post_turn_signals", fake_spawn_outbox), \
         patch.object(memory_server.gates, "_aclear_review_clean", AsyncMock()):
        result = await memory_server.cache_conversation(request, "测试角色")

    assert result == {
        "status": "error",
        "message": "idempotency_key_requires_theater_episode",
    }
    fake_recent_history_manager.update_history.assert_not_awaited()
    fake_time_manager.astore_conversation.assert_not_awaited()
    fake_spawn_outbox.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    "endpoint_name",
    [
        "cache_conversation",
        "process_conversation",
        "process_conversation_for_renew",
    ],
)
async def test_stale_recent_identity_aborts_downstream_persistence(endpoint_name):
    """A stale recent append must not reach time-indexed or outbox storage."""
    from app import memory_server
    from utils.recent_file import RecentFileDeletedError

    fake_config = MagicMock()
    fake_config.aload_characters = AsyncMock(return_value={"猫娘": {"测试角色": {}}})
    fake_recent_history_manager = MagicMock()
    fake_recent_history_manager.update_history = AsyncMock(
        side_effect=RecentFileDeletedError("identity replaced")
    )
    fake_time_manager = MagicMock()
    fake_time_manager.astore_conversation = AsyncMock(return_value=None)
    fake_spawn_outbox = AsyncMock(return_value=None)
    payload = _build_history_request_payload([
        {"role": "human", "content": "stale turn"},
    ])
    request = memory_server.HistoryRequest(input_history=payload)

    with patch.object(memory_server.runtime, "_config_manager", fake_config), \
         patch.object(memory_server.runtime, "embedding_warmup_worker", None), \
         patch.object(memory_server.runtime, "time_manager", fake_time_manager), \
         patch.object(
             memory_server.runtime,
             "recent_history_manager",
             fake_recent_history_manager,
         ), patch.object(
             memory_server.post_turn,
             "_spawn_outbox_post_turn_signals",
             fake_spawn_outbox,
         ), patch.object(
             memory_server.gates,
             "_aclear_review_clean",
             AsyncMock(return_value=None),
         ):
        result = await getattr(memory_server, endpoint_name)(request, "测试角色")

    assert result["status"] == "error"
    fake_time_manager.astore_conversation.assert_not_awaited()
    fake_spawn_outbox.assert_not_awaited()


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
    """  # noqa: DOCSTRING_CJK
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
    request = memory_server.HistoryRequest(input_history=payload, language="zh-CN")

    with patch.object(memory_server.runtime, "time_manager", fake_time_manager), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent_history_manager), \
         patch.object(memory_server.post_turn, "_spawn_outbox_post_turn_signals", fake_spawn_outbox), \
         patch.object(memory_server.gates, "_aclear_review_clean", AsyncMock(return_value=None)):
        await memory_server.cache_conversation(request, "测试角色")

    fake_spawn_outbox.assert_awaited_once()
    spawn_args = fake_spawn_outbox.await_args
    assert spawn_args.args[0] == "测试角色"
    assert len(spawn_args.args[1]) == 2
    assert spawn_args.kwargs["language"] == "zh-CN"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_post_turn_signals_skips_stage1_when_powerful_memory_on():
    """powerful_memory ON 模式：Stage-1 per-turn fact_extract 已按 RFC §3.4.3  # noqa: DOCSTRING_CJK
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
    """powerful_memory OFF 模式：``_periodic_signal_extraction_loop`` 整段停  # noqa: DOCSTRING_CJK
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
async def test_run_post_turn_signals_excludes_theater_from_reality_signals():
    """剧场批次只做持久化，不参与事实、反馈、复读或人格信号。"""  # noqa: DOCSTRING_CJK
    from app import memory_server
    from utils.llm_client import AIMessage, HumanMessage

    metadata = {"source": "theater_numeric_v2", "session_id": "theater_session"}
    payload_messages = [
        HumanMessage(content="我在虚构剧情里住 302。", metadata=metadata),
        AIMessage(content="这是我们的剧本住处。", metadata=metadata),
    ]
    fake_fact_store = MagicMock()
    fake_fact_store.extract_facts = AsyncMock(return_value=[])
    fake_persona_manager = MagicMock()
    fake_persona_manager.arecord_mentions = AsyncMock(return_value=None)
    fake_reflection_engine = MagicMock()
    fake_reflection_engine.arecord_mentions = AsyncMock(return_value=None)
    fake_reflection_engine.aload_surfaced = AsyncMock(return_value=[{"feedback": None}])
    fake_reflection_engine.check_feedback = AsyncMock(return_value=[])
    record_turn = MagicMock(return_value=None)

    with patch.object(memory_server.runtime, "fact_store", fake_fact_store), \
         patch.object(memory_server.runtime, "persona_manager", fake_persona_manager), \
         patch.object(memory_server.runtime, "reflection_engine", fake_reflection_engine), \
         patch.object(memory_server.signal_extraction, "_signal_check_record_turn", record_turn), \
         patch.object(memory_server.gates, "_ais_powerful_memory_enabled", AsyncMock(return_value=False)):
        await memory_server._run_post_turn_signals(payload_messages, "测试角色")

    fake_fact_store.extract_facts.assert_not_awaited()
    fake_persona_manager.arecord_mentions.assert_not_awaited()
    fake_reflection_engine.arecord_mentions.assert_not_awaited()
    fake_reflection_engine.check_feedback.assert_not_awaited()
    record_turn.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_post_turn_signals_off_mode_keeps_stage1_for_ai_only_ordinary_batch():
    """OFF mode: an AI-only ordinary batch (proactive message, no user reply)
    still runs per-turn Stage-1 like before theater existed; theater messages in
    the same batch are filtered out of the extraction input.
    """
    from app import memory_server
    from utils.llm_client import AIMessage

    theater_metadata = {"source": "theater_numeric_v2", "session_id": "theater_session"}
    ordinary = AIMessage(content="早上好呀，今天也要元气满满哦")
    payload_messages = [
        AIMessage(content="这是我们的剧本住处。", metadata=theater_metadata),
        ordinary,
    ]
    fake_fact_store = MagicMock()
    fake_fact_store.extract_facts = AsyncMock(return_value=[])
    fake_persona_manager = MagicMock()
    fake_persona_manager.arecord_mentions = AsyncMock(return_value=None)
    fake_reflection_engine = MagicMock()
    fake_reflection_engine.arecord_mentions = AsyncMock(return_value=None)
    fake_reflection_engine.aload_surfaced = AsyncMock(return_value=[])
    record_turn = MagicMock(return_value=None)

    with patch.object(memory_server.runtime, "fact_store", fake_fact_store), \
         patch.object(memory_server.runtime, "persona_manager", fake_persona_manager), \
         patch.object(memory_server.runtime, "reflection_engine", fake_reflection_engine), \
         patch.object(memory_server.signal_extraction, "_signal_check_record_turn", record_turn), \
         patch.object(memory_server.gates, "_ais_powerful_memory_enabled", AsyncMock(return_value=False)):
        await memory_server._run_post_turn_signals(payload_messages, "测试角色")

    fake_fact_store.extract_facts.assert_awaited_once_with([ordinary], "测试角色")
    # No user utterance: the signal-extraction turn counter stays untouched.
    record_turn.assert_not_called()


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
    """  # noqa: DOCSTRING_CJK
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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_settle_empty_payload_persists_explicit_locale():
    from app import memory_server

    fake_recent_history_manager = MagicMock()
    fake_recent_history_manager.update_history = AsyncMock(return_value=None)
    fake_spawn_outbox = AsyncMock(return_value=None)
    request = memory_server.HistoryRequest(
        input_history=json.dumps([]),
        language="zh-TW",
    )

    with patch.object(
        memory_server.runtime,
        "recent_history_manager",
        fake_recent_history_manager,
    ), patch.object(
        memory_server.post_turn,
        "_spawn_outbox_post_turn_signals",
        fake_spawn_outbox,
    ), patch.object(
        memory_server.review,
        "maybe_spawn_review",
        AsyncMock(return_value=None),
    ), patch.object(
        memory_server.locale_state,
        "allocate_character_prompt_locale_order",
        MagicMock(return_value=314),
    ):
        result = await memory_server.settle_conversation(request, "测试角色")

    assert result == {"status": "settled"}
    fake_spawn_outbox.assert_awaited_once_with(
        "测试角色",
        [],
        language="zh-TW",
        render_language=None,
        locale_admission_order=314,
    )


@pytest.mark.asyncio
async def test_theater_memory_list_exposes_only_latest_public_summaries():
    from app import memory_server
    from utils.llm_client import SystemMessage, HumanMessage

    metadata = {'source': 'theater_numeric_v2', 'story_id': 'deleted_story',
                'session_id': 'run', 'story_title': '星火之后', 'episode_summary': '旧摘要'}
    history = [HumanMessage(content='私人日常聊天'), SystemMessage(content='旧文本', metadata=metadata),
               SystemMessage(content='公开文本', metadata={**metadata, 'episode_summary': '重逢。', 'hidden_debug': '不能返回'})]
    with patch.object(memory_server.runtime._config_manager, 'aload_characters', AsyncMock(return_value={'猫娘': {'测试角色': {}}})), \
         patch.object(memory_server.runtime, 'recent_history_manager', MagicMock(aget_recent_history=AsyncMock(return_value=history))) as recent:
        from app.memory_server import routes
        result = await routes.list_theater_memory_stories('测试角色')
    assert result == {'ok': True, 'stories': [{'story_id': 'deleted_story', 'title': '星火之后', 'memory_summaries': ['重逢。']}]}
    recent.aget_recent_history.assert_awaited_once_with('测试角色')
