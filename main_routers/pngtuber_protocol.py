# -*- coding: utf-8 -*-
"""Shared NEKO PNGTuber protocol helpers.

This module is intentionally small: it centralizes the naming, path resolving,
and adapter inference used by routers without taking over every model workflow.
"""

from __future__ import annotations

import json
from pathlib import Path
from pathlib import PurePosixPath
from urllib.parse import urlsplit

NEKO_PNGTUBER_PACKAGE_FORMAT = "neko.pngtuber.package.v2"
NEKO_PNGTUBER_METADATA_FORMAT = "neko.pngtuber.v2"
NEKO_PNGTUBER_METADATA_FILENAME = "metadata.neko-pngtuber.v2.json"
NEKO_PNGTUBER_ADAPTER = "neko_pngtuber_v2"
LAYERED_CANVAS_ADAPTER = "layered_canvas_v1"

PNGTUBER_USER_PATH = "/user_pngtuber"
PNGTUBER_EXTENSIONS = {".png", ".gif", ".jpg", ".jpeg", ".webp"}
PNGTUBER_IMAGE_KEYS = (
    "idle_image",
    "talking_image",
    "drag_image",
    "click_image",
    "happy_image",
    "sad_image",
    "angry_image",
    "surprised_image",
)
LEGACY_PNGTUBER_METADATA_FILENAMES = (
    "metadata.pngtube-remix.json",
    "metadata.pngtuber-plus.json",
    "metadata.live2d-auto-layer.json",
    "metadata.json",
)
PNGTUBER_METADATA_FILENAMES = (
    NEKO_PNGTUBER_METADATA_FILENAME,
    *LEGACY_PNGTUBER_METADATA_FILENAMES,
)
PNGTUBER_LAYERED_ADAPTERS = (NEKO_PNGTUBER_ADAPTER, LAYERED_CANVAS_ADAPTER)


def _warn(logger, message: str) -> None:
    if logger is not None:
        logger.warning(message)


def safe_relative_path(raw_path: str) -> PurePosixPath | None:
    normalized = str(raw_path or "").replace("\\", "/").strip()
    if not normalized:
        return None
    if normalized.startswith("/"):
        return None
    if urlsplit(normalized).scheme:
        return None
    raw_parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in raw_parts):
        return None
    rel = PurePosixPath(normalized)
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        return None
    return rel


def adapter_for_metadata(metadata_path: str, raw_adapter: str = "") -> str:
    adapter = str(raw_adapter or "").strip()
    filename = PurePosixPath(urlsplit(str(metadata_path or "").replace("\\", "/")).path).name
    if filename == NEKO_PNGTUBER_METADATA_FILENAME:
        return NEKO_PNGTUBER_ADAPTER
    if filename in LEGACY_PNGTUBER_METADATA_FILENAMES:
        return LAYERED_CANVAS_ADAPTER
    if adapter in PNGTUBER_LAYERED_ADAPTERS:
        return adapter
    return ""


def is_neko_pngtuber_v2_model(model_json: dict) -> bool:
    """Return true when a model manifest opts into NEKO PNGTuber v2."""
    model = model_json if isinstance(model_json, dict) else {}
    config = model.get("pngtuber") or model.get("_reserved", {}).get("avatar", {}).get("pngtuber") or {}
    metadata_path = str(config.get("layered_metadata") or config.get("metadata") or "")
    return (
        model.get("format") == NEKO_PNGTUBER_PACKAGE_FORMAT
        or str(config.get("adapter") or "").strip() == NEKO_PNGTUBER_ADAPTER
        or PurePosixPath(urlsplit(metadata_path.replace("\\", "/")).path).name == NEKO_PNGTUBER_METADATA_FILENAME
    )


