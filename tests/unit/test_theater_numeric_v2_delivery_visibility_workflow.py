"""Delivery checks consume visible originals, not untriggered author templates.

Only model boundaries are fixed here; compilation, entry insertion, Workflow
checks, commit, restoration and fork replay use their native implementations.
"""

from copy import deepcopy

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_options import default_options
from services.theater.numeric_v2_performance import performance_content_blocks
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding


FIXED_TEXT = "登记册摊在桌上，封面保留着原来的日期。"
MESSAGE = "带我到窗边长桌看看。"


def _compiled_engine(delivery):
    story = deepcopy(_engine(ordinary=True).story)
    source = next(node for node in story["nodes"] if node["id"] == "start")
    source["route_gates"][0]["transition_contract"]["must_deliver"] = [
        "关键道具“登记册”[register_book]：本轮须公开展示。",
    ]
    target = next(node for node in story["nodes"] if node["id"] == "middle")
    target["story_beat"]["opening_scene"] = "窗边长桌旁摆着两把椅子。"
    if delivery in {"entry", "condition"}:
        trigger = {"type": delivery}
        if delivery == "condition":
            trigger["condition"] = "管理员打开抽屉后展示登记册。"
        target["story_beat"]["fixed_narrations"] = [{
            "id": "register_visible", "text": FIXED_TEXT,
            "trigger": trigger, "after": [], "required_before_exit": False,
        }]
    # Do not mutate an already compiled Engine to bypass definition validation.
    return NumericV2Engine.from_mapping(story)


@pytest.mark.asyncio
@pytest.mark.parametrize("check_delivery", [False, True], ids=["delivery-off", "delivery-on"])
@pytest.mark.parametrize("delivery", ["entry", "condition", "missing", "body"])
async def test_native_transition_checks_only_actual_delivery_and_replays_once(
    tmp_path, monkeypatch, check_delivery, delivery,
):
    engine = _compiled_engine(delivery)
    runtime = NumericV2Runtime(engine, tmp_path)
    binding = _binding()
    current = await runtime.start_session(
        session_id="delivery-visibility", catgirl_binding=binding,
        opening_performance={
            "performance": "窗边长桌现在开放，可以到那里看看。",
            "suggested_inputs": [],
        },
    )
    turn = TurnRequestV2("go", 0, MESSAGE)
    original = deepcopy((engine.story, current, turn, binding))
    path = runtime.store._path(current.session.session_id)
    original_bytes = path.read_bytes()
    calls = {"evaluator": 0, "actor": 0, "review": 0}
    actor_outputs = []
    retry_hints = []
    default_snapshot = default_options()
    defaults = {key: key == 'evaluator' for key in default_snapshot}
    assert defaults["review"] is False and defaults["review_delivery"] is False

    async def options():
        return {**defaults, "review_delivery": check_delivery}

    async def evaluate(self, **kwargs):
        calls["evaluator"] += 1
        return evaluator.NumericV2EvaluationResult(
            (), False, transition_intent="initiate",
        )

    async def generate(self, **kwargs):
        calls["actor"] += 1
        retry_hints.append(kwargs.get("retry_hint", ""))
        assert kwargs["session"] == current.session
        assert kwargs["player_input"] == MESSAGE
        assert kwargs["outcome"].session.current_node_id == "middle"
        candidate = {
            "source_performance": "",
            "bridge_scene_narration": "两人走到窗边。",
            "target_scene_narration": "长桌旁摆着两把椅子。",
            "target_performance": (
                "登记册放在桌上，先看这一页。" if delivery == "body" else "先看看这里。"
            ),
            "suggested_inputs": [],
        }
        performance = engine.finalize_transition_performance(
            kwargs["outcome"], candidate,
            target_opening="长桌旁摆着两把椅子。",
            source_dialogue_policy="optional",
        )
        # Native finalization, not the mock, determines whether fixed text is
        # inserted; an untriggered condition definition cannot count as delivery.
        fixed = performance["segments"][2].get("fixed_narrations", [])
        assert [piece["text"] for piece in fixed] == ([FIXED_TEXT] if delivery == "entry" else [])
        actor_outputs.append(deepcopy(performance))
        return performance

    async def unexpected_review(self, **kwargs):
        calls["review"] += 1
        raise AssertionError("delivery checking must not enable full Review")

    async def before_commit():
        assert path.read_bytes() == original_bytes
        assert (engine.story, current, turn, binding) == original

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", unexpected_review)
    monkeypatch.setattr(workflow.NumericV2Actor, "generate_turn", generate)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    diagnostics = {}
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current, turn=turn,
        ensure_current_binding=lambda _: binding, before_commit=before_commit,
        diagnostics_sink=diagnostics,
    )

    needs_rewrite = check_delivery and delivery in {"condition", "missing"}
    assert calls == {"evaluator": 1, "actor": 1 + int(needs_rewrite), "review": 0}
    assert diagnostics["semantic_rewrite_attempts"] == int(needs_rewrite)
    assert retry_hints[0] == ""
    if needs_rewrite:
        assert diagnostics["contract_missing"] == ["登记册"]
        assert diagnostics["contract_missing_after_rewrite"] == ["登记册"]
        assert diagnostics["contract_missing_fallback"] is True
        assert "登记册" in retry_hints[1]
    else:
        assert not diagnostics.get("contract_missing")
    assert (engine.story, current, turn, binding) == original
    assert default_options() == default_snapshot
    assert result.stored.session.revision == 1
    assert result.stored.session.current_node_id == "middle"
    assert result.performance["segments"] == actor_outputs[-1]["segments"]
    public = [block["text"] for block in performance_content_blocks(result.performance)]
    assert public.count(FIXED_TEXT) == int(delivery == "entry")

    committed_bytes = path.read_bytes()
    restored = await NumericV2Runtime(engine, tmp_path).restore_session(current.session.session_id)
    assert restored == result.stored
    forked = await runtime.fork_session_for_test(
        current.session.session_id, session_id="delivery-visibility-fork", through_revision=1,
    )
    assert forked.session.performance_history == result.stored.session.performance_history
    for stored in (restored, forked):
        last = stored.session.performance_history[-1]
        assert [block["text"] for block in performance_content_blocks(last)] == public
    assert path.read_bytes() == committed_bytes
