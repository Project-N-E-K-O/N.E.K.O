"""Chat wording must not disable the ordinary theater pacing and invitation path."""

from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as workflow
from services.theater.numeric_v2_runtime import (
    NumericV2Engine,
    NumericV2Runtime,
    TurnRequestV2,
)
from services.theater.numeric_v2_options import default_options
from tests.utils.theater_prompt import prompt_payload
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding


def story_for(destination):
    story = numeric_v2_story()
    story["fact_contract"] = {
        "facts": {
            "scene:start:done": {
                "value_type": "bool",
                "visibility": "public",
                "description": "眼前的事情已经处理完。",
            }
        }
    }
    story["nodes"][0]["completion_contract"] = {
        "all": [{"key": "scene:start:done", "equals": True}]
    }
    contract = story["nodes"][0]["route_gates"][1]["transition_contract"]
    contract.update(
        fallback_offer=f"要现在一起去{destination}吗？",
        accept_input="好，我们现在过去。",
    )
    middle = story["nodes"][2]
    middle.update(type="scene", min_turns=1)
    middle.pop("terminal")
    middle.pop("ending_id")
    middle["route_gates"] = [
        {
            "id": "middle_to_leave",
            "target_node_id": "ending_after_middle",
            "priority": 100,
            "conditions": {"all": []},
            "transition_contract": deepcopy(contract),
        }
    ]
    middle["route_gates"][0]["transition_contract"].pop("fallback_offer")
    story["nodes"].append(
        {
            "id": "ending_after_middle",
            "type": "ending",
            "chapter": "离开",
            "story_beat": deepcopy(middle["story_beat"]),
            "route_gates": [],
            "terminal": True,
            "ending_id": "leave",
        }
    )
    return story


def add_fact_selected_archive(story, middle):
    archive = deepcopy(story["nodes"][2])
    archive.update(id="archive", chapter="档案室")
    archive["route_gates"][0]["id"] = "archive_to_leave"
    story["nodes"].append(archive)
    route = deepcopy(middle)
    route.update(id="to_archive", target_node_id="archive", priority=200, conditions={
        "all": [{"type": "fact_equals", "key": "scene:start:done", "value": True}],
    })
    route["transition_contract"].update(
        reason="记录封存后开放档案室。", fallback_offer="要和我一起去档案室吗？",
        accept_input="好，我和你去档案室。",
    )
    story["nodes"][0]["route_gates"].append(route)
    return route