def _validate_local_json_ref(package_dir: Path, raw_path: str, *, label: str) -> tuple[Path | None, str]:
    rel = safe_relative_path(urlsplit(str(raw_path or "").replace("\\", "/")).path)
    if rel is None:
        return None, f"{label} 路径无效: {raw_path}"
    if rel.suffix.lower() != ".json":
        return None, f"{label} 必须是 .json 文件: {raw_path}"
    target = package_dir / rel.as_posix()
    if not target.is_file():
        return None, f"{label} 引用的文件不存在: {raw_path}"
    return target, ""


def _validate_local_asset_ref(package_dir: Path, raw_path: str, *, label: str) -> tuple[Path | None, str]:
    rel = safe_relative_path(urlsplit(str(raw_path or "").replace("\\", "/")).path)
    if rel is None:
        return None, f"{label} 路径无效: {raw_path}"
    if rel.suffix.lower() not in PNGTUBER_EXTENSIONS:
        return None, f"{label} 文件格式不支持: {raw_path}"
    target = package_dir / rel.as_posix()
    if not target.is_file():
        return None, f"{label} 引用的文件不存在: {raw_path}"
    return target, ""


def _positive_int(value) -> bool:
    return type(value) is int and value > 0


def validate_neko_pngtuber_v2_package(package_dir: Path, model_json: dict) -> tuple[bool, str]:
    """Validate a package against the NEKO PNGTuber v2 file contract."""
    package_dir = Path(package_dir)
    model = model_json if isinstance(model_json, dict) else {}
    if model.get("format") != NEKO_PNGTUBER_PACKAGE_FORMAT:
        return False, f"model.json 的 format 必须是 {NEKO_PNGTUBER_PACKAGE_FORMAT}"
    if model.get("model_type") != "pngtuber":
        return False, "model.json 的 model_type 必须是 pngtuber"

    config = model.get("pngtuber")
    if not isinstance(config, dict):
        return False, "model.json 必须包含 pngtuber 配置"
    if str(config.get("adapter") or "").strip() != NEKO_PNGTUBER_ADAPTER:
        return False, f"pngtuber.adapter 必须是 {NEKO_PNGTUBER_ADAPTER}"

    idle_image = str(config.get("idle_image") or "").strip()
    if not idle_image:
        return False, "PNGTuber v2 必须配置 pngtuber.idle_image"
    _, error = _validate_local_asset_ref(package_dir, idle_image, label="pngtuber.idle_image")
    if error:
        return False, error

    for key in PNGTUBER_IMAGE_KEYS:
        value = str(config.get(key) or "").strip()
        if not value:
            continue
        _, error = _validate_local_asset_ref(package_dir, value, label=f"pngtuber.{key}")
        if error:
            return False, error

    metadata_ref = str(config.get("metadata") or config.get("layered_metadata") or "").strip()
    if not metadata_ref:
        return False, f"PNGTuber v2 必须配置 pngtuber.metadata 指向 {NEKO_PNGTUBER_METADATA_FILENAME}"
    if PurePosixPath(metadata_ref.replace("\\", "/")).name != NEKO_PNGTUBER_METADATA_FILENAME:
        return False, f"PNGTuber v2 metadata 文件名必须是 {NEKO_PNGTUBER_METADATA_FILENAME}"
    metadata_path, error = _validate_local_json_ref(package_dir, metadata_ref, label="pngtuber.metadata")
    if error:
        return False, error

    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"{NEKO_PNGTUBER_METADATA_FILENAME} 解析失败: {exc}"

    if not isinstance(metadata, dict):
        return False, f"{NEKO_PNGTUBER_METADATA_FILENAME} 必须是 JSON object"
    if metadata.get("format") != NEKO_PNGTUBER_METADATA_FORMAT:
        return False, f"metadata.format 必须是 {NEKO_PNGTUBER_METADATA_FORMAT}"
    if str(metadata.get("runtime") or "").strip() != "neko_layered_canvas":
        return False, "metadata.runtime 必须是 neko_layered_canvas"

    canvas = metadata.get("canvas")
    if not isinstance(canvas, dict) or not _positive_int(canvas.get("width")) or not _positive_int(canvas.get("height")):
        return False, "metadata.canvas 必须包含正整数 width/height"

    layers = metadata.get("layers")
    if not isinstance(layers, list) or not layers:
        return False, "metadata.layers 必须至少包含一个图层"
    if not _positive_int(metadata.get("state_count")):
        return False, "metadata.state_count 必须是正整数"
    state_count = int(metadata.get("state_count"))
    emotions = metadata.get("emotions")
    if not isinstance(emotions, dict) or not emotions:
        return False, "PNGTuber v2 metadata 必须包含非空 emotions 映射"
    for emotion_name, emotion_config in emotions.items():
        if not str(emotion_name or "").strip():
            return False, "metadata.emotions 不能包含空情绪名"
        if not isinstance(emotion_config, dict):
            return False, f"metadata.emotions.{emotion_name} 必须是 object"
        try:
            state_index = int(emotion_config["state_index"])
        except (KeyError, TypeError, ValueError):
            return False, f"metadata.emotions.{emotion_name}.state_index 必须是整数"
        if state_index < 0 or state_index >= state_count:
            return False, f"metadata.emotions.{emotion_name}.state_index 超出 state_count"
    seen_ids: set[str] = set()
    for index, layer in enumerate(layers):
        if not isinstance(layer, dict):
            return False, f"metadata.layers[{index}] 必须是 object"
        layer_id = str(layer.get("id") or layer.get("name") or "").strip()
        if not layer_id:
            return False, f"metadata.layers[{index}] 必须包含 id 或 name"
        if layer_id in seen_ids:
            return False, f"metadata.layers[{index}] id/name 重复: {layer_id}"
        seen_ids.add(layer_id)
        image_ref = str(layer.get("image") or "").strip()
        if not image_ref:
            return False, f"metadata.layers[{index}].image 必须存在"
        _, error = _validate_local_asset_ref(package_dir, image_ref, label=f"metadata.layers[{index}].image")
        if error:
            return False, error

    return True, ""


