from __future__ import annotations

from types import SimpleNamespace

import pytest

from config.prompts import prompts_sys
from utils import config_manager
from utils import language_utils
from utils import screenshot_utils


pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_custom_vision_endpoint_allows_an_empty_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ConfigManager:
        async def aget_model_api_config(self, model_type: str) -> dict[str, object]:
            assert model_type == "vision"
            return {
                "model": "local-vision",
                "api_key": "",
                "base_url": "http://127.0.0.1:11434/v1",
                "is_custom": True,
                "provider_type": "openai",
            }

    class _VisionClient:
        async def __aenter__(self) -> "_VisionClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def ainvoke(self, _messages: object) -> SimpleNamespace:
            return SimpleNamespace(content="local description")

    create_calls: list[dict[str, object]] = []

    async def _create_chat_llm_async(**kwargs: object) -> _VisionClient:
        create_calls.append(kwargs)
        return _VisionClient()

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(
        screenshot_utils,
        "create_chat_llm_async",
        _create_chat_llm_async,
    )

    result = await screenshot_utils.analyze_image_with_vision_model("encoded-image")

    assert result == "local description"
    assert create_calls[0]["api_key"] == ""


@pytest.mark.asyncio
async def test_builtin_vision_endpoint_still_requires_an_api_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ConfigManager:
        async def aget_model_api_config(self, model_type: str) -> dict[str, object]:
            assert model_type == "vision"
            return {
                "model": "hosted-vision",
                "api_key": "",
                "base_url": "https://example.invalid/v1",
                "is_custom": False,
                "provider_type": "openai",
            }

    async def _unexpected_create(**_kwargs: object) -> object:
        raise AssertionError("hosted vision without an API key must not create a client")

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(
        screenshot_utils,
        "create_chat_llm_async",
        _unexpected_create,
    )

    assert await screenshot_utils.analyze_image_with_vision_model("encoded-image") is None


# -- Prompt language follows the overlay's script (issue #2841) --
#
# The avatar overlay on the image is drawn from get_global_language_full(), so a
# Traditional user's screenshot carries Traditional text. The vision prompt and
# its ignore hint used to read get_global_language(), whose short code collapses
# zh-TW into zh: the model got a Simplified prompt describing a Traditional
# overlay, and every zh-TW row of the vision tables went unused.

_LANGUAGE_CASES = [
    ("zh-TW", "zh-TW"),
    ("zh-CN", "zh"),
    ("en", "en"),
    ("ja", "ja"),
    ("ko", "ko"),
    ("ru", "ru"),
    ("es", "es"),
    ("pt", "pt"),
]


def _install_vision_capture(
    monkeypatch: pytest.MonkeyPatch, full_language: str,
) -> list[object]:
    """Patch config/LLM and the language getters; return the captured messages."""

    class _ConfigManager:
        async def aget_model_api_config(self, model_type: str) -> dict[str, object]:
            return {
                "model": "local-vision",
                "api_key": "",
                "base_url": "http://127.0.0.1:11434/v1",
                "is_custom": True,
                "provider_type": "openai",
            }

    captured: list[object] = []

    class _VisionClient:
        async def __aenter__(self) -> "_VisionClient":
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        async def ainvoke(self, messages: object) -> SimpleNamespace:
            captured.append(messages)
            return SimpleNamespace(content="description")

    async def _create_chat_llm_async(**_kwargs: object) -> _VisionClient:
        return _VisionClient()

    monkeypatch.setattr(config_manager, "get_config_manager", lambda: _ConfigManager())
    monkeypatch.setattr(screenshot_utils, "create_chat_llm_async", _create_chat_llm_async)
    # The two getters disagree exactly the way the real ones do: short drops the script.
    monkeypatch.setattr(language_utils, "get_global_language_full", lambda: full_language)
    monkeypatch.setattr(
        language_utils,
        "get_global_language",
        lambda: language_utils.normalize_language_code(full_language, format="short"),
    )
    return captured


@pytest.mark.parametrize(
    "table",
    [
        prompts_sys.VISION_SYSTEM_WITH_TITLE,
        prompts_sys.VISION_SYSTEM_NO_TITLE,
        prompts_sys.VISION_USER_WITH_TITLE,
        prompts_sys.VISION_USER_NO_TITLE,
        prompts_sys.AVATAR_ANNOTATION_IGNORE_HINT,
    ],
)
def test_traditional_and_simplified_vision_rows_differ(table: dict[str, str]) -> None:
    """Guards the cases below against passing vacuously on identical rows."""
    assert table["zh-TW"] != table["zh"]


@pytest.mark.parametrize("window_title", ["", "Editor"], ids=["no_title", "with_title"])
@pytest.mark.parametrize("full_language, key", _LANGUAGE_CASES)
@pytest.mark.asyncio
async def test_vision_prompt_follows_the_full_global_language(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    full_language: str,
    key: str,
    window_title: str,
) -> None:
    captured = _install_vision_capture(monkeypatch, full_language)

    result = await screenshot_utils.analyze_image_with_vision_model(
        "encoded-image", window_title=window_title,
    )

    assert result == "description"
    system_message, user_message = captured[0]
    hint = prompts_sys.AVATAR_ANNOTATION_IGNORE_HINT[key]
    if window_title:
        expected_system = prompts_sys.VISION_SYSTEM_WITH_TITLE[key]
        expected_user = prompts_sys.VISION_USER_WITH_TITLE[key].format(window_title=window_title)
    else:
        expected_system = prompts_sys.VISION_SYSTEM_NO_TITLE[key]
        expected_user = prompts_sys.VISION_USER_NO_TITLE[key]
    assert system_message["content"] == (
        prompts_sys.VISION_WATERMARK + expected_system + " " + hint
    )
    assert user_message["content"][1]["text"] == expected_user
    # A full locale handed to _loc unnormalized still lands on the right row via its
    # fallback, but warns on every call; the key has to be a real table key.
    assert "Unexpected lang code" not in capsys.readouterr().out
