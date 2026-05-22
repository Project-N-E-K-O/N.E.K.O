from __future__ import annotations

import logging
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

try:
    import psutil
except ImportError:  # pragma: no cover - psutil is available in the project runtime.
    psutil = None

from ._types import DetectedGameProcess, MEMORY_READER_DEFAULT_ENGINE


_LOGGER = logging.getLogger(__name__)

_KIRIKIRI_DIR_CACHE_TTL_SECONDS = 10.0
_KIRIKIRI_COMMON_XP3_NAMES = {
    "data.xp3",
    "patch.xp3",
    "scenario.xp3",
}
_KIRIKIRI_PROCESS_SIGNATURE_PRESETS = (
    {
        "id": "senren_banka",
        "tokens": ("senrenbanka",),
        "steam_app_ids": ("1144400",),
    },
)
_EXCLUDED_PROCESS_NAMES = {
    "crashpad_handler",
}
_EXCLUDED_PROCESS_NAME_SUBSTRINGS = (
    "unitycrashhandler",
    "crashhandler",
    "crashreporter",
)
_KIRIKIRI_DIR_CACHE_LOCK = threading.Lock()
_KIRIKIRI_DIR_CACHE: dict[str, tuple[float, str, str]] = {}


def _engine_from_text(text: str) -> str:
    lowered = text.lower()
    if "renpy" in lowered or "ren'py" in lowered:
        return "renpy"
    if "unity" in lowered:
        return "unity"
    if "kirikiri" in lowered or "krkr" in lowered:
        return "kirikiri"
    return ""


