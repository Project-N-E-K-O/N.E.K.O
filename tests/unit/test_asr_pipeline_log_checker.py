import json

from scripts.check_asr_pipeline_log import parse_record, summarize


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
