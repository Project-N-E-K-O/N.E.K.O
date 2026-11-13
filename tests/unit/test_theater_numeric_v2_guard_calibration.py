"""Regress review evidence formatting and formal-output capacity; compare semantic accuracy separately with real models."""
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev
from services.theater.numeric_v2_runtime import TurnRequestV2
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.utils.theater_prompt import without_public_destination_indexes


@pytest.mark.parametrize('text', [
    '（收起凭据）左侧走廊通往阅览室，通道已经开放。',
    '（拿起琴盒）演奏厅在走廊尽头。（指向左侧）我们可以从这里过去。',
    '（关上抽屉）温室入口在花园东侧，但今天尚未开放。',
])
def test_mixed_quote_matches_the_exact_history_given_to_model(text):
    # Complete mixed fields and split content blocks must count as the same source; a valid source does not imply semantic authorization.
    case = initiation_case()
    session = replace(case['session'], opening_performance={'performance': text, 'suggested_inputs': []})
    assert ev._has_public_transition_quote(text, session)
    result = ev._parse_transition_judge_output(json.dumps(dict(
        offer_present=False, valid=False, body_violations=[], unsafe_suggestion_indexes=[],
        public_destination_quote=text)), initiation_session=session)
    assert not result.body_violations


def test_quote_cannot_splice_records_or_change_negation():
    case = initiation_case()
    session = replace(case['session'], opening_performance={'performance': '（收起凭据）通道今天没有开放。'},
                      revision=1, performance_history=({'revision': 1, 'from_node_id': 'start', 'to_node_id': 'start',
                          'input_text': '秘密门通往阅览室。', 'performance': '（打开抽屉）请先等候。',
                          'suggested_inputs': ['后门已经开放。']},))
    for quote in ['（收起凭据）通道今天已经开放。', '（收起凭据）请先等候。',
                  '秘密门通往阅览室。', '后门已经开放。']:
        assert not ev._has_public_transition_quote(quote, session)


def test_old_scene_quote_and_source_response_cannot_authorize_current_visit():
    case = initiation_case()
    old = '（收起凭据）旧码头通往档案馆。'
    public = '（指向花园）温室入口通道开放。'
    session = replace(case['session'], opening_performance={'performance': old}, revision=2,
        performance_history=({'revision': 2, 'from_node_id': 'old', 'to_node_id': 'start',
          'segments': [{'phase': 'source_response', 'performance': old},
                       {'phase': 'target_opening', 'performance': public}]},))
    assert ev._has_public_transition_quote(public, session)
    assert not ev._has_public_transition_quote(old, session)


@pytest.mark.asyncio
async def test_committed_entrance_bridge_supports_public_destination_without_completion_claim(tmp_path):
    from services.theater.numeric_v2_actor import _turn_messages
    from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime
    from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
    from tests.unit.test_theater_numeric_v2_transition_history import _candidate
    from tests.unit.theater_workshop.test_numeric_v2_branch import branchable_project
    from tests.utils.theater_prompt import prompt_payload

    quote = "北侧楼梯通往档案室，门已经打开。"
    old_response = "旧码头可以乘船去档案室。"
    current_opening = "大厅的顶灯亮着。"
    key = "scene:main_2:gate_opened"
    story = branchable_project()['story']
    for node in story['nodes'][:3]:
        node['min_turns'] = 1
    story['fact_contract'] = {"facts": {key: {
        "value_type": "bool", "visibility": "public", "description": "记录档案室已经开门。",
    }}}
    story['nodes'][1]['completion_contract'] = {"all": [{"key": key, "equals": True}]}
    story['nodes'][1]['route_gates'][0]['transition_contract'].update(
        reason="从北侧楼梯去档案室。", fallback_offer="要从北侧楼梯去档案室吗？", accept_input="好，请带我去档案室。",
    )
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    original = await runtime.start_session(session_id="entry-bridge", catgirl_binding=_binding(),
        opening_performance=_opening())
    entry = runtime.prepare_turn(original, TurnRequestV2("go", 0, "请带路。"), (), transition_intent="initiate")
    assert entry.session.current_node_id == 'main_2'
    candidate = {**_candidate(), "source_performance": old_response, "bridge_scene_narration": quote,
                 "target_scene_narration": current_opening, "target_performance": "总算到了。"}
    stored = await runtime.commit_turn(entry, engine.finalize_transition_performance(
        entry, candidate, target_opening=current_opening,
    ))
    session = stored.session
    request = "请带我去档案室。"
    sources, completion = [], []
    messages = ev._build_messages(engine, session, request,
        public_destination_evidence=sources, committed_evidence=completion)
    data = prompt_payload(messages[1].content)
    assert without_public_destination_indexes(data['scene_context'][0]['entry_bridge']) == [{"type": "narration", "text": quote}]
    assert messages[1].content.count(quote) == 1
    assert current_opening in '\n'.join(completion) and quote not in '\n'.join(completion)
    assert ev._has_public_transition_quote(quote, session)
    turn = TurnRequestV2("ask", session.revision, request)
    for intent in ("unclear", "initiate"):
        outcome = runtime.prepare_turn(stored, turn, (), transition_intent=intent, fact_operations=(
            {"op": "set", "key": key, "value": True, "visibility": "public"},
        ))
        assert (outcome.session.current_node_id != session.current_node_id) is (intent == "initiate")
        actor_messages = _turn_messages(engine, session, outcome, request, "沉着。", "小岚", "玩家")
        assert actor_messages[1].content.count(quote) == 1
    for candidate_quote, expected in ((quote, 'initiate'), (old_response, 'unclear')):
        parsed = ev._parse_output(json.dumps({"scene_complete": False, "metric_changes": {},
            "transition_intent": "initiate", "exit_matches": True, "exit_comparison": "去档案室。",
            "public_destination_quote": candidate_quote}), engine, request, session, public_destination_evidence=sources)
        assert parsed.transition_intent == expected


