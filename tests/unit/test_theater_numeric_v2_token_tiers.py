"""整链路预算、完整证据与请求级实耗隔离；不以离线断言替代模型压测。"""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest
from starlette.responses import JSONResponse

from services.theater.numeric_v2_budget import numeric_v2_actor_budget
from services.theater.numeric_v2_context import history_evidence
from services.theater import numeric_v2_evaluator as ev
from services.theater.numeric_v2_usage import numeric_v2_usage_scope, invoke_with_usage, with_numeric_v2_usage
from tests.unit.test_theater_numeric_v2_review_capacity import _fixture


@pytest.mark.parametrize('profile,limits', [(name, (7000, 6000, 8000, 1500))
    for name in ('economy', 'balanced', 'quality')])
def test_every_tier_keeps_player_and_candidate_complete(profile, limits):
    # 正文超过过去360字段上限，句尾否定必须真的进入每档消息，当前输入也不能被截断。
    engine, session, _, _ = _fixture()
    session = replace(session, actor_budget_profile=profile)
    player = '我先整理工具。' * 40 + '但我没有同意离开。'
    candidate = '她仔细检查桌上的工具。' * 40 + '还没有把它交给任何人。'
    diagnostic = {}
    evaluated = ev._build_messages(engine, session, player, diagnostics=diagnostic)
    reviewed = ev._build_transition_judge_messages(engine, session, player_input=player,
        actor_performance={'performance': candidate})
    data = json.loads(reviewed[1].content.split('：', 1)[1])
    assert data['actor_performance'] == candidate
    assert data['player_input'] == player
    assert player in evaluated[1].content
    assert diagnostic['budget_tokens'] == limits[0]
    assert sum(ev.count_tokens(x.content) for x in evaluated) <= limits[0]
    assert sum(ev.count_tokens(x.content) for x in reviewed) <= limits[1]
    budget = numeric_v2_actor_budget(profile)
    assert budget['formal_judge_input_max_tokens'] == limits[2]
    assert budget['evidence_max_tokens'] == limits[3]


def test_legacy_profile_names_use_identical_fixed_budget_and_evidence():
    # 保留旧字段只为兼容；旧存档不得继续以不同容量演绎，也无需写回存档。
    _, session, _, _ = _fixture()
    history = tuple({'revision': i, 'from_node_id': 'start', 'to_node_id': 'start',
        'performance': f'第{i}号样本记录：样本已归还木盒，标签完好。' * 3} for i in range(1, 25))
    sizes = []
    for profile in ('economy', 'balanced', 'quality'):
        selected = history_evidence(replace(session, actor_budget_profile=profile, performance_history=history), '样本标签归还木盒')
        sizes.append(len(selected))
        assert all(row['text'] in [h['performance'] for h in history] for row in selected)
        assert ev.count_tokens(json.dumps(selected, ensure_ascii=False, separators=(',', ':'))) <= numeric_v2_actor_budget(profile)['evidence_max_tokens']
    assert sizes[0] == sizes[1] == sizes[2]
    assert all(numeric_v2_actor_budget(name) == numeric_v2_actor_budget('balanced')
               for name in ('economy', 'quality'))
    assert numeric_v2_actor_budget('balanced')['input_max_tokens'] == 10000


class UsageClient:
    """客户端返回真实协议形状；缺报用量和取消请求必须能被单独观察。"""
    def __init__(self, usage=None, cancel=False):
        self.usage, self.cancel = usage, cancel

    async def ainvoke(self, messages):
        await asyncio.sleep(0)
        if self.cancel:
            raise asyncio.CancelledError()
        return SimpleNamespace(content='{}', response_metadata={'token_usage': self.usage})


@pytest.mark.asyncio
async def test_request_usage_isolated_counts_retries_and_missing_reports():
    async def request(tokens):
        with numeric_v2_usage_scope() as calls:
            await invoke_with_usage(UsageClient({'prompt_tokens': tokens, 'completion_tokens': 7}), [], stage='actor')
            await invoke_with_usage(UsageClient(), [], stage='review')
            try:
                await invoke_with_usage(UsageClient(cancel=True), [], stage='dispute')
            except asyncio.CancelledError:
                pass
        return with_numeric_v2_usage({'ok': False}, calls)['token_usage']
    first, second = await asyncio.gather(request(123), request(456))
    assert [first['input_tokens'], second['input_tokens']] == [123, 456]
    assert all(not item['complete'] and len(item['calls']) == 3 for item in (first, second))
    # 请求退出后不捕获别的链路；新请求或幂等重放不能重复上一轮用量。
    with numeric_v2_usage_scope() as calls:
        pass
    assert with_numeric_v2_usage({'ok': True, 'idempotent_replay': True}, calls)['token_usage']['calls'] == []


@pytest.mark.asyncio
async def test_usage_keeps_provider_zero_cache_and_error_response():
    with numeric_v2_usage_scope() as calls:
        await invoke_with_usage(UsageClient({'input_tokens': 10, 'cache_read_input_tokens': 20,
            'cache_creation_input_tokens': 5, 'output_tokens': 0}), [], stage='evaluator')
    result = with_numeric_v2_usage(JSONResponse({'ok': False, 'reason': 'failed'}, status_code=502,
        headers={'X-Test': 'kept'}), calls)
    data = json.loads(result.body)
    assert result.status_code == 502 and result.headers['x-test'] == 'kept'
    assert int(result.headers['content-length']) == len(result.body)
    assert data['token_usage']['input_tokens'] == 35
    assert data['token_usage']['output_tokens'] == 0 and data['token_usage']['complete']
