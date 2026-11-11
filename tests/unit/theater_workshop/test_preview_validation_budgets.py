"""Apply native text budgets before showing or saving AI-authored changes."""

from copy import deepcopy
import json

import pytest

from services.theater.numeric_v2 import MAX_ACTOR_PROMPT_FIELD_TOKENS, NumericV2Compiler
from services.theater.numeric_v2_fixed_narration import MAX_FIXED_NARRATION_TOKENS
from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2GenerationError
from services.theater_workshop.sdk.numeric_v2_branch import NumericV2BranchError, validate_branch_original_budget
from utils.tokenize import count_tokens

from .test_numeric_v2_branch import branchable_project, _path_result_from_context
from .test_numeric_v2_generation import _ending_enhancement_case
from .test_sdk_lifecycle import opened


def _branch_project(sdk):
    example = branchable_project()
    project = sdk.import_story(example["story"])
    return sdk.set_mainline_order(project["project_id"], base_revision=project["revision"],
                                  node_ids=example["authoring"]["mainline_node_ids"])


def test_branch_preview_budget_matches_native_boundary():
    validate_branch_original_budget(["原文"], "fixed_narrations", token_counter=lambda _: MAX_FIXED_NARRATION_TOKENS)
    with pytest.raises(NumericV2BranchError, match="fixed_narration_budget_exceeded") as error:
        validate_branch_original_budget(["原文"], "fixed_narrations",
                                        token_counter=lambda _: MAX_FIXED_NARRATION_TOKENS + 1)
    assert error.value.details["limit"] == MAX_FIXED_NARRATION_TOKENS


@pytest.mark.parametrize("mode", ["ending", "path", "same_chapter"])
def test_known_oversized_author_originals_stop_before_the_model(opened, monkeypatch, mode):
    sdk = opened[0].sdk
    project = _branch_project(sdk)
    original = "蓝桥锁仓落叶供电端口。" * (110 if mode == "same_chapter" else 280)
    assert (count_tokens(original) > MAX_FIXED_NARRATION_TOKENS) is (mode != "same_chapter")
    assert count_tokens(original) * 2 > MAX_FIXED_NARRATION_TOKENS
    direction = f"支线第一幕入幕展示[原文起]{original}[原文止]。"
    if mode == "same_chapter":
        direction += f"支线第一幕入幕展示[原文起]{original}[原文止]。"
    monkeypatch.setattr(sdk._generator, "call_llm", lambda *a, **k: pytest.fail("invalid input reached the model"))
    with pytest.raises(NumericV2GenerationError, match="invalid_author_original") as error:
        if mode == "ending":
            sdk.draft_branch_ending(project["project_id"], base_revision=project["revision"], source_node_id="main_1",
                                   ending_direction=direction, condition_selection={"mode": "recommend"})
        else:
            sdk.draft_branch_path(project["project_id"], base_revision=project["revision"], source_node_id="main_1",
                                 endpoint_mode="existing_ending", endpoint_node_id="ending_normal", direction=direction,
                                 length=1, condition_selection={"mode": "recommend"})
    assert error.value.issues[0]["code"] == "author_passages_budget_exceeded"
    assert sdk.get_project(project["project_id"]) == project


def test_branch_preview_and_old_apply_recheck_combined_original_budget(opened, monkeypatch):
    sdk = opened[0].sdk
    project = _branch_project(sdk)
    project_id, revision = project["project_id"], project["revision"]
    text = "蓝桥锁仓落叶供电端口。" * 110
    pieces = [{"id": item, "text": text, "trigger": {"type": "entry"}, "after": [],
               "required_before_exit": False} for item in ("record_a", "record_b")]
    assert count_tokens(text) < MAX_FIXED_NARRATION_TOKENS < 2 * count_tokens(text)
    calls = []

    def model(messages, **options):
        calls.append(options["operation"])
        result = _path_result_from_context(json.loads(messages[1]["content"]))
        result["scenes"][0]["fixed_narrations"] = pieces if len(calls) <= 2 else pieces[:1]
        result["scenes"][0]["completion_facts"] = [{"id": "log_displayed", "description": "日志已展示。",
            "value_type": "bool", "target_value": True, "visibility": "public", "fixed_narration_id": "record_a"}]
        result["transitions"][1]["trigger_fact_ids"] = ["log_displayed"]
        return json.dumps(result, ensure_ascii=False)

    monkeypatch.setattr(sdk._generator, "call_llm", model)
    params = dict(base_revision=revision, source_node_id="main_1", endpoint_mode="existing_ending",
                  endpoint_node_id="ending_normal", direction="在支线调查中公开日志。", length=1,
                  condition_selection={"mode": "recommend"})
    with pytest.raises(NumericV2BranchError, match="fixed_narration_budget_exceeded"):
        sdk.draft_branch_path(project_id, **params)
    assert sdk.get_project(project_id) == project

    draft = sdk.draft_branch_path(project_id, **params)["draft"]
    old = deepcopy(draft)
    old["scenes"][0]["fixed_narrations"] = pieces
    sdk._store.save_branch_draft(project_id, base_revision=revision, draft=old)
    project_file = sdk.root / f"{project_id}.json"
    before = project_file.read_bytes()
    with pytest.raises(NumericV2BranchError, match="fixed_narration_budget_exceeded"):
        sdk.apply_branch(project_id, old["draft_id"], base_revision=revision)
    assert project_file.read_bytes() == before

    sdk._store.save_branch_draft(project_id, base_revision=revision, draft=draft)
    updated = sdk.apply_branch(project_id, draft["draft_id"], base_revision=revision)["project"]
    assert NumericV2Compiler().compile_v2_2(updated["story"]).story == updated["story"]
    assert calls == ["numeric_v2_branch_path"] * 3


