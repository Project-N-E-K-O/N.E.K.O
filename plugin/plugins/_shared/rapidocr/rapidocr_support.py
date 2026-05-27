from __future__ import annotations

import importlib
import sys
import types as _types
from typing import Any

import httpx

from utils.config_manager import get_config_manager

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
    is_windows_platform,
    resolve_rapidocr_install_target,
    resolve_rapidocr_model_cache_dir,
    resolve_rapidocr_runtime_dir,
    resolve_rapidocr_site_packages_dir,
)
from ._runtime import (
    load_rapidocr_runtime,
    _build_runtime_constructor_kwargs,
    _onnxruntime_intra_op_thread_cap,
)
from ._inspect_download import (
    ProgressCallback,
    download_rapidocr_models,
    inspect_rapidocr_installation,
    _verify_model_sha256,
)


# Pre-split, all rapidocr names lived in this one module. After the split, tests
# that monkeypatch ``rapidocr_support.<name>`` must keep affecting the call sites
# in the submodules where the names now actually live (``download_rapidocr_models``,
# ``load_rapidocr_runtime``, ``inspect_rapidocr_installation`` resolve their
# helpers in their own module globals). This proxy reroutes the writes so the
# old test-time semantics survive the split unchanged.
_PROXY_TO_INSPECT_DOWNLOAD = frozenset(
    {
        "httpx",
        "_verify_model_sha256",
        "required_rapidocr_model_files",
        "missing_rapidocr_model_files",
    }
)
_PROXY_TO_RUNTIME = frozenset(
    {
        "importlib",
        "_onnxruntime_intra_op_thread_cap",
        "_build_runtime_constructor_kwargs",
        "load_rapidocr_runtime",
    }
)
_PROXY_TO_PATHS = frozenset(
    {
        "get_config_manager",
        "is_windows_platform",
    }
)


class _ShimModule(_types.ModuleType):
    def __setattr__(self, name: str, value: Any) -> None:
        super().__setattr__(name, value)
        if name in _PROXY_TO_INSPECT_DOWNLOAD:
            from . import _inspect_download

            setattr(_inspect_download, name, value)
        if name in _PROXY_TO_RUNTIME:
            from . import _runtime

            setattr(_runtime, name, value)
        if name in _PROXY_TO_PATHS:
            from . import _paths

            setattr(_paths, name, value)


sys.modules[__name__].__class__ = _ShimModule
