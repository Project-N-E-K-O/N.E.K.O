"""Correlated diagnostics observe real decisions without becoming authority."""

import asyncio
from collections import deque
from dataclasses import replace
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic.asr_client import runtime as runtime_module
from main_logic.asr_client.pipeline_diagnostics import PipelineDiagnostics, safe_fields
from main_logic.asr_client.admission.contracts import (
    AdmissionDisposition,
    CountDiagnostic,
    ExactIntervalOutcome,
    ExactIntervalTransitionReceipt,
    SpeakerCaptureLeaseToken,
    SpeakerCheckpointKind,
    SpeakerLeaseLow,
    SpeakerLeaseState,
    SpeakerLeaseTransitionOutcome,
    SpeakerLeaseTransitionReceipt,
    SpeakerLow,
)
from main_logic.asr_client.endpointing.detector import DetectorIngressIdentity
from main_logic.asr_client.endpointing.detector_runtime import _VoiceTurnAdapter
from main_logic.asr_client.speaker_shadow.contracts import SpeakerShadowCandidateKey
from main_logic.asr_client.speaker_shadow.diagnostics import SpeakerShadowDiagnostic
from main_logic.voice_turn.contracts import SpeechActivityEvent, EvaluationStatus, VoiceTranscriptEvent
from scripts.check_asr_pipeline_log import summarize, parse_record
from tests.unit.test_asr_voice_turn_adapter import (
    _FakeVad, _UnavailableVad, _FakeGate, _FailingGate, _FakeCoordinator,
    _complete, _incomplete, _failed_evaluation, _eventually,
)
from tests.unit.test_core_independent_asr import _Runtime, _install_ready_lifecycle
from tests.unit.asr_client.test_short_speaker_diagnostics import _join_logs, _interval
from tests.unit.asr_client.test_provider_speaker_continuity import _active_real_stack, _close_stack


def _score_diagnostic(
    candidate: SpeakerShadowCandidateKey,
    *,
    score_id: str,
    evidence_sequence_no: int,
    stage: str = "speaker_score_started",
) -> SpeakerShadowDiagnostic:
    return SpeakerShadowDiagnostic(
        candidate=candidate,
        stage=stage,
        worker_generation=1,
        sample_rate_hz=16_000,
        accepted_sample_count=24_000,
        buffered_sample_count=24_000,
        finish_sample_count=None,
        minimum_sample_count=24_000,
        score_attempt_count=1,
        score_input_sample_count=24_000,
        score_outcome="in_progress" if stage.endswith("started") else "completed",
        scored_sample_count=0,
        last_checkpoint_ms=1_500,
        terminal_reason=None,
        evidence_sequence_no=evidence_sequence_no,
        anchor_applied=True,
        anchor_discard_prefix_sample_count=0,
        scoring_deferred=False,
        score_id=score_id,
    )


def test_projection_and_audio_aggregation_are_bounded():
    core = _Runtime()
    records = []
    observer = PipelineDiagnostics(core._asr_runtime, lambda r, **kw: records.append(r))
    assert safe_fields({"text": "PRIVATE", "reason": "PRIVATE!", "phase": object(), "audio_samples": 3}) == {"audio_samples": 3}
    for _ in range(300):
        observer.audio("audio_received", 1, audio_samples=1600)
    assert len(records) == 1
    observer.flush()
    assert records[-1]["frame_count"] == 300
    assert records[-1]["audio_samples"] == 480000
    assert records[0]["diagnostic_session_ref"] == records[-1]["diagnostic_session_ref"]
    for epoch in range(100):
        observer.audio("audio_received", epoch, audio_samples=1)
    assert len(observer._progress) == 32
    observer.flush()
    assert not observer._progress
    assert "PRIVATE" not in json.dumps(records)


def test_broken_observer_cannot_raise():
    def broken(*args, **kwargs):
        raise OSError("PRIVATE")
    observer = PipelineDiagnostics(_Runtime()._asr_runtime, broken)
    observer.event("hello", 1, reason="test")
    observer.event("unsafe value!", 1)
    observer.audio("audio_received", 1, audio_samples=10)
    observer.flush()


