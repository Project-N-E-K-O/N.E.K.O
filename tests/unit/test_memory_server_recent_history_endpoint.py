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

    with patch.object(memory_server, "_config_manager", fake_config), \
         patch.object(memory_server, "recent_history_manager", fake_recent):
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

    with patch.object(memory_server, "_config_manager", fake_config), \
         patch.object(memory_server, "recent_history_manager", fake_recent):
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

    with patch.object(memory_server, "_config_manager", fake_config), \
         patch.object(memory_server, "recent_history_manager", fake_recent):
        result = await memory_server.get_recent_history("test_char")

    assert "tool | tool result" in result
