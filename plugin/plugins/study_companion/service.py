from __future__ import annotations

import logging
import importlib.util
import subprocess
import sys
from pathlib import Path
from typing import Any

from .models import OcrSnapshot, StudyConfig, StudyState, TutorReply, json_copy


_LOGGER = logging.getLogger(__name__)


def build_status_payload(
    *,
    config: StudyConfig,
    state: StudyState,
    history: list[dict[str, Any]] | None = None,
    knowledge: dict[str, Any] | None = None,
    is_first_run: bool = False,
) -> dict[str, Any]:
    knowledge_payload = json_copy(knowledge or {})
    return {
        "status": state.status,
        "is_first_run": bool(is_first_run),
        "mode": config.mode,
        "default_mode": config.default_mode,
        "active_mode": state.active_mode,
        "mode_started_at": state.mode_started_at,
        "recent_mode_switches": json_copy(state.recent_mode_switches),
        "suggestion_cooldowns": json_copy(state.suggestion_cooldowns),
        "session_suggestions": json_copy(state.session_suggestions),
        "mode_lock_until": state.mode_lock_until,
        "last_error": state.last_error,
        "last_started_at": state.last_started_at,
        "last_ocr_text": state.last_ocr_text,
        "last_ocr_at": state.last_ocr_at,
        "screen_classification": json_copy(state.last_screen_classification),
        "recent_screen_classifications": json_copy(state.recent_screen_classifications),
        "current_question": json_copy(state.current_question),
        "last_answer_evaluation": json_copy(state.last_answer_evaluation),
        "session_summary_seed": json_copy(state.session_summary_seed),
        "recent_learning_events": json_copy(state.recent_learning_events),
        "last_question_at": state.last_question_at,
        "last_answer_evaluated_at": state.last_answer_evaluated_at,
        "last_session_summary": state.last_session_summary,
        "last_session_summary_at": state.last_session_summary_at,
        "last_reply": state.last_reply,
        "last_reply_at": state.last_reply_at,
        "checkpoint": json_copy(state.checkpoint),
        "dependencies": json_copy(state.dependency_status),
        "knowledge_summary": knowledge_payload.get("knowledge_summary") or {},
        "knowledge_quality_summary": knowledge_payload.get("knowledge_quality_summary")
        or {},
        "anonymous_knowledge_stats_summary": knowledge_payload.get(
            "anonymous_knowledge_stats_summary"
        )
        or {},
        "habit": knowledge_payload.get("habit") or {},
        "review_queue": knowledge_payload.get("review_queue") or [],
        "memory_deck": knowledge_payload.get("memory_deck") or {},
        "weak_topics": knowledge_payload.get("weak_topics") or [],
        "mastery_overview": knowledge_payload.get("mastery_overview") or [],
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
        if isinstance(status, dict)
        and status.get("installed") is False
        and status.get("can_install")
    ]
    return {
        "rapidocr": rapidocr,
        "tesseract": tesseract,
        "dxcam": dxcam,
        "missing_installable": missing,
    }


def _inspect_rapidocr(config: StudyConfig) -> dict[str, Any]:
    from plugin.plugins._shared.rapidocr.rapidocr_support import (
        inspect_rapidocr_installation,
    )

    return inspect_rapidocr_installation(
        install_target_dir_raw=config.rapidocr_install_target_dir,
        engine_type=config.rapidocr_engine_type,
        lang_type=config.rapidocr_lang_type,
        model_type=config.rapidocr_model_type,
        ocr_version=config.rapidocr_ocr_version,
        plugin_id="study_companion",
    )


def _inspect_tesseract(config: StudyConfig) -> dict[str, Any]:
    from .tesseract_support import inspect_tesseract_installation

    return inspect_tesseract_installation(
        configured_path=config.ocr_tesseract_path,
        install_target_dir_raw=config.ocr_install_target_dir,
        languages=config.ocr_languages,
    )


def _available_tesseract_languages(
    detected: Path | None, target_dir: Path | None
) -> set[str]:
    tessdata_dirs = []
    if detected is not None:
        try:
            completed = subprocess.run(
                [str(detected), "--list-langs"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5.0,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            output = "\n".join(
                part for part in (completed.stdout, completed.stderr) if part
            )
            languages = {
                line.strip()
                for line in output.splitlines()
                if line.strip()
                and not line.lower().startswith("list of available languages")
            }
            if completed.returncode != 0:
                _LOGGER.warning(
                    "tesseract --list-langs exited with code %s, falling back to filesystem scan",
                    completed.returncode,
                )
            if languages:
                return languages
        except subprocess.TimeoutExpired as exc:
            _LOGGER.warning(
                "tesseract --list-langs timed out, falling back to filesystem scan: %s",
                exc,
            )
        except OSError as exc:
            _LOGGER.warning(
                "tesseract --list-langs failed, falling back to filesystem scan: %s",
                exc,
            )
    if target_dir is not None:
        tessdata_dirs.append(target_dir / "tessdata")
    if detected is not None:
        tessdata_dirs.append(detected.parent / "tessdata")
    for tessdata_dir in tessdata_dirs:
        if tessdata_dir.is_dir():
            languages = {path.stem for path in tessdata_dir.glob("*.traineddata")}
            if languages:
                return languages
    return set()


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
        "detail": "installed"
        if installed
        else ("missing" if supported else "unsupported_platform"),
        "runtime_error": "",
    }


def build_tutor_payload(reply: TutorReply) -> dict[str, Any]:
    payload = reply.to_dict()
    if reply.payload:
        payload.update(json_copy(reply.payload))
    if not payload.get("summary"):
        payload["summary"] = reply.reply
    return payload


def build_explain_payload(reply: TutorReply) -> dict[str, Any]:
    return build_tutor_payload(reply)


def build_ocr_payload(snapshot: OcrSnapshot) -> dict[str, Any]:
    payload = snapshot.to_dict()
    payload["summary"] = snapshot.text or snapshot.status
    return payload
