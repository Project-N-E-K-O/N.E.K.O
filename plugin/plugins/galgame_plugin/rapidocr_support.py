from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import json
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterator

import httpx

from utils.config_manager import get_config_manager

from .memory_reader import is_windows_platform


RAPIDOCR_PACKAGE_NAME = "rapidocr_onnxruntime"
DEFAULT_RAPIDOCR_ENGINE_TYPE = "onnxruntime"
# galgame_plugin's primary use case is Japanese visual novels — `japan` rec
# is the right product default. The bundled wheel ships only ch+v4 models, so
# the first run with `japan` will land on the `missing_model_files` flow and
# offer the user an explicit, opt-in download (see download_rapidocr_models).
DEFAULT_RAPIDOCR_LANG_TYPE = "japan"
DEFAULT_RAPIDOCR_MODEL_TYPE = "mobile"
# PP-OCRv4 keeps the bundled-no-download path working for ch+v4. v5 has
# better quality but requires a download for everything (no bundled v5).
DEFAULT_RAPIDOCR_OCR_VERSION = "PP-OCRv4"

# Models hosted on RapidAI's ModelScope mirror. URL pattern stable as of
# RapidOCR v3.8.0 (the registry source). Each entry's `name` is the on-disk
# filename (also forms the URL leaf) and is what we pass to RapidOCR via
# det_model_path / rec_model_path / cls_model_path. SHA256 is from the
# upstream default_models.yaml — used for integrity checks after download.
_RAPIDOCR_MODELSCOPE_BASE = (
    "https://www.modelscope.cn/models/RapidAI/RapidOCR/resolve/v3.8.0/onnx"
)


def _ms_url(version: str, kind: str, name: str) -> str:
    return f"{_RAPIDOCR_MODELSCOPE_BASE}/{version}/{kind}/{name}"


