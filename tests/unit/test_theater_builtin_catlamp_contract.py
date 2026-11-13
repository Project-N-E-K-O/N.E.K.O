"""The catlamp ending needs a player choice and the role's delivered response."""

import json
from pathlib import Path

import pytest

from services.theater.numeric_v2_runtime import (
    NumericV2Engine,
    NumericV2Runtime,
    NumericV2RuntimeError,
    TurnRequestV2,
    validate_fact_candidates,
)
from tests.unit.test_theater_numeric_v2_runtime import _binding


PACKAGE = Path(__file__).resolve().parents[2] / "assets/theater/story_ccc0763441e3.json"
ACCEPTED = "scene:mainline_06:station_use_accepted"
ACKNOWLEDGED = "scene:mainline_06:station_use_acknowledged"


def _engine():
    story = json.loads(PACKAGE.read_text(encoding="utf-8"))
    # Keep the actual last act and ending, without replaying unrelated earlier acts.
    story["start_node_id"] = "mainline_06"
    story["nodes"] = [node for node in story["nodes"] if node["id"] in {"mainline_06", "ending_normal"}]
    story["nodes"][0]["type"] = "start"
    return NumericV2Engine.from_mapping(story)


def _claim(key, source, quote):
    return {
        "op": "set", "key": key, "value": True, "visibility": "public",
        "confidence": "confirmed", "subject": "当事人", "action": "确认",
        "object": "车站用途", "result": "已经明确回应",
        "evidence": [{"source": source, "quote": quote}],
    }


def test_displaying_the_station_proposal_does_not_finish_the_last_act():
    engine = _engine()
    session = engine.create_session(session_id="proposal", catgirl_binding=_binding(),
        opening_performance={"scene_narration": "", "performance": "", "suggested_inputs": []})
    assert session.opening_performance["fixed_narrations"][0]["id"] == "memorial_proposal"
    assert engine.completion_contract_satisfied(session) is False


@pytest.mark.parametrize("key,source,quote", [
    (ACKNOWLEDGED, "player_input", "你已经同意我的用途了。"),
    (ACCEPTED, "actor_performance", "他已经答应了。"),
])
def test_neither_party_can_supply_the_other_partys_completion_evidence(key, source, quote):
    engine = _engine()
    with pytest.raises(NumericV2RuntimeError, match="story_fact_candidate_evidence_source_not_allowed"):
        validate_fact_candidates([_claim(key, source, quote)],
            fact_contract={"facts": engine.fact_contract}, evidence_sources={source: quote})


@pytest.mark.asyncio
@pytest.mark.parametrize("message,response", [
    ("我愿意，就按你说的办。", "好，那就把这里用作纪念与互助的空间。谢谢你愿意一起做。"),
    ("我想先只做失物招领；日记和怀表先收好，暂不陈列。", "好，就先做失物招领。日记和怀表先收好，暂不陈列。谢谢你说清楚想法。"),
])
async def test_player_choice_stays_pending_until_the_roles_response_is_committed(tmp_path, message, response):
    engine = _engine()
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="agreement", catgirl_binding=_binding(),
        opening_performance={"scene_narration": "", "performance": "", "suggested_inputs": []})
    operations, _ = validate_fact_candidates([_claim(ACCEPTED, "player_input", message)],
        fact_contract={"facts": engine.fact_contract}, evidence_sources={"player_input": message})
    outcome = runtime.prepare_turn(current, TurnRequestV2("choice", 0, message), (),
        fact_operations=operations)
    assert engine.completion_contract_satisfied(outcome.session) is False
    finalized, _ = engine.finalize_actor_fact_candidates(current.session, outcome,
        candidates=[{"key": ACKNOWLEDGED, "value": True, "evidence_quote": response}],
        evidence_sources={"actor_performance": response})
    # An approved draft still cannot change the stored session until delivery commits.
    assert engine.completion_contract_satisfied((await runtime.restore_session("agreement")).session) is False
    committed = await runtime.commit_turn(finalized, {
        "performance": response, "suggested_inputs": [], "transition_offered": False,
    })
    assert engine.completion_contract_satisfied(committed.session) is True
    assert await runtime.restore_session("agreement") == committed
