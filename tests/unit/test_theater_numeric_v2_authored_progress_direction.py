"""Pacing review selects current author material rather than inventing tasks."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case


_REVIEW = ev.NumericV2MetricEvaluator.review_story_evidence
DESCRIPTION = "角色指出眼前地图上已经标明的入口。"
OFFER = "现在一起沿左侧走廊去阅览室，好吗？"


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["show_map", DESCRIPTION, "", "exit:to_leave",
                                    "player_enters", "future_goal", "寻找暗格中的新日记。"])
async def test_ordinary_progress_can_only_select_current_author_task_or_declared_offer(monkeypatch, choice):
    case = initiation_case()
    initial_state = deepcopy(case["session"].story_state)
    source = case["engine"].nodes["start"]
    source["story_beat"]["goals"] = [
        {"id": "show_map", "owner": "catgirl", "description": DESCRIPTION,
         "delivery": {"source_ids": ["opening.start"]}},
        {"id": "player_enters", "owner": "player", "description": "玩家进入下一间房。",
         "delivery": {"source_ids": ["runtime.player_input"]}},
    ]
    source["route_gates"][1]["transition_contract"]["fallback_offer"] = OFFER
    requests, formats = [], []

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            requests.append(json.loads(messages[1].content))
            formats.append(kwargs.get("response_format"))
            return SimpleNamespace(content=json.dumps({"direction": choice}, ensure_ascii=False))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    kwargs = dict(engine=case["engine"], session=case["session"], message="看看眼前地图。",
                  claims=(), review_progress=True, timeout_seconds=1)
    if choice in {"player_enters", "future_goal", "寻找暗格中的新日记。"}:
        with pytest.raises(ev.NumericV2EvaluatorError, match="story_evidence_failed"):
            await _REVIEW(ev.NumericV2MetricEvaluator(object()), **kwargs)
    else:
        result = await _REVIEW(ev.NumericV2MetricEvaluator(object()), **kwargs)
        assert result.approved_indexes == () and result.ending_ready is False
        if choice in {"show_map", DESCRIPTION}:
            assert result.direction == DESCRIPTION
            assert result.current_scene_task["selected_id"] == "show_map"
        elif choice:
            assert OFFER in result.direction and "不执行" in result.direction
            assert result.current_scene_task is None
        else:
            assert result.direction == "" and result.current_scene_task is None
    request, = requests
    assert request["progress"]["direction_ids"] == ["show_map", "exit:to_leave"]
    assert request["progress"]["exit_proposal"] == {"id": "exit:to_leave", "offer": OFFER}
    assert formats[0]["json_schema"]["schema"]["properties"]["direction"]["enum"] == ["", "show_map", "exit:to_leave"]
    assert case["session"].revision == 0 and case["session"].story_state == initial_state


@pytest.mark.asyncio
async def test_progress_without_author_work_or_offer_cannot_invent_a_fallback(monkeypatch):
    case = initiation_case()
    requests = []

    async def config(_): return {"model": "test", "base_url": "https://test.invalid", "api_key": "test"}
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            requests.append(json.loads(messages[1].content))
            return SimpleNamespace(content='{"direction":""}')
    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    result = await _REVIEW(ev.NumericV2MetricEvaluator(object()), engine=case["engine"],
        session=case["session"], message="先等一下。", claims=(), review_progress=True, timeout_seconds=1)
    assert requests[0]["progress"]["direction_ids"] == []
    assert result.direction == "" and result.current_scene_task is None


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["performance", "player_input", None])
async def test_ordinary_legacy_fact_fallback_does_not_schedule_player_or_unknown_ownership(monkeypatch, source):
    case = initiation_case()
    key = "scene:start:delivered"
    definition = {"value_type": "bool", "visibility": "public", "description": "仪表实际展示读数。"}
    if source:
        definition["evidence_source"] = source
    case["engine"].fact_contract[key] = definition
    case["engine"].nodes["start"]["completion_contract"] = {"all": [{"key": key, "equals": True}]}
    requests = []

    async def config(_): return {"model": "test", "base_url": "https://test.invalid", "api_key": "test"}
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            requests.append(json.loads(messages[1].content))
            return SimpleNamespace(content=json.dumps({"direction": key}))
    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    kwargs = dict(engine=case["engine"], session=case["session"], message="先看看。", claims=(),
                  review_progress=True, timeout_seconds=1)
    if source == "performance":
        result = await _REVIEW(ev.NumericV2MetricEvaluator(object()), **kwargs)
        assert result.current_scene_task["selected_id"] == key
    else:
        with pytest.raises(ev.NumericV2EvaluatorError, match="story_evidence_failed"):
            await _REVIEW(ev.NumericV2MetricEvaluator(object()), **kwargs)
    assert requests[0]["progress"]["direction_ids"] == ([key] if source == "performance" else [])