@pytest.mark.asyncio
@pytest.mark.parametrize("case", [
    "blocked", "button_only", "separate", "quoted", "late", "established", "disappears",
    "different_exit", "matches_final_exit", "malformed_fallback",
])
async def test_visible_invitation_tracks_the_settled_exit(tmp_path, monkeypatch, case):
    from services.theater import numeric_v2_options
    from services.theater.numeric_v2_actor_output import _parse_output
    from services.theater.numeric_v2_structured_output import actor_output_schema

    story = story_for("长街")
    key = "scene:start:done"
    ending, middle = story["nodes"][0]["route_gates"]
    ending["conditions"]["all"][0]["value"] = 90
    changes_exit = case in {"different_exit", "matches_final_exit"}
    if case != "established" and not changes_exit:
        middle["conditions"] = {"all": [{"type": "fact_equals", "key": key, "value": True}]}
    if case == "malformed_fallback":
        middle["transition_contract"]["accept_input"] = "明确选择一起去长街或暂缓"
    if changes_exit:
        route = add_fact_selected_archive(story, middle)
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id="unresolved-offer", catgirl_binding=_binding(),
        opening_performance={"performance": "先处理眼前的事。", "suggested_inputs": []},
    )
    if case == "disappears":
        prepared = runtime.prepare_turn(current, TurnRequestV2("ready", 0, "事情处理完了。"), (),
            fact_operations=({"op": "set", "key": key, "value": True, "visibility": "public"},))
        current = await runtime.commit_turn(prepared, {"performance": "已经处理完了。", "suggested_inputs": []})
    initial_route = engine.preview_route("start", current.session.metrics, facts=current.session.story_state["facts"])
    actor_may_offer = case in {"established", "disappears"} or changes_exit
    assert (initial_route is not None) is actor_may_offer

    authored_offer = middle["transition_contract"]["fallback_offer"]
    authored_accept = middle["transition_contract"]["accept_input"]
    offer = "事情已经办妥，要和我一起去长街吗？" if case == "established" else "要现在一起去东门档案室吗？"
    suggestion = "好，我和你去长街。" if case == "established" else "好，我这就和你去东门档案室。"
    if changes_exit:
        choice = middle if case == "different_exit" else route
        offer = choice["transition_contract"]["fallback_offer"]
        suggestion = choice["transition_contract"]["accept_input"]
    elif case == "button_only":
        offer = ""
    body = {
        "late": "眼前的事情已经处理完。",
        "malformed_fallback": "眼前的事情已经处理完。",
        "disappears": "登记已经作废。",
    }.get(case, "登记已经收好。")
    quoted = f"纸条上写着「{offer}」"
    wire_body = body + {"separate": f"\n{offer}", "quoted": quoted, "late": f"\n{offer}"}.get(case, "")
    candidates = {
        "late": [{"key": key, "value": True, "evidence_quote": body}],
        "malformed_fallback": [{"key": key, "value": True, "evidence_quote": body}],
        "established": [{"key": key, "value": True, "evidence_quote": "事情已经办妥"}],
        "disappears": [{"key": key, "value": False, "evidence_quote": body}],
    }.get(case, [])
    if changes_exit:
        candidates = [{"key": key, "value": True, "evidence_quote": body}]

    async def options():
        return {key: key == "evaluator" for key in default_options()}

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False)

    async def invoke(self, messages, **kwargs):
        next_scene = prompt_payload(messages[1].content)["next_scene"]
        assert ("当前没有可用出口" in next_scene) is not actor_may_offer
        assert kwargs["suppress_transition_offer"] is not actor_may_offer
        assert kwargs["suppress_suggestions"] is not actor_may_offer
        assert ("给一至三条可直接发送的玩家输入" in messages[0].content) is actor_may_offer
        schema = actor_output_schema(suppress_transition_offer=kwargs["suppress_transition_offer"],
                                     suppress_suggestions=kwargs["suppress_suggestions"])
        assert (schema["properties"]["transition_offer"].get("enum") == [""]) is not actor_may_offer
        assert (schema["properties"]["suggested_inputs"].get("maxItems") == 0) is not actor_may_offer
        return _parse_output(json.dumps({
            "performance": wire_body,
            "transition_offer": offer,
            "suggested_inputs": [suggestion],
            "fact_candidates": candidates,
        }, ensure_ascii=False), dialogue_policy=kwargs["dialogue_policy"],
            suppress_transition_offer=kwargs["suppress_transition_offer"],
            fact_candidates_expected=kwargs["fact_candidates_expected"])

    monkeypatch.setattr(numeric_v2_options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_invoke", invoke)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("reply", current.session.revision, "你看一下处理结果。"),
        ensure_current_binding=lambda _: _binding(),
    )

    assert result.stored.session.current_node_id == "start"
    has_visible_offer = case in {"late", "established", "matches_final_exit"}
    assert engine.completion_contract_satisfied(result.stored.session) is (
        has_visible_offer or changes_exit or case == "malformed_fallback"
    )
    assert result.stored.session.transition_offered is has_visible_offer
    suffix = {"late": f"\n{authored_offer}", "established": f"\n{offer}", "quoted": quoted}
    buttons = {"late": [authored_accept], "established": [suggestion]}
    if changes_exit:
        settled = engine.preview_route("start", result.stored.session.metrics,
                                       facts=result.stored.session.story_state["facts"])
        assert settled["id"] == "to_archive"
    if case == "matches_final_exit":
        suffix[case], buttons[case] = f"\n{offer}", [suggestion]
    assert result.performance["performance"] == body + suffix.get(case, "")
    assert result.performance["suggested_inputs"] == buttons.get(case, [])
    assert result.diagnostics["completion_fallback_offer_applied"] == int(case == "late")
    if case == "late":
        from services.theater.numeric_v2_context import recorded_transition_offer

        assert recorded_transition_offer(result.stored.session.performance_history[-1]) == authored_offer
    assert await runtime.restore_session("unresolved-offer") == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_offer", [False, True])
