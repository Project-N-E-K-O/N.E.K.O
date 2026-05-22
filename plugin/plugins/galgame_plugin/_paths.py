from __future__ import annotations

import os
from pathlib import Path

from utils.config_manager import get_config_manager

from .memory_reader import is_windows_platform


_BUNDLED_KEY: tuple[str, str] = ("PP-OCRv4", "ch")
_INSTALL_STATE_NAME = "install_state.json"


def _expand_candidate_path(raw_path: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(raw_path)))


def _app_runtimes_root() -> Path:
    return get_config_manager().app_docs_dir / "runtimes" / "galgame_plugin"


def default_rapidocr_install_target_raw() -> str:
    if is_windows_platform():
        return str(_app_runtimes_root() / "RapidOCR")
    return ""


def default_rapidocr_install_target_raw_legacy() -> str:
    if is_windows_platform():
        return "%LOCALAPPDATA%/Programs/N.E.K.O/RapidOCR"
    return ""


def resolve_rapidocr_install_target(raw_target_dir: str) -> Path:
    from ._model_registry import RAPIDOCR_PACKAGE_NAME

    normalized = str(raw_target_dir or "").strip()
    if normalized:
        return _expand_candidate_path(normalized)

    target = _app_runtimes_root() / "RapidOCR"
    if not target.exists():
        legacy_raw = default_rapidocr_install_target_raw_legacy()
        if legacy_raw:
            legacy_target = _expand_candidate_path(legacy_raw)
            legacy_package_dir = legacy_target / "runtime" / "site-packages" / RAPIDOCR_PACKAGE_NAME
            if legacy_package_dir.exists():
                return legacy_target
    return target


def resolve_rapidocr_runtime_dir(raw_target_dir: str) -> Path:
    target_dir = resolve_rapidocr_install_target(raw_target_dir)
    return target_dir / "runtime" if target_dir else Path()


def resolve_rapidocr_site_packages_dir(raw_target_dir: str) -> Path:
    runtime_dir = resolve_rapidocr_runtime_dir(raw_target_dir)
    return runtime_dir / "site-packages" if runtime_dir else Path()


def resolve_rapidocr_model_cache_dir(raw_target_dir: str) -> Path:
    target_dir = resolve_rapidocr_install_target(raw_target_dir)
    return target_dir / "models" if target_dir else Path()


def _rapidocr_install_state_path(raw_target_dir: str) -> Path:
    target_dir = resolve_rapidocr_install_target(raw_target_dir)
    return target_dir / _INSTALL_STATE_NAME if target_dir else Path()
