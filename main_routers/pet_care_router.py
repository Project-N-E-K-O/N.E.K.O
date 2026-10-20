"""PetCare account, role binding, snapshot, care, task, and check-in endpoints."""

from __future__ import annotations

import asyncio
import os
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

import main_routers.community_oauth as community_oauth
from services.pet_care import (
    BindingServiceError,
    LocalPetCareBindingService,
    resolve_verified_account_session,
)
from utils.config_manager import get_config_manager
from utils.config_manager.reserved_schema import get_character_uid

router = APIRouter(prefix="/api/pet-care", tags=["pet-care"])

_CHARACTER_UID_RE = re.compile(r"^[0-9a-f]{32}$")


def _error(code: str, status_code: int) -> JSONResponse:
    return JSONResponse(
        {"detail": code, "code": code},
        status_code=status_code,
        headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
    )


def _pet_care_dev_tools_enabled() -> bool:
    """Return the explicit local opt-in for development-only PetCare tools.

    This is intentionally separate from the product/service configuration and
    defaults to disabled.  A deployed community service therefore never
    exposes a client capability merely because the route exists.
    """
    return os.environ.get("NEKO_PET_CARE_DEV_TOOLS", "").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _with_dev_tools(payload: dict[str, Any]) -> dict[str, Any]:
    if _pet_care_dev_tools_enabled() and payload.get("binding_status") == "bound" and payload.get("snapshot"):
        payload = dict(payload)
        payload["dev_tools"] = {"fill_status": True}
    return payload


def _validate_uid(value: Any) -> str:
    uid = value.strip() if isinstance(value, str) else ""
    if not _CHARACTER_UID_RE.fullmatch(uid):
        raise HTTPException(status_code=422, detail="invalid_character_uid")
    return uid


def _assert_current_character(config_manager: Any, uid: str) -> None:
    try:
        characters = config_manager.load_characters(require_authoritative=True)
    except Exception as exc:  # noqa: BLE001 - map storage/maintenance uniformly
        raise BindingServiceError("character_identity_unavailable") from exc
    catgirls = characters.get("猫娘") if isinstance(characters, dict) else None
    if not isinstance(catgirls, dict):
        raise BindingServiceError("character_identity_unavailable")
    if not any(
        isinstance(profile, dict) and get_character_uid(profile) == uid
        for profile in catgirls.values()
    ):
        raise HTTPException(status_code=404, detail="character_not_found")


async def _verified_session(request: Request):
    """Return the unified-account context used by Stage 8 reconciliation."""
    return await resolve_verified_account_session(request)


async def _resolve_bound_service(request: Request, uid: str, service: LocalPetCareBindingService):
    """Resolve the bound role through the same session fence as ``/context``.

    State-changing routes must not rely on a previous page load to trigger
    Stage 8 reconciliation.  They use this helper before their operation so
    deep links, refreshes, and direct retries share the service-owned session
    edge and account/role check.
    """
    session_context = await _verified_session(request)
    account_id = session_context.account_id
    context = await asyncio.to_thread(
        service.resolve,
        account_id,
        uid,
        session_context=session_context.as_dict(),
    )
    if context.binding_status != "bound":
        raise BindingServiceError("role_not_bound", status_code=409)
    return account_id, session_context, context


async def _resolve_context(request: Request, character_uid: str, *, bind: bool = False):
    session_context = await _verified_session(request)
    account_id = session_context.account_id
    uid = _validate_uid(character_uid)
    config_manager = get_config_manager()
    await asyncio.to_thread(_assert_current_character, config_manager, uid)
    service = LocalPetCareBindingService(config_manager)
    method = service.bind if bind else service.resolve
    return await asyncio.to_thread(method, account_id, uid, session_context=session_context.as_dict())