@pytest.mark.parametrize("mode", ["complete", "incomplete", "unavailable", "error", "stale", "discarded", "superseded", "cancelled", "broken_sink"])
async def test_smart_turn_result_logs_captured_identity_and_preserves_commit(mode):
    core = _Runtime()
    ingress = core._capture_ingress_token()
    captured = DetectorIngressIdentity(ingress, 7, 11)
    result = {"incomplete": _incomplete(), "unavailable": _failed_evaluation(EvaluationStatus.UNAVAILABLE),
              "error": _failed_evaluation(EvaluationStatus.ERROR), "stale": _failed_evaluation(EvaluationStatus.STALE)}.get(mode, _complete())
    coordinator = _FakeCoordinator([result], block_evaluation=True)
    coordinator.generation = 0
    coordinator.activity_seq = 1
    coordinator.evaluation_threshold = .5
    commit = AsyncMock()
    adapter = _VoiceTurnAdapter(
        vad=_FakeVad(), gate=_FakeGate([(SpeechActivityEvent.CANDIDATE_PAUSE,)]),
        coordinator=coordinator, on_commit=commit, smart_turn_required=True,
    )
    records = []
    def observe(fields, token):
        if mode == "broken_sink":
            raise OSError("PRIVATE")
        records.append((fields, token))
    adapter._on_pipeline_diagnostic = observe
    try:
        await adapter.start()
        await adapter.push_audio(generation=1, buffer_epoch=2, utterance_id=3, pcm16=b"\x01\x00" * 1600, detector_identity=captured)
        await coordinator.evaluate_started.wait()
        if mode == "discarded":
            coordinator.generation += 1
        elif mode == "superseded":
            coordinator.activity_seq += 1
        elif mode == "cancelled":
            await adapter.reset(generation=2, buffer_epoch=2, utterance_id=4)
        coordinator.evaluate_release.set()
        if mode != "broken_sink":
            await _eventually(lambda: any(r[0]["phase"] == "evaluation_result" for r in records))
            evaluations = [r for r in records if r[0]["phase"] == "evaluation_result"]
            assert evaluations[-1][0]["outcome"] == mode
            assert evaluations[-1][0]["semantic_turn_id"] == 3
            assert evaluations[-1][0]["sequence_no"] == 11
            assert evaluations[-1][1] is ingress
            assert "PRIVATE" not in json.dumps([r[0] for r in records])
        if mode in {"complete", "broken_sink"}:
            await _eventually(lambda: commit.await_count == 1)
        else:
            assert commit.await_count == 0
    finally:
        coordinator.evaluate_release.set()
        await adapter.close()


@pytest.mark.parametrize("failure", ["vad_load", "vad_feed"])
async def test_vad_failure_records_degradation_before_periodic_smart_turn(failure):
    core = _Runtime()
    ingress = core._capture_ingress_token()
    records = []
    commit = AsyncMock()
    adapter = _VoiceTurnAdapter(
        vad=_UnavailableVad() if failure == "vad_load" else _FakeVad(),
        gate=_FailingGate() if failure == "vad_feed" else _FakeGate(),
        coordinator=_FakeCoordinator([_complete()]), on_commit=commit, smart_turn_required=True,
    )
    adapter._on_pipeline_diagnostic = lambda fields, token: records.append(fields)
    try:
        await adapter.start()
        for sequence in range(1, 7):
            await adapter.push_audio(generation=1, buffer_epoch=0, utterance_id=1,
                pcm16=b"\x01\x00" * 1600, detector_identity=DetectorIngressIdentity(ingress, 0, sequence))
        await _eventually(lambda: commit.await_count == 1)
        assert any(r["phase"] == failure for r in records)
        assert any(r.get("reason") == "periodic_no_vad" for r in records)
    finally:
        await adapter.close()


@pytest.mark.parametrize("mode", ["submitted", "empty", "rejected", "swap_timeout", "cancelled", "failed"])
async def test_core_terminal_records_distinguish_request_from_reply(monkeypatch, mode):
    core = _Runtime()
    _install_ready_lifecycle(core)
    runtime = core._asr_runtime
    logs = []
    monkeypatch.setattr(runtime_module.asr_diagnostic_logger, "info", lambda _, r: logs.append(r))
    turn = runtime._capture_turn_token(core._asr_lifecycle)
    event = VoiceTranscriptEvent(turn, "qwen", "" if mode == "empty" else "PRIVATE")
    if mode == "rejected":
        core.handle_input_transcript.return_value = False
    elif mode in {"cancelled", "failed"}:
        core.session.create_response.side_effect = asyncio.CancelledError() if mode == "cancelled" else RuntimeError("PRIVATE")
    if mode == "swap_timeout":
        core._core_voice_session_swap_barrier_timeout_s = .01
        await core._core_voice_session_swap_lock.acquire()
    try:
        if mode in {"cancelled", "failed"}:
            with pytest.raises(asyncio.CancelledError if mode == "cancelled" else RuntimeError):
                await core._dispatch_core_asr_transcript(event)
        else:
            await core._dispatch_core_asr_transcript(event)
        await _join_logs(runtime)
        terminal = [r for r in logs if r.get("stage") == "core_voice_delivery"][-1]
        assert terminal["outcome"] == (mode if mode in {"submitted", "cancelled", "failed"} else "abandoned")
        assert terminal["turn_id"] == turn.turn_id
        assert "PRIVATE" not in json.dumps(logs)
    finally:
        if core._core_voice_session_swap_lock.locked():
            core._core_voice_session_swap_lock.release()
        await runtime.close()


