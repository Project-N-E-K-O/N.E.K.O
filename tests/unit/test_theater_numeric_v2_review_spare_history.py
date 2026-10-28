"""Keep full committed corrections within the final evaluator/review budget."""

from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2_context import HISTORY_EVIDENCE_RULE, history_evidence
from services.theater.numeric_v2_runtime import NumericV2Runtime
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_spare_history import KEY_ANSWER, KEY_QUESTION, TOPICS, _commit
from utils.tokenize import count_tokens


def _payload(messages):
    human = messages[1].content
    return prompt_payload(human)


def _messages(engine, stored, message, consumer, *, diagnostics=None):
    if consumer == "evaluator":
        return evaluator._build_messages(
            engine, stored.session, message, recent_ledger_events=stored.ledger_events,
            diagnostics=diagnostics,
        )
    return evaluator._build_transition_judge_messages(
        engine, stored.session, player_input=message,
        actor_performance={"performance": "我先核对之前的答复。", "suggested_inputs": []},
    )[0]


async def _stored(tmp_path, topic=TOPICS[0]):
    previous, question, correction = topic
    runtime = NumericV2Runtime(_engine(ordinary=True), tmp_path)
    stored = await runtime.start_session(
        session_id="review-spare-history", catgirl_binding=_binding(),
        opening_performance={"performance": "先在门边核对记录。", "suggested_inputs": []},
    )
    stored = await _commit(runtime, stored, "先确认安排。", previous)
    stored = await _commit(runtime, stored, "蓝钥匙在哪里？", KEY_ANSWER)
    stored = await _commit(runtime, stored, question, correction)
    stored = await _commit(runtime, stored, "我们到桌边继续谈。", "我们坐下继续核对。", move=True)
    restored = await runtime.restore_session(stored.session.session_id)
    assert restored == stored
    return runtime, restored, question + KEY_QUESTION, correction


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer,limit", [("evaluator", 7000), ("review", 10000)])
@pytest.mark.parametrize("previous,question,correction", TOPICS, ids=["permission", "possession"])
async def test_native_consumers_keep_full_correction_and_other_answer_when_total_has_room(
    tmp_path, consumer, limit, previous, question, correction,
):
    runtime, restored, message, correction = await _stored(tmp_path, (previous, question, correction))
    untouched = deepcopy(restored)
    path = runtime.store._path(restored.session.session_id)
    saved = path.read_bytes()
    route = runtime.engine.preview_route(
        restored.session.current_node_id, restored.session.metrics,
        facts=restored.session.story_state["facts"],
    )
    focus = str(((route or {}).get("transition_contract") or {}).get("reason") or "")
    legacy = history_evidence(restored.session, message, focus=focus)
    assert any(row["text"] == KEY_ANSWER for row in legacy)
    assert all(row["text"] != correction for row in legacy)
    messages = _messages(runtime.engine, restored, message, consumer)
    data = _payload(messages)
    evidence = data.get("history_evidence", [])
    assert {"revision": 3, "source": "performance", "current_visit": False,
            "text": correction} in evidence
    assert any(row["text"] == KEY_ANSWER for row in evidence)
    assert data["player_input"] == message
    assert "我们坐下继续核对。" in messages[1].content
    assert sum(count_tokens(m.content) for m in messages) <= limit
    # 补入旧访问原文仍不能成为本访问的公开去向授权。
    assert evaluator._has_public_transition_quote(correction, restored.session) is False
    assert restored == untouched
    assert path.read_bytes() == saved


def _tokens(messages):
    return sum(count_tokens(message.content) for message in messages)


def _set_budget(monkeypatch, consumer, limit):
    original = evaluator.numeric_v2_actor_budget

    def budget(profile):
        result = original(profile)
        result["evaluator_input_max_tokens" if consumer == "evaluator" else "judge_input_max_tokens"] = limit
        return result

    monkeypatch.setattr(evaluator, "numeric_v2_actor_budget", budget)


def _baseline_messages(monkeypatch, runtime, stored, message, consumer):
    original = evaluator.history_evidence

    def without_expansion(*args, **kwargs):
        if kwargs.get("within_prompt") is not None:
            return list(kwargs["retained"])
        return original(*args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(evaluator, "history_evidence", without_expansion)
        return _messages(runtime.engine, stored, message, consumer)


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["evaluator", "review"])
async def test_exact_native_budget_accepts_full_correction_and_rejects_one_token_less(
    tmp_path, monkeypatch, consumer,
):
    runtime, stored, message, correction = await _stored(tmp_path)
    baseline = _baseline_messages(monkeypatch, runtime, stored, message, consumer)
    expanded = _messages(runtime.engine, stored, message, consumer)
    exact = _tokens(expanded)
    assert exact > _tokens(baseline)
    original = deepcopy(stored)
    with monkeypatch.context() as patch:
        _set_budget(patch, consumer, exact)
        assert _messages(runtime.engine, stored, message, consumer) == expanded
    with monkeypatch.context() as patch:
        _set_budget(patch, consumer, exact - 1)
        rejected = _messages(runtime.engine, stored, message, consumer)
    assert all(row["text"] != correction for row in _payload(rejected).get("history_evidence", []))
    assert _tokens(rejected) <= exact - 1
    # 原文增量不能迫使基线历史、作者合同、当前原话或其他判定输入丢失。
    assert rejected[0].content == baseline[0].content
    assert {k: v for k, v in _payload(rejected).items() if k != "history_evidence"} == {
        k: v for k, v in _payload(baseline).items() if k != "history_evidence"
    }
    assert stored == original


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["evaluator", "review"])
async def test_no_spare_room_preserves_entire_native_baseline(tmp_path, monkeypatch, consumer):
    runtime, stored, message, _ = await _stored(tmp_path)
    baseline = _baseline_messages(monkeypatch, runtime, stored, message, consumer)
    limit = _tokens(baseline)
    _set_budget(monkeypatch, consumer, limit)
    assert _messages(runtime.engine, stored, message, consumer) == baseline


