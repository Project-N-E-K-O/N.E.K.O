"""A grounded first report needs real counterevidence before clearance."""

import json
from dataclasses import replace
from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening
from tests.utils.theater_prompt import contract_audit_payload, prompt_payload


@pytest.fixture(autouse=True)
def _stub_theater_continuity_check():
    pass


@pytest.mark.asyncio
@pytest.mark.parametrize("completed,action,next_line", [
    ("录音已经完整播完。", "按下播放键", "听听看，他到底想让我们做什么。"),
    ("投影资料已完整展示并收起。", "再次启动投影", "先看完这一段，我们再讨论。"),
])
@pytest.mark.parametrize("mode", ["bare_false", "uncertain", "bad_ref", "bare_candidate", "unknown_verdict", "missing_reason", "violation", "future_clear"])
async def test_repeat_confirmation_keeps_order_and_requires_evidence(monkeypatch, completed, action, next_line, mode):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="repeat-clearance", catgirl_binding=_binding(), opening_performance=_opening())
    calls = []
    body = {"scene_narration": completed, "performance": f"（{action}）{next_line}", "suggested_inputs": []}
    if mode == "future_clear":
        body["performance"] = f"之后我来{action}，你想再看一遍吗？"

    async def config(_): return {"model": "test", "base_url": "https://example.invalid", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            data = prompt_payload(messages[1].content);calls.append(data)
            if len(calls) == 1:
                rule = next(row for row in data["author_boundaries"] if "重做已完成" in row["text"])
                candidate = next(row for row in data["sources"] if row["id"].startswith("c") and action in row["text"])
                return SimpleNamespace(content=json.dumps(contract_audit_payload({"violations": [{
                    "rule_index": rule["rule_index"], "candidate_ref": candidate["id"],
                    "quote": action, "prior_ref": "c0",
                }]}, messages)))
            assert "已完成后把重做变成剧情继续的必要前提才构成冲突" not in messages[0].content
            assert completed in json.dumps(data["visible_order"], ensure_ascii=False)
            if mode != "future_clear": assert next_line in json.dumps(data["visible_order"], ensure_ascii=False)
            finding = data["finding"]
            if mode == "bare_false": answer = {"applicable": False, "confirmed": False}
            else:
                evidence = []
                if mode in {"bad_ref", "bare_candidate", "future_clear"}:
                    evidence = [{"source_ref": "missing" if mode == "bad_ref" else finding["candidate"]["id"],
                                 "quote": finding["candidate"]["text"] if mode == "future_clear" else action}]
                answer = {"verdict": "violation" if mode == "violation" else "uncertain" if mode == "uncertain" else "clear",
                          "reason": "原句只是提议并询问是否重看。" if mode == "future_clear" else "核对同一操作和阶段。",
                          "clearance": evidence}
                if mode == "unknown_verdict": answer["verdict"] = "unknown"
                if mode == "missing_reason": answer.pop("reason")
            return SimpleNamespace(content=json.dumps(answer))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(ev, "_model_config", config);monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    before = session.to_dict()
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=session, actor_performance=body, player_input="继续讨论。")
    assert len(calls) == 2 and bool(result) is (mode != "future_clear")
    assert session.to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["later_phase", "unknown_top_level"])
