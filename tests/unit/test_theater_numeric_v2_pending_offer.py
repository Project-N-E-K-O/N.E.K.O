"""通过实际提交、恢复和分叉复现：保留提议的后续对白不能取代最初的邀请。"""

import json

import pytest

from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_evaluator import _build_messages
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


async def _commit(runtime, stored, text, *, offer=False, intent="unclear", narration=""):
    """使用正式 Runtime 的锁存和写盘，避免手写历史漏掉此次根因中的状态合并。"""

    outcome = runtime.prepare_turn(stored,
        TurnRequestV2(f"turn_{stored.session.revision + 1}", stored.session.revision, "我先问个细节。"),
        (), transition_intent=intent)
    performance = {"performance": text, "suggested_inputs": ["好，就按这个安排。"]}
    if narration:
        performance["scene_narration"] = narration
    outcome, performance = runtime.engine.finalize_transition_offer_state(outcome, performance, new_offer=offer)
    return await runtime.commit_turn(outcome, performance)


@pytest.mark.asyncio
@pytest.mark.parametrize("narrated", [False, True])
async def test_pending_offer_survives_followups_restore_and_fork(tmp_path, narrated):
    """不靠最新正文猜提议；旁白邀请与普通对白邀请都要保留原始出处。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    runtime = NumericV2Runtime(engine, tmp_path)
    stored = await runtime.start_session(session_id="pending_origin", catgirl_binding=_binding(), opening_performance=_opening())
    invitation = "我们现在去长街寻找旧信，好吗？"
    stored = await _commit(runtime, stored, "我等你决定。" if narrated else invitation,
        offer=True, narration=invitation if narrated else "")
    original = stored.session.performance_history[-1]
    stored = await _commit(runtime, stored, "街上灯还亮着。")
    stored = await _commit(runtime, stored, "不会很远，你慢慢考虑。")
    assert all(record["transition_offered"] for record in stored.session.performance_history)
    restored = await runtime.restore_session(stored.session.session_id)
    forked = await runtime.fork_session_for_test(stored.session.session_id, session_id="pending_fork", through_revision=3)
    for current in (stored, restored, forked):
        messages = _build_messages(engine, current.session, "好，我们现在去长街。", recent_ledger_events=current.ledger_events)
        pending = json.loads(messages[1].content.split("：", 1)[1])["pending_transition"]
        assert invitation in pending["visible_performance"]
        assert "不会很远" not in pending["visible_performance"]
        assert pending["suggested_inputs"] == original["suggested_inputs"]
        # Actor 在重试前后也必须看到同一原提议，且账本本身不进入演绎输入。
        outcome = runtime.prepare_turn(current,
            TurnRequestV2("followup", current.session.revision, "我再想一想。"), (), transition_intent="unclear")
        for retry_hint in ("", "请修正未提交的重复正文。"):
            actor_messages = _turn_messages(engine, current.session, outcome, "我再想一想。", "安静克制。", "测试猫娘", "哥哥",
                retry_hint=retry_hint, recent_ledger_events=current.ledger_events)
            actor_data = json.loads(actor_messages[1].content.split("：", 1)[1])
            assert invitation in actor_data["pacing"]
            assert "ledger_events" not in actor_data


@pytest.mark.asyncio
async def test_reject_then_new_offer_uses_new_origin_even_when_all_records_are_true(tmp_path):
    """同轮拒绝旧提议再提出新提议时，必须读取 Ledger 的态度边界，不能只找连续 true 的开头。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    runtime = NumericV2Runtime(engine, tmp_path)
    stored = await runtime.start_session(session_id="replacement_origin", catgirl_binding=_binding(), opening_performance=_opening())
    stored = await _commit(runtime, stored, "我们现在去长街，好吗？", offer=True)
    stored = await _commit(runtime, stored, "那就改为结束今晚的谈话，好吗？", offer=True, intent="reject")
    stored = await _commit(runtime, stored, "你可以慢慢考虑。")
    assert all(record["transition_offered"] for record in stored.session.performance_history)
    pending = json.loads(_build_messages(engine, stored.session, "好，那今晚到这里。",
        recent_ledger_events=stored.ledger_events)[1].content.split("：", 1)[1])["pending_transition"]
    assert "结束今晚的谈话" in pending["visible_performance"]
    assert "长街" not in pending["visible_performance"]


@pytest.mark.asyncio
async def test_withdrawn_offer_can_be_explicitly_accepted_without_reinviting(tmp_path):
    """拒绝仍清除活跃提议；后来明确接受真实旧邀请可直接推进，并能冷恢复与分叉重放。"""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    runtime = NumericV2Runtime(engine, tmp_path)
    stored = await runtime.start_session(session_id="reconsider", catgirl_binding=_binding(), opening_performance=_opening())
    stored = await _commit(runtime, stored, "我们沿长街寻找旧信，好吗？", offer=True)
    stored = await _commit(runtime, stored, "别急，我再说清楚些。")
    stored = await _commit(runtime, stored, "那就先留在这里。", intent="reject")
    stored = await _commit(runtime, stored, "我听着。")
    assert not stored.session.transition_offered
    data = json.loads(_build_messages(engine, stored.session, "我改主意了，就按刚才说的走。",
                      recent_ledger_events=stored.ledger_events)[1].content.split("：", 1)[1])
    assert data["pending_transition"]["status"] == "withdrawn"
    assert "沿长街寻找旧信" in data["pending_transition"]["visible_performance"]
    assert "我听着" not in data["pending_transition"]["visible_performance"]
    # 不明确接受时仍留幕；光有历史邀请不能使旧邀请自动重新生效。
    waiting = runtime.prepare_turn(stored, TurnRequestV2("wait", 4, "再想想。"), (), transition_intent="unclear")
    assert waiting.session.current_node_id == stored.session.current_node_id
    assert not waiting.session.transition_offered
    outcome = runtime.prepare_turn(stored, TurnRequestV2("go", 4, "我改主意了，就按刚才说的走。"), (), transition_intent="accept")
    assert outcome.session.current_node_id != stored.session.current_node_id
    assert outcome.ledger_event["transition_intent"] == "accept"
    # 使用正式组装和提交链，确保无需新存档字段也能完整重放重新接受的回合。
    from tests.unit.test_theater_numeric_v2_transition_history import _candidate
    performance = engine.finalize_transition_performance(outcome, _candidate(), target_opening="旧开场。")
    committed = await runtime.commit_turn(outcome, performance)
    assert await runtime.restore_session("reconsider") == committed
    forked = await runtime.fork_session_for_test("reconsider", session_id="reconsider_fork", through_revision=5)
    assert forked.session.current_node_id == committed.session.current_node_id


@pytest.mark.asyncio
async def test_withdrawn_offer_does_not_cross_a_scene_visit(tmp_path):
    """已离开的场景不能为新场景的自由输入提供换幕授权。"""

    from dataclasses import replace
    from services.theater.numeric_v2_context import pending_transition_record

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    runtime = NumericV2Runtime(engine, tmp_path)
    stored = await runtime.start_session(session_id="old_visit", catgirl_binding=_binding(), opening_performance=_opening())
    stored = await _commit(runtime, stored, "我们沿长街走，好吗？", offer=True)
    # 构造重新访问的明确边界；它会截断旧访问，不能从旧 true 提取邀请。
    boundary = {"revision": 2, "from_node_id": "elsewhere", "to_node_id": "start", "transition_offered": False}
    revisited = replace(stored.session, transition_offered=False, performance_history=stored.session.performance_history + (boundary,))
    assert pending_transition_record(revisited, include_withdrawn=True) is None
