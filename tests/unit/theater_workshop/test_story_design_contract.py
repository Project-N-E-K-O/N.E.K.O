"""Creative guidance reaches writing/review without becoming runtime conditions."""

from copy import deepcopy
import json

import pytest

from services.theater_workshop.sdk.generation.runtime_rules import STORY_DESIGN_RULE
from services.theater.numeric_v2 import NumericV2Compiler
from services.theater_workshop.sdk.generation.numeric_v2 import (
    NumericV2Generator, _MAINLINE_PROMPT, _MAINLINE_CONTINUATION_PROMPT,
    _BRANCH_PATH_PROMPT, _BRANCH_ENDING_PROMPT, _NODE_ENHANCEMENT_PROMPT,
    _validate_idea_outline,
)
from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor
from .test_staged_generation import staged_reply
from .test_numeric_v2_generation import (
    _idea_outline, _generation_setup, _quality_story_context, _quality_payload, _quality_reply,
)


def test_staged_writing_receives_design_guidance_once_and_preserves_the_existing_graph():
    outline, setup = _idea_outline(3), _generation_setup()
    original = deepcopy(outline)
    calls = []

    def model(messages, **options):
        operation = options["operation"]
        calls.append(operation)
        assert messages[0]["content"].count(STORY_DESIGN_RULE) == (
            1 if operation == "numeric_v2_story_frame" else 0)
        data = json.loads(messages[1]["content"])
        if operation in {"numeric_v2_chapter_generation", "numeric_v2_ending_generation"}:
            assert data["author_brief"] == setup["brief"]
        else:
            assert "author_brief" not in data
        return staged_reply(messages, operation, outline)

    result = NumericV2Generator(model).generate_staged(title="既有结构", setup=setup)
    assert len(calls) == 9
    assert outline == original
    NumericV2Compiler().compile_v2_2(result["story"])
    assert STORY_DESIGN_RULE not in json.dumps(result["story"], ensure_ascii=False)
    for prompt in (_MAINLINE_PROMPT, _MAINLINE_CONTINUATION_PROMPT, _BRANCH_PATH_PROMPT):
        assert prompt.count(STORY_DESIGN_RULE) == 1
    for prompt in (_BRANCH_ENDING_PROMPT, _NODE_ENHANCEMENT_PROMPT):
        assert STORY_DESIGN_RULE not in prompt


def test_literary_guidance_stays_out_of_fact_review_and_does_not_override_author_intent():
    story, authoring = _quality_story_context()
    original = deepcopy((story, authoring))
    reply = _quality_reply(_quality_payload())
    literary = []

    def model(messages, **options):
        system = messages[0]["content"]
        if options["operation"] == "numeric_v2_fact_review":
            assert STORY_DESIGN_RULE not in system
        else:
            literary.append(options["operation"])
            assert system.count(STORY_DESIGN_RULE) == 1
            assert "优劣本来明确或只是偏好选择" in system
            assert "人物可以成长、停滞或退步" in system
        return reply(messages, **options)

    report = NumericV2QualityAssessor(model).assess(story=story, setup=_generation_setup(), authoring=authoring)
    edges = {(node["id"], route["target_node_id"]) for node in story["nodes"] for route in node["route_gates"]}
    assert literary == ["numeric_v2_literary_block"] * len(edges) + ["numeric_v2_quality_assessment"]
    assert (story, authoring) == original
    assert report["issues"] == []


@pytest.mark.parametrize("field,name", [("story_protagonist", "女主"), ("player_role", "男主")])
@pytest.mark.parametrize("tail,valid", [("", False), (" 。…！ ", False), ("具体身份未交代。", True)])
def test_generation_requires_a_description_after_the_identity_name_without_inventing_an_occupation(field, name, tail, valid):
    outline = _idea_outline(3)
    outline[field]["identity"] = name + "，" + tail
    issues = _validate_idea_outline(outline, minimum=3, maximum=9)
    empty = [i for i in issues if i["code"] == "identity_description_empty"]
    assert bool(empty) is not valid
    if empty:
        assert empty[0]["path"] == field + ".identity"