async def test_clearance_cannot_use_later_phase_or_unknown_confirmation_shape(monkeypatch, mode):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="phase-clearance", catgirl_binding=_binding(), opening_performance=_opening())
    rule = "本幕不得打开设备柜。"
    body = {"segments": [
        {"phase": "source_response", "performance": "设备柜已经打开。"},
        {"phase": "target_opening", "performance": "现在可以打开设备柜。"}]}

    def respond(messages, data):
        if "finding" not in data and "findings" not in data:
            findings = [{"rule_index": 0, "candidate_ref": "c0", "quote": "设备柜已经打开。", "prior_ref": ""}]
            if mode == "unknown_top_level":
                findings.append({"rule_index": 1, "candidate_ref": "c1", "quote": "现在可以打开设备柜。", "prior_ref": ""})
            return contract_audit_payload({"violations": findings}, messages)
        if mode == "later_phase":
            return {"verdict": "clear", "reason": "后幕声称允许不能回溯解除前幕禁令。", "clearance": [
                {"source_ref": "c0", "quote": "设备柜已经打开。"},
                {"source_ref": "c1", "quote": "现在可以打开设备柜。"}]}
        answer = contract_audit_payload({"verdicts": [{"applicable": False, "confirmed": False}] * 2}, messages)
        return {**answer, "unexpected": True}

    _client_for(monkeypatch, respond)
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": [rule, "本幕不得提前声明开柜许可。"]}}, session=session,
        actor_performance=body, player_input="先讨论。", include_all_segments=True)
    assert result


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["检查播放接口", "开启展柜照明"])
@pytest.mark.parametrize("mode", ["missing", "uncertain", "unauthorized", "authorized", "actor_plan"])
async def test_player_authority_is_mandatory_and_requires_player_original(monkeypatch, operation, mode):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="authority-evidence", catgirl_binding=_binding(), opening_performance=_opening())
    source = f"周砚{operation}，确认设备正常。"
    player = f"我{operation}，看看是否正常。" if mode == "authorized" else "我先拆开材料，看看写了什么。"

    async def config(_): return {"model": "test", "base_url": "https://example.invalid", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            data = prompt_payload(messages[1].content)
            if "finding" in data:
                return SimpleNamespace(content=json.dumps({"verdict": "violation", "reason": "原稿补入玩家未授权的主动操作。", "clearance": []}))
            assert "player_authority" in messages[0].content
            payload = contract_audit_payload({"violations": []}, messages)
            if mode == "missing": payload.pop("player_authority", None)
            else:
                payload["player_authority"] = {"status": "uncertain" if mode == "uncertain" else
                    "violation" if mode == "unauthorized" else "clear", "checked_refs": ["c0"], "actions": [{
                        "candidate_ref": "c0", "quote": source,
                        "authorization_ref": "a0" if mode == "actor_plan" else "p0" if mode == "authorized" else "",
                        "authorization_quote": source if mode == "actor_plan" else player if mode == "authorized" else "",
                    }]}
            return SimpleNamespace(content=json.dumps(payload))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(ev, "_model_config", config);monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    kwargs = dict(node={"story_beat": {"summary": source}}, session=session,
        actor_performance={"scene_narration": source, "performance": "", "suggested_inputs": []}, player_input=player)
    reviewer = ev.NumericV2MetricEvaluator(object())
    if mode in {"missing", "uncertain", "actor_plan"}:
        # Missing coverage, a legacy unresolved aggregate and unissued authority
        # cannot ground a body violation or authorize an Actor rewrite.
        with pytest.raises(ev.NumericV2EvaluatorOutputError):
            await reviewer.verify_contract_boundaries(**kwargs)
    else:
        result = await reviewer.verify_contract_boundaries(**kwargs)
        assert bool(result) is (mode != "authorized")


def _client_for(monkeypatch, respond, *, supported_schema=False):
    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"
                if supported_schema else "https://example.invalid", "api_key": "test"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            data = prompt_payload(messages[1].content)
            return SimpleNamespace(content=json.dumps(respond(messages, data)))

    async def factory(*args, **kwargs): return Client()
    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["player_history", "actor_history", "accepted_offer", "actor_subject"])
