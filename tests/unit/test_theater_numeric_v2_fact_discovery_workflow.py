"""Native discovery protocols, deadlines and atomic state writes with injected provider JSON."""

import asyncio
from contextvars import ContextVar
from copy import deepcopy
import json
from types import SimpleNamespace

from tests.utils.theater_prompt import contract_audit_payload, serialized_prompt_tokens
import time

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater import numeric_v2_fact_discovery as discovery
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_fact_discovery import (
    CHECKED, COMPLETE, HANDED, KEY, OTHER, TARGET_KEY, WAITING, _body, _items, _packet, _story,
)
from tests.unit.test_theater_numeric_v2_runtime import _binding
from utils.tokenize import count_tokens


# Capture actual adapters before conftest's orchestration-only autouse stubs.
_VERIFY = ev.NumericV2MetricEvaluator.verify_contract_boundaries
_REVIEW = ev.NumericV2MetricEvaluator.review_story_evidence
_STATE = ContextVar("test_native_fact_discovery")


def _schema(value):
    if isinstance(value, dict):
        if value.get("type") == "object" and "properties" in value:
            return value
        for child in value.values():
            found = _schema(child)
            if found is not None:
                return found
    return None


def _empty(schema):
    if schema.get("type") == "object":
        return {key: _empty(schema["properties"][key]) for key in schema.get("required", ())}
    return {"array": [], "boolean": False, "integer": 0}.get(
        schema.get("type"), schema.get("enum", [""])[0])


def _payload(messages):
    raw = messages[1].content
    return json.JSONDecoder().raw_decode(raw[raw.index("{"):])[0]


