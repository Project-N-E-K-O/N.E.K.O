from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

from ._paths import _BUNDLED_KEY, resolve_rapidocr_model_cache_dir


RAPIDOCR_PACKAGE_NAME = "rapidocr_onnxruntime"
DEFAULT_RAPIDOCR_ENGINE_TYPE = "onnxruntime"
# Default to the bundled Chinese PP-OCRv4 model so minimal/older configs take
# the no-download path. Japanese games can still opt into `japan` explicitly.
DEFAULT_RAPIDOCR_LANG_TYPE = "ch"
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
        existing RapidOCR support fixtures that came in with PR #1194).
    """
    lang = str(lang_type or DEFAULT_RAPIDOCR_LANG_TYPE).strip() or DEFAULT_RAPIDOCR_LANG_TYPE
    version = str(ocr_version or DEFAULT_RAPIDOCR_OCR_VERSION).strip() or DEFAULT_RAPIDOCR_OCR_VERSION
    mt = (str(model_type or DEFAULT_RAPIDOCR_MODEL_TYPE).strip() or DEFAULT_RAPIDOCR_MODEL_TYPE).lower()
    server_infix = "_server" if mt == "server" else ""
    type_suffix = "_server" if mt == "server" else "_mobile"

    # Consult the registry FIRST so cross-version fallbacks resolve correctly.
    # Example: ("PP-OCRv5", "japan") rec actually downloads as
    # `japan_PP-OCRv4_rec_mobile.onnx` (no v5 japan rec exists upstream); the
    # synthesized `f"{lang}_{version}_rec_*"` names below would never match.
    # The registry's `name` is the on-disk filename our downloader writes.
    registry = _registry_lookup(version, lang) or {}

    def _names(*items: str) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for item in items:
            if item and item not in seen:
                seen.add(item)
                out.append(item)
        return out

    def _alt_infer(name: str) -> str:
        """Wheel/Paddle pattern: same prefix but `_infer.onnx` instead of
        `_mobile.onnx` / `_server.onnx`. Lets us pick up wheel-bundled
        files even when the registry lists the modelscope `_mobile` name.
        Example: `ch_PP-OCRv4_det_mobile.onnx` ↔ `ch_PP-OCRv4_det_infer.onnx`.
        """
        for suf in ("_mobile.onnx", "_server.onnx"):
            if name.endswith(suf):
                return name[: -len(suf)] + "_infer.onnx"
        return ""

    reg_det = str((registry.get("det") or {}).get("name") or "")
    reg_rec = str((registry.get("rec") or {}).get("name") or "")
    reg_cls = str((registry.get("cls") or {}).get("name") or "")

    det_names = _names(
        reg_det,
        _alt_infer(reg_det),
        f"{lang}_{version}_det{server_infix}_infer.onnx",  # paddle / wheel
        f"{lang}_{version}_det{type_suffix}.onnx",          # modelscope v3.x
    )
    rec_names = _names(
        reg_rec,
        _alt_infer(reg_rec),
        f"{lang}_{version}_rec{server_infix}_infer.onnx",
        f"{lang}_{version}_rec{type_suffix}.onnx",
    )
    # Cls is shared across mobile/server variants. PaddleOCR ships the
    # legacy v2.0 mobile cls; PP-OCRv5 introduces a new textline-orientation
    # cls. Consult the registry first, then list the known generics.
    cls_names = _names(
        reg_cls,
        _alt_infer(reg_cls),
        "ch_ppocr_mobile_v2.0_cls_infer.onnx",
        "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        "ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx",
    )

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

def _normalize_model_key(ocr_version: str, lang_type: str) -> tuple[str, str]:
    return (
        str(ocr_version or DEFAULT_RAPIDOCR_OCR_VERSION).strip() or DEFAULT_RAPIDOCR_OCR_VERSION,
        str(lang_type or DEFAULT_RAPIDOCR_LANG_TYPE).strip() or DEFAULT_RAPIDOCR_LANG_TYPE,
    )


def _registry_lookup(ocr_version: str, lang_type: str) -> dict[str, dict[str, Any]] | None:
    """Return the (det, rec, cls) entries for a given selection, or None if not catalogued."""
    return _RAPIDOCR_MODEL_REGISTRY.get(_normalize_model_key(ocr_version, lang_type))


def rapidocr_selection_requires_downloaded_models(
    *,
    ocr_version: str,
    lang_type: str,
) -> bool:
    """Return True when the local registry expects explicit downloaded model files."""
    key = _normalize_model_key(ocr_version, lang_type)
    return key != _BUNDLED_KEY and key in _RAPIDOCR_MODEL_REGISTRY


def _registry_spec_for_model_type(
    spec: dict[str, Any],
    *,
    kind: str,
    model_type: str,
) -> dict[str, Any]:
    model_kind = (str(model_type or DEFAULT_RAPIDOCR_MODEL_TYPE).strip() or DEFAULT_RAPIDOCR_MODEL_TYPE).lower()
    if model_kind != "server" or kind == "cls":
        return dict(spec)
    name = str(spec.get("name") or "")
    url = str(spec.get("url") or "")
    if name.endswith("_mobile.onnx"):
        name = name[: -len("_mobile.onnx")] + "_server.onnx"
    if url.endswith("_mobile.onnx"):
        url = url[: -len("_mobile.onnx")] + "_server.onnx"
    return {
        **spec,
        "name": name,
        "url": url,
        "sha256": "",
    }


def required_rapidocr_model_files(
    *,
    install_target_dir_raw: str,
    ocr_version: str,
    lang_type: str,
    model_type: str = DEFAULT_RAPIDOCR_MODEL_TYPE,
    plugin_id: str,
) -> list[dict[str, Any]]:
    """Files that must exist on disk for a given selection. Empty for the bundled combo."""
    key = _normalize_model_key(ocr_version, lang_type)
    if key == _BUNDLED_KEY:
        return []
    registry = _RAPIDOCR_MODEL_REGISTRY.get(key)
    if not registry:
        return []
    cache_dir = resolve_rapidocr_model_cache_dir(
        install_target_dir_raw,
        plugin_id=plugin_id,
    )
    files: list[dict[str, Any]] = []
    for kind in ("det", "rec", "cls"):
        spec = registry.get(kind)
        if not spec:
            continue
        spec = _registry_spec_for_model_type(spec, kind=kind, model_type=model_type)
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
    model_type: str = DEFAULT_RAPIDOCR_MODEL_TYPE,
    plugin_id: str,
) -> list[dict[str, Any]]:
    """Required files that the resolver can't locate on disk.

    Delegates to `_resolve_rapidocr_model_paths` so we accept the same files
    RapidOCR will actually load: both filename conventions (`_infer.onnx` for
    the wheel/PaddleOCR pattern, `_mobile.onnx`/`_server.onnx` for ModelScope
    v3.x) and both locations (model_cache_dir + the imported package's
    bundled `models/` dir). Marking a stage missing only because the
    registry's preferred filename isn't at the exact target_path would have
    caused inspect_rapidocr_installation to keep returning
    `detail="missing_model_files"` even when RapidOCR could already serve
    OCR successfully from a wheel-bundled file or a manually-dropped
    alternate-name file — locking the user into a perpetual download banner.
    """
    required = required_rapidocr_model_files(
        install_target_dir_raw=install_target_dir_raw,
        ocr_version=ocr_version,
        lang_type=lang_type,
        model_type=model_type,
        plugin_id=plugin_id,
    )
    if not required:
        return []

    cache_dir = resolve_rapidocr_model_cache_dir(
        install_target_dir_raw,
        plugin_id=plugin_id,
    )
    # Two possible `<package>/models/` dirs to scan:
    # 1. The bundled-import path's models dir (find_spec → wheel models).
    # 2. The legacy plugin-isolated install's package dir, which sits at
    #    `<install_target>/runtime/site-packages/rapidocr_onnxruntime/models`
    #    and is loaded via `_rapidocr_import_context` rather than the normal
    #    Python import machinery — so `find_spec` returns None for it even
    #    when load_rapidocr_runtime can use it. Without this fallback,
    #    legacy-install users see a perpetual "missing models" banner even
    #    though their files are reachable.
    candidate_package_dirs: list[Path | None] = []
    try:
        spec = importlib.util.find_spec(RAPIDOCR_PACKAGE_NAME)
        if spec is not None and spec.origin:
            candidate_package_dirs.append(Path(spec.origin).resolve().parent / "models")
    except (ImportError, ValueError):
        pass
    from ._runtime import _rapidocr_package_dir
    legacy_pkg = _rapidocr_package_dir(install_target_dir_raw, plugin_id=plugin_id)
    if legacy_pkg and legacy_pkg.exists():
        candidate_package_dirs.append(legacy_pkg / "models")
    if not candidate_package_dirs:
        candidate_package_dirs.append(None)

    # "Any candidate dir resolves a stage" → that stage isn't missing.
    found_by_kind: dict[str, str | None] = {"det": None, "cls": None, "rec": None}
    for pkg_dir in candidate_package_dirs:
        det_path, cls_path, rec_path = _resolve_rapidocr_model_paths(
            model_cache_dir=cache_dir,
            package_models_dir=pkg_dir,
            lang_type=lang_type,
            ocr_version=ocr_version,
            model_type=model_type,
        )
        found_by_kind["det"] = found_by_kind["det"] or det_path
        found_by_kind["cls"] = found_by_kind["cls"] or cls_path
        found_by_kind["rec"] = found_by_kind["rec"] or rec_path
        if all(found_by_kind.values()):
            break
    return [
        item for item in required
        if not found_by_kind.get(item["kind"])
    ]
