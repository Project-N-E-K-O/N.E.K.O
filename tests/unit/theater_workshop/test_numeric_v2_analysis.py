from __future__ import annotations
from theater_workshop.host import InProcessPackageGateway

from copy import deepcopy

from theater_workshop.sdk.numeric_v2 import NumericV2Compiler
from theater_workshop.sdk.numeric_v2_analysis import analyze_numeric_v2_story
from .numeric_v2_fixture import numeric_v2_story


def _codes(story: dict) -> set[str]:
    return {warning.code for warning in analyze_numeric_v2_story(story)}


def test_full_story_analysis_keeps_recommended_turns_as_soft_pacing_warning():
    story = numeric_v2_story()

    compiled = NumericV2Compiler(InProcessPackageGateway()).compile(story)
    codes = {warning.code for warning in compiled.warnings}

    assert compiled.package_hash.startswith("sha256:")
    assert "route_pacing_difficult" in codes
    assert "ending_pacing_difficult" in codes
    assert "route_mathematically_impossible" not in codes


def test_full_story_analysis_distinguishes_priority_shadow_and_numeric_unreachable():
    story = numeric_v2_story()
    routes = story["nodes"][0]["route_gates"]
    routes[0]["conditions"]["all"][0].update({"op": ">=", "value": 70})
    routes[0]["priority"] = 20
    routes[1]["conditions"]["all"][0].update({"op": ">=", "value": 80})
    routes[1]["priority"] = 10

    compiled = NumericV2Compiler(InProcessPackageGateway()).compile(story)
    codes = {warning.code for warning in compiled.warnings}

    # The lower-priority route's entire condition space is covered, but this remains an author diagnostic rather than a compilation failure for a valid package.
    assert "route_priority_shadowed" in codes
    assert "ending_numeric_unreachable" in codes
    assert "route_condition_gap" in codes


def test_full_story_analysis_reports_mathematical_impossibility_independently():
    story = numeric_v2_story()
    story["nodes"][0]["route_gates"][0]["conditions"]["all"][0].update({
        "op": ">",
        "value": 100,
    })

    assert "route_mathematically_impossible" in _codes(story)


def test_full_story_analysis_marks_cycles_as_unknown_instead_of_guessing():
    story = numeric_v2_story()
    start = story["nodes"][0]
    ending_leave = story["nodes"][2]
    story["nodes"] = [
        {
            **start,
            "route_gates": [{
                **start["route_gates"][0],
                "id": "to_loop",
                "target_node_id": "loop",
                "priority": 10,
                "conditions": {"all": []},
            }],
        },
        {
            "id": "loop",
            "type": "scene",
            "chapter": "回望",
            "min_turns": 2,
            "recommended_turns": 4,
            "story_beat": start["story_beat"],
            "route_gates": [
                {
                    **start["route_gates"][0],
                    "id": "loop_back",
                    "target_node_id": "start",
                    "priority": 20,
                    "conditions": {"all": [{
                        "type": "metric_compare",
                        "metric": "trust",
                        "op": ">=",
                        "value": 30,
                    }]},
                },
                {
                    **start["route_gates"][1],
                    "id": "loop_end",
                    "target_node_id": "ending_leave",
                    "priority": 10,
                    "conditions": {"all": [{
                        "type": "metric_compare",
                        "metric": "trust",
                        "op": "<",
                        "value": 30,
                    }]},
                },
            ],
        },
        ending_leave,
    ]
    story["endings"] = [story["endings"][1]]

    assert "route_analysis_unknown" in _codes(story)


def test_exact_duplicate_sibling_transitions_are_only_soft_warnings():
    story = numeric_v2_story()

    compiled = NumericV2Compiler(InProcessPackageGateway()).compile(story)

    assert "route_transition_duplicate" in {warning.code for warning in compiled.warnings}

    story["nodes"][0]["route_gates"][1]["transition_contract"]["reason"] = "她决定独自离开。"
    assert "route_transition_duplicate" not in _codes(story)


