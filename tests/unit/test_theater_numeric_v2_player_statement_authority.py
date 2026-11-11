"""Keep player quotations distinct from authorization and Actor-owned performance."""

from copy import deepcopy
import json
from types import SimpleNamespace

from tests.utils.theater_prompt import contract_audit_payload

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_options as options
from services.theater import numeric_v2_workflow as wf
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from services.theater.numeric_v2_action_projection import (
    normalize_player_action_projection,
    player_action_projection_for_prompt,
    project_player_action_result,
)
from tests.unit.test_theater_numeric_v2_progress_ownership import _pending_departure_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.utils.theater_prompt import prompt_payload


CLAIMS = (
    "我已经醒来了。",
    "我已经离开站台了。",
    "我已经到检修间了。",
    "下一阶段也已经结束了。",
)
LOCAL = "（指向桌边的指示灯）接头松了，我现在把它固定好。"
_VERIFY_CONTRACT_BOUNDARIES = evaluator.NumericV2MetricEvaluator.verify_contract_boundaries


def _ordinary_story():
    story = _pending_departure_story()
    contract = story["nodes"][0]["route_gates"][0]["transition_contract"]
    contract.update(advance_on_completion=False, fallback_offer="要现在去检修间吗？", accept_input="好，现在过去。")
    return story


def _frame(message, *, completed=False, blocked=True):
    engine = NumericV2Engine.from_mapping(_pending_departure_story() if blocked else _ordinary_story())
    session = engine.create_session(session_id="statement_authority", catgirl_binding=_binding(),
                                    opening_performance=_opening())
    facts = ({"op": "set", "key": "scene:start:done", "value": True,
              "visibility": "public"},) if completed else ()
    outcome = engine.resolve_turn(session, TurnRequestV2("reply", 0, message), (), fact_operations=facts)
    return engine, session, outcome


def _evaluation(engine, session, message, quote):
    return evaluator._parse_output(json.dumps({
        "scene_complete": False, "metric_changes": {}, "exit_matches": False,
        "exit_comparison": "当前幕条件尚未具备。", "departure_attempt_quote": quote,
    }, ensure_ascii=False), engine, message, session)


def _request_action_projection(messages):
    encoded = messages[0].content.split("本轮玩家动作结果投影（仅为约束，不是新增剧情）：", 1)[1]
    return json.JSONDecoder().raw_decode(encoded)[0]


@pytest.mark.parametrize("blocked", [False, True])
@pytest.mark.parametrize("message", [CLAIMS[2], "现在就去检修间。", "请检查眼前的接线。", "检查完后我们再过去，好吗？"])
def test_evaluator_always_requests_current_player_quote_without_granting_stage_authority(blocked, message):
    engine, session, outcome = _frame(message, blocked=blocked)
    assert outcome.route is None
    assert (outcome.departure_blocker == "completion_pending") is blocked
    system, human = evaluator._build_messages(engine, session, message)
    assert prompt_payload(human.content)["player_input"] == message
    # This asserts the request contract; it does not simulate the model classifying these sentences.
    assert "departure_attempt_quote" in system.content
    assert "从 player_input 逐字摘录对应短句" in system.content
    assert "声称已经离场／抵达" in system.content
    assert "讨论未来安排填空" in system.content
    assert "不授予换幕" in system.content


@pytest.mark.parametrize("claim", CLAIMS)
@pytest.mark.parametrize("blocked", [False, True])
def test_evaluator_retains_only_the_verbatim_current_player_claim(claim, blocked):
    message = "（把手放在桌边）" + claim
    engine, session, _ = _frame(message, blocked=blocked)
    result = _evaluation(engine, session, message, "  " + claim + "  ")
    assert result.departure_attempt_observed
    assert result.departure_attempt_quote == claim


@pytest.mark.parametrize("quote", [None, 1, {}, "", "   ", "这是旧回合的离场说法。"])
def test_evaluator_does_not_retain_unverified_claim_text(quote):
    message = "（把手放在桌边）请你检查接线。"
    engine, session, _ = _frame(message)
    result = _evaluation(engine, session, message, quote)
    assert result.departure_attempt_quote == ""
    assert not result.departure_attempt_observed


