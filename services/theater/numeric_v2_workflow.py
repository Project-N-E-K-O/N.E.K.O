"""Numeric v2 的应用级回合工作流，不处理 HTTP 请求与响应映射。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, replace
import json
import logging
import time
from typing import Any, Callable, Mapping

from utils.character_memory import character_config_mutation_lock

from .numeric_v2_actor import (
    NumericV2Actor,
    NumericV2ActorOutputError,
)
from .numeric_v2_context import scene_opening_text
from .numeric_v2_history import lookup_history
from .numeric_v2_evaluator import (
    NumericV2EvaluationResult,
    NumericV2EvaluatorError,
    NumericV2MetricEvaluator,
    NumericV2TransitionOfferReview,
)
from .numeric_v2_runtime import (
    NumericV2Engine,
    NumericV2Runtime,
    TurnOutcomeV2,
    TurnRequestV2,
)
from .numeric_v2_store import NumericV2StoredSession


logger = logging.getLogger(__name__)


def _drop_reported_unsafe_suggestions(
    candidate: Mapping[str, Any],
    unsafe_indexes: tuple[int, ...],
) -> tuple[dict[str, Any], int]:
    """按结构化索引删除不安全推荐；正文和其它字段保持原样。"""  # noqa: DOCSTRING_CJK

    result = dict(candidate)
    suggestions = candidate.get("suggested_inputs")
    if not isinstance(suggestions, list) or not unsafe_indexes:
        return result, 0
    # 协议允许索引 0—2，但候选可能不足三条；无法定位已报告的问题时只撤下按钮。
    if any(index < 0 or index >= len(suggestions) for index in unsafe_indexes):
        result["suggested_inputs"] = []
        return result, len(suggestions)
    valid_indexes = {
        index
        for index in unsafe_indexes
        if 0 <= index < len(suggestions)
    }
    if not valid_indexes:
        return result, 0
    result["suggested_inputs"] = [
        item
        for index, item in enumerate(suggestions)
        if index not in valid_indexes
    ]
    return result, len(valid_indexes)


async def generate_validated_opening(
    *,
    engine: NumericV2Engine,
    config_manager: Any,
    session_id: str,
    catgirl_binding: Mapping[str, Any],
    actor_budget_profile: str,
) -> dict[str, Any]:
    """生成公开开场；声明临时开场边界时必须在建 Session 前通过复核。"""  # noqa: DOCSTRING_CJK

    actor = NumericV2Actor(config_manager)
    opening = await actor.generate_opening(
        engine=engine,
        actor_budget_profile=actor_budget_profile,
    )
    start_node = engine.nodes[str(engine.story["start_node_id"])]
    opening_boundaries = start_node["story_beat"].get("opening_only_boundaries")
    if not opening_boundaries:
        return opening

    evaluator = NumericV2MetricEvaluator(config_manager)
    for attempt in range(2):
        review_session = engine.create_session(
            session_id=session_id,
            catgirl_binding=catgirl_binding,
            opening_performance={"performance": "", "suggested_inputs": []},
            actor_budget_profile=actor_budget_profile,
        )
        try:
            review = await evaluator.validate_transition_offer(
                engine=engine,
                session=review_session,
                message="",
                actor_performance=opening,
                route_changed=True,
            )
        except NumericV2EvaluatorError as exc:
            raise NumericV2ActorOutputError(
                "numeric_v2_opening_review_failed"
            ) from exc
        # 正文与按钮已分别判定；删掉坏按钮不会改变正文事实或制造新的离幕提议。
        opening, _ = _drop_reported_unsafe_suggestions(
            opening, review.unsafe_suggestion_indexes,
        )
        if not review.body_violations and not review.offer_present:
            return opening
        if attempt == 0:
            opening = await actor.generate_opening(
                engine=engine,
                actor_budget_profile=actor_budget_profile,
                retry_hint=(
                    "上一版正文或推荐没有遵守 opening_only_boundaries。"
                    "只保留开场已授权的可见事实，不得提前交付后续阶段内容，也不得提出离幕行动。"
                    f"具体失败：{review.failure_reason or '公开开场边界未通过。'}"
                    f"{_actor_rewrite_candidate_context(opening)}"
                ),
            )
    raise NumericV2ActorOutputError("numeric_v2_opening_fact_boundary")


def _output_retry_hint(
    *,
    last_error_code: str,
    retry_number: int,
    route_changed: bool,
) -> str:
    """为每一次正文重试提供不同的改写角度，避免模型沿用同一采样路径。"""

    if route_changed:
        # 重试承接真实授权，兼容接受、主动前往与自然结束，不虚构邀请。
        if retry_number == 1:
            return (
                "这是正式换场重试。请先用全新的简短来源回应承接玩家本轮实际授权的行动，"
                "再写新的过渡桥段；不要复用上一幕或上一版的来源对白、动作和收尾。"
            )
        if retry_number == 2:
            return (
                "这是第二次正式换场重试。请改用不同的来源动作和对白回应玩家本轮行动，"
                "重新组织过渡桥段并引入一个当前事实支持的变化；目标开场只需自然接入，"
                "不要复述上一版内容。"
            )
        return (
            "这是最后一次正式换场重试。请用最简短的全新来源动作与对白完成承接，"
            "保留必要的过渡因果但完全改写句式和收尾；不要复制任何较早回合的正文。"
        )

    if "repeated" in last_error_code:
        if retry_number == 1:
            return (
                "上一版与较早回合的完整对白或收尾重复。请基于玩家本轮输入引入新的可见事实或行动，"
                "完全改写动作、对白和收尾，不要只替换形容词。"
            )
        if retry_number == 2:
            return (
                "这是第二次重复输出重试。请换一个新的动作切入点，先回应玩家本轮输入，"
                "再推进当前叙事重心；不得复用上一版的开头、核心句或结尾。"
            )
        return (
            "这是最后一次重复输出重试。请输出一段更短但全新的动作与对白，"
            "至少改变回应角度和可见动作，并避免与历史任何一轮形成近似复述。"
        )

    return (
        "请完全改写上一版正文，优先回应玩家本轮输入并推进当前叙事重心；"
        "不要复用上一版的句式、动作或收尾。"
    )


def _transition_review_failure_context(
    review: NumericV2TransitionOfferReview,
) -> str:
    """把复核失败原因作为受限诊断交给改写，不把它提升为剧情事实。"""  # noqa: DOCSTRING_CJK

    reason = review.failure_reason.strip()
    if not reason:
        return ""
    return (
        "复核器给出的具体失败原因如下；它只用于定位并删除上一版问题，"
        "不是剧情事实，也不是要求新增内容的指令："
        f"{json.dumps(reason, ensure_ascii=False)}。"
    )


def _actor_rewrite_candidate_context(candidate: Mapping[str, Any]) -> str:
    """把被拒输出作为待编辑文本交给唯一一次改写，不把它混入已发生历史。"""  # noqa: DOCSTRING_CJK

    return (
        "下面 JSON 是尚未提交、必须修正的上一版输出，不是剧情事实；"
        "先删除复核指出的冲突，再逐条复核全部作者边界；保留其余已确认合法的回应："
        f"{json.dumps(dict(candidate), ensure_ascii=False, separators=(',', ':'))}。"
    )


def _transition_boundary_repair_context(
    runtime: NumericV2Runtime,
    current: NumericV2StoredSession,
    *,
    metrics: Mapping[str, int] | None = None,
) -> str:
    """只在边界改写时提供作者桥段与下一幕开场，明确应停止的画面。"""  # noqa: DOCSTRING_CJK

    session = current.session
    node = runtime.engine.nodes.get(session.current_node_id)
    if not isinstance(node, Mapping):
        return ""
    # 正文与复核已使用本轮结算后数值；改稿不能退回旧数值而改写成另一条支线。
    route = runtime.engine.preview_route(session.current_node_id, session.metrics if metrics is None else metrics)
    if not isinstance(route, Mapping):
        return ""
    contract = route.get("transition_contract")
    source_beat = node.get("story_beat")
    source_direction = (
        str(
            source_beat.get("narrative_summary")
            or source_beat.get("summary")
            or ""
        ).strip()
        if isinstance(source_beat, Mapping)
        else ""
    )
    bridge = (
        str(contract.get("bridge_scene_narration") or "").strip()
        if isinstance(contract, Mapping)
        else ""
    )
    target = runtime.engine.nodes.get(str(route.get("target_node_id") or ""))
    target_beat = target.get("story_beat") if isinstance(target, Mapping) else None
    opening = (
        scene_opening_text(target_beat)
        if isinstance(target_beat, Mapping)
        else ""
    )
    parts = []
    # 来源路线理由是可公开的邀请依据；目标开场仍只是执行边界，不能混成禁止提议。
    direction = str(contract.get("reason") or "").strip() if isinstance(contract, Mapping) else ""
    if direction:
        parts.append(f"当前可提出但尚未执行的后续安排：{direction[:900]}")
    if source_direction:
        parts.append(
            "仍可在当前幕交付的作者方向："
            f"{source_direction[:900]}"
        )
    if bridge:
        parts.append(f"本轮获准转场才可播放的作者桥段：{bridge[:600]}")
    if opening:
        parts.append(f"正式换幕后才成立的下一幕开场：{opening[:600]}")
    if not parts:
        return ""
    return (
        "以下内容用于区分当前幕可交付结果与正式换幕边界。"
        "保留玩家本轮已经实施的合法当前幕行动及其获准结果；删除提前播放的桥段或目标幕独有结果。"
        "此处仅定义场景边界，不覆盖本轮玩家所有权和作者事实的修复要求。"
        "桥段与下一幕开场只定义停止边界，不能把其独有结果写成已发生；"
        "仍可依据来源路线理由提出未来安排。改写只修冲突，不把正确邀请换成另一去向或追加任务；"
        "保持当前可用安排的时间、地点与阶段，保留玩家接受或暂缓的选择。"
        + " ".join(parts)
    )


@dataclass(frozen=True, slots=True)
class NumericV2TurnWorkflowResult:
    """回合模型调用和原子提交完成后交还给接口层的公开工作结果。"""  # noqa: DOCSTRING_CJK

    stored: NumericV2StoredSession
    outcome: TurnOutcomeV2
    performance: dict[str, Any]
    display_binding: Mapping[str, str]
    diagnostics: Mapping[str, Any]


def _add_elapsed_ms(
    diagnostics: dict[str, Any],
    phase: str,
    started_at: float,
) -> None:
    """累计阶段耗时；整回合墙钟时间仍单独记录。"""  # noqa: DOCSTRING_CJK

    elapsed_ms = round((time.monotonic() - started_at) * 1000, 3)
    timings = diagnostics["timings_ms"]
    timings[phase] = round(float(timings.get(phase, 0.0)) + elapsed_ms, 3)


def _increment_actor_attempts(diagnostics: dict[str, Any] | None) -> None:
    """记录 Actor 生成尝试次数；真实供应商请求由 Actor 的调用边界另行统计。"""  # noqa: DOCSTRING_CJK

    if diagnostics is not None:
        diagnostics["actor_generation_attempts"] = int(
            diagnostics.get("actor_generation_attempts", 0)
        ) + 1


async def _generate_actor_turn_with_output_retry(
    actor: NumericV2Actor,
    *,
    diagnostics: dict[str, Any] | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Actor 正文输出合同最多尝试四次，含第一次生成。"""  # noqa: DOCSTRING_CJK

    last_error_code = ""
    required_retry_hint = str(kwargs.get("retry_hint") or "").strip()
    for attempt in range(4):
        try:
            retry_kwargs = dict(kwargs)
            if attempt:
                # 重复输出重试必须逐次改变模型收到的任务提示，不能原样发送四次相同请求。
                outcome = kwargs.get("outcome")
                ledger_event = getattr(outcome, "ledger_event", {})
                route_changed = (
                    isinstance(ledger_event, Mapping)
                    and str(ledger_event.get("from_node_id") or "")
                    != str(ledger_event.get("to_node_id") or "")
                )
                output_retry_hint = _output_retry_hint(
                    last_error_code=last_error_code,
                    retry_number=attempt,
                    route_changed=route_changed,
                )
                # 场景边界改写属于本次生成的核心任务；即使改写稿另有格式错误，
                # 后续输出重试也必须继续携带它，不能退回普通生成而再次越幕。
                retry_kwargs["retry_hint"] = "\n".join(
                    part for part in (required_retry_hint, output_retry_hint) if part
                )
            _increment_actor_attempts(diagnostics)
            return await actor.generate_turn(**retry_kwargs)
        except NumericV2ActorOutputError as exc:
            last_error_code = str(exc)
            if attempt == 3:
                raise
            session = kwargs.get("session")
            logger.warning(
                "Numeric v2 Actor retrying rejected visible output: reason=%s session_id=%s revision=%s",
                str(exc),
                getattr(session, "session_id", ""),
                getattr(session, "revision", ""),
            )
    raise AssertionError("unreachable")


