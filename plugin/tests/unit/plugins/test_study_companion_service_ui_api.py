from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.plugins._shared.rapidocr import _inspect_download as shared_rapidocr_inspect
from plugin.plugins._shared.rapidocr import _runtime as shared_rapidocr_runtime
from plugin.plugins._shared.rapidocr import rapidocr_support as shared_rapidocr_support
from plugin.plugins.study_companion.models import OcrSnapshot, StudyConfig, TutorReply
from plugin.plugins.study_companion.service import (
    _build_ocr_readiness,
    build_dependency_status,
    build_explain_payload,
    build_ocr_payload,
    build_status_payload,
    build_tutor_payload,
)
from plugin.plugins.study_companion.state import build_initial_state
from plugin.plugins.study_companion.ui_api import (
    build_contribution_settings_payload,
    build_knowledge_map_payload,
    build_open_ui_payload,
)

pytestmark = pytest.mark.unit


def test_ocr_readiness_uses_selected_capture_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "plugin.plugins.study_companion.service.importlib.util.find_spec",
        lambda name: object() if name == "mss" else None,
    )
    readiness = _build_ocr_readiness(
        config=StudyConfig(
            ocr_enabled=True,
            ocr_backend_selection="rapidocr",
            ocr_capture_backend="mss",
        ),
        rapidocr={"installed": True},
        dxcam={"installed": False},
    )

    assert readiness["ready"] is True
    assert readiness["diagnostic"] == "ready"


class _RapidOcrWithKwargs:
    def __init__(self, config_path=None, **kwargs) -> None:
        del config_path, kwargs


def test_service_payload_builders_preserve_nested_state_and_reply_payloads() -> None:
    config = StudyConfig(language="en")
    state = build_initial_state(mode=config.mode)
    state.last_screen_classification = {"screen_type": "question"}
    reply = TutorReply(
        operation="concept_explain",
        input_text="text",
        reply="fallback summary",
        payload={"summary": "structured", "extra": {"nested": True}},
    )
    snapshot = OcrSnapshot(text="ocr text", status="ok", backend="fake")

    status = build_status_payload(
        config=config,
        state=state,
        history=[{"role": "user"}],
        knowledge={"weak_topics": [{"topic_id": "t"}], "memory_deck": {"card_count": 1}},
        is_first_run=True,
    )

    assert status["is_first_run"] is True
    assert status["history"] == [{"role": "user"}]
    assert status["weak_topics"] == [{"topic_id": "t"}]
    assert "current_question" not in status
    assert build_tutor_payload(reply)["summary"] == "structured"
    assert build_explain_payload(reply)["extra"] == {"nested": True}
    assert build_ocr_payload(snapshot)["summary"] == "ocr text"


def test_status_payload_only_deepcopies_exposed_knowledge_fields() -> None:
    class _KnowledgeDict(dict):
        def __deepcopy__(self, memo):  # noqa: ANN001
            raise AssertionError("outer knowledge mapping should not be deep-copied")

    config = StudyConfig(language="en")
    state = build_initial_state(mode=config.mode)
    knowledge = _KnowledgeDict({"weak_topics": [{"topic_id": "limits"}]})

    status = build_status_payload(config=config, state=state, knowledge=knowledge)
    knowledge["weak_topics"][0]["topic_id"] = "mutated"

    assert status["weak_topics"] == [{"topic_id": "limits"}]


def test_dependency_status_preserves_legacy_fields_and_adds_readiness(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "plugin.plugins.study_companion.service._inspect_rapidocr",
        lambda _config: {
            "installed": False,
            "can_download_models": True,
            "detail": "missing_model_files",
        },
    )
    monkeypatch.setattr(
        "plugin.plugins.study_companion.service._inspect_dxcam",
        lambda: {"installed": False, "can_install": True, "detail": "missing"},
    )

    status = build_dependency_status(StudyConfig())

    assert set(status) == {
        "rapidocr",
        "dxcam",
        "missing_installable",
        "ocr_readiness",
    }
    assert status["missing_installable"] == ["rapidocr_models"]
    assert status["ocr_readiness"] == {
        "enabled": True,
        "selected_backend": "rapidocr",
        "selected_backend_ready": False,
        "capture_ready": False,
        "ready": False,
        "diagnostic": "rapidocr_models_missing",
    }


