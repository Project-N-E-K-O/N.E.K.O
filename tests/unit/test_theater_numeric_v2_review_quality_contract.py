"""The advertised review JSON must include the fields needed by its enabled tasks."""

import json

import pytest

from services.theater.numeric_v2_evaluator import _build_transition_judge_messages
from tests.unit.test_theater_numeric_v2_player_transition import initiation_case


@pytest.mark.parametrize("recover,narration", [(False, ""), (True, ""), (True, "灯光微暗。")])
def test_review_example_declares_flat_recovery_fields(recover, narration):
    case = initiation_case()
    messages, _ = _build_transition_judge_messages(
        case["engine"], case["session"], player_input=case["message"],
        actor_performance={"performance": "（点头）我在这里。", "scene_narration": narration,
                           "suggested_inputs": ["先等等。", "带路吧。"]},
        check_missed_initiation=recover,
    )
    contract = messages[0].content.split("只输出一个完整 JSON", 1)[1]
    example, _ = json.JSONDecoder().raw_decode(contract[contract.index("{"):])
    assert "missed_initiation_check" not in example
    recovery_fields = {"player_request_quote": "", "missed_initiation": False, "public_destination_index": -1}
    if recover:
        assert {key: example[key] for key in recovery_fields} == recovery_fields
    else:
        assert not recovery_fields.keys() & example.keys()
