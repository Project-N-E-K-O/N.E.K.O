from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.plugins.galgame_plugin import rapidocr_support


pytestmark = pytest.mark.plugin_unit


class _RapidOcrWithKwargs:
    captured_kwargs: dict[str, object] | None = None

    def __init__(self, config_path=None, **kwargs) -> None:
        del config_path
        type(self).captured_kwargs = dict(kwargs)


def _touch(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    return path


def test_rapidocr_kwargs_resolve_configured_model_paths(tmp_path: Path) -> None:
    model_cache_dir = tmp_path / "RapidOCR" / "models"
    package_models_dir = tmp_path / "package" / "models"
    det_path = _touch(package_models_dir / "ch_PP-OCRv4_det_infer.onnx")
    cls_path = _touch(package_models_dir / "ch_ppocr_mobile_v2.0_cls_infer.onnx")
    rec_path = _touch(package_models_dir / "ch_PP-OCRv4_rec_infer.onnx")

    kwargs = rapidocr_support._build_runtime_constructor_kwargs(
        _RapidOcrWithKwargs,
        engine_type="onnxruntime",
        lang_type="ch",
        model_type="mobile",
        ocr_version="PP-OCRv4",
        model_cache_dir=model_cache_dir,
        package_models_dir=package_models_dir,
    )

    assert kwargs == {
        "det_model_path": str(det_path),
        "cls_model_path": str(cls_path),
        "rec_model_path": str(rec_path),
        "engine_type": "onnxruntime",
    }


def test_rapidocr_kwargs_prefers_user_model_cache(tmp_path: Path) -> None:
    model_cache_dir = tmp_path / "RapidOCR" / "models"
    package_models_dir = tmp_path / "package" / "models"
    user_det_path = _touch(model_cache_dir / "japan_PP-OCRv4_det_infer.onnx")
    user_rec_path = _touch(model_cache_dir / "japan_PP-OCRv4_rec_infer.onnx")
    package_cls_path = _touch(package_models_dir / "ch_ppocr_mobile_v2.0_cls_infer.onnx")
    _touch(package_models_dir / "japan_PP-OCRv4_det_infer.onnx")
    _touch(package_models_dir / "japan_PP-OCRv4_rec_infer.onnx")

    kwargs = rapidocr_support._build_runtime_constructor_kwargs(
        _RapidOcrWithKwargs,
        engine_type="onnxruntime",
        lang_type="japan",
        model_type="mobile",
        ocr_version="PP-OCRv4",
        model_cache_dir=model_cache_dir,
        package_models_dir=package_models_dir,
    )

    assert kwargs["det_model_path"] == str(user_det_path)
    assert kwargs["rec_model_path"] == str(user_rec_path)
    assert kwargs["cls_model_path"] == str(package_cls_path)


def test_rapidocr_kwargs_resolves_server_variant_filenames(tmp_path: Path) -> None:
    model_cache_dir = tmp_path / "RapidOCR" / "models"
    package_models_dir = tmp_path / "package" / "models"
    server_det_path = _touch(model_cache_dir / "ch_PP-OCRv4_server_det_infer.onnx")
    server_rec_path = _touch(model_cache_dir / "ch_PP-OCRv4_server_rec_infer.onnx")
    cls_path = _touch(package_models_dir / "ch_ppocr_mobile_v2.0_cls_infer.onnx")
    # Mobile variants exist alongside server ones to ensure model_type drives selection.
    _touch(package_models_dir / "ch_PP-OCRv4_det_infer.onnx")
    _touch(package_models_dir / "ch_PP-OCRv4_rec_infer.onnx")

    kwargs = rapidocr_support._build_runtime_constructor_kwargs(
        _RapidOcrWithKwargs,
        engine_type="onnxruntime",
        lang_type="ch",
        model_type="server",
        ocr_version="PP-OCRv4",
        model_cache_dir=model_cache_dir,
        package_models_dir=package_models_dir,
    )

    assert kwargs == {
        "det_model_path": str(server_det_path),
        "rec_model_path": str(server_rec_path),
        "cls_model_path": str(cls_path),
        "engine_type": "onnxruntime",
    }


def test_rapidocr_kwargs_omits_model_paths_when_configured_model_is_missing(tmp_path: Path) -> None:
    model_cache_dir = tmp_path / "RapidOCR" / "models"
    package_models_dir = tmp_path / "package" / "models"
    _touch(package_models_dir / "ch_PP-OCRv4_det_infer.onnx")
    _touch(package_models_dir / "ch_ppocr_mobile_v2.0_cls_infer.onnx")
    _touch(package_models_dir / "ch_PP-OCRv4_rec_infer.onnx")

    kwargs = rapidocr_support._build_runtime_constructor_kwargs(
        _RapidOcrWithKwargs,
        engine_type="onnxruntime",
        lang_type="ch",
        model_type="mobile",
        ocr_version="PP-OCRv5",
        model_cache_dir=model_cache_dir,
        package_models_dir=package_models_dir,
    )

    assert kwargs == {"engine_type": "onnxruntime"}


def test_load_rapidocr_runtime_uses_imported_package_models_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_target = tmp_path / "RapidOCR"
    bundled_package_dir = tmp_path / "bundled" / "rapidocr_onnxruntime"
    _touch(bundled_package_dir / "__init__.py")
    det_path = _touch(bundled_package_dir / "models" / "ch_PP-OCRv4_det_infer.onnx")
    cls_path = _touch(bundled_package_dir / "models" / "ch_ppocr_mobile_v2.0_cls_infer.onnx")
    rec_path = _touch(bundled_package_dir / "models" / "ch_PP-OCRv4_rec_infer.onnx")
    _RapidOcrWithKwargs.captured_kwargs = None

    monkeypatch.setattr(
        rapidocr_support.importlib,
        "import_module",
        lambda name: SimpleNamespace(
            RapidOCR=_RapidOcrWithKwargs,
            __file__=str(bundled_package_dir / "__init__.py"),
        ),
    )
    monkeypatch.setattr(rapidocr_support, "_onnxruntime_intra_op_thread_cap", lambda _limit: nullcontext())

    runtime, metadata = rapidocr_support.load_rapidocr_runtime(
        install_target_dir_raw=str(install_target),
        engine_type="onnxruntime",
        lang_type="ch",
        model_type="mobile",
        ocr_version="PP-OCRv4",
    )

    assert isinstance(runtime, _RapidOcrWithKwargs)
    assert _RapidOcrWithKwargs.captured_kwargs == {
        "det_model_path": str(det_path),
        "cls_model_path": str(cls_path),
        "rec_model_path": str(rec_path),
        "engine_type": "onnxruntime",
    }
    assert metadata["detected_path"] == str(bundled_package_dir.resolve())
    assert metadata["selected_model"] == "PP-OCRv4/ch/mobile"
