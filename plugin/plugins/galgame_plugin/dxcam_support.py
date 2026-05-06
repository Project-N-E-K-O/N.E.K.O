from __future__ import annotations

import importlib
import importlib.util
import sys
from typing import Any, Callable

from .memory_reader import is_windows_platform

DXCAM_PACKAGE_NAME = "dxcam"


def _purge_module(module_name: str) -> None:
    for name in list(sys.modules.keys()):
        if name == module_name or name.startswith(f"{module_name}."):
            sys.modules.pop(name, None)


def _module_origin(module_name: str) -> str:
    try:
        spec = importlib.util.find_spec(module_name)
    except (ImportError, ValueError):
        return ""
    return str(getattr(spec, "origin", "") or "") if spec is not None else ""


def inspect_dxcam_installation(
    *,
    platform_fn: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    checker = platform_fn or is_windows_platform
    supported = bool(checker())
    detected_path = _module_origin(DXCAM_PACKAGE_NAME) if supported else ""
    runtime_error = ""
    detail = "installed" if detected_path else "missing"
    if not supported:
        detail = "unsupported_platform"
    elif detected_path:
        try:
            importlib.import_module(DXCAM_PACKAGE_NAME)
        except Exception as exc:
            detail = "broken_runtime"
            runtime_error = str(exc)
    return {
        "install_supported": supported,
        "installed": detail == "installed",
        # dxcam is now a main-program dependency (see pyproject.toml). When the
        # package is not importable the user is on a non-Windows platform or a
        # source install without `uv sync --group galgame` — neither case is
        # fixable from inside the running app, so `can_install` stays False.
        "can_install": False,
        "detected_path": detected_path,
        "package_name": DXCAM_PACKAGE_NAME,
        "target_dir": "current_python_environment",
        "detail": detail,
        "runtime_error": runtime_error,
    }