@pytest.mark.parametrize(
    ("config", "rapidocr", "tesseract", "dxcam", "expected"),
    [
        (
            StudyConfig(ocr_backend_selection="rapidocr"),
            {"installed": True, "detail": "installed"},
            {"installed": False, "detail": "missing"},
            {"installed": True, "detail": "installed"},
            {
                "enabled": True,
                "selected_backend": "rapidocr",
                "selected_backend_ready": True,
                "capture_ready": True,
                "ready": True,
                "diagnostic": "ready",
            },
        ),
        (
            StudyConfig(ocr_backend_selection="tesseract"),
            {"installed": False, "detail": "missing"},
            {"installed": True, "detail": "installed"},
            {"installed": True, "detail": "installed"},
            {
                "enabled": True,
                "selected_backend": "rapidocr",
                "selected_backend_ready": False,
                "capture_ready": True,
                "ready": False,
                "diagnostic": "rapidocr_runtime_missing",
            },
        ),
        (
            StudyConfig(ocr_enabled=False, ocr_backend_selection="rapidocr"),
            {"installed": False, "detail": "missing"},
            {"installed": False, "detail": "missing"},
            {"installed": False, "detail": "missing"},
            {
                "enabled": False,
                "selected_backend": "rapidocr",
                "selected_backend_ready": False,
                "capture_ready": False,
                "ready": False,
                "diagnostic": "ocr_disabled",
            },
        ),
        (
            StudyConfig(ocr_backend_selection="rapidocr"),
            {"installed": False, "detail": "missing_model_files"},
            {"installed": True, "detail": "installed"},
            {"installed": True, "detail": "installed"},
            {
                "enabled": True,
                "selected_backend": "rapidocr",
                "selected_backend_ready": False,
                "capture_ready": True,
                "ready": False,
                "diagnostic": "rapidocr_models_missing",
            },
        ),
        (
            StudyConfig(ocr_backend_selection="rapidocr"),
            {"installed": False, "detail": "broken_runtime"},
            {"installed": True, "detail": "installed"},
            {"installed": True, "detail": "installed"},
            {
                "enabled": True,
                "selected_backend": "rapidocr",
                "selected_backend_ready": False,
                "capture_ready": True,
                "ready": False,
                "diagnostic": "rapidocr_runtime_broken",
            },
        ),
        (
            StudyConfig(ocr_backend_selection="tesseract"),
            {"installed": True, "detail": "installed"},
            {"installed": False, "detail": "missing_languages"},
            {"installed": True, "detail": "installed"},
            {
                "enabled": True,
                "selected_backend": "rapidocr",
                "selected_backend_ready": True,
                "capture_ready": True,
                "ready": True,
                "diagnostic": "ready",
            },
        ),
        (
            StudyConfig(ocr_backend_selection="tesseract"),
            {"installed": True, "detail": "installed"},
            {"installed": True, "detail": "installed"},
            {"installed": False, "detail": "missing"},
            {
                "enabled": True,
                "selected_backend": "rapidocr",
                "selected_backend_ready": True,
                "capture_ready": False,
                "ready": False,
                "diagnostic": "capture_dependency_missing",
            },
        ),
        (
            StudyConfig(ocr_backend_selection="unknown"),
            {"installed": True, "detail": "installed"},
            {"installed": True, "detail": "installed"},
            {"installed": True, "detail": "installed"},
            {
                "enabled": True,
                "selected_backend": "rapidocr",
                "selected_backend_ready": True,
                "capture_ready": True,
                "ready": True,
                "diagnostic": "ready",
            },
        ),
    ],
)
def test_dependency_status_reports_selected_ocr_chain_readiness(
    monkeypatch: pytest.MonkeyPatch,
    config: StudyConfig,
    rapidocr: dict[str, object],
    tesseract: dict[str, object],
    dxcam: dict[str, object],
    expected: dict[str, object],
) -> None:
    monkeypatch.setattr(
        "plugin.plugins.study_companion.service._inspect_rapidocr",
        lambda _config: rapidocr,
    )
    monkeypatch.setattr(
        "plugin.plugins.study_companion.service._inspect_dxcam", lambda: dxcam
    )

    status = build_dependency_status(config)

    assert status["ocr_readiness"] == expected
    assert status["rapidocr"] is rapidocr
    assert status["dxcam"] is dxcam