async def test_real_provider_pipeline_report_and_old_log_gaps(monkeypatch):
    logs = []
    monkeypatch.setattr(runtime_module.asr_diagnostic_logger, "info", lambda _, r: logs.append(r))
    core, runtime, detector, shadow, lifecycle, session, turn = await _active_real_stack(score=.78)
    core.continuity_score_host.ready.set()
    try:
        await _interval(runtime, shadow, turn, 1600)
        await core._voice_input_registry.wait_idle()
        await _join_logs(runtime)
        report = summarize("ASR resolution " + repr(r) for r in logs)
        current = report["sessions"][0]
        assert current["coverage"]["audio_input"] == "observed"
        assert current["coverage"]["audio_write"] == "observed"
        assert current["turns"][-1]["core_outcome"] == "submitted"
        assert core.session.create_response.await_count == 1
        assert "PRIVATE" not in json.dumps(report)
        old = summarize(["ASR resolution " + repr({"diagnostic_session_ref": "a" * 24, "stage": "provider_final_received", "text": "PRIVATE"})])
        assert old["sessions"][0]["coverage"]["audio_input"] == "not_observed"
        assert old["sessions"][0]["turns"] == []
    finally:
        await _close_stack(core)


async def test_real_score_chain_remains_separate_from_final_text_decision(monkeypatch):
    logs = []
    monkeypatch.setattr(
        runtime_module.asr_diagnostic_logger, "info", lambda _, record: logs.append(record)
    )
    core, runtime, detector, shadow, lifecycle, session, turn = await _active_real_stack(
        score=.78
    )
    core.continuity_score_host.ready.set()
    try:
        await _interval(runtime, shadow, turn, 1600)
        await core._voice_input_registry.wait_idle()
        await _join_logs(runtime)
        session_report = summarize(
            "ASR resolution " + repr(record) for record in logs
        )["sessions"][0]
        assert len(session_report["scores"]) == 1
        score = session_report["scores"][0]
        assert score["score_outcome"] == "completed"
        assert score["quality"]["status"] == "measured"
        assert score["scored_interval"]["relative_end_sample"] == 24_000
        assert score["evidence_observation"] == "observed"
        assert score["final_text_decision"] == "forward"
        assert score["final_text_observation"] == "observed"
        assert not score["correlation_conflicts"]
    finally:
        await _close_stack(core)


def test_checker_missing_truncated_dropped_and_untrusted_records():
    assert parse_record("ASR resolution __import__('os').system('PRIVATE')") is None
    assert parse_record("ASR resolution {not valid}") is None
    assert parse_record("other line") is None
    records = ["ASR resolution " + repr({"diagnostic_session_ref": "a" * 24, "stage": "asr_lifecycle",
               "endpoint_authority": "provider", "diagnostic_records_dropped": 1})]
    report = summarize(records)
    assert report["sessions"][0]["log_gaps"]
    assert report["sessions"][0]["coverage"]["smart_turn"] == "not_applicable"
    assert summarize(records * 4, max_records=2)["sessions"][0]["log_gaps"]
    assert summarize(records + [records[0].replace("a" * 24, "b" * 24)], max_sessions=1)["sessions_truncated"]


def test_checker_preserves_first_landmarks_when_noisy_tail_is_truncated():
    ref = "a" * 24
    records = [
        {"diagnostic_session_ref": ref, "session_epoch": 1,
         "stage": "audio_received", "frame_count": 1},
        {"diagnostic_session_ref": ref, "session_epoch": 1,
         "stage": "speaker_verifier_installation", "phase": "entry",
         "installation_trace_ref": "c" * 32, "installation_initiator": "core_route_start",
         "installation_reason": "route_ready", "reason": "reconcile_requested"},
        {"diagnostic_session_ref": ref, "session_epoch": 1,
         "stage": "speaker_verifier_installation", "phase": "entry",
         "installation_trace_ref": "b" * 32, "installation_initiator": "activation_prepare",
         "installation_reason": "configuration_replace", "reason": "reconcile_requested"},
        {"diagnostic_session_ref": ref, "session_epoch": 1,
         "stage": "provider_state_change", "operation": "retirement_validation",
         "reason": "accounting_retirement_unproven", "outcome": "rejected",
         "proof_present": False},
    ]
    records.extend(
        {"diagnostic_session_ref": ref, "session_epoch": 1,
         "stage": "provider_state_change", "operation": "evidence_alias_consume",
         "reason": "evidence_alias_consume", "outcome": "rejected",
         "coalesced_count": sequence}
        for sequence in range(600)
    )
    records.append(
        {"diagnostic_session_ref": ref, "session_epoch": 2,
         "stage": "speaker_verifier_installation", "phase": "result",
         "installation_trace_ref": "b" * 32, "decision": "install_missing",
         "reason": "install_completed", "outcome": "installed"}
    )
    session = summarize(
        ("ASR resolution " + repr(record) for record in records), max_records=32,
    )["sessions"][0]
    assert session["log_gaps"] is True
    assert session["records_omitted"] == len(records) - 32
    assert session["session_epoch"] == 2
    assert session["coverage"]["audio_input"] == "observed"
    findings = session["session_findings"]
    assert any(r.get("phase") == "entry" and r.get("installation_initiator") == "activation_prepare" for r in findings)
    assert any(r.get("operation") == "retirement_validation" and r.get("proof_present") is False for r in findings)
    assert any(r.get("phase") == "result" and r.get("outcome") == "installed" for r in findings)
    assert len(findings) <= 32