@pytest.fixture(autouse=True)
def native_narrow_protocols(monkeypatch):
    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", options.aload_theater_module_options)
    monkeypatch.setattr(ev.NumericV2MetricEvaluator, "verify_contract_boundaries", _VERIFY)
    monkeypatch.setattr(ev.NumericV2MetricEvaluator, "review_story_evidence", _REVIEW)
    monkeypatch.setattr(wf, "_can_combine_ordinary_turn", lambda *args: False)
    # Concurrent tests use separate pytest loops; production uses one server loop.
    monkeypatch.setattr(wf, "character_config_mutation_lock", asyncio.Lock())
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "自然克制。")

    class Client:
        def __init__(self, record):
            self.record = record

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    async def config(_):
        return {"model": "qwen3.8-flash", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "api_key": "injected", "provider_type": "openai"}

    async def factory(*args, **kwargs):
        record = deepcopy(kwargs)
        _STATE.get().client_options.append(record)
        return Client(record)

    async def evaluate(self, **kwargs):
        accepting = kwargs["message"] == "好，我们现在过去。"
        return ev.NumericV2EvaluationResult((), False,
            transition_intent="accept" if accepting else "unclear",
            public_destination_quote=kwargs["message"] if accepting else "")

    async def generate(self, **kwargs):
        state = _STATE.get()
        state.actor_inputs.append(deepcopy(kwargs))
        outcome = kwargs["outcome"]
        if outcome.ledger_event["from_node_id"] != outcome.ledger_event["to_node_id"]:
            return kwargs["engine"].finalize_transition_performance(outcome, {
                "source_performance": "（起身）好，证件带好了。", "bridge_scene_narration": "两人进入候车厅。",
                "target_scene_narration": "行李架靠在候车厅墙边，行李尚未整理。",
                "target_performance": "（停在架子旁）先在这里等候。", "suggested_inputs": [],
            }, target_opening="行李架靠在候车厅墙边，行李尚未整理。")
        if outcome.session.current_node_id != "start":
            return _body("行李还未整理。")
        state.draft_index += 1
        return deepcopy(state.drafts[min(state.draft_index, len(state.drafts) - 1)])

    async def invoke(client, messages, *, stage, response_format=None):
        state = _STATE.get()
        data = _payload(messages)
        schema = _schema(response_format)
        assert schema is not None
        answer = _empty(schema)
        request = {"stage": stage, "data": data, "system": messages[0].content,
                   "messages": [message.content for message in messages], "schema": deepcopy(schema)}
        state.requests.append(request)
        client.record["stage"] = stage
        if stage.startswith("contract"):
            if "offer_actionable" in answer:
                answer["offer_actionable"] = True
            if stage == "contract":
                check = data.get("completion_check", {})
                discoveries = []
                for index, target in enumerate(check.get("targets", ())):
                    if target["key"] not in {KEY, OTHER} or not state.discover:
                        continue
                    refs = []
                    for quote in state.quotes:
                        matches = [row["ref"] for row in check["sources"] if row["text"] == quote]
                        if matches:
                            refs.append(matches[-1])
                    if refs:
                        discoveries.append({"target_index": index, "refs": refs})
                if "fact_discoveries" in answer:
                    answer["fact_discoveries"] = discoveries
                if state.prior_review_ms is not None:
                    state.diagnostics["timings_ms"]["transition_judge_work"] = state.prior_review_ms
                    state.prior_review_ms = None
            if state.reject_draft and state.draft_index == 0:
                candidate = next((row for row in data.get("sources", ())
                                  if str(row.get("id", "")).startswith("c")
                                  and "两人已抵达候车厅。" in row.get("text", "")), None)
                if "violations" in answer and candidate:
                    rule = next(row["rule_index"] for row in data["author_boundaries"]
                                if "不得把进入候车厅" in row["text"])
                    answer["violations"] = [{"rule_index": rule, "candidate_ref": candidate["id"],
                                               "quote": "两人已抵达候车厅。", "prior_ref": ""}]
                if "applicable" in answer:
                    answer.update(applicable=True, confirmed=True)
                if "verdicts" in answer:
                    answer["verdicts"] = [{"applicable": True, "confirmed": True}]
            if "departure_scope" in answer:
                answer["departure_scope"] = [{"scope": "current_scene", "candidate_ref": row["id"], "quote": row["text"]}
                    for row in data.get("sources", ()) if str(row.get("id", "")).startswith("c")]
        elif stage == "story_evidence":
            if data.get("claims"):
                state.fact_audits += 1
            if state.barrier is not None:
                await state.barrier.wait()
            table = {row["ref"]: row for row in data.get("evidence_source_table", ())}
            for row in data.get("evidence_bindings", ()):
                claim = data["claims"][row["claim_index"]]
                for ref, evidence in zip(row["refs"], claim["evidence"]):
                    assert evidence["source"] == table[ref]["source"]
                    assert evidence["quote"] in table[ref]["text"]
            if state.marker and table:
                text = json.dumps(data, ensure_ascii=False)
                assert state.marker + "号通行证" in text
                assert ("乙" if state.marker == "甲" else "甲") + "号通行证" not in text
            delay = state.delays[min(max(0, state.fact_audits - 1), len(state.delays) - 1)]
            try:
                if delay:
                    await asyncio.sleep(delay)
            finally:
                if delay:
                    state.cancel_cleanup.append(state.fact_audits)
            if "approved_indexes" in answer:
                verdict = state.verdicts[min(max(0, state.fact_audits - 1), len(state.verdicts) - 1)]
                answer["approved_indexes"] = (list(range(len(data.get("claims", ())))) if verdict is True
                                              else [] if verdict is False else verdict)
            if "offer_decision" in answer and data.get("player_input") == "好，我们现在过去。":
                answer.update(offer_decision="accept", offer_quote=data["player_input"], offer_reason="接受当前邀约。")
        else:
            raise AssertionError("Unexpected provider stage: " + stage)
        request["response"] = answer
        return SimpleNamespace(content=json.dumps(contract_audit_payload(answer, messages), ensure_ascii=False), response_metadata={})

    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    monkeypatch.setattr(ev, "invoke_with_usage", invoke)
    monkeypatch.setattr(ev.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)