def _normalize_process_signature_text(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _path_parts_for_signature(path: str) -> list[str]:
    normalized = str(path or "").strip()
    if not normalized:
        return []
    parts: list[str] = [normalized]
    try:
        path_obj = Path(normalized)
        parts.append(path_obj.stem)
        parts.append(path_obj.name)
        parts.append(path_obj.parent.name)
        parts.extend(path_obj.parts[-4:])
    except Exception:
        _LOGGER.debug("path info extraction failed", exc_info=True)
        pass
    return [part for part in parts if part]


def _kirikiri_preset_detection(
    *,
    name: str,
    cmdline: str,
    exe_path: str,
) -> tuple[str, str]:
    raw_values = [name, cmdline, *_path_parts_for_signature(exe_path)]
    normalized_values = [
        normalized
        for value in raw_values
        if (normalized := _normalize_process_signature_text(value))
    ]
    if not normalized_values:
        return "", ""
    combined = "\n".join(normalized_values)
    steam_context = any("steam" in value or "steamapps" in value for value in normalized_values)
    for preset in _KIRIKIRI_PROCESS_SIGNATURE_PRESETS:
        preset_id = str(preset.get("id") or "").strip()
        tokens = tuple(str(token or "").strip().lower() for token in preset.get("tokens", ()))
        if any(token and token in combined for token in tokens):
            return "kirikiri", f"detected_kirikiri_preset_{preset_id}"
        app_ids = tuple(str(app_id or "").strip() for app_id in preset.get("steam_app_ids", ()))
        if steam_context and any(app_id and app_id in combined for app_id in app_ids):
            return "kirikiri", f"detected_kirikiri_preset_{preset_id}"
    return "", ""


def _is_excluded_helper_process(name: str, cmdline: str) -> bool:
    lowered_name = str(name or "").strip().lower()
    lowered_cmdline = str(cmdline or "").strip().lower()
    if lowered_name in _EXCLUDED_PROCESS_NAMES:
        return True
    if any(token in lowered_name for token in _EXCLUDED_PROCESS_NAME_SUBSTRINGS):
        return True
    if "unitycrashhandler" in lowered_cmdline:
        return True
    return False


def _loaded_module_names(proc: Any) -> set[str]:
    names: set[str] = set()
    try:
        mappings = proc.memory_maps(grouped=False)
    except Exception:
        _LOGGER.debug(
            "memory_reader process module scan skipped for pid=%s",
            getattr(proc, "pid", ""),
            exc_info=True,
        )
        return names
    for item in mappings:
        path = getattr(item, "path", "") or ""
        if not path:
            continue
        names.add(Path(path).name.lower())
    return names


def _kirikiri_directory_detection(exe_path: str) -> tuple[str, str]:
    normalized_path = str(exe_path or "").strip()
    if not normalized_path:
        return "", ""
    try:
        exe_dir = Path(normalized_path).parent
    except Exception:
        _LOGGER.debug("exe_dir resolution failed", exc_info=True)
        return "", ""
    if not str(exe_dir):
        return "", ""
    cache_key = os.path.normcase(str(exe_dir))
    now = time.monotonic()
    with _KIRIKIRI_DIR_CACHE_LOCK:
        cached = _KIRIKIRI_DIR_CACHE.get(cache_key)
        if cached is not None and now - cached[0] < _KIRIKIRI_DIR_CACHE_TTL_SECONDS:
            return cached[1], cached[2]
    engine = ""
    reason = ""
    try:
        names = {item.name.lower() for item in exe_dir.iterdir()}
    except Exception:
        _LOGGER.debug("iterdir failed", exc_info=True)
        names = set()
    if "startup.tjs" in names:
        engine = "kirikiri"
        reason = "detected_kirikiri_startup_tjs"
    elif any(name in names for name in _KIRIKIRI_COMMON_XP3_NAMES):
        engine = "kirikiri"
        reason = "detected_kirikiri_common_xp3"
    elif any(name.endswith(".xp3") for name in names):
        engine = "kirikiri"
        reason = "detected_kirikiri_xp3"
    with _KIRIKIRI_DIR_CACHE_LOCK:
        _KIRIKIRI_DIR_CACHE[cache_key] = (now, engine, reason)
        if len(_KIRIKIRI_DIR_CACHE) > 512:
            for stale_key in list(_KIRIKIRI_DIR_CACHE)[:-512]:
                _KIRIKIRI_DIR_CACHE.pop(stale_key, None)
    return engine, reason


def _detect_process_engine(
    *,
    name: str,
    cmdline: str,
    exe_path: str,
    modules: set[str],
) -> tuple[str, str]:
    lowered_name = name.lower()
    lowered_cmdline = cmdline.lower()
    if "python" in lowered_name and "renpy" in lowered_cmdline:
        return "renpy", "detected_renpy_cmdline"
    if "renpy.pyd" in modules or "pygame" in modules:
        return "renpy", "detected_renpy_module"
    if "unity" in lowered_name or "unity" in lowered_cmdline:
        return "unity", "detected_unity_name_or_cmdline"
    if "unityplayer.dll" in modules or "assembly-csharp.dll" in modules:
        return "unity", "detected_unity_module"
    if "kirikiri" in lowered_name or "krkr" in lowered_name:
        return "kirikiri", "detected_kirikiri_process_name"
    if "krkr.dll" in modules:
        return "kirikiri", "detected_kirikiri_module"
    directory_engine, directory_reason = _kirikiri_directory_detection(exe_path)
    if directory_engine:
        return directory_engine, directory_reason
    preset_engine, preset_reason = _kirikiri_preset_detection(
        name=name,
        cmdline=cmdline,
        exe_path=exe_path,
    )
    if preset_engine:
        return preset_engine, preset_reason
    return "", ""


def _scan_processes(*, include_unknown: bool = False) -> list[DetectedGameProcess]:
    if psutil is None:
        return []
    detected: list[DetectedGameProcess] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "create_time", "exe"]):
        try:
            info = proc.info
            name = str(info.get("name") or "")
            cmdline_parts = info.get("cmdline") or []
            cmdline = " ".join(str(item) for item in cmdline_parts)
            if _is_excluded_helper_process(name, cmdline):
                continue
            exe_path = str(info.get("exe") or "")
            modules = _loaded_module_names(proc)
            engine, detection_reason = _detect_process_engine(
                name=name,
                cmdline=cmdline,
                exe_path=exe_path,
                modules=modules,
            )
            if not engine and not include_unknown:
                continue
            detected.append(
                DetectedGameProcess(
                    pid=int(info.get("pid") or 0),
                    name=name or f"pid-{int(info.get('pid') or 0)}",
                    create_time=float(info.get("create_time") or 0.0),
                    engine=engine or MEMORY_READER_DEFAULT_ENGINE,
                    exe_path=exe_path,
                    detection_reason=detection_reason or "unknown_engine",
                )
            )
        except Exception:
            _LOGGER.debug(
                "memory_reader process scan skipped for pid=%s",
                getattr(proc, "pid", ""),
                exc_info=True,
            )
            continue
    detected.sort(key=lambda item: (-item.create_time, item.pid))
    return detected


def _default_process_scanner() -> list[DetectedGameProcess]:
    return _scan_processes(include_unknown=False)


def _default_process_inventory() -> list[DetectedGameProcess]:
    return _scan_processes(include_unknown=True)
