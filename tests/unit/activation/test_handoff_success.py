from __future__ import annotations
import asyncio
import pytest
from main_logic.voice_input.activation import ActivationState
from main_logic.voice_turn.contracts import AsrSubmitResult, AsrSubmitStatus

from tests.support.activation_handoff_fakes import (
    _until,
)

from tests.unit.activation._scenarios import (
    _harness,
)

pytestmark = [pytest.mark.asyncio, pytest.mark.runtime]


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_three_core_handoffs_keep_short_speech_once_without_rescoring(route):
    async with _harness(route) as h:
        runtime = h.activation
        generation = runtime.generation
        idle_task = runtime._idle_task
        receiver = h.manager._asr_runtime
        expected = h.pcm.copy()
        for turn in range(3):
            target = h.session(f"target-{turn}")
            deadline = runtime.idle_deadline
            ticket = await h.manager._begin_voice_activation_handoff(target)
            assert ticket is not None and ticket is not False
            assert runtime.state is ActivationState.ACTIVE
            assert runtime.idle_deadline == deadline
            h.clock.value += 0.1
            short = await h.feed(3_000 + turn * 2)
            assert len(h.pcm) == len(expected)
            assert runtime.pending_output_bytes == len(short)
            expected.append(short)
            deadline_after_voice = runtime.idle_deadline
            await h.promote(ticket, target)
            await _until(lambda: h.pcm == expected)
            assert not await h.manager._commit_voice_activation_handoff(ticket)
            h.clock.value += 0.1
            expected.append(await h.feed(3_001 + turn * 2))
            await _until(lambda: h.pcm == expected)
            assert runtime.idle_deadline >= deadline_after_voice
            assert runtime.generation == generation
            assert h.manager._voice_session_activation_runtime is runtime
            assert h.manager._asr_runtime is receiver
            assert runtime._idle_task is idle_task
            assert runtime.state is ActivationState.ACTIVE
            assert len(h.factory.runtimes) == len(h.factory.scorers) == 1
            assert h.factory.scorers[0].calls == 1
            assert not h.factory.scorers[0].closed
            if route == "native":
                assert h.deliveries[-2:] == [
                    (target.name, pcm) for pcm in expected[-2:]
                ]
        assert h.manager._voice_session_activation_sequence == 21
        assert len(h.pcm) == len(set(h.pcm)) == 21


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_handoff_settles_inflight_send_before_rebinding_and_never_replays_it(
    route,
):
    async with _harness(route) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        source = h.manager.session
        original = h.pcm.copy()

        async def held_send(value, **kwargs):
            entered.set()
            await release.wait()
            if route == "native":
                await source._stream(value)
                return None
            return await h.submit(value, **kwargs)

        transport = (
            source.stream_audio if route == "native" else h.manager._asr_runtime.submit
        )
        transport.side_effect = held_send
        inflight_pcm = await h.feed(3_000)
        await entered.wait()
        target = h.session("target")
        begin = asyncio.create_task(h.manager._begin_voice_activation_handoff(target))
        try:
            await _until(lambda: h.activation.output_paused)
            assert not begin.done()
            queued_pcm = await h.feed(3_001)
            assert h.manager.session is source
            assert h.pcm == original
            release.set()
            ticket = await asyncio.wait_for(begin, timeout=2.0)
            assert ticket is not None and ticket is not False
            assert h.pcm == original + [inflight_pcm]
            transport.side_effect = source._stream if route == "native" else h.submit
            await h.promote(ticket, target)
            await _until(lambda: h.pcm == original + [inflight_pcm, queued_pcm])
            assert h.activation.state is ActivationState.ACTIVE
            assert h.factory.scorers[0].calls == 1
            assert h.pcm.count(inflight_pcm) == h.pcm.count(queued_pcm) == 1
            if route == "native":
                assert h.deliveries[-2:] == [
                    ("source", inflight_pcm),
                    ("target", queued_pcm),
                ]
        finally:
            release.set()
            if not begin.done():
                begin.cancel()
            await asyncio.gather(begin, return_exceptions=True)


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_known_not_sent_frame_resumes_once_on_authorized_target(route):
    async with _harness(route) as h:
        original = h.pcm.copy()
        if route == "native":
            h.manager.session.stream_audio = None
        else:
            h.manager._asr_runtime.submit.side_effect = None
            h.manager._asr_runtime.submit.return_value = AsrSubmitResult(
                AsrSubmitStatus.STALE
            )
        pending = await h.feed(3_000)
        await _until(lambda: not h.activation.output_inflight)
        assert h.pcm == original
        assert h.activation.pending_output_bytes == len(pending)
        target = h.session("target")
        ticket = await h.manager._begin_voice_activation_handoff(target)
        assert ticket is not None and ticket is not False
        h.manager._asr_runtime.submit.side_effect = h.submit
        await h.promote(ticket, target)
        await _until(lambda: h.pcm == original + [pending])
        assert h.activation.pending_output_bytes == 0
        assert h.pcm.count(pending) == 1


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_verifying_handoff_preserves_one_scorer_result_without_early_activation(
    route,
):
    async with _harness(route, active=False) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        scorer = h.factory.scorers[0]
        original_score = scorer.score

        async def held_score(*args, **kwargs):
            entered.set()
            await release.wait()
            return await original_score(*args, **kwargs)

        scorer.score = held_score
        expected = [int(2_000).to_bytes(2, "little", signed=True) * 1_600]
        for index in range(14):
            h.clock.value += 0.1
            expected.append(await h.feed(2_001 + index))
        await asyncio.wait_for(entered.wait(), timeout=2.0)
        target = h.session("target")
        try:
            assert h.activation.state is ActivationState.VERIFYING
            ticket = await h.manager._begin_voice_activation_handoff(target)
            assert ticket is not None and ticket is not False
            await h.promote(ticket, target)
            assert h.activation.state is ActivationState.VERIFYING
            assert h.deliveries == []
            release.set()
            await _until(lambda: len(h.pcm) >= len(expected))
            assert h.pcm == expected
            assert h.activation.state is ActivationState.ACTIVE
            assert len(h.factory.runtimes) == len(h.factory.scorers) == 1
            assert scorer.calls == 1
            assert not scorer.closed
            if route == "native":
                assert {name for name, _pcm in h.deliveries} == {"target"}
        finally:
            release.set()


