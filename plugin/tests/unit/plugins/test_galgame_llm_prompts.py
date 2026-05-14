from __future__ import annotations

import json
from types import SimpleNamespace

from plugin.plugins.galgame_plugin.context_builder import build_local_scene_summary
from plugin.plugins.galgame_plugin import llm_prompts
from plugin.plugins.galgame_plugin.llm_prompts import (
    build_prompt_messages,
    build_prompt_messages_with_metadata,
)


def _cfg(**overrides):
    values = {
        "context_counting_mode": "char",
        "context_max_tokens": 6000,
        "context_semantic_compression": False,
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


def test_semantic_compression_disabled_keeps_rendered_context_unchanged() -> None:
    context = {
        "recent_lines": [
            {"speaker": "A", "text": "one", "scene_id": "s", "line_id": "1"},
            {"speaker": "A", "text": "two", "scene_id": "s", "line_id": "2"},
        ],
        "evidence": [
            {"type": "history_line", "text": "one", "line_id": "1", "speaker": "A"},
        ],
    }

    default = build_prompt_messages_with_metadata("explain_line", context)
    disabled = build_prompt_messages_with_metadata(
        "explain_line",
        context,
        _cfg(context_semantic_compression=False),
    )

    assert _rendered_context(default) == _rendered_context(disabled)
    assert disabled.metadata["semantic_compression_enabled"] is False


def test_semantic_compression_disabled_returns_independent_context() -> None:
    context = {
        "recent_lines": [
            {"speaker": "A", "text": "one", "scene_id": "s", "line_id": "1"},
        ],
        "public_context": {
            "recent_lines": [
                {"speaker": "B", "text": "two", "scene_id": "s", "line_id": "2"},
            ],
        },
    }

    prompt_context, _metadata = llm_prompts._condense_context(
        context,
        _cfg(context_semantic_compression=False),
    )
    prompt_context["recent_lines"][0]["text"] = "changed"
    prompt_context["public_context"]["recent_lines"][0]["text"] = "changed-public"

    assert context["recent_lines"][0]["text"] == "one"
    assert context["public_context"]["recent_lines"][0]["text"] == "two"


def test_semantic_compression_merges_same_speaker_short_lines() -> None:
    context = {
        "recent_lines": [
            {"speaker": "A", "text": "one", "scene_id": "s", "line_id": "1"},
            {"speaker": "A", "text": "two", "scene_id": "s", "line_id": "2"},
            {"speaker": "B", "text": "three", "scene_id": "s", "line_id": "3"},
        ],
    }

    result = build_prompt_messages_with_metadata(
        "explain_line",
        context,
        _cfg(context_semantic_compression=True),
    )
    rendered = json.loads(_rendered_context(result))

    assert len(rendered["recent_lines"]) == 2
    assert rendered["recent_lines"][0]["text"] == "one\ntwo"
    assert "_condensed_count" not in rendered["recent_lines"][0]
    assert "_condensed_line_ids" not in rendered["recent_lines"][0]
    assert result.metadata["semantic_compression_enabled"] is True
    assert result.metadata["semantic_lines_before"] == 3
    assert result.metadata["semantic_lines_after"] == 2


def test_semantic_compression_does_not_touch_evidence_current_or_choices() -> None:
    context = {
        "current_line": {"speaker": "A", "text": "current", "line_id": "current"},
        "visible_choices": [
            {"choice_id": "c1", "text": "left"},
            {"choice_id": "c2", "text": "right"},
        ],
        "recent_choices": [
            {"speaker": "A", "text": "choice one", "line_id": "choice-1"},
            {"speaker": "A", "text": "choice two", "line_id": "choice-2"},
        ],
        "evidence": [
            {"speaker": "A", "text": "evidence one", "line_id": "e1"},
            {"speaker": "A", "text": "evidence two", "line_id": "e2"},
        ],
        "public_context": {
            "recent_lines": [
                {"speaker": "A", "text": "one", "scene_id": "s", "line_id": "1"},
                {"speaker": "A", "text": "two", "scene_id": "s", "line_id": "2"},
            ],
            "recent_choices": [
                {"speaker": "A", "text": "public choice one", "line_id": "pc1"},
                {"speaker": "A", "text": "public choice two", "line_id": "pc2"},
            ],
            "current_line": {"speaker": "A", "text": "public current", "line_id": "pc"},
        },
    }

    result = build_prompt_messages_with_metadata(
        "agent_reply",
        context,
        _cfg(context_semantic_compression=True),
    )
    rendered = json.loads(_rendered_context(result))

    assert rendered["current_line"] == context["current_line"]
    assert rendered["visible_choices"] == context["visible_choices"]
    assert rendered["recent_choices"] == context["recent_choices"]
    assert rendered["evidence"] == context["evidence"]
    assert rendered["public_context"]["recent_choices"] == context["public_context"]["recent_choices"]
    assert rendered["public_context"]["current_line"] == context["public_context"]["current_line"]
    assert len(rendered["public_context"]["recent_lines"]) == 1


def test_semantic_compression_handles_missing_or_empty_public_context() -> None:
    contexts = [
        {},
        {"recent_lines": []},
        {"public_context": {"recent_lines": None}},
        {"public_context": {"recent_lines": []}},
    ]

    for context in contexts:
        result = build_prompt_messages_with_metadata(
            "agent_reply",
            context,
            _cfg(context_semantic_compression=True),
        )

        assert result.metadata["semantic_compression_enabled"] is True
        assert isinstance(json.loads(_rendered_context(result)), dict)


def test_semantic_compression_failure_falls_back_to_uncompressed(
    monkeypatch,
) -> None:
    context = {
        "recent_lines": [
            {"speaker": "A", "text": "one", "scene_id": "s", "line_id": "1"},
        ],
    }

    def _raise(_lines):
        raise ValueError("bad lines")

    monkeypatch.setattr(llm_prompts, "_condense_dialogue_batch", _raise)

    result = build_prompt_messages_with_metadata(
        "agent_reply",
        context,
        _cfg(context_semantic_compression=True),
    )
    rendered = json.loads(_rendered_context(result))

    assert rendered == context
    assert result.metadata["semantic_compression_enabled"] is False
    assert result.metadata["semantic_compression_fallback"] is True


def test_semantic_compression_failure_fallback_returns_independent_context(
    monkeypatch,
) -> None:
    context = {
        "recent_lines": [
            {"speaker": "A", "text": "one", "scene_id": "s", "line_id": "1"},
        ],
        "public_context": {
            "recent_lines": [
                {"speaker": "B", "text": "two", "scene_id": "s", "line_id": "2"},
            ],
        },
    }

    def _raise(_lines):
        raise ValueError("bad lines")

    monkeypatch.setattr(llm_prompts, "_condense_dialogue_batch", _raise)

    prompt_context, metadata = llm_prompts._condense_context(
        context,
        _cfg(context_semantic_compression=True),
    )
    prompt_context["recent_lines"][0]["text"] = "changed"
    prompt_context["public_context"]["recent_lines"][0]["text"] = "changed-public"

    assert metadata["semantic_compression_fallback"] is True
    assert context["recent_lines"][0]["text"] == "one"
    assert context["public_context"]["recent_lines"][0]["text"] == "two"


def test_semantic_compression_preserves_local_scene_summary_seed() -> None:
    lines = [
        {"speaker": "A", "text": "one", "scene_id": "s", "line_id": "1"},
        {"speaker": "A", "text": "two", "scene_id": "s", "line_id": "2"},
        {"speaker": "B", "text": "three", "scene_id": "s", "line_id": "3"},
    ]
    scene_summary_seed = build_local_scene_summary(
        scene_id="s",
        route_id="r",
        lines=lines,
        selected_choices=[{"choice_id": "c1", "text": "left"}],
        snapshot={"speaker": "B", "text": "three", "scene_id": "s", "route_id": "r"},
    )
    context = {
        "scene_id": "s",
        "scene_summary_seed": scene_summary_seed,
        "recent_lines": lines,
    }

    disabled = json.loads(_rendered_context(build_prompt_messages_with_metadata(
        "summarize_scene",
        context,
        _cfg(context_semantic_compression=False),
    )))
    enabled = json.loads(_rendered_context(build_prompt_messages_with_metadata(
        "summarize_scene",
        context,
        _cfg(context_semantic_compression=True),
    )))

    assert disabled["scene_summary_seed"] == scene_summary_seed
    assert enabled["scene_summary_seed"] == scene_summary_seed
