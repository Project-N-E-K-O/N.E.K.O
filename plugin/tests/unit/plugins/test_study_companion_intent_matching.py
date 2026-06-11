from __future__ import annotations

import pytest

from plugin.plugins.study_companion.constants import MODE_COMPANION, MODE_INTERACTIVE, MODE_TEACHING
from plugin.plugins.study_companion.mode_manager import handle_user_intent

pytestmark = pytest.mark.unit


def test_handle_user_intent_ignores_plain_learning_text_with_mode_words() -> None:
    for text in (
        "discussion of mitochondria",
        "teaching strategies in biology",
        "companion matrix eigenvalues",
        "an explainable model example",
        "explanation of photosynthesis",
    ):
        intent = handle_user_intent(text, language="en")
        assert intent["matched"] is False
        assert intent["remaining_text"] == text


def test_handle_user_intent_accepts_explicit_switch_phrases() -> None:
    discussion = handle_user_intent("switch to discussion mode mitochondria", language="en")
    assert discussion["mode"] == MODE_INTERACTIVE
    assert discussion["remaining_text"] == "mitochondria"

    layered = handle_user_intent("please switch to teaching mode photosynthesis", language="en")
    assert layered["mode"] == MODE_TEACHING
    assert layered["remaining_text"] == "photosynthesis"

    companion = handle_user_intent("switch to companion", language="en")
    assert companion["mode"] == MODE_COMPANION
    assert companion["pure_switch"] is True

    teaching = handle_user_intent("teach me photosynthesis", language="en")
    assert teaching["mode"] == MODE_TEACHING
    assert teaching["remaining_text"] == "photosynthesis"

    cross_mode = handle_user_intent("教我互动模式 光合作用", language="zh-CN")
    assert cross_mode["mode"] == MODE_INTERACTIVE
    assert cross_mode["remaining_text"] == "光合作用"


def test_handle_user_intent_explain_requires_word_boundary() -> None:
    explain = handle_user_intent("please explain the derivative", language="en")
    assert explain["kind"] == "concept_explain"
    assert explain["remaining_text"] == "the derivative"

    plain = handle_user_intent("teach photosynthesis", language="en")
    assert plain["matched"] is False
    assert plain["remaining_text"] == "teach photosynthesis"