async def test_locked_ending_keeps_only_local_recommendations_from_same_actor_call(tmp_path, monkeypatch, invalid_offer):
    from services.theater import numeric_v2_options
    from services.theater.numeric_v2_actor_output import _parse_output
    from services.theater.numeric_v2_structured_output import actor_output_schema

    story = numeric_v2_story()
    key = "scene:start:result_shown"
    story["fact_contract"] = {"facts": {key: {
        "value_type": "bool", "visibility": "public", "description": "眼前的结果已实际交付。",
    }}}
    story["nodes"][0]["completion_contract"] = {"all": [{"key": key, "equals": True}]}
    for gate in story["nodes"][0]["route_gates"]:
        gate["conditions"] = {"all": [{"type": "fact_equals", "key": key, "value": True}]}
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id="climax_local_buttons", catgirl_binding=_binding(),
        opening_performance={"performance": "那是探照灯。真正的出口还没找到。", "suggested_inputs": []},
    )
    suggestions = ["你确定现在只剩这一种办法吗？", "（扶住她的手臂）我先扶住你。"]
    call_count = 0

    async def options():
        return {key: key == "evaluator" for key in default_options()}

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False)

    async def invoke(self, messages, **kwargs):
        nonlocal call_count
        call_count += 1
        assert kwargs["suppress_transition_offer"] is True
        assert kwargs["suppress_suggestions"] is False
        assert "只给玩家留在当前可见场景的回应、问题或动作" in messages[0].content
        assert "有邀请时首项接受同一安排" not in messages[0].content
        assert "当前没有可用出口" in prompt_payload(messages[1].content)["next_scene"]
        schema = actor_output_schema(suppress_transition_offer=True, suppress_suggestions=False)
        assert schema["properties"]["transition_offer"]["enum"] == [""]
        assert schema["properties"]["suggested_inputs"].get("maxItems") != 0
        return _parse_output(json.dumps({
            "performance": "不是出口。前面是探照灯，我先把办法说清楚。",
            "transition_offer": "现在一起去没见过的地下通道吗？" if invalid_offer else "",
            "suggested_inputs": suggestions,
            "fact_candidates": [],
        }, ensure_ascii=False), dialogue_policy=kwargs["dialogue_policy"],
            suppress_transition_offer=True, fact_candidates_expected=True)

    monkeypatch.setattr(numeric_v2_options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_invoke", invoke)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "冷静。")
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("look-at-light", 0, "前面有光，是出口吗？"),
        ensure_current_binding=lambda _: _binding(),
    )

    assert call_count == 1
    assert result.stored.session.current_node_id == "start"
    assert result.stored.session.transition_offered is False
    assert result.performance["suggested_inputs"] == ([] if invalid_offer else suggestions)
    assert result.diagnostics["actor_suggestion_fill_attempts"] == 0
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
async def test_old_placeholder_invitation_can_be_withdrawn_without_changing_saved_prose(tmp_path, monkeypatch):
    from services.theater import numeric_v2_options
    from services.theater.numeric_v2_context import pending_transition_record

    story = story_for("长街")
    contract = story["nodes"][0]["route_gates"][1]["transition_contract"]
    placeholder = "明确选择全拓、只挑部分或暂缓"
    contract["accept_input"] = placeholder
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id="legacy-meta", catgirl_binding=_binding(),
                                          opening_performance={"performance": "先处理眼前的事。"})
    prepared = runtime.prepare_turn(current, TurnRequestV2("ready", 0, "事情处理完了。"), (),
        fact_operations=({"op": "set", "key": "scene:start:done", "value": True, "visibility": "public"},))
    prepared, old_performance = engine.finalize_transition_offer_state(prepared, {
        "performance": f"事情已经处理好了。\n{contract['fallback_offer']}",
        "suggested_inputs": [placeholder],
    }, new_offer=True)
    old = await runtime.commit_turn(prepared, old_performance)
    assert await runtime.restore_session("legacy-meta") == old
    assert engine.has_placeholder_authored_invitation(old)
    direct = runtime.prepare_turn(old, TurnRequestV2("real", 1, "好，我们现在一起去长街。"), (),
                                  transition_intent="accept")
    assert direct.session.current_node_id == "ending_leave"

    async def options():
        return {key: key == "evaluator" for key in default_options()}

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), True, transition_intent="accept")

    async def generate(self, **kwargs):
        assert kwargs["outcome"].session.current_node_id == "start"
        return {"performance": "我们先说清接下来的安排。", "suggested_inputs": [placeholder, "我们再商量。"]}

    monkeypatch.setattr(numeric_v2_options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=old,
        turn=TurnRequestV2("click", 1, placeholder, "suggestion"), ensure_current_binding=lambda _: _binding(),
    )
    assert result.performance["suggested_inputs"] == ["我们再商量。"]
    assert result.stored.session.performance_history[0] == old.session.performance_history[0]
    assert result.stored.ledger_events[-1]["transition_offer_invalidated"] is True
    assert pending_transition_record(result.stored.session, ledger_events=result.stored.ledger_events,
                                     include_withdrawn=True) is None
    assert workflow.invitation_recovery_contract(runtime, result.stored) is None
    assert await runtime.restore_session("legacy-meta") == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("offer_matches_settled_route", [False, True])
