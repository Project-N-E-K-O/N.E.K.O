from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from utils.config_manager import get_config_manager

from .memory_reader import is_windows_platform


RAPIDOCR_PACKAGE_NAME = "rapidocr_onnxruntime"
DEFAULT_RAPIDOCR_ENGINE_TYPE = "onnxruntime"
DEFAULT_RAPIDOCR_LANG_TYPE = "ch"
DEFAULT_RAPIDOCR_MODEL_TYPE = "mobile"
DEFAULT_RAPIDOCR_OCR_VERSION = "PP-OCRv5"
_INSTALL_STATE_NAME = "install_state.json"
# Leave one core free for the OS / interactive use; floor at 2 so 1-2 core hosts still parallelise.
_RAPIDOCR_INFERENCE_THREAD_LIMIT = max(2, (os.cpu_count() or 2) - 1)

_RAPIDOCR_IMPORT_CONTEXT_LOCK = threading.RLock()


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


def rapidocr_selected_model_name(
    *,
    ocr_version: str,
    lang_type: str,
    model_type: str,
) -> str:
    return "/".join(
        [
            str(ocr_version or DEFAULT_RAPIDOCR_OCR_VERSION).strip() or DEFAULT_RAPIDOCR_OCR_VERSION,
            str(lang_type or DEFAULT_RAPIDOCR_LANG_TYPE).strip() or DEFAULT_RAPIDOCR_LANG_TYPE,
            str(model_type or DEFAULT_RAPIDOCR_MODEL_TYPE).strip() or DEFAULT_RAPIDOCR_MODEL_TYPE,
        ]
    )


def _purge_modules(prefixes: tuple[str, ...]) -> None:
    for name in list(sys.modules.keys()):
        if any(name == prefix or name.startswith(f"{prefix}.") for prefix in prefixes):
            sys.modules.pop(name, None)


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
) -> dict[str, Any]:
    try:
        parameters = inspect.signature(runtime_class).parameters
    except (TypeError, ValueError):
        return {}
    kwargs: dict[str, Any] = {}
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
    force_reload: bool = False,
) -> tuple[Any, dict[str, str]]:
    site_packages_dir = resolve_rapidocr_site_packages_dir(install_target_dir_raw)
    model_cache_dir = resolve_rapidocr_model_cache_dir(install_target_dir_raw)
    with _rapidocr_import_context(
        site_packages_dir=site_packages_dir,
        model_cache_dir=model_cache_dir,
    ):
        if force_reload:
            _purge_modules((RAPIDOCR_PACKAGE_NAME,))
        importlib.invalidate_caches()
        module = importlib.import_module(RAPIDOCR_PACKAGE_NAME)
        runtime_class = getattr(module, "RapidOCR", None)
        if runtime_class is None:
            raise RuntimeError("RapidOCR runtime class not found")
        with _onnxruntime_intra_op_thread_cap(_RAPIDOCR_INFERENCE_THREAD_LIMIT):
            runtime = runtime_class(
                **_build_runtime_constructor_kwargs(
                    runtime_class,
                    engine_type=engine_type,
                    lang_type=lang_type,
                    model_type=model_type,
                    ocr_version=ocr_version,
                    model_cache_dir=model_cache_dir,
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


def inspect_rapidocr_installation(
    *,
    install_target_dir_raw: str,
    engine_type: str = DEFAULT_RAPIDOCR_ENGINE_TYPE,
    lang_type: str = DEFAULT_RAPIDOCR_LANG_TYPE,
    model_type: str = DEFAULT_RAPIDOCR_MODEL_TYPE,
    ocr_version: str = DEFAULT_RAPIDOCR_OCR_VERSION,
    platform_fn: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    checker = platform_fn or is_windows_platform
    supported = bool(checker())
    target_dir = resolve_rapidocr_install_target(install_target_dir_raw)
    runtime_dir = resolve_rapidocr_runtime_dir(install_target_dir_raw)
    site_packages_dir = resolve_rapidocr_site_packages_dir(install_target_dir_raw)
    model_cache_dir = resolve_rapidocr_model_cache_dir(install_target_dir_raw)
    package_dir = _rapidocr_package_dir(install_target_dir_raw)
    install_state_path = _rapidocr_install_state_path(install_target_dir_raw)
    selected_model = rapidocr_selected_model_name(
        ocr_version=ocr_version,
        lang_type=lang_type,
        model_type=model_type,
    )
    detail = "missing"
    detected_path = str(package_dir) if package_dir.exists() else ""
    install_state: dict[str, Any] = {}
    runtime_error = ""

    # Legacy install_state.json holds metadata about which model variant the
    # plugin-isolated install picked. Read it as a hint for callers; the bundled
    # path (post-refactor) never writes it, so absence is fine.
    if supported and install_state_path.is_file():
        try:
            install_state_payload = json.loads(install_state_path.read_text(encoding="utf-8"))
            if isinstance(install_state_payload, dict):
                install_state = install_state_payload
        except (OSError, ValueError, TypeError):
            install_state = {}

    # rapidocr-onnxruntime is now bundled into the main program (see
    # pyproject.toml [dependency-groups] galgame). Treat either source as
    # "package present": main interpreter import OR legacy plugin-isolated dir.
    bundled_spec = None
    try:
        bundled_spec = importlib.util.find_spec(RAPIDOCR_PACKAGE_NAME)
    except (ImportError, ValueError):
        bundled_spec = None
    package_present = package_dir.exists() or bundled_spec is not None

    if not supported:
        detail = "unsupported_platform"
    elif not package_present:
        detail = "missing"
    else:
        try:
            _runtime, runtime_meta = load_rapidocr_runtime(
                install_target_dir_raw=install_target_dir_raw,
                engine_type=engine_type,
                lang_type=lang_type,
                model_type=model_type,
                ocr_version=ocr_version,
                force_reload=False,
            )
            detected_path = str(runtime_meta.get("detected_path") or detected_path)
            detail = "installed"
        except Exception as exc:
            detail = "broken_runtime"
            runtime_error = str(exc)

    installed = detail == "installed"
    return {
        "install_supported": supported,
        "installed": installed,
        # rapidocr-onnxruntime is now bundled into the main program (see
        # pyproject.toml [dependency-groups] galgame). When it's not importable
        # the user is on a source install without `uv sync --group galgame` —
        # no in-app install action exists anymore (HTTP routes removed in this
        # refactor), so `can_install` stays False to keep the UI button hidden.
        "can_install": False,
        "detected_path": detected_path,
        "target_dir": str(target_dir) if target_dir else "",
        "runtime_dir": str(runtime_dir) if runtime_dir else "",
        "site_packages_dir": str(site_packages_dir) if site_packages_dir else "",
        "model_cache_dir": str(model_cache_dir) if model_cache_dir else "",
        "selected_model": str(install_state.get("selected_model") or selected_model),
        "engine_type": str(install_state.get("engine_type") or engine_type),
        "lang_type": str(install_state.get("lang_type") or lang_type),
        "model_type": str(install_state.get("model_type") or model_type),
        "ocr_version": str(install_state.get("ocr_version") or ocr_version),
        "detail": detail,
        "runtime_error": runtime_error,
    }
