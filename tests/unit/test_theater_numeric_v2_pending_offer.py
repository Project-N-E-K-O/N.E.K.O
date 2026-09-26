"""Reproduce through real commits, recovery and forks that later offer-preserving dialogue cannot replace the original invitation."""

import json
from copy import deepcopy

import pytest

from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_evaluator import _build_messages
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening


async def _commit(runtime, stored, text, *, offer=False, intent="unclear", narration="", invalidate=False):
    """Use production Runtime latching and persistence so handwritten history cannot omit the state merge behind the failure."""

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
    """A confirmed invalid invitation establishes a history boundary; a corrected invitation in the same turn can still be accepted or reconsidered."""
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
async def test_valid_represented_invitation_refreshes_origin_after_restore_and_fork(tmp_path):
    """本轮重新公开有效邀请要刷新回复对象，单纯保留旧状态则不能刷新。"""  # noqa: DOCSTRING_CJK

    from services.theater.numeric_v2_context import pending_transition_record

    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(numeric_v2_story()), tmp_path)
    current = await runtime.start_session(
        session_id="refreshed_offer",
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    current = await _commit(runtime, current, "我们去长街，好吗？", offer=True)
    current = await _commit(runtime, current, "你可以先想想。", offer=False)
    assert pending_transition_record(current.session, ledger_events=current.ledger_events)["revision"] == 1
    current = await _commit(runtime, current, "还是去长街吧，我来带路。", offer=True)

    restored = await runtime.restore_session(current.session.session_id)
    forked = await runtime.fork_session_for_test(
        current.session.session_id,
        session_id="refreshed_offer_fork",
        through_revision=current.session.revision,
    )

    for stored in (current, restored, forked):
        pending = pending_transition_record(
            stored.session,
            ledger_events=stored.ledger_events,
        )
        assert pending is not None
        assert pending["revision"] == 3
        assert pending["transition_offer_presented"] is True


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
        actor_performance={"performance": "那就走吧。"}, cancelled_transition=True, invalidated_invitation=True)[0]
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
    data = json.loads(path.read_text(encoding="utf-8"))
    data['ledger_events'][-1].pop('transition_offer_invalidated')
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(NumericV2StoreError, match='numeric_ledger_replay_mismatch'):
        await runtime.restore_session('tampered_reset')


@pytest.mark.asyncio
@pytest.mark.parametrize("narrated", [False, True])
async def test_pending_offer_survives_followups_restore_and_fork(tmp_path, narrated):
    """Retain original sources for both narrated and spoken invitations instead of guessing from the latest body."""

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
    """When rejecting an old offer and proposing a new one in one turn, use the Ledger attitude boundary rather than the first continuous true flag."""

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
    """Rejection clears the active offer; explicit later acceptance of the original invitation can advance and replay through cold recovery and forks."""

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
    """A departed scene cannot authorize a transition for free input in the new scene."""

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
    """Review prose even with an existing invitation; safe questions preserve it and unsafe drafts share the existing correction and commit limits."""
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
        # 本轮不安全推荐被删除后，仍保留此前已公开的原始接受按钮。
        assert result.performance['suggested_inputs'] == ['好，就按这个安排。']
    assert await runtime.restore_session(current.session.session_id) == result.stored
    # 冷恢复后仍可按原邀请接受，不需先由演员再邀请一次。
    accepted = runtime.prepare_turn(result.stored, TurnRequestV2('accept', result.stored.session.revision, '好，现在出发。'), (),
        transition_intent='accept')
    assert accepted.session.current_node_id != current.session.current_node_id


@pytest.mark.asyncio
async def test_invalid_fallback_offer_without_prior_invitation_is_not_latched(tmp_path, monkeypatch):
    """末稿兜底可提交正文，但复核无效的新去向不能创建可接受的会话邀请。"""  # noqa: DOCSTRING_CJK

    from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as workflow

    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(numeric_v2_story()), tmp_path)
    current = await runtime.start_session(
        session_id='invalid_fallback_offer', catgirl_binding=_binding(), opening_performance=_opening())
    generations = []

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False, transition_intent='unclear')

    async def generate(self, **kwargs):
        generations.append(kwargs)
        return {
            'performance': '我们去错误地点吧？',
            'suggested_inputs': [],
            'transition_offered': True,
        }

    async def review(self, **kwargs):
        return ev.NumericV2TransitionOfferReview(
            offer_present=True,
            valid=False,
            body_violations=(),
            unsafe_suggestion_indexes=(),
            failure_reason='邀请去向与 next_scene_direction 不符。',
        )

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')

    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(),
        runtime=runtime,
        current=current,
        turn=TurnRequestV2('invalid_offer', current.session.revision, '接下来怎么办？'),
        ensure_current_binding=lambda _: _binding(),
    )

    assert len(generations) == 2
    assert result.diagnostics['semantic_review_fallback'] is True
    assert result.stored.session.transition_offered is False
    assert result.performance['transition_offered'] is False
    assert result.stored.ledger_events[-1]['transition_offered'] is False


