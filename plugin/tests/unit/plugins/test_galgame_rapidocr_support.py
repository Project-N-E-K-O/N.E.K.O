from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path
import threading
from types import SimpleNamespace

import httpx
import pytest

from plugin.plugins._shared.rapidocr import ocr_rapidocr_backend, rapidocr_support
from plugin.plugins._shared.rapidocr.ocr_rapidocr_backend import RapidOcrBackend
from plugin.plugins._shared.rapidocr.ocr_runtime_types import (
    _RAPIDOCR_RUNTIME_CACHE,
    _rapidocr_runtime_cache_key,
)


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


def test_shared_rapidocr_runtime_cache_key_includes_plugin_id() -> None:
    study_key = _rapidocr_runtime_cache_key(
        install_target_dir_raw=" C:/RapidOCR ",
        engine_type="ONNXRUNTIME",
        lang_type="CH",
        model_type="Mobile",
        ocr_version=" PP-OCRv5 ",
        plugin_id="study_companion",
    )
    other_key = _rapidocr_runtime_cache_key(
        install_target_dir_raw=" C:/RapidOCR ",
        engine_type="ONNXRUNTIME",
        lang_type="CH",
        model_type="Mobile",
        ocr_version=" PP-OCRv5 ",
        plugin_id="other_plugin",
    )

    assert study_key == (
        "study_companion",
        "C:/RapidOCR",
        "onnxruntime",
        "ch",
        "mobile",
        "PP-OCRv5",
    )
    assert other_key != study_key


def test_shared_rapidocr_backend_close_releases_plugin_scoped_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = object()
    install_target_dir = str(tmp_path / "RapidOCR")
    monkeypatch.setattr(
        "plugin.plugins._shared.rapidocr.ocr_rapidocr_backend.load_rapidocr_runtime",
        lambda **_kwargs: (runtime, {}),
    )
    backend = RapidOcrBackend(
        install_target_dir_raw=install_target_dir,
        engine_type="onnxruntime",
        lang_type="ch",
        model_type="mobile",
        ocr_version="PP-OCRv5",
        plugin_id="study_companion",
    )
    second = RapidOcrBackend(
        install_target_dir_raw=install_target_dir,
        engine_type="onnxruntime",
        lang_type="ch",
        model_type="mobile",
        ocr_version="PP-OCRv5",
        plugin_id="study_companion",
    )
    key = _rapidocr_runtime_cache_key(
        install_target_dir_raw=install_target_dir,
        engine_type="onnxruntime",
        lang_type="ch",
        model_type="mobile",
        ocr_version="PP-OCRv5",
        plugin_id="study_companion",
    )

    assert backend._ensure_runtime() is runtime
    assert second._ensure_runtime() is runtime
    assert _RAPIDOCR_RUNTIME_CACHE[key][0] is runtime

    backend.close()

    assert _RAPIDOCR_RUNTIME_CACHE[key][0] is runtime

    second.close()

    assert key not in _RAPIDOCR_RUNTIME_CACHE


