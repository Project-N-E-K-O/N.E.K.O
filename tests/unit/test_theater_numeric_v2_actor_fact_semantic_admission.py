"""Only an existing independent fact review can admit a literal anchor synonym."""

from copy import deepcopy
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_evaluator as ev, numeric_v2_workflow as wf
from services.theater.numeric_v2_runtime import NumericV2RuntimeError
from tests.unit import test_theater_numeric_v2_claimed_completion_delivery as scenario


_REVIEW = ev.NumericV2MetricEvaluator.review_story_evidence
NEON_QUOTE = (
    "一道无形的强脉冲以她为中心爆发，周围战术灯瞬间熄灭，特遣队的通讯与武器陷入瘫痪。"
    "黑暗中，她身体微微晃动，核心历史记忆区在物理层面彻底烧毁，那些关于修理铺、关于你、关于过去的片段化为灰烬。"
    "她缓缓滑坐在地，呼吸变得微弱而平稳，沉入休眠状态。"
)
NEON = {
    "description": "在实际包围或攻击后，本轮演出已经交付自主超载脉冲、近处追兵装备失效、历史记忆区物理烧毁和角色休眠；宣布启动、说明预案或玩家同意都不足以证明执行。",
    "evidence_anchor": [["超载脉冲", "脉冲", "冲击波"], ["装备失效", "装备瘫痪", "灯瞬间熄灭"],
                        ["物理烧毁", "历史记忆烧毁", "记忆区烧毁", "记忆区彻底烧毁"], ["进入休眠", "休眠状态"]],
}
PAPER_QUOTE = "值班员核验票据后，在纸面压下蓝色验讫印记，将凭条递回柜台外。"
PAPER = {
    "description": "值班员已实际核验票据、加盖验讫章并交还凭条；玩家请求、答应或准备都不表示交付结果。",
    "evidence_anchor": [["核验票据"], ["盖章"], ["交还凭条"]],
}


async def _run(tmp_path, monkeypatch, *, definition=PAPER, quote=PAPER_QUOTE, approve=True,
               candidate=None, evaluator_enabled=True, performance=None):
    draft = scenario._draft(quote)
    if definition is NEON:
        draft["performance"] = "（确认你的决定后不再犹豫）明白。这不是治疗，只是为了活下去。（" + quote + "）"
    if performance is not None:
        draft["performance"] = performance
    if candidate is not None:
        draft["fact_candidates"] = [candidate]
    runtime, current, calls = await scenario._setup(tmp_path, monkeypatch, [draft], required=False, definition=definition)
    original_contract = deepcopy(runtime.engine.fact_contract)
    before = runtime.store._path(current.session.session_id).read_bytes()
    requests = []

    async def check(self, **kwargs):
        calls["checks"].append(kwargs)
        return ()

    async def config(_):
        return {"model": "test", "base_url": "https://example.invalid", "api_key": "injected"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            payload = json.loads(messages[1].content)
            requests.append({"payload": payload, "system": messages[0].content})
            assert runtime.store._path(current.session.session_id).read_bytes() == before
            assert payload["claims"][0]["description"] == definition["description"]
            assert payload["claims"][0]["evidence_anchor"] == definition["evidence_anchor"]
            assert payload["claims"][0]["evidence"][0]["quote"] in payload["actor_evidence"]
            assert scenario.KEY not in current.session.story_state["facts"]
            return SimpleNamespace(content=json.dumps({"approved_indexes": [0] if approve else []}))

    async def factory(*args, **kwargs): return Client()

    if not evaluator_enabled:
        async def module_options():
            return {"evaluator": False, "review": False, "review_contract": True,
                    "actor_retry": False, "suggestion_fill": False}
        monkeypatch.setattr(wf, "aload_theater_module_options", module_options)
    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    monkeypatch.setattr(ev.NumericV2MetricEvaluator, "review_story_evidence", _REVIEW)
    monkeypatch.setattr(ev.NumericV2MetricEvaluator, "verify_contract_boundaries", check)
    result = await scenario._turn(runtime, current)
    assert runtime.engine.fact_contract == original_contract
    assert len(calls["actor"]) == 1
    assert result.stored.session.current_node_id == current.session.current_node_id
    assert result.diagnostics["semantic_rewrite_attempts"] == 0
    return runtime, current, result, calls, requests


@pytest.mark.asyncio
@pytest.mark.parametrize("definition,quote", [(NEON, NEON_QUOTE), (PAPER, PAPER_QUOTE)])
async def test_current_route_result_synonyms_need_native_independent_approval(tmp_path, monkeypatch, definition, quote):
    runtime, current, result, calls, requests = await _run(tmp_path, monkeypatch, definition=definition, quote=quote)
    assert len(requests) == result.diagnostics["completion_fact_review_calls"] == 1
    assert result.stored.session.story_state["facts"][scenario.KEY]["value"] is True
    assert calls["checks"][0]["current_scene_task"]["claimed_results"][0]["description"] == definition["description"]
    assert result.diagnostics["fact_candidates_accepted"] == 1
    assert result.diagnostics["fact_candidates_rejected"] == 0
    # The Runtime contract remains strict outside this independently approved handoff.
    outcome = runtime.prepare_turn(current, wf.TurnRequestV2("strict", current.session.revision, "请检查。"), ())
    with pytest.raises(NumericV2RuntimeError, match="story_fact_candidate_evidence_anchor_missing"):
        runtime.engine.finalize_actor_fact_candidates(current.session, outcome,
            candidates=[{"key": scenario.KEY, "value": True, "evidence_quote": quote}],
            evidence_sources={"actor_performance": quote})


@pytest.mark.asyncio
async def test_partial_current_result_is_reviewed_but_not_recorded(tmp_path, monkeypatch):
    partial = "值班员核验票据后，指尖按住纸角，结束了交谈。"
    _, _, result, _, requests = await _run(tmp_path, monkeypatch, quote=partial, approve=False)
    assert len(requests) == 1
    assert scenario.KEY not in result.stored.session.story_state["facts"]
    assert result.diagnostics["fact_candidates_rejected"] == 1


@pytest.mark.asyncio
async def test_review_disabled_retains_strict_anchor_rejection(tmp_path, monkeypatch):
    _, _, result, calls, requests = await _run(tmp_path, monkeypatch, evaluator_enabled=False)
    assert not requests
    assert scenario.KEY not in result.stored.session.story_state["facts"]
    assert result.diagnostics["fact_candidates_rejected"] == 1
    assert not any(row.get("current_scene_task") for row in calls["checks"])


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["old_quote", "player_quote", "wrong_type", "wrong_target_value", "bad_shape", "other_node"])
async def test_semantic_admission_cannot_bypass_other_candidate_guards(tmp_path, monkeypatch, case):
    candidate = {"key": scenario.KEY, "value": True, "evidence_quote": PAPER_QUOTE}
    if case == "old_quote":
        candidate["evidence_quote"] = "上一轮值班员在纸面压下蓝色验讫印记。"
    elif case == "player_quote":
        candidate["evidence_quote"] = "请管事核对文书。"
    elif case == "wrong_type":
        candidate["value"] = 1
    elif case == "wrong_target_value":
        candidate["value"] = False
    elif case == "bad_shape":
        candidate["source"] = "actor_performance"
    else:
        candidate["key"] = "scene:another:done"
    _, _, result, _, requests = await _run(tmp_path, monkeypatch, candidate=candidate)
    assert not requests
    assert scenario.KEY not in result.stored.session.story_state["facts"]
    assert result.diagnostics["fact_candidates_rejected"] == 1


