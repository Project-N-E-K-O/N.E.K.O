"""Current-field evidence can recover lost action wrappers, never changed visible text."""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_workflow as wf
from tests.unit import test_theater_numeric_v2_actor_fact_semantic_admission as semantic


NEON_BODY = (
    "（看着你因疼痛而扭曲的表情，眼神中闪过一丝不易察觉的动容，随即恢复决绝）既然这是你的选择，那我便不再犹豫。"
    "（抬起右手，掌心贴向胸口核心的位置，指尖微微发颤却坚定无比）记住这一刻。如果以后我们还能相遇，我会重新认识你。\n\n"
    "（随着一声低沉的电子嗡鸣，核心超载启动。一道肉眼可见的蓝色脉冲以两人为中心瞬间爆发，周围的战术灯骤然熄灭，"
    "特遣队的通讯频道陷入刺耳的静电噪音，装备指示灯全部熄灭。）\n\n"
    "（黑暗与寂静降临的瞬间，零号眼中的光芒迅速黯淡，身体无力地靠在你身上，声音微弱得几乎听不见）再见……阿岚……\n\n"
    "（历史记忆区物理烧毁完成，系统进入休眠状态，呼吸平稳但意识已沉入深处，不再有任何反应。）"
)
NEON_QUOTE = (
    "一道肉眼可见的蓝色脉冲以两人为中心瞬间爆发，周围的战术灯骤然熄灭，特遣队的通讯频道陷入刺耳的静电噪音，装备指示灯全部熄灭。"
    "（黑暗与寂静降临的瞬间，零号眼中的光芒迅速黯淡，身体无力地靠在你身上，声音微弱得几乎听不见）再见……阿岚……"
    "（历史记忆区物理烧毁完成，系统进入休眠状态，呼吸平稳但意识已沉入深处，不再有任何反应。）"
)
PAPER_BODY = "（值班员核验票据。）\n\n（在纸面压下蓝色验讫印记。）纸面已标记。\n（将凭条递回柜台外。）"
PAPER_QUOTE = "值班员核验票据。（在纸面压下蓝色验讫印记。）纸面已标记。（将凭条递回柜台外。）"


@pytest.mark.asyncio
@pytest.mark.parametrize("definition,body,quote", [
    (semantic.NEON, NEON_BODY, NEON_QUOTE), (semantic.PAPER, PAPER_BODY, PAPER_QUOTE),
])
async def test_recovered_current_format_uses_literal_full_field_before_native_fact_approval(
    tmp_path, monkeypatch, definition, body, quote,
):
    assert quote not in body
    _, _, result, _, requests = await semantic._run(tmp_path, monkeypatch,
        definition=definition, quote=quote, performance=body)
    assert result.stored.session.story_state["facts"][semantic.scenario.KEY]["value"] is True
    assert result.performance["performance"] == body
    assert len(requests) == result.diagnostics["completion_fact_review_calls"] == 1
    assert requests[0]["payload"]["claims"][0]["evidence"][0]["quote"] == body
    assert result.diagnostics["actor_generation_attempts"] == 1


def _expand(performance, quote, *, source="performance", enabled=True, player_input="请核验票据。", published_offer=""):
    candidate = {"key": semantic.scenario.KEY, "value": True, "evidence_quote": quote}
    original = deepcopy((candidate, performance))
    result = wf._expand_grouped_actor_fact_quote(candidate,
        {**semantic.PAPER, "evidence_source": source}, performance,
        player_input=player_input, published_offer=published_offer, recover_formatting=enabled)
    assert (candidate, performance) == original
    return result


@pytest.mark.parametrize("case", [
    "multiple_occurrences", "multiple_fields", "cross_field", "cross_segment", "noncontinuous",
    "missing_word", "changed_punctuation", "internal_space", "lost_negation", "disabled", "wrong_source",
    "player_input", "published_offer",
])
def test_format_recovery_does_not_accept_ambiguous_or_changed_visible_text(case):
    body, quote = PAPER_BODY, PAPER_QUOTE
    performance = {"performance": body}
    source, enabled, context = "performance", True, {}
    if case == "multiple_occurrences":
        performance["performance"] = body + "\n" + body
    elif case == "multiple_fields":
        performance["scene_narration"] = "值班员核验票据。在纸面压下蓝色验讫印记。纸面已标记。将凭条递回柜台外。"
    elif case == "cross_field":
        performance = {"performance": "（值班员核验票据。）", "scene_narration": "在纸面压下蓝色验讫印记。纸面已标记。将凭条递回柜台外。"}
    elif case == "cross_segment":
        performance = {"segments": [{"phase": "source_response", "performance": body}]}
    elif case == "noncontinuous":
        quote = quote.replace("纸面已标记。", "")
    elif case == "missing_word":
        quote = quote.replace("蓝色", "")
    elif case == "changed_punctuation":
        quote = quote.replace("票据。", "票据，")
    elif case == "internal_space":
        performance["performance"] = body.replace("蓝色验讫", "蓝色  验讫")
    elif case == "lost_negation":
        performance["performance"] = body.replace("在纸面压下", "未在纸面压下")
    elif case == "disabled":
        enabled = False
    elif case in {"player_input", "published_offer"}:
        context[case] = quote
    else:
        source = "player_input"
    assert _expand(performance, quote, source=source, enabled=enabled, **context)["evidence_quote"] == quote


def test_single_narration_field_and_internal_spaces_are_preserved():
    narration = "值班员核验票据。在纸面压下蓝色  验讫印记。纸面已标记。将凭条递回柜台外。"
    quote = PAPER_QUOTE.replace("蓝色验讫", "蓝色  验讫")
    assert _expand({"performance": "请留在柜台旁。", "scene_narration": narration}, quote)["evidence_quote"] == narration


def test_already_verbatim_quote_keeps_the_existing_expansion_behavior():
    performance = {"performance": "（值班员核验票据后盖章，随后交还凭条。）结果已齐。"}
    quote = "核验票据"
    assert _expand(performance, quote) == _expand(performance, quote, enabled=False)


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled,approve", [(False, True), (True, False)])
async def test_format_recovery_cannot_record_without_independent_fact_approval(tmp_path, monkeypatch, enabled, approve):
    _, _, result, _, requests = await semantic._run(tmp_path, monkeypatch,
        definition=semantic.PAPER, quote=PAPER_QUOTE, performance=PAPER_BODY,
        evaluator_enabled=enabled, approve=approve)
    assert semantic.scenario.KEY not in result.stored.session.story_state["facts"]
    assert len(requests) == int(enabled)
    assert result.diagnostics["fact_candidates_rejected"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["wrong_type", "wrong_target_value", "other_node"])
async def test_format_recovery_does_not_expand_an_unallowed_typed_target(tmp_path, monkeypatch, case):
    candidate = {"key": semantic.scenario.KEY, "value": True, "evidence_quote": PAPER_QUOTE}
    if case == "wrong_type":
        candidate["value"] = 1
    elif case == "wrong_target_value":
        candidate["value"] = False
    else:
        candidate["key"] = "scene:another:done"
    _, _, result, _, requests = await semantic._run(tmp_path, monkeypatch,
        definition=semantic.PAPER, quote=PAPER_QUOTE, performance=PAPER_BODY, candidate=candidate)
    assert not requests
    assert semantic.scenario.KEY not in result.stored.session.story_state["facts"]
    assert result.diagnostics["fact_candidates_rejected"] == 1
