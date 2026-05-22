from __future__ import annotations

import importlib
import importlib.util
import inspect
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from ._model_registry import (
    RAPIDOCR_PACKAGE_NAME,
    _resolve_rapidocr_model_paths,
    rapidocr_selected_model_name,
)
from ._paths import (
    resolve_rapidocr_model_cache_dir,
    resolve_rapidocr_site_packages_dir,
)


# Leave one core free for the OS / interactive use; floor at 2 so 1-2 core hosts still parallelise.
_RAPIDOCR_INFERENCE_THREAD_LIMIT = max(2, (os.cpu_count() or 2) - 1)

_RAPIDOCR_IMPORT_CONTEXT_LOCK = threading.RLock()


@contextmanager
def _rapidocr_import_context(
    *,
    site_packages_dir: Path,
    model_cache_dir: Path,
) -> Iterator[None]:
    with _RAPIDOCR_IMPORT_CONTEXT_LOCK:
        inserted = False
        old_model_dir = os.environ.get("RAPIDOCR_MODEL_DIR")
        old_model_home = os.environ.get("RAPIDOCR_MODEL_HOME")
        dll_handles: list[Any] = []
        # Legacy plugin-isolated install layout: only injected as a fallback
        # when the bundled main-program rapidocr_onnxruntime is NOT importable.
        # Otherwise sys.path order would let a stale legacy install shadow the
        # bundled (likely newer) version, breaking upgrades for users who
        # haven't manually cleaned %LOCALAPPDATA%/.../RapidOCR/runtime.
        bundled_available = importlib.util.find_spec(RAPIDOCR_PACKAGE_NAME) is not None
        use_legacy_layout = (
            site_packages_dir
            and site_packages_dir.is_dir()
            and not bundled_available
        )
        if use_legacy_layout:
            site_path = str(site_packages_dir)
            if site_path not in sys.path:
                sys.path.insert(0, site_path)
                inserted = True
            if hasattr(os, "add_dll_directory"):
                for candidate in (
                    site_packages_dir,
                    site_packages_dir / "onnxruntime",
                    site_packages_dir / "onnxruntime" / "capi",
                ):
                    if candidate.is_dir():
                        try:
                            dll_handles.append(os.add_dll_directory(str(candidate)))
                        except OSError:
                            continue
        if model_cache_dir:
            model_cache_dir.mkdir(parents=True, exist_ok=True)
            os.environ["RAPIDOCR_MODEL_DIR"] = str(model_cache_dir)
            os.environ["RAPIDOCR_MODEL_HOME"] = str(model_cache_dir)
        try:
            yield
        finally:
            for handle in dll_handles:
                try:
                    handle.close()
                except Exception:
                    pass
            if old_model_dir is None:
                os.environ.pop("RAPIDOCR_MODEL_DIR", None)
            else:
                os.environ["RAPIDOCR_MODEL_DIR"] = old_model_dir
            if old_model_home is None:
                os.environ.pop("RAPIDOCR_MODEL_HOME", None)
            else:
                os.environ["RAPIDOCR_MODEL_HOME"] = old_model_home
            if inserted:
                try:
                    sys.path.remove(str(site_packages_dir))
                except ValueError:
                    pass


def _rapidocr_package_dir(raw_target_dir: str) -> Path:
    site_packages_dir = resolve_rapidocr_site_packages_dir(raw_target_dir)
    return site_packages_dir / RAPIDOCR_PACKAGE_NAME if site_packages_dir else Path()

def _build_runtime_constructor_kwargs(
    runtime_class: type[Any],
    *,
    engine_type: str,
    lang_type: str,
    model_type: str,
    ocr_version: str,
    model_cache_dir: Path,
    package_models_dir: Path | None = None,
) -> dict[str, Any]:
    """Build kwargs passed to RapidOCR(...).

    Bug history: the previous implementation only kept keys whose name
    appeared in `inspect.signature(RapidOCR).parameters`. RapidOCR's signature
    is `(config_path: Optional[str] = None, **kwargs)`, so `'engine_type' in
    parameters` etc. were always False — every direct_value was silently
    dropped, and `lang_type` / `ocr_version` never reached the runtime.
    Real routing happens inside `UpdateParameters.__call__` (rapidocr's
    `parse_parameters.py`), which dispatches kwargs by *name prefix*
    (`det_*` / `cls_*` / `rec_*` / global). When a class accepts **kwargs,
    we passthrough the model paths directly so RapidOCR can route them.
    """
    try:
        parameters = inspect.signature(runtime_class).parameters
    except (TypeError, ValueError):
        return {}

    has_var_kwargs = any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in parameters.values()
    )
    # Passthrough mode (RapidOCR's actual signature path): the runtime accepts
    # **kwargs and routes by name prefix in `UpdateParameters.__call__`. We
    # resolve det/cls/rec paths from disk and hand them through. The resolver
    # checks two filename conventions per location — `_infer.onnx` (PaddleOCR
    # / wheel-bundled) and `_mobile.onnx` (RapidAI ModelScope downloads via
    # `download_rapidocr_models`). Only emit a model_path key when the file
    # actually exists; passing a non-existent path makes RapidOCR silently
    # fall back to its bundled config (wrong model, no error).
    if has_var_kwargs:
        det_path, cls_path, rec_path = _resolve_rapidocr_model_paths(
            model_cache_dir=model_cache_dir,
            package_models_dir=package_models_dir,
            lang_type=lang_type,
            ocr_version=ocr_version,
            model_type=model_type,
        )
        kwargs: dict[str, Any] = {}
        if det_path and rec_path:
            kwargs["det_model_path"] = det_path
            kwargs["rec_model_path"] = rec_path
            if cls_path:
                kwargs["cls_model_path"] = cls_path
        if engine_type:
            kwargs["engine_type"] = engine_type
        return kwargs

    kwargs: dict[str, Any] = {}

    # Legacy / explicit-arg mode: older RapidOCR builds may take some of
    # these as named parameters. inspect-by-name only catches them if they
    # actually exist in the signature (the original intent).
    direct_values = {
        "engine_type": engine_type,
        "lang_type": lang_type,
        "model_type": model_type,
        "ocr_version": ocr_version,
        "det_model_type": model_type,
        "cls_model_type": model_type,
        "rec_model_type": model_type,
        "cache_dir": str(model_cache_dir),
        "model_dir": str(model_cache_dir),
        "models_dir": str(model_cache_dir),
        "model_root": str(model_cache_dir),
    }
    for key, value in direct_values.items():
        if key in parameters:
            kwargs[key] = value
    return kwargs