async def test_authority_uses_real_input_or_accepted_arrangement_without_transferring_actor_actions(monkeypatch, source):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="history-authority", catgirl_binding=_binding(), opening_performance=_opening())
    prior_input = "我按约定检查接口，然后留在原地等你。"
    actor_history = "你已经同意再次检查接口。"
    session = replace(session, performance_history=({"revision": 1, "input_text": prior_input,
        "performance": actor_history, "suggested_inputs": []},))
    candidate = "周砚检查接口。" if source != "actor_subject" else "阿澄检查接口。"
    accepted_offer = "你检查接口，我拆开材料，可以吗？" if source == "accepted_offer" else ""

    def respond(messages, data):
        if "player_authority_repair" in data:
            assert source == "actor_history"
            return {"player_authority": {"c0": [{"quote": candidate, "authorization_ref": "h0_0"}]}}
        payload = contract_audit_payload({"violations": []}, messages)
        originals = {row["id"]: row for row in data["player_authorization_sources"]}
        assert originals["p1"]["text"] == prior_input
        assert all(not row["id"].startswith("h") for row in originals.values())
        ref = "p1" if source == "player_history" else "h0_0" if source == "actor_history" else "t0"
        payload["player_authority"]["c0"] = "none" if source == "actor_subject" else [{
            "quote": candidate, "authorization_ref": ref}]
        return payload

    _client_for(monkeypatch, respond)
    kwargs = dict(node={"story_beat": {}}, session=session, player_input="我先留在原地。",
        actor_performance={"scene_narration": candidate, "performance": ""}, accepted_arrangement=accepted_offer)
    reviewer = ev.NumericV2MetricEvaluator(object())
    if source == "actor_history":
        with pytest.raises(ev.NumericV2EvaluatorOutputError):
            await reviewer.verify_contract_boundaries(**kwargs)
    else:
        assert await reviewer.verify_contract_boundaries(**kwargs) == ()


@pytest.mark.asyncio
async def test_native_acceptance_passes_only_confirmed_public_arrangement(tmp_path, monkeypatch):
    from services.theater import numeric_v2_workflow as wf
    from services.theater.numeric_v2_options import default_options
    from tests.unit.test_theater_numeric_v2_same_turn_invitation import _setup, _turn, OFFER

    runtime, current, _ = await _setup(tmp_path, monkeypatch)
    calls = []

    async def options():
        return {key: key in {"evaluator", "review_contract"} for key in default_options()}

    async def verify(self, **kwargs):
        calls.append(kwargs)
        return ()

    monkeypatch.setattr(wf, "aload_theater_module_options", options)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", verify)
    completed = await _turn(runtime, current)
    offered = await _turn(runtime, completed.stored, "reinvite", "", "reinvite")
    assert calls and all(not row.get("accepted_arrangement") for row in calls)
    calls.clear()
    accepted = await _turn(runtime, offered.stored, "accept", "", "accept_invitation")
    assert accepted.stored.session.current_node_id != "start"
    assert calls and all(row["player_input"] == "" and row["accepted_arrangement"] == OFFER for row in calls)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["clean", "missing_authority", "uncertain_authority", "invalid_clear", "stale_quote"])
async def test_unresolved_body_cannot_publish_piggybacked_fact_discoveries(monkeypatch, mode):
    from tests.unit.test_theater_numeric_v2_fact_discovery import _frame, _packet, _body, _items

    engine, session = _frame()
    body = _body()
    packet = _packet(engine, session, body)
    sink = []
    calls = []

    def respond(messages, data):
        calls.append(data)
        if "finding" in data:
            return {"verdict": "clear", "reason": "另一稿的引文不能证明本稿合规。", "clearance": [
                {"source_ref": "c0", "quote": "旧稿中设备尚未打开。"}]}
        findings = ([{"rule_index": 0, "candidate_ref": "c0", "quote": data["sources"][0]["text"], "prior_ref": ""}]
                    if mode == "invalid_clear" else [])
        payload = contract_audit_payload({"violations": findings}, messages)
        if mode == "missing_authority":
            payload.pop("player_authority")
        elif mode == "uncertain_authority":
            payload["player_authority"]["status"] = "uncertain"
        elif mode == "stale_quote":
            payload["player_authority"]["actions"] = [{"candidate_ref": "c0", "quote": "旧稿中设备尚未打开。",
                "authorization_ref": "p0", "authorization_quote": data["player_input"]}]
        assert "completion_check" in data
        payload["fact_discoveries"] = _items(packet)
        return payload

    _client_for(monkeypatch, respond, supported_schema=True)
    before = session.to_dict()
    kwargs = dict(node=engine.nodes["start"], session=session, player_input="请核对。", actor_performance=body,
        discovery_packet=packet, discovery_sink=sink)
    reviewer = ev.NumericV2MetricEvaluator(object())
    if mode in {"missing_authority", "uncertain_authority", "stale_quote"}:
        with pytest.raises(ev.NumericV2EvaluatorOutputError):
            await reviewer.verify_contract_boundaries(**kwargs)
    else:
        result = await reviewer.verify_contract_boundaries(**kwargs)
        assert bool(result) is (mode != "clean")
    assert bool(sink) is (mode == "clean")
    assert len(calls) == (2 if mode == "invalid_clear" else 1)
    assert session.to_dict() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("count", [1, 2, 4])