def test_shared_rapidocr_cache_acquire_is_atomic_with_lookup(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = object()
    install_target_dir = str(tmp_path / "RapidOCR")
    monkeypatch.setattr(
        ocr_rapidocr_backend,
        "load_rapidocr_runtime",
        lambda **_kwargs: (runtime, {}),
    )
    first = RapidOcrBackend(
        install_target_dir_raw=install_target_dir,
        engine_type="onnxruntime",
        lang_type="ch",
        model_type="mobile",
        ocr_version="PP-OCRv5",
        plugin_id="study_companion",
    )
    second = RapidOcrBackend(
        install_target_dir_raw=install_target_dir,
        engine_type="onnxruntime",
        lang_type="ch",
        model_type="mobile",
        ocr_version="PP-OCRv5",
        plugin_id="study_companion",
    )
    key = _rapidocr_runtime_cache_key(
        install_target_dir_raw=install_target_dir,
        engine_type="onnxruntime",
        lang_type="ch",
        model_type="mobile",
        ocr_version="PP-OCRv5",
        plugin_id="study_companion",
    )
    assert first._ensure_runtime() is runtime

    acquire_started = threading.Event()
    release_acquire = threading.Event()
    close_completed = threading.Event()
    result: list[object] = []
    real_acquire = ocr_rapidocr_backend._acquire_rapidocr_runtime_cache

    def _blocking_acquire(cache_key) -> None:
        acquire_started.set()
        assert release_acquire.wait(timeout=2.0)
        real_acquire(cache_key)

    monkeypatch.setattr(
        ocr_rapidocr_backend,
        "_acquire_rapidocr_runtime_cache",
        _blocking_acquire,
    )
    ensure_thread = threading.Thread(target=lambda: result.append(second._ensure_runtime()))

    def _close_first() -> None:
        first.close()
        close_completed.set()

    close_thread = threading.Thread(target=_close_first)
    try:
        ensure_thread.start()
        assert acquire_started.wait(timeout=2.0)
        close_thread.start()
        assert not close_completed.wait(timeout=0.2)
        release_acquire.set()
        ensure_thread.join(timeout=2.0)
        close_thread.join(timeout=2.0)

        assert result == [runtime]
        assert close_completed.is_set()
        assert _RAPIDOCR_RUNTIME_CACHE[key][0] is runtime
    finally:
        release_acquire.set()
        ensure_thread.join(timeout=2.0)
        close_thread.join(timeout=2.0)
        second.close()


def test_default_rapidocr_install_target_rejects_path_traversal_plugin_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(rapidocr_support, "is_windows_platform", lambda: True)
    monkeypatch.setattr(
        rapidocr_support,
        "get_config_manager",
        lambda: SimpleNamespace(app_docs_dir=tmp_path),
    )

    with pytest.raises(ValueError):
        rapidocr_support.default_rapidocr_install_target_raw("../study_companion")


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
    server_det_path = _touch(model_cache_dir / "ch_PP-OCRv4_det_server_infer.onnx")
    server_rec_path = _touch(model_cache_dir / "ch_PP-OCRv4_rec_server_infer.onnx")
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


def test_rapidocr_kwargs_sets_ppocrv5_cls_image_shape(tmp_path: Path) -> None:
    model_cache_dir = tmp_path / "RapidOCR" / "models"
    package_models_dir = tmp_path / "package" / "models"
    det_path = _touch(model_cache_dir / "ch_PP-OCRv5_det_mobile.onnx")
    rec_path = _touch(model_cache_dir / "ch_PP-OCRv5_rec_mobile.onnx")
    cls_path = _touch(model_cache_dir / "ch_PP-LCNet_x0_25_textline_ori_cls_mobile.onnx")

    kwargs = rapidocr_support._build_runtime_constructor_kwargs(
        _RapidOcrWithKwargs,
        engine_type="onnxruntime",
        lang_type="ch",
        model_type="mobile",
        ocr_version="PP-OCRv5",
        model_cache_dir=model_cache_dir,
        package_models_dir=package_models_dir,
    )

    assert kwargs == {
        "det_model_path": str(det_path),
        "rec_model_path": str(rec_path),
        "cls_model_path": str(cls_path),
        "cls_image_shape": [3, 80, 160],
        "engine_type": "onnxruntime",
    }


def test_rapidocr_kwargs_fails_when_registered_model_is_missing(tmp_path: Path) -> None:
    model_cache_dir = tmp_path / "RapidOCR" / "models"
    package_models_dir = tmp_path / "package" / "models"
    _touch(package_models_dir / "ch_PP-OCRv4_det_infer.onnx")
    _touch(package_models_dir / "ch_ppocr_mobile_v2.0_cls_infer.onnx")
    _touch(package_models_dir / "ch_PP-OCRv4_rec_infer.onnx")

    with pytest.raises(RuntimeError, match="PP-OCRv5/ch/mobile"):
        rapidocr_support._build_runtime_constructor_kwargs(
            _RapidOcrWithKwargs,
            engine_type="onnxruntime",
            lang_type="ch",
            model_type="mobile",
            ocr_version="PP-OCRv5",
            model_cache_dir=model_cache_dir,
            package_models_dir=package_models_dir,
        )


def test_required_rapidocr_model_files_defaults_to_bundled_ch(tmp_path: Path) -> None:
    files = rapidocr_support.required_rapidocr_model_files(
        install_target_dir_raw=str(tmp_path / "RapidOCR"),
        lang_type="",
        ocr_version="",
        plugin_id="galgame_plugin",
    )

    assert files == []


def test_inspect_rapidocr_installation_reports_modelscope_download_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        rapidocr_support.importlib.util,
        "find_spec",
        lambda _name: SimpleNamespace(origin=str(tmp_path / "rapidocr_onnxruntime" / "__init__.py")),
    )

    status = rapidocr_support.inspect_rapidocr_installation(
        install_target_dir_raw=str(tmp_path / "RapidOCR"),
        lang_type="en",
        ocr_version="PP-OCRv4",
        plugin_id="galgame_plugin",
        platform_fn=lambda: True,
    )

    assert status["detail"] == "missing_model_files"
    assert status["can_download_models"] is True
    assert "modelscope.cn" in status["model_download_source"]
    assert "baidu" not in status["model_download_source"].lower()


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
        plugin_id="galgame_plugin",
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