@pytest.mark.asyncio
async def test_current_scene_completion_action_is_not_rewritten_as_wrong_exit(tmp_path, monkeypatch):
    """玩家已授权且能写入完成事实的幕内移动，不应因模型误报出口而进入改写和争议复查。"""  # noqa: DOCSTRING_CJK

    from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as workflow

    story = numeric_v2_story()
    story['fact_contract'] = {
        'facts': {
            'scene:start:civilians_sheltered': {
                'value_type': 'bool',
                'visibility': 'public',
                'description': '三名平民已经全部进入深层屏蔽走廊并安全安置。',
            },
        },
    }
    story['nodes'][0]['completion_contract'] = {
        'all': [{'key': 'scene:start:civilians_sheltered', 'equals': True}],
    }
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(
        session_id='current_scene_completion_action',
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    generations = []
    reviews = []
    player_input = '大家互相搀扶，跟着我进入深层走廊。'
    narration = '三名平民已经进入深层屏蔽走廊并安全安置。'

    # 即使复核同时返回了当前幕完成事实，无关的错误目的地也不能借此逃过改写。
    wrong_destination_review = ev.NumericV2TransitionOfferReview(
        offer_present=True,
        valid=False,
        body_violations=(),
        unsafe_suggestion_indexes=(),
        failure_reason='正文提议前往便利店，与节点出口不一致。',
        offer_quote='我们去便利店吧。',
        fact_candidates=({
            'key': 'scene:start:civilians_sheltered',
            'value': True,
            'evidence_quote': narration,
        },),
    )
    assert workflow._current_scene_completion_offer_evidence(
        engine=runtime.engine,
        session=current.session,
        player_input=player_input,
        review=wrong_destination_review,
    ) == ()

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=True,
            transition_intent='unclear',
            interaction_intent='scene_action',
        )

    async def generate(self, **kwargs):
        generations.append(kwargs)
        return {
            'performance': '快，扶着他们进入走廊深处。',
            'scene_narration': narration,
            'suggested_inputs': ['（确认三人站稳）都跟紧了。'],
            'transition_offered': True,
        }

    async def review(self, **kwargs):
        reviews.append(kwargs)
        assert not kwargs.get('dispute_review')
        return ev.NumericV2TransitionOfferReview(
            offer_present=True,
            valid=False,
            body_violations=(),
            unsafe_suggestion_indexes=(),
            failure_reason='正文邀请进入深层走廊，与节点出口不一致。',
            offer_quote='快，扶着他们进入走廊深处。',
            fact_candidates=({
                'key': 'scene:start:civilians_sheltered',
                'value': True,
                'evidence_quote': narration,
            },),
        )

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')

    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(),
        runtime=runtime,
        current=current,
        turn=TurnRequestV2('shelter_civilians', 0, player_input),
        ensure_current_binding=lambda _: _binding(),
    )

    assert len(generations) == 1
    assert len(reviews) == 1
    assert result.diagnostics['current_scene_offer_flags_cleared'] == 1
    assert result.diagnostics['semantic_rewrite_attempts'] == 0
    assert result.diagnostics['transition_offer_retries'] == 0
    assert result.diagnostics['dispute_review_attempts'] == 0
    assert result.diagnostics['fact_candidates_accepted'] == 1
    assert result.stored.session.transition_offered is False
    assert runtime.engine.completion_contract_satisfied(result.stored.session) is True