async def test_live_acceptance_checks_older_unsigned_offers_without_rewriting_saved_history(
    tmp_path, offer_matches_settled_route,
):
    story = story_for("长街")
    _, old_route = story["nodes"][0]["route_gates"]
    settled_route = add_fact_selected_archive(story, old_route)
    contract = (settled_route if offer_matches_settled_route else old_route)["transition_contract"]
    engine = NumericV2Engine.from_mapping(story)
    runtime = NumericV2Runtime(engine, tmp_path)
    started = await runtime.start_session(session_id="old-offer", catgirl_binding=_binding(),
        opening_performance={"performance": "先处理眼前的事。", "suggested_inputs": []})
    prepared = runtime.prepare_turn(started, TurnRequestV2("deliver", 0, "你看一下结果。"), ())
    prepared = engine.finalize_fact_operations(started.session, prepared, operations=({
        "op": "set", "key": "scene:start:done", "value": True, "visibility": "public",
    },))
    body = f"登记已经处理完。\n{contract['fallback_offer']}"
    prepared, performance = engine.finalize_transition_offer_state(prepared, {
        "performance": body, "suggested_inputs": [contract["accept_input"]],
    }, new_offer=True)
    committed = await runtime.commit_turn(prepared, performance)
    current = await runtime.restore_session("old-offer")
    assert current == committed
    click = TurnRequestV2("accept", current.session.revision, contract["accept_input"], "suggestion")
    accepted = runtime.prepare_turn(current, click, (), transition_intent="accept")
    if offer_matches_settled_route:
        assert accepted.session.current_node_id == "archive"
    else:
        assert accepted.session.current_node_id == "start"
        assert accepted.ledger_event["transition_offer_invalidated"] is True
        accepted, response = engine.finalize_transition_offer_state(accepted, {
            "performance": "旧安排需要重新核对。", "suggested_inputs": [],
        }, new_offer=False)
        committed = await runtime.commit_turn(accepted, response)
        assert not committed.session.transition_offered
        assert committed.session.performance_history[0] == current.session.performance_history[0]
        assert await runtime.restore_session("old-offer") == committed

        legacy = await runtime.fork_session_for_test("old-offer", session_id="historical",
                                                     through_revision=current.session.revision)
        old_outcome = engine.resolve_turn(legacy.session, click, (), transition_intent="accept",
                                          ledger_events=legacy.ledger_events)
        assert old_outcome.session.current_node_id == "archive"
        old_performance = engine.finalize_transition_performance(old_outcome, {
            "source_performance": "现在走吧。", "bridge_scene_narration": "走过拱门。",
            "target_scene_narration": "档案柜排开。", "target_performance": "到了。", "suggested_inputs": [],
        }, target_opening="档案柜排开。", bridge_scene_narration="走过拱门。")
        old_committed = await runtime.commit_turn(old_outcome, old_performance)
        assert await runtime.restore_session("historical") == old_committed