def resolve_pngtuber_image_path(image_path: str, config_manager, target_name: str = "", logger=None) -> str:
    """Resolve a PNGTuber image reference to a browser-loadable URL."""
    image_path = str(image_path or "").strip().replace("\\", "/")
    if not image_path or image_path.lower() in {"undefined", "null"}:
        return ""
    if image_path.startswith("http://") or image_path.startswith("https://"):
        return image_path
    if image_path.startswith("//"):
        _warn(logger, f"Invalid PNGTuber protocol-relative image path for {target_name}: {image_path}")
        return ""

    lookup_path = urlsplit(image_path).path
    if image_path.startswith("/"):
        if lookup_path.startswith(PNGTUBER_USER_PATH + "/"):
            rel = lookup_path[len(PNGTUBER_USER_PATH) + 1:]
            safe_rel = safe_relative_path(rel)
            if safe_rel is None:
                _warn(logger, f"Invalid PNGTuber image path for {target_name}: {image_path}")
                return ""
            if (config_manager.pngtuber_dir / safe_rel.as_posix()).exists():
                return image_path
            _warn(logger, f"PNGTuber image not found for {target_name}: {image_path}")
            return ""
        return image_path

    safe_rel = safe_relative_path(lookup_path)
    if safe_rel is None:
        _warn(logger, f"Invalid PNGTuber image path for {target_name}: {image_path}")
        return ""
    if safe_rel.suffix.lower() not in PNGTUBER_EXTENSIONS:
        _warn(logger, f"Unsupported PNGTuber image extension for {target_name}: {image_path}")
        return ""
    if (config_manager.pngtuber_dir / safe_rel.as_posix()).exists():
        return f"{PNGTUBER_USER_PATH}/{safe_rel.as_posix()}"
    _warn(logger, f"PNGTuber image not found for {target_name}: {image_path}")
    return ""


