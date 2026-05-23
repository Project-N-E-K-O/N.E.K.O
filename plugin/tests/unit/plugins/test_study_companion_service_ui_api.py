from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from plugin.plugins.study_companion.models import OcrSnapshot, StudyConfig, TutorReply
from plugin.plugins.study_companion.service import (
    _available_tesseract_languages,
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
    assert build_tutor_payload(reply)["summary"] == "structured"
    assert build_explain_payload(reply)["extra"] == {"nested": True}
    assert build_ocr_payload(snapshot)["summary"] == "ocr text"


def test_dependency_status_uses_installability_and_tesseract_language_fallbacks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    tessdata = tmp_path / "tessdata"
    tessdata.mkdir()
    (tessdata / "eng.traineddata").write_text("fake", encoding="utf-8")
    detected = tmp_path / "tesseract.exe"
    detected.write_text("fake", encoding="utf-8")

    monkeypatch.setattr(
        "plugin.plugins.study_companion.service._inspect_rapidocr",
        lambda config: {"installed": False, "can_install": True},
    )
    monkeypatch.setattr(
        "plugin.plugins.study_companion.service._inspect_tesseract",
        lambda config: {"installed": True, "can_install": False},
    )
    monkeypatch.setattr(
        "plugin.plugins.study_companion.service._inspect_dxcam",
        lambda: {"installed": False, "can_install": True},
    )
    monkeypatch.setattr(
        "plugin.plugins.study_companion.service.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1, stdout="", stderr=""),
    )

    status = build_dependency_status(StudyConfig())
    languages = _available_tesseract_languages(detected, tmp_path)

    assert status["missing_installable"] == ["rapidocr", "dxcam"]
    assert languages == {"eng"}


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
                "prerequisites": [{"id": "topic-pre", "required_mastery": 0.7}],
                "related": [{"topic_id": "topic-b", "relation": "similar"}],
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
    assert map_payload["edges"][0]["required_mastery"] == 0.7
    assert contribution["preview"]["opt_in"] is True
    assert contribution["queue"] == [{"id": "q"}]