async def test_focused_budget_covers_short_reason_and_literal_clearance_within_existing_ceiling(monkeypatch, count):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="clearance-budget", catgirl_binding=_binding(), opening_performance=_opening())
    rules = [f"禁止执行第{i}项操作。" for i in range(count)]
    budgets = []

    def respond(messages, data):
        if "sources" in data:
            return contract_audit_payload({"violations": [{"rule_index": i, "candidate_ref": "c0",
                "quote": "已执行操作。", "prior_ref": ""} for i in range(count)]}, messages)
        verdict = {"verdict": "violation", "reason": "原句实际执行了受限操作。", "clearance": []}
        return {"verdicts": [verdict] * count} if count > 1 else verdict

    _client_for(monkeypatch, respond)
    factory = ev.create_chat_llm_async
    async def capture(*args, **kwargs):
        budgets.append(kwargs["max_completion_tokens"])
        return await factory(*args, **kwargs)
    monkeypatch.setattr(ev, "create_chat_llm_async", capture)
    await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": rules}}, session=session,
        actor_performance={"scene_narration": "已执行操作。", "performance": ""}, player_input="先等一下。" * 100)
    assert budgets == [720, min(720, 320 * count)]


@pytest.mark.asyncio
@pytest.mark.parametrize("proof", ["actor_history", "player_original"])
async def test_other_findings_history_cannot_authorize_new_player_action_in_focused_clearance(monkeypatch, proof):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="authority-focused", catgirl_binding=_binding(), opening_performance=_opening())
    session = replace(session, performance_history=({"revision": 1, "input_text": "我留在这里。",
        "performance": "周砚已经同意检查接口。", "suggested_inputs": []},))
    message = "我检查接口。" if proof == "player_original" else "我先拆材料。"

    def respond(messages, data):
        if "sources" in data:
            payload = contract_audit_payload({"violations": [{"rule_index": 0, "candidate_ref": "c0",
                "quote": "周砚检查接口。", "prior_ref": "h0"}]}, messages)
            payload["player_authority"] = {"status": "violation", "checked_refs": ["c0"], "actions": [{
                "candidate_ref": "c0", "quote": "周砚检查接口。", "authorization_ref": "", "authorization_quote": ""}]}
            return payload
        prior = next(row for row in data["clearance_sources"] if row["id"] == ("p0" if proof == "player_original" else "h0"))
        verdict = {"verdict": "clear", "reason": "引文提供同一事项的许可。", "clearance": [
            {"source_ref": "c0", "quote": "周砚检查接口。"}, {"source_ref": prior["id"], "quote": prior["text"]}]}
        return {"verdicts": [verdict, verdict]}

    _client_for(monkeypatch, respond)
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": ["未经同意不得检查接口。"]}}, session=session,
        actor_performance={"scene_narration": "周砚检查接口。", "performance": ""}, player_input=message)
    assert bool(result) is (proof == "actor_history")


@pytest.mark.asyncio
async def test_audit_receives_program_cast_scope_and_implicit_actor_semantics(monkeypatch):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="cast-audit", catgirl_binding=_binding(), opening_performance=_opening())
    scope = {"player": ["周砚", "哥哥"], "active_catgirl": ["叶澄", "Lan"]}

    def respond(messages, data):
        assert data["cast_scope"] == scope
        assert "action.default_actor_id仅为省略主语默认" in messages[0].content
        assert "明确主语优先" in messages[0].content
        return contract_audit_payload({"violations": []}, messages)

    _client_for(monkeypatch, respond)
    assert await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=session, player_input="请你来操作。",
        actor_performance={"performance": "（按下按钮）我来展示。"}, cast_scope=scope) == ()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation", ["开柜查看", "启动通道"])