_SESSION_OPTIONS_PATCH_TLS = threading.local()
_SESSION_OPTIONS_PATCH_LOCK = threading.Lock()
_SESSION_OPTIONS_PATCH_INSTALLED = False


def _ensure_session_options_patch_installed() -> None:
    """Patch ort.SessionOptions.__init__ once; the patch only acts on threads that opted in."""
    global _SESSION_OPTIONS_PATCH_INSTALLED
    if _SESSION_OPTIONS_PATCH_INSTALLED:
        return
    with _SESSION_OPTIONS_PATCH_LOCK:
        if _SESSION_OPTIONS_PATCH_INSTALLED:
            return
        try:
            import onnxruntime as _ort
        except Exception:
            return
        options_cls = getattr(_ort, "SessionOptions", None)
        if options_cls is None:
            return
        orig_init = options_cls.__init__

        def _patched_init(self: Any, *args: Any, **kwargs: Any) -> None:
            orig_init(self, *args, **kwargs)
            intra = getattr(_SESSION_OPTIONS_PATCH_TLS, "intra", None)
            if intra is None:
                return
            if getattr(self, "intra_op_num_threads", 0) == 0:
                self.intra_op_num_threads = intra

        options_cls.__init__ = _patched_init
        _SESSION_OPTIONS_PATCH_INSTALLED = True


@contextmanager
def _onnxruntime_intra_op_thread_cap(limit: int) -> Iterator[None]:
    """Clamp SessionOptions.intra_op_num_threads on the calling thread only."""
    _ensure_session_options_patch_installed()
    prev = getattr(_SESSION_OPTIONS_PATCH_TLS, "intra", None)
    _SESSION_OPTIONS_PATCH_TLS.intra = limit
    try:
        yield
    finally:
        if prev is None:
            try:
                del _SESSION_OPTIONS_PATCH_TLS.intra
            except AttributeError:
                pass
        else:
            _SESSION_OPTIONS_PATCH_TLS.intra = prev


def load_rapidocr_runtime(
    *,
    install_target_dir_raw: str,
    engine_type: str,
    lang_type: str,
    model_type: str,
    ocr_version: str,
) -> tuple[Any, dict[str, str]]:
    site_packages_dir = resolve_rapidocr_site_packages_dir(install_target_dir_raw)
    model_cache_dir = resolve_rapidocr_model_cache_dir(install_target_dir_raw)
    with _rapidocr_import_context(
        site_packages_dir=site_packages_dir,
        model_cache_dir=model_cache_dir,
    ):
        importlib.invalidate_caches()
        module = importlib.import_module(RAPIDOCR_PACKAGE_NAME)
        runtime_class = getattr(module, "RapidOCR", None)
        if runtime_class is None:
            raise RuntimeError("RapidOCR runtime class not found")
        module_file = getattr(module, "__file__", "") or ""
        # Sentinel must be None (not Path()) — Path() resolves to CWD and would
        # let _resolve_rapidocr_model_paths inadvertently scan the working
        # directory if `__file__` were ever missing.
        package_models_dir: Path | None = (
            Path(module_file).resolve().parent / "models" if module_file else None
        )
        with _onnxruntime_intra_op_thread_cap(_RAPIDOCR_INFERENCE_THREAD_LIMIT):
            runtime = runtime_class(
                **_build_runtime_constructor_kwargs(
                    runtime_class,
                    engine_type=engine_type,
                    lang_type=lang_type,
                    model_type=model_type,
                    ocr_version=ocr_version,
                    model_cache_dir=model_cache_dir,
                    package_models_dir=package_models_dir,
                )
            )
    metadata = {
        "detected_path": str(Path(getattr(module, "__file__", "")).resolve().parent),
        "model_cache_dir": str(model_cache_dir),
        "selected_model": rapidocr_selected_model_name(
            ocr_version=ocr_version,
            lang_type=lang_type,
            model_type=model_type,
        ),
    }
    return runtime, metadata