def test_proactive_transition_requires_quote_in_final_evaluator_request(monkeypatch):
    from services.theater.numeric_v2_budget import NUMERIC_V2_ACTOR_BUDGET_PROFILES
    from tests.utils.theater_prompt import prompt_payload

    case = initiation_case()
    engine = case["engine"]
    quote = "由蓝色防火梯进入西侧档案室，那里就是下一站。"
    opening = {"performance": "先看手上的清单。" * 90 + quote}
    session = replace(case["session"], opening_performance=opening, revision=1,
        performance_history=({"revision": 1, "from_node_id": "start", "to_node_id": "start",
                              "input_text": "刚刚的事已经完成。", "performance": "我把登记表收好了。"},))
    message = "我现在就出发。"
    monkeypatch.setattr(ev, "history_evidence", lambda *args, **kwargs: [])
    source = []
    messages = ev._build_messages(engine, session, message, public_destination_evidence=source)
    payload = prompt_payload(messages[1].content)
    assert quote in messages[1].content
    reply = json.dumps({"scene_complete": True, "metric_changes": {}, "transition_intent": "initiate",
        "exit_matches": True, "exit_comparison": "玩家要求前往公开去向。", "public_destination_quote": quote})
    assert ev._parse_output(reply, engine, message, session, public_destination_evidence=source).transition_intent == "initiate"

    payload["scene_context"] = payload["scene_context"][-1:]
    prefix = messages[1].content.split("======以下为", 1)[0]
    limit = ev.count_tokens(messages[0].content) + ev.count_tokens(prefix + ev.render_prompt_context(payload))
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES[session.actor_budget_profile],
                        "evaluator_input_max_tokens", limit)
    messages = ev._build_messages(engine, session, message, public_destination_evidence=source)
    assert quote not in messages[1].content
    assert ev._parse_output(reply, engine, message, session, public_destination_evidence=source).transition_intent == "unclear"


@pytest.mark.asyncio
async def test_formal_and_dispute_output_room_stays_independent_of_ordinary_floor(monkeypatch):
    # Full ordinary verdicts need 320; formal/dispute outputs and every deadline retain their existing budgets.
    options = []
    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): return False
        async def ainvoke(self, messages):
            if json.loads(messages[1].content[messages[1].content.index("{"):]).get("review_task") == "player_actions":
                return SimpleNamespace(content=json.dumps({"body_violations": [], "failure_reason": ""}))
            return SimpleNamespace(content=json.dumps(dict(offer_present=False, offer_quote="", valid=False,
                body_violations=[], unsafe_suggestion_indexes=[], failure_reason='',
                **({'delivery_matches_route': True} if formal else {}))))
    async def config(_): return {'model': 'test', 'base_url': 'http://test.invalid'}
    async def factory(*args, **kwargs): options.append(kwargs); return Client()
    monkeypatch.setattr(ev, '_model_config', config)
    monkeypatch.setattr(ev, 'create_chat_llm_async', factory)
    monkeypatch.setattr(ev, 'focus_extra_body', lambda *_: {'enable_thinking': True})
    case = initiation_case(); engine = case['engine']; session = case['session']
    outcome = engine.resolve_turn(session, TurnRequestV2('go', 0, '好。'), (), transition_intent='accept')
    evaluator = ev.NumericV2MetricEvaluator(object())
    for formal, dispute in [(False, False), (True, False), (True, True)]:
        await evaluator.validate_transition_offer(engine=engine, session=session, message='好。',
            actor_performance={'performance': '知道了。'}, transition_outcome=outcome if formal else None,
            dispute_review=dispute)
    assert [x['max_completion_tokens'] for x in options] == [320, 512, 190, 4096, 4096]
    assert [x['timeout'] for x in options] == [
        ev.NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS,
        ev.NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS,
        ev.NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS,
        ev.NUMERIC_V2_DISPUTE_JUDGE_TIMEOUT_SECONDS,
        ev.NUMERIC_V2_DISPUTE_JUDGE_TIMEOUT_SECONDS,
    ]
