# -*- coding: utf-8 -*-
"""Inspect and persist emote template packages.

The import endpoint validates the supplied Spine ZIP in memory and returns a
renderer-neutral manifest. Library endpoints save only the selected Spine
resources under a content-derived identifier in the user data directory. Editor
session images are stored separately.
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import os
import re
import zipfile
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field

from main_logic.emote_template_adapter import (
    MAX_ARCHIVE_BYTES,
    EmoteTemplatePackageError,
    inspect_spine_zip,
    spine_resource_archive,
)
from main_logic.emote_template_store import (
    MAX_TEXTURE_BYTES,
    MAX_TEXTURE_FILES,
    MAX_TEXTURE_TOTAL_BYTES,
    EmoteTemplateArchiveStore,
    EmoteTemplateStoreError,
    normalize_texture_path,
)
from main_logic.emote_stage_images import MAX_STAGE_GIF_BYTES, decode_stage_gif
from main_logic.emote_wardrobe import (
    MAX_IMAGE_BYTES,
    MAX_SAVED_SPRITESHEET_BYTES,
    MAX_SPRITESHEET_REGIONS,
    SPRITESHEET_CELLS,
    EmoteWardrobe,
    WardrobeError,
    parse_annotations,
    prepare_spritesheet_crops,
    prepare_spritesheet_result,
    read_library_spritesheet,
    spritesheet_crop_aspect_limit,
)
from utils.file_utils import atomic_write_bytes
from utils.image_generation import MAX_IMAGE_PROMPT_LENGTH, ImageGenerationError, ImageRequest, generate_image
from utils.image_generation.config import PROVIDERS as IMAGE_PROVIDERS
from utils.image_generation.config import supports_image_edit
from utils.image_generation.transport import read_generated_image
from utils.llm_client import HumanMessage, SystemMessage, create_chat_llm_async
from utils.token_tracker import llm_call_context
from .local_access import is_direct_loopback_request
from .shared_state import get_config_manager


router = APIRouter(prefix="/api/emote-maker", tags=["emote-maker"])
# Includes multipart headers/paths while bounding the parser's spooled uploads.
MAX_MULTIPART_BODY_BYTES = MAX_TEXTURE_TOTAL_BYTES + 8 * 1024 * 1024
_CHUNK_SIZE = 1024 * 1024
_EXPORT_FILE_LIMIT = 32 * 1024 * 1024
_EXPORT_HEADER = "x-neko-emote-export"
_LIBRARY_HEADER = "x-neko-emote-library"
_INVALID_EXPORT_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
# Retain the storage key for existing wardrobe assets and editor sessions.
_BUILTIN_KEY = "builtin:yui-0.2"
_BUILTIN_VERSION = "0.5"
_BUILTIN_BASENAME = f"yui-{_BUILTIN_VERSION}"
_BUILTIN_FILENAME = f"{_BUILTIN_BASENAME}.zip"
_WARDROBE_HEADER = "x-neko-emote-wardrobe"
_MAX_WARDROBE_SUPPLEMENTAL_PROMPT = 240
_WARDROBE_SPRITES = "WardrobeSprites/"
_WARDROBE_SHEET_ID = re.compile(r"[0-9a-f]{64}(?:-check)?")
_BUILTIN_USER_TEXTURE_PREFIXES = ("StageImages/", _WARDROBE_SPRITES)
_wardrobe_generation_lock = asyncio.Lock()
_wardrobe_library_lock = asyncio.Lock()
_builtin_storage_lock = asyncio.Lock()
_library_storage_lock = asyncio.Lock()


async def _storage_transaction(lock, operation, *args, **kwargs):
    """Keep storage ownership until a worker finishes, even if its caller cancels."""
    async with lock:
        work = asyncio.create_task(asyncio.to_thread(operation, *args, **kwargs))
        cancelled = False
        while True:
            try:
                result = await asyncio.shield(work)
            except asyncio.CancelledError:
                if work.cancelled():
                    raise
                cancelled = True
            except Exception:
                if cancelled:
                    raise asyncio.CancelledError from None
                raise
            else:
                if cancelled:
                    raise asyncio.CancelledError
                return result


def _builtin_template_directory() -> Path:
    configured = (os.environ.get("NEKO_EMOTE_TEMPLATE_DIR") or "").strip()
    if configured:
        root = Path(configured).expanduser()
    else:
        root = Path(__file__).resolve().parents[1] / "N.E.K.O.-PC" / "assets" / "emote-templates"
    return root / _BUILTIN_BASENAME


@lru_cache(maxsize=1)
def _builtin_template(directory: Path):
    archive = (directory / _BUILTIN_FILENAME).read_bytes()
    profile = (directory / "neko-template.json").read_bytes()
    profile_data = json.loads(profile)
    package = inspect_spine_zip(archive, filename=_BUILTIN_FILENAME, part_profile=profile)
    # The built-in's part catalog also identifies slots that only need color controls.
    slot_groups = package["catalogs"]["slotGroups"]
    known_slots = {part["id"] for part in slot_groups}
    for part in profile_data["textureParts"]:
        slot = part["slot"]
        if slot in known_slots:
            continue
        known_slots.add(slot)
        slot_groups.append({"id": slot, "displayName": part["labels"].get("en", slot), "labels": part["labels"],
                            "kind": "tint", "attachments": [], "hideSlots": [], "attachmentLabels": {}})
        package["character"]["slots"].append({"name": slot, "attachmentMode": "auto"})
    package["counts"]["slotGroups"] = len(slot_groups)
    package["id"] = _BUILTIN_KEY
    package["source"].update(
        packageId=_BUILTIN_KEY,
        builtIn=True,
        sceneAliases=profile_data["sceneAliases"],
        wardrobeBindings=profile_data.get("wardrobeBindings", {}),
    )
    package["model"].update(id=_BUILTIN_KEY, displayName=f"YUI {_BUILTIN_VERSION}")
    return package, archive, profile_data


@lru_cache(maxsize=1)
def _builtin_texture_overrides(directory: Path):
    package, archive, profile = _builtin_template(directory)
    return EmoteWardrobe(archive, package, profile["textureParts"])


@router.get("/builtins")
async def list_builtin_templates():
    directory = _builtin_template_directory()
    if not directory.is_dir():
        return {"success": True, "packages": []}
    try:
        package, _, _ = _builtin_template(directory)
    except (OSError, EmoteTemplatePackageError):
        return _store_error(EmoteTemplateStoreError("内置模板无法加载"), status=500)
    return {"success": True, "packages": [package]}


async def _read_upload(upload: UploadFile) -> bytes:
    if not upload or not upload.filename:
        raise EmoteTemplatePackageError("没有上传模板包")
    if not upload.filename.lower().endswith(".zip"):
        raise EmoteTemplatePackageError("请上传支持的模板文件")

    buffer = io.BytesIO()
    total = 0
    while True:
        chunk = await upload.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > MAX_ARCHIVE_BYTES:
            raise EmoteTemplatePackageError(
                f"模板包过大，当前导入上限为 {MAX_ARCHIVE_BYTES // (1024 * 1024)} MB"
            )
        buffer.write(chunk)
    return buffer.getvalue()


@router.post("/import")
async def import_spine_template(
    request: Request, file: UploadFile = File(...), model_path: str = Form(""), atlas_path: str = Form(""),
):
    """Inspect a complete Spine ZIP using only its own resources."""

    try:
        if request.headers.get(_LIBRARY_HEADER) != "1":
            return _store_error(ValueError("forbidden"), status=403)
        content = await _read_upload(file)
        package = await asyncio.to_thread(
            inspect_spine_zip, content, filename=file.filename or "", model_path=model_path, atlas_path=atlas_path,
        )
    except EmoteTemplatePackageError as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": str(exc), "code": exc.code, "role": exc.role, "candidates": exc.candidates},
        )
    finally:
        await file.close()

    return JSONResponse(content={"success": True, "package": package})


# ── 导入即拷副本 ────────────────────────────────────────────────────────────
# 键使用 query 参数，同时支持新的内容标识和含有斜杠的旧库键。
def _template_library_directory() -> Path:
    config_mgr = get_config_manager()
    if not config_mgr.ensure_emote_templates_directory():
        raise EmoteTemplateStoreError("本地模板目录不可用")
    return Path(config_mgr.emote_templates_dir)


def _archive_store() -> EmoteTemplateArchiveStore:
    return EmoteTemplateArchiveStore(_template_library_directory())


def _store_error(exc: Exception, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, content={"success": False, "error": str(exc)})


async def _read_upload_bytes(upload: UploadFile, limit: int, label: str) -> bytes:
    buffer = io.BytesIO()
    total = 0
    while True:
        chunk = await upload.read(min(_CHUNK_SIZE, limit - total + 1))
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise EmoteTemplateStoreError(f"{label}超过上限")
        buffer.write(chunk)
    return buffer.getvalue()


def _wardrobe_error(code: str, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, content={"success": False, "code": code})


def _wardrobe_provider_error(exc: ImageGenerationError) -> JSONResponse:
    if exc.code != "provider_rate_limited":
        return _wardrobe_error(exc.code)
    headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else None
    return JSONResponse(
        status_code=429, content={"success": False, "code": exc.code}, headers=headers,
    )


async def _wardrobe_image_config(config_manager) -> tuple[str, str, str]:
    try:
        config = await config_manager.aget_model_api_config("image")
    except ValueError:
        return "invalid_configuration", "", ""
    if not config.get("enabled"):
        return "not_configured", "", ""
    protocol, model = config["protocol"], config["model"]
    if not config.get("api_key"):
        return "missing_api_key", model, protocol
    return "", model, protocol


@router.get("/builtin/wardrobe")
async def builtin_wardrobe_status():
    code, model, protocol = await _wardrobe_image_config(get_config_manager())
    edit_code = code or ("" if supports_image_edit(protocol, model) else "image_edit_unsupported")
    return {
        "success": True,
        "spritesheetConfigurationCode": edit_code,
        "spritesheetRegionLimit": MAX_SPRITESHEET_REGIONS,
        "spritesheetCropAspectLimit": spritesheet_crop_aspect_limit(protocol, model),
        "model": model,
    }


async def _wardrobe_vision_config(config_manager) -> dict:
    try:
        config = await config_manager.aget_model_api_config("vision")
    except ValueError:
        raise WardrobeError("wardrobe_vision_unavailable") from None
    configured = config.get("model") and (config.get("api_key") or config.get("is_custom"))
    if config.get("model") and config["model"] != "free-vision-model":
        if configured:
            return config
        raise WardrobeError("wardrobe_vision_unavailable")
    try:
        image = await config_manager.aget_model_api_config("image")
    except ValueError:
        raise WardrobeError("wardrobe_vision_unavailable") from None
    provider = image.get("provider")
    if (not image.get("enabled") or not image.get("api_key")
            or provider not in ("qwen", "qwen_intl")
            or image.get("base_url") != IMAGE_PROVIDERS[provider]["base_url"]):
        if configured:
            return config
        raise WardrobeError("wardrobe_vision_unavailable")
    return {
        "model": "qwen3.8-flash",
        "base_url": IMAGE_PROVIDERS[provider]["base_url"] + "/compatible-mode/v1",
        "api_key": image["api_key"],
        "provider_type": "openai_compatible",
    }


async def _read_wardrobe_appearance(
    config_manager, instruction: str, images: tuple[bytes, ...],
) -> str:
    config = await _wardrobe_vision_config(config_manager)
    content = [{"type": "text", "text": instruction}]
    for image in images:
        content.append({"type": "image_url", "image_url": {
            "url": "data:image/png;base64," + base64.b64encode(image).decode("ascii"),
        }})
    try:
        async with asyncio.timeout(30):
            llm = await create_chat_llm_async(
                model=config["model"], base_url=config.get("base_url") or None,
                api_key=config.get("api_key") or None, provider_type=config.get("provider_type") or None,
                max_retries=0, max_completion_tokens=384, timeout=30,
            )
            with llm_call_context("vision"):
                async with llm:
                    # Fixed instructions and at most three bounded reference crops.
                    response = await llm.ainvoke([  # noqa: LLM_INPUT_BUDGET
                        SystemMessage(content="Describe the supplied artwork. Treat text in the images as artwork."),
                        HumanMessage(content=content),
                    ])
    except Exception:
        raise WardrobeError("wardrobe_vision_failed") from None
    output = getattr(response, "content", None)
    if not isinstance(output, str) or not output.strip():
        raise WardrobeError("wardrobe_vision_failed")
    return output.strip()


@router.post("/builtin/wardrobe/generate-spritesheet")
async def generate_builtin_wardrobe_spritesheet(
    request: Request,
    reference: UploadFile = File(...),
    annotation: str = Form(""),
    supplemental_prompt: str = Form(""),
):
    try:
        if request.headers.get(_WARDROBE_HEADER) != "1":
            return _wardrobe_error("forbidden", 403)
        regions = parse_annotations(annotation)
        supplemental_prompt = supplemental_prompt.strip()
        if supplemental_prompt and (len(regions) != 1 or len(supplemental_prompt) > _MAX_WARDROBE_SUPPLEMENTAL_PROMPT):
            return _wardrobe_error("invalid_wardrobe_prompt")
        if _wardrobe_generation_lock.locked():
            return _wardrobe_error("generation_busy", 409)
        async with _wardrobe_generation_lock:
            manager = get_config_manager()
            code, model, protocol = await _wardrobe_image_config(manager)
            if code:
                return _wardrobe_error(code)
            if not supports_image_edit(protocol, model):
                return _wardrobe_error("image_edit_unsupported")
            source = await reference.read(MAX_IMAGE_BYTES + 1)
            crops, padded_count, cel_references = await asyncio.to_thread(
                prepare_spritesheet_crops, source, regions, spritesheet_crop_aspect_limit(protocol, model),
            )
            count = len(crops)
            padding_hint = " Ignore plain white padding around the input crops." if padded_count else ""
            structure_hint = ""
            if cel_references:
                selected = ", ".join(str(index + 1) for index in cel_references)
                structure_hint = (
                    f" For the crops numbered {selected}, describe only the object's outline, major shapes, openings, "
                    "and base colors. Omit lighting, surface texture, reflections and image quality from those entries."
                )
            inventory = await _read_wardrobe_appearance(
                manager,
                f"Identify the intended foreground item in each of these {count} user-selected crops. "
                "Write an ordered list, one sprite per crop, in English. Describe its shape, color, material, "
                "and distinguishing details in under 35 words per item. Ignore incidental fragments around "
                "the main item. If the crop is blurry, describe only the discernible silhouette, main colors and large details. "
                "Do not guess small details or introduce extra props or variants."
                + structure_hint + padding_hint,
                crops,
            )
            layout = {
                1: "One centered cell.",
                2: "Two cells side by side: left and right.",
                3: "A 2-by-2 grid: item 1 top left, item 2 top right, item 3 bottom left. Leave the bottom-right cell transparent.",
            }[count]
            cells = " ".join(
                f"Item {index}: x {left}–{right}%, y {top}–{bottom}%."
                for index, (left, top, right, bottom) in enumerate(SPRITESHEET_CELLS[count], 1)
            )
            rendering = "Preserve the reference design, proportions, orientation, colors, line weight and shading. "
            cel_note = ""
            if cel_references:
                positions = {1: ("center",), 2: ("left", "right"), 3: ("top-left", "top-right", "bottom-left")}[count]
                targets = ", ".join(positions[index] for index in cel_references)
                rendering = "Preserve each reference's silhouette, proportions, angle, main hues and readable markings. "
                cel_note = (
                    f"Cel animation redraw for these positions only: {targets}. "
                    "Use the reference only for geometry and main hues. Draw clean ink contours and broad flat color shapes. "
                    "Paint every surface as matte: one solid base color and one solid shadow color per material. "
                    "Draw the shadow as one connected shape along one side, bounded by a simple hard edge. "
                    "Leave broad uninterrupted areas of uniform color. Use no gradients, metallic reflections, gloss, "
                    "highlights, or patches tracing variations in the original shading. "
                    "Read the references for silhouettes and material colors. Invent clean shadow boundaries. "
                    "Keep the source line weight and shading for the other objects. "
                )
            prompt = (
                f"Redraw {count} isolated 2D sprites on one square RGBA sprite sheet, one per input crop. "
                f"{cel_note}"
                "Use the crops as visual references and redraw the foreground objects with clean edges. "
                "Never paste or enlarge input crops. Remove all surrounding fabric, skin and scenery. "
                "Leave everything outside the objects and inside any openings transparent. "
                "For a blurry object, keep its readable silhouette and main colors; simplify unresolved details. "
                f"{layout} Drawable areas from the top-left corner: {cells} "
                "Center each complete sprite in its cell. Scale it down proportionally until its entire outline fits inside the cell. "
                "Keep the gutters and outer margins fully transparent. No overlap, clipping, grid lines, labels, "
                "shadows on the canvas, checkerboard, or character fragments. "
                f"{rendering}"
                "Reconstruct hidden edges only when their continuation is clear."
                f"{padding_hint}"
            )
            user_edits = ""
            if supplemental_prompt:
                user_edits = (
                    "\nUser edits for the selected object override conflicting appearance defaults. "
                    f"Keep the layout and transparency:\n{supplemental_prompt}"
                )
            inventory_heading = "\nVisual inventory in input order:\n"
            inventory_budget = min(1000, MAX_IMAGE_PROMPT_LENGTH - len(prompt) - len(user_edits) - len(inventory_heading))
            if inventory_budget > 0:
                prompt += inventory_heading + inventory[:inventory_budget]
            prompt += user_edits
            reference_images = tuple(cel_references.get(index, crop) for index, crop in enumerate(crops))
            result = await generate_image(
                ImageRequest(prompt, size="1536x1536" if protocol == "dashscope" else "1024x1024",
                             reference_images=reference_images),
                config_manager=manager,
            )
            raw = await read_generated_image(result.image, limit=MAX_IMAGE_BYTES)
            png, separated = await asyncio.to_thread(prepare_spritesheet_result, raw, count)
        return Response(png, media_type="image/png", headers={
            "Cache-Control": "no-store",
            "X-Wardrobe-Spritesheet-Layout": "separated" if separated else "check",
            "X-Wardrobe-Spritesheet-Padded": str(padded_count),
        })
    except ImageGenerationError as exc:
        return _wardrobe_provider_error(exc)
    except WardrobeError as exc:
        return _wardrobe_error(str(exc))
    except (KeyError, OSError):
        return _wardrobe_error("wardrobe_unavailable", 500)
    finally:
        await reference.close()


def _wardrobe_sheet_directory() -> Path:
    return _template_library_directory() / ".wardrobe-sheets"


@router.get("/builtin/wardrobe/spritesheets")
async def list_wardrobe_spritesheets():
    try:
        async with _wardrobe_library_lock:
            entries = [
                {"id": path.stem, "savedAt": path.stat().st_mtime_ns // 1_000_000}
                for path in _wardrobe_sheet_directory().glob("*.png")
                if _WARDROBE_SHEET_ID.fullmatch(path.stem) and path.is_file()
            ]
        entries.sort(key=lambda item: (item["savedAt"], item["id"]), reverse=True)
        return JSONResponse({"entries": entries}, headers={"Cache-Control": "no-store"})
    except (OSError, EmoteTemplateStoreError):
        return _wardrobe_error("wardrobe_library_failed", 500)


@router.post("/builtin/wardrobe/spritesheets")
async def save_wardrobe_spritesheet(
    request: Request, sheet: UploadFile = File(...), layout_warning: bool = Form(False),
):
    try:
        if request.headers.get(_WARDROBE_HEADER) != "1":
            return _wardrobe_error("forbidden", 403)
        raw = await sheet.read(MAX_SAVED_SPRITESHEET_BYTES + 1)
        png = await asyncio.to_thread(read_library_spritesheet, raw)
        sheet_id = sha256(png).hexdigest() + ("-check" if layout_warning else "")
        async with _wardrobe_library_lock:
            target = _wardrobe_sheet_directory() / f"{sheet_id}.png"
            if not target.is_file():
                await asyncio.to_thread(atomic_write_bytes, target, png)
        return {"id": sheet_id}
    except WardrobeError as exc:
        return _wardrobe_error(str(exc))
    except (OSError, EmoteTemplateStoreError):
        return _wardrobe_error("wardrobe_library_failed", 500)
    finally:
        await sheet.close()


@router.get("/builtin/wardrobe/spritesheets/{sheet_id}")
async def open_wardrobe_spritesheet(sheet_id: str, preview: bool = False):
    if not _WARDROBE_SHEET_ID.fullmatch(sheet_id):
        return _wardrobe_error("wardrobe_library_missing", 404)
    try:
        raw = await asyncio.to_thread((_wardrobe_sheet_directory() / f"{sheet_id}.png").read_bytes)
        png = await asyncio.to_thread(read_library_spritesheet, raw, preview=True) if preview else raw
        return Response(png, media_type="image/png", headers={
            "Cache-Control": "private, max-age=3600" if preview else "no-store",
            "X-Wardrobe-Spritesheet-Layout": "check" if sheet_id.endswith("-check") else "separated",
        })
    except FileNotFoundError:
        return _wardrobe_error("wardrobe_library_missing", 404)
    except (OSError, EmoteTemplateStoreError, WardrobeError):
        return _wardrobe_error("wardrobe_library_failed", 500)


@router.delete("/builtin/wardrobe/spritesheets/{sheet_id}")
async def delete_wardrobe_spritesheet(sheet_id: str, request: Request):
    if request.headers.get(_WARDROBE_HEADER) != "1":
        return _wardrobe_error("forbidden", 403)
    if not _WARDROBE_SHEET_ID.fullmatch(sheet_id):
        return _wardrobe_error("wardrobe_library_missing", 404)
    try:
        async with _wardrobe_library_lock:
            (_wardrobe_sheet_directory() / f"{sheet_id}.png").unlink(missing_ok=True)
        return Response(status_code=204)
    except (OSError, EmoteTemplateStoreError):
        return _wardrobe_error("wardrobe_library_failed", 500)


def _builtin_archive_for_editor() -> bytes:
    directory = _builtin_template_directory()
    _, original, _ = _builtin_template(directory)
    try:
        store = _archive_store()
        paths = set(store.texture_paths(_BUILTIN_KEY))
    except EmoteTemplateStoreError:
        return original
    if not any(path.startswith("GeneratedParts/") for path in paths):
        return original
    wardrobe = _builtin_texture_overrides(directory)
    parts = {}
    for part in wardrobe.parts.values():
        if part.storage_path not in paths:
            continue
        try:
            with store.texture_path(_BUILTIN_KEY, part.storage_path).open("rb") as stream:
                parts[part.id] = part.validate_result(stream.read(MAX_IMAGE_BYTES + 1))
        except (EmoteTemplateStoreError, WardrobeError, OSError):
            continue
    return wardrobe.build_archive(parts) if parts else original


@router.get("/library")
async def list_template_library():
    try:
        entries = await _storage_transaction(_library_storage_lock, lambda: _archive_store().list())
    except EmoteTemplateStoreError as exc:
        return _store_error(exc, status=500)
    return {
        "success": True,
        "entries": entries,
        "totalBytes": sum(entry["bytes"] for entry in entries),
    }


def _save_template_copy(store, key, archive, filename, model_path, atlas_path, has_textures):
    """Validate, repack and persist an archive away from the server event loop."""
    package = inspect_spine_zip(archive, filename=filename, model_path=model_path, atlas_path=atlas_path)
    if key != package["id"] or has_textures:
        raise EmoteTemplateStoreError("资源包标识无效；模型贴图必须包含在 ZIP 内")
    archive = spine_resource_archive(archive, package)
    # Reimporting an immutable content ID preserves user-authored session images.
    return store.save(key, archive, filename=filename, preserve_textures=True)


@router.post("/library/archive")
async def save_template_library_archive(
    request: Request,
    key: str = Query(...),
    file: UploadFile = File(...),
    textures: list[UploadFile] | None = File(None),
    texture_paths: list[str] | None = Form(None),
    model_path: str = Form(""),
    atlas_path: str = Form(""),
):
    try:
        if request.headers.get(_LIBRARY_HEADER) != "1":
            return _store_error(ValueError("forbidden"), status=403)
        store = await asyncio.to_thread(_archive_store)
        archive = await _read_upload(file)
        entry = await _storage_transaction(
            _library_storage_lock, _save_template_copy, store, key, archive,
            file.filename or "", model_path, atlas_path, bool(textures),
        )
    except (EmoteTemplateStoreError, EmoteTemplatePackageError) as exc:
        return _store_error(exc)
    finally:
        await file.close()
        for texture in textures or []:
            await texture.close()
    return JSONResponse(content={"success": True, "entry": entry})


def _read_library_archive(key):
    store = _archive_store()
    path = store.archive_path(key)
    with path.open('rb') as stream:
        return stream.read(MAX_ARCHIVE_BYTES + 1), store.archive_name(key)


@router.get("/library/archive")
async def open_template_library_archive(key: str = Query(...)):
    try:
        if key == _BUILTIN_KEY:
            content = await _storage_transaction(_builtin_storage_lock, _builtin_archive_for_editor)
            name = _BUILTIN_FILENAME
        else:
            content, name = await _storage_transaction(_library_storage_lock, _read_library_archive, key)
        if len(content) > MAX_ARCHIVE_BYTES:
            raise EmoteTemplateStoreError("已保存的模板包超过大小上限")
    except (EmoteTemplateStoreError, EmoteTemplatePackageError, WardrobeError, OSError) as exc:
        return _store_error(exc, status=404)
    # 通过 URL 编码头返回应用生成的文件名；兼顾旧记录的非 ASCII 名称。
    return Response(
        content, media_type="application/zip",
        headers={"X-Archive-Filename": quote(name), "Cache-Control": "no-store"},
    )


def _library_texture_paths(key):
    store = _archive_store()
    if key == _BUILTIN_KEY:
        return [path for path in store.texture_paths(key) if path.startswith(_BUILTIN_USER_TEXTURE_PREFIXES)]
    store.archive_path(key)
    return store.texture_paths(key)


@router.get("/library/textures")
async def list_template_library_textures(key: str = Query(...)):
    try:
        lock = _builtin_storage_lock if key == _BUILTIN_KEY else _library_storage_lock
        paths = await _storage_transaction(lock, _library_texture_paths, key)
    except EmoteTemplateStoreError as exc:
        return _store_error(exc, status=404)
    return JSONResponse(content={"success": True, "paths": paths})


def _merge_library_textures(key, blobs):
    store = _archive_store()
    if key != _BUILTIN_KEY or store.has(key):
        return store.save_textures(key, blobs)
    _, archive, _ = _builtin_template(_builtin_template_directory())
    if blobs:
        store.save(key, archive, blobs, filename=_BUILTIN_FILENAME, preserve_textures=True)
    return {"key": key, "textures": store.texture_paths(key)}


@router.post("/library/textures")
async def merge_template_library_textures(
    request: Request,
    key: str = Query(...),
    textures: list[UploadFile] | None = File(None),
    texture_paths: list[str] | None = Form(None),
):
    try:
        if request.headers.get(_LIBRARY_HEADER) != "1":
            return _store_error(ValueError("forbidden"), status=403)
        paths = [str(path or "") for path in (texture_paths or [])]
        if len(textures or []) > MAX_TEXTURE_FILES or len(paths) > MAX_TEXTURE_FILES:
            raise EmoteTemplateStoreError(f"贴图数量超过上限（最多 {MAX_TEXTURE_FILES} 张）")
        blobs: list[tuple[str, bytes]] = []
        total_bytes = 0
        for index, texture in enumerate(textures or []):
            relative = paths[index] if index < len(paths) else (texture.filename or "")
            prefixes = _BUILTIN_USER_TEXTURE_PREFIXES if key == _BUILTIN_KEY else ("StageImages/",)
            if not normalize_texture_path(relative).startswith(prefixes):
                raise EmoteTemplateStoreError("Spine 模型贴图必须随 ZIP 提供；这里只接受会话图片")
            blob = await _read_upload_bytes(
                texture, min(MAX_TEXTURE_BYTES, MAX_TEXTURE_TOTAL_BYTES - total_bytes), "附带贴图",
            )
            total_bytes += len(blob)
            blobs.append((relative, blob))
        lock = _builtin_storage_lock if key == _BUILTIN_KEY else _library_storage_lock
        result = await _storage_transaction(lock, _merge_library_textures, key, blobs)
    except (EmoteTemplateStoreError, EmoteTemplatePackageError, OSError) as exc:
        return _store_error(exc, status=404 if "还没有本地副本" in str(exc) else 400)
    finally:
        for texture in textures or []:
            await texture.close()
    return JSONResponse(content={"success": True, **result})


def _remove_library_textures(key, paths):
    store = _archive_store()
    for path in paths:
        store.remove_texture(key, path)


@router.post("/library/textures/remove")
async def remove_template_library_texture(
    request: Request,
    key: str = Query(...),
    texture_paths: list[str] | None = Form(None),
):
    try:
        if request.headers.get(_LIBRARY_HEADER) != "1":
            return _store_error(ValueError("forbidden"), status=403)
        if key == _BUILTIN_KEY and any(
            not normalize_texture_path(path).startswith(_BUILTIN_USER_TEXTURE_PREFIXES) for path in (texture_paths or [])
        ):
            raise EmoteTemplateStoreError("这里只接受会话图片")
        lock = _builtin_storage_lock if key == _BUILTIN_KEY else _library_storage_lock
        await _storage_transaction(lock, _remove_library_textures, key, texture_paths or [])
    except EmoteTemplateStoreError as exc:
        return _store_error(exc, status=404)
    return JSONResponse(content={"success": True})


@router.get("/library/texture")
async def open_template_library_texture(key: str = Query(...), path: str = Query(...)):
    def read_texture():
        target = _archive_store().texture_path(key, path)
        with target.open('rb') as stream:
            content = stream.read(MAX_TEXTURE_BYTES + 1)
        if len(content) > MAX_TEXTURE_BYTES:
            raise EmoteTemplateStoreError("附带贴图超过上限")
        return content, target.name, target.suffix

    try:
        lock = _builtin_storage_lock if key == _BUILTIN_KEY else _library_storage_lock
        content, name, suffix = await _storage_transaction(lock, read_texture)
    except (EmoteTemplateStoreError, OSError) as exc:
        return _store_error(exc, status=404)
    media_type = "image/gif" if suffix.lower() == ".gif" else "image/png"
    return Response(content, media_type=media_type, headers={
        "Cache-Control": "no-store",
        "Content-Disposition": f"attachment; filename*=utf-8''{quote(name)}",
    })


@router.post("/library/stage-image-frames")
async def stage_image_frames(request: Request, file: UploadFile = File(...), edge: int = Query(ge=1, le=512)):
    try:
        if request.headers.get("x-neko-stage-image") != "1":
            return JSONResponse(status_code=403, content={"code": "forbidden"})
        raw = await file.read(MAX_STAGE_GIF_BYTES + 1)
        png, cell, columns, delays = await asyncio.to_thread(decode_stage_gif, raw, edge)
        return Response(png, media_type="image/png", headers={
            "Cache-Control": "no-store",
            "X-Stage-Image-Frames": json.dumps([cell, columns, delays], separators=(",", ":")),
        })
    except ValueError:
        return JSONResponse(status_code=400, content={"code": "invalid_stage_gif"})
    finally:
        await file.close()


@router.delete("/library")
async def delete_template_library_archive(key: str = Query(...)):
    if key == _BUILTIN_KEY:
        return _store_error(EmoteTemplateStoreError("内置模板不可删除"), status=403)
    try:
        result = await _storage_transaction(_library_storage_lock, lambda: _archive_store().delete(key))
    except EmoteTemplateStoreError as exc:
        return _store_error(exc, status=500)
    return JSONResponse(content={"success": True, **result})


class ExportWriteError(Exception):
    pass


class ExportFolderBody(BaseModel):
    directory: str = Field(min_length=1, max_length=4096)
    folder: str = Field(default="group", max_length=120)


class ExportZipBody(BaseModel):
    directory: str = Field(min_length=1, max_length=4096)
    folder: str = Field(min_length=1, max_length=120)
    names: list[str] = Field(default_factory=list, max_length=400)
    zip_name: str = Field(default="", max_length=120)


def _reject_unscoped_export(request: Request) -> JSONResponse | None:
    # The header blocks cross-site forms; authenticated remote sessions still
    # cannot grant write access to arbitrary paths on the backend machine.
    if (request.headers.get(_EXPORT_HEADER) != "1"
            or request.scope.get("neko.instance_identity")
            or not is_direct_loopback_request(request)):
        return JSONResponse(status_code=403, content={"success": False, "error": "拒绝写入"})
    return None


def _export_error(exc: Exception, status: int = 400) -> JSONResponse:
    return JSONResponse(status_code=status, content={"success": False, "error": str(exc)})


def _safe_export_segment(value: str, fallback: str = "group") -> str:
    cleaned = _INVALID_EXPORT_NAME.sub("_", str(value or ""))
    cleaned = cleaned.strip().strip(".").strip()[:80]
    if not cleaned or cleaned in {".", ".."}:
        return fallback
    return cleaned


def _safe_export_file_name(value: str) -> str:
    text = str(value or "")
    if not text or text != Path(text).name or text in {".", ".."}:
        raise ExportWriteError("文件名不合法")
    if any(ord(char) < 32 for char in text):
        raise ExportWriteError("文件名不合法")
    return text


def _existing_export_directory(raw: str) -> Path:
    text = str(raw or "").strip()
    if not text:
        raise ExportWriteError("没有导出目录")
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise ExportWriteError("导出目录必须是绝对路径")
    try:
        resolved = Path(os.path.realpath(path))
    except OSError as exc:
        raise ExportWriteError("导出目录不可用") from exc
    if not resolved.is_dir():
        raise ExportWriteError("导出目录不存在")
    return resolved


def _direct_child(parent: Path, name: str) -> Path:
    child = parent / name
    try:
        child_real = Path(os.path.realpath(child))
        parent_real = Path(os.path.realpath(parent))
    except OSError as exc:
        raise ExportWriteError("导出路径不可用") from exc
    if os.path.normcase(str(child_real.parent)) != os.path.normcase(str(parent_real)):
        raise ExportWriteError("导出路径超出了所选目录")
    return child_real


def _create_export_folder(parent: Path, folder: str) -> str:
    name = _safe_export_segment(folder, "group")
    candidate = name
    for index in range(2, 500):
        try:
            (parent / candidate).mkdir(parents=False, exist_ok=False)
            return candidate
        except FileExistsError:
            suffix = f" {index}"
            candidate = name[:80 - len(suffix)] + suffix
    raise ExportWriteError("没法在这个目录里建文件夹")


def _unique_export_file(parent: Path, name: str) -> Path:
    safe = _safe_export_file_name(name)
    candidate = parent / safe
    if not candidate.exists():
        return candidate
    stem = Path(safe).stem
    suffix = Path(safe).suffix
    for index in range(2, 500):
        nxt = parent / f"{stem} {index}{suffix}"
        if not nxt.exists():
            return nxt
    raise ExportWriteError("没法写入压缩包")


@router.post("/export/folder")
async def create_export_folder(body: ExportFolderBody, request: Request):
    """Create a unique child folder in the selected directory for a scoped editor request."""

    rejected = _reject_unscoped_export(request)
    if rejected is not None:
        return rejected
    try:
        directory, folder = await asyncio.to_thread(_prepare_export_folder, body)
    except ExportWriteError as exc:
        return _export_error(exc)
    return JSONResponse(content={
        "success": True,
        "directory": str(directory),
        "folder": folder,
    })


def _prepare_export_folder(body):
    directory = _existing_export_directory(body.directory)
    return directory, _create_export_folder(directory, body.folder)


def _write_export_file(directory, folder, name, payload):
    parent = _existing_export_directory(directory)
    child = _direct_child(parent, _safe_export_segment(folder, 'group'))
    if not child.is_dir():
        raise ExportWriteError('导出文件夹不存在')
    target = _direct_child(child, _safe_export_file_name(name))
    target.write_bytes(payload)


@router.post("/export/file")
async def write_export_file(
    request: Request,
    directory: str = Form(...),
    folder: str = Form(...),
    name: str = Form(...),
    file: UploadFile = File(...),
):
    try:
        rejected = _reject_unscoped_export(request)
        if rejected is not None:
            return rejected
        payload = await _read_limited_upload(file, _EXPORT_FILE_LIMIT)
        await asyncio.to_thread(_write_export_file, directory, folder, name, payload)
    except ExportWriteError as exc:
        return _export_error(exc)
    except OSError:
        return _export_error(ExportWriteError("没法写入这个文件"))
    finally:
        await file.close()
    return JSONResponse(content={"success": True})


@router.post("/export/zip")
async def zip_export_folder(body: ExportZipBody, request: Request):
    rejected = _reject_unscoped_export(request)
    if rejected is not None:
        return rejected
    try:
        name = await asyncio.to_thread(_zip_export_folder, body)
    except ExportWriteError as exc:
        return _export_error(exc)
    return JSONResponse(content={"success": True, "zip": name})


def _zip_export_folder(body):
    archive_path = None
    try:
        parent = _existing_export_directory(body.directory)
        child = _direct_child(parent, _safe_export_segment(body.folder, "group"))
        if not child.is_dir():
            raise ExportWriteError("导出文件夹不存在")
        zip_label = _safe_export_segment(body.zip_name or f"{child.name}.zip", f"{child.name}.zip")
        if not zip_label.lower().endswith(".zip"):
            zip_label = f"{zip_label}.zip"
        # Reserve the filename atomically: parallel exports must never share it.
        for _ in range(500):
            candidate = _unique_export_file(parent, zip_label)
            try:
                stream = candidate.open('xb')
                archive_path = candidate
                break
            except FileExistsError:
                continue
        else:
            raise ExportWriteError('没法写入压缩包')
        try:
            with stream, zipfile.ZipFile(stream, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
                for name in body.names:
                    file_name = _safe_export_file_name(name)
                    source = _direct_child(child, file_name)
                    if not source.is_file():
                        raise ExportWriteError("要打包的文件不在文件夹里")
                    archive.write(source, arcname=file_name)
        except ExportWriteError:
            archive_path.unlink(missing_ok=True)
            raise
        except OSError as exc:
            archive_path.unlink(missing_ok=True)
            raise ExportWriteError("没法写入压缩包") from exc
    except OSError as exc:
        raise ExportWriteError('没法写入压缩包') from exc
    return archive_path.name


async def _read_limited_upload(upload: UploadFile, limit: int) -> bytes:
    buffer = io.BytesIO()
    total = 0
    while True:
        chunk = await upload.read(_CHUNK_SIZE)
        if not chunk:
            break
        total += len(chunk)
        if total > limit:
            raise ExportWriteError("单个导出文件过大")
        buffer.write(chunk)
    return buffer.getvalue()


__all__ = ["router"]
