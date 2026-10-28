"""Avoid redundant encoding while retaining Actor packing and budget contracts."""

from collections import Counter
from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_actor as actor


SYSTEM = "承接实际经历，保留角色与玩家权限。"
PREFIX = "上下文："


def _wire(data):
    return PREFIX + actor.render_prompt_context(data)


def _data(formal, rows, message="现在继续。"):
    if formal:
        return {"recent_context": rows, "player_input": message,
                "transition": {"contract": {"must_preserve": ["先前选择有效"]}}}
    return {"role": "角色与人格合同。", "current_scene": "当前幕完整合同。",
            "story_so_far": actor._story_so_far_text(rows), "pacing": "先回应当前输入。",
            "next_scene": "下一幕尚未发生。", "player_input": message}


def _pack(formal, data, rows, limit, diagnostics, **kwargs):
    common = {"system_prompt": SYSTEM, "human_prefix": PREFIX, "data": data,
              "max_tokens": limit, "diagnostics": diagnostics}
    if formal:
        return actor._fit_turn_prompt_data(**common, **kwargs)
    return actor._fit_simple_turn_prompt_data(**common, history_rows=rows, **kwargs)


def _observe_counts(monkeypatch):
    calls = []
    original = actor.count_tokens

    def count(text):
        calls.append(text)
        return original(text)

    monkeypatch.setattr(actor, "count_tokens", count)
    return calls, original


@pytest.mark.parametrize("formal", [False, True])
def test_unchanged_prompt_is_encoded_once_per_fit(monkeypatch, formal):
    rows = [{"revision": 3, "player_input": "先保密。", "performance": "好，我会记住。"}]
    data = _data(formal, rows)
    untouched = deepcopy(data)
    calls, original = _observe_counts(monkeypatch)
    diagnostics = {}

    result = _pack(formal, data, rows, 10000, diagnostics)

    assert result == untouched
    assert data == untouched
    assert Counter(calls)[SYSTEM] == 1
    assert Counter(calls)[_wire(result)] == 1
    assert diagnostics["final_tokens"] == original(SYSTEM) + original(_wire(result))
    assert diagnostics["history_included_revisions"] == [3]
    assert diagnostics["history_dropped_revisions"] == []


@pytest.mark.parametrize("formal", [False, True])
def test_count_reuse_does_not_escape_the_current_fit(monkeypatch, formal):
    # 不跨回合缓存：即使后来又出现相同原话，也要在新装箱内使用当前计量器。
    rows = [{"revision": 1, "player_input": "留下。", "performance": "我陪你。"}]
    calls, original = _observe_counts(monkeypatch)
    results = []
    for message in ("现在继续。", "先回答我的问题。", "现在继续。"):
        diagnostics = {}
        result = _pack(formal, _data(formal, rows, message), rows, 10000, diagnostics)
        assert result["player_input"] == message
        assert diagnostics["final_tokens"] == original(SYSTEM) + original(_wire(result))
        results.append(result)

    counts = Counter(calls)
    assert counts[SYSTEM] == 3
    assert counts[_wire(results[0])] == 2
    assert counts[_wire(results[1])] == 1


@pytest.mark.parametrize("formal", [False, True])
def test_evicted_history_recounts_complete_new_prompt_and_keeps_latest_turn(monkeypatch, formal):
    rows = [
        {"revision": 1, "player_input": "很早以前的闲谈。" * 150, "performance": "旧回应。"},
        {"revision": 2, "player_input": "尚未答应。", "performance": "我会等你的决定。"},
    ]
    data = _data(formal, rows)
    untouched = deepcopy(data)
    expected = _data(formal, rows[-1:])
    original = actor.count_tokens
    limit = original(SYSTEM) + original(_wire(expected))
    assert original(SYSTEM) + original(_wire(data)) > limit
    calls, original = _observe_counts(monkeypatch)
    diagnostics = {}

    result = _pack(formal, data, rows, limit, diagnostics)

    assert result == expected
    assert data == untouched
    assert diagnostics["history_included_revisions"] == [2]
    assert diagnostics["history_dropped_revisions"] == [1]
    assert diagnostics["final_tokens"] == limit
    assert Counter(calls)[SYSTEM] == 1
    assert Counter(calls)[_wire(data)] == 1
    assert Counter(calls)[_wire(result)] == 1


@pytest.mark.parametrize("pressure", ["attribute_quote", "pending_offer"])
def test_capacity_rewrites_recount_full_human_without_losing_contracts(monkeypatch, pressure):
    rows = [{"revision": 8, "player_input": "先留在原地。", "performance": "我也留在这里。"}]
    data = _data(False, rows)
    kwargs = {}
    if pressure == "attribute_quote":
        state = {
            "current": {"player": {"position": {"value": "原地", "source_event": "r8.player_input.1",
                "quote": "玩家仍留在原地，没有作出新承诺。" * 100}}},
            "last_known": {},
            "recent_events": [{"event_id": "r8.player_input.1", "quote": "先留在原地。"}],
            "projection": {"quotes_omitted": 0},
        }
        marker = "\nnarrative_state（已提交动态叙事账本）："
        data["current_scene"] += marker + json.dumps(state, ensure_ascii=False, separators=(",", ":"))
        expected = deepcopy(data)
        compact = deepcopy(state)
        compact["current"]["player"]["position"].pop("quote")
        compact["projection"]["quotes_omitted"] = 1
        expected["current_scene"] = data["current_scene"].replace(
            json.dumps(state, ensure_ascii=False, separators=(",", ":")),
            json.dumps(compact, ensure_ascii=False, separators=(",", ":")),
        )
        kwargs["narrative_state"] = state
    else:
        offer = "（望向站台）要一起去长椅旁等通知吗？" * 12
        rows[0]["performance"] = offer
        data = _data(False, rows)
        marker = "当前待确认提议原文（只证明此前说过，不证明安排正确）："
        warning = "先核对原邀请，不得替玩家接受。"
        data["pacing"] += marker + offer + warning
        expected = deepcopy(data)
        expected["pacing"] = expected["pacing"].replace(
            marker + offer, marker + "见 story_so_far 中角色最新的完整原文。", 1,
        )
        kwargs["pending_offer_text"] = offer
    untouched = deepcopy((data, rows, kwargs))
    original = actor.count_tokens
    limit = original(SYSTEM) + original(_wire(expected))
    assert original(SYSTEM) + original(_wire(data)) > limit
    calls, original = _observe_counts(monkeypatch)
    diagnostics = {}

    result = _pack(False, data, rows, limit, diagnostics, **kwargs)

    # 完整新Human重新计量；只省略允许重复引用的部分，玩家输入、最新历史和合同必须保留。
    assert result == expected
    assert (data, rows, kwargs) == untouched
    assert result["player_input"] == untouched[0]["player_input"]
    assert result["story_so_far"] == actor._story_so_far_text(rows)
    assert diagnostics["history_included_revisions"] == [8]
    assert diagnostics["history_dropped_revisions"] == []
    assert diagnostics["final_tokens"] == limit
    assert Counter(calls)[SYSTEM] == 1
    assert Counter(calls)[_wire(data)] == 1
    assert Counter(calls)[_wire(result)] == 1