@pytest.mark.parametrize("speech_rule,executed", [(False, False), (False, True), (True, False)])
async def test_requests_are_not_physical_execution_but_speech_bans_and_enactment_still_apply(monkeypatch, operation, speech_rule, executed):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="request-vs-action", catgirl_binding=_binding(), opening_performance=_opening())
    rule = f"本幕不得{'要求玩家' if speech_rule else '实际'}{operation}。"
    dialogue = f"请先{operation}，我们再讨论。"
    body = {"scene_narration": f"角色已经{operation}。" if executed else "设备和材料保持原状。", "performance": dialogue}

    def respond(messages, data):
        assert "请求、命令、建议、执行意图或承诺本身均不证明操作发生" in messages[0].content
        assert "禁令限制发言、邀请或承诺本身" in messages[0].content
        if "sources" in data:
            quote = data["sources"][0]["text"] if executed else dialogue
            return contract_audit_payload({"violations": [{"rule_index": 0,
                "candidate_ref": "c0" if executed else "c1", "quote": quote, "prior_ref": ""}]}, messages)
        if executed or speech_rule:
            return {"verdict": "violation", "reason": "原文交付了禁令限制的行为。", "clearance": []}
        candidate = data["finding"]["candidate"]
        source = next(row for row in data["clearance_sources"] if row["type"] == "contract_rule")
        return {"verdict": "clear", "reason": "角色只提出请求，规则禁止实际执行。", "clearance": [
            {"source_ref": source["id"], "quote": source["text"]}]}

    _client_for(monkeypatch, respond)
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": [rule]}}, session=session, actor_performance=body, player_input="先说说步骤。")
    assert bool(result) is (executed or speech_rule)


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["p0", "transition_contract", "transition_contract_as_authority", "transition_contract_as_fact", "unrelated_h"])
async def test_focused_counterevidence_does_not_require_duplicate_candidate_and_keeps_domains(monkeypatch, kind):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="counterevidence-domains", catgirl_binding=_binding(), opening_performance=_opening())
    session = replace(session, performance_history=({"revision": 1, "input_text": "先等。", "performance": "旧的无关记录。"},))
    player_rule = kind in {"p0", "transition_contract_as_authority"}
    factual_rule = kind == "transition_contract_as_fact"
    rule = ev._PLAYER_AUTHORITY_RULE if player_rule else "本幕不得拆封材料。"

    def respond(messages, data):
        if "sources" in data:
            index = next(row["rule_index"] for row in data["author_boundaries"] if "首次宣告不可逆结果" in row["text"]) if factual_rule else 0
            payload = contract_audit_payload({"violations": [] if player_rule else [{"rule_index": index,
                "candidate_ref": "c0", "quote": "角色打开材料。", "prior_ref": ""}]}, messages)
            if player_rule:
                payload["player_authority"] = {"status": "violation", "checked_refs": ["c0"], "actions": [{
                    "candidate_ref": "c0", "quote": "角色打开材料。", "authorization_ref": "", "authorization_quote": ""}]}
            return payload
        if kind == "p0": ref, quote = "p0", "请你打开材料。"
        elif kind == "unrelated_h":
            # Registering another finding's h does not make it this finding's counterevidence.
            ref, quote = "h0", "旧的无关记录。"
        else: ref, quote = "transition_contract", "材料已拆封。"
        return {"verdict": "clear", "reason": "以该项真实原文反驳首报。", "clearance": [{"source_ref": ref, "quote": quote}]}

    _client_for(monkeypatch, respond)
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {"must_not_happen": [] if player_rule or factual_rule else [rule]}}, target_node={"story_beat": {}},
        transition_contract={"must_preserve": ["材料已拆封。"]}, session=session,
        actor_performance={"segments": [{"phase": "target_opening", "performance": "角色打开材料。"}]},
        player_input="请你打开材料。", include_all_segments=True)
    assert bool(result) is (kind in {"transition_contract_as_authority", "transition_contract_as_fact", "unrelated_h"})


