"""Delivered originals reach native ledger models; unapproved text never gains state."""

from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from services.theater import numeric_v2_background_narrative as background
from services.theater import numeric_v2_narrative_proposer as proposer
from tests.unit.test_theater_numeric_v2_narrative_proposer import _wire_events
from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_narrative import RECORD_SCHEMA
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story
from tests.unit.test_theater_numeric_v2_narrative_degradation import _parsed_review
from tests.unit.test_theater_numeric_v2_runtime import _binding


ACTION = "（指尖碰到灯座）我先看看。"
SCENE = "窗边的风停了。"
ORIGINALS = ("警灯转为红色，照亮了墙角。", "警灯随后转为绿色，恢复稳定。")
_NATIVE_PROPOSE = proposer.NumericV2NarrativeProposer.propose


def _events():
    # Model table order is deliberately reversed; actual display order determines the final state.
    return [{"phase": "turn", "subject": "environment:警灯", "kind": "action",
             "changes": [{"entity": "environment:警灯", "attribute": "condition", "value": color}],
             "source": "scene_narration", "quote": quote, "known_to": ["player", "character"]}
            for quote, color in reversed(tuple(zip(ORIGINALS, ("红色", "绿色"))))]


async def _setup(tmp_path, monkeypatch, mode="ok"):
    story = numeric_v2_story()
    story["nodes"][0]["story_beat"]["fixed_narrations"] = [
        {"id": f"light_{index}", "text": text,
         "trigger": {"type": "condition", "condition": "猫娘实际触碰灯座。", "player_handoff_required": False},
         "after": [] if index == 0 else ["light_0"], "required_before_exit": False}
        for index, text in enumerate(ORIGINALS)
    ]
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(story), tmp_path)
    current = await runtime.start_session(session_id="fixed-ledger", catgirl_binding=_binding(),
        opening_performance={"performance": "灯座尚未触碰。", "suggested_inputs": [],
                             "narrative_record": {"schema": RECORD_SCHEMA, "coverage": "confirmed_no_change",
                                                  "events": [], "proposal_count": 0}})
    calls = {"actor": 0, "body": 0, "proposal": 0, "ledger": 0}
    requests, clients = [], []

    async def options():
        return {"evaluator": True, "review": mode != "review_disabled", "dispute": False}

    async def evaluate(self, **kwargs):
        return workflow.NumericV2EvaluationResult((), False)

    async def generate(self, **kwargs):
        calls["actor"] += 1
        if calls["actor"] == 2:
            state = kwargs["session"].narrative_state["entities"]["environment:警灯"]["condition"]
            assert state["value"] == "绿色" and state["current"] is True
            assert [item["text"] for item in kwargs["session"].performance_history[-1]["fixed_narrations"]] == list(ORIGINALS)
        return {"performance": ACTION if calls["actor"] == 1 else "你可以继续说。",
                "scene_narration": SCENE, "suggested_inputs": []}

    async def body_review(self, **kwargs):
        calls["body"] += 1
        review = _parsed_review(kwargs["actor_performance"], kwargs["message"])
        claims = tuple({"id": f"light_{index}", "evidence": "指尖碰到灯座"}
                       for index in range(2)) if calls["body"] == 1 and mode != "trigger_denied" else ()
        return replace(review, fixed_narration_triggers=claims)

    async def config(_):
        return {"model": "test", "base_url": "http://test.invalid"}

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def ainvoke(self, messages, **kwargs):
            data = json.loads(messages[1].content)
            stage = "ledger" if "narrative_events" in data else "proposal"
            calls[stage] += 1
            requests.append({"stage": stage, "data": data})
            if mode == "provider_failed":
                raise RuntimeError("isolated model transport failure")
            if stage == "ledger":
                payload = {"approved_narrative_event_indexes": [] if mode == "ledger_denied" else list(range(len(data["narrative_events"]))),
                           "narrative_review_complete": mode != "ledger_denied"}
            elif mode == "sync":
                payload = {"narrative_events": [{"phase": "turn", "subject": "character", "kind": "action",
                    "changes": [{"entity": "character", "attribute": "condition", "value": "先看看"}],
                    "source": "performance", "quote": ACTION, "known_to": ["player", "character"]}]}
            else:
                # In the denied-trigger case this deliberately untrusted reply cites undisplayed text;
                # native source validation must reject it before any ledger approval can run.
                payload = {"narrative_events": _events() if calls["proposal"] == 1 else []}
            if "source_texts" in data:
                # The fake model speaks the provider-only format; the real proposer performs source binding.
                payload["narrative_events"] = _wire_events(payload["narrative_events"], data["source_texts"])
            return SimpleNamespace(content=json.dumps(payload, ensure_ascii=False))

    async def factory(*args, **kwargs):
        clients.append(kwargs)
        return Client()

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", body_review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda _: "温和。")
    # 父级 autouse fixture 会将提案替成空表；仅恢复真正方法，本组只替换模型传输。
    monkeypatch.setattr(proposer.NumericV2NarrativeProposer, "propose", _NATIVE_PROPOSE)
    monkeypatch.setattr(proposer, "_model_config", config)
    monkeypatch.setattr(proposer, "create_chat_llm_async", factory)

    async def execute(stored=current, *, background_narrative=True):
        return await workflow.execute_numeric_v2_turn(config_manager=object(), runtime=runtime, current=stored,
            turn=TurnRequestV2(f"fixed_{stored.session.revision + 1}", stored.session.revision, "请检查灯座。"),
            ensure_current_binding=lambda _: _binding(), background_narrative=background_narrative)

    return runtime, current, execute, calls, requests, clients


