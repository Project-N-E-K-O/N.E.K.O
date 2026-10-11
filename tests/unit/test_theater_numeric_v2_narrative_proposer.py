"""Isolate one narrative proposal request without changing story state or approval policy."""

import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_narrative_proposer as proposer
from services.theater.numeric_v2_actor_output import NumericV2ActorError, NumericV2ActorOutputError
from services.theater.numeric_v2_narrative import apply_narrative_record
from services.theater.numeric_v2_usage import numeric_v2_usage_scope


@pytest.fixture(autouse=True)
def _offline_token_counter(monkeypatch):
    # Tokenizer initialization may download encoding data; these contract tests stay offline.
    monkeypatch.setattr("utils.tokenize._get_encoder", lambda encoding: None)


def _session():
    return SimpleNamespace(
        narrative_state=None, current_node_id="scene", revision=1,
        opening_performance={"performance": "物品放在桌上。"},
        performance_history=({"revision": 1, "from_node_id": "scene", "to_node_id": "scene",
            "input_text": "先保留物品。", "performance": "物品仍在桌上，尚未交接。",
            "suggested_inputs": ["未选择的历史推荐"], "completion_contract": "历史作者目标"},),
    )


def _event(quote, changes, *, phase="player_input", source="player_input", subject="player"):
    return {"phase": phase, "subject": subject, "kind": "action", "changes": changes,
            "source": source, "quote": quote, "known_to": ["player", "character"],
            **({"authorization_quote": quote} if phase == "player_input" else {})}


def _client(monkeypatch, payload, *, slow="", fail=""):
    observed = {"config": [], "factory": [], "requests": [], "closed": 0, "call_types": []}

    async def stage(name):
        if slow == name:
            await asyncio.sleep(1)
        if fail == name:
            raise RuntimeError("test failure")

    class Config:
        async def aget_model_api_config(self, channel):
            observed["config"].append(channel)
            await stage("config")
            return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                    "api_key": "test-only", "provider_type": "openai"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            observed["closed"] += 1
            await stage("close")
            return False

        async def ainvoke(self, messages, **kwargs):
            observed["requests"].append((messages, kwargs))
            await stage("request")
            return SimpleNamespace(
                content=payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False),
                response_metadata={"token_usage": {"prompt_tokens": 321, "completion_tokens": 45}},
            )

    async def factory(*args, **kwargs):
        observed["factory"].append((args, kwargs))
        await stage("client")
        return Client()

    monkeypatch.setattr(proposer, "create_chat_llm_async", factory)
    monkeypatch.setattr(proposer, "set_call_type", observed["call_types"].append)
    return proposer.NumericV2NarrativeProposer(Config()), observed


@pytest.mark.parametrize("player_input,changes", [
    ("（接过毛巾简单擦了擦头发，将试卷平铺在桌上）还好没湿透。", [
        {"entity": "item:毛巾", "attribute": "holder", "value": "player"},
        {"entity": "item:试卷", "attribute": "position", "value": "平铺在桌上"},
    ]),
    ("（接过行囊，走到院门旁）我先在这里等。", [
        {"entity": "item:行囊", "attribute": "holder", "value": "player"},
        {"entity": "player", "attribute": "position", "value": "走到院门旁"},
    ]),
])
async def test_player_changes_use_one_formal_request_with_complete_input_and_actual_usage(monkeypatch, player_input, changes):
    events = [_event(player_input, changes)]
    instance, seen = _client(monkeypatch, {"narrative_events": events})
    session = _session()
    history_text = changes[0]["entity"].partition(":")[2] + "仍在桌上，尚未交接。"
    session.performance_history[0]["performance"] = history_text
    performance = {"performance": "我看到了。", "scene_update": "灯亮着。",
                   "suggested_inputs": ["未选择的本轮推荐"], "author_target": "本轮作者目标",
                   "fact_candidates": [{"key": "不应进入的完成事实"}], "persona": "不应进入的人格"}
    original = deepcopy((session.__dict__, performance))

    with numeric_v2_usage_scope() as calls:
        result = await instance.propose(performance=performance, player_input=player_input, session=session)

    assert result == events
    assert (session.__dict__, performance) == original
    assert instance.provider_call_count == len(seen["requests"]) == 1
    assert seen["config"] == ["conversation"]
    assert seen["call_types"] == ["theater_numeric_v2_narrative"]
    assert calls == [{"stage": "narrative", "input_tokens": 321, "output_tokens": 45}]
    args, options = seen["factory"][0]
    assert args[0] == "qwen3.8-flash"
    assert options == {"provider_type": "openai", "timeout": 12.0, "max_retries": 0, "max_completion_tokens": 1000}
    messages, request_options = seen["requests"][0]
    data = json.loads(messages[1].content)
    assert data["player_input"] == player_input
    assert data["phase_texts"] == [{"phase": "player_input"},
                                  {"phase": "turn", "scene_narration": "灯亮着。", "performance": "我看到了。"}]
    assert history_text in messages[1].content
    for excluded in ("未选择的", "作者目标", "不应进入"):
        assert excluded not in messages[1].content
    schema = request_options["response_format"]["json_schema"]["schema"]
    assert list(schema["properties"]) == schema["required"] == ["narrative_events"]
    assert seen["closed"] == 1


