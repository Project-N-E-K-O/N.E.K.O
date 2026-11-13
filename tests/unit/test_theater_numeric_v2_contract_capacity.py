"""Body contract checks use review capacity without dropping authored obligations."""

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_budget import NUMERIC_V2_ACTOR_BUDGET_PROFILES
from services.theater.numeric_v2_cast import NumericV2CastProjection
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.utils.theater_prompt import contract_audit_payload, serialized_prompt_tokens


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    """Exercise the real contract checker rather than the workflow fixture's stub."""


def _transition(*, long_history=False):
    package = Path(__file__).resolve().parents[2] / "assets/theater/story_neon_rebuild_ea2e45dab7.json"
    engine = NumericV2Engine.from_mapping(json.loads(package.read_text(encoding="utf-8")))
    cast = NumericV2CastProjection.from_story(engine.story,
        player_name=_binding()["player_address"], catgirl_name=_binding()["catgirl_name"])
    source, target = engine.nodes["mainline_01"], engine.nodes["mainline_02"]
    contract = cast.value(source["route_gates"][0]["transition_contract"])
    message = "好，今晚先保持隔离休息，天亮再查日志。"
    latest = "来源仍然未知。今晚先保持终端隔离，天亮再一起查日志，好吗？"
    rows = tuple({"revision": index, "from_node_id": source["id"], "to_node_id": source["id"],
        "input_text": "我们先确认眼前情况。", "performance": "雨仍在下。" * 600}
        for index in range(1, 6)) if long_history else ()
    rows = (*rows, {"revision": len(rows) + 1, "from_node_id": source["id"], "to_node_id": source["id"],
        "input_text": "我选择让终端继续断网。", "performance": latest})
    session = engine.create_session(session_id="contract-review-capacity", catgirl_binding=_binding(),
        opening_performance={"scene_narration": cast.text(source["story_beat"]["opening_scene"]),
                             "performance": "这里是哪里？", "suggested_inputs": []})
    session = replace(session, revision=len(rows), performance_history=rows, player_address_known=True)
    original = target["story_beat"]["fixed_narrations"][0]
    candidate = {"segments": [
        {"phase": "source_response", "performance": ""},
        {"phase": "transition_bridge", "scene_narration": contract["bridge_scene_narration"]},
        {"phase": "target_opening", "scene_narration": "", "performance": "", "fixed_narrations": [{
            "node_id": target["id"], "id": original["id"], "text": cast.text(original["text"]), "position": "before"}]},
    ], "suggested_inputs": []}
    return {
        "node": {**source, "story_beat": cast.value(source["story_beat"])},
        "target_node": {**target, "story_beat": cast.value(target["story_beat"])},
        "transition_contract": contract, "session": session, "player_input": message,
        "actor_performance": candidate, "include_all_segments": True,
        "cast_scope": {"player": [cast.player_name], "active_catgirl": [cast.catgirl_name]},
    }


async def _config(_):
    return {"model": "test", "base_url": "https://test.invalid", "api_key": "test"}


def _client(monkeypatch, requests):
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            requests.append(messages)
            return SimpleNamespace(content=json.dumps(contract_audit_payload({"violations": []}, messages)))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(evaluator, "_model_config", _config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", factory)


@pytest.mark.asyncio
@pytest.mark.parametrize("long_history", [False, True])
async def test_complete_builtin_transition_uses_formal_review_capacity(monkeypatch, long_history):
    kwargs = _transition(long_history=long_history)
    before = deepcopy(kwargs)
    requests = []
    _client(monkeypatch, requests)
    monkeypatch.setattr(evaluator, "history_evidence", lambda *args, **kwargs: [])
    assert await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(**kwargs) == ()
    assert len(requests) == 1
    messages = requests[0]
    budget = NUMERIC_V2_ACTOR_BUDGET_PROFILES[kwargs["session"].actor_budget_profile]
    tokens = serialized_prompt_tokens(*(message.content for message in messages))
    assert budget["evaluator_input_max_tokens"] < tokens <= budget["formal_judge_input_max_tokens"]
    data = json.loads(messages[1].content.split("\n", 1)[1])
    rules = [*evaluator._explicit_author_boundaries(kwargs["node"]),
             *evaluator._explicit_author_boundaries(kwargs["target_node"])]
    assert [row["text"] for row in data["author_boundaries"][:len(rules)]] == rules
    assert data["transition_context"]["target_entry"]["author_delivery_plan"]["goals"]
    candidates = [row for row in data["sources"] if row["id"].startswith("c")]
    assert candidates[-1]["program_fixed_original"] is True
    assert candidates[-1]["text"] == kwargs["actor_performance"]["segments"][-1]["fixed_narrations"][0]["text"]
    assert any(row["text"] == kwargs["player_input"] for row in data["player_authorization_sources"])
    assert any(row["text"] == "我选择让终端继续断网。" for row in data["player_authorization_sources"])
    assert any("天亮再一起查日志" in row["text"] for row in data["sources"] if row["id"].startswith("h"))
    assert kwargs == before


@pytest.mark.asyncio
async def test_body_contract_keeps_formal_budget_independent_from_ordinary_review(monkeypatch):
    kwargs = _transition()
    requests = []
    _client(monkeypatch, requests)
    profile = NUMERIC_V2_ACTOR_BUDGET_PROFILES[kwargs["session"].actor_budget_profile]
    monkeypatch.setitem(profile, "judge_input_max_tokens", 1)
    assert await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(**kwargs) == ()
    assert len(requests) == 1
    kwargs.pop("target_node")
    kwargs.pop("transition_contract")
    kwargs["actor_performance"] = {"performance": "来源未知，先保持隔离。", "suggested_inputs": []}
    with pytest.raises(evaluator.NumericV2EvaluatorError, match="contract_check_input_over_budget"):
        await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(**kwargs)
    assert len(requests) == 1


@pytest.mark.asyncio
async def test_oversized_hard_rules_are_not_trimmed_to_force_a_request(monkeypatch):
    kwargs = _transition()
    kwargs["transition_contract"]["must_preserve"].append("必须保留这一条作者硬约束。" * 3000)
    before = deepcopy(kwargs)

    async def unexpected_config(_):
        raise AssertionError("Oversized mandatory input must fail before any provider setup.")

    monkeypatch.setattr(evaluator, "_model_config", unexpected_config)
    with pytest.raises(evaluator.NumericV2EvaluatorError, match="contract_check_input_over_budget"):
        await evaluator.NumericV2MetricEvaluator(object()).verify_contract_boundaries(**kwargs)
    assert kwargs == before
