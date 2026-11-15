"""An expressed intention is present evidence of willingness, not of its future result."""

import pytest

from services.theater.numeric_v2_runtime import NumericV2RuntimeError, validate_fact_candidates


@pytest.mark.parametrize("description,quote,accepted", [
    ("当事人明确表示愿意参与志愿服务。", "我打算和大家一起参加志愿服务。", True),
    ("当事人表达合作意愿。", "我打算和你合作，一起面对后果。", True),
    ("当事人明确表达出行意向。", "我打算和大家一道出行。", True),
    ("当事人明确表示愿意参与志愿服务。", "我打算明天再表达参与意愿。", False),
    ("当事人表达合作意愿。", "我还没表示愿意合作。", False),
    ("当事人表达合作意愿。", "我准备去和大家商量合作。", False),
    ("双方已经合作完成了修复。", "我打算和你合作修复。", False),
    ("当事人已经抵达目的地。", "我打算和大家一道出行。", False),
    ("当事人表示愿意归还物品，且实际完成归还。", "我打算归还物品。", False),
    ("当事人表示愿意参与志愿服务。", "我尚未决定要不要参加。", False),
])
def test_intention_evidence_does_not_turn_future_actions_into_completed_results(description, quote, accepted):
    key = "scene:start:result"
    contract = {"facts": {key: {"value_type": "bool", "visibility": "public", "description": description}}}
    candidate = {"op": "set", "key": key, "value": True, "visibility": "public", "confidence": "confirmed",
        "subject": "当事人", "action": "陈述", "object": "当前状态", "result": description,
        "evidence": [{"source": "player_input", "quote": quote}]}
    if not accepted:
        with pytest.raises(NumericV2RuntimeError, match="evidence_not_completed"):
            validate_fact_candidates([candidate], fact_contract=contract, evidence_sources={"player_input": quote})
        return
    operations, _ = validate_fact_candidates([candidate], fact_contract=contract, evidence_sources={"player_input": quote})
    assert operations[0]["value"] is True