def test_invalid_rapidocr_language_uses_fallback_and_reports_warning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    def _inspect_fallback(**kwargs: object) -> dict[str, object]:
        calls.append(dict(kwargs))
        return {
            "installed": True,
            "can_install": False,
            "can_download_models": False,
            "detail": "installed",
        }

    monkeypatch.setattr(
        shared_rapidocr_support,
        "inspect_rapidocr_installation",
        _inspect_fallback,
    )
    monkeypatch.setattr(
        "plugin.plugins.study_companion.service._inspect_dxcam",
        lambda: {"installed": True},
    )

    status = build_dependency_status(StudyConfig(rapidocr_lang_type=" invalid "))

    assert calls == [
        {
            "install_target_dir_raw": "",
            "engine_type": "onnxruntime",
            "lang_type": "ch",
            "model_type": "mobile",
            "ocr_version": "PP-OCRv4",
            "plugin_id": "study_companion",
        }
    ]
    assert status["rapidocr"]["installed"] is True
    assert status["rapidocr"]["detail"] == "installed"
    assert status["rapidocr"]["diagnostic"] == "rapidocr_language_invalid"
    assert status["ocr_readiness"]["ready"] is True
    assert status["ocr_readiness"]["diagnostic"] == "rapidocr_language_invalid"


def test_invalid_rapidocr_language_preserves_fallback_model_download(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        shared_rapidocr_support,
        "inspect_rapidocr_installation",
        lambda **_kwargs: {
            "installed": False,
            "can_install": False,
            "can_download_models": True,
            "detail": "missing_model_files",
        },
    )

    status = build_dependency_status(StudyConfig(rapidocr_lang_type="invalid"))

    assert status["rapidocr"]["can_download_models"] is True
    assert status["rapidocr"]["detail"] == "missing_model_files"
    assert status["missing_installable"] == ["rapidocr_models"]
    assert status["ocr_readiness"]["diagnostic"] == "rapidocr_models_missing"


def test_legacy_tesseract_config_values_remain_serializable_for_rollback() -> None:
    config = StudyConfig(
        ocr_backend_selection="tesseract",
        ocr_tesseract_path="C:/legacy/tesseract.exe",
        ocr_install_manifest_url="https://legacy.invalid/manifest.json",
        ocr_install_target_dir="C:/legacy/Tesseract-OCR",
        ocr_languages="eng+jpn",
    )

    payload = config.to_dict()

    assert payload["ocr_backend_selection"] == "tesseract"
    assert payload["ocr_tesseract_path"] == "C:/legacy/tesseract.exe"
    assert payload["ocr_install_manifest_url"] == "https://legacy.invalid/manifest.json"
    assert payload["ocr_install_target_dir"] == "C:/legacy/Tesseract-OCR"
    assert payload["ocr_languages"] == "eng+jpn"


@pytest.mark.parametrize("lang_type", ["ch", "japan", "korean", "en"])
def test_rapidocr_language_values_are_preserved(lang_type: str) -> None:
    assert StudyConfig(rapidocr_lang_type=f" {lang_type.upper()} ").rapidocr_lang_type == lang_type


