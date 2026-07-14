"""Embedding model-profile validation and directory selection."""
from __future__ import annotations

import os
import sys
from collections.abc import Callable, Iterable


def is_nonempty_file(path: str) -> bool:
    """Return whether ``path`` is a regular, non-empty file."""
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def profile_exists(model_dir: str, profile_id: str) -> bool:
    return os.path.isdir(os.path.join(model_dir, profile_id))


def profile_is_complete(
    model_dir: str,
    profile_id: str,
    quantization: str | None = None,
    *,
    file_check: Callable[[str], bool] = is_nonempty_file,
) -> bool:
    """Validate the tokenizer and complete ONNX external-data pair."""
    profile_dir = os.path.join(model_dir, profile_id)
    if not os.path.isdir(profile_dir):
        return False
    if not file_check(os.path.join(profile_dir, "tokenizer.json")):
        return False
    stem = "model.onnx" if quantization == "fp32" else "model_quantized.onnx"
    model_path = os.path.join(profile_dir, "onnx", stem)
    return file_check(model_path) and file_check(model_path + "_data")


def bundled_model_dirs(
    module_file: str,
    model_dir_name: str,
    *,
    sys_module=sys,
    compiled: bool = False,
) -> list[str]:
    """Return de-duplicated source and frozen-build asset roots."""
    roots: list[str] = []
    if hasattr(sys_module, "_MEIPASS"):
        roots.append(str(sys_module._MEIPASS))
    if getattr(sys_module, "frozen", False) or compiled:
        roots.append(os.path.dirname(os.path.abspath(sys_module.executable)))
    roots.append(os.path.dirname(os.path.dirname(os.path.abspath(module_file))))

    seen: set[str] = set()
    result: list[str] = []
    for root in roots:
        path = os.path.abspath(os.path.join(root, "data", model_dir_name))
        if path not in seen:
            seen.add(path)
            result.append(path)
    return result


def select_model_dir(
    app_docs_model_dir: str,
    profile_id: str,
    quantization: str | None,
    bundled_dirs: Iterable[str],
    *,
    completeness_check: Callable[[str, str, str | None], bool],
) -> str:
    """Prefer a complete app-data profile, then a complete bundle."""
    if completeness_check(app_docs_model_dir, profile_id, quantization):
        return app_docs_model_dir
    for bundled_dir in bundled_dirs:
        if completeness_check(bundled_dir, profile_id, quantization):
            return bundled_dir
    return app_docs_model_dir
