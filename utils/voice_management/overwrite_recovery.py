"""Explicit recovery of operations which never acquired submission permission."""

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