def test_checker_correlates_score_input_result_evidence_and_final_decision():
    ref = "a" * 24
    score_id = "provider_candidate_4_9_1"
    context = {
        "diagnostic_session_ref": ref,
        "session_epoch": 2,
        "turn_id": 7,
        "provider_generation": 1,
        "provider_buffer_epoch": 3,
        "provider_utterance_id": 5,
        "provider_start_sample_16k": 1_000,
        "detector_epoch": 4,
        "shadow_generation": 9,
        "candidate_scope": "provider_candidate",
        "sample_rate_hz": 16_000,
        "score_id": score_id,
        "score_checkpoint_kind": "checkpoint",
        "score_checkpoint_ms": 1_500,
        "score_window_start_sample": 0,
        "score_window_end_sample": 24_000,
        "score_duration_ms": 1_500,
        "score_input_sample_count": 24_000,
        "score_continuity": "unknown",
        "trimmed_prefix_sample_count": 0,
        "profile_generation_ref": "1" * 16,
        "activation_generation_ref": "2" * 16,
        "installation_ref": "3" * 16,
        "model_version": "campplus_v1_0_0",
        "scoring_rule_version": "owner_voice_v1",
        "quality_summary_outcome": "measured",
        "quality_summary_version": "pcm16_quality_v1",
        "rms_milli": 125,
        "peak_milli": 500,
        "near_silence_ratio_milli": 250,
        "clipping_ratio_milli": 0,
        "near_silence_threshold_milli": 10,
        "clipping_threshold_milli": 1_000,
        "voice_activity_measurement": "not_measured",
    }
    records = [
        {**context, "stage": "speaker_score_started", "score_outcome": "in_progress",
         "evidence_sequence_no": 0},
        {**context, "stage": "speaker_score_finished", "score_outcome": "completed",
         "evidence_sequence_no": 1},
        {"diagnostic_session_ref": ref, "session_epoch": 2, "turn_id": 7,
         "provider_generation": 1, "provider_buffer_epoch": 3,
         "provider_utterance_id": 5, "stage": "speaker_fact_observed",
         "speaker_sequence_no": 1, "speaker_classification": "high"},
        {"diagnostic_session_ref": ref, "session_epoch": 2, "turn_id": 7,
         "provider_generation": 1, "provider_buffer_epoch": 3,
         "provider_utterance_id": 5, "stage": "provider_final_received"},
        {"diagnostic_session_ref": ref, "session_epoch": 2, "turn_id": 7,
         "stage": "admission_decision", "disposition": "forward",
         "reason_code": "ASR_SPEAKER_VERIFIED"},
    ]
    score = summarize(
        "ASR resolution " + repr(record) for record in records
    )["sessions"][0]["scores"][0]
    assert score["score_id"] == score_id
    assert score["start_observation"] == "observed"
    assert score["end_observation"] == "observed"
    assert score["score_outcome"] == "completed"
    assert score["scored_interval"] == {
        "relative_status": "known",
        "relative_start_sample": 0,
        "relative_end_sample": 24_000,
        "timeline_status": "known",
        "timeline_start_sample_16k": 1_000,
        "timeline_end_sample_16k": 25_000,
    }
    assert score["quality"]["status"] == "measured"
    assert score["quality"]["voice_activity"] == {
        "status": "not_measured", "ratio_milli": None,
    }
    assert score["evidence_observation"] == "observed"
    assert score["speaker_classification"] == "high"
    assert score["final_text_observation"] == "observed"
    assert score["final_text_decision"] == "forward"
    assert score["final_text_reason"] == "ASR_SPEAKER_VERIFIED"
    assert not score["correlation_conflicts"]


def test_checker_reports_missing_end_unknown_timeline_and_score_conflict():
    ref = "a" * 24
    base = {
        "diagnostic_session_ref": ref,
        "session_epoch": 1,
        "stage": "speaker_score_started",
        "score_id": "provider_candidate_1_2_1",
        "score_checkpoint_kind": "checkpoint",
        "score_checkpoint_ms": 1_500,
        "score_window_start_sample": 0,
        "score_window_end_sample": 24_000,
        "sample_rate_hz": 16_000,
        "quality_summary_outcome": "unavailable",
        "quality_summary_version": "pcm16_quality_v1",
        "diagnostic_records_dropped": 2,
    }
    records = [base, {**base, "score_window_end_sample": 48_000}]
    session = summarize(
        ("ASR resolution " + repr(record) for record in records), max_records=8,
    )["sessions"][0]
    score = session["scores"][0]
    assert score["end_observation"] == "not_observed"
    assert score["score_outcome"] == "not_observed"
    assert score["scored_interval"]["timeline_status"] == "unknown"
    assert score["quality"]["status"] == "not_measured"
    assert score["duplicate_start_count"] == 1
    assert score["correlation_conflicts"] == ["score_window_end_sample"]
    assert session["log_integrity"] == {
        "diagnostic_drop_observed": True,
        "records_truncated": False,
        "score_correlation_conflicts": 1,
    }


