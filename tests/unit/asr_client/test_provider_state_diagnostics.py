"""Reset provenance explains stale proof rejection without changing authority."""

import asyncio
from dataclasses import replace
import json

import pytest

from main_logic.asr_client.pipeline_diagnostics import STATE_DIAGNOSTIC_FIELDS
from main_logic.asr_client.speaker_diagnostics import diagnostic_context
from main_logic.voice_turn.contracts import AsrSubmitStatus
from scripts.check_asr_pipeline_log import STATE_DIAGNOSTIC_FIELDS as CHECKER_FIELDS, summarize
from tests.unit.asr_client.test_provider_speaker_continuity import (
    _active_real_stack, _submit_pcm, _close_stack,
)


def _capture(monkeypatch, runtime, detector):
    records = []
    monkeypatch.setattr(runtime, "_schedule_pipeline_metadata", lambda fields, **kw: records.append(fields))
    runtime._install_provider_state_diagnostics(detector)
    return records


async def test_timeline_reset_explains_retained_proof_rejection(monkeypatch):
    core, runtime, detector, _, _, _, turn = await _active_real_stack()
    records = _capture(monkeypatch, runtime, detector)
    try:
        assert (await _submit_pcm(runtime, turn, sequence=1)).status is AsrSubmitStatus.ACCEPTED
        lease = runtime._asr_provider_speaker_evidence_lease
        runtime._poison_provider_speaker_ledger(
            runtime._asr_provider_speaker_ledgers[lease.candidate], "speaker_capture_unavailable",
        )
        await detector.abandon_provider_speaker_evidence_lease(lease)
        assert await runtime._reset_detector_with_diagnostics(
            detector, initiator="restart_transport", reason="transport_reconnect", timeline_only=True,
        )
        assert (await _submit_pcm(runtime, turn, sequence=2)).status is AsrSubmitStatus.UNAVAILABLE
        reset = next(r for r in records if r.get("operation") == "segment_state_clear")
        assert reset["initiator"] == "reset_provider_audio_timeline"
        assert reset["reset_initiator"] == "restart_transport"
        assert reset["reset_reason"] == "transport_reconnect"
        assert reset["after_timeline_generation"] == reset["before_timeline_generation"] + 1
        assert reset["before_retirement_count"] == reset["after_retirement_count"] == 1
        rejection = next(r for r in records if r.get("operation") == "retirement_validation")
        assert rejection["proof_present"] is True
        assert rejection["proof_epoch_matches"] is True
        assert rejection["proof_timeline_matches"] is False
        assert rejection["requested_lease_generation"] == lease.lease_generation
        assert rejection["proof_timeline_generation"] == reset["before_timeline_generation"]
        assert rejection["diagnostic_session_ref"] == reset["diagnostic_session_ref"]
        cleanup = next(r for r in records if r.get("operation") == "turn_state_clear")
        assert cleanup["initiator"] == "handle_independent_asr_error"
        assert cleanup["reason"] == "asr_audio_ordering_failed"
        assert cleanup["diagnostic_session_ref"] == rejection["diagnostic_session_ref"]
        assert cleanup["before_session_epoch"] > cleanup["session_epoch"]
        report = summarize("ASR resolution " + repr(r) for r in records)
        findings = next(s for s in report["sessions"] if s["session_ref"] == reset["diagnostic_session_ref"])["session_findings"]
        assert any(r.get("proof_timeline_matches") is False for r in findings)
    finally:
        await _close_stack(core)