@pytest.mark.asyncio
@pytest.mark.parametrize('body,offer,intent', [
    ('事情已经处理完了。', '', 'unclear'),
    ('事情已经处理完了。', '明天下午三点再一起去长街，好吗？', 'unclear'),
    ('那就明天下午三点一起去长街。', '', 'unclear'),
    ('好，今晚先不商量明天的安排。', '', 'reject'),
])
async def test_new_invitation_format_does_not_receive_a_second_authored_arrangement(
    tmp_path, monkeypatch, body, offer, intent,
):
    from services.theater.numeric_v2_actor_output import _parse_output
    from services.theater import numeric_v2_options

    engine = NumericV2Engine.from_mapping(story_for('长街'))
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='published-offer', catgirl_binding=_binding(),
        opening_performance={'performance': '先处理眼前的事。', 'suggested_inputs': []})
    setup = runtime.prepare_turn(current, TurnRequestV2('done', 0, '事情处理好了。'), (),
        fact_operations=({'op': 'set', 'key': 'scene:start:done', 'value': True,
                          'visibility': 'public'},))
    current = await runtime.commit_turn(setup, {'performance': '事情处理好了。',
        'suggested_inputs': [], 'transition_offered': False})

    async def options():
        return {key: key in {'evaluator', 'history_lookup'} for key in default_options()}

    async def evaluate(self, **kwargs):
        return ev._parse_output(json.dumps({'scene_complete': True,
            'transition_intent': intent, 'metric_changes': {}}), engine, kwargs['message'], current.session)

    async def invoke(self, messages, **kwargs):
        return _parse_output(json.dumps({'performance': body, 'transition_offer': offer,
            'suggested_inputs': ['好，那就三点见。', '先让我考虑一下。'] if offer else ['我先休息一下。']}))

    monkeypatch.setattr(numeric_v2_options, 'MODEL_REVIEWS_ENABLED', False)
    monkeypatch.setattr(workflow, 'aload_theater_module_options', options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(workflow.NumericV2Actor, '_invoke', invoke)
    monkeypatch.setattr(workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    result = await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime,
        current=current, turn=TurnRequestV2('reply', 1, '接下来呢？'),
        ensure_current_binding=lambda _: _binding())
    assert result.performance['performance'] == body + ('\n' + offer if offer else '')
    assert result.diagnostics['completion_fallback_offer_applied'] == 0
    assert result.stored.session.transition_offered is bool(offer)
    assert '_transition_offer_provided' not in json.dumps({
        'session': result.stored.session.to_dict(), 'ledger': result.stored.ledger_events})
    assert '_transition_offer_provided' not in result.performance
    assert await NumericV2Runtime(engine, tmp_path).restore_session('published-offer') == result.stored


@pytest.mark.parametrize("legacy", ["chat", "scene_action", "mixed_or_unclear"])
def test_legacy_classification_does_not_change_evaluation(legacy):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    payload = {
        "scene_complete": True,
        "transition_intent": "unclear",
        "metric_changes": {},
    }
    baseline = ev._parse_output(json.dumps(payload), engine, "你还好吗？")
    old = ev._parse_output(
        json.dumps({**payload, "interaction_intent": legacy}), engine, "你还好吗？"
    )
    assert old == baseline


def test_truncated_optional_facts_do_not_require_retired_classification():
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    content = (
        '{"public_destination_quote":"","scene_complete":false,'
        '"transition_intent":"unclear","transition_reply_target":"unclear",'
        '"metric_changes":{},"fact_candidates":[{"key":'
    )
    parsed = ev._parse_output(content, engine, "你还好吗？", finish_reason="length")
    assert not parsed.scene_complete and parsed.fact_operations == ()
    assert parsed.transition_intent == "unclear"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "destination,message",
    [
        ("长街继续调查", "其实你刚才是不是也有点紧张？"),
        ("轨道站核对星图", "这趟旅程让你有什么感受？"),
    ],
)
async def test_completed_scene_can_invite_after_subjective_reply_without_auto_accept(
    tmp_path,
    monkeypatch,
    destination,
    message,
):
    engine = NumericV2Engine.from_mapping(story_for(destination))
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(
        session_id="unified",
        catgirl_binding=_binding(),
        opening_performance={
            "performance": "这边的事情需要先处理。",
            "suggested_inputs": [],
        },
    )
    setup = runtime.prepare_turn(
        current,
        TurnRequestV2("done", 0, "眼前的事情处理完了。"),
        (),
        fact_operations=(
            {
                "op": "set",
                "key": "scene:start:done",
                "value": True,
                "visibility": "public",
            },
        ),
    )
    current = await runtime.commit_turn(
        setup,
        {
            "performance": "眼前的事情已经处理完了。",
            "suggested_inputs": [],
            "transition_offered": False,
        },
    )
    captured = []

    async def options():
        return {**{key: key == 'evaluator' for key in default_options()}, "review": True}

    async def evaluate(self, **kwargs):
        return ev._parse_output(
            json.dumps(
                {
                    "interaction_intent": "chat",
                    "scene_complete": False,
                    "transition_intent": "reject"
                    if kwargs["message"] == "先不要去。"
                    else "unclear",
                    "transition_reply_target": "pending_transition",
                    "metric_changes": {},
                }
            ),
            engine,
            kwargs["message"],
            current.session,
        )

    async def invoke(self, messages, **kwargs):
        captured.append(messages)
        return {
            "performance": "刚才确实有些紧张，现在已经安心了。",
            "suggested_inputs": [],
            "transition_offered": False,
        }

    async def review(self, **kwargs):
        if kwargs["actor_performance"]["performance"].endswith(f"要现在一起去{destination}吗？"):
            assert kwargs["actor_performance"]["suggested_inputs"][0] == "好，我们现在过去。"
            return ev.NumericV2TransitionOfferReview(True, True, (), (),
                approved_suggestion_texts=tuple(kwargs["actor_performance"].get("suggested_inputs") or []))
        return ev.NumericV2TransitionOfferReview(False, False, (), (),
            approved_suggestion_texts=tuple(kwargs["actor_performance"].get("suggested_inputs") or []))

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(
        workflow.NumericV2MetricEvaluator, "validate_transition_offer", review
    )
    monkeypatch.setattr(workflow.NumericV2Actor, "_invoke", invoke)
    monkeypatch.setattr(
        workflow.NumericV2Actor, "_character_profile", lambda self: "温和。"
    )
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(),
        runtime=runtime,
        current=current,
        turn=TurnRequestV2("respond", 1, message),
        ensure_current_binding=lambda _: _binding(),
    )
    assert len(captured) == 1
    assert result.stored.session.current_node_id == "start"
    assert result.stored.session.transition_offered is True
    assert result.performance["performance"].endswith(f"要现在一起去{destination}吗？")
    assert result.performance["suggested_inputs"][0] == "好，我们现在过去。"
    assert (
        await NumericV2Runtime(engine, tmp_path).restore_session("unified")
        == result.stored
    )
    declined = runtime.prepare_turn(
        result.stored,
        TurnRequestV2("decline", 2, "先不要去。"),
        (),
        transition_intent="reject",
    )
    assert (
        declined.session.current_node_id == "start"
        and not declined.session.transition_offered
    )
    accepted = runtime.prepare_turn(
        result.stored,
        TurnRequestV2("accept", 2, "好，我们现在过去。"),
        (),
        transition_intent="accept",
    )
    assert accepted.session.current_node_id == "ending_leave"
    rejected = await workflow.execute_numeric_v2_turn(
        config_manager=object(),
        runtime=runtime,
        current=result.stored,
        turn=TurnRequestV2("decline", 2, "先不要去。"),
        ensure_current_binding=lambda _: _binding(),
    )
    assert not rejected.stored.session.transition_offered
    assert "好，我们现在过去。" not in rejected.performance["suggested_inputs"]
    current = rejected.stored
    continued = await workflow.execute_numeric_v2_turn(
        config_manager=object(),
        runtime=runtime,
        current=current,
        turn=TurnRequestV2("continue", 3, message),
        ensure_current_binding=lambda _: _binding(),
    )
    assert not continued.stored.session.transition_offered
    assert continued.performance["performance"] == "刚才确实有些紧张，现在已经安心了。"
    assert continued.diagnostics["completion_fallback_offer_applied"] == 0
    assert "本轮确定性完成收束合同" not in captured[-1][0].content
    assert "本轮自然收束合同" not in captured[-1][0].content
    data = prompt_payload(captured[-1][1].content)
    # Historical withdrawal prevents automatic revival without classifying every later input as refusal.
    assert "不自动恢复历史撤回的提议" in data["pacing"]
    assert "玩家拒绝或暂停了上一提议" not in data["pacing"]
    assert "新的跨阶段安排仍须公开并等待接受" in data["pacing"]
    resumed = runtime.prepare_turn(
        continued.stored,
        TurnRequestV2("changed_mind", 4, "我休息好了，我们现在过去吧。"),
        (),
        transition_intent="accept",
    )
    assert resumed.session.current_node_id == "ending_leave"
