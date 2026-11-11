"""Fixed originals are audited at their real render position, independently of story topic."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_evaluator as ev
from services.theater.numeric_v2_performance import performance_content_blocks
from tests.unit.test_theater_numeric_v2_review_protocol import _client, _session
from tests.utils.theater_prompt import contract_audit_payload


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


def _piece(text, position):
    return {"id": "original", "node_id": "start", "text": text, "position": position, "bindings": {}}


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["before", "after", "dedup_echo", "segments", "legacy_ignored"])
async def test_only_actual_fixed_slots_are_marked_when_actor_text_is_identical(monkeypatch, kind):
    original = "档案原文：保管位置是东侧库房。"
    body = {"scene_narration": "材料放在桌上。", "performance": original,
            "fixed_narrations": [_piece(original, "after" if kind == "after" else "before")]}
    expected = [1]
    if kind == "after": expected = [2]
    if kind == "dedup_echo":
        body["scene_narration"] = original
        expected = [0]
    if kind == "segments":
        body = {"segments": [{"phase": "source_response", "scene_narration": original},
                              {"phase": "transition_bridge", "scene_narration": "时间向前流转，现场随之转换。"},
                              {"phase": "target_opening", **body}]}
        expected = [2]
    if kind == "legacy_ignored":
        body = {"content": [{"type": "narration", "text": original}], "fixed_narrations": [_piece(original, "before")]}
        expected = []
    before = deepcopy(body)
    calls = []

    def respond(messages, data, kwargs):
        calls.append(data)
        candidates = [row for row in data["sources"] if row["id"].startswith("c")]
        assert [(row["type"], row["text"]) for row in candidates] == [
            (row["type"], row["text"]) for row in performance_content_blocks(body)]
        assert [i for i, row in enumerate(candidates) if row.get("program_fixed_original")] == expected
        return contract_audit_payload({"violations": []}, messages)

    _client(monkeypatch, respond)
    assert await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": ["不得改写档案内容。"]}}, session=_session(),
        actor_performance=body, player_input="先讨论。", include_all_segments=True) == ()
    assert len(calls) == 1 and body == before


@pytest.mark.asyncio
@pytest.mark.parametrize("original,cause", [
    ("录音里传来老人的请求。", "角色按下播放键。"),
    ("档案原文记载借阅已结束。", "角色打开档案卷。"),
])
@pytest.mark.parametrize("order", ["before", "after", "missing", "history", "new_action_after"])
async def test_existing_contract_audit_checks_original_cause_before_display_without_an_extra_stage(
    monkeypatch, original, cause, order,
):
    from dataclasses import replace

    session = _session()
    if order == "history":
        session = replace(session, performance_history=({"revision": 1, "performance": cause},))
    body = {"scene_narration": cause if order in {"before", "new_action_after"} else "房间很安静。",
            "performance": f"（{cause}）请看。" if order == "after" else "（合上登记簿）可以接着讨论。" if order == "new_action_after" else "请看。",
            "fixed_narrations": [_piece(original, "before")]}
    calls = []

    def respond(messages, data, kwargs):
        calls.append(data)
        assert "原文后补执行" in messages[0].content
        if "finding" in data:
            assert any(row.get("program_fixed_original") for row in data["visible_order"])
            return {"verdict": "violation", "reason": "原文已先交付，必要前因缺失或出现在其后。", "clearance": []}
        rule = next(row for row in data["author_boundaries"] if "程序固定原文" in row["text"])
        fixed = next(row for row in data["sources"] if row.get("program_fixed_original"))
        return contract_audit_payload({"violations": [{"rule_index": rule["rule_index"],
            "candidate_ref": fixed["id"], "quote": fixed["text"], "prior_ref": ""}]
            if order in {"after", "missing"} else []}, messages)

    _client(monkeypatch, respond)
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=session, actor_performance=body,
        player_input="请你来展示，我在旁边等。", author_boundaries_only=True)
    assert bool(result) is (order in {"after", "missing"})
    assert len(calls) == (2 if result else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("proof", ["later_same_phase", "same_original", "earlier_same_phase", "history", "other_rule_same_original"])
async def test_fixed_original_clearance_cannot_use_its_own_effect_or_a_later_trigger(monkeypatch, proof):
    from dataclasses import replace

    original = "档案原文记载：上周已经借出档案。"
    cause = "角色打开档案卷。"
    session = _session()
    if proof == "history":
        session = replace(session, performance_history=({"revision": 1, "performance": cause},))
    body = {"scene_narration": cause if proof == "earlier_same_phase" else "屋里很安静。",
            "performance": "（打开档案卷）请看。", "fixed_narrations": [_piece(original, "before")]}
    other_rule = proof == "other_rule_same_original"

    def respond(messages, data, kwargs):
        if "sources" in data:
            rule = next(row for row in data["author_boundaries"] if (
                row["text"] == "本幕不得借出档案。" if other_rule else "程序固定原文" in row["text"]))
            return contract_audit_payload({"violations": [{"rule_index": rule["rule_index"],
                "candidate_ref": "c1", "quote": "上周已经借出档案", "prior_ref": "h0" if proof == "history" else ""}]}, messages)
        ref = {"later_same_phase": "c2", "same_original": "c1", "earlier_same_phase": "c0",
               "history": "h0", "other_rule_same_original": "c1"}[proof]
        return {"verdict": "clear", "reason": "核对原文展示前的直接前因，或档案记载的过去事件。", "clearance": [ref]}

    _client(monkeypatch, respond)
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": ["本幕不得借出档案。"] if other_rule else []}},
        session=session, actor_performance=body, player_input="请你展示，我在旁边等。", author_boundaries_only=True)
    assert bool(result) is (proof in {"later_same_phase", "same_original"})
