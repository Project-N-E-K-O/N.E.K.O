"""Incident regressions using real ASR state machines and a fake outer Core.

Delivery assertions stop at the Core callbacks: no remote provider acknowledgment,
production history, microphone, browser, or Electron presentation is claimed here.
"""

from __future__ import annotations

import asyncio
from functools import partial
from unittest.mock import AsyncMock, MagicMock

import pytest

from main_logic.asr_client._provider_events import (
    ProviderAudioRange,
    ProviderEndpointNotification,
    ProviderUtteranceKey,
    ProviderUtteranceStartedNotification,
)
from main_logic.asr_client.admission.contracts import (
    AdmissionDisposition,
    SpeakerHigh,
)
from main_logic.asr_client.provider_policy import resolve_provider_policy
from main_logic.voice_turn.contracts import AsrSubmitStatus
from tests.unit.asr_client.test_candidate_rejection_runtime import (
    _drain_runtime_admission,
)
from tests.unit.asr_client.test_provider_speaker_continuity import (
    _active_real_stack,
    _ConstantBackend,
    _close_stack,
    _submit_pcm,
)
from tests.unit import test_asr_detector_runtime as detector_fixture


async def _settle_deliveries(core, runtime):
    await _drain_runtime_admission(runtime)
    await runtime.wait_transcript_idle()
    await core._voice_input_registry.wait_idle()


def _endpoint(utterance, phase, *, exact=True):
    return ProviderEndpointNotification(
        phase=phase,
        generation=0,
        buffer_epoch=0,
        utterance_id=utterance,
        boundary_quality="exact" if exact else "unknown",
        audio_range=(
            ProviderAudioRange((utterance - 1) * 25_600, utterance * 25_600)
            if exact
            else None
        ),
    )


