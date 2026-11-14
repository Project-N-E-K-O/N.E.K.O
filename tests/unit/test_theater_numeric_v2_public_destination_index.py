"""Initiation selects only public originals retained in the exact request."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev
from services.theater.numeric_v2_budget import NUMERIC_V2_ACTOR_BUDGET_PROFILES
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.utils.theater_prompt import prompt_payload


def _decision(index, **fields):
    return json.dumps({"scene_complete": False, "metric_changes": {}, "transition_intent": "initiate",
        "transition_reply_target": "unclear", "exit_matches": True,
        "exit_comparison": "玩家要求前往公开的阅览室，出口一致。", "public_destination_index": index,
        **fields}, ensure_ascii=False)


def _originals(data):
    assert "public_destination_evidence" not in data
    numbered = {}
    def visit(value):
        if isinstance(value, dict):
            if "public_destination_index" in value:
                index = value["public_destination_index"]
                assert type(index) is int and index not in numbered and value["text"]
                numbered[index] = value["text"]
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)
    visit(data)
    assert sorted(numbered) == list(range(len(numbered)))
    return [numbered[index] for index in range(len(numbered))]


def test_index_selects_request_original_and_preserves_legacy_quote():
    case = initiation_case()
    original_opening = deepcopy(case["session"].opening_performance)
    evidence = []
    messages = ev._build_messages(case["engine"], case["session"], case["message"],
                                  public_destination_evidence=evidence)
    data = prompt_payload(messages[1].content)
    originals = _originals(data)
    index = next(i for i, text in enumerate(originals) if "通往阅览室" in text)
    result = ev._parse_output(_decision(index), case["engine"], case["message"], case["session"],
                              public_destination_evidence=evidence)
    assert result.transition_intent == "initiate" and result.public_destination_quote == originals[index]
    assert case["session"].opening_performance == original_opening
    assert messages[1].content.count(originals[index]) == 1
    old = json.loads(_decision(index))
    old.pop("public_destination_index")
    old["public_destination_quote"] = originals[index]
    assert ev._parse_output(json.dumps(old), case["engine"], case["message"], case["session"],
                            public_destination_evidence=evidence) == result


@pytest.mark.parametrize("index", [-1, 1000, True, "0", None])
def test_invalid_index_never_falls_back_to_another_quote_or_session(index):
    case = initiation_case()
    evidence = []
    messages = ev._build_messages(case["engine"], case["session"], case["message"],
                                  public_destination_evidence=evidence)
    data = prompt_payload(messages[1].content)
    original = next(text for text in _originals(data) if "通往阅览室" in text)
    result = ev._parse_output(_decision(index, public_destination_quote=original), case["engine"],
                              case["message"], case["session"], public_destination_evidence=evidence)
    assert result.transition_intent == "unclear" and result.public_destination_quote == ""
    result = ev._parse_output(_decision(0, public_destination_quote=original), case["engine"],
                              case["message"], case["session"])
    assert result.transition_intent == "unclear"


@pytest.mark.parametrize("fields", [{"transition_intent": "unclear"}, {"exit_matches": False},
                                    {"exit_comparison": ""}])
def test_a_real_public_source_does_not_create_intent_or_exit_alignment(fields):
    case = initiation_case()
    evidence = []
    ev._build_messages(case["engine"], case["session"], case["message"], public_destination_evidence=evidence)
    result = ev._parse_output(_decision(1, **fields), case["engine"], case["message"], case["session"],
                              public_destination_evidence=evidence)
    assert result.transition_intent == "unclear"


def test_bad_index_cannot_use_legacy_entry_invitation_fallback(monkeypatch):
    case = initiation_case()
    evidence = []
    ev._build_messages(case["engine"], case["session"], case["message"], public_destination_evidence=evidence)
    def forbidden(*args):
        raise AssertionError("An invalid request-bound index must not search another invitation")
    monkeypatch.setattr(ev, "_entry_invitation_quote", forbidden)
    result = ev._parse_output(_decision(999, scene_complete=True, transition_intent="accept"),
        case["engine"], case["message"], case["session"], public_destination_evidence=evidence)
    assert result.transition_intent == "unclear"


def test_complete_indexed_decision_survives_truncated_optional_fact_array():
    case = initiation_case()
    evidence = []
    ev._build_messages(case["engine"], case["session"], case["message"], public_destination_evidence=evidence)
    response = _decision(1)[:-1] + ',"fact_candidates":[{"key":'
    result = ev._parse_output(response, case["engine"], case["message"], case["session"],
        public_destination_evidence=evidence, finish_reason="length")
    assert result.transition_intent == "initiate" and result.fact_operations == ()


def test_catalog_excludes_plans_old_visits_and_player_inputs_but_keeps_current_bridge(monkeypatch):
    case = initiation_case()
    rows = [
        {"revision": 0, "source": "performance", "current_visit": False, "text": "旧访问去过旧大厅。"},
        {"revision": 0, "source": "player_input", "current_visit": True, "text": "玩家首次提出去塔顶。"},
        {"revision": 0, "source": "performance", "current_entry_bridge": True, "current_visit": False,
         "text": "桥段已展示：左侧通往阅览室。"},
    ]
    monkeypatch.setattr(ev, "history_evidence", lambda *args, **kwargs: deepcopy(kwargs.get("retained", rows)))
    evidence = []
    messages = ev._build_messages(case["engine"], case["session"], case["message"], public_destination_evidence=evidence)
    data = prompt_payload(messages[1].content)
    originals = _originals(data)
    assert rows[2]["text"] in originals
    assert rows[0]["text"] not in originals and rows[1]["text"] not in originals
    assert data["transition_preview"]["transition_direction"] not in originals
    assert "public_destination_index" not in str(data["transition_preview"])
    assert "public_destination_index" not in data["history_evidence"][0]
    assert "public_destination_index" not in data["history_evidence"][1]
    index = originals.index(rows[2]["text"])
    result = ev._parse_output(_decision(index), case["engine"], case["message"], case["session"],
                              public_destination_evidence=evidence)
    assert result.public_destination_quote == rows[2]["text"]


def test_catalog_rebinds_after_whole_record_budget_trimming(monkeypatch):
    case = initiation_case()
    older = {"revision": 0, "phase": "opening", "player_input": "",
             "content": [{"type": "dialogue", "text": "旧记录。" * 3000}]}
    latest = {"revision": 1, "phase": "turn", "player_input": "先看看。",
              "content": [{"type": "dialogue", "text": "眼前走廊通往阅览室。"}]}
    monkeypatch.setattr(ev, "_current_scene_context", lambda *_: deepcopy([older, latest]))
    monkeypatch.setattr(ev, "history_evidence", lambda *args, **kwargs: [])
    profile = NUMERIC_V2_ACTOR_BUDGET_PROFILES[case["session"].actor_budget_profile]
    monkeypatch.setitem(profile, "evaluator_input_max_tokens", 100000)
    full = ev._build_messages(case["engine"], case["session"], case["message"])
    limit = sum(ev.count_tokens(m.content) for m in full) - ev.count_tokens(older["content"][0]["text"])
    monkeypatch.setitem(profile, "evaluator_input_max_tokens", limit)
    evidence = []
    packed = ev._build_messages(case["engine"], case["session"], case["message"], public_destination_evidence=evidence)
    data = prompt_payload(packed[1].content)
    assert data["scene_context"] == [{**latest, "content": [{**latest["content"][0], "public_destination_index": 0}]}]
    assert _originals(data) == [latest["content"][0]["text"]]
    assert "public_destination_index" not in latest["content"][0]
    assert sum(ev.count_tokens(m.content) for m in packed) <= limit
    result = ev._parse_output(_decision(0), case["engine"], case["message"], case["session"],
                              public_destination_evidence=evidence)
    assert result.public_destination_quote == latest["content"][0]["text"]


@pytest.mark.asyncio
async def test_evaluate_resolves_one_response_against_its_frozen_sent_catalog(monkeypatch):
    case = initiation_case()
    requests = []

    async def config(_): return {"model": "test", "base_url": "https://test.invalid", "api_key": "test"}
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            data = prompt_payload(messages[1].content)
            originals = _originals(data)
            requests.append(originals)
            index = next(i for i, text in enumerate(originals) if "通往阅览室" in text)
            return SimpleNamespace(content=_decision(index))
    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    result = await ev.NumericV2MetricEvaluator(object()).evaluate(
        engine=case["engine"], session=case["session"], message=case["message"])
    assert len(requests) == 1 and result.transition_intent == "initiate"
    assert result.public_destination_quote in requests[0]
