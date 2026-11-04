"""Delivery names follow the cast that actually produced each visible channel.

Only model boundaries are fixed. SDK import/export, Actor name projection,
fixed-original insertion, Workflow checks and persistence remain native.
"""

from copy import deepcopy
import json

import pytest

from services.theater import numeric_v2_evaluator as evaluator
from services.theater import numeric_v2_workflow as workflow
from services.theater.numeric_v2_options import default_options
from services.theater.numeric_v2_performance import performance_content_blocks
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
from tests.unit.test_theater_numeric_v2_natural_ending import _engine
from tests.unit.test_theater_numeric_v2_runtime import _binding
from tests.unit.theater_workshop.test_sdk_lifecycle import opened


def _sdk_engine(sdk, *, required, known, fixed_kind, player_author="林舟", literal_fixed=False):
    story = deepcopy(_engine(ordinary=True).story)
    story["intro"].update(player_name=player_author, catgirl_name="小岚")
    story["intro"]["player_identity"] = f"{player_author}，回乡整理旧屋的年轻男性。"
    story["initial_state"]["player_address_known"] = known
    source = next(node for node in story["nodes"] if node["id"] == "start")
    # This is an explicit author delivery requirement. Ordinary carry_props /
    # must_preserve declarations do not require redisplaying every prop.
    source["route_gates"][0]["transition_contract"]["must_deliver"] = [required]
    target = next(node for node in story["nodes"] if node["id"] == "middle")
    target["story_beat"]["opening_scene"] = "窗边长桌旁摆着两把椅子。"
    if fixed_kind:
        trigger = {"type": fixed_kind}
        if fixed_kind == "condition":
            trigger["condition"] = "管理员打开抽屉后展示登记册。"
        target["story_beat"]["fixed_narrations"] = [{
            "id": "register_visible",
            "text": (
                "林舟的登记册摊在桌上，封面保留着原来的日期。" if literal_fixed else
                "{{player_name}}的登记册摊在桌上，封面保留着原来的日期。"
            ),
            "trigger": trigger, "after": [], "required_before_exit": False,
        }]
    original = deepcopy(story)
    project = sdk.import_story(story)
    compiled = sdk.compile(project["project_id"], base_revision=project["revision"])
    sdk.validate(project["project_id"], base_revision=project["revision"])
    exported = sdk.export(project["project_id"], base_revision=project["revision"])
    assert exported.json_bytes == compiled["json_bytes"]
    assert json.loads(exported.json_bytes) == original == story
    return NumericV2Engine.from_mapping(json.loads(exported.json_bytes))


async def _run_case(
    tmp_path, monkeypatch, sdk, *, required, body_name="", fixed_kind=None,
    known=False, disclose=False, check_delivery=True, missing=False,
    catgirl_name="Lan", player_author="林舟", literal_fixed=False,
):
    engine = _sdk_engine(
        sdk, required=required, known=known, fixed_kind=fixed_kind,
        player_author=player_author, literal_fixed=literal_fixed,
    )
    binding = {**_binding(), "catgirl_name": catgirl_name}
    runtime = NumericV2Runtime(engine, tmp_path / "sessions")
    current = await runtime.start_session(
        session_id="delivery-names", catgirl_binding=binding,
        opening_performance={
            "performance": "窗边长桌现在开放，可以到那里看看。",
            "suggested_inputs": [],
        },
    )
    message = ("我叫哥哥，" if disclose else "") + "带我到窗边长桌看看。"
    turn = TurnRequestV2("go", 0, message)
    original = deepcopy((engine.story, current, turn, binding))
    path = runtime.store._path(current.session.session_id)
    original_bytes = path.read_bytes()
    default_snapshot = default_options()
    defaults = {key: key == 'evaluator' for key in default_snapshot}
    calls = {"evaluator": 0, "actor": 0, "review": 0}
    requests = []
    target_text = f"{body_name}放在桌上，先看这一页。" if body_name else "先看看这里。"
    candidate = {
        "source_performance": "",
        "bridge_scene_narration": "两人走到窗边。",
        "target_scene_narration": "长桌旁摆着两把椅子。",
        "target_performance": target_text,
        "suggested_inputs": ["我先看看这一页。"],
    }
    original_candidate = deepcopy(candidate)

    async def options():
        return {**defaults, "review_delivery": check_delivery}

    async def evaluate(self, **kwargs):
        calls["evaluator"] += 1
        return evaluator.NumericV2EvaluationResult((), False, transition_intent="initiate")

    async def invoke(self, messages, **kwargs):
        calls["actor"] += 1
        assert kwargs["transition_required"] is True
        assert not kwargs.get("suggestions_only")
        assert not kwargs.get("transition_suggestions_only")
        requests.append(messages)
        return deepcopy(candidate)

    async def unexpected_review(self, **kwargs):
        calls["review"] += 1
        raise AssertionError("delivery checking must not enable full Review")

    async def before_commit():
        assert path.read_bytes() == original_bytes
        assert (engine.story, current, turn, binding) == original

    monkeypatch.setattr(workflow, "aload_theater_module_options", options)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "evaluate", evaluate)
    monkeypatch.setattr(workflow.NumericV2MetricEvaluator, "validate_transition_offer", unexpected_review)
    # Keep real generate_turn: it must use current knowledge for dynamic text,
    # then let Runtime use outcome knowledge for newly inserted fixed text.
    monkeypatch.setattr(workflow.NumericV2Actor, "_invoke", invoke)
    monkeypatch.setattr(workflow.NumericV2Actor, "_character_profile", lambda self: "温和。")
    diagnostics = {}
    result = await workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current, turn=turn,
        ensure_current_binding=lambda _: binding, before_commit=before_commit,
        diagnostics_sink=diagnostics,
    )
    rewrite = check_delivery and missing
    assert calls == {"evaluator": 1, "actor": 1 + int(rewrite), "review": 0}
    assert diagnostics["semantic_rewrite_attempts"] == int(rewrite)
    if rewrite:
        assert diagnostics["contract_missing"]
        assert diagnostics["contract_missing_after_rewrite"]
        assert diagnostics["contract_missing_fallback"] is True
    else:
        assert not diagnostics.get("contract_missing")
    assert current.session.player_address_known is known
    assert result.stored.session.player_address_known is (known or disclose)
    assert result.stored.session.current_node_id == "middle"
    assert result.stored.session.revision == 1
    assert result.performance["segments"][2]["performance"] == target_text
    pieces = result.performance["segments"][2].get("fixed_narrations", [])
    expected_fixed = (
        ("林舟" if literal_fixed else "哥哥" if known or disclose else "你")
        + "的登记册摊在桌上，封面保留着原来的日期。"
    )
    assert [piece["text"] for piece in pieces] == ([expected_fixed] if fixed_kind == "entry" else [])
    public = [block["text"] for block in performance_content_blocks(result.performance)]
    assert public.count(expected_fixed) == int(fixed_kind == "entry")
    if pieces:
        assert pieces[0]["id"] == "register_visible"
        assert pieces[0]["bindings"]["player_name"] == ("哥哥" if known or disclose else "你")
    if disclose and not known:
        # The first request was built before committing the disclosed nickname;
        # the fixed original below can nevertheless use the native outcome.
        assert "玩家的登记册" in requests[0][1].content
        assert "哥哥的登记册" not in requests[0][1].content
    assert candidate == original_candidate
    assert (engine.story, current, turn, binding) == original
    assert default_options() == default_snapshot
    committed_bytes = path.read_bytes()
    restored = await NumericV2Runtime(engine, tmp_path / "sessions").restore_session("delivery-names")
    forked = await runtime.fork_session_for_test(
        "delivery-names", session_id="delivery-names-fork", through_revision=1,
    )
    assert restored == result.stored
    assert forked.session.performance_history == result.stored.session.performance_history
    for stored in (restored, forked):
        assert [block["text"] for block in performance_content_blocks(
            stored.session.performance_history[-1],
        )] == public
        assert stored.session.player_address_known is (known or disclose)
    assert path.read_bytes() == committed_bytes


