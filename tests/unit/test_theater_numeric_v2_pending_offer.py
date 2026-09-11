"""通过实际提交、恢复和分叉复现：保留提议的后续对白不能取代最初的邀请。"""

import json

import pytest

from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_evaluator import _build_messages
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


async def _commit(runtime, stored, text, *, offer=False, intent="unclear", narration="", invalidate=False):
    """使用正式 Runtime 的锁存和写盘，避免手写历史漏掉此次根因中的状态合并。"""

    outcome = runtime.prepare_turn(stored,
        TurnRequestV2(f"turn_{stored.session.revision + 1}", stored.session.revision, "我先问个细节。"),
        (), transition_intent=intent)
    performance = {"performance": text, "suggested_inputs": ["好，就按这个安排。"]}
    if narration:
        performance["scene_narration"] = narration
    outcome, performance = runtime.engine.finalize_transition_offer_state(
        outcome, performance, new_offer=offer, invalidate_previous_offer=invalidate)
    return await runtime.commit_turn(outcome, performance)


@pytest.mark.asyncio
@pytest.mark.parametrize("new_offer", [False, True])
async def test_invalid_invitation_is_not_revived_by_followup_restore_or_fork(tmp_path, new_offer):
    """已确认的错误邀请形成历史边界；同轮更正的新邀请仍可正常接受或重新考虑。"""
    from services.theater.numeric_v2_context import pending_transition_record

    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(numeric_v2_story()), tmp_path)
    current = await runtime.start_session(session_id="invalid_offer", catgirl_binding=_binding(), opening_performance=_opening())
    current = await _commit(runtime, current, "我们去便利店吧？", offer=True)
    current = await _commit(runtime, current, "刚才说错了。我们去长街找旧信，好吗？" if new_offer else "刚才我说错了，先留在这里。",
                            offer=new_offer, invalidate=True)
    assert current.session.transition_offered is new_offer
    assert current.ledger_events[-1]["transition_offer_invalidated"] is True
    assert current.session.performance_history[-1]["transition_offer_invalidated"] is True
    current = await _commit(runtime, current, "你可以慢慢考虑。")
    if new_offer:
        current = await _commit(runtime, current, "好，先不去。", intent="reject")
    restored = await runtime.restore_session(current.session.session_id)
    forked = await runtime.fork_session_for_test(current.session.session_id, session_id="invalid_offer_fork", through_revision=current.session.revision)
    for stored in (current, restored, forked):
        for events in ((), stored.ledger_events):
            pending = pending_transition_record(stored.session, ledger_events=events, include_withdrawn=True)
            assert (pending is not None) is new_offer
            if pending is not None:
                assert pending["revision"] == 2
                assert "长街" in pending["performance"]
        outcome = runtime.prepare_turn(stored, TurnRequestV2("accept_after_reset", stored.session.revision, "好，就按刚才说的去。"), (), transition_intent="accept")
        assert (outcome.route is not None) is new_offer


@pytest.mark.asyncio
async def test_invitation_invalidation_cannot_be_injected_by_actor(tmp_path):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(numeric_v2_story()), tmp_path)
    current = await runtime.start_session(session_id="actor_reset", catgirl_binding=_binding(), opening_performance=_opening())
    current = await _commit(runtime, current, "我们去长街吧？", offer=True)
    outcome = runtime.prepare_turn(current, TurnRequestV2("followup", 1, "远吗？"), ())
    outcome, performance = runtime.engine.finalize_transition_offer_state(outcome,
        {"performance": "不远。", "transition_offer_invalidated": True}, new_offer=False)
    assert outcome.session.transition_offered
    assert "transition_offer_invalidated" not in performance
    outcome, _ = runtime.engine.finalize_transition_offer_state(outcome, {}, new_offer=False, invalidate_previous_offer=True)
    outcome, performance = runtime.engine.finalize_transition_offer_state(outcome, {"performance": "更正后的邀请。"}, new_offer=True)
    assert outcome.session.transition_offered
    assert outcome.ledger_event['transition_offer_invalidated'] is True
    assert performance['transition_offer_invalidated'] is True


@pytest.mark.asyncio
async def test_invalidated_invitation_is_shared_before_rewrite_and_review(tmp_path):
    from services.theater.numeric_v2_evaluator import _build_transition_judge_messages

    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(numeric_v2_story()), tmp_path)
    current = await runtime.start_session(session_id="shared_reset", catgirl_binding=_binding(), opening_performance=_opening())
    current = await _commit(runtime, current, "我们去便利店买热饮吧？", offer=True)
    outcome = runtime.prepare_turn(current, TurnRequestV2("go", 1, "好，走吧。"), ())
    outcome, _ = runtime.engine.finalize_transition_offer_state(outcome, {}, new_offer=False, invalidate_previous_offer=True)
    messages = _turn_messages(runtime.engine, current.session, outcome, "好，走吧。", "温和。", "测试猫娘", "哥哥",
                             retry_hint="请更正邀请。", interaction_intent="scene_action", recent_ledger_events=current.ledger_events)
    data = json.loads(messages[1].content.split('：', 1)[1])
    assert "已确认去向错误并撤下的旧邀请原文：我们去便利店买热饮吧？" in data['pacing']
    assert "当前待确认提议原文" not in data['pacing']
    messages = _build_transition_judge_messages(runtime.engine, outcome.session, player_input="好，走吧。",
        actor_performance={"performance": "那就走吧。"}, cancelled_transition=True, invalidated_invitation=True)
    data = json.loads(messages[1].content.split('：', 1)[1])
    assert "便利店" in json.dumps(data['invalidated_invitation'], ensure_ascii=False)
    assert "不再判断它是否有效" in messages[0].content


