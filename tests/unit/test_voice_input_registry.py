from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.voice_input import (
    BuiltinVoiceInputConsumer,
    VoiceInputConsumerCapabilities,
    VoiceInputDispatchResult,
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


def _register_chat(
    registry: VoiceInputRegistry,
    consumer: SimpleNamespace,
):
    return registry.register_builtin(
        BuiltinVoiceInputConsumer.CORE_CHAT,
        consumer,
        capabilities=VoiceInputConsumerCapabilities(
            accepts_partial=True,
            accepts_final=True,
        ),
    )


async def test_builtin_route_delivers_partial_and_final_once() -> None:
    registry = VoiceInputRegistry()
    chat = _consumer()
    registration = _register_chat(registry, chat)
    registry.activate(registration.handle)
    turn = _turn()

    assert registry.begin_utterance(turn) is True
    assert await registry.prepare_utterance(turn) is True
    partial = VoicePartialEvent(turn_token=turn, text="hel")
    assert (
        await registry.dispatch_partial(partial)
        is VoiceInputDispatchResult.DELIVERED
    )
    event = VoiceTranscriptEvent(turn_token=turn, provider="qwen", text="hello")
    assert (
        await registry.dispatch_final(event)
        is VoiceInputDispatchResult.DELIVERED
    )
    assert (
        await registry.dispatch_final(event)
        is VoiceInputDispatchResult.REJECTED
    )

    chat.prepare_turn.assert_awaited_once_with(turn)
    chat.on_partial.assert_awaited_once_with(partial)
    chat.on_final.assert_awaited_once_with(event)


async def test_partial_routes_only_by_its_own_full_turn_token() -> None:
    registry = VoiceInputRegistry()
    chat = _consumer()
    registration = _register_chat(registry, chat)
    registry.activate(registration.handle)
    first = _turn(1)
    second = _turn(2)
    assert registry.begin_utterance(first)
    assert registry.begin_utterance(second)
    assert await registry.prepare_utterance(first)
    assert await registry.prepare_utterance(second)

    second_partial = VoicePartialEvent(turn_token=second, text="second")
    first_partial = VoicePartialEvent(turn_token=first, text="first")
    assert (
        await registry.dispatch_partial(second_partial)
        is VoiceInputDispatchResult.DELIVERED
    )
    assert (
        await registry.dispatch_partial(first_partial)
        is VoiceInputDispatchResult.DELIVERED
    )
    unknown = VoicePartialEvent(turn_token=_turn(3), text="unknown")
    assert (
        await registry.dispatch_partial(unknown)
        is VoiceInputDispatchResult.REJECTED
    )

    assert chat.on_partial.await_args_list == [
        ((second_partial,), {}),
        ((first_partial,), {}),
    ]


async def test_switch_invalidates_pinned_routes_without_fallback() -> None:
    registry = VoiceInputRegistry()
    chat = _consumer()
    game = _consumer()
    chat_registration = _register_chat(registry, chat)
    game_registration = registry.register_builtin(
        BuiltinVoiceInputConsumer.GAME,
        game,
    )
    registry.activate(game_registration.handle)
    turn = _turn()
    assert registry.begin_utterance(turn)
    assert await registry.prepare_utterance(turn)

    registry.activate(chat_registration.handle)
    await registry.wait_idle()
    stale = VoiceTranscriptEvent(turn_token=turn, provider="qwen", text="play")

    assert (
        await registry.dispatch_final(stale)
        is VoiceInputDispatchResult.REJECTED
    )
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
    assert registry.begin_utterance(turn)

    assert registration.close() is True
    assert registration.close() is False
    await registry.wait_idle()

    with pytest.raises(VoiceInputHandleError, match="STALE"):
        registry.activate(registration.handle)
    assert (
        await registry.dispatch_final(
            VoiceTranscriptEvent(
                turn_token=turn,
                provider="qwen",
                text="stale",
            )
        )
        is VoiceInputDispatchResult.REJECTED
    )
    game.on_cancelled.assert_awaited_once_with(
        turn,
        "consumer_unregistered",
    )
    game.on_final.assert_not_awaited()


async def test_foreign_and_forged_handles_are_rejected() -> None:
    registry = VoiceInputRegistry()
    other = VoiceInputRegistry()
    first = _register_chat(registry, _consumer())
    second = registry.register_builtin(
        BuiltinVoiceInputConsumer.GAME,
        _consumer(),
    )
    foreign = other.register_builtin(
        BuiltinVoiceInputConsumer.CORE_CHAT,
        _consumer(),
    )

    with pytest.raises(VoiceInputHandleError, match="FOREIGN"):
        registry.activate(foreign.handle)

    forged = replace(first.handle, identity=second.handle.identity)
    with pytest.raises(VoiceInputHandleError, match="STALE"):
        registry.activate(forged)


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
    turn = _turn()
    assert registry.begin_utterance(turn)

    assert (
        await registry.dispatch_partial(
            VoicePartialEvent(turn_token=turn, text="hidden")
        )
        is VoiceInputDispatchResult.REJECTED
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
    assert registry.begin_utterance(turn)
    assert await registry.prepare_utterance(turn)
    event = VoiceTranscriptEvent(
        turn_token=turn,
        provider="soniox",
        text="note",
    )
    assert (
        await registry.dispatch_final(event)
        is VoiceInputDispatchResult.DELIVERED
    )
    plugin.on_final.assert_awaited_once_with(event)


@pytest.mark.parametrize(
    "plugin_id",
    ("core_chat", "game", "", "spaces are invalid", "../escape"),
)
async def test_plugin_registrar_rejects_reserved_or_invalid_ids(
    plugin_id: str,
) -> None:
    registry = VoiceInputRegistry()

    with pytest.raises(ValueError, match="PLUGIN_ID_INVALID"):
        registry.issue_plugin_registrar(plugin_id)


async def test_unavailable_consumer_keeps_input_fail_closed() -> None:
    registry = VoiceInputRegistry()
    registration = registry.register_builtin(
        BuiltinVoiceInputConsumer.GAME,
        _consumer(available=False),
    )
    registry.activate(registration.handle)

    assert registry.active_accepts_input is False
    assert registry.begin_utterance(_turn()) is False


async def test_consumer_becoming_unavailable_before_prepare_consumes_route() -> None:
    registry = VoiceInputRegistry()
    game = _consumer()
    registration = registry.register_builtin(
        BuiltinVoiceInputConsumer.GAME,
        game,
    )
    registry.activate(registration.handle)
    turn = _turn()

    assert registry.begin_utterance(turn) is True
    game.is_available = lambda: False

    assert await registry.prepare_utterance(turn) is False
    game.prepare_turn.assert_not_awaited()
    game.on_cancelled.assert_awaited_once_with(turn, "consumer_unavailable")

    game.is_available = lambda: True
    assert registry.begin_utterance(turn) is True


async def test_availability_error_keeps_input_fail_closed() -> None:
    registry = VoiceInputRegistry()
    game = _consumer()
    game.is_available = lambda: (_ for _ in ()).throw(
        RuntimeError("availability failed")
    )
    registration = registry.register_builtin(
        BuiltinVoiceInputConsumer.GAME,
        game,
    )
    registry.activate(registration.handle)

    assert registry.active_accepts_input is False
    assert registry.begin_utterance(_turn()) is False


async def test_switch_during_prepare_rejects_old_route_once() -> None:
    registry = VoiceInputRegistry()
    game = _consumer()
    chat = _consumer()
    prepare_entered = asyncio.Event()
    release_prepare = asyncio.Event()
    callback_order: list[str] = []
    prepared_state = False

    async def slow_prepare(_token: VoiceTurnToken) -> bool:
        nonlocal prepared_state
        prepare_entered.set()
        await release_prepare.wait()
        prepared_state = True
        callback_order.append("prepare")
        return True

    async def cancel_after_prepare(
        _token: VoiceTurnToken,
        _reason: str,
    ) -> None:
        nonlocal prepared_state
        callback_order.append("cancel")
        assert prepared_state is True
        prepared_state = False

    game.prepare_turn.side_effect = slow_prepare
    game.on_cancelled.side_effect = cancel_after_prepare
    game_registration = registry.register_builtin(
        BuiltinVoiceInputConsumer.GAME,
        game,
    )
    chat_registration = _register_chat(registry, chat)
    registry.activate(game_registration.handle)
    turn = _turn()
    assert registry.begin_utterance(turn)

    prepare_task = asyncio.create_task(registry.prepare_utterance(turn))
    await prepare_entered.wait()
    registry.activate(chat_registration.handle)
    await asyncio.sleep(0)
    game.on_cancelled.assert_not_awaited()
    release_prepare.set()

    assert await prepare_task is False
    await registry.wait_idle()
    game.on_cancelled.assert_awaited_once_with(turn, "consumer_switched")
    assert callback_order == ["prepare", "cancel"]
    assert prepared_state is False
    chat.prepare_turn.assert_not_awaited()


@pytest.mark.parametrize("failure_mode", ("false", "error"))
async def test_prepare_failure_consumes_route_without_fallback(
    failure_mode: str,
) -> None:
    registry = VoiceInputRegistry()
    game = _consumer()
    if failure_mode == "false":
        game.prepare_turn.return_value = False
    else:
        game.prepare_turn.side_effect = RuntimeError("prepare failed")
    registration = registry.register_builtin(
        BuiltinVoiceInputConsumer.GAME,
        game,
    )
    registry.activate(registration.handle)
    turn = _turn()
    assert registry.begin_utterance(turn)

    assert await registry.prepare_utterance(turn) is False
    await registry.wait_idle()
    assert (
        await registry.dispatch_final(
            VoiceTranscriptEvent(
                turn_token=turn,
                provider="qwen",
                text="stale",
            )
        )
        is VoiceInputDispatchResult.REJECTED
    )
    game.on_cancelled.assert_awaited_once_with(turn, "prepare_rejected")


async def test_prepare_rejection_finishes_cancellation_before_same_token_retry() -> (
    None
):
    registry = VoiceInputRegistry()
    chat = _consumer()
    chat.prepare_turn.side_effect = [False, True]
    cancellation_started = asyncio.Event()
    release_cancellation = asyncio.Event()

    async def slow_cancel(_token: VoiceTurnToken, _reason: str) -> None:
        cancellation_started.set()
        await release_cancellation.wait()

    chat.on_cancelled.side_effect = slow_cancel
    registration = _register_chat(registry, chat)
    registry.activate(registration.handle)
    turn = _turn()
    assert registry.begin_utterance(turn)

    rejected = asyncio.create_task(registry.prepare_utterance(turn))
    await cancellation_started.wait()
    assert rejected.done() is False
    release_cancellation.set()
    assert await rejected is False

    assert registry.begin_utterance(turn) is True
    assert await registry.prepare_utterance(turn) is True
    event = VoiceTranscriptEvent(
        turn_token=turn,
        provider="qwen",
        text="retry",
    )
    assert (
        await registry.dispatch_final(event)
        is VoiceInputDispatchResult.DELIVERED
    )
    chat.on_final.assert_awaited_once_with(event)


async def test_empty_final_consumes_route_before_terminal_cancellation() -> None:
    registry = VoiceInputRegistry()
    chat = _consumer()
    registration = _register_chat(registry, chat)
    registry.activate(registration.handle)
    turn = _turn()
    assert registry.begin_utterance(turn)
    assert await registry.prepare_utterance(turn)
    callback_saw_consumed_route = False

    async def on_cancelled(token: VoiceTurnToken, reason: str) -> None:
        nonlocal callback_saw_consumed_route
        duplicate = VoiceTranscriptEvent(
            turn_token=token,
            provider="qwen",
            text="duplicate",
        )
        callback_saw_consumed_route = (
            await registry.dispatch_final(duplicate)
            is VoiceInputDispatchResult.REJECTED
        )
        assert reason == "empty_final"

    chat.on_cancelled.side_effect = on_cancelled
    empty = VoiceTranscriptEvent(
        turn_token=turn,
        provider="qwen",
        text=" \t ",
    )

    assert (
        await registry.dispatch_final(empty)
        is VoiceInputDispatchResult.EMPTY_CONSUMED
    )
    await registry.wait_idle()
    assert callback_saw_consumed_route is True
    chat.on_final.assert_not_awaited()
    chat.on_cancelled.assert_awaited_once_with(turn, "empty_final")
    assert (
        await registry.dispatch_final(empty)
        is VoiceInputDispatchResult.REJECTED
    )


async def test_final_callback_error_never_restores_consumed_route() -> None:
    registry = VoiceInputRegistry()
    chat = _consumer()
    chat.on_final.side_effect = RuntimeError("consumer failed")
    registration = _register_chat(registry, chat)
    registry.activate(registration.handle)
    turn = _turn()
    assert registry.begin_utterance(turn)
    event = VoiceTranscriptEvent(
        turn_token=turn,
        provider="qwen",
        text="hello",
    )

    assert (
        await registry.dispatch_final(event)
        is VoiceInputDispatchResult.CALLBACK_FAILED
    )
    assert (
        await registry.dispatch_final(event)
        is VoiceInputDispatchResult.REJECTED
    )
    chat.on_final.assert_awaited_once_with(event)


async def test_partial_callback_error_is_rejected_without_losing_route() -> None:
    registry = VoiceInputRegistry()
    chat = _consumer()
    chat.on_partial.side_effect = RuntimeError("consumer failed")
    registration = _register_chat(registry, chat)
    registry.activate(registration.handle)
    turn = _turn()
    assert registry.begin_utterance(turn)
    partial = VoicePartialEvent(turn_token=turn, text="hel")

    assert (
        await registry.dispatch_partial(partial)
        is VoiceInputDispatchResult.CALLBACK_FAILED
    )

    final = VoiceTranscriptEvent(
        turn_token=turn,
        provider="qwen",
        text="hello",
    )
    assert (
        await registry.dispatch_final(final)
        is VoiceInputDispatchResult.DELIVERED
    )


async def test_targeted_and_global_invalidation_cancel_each_route_once() -> None:
    registry = VoiceInputRegistry()
    chat = _consumer()
    registration = _register_chat(registry, chat)
    registry.activate(registration.handle)
    first = _turn(1)
    second = _turn(2)
    assert registry.begin_utterance(first)
    assert registry.begin_utterance(second)

    assert registry.invalidate_utterance(first, reason="pcm_hole") is True
    assert registry.invalidate_utterance(first, reason="duplicate") is False
    assert registry.invalidate_utterance(reason="route_swap") is True
    assert registry.invalidate_utterance(reason="duplicate") is False
    await registry.wait_idle()

    assert chat.on_cancelled.await_args_list == [
        ((first, "pcm_hole"), {}),
        ((second, "route_swap"), {}),
    ]


async def test_close_cancels_routes_and_invalidates_registrations() -> None:
    registry = VoiceInputRegistry()
    chat = _consumer()
    registration = _register_chat(registry, chat)
    registry.activate(registration.handle)
    turn = _turn()
    assert registry.begin_utterance(turn)

    await registry.close()
    await registry.close()

    assert registry.active_identity is None
    assert registry.active_accepts_input is False
    chat.on_cancelled.assert_awaited_once_with(turn, "registry_closed")
    with pytest.raises(VoiceInputHandleError):
        registry.activate(registration.handle)
