"""An author opts a route into literal-only target entry; old packages stay intact."""

from copy import deepcopy
import hashlib
import json

import pytest

from services.theater.numeric_v2 import NumericV2CompileError, NumericV2Compiler
from tests.unit.test_theater_numeric_v2_behavior_permissions import _contract
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story


def _story(*, policy="optional", entry=True):
    story = numeric_v2_story()
    target = story["nodes"][1]["story_beat"]
    target["acting_contract"] = _contract([], dialogue=policy)
    if entry:
        target["fixed_narrations"] = [{
            "id": "arrival", "text": "门外的雨停了，旧信仍留在桌上。",
            "trigger": {"type": "entry"}, "after": [], "required_before_exit": True,
        }]
    return story


@pytest.mark.parametrize("policy", ["optional", "forbidden"])
def test_explicit_fixed_entry_route_accepts_complete_author_entry(policy):
    story = _story(policy=policy)
    story["nodes"][0]["route_gates"][0]["transition_contract"]["fixed_entry_only"] = True
    result = NumericV2Compiler().compile(story)
    assert result.story == story


@pytest.mark.parametrize("value", [None, 0, 1, "true", [], {}])
def test_fixed_entry_route_requires_a_real_boolean(value):
    story = _story()
    story["nodes"][0]["route_gates"][0]["transition_contract"]["fixed_entry_only"] = value
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)
    assert any(issue.code == "transition_fixed_entry_only_invalid" for issue in caught.value.issues)


@pytest.mark.parametrize("case", ["missing", "condition", "required", "implicit_required"])
def test_fixed_entry_route_rejects_ineligible_targets(case):
    story = _story(entry=case != "missing")
    target = story["nodes"][1]["story_beat"]
    if case == "condition":
        # Endings independently reject condition narrations; this also must not
        # satisfy the new route's requirement for an entry original.
        target["fixed_narrations"][0]["trigger"] = {"type": "condition", "condition": "门关上了。"}
    elif case == "required":
        target["acting_contract"]["dialogue_policy"] = "required"
    elif case == "implicit_required":
        target.pop("acting_contract")
    story["nodes"][0]["route_gates"][0]["transition_contract"]["fixed_entry_only"] = True
    with pytest.raises(NumericV2CompileError) as caught:
        NumericV2Compiler().compile(story)
    assert any(issue.code == "transition_fixed_entry_only_invalid" for issue in caught.value.issues)


@pytest.mark.parametrize("explicit_false", [False, True])
def test_missing_or_false_flag_keeps_original_package_bytes_and_hash(explicit_false):
    story = numeric_v2_story()
    if explicit_false:
        story["nodes"][0]["route_gates"][0]["transition_contract"]["fixed_entry_only"] = False
    before = deepcopy(story)
    expected = json.dumps(story, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    compiled = NumericV2Compiler().compile(story)
    assert story == before
    assert compiled.json_bytes == expected
    assert compiled.package_hash == "sha256:" + hashlib.sha256(expected).hexdigest()