@pytest.mark.parametrize("mode", ["accepted", "forged", "superseded"])
async def test_alias_cleanup_logs_before_after_and_rejected_owner(monkeypatch, mode):
    core, runtime, detector, _, _, _, turn = await _active_real_stack()
    records = _capture(monkeypatch, runtime, detector)
    try:
        await _submit_pcm(runtime, turn, sequence=1)
        lease = runtime._asr_provider_speaker_evidence_lease
        identity = runtime._capture_runtime_identity(ingress_token=turn.ingress)
        await detector.abandon_provider_speaker_evidence_lease(lease)
        settlement = await detector.confirm_provider_speaker_evidence_retirement(lease)
        if mode == "forged":
            settlement = replace(settlement)
        if mode == "superseded":
            runtime._asr_session_epoch += 1
        result = runtime._consume_provider_speaker_evidence_settlement(
            settlement, lease=lease, detector=detector, identity=identity,
            owner_generation=runtime._speaker_verifier_activation_generation, turn_token=turn,
        )
        assert result is (mode == "accepted")
        record = next(r for r in records if r.get("operation") == "evidence_alias_consume")
        assert record["requested_lease_generation"] == lease.lease_generation
        assert record["before_physical_lease_present"] is True
        assert record["after_physical_lease_present"] is (mode != "accepted")
        assert record["outcome"] == ("returned" if mode == "accepted" else "rejected")
        assert record["session_epoch"] == identity.session_epoch
        if mode == "superseded":
            assert record["identity_matches"] is False
            assert record["before_session_epoch"] != record["session_epoch"]
    finally:
        await _close_stack(core)


async def test_unchanged_alias_rejections_are_bounded_and_keep_first_evidence(monkeypatch):
    core, runtime, detector, _, _, _, turn = await _active_real_stack()
    records = _capture(monkeypatch, runtime, detector)
    try:
        await _submit_pcm(runtime, turn, sequence=1)
        lease = runtime._asr_provider_speaker_evidence_lease
        identity = runtime._capture_runtime_identity(ingress_token=turn.ingress)
        await detector.abandon_provider_speaker_evidence_lease(lease)
        settlement = await detector.confirm_provider_speaker_evidence_retirement(lease)
        forged = replace(settlement)
        for _ in range(600):
            assert runtime._consume_provider_speaker_evidence_settlement(
                forged, lease=lease, detector=detector, identity=identity,
                owner_generation=runtime._speaker_verifier_activation_generation,
                turn_token=turn,
            ) is False
        runtime._pipeline_observer().flush()
        aliases = [r for r in records if r.get("operation") == "evidence_alias_consume"]
        assert len(aliases) == 2
        assert [r.get("coalesced_count") for r in aliases] == [1, 600]
        assert runtime._consume_provider_speaker_evidence_settlement(
            settlement, lease=lease, detector=detector, identity=identity,
            owner_generation=runtime._speaker_verifier_activation_generation,
            turn_token=turn,
        ) is True
        aliases = [r for r in records if r.get("operation") == "evidence_alias_consume"]
        assert len(aliases) == 3
        assert [r.get("coalesced_count") for r in aliases] == [1, 600, None]
        assert aliases[0]["requested_lease_generation"] == lease.lease_generation
        assert aliases[0]["before_physical_lease_present"] is True
        assert aliases[0]["after_physical_lease_present"] is True
        assert aliases[0]["outcome"] == "rejected"
        assert aliases[-1]["after_physical_lease_present"] is False
        assert aliases[-1]["outcome"] == "returned"
        assert len(records) < 10
    finally:
        await _close_stack(core)


async def test_stop_reset_clears_credentials_and_does_not_leak_origin(monkeypatch):
    core, runtime, detector, _, _, _, turn = await _active_real_stack()
    records = _capture(monkeypatch, runtime, detector)
    try:
        await _submit_pcm(runtime, turn, sequence=1)
        await runtime.abort("user_stop")
        request = next(r for r in records if r.get("operation") == "reset_request")
        assert request["initiator"] == "abort"
        assert request["reason"] == "user_stop"
        assert request["after_timeline_generation"] > request["before_timeline_generation"]
        assert any(r.get("operation") == "speaker_identity_clear" and r["reset_reason"] == "user_stop" for r in records)
        alias_clear = next(r for r in records if r.get("operation") == "turn_state_clear")
        assert alias_clear["before_physical_lease_present"] is True
        assert alias_clear["after_physical_lease_present"] is False
        assert alias_clear["initiator"] == "abort_transport"
        assert alias_clear["reason"] == "user_stop"
        records.clear()
        await detector.reset_provider_audio_timeline()
        assert all("reset_initiator" not in r for r in records)
    finally:
        await _close_stack(core)


