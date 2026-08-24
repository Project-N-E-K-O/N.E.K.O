# -*- coding: utf-8 -*-
"""Loopback-only API for user-created local avatar tools."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from main_routers.cookies_login_router import verify_local_access
from main_routers.shared_state import get_config_manager
from main_routers.system_router._shared import _validate_local_mutation_request
from utils.avatar_tool_store import (
    AvatarToolStoreError,
    get_avatar_tool_store,
    is_local_avatar_tool_id,
)
from utils.cloudsave_runtime import MaintenanceModeError, maintenance_error_payload


router = APIRouter(
    prefix="/api/avatar-tools",
    tags=["avatar-tools"],
    dependencies=[Depends(verify_local_access)],
)


def _error_response(exc: AvatarToolStoreError) -> JSONResponse:
    detail = {
        **({"field": exc.field} if exc.field is not None else {}),
        **({"index": exc.index} if exc.index is not None else {}),
    }
    return JSONResponse(
        status_code=exc.status_code,
        content={"ok": False, "error_code": exc.code, "error": str(exc), **detail},
    )


async def _read_upload_limited(
    upload: UploadFile,
    maximum: int,
    *,
    error_code: str,
    error_message: str,
    field: str,
    index: int | None = None,
) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(min(1024 * 1024, maximum + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > maximum:
            raise AvatarToolStoreError(
                error_code,
                error_message,
                status_code=413,
                field=field,
                index=index,
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("")
async def list_avatar_tools():
    store = get_avatar_tool_store(get_config_manager())
    try:
        items = await asyncio.to_thread(store.list_items)
    except AvatarToolStoreError as exc:
        return _error_response(exc)
    except MaintenanceModeError as exc:
        return JSONResponse(status_code=409, content=maintenance_error_payload(exc))
    return {"ok": True, "items": items, "limits": store.limits}


@router.post("")
async def create_avatar_tool(
    request: Request,
    tool_id: str = Form(...),
    name: str = Form(...),
    change_mode: str = Form(...),
    change_meanings: list[str] = Form(...),
    default_image: UploadFile = File(...),
    change_images: list[UploadFile] = File(...),
    normal_sound: UploadFile | None = File(None),
    special_probability: str | None = Form(None),
    special_image: UploadFile | None = File(None),
    special_meaning: str | None = Form(None),
    special_sound: UploadFile | None = File(None),
):
    rejected = _validate_local_mutation_request(request)
    if rejected is not None:
        await default_image.close()
        for upload in change_images:
            await upload.close()
        if normal_sound is not None:
            await normal_sound.close()
        if special_image is not None:
            await special_image.close()
        if special_sound is not None:
            await special_sound.close()
        return rejected

    store = get_avatar_tool_store(get_config_manager())
    try:
        if not is_local_avatar_tool_id(tool_id):
            raise AvatarToolStoreError(
                "invalid_tool_id",
                "Invalid local avatar tool ID",
            )
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
                field="default_image",
            ),
            *(
                _read_upload_limited(
                    upload,
                    store.limits["maxImageBytes"],
                    error_code="image_too_large",
                    error_message="PNG image is too large",
                    field="change_image",
                    index=index,
                )
                for index, upload in enumerate(change_images)
            ),
        )
        normal_sound_data = None
        if normal_sound is not None:
            normal_sound_data = await _read_upload_limited(
                normal_sound,
                store.limits["maxAudioBytes"],
                error_code="audio_too_large",
                error_message="MP3 audio is too large",
                field="normal_sound",
            )
        special_image_data = None
        if special_image is not None:
            special_image_data = await _read_upload_limited(
                special_image,
                store.limits["maxImageBytes"],
                error_code="image_too_large",
                error_message="PNG image is too large",
                field="special_image",
            )
        special_sound_data = None
        if special_sound is not None:
            special_sound_data = await _read_upload_limited(
                special_sound,
                store.limits["maxAudioBytes"],
                error_code="special_audio_too_large",
                error_message="MP3 audio is too large",
                field="special_sound",
            )
        item = await asyncio.to_thread(
            store.create_tool,
            tool_id=tool_id,
            name=name,
            change_mode=change_mode,
            change_meanings=change_meanings,
            default_image=uploaded[0],
            change_images=list(uploaded[1:]),
            normal_sound=normal_sound_data,
            special_probability=special_probability,
            special_image=special_image_data,
            special_meaning=special_meaning,
            special_sound=special_sound_data,
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
        if special_image is not None:
            await special_image.close()
        if special_sound is not None:
            await special_sound.close()

    return JSONResponse(status_code=201, content={"ok": True, "item": item})


@router.get("/{tool_id}")
async def get_avatar_tool_detail(tool_id: str):
    store = get_avatar_tool_store(get_config_manager())
    try:
        detail = await asyncio.to_thread(store.get_detail, tool_id)
    except AvatarToolStoreError as exc:
        return _error_response(exc)
    except MaintenanceModeError as exc:
        return JSONResponse(status_code=409, content=maintenance_error_payload(exc))
    return {"ok": True, "detail": detail, "limits": store.limits}


@router.put("/{tool_id}")
async def update_avatar_tool(
    request: Request,
    tool_id: str,
    base_revision: str = Form(...),
    name: str = Form(...),
    change_mode: str = Form(...),
    change_meanings: list[str] = Form(...),
    change_resources: list[str] = Form(...),
    default_resource: str | None = Form(None),
    default_image: UploadFile | None = File(None),
    change_images: list[UploadFile] | None = File(None),
    normal_sound_resource: str | None = Form(None),
    normal_sound: UploadFile | None = File(None),
    special_probability: str | None = Form(None),
    special_image_resource: str | None = Form(None),
    special_image: UploadFile | None = File(None),
    special_meaning: str | None = Form(None),
    special_sound_resource: str | None = Form(None),
    special_sound: UploadFile | None = File(None),
):
    change_uploads = change_images or []
    uploads = [
        *(upload for upload in [default_image] if upload is not None),
        *change_uploads,
        *(upload for upload in [normal_sound, special_image, special_sound] if upload is not None),
    ]
    rejected = _validate_local_mutation_request(request)
    if rejected is not None:
        for upload in uploads:
            await upload.close()
        return rejected

    store = get_avatar_tool_store(get_config_manager())
    try:
        if (
            len(change_resources) > store.limits["maxChangeImages"]
            or len(change_uploads) > store.limits["maxChangeImages"]
        ):
            raise AvatarToolStoreError(
                "change_items_invalid",
                "Image change item count is invalid",
                status_code=413,
            )
        default_image_data = (
            await _read_upload_limited(
                default_image,
                store.limits["maxImageBytes"],
                error_code="image_too_large",
                error_message="PNG image is too large",
                field="default_image",
            )
            if default_image is not None
            else None
        )
        replacement_positions = [
            index for index, resource in enumerate(change_resources) if not resource
        ]
        change_image_data = await asyncio.gather(*(
            _read_upload_limited(
                upload,
                store.limits["maxImageBytes"],
                error_code="image_too_large",
                error_message="PNG image is too large",
                field="change_image",
                index=(replacement_positions[index]
                       if index < len(replacement_positions) else index),
            )
            for index, upload in enumerate(change_uploads)
        ))
        normal_sound_data = (
            await _read_upload_limited(
                normal_sound,
                store.limits["maxAudioBytes"],
                error_code="audio_too_large",
                error_message="MP3 audio is too large",
                field="normal_sound",
            )
            if normal_sound is not None
            else None
        )
        special_image_data = (
            await _read_upload_limited(
                special_image,
                store.limits["maxImageBytes"],
                error_code="image_too_large",
                error_message="PNG image is too large",
                field="special_image",
            )
            if special_image is not None
            else None
        )
        special_sound_data = (
            await _read_upload_limited(
                special_sound,
                store.limits["maxAudioBytes"],
                error_code="special_audio_too_large",
                error_message="MP3 audio is too large",
                field="special_sound",
            )
            if special_sound is not None
            else None
        )
        item = await asyncio.to_thread(
            store.update_tool,
            tool_id,
            base_revision=base_revision,
            name=name,
            change_mode=change_mode,
            change_meanings=change_meanings,
            default_resource=default_resource,
            default_image=default_image_data,
            change_resources=change_resources,
            change_images=list(change_image_data),
            normal_sound_resource=normal_sound_resource,
            normal_sound=normal_sound_data,
            special_probability=special_probability,
            special_image_resource=special_image_resource,
            special_image=special_image_data,
            special_meaning=special_meaning,
            special_sound_resource=special_sound_resource,
            special_sound=special_sound_data,
        )
    except AvatarToolStoreError as exc:
        return _error_response(exc)
    except MaintenanceModeError as exc:
        return JSONResponse(status_code=409, content=maintenance_error_payload(exc))
    finally:
        for upload in uploads:
            await upload.close()
    return {"ok": True, "item": item}


@router.delete("/{tool_id}")
async def delete_avatar_tool(request: Request, tool_id: str):
    rejected = _validate_local_mutation_request(request)
    if rejected is not None:
        return rejected

    store = get_avatar_tool_store(get_config_manager())
    try:
        deleted_tool_id = await asyncio.to_thread(store.delete_tool, tool_id)
    except AvatarToolStoreError as exc:
        return _error_response(exc)
    except MaintenanceModeError as exc:
        return JSONResponse(status_code=409, content=maintenance_error_payload(exc))
    return {"ok": True, "deletedId": deleted_tool_id}