@pytest.mark.parametrize("invalid", ["missing_entry", "required"])
def test_fixed_entry_branch_preview_and_old_apply_validate_the_target(opened, monkeypatch, invalid):
    sdk = opened[0].sdk
    project = _branch_project(sdk)
    project_id, revision = project["project_id"], project["revision"]
    pieces = [{"id": "report", "text": "原文：一份档案已经入库。", "trigger": {"type": "entry"},
               "after": [], "required_before_exit": False}]
    generated = []

    def invalidate(scene):
        if invalid == "missing_entry":
            scene.pop("fixed_narrations")
        else:
            scene["character_state"]["acting_contract"]["dialogue_policy"] = "required"

    def model(messages, **_options):
        assert "才可用 fixed_entry_only 独立展示原文" in messages[0]["content"]
        result = _path_result_from_context(json.loads(messages[1]["content"]))
        scene = result["scenes"][0]
        scene["fixed_narrations"] = pieces
        scene["character_state"]["acting_contract"]["dialogue_policy"] = "optional"
        scene["completion_facts"] = [{"id": "seen", "description": "档案内容已公开。",
            "value_type": "bool", "target_value": True, "visibility": "public"}]
        result["transitions"][0]["fixed_entry_only"] = True
        result["transitions"][1]["trigger_fact_ids"] = ["seen"]
        if len(generated) < 2:
            invalidate(scene)
        generated.append(result)
        return json.dumps(result, ensure_ascii=False)

    monkeypatch.setattr(sdk._generator, "call_llm", model)
    params = dict(base_revision=revision, source_node_id="main_1", endpoint_mode="existing_ending",
                  endpoint_node_id="ending_normal", direction="支线首幕只播放固定旁白。", length=1,
                  condition_selection={"mode": "recommend"})
    project_file = sdk.root / f"{project_id}.json"
    before = project_file.read_bytes()
    with pytest.raises(NumericV2BranchError, match="transition_fixed_entry_only_invalid") as error:
        sdk.draft_branch_path(project_id, **params)
    assert error.value.details["path"] == "transitions[0].fixed_entry_only"
    assert project_file.read_bytes() == before

    draft = sdk.draft_branch_path(project_id, **params)["draft"]
    old = deepcopy(draft)
    invalidate(old["scenes"][0])
    sdk._store.save_branch_draft(project_id, base_revision=revision, draft=old)
    before = project_file.read_bytes()
    with pytest.raises(NumericV2BranchError, match="transition_fixed_entry_only_invalid"):
        sdk.apply_branch(project_id, old["draft_id"], base_revision=revision)
    assert project_file.read_bytes() == before

    sdk._store.save_branch_draft(project_id, base_revision=revision, draft=draft)
    updated = sdk.apply_branch(project_id, draft["draft_id"], base_revision=revision)["project"]
    assert NumericV2Compiler().compile_v2_2(updated["story"]).story == updated["story"]


