"""Actor admission uses the transport's serialized messages, including escaping."""

import json

import pytest

from services.theater import numeric_v2_actor as actor
from utils.llm_client import HumanMessage, SystemMessage, _normalize_messages


def test_wire_budget_rejects_a_body_that_fits_only_without_the_envelope():
    messages = [SystemMessage(content='保持原文中的引号。'), HumanMessage(content='记录："A"\n' * 600)]
    body_size = sum(actor.count_tokens(message.content) for message in messages)
    wire_size = actor.count_tokens(json.dumps(_normalize_messages(messages), ensure_ascii=False))
    assert wire_size > body_size
    with pytest.raises(actor.NumericV2ActorError, match="fixed_context_budget_exceeded"):
        actor._ensure_actor_messages_fit(messages, max_tokens=body_size)


@pytest.mark.parametrize("formal", [False, True])
def test_wire_pressure_removes_only_the_old_complete_turn(formal):
    system, prefix = '保持完整合同。', '数据：\n'
    rows = [{"revision": 1, "performance": '记录："A"\n' * 200},
            {"revision": 2, "performance": '许可未签，仍不可执行。', "player_input": '先等等。'}]
    base = {"player_input": '当前只问许可。', "contract": '不可替玩家执行。'}
    if formal:
        fit = lambda history, limit: actor._fit_turn_prompt_data(
            system_prompt=system, human_prefix=prefix, data={**base, "recent_context": history}, max_tokens=limit)
    else:
        fit = lambda history, limit: actor._fit_simple_turn_prompt_data(
            system_prompt=system, human_prefix=prefix, data=base, history_rows=history, max_tokens=limit)
    expected = fit(rows[-1:], 10000)
    wire = [{"role": "system", "content": system},
            {"role": "user", "content": prefix + actor.render_prompt_context(expected)}]
    limit = actor.count_tokens(json.dumps(wire, ensure_ascii=False))
    fitted = fit(rows, limit)
    assert fitted == expected
    assert fitted["player_input"] == base["player_input"]
    assert fitted["contract"] == base["contract"]
