"""Workshop author declarations survive projection without enabling old drafts."""

from copy import deepcopy

import pytest

from services.theater.numeric_v2 import NumericV2Compiler
from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator, _validate_idea_outline
from services.theater_workshop.sdk.numeric_v2_branch import NumericV2BranchService
from tests.unit.theater_workshop.numeric_v2_fixture import numeric_v2_setup
from tests.unit.theater_workshop.test_numeric_v2_generation import _fixed_piece, _idea_outline


@pytest.mark.parametrize("terminal", [False, True])
@pytest.mark.parametrize("flag", [None, False, True])
def test_mainline_preserves_only_explicit_fixed_entry_declaration(terminal, flag):
    outline = _idea_outline()
    source_index = len(outline["mainline_chapters"]) - 1 if terminal else 0
    source = outline["mainline_chapters"][source_index]
    if flag is not None:
        source["exit_plan"]["fixed_entry_only"] = flag
    target = outline["ending"] if terminal else outline["mainline_chapters"][1]
    stage = (outline["character_state_arc"]["ending_stage"] if terminal
             else outline["character_state_arc"]["stages"][1])
    target["fixed_narrations"] = [_fixed_piece(text="雨停后的花店里，她收好旧信，抬眼看向你。")]
    stage["acting_contract"]["dialogue_policy"] = "optional"
    before = deepcopy(outline)
    assert not _validate_idea_outline(outline, minimum=4, maximum=6)
    story = NumericV2Generator()._project_story(
        title="旧信", original_idea="一次自然的重逢", setup=numeric_v2_setup(), outline=outline, tone=["克制"],
    )
    NumericV2Compiler().compile(story)
    contract = story["nodes"][source_index]["route_gates"][0]["transition_contract"]
    if flag is None:
        assert "fixed_entry_only" not in contract
    else:
        assert contract["fixed_entry_only"] is flag
    assert outline == before


@pytest.mark.parametrize("case", ["not_bool", "no_entry", "required"])
def test_mainline_requests_repair_of_invalid_fixed_entry_declaration(case):
    outline = _idea_outline()
    outline["mainline_chapters"][0]["exit_plan"]["fixed_entry_only"] = "true" if case == "not_bool" else True
    if case == "required":
        outline["mainline_chapters"][1]["fixed_narrations"] = [_fixed_piece()]
    issues = _validate_idea_outline(outline, minimum=4, maximum=6)
    assert any(issue["code"] == "transition_fixed_entry_only_invalid"
               and issue["path"] == "mainline_chapters[0].exit_plan.fixed_entry_only" for issue in issues)


@pytest.mark.parametrize("extracted", [False, True])
@pytest.mark.parametrize("case", ["no_entry", "required"])
def test_staged_pass_defers_cross_chapter_entry_check_until_target_exists(case, extracted):
    """Neither validation pass of a chapter can repair a target chapter not written yet."""
    outline = _idea_outline()
    outline["mainline_chapters"][0]["exit_plan"]["fixed_entry_only"] = True
    if case == "required":
        outline["mainline_chapters"][1]["fixed_narrations"] = [_fixed_piece()]
    codes = {issue["code"] for issue in _validate_idea_outline(
        outline, minimum=4, maximum=6,
        # The state extractor's pass clears pending_state_index but leaves the
        # next chapter a stub, so this gate must not ride on that argument.
        pending_state_index=None if extracted else 0, pending_chapter_index=0)}
    assert "transition_fixed_entry_only_invalid" not in codes
    assert any(issue["code"] == "transition_fixed_entry_only_invalid"
               for issue in _validate_idea_outline(outline, minimum=4, maximum=6))


def test_situation_budget_check_follows_state_extraction_not_the_chapter_gate():
    """Only pending_state_index defers the situation budget; the chapter gate must not."""
    from utils.tokenize import count_tokens

    outline = _idea_outline()
    outline["mainline_chapters"][0]["catgirl_situation"] = "女主" + "甲" * 1200

    def codes(**kwargs):
        return {issue["code"] for issue in _validate_idea_outline(
            outline, minimum=4, maximum=6, token_counter=count_tokens, **kwargs)}

    assert "projected_prompt_field_too_large" in codes()
    # Before extraction the extractor still owns catgirl_situation, so its budget waits.
    assert "projected_prompt_field_too_large" not in codes(pending_state_index=0, pending_chapter_index=0)
    # After extraction the field is real and must be measured again, even though the
    # next chapter is still a stub and the cross-chapter gate stays closed.
    assert "projected_prompt_field_too_large" in codes(pending_chapter_index=0)


def test_staged_pass_keeps_cross_chapter_entry_check_for_written_chapters():
    """A declaration on an already written chapter still points at a written target."""
    outline = _idea_outline()
    outline["mainline_chapters"][0]["exit_plan"]["fixed_entry_only"] = True
    issues = _validate_idea_outline(outline, minimum=4, maximum=6, pending_chapter_index=1)
    assert any(issue["code"] == "transition_fixed_entry_only_invalid"
               and issue["path"] == "mainline_chapters[0].exit_plan.fixed_entry_only" for issue in issues)


@pytest.mark.parametrize("flag", [None, False, True])
def test_branch_contract_preserves_explicit_flag(flag):
    transition = {"reason": "此前交流已结束。", "bridge_scene_narration": "片刻后，雨停了。",
                  "must_preserve": [], "tone": "克制"}
    if flag is not None:
        transition["fixed_entry_only"] = flag
    result = NumericV2BranchService._transition_contract(transition, source_ids=["goal.closed"])
    if flag is None:
        assert "fixed_entry_only" not in result
    else:
        assert result["fixed_entry_only"] is flag
