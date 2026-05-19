from __future__ import annotations

import json
from types import SimpleNamespace

from plugin.plugins.galgame_plugin.context_builder import build_local_scene_summary
from plugin.plugins.galgame_plugin import llm_prompts
from plugin.plugins.galgame_plugin.llm_prompts import (
    CHARACTER_ANCHOR_CONTEXT_TEMPLATE,
    CONSULT_CAT_CHOICE_QUESTION_TEMPLATE,
    CONSULT_CAT_PROMPT_TEMPLATE,
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


def test_prompt_truncation_notice_only_when_compacted() -> None:
    compacted = build_prompt_messages_with_metadata(
        "agent_reply",
        {"text": "x" * 13000},
    )
    uncompressed = build_prompt_messages_with_metadata(
        "agent_reply",
        {"text": "short"},
    )

    assert "Context truncation notice" in compacted.messages[0]["content"]
    assert "Context truncation notice" not in uncompressed.messages[0]["content"]


def test_prompt_truncation_notice_is_stronger_for_hard_fallback() -> None:
    result = build_prompt_messages_with_metadata(
        "agent_reply",
        {"items": [{"text": "日" * 1000, "extra": list(range(100))} for _ in range(50)]},
        _cfg(context_counting_mode="token", context_max_tokens=1),
    )

    assert result.metadata["compression_level"] == 4
    assert "heavily compacted" in result.messages[0]["content"]
    assert "uncertainty" in result.messages[0]["content"]


def test_prompt_rendering_strips_line_importance_metadata() -> None:
    result = build_prompt_messages_with_metadata(
        "agent_reply",
        {"recent_lines": [{"text": "important", "_importance_score": 9.0}]},
    )
    rendered = json.loads(_rendered_context(result))

    assert "_importance_score" not in rendered["recent_lines"][0]


def test_prompt_compaction_uses_importance_before_stripping_metadata() -> None:
    context = {
        "recent_lines": [
            {
                "line_id": "important",
                "text": "important " + ("x" * 900),
                "_importance_score": 999,
            },
            *[
                {
                    "line_id": f"filler-{index}",
                    "text": "filler " + ("x" * 900),
                    "_importance_score": 1,
                }
                for index in range(19)
            ],
        ]
    }

    result = llm_prompts._context_json_result_for_prompt(context)
    rendered = json.loads(result.text)
    line_ids = [line["line_id"] for line in rendered["recent_lines"]]

    assert result.metadata["compression_level"] > 0
    assert "important" in line_ids
    assert all("_importance_score" not in line for line in rendered["recent_lines"])


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


def test_host_play_mode_prompt_constants_are_available() -> None:
    anchor = CHARACTER_ANCHOR_CONTEXT_TEMPLATE.format(
        character_name="叢雨",
        identity="刀灵",
        voice_traits="· 嘴硬 -> 句尾带「ぞ」",
        verbal_tics="自称「わらわ」",
        relationships="有地将臣（契约者）",
        background="被封印数百年",
    )
    consult = CONSULT_CAT_PROMPT_TEMPLATE.format(
        scene_summary="场景摘要",
        consult_question=CONSULT_CAT_CHOICE_QUESTION_TEMPLATE.format(
            choices="A / B",
            character_name="叢雨",
        ),
        character_name="叢雨",
        character_voice_summary="自称「わらわ」",
    )

    assert "======[角色分析锚点]" in anchor
    assert "不要自由角色扮演，也不要冒充该角色说话" in anchor
    assert "角色：叢雨" in anchor
    assert "视角下的策略意见" in consult
    assert "不是强制指令" in consult


def test_suggest_choice_prompt_accepts_bounded_fixed_character_pov() -> None:
    result = build_prompt_messages_with_metadata(
        "suggest_choice",
        {
            "visible_choices": [{"choice_id": "c1", "text": "Protect the promise"}],
            "fixed_character_pov": {
                "enabled": True,
                "character_name": "Murasame",
                "profile_known": True,
                "role": "bounded_strategy_reference",
            },
        },
    )

    system = result.messages[0]["content"]
    user = result.messages[1]["content"]
    assert "bounded strategy lens" in system
    assert "Do not freeform role-play" in system
    assert "visible_choices" in system
    assert "fixed_character_pov" in user
    assert "valid-choice constraints" in user


def test_summarize_scene_prompt_uses_pov_without_roleplay() -> None:
    result = build_prompt_messages_with_metadata(
        "summarize_scene",
        {
            "stable_lines": [{"speaker": "A", "text": "A promise returns."}],
            "fixed_character_pov": {
                "enabled": True,
                "character_name": "Murasame",
                "profile_known": True,
            },
        },
    )

    system = result.messages[0]["content"]
    user = result.messages[1]["content"]
    assert "bounded narrative lens" in system
    assert "Do not freeform role-play" in system
    assert "relationship shifts" in user
    assert "do not narrate as the character" in user


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


def test_prompt_metadata_strip_removes_all_internal_condensed_keys() -> None:
    context = {
        "recent_lines": [
            {
                "speaker": "A",
                "text": "one",
                "line_id": "1",
                "_importance_score": 9,
                "_condensed_line_ids": ["1"],
                "_condensed_count": 2,
                "_condensed_debug": {"source": "test"},
            }
        ]
    }

    result = build_prompt_messages_with_metadata("explain_line", context)
    rendered = json.loads(_rendered_context(result))

    line = rendered["recent_lines"][0]
    assert "_importance_score" not in line
    assert "_condensed_line_ids" not in line
    assert "_condensed_count" not in line
    assert "_condensed_debug" not in line


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
