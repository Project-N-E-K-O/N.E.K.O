"""A failed predecessor must not retire a route installed by a real start."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client import runtime as runtime_module
from main_logic.asr_client._provider_events import (
    ProviderUtteranceKey,
    ProviderUtteranceStartedNotification,
)
from main_logic.asr_client.admission.contracts import SpeakerCaptureLeaseToken
from main_logic.asr_client.lifecycle import FinalKey
from main_logic.asr_client.runtime import AsrStartStatus
from main_logic.asr_client.speaker_shadow.contracts import SpeakerShadowCandidateKey
from tests.unit.test_core_independent_asr import _selection
from tests.unit.asr_client.test_provider_speaker_continuity import (
    _active_real_stack,
    _close_stack,
    _submit_pcm,
    detector_fixture,
)
from main_logic.voice_turn.contracts import AsrSubmitStatus


async def test_restart_old_cleanup_cannot_retire_new_scope_or_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        core,
        runtime,
        detector,
        shadow,
        lifecycle,
        session,
        old_turn,
    ) = await _active_real_stack()
    provider_key = ProviderUtteranceKey(0, 0, 1)
    retire_entered = asyncio.Event()
    release_retire = asyncio.Event()
    original_retire_turn = runtime._asr_admission_ingress.retire_turn
    cleanup_tasks: set[asyncio.Task] = set()

    async def hold_old_turn_retirement(turn_token):
        if turn_token == old_turn:
            retire_entered.set()
            await release_retire.wait()
        return await original_retire_turn(turn_token)

    monkeypatch.setattr(
        runtime._asr_admission_ingress,
        "retire_turn",
        hold_old_turn_retirement,
    )
    try:
        for sequence in range(1, 17):
            assert (
                await _submit_pcm(runtime, old_turn, sequence=sequence)
            ).status is AsrSubmitStatus.ACCEPTED
        await shadow.wait_idle()
        assert await runtime._handle_provider_utterance_started(
            ProviderUtteranceStartedNotification(
                0,
                0,
                1,
                audio_start_sample_16k=0,
            ),
            runtime._asr_session_epoch,
        )
        old_scope = runtime._asr_provider_transport_scope
        old_dispatcher = runtime._asr_transcript_dispatcher
        old_lease = runtime._asr_admission_turn_leases[old_turn]
        assert old_scope is not None
        assert tuple(
            binding.provider_key
            for binding in await runtime._asr_admission.live_provider_binding_keys(
                old_scope
            )
        ) == (provider_key,)

        replacement = SimpleNamespace(
            is_ready=True,
            connect=AsyncMock(),
            close=AsyncMock(),
            stream_audio=AsyncMock(),
            signal_user_activity_end=AsyncMock(),
        )
        session.is_ready = False
        runtime._asr_session_factory = lambda _selection: replacement
        runtime._asr_transport_selection = object()
        tasks_before_restart = set(runtime._asr_close_tasks)

        await asyncio.wait_for(runtime._restart_transport(max_attempts=1), timeout=2)
        await asyncio.wait_for(retire_entered.wait(), timeout=2)
        cleanup_tasks = set(runtime._asr_close_tasks) - tasks_before_restart

        new_scope = runtime._asr_provider_transport_scope
        new_dispatcher = runtime._asr_transcript_dispatcher
        assert runtime._asr_session is replacement
        assert new_scope is not None and new_scope != old_scope
        assert new_dispatcher is not old_dispatcher

        lifecycle.invalidate_audio()
        runtime._asr_current_ingress_token = core._capture_ingress_token()
        new_turn = runtime._capture_turn_token(lifecycle)
        new_candidate = SpeakerShadowCandidateKey(
            detector.detector_epoch,
            10_000,
            "provider_candidate",
        )
        new_lease = SpeakerCaptureLeaseToken(
            session_generation=runtime._asr_session_epoch,
            start_generation=runtime._asr_start_generation,
            transport_generation=lifecycle.snapshot.transport_generation,
            detector_epoch=detector.detector_epoch,
            lease_nonce=old_lease.lease_nonce + 100,
        )
        await runtime._asr_admission_ingress.open_speaker_lease(
            new_lease,
            new_candidate,
            transport_scope=new_scope,
        )
        await runtime._asr_admission_ingress.attach_turn_to_speaker_lease(
            new_turn,
            new_lease,
            provider_key,
            transport_scope=new_scope,
        )
        new_final_key = FinalKey.from_turn(new_turn)
        assert new_dispatcher.try_reserve(new_final_key)

        release_retire.set()
        if cleanup_tasks:
            await asyncio.wait_for(
                asyncio.gather(*cleanup_tasks, return_exceptions=True),
                timeout=2,
            )

        assert await runtime._asr_admission.get_record(old_turn) is None
        assert await runtime._asr_admission.get_speaker_lease(old_lease) is None
        assert await runtime._asr_admission.live_provider_binding_keys(old_scope) == ()
        new_record = await runtime._asr_admission.get_record(new_turn)
        new_parent = await runtime._asr_admission.get_speaker_lease(new_lease)
        assert new_record is not None
        assert new_record.speaker_lease_token == new_lease
        assert new_parent is not None
        assert new_parent.child_bindings[0].turn_token == new_turn
        assert tuple(
            binding.provider_key
            for binding in await runtime._asr_admission.live_provider_binding_keys(
                new_scope
            )
        ) == (provider_key,)
        assert runtime._asr_transcript_dispatcher is new_dispatcher
        assert new_dispatcher.has_pending_delivery
        assert new_final_key in new_dispatcher._reservations
    finally:
        release_retire.set()
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        await _close_stack(core)


async def test_partial_audio_failure_cannot_detach_successful_start_takeover(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        core,
        runtime,
        detector,
        shadow,
        lifecycle,
        session,
        turn,
    ) = await _active_real_stack()
    invalidation_completed = asyncio.Event()
    resume_failure = asyncio.Event()
    original_finish = runtime._finish_admission_invalidation
    invalidation_calls = 0

    async def hold_first_invalidation_return(*args, **kwargs):
        nonlocal invalidation_calls
        invalidation_calls += 1
        ordinal = invalidation_calls
        # Execute the actual ingress invalidation, effect settlement, correlator
        # retirement and dispatcher invalidation. Only its return is gated.
        await original_finish(*args, **kwargs)
        if ordinal == 1:
            invalidation_completed.set()
            await resume_failure.wait()

    monkeypatch.setattr(
        runtime, "_finish_admission_invalidation", hold_first_invalidation_return
    )
    replacement = SimpleNamespace(
        is_ready=True,
        connect=AsyncMock(),
        close=AsyncMock(),
        stream_audio=AsyncMock(),
        signal_user_activity_end=AsyncMock(),
    )
    monkeypatch.setattr(
        runtime_module,
        "_resolve_asr_selection",
        lambda _: _selection("qwen", "provider"),
    )
    monkeypatch.setattr(
        runtime_module,
        "_create_asr_session_from_selection",
        lambda *args, **kwargs: replacement,
    )

    def create_real_detector(**kwargs):
        return detector_fixture.DetectorRuntime(
            vad=detector_fixture._Vad(),
            gate=detector_fixture._Gate(),
            **kwargs,
        )

    monkeypatch.setattr(runtime_module, "DetectorRuntime", create_real_detector)
    failure = None
    try:
        assert (
            await _submit_pcm(runtime, turn, sequence=1)
        ).status is AsrSubmitStatus.ACCEPTED
        identity = runtime._capture_runtime_identity(ingress_token=turn.ingress)
        failure = asyncio.create_task(runtime._retire_partial_provider_audio(identity))
        await asyncio.wait_for(invalidation_completed.wait(), timeout=2)
        assert not failure.done()
        start = await asyncio.wait_for(
            runtime.start(route_key="qwen", resource_optimization_enabled=False),
            timeout=3,
        )
        assert start.status is AsrStartStatus.READY
        assert runtime._asr_session is replacement
        replacement_lifecycle = runtime._asr_lifecycle
        replacement_detector = runtime._asr_detector
        replacement_audio = runtime._asr_audio_dispatcher
        replacement_events = runtime._asr_detector_dispatcher
        replacement_transcripts = runtime._asr_transcript_dispatcher
        assert replacement_lifecycle is not lifecycle
        assert replacement_detector is not detector
        replacement_epoch = runtime._asr_session_epoch
        core.send_status.reset_mock()
        resume_failure.set()
        await asyncio.wait_for(failure, timeout=2)
        assert runtime._asr_session is replacement
        assert runtime._asr_lifecycle is replacement_lifecycle
        assert runtime._asr_detector is replacement_detector
        assert runtime._asr_audio_dispatcher is replacement_audio
        assert runtime._asr_detector_dispatcher is replacement_events
        assert runtime._asr_transcript_dispatcher is replacement_transcripts
        assert runtime._asr_session_epoch == replacement_epoch
        replacement.close.assert_not_awaited()
        notices = [
            json.loads(call.args[0]) for call in core.send_status.await_args_list
        ]
        assert not any(item["code"] == "ASR_AUDIO_ORDERING_FAILED" for item in notices)
        assert not any(
            item["code"] == "ASR_LIFECYCLE_STATE"
            and item["details"].get("state") == "blocked"
            for item in notices
        )
    finally:
        resume_failure.set()
        if failure is not None:
            await asyncio.gather(failure, return_exceptions=True)
        await _close_stack(core)
