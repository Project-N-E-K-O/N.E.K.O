"""A history index must not duplicate the complete latest exchange under pressure."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.theater.numeric_v2_actor import _fit_simple_turn_prompt_data
from services.theater.numeric_v2_context import render_prompt_context
from services.theater.numeric_v2_context import current_scene_history_index
from utils.tokenize import count_tokens


def _pack(evidence, *, limit=100000):
    return _fit_simple_turn_prompt_data(
        system_prompt='承接实际记录，不补造结果。', human_prefix='资料：\n',
        data=dict(role='当前角色', current_scene='当前场景', pacing='先回应', next_scene='尚未接受',
                  story_so_far='', player_input='接下来呢？'),
        history_rows=[{'revision': 12, 'player_input': '我在这里等。',
                       'performance': '已经检查完毕，结果仍然不确定。' * 30}],
        history_preselected=True,
        fact_index=lambda: {'evidence': evidence, 'omitted_units': 3, 'latest_omitted_revision': 8},
        max_tokens=limit,
    )


def test_complete_recent_source_is_kept_once_and_restores_a_fitting_prompt():
    latest = dict(revision=12, source='performance', current_visit=True, field='performance',
                  text='已经检查完毕，结果仍然不确定。' * 30)
    older = dict(revision=9, source='performance', current_visit=True, field='performance',
                 text='此前约定的物品仍由玩家保管，没有交给角色。')
    reference = _pack([older])
    limit = count_tokens('承接实际记录，不补造结果。') + count_tokens('资料：\n' + render_prompt_context(reference))
    packed = _pack([latest, older], limit=limit)
    assert packed == reference
    assert packed['story_so_far'].count(latest['text']) == 1
    assert older['text'] in packed['story_so_far']


@pytest.mark.parametrize('change', [
    {'revision': 11}, {'source': 'player_input', 'field': 'input_text'},
    {'current_visit': False}, {'text': '已经检查完毕，结果仍然不确定。'},
])
def test_index_keeps_other_revisions_sources_visits_and_partial_quotes(change):
    original = dict(revision=12, source='performance', current_visit=True, field='performance',
                    text='已经检查完毕，结果仍然不确定。' * 30)
    source = {**original, **change}
    before = deepcopy(source)
    packed = _pack([source])
    assert '"evidence":[{' in packed['story_so_far']
    assert source['text'] in packed['story_so_far']
    assert source == before


@pytest.mark.parametrize('with_retrieval', [False, True])
def test_index_reselects_whole_sources_against_final_request_budget(with_retrieval):
    rows = [dict(revision=1, from_node_id='current', to_node_id='current',
                 performance='观察记录原来允许公开。' * 35),
            dict(revision=2, from_node_id='current', to_node_id='current',
                 performance='观察记录授权已经撤回，请继续保密。'),
            dict(revision=3, from_node_id='current', to_node_id='current',
                 performance='我在门边等你的决定。')]
    session = SimpleNamespace(current_node_id='current', revision=3,
                              opening_performance={}, performance_history=rows)
    full = current_scene_history_index(session, '观察记录还能公开吗？')
    latest = next(unit for unit in full['evidence'] if unit['revision'] == 3)
    correction = next(unit for unit in full['evidence'] if unit['revision'] == 2)
    selected = dict(evidence=[correction, latest], omitted_units=1,
                    latest_omitted_revision=1)
    common = dict(system_prompt='只按实际历史回应。', human_prefix='上下文：',
                  data=dict(role='角色完整合同', current_scene='当前场景完整合同',
                            pacing='先回应', next_scene='尚未获准离开',
                            player_input='观察记录还能公开吗？'),
                  history_rows=[rows[-1]], history_preselected=True)
    expected = _fit_simple_turn_prompt_data(**common, fact_index=lambda: selected,
                                           max_tokens=10000)
    limit = count_tokens(common['system_prompt']) + count_tokens(
        common['human_prefix'] + render_prompt_context(expected))
    original = deepcopy((session, common))
    retrieved = [dict(revision=0, source='performance', current_visit=False,
                      text='另一幕的旧背景说明。' * 90)] if with_retrieval else []

    def expand_evidence(seed, within):
        return retrieved if within(retrieved) else []

    fitted = _fit_simple_turn_prompt_data(
        **common, max_tokens=limit, fact_index=lambda: full,
        evidence_records=retrieved, evidence_expander=expand_evidence,
        fact_index_expander=lambda within: current_scene_history_index(
            session, '观察记录还能公开吗？', within_prompt=within),
    )
    assert fitted == expected
    assert fitted['story_so_far'].count(rows[-1]['performance']) == 1
    assert rows[0]['performance'] not in fitted['story_so_far']
    assert (session, common) == original
