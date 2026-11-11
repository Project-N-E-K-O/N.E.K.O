"""Completion backfill uses request-bound current-visit delivery, never drafts or author plans."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as wf
from services.theater.numeric_v2_options import default_options
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.utils.theater_prompt import prompt_payload, prompt_reference
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_pending_offer import _commit
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_transition_history import _candidate


NODE = "node_branch_1f40718adf"
KEY = f"scene:{NODE}:branch_complete"
# Actual bread R14 performance and R15 input/candidate: only the evidence source is repaired.
QUOTE = ("既然你也愿意承担那两小时，那我们就按这个边界来执行。\n"
         "我会在活动前三天完成备货，确保五十份甜点的质量，剩下的时间我会留在店里正常经营。")
PERFORMANCE = "（看着张阿姨离去的背影，紧绷的肩膀稍微放松了一些，拿起笔在报名表的供应商栏签下名字）" + QUOTE
MESSAGE = "（把试卷重新摊开）好，我就只负责那两小时的引导，其他时间照常复习。活动安排已经说清楚了，我们回到眼前的试卷吧。"
DESCRIPTION = "双方确认各自的底线：Phone-local只负责提供定额甜点且不参与现场杂务；老谢若参与则仅负责指定时段的引导，不影响复习主线。"
_review_story_evidence = ev.NumericV2MetricEvaluator.review_story_evidence


@pytest.mark.asyncio
async def test_batched_ending_review_binds_original_mixed_performance_and_excludes_bridge(monkeypatch):
    engine = _engine()
    session = _session(engine, "scene_entry")
    requests = []

    async def config(_):
        return {"model": "test", "base_url": "https://test.invalid", "api_key": "test"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def ainvoke(self, messages, **kwargs):
            requests.append(json.loads(messages[1].content))
            return SimpleNamespace(content='{"source_ready":true,"target_ready":true,"approved_indexes":[0,1]}')

    async def factory(*args, **kwargs):
        return Client()

    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    result = await _review_story_evidence(
        ev.NumericV2MetricEvaluator(object()), engine=engine, session=session, message=MESSAGE,
        claims=(_fact(quote=PERFORMANCE), _fact(quote="两人走进店里。")),
        ending_route={"target_node_id": "ending_leave", "transition_contract": {}}, timeout_seconds=1,
    )
    assert result == ev.NumericV2StoryEvidenceReview((0,), True)
    assert len(requests) == 1
    assert requests[0]["scene_context"][-1]["content"] == [PERFORMANCE]


@pytest.mark.asyncio
@pytest.mark.parametrize("quote_kind", ["original", "projected", "joined_fields"])
async def test_historical_quote_keeps_original_representation_through_fact_review(monkeypatch, quote_kind):
    engine = _engine()
    engine.fact_contract[KEY].update(description="角色已按下按钮并现场展示仪表读数。", evidence_source="performance")
    original = "（按下按钮）读数升至20。"
    projected = "按下按钮\n读数升至20。"
    assert projected not in original
    session = _session(engine)
    row = {**session.performance_history[-1], "scene_narration": "", "performance": original}
    if quote_kind == "joined_fields":
        row["scene_narration"] = "仪表亮起。"
    session = replace(session, performance_history=(row,))
    # A projection of action/dialogue is transport data, not a verbatim quotation of the performed original.
    monkeypatch.setattr(ev, "history_evidence", lambda *args, **kwargs: [])
    bank = []
    ev._build_messages(engine, session, MESSAGE, committed_evidence=bank)
    quote = {"original": original, "projected": projected, "joined_fields": "仪表亮起。\n" + original}[quote_kind]
    candidate = _fact(quote=quote)
    candidate.update(subject="角色", action="按下按钮", object="仪表", result="读数已呈现")
    candidate["evidence"] = candidate["evidence"][:1]
    initial = ev._parse_output(json.dumps({"scene_complete": False, "metric_changes": {},
        "fact_candidates": [candidate]}, ensure_ascii=False), engine, MESSAGE, session, committed_evidence=bank)

    async def config(_):
        return {"model": "test", "base_url": "https://test.invalid", "api_key": "test"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def ainvoke(self, messages, **kwargs):
            request = json.loads(messages[1].content)
            assert request["scene_context"][-1]["content"] == (["仪表亮起。", original] if row["scene_narration"] else [original])
            assert request["claims"][0]["evidence"][0]["quote"] == quote
            return SimpleNamespace(content='{"approved_indexes":[0]}')

    async def factory(*args, **kwargs):
        return Client()

    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    reviewed = await _review_story_evidence(ev.NumericV2MetricEvaluator(object()), engine=engine,
        session=session, message=MESSAGE, claims=({**candidate, "description": engine.fact_contract[KEY]["description"]},),
        timeout_seconds=1)
    observed = (bool(initial.fact_operations), reviewed.approved_indexes == (0,))
    # Both gates must accept the same literal source. Neither may elevate the synthetic projection to an original.
    assert observed == (quote_kind == "original", quote_kind == "original")


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [
    None, "show_reading", "player_checks", "视觉体验已经完成。", "", "legacy", "player_only", "player_only_fact",
])
async def test_progress_review_receives_published_dialogue_and_returns_advice_without_authority(monkeypatch, selected):
    blocked_departure = selected is not None
    engine = _engine()
    permission = "可调整眼前设备，现场展示读数变化。"
    engine.nodes[NODE]["story_beat"].setdefault("acting_contract", {})["allowed_behaviors"] = [permission]
    goals = [
        {"id": "show_reading", "owner": "environment", "description": "仪表呈现读数变化。",
         "delivery": {"source_ids": ["opening.device"], "output_field": "scene_update"}},
        {"id": "player_checks", "owner": "player", "description": "玩家观察仪表并反馈。",
         "delivery": {"source_ids": ["goal.show_reading"], "output_field": "player_input"}},
    ]
    if selected in {"player_only", "player_only_fact"}:
        goals = goals[1:]
    engine.nodes[NODE]["story_beat"]["goals"] = goals
    if selected == "legacy":
        engine.nodes[NODE]["story_beat"].pop("goals")
    session = _session(engine, "older_scene_entry")
    requests = []
    reply_direction = "承接已经发生的动作，回应本轮请求。"
    if blocked_departure:
        reply_direction = selected
    if selected in {"legacy", "player_only_fact"}:
        reply_direction = KEY
    elif selected == "player_only":
        reply_direction = ""

    async def config(_):
        return {"model": "test", "base_url": "https://test.invalid", "api_key": "test"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def ainvoke(self, messages, **kwargs):
            requests.append(json.loads(messages[1].content))
            return SimpleNamespace(content=json.dumps({
                "direction": reply_direction,
            }, ensure_ascii=False))

    async def factory(*args, **kwargs):
        return Client()

    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    kwargs = {"engine": engine, "session": session, "message": MESSAGE,
              "claims": (), "review_progress": True, "blocked_departure": blocked_departure, "timeout_seconds": 1}
    reviewer = ev.NumericV2MetricEvaluator(object())
    if selected in {"player_checks", "视觉体验已经完成。", "player_only_fact"}:
        with pytest.raises(ev.NumericV2EvaluatorError, match="numeric_v2_story_evidence_failed"):
            await _review_story_evidence(reviewer, **kwargs)
        return
    result = await _review_story_evidence(reviewer, **kwargs)
    request, = requests
    assert [row["revision"] for row in request["scene_context"]] == [14, 15]
    assert request["scene_context"][0]["player_input"] == ""
    assert request["scene_context"][1]["player_input"] == "我们继续看眼前的试卷。"
    assert "我先拿一支笔。" in str(request["scene_context"][1]["content"])
    if blocked_departure:
        assert {"player_input", "entry_cause", "exit_plan", "earlier_evidence"}.isdisjoint(request)
        assert request["progress"]["allowed_behaviors"] == [permission]
        assert request["progress"]["goals"] == ([] if selected == "legacy" else [
            {"id": goal["id"], "owner": goal["owner"], "description": goal["description"],
             "source_ids": goal["delivery"]["source_ids"]} for goal in goals
        ])
        expected_ids = [] if selected == "player_only" else ["show_reading"]
        if selected == "legacy":
            expected_ids = [KEY]
        assert request["progress"]["direction_ids"] == expected_ids
        expected = request["progress"]["pending_results"][0]["description"] if selected == "legacy" else (
            goals[0]["description"] if selected == "show_reading" else "")
    else:
        assert request["player_input"] == MESSAGE
        expected = "承接已经发生的动作，回应本轮请求。"
    expected_task = None
    if blocked_departure and reply_direction:
        expected_task = {
            "selected_id": reply_direction, "direction": expected,
            "goals": request["progress"]["goals"],
            "allowed_behaviors": [permission],
            "pending_results": [row for row in request["progress"]["pending_results"] if row["required_this_scene"]],
        }
    assert result == ev.NumericV2StoryEvidenceReview(direction=expected, current_scene_task=expected_task)
    assert request["progress"]["pending_results"][0]["key"] == KEY


@pytest.mark.asyncio
@pytest.mark.parametrize("decision,quote,expected", [
    ("accept", "好，我这就照做。", "accept"),
    ("accept", "未说过的同意", "hold"),
    ("reject", "我拒绝这个安排。", "reject"),
    ("obsolete", "我先拿一支笔。", "obsolete"),
    ("obsolete", "两人走进店里。", "hold"),
    ("obsolete", "活动的安排还要商量。", "hold"),
])
async def test_invitation_review_requires_evidence_from_the_correct_source(monkeypatch, decision, quote, expected):
    engine = _engine()
    session = _session(engine, "older_scene_entry")

    async def config(_):
        return {"model": "test", "base_url": "https://test.invalid", "api_key": "test"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def ainvoke(self, messages, **kwargs):
            request = json.loads(messages[1].content)
            assert request["pending_offer_revision"] == 14
            assert request["scene_context"][-1]["revision"] == 15
            return SimpleNamespace(content=json.dumps({"offer_decision": decision,
                "offer_quote": quote, "offer_reason": "依据实际原文核对。"}))

    async def factory(*args, **kwargs):
        return Client()

    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    result = await _review_story_evidence(
        ev.NumericV2MetricEvaluator(object()), engine=engine, session=session,
        message="我拒绝这个安排。" if decision == "reject" else "好，我这就照做。",
        claims=(), pending_offer="现在开始吗？", pending_offer_revision=14, timeout_seconds=1,
    )
    assert result.offer_decision == expected
    assert result.offer_quote == (quote if expected != "hold" else "")


def _engine():
    story = numeric_v2_story()
    story["start_node_id"] = story["nodes"][0]["id"] = NODE
    story["fact_contract"] = {"facts": {
        KEY: {"value_type": "bool", "visibility": "public", "description": DESCRIPTION},
        f"scene:{NODE}:other": {"value_type": "bool", "visibility": "public", "description": "另一结果。"},
        "scene:ending_leave:done": {"value_type": "bool", "visibility": "public", "description": "后幕结果。"},
        "prop:ready": {"value_type": "bool", "visibility": "public", "description": "另一事实。"},
    }}
    story["nodes"][0]["completion_contract"] = {"all": [{"key": KEY, "equals": True}]}
    return NumericV2Engine.from_mapping(story)


def _session(engine, phase="turn"):
    if phase.startswith("older_"):
        earlier = _session(engine, phase.removeprefix("older_"))
        revision = earlier.revision + 1
        row = {"revision": revision, "from_node_id": NODE, "to_node_id": NODE,
            "input_text": "我们继续看眼前的试卷。", "performance": "我先拿一支笔。"}
        return replace(earlier, revision=revision,
            story_state={**earlier.story_state, "revision": revision},
            node_turn_count=earlier.node_turn_count + 1,
            performance_history=(*earlier.performance_history, row))
    base = engine.create_session(session_id="committed_evidence", catgirl_binding=_binding(),
        opening_performance={"performance": "活动的安排还要商量。", "suggested_inputs": []})
    if phase == "opening":
        return replace(base, opening_performance={"performance": PERFORMANCE})
    row = {
        "revision": 14, "from_node_id": NODE, "to_node_id": NODE,
        "input_text": "玩家旧输入不能借历史演出来源提交。", "performance": PERFORMANCE,
        "scene_narration": "张阿姨收起了随身的文件夹。",
    }
    if phase == "scene_entry":
        # 入幕证明只能取已交付目标段；来源回应和桥段都故意使用不同原文。
        row = {"revision": 14, "from_node_id": "other", "to_node_id": NODE,
            "input_text": "玩家旧输入不能借历史演出来源提交。",
            "segments": [{"phase": "source_response", "performance": "那我们进去吧。"},
                {"phase": "transition_bridge", "scene_narration": "两人走进店里。"},
                {"phase": "target_opening", "performance": PERFORMANCE}]}
    return replace(base, revision=14, story_state={**base.story_state, "revision": 14},
        node_turn_count=2, performance_history=(row,))


def _fact(source="actor_performance", quote=QUOTE):
    return {"op": "set", "key": KEY, "value": True, "visibility": "public", "confidence": "confirmed",
        "subject": "双方", "action": "确认协作边界", "object": "活动参与责任",
        "result": "Phone-local只提供定额甜点，老谢仅负责两小时引导并回归复习",
        "evidence": [{"source": source, "quote": quote},
            {"source": "player_input", "quote": MESSAGE.removeprefix("（把试卷重新摊开）")}]}


def _parse(engine, session, candidate=None):
    # Parser 只能核验这次请求真正保留的原文，不能另扫完整 Session 放宽证据。
    bank = []
    ev._build_messages(engine, session, MESSAGE, committed_evidence=bank)
    return ev._parse_output(json.dumps({"scene_complete": False, "metric_changes": {},
        "fact_candidates": [_fact() if candidate is None else candidate]}, ensure_ascii=False),
        engine, MESSAGE, session, committed_evidence=bank)


def _data(messages):
    return prompt_payload(messages[1].content)


@pytest.mark.parametrize("value_type,target,stored", [
    ("bool", True, False), ("bool", False, True), ("int", 3, 2), ("string", "confirmed", "pending"),
])
@pytest.mark.parametrize("satisfied", [False, True])
def test_existing_fact_history_is_bound_until_its_typed_target_is_satisfied(value_type, target, stored, satisfied):
    engine = _engine()
    engine.fact_contract[KEY]["value_type"] = value_type
    engine.nodes[NODE]["completion_contract"]["all"] = [{"key": KEY, "equals": target}]
    session = _session(engine)
    session = replace(session, story_state={**session.story_state,
        "facts": {KEY: {"value": target if satisfied else stored, "visibility": "public"}}})
    before = deepcopy(session)
    metadata, originals = ev._committed_completion_evidence(engine, session)
    assert (KEY in metadata.get("allowed_keys", ())) is not satisfied
    assert (PERFORMANCE in originals) is not satisfied
    assert session == before


def test_existing_false_fact_can_use_verbatim_history_without_becoming_approved():
    engine = _engine()
    session = _session(engine)
    session = replace(session, story_state={**session.story_state,
        "facts": {KEY: {"value": False, "visibility": "public"}}})
    result = _parse(engine, session)
    assert result.fact_operations == ({"op": "set", "key": KEY, "value": True, "visibility": "public"},)
    assert result.fact_audit[0]["evidence"][0]["quote"] == QUOTE
    assert session.story_state["facts"][KEY]["value"] is False


@pytest.mark.parametrize("value_type,target,stored,value,accepted", [
    ("int", 3, 2, 3, True), ("int", 3, 2, 4, False),
    ("string", "confirmed", "pending", "confirmed", True),
    ("string", "confirmed", "pending", "different", False),
    ("bool", True, False, 1, False), ("int", 1, 0, True, False),
])
def test_existing_history_candidate_cannot_escape_its_typed_pending_value(value_type, target, stored, value, accepted):
    engine = _engine()
    engine.fact_contract[KEY]["value_type"] = value_type
    engine.nodes[NODE]["completion_contract"]["all"] = [{"key": KEY, "equals": target}]
    session = _session(engine)
    session = replace(session, story_state={**session.story_state,
        "facts": {KEY: {"value": stored, "visibility": "public"}}})
    candidate = {**_fact(), "value": value}
    result = _parse(engine, session, candidate)
    assert bool(result.fact_operations) is accepted
    if accepted:
        assert type(result.fact_operations[0]["value"]) is type(target)
        assert result.fact_operations[0]["value"] == target


@pytest.mark.parametrize("retain_history", [False, True])
def test_evaluator_performance_fact_source_matches_retained_originals(retain_history):
    engine = _engine()
    engine.fact_contract[KEY]["evidence_source"] = "performance"
    session = _session(engine)
    if not retain_history:
        session = replace(session, performance_history=())
    messages = ev._build_messages(engine, session, MESSAGE)
    system = messages[0].content
    data = _data(messages)
    beat = data["current_story_beat"]
    assert system.count("evidence.source 仅使用 fact_candidate_sources 列出的值") == 1
    assert "history_evidence 的 performance 是检索标签，不能填入 evidence.source" in system
    assert ("actor_performance" in beat["fact_candidate_sources"]) is retain_history
    assert ("committed_fact_evidence" in data) is retain_history
    assert (KEY in beat["fact_contract"]["facts"]) is retain_history
    assert "prop:ready" in beat["fact_contract"]["facts"]
    assert "player_input" in beat["fact_candidate_sources"]
    assert "标注 evidence_source=performance 的事实只用此来源" in system


def test_ending_request_lists_no_sources_for_already_recorded_performance_only_fact():
    engine = _engine()
    engine.fact_contract[KEY]["evidence_source"] = "performance"
    for key in set(engine.fact_contract) - {KEY}:
        del engine.fact_contract[key]
    session = _session(engine)
    session = replace(session, story_state={**session.story_state, "facts": {
        KEY: {"value": True, "visibility": "public"},
    }})
    messages = ev._build_messages(engine, session, "已经放稳了。帮我拍一张吧。")
    beat = _data(messages)["current_story_beat"]
    assert beat["fact_contract"]["facts"] == {}
    assert beat["fact_candidate_sources"] == []
    assert beat["committed_fact_values"] == {KEY: True}
    assert "玩家请角色执行收尾动作时，只写已获授权待演" in messages[0].content


@pytest.mark.parametrize("description,quote,accepted", [
    (
        "小满在近处长桌上用折页夹将选定拓样实际装订成私人册并捧在手中",
        "（低头把缺角猫和鱼骨那两张对齐叠好，用折页夹稳稳压住边角）去吧，我在这儿守着。"
        "（抬头瞥了一眼空荡荡的院子入口，手指无意识地敲了敲桌面）别太急，纸我都理好了，就等你回来装订。",
        False,
    ),
    ("工作人员已签收机器。", "机器放在台上了，等你到场后再签收。", False),
    ("两人约定等玩家回来装订成册。", "纸我都理好了，就等你回来装订。", True),
    ("小满实际装订成私人册并捧在手中。", "我已装订好这一本，就等你回来再装订。", True),
    ("工作人员已签收机器。", "机器已签收。就等你回来装订。", True),
])
def test_historical_waiting_for_target_action_is_not_completed_evidence(description, quote, accepted):
    engine = _engine()
    engine.fact_contract[KEY]["description"] = description
    session = engine.create_session(session_id="history-wait", catgirl_binding=_binding(),
                                    opening_performance={"performance": quote})
    candidate = _fact(quote=quote)
    candidate["evidence"] = candidate["evidence"][:1]
    result = _parse(engine, session, candidate)
    assert bool(result.fact_operations) is accepted


@pytest.mark.parametrize("quote,accepted", [
    ("双方在报名表上签字。", True),
    ("桌上有报名表。", False),
])
def test_author_evidence_anchor_must_be_in_the_historical_quote_itself(quote, accepted):
    engine = _engine()
    engine.fact_contract[KEY].update(evidence_source="performance", evidence_anchor="签字")
    session = engine.create_session(session_id="anchor-history", catgirl_binding=_binding(),
                                    opening_performance={"performance": "桌上有报名表。双方在报名表上签字。"})
    originals = []
    system, human = ev._build_messages(engine, session, MESSAGE, committed_evidence=originals)
    assert ev._pending_completion_facts(engine, session)[0]["evidence_anchor"] == "签字"
    fact = prompt_payload(human.content)["current_story_beat"]["fact_contract"]["facts"][KEY]
    assert fact["evidence_anchor"] == "签字"
    assert "同一实际结果引文须含完整短语" in system.content
    candidate = _fact(quote=quote)
    candidate["evidence"] = candidate["evidence"][:1]
    result = ev._parse_output(json.dumps({"scene_complete": False, "metric_changes": {},
        "fact_candidates": [candidate]}), engine, MESSAGE, session, committed_evidence=originals)
    assert bool(result.fact_operations) is accepted


def _reference_request(engine, session):
    """Bind model references to the same final request as the legacy originals."""
    originals, references = [], {}
    messages = ev._build_messages(engine, session, MESSAGE,
        committed_evidence=originals, fact_evidence_refs=references)
    return messages, originals, references


def _reference_id(references, fragment):
    # IDs are request-local transport data, not part of the persisted fact contract.
    matches = [reference for reference, row in references.items() if fragment in row["quote"]]
    assert matches
    # A recent record and a retained retrieval unit may legitimately contain the same original.
    return min(matches, key=lambda reference: len(references[reference]["quote"]))


def _parse_references(engine, session, candidates, originals, references):
    return ev._parse_output(json.dumps({"scene_complete": False, "metric_changes": {},
        "fact_candidates": candidates}, ensure_ascii=False), engine, MESSAGE, session,
        committed_evidence=originals, fact_evidence_refs=references)


def _assert_bound_sources(data, bank):
    """Read originals through final references instead of assuming all evidence is recent history."""
    binding = data["committed_fact_evidence"]
    assert set(binding) == {"source", "content_refs", "allowed_keys"}
    assert binding["source"] == "actor_performance" and binding["allowed_keys"] == [KEY]
    referenced = []
    for reference in binding["content_refs"]:
        collection, suffix = reference.split("[", 1)
        index, field = suffix.split("].", 1)
        row = data[collection][int(index)]
        if collection == "scene_context":
            assert field.startswith("fact_originals[") or (field.startswith("content[") and field.endswith("].text"))
            referenced.append(prompt_reference(data, reference))
        else:
            assert collection == "history_evidence" and field == "text"
            assert row["source"] == "performance" and row["current_visit"] is True
            referenced.append(row[field])
    assert bank == referenced


@pytest.mark.parametrize("text,fragment", [
    # D1/D4 really failed because a correct result was paraphrased or spliced with ellipses.
    ("姚师傅将录音机放进接收架，在签收单上签字并把副联交给林舟："
     "“录音机我收到了，今天你们可以进工作间取回执。”"
     "柜台记录员说：“费用由馆方承担，你们不用垫钱。”", "录音机我收到了"),
    ("姚师傅把录音机放进接收架，在签收单上签字，把副联交给林舟："
     "“录音机已经签收。今天你们可以进工作间。”"
     "柜台记录员按住登记本：“费用归属还没谈妥，这个工具箱也还在你们手里，没有归还点收。”",
     "录音机已经签收"),
    ("值班员把回执递来：‘接收已办好🙂！’然后转身整理台账。", "接收已办好🙂"),
])
def test_request_reference_repairs_copy_failure_without_changing_canonical_evidence(text, fragment):
    engine = _engine()
    session = _session(engine, "opening")
    session = replace(session, opening_performance={"performance": "我先收好副联。", "scene_narration": text})
    original = deepcopy(session)
    # A numbered source replaces copying; it does not legalize the old fabricated quotation.
    broken = {**_fact(), "evidence": [{"source": "actor_performance", "quote": fragment + "……副联交给林舟。"}]}
    assert _parse(engine, session, broken).fact_operations == ()
    messages, originals, references = _reference_request(engine, session)
    assert "提议不代替同意，双方选定须实际确认" in messages[0].content
    reference = _reference_id(references, fragment)
    data = _data(messages)
    prefix, location = next((prefix, location) for prefix, location in data["fact_evidence_refs"].items()
                        if reference.startswith(prefix))
    assert reference in location["ids"]
    assert location["revision"] == 0
    assert "h编号不是回合号" in messages[0].content
    source = prompt_reference(data, location["path"])
    assert references[reference]["quote"] == ev._fact_evidence_units(source)[int(reference.removeprefix(prefix))]
    assert references[reference]["quote"] not in json.dumps(data["fact_evidence_refs"], ensure_ascii=False)
    candidate = {**_fact(), "evidence": [{"ref": reference}]}
    result = _parse_references(engine, session, [candidate], originals, references)
    assert result.fact_operations == ({"op": "set", "key": KEY, "value": True, "visibility": "public"},)
    assert result.fact_audit[0]["evidence"] == [references[reference]]
    assert references[reference]["quote"] in text
    assert "ref" not in result.fact_audit[0]["evidence"][0]
    assert session == original


def test_mixed_history_references_keep_true_results_separate_from_incomplete_work():
    story = deepcopy(_engine().story)
    definitions = story["fact_contract"]["facts"]
    keys = [KEY, f"scene:{NODE}:entry_permitted", f"scene:{NODE}:fee_agreed", f"scene:{NODE}:tools_returned"]
    for key, description in zip(keys, ("录音机已经签收。", "今天准许进入工作间。", "双方已谈妥费用归属。", "借具已归还点收。")):
        definitions[key] = {"value_type": "bool", "visibility": "public", "description": description}
    story["nodes"][0]["completion_contract"] = {"all": [{"key": key, "equals": True} for key in keys]}
    engine = NumericV2Engine.from_mapping(story)
    text = ("姚师傅把副联交给林舟：“录音机已经签收。今天你们可以进工作间。”"
            "记录员按住登记本：“费用归属还没谈妥。工具箱也尚未归还点收。”")
    session = replace(_session(engine, "opening"),
        opening_performance={"performance": "按眼前实际办好的手续来。", "scene_narration": text})
    _, originals, references = _reference_request(engine, session)
    candidates = [{**_fact(), "key": key, "evidence": [{"ref": _reference_id(references, fragment)}]}
        for key, fragment in zip(keys, ("录音机已经签收", "今天你们可以进工作间", "费用归属还没谈妥", "工具箱也尚未归还"))]
    result = _parse_references(engine, session, candidates, originals, references)
    assert {operation["key"] for operation in result.fact_operations} == set(keys[:2])
    assert all("还没" not in row["evidence"][0]["quote"] and "尚未" not in row["evidence"][0]["quote"]
        for row in result.fact_audit)
    # Splitting for transport must not remove the full mixed original needed by semantic Review.
    assert any(text in original for original in originals)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["separate_refs", "joined_quote", "unknown_ref", "old_visit", "wrong_source"])
async def test_multi_turn_completion_preserves_each_reference_through_native_review(monkeypatch, case):
    engine = _engine()
    description = "值班员已将保险绳和护目镜交由同伴使用。"
    engine.fact_contract[KEY].update(description=description, evidence_source="performance")
    delivered = ["值班员将保险绳交给同伴，同伴接住并系好。", "值班员把护目镜递给同伴，同伴接过戴好。"]
    session = _session(engine, "older_turn")
    session = replace(session, performance_history=tuple(
        {**row, "scene_narration": "", "performance": text}
        for row, text in zip(session.performance_history, delivered)
    ))
    _, originals, references = _reference_request(engine, session)
    ids = [_reference_id(references, text) for text in delivered]
    evidence = [dict(references[reference]) for reference in ids]
    candidate = {**_fact(), "subject": "值班员", "action": "交付", "object": "保险绳和护目镜",
                 "result": description, "evidence": [{"ref": reference} for reference in ids]}
    if case == "joined_quote":
        candidate["evidence"] = [{"source": "actor_performance", "quote": "\n".join(delivered)}]
    elif case == "unknown_ref":
        candidate["evidence"][-1] = {"ref": "never-issued-in-this-request"}
    elif case == "wrong_source":
        candidate["evidence"][-1] = {"source": "player_input", "quote": delivered[-1]}
    elif case == "old_visit":
        entry = {"revision": 17, "from_node_id": "other", "to_node_id": NODE,
            "segments": [{"phase": "target_opening", "performance": "本次只核对站位，尚未交付设备。"}]}
        session = replace(session, revision=17, story_state={**session.story_state, "revision": 17},
            node_turn_count=0, performance_history=(*session.performance_history,
                {"revision": 16, "from_node_id": NODE, "to_node_id": "other", "performance": "离开工作间。"}, entry))
        _, originals, references = _reference_request(engine, session)
        assert all(text not in original for text in delivered for original in originals)
        # Even a stale reference map cannot restore originals excluded from the current visit.
        references.update({f"stale-{index}": item for index, item in enumerate(evidence)})
        candidate["evidence"] = [{"ref": f"stale-{index}"} for index in range(len(evidence))]
    original = deepcopy(session)
    parsed = _parse_references(engine, session, [candidate], originals, references)
    if case != "separate_refs":
        assert parsed.fact_operations == ()
        assert parsed.fact_audit == ()
        assert session == original
        return
    assert parsed.fact_audit[0]["evidence"] == evidence
    assert parsed.fact_operations == ({"op": "set", "key": KEY, "value": True, "visibility": "public"},)
    requests = []

    async def config(_):
        return {"model": "offline", "base_url": "https://test.invalid", "api_key": "test"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages, **kwargs):
            request = json.loads(messages[1].content)
            requests.append(request)
            assert request["actor_evidence"] == ""
            assert request["claims"][0]["evidence"] == evidence
            history = [text for row in request["scene_context"] for text in row["content"]]
            assert all(text in history for text in delivered)
            assert "\n".join(delivered) not in history
            # Approval is stubbed only to verify transport/provenance, not semantic sufficiency.
            return SimpleNamespace(content='{"approved_indexes":[0]}')

    async def factory(*args, **kwargs):
        return Client()

    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    claims = tuple({**audit, **operation, "description": description}
                   for operation, audit in zip(parsed.fact_operations, parsed.fact_audit))
    reviewed = await _review_story_evidence(ev.NumericV2MetricEvaluator(object()), engine=engine,
        session=session, message=MESSAGE, claims=claims, timeout_seconds=1)
    assert reviewed.approved_indexes == (0,)
    assert len(requests) == 1
    assert parsed.fact_audit[0]["evidence"] == evidence
    assert session == original


@pytest.mark.parametrize("invalid", ["unknown", "mixed", "non_target", "other_scene", "global"])
def test_invalid_reference_rejects_only_its_candidate_and_does_not_grant_history_permissions(invalid):
    story = deepcopy(_engine().story)
    story["fact_contract"]["facts"]["prop:independent"] = {
        "value_type": "bool", "visibility": "public", "description": "玩家确认自己的当前选择。"}
    engine = NumericV2Engine.from_mapping(story)
    session = _session(engine)
    _, originals, references = _reference_request(engine, session)
    reference = _reference_id(references, "既然你也愿意承担那两小时")
    candidate = {**_fact(), "evidence": [{"ref": reference}]}
    if invalid == "unknown":
        candidate["evidence"] = [{"ref": "never-emitted-in-this-request"}]
    elif invalid == "mixed":
        candidate["evidence"][0].update(source="actor_performance", quote=QUOTE)
    else:
        candidate["key"] = {"non_target": f"scene:{NODE}:other", "other_scene": "scene:ending_leave:done",
            "global": "prop:ready"}[invalid]
    # An unrelated legal current-input fact survives a damaged optional candidate.
    independent = {**_fact(), "key": "prop:independent", "evidence": [{"source": "player_input", "quote": MESSAGE}]}
    result = _parse_references(engine, session, [candidate, independent], originals, references)
    assert result.fact_operations == ({"op": "set", "key": "prop:independent", "value": True, "visibility": "public"},)


@pytest.mark.parametrize("excluded", ["old_visit", "trimmed_original"])
def test_reference_table_never_recovers_sources_excluded_from_the_final_request(excluded):
    engine = _engine()
    session = _session(engine, "older_turn")
    earlier, latest = session.performance_history
    if excluded == "trimmed_original":
        earlier = {**earlier, "performance": "完整的背景记录。" * 2000 + PERFORMANCE}
        session = replace(session, performance_history=(earlier, latest))
    else:
        entry = {"revision": 15, "from_node_id": "other", "to_node_id": NODE,
            "segments": [{"phase": "source_response", "performance": PERFORMANCE},
                {"phase": "transition_bridge", "scene_narration": "重新走进店里。"},
                {"phase": "target_opening", "performance": "这次先看看新的试卷。"}]}
        session = replace(session, performance_history=({**earlier, "revision": 13},
            {"revision": 14, "from_node_id": NODE, "to_node_id": "other", "performance": "先离开店里。"}, entry))
    _, originals, references = _reference_request(engine, session)
    assert not any(QUOTE in text for text in originals)
    assert not any("既然你也愿意承担那两小时" in row["quote"] for row in references.values())
    # Even an old request's real map must not supersede this request's retained originals.
    old_reference = "previous-request-original"
    forged = {**references, old_reference: {"source": "actor_performance", "quote": QUOTE}}
    result = _parse_references(engine, session, [{**_fact(), "evidence": [{"ref": old_reference}]}], originals, forged)
    assert result.fact_operations == ()


def test_registered_history_reference_cannot_create_an_unmapped_route_value():
    story = deepcopy(_engine().story)
    story["nodes"][0].pop("completion_contract")
    story["fact_contract"]["facts"][KEY].update(value_type="string", description="玩家明确选定的交接方式。")
    for route, value in zip(story["nodes"][0]["route_gates"], ("library", "teacher")):
        route["conditions"] = {"all": [{"type": "fact_equals", "key": KEY, "value": value}]}
    engine = NumericV2Engine.from_mapping(story)
    session = _session(engine)
    _, originals, references = _reference_request(engine, session)
    reference = _reference_id(references, "既然你也愿意承担那两小时")
    candidate = {**_fact(), "value": "unmapped_choice", "evidence": [{"ref": reference}]}
    assert _parse_references(engine, session, [candidate], originals, references).fact_operations == ()


@pytest.mark.parametrize("keep_player_reference", [False, True])
def test_reference_tables_use_only_spare_budget_and_clear_reused_request_banks(monkeypatch, keep_player_reference):
    from services.theater.numeric_v2_budget import NUMERIC_V2_ACTOR_BUDGET_PROFILES

    engine = _engine()
    session = _session(engine)
    baseline_originals = []
    baseline = ev._build_messages(engine, session, MESSAGE, committed_evidence=baseline_originals)
    data = _data(baseline)
    player_reference = data.pop("fact_evidence_refs", {}).get("p")
    if keep_player_reference:
        data["fact_evidence_refs"] = {"p": player_reference}
    human_prefix = baseline[1].content.split("======以下为", 1)[0]
    baseline_contents = [baseline[0].content,
        human_prefix + ev.render_prompt_context(data)]
    token_limit = sum(ev.count_tokens(content) for content in baseline_contents)
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES[session.actor_budget_profile],
        "evaluator_input_max_tokens", token_limit)
    originals = []
    references = {"stale": {"source": "actor_performance", "quote": "旧请求原文。"}}
    messages = ev._build_messages(engine, session, MESSAGE,
        committed_evidence=originals, fact_evidence_refs=references)
    assert [message.content for message in messages] == baseline_contents
    assert originals == baseline_originals
    assert "stale" not in references
    assert bool(references) is keep_player_reference
    assert all(reference.startswith("p") for reference in references)
    assert _data(messages).get("fact_evidence_refs") == data.get("fact_evidence_refs")
    assert "先按revision和path找到实际证据，再从该组ids选" in messages[0].content
    assert "不自行拼编号" in messages[0].content
    assert "不自行拼编号或用省略号拼引文" in messages[0].content
    assert "h1.0" not in messages[0].content


def test_runtime_fact_quotes_use_the_projection_in_the_final_request(monkeypatch):
    from services.theater.numeric_v2_budget import NUMERIC_V2_ACTOR_BUDGET_PROFILES

    engine = _engine()
    session = _session(engine, "opening")
    engine.nodes[session.current_node_id]["story_beat"]["narrative_summary"] = "按证据办理。"
    monkeypatch.setattr(ev, "history_evidence", lambda *args, **kwargs: [])
    runtime_evidence = []
    messages = ev._build_messages(engine, session, MESSAGE, runtime_fact_evidence=runtime_evidence)
    payload = _data(messages)
    projection = payload["current_story_beat"]["runtime_scene_facts"]
    quote = projection["facts"][0]["key"]
    candidate = {**_fact(), "evidence": [{"source": "runtime_fact", "quote": quote}]}
    response = json.dumps({"scene_complete": False, "metric_changes": {}, "fact_candidates": [candidate]})
    assert ev._parse_output(response, engine, MESSAGE, session,
                            runtime_fact_evidence=runtime_evidence[0]).fact_operations

    payload.pop("fact_evidence_refs", None)
    payload["current_story_beat"]["runtime_scene_facts"] = {"truncated": True, "scope": "omitted_for_capacity"}
    prefix = messages[1].content.split("======以下为", 1)[0]
    limit = ev.count_tokens(messages[0].content) + ev.count_tokens(prefix + ev.render_prompt_context(payload))
    monkeypatch.setitem(NUMERIC_V2_ACTOR_BUDGET_PROFILES[session.actor_budget_profile],
                        "evaluator_input_max_tokens", limit)
    messages = ev._build_messages(engine, session, MESSAGE, runtime_fact_evidence=runtime_evidence)
    assert quote not in messages[1].content and runtime_evidence == [""]
    assert ev._parse_output(response, engine, MESSAGE, session,
                            runtime_fact_evidence=runtime_evidence[0]).fact_operations == ()


@pytest.mark.parametrize("phase", ["turn", "opening", "scene_entry",
    "older_turn", "older_opening", "older_scene_entry"])
def test_real_r15_exact_history_quote_can_backfill_without_reperforming(phase):
    engine = _engine()
    session = _session(engine, phase)
    original = deepcopy(session)
    result = _parse(engine, session)
    assert result.fact_operations == ({"op": "set", "key": KEY, "value": True, "visibility": "public"},)
    assert result.fact_audit[0]["evidence"] == _fact()["evidence"]
    assert session == original


@pytest.mark.parametrize("source", ["performance", "runtime_scene_facts", "history", "scene_context"])
def test_unknown_sources_are_never_guessed_as_committed_history(source):
    engine = _engine()
    assert _parse(engine, _session(engine), _fact(source)).fact_operations == ()


@pytest.mark.parametrize("case", [
    "valid", "performance", "runtime_fact", "unregistered", "other_scene", "missing_bank",
])
def test_route_only_history_permission_is_limited_to_current_registered_targets(case):
    story = deepcopy(_engine().story)
    story["nodes"][0].pop("completion_contract")
    for route in story["nodes"][0]["route_gates"]:
        route["conditions"]["all"].append({"type": "fact_equals", "key": KEY, "value": True})
    engine = NumericV2Engine.from_mapping(story)
    session = _session(engine)
    candidate = _fact()
    if case in {"performance", "runtime_fact"}:
        # The first is an unknown source; the second is legal only for the
        # actual runtime JSON, never an alias for a previously spoken NPC line.
        candidate["evidence"][0]["source"] = case
    elif case in {"unregistered", "other_scene"}:
        candidate["key"] = f"scene:{NODE}:other" if case == "unregistered" else "scene:ending_leave:done"
    bank = []
    messages = ev._build_messages(engine, session, MESSAGE, committed_evidence=bank)
    _assert_bound_sources(_data(messages), bank)
    result = ev._parse_output(json.dumps({"scene_complete": False, "metric_changes": {},
        "fact_candidates": [candidate]}, ensure_ascii=False), engine, MESSAGE, session,
        committed_evidence=() if case == "missing_bank" else bank)

    assert bool(result.fact_operations) is (case == "valid")
    assert KEY not in session.story_state["facts"]


@pytest.mark.asyncio
async def test_unknown_route_value_from_history_is_not_committed_without_a_review(tmp_path, monkeypatch):
    # A real original quote grants no authority to invent a third route value.
    # Discard that candidate before Workflow; a normal reply needs no new check.
    story = deepcopy(_engine().story)
    story["nodes"][0].pop("completion_contract")
    story["fact_contract"]["facts"][KEY].update(
        value_type="string", description="玩家明确选定的借书交接方式。")
    for route, value in zip(story["nodes"][0]["route_gates"], ("library", "teacher")):
        route["conditions"] = {"all": [{"type": "fact_equals", "key": KEY, "value": value}]}
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    quote = "值班老师点清了借书，说交接已经确认。"
    current = await runtime.start_session(session_id="unknown-route-history", catgirl_binding=_binding(),
        opening_performance={"performance": quote, "suggested_inputs": []})
    message = "谢谢老师。"
    candidate = {"op": "set", "key": KEY, "value": "unmapped_choice", "visibility": "public",
        "confidence": "confirmed", "subject": "值班老师", "action": "确认", "object": "借书交接",
        "result": "交接确认", "evidence": [{"source": "actor_performance", "quote": quote}]}
    calls = {"evaluate": 0, "actor": 0, "review": 0}

    async def options():
        return {**{key: key == 'evaluator' for key in default_options()}, "review": False, "actor_retry": False, "suggestion_fill": False}

    async def evaluate(self, **kwargs):
        calls["evaluate"] += 1
        bank = []
        ev._build_messages(runtime.engine, kwargs["session"], message, committed_evidence=bank)
        assert any(quote in text for text in bank)
        return ev._parse_output(json.dumps({"scene_complete": False, "metric_changes": {},
            "fact_candidates": [candidate]}, ensure_ascii=False), runtime.engine, message,
            kwargs["session"], committed_evidence=bank)

    async def generate(self, **kwargs):
        calls["actor"] += 1
        return {"performance": "那我们先收好登记笔。", "suggested_inputs": [], "transition_offered": False}

    async def unexpected_review(self, **kwargs):
        calls["review"] += 1
        raise AssertionError("an invalid historical route value must not add a model call")

    monkeypatch.setattr(wf, "aload_theater_module_options", options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", unexpected_review)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "温和。")
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("thanks", 0, message), ensure_current_binding=lambda _: _binding())

    assert KEY not in result.stored.session.story_state["facts"]
    assert not result.stored.ledger_events[-1].get("fact_operations")
    assert calls == {"evaluate": 1, "actor": 1, "review": 0}
    assert result.stored.session.revision == current.session.revision + 1
    assert await runtime.restore_session(current.session.session_id) == result.stored


def test_real_r14_ellipsis_remains_unverifiable_even_with_correct_source():
    engine = _engine()
    session = _session(engine)
    real_text = ("我能负责的是按时提供定额甜点，但现场布置、搬运和长时间值守我确实顾不过来，毕竟店里人手就我一个。\n"
                 "至于志愿者引导，那是社区需要协调的事，不是我的经营责任，也不该压在你身上。")
    row = {**session.performance_history[-1], "performance": real_text}
    session = replace(session, performance_history=(row,))
    quote = "我能负责的是按时提供定额甜点，但现场布置、搬运和长时间值守我确实顾不过来……至于志愿者引导，那是社区需要协调的事，不是我的经营责任，也不该压在你身上。"
    assert _parse(engine, session, _fact(quote=quote)).fact_operations == ()


def test_history_permission_requires_the_actual_request_bank_and_cannot_splice_records():
    engine = _engine()
    session = _session(engine)
    payload = json.dumps({"scene_complete": False, "metric_changes": {}, "fact_candidates": [_fact()]},
        ensure_ascii=False)
    # 不传请求来源时不授予历史权限，即使完整 Session 碰巧包含那句原话。
    assert ev._parse_output(payload, engine, MESSAGE, session).fact_operations == ()
    first, second = QUOTE.split("\n")
    row = session.performance_history[-1]
    session = replace(session, revision=15, story_state={**session.story_state, "revision": 15},
        performance_history=({**row, "performance": first, "scene_narration": ""},
            {**row, "revision": 15, "performance": second, "scene_narration": ""}))
    # 两条各自真实的演出也不能拼接成从未作为一段交付过的引文。
    assert _parse(engine, session).fact_operations == ()


@pytest.mark.parametrize("case", ["wrong_revision", "missing_revision", "bool_revision", "entry", "entry_bridge",
    "entry_no_target", "entry_duplicate_target", "unplayed_opening",
    "loop", "old_player_input", "new_draft", "already_committed",
    "noncompletion", "global", "other_scene", "uncompleted", "bad_subject", "bad_type",
    "bad_key_list", "bad_key_dict", "duplicate"])
def test_committed_evidence_keeps_latest_visit_scope_and_existing_validation(case):
    engine = _engine()
    session = _session(engine)
    row = deepcopy(session.performance_history[-1])
    fact = _fact()
    if case in {"wrong_revision", "missing_revision", "bool_revision"}:
        row["revision"] = {"wrong_revision": 13, "missing_revision": None, "bool_revision": True}[case]
        session = replace(session, performance_history=(row,))
    elif case in {"entry", "entry_bridge", "entry_no_target", "entry_duplicate_target", "loop"}:
        entry = {"revision": 14, "from_node_id": "other", "to_node_id": NODE,
            "segments": [{"phase": "source_response", "performance": PERFORMANCE},
                {"phase": "transition_bridge", "scene_narration": PERFORMANCE if case == "entry_bridge" else "进店了。"},
                {"phase": "target_opening", "performance": "重新到了店里。"}]}
        if case == "entry_bridge":
            entry["segments"][0]["performance"] = "我们进去看看。"
        elif case == "entry_no_target":
            entry["segments"].pop()
        elif case == "entry_duplicate_target":
            entry["segments"][2]["performance"] = PERFORMANCE
            entry["segments"].append({"phase": "target_opening", "performance": "另一个目标段。"})
        history = (entry,) if case != "loop" else ({**row, "revision": 12},
            {"revision": 13, "from_node_id": NODE, "to_node_id": "other", "performance": PERFORMANCE}, entry)
        session = replace(session, performance_history=history)
    elif case == "unplayed_opening":
        # 作者模板含结果，不代表当前真实开场交付了结果。
        engine.nodes[NODE]["story_beat"]["opening_scene"] = PERFORMANCE
        session = replace(_session(engine, "opening"), opening_performance={"performance": "还要商量。"})
    elif case in {"old_player_input", "new_draft"}:
        fact["evidence"][0]["quote"] = row["input_text"] if case == "old_player_input" else "这是尚未播放的新稿。"
    elif case == "already_committed":
        session = replace(session, story_state={**session.story_state,
            "facts": {KEY: {"value": True, "visibility": "public"}}})
    elif case in {"noncompletion", "global", "other_scene"}:
        fact["key"] = {"noncompletion": f"scene:{NODE}:other", "global": "prop:ready", "other_scene": "scene:ending_leave:done"}[case]
    elif case == "uncompleted":
        row["performance"] = fact["evidence"][0]["quote"] = "我打算完成这些安排。"
        session = replace(session, performance_history=(row,))
    elif case == "bad_subject":
        fact["subject"] = None
    elif case == "bad_type":
        fact["value"] = "true"
    elif case in {"bad_key_list", "bad_key_dict"}:
        fact["key"] = [] if case == "bad_key_list" else {}
    if case in {"wrong_revision", "missing_revision", "bool_revision"}:
        # 非法 Session 不进入通用检索排序；单独验证来源绑定和 Parser 均拒绝。
        assert ev._committed_completion_evidence(engine, session)[1] == ()
        result = ev._parse_output(json.dumps({"scene_complete": False, "metric_changes": {},
            "fact_candidates": [fact]}, ensure_ascii=False), engine, MESSAGE, session,
            committed_evidence=())
    elif case == "duplicate":
        bank = []
        ev._build_messages(engine, session, MESSAGE, committed_evidence=bank)
        result = ev._parse_output(json.dumps({"scene_complete": False, "metric_changes": {},
            "fact_candidates": [fact, deepcopy(fact)]}, ensure_ascii=False), engine, MESSAGE, session,
            committed_evidence=bank)
    else:
        result = _parse(engine, session, fact)
    assert result.fact_operations == ()
    if case in {"bad_key_list", "bad_key_dict"}:
        # 一个损坏的可选候选不能让同批独立、合法的玩家事实也丢失。
        valid = {**_fact(), "key": "prop:ready", "subject": "玩家", "action": "继续复习",
            "object": "试卷", "result": "回到眼前试卷",
            "evidence": [{"source": "player_input", "quote": MESSAGE}]}
        bank = []
        ev._build_messages(engine, session, MESSAGE, committed_evidence=bank)
        result = ev._parse_output(json.dumps({"scene_complete": False, "metric_changes": {},
            "fact_candidates": [fact, valid]}, ensure_ascii=False), engine, MESSAGE, session,
            committed_evidence=bank)
        assert result.fact_operations == ({"op": "set", "key": "prop:ready", "value": True,
            "visibility": "public"},)


@pytest.mark.parametrize("phase", ["turn", "opening", "scene_entry"])
def test_request_binds_retained_committed_rows_and_names_exact_sources(phase):
    engine = _engine()
    session = _session(engine, phase)
    bank = []
    messages = ev._build_messages(engine, session, MESSAGE, committed_evidence=bank)
    data = _data(messages)
    _assert_bound_sources(data, bank)
    assert data["scene_context"][-1] == ev._fact_scene_context(session)[-1]
    assert "player_input" in messages[0].content and "runtime_fact" in messages[0].content
    assert "actor_performance" in messages[0].content and "而非本轮草稿" in messages[0].content
    assert "subject" in messages[0].content and "不自证" in messages[0].content


@pytest.mark.parametrize("formal", [False, True])
@pytest.mark.parametrize("phase", ["turn", "opening", "scene_entry"])
def test_review_binds_previous_row_separately_from_new_actor_draft(formal, phase):
    engine = _engine()
    session = _session(engine, phase)
    outcome = engine.resolve_turn(session, TurnRequestV2("formal", session.revision, MESSAGE), (),
        transition_intent="initiate") if formal else None
    candidate = {"performance": "这是本轮新稿，不能替之前的事实作证。", "suggested_inputs": []}
    if formal:
        candidate = engine.finalize_transition_performance(outcome, _candidate(), target_opening="下一幕。")
    bank = []
    messages, _ = ev._build_transition_judge_messages(engine, session, actor_performance=candidate,
        player_input=MESSAGE, evaluator_fact_claims=({**_fact(), "description": DESCRIPTION},),
        route_changed=formal, transition_outcome=outcome, committed_evidence=bank)
    data = _data(messages)
    _assert_bound_sources(data, bank)
    assert data["scene_context"][-1] == ev._fact_scene_context(session)[-1]
    assert "不是顶层 actor_performance 或 candidate_segments" in messages[0].content
    assert "引文存在不等于语义证明" in messages[0].content
    assert "只允许原 player_input 或 runtime_fact 引文证明；" not in messages[0].content


@pytest.mark.parametrize("review", [False, True])
def test_packing_retrieves_earlier_entry_and_binds_only_retained_originals(review):
    engine = _engine()
    session = _session(engine, "scene_entry")
    entry = session.performance_history[-1]
    row = {"from_node_id": NODE, "to_node_id": NODE,
        "input_text": "先整理眼前的材料。", "performance": "较早的完整演出。" * 160}
    # 原 entry 超出复核窗口、也挤出普通判定的近期正文，但完整短原文仍可按完成合同检索。
    session = replace(session, revision=29, story_state={**session.story_state, "revision": 29},
        performance_history=(entry, *({**row, "revision": index} for index in range(15, 29)),
            {**row, "revision": 29, "performance": "我先看眼前的试题。"}))
    bank = []
    messages = (ev._build_transition_judge_messages(engine, session,
        actor_performance={"performance": "先看眼前的试题。"}, player_input=MESSAGE,
        evaluator_fact_claims=({**_fact(), "description": DESCRIPTION},), committed_evidence=bank)[0] if review
        else ev._build_messages(engine, session, MESSAGE, committed_evidence=bank))
    data = _data(messages)
    _assert_bound_sources(data, bank)
    assert any(QUOTE in source for source in bank)
    assert any(ref.startswith(f"scene_context[{len(data['scene_context']) - 1}].content[")
               for ref in data["committed_fact_evidence"]["content_refs"])
    assert data["scene_context"][-1] == ev._fact_scene_context(session)[-1]
    assert all(row["revision"] != entry["revision"] for row in data["scene_context"])
    assert any(row["revision"] == entry["revision"] and row["current_visit"] is True
        and row["source"] == "performance" and QUOTE in row["text"]
        for row in data["history_evidence"])


@pytest.mark.asyncio
@pytest.mark.parametrize("consumer", ["evaluator", "review"])
@pytest.mark.parametrize("retained", [False, True])
async def test_native_fact_approval_uses_only_final_packed_originals(monkeypatch, consumer, retained):
    engine = _engine()
    session = _session(engine, "older_turn")
    if not retained:
        # 结果放在超长完整原文的末尾：原回合及检索单元都无法装下，压缩索引也不能代证。
        earlier, latest = session.performance_history
        earlier = {**earlier, "performance": "完整的背景记录。" * 2000 + PERFORMANCE}
        session = replace(session, performance_history=(earlier, latest))
    original = deepcopy(session)
    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    async def config(*args, **kwargs):
        return {"model": "offline", "base_url": "http://invalid.local"}

    async def factory(*args, **kwargs):
        return Client()

    async def invoke(client, messages, **kwargs):
        calls.append(kwargs["stage"])
        data = _data(messages)
        sources = ["\n".join(block["text"] for block in row["content"])
            for row in data.get("scene_context", [])]
        sources += [row["text"] for row in data.get("history_evidence", [])
            if row.get("current_visit") is True and row.get("source") == "performance"]
        assert any(QUOTE in source for source in sources) is retained
        if consumer == "evaluator":
            payload = {"scene_complete": False, "metric_changes": {}, "fact_candidates": [_fact()]}
        else:
            # 故意让模型无条件批准；实际被裁掉的原文仍应由 Parser 收回批准。
            payload = {"offer_present": False, "valid": False, "body_violations": [],
                "unsafe_suggestion_indexes": [], "failure_reason": "",
                "approved_evaluator_fact_indexes": [0]}
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False),
            response_metadata={"finish_reason": "stop"})

    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    monkeypatch.setattr(ev, "invoke_with_usage", invoke)
    evaluator = ev.NumericV2MetricEvaluator(object())
    if consumer == "evaluator":
        result = await evaluator.evaluate(engine=engine, session=session, message=MESSAGE)
        assert bool(result.fact_operations) is retained
    else:
        result = await evaluator.validate_transition_offer(engine=engine, session=session, message=MESSAGE,
            actor_performance={"performance": "我先看看眼前这道题。", "suggested_inputs": []},
            evaluator_fact_claims=({**_fact(), "description": DESCRIPTION},))
        assert result.approved_evaluator_fact_indexes == ((0,) if retained else ())
    assert calls == ["evaluator" if consumer == "evaluator" else "review"]
    assert session == original


@pytest.mark.asyncio
@pytest.mark.parametrize("review_on,approve", [(False, False), (False, True), (True, False), (True, True)])
@pytest.mark.parametrize("contract_origin", ["completion", "route_only"])
async def test_real_commit_restore_fork_keeps_guard_approval_authority(
    tmp_path, monkeypatch, review_on, approve, contract_origin,
):
    engine = _engine()
    if contract_origin == "route_only":
        story = deepcopy(engine.story)
        story["nodes"][0].pop("completion_contract")
        for route in story["nodes"][0]["route_gates"]:
            route["conditions"]["all"].append({"type": "fact_equals", "key": KEY, "value": True})
        engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="committed_evidence", catgirl_binding=_binding(),
        opening_performance={"performance": "活动的安排还要商量。", "suggested_inputs": []})
    current = await _commit(runtime, current, PERFORMANCE)
    assert KEY not in current.session.story_state["facts"]
    calls = {"evaluate": 0, "actor": 0, "review": 0}

    async def options():
        return {**{key: key == 'evaluator' for key in default_options()}, "review": review_on, "actor_retry": False, "suggestion_fill": False}

    async def evaluate(self, **kwargs):
        calls["evaluate"] += 1
        return _parse(engine, kwargs["session"])

    async def generate(self, **kwargs):
        calls["actor"] += 1
        # Default mode must approve the historical result before Actor sees it;
        # full Review keeps its existing final-draft approval order.
        assert (KEY in kwargs["outcome"].session.story_state["facts"]) is (not review_on and approve)
        # 检查真正装箱的完成视图：获批历史无需重演，但原子提交前不能谎称已落盘。
        from services.theater import numeric_v2_actor as actor
        forwarded = ({"approved_completion_facts": kwargs["approved_completion_facts"]}
                     if "approved_completion_facts" in kwargs else {})
        messages = actor._turn_messages(engine, kwargs["session"], kwargs["outcome"],
            kwargs["player_input"], "温和。", "小岚", "林舟", **forwarded)
        scene = _data(messages)["current_scene"]
        if contract_origin == "completion":
            start = scene.index("{", scene.index("结构化幕完成事实"))
            context = json.JSONDecoder().raw_decode(scene[start:])[0]
            row = next(row for row in context["all"] if row["key"] == KEY)
        else:
            start = scene.index("[", scene.index("当前幕路线事实条件"))
            groups = json.JSONDecoder().raw_decode(scene[start:])[0]
            row = next(row for group in groups for row in group["facts"] if row["key"] == KEY)
        assert row["committed"] is False
        assert row["satisfied"] is (not review_on and approve)
        assert row.get("approved_this_turn", False) is (not review_on and approve)
        if not review_on and approve:
            assert row["approved_value"] is True
            assert row["evidence"] == _fact()["evidence"]
            if contract_origin == "completion":
                assert context["status"] == "satisfied"
        assert KEY not in current.session.story_state["facts"]
        return {"performance": "（望向摊开的试卷）那我们接着看这道题。", "suggested_inputs": [], "transition_offered": False}

    async def guard(self, **kwargs):
        calls["review"] += 1
        assert KEY not in kwargs["session"].story_state["facts"]
        claims = kwargs["evaluator_fact_claims"]
        assert kwargs.get("completion_fact_review_only", False) is (not review_on)
        if not review_on:
            assert kwargs["actor_performance"] == {}
        assert claims[0]["evidence"] == _fact()["evidence"]
        bank = []
        messages, _ = ev._build_transition_judge_messages(engine, kwargs["session"],
            actor_performance=kwargs["actor_performance"], player_input=MESSAGE, evaluator_fact_claims=claims,
            committed_evidence=bank)
        assert _data(messages)["committed_fact_evidence"]["content_refs"]
        assert any(QUOTE in source for source in bank)
        return ev.NumericV2TransitionOfferReview(False, False, (), (),
            approved_evaluator_fact_indexes=(0,) if approve else ())

    monkeypatch.setattr(wf, "aload_theater_module_options", options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", guard)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "温和。")
    restored = await runtime.restore_session(current.session.session_id)
    fork = await runtime.fork_session_for_test(current.session.session_id, session_id="before-backfill", through_revision=1)
    assert _parse(engine, restored.session).fact_operations == _parse(engine, fork.session).fact_operations != ()
    result = await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=restored,
        turn=TurnRequestV2("backfill", 1, MESSAGE), ensure_current_binding=lambda _: _binding())
    assert (KEY in result.stored.session.story_state["facts"]) == approve
    assert calls == {"evaluate": 1, "actor": 1, "review": 1}
    reloaded = await runtime.restore_session(current.session.session_id)
    assert reloaded == result.stored
    forked = await runtime.fork_session_for_test(current.session.session_id, session_id="after-backfill", through_revision=2)
    assert forked.session.story_state == result.stored.session.story_state
