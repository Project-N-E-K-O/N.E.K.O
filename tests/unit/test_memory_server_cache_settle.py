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
    from utils.llm_client import is_theater_memory_message

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
    }
    events = fake_time.areconcile_theater_conversations.await_args.args[0]
    assert set(events) == {"story_keep", "story_legacy_keep"}
    assert events["story_legacy_keep"][1] == legacy_remaining


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


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_idempotency_key_skips_duplicate_memory_batch():
    """稳定键重试只能写一次 recent、time-indexed 和后续信号。"""  # noqa: DOCSTRING_CJK

    from app import memory_server

    fake_time_manager = MagicMock()
    fake_time_manager.ahas_conversation_event = AsyncMock(
        side_effect=[False, False, True]
    )
    fake_time_manager.astore_conversation = AsyncMock(return_value=None)
    fake_recent_history_manager = MagicMock()
    fake_recent_history_manager.aget_recent_history = AsyncMock(return_value=[])
    fake_recent_history_manager.update_history = AsyncMock(return_value=None)
    fake_spawn_outbox = AsyncMock(return_value=None)
    request = memory_server.HistoryRequest(
        input_history=_build_history_request_payload([
            {"role": "human", "content": "把这次演绎记下来。"},
            {"role": "ai", "content": "我会记住的。"},
        ]),
        language="zh-CN",
        idempotency_key="theater_archive_stable",
    )

    with patch.object(memory_server.runtime, "time_manager", fake_time_manager), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent_history_manager), \
         patch.object(memory_server.post_turn, "_spawn_outbox_post_turn_signals", fake_spawn_outbox), \
         patch.object(memory_server.gates, "_aclear_review_clean", AsyncMock(return_value=None)):
        first = await memory_server.cache_conversation(request, "测试角色")
        replay = await memory_server.cache_conversation(request, "测试角色")

    assert first["status"] == "cached"
    assert replay["status"] == "already_cached"
    fake_recent_history_manager.update_history.assert_awaited_once()
    fake_time_manager.astore_conversation.assert_awaited_once()
    assert fake_time_manager.astore_conversation.await_args.args[0].startswith(
        "cache-idempotent-"
    )
    fake_spawn_outbox.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cache_idempotency_retry_does_not_repeat_recent_after_index_failure():
    """recent 已写成但 time-indexed 失败时，重试只能补索引。"""  # noqa: DOCSTRING_CJK

    from app import memory_server

    payload = _build_history_request_payload([
        {"role": "human", "content": "这是一次需要恢复的演绎。"},
        {"role": "ai", "content": "我已经先记进最近历史了。"},
    ])
    committed_batch = []
    fake_time_manager = MagicMock()
    fake_time_manager.ahas_conversation_event = AsyncMock(
        side_effect=[False, False, False, False]
    )
    fake_time_manager.astore_conversation = AsyncMock(
        side_effect=[RuntimeError("time-indexed unavailable"), None]
    )
    fake_recent_history_manager = MagicMock()
    fake_recent_history_manager.aget_recent_history = AsyncMock(return_value=committed_batch)
    fake_recent_history_manager.update_history = AsyncMock(
        side_effect=lambda messages, *args, **kwargs: committed_batch.extend(messages)
    )
    fake_spawn_outbox = AsyncMock(return_value=None)
    request = memory_server.HistoryRequest(
        input_history=payload,
        idempotency_key="theater_archive_partial_commit",
    )

    with patch.object(memory_server.runtime, "time_manager", fake_time_manager), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent_history_manager), \
         patch.object(memory_server.post_turn, "_spawn_outbox_post_turn_signals", fake_spawn_outbox), \
         patch.object(memory_server.gates, "_aclear_review_clean", AsyncMock(return_value=None)):
        first = await memory_server.cache_conversation(request, "测试角色")
        replay = await memory_server.cache_conversation(request, "测试角色")

    assert first["status"] == "error"
    assert replay["status"] == "cached"
    fake_recent_history_manager.update_history.assert_awaited_once()
    assert fake_time_manager.astore_conversation.await_count == 2
    fake_spawn_outbox.assert_awaited_once()


@pytest.mark.asyncio
async def test_identical_text_with_distinct_cache_events_is_preserved_in_both_stores():
    from app import memory_server
    recent = []
    fake_recent = MagicMock()
    fake_recent.aget_recent_history = AsyncMock(return_value=recent)
    fake_recent.update_history = AsyncMock(side_effect=lambda batch, *args, **kwargs: recent.extend(batch))
    fake_time = MagicMock()
    fake_time.ahas_conversation_event = AsyncMock(return_value=False)
    fake_time.astore_conversation = AsyncMock()
    payload = _build_history_request_payload([{"role": "human", "content": "你好"}])
    with patch.object(memory_server.runtime, "time_manager", fake_time), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent), \
         patch.object(memory_server.post_turn, "_spawn_outbox_post_turn_signals", AsyncMock()), \
         patch.object(memory_server.gates, "_aclear_review_clean", AsyncMock()):
        for key in ("first", "second"):
            result = await memory_server.cache_conversation(memory_server.HistoryRequest(
                input_history=payload, idempotency_key=key,
            ), "测试角色")
            assert result["status"] == "cached"
    assert [message.content for message in recent] == ["你好", "你好"]
    event_ids = [message.metadata["cache_event_id"] for message in recent]
    assert len(set(event_ids)) == 2
    assert [call.args[0] for call in fake_time.astore_conversation.await_args_list] == event_ids


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
