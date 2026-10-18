"""Local desktop controls for opt-in chat originals, independent of memory edits."""

from __future__ import annotations

import sqlite3
from typing import Annotated, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from pydantic import BaseModel, ConfigDict, StrictBool, StrictInt, StrictStr

from services.chat_sync.runtime import get_desktop_chat_sync_runtime
from utils.config_manager import get_config_manager
from utils.config_manager.reserved_schema import get_character_uid


async def _local_access(request: Request, response: Response) -> None:
    """Protect local chat contents and consent from remote and cross-origin calls."""
    from main_routers.card_drop_router import _local_request_source_allowed
    from main_routers.local_access import is_local_oauth_status_request

    response.headers["Cache-Control"] = "no-store"
    if not is_local_oauth_status_request(request) or not _local_request_source_allowed(request):
        raise HTTPException(403, "chat_sync_local_access_required", headers={"Cache-Control": "no-store"})


router = APIRouter(prefix="/api/chat-sync", tags=["chat-sync"], dependencies=[Depends(_local_access)])


class ServiceConfiguration(BaseModel):
    """Browser requests use the built-in origin; never forward OAuth to a supplied host."""

    model_config = ConfigDict(extra="forbid")
    serviceUrl: Literal["https://sync.nekotech.cn", "https://sync.nekotech.cn/"]


class CharacterSyncChoice(BaseModel):
    """Accept explicit history consent; missing/old-client fields never expand a previous grant."""

    model_config = ConfigDict(extra="forbid")
    enabled: StrictBool
    includeHistory: StrictBool = False


class ManualSyncRequest(BaseModel):
    """Bound manual work; runtime scheduling still enforces device retry gates."""

    model_config = ConfigDict(extra="forbid")
    byteBudget: StrictInt
    maxPullPages: StrictInt


class DeleteOriginalsRequest(BaseModel):
    """Delete only the stable IDs in the reviewed local operation."""

    model_config = ConfigDict(extra="forbid")
    messageIds: list[StrictStr]


class HistoryApprovalRequest(BaseModel):
    """Approve only the exact persisted member snapshot reviewed by this user."""

    model_config = ConfigDict(extra="forbid")
    taskId: StrictStr


class ProfileChoiceRequest(BaseModel):
    """Pin the saved local candidate and cloud revision shown in the consent UI."""

    model_config = ConfigDict(extra="forbid")
    localHash: StrictStr
    revision: StrictInt


class ProfileResolveRequest(ProfileChoiceRequest):
    """Resolve retained candidates without silently accepting a renamed role."""

    choice: Literal["local", "remote"]
    confirmRename: StrictBool = False


class ProfileImportRequest(BaseModel):
    """Import only the exact server-owned version reviewed in the local UI."""

    model_config = ConfigDict(extra="forbid")
    revision: StrictInt
    profileHash: StrictStr


async def _runtime():
    """Resolve the configured runtime root only after local authorization."""
    return await get_desktop_chat_sync_runtime(get_config_manager())


Runtime = Annotated[object, Depends(_runtime)]


async def _characters() -> list[dict]:
    """Use durable desktop UIDs; never bind by display name or issue IDs on reads."""
    data = await get_config_manager().aload_characters()
    entries = data.get("猫娘", {}) if isinstance(data, dict) else {}
    return [
        {"characterSyncId": uid, "displayName": name}
        for name, profile in entries.items() if isinstance(profile, dict) and (uid := get_character_uid(profile))
    ]


async def _character(character_uid: str) -> dict:
    """Prevent paths for absent or newly imported unrelated characters."""
    for character in await _characters():
        if character["characterSyncId"] == character_uid:
            return character
    raise HTTPException(404, "chat_sync_character_not_found", headers={"Cache-Control": "no-store"})


def _error(exc: ValueError) -> HTTPException:
    """Expose fixed adapter errors without bearer, network body or account PII."""
    code = str(exc)
    status = 401 if code == "chat_sync_login_required" else 409
    if code in {"chat_sync_unavailable", "chat_sync_service_rejected", "chat_sync_retry_later"}:
        status = 503
    return HTTPException(status, code, headers={"Cache-Control": "no-store"})


