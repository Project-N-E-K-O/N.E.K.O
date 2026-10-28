"""Review sees Actor-visible author role guidance, without promoting it to facts."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_review_player_background import (
    MESSAGE, _case, _data, _review_messages,
)


ROLE = "作者给定初始动机：林雨想保留旧记录的原件，可随真实经历改变；但不要求周宁交出私人物品，也不表示已经得到许可。"
PROJECTED_ROLE = ROLE.replace("林雨", "Lan").replace("周宁", "哥哥")


def _role_case(role=ROLE, *, restricted=False):
    engine, session = _case(restricted=restricted)
    story = deepcopy(engine.story)
    story["catgirl_binding"]["role_overlay"] = role
    engine = NumericV2Engine.from_mapping(story)
    return engine, replace(session, story_package_hash=engine.compiled.package_hash)


@pytest.mark.parametrize('mode', ['ordinary', 'formal'])
@pytest.mark.parametrize("restricted", [False, True])
def test_initial_role_context_keeps_source_and_cognition_scope(mode, restricted):
    engine, session = _role_case(restricted=restricted)
    before = deepcopy((engine.story, session.to_dict()))
    messages = _review_messages(engine, session, mode)
    data = _data(messages)
    if restricted:
        assert "author_initial_role_context" not in data
        assert "保留旧记录的原件" not in json.dumps(data, ensure_ascii=False)
    else:
        assert data["author_initial_role_context"] == {
            "source": "catgirl_binding.role_overlay", "text": PROJECTED_ROLE,
        }
        assert "不要求哥哥交出私人物品" in data["author_initial_role_context"]["text"]
    assert data["player_input"] == MESSAGE
    assert PROJECTED_ROLE not in json.dumps(data["scene_context"], ensure_ascii=False)
    assert "author_initial_role_context" not in data.get("actor_fact_candidates", ())
    system = messages[0].content
    assert system.count("作者初始角色职责、关系与动机") == 1
    assert "不是玩家义务、完成证据或永久不变的立场" in system
    assert "实际经历可以改变动机" in system
    assert (engine.story, session.to_dict()) == before


@pytest.mark.parametrize("field,value", [("cognition_state", "limited"), ("memory_state", "partial")])
def test_actual_target_restriction_hides_role_but_future_preview_does_not(field, value):
    source, session = _role_case()
    story = deepcopy(source.story)
    target = next(node for node in story["nodes"] if node["id"] == "middle")
    target["story_beat"]["acting_contract"][field] = value
    engine = NumericV2Engine.from_mapping(story)
    session = replace(session, story_package_hash=engine.compiled.package_hash)
    assert _data(_review_messages(engine, session))["author_initial_role_context"]["text"] == PROJECTED_ROLE
    formal = _data(_review_messages(engine, session, "formal"))
    assert "author_initial_role_context" not in formal
    assert "保留旧记录的原件" not in json.dumps(formal, ensure_ascii=False)
