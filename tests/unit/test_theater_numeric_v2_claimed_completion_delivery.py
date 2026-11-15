"""A rejected current completion claim can expose missing delivery, without becoming fact."""

from copy import deepcopy
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_options as options, numeric_v2_workflow as wf
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_evidence_clearance import _client_for
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.test_theater_numeric_v2_unified_pacing import story_for
from tests.utils.theater_prompt import contract_audit_payload


KEY = "scene:start:done"
PARTIAL = "管事核对文书后闭眼睡去。"
COMPLETE = "管事核对文书后盖章，随后放行。"
DESCRIPTION = "本轮管事实际核对文书、盖章并放行。请求、同意或准备都不证明完成。"
ANCHOR = [["核对文书"], ["盖章"], ["放行"]]
RULE = "当前稿自报关键完成结果但漏交付。"
_VERIFY = ev.NumericV2MetricEvaluator.verify_contract_boundaries


def _draft(text=PARTIAL, *, quote=None, value=True, candidate=True):
    return {"performance": f"（{text}）先在这里等候。", "suggested_inputs": [], "transition_offered": False,
            "fact_candidates": [{"key": KEY, "value": value, "evidence_quote": text if quote is None else quote}]
            if candidate else []}


async def _setup(tmp_path, monkeypatch, drafts, *, source="performance", required=True, committed=False,
                 definition=None, approved=True):
    story = story_for("候车厅")
    story["fact_contract"]["facts"][KEY].update(
        description=DESCRIPTION, evidence_source=source, evidence_anchor=ANCHOR)
    story["fact_contract"]["facts"][KEY].update(definition or {})
    if source != "performance":
        story["fact_contract"]["facts"][KEY].pop("evidence_anchor")
    if not required:
        story["nodes"][0].pop("completion_contract")
        story["nodes"][0]["route_gates"][1]["conditions"]["all"].append(
            {"type": "fact_equals", "key": KEY, "value": True})
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id="claimed-completion", catgirl_binding=_binding(),
        opening_performance={"performance": "管事仍在柜台旁等候。", "suggested_inputs": []})
    if committed:
        outcome = runtime.prepare_turn(current, TurnRequestV2("prior", 0, "请核对文书。"), (),
            fact_operations=({"op": "set", "key": KEY, "value": True, "visibility": "public"},))
        current = await runtime.commit_turn(outcome, _draft(COMPLETE, candidate=False))
    calls = {"actor": [], "checks": [], "facts": []}

    async def module_options():
        return {"evaluator": True, "review": False, "review_contract": True,
                "actor_retry": False, "suggestion_fill": False}

    async def evaluate(self, **kwargs):
        return ev.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        calls["actor"].append(deepcopy(kwargs))
        assert kwargs["session"] == current.session
        assert kwargs["outcome"].session.story_state["facts"] == current.session.story_state["facts"]
        return deepcopy(drafts[min(len(calls["actor"]) - 1, len(drafts) - 1)])

    async def review(self, **kwargs):
        calls["facts"].append(deepcopy(kwargs))
        return ev.NumericV2StoryEvidenceReview(approved_indexes=tuple(range(len(kwargs["claims"]))) if approved else ())

    monkeypatch.setattr(options, "MODEL_REVIEWS_ENABLED", False)
    monkeypatch.setattr(wf, "aload_theater_module_options", module_options)
    monkeypatch.setattr(wf, "_can_combine_ordinary_turn", lambda *args: False)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "review_story_evidence", review)
    monkeypatch.setattr(wf.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(wf.NumericV2Actor, "_character_profile", lambda self: "自然克制。")
    return runtime, current, calls


async def _turn(runtime, current):
    return await wf._execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2("inspect", current.session.revision, "请管事核对文书。"),
        ensure_current_binding=lambda _: _binding())


