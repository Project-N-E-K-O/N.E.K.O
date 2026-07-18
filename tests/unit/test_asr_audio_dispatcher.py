from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from main_logic.asr_client.lifecycle_contracts import VoiceIngressToken, VoiceTurnToken
from main_logic.core.asr_audio_dispatcher import AsrAudioDispatcher


def _turn(turn_id: int = 1) -> VoiceTurnToken:
    return VoiceTurnToken(VoiceIngressToken(1, "socket", 1, 1, 1), turn_id)


async def test_activate_audio_and_seal_are_strictly_ordered() -> None:
    calls: list[tuple[str, bytes | None]] = []
    session = type("Session", (), {})()

    async def stream_audio(pcm16: bytes, *, sample_rate_hz: int) -> None:
        assert sample_rate_hz == 16_000
        calls.append(("audio", pcm16))

    async def seal() -> None:
        calls.append(("seal", None))

    session.stream_audio = stream_audio
    session.signal_user_activity_end = seal
    dispatcher = AsrAudioDispatcher(
        validator=lambda _token, ref: ref is session,
        on_wire_audio=AsyncMock(),
        on_failure=AsyncMock(),
    )
    turn = _turn()

    assert dispatcher.activate(turn, session, b"pre-roll")
    assert dispatcher.enqueue_audio(
        turn,
        session,
        b"realtime",
        sample_rate_hz=16_000,
        sequence_no=1,
    )
    assert dispatcher.seal(turn, session, after_sequence=1)
    await dispatcher.wait_idle()

    assert calls == [
        ("audio", b"pre-roll"),
        ("audio", b"realtime"),
        ("seal", None),
    ]
    await dispatcher.close()


async def test_abort_discards_queued_writes_before_they_start() -> None:
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    writes: list[bytes] = []
    session = type("Session", (), {})()

    async def stream_audio(pcm16: bytes, *, sample_rate_hz: int) -> None:
        del sample_rate_hz
        writes.append(pcm16)
        first_started.set()
        await release_first.wait()

    session.stream_audio = stream_audio
    session.signal_user_activity_end = AsyncMock()
    dispatcher = AsrAudioDispatcher(
        validator=lambda _token, ref: ref is session,
        on_wire_audio=AsyncMock(),
        on_failure=AsyncMock(),
    )
    turn = _turn()
    dispatcher.activate(turn, session, b"first!")
    dispatcher.enqueue_audio(
        turn,
        session,
        b"must-not-start",
        sample_rate_hz=16_000,
        sequence_no=1,
    )
    await asyncio.wait_for(first_started.wait(), 1)

    dispatcher.abort(turn)
    release_first.set()
    await dispatcher.wait_idle()

    assert writes == [b"first!"]
    session.signal_user_activity_end.assert_not_awaited()
    await dispatcher.close()


async def test_dispatcher_records_wire_sequence_and_abort_discards() -> None:
    release = asyncio.Event()
    started = asyncio.Event()
    session = type("Session", (), {})()

    async def stream_audio(_pcm16: bytes, *, sample_rate_hz: int) -> None:
        assert sample_rate_hz == 16_000
        started.set()
        await release.wait()

    session.stream_audio = stream_audio
    session.signal_user_activity_end = AsyncMock()
    dispatcher = AsrAudioDispatcher(
        validator=lambda _token, ref: ref is session,
        on_wire_audio=AsyncMock(),
        on_failure=AsyncMock(),
    )
    turn = _turn()
    dispatcher.activate(turn, session, b"first!")
    dispatcher.enqueue_audio(
        turn,
        session,
        b"second",
        sample_rate_hz=16_000,
        sequence_no=1,
    )
    await asyncio.wait_for(started.wait(), 1)

    dispatcher.abort(turn)
    release.set()
    await dispatcher.wait_idle()

    assert dispatcher.provider_wire_sequence == 1
    assert dispatcher.asr_abort_discarded_command_count >= 1
    assert dispatcher.asr_audio_command_queue_ms >= 0
    await dispatcher.close()
