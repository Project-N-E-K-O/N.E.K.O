"""Evidence scope reaches every judge without changing sources or approval authority."""

from copy import deepcopy
from dataclasses import replace
import json

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding


OBSERVATION = "仪表指针停在零位，尚未接通检测设备，原因未查明。"
WORLD_RULE = "这台仪表在接通自检并完成归零后，零位才表示校准通过。"
PUBLIC_MEMORY = "我以前在这个工作台旁学过装订。"
UNCERTAIN = "零位也许只是静止位置，现在还不能判断校准是否通过。"


@pytest.fixture(autouse=True)
def _offline_tokenizer(monkeypatch):
    # Contract tests must never fetch tokenizer data or invoke a provider.
    monkeypatch.setattr("utils.tokenize._get_encoder", lambda encoding: None)


def _case():
    story = deepcopy(_engine(ordinary=True).story)
    story["nodes"][0]["story_beat"]["summary"] = WORLD_RULE
    story["nodes"][0]["story_beat"]["opening_scene"] = OBSERVATION
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(
        session_id="evidence-scope", catgirl_binding=_binding(),
        opening_performance={"scene_narration": OBSERVATION, "performance": "先观察。",
                             "suggested_inputs": []},
    )
    state = deepcopy(session.story_state)
    state["revision"] = 1
    session = replace(session, revision=1, node_turn_count=1, story_state=state,
        performance_history=({
            "revision": 1, "from_node_id": "start", "to_node_id": "start",
            "input_text": PUBLIC_MEMORY, "performance": "你说的那次经历，我听到了。",
            "scene_narration": "管理员说：“这次还没有自检。”", "suggested_inputs": [],
        },))
    return engine, session


def _data(messages):
    text = messages[1].content
    return prompt_payload(text)


def test_evaluator_retains_world_rule_observation_and_public_memory_as_distinct_evidence():
    engine, session = _case()
    before = deepcopy((engine.story, session.to_dict()))
    messages = evaluator._build_messages(engine, session, "现在能确定原因吗？")
    data = _data(messages)
    text = json.dumps(data, ensure_ascii=False)
    for source in (OBSERVATION, WORLD_RULE, PUBLIC_MEMORY, "这次还没有自检"):
        assert source in text
    assert data["player_input"] == "现在能确定原因吗？"
    assert (engine.story, session.to_dict()) == before