@router.get("/status")
async def status(runtime: Runtime):
    """Report opt-in per local character without claiming chats are uploaded."""
    state = await runtime.status()
    bindings = {item["character_uid"]: item for item in state.pop("bindings")}
    choices = state.pop("syncChoices", {})
    pending_choices = state.pop("syncPendingChoices", {})
    setup_errors = state.pop("syncSetupErrors", {})
    characters = await _characters()
    for character in characters:
        if character["characterSyncId"] in setup_errors:
            character["syncSetupError"] = setup_errors[character["characterSyncId"]]
        binding = bindings.get(character["characterSyncId"])
        paused_or_bound = binding and (binding["binding_id"] or binding["generation"] > 1)
        # Pending On is the saved user choice even before online owner checks
        # activate a paused binding. Editing the card must preserve that choice.
        character["syncPending"] = bool(pending_choices.get(character["characterSyncId"]))
        # A role without a saved choice is Off, matching cloud saves: only an
        # explicit checked save grants sync.
        character["syncEnabled"] = (bool(binding["enabled"]) or character["syncPending"]) if paused_or_bound else choices.get(character["characterSyncId"], False)
        character["binding"] = None if binding is None else {
            "ownerId": binding["owner_id"], "bindingId": binding["binding_id"],
            "enabled": bool(binding["enabled"]), "generation": binding["generation"],
            **{key: binding[key] for key in ("pendingOriginals", "pendingDeletions", "cursor", "hasSealedBatch", "localOnlyOriginals")},
            # Only fixed adapter codes are exposed, never remote response text.
            "lastSyncError": binding.get("lastSyncError"),
            "writerRecoveryRequired": binding.get("writerRecoveryRequired", False),
            # Profile revision hints contain no raw settings or credential data.
            "profile": binding.get("profile"),
            # Registration progress never represents cloud delivery or recovery.
            "historyBackfill": binding.get("historyBackfill"),
        }
    return {**state, "characters": characters}


@router.put("/configuration")
async def configure(body: ServiceConfiguration, runtime: Runtime):
    """Save explicit HTTPS setup while refusing to redirect existing bindings."""
    try:
        await runtime.configure(body.serviceUrl)
    except ValueError as exc:
        raise _error(exc) from None
    return {"configured": True}


@router.post("/wake")
async def wake(runtime: Runtime, body: None = Body(default=None)):
    """Wake all opted-in roles without exposing OAuth or awaiting remote I/O."""
    try:
        await runtime.request_catchup()
    except ValueError as exc:
        raise _error(exc) from None
    except (OSError, sqlite3.Error):
        raise _error(ValueError("chat_sync_unavailable")) from None
    return {"scheduled": True}


@router.post("/characters/{character_uid}/sync-choice")
async def save_sync_choice(character_uid: str, body: CharacterSyncChoice, runtime: Runtime):
    """Resolve the saved UID and treat this save as the single affirmative choice."""
    character = await _character(character_uid)
    try:
        return await runtime.save_character_sync_choice(character_uid, character["displayName"], body.enabled,
                                                       include_history=body.includeHistory)
    except ValueError as exc:
        raise _error(exc) from None
    except (OSError, sqlite3.Error):
        raise _error(ValueError("chat_sync_unavailable")) from None


@router.post("/characters/{character_uid}/enable")
async def enable(character_uid: str, runtime: Runtime):
    """Bind the real saved UID to the service-verified community account."""
    character = await _character(character_uid)
    try:
        binding = await runtime.enable(character_uid, character["displayName"])
    except ValueError as exc:
        raise _error(exc) from None
    runtime.start_deletion_flush(character_uid)
    return {"characterSyncId": character_uid, "ownerId": binding["owner_id"], "enabled": True}


@router.post("/characters/{character_uid}/sync-choice/run")
async def run_sync_choice(character_uid: str, runtime: Runtime, body: None = Body(default=None)):
    """Run saved consent for an explicit target without suppressing login or network errors."""
    await _character(character_uid)
    try:
        return await runtime.run_character_sync_choice(character_uid)
    except ValueError as exc:
        raise _error(exc) from None
    except (OSError, sqlite3.Error):
        raise _error(ValueError("chat_sync_unavailable")) from None


@router.post("/characters/{character_uid}/pause")
async def pause(character_uid: str, runtime: Runtime):
    """Pause this device while preserving cloud data and local account ownership."""
    await _character(character_uid)
    await runtime.pause(character_uid)
    return {"characterSyncId": character_uid, "enabled": False}


@router.post("/characters/{character_uid}/sync")
async def sync(character_uid: str, body: ManualSyncRequest, runtime: Runtime):
    """Request bounded work without bypassing retries or invoking memory processing."""
    await _character(character_uid)
    try:
        return await runtime.sync(character_uid, byte_budget=body.byteBudget, max_pull_pages=body.maxPullPages)
    except ValueError as exc:
        raise _error(exc) from None


@router.get("/characters/{character_uid}/history")
async def history(character_uid: str, runtime: Runtime):
    """Return canonical originals and separate local-only replies for passive display."""
    await _character(character_uid)
    return {"characterSyncId": character_uid, "originals": await runtime.history(character_uid),
            "localReplies": await runtime.local_replies(character_uid)}


@router.post("/characters/{character_uid}/history/preview")
async def preview_history(character_uid: str, runtime: Runtime):
    """Show one authenticated target name and precise trusted historical count."""
    character = await _character(character_uid)
    try:
        return await runtime.preview_history(character_uid, character["displayName"])
    except ValueError as exc:
        raise _error(exc) from None