def test_full_story_analysis_does_not_call_unknown_downstream_unreachable():
    story = numeric_v2_story()
    base_metric = story["metric_schema"]["trust"]
    for metric_id in ("clue", "courage", "pressure"):
        definition = deepcopy(base_metric)
        definition["name"] = metric_id
        story["metric_schema"][metric_id] = definition
        story["initial_state"]["metrics"][metric_id] = definition["initial"]

    routes = []
    template = story["nodes"][0]["route_gates"][0]
    for index, threshold in enumerate((10, 25, 40, 55, 70)):
        route = deepcopy(template)
        route.update({
            "id": f"route_{index}",
            "target_node_id": "ending_stay" if index % 2 else "ending_leave",
            "priority": 100 - index,
            "conditions": {
                "any": [
                    {
                        "type": "metric_compare",
                        "metric": metric_id,
                        "op": ">=",
                        "value": threshold,
                    }
                    for metric_id in ("trust", "clue", "courage", "pressure")
                ]
            },
        })
        routes.append(route)
    story["nodes"][0]["route_gates"] = routes

    warnings = NumericV2Compiler(InProcessPackageGateway()).compile(story).warnings
    codes = {warning.code for warning in warnings}

    # When state combinations exceed the exact-enumeration limit, mark only unknown; do not infer that structurally downstream nodes are unreachable.
    assert "route_analysis_unknown" in codes
    assert "node_numeric_unreachable" not in codes
    assert "ending_numeric_unreachable" not in codes


def test_full_story_analysis_suppresses_pacing_claims_across_cycles():
    story = numeric_v2_story()
    start = story["nodes"][0]
    ending = story["nodes"][1]
    template = start["route_gates"][0]
    story["metric_schema"]["trust"]["initial"] = 0
    story["initial_state"]["metrics"]["trust"] = 0
    start.update({"min_turns": 3, "recommended_turns": 3})
    start["route_gates"] = [{
        **deepcopy(template),
        "id": "to_loop",
        "target_node_id": "loop",
        "priority": 10,
        "conditions": {"all": []},
    }]
    loop = {
        "id": "loop",
        "type": "scene",
        "chapter": "循环",
        "min_turns": 3,
        "recommended_turns": 3,
        "story_beat": deepcopy(start["story_beat"]),
        "route_gates": [
            {
                **deepcopy(template),
                "id": "loop_exit",
                "target_node_id": "ending_stay",
                "priority": 20,
                "conditions": {"all": [{
                    "type": "metric_compare",
                    "metric": "trust",
                    "op": ">=",
                    "value": 45,
                }]},
            },
            {
                **deepcopy(template),
                "id": "loop_back",
                "target_node_id": "start",
                "priority": 10,
                "conditions": {"all": [{
                    "type": "metric_compare",
                    "metric": "trust",
                    "op": "<",
                    "value": 45,
                }]},
            },
        ],
    }
    story["nodes"] = [start, loop, ending]
    story["endings"] = [story["endings"][0]]

    codes = {warning.code for warning in NumericV2Compiler(InProcessPackageGateway()).compile(story).warnings}

    # Both loops can reach the threshold within three turns each, so the first visit must not imply 'extra turns required'.
    assert "route_analysis_unknown" in codes
    assert "route_pacing_difficult" not in codes
    assert "route_pacing_partial" not in codes
    assert "ending_pacing_difficult" not in codes


