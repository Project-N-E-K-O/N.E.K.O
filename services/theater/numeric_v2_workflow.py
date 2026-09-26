"""Numeric v2 的应用级回合工作流，不处理 HTTP 请求与响应映射。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, replace
import json
import logging
import re
import time
from typing import Any, Callable, Mapping

from utils.character_memory import character_config_mutation_lock

from .numeric_v2_actor import (
    NumericV2Actor,
    NumericV2ActorOutputError,
)
from .numeric_v2_action_projection import (
    normalize_player_action_projection,
    project_player_action_result,
)
from .numeric_v2_context import (
    missing_contract_names,
    pending_transition_performance,
    pending_transition_record,
    premature_target_markers,
    premature_target_scene_facts,
    scene_opening_text,
    transition_bridge_leak_markers,
)
from .numeric_v2_fixed_narration import apply_triggers
from .numeric_v2_history import lookup_history
from .numeric_v2_evaluator import (
    NumericV2EvaluationResult,
    NumericV2EvaluatorError,
    NumericV2MetricEvaluator,
    NumericV2TransitionOfferReview,
)
from .numeric_v2_options import aload_theater_module_options
from .numeric_v2_performance import mixed_performance_blocks, performance_content_blocks
from .numeric_v2_runtime import (
    NumericV2Engine,
    NumericV2Runtime,
    NumericV2RuntimeError,
    TurnOutcomeV2,
    TurnRequestV2,
)
from .numeric_v2_store import NumericV2StoredSession
from .numeric_v2_trace import text_trace_scope, trace_event, trace_state


logger = logging.getLogger(__name__)

# 整回合复核时间预算。首次快检始终执行；预算耗尽后不再追加争议复查或改写后复检，
# 普通回合沿用最近一次判定并按既有末稿兜底处理，正式转场沿用原有的"未完成复核不提交"回滚。
# 该预算只是等待上限，不改变任何授权、去向或原子提交判定。
NUMERIC_V2_REVIEW_BUDGET_SECONDS = 20.0
# 单次争议复查最多等待 8 秒；超出后仍沿用既有保守回滚/兜底，不改变授权判定。
NUMERIC_V2_DISPUTE_TIMEOUT_CAP_SECONDS = 8.0
# 只拦截推荐中“把当前正文没有交付的外部结果写成事实”的明确句式；
# 模糊的语义归属、物品持有者和剧情合理性仍交给现有复核，不在这里猜测。
_SUGGESTION_RESULT_CLAIM_MARKERS = (
    "读数显示",
    "读数是",
    "结果是",
    "方向是",
    "指向",
    "已经找到",
    "已经打开",
    "已经到达",
    "成功了",
)


def _completion_action_terms(value: Any) -> set[str]:
    """提取可逐字核对的短语，供当前幕动作与完成事实做三方交集。"""  # noqa: DOCSTRING_CJK

    result: set[str] = set()
    for unit in re.findall(r"[^\W_]+", str(value or "").casefold(), flags=re.UNICODE):
        if unit.isascii():
            if len(unit) >= 3:
                result.add(unit)
            continue
        for size in range(2, min(len(unit), 8) + 1):
            result.update(unit[start:start + size] for start in range(len(unit) - size + 1))
    return result


_GENERIC_COMPLETION_ACTION_TERMS = _completion_action_terms(
    "好的 可以 已经 现在 一起 我们 你们 他们 进入 进去 开始 继续 完成 安全 "
    "这里 那里 这个 那个 需要 还是 然后"
)


def _current_scene_completion_offer_evidence(
    *,
    engine: NumericV2Engine,
    session: Any,
    player_input: str,
    review: NumericV2TransitionOfferReview,
) -> tuple[str, ...]:
    """识别被误报成出口邀请、但实际承接本轮完成事实的当前幕动作。"""  # noqa: DOCSTRING_CJK

    if not review.offer_quote or not review.fact_candidates:
        return ()
    node = engine.nodes.get(str(getattr(session, "current_node_id", "") or ""))
    contract = node.get("completion_contract") if isinstance(node, Mapping) else None
    requirements = {
        str(item.get("key") or "")
        for item in (contract.get("all") if isinstance(contract, Mapping) else ()) or ()
        if isinstance(item, Mapping) and str(item.get("key") or "")
    }
    if not requirements:
        return ()
    quote_terms = _completion_action_terms(review.offer_quote)
    player_terms = _completion_action_terms(player_input)
    matches: set[str] = set()
    for candidate in review.fact_candidates:
        if not isinstance(candidate, Mapping):
            continue
        key = str(candidate.get("key") or "")
        if key not in requirements:
            continue
        definition = engine.fact_contract.get(key)
        if not isinstance(definition, Mapping):
            continue
        fact_terms = _completion_action_terms(
            "\n".join((
                str(definition.get("description") or ""),
                str(candidate.get("evidence_quote") or ""),
            ))
        )
        matches.update(
            quote_terms
            & player_terms
            & fact_terms
            - _GENERIC_COMPLETION_ACTION_TERMS
        )
    # 最长片段最便于日志回溯；短片段只用于证明三份原文确实指向同一个当前幕对象。
    return tuple(sorted(matches, key=lambda item: (-len(item), item))[:4])


def _review_denies_narration_only_offer(
    candidate: Mapping[str, Any],
    review: NumericV2TransitionOfferReview,
) -> bool:
    """复核若明确说明旁白只公开了位置、没有发出邀请，不能又保留邀请标志。"""  # noqa: DOCSTRING_CJK

    if (
        not review.offer_present
        or review.valid
        or review.body_violations
        or not review.offer_quote
    ):
        return False
    reason = str(review.failure_reason or "")
    if not ("正文仅" in reason and "未发出" in reason and "邀请" in reason):
        return False
    quote_sources = {
        str(block.get("type") or "")
        for block in performance_content_blocks(candidate)
        if review.offer_quote in str(block.get("text") or "")
    }
    return "narration" in quote_sources and "dialogue" not in quote_sources


def _review_mislabels_explicit_player_movement(
    review: NumericV2TransitionOfferReview,
    player_action_projection: Mapping[str, Any] | None = None,
) -> bool:
    """复核若承认移动正是玩家本轮明确要求，就不能又把同一动作判成代做。"""  # noqa: DOCSTRING_CJK

    if (
        review.offer_present
        or tuple(review.body_violations) != ("player_action",)
    ):
        return False
    reason = str(review.failure_reason or "")
    explicitly_requested = (
        "玩家本轮明确要求" in reason
        or "玩家本轮才明确要求" in reason
        or "玩家本轮明确表达的移动动作" in reason
    )
    projected_departure = bool(
        isinstance(player_action_projection, Mapping)
        and normalize_player_action_projection(player_action_projection).get(
            "player_left_current_scene"
        )
        and any(marker in reason for marker in ("离开", "离场", "移动", "转移", "走向", "走出"))
    )
    if not (explicitly_requested or projected_departure):
        return False
    if not any(marker in reason for marker in ("移动", "转移", "离开", "离场", "走向", "走出")):
        return False
    # 只清除“执行的就是本轮要求”这一种自相矛盾；目的地错配、额外操作仍交给原链路拦截。
    return not any(
        marker in reason
        for marker in (
            "但", "却", "不一致", "不相符", "不同", "额外", "除此", "超出",
            "返回", "回到", "重新进入", "重新回", "折返", "回来",
        )
    )


_PLAYER_DEPARTURE_RETURN_MARKERS = (
    "返回",
    "回到",
    "重新进入",
    "重新回",
    "折返",
    "回来",
    "回了当前",
    "回了原",
)
_PLAYER_ACTION_REVIEW_ASSERTION_MARKERS = (
    "写成",
    "写回",
    "把玩家",
    "让玩家",
    "视为",
    "描述",
    "正文",
    "scene_update",
    "场景更新",
    "旁白",
)
_PLAYER_ACTION_SCENE_UPDATE_MARKERS = (
    "scene_update",
    "scene narration",
    "场景更新",
    "场景旁白",
)
_PLAYER_ACTION_NON_SCENE_UPDATE_MARKERS = (
    "performance",
    "对白",
    "猫娘正文",
    "角色正文",
)


def _player_action_projection_conflicts_with_review(
    review: NumericV2TransitionOfferReview,
    player_action_projection: Mapping[str, Any] | None,
) -> bool:
    """识别“已离场却被正文写回当前幕”的结构化冲突。"""  # noqa: DOCSTRING_CJK

    if (
        review.offer_present
        or tuple(review.body_violations) != ("player_action",)
        or not isinstance(player_action_projection, Mapping)
    ):
        return False
    projection = normalize_player_action_projection(player_action_projection)
    if not projection.get("player_left_current_scene"):
        return False
    reason = str(review.failure_reason or "")
    return (
        any(marker in reason for marker in _PLAYER_DEPARTURE_RETURN_MARKERS)
        and any(marker in reason for marker in _PLAYER_ACTION_REVIEW_ASSERTION_MARKERS)
    )


def _safe_degrade_conflicting_scene_update(
    candidate: Mapping[str, Any],
    review: NumericV2TransitionOfferReview,
    player_action_projection: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """只裁掉被 Review 定位为冲突的场景更新，避免把整段安全对白一起丢掉。"""  # noqa: DOCSTRING_CJK

    if not _player_action_projection_conflicts_with_review(review, player_action_projection):
        return None
    reason = str(review.failure_reason or "")
    if not any(marker in reason for marker in _PLAYER_ACTION_SCENE_UPDATE_MARKERS):
        return None
    if any(marker in reason for marker in _PLAYER_ACTION_NON_SCENE_UPDATE_MARKERS):
        return None
    if not str(candidate.get("scene_narration") or "").strip():
        return None
    degraded = dict(candidate)
    degraded.pop("scene_narration", None)
    # 场景更新是唯一被否定的结构；同一候选的事实证据可能来自被删除的旁白，不能继续提交。
    degraded.pop("fact_candidates", None)
    degraded["transition_offered"] = False
    return degraded


def _normalized_suggestion_claim_text(value: Any) -> str:
    """去掉空白和标点，供确定性比较推荐中的短结果断言。"""  # noqa: DOCSTRING_CJK

    return "".join(
        character
        for character in str(value or "")
        if character.isalnum() or character == "_"
    ).casefold()


def _suggestion_has_unproven_result_claim(suggestion: str, visible_text: str) -> bool:
    """只识别带明确结果标记、且短断言未出现在当前可见正文中的推荐。"""  # noqa: DOCSTRING_CJK

    blocks = mixed_performance_blocks(suggestion)
    if [block.get("type") for block in blocks] != ["action", "dialogue"]:
        return False
    dialogue = str(blocks[1].get("text") or "").strip()
    # 问句是在请求信息，不是把结果写成已知事实；交给模型复核判断是否越权。
    if any(marker in dialogue for marker in ("？", "?", "吗", "什么", "怎么", "是否", "有没有")):
        return False
    normalized_visible = _normalized_suggestion_claim_text(visible_text)
    if not normalized_visible:
        return False
    for marker in _SUGGESTION_RESULT_CLAIM_MARKERS:
        marker_index = dialogue.find(marker)
        if marker_index < 0:
            continue
        # 取标记前后短窗口，要求该完整片段已经在本轮可见内容中出现；
        # 这样“读数显示蘑菇村”会被拦下，而“让我看看读数”不会被误杀。
        claim_window = dialogue[marker_index: marker_index + len(marker) + 6]
        if _normalized_suggestion_claim_text(claim_window) not in normalized_visible:
            return True
    return False


def _prefilter_suggestion_candidates(
    candidate: Mapping[str, Any],
) -> tuple[dict[str, Any], int, tuple[str, ...]]:
    """在模型复核前删除确定性可证的未来结果推荐，不改正文和转场字段。"""  # noqa: DOCSTRING_CJK

    result = dict(candidate)
    suggestions = candidate.get("suggested_inputs")
    if not isinstance(suggestions, list) or not suggestions:
        return result, 0, ()
    visible_text = "\n".join(
        str(block.get("text") or "")
        for block in performance_content_blocks(candidate)
        if block.get("type") != "action" and str(block.get("text") or "").strip()
    )
    kept: list[str] = []
    reasons: list[str] = []
    for suggestion in suggestions:
        text = str(suggestion or "").strip()
        if text and _suggestion_has_unproven_result_claim(text, visible_text):
            reasons.append("unproven_result_claim")
            continue
        kept.append(suggestion)
    removed = len(suggestions) - len(kept)
    if removed:
        result["suggested_inputs"] = kept
        trace_event(
            "suggestions.filtered_deterministic",
            removed=removed,
            reasons=reasons,
            before=suggestions,
            after=kept,
        )
    return result, removed, tuple(reasons)


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
        trace_event("suggestions.filtered", reported_indexes=unsafe_indexes, before=suggestions, after=[])
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
    trace_event("suggestions.filtered", reported_indexes=unsafe_indexes,
                before=suggestions, after=result["suggested_inputs"])
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

    trace_event("opening.context", session_id=session_id, story_id=engine.story_id,
                package_hash=engine.compiled.package_hash, package_revision=engine.story["meta"]["revision"],
                actor_budget_profile=actor_budget_profile)
    actor = NumericV2Actor(config_manager)
    opening_options = await aload_theater_module_options()
    opening = await actor.generate_opening(
        engine=engine,
        actor_budget_profile=actor_budget_profile,
        # 开场只等待一次 Actor 正文；推荐按钮补全留给后续回合，避免进入演绎前再串行等一次模型请求。
        allow_suggestion_fill=False,
    )
    trace_event("opening.candidate", attempt=1, performance=opening)
    start_node = engine.nodes[str(engine.story["start_node_id"])]
    opening_boundaries = start_node["story_beat"].get("opening_only_boundaries")
    if not opening_boundaries:
        trace_event("opening.ready", performance=opening)
        return opening

    if not opening_options.get("review"):
        # 复核模块关闭：开场不做模型复核与改写，直接交付演员输出。
        trace_event("review.skipped", phase="opening")
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
            trace_event("review.failed", phase="opening", error_code=str(exc))
            raise NumericV2ActorOutputError(
                "numeric_v2_opening_review_failed"
            ) from exc
        trace_event("review.result", phase="opening", attempt=attempt + 1, result=review)
        # 正文与按钮已分别判定；删掉坏按钮不会改变正文事实或制造新的离幕提议。
        opening, _ = _drop_reported_unsafe_suggestions(
            opening, review.unsafe_suggestion_indexes,
        )
        if not review.body_violations and not review.offer_present:
            trace_event("opening.ready", performance=opening)
            return opening
        if attempt == 0:
            trace_event("opening.rewrite", review=review)
            opening = await actor.generate_opening(
                engine=engine,
                actor_budget_profile=actor_budget_profile,
                allow_suggestion_fill=False,
                retry_hint=(
                    "上一版正文或推荐没有遵守 opening_only_boundaries。"
                    "只保留开场已授权的可见事实，不得提前交付后续阶段内容，也不得提出离幕行动。"
                    f"具体失败：{review.failure_reason or '公开开场边界未通过。'}"
                    f"{_actor_rewrite_candidate_context(opening)}"
                ),
            )
            trace_event("opening.candidate", attempt=2, performance=opening)
    raise NumericV2ActorOutputError("numeric_v2_opening_fact_boundary")


def _output_retry_hint(
    *,
    last_error_code: str,
    retry_number: int,
    route_changed: bool,
) -> str:
    """Give each body retry a distinct rewriting angle to avoid repeating the same sampling path."""

    if route_changed:
        # 重试承接真实授权，兼容接受、主动前往与自然结束，不虚构邀请。
        if retry_number == 1:
            return (
                "这是正式换场重试。请先用全新的简短来源回应承接玩家本轮实际授权的行动，"
                "再写新的过渡桥段；只用各段发声策略允许的表现，不要复用上一幕或上一版的来源正文和收尾。"
            )
        if retry_number == 2:
            return (
                "这是第二次正式换场重试。请在来源发声策略内改用不同的回应承接玩家本轮行动，"
                "重新组织过渡桥段并引入一个当前事实支持的变化；目标开场只需自然接入，"
                "不要复述上一版内容。"
            )
        return (
            "这是最后一次正式换场重试。请在来源发声策略内用最简短的全新回应完成承接，"
            "保留必要的过渡因果但完全改写句式和收尾；不要复制任何较早回合的正文。"
        )

    if "repeated" in last_error_code:
        if retry_number == 1:
            return (
                "上一版与较早回合的完整正文或收尾重复。请基于玩家本轮输入引入新的可见事实或行动，"
                "在当前发声策略内改写获准的表现和收尾，不要只替换形容词。"
            )
        if retry_number == 2:
            return (
                "这是第二次重复输出重试。请换一个新的动作切入点，先回应玩家本轮输入，"
                "再推进当前叙事重心；不得复用上一版的开头、核心句或结尾。"
            )
        return (
            "这是最后一次重复输出重试。请在当前发声策略内输出一段更短但全新的回应，"
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


def _source_side_delivery(performance: Mapping[str, Any]) -> Mapping[str, Any]:
    """换场候选里属于来源幕的部分：来源回应与过渡桥。

    目标幕开场是作者写给下一幕的正文，天然包含目标幕的事实；用它核对来源幕禁令会把
    正常换场判成越界（问题2.143的run-D反例），因此边界核对只看来源侧两段。
    """  # noqa: DOCSTRING_CJK

    segments = performance.get("segments") if isinstance(performance, Mapping) else None
    if not isinstance(segments, list):
        return performance
    kept = [
        dict(segment) for segment in segments
        if isinstance(segment, Mapping) and segment.get("phase") in ("source_response", "transition_bridge")
    ]
    if not kept:
        return performance
    return {"segments": kept}


def _terminal_new_question_markers(
    *,
    engine: NumericV2Engine,
    outcome: TurnOutcomeV2,
    performance: Mapping[str, Any],
) -> tuple[str, ...]:
    """结局交付不得留下需要玩家回答的新问题；返回稳定诊断标记而不记录正文。"""  # noqa: DOCSTRING_CJK

    target_id = str(outcome.ledger_event.get("to_node_id") or "")
    target = engine.nodes.get(target_id)
    if not isinstance(target, Mapping) or not (
        target.get("type") == "ending" or target.get("terminal") is True
    ):
        return ()
    if any(
        marker in str(block.get("text") or "")
        for block in performance_content_blocks(performance)
        for marker in ("？", "?")
    ):
        return ("terminal_new_question",)
    return ()


def _actor_fact_evidence_text(performance: Mapping[str, Any]) -> str:
    """提取最终可见正文，供 Actor 事实候选做逐字引文核验。"""  # noqa: DOCSTRING_CJK

    segments = performance.get("segments")
    if isinstance(segments, list):
        return "\n".join(
            str(segment.get(field) or "").strip()
            for segment in segments
            if isinstance(segment, Mapping)
            for field in ("scene_narration", "performance")
            if str(segment.get(field) or "").strip()
        )
    return "\n".join(
        str(performance.get(field) or "").strip()
        for field in ("scene_narration", "performance")
        if str(performance.get(field) or "").strip()
    )


def _pending_offer_acceptance_path(session: Any) -> str:
    """只认最近一次已提交演绎中带 transition_offered 的那一条的第一条推荐。

    取"最后一条演绎"会接受更早回合留下的旧提议，从而把剧情倒着送回前面的幕；
    因此这里要求提议来自最近一次提交，并且该条自己就带提议标记。
    """  # noqa: DOCSTRING_CJK

    records = tuple(getattr(session, "performance_history", ()) or ())
    if not records:
        return ""
    last = records[-1]
    parts = last.get("segments") if isinstance(last, Mapping) and isinstance(last.get("segments"), list) else [last]
    for part in reversed(parts):
        if not isinstance(part, Mapping) or part.get("transition_offered") is not True:
            continue
        suggestions = part.get("suggested_inputs")
        if isinstance(suggestions, list) and suggestions:
            first = str(suggestions[0] or "").strip()
            if first:
                return first
    return ""


def _preserve_pending_acceptance_suggestion(
    performance: Mapping[str, Any],
    *,
    current: NumericV2StoredSession,
    keep_pending: bool,
) -> tuple[dict[str, Any], bool]:
    """旧邀请仍待确认时，把原始接受按钮保留在推荐首位。

    原按钮已经随邀请公开并提交，后续追问只应更新正文，不能让新推荐覆盖唯一的
    确定性接受入口。新邀请、换幕或撤下旧邀请时不沿用，避免把旧路线带入新状态。
    """  # noqa: DOCSTRING_CJK

    result = dict(performance)
    if not keep_pending:
        return result, False
    origin = pending_transition_record(
        current.session,
        ledger_events=current.ledger_events,
    )
    if not isinstance(origin, Mapping):
        return result, False
    suggestions = origin.get("suggested_inputs")
    if not isinstance(suggestions, list) or not suggestions:
        return result, False
    acceptance = str(suggestions[0] or "").strip()
    if not acceptance:
        return result, False

    current_suggestions = result.get("suggested_inputs")
    kept = [
        str(item).strip()
        for item in current_suggestions
        if str(item or "").strip() and str(item).strip() != acceptance
    ] if isinstance(current_suggestions, list) else []
    preserved = [acceptance, *kept[:2]]
    if current_suggestions == preserved:
        return result, False
    result["suggested_inputs"] = preserved
    return result, True


def _insert_verified_offer_acceptance_suggestion(
    performance: Mapping[str, Any],
    *,
    accept_input: str,
) -> tuple[dict[str, Any], bool]:
    """为已经通过复核的新邀请插入作者写定的确定性接受按钮。"""  # noqa: DOCSTRING_CJK

    result = dict(performance)
    acceptance = str(accept_input or "").strip()
    if not acceptance:
        return result, False
    current_suggestions = result.get("suggested_inputs")
    alternatives = [
        str(item).strip()
        for item in current_suggestions
        if str(item or "").strip()
        and str(item).strip() != acceptance
    ] if isinstance(current_suggestions, list) else []
    suggestions = [acceptance, *alternatives[:2]]
    if current_suggestions == suggestions:
        return result, False
    result["suggested_inputs"] = suggestions
    return result, True


def _evaluation_without_evaluator(current: Any, turn: Any) -> NumericV2EvaluationResult:
    """判定模块关闭时的确定性结果：不结算数值、不猜意图。  # noqa: DOCSTRING_CJK

    只保留一条不依赖模型的放行：玩家提交的正是当前已公开提议的第一条推荐（接受路径）时，
    允许 Runtime 走既有的接受选路。其余情况一律 unclear——剧情停在当前幕，不换幕、不加分。
    """  # noqa: DOCSTRING_CJK

    session = current.session
    message = str(getattr(turn, "message", "") or "").strip()
    accepted = bool(session.transition_offered) and bool(message) and message == _pending_offer_acceptance_path(session)
    return NumericV2EvaluationResult(
        metric_changes=(),
        scene_complete=False,
        transition_intent="accept" if accepted else "unclear",
        interaction_intent="scene_action" if accepted else "mixed_or_unclear",
    )


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
    """按开关控制 Actor 重试；正式接受换场的来源复用命中额外保留一次窄重试。"""  # noqa: DOCSTRING_CJK

    last_error_code = ""
    required_retry_hint = str(kwargs.get("retry_hint") or "").strip()
    # 直接调用方（既有集成与测试）默认保留四次尝试；工作流按模块开关显式传入。
    allow_output_retry = bool(kwargs.pop("allow_output_retry", True))
    allow_transition_repeat_retry = bool(
        kwargs.pop("allow_transition_repeat_retry", False)
    )
    max_attempts = (
        4 if allow_output_retry
        else (2 if allow_transition_repeat_retry else 1)
    )
    for attempt in range(max_attempts):
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
            trace_event("actor.attempt", attempt=attempt + 1, retry_hint=retry_kwargs.get("retry_hint", ""))
            generated = await actor.generate_turn(**retry_kwargs)
            trace_event("actor.candidate", attempt=attempt + 1, performance=generated)
            return generated
        except NumericV2ActorOutputError as exc:
            repetition_guard = str(getattr(exc, "repetition_guard", "") or "").strip()
            if diagnostics is not None and repetition_guard:
                guard_counts = diagnostics.setdefault("actor_repeated_output_guards", {})
                guard_counts[repetition_guard] = int(guard_counts.get(repetition_guard, 0)) + 1
            trace_event(
                "actor.rejected",
                attempt=attempt + 1,
                error_code=str(exc),
                **({"repetition_guard": repetition_guard} if repetition_guard else {}),
            )
            last_error_code = str(exc)
            targeted_transition_repeat_retry = (
                allow_transition_repeat_retry
                and repetition_guard in {"earlier_session", "transition_source"}
                and attempt == 0
            )
            if targeted_transition_repeat_retry:
                # 正式接受换场即使关闭通用正文重试，也保留一次窄范围重试；
                # 只针对来源段复用历史正文的两类重复保护命中。
                trace_event(
                    "actor.retry_enabled_for_transition_repeat",
                    attempt=attempt + 1,
                    reason="accepted_transition_repeated_output",
                    repetition_guard=repetition_guard,
                )
            if repetition_guard and attempt >= 1:
                if diagnostics is not None:
                    diagnostics["actor_repeated_output_retry_aborted"] = int(
                        diagnostics.get("actor_repeated_output_retry_aborted", 0)
                    ) + 1
                trace_event(
                    "actor.retry_aborted",
                    attempt=attempt + 1,
                    reason="repeated_output_budget",
                    repetition_guard=repetition_guard,
                )
                raise
            if attempt == max_attempts - 1 or (
                not allow_output_retry and not targeted_transition_repeat_retry
            ):
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
    """Trace one attempt without changing the workflow, retry policy or public result."""
    diagnostics = diagnostics_sink if diagnostics_sink is not None else {}
    with text_trace_scope("turn", state_before=trace_state(current.session), turn=turn):
        try:
            result = await _execute_numeric_v2_turn(
                config_manager=config_manager, runtime=runtime, current=current, turn=turn,
                ensure_current_binding=ensure_current_binding, before_commit=before_commit,
                diagnostics_sink=diagnostics,
            )
            trace_event("turn.committed", state_after=trace_state(result.stored.session),
                        performance=result.performance, ledger_event=result.stored.ledger_events[-1],
                        stored_performance=result.stored.session.performance_history[-1])
            return result
        finally:
            trace_event("turn.diagnostics", diagnostics=diagnostics)


async def _execute_numeric_v2_turn(
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
        "actor_repeated_output_guards": {},
        "actor_repeated_output_retry_aborted": 0,
        "actor_provider_calls": 0,
        "actor_suggestion_fill_attempts": 0,
        "actor_suggestion_fill_provider_calls": 0,
        "actor_suggestion_refill_after_review_attempts": 0,
        "actor_suggestion_fill_reasons": {},
        "actor_base_suggestion_parse_counts": {},
        "actor_base_fact_candidate_parse_counts": {},
        "transition_judge_calls": 0,
        "transition_judge_degraded": False,
        # 复核时间预算耗尽后跳过的复检次数；仅作诊断，不代表复核通过。
        "review_budget_skips": 0,
        # 普通回合把目标幕开场／桥接时点演成现在时的确定性命中记录（问题2.141 B3）。
        "target_opening_leak_markers": [],
        # 每回合共享一个复查机会，改写稿不能再次触发；失败时保留快速初判。
        "dispute_review_attempts": 0,
        "dispute_review_degraded": False,
        # 正式转场的按钮字段不决定三段正文是否可交付；无正文违规时不为无效按钮再等争议复查。
        "dispute_review_skipped_formal_offer": 0,
        # 高置信的玩家越权/提前换幕结果直接进入改写，不重复等待争议复查。
        "dispute_review_skipped_high_confidence_body": 0,
        # 正文安全但推荐按钮越界时只删除按钮，不重复请求争议复查。
        "dispute_review_skipped_unsafe_offer_buttons": 0,
        # 正文邀请与结构化出口方向明确冲突时，不重复请求同一份合同判断。
        "dispute_review_skipped_contract_offer": 0,
        # 普通首稿只有邀请不合格时，先用既有改写额度修复，再决定是否需要争议复查。
        "dispute_review_deferred_offer_repair": 0,
        # 模型把玩家已授权的当前幕完成动作误报成出口邀请时，按三方逐字证据清除的次数。
        "current_scene_offer_flags_cleared": 0,
        # 争议超时且预算耗尽时拒绝追加 Actor 改写，保持原子回滚。
        "review_timeout_aborted": False,
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
        # 结局内容若留下问号，默认视为需要玩家继续回答的未收束问题。
        "terminal_new_question_markers": [],
        "terminal_structure_rejected": False,
        # 漏判恢复只生成一次正式候选，不重新判分、不写入未提交普通稿。
        "missed_initiation_recoveries": 0,
        "phantom_transition_flags_cleared": 0,
        # 完成合同已满足但 Actor 漏写公开出口时，追加作者提供的确定性邀请次数。
        "completion_fallback_offer_applied": 0,
        # 待确认期间普通追问不得覆盖原始接受按钮；记录实际补回次数便于压测回溯。
        "pending_acceptance_suggestions_preserved": 0,
        # 只在普通复核确认新邀请有效后插入固定接受按钮；不增加模型调用。
        "verified_offer_acceptance_suggestions_inserted": 0,
        "author_fallback_invitation_protected": 0,
        "narration_offer_flags_cleared": 0,
        # 模型理由明确承认动作来自玩家本轮要求时，清除自相矛盾的玩家越权枚举。
        "explicit_player_movement_flags_cleared": 0,
        # 结构化离场结果与 Review 发现的“写回当前幕”冲突次数。
        "player_action_projection_conflicts": 0,
        # 仅删除被 Review 定位为 scene_update 的冲突，不把安全对白交给重复 Actor 改写。
        "player_action_projection_safe_degrades": 0,
        "unsafe_suggestions_removed": 0,
        "deterministic_suggestions_removed": 0,
        "deterministic_suggestion_filter_reasons": {},
        "fact_candidates_accepted": 0,
        "fact_candidates_rejected": 0,
        "review_fact_candidates_proposed": 0,
        "route_suggestion_reviews": 0,
        "evaluator_degraded": False,
        "input_source": turn.input_source,
        "completed": False,
    })
    evaluator = NumericV2MetricEvaluator(config_manager)
    actor = NumericV2Actor(config_manager)
    # 除"回复"之外的每一步模型调用都是可选模块，默认全部关闭（省等待与 token）。
    module_options = await aload_theater_module_options()
    diagnostics["theater_module_options"] = dict(module_options)
    # 兼容既有诊断键：争议复查已并入模块表。
    dispute_review_enabled = bool(module_options.get("dispute"))
    diagnostics["dispute_review_enabled"] = dispute_review_enabled
    # 仅属于本次工作流的原文结果；所有正文重试与复核共享，不写入 Session 或 Ledger。
    history_lookup_result: dict[str, Any] | None = None
    invalidate_previous_offer = False
    final_fixed_review: NumericV2TransitionOfferReview | None = None
    # 在正文重采样前冻结真实人格输入；推荐失败由内部降级，最终正文仍须属于同一角色世代。
    generation_binding = ensure_current_binding(current.session)
    generation_profile = actor._character_profile()

    async def evaluate_turn() -> NumericV2EvaluationResult:
        """执行一次 Evaluator，并把模型故障保守降级为无状态变化。"""  # noqa: DOCSTRING_CJK

        started_at = time.monotonic()
        if not module_options.get("evaluator"):
            # 判定模块关闭：不发模型调用，也不猜数值与意图。
            diagnostics["evaluator_skipped"] = True
            trace_event("evaluator.skipped")
            try:
                return _evaluation_without_evaluator(current, turn)
            finally:
                _add_elapsed_ms(diagnostics, "evaluator_work", started_at)
        diagnostics["evaluator_model_attempts"] += 1
        try:
            result = await evaluator.evaluate(
                engine=runtime.engine,
                session=current.session,
                message=turn.message,
                recent_ledger_events=current.ledger_events,
                player_action_projection=project_player_action_result(turn.message),
            )
            trace_event("evaluator.result", result=result)
            return result
        except NumericV2EvaluatorError as exc:
            # Evaluator 只负责隐藏数值和已有转场态度，不应让一次判定服务抖动阻断玩家的正常演绎。
            # 故障时复用关闭 Evaluator 的确定性退路：不改数值、不猜意图，
            # 但玩家逐字点击当前已公开提议的首个接受按钮时，不能把授权丢掉并永久卡在来源幕。
            diagnostics["evaluator_degraded"] = True
            trace_event("evaluator.degraded", error_code=str(exc))
            logger.warning(
                "Numeric v2 Evaluator degraded to no-op: reason=%s session_id=%s revision=%s",
                str(exc),
                current.session.session_id,
                current.session.revision,
            )
            return _evaluation_without_evaluator(current, turn)
        finally:
            _add_elapsed_ms(diagnostics, "evaluator_work", started_at)

    def prepare_turn(evaluation: NumericV2EvaluationResult) -> TurnOutcomeV2:
        """执行确定性结算并累计同步 Runtime 耗时。"""  # noqa: DOCSTRING_CJK

        started_at = time.monotonic()
        try:
            prepared = runtime.prepare_turn(
                current,
                turn,
                evaluation.metric_changes,
                scene_complete=evaluation.scene_complete,
                transition_intent=evaluation.transition_intent,
                # 同次判定提供结局就绪信号；缺省/降级为 false，不增加一轮确认或模型调用。
                natural_ending_ready=getattr(evaluation, "natural_ending_ready", False),
                # 事实候选已经由 Evaluator 按剧本合同和逐字证据整批核验，Runtime 仍会再次校验。
                fact_operations=getattr(evaluation, "fact_operations", ()),
            )
            trace_event("runtime.prepared", evaluation=evaluation, state=trace_state(prepared.session),
                        route=prepared.route, ledger_event=prepared.ledger_event)
            return prepared
        finally:
            _add_elapsed_ms(diagnostics, "runtime_prepare_work", started_at)

    async def generate_actor_turn(
        outcome: TurnOutcomeV2,
        *,
        retry_hint: str = "",
    ) -> dict[str, Any]:
        """按正式路径生成 Actor 正文；节奏只由同一次调用中的软提示引导。"""  # noqa: DOCSTRING_CJK

        nonlocal final_fixed_review
        final_fixed_review = None
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
                "allow_suggestion_fill": bool(module_options.get("suggestion_fill")),
            }
            if history_lookup_result is not None:
                generation_kwargs["history_lookup"] = history_lookup_result
            generated = await _generate_actor_turn_with_output_retry(
                actor,
                **generation_kwargs,
                retry_hint=retry_hint,
                allow_output_retry=bool(module_options.get("actor_retry")),
                allow_transition_repeat_retry=(
                    str(outcome.ledger_event.get("transition_intent") or "") == "accept"
                    and outcome.ledger_event.get("from_node_id")
                    != outcome.ledger_event.get("to_node_id")
                ),
            )
            return generated
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
            diagnostics["actor_base_fact_candidate_parse_counts"] = dict(
                getattr(actor, "base_fact_candidate_parse_counts", {})
            )
            _add_elapsed_ms(diagnostics, "actor_work", started_at)

    async def refill_suggestions_after_review_filter(
        candidate: Mapping[str, Any],
        *,
        removed_suggestions: int,
    ) -> dict[str, Any]:
        """复核删掉推荐后，沿用同一 Actor 边界补齐按钮，不重写已经通过的正文。"""  # noqa: DOCSTRING_CJK

        suggestions = candidate.get("suggested_inputs")
        if (
            not module_options.get("suggestion_fill")
            or removed_suggestions <= 0
            or not isinstance(suggestions, list)
            or len(suggestions) in {2, 3}
        ):
            return dict(candidate)
        diagnostics["actor_suggestion_refill_after_review_attempts"] += 1
        started_at = time.monotonic()
        try:
            refilled = await actor.refill_suggestions_after_review(
                engine=runtime.engine,
                session=current.session,
                outcome=outcome,
                performance=candidate,
                player_input=turn.message,
                allow_fill=True,
            )
            # 补推荐失败时保留复核后仍安全的原列表，不能因一次附加调用失败把已有按钮清空。
            if len(refilled.get("suggested_inputs") or []) < len(suggestions):
                return dict(candidate)
            trace_event(
                "suggestions.refilled_after_review",
                before=len(suggestions),
                after=len(refilled.get("suggested_inputs") or []),
            )
            return refilled
        finally:
            diagnostics["actor_provider_calls"] = int(getattr(actor, "provider_call_count", 0))
            diagnostics["actor_suggestion_fill_attempts"] = int(
                getattr(actor, "suggestion_fill_attempt_count", 0)
            )
            diagnostics["actor_suggestion_fill_provider_calls"] = int(
                getattr(actor, "suggestion_fill_provider_call_count", 0)
            )
            diagnostics["actor_suggestion_fill_reasons"] = dict(
                getattr(actor, "suggestion_fill_reason_counts", {})
            )
            _add_elapsed_ms(diagnostics, "actor_work", started_at)

    def apply_deterministic_suggestion_filter(candidate: Mapping[str, Any]) -> dict[str, Any]:
        """应用零调用推荐预筛，并把命中原因写入本轮诊断。"""  # noqa: DOCSTRING_CJK

        filtered, removed, reasons = _prefilter_suggestion_candidates(candidate)
        if removed:
            diagnostics["deterministic_suggestions_removed"] += removed
            reason_counts = diagnostics["deterministic_suggestion_filter_reasons"]
            for reason in reasons:
                reason_counts[reason] = int(reason_counts.get(reason, 0)) + 1
            # 当前 Actor 返回的是可变字典；原地更新可让所有后续复核路径看到同一份候选。
            if isinstance(candidate, dict):
                candidate.clear()
                candidate.update(filtered)
                return candidate
        return filtered

    review_call_count = 0
    last_review: NumericV2TransitionOfferReview | None = None

    def review_budget_exhausted() -> bool:
        """本轮已用复核时间是否达到上限；只读诊断累计值，不额外调用模型。"""  # noqa: DOCSTRING_CJK

        return (
            float(diagnostics["timings_ms"].get("transition_judge_work", 0.0))
            >= NUMERIC_V2_REVIEW_BUDGET_SECONDS * 1000.0
        )

    def review_budget_effectively_exhausted() -> bool:
        """Treat the small timeout cushion as spent so a timed-out dispute cannot trigger another Actor call."""

        used_ms = float(diagnostics["timings_ms"].get("transition_judge_work", 0.0))
        return used_ms >= max(0.0, NUMERIC_V2_REVIEW_BUDGET_SECONDS - 0.5) * 1000.0

    async def review_transition_offer(
        candidate: Mapping[str, Any],
        *,
        defer_offer_only_dispute: bool = False,
    ) -> NumericV2TransitionOfferReview:
        """复核可见提议并累计调用成本；模型故障沿用原有保守撤销语义。"""  # noqa: DOCSTRING_CJK

        nonlocal final_fixed_review, review_call_count, last_review
        final_fixed_review = None
        # 先做零调用事实预筛，再进入模型复核；这样明确的未来结果不会占用复核等待。
        candidate = apply_deterministic_suggestion_filter(candidate)
        if review_call_count and review_budget_exhausted():
            # 预算耗尽后不再追加复检。正式转场沿用"未完成复核不提交"的回滚；
            # 普通回合沿用最近一次判定，由既有改写/末稿兜底路径收尾。
            diagnostics["review_budget_skips"] += 1
            if outcome.ledger_event["from_node_id"] != outcome.ledger_event["to_node_id"]:
                trace_event("review.budget_exhausted", phase="transition")
                raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
            if last_review is not None:
                trace_event("review.budget_exhausted", phase="ordinary")
                return last_review
        review_call_count += 1
        transition_judge_started_at = time.monotonic()
        diagnostics["transition_judge_calls"] += 1
        try:
            prior_review_seconds = float(
                diagnostics["timings_ms"].get("transition_judge_work", 0.0)
            ) / 1000.0

            def remaining_review_seconds() -> float:
                """Return the budget left, including the current in-flight review call."""

                return NUMERIC_V2_REVIEW_BUDGET_SECONDS - prior_review_seconds - (
                    time.monotonic() - transition_judge_started_at
                )

            # 转场旁白也由 Actor 生成后，要从来源历史复核整段；普通回合沿用原证据与判断。
            changed = outcome.ledger_event["from_node_id"] != outcome.ledger_event["to_node_id"]
            review_kwargs = dict(
                engine=runtime.engine,
                # 普通稿仍审已提交历史；候选已递增的回合号会丢掉首轮开场并误报历史缺失。
                # 只还原历史水位，保留本轮数值、称呼与邀请状态供出口和边界复核。
                session=current.session if changed else replace(
                    outcome.session, revision=current.session.revision,
                    node_turn_count=current.session.node_turn_count,
                ),
                message=turn.message,
                actor_performance=candidate,
                scene_complete=evaluation.scene_complete,
                route_changed=(
                    outcome.ledger_event["from_node_id"]
                    != outcome.ledger_event["to_node_id"]
                ),
                **({"transition_outcome": outcome} if changed else {}),
                player_action_projection=outcome.ledger_event.get("player_action_projection"),
            )
            if history_lookup_result is not None:
                review_kwargs["history_lookup"] = history_lookup_result
            if not changed and diagnostics["transition_cancellations"]:
                review_kwargs["cancelled_transition"] = True
                review_kwargs["invalidated_invitation"] = invalidate_previous_offer
            # 快检、争议复查及正文重写后都沿用同一份已核对原文；不重新从作者方向猜公开事实。
            if changed and outcome.ledger_event.get("transition_intent") == "initiate":
                review_kwargs["public_destination_quote"] = evaluation.public_destination_quote
            # 仅补查未识别的普通主动请求；拒绝、已有待确认邀请、开场和正式转场不走此入口。
            if (not changed and evaluation.transition_intent == "unclear"
                    and not current.session.transition_offered
                    and not diagnostics["missed_initiation_recoveries"]
                    and not diagnostics["transition_cancellations"]):
                review_kwargs["check_missed_initiation"] = True
            # 只有首次快检使用完整材料；改写后的复检属于对已指控问题的二次判定，按 L2/L4 收窄输入。
            if review_call_count > 1:
                review_kwargs["recheck_only"] = True
            first_call_budget = remaining_review_seconds()
            # 首次快检始终执行，即使测试把总预算设为0；只有后续调用才允许被预算跳过。
            if review_call_count > 1 and first_call_budget <= 0.05:
                diagnostics["review_budget_skips"] += 1
                if changed:
                    trace_event("review.budget_exhausted", phase="transition")
                    raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
                if last_review is not None:
                    trace_event("review.budget_exhausted", phase="ordinary")
                    return last_review
            if review_call_count > 1 or prior_review_seconds > 0.0:
                review_kwargs["timeout_seconds"] = max(0.05, first_call_budget)
            review = await evaluator.validate_transition_offer(**review_kwargs)
            if (
                changed
                and outcome.ledger_event.get("transition_intent") == "accept"
                and review.pending_invitation_invalid is True
            ):
                transition_contract = outcome.transition_contract or {}
                author_fallback = (
                    str(transition_contract.get("fallback_offer") or "").strip()
                    if isinstance(transition_contract, Mapping)
                    else ""
                )
                pending_text = pending_transition_performance(
                    current.session,
                    ledger_events=current.ledger_events,
                    include_withdrawn=True,
                )
                if author_fallback and author_fallback in pending_text:
                    # 作者逐字兜底与当前实际路线一致时，模型只能否定本轮接受，不能撤下邀请本身。
                    review = replace(review, pending_invitation_invalid=False)
                    diagnostics["author_fallback_invitation_protected"] += 1
                    trace_event(
                        "review.author_fallback_invitation_protected",
                        route_id=(outcome.route or {}).get("id"),
                    )

            def record_review(result: NumericV2TransitionOfferReview, mode: str) -> None:
                trace_event("review.result", mode=mode, phase="transition" if changed else "ordinary", result=result)
                # 两次判断分别留作诊断，不混入剧情历史，也不把初判理由喂给独立复查。
                diagnostics["transition_review_results"].append({
                    "review_mode": mode,
                    "offer_present": result.offer_present,
                    "offer_quote": result.offer_quote,
                    "valid": result.valid,
                    "player_action_preserved": result.player_action_preserved,
                    "scene_boundary_preserved": result.scene_boundary_preserved,
                    "author_boundaries_preserved": result.author_boundaries_preserved,
                    "unsafe_suggestion_indexes": list(result.unsafe_suggestion_indexes),
                    "body_violations": list(result.body_violations),
                    "failure_reason": result.failure_reason,
                    "missed_initiation": result.missed_initiation,
                    "initiation_authorized": result.initiation_authorized,
                    "acceptance_authorized": result.acceptance_authorized,
                    "pending_invitation_invalid": result.pending_invitation_invalid,
                    **({"fixed_narration_triggers": list(result.fixed_narration_triggers)}
                       if result.fixed_narration_triggers else {}),
                    **({"fact_candidates": list(result.fact_candidates)}
                       if result.fact_candidates else {}),
                })

            record_review(review, "fast")
            if not changed and _review_denies_narration_only_offer(candidate, review):
                # 旁白只展示出口标识不等于角色邀请玩家换幕。复核理由已明确否认
                # 邀请时，只清除自相矛盾的布尔标志，保留旁白和同轮事实候选。
                diagnostics["narration_offer_flags_cleared"] += 1
                trace_event(
                    "review.narration_offer_cleared",
                    offer_quote=review.offer_quote,
                    failure_reason=review.failure_reason,
                )
                review = replace(
                    review,
                    offer_present=False,
                    valid=False,
                    offer_quote="",
                    failure_reason="",
                )
            current_scene_evidence = ()
            if (
                not changed
                and evaluation.transition_intent == "unclear"
                and review.offer_present
                and not review.valid
                and not review.body_violations
            ):
                current_scene_evidence = _current_scene_completion_offer_evidence(
                    engine=runtime.engine,
                    session=current.session,
                    player_input=turn.message,
                    review=review,
                )
            if current_scene_evidence:
                # 三份独立原文都指向同一个完成事实时，这是玩家已经授权的幕内动作，
                # 不是等待玩家再次决定的节点出口；保留正文与事实候选，只清除邀请判定。
                diagnostics["current_scene_offer_flags_cleared"] += 1
                trace_event(
                    "review.current_scene_offer_cleared",
                    evidence=list(current_scene_evidence),
                    fact_keys=[
                        str(candidate.get("key") or "")
                        for candidate in review.fact_candidates
                        if isinstance(candidate, Mapping)
                    ],
                )
                review = replace(
                    review,
                    offer_present=False,
                    valid=False,
                    offer_quote="",
                    failure_reason="",
                )
            player_action_projection = normalize_player_action_projection(
                outcome.ledger_event.get("player_action_projection")
            )
            if not changed and _review_mislabels_explicit_player_movement(
                review,
                player_action_projection,
            ):
                # 玩家明确要求移动只授权该次移动；此处不推断目的地、不创建换幕，也不放行额外操作。
                diagnostics["explicit_player_movement_flags_cleared"] += 1
                trace_event(
                    "review.explicit_player_movement_cleared",
                    failure_reason=review.failure_reason,
                )
                review = replace(
                    review,
                    body_violations=(),
                    failure_reason="",
                )
            failure_reason = str(review.failure_reason or "")
            projection_conflict = _player_action_projection_conflicts_with_review(
                review,
                player_action_projection,
            )
            if projection_conflict:
                diagnostics["player_action_projection_conflicts"] += 1
                trace_event(
                    "review.player_action_projection_conflict",
                    failure_reason=review.failure_reason,
                )
            high_confidence_body_violation = (
                (
                    not review.offer_present
                    and (
                        (
                            not changed
                            and tuple(review.body_violations) == ("scene_boundary",)
                        )
                        or (
                            not changed
                            and tuple(review.body_violations) == ("player_action",)
                            and (
                                projection_conflict
                                or
                                not failure_reason.strip()
                                or any(
                                    marker in failure_reason
                                    for marker in (
                                        "替玩家", "未获授权", "未授权行动", "确认未知", "未承接", "遗漏",
                                    )
                                )
                            )
                        )
                        or (
                            not changed
                            and tuple(review.body_violations) == ("author_boundary",)
                            and any(
                                marker in failure_reason
                                for marker in (
                                    "hard_boundaries", "authoritative_state", "作者禁令", "禁令", "不得另找",
                                )
                            )
                        )
                    )
                )
                or (
                    # 正式转场或已公开邀请也可能由复核明确指出硬边界／权威状态冲突；
                    # 这类主体证据足够驱动一次 Actor 修复，不再为同一问题发争议复查。
                    tuple(review.body_violations) == ("player_action",)
                    and any(
                        marker in failure_reason
                        for marker in ("hard_boundaries", "authoritative_state", "作者禁令", "禁令")
                    )
                )
            )
            if high_confidence_body_violation:
                # 这类快检结果已经有明确的主体/授权证据；争议复查只会重复发送同一证据，
                # 压测显示它经常白等到超时，再叠加一次演员重写。保留快检拒绝并进入一次修复。
                diagnostics["dispute_review_skipped_high_confidence_body"] += 1
                trace_event("review.dispute_skipped", reason="high_confidence_body_violation")
            elif changed and review.offer_present and not review.valid and not review.body_violations:
                # 正式换场的三段正文已经由 Runtime 选路并由正文复核；按钮无效只需丢弃推荐，
                # 不应再触发一次高成本争议复查。
                diagnostics["dispute_review_skipped_formal_offer"] += 1
                trace_event("review.dispute_skipped", reason="formal_offer_not_delivery_gate")
            elif (
                not review.body_violations
                and review.offer_present
                and not review.valid
                and review.unsafe_suggestion_indexes
            ):
                # 正文没有违规，复核只指出按钮越界；按钮会按索引删除，第二次争议无法改变正文结论。
                diagnostics["dispute_review_skipped_unsafe_offer_buttons"] += 1
                trace_event("review.dispute_skipped", reason="unsafe_offer_buttons_only")
            elif (
                not review.body_violations
                and review.offer_present
                and not review.valid
                and "next_scene_direction" in failure_reason
                and any(marker in failure_reason for marker in ("不符", "不符合", "不一致"))
            ):
                # 出口合同已经给出确定方向；再次询问模型不会改变结构化去向，只会增加等待。
                diagnostics["dispute_review_skipped_contract_offer"] += 1
                trace_event("review.dispute_skipped", reason="contract_offer_mismatch")
            elif (
                defer_offer_only_dispute
                and not changed
                and not current.session.transition_offered
                and not review.body_violations
                and review.offer_present
                and not review.valid
            ):
                # 普通首稿只有邀请不合格时，既有唯一一次 Actor 改写就是直接修复手段。
                # 先修后验；仅当修复稿仍被拒绝时，才让争议复查仲裁，避免修好后继续空等。
                diagnostics["dispute_review_deferred_offer_repair"] += 1
                trace_event("review.dispute_deferred", reason="ordinary_offer_repair_first")
            elif dispute_review_enabled and not diagnostics["dispute_review_attempts"] and not review_budget_exhausted() and (
                review.body_violations or (review.offer_present and not review.valid and not changed)
            ):
                dispute_budget = remaining_review_seconds()
                if dispute_budget <= 0.05:
                    diagnostics["review_budget_skips"] += 1
                    trace_event("review.budget_exhausted", phase="dispute")
                else:
                    diagnostics["dispute_review_attempts"] += 1
                    diagnostics["transition_judge_calls"] += 1
                    try:
                        # 争议复查必须与快检使用完全相同的请求与证据（既有不变量），
                        # 因此这里不加 recheck_only；定向收窄只用于改写后的复检。
                        dispute_kwargs = dict(review_kwargs)
                        dispute_kwargs["dispute_review"] = True
                        dispute_kwargs["timeout_seconds"] = min(
                            NUMERIC_V2_DISPUTE_TIMEOUT_CAP_SECONDS,
                            max(0.05, dispute_budget - 0.05),
                        )
                        reviewed = await evaluator.validate_transition_offer(**dispute_kwargs)
                    except NumericV2EvaluatorError as exc:
                        trace_event("review.failed", mode="dispute", error_code=str(exc))
                        # 此处必须与快速复核故障分开：已有违规证据不可被普通回合的降级路径清空。
                        diagnostics["dispute_review_degraded"] = True
                        diagnostics["transition_review_results"].append({
                            "review_mode": "dispute", "degraded": True, "failure_reason": str(exc),
                        })
                    else:
                        record_review(reviewed, "dispute")
                        review = reviewed
            if not changed:
                # 普通回合不得把目标幕开场或桥接独有的时间标记演成现在时（问题2.141 B3）。
                # 该检查是确定性的，放在模型判定与争议之后：模型判断不能清除它。
                leaked = tuple(dict.fromkeys((
                    *premature_target_markers(
                        runtime.engine, current.session, outcome, candidate, player_input=turn.message),
                    *premature_target_scene_facts(
                        runtime.engine, current.session, outcome, candidate, player_input=turn.message),
                )))
                if leaked:
                    markers = "、".join(leaked)
                    diagnostics["target_opening_leak_markers"] = sorted({
                        *diagnostics.get("target_opening_leak_markers", []), *leaked})
                    note = (
                        f"来源回合的可见旁白出现了只属于目标幕开场或桥接的事实/时间标记：{markers}。"
                        "该事实尚未在当前幕发生；本回合只可提出邀请，不得把它叙述为现在时。"
                    )
                    trace_event("review.target_opening_leak", markers=list(leaked))
                    review = replace(
                        review,
                        body_violations=tuple(dict.fromkeys((*review.body_violations, "target_opening_leak"))),
                        failure_reason=" ".join(part for part in (review.failure_reason.strip(), note) if part),
                    )
            final_fixed_review = review
            last_review = review
            return review
        except NumericV2EvaluatorError as exc:
            trace_event("review.failed", mode="fast", error_code=str(exc))
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
            last_review = NumericV2TransitionOfferReview(
                offer_present=False,
                valid=False,
                body_violations=(),
                unsafe_suggestion_indexes=(),
            )
            return last_review
        finally:
            _add_elapsed_ms(
                diagnostics,
                "transition_judge_work",
                transition_judge_started_at,
            )

    evaluation = await evaluate_turn()
    if evaluation.history_query and module_options.get("history_lookup"):
        # 普通回合不额外调用；有证据缺口才查一次完整记录，失败结果也共享，防止改稿反复查找。
        lookup_started_at = time.monotonic()
        history_lookup_result = await lookup_history(config_manager, current.session, evaluation.history_query)
        trace_event("history.result", query=evaluation.history_query, result=history_lookup_result)
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
    performance = apply_deterministic_suggestion_filter(performance)
    route_changed = (
        outcome.ledger_event["from_node_id"]
        != outcome.ledger_event["to_node_id"]
    )
    reviewed_transition_offered = False
    if route_changed:
        # 目标幕开场是下一段要交付的内容；桥段不能把其中的完整事实再提前演一次。
        # 这里只做确定性的逐句/时点溯源检查，语义改写仍交给现有 Actor，改写后仍冲突则回滚。
        target_node = runtime.engine.nodes[str(outcome.ledger_event["to_node_id"])]
        transition_contract = outcome.transition_contract or {}
        candidate_segments = performance.get("segments")
        bridge_text = ""
        if isinstance(candidate_segments, list):
            bridge_text = "\n".join(
                str(segment.get("scene_narration") or "")
                for segment in candidate_segments
                if isinstance(segment, Mapping) and segment.get("phase") == "transition_bridge"
            )
        leak_markers = transition_bridge_leak_markers(
            target_opening=scene_opening_text(target_node.get("story_beat") or {}),
            bridge_text=bridge_text,
            authored_bridge=str(transition_contract.get("bridge_scene_narration") or ""),
        )
        if leak_markers:
            diagnostics["transition_bridge_leak_markers"] = list(leak_markers)
            trace_event("transition.bridge_target_leak", markers=list(leak_markers))
            diagnostics["semantic_rewrite_attempts"] += 1
            performance = await generate_actor_turn(outcome, retry_hint=(
                "过渡桥段提前包含了目标幕开场独有内容：" + "、".join(leak_markers)
                + "。只保留来源回应和作者允许的过渡时空；目标幕开场会在下一段单独交付，"
                "不得在 transition_bridge 中重复写出目标幕的到达、时点或独有事实。"))
            candidate_segments = performance.get("segments")
            bridge_text = "\n".join(
                str(segment.get("scene_narration") or "")
                for segment in candidate_segments
                if isinstance(candidate_segments, list)
                and isinstance(segment, Mapping)
                and segment.get("phase") == "transition_bridge"
            ) if isinstance(candidate_segments, list) else ""
            remaining_leaks = transition_bridge_leak_markers(
                target_opening=scene_opening_text(target_node.get("story_beat") or {}),
                bridge_text=bridge_text,
                authored_bridge=str(transition_contract.get("bridge_scene_narration") or ""),
            )
            diagnostics["transition_bridge_leak_markers_after_rewrite"] = list(remaining_leaks)
            if remaining_leaks:
                diagnostics["transition_structure_rejected"] = True
                trace_event("transition.bridge_target_leak_unresolved", markers=list(remaining_leaks))
                raise NumericV2ActorOutputError("numeric_v2_transition_segment_overlap")
        terminal_question_markers = _terminal_new_question_markers(
            engine=runtime.engine,
            outcome=outcome,
            performance=performance,
        )
        if terminal_question_markers:
            diagnostics["terminal_new_question_markers"] = list(terminal_question_markers)
            trace_event("transition.terminal_new_question", markers=list(terminal_question_markers))
            if not diagnostics["semantic_rewrite_attempts"]:
                diagnostics["semantic_rewrite_attempts"] += 1
                performance = await generate_actor_turn(
                    outcome,
                    retry_hint=(
                        "这是结局交付，不能留下需要玩家回答的新问题。"
                        "请把目标幕中的疑问句改成角色已经完成的回应、动作或确定性收束，"
                        "不得追加新邀约、选择或等待玩家输入。"
                    ),
                )
                terminal_question_markers = _terminal_new_question_markers(
                    engine=runtime.engine,
                    outcome=outcome,
                    performance=performance,
                )
                diagnostics["terminal_new_question_markers"] = list(terminal_question_markers)
            if terminal_question_markers:
                diagnostics["terminal_structure_rejected"] = True
                trace_event(
                    "transition.terminal_new_question_unresolved",
                    markers=list(terminal_question_markers),
                )
                raise NumericV2ActorOutputError("numeric_v2_terminal_new_question")
    if route_changed and (module_options.get("review_delivery") or module_options.get("review_contract")):
        # 换场前的合同核对：显式交付校验是纯程序（零调用），边界校验是一次窄判定（仅换场时）。
        # 两者共用同一次改稿额度；任一失败都不阻断提交，只留诊断。
        source_node = runtime.engine.nodes[str(outcome.ledger_event["from_node_id"])]
        target_node_id = str(outcome.ledger_event["to_node_id"])
        problems: list[str] = []
        if module_options.get("review_delivery"):
            missing_names = missing_contract_names(source_node, target_node_id, performance, current.session)
            if missing_names:
                diagnostics["contract_missing"] = list(missing_names)
                trace_event("contract.missing", names=list(missing_names))
                problems.append("合同要求本轮交付但演绎里没有出现的关键道具：" + "、".join(missing_names))
        if module_options.get("review_contract") and not module_options.get("review"):
            # 换场交付里目标幕开场是作者写给下一幕的正文，天然带着目标幕的事实，
            # 用它去核对来源幕的禁令会把正常换场判成越界；只把来源回应与桥段送去核对。
            boundary_view = _source_side_delivery(performance)
            try:
                violated = await evaluator.verify_contract_boundaries(
                    node=source_node, actor_performance=boundary_view, player_input=turn.message)
            except NumericV2EvaluatorError as exc:
                diagnostics["contract_check_degraded"] = True
                trace_event("contract.check_degraded", error_code=str(exc))
                violated = ()
            if violated:
                diagnostics["contract_violated"] = list(violated)
                trace_event("contract.violated", names=list(violated))
                problems.append("作者禁令被本轮演绎违反：" + "、".join(violated))
        if problems and not diagnostics["semantic_rewrite_attempts"]:
            diagnostics["semantic_rewrite_attempts"] += 1
            performance = await generate_actor_turn(outcome, retry_hint=(
                "本轮换场前的合同核对发现问题：" + "；".join(problems)
                + "。请按实际历史与作者边界改写：删除尚未发生或越界的内容，缺的道具自然写出，"
                "其余已获准内容保持不变。"))
            if module_options.get("review_delivery"):
                still_missing = missing_contract_names(source_node, target_node_id, performance, current.session)
                diagnostics["contract_missing_after_rewrite"] = list(still_missing)
                if still_missing:
                    diagnostics["contract_missing_fallback"] = True
            if module_options.get("review_contract") and not module_options.get("review"):
                try:
                    still_violated = await evaluator.verify_contract_boundaries(
                        node=source_node, actor_performance=_source_side_delivery(performance),
                        player_input=turn.message)
                except NumericV2EvaluatorError:
                    still_violated = ()
                diagnostics["contract_violated_after_rewrite"] = list(still_violated)
    if not module_options.get("review"):
        # 复核模块关闭：不调用复核模型；但场景事实边界是零调用的确定性保护，仍允许一次演员修复。
        diagnostics["review_skipped"] = True
        trace_event("review.skipped", phase="transition" if route_changed else "ordinary")
        if not route_changed:
            leaked = tuple(dict.fromkeys((
                *premature_target_markers(
                    runtime.engine, current.session, outcome, performance, player_input=turn.message),
                *premature_target_scene_facts(
                    runtime.engine, current.session, outcome, performance, player_input=turn.message),
            )))
            if leaked:
                diagnostics["target_opening_leak_markers"] = sorted({
                    *diagnostics.get("target_opening_leak_markers", []), *leaked})
                trace_event("review.target_opening_leak", markers=list(leaked), mode="deterministic")
                if not diagnostics["semantic_rewrite_attempts"]:
                    diagnostics["semantic_rewrite_attempts"] += 1
                    performance = await generate_actor_turn(outcome, retry_hint=(
                        "本轮可见旁白提前写出了目标幕独有事实：" + "、".join(leaked)
                        + "。目标幕尚未进入；请只保留当前幕可证实内容，邀请可以提出但不能把目标事实写成现在时。"))
        if module_options.get("review_contract") and not route_changed:
            # 留幕回合也要核对作者禁令：提前到达一类越界必须在当轮拦下，
            # 等到下一次换场再拦时，越界内容已经提交给玩家了。与换场共用同一次改稿额度。
            stay_node = runtime.engine.nodes[str(outcome.ledger_event["from_node_id"])]
            try:
                violated = await evaluator.verify_contract_boundaries(
                    node=stay_node, actor_performance=performance, player_input=turn.message)
            except NumericV2EvaluatorError as exc:
                diagnostics["contract_check_degraded"] = True
                trace_event("contract.check_degraded", error_code=str(exc))
                violated = ()
            if violated:
                diagnostics["contract_violated"] = list(violated)
                trace_event("contract.violated", names=list(violated))
                if not diagnostics["semantic_rewrite_attempts"]:
                    diagnostics["semantic_rewrite_attempts"] += 1
                    performance = await generate_actor_turn(outcome, retry_hint=(
                        "本轮演绎违反了作者禁令：" + "、".join(violated)
                        + "。请按实际历史与作者边界改写：删除尚未发生或越界的内容，"
                        "其余已获准内容保持不变。"))
                    try:
                        still_violated = await evaluator.verify_contract_boundaries(
                            node=stay_node, actor_performance=performance, player_input=turn.message)
                    except NumericV2EvaluatorError:
                        still_violated = ()
                    diagnostics["contract_violated_after_rewrite"] = list(still_violated)
        reviewed_transition_offered = performance.get("transition_offered") is True
    elif (
        not route_changed
        and (
            performance.get("transition_offered") is True
            or str(performance.get("performance") or "").strip()
            or str(performance.get("scene_narration") or "").strip()
        )
    ):
        # 旧邀请锁存不证明本轮正文安全；留幕追问、澄清同样复核，邀请状态仍由 Runtime 保留。
        # 正文违规和无效邀请共用一次改写；首次争议复查后仍违规才重写，不按错误类别叠加。
        for rewrite_attempt in range(2):
            transition_review = await review_transition_offer(
                performance,
                defer_offer_only_dispute=rewrite_attempt == 0,
            )
            if diagnostics["dispute_review_degraded"] and review_budget_effectively_exhausted():
                # 争议复查已耗尽本轮预算时，不再追加同样昂贵的 Actor 改写；让玩家重试，
                # 避免把未经完整复核的修复稿写入历史。
                diagnostics["review_timeout_aborted"] = True
                trace_event("review.rewrite_aborted", phase="ordinary", reason="review_budget_exhausted")
                raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
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
                    trace_event("transition.recovered", evaluation=recovered_evaluation)
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
            safe_degraded_performance = _safe_degrade_conflicting_scene_update(
                performance,
                transition_review,
                outcome.ledger_event.get("player_action_projection"),
            )
            if safe_degraded_performance is not None:
                performance = safe_degraded_performance
                transition_review = replace(
                    transition_review,
                    body_violations=(),
                    failure_reason="",
                    fact_candidates=(),
                )
                final_fixed_review = transition_review
                diagnostics["player_action_projection_safe_degrades"] += 1
                trace_event(
                    "review.player_action_projection_safe_degrade",
                    removed_fields=["scene_narration", "fact_candidates"],
                )
            if not transition_review.body_violations:
                performance = await refill_suggestions_after_review_filter(
                    performance, removed_suggestions=removed_suggestions,
                )
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
                # 兜底只允许提交末稿正文，不能把已被复核判无效的新去向锁存成待确认邀请。
                # 先前已经公开且仍合法的邀请由 Runtime 独立保留，不依赖本轮末稿重新获准。
                reviewed_transition_offered = transition_review.offer_present and transition_review.valid
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
            # 普通回合从同一输入和真实历史重新回应，避免沿用被拒稿的错误事实；原因仍供核对。
            performance = await generate_actor_turn(
                outcome,
                retry_hint=(
                    "这是唯一一次正文与提议修复，上一稿未提交；从本轮原始上下文重新回应，不是继续扩写剧情。"
                    "以玩家实际输入、已提交历史和作者硬边界为准，保留获准回应，"
                    "不补出未表达的后续操作及正文、场景更新、推荐中依赖它的结果。"
                    "scene_update 只记录本轮新的可见变化；没有新变化就省略。"
                    "猫娘可用自身反应、回答或明确未知承接玩家，不要求本轮推进剧情或产生外部结果。"
                    "不得为了交付结果、收束或兑现旧推荐补造操作、事实或下一阶段，也不能撤销玩家已做的合法动作。"
                    "只有正文已公开具体、合乎当前事实且与实际下一阶段一致的未来邀请时才设 transition_offered=true；"
                    "邀请停在执行前，按钮不能代替正文首次提出转场。没有合适出口就不提议，不追加前提清单。"
                    f"{_transition_review_failure_context(transition_review)}"
                    f"{boundary_context}"
                ),
            )
    if route_changed and not module_options.get("review"):
        # 复核模块关闭：三段落直接采用演员输出，不做违规判定、取消或改写。
        diagnostics["review_skipped"] = True
        trace_event("review.skipped", phase="transition")
        reviewed_transition_offered = performance.get("transition_offered") is True
    elif route_changed:
        # 三段与首轮按钮合并一次复核；仅明确正文冲突可改写一次，不能用静态旁白覆盖或强制结束。
        diagnostics["route_suggestion_reviews"] += int(bool(performance.get("suggested_inputs")))
        for rewrite_attempt in range(2):
            review = await review_transition_offer(performance)
            if diagnostics["dispute_review_degraded"] and review_budget_effectively_exhausted():
                # 正式转场在争议复查超时后直接回滚，不能用未经完整复核的改写稿提交三段记录。
                diagnostics["review_timeout_aborted"] = True
                trace_event("review.rewrite_aborted", phase="transition", reason="review_budget_exhausted")
                raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
            performance, removed = _drop_reported_unsafe_suggestions(performance, review.unsafe_suggestion_indexes)
            diagnostics["unsafe_suggestions_removed"] += removed
            if not review.body_violations:
                performance = await refill_suggestions_after_review_filter(
                    performance, removed_suggestions=removed,
                )
            if not review.body_violations:
                break
            if (
                review.initiation_authorized is False
                or review.acceptance_authorized is False
            ) and rewrite_attempt == 0 and not diagnostics["transition_cancellations"]:
                # 去向授权失败是提交安全闸门，不能被先前的桥段或交付合同改写额度吞掉。
                # Actor 语义改写后的复检不重新推翻首轮授权；取消后仍只生成一次留幕稿。
                # 从原始快照及同一次计分重新prepare，不能从已换幕候选倒扣或再次累计分数。
                diagnostics["transition_cancellations"] += 1
                trace_event("transition.cancelled", review=review)
                diagnostics["semantic_rewrite_attempts"] += 1
                # 只撤下已被明确判错的邀请；仅询问/犹豫导致的未获准移动仍保留合法原邀请。
                invalidate_previous_offer = review.pending_invitation_invalid is True
                evaluation = replace(evaluation, transition_intent="unclear",
                                     natural_ending_ready=False, public_destination_quote="")
                outcome = prepare_turn(evaluation)
                if invalidate_previous_offer:
                    # 撤下结论在改稿前共享，不能继续把已否定的原话标为“当前待确认”。
                    # 这里只改未提交候选，技术失败仍回滚到 current。
                    outcome, _ = runtime.engine.finalize_transition_offer_state(
                        outcome, {}, new_offer=False, invalidate_previous_offer=True)
                route_changed = False
                effective_interaction_intent = "scene_action"
                diagnostics["effective_interaction_intent"] = effective_interaction_intent
                # 三段及其去向比较理由均不作留幕底稿，避免把另一跨幕去向误作执行指令。
                # 复核原理由仍留在诊断中；演员从原始玩家输入、正式历史及当前出口重新回应。
                performance = await generate_actor_turn(outcome, retry_hint=(
                    "此前候选换幕因公开去向与玩家授权不符已取消，三段均未播放。"
                    # 复核可能指出旧邀请本身有误；不能因此从留幕稿改演另一个跨幕去向。
                    "本轮留在当前幕，已获准的幕内行动照常回应；留幕不代表改去另一个跨幕地点。"
                    "若旧邀请与 next_scene 不符，承认自己先前邀约有误，说明当前可行安排并保留玩家重新选择，"
                    "不要执行旧错误邀请，也不要把 next_scene 的不同安排说成玩家已经同意。"
                    "不要要求玩家重复输入，不执行已取消的下一幕安排，不新增额外任务。"
                ))
                # 已用完共享改稿额度，只核对这一份留幕稿；禁用同轮主动请求补查，避免反复换幕。
                review = await review_transition_offer(performance)
                performance, removed = _drop_reported_unsafe_suggestions(performance, review.unsafe_suggestion_indexes)
                diagnostics["unsafe_suggestions_removed"] += removed
                if not review.body_violations:
                    performance = await refill_suggestions_after_review_filter(
                        performance, removed_suggestions=removed,
                    )
                if review.body_violations or (review.offer_present and not review.valid):
                    diagnostics["semantic_review_fallback"] = True
                    diagnostics["semantic_review_fallback_phase"] = "ordinary"
                # 取消正式转场后的留幕稿同样只能锁存复核有效的新邀请；旧邀请是否保留由
                # invalidate_previous_offer 与 Runtime 共同决定，不能借无效新去向续命。
                reviewed_transition_offered = review.offer_present and review.valid
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
    # 只在完整复核确认正文安全且没有公开邀请时，追加作者写定的可见邀请。
    # 该文案属于剧本合同，不再调用 Actor；真正换幕仍需玩家下一回合明确接受。
    completion_ready_before_turn = (
        runtime.engine.completion_contract_satisfied(current.session) is True
    )
    fallback_route = (
        runtime.engine.preview_route(current.session.current_node_id, outcome.session.metrics)
        if completion_ready_before_turn
        else None
    )
    fallback_target = (
        runtime.engine.nodes.get(str(fallback_route.get("target_node_id") or ""))
        if isinstance(fallback_route, Mapping)
        else None
    )
    fallback_contract = (
        fallback_route.get("transition_contract")
        if isinstance(fallback_route, Mapping)
        else None
    )
    fallback_offer = (
        str(fallback_contract.get("fallback_offer") or "").strip()
        if isinstance(fallback_contract, Mapping)
        else ""
    )
    if (
        not route_changed
        and not current.session.transition_offered
        and evaluation.transition_intent != "reject"
        and not (evaluation.interaction_intent == "chat" and turn.input_source != "suggestion")
        and isinstance(fallback_target, Mapping)
        and fallback_target.get("type") != "ending"
        and fallback_target.get("terminal") is not True
        and fallback_offer
        and final_fixed_review is not None
        and not final_fixed_review.offer_present
        and not final_fixed_review.body_violations
    ):
        visible_performance = str(performance.get("performance") or "").rstrip()
        if fallback_offer not in visible_performance:
            visible_performance = "\n".join(
                item for item in (visible_performance, fallback_offer) if item
            )
        performance = {
            **performance,
            "performance": visible_performance,
            "transition_offered": True,
        }
        reviewed_transition_offered = True
        diagnostics["completion_fallback_offer_applied"] += 1
        trace_event(
            "completion.fallback_offer_applied",
            route_id=fallback_route.get("id"),
            target_node_id=fallback_route.get("target_node_id"),
        )

    # 最终复核比 Actor 更适合做语义检测；两者复用同一紧凑协议，按键去重后仍由 Runtime 裁定。
    actor_fact_candidates = performance.pop("fact_candidates", [])
    review_fact_candidates = (
        list(final_fixed_review.fact_candidates)
        if final_fixed_review is not None and not final_fixed_review.body_violations
        else []
    )
    diagnostics["review_fact_candidates_proposed"] = len(review_fact_candidates)
    proposed_fact_candidates: list[Mapping[str, Any]] = []
    for candidate in (*review_fact_candidates, *(actor_fact_candidates if isinstance(actor_fact_candidates, list) else [])):
        if not isinstance(candidate, Mapping):
            continue
        proposed_fact_candidates.append(candidate)
    if proposed_fact_candidates:
        # 每条候选仍执行完整白名单、类型和逐字证据校验；坏候选只淘汰自己，不能拖掉
        # 同批合法事实。所有通过项仍只写入未提交的 outcome，最终与本回合一次性原子提交。
        actor_fact_audit: list[dict[str, Any]] = []
        committed_facts = current.session.story_state.get("facts")
        if not isinstance(committed_facts, Mapping):
            committed_facts = {}
        for candidate in proposed_fact_candidates:
            key = str(candidate.get("key") or "")
            value = candidate.get("value")
            committed = committed_facts.get(key)
            if isinstance(committed, Mapping) and committed.get("value") == value:
                trace_event("fact_candidates.ignored", key=key, reason="already_committed")
                continue
            current_turn_operation = next((
                operation
                for operation in outcome.ledger_event.get("fact_operations") or []
                if isinstance(operation, Mapping) and operation.get("key") == key
            ), None)
            if (
                isinstance(current_turn_operation, Mapping)
                and current_turn_operation.get("value") == value
            ):
                trace_event("fact_candidates.ignored", key=key, reason="current_turn_duplicate")
                continue
            try:
                outcome, candidate_audit = runtime.engine.finalize_actor_fact_candidates(
                    current.session,
                    outcome,
                    candidates=[candidate],
                    evidence_sources={
                        "actor_performance": _actor_fact_evidence_text(performance),
                    },
                )
            except NumericV2RuntimeError as exc:
                # 拒绝原因只记录字段与错误码，不把候选正文复制到日志摘要。
                diagnostics["fact_candidates_rejected"] += 1
                trace_event("fact_candidates.rejected", key=key, reason=str(exc))
            else:
                actor_fact_audit.extend(candidate_audit)
        if actor_fact_audit:
            diagnostics["fact_candidates_accepted"] += len(actor_fact_audit)
            trace_event("fact_candidates.accepted", audit=actor_fact_audit)
    completion_status = runtime.engine.completion_contract_satisfied(outcome.session)
    diagnostics["completion_contract_status"] = (
        "undeclared" if completion_status is None
        else "satisfied" if completion_status
        else "pending"
    )
    trace_event(
        "completion_contract.checked",
        status=diagnostics["completion_contract_status"],
    )

    # 新提议按正文复核结论锁存；末稿兜底可以提交正文，但不能新增复核判无效的邀请。
    # Runtime 已经根据本轮 Evaluator 结果计算出转场生命周期，尤其是 unclear 时必须保留旧提议；
    # 这里不能再用 Actor 的 false 覆盖 Runtime 的 true，否则下一轮 Evaluator 会失去可见提议。
    # Workflow 提交已复核或明确兜底的新提议信号；旧状态保留及三份记录同步均由Runtime决定。
    filtered_performance = apply_deterministic_suggestion_filter(performance)
    new_offer = (
        performance.get("transition_offered") is True
        or reviewed_transition_offered
    )
    acceptance_route = runtime.engine.preview_route(
        outcome.session.current_node_id,
        outcome.session.metrics,
    )
    acceptance_contract = (
        acceptance_route.get("transition_contract")
        if isinstance(acceptance_route, Mapping)
        else None
    )
    authored_accept_input = (
        str(acceptance_contract.get("accept_input") or "").strip()
        if isinstance(acceptance_contract, Mapping)
        else ""
    )
    if module_options.get("review") and reviewed_transition_offered:
        filtered_performance, acceptance_inserted = (
            _insert_verified_offer_acceptance_suggestion(
                filtered_performance,
                accept_input=authored_accept_input,
            )
        )
        if acceptance_inserted:
            diagnostics["verified_offer_acceptance_suggestions_inserted"] += 1
            trace_event(
                "transition.verified_acceptance_suggestion_inserted",
                suggested_inputs=filtered_performance.get("suggested_inputs"),
            )
    filtered_performance, acceptance_preserved = _preserve_pending_acceptance_suggestion(
        filtered_performance,
        current=current,
        keep_pending=(
            current.session.transition_offered
            and outcome.session.current_node_id == current.session.current_node_id
            and not invalidate_previous_offer
            and not new_offer
        ),
    )
    if acceptance_preserved:
        diagnostics["pending_acceptance_suggestions_preserved"] += 1
        trace_event(
            "transition.pending_acceptance_suggestion_preserved",
            suggested_inputs=filtered_performance.get("suggested_inputs"),
        )
    outcome, performance = runtime.engine.finalize_transition_offer_state(
        outcome,
        filtered_performance,
        new_offer=new_offer,
        invalidate_previous_offer=invalidate_previous_offer,
    )
    if final_fixed_review is not None and not final_fixed_review.body_violations:
        performance = apply_triggers(
            runtime.engine.nodes[current.session.current_node_id], current.session, performance,
            final_fixed_review.fixed_narration_triggers, turn.message,
            known=outcome.session.player_address_known,
        )
    # 模型调用不占生命周期锁；仅将身份复验、展示刷新和原子提交与角色改名串行。
    trace_event("turn.finalized", state=trace_state(outcome.session), performance=performance,
                semantic_review_fallback=diagnostics["semantic_review_fallback"],
                semantic_review_fallback_phase=diagnostics["semantic_review_fallback_phase"])
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
    # Reviews can quote private player/candidate text. Ordinary logs accept only these
    # numeric timings, counters and flags; detailed diagnostics stay with the caller.
    log_diagnostics = {
        key: diagnostics[key]
        for key in (
            "evaluator_model_attempts", "actor_generation_attempts", "actor_provider_calls",
            "actor_suggestion_fill_attempts", "actor_suggestion_fill_provider_calls",
            "actor_suggestion_refill_after_review_attempts",
            "transition_judge_calls", "dispute_review_attempts", "semantic_rewrite_attempts",
            "transition_cancellations", "missed_initiation_recoveries", "unsafe_suggestions_removed",
            "dispute_review_skipped_high_confidence_body",
            "dispute_review_skipped_unsafe_offer_buttons",
            "dispute_review_skipped_contract_offer",
            "dispute_review_deferred_offer_repair",
            "explicit_player_movement_flags_cleared",
            "actor_repeated_output_retry_aborted",
            "player_action_projection_conflicts", "player_action_projection_safe_degrades",
            "transition_judge_degraded", "dispute_review_degraded", "semantic_review_fallback",
            "evaluator_degraded", "completed",
        )
        if type(diagnostics.get(key)) in (int, bool)
    }
    log_diagnostics["timings_ms"] = {
        key: value for key in (
            "evaluator_work", "runtime_prepare_work", "actor_work", "transition_judge_work",
            "history_lookup_work", "commit_work", "total_wall",
        )
        if type(value := diagnostics["timings_ms"].get(key)) in (int, float)
    }
    logger.info(
        "Numeric v2 workflow timing: session_id=%s revision=%s diagnostics=%s",
        current.session.session_id,
        stored.session.revision,
        log_diagnostics,
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