@router.get("/context", summary="读取当前账号与角色的宠物绑定上下文")
async def pet_care_context(
    request: Request,
    character_uid: str = Query(..., min_length=32, max_length=32),
):
    try:
        result = await _resolve_context(request, character_uid)
        return JSONResponse(_with_dev_tools(result.as_dict()), headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    except HTTPException as exc:
        return _error(str(exc.detail), exc.status_code)
    except BindingServiceError as exc:
        return _error(exc.code, exc.status_code)


@router.get("/pets/{character_uid}", summary="读取当前绑定宠物的完整快照")
async def pet_care_pet_snapshot(request: Request, character_uid: str):
    """Return the server-owned snapshot after the local service settles time.

    A non-bound role receives the ordinary-cat binding context and no pet
    snapshot.  The account and bound role are always resolved by the service;
    the path UID is only the current-role verification input.
    """
    try:
        result = await _resolve_context(request, character_uid)
        return JSONResponse(
            _with_dev_tools(result.as_dict()),
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    except HTTPException as exc:
        return _error(str(exc.detail), exc.status_code)
    except BindingServiceError as exc:
        return _error(exc.code, exc.status_code)


@router.post("/bind", summary="显式绑定当前角色到账号唯一宠物")
async def pet_care_bind(request: Request):
    try:
        payload = await request.json()
    except Exception as exc:  # noqa: BLE001
        return _error("invalid_json", 400)
    if not isinstance(payload, dict):
        return _error("invalid_json", 400)
    try:
        result = await _resolve_context(
            request,
            payload.get("character_uid") or payload.get("currentCharacterUid"),
            bind=True,
        )
        return JSONResponse(_with_dev_tools(result.as_dict()), headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    except HTTPException as exc:
        return _error(str(exc.detail), exc.status_code)
    except BindingServiceError as exc:
        return _error(exc.code, exc.status_code)


def _exploration_envelope(result: dict[str, Any]) -> dict[str, Any]:
    """Return the stable activity envelope without inventing client state."""
    return result


@router.post("/pets/{character_uid}/explorations", summary="开始一项配置中的探索活动")
async def pet_care_start_exploration(request: Request, character_uid: str):
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return _error("invalid_json", 400)
    if not isinstance(payload, dict):
        return _error("invalid_json", 400)
    try:
        uid = _validate_uid(character_uid)
        config_manager = get_config_manager()
        await asyncio.to_thread(_assert_current_character, config_manager, uid)
        idempotency_key = payload.get("idempotency_key") or payload.get("idempotencyKey") or request.headers.get("Idempotency-Key")
        location_id = payload.get("location_id") or payload.get("locationId")
        service = LocalPetCareBindingService(config_manager)
        account_id, session_context, _context = await _resolve_bound_service(request, uid, service)
        result = await asyncio.to_thread(
            service.start_exploration, account_id, uid,
            location_id=location_id, idempotency_key=idempotency_key,
            session_context=session_context.as_dict(),
        )
        return JSONResponse(_exploration_envelope(result), headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    except HTTPException as exc:
        return _error(str(exc.detail), exc.status_code)
    except BindingServiceError as exc:
        return _error(exc.code, exc.status_code)


@router.get("/pets/{character_uid}/explorations", summary="读取当前探索活动状态")
async def pet_care_current_exploration(request: Request, character_uid: str):
    try:
        uid = _validate_uid(character_uid)
        config_manager = get_config_manager()
        await asyncio.to_thread(_assert_current_character, config_manager, uid)
        service = LocalPetCareBindingService(config_manager)
        account_id, session_context, _context = await _resolve_bound_service(request, uid, service)
        result = await asyncio.to_thread(service.get_exploration, account_id, uid, session_context=session_context.as_dict())
        return JSONResponse(_exploration_envelope(result), headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    except HTTPException as exc:
        return _error(str(exc.detail), exc.status_code)
    except BindingServiceError as exc:
        return _error(exc.code, exc.status_code)


@router.get("/pets/{character_uid}/explorations/{exploration_id}", summary="读取探索活动状态")
async def pet_care_exploration_status(request: Request, character_uid: str, exploration_id: str):
    try:
        uid = _validate_uid(character_uid)
        config_manager = get_config_manager()
        await asyncio.to_thread(_assert_current_character, config_manager, uid)
        service = LocalPetCareBindingService(config_manager)
        account_id, session_context, _context = await _resolve_bound_service(request, uid, service)
        result = await asyncio.to_thread(service.get_exploration, account_id, uid, exploration_id=exploration_id, session_context=session_context.as_dict())
        return JSONResponse(_exploration_envelope(result), headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    except HTTPException as exc:
        return _error(str(exc.detail), exc.status_code)
    except BindingServiceError as exc:
        return _error(exc.code, exc.status_code)


async def _settle_exploration_route(request: Request, character_uid: str, exploration_id: str, *, kind: str):
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return _error("invalid_json", 400)
    if not isinstance(payload, dict):
        return _error("invalid_json", 400)
    try:
        uid = _validate_uid(character_uid)
        config_manager = get_config_manager()
        await asyncio.to_thread(_assert_current_character, config_manager, uid)
        idempotency_key = payload.get("idempotency_key") or payload.get("idempotencyKey") or request.headers.get("Idempotency-Key")
        service = LocalPetCareBindingService(config_manager)
        account_id, session_context, _context = await _resolve_bound_service(request, uid, service)
        method = service.complete_exploration if kind == "complete" else service.recall_exploration
        result = await asyncio.to_thread(
            method, account_id, uid, exploration_id=exploration_id, idempotency_key=idempotency_key,
            session_context=session_context.as_dict(),
        )
        return JSONResponse(_exploration_envelope(result), headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    except HTTPException as exc:
        return _error(str(exc.detail), exc.status_code)
    except BindingServiceError as exc:
        return _error(exc.code, exc.status_code)


@router.post("/pets/{character_uid}/explorations/{exploration_id}/complete", summary="正常完成探索并结算")
async def pet_care_complete_exploration(request: Request, character_uid: str, exploration_id: str):
    return await _settle_exploration_route(request, character_uid, exploration_id, kind="complete")


@router.post("/pets/{character_uid}/explorations/{exploration_id}/recall", summary="强制召回并按已完成比例结算")
async def pet_care_recall_exploration(request: Request, character_uid: str, exploration_id: str):
    return await _settle_exploration_route(request, character_uid, exploration_id, kind="recall")


@router.post("/pets/{character_uid}/care-actions", summary="执行一项已配置的照料或陪玩操作")
async def pet_care_action(request: Request, character_uid: str):
    """Submit a server-confirmed care or user-active play operation.

    The route only translates the authenticated request into the local
    PetCare adapter.  It does not infer role identity or settle values in the
    browser; the adapter returns the complete confirmed snapshot.
    """
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001 - malformed body is a client error
        return _error("invalid_json", 400)
    if not isinstance(payload, dict):
        return _error("invalid_json", 400)
    try:
        uid = _validate_uid(character_uid)
        config_manager = get_config_manager()
        await asyncio.to_thread(_assert_current_character, config_manager, uid)
        category = payload.get("category") or payload.get("action_category")
        content_id = payload.get("content_id") or payload.get("operation_id") or payload.get("item_id")
        idempotency_key = (
            payload.get("idempotency_key")
            or payload.get("idempotencyKey")
            or request.headers.get("Idempotency-Key")
        )
        source = payload.get("source")
        activity_id = payload.get("activity_id") or payload.get("activityId")
        user_initiated = payload.get("user_initiated") if "user_initiated" in payload else payload.get("userInitiated")
        cat_responded = payload.get("cat_responded") if "cat_responded" in payload else payload.get("catResponded")
        terminal_status = payload.get("terminal_status") or payload.get("terminalStatus")
        service = LocalPetCareBindingService(config_manager)
        account_id, session_context, _context = await _resolve_bound_service(request, uid, service)
        result = await asyncio.to_thread(
            service.care_action,
            account_id,
            uid,
            category=category,
            content_id=content_id,
            idempotency_key=idempotency_key,
            source=source,
            activity_id=activity_id,
            user_initiated=user_initiated,
            cat_responded=cat_responded,
            terminal_status=terminal_status,
            session_context=session_context.as_dict(),
        )
        snapshot = result.get("snapshot") if isinstance(result, dict) else None
        envelope = {
            "account_id": account_id,
            "current_character_uid": uid,
            "binding_status": "bound",
            "pet_id": snapshot.get("pet_id") if isinstance(snapshot, dict) else None,
            "bound_role_id": snapshot.get("bound_role_id") if isinstance(snapshot, dict) else None,
            "service": "local",
            "snapshot": snapshot,
            "settlement": result.get("settlement") if isinstance(result, dict) else None,
            "play": result.get("play") if isinstance(result, dict) else None,
            **service.catalog_for_snapshot(snapshot),
            "operation": result.get("operation") if isinstance(result, dict) else None,
            "result": result,
        }
        if isinstance(result, dict) and result.get("ok") is False:
            return JSONResponse(
                {**envelope, "detail": result.get("code"), "code": result.get("code")},
                status_code=409,
                headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
            )
        return JSONResponse(envelope, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    except HTTPException as exc:
        return _error(str(exc.detail), exc.status_code)
    except BindingServiceError as exc:
        return _error(exc.code, exc.status_code)


@router.post("/pets/{character_uid}/debug/fill-status", summary="开发调试：填满三项基础状态")
async def pet_care_debug_fill_status(request: Request, character_uid: str):
    """Fill satiety, energy and cleanliness in a local development build.

    The endpoint is deliberately absent from the usable contract unless the
    process has explicitly opted in with ``NEKO_PET_CARE_DEV_TOOLS``.  The
    service still performs the account, role, activity and idempotency checks;
    this route only exposes the developer capability to the local UI.
    """
    if not _pet_care_dev_tools_enabled():
        return _error("dev_tools_unavailable", 404)
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return _error("invalid_json", 400)
    if not isinstance(payload, dict):
        return _error("invalid_json", 400)
    try:
        uid = _validate_uid(character_uid)
        config_manager = get_config_manager()
        await asyncio.to_thread(_assert_current_character, config_manager, uid)
        idempotency_key = (
            payload.get("idempotency_key")
            or payload.get("idempotencyKey")
            or request.headers.get("Idempotency-Key")
        )
        service = LocalPetCareBindingService(config_manager)
        account_id, session_context, _context = await _resolve_bound_service(request, uid, service)
        result = await asyncio.to_thread(
            service.debug_fill_status,
            account_id,
            uid,
            idempotency_key=idempotency_key,
            session_context=session_context.as_dict(),
        )
        snapshot = result.get("snapshot") if isinstance(result, dict) else None
        envelope = {
            "account_id": account_id,
            "current_character_uid": uid,
            "binding_status": "bound",
            "pet_id": snapshot.get("pet_id") if isinstance(snapshot, dict) else None,
            "bound_role_id": snapshot.get("bound_role_id") if isinstance(snapshot, dict) else None,
            "service": "local",
            "snapshot": snapshot,
            "settlement": result.get("settlement") if isinstance(result, dict) else None,
            "operation": result.get("operation") if isinstance(result, dict) else None,
            "result": result,
        }
        if isinstance(result, dict) and result.get("ok") is False:
            return JSONResponse(
                {**_with_dev_tools(envelope), "detail": result.get("code"), "code": result.get("code")},
                status_code=409,
                headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
            )
        return JSONResponse(
            _with_dev_tools(envelope),
            headers={"Cache-Control": "no-store", "Pragma": "no-cache"},
        )
    except HTTPException as exc:
        return _error(str(exc.detail), exc.status_code)
    except BindingServiceError as exc:
        return _error(exc.code, exc.status_code)


@router.get("/pets/{character_uid}/tasks", summary="读取当前服务任务")
async def pet_care_tasks(request: Request, character_uid: str):
    try:
        uid = _validate_uid(character_uid)
        config_manager = get_config_manager()
        await asyncio.to_thread(_assert_current_character, config_manager, uid)
        service = LocalPetCareBindingService(config_manager)
        account_id, session_context, _context = await _resolve_bound_service(request, uid, service)
        result = await asyncio.to_thread(service.get_tasks, account_id, uid, session_context=session_context.as_dict())
        return JSONResponse(result, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    except HTTPException as exc:
        return _error(str(exc.detail), exc.status_code)
    except BindingServiceError as exc:
        return _error(exc.code, exc.status_code)


@router.post("/pets/{character_uid}/tasks/{task_id}/claim", summary="手动领取一项任务奖励")
async def pet_care_claim_task(request: Request, character_uid: str, task_id: str):
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return _error("invalid_json", 400)
    if not isinstance(payload, dict):
        return _error("invalid_json", 400)
    try:
        uid = _validate_uid(character_uid)
        config_manager = get_config_manager()
        await asyncio.to_thread(_assert_current_character, config_manager, uid)
        idempotency_key = payload.get("idempotency_key") or payload.get("idempotencyKey") or request.headers.get("Idempotency-Key")
        service = LocalPetCareBindingService(config_manager)
        account_id, session_context, _context = await _resolve_bound_service(request, uid, service)
        result = await asyncio.to_thread(
            service.claim_task, account_id, uid, task_id=task_id, idempotency_key=idempotency_key,
            session_context=session_context.as_dict(),
        )
        snapshot = result.get("snapshot") if isinstance(result, dict) else None
        envelope = {
            "account_id": account_id,
            "current_character_uid": uid,
            "binding_status": "bound",
            "pet_id": snapshot.get("pet_id") if isinstance(snapshot, dict) else None,
            "bound_role_id": snapshot.get("bound_role_id") if isinstance(snapshot, dict) else None,
            "service": "local",
            "tasks": snapshot.get("tasks") if isinstance(snapshot, dict) else None,
            "snapshot": snapshot,
            **service.catalog_for_snapshot(snapshot),
            "operation": result.get("operation") if isinstance(result, dict) else None,
            "reward": result.get("reward") if isinstance(result, dict) else None,
            "result": result,
        }
        if isinstance(result, dict) and result.get("ok") is False:
            return JSONResponse({**envelope, "detail": result.get("code"), "code": result.get("code")}, status_code=409, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
        return JSONResponse(envelope, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    except HTTPException as exc:
        return _error(str(exc.detail), exc.status_code)
    except BindingServiceError as exc:
        return _error(exc.code, exc.status_code)


@router.post("/pets/{character_uid}/status-recovery", summary="使用服务确认的恢复物品")
async def pet_care_status_recovery(request: Request, character_uid: str):
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return _error("invalid_json", 400)
    if not isinstance(payload, dict):
        return _error("invalid_json", 400)
    try:
        uid = _validate_uid(character_uid)
        config_manager = get_config_manager()
        await asyncio.to_thread(_assert_current_character, config_manager, uid)
        status_key = payload.get("status") or payload.get("negative_status")
        idempotency_key = payload.get("idempotency_key") or payload.get("idempotencyKey") or request.headers.get("Idempotency-Key")
        service = LocalPetCareBindingService(config_manager)
        account_id, session_context, _context = await _resolve_bound_service(request, uid, service)
        result = await asyncio.to_thread(
            service.use_recovery_item,
            account_id,
            uid,
            status_key=status_key,
            idempotency_key=idempotency_key,
            session_context=session_context.as_dict(),
        )
        snapshot = result.get("snapshot") if isinstance(result, dict) else None
        envelope = {
            "account_id": account_id,
            "current_character_uid": uid,
            "binding_status": "bound",
            "pet_id": snapshot.get("pet_id") if isinstance(snapshot, dict) else None,
            "bound_role_id": snapshot.get("bound_role_id") if isinstance(snapshot, dict) else None,
            "service": "local",
            "snapshot": snapshot,
            "tasks": snapshot.get("tasks") if isinstance(snapshot, dict) else None,
            "operation": result.get("operation") if isinstance(result, dict) else None,
            "result": result,
        }
        return JSONResponse(envelope, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    except HTTPException as exc:
        return _error(str(exc.detail), exc.status_code)
    except BindingServiceError as exc:
        return _error(exc.code, exc.status_code)


@router.post("/pets/{character_uid}/daily-check-in", summary="确认当前服务日首次登录")
async def pet_care_daily_check_in(request: Request, character_uid: str):
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        return _error("invalid_json", 400)
    if not isinstance(payload, dict):
        return _error("invalid_json", 400)
    try:
        uid = _validate_uid(character_uid)
        config_manager = get_config_manager()
        await asyncio.to_thread(_assert_current_character, config_manager, uid)
        idempotency_key = payload.get("idempotency_key") or payload.get("idempotencyKey") or request.headers.get("Idempotency-Key")
        service = LocalPetCareBindingService(config_manager)
        account_id, session_context, _context = await _resolve_bound_service(request, uid, service)
        result = await asyncio.to_thread(service.daily_check_in, account_id, uid, idempotency_key=idempotency_key, session_context=session_context.as_dict())
        snapshot = result.get("snapshot") if isinstance(result, dict) else None
        envelope = {
            "account_id": account_id,
            "current_character_uid": uid,
            "binding_status": "bound",
            "pet_id": snapshot.get("pet_id") if isinstance(snapshot, dict) else None,
            "bound_role_id": snapshot.get("bound_role_id") if isinstance(snapshot, dict) else None,
            "service": "local",
            "snapshot": snapshot,
            **service.catalog_for_snapshot(snapshot),
            "operation": result.get("operation") if isinstance(result, dict) else None,
            "progress_delta": result.get("progress_delta") if isinstance(result, dict) else None,
            "unlocked": result.get("unlocked") if isinstance(result, dict) else [],
            "result": result,
        }
        return JSONResponse(envelope, headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    except HTTPException as exc:
        return _error(str(exc.detail), exc.status_code)
    except BindingServiceError as exc:
        return _error(exc.code, exc.status_code)