@pytest.mark.parametrize("segments", [False, True])
async def test_transition_reads_each_actual_phase_without_target_author_fields(monkeypatch, segments):
    rows = [
        {"phase": "source_response", "performance": "（收好工具）可以走了。"},
        {"phase": "transition_bridge", "scene_narration": "两人来到走廊。"},
        {"phase": "target_opening", "scene_narration": "走廊亮着灯。", "performance": "我站在窗边。"},
    ]
    events = [
        _event("收好工具", [{"entity": "item:工具", "attribute": "condition", "value": "收好工具"}],
               phase="source_response", source="performance", subject="character"),
        _event("两人来到走廊。", [{"entity": "environment:走廊", "attribute": "location", "value": "走廊"}],
               phase="transition_bridge", source="scene_narration", subject="character"),
        _event("我站在窗边。", [{"entity": "character", "attribute": "position", "value": "站在窗边"}],
               phase="target_opening", source="performance", subject="character"),
    ]
    performance = {"segments": rows} if segments else {
        "source_performance": rows[0]["performance"], "bridge_scene_narration": rows[1]["scene_narration"],
        "target_scene_narration": rows[2]["scene_narration"], "target_performance": rows[2]["performance"],
    }
    performance["next_scene"] = "未选的后续目标"
    instance, seen = _client(monkeypatch, {"narrative_events": events})
    assert await instance.propose(performance=performance, player_input="继续。", session=_session()) == events
    messages, options = seen["requests"][0]
    assert json.loads(messages[1].content)["phase_texts"] == [{"phase": "player_input"}, *rows]
    assert "未选的后续目标" not in messages[1].content
    phases = options["response_format"]["json_schema"]["schema"]["properties"]["narrative_events"]["items"]["properties"]["phase"]["enum"]
    assert phases == ["player_input", "source_response", "transition_bridge", "target_opening"]


async def test_opening_has_no_player_input_source_and_rejects_player_event(monkeypatch):
    performance = {"scene_narration": "窗外雨停了。", "performance": "我收好书。"}
    event = _event("我收好书。", [{"entity": "item:书", "attribute": "condition", "value": "收好书"}],
                   phase="opening", source="performance", subject="character")
    instance, seen = _client(monkeypatch, {"narrative_events": [event]})
    assert await instance.propose(performance=performance, player_input="不应作为开场输入", session=_session(), opening=True) == [event]
    messages, options = seen["requests"][0]
    data = json.loads(messages[1].content)
    assert data["player_input"] == ""
    assert data["phase_texts"] == [{"phase": "opening", **performance}]
    assert "不应作为开场输入" not in messages[1].content
    assert options["response_format"]["json_schema"]["schema"]["properties"]["narrative_events"]["items"]["properties"]["phase"]["enum"] == ["opening"]
    bad = _event("玩家动作", [{"entity": "player", "attribute": "position", "value": "玩家动作"}])
    instance, _ = _client(monkeypatch, {"narrative_events": [bad]})
    with pytest.raises(NumericV2ActorOutputError):
        await instance.propose(performance=performance, player_input="玩家动作", session=_session(), opening=True)


def test_committed_projection_keeps_original_history_and_does_not_invent_current_from_partial():
    session = _session()
    session.performance_history[0]["performance"] = "工具匣仍在桌上，尚未交接。"
    quote = "玩家接过工具匣。"
    event = _event(quote, [{"entity": "item:工具匣", "attribute": "holder", "value": "player"}])
    session.narrative_state = apply_narrative_record(None,
        {"schema": "numeric_v2_narrative_record_v2", "coverage": "partial", "events": [event], "proposal_count": 2},
        revision=1, node_id="scene", from_node_id="scene", performance={}, message=quote)
    original = deepcopy(session.__dict__)
    messages = proposer._messages({"performance": "工具匣保持原处。"}, "工具匣呢？", session, False)
    data = json.loads(messages[1].content)
    assert "item:工具匣" in data["narrative_state"]["last_known"]
    assert not data["narrative_state"]["current"]
    assert "工具匣仍在桌上，尚未交接。" in messages[1].content
    assert session.__dict__ == original