async def _setup(path, *, source="performance", last=True, two=False, actor=False, history=False, marker=""):
    story = _story(source=source, last=last, two=two)
    body = _body(candidate=actor)
    if marker:
        for field in ("scene_narration", "performance"):
            body[field] = body[field].replace("通行证", marker + "号通行证")
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), path)
    current = await runtime.start_session(session_id="discovery_" + ("A" if marker == "甲" else "B" if marker else "test"),
        catgirl_binding=_binding(), opening_performance=_body("柜台上有一张尚未核对的通行证。"))
    quotes = [body["scene_narration"]]
    if history:
        outcome = runtime.prepare_turn(current, TurnRequestV2("prior", 0, "请先核对。"), ())
        current = await runtime.commit_turn(outcome, _body(CHECKED))
        body = _body(HANDED)
        quotes = [CHECKED, HANDED]
    state = SimpleNamespace(drafts=[body], draft_index=-1, quotes=quotes, discover=True,
        requests=[], actor_inputs=[], client_options=[], fact_audits=0, verdicts=[True], delays=[0],
        cancel_cleanup=[], diagnostics={}, prior_review_ms=None, reject_draft=False, marker=marker, barrier=None)
    return runtime, current, state


async def _run(runtime, current, state, *, message="请工作人员核对并交还通行证。", source="freeform", key="inspect"):
    token = _STATE.set(state)
    state.diagnostics = {}
    try:
        return await wf._execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
            turn=TurnRequestV2(key, current.session.revision, message, source), ensure_current_binding=lambda _: _binding(),
            diagnostics_sink=state.diagnostics)
    finally:
        _STATE.reset(token)


def _fact_requests(state):
    return [row["data"] for row in state.requests if row["stage"] == "story_evidence" and row["data"].get("claims")]


@pytest.mark.asyncio
async def test_actor_and_omitted_fact_share_one_independent_bound_review(tmp_path):
    runtime, current, state = await _setup(tmp_path, two=True, actor=True)
    # The Actor names the completed passport work; discovery also proposes
    # luggage inspection, which the actual body does not establish.
    state.verdicts = [[0]]
    result = await _run(runtime, current, state)
    audits = _fact_requests(state)
    assert len(audits) == 1
    assert [row["key"] for row in audits[0]["claims"]] == [KEY, OTHER]
    assert len(audits[0]["evidence_bindings"]) == 2
    assert result.stored.session.story_state["facts"][KEY]["value"] is True
    assert OTHER not in result.stored.session.story_state["facts"]
    assert not result.stored.session.transition_offered
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
@pytest.mark.parametrize("verdict", [True, False, "timeout"])
async def test_merged_fact_review_preserves_approval_failure_and_final_recheck(tmp_path, monkeypatch, verdict):
    runtime, current, state = await _setup(tmp_path, two=True, actor=True)
    state.verdicts = [verdict is True]
    if verdict is True:
        text = COMPLETE + "工作人员随后完成了独立的行李检查。"
        state.drafts = [_body(text, candidate=True)]
        state.quotes = [text]
    if verdict == "timeout":
        monkeypatch.setattr(wf, "NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS", 0.03)
        state.delays = [0.2]
    result = await _run(runtime, current, state)
    facts = result.stored.session.story_state["facts"]
    if verdict is True:
        assert {KEY, OTHER} <= set(facts)
        assert result.stored.session.transition_offered
        # Adding the invitation changes the body: its bound discovery still
        # receives the existing final-draft review after the shared initial one.
        assert len(_fact_requests(state)) == 2
        assert [row["key"] for row in _fact_requests(state)[1]["claims"]] == [OTHER]
    else:
        assert KEY not in facts and OTHER not in facts
        assert not result.stored.session.transition_offered
        assert len(_fact_requests(state)) == 1
    assert result.diagnostics["completion_fact_reviews_merged"] == 1
    if verdict == "timeout":
        assert state.cancel_cleanup == [1]
        assert result.diagnostics["transition_judge_degraded"]
    assert await runtime.restore_session(current.session.session_id) == result.stored


