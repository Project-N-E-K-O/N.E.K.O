"""A destination state stays at its own phase instead of becoming a source invariant."""
from copy import deepcopy

import pytest

from services.theater_workshop.sdk.generation.numeric_v2 import NumericV2Generator
from .test_numeric_v2_generation import _generation_setup, _idea_outline


@pytest.mark.parametrize("target_index", [1, 4])
@pytest.mark.parametrize("source_state,target_state", [
    ("设备尚未通电。", "设备已通电，指示灯亮起。"),
    ("展柜仍处于关闭状态。", "展柜已打开，展品在灯下可见。"),
])
def test_target_continuity_is_not_copied_into_source_preservation(target_index, source_state, target_state):
    outline = _idea_outline(4)
    source_index = target_index - 1
    outline["mainline_chapters"][source_index]["exit_plan"]["preserve_facts"] = [source_state]
    stage = (outline["character_state_arc"]["stages"][target_index] if target_index < 4
             else outline["character_state_arc"]["ending_stage"])
    stage["continuity_from_previous"] = [target_state]
    before = deepcopy(outline)
    package = NumericV2Generator()._project_story(title="阶段回归", original_idea="阶段状态不混合。",
        setup=_generation_setup(), outline=outline, tone=["自然"])
    invariant = package["nodes"][source_index]["route_gates"][0]["transition_contract"]["must_preserve"]
    assert source_state in invariant
    assert target_state not in invariant
    assert package["nodes"][target_index]["story_beat"]["character_state"]["continuity_from_previous"] == [target_state]
    assert outline == before