# (ocr_version, lang_type) -> {det/rec/cls: {name, url, sha256, size}}.
# `det` is largely language-agnostic (we use ch det for all langs); `cls` is
# orientation-only. Only `rec` truly varies by lang. PP-OCRv5 has no japan
# rec model upstream, so we fall back to the v4 japan rec — det/cls stay v5.
_RAPIDOCR_MODEL_REGISTRY: dict[tuple[str, str], dict[str, dict[str, Any]]] = {
    ("PP-OCRv4", "ch"): {
        "det": {"name": "ch_PP-OCRv4_det_mobile.onnx", "url": _ms_url("PP-OCRv4", "det", "ch_PP-OCRv4_det_mobile.onnx"), "sha256": "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9", "size": 4_700_000},
        "rec": {"name": "ch_PP-OCRv4_rec_mobile.onnx", "url": _ms_url("PP-OCRv4", "rec", "ch_PP-OCRv4_rec_mobile.onnx"), "sha256": "48fc40f24f6d2a207a2b1091d3437eb3cc3eb6b676dc3ef9c37384005483683b", "size": 10_700_000},
        "cls": {"name": "ch_ppocr_mobile_v2.0_cls_mobile.onnx", "url": _ms_url("PP-OCRv4", "cls", "ch_ppocr_mobile_v2.0_cls_mobile.onnx"), "sha256": "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c", "size": 580_000},
    },
    ("PP-OCRv4", "japan"): {
        "det": {"name": "ch_PP-OCRv4_det_mobile.onnx", "url": _ms_url("PP-OCRv4", "det", "ch_PP-OCRv4_det_mobile.onnx"), "sha256": "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9", "size": 4_700_000},
        "rec": {"name": "japan_PP-OCRv4_rec_mobile.onnx", "url": _ms_url("PP-OCRv4", "rec", "japan_PP-OCRv4_rec_mobile.onnx"), "sha256": "e1075a67dba758ecfc7ebc78a10ae61c95ac8fb66a9c86fab5541e33f085cb7a", "size": 9_753_335},
        "cls": {"name": "ch_ppocr_mobile_v2.0_cls_mobile.onnx", "url": _ms_url("PP-OCRv4", "cls", "ch_ppocr_mobile_v2.0_cls_mobile.onnx"), "sha256": "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c", "size": 580_000},
    },
    ("PP-OCRv4", "korean"): {
        "det": {"name": "ch_PP-OCRv4_det_mobile.onnx", "url": _ms_url("PP-OCRv4", "det", "ch_PP-OCRv4_det_mobile.onnx"), "sha256": "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9", "size": 4_700_000},
        "rec": {"name": "korean_PP-OCRv4_rec_mobile.onnx", "url": _ms_url("PP-OCRv4", "rec", "korean_PP-OCRv4_rec_mobile.onnx"), "sha256": "ab151ba9065eccd98f884cf4d927db091be86137276392072edd4f9d43ad7426", "size": 9_500_000},
        "cls": {"name": "ch_ppocr_mobile_v2.0_cls_mobile.onnx", "url": _ms_url("PP-OCRv4", "cls", "ch_ppocr_mobile_v2.0_cls_mobile.onnx"), "sha256": "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c", "size": 580_000},
    },
    ("PP-OCRv4", "en"): {
        "det": {"name": "ch_PP-OCRv4_det_mobile.onnx", "url": _ms_url("PP-OCRv4", "det", "ch_PP-OCRv4_det_mobile.onnx"), "sha256": "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9", "size": 4_700_000},
        "rec": {"name": "en_PP-OCRv4_rec_mobile.onnx", "url": _ms_url("PP-OCRv4", "rec", "en_PP-OCRv4_rec_mobile.onnx"), "sha256": "e8770c967605983d1570cdf5352041dfb68fa0c21664f49f47b155abd3e0e318", "size": 9_500_000},
        "cls": {"name": "ch_ppocr_mobile_v2.0_cls_mobile.onnx", "url": _ms_url("PP-OCRv4", "cls", "ch_ppocr_mobile_v2.0_cls_mobile.onnx"), "sha256": "e47acedf663230f8863ff1ab0e64dd2d82b838fceb5957146dab185a89d6215c", "size": 580_000},
    },
    ("PP-OCRv5", "ch"): {
        "det": {"name": "ch_PP-OCRv5_det_mobile.onnx", "url": _ms_url("PP-OCRv5", "det", "ch_PP-OCRv5_det_mobile.onnx"), "sha256": "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae", "size": 5_000_000},
        "rec": {"name": "ch_PP-OCRv5_rec_mobile.onnx", "url": _ms_url("PP-OCRv5", "rec", "ch_PP-OCRv5_rec_mobile.onnx"), "sha256": "5825fc7ebf84ae7a412be049820b4d86d77620f204a041697b0494669b1742c5", "size": 11_500_000},
        "cls": {"name": "ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx", "url": _ms_url("PP-OCRv5", "cls", "ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx"), "sha256": "54379ae5174d026780215fc748a7f31910dee36818e63d49e17dc598ecc82df7", "size": 600_000},
    },
    # PP-OCRv5 has no `japan` rec model upstream — fall back to v4 japan rec.
    # Det + cls stay on v5; mixing release lines is supported by RapidOCR's
    # per-stage model_path config.
    ("PP-OCRv5", "japan"): {
        "det": {"name": "ch_PP-OCRv5_det_mobile.onnx", "url": _ms_url("PP-OCRv5", "det", "ch_PP-OCRv5_det_mobile.onnx"), "sha256": "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae", "size": 5_000_000},
        "rec": {"name": "japan_PP-OCRv4_rec_mobile.onnx", "url": _ms_url("PP-OCRv4", "rec", "japan_PP-OCRv4_rec_mobile.onnx"), "sha256": "e1075a67dba758ecfc7ebc78a10ae61c95ac8fb66a9c86fab5541e33f085cb7a", "size": 9_753_335},
        "cls": {"name": "ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx", "url": _ms_url("PP-OCRv5", "cls", "ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx"), "sha256": "54379ae5174d026780215fc748a7f31910dee36818e63d49e17dc598ecc82df7", "size": 600_000},
    },
    ("PP-OCRv5", "korean"): {
        "det": {"name": "ch_PP-OCRv5_det_mobile.onnx", "url": _ms_url("PP-OCRv5", "det", "ch_PP-OCRv5_det_mobile.onnx"), "sha256": "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae", "size": 5_000_000},
        "rec": {"name": "korean_PP-OCRv5_rec_mobile.onnx", "url": _ms_url("PP-OCRv5", "rec", "korean_PP-OCRv5_rec_mobile.onnx"), "sha256": "cd6e2ea50f6943ca7271eb8c56a877a5a90720b7047fe9c41a2e541a25773c9b", "size": 10_000_000},
        "cls": {"name": "ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx", "url": _ms_url("PP-OCRv5", "cls", "ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx"), "sha256": "54379ae5174d026780215fc748a7f31910dee36818e63d49e17dc598ecc82df7", "size": 600_000},
    },
    ("PP-OCRv5", "en"): {
        "det": {"name": "ch_PP-OCRv5_det_mobile.onnx", "url": _ms_url("PP-OCRv5", "det", "ch_PP-OCRv5_det_mobile.onnx"), "sha256": "4d97c44a20d30a81aad087d6a396b08f786c4635742afc391f6621f5c6ae78ae", "size": 5_000_000},
        "rec": {"name": "en_PP-OCRv5_rec_mobile.onnx", "url": _ms_url("PP-OCRv5", "rec", "en_PP-OCRv5_rec_mobile.onnx"), "sha256": "c3461add59bb4323ecba96a492ab75e06dda42467c9e3d0c18db5d1d21924be8", "size": 10_000_000},
        "cls": {"name": "ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx", "url": _ms_url("PP-OCRv5", "cls", "ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx"), "sha256": "54379ae5174d026780215fc748a7f31910dee36818e63d49e17dc598ecc82df7", "size": 600_000},
    },
}

