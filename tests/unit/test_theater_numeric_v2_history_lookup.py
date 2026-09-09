"""按需历史查找的来源、容量、失败继续和本回合共享边界。"""

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_history as history
from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_context import history_evidence, performance_history_records
from services.theater.numeric_v2_actor import _turn_messages
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2, MetricChangeV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


def _session():
    engine = _engine()
    session = engine.create_session(session_id='lookup', catgirl_binding=_binding(), opening_performance=_opening())
    return replace(session, revision=2, node_turn_count=2, performance_history=(
        {'revision': 1, 'from_node_id': 'start', 'to_node_id': 'start',
         'input_text': '我同意展示日记。', 'performance': '日记暂时交给管理员。',
         'suggested_inputs': ['我把从未取得的怀表放进保险箱。']},
        {'revision': 2, 'from_node_id': 'start', 'to_node_id': 'start',
         'input_text': '许可撤回，日记保密。', 'performance': '收到，按保密处理。'},
    ))


def test_record_projection_omits_unselected_text_and_preserves_revocation():
    rows = performance_history_records(_session())
    assert '怀表' not in str(rows)
    assert any(row['text'] == '许可撤回，日记保密。' and row['source'] == 'player_input' for row in rows)


def test_lookup_pages_keep_complete_original_fields_and_stable_ids():
    # 人为小容量验证分页；长字段不裁掉尾部否定，也不谎称已扫描完整历史。
    rows = [{'revision': i, 'text': '记录。' * 35 + '没有同意。'} for i in range(5)]
    pages, omitted = history._pages(rows, '是否同意？', 400)
    assert len(pages) > 1 and not omitted
    assert [{k: v for k, v in row.items() if k != 'id'} for page in pages for row in page] == rows
    assert [row['id'] for page in pages for row in page] == list(range(5))
    assert all(sum(history.count_tokens(m.content) for m in history._messages('是否同意？', page)) <= 400 for page in pages)
    _, omitted = history._pages([{'text': '很长的记录。' * 200}], '问', 400)
    assert omitted


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['found', 'unknown', 'invented_id', 'timeout'])
async def test_lookup_selects_real_records_and_reports_failures(monkeypatch, mode):
    calls = []

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def ainvoke(self, messages):
            rows = json.loads(messages[1].content)['records']
            calls.append(rows)
            if mode == 'timeout': await asyncio.sleep(1)
            ids = [row['id'] for row in rows if '许可撤回' in row['text']] if mode == 'found' else []
            if mode == 'invented_id': ids = [100000]
            return SimpleNamespace(content=json.dumps({'evidence_ids': ids}))

    async def config(_): return {'model': 'test', 'base_url': 'http://invalid.test'}
    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(history, '_model_config', config)
    monkeypatch.setattr(history, 'create_chat_llm_async', factory)
    if mode == 'timeout': monkeypatch.setattr(history, '_LOOKUP_TIMEOUT_SECONDS', .01)
    result = await history.lookup_history(object(), _session(), '那项许可还有效吗？')
    assert len(calls) == 1 and result['calls'] == 1
    assert result['status'] == ('found' if mode == 'found' else 'not_found' if mode == 'unknown' else 'partial')
    assert all(row in performance_history_records(_session()) for row in result['evidence'])
    if mode == 'found': assert result['evidence'][0]['text'] == '许可撤回，日记保密。'
    else: assert not result['evidence']


def test_lookup_evidence_rejects_foreign_text_and_prioritizes_original():
    session = _session()
    fact = next(row for row in performance_history_records(session) if '许可撤回' in row['text'])
    lookup = {'evidence': [fact, {**fact, 'text': '允许公开。'}]}
    rows = history_evidence(session, '那件事呢？', lookup=lookup)
    assert fact in rows and all(row['text'] != '允许公开。' for row in rows)