def test_checker_keeps_authoritative_evidence_and_final_verdict_separate():
    ref = "a" * 24
    base = {
        "diagnostic_session_ref": ref,
        "session_epoch": 1,
        "score_id": "provider_candidate_1_2_1",
        "detector_epoch": 1,
        "shadow_generation": 2,
        "candidate_scope": "provider",
    }
    records = [
        {**base, "stage": "speaker_score_started", "evidence_sequence_no": 0},
        {
            **base,
            "stage": "speaker_evidence_disposition",
            "evidence_sequence_no": 1,
            "evidence_path": "provisional_ledger",
            "evidence_disposition": "accepted",
            "reason": "appended",
        },
        {
            **base,
            "stage": "speaker_evidence_disposition",
            "evidence_sequence_no": 1,
            "evidence_path": "exact_interval",
            "evidence_disposition": "rejected_acceptance",
            "reason": "conflict",
        },
    ]
    score = summarize(
        "ASR resolution " + repr(record) for record in records
    )["sessions"][0]["scores"][0]
    assert score["end_observation"] == "not_observed"
    assert score["evidence_disposition_observation"] == "observed"
    assert score["evidence_disposition"] == "rejected_acceptance"
    assert score["evidence_disposition_history"] == [
        {
            "path": "provisional_ledger",
            "disposition": "accepted",
            "reason": "appended",
        },
        {
            "path": "exact_interval",
            "disposition": "rejected_acceptance",
            "reason": "conflict",
        },
    ]
    assert score["final_text_observation"] == "not_observed"
    assert score["final_text_decision"] == "not_observed"


def test_provisional_ledger_dispositions_come_from_actual_mutation(monkeypatch):
    core = _Runtime()
    runtime = core._asr_runtime
    runtime._ensure_asr_runtime_state()
    candidate = SpeakerShadowCandidateKey(1, 2, "provider_candidate")
    ledger = SimpleNamespace(
        state=runtime_module._ProviderSpeakerLedgerState.ANCHORED_SCORING,
        poisoned_reason=None,
        candidate=candidate,
        close_event=None,
        event_by_sequence={},
        last_speaker_sequence_no=0,
        events=deque(),
    )
    emitted = []
    monkeypatch.setattr(
        runtime, "_schedule_asr_diagnostic_metadata",
        lambda metadata, **_kwargs: emitted.append(metadata),
    )
    monkeypatch.setattr(
        runtime,
        "_poison_provider_speaker_ledger",
        lambda owner, reason: setattr(owner, "poisoned_reason", reason),
    )

    def remember(score_id, prior_sequence):
        runtime._remember_speaker_score_correlation(
            _score_diagnostic(
                candidate, score_id=score_id,
                evidence_sequence_no=prior_sequence,
            ),
            activation_generation="activation",
            session_epoch=7,
            provider_key=None,
            turn_id=None,
        )

    remember("score_accepted", 0)
    assert runtime._record_provider_provisional_speaker_event(
        ledger, SpeakerLeaseLow(candidate, 1, SpeakerCheckpointKind.FIRST)
    )
    remember("score_rejected", 2)
    assert runtime._record_provider_provisional_speaker_event(
        ledger, SpeakerLeaseLow(candidate, 3, SpeakerCheckpointKind.FIRST)
    )
    ledger.poisoned_reason = None
    ledger.state = runtime_module._ProviderSpeakerLedgerState.RESOLVED
    remember("score_expired", 3)
    assert not runtime._record_provider_provisional_speaker_event(
        ledger, SpeakerLeaseLow(candidate, 4, SpeakerCheckpointKind.FIRST)
    )
    assert [item["evidence_disposition"] for item in emitted] == [
        "accepted", "rejected_acceptance", "expired",
    ]
    assert [item["reason"] for item in emitted] == [
        "appended", "sequence_gap", "ledger_terminal",
    ]


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (SpeakerLeaseTransitionOutcome.APPLIED, "accepted"),
        (SpeakerLeaseTransitionOutcome.STALE, "expired"),
        (SpeakerLeaseTransitionOutcome.CONFLICT, "rejected_acceptance"),
    ],
)
def test_typed_lease_receipt_maps_authoritative_disposition(
    monkeypatch, outcome, expected,
):
    core = _Runtime()
    runtime = core._asr_runtime
    candidate = SpeakerShadowCandidateKey(1, 2, "provider_candidate")
    runtime._remember_speaker_score_correlation(
        _score_diagnostic(candidate, score_id="score_lease", evidence_sequence_no=0),
        activation_generation="activation",
        session_epoch=4,
        provider_key=None,
        turn_id=None,
    )
    emitted = []
    monkeypatch.setattr(
        runtime, "_schedule_asr_diagnostic_metadata",
        lambda metadata, **_kwargs: emitted.append(metadata),
    )
    receipt = SpeakerLeaseTransitionReceipt(
        lease_token=SpeakerCaptureLeaseToken(1, 1, 1, 1, 1),
        before_state=SpeakerLeaseState.COLLECTING,
        after_state=SpeakerLeaseState.COLLECTING,
        outcome=outcome,
        terminal_sequence_no=None,
        capture_through_sequence_no=None,
        frozen_children=(),
        child_results=(),
        diagnostics=(),
    )
    runtime._schedule_speaker_lease_disposition(
        SpeakerLeaseLow(candidate, 1, SpeakerCheckpointKind.FIRST), receipt,
    )
    assert emitted[-1]["evidence_disposition"] == expected
    assert emitted[-1]["reason"] == outcome.value


