from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest


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


_COMMENT_A = "屏幕搭话 蓝色小车停在一棵大树旁边，树叶的影子落在了车顶上。"
_COMMENT_B = "屏幕搭话 远处的红色小车正在缓慢经过桥面，桥下的河水十分平静。"
_BODY_A = _COMMENT_A[len("屏幕搭话 "):]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_recent_history_cuts_screen_chains_before_rendering():
    """Renewal and restarts bring this history back as system-prompt text,
    past the offline client's request-view projection: the chain must be cut
    here, on the structured messages."""
    from app import memory_server

    fake_config = SimpleNamespace(
        aload_characters=AsyncMock(return_value={"猫娘": {"test_char": {}}}),
        aget_character_data=AsyncMock(return_value=(
            "master", None, None, None,
            {"human": "Master", "ai": "Catgirl", "system": "System"},
            None, None, None, None,
        )),
    )
    fake_recent = SimpleNamespace(aget_recent_history=AsyncMock(return_value=[
        SimpleNamespace(type="human", content="陪我聊聊"),
        SimpleNamespace(type="ai", content=_COMMENT_A + _COMMENT_B),
        SimpleNamespace(type="ai", content="普通的一句回复。"),
        SimpleNamespace(type="human", content="继续"),
        # A chain split over the replies that end the history: the new
        # session's user turn follows them, so they are one run.
        SimpleNamespace(type="ai", content=_COMMENT_A),
        # cross_server stores assistant turns as text-part lists.
        SimpleNamespace(type="ai", content=[{"type": "text", "text": _COMMENT_B}]),
    ]))

    with patch.object(memory_server.runtime, "_config_manager", fake_config), \
         patch.object(memory_server.runtime, "recent_history_manager", fake_recent):
        result = await memory_server.get_recent_history("test_char", "zh")

    assert "屏幕搭话" not in result and "红色小车" not in result
    assert result.count(f"test_char | {_BODY_A}") == 2
    assert "test_char | 普通的一句回复。" in result
    assert "Master | 陪我聊聊" in result


def test_new_dialog_renders_through_the_screen_guard():
    """_new_dialog needs the whole runtime to run; pin that it renders the
    recent history through the guarded helper."""
    import inspect
    from app.memory_server import routes

    source = inspect.getsource(routes._new_dialog)
    assert "_screen_guarded_recent_history(" in source
    assert "for i in await runtime.recent_history_manager.aget_recent_history" not in source
