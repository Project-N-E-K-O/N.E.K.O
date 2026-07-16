"""Focused coverage for ordinary Minecraft player-chat ingestion."""
from __future__ import annotations

import asyncio
import json

import pytest

from plugin.plugins.game_agent_minecraft.client import (
    GameAgentClient,
    IngameChatMessage,
)
from plugin.plugins.game_agent_minecraft.service import GameAgentService


class _FrameStream:
    """Small async iterable used to drive ``GameAgentClient._listen``."""

    def __init__(self, frames: list[str]) -> None:
        self._frames = iter(frames)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            return next(self._frames)
        except StopIteration as exc:
            raise StopAsyncIteration from exc


class _FakeClient:
    def __init__(self) -> None:
        self.is_connected = True
        self.sent: list[str] = []

    async def send_task(self, task: str, *, task_id: str = "") -> bool:
        self.sent.append(task)
        return True


def _make_service(*, push_calls: list | None = None):
    captured = push_calls if push_calls is not None else []

    def fake_push(**kwargs):
        captured.append(kwargs)

    return GameAgentService(logger=None, push_message_fn=fake_push), captured


@pytest.mark.asyncio
async def test_client_dispatches_typed_ingame_chat_batch_once():
    received: list[tuple[IngameChatMessage, ...]] = []

    async def on_ingame_chat(messages: tuple[IngameChatMessage, ...]) -> None:
        received.append(messages)

    client = GameAgentClient("ws://example", on_ingame_chat=on_ingame_chat)
    client._ws = _FrameStream([
        json.dumps({"type": "future_unknown", "payload": "ignored"}),
        json.dumps({
            "type": "ingame_chat",
            "count": 5,
            "messages": [
                {"player": " Alice ", "text": " hello Neko "},
                {"player": "Bob", "text": "come explore"},
                {"player": "", "text": "missing player"},
                {"player": "Eve", "text": 123},
                "not-an-object",
            ],
            "text": "<Alice> hello Neko\n<Bob> come explore",
        }),
    ])

    await client._listen()

    assert received == [(
        IngameChatMessage(player="Alice", text="hello Neko"),
        IngameChatMessage(player="Bob", text="come explore"),
    )]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "messages",
    [
        None,
        [],
        {},
        [None],
        [{"player": "", "text": "hello"}],
        [{"player": "Alice", "text": ""}],
        [{"player": 7, "text": "hello"}],
    ],
)
async def test_client_drops_malformed_or_empty_ingame_chat_batch(messages):
    received = []

    async def on_ingame_chat(batch) -> None:
        received.append(batch)

    client = GameAgentClient("ws://example", on_ingame_chat=on_ingame_chat)
    client._ws = _FrameStream([
        json.dumps({"type": "ingame_chat", "messages": messages}),
    ])

    await client._listen()

    assert received == []


@pytest.mark.asyncio
async def test_service_wires_ingame_chat_callback_on_start(monkeypatch):
    from plugin.plugins.game_agent_minecraft import service as service_module

    captured = {}

    class _StartClient:
        is_connected = False

        def __init__(self, **kwargs):
            captured.update(kwargs)

        async def start(self):
            await asyncio.Event().wait()

        async def stop(self):
            return None

    monkeypatch.setattr(service_module, "GameAgentClient", _StartClient)
    service, _ = _make_service()

    await service.start()
    try:
        assert captured["on_ingame_chat"] == service._on_ingame_chat
    finally:
        await service.stop()


@pytest.mark.asyncio
async def test_service_pushes_one_non_admin_dialog_turn_without_echo():
    service, push_calls = _make_service()
    fake_client = _FakeClient()
    service._client = fake_client

    await service._on_ingame_chat((
        IngameChatMessage(player="Alice", text="hello Neko"),
        IngameChatMessage(player="Bob", text="come explore"),
    ))

    assert len(push_calls) == 1
    call = push_calls[0]
    assert call["source"] == "game_agent_minecraft"
    assert call["visibility"] == []
    assert call["ai_behavior"] == "respond"
    assert call["priority"] == 5
    assert call["priority"] < 9
    assert "coalesce_key" not in call
    assert call["parts"][0]["type"] == "text"
    body = call["parts"][0]["text"]
    assert "not a privileged @neko admin mission" in body
    assert '{"player":"Alice","text":"hello Neko"}' in body
    assert '{"player":"Bob","text":"come explore"}' in body
    assert body.count("hello Neko") == 1
    assert fake_client.sent == []


@pytest.mark.asyncio
async def test_service_ignores_empty_ingame_chat_batch():
    service, push_calls = _make_service()

    await service._on_ingame_chat(())

    assert push_calls == []