@pytest.mark.asyncio
async def test_merged_review_over_input_budget_preserves_original_separate_approvals(tmp_path):
    _, _, state = await _setup(tmp_path / "fixture", two=True, actor=True)
    story = _story(two=True)
    extra_keys = [f"scene:start:extra_{index}" for index in range(3)]
    for index, key in enumerate(extra_keys):
        story["fact_contract"]["facts"][key] = {
            "value_type": "bool", "visibility": "public", "evidence_source": "performance",
            "description": f"工作人员已完成第{index + 1}项核验。"}
        story["nodes"][0]["completion_contract"]["all"].append({"key": key, "equals": True})
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path / "actual")
    current = await runtime.start_session(session_id="merged-budget", catgirl_binding=_binding(),
        opening_performance=_body("柜台上有一张尚未核对的通行证。"))
    text = COMPLETE + "工作人员已完成独立的行李检查，且完成其余三项核验。" + "柜台照明保持稳定。" * 120
    body = _body(text)
    body["fact_candidates"] = [{"key": key, "value": True, "evidence_quote": text}
                               for key in [KEY, *extra_keys]]
    state.drafts, state.quotes = [body], [text]
    result = await _run(runtime, current, state)
    assert {KEY, OTHER, *extra_keys} <= set(result.stored.session.story_state["facts"])
    # The combined packet is rejected before any provider request; both
    # original review packets fit without trimming evidence or raising caps.
    audits = _fact_requests(state)
    assert [len(row["claims"]) for row in audits] == [4, 1, 1]
    assert "evidence_source_table" not in audits[0]
    assert "evidence_source_table" in audits[1]
    assert not result.diagnostics["transition_judge_degraded"]
    assert not result.diagnostics.get("completion_fact_reviews_merged")


@pytest.mark.asyncio
async def test_discovery_over_budget_falls_back_to_identical_contract_request(tmp_path, monkeypatch):
    runtime, current, state = await _setup(tmp_path)
    reviewer = ev.NumericV2MetricEvaluator(object())
    kwargs = {"node": runtime.engine.nodes["start"], "session": current.session,
              "actor_performance": state.drafts[0], "player_input": "请核对。", "include_all_segments": True}
    token = _STATE.set(state)
    try:
        assert await reviewer.verify_contract_boundaries(**kwargs) == ()
        baseline = state.requests[-1]
        original = ev.numeric_v2_actor_budget
        limit = serialized_prompt_tokens(*baseline["messages"])
        monkeypatch.setattr(ev, "numeric_v2_actor_budget", lambda profile: {
            **original(profile), "judge_input_max_tokens": limit})
        packet = _packet(runtime.engine, current.session, state.drafts[0], "请核对。")
        sink = []
        assert await reviewer.verify_contract_boundaries(**kwargs, discovery_packet=packet, discovery_sink=sink) == ()
        assert not sink
        assert state.requests[-1]["messages"] == baseline["messages"]
        assert state.requests[-1]["schema"] == baseline["schema"]
        assert "completion_check" not in state.requests[-1]["data"]
    finally:
        _STATE.reset(token)


@pytest.mark.asyncio
async def test_oversized_bound_batch_does_not_trim_evidence_or_change_native_actor_request(tmp_path):
    runtime, current, state = await _setup(tmp_path)
    engine, session, body = runtime.engine, current.session, state.drafts[0]
    packet = discovery.build_packet(engine, session, body, "请核对。",
        pending_targets=ev._pending_completion_facts(engine, session), history_metadata={"allowed_keys": [KEY]},
        history_originals=("完整历史原文。" * 10000,))
    batch = discovery.parse_discoveries(_items(packet), packet)
    batch.validate()
    snapshot = deepcopy(batch)
    reviewer = ev.NumericV2MetricEvaluator(object())
    kwargs = {"engine": engine, "session": session, "message": "请核对。", "claims": batch.claims,
              "actor_evidence": "\n".join(body[field] for field in ("scene_narration", "performance")),
              "timeout_seconds": 1}
    token = _STATE.set(state)
    try:
        assert (await reviewer.review_story_evidence(**kwargs)).approved_indexes == (0,)
        baseline = state.requests[-1]
        with pytest.raises(ev.NumericV2EvaluatorError, match="budget"):
            await reviewer.review_story_evidence(**kwargs, evidence_batch=batch)
        assert batch == snapshot
        assert len(state.requests) == 1
        assert (await reviewer.review_story_evidence(**kwargs)).approved_indexes == (0,)
        assert state.requests[-1]["messages"] == baseline["messages"]
        assert state.requests[-1]["schema"] == baseline["schema"]
        assert "evidence_source_table" not in baseline["data"]
    finally:
        _STATE.reset(token)