def test_late_score_callback_uses_only_original_correlation(monkeypatch):
    core = _Runtime()
    runtime = core._asr_runtime
    candidate = SpeakerShadowCandidateKey(1, 2, "provider_candidate")
    started = _score_diagnostic(
        candidate, score_id="score_late", evidence_sequence_no=0,
    )
    runtime._remember_speaker_score_correlation(
        started,
        activation_generation="old_activation",
        session_epoch=3,
        provider_key=None,
        turn_id=None,
    )
    runtime._speaker_verifier_activation_generation = "new_activation"
    runtime._asr_session_epoch = 99
    emitted = []
    monkeypatch.setattr(
        runtime, "_schedule_asr_diagnostic_metadata",
        lambda metadata, **_kwargs: emitted.append(metadata),
    )
    runtime._accept_speaker_diagnostic(
        replace(started, stage="speaker_score_finished", score_outcome="completed"),
        activation_generation="old_activation",
        source=object(),
    )
    assert len(emitted) == 1
    stale = emitted[0]
    assert stale["stage"] == "speaker_score_stale"
    assert stale["session_epoch"] == 3
    assert stale["reason"] == "activation_replaced"
    assert stale["score_id"] == "score_late"
    assert not any(key.startswith("provider_") or key == "turn_id" for key in stale)


@pytest.mark.parametrize(
    ("effects", "expected", "reason"),
    [
        (
            (CountDiagnostic("admission_stale_speaker_fact"),),
            "expired",
            "stale_reducer_result",
        ),
        ((), "not_observed", "ambiguous_reducer_result"),
    ],
)
async def test_direct_ingress_reports_only_what_reducer_result_proves(
    monkeypatch, effects, expected, reason,
):
    core = _Runtime()
    _install_ready_lifecycle(core)
    runtime = core._asr_runtime
    runtime._ensure_asr_runtime_state()
    turn = runtime._capture_turn_token(core._asr_lifecycle)
    candidate = SpeakerShadowCandidateKey(1, 2, "smart_turn_turn")
    runtime._remember_speaker_score_correlation(
        _score_diagnostic(candidate, score_id="score_direct", evidence_sequence_no=0),
        activation_generation="activation",
        session_epoch=turn.ingress.session_epoch,
        provider_key=None,
        turn_id=turn.turn_id,
    )
    emitted = []
    monkeypatch.setattr(
        runtime, "_schedule_asr_diagnostic_metadata",
        lambda metadata, **_kwargs: emitted.append(metadata),
    )
    monkeypatch.setattr(
        runtime._asr_admission_ingress, "retire_turn", AsyncMock(return_value=None),
    )
    future = asyncio.get_running_loop().create_future()
    future.set_result(effects)
    await runtime._consume_admission_future(
        turn,
        future,
        speaker_fact=SpeakerLow(
            candidate, 1, SpeakerCheckpointKind.FIRST,
        ),
    )
    assert emitted[-1]["evidence_disposition"] == expected
    assert emitted[-1]["reason"] == reason