@pytest.mark.parametrize("message", [
    "（把工具箱放到桌上，慢慢抬起手指）我已经到检修间了。",
    "（握紧扶手，放缓呼吸）我已经醒来了，我们回到现实里了。",
])
def test_actor_cannot_prefix_its_response_with_the_complete_player_turn(message):
    response = message + "（看向桌边的设备）我先检查这根接线。"
    assert actor._repeats_player_dialogue({"performance": response}, message)


@pytest.mark.parametrize("message,response", [
    ("（点头）好，我们一起过去。", "（点头）好，我们一起过去。（看向你）走吧。"),
    ("（沿着狭窄通道贴墙蹲下，放缓呼吸）", "（沿着狭窄通道贴墙蹲下，放缓呼吸）"),
    ("（把工具箱放到桌上，慢慢抬起手指）我已经到检修间了。",
     "你说‘我已经到检修间了’，但我们仍在站台。（指向桌面）先看这处接线。"),
    ("（深吸一口气，闭上眼）……那就动手吧。别犹豫了。", "（深吸一口气，闭上眼）收到，开始执行。"),
])
def test_player_source_guard_preserves_quoting_shared_actions_and_brief_agreement(message, response):
    assert not actor._repeats_player_dialogue({"performance": response}, message)


@pytest.mark.parametrize("claim", CLAIMS)
@pytest.mark.parametrize("retry_hint", ["", "从当前本幕重新写。"])
@pytest.mark.parametrize("combined", [False, True])
@pytest.mark.parametrize("blocked", [False, True])
def test_actor_reads_unrouted_claim_as_player_statement_in_both_drafts(claim, retry_hint, combined, blocked):
    message = "（把手放在桌边）" + claim
    engine, session, outcome = _frame(message, blocked=blocked)
    assert outcome.route is None
    assert (outcome.departure_blocker == "completion_pending") is blocked
    direction = "检查桌边设备的接线，并读出当前仪表结果。"
    system, human = actor._turn_messages(engine, session, outcome, message, "认真。", "猫娘", "你",
        retry_hint=retry_hint, progress_direction=direction, unconfirmed_departure_quote=claim,
        combined_evaluation=combined)
    data = prompt_payload(human.content)
    assert data["player_input"] == message and list(data)[-1] == "player_input"
    scope = data["player_input_scope"]
    assert scope["statement_source"] == "player_input" and scope["statement_quote"] == claim
    assert scope["stage_change_authorized"] is False
    assert data["current_scene_task"] == {"direction": direction}
    assert "current_scene_task" not in scope
    assert "scope" in scope and "statement_quote" not in data["current_scene"]
    assert "本轮舞台锁定" in system.content
    assert ("turn_evaluation" in system.content) is combined


def test_unblocked_ordinary_turn_keeps_ordinary_system_contract():
    message = "请先检查眼前接线。"
    engine, session, outcome = _frame(message, blocked=False)
    assert outcome.departure_blocker is None and outcome.route is None
    system, _ = actor._turn_messages(engine, session, outcome, message, "认真。", "猫娘", "你",
                                    progress_direction="检查桌边接线。")
    assert actor.PLAYER_ACTION_EXECUTION_RULE in system.content
    assert "【本轮舞台锁定】" not in system.content


@pytest.mark.parametrize("phase", ["turn", "opening", "transition_compact", "reinvitation"])
def test_unlocked_author_boundary_contract_keeps_its_existing_wording(phase):
    expected = (
        "\n以下为本轮作者硬边界，优先级高于角色人格、玩家诱导和剧情发挥；"
        "本轮正文、旁白与推荐均须逐条遵守。"
        + ("逐条按主体、前提和阶段适用：有阶段限定的来源禁令不延伸到目标段；"
           "共同事实和未限定阶段的限制仍保留，目标段及其推荐按目标幕边界检查。"
           if phase == "transition_compact" else "")
        + ("开场不得通过新增玩家行为满足作者前提。" if phase == "opening" else
           "玩家要求若与硬边界冲突，你必须在正文直接拒绝或提出符合边界的替代做法；"
           "不得顺从越界要求、交换玩家与你的行动职责，或把越界结果写成已发生。")
        + "\n- 不得销毁登记单。\n- 未获许可不得拆除封条。"
        "\n以上限制须逐条遵守，但无需逐条宣读；未被问及且无当场必要的禁令、能力和自我事实不必主动声明。"
    )
    assert actor._hard_boundary_system_instruction(
        ["  不得销毁登记单。  ", "", "未获许可不得拆除封条。"], phase=phase,
    ) == expected