@pytest.mark.parametrize("mixed_scores", [False, True])
@pytest.mark.parametrize("final_first", [False, True])
async def test_continuous_exact_finals_have_per_key_disposition_without_manual_wake(
    mixed_scores, final_first
):
    (
        core,
        runtime,
        detector,
        shadow,
        lifecycle,
        session,
        turn,
    ) = await _active_real_stack(score=0.95, mixed_scores=mixed_scores)
    expected = []
    generations = []
    try:
        for utterance in range(1, 4):
            for sequence in range((utterance - 1) * 16 + 1, utterance * 16 + 1):
                submitted = await _submit_pcm(
                    runtime,
                    turn,
                    sequence=sequence,
                    owner_pcm=mixed_scores and utterance > 1,
                )
                assert submitted.status is AsrSubmitStatus.ACCEPTED
            key = ProviderUtteranceKey(0, 0, utterance)
            assert await runtime._handle_provider_utterance_started(
                ProviderUtteranceStartedNotification(
                    0,
                    0,
                    utterance,
                    audio_start_sample_16k=(utterance - 1) * 25_600,
                ),
                runtime._asr_session_epoch,
            )
            turn = runtime._asr_provider_started_turns[key]
            boundary = asyncio.create_task(
                runtime._handle_provider_endpoint_notification(
                    _endpoint(utterance, "boundary"),
                    runtime._asr_session_epoch,
                )
            )
            async with asyncio.timeout(1):
                while (
                    key not in runtime._asr_provider_exact_intervals
                    and not boundary.done()
                ):
                    await asyncio.sleep(0)
            assert key in runtime._asr_provider_exact_intervals, (
                key,
                runtime._speaker_verifier_diagnostics(),
            )
            transaction = runtime._asr_provider_exact_intervals[key]
            generations.append(transaction.snapshot.candidate_generation)
            core.continuity_score_host.ready.set()
            await boundary
            await shadow.wait_idle()
            text = f"accepted provider key {utterance}"
            if final_first:
                await runtime._handle_provider_final(
                    key, text, runtime._asr_session_epoch, "qwen"
                )
            await runtime._handle_provider_endpoint_notification(
                _endpoint(utterance, "ordered"),
                runtime._asr_session_epoch,
            )
            if not final_first:
                await runtime._handle_provider_final(
                    key, text, runtime._asr_session_epoch, "qwen"
                )
            await _settle_deliveries(core, runtime)
            if mixed_scores and utterance == 1:
                assert transaction.resolved_disposition is AdmissionDisposition.DROP
            else:
                expected.append(text)
            assert [
                call.args[0] for call in core.handle_input_transcript.await_args_list
            ] == expected
            assert [
                call.args[0] for call in core.session.create_response.await_args_list
            ] == expected
            await runtime._handle_provider_final(
                key,
                "late duplicate must never replace original",
                runtime._asr_session_epoch,
                "qwen",
            )
            await _settle_deliveries(core, runtime)
            assert [
                call.args[0] for call in core.handle_input_transcript.await_args_list
            ] == expected
            assert runtime._asr_session is session
            session.close.assert_not_awaited()
        assert len(set(generations)) == 3
        await runtime._asr_audio_dispatcher.wait_idle()
        assert (
            sum(len(call.args[0]) // 2 for call in session.stream_audio.await_args_list)
            == 76_800
        )
        assert detector._provider_audio_sample_cursor_16k == 76_800
    finally:
        await _close_stack(core)


@pytest.mark.parametrize("entrypoint", ["submit", "activation"])
async def test_unavailable_turn_accounts_until_final_then_successor_recovers(
    entrypoint,
):
    (
        core,
        runtime,
        detector,
        shadow,
        lifecycle,
        session,
        turn,
    ) = await _active_real_stack(score=0.95, gated_scores=False)
    try:
        assert (
            await _submit_pcm(runtime, turn, sequence=1)
        ).status is AsrSubmitStatus.ACCEPTED
        key = ProviderUtteranceKey(0, 0, 1)
        assert await runtime._handle_provider_utterance_started(
            ProviderUtteranceStartedNotification(0, 0, 1, audio_start_sample_16k=0),
            runtime._asr_session_epoch,
        )
        old = runtime._asr_provider_speaker_evidence_lease
        ledger = runtime._asr_provider_speaker_key_ledgers[key]
        runtime._poison_provider_speaker_ledger(ledger, "provider_pcm_receipt_missing")
        for sequence in range(2, 5):
            if entrypoint == "activation" and sequence == 2:
                runtime._asr_audio_dispatcher.abort(turn)
                await runtime._asr_audio_dispatcher.wait_idle()
                pcm = detector_fixture._speaker_pcm(100)
                feed = await detector.feed(
                    pcm,
                    ingress_token=turn.ingress,
                    speech_probability=0.9,
                    rnnoise_available=True,
                )
                assert feed.identity is not None
                runtime._record_buffered_provider_speaker_observation(
                    identity=feed.identity,
                    byte_count=len(pcm),
                    split_before_audio=False,
                    evidence_complete=True,
                )
                assert await runtime._activate_asr_audio_dispatcher(
                    lifecycle,
                    turn,
                    buffered_pcm16=pcm,
                )
            else:
                assert (
                    await _submit_pcm(runtime, turn, sequence=sequence)
                ).status is AsrSubmitStatus.ACCEPTED
            assert detector._provider_speaker_evidence_state_for(old) is None
            assert runtime._asr_provider_speaker_evidence_lease is None
            assert ledger.poisoned_reason == "provider_pcm_receipt_missing"
        await runtime._handle_provider_final(
            key, "degraded old turn", runtime._asr_session_epoch, "qwen"
        )
        await _settle_deliveries(core, runtime)
        before = [call.args[0] for call in core.handle_input_transcript.await_args_list]
        assert before == ["degraded old turn"]
        # A provider started event follows newly submitted audio. This ingress
        # performs the real post-final wake; no lifecycle state is set by hand.
        assert (
            await _submit_pcm(runtime, turn, sequence=5)
        ).status is AsrSubmitStatus.ACCEPTED
        assert await runtime._handle_provider_utterance_started(
            ProviderUtteranceStartedNotification(0, 0, 2, audio_start_sample_16k=6_400),
            runtime._asr_session_epoch,
        )
        successor_key = ProviderUtteranceKey(0, 0, 2)
        turn = runtime._asr_provider_started_turns[successor_key]
        for sequence in range(6, 21):
            assert (
                await _submit_pcm(runtime, turn, sequence=sequence)
            ).status is AsrSubmitStatus.ACCEPTED
        successor = runtime._asr_provider_speaker_evidence_lease
        assert successor is not None and successor != old
        assert detector._provider_speaker_evidence_state_for(successor) is not None
        await runtime._handle_provider_final(
            key, "late unavailable duplicate", runtime._asr_session_epoch, "qwen"
        )
        assert runtime._asr_provider_speaker_evidence_lease is successor
        await _settle_deliveries(core, runtime)
        assert [
            call.args[0] for call in core.handle_input_transcript.await_args_list
        ] == before
        await runtime._handle_provider_endpoint_notification(
            ProviderEndpointNotification(
                phase="boundary",
                generation=0,
                buffer_epoch=0,
                utterance_id=2,
                boundary_quality="exact",
                audio_range=ProviderAudioRange(6_400, 32_000),
            ),
            runtime._asr_session_epoch,
        )
        assert successor_key in runtime._asr_provider_exact_intervals
        await shadow.wait_idle()
        await runtime._handle_provider_final(
            successor_key,
            "recovered successor",
            runtime._asr_session_epoch,
            "qwen",
        )
        await _settle_deliveries(core, runtime)
        expected = ["degraded old turn", "recovered successor"]
        assert [
            call.args[0] for call in core.handle_input_transcript.await_args_list
        ] == expected
        assert [
            call.args[0] for call in core.session.create_response.await_args_list
        ] == expected
        await runtime._asr_audio_dispatcher.wait_idle()
        assert detector._provider_audio_sample_cursor_16k == 32_000
        assert (
            sum(len(call.args[0]) // 2 for call in session.stream_audio.await_args_list)
            == 32_000
        )
        assert runtime._asr_session is session
        session.close.assert_not_awaited()
    finally:
        await _close_stack(core)


@pytest.mark.parametrize("entrypoint", ["submit", "activation"])
async def test_real_foreign_owner_still_blocks_audio(entrypoint):
    (
        core,
        runtime,
        detector,
        shadow,
        lifecycle,
        session,
        turn,
    ) = await _active_real_stack()
    other = detector_fixture.DetectorRuntime(
        vad=detector_fixture._Vad(),
        gate=detector_fixture._Gate(),
        provider_policy=detector_fixture._provider_endpoint_policy(),
        speaker_shadow=detector_fixture.SpeakerShadowRuntime(
            backend_factory=partial(_ConstantBackend, 0.95),
            config=detector_fixture._provider_speaker_config(),
        ),
    )
    try:
        # Both leases are really issued, with different Detector ownership.
        foreign = await other.ensure_provider_speaker_evidence_lease()
        assert foreign is not None
        runtime._asr_provider_speaker_evidence_lease = foreign
        runtime._asr_current_speaker_candidate = foreign.candidate
        if entrypoint == "submit":
            result = await _submit_pcm(runtime, turn, sequence=1)
            assert result.status is AsrSubmitStatus.UNAVAILABLE
        else:
            runtime._asr_audio_dispatcher.abort(turn)
            await runtime._asr_audio_dispatcher.wait_idle()
            pcm = detector_fixture._speaker_pcm(100)
            feed = await detector.feed(
                pcm,
                ingress_token=turn.ingress,
                speech_probability=0.9,
                rnnoise_available=True,
            )
            assert feed.identity is not None
            runtime._record_buffered_provider_speaker_observation(
                identity=feed.identity,
                byte_count=len(pcm),
                split_before_audio=False,
                evidence_complete=True,
            )
            assert not await runtime._activate_asr_audio_dispatcher(
                lifecycle,
                turn,
                buffered_pcm16=pcm,
            )
        session.stream_audio.assert_not_awaited()
        assert runtime._asr_session is not session
        assert not runtime._ingress_token_matches(turn.ingress)
        # The foreign owner belongs to another Detector, so failed adoption
        # must not retire it as part of cleaning up the local attempted lease.
        assert other._provider_speaker_evidence_state_for(foreign) is not None
    finally:
        await _close_stack(core)
        await other.close()


async def test_exact_old_final_after_started_successor_keeps_new_owner_and_text():
    (
        core,
        runtime,
        detector,
        shadow,
        lifecycle,
        session,
        turn,
    ) = await _active_real_stack(
        score=0.95,
        gated_scores=False,
    )
    try:
        for sequence in range(1, 17):
            assert (
                await _submit_pcm(runtime, turn, sequence=sequence)
            ).status is AsrSubmitStatus.ACCEPTED
        first_key = ProviderUtteranceKey(0, 0, 1)
        next_key = ProviderUtteranceKey(0, 0, 2)
        assert await runtime._handle_provider_utterance_started(
            ProviderUtteranceStartedNotification(0, 0, 1, audio_start_sample_16k=0),
            runtime._asr_session_epoch,
        )
        await runtime._handle_provider_endpoint_notification(
            _endpoint(1, "boundary"),
            runtime._asr_session_epoch,
        )
        first = runtime._asr_provider_exact_intervals[first_key]
        successor = first.successor_evidence_lease
        assert successor is not None
        await runtime._handle_provider_endpoint_notification(
            _endpoint(1, "ordered"),
            runtime._asr_session_epoch,
        )
        assert (
            await _submit_pcm(runtime, turn, sequence=17)
        ).status is AsrSubmitStatus.ACCEPTED
        assert await runtime._handle_provider_utterance_started(
            ProviderUtteranceStartedNotification(
                0, 0, 2, audio_start_sample_16k=25_600
            ),
            runtime._asr_session_epoch,
        )
        next_turn = runtime._asr_provider_started_turns[next_key]
        await runtime._handle_provider_final(
            first_key, "first before overlap", runtime._asr_session_epoch, "qwen"
        )
        await _settle_deliveries(core, runtime)
        assert runtime._asr_provider_speaker_evidence_lease is successor
        assert detector._provider_speaker_evidence_state_for(successor) is not None
        assert runtime._asr_provider_started_turns[next_key] == next_turn
        for sequence in range(18, 33):
            assert (
                await _submit_pcm(runtime, next_turn, sequence=sequence)
            ).status is AsrSubmitStatus.ACCEPTED
        await runtime._handle_provider_endpoint_notification(
            _endpoint(2, "boundary"),
            runtime._asr_session_epoch,
        )
        assert next_key in runtime._asr_provider_exact_intervals, str(
            (
                runtime._asr_provider_speaker_key_ledgers[next_key],
                detector._candidate_generation,
                detector._provider_audio_sample_cursor_16k,
                {k: v for k, v in runtime._speaker_verifier_diagnostics().items() if v},
            )
        )
        await shadow.wait_idle()
        await runtime._handle_provider_final(
            next_key, "successor after overlap", runtime._asr_session_epoch, "qwen"
        )
        await _settle_deliveries(core, runtime)
        expected = ["first before overlap", "successor after overlap"]
        assert [
            call.args[0] for call in core.handle_input_transcript.await_args_list
        ] == expected
        assert [
            call.args[0] for call in core.session.create_response.await_args_list
        ] == expected
        await runtime._handle_provider_endpoint_notification(
            _endpoint(1, "ordered"),
            runtime._asr_session_epoch,
        )
        await runtime._handle_provider_final(
            first_key, "stale duplicate", runtime._asr_session_epoch, "qwen"
        )
        await _settle_deliveries(core, runtime)
        assert [
            call.args[0] for call in core.handle_input_transcript.await_args_list
        ] == expected
        session.close.assert_not_awaited()
    finally:
        await _close_stack(core)


@pytest.mark.parametrize("replacement_activation", [False, True])
async def test_delayed_retirement_confirmation_cannot_clear_adopted_successor(
    replacement_activation,
):
    (
        core,
        runtime,
        detector,
        shadow,
        lifecycle,
        session,
        turn,
    ) = await _active_real_stack()
    try:
        assert (
            await _submit_pcm(runtime, turn, sequence=1)
        ).status is AsrSubmitStatus.ACCEPTED
        key = ProviderUtteranceKey(0, 0, 1)
        assert await runtime._handle_provider_utterance_started(
            ProviderUtteranceStartedNotification(0, 0, 1, audio_start_sample_16k=0),
            runtime._asr_session_epoch,
        )
        old = runtime._asr_provider_speaker_evidence_lease
        ledger = runtime._asr_provider_speaker_key_ledgers[key]
        runtime._poison_provider_speaker_ledger(ledger, "provider_pcm_receipt_missing")
        assert (
            await _submit_pcm(runtime, turn, sequence=2)
        ).status is AsrSubmitStatus.ACCEPTED
        settlement = await detector.confirm_provider_speaker_evidence_retirement(old)
        old_identity = runtime._capture_runtime_identity(ingress_token=turn.ingress)
        old_activation = runtime._speaker_verifier_activation_generation
        await runtime._handle_provider_final(
            key, "old owner", runtime._asr_session_epoch, "qwen"
        )
        await _settle_deliveries(core, runtime)
        assert (
            await _submit_pcm(runtime, turn, sequence=3)
        ).status is AsrSubmitStatus.ACCEPTED
        assert await runtime._handle_provider_utterance_started(
            ProviderUtteranceStartedNotification(0, 0, 2, audio_start_sample_16k=3_200),
            runtime._asr_session_epoch,
        )
        successor = runtime._asr_provider_speaker_evidence_lease
        assert successor is not None and successor is not old
        current_candidate = runtime._asr_current_speaker_candidate
        current_logical = runtime._asr_current_speaker_lease
        if replacement_activation:
            runtime._speaker_verifier_activation_generation = "replacement-activation"
        consumed = runtime._consume_provider_speaker_evidence_settlement(
            settlement,
            lease=old,
            detector=detector,
            identity=old_identity,
            owner_generation=old_activation,
            turn_token=turn,
        )
        assert consumed is (not replacement_activation)
        assert runtime._asr_provider_speaker_evidence_lease is successor
        assert runtime._asr_current_speaker_candidate == current_candidate
        assert runtime._asr_current_speaker_lease == current_logical
        assert detector._provider_speaker_evidence_state_for(successor) is not None
    finally:
        await _close_stack(core)


def _replacement_factory():
    shadow = detector_fixture._StableEvidenceSpeakerShadowSpy()
    shadow.close = AsyncMock(wraps=shadow.close)
    factory = MagicMock(return_value=shadow)
    factory.enforces_admission = True
    return factory, shadow


def _enable_exact_verifier_install(runtime) -> None:
    runtime._asr_lifecycle.provider_policy = resolve_provider_policy(
        "qwen",
        endpointing_mode="provider",
    )


async def test_verifier_replacement_settles_physical_alias_and_keeps_final_owner() -> (
    None
):
    core, runtime, detector, _shadow, _lifecycle, session, turn = (
        await _active_real_stack()
    )
    _enable_exact_verifier_install(runtime)
    factory, _replacement = _replacement_factory()
    try:
        assert (
            await _submit_pcm(runtime, turn, sequence=1)
        ).status is AsrSubmitStatus.ACCEPTED
        key = ProviderUtteranceKey(0, 0, 1)
        assert await runtime._handle_provider_utterance_started(
            ProviderUtteranceStartedNotification(0, 0, 1, audio_start_sample_16k=0),
            runtime._asr_session_epoch,
        )
        old_lease = runtime._asr_provider_speaker_evidence_lease
        assert old_lease is not None
        ledger = runtime._asr_provider_speaker_key_ledgers[key]
        before = (
            detector._provider_audio_timeline_generation,
            detector._provider_audio_sample_cursor_16k,
            detector._provider_segment_last_sequence_no,
        )

        updated = await runtime.set_speaker_verifier_factory(
            factory,
            activation_generation="replacement",
        )
        assert updated, (
            runtime._speaker_verifier_install_receipt,
            runtime._speaker_installation_diagnostics,
            runtime._speaker_verifier_activation_generation,
        )

        assert runtime._asr_provider_speaker_evidence_lease is None
        assert runtime._asr_provider_speaker_ledgers[old_lease.candidate] is ledger
        assert ledger.poisoned_reason == "installation_retired"
        assert not runtime._accept_speaker_evidence_fact(
            SpeakerHigh(
                old_lease.candidate,
                1,
            ),
            activation_generation="continuity-test",
            enforce=True,
        )
        assert (
            detector._provider_audio_timeline_generation,
            detector._provider_audio_sample_cursor_16k,
            detector._provider_segment_last_sequence_no,
        ) == before
        settlement = await detector.confirm_provider_speaker_evidence_retirement(
            old_lease
        )
        assert detector.validate_provider_speaker_evidence_settlement(
            settlement,
            lease=old_lease,
        )

        assert (
            await _submit_pcm(runtime, turn, sequence=2)
        ).status is AsrSubmitStatus.ACCEPTED
        await runtime._asr_audio_dispatcher.wait_idle()
        assert detector._provider_audio_sample_cursor_16k == before[1] + 1_600
        assert detector._provider_segment_last_sequence_no == 2
        assert runtime._asr_session is session
        session.close.assert_not_awaited()

        await runtime._handle_provider_final(
            key,
            "final retained across replacement",
            runtime._asr_session_epoch,
            "qwen",
        )
        await _settle_deliveries(core, runtime)
        assert [
            call.args[0] for call in core.handle_input_transcript.await_args_list
        ] == ["final retained across replacement"]
        await runtime._handle_provider_final(
            key,
            "late duplicate",
            runtime._asr_session_epoch,
            "qwen",
        )
        await _settle_deliveries(core, runtime)
        assert core.handle_input_transcript.await_count == 1
    finally:
        await _close_stack(core)


async def test_verifier_replacement_failure_after_detector_keeps_audio_accountable() -> (
    None
):
    core, runtime, detector, _shadow, _lifecycle, session, turn = (
        await _active_real_stack()
    )
    _enable_exact_verifier_install(runtime)
    factory, replacement = _replacement_factory()
    original_replace = detector.replace_speaker_verifier

    async def replace_then_fail(*args, **kwargs):
        await original_replace(*args, **kwargs)
        raise RuntimeError("injected post-swap failure")

    detector.replace_speaker_verifier = replace_then_fail
    try:
        assert (
            await _submit_pcm(runtime, turn, sequence=1)
        ).status is AsrSubmitStatus.ACCEPTED
        assert await runtime._handle_provider_utterance_started(
            ProviderUtteranceStartedNotification(0, 0, 1, audio_start_sample_16k=0),
            runtime._asr_session_epoch,
        )
        old_lease = runtime._asr_provider_speaker_evidence_lease
        before_timeline = detector._provider_audio_timeline_generation

        assert not await runtime.set_speaker_verifier_factory(
            factory,
            activation_generation="replacement",
        )
        assert detector._speaker_shadow is replacement
        assert runtime._asr_provider_speaker_evidence_lease is None
        settlement = await detector.confirm_provider_speaker_evidence_retirement(
            old_lease
        )
        assert detector.validate_provider_speaker_evidence_settlement(
            settlement,
            lease=old_lease,
        )

        assert (
            await _submit_pcm(runtime, turn, sequence=2)
        ).status is AsrSubmitStatus.ACCEPTED
        await runtime._asr_audio_dispatcher.wait_idle()
        assert detector._provider_audio_timeline_generation == before_timeline
        assert detector._provider_audio_sample_cursor_16k == 3_200
        assert detector._provider_segment_last_sequence_no == 2
        assert runtime._asr_session is session
        session.close.assert_not_awaited()
    finally:
        await _close_stack(core)


async def test_verifier_replacement_failure_before_detector_keeps_audio_accountable() -> (
    None
):
    core, runtime, detector, _shadow, _lifecycle, session, turn = (
        await _active_real_stack()
    )
    _enable_exact_verifier_install(runtime)
    factory, replacement = _replacement_factory()
    try:
        assert (
            await _submit_pcm(runtime, turn, sequence=1)
        ).status is AsrSubmitStatus.ACCEPTED
        assert await runtime._handle_provider_utterance_started(
            ProviderUtteranceStartedNotification(0, 0, 1, audio_start_sample_16k=0),
            runtime._asr_session_epoch,
        )
        old_lease = runtime._asr_provider_speaker_evidence_lease
        before_timeline = detector._provider_audio_timeline_generation
        detector.replace_speaker_verifier = AsyncMock(
            side_effect=RuntimeError("injected pre-swap failure")
        )

        assert not await runtime.set_speaker_verifier_factory(
            factory,
            activation_generation="replacement",
        )
        assert runtime._asr_provider_speaker_evidence_lease is old_lease
        replacement.close.assert_awaited_once()

        assert (
            await _submit_pcm(runtime, turn, sequence=2)
        ).status is AsrSubmitStatus.ACCEPTED
        await runtime._asr_audio_dispatcher.wait_idle()
        assert runtime._asr_provider_speaker_evidence_lease is None
        assert detector._provider_audio_timeline_generation == before_timeline
        assert detector._provider_audio_sample_cursor_16k == 3_200
        assert detector._provider_segment_last_sequence_no == 2
        assert runtime._asr_session is session
        session.close.assert_not_awaited()
    finally:
        await _close_stack(core)


async def test_verifier_handoff_consumes_retirement_before_detached_cleanup() -> (
    None
):
    core, runtime, detector, old_shadow, _lifecycle, session, turn = (
        await _active_real_stack()
    )
    _enable_exact_verifier_install(runtime)
    factory, _replacement = _replacement_factory()
    close_started = asyncio.Event()
    close_release = asyncio.Event()
    original_close = old_shadow.close

    async def blocked_close():
        close_started.set()
        await close_release.wait()
        await original_close()

    old_shadow.close = blocked_close
    try:
        assert (
            await _submit_pcm(runtime, turn, sequence=1)
        ).status is AsrSubmitStatus.ACCEPTED
        assert await runtime._handle_provider_utterance_started(
            ProviderUtteranceStartedNotification(0, 0, 1, audio_start_sample_16k=0),
            runtime._asr_session_epoch,
        )
        old_lease = runtime._asr_provider_speaker_evidence_lease
        task = asyncio.create_task(
            runtime.set_speaker_verifier_factory(
                factory,
                activation_generation="replacement",
            )
        )
        await asyncio.wait_for(close_started.wait(), 1.0)
        assert await asyncio.wait_for(task, 0.1)
        assert not close_release.is_set()

        assert runtime._asr_provider_speaker_evidence_lease is None
        settlement = await detector.confirm_provider_speaker_evidence_retirement(
            old_lease
        )
        assert detector.validate_provider_speaker_evidence_settlement(
            settlement,
            lease=old_lease,
        )
        assert (
            await _submit_pcm(runtime, turn, sequence=2)
        ).status is AsrSubmitStatus.ACCEPTED
        await runtime._asr_audio_dispatcher.wait_idle()
        assert detector._provider_audio_sample_cursor_16k == 3_200
        assert detector._provider_segment_last_sequence_no == 2
        assert runtime._asr_session is session
        session.close.assert_not_awaited()
        close_release.set()
        await asyncio.gather(*tuple(runtime._speaker_retired_cleanup))
    finally:
        close_release.set()
        await _close_stack(core)


async def test_consecutive_verifier_replacements_fence_old_callback_and_alias() -> None:
    core, runtime, detector, _shadow, _lifecycle, session, turn = (
        await _active_real_stack()
    )
    _enable_exact_verifier_install(runtime)
    first_factory, first_shadow = _replacement_factory()
    second_factory, second_shadow = _replacement_factory()
    try:
        assert (
            await _submit_pcm(runtime, turn, sequence=1)
        ).status is AsrSubmitStatus.ACCEPTED
        first_key = ProviderUtteranceKey(0, 0, 1)
        assert await runtime._handle_provider_utterance_started(
            ProviderUtteranceStartedNotification(0, 0, 1, audio_start_sample_16k=0),
            runtime._asr_session_epoch,
        )
        assert await runtime.set_speaker_verifier_factory(
            first_factory,
            activation_generation="replacement-a",
        )
        await runtime._handle_provider_final(
            first_key,
            "first unavailable owner",
            runtime._asr_session_epoch,
            "qwen",
        )
        await _settle_deliveries(core, runtime)

        assert (
            await _submit_pcm(runtime, turn, sequence=2)
        ).status is AsrSubmitStatus.ACCEPTED
        second_key = ProviderUtteranceKey(0, 0, 2)
        assert await runtime._handle_provider_utterance_started(
            ProviderUtteranceStartedNotification(
                0,
                0,
                2,
                audio_start_sample_16k=1_600,
            ),
            runtime._asr_session_epoch,
        )
        next_turn = runtime._asr_provider_started_turns[second_key]
        first_lease = runtime._asr_provider_speaker_evidence_lease
        first_activation = runtime._speaker_verifier_activation_generation
        assert first_lease is not None and first_activation is not None
        before = (
            detector._provider_audio_timeline_generation,
            detector._provider_audio_sample_cursor_16k,
            detector._provider_segment_last_sequence_no,
        )

        assert await runtime.set_speaker_verifier_factory(
            second_factory,
            activation_generation="replacement-b",
        )

        assert detector._speaker_shadow is second_shadow
        first_shadow.close.assert_awaited_once()
        assert runtime._asr_provider_speaker_evidence_lease is None
        assert (
            detector._provider_audio_timeline_generation,
            detector._provider_audio_sample_cursor_16k,
            detector._provider_segment_last_sequence_no,
        ) == before
        assert not runtime._accept_speaker_evidence_fact(
            SpeakerHigh(
                first_lease.candidate,
                1,
            ),
            activation_generation=first_activation,
            enforce=True,
        )
        settlement = await detector.confirm_provider_speaker_evidence_retirement(
            first_lease
        )
        assert detector.validate_provider_speaker_evidence_settlement(
            settlement,
            lease=first_lease,
        )

        assert (
            await _submit_pcm(runtime, next_turn, sequence=3)
        ).status is AsrSubmitStatus.ACCEPTED
        await runtime._asr_audio_dispatcher.wait_idle()
        assert detector._provider_audio_sample_cursor_16k == before[1] + 1_600
        assert detector._provider_segment_last_sequence_no == 3
        assert runtime._asr_session is session
        session.close.assert_not_awaited()
    finally:
        await _close_stack(core)
