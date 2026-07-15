# -*- coding: utf-8 -*-
"""PNGTuber model package endpoints."""

import asyncio
import json
import math
import re
import shutil
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from fastapi import APIRouter, Body, File, UploadFile
from fastapi.responses import JSONResponse

from .pngtuber_importers import PNGTuberImportError, import_pngtuber_package
from .shared_state import get_config_manager
from utils.logger_config import get_module_logger

router = APIRouter(prefix="/api/model/pngtuber", tags=["pngtuber"])
logger = get_module_logger(__name__, "Main")

PNGTUBER_USER_PATH = "/user_pngtuber"
PNGTUBER_EXTENSIONS = {".png", ".gif", ".jpg", ".jpeg", ".webp"}
MAX_FILE_SIZE = 50 * 1024 * 1024
MAX_PACKAGE_SIZE = 250 * 1024 * 1024
CHUNK_SIZE = 1024 * 1024


def _slugify_name(name: str) -> str:
    cleaned = re.sub(r"[^\w.-]+", "_", (name or "").strip(), flags=re.UNICODE)
    cleaned = cleaned.strip("._-")
    return cleaned or "pngtuber_model"


def _safe_relative_path(raw_path: str) -> PurePosixPath | None:
    normalized = (raw_path or "").replace("\\", "/").strip("/")
    if not normalized:
        return None
    rel = PurePosixPath(normalized)
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        return None
    return rel


def _resolve_delete_folder_from_key(key: str) -> str | None:
    normalized = (key or "").replace("\\", "/").strip()
    if not normalized:
        return None

    parsed = urlsplit(normalized)
    if parsed.scheme and parsed.path:
        normalized = parsed.path
    else:
        normalized = normalized.split("?", 1)[0].split("#", 1)[0]

    rel = _safe_relative_path(normalized)
    if rel is None:
        return None

    parts = rel.parts
    user_prefix = PNGTUBER_USER_PATH.strip("/")
    if parts and parts[0] == user_prefix:
        parts = parts[1:]
    if not parts:
        return None

    if parts[-1].lower() == "model.json":
        if len(parts) != 2:
            return None
        return parts[-2]

    if len(parts) != 1:
        return None
    return parts[0]


def _split_upload_root(paths: list[PurePosixPath]) -> tuple[str, dict[PurePosixPath, PurePosixPath]]:
    first_parts = {p.parts[0] for p in paths if p.parts}
    if len(first_parts) == 1 and all(len(p.parts) > 1 for p in paths):
        root = next(iter(first_parts))
        return root, {p: PurePosixPath(*p.parts[1:]) for p in paths}
    return "", {p: p for p in paths}


def _read_model_json(package_dir: Path) -> dict:
    with open(package_dir / "model.json", "r", encoding="utf-8") as f:
        return json.load(f)


def _normalize_pngtuber_config(model_dir_name: str, model_json: dict) -> dict:
    raw = model_json.get("pngtuber") or model_json.get("_reserved", {}).get("avatar", {}).get("pngtuber") or {}
    result: dict = {}
    image_fields = [
        "idle_image",
        "talking_image",
        "drag_image",
        "click_image",
        "happy_image",
        "sad_image",
        "angry_image",
        "surprised_image",
    ]

    for field in image_fields:
        value = raw.get(field, "")
        if not isinstance(value, str) or not value.strip():
            result[field] = ""
            continue
        stripped = value.replace("\\", "/").strip()
        if stripped.startswith(("/", "http://", "https://")):
            result[field] = stripped
        else:
            rel = _safe_relative_path(stripped)
            result[field] = f"{PNGTUBER_USER_PATH}/{model_dir_name}/{rel.as_posix()}" if rel else ""

    metadata_path = raw.get("layered_metadata") or raw.get("metadata")
    if isinstance(metadata_path, str) and metadata_path.strip():
        stripped = metadata_path.strip().replace("\\", "/")
        if stripped.startswith(("/", "http://", "https://")):
            result["layered_metadata"] = stripped
        else:
            rel = _safe_relative_path(stripped)
            result["layered_metadata"] = f"{PNGTUBER_USER_PATH}/{model_dir_name}/{rel.as_posix()}" if rel else ""
    else:
        result["layered_metadata"] = ""

    adapter = raw.get("adapter")
    if isinstance(adapter, str):
        result["adapter"] = adapter
    elif result["layered_metadata"]:
        result["adapter"] = "layered_canvas_v1"
    else:
        result["adapter"] = ""

    result["scale"] = raw.get("scale", 1)
    result["offset_x"] = raw.get("offset_x", 0)
    result["offset_y"] = raw.get("offset_y", 0)
    try:
        scale_number = float(result["scale"])
        mobile_scale_default = min(scale_number, 1) if math.isfinite(scale_number) else 1
    except (TypeError, ValueError):
        mobile_scale_default = 1
    result["mobile_scale"] = raw.get("mobile_scale", mobile_scale_default)
    result["mobile_offset_x"] = raw.get("mobile_offset_x", 0)
    result["mobile_offset_y"] = raw.get("mobile_offset_y", 0)
    result["mirror"] = bool(raw.get("mirror", False))
    result["source_type"] = raw.get("source_type") or "transparent_asset"
    result["source_format"] = model_json.get("source_format") or raw.get("source_format") or result["source_type"]
    return result