@pytest.mark.asyncio
@pytest.mark.parametrize("case,expected", [
    ("partial", True), ("complete", False), ("short_quote", False), ("future", True),
    ("old_quote", False), ("wrong_value", False), ("player_source", False),
    ("optional_route", True), ("committed", False), ("no_claim", False),
])
async def test_only_bound_pending_claim_with_missing_anchor_gets_delivery_task(tmp_path, monkeypatch, case, expected):
    draft = _draft()
    if case in {"complete", "short_quote"}:
        draft = _draft(COMPLETE, quote="盖章" if case == "short_quote" else None)
    elif case == "future":
        draft = _draft("管事准备核对文书后盖章并放行。")
    elif case == "old_quote":
        draft = _draft(quote="上一轮管事已经核对文书。")
    elif case == "wrong_value":
        draft = _draft(value=False)
    elif case == "no_claim":
        draft = _draft(candidate=False)
    elif case == "player_source":
        draft = _draft(quote="请管事核对文书。")
    runtime, current, calls = await _setup(tmp_path, monkeypatch, [draft],
        source="player_input" if case == "player_source" else "performance",
        required=case != "optional_route", committed=case == "committed", approved=not expected)

    async def check(self, **kwargs):
        calls["checks"].append(kwargs)
        task = kwargs.get("current_scene_task") or {}
        assert bool(task.get("claimed_results")) is expected
        assert not kwargs.get("departure_attempt_observed")
        if expected:
            assert task["claimed_results"] == [{"key": KEY, "value": True, "description": DESCRIPTION}]
        return ()

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", check)
    result = await _turn(runtime, current)
    assert len(calls["actor"]) == 1
    assert result.stored.session.current_node_id == "start"
    if expected:
        assert KEY not in result.stored.session.story_state["facts"]
        assert result.diagnostics["fact_candidates_rejected"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("repair", ["complete", "drop_claim", "still_missing"])
async def test_missing_delivery_shares_one_rewrite_and_never_carries_old_fact_approval(
    tmp_path, monkeypatch, repair,
):
    second = (_draft(COMPLETE) if repair == "complete" else
              _draft(PARTIAL if repair == "still_missing" else "管事还在核对文书。", candidate=False))
    runtime, current, calls = await _setup(tmp_path, monkeypatch, [_draft(), second])
    path = runtime.store._path(current.session.session_id)
    before = path.read_bytes()

    async def check(self, **kwargs):
        calls["checks"].append(deepcopy(kwargs))
        assert path.read_bytes() == before
        assert kwargs["session"] == current.session
        assert kwargs["current_scene_task"]["claimed_results"][0]["key"] == KEY
        if len(calls["checks"]) == 1 or repair == "still_missing":
            kwargs["diagnostics"].update(local_delivery_missing=True,
                findings=[{"rule": RULE, "quote": PARTIAL}])
            return (RULE,)
        return ()

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", check)
    if repair == "still_missing":
        with pytest.raises(wf.NumericV2ActorOutputError, match="numeric_v2_contract_violation"):
            await _turn(runtime, current)
        assert path.read_bytes() == before
    else:
        result = await _turn(runtime, current)
        assert result.diagnostics["semantic_rewrite_attempts"] == 1
        assert result.stored.session.revision == current.session.revision + 1
        assert result.stored.session.current_node_id == "start"
        assert PARTIAL not in str(result.stored.session.performance_history)
        assert (KEY in result.stored.session.story_state["facts"]) is (repair == "complete")
        if repair == "complete":
            assert len(calls["facts"]) == 1
            assert COMPLETE in calls["facts"][0]["actor_evidence"]
            assert PARTIAL not in calls["facts"][0]["actor_evidence"]
    assert len(calls["actor"]) == 2
    # An identical rejected body reuses its rejection; changed bodies must get a fresh full check.
    assert len(calls["checks"]) == 1 if repair == "still_missing" else len(calls["checks"]) >= 2
    assert "未提交" in calls["actor"][1]["retry_hint"]
    assert await runtime.restore_session(current.session.session_id) == (current if repair == "still_missing" else result.stored)


@pytest.mark.asyncio
@pytest.mark.parametrize("missing", [True, False])
async def test_native_contract_checks_current_claim_full_body_without_departure_or_keyword_verdict(monkeypatch, missing):
    story = story_for("候车厅")
    engine = NumericV2Engine.from_mapping(story)
    session = engine.create_session(session_id="native-claimed-delivery", catgirl_binding=_binding(),
        opening_performance={"performance": "管事在柜台旁。", "suggested_inputs": []})
    body = _draft(PARTIAL if missing else "管事看清文书，在纸上压下印记，然后示意门卫打开通道。")
    diagnostics, requests = {}, []

    def respond(messages, data):
        requests.append(data)
        if "sources" in data:
            assert data["current_scene_task"]["claimed_results"][0]["description"] == DESCRIPTION
            rule = next(row for row in data["author_boundaries"] if "claimed_results" in row["text"])
            assert "同义" in rule["text"] and "玩家" in rule["text"] and "未提交" in rule["text"]
            assert not any(row.get("departure_blocker") for row in data["sources"])
            report = contract_audit_payload({"violations": []}, messages)
            if missing:
                candidate = next(row for row in data["sources"] if row["id"].startswith("c"))
                report["violations"] = [{"rule_index": rule["rule_index"], "candidate_ref": candidate["id"],
                                         "quote": PARTIAL, "prior_ref": ""}]
            return report
        assert data["current_scene_task"]["claimed_results"][0]["key"] == KEY
        return {"verdict": "violation", "reason": "当前稿在交付必要后果前已使角色无法继续回应。", "clearance": []}

    _client_for(monkeypatch, respond)
    monkeypatch.setattr(ev.NumericV2MetricEvaluator, "verify_contract_boundaries", _VERIFY)
    result = await ev.NumericV2MetricEvaluator(object()).verify_contract_boundaries(
        node=engine.nodes["start"], session=session, actor_performance=body, player_input="请核对文书。",
        current_scene_task={"claimed_results": [{"key": KEY, "value": True, "description": DESCRIPTION}]},
        diagnostics=diagnostics)
    assert bool(result) is missing
    assert diagnostics.get("local_delivery_missing", False) is missing
    assert len(requests) == 1 + int(missing)


@pytest.mark.asyncio
async def test_claimed_completion_cannot_take_a_second_semantic_rewrite(tmp_path, monkeypatch):
    runtime, current, calls = await _setup(tmp_path, monkeypatch,
        [{**_draft("管事说文书已经全部核验。", candidate=False), "scene_narration": "柜台仍在原处。"}, _draft()])
    before = runtime.store._path(current.session.session_id).read_bytes()

    async def check(self, **kwargs):
        calls["checks"].append(kwargs)
        if len(calls["checks"]) == 1:
            assert not kwargs.get("current_scene_task")
            return ("正文包含另一个已确认的冲突。",)
        assert kwargs["current_scene_task"]["claimed_results"][0]["key"] == KEY
        kwargs["diagnostics"]["local_delivery_missing"] = True
        return (RULE,)

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", check)
    with pytest.raises(wf.NumericV2ActorOutputError, match="numeric_v2_contract_violation"):
        await _turn(runtime, current)
    assert len(calls["actor"]) == len(calls["checks"]) == 2
    assert runtime.store._path(current.session.session_id).read_bytes() == before


@pytest.mark.asyncio
async def test_claimed_completion_does_not_extend_the_existing_wait_budget(tmp_path, monkeypatch):
    runtime, current, calls = await _setup(tmp_path, monkeypatch, [_draft()])
    before = runtime.store._path(current.session.session_id).read_bytes()
    real_clock, elapsed = wf.time.monotonic, [0.0]
    monkeypatch.setattr(wf, "time", SimpleNamespace(monotonic=lambda: real_clock() + elapsed[0]))

    async def check(self, **kwargs):
        calls["checks"].append(kwargs)
        assert kwargs["current_scene_task"]["claimed_results"]
        elapsed[0] = wf.NUMERIC_V2_TURN_MODEL_BUDGET_SECONDS + 1
        kwargs["diagnostics"].update(local_delivery_missing=True, findings=[{"rule": RULE, "quote": PARTIAL}])
        return (RULE,)

    monkeypatch.setattr(wf.NumericV2MetricEvaluator, "verify_contract_boundaries", check)
    with pytest.raises(wf.NumericV2ActorError):
        await _turn(runtime, current)
    assert len(calls["actor"]) == len(calls["checks"]) == 1
    assert runtime.store._path(current.session.session_id).read_bytes() == before