@pytest.mark.asyncio
async def test_delivered_originals_reach_both_native_calls_and_persist_in_actual_order(tmp_path, monkeypatch):
    runtime, current, execute, calls, requests, clients = await _setup(tmp_path, monkeypatch)
    first = await execute()
    delivered = deepcopy(first.performance)
    settled = await background.settle_before_input(runtime, first.stored)
    assert [item["text"] for item in delivered["fixed_narrations"]] == list(ORIGINALS)
    assert requests and requests[0]["stage"] == "proposal"
    expected = [{"source": "scene_narration", "text": SCENE}, {"source": "performance", "text": ACTION},
                *({"source": "scene_narration", "text": text} for text in ORIGINALS)]
    # These are the actual native request payloads, not a replacement proposer/reviewer implementation.
    for request in requests:
        if request["stage"] == "proposal":
            assert [{key: row[key] for key in ("source", "text")}
                    for row in request["data"]["source_texts"] if row["phase"] == "turn"] == expected
        else:
            row = next(row for row in request["data"]["phase_texts"] if row["phase"] == "turn")
            assert row.get("ordered_sources") == expected
    assert [request["stage"] for request in requests] == ["proposal", "ledger"]
    assert all(options["timeout"] == 12.0 and options["max_retries"] == 0 for options in clients)
    assert [options["max_completion_tokens"] for options in clients] == [1000, 150]
    record = settled.ledger_events[-1]["narrative_record"]
    assert record["coverage"] == "confirmed_changes"
    assert [event["quote"] for event in record["events"]] == list(ORIGINALS)
    assert settled.session.performance_history[-1]["narrative_record"] == record
    state = settled.session.narrative_state["entities"]["environment:警灯"]["condition"]
    assert state["value"] == "绿色" and state["current"] is True
    assert settled.session.performance_history[-1]["fixed_narrations"] == delivered["fixed_narrations"]
    restarted = NumericV2Runtime(NumericV2Engine.from_mapping(runtime.engine.story), tmp_path)
    assert await restarted.restore_session(current.session.session_id) == settled
    fork = await restarted.fork_session_for_test(current.session.session_id, session_id="fixed-fork", through_revision=1)
    assert fork.session.narrative_state == settled.session.narrative_state
    second = await execute(first.stored)  # The real input entry waits for/reloads the settled tail.
    second_settled = await background.settle_before_input(runtime, second.stored)
    assert second_settled.session.revision == 2 and calls["actor"] == calls["body"] == 2
    assert calls["proposal"] == calls["ledger"] == 2
    assert second_settled.session.narrative_state["entities"]["environment:警灯"]["condition"]["value"] == "绿色"


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["trigger_denied", "ledger_denied", "provider_failed", "review_disabled"])
async def test_missing_trigger_or_approval_keeps_a_gap_without_fabricated_state(tmp_path, monkeypatch, mode):
    runtime, current, execute, calls, requests, _ = await _setup(tmp_path, monkeypatch, mode)
    result = await execute()
    settled = await background.settle_before_input(runtime, result.stored)
    assert settled.session.narrative_state["entities"] == {}
    assert settled.ledger_events[-1]["narrative_record"]["coverage"] not in {"confirmed_changes", "confirmed_no_change"}
    assert "narrative_pending" not in settled.ledger_events[-1]
    if mode in {"trigger_denied", "review_disabled"}:
        assert not result.performance.get("fixed_narrations")
        assert all(not any(text in json.dumps(request["data"][
            "source_texts" if request["stage"] == "proposal" else "phase_texts"], ensure_ascii=False) for text in ORIGINALS)
                   for request in requests)
    else:
        assert [item["text"] for item in result.performance["fixed_narrations"]] == list(ORIGINALS)
    if mode == "review_disabled":
        # Ledger work may run without foreground approval, but cannot cite text never displayed.
        assert [request["stage"] for request in requests] == ["proposal"]
        assert not calls["body"] and not calls["ledger"]
    assert await NumericV2Runtime(runtime.engine, tmp_path).restore_session(current.session.session_id) == settled


@pytest.mark.asyncio
async def test_synchronous_late_originals_do_not_reuse_earlier_body_approval(tmp_path, monkeypatch):
    runtime, current, execute, calls, requests, _ = await _setup(tmp_path, monkeypatch, "sync")
    result = await execute(background_narrative=False)
    assert [item["text"] for item in result.performance["fixed_narrations"]] == list(ORIGINALS)
    # Fixed text arrives after this proposal and body approval; preserve the full fingerprint gap.
    assert calls["proposal"] == calls["body"] == 1 and not calls["ledger"]
    assert not any(text in json.dumps(requests[0]["data"]["source_texts"], ensure_ascii=False) for text in ORIGINALS)
    assert result.stored.ledger_events[-1]["narrative_record"]["coverage"] == "unconfirmed"
    assert result.stored.session.narrative_state["entities"] == {}
    assert await runtime.restore_session(current.session.session_id) == result.stored
