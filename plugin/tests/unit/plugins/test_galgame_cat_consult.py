from __future__ import annotations

from dataclasses import asdict

import pytest

from plugin.plugins.galgame_plugin.agent_consultation import (
    CONSULT_COOLDOWN_SECONDS,
    CONSULT_PROGRESS_LINE_THRESHOLD,
    CONSULT_REASON_CHOICE,
    CONSULT_REASON_SCENE_CHANGE,
    CONSULT_REASON_STORY_PROGRESS,
    MAX_CAT_OPINIONS,
    ConsultInputs,
    build_consult_prompt,
    decide_consultation,
    get_recent_cat_opinions,
    inject_cat_opinion,
    render_cat_opinions_for_strategy,
    summarize_character_voice,
)


# ---------------------------------------------------------------------------
# decide_consultation
# ---------------------------------------------------------------------------


@pytest.fixture
def fixed_mode_base() -> ConsultInputs:
    return ConsultInputs(
        character_mode="fixed",
        character_fixed_name="叢雨",
        scene_id="scene_12",
        now=1000.0,
        last_consult_ts=0.0,
        profile_known=True,
    )


def test_mode_off_never_consults() -> None:
    decision = decide_consultation(
        ConsultInputs(character_mode="off", character_fixed_name="叢雨")
    )
    assert decision.should_consult is False
    assert decision.skip_reason == "mode_off"


def test_fixed_mode_requires_name(fixed_mode_base: ConsultInputs) -> None:
    decision = decide_consultation(
        ConsultInputs(
            character_mode="fixed",
            character_fixed_name="",
            now=fixed_mode_base.now,
            profile_known=False,
        )
    )
    assert decision.should_consult is False
    assert decision.skip_reason == "no_fixed_character"


def test_fixed_mode_requires_loaded_profile(
    fixed_mode_base: ConsultInputs,
) -> None:
    decision = decide_consultation(
        ConsultInputs(
            character_mode="fixed",
            character_fixed_name="叢雨",
            now=fixed_mode_base.now,
            profile_known=False,
        )
    )
    assert decision.should_consult is False
    assert decision.skip_reason == "profile_missing"


def test_choice_trigger_wins(fixed_mode_base: ConsultInputs) -> None:
    decision = decide_consultation(
        ConsultInputs(
            **{
                **asdict(fixed_mode_base),
                "visible_choices": ("坦率", "装作"),
                "scene_changed": True,
                "lines_since_last_consult": 10,
            }
        )
    )
    assert decision.should_consult is True
    assert decision.reason == CONSULT_REASON_CHOICE


def test_scene_change_trigger_when_no_choices(
    fixed_mode_base: ConsultInputs,
) -> None:
    decision = decide_consultation(
        ConsultInputs(
            **{**asdict(fixed_mode_base), "scene_changed": True}
        )
    )
    assert decision.should_consult is True
    assert decision.reason == CONSULT_REASON_SCENE_CHANGE


def test_progress_trigger_at_threshold(
    fixed_mode_base: ConsultInputs,
) -> None:
    decision = decide_consultation(
        ConsultInputs(
            **{
                **asdict(fixed_mode_base),
                "lines_since_last_consult": CONSULT_PROGRESS_LINE_THRESHOLD,
            }
        )
    )
    assert decision.should_consult is True
    assert decision.reason == CONSULT_REASON_STORY_PROGRESS


def test_progress_below_threshold_skips(
    fixed_mode_base: ConsultInputs,
) -> None:
    decision = decide_consultation(
        ConsultInputs(
            **{
                **asdict(fixed_mode_base),
                "lines_since_last_consult": CONSULT_PROGRESS_LINE_THRESHOLD - 1,
            }
        )
    )
    assert decision.should_consult is False
    assert decision.skip_reason == "no_trigger"


def test_cooldown_blocks_even_with_strong_trigger(
    fixed_mode_base: ConsultInputs,
) -> None:
    decision = decide_consultation(
        ConsultInputs(
            **{
                **asdict(fixed_mode_base),
                "visible_choices": ("a", "b"),
                "scene_changed": True,
                "last_consult_ts": fixed_mode_base.now - 5.0,
            }
        )
    )
    assert decision.should_consult is False
    assert decision.skip_reason == "cooldown"


def test_cooldown_elapses(fixed_mode_base: ConsultInputs) -> None:
    decision = decide_consultation(
        ConsultInputs(
            **{
                **asdict(fixed_mode_base),
                "visible_choices": ("a", "b"),
                "last_consult_ts": fixed_mode_base.now
                - (CONSULT_COOLDOWN_SECONDS + 1.0),
            }
        )
    )
    assert decision.should_consult is True
    assert decision.reason == CONSULT_REASON_CHOICE


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------


