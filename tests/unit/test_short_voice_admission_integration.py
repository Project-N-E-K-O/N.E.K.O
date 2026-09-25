import asyncio
from dataclasses import replace

import pytest

from main_logic.voice_turn.admission import AdmissionConfig
from tests.unit import test_voice_admission_integration as base


def make_trial(monkeypatch, *, optimization=False, smart_turn=False, shadow=False):
    original = base.AdmissionActivityGate

    def gate(vad, config):
        return original(
            vad,
            config,
            admission_config=None
            if shadow
            else AdmissionConfig(experimental_short_speech=True),
            admission_shadow_config=AdmissionConfig(experimental_short_speech=True)
            if shadow
            else None,
        )

    monkeypatch.setattr(base, "AdmissionActivityGate", gate)
    return base.make_runtime(optimization, smart_turn)


@pytest.mark.asyncio
@pytest.mark.parametrize("optimization", [True, False])
@pytest.mark.parametrize("smart_turn", [True, False])
async def test_short_admission_delivers_exact_audio_and_settles_once(
    monkeypatch, optimization, smart_turn
):
    runtime, callbacks, session, vad, token = make_trial(
        monkeypatch, optimization=optimization, smart_turn=smart_turn
    )
    try:
        speech = await base.send(runtime, vad, token, [0.95] * 6)
        callbacks.on_prepare_turn.assert_not_awaited()
        session.stream_audio.assert_not_awaited()
        tail = await base.send(runtime, vad, token, [0.1] * 4)
        callbacks.on_prepare_turn.assert_awaited_once()
        assert (
            b"".join(c.args[0] for c in session.stream_audio.await_args_list)
            == speech + tail
        )
        remaining = await base.send(runtime, vad, token, [0.1] * 6)
        assert (
            b"".join(c.args[0] for c in session.stream_audio.await_args_list)
            == speech + tail + remaining
        )
        assert session.signal_user_activity_end.await_count == int(smart_turn)
        if not smart_turn:
            await runtime._handle_independent_asr_endpoint(runtime._asr_session_epoch)
        await runtime._handle_independent_asr_final(
            "停", runtime._asr_session_epoch, "qwen"
        )
        await runtime.wait_transcript_idle()
        callbacks.on_final.assert_awaited_once()
        assert callbacks.on_final.await_args.args[0].evidence.admission_path == "short"
        callbacks.on_prepare_turn.assert_awaited_once()
        callbacks.on_failure.assert_not_awaited()
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("shadow", [True, False])
async def test_rejected_pulses_do_not_upload_or_prepare(monkeypatch, shadow):
    runtime, callbacks, session, vad, token = make_trial(monkeypatch, shadow=shadow)
    try:
        await base.send(runtime, vad, token, ([0.95] + [0.1] * 10) * 5)
        if shadow:
            await base.send(runtime, vad, token, [0.95] * 6 + [0.1] * 10)
        callbacks.on_prepare_turn.assert_not_awaited()
        session.stream_audio.assert_not_awaited()
        callbacks.on_final.assert_not_awaited()
        callbacks.on_failure.assert_not_awaited()
        speech = await base.send(runtime, vad, token, [0.95] * 7)
        callbacks.on_prepare_turn.assert_awaited_once()
        delivered = b"".join(c.args[0] for c in session.stream_audio.await_args_list)
        assert delivered.endswith(speech)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_pending_short_successor_survives_old_final(monkeypatch):
    runtime, callbacks, session, vad, token = make_trial(monkeypatch)
    try:
        first = await base.send(runtime, vad, token, [0.95] * 6 + [0.1] * 10)
        await runtime._handle_independent_asr_endpoint(runtime._asr_session_epoch)
        second = await base.send(runtime, vad, token, [0.95] * 6 + [0.1] * 4)
        callbacks.on_prepare_turn.assert_awaited_once()
        await runtime._handle_independent_asr_final(
            "first", runtime._asr_session_epoch, "qwen"
        )
        await runtime.wait_transcript_idle()
        await runtime._asr_audio_dispatcher.wait_idle()
        assert callbacks.on_prepare_turn.await_count == 2
        old = callbacks.on_final.await_args.args[0].evidence
        new = runtime._asr_admission_evidence[runtime._asr_prepared_turn_token]
        assert old.candidate_id != new.candidate_id
        assert old.admission_path == new.admission_path == "short"
        assert (
            b"".join(c.args[0] for c in session.stream_audio.await_args_list)
            == first + second
        )
        callbacks.on_failure.assert_not_awaited()
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_close_before_short_verdict_cannot_deliver_late_audio(monkeypatch):
    runtime, callbacks, session, vad, token = make_trial(monkeypatch)
    await base.send(runtime, vad, token, [0.95] * 6 + [0.1] * 3)
    await runtime.close()
    await base.send(runtime, vad, token, [0.1])
    callbacks.on_prepare_turn.assert_not_awaited()
    session.stream_audio.assert_not_awaited()
    callbacks.on_final.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["prefix", "middle", "capacity"])
async def test_short_verdict_does_not_bypass_retained_range_protection(
    monkeypatch, damage
):
    runtime, callbacks, session, vad, token = make_trial(monkeypatch)
    lifecycle = runtime._asr_lifecycle
    try:
        await base.send(runtime, vad, token, [0.95] * 6 + [0.1] * 3)
        buffer = lifecycle._pre_roll
        if damage == "prefix":
            buffer.clear()
        elif damage == "middle":
            pcm = buffer.peek()
            buffer.clear()
            buffer.append(pcm[: 2048 * 2], start_sample=0)
            buffer.append(pcm[2560 * 2 :], start_sample=2560)
        else:
            # Keep the real PCM intact while exercising the capacity check.
            lifecycle.config = replace(lifecycle.config, pre_roll_ms=256)
        await base.send(runtime, vad, token, [0.1])
        callbacks.on_prepare_turn.assert_not_awaited()
        session.stream_audio.assert_not_awaited()
        callbacks.on_failure.assert_awaited_once()
        expected = {
            "prefix": "candidate_audio_range_missing",
            "middle": "candidate_audio_range_discontinuous",
            "capacity": "candidate_audio_capacity_exceeded",
        }
        assert lifecycle.admission_failure_reason == expected[damage]
    finally:
        await runtime.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("smart_turn", [True, False])
async def test_close_during_detector_await_invalidates_pending_verdict(
    monkeypatch, smart_turn
):
    runtime, callbacks, session, vad, token = make_trial(
        monkeypatch, smart_turn=smart_turn
    )
    await base.send(runtime, vad, token, [0.95] * 6 + [0.1] * 3)
    detector = runtime._asr_detector
    method = "submit_audio" if smart_turn else "feed"
    original = getattr(detector, method)
    entered, release = asyncio.Event(), asyncio.Event()

    async def blocked(*args, **kwargs):
        entered.set()
        await release.wait()
        return await original(*args, **kwargs)

    monkeypatch.setattr(detector, method, blocked)
    task = asyncio.create_task(base.send(runtime, vad, token, [0.1]))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        await runtime.close()
        release.set()
        await asyncio.wait_for(task, 5)
        callbacks.on_prepare_turn.assert_not_awaited()
        session.stream_audio.assert_not_awaited()
        callbacks.on_final.assert_not_awaited()
    finally:
        release.set()
        await task
        await runtime.close()