@pytest.mark.asyncio
async def test_restore_rejects_invalidation_boundary_missing_from_ledger(tmp_path):
    from services.theater.numeric_v2_store import NumericV2StoreError

    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(numeric_v2_story()), tmp_path)
    current = await runtime.start_session(session_id="tampered_reset", catgirl_binding=_binding(), opening_performance=_opening())
    current = await _commit(runtime, current, "去便利店吧？", offer=True)
    current = await _commit(runtime, current, "我说错了。", invalidate=True)
    path = tmp_path/'numeric_v2/sessions/tampered_reset.json'
    data = json.loads(path.read_text())
    data['ledger_events'][-1].pop('transition_offer_invalidated')
    path.write_text(json.dumps(data))
    with pytest.raises(NumericV2StoreError, match='numeric_ledger_replay_mismatch'):
        await runtime.restore_session('tampered_reset')


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
            # 锁存只证明曾经公开，不能将错误原话变成演员必须兑现的正确安排。
            assert "只证明此前说过，不证明安排正确" in actor_data["pacing"]
            assert "不为更正补造人物行程或新的阻碍" in actor_data["pacing"]


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


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['safe', 'buttons', 'repair', 'fallback', 'dispute_timeout', 'fast_failure', 'actor_failure'])
async def test_pending_reply_is_reviewed_without_losing_invitation_or_recounting(tmp_path, monkeypatch, mode):
    """已有邀请不免除正文复核；安全追问保留旧邀请，错误稿仍共用原纠错/提交边界。"""
    from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as workflow
    from services.theater.numeric_v2_actor import NumericV2ActorOutputError
    from services.theater.numeric_v2_runtime import MetricChangeV2

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='pending_review', catgirl_binding=_binding(), opening_performance=_opening())
    current = await _commit(runtime, current, '我们沿长街寻找旧信，好吗？', offer=True)
    invitations = current.session.performance_history
    evaluations, generations, reviews = [], [], []
    needs_repair = mode in ('repair', 'fallback', 'dispute_timeout', 'actor_failure')

    async def evaluate(self, **kwargs):
        evaluations.append(kwargs)
        return ev.NumericV2EvaluationResult((MetricChangeV2('trust', 2, '玩家兑现承诺', '我先问个细节。'),), False,
            transition_intent='unclear')

    async def generate(self, **kwargs):
        generations.append(kwargs)
        assert kwargs['outcome'].session.current_node_id == current.session.current_node_id
        if len(generations) >= 2 and mode == 'actor_failure':
            raise NumericV2ActorOutputError('test_actor_failure')
        return {'performance': '（点头）先听你说。' if len(generations) == 2 else '（抬眼）你想问什么？',
            'suggested_inputs': ['（点头）先说说路上情况。'], 'transition_offered': False}

    async def review(self, **kwargs):
        reviews.append(kwargs)
        assert not kwargs['route_changed']
        assert not kwargs.get('check_missed_initiation'), '旧邀请存在时不能把追问恢复成主动换幕'
        assert kwargs['session'].performance_history == invitations
        if mode == 'fast_failure' or (mode == 'dispute_timeout' and kwargs.get('dispute_review')):
            raise ev.NumericV2EvaluatorError('test_review_failure')
        bad = needs_repair and (len(generations) == 1 or mode == 'fallback')
        return ev.NumericV2TransitionOfferReview(offer_present=bad, valid=False,
            body_violations=(), unsafe_suggestion_indexes=(0,) if mode == 'buttons' else (),
            failure_reason='当前正文仍在邀请错误去向。' if bad else '')

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    kwargs = dict(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('followup', current.session.revision, '我先问个细节。'), ensure_current_binding=lambda _: _binding())
    if mode == 'actor_failure':
        with pytest.raises(NumericV2ActorOutputError):
            await workflow.execute_numeric_v2_turn(**kwargs)
        assert await runtime.restore_session(current.session.session_id) == current
        return
    result = await workflow.execute_numeric_v2_turn(**kwargs)
    assert len(reviews) == (3 if needs_repair else 1)
    assert len(evaluations) == 1 and len(generations) == (2 if needs_repair else 1)
    if needs_repair:
        # 改稿仍用原输入和已提交历史；被拒的第一稿不能再成为演员续写底稿。
        repair = generations[1]
        assert repair['player_input'] == generations[0]['player_input'] == '我先问个细节。'
        assert repair['session'].performance_history == invitations
        assert '（抬眼）你想问什么？' not in repair['retry_hint']
        assert '当前正文仍在邀请错误去向。' in repair['retry_hint']
    assert result.stored.session.revision == current.session.revision + 1
    assert len(result.stored.ledger_events) == len(current.ledger_events) + 1
    assert result.stored.session.metrics['trust'] == current.session.metrics['trust'] + 2
    assert result.stored.session.transition_offered
    assert result.stored.session.current_node_id == current.session.current_node_id
    assert result.diagnostics['semantic_review_fallback'] is (mode == 'fallback')
    assert result.diagnostics['transition_judge_degraded'] is (mode == 'fast_failure')
    assert result.diagnostics['dispute_review_degraded'] is (mode == 'dispute_timeout')
    if mode == 'buttons':
        assert result.performance['suggested_inputs'] == []
    assert await runtime.restore_session(current.session.session_id) == result.stored
    # 冷恢复后仍可按原邀请接受，不需先由演员再邀请一次。
    accepted = runtime.prepare_turn(result.stored, TurnRequestV2('accept', result.stored.session.revision, '好，现在出发。'), (),
        transition_intent='accept')
    assert accepted.session.current_node_id != current.session.current_node_id