@pytest.mark.parametrize(
    ("outcome", "expected"),
    [
        (ExactIntervalOutcome.RESOLVED, "accepted"),
        (ExactIntervalOutcome.STALE, "expired"),
        (ExactIntervalOutcome.CONFLICT, "rejected_acceptance"),
        (ExactIntervalOutcome.HELD, "not_observed"),
    ],
)
async def test_exact_receipt_reports_only_authoritative_disposition(
    monkeypatch, outcome, expected,
):
    core = _Runtime()
    runtime = core._asr_runtime
    candidate = SpeakerShadowCandidateKey(1, 2, "provider_candidate")
    runtime._remember_speaker_score_correlation(
        _score_diagnostic(candidate, score_id="score_exact", evidence_sequence_no=0),
        activation_generation="activation",
        session_epoch=5,
        provider_key=None,
        turn_id=None,
    )
    emitted = []
    monkeypatch.setattr(
        runtime, "_schedule_asr_diagnostic_metadata",
        lambda metadata, **_kwargs: emitted.append(metadata),
    )
    monkeypatch.setattr(
        runtime, "_exact_interval_evidence_owner_is_current", lambda _owner: True,
    )
    monkeypatch.setattr(
        runtime, "_speaker_exact_installation_is_current", lambda _owner: True,
    )
    monkeypatch.setattr(
        runtime, "_execute_exact_admission_effects", AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        runtime, "_fail_exact_interval_group", AsyncMock(return_value=None),
    )
    receipt = ExactIntervalTransitionReceipt(
        interval_id=1,
        outcome=outcome,
        disposition=(
            AdmissionDisposition.FORWARD
            if outcome is ExactIntervalOutcome.RESOLVED else None
        ),
        effects=(),
    )
    future = asyncio.get_running_loop().create_future()
    future.set_result(receipt)
    runtime._asr_admission_ingress = SimpleNamespace(
        post_exact_interval_nowait=lambda *_args, **_kwargs: future,
    )
    transaction = SimpleNamespace(resolved_disposition=None, activation=object())
    await runtime._apply_exact_interval_event(
        transaction,
        SpeakerLeaseLow(candidate, 1, SpeakerCheckpointKind.FIRST),
    )
    assert emitted[-1]["evidence_disposition"] == expected


def test_checker_never_merges_reused_turn_ids_across_routes():
    base = {"diagnostic_session_ref": "a" * 24, "stage": "core_voice_delivery", "turn_id": 1,
            "audio_generation": 0, "lease_generation": 1, "outcome": "submitted"}
    records = [{**base, "route_generation": 1}, {**base, "route_generation": 2, "outcome": "abandoned"},
               {"diagnostic_session_ref": "a" * 24, "turn_id": 1, "stage": "admission_decision", "disposition": "forward"}]
    session = summarize("ASR resolution " + repr(r) for r in records)["sessions"][0]
    assert session["ambiguous_partial_turn_ids"] == [1]
    assert len(session["turns"]) == 2
    assert [r["core_outcome"] for r in session["turns"]] == ["submitted", "abandoned"]
    assert all(r["admission"] == "not_observed" for r in session["turns"])


async def test_smart_turn_speaker_diagnostics_require_current_binding(monkeypatch):
    from main_logic.asr_client.speaker_shadow.contracts import SpeakerShadowCandidateKey
    from main_logic.asr_client.speaker_shadow.diagnostics import SpeakerShadowDiagnostic
    core = _Runtime()
    _install_ready_lifecycle(core)
    runtime = core._asr_runtime
    logs = []
    monkeypatch.setattr(runtime_module.asr_diagnostic_logger, "info", lambda _, r: logs.append(r))
    source = object()
    runtime._asr_detector._speaker_shadow = source
    runtime._speaker_verifier_activation_generation = "test-generation"
    candidate = SpeakerShadowCandidateKey(0, 1, "smart_turn_turn")
    turn = runtime._capture_turn_token(core._asr_lifecycle)
    event = SpeakerShadowDiagnostic(candidate, "speaker_score_completed", 0, 16000, 24000,
        24000, None, 24000, 1, 24000, "completed", 24000, 1500, None, 1, False, None, False)
    try:
        runtime._accept_speaker_diagnostic(event, activation_generation="test-generation", source=source)
        await _join_logs(runtime)
        assert not logs
        runtime._asr_admission_candidate_turns[candidate] = turn
        runtime._accept_speaker_diagnostic(event, activation_generation="test-generation", source=source)
        await _join_logs(runtime)
        assert logs[-1]["candidate_role"] == "smart_turn"
        assert logs[-1]["turn_id"] == turn.turn_id
        runtime._asr_admission_candidate_turns[candidate] = replace(turn, ingress=replace(turn.ingress, audio_generation=99))
        runtime._accept_speaker_diagnostic(event, activation_generation="test-generation", source=source)
        await _join_logs(runtime)
        assert len(logs) == 1
    finally:
        runtime._asr_detector._speaker_shadow = None
        await runtime.close()


async def test_endpoint_callback_keeps_original_semantic_identity(monkeypatch):
    from main_logic.asr_client.endpointing.detector_runtime import DetectorRuntime
    from tests.unit.test_asr_detector_runtime import _smart_turn_policy
    core = _Runtime()
    _install_ready_lifecycle(core)
    runtime = core._asr_runtime
    detector = DetectorRuntime(vad=_FakeVad(), gate=_FakeGate(), provider_policy=_smart_turn_policy(), coordinator=_FakeCoordinator(), on_event=AsyncMock())
    runtime._asr_detector = detector
    ingress = core._capture_ingress_token()
    logs = []
    monkeypatch.setattr(runtime_module.asr_diagnostic_logger, "info", lambda _, r: logs.append(r))
    detector.set_pipeline_diagnostic_callback(lambda fields, token: runtime._observe_endpoint_diagnostic(fields, token, source=detector, epoch=ingress.session_epoch))
    try:
        callback = detector._semantic_adapter._on_pipeline_diagnostic
        callback({"phase": "evaluation_result", "semantic_turn_id": 5, "outcome": "stale"}, ingress)
        await _join_logs(runtime)
        assert logs[-1]["semantic_turn_id"] == 5
        callback({"phase": "evaluation_result"}, None)
        callback({"phase": "evaluation_result"}, replace(ingress, session_epoch=99))
        runtime._observe_endpoint_diagnostic({}, ingress, source=object(), epoch=ingress.session_epoch)
        await _join_logs(runtime)
        assert len(logs) == 1
    finally:
        await runtime.close()