@pytest.mark.asyncio
async def test_evidence_batch_cannot_approve_different_claims(tmp_path):
    runtime, current, state = await _setup(tmp_path)
    packet = _packet(runtime.engine, current.session, state.drafts[0])
    batch = discovery.parse_discoveries(_items(packet), packet)
    token = _STATE.set(state)
    try:
        with pytest.raises(ev.NumericV2EvaluatorError, match="bound_mode_invalid"):
            await ev.NumericV2MetricEvaluator(object()).review_story_evidence(
                engine=runtime.engine, session=current.session, message="请核对。",
                claims=({**batch.claims[0], "value": False},),
                actor_evidence=COMPLETE, timeout_seconds=1, evidence_batch=batch)
        assert not state.requests
    finally:
        _STATE.reset(token)


@pytest.mark.asyncio
@pytest.mark.parametrize("actor,history,combined", [(False, False, False), (True, False, False), (False, True, False), (False, False, True)])
async def test_fact_delivery_survives_final_invitation_and_restore(tmp_path, monkeypatch, actor, history, combined):
    runtime, current, state = await _setup(tmp_path, actor=actor, history=history)
    if combined:
        monkeypatch.setattr(wf, "_can_combine_ordinary_turn", lambda *args: True)
        state.drafts[0]["_turn_evaluation"] = {"scene_complete": False, "metric_changes": {}, "fact_candidates": []}
    before = deepcopy(current)
    result = await _run(runtime, current, state)
    assert current == before
    assert result.stored.session.current_node_id == "start"
    assert result.stored.session.story_state["facts"][KEY]["value"] is True
    assert result.stored.session.transition_offered
    assert await runtime.restore_session(current.session.session_id) == result.stored
    fork = await runtime.fork_session_for_test(current.session.session_id, session_id="fork", through_revision=result.stored.session.revision)
    assert fork.session.story_state == result.stored.session.story_state
    assert fork.session.performance_history == result.stored.session.performance_history
    audits = _fact_requests(state)
    assert len(audits) == (1 if actor else 2)
    if actor:
        assert "evidence_source_table" not in audits[0]
    else:
        assert audits[0]["claims"] == audits[1]["claims"]
        assert result.performance["performance"] in [row["text"] for row in audits[1]["evidence_source_table"]]
        if history:
            assert {"committed_current_visit", "current_draft"} <= {row["kind"] for row in audits[0]["evidence_source_table"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["future", "partial", "player_required"])
async def test_request_partial_result_and_npc_do_not_complete_player_action(tmp_path, case):
    runtime, current, state = await _setup(tmp_path, source="player_input" if case == "player_required" else "performance")
    text = CHECKED if case == "partial" else "工作人员说：我会核对后再交给你。"
    state.drafts = [_body(text)]
    state.quotes = [text]
    state.verdicts = [False]
    result = await _run(runtime, current, state)
    assert KEY not in result.stored.session.story_state["facts"]
    assert not result.stored.session.transition_offered
    assert result.stored.session.revision == current.session.revision + 1
    assert len(_fact_requests(state)) == (0 if case == "player_required" else 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["reject", "partial", "timeout"])
async def test_final_fact_failure_rolls_back_body_and_new_facts(tmp_path, monkeypatch, case):
    runtime, current, state = await _setup(tmp_path, two=case == "partial")
    state.verdicts = [True, [0] if case == "partial" else False]
    if case == "timeout":
        monkeypatch.setattr(wf, "NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS", 0.03)
        state.delays = [0, 0.2]
    before = runtime.store._path(current.session.session_id).read_bytes()
    with pytest.raises(wf.NumericV2ActorOutputError):
        await _run(runtime, current, state)
    assert runtime.store._path(current.session.session_id).read_bytes() == before
    assert await runtime.restore_session(current.session.session_id) == current
    assert len(_fact_requests(state)) == 2
    if case == "timeout":
        assert state.cancel_cleanup == [2]
        assert state.diagnostics["transition_judge_degraded"]


@pytest.mark.asyncio
async def test_initial_fact_timeout_commits_only_legal_body(tmp_path, monkeypatch):
    runtime, current, state = await _setup(tmp_path)
    monkeypatch.setattr(wf, "NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS", 0.03)
    state.delays = [0.2]
    result = await _run(runtime, current, state)
    assert state.cancel_cleanup == [1]
    assert KEY not in result.stored.session.story_state["facts"]
    assert not result.stored.session.transition_offered
    assert result.diagnostics["transition_judge_degraded"]


@pytest.mark.asyncio
async def test_shared_review_budget_is_not_reset_for_final_review(tmp_path):
    runtime, current, state = await _setup(tmp_path)
    state.prior_review_ms = 19850.0
    state.delays = [0.02, 0.3]
    before = runtime.store._path(current.session.session_id).read_bytes()
    with pytest.raises(wf.NumericV2ActorOutputError):
        await _run(runtime, current, state)
    assert len(_fact_requests(state)) == 2
    calls = [row for row in state.client_options if row["stage"] == "story_evidence"]
    assert calls[1]["timeout"] < calls[0]["timeout"] <= 0.151
    assert runtime.store._path(current.session.session_id).read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["initial", "final"])
