from __future__ import annotations

import os
import sys
from pathlib import Path

from utils.config_manager import get_config_manager


_BUNDLED_KEY: tuple[str, str] = ("PP-OCRv4", "ch")
_INSTALL_STATE_NAME = "install_state.json"


def _expand_candidate_path(raw_path: str) -> Path:
    return Path(os.path.expanduser(os.path.expandvars(raw_path)))


def is_windows_platform() -> bool:
    return sys.platform == "win32"


def _app_runtimes_root() -> Path:
    return get_config_manager().app_docs_dir / "runtimes" / "study_companion"


def _legacy_galgame_rapidocr_target() -> Path:
    return get_config_manager().app_docs_dir / "runtimes" / "galgame_plugin" / "RapidOCR"


def _rapidocr_target_has_assets(target: Path, package_name: str) -> bool:
    package_dir = target / "runtime" / "site-packages" / package_name
    if package_dir.exists():
        return True
    models_dir = target / "models"
    return models_dir.is_dir() and any(models_dir.iterdir())


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
    if _rapidocr_target_has_assets(target, RAPIDOCR_PACKAGE_NAME):
        return target

    galgame_target = _legacy_galgame_rapidocr_target()
    if _rapidocr_target_has_assets(galgame_target, RAPIDOCR_PACKAGE_NAME):
        return galgame_target

    legacy_raw = default_rapidocr_install_target_raw_legacy()
    if legacy_raw:
        legacy_target = _expand_candidate_path(legacy_raw)
        if _rapidocr_target_has_assets(legacy_target, RAPIDOCR_PACKAGE_NAME):
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