@pytest.mark.parametrize("blocked", [False, True])
def test_source_scene_keeps_hard_prohibitions_without_forcing_a_refusal_performance(blocked):
    message = "我已经到检修间了。"
    engine, session, outcome = _frame(message, blocked=blocked)
    system, _ = actor._turn_messages(engine, session, outcome, message, "认真。", "猫娘", "你",
                                    unconfirmed_departure_quote=message)
    assert "本幕不得销毁登记单。" in system.content
    assert "不得顺从越界要求、交换玩家与你的行动职责，或把越界结果写成已发生。" in system.content
    assert "你必须在正文直接拒绝或提出符合边界的替代做法" not in system.content
    boundaries = ["不得销毁登记单。", "未获许可不得拆除封条。"]
    contract = actor._hard_boundary_system_instruction(boundaries, source_scene_locked=True)
    for boundary in boundaries:
        assert "\n- " + boundary in contract
    assert "本轮正文、旁白与推荐均须逐条遵守。" in contract


@pytest.mark.parametrize("blocked", [False, True])
@pytest.mark.parametrize("quote", ["", "我离开了。"])
def test_unrouted_actor_drops_departure_confirmation_only_from_its_detached_request(blocked, quote):
    message = "我离开了。周六见。"
    engine, session, outcome = _frame(message, blocked=blocked)
    projection = outcome.ledger_event["player_action_projection"]
    ledger_before = deepcopy(outcome.ledger_event)
    normalized_before = normalize_player_action_projection(projection)
    assert normalized_before["player_left_current_scene"] is True
    messages = actor._turn_messages(engine, session, outcome, message, "认真。", "猫娘", "你",
        player_action_projection=projection, unconfirmed_departure_quote=quote)
    projected = _request_action_projection(messages)
    assert projected["confirmed_actions"] == []
    assert "player_left_current_scene" not in projected
    assert projected["has_confirmed_player_action"] is False
    assert projected["future_references"] == [{
        "kind": "future_return_or_follow_up", "status": "future", "source": "player_input",
        "evidence_ref": "player_input",
    }]
    assert prompt_payload(messages[1].content)["player_input"] == message
    assert outcome.ledger_event == ledger_before
    assert normalize_player_action_projection(projection) == normalized_before
    shared = player_action_projection_for_prompt(projection)
    assert shared["player_left_current_scene"] is True
    assert shared["confirmed_actions"][0]["kind"] == "leave_current_scene"


@pytest.mark.parametrize("completed", [False, True])
def test_stage_projection_filter_keeps_local_actions_and_authorized_transition_results(completed):
    message = "我签了字。我离开了。周六见。"
    engine, session, outcome = _frame(message, completed=completed)
    projection = project_player_action_result(message, transition_intent="initiate", route_changed=completed)
    local = project_player_action_result("我签了字。")["confirmed_actions"][0]
    projection["confirmed_actions"].append(local)
    before, ledger_before = deepcopy(projection), deepcopy(outcome.ledger_event)
    messages = actor._turn_messages(engine, session, outcome, message, "认真。", "猫娘", "你",
        player_action_projection=projection, unconfirmed_departure_quote="我离开了。")
    projected = _request_action_projection(messages)
    assert local in projected["confirmed_actions"]
    assert projected["has_confirmed_player_action"] is True
    departures = [row for row in projected["confirmed_actions"] if row["kind"] == "leave_current_scene"]
    transitions = [row for row in projected["confirmed_actions"] if row["kind"] == "transition_committed"]
    if completed:
        assert departures == [{"kind": "leave_current_scene", "status": "completed",
                                "source": "player_input", "evidence_ref": "player_input"}]
        assert transitions == [{"kind": "transition_committed", "status": "completed",
                                 "source": "runtime_transition"}]
        assert projected["player_left_current_scene"] is True
    else:
        assert not departures and not transitions
        assert "player_left_current_scene" not in projected
    assert prompt_payload(messages[1].content)["player_input"] == message
    assert projection == before and outcome.ledger_event == ledger_before