def _validate_model_package(package_dir: Path, model_json: dict) -> tuple[bool, str]:
    if model_json.get("model_type") != "pngtuber":
        return False, "model.json 的 model_type 必须是 pngtuber"

    config = model_json.get("pngtuber") or model_json.get("_reserved", {}).get("avatar", {}).get("pngtuber") or {}
    idle_image = config.get("idle_image")
    if not isinstance(idle_image, str) or not idle_image.strip():
        return False, "PNGTuber 模型必须配置 idle_image"

    for key, value in config.items():
        if not key.endswith("_image") or not isinstance(value, str) or not value.strip():
            continue
        if value.startswith(("/", "http://", "https://")):
            continue
        rel = _safe_relative_path(value)
        if rel is None:
            return False, f"{key} 路径无效: {value}"
        if rel.suffix.lower() not in PNGTUBER_EXTENSIONS:
            return False, f"{key} 文件格式不支持: {value}"
        if not (package_dir / rel.as_posix()).exists():
            return False, f"{key} 引用的文件不存在: {value}"
    return True, ""


@router.post("/upload_model")
async def upload_pngtuber_model(files: list[UploadFile] = File(...)):
    if not files:
        return JSONResponse(status_code=400, content={"success": False, "error": "没有上传文件"})

    config_mgr = get_config_manager()
    if not config_mgr.ensure_pngtuber_directory():
        return JSONResponse(status_code=500, content={"success": False, "error": "PNGTuber目录创建失败"})

    upload_paths: list[PurePosixPath] = []
    by_path: dict[PurePosixPath, UploadFile] = {}
    for file in files:
        rel = _safe_relative_path(file.filename or "")
        if rel is None:
            return JSONResponse(status_code=400, content={"success": False, "error": f"上传路径无效: {file.filename}"})
        upload_paths.append(rel)
        by_path[rel] = file

    upload_root, stripped_paths = _split_upload_root(upload_paths)
    model_name_seed = upload_root or ""
    if not model_name_seed:
        model_file = next((f for p, f in by_path.items() if stripped_paths[p] == PurePosixPath("model.json")), None)
        if model_file:
            model_name_seed = Path(model_file.filename or "pngtuber_model").stem
        elif len(upload_paths) == 1:
            model_name_seed = Path(upload_paths[0].name or "pngtuber_model").stem
        else:
            # Multi-file third-party uploads (e.g. a .save/.pngRemix plus sidecar
            # images) carry no shared root and no model.json, but the importer will
            # name the model after the project file. Seed from that file so the
            # early target_dir.exists() check uses the real name instead of the
            # placeholder, which would otherwise reject any upload whenever a folder
            # literally named "pngtuber_model" already exists.
            project_exts = {".save", ".pngremix", ".veadomini", ".veado"}
            project_files = [p for p in upload_paths if p.suffix.lower() in project_exts]
            if len(project_files) == 1:
                model_name_seed = project_files[0].stem
            else:
                model_name_seed = "pngtuber_model"
    model_dir_name = _slugify_name(model_name_seed)

    target_dir = config_mgr.pngtuber_dir / model_dir_name
    if target_dir.exists():
        return JSONResponse(status_code=400, content={"success": False, "error": f"PNGTuber模型 {model_dir_name} 已存在，请先删除或重命名"})

    temp_dir = config_mgr.pngtuber_dir / f".{model_dir_name}.uploading"
    if temp_dir.exists():
        await asyncio.to_thread(shutil.rmtree, temp_dir)
    temp_dir.mkdir(parents=True, exist_ok=True)

    total_size = 0
    try:
        resolved_temp = temp_dir.resolve()
        for original_rel, file in by_path.items():
            stripped_rel = stripped_paths[original_rel]
            target_file = (temp_dir / stripped_rel.as_posix()).resolve()
            try:
                target_file.relative_to(resolved_temp)
            except ValueError:
                raise ValueError(f"上传路径越界: {file.filename}")
            target_file.parent.mkdir(parents=True, exist_ok=True)
            with open(target_file, "xb") as out:
                while True:
                    chunk = await file.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    total_size += len(chunk)
                    if total_size > MAX_PACKAGE_SIZE:
                        raise ValueError(f"模型包过大，最大允许 {MAX_PACKAGE_SIZE // (1024 * 1024)}MB")
                    if target_file.stat().st_size + len(chunk) > MAX_FILE_SIZE:
                        raise ValueError(f"单个文件过大，最大允许 {MAX_FILE_SIZE // (1024 * 1024)}MB")
                    out.write(chunk)

        import_result = import_pngtuber_package(temp_dir, model_dir_name)
        model_json = import_result.model_json
        ok, error = _validate_model_package(temp_dir, model_json)
        if not ok:
            return JSONResponse(status_code=400, content={"success": False, "error": error})

        model_dir_name = _slugify_name(import_result.model_name or model_json.get("name") or model_dir_name)
        target_dir = config_mgr.pngtuber_dir / model_dir_name
        if target_dir.exists():
            return JSONResponse(status_code=400, content={"success": False, "error": f"PNGTuber模型 {model_dir_name} 已存在，请先删除或重命名"})

        normalized_config = _normalize_pngtuber_config(model_dir_name, model_json)
        model_json["model_type"] = "pngtuber"
        model_json["pngtuber"] = normalized_config
        model_json["source_format"] = import_result.source_format
        with open(temp_dir / "model.json", "w", encoding="utf-8") as f:
            json.dump(model_json, f, ensure_ascii=False, indent=2)

        temp_dir.rename(target_dir)
        logger.info("PNGTuber模型上传成功: %s", target_dir)
        return JSONResponse(content={
            "success": True,
            "message": import_result.message or f"PNGTuber模型 {model_json.get('name') or model_dir_name} 上传成功",
            "model_type": "pngtuber",
            "model_name": model_json.get("name") or model_dir_name,
            "name": model_json.get("name") or model_dir_name,
            "folder": model_dir_name,
            "url": f"{PNGTUBER_USER_PATH}/{model_dir_name}/model.json",
            "pngtuber": normalized_config,
            "source_format": import_result.source_format,
            "warnings": import_result.warnings,
            "file_size": total_size,
        })
    except PNGTuberImportError as exc:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "error": str(exc),
                "source_format": exc.source_format,
                "warnings": exc.warnings,
            },
        )
    except ValueError as exc:
        return JSONResponse(status_code=400, content={"success": False, "error": str(exc)})
    except Exception as exc:
        logger.error("上传PNGTuber模型失败: %s", exc, exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})
    finally:
        for file in files:
            try:
                await file.close()
            except Exception:
                pass
        if temp_dir.exists():
            await asyncio.to_thread(shutil.rmtree, temp_dir, ignore_errors=True)