@pytest.mark.parametrize("case", [
    "valid", "invalid_opening", "invalid_situation", "invalid_focus", "invalid_transition",
    "invalid_native_boundary", "unrelated_old_field", "old_and_new_native_errors",
])
def test_node_enhancement_validates_new_projected_text_before_saving(opened, monkeypatch, case):
    sdk = opened[0].sdk
    _, story, candidate = _ending_enhancement_case()
    project = sdk.import_story(story)
    oversized = "窗台上有一排待清点的黑色盒子。" * 120
    assert count_tokens(oversized) > MAX_ACTOR_PROMPT_FIELD_TOKENS
    if case == "invalid_opening":
        candidate["opening_scene"] = oversized
    if case == "invalid_focus":
        candidate["narrative_focus"] = oversized
    if case == "invalid_transition":
        candidate["transition_goal"] = oversized
    if case in {"invalid_native_boundary", "old_and_new_native_errors"}:
        candidate["must_not_happen"] = [oversized]
    if case == "invalid_situation":
        state = candidate["character_state"]
        subjects = {"catgirl_state": "女主", "player_state": "男主", "environment_state": "环境"}
        for field, subject in subjects.items():
            state[field] = subject + "记录地点的具体灯光方向。" * 18
        sizes = [count_tokens(state[field]) for field in subjects]
        assert max(sizes) < MAX_ACTOR_PROMPT_FIELD_TOKENS < sum(sizes)
    if case in {"unrelated_old_field", "old_and_new_native_errors"}:
        old_story = deepcopy(story)
        old_story["nodes"][0]["story_beat"]["opening_scene"] = oversized
        project = sdk.update_project(project["project_id"], base_revision=project["revision"],
                                     changes={"story": old_story})
    project_id, revision = project["project_id"], project["revision"]
    project_file = sdk.root / f"{project_id}.json"
    before = project_file.read_bytes()
    monkeypatch.setattr(sdk._generator, "call_llm", lambda *a, **k: json.dumps(candidate, ensure_ascii=False))
    invalid_fields = {
        "invalid_opening": "opening_scene", "invalid_situation": "catgirl_situation",
        "invalid_focus": "narrative_focus", "invalid_transition": "transition_goal",
        "invalid_native_boundary": "must_not_happen[0]", "old_and_new_native_errors": "must_not_happen[0]",
    }
    if case in invalid_fields:
        with pytest.raises(NumericV2GenerationError, match="invalid_node_enhancement") as error:
            sdk.enhance_node(project_id, "ending_normal", base_revision=revision)
        assert [issue["code"] for issue in error.value.issues] == ["actor_prompt_field_too_large"]
        assert error.value.issues[0]["path"].rsplit(".", 1)[-1] == invalid_fields[case]
        assert project_file.read_bytes() == before
    else:
        updated = sdk.enhance_node(project_id, "ending_normal", base_revision=revision)["project"]
        assert updated["revision"] == revision + 1
        assert updated["story"]["nodes"][-1]["story_beat"]["opening_scene"] == candidate["opening_scene"]
        if case == "valid":
            assert NumericV2Compiler().compile_v2_2(updated["story"]).story == updated["story"]
        else:
            assert updated["story"]["nodes"][0] == project["story"]["nodes"][0]


@pytest.mark.parametrize("case", ["preserve_silent", "remove_silence", "remove_final_silence", "silence_confirmation"])
def test_node_enhancement_preserves_existing_exit_speech_contract(opened, monkeypatch, case):
    sdk = opened[0].sdk
    _, story, candidate = _ending_enhancement_case()
    node = story["nodes"][0]
    beat = node["story_beat"]
    contract = node["route_gates"][0]["transition_contract"]
    new_policy = candidate["character_state"]["acting_contract"]
    candidate["character_state"]["continuity_from_previous"] = []

    if case == "silence_confirmation":
        contract["accept_input_confirmation_only"] = True
        new_policy["dialogue_policy"] = "forbidden"
    else:
        contract.pop("fallback_offer")
        contract["silent_choice"] = {"destination": "观测舱", "arrangement": "查看封存记录"}
        if case == "remove_final_silence":
            beat["acting_contract"]["dialogue_policy"] = "optional"
            beat["goals"][-1]["delivery"]["state_effects"] = {"dialogue_policy": "forbidden"}
            new_policy["dialogue_policy"] = "optional"
        else:
            beat["acting_contract"]["dialogue_policy"] = "forbidden"
            new_policy["dialogue_policy"] = "forbidden" if case == "preserve_silent" else "required"

    NumericV2Compiler().compile_v2_2(story)
    project = sdk.import_story(story)
    project_id, revision = project["project_id"], project["revision"]
    project_file = sdk.root / f"{project_id}.json"
    before = project_file.read_bytes()

    def model(messages, **_options):
        route = json.loads(messages[1]["content"])["outgoing_routes"][0]
        assert route["transition_contract"] == {key: contract[key] for key in (
            "fallback_offer", "silent_choice", "accept_input_confirmation_only",
        ) if key in contract}
        return json.dumps(candidate, ensure_ascii=False)

    monkeypatch.setattr(sdk._generator, "call_llm", model)
    if case == "preserve_silent":
        updated = sdk.enhance_node(project_id, node["id"], base_revision=revision)["project"]
        updated_contract = updated["story"]["nodes"][0]["route_gates"][0]["transition_contract"]
        assert updated_contract["silent_choice"] == contract["silent_choice"]
        assert "fallback_offer" not in updated_contract
        NumericV2Compiler().compile_v2_2(updated["story"])
    else:
        with pytest.raises(NumericV2GenerationError, match="invalid_node_enhancement") as error:
            sdk.enhance_node(project_id, node["id"], base_revision=revision)
        assert error.value.issues[0]["code"] == "transition_dialogue_policy_conflict"
        assert project_file.read_bytes() == before
