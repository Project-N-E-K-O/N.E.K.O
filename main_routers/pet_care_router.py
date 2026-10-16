"""PetCare account, role binding, and stage-2 snapshot endpoints."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse

import main_routers.community_oauth as community_oauth
from services.pet_care import (
    BindingServiceError,
    LocalPetCareBindingService,
    resolve_verified_account_id,
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


async def _verified_account(request: Request) -> str:
    # The local implementation is the current adapter for the N.E.K.O-wide
    # account contract; it is deliberately kept outside PetCare route logic.
    return await resolve_verified_account_id(request)


async def _resolve_context(request: Request, character_uid: str, *, bind: bool = False):
    account_id = await _verified_account(request)
    uid = _validate_uid(character_uid)
    config_manager = get_config_manager()
    await asyncio.to_thread(_assert_current_character, config_manager, uid)
    service = LocalPetCareBindingService(config_manager)
    method = service.bind if bind else service.resolve
    return await asyncio.to_thread(method, account_id, uid)


@router.get("/context", summary="读取当前账号与角色的宠物绑定上下文")
async def pet_care_context(
    request: Request,
    character_uid: str = Query(..., min_length=32, max_length=32),
):
    try:
        result = await _resolve_context(request, character_uid)
        return JSONResponse(result.as_dict(), headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    except HTTPException as exc:
        return _error(str(exc.detail), exc.status_code)
    except BindingServiceError as exc:
        return _error(exc.code, exc.status_code)


@router.get("/pets/{character_uid}", summary="读取当前绑定宠物的完整快照")
async def pet_care_pet_snapshot(request: Request, character_uid: str):
    """Return the server-owned snapshot without performing time settlement.

    A non-bound role receives the ordinary-cat binding context and no pet
    snapshot.  The account and bound role are always resolved by the service;
    the path UID is only the current-role verification input.
    """
    try:
        result = await _resolve_context(request, character_uid)
        return JSONResponse(
            result.as_dict(),
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
        return JSONResponse(result.as_dict(), headers={"Cache-Control": "no-store", "Pragma": "no-cache"})
    except HTTPException as exc:
        return _error(str(exc.detail), exc.status_code)
    except BindingServiceError as exc:
        return _error(exc.code, exc.status_code)