@pytest.mark.asyncio
async def test_fact_candidates_keep_valid_siblings_when_one_candidate_is_stale_or_invalid(tmp_path, monkeypatch):
    """旧候选与坏证据各自淘汰，本轮可核验事实仍与回合一起原子提交。"""  # noqa: DOCSTRING_CJK

    from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as workflow

    story = numeric_v2_story()
    story['fact_contract'] = {'facts': {
        'scene:start:old': {
            'value_type': 'bool', 'visibility': 'public', 'description': '旧事实已经成立。'},
        'scene:start:invalid': {
            'value_type': 'bool', 'visibility': 'public', 'description': '这条候选缺少本轮证据。'},
        'scene:start:new': {
            'value_type': 'bool', 'visibility': 'public', 'description': '本轮新事实已经成立。'},
    }}
    story['nodes'][0]['completion_contract'] = {'all': [
        {'key': 'scene:start:old', 'equals': True},
        {'key': 'scene:start:invalid', 'equals': True},
        {'key': 'scene:start:new', 'equals': True},
    ]}
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(
        session_id='fact_candidate_partial_acceptance',
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    setup = runtime.prepare_turn(
        current,
        TurnRequestV2('establish_old_fact', 0, '旧事实已经确认。'),
        (),
        fact_operations=({
            'op': 'set', 'key': 'scene:start:old', 'value': True, 'visibility': 'public'},),
    )
    current = await runtime.commit_turn(
        setup,
        {'performance': '旧事实已经确认。', 'suggested_inputs': [], 'transition_offered': False},
    )

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult(
            (), False, transition_intent='unclear', interaction_intent='scene_action')

    async def generate(self, **kwargs):
        return {
            'performance': '本轮可核验的新事实已经公开。',
            'suggested_inputs': ['（点头）我记住了。'],
            'transition_offered': False,
            'fact_candidates': [
                {'key': 'scene:start:old', 'value': True, 'evidence_quote': '旧回合的原文不在本轮正文。'},
                {'key': 'scene:start:invalid', 'value': True, 'evidence_quote': '本轮正文中不存在的引文。'},
                {'key': 'scene:start:new', 'value': True, 'evidence_quote': '本轮可核验的新事实已经公开。'},
            ],
        }

    async def review(self, **kwargs):
        return ev.NumericV2TransitionOfferReview(False, False, (), ())

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')

    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(),
        runtime=runtime,
        current=current,
        turn=TurnRequestV2('record_new_fact', current.session.revision, '请说明本轮新事实。'),
        ensure_current_binding=lambda _: _binding(),
    )

    facts = result.stored.session.story_state['facts']
    assert facts['scene:start:old']['value'] is True
    assert facts['scene:start:new']['value'] is True
    assert 'scene:start:invalid' not in facts
    assert result.diagnostics['fact_candidates_accepted'] == 1
    assert result.diagnostics['fact_candidates_rejected'] == 1
    assert [operation['key'] for operation in result.stored.ledger_events[-1]['fact_operations']] == [
        'scene:start:new'
    ]


@pytest.mark.asyncio
async def test_narration_location_is_not_rewritten_when_review_denies_its_own_offer(tmp_path, monkeypatch):
    """旁白只公开出口标识时，复核不能一边否认邀请、一边用邀请标志触发改写。"""  # noqa: DOCSTRING_CJK

    from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as workflow

    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(numeric_v2_story()), tmp_path)
    current = await runtime.start_session(
        session_id='narration_offer_contradiction',
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    quote = '走廊尽头的红灯标识显示“地下信标室·检修入口”。'
    candidate = {
        'performance': '先把伤员安顿好，人家会继续看着门口。',
        'scene_narration': quote,
        'suggested_inputs': ['我先检查伤口。', '那个入口是做什么的？'],
        'transition_offered': False,
    }
    review_result = ev.NumericV2TransitionOfferReview(
        offer_present=True,
        valid=False,
        body_violations=(),
        unsafe_suggestion_indexes=(),
        failure_reason='正文仅公开了信标室入口位置，未发出前往下一地点的明确邀请。',
        offer_quote=quote,
    )
    # 同一句若由角色对白说出，不能依靠理由文案直接清除，仍须交给原复核链处理。
    assert workflow._review_denies_narration_only_offer(
        {'performance': quote, 'suggested_inputs': []},
        review_result,
    ) is False
    generations = []
    reviews = []

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult(
            metric_changes=(),
            scene_complete=False,
            transition_intent='unclear',
            interaction_intent='scene_action',
        )

    async def generate(self, **kwargs):
        generations.append(kwargs)
        return dict(candidate)

    async def review(self, **kwargs):
        reviews.append(kwargs)
        return review_result

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')

    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(),
        runtime=runtime,
        current=current,
        turn=TurnRequestV2('narration_location', 0, '我先检查伤员。'),
        ensure_current_binding=lambda _: _binding(),
    )

    assert len(generations) == 1
    assert len(reviews) == 1
    assert result.diagnostics['narration_offer_flags_cleared'] == 1
    assert result.diagnostics['semantic_rewrite_attempts'] == 0
    assert result.diagnostics['transition_offer_retries'] == 0
    assert result.stored.session.transition_offered is False
    assert result.performance['scene_narration'] == quote


