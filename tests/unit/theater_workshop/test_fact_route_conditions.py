"""Fact-based routes survive workshop import and remain unknown to metric-only analysis."""

from copy import deepcopy

import pytest

from services.theater_workshop.sdk.generation.quality import NumericV2QualityAssessor
from services.theater_workshop.sdk.numeric_v2_analysis import analyze_numeric_v2_story
from services.theater_workshop.sdk.numeric_v2_branch import NumericV2BranchService
from tests.unit.test_theater_numeric_v2_fact_conditions import fact_route_story


@pytest.mark.parametrize("with_metrics", [True, False])
def test_fact_route_analysis_is_explicitly_unknown_without_false_unreachable_claims(with_metrics):
    story = fact_route_story()
    if not with_metrics:
        story["metric_schema"] = {}
        story["initial_state"]["metrics"] = {}
    codes = {warning.code for warning in analyze_numeric_v2_story(story)}
    assert "route_analysis_unknown" in codes
    assert not codes.intersection({"route_condition_gap", "route_mathematically_impossible",
                                   "route_priority_shadowed", "ending_numeric_unreachable",
                                   "route_pacing_difficult", "ending_pacing_difficult"})


def test_quality_branch_projection_keeps_fact_key_and_typed_value():
    story = fact_route_story()
    branch = deepcopy(story["nodes"][0])
    branch.update(id="mountain_path", type="scene")
    branch["route_gates"] = [deepcopy(story["nodes"][0]["route_gates"][1])]
    branch["route_gates"][0].update(id="mountain_finish", conditions={"all": []})
    story["nodes"].append(branch)
    story["nodes"][0]["route_gates"][1]["target_node_id"] = "mountain_path"
    before = deepcopy(story)
    context = NumericV2QualityAssessor._assessment_context(story, {}, {"mainline_node_ids": ["start"]})
    assert context["branches"][0]["entry_condition"]["rules"] == [
        {"type": "fact_equals", "key": "scene:start:route_decided", "value": "山径"},
    ]
    assert story == before


@pytest.mark.parametrize("mode", ["all", "any"])
def test_metric_branch_factory_does_not_guess_fact_based_entrances(mode):
    story = fact_route_story()
    rows = story["nodes"][0]["route_gates"][0]["conditions"]["all"]
    rows.append({"type": "metric_compare", "metric": "trust", "op": ">=", "value": 50})
    assert NumericV2BranchService._condition_alternatives(
        {mode: rows}, story["metric_schema"], "trust", 20,
    ) is None