@pytest.mark.asyncio
async def test_download_rapidocr_models_uses_modelscope_urls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_target = tmp_path / "RapidOCR"
    expected_files = rapidocr_support.required_rapidocr_model_files(
        install_target_dir_raw=str(install_target),
        lang_type="en",
        ocr_version="PP-OCRv4",
        plugin_id="galgame_plugin",
    )
    file_bytes = {spec["name"]: spec["name"].encode("utf-8") for spec in expected_files}
    for spec in expected_files:
        spec["sha256"] = ""
    monkeypatch.setattr(
        rapidocr_support,
        "required_rapidocr_model_files",
        lambda **_kwargs: [dict(spec) for spec in expected_files],
    )
    monkeypatch.setattr(rapidocr_support, "_verify_model_sha256", lambda *_args, **_kwargs: None)

    requested_urls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        name = Path(request.url.path).name
        assert "modelscope.cn" in request.url.host
        return httpx.Response(200, content=file_bytes[name])

    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(_handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(rapidocr_support.httpx, "AsyncClient", _PatchedAsyncClient)

    result = await rapidocr_support.download_rapidocr_models(
        logger=SimpleNamespace(warning=lambda *args, **kwargs: None),
        install_target_dir_raw=str(install_target),
        lang_type="en",
        ocr_version="PP-OCRv4",
        plugin_id="galgame_plugin",
    )

    assert sorted(result["downloaded"]) == sorted(file_bytes)
    assert result["source"] == "modelscope"
    assert set(result["sources"].values()) == {"modelscope"}
    assert "fallback_used" not in result
    assert "baidu_error" not in result
    assert requested_urls == [spec["url"] for spec in expected_files]
    for name, content in file_bytes.items():
        assert (install_target / "models" / name).read_bytes() == content


@pytest.mark.asyncio
async def test_download_rapidocr_models_warns_when_task_id_has_no_state_updater(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_target = tmp_path / "RapidOCR"
    monkeypatch.setattr(
        rapidocr_support,
        "required_rapidocr_model_files",
        lambda **_kwargs: [],
    )
    warnings: list[str] = []

    result = await rapidocr_support.download_rapidocr_models(
        logger=SimpleNamespace(warning=lambda message, *args, **kwargs: warnings.append(str(message))),
        install_target_dir_raw=str(install_target),
        lang_type="ch",
        ocr_version="PP-OCRv4",
        plugin_id="galgame_plugin",
        task_id="run-without-state-updater",
    )

    assert result["skipped_bundled"] is True
    assert any("no install state updater" in message for message in warnings)


@pytest.mark.asyncio
async def test_download_rapidocr_models_keeps_going_when_state_updater_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_target = tmp_path / "RapidOCR"
    monkeypatch.setattr(
        rapidocr_support,
        "required_rapidocr_model_files",
        lambda **_kwargs: [],
    )
    warnings: list[str] = []

    def _failing_state_updater(*_args, **_kwargs):
        raise RuntimeError("state store unavailable")

    result = await rapidocr_support.download_rapidocr_models(
        logger=SimpleNamespace(warning=lambda message, *args, **kwargs: warnings.append(str(message))),
        install_target_dir_raw=str(install_target),
        lang_type="ch",
        ocr_version="PP-OCRv4",
        plugin_id="galgame_plugin",
        task_id="run-with-failing-state-updater",
        install_state_updater=_failing_state_updater,
    )

    assert result["skipped_bundled"] is True
    assert any("install state update failed" in message for message in warnings)


@pytest.mark.asyncio
async def test_download_rapidocr_models_reports_modelscope_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_target = tmp_path / "RapidOCR"
    expected_files = rapidocr_support.required_rapidocr_model_files(
        install_target_dir_raw=str(install_target),
        lang_type="en",
        ocr_version="PP-OCRv4",
        plugin_id="galgame_plugin",
    )
    file_bytes = {spec["name"]: f"modelscope:{spec['name']}".encode("utf-8") for spec in expected_files}
    for spec in expected_files:
        spec["sha256"] = ""
    monkeypatch.setattr(
        rapidocr_support,
        "required_rapidocr_model_files",
        lambda **_kwargs: [dict(spec) for spec in expected_files],
    )
    monkeypatch.setattr(rapidocr_support, "_verify_model_sha256", lambda *_args, **_kwargs: None)

    requested_urls: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        name = Path(request.url.path).name
        assert name in file_bytes
        assert "modelscope.cn" in request.url.host
        return httpx.Response(503, content=b"unavailable")

    class _PatchedAsyncClient(httpx.AsyncClient):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(_handler)
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(rapidocr_support.httpx, "AsyncClient", _PatchedAsyncClient)

    with pytest.raises(RuntimeError) as exc_info:
        await rapidocr_support.download_rapidocr_models(
            logger=SimpleNamespace(warning=lambda *args, **kwargs: None),
            install_target_dir_raw=str(install_target),
            lang_type="en",
            ocr_version="PP-OCRv4",
            plugin_id="galgame_plugin",
        )

    assert "ModelScope" in str(exc_info.value)
    assert "Baidu" not in str(exc_info.value)
    assert requested_urls == [expected_files[0]["url"]]
