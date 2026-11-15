"""Typed fact route conditions retain deterministic, mutually exclusive exits."""

from copy import deepcopy
from itertools import product
import operator
import random

import pytest

from services.theater.numeric_v2 import NumericV2CompileError, NumericV2Compiler, _conditions_overlap
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story


def fact_route_story(value_type="string", values=("官道", "山径")):
    story = numeric_v2_story()
    key = "scene:start:route_decided"
    story["fact_contract"] = {"facts": {key: {
        "value_type": value_type, "visibility": "story",
        "description": "玩家明确选定的行进路线。",
    }}}
    for route, value in zip(story["nodes"][0]["route_gates"], values):
        route["priority"] = 10
        route["conditions"] = {"all": [{"type": "fact_equals", "key": key, "value": value}]}
    return story


@pytest.mark.parametrize("value_type,values", [("string", ("官道", "山径")), ("bool", (True, False)), ("int", (1, 2))])
def test_distinct_fact_values_compile_as_mutually_exclusive_exits(value_type, values):
    story = fact_route_story(value_type, values)
    compiled = NumericV2Compiler().compile_v2_2(story)
    assert compiled.story["nodes"][0]["route_gates"] == story["nodes"][0]["route_gates"]


@pytest.mark.parametrize("value_type,value", [("bool", 1), ("int", True), ("string", 1), ("string", ""), ("string", " 山径 ")])
def test_fact_condition_value_must_match_declared_type(value_type, value):
    story = fact_route_story(value_type, (value, value))
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile_v2_2(story)
    assert "route_fact_value_type_mismatch" in {issue.code for issue in caught.value.issues}


@pytest.mark.parametrize("change,code", [
    ("missing_definition", "unknown_route_fact_key"),
    ("missing_description", "route_fact_description_required"),
    ("invalid_key", "invalid_route_fact_key"),
    ("extra_field", "invalid_route_fact_shape"),
])
def test_fact_condition_requires_declared_semantics_and_exact_shape(change, code):
    story = fact_route_story()
    row = story["nodes"][0]["route_gates"][0]["conditions"]["all"][0]
    if change == "missing_definition":
        story["fact_contract"]["facts"] = {}
    elif change == "missing_description":
        story["fact_contract"]["facts"][row["key"]].pop("description")
    elif change == "invalid_key":
        row["key"] = "route decided"
    else:
        row["metric"] = "trust"
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile_v2_2(story)
    assert code in {issue.code for issue in caught.value.issues}


def test_fact_and_metric_conjunctions_can_disambiguate_same_fact_value():
    story = fact_route_story(values=("山径", "山径"))
    for route, op in zip(story["nodes"][0]["route_gates"], ("<", ">=")):
        route["conditions"]["all"].append({"type": "metric_compare", "metric": "trust", "op": op, "value": 50})
    NumericV2Compiler().compile_v2_2(story)


def test_contradictory_fact_conjunction_is_rejected():
    story = fact_route_story()
    routes = story["nodes"][0]["route_gates"]
    routes[0]["conditions"]["all"].extend(deepcopy(routes[1]["conditions"]["all"]))
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile_v2_2(story)
    assert "route_condition_impossible" in {issue.code for issue in caught.value.issues}


def test_overlapping_fact_disjunction_is_rejected_at_equal_priority():
    story = fact_route_story()
    routes = story["nodes"][0]["route_gates"]
    routes[0]["conditions"] = {"any": [
        *routes[0]["conditions"]["all"], *deepcopy(routes[1]["conditions"]["all"]),
    ]}
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile_v2_2(story)
    assert "overlapping_route_priority" in {issue.code for issue in caught.value.issues}


def test_mixed_fact_metric_overlap_matches_exhaustive_oracle():
    ops = {"==": operator.eq, "!=": operator.ne, ">": operator.gt, "<": operator.lt,
           ">=": operator.ge, "<=": operator.le}
    rng = random.Random(23)
    fact_values = [True, 1, "官道", "山径"]
    missing = object()

    def holds(conditions, metric, facts):
        mode = "any" if "any" in conditions else "all"
        checks = []
        for row in conditions[mode]:
            if row["type"] == "fact_equals":
                value = facts[row["key"]]
                checks.append(type(value) is type(row["value"]) and value == row["value"])
            else:
                checks.append(ops[row["op"]](metric, row["value"]))
        return any(checks) if mode == "any" else all(checks)

    def conditions():
        rows = []
        for _ in range(rng.randrange(5)):
            if rng.choice((True, False)):
                rows.append({"type": "fact_equals", "key": rng.choice(("route", "backup")), "value": rng.choice(fact_values)})
            else:
                rows.append({"type": "metric_compare", "metric": "trust", "op": rng.choice(list(ops)), "value": rng.randrange(3)})
        return {rng.choice(("all", "any")): rows}

    for _ in range(600):
        left, right = conditions(), conditions()
        expected = any(
            holds(left, metric, {"route": route, "backup": backup})
            and holds(right, metric, {"route": route, "backup": backup})
            for metric, route, backup in product(range(3), [missing, *fact_values], [missing, *fact_values])
        )
        assert _conditions_overlap(left, right, {"trust": (0, 2)}) is expected, (left, right)