@pytest.mark.parametrize("payload", [
    "not json", {}, {"narrative_events": None}, {"narrative_events": {}, "complete": True},
    {"narrative_events": [], "complete": True},
    {"narrative_events": [_event("原话", [{"entity": "item:物品", "attribute": "holder", "value": "由玩家保管"}])]},
    {"narrative_events": [_event("不存在的引文", [{"entity": "player", "attribute": "position", "value": "引文"}])]},
    {"narrative_events": [_event("原话", [{"entity": "character", "attribute": "position", "value": "原话"}],
                                       phase="target_opening", source="performance", subject="character")]},
])
async def test_bad_output_aborts_without_retries_or_empty_success(monkeypatch, payload):
    instance, seen = _client(monkeypatch, payload)
    with pytest.raises(NumericV2ActorOutputError):
        await instance.propose(performance={"performance": "原话"}, player_input="原话", session=_session())
    assert instance.provider_call_count == len(seen["requests"]) == 1
    assert seen["closed"] == 1


async def test_explicit_empty_array_is_valid_but_only_a_proposal(monkeypatch):
    instance, seen = _client(monkeypatch, {"narrative_events": []})
    assert await instance.propose(performance={"performance": "嗯。"}, player_input="嗯。", session=_session()) == []
    assert len(seen["requests"]) == 1


@pytest.mark.parametrize("stage", ["config", "client", "request", "close"])
async def test_total_timeout_includes_config_request_and_close(monkeypatch, stage):
    instance, seen = _client(monkeypatch, {"narrative_events": []}, slow=stage)
    monkeypatch.setattr(proposer, "NUMERIC_V2_NARRATIVE_TIMEOUT_SECONDS", .01)
    with pytest.raises(NumericV2ActorError, match="narrative_timeout"):
        await instance.propose(performance={"performance": "嗯。"}, player_input="嗯。", session=_session())
    assert len(seen["requests"]) == int(stage in {"request", "close"})
    assert len(seen["factory"]) <= 1


@pytest.mark.parametrize("stage", ["client", "request", "close"])
async def test_client_failure_is_not_swallowed(monkeypatch, stage):
    instance, seen = _client(monkeypatch, {"narrative_events": []}, fail=stage)
    with pytest.raises(NumericV2ActorError, match="narrative_model_call_failed"):
        await instance.propose(performance={"performance": "嗯。"}, player_input="嗯。", session=_session())
    assert len(seen["factory"]) == 1


@pytest.mark.parametrize("oversized", ["player_input", "performance"])
async def test_oversized_required_text_fails_before_config_or_client(monkeypatch, oversized):
    instance, seen = _client(monkeypatch, {"narrative_events": []})
    monkeypatch.setattr(proposer, "count_tokens", len)
    performance, player_input = {"performance": "回应。"}, "本轮原话。"
    if oversized == "player_input":
        player_input = "输入" * 5000
    else:
        performance["performance"] = "正文" * 5000
    with pytest.raises(NumericV2ActorError, match="input_over_budget"):
        await instance.propose(performance=performance, player_input=player_input, session=_session())
    assert not seen["config"] and not seen["factory"] and not seen["requests"]


def test_only_history_can_shrink_at_input_boundary(monkeypatch):
    monkeypatch.setattr(proposer, "count_tokens", len)
    history = [{"revision": 1, "source": "performance", "text": "旧" * 300},
               {"revision": 2, "source": "performance", "text": "最近更正"}]
    monkeypatch.setattr(proposer, "history_evidence", lambda *args, **kwargs: deepcopy(history))
    monkeypatch.setattr(proposer, "project_narrative_state", lambda *args, **kwargs: {"current": "完整状态"})
    baseline = proposer._messages({"performance": "完整正文"}, "完整原话", _session(), False)
    limit = sum(len(m.content) for m in baseline) - 200
    monkeypatch.setattr(proposer, "NUMERIC_V2_NARRATIVE_INPUT_MAX_TOKENS", limit)
    messages = proposer._messages({"performance": "完整正文"}, "完整原话", _session(), False)
    data = json.loads(messages[1].content)
    assert data["history_evidence"] == history[1:]
    assert data["player_input"] == "完整原话"
    assert data["phase_texts"][1]["performance"] == "完整正文"
    assert data["narrative_state"] == {"current": "完整状态"}
    assert sum(len(m.content) for m in messages) <= limit