async def test_exhausted_model_budget_does_not_start_fact_provider(tmp_path, monkeypatch, phase):
    runtime, current, state = await _setup(tmp_path)
    elapsed = [0.0]
    monkeypatch.setattr(wf, "time", SimpleNamespace(monotonic=lambda: time.monotonic() + elapsed[0]))
    before = runtime.store._path(current.session.session_id).read_bytes()
    if phase == "initial":
        async def verified(self, **kwargs):
            result = await _VERIFY(self, **kwargs)
            elapsed[0] = 59.6
            return result
        monkeypatch.setattr(ev.NumericV2MetricEvaluator, "verify_contract_boundaries", verified)
        result = await _run(runtime, current, state)
        assert KEY not in result.stored.session.story_state["facts"]
        assert not _fact_requests(state)
    else:
        original = wf.rebind_completion_evidence

        def rebound(*args):
            result = original(*args)
            elapsed[0] = 59.6
            return result

        monkeypatch.setattr(wf, "rebind_completion_evidence", rebound)
        with pytest.raises(wf.NumericV2ActorOutputError, match="discovery_final_review_failed"):
            await _run(runtime, current, state)
        assert len(_fact_requests(state)) == 1
        assert runtime.store._path(current.session.session_id).read_bytes() == before
    assert state.diagnostics["review_budget_skips"] >= 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", ["negation", "removed", "other_field"])
async def test_final_body_changes_cannot_keep_the_original_fact_approval(tmp_path, monkeypatch, mutation):
    runtime, current, state = await _setup(tmp_path)
    state.verdicts = [True, False]
    original = runtime.engine.finalize_transition_offer_state
    mutated = []

    def alter(outcome, performance, **kwargs):
        outcome, performance = original(outcome, performance, **kwargs)
        if (not mutated and performance.get("scene_narration") == COMPLETE
                and any(row.get("key") == KEY for row in outcome.ledger_event.get("fact_operations", ()))):
            performance = deepcopy(performance)
            if mutation == "negation":
                performance["scene_narration"] += "但工作人员随即收回证件，核验结果已作废。"
            elif mutation == "removed":
                performance["scene_narration"] = WAITING
            else:
                performance["performance"] += performance.pop("scene_narration")
            mutated.append(True)
        return outcome, performance

    monkeypatch.setattr(runtime.engine, "finalize_transition_offer_state", alter)
    before = runtime.store._path(current.session.session_id).read_bytes()
    with pytest.raises(wf.NumericV2ActorOutputError, match="discovery_final"):
        await _run(runtime, current, state)
    assert mutated
    assert runtime.store._path(current.session.session_id).read_bytes() == before
    assert await runtime.restore_session(current.session.session_id) == current
    audits = _fact_requests(state)
    assert len(audits) == (2 if mutation == "negation" else 1)
    if mutation == "negation":
        assert any("已作废" in row["text"] for row in audits[-1]["evidence_source_table"])
        assert audits[0]["claims"] == audits[-1]["claims"]


