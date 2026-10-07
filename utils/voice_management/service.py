"""Existing-voice operations, with ownership checked across network awaits."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import asdict
from datetime import datetime, timezone
from functools import wraps
from weakref import WeakValueDictionary

from utils.config_manager.imported_voices import VOICE_STORAGE_LOCK

from .types import AttemptOutcome, RemoteVoice, StateSync, VoiceManagementAdapter, VoiceManagementError, VoiceRuntime

_CONTEXT_SECRET = secrets.token_bytes(32)
_OVERWRITE_LOCKS: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()
_IMPORT_FIELDS = frozenset({"clone_model", "doubao_resource_id", "ref_language"})
_VOICE_METADATA_FIELDS = _IMPORT_FIELDS | frozenset({
    "minimax_base_url", "elevenlabs_base_url", "dashscope_base_url",
    "doubao_base_url", "glm_base_url", "glm_voice_name", "raw_voice_id",
    "remote_revision",
})
_PUBLIC_METADATA_FIELDS = _VOICE_METADATA_FIELDS - frozenset({
    "minimax_base_url", "elevenlabs_base_url", "dashscope_base_url",
    "doubao_base_url", "glm_base_url",
})


def context_token(runtime: VoiceRuntime) -> str:
    payload = json.dumps(asdict(runtime), sort_keys=True, separators=(",", ":"))
    return hmac.new(_CONTEXT_SECRET, payload.encode(), hashlib.sha256).hexdigest()


def public_voice_data(data: dict) -> dict:
    """Whitelist local fields rather than projecting an upstream response."""
    fields = _PUBLIC_METADATA_FIELDS | frozenset({
        "local_ref", "voice_id", "remote_voice_id", "source", "provider", "origin",
        "prefix", "display_name", "created_at", "remote_created_at", "imported_at",
        "updated_at", "remote_status", "can_overwrite", "verification", "availability",
        "overwrite_status", "overwrite_operation_id",
    })
    return {key: value for key, value in data.items() if key in fields}


def _voice_metadata(data: dict) -> dict:
    return {
        key: value for key, value in data.items()
        if key in _VOICE_METADATA_FIELDS and isinstance(value, str) and len(value) <= 2048
    }


def _overwrite_allowed(adapter: VoiceManagementAdapter, runtime: VoiceRuntime, voice: RemoteVoice | None) -> bool:
    # A ready voice without a revision cannot provide proof that this update completed.
    return bool(adapter.capabilities_for(runtime).overwrite and voice and voice.can_overwrite
                and adapter.compare_revisions(
                    _voice_metadata(voice.metadata).get("remote_revision"),
                    _voice_metadata(voice.metadata).get("remote_revision"),
                ) == 0)


def _revision_floor(adapter: VoiceManagementAdapter, floor: str | None, *revisions: str | None) -> str | None:
    """Keep the newest comparable evidence for this operation across queries."""
    for revision in revisions:
        if revision is None:
            continue
        if adapter.compare_revisions(revision, revision) != 0:
            raise VoiceManagementError("UPDATE_OUTCOME_UNKNOWN", 409)
        if floor is None or adapter.compare_revisions(revision, floor) == 1:
            floor = revision
        elif adapter.compare_revisions(revision, floor) is None:
            raise VoiceManagementError("UPDATE_OUTCOME_UNKNOWN", 409)
    return floor


async def overwrite_result_details(
    adapter, cm, local_ref: str, *, token: str,
    attempt_outcome: AttemptOutcome = AttemptOutcome.NOT_SUBMITTED,
    state_sync: StateSync = StateSync.UNCHANGED,
    strict_context: bool = False,
) -> dict:
    """Project a current persisted snapshot, independently of this request's result.

    Actions are advice, never mutation permission. A missing/invalid context or
    unreadable record deliberately produces no snapshot instead of a ready voice.
    """
    context_changed = False

    def read():
        nonlocal context_changed
        record = cm.get_imported_voice(local_ref, include_inactive=True)
        if not record:
            return None
        runtime = adapter.resolve_runtime(cm, voice_data=record)
        if (not isinstance(token, str) or not hmac.compare_digest(context_token(runtime), token)
                or record.get("scope_id") != runtime.scope_id
                or record.get("provider") != runtime.provider):
            context_changed = True
            return None
        capabilities = adapter.capabilities_for(runtime)
        status = record.get("overwrite_status") or "completed"
        actions = ["refresh"] if runtime.api_key and capabilities.details else []
        lock = _OVERWRITE_LOCKS.get(local_ref)
        if (runtime.api_key and capabilities.overwrite and record.get("can_overwrite")
                and status in {"completed", "failed"} and not (lock and lock.locked())):
            actions.append("overwrite")
        if (runtime.api_key and status in {"processing", "unknown"}
                and record.get("overwrite_submission_phase") == "prepared"):
            actions.append("recover")
        return {"local_ref": local_ref, "operation_id": record.get("overwrite_operation_id"),
                "record_revision": record.get("_record_revision", 0),
                "submission_phase": record.get("overwrite_submission_phase"),
                "overwrite_status": status, "actions": actions}

    try:
        snapshot = await asyncio.to_thread(read)
    except Exception:
        snapshot = None
    if strict_context and context_changed:
        raise VoiceManagementError("CONTEXT_CHANGED", 409, {
            "attempt_outcome": AttemptOutcome(attempt_outcome).value,
            "state_sync": StateSync(state_sync).value, "voice_state": None,
        })
    return {"attempt_outcome": AttemptOutcome(attempt_outcome).value, "voice_state": snapshot,
            "state_sync": StateSync(state_sync).value}


def _overwrite_feedback(*, mutation: bool):
    """Add the same persisted-state contract to update and query responses."""
    def decorate(function):
        @wraps(function)
        async def feedback(adapter, cm, local_ref, *, token, **kwargs):
            try:
                result = await function(adapter, cm, local_ref, token=token, **kwargs)
            except VoiceManagementError as exc:
                exc.details.update(await overwrite_result_details(
                    adapter, cm, local_ref, token=token,
                    attempt_outcome=exc.details.get("attempt_outcome", AttemptOutcome.NOT_SUBMITTED),
                    state_sync=exc.details.get("state_sync", StateSync.UNCHANGED),
                ))
                raise
            except (OSError, ValueError) as exc:
                context_changed = isinstance(exc, ValueError) and exc.args == ("VOICE_CONTEXT_CHANGED",)
                error = VoiceManagementError("CONTEXT_CHANGED" if context_changed else "STORAGE_ERROR",
                                             409 if context_changed else 500)
                error.details.update(await overwrite_result_details(
                    adapter, cm, local_ref, token=token,
                    state_sync=StateSync.UNCHANGED if context_changed else StateSync.FAILED,
                ))
                raise error from exc
            details = result.setdefault("details", {})
            details.update(await overwrite_result_details(
                adapter, cm, local_ref, token=token,
                attempt_outcome=AttemptOutcome.ACCEPTED if mutation else AttemptOutcome.NOT_SUBMITTED,
                state_sync=details.get("state_sync", StateSync.SAVED),
                strict_context=True,
            ))
            if details["voice_state"] is not None:
                result["status"] = details["voice_state"]["overwrite_status"]
            return result
        return feedback
    return decorate


async def management_context(adapter: VoiceManagementAdapter, cm, *, local_ref: str | None = None) -> dict:
    record = None
    if local_ref is not None:
        record = await asyncio.to_thread(cm.get_imported_voice, local_ref, include_inactive=True)
        if not record:
            raise VoiceManagementError("VOICE_NOT_FOUND", 404)
    runtime = await asyncio.to_thread(adapter.resolve_runtime, cm, voice_data=record)
    if record and (record.get("provider") != runtime.provider or record.get("scope_id") != runtime.scope_id):
        raise VoiceManagementError("CONTEXT_CHANGED", 409)
    capabilities = adapter.capabilities_for(runtime)
    return {
        "success": True, "provider": runtime.provider,
        "context_token": context_token(runtime), "configured": bool(runtime.api_key),
        "capabilities": capabilities.to_dict(), "required_fields": adapter.manual_fields(runtime),
    }


async def _runtime(adapter: VoiceManagementAdapter, cm, token: str, *, voice_data: dict | None = None) -> VoiceRuntime:
    runtime = await asyncio.to_thread(adapter.resolve_runtime, cm, voice_data=voice_data)
    if not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{64}", token) is None or not hmac.compare_digest(context_token(runtime), token):
        raise VoiceManagementError("CONTEXT_CHANGED", 409)
    if not runtime.api_key:
        raise VoiceManagementError("CONFIG_MISSING", 400)
    return runtime


async def _check_context(adapter: VoiceManagementAdapter, cm, runtime: VoiceRuntime, *, voice_data: dict | None = None) -> None:
    current = await asyncio.to_thread(adapter.resolve_runtime, cm, voice_data=voice_data)
    if not hmac.compare_digest(context_token(current), context_token(runtime)):
        raise VoiceManagementError("CONTEXT_CHANGED", 409)


async def transition_with_context(adapter, cm, runtime, record, *, action):
    """Check context and transition under the same single-process storage lock."""
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


def _remote_id(value: object) -> str:
    if not isinstance(value, str):
        raise VoiceManagementError("INVALID_VOICE_ID", 400)
    if any(ord(char) < 32 for char in value):
        raise VoiceManagementError("INVALID_VOICE_ID", 400)
    value = value.strip()
    if not value or len(value) > 512:
        raise VoiceManagementError("INVALID_VOICE_ID", 400)
    return value


async def list_remote_voices(
    adapter: VoiceManagementAdapter, cm, *, token: str, cursor: str | None = None, query: str = ""
) -> dict:
    runtime = await _runtime(adapter, cm, token)
    if not adapter.capabilities_for(runtime).list_voices:
        raise VoiceManagementError("LIST_UNSUPPORTED", 400)
    if len(query) > 200 or (cursor is not None and len(cursor) > 2048):
        raise VoiceManagementError("INVALID_CURSOR", 400)
    page = await adapter.list_voices(runtime, cursor=cursor, query=query)
    await _check_context(adapter, cm, runtime)
    local = await asyncio.to_thread(cm.get_voices_for_current_api, for_listing=True)
    registered = {
        data.get("remote_voice_id"): ref for ref, data in local.items()
        if isinstance(data, dict) and data.get("origin") == "import" and data.get("scope_id") == runtime.scope_id
    }
    voices = [{
        "voice_id": voice.voice_id, "name": voice.name or voice.voice_id,
        "created_at": voice.created_at, "status": voice.status,
        "metadata": {key: value for key, value in _voice_metadata(voice.metadata).items()
                     if key in _PUBLIC_METADATA_FIELDS},
        "can_overwrite": _overwrite_allowed(adapter, runtime, voice),
        "imported": voice.voice_id in registered, "local_ref": registered.get(voice.voice_id),
    } for voice in page.voices]
    await _check_context(adapter, cm, runtime)
    return {
        "success": True, "provider": runtime.provider, "context_token": token,
        "voices": voices, "next_cursor": page.next_cursor,
    }


async def import_remote_voice(adapter: VoiceManagementAdapter, cm, payload: dict) -> dict:
    runtime = await _runtime(adapter, cm, payload.get("context_token"))
    capabilities = adapter.capabilities_for(runtime)
    if not capabilities.manual_import:
        raise VoiceManagementError("IMPORT_UNSUPPORTED", 400)
    remote_id = adapter.validate_voice_id(_remote_id(payload.get("remote_voice_id")))
    display_name = payload.get("display_name", "")
    if not isinstance(display_name, str) or len(display_name) > 200:
        raise VoiceManagementError("INVALID_DISPLAY_NAME", 400)
    supplied = payload.get("metadata", {})
    if not isinstance(supplied, dict) or set(supplied) - _IMPORT_FIELDS:
        raise VoiceManagementError("INVALID_METADATA", 400)
    if any(not isinstance(value, str) or len(value) > 200 for value in supplied.values()):
        raise VoiceManagementError("INVALID_METADATA", 400)
    # Resource identity is taken from settings, never supplied by a stale client.
    if supplied.get("doubao_resource_id", runtime.resource_id) != runtime.resource_id:
        raise VoiceManagementError("CONTEXT_CHANGED", 409)
    remote: RemoteVoice | None = None
    if capabilities.details:
        try:
            remote = await adapter.get_voice(runtime, remote_id)
        except VoiceManagementError as exc:
            if exc.code not in {
                "DETAILS_UNSUPPORTED", "PERMISSION_DENIED", "AUTH_FAILED",
                "MANAGEMENT_CONFIG_MISSING", "LIST_UNSUPPORTED",
            }:
                raise
    await _check_context(adapter, cm, runtime)
    if remote and remote.status in {"failed", "processing", "unavailable"}:
        raise VoiceManagementError("VOICE_NOT_READY", 409)
    metadata = _voice_metadata(adapter.import_metadata(runtime))
    metadata.update(_voice_metadata(supplied))
    if remote:
        metadata.update(_voice_metadata(remote.metadata))
        remote_id = remote.voice_id
    now = datetime.now(timezone.utc).isoformat()
    name = display_name.strip() or (remote.name if remote else "") or remote_id
    metadata.update({
        "source": "clone", "provider": runtime.provider, "origin": "import",
        "remote_voice_id": remote_id, "scope_id": runtime.scope_id,
        "display_name": name, "prefix": name, "imported_at": now, "created_at": now,
        "remote_created_at": remote.created_at if remote else None,
        "remote_status": remote.status if remote else "unknown",
        "verification": "verified" if remote else "unverified",
        "can_overwrite": _overwrite_allowed(adapter, runtime, remote),
    })

    def commit():
        current = adapter.resolve_runtime(cm)
        if not hmac.compare_digest(context_token(current), context_token(runtime)):
            raise VoiceManagementError("CONTEXT_CHANGED", 409)
        return cm.import_remote_voice(runtime.scope_id, runtime.provider, remote_id, metadata)

    ref, saved, created = await asyncio.to_thread(commit)
    return {
        "success": True, "voice_id": ref, "voice_data": public_voice_data(saved),
        "created": created, "verification": saved.get("verification", "unverified"),
    }


@_overwrite_feedback(mutation=True)
async def overwrite_remote_voice(
    adapter: VoiceManagementAdapter, cm, local_ref: str, *, token: str, audio: bytes, filename: str
) -> dict:
    if re.fullmatch(r"voice_[0-9a-f]{32}", local_ref) is None:
        raise VoiceManagementError("VOICE_NOT_FOUND", 404)
    record = await asyncio.to_thread(cm.get_imported_voice, local_ref, include_inactive=True)
    runtime = await _runtime(adapter, cm, token, voice_data=record)
    if not record or record.get("scope_id") != runtime.scope_id:
        raise VoiceManagementError("CONTEXT_CHANGED", 409)
    if not adapter.capabilities_for(runtime).overwrite or not record.get("can_overwrite"):
        raise VoiceManagementError("OVERWRITE_UNSUPPORTED", 400)
    lock = _OVERWRITE_LOCKS.setdefault(local_ref, asyncio.Lock())
    if lock.locked():
        raise VoiceManagementError("OPERATION_IN_PROGRESS", 409)
    async with lock:
        record = await asyncio.to_thread(cm.get_imported_voice, local_ref, include_inactive=True)
        if not record or record.get("scope_id") != runtime.scope_id:
            raise VoiceManagementError("CONTEXT_CHANGED", 409)
        if not record.get("can_overwrite"):
            raise VoiceManagementError("OVERWRITE_UNSUPPORTED", 400)
        # An unknown upstream outcome requires a status refresh, not resubmission.
        if record.get("overwrite_status") in {"processing", "unknown"}:
            raise VoiceManagementError("UPDATE_OUTCOME_UNKNOWN", 409)
        remote_id = record["remote_voice_id"]
        operation_id = secrets.token_hex(16)
        mutation_started = False
        claim_owned = False
        claim_revision = None
        previous_revision = None

        async def before_mutation(current: RemoteVoice):
            nonlocal mutation_started, claim_owned, previous_revision, claim_revision
            if current.voice_id != remote_id or not _overwrite_allowed(adapter, runtime, current):
                raise VoiceManagementError("OVERWRITE_UNSUPPORTED", 400)
            previous_revision = _voice_metadata(current.metadata)["remote_revision"]
            await _check_context(adapter, cm, runtime, voice_data=record)
            latest = await asyncio.to_thread(cm.get_imported_voice, local_ref, include_inactive=True)
            if not latest or any(latest.get(key) != record.get(key) for key in (
                "scope_id", "remote_voice_id", "provider",
            )):
                raise VoiceManagementError("CONTEXT_CHANGED", 409)
            floor = _revision_floor(adapter, None, latest.get("remote_revision"), latest.get("overwrite_previous_revision"))
            order = adapter.compare_revisions(previous_revision, floor)
            if order is None or order < 0:
                raise VoiceManagementError("UPDATE_OUTCOME_UNKNOWN", 409)
            claim = asyncio.create_task(cm.aupdate_imported_voice(
                local_ref, runtime.scope_id, {
                    "overwrite_status": "processing", "overwrite_operation_id": operation_id,
                    "overwrite_previous_revision": previous_revision,
                    "overwrite_submission_phase": "prepared", "overwrite_terminal_reason": None,
                }, expected_operation_id=record.get("overwrite_operation_id") or "",
                expected_record_revision=latest.get("_record_revision", 0), return_receipt=True,
            ))
            try:
                claimed = await asyncio.shield(claim)
            except asyncio.CancelledError:
                # A to_thread write continues after its waiter is cancelled.
                # Join it before cleanup so it cannot write a late pending marker.
                while not claim.done():
                    try:
                        await asyncio.shield(claim)
                    except asyncio.CancelledError:
                        continue
                receipt = claim.result()
                claimed = receipt.record
                claim_owned = receipt.applied and claimed.get("overwrite_operation_id") == operation_id
                claim_revision = claimed.get("_record_revision", 0)
                raise
            # A rejected conditional write returns the stored winner. Only a
            # persisted new owner permits the provider mutation or cleanup.
            receipt = claimed
            claimed = receipt.record
            claim_owned = receipt.applied and claimed.get("overwrite_operation_id") == operation_id
            claim_revision = claimed.get("_record_revision", 0)
            if not claim_owned:
                raise VoiceManagementError("VOICE_STATE_CHANGED", 409)
            await _check_context(adapter, cm, runtime, voice_data=record)
            submission = asyncio.create_task(transition_with_context(
                adapter, cm, runtime, claimed, action="submit",
            ))
            try:
                permission = await asyncio.shield(submission)
            except asyncio.CancelledError:
                # A cancelled waiter cannot assume the permission write stopped.
                # Join the atomic transition before selecting cleanup evidence.
                while not submission.done():
                    try:
                        await asyncio.shield(submission)
                    except asyncio.CancelledError:
                        continue
                permission = submission.result()
                if permission.applied:
                    mutation_started = True
                    claim_revision = permission.record["_record_revision"]
                raise
            if not permission.applied:
                raise VoiceManagementError("VOICE_STATE_CHANGED", 409)
            mutation_started = True
            claim_revision = permission.record["_record_revision"]
            await _check_context(adapter, cm, runtime, voice_data=record)

        try:
            updated = await adapter.overwrite(
                runtime, remote_id, audio=audio, filename=filename, before_mutation=before_mutation,
            )
        except (Exception, asyncio.CancelledError) as exc:
            evidence = exc.details.get("attempt_outcome") if isinstance(exc, VoiceManagementError) else None
            outcome = (AttemptOutcome.NOT_SUBMITTED if not mutation_started else
                       AttemptOutcome(evidence) if evidence in {"not_submitted", "rejected"} else AttemptOutcome.UNKNOWN)
            status = "failed" if outcome in {AttemptOutcome.NOT_SUBMITTED, AttemptOutcome.REJECTED} else "unknown"
            sync = StateSync.UNCHANGED
            if claim_owned:
                try:
                    receipt = await asyncio.shield(cm.aupdate_imported_voice(local_ref, runtime.scope_id, {
                        "overwrite_status": status, "overwrite_operation_id": operation_id,
                    }, expected_operation_id=operation_id, expected_record_revision=claim_revision, return_receipt=True))
                    sync = StateSync.SAVED if receipt.applied else StateSync.UNCHANGED
                except ValueError as conflict:
                    # Never recreate a deleted record or overwrite a new owner.
                    sync = (StateSync.UNCHANGED if conflict.args == ("VOICE_CONTEXT_CHANGED",)
                            else StateSync.FAILED)
                except OSError:
                    sync = StateSync.FAILED
            if isinstance(exc, VoiceManagementError):
                exc.details.update(attempt_outcome=outcome.value, state_sync=sync.value)
            if sync == StateSync.FAILED and not isinstance(exc, asyncio.CancelledError):
                raise VoiceManagementError("LOCAL_SAVE_FAILED_AFTER_UPDATE", 500, {
                    "attempt_outcome": outcome.value, "state_sync": sync.value,
                }) from exc
            if mutation_started and status == "unknown" and not isinstance(exc, (VoiceManagementError, asyncio.CancelledError)):
                raise VoiceManagementError("UPDATE_OUTCOME_UNKNOWN", 502, {
                    "attempt_outcome": outcome.value, "state_sync": sync.value,
                }) from exc
            raise
        try:
            await _check_context(adapter, cm, runtime, voice_data=record)
        except VoiceManagementError as exc:
            exc.details["attempt_outcome"] = AttemptOutcome.ACCEPTED.value
            raise
        except (OSError, ValueError) as exc:
            context_changed = isinstance(exc, ValueError) and exc.args == ("VOICE_CONTEXT_CHANGED",)
            raise VoiceManagementError("CONTEXT_CHANGED" if context_changed else "STORAGE_ERROR",
                                       409 if context_changed else 500, {
                "attempt_outcome": "accepted", "state_sync": "unchanged",
            }) from exc
        if updated.voice_id != remote_id:
            try:
                receipt = await cm.aupdate_imported_voice(local_ref, runtime.scope_id, {"overwrite_status": "unknown"},
                                                         expected_operation_id=operation_id, expected_record_revision=claim_revision,
                                                         return_receipt=True)
            except Exception as exc:
                if isinstance(exc, ValueError) and exc.args == ("VOICE_CONTEXT_CHANGED",):
                    raise VoiceManagementError("CONTEXT_CHANGED", 409, {
                        "attempt_outcome": "unknown", "state_sync": "unchanged",
                    }) from exc
                raise VoiceManagementError("LOCAL_SAVE_FAILED_AFTER_UPDATE", 500, {
                    "attempt_outcome": "unknown", "state_sync": "failed",
                }) from exc
            raise VoiceManagementError("UPSTREAM_INVALID_RESPONSE", 502, {
                "attempt_outcome": "unknown", "state_sync": "saved" if receipt.applied else "unchanged",
            })
        order = adapter.compare_revisions(updated.metadata.get("remote_revision"), previous_revision)
        status = (
            "completed" if updated.status in {"ready", "completed", "OK"}
            and order == 1
            else "failed" if updated.status in {"failed", "unavailable"}
            else "processing"
        )
        values = _voice_metadata(updated.metadata)
        if order is None or order < 0:
            # An acknowledged mutation followed by an old/invalid observation
            # has an unknown outcome. Preserve the previous metadata and owner.
            status = "unknown"
            values = {}
        values.update({
            "overwrite_status": status, "remote_status": "unknown" if status == "unknown" else updated.status,
            "can_overwrite": status != "unknown" and _overwrite_allowed(adapter, runtime, updated),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        })
        try:
            receipt = await cm.aupdate_imported_voice(local_ref, runtime.scope_id, values,
                                                    expected_operation_id=operation_id,
                                                    expected_record_revision=claim_revision, return_receipt=True)
        except Exception as exc:
            if isinstance(exc, ValueError) and exc.args == ("VOICE_CONTEXT_CHANGED",):
                raise VoiceManagementError("CONTEXT_CHANGED", 409, {
                    "attempt_outcome": "accepted", "state_sync": "unchanged",
                }) from exc
            raise VoiceManagementError("LOCAL_SAVE_FAILED_AFTER_UPDATE", 500, {
                "attempt_outcome": "accepted", "state_sync": "failed",
            }) from exc
        saved = receipt.record
        return {
            "success": True, "voice_id": local_ref, "status": saved.get("overwrite_status", "completed"),
            "voice_data": public_voice_data(saved),
            "details": {"state_sync": "saved" if receipt.applied else "unchanged"},
        }


@_overwrite_feedback(mutation=False)
async def refresh_overwrite_status(adapter: VoiceManagementAdapter, cm, local_ref: str, *, token: str) -> dict:
    deadline = asyncio.timeout(30)
    try:
        # Two upstream queries must fit the frontend's 35-second request limit.
        async with deadline:
            return await _reconcile_overwrite_status(adapter, cm, local_ref, token=token)
    except TimeoutError:
        if not deadline.expired():
            raise
        raise VoiceManagementError("UPSTREAM_TIMEOUT", 504) from None


async def _reconcile_overwrite_status(adapter: VoiceManagementAdapter, cm, local_ref: str, *, token: str) -> dict:
    owner = None
    floor = None
    for _ in range(2):
        record = await asyncio.to_thread(cm.get_imported_voice, local_ref, include_inactive=True)
        if not record:
            raise VoiceManagementError("VOICE_NOT_FOUND", 404)
        identity = tuple(record.get(field) for field in (
            "scope_id", "provider", "remote_voice_id", "overwrite_operation_id",
        ))
        if owner is not None and identity != owner:
            raise VoiceManagementError("CONTEXT_CHANGED", 409)
        owner = identity
        runtime = await _runtime(adapter, cm, token, voice_data=record)
        lock = _OVERWRITE_LOCKS.get(local_ref)
        if lock is not None and lock.locked():
            raise VoiceManagementError("OPERATION_IN_PROGRESS", 409)
        if record.get("scope_id") != runtime.scope_id:
            raise VoiceManagementError("CONTEXT_CHANGED", 409)
        floor = _revision_floor(adapter, floor, record.get("remote_revision"), record.get("overwrite_previous_revision"))
        remote = await adapter.get_voice(runtime, record["remote_voice_id"])
        await _check_context(adapter, cm, runtime, voice_data=record)
        if remote is None:
            raise VoiceManagementError("VOICE_NOT_FOUND", 404)
        status = record.get("overwrite_status", "completed")
        previous = record.get("overwrite_previous_revision")
        revision = _voice_metadata(remote.metadata).get("remote_revision")
        order = adapter.compare_revisions(revision, floor)
        if order is None or order < 0:
            # A later query may hit a lagging replica. Its arrival time is not
            # proof of freshness, including after a local CAS conflict.
            raise VoiceManagementError("UPDATE_OUTCOME_UNKNOWN", 409)
        floor = revision
        # Reconciliation may settle only an unresolved operation. A later external
        # revision cannot turn a known rejection into success (or undo completion).
        if status in {"processing", "unknown"}:
            if remote.status in {"failed", "unavailable"}:
                status = "failed"
            elif remote.status in {"ready", "completed", "OK"} and adapter.compare_revisions(revision, previous) == 1:
                status = "completed"
        latest = await asyncio.to_thread(cm.get_imported_voice, local_ref, include_inactive=True)
        if not latest or latest.get("overwrite_operation_id") != record.get("overwrite_operation_id"):
            raise VoiceManagementError("CONTEXT_CHANGED", 409)
        values = _voice_metadata(remote.metadata)
        values.update({
            "overwrite_status": status, "remote_status": remote.status,
            "can_overwrite": _overwrite_allowed(adapter, runtime, remote),
        })
        receipt = await cm.aupdate_imported_voice(local_ref, runtime.scope_id, values,
                                                expected_operation_id=record.get("overwrite_operation_id") or "",
                                                expected_record_revision=record.get("_record_revision", 0), return_receipt=True)
        saved = receipt.record
        await _check_context(adapter, cm, runtime, voice_data=saved)
        saved_status = saved.get("overwrite_status", "completed")
        if status in {"completed", "failed"} and saved_status in {"processing", "unknown"}:
            # A pending observation won the CAS. Re-read and query once more;
            # reusing this old response could overwrite a newer remote revision.
            continue
        return {"success": True, "voice_id": local_ref,
                "status": saved_status, "voice_data": public_voice_data(saved),
                "details": {"state_sync": "saved" if receipt.applied else "unchanged"}}
    raise VoiceManagementError("OPERATION_IN_PROGRESS", 409)
