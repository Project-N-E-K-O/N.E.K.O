"""Speech-only suggestions keep existing filters, call counts and input identity."""

from copy import deepcopy
import json

import pytest

from main_routers import numeric_theater_router as router
from services.theater import numeric_v2_actor as actor, numeric_v2_workflow as workflow
from services.theater.numeric_v2_actor_output import _parse_output
from tests.unit.test_theater_numeric_v2_router import _client


@pytest.mark.parametrize("prefix", ["", "（看向仪表）"])
@pytest.mark.parametrize("shown", [False, True])
def test_optional_action_does_not_bypass_existing_result_claim_filter(prefix, shown):
    text = "读数显示北侧通道。"
    body = "读数显示北侧通道。" if shown else "读数尚未出现。"
    candidate = {"performance": body, "transition_offered": False,
                 "suggested_inputs": [prefix + text, "读数现在能看清吗？"]}
    before = deepcopy(candidate)
    result, removed, reasons = workflow._prefilter_suggestion_candidates(candidate)
    assert result["suggested_inputs"] == ([prefix + text] if shown else []) + ["读数现在能看清吗？"]
    assert removed == int(not shown)
    assert reasons == (() if shown else ("unproven_result_claim",))
    assert result["performance"] == body
    assert candidate == before


@pytest.mark.asyncio
@pytest.mark.parametrize("scene_changed", [False, True])
async def test_one_usable_question_needs_no_auxiliary_call(monkeypatch, scene_changed):
    instance = actor.NumericV2Actor(object())

    async def unexpected(*args, **kwargs):
        pytest.fail("A usable question must not cause a fill request")

    monkeypatch.setattr(instance, "_invoke", unexpected)
    candidate = _parse_output(json.dumps({"performance": "我在听。",
        "suggested_inputs": ["你愿意再解释一下吗？"], "transition_offered": False}))
    result = await instance._ensure_suggestions(
        performance=candidate, player_input="先说说看。", catgirl_name="测试猫娘",
        max_input_tokens=10000, scene_changed=scene_changed,
    )
    assert result == ["你愿意再解释一下吗？"]
    assert instance.suggestion_fill_attempt_count == 0


def test_speech_only_button_submission_and_session_restore_preserve_original(tmp_path, monkeypatch):
    """Exercise actual HTTP membership checks and persistence, without a model call."""
    client = _client(tmp_path, monkeypatch)
    choice = "你愿意再解释一下吗？"
    captured = []

    async def opening(*args, **kwargs):
        return _parse_output(json.dumps({"scene_narration": "两人在桌边交谈。",
            "performance": "有想问的就说。", "suggested_inputs": [choice],
            "transition_offered": False}), opening_required=True)

    async def turn(*args, **kwargs):
        captured.append((kwargs["player_input"], kwargs["input_source"]))
        return _parse_output(json.dumps({"performance": "我再解释一下。",
            "suggested_inputs": ["我先听你说。"], "transition_offered": False}))

    monkeypatch.setattr(router.NumericV2Actor, "generate_opening", opening)
    monkeypatch.setattr(router.NumericV2Actor, "generate_turn", turn)
    scope = {"story_id": "numeric_v2_contract", "session_id": "plain-choice"}
    with client:
        started = client.post("/api/theater-numeric/session/start", json=scope)
        assert started.status_code == 200, started.text
        assert started.json()["suggested_inputs"] == [choice]
        submitted = client.post("/api/theater-numeric/session/input", json={**scope,
            "client_turn_id": "one", "base_revision": 0,
            "message": choice, "input_source": "suggestion"})
        assert submitted.status_code == 200, submitted.text
        restored = client.get("/api/theater-numeric/session/plain-choice",
                              params={"story_id": "numeric_v2_contract"})
        assert restored.status_code == 200, restored.text
        assert restored.json()["session"]["performance_history"][0]["input_text"] == choice
        stale = client.post("/api/theater-numeric/session/input", json={**scope,
            "client_turn_id": "stale", "base_revision": 1,
            "message": choice, "input_source": "suggestion"})
        assert stale.status_code == 409
        assert stale.json()["reason"] == "numeric_suggested_input_not_current"
    assert captured == [(choice, "suggestion")]