def test_study_rapidocr_resolve_uses_galgame_runtime_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_docs_dir = tmp_path / "AppDocs"
    galgame_target = app_docs_dir / "runtimes" / "galgame_plugin" / "RapidOCR"
    (galgame_target / "models").mkdir(parents=True)
    (galgame_target / "models" / "japan_PP-OCRv4_rec_infer.onnx").write_bytes(
        b"model"
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "EmptyLocalAppData"))
    monkeypatch.setattr(shared_rapidocr_support, "is_windows_platform", lambda: True)
    monkeypatch.setattr(
        shared_rapidocr_support,
        "get_config_manager",
        lambda: SimpleNamespace(app_docs_dir=app_docs_dir),
    )

    raw_target = shared_rapidocr_support.default_rapidocr_install_target_raw(
        plugin_id="study_companion"
    )
    resolved = shared_rapidocr_support.resolve_rapidocr_install_target(
        "",
        plugin_id="study_companion",
    )

    assert raw_target == str(
        app_docs_dir / "runtimes" / "study_companion" / "RapidOCR"
    )
    assert resolved == galgame_target
    assert shared_rapidocr_support.resolve_rapidocr_model_cache_dir(
        "",
        plugin_id="study_companion",
    ) == (galgame_target / "models")


def test_study_rapidocr_resolve_uses_galgame_fallback_when_new_target_is_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_docs_dir = tmp_path / "AppDocs"
    study_target = app_docs_dir / "runtimes" / "study_companion" / "RapidOCR"
    galgame_target = app_docs_dir / "runtimes" / "galgame_plugin" / "RapidOCR"
    study_target.mkdir(parents=True)
    (galgame_target / "models").mkdir(parents=True)
    (galgame_target / "models" / "japan_PP-OCRv4_rec_infer.onnx").write_bytes(
        b"model"
    )
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "EmptyLocalAppData"))
    monkeypatch.setattr(shared_rapidocr_support, "is_windows_platform", lambda: True)
    monkeypatch.setattr(
        shared_rapidocr_support,
        "get_config_manager",
        lambda: SimpleNamespace(app_docs_dir=app_docs_dir),
    )

    assert shared_rapidocr_support.resolve_rapidocr_install_target(
        "",
        plugin_id="study_companion",
    ) == galgame_target


def test_study_rapidocr_resolve_uses_legacy_models_only_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    app_docs_dir = tmp_path / "AppDocs"
    legacy_root = tmp_path / "LegacyLocalAppData"
    legacy_target = legacy_root / "Programs" / "N.E.K.O" / "RapidOCR"
    (legacy_target / "models").mkdir(parents=True)
    (legacy_target / "models" / "en_PP-OCRv4_rec_infer.onnx").write_bytes(b"model")
    monkeypatch.setenv("LOCALAPPDATA", str(legacy_root))
    monkeypatch.setattr(shared_rapidocr_support, "is_windows_platform", lambda: True)
    monkeypatch.setattr(
        shared_rapidocr_support,
        "get_config_manager",
        lambda: SimpleNamespace(app_docs_dir=app_docs_dir),
    )

    assert shared_rapidocr_support.resolve_rapidocr_install_target(
        "",
        plugin_id="study_companion",
    ) == legacy_target


def test_shared_rapidocr_kwargs_fail_when_configured_model_is_missing(
    tmp_path: Path,
) -> None:
    model_cache_dir = tmp_path / "RapidOCR" / "models"
    package_models_dir = tmp_path / "package" / "models"
    package_models_dir.mkdir(parents=True)
    (package_models_dir / "ch_PP-OCRv4_det_infer.onnx").write_text("", encoding="utf-8")
    (package_models_dir / "ch_PP-OCRv4_rec_infer.onnx").write_text("", encoding="utf-8")

    with pytest.raises(RuntimeError, match="PP-OCRv5/ch/mobile"):
        shared_rapidocr_runtime._build_runtime_constructor_kwargs(
            _RapidOcrWithKwargs,
            engine_type="onnxruntime",
            lang_type="ch",
            model_type="mobile",
            ocr_version="PP-OCRv5",
            model_cache_dir=model_cache_dir,
            package_models_dir=package_models_dir,
        )


