import asyncio
from unittest.mock import AsyncMock
import pytest
from main_logic.asr_client import VoiceIdentityActivationResult
from main_logic.voice_input.activation import ActivationState

from tests.unit.asr_runtime._scenarios import (
    _wait_for_activation_output,
)

from tests.support.asr_fakes import (
    _CoreActivationFactory,
    _Runtime,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


async def test_native_idle_disconnect_reconnects_before_activation_replay() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    runtime.is_active = True
    runtime.session.instructions = "stay in character"
    runtime.session._connection_generation = 4
    delivered: list[bytes] = []
    old_listener_started = asyncio.Event()
    release_old_listener = asyncio.Event()

    async def old_listener() -> None:
        old_listener_started.set()
        await release_old_listener.wait()

    old_listener_task = asyncio.create_task(old_listener())
    await old_listener_started.wait()

    async def stream_audio(pcm16: bytes) -> None:
        delivered.append(pcm16)

    async def reconnect(_instructions: str, *, native_audio: bool) -> None:
        assert native_audio is True
        assert old_listener_task.done()
        runtime.session._connection_generation += 1

    runtime.session.stream_audio = AsyncMock(side_effect=stream_audio)
    runtime.session.connect = AsyncMock(side_effect=reconnect)
    runtime.session.close = AsyncMock()
    runtime.message_handler_task = old_listener_task
    runtime._restart_message_handler_after_session_reconnect = AsyncMock(
        return_value=True
    )
    factory = _CoreActivationFactory()
    await runtime.set_voice_session_activation_factory(
        factory,
        activation_generation="profile",
    )

    frames = [
        int(2_000 + sequence).to_bytes(2, "little", signed=True) * 1_600
        for sequence in range(15)
    ]
    await runtime._route_microphone_audio(frames[0], sample_rate_hz=16_000)
    await asyncio.sleep(0)
    generation = factory.runtimes[0].generation
    runtime.session_closed_by_server = True
    runtime._native_activation_idle_reconnect_identity = (generation, 4)

    for frame in frames[1:]:
        await runtime._route_microphone_audio(frame, sample_rate_hz=16_000)

    for _ in range(10):
        await asyncio.sleep(0)
    runtime.session.connect.assert_not_awaited()
    assert delivered == []
    release_old_listener.set()

    await _wait_for_activation_output(
        lambda: len(delivered),
        expected_count=len(frames),
    )

    runtime.session.connect.assert_awaited_once_with(
        "stay in character",
        native_audio=True,
    )
    runtime._restart_message_handler_after_session_reconnect.assert_awaited_once_with(
        runtime.session
    )
    assert runtime.session_closed_by_server is False
    assert runtime._native_activation_idle_reconnect_identity is None
    assert delivered == frames
    assert factory.scorers[0].calls == 1

    live_frame = b"\x01\x00" * 160
    await runtime._route_microphone_audio(live_frame, sample_rate_hz=16_000)
    await asyncio.sleep(0)
    assert delivered == [*frames, live_frame]
    assert runtime.session.connect.await_count == 1
    assert factory.scorers[0].calls == 1
    await runtime.set_voice_session_activation_factory(
        None,
        activation_generation="disabled",
    )


async def test_native_idle_reconnect_survives_activation_authority_replacement() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    runtime.is_active = True
    runtime.session.instructions = "stay in character"
    runtime.session._connection_generation = 12
    delivered: list[bytes] = []

    async def stream_audio(pcm16: bytes) -> None:
        delivered.append(pcm16)

    async def reconnect(_instructions: str, *, native_audio: bool) -> None:
        assert native_audio is True
        runtime.session._connection_generation += 1

    runtime.session.stream_audio = AsyncMock(side_effect=stream_audio)
    runtime.session.connect = AsyncMock(side_effect=reconnect)
    runtime.session.close = AsyncMock()
    runtime.message_handler_task = None
    runtime._restart_message_handler_after_session_reconnect = AsyncMock(
        return_value=True
    )

    retired_factory = _CoreActivationFactory()
    await runtime.set_voice_session_activation_factory(
        retired_factory,
        activation_generation="profile",
    )
    await runtime._route_microphone_audio(
        b"\xd0\x07" * 1_600,
        sample_rate_hz=16_000,
    )
    await asyncio.sleep(0)
    retired_generation = retired_factory.runtimes[0].generation
    runtime.session_closed_by_server = True
    runtime._native_activation_idle_reconnect_identity = (retired_generation, 12)

    replacement_factory = _CoreActivationFactory()
    replacement_factory.activation_generation = "replacement-profile"
    await runtime.set_voice_session_activation_factory(
        replacement_factory,
        activation_generation="replacement-profile",
    )
    replacement_generation = runtime._capture_voice_session_activation_generation()
    assert runtime._native_activation_idle_reconnect_identity == (
        replacement_generation,
        12,
    )

    frames = [b"\xd1\x07" * 1_600 for _ in range(15)]
    await runtime._route_microphone_audio(frames[0], sample_rate_hz=16_000)
    await asyncio.sleep(0)
    for frame in frames[1:]:
        await runtime._route_microphone_audio(frame, sample_rate_hz=16_000)
    for _ in range(100):
        if replacement_factory.scorers[0].calls:
            break
        await asyncio.sleep(0)
    assert replacement_factory.scorers[0].calls == 1
    await _wait_for_activation_output(
        lambda: len(delivered),
        expected_count=len(frames),
    )

    runtime.session.connect.assert_awaited_once_with(
        "stay in character",
        native_audio=True,
    )
    assert replacement_factory.scorers[0].calls == 1
    assert delivered == frames
    assert runtime.session_closed_by_server is False
    assert runtime._native_activation_idle_reconnect_identity is None
    await runtime.set_voice_session_activation_factory(
        None,
        activation_generation="disabled",
    )


async def test_disabling_activation_reconnects_idle_native_session_on_next_frame() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    runtime.is_active = True
    runtime.session.instructions = "stay in character"
    runtime.session._connection_generation = 18
    delivered: list[bytes] = []

    async def reconnect(_instructions: str, *, native_audio: bool) -> None:
        assert native_audio is True
        runtime.session._connection_generation += 1

    runtime.session.connect = AsyncMock(side_effect=reconnect)
    runtime.session.stream_audio = AsyncMock(
        side_effect=lambda pcm16: delivered.append(pcm16)
    )
    runtime.session.close = AsyncMock()
    runtime.message_handler_task = None
    runtime._restart_message_handler_after_session_reconnect = AsyncMock(
        return_value=True
    )
    factory = _CoreActivationFactory()
    await runtime.set_voice_session_activation_factory(
        factory,
        activation_generation="profile",
    )
    await runtime._route_microphone_audio(
        b"\xd0\x07" * 1_600,
        sample_rate_hz=16_000,
    )
    await asyncio.sleep(0)
    runtime.session_closed_by_server = True
    runtime._native_activation_idle_reconnect_identity = (
        factory.runtimes[0].generation,
        18,
    )

    assert (
        await runtime.set_voice_session_activation_factory(
            None,
            activation_generation="disabled",
        )
        is VoiceIdentityActivationResult.READY
    )
    disabled_generation = runtime._capture_voice_session_activation_generation()
    assert runtime._native_activation_idle_reconnect_identity == (
        disabled_generation,
        18,
    )

    live_frame = b"\x01\x00" * 160
    await runtime._route_microphone_audio(live_frame, sample_rate_hz=16_000)

    runtime.session.connect.assert_awaited_once_with(
        "stay in character",
        native_audio=True,
    )
    assert delivered == [live_frame]
    assert runtime.session_closed_by_server is False
    assert runtime._native_activation_idle_reconnect_identity is None


async def test_activation_authority_replacement_waits_for_native_reconnect() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    runtime.is_active = True
    runtime.session.instructions = "stay in character"
    runtime.session._connection_generation = 24
    connect_entered = asyncio.Event()
    release_connect = asyncio.Event()
    delivered: list[bytes] = []

    async def reconnect(_instructions: str, *, native_audio: bool) -> None:
        assert native_audio is True
        connect_entered.set()
        await release_connect.wait()
        runtime.session._connection_generation += 1

    runtime.session.connect = AsyncMock(side_effect=reconnect)
    runtime.session.stream_audio = AsyncMock(
        side_effect=lambda pcm16: delivered.append(pcm16)
    )
    runtime.session.close = AsyncMock()
    runtime.message_handler_task = None
    runtime._restart_message_handler_after_session_reconnect = AsyncMock(
        return_value=True
    )
    retired_factory = _CoreActivationFactory()
    await runtime.set_voice_session_activation_factory(
        retired_factory,
        activation_generation="profile",
    )
    await runtime._route_microphone_audio(
        b"\xd0\x07" * 1_600,
        sample_rate_hz=16_000,
    )
    await asyncio.sleep(0)
    retired_generation = retired_factory.runtimes[0].generation
    runtime.session_closed_by_server = True
    runtime._native_activation_idle_reconnect_identity = (retired_generation, 24)

    reconnect_task = asyncio.create_task(
        runtime._reconnect_native_voice_session_for_activation(
            retired_generation,
            runtime._capture_native_ingress_token(),
        )
    )
    await connect_entered.wait()

    replacement_factory = _CoreActivationFactory()
    replacement_factory.activation_generation = "replacement-profile"
    replacement_task = asyncio.create_task(
        runtime.set_voice_session_activation_factory(
            replacement_factory,
            activation_generation="replacement-profile",
        )
    )
    await asyncio.sleep(0)
    assert replacement_task.done() is False

    release_connect.set()
    assert await reconnect_task is True
    assert await replacement_task is VoiceIdentityActivationResult.READY
    assert runtime.session_closed_by_server is False
    assert runtime._native_activation_idle_reconnect_identity is None

    frames = [b"\xd1\x07" * 1_600 for _ in range(15)]
    await runtime._route_microphone_audio(frames[0], sample_rate_hz=16_000)
    await asyncio.sleep(0)
    for frame in frames[1:]:
        await runtime._route_microphone_audio(frame, sample_rate_hz=16_000)
    await _wait_for_activation_output(
        lambda: len(delivered),
        expected_count=len(frames),
    )

    assert runtime.session.connect.await_count == 1
    assert delivered == frames
    assert replacement_factory.scorers[0].calls == 1
    await runtime.set_voice_session_activation_factory(
        None,
        activation_generation="disabled",
    )


async def test_native_idle_reconnect_failure_keeps_replay_for_one_safe_retry() -> None:
    runtime = _Runtime()
    runtime._asr_route_mode = "native"
    runtime.is_active = True
    runtime.session.instructions = "stay in character"
    runtime.session._connection_generation = 9
    delivered: list[bytes] = []
    reconnect_attempts = 0

    async def stream_audio(pcm16: bytes) -> None:
        if len(delivered) == len(frames):
            # Keep the final live frame pending for one scheduling turn. A wait
            # for only the 15 replay frames would return before delivery ends.
            await asyncio.sleep(0)
        delivered.append(pcm16)

    async def reconnect(_instructions: str, *, native_audio: bool) -> None:
        nonlocal reconnect_attempts
        assert native_audio is True
        reconnect_attempts += 1
        if reconnect_attempts == 1:
            raise RuntimeError("still offline")
        runtime.session._connection_generation += 1

    runtime.session.stream_audio = AsyncMock(side_effect=stream_audio)
    runtime.session.connect = AsyncMock(side_effect=reconnect)
    runtime.session.close = AsyncMock()
    runtime.message_handler_task = None
    runtime._restart_message_handler_after_session_reconnect = AsyncMock(
        return_value=True
    )
    factory = _CoreActivationFactory()
    await runtime.set_voice_session_activation_factory(
        factory,
        activation_generation="profile",
    )

    frames = [b"\xd0\x07" * 1_600 for _ in range(15)]
    await runtime._route_microphone_audio(frames[0], sample_rate_hz=16_000)
    await asyncio.sleep(0)
    generation = factory.runtimes[0].generation
    runtime.session_closed_by_server = True
    runtime._native_activation_idle_reconnect_identity = (generation, 9)
    for frame in frames[1:]:
        await runtime._route_microphone_audio(frame, sample_rate_hz=16_000)

    for _ in range(100):
        if (
            runtime.session.close.await_count
            and factory.runtimes[0]._output_task is None
        ):
            break
        await asyncio.sleep(0)
    assert reconnect_attempts == 1
    assert delivered == []
    assert factory.runtimes[0].state is ActivationState.REPLAYING
    runtime.session.close.assert_awaited_once_with()

    retry_frame = b"\xd1\x07" * 1_600
    await runtime._route_microphone_audio(retry_frame, sample_rate_hz=16_000)
    await _wait_for_activation_output(
        lambda: len(delivered),
        expected_count=len(frames) + 1,
    )

    assert reconnect_attempts == 2
    assert delivered == [*frames, retry_frame]
    assert factory.runtimes[0].state is ActivationState.ACTIVE
    assert factory.scorers[0].calls == 1
    await runtime.set_voice_session_activation_factory(
        None,
        activation_generation="disabled",
    )
