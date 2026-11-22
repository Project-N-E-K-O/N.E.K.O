"""Provider presets whose chat model must read images now that the vision slot is retired."""

import json
from pathlib import Path

import pytest

from config.api_profiles import DEFAULT_ASSIST_API_PROFILES
from config.providers import MODELS_EXTRA_BODY_MAP, EXTRA_BODY_OPENAI, EXTRA_BODY_STEP_LOW

pytestmark = pytest.mark.unit

_PROVIDERS = json.loads(
    (Path(__file__).resolve().parents[2] / "config" / "api_providers.json").read_text(encoding="utf-8")
)["assist_api_providers"]


@pytest.mark.parametrize("provider, chat", [
    ("glm", "glm-5.3-flash"),
    ("silicon", "deepseek-ai/DeepSeek-V4.1-Flash"),
    ("free", "free-model"),
])
def test_chat_preset_reads_images_and_vision_follows_it(provider, chat):
    # Images go to the chat model; a preset vision model that differs from it
    # would make every preset user hit the legacy fallback path.
    profile = DEFAULT_ASSIST_API_PROFILES[provider]
    assert profile["CONVERSATION_MODEL"] == chat
    assert profile["VISION_MODEL"] == chat
    assert _PROVIDERS[provider]["conversation_model"] == chat
    assert _PROVIDERS[provider]["vision_model"] == chat


def test_every_preset_vision_model_matches_its_chat_model():
    for key, entry in _PROVIDERS.items():
        if entry.get("vision_model"):
            assert entry["vision_model"] == entry.get("conversation_model"), key


def test_silicon_agent_preset_left_the_disabled_model():
    # Qwen/Qwen3.5-122B-A10B answered 403 "Model disabled" (2026-11-22).
    assert DEFAULT_ASSIST_API_PROFILES["silicon"]["AGENT_MODEL"] == "deepseek-ai/DeepSeek-V4.1-Flash"
    assert _PROVIDERS["silicon"]["agent_model"] == "deepseek-ai/DeepSeek-V4.1-Flash"


def test_new_chat_models_have_thinking_controls():
    # glm-5.3-flash cannot disable thinking: low is the floor.
    assert MODELS_EXTRA_BODY_MAP["glm-5.3-flash"] is EXTRA_BODY_STEP_LOW
    assert MODELS_EXTRA_BODY_MAP["deepseek-ai/DeepSeek-V4.1-Flash"] is EXTRA_BODY_OPENAI