def test_shared_rapidocr_kwargs_allows_unregistered_model_fallback(
    tmp_path: Path,
) -> None:
    model_cache_dir = tmp_path / "RapidOCR" / "models"
    package_models_dir = tmp_path / "package" / "models"
    package_models_dir.mkdir(parents=True)
    (package_models_dir / "ch_PP-OCRv4_det_infer.onnx").write_text("", encoding="utf-8")
    (package_models_dir / "ch_PP-OCRv4_rec_infer.onnx").write_text("", encoding="utf-8")

    kwargs = shared_rapidocr_runtime._build_runtime_constructor_kwargs(
        _RapidOcrWithKwargs,
        engine_type="onnxruntime",
        lang_type="multi",
        model_type="mobile",
        ocr_version="PP-OCRv4",
        model_cache_dir=model_cache_dir,
        package_models_dir=package_models_dir,
    )

    assert kwargs == {"engine_type": "onnxruntime"}


def test_shared_rapidocr_inspection_returns_install_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_target = tmp_path / "RapidOCR"
    install_target.mkdir()
    (install_target / "install_state.json").write_text(
        '{"selected_model": "PP-OCRv4/japan/mobile"}',
        encoding="utf-8",
    )
    monkeypatch.setattr(
        shared_rapidocr_inspect.importlib.util,
        "find_spec",
        lambda _name: SimpleNamespace(
            origin=str(tmp_path / "rapidocr_onnxruntime" / "__init__.py")
        ),
    )

    status = shared_rapidocr_inspect.inspect_rapidocr_installation(
        install_target_dir_raw=str(install_target),
        lang_type="ch",
        ocr_version="PP-OCRv4",
        plugin_id="study_companion",
        platform_fn=lambda: True,
    )

    assert status["install_state"] == {"selected_model": "PP-OCRv4/japan/mobile"}


def test_ui_api_payloads_cover_open_map_and_contribution_shapes() -> None:
    open_payload = build_open_ui_payload(plugin_id="study", available=True)
    unavailable = build_open_ui_payload(plugin_id="study", available=False)
    map_payload = build_knowledge_map_payload(
        topics=[
            {
                "id": "topic-a",
                "name": "Topic A",
                "subject": "math",
                "chapter": "1",
                "stage": "senior_high",
                "typical_misconceptions": ["Treating prerequisites as optional review."],
                "prerequisites": [
                    {
                        "id": "topic-pre",
                        "required_mastery": 0.7,
                        "reason": "Topic A builds on the prerequisite topic.",
                    }
                ],
                "related": [
                    {
                        "topic_id": "topic-b",
                        "relation": "application",
                        "reason": "Topic B applies Topic A.",
                        "use_cases": ["learning_path"],
                    }
                ],
            },
            {"id": ""},
        ],
        mastery_overview=[{"topic_id": "topic-a", "mastery": 0.4, "level": "weak"}],
        weak_topics=[{"topic_id": "topic-a"}],
        wrong_questions=[{"id": 1}],
    )
    contribution = build_contribution_settings_payload(
        opt_in=True,
        preview={"summary": {"topic_count": 1}, "queue": [{"id": "q"}]},
    )

    assert open_payload["path"] == "/plugin/study/ui/"
    assert unavailable["message_key"] == "ui.open.unavailable"
    assert map_payload["summary"]["weak_topic_count"] == 1
    assert map_payload["summary"]["stage_counts"]["senior_high"] == 1
    topic_node = next(node for node in map_payload["nodes"] if node["id"] == "topic-a")
    assert topic_node["stage"] == "senior_high"
    assert topic_node["typical_misconceptions"] == ["Treating prerequisites as optional review."]
    assert map_payload["edges"][0]["required_mastery"] == 0.7
    assert map_payload["edges"][0]["reason"] == "Topic A builds on the prerequisite topic."
    assert map_payload["edges"][0]["priority"] == "core"
    assert map_payload["edges"][0]["context"] == "diagnosis"
    assert map_payload["edges"][0]["confidence"] == 0.8
    assert map_payload["edges"][1]["relation"] == "application"
    assert map_payload["edges"][1]["use_cases"] == ["learning_path"]
    assert map_payload["edges"][1]["priority"] == "useful"
    assert map_payload["edges"][1]["context"] == "practice"
    assert map_payload["edges"][1]["confidence"] == 0.9
    assert contribution["preview"]["opt_in"] is True
    assert contribution["queue"] == [{"id": "q"}]