@pytest.mark.asyncio
@pytest.mark.parametrize("delivery", ["body", "entry", "both"])
async def test_disclosure_turn_keeps_dynamic_and_fixed_delivery_casts_independent(
    tmp_path, monkeypatch, opened, delivery,
):
    await _run_case(
        tmp_path, monkeypatch, opened[0].sdk,
        required="关键道具“林舟的登记册”[register_book]：本轮须公开展示。",
        body_name="你的登记册" if delivery in {"body", "both"} else "",
        fixed_kind="entry" if delivery in {"entry", "both"} else None,
        disclose=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("known", [False, True])
async def test_runtime_catgirl_name_equal_to_author_player_name_is_not_cast_twice(
    tmp_path, monkeypatch, opened, known,
):
    await _run_case(
        tmp_path, monkeypatch, opened[0].sdk, known=known, catgirl_name="林舟",
        required="关键道具“小岚的登记册”[register_book]：本轮须公开展示。",
        body_name="林舟的登记册",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["unrevealed-nickname", "condition", "absent"])
@pytest.mark.parametrize("check_delivery", [False, True])
async def test_missing_or_unavailable_name_keeps_existing_optional_rewrite(
    tmp_path, monkeypatch, opened, kind, check_delivery,
):
    # Merely seeing a configured nickname inside a prop name is not a public
    # self-introduction. Do not make it a valid alias for the unknown player.
    await _run_case(
        tmp_path, monkeypatch, opened[0].sdk,
        required="关键道具“林舟的登记册”[register_book]：本轮须公开展示。",
        body_name="哥哥的登记册" if kind == "unrevealed-nickname" else "",
        fixed_kind="condition" if kind == "condition" else None,
        check_delivery=check_delivery, missing=True,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("id_only", [False, True])
async def test_non_name_props_and_protocol_id_fallback_remain_literal(
    tmp_path, monkeypatch, opened, id_only,
):
    await _run_case(
        tmp_path, monkeypatch, opened[0].sdk, known=True,
        player_author="linzhou" if id_only else "林舟",
        required=(
            "关键道具 [linzhou]：本轮须公开展示。" if id_only else
            "关键道具“登记册”[register_book]：本轮须公开展示。"
        ),
        body_name="linzhou" if id_only else "登记册",
    )


@pytest.mark.asyncio
async def test_literal_author_fixed_original_remains_visible_without_recasting(
    tmp_path, monkeypatch, opened,
):
    # Fixed originals replace only explicit placeholders. Existing author-name
    # text remains verbatim and must not become a newly missing delivery.
    await _run_case(
        tmp_path, monkeypatch, opened[0].sdk,
        required="关键道具“林舟的登记册”[register_book]：本轮须公开展示。",
        fixed_kind="entry", literal_fixed=True,
    )
