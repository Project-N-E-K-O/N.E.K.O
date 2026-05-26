from __future__ import annotations

import pytest

from plugin.plugins.study_companion import prompt_templates as templates

pytestmark = pytest.mark.unit


def test_prompt_template_constants_render_expected_placeholders() -> None:
    rendered = templates.STUDY_STRUCTURED_USER_TEMPLATE.format(
        requirements="Rules:\n",
        example_json='{"ok": true}',
        context_json='{"text": "hello"}',
    )
    prefixed = templates.STUDY_STRUCTURED_MODE_PREFIX_TEMPLATE.format(
        mode="teaching", prompt=rendered
    )

    assert "Rules:" in rendered
    assert '{"ok": true}' in rendered
    assert "context:" in rendered
    assert prefixed.startswith("Mode: teaching")


def test_concept_prompt_templates_require_all_variables() -> None:
    with pytest.raises(KeyError):
        templates.STUDY_CONCEPT_EXPLAIN_USER_TEMPLATE.format(language="zh-CN")

    rendered = templates.STUDY_CONCEPT_EXPLAIN_USER_TEMPLATE.format(
        language="zh-CN",
        source="manual",
        mode="companion",
        text="calculus",
    )
    assert "Language: zh-CN" in rendered
    assert "Task: concept_explain" in rendered
    assert "calculus" in rendered


def test_prompt_context_limits_cover_all_structured_operations() -> None:
    assert set(templates.STUDY_PROMPT_CONTEXT_MAX_CHARS) == {
        "concept_explain",
        "question_generate",
        "answer_evaluate",
        "knowledge_track",
        "summarize_session",
    }
    assert templates.STUDY_FALLBACK_QUESTION_EMPTY["difficulty"] == 1
    assert templates.STUDY_MARKDOWN_SECTION_EMPTY_ITEM