@pytest.mark.parametrize("message", [
    "（在登记单上签下自己的名字）已经签好了。",
    "（端起桌边的水杯喝了一口）这水还是温的。",
    "（走向门口，停在门内）我还有个问题。",
    "如果接线检查完了，我们再去检修间，好吗？",
])
@pytest.mark.parametrize("blocked", [False, True])
def test_local_actions_and_future_questions_are_not_relabelled_as_unconfirmed_claims(message, blocked):
    engine, session, outcome = _frame(message, blocked=blocked)
    projection = project_player_action_result(message)
    original = deepcopy(projection)
    _, human = actor._turn_messages(engine, session, outcome, message, "认真。", "猫娘", "你",
                                    player_action_projection=projection)
    data = prompt_payload(human.content)
    assert data["player_input"] == message
    assert not isinstance(data.get("player_input_scope"), dict)
    assert projection == original


@pytest.mark.parametrize("blocked", [False, True])
def test_invented_quote_is_not_packaged_as_player_statement(blocked):
    message = "请你检查眼前设备。"
    engine, session, outcome = _frame(message, blocked=blocked)
    _, human = actor._turn_messages(engine, session, outcome, message, "认真。", "猫娘", "你",
                                    unconfirmed_departure_quote="我已经到检修间了。")
    assert not isinstance(prompt_payload(human.content).get("player_input_scope"), dict)
    assert "我已经到检修间了。" not in human.content


def test_reinvitation_does_not_receive_the_player_claim_or_last_current_scene_task():
    engine, session, outcome = _frame("", blocked=False)
    _, human = actor._turn_messages(engine, session, outcome, "", "认真。", "猫娘", "你",
        input_source="reinvite", unconfirmed_departure_quote="我已经到检修间了。",
        progress_direction="检查桌边设备接线。")
    data = prompt_payload(human.content)
    assert "player_input_scope" not in data
    assert data["player_input"] == ""


def test_authorized_runtime_transition_does_not_receive_unconfirmed_statement_scope():
    message = "我已经到检修间了。"
    engine, session, outcome = _frame(message, completed=True)
    assert outcome.route is not None and outcome.session.current_node_id != session.current_node_id
    _, human = actor._turn_messages(engine, session, outcome, message, "认真。", "猫娘", "你",
                                    unconfirmed_departure_quote=message,
                                    progress_direction="检查桌边设备接线。")
    assert "player_input_scope" not in prompt_payload(human.content)


