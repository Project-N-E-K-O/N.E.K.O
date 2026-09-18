# -*- coding: utf-8 -*-
"""Loopback-only API for user-created local avatar tools."""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse

from main_routers.cookies_login_router import verify_local_access
from main_routers.shared_state import get_config_manager
from main_routers.system_router._shared import _validate_local_mutation_request
from utils.avatar_tool_store import (
    AVATAR_TOOL_MAX_RECORD_BYTES,
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


async def _close_uploads(uploads: list[UploadFile]) -> None:
    await asyncio.gather(*(upload.close() for upload in uploads), return_exceptions=True)


def _parse_v3_manifest(raw: str | None, *, expected_tool_id: str | None = None) -> dict:
    if raw is None or len(raw.encode("utf-8")) > AVATAR_TOOL_MAX_RECORD_BYTES:
        raise AvatarToolStoreError("manifest_invalid", "Avatar tool manifest is invalid", field="manifest")
    try:
        manifest = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AvatarToolStoreError("manifest_invalid", "Avatar tool manifest is invalid", field="manifest") from exc
    if (
        not isinstance(manifest, dict)
        or manifest.get("recordVersion") != 3
        or not is_local_avatar_tool_id(manifest.get("id"))
        or (expected_tool_id is not None and manifest.get("id") != expected_tool_id)
    ):
        raise AvatarToolStoreError("manifest_invalid", "Avatar tool manifest is invalid", field="manifest")
    return manifest


async def _read_v3_uploads(store, uploads: list[UploadFile], manifest: dict) -> list[bytes]:
    if len(uploads) > store.limits["maxImages"] + 3:
        raise AvatarToolStoreError(
            "uploads_invalid",
            "Avatar tool upload count is invalid",
            status_code=413,
            field="uploads",
        )
    locations: dict[int, list[tuple[str, int | None, int]]] = {}

    def add_location(source, field: str, index: int | None, maximum: int) -> None:
        if (
            isinstance(source, dict)
            and set(source) == {"kind", "index"}
            and source.get("kind") == "upload"
            and isinstance(source.get("index"), int)
            and not isinstance(source.get("index"), bool)
        ):
            locations.setdefault(source["index"], []).append((field, index, maximum))

    images = manifest.get("images")
    if isinstance(images, list):
        for image_index, image in enumerate(images):
            if isinstance(image, dict):
                add_location(image.get("source"), "image", image_index, store.limits["maxImageBytes"])
    interaction = manifest.get("interaction")
    if isinstance(interaction, dict):
        add_location(interaction.get("normalSound"), "normal_sound", None, store.limits["maxAudioBytes"])
        special = interaction.get("special")
        if isinstance(special, dict):
            add_location(special.get("image"), "special_image", None, store.limits["maxImageBytes"])
            add_location(special.get("sound"), "special_sound", None, store.limits["maxAudioBytes"])

    maximum_upload = max(store.limits["maxImageBytes"], store.limits["maxAudioBytes"])
    reads = []
    for upload_index, upload in enumerate(uploads):
        matching_locations = locations.get(upload_index, [])
        field, item_index, maximum = (
            matching_locations[0]
            if len(matching_locations) == 1
            else ("uploads", upload_index, maximum_upload)
        )
        reads.append(_read_upload_limited(
            upload,
            maximum,
            error_code="upload_too_large",
            error_message="Avatar tool upload is too large",
            field=field,
            index=item_index,
        ))
    return list(await asyncio.gather(*reads))


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
    record_version: str | None = Form(None),
    manifest: str | None = Form(None),
    uploads: list[UploadFile] | None = File(None),
    tool_id: str | None = Form(None),
    name: str | None = Form(None),
    change_mode: str | None = Form(None),
    change_meanings: list[str] | None = Form(None),
    default_image: UploadFile | None = File(None),
    change_images: list[UploadFile] | None = File(None),
    normal_sound: UploadFile | None = File(None),
    special_probability: str | None = Form(None),
    special_image: UploadFile | None = File(None),
    special_meaning: str | None = Form(None),
    special_sound: UploadFile | None = File(None),
):
    change_uploads = change_images or []
    v3_uploads = uploads or []
    all_uploads = [
        *v3_uploads,
        *(upload for upload in [default_image] if upload is not None),
        *change_uploads,
        *(upload for upload in [normal_sound, special_image, special_sound] if upload is not None),
    ]
    rejected = _validate_local_mutation_request(request)
    if rejected is not None:
        await _close_uploads(all_uploads)
        return rejected

    store = get_avatar_tool_store(get_config_manager())
    try:
        form_data = await request.form()
        field_names = set(form_data.keys())
        if record_version == "3":
            if (
                not field_names.issubset({"record_version", "manifest", "uploads"})
                or form_data.getlist("record_version") != ["3"]
                or len(form_data.getlist("manifest")) != 1
            ):
                raise AvatarToolStoreError("request_fields_invalid", "Avatar tool request fields are invalid")
            parsed_manifest = _parse_v3_manifest(manifest)
            uploaded = await _read_v3_uploads(store, v3_uploads, parsed_manifest)
            item = await asyncio.to_thread(
                store.create_tool_v3,
                manifest=parsed_manifest,
                uploads=uploaded,
            )
            return JSONResponse(status_code=201, content={"ok": True, "item": item})
        if record_version is not None or manifest is not None or v3_uploads:
            raise AvatarToolStoreError("record_version_invalid", "Avatar tool record version is invalid")
        if not field_names.issubset({
            "tool_id", "name", "change_mode", "change_meanings", "default_image",
            "change_images", "normal_sound", "special_probability", "special_image",
            "special_meaning", "special_sound",
        }):
            raise AvatarToolStoreError("request_fields_invalid", "Avatar tool request fields are invalid")
        if not is_local_avatar_tool_id(tool_id):
            raise AvatarToolStoreError(
                "invalid_tool_id",
                "Invalid local avatar tool ID",
            )
        if default_image is None:
            raise AvatarToolStoreError("image_required", "PNG image is required", field="default_image")
        if name is None or change_mode is None or change_meanings is None or not change_uploads:
            raise AvatarToolStoreError("request_fields_invalid", "Avatar tool request fields are invalid")
        if len(change_uploads) > store.limits["maxChangeImages"]:
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
                for index, upload in enumerate(change_uploads)
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
        await _close_uploads(all_uploads)

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
    base_revision: str | None = Form(None),
    record_version: str | None = Form(None),
    manifest: str | None = Form(None),
    uploads: list[UploadFile] | None = File(None),
    name: str | None = Form(None),
    change_mode: str | None = Form(None),
    change_meanings: list[str] | None = Form(None),
    change_resources: list[str] | None = Form(None),
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
    v3_uploads = uploads or []
    all_uploads = [
        *v3_uploads,
        *(upload for upload in [default_image] if upload is not None),
        *change_uploads,
        *(upload for upload in [normal_sound, special_image, special_sound] if upload is not None),
    ]
    rejected = _validate_local_mutation_request(request)
    if rejected is not None:
        await _close_uploads(all_uploads)
        return rejected

    store = get_avatar_tool_store(get_config_manager())
    try:
        form_data = await request.form()
        field_names = set(form_data.keys())
        if record_version == "3":
            if (
                not field_names.issubset({"base_revision", "record_version", "manifest", "uploads"})
                or form_data.getlist("record_version") != ["3"]
                or len(form_data.getlist("manifest")) != 1
                or len(form_data.getlist("base_revision")) != 1
            ):
                raise AvatarToolStoreError("request_fields_invalid", "Avatar tool request fields are invalid")
            if base_revision is None:
                raise AvatarToolStoreError("base_revision_required", "Base revision is required", field="base_revision")
            parsed_manifest = _parse_v3_manifest(manifest, expected_tool_id=tool_id)
            uploaded = await _read_v3_uploads(store, v3_uploads, parsed_manifest)
            item = await asyncio.to_thread(
                store.update_tool_v3,
                tool_id,
                base_revision=base_revision,
                manifest=parsed_manifest,
                uploads=uploaded,
            )
            return {"ok": True, "item": item}
        if record_version is not None or manifest is not None or v3_uploads:
            raise AvatarToolStoreError("record_version_invalid", "Avatar tool record version is invalid")
        if not field_names.issubset({
            "base_revision", "name", "change_mode", "change_meanings", "change_resources",
            "default_resource", "default_image", "change_images", "normal_sound_resource",
            "normal_sound", "special_probability", "special_image_resource", "special_image",
            "special_meaning", "special_sound_resource", "special_sound",
        }):
            raise AvatarToolStoreError("request_fields_invalid", "Avatar tool request fields are invalid")
        if (
            base_revision is None
            or name is None
            or change_mode is None
            or change_meanings is None
            or change_resources is None
        ):
            raise AvatarToolStoreError("request_fields_invalid", "Avatar tool request fields are invalid")
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
        await _close_uploads(all_uploads)
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
