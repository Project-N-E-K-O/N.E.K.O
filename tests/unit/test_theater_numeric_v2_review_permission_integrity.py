"""Review preserves compiled role permissions and their trailing conditions."""

from copy import deepcopy
from dataclasses import replace

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater.numeric_v2 import MAX_ACTOR_PROMPT_FIELD_TOKENS
from services.theater.numeric_v2_budget import numeric_v2_actor_budget
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_review_entry_plan_integrity import _fake_client, _messages, _request
from tests.unit.test_theater_numeric_v2_review_player_background import _case, _data


MODES = ("ordinary", "opening", "completion", "formal")
FIELDS = (("allowed_behaviors", "authorized_behaviors"), ("assertable_self_facts", "assertable_self_facts"))
TAIL = "；但未经保管人许可，不能拆封，也不代表周宁已经授权。"


def _clause(node_id, field):
    value = f"林雨在{node_id}的{field}范围说明。"
    # 填至真实编译上限，保证完整否定尾句落在旧复核的 360-token 截断位置之后。
    while evaluator.count_tokens(value + " detail" + TAIL) <= MAX_ACTOR_PROMPT_FIELD_TOKENS:
        value += " detail"
    value += TAIL
    assert evaluator.count_tokens(value) == MAX_ACTOR_PROMPT_FIELD_TOKENS == 384
    return value


def _fixture(*, restricted=False, many=False, legacy=False):
    source, session = _case(restricted=restricted)
    story = deepcopy(source.story)
    for node in story["nodes"]:
        beat = node["story_beat"]
        if legacy:
            beat.pop("acting_contract")
            continue
        for field, _ in FIELDS:
            beat["acting_contract"][field] = [_clause(node["id"], field)]
        if many:
            # 当前工坊生成器限四条；第九条覆盖的是 Runtime 编译合法的手工／导入合同。
            beat["acting_contract"]["allowed_behaviors"] += [
                f"林雨在{node['id']}可以核对第{number}份已公开资料；仍不替周宁决定。"
                for number in range(2, 10)
            ]
    engine = NumericV2Engine.from_mapping(story)
    return engine, replace(session, story_package_hash=engine.compiled.package_hash)


def _expected(engine, node_id, field):
    return [text.replace("林雨", "Lan").replace("周宁", "哥哥")
            for text in engine.nodes[node_id]["story_beat"]["acting_contract"][field]]


def test_restricted_role_keeps_local_permissions_without_restoring_hidden_background():
    engine, session = _fixture(restricted=True)
    data = _data(_messages(_request(engine, session, "ordinary")))
    for field, projected_field in FIELDS:
        assert data["current_scene"][projected_field] == _expected(engine, "start", field)
    assert "author_initial_player_background" not in data
    assert "author_initial_role_context" not in data
    assert "曾参与资料整理" not in str(data)


def test_formal_source_and_target_keep_their_own_permissions_separate():
    engine, session = _fixture(many=True)
    data = _data(_messages(_request(engine, session, "formal")))
    for field, projected_field in FIELDS:
        assert data["current_scene"][projected_field] == _expected(engine, "start", field)
        assert data["target_scene"]["acting_contract"][field] == _expected(engine, "middle", field)
    assert _expected(engine, "middle", "allowed_behaviors")[0] not in data["current_scene"]["authorized_behaviors"]


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['ordinary', 'formal'])
async def test_native_scene_review_sends_complete_permissions(monkeypatch, mode):
    requests, clients = [], []
    _fake_client(monkeypatch, requests, clients)
    engine, session = _fixture()
    kwargs = _request(engine, session, mode)
    result = await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(**kwargs)
    assert len(requests) == 1  # This fake records the scene half; player-action coverage is in split_review.
    assert len(clients) == (2 if mode == "formal" else 1)
    for field, projected_field in FIELDS:
        assert _data(requests[0])["current_scene"][projected_field] == _expected(engine, "start", field)
    assert clients[0]["timeout"] == evaluator.NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS == 8.0
    assert clients[0]["max_retries"] == 0
    assert clients[0]["max_completion_tokens"] == (
        evaluator.NUMERIC_V2_FORMAL_TRANSITION_JUDGE_MAX_OUTPUT_TOKENS if mode == "formal"
        else 320)
    assert not result.fact_candidates and not result.body_violations


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['formal'])
async def test_full_permissions_are_not_cut_to_send_an_oversized_request(monkeypatch, mode):
    requests, clients = [], []
    _fake_client(monkeypatch, requests, clients)
    engine, session = _fixture(many=True)
    kwargs = _request(engine, session, mode, repetitions=2000)
    before = deepcopy((engine.story, kwargs["session"].to_dict(), kwargs["actor_performance"]))
    messages = _messages(kwargs)
    for field, projected_field in FIELDS:
        assert _data(messages)["current_scene"][projected_field] == _expected(engine, "start", field)
    budget = numeric_v2_actor_budget(session.actor_budget_profile)
    assert budget["judge_input_max_tokens"] == budget["formal_judge_input_max_tokens"] == 10000
    assert sum(evaluator.count_tokens(message.content) for message in messages) > 10000
    with pytest.raises(evaluator.NumericV2EvaluatorError, match="transition_review_budget_exceeded"):
        await evaluator.NumericV2MetricEvaluator(object()).validate_transition_offer(**kwargs)
    assert requests == clients == []
    assert (engine.story, kwargs["session"].to_dict(), kwargs["actor_performance"]) == before