@pytest.mark.asyncio
async def test_actor_generate_passes_exact_source_tag_into_its_real_request(monkeypatch):
    message = "（把手放在桌边）我已经到检修间了。"
    claim = "我已经到检修间了。"
    engine, session, outcome = _frame(message)
    requests = []

    async def invoke(self, messages, **kwargs):
        requests.append(messages)
        return {"performance": LOCAL, "suggested_inputs": [], "fact_candidates": [],
                "transition_offered": False}

    monkeypatch.setattr(actor.NumericV2Actor, "_invoke", invoke)
    result = await actor.NumericV2Actor(object()).generate_turn(engine=engine, session=session, outcome=outcome,
        player_input=message, character_profile="认真。", unconfirmed_departure_quote=claim)
    assert result["performance"] == LOCAL and len(requests) == 1
    data = prompt_payload(requests[0][1].content)
    assert data["player_input"] == message
    assert data["player_input_scope"]["statement_quote"] == claim
    assert data["player_input_scope"]["stage_change_authorized"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("message,quote,locked", [
    ("（把手放在桌边）我已经到检修间了。", "我已经到检修间了。", True),
    ("如果检查完了，我们再去检修间，好吗？", "", False),
    ("请先检查眼前设备。", "我已经到检修间了。", False),
])
async def test_generate_uses_the_same_stage_authority_for_messages_parser_and_suggestions(
    monkeypatch, message, quote, locked,
):
    engine, session, outcome = _frame(message, blocked=False)
    assert outcome.route is None and outcome.departure_blocker is None
    cast = actor.NumericV2CastProjection.from_story(engine.story, player_name="你", catgirl_name="猫娘")
    preview = actor._next_scene_preview_for_actor(engine, cast, engine.nodes[session.current_node_id],
        outcome.session.metrics, facts=outcome.session.story_state["facts"])
    assert preview["status"] == "after_acceptance_only"
    requests = []
    recommendation = "（观察桌边仪表）接下来显示什么读数？"

    async def invoke(self, messages, **kwargs):
        requests.append((messages, kwargs))
        return {"performance": LOCAL, "suggested_inputs": [recommendation], "fact_candidates": [],
                "transition_offered": False}

    monkeypatch.setattr(actor.NumericV2Actor, "_invoke", invoke)
    result = await actor.NumericV2Actor(object()).generate_turn(engine=engine, session=session, outcome=outcome,
        player_input=message, character_profile="认真。", unconfirmed_departure_quote=quote)
    assert len(requests) == 1
    messages, flags = requests[0]
    data = prompt_payload(messages[1].content)
    assert data["player_input"] == message
    assert isinstance(data.get("player_input_scope"), dict) is locked
    assert flags["suppress_transition_offer"] is locked
    assert flags["suppress_suggestions"] is locked
    assert result["suggested_inputs"] == ([] if locked else [recommendation])
    assert result["performance"] == LOCAL


@pytest.fixture
def narrow_workflow(monkeypatch):
    """Exercise Runtime and Workflow, with explicit body verdicts rather than provider semantics."""
    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", options.aload_theater_module_options)
    monkeypatch.setattr(wf, "_can_combine_ordinary_turn", lambda *args: False)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "认真。")

    async def direction(self, **kwargs):
        assert not kwargs["claims"]
        return wf.NumericV2StoryEvidenceReview(direction="当场检查设备接线。")

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "review_story_evidence", direction)


@pytest.mark.asyncio
@pytest.mark.parametrize("claim", CLAIMS)
@pytest.mark.parametrize("blocked", [False, True])
async def test_unrouted_claim_reaches_both_drafts_as_unconfirmed_original(tmp_path, monkeypatch, narrow_workflow,
                                                                       claim, blocked):
    message = "（把手放在桌边）" + claim
    engine = NumericV2Engine.from_mapping(_pending_departure_story() if blocked else _ordinary_story())
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="authority_retry", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    original = deepcopy(current)
    calls = []
    rejected = "后续地点的结果已经完成。"

    async def evaluate(self, **kwargs):
        return _evaluation(engine, current.session, message, claim)

    async def generate(self, **kwargs):
        calls.append(deepcopy(kwargs))
        assert (kwargs["outcome"].departure_blocker == "completion_pending") is blocked
        assert kwargs["outcome"].route is None and kwargs["outcome"].session.status == "active"
        assert kwargs["player_input"] == message
        assert kwargs["unconfirmed_departure_quote"] == claim
        assert kwargs["progress_direction"] == "当场检查设备接线。"
        assert kwargs.get("draft_repair_context") is None
        assert rejected not in kwargs.get("retry_hint", "")
        return {"performance": rejected if len(calls) == 1 else LOCAL, "suggested_inputs": [],
                "fact_candidates": [], "transition_offered": False}

    async def contract(self, **kwargs):
        if kwargs["actor_performance"]["performance"] == rejected:
            kwargs["diagnostics"]["findings"] = [{"prior": {"id": "d0"}, "quote": rejected}]
            return ("不能把后续阶段写成已经发生。",)
        return ()

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("reply", 0, message), ensure_current_binding=lambda _: _binding())
    assert current == original
    assert len(calls) == 2 and calls[1].get("retry_hint")
    assert result.performance["performance"] == LOCAL
    assert result.stored.session.current_node_id == "start" and result.stored.session.revision == 1
    assert rejected not in json.dumps(result.stored.session.performance_history, ensure_ascii=False)
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
async def test_no_blocker_claim_reaches_the_actual_actor_request_with_reviewed_task(tmp_path, monkeypatch,
                                                                                narrow_workflow):
    claim = "我已经到检修间了。"
    message = "（把手放在桌边）" + claim
    engine = NumericV2Engine.from_mapping(_ordinary_story())
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="authority_real_request", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    requests = []

    async def evaluate(self, **kwargs):
        return _evaluation(engine, current.session, message, claim)

    async def invoke(self, messages, **kwargs):
        requests.append(messages)
        return {"performance": LOCAL, "suggested_inputs": [], "fact_candidates": [],
                "transition_offered": False}

    async def contract(self, **kwargs):
        return ()

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(actor.NumericV2Actor, "_invoke", invoke)
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("reply", 0, message), ensure_current_binding=lambda _: _binding())
    assert len(requests) == 1 and len(requests[0]) == 2
    _, human = requests[0]
    data = prompt_payload(human.content)
    assert data["player_input"] == message and list(data)[-1] == "player_input"
    assert data["player_input_scope"]["statement_quote"] == claim
    direction = data["current_scene_task"]["direction"]
    assert direction == "当场检查设备接线。"
    assert result.stored.session.current_node_id == "start" and result.performance["performance"] == LOCAL