@pytest.mark.asyncio
async def test_already_approved_evaluator_key_does_not_gain_discovery_review(tmp_path, monkeypatch):
    runtime, current, state = await _setup(tmp_path, actor=True)
    # This fact was genuinely performed in the opening but not yet registered.
    runtime = NumericV2Runtime(runtime.engine, tmp_path / "history")
    current = await runtime.start_session(session_id="prior-native", catgirl_binding=_binding(),
        opening_performance=_body(COMPLETE))

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False,
            fact_operations=({"op": "set", "key": KEY, "value": True, "visibility": "public"},),
            fact_audit=({"key": KEY, "subject": "工作人员", "action": "核对并交还", "object": "通行证",
                         "result": "已完成", "evidence": [{"source": "actor_performance", "quote": COMPLETE}]},))

    monkeypatch.setattr(ev.NumericV2MetricEvaluator, "evaluate", evaluate)
    result = await _run(runtime, current, state)
    assert result.stored.session.story_state["facts"][KEY]["value"] is True
    assert len(_fact_requests(state)) == 1
    assert "evidence_source_table" not in _fact_requests(state)[0]


@pytest.mark.asyncio
async def test_rejected_draft_discoveries_do_not_enter_repaired_commit(tmp_path):
    runtime, current, state = await _setup(tmp_path)
    rejected = COMPLETE + "两人已抵达候车厅。"
    state.drafts = [_body(rejected), _body(WAITING)]
    state.quotes = [rejected]
    state.reject_draft = True
    result = await _run(runtime, current, state)
    assert len(state.actor_inputs) == 2
    assert not _fact_requests(state)
    assert KEY not in result.stored.session.story_state["facts"]
    assert rejected not in json.dumps(result.stored.session.performance_history, ensure_ascii=False)
    assert rejected not in json.dumps(result.stored.ledger_events, ensure_ascii=False)


@pytest.mark.asyncio
async def test_completion_then_actual_acceptance_transitions_once(tmp_path):
    runtime, current, state = await _setup(tmp_path)
    first = await _run(runtime, current, state)
    acceptance = first.performance["suggested_inputs"][0]
    assert acceptance == "好，我们现在过去。"
    moved = await _run(runtime, first.stored, state, message=acceptance, source="suggestion", key="accept")
    assert moved.stored.session.current_node_id == "ending_leave"
    waiting = await _run(runtime, moved.stored, state, message="先等一会儿，行李不要整理。", key="wait")
    events = waiting.stored.ledger_events
    assert sum(row["from_node_id"] != row["to_node_id"] for row in events) == 1
    assert sum(part.get("phase") == "target_opening" for row in waiting.stored.session.performance_history
               for part in row.get("segments", ())) == 1
    assert TARGET_KEY not in waiting.stored.session.story_state["facts"]
    before = runtime.store._path(current.session.session_id).read_bytes()
    with pytest.raises(Exception, match="revision|duplicate"):
        runtime.prepare_turn(waiting.stored, TurnRequestV2("accept", first.stored.session.revision, acceptance, "suggestion"), ())
    assert runtime.store._path(current.session.session_id).read_bytes() == before


@pytest.mark.asyncio
@pytest.mark.parametrize("shared", [False, True])
async def test_concurrent_fact_review_has_own_sources_and_one_commit_per_revision(tmp_path, shared):
    runtime, current, a = await _setup(tmp_path / "a", last=False, marker="甲")
    other_runtime, other_current, b = await _setup(tmp_path / "b", last=False, marker="乙")
    if shared:
        other_runtime, other_current = runtime, current
    a.barrier = b.barrier = asyncio.Barrier(2)
    results = await asyncio.gather(_run(runtime, current, a), _run(other_runtime, other_current, b), return_exceptions=True)
    assert sum(not isinstance(result, Exception) for result in results) == (1 if shared else 2), [str(result) for result in results]
    if shared:
        assert sum("numeric_base_revision_mismatch" in str(result) for result in results) == 1
        restored = await runtime.restore_session(current.session.session_id)
        assert restored.session.revision == current.session.revision + 1
        assert len(restored.ledger_events) == 1
    assert _STATE.get(None) is None