async def execute_numeric_v2_turn(
    *,
    config_manager: Any,
    runtime: NumericV2Runtime,
    current: NumericV2StoredSession,
    turn: TurnRequestV2,
    ensure_current_binding: Callable[[Any], Mapping[str, str]],
    before_commit: Callable[[], Awaitable[None]] | None = None,
    diagnostics_sink: dict[str, Any] | None = None,
) -> NumericV2TurnWorkflowResult:
    """固定执行 Evaluator、Runtime、正文与推荐生成、身份复验和原子提交。"""  # noqa: DOCSTRING_CJK

    workflow_started_at = time.monotonic()
    # 压测器可传入可变容器；即使本轮失败，也能读取已经完成的阶段与模型成本。
    diagnostics = diagnostics_sink if diagnostics_sink is not None else {}
    diagnostics.clear()
    diagnostics.update({
        "timings_ms": {
            "evaluator_work": 0.0,
            "runtime_prepare_work": 0.0,
            "actor_work": 0.0,
            "transition_judge_work": 0.0,
            "commit_work": 0.0,
            "total_wall": 0.0,
        },
        "evaluator_model_attempts": 0,
        "actor_generation_attempts": 0,
        "actor_provider_calls": 0,
        "actor_suggestion_fill_attempts": 0,
        "actor_suggestion_fill_provider_calls": 0,
        "actor_suggestion_fill_reasons": {},
        "actor_base_suggestion_parse_counts": {},
        "transition_judge_calls": 0,
        "transition_judge_degraded": False,
        # 每回合共享一个复查机会，改写稿不能再次触发；失败时保留快速初判。
        "dispute_review_attempts": 0,
        "dispute_review_degraded": False,
        "transition_review_results": [],
        "transition_ownership_retries": 0,
        "transition_scene_boundary_retries": 0,
        "transition_author_boundary_retries": 0,
        "transition_offer_retries": 0,
        "semantic_rewrite_attempts": 0,
        # 纠错预算耗尽后采用最后一版完整稿；标记仅供诊断，不能被当成复核通过或剧情事实。
        "semantic_review_fallback": False,
        "semantic_review_fallback_phase": "",
        "transition_cancellations": 0,
        # 漏判恢复只生成一次正式候选，不重新判分、不写入未提交普通稿。
        "missed_initiation_recoveries": 0,
        "phantom_transition_flags_cleared": 0,
        "unsafe_suggestions_removed": 0,
        "route_suggestion_reviews": 0,
        "evaluator_degraded": False,
        "input_source": turn.input_source,
        "completed": False,
    })
    evaluator = NumericV2MetricEvaluator(config_manager)
    actor = NumericV2Actor(config_manager)
    # 仅属于本次工作流的原文结果；所有正文重试与复核共享，不写入 Session 或 Ledger。
    history_lookup_result: dict[str, Any] | None = None
    # 在正文重采样前冻结真实人格输入；推荐失败由内部降级，最终正文仍须属于同一角色世代。
    generation_binding = ensure_current_binding(current.session)
    generation_profile = actor._character_profile()

    async def evaluate_turn() -> NumericV2EvaluationResult:
        """执行一次 Evaluator，并把模型故障保守降级为无状态变化。"""  # noqa: DOCSTRING_CJK

        started_at = time.monotonic()
        diagnostics["evaluator_model_attempts"] += 1
        try:
            return await evaluator.evaluate(
                engine=runtime.engine,
                session=current.session,
                message=turn.message,
                recent_ledger_events=current.ledger_events,
            )
        except NumericV2EvaluatorError as exc:
            # Evaluator 只负责隐藏数值和已有转场态度，不应让一次判定服务抖动阻断玩家的正常演绎。
            # 降级结果不会改变数值，也不会凭空接受转场；下一回合仍可重新判定。
            diagnostics["evaluator_degraded"] = True
            logger.warning(
                "Numeric v2 Evaluator degraded to no-op: reason=%s session_id=%s revision=%s",
                str(exc),
                current.session.session_id,
                current.session.revision,
            )
            return NumericV2EvaluationResult(
                metric_changes=(),
                scene_complete=False,
                transition_intent="unclear",
                interaction_intent="mixed_or_unclear",
            )
        finally:
            _add_elapsed_ms(diagnostics, "evaluator_work", started_at)

    def prepare_turn(evaluation: NumericV2EvaluationResult) -> TurnOutcomeV2:
        """执行确定性结算并累计同步 Runtime 耗时。"""  # noqa: DOCSTRING_CJK

        started_at = time.monotonic()
        try:
            return runtime.prepare_turn(
                current,
                turn,
                evaluation.metric_changes,
                scene_complete=evaluation.scene_complete,
                transition_intent=evaluation.transition_intent,
                # 同次判定提供结局就绪信号；缺省/降级为 false，不增加一轮确认或模型调用。
                natural_ending_ready=getattr(evaluation, "natural_ending_ready", False),
            )
        finally:
            _add_elapsed_ms(diagnostics, "runtime_prepare_work", started_at)

    async def generate_actor_turn(
        outcome: TurnOutcomeV2,
        *,
        retry_hint: str = "",
    ) -> dict[str, Any]:
        """按正式路径生成 Actor 正文；节奏只由同一次调用中的软提示引导。"""  # noqa: DOCSTRING_CJK

        started_at = time.monotonic()
        try:
            generation_kwargs = {
                "engine": runtime.engine,
                "session": current.session,
                "outcome": outcome,
                "player_input": turn.message,
                "character_profile": generation_profile,
                "interaction_intent": effective_interaction_intent,
                "input_source": turn.input_source,
                # 与 Evaluator 使用相同已提交 Ledger 定位原提议，包含所有格式/语义重试。
                "recent_ledger_events": current.ledger_events,
                "diagnostics": diagnostics,
            }
            if history_lookup_result is not None:
                generation_kwargs["history_lookup"] = history_lookup_result
            return await _generate_actor_turn_with_output_retry(
                actor,
                **generation_kwargs,
                retry_hint=retry_hint,
            )
        finally:
            # Actor 只可能因格式、重复或明确边界问题重试；这里记录累计调用耗时和真实供应商请求数。
            diagnostics["actor_provider_calls"] = int(
                getattr(actor, "provider_call_count", 0)
            )
            diagnostics["actor_suggestion_fill_attempts"] = int(
                getattr(actor, "suggestion_fill_attempt_count", 0)
            )
            diagnostics["actor_suggestion_fill_provider_calls"] = int(
                getattr(actor, "suggestion_fill_provider_call_count", 0)
            )
            diagnostics["actor_suggestion_fill_reasons"] = dict(
                getattr(actor, "suggestion_fill_reason_counts", {})
            )
            diagnostics["actor_base_suggestion_parse_counts"] = dict(
                getattr(actor, "base_suggestion_parse_counts", {})
            )
            _add_elapsed_ms(diagnostics, "actor_work", started_at)

    async def review_transition_offer(
        candidate: Mapping[str, Any],
    ) -> NumericV2TransitionOfferReview:
        """复核可见提议并累计调用成本；模型故障沿用原有保守撤销语义。"""  # noqa: DOCSTRING_CJK

        transition_judge_started_at = time.monotonic()
        diagnostics["transition_judge_calls"] += 1
        try:
            # 转场旁白也由 Actor 生成后，要从来源历史复核整段；普通回合沿用原证据与判断。
            changed = outcome.ledger_event["from_node_id"] != outcome.ledger_event["to_node_id"]
            review_kwargs = dict(
                engine=runtime.engine,
                session=current.session if changed else outcome.session,
                message=turn.message,
                actor_performance=candidate,
                scene_complete=evaluation.scene_complete,
                route_changed=(
                    outcome.ledger_event["from_node_id"]
                    != outcome.ledger_event["to_node_id"]
                ),
                **({"transition_outcome": outcome} if changed else {}),
            )
            if history_lookup_result is not None:
                review_kwargs["history_lookup"] = history_lookup_result
            # 快检、争议复查及正文重写后都沿用同一份已核对原文；不重新从作者方向猜公开事实。
            if changed and outcome.ledger_event.get("transition_intent") == "initiate":
                review_kwargs["public_destination_quote"] = evaluation.public_destination_quote
            # 仅补查未识别的普通主动请求；拒绝、已有待确认邀请、开场和正式转场不走此入口。
            if (not changed and evaluation.transition_intent == "unclear"
                    and not current.session.transition_offered
                    and not diagnostics["missed_initiation_recoveries"]
                    and not diagnostics["transition_cancellations"]):
                review_kwargs["check_missed_initiation"] = True
            review = await evaluator.validate_transition_offer(**review_kwargs)

            def record_review(result: NumericV2TransitionOfferReview, mode: str) -> None:
                # 两次判断分别留作诊断，不混入剧情历史，也不把初判理由喂给独立复查。
                diagnostics["transition_review_results"].append({
                    "review_mode": mode,
                    "offer_present": result.offer_present,
                    "valid": result.valid,
                    "player_action_preserved": result.player_action_preserved,
                    "scene_boundary_preserved": result.scene_boundary_preserved,
                    "author_boundaries_preserved": result.author_boundaries_preserved,
                    "unsafe_suggestion_indexes": list(result.unsafe_suggestion_indexes),
                    "body_violations": list(result.body_violations),
                    "failure_reason": result.failure_reason,
                    "missed_initiation": result.missed_initiation,
                    "initiation_authorized": result.initiation_authorized,
                })

            record_review(review, "fast")
            if not diagnostics["dispute_review_attempts"] and (
                review.body_violations or (review.offer_present and not review.valid)
            ):
                diagnostics["dispute_review_attempts"] += 1
                diagnostics["transition_judge_calls"] += 1
                try:
                    reviewed = await evaluator.validate_transition_offer(**review_kwargs, dispute_review=True)
                except NumericV2EvaluatorError as exc:
                    # 此处必须与快速复核故障分开：已有违规证据不可被普通回合的降级路径清空。
                    diagnostics["dispute_review_degraded"] = True
                    diagnostics["transition_review_results"].append({
                        "review_mode": "dispute", "degraded": True, "failure_reason": str(exc),
                    })
                else:
                    record_review(reviewed, "dispute")
                    review = reviewed
            return review
        except NumericV2EvaluatorError as exc:
            diagnostics["transition_judge_degraded"] = True
            diagnostics["transition_review_results"].append({
                "degraded": True,
                "failure_reason": str(exc),
            })
            logger.warning(
                "Numeric v2 transition judge degraded to reject: reason=%s session_id=%s revision=%s",
                str(exc),
                current.session.session_id,
                current.session.revision,
            )
            if changed:
                # 动态旁白不能再依赖静态作者原文兜底；未完成复核就不提交换场，保留完整事务回滚。
                raise NumericV2ActorOutputError("numeric_v2_transition_review_failed") from exc
            # 服务或协议故障不是正文违规证据：保留既有降级，但不凭新提议换幕，也不为服务故障改稿。
            return NumericV2TransitionOfferReview(
                offer_present=False,
                valid=False,
                body_violations=(),
                unsafe_suggestion_indexes=(),
            )
        finally:
            _add_elapsed_ms(
                diagnostics,
                "transition_judge_work",
                transition_judge_started_at,
            )

    evaluation = await evaluate_turn()
    if evaluation.history_query:
        # 普通回合不额外调用；有证据缺口才查一次完整记录，失败结果也共享，防止改稿反复查找。
        lookup_started_at = time.monotonic()
        history_lookup_result = await lookup_history(config_manager, current.session, evaluation.history_query)
        diagnostics["history_lookup"] = {key: value for key, value in history_lookup_result.items() if key != "evidence"}
        _add_elapsed_ms(diagnostics, "history_lookup_work", lookup_started_at)
    diagnostics["interaction_intent"] = evaluation.interaction_intent
    # 保留模型判断依据供定位误判；不传给演员、不写入剧情历史、不改变结束条件。
    diagnostics["ending_reason"] = evaluation.ending_reason
    effective_interaction_intent = (
        evaluation.interaction_intent
        if turn.input_source == "freeform"
        else "mixed_or_unclear"
    )
    diagnostics["effective_interaction_intent"] = effective_interaction_intent
    outcome = prepare_turn(evaluation)
    performance = await generate_actor_turn(outcome)
    route_changed = (
        outcome.ledger_event["from_node_id"]
        != outcome.ledger_event["to_node_id"]
    )
    reviewed_transition_offered = False
    if (
        not route_changed
        and not outcome.session.transition_offered
        and (
            performance.get("transition_offered") is True
            or str(performance.get("performance") or "").strip()
            or str(performance.get("scene_narration") or "").strip()
        )
    ):
        # 正文违规和无效邀请共用一次改写；首次争议复查后仍违规才重写，不按错误类别叠加。
        for rewrite_attempt in range(2):
            transition_review = await review_transition_offer(performance)
            # 用户允许补查漏判后重走正式转场；普通稿不提交，也不作为公开去向证据。
            if (transition_review.missed_initiation and evaluation.transition_intent == "unclear"
                    and not current.session.transition_offered
                    and not diagnostics["missed_initiation_recoveries"]):
                recovered_evaluation = replace(
                    evaluation, transition_intent="initiate", interaction_intent="scene_action",
                    public_destination_quote=transition_review.public_destination_quote,
                    natural_ending_ready=False,
                )
                # prepare_turn始终从current计算，绝不能拿已加分的outcome.session再结算一次。
                recovered_outcome = prepare_turn(recovered_evaluation)
                if recovered_outcome.ledger_event["from_node_id"] != recovered_outcome.ledger_event["to_node_id"]:
                    diagnostics["missed_initiation_recoveries"] += 1
                    evaluation, outcome = recovered_evaluation, recovered_outcome
                    effective_interaction_intent = "scene_action"
                    diagnostics["effective_interaction_intent"] = effective_interaction_intent
                    route_changed = True
                    performance = await generate_actor_turn(outcome)
                    break
            performance, removed_suggestions = _drop_reported_unsafe_suggestions(
                performance, transition_review.unsafe_suggestion_indexes,
            )
            diagnostics["unsafe_suggestions_removed"] += removed_suggestions
            invalid_offer = (
                transition_review.offer_present and not transition_review.valid
            )
            if not transition_review.body_violations and not invalid_offer:
                # 推荐只影响按钮本身；定点删除后不重审已判定的正文，也不再次为凑数量调用模型。
                reviewed_transition_offered = (
                    transition_review.offer_present and transition_review.valid
                )
                if performance.get("transition_offered") is True and not reviewed_transition_offered:
                    if not diagnostics["transition_judge_degraded"]:
                        diagnostics["phantom_transition_flags_cleared"] += 1
                performance = {
                    **performance,
                    "transition_offered": reviewed_transition_offered,
                }
                break
            if rewrite_attempt:
                # 用户允许持续语义否定后继续演绎：末稿走原子提交并进入真实历史，不另造展示副本。
                diagnostics["semantic_review_fallback"] = True
                diagnostics["semantic_review_fallback_phase"] = "ordinary"
                # 已公开邀请可留待下一轮接受；不要求其再次获Guard认可，也不凭Actor幽灵标记制造邀请。
                # 本轮是否换幕仍由上方Runtime结果决定，不能在兜底处直接改节点或再次结算数值。
                reviewed_transition_offered = transition_review.offer_present
                performance = {**performance, "transition_offered": reviewed_transition_offered}
                break

            diagnostics["semantic_rewrite_attempts"] += 1
            for violation, counter in (
                ("player_action", "transition_ownership_retries"),
                ("scene_boundary", "transition_scene_boundary_retries"),
                ("author_boundary", "transition_author_boundary_retries"),
            ):
                if violation in transition_review.body_violations:
                    diagnostics[counter] += 1
            if invalid_offer:
                diagnostics["transition_offer_retries"] += 1
            boundary_context = (
                _transition_boundary_repair_context(runtime, current, metrics=outcome.session.metrics)
                if "scene_boundary" in transition_review.body_violations or invalid_offer
                else ""
            )
            # 被拒稿仅是编辑材料，不进入历史；保留具体原因，禁止用修复任务追加结果或收束压力。
            performance = await generate_actor_turn(
                outcome,
                retry_hint=(
                    "这是唯一一次正文与提议修复，仅编辑未提交候选，不是继续扩写剧情。"
                    "以玩家实际输入、已提交历史和作者硬边界为准，保留获准回应，"
                    "删除未表达的后续操作及正文、场景更新、推荐中依赖它的结果。"
                    "候选 scene_narration 对应输出 scene_update，也必须一起修正；没有新变化就省略。"
                    "猫娘可用自身反应、回答或明确未知承接玩家，不要求本轮推进剧情或产生外部结果。"
                    "不得为了交付结果、收束或兑现旧推荐补造操作、事实或下一阶段，也不能撤销玩家已做的合法动作。"
                    "只有正文已公开具体、合乎当前事实且与实际下一阶段一致的未来邀请时才设 transition_offered=true；"
                    "邀请停在执行前，按钮不能代替正文首次提出转场。没有合适出口就不提议，不追加前提清单。"
                    f"{_actor_rewrite_candidate_context(performance)}"
                    f"{_transition_review_failure_context(transition_review)}"
                    f"{boundary_context}"
                ),
            )
    if route_changed:
        # 三段与首轮按钮合并一次复核；仅明确正文冲突可改写一次，不能用静态旁白覆盖或强制结束。
        diagnostics["route_suggestion_reviews"] += int(bool(performance.get("suggested_inputs")))
        for rewrite_attempt in range(2):
            review = await review_transition_offer(performance)
            performance, removed = _drop_reported_unsafe_suggestions(performance, review.unsafe_suggestion_indexes)
            diagnostics["unsafe_suggestions_removed"] += removed
            if not review.body_violations:
                break
            if review.initiation_authorized is False and not diagnostics["semantic_rewrite_attempts"]:
                # 用户允许撤销未提交的错误主动转场，用原有一次改稿留幕回应。
                # 从原始快照及同一次计分重新prepare，不能从已换幕候选倒扣或再次累计分数。
                diagnostics["transition_cancellations"] += 1
                diagnostics["semantic_rewrite_attempts"] += 1
                evaluation = replace(evaluation, transition_intent="unclear",
                                     natural_ending_ready=False, public_destination_quote="")
                outcome = prepare_turn(evaluation)
                route_changed = False
                effective_interaction_intent = "scene_action"
                diagnostics["effective_interaction_intent"] = effective_interaction_intent
                # 不把错误目标三段作为改写底稿，避免将未播放的未来事实带回当前幕。
                performance = await generate_actor_turn(outcome, retry_hint=(
                    "此前候选换幕因公开去向与玩家授权不符已取消，三段均未播放。"
                    "本轮留在当前幕，承接玩家原话所指的实际已公开行动或去向。"
                    "不要要求玩家重复输入，不执行已取消的下一幕安排，不新增额外任务。"
                    + _transition_review_failure_context(review)
                ))
                # 已用完共享改稿额度，只核对这一份留幕稿；禁用同轮主动请求补查，避免反复换幕。
                review = await review_transition_offer(performance)
                performance, removed = _drop_reported_unsafe_suggestions(performance, review.unsafe_suggestion_indexes)
                diagnostics["unsafe_suggestions_removed"] += removed
                if review.body_violations or (review.offer_present and not review.valid):
                    diagnostics["semantic_review_fallback"] = True
                    diagnostics["semantic_review_fallback_phase"] = "ordinary"
                reviewed_transition_offered = review.offer_present
                performance = {**performance, "transition_offered": reviewed_transition_offered}
                break
            if rewrite_attempt or diagnostics["semantic_rewrite_attempts"]:
                # 普通稿补查恢复成转场时共享同一改稿额度；额度用完采用当前完整三段，不再追加调用。
                diagnostics["semantic_review_fallback"] = True
                diagnostics["semantic_review_fallback_phase"] = "transition"
                break
            # 和普通回合共用诊断计数，让新增转场复核的实际改写成本可追踪。
            diagnostics["semantic_rewrite_attempts"] += 1
            performance = await generate_actor_turn(outcome, retry_hint=(
                "正式转场尚未提交，请修正指出的三段事实冲突，保留其余合法内容，不新增玩家行动或前提。"
                + _transition_review_failure_context(review)
                + _actor_rewrite_candidate_context(performance)
            ))
    # 新提议按正文复核结论锁存；超限兜底允许保留已公开但被判无效的邀请，不能只凭Actor布尔标记。
    # Runtime 已经根据本轮 Evaluator 结果计算出转场生命周期，尤其是 unclear 时必须保留旧提议；
    # 这里不能再用 Actor 的 false 覆盖 Runtime 的 true，否则下一轮 Evaluator 会失去可见提议。
    # Workflow 提交已复核或明确兜底的新提议信号；旧状态保留及三份记录同步均由Runtime决定。
    outcome, performance = runtime.engine.finalize_transition_offer_state(
        outcome,
        performance,
        new_offer=(
            performance.get("transition_offered") is True
            or reviewed_transition_offered
        ),
    )
    # 模型调用不占生命周期锁；仅将身份复验、展示刷新和原子提交与角色改名串行。
    commit_started_at = time.monotonic()
    try:
        async with character_config_mutation_lock:
            # 模型调用期间角色卡可能切换；成功输出不能提交到另一只猫娘的恢复槽位。
            display_binding = ensure_current_binding(current.session)
            current_profile = actor._character_profile()
            same_display_name = str(display_binding.get("catgirl_name") or "") == str(
                generation_binding.get("catgirl_name") or ""
            )
            if current_profile != generation_profile or (
                same_display_name
                and str(display_binding.get("profile_hash") or "")
                != str(generation_binding.get("profile_hash") or "")
            ):
                # 同名角色资料或实际人格文本已改变；旧人格输出不能伪装成新版本提交。
                raise ValueError("catgirl_profile_changed_requires_retry")
            refreshed_binding = {
                str(key): str(value)
                for key, value in display_binding.items()
            }
            # 本轮 Ledger 已按模型调用前的称呼事实计算；只刷新角色展示字段，避免称呼并发变化破坏重放。
            refreshed_binding["player_address"] = str(
                outcome.session.catgirl_binding.get("player_address") or ""
            )
            outcome = replace(
                outcome,
                session=replace(
                    outcome.session,
                    # 不可变角色 ID 已通过校验；提交前刷新名称和人格版本，避免并发改名被旧候选覆盖。
                    catgirl_binding=refreshed_binding,
                ),
            )
            # 角色锁始终先于故事锁，保持与归档、遗忘链路一致的锁顺序。
            async with runtime.story_session_guard():
                if before_commit is not None:
                    # 长耗时模型调用结束后再次检查云存档写栅栏，避免请求期间进入维护态仍然提交。
                    await before_commit()
                stored = await runtime.commit_turn(outcome, performance)
    finally:
        # 身份复验、写栅栏或存储失败也要留下提交阶段耗时，供失败样本定位。
        _add_elapsed_ms(diagnostics, "commit_work", commit_started_at)
    diagnostics["timings_ms"]["total_wall"] = round(
        (time.monotonic() - workflow_started_at) * 1000,
        3,
    )
    diagnostics["completed"] = True
    logger.info(
        "Numeric v2 workflow timing: session_id=%s revision=%s diagnostics=%s",
        current.session.session_id,
        stored.session.revision,
        diagnostics,
    )
    return NumericV2TurnWorkflowResult(
        stored=stored,
        outcome=outcome,
        performance=performance,
        display_binding=display_binding,
        diagnostics=diagnostics,
    )


__all__ = [
    "NumericV2TurnWorkflowResult",
    "execute_numeric_v2_turn",
    "generate_validated_opening",
]
