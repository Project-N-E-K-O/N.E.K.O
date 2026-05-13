from __future__ import annotations

import json
from types import SimpleNamespace

from plugin.plugins.galgame_plugin.llm_prompts import (
    build_prompt_messages,
    build_prompt_messages_with_metadata,
)


def _cfg(**overrides):
    values = {
        "context_counting_mode": "char",
        "context_max_tokens": 6000,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _rendered_context(result) -> str:
    return result.messages[1]["content"].split("context:\n", 1)[1]


def test_prompt_context_keeps_default_char_mode_behavior() -> None:
    context = {"text": "x" * 13000}

    default_rendered = _rendered_context(build_prompt_messages_with_metadata("agent_reply", context))
    configured_rendered = _rendered_context(build_prompt_messages_with_metadata(
        "agent_reply",
        context,
        _cfg(context_counting_mode="char", context_max_tokens=1),
    ))

    assert default_rendered == configured_rendered
    assert len(default_rendered) <= 12000
    assert json.loads(default_rendered)["_prompt_truncated"] is True


def test_token_mode_allows_long_ascii_context_past_char_budget() -> None:
    context = {"text": "a" * 20000}

    rendered = _rendered_context(build_prompt_messages_with_metadata(
        "agent_reply",
        context,
        _cfg(context_counting_mode="token", context_max_tokens=6000),
    ))

    assert len(rendered) > 12000
    assert json.loads(rendered)["text"] == "a" * 20000


def test_token_mode_compacts_cjk_context_earlier() -> None:
    context = {"text": "日" * 5000}

    result = build_prompt_messages_with_metadata(
        "agent_reply",
        context,
        _cfg(context_counting_mode="token", context_max_tokens=2000),
    )

    assert result.metadata["compression_level"] == 1
    assert result.metadata["compacted_tokens"] <= result.metadata["raw_tokens"]
    assert "context:" in result.messages[1]["content"]


def test_token_mode_hard_fallback_reports_level_four() -> None:
    context = {"items": [{"text": "日" * 1000, "extra": list(range(100))} for _ in range(50)]}

    result = build_prompt_messages_with_metadata(
        "agent_reply",
        context,
        _cfg(context_counting_mode="token", context_max_tokens=1),
    )

    assert result.metadata["compression_level"] == 4
    assert json.loads(_rendered_context(result))["_prompt_truncated"] is True


def test_token_mode_hard_fallback_trims_excerpt_to_token_budget() -> None:
    context = {"items": [{"text": "日" * 5000, "extra": list(range(100))} for _ in range(50)]}

    result = build_prompt_messages_with_metadata(
        "agent_reply",
        context,
        _cfg(context_counting_mode="token", context_max_tokens=300),
    )
    rendered_context = json.loads(_rendered_context(result))

    assert result.metadata["compression_level"] == 4
    assert result.metadata["compacted_tokens"] <= result.metadata["budget"]
    assert rendered_context["_prompt_truncated"] is True
    assert "context_excerpt" in rendered_context


def test_build_prompt_messages_public_contract_returns_message_list() -> None:
    messages = build_prompt_messages("agent_reply", {"prompt": "status"})

    assert isinstance(messages, list)
    assert [message["role"] for message in messages] == ["system", "user"]
