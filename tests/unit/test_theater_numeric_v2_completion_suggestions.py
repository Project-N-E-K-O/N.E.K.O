"""An explicitly reviewed invitation stays usable without approving completion or narrative events."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as wf
from services.theater.numeric_v2_actor import NumericV2ActorOutputError
from services.theater.numeric_v2_context import pending_transition_record
from services.theater.numeric_v2_options import default_options
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_pending_offer import _commit
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_unified_pacing import story_for


KEY = "scene:start:done"
OFFER = "要现在一起去山径的避风棚吗？"
BODY = "（收好自己的手记）前面就是避风棚，要一起过去看看吗？"
CHOICES = ["好，我们去避风棚看看。", "先留在这里吧，我还想看看山景。"]
UNREVIEWED = "我想再问一个刚刚没有提到的问题。"


async def _setup(tmp_path, monkeypatch, *, actor_flag=True, author_offer=False,
                 protocol="allow", mode="on", history="", candidate=True, fact_switch=False,
                 fact_blocked=False):
    story = story_for("山径的避风棚")
    story["fact_contract"]["facts"][KEY]["description"] = "双方决定前往避风棚并实际启程。"
    if fact_switch:
        story["fact_contract"]["facts"][KEY]["description"] = "猫娘已经收好自己的手记。"
        # 新结果成立后，高优先级出口会取代复核时的低优先级出口。
        story["nodes"][0]["route_gates"][0]["conditions"] = {
            "all": [{"type": "fact_equals", "key": KEY, "value": True}]}
    if fact_blocked:
        for route in story["nodes"][0]["route_gates"]:
            route["conditions"]["all"].append({"type": "fact_equals", "key": KEY, "value": True})
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="completion-choices", catgirl_binding=_binding(),
        opening_performance={"performance": "（望向山径）这里的风有些凉。", "suggested_inputs": []})
    if history:
        current = await _commit(runtime, current, OFFER, offer=True)
        if history == "withdrawn":
            current = await _commit(runtime, current, "好，先留在这里。", intent="reject")
    body = "（收好自己的手记）" + OFFER if author_offer else BODY
    state = {"mode": mode, "protocol": protocol, "reviewed": False, "calls": [], "payloads": [],
        "approve_fact": fact_switch,
        "candidate": {"performance": body, "scene_narration": "山风吹过路边的草叶。",
            "suggested_inputs": list(CHOICES), "transition_offered": actor_flag,
            "fact_candidates": [{"key": KEY, "value": True,
                "evidence_quote": body}] if candidate else []}}

    async def options():
        return {**{key: key == 'evaluator' for key in default_options()}, "evaluator": state["mode"] != "off"}

    async def evaluate(self, **kwargs):
        if state["mode"] == "degraded":
            raise ev.NumericV2EvaluatorError("test_evaluator_failure")
        return ev.NumericV2EvaluationResult((), False,
            transition_intent=state["mode"] if state["mode"] in {"accept", "reject"} else "unclear")

    async def generate(self, **kwargs):
        outcome = kwargs["outcome"]
        if outcome.ledger_event["from_node_id"] != outcome.ledger_event["to_node_id"]:
            # 真正执行 Runtime 的三段组装，避免把“允许 accept”误当成已完成换幕。
            return engine.finalize_transition_performance(outcome, {
                "source_performance": "（收好手记）那就走吧。",
                "bridge_scene_narration": "山风掠过路边的草叶。",
                "target_scene_narration": "棚檐挡住迎面的风，门边留着空位。",
                "target_performance": "（在棚边停下）这里能避一避风。",
                "suggested_inputs": [],
            }, target_opening="山径的避风棚敞着门。")
        return deepcopy(state["candidate"])

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    async def factory(*args, **kwargs):
        state["calls"].append(kwargs)
        return Client()

    async def invoke(client, messages, **kwargs):
        # 固定模型边界的原始响应，实际请求构造、schema 与 parser 均执行生产代码。
        raw = messages[1].content
        data = json.loads(raw[raw.index("{"):])
        state["payloads"].append(data)
        assert kwargs["stage"] == "review"
        assert data["suggested_inputs"] == CHOICES
        payload = {"offer_present": True, "offer_quote": OFFER if author_offer else BODY.split("）", 1)[1],
            "offer_kind": "invitation", "valid": True, "body_violations": [],
            "suggestion_checks": [{"index": index, "decision": "allow", "requires": []}
                for index in range(2)], "failure_reason": "", "player_action_kind": "",
            "fact_candidates": [], "approved_actor_fact_indexes": [0] if state["approve_fact"] else []}
        choice = state["protocol"]
        if choice == "reject":
            payload["suggestion_checks"][0]["decision"] = "reject"
        elif choice == "legacy":
            payload.pop("suggestion_checks")
            payload["unsafe_suggestion_indexes"] = []
        elif choice == "incomplete":
            payload["suggestion_checks"].pop()
        elif choice == "mixed":
            payload["unsafe_suggestion_indexes"] = []
        elif choice == "body":
            payload.update(body_violations=["author_boundary"], failure_reason="固定负控：正文违反作者边界。")
        elif choice == "offer":
            payload.update(valid=False, failure_reason="固定负控：邀请去向不合法。")
        schema = kwargs["response_format"]["json_schema"]["schema"]
        if "offer_acceptance_valid" in schema["required"]:
            # 首按钮是否接受须独立给出严格布尔量；合法的留幕按钮不因此获得接受资格。
            first_check = next((item for item in payload.get("suggestion_checks", [])
                                if item["index"] == 0), None)
            payload["offer_acceptance_valid"] = bool(
                data["suggested_inputs"][0] == CHOICES[0]
                and payload["offer_quote"] in data["actor_performance"]
                and payload["valid"] and not payload["body_violations"]
                and first_check is not None and first_check["decision"] == "allow"
            )
        state["reviewed"] = True
        return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False),
            response_metadata={"finish_reason": "stop"})

    async def no_proposal(self, **kwargs):
        raise AssertionError("narrow button approval cannot create a narrative proposal")

    monkeypatch.setattr(wf, "aload_theater_module_options", options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "温和。")
    monkeypatch.setattr(wf.NumericV2NarrativeProposer, "propose", no_proposal)
    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    monkeypatch.setattr(ev, "invoke_with_usage", invoke)
    return runtime, current, state


async def _turn(runtime, current, *, key="choices", message="接下来是继续坐着，还是去别处看看？", source="freeform"):
    return await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2(key, current.session.revision, message, source),
        ensure_current_binding=lambda _: _binding())


@pytest.mark.asyncio
@pytest.mark.parametrize("second_veto", [False, True])
async def test_invitation_without_new_facts_shares_one_reviewed_repair(tmp_path, monkeypatch, second_veto):
    runtime, current, state = await _setup(tmp_path, monkeypatch, candidate=False)
    done = runtime.prepare_turn(current, TurnRequestV2("done", current.session.revision, "已经处理完了。"), (),
        fact_operations=({"op": "set", "key": KEY, "value": True, "visibility": "public"},))
    current = await runtime.commit_turn(done, {
        "performance": "眼前的事情已经处理完了。", "suggested_inputs": [], "transition_offered": False,
    })
    path = runtime.store._path(current.session.session_id)
    before = path.read_bytes()
    calls = {"actor": 0, "review": 0}

    async def generate(self, **kwargs):
        calls["actor"] += 1
        assert calls["actor"] <= 2
        assert path.read_bytes() == before
        draft = deepcopy(state["candidate"])
        if calls["actor"] == 2:
            assert kwargs.get("retry_hint")
            draft["performance"] = "（看向避风棚）" + OFFER
        return draft

    async def review(self, **kwargs):
        calls["review"] += 1
        assert calls["review"] <= 2
        assert kwargs["completion_fact_review_only"] is True
        assert not kwargs["actor_performance"].get("fact_candidates")
        assert path.read_bytes() == before
        veto = calls["review"] == 1 or second_veto
        return ev.NumericV2TransitionOfferReview(
            offer_present=not veto, valid=not veto,
            body_violations=("player_action",) if veto else (), unsafe_suggestion_indexes=(),
            offer_quote=OFFER if not veto else "", offer_kind="invitation" if not veto else "",
            approved_suggestion_texts=tuple(CHOICES) if not veto else (),
            offer_acceptance_valid=not veto,
        )

    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "validate_transition_offer", review)
    if second_veto:
        with pytest.raises(NumericV2ActorOutputError):
            await _turn(runtime, current)
        assert path.read_bytes() == before
        assert await runtime.restore_session(current.session.session_id) == current
    else:
        result = await _turn(runtime, current)
        assert result.stored.session.revision == current.session.revision + 1
        assert result.stored.session.current_node_id == current.session.current_node_id
        assert result.diagnostics["semantic_rewrite_attempts"] == 1
        assert result.stored.session.transition_offered is True
        assert BODY not in str(result.stored.session.performance_history)
    assert calls == {"actor": 2, "review": 2}


def _assert_no_new_authority(result):
    assert KEY not in result.stored.session.story_state["facts"]
    assert not result.stored.session.transition_offered
    assert not result.performance["transition_offered"]
    assert result.stored.session.current_node_id == "start"
    for value in (result.performance, result.stored.ledger_events[-1]):
        for field in ("program_invitation", "transition_offer_presented", "transition_offer_source"):
            assert field not in value
    assert result.stored.ledger_events[-1]["narrative_record"]["coverage"] == "unrecorded"


def _assert_reviewed_invitation_only(result, *, acceptance_receipt=True):
    assert KEY not in result.stored.session.story_state["facts"]
    assert result.stored.session.current_node_id == "start"
    assert result.stored.session.transition_offered
    assert result.performance["transition_offered"]
    for value in (result.performance, result.stored.ledger_events[-1]):
        assert value["transition_offer_source"] == "reviewed"
        assert value["transition_offer_presented"] is True
    assert "program_invitation" not in result.performance
    event = result.stored.ledger_events[-1]
    if acceptance_receipt:
        # 只保存独立核验过的原稿首按钮回执；它不代表事实、叙事事件或玩家已经接受。
        receipt = event["program_invitation"]
        assert receipt["kind"] == "reviewed_dynamic"
        assert receipt["revision"] == result.stored.session.revision
        assert receipt["route_id"] == "to_leave"
        assert receipt["target_node_id"] == "ending_leave"
        assert receipt["accept_input"] == CHOICES[0]
        assert receipt["performance"] == result.performance["performance"]
        assert receipt["offer"] == BODY.split("）", 1)[1]
    else:
        assert "program_invitation" not in event
    assert result.stored.ledger_events[-1]["narrative_record"]["coverage"] == "unrecorded"


@pytest.mark.asyncio
@pytest.mark.parametrize("actor_flag,author_offer", [(True, False), (False, True)])
async def test_reviewed_invitation_survives_without_completion_or_narrative_approval(
    tmp_path, monkeypatch, actor_flag, author_offer,
):
    runtime, current, state = await _setup(tmp_path, monkeypatch,
        actor_flag=actor_flag, author_offer=author_offer)
    result = await _turn(runtime, current)
    assert result.performance["suggested_inputs"] == CHOICES
    _assert_reviewed_invitation_only(result, acceptance_receipt=actor_flag)
    assert len(state["calls"]) == 1
    assert state["calls"][0]["max_retries"] == 0
    assert state["calls"][0]["max_completion_tokens"] == 350
    assert result.diagnostics["completion_fact_review_calls"] == 1
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id) == result.stored
    fork = await runtime.fork_session_for_test(current.session.session_id,
        session_id="choice-fork", through_revision=result.stored.session.revision)
    assert fork.session.performance_history[-1]["suggested_inputs"] == CHOICES
    assert fork.session.transition_offered
    assert fork.session.performance_history[-1]["transition_offer_source"] == "reviewed"
    assert fork.ledger_events[-1]["transition_offer_source"] == "reviewed"
    if actor_flag:
        assert fork.ledger_events[-1]["program_invitation"] == result.stored.ledger_events[-1]["program_invitation"]
    else:
        assert "program_invitation" not in fork.ledger_events[-1]


@pytest.mark.asyncio
async def test_reviewed_invitation_can_be_accepted_after_restore_and_fork(tmp_path, monkeypatch):
    runtime, current, state = await _setup(tmp_path, monkeypatch)
    first = await _turn(runtime, current)
    assert first.performance["suggested_inputs"] == CHOICES
    state["mode"] = "accept"
    restored = await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id)
    fork = await runtime.fork_session_for_test(current.session.session_id,
        session_id="accept-fork", through_revision=first.stored.session.revision)
    for stored, key in [(restored, "choose"), (fork, "fork-choose")]:
        result = await _turn(runtime, stored, key=key, message=CHOICES[0], source="suggestion")
        assert result.stored.session.current_node_id == "ending_leave"
        assert result.stored.ledger_events[-1]["transition_intent"] == "accept"
        assert len(result.performance["segments"]) == 3
        assert not result.stored.session.transition_offered
        assert KEY not in result.stored.session.story_state["facts"]
        assert "program_invitation" not in result.stored.ledger_events[-1]
        assert await runtime.restore_session(stored.session.session_id) == result.stored
    assert len(state["calls"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("message,source", [(CHOICES[0], "freeform"), ("好，就按这个安排。", "suggestion")])
async def test_reviewed_offer_does_not_authorize_other_input_sources_or_unshown_choices(
    tmp_path, monkeypatch, message, source,
):
    runtime, current, state = await _setup(tmp_path, monkeypatch)
    offered = await _turn(runtime, current)
    state["mode"] = "accept"
    state["candidate"] = {"performance": "（望着山景）先看看风向。",
        "suggested_inputs": [], "transition_offered": False}
    result = await _turn(runtime, offered.stored, key="unmatched", message=message, source=source)
    assert result.stored.session.current_node_id == "start"
    assert result.stored.ledger_events[-1]["transition_intent"] == "unclear"
    assert KEY not in result.stored.session.story_state["facts"]
    assert len(state["calls"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["reject", "legacy", "incomplete", "mixed"])
async def test_nonapproved_button_group_stays_empty(tmp_path, monkeypatch, protocol):
    runtime, current, state = await _setup(tmp_path, monkeypatch, protocol=protocol)
    result = await _turn(runtime, current)
    assert result.performance["suggested_inputs"] == []
    _assert_no_new_authority(result)
    assert len(state["calls"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("protocol", ["body", "offer"])
async def test_body_or_invitation_rejection_remains_atomic(tmp_path, monkeypatch, protocol):
    runtime, current, _ = await _setup(tmp_path, monkeypatch, protocol=protocol)
    before = runtime.store._path(current.session.session_id).read_bytes()
    with pytest.raises(NumericV2ActorOutputError, match="^numeric_v2_completion_fact_review_rejected$"):
        await _turn(runtime, current)
    assert runtime.store._path(current.session.session_id).read_bytes() == before
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,candidate", [("on", False), ("off", True), ("degraded", True)])
async def test_no_eligible_review_or_degraded_evaluator_does_not_exempt_choices(tmp_path, monkeypatch, mode, candidate):
    runtime, current, state = await _setup(tmp_path, monkeypatch, mode=mode, candidate=candidate)
    result = await _turn(runtime, current)
    assert result.performance["suggested_inputs"] == []
    _assert_no_new_authority(result)
    assert len(state["calls"]) == int(mode == "degraded")


@pytest.mark.asyncio
async def test_player_rejection_keeps_choices_retracted_despite_narrow_approval(tmp_path, monkeypatch):
    runtime, current, state = await _setup(tmp_path, monkeypatch, mode="reject")
    result = await _turn(runtime, current, message="先不去，我想留在这里。")
    assert result.performance["suggested_inputs"] == []
    _assert_no_new_authority(result)
    assert len(state["calls"]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("history", ["pending", "withdrawn"])
async def test_old_offer_history_keeps_conservative_retraction(tmp_path, monkeypatch, history):
    runtime, current, _ = await _setup(tmp_path, monkeypatch, history=history)
    result = await _turn(runtime, current)
    assert result.performance["suggested_inputs"] == []
    _assert_no_new_authority(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["performance", "scene_narration"])
async def test_later_body_change_cannot_inherit_choice_approval(tmp_path, monkeypatch, field):
    runtime, current, state = await _setup(tmp_path, monkeypatch)
    original = wf._prefilter_suggestion_candidates

    def changed(candidate):
        filtered, removed, reasons = original(candidate)
        if state["reviewed"]:
            filtered = {**filtered, field: str(filtered.get(field) or "") + "风势忽然变大。"}
        return filtered, removed, reasons

    monkeypatch.setattr(wf, "_prefilter_suggestion_candidates", changed)
    result = await _turn(runtime, current)
    assert result.performance["suggested_inputs"] == []
    _assert_no_new_authority(result)


@pytest.mark.asyncio
async def test_later_unreviewed_button_is_not_resurrected(tmp_path, monkeypatch):
    runtime, current, state = await _setup(tmp_path, monkeypatch)
    original = wf._prefilter_suggestion_candidates

    def appended(candidate):
        filtered, removed, reasons = original(candidate)
        if state["reviewed"]:
            filtered = {**filtered, "suggested_inputs": [*filtered["suggested_inputs"], UNREVIEWED]}
        return filtered, removed, reasons

    monkeypatch.setattr(wf, "_prefilter_suggestion_candidates", appended)
    result = await _turn(runtime, current)
    assert result.performance["suggested_inputs"] == CHOICES
    _assert_reviewed_invitation_only(result)


@pytest.mark.asyncio
async def test_legacy_offer_without_explicit_reviewed_source_cannot_gain_acceptance(tmp_path, monkeypatch):
    runtime, current, state = await _setup(tmp_path, monkeypatch, history="pending", candidate=False)
    state["mode"] = "accept"
    state["candidate"] = {"performance": "（望着山景）先看看风向。",
        "suggested_inputs": [], "transition_offered": False}
    result = await _turn(runtime, current, message="好，就按这个安排。", source="suggestion")
    assert result.stored.session.current_node_id == "start"
    assert result.stored.ledger_events[-1]["transition_intent"] == "unclear"
    assert "transition_offer_source" not in result.stored.ledger_events[-1]
    assert state["calls"] == []


@pytest.mark.asyncio
async def test_fact_settlement_cannot_retarget_the_reviewed_invitation(tmp_path, monkeypatch):
    runtime, current, state = await _setup(tmp_path, monkeypatch, fact_switch=True)
    assert runtime.engine.preview_route("start", current.session.metrics,
        facts=current.session.story_state["facts"])["id"] == "to_leave"
    result = await _turn(runtime, current)
    assert len(state["calls"]) == 1
    assert result.stored.session.story_state["facts"][KEY]["value"] is True
    assert runtime.engine.preview_route("start", result.stored.session.metrics,
        facts=result.stored.session.story_state["facts"])["id"] == "to_stay"
    assert result.performance["suggested_inputs"] == []
    assert result.stored.session.current_node_id == "start"
    assert not result.stored.session.transition_offered
    for value in (result.performance, result.stored.ledger_events[-1]):
        assert "program_invitation" not in value
        assert "transition_offer_source" not in value


@pytest.mark.asyncio
async def test_review_cannot_authorize_a_route_blocked_by_an_unapproved_fact(tmp_path, monkeypatch):
    runtime, current, state = await _setup(tmp_path, monkeypatch, fact_blocked=True)
    assert runtime.engine.preview_route("start", current.session.metrics,
        facts=current.session.story_state["facts"]) is None
    result = await _turn(runtime, current)
    # 即使模型错误批准邀请和全部按钮，程序的事实路线门禁仍然有效。
    assert len(state["calls"]) == 1
    assert result.performance["suggested_inputs"] == []
    _assert_no_new_authority(result)


@pytest.mark.asyncio
async def test_unclear_reply_to_reviewed_invitation_does_not_become_acceptance(tmp_path, monkeypatch):
    runtime, current, state = await _setup(tmp_path, monkeypatch)
    first = await _turn(runtime, current)
    _assert_reviewed_invitation_only(first)
    state["candidate"] = {"performance": "（望着山景）你可以再想一想。",
        "suggested_inputs": [], "transition_offered": False}
    result = await _turn(runtime, first.stored, key="unclear", message="我再想一想。")
    assert result.stored.ledger_events[-1]["transition_intent"] == "unclear"
    assert result.stored.session.current_node_id == "start"
    assert result.performance["suggested_inputs"] == []
    assert len(state["calls"]) == 1
    assert not result.stored.session.transition_offered


@pytest.mark.asyncio
async def test_reviewed_invitation_commit_fence_leaves_no_partial_state(tmp_path, monkeypatch):
    runtime, current, state = await _setup(tmp_path, monkeypatch)
    before = runtime.store._path(current.session.session_id).read_bytes()
    unchanged_current = deepcopy(current)

    async def before_commit():
        raise RuntimeError("test_reviewed_invitation_fence")

    with pytest.raises(RuntimeError, match="^test_reviewed_invitation_fence$"):
        await wf.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2("fenced", current.session.revision, "接下来去哪里？"),
            ensure_current_binding=lambda _: _binding(), before_commit=before_commit)
    assert len(state["calls"]) == 1
    assert current == unchanged_current
    assert runtime.store._path(current.session.session_id).read_bytes() == before
    assert await runtime.restore_session(current.session.session_id) == current


@pytest.mark.asyncio
async def test_stale_reviewed_origin_does_not_authorize_later_acceptance(tmp_path, monkeypatch):
    runtime, current, state = await _setup(tmp_path, monkeypatch)
    first = await _turn(runtime, current)
    _assert_reviewed_invitation_only(first)
    current = await _commit(runtime, first.stored, "你还想问些什么？", offer=False)
    origin = pending_transition_record(current.session, ledger_events=current.ledger_events)
    # 原始 reviewed 来源仍存在；拒绝来自轮次已旧，而非误把该记录当作 legacy。
    assert current.session.transition_offered
    assert origin["transition_offer_source"] == "reviewed"
    assert origin["revision"] == first.stored.session.revision < current.session.revision
    state["mode"] = "accept"
    state["candidate"] = {"performance": "（站在原处）先把你的问题说清楚。",
        "suggested_inputs": [], "transition_offered": False}
    result = await _turn(runtime, current, key="stale", message=CHOICES[0], source="suggestion")
    assert result.stored.ledger_events[-1]["transition_intent"] == "unclear"
    _assert_no_new_authority(result)
    assert len(state["calls"]) == 1
