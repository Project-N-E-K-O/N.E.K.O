from theater_workshop.host import InProcessPackageGateway
import json
from copy import deepcopy

import pytest

from theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator, NumericV2GenerationError
from theater_workshop.sdk.numeric_v2 import NumericV2Compiler
from .test_numeric_v2_generation import _idea_outline, _generation_setup


NAMES = {"player_name": "小明，二号", "catgirl_name": "小岚"}


def named_outline():
    return json.loads(json.dumps(_idea_outline(), ensure_ascii=False).replace("男主", NAMES["player_name"]).replace("女主", NAMES["catgirl_name"]))


def test_generate_receives_real_names_and_exports_explicit_binding():
    generator = NumericV2Generator()
    calls = []
    def reply(messages, **kwargs):
        calls.append(json.loads(messages[1]["content"]))
        return json.dumps(named_outline(), ensure_ascii=False)
    generator.call_llm = reply
    result = generator.generate(title="旧信", setup=_generation_setup(), cast_names=NAMES)
    assert len(calls) == 1
    assert calls[0]["cast_names"] == NAMES
    story = result["story"]
    assert story["intro"]["player_name"] == NAMES["player_name"]
    assert story["intro"]["catgirl_name"] == NAMES["catgirl_name"]
    assert story["intro"]["player_identity"].startswith(NAMES["player_name"] + "，")
    assert story["initial_state"]["player_address_known"] is False
    assert NumericV2Compiler(InProcessPackageGateway()).compile(story).story == story


def test_wrong_model_names_request_targeted_continuation_with_same_cast():
    generator = NumericV2Generator()
    calls = []
    def reply(messages, **kwargs):
        data = json.loads(messages[1]["content"])
        calls.append(data)
        candidate = named_outline()
        if len(calls) == 1:
            candidate["player_role"]["identity"] = "别名，故人。"
            return json.dumps(candidate, ensure_ascii=False)
        return json.dumps({"replacements": {"player_role.identity": named_outline()["player_role"]["identity"]}}, ensure_ascii=False)
    generator.call_llm = reply
    generator.generate(title="旧信", setup=_generation_setup(), cast_names=NAMES)
    assert len(calls) == 2
    assert calls[1]["cast_names"] == NAMES
    assert calls[1]["requested_paths"] == ["player_role.identity"]


@pytest.mark.parametrize("names", [{}, {"player_name": "阿晨"}, {"player_name": "同名", "catgirl_name": "同名"}])
def test_invalid_cast_is_rejected_before_model_call(names):
    generator = NumericV2Generator()
    def unexpected(*args, **kwargs):
        pytest.fail("invalid names must not call a model")
    generator.call_llm = unexpected
    with pytest.raises(NumericV2GenerationError):
        generator.generate(title="旧信", setup=_generation_setup(), cast_names=names)


def test_resume_uses_saved_names_instead_of_mixing_current_character():
    generator = NumericV2Generator()
    generator.call_llm = lambda *a, **k: pytest.fail("complete checkpoint must not call a model")
    checkpoint = {"candidate": named_outline(), "cast_names": NAMES}
    original = deepcopy(checkpoint)
    result = generator.generate(title="旧信", setup=_generation_setup(), checkpoint=checkpoint,
                                cast_names={"player_name": "新昵称", "catgirl_name": "新猫娘"})
    assert result["story"]["intro"]["player_name"] == NAMES["player_name"]
    assert checkpoint == original


def test_failed_generation_saves_names_for_continuation():
    generator = NumericV2Generator()
    candidate = named_outline()
    candidate["player_role"]["identity"] = "别名，故人。"
    generator.call_llm = lambda *a, **k: json.dumps(candidate, ensure_ascii=False)
    with pytest.raises(NumericV2GenerationError) as caught:
        generator.generate(title="旧信", setup=_generation_setup(), cast_names=NAMES)
    assert caught.value.checkpoint["cast_names"] == NAMES
    assert caught.value.checkpoint["candidate"] == candidate


def test_legacy_checkpoint_remains_in_legacy_slots():
    generator = NumericV2Generator()
    generator.call_llm = lambda *a, **k: pytest.fail("complete checkpoint must not call a model")
    result = generator.generate(title="旧信", setup=_generation_setup(),
                                checkpoint={"candidate": _idea_outline()}, cast_names=NAMES)
    assert "player_name" not in result["story"]["intro"]
    assert result["story"]["intro"]["player_identity"].startswith("男主，")


def test_enhancement_and_review_receive_existing_project_names():
    from theater_workshop.sdk.generation.quality import NumericV2QualityAssessor
    from .test_numeric_v2_generation import _ending_enhancement_case

    generator, _, candidate = _ending_enhancement_case()
    story = generator._project_story(title="旧信", original_idea="旧信",
        setup=_generation_setup(), outline=named_outline(), tone=["克制"], cast_names=NAMES)
    candidate = json.loads(json.dumps(candidate, ensure_ascii=False).replace("男主", NAMES["player_name"]).replace("女主", NAMES["catgirl_name"]))
    calls = []
    def reply(messages, **kwargs):
        calls.append(json.loads(messages[1]["content"]))
        return json.dumps(candidate, ensure_ascii=False)
    generator.call_llm = reply
    original = deepcopy(story)
    enhanced = generator.enhance_node(story=story, node_id="ending_normal")
    assert enhanced["character_state"]["player_state"].startswith(NAMES["player_name"])
    assert calls[0]["story_intro"]["player_name"] == NAMES["player_name"]
    context = NumericV2QualityAssessor._assessment_context(story, _generation_setup(), {
        "mainline_node_ids": [n["id"] for n in story["nodes"] if n["type"] != "ending"],
    })
    assert context["characters"]["player_name"] == NAMES["player_name"]
    assert context["characters"]["catgirl_name"] == NAMES["catgirl_name"]
    assert story == original


def test_branch_path_preserves_named_state_and_rejects_swapped_roles():
    from theater_workshop.sdk.numeric_v2_branch import NumericV2BranchService, NumericV2BranchError
    from .test_numeric_v2_branch import branchable_project, _path_result

    project = branchable_project()
    project["story"]["intro"].update(NAMES)
    service = NumericV2BranchService()
    plan = service.prepare_path(project, source_node_id="main_3", endpoint_mode="mainline",
        endpoint_node_id="main_4", direction="核对旧照片。", length=1,
        condition_selection={"mode": "recommend"})
    candidate = json.loads(json.dumps(_path_result(plan), ensure_ascii=False)
        .replace("男主", NAMES["player_name"]).replace("女主", NAMES["catgirl_name"]))
    draft = service.finish_path(plan, candidate)
    assert draft["scenes"][0]["character_state"]["player_state"].startswith(NAMES["player_name"])
    candidate["scenes"][0]["character_state"]["player_state"] = "小岚坐在桌旁。"
    with pytest.raises(NumericV2BranchError):
        service.finish_path(plan, candidate)
