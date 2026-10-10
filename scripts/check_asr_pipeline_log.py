"""Summarize ASR diagnostic evidence; missing observations are never success."""

from __future__ import annotations

import argparse
import ast
from collections import OrderedDict
import json
from pathlib import Path
import re

_REF = re.compile(r"[a-f0-9]{24}\Z")
_LABEL = re.compile(r"[a-zA-Z][a-zA-Z0-9_]{0,79}\Z")
_KEYS = frozenset({
    "stage", "phase", "reason", "reason_code", "failed_check", "outcome", "state",
    "disposition", "dispatcher_applied", "endpoint_authority", "speaker_enabled",
    "speaker_classification", "score_outcome", "terminal_reason", "candidate_role",
    "turn_id", "session_epoch", "provider_generation", "provider_buffer_epoch",
    "provider_utterance_id", "provider_start_sample_16k", "provider_end_sample_16k",
    "boundary_quality", "detector_epoch", "semantic_turn_id", "semantic_generation",
    "sequence_no", "speaker_sequence_no", "diagnostic_records_dropped", "has_text",
    "frame_count", "audio_samples", "sample_rate_hz", "pipeline_schema", "observed_at_ns",
    "source_session_epoch", "component", "cleanup_outcome", "residual_count",
    "audio_generation", "route_generation", "lease_generation", "residual_components",
    "coalesced_count",
    "worker_generation", "shadow_generation", "candidate_scope",
    "accepted_sample_count", "buffered_sample_count", "finish_sample_count",
    "minimum_sample_count", "score_attempt_count", "score_input_sample_count",
    "scored_sample_count", "last_checkpoint_ms", "evidence_sequence_no",
    "anchor_applied", "anchor_discard_prefix_sample_count", "scoring_deferred",
    "checkpoint_kind", "score_id", "score_checkpoint_kind", "score_checkpoint_ms",
    "score_window_start_sample", "score_window_end_sample", "score_duration_ms",
    "score_continuity", "known_missing_sample_count",
    "known_duplicate_sample_count", "trimmed_prefix_sample_count",
    "profile_generation_ref", "activation_generation_ref", "installation_ref",
    "model_version", "scoring_rule_version", "quality_summary_outcome",
    "quality_summary_version", "rms_milli", "peak_milli",
    "near_silence_ratio_milli", "clipping_ratio_milli",
    "near_silence_threshold_milli", "clipping_threshold_milli",
    "voice_activity_measurement", "voice_activity_ratio_milli",
    "evidence_path", "evidence_disposition",
})


_STATE_FIELDS = (
    "detector_epoch", "timeline_generation", "sample_cursor_16k", "sequence_no",
    "evidence_lease_generation", "evidence_timeline_generation", "retirement_count",
    "exact_receipt_count", "boundary_snapshot_count", "closed",
    "physical_lease_present", "logical_lease_present", "candidate_present",
    "speaker_ledger_count", "audio_generation", "transport_generation", "session_epoch",
)
STATE_DIAGNOSTIC_FIELDS = frozenset({
    "operation", "operation_id", "initiator", "reset_initiator", "reset_reason", "identity_matches",
    "component", "proof_present", "proof_timeline_generation", "proof_detector_epoch",
    "proof_operation_serial", "proof_lease_generation", "proof_owner_matches",
    "proof_epoch_matches", "proof_timeline_matches", "requested_lease_generation",
    "installation_trace_ref", "installation_initiator", "installation_reason",
    "decision", "spec_changed", "requested_enabled", "participating", "route_supported",
    "route_mode", "detector_present", "activation_revision", "cleanup_pending",
    *(f"{side}_{field}" for side in ("before", "after") for field in _STATE_FIELDS),
})


_KEYS = _KEYS | STATE_DIAGNOSTIC_FIELDS


def parse_record(line: str) -> dict | None:
    marker = next((m for m in ("ASR resolution ", "ASR incident ", "ASR cleanup ") if m + "{" in line), None)
    if len(line) > 65_536 or marker is None:
        return None
    try:
        record = ast.literal_eval(line.split(marker, 1)[1].strip())
        if type(record) is not dict or not _REF.fullmatch(str(record.get("diagnostic_session_ref", ""))):
            return None
        result = {k: v for k, v in record.items() if k in _KEYS and (
            v is None or type(v) in (int, bool) or type(v) is str and _LABEL.fullmatch(v)
        )}
        result["diagnostic_session_ref"] = record["diagnostic_session_ref"]
        if marker == "ASR incident ":
            result["stage"] = "incident"
            result["session_epoch"] = record.get("source_session_epoch", record.get("session_epoch"))
        return result
    except (ValueError, SyntaxError, TypeError, RecursionError):
        return None


