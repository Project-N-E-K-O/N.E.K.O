"""Formal transition history is committed prose, not an old target-output template."""

from copy import deepcopy
from dataclasses import asdict, replace
import json

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_prompt_contract import _session, _story_state_at


def _fit(data, **kwargs):
    return actor._fit_turn_prompt_data(system_prompt="规则", human_prefix="原文：\n",
        data=data, render_committed_history=True, **kwargs)


def _tokens(data):
    return actor.count_tokens("规则") + actor.count_tokens(
        "原文：\n" + actor.render_prompt_context(data))


def test_formal_request_does_not_present_previous_entry_as_target_output():
    engine = _engine()
    base = _session(engine)
    # Actual bread R12 failure shape: the source's older entry still carried target_opening.
    narration = "老谢面前摊开着几张城市地图和几份招生简章，神情略显犹豫。"
    spoken = "（停下手中的笔）看你眉头锁着，是在纠结去哪个城市？"
    session = replace(base, revision=9, story_state=_story_state_at(base, 9), node_turn_count=1,
        performance_history=(
            {"revision": 8, "from_node_id": "previous", "to_node_id": "start",
             "input_text": "好，周末带资料来。", "segments": [
                 {"phase": "source_response", "performance": "上幕收尾，不重新作为当前输入。"},
                 {"phase": "transition_bridge", "scene_narration": "周末的下午，店里没什么人。"},
                 {"phase": "target_opening", "scene_narration": narration, "performance": spoken},
             ]},
            {"revision": 9, "from_node_id": "start", "to_node_id": "start",
             "input_text": "我会先查清生活成本，再比较学校。", "performance": "等社区的人来，一起听活动安排。"},
        ))
    original = deepcopy(asdict(session))
    message = "好，我们一起听听活动安排。"
    outcome = engine.resolve_turn(session, TurnRequestV2("history-shape", 9, message), (),
                                  scene_complete=True, natural_ending_ready=True)
    assert outcome.ledger_event["from_node_id"] != outcome.ledger_event["to_node_id"]
    messages = actor._turn_messages(engine, session, outcome, message, "温和", "猫娘", "哥哥")
    data = prompt_payload(messages[1].content)
    entry = data["recent_context"][0]
    assert set(entry) == {"revision", "player_input", "committed_text"}
    assert entry["revision"] == 8 and entry["player_input"] == ""
    assert entry["committed_text"] == f"场景／NPC | 周末的下午，店里没什么人。\n场景／NPC | {narration}\n角色（你） | {spoken}"
    assert data["recent_context"][1]["player_input"] == "我会先查清生活成本，再比较学校。"
    assert data["player_input"] == message
    assert "上幕收尾" not in entry["committed_text"]
    assert asdict(session) == original


def test_render_keeps_npc_fixed_text_actions_and_player_input_once_in_order():
    player = "请管事回答，我暂不打开箱子。"
    data = {"recent_context": [{"revision": 4, "phase": "turn", "player_input": player,
        "segments": [
            {"phase": "source_response", "scene_narration": "管事回答：“许可还没下来。”", "performance": ""},
            {"phase": "transition_bridge", "scene_narration": "两人仍站在门外。"},
            {"phase": "target_opening", "scene_narration": "箱子没有打开。",
             "fixed_narration_before": "告示原文：\n  未经许可，不得开启。  ",
             "performance": "（收回伸向锁扣的手）那就等许可。",
             "fixed_narration_after": "旧信：‘昨天门开过。’"},
        ]}]}
    original = deepcopy(data)
    result = _fit(data)
    assert result["recent_context"] == [{"revision": 4, "player_input": player,
        "committed_text": "场景／NPC | 管事回答：“许可还没下来。”\n场景／NPC | 两人仍站在门外。\n场景／NPC | 箱子没有打开。\n"
        "已展示的作者原文 | 告示原文：\n  未经许可，不得开启。  \n"
        "角色（你） | （收回伸向锁扣的手）那就等许可。\n已展示的作者原文 | 旧信：‘昨天门开过。’"}]
    assert json.dumps(result, ensure_ascii=False).count(player) == 1
    assert data == original


@pytest.mark.parametrize("trim", [False, True])
def test_raw_history_dedup_runs_before_render_and_restores_evicted_evidence(trim):
    quote = "许可尚未批准，箱子仍由玩家保管。"
    history = [
        {"revision": 1, "player_input": "还要查什么？" * 120, "performance": quote},
        {"revision": 2, "player_input": "暂时不动。", "performance": "我也不碰锁扣。"},
    ]
    evidence = [
        {"revision": 1, "source": "performance", "current_visit": True, "text": quote},
        {"revision": 1, "source": "player_input", "current_visit": True, "text": quote},
        {"revision": 0, "source": "performance", "current_visit": False, "text": quote},
    ]
    data = {"recent_context": history, "history_evidence": evidence}
    original = deepcopy(data)
    trimmed = {"recent_context": [{"revision": 2, "player_input": "暂时不动。", "committed_text": "角色（你） | 我也不碰锁扣。"}],
               "history_evidence": evidence}
    diagnostics = {}
    result = _fit(data, max_tokens=_tokens(trimmed) if trim else 10000, diagnostics=diagnostics)
    assert result["history_evidence"] == (evidence if trim else evidence[1:])
    assert [r["revision"] for r in result["recent_context"]] == ([2] if trim else [1, 2])
    assert diagnostics["final_tokens"] == _tokens(result)
    assert diagnostics["history_dropped_revisions"] == ([1] if trim else [])
    assert data == original


def test_budget_counts_rendered_representation_and_preserves_latest_complete_turn():
    data = {"recent_context": [{"revision": 1, "performance": "好。"}]}
    rendered = {"recent_context": [{"revision": 1, "player_input": "", "committed_text": "角色（你） | 好。"}]}
    assert _tokens(rendered) > _tokens(data)
    exact = _tokens(rendered)
    diagnostics = {}
    assert _fit(data, max_tokens=exact, diagnostics=diagnostics) == rendered
    assert diagnostics["final_tokens"] == exact
    with pytest.raises(actor.NumericV2ActorError, match="budget_exceeded"):
        _fit(data, max_tokens=exact - 1)


def test_default_fit_retains_existing_raw_representation():
    data = {"recent_context": [{"revision": 1, "phase": "turn", "segments": [
        {"phase": "target_opening", "scene_narration": "门仍关着。", "performance": "先等。"}]}]}
    assert actor._fit_turn_prompt_data(system_prompt="规则", human_prefix="原文：\n", data=data) == data


@pytest.mark.parametrize("phase", ["opening", "ordinary"])
def test_other_phases_do_not_use_formal_history_packer(monkeypatch, phase):
    def unexpected(**kwargs):
        raise AssertionError("ordinary/opening entered the formal history packer")
    monkeypatch.setattr(actor, "_fit_turn_prompt_data", unexpected)
    engine = _engine()
    if phase == "opening":
        messages = actor._opening_messages(engine, "温和", "猫娘", "哥哥")
        data = prompt_payload(messages[1].content)
        assert "current_story_beat" in data
    else:
        session = _session(engine)
        outcome = engine.resolve_turn(session, TurnRequestV2("ordinary", 0, "先看看。"), ())
        messages = actor._turn_messages(engine, session, outcome, "先看看。", "温和", "猫娘", "哥哥")
        data = prompt_payload(messages[1].content)
        assert list(data) == ["role", "current_scene", "pacing", "next_scene", "story_so_far", "player_input"]
    assert "committed_text" not in json.dumps(data, ensure_ascii=False)
