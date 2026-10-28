"""Use only spare Actor capacity to retain complete committed corrections."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater.numeric_v2_context import history_evidence, performance_history_records
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding
from utils.tokenize import count_tokens


PROFILE = "性格: 温和谨慎。\n说话风格: 简短具体。"
KEY_ANSWER = (
    "蓝钥匙一直放在一楼值班室的红色抽屉里，钥匙上系着白色标签，标签写着蓝钥匙。"
    + "对照标签和抽屉的标记逐项核对。" * 60
)
KEY_QUESTION = "蓝钥匙是在一楼值班室红色抽屉里，上面系着写有蓝钥匙的白色标签吗？"
TOPICS = [
    ("观察日记可以公开展示。", "观察日记可以公开展示吗？",
     "先把日记展示有关的说明逐条核对清楚，避免遗漏上下文。" * 30
     + "但公开许可已经撤回，现在必须保密。"),
    ("银纹胸针暂放在木盒里。", "银纹胸针还在木盒里吗？",
     "先把胸针保管有关的说明逐条核对清楚，避免遗漏上下文。" * 30
     + "但已经交给保管员，木盒里没有了。"),
]


async def _commit(runtime, current, message, text, *, move=False):
    outcome = runtime.prepare_turn(
        current, TurnRequestV2(f"r{current.session.revision + 1}", current.session.revision, message),
        (), transition_intent="initiate" if move else "unclear",
    )
    if move:
        assert outcome.session.current_node_id == "middle"
        performance = runtime.engine.finalize_transition_performance(outcome, {
            "source_performance": "我们到桌边继续核对。",
            "bridge_scene_narration": "两人走到窗边的长桌旁。",
            "target_scene_narration": "长桌旁摆着两把椅子。",
            "target_performance": text,
            "suggested_inputs": [],
        }, target_opening="长桌旁摆着两把椅子。")
    else:
        performance = {"performance": text, "suggested_inputs": []}
    return await runtime.commit_turn(outcome, performance)


def _payload(messages):
    human = messages[1].content
    return prompt_payload(human)


def _evidence(payload):
    if "history_evidence" in payload:
        return payload["history_evidence"]
    marker = "history_evidence（已提交原文）："
    story = payload["story_so_far"]
    return json.JSONDecoder().raw_decode(story.split(marker, 1)[1])[0] if marker in story else []


@pytest.mark.asyncio
@pytest.mark.parametrize("formal", [False, True])
@pytest.mark.parametrize("previous,question,correction", TOPICS, ids=["permission", "possession"])
async def test_native_actor_keeps_complete_correction_and_other_answer_when_total_has_room(
    tmp_path, formal, previous, question, correction,
):
    runtime = NumericV2Runtime(_engine(ordinary=True), tmp_path)
    stored = await runtime.start_session(
        session_id="spare-history", catgirl_binding=_binding(),
        opening_performance={"performance": "先在门边核对记录。", "suggested_inputs": []},
    )
    stored = await _commit(runtime, stored, "先确认安排。", previous)
    stored = await _commit(runtime, stored, "蓝钥匙在哪里？", KEY_ANSWER)
    stored = await _commit(runtime, stored, question, correction)
    stored = await _commit(runtime, stored, "我们到桌边继续谈。", "我们坐下继续核对。", move=True)
    restored = await runtime.restore_session(stored.session.session_id)
    assert restored == stored
    untouched = deepcopy(restored)
    path = runtime.store._path(stored.session.session_id)
    saved = path.read_bytes()
    message = question + KEY_QUESTION
    route = runtime.engine.preview_route(restored.session.current_node_id, restored.session.metrics,
                                         facts=restored.session.story_state["facts"])
    focus = str(((route or {}).get("transition_contract") or {}).get("reason") or "")
    legacy = history_evidence(restored.session, message, focus=focus)
    # This is genuine attachment competition, not a relevance miss or missing API:
    # the old 1500-token selection retains the other whole answer but not the correction.
    assert any(row["text"] == KEY_ANSWER for row in legacy)
    assert all(row["text"] != correction for row in legacy)
    assert count_tokens(json.dumps(legacy, ensure_ascii=False, separators=(",", ":"))) <= 1500
    outcome = runtime.prepare_turn(
        restored, TurnRequestV2("ask-current", 4, message), (),
        transition_intent="initiate" if formal else "unclear",
    )
    assert (outcome.session.current_node_id != restored.session.current_node_id) is formal
    messages = actor._turn_messages(
        runtime.engine, restored.session, outcome, message, PROFILE, "小岚", "林舟",
        recent_ledger_events=restored.ledger_events,
    )
    data = _payload(messages)
    evidence = _evidence(data)
    assert {"revision": 3, "source": "performance", "current_visit": False,
            "text": correction} in evidence
    assert any(row["text"] == KEY_ANSWER for row in evidence)
    assert data["player_input"] == message
    assert "温和谨慎" in messages[1].content
    assert "我们坐下继续核对。" in messages[1].content
    assert sum(count_tokens(m.content) for m in messages) <= 10000
    assert restored == untouched
    assert path.read_bytes() == saved


SYSTEM = "承接实际经历，保留玩家选择与角色边界。"
PREFIX = "上下文："
SEED = {"revision": 1, "source": "performance", "current_visit": False,
        "text": "观察日记此前允许展示。"}
EXTRA = {"revision": 2, "source": "performance", "current_visit": False,
         "text": "核对完这份记录后，公开许可已经撤回。"}


def _wire(data):
    return PREFIX + actor.render_prompt_context(data)


def _packing_input(formal):
    rows = [{"revision": 5, "player_input": "我先坐下。", "performance": "我们坐下核对。"}]
    if formal:
        data = {"recent_context": rows, "player_input": "先回答问题，再移动。",
                "acting_context": {"persona": "谨慎。"},
                "transition": {"contract": {"must_preserve": ["许可不扩大"]}},
                "history_evidence": [deepcopy(SEED)]}
    else:
        data = {"role": "谨慎的角色与原始人格合同。", "current_scene": "当前幕完整合同。",
                "story_so_far": "", "pacing": "先回答问题，不替玩家接受。",
                "next_scene": "目标尚未发生。", "player_input": "先回答问题，再移动。"}
    return data, rows


def _fit(formal, data, rows, limit, diagnostics, *, evidence=None, expander=None):
    common = {"system_prompt": SYSTEM, "human_prefix": PREFIX, "data": data,
              "max_tokens": limit, "diagnostics": diagnostics}
    if expander is not None:
        common["evidence_expander"] = expander
    if formal:
        if evidence is not None:
            data = {**data, "history_evidence": evidence}
            common["data"] = data
        return actor._fit_turn_prompt_data(**common, render_committed_history=True)
    return actor._fit_simple_turn_prompt_data(
        **common, history_rows=rows, evidence_records=[deepcopy(SEED)] if evidence is None else evidence,
    )


@pytest.mark.parametrize("formal", [False, True])
def test_spare_history_uses_exact_final_wire_budget_without_changing_baseline(formal):
    data, rows = _packing_input(formal)
    original = deepcopy((data, rows))
    # 引号、反斜杠和换行需按实际 Human JSON 计量；普通六块会转义两次。
    extra = {**EXTRA, "text": '"\\\n' * 90 + EXTRA["text"]}
    baseline = _fit(formal, data, rows, 10000, {})
    expected = _fit(formal, data, rows, 10000, {}, evidence=[SEED, extra])
    exact = count_tokens(SYSTEM) + count_tokens(_wire(expected))
    assert exact > count_tokens(SYSTEM) + count_tokens(_wire(baseline))
    calls = []

    def expand(retained, within_prompt):
        calls.append(deepcopy(retained))
        trial = [*retained, extra]
        return trial if within_prompt(trial) else retained

    diagnostics = {}
    fitted = _fit(formal, data, rows, exact, diagnostics, expander=expand)
    assert fitted == expected
    assert diagnostics["final_tokens"] == exact
    assert diagnostics["history_included_revisions"] == [5]
    assert diagnostics["history_dropped_revisions"] == []
    assert calls == [[SEED]]
    # 少一个 token 就保留原请求；试装失败不能留下正文片段或挤掉原历史。
    diagnostics = {}
    rejected = _fit(formal, data, rows, exact - 1, diagnostics, expander=expand)
    assert rejected == baseline
    assert diagnostics["final_tokens"] == count_tokens(SYSTEM) + count_tokens(_wire(baseline))
    assert diagnostics["history_included_revisions"] == [5]
    assert (data, rows) == original


@pytest.mark.parametrize("formal", [False, True])
def test_no_spare_capacity_keeps_whole_baseline_and_latest_turn(formal):
    data, rows = _packing_input(formal)
    baseline = _fit(formal, data, rows, 10000, {})
    limit = count_tokens(SYSTEM) + count_tokens(_wire(baseline))
    trials = []

    def expand(retained, within_prompt):
        trial = [*retained, EXTRA]
        trials.append(within_prompt(trial))
        return trial if trials[-1] else retained

    diagnostics = {}
    fitted = _fit(formal, data, rows, limit, diagnostics, expander=expand)
    assert trials == [False]
    assert fitted == baseline
    assert diagnostics["final_tokens"] == limit
    assert diagnostics["history_included_revisions"] == [5]
    assert diagnostics["history_dropped_revisions"] == []


@pytest.mark.parametrize("formal", [False, True])
def test_expansion_never_evicts_an_older_turn_that_baseline_retained(formal):
    data, latest = _packing_input(formal)
    rows = [{"revision": 4, "player_input": "先确认之前的约定。",
             "performance": "约定继续有效，不因下一次问话取消。" * 30}, *latest]
    if formal:
        data["recent_context"] = rows
    baseline = _fit(formal, data, rows, 10000, {})
    limit = count_tokens(SYSTEM) + count_tokens(_wire(baseline))
    # 新原文若重走旧裁剪流程，可通过牺牲R4塞入；扩充不能使用这种空间。
    shorter_data = {**data, "recent_context": latest} if formal else data
    shorter = _fit(formal, shorter_data, latest, 10000, {}, evidence=[SEED, EXTRA])
    assert count_tokens(SYSTEM) + count_tokens(_wire(shorter)) < limit

    def expand(retained, within_prompt):
        trial = [*retained, EXTRA]
        return trial if within_prompt(trial) else retained

    diagnostics = {}
    fitted = _fit(formal, data, rows, limit, diagnostics, expander=expand)
    assert fitted == baseline
    assert diagnostics["history_included_revisions"] == [4, 5]
    assert diagnostics["history_dropped_revisions"] == []


def _source_session(count=20):
    records = tuple({"revision": i, "from_node_id": "old", "to_node_id": "old",
                     "performance": "日记状态原文。" * 100 + f"记录{i}：保持保密。"}
                    for i in range(1, count + 1))
    return SimpleNamespace(current_node_id="current", revision=count + 1,
        actor_budget_profile="balanced", opening_performance={}, performance_history=(*records, {
            "revision": count + 1, "from_node_id": "old", "to_node_id": "current",
            "segments": [{"phase": "target_opening", "performance": "我们坐下休息。"}],
        }))


def test_context_expansion_keeps_legacy_selection_and_rejects_foreign_seed():
    session = _source_session()
    before = deepcopy(session)
    legacy = history_evidence(session, "日记状态？")
    assert legacy
    original = performance_history_records(session)[0]
    invalid = [{**original, "text": "伪造的新许可。"},
               {**original, "current_visit": True}, {**original, "source": "player_input"}]
    # 仅回调路径可借请求容量；默认接口依旧保持1500-token筛选，不能被种子偷偷改写。
    assert history_evidence(session, "日记状态？", retained=invalid) == legacy
    seed = [legacy[0], *invalid]
    expanded = history_evidence(session, "日记状态？", retained=seed, within_prompt=lambda rows: False)
    assert expanded == [legacy[0]]
    assert seed == [legacy[0], *invalid]
    assert session == before


def test_context_expansion_bounds_trial_work_and_preserves_full_source_units():
    session = _source_session(40)
    originals = performance_history_records(session)
    seed = history_evidence(session, "日记状态？")
    trials = []

    def no_room(rows):
        trials.append(deepcopy(rows))
        assert all(row in originals for row in rows)
        assert all(row in rows for row in seed)
        return False

    assert history_evidence(session, "日记状态？", retained=seed, within_prompt=no_room) == seed
    assert 0 < len(trials) <= 12
    accepted = history_evidence(session, "日记状态？", retained=seed, within_prompt=lambda rows: True)
    assert len(accepted) == 12
    assert all(row in accepted for row in seed)
    assert all(row in originals for row in accepted)
    assert [row["revision"] for row in accepted] == sorted(row["revision"] for row in accepted)