def test_actor_and_guard_pack_the_same_retrieved_original_with_fixed_budgets():
    """实际打包后的两个消费者都包含撤回原文；六块 Actor 和容量约束保持生效。"""
    session = _session(); engine = _engine()
    fact = next(row for row in performance_history_records(session) if '许可撤回' in row['text'])
    lookup = {'status': 'found', 'evidence': [fact]}
    outcome = engine.resolve_turn(session, TurnRequestV2('pack', 2, '那件事呢？'), (), scene_complete=False)
    actor = _turn_messages(engine, session, outcome, '那件事呢？', '温和', '测试猫娘', '哥哥', history_lookup=lookup)
    guard = evaluator._build_transition_judge_messages(engine, session, player_input='那件事呢？',
        actor_performance={'performance': '日记继续保密。'}, history_lookup=lookup)
    for messages, limit in [(actor, 10000), (guard, 6000)]:
        assert fact['text'] in messages[1].content
        assert '按需查找 Session 演绎原文' in messages[0].content
        assert sum(history.count_tokens(m.content) for m in messages) <= limit
    assert len(json.loads(actor[1].content.split('：\n', 1)[1])) == 6


@pytest.mark.parametrize('query', ['', '日记最后是否允许公开？'])
def test_evaluator_lookup_request_is_optional_and_not_a_runtime_decision(query):
    engine = _engine()
    raw = {'scene_complete': False, 'metric_changes': {}}
    if query: raw['history_query'] = query
    result = evaluator._parse_output(json.dumps(raw), engine, '问', _session())
    assert result.history_query == query and not result.metric_changes and result.transition_intent == 'unclear'
    raw['history_query'] = True
    with pytest.raises(evaluator.NumericV2EvaluatorOutputError):
        evaluator._parse_output(json.dumps(raw), engine, '问', _session())


@pytest.mark.asyncio
@pytest.mark.parametrize('formal', [False, True])
@pytest.mark.parametrize('requested,status', [(False, 'found'), (True, 'found'), (True, 'partial')])
async def test_workflow_shares_one_lookup_across_rewrite_and_dispute(monkeypatch, tmp_path, requested, status, formal):
    """普通回合零查找；失败仍提交末稿，原文结果既不重复调用也不变成新存档字段。"""
    engine = _engine(); runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='lookup_flow', catgirl_binding=_binding(), opening_performance=_opening())
    lookups, generations, reviews = [], [], []
    result = {'status': status, 'evidence': [], 'calls': 1}

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((MetricChangeV2('trust', 2, '玩家兑现承诺', '原话'),), formal, natural_ending_ready=formal,
            history_query='先前如何决定？' if requested else '')
    async def lookup(*args): lookups.append(args); return result
    async def generate(self, **kwargs):
        generations.append(kwargs)
        if formal:
            return engine.finalize_transition_performance(kwargs['outcome'],
                {**_candidate(), 'source_performance': f'第{len(generations)}版回应。'}, target_opening='旧开场。')
        return {'performance': f'第{len(generations)}版回应。', 'suggested_inputs': [], 'transition_offered': False}
    async def review(self, **kwargs):
        reviews.append(kwargs)
        return evaluator.NumericV2TransitionOfferReview(offer_present=False, valid=False,
            body_violations=('author_boundary',), unsafe_suggestion_indexes=(), failure_reason='持续否定')
    monkeypatch.setattr(workflow, 'lookup_history', lookup)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda _: '温和')
    completed = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('lookup_once', 0, '那件事呢？'), ensure_current_binding=lambda _: _binding())
    assert len(lookups) == int(requested) and len(generations) == 2 and len(reviews) == 3
    assert all(row.get('history_lookup') is (result if requested else None) for row in [*generations, *reviews])
    assert completed.stored.session.metrics['trust'] == current.session.metrics['trust'] + 2
    assert completed.stored.session.revision == 1
    assert 'history_lookup' not in json.dumps(completed.stored.session.to_dict())
    assert await NumericV2Runtime(engine, tmp_path).restore_session('lookup_flow') == completed.stored
