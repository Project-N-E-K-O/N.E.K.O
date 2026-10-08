"""Prepared recovery and explicit abandonment of unresolved remote updates."""

from __future__ import annotations

import asyncio
import re

from .types import VoiceManagementError
from .service import _overwrite_feedback


@_overwrite_feedback
async def recover_prepared_overwrite(
    adapter, cm, local_ref, *, token, operation_id, record_revision, evidence,
):
    """Retire only an explicitly identified, still-prepared operation."""
    from .service import _check_context, _runtime, public_voice_data, transition_with_context

    if re.fullmatch(r"voice_[0-9a-f]{32}", local_ref) is None:
        raise VoiceManagementError("VOICE_NOT_FOUND", 404)
    if (not isinstance(operation_id, str) or not operation_id or len(operation_id) > 128
            or type(record_revision) is not int or record_revision < 0):
        raise VoiceManagementError("INVALID_METADATA", 400)
    record = await asyncio.to_thread(cm.get_imported_voice, local_ref, include_inactive=True)
    if not record:
        raise VoiceManagementError("VOICE_NOT_FOUND", 404)
    runtime = await _runtime(adapter, cm, token, voice_data=record)
    if record.get("scope_id") != runtime.scope_id or record.get("provider") != runtime.provider:
        raise VoiceManagementError("CONTEXT_CHANGED", 409)
    if (record.get("overwrite_operation_id") != operation_id
            or record.get("_record_revision", 0) != record_revision):
        raise VoiceManagementError("VOICE_STATE_CHANGED", 409)
    result = await evidence.write(transition_with_context(adapter, cm, runtime, record, action="recover"))
    if not result.applied:
        raise VoiceManagementError("VOICE_STATE_CHANGED", 409)
    await _check_context(adapter, cm, runtime, voice_data=result.record)
    return {
        "success": True, "recovered": True, "voice_id": local_ref,
        "status": result.record["overwrite_status"],
        "voice_data": public_voice_data(result.record),
    }


@_overwrite_feedback
async def abandon_unknown_overwrite(
    adapter, cm, local_ref, *, token, operation_id, record_revision, evidence,
):
    """Explicitly accept duplicate-submission risk; never claim remote rejection."""
    from .service import (
        _check_context, _runtime, _OVERWRITE_LOCKS, _voice_metadata, public_voice_data, transition_with_context,
    )
    from .types import AttemptOutcome

    evidence.attempt_outcome = AttemptOutcome.UNKNOWN
    if re.fullmatch(r"voice_[0-9a-f]{32}", local_ref) is None:
        raise VoiceManagementError("VOICE_NOT_FOUND", 404)
    if (not isinstance(operation_id, str) or not operation_id or len(operation_id) > 128
            or type(record_revision) is not int or record_revision < 0):
        raise VoiceManagementError("INVALID_METADATA", 400)
    # Use the same per-voice lock as submission, including the remote recheck.
    lock = _OVERWRITE_LOCKS.setdefault(local_ref, asyncio.Lock())
    if lock.locked():
        raise VoiceManagementError("OPERATION_IN_PROGRESS", 409)
    async with lock:
        deadline = asyncio.timeout(30)
        try:
            async with deadline:
                record = await asyncio.to_thread(cm.get_imported_voice, local_ref, include_inactive=True)
                if not record:
                    raise VoiceManagementError("VOICE_NOT_FOUND", 404)
                runtime = await _runtime(adapter, cm, token, voice_data=record)
                if record.get("scope_id") != runtime.scope_id or record.get("provider") != runtime.provider:
                    raise VoiceManagementError("CONTEXT_CHANGED", 409)
                if (record.get("overwrite_operation_id") != operation_id
                        or record.get("_record_revision", 0) != record_revision
                        or record.get("overwrite_status") != "unknown"
                        or record.get("overwrite_abandon_revision") != record_revision):
                    raise VoiceManagementError("VOICE_STATE_CHANGED", 409)
                remote = await adapter.get_voice(runtime, record["remote_voice_id"])
                await _check_context(adapter, cm, runtime, voice_data=record)
                if (remote is None or remote.status not in {"ready", "completed", "OK"}
                        or adapter.compare_revisions(_voice_metadata(remote.metadata).get("remote_revision"),
                                                     record.get("overwrite_previous_revision")) != 0):
                    raise VoiceManagementError("VOICE_STATE_CHANGED", 409)
                receipt = await evidence.write(transition_with_context(adapter, cm, runtime, record, action="abandon"))
                if not receipt.applied:
                    raise VoiceManagementError("VOICE_STATE_CHANGED", 409)
                await _check_context(adapter, cm, runtime, voice_data=receipt.record)
                return {"success": True, "abandoned": True, "voice_id": local_ref,
                        "status": "failed", "voice_data": public_voice_data(receipt.record)}
        except TimeoutError:
            if not deadline.expired():
                raise
            raise VoiceManagementError("UPSTREAM_TIMEOUT", 504) from None
        finally:
            evidence.projection_deadline = deadline.when()