def resolve_pngtuber_metadata_path(metadata_path: str, config_manager, target_name: str = "", logger=None) -> str:
    """Resolve a PNGTuber metadata reference to a browser-loadable URL."""
    metadata_path = str(metadata_path or "").strip().replace("\\", "/")
    if not metadata_path or metadata_path.lower() in {"undefined", "null"}:
        return ""
    if metadata_path.startswith("http://") or metadata_path.startswith("https://"):
        return metadata_path
    if metadata_path.startswith("//") or metadata_path.startswith("data:"):
        _warn(logger, f"Invalid PNGTuber metadata path for {target_name}: {metadata_path}")
        return ""

    lookup_path = urlsplit(metadata_path).path
    if not lookup_path.lower().endswith(".json"):
        _warn(logger, f"Unsupported PNGTuber metadata extension for {target_name}: {metadata_path}")
        return ""
    if metadata_path.startswith("/"):
        if lookup_path.startswith(PNGTUBER_USER_PATH + "/"):
            rel = lookup_path[len(PNGTUBER_USER_PATH) + 1:]
            safe_rel = safe_relative_path(rel)
            if safe_rel is None:
                _warn(logger, f"Invalid PNGTuber metadata path for {target_name}: {metadata_path}")
                return ""
            if (config_manager.pngtuber_dir / safe_rel.as_posix()).exists():
                return metadata_path
            _warn(logger, f"PNGTuber metadata not found for {target_name}: {metadata_path}")
            return ""
        return metadata_path

    safe_rel = safe_relative_path(lookup_path)
    if safe_rel is None:
        _warn(logger, f"Invalid PNGTuber metadata path for {target_name}: {metadata_path}")
        return ""
    if (config_manager.pngtuber_dir / safe_rel.as_posix()).exists():
        return f"{PNGTUBER_USER_PATH}/{safe_rel.as_posix()}"
    _warn(logger, f"PNGTuber metadata not found for {target_name}: {metadata_path}")
    return ""


def infer_pngtuber_metadata_from_idle(idle_path: str, config_manager) -> str:
    """Find known metadata next to a resolved PNGTuber idle image URL."""
    lookup_path = urlsplit(str(idle_path or "").replace("\\", "/")).path
    parts = [part for part in lookup_path.split("/") if part]
    if len(parts) < 3 or parts[0] != PNGTUBER_USER_PATH.strip("/"):
        return ""
    model_folder = parts[1]
    safe_model_folder = safe_relative_path(model_folder)
    if safe_model_folder is None:
        return ""
    root = config_manager.pngtuber_dir / safe_model_folder.as_posix()
    for filename in PNGTUBER_METADATA_FILENAMES:
        if (root / filename).is_file():
            return f"{PNGTUBER_USER_PATH}/{safe_model_folder.as_posix()}/{filename}"
    return ""


def normalize_pngtuber_runtime_config(raw_config: dict, config_manager, target_name: str = "", logger=None) -> dict:
    """Return the resolved runtime config consumed by the frontend."""
    raw = raw_config if isinstance(raw_config, dict) else {}
    normalized = dict(raw)
    for key in PNGTUBER_IMAGE_KEYS:
        normalized[key] = resolve_pngtuber_image_path(
            str(raw.get(key) or ""),
            config_manager,
            target_name,
            logger,
        )
    metadata_path = resolve_pngtuber_metadata_path(
        str(raw.get("layered_metadata") or raw.get("metadata") or ""),
        config_manager,
        target_name,
        logger,
    )
    if not metadata_path:
        metadata_path = infer_pngtuber_metadata_from_idle(normalized.get("idle_image", ""), config_manager)
    normalized["metadata"] = metadata_path
    normalized["layered_metadata"] = metadata_path
    normalized["adapter"] = adapter_for_metadata(metadata_path, str(raw.get("adapter") or ""))
    normalized["protocol"] = NEKO_PNGTUBER_METADATA_FORMAT if normalized["adapter"] == NEKO_PNGTUBER_ADAPTER else ""
    return normalized
