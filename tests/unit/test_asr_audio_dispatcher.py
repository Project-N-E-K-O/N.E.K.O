from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from main_logic.asr_client.lifecycle import VoiceIngressToken, VoiceTurnToken
from main_logic.asr_client.audio import AsrAudioDispatcher


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


async def test_abort_invalidates_inflight_success_side_effects() -> None:
    release = asyncio.Event()
    started = asyncio.Event()
    session = type("Session", (), {})()

    async def stream_audio(_pcm16: bytes, *, sample_rate_hz: int) -> None:
        assert sample_rate_hz == 16_000
        started.set()
        await release.wait()

    session.stream_audio = stream_audio
    session.signal_user_activity_end = AsyncMock()
    on_wire_audio = AsyncMock()
    dispatcher = AsrAudioDispatcher(
        validator=lambda _token, ref: ref is session,
        on_wire_audio=on_wire_audio,
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

    assert dispatcher.provider_wire_sequence == 0
    on_wire_audio.assert_not_awaited()
    assert dispatcher.asr_abort_discarded_command_count >= 1
    assert dispatcher.asr_audio_command_queue_ms >= 0
    await dispatcher.close()


async def test_current_success_records_wire_side_effects_once() -> None:
    session = type("Session", (), {})()
    session.stream_audio = AsyncMock()
    session.signal_user_activity_end = AsyncMock()
    on_wire_audio = AsyncMock()
    dispatcher = AsrAudioDispatcher(
        validator=lambda _token, ref: ref is session,
        on_wire_audio=on_wire_audio,
        on_failure=AsyncMock(),
    )
    turn = _turn()

    assert dispatcher.activate(turn, session, b"\x01\x00")
    await dispatcher.wait_idle()

    assert dispatcher.provider_wire_sequence == 1
    on_wire_audio.assert_awaited_once_with(turn, session, 2)
    await dispatcher.close()


async def test_abort_suppresses_failure_from_inflight_audio_command() -> None:
    started = asyncio.Event()
    release = asyncio.Event()
    session = type("Session", (), {})()

    async def stream_audio(_pcm16: bytes, *, sample_rate_hz: int) -> None:
        assert sample_rate_hz == 16_000
        started.set()
        await release.wait()
        raise RuntimeError("session closed during intentional abort")

    session.stream_audio = stream_audio
    session.signal_user_activity_end = AsyncMock()
    on_failure = AsyncMock()
    dispatcher = AsrAudioDispatcher(
        validator=lambda _token, ref: ref is session,
        on_wire_audio=AsyncMock(),
        on_failure=on_failure,
    )
    turn = _turn()
    assert dispatcher.activate(turn, session, b"\x01\x00")
    await asyncio.wait_for(started.wait(), 1)

    dispatcher.abort(turn)
    release.set()
    await dispatcher.wait_idle()

    on_failure.assert_not_awaited()
    await dispatcher.close()


async def test_current_audio_command_failure_still_fails_closed() -> None:
    session = type("Session", (), {})()

    async def stream_audio(_pcm16: bytes, *, sample_rate_hz: int) -> None:
        assert sample_rate_hz == 16_000
        raise RuntimeError("current provider write failed")

    session.stream_audio = stream_audio
    session.signal_user_activity_end = AsyncMock()
    on_failure = AsyncMock()
    dispatcher = AsrAudioDispatcher(
        validator=lambda _token, ref: ref is session,
        on_wire_audio=AsyncMock(),
        on_failure=on_failure,
    )
    turn = _turn()
    assert dispatcher.activate(turn, session, b"\x01\x00")
    await dispatcher.wait_idle()

    on_failure.assert_awaited_once()
    await dispatcher.close()


async def test_backpressure_failure_task_is_retained_until_completion() -> None:
    failure_started = asyncio.Event()
    release_failure = asyncio.Event()
    session = type("Session", (), {})()

    async def on_failure(
        _turn_token: VoiceTurnToken, _error: BaseException
    ) -> None:
        failure_started.set()
        await release_failure.wait()

    session.stream_audio = AsyncMock()
    session.signal_user_activity_end = AsyncMock()
    dispatcher = AsrAudioDispatcher(
        validator=lambda _token, ref: ref is session,
        on_wire_audio=AsyncMock(),
        on_failure=on_failure,
        max_commands=1,
    )
    turn = _turn()

    assert dispatcher.activate(turn, session, b"first!")
    assert not dispatcher.enqueue_audio(
        turn,
        session,
        b"overflow",
        sample_rate_hz=16_000,
        sequence_no=1,
    )
    await asyncio.wait_for(failure_started.wait(), 1)

    assert len(dispatcher._failure_tasks) == 1
    failure_task = next(iter(dispatcher._failure_tasks))
    assert failure_task.get_name() == "asr-audio-command-backpressure"

    release_failure.set()
    await failure_task
    await asyncio.sleep(0)

    assert not dispatcher._failure_tasks
    await dispatcher.close()

async def test_optional_pause_has_separate_capacity_from_pcm_commands() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    writes = []
    session = type("Session", (), {})()

    async def stream(audio, **_kwargs):
        writes.append(audio)
        if audio == b"aa":
            entered.set()
            await release.wait()

    session.stream_audio = stream
    session.signal_local_activity = AsyncMock()
    on_failure = AsyncMock()
    dispatcher = AsrAudioDispatcher(
        validator=lambda *_: True, on_wire_audio=AsyncMock(),
        on_failure=on_failure, max_commands=2,
    )
    turn = _turn()
    try:
        assert dispatcher.activate(turn, session, b"aa")
        await entered.wait()
        assert dispatcher.enqueue_audio(turn, session, b"bb", sample_rate_hz=16000, sequence_no=1)
        assert await dispatcher.signal_pause_after_audio(session, wait_for_delivery=False)
        assert dispatcher.enqueue_audio(turn, session, b"cc", sample_rate_hz=16000, sequence_no=2)
        release.set()
        await dispatcher.wait_idle()
        assert writes == [b"aa", b"bb", b"cc"]
        session.signal_local_activity.assert_awaited_once_with(speech_active=False)
        on_failure.assert_not_awaited()
    finally:
        release.set()
        await dispatcher.close()


async def test_repeated_pause_cancellation_preserves_capacity_and_fifo() -> None:
    entered, release = asyncio.Event(), asyncio.Event()
    writes = []

    async def stream(audio, **_kwargs):
        writes.append(audio)
        if audio == b"aa":
            entered.set()
            await release.wait()

    session = type("Session", (), {})()
    session.stream_audio = stream
    session.signal_local_activity = AsyncMock()
    dispatcher = AsrAudioDispatcher(
        validator=lambda *_: True, on_wire_audio=AsyncMock(),
        on_failure=AsyncMock(), max_commands=2,
    )
    turn = _turn()
    try:
        assert dispatcher.activate(turn, session, b"aa")
        await entered.wait()
        assert dispatcher.enqueue_audio(turn, session, b"bb", sample_rate_hz=16000, sequence_no=1)
        for _ in range(20):
            assert await dispatcher.signal_pause_after_audio(session, wait_for_delivery=False)
            dispatcher.cancel_pending_pause_hints()
        assert dispatcher.enqueue_audio(turn, session, b"cc", sample_rate_hz=16000, sequence_no=2)
        release.set()
        await asyncio.wait_for(dispatcher.wait_idle(), 1)
        assert writes == [b"aa", b"bb", b"cc"]
        assert dispatcher._queue.normal_count == dispatcher._queue.pause_count == 0
        session.signal_local_activity.assert_not_awaited()
    finally:
        release.set()
        await dispatcher.close()


async def test_nonwaiting_hint_failure_logs_type_without_payload_and_keeps_pcm(caplog) -> None:
    session = type("Session", (), {})()
    session.stream_audio = AsyncMock()
    session.signal_local_activity = AsyncMock(side_effect=ValueError("private transcript"))
    on_failure = AsyncMock()
    dispatcher = AsrAudioDispatcher(
        validator=lambda *_: True, on_wire_audio=AsyncMock(), on_failure=on_failure,
    )
    turn = _turn()
    try:
        assert dispatcher.activate(turn, session, b"aa")
        assert await dispatcher.signal_pause_after_audio(session, wait_for_delivery=False)
        assert dispatcher.enqueue_audio(turn, session, b"bb", sample_rate_hz=16000, sequence_no=1)
        await asyncio.wait_for(dispatcher.wait_idle(), 1)
        await asyncio.sleep(0)
        assert "type=ValueError" in caplog.text
        assert "category=session_error" in caplog.text
        assert "private transcript" not in caplog.text
        assert session.stream_audio.await_count == 2
        on_failure.assert_not_awaited()
    finally:
        await dispatcher.close()
