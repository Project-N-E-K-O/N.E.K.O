"""Prompt roles do not override author gender or the legacy slot protocol."""

import json

import pytest

from config.prompts.prompts_theater_pipeline import _FRAME_PROMPT, _STATE_PROMPT
from services.theater_workshop.sdk.generation import numeric_v2 as generation, runtime_rules
from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator, _validate_idea_outline
from .test_numeric_v2_generation import _generation_setup, _idea_outline


@pytest.mark.parametrize("prompt", [_FRAME_PROMPT, _STATE_PROMPT, generation._MAINLINE_PROMPT,
                                    runtime_rules.SCORING_RUNTIME_RULES], ids=["frame", "state", "mainline", "review"])
def test_prompt_roles_do_not_imply_gender(prompt):
    assert "不预设性别" in prompt
    assert "剧情主角必须是女性" not in prompt and "玩家角色必须是男性" not in prompt
    assert "女性剧情主角身份" not in prompt and "男性玩家角色身份" not in prompt
    assert "男主与女主" not in prompt


def test_generation_keeps_legacy_markers_but_preserves_explicit_author_gender():
    outline = _idea_outline()
    outline["story_protagonist"]["identity"] = "女主，作者明确为男性的修复师。"
    outline["player_role"]["identity"] = "男主，作者明确为女性的来访者。"
    setup = _generation_setup()
    setup["brief"] += "演出角色明确为男性，玩家明确为女性，保留这些作者设定。"
    calls = []

    def model(messages, **options):
        calls.append(messages)
        assert setup["brief"] in messages[1]["content"]
        return json.dumps(outline, ensure_ascii=False)

    story = NumericV2Generator(model).generate(title="角色槽位不代表性别", setup=setup)["story"]
    assert len(calls) == 1
    assert "作者明确为男性" in story["intro"]["catgirl_identity"]
    assert "作者明确为女性" in story["intro"]["player_identity"]
    assert not _validate_idea_outline(outline, minimum=3, maximum=6, scene_expected_turns_target=8)
    contract = json.loads(generation._MAINLINE_OUTPUT_CONTRACT)
    assert "cast_names.catgirl_name" in contract["story_protagonist"]["identity"]
    assert "‘女主，’" in contract["story_protagonist"]["identity"]
    assert "cast_names.player_name" in contract["player_role"]["identity"]
    assert "‘男主，’" in contract["player_role"]["identity"]