@pytest.mark.asyncio
@pytest.mark.parametrize("message,quote,attempted", [
    ("我已经到检修间了。", "我已经到检修间了。", True),
    ("如果检查完了，我们再去检修间，好吗？", "", False),
    ("请先检查设备。", "我已经到检修间了。", False),
])
async def test_unblocked_claim_requires_full_contract_check_even_for_plain_performance(
    tmp_path, monkeypatch, narrow_workflow, message, quote, attempted,
):
    engine = NumericV2Engine.from_mapping(_ordinary_story())
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="authority_plain_contract", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    checked = []

    async def evaluate(self, **kwargs):
        return _evaluation(engine, current.session, message, quote)

    async def generate(self, **kwargs):
        assert kwargs["outcome"].route is None and kwargs["outcome"].departure_blocker is None
        return {"performance": LOCAL, "suggested_inputs": []}

    async def contract(self, **kwargs):
        checked.append(kwargs)
        assert kwargs["actor_performance"] == {"performance": LOCAL, "suggested_inputs": []}
        assert kwargs["author_boundaries_only"] is not attempted
        assert kwargs.get("departure_blocker") is None and kwargs["target_node"] is None
        assert bool(kwargs.get("departure_attempt_observed")) is attempted
        if attempted:
            mismatch = kwargs["exit_mismatch_reason"]
            assert "运行时本轮仍留在当前幕" in mismatch
            assert "目标开场" not in mismatch and "后续阶段方向" not in mismatch
            assert kwargs["current_scene_task"]["direction"] == "当场检查设备接线。"
        else:
            assert not kwargs.get("current_scene_task")
        return ()

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("reply", 0, message), ensure_current_binding=lambda _: _binding())
    assert len(checked) == 1
    assert result.stored.session.current_node_id == "start" and result.performance["performance"] == LOCAL