def test_full_story_analysis_keeps_non_convex_entry_bounds_unknown():
    story = numeric_v2_story()
    start = story["nodes"][0]
    stay = story["nodes"][1]
    leave = story["nodes"][2]
    template = start["route_gates"][0]
    story["metric_schema"]["trust"]["initial"] = 50
    story["initial_state"]["metrics"]["trust"] = 50
    start.update({"min_turns": 3, "recommended_turns": 10})
    start["route_gates"] = [
        {
            **deepcopy(template),
            "id": "to_extreme_scene",
            "target_node_id": "middle",
            "priority": 20,
            "conditions": {"any": [
                {"type": "metric_compare", "metric": "trust", "op": "<=", "value": 10},
                {"type": "metric_compare", "metric": "trust", "op": ">=", "value": 90},
            ]},
        },
        {
            **deepcopy(template),
            "id": "to_leave",
            "target_node_id": "ending_leave",
            "priority": 10,
            "conditions": {"all": [
                {"type": "metric_compare", "metric": "trust", "op": ">", "value": 10},
                {"type": "metric_compare", "metric": "trust", "op": "<", "value": 90},
            ]},
        },
    ]
    middle_route = deepcopy(template)
    middle_route.update({
        "id": "middle_to_stay",
        "target_node_id": "ending_stay",
        "priority": 10,
        "conditions": {"all": [
            {"type": "metric_compare", "metric": "trust", "op": ">=", "value": 40},
            {"type": "metric_compare", "metric": "trust", "op": "<=", "value": 60},
        ]},
    })
    middle = {
        "id": "middle",
        "type": "scene",
        "chapter": "两端之间",
        "min_turns": 1,
        "recommended_turns": 1,
        "story_beat": deepcopy(start["story_beat"]),
        "route_gates": [middle_route],
    }
    story["nodes"] = [start, middle, stay, leave]

    warnings = NumericV2Compiler(InProcessPackageGateway()).compile(story).warnings
    codes = {warning.code for warning in warnings}

    # <=10 and >=90 are disjoint entry ranges; a 0..100 bounding box must not invent intermediate values and propagate them.
    assert "route_analysis_unknown" in codes
    assert "ending_pacing_difficult" not in codes


def test_recommended_budget_flags_branch_that_requires_near_maximum_changes():
    # Twelve rounds at +5 can reach it, but ordinary +2 rounds cannot; do not equate theoretical extremes with everyday experience.
    story = numeric_v2_story()
    story["nodes"][0]["recommended_turns"] = 12
    original = deepcopy(story)
    codes = _codes(story)
    assert "route_pacing_difficult" not in codes
    assert "route_normal_pacing_difficult" in codes
    assert story == original


def test_normal_budget_does_not_warn_when_branch_is_within_normal_range():
    # The hint disappears when the threshold falls within the normal-intensity budget; diagnostics compute possible intervals without automatically awarding story points.
    story = numeric_v2_story()
    story["nodes"][0]["recommended_turns"] = 12
    for route in story["nodes"][0]["route_gates"]:
        route["conditions"]["all"][0]["value"] = 40
    codes = _codes(story)
    assert "route_normal_pacing_difficult" not in codes


def test_normal_budget_accumulates_upstream_but_never_future_turns():
    # Three rounds in each of two acts can add 12 points total; count more than the entry act, but never borrow post-ending rounds for entry.
    story = numeric_v2_story()
    start = story["nodes"][0]
    middle = deepcopy(start)
    middle.update(id="middle", type="scene", min_turns=3, recommended_turns=3)
    start.update(min_turns=3, recommended_turns=3)
    start["route_gates"] = [{
        **deepcopy(start["route_gates"][0]), "id": "to_middle",
        "target_node_id": "middle", "conditions": {"all": []},
    }]
    for route in middle["route_gates"]:
        route["conditions"]["all"][0]["value"] = 30
    story["nodes"].insert(1, middle)
    assert "route_normal_pacing_difficult" not in _codes(story)
    for route in middle["route_gates"]:
        route["conditions"]["all"][0]["value"] = 34
    assert "route_normal_pacing_difficult" in _codes(story)


def test_two_point_estimate_does_not_follow_model_strength_mapping():
    # A declared cap of 3 still allows 2 points per round; do not arbitrarily double the budget because the model's normal tier might map to 1.
    story = numeric_v2_story()
    story["metric_schema"]["trust"]["per_turn_limit"] = {"increase": 3, "decrease": 3}
    story["nodes"][0]["recommended_turns"] = 12
    for route in story["nodes"][0]["route_gates"]:
        route["conditions"]["all"][0]["value"] = 40
    assert "route_normal_pacing_difficult" not in _codes(story)