# Bundled ch+PP-OCRv4 models that ship inside the rapidocr_onnxruntime wheel.
# When the user's selection matches this combo, no download is needed and we
# pass no model paths — the runtime falls through to its packaged config.
_BUNDLED_KEY: tuple[str, str] = ("PP-OCRv4", "ch")
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


def _resolve_rapidocr_model_paths(
    *,
    model_cache_dir: Path,
    package_models_dir: Path | None,
    lang_type: str,
    ocr_version: str,
    model_type: str,
) -> tuple[str | None, str | None, str | None]:
    """Find det/cls/rec ONNX files on disk for a given (lang, version, type).

    Two filename conventions in the wild:
      - PaddleOCR / wheel-bundled: f"{lang}_{version}_{stage}{_server?}_infer.onnx"
        e.g. ch_PP-OCRv4_det_infer.onnx, ch_PP-OCRv4_det_server_infer.onnx
      - RapidAI ModelScope releases (v3.x): f"{lang}_{version}_{stage}_{type}.onnx"
        e.g. ch_PP-OCRv4_det_mobile.onnx, ch_PP-OCRv4_det_server.onnx
        (no `_infer` suffix; type is `_mobile` or `_server`)

    Both conventions are checked per location to support either source. The
    `_infer` form is preferred (matches both bundled wheels and the
    test_galgame_rapidocr_support fixtures that came in with PR #1194).
    """
    lang = str(lang_type or DEFAULT_RAPIDOCR_LANG_TYPE).strip() or DEFAULT_RAPIDOCR_LANG_TYPE
    version = str(ocr_version or DEFAULT_RAPIDOCR_OCR_VERSION).strip() or DEFAULT_RAPIDOCR_OCR_VERSION
    mt = (str(model_type or DEFAULT_RAPIDOCR_MODEL_TYPE).strip() or DEFAULT_RAPIDOCR_MODEL_TYPE).lower()
    server_infix = "_server" if mt == "server" else ""
    type_suffix = "_server" if mt == "server" else "_mobile"

    det_names = [
        f"{lang}_{version}_det{server_infix}_infer.onnx",  # paddle / wheel
        f"{lang}_{version}_det{type_suffix}.onnx",          # modelscope v3.x
    ]
    rec_names = [
        f"{lang}_{version}_rec{server_infix}_infer.onnx",
        f"{lang}_{version}_rec{type_suffix}.onnx",
    ]
    # Cls is shared across mobile/server variants. PaddleOCR ships the
    # legacy v2.0 mobile cls; PP-OCRv5 introduces a new textline-orientation
    # cls. List both — first match wins.
    cls_names = [
        "ch_ppocr_mobile_v2.0_cls_infer.onnx",
        "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx",
    ]

    def _find_first(search_dir: Path, names: list[str]) -> str | None:
        for name in names:
            candidate = search_dir / name
            if candidate.is_file():
                return str(candidate)
        return None

    det_path: str | None = None
    cls_path: str | None = None
    rec_path: str | None = None
    for search_dir in (model_cache_dir, package_models_dir):
        if not search_dir or not search_dir.is_dir():
            continue
        if det_path is None:
            det_path = _find_first(search_dir, det_names)
        if cls_path is None:
            cls_path = _find_first(search_dir, cls_names)
        if rec_path is None:
            rec_path = _find_first(search_dir, rec_names)
    return det_path, cls_path, rec_path


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


