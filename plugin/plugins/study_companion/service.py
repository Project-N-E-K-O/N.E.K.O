from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from .models import OcrSnapshot, StudyConfig, StudyState, TutorReply, json_copy


def build_status_payload(
    *,
    config: StudyConfig,
    state: StudyState,
    history: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "status": state.status,
        "active_mode": state.active_mode,
        "last_error": state.last_error,
        "last_started_at": state.last_started_at,
        "last_ocr_text": state.last_ocr_text,
        "last_ocr_at": state.last_ocr_at,
        "last_reply": state.last_reply,
        "last_reply_at": state.last_reply_at,
        "checkpoint": json_copy(state.checkpoint),
        "dependencies": json_copy(state.dependency_status),
        "config": config.to_dict(),
        "history": list(history or []),
    }


def build_dependency_status(config: StudyConfig) -> dict[str, Any]:
    rapidocr = _inspect_rapidocr(config)
    tesseract = _inspect_tesseract(config)
    dxcam = _inspect_dxcam()
    missing = [
        name
        for name, status in {
            "rapidocr": rapidocr,
            "tesseract": tesseract,
            "dxcam": dxcam,
        }.items()
        if isinstance(status, dict) and status.get("installed") is False and status.get("can_install")
    ]
    return {
        "rapidocr": rapidocr,
        "tesseract": tesseract,
        "dxcam": dxcam,
        "missing_installable": missing,
    }


def _expand_path(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(str(value or ""))))


def _inspect_rapidocr(config: StudyConfig) -> dict[str, Any]:
    spec = importlib.util.find_spec("rapidocr_onnxruntime")
    origin = str(getattr(spec, "origin", "") or "") if spec is not None else ""
    installed = bool(spec)
    return {
        "install_supported": sys.platform == "win32",
        "installed": installed,
        "can_install": False,
        "can_download_models": installed and (config.rapidocr_lang_type, config.rapidocr_ocr_version) != ("ch", "PP-OCRv4"),
        "detected_path": str(Path(origin).resolve().parent) if origin else "",
        "target_dir": config.rapidocr_install_target_dir,
        "engine_type": config.rapidocr_engine_type,
        "lang_type": config.rapidocr_lang_type,
        "model_type": config.rapidocr_model_type,
        "ocr_version": config.rapidocr_ocr_version,
        "detail": "installed" if installed else "missing",
    }


def _inspect_tesseract(config: StudyConfig) -> dict[str, Any]:
    candidates: list[Path] = []
    if config.ocr_tesseract_path:
        candidates.append(_expand_path(config.ocr_tesseract_path))
    if config.ocr_install_target_dir:
        candidates.append(_expand_path(config.ocr_install_target_dir) / "tesseract.exe")
    path_hit = shutil.which("tesseract.exe" if sys.platform == "win32" else "tesseract")
    if path_hit:
        candidates.append(Path(path_hit))
    detected = next((candidate for candidate in candidates if candidate.is_file()), None)
    installed = detected is not None
    return {
        "install_supported": sys.platform == "win32",
        "installed": installed,
        "can_install": sys.platform == "win32" and not installed,
        "detected_path": str(detected) if detected else "",
        "target_dir": config.ocr_install_target_dir,
        "required_languages": [item for item in config.ocr_languages.split("+") if item],
        "missing_languages": [],
        "detail": "installed" if installed else "missing",
    }


def _inspect_dxcam() -> dict[str, Any]:
    supported = sys.platform == "win32"
    spec = importlib.util.find_spec("dxcam") if supported else None
    origin = str(getattr(spec, "origin", "") or "") if spec is not None else ""
    installed = bool(origin)
    return {
        "install_supported": supported,
        "installed": installed,
        "can_install": False,
        "detected_path": origin,
        "package_name": "dxcam",
        "target_dir": "current_python_environment",
        "detail": "installed" if installed else ("missing" if supported else "unsupported_platform"),
        "runtime_error": "",
    }


def build_explain_payload(reply: TutorReply) -> dict[str, Any]:
    payload = reply.to_dict()
    payload["summary"] = reply.reply
    return payload


def build_ocr_payload(snapshot: OcrSnapshot) -> dict[str, Any]:
    payload = snapshot.to_dict()
    payload["summary"] = snapshot.text or snapshot.status
    return payload