@pytest.mark.parametrize("outcome", ["cancelled", "failed", "broken_sink"])
async def test_reset_observation_preserves_failure_and_cancellation(monkeypatch, outcome):
    core, runtime, detector, _, _, _, _ = await _active_real_stack()
    records = _capture(monkeypatch, runtime, detector)
    reset = detector.reset
    try:
        if outcome == "broken_sink":
            def broken(fields):
                raise OSError("PRIVATE_ERROR")
            detector.set_provider_state_diagnostic_callback(broken)
            old_timeline = detector._provider_audio_timeline_generation
            await runtime._reset_detector_with_diagnostics(detector, initiator="abort", reason="user_stop")
            assert detector._provider_audio_timeline_generation == old_timeline + 1
        else:
            started = asyncio.Event()
            async def fail():
                started.set()
                if outcome == "failed":
                    raise RuntimeError("PRIVATE_ERROR")
                await asyncio.Event().wait()
            monkeypatch.setattr(detector, "reset", fail)
            task = asyncio.create_task(runtime._reset_detector_with_diagnostics(
                detector, initiator="abort", reason="user_stop",
            ))
            await started.wait()
            if outcome == "cancelled":
                task.cancel()
            with pytest.raises(asyncio.CancelledError if outcome == "cancelled" else RuntimeError):
                await task
            record = next(r for r in records if r.get("operation") == "reset_request")
            assert record["outcome"] == outcome
            assert record["before_timeline_generation"] == record["after_timeline_generation"]
        assert "PRIVATE_ERROR" not in json.dumps(records)
    finally:
        monkeypatch.setattr(detector, "reset", reset)
        await _close_stack(core)


async def test_detached_detector_cleanup_keeps_original_session(monkeypatch):
    core, runtime, detector, _, _, _, turn = await _active_real_stack()
    records = _capture(monkeypatch, runtime, detector)
    epoch = runtime._asr_session_epoch
    ref = diagnostic_context(runtime, epoch)["diagnostic_session_ref"]
    try:
        await _submit_pcm(runtime, turn, sequence=1)
        runtime._asr_session_epoch += 1
        runtime._asr_detector = None
        await detector.close()
        cleanup = [r for r in records if r.get("component") == "detector"]
        assert cleanup
        assert all(r["diagnostic_session_ref"] == ref and r["session_epoch"] == epoch for r in cleanup)
        assert any(r.get("operation") == "speaker_identity_clear" and r["before_physical_lease_present"] and not r["after_physical_lease_present"] for r in cleanup)
    finally:
        await detector.close()
        await _close_stack(core)


async def test_vad_failure_records_reset_initiator(monkeypatch):
    core, runtime, detector, _, _, _, turn = await _active_real_stack()
    records = _capture(monkeypatch, runtime, detector)
    try:
        await _submit_pcm(runtime, turn, sequence=1)
        def broken(pcm):
            raise RuntimeError("PRIVATE_VAD_ERROR")
        monkeypatch.setattr(detector._gate, "feed", broken)
        await _submit_pcm(runtime, turn, sequence=2)
        record = next(r for r in records if r.get("reason") == "vad_feed_failed")
        assert record["initiator"] == "feed"
        assert record["after_timeline_generation"] == record["before_timeline_generation"] + 1
        assert "PRIVATE_VAD_ERROR" not in json.dumps(records)
    finally:
        await _close_stack(core)


def test_standalone_checker_keeps_strict_projection_in_sync():
    assert STATE_DIAGNOSTIC_FIELDS == CHECKER_FIELDS


async def test_finished_candidate_records_physical_clear_and_retained_proof(monkeypatch):
    core, runtime, detector, _, _, _, turn = await _active_real_stack()
    records = _capture(monkeypatch, runtime, detector)
    try:
        await _submit_pcm(runtime, turn, sequence=1)
        lease = runtime._asr_provider_speaker_evidence_lease
        assert await detector.finish_provider_speaker_evidence_lease(lease)
        record = next(r for r in records if r.get("operation") == "evidence_finish")
        assert record["before_physical_lease_present"] is True
        assert record["after_physical_lease_present"] is False
        assert record["after_retirement_count"] == record["before_retirement_count"] + 1
        assert record["before_evidence_lease_generation"] == lease.lease_generation
    finally:
        await _close_stack(core)