def test_build_consult_prompt_choice_includes_options() -> None:
    prompt = build_consult_prompt(
        reason=CONSULT_REASON_CHOICE,
        character_name="叢雨",
        character_voice_summary="孤高冷淡→嫌弃口吻；自称「わらわ」",
        scene_summary="神社，将臣发现叢雨偷偷找护身符",
        visible_choices=("坦率道谢", "装作不知"),
    )
    assert "你正在玩一款 galgame" in prompt
    assert "叢雨" in prompt
    assert "坦率道谢" in prompt
    assert "装作不知" in prompt
    assert "视角下的策略意见" in prompt
    assert "不是强制指令" in prompt
    assert "{character_name}" not in prompt
    assert "{scene_summary}" not in prompt


def test_build_consult_prompt_scene_change() -> None:
    prompt = build_consult_prompt(
        reason=CONSULT_REASON_SCENE_CHANGE,
        character_name="叢雨",
        character_voice_summary="孤高冷淡",
        scene_summary="新场景：海边",
    )
    assert "场景发生了变化" in prompt
    assert "叢雨" in prompt


def test_build_consult_prompt_story_progress_includes_recent_lines() -> None:
    prompt = build_consult_prompt(
        reason=CONSULT_REASON_STORY_PROGRESS,
        character_name="叢雨",
        character_voice_summary="孤高冷淡",
        scene_summary="神社",
        recent_lines=(
            "叢雨：わらわは別に気にしてなどおらぬ",
            "将臣：ありがとう",
        ),
    )
    assert "最近的剧情发展" in prompt
    assert "わらわは別に気にしてなどおらぬ" in prompt


def test_summarize_character_voice_uses_top_traits() -> None:
    voice = {
        "character_voice": {
            "core_traits": [
                {"trait": "孤高", "speech_effect": "嫌弃口吻"},
                {"trait": "嘴硬", "speech_effect": "句尾带ぞ"},
                {"trait": "其实柔软", "speech_effect": "语气会变弱"},
            ],
            "first_person_pronoun": "わらわ",
        }
    }
    summary = summarize_character_voice(voice)
    assert "孤高→嫌弃口吻" in summary
    assert "嘴硬→句尾带ぞ" in summary
    assert "其实柔软" not in summary
    assert "わらわ" in summary


def test_summarize_character_voice_handles_missing_voice() -> None:
    assert summarize_character_voice(None) == ""
    assert summarize_character_voice({"identity": "x"}) == ""
    assert summarize_character_voice({"character_voice": {}}) == ""


# ---------------------------------------------------------------------------
# Opinion injection
# ---------------------------------------------------------------------------


def test_inject_cat_opinion_appends_and_caps() -> None:
    shared: dict[str, object] = {}
    for i in range(MAX_CAT_OPINIONS + 3):
        inject_cat_opinion(
            shared,
            opinion=f"意见{i}",
            scene_id=f"scene_{i}",
            reason="choice",
            ts=float(1000 + i),
        )
    queue = shared["cat_opinions"]
    assert isinstance(queue, list)
    assert len(queue) == MAX_CAT_OPINIONS
    assert queue[0]["opinion"] == "意见3"
    assert queue[-1]["opinion"] == f"意见{MAX_CAT_OPINIONS + 2}"


def test_inject_cat_opinion_rejects_empty_text() -> None:
    shared: dict[str, object] = {}
    assert inject_cat_opinion(shared, opinion="   ") is None
    assert "cat_opinions" not in shared


def test_get_recent_cat_opinions_respects_n() -> None:
    shared: dict[str, object] = {}
    for i in range(4):
        inject_cat_opinion(shared, opinion=f"o{i}", ts=float(i))
    recent = get_recent_cat_opinions(shared, n=2)
    assert [item["opinion"] for item in recent] == ["o2", "o3"]


def test_render_cat_opinions_for_strategy_marks_reference() -> None:
    shared: dict[str, object] = {}
    inject_cat_opinion(shared, opinion="坦率说出来吧", reason="choice", ts=10.0)
    rendered = render_cat_opinions_for_strategy(shared)
    assert "Fixed-character POV advice" in rendered
    assert "not a command" in rendered
    assert "坦率说出来吧" in rendered
    assert "（choice）" in rendered


def test_render_cat_opinions_for_strategy_empty_returns_blank() -> None:
    assert render_cat_opinions_for_strategy({}) == ""


# ---------------------------------------------------------------------------
# Fire-and-forget contract
# ---------------------------------------------------------------------------


def test_no_recent_consult_allows_first_consult(
    fixed_mode_base: ConsultInputs,
) -> None:
    decision = decide_consultation(
        ConsultInputs(
            **{
                **asdict(fixed_mode_base),
                "visible_choices": ("a", "b"),
                "last_consult_ts": 0.0,
                "now": 0.5,
            }
        )
    )
    assert decision.should_consult is True


def test_decision_carries_character_name_even_when_skipped(
    fixed_mode_base: ConsultInputs,
) -> None:
    decision = decide_consultation(
        ConsultInputs(
            **{
                **asdict(fixed_mode_base),
                "last_consult_ts": fixed_mode_base.now - 1.0,
                "visible_choices": ("a", "b"),
            }
        )
    )
    assert decision.should_consult is False
    assert decision.character_name == "叢雨"