@pytest.mark.parametrize("route", ["native", "independent"])
async def test_replaying_handoff_keeps_native_utterance_whole_and_independent_pcm_once(
    route,
):
    async with _harness(route, active=False) as h:
        entered, release = asyncio.Event(), asyncio.Event()
        source = h.manager.session

        async def held_first_send(value, **kwargs):
            entered.set()
            await release.wait()
            if route == "native":
                await source._stream(value)
                return None
            return await h.submit(value, **kwargs)

        transport = (
            source.stream_audio if route == "native" else h.manager._asr_runtime.submit
        )
        transport.side_effect = held_first_send
        expected = [int(2_000).to_bytes(2, "little", signed=True) * 1_600]
        for index in range(14):
            h.clock.value += 0.1
            expected.append(await h.feed(2_001 + index))
        await asyncio.wait_for(entered.wait(), timeout=2.0)
        target = h.session("target")
        begin = None
        try:
            assert h.activation.state is ActivationState.REPLAYING
            if route == "native":
                assert await h.manager._begin_voice_activation_handoff(target) is False
                assert not h.activation.output_paused
                release.set()
            else:
                begin = asyncio.create_task(
                    h.manager._begin_voice_activation_handoff(target)
                )
                await _until(lambda: h.activation.output_paused)
                release.set()
                ticket = await asyncio.wait_for(begin, timeout=2.0)
                assert ticket is not None and ticket is not False
                assert h.pcm == expected[:1]
                transport.side_effect = h.submit
                await h.promote(ticket, target)
            await _until(lambda: len(h.pcm) >= len(expected))
            assert h.pcm == expected
            assert h.activation.state is ActivationState.ACTIVE
            assert h.factory.scorers[0].calls == 1
            if route == "native":
                assert h.manager.session is source
                assert {name for name, _pcm in h.deliveries} == {"source"}
                ticket = await h.manager._begin_voice_activation_handoff(target)
                assert ticket is not None and ticket is not False
                await h.promote(ticket, target)
        finally:
            release.set()
            if begin is not None:
                if not begin.done():
                    begin.cancel()
                await asyncio.gather(begin, return_exceptions=True)
