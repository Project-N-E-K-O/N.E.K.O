from __future__ import annotations

import json
from pathlib import Path

import pytest


pytestmark = pytest.mark.unit

PLUGIN_DIR = Path(__file__).resolve().parents[3] / "plugins" / "study_companion"
STATIC_DIR = PLUGIN_DIR / "static"
LOCALES = ("en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW")


def test_static_knowledge_map_builds_server_authoritative_scopes() -> None:
    source = (STATIC_DIR / "knowledge-map.js").read_text(encoding="utf-8")

    assert "function knowledgePracticeScopeFromNode" in source
    assert "function knowledgeCurrentPracticeScope" in source
    assert "mode: 'explicit_topic'" in source
    assert "mode: 'explicit_scope'" in source
    assert "topic_id:" in source
    assert "course_family:" in source
    assert source.count("\n    chapter,") >= 2
    assert "unit:" in source
    assert source.count("\n    unit: chapter ?") >= 2
    assert "eligible_topic_ids" not in source


def test_static_knowledge_map_exposes_explicit_scope_actions_without_generating() -> None:
    source = (STATIC_DIR / "knowledge-map.js").read_text(encoding="utf-8")

    assert "ui.knowledge.practice_topic" in source
    assert "ui.knowledge.practice_current_scope" in source
    assert "activateKnowledgePracticeScope" in source
    assert "study_generate_targeted_question" not in source
    assert "renderKnowledgeHierarchyScopePicker" in source
    assert "knowledgeMapChapter" in source
    assert "knowledgeMapUnit" in source


def test_static_practice_generation_reloads_scope_and_one_time_context() -> None:
    source = (STATIC_DIR / "main.js").read_text(encoding="utf-8")
    generate = source[source.index("async function generateQuestion()") :]
    generate = generate[: generate.index("\n}") + 2]

    assert "await loadPracticeScope" in generate
    assert "await loadQuestionContext" in generate
    assert generate.index("await loadPracticeScope") < generate.index(
        "await loadQuestionContext"
    )
    assert "currentSelectionContext = null" in generate.split(
        "await loadQuestionContext", 1
    )[0]
    assert "selection_context_id" in generate


def test_static_practice_scope_path_and_clear_control_exist() -> None:
    index = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    source = "\n".join(
        (STATIC_DIR / name).read_text(encoding="utf-8")
        for name in ("knowledge-map.js", "main.js")
    )

    assert 'id="practiceScopePath"' in index
    assert 'id="clearPracticeScopeBtn"' in index
    assert "study_set_practice_scope" in source
    assert "study_get_practice_scope" in source
    assert "study_clear_practice_scope" in source
    assert "display_path" in source
    assert "currentSelectionContext = null" in source


def test_practice_scope_ui_messages_exist_in_all_locales() -> None:
    required = {
        "entries.practice_scope.set.name",
        "entries.practice_scope.set.description",
        "entries.practice_scope.get.name",
        "entries.practice_scope.get.description",
        "entries.practice_scope.clear.name",
        "entries.practice_scope.clear.description",
        "ui.practice.scope_label",
        "ui.practice.scope_automatic",
        "ui.practice.scope_set",
        "ui.button.clear_practice_scope",
        "ui.knowledge.chapter_label",
        "ui.knowledge.unit_label",
        "ui.knowledge.hierarchy_all",
        "ui.knowledge.practice_topic",
        "ui.knowledge.practice_current_scope",
        "ui.knowledge.scope_requires_stage_subject",
    }

    for locale in LOCALES:
        bundle = json.loads(
            (PLUGIN_DIR / "i18n" / f"{locale}.json").read_text(encoding="utf-8")
        )
        assert required <= bundle.keys(), locale
        assert all(str(bundle[key]).strip() for key in required), locale


def test_existing_knowledge_map_window_zoom_contract_is_preserved() -> None:
    source = (STATIC_DIR / "knowledge-map.js").read_text(encoding="utf-8")

    assert "KNOWLEDGE_MAP_ZOOM_LEVELS" in source
    assert "renderKnowledgeZoomControls" in source
    assert "surfaceDrawer.dataset.windowScale" in source


def test_static_scope_failure_clears_stale_path_and_dialog_restores_focus() -> None:
    source = (STATIC_DIR / "knowledge-map.js").read_text(encoding="utf-8")

    assert "setPracticeScopeState({ active: false, scope: {}, scope_revision: 0 })" in source
    assert "detailMount.replaceChildren();\n        item.focus();" in source
