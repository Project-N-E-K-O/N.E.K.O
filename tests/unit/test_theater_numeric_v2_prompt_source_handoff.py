"""Keep program scope, selected author tasks and player wording in their own sources."""

from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_actor as actor
from services.theater import numeric_v2_workflow as wf
from services.theater.numeric_v2_context import prompt_data_block_payload, render_prompt_context
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_player_statement_authority import (
    _evaluation,
    _frame,
    _ordinary_story,
    narrow_workflow,  # noqa: F401 -- reuse the existing isolated Workflow fixture
)
from tests.unit.test_theater_numeric_v2_progress_ownership import _pending_departure_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.utils.theater_prompt import prompt_payload


TASK = {
    "selected_id": "inspection",
    "direction": "管事当场核验文书的印纹。",
    "allowed_behaviors": ["说明文书的来历，不能代替管事批准通行。"],
    "goals": [
        {"id": "show_document", "owner": "catgirl", "description": "出示文书。",
         "source_ids": ["opening.start"]},
        {"id": "inspection", "owner": "environment", "description": "管事当场核验文书的印纹。",
         "source_ids": ["goal.show_document", "player_input"]},
    ],
    "pending_results": [{"key": "scene:start:done", "description": "文书核验结果已经交付。",
                         "equals": True, "required_this_scene": True}],
}
CLAIM = "我已经到检修间了。"
MESSAGE = "（把手放在桌边）" + CLAIM
TARGET_REFERENCE = "检修间的白色仪表台首次显示最终校准报告。"
LOCAL_REPLY = "（看向桌边的文书）请看这处印纹。"


def _blocks(messages):
    assert len(messages) == 2
    return (prompt_data_block_payload(messages[1].content, "剧本资料"),
            prompt_data_block_payload(messages[1].content, "玩家本轮输入"))


def _assert_source_handoff(messages, task):
    story, player = _blocks(messages)
    assert player == {"player_input": MESSAGE}
    assert story["current_scene_task"] == task
    scope = story["player_input_scope"]
    assert scope["statement_source"] == "player_input" and scope["statement_quote"] == CLAIM
    assert scope["stage_change_authorized"] is False
    assert "source" not in scope and "current_scene_task" not in scope
    assert sum(item.content.count(MESSAGE) for item in messages) == 1
    assert TARGET_REFERENCE not in "\n".join(item.content for item in messages)


def test_program_scope_and_selected_task_are_not_part_of_the_player_block():
    message = "（把手放在桌边）我已经到检修间了。"
    scope = {"statement_source": "player_input", "statement_quote": "我已经到检修间了。",
             "stage_change_authorized": False}
    source = {"current_scene": "当前仍在站台。", "player_input_scope": scope,
              "current_scene_task": deepcopy(TASK), "player_input": message}
    before = deepcopy(source)
    text = render_prompt_context(source)
    player = prompt_data_block_payload(text, "玩家本轮输入")
    story = prompt_data_block_payload(text, "剧本资料")
    assert player == {"player_input": message}
    assert story["player_input_scope"] == scope
    assert story["current_scene_task"] == TASK
    assert source == before


@pytest.mark.parametrize("blocked", [False, True])
@pytest.mark.parametrize("retry", ["", "从当前幕和玩家原话重新写。"])
def test_actor_task_packet_keeps_selected_subject_dependencies_and_scope_outside_player_input(blocked, retry):
    engine, session, outcome = _frame(MESSAGE, blocked=blocked)
    task = deepcopy(TASK)
    messages = actor._turn_messages(engine, session, outcome, MESSAGE, "认真。", "猫娘", "你",
        unconfirmed_departure_quote=CLAIM, current_scene_task=task, progress_direction=task["direction"],
        retry_hint=retry)
    _assert_source_handoff(messages, TASK)
    assert task == TASK
    assert [goal["id"] for goal in task["goals"]] == ["show_document", "inspection"]
    assert task["goals"][1]["source_ids"] == ["goal.show_document", "player_input"]


@pytest.mark.asyncio
async def test_real_generate_turn_passes_same_task_metadata_into_the_actual_invoke(monkeypatch):
    engine, session, outcome = _frame(MESSAGE)
    task = deepcopy(TASK)
    requests = []

    async def invoke(self, messages, **kwargs):
        requests.append((messages, kwargs))
        return {"performance": LOCAL_REPLY, "suggested_inputs": [], "fact_candidates": [],
                "transition_offered": False}

    monkeypatch.setattr(actor.NumericV2Actor, "_invoke", invoke)
    result = await actor.NumericV2Actor(object()).generate_turn(engine=engine, session=session, outcome=outcome,
        player_input=MESSAGE, character_profile="认真。", unconfirmed_departure_quote=CLAIM,
        current_scene_task=task, progress_direction=task["direction"])
    assert len(requests) == 1 and result["performance"] == LOCAL_REPLY
    messages, flags = requests[0]
    _assert_source_handoff(messages, TASK)
    assert flags["suppress_transition_offer"] is True and flags["suppress_suggestions"] is True
    assert task == TASK


