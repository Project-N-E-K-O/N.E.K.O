"""Whether a conversation model can be shown a picture.

A name-matching heuristic, so it will be wrong sometimes. Every caller must
degrade gracefully on a False — the cost of a wrong answer is one extra
vision-model call, never a broken feature.
"""

from __future__ import annotations

import pytest

from utils.vision_capability import model_supports_vision


@pytest.mark.parametrize("model", [
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4.1",
    "gpt-4.5-preview",
    "gpt-5-thinking",
    "some-vision-model",
    "qwen2.5-vl-72b-instruct",
    "qwen-vl-max",
    "InternVL2",
    "OpenGVLab/InternVL2_5-8B",
    "gemini-2.0-flash",
    "claude-3-5-sonnet",
    "claude-4-opus",
    "glm-4v",
    "glm-4.5v",
    "GPT-4O",
    "  gpt-4o  ",
])
def test_known_vision_models(model):
    assert model_supports_vision(model) is True


@pytest.mark.parametrize("model", [
    "gpt-3.5-turbo",
    "deepseek-chat",
    "glm-4",
    "glm-4-plus",
    "moonshot-v1-8k",
    # ``vl`` must be a bounded token; these only contain the letters.
    "avl-router",
    "evolved-chat",
    "revision-text",
])
def test_text_only_models(model):
    assert model_supports_vision(model) is False


@pytest.mark.parametrize("model", [
    "foo-vl-bar",
    "vl-7b",
    "model.vl.1",
])
def test_bounded_vl_token_still_counts_as_vision(model):
    assert model_supports_vision(model) is True


@pytest.mark.parametrize("model", ["", "   ", None, 123])
def test_missing_or_junk_input_is_not_vision(model):
    assert model_supports_vision(model) is False
