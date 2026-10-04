"""Newly displayed authored text invalidates the turn's pregenerated choices."""

from copy import deepcopy
from dataclasses import replace

import pytest

from services.theater import numeric_v2_workflow as workflow
from tests.unit.test_theater_numeric_v2_display_suggestions import CHOICES, _review


@pytest.mark.parametrize('text', ['家书原文：愿你平安。', '航行记录：所有人员生还。'])
@pytest.mark.parametrize('confirmed', [True, False])
def test_new_display_clears_choices_even_when_review_marks_them_post_display(text, confirmed):
    review = _review()
    if not confirmed:
        review = replace(review, display_dependent_suggestions=())
    candidate = {'performance': '请慢慢看。', 'suggested_inputs': list(CHOICES),
                 'fixed_narrations': [{'node_id': 'start', 'id': 'record',
                                       'position': 'after', 'text': text}]}
    before = deepcopy(candidate)
    result, removed = workflow._drop_undelivered_display_suggestions(candidate, review, node_id='start')
    assert result['suggested_inputs'] == []
    assert removed == 3
    assert candidate == before
    assert {k: v for k, v in result.items() if k != 'suggested_inputs'} == {
        k: v for k, v in before.items() if k != 'suggested_inputs'}


@pytest.mark.parametrize('position,node_id', [('before', 'start'), ('after', 'other')])
def test_entry_or_another_node_does_not_invalidate_unrelated_choices(position, node_id):
    candidate = {'suggested_inputs': list(CHOICES), 'fixed_narrations': [
        {'node_id': node_id, 'id': 'record', 'position': position, 'text': '显示文字。'}]}
    result, _ = workflow._drop_undelivered_display_suggestions(candidate, _review(), node_id='start')
    assert result['suggested_inputs'] == (list(CHOICES) if node_id == 'start' else list(CHOICES[1:]))


def test_new_display_never_restores_a_previously_rejected_choice():
    candidate = {'suggested_inputs': list(CHOICES[1:]), 'fixed_narrations': [
        {'node_id': 'start', 'id': 'record', 'position': 'after', 'text': '显示文字。'}]}
    result, removed = workflow._drop_undelivered_display_suggestions(candidate, _review(), node_id='start')
    assert result['suggested_inputs'] == []
    assert removed == 2


def test_formal_transition_keeps_its_existing_target_choices():
    candidate = {'segments': [{'phase': 'target_opening', 'performance': '到了。'}],
                 'suggested_inputs': list(CHOICES)}
    assert workflow._drop_undelivered_display_suggestions(candidate, _review(), node_id='start') == (candidate, 0)