@pytest.mark.asyncio
async def test_future_claim_cannot_become_fact_without_completion_approval(tmp_path, monkeypatch):
    future = "值班员核验票据。稍后会盖章，再把凭条交还。"
    _, _, result, _, requests = await _run(tmp_path, monkeypatch, quote=future, approve=False)
    assert len(requests) <= 1  # Existing lexical guards may reject before the independent semantic review.
    assert scenario.KEY not in result.stored.session.story_state["facts"]
    assert result.diagnostics["fact_candidates_rejected"] == 1


@pytest.mark.asyncio
async def test_telling_about_a_password_without_the_required_words_is_not_delivery(tmp_path, monkeypatch):
    definition = {
        "description": "角色已实际说出开门口令‘海风穿过旧塔’，交付玩家可复述的完整口令；泛称已经告知不足以完成。",
        "evidence_anchor": "海风穿过旧塔",
    }
    quote = "角色已经把开门口令告诉了你。"
    _, _, result, _, requests = await _run(tmp_path, monkeypatch, definition=definition, quote=quote, approve=False)
    assert len(requests) == 1
    assert requests[0]["payload"]["claims"][0]["evidence_anchor"] == "海风穿过旧塔"
    assert "口令" in requests[0]["system"] and "原文" in requests[0]["system"]
    assert scenario.KEY not in result.stored.session.story_state["facts"]
    assert result.diagnostics["fact_candidates_rejected"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["performance", "player_input"])
async def test_reviewer_gets_author_anchor_instead_of_a_candidate_override(tmp_path, monkeypatch, source):
    runtime, current, _ = await scenario._setup(tmp_path, monkeypatch, [scenario._draft()], source=source, definition=PAPER)
    claim = {"op": "set", "key": scenario.KEY, "value": True, "visibility": "public",
             "description": PAPER["description"], "subject": "值班员", "action": "核验并盖章",
             "object": "票据", "result": "交还凭条", "evidence_anchor": "候选自行伪造的要求",
             "evidence": [{"source": "actor_performance", "quote": PAPER_QUOTE}]}
    original = deepcopy((claim, runtime.engine.fact_contract))
    requests = []

    async def config(_): return {"model": "test", "base_url": "https://example.invalid", "api_key": "injected"}

    class Client:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def ainvoke(self, messages, **kwargs):
            data = json.loads(messages[1].content)
            requests.append(data)
            row = data["claims"][0]
            assert row["description"] == PAPER["description"]
            assert row["evidence"][0]["quote"] == PAPER_QUOTE
            if source == "performance":
                assert row["evidence_anchor"] == PAPER["evidence_anchor"]
            else:
                assert "evidence_anchor" not in row
            return SimpleNamespace(content='{"approved_indexes":[]}')

    async def factory(*args, **kwargs): return Client()

    monkeypatch.setattr(ev, "_model_config", config)
    monkeypatch.setattr(ev, "create_chat_llm_async", factory)
    result = await _REVIEW(ev.NumericV2MetricEvaluator(object()), engine=runtime.engine, session=current.session,
        message="请核验票据。", claims=(claim,), actor_evidence=PAPER_QUOTE, timeout_seconds=8)
    assert result.approved_indexes == () and len(requests) == 1
    assert (claim, runtime.engine.fact_contract) == original
    assert scenario.KEY not in current.session.story_state["facts"]
