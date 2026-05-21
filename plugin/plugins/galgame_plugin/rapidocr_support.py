from __future__ import annotations

from .memory_reader import is_windows_platform
from ._model_registry import (
    DEFAULT_RAPIDOCR_ENGINE_TYPE,
    DEFAULT_RAPIDOCR_LANG_TYPE,
    DEFAULT_RAPIDOCR_MODEL_TYPE,
    DEFAULT_RAPIDOCR_OCR_VERSION,
    RAPIDOCR_PACKAGE_NAME,
    missing_rapidocr_model_files,
    rapidocr_selected_model_name,
    required_rapidocr_model_files,
)
from ._paths import (
    default_rapidocr_install_target_raw,
    default_rapidocr_install_target_raw_legacy,
    resolve_rapidocr_install_target,
    resolve_rapidocr_model_cache_dir,
    resolve_rapidocr_runtime_dir,
    resolve_rapidocr_site_packages_dir,
)
from ._runtime import load_rapidocr_runtime
from ._inspect_download import (
    ProgressCallback,
    download_rapidocr_models,
    inspect_rapidocr_installation,
)
