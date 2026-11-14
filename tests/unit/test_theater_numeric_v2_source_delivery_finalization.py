"""Source-first continuation shares repairs and retains final source evidence."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_evaluator as ev
from services.theater import numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_actor_output import NumericV2ActorOutputError
from services.theater.numeric_v2_runtime import MetricChangeV2, NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_source_delivery_routing import _engine, AGREED, INPUT, KEY


TARGET = '检修间中央的黄铜仪表第一次亮起蓝色刻度。'
BAD_SOURCE = '（撕毁登记单）我把登记单撕掉了。'
OFFER = '这就开始最后一轮校准，好吗？'


def _scenario(*, partial=False):
    story = deepcopy(_engine().story)
    story['nodes'][0]['story_beat']['must_not_happen'] = ['不得撕毁登记单。']
    story['nodes'][1]['story_beat']['opening_scene'] = TARGET
    if partial:
        key = 'scene:start:reading_checked'
        story['fact_contract']['facts'][key] = {
            'value_type': 'bool', 'visibility': 'public', 'evidence_source': 'performance',
            'description': '双方已完成另一项独立的仪表读数核对。',
        }
        story['nodes'][0]['completion_contract']['all'].append({'key': key, 'equals': True})
    return NumericV2Engine.from_mapping(story)


def _evaluation():
    return ev.NumericV2EvaluationResult((MetricChangeV2('trust', 2, '玩家兑现承诺', INPUT),), False,
        fact_operations=({'op': 'set', 'key': KEY, 'value': True, 'visibility': 'public'},),
        fact_audit=({'key': KEY, 'subject': 'player', 'action': 'propose', 'object': 'equipment',
                     'result': 'agreed', 'evidence': [{'source': 'player_input', 'quote': INPUT}]},))


def _install(monkeypatch, generate, approve, contract):
    async def evaluate(self, **kwargs): return _evaluation()
    monkeypatch.setattr(options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(wf, 'aload_theater_module_options', options.aload_theater_module_options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'review_story_evidence', approve)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, 'verify_contract_boundaries', contract)
    monkeypatch.setattr(wf.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(wf.NumericV2Actor, '_character_profile', lambda self: '温和克制。')


async def _start(tmp_path, *, partial=False):
    runtime = NumericV2Runtime(_scenario(partial=partial), tmp_path)
    current = await runtime.start_session(session_id='final-source', catgirl_binding=_binding(),
        opening_performance={'performance': '去向尚未商定，请先说说你的方案。', 'suggested_inputs': []})
    return runtime, current, runtime.store._path(current.session.session_id).read_bytes()


async def _run(runtime, current, *, diagnostics=None):
    return await wf._execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('final-source-turn', 0, INPUT), ensure_current_binding=lambda _: _binding(),
        diagnostics_sink=diagnostics)


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['recover', 'still_invalid', 'source_repair_used'])
async def test_first_tail_overlap_has_one_shared_repair_and_never_rewrites_source(tmp_path, monkeypatch, mode):
    runtime, current, original = await _start(tmp_path)
    sources, tails, approvals = [], [], []
    counts = {}

    async def generate(self, **kwargs):
        assert runtime.store._path(current.session.session_id).read_bytes() == original
        if kwargs['outcome'].route is None:
            text = BAD_SOURCE if mode == 'source_repair_used' and not sources else AGREED
            sources.append(text)
            return {'performance': text, 'suggested_inputs': [], 'fact_candidates': []}
        source = kwargs['transition_source']
        assert source['performance'] == AGREED
        assert KEY not in current.session.story_state['facts']
        tails.append(deepcopy(source))
        assert len(tails) <= (1 if mode == 'source_repair_used' else 2)
        bridge = '' if mode == 'recover' and len(tails) == 2 else TARGET
        return runtime.engine.finalize_transition_performance(kwargs['outcome'], {
            'source_performance': source['performance'], 'bridge_scene_narration': bridge,
            'target_scene_narration': TARGET, 'target_performance': '接下来核对仪表。',
            'suggested_inputs': [],
        }, target_opening=TARGET, bridge_required=False)

    async def approve(self, **kwargs):
        if not kwargs['claims']: return ev.NumericV2StoryEvidenceReview()
        assert sources[-1] == AGREED and not tails
        assert kwargs['actor_evidence'] == AGREED
        approvals.append(kwargs['claims'])
        return ev.NumericV2StoryEvidenceReview((0,))

    async def contract(self, **kwargs):
        body = kwargs['actor_performance']
        return ('不得撕毁登记单。',) if body.get('performance') == BAD_SOURCE else ()

    _install(monkeypatch, generate, approve, contract)
    if mode == 'recover':
        result = await _run(runtime, current, diagnostics=counts)
        assert result.performance['segments'][0]['performance'] == AGREED
        assert result.performance['segments'][1]['scene_narration'] == ''
        assert result.performance['segments'][2]['scene_narration'] == TARGET
        assert result.stored.session.revision == 1 and result.stored.session.metrics['trust'] == 22
        assert result.stored.session.current_node_id == 'work'
        assert len([row for row in result.stored.ledger_events if row['from_node_id'] != row['to_node_id']]) == 1
    else:
        error = ('numeric_v2_contract_violation' if mode == 'source_repair_used'
                 else 'numeric_v2_transition_segment_overlap')
        with pytest.raises(NumericV2ActorOutputError, match=error):
            await _run(runtime, current, diagnostics=counts)
        assert runtime.store._path(current.session.session_id).read_bytes() == original
        assert await runtime.restore_session(current.session.session_id) == current
    assert len(approvals) == 1
    assert sources == ([BAD_SOURCE, AGREED] if mode == 'source_repair_used' else [AGREED])
    assert all(tail == tails[0] for tail in tails)
    assert counts['semantic_rewrite_attempts'] == 1
    assert counts['actor_generation_attempts'] == 3


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['retained_offer', 'postprocessed_accepted', 'postprocessed_rejected', 'unchanged'])
async def test_partial_delivery_rechecks_real_postprocessed_source_and_rolls_back_on_rejection(
    tmp_path, monkeypatch, mode,
):
    runtime, current, original = await _start(tmp_path, partial=True)
    source = AGREED if mode == 'unchanged' else AGREED + '\n' + OFFER
    approvals, offers, body_checks, edits = [], [], [], []
    postprocessed = mode.startswith('postprocessed_')

    if postprocessed:
        finalize = runtime.engine.finalize_transition_offer_state

        def finalize_with_body_edit(outcome, performance, **kwargs):
            outcome, performance = finalize(outcome, performance, **kwargs)
            if approvals and performance.get('performance') == source:
                # Exercise an actual late body edit, independently of an
                # optional proposal's routing eligibility. Metadata alone
                # must not trigger another completion approval.
                edits.append(performance['performance'])
                performance = {**performance, 'performance': AGREED}
            return outcome, performance

        monkeypatch.setattr(runtime.engine, 'finalize_transition_offer_state', finalize_with_body_edit)

    async def generate(self, **kwargs):
        assert kwargs['outcome'].route is None and not kwargs.get('transition_source')
        assert not approvals
        body = {'performance': source, 'suggested_inputs': [], 'fact_candidates': []}
        if mode != 'unchanged':
            body.update(transition_offered=True, _published_transition_offer=OFFER, _transition_offer_provided=True)
        return body

    async def approve(self, **kwargs):
        if not kwargs['claims']: return ev.NumericV2StoryEvidenceReview()
        assert runtime.store._path(current.session.session_id).read_bytes() == original
        assert KEY not in kwargs['session'].story_state['facts']
        body = kwargs['actor_evidence']
        approvals.append(body)
        assert body == (source if len(approvals) == 1 else AGREED)
        evidence = kwargs['claims'][0]['evidence']
        assert any(row['source'] == 'actor_performance' and row['quote'] == body for row in evidence)
        assert all(OFFER not in row['quote'] for row in evidence if len(approvals) > 1)
        approved = not (mode == 'postprocessed_rejected' and len(approvals) == 2)
        return ev.NumericV2StoryEvidenceReview((0,) if approved else ())

    async def contract(self, **kwargs):
        body_checks.append(kwargs['actor_performance']['performance'])
        if kwargs.get('proposed_offer'):
            offers.append(kwargs['proposed_offer']['text'])
            kwargs['diagnostics']['offer_actionable'] = False
        return ()

    _install(monkeypatch, generate, approve, contract)
    if mode == 'postprocessed_rejected':
        with pytest.raises(NumericV2ActorOutputError, match='numeric_v2_source_delivery_final_review_failed'):
            await _run(runtime, current)
        assert runtime.store._path(current.session.session_id).read_bytes() == original
        assert await runtime.restore_session(current.session.session_id) == current
    else:
        result = await _run(runtime, current)
        assert result.performance['performance'] == (AGREED if postprocessed else source)
        assert result.stored.session.current_node_id == 'start' and result.stored.session.revision == 1
        assert result.stored.session.metrics['trust'] == 22
        assert result.stored.session.story_state['facts'][KEY]['value'] is True
        assert runtime.engine.completion_contract_satisfied(result.stored.session) is False
        assert not result.performance.get('transition_offered')
        assert all(row['from_node_id'] == row['to_node_id'] for row in result.stored.ledger_events)
    assert approvals == ([source, AGREED] if postprocessed else [source])
    assert body_checks == ([source, AGREED] if postprocessed else [source])
    assert edits == ([source] if postprocessed else [])
    assert offers == ([] if mode == 'unchanged' else [OFFER])