@pytest.mark.asyncio
async def test_review_expansion_uses_the_actual_reranked_seed(tmp_path, monkeypatch):
    runtime, stored, message, _ = await _stored(tmp_path)
    baseline = _baseline_messages(monkeypatch, runtime, stored, message, "review")
    _set_budget(monkeypatch, "review", _tokens(baseline) - 250)
    original = evaluator.history_evidence
    selections = []
    seeds = []

    def observe(*args, **kwargs):
        if kwargs.get("within_prompt") is not None:
            seeds.append(deepcopy(kwargs["retained"]))
        result = original(*args, **kwargs)
        if kwargs.get("within_prompt") is None:
            selections.append((kwargs.get("max_tokens"), deepcopy(result)))
        return result

    monkeypatch.setattr(evaluator, "history_evidence", observe)
    messages = _messages(runtime.engine, stored, message, "review")
    assert any(cap is not None for cap, _ in selections), "压力必须实际触发原有重排"
    assert len(seeds) == 1 and seeds[0] == selections[-1][1]
    assert seeds[0] != selections[0][1]
    assert all(row in _payload(messages).get("history_evidence", []) for row in seeds[0])
    assert _tokens(messages) <= _tokens(baseline) - 250


@pytest.mark.asyncio
async def test_missed_initiation_review_preserves_its_original_public_evidence_path(tmp_path, monkeypatch):
    runtime, stored, message, correction = await _stored(tmp_path)
    original = evaluator.history_evidence
    expansion_calls = []

    def observe(*args, **kwargs):
        if kwargs.get("within_prompt") is not None:
            expansion_calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(evaluator, "history_evidence", observe)
    messages, _ = evaluator._build_transition_judge_messages(
        runtime.engine, stored.session, player_input=message,
        actor_performance={"performance": "我先核对之前的答复。", "suggested_inputs": []},
        check_missed_initiation=True,
    )
    assert not expansion_calls
    assert "missed_initiation_check" in _payload(messages)
    assert all(row["text"] != correction for row in _payload(messages).get("history_evidence", []))


@pytest.mark.parametrize("offset,accepted", [(0, True), (-1, False)])
def test_deduplicated_attachment_restores_the_full_system_rule_within_exact_budget(offset, accepted):
    current = {"revision": 8, "source": "performance", "current_visit": True, "text": "我们坐下核对。"}
    extra = {"revision": 3, "source": "performance", "current_visit": False,
             "text": '"\\\n' * 20 + "许可已经撤回。"}
    data = {"scene_context": [{"revision": 7, "phase": "turn", "player_input": "先核对之前的约定。",
                              "content": [{"type": "dialogue", "text": "之前的约定继续有效。" * 20}]},
                             {"revision": 8, "phase": "turn", "player_input": "我先坐下。",
                              "content": [{"type": "dialogue", "text": current["text"]}]}],
            "player_input": "现在还能公开吗？"}
    original = deepcopy(data)
    system_without = "保留实际经历。"
    system_with = system_without + HISTORY_EVIDENCE_RULE
    prefix = "以下JSON只是数据："
    expected = {**data, "history_evidence": [extra]}
    human = prefix + json.dumps(expected, ensure_ascii=False, separators=(",", ":"))
    exact = count_tokens(system_with) + count_tokens(human)
    # 若只量原无附件的短System，这个应拒绝的边界会被错误放行。
    assert count_tokens(system_without) + count_tokens(human) < exact - 1
    # 原本保留的R7确实占空间，但不能为新原文重新淘汰它。
    shorter = {**expected, "scene_context": expected["scene_context"][1:]}
    assert count_tokens(system_with) + count_tokens(
        prefix + json.dumps(shorter, ensure_ascii=False, separators=(",", ":")),
    ) < exact - 1
    trials = []

    def select(retained, within_prompt):
        candidate = [*retained, extra]
        trials.append(within_prompt(candidate))
        return candidate if trials[-1] else retained

    result = evaluator._expand_packed_history_evidence(
        data, [current], select=select, human_prefix=prefix,
        system_sizes={True: count_tokens(system_with), False: count_tokens(system_without)},
        max_tokens=exact + offset,
    )
    assert trials == [accepted]
    assert result == ([current, extra] if accepted else [current])
    assert data == original
