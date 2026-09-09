"""Bounded, content-free observations of Provider resets and proof retirement."""

import asyncio
from collections import OrderedDict
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
import time
from uuid import uuid4

from .pipeline_diagnostics import safe_fields
from .speaker_diagnostics import diagnostic_context


_ORIGIN = ContextVar("provider_reset_diagnostic_origin", default=None)
_INSTALLATION_TRACE = ContextVar("speaker_installation_diagnostic_trace", default=None)
_ALIAS_AGGREGATE_INTERVAL_SECONDS = 5.0
_MAX_DIAGNOSTIC_EMITTERS = 8
_MAX_ALIAS_AGGREGATES = 32


@contextmanager
def speaker_installation_trace(initiator: str, reason: str):
    """Carry one content-free trace identity across Registry/Core/Runtime awaits."""
    existing = _INSTALLATION_TRACE.get()
    if existing is not None:
        yield existing
        return
    trace = (f"t{uuid4().hex}", initiator, reason)
    token = _INSTALLATION_TRACE.set(trace)
    try:
        yield trace
    finally:
        _INSTALLATION_TRACE.reset(token)


def _installation_trace_fields() -> dict:
    trace = _INSTALLATION_TRACE.get()
    if trace is None:
        return {}
    return {
        "installation_trace_ref": trace[0],
        "installation_initiator": trace[1],
        "installation_reason": trace[2],
    }


def installation_revision_label(value: str | None) -> str:
    """Normalize an internal revision into the diagnostics label contract."""
    if value is None:
        return "disabled"
    normalized = value.lower().replace("-", "_")
    if len(normalized) <= 61 and all(character.isascii() and (character.isalnum() or character == "_") for character in normalized):
        return f"r_{normalized}"
    return "unavailable"


def emit_speaker_installation_event(runtime, *, phase: str, **fields) -> None:
    """Emit one best-effort control-plane event without affecting installation."""
    try:
        trace = _installation_trace_fields()
        runtime._schedule_pipeline_session_event(
            "speaker_verifier_installation",
            runtime._asr_session_epoch,
            phase=phase,
            **trace,
            **fields,
        )
    except Exception:
        pass


def trace_speaker_installation_call(default_initiator: str, default_reason: str):
    """Give an async control-plane entry one trace unless its caller supplied one."""
    def decorate(function):
        @wraps(function)
        async def observed(subject, *args, **kwargs):
            initiator = kwargs.get("diagnostic_initiator", default_initiator)
            reason = kwargs.get("diagnostic_reason", default_reason)
            with speaker_installation_trace(initiator, reason):
                return await function(subject, *args, **kwargs)
        return observed
    return decorate


def _snapshot(subject, component):
    if component == "runtime":
        lease = subject._asr_provider_speaker_evidence_lease
        return {
            "session_epoch": subject._asr_session_epoch,
            "evidence_lease_generation": getattr(lease, "lease_generation", None),
            "physical_lease_present": lease is not None,
            "logical_lease_present": subject._asr_current_speaker_lease is not None,
            "candidate_present": subject._asr_current_speaker_candidate is not None,
            "speaker_ledger_count": len(subject._asr_provider_speaker_ledgers),
            "audio_generation": subject._asr_audio_generation,
            "transport_generation": (subject._asr_lifecycle.snapshot.transport_generation
                                     if subject._asr_lifecycle is not None else None),
        }
    state = subject._provider_speaker_evidence_state
    return {
        "detector_epoch": subject._detector_epoch,
        "timeline_generation": subject._provider_audio_timeline_generation,
        "sample_cursor_16k": subject._provider_audio_sample_cursor_16k,
        "sequence_no": subject._provider_segment_last_sequence_no,
        "evidence_lease_generation": state.lease.lease_generation if state else None,
        "evidence_timeline_generation": state.timeline_generation if state else None,
        "physical_lease_present": state is not None,
        "retirement_count": len(subject._provider_speaker_evidence_settlements),
        "exact_receipt_count": len(subject._provider_exact_interval_records),
        "boundary_snapshot_count": len(subject._provider_boundary_snapshots),
        "closed": subject._closed,
    }