@pytest.mark.parametrize("blocked", [False, True])
@pytest.mark.asyncio
async def test_workflow_keeps_same_selected_task_and_audit_only_plan_through_clean_retry(
    tmp_path, monkeypatch, narrow_workflow, blocked,
):
    story = _pending_departure_story() if blocked else _ordinary_story()
    target_id = story["nodes"][0]["route_gates"][0]["target_node_id"]
    next(node for node in story["nodes"] if node["id"] == target_id)["story_beat"]["opening_scene"] = TARGET_REFERENCE
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="source_handoff", catgirl_binding=_binding(),
                                          opening_performance=_opening())
    original = deepcopy(current)
    task = deepcopy(TASK)
    requests, checked, guide_calls = [], [], []
    rejected = "后续阶段的校准已经完成。"

    async def evaluate(self, **kwargs):
        return _evaluation(engine, current.session, MESSAGE, CLAIM)

    async def guide(self, **kwargs):
        guide_calls.append(kwargs)
        assert kwargs["blocked_departure"] is True and kwargs["review_progress"] is True
        return wf.NumericV2StoryEvidenceReview(direction=task["direction"], current_scene_task=task)

    async def invoke(self, messages, **kwargs):
        requests.append((messages, kwargs))
        _assert_source_handoff(messages, TASK)
        assert rejected not in "\n".join(item.content for item in messages)
        return {"performance": rejected if len(requests) == 1 else LOCAL_REPLY,
                "suggested_inputs": [], "fact_candidates": [], "transition_offered": False}

    async def contract(self, **kwargs):
        checked.append(deepcopy(kwargs))
        assert kwargs["current_scene_task"] == TASK
        assert kwargs["target_node"] is None and kwargs["author_boundaries_only"] is False
        assert TARGET_REFERENCE not in kwargs["exit_mismatch_reason"]
        assert "后续阶段方向" not in kwargs["exit_mismatch_reason"]
        if blocked:
            assert kwargs["departure_plan"]["opening_reference"] == TARGET_REFERENCE
            route = engine.nodes["start"]["route_gates"][0]
            assert kwargs["departure_plan"]["transition_direction"] == route["transition_contract"]["reason"]
        else:
            assert not kwargs.get("departure_plan")
        if kwargs["actor_performance"]["performance"] == rejected:
            kwargs["diagnostics"]["findings"] = [{"prior": {"id": "d0"}, "quote": rejected}]
            return ("不能把后续阶段写成已经完成。",)
        return ()

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "review_story_evidence", guide)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", contract)
    monkeypatch.setattr(actor.NumericV2Actor, "_invoke", invoke)
    # Exercise the retained model-selected task adapter; public turns use program scheduling.
    result = await wf._execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("reply", 0, MESSAGE), ensure_current_binding=lambda _: _binding())
    assert len(guide_calls) == 1 and len(requests) == len(checked) == 2
    assert current == original and task == TASK
    assert result.performance["performance"] == LOCAL_REPLY
    assert result.stored.session.current_node_id == "start" and result.stored.session.revision == 1
    committed = json.dumps(result.stored.session.to_dict(), ensure_ascii=False)
    assert rejected not in committed and "selected_id" not in committed and "opening_reference" not in committed
    assert await runtime.restore_session(current.session.session_id) == result.stored


def test_legacy_blocked_direction_stays_a_program_task_without_inventing_a_selected_goal():
    engine, session, outcome = _frame(MESSAGE)
    direction = "检查桌边设备的接线。"
    messages = actor._turn_messages(engine, session, outcome, MESSAGE, "认真。", "猫娘", "你",
        unconfirmed_departure_quote=CLAIM, progress_direction=direction)
    story, player = _blocks(messages)
    assert player == {"player_input": MESSAGE}
    assert story["current_scene_task"]["direction"] == direction
    assert "selected_id" not in story["current_scene_task"]
    assert "current_scene_task" not in story["player_input_scope"]


@pytest.mark.parametrize("message", [
    "（把文书放在桌边）请看看这里。", "如果核验通过了，我们再去检修间，好吗？",
])
def test_ordinary_local_actions_and_future_proposals_keep_raw_input_without_program_scope(message):
    engine, session, outcome = _frame(message, blocked=False)
    messages = actor._turn_messages(engine, session, outcome, message, "认真。", "猫娘", "你")
    story, player = _blocks(messages)
    assert player == {"player_input": message}
    assert "player_input_scope" not in story and "current_scene_task" not in story
    assert "【本轮舞台锁定】" not in messages[0].content


def test_ordinary_soft_progress_remains_a_hint_without_selected_task_metadata():
    message = "请先检查桌边设备。"
    engine, session, outcome = _frame(message, blocked=False)
    direction = "检查眼前接线。"
    messages = actor._turn_messages(engine, session, outcome, message, "认真。", "猫娘", "你",
                                    progress_direction=direction)
    story, player = _blocks(messages)
    assert player == {"player_input": message}
    assert "current_scene_task" not in story and "player_input_scope" not in story
    assert direction in story["pacing"]


@pytest.mark.parametrize("phase", ["transition", "reinvite"])
def test_current_scene_task_is_not_reused_as_a_formal_transition_or_reinvitation_task(phase):
    engine, session, outcome = _frame(MESSAGE if phase == "transition" else "", completed=phase == "transition",
                                      blocked=phase == "transition")
    assert (outcome.route is not None) is (phase == "transition")
    messages = actor._turn_messages(engine, session, outcome, MESSAGE if phase == "transition" else "",
        "认真。", "猫娘", "你", input_source="freeform" if phase == "transition" else "reinvite",
        unconfirmed_departure_quote=CLAIM, current_scene_task=deepcopy(TASK), progress_direction=TASK["direction"])
    assert len(messages) == 2
    data = prompt_payload(messages[1].content)
    assert "current_scene_task" not in data and "player_input_scope" not in data
    assert "selected_id" not in messages[1].content and TASK["direction"] not in messages[1].content
