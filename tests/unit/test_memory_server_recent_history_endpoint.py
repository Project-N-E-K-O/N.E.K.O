from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


@pytest.mark.unit
@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("月台尽头的猫灯", "小剧场《月台尽头的猫灯》"),
        ("《月台尽头的猫灯》", "小剧场《月台尽头的猫灯》"),
        ("『月台尽头的猫灯』", "小剧场『月台尽头的猫灯』"),
    ],
)
def test_theater_memory_title_marks_are_idempotent(title, expected):
    """无书名号时自动补齐，已有成对标题符号时不能再套一层。"""  # noqa: DOCSTRING_CJK
    from config.prompts.prompts_memory import get_theater_memory_context

    rendered = get_theater_memory_context(
        "zh-CN",
        name="小葵",
        master="哥哥",
        title=title,
        status="paused",
    )

    assert expected in rendered
    assert "《《" not in rendered
    assert "》》" not in rendered


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_recent_history_accepts_string_content():
    from app import memory_server

    fake_config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {"test_char": {}}}),
        aget_character_data=AsyncMock(return_value=(
            "master",
            None,
            None,
            None,
            {"human": "Master", "ai": "Catgirl", "system": "System"},
            None,
            None,
            None,
            None,
        )),
    )
    fake_recent = SimpleNamespace(
        aget_recent_history=AsyncMock(return_value=[
            SimpleNamespace(type="system", content="session note"),
            SimpleNamespace(type="human", content="plain user history"),
            SimpleNamespace(type="ai", content="plain ai history"),
        ])
    )

    with patch.object(memory_server.runtime, "_config_manager", fake_config), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent):
        result = await memory_server.get_recent_history("test_char")

    assert "session note" in result
    assert "Master | plain user history" in result
    assert "test_char | plain ai history" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_recent_history_keeps_text_part_content():
    from app import memory_server

    fake_config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {"test_char": {}}}),
        aget_character_data=AsyncMock(return_value=(
            "master",
            None,
            None,
            None,
            {"human": "Master", "ai": "Catgirl", "system": "System"},
            None,
            None,
            None,
            None,
        )),
    )
    fake_recent = SimpleNamespace(
        aget_recent_history=AsyncMock(return_value=[
            SimpleNamespace(
                type="human",
                content=[
                    {"type": "text", "text": "part one"},
                    {"type": "image_url", "image_url": "ignored"},
                    {"type": "text", "text": "part two"},
                ],
            ),
        ])
    )

    with patch.object(memory_server.runtime, "_config_manager", fake_config), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent):
        result = await memory_server.get_recent_history("test_char")

    assert "Master | part one\npart two" in result
    assert "ignored" not in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_recent_history_uses_type_as_unknown_speaker():
    from app import memory_server

    fake_config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {"test_char": {}}}),
        aget_character_data=AsyncMock(return_value=(
            "master",
            None,
            None,
            None,
            {"human": "Master", "ai": "Catgirl", "system": "System"},
            None,
            None,
            None,
            None,
        )),
    )
    fake_recent = SimpleNamespace(
        aget_recent_history=AsyncMock(return_value=[
            SimpleNamespace(type="tool", content="tool result"),
        ])
    )

    with patch.object(memory_server.runtime, "_config_manager", fake_config), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent):
        result = await memory_server.get_recent_history("test_char")

    assert "tool | tool result" in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_recent_history_renders_one_theater_episode_context():
    """日常上下文只渲染单集摘要，不展开完整剧场正文。"""  # noqa: DOCSTRING_CJK
    from app import memory_server
    from utils.llm_client import AIMessage, HumanMessage

    episode = {
        "source": "theater_numeric_v2",
        "session_id": "theater_session",
        "archive_from_revision": 1,
        "archive_through_revision": 1,
        "story_title": "雨夜合租",
        "episode_status": "paused",
    }
    fake_config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {"test_char": {}}}),
        aget_character_data=AsyncMock(return_value=(
            "哥哥",
            None,
            None,
            None,
            {"human": "哥哥", "ai": "Catgirl", "system": "System"},
            None,
            None,
            None,
            None,
        )),
    )
    fake_recent = SimpleNamespace(
        aget_recent_history=AsyncMock(return_value=[
            AIMessage(
                content="雨点敲在窗沿。\n\n（抬起头）你来了。",
                metadata={
                    **episode,
                    "parts": [
                        {"kind": "scene_narration", "phase": "opening", "text": "雨点敲在窗沿。"},
                        {"kind": "action", "phase": "opening", "text": "（抬起头）"},
                        {"kind": "dialogue", "phase": "opening", "text": "你来了。"},
                    ],
                },
            ),
            HumanMessage(content="把合同递过去。", metadata=episode),
        ])
    )

    with patch.object(memory_server.runtime, "_config_manager", fake_config), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent):
        result = await memory_server.get_recent_history("test_char", "zh")

    assert "共同演绎小剧场《雨夜合租》" in result
    assert "属于虚构剧情，不代表现实经历" in result
    assert "雨点敲在窗沿。" not in result
    assert "test_char | （抬起头）你来了。" not in result
    assert "哥哥 | 把合同递过去。" not in result
    assert "【旁白】" not in result
    assert "【转场】" not in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_recent_history_merges_incremental_theater_archives_by_session():
    """同 Session 的暂停与完成批次只显示一次，并采用最新完成状态。"""  # noqa: DOCSTRING_CJK

    from app import memory_server
    from utils.llm_client import AIMessage, SystemMessage

    shared = {
        "source": "theater_numeric_v2",
        "story_id": "story_rain",
        "session_id": "theater_session",
        "story_title": "《雨夜合租》",
    }
    fake_config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {"test_char": {}}}),
        aget_character_data=AsyncMock(return_value=(
            "哥哥", None, None, None,
            {"human": "哥哥", "ai": "Catgirl", "system": "System"},
            None, None, None, None,
        )),
    )
    fake_recent = SimpleNamespace(
        aget_recent_history=AsyncMock(return_value=[
            AIMessage(content="旧开场正文", metadata={
                **shared,
                "episode_status": "paused",
                "archive_from_revision": 1,
                "archive_through_revision": 3,
            }),
            SystemMessage(content="两人保住了共同的住处。", metadata={
                **shared,
                "memory_tier": "episode_summary",
                "message_kind": "episode_summary",
                "episode_status": "completed",
                "ending_title": "雨停之后",
                "episode_summary": "两人保住了共同的住处。",
                "run_index": 2,
                "story_run_count": 2,
                "ending_titles_seen": ["雨中相守", "雨停之后"],
                "archive_from_revision": 4,
                "archive_through_revision": 8,
            }),
        ]),
    )

    with patch.object(memory_server.runtime, "_config_manager", fake_config), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent):
        result = await memory_server.get_recent_history("test_char", "zh")

    assert result.count("共同演绎小剧场《雨夜合租》") == 1
    assert "第 2 次演绎" in result
    assert "共演绎这个剧本 2 次" in result
    assert "雨中相守、雨停之后" in result
    assert "达成结局《雨停之后》" in result
    assert "两人保住了共同的住处。" in result
    assert "剧情尚未结束时暂停" not in result
    assert "旧开场正文" not in result
