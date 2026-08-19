# -*- coding: utf-8 -*-
"""Loopback-only API for user-created local avatar tools."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from main_routers.cookies_login_router import verify_local_access
from main_routers.shared_state import get_config_manager
from main_routers.system_router._shared import _validate_local_mutation_request
from utils.avatar_tool_store import AvatarToolStoreError, get_avatar_tool_store
from utils.cloudsave_runtime import MaintenanceModeError, maintenance_error_payload


router = APIRouter(
    prefix="/api/avatar-tools",
    tags=["avatar-tools"],
    dependencies=[Depends(verify_local_access)],
)


def _error_response(exc: AvatarToolStoreError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "error_code": exc.code, "error": str(exc)},
    )


async def _read_upload_limited(
    upload: UploadFile,
    maximum: int,
    *,
    error_code: str,
    error_message: str,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(min(1024 * 1024, maximum + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > maximum:
            raise AvatarToolStoreError(error_code, error_message, status_code=413)
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("")
async def list_avatar_tools():
    store = get_avatar_tool_store(get_config_manager())
    try:
        items = await asyncio.to_thread(store.list_items)
    except AvatarToolStoreError as exc:
        return _error_response(exc)
    return {"ok": True, "items": items, "limits": store.limits}


@router.post("")
async def create_avatar_tool(
    request: Request,
    name: str = Form(...),
    change_mode: str = Form(...),
    change_meanings: list[str] = Form(...),
    default_image: UploadFile = File(...),
    change_images: list[UploadFile] = File(...),
    normal_sound: UploadFile | None = File(None),
):
    rejected = _validate_local_mutation_request(request)
    if rejected is not None:
        await default_image.close()
        for upload in change_images:
            await upload.close()
        if normal_sound is not None:
            await normal_sound.close()
        return rejected

    store = get_avatar_tool_store(get_config_manager())
    try:
        if len(change_images) > store.limits["maxChangeImages"]:
            raise AvatarToolStoreError(
                "change_items_invalid",
                "Image change item count is invalid",
                status_code=413,
            )
        uploaded = await asyncio.gather(
            _read_upload_limited(
                default_image,
                store.limits["maxImageBytes"],
                error_code="image_too_large",
                error_message="PNG image is too large",
            ),
            *(
                _read_upload_limited(
                    upload,
                    store.limits["maxImageBytes"],
                    error_code="image_too_large",
                    error_message="PNG image is too large",
                )
                for upload in change_images
            ),
        )
        normal_sound_data = None
        if normal_sound is not None:
            normal_sound_data = await _read_upload_limited(
                normal_sound,
                store.limits["maxAudioBytes"],
                error_code="audio_too_large",
                error_message="MP3 audio is too large",
            )
        item = await asyncio.to_thread(
            store.create_tool,
            name=name,
            change_mode=change_mode,
            change_meanings=change_meanings,
            default_image=uploaded[0],
            change_images=list(uploaded[1:]),
            normal_sound=normal_sound_data,
        )
    except AvatarToolStoreError as exc:
        return _error_response(exc)
    except MaintenanceModeError as exc:
        return JSONResponse(status_code=409, content=maintenance_error_payload(exc))
    finally:
        await default_image.close()
        for upload in change_images:
            await upload.close()
        if normal_sound is not None:
            await normal_sound.close()

    return JSONResponse(status_code=201, content={"ok": True, "item": item})
