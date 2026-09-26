"""Verify state merging and fallback boundaries in the theater workflow."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
import json

import pytest

from services.theater import numeric_v2_workflow
from services.theater.numeric_v2_actor import (
    NumericV2ActorOutputError,
)
from services.theater.numeric_v2_evaluator import (
    NumericV2TransitionOfferReview,
)
from services.theater.numeric_v2_workflow import (
    _actor_rewrite_candidate_context,
    _drop_reported_unsafe_suggestions,
    _generate_actor_turn_with_output_retry,
    _terminal_new_question_markers,
    _transition_review_failure_context,
    _transition_boundary_repair_context,
    generate_validated_opening,
)
from services.theater.numeric_v2_runtime import NumericV2Engine
from tests.unit.test_theater_numeric_v2_contract import numeric_v2_story


def test_terminal_new_question_is_rejected_but_ordinary_scene_question_is_allowed() -> None:
    story = numeric_v2_story()
    engine = NumericV2Engine.from_mapping(story)
    outcome = SimpleNamespace(ledger_event={"to_node_id": "ending_leave"})

    assert _terminal_new_question_markers(
        engine=engine,
        outcome=outcome,
        performance={"performance": "你这次是路过，还是特意回来？"},
    ) == ("terminal_new_question",)

    ordinary = SimpleNamespace(ledger_event={"to_node_id": "scene"})
    assert _terminal_new_question_markers(
        engine=engine,
        outcome=ordinary,
        performance={"performance": "你要不要先坐下？"},
    ) == ()


@pytest.mark.asyncio
async def test_scoped_opening_is_reviewed_and_rewritten_before_session(monkeypatch) -> None:
    """Rewrite failed temporary opening boundaries only once and do not create a formal Session before approval."""

    story = numeric_v2_story()
    story["nodes"][0]["story_beat"]["opening_only_boundaries"] = [
        "不得在公开开场披露后续身份。"
    ]
    engine = NumericV2Engine.from_mapping(story)
    actor_calls = []
    review_calls = []

    async def generate_opening(self, **kwargs):
        actor_calls.append(str(kwargs.get("retry_hint") or ""))
        if len(actor_calls) == 1:
            return {"performance": "我是后续身份。", "suggested_inputs": []}
        return {"performance": "（抬眼）这里是什么地方？", "suggested_inputs": []}

    async def validate(self, **kwargs):
        review_calls.append(kwargs)
        safe = "后续身份" not in kwargs["actor_performance"]["performance"]
        return NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            body_violations=() if safe else ("author_boundary",),
            unsafe_suggestion_indexes=(),
            failure_reason=("公开开场提前披露身份。" if not safe else ""),
        )

    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, "generate_opening", generate_opening)
    monkeypatch.setattr(
        numeric_v2_workflow.NumericV2MetricEvaluator,
        "validate_transition_offer",
        validate,
    )

    opening = await generate_validated_opening(
        engine=engine,
        config_manager=object(),
        session_id="opening_review",
        catgirl_binding={"catgirl_id": "catgirl:test", "catgirl_name": "测试猫娘"},
        actor_budget_profile="balanced",
    )

    assert opening["performance"] == "（抬眼）这里是什么地方？"
    assert len(actor_calls) == 2
    assert "具体失败：公开开场提前披露身份" in actor_calls[1]
    assert "我是后续身份" in actor_calls[1]
    assert "尚未提交、必须修正的上一版输出" in actor_calls[1]
    assert len(review_calls) == 2
    assert all(call["route_changed"] is True for call in review_calls)


@pytest.mark.asyncio
async def test_scoped_opening_drops_only_unsafe_suggestions(monkeypatch) -> None:
    """Retain safe opening prose and remove suggestions that exceed author boundaries."""

    story = numeric_v2_story()
    story["nodes"][0]["story_beat"]["opening_only_boundaries"] = [
        "不得在公开开场披露后续身份。"
    ]
    engine = NumericV2Engine.from_mapping(story)
    actor_calls = 0
    review_calls = []

    async def generate_opening(self, **kwargs):
        nonlocal actor_calls
        actor_calls += 1
        return {
            "performance": "（抬眼）这里是什么地方？",
            "suggested_inputs": ["（追问）请公开后续身份。"],
        }

    async def validate(self, **kwargs):
        review_calls.append(kwargs)
        suggestions = kwargs["actor_performance"].get("suggested_inputs") or []
        return NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            body_violations=(),
            unsafe_suggestion_indexes=(0,) if suggestions else (),
            failure_reason=("推荐提前要求后续身份。" if suggestions else ""),
        )

    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, "generate_opening", generate_opening)
    monkeypatch.setattr(
        numeric_v2_workflow.NumericV2MetricEvaluator,
        "validate_transition_offer",
        validate,
    )

    opening = await generate_validated_opening(
        engine=engine,
        config_manager=object(),
        session_id="opening_suggestion_review",
        catgirl_binding={"catgirl_id": "catgirl:test", "catgirl_name": "测试猫娘"},
        actor_budget_profile="balanced",
    )

    assert opening["performance"] == "（抬眼）这里是什么地方？"
    assert opening["suggested_inputs"] == []
    assert actor_calls == 1
    assert len(review_calls) == 1


@pytest.mark.asyncio
async def test_unscoped_opening_skips_semantic_review(monkeypatch) -> None:
    """Legacy packages without temporary opening boundaries retain their call cost and startup behavior."""

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    review_calls = 0

    async def generate_opening(self, **kwargs):
        return {"performance": "（抬眼）这里是什么地方？", "suggested_inputs": []}

    async def validate(self, **kwargs):
        nonlocal review_calls
        review_calls += 1
        raise AssertionError("未声明 opening_only_boundaries 时不应调用开场复核")

    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, "generate_opening", generate_opening)
    monkeypatch.setattr(
        numeric_v2_workflow.NumericV2MetricEvaluator,
        "validate_transition_offer",
        validate,
    )

    opening = await generate_validated_opening(
        engine=engine,
        config_manager=object(),
        session_id="opening_without_scope",
        catgirl_binding={"catgirl_id": "catgirl:test", "catgirl_name": "测试猫娘"},
        actor_budget_profile="balanced",
    )

    assert opening["performance"] == "（抬眼）这里是什么地方？"
    assert review_calls == 0


@pytest.mark.asyncio
async def test_opening_skips_suggestion_fill_even_when_module_is_enabled(monkeypatch) -> None:
    """开场不因补推荐再串行等待一次模型调用，且保留单条合法选项。"""  # noqa: DOCSTRING_CJK

    engine = NumericV2Engine.from_mapping(numeric_v2_story())
    captured: dict[str, Any] = {}

    async def module_options():
        return {"review": False, "suggestion_fill": True}

    async def generate_opening(self, **kwargs):
        captured["allow_suggestion_fill"] = kwargs["allow_suggestion_fill"]
        return {
            "performance": "（抬眼）这里是什么地方？",
            "suggested_inputs": ["（看向她）你能先说说现在的情况吗？"],
        }

    monkeypatch.setattr(numeric_v2_workflow, "aload_theater_module_options", module_options)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, "generate_opening", generate_opening)

    opening = await generate_validated_opening(
        engine=engine,
        config_manager=object(),
        session_id="opening_without_suggestion_fill",
        catgirl_binding={"catgirl_id": "catgirl:test", "catgirl_name": "测试猫娘"},
        actor_budget_profile="balanced",
    )

    assert captured["allow_suggestion_fill"] is False
    assert opening["suggested_inputs"] == ["（看向她）你能先说说现在的情况吗？"]


def test_transition_boundary_repair_receives_bridge_and_target_opening() -> None:
    """Boundary rewrites receive the author bridge and next opening only as stopping boundaries."""

    engine = SimpleNamespace(
        nodes={
            "current": {"story_beat": {"summary": "当前幕先完成控制台同步。"}},
            "target": {"story_beat": {"opening_scene": "下一幕的警报已经响起。"}},
        },
        preview_route=lambda _node_id, _metrics: {
            "target_node_id": "target",
            "transition_contract": {
                "bridge_scene_narration": "舱门在玩家确认后关闭。",
            },
        },
    )
    session = SimpleNamespace(current_node_id="current", metrics={})

    context = _transition_boundary_repair_context(
        SimpleNamespace(engine=engine),
        SimpleNamespace(session=session),
    )

    assert "只定义停止边界" in context
    assert "仍可在当前幕交付的作者方向" in context
    assert "保留玩家本轮已经实施的合法当前幕行动及其获准结果" in context
    assert "不覆盖本轮玩家所有权和作者事实的修复要求" in context
    assert "只删除桥段或目标幕独有结果" not in context
    assert "舱门在玩家确认后关闭" in context
    assert "下一幕的警报已经响起" in context


def test_transition_boundary_retry_receives_specific_failure_reason() -> None:
    """Carry the specific conflict into boundary rewrites while making clear it is not a new story fact."""

    context = _transition_review_failure_context(
        NumericV2TransitionOfferReview(
            offer_present=False,
            valid=False,
            body_violations=("author_boundary",),
            unsafe_suggestion_indexes=(),
            failure_reason=(
                "上一版声称保护罩能隔绝热信号，但当前幕只确认保护罩可以短时展开。"
            ),
        )
    )

    assert "保护罩能隔绝热信号" in context
    assert "只用于定位并删除上一版问题" in context
    assert "不是剧情事实" in context


def test_transition_boundary_repair_uses_same_legacy_opening_as_playback() -> None:
    """When legacy packages omit the opening field, rewrites still need the actual played summary-first-sentence boundary."""

    engine = SimpleNamespace(
        nodes={"current": {"story_beat": {"summary": "来源阶段。"}},
               "target": {"story_beat": {"summary": "警报响起。稍后才揭露真相。"}}},
        preview_route=lambda *_: {"target_node_id": "target", "transition_contract": {}},
    )
    context = _transition_boundary_repair_context(
        SimpleNamespace(engine=engine),
        SimpleNamespace(session=SimpleNamespace(current_node_id="current", metrics={})),
    )
    assert "正式换幕后才成立的下一幕开场：警报响起。" in context
    assert "稍后才揭露真相" not in context


def test_boundary_repair_uses_updated_route_and_preserves_its_proposal() -> None:
    """After same-turn metrics cross a branch threshold, correction context must match the route seen by the Actor and reviewer."""
    engine = SimpleNamespace(
        nodes={"current": {"story_beat": {"summary": "眼前交流已完成。"}},
               "low": {"story_beat": {"opening_scene": "次日回接待室。"}},
               "high": {"story_beat": {"opening_scene": "周末到观测室。"}}},
        preview_route=lambda _, metrics: {
            "target_node_id": "high" if metrics["trust"] >= 70 else "low",
            "transition_contract": {"reason": "周末到观测室核对结果。" if metrics["trust"] >= 70 else "次日回接待室。"},
        },
    )
    current = SimpleNamespace(session=SimpleNamespace(current_node_id="current", metrics={"trust": 69}))
    context = _transition_boundary_repair_context(SimpleNamespace(engine=engine), current, metrics={"trust": 71})
    assert "周末到观测室核对结果。" in context
    assert "次日回接待室" not in context
    assert current.session.metrics == {"trust": 69}


def test_unsafe_suggestion_drop_preserves_all_visible_body_fields() -> None:
    """Targeted suggestion removal must not alter body text, scene updates or offer flags."""

    candidate = {
        "performance": "（望向门边）我们还在屋内。",
        "scene_narration": "两人已经抵达长街。",
        "suggested_inputs": ["继续追问。", "已经抵达了。", "再等等。"],
        "transition_offered": True,
    }
    filtered, removed = _drop_reported_unsafe_suggestions(candidate, (1,))

    assert removed == 1
    assert filtered == {**candidate, "suggested_inputs": ["继续追问。", "再等等。"]}
    assert candidate["suggested_inputs"] == ["继续追问。", "已经抵达了。", "再等等。"]


def test_invalid_unsafe_suggestion_index_drops_buttons_without_touching_body() -> None:
    """If unsafe buttons cannot be located, clear suggestions without treating invalid indices as body-safety evidence or starting another review."""

    candidate = {
        "performance": "（指向门口）我们去阅览室，好吗？",
        "scene_narration": "档案仍留在桌上。",
        "transition_offered": True,
        "suggested_inputs": ["好，一起去。", "先等等。"],
    }
    filtered, removed = _drop_reported_unsafe_suggestions(candidate, (2,))

    assert filtered == {**candidate, "suggested_inputs": []}
    assert removed == 2
    assert candidate["suggested_inputs"] == ["好，一起去。", "先等等。"]


def test_actor_rewrite_candidate_context_marks_rejected_output_as_uncommitted() -> None:
    """The sole boundary rewrite must see the original text to edit without promoting it to story facts."""

    context = _actor_rewrite_candidate_context({
        "performance": "（抬眼）这是尚未获准公开的信息。",
        "suggested_inputs": ["（追问）请继续。"],
        "transition_offered": False,
    })

    assert "这是尚未获准公开的信息" in context
    assert "尚未提交、必须修正的上一版输出" in context
    assert "不是剧情事实" in context
    assert "再逐条复核全部作者边界" in context


@pytest.mark.asyncio
async def test_actor_output_retry_changes_hint_for_each_attempt() -> None:
    """Give each retry a different rewriting angle after repeated body failures."""

    class RetryActor:
        def __init__(self) -> None:
            self.hints: list[str] = []

        async def generate_turn(self, **kwargs):
            self.hints.append(str(kwargs.get("retry_hint") or ""))
            if len(self.hints) < 4:
                raise NumericV2ActorOutputError("numeric_v2_actor_repeated_output")
            return {"performance": "（抬眼）这次回应加入了新的动作。"}

    actor = RetryActor()
    outcome = SimpleNamespace(
        ledger_event={"from_node_id": "start", "to_node_id": "start"},
    )

    result = await _generate_actor_turn_with_output_retry(
        actor,
        outcome=outcome,
        session=SimpleNamespace(session_id="retry-test", revision=2),
    )

    assert result["performance"] == "（抬眼）这次回应加入了新的动作。"
    assert actor.hints[0] == ""
    assert len(set(actor.hints[1:])) == 3
    assert "第二次重复输出重试" in actor.hints[2]
    assert "最后一次重复输出重试" in actor.hints[3]


@pytest.mark.asyncio
async def test_tagged_repeated_output_stops_after_one_retry_and_records_guard() -> None:
    """真实重复保护只再采样一次，并把命中来源写入本轮诊断。"""  # noqa: DOCSTRING_CJK

    class RetryActor:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, **kwargs):
            self.calls += 1
            error = NumericV2ActorOutputError("numeric_v2_actor_repeated_output")
            error.repetition_guard = "previous_performance"
            raise error

    actor = RetryActor()
    diagnostics = {}
    outcome = SimpleNamespace(
        ledger_event={"from_node_id": "start", "to_node_id": "start"},
    )

    with pytest.raises(NumericV2ActorOutputError):
        await _generate_actor_turn_with_output_retry(
            actor,
            diagnostics=diagnostics,
            outcome=outcome,
            session=SimpleNamespace(session_id="tagged-retry", revision=2),
        )

    assert actor.calls == 2
    assert diagnostics["actor_repeated_output_guards"] == {
        "previous_performance": 2,
    }
    assert diagnostics["actor_repeated_output_retry_aborted"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("repetition_guard", ["earlier_session", "transition_source"])
async def test_accepted_transition_allows_one_source_repeat_retry_when_output_retry_is_disabled(
    repetition_guard: str,
) -> None:
    """正式接受换场的来源复用命中不应因通用重试关闭而卡死。"""  # noqa: DOCSTRING_CJK

    class RetryActor:
        def __init__(self) -> None:
            self.calls = 0

        async def generate_turn(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                error = NumericV2ActorOutputError("numeric_v2_actor_repeated_output")
                error.repetition_guard = repetition_guard
                raise error
            return {"performance": "（重新抬眼）那就走吧。"}

    actor = RetryActor()
    diagnostics = {}
    outcome = SimpleNamespace(
        ledger_event={
            "from_node_id": "mainline_01",
            "to_node_id": "branch_01",
            "transition_intent": "accept",
        },
    )

    result = await _generate_actor_turn_with_output_retry(
        actor,
        diagnostics=diagnostics,
        outcome=outcome,
        allow_output_retry=False,
        allow_transition_repeat_retry=True,
        session=SimpleNamespace(session_id="accepted-transition-retry", revision=2),
    )

    assert result["performance"] == "（重新抬眼）那就走吧。"
    assert actor.calls == 2
    assert diagnostics["actor_generation_attempts"] == 2
    assert diagnostics["actor_repeated_output_guards"] == {repetition_guard: 1}
    assert diagnostics.get("actor_repeated_output_retry_aborted", 0) == 0


@pytest.mark.asyncio
async def test_actor_output_retry_preserves_required_boundary_rewrite() -> None:
    """A format failure in a boundary rewrite must not make later retries lose the original boundary requirements."""

    class RetryActor:
        def __init__(self) -> None:
            self.hints: list[str] = []

        async def generate_turn(self, **kwargs):
            self.hints.append(str(kwargs.get("retry_hint") or ""))
            if len(self.hints) == 1:
                raise NumericV2ActorOutputError("numeric_v2_actor_repeated_output")
            return {"performance": "（撑住门）要继续穿过去吗？"}

    actor = RetryActor()
    outcome = SimpleNamespace(
        ledger_event={"from_node_id": "start", "to_node_id": "start"},
    )

    await _generate_actor_turn_with_output_retry(
        actor,
        outcome=outcome,
        session=SimpleNamespace(session_id="boundary-retry", revision=2),
        retry_hint="必须停在门槛前等待玩家确认。",
    )

    assert actor.hints[0] == "必须停在门槛前等待玩家确认。"
    assert "必须停在门槛前等待玩家确认。" in actor.hints[1]
    assert "上一版与较早回合" in actor.hints[1]


@pytest.mark.asyncio
@pytest.mark.parametrize("entered", [False, True])
@pytest.mark.parametrize("prior_turns", [0, 2])
async def test_ordinary_review_keeps_committed_history_watermark_with_projected_metrics(
    tmp_path, monkeypatch, entered, prior_turns,
):
    """Real Workflow calls must retain opening evidence and coverage through disputes and rewrites."""
    from services.theater import numeric_v2_evaluator as evaluator
    from services.theater.numeric_v2_runtime import NumericV2Runtime, TurnRequestV2, MetricChangeV2
    from tests.unit.test_theater_numeric_v2_player_transition import initiation_case
    from tests.unit.test_theater_numeric_v2_runtime import _binding
    from tests.unit.test_theater_numeric_v2_transition_history import _candidate

    case = initiation_case()
    engine = case['engine']
    runtime = NumericV2Runtime(engine, tmp_path)
    current = await runtime.start_session(session_id='review_watermark', catgirl_binding=_binding(),
                                          opening_performance=case['session'].opening_performance)
    if entered:
        prepared = runtime.prepare_turn(current, TurnRequestV2('enter', 0, '带路吧。'), (), transition_intent='initiate')
        current = await runtime.commit_turn(prepared, engine.finalize_transition_performance(
            prepared, _candidate(), target_opening='阅览室入口。'))
    for index in range(prior_turns):
        prepared = runtime.prepare_turn(current, TurnRequestV2(f'old{index}', current.session.revision, '稍等。'), ())
        current = await runtime.commit_turn(prepared, {'performance': f'我在这里等你，这是第{index}次回应。', 'suggested_inputs': []})

    review_messages = []
    generations = []

    async def evaluate(self, **kwargs):
        return evaluator.NumericV2EvaluationResult((MetricChangeV2('trust', 2, '玩家兑现承诺', '我来帮你。'),), False)

    async def generate(self, **kwargs):
        generations.append(kwargs)
        return {'performance': '尚未提交的初稿。' if len(generations) == 1 else '修正后的最终回应。', 'suggested_inputs': []}

    async def review(self, **kwargs):
        snapshot = kwargs['session']
        # 计分后可用出口仍由本轮候选数值预览；历史必须来自已提交快照。
        assert snapshot.metrics['trust'] == current.session.metrics['trust'] + 2
        assert snapshot.revision == current.session.revision
        assert snapshot.node_turn_count == current.session.node_turn_count
        messages = evaluator._build_transition_judge_messages(
            engine, snapshot, actor_performance=kwargs['actor_performance'], player_input=kwargs['message'],
            check_missed_initiation=kwargs.get('check_missed_initiation', False),
        )[0]
        payload = json.loads(messages[1].content.split('：', 1)[1])
        assert payload['current_visit_history_complete'] is True
        assert payload['scene_context'][0]['phase'] == ('scene_entry' if entered else 'opening')
        assert payload['scene_context'][-1]['revision'] == current.session.revision
        assert '尚未提交的初稿' not in json.dumps(payload['scene_context'], ensure_ascii=False)
        review_messages.append(payload)
        return evaluator.NumericV2TransitionOfferReview(False, False,
            ('author_boundary',) if len(generations) == 1 else (), (), '需要修正初稿。')

    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'evaluate', evaluate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2MetricEvaluator, 'validate_transition_offer', review)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, 'generate_turn', generate)
    monkeypatch.setattr(numeric_v2_workflow.NumericV2Actor, '_character_profile', lambda self: '温和。')
    result = await numeric_v2_workflow.execute_numeric_v2_turn(
        config_manager=object(), runtime=runtime, current=current,
        turn=TurnRequestV2('new', current.session.revision, '我来帮你。'), ensure_current_binding=lambda _: _binding(),
    )
    assert len(review_messages) == 3 and len(generations) == 2
    assert all(row['scene_context'] == review_messages[0]['scene_context'] for row in review_messages)
    assert result.stored.session.revision == current.session.revision + 1
    assert result.stored.session.node_turn_count == current.session.node_turn_count + 1
    assert result.stored.session.metrics['trust'] == current.session.metrics['trust'] + 2
    assert await runtime.restore_session('review_watermark') == result.stored