@pytest.mark.parametrize("mode", ["missing_lifecycle", "deny_fenced", "ingress_stale", "feed_error"])
async def test_audio_exit_reason_explains_non_delivery(monkeypatch, mode):
    from main_logic.asr_client.runtime import DenyTransportState
    from tests.unit.asr_client.test_provider_speaker_continuity import _submit_pcm
    core, runtime, detector, shadow, lifecycle, session, turn = await _active_real_stack(score=.95)
    logs = []
    monkeypatch.setattr(runtime_module.asr_diagnostic_logger, "info", lambda _, r: logs.append(r))
    try:
        if mode == "missing_lifecycle":
            runtime._asr_lifecycle = None
        elif mode == "deny_fenced":
            runtime._asr_deny_transport_state = DenyTransportState.DENY_FENCED
        elif mode == "ingress_stale":
            turn = replace(turn, ingress=replace(turn.ingress, audio_generation=99))
        else:
            monkeypatch.setattr(detector, "feed", AsyncMock(side_effect=RuntimeError("PRIVATE")))
        result = await _submit_pcm(runtime, turn, sequence=1)
        await _join_logs(runtime)
        events = [r for r in logs if r.get("stage") == "audio_submit"]
        assert events
        expected = {"missing_lifecycle": "unavailable", "deny_fenced": "accepted", "ingress_stale": "stale", "feed_error": "unavailable"}[mode]
        assert result.status.value == expected
        assert events[-1]["outcome"] == expected
        assert events[-1]["reason"] in {mode, "handoff_stale", "submit_exception"}
        assert core.session.create_response.await_count == 0
        assert "PRIVATE" not in json.dumps(logs)
    finally:
        await _close_stack(core)


def test_checker_cli_writes_only_safe_report(tmp_path, monkeypatch):
    import sys
    from scripts.check_asr_pipeline_log import main
    source = tmp_path / "main.log"
    target = tmp_path / "report.json"
    record = {"diagnostic_session_ref": "a" * 24, "source_session_epoch": 1,
              "reason_code": "ASR_TEST_FAILED", "secret": "PRIVATE"}
    source.write_text("ASR incident " + repr(record), encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["check_asr_pipeline_log", str(source), "--output", str(target)])
    main()
    output = target.read_text(encoding="utf-8")
    assert "PRIVATE" not in output
    assert json.loads(output)["sessions"][0]["session_findings"][0]["reason_code"] == "ASR_TEST_FAILED"


@pytest.mark.parametrize("failure", ["overflow", "unavailable", "error", "cancelled", "cancelled_before_start"])
async def test_pipeline_writer_bound_failures_are_visible(monkeypatch, failure):
    from concurrent.futures import Future
    core = _Runtime()
    runtime = core._asr_runtime
    batches = []
    blocked = Future()
    def submit(records, *, kind):
        assert kind == "pipeline"
        assert len(records) <= 16
        batches.append(records)
        if failure == "unavailable":
            return None
        if failure == "error":
            raise OSError("PRIVATE")
        return blocked
    monkeypatch.setattr(runtime_module, "submit_resolution_log", submit)
    try:
        for _ in range(100 if failure == "overflow" else 1):
            runtime._schedule_pipeline_session_event("test_event", 0, outcome="started")
        assert len(runtime._asr_pipeline_pending) <= 32
        assert len([t for t in runtime._asr_close_tasks if t.get_name() == "asr-pipeline-log"]) == 1
        if failure == "cancelled_before_start":
            runtime._asr_pipeline_log_task.cancel()
        await asyncio.sleep(0)
        if failure in {"cancelled", "cancelled_before_start"}:
            runtime._asr_pipeline_log_task.cancel()
            await asyncio.gather(runtime._asr_pipeline_log_task, return_exceptions=True)
        elif failure == "overflow":
            blocked.set_result(None)
            await _join_logs(runtime)
        else:
            await _join_logs(runtime)
        assert runtime._asr_resolution_log_dropped > 0
        assert len(runtime._asr_pipeline_pending) == 0
        assert "PRIVATE" not in json.dumps(batches)
    finally:
        if not blocked.done():
            blocked.set_result(None)
        await runtime.close()
