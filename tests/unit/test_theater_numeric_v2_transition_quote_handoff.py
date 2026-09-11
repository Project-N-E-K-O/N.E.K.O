"""Pass public quotations through evaluation and review without replacing player-intent checks or persisting them in archives."""
import json
import pytest
from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as workflow
from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_transition_history import _candidate

QUOTE = '左侧走廊通往阅览室，通道已经开放。'


def test_evaluation_retains_only_verified_initiation_quote():
    """Pass only actual quotations; author summaries and uncertain intent cannot be attached as verified evidence."""
    c=initiation_case()
    for intent,quote,expected in [('initiate',QUOTE,QUOTE),('initiate','作者安排去阅览室。',''),('unclear',QUOTE,'')]:
        payload=dict(scene_complete=False,metric_changes={},transition_intent=intent,public_destination_quote=quote)
        result=ev._parse_output(json.dumps(payload),c['engine'],c['message'],c['session'])
        assert result.public_destination_quote==expected


@pytest.mark.parametrize('quote', [QUOTE,'作者安排去阅览室。'])
def test_formal_guard_quote_is_verified_again_at_projection(quote):
    """Even if the caller supplies an incorrect quotation, review input must not label it verified evidence."""
    c=initiation_case();engine=c['engine'];session=c['session']
    outcome=engine.resolve_turn(session,TurnRequestV2('go',0,c['message']),(),transition_intent='initiate')
    messages=ev._build_transition_judge_messages(engine,session,actor_performance={'segments':[],'suggested_inputs':[]},player_input=c['message'],transition_outcome=outcome,public_destination_quote=quote)
    data=json.loads(messages[1].content.split('：',1)[1])
    assert data['transition_authorization'].get('public_destination_quote')==(QUOTE if quote==QUOTE else None)
    assert '它不证明玩家已同意' in messages[0].content


@pytest.mark.asyncio
async def test_same_quote_reaches_fast_and_dispute_but_not_persistent_state(tmp_path,monkeypatch):
    """The real Workflow passes the same quotation during the first dispute; successful commits add no evidence state to Session or Ledger."""
    c=initiation_case();engine=c['engine'];runtime=NumericV2Runtime(engine,tmp_path)
    current=await runtime.start_session(session_id='quote_handoff',catgirl_binding=_binding(),opening_performance=c['session'].opening_performance)
    calls=[]
    async def evaluate(self,**kwargs):
        return ev.NumericV2EvaluationResult((),False,transition_intent='initiate',public_destination_quote=QUOTE)
    async def generate(self,**kwargs):
        return engine.finalize_transition_performance(kwargs['outcome'],_candidate(),target_opening='阅览室入口。')
    async def review(self,**kwargs):
        calls.append(kwargs)
        assert kwargs['public_destination_quote']==QUOTE
        return ev.NumericV2TransitionOfferReview(False,False,() if kwargs.get('dispute_review') else ('player_action',),(), '测试首次争议。')
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator,'evaluate',evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator,'validate_transition_offer',review)
    monkeypatch.setattr(workflow.NumericV2Actor,'generate_turn',generate)
    monkeypatch.setattr(workflow.NumericV2Actor,'_character_profile',lambda self:'温和。')
    result=await workflow.execute_numeric_v2_turn(config_manager=object(),runtime=runtime,current=current,turn=TurnRequestV2('go',0,c['message']),ensure_current_binding=lambda _: _binding())
    assert len(calls)==2 and calls[1]['dispute_review']
    assert result.stored.session.current_node_id=='ending_leave'
    assert 'public_destination_quote' not in result.stored.session.to_dict()
    assert 'public_destination_quote' not in result.stored.ledger_events[-1]
    assert await NumericV2Runtime(engine,tmp_path).restore_session('quote_handoff')==result.stored