def summarize(lines, *, max_sessions=16, max_records=512) -> dict:
    max_records = max(1, int(max_records))
    landmark_limit = max(1, min(64, max_records // 4 or 1))
    sessions = OrderedDict()
    truncated = False
    for line in lines:
        record = parse_record(line)
        if record is None:
            continue
        ref = record["diagnostic_session_ref"]
        if ref not in sessions:
            if len(sessions) >= max_sessions:
                sessions.popitem(last=False)
                truncated = True
            sessions[ref] = {
                "records": [], "landmarks": OrderedDict(), "record_count": 0,
                "latest_epoch": None, "truncated": False, "drops": False,
            }
        session = sessions[ref]
        sessions.move_to_end(ref)
        session["record_count"] += 1
        session["latest_epoch"] = record.get("session_epoch")
        session["drops"] |= bool(record.get("diagnostic_records_dropped"))
        landmark_key = _landmark_key(record)
        if landmark_key is not None and landmark_key not in session["landmarks"]:
            if len(session["landmarks"]) < landmark_limit:
                session["landmarks"][landmark_key] = record
        if len(session["records"]) >= max_records:
            session["records"].pop(0)
            session["truncated"] = True
        session["records"].append(record)
    reports = []
    for ref, session in sessions.items():
        recent = session["records"]
        preserved = [record for record in session["landmarks"].values() if record not in recent]
        recent_slots = max(0, max_records - len(preserved))
        records = preserved + (recent[-recent_slots:] if recent_slots else [])
        stages = {r.get("stage") for r in records}
        phases = {r.get("phase") for r in records if r.get("stage") == "endpoint_diagnostic"}
        policies = {r.get("endpoint_authority") for r in records if r.get("endpoint_authority")}
        provider_only = policies == {"provider"}
        coverage = {
            "audio_input": "observed" if "audio_received" in stages else "not_observed",
            "audio_write": "observed" if "provider_audio_written" in stages else "not_observed",
            "vad": "observed" if phases & {"vad_activity", "vad_load", "vad_feed"} or "vad_activity" in stages else "not_observed",
            "smart_turn": "observed" if "evaluation_result" in phases else "not_applicable" if provider_only else "not_observed",
            "asr_final": "observed" if stages & {"asr_final_received", "provider_final_received"} else "not_observed",
            "speaker": "observed" if stages & {"speaker_fact_observed", "speaker_capture_closed"} else "not_observed",
            "admission": "observed" if "admission_decision" in stages else "not_observed",
            "core_delivery": "observed" if "core_voice_delivery" in stages else "not_observed",
        }
        # Provider alias mapping is learned only from records explicitly carrying
        # both identities. Never assign unkeyed session events to the latest turn.
        identities = {}
        for r in records:
            if type(r.get("turn_id")) is int and all(type(r.get(k)) is int for k in ("audio_generation", "route_generation", "lease_generation")):
                identities.setdefault(r["turn_id"], set()).add(_turn_key(r))
        ambiguous = {turn for turn, keys in identities.items() if len(keys) > 1}
        alias = {}
        for r in records:
            if type(r.get("turn_id")) is int and r["turn_id"] not in ambiguous and type(r.get("provider_utterance_id")) is int:
                alias[_provider_key(r)] = r["turn_id"]
        turns = OrderedDict()
        for r in records:
            turn_id = r.get("turn_id")
            if turn_id is None:
                turn_id = alias.get(_provider_key(r))
            if turn_id is not None:
                known = identities.get(turn_id, set())
                if len(known) == 1:
                    key = next(iter(known))
                elif len(known) > 1:
                    key = _turn_key(r)
                    if key not in known:
                        continue
                else:
                    key = (turn_id, None, None, None)
                turns.setdefault(key, []).append(r)
        turn_reports = []
        for turn_key, events in turns.items():
            decisions = [r for r in events if r.get("stage") == "admission_decision"]
            core = [r for r in events if r.get("stage") == "core_voice_delivery" and r.get("outcome") not in {"started", "accepted"}]
            findings = []
            for r in events:
                if r.get("failed_check") or r.get("stage", "").endswith("ignored"):
                    findings.append({k: r[k] for k in ("stage", "failed_check", "reason") if k in r})
            turn_reports.append({
                "turn_id": turn_key[0], "audio_generation": turn_key[1],
                "route_generation": turn_key[2], "lease_generation": turn_key[3],
                "admission": decisions[-1].get("disposition") if decisions else "not_observed",
                "reason": decisions[-1].get("reason_code") if decisions else None,
                "core_outcome": core[-1].get("outcome") if core else "not_observed",
                "core_phase": core[-1].get("phase") if core else None,
                "findings": findings,
            })
        score_groups = OrderedDict()
        for record in records:
            score_id = record.get("score_id")
            if type(score_id) is str:
                score_groups.setdefault(score_id, []).append(record)
        score_reports = [
            _score_report(score_id, events, records, ambiguous)
            for score_id, events in score_groups.items()
        ]
        reports.append({
            "session_ref": ref, "session_epoch": session["latest_epoch"],
            "coverage": coverage, "log_gaps": session["drops"] or session["truncated"],
            "records_omitted": max(0, session["record_count"] - len(records)),
            "log_integrity": {
                "diagnostic_drop_observed": session["drops"],
                "records_truncated": session["truncated"],
                "score_correlation_conflicts": sum(
                    bool(report["correlation_conflicts"])
                    for report in score_reports
                ),
            },
            "ambiguous_partial_turn_ids": sorted(ambiguous),
            "scores": score_reports,
            "turns": turn_reports,
            "session_findings": [r for r in records if r.get("stage", "").endswith("ignored")
                                  or r.get("stage") in {"endpoint_diagnostic", "incident", "provider_state_change", "speaker_verifier_installation"}
                                  or r.get("stage", "").startswith("cleanup_")],
        })
    return {"schema": 1, "sessions_truncated": truncated, "sessions": reports,
            "interpretation": (
                "not_observed means insufficient evidence; a speaker fact observation does not prove "
                "coordinator acceptance; submitted means request submitted, not reply played"
            )}


def _provider_key(record):
    return tuple(record.get(k) for k in ("provider_generation", "provider_buffer_epoch", "provider_utterance_id"))


_SCORE_CONTEXT_FIELDS = (
    "detector_epoch", "shadow_generation", "candidate_scope", "sample_rate_hz",
    "score_checkpoint_kind", "score_checkpoint_ms", "score_window_start_sample",
    "score_window_end_sample", "score_duration_ms", "score_continuity",
    "known_missing_sample_count", "known_duplicate_sample_count",
    "trimmed_prefix_sample_count", "profile_generation_ref",
    "activation_generation_ref", "installation_ref", "model_version",
    "scoring_rule_version", "quality_summary_version",
    "near_silence_threshold_milli", "clipping_threshold_milli",
    "voice_activity_measurement",
)


def _score_report(score_id, events, records, ambiguous_turn_ids):
    starts = [r for r in events if r.get("stage") == "speaker_score_started"]
    finishes = [r for r in events if r.get("stage") == "speaker_score_finished"]
    dispositions = [
        r for r in events if r.get("stage") == "speaker_evidence_disposition"
    ]
    reference = starts[0] if starts else events[0]
    conflicts = [
        field for field in _SCORE_CONTEXT_FIELDS
        if len({r.get(field) for r in events if r.get(field) is not None}) > 1
    ]
    start_sample = reference.get("score_window_start_sample")
    end_sample = reference.get("score_window_end_sample")
    interval_known = (
        type(start_sample) is int and type(end_sample) is int
        and 0 <= start_sample <= end_sample
    )
    provider_start = reference.get("provider_start_sample_16k")
    timeline_known = (
        interval_known and type(provider_start) is int
        and reference.get("sample_rate_hz") == 16_000
    )
    turn_id = reference.get("turn_id")
    provider_key = _provider_key(reference)

    def same_owner(record):
        if type(turn_id) is int and turn_id not in ambiguous_turn_ids:
            return record.get("turn_id") == turn_id
        if any(value is not None for value in provider_key):
            return _provider_key(record) == provider_key
        return False

    last_finish = finishes[-1] if finishes else None
    evidence_sequence = (
        last_finish.get("evidence_sequence_no") if last_finish is not None else None
    )
    facts = [
        record for record in records
        if record.get("stage") == "speaker_fact_observed"
        and same_owner(record)
        and (
            evidence_sequence is None
            or record.get("speaker_sequence_no", record.get("sequence_no"))
            == evidence_sequence
        )
    ]
    decisions = [
        record for record in records
        if record.get("stage") == "admission_decision" and same_owner(record)
    ]
    finals = [
        record for record in records
        if record.get("stage") in {"provider_final_received", "asr_final_received"}
        and same_owner(record)
    ]
    quality_outcome = reference.get("quality_summary_outcome")
    return {
        "score_id": score_id,
        "start_observation": "observed" if starts else "not_observed",
        "end_observation": "observed" if finishes else "not_observed",
        "score_outcome": (
            last_finish.get("score_outcome") if last_finish is not None
            else "not_observed"
        ),
        "duplicate_start_count": max(0, len(starts) - 1),
        "duplicate_end_count": max(0, len(finishes) - 1),
        "correlation_conflicts": conflicts,
        "checkpoint": {
            "kind": reference.get("score_checkpoint_kind"),
            "milliseconds": reference.get("score_checkpoint_ms"),
        },
        "scored_interval": {
            "relative_status": "known" if interval_known else "unknown",
            "relative_start_sample": start_sample if interval_known else None,
            "relative_end_sample": end_sample if interval_known else None,
            "timeline_status": "known" if timeline_known else "unknown",
            "timeline_start_sample_16k": (
                provider_start + start_sample if timeline_known else None
            ),
            "timeline_end_sample_16k": (
                provider_start + end_sample if timeline_known else None
            ),
        },
        "continuity": {
            "status": reference.get("score_continuity", "not_observed"),
            "known_missing_sample_count": reference.get("known_missing_sample_count"),
            "known_duplicate_sample_count": reference.get("known_duplicate_sample_count"),
            "trimmed_prefix_sample_count": reference.get("trimmed_prefix_sample_count"),
        },
        "configuration": {
            field: reference.get(field) for field in (
                "profile_generation_ref", "activation_generation_ref",
                "installation_ref", "model_version", "scoring_rule_version",
            )
        },
        "quality": {
            "status": (
                "measured" if quality_outcome == "measured"
                else "not_measured" if quality_outcome == "unavailable"
                else "not_observed"
            ),
            "algorithm_version": reference.get("quality_summary_version"),
            "duration_ms": reference.get("score_duration_ms"),
            "sample_count": reference.get("score_input_sample_count"),
            "rms_milli": reference.get("rms_milli"),
            "peak_milli": reference.get("peak_milli"),
            "near_silence_ratio_milli": reference.get("near_silence_ratio_milli"),
            "clipping_ratio_milli": reference.get("clipping_ratio_milli"),
            "voice_activity": {
                "status": reference.get(
                    "voice_activity_measurement", "not_observed"
                ),
                "ratio_milli": reference.get("voice_activity_ratio_milli"),
            },
        },
        "evidence_observation": "observed" if facts else "not_observed",
        "evidence_disposition_observation": (
            "observed" if dispositions else "not_observed"
        ),
        "evidence_disposition": (
            dispositions[-1].get("evidence_disposition")
            if dispositions else "not_observed"
        ),
        "evidence_path": (
            dispositions[-1].get("evidence_path") if dispositions else None
        ),
        "evidence_disposition_reason": (
            dispositions[-1].get("reason") if dispositions else None
        ),
        "evidence_disposition_history": [
            {
                "path": item.get("evidence_path"),
                "disposition": item.get("evidence_disposition"),
                "reason": item.get("reason"),
            }
            for item in dispositions
        ],
        "speaker_classification": (
            facts[-1].get("speaker_classification", facts[-1].get("outcome"))
            if facts else None
        ),
        "final_text_observation": "observed" if finals else "not_observed",
        "final_text_decision": (
            decisions[-1].get("disposition") if decisions else "not_observed"
        ),
        "final_text_reason": decisions[-1].get("reason_code") if decisions else None,
    }


def _landmark_key(record):
    """Bounded first evidence for each pipeline stage/decision survives tail truncation."""
    stage = record.get("stage")
    if not stage:
        return None
    return (
        stage,
        record.get("phase"),
        record.get("operation"),
        record.get("decision"),
        record.get("reason_code"),
        record.get("installation_trace_ref"),
        record.get("score_id"),
    )


def _turn_key(record):
    return tuple(record.get(k) for k in ("turn_id", "audio_generation", "route_generation", "lease_generation"))


def main() -> None:
    parser = argparse.ArgumentParser(description="检查 ASR/VAD/SmartTurn/声纹日志证据，缺失记录不视为通过")
    parser.add_argument("log", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    with args.log.open(encoding="utf-8", errors="replace") as stream:
        report = summarize(stream)
    output = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    else:
        print(output)


if __name__ == "__main__":
    main()