@router.post("/characters/{character_uid}/history/approve")
async def approve_history(character_uid: str, body: HistoryApprovalRequest, runtime: Runtime):
    """Queue fixed local history registration without waiting for an upload."""
    await _character(character_uid)
    try:
        return await runtime.approve_history(character_uid, body.taskId)
    except ValueError as exc:
        raise _error(exc) from None


@router.post("/characters/{character_uid}/profile/local")
async def local_profile(character_uid: str, runtime: Runtime):
    """Observe saved raw settings even when the user declines uploading them."""
    await _character(character_uid)
    try:
        return await runtime.profiles.local(character_uid)
    except ValueError as exc:
        raise _error(exc) from None


@router.get("/cloud/characters")
async def cloud_characters(runtime: Runtime):
    """Cloud discovery is explicit and cannot create local characters."""
    try:
        return await runtime.profiles.cloud_roles()
    except ValueError as exc:
        raise _error(exc) from None


@router.get("/cloud/characters/{character_uid}/profile")
async def cloud_profile(character_uid: str, runtime: Runtime):
    """Preview account-owned raw settings before the user chooses a new role."""
    try:
        return await runtime.profiles.preview(character_uid)
    except ValueError as exc:
        raise _error(exc) from None


@router.post("/cloud/characters/{character_uid}/import")
async def import_cloud_profile(character_uid: str, body: ProfileImportRequest, runtime: Runtime):
    """Create once by UID; a name collision never replaces an existing character."""
    try:
        return await runtime.profiles.import_role(character_uid, body.revision, body.profileHash)
    except ValueError as exc:
        raise _error(exc) from None


@router.post("/characters/{character_uid}/profile/approve")
async def approve_profile(character_uid: str, body: ProfileChoiceRequest, runtime: Runtime):
    """Queue one immutable approved snapshot; the browser supplies no profile/owner."""
    await _character(character_uid)
    try:
        return await runtime.profiles.approve(character_uid, body.localHash, body.revision)
    except ValueError as exc:
        raise _error(exc) from None


@router.post("/characters/{character_uid}/profile/resolve")
async def resolve_profile(character_uid: str, body: ProfileResolveRequest, runtime: Runtime):
    """Keep both versions until a reviewed choice passes the current CAS guards."""
    await _character(character_uid)
    try:
        if body.choice == "local":
            return await runtime.profiles.approve(character_uid, body.localHash, body.revision, resolve=True)
        return await runtime.profiles.step(character_uid, manual=True, choose_remote=True,
                                           local_hash=body.localHash, revision=body.revision,
                                           confirm_rename=body.confirmRename)
    except ValueError as exc:
        raise _error(exc) from None


@router.post("/characters/{character_uid}/deletions")
async def delete_originals(character_uid: str, body: DeleteOriginalsRequest, runtime: Runtime):
    """Persist exact-ID deletion first; offline state never blocks hiding chats."""
    await _character(character_uid)
    try:
        receipt = await runtime.delete(character_uid, body.messageIds)
    except ValueError as exc:
        raise _error(exc) from None
    runtime.start_deletion_flush(character_uid)
    return receipt


@router.post("/characters/{character_uid}/clear")
async def clear_originals(character_uid: str, runtime: Runtime):
    """Clear known sync originals and local-only replies, excluding future messages."""
    await _character(character_uid)
    try:
        receipt = await runtime.clear(character_uid)
    except ValueError as exc:
        raise _error(exc) from None
    runtime.start_deletion_flush(character_uid)
    return receipt


async def close_runtime() -> None:
    """Release adapter clients without clearing local original or deletion queues."""
    from services.chat_sync.runtime import close_desktop_chat_sync_runtimes
    await close_desktop_chat_sync_runtimes()


@router.post("/characters/{character_uid}/recovery/preview")
async def recovery_preview(character_uid: str, body: ServiceConfiguration, runtime: Runtime):
    """Renew installation authority and verify one role before explicit history consent."""
    character = await _character(character_uid)
    try:
        return await runtime.recover_writer_role(character_uid, character["displayName"], service_url=body.serviceUrl)
    except ValueError as exc:
        raise _error(exc) from None


@router.post("/characters/{character_uid}/recovery/deletions/approve")
async def recovery_approve_deletions(character_uid: str, body: HistoryApprovalRequest, runtime: Runtime):
    """Approve only the exact current-owner deletion set reviewed in recovery UI."""
    await _character(character_uid)
    try:
        result = await runtime.approve_recovery_deletions(character_uid, body.taskId)
    except ValueError as exc:
        raise _error(exc) from None
    runtime.start_deletion_flush(character_uid)
    return result