@router.get("/models")
async def get_pngtuber_models():
    try:
        config_mgr = get_config_manager()
        config_mgr.ensure_pngtuber_directory()
        models = []
        for package_dir in sorted(config_mgr.pngtuber_dir.iterdir(), key=lambda p: p.name.lower()):
            if not package_dir.is_dir() or not (package_dir / "model.json").exists():
                continue
            try:
                model_json = await asyncio.to_thread(_read_model_json, package_dir)
                if model_json.get("model_type") != "pngtuber":
                    continue
                pngtuber = _normalize_pngtuber_config(package_dir.name, model_json)
                display_name = model_json.get("name") or package_dir.name
                models.append({
                    "name": display_name,
                    "folder": package_dir.name,
                    "filename": package_dir.name,
                    "location": "user",
                    "type": "pngtuber",
                    "model_type": "pngtuber",
                    "url": f"{PNGTUBER_USER_PATH}/{package_dir.name}/model.json",
                    "pngtuber": pngtuber,
                    "source_format": model_json.get("source_format", "simple_package"),
                })
            except Exception as exc:
                logger.warning("跳过无效PNGTuber模型 %s: %s", package_dir, exc)
        return JSONResponse(content={"success": True, "models": models})
    except Exception as exc:
        logger.error("获取PNGTuber模型列表失败: %s", exc, exc_info=True)
        return JSONResponse(status_code=500, content={"success": False, "error": str(exc)})


@router.delete("/model")
async def delete_pngtuber_model(payload: dict = Body(...)):
    key = payload.get("folder") or payload.get("url") or payload.get("name")
    if not isinstance(key, str) or not key.strip():
        return JSONResponse(status_code=400, content={"success": False, "error": "缺少PNGTuber模型标识"})
    folder = _resolve_delete_folder_from_key(key)
    if not folder:
        return JSONResponse(status_code=400, content={"success": False, "error": "无效的PNGTuber模型标识"})

    config_mgr = get_config_manager()
    config_mgr.ensure_pngtuber_directory()
    target_dir = (config_mgr.pngtuber_dir / folder).resolve()
    root_dir = config_mgr.pngtuber_dir.resolve()
    try:
        target_dir.relative_to(root_dir)
    except ValueError:
        return JSONResponse(status_code=400, content={"success": False, "error": "路径越界"})
    if not target_dir.exists() or not target_dir.is_dir():
        return JSONResponse(status_code=404, content={"success": False, "error": "PNGTuber模型不存在"})
    await asyncio.to_thread(shutil.rmtree, target_dir)
    return JSONResponse(content={"success": True, "message": f"PNGTuber模型 {folder} 已删除"})