def _normalize_model_key(ocr_version: str, lang_type: str) -> tuple[str, str]:
    return (
        str(ocr_version or DEFAULT_RAPIDOCR_OCR_VERSION).strip() or DEFAULT_RAPIDOCR_OCR_VERSION,
        str(lang_type or DEFAULT_RAPIDOCR_LANG_TYPE).strip() or DEFAULT_RAPIDOCR_LANG_TYPE,
    )


def _registry_lookup(ocr_version: str, lang_type: str) -> dict[str, dict[str, Any]] | None:
    """Return the (det, rec, cls) entries for a given selection, or None if not catalogued."""
    return _RAPIDOCR_MODEL_REGISTRY.get(_normalize_model_key(ocr_version, lang_type))


def required_rapidocr_model_files(
    *,
    install_target_dir_raw: str,
    ocr_version: str,
    lang_type: str,
) -> list[dict[str, Any]]:
    """Files that must exist on disk for a given selection. Empty for the bundled combo."""
    key = _normalize_model_key(ocr_version, lang_type)
    if key == _BUNDLED_KEY:
        return []
    registry = _RAPIDOCR_MODEL_REGISTRY.get(key)
    if not registry:
        return []
    cache_dir = resolve_rapidocr_model_cache_dir(install_target_dir_raw)
    files: list[dict[str, Any]] = []
    for kind in ("det", "rec", "cls"):
        spec = registry.get(kind)
        if not spec:
            continue
        files.append({
            "kind": kind,
            "name": str(spec["name"]),
            "url": str(spec["url"]),
            "sha256": str(spec.get("sha256") or ""),
            "size": int(spec.get("size") or 0),
            "target_path": str(cache_dir / spec["name"]) if cache_dir else "",
        })
    return files


def missing_rapidocr_model_files(
    *,
    install_target_dir_raw: str,
    ocr_version: str,
    lang_type: str,
) -> list[dict[str, Any]]:
    """Required files that are NOT yet on disk."""
    return [
        spec for spec in required_rapidocr_model_files(
            install_target_dir_raw=install_target_dir_raw,
            ocr_version=ocr_version,
            lang_type=lang_type,
        )
        if not (spec["target_path"] and Path(spec["target_path"]).is_file())
    ]


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

    if not supported:
        detail = "unsupported_platform"
    elif bundled_spec is not None:
        # Bundled main-program path: trust find_spec instead of constructing a
        # full RapidOCR runtime (which inits an ONNX session) on every status
        # probe. inspect_*_installation gets called from the bridge poll on a
        # short cache TTL — running ORT init repeatedly would hammer CPU even
        # when OCR is disabled. Real OCR errors will still surface from
        # OcrReaderManager when capture/recognition is actually attempted.
        detail = "installed"
        spec_origin = getattr(bundled_spec, "origin", None) or ""
        if spec_origin:
            detected_path = str(Path(spec_origin).resolve().parent)
        # Non-bundled (ocr_version, lang_type) combos require additional
        # ONNX files that the wheel doesn't ship. Surface that as its own
        # state so the UI can offer an explicit, opt-in download instead
        # of "installed but silently broken at first capture".
        missing = missing_rapidocr_model_files(
            install_target_dir_raw=install_target_dir_raw,
            ocr_version=ocr_version,
            lang_type=lang_type,
        )
        if missing:
            detail = "missing_model_files"
    elif not package_dir.exists():
        detail = "missing"
    else:
        # Legacy plugin-isolated install: still validated by full runtime load
        # since this path is for upgrade users with potentially-stale installs
        # that may legitimately be broken. Frequency is low (only when bundled
        # path is unavailable AND legacy dir exists).
        try:
            _runtime, runtime_meta = load_rapidocr_runtime(
                install_target_dir_raw=install_target_dir_raw,
                engine_type=engine_type,
                lang_type=lang_type,
                model_type=model_type,
                ocr_version=ocr_version,
            )
            detected_path = str(runtime_meta.get("detected_path") or detected_path)
            detail = "installed"
        except Exception as exc:
            detail = "broken_runtime"
            runtime_error = str(exc)

    installed = detail == "installed"
    missing_files = missing_rapidocr_model_files(
        install_target_dir_raw=install_target_dir_raw,
        ocr_version=ocr_version,
        lang_type=lang_type,
    )
    total_size_estimate = sum(int(f.get("size") or 0) for f in missing_files)
    return {
        "install_supported": supported,
        "installed": installed,
        # rapidocr-onnxruntime is now bundled into the main program (see
        # pyproject.toml [dependency-groups] galgame). When it's not importable
        # the user is on a source install without `uv sync --group galgame` —
        # no in-app install action exists anymore (HTTP routes removed in this
        # refactor), so `can_install` stays False to keep the UI button hidden.
        "can_install": False,
        # `can_download_models` is True only when the package is present but
        # the user-selected language pack isn't on disk yet — that's the only
        # condition under which the download UX is meaningful.
        "can_download_models": detail == "missing_model_files",
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
        "missing_model_files": missing_files,
        "missing_model_total_size": total_size_estimate,
        "model_download_source": _RAPIDOCR_MODELSCOPE_BASE,
    }


