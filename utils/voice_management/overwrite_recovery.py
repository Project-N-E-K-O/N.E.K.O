"""Explicit recovery of operations which never acquired submission permission."""

from __future__ import annotations

import asyncio
import hmac
import re

from utils.config_manager.imported_voices import VOICE_STORAGE_LOCK

from .types import VoiceManagementError


async def transition_with_context(adapter, cm, runtime, record, *, action):
    """Check context and transition under the same single-process storage lock."""
    from .service import context_token

    def commit():
        with VOICE_STORAGE_LOCK:
            current = adapter.resolve_runtime(cm, voice_data=record)
            if not hmac.compare_digest(context_token(current), context_token(runtime)):
                raise VoiceManagementError("CONTEXT_CHANGED", 409)
            return cm.transition_imported_voice_overwrite(
                record["local_ref"], runtime.scope_id, action=action,
                expected_operation_id=record["overwrite_operation_id"],
                expected_record_revision=record.get("_record_revision", 0),
            )

    return await asyncio.to_thread(commit)


async def recover_prepared_overwrite(
    adapter, cm, local_ref, *, token, operation_id, record_revision,
):
    """Retire only an explicitly identified, still-prepared operation."""
    from .service import _check_context, _runtime, overwrite_result_details, public_voice_data
    from .types import AttemptOutcome, StateSync

    state_sync = StateSync.UNCHANGED
    try:
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
        try:
            result = await transition_with_context(adapter, cm, runtime, record, action="recover")
        except (OSError, ValueError) as exc:
            context_changed = isinstance(exc, ValueError) and exc.args == ("VOICE_CONTEXT_CHANGED",)
            state_sync = StateSync.UNCHANGED if context_changed else StateSync.FAILED
            raise VoiceManagementError("CONTEXT_CHANGED" if context_changed else "STORAGE_ERROR",
                                       409 if context_changed else 500) from exc
        if not result.applied:
            raise VoiceManagementError("VOICE_STATE_CHANGED", 409)
        state_sync = StateSync.SAVED
        await _check_context(adapter, cm, runtime, voice_data=result.record)
        details = await overwrite_result_details(
            adapter, cm, local_ref, token=token,
            attempt_outcome=AttemptOutcome.NOT_SUBMITTED, state_sync=state_sync,
        )
        if details["voice_state"] is None:
            await _check_context(adapter, cm, runtime, voice_data=result.record)
        return {
            "success": True, "recovered": True, "voice_id": local_ref,
            "status": result.record["overwrite_status"],
            "voice_data": public_voice_data(result.record), "details": details,
        }
    except VoiceManagementError as exc:
        details = await overwrite_result_details(
            adapter, cm, local_ref, token=token,
            attempt_outcome=AttemptOutcome.NOT_SUBMITTED, state_sync=state_sync,
        )
        raise VoiceManagementError(exc.code, exc.status_code, details=details) from exc
    except (OSError, ValueError) as exc:
        details = await overwrite_result_details(
            adapter, cm, local_ref, token=token,
            attempt_outcome=AttemptOutcome.NOT_SUBMITTED, state_sync=state_sync,
        )
        context_changed = isinstance(exc, ValueError) and exc.args == ("VOICE_CONTEXT_CHANGED",)
        raise VoiceManagementError("CONTEXT_CHANGED" if context_changed else "STORAGE_ERROR",
                                   409 if context_changed else 500, details=details) from exc