def runtime_state_emitter(runtime, epoch=None):
    """Capture the session before mutation/await, including detached cleanup."""
    context = diagnostic_context(runtime, runtime._asr_session_epoch if epoch is None else epoch)

    try:
        cache = getattr(runtime, "_provider_state_diagnostic_emitters", None)
        if cache is None:
            cache = OrderedDict()
            runtime._provider_state_diagnostic_emitters = cache
        cache_key = (context["diagnostic_session_ref"], context["session_epoch"])
        cached = cache.get(cache_key)
        if cached is not None:
            cache.move_to_end(cache_key)
            return cached
    except Exception:
        cache = None
        cache_key = None

    aggregates = OrderedDict()

    def publish(fields):
        runtime._schedule_pipeline_metadata({
            **context, **safe_fields(fields), "stage": "provider_state_change",
            "pipeline_schema": 1, "observed_at_ns": time.time_ns(),
        }, capacity=32)

    def publish_aggregate(bucket):
        if bucket["count"] <= bucket["published"]:
            return
        record = dict(bucket["record"])
        record["coalesced_count"] = bucket["count"]
        publish(record)
        bucket["published"] = bucket["count"]
        bucket["published_at"] = time.monotonic()

    def flush_aggregates():
        for bucket in aggregates.values():
            publish_aggregate(bucket)

    def unchanged_alias_rejection(fields):
        if fields.get("operation") != "evidence_alias_consume" or fields.get("outcome") != "rejected":
            return False
        return all(
            fields.get(f"before_{name}") == fields.get(f"after_{name}")
            for name in (
                "session_epoch", "evidence_lease_generation", "physical_lease_present",
                "logical_lease_present", "candidate_present", "speaker_ledger_count",
                "audio_generation", "transport_generation",
            )
        )

    def emit(fields):
        try:
            fields = {**_installation_trace_fields(), **fields}
            if unchanged_alias_rejection(fields):
                key = tuple(
                    (name, fields.get(name))
                    for name in (
                        "installation_trace_ref", "requested_lease_generation",
                        "identity_matches", "before_session_epoch",
                        "before_evidence_lease_generation", "before_physical_lease_present",
                        "before_logical_lease_present", "before_candidate_present",
                    )
                )
                now = time.monotonic()
                bucket = aggregates.get(key)
                if bucket is None:
                    if len(aggregates) >= _MAX_ALIAS_AGGREGATES:
                        _, evicted = aggregates.popitem(last=False)
                        publish_aggregate(evicted)
                    bucket = {
                        "record": dict(fields), "count": 1, "published": 0,
                        "published_at": now,
                    }
                    aggregates[key] = bucket
                    publish_aggregate(bucket)
                else:
                    bucket["count"] += 1
                    bucket["record"] = dict(fields)
                    aggregates.move_to_end(key)
                    if now - bucket["published_at"] >= _ALIAS_AGGREGATE_INTERVAL_SECONDS:
                        publish_aggregate(bucket)
                return
            flush_aggregates()
            publish(fields)
        except Exception:
            pass

    if cache is not None and cache_key is not None:
        try:
            if len(cache) >= _MAX_DIAGNOSTIC_EMITTERS:
                _, evicted = cache.popitem(last=False)
                flush = getattr(evicted, "flush", None)
                if callable(flush):
                    flush()
            emit.flush = flush_aggregates
            cache[cache_key] = emit
        except Exception:
            pass
    return emit


@contextmanager
def reset_origin(initiator, reason):
    token = _ORIGIN.set((initiator, reason))
    try:
        yield
    finally:
        _ORIGIN.reset(token)


@contextmanager
def state_change(subject, *, operation, initiator, reason, component="detector", source_epoch=None, **fields):
    """One before/after record per operation; never retain audio or exceptions.

    The callback and scalar pre-state are captured synchronously. Diagnostic
    failures cannot suppress application exceptions or change reset decisions.
    """
    emit = None
    record = {}
    try:
        emit = (runtime_state_emitter(subject, source_epoch) if component == "runtime"
                else getattr(subject, "_provider_state_diagnostic_callback", None))
        if callable(emit):
            serial = getattr(subject, "_provider_state_diagnostic_serial", 0) + 1
            subject._provider_state_diagnostic_serial = serial
            origin = _ORIGIN.get()
            record = dict(operation=operation, operation_id=serial, component=component,
                          initiator=initiator, reason=reason, **fields)
            record.update(_installation_trace_fields())
            if origin is not None:
                record.update(reset_initiator=origin[0], reset_reason=origin[1])
            record.update((f"before_{k}", v) for k, v in _snapshot(subject, component).items())
    except Exception:
        emit = None
    outcome = "returned"
    result = {}
    try:
        yield result
    except asyncio.CancelledError:
        outcome = "cancelled"
        raise
    except BaseException:
        outcome = "failed"
        raise
    finally:
        if callable(emit):
            try:
                record.update((f"after_{k}", v) for k, v in _snapshot(subject, component).items())
                record.update(outcome=result.get("outcome", outcome))
                emit(record)
            except Exception:
                pass


def trace_state_change(operation, *, component="detector"):
    """Observe synchronous identity fences without moving their boundaries."""
    def decorate(function):
        @wraps(function)
        def observed(subject, *args, **kwargs):
            fields = {}
            source_epoch = kwargs.get("source_epoch")
            try:
                if operation == "evidence_alias_consume":
                    lease = kwargs.get("lease")
                    identity = kwargs.get("identity")
                    source_epoch = identity.session_epoch
                    fields.update(requested_lease_generation=lease.lease_generation,
                                  identity_matches=subject._runtime_identity_matches(identity))
                if operation == "evidence_retirement":
                    state = args[0]
                    fields.update(proof_lease_generation=state.lease.lease_generation,
                                  proof_timeline_generation=state.timeline_generation,
                                  proof_detector_epoch=state.lease.detector_epoch)
            except Exception:
                pass
            with state_change(
                subject, operation=operation, component=component,
                initiator=kwargs.get("initiator", function.__name__.lstrip("_")),
                reason=kwargs.get("reason", operation),
                source_epoch=source_epoch, **fields,
            ) as diagnostic:
                result = function(subject, *args, **kwargs)
                if operation == "evidence_alias_consume" and result is False:
                    diagnostic["outcome"] = "rejected"
                return result
        return observed
    return decorate


def observe_retirement_failure(detector, lease, issued):
    """Record why the retained proof cannot authorize accounting on this timeline."""
    try:
        proof = issued[1] if issued else None
        with state_change(
            detector, operation="retirement_validation", initiator="account_provider_audio",
            reason="accounting_retirement_unproven",
            requested_lease_generation=lease.lease_generation,
            proof_present=proof is not None,
            proof_timeline_generation=getattr(proof, "timeline_generation", None),
            proof_detector_epoch=getattr(proof, "detector_epoch", None),
            proof_operation_serial=getattr(proof, "operation_serial", None),
            proof_lease_generation=proof.lease.lease_generation if proof else None,
            proof_owner_matches=lease._owner is detector._provider_speaker_evidence_owner,
            proof_epoch_matches=proof is not None and proof.detector_epoch == detector._detector_epoch,
            proof_timeline_matches=proof is not None and proof.timeline_generation == detector._provider_audio_timeline_generation,
        ) as diagnostic:
            diagnostic["outcome"] = "rejected"
    except Exception:
        pass