# ====== Model download ======

ProgressCallback = Callable[[dict[str, Any]], Awaitable[None] | None]


async def _emit_model_progress(
    progress_callback: ProgressCallback | None,
    payload: dict[str, Any],
) -> None:
    if progress_callback is None:
        return
    maybe = progress_callback(dict(payload))
    if inspect.isawaitable(maybe):
        await maybe


def _verify_model_sha256(path: Path, expected_sha256: str) -> None:
    expected = (expected_sha256 or "").strip().lower()
    if not expected:
        return
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(chunk)
    actual = hasher.hexdigest()
    if actual != expected:
        path.unlink(missing_ok=True)
        raise RuntimeError(
            f"downloaded model checksum mismatch for {path.name}: expected {expected}, got {actual}"
        )


async def download_rapidocr_models(
    *,
    logger,
    install_target_dir_raw: str,
    ocr_version: str,
    lang_type: str,
    timeout_seconds: float = 180.0,
    force: bool = False,
    task_id: str | None = None,
    progress_callback: ProgressCallback | None = None,
) -> dict[str, Any]:
    """Download all model files required for the (ocr_version, lang_type) selection.

    Bundled (PP-OCRv4 + ch) is a no-op. Otherwise downloads each missing file
    from ModelScope into model_cache_dir, verifies SHA256, emits progress.
    Failures preserve specific error text (HTTP status, timeout, network) so
    the UI can show actionable copy.
    """
    from .install_tasks import update_install_task_state  # local import: avoid cycle

    cache_dir = resolve_rapidocr_model_cache_dir(install_target_dir_raw)
    if not cache_dir:
        raise RuntimeError("missing RapidOCR model cache directory")
    cache_dir.mkdir(parents=True, exist_ok=True)

    required = required_rapidocr_model_files(
        install_target_dir_raw=install_target_dir_raw,
        ocr_version=ocr_version,
        lang_type=lang_type,
    )
    if not required:
        if task_id:
            update_install_task_state(
                task_id,
                kind="rapidocr_models",
                status="completed",
                phase="completed",
                message="No download needed for bundled ch + PP-OCRv4 models",
                progress=1.0,
                target_dir=str(cache_dir),
            )
        await _emit_model_progress(
            progress_callback,
            {
                "status": "completed",
                "phase": "completed",
                "message": "No download needed for bundled ch + PP-OCRv4 models",
                "progress": 1.0,
                "downloaded_bytes": 0,
                "total_bytes": 0,
                "target_dir": str(cache_dir),
            },
        )
        return {"downloaded": [], "skipped_bundled": True, "target_dir": str(cache_dir)}

    pending = required if force else [
        spec for spec in required
        if not (spec["target_path"] and Path(spec["target_path"]).is_file())
    ]
    total_bytes = sum(int(spec.get("size") or 0) for spec in pending)

    if not pending:
        if task_id:
            update_install_task_state(
                task_id,
                kind="rapidocr_models",
                status="completed",
                phase="completed",
                message="All required RapidOCR models already on disk",
                progress=1.0,
                target_dir=str(cache_dir),
            )
        return {"downloaded": [], "already_present": True, "target_dir": str(cache_dir)}

    downloaded_bytes = 0
    downloaded: list[str] = []
    async with httpx.AsyncClient(
        timeout=timeout_seconds,
        trust_env=True,
        follow_redirects=True,
    ) as client:
        for index, spec in enumerate(pending):
            asset_name = spec["name"]
            destination = Path(spec["target_path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            running_message = f"Downloading {asset_name} ({index + 1}/{len(pending)})"
            if task_id:
                update_install_task_state(
                    task_id,
                    kind="rapidocr_models",
                    status="running",
                    phase="downloading",
                    message=running_message,
                    progress=(downloaded_bytes / total_bytes) if total_bytes else 0.0,
                    downloaded_bytes=downloaded_bytes,
                    total_bytes=total_bytes,
                    target_dir=str(cache_dir),
                    asset_name=asset_name,
                )
            await _emit_model_progress(
                progress_callback,
                {
                    "status": "running",
                    "phase": "downloading",
                    "message": running_message,
                    "progress": (downloaded_bytes / total_bytes) if total_bytes else 0.0,
                    "downloaded_bytes": downloaded_bytes,
                    "total_bytes": total_bytes,
                    "target_dir": str(cache_dir),
                    "asset_name": asset_name,
                },
            )

            tmp_path = destination.with_suffix(destination.suffix + ".part")
            try:
                async with client.stream(
                    "GET",
                    spec["url"],
                    headers={
                        "Accept": "application/octet-stream",
                        "User-Agent": "N.E.K.O/galgame_plugin",
                    },
                ) as response:
                    response.raise_for_status()
                    asset_total = int(response.headers.get("Content-Length") or spec.get("size") or 0)
                    asset_downloaded = 0
                    last_emit = 0.0
                    with tmp_path.open("wb") as fh:
                        async for chunk in response.aiter_bytes(chunk_size=64 * 1024):
                            if not chunk:
                                continue
                            fh.write(chunk)
                            asset_downloaded += len(chunk)
                            now = downloaded_bytes + asset_downloaded
                            # Throttle progress emission to ~1% steps to keep
                            # the SSE stream cheap.
                            if total_bytes and (now - last_emit) > max(64 * 1024, total_bytes // 100):
                                last_emit = float(now)
                                if task_id:
                                    update_install_task_state(
                                        task_id,
                                        kind="rapidocr_models",
                                        status="running",
                                        phase="downloading",
                                        message=running_message,
                                        progress=(now / total_bytes) if total_bytes else 0.0,
                                        downloaded_bytes=now,
                                        total_bytes=total_bytes,
                                        target_dir=str(cache_dir),
                                        asset_name=asset_name,
                                    )
                                await _emit_model_progress(
                                    progress_callback,
                                    {
                                        "status": "running",
                                        "phase": "downloading",
                                        "message": running_message,
                                        "progress": (now / total_bytes) if total_bytes else 0.0,
                                        "downloaded_bytes": now,
                                        "total_bytes": total_bytes,
                                        "target_dir": str(cache_dir),
                                        "asset_name": asset_name,
                                    },
                                )
                _verify_model_sha256(tmp_path, str(spec.get("sha256") or ""))
                if destination.exists():
                    destination.unlink()
                tmp_path.replace(destination)
                downloaded_bytes += int(spec.get("size") or asset_downloaded)
                downloaded.append(asset_name)
            except httpx.HTTPError as exc:
                tmp_path.unlink(missing_ok=True)
                raise RuntimeError(
                    f"failed to download {asset_name}: {type(exc).__name__}: {exc}"
                ) from exc
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise

    if task_id:
        update_install_task_state(
            task_id,
            kind="rapidocr_models",
            status="completed",
            phase="completed",
            message=f"Downloaded {len(downloaded)} model file(s)",
            progress=1.0,
            downloaded_bytes=total_bytes,
            total_bytes=total_bytes,
            target_dir=str(cache_dir),
        )
    await _emit_model_progress(
        progress_callback,
        {
            "status": "completed",
            "phase": "completed",
            "message": f"Downloaded {len(downloaded)} model file(s)",
            "progress": 1.0,
            "downloaded_bytes": total_bytes,
            "total_bytes": total_bytes,
            "target_dir": str(cache_dir),
        },
    )
    return {"downloaded": downloaded, "target_dir": str(cache_dir)}
