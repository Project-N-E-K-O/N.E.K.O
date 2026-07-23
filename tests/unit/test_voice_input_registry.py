from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.voice_input import (
    BuiltinVoiceInputConsumer,
    VoiceInputConsumerCapabilities,
    VoiceInputHandleError,
    VoiceInputRegistry,
)
from main_logic.voice_input.plugin_api import PluginVoiceInputRegistrar
from main_logic.voice_turn.contracts import (
    VoiceIngressToken,
    VoicePartialEvent,
    VoiceTranscriptEvent,
    VoiceTurnToken,
)


pytestmark = pytest.mark.asyncio


def _turn(turn_id: int = 1) -> VoiceTurnToken:
    return VoiceTurnToken(
        ingress=VoiceIngressToken(
            session_epoch=7,
            connection_id="socket-a",
            lease_generation=3,
            route_generation=5,
            audio_generation=11,
        ),
        turn_id=turn_id,
    )


def _consumer(*, available: bool = True) -> SimpleNamespace:
    return SimpleNamespace(
        is_available=lambda: available,
        prepare_turn=AsyncMock(return_value=True),
        on_partial=AsyncMock(),
        on_final=AsyncMock(),
        on_cancelled=AsyncMock(),
    )


async def test_builtin_route_delivers_partial_and_final_once() -> None:
    registry = VoiceInputRegistry()
    chat = _consumer()
    registration = registry.register_builtin(
        BuiltinVoiceInputConsumer.CORE_CHAT,
        chat,
        capabilities=VoiceInputConsumerCapabilities(
            accepts_partial=True,
            accepts_final=True,
        ),
    )
    registry.activate(registration.handle)
    turn = _turn()

    assert registry.begin_utterance(turn) is True
    assert await registry.prepare_utterance() is True
    assert (
        await registry.dispatch_partial(VoicePartialEvent(text="hel", session_epoch=7))
        is True
    )
    event = VoiceTranscriptEvent(turn_token=turn, provider="qwen", text="hello")
    assert await registry.dispatch_final(event) is True
    assert await registry.dispatch_final(event) is False

    chat.prepare_turn.assert_awaited_once_with(turn)
    chat.on_partial.assert_awaited_once()
    chat.on_final.assert_awaited_once_with(event)


async def test_switch_invalidates_pinned_utterance_without_fallback() -> None:
    registry = VoiceInputRegistry()
    chat = _consumer()
    game = _consumer()
    chat_registration = registry.register_builtin(
        BuiltinVoiceInputConsumer.CORE_CHAT,
        chat,
    )
    game_registration = registry.register_builtin(
        BuiltinVoiceInputConsumer.GAME,
        game,
    )
    registry.activate(game_registration.handle)
    turn = _turn()
    assert registry.begin_utterance(turn) is True
    assert await registry.prepare_utterance() is True

    registry.activate(chat_registration.handle)
    await registry.wait_idle()
    stale = VoiceTranscriptEvent(turn_token=turn, provider="qwen", text="play")

    assert await registry.dispatch_final(stale) is False
    game.on_cancelled.assert_awaited_once_with(turn, "consumer_switched")
    game.on_final.assert_not_awaited()
    chat.on_final.assert_not_awaited()


async def test_closed_registration_rejects_stale_handle_and_final() -> None:
    registry = VoiceInputRegistry()
    game = _consumer()
    registration = registry.register_builtin(
        BuiltinVoiceInputConsumer.GAME,
        game,
    )
    registry.activate(registration.handle)
    turn = _turn()
    assert registry.begin_utterance(turn) is True

    assert registration.close() is True
    await registry.wait_idle()

    with pytest.raises(VoiceInputHandleError, match="STALE"):
        registry.activate(registration.handle)
    assert (
        await registry.dispatch_final(
            VoiceTranscriptEvent(turn_token=turn, provider="qwen", text="stale")
        )
        is False
    )
    game.on_final.assert_not_awaited()


async def test_consumer_capability_blocks_partial_delivery() -> None:
    registry = VoiceInputRegistry()
    game = _consumer()
    registration = registry.register_builtin(
        BuiltinVoiceInputConsumer.GAME,
        game,
        capabilities=VoiceInputConsumerCapabilities(
            accepts_partial=False,
            accepts_final=True,
        ),
    )
    registry.activate(registration.handle)
    assert registry.begin_utterance(_turn()) is True

    assert (
        await registry.dispatch_partial(
            VoicePartialEvent(text="hidden", session_epoch=7)
        )
        is False
    )
    game.on_partial.assert_not_awaited()


async def test_fake_plugin_registers_through_namespaced_registrar() -> None:
    registry = VoiceInputRegistry()
    plugin = _consumer()
    registrar = registry.issue_plugin_registrar("study-companion")

    assert isinstance(registrar, PluginVoiceInputRegistrar)
    registration = registrar.register_consumer(
        plugin,
        capabilities=VoiceInputConsumerCapabilities(
            accepts_partial=True,
            accepts_final=True,
        ),
    )
    assert registration.handle.identity.namespace == "plugin"
    assert registration.handle.identity.name == "study-companion"

    registry.activate(registration.handle)
    turn = _turn()
    assert registry.begin_utterance(turn) is True
    assert await registry.prepare_utterance() is True
    event = VoiceTranscriptEvent(turn_token=turn, provider="soniox", text="note")
    assert await registry.dispatch_final(event) is True
    plugin.on_final.assert_awaited_once_with(event)


async def test_plugin_registrar_cannot_claim_builtin_namespace() -> None:
    registry = VoiceInputRegistry()

    with pytest.raises(ValueError, match="PLUGIN_ID_INVALID"):
        registry.issue_plugin_registrar("core_chat")
    with pytest.raises(ValueError, match="PLUGIN_ID_INVALID"):
        registry.issue_plugin_registrar("game")


async def test_unavailable_consumer_keeps_input_fail_closed() -> None:
    registry = VoiceInputRegistry()
    registration = registry.register_builtin(
        BuiltinVoiceInputConsumer.GAME,
        _consumer(available=False),
    )
    registry.activate(registration.handle)

    assert registry.active_accepts_input is False
    assert registry.begin_utterance(_turn()) is False