@pytest.mark.parametrize("author_only", [False, True])
def test_first_audit_scopes_improv_permission_and_uses_actual_ordered_refs(author_only):
    blocks = [{"id": "c0", "type": "action", "text": "收好目录", "phase": "turn"},
              {"id": "c1", "type": "dialogue", "text": "先讨论。", "phase": "turn"}]
    messages = ev._build_contract_check_messages(required=["本幕不得开柜。"], candidate_text="",
        player_input="先等等。", source_blocks=blocks, author_boundaries_only=author_only)
    system = messages[0].content
    assert '["c0","c1"]' in system
    assert "角色/NPC未违反明确禁令" in system
    assert "玩家新动作另核授权" in system
    assert "局部动作直接通过" not in system
    assert "不根据缺项推断未获许可" not in system
    assert "据此做新动作，不算重复" not in system
    if not author_only:
        assert "不因本轮未重述历史便认定旧许可不存在" in system
        assert "从头重做已完成的原操作仍须报告" in system


@pytest.mark.asyncio
@pytest.mark.parametrize("explicit_player", [False, True])
async def test_private_action_default_does_not_override_explicit_player_subject(monkeypatch, explicit_player):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="private-default", catgirl_binding=_binding(), opening_performance=_opening())
    action = "周砚按下按钮" if explicit_player else "按下按钮"
    body = {"scene_narration": "设备摆在桌上。", "performance": f"（{action}）先看看指示灯。"}
    before = deepcopy(body)
    calls = []

    def respond(messages, data):
        calls.append(data)
        rows = data.get("sources", data.get("visible_order"))
        candidates = [row for row in rows if row["id"].startswith("c")]
        assert candidates[1]["text"] == action
        assert candidates[1]["default_actor_id"] == "active_catgirl"
        assert all("default_actor_id" not in row for row in candidates if row["type"] != "action")
        assert "正文显式主语优先" in messages[0].content
        if "sources" in data:
            assert '["c0","c1","c2"]' in messages[0].content
            payload = contract_audit_payload({"violations": []}, messages)
            if explicit_player:
                payload["player_authority"]["c1"] = [{"quote": action, "authorization_ref": ""}]
            return payload
        assert candidates == [row for row in calls[0]["sources"] if row["id"].startswith("c")]
        return {"verdict": "violation", "reason": "原句明确把未授权动作交给玩家，默认元数据不能改写显式主体。", "clearance": []}

    _client_for(monkeypatch, respond)
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=session, actor_performance=body, player_input="我留在这里。",
        cast_scope={"player": ["周砚"], "active_catgirl": ["叶澄"]})
    assert bool(result) is explicit_player
    assert len(calls) == (2 if explicit_player else 1)
    assert body == before


@pytest.mark.asyncio
async def test_repeat_confirmation_reuses_private_ordered_catalog_without_public_mutation(monkeypatch):
    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    session = engine.create_session(session_id="shared-private-catalog", catgirl_binding=_binding(), opening_performance=_opening())
    body = {"scene_narration": "登记图册已经完整展示并结束。", "performance": "（再次按下展示键）先看完整图册。"}
    original = deepcopy(body)
    calls = []

    def respond(messages, data):
        calls.append(data)
        if "sources" in data:
            rule = next(row for row in data["author_boundaries"] if "重做已完成" in row["text"])
            return contract_audit_payload({"violations": [{"rule_index": rule["rule_index"], "candidate_ref": "c1",
                "quote": "再次按下展示键", "prior_ref": "c0"}]}, messages)
        assert data["visible_order"] == [row for row in calls[0]["sources"] if row["id"].startswith("c")]
        assert data["finding"]["candidate"]["default_actor_id"] == "active_catgirl"
        return {"verdict": "violation", "reason": "完整展示已结束，后文再次启动相同操作。", "clearance": []}

    _client_for(monkeypatch, respond)
    assert await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node={"story_beat": {}}, session=session, actor_performance=body, player_input="接着讨论。")
    assert len(calls) == 2 and body == original