@pytest.mark.asyncio
async def test_completed_scene_uses_author_fallback_when_safe_actor_reply_has_no_offer(tmp_path, monkeypatch):
    """完成合同已满足且复核确认正文安全但没有邀请时，零调用追加作者原文。"""  # noqa: DOCSTRING_CJK

    from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as workflow

    story = numeric_v2_story()
    story['fact_contract'] = {
        'facts': {
            'scene:start:done': {
                'value_type': 'bool',
                'visibility': 'public',
                'description': '当前幕的核心结果已经成立。',
            },
        },
    }
    story['nodes'][0]['completion_contract'] = {
        'all': [{'key': 'scene:start:done', 'equals': True}],
    }
    fallback_offer = '大家已经安全了。要现在和我一起去长街继续调查吗？'
    story['nodes'][0]['route_gates'][1]['transition_contract']['fallback_offer'] = fallback_offer
    middle = story['nodes'][2]
    middle['type'] = 'scene'
    middle.pop('terminal')
    middle.pop('ending_id')
    middle['min_turns'] = 1
    middle['route_gates'] = [{
        'id': 'middle_to_leave',
        'target_node_id': 'ending_after_middle',
        'priority': 100,
        'conditions': {'all': []},
        'transition_contract': deepcopy(
            story['nodes'][0]['route_gates'][1]['transition_contract']
        ),
    }]
    middle['route_gates'][0]['transition_contract'].pop('fallback_offer')
    story['nodes'].append({
        'id': 'ending_after_middle',
        'type': 'ending',
        'chapter': '离开',
        'story_beat': deepcopy(middle['story_beat']),
        'route_gates': [],
        'terminal': True,
        'ending_id': 'leave',
    })
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(
        session_id='completion_fallback_offer',
        catgirl_binding=_binding(),
        opening_performance=_opening(),
    )
    setup = runtime.prepare_turn(
        current,
        TurnRequestV2('complete_scene', 0, '眼前的问题已经解决。'),
        (),
        fact_operations=({
            'op': 'set',
            'key': 'scene:start:done',
            'value': True,
            'visibility': 'public',
        },),
    )
    current = await runtime.commit_turn(
        setup,
        {'performance': '眼前的问题已经解决。', 'suggested_inputs': [], 'transition_offered': False},
    )
    generations = []
    reviews = []

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult(
            (), False, transition_intent='unclear', interaction_intent='scene_action')

    async def generate(self, **kwargs):
        generations.append(kwargs)
        # 模拟真实样本：Actor 自报已经邀请，但正文只完成当前幕收束。
        return {
            'performance': '（收回工具）这边总算处理完了。',
            'suggested_inputs': ['（擦去汗水）接下来呢？'],
            'transition_offered': True,
        }

    async def review(self, **kwargs):
        reviews.append(kwargs)
        return ev.NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            body_violations=(),
            unsafe_suggestion_indexes=(),
        )

    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')

    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(),
        runtime=runtime,
        current=current,
        turn=TurnRequestV2('ask_next', current.session.revision, '接下来怎么办？'),
        ensure_current_binding=lambda _: _binding(),
    )

    assert len(generations) == 1
    assert len(reviews) == 1
    assert result.diagnostics['completion_fallback_offer_applied'] == 1
    assert result.diagnostics['phantom_transition_flags_cleared'] == 1
    assert result.performance['performance'].endswith(fallback_offer)
    assert result.performance['transition_offered'] is True
    assert result.stored.session.transition_offered is True
    accepted = runtime.prepare_turn(
        result.stored,
        TurnRequestV2('accept_fallback', result.stored.session.revision, '好，现在过去。'),
        (),
        transition_intent='accept',
    )
    assert accepted.session.current_node_id == 'ending_leave'