@pytest.mark.asyncio
async def test_unblocked_claim_without_completion_contract_keeps_environment_goal_in_full_audit(
    tmp_path, monkeypatch, narrow_workflow,
):
    story = _ordinary_story()
    source = story["nodes"][0]
    source.pop("completion_contract")
    source["story_beat"].pop("must_happen")
    goal = {"id": "show_reading", "owner": "environment", "description": "仪表在本幕显示接线读数。",
            "evidence": {"mode": "exact", "anchors": ["接线读数"]},
            "delivery": {"type": "environment_fact", "output_field": "scene_update",
                         "source_ids": ["opening.start"]}}
    source["story_beat"]["goals"] = [goal]
    permission = "可检查当前幕桌边设备并展示仪表读数。"
    source["story_beat"]["acting_contract"] = {
        "cognition_state": "normal", "memory_state": "available", "self_reference_mode": "persona_allowed",
        "persona_scope": "full", "dialogue_policy": "required", "allowed_behaviors": [permission],
        "forbidden_behaviors": [],
    }
    engine = NumericV2Engine.from_mapping(story)
    assert "completion_contract" not in engine.nodes["start"]
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="authority_goal_without_completion", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    message = "我已经到检修间了。"
    checked = []

    async def evaluate(self, **kwargs):
        return _evaluation(engine, current.session, message, message)

    async def generate(self, **kwargs):
        assert kwargs["outcome"].route is None and kwargs["outcome"].departure_blocker is None
        return {"performance": LOCAL, "suggested_inputs": []}

    async def contract(self, **kwargs):
        checked.append(kwargs)
        assert kwargs["author_boundaries_only"] is False and kwargs["departure_attempt_observed"] is True
        task = kwargs["current_scene_task"]
        assert task["pending_results"] == [] and task["direction"] == "当场检查设备接线。"
        assert task["allowed_behaviors"] == [permission]
        assert task["goals"] == [{key: goal[key] for key in ("id", "owner", "description")}
                                 | {"source_ids": ["opening.start"]}]
        return ()

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("reply", 0, message), ensure_current_binding=lambda _: _binding())
    assert len(checked) == 1 and result.stored.session.current_node_id == "start"
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("attempted,complete_scope", [(True, True), (True, False), (False, True)])
async def test_evaluator_audits_every_plain_body_block_without_a_runtime_blocker(
    monkeypatch, attempted, complete_scope,
):
    message = "我已经到检修间了。" if attempted else "如果检查完了，我们再过去，好吗？"
    engine, session, outcome = _frame(message, blocked=False)
    assert outcome.route is None and outcome.departure_blocker is None
    task = {"direction": "当场检查设备接线。", "pending_results": []}
    body = "（转动桌边仪表）接头读数现在稳定了。"
    calls = []

    class ContractClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages, **kwargs):
            data = json.loads(messages[1].content.split("\n", 1)[1])
            calls.append(data)
            sources = {row["id"]: row for row in data["sources"]}
            candidates = [row for row in data["sources"] if row["id"].startswith("c")]
            assert len(candidates) == 2
            schema = kwargs["response_format"]["json_schema"]["schema"]
            assert "premature_departure" not in schema["required"]
            assert ("departure_scope" in schema["required"]) is attempted
            assert "premature_departure" not in messages[0].content
            assert ("departure_scope" in messages[0].content) is attempted
            result = {"violations": []}
            if attempted:
                assert sources["d0"]["transition_authorized"] is False
                assert "departure_blocker" not in sources["d0"]
                assert data["current_scene_task"] == task
                assert any("本轮留幕回应须按current_scene_task交付" in row["text"]
                           for row in data["author_boundaries"])
                assert "全部正文编号依次为 c0、c1。" in messages[0].content
                scoped = candidates if complete_scope else candidates[:1]
                result.update(departure_scope=[{
                    "scope": "current_scene", "candidate_ref": row["id"], "quote": row["text"],
                } for row in scoped])
            else:
                assert "d0" not in sources and "current_scene_task" not in data
            return SimpleNamespace(content=json.dumps(contract_audit_payload(result, messages), ensure_ascii=False))

    async def model_config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"}

    async def create_client(*args, **kwargs):
        return ContractClient()

    monkeypatch.setattr(evaluator, "_model_config", model_config)
    monkeypatch.setattr(evaluator, "create_chat_llm_async", create_client)
    diagnostics = {}
    # Bypass the unit suite's default audit stub while keeping transport fully offline.
    problems = await _VERIFY_CONTRACT_BOUNDARIES(evaluator.NumericV2MetricEvaluator(object()),
        node=engine.nodes["start"], actor_performance={"performance": body, "suggested_inputs": []},
        player_input=message, session=session, include_all_segments=True, target_node=None,
        exit_mismatch_reason="运行时本轮仍留在当前幕，未授权阶段变化。" if attempted else "",
        departure_blocker=None, departure_attempt_observed=attempted, author_boundaries_only=not attempted,
        current_scene_task=task, diagnostics=diagnostics,
    )
    assert len(calls) == 1
    assert bool(problems) is (attempted and not complete_scope)
    if attempted:
        assert diagnostics["departure_scope"] == ("current_scene" if complete_scope else "uncertain")
    else:
        assert "departure_scope" not in diagnostics
