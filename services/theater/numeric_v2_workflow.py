"""Numeric v2 application-level turn workflow, excluding HTTP request/response mapping."""  # noqa: DOCSTRING_CJK

from __future__ import annotations

from collections.abc import Awaitable
import asyncio
from copy import deepcopy
from dataclasses import dataclass, replace
import json
import logging
import re
import time
from typing import Any, Callable, Mapping

from utils.character_memory import character_config_mutation_lock

from . import numeric_v2_options
from .numeric_v2_actor import (
    NumericV2Actor,
    NumericV2ActorOutputError,
    _allows_local_suggestions_before_ending,
    actor_visible_profile,
)
from .numeric_v2_actor_output import (
    NumericV2ActorError,
    _normalized_comparison_text,
    _without_standalone_transition_offer,
)
from .numeric_v2_action_projection import (
    normalize_player_action_projection,
    performed_player_handoff,
    project_player_action_result,
)
from .numeric_v2_cast import NumericV2CastProjection
from .numeric_v2_context import (
    contract_boundary_items,
    current_entry_fixed_narrations,
    current_scene_records,
    missing_contract_names,
    pending_transition_performance,
    pending_transition_record,
    premature_target_markers,
    premature_target_scene_facts,
    public_player_fact_evidence,
    recorded_silent_choice,
    scene_opening_text,
    silent_exit_allowed as _silent_exit_allowed,
    transition_bridge_leak_markers,
)
from .numeric_v2_fixed_narration import apply_triggers, has_pending_entry, required_pending, review_candidates
from .numeric_v2_fact_discovery import (
    CompletionFactBatch,
    bind_source_delivery,
    build_packet as completion_evidence_packet,
    combine_actor_claims,
    matches as completion_evidence_matches,
    rebind_final as rebind_completion_evidence,
)
from .numeric_v2_history import lookup_history
from .numeric_v2_evaluator import (
    NumericV2EvaluationResult,
    NumericV2EvaluatorError,
    NumericV2MetricEvaluator,
    NumericV2StoryEvidenceReview,
    NumericV2TransitionOfferReview,
    OFFER_KIND_EXIT_MENTION_ONLY,
    PLAYER_ACTION_KIND_REQUESTED_MOVEMENT,
    NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS,
    NUMERIC_V2_CONTRACT_CHECK_TIMEOUT_SECONDS,
    _actor_completion_fact_candidates,
    _adds_unconfirmed_prerequisite,
    _pending_completion_facts,
    _scene_delivery_plan,
    _verified_body_issues,
    _FIXED_ORIGINAL_CAUSE_RULE,
    _parse_output as _parse_evaluation_output,
    _committed_completion_evidence,
    _committed_player_route_evidence,
)
from .numeric_v2_options import aload_theater_module_options
from .numeric_v2_budget import numeric_v2_actor_budget
from .numeric_v2_performance import (
    mixed_performance_blocks,
    performance_content_blocks,
    performance_fingerprint,
    scene_narration_text,
    transition_source_dialogue_policy,
    valid_mixed_performance_policy,
)
from .numeric_v2_runtime import (
    NumericV2Engine,
    NumericV2Runtime,
    NumericV2RuntimeError,
    ScriptSessionV2,
    TurnOutcomeV2,
    TurnRequestV2,
    current_visit_started_revision,
    _FACT_CANDIDATE_MAX_EVIDENCE,
    _matches_evidence_anchor,
    _normalize_actor_fact_candidates,
    validate_fact_candidates,
)
from .numeric_v2_store import NumericV2StoredSession
from .numeric_v2_trace import text_trace_scope, trace_event, trace_state
from .numeric_v2_transition_text import (
    is_explicit_transition_deferral,
    is_placeholder_accept_input,
    is_pressure_only_offer,
)


logger = logging.getLogger(__name__)

# Whole-turn review budget. Always run initial quick check; after exhaustion, add no dispute recheck or post-rewrite review.
# Never commit an unreviewed current draft; an old draft's verdict cannot approve rewritten narrative.
NUMERIC_V2_REVIEW_BUDGET_SECONDS = 20.0
# Revisions share one wait budget per turn; keep commit outside cancellable timeout context.
NUMERIC_V2_TURN_MODEL_BUDGET_SECONDS = 60.0
NUMERIC_V2_TURN_REVIEW_RESERVE_SECONDS = 8.5
NUMERIC_V2_MAX_ACTOR_DRAFTS_PER_TURN = 4
# Each dispute recheck waits at most 8 seconds; timeout retains conservative rollback/fallback without changing authorization.
NUMERIC_V2_DISPUTE_TIMEOUT_CAP_SECONDS = 8.0
# Intercept only explicit recommendation phrasing asserting external outcomes absent from current narrative as facts;
# existing review handles ambiguous semantic ownership, item holders, and plot plausibility, without guesses here.
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


def _review_denies_narration_only_offer(
    candidate: Mapping[str, Any],
    review: NumericV2TransitionOfferReview,
) -> bool:
    """Clear a narration-only offer flag only on the Guard's structured exit-mention code.

    The Guard reports ``offer_kind=exit_mention_only`` when its verified offer quote
    merely shows where the exit is and nobody invites the player. ``failure_reason``
    is diagnostic prose and is never parsed here: an absent or unknown kind keeps the
    offer flag (fail closed), and a quote the neko speaks in dialogue never clears.
    """

    if (
        not review.offer_present
        or review.valid
        or review.body_violations
        or not review.offer_quote
        or review.offer_kind != OFFER_KIND_EXIT_MENTION_ONLY
    ):
        return False
    quote_sources = {
        str(block.get("type") or "")
        for block in performance_content_blocks(candidate)
        if review.offer_quote in str(block.get("text") or "")
    }
    return "narration" in quote_sources and "dialogue" not in quote_sources


def _review_mislabels_explicit_player_movement(
    review: NumericV2TransitionOfferReview,
) -> bool:
    """Clear a ``player_action`` veto only on the Guard's structured requested-movement code.

    The Guard reports ``player_action_kind=requested_movement`` when the sole
    player-side action it flagged is the movement the player explicitly asked for
    this turn. ``failure_reason`` is diagnostic prose and is never parsed here: an
    absent or unknown kind keeps the veto (fail closed).
    """

    if (
        review.offer_present
        or tuple(review.body_violations) != ("player_action",)
        or review.body_issues
    ):
        return False
    # Clear only contradiction where action is exactly this turn's request; infer no destination, create no act change, allow no extra action.
    return review.player_action_kind == PLAYER_ACTION_KIND_REQUESTED_MOVEMENT


def _player_action_projection_conflicts_with_review(
    review: NumericV2TransitionOfferReview,
    player_action_projection: Mapping[str, Any] | None,
) -> bool:
    """Identify structured conflicts where departed scenes are narrated back into the current act."""  # noqa: DOCSTRING_CJK

    if (
        review.offer_present
        or "player_action" not in review.body_violations
        or not set(review.body_violations).issubset({"player_action", "scene_boundary"})
        or not isinstance(player_action_projection, Mapping)
    ):
        return False
    projection = normalize_player_action_projection(player_action_projection)
    if not projection.get("player_left_current_scene"):
        return False
    return any(
        issue["code"] == "player_return_after_departure"
        for issue in review.body_issues
    )


def _safe_degrade_conflicting_scene_update(
    candidate: Mapping[str, Any],
    review: NumericV2TransitionOfferReview,
    player_action_projection: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """Trim only scene updates Review locates as conflicts, retaining entire safe dialogue segments."""  # noqa: DOCSTRING_CJK

    if not _player_action_projection_conflicts_with_review(review, player_action_projection):
        return None
    scene_update = str(candidate.get("scene_narration") or "")
    performance = str(candidate.get("performance") or "")
    # Require matching conflicts in this draft's standalone narration; never trim dialogue conflicts, other issues, or stale quotes.
    if not review.body_issues or not all(
        issue["code"] == "player_return_after_departure"
        and issue["field"] == "scene_update"
        and bool(issue["quote"].strip())
        and issue["quote"] in scene_update
        and issue["quote"] not in performance
        for issue in review.body_issues
    ):
        return None
    degraded = dict(candidate)
    degraded.pop("scene_narration", None)
    # scene_update is the sole rejected structure; same candidate's fact evidence may come from deleted narration and cannot still commit.
    degraded.pop("fact_candidates", None)
    degraded["transition_offered"] = False
    return degraded


def _safe_drop_invalid_scene_update(
    candidate: Mapping[str, Any],
    review: NumericV2TransitionOfferReview,
) -> dict[str, Any] | None:
    """Remove located conflicts existing only in optional narration, preserving approved dialogue."""  # noqa: DOCSTRING_CJK

    fixed_content_only = (
        review.body_violations == ("author_boundary",)
        and bool(review.body_issues) and bool(review.fixed_narration_triggers)
        and all(issue.get("code") == "fixed_narration_content" for issue in review.body_issues)
    )
    if (
        not review.scene_update_removal_safe
        or not review.body_violations
        or (not fixed_content_only
            and not set(review.body_violations).issubset({"player_action", "scene_boundary"}))
        or review.offer_present or review.missed_initiation
        or review.fact_candidates or review.approved_evaluator_fact_indexes
        or (review.fixed_narration_triggers and not fixed_content_only)
        or candidate.get("segments") or candidate.get("fixed_narrations")
    ):
        return None
    narration = str(candidate.get("scene_narration") or "")
    performance = str(candidate.get("performance") or "")
    if not narration.strip() or not performance.strip() or not review.body_issues:
        return None
    # Allow triggers proven by retained dialogue itself only; evidence solely in deleted narration, player input, or history uses original repair.
    # Do not reinterpret trigger semantics, undo valid actions, or treat trimming as display authorization.
    if fixed_content_only and any(
        not isinstance(claim.get("evidence"), str) or not claim["evidence"].strip()
        or claim["evidence"] not in performance
        for claim in review.fixed_narration_triggers
    ):
        return None
    # Even caller-constructed Review cannot supply trusted stale quotes or locations covering only part of a conflict.
    covered: set[str] = set()
    for issue in review.body_issues:
        quote = issue.get("quote")
        violations = issue.get("violations")
        if (
            issue.get("code") not in {"other", "player_return_after_departure", "fixed_narration_content"}
            or issue.get("field") != "scene_update"
            or not isinstance(quote, str) or not quote.strip()
            or quote not in narration or quote in performance
            or not isinstance(violations, (list, tuple)) or not violations
            or not set(violations).issubset(review.body_violations)
        ):
            return None
        covered.update(violations)
    if covered != set(review.body_violations):
        return None
    repaired = {**candidate, "transition_offered": False}
    repaired.pop("scene_narration", None)
    repaired.pop("fact_candidates", None)
    return repaired


def _safe_drop_invalid_offer(
    candidate: Mapping[str, Any],
    review: NumericV2TransitionOfferReview,
) -> dict[str, Any] | None:
    """Remove only standalone invalid trailing invitations, retaining approved narrative."""  # noqa: DOCSTRING_CJK

    if (
        not review.offer_present or review.valid or review.body_violations
        or review.missed_initiation or not review.unsafe_suggestion_indexes
        or review.fact_candidates or review.approved_evaluator_fact_indexes
        or review.fixed_narration_triggers
        or candidate.get("segments") or candidate.get("scene_narration")
        or candidate.get("fixed_narrations")
    ):
        return None
    text = str(candidate.get("performance") or "").rstrip()
    quote = review.offer_quote.strip()
    blocks = mixed_performance_blocks(text)
    if (
        not quote or not text.endswith(quote) or text.count(quote) != 1
        or not blocks or blocks[-1].get("type") != "dialogue"
        or blocks[-1].get("text") != quote
        or not any(block.get("type") == "dialogue" for block in blocks[:-1])
    ):
        return None
    repaired = {**candidate, "performance": text[:-len(quote)].rstrip(), "transition_offered": False}
    # With Review enabled, unconfirmed Actor candidates are already rejected; trimming must not preserve them as a bypass.
    repaired.pop("fact_candidates", None)
    return repaired


def _suggestion_has_unproven_result_claim(suggestion: str, visible_text: str) -> bool:
    """Identify recommendations with explicit result markers and short assertions absent from current visible narrative."""  # noqa: DOCSTRING_CJK

    blocks = mixed_performance_blocks(suggestion)
    if [block.get("type") for block in blocks] not in (["dialogue"], ["action", "dialogue"]):
        return False
    # Optional gestures must not decide whether a spoken result claim is checked.
    # Reuse the existing limited filter; do not invent a broader truth classifier.
    dialogue = str(blocks[-1].get("text") or "").strip()
    # Questions request information rather than asserting known results; let model review decide overreach.
    if any(marker in dialogue for marker in ("？", "?", "吗", "什么", "怎么", "是否", "有没有")):
        return False
    normalized_visible = _normalized_comparison_text(visible_text)
    if not normalized_visible:
        return False
    for marker in _SUGGESTION_RESULT_CLAIM_MARKERS:
        marker_index = dialogue.find(marker)
        if marker_index < 0:
            continue
        # Take short windows around marker, requiring the entire fragment to appear in current visible content;
        # this blocks '读数显示蘑菇村' (reading shows Mushroom Village), without rejecting '让我看看读数' (let me see the reading).
        claim_window = dialogue[marker_index: marker_index + len(marker) + 6]
        if _normalized_comparison_text(claim_window) not in normalized_visible:
            return True
    return False


def _prefilter_suggestion_candidates(
    candidate: Mapping[str, Any],
) -> tuple[dict[str, Any], int, tuple[str, ...]]:
    """Remove provably future-result recommendations before model review without changing narrative/transition fields."""  # noqa: DOCSTRING_CJK

    result = dict(candidate)
    suggestions = candidate.get("suggested_inputs")
    if not isinstance(suggestions, list) or not suggestions:
        return result, 0, ()
    visible_text = "\n".join(
        str(block.get("text") or "")
        for block in performance_content_blocks(candidate)
        if str(block.get("text") or "").strip()
    )
    kept: list[str] = []
    reasons: list[str] = []
    for suggestion in suggestions:
        text = str(suggestion or "").strip()
        if is_placeholder_accept_input(text):
            reasons.append("placeholder_accept_input")
            continue
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


def _drop_completed_action_suggestions(
    suggestions: list[str], engine: NumericV2Engine, session: ScriptSessionV2,
) -> list[str]:
    """Use declared evidence anchors for explicit redo actions in the current visit."""
    visit_start = current_visit_started_revision(session)
    anchors = []
    for requirement in engine.nodes[session.current_node_id].get("completion_contract", {}).get("all", ()):
        key = requirement.get("key")
        fact = session.story_state["facts"].get(key)
        anchor = engine.fact_contract.get(key, {}).get("evidence_anchor")
        if (anchor and fact and fact["visibility"] == "public" and fact["value"] == requirement["equals"]
                and fact["updated_revision"] >= visit_start):
            anchors.append(anchor)
    if not anchors:
        return suggestions
    return [text for text in suggestions if not any(
        block["type"] == "action" and re.match(r"^(?:我)?(?:重新|再做一遍|再执行一次|再一次)", block["text"])
        and any(_matches_evidence_anchor(anchor, block["text"]) for anchor in anchors)
        for block in mixed_performance_blocks(text)
    )]


def _drop_undelivered_display_suggestions(
    candidate: Mapping[str, Any], review: NumericV2TransitionOfferReview, *, node_id: str,
) -> tuple[dict[str, Any], int]:
    """Settle display dependencies by actual delivery; clear pre-generated recommendations when new originals appear this turn."""  # noqa: DOCSTRING_CJK

    result = dict(candidate)
    if candidate.get("segments"):
        return result, 0
    new_display = any(
        item.get("node_id") == node_id and item.get("position") == "after"
        for item in candidate.get("fixed_narrations", [])
    )
    if not review.display_dependent_suggestions and not new_display:
        return result, 0
    delivered = {
        item["id"] for item in candidate.get("fixed_narrations", [])
        if item.get("node_id") == node_id
    }
    blocked = {
        item["text"] for item in review.display_dependent_suggestions
        if not set(item["requires"]).issubset(delivered)
    }
    before = candidate.get("suggested_inputs", [])
    # Bind original text, not old index: invalid recommendations may have been deleted or accept buttons inserted; never restore removed recommendations.
    # Text inserted after review cannot allow pre-generated recommendation; empty is acceptable, without extra calls or invented buttons.
    result["suggested_inputs"] = [
        text for text in before
        if not new_display and text not in blocked
    ]
    removed = len(before) - len(result["suggested_inputs"])
    trace_event("suggestions.display_dependencies_resolved", delivered_ids=sorted(delivered),
                post_display_only=new_display, removed=removed)
    return result, removed


def _drop_reported_unsafe_suggestions(
    candidate: Mapping[str, Any],
    unsafe_indexes: tuple[int, ...],
    *,
    review: NumericV2TransitionOfferReview | None = None,
) -> tuple[dict[str, Any], int]:
    """Keep approved originals only when the complete batch has no rejection; never change narrative."""  # noqa: DOCSTRING_CJK

    result = dict(candidate)
    suggestions = candidate.get("suggested_inputs")
    if not isinstance(suggestions, list):
        return result, 0
    if review is not None and review.approved_suggestion_texts is not None and not unsafe_indexes:
        # A live mixed verdict missed one shared premature premise. Explicit allows do not relax batch rejection.
        # Dependencies are provisional: final insertion settlement still removes undelivered pieces.
        allowed = set(review.approved_suggestion_texts) | {
            item["text"] for item in review.display_dependent_suggestions
        }
        result["suggested_inputs"] = [text for text in suggestions if text in allowed]
    elif review is not None or unsafe_indexes:
        # Legacy omission is not approval. Do not guess shared premises or generate replacement buttons.
        result["suggested_inputs"] = []
    else:
        return result, 0
    removed = len(suggestions) - len(result["suggested_inputs"])
    trace_event("suggestions.filtered", reported_indexes=unsafe_indexes,
                reason="explicit_approval" if review is not None and review.approved_suggestion_texts is not None and not unsafe_indexes else "unsafe_batch",
                before=suggestions, after=result["suggested_inputs"])
    return result, removed




async def _review_with_deadline(
    evaluator: NumericV2MetricEvaluator, **review_kwargs: Any,
) -> NumericV2TransitionOfferReview:
    """Bound packing and model review by the same existing deadline."""
    timeout = min(NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS,
                  float(review_kwargs.get("timeout_seconds", NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS)))
    try:
        async with asyncio.timeout(timeout):
            return await evaluator.validate_transition_offer(**review_kwargs)
    except TimeoutError as exc:
        raise NumericV2EvaluatorError("numeric_v2_transition_judge_timeout") from exc


async def generate_validated_opening(
    *,
    engine: NumericV2Engine,
    config_manager: Any,
    session_id: str,
    catgirl_binding: Mapping[str, Any],
    actor_budget_profile: str,
) -> dict[str, Any]:
    """Generate public opening; temporary opening boundaries require review before creating Session."""  # noqa: DOCSTRING_CJK


    trace_event("opening.context", session_id=session_id, story_id=engine.story_id,
                package_hash=engine.compiled.package_hash, package_revision=engine.story["meta"]["revision"],
                actor_budget_profile=actor_budget_profile)
    actor = NumericV2Actor(config_manager)
    opening_options = await aload_theater_module_options()
    opening = await actor.generate_opening(
        engine=engine,
        actor_budget_profile=actor_budget_profile,
        # Opening waits for Actor narrative once; leave recommendation completion to later turns, avoiding another serial model wait before performance starts.
        allow_suggestion_fill=False,
    )
    trace_event("opening.candidate", attempt=1, performance=opening)
    start_node = engine.nodes[str(engine.story["start_node_id"])]
    opening_boundaries = start_node["story_beat"].get("opening_only_boundaries")
    if not opening_boundaries:
        trace_event("opening.ready", performance=opening)
        return dict(opening)

    if not opening_options.get("review"):
        # Review module disabled: deliver Actor opening directly without model review/rewrite.
        trace_event("review.skipped", phase="opening")
        return dict(opening)
    evaluator = NumericV2MetricEvaluator(config_manager)
    for attempt in range(2):
        review_session = engine.create_session(
            session_id=session_id,
            catgirl_binding=catgirl_binding,
            opening_performance={"performance": "", "suggested_inputs": []},
            actor_budget_profile=actor_budget_profile,
        )
        try:
            review = await _review_with_deadline(
                evaluator,
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
        # Narrative and buttons judged separately; deleting bad buttons neither changes narrative facts nor creates new exit proposals.
        opening, _ = _drop_reported_unsafe_suggestions(
            opening, review.unsafe_suggestion_indexes, review=review,
        )
        if not review.body_violations and not review.offer_present:
            trace_event("opening.ready", performance=opening)
            return dict(opening)
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
    repetition_guard: str = "",
) -> str:
    """Give each body retry a distinct rewriting angle to avoid repeating the same sampling path."""

    if last_error_code == "numeric_v2_actor_response_phase_invalid":
        return "表演非空选对应相位；none则表演为空。"

    if last_error_code == "numeric_v2_actor_dialogue_required":
        return (
            "上一稿缺少必需对白。只在本次合同dialogue_policy=required的表演字段补上简短的括号外对白；"
            "forbidden字段仍只写动作，optional字段不强制补对白。保留合法前段和已完成结果，不新增动作或事实。"
        )

    if last_error_code == "numeric_v2_actor_player_handoff_unverified":
        return "上一稿补写了玩家没有做出的递交。只回应玩家已做的动作，保留原持有者，接收与交付事实等玩家亲自递出。"

    if repetition_guard == "player_input":
        return (
            "上一稿把玩家本轮的动作或台词原样写成了你的演出。已发生的玩家动作和持物保持原归属。"
            "直接回答其问题或承接其选择，只演你自己的反应；不新增事实或行动。"
        )

    if repetition_guard == "fixed_narration":
        return (
            "上一稿长段复演了程序即将展示的入幕固定原文。固定原文仍会原样交付，不能抄进任何模型正文。"
            "必要直接前因放本次输出合同指定的前因字段，目标场景旁白只留环境；角色从原文结束后的状态给出新的简短反应；"
            "不要只换开头一句或将事件换词重演，不新增事实或玩家行动。其他转场段及授权边界保持。"
        )

    if route_changed:
        if "repeated" in last_error_code:
            repeated_field = {
                "transition_target": "target_performance",
                "transition_source": "source_performance",
                "earlier_session": "source_performance",
                "transition_target_narration": "target_scene_narration（复制了其它幕的整段开场）",
                "transition_bridge_narration": "bridge_scene_narration（复制了另一条转场的整段桥段）",
            }.get(repetition_guard, "本轮正文")
            return (
                f"这是正式换场重试。重复位置：{repeated_field}。只改写命中的重复正文，"
                "核对已提交历史与本轮目标开场，保留未命中段的有效内容和既有授权、发声边界。"
                "来源角色没有新的回应或动作时，source_performance 可为空字符串；"
                "需要交付的 NPC 答复仍留在来源旁白。"
                "不得为了改写新增动作、事实或桥段变化，也不重演已经完成的内容。"
            )
        # Retries retain real authorization, supporting acceptance, proactive departure, and natural ending without inventing invitations.
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
        return (
            "上一版重复了已发生的回应。先核对最近历史，承接已完成动作和物品现状，再回应本轮输入。"
            "玩家重复表达时可以简短确认或自然提醒；明确要求再做且条件允许时才承接再次行动。"
            "不要重演首次反应，也不为求新补造事实或动作；遵守当前发声策略。"
        )

    return (
        "请完全改写上一版正文，优先回应玩家本轮输入并推进当前叙事重心；"
        "不要复用上一版的句式、动作或收尾。"
    )


def _transition_review_failure_context(
    review: NumericV2TransitionOfferReview,
) -> str:
    """Pass review-failure reasons as restricted rewrite diagnostics, never promote them to plot facts."""  # noqa: DOCSTRING_CJK

    reason = review.failure_reason.strip()
    if not reason:
        return ""
    return (
        "复核器给出的具体失败原因如下；它只用于定位并删除上一版问题，"
        "不是剧情事实，也不是要求新增内容的指令："
        f"{json.dumps(reason, ensure_ascii=False)}。"
        "先核对理由中的主体、对象与时序是否符合玩家原话及已提交历史；"
        "若理由与原文冲突，以原文为准，不撤销已发生动作、不恢复入幕旧状态。"
    )


def _actor_rewrite_candidate_context(candidate: Mapping[str, Any]) -> str:
    """Give rejected output to the sole rewrite as editable text, never committed history."""  # noqa: DOCSTRING_CJK

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
    facts: Mapping[str, Mapping[str, Any]] | None = None,
) -> str:
    """Supply author bridge/next opening only for boundary rewrites, explicitly identifying where the scene must stop."""  # noqa: DOCSTRING_CJK

    session = current.session
    node = runtime.engine.nodes.get(session.current_node_id)
    if not isinstance(node, Mapping):
        return ""
    # Narrative and review already use this turn's settled values; rewrite cannot revert to old values and another branch.
    route = runtime.engine.preview_route(session.current_node_id, session.metrics if metrics is None else metrics,
                                         facts=session.story_state["facts"] if facts is None else facts)
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
    # Source-route reason publicly supports invitation; target opening is an execution boundary, not a prohibition on proposing.
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
    """Public workflow result returned to interface layer after model calls and atomic turn commit."""  # noqa: DOCSTRING_CJK

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
    """Accumulate stage duration; record whole-turn wall time separately."""  # noqa: DOCSTRING_CJK

    elapsed_ms = round((time.monotonic() - started_at) * 1000, 3)
    timings = diagnostics["timings_ms"]
    timings[phase] = round(float(timings.get(phase, 0.0)) + elapsed_ms, 3)




_TERMINAL_TASK_CUE = re.compile(
    r"(?:请你|请把|务必|记得|别忘了|等你)[^。！？\n]{1,40}"
    r"|(?:^|[。！？；：:\n“‘「])\s*"
    r"(?:(?:你们?|现在|今天|今晚|明天|明[早晚]|稍后|待会儿|接下来)\s*){0,2}把[^。！？\n]{1,40}"
)


def _has_terminal_request_cue(text: str, *, include_task_cues: bool) -> bool:
    # These cues schedule a semantic check. They do not distinguish an instruction from a description.
    return "？" in text or "?" in text or (include_task_cues and _TERMINAL_TASK_CUE.search(text) is not None)


def _transition_repair_draft(
    performance: Mapping[str, Any], *, draft_provenance: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project the three committed-format segments back to editable Actor fields."""
    parts = {part["phase"]: part for part in performance["segments"]}
    draft = {
        "source_scene_narration": str(parts["source_response"].get("scene_narration") or ""),
        "source_performance": str(parts["source_response"].get("performance") or ""),
        "bridge_scene_narration": str(parts["transition_bridge"].get("scene_narration") or ""),
        "target_scene_narration": str(parts["target_opening"].get("scene_narration") or ""),
        "target_performance": str(parts["target_opening"].get("performance") or ""),
        "suggested_inputs": deepcopy(performance.get("suggested_inputs") or []),
    }
    if (isinstance(draft_provenance, Mapping)
            and draft_provenance.get("body_fingerprint") == performance_fingerprint(performance)):
        fields = draft_provenance.get("entry_fields")
        if (isinstance(fields, Mapping) and set(fields) == {"target_scene_narration", "target_entry_trigger"}
                and all(isinstance(value, str) for value in fields.values())
                and "\n".join(fields[key] for key in ("target_scene_narration", "target_entry_trigger")
                              if fields[key]) == draft["target_scene_narration"]):
            draft.update(fields)
    return draft


def _terminal_new_question_markers(
    *,
    engine: NumericV2Engine,
    outcome: TurnOutcomeV2,
    performance: Mapping[str, Any],
    include_task_cues: bool = False,
) -> tuple[str, ...]:
    """Select possible next-turn requests for the narrow terminal check."""  # noqa: DOCSTRING_CJK

    target_id = str(outcome.ledger_event.get("to_node_id") or "")
    target = engine.nodes.get(target_id)
    if not isinstance(target, Mapping) or not (
        target.get("type") == "ending" or target.get("terminal") is True
    ):
        return ()
    for block in performance_content_blocks(performance):
        text = str(block.get("text") or "")
        if _has_terminal_request_cue(text, include_task_cues=include_task_cues):
            return ("terminal_new_question",)
    return ()


def _actor_fact_evidence_text(performance: Mapping[str, Any]) -> str:
    """Extract final visible narrative for verbatim citation checks of Actor fact candidates."""  # noqa: DOCSTRING_CJK

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


def _recover_actor_fact_quote_format(candidate: Mapping[str, Any], performance: Mapping[str, Any]) -> Mapping[str, Any]:
    """Recover one current raw field only when its visible characters match uniquely."""

    quote = candidate.get("evidence_quote")
    if (not isinstance(quote, str) or not quote.strip() or isinstance(performance.get("segments"), list)
            or quote.strip() in _actor_fact_evidence_text(performance)):
        return candidate
    quoted_blocks = mixed_performance_blocks(quote)
    visible_quote = "".join(block["text"] for block in quoted_blocks)
    if not visible_quote:
        return candidate
    matches = []
    for field in ("performance", "scene_narration"):
        raw = performance.get(field)
        if not isinstance(raw, str) or not raw.strip():
            continue
        blocks = mixed_performance_blocks(raw) if field == "performance" else [{"text": raw.strip()}]
        visible = "".join(block["text"] for block in blocks)
        start = visible.find(visible_quote)
        if start < 0:
            continue
        if visible.find(visible_quote, start + 1) >= 0:
            return candidate
        matches.append(raw)
    return {**candidate, "evidence_quote": matches[0]} if len(matches) == 1 else candidate


def _expand_grouped_actor_fact_quote(
    candidate: Mapping[str, Any], definition: Mapping[str, Any], performance: Mapping[str, Any],
    *, player_input: str, published_offer: str, recover_formatting: bool = False,
) -> Mapping[str, Any]:
    """Recover an incomplete citation only from its own visible Actor action."""

    original_quote = candidate.get("evidence_quote")
    if (recover_formatting and definition.get("evidence_source") == "performance"
            and isinstance(original_quote, str) and original_quote.strip()
            and original_quote.strip() not in player_input and original_quote.strip() not in published_offer
            and not (performance.get("transition_offered") and not published_offer)):
        candidate = _recover_actor_fact_quote_format(candidate, performance)
    anchor = definition.get("evidence_anchor")
    quote = candidate.get("evidence_quote")
    if (definition.get("evidence_source") != "performance" or not isinstance(anchor, list)
            or not isinstance(quote, str) or not quote.strip()):
        return candidate
    quote = quote.strip()
    if (quote in player_input or quote in published_offer
            or (performance.get("transition_offered") and not published_offer)
            or _matches_evidence_anchor(anchor, quote)
            or not any(_matches_evidence_anchor([group], quote) for group in anchor)):
        return candidate
    matches = [block["text"] for block in mixed_performance_blocks(performance.get("performance"))
               if block["type"] == "action" and quote in block["text"]]
    if len(matches) != 1 or not _matches_evidence_anchor(anchor, matches[0]):
        return candidate
    return {**candidate, "evidence_quote": matches[0]}


def _has_new_review_facts(
    engine: NumericV2Engine,
    current: NumericV2StoredSession,
    outcome: TurnOutcomeV2,
    performance: Mapping[str, Any],
    review: NumericV2TransitionOfferReview,
) -> bool:
    """Only admissible new facts block reuse; validate with final commit's Runtime checks, without semantic guesses."""  # noqa: DOCSTRING_CJK

    committed = current.session.story_state.get("facts") or {}
    existing = outcome.ledger_event.get("fact_operations") or ()
    for candidate in review.fact_candidates:
        key, value = candidate.get("key"), candidate.get("value")
        prior = committed.get(key)
        if isinstance(prior, Mapping) and prior.get("value") == value:
            continue
        if any(item.get("key") == key and item.get("value") == value for item in existing):
            continue
        try:
            engine.finalize_actor_fact_candidates(
                current.session, outcome, candidates=[candidate],
                evidence_sources={"actor_performance": _actor_fact_evidence_text(performance)},
            )
        except NumericV2RuntimeError as exc:
            if str(exc).startswith(("actor_fact_candidate_", "story_fact_candidate_")):
                continue
            # Do not classify transaction/state corruption as bad candidate; unknown failures continue blocking reuse.
            return True
        return True
    return False


def _project_authored_transition_text(engine: NumericV2Engine, session: Any, text: str) -> str:
    """Direct author-text delivery also respects current character binding and known-address boundaries."""  # noqa: DOCSTRING_CJK

    return NumericV2CastProjection.from_story(
        engine.story,
        player_name=(str(session.catgirl_binding.get("player_address") or "你")
                     if session.player_address_known else "你"),
        catgirl_name=str(session.catgirl_binding.get("catgirl_name") or "当前角色"),
    ).text(text)


def _authored_transition_recovery(
    engine: NumericV2Engine, current: NumericV2StoredSession, outcome: TurnOutcomeV2,
    *, reviewed_source: Mapping[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Build only an already authorized, independently playable author entry."""
    source_id = str(outcome.ledger_event.get("from_node_id") or "")
    target_id = str(outcome.ledger_event.get("to_node_id") or "")
    if source_id != current.session.current_node_id or source_id == target_id or outcome.route is None:
        return None
    route = next((gate for gate in engine.nodes[source_id]["route_gates"]
                  if gate["id"] == outcome.ledger_event.get("route_id") and gate["target_node_id"] == target_id), None)
    if route is None or route != outcome.route:
        return None
    contract = route["transition_contract"]
    target = engine.nodes[target_id]
    if (contract.get("fixed_entry_only") is not True
            or outcome.session.dialogue_policy not in {"optional", "forbidden"}
            or not has_pending_entry(target, current.session)):
        return None
    source = {key: deepcopy(reviewed_source[key]) for key in ("performance", "scene_narration", "fixed_narrations")
              if reviewed_source is not None and key in reviewed_source}
    bridge = _project_authored_transition_text(
        engine, outcome.session, str(contract.get("bridge_scene_narration") or ""))
    try:
        result = engine.finalize_transition_performance(outcome, {
            "source_performance": str(source.get("performance") or ""),
            **({"source_scene_narration": source["scene_narration"]} if source.get("scene_narration") else {}),
            "bridge_scene_narration": bridge, "target_scene_narration": "", "target_performance": "",
            "suggested_inputs": [], "transition_offered": False,
        }, target_opening="", bridge_required=bool(contract.get("bridge_required")),
            bridge_scene_narration=bridge,
            source_dialogue_policy=transition_source_dialogue_policy(str(
                outcome.ledger_event.get("source_transition_dialogue_policy")
                or outcome.ledger_event.get("before_dialogue_policy") or "required")),
            target_dialogue_policy=outcome.session.dialogue_policy)
    except NumericV2RuntimeError:
        return None
    if source.get("fixed_narrations"):
        result["segments"][0]["fixed_narrations"] = source["fixed_narrations"]
    # Even author text cannot silently leave a new interaction after input closes.
    if _terminal_new_question_markers(engine=engine, outcome=outcome,
            performance=result, include_task_cues=True):
        return None
    return result


def _pending_offer_acceptance_path(
    session: Any, *, ledger_events: tuple[Mapping[str, Any], ...] = (),
) -> str:
    """Accept only first recommendation of the latest committed performance entry bearing transition_offered.

    Taking the last performance entry can accept stale earlier proposals and send plot backward to previous acts;
    require proposal from the latest commit, with its own proposal flag.
    """  # noqa: DOCSTRING_CJK

    records = tuple(getattr(session, "performance_history", ()) or ())
    if not records:
        return ""
    origin = pending_transition_record(session, ledger_events=ledger_events)
    original_suggestions = origin.get("suggested_inputs") if isinstance(origin, Mapping) else None
    last = records[-1]
    parts = last.get("segments") if isinstance(last, Mapping) and isinstance(last.get("segments"), list) else [last]
    for part in reversed(parts):
        if not isinstance(part, Mapping) or part.get("transition_offered") is not True:
            continue
        suggestions = part.get("suggested_inputs")
        if isinstance(suggestions, list) and suggestions:
            first = str(suggestions[0] or "").strip()
            if first:
                # After filtering old accept button, a follow-up/deferral promoted to first position does not inherit acceptance permission.
                if isinstance(original_suggestions, list) and (
                    not original_suggestions or first != str(original_suggestions[0] or "").strip()
                ):
                    return ""
                return first
    return ""


def _authored_offer_visible(performance: Mapping[str, Any], offer: str) -> bool:
    authored_dialogue = "".join(str(block.get("text") or "") for block in mixed_performance_blocks(offer)
                                if block.get("type") == "dialogue").strip()
    if not authored_dialogue:
        return False
    delivered_dialogue = ""
    for block in performance_content_blocks(performance):
        if block.get("type") == "dialogue":
            delivered_dialogue += str(block.get("text") or "")
            if delivered_dialogue.strip().endswith(authored_dialogue):
                return True
    return False


def _authored_offer_is_canonical_content(performance: Mapping[str, Any], offer: str) -> bool:
    """Only an entire canonical author delivery can be adopted without semantic review."""

    authored = mixed_performance_blocks(offer)
    visible = performance_content_blocks(performance)
    return bool(authored) and visible == authored


def _cited_dialogue_recovery(candidate, findings, *, author_rules, dialogue_policy):
    """Remove only uniquely located, confirmed author-rule quotes for fresh review."""
    raw = candidate.get("performance")
    if not isinstance(raw, str) or not findings or candidate.get("segments") or candidate.get("fixed_narrations"):
        return None
    visible = performance_content_blocks(candidate)
    spans = set()
    for finding in findings:
        block, quote = finding.get("candidate") or {}, finding.get("quote")
        ref = str(block.get("id") or "")
        source = visible[int(ref[1:])] if re.fullmatch(r"c\d+", ref) and int(ref[1:]) < len(visible) else {}
        if (finding.get("rule") not in author_rules or block.get("type") != "dialogue"
                or block.get("phase") != "turn" or block.get("program_fixed_original")
                or source.get("type") != "dialogue" or source.get("text") != block.get("text")
                or not isinstance(quote, str) or not quote or quote not in str(block.get("text") or "")
                or str(block.get("text") or "") not in raw or raw.count(quote) != 1
                or sum(row["text"].count(quote) for row in visible) != 1):
            return None
        start = raw.index(quote)
        span = (start, start + len(quote))
        if any(span != old and span[0] < old[1] and old[0] < span[1] for old in spans):
            return None
        spans.add(span)
    for start, end in sorted(spans, reverse=True):
        raw = raw[:start] + raw[end:]
    recovered = {**candidate, "performance": raw.strip(), "suggested_inputs": [],
                 "fact_candidates": [], "transition_offered": False}
    for key in ("_published_transition_offer", "_transition_offer_provided", "transition_offer_presented"):
        recovered.pop(key, None)
    if (not valid_mixed_performance_policy(recovered, dialogue_policy)
            or not any(block["type"] == "dialogue" for block in mixed_performance_blocks(raw))):
        return None
    return recovered


def _unmarked_authored_offer(
    runtime: NumericV2Runtime, current: NumericV2StoredSession,
) -> str:
    """Locate a latest public author offer for review, without issuing a receipt."""
    session, engine = current.session, runtime.engine
    if session.transition_offered or engine.completion_contract_satisfied(session) is False:
        return ""
    records, _ = current_scene_records(session)
    latest = records[0] if records else {}
    if (latest.get("revision") != session.revision
            or latest.get("from_node_id") != latest.get("to_node_id")
            or latest.get("transition_offer_invalidated") is True
            or latest.get("input_source") in {"reinvite", "accept_invitation"}):
        return ""
    event = next((row for row in current.ledger_events
                  if row.get("result_revision") == session.revision), {})
    if event.get("scene_complete") is not True or event.get("transition_intent") == "reject":
        return ""
    route = engine.preview_route(session.current_node_id, session.metrics, facts=session.story_state["facts"])
    if route is None or engine.route_departure_blocker(session, route,
            condition_narrations_enabled=False) is not None:
        return ""
    target = engine.nodes[route["target_node_id"]]
    contract = route["transition_contract"]
    if target.get("type") == "ending" or target.get("terminal") is True or "silent_choice" in contract:
        return ""
    offer = _project_authored_transition_text(engine, session, str(contract.get("fallback_offer") or "")).strip()
    visible = performance_content_blocks(latest)
    # Do not revive an intermediate proposal followed by a withdrawal or a
    # different arrangement in the same committed response.
    return offer if (offer and visible and visible[-1].get("type") == "dialogue"
        and _authored_offer_visible({"performance": visible[-1]["text"]}, offer)) else ""


def _silent_choice_input(engine, session, contract: Mapping[str, Any]) -> str:
    """Render only explicit structured route choices; never infer missing author intent."""
    choice = contract.get("silent_choice")
    if not isinstance(choice, Mapping) or set(choice) != {"destination", "arrangement"}:
        return ""
    if any(not isinstance(choice[key], str) or not choice[key].strip()
           or "\n" in choice[key] or "\r" in choice[key] for key in choice):
        return ""
    text = f"我同意前往{choice['destination'].strip()}，安排是：{choice['arrangement'].strip()}。"
    acceptance = _project_authored_transition_text(engine, session, text).strip()
    # Validate after cast projection: short author names can expand at runtime.
    try:
        TurnRequestV2.from_mapping({"client_turn_id": "silent_validation", "base_revision": 0,
                                   "message": acceptance, "input_source": "suggestion"})
    except NumericV2RuntimeError as exc:
        if str(exc) == "numeric_turn_input_too_long":
            raise NumericV2RuntimeError("numeric_silent_choice_input_too_long") from exc
        raise
    return acceptance


def _pending_silent_receipt(current: NumericV2StoredSession, *, include_withdrawn: bool = False):
    """Locate a program receipt, never an Actor-authored invitation flag."""
    origin = pending_transition_record(current.session, ledger_events=current.ledger_events,
                                       include_withdrawn=include_withdrawn)
    if not isinstance(origin, Mapping):
        return None
    event = next((row for row in current.ledger_events
                  if row.get("result_revision") == origin.get("revision")), None)
    receipt = event.get("program_invitation") if isinstance(event, Mapping) else None
    return receipt if isinstance(receipt, Mapping) and receipt.get("silent") is True else None


def _confirmed_authored_acceptance(
    engine: NumericV2Engine, current: NumericV2StoredSession, turn: TurnRequestV2,
    *, require_program_invitation: bool = False,
) -> str:
    """Check only the just-displayed author invitation/accept-text pair, without inferring free input or stale invitation semantics."""  # noqa: DOCSTRING_CJK

    session = current.session
    if turn.input_source == "accept_invitation":
        # 接受控件没有玩家台词：权限只来自当前版本的程序回执，不从按钮文字猜意图。
        if turn.message or turn.base_revision != session.revision:
            return ""
        return engine.invitation_acceptance_route(session, ledger_events=current.ledger_events)
    if (not session.transition_offered or turn.input_source != "suggestion"
            or is_placeholder_accept_input(turn.message)):
        return ""
    origin = pending_transition_record(session, ledger_events=current.ledger_events)
    if not isinstance(origin, Mapping):
        return ""
    route = engine.preview_route(session.current_node_id, session.metrics, facts=session.story_state["facts"])
    contract = route.get("transition_contract") if route else None
    if not isinstance(contract, Mapping):
        return ""
    event = next((row for row in current.ledger_events
                  if row.get("result_revision") == session.revision), None)
    receipt = event.get("program_invitation") if isinstance(event, Mapping) else None
    if isinstance(receipt, Mapping) and receipt.get("kind") == "reviewed_dynamic":
        # 动态回执绑定实际展示稿和当前访问，不能拿作者旧原句或后续按钮替代。
        # 首按钮已在签发时独立核验为接受；这里仅承接原样点击，不解释自由输入。
        accept = str(receipt.get("accept_input") or "")
        offer = str(receipt.get("offer") or "")
        if (receipt.get("revision") != session.revision or origin.get("revision") != session.revision
                or event.get("from_node_id") != session.current_node_id
                or event.get("to_node_id") != session.current_node_id
                or event.get("visit_started_revision") != current_visit_started_revision(session)
                or receipt.get("route_id") != route.get("id")
                or receipt.get("target_node_id") != route.get("target_node_id")
                or receipt.get("performance") != origin.get("performance")
                or receipt.get("visible_blocks") != performance_content_blocks(origin)
                or not offer or not _authored_offer_visible(origin, offer)
                or not accept or turn.message.strip() != accept
                or _pending_offer_acceptance_path(session, ledger_events=current.ledger_events) != accept):
            return ""
        return str(route["id"])
    offer = _project_authored_transition_text(engine, session, str(contract.get("fallback_offer") or "")).strip()
    silent_receipt = _pending_silent_receipt(current)
    # A signed button keeps its original text across display-name changes.
    if not offer:
        accept = str(silent_receipt.get("accept_input") or "") if silent_receipt is not None else ""
    else:
        accept = _project_authored_transition_text(
            engine, session, str(contract.get("accept_input") or ""),
        ).strip()
    if require_program_invitation or silent_receipt is not None:
        # A literal quote is not proof of a live invitation. Only the latest
        # committed, program-issued pair can authorize an unreviewed acceptance.
        event = next((row for row in current.ledger_events
                      if row.get("result_revision") == session.revision), None)
        receipt = event.get("program_invitation") if isinstance(event, Mapping) else None
        if (not isinstance(receipt, Mapping)
                or origin.get("revision") != session.revision
                or receipt.get("route_id") != route.get("id")
                or receipt.get("offer") != offer or receipt.get("accept_input") != accept
                or receipt.get("performance") != origin.get("performance")
                or receipt.get("visible_blocks") != performance_content_blocks(origin)):
            return ""
    if silent_receipt is not None:
        if (offer or silent_receipt.get("offer") != ""
                or silent_receipt.get("revision") != session.revision
                or not _silent_exit_allowed(engine.nodes[session.current_node_id])):
            return ""
    if ((not offer and silent_receipt is None) or not accept or turn.message.strip() != accept
            or _pending_offer_acceptance_path(session, ledger_events=current.ledger_events) != accept
            or accept not in origin.get("suggested_inputs", [])
            or (silent_receipt is None and not _authored_offer_visible(origin, offer))):
        return ""
    # Original invitation and current values must select same exit; turn scoring or stale invitation cannot silently replace route.
    event = next((row for row in current.ledger_events if row.get("result_revision") == origin.get("revision")), None)
    offered_route = engine.preview_route(
        session.current_node_id, event["after_metrics"],
        facts=engine.route_facts_at_revision(current.ledger_events, int(event["result_revision"]))
        if engine.node_uses_fact_routes(engine.nodes[session.current_node_id]) else None,
    ) if event else None
    return str(route["id"]) if offered_route and offered_route["id"] == route["id"] else ""


def _preserve_pending_acceptance_suggestion(
    performance: Mapping[str, Any],
    *,
    current: NumericV2StoredSession,
    keep_pending: bool,
    player_input: str = "",
) -> tuple[dict[str, Any], bool]:
    """Keep original accept button first while old invitation awaits confirmation.

    The original button was public and committed with invitation; follow-ups update narrative only, never replacing the sole
    deterministic accept entry. Do not reuse on new invitation, act change, or old-invitation removal, avoiding stale routes.
    If player already submitted it but stayed in act, stop forcing recommendation; old invitation remains freely answerable.
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

    consumed_inputs = [player_input]
    records, _ = current_scene_records(current.session)
    for record in records:
        if record is origin:
            break
        consumed_inputs.append(str(record.get("input_text") or ""))
    return _insert_verified_offer_acceptance_suggestion(
        result, accept_input=acceptance, consumed_inputs=tuple(consumed_inputs),
    )


def _insert_verified_offer_acceptance_suggestion(
    performance: Mapping[str, Any],
    *,
    accept_input: str,
    consumed_inputs: tuple[str, ...] = (),
) -> tuple[dict[str, Any], bool]:
    """Prepare author-written accept button; corresponding candidate review determines delivery eligibility."""  # noqa: DOCSTRING_CJK

    result = dict(performance)
    acceptance = str(accept_input or "").strip()
    if not acceptance or is_placeholder_accept_input(acceptance):
        return result, False
    current_suggestions = result.get("suggested_inputs")
    normalized_acceptance = re.sub(r"\s+", "", acceptance)
    if any(re.sub(r"\s+", "", item) == normalized_acceptance for item in consumed_inputs):
        if isinstance(current_suggestions, list):
            result["suggested_inputs"] = [
                item for item in current_suggestions
                if re.sub(r"\s+", "", str(item or "")) != normalized_acceptance
            ]
        trace_event("transition.consumed_acceptance_suggestion_omitted")
        return result, False
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


def _evaluation_without_evaluator(
    current: Any, turn: Any, *, engine: NumericV2Engine | None = None,
) -> NumericV2EvaluationResult:
    """Deterministic result with evaluation off: no numeric settlement or intent guessing.  # noqa: DOCSTRING_CJK

    Keep one model-independent allowance: clicking accept text paired with latest program-receipt-verified author invitation
    permits Runtime's existing acceptance routing. Everything else is unclear: remain in current act without transition or scoring.
    """  # noqa: DOCSTRING_CJK

    accepted = engine is not None and bool(_confirmed_authored_acceptance(
        engine, current, turn, require_program_invitation=True))
    return NumericV2EvaluationResult(
        metric_changes=(),
        scene_complete=False,
        transition_intent="accept" if accepted else "unclear",
    )


def _shared_agreement_requirement(node: Mapping[str, Any]) -> Mapping[str, Any] | None:
    requirements = node.get("completion_contract", {}).get("all", ())
    shared_goals = {f"goal.{goal['id']}" for goal in node["story_beat"].get("goals", ())
                    if goal.get("owner") == "shared"
                    and goal.get("delivery", {}).get("type") == "shared_agreement"}
    routes = node.get("route_gates", ())
    linked_goals = [route["transition_contract"].get("source_ids", ()) for route in routes]
    return requirements[0] if (
        len(requirements) == 1 and "key" in requirements[0] and routes
        and all(route["transition_contract"].get("advance_on_completion") is True for route in routes)
        and all(len(refs) == 1 and refs[0] in shared_goals for refs in linked_goals)
        and len({refs[0] for refs in linked_goals}) == 1
    ) else None


def _can_combine_ordinary_turn(runtime: NumericV2Runtime, current: NumericV2StoredSession,
                               turn: TurnRequestV2, options: Mapping[str, bool]) -> bool:
    """Share source drafting only when no proposed fact can authorize its own entry."""
    if (numeric_v2_options.MODEL_REVIEWS_ENABLED
            or not options.get("evaluator") or options.get("review")
            or turn.input_source not in {"freeform", "suggestion"}
            or current.session.status != "active" or current.session.transition_offered
            # Legacy scenes have no completion gate; retain Evaluator's public-route initiation judgment.
            or runtime.engine.completion_contract_satisfied(current.session) is not False
            or pending_transition_record(current.session, ledger_events=current.ledger_events,
                                         include_withdrawn=True) is not None):
        return False
    source = runtime.engine.nodes[current.session.current_node_id]
    scene_prefix = f"scene:{source['id']}:"
    pending = _pending_completion_facts(runtime.engine, current.session)
    if runtime.engine.node_has_completion_advance(source):
        requirement = _shared_agreement_requirement(source)
        routes = source.get("route_gates", ())
        if (requirement is None or requirement.get("equals") is not True
                or not options.get("review_contract") or len(routes) != 1
                or not turn.message.strip() or is_explicit_transition_deferral(turn.message)
                or project_player_action_result(turn.message).get("player_left_current_scene")
                or required_pending(source, current.session, condition_triggers_enabled=False)
                or len(pending) != 1 or pending[0]["key"] != requirement["key"]
                or pending[0]["value"] is not True):
            return False
        definition = runtime.engine.fact_contract[requirement["key"]]
        target = runtime.engine.nodes[routes[0]["target_node_id"]]
        return bool(definition.get("visibility") == "public" and not definition.get("evidence_source")
            and not any(key != requirement["key"] and (not key.startswith("scene:")
                        or key.startswith(scene_prefix)) for key in runtime.engine.fact_contract)
            and target.get("type") != "ending" and target.get("terminal") is not True
            and all(row.get("type") == "fact_equals" and row.get("key") == requirement["key"]
                    and row.get("value") is True for row in routes[0]["conditions"].get(
                        "any", routes[0]["conditions"].get("all", ()))))
    if not pending or any(
        runtime.engine.fact_contract[target["key"]].get("evidence_source") != "performance"
        for target in pending
    ) or any(
        key.startswith(scene_prefix) and definition.get("evidence_source") == "player_input"
        and definition.get("value_type") == "string"
        for key, definition in runtime.engine.fact_contract.items()
    ):
        # Player choices and shared agreements must settle before the draft;
        # an Actor's own continuation cannot stand in for those decisions.
        return False
    routes = source.get("route_gates", ())
    target_ids = {route["target_node_id"] for route in routes}
    # Settle the player's choice before drafting when scoring can select a
    # different destination. Local turns and fact-only routes still combine.
    if len(target_ids) > 1 and any(
        row["type"] == "metric_compare"
        for route in routes
        for row in route["conditions"].get("any", route["conditions"].get("all", ()))
    ):
        return False
    targets = [source, *(runtime.engine.nodes[target_id] for target_id in target_ids)]
    return not any(node.get("type") == "ending" or node.get("terminal") is True for node in targets)


def _confirmation_only_acceptance(
    runtime: NumericV2Runtime, current: NumericV2StoredSession, turn: TurnRequestV2,
    *, history_lookup_enabled: bool, review_enabled: bool,
) -> bool:
    """Check an author-declared control input using the ordinary receipt and routing gates."""  # noqa: DOCSTRING_CJK

    session = current.session
    if (history_lookup_enabled or session.status != "active" or not session.transition_offered
            or turn.input_source != "suggestion"
            or turn.base_revision != session.revision
            or turn.client_turn_id in session.processed_client_turn_ids
            or session.dialogue_policy == "forbidden"):
        return False
    route = runtime.engine.preview_route(session.current_node_id, session.metrics,
                                         facts=session.story_state["facts"])
    contract = route.get("transition_contract") if isinstance(route, Mapping) else None
    target = runtime.engine.nodes.get(str(route.get("target_node_id") or "")) if route else None
    if (not isinstance(contract, Mapping)
            or contract.get("accept_input_confirmation_only") is not True
            or "silent_choice" in contract
            or not isinstance(target, Mapping) or target.get("type") == "ending"
            or target.get("terminal") is True
            or runtime.engine.completion_contract_satisfied(session) is not True):
        return False
    # 旧作者显式声明的免评估按钮仅作用于原静态回执；动态措辞仍走常规评估。
    latest = next((event for event in current.ledger_events
                   if event.get("result_revision") == session.revision), {})
    receipt = latest.get("program_invitation")
    if isinstance(receipt, Mapping) and receipt.get("kind") == "reviewed_dynamic":
        return False
    route_id = _confirmed_authored_acceptance(runtime.engine, current, turn, require_program_invitation=True)
    if not route_id or route_id != route["id"]:
        return False
    # Previewing an eligible route does not prove departure: required fixed pieces may still block it.
    # prepare_turn is read-only; the workflow settles again through the same Runtime path before Actor/Review/commit.
    prepared = runtime.prepare_turn(current, turn, (), transition_intent="accept",
        condition_narrations_enabled=review_enabled)
    return (prepared.route is not None and prepared.route["id"] == route_id
            and prepared.session.current_node_id != session.current_node_id
            and prepared.session.status == "active")


def _committed_natural_ending_review_redundant(
    runtime: NumericV2Runtime, current: NumericV2StoredSession, turn: TurnRequestV2,
    evaluation: NumericV2EvaluationResult, ending_route: Mapping[str, Any] | None,
    module_options: Mapping[str, Any],
) -> bool:
    """Leave new ending prose to delivery review only after source completion is committed."""
    if (numeric_v2_options.MODEL_REVIEWS_ENABLED or not module_options.get("evaluator")
            or module_options.get("review") or not module_options.get("review_contract")
            or not module_options.get("review_delivery") or current.session.status != "active"
            or evaluation.scene_complete is not True or evaluation.natural_ending_ready is not True
            or evaluation.transition_intent == "reject" or is_explicit_transition_deferral(turn.message)
            or ending_route is None or runtime.engine.completion_contract_satisfied(current.session) is not True):
        return False
    committed_route = runtime.engine.preview_route(current.session.current_node_id, current.session.metrics,
        facts=current.session.story_state["facts"])
    if committed_route != ending_route:
        return False
    target = runtime.engine.nodes[ending_route["target_node_id"]]
    if target.get("type") != "ending" and target.get("terminal") is not True:
        return False
    try:
        prepared = runtime.prepare_turn(current, turn, evaluation.metric_changes,
            scene_complete=evaluation.scene_complete, natural_ending_ready=evaluation.natural_ending_ready,
            transition_intent=evaluation.transition_intent, fact_operations=evaluation.fact_operations,
            condition_narrations_enabled=bool(module_options.get("review")))
    except NumericV2RuntimeError:
        return False
    return (prepared.route == ending_route and prepared.departure_blocker is None
            and prepared.session.status == "ended" and runtime.engine.completion_contract_satisfied(
                current.session, facts=prepared.session.story_state["facts"]) is True)


def _increment_actor_attempts(diagnostics: dict[str, Any] | None) -> None:
    """Record Actor generation attempts; actual supplier requests are counted separately at Actor call boundaries."""  # noqa: DOCSTRING_CJK

    if diagnostics is not None:
        diagnostics["actor_generation_attempts"] = int(
            diagnostics.get("actor_generation_attempts", 0)
        ) + 1


async def _generate_actor_turn_with_output_retry(
    actor: NumericV2Actor,
    *,
    diagnostics: dict[str, Any] | None = None,
    generation_deadline: float | None = None,
    blocked_player_handoffs: Mapping[str, list[str]] | None = None,
    remaining_attempts: int | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Control retries by switch; Runtime-approved transitions/endings retain one bounded replay repair."""  # noqa: DOCSTRING_CJK

    last_error_code = ""
    last_repetition_guard = ""
    required_retry_hint = str(kwargs.get("retry_hint") or "").strip()
    # Direct callers (existing integrations/tests) default four attempts; workflow passes module switches explicitly.
    allow_output_retry = bool(kwargs.pop("allow_output_retry", True))
    allow_transition_repeat_retry = bool(
        kwargs.pop("allow_transition_repeat_retry", False)
    )
    max_attempts = (
        NUMERIC_V2_MAX_ACTOR_DRAFTS_PER_TURN if allow_output_retry
        else (2 if allow_transition_repeat_retry else 1)
    )
    if remaining_attempts is not None:
        max_attempts = min(max_attempts, remaining_attempts)
    if max_attempts <= 0:
        trace_event("actor.retry_aborted", reason="shared_generation_budget")
        raise NumericV2ActorOutputError("numeric_v2_actor_retry_budget_exhausted")
    for attempt in range(max_attempts):
        if attempt and generation_deadline is not None and time.monotonic() >= generation_deadline:
            # Reuse the original turn deadline, including its Review reserve.
            # An expired timeout callback may not have run yet; do not start
            # another provider request before the event loop delivers it.
            if diagnostics is not None and last_repetition_guard:
                diagnostics["actor_repeated_output_retry_aborted"] = int(
                    diagnostics.get("actor_repeated_output_retry_aborted", 0)
                ) + 1
            trace_event("actor.retry_aborted", attempt=attempt + 1, reason="generation_deadline_exhausted")
            raise NumericV2ActorError("numeric_v2_actor_timeout")
        try:
            retry_kwargs = dict(kwargs)
            if attempt:
                # Retry carries rewrite requirements corresponding to current rejection.
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
                    repetition_guard=last_repetition_guard,
                )
                # Scene-boundary rewrite is core to this generation; even if rewritten draft has formatting errors,
                # later output retries must retain that requirement rather than returning to ordinary generation and crossing acts again.
                retry_kwargs["retry_hint"] = "\n".join(
                    part for part in (required_retry_hint, output_retry_hint) if part
                )
            _increment_actor_attempts(diagnostics)
            trace_event("actor.attempt", attempt=attempt + 1, retry_hint=retry_kwargs.get("retry_hint", ""))
            generated = await actor.generate_turn(**retry_kwargs)
            trace_event("actor.candidate", attempt=attempt + 1, performance=generated)
            if blocked_player_handoffs:
                text = "\n".join(block["text"] for block in performance_content_blocks(generated)
                                 if block["type"] != "dialogue")
                if any(performed_player_handoff(text, names) for names in blocked_player_handoffs.values()):
                    raise NumericV2ActorOutputError("numeric_v2_actor_player_handoff_unverified")
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
            last_repetition_guard = repetition_guard
            if last_error_code == "numeric_v2_actor_player_handoff_unverified" and attempt:
                raise
            targeted_transition_repeat_retry = (
                allow_transition_repeat_retry
                and repetition_guard in {"earlier_session", "transition_source", "transition_target"}
                and attempt == 0
            )
            if targeted_transition_repeat_retry:
                # Runtime-approved transitions and endings retain one narrow retry with generic retries disabled,
                # only for guards detecting segment reuse of historical narrative.
                trace_event(
                    "actor.retry_enabled_for_transition_repeat",
                    attempt=attempt + 1,
                    reason="runtime_transition_repeated_output",
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


def invitation_recovery_contract(runtime: NumericV2Runtime, current: NumericV2StoredSession, *, condition_narrations_enabled: bool = True) -> dict[str, Any] | None:
    """Project a new invitation only when the current state can authorize it."""
    session = current.session
    if session.status != "active" or session.transition_offered or runtime.engine.completion_contract_satisfied(session) is not True:
        return None
    if required_pending(runtime.engine.nodes[session.current_node_id], session,
            condition_triggers_enabled=condition_narrations_enabled):
        return None
    route = runtime.engine.preview_route(session.current_node_id, session.metrics, facts=session.story_state["facts"])
    if not isinstance(route, Mapping):
        return None
    target = runtime.engine.nodes.get(str(route.get("target_node_id") or ""))
    contract = route.get("transition_contract")
    if not isinstance(target, Mapping) or target.get("type") == "ending" or target.get("terminal") is True or not isinstance(contract, Mapping):
        return None
    offer = _project_authored_transition_text(runtime.engine, session, str(contract.get("fallback_offer") or "")).strip()
    acceptance = (_silent_choice_input(runtime.engine, session, contract) if not offer else
                  _project_authored_transition_text(runtime.engine, session, str(contract.get("accept_input") or "")).strip())
    silent = not offer and _silent_exit_allowed(runtime.engine.nodes[session.current_node_id])
    if (not acceptance or is_placeholder_accept_input(acceptance) or (not silent and (
            not offer or is_pressure_only_offer(offer) or not _authored_offer_visible({"performance": offer}, offer)
            or not valid_mixed_performance_policy({"performance": offer}, session.dialogue_policy)))):
        return None
    try:
        TurnRequestV2.from_mapping({"client_turn_id": "reinvitation_validation",
            "base_revision": session.revision, "message": acceptance, "input_source": "suggestion"})
    except NumericV2RuntimeError:
        return None
    return {"route_id": str(route["id"]), "offer": offer, "accept_input": acceptance,
            **({"silent": True} if silent else {})}


def _authored_invitation_recovery(
    runtime: NumericV2Runtime, current: NumericV2StoredSession, outcome: TurnOutcomeV2,
    *, player_input: str,
) -> tuple[TurnOutcomeV2, dict[str, Any], dict[str, Any]] | None:
    """Expose one concrete author-owned next choice after completed in-act work."""
    projection = normalize_player_action_projection(outcome.ledger_event.get("player_action_projection"))
    if (outcome.route is not None or outcome.session.status != "active"
            or outcome.session.current_node_id != current.session.current_node_id
            or outcome.session.dialogue_policy == "forbidden"
            or outcome.ledger_event.get("transition_intent") == "reject"
            or is_explicit_transition_deferral(player_input)
            or projection.get("player_left_current_scene") or projection.get("player_departure_pending")):
        return None
    records, _ = current_scene_records(current.session)
    revisions = {record.get("revision") for record in records}
    if any(event.get("result_revision") in revisions and isinstance(event.get("program_invitation"), Mapping)
           for event in current.ledger_events):
        return None
    # Keep Runtime's original-delivery gate; discarded draft results cannot
    # make an invitation appear available.
    contract = invitation_recovery_contract(runtime, replace(current, session=outcome.session),
        condition_narrations_enabled=outcome.ledger_event.get("condition_narrations_enabled") is not False)
    if (contract is None or contract.get("silent") or not contract["offer"]
            or _adds_unconfirmed_prerequisite(player_input, contract["offer"])
            or any(block["type"] != "dialogue" for block in mixed_performance_blocks(contract["offer"]))):
        return None
    prepared, performance = runtime.engine.finalize_transition_offer_state(outcome, {
        "performance": contract["offer"], "suggested_inputs": [contract["accept_input"]],
    }, new_offer=True)
    return prepared, performance, contract


async def _review_authored_invitation(
    *, evaluator: NumericV2MetricEvaluator, engine: NumericV2Engine,
    session: ScriptSessionV2, message: str, performance: Mapping[str, Any],
    offer: str, accept_input: str | None, timeout_seconds: float,
    require_offer_acceptance: bool = False,
) -> bool:
    """Review a proposal alone, or the legacy proposal/button pair, with no other authority."""

    if accept_input is None and require_offer_acceptance:
        raise ValueError("invitation_proposal_cannot_review_acceptance_text")
    timeout = min(NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS, timeout_seconds)
    if timeout <= 0:
        raise TimeoutError
    candidate = {**deepcopy(dict(performance)), "suggested_inputs": [] if accept_input is None else [accept_input]}
    async with asyncio.timeout(timeout):
        review = await evaluator.validate_transition_offer(
            engine=engine, session=session, message=message, actor_performance=candidate,
            completion_fact_review_only=True, timeout_seconds=timeout,
            **({"invitation_proposal_only": True} if accept_input is None else {}),
            **({"require_offer_acceptance": True} if require_offer_acceptance else {}),
        )
    quote = review.offer_quote.strip()
    authored_dialogue = "".join(
        str(block.get("text") or "") for block in mixed_performance_blocks(offer)
        if block.get("type") == "dialogue"
    )
    if quote not in authored_dialogue and quote in offer:
        # 先确认混排引文逐字来自本次邀请，再按同一规则核对白；不能丢掉伪造动作来凑真引用。
        # 这里只消除表示方式差异，不修改实际稿、回执或任何语义审批结果；纯动作不构成口头邀请。
        quote = "".join(
            str(block.get("text") or "") for block in mixed_performance_blocks(quote)
            if block.get("type") == "dialogue"
        )
    return bool(
        review.offer_present and review.valid and review.offer_kind == "invitation"
        and not review.body_violations and not review.unsafe_suggestion_indexes
        and quote and quote in authored_dialogue
        and _authored_offer_visible(candidate, offer)
        and (accept_input is None or accept_input in (review.approved_suggestion_texts or ()))
        and (not require_offer_acceptance or review.offer_acceptance_valid)
    )


async def _execute_reinvitation(*, config_manager, runtime, current, turn, ensure_current_binding, before_commit):
    # Explicit control action: invitation review never scores input or moves the scene.
    modules = await aload_theater_module_options()
    contract = invitation_recovery_contract(runtime, current, condition_narrations_enabled=bool(modules.get("review")))
    if contract is None:
        raise NumericV2RuntimeError("numeric_reinvitation_not_available")
    outcome = runtime.prepare_turn(current, turn, (), condition_narrations_enabled=False)
    performance = {"performance": contract["offer"], "suggested_inputs": [contract["accept_input"]]}
    dynamic = (numeric_v2_options.MODEL_REVIEWS_ENABLED and not contract.get("silent")
               and (modules.get("evaluator") or modules.get("review")))
    actor = NumericV2Actor(config_manager) if dynamic else None
    generation_profile = actor._character_profile() if actor is not None else ""
    generation_binding = ensure_current_binding(current.session) if dynamic else None
    if dynamic:
        # 控制只请求再提邀请；沿用完整已提交历史和人设，不模拟玩家发言或结算剧情。
        # 一次生成加一次独立核验共享原整轮预算，不补推荐、不嵌套重试、不回放旧邀约兜底。
        deadline = time.monotonic() + NUMERIC_V2_TURN_MODEL_BUDGET_SECONDS
        try:
            async with asyncio.timeout(max(0.001, deadline - time.monotonic() - NUMERIC_V2_TURN_REVIEW_RESERVE_SECONDS)):
                candidate = await actor.generate_turn(
                    engine=runtime.engine, session=current.session, outcome=outcome,
                    player_input="", input_source="reinvite", recent_ledger_events=current.ledger_events,
                    allow_suggestion_fill=False, character_profile=generation_profile,
                )
            suggestions = candidate.get("suggested_inputs") or []
            if (candidate.get("transition_offered") is not True or suggestions
                    or candidate.get("scene_narration") or candidate.get("scene_update")
                    or candidate.get("fact_candidates")):
                raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
            offer = str(candidate.get("performance") or "")
            # 模型只写邀请，不写玩家的接受话语；显式控件稍后消费同一份回执。
            performance = {"performance": offer, "suggested_inputs": []}
            if not valid_mixed_performance_policy(performance, current.session.dialogue_policy):
                raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
            approved = await _review_authored_invitation(
                evaluator=NumericV2MetricEvaluator(config_manager), engine=runtime.engine,
                session=current.session, message="", performance=performance,
                offer=offer, accept_input=None,
                timeout_seconds=min(NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS, deadline - time.monotonic()),
            )
        except (TimeoutError, NumericV2EvaluatorError) as exc:
            raise NumericV2ActorOutputError("numeric_v2_transition_review_failed") from exc
        if not approved:
            raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
        route = runtime.engine.preview_route(current.session.current_node_id, current.session.metrics,
                                             facts=current.session.story_state["facts"])
        contract = {"route_id": contract["route_id"], "kind": "reviewed_proposal", "offer": offer,
                    "target_node_id": route["target_node_id"], "revision": outcome.session.revision}
    outcome, performance = runtime.engine.finalize_transition_offer_state(
        outcome, performance, new_offer=True, invalidate_previous_offer=True)
    if contract.get("silent") is True:
        performance["transition_offer_source"] = "program_silent"
        outcome = replace(outcome, ledger_event={**outcome.ledger_event, "transition_offer_source": "program_silent"})
    outcome = replace(outcome, ledger_event={**outcome.ledger_event, "program_invitation": {
        **contract, "performance": performance["performance"], "visible_blocks": performance_content_blocks(performance),
        **({"revision": outcome.session.revision} if contract.get("silent") else {})}})
    async with character_config_mutation_lock:
        binding = ensure_current_binding(current.session)
        if dynamic and (actor_visible_profile(actor._character_profile()) != actor_visible_profile(generation_profile)
                or (binding.get("catgirl_name") == generation_binding.get("catgirl_name")
                    and binding.get("profile_hash", "") != generation_binding.get("profile_hash", ""))):
            # 长请求期间的人设变更不能让旧人设邀请以新卡版本落盘；仅比较Actor实际消费投影。
            raise ValueError("catgirl_profile_changed_requires_retry")
        refreshed_binding = {**binding, "player_address": current.session.catgirl_binding.get("player_address", "")}
        outcome = replace(outcome, session=replace(outcome.session, catgirl_binding=refreshed_binding))
        async with runtime.story_session_guard():
            if before_commit is not None:
                await before_commit()
            stored = await runtime.commit_turn(outcome, performance)
    return NumericV2TurnWorkflowResult(stored, outcome, performance, binding, {"completed": True})


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
    if turn.input_source == "reinvite":
        return await _execute_reinvitation(config_manager=config_manager, runtime=runtime, current=current, turn=turn,
            ensure_current_binding=ensure_current_binding, before_commit=before_commit)
    if turn.input_source == "accept_invitation" and (
            turn.message or not runtime.engine.invitation_acceptance_route(
                current.session, ledger_events=current.ledger_events)):
        # 无效控件在读取人设或调用模型前拒绝，不能降级成空的普通聊天回合。
        raise NumericV2RuntimeError("numeric_invitation_acceptance_not_available")
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
    """Execute Evaluator, Runtime, narrative/recommendation generation, identity revalidation, and atomic commit in fixed order."""  # noqa: DOCSTRING_CJK

    workflow_started_at = time.monotonic()
    model_deadline = workflow_started_at + NUMERIC_V2_TURN_MODEL_BUDGET_SECONDS
    # Load tester may pass mutable container to read completed stages and model costs even on turn failure.
    diagnostics = diagnostics_sink if diagnostics_sink is not None else {}
    diagnostics.clear()
    diagnostics.update({
        "timings_ms": {
            "evaluator_work": 0.0,
            "runtime_prepare_work": 0.0,
            "actor_work": 0.0,
            "transition_judge_work": 0.0,
            "completion_fact_review_work": 0.0,
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
        "progress_review_calls": 0,
        "completion_fact_review_calls": 0,
        "completion_fact_rewrite_attempts": 0,
        "transition_judge_degraded": False,
        # Rechecks skipped after review-budget exhaustion; diagnostic only, never review approval.
        "review_budget_skips": 0,
        # Deterministic detections of ordinary turns presenting target-act opening / bridge time as current (issue 2.141 B3).
        "target_opening_leak_markers": [],
        # One shared dispute recheck per turn; rewritten drafts cannot retrigger it. Retain quick verdict on failure.
        "dispute_review_attempts": 0,
        "dispute_review_degraded": False,
        # Official-transition button fields do not govern delivery of three narrative parts; without narrative violations, skip dispute wait for invalid buttons.
        "dispute_review_skipped_formal_offer": 0,
        # High-confidence player overreach / premature act change goes directly to rewrite without another dispute wait.
        "dispute_review_skipped_high_confidence_body": 0,
        # Safe narrative with out-of-bounds buttons removes only buttons, without repeated dispute requests.
        "dispute_review_skipped_unsafe_offer_buttons": 0,
        # Clear conflict between narrative invitation and structured exit direction needs no repeat contract judgment.
        "dispute_review_skipped_contract_offer": 0,
        # Ordinary first drafts with invalid invitation alone first use existing rewrite budget, then decide whether dispute recheck is needed.
        "dispute_review_deferred_offer_repair": 0,
        # On dispute timeout with exhausted budget, reject extra Actor rewrite and retain atomic rollback.
        "review_timeout_aborted": False,
        "transition_review_results": [],
        "transition_ownership_retries": 0,
        "transition_scene_boundary_retries": 0,
        "transition_author_boundary_retries": 0,
        "transition_offer_retries": 0,
        "semantic_rewrite_attempts": 0,
        # After correction budget exhaustion use final complete draft; flag is diagnostic, not approval or plot fact.
        "semantic_review_fallback": False,
        "semantic_review_fallback_phase": "",
        "transition_cancellations": 0,
        # Questions left in ending content default to unresolved issues requiring player answers.
        "terminal_new_question_markers": [],
        "terminal_structure_rejected": False,
        "terminal_question_review_calls": 0,
        "terminal_question_review_degraded": False,
        # Missed-detection recovery generates one official candidate without rescoring; commit only official draft on success.
        "missed_initiation_recoveries": 0,
        "recovered_ordinary_drafts_reused": 0,
        "phantom_transition_flags_cleared": 0,
        # Count appended author-provided deterministic invitations when completion contract holds but Actor omitted public exit.
        "completion_fallback_offer_applied": 0,
        "completion_fallback_offer_skipped": 0,
        # Pending confirmation: ordinary follow-ups cannot overwrite original accept button; count actual restorations for load-test tracing.
        "pending_acceptance_suggestions_preserved": 0,
        # Insert fixed accept button only after ordinary review validates new invitation, without extra model calls.
        "verified_offer_acceptance_suggestions_inserted": 0,
        "narration_offer_flags_cleared": 0,
        # Clear contradictory player-overreach enum when model reason explicitly acknowledges action came from player's current request.
        "explicit_player_movement_flags_cleared": 0,
        # Count structured-departure conflicts with Review's detected return to current act.
        "player_action_projection_conflicts": 0,
        # Remove only conflicts located by Review as scene_update, avoiding repeated Actor rewrites of safe dialogue.
        "player_action_projection_safe_degrades": 0,
        "invalid_offer_local_crops": 0,
        "invalid_scene_update_local_crops": 0,
        "unsafe_suggestions_removed": 0,
        "deterministic_suggestions_removed": 0,
        "deterministic_suggestion_filter_reasons": {},
        "fact_candidates_accepted": 0,
        "fact_candidates_rejected": 0,
        "review_fact_candidates_proposed": 0,
        "evaluator_fact_claims_deferred": 0,
        "evaluator_fact_claims_approved": 0,
        "route_suggestion_reviews": 0,
        "evaluator_degraded": False,
        "input_source": turn.input_source,
        "completed": False,
        "authored_transition_recoveries": 0,
        "authored_invitation_recoveries": 0,
        "committed_ending_review_skips": 0,
    })
    evaluator = NumericV2MetricEvaluator(config_manager)
    actor = NumericV2Actor(config_manager)
    discovered_completion_facts: dict[str, CompletionFactBatch] = {}
    source_completion_reviews: dict[str, tuple[CompletionFactBatch, tuple[int, ...]]] = {}
    actor_draft_provenance: dict[str, dict[str, Any]] = {}
    adopted_completion_facts: CompletionFactBatch | None = None
    discovery_collection_open = True
    completion_source_restricted = False

    def completion_packet(candidate):
        if reviewed_transition_source is not None and candidate.get("segments"):
            # Source proof never borrows the target's later result or changes visit scope.
            candidate = {field: candidate["segments"][0][field] for field in (
                "performance", "scene_narration", "fixed_narrations") if field in candidate["segments"][0]}
        metadata, originals = _committed_completion_evidence(runtime.engine, current.session)
        return completion_evidence_packet(runtime.engine, current.session, candidate, turn.message,
            pending_targets=_pending_completion_facts(runtime.engine, current.session),
            history_metadata=metadata, history_originals=originals,
            restrict_player_results=completion_source_restricted)
    # Preliminary evaluation and new completion approval share the evaluator switch; full Review remains independently optional.
    module_options = await aload_theater_module_options()
    diagnostics["theater_module_options"] = dict(module_options)
    # Mandatory contract review can require first review plus confirmation. Reserve both request waits
    # inside the existing turn deadline, without shortening generation for other module configurations.
    required_review_reserve = max(
        NUMERIC_V2_TURN_REVIEW_RESERVE_SECONDS, 2 * NUMERIC_V2_CONTRACT_CHECK_TIMEOUT_SECONDS + 0.5,
    ) if module_options.get("review_contract") and not module_options.get("review") else NUMERIC_V2_TURN_REVIEW_RESERVE_SECONDS
    # Retain legacy diagnostic keys: dispute recheck is now in module table.
    dispute_review_enabled = bool(module_options.get("dispute"))
    diagnostics["dispute_review_enabled"] = dispute_review_enabled
    # Original results belong only to this workflow, shared across narrative retries/reviews without writing Session/Ledger.
    history_lookup_result: dict[str, Any] | None = None
    progress_direction = ""
    current_scene_task: Mapping[str, Any] | None = None
    unconfirmed_departure_quote = ""
    obsolete_offer = False
    wrong_exit_offer_quote = ""
    exit_mismatch_reason = ""
    invalidate_previous_offer = False
    final_fixed_review: NumericV2TransitionOfferReview | None = None
    program_invitation_performance: str | None = None
    # Freeze actual persona input before narrative resampling; recommendations degrade internally, but final narrative must use same character generation.
    generation_binding = ensure_current_binding(current.session)
    generation_profile = actor._character_profile()
    blocked_handoffs = runtime.engine.blocked_player_handoffs(current.session, turn.message,
        input_source=turn.input_source)
    source_node = runtime.engine.nodes[current.session.current_node_id]
    source_completion = source_node.get("completion_contract", {})
    routes = source_node.get("route_gates", ())
    shared_requirement = _shared_agreement_requirement(source_node)
    recommended_turns = source_node.get("recommended_turns")
    progress_review_due = (
        not numeric_v2_options.MODEL_REVIEWS_ENABLED
        and module_options.get("evaluator") and module_options.get("review_contract")
        and not module_options.get("review")
        and turn.input_source in {"freeform", "suggestion"}
        and type(recommended_turns) is int
        and current.session.node_turn_count >= recommended_turns
    )
    if progress_review_due and current.session.node_turn_count > recommended_turns:
        origin = pending_transition_record(current.session, ledger_events=current.ledger_events)
        origin_revision = origin.get("revision") if origin is not None else None
        offered_turn = next(
            (event.get("node_turn_count") for event in current.ledger_events
             if event.get("result_revision") == origin_revision), None,
        )
        elapsed = current.session.node_turn_count - recommended_turns
        progress_review_due = (
            type(offered_turn) is int and offered_turn > recommended_turns
            and current.session.node_turn_count - offered_turn == 2
        )
        if not progress_review_due and type(origin_revision) is int and elapsed % 2 == 0:
            # Each two-turn window can reconsider an old invitation if it
            # contains a newly displayed world change or an authored fact.
            records, _ = current_scene_records(current.session)
            ordinary = [row for row in records if row.get("input_source") not in {"reinvite", "accept_invitation"}]
            prior_narration = {row.get("scene_narration") for row in ordinary[2:]}
            recent_revisions = {row["revision"] for row in ordinary[:2]
                                if type(row.get("revision")) is int and row["revision"] > origin_revision}
            progress_review_due = any(
                row.get("revision") in recent_revisions and row.get("scene_narration")
                and row["scene_narration"] not in prior_narration for row in ordinary[:2]
            ) or any(
                event.get("result_revision") in recent_revisions
                and any(op.get("key") in runtime.engine.fact_contract for op in event.get("fact_operations", ()))
                for event in current.ledger_events
            )
        if origin is None and elapsed == 2 and runtime.engine.completion_contract_satisfied(current.session) is True:
            ready_route = runtime.engine.preview_route(source_node["id"], current.session.metrics,
                                                       facts=current.session.story_state["facts"])
            progress_review_due = bool(ready_route and ready_route["transition_contract"].get("fallback_offer"))
    required_handoff_keys = {
        row["key"] for row in source_completion.get("all", ())
        if row.get("equals") is True and "player_handoff_item" in runtime.engine.fact_contract[row["key"]]
    }

    def respect_player_handoffs(evaluation: NumericV2EvaluationResult) -> NumericV2EvaluationResult:
        rejected = {op["key"] for op in evaluation.fact_operations
                    if op.get("value") is True and op["key"] in blocked_handoffs}
        if rejected:
            trace_event("evaluator.player_handoff_rejected", keys=sorted(rejected))
        if not rejected and not required_handoff_keys.intersection(blocked_handoffs):
            return evaluation
        return replace(evaluation,
            fact_operations=tuple(op for op in evaluation.fact_operations if op["key"] not in rejected),
            fact_audit=tuple(item for item in evaluation.fact_audit if item["key"] not in rejected),
            natural_ending_ready=evaluation.natural_ending_ready and not required_handoff_keys.intersection(blocked_handoffs),
        )

    async def evaluate_turn() -> NumericV2EvaluationResult:
        """Run Evaluator once, conservatively degrading model failures to unchanged state."""  # noqa: DOCSTRING_CJK

        started_at = time.monotonic()
        if turn.input_source == "accept_invitation":
            # 点击本身只确认刚展示的提议；不提供可供打分或补记完成事实的玩家输入。
            diagnostics["evaluator_skipped"] = True
            diagnostics["evaluator_skip_reason"] = "invitation_control"
            trace_event("evaluator.skipped", reason="invitation_control")
            try:
                return NumericV2EvaluationResult((), False, transition_intent="accept",
                    transition_reply_target="pending_transition")
            finally:
                _add_elapsed_ms(diagnostics, "evaluator_work", started_at)
        if not module_options.get("evaluator"):
            # Evaluation module disabled: no model calls, guessed values, or intents.
            diagnostics["evaluator_skipped"] = True
            trace_event("evaluator.skipped")
            try:
                return _evaluation_without_evaluator(current, turn, engine=runtime.engine)
            finally:
                _add_elapsed_ms(diagnostics, "evaluator_work", started_at)
        if _confirmation_only_acceptance(runtime, current, turn,
                history_lookup_enabled=bool(module_options.get("history_lookup")),
                review_enabled=bool(module_options.get("review"))):
            diagnostics["evaluator_skipped"] = True
            diagnostics["evaluator_skip_reason"] = "authored_confirmation_only"
            trace_event("evaluator.skipped", reason="authored_confirmation_only")
            try:
                return NumericV2EvaluationResult((), False, transition_intent="accept",
                    transition_reply_target="pending_transition")
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
                allow_history_lookup=bool(module_options.get("history_lookup")),
                **({"confirmed_silent_choice": True}
                   if confirmed_acceptance_route_id and _pending_silent_receipt(current) is not None else {}),
            )
            trace_event("evaluator.result", result=result)
            return result
        except NumericV2EvaluatorError as exc:
            # Evaluator handles only hidden values and existing transition attitudes; transient service errors must not block normal performance.
            # Reuse deterministic Evaluator-disabled fallback on failure: no value changes or guessed intent,
            # but preserve authorization from verbatim clicking current public proposal's first accept button, avoiding permanent source-act lock.
            diagnostics["evaluator_degraded"] = True
            trace_event("evaluator.degraded", error_code=str(exc))
            logger.warning(
                "Numeric v2 Evaluator degraded to no-op: reason=%s session_id=%s revision=%s",
                str(exc),
                current.session.session_id,
                current.session.revision,
            )
            return _evaluation_without_evaluator(current, turn, engine=runtime.engine)
        finally:
            _add_elapsed_ms(diagnostics, "evaluator_work", started_at)

    approved_completion_facts: tuple[Mapping[str, Any], ...] = ()
    deferred_delivery_claims: tuple[Mapping[str, Any], ...] = ()
    reviewed_transition_source: dict[str, Any] | None = None
    reviewed_transition_player_absence: tuple[Mapping[str, Any], ...] = ()
    approved_delivery_claims: tuple[Mapping[str, Any], ...] = ()
    approved_delivery_fingerprint = ""
    departure_attempt_observed = False
    departure_direction_reviewed = False

    async def review_completion_evidence(
        claims, *, ending_route=None, actor_evidence=None, pending_offer="",
        pending_offer_revision=None, review_progress=False, blocked_departure=False,
        evidence_batch: CompletionFactBatch | None = None,
        separate_on_input_over_budget: bool = False,
    ):
        started_at = time.monotonic()
        try:
            remaining = min(
                NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS,
                NUMERIC_V2_REVIEW_BUDGET_SECONDS - diagnostics["timings_ms"]["transition_judge_work"] / 1000.0,
                model_deadline - time.monotonic() - 0.5,
            )
            if remaining <= 0:
                diagnostics["review_budget_skips"] += 1
                raise NumericV2EvaluatorError("numeric_v2_transition_judge_timeout")
            if claims or ending_route is not None or pending_offer:
                diagnostics["completion_fact_review_calls"] += 1
            diagnostics["transition_judge_calls"] += 1
            if review_progress:
                diagnostics["progress_review_calls"] = 1
            async with asyncio.timeout(remaining):
                reviewed = await evaluator.review_story_evidence(
                    engine=runtime.engine, session=current.session, message=turn.message, claims=claims,
                    ending_route=ending_route, actor_evidence=actor_evidence or "",
                    pending_offer=pending_offer, pending_offer_revision=pending_offer_revision,
                    review_progress=review_progress, timeout_seconds=remaining,
                    **({"evidence_batch": evidence_batch} if evidence_batch is not None else {}),
                    **({"blocked_departure": True} if blocked_departure else {}),
                )
                if completion_source_restricted:
                    reviewed = replace(reviewed, approved_indexes=tuple(index for index in reviewed.approved_indexes
                        if runtime.engine.fact_contract[claims[index]["key"]].get("evidence_source") == "player_input"
                        or not any(row["source"] == "player_input" for row in claims[index]["evidence"])))
                return reviewed
        except (TimeoutError, NumericV2EvaluatorError) as exc:
            if separate_on_input_over_budget and str(exc) == "numeric_v2_story_evidence_input_over_budget":
                # Packing failed before the provider call. Let the optional
                # merge fall back to the original, smaller review packets.
                raise
            diagnostics["transition_judge_degraded"] = True
            trace_event("story_evidence.review_failed", error_code=str(exc))
            return NumericV2StoryEvidenceReview()
        finally:
            if claims or ending_route is not None or pending_offer:
                _add_elapsed_ms(diagnostics, "completion_fact_review_work", started_at)
            _add_elapsed_ms(diagnostics, "transition_judge_work", started_at)

    def defer_shared_delivery(evaluation: NumericV2EvaluationResult) -> None:
        nonlocal deferred_delivery_claims
        if (not numeric_v2_options.MODEL_REVIEWS_ENABLED and module_options.get("evaluator")
                and not module_options.get("review") and module_options.get("review_contract")
                and shared_requirement is not None and shared_requirement.get("equals") is True
                and not completion_source_restricted and not diagnostics["evaluator_degraded"]
                and turn.input_source in {"freeform", "suggestion"} and turn.message.strip()
                and evaluation.transition_intent != "reject" and not is_explicit_transition_deferral(turn.message)
                and all(runtime.engine.nodes[route["target_node_id"]].get("type") != "ending"
                        and runtime.engine.nodes[route["target_node_id"]].get("terminal") is not True for route in routes)):
            key = shared_requirement["key"]
            definition = runtime.engine.fact_contract[key]
            target = next((row for row in _pending_completion_facts(runtime.engine, current.session)
                           if row["key"] == key and row["value"] is True), None)
            if (target is not None and definition.get("visibility") == "public"
                    and not definition.get("evidence_source")
                    and not any(operation["key"] == key for operation in evaluation.fact_operations)
                    and not any(claim["key"] == key for claim in deferred_delivery_claims)):
                # Metadata issues a question for independent review, never an answer.
                deferred_delivery_claims = (*deferred_delivery_claims, {
                    "op": "set", "key": key, "value": True, "visibility": "public",
                    "description": target["description"], "subject": "双方", "action": "共同同意",
                    "object": "作者定义的当前安排", "result": "已达成约定",
                    "evidence": [{"source": "player_input", "quote": turn.message}]})
                trace_event("evaluator.shared_delivery_scheduled", key=key)

    async def approve_evaluator_progress(
        evaluation: NumericV2EvaluationResult, confirmed_route_id: str,
    ) -> NumericV2EvaluationResult:
        """Verify authority before preparing routes and Actor-visible state."""

        nonlocal approved_completion_facts, progress_direction, current_scene_task, obsolete_offer, deferred_delivery_claims

        # Full Review has its own independent fact and transition approval.
        if module_options.get("review") or not module_options.get("evaluator"):
            return evaluation
        narrow_review = not numeric_v2_options.MODEL_REVIEWS_ENABLED
        review_progress = bool(progress_review_due and not confirmed_route_id
                               and evaluation.transition_intent != "initiate")
        if review_progress:
            session = current.session
            route = runtime.engine.preview_route(session.current_node_id, session.metrics,
                                                 facts=session.story_state["facts"])
            if route is not None and runtime.engine.route_departure_blocker(
                session, route, condition_narrations_enabled=bool(module_options.get("review")),
            ) is not None:
                review_progress = False
        # At the checkpoint, review a still-active invitation even if the
        # first evaluator treated a concrete reply as local interaction.
        recover_offer = (
            (evaluation.needs_offer_review or review_progress)
            and evaluation.transition_intent == "unclear" and current.session.transition_offered
            and not evaluation.wrong_exit_offer_quote
            and runtime.engine.completion_contract_satisfied(current.session) is not False
        )
        pending_offer = ""
        pending_offer_revision = None
        unmarked_offer = ""
        unmarked_reply = (evaluation.transition_intent == "unclear" and evaluation.needs_offer_review
                          and not current.session.transition_offered)
        if narrow_review and not confirmed_route_id and (
                evaluation.transition_intent == "accept" or recover_offer or unmarked_reply):
            origin = pending_transition_record(current.session, ledger_events=current.ledger_events, include_withdrawn=True)
            pending_offer = pending_transition_performance(
                current.session, ledger_events=current.ledger_events, include_withdrawn=True,
            )
            if current.session.transition_offered and origin is not None:
                pending_offer_revision = origin.get("revision")
            if recover_offer:
                if (recorded_silent_choice(origin, current.ledger_events) is not None
                        or _adds_unconfirmed_prerequisite(turn.message, pending_offer)):
                    pending_offer = ""
            if (not pending_offer and (evaluation.transition_intent == "accept" or unmarked_reply) and evaluation.scene_complete
                    and not evaluation.exit_mismatch_reason and not evaluation.wrong_exit_offer_quote
                    and turn.input_source in {"freeform", "suggestion"}
                    and not is_placeholder_accept_input(turn.message)
                    and not is_explicit_transition_deferral(turn.message)):
                unmarked_offer = _unmarked_authored_offer(runtime, current)
                pending_offer = unmarked_offer
            if not pending_offer:
                evaluation = replace(evaluation, transition_intent="unclear")
        ending_route = None
        if evaluation.natural_ending_ready and evaluation.scene_complete and evaluation.transition_intent != "reject":
            route = runtime.engine.preview_route(current.session.current_node_id, current.session.metrics,
                                                 facts=current.session.story_state["facts"])
            if route is not None:
                target = runtime.engine.nodes[route["target_node_id"]]
                if target.get("type") == "ending" or target.get("terminal") is True:
                    ending_route = route
        pending = _pending_completion_facts(runtime.engine, current.session)
        completion_keys = {
            operation["key"] for operation in evaluation.fact_operations
            if any(operation["key"] == target["key"]
                   and type(operation["value"]) is type(target["value"])
                   and operation["value"] == target["value"] for target in pending)
        }
        audits = {item["key"]: item for item in evaluation.fact_audit}
        review_keys = set(completion_keys)
        if narrow_review:
            # Previously committed performance is already separate from the
            # Evaluator. Player replies and state excerpts can be mistaken for
            # completed actions even for optional facts or updates to old keys.
            review_keys.update(
                operation["key"] for operation in evaluation.fact_operations
                if not (evidence := audits.get(operation["key"], {}).get("evidence")) or any(
                    row["source"] != "actor_performance" for row in evidence
                )
            )
        defer_shared_delivery(evaluation)
        if not review_keys and ending_route is None and not pending_offer and not review_progress:
            return replace(evaluation, natural_ending_ready=False) if deferred_delivery_claims else evaluation
        history, originals = _committed_completion_evidence(runtime.engine, current.session) if narrow_review else ({}, ())
        if narrow_review and evaluation.transition_intent != "reject":
            if shared_requirement is not None:
                records, _ = current_scene_records(current.session)
                # Entry exposition is not a prior conversational reply. Only supplement from an ordinary turn in this visit.
                latest = next((record for record in records
                    if record.get("from_node_id") == record.get("to_node_id") == current.session.current_node_id
                    and record.get("input_source") not in {"reinvite", "accept_invitation"}
                    and isinstance(record.get("performance"), str) and record["performance"].strip()), {})
                original = str(latest.get("performance") or "").strip()
                for operation in evaluation.fact_operations:
                    key = operation["key"]
                    evidence = audits.get(key, {}).get("evidence", ())
                    if (key == shared_requirement["key"] and key in history.get("allowed_keys", ())
                            and type(operation["value"]) is type(shared_requirement.get("equals"))
                            and operation["value"] == shared_requirement.get("equals")
                            and not runtime.engine.fact_contract[key].get("evidence_source")
                            and evidence and len(evidence) < _FACT_CANDIDATE_MAX_EVIDENCE
                            and all(row["source"] == "player_input" for row in evidence)
                            and original and original in originals):
                        # Goal linkage schedules proof, never establishes agreement.
                        # Bind one complete current-visit role original rather than asking Actor to repeat it.
                        audits[key] = {**audits[key], "evidence": [*evidence,
                            {"source": "actor_performance", "quote": original}]}
        claims = tuple(
            {**audits[operation["key"]], **operation,
             "description": runtime.engine.fact_contract[operation["key"]].get("description", "")}
            for operation in evaluation.fact_operations
            if operation["key"] in review_keys and operation["key"] in audits
        )
        pre_review_delivery_claims: tuple[Mapping[str, Any], ...] = ()
        if narrow_review:
            targets = runtime.engine.scene_fact_targets(current.session.current_node_id)
            delivery_claims = tuple(
                claim for claim in claims if any(row["key"] == claim["key"]
                    and type(row["value"]) is type(claim["value"]) and row["value"] == claim["value"]
                    for row in targets)
                and runtime.engine.fact_contract[claim["key"]].get("evidence_source") != "player_input"
                and any(row["source"] == "player_input" for row in claim["evidence"])
            )
            pre_review_delivery_claims = tuple(claim for claim in delivery_claims
                if claim["key"] in history.get("allowed_keys", ())
                and (quotes := [row["quote"] for row in claim["evidence"] if row["source"] == "actor_performance"])
                and all(isinstance(quote, str) and quote and any(quote in original for original in originals)
                        for quote in quotes))
            pre_review_keys = {claim["key"] for claim in pre_review_delivery_claims}
            deferred_delivery_claims = (*deferred_delivery_claims, *(claim for claim in delivery_claims
                                              if claim["key"] not in pre_review_keys))
            deferred_keys = {claim["key"] for claim in deferred_delivery_claims}
            claims = tuple(claim for claim in claims if claim["key"] not in deferred_keys)
            if deferred_keys:
                # A player's proposal alone still needs actual source delivery.
                # Mixed committed originals go through independent review first.
                ending_route = None
                trace_event("evaluator.source_delivery_deferred", keys=sorted(deferred_keys))
        diagnostics["evaluator_fact_claims_deferred"] += len(claims)
        approved_keys: set[str] = set()
        ending_ready = evaluation.natural_ending_ready
        if deferred_delivery_claims:
            ending_ready = False
        ending_review_route = ending_route
        if not deferred_delivery_claims and _committed_natural_ending_review_redundant(
                runtime, current, turn, evaluation, ending_route, module_options):
            ending_review_route = None
            diagnostics["committed_ending_review_skips"] += 1
            trace_event("story_evidence.ending_review_skipped", route_id=ending_route["id"],
                        reason="committed_completion_with_delivery_review")
        if narrow_review or ending_route is not None:
            if claims or ending_review_route is not None or pending_offer or review_progress:
                review = await review_completion_evidence(
                    claims, ending_route=ending_review_route, pending_offer=pending_offer,
                    pending_offer_revision=pending_offer_revision, review_progress=review_progress,
                )
                progress_direction = review.direction
                current_scene_task = review.current_scene_task
                approved_keys = {claim["key"] for index, claim in enumerate(claims) if index in review.approved_indexes}
                if review_progress:
                    trace_event("story_evidence.progress_reviewed", direction=progress_direction)
                if ending_review_route is not None:
                    ending_ready = review.ending_ready
                    trace_event("story_evidence.ending_reviewed", route_id=ending_route["id"], approved=ending_ready)
                if pending_offer:
                    trace_event("story_evidence.offer_reviewed", decision=review.offer_decision,
                                quote=review.offer_quote, reason=review.offer_reason, origin_revision=pending_offer_revision)
                    if ending_route is not None:
                        ending_ready = ending_ready and review.offer_decision == "accept"
                    if review.offer_decision == "accept" and (ending_route is None or ending_ready):
                        evaluation = replace(evaluation,
                            transition_intent="initiate" if unmarked_offer else "accept",
                            public_destination_quote=unmarked_offer or evaluation.public_destination_quote,
                            transition_reply_target="pending_transition", exit_mismatch_reason="")
                    elif review.offer_decision == "reject":
                        evaluation = replace(evaluation, transition_intent="reject", natural_ending_ready=False)
                        ending_ready = False
                    elif review.offer_decision == "obsolete" and pending_offer_revision is not None:
                        obsolete_offer = True
                        evaluation = replace(evaluation, transition_intent="unclear", natural_ending_ready=False,
                            exit_mismatch_reason="")
                        ending_ready = False
                    elif evaluation.transition_intent == "accept":
                        evaluation = replace(evaluation, transition_intent="unclear",
                            exit_mismatch_reason="玩家本轮尚未明确授权执行原邀约，先承接其当前行动。")
                        trace_event("story_evidence.acceptance_rejected")
        elif claims:
            started_at = time.monotonic()
            try:
                remaining = min(
                    NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS,
                    NUMERIC_V2_REVIEW_BUDGET_SECONDS - diagnostics["timings_ms"]["transition_judge_work"] / 1000.0,
                    model_deadline - time.monotonic() - 0.5,
                )
                if remaining <= 0:
                    diagnostics["review_budget_skips"] += 1
                    raise NumericV2EvaluatorError("numeric_v2_transition_judge_timeout")
                diagnostics["completion_fact_review_calls"] += 1
                diagnostics["transition_judge_calls"] += 1
                async with asyncio.timeout(remaining):
                    review = await evaluator.validate_transition_offer(
                        engine=runtime.engine, session=current.session, message=turn.message,
                        actor_performance={}, scene_complete=evaluation.scene_complete,
                        player_action_projection=project_player_action_result(turn.message),
                        evaluator_fact_claims=claims, completion_fact_review_only=True, timeout_seconds=remaining,
                    )
                # Only original indices grant authority; Review cannot create a
                # value or use a not-yet-generated Actor draft as supporting proof.
                approved_keys = {
                    claim["key"] for index, claim in enumerate(claims)
                    if index in review.approved_evaluator_fact_indexes
                }
            except (TimeoutError, NumericV2EvaluatorError) as exc:
                # Match Evaluator degradation: legal replies and independent
                # facts continue, while unknown completion remains unrecorded.
                # Do not guess true/false, alter old prose or retry this batch.
                diagnostics["transition_judge_degraded"] = True
                trace_event("evaluator.completion_claims_review_failed", error_code=str(exc))
                logger.warning("Numeric v2 completion approval degraded: session_id=%s revision=%s reason=%s",
                               current.session.session_id, current.session.revision, str(exc))
            finally:
                _add_elapsed_ms(diagnostics, "completion_fact_review_work", started_at)
                _add_elapsed_ms(diagnostics, "transition_judge_work", started_at)
        # Real history may still be only a question, offer, or partial delivery.
        # Failed preliminary proof waits for the actual current source response.
        rejected_delivery = tuple(claim for claim in pre_review_delivery_claims if claim["key"] not in approved_keys)
        if rejected_delivery:
            deferred_delivery_claims = (*deferred_delivery_claims, *rejected_delivery)
            ending_ready = False
            trace_event("evaluator.source_delivery_deferred", keys=sorted(claim["key"] for claim in rejected_delivery))
        diagnostics["evaluator_fact_claims_approved"] += len(approved_keys)
        # 向演员交接同一批获准原值与证据；不把全部 prepare 暂存操作设成已完成。
        # 只读投影随本轮销毁，不提前落盘，也不改变原始历史。
        approved_completion_facts = tuple(claim for claim in claims if claim["key"] in approved_keys)
        trace_event("evaluator.completion_claims_reviewed", proposed=len(claims), approved_keys=sorted(approved_keys),
                    missing_audit_keys=sorted(review_keys - set(audits)))
        # A missing audit cannot bypass approval. Omit only unapproved targets;
        # do not turn refusal, incomplete execution or uncertainty into false.
        unapproved_completion = (review_keys - approved_keys) & {
            requirement["key"] for requirement in source_completion.get("all", ()) if "key" in requirement
        }
        return replace(evaluation,
            natural_ending_ready=ending_ready and not unapproved_completion,
            fact_operations=tuple(operation for operation in evaluation.fact_operations
                                  if operation["key"] not in review_keys or operation["key"] in approved_keys),
            fact_audit=tuple(audits[item["key"]] for item in evaluation.fact_audit
                             if item["key"] not in review_keys or item["key"] in approved_keys))

    def prepare_turn(evaluation: NumericV2EvaluationResult) -> TurnOutcomeV2:
        """Perform deterministic settlement and accumulate synchronous Runtime duration."""  # noqa: DOCSTRING_CJK

        started_at = time.monotonic()
        try:
            intent = evaluation.transition_intent
            if (runtime.engine.node_has_completion_advance(source_node)
                    and turn.input_source in {"freeform", "suggestion"}
                    and is_explicit_transition_deferral(turn.message)):
                intent = "reject"
            prepared = runtime.prepare_turn(
                current,
                turn,
                evaluation.metric_changes,
                scene_complete=evaluation.scene_complete,
                transition_intent=intent,
                initiation_quote=evaluation.public_destination_quote if intent == "initiate" else None,
                # Same evaluation supplies ending-readiness signal; default/degrade false without another confirmation or model call.
                natural_ending_ready=getattr(evaluation, "natural_ending_ready", False),
                # Valid fields/provenance do not prove semantics. Stage proposals during review, never let
                # Actor treat them as committed early or Review skip facts as already satisfied.
                fact_operations=() if module_options.get("review") else evaluation.fact_operations,
                # Conditional fixed narration triggers only through review; with review disabled it must not lock exits.
                condition_narrations_enabled=bool(module_options.get("review")),
            )
            if obsolete_offer and prepared.route is None:
                prepared, _ = runtime.engine.finalize_transition_offer_state(
                    prepared, {}, new_offer=False, invalidate_previous_offer=True,
                )
            trace_event("runtime.prepared", evaluation=evaluation, state=trace_state(prepared.session),
                        route=prepared.route, departure_blocker=prepared.departure_blocker,
                        ledger_event=prepared.ledger_event)
            return prepared
        finally:
            _add_elapsed_ms(diagnostics, "runtime_prepare_work", started_at)

    actor_publishes_transition_offer = False
    actor_draft_route = None

    async def generate_actor_turn(
        outcome: TurnOutcomeV2,
        *,
        retry_hint: str = "",
        completion_repair: bool = False,
        draft_repair_context: Mapping[str, Any] | None = None,
        departure_repair: bool = False,
        combined_evaluation: bool = False,
        transition_source: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Generate Actor narrative through official path, guiding pacing only by soft hints within same call."""  # noqa: DOCSTRING_CJK

        nonlocal final_fixed_review, actor_publishes_transition_offer, actor_draft_route
        final_fixed_review = None
        started_at = time.monotonic()
        try:
            generation_kwargs = {
                "engine": runtime.engine,
                "session": current.session,
                "outcome": outcome,
                "player_input": turn.message,
                "character_profile": generation_profile,
                "input_source": turn.input_source,
                # Locate original proposal with same committed Ledger as Evaluator, including all format/semantic retries.
                "recent_ledger_events": current.ledger_events,
                "diagnostics": diagnostics,
                # A completion repair gets one draft, without nested refill calls.
                # Recommendations belong to this Actor response; never add a separate completion call.
                "allow_suggestion_fill": False,
            }
            draft_provenance_sink: dict[str, Any] = {}
            if outcome.ledger_event["from_node_id"] != outcome.ledger_event["to_node_id"]:
                generation_kwargs["draft_provenance_sink"] = draft_provenance_sink
            if approved_completion_facts:
                generation_kwargs["approved_completion_facts"] = approved_completion_facts
            if (deferred_delivery_claims and runtime.engine.node_has_completion_advance(source_node)
                    and outcome.route is None and outcome.session.status == "active"
                    and outcome.departure_blocker in {None, "completion_pending"} and not departure_attempt_observed
                    and outcome.ledger_event.get("transition_intent") != "reject"
                    and turn.input_source in {"freeform", "suggestion"} and transition_source is None):
                # The claim may overstate an agreement as execution. Dispatch the
                # author's pending result, never the unapproved action/result.
                pending_results = [
                    {field: target[field] for field in ("key", "value", "description")}
                    for target in _pending_completion_facts(runtime.engine, current.session)
                    if runtime.engine.fact_contract[target["key"]].get("visibility") == "public"
                    and any(claim["key"] == target["key"]
                            and type(claim["value"]) is type(target["value"])
                            and claim["value"] == target["value"] for claim in deferred_delivery_claims)
                ]
                if pending_results:
                    generation_kwargs["source_delivery_task"] = {"pending_results": pending_results,
                        **({"kind": "shared_agreement"} if shared_requirement is not None
                           and all(row["key"] == shared_requirement["key"] for row in pending_results) else {})}
            if transition_source is not None:
                generation_kwargs["transition_source"] = transition_source
            if departure_attempt_observed:
                generation_kwargs["departure_attempt_observed"] = True
            if outcome.route is None and outcome.session.status == "active" and unconfirmed_departure_quote:
                generation_kwargs["unconfirmed_departure_quote"] = unconfirmed_departure_quote
            if combined_evaluation:
                generation_kwargs["combined_evaluation"] = True
                if module_options.get("history_lookup"):
                    generation_kwargs["combined_history_lookup"] = True
            if module_options.get("review"):
                generation_kwargs["defer_dialogue_repetition"] = True
            if draft_repair_context is not None:
                # 只传未提交的编辑材料；不进入历史、预审结果或Runtime事实。
                generation_kwargs["draft_repair_context"] = draft_repair_context
            if history_lookup_result is not None:
                generation_kwargs["history_lookup"] = history_lookup_result
            if not departure_repair and wrong_exit_offer_quote and outcome.route is None and outcome.session.status == "active":
                generation_kwargs["wrong_exit_offer_quote"] = wrong_exit_offer_quote
            if not departure_repair and exit_mismatch_reason and outcome.route is None and outcome.session.status == "active":
                generation_kwargs["exit_mismatch_reason"] = exit_mismatch_reason
            if (progress_direction and outcome.route is None and outcome.session.status == "active"
                    and (outcome.departure_blocker is None or departure_direction_reviewed)):
                generation_kwargs["progress_direction"] = progress_direction
                if current_scene_task:
                    generation_kwargs["current_scene_task"] = current_scene_task
            remaining = model_deadline - time.monotonic() - required_review_reserve
            if remaining <= 0:
                raise NumericV2ActorError("numeric_v2_actor_timeout")
            try:
                async with asyncio.timeout(remaining):
                    generated = await _generate_actor_turn_with_output_retry(
                        actor,
                        **generation_kwargs,
                        retry_hint=retry_hint,
                        generation_deadline=model_deadline - required_review_reserve,
                        blocked_player_handoffs=blocked_handoffs,
                        # Ordinary validation and semantic repair spend the same turn allowance.
                        remaining_attempts=NUMERIC_V2_MAX_ACTOR_DRAFTS_PER_TURN - diagnostics["actor_generation_attempts"],
                        allow_output_retry=bool(module_options.get("actor_retry")) and not completion_repair
                            and not diagnostics["semantic_rewrite_attempts"],
                        allow_transition_repeat_retry=(
                            outcome.ledger_event.get("from_node_id")
                            != outcome.ledger_event.get("to_node_id")
                            and (
                                # Runtime has already selected and authorized this route;
                                # initiation and acceptance use the same replay repair.
                                str(outcome.ledger_event.get("transition_intent") or "") in {"accept", "initiate"}
                                or outcome.session.status == "ended"
                            )
                        ),
                    )
            except asyncio.TimeoutError as exc:
                trace_event("actor.wait_budget_exhausted")
                raise NumericV2ActorError("numeric_v2_actor_timeout") from exc
            actor_publishes_transition_offer = generated.pop("_transition_offer_provided", False) is True
            fingerprint = performance_fingerprint(generated)
            actor_draft_provenance.pop(fingerprint, None)
            if draft_provenance_sink.get("body_fingerprint") == fingerprint:
                actor_draft_provenance[fingerprint] = deepcopy(draft_provenance_sink)
            actor_draft_route = runtime.engine.preview_route(
                outcome.session.current_node_id, outcome.session.metrics, facts=outcome.session.story_state["facts"],
            ) if outcome.ledger_event["from_node_id"] == outcome.ledger_event["to_node_id"] else None
            return generated
        finally:
            # Actor retries only on format, repetition, or explicit boundaries; record cumulative call latency and actual supplier request count.
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

    def apply_deterministic_suggestion_filter(candidate: Mapping[str, Any]) -> dict[str, Any]:
        """Apply zero-call recommendation prescreen and record reasons in turn diagnostics."""  # noqa: DOCSTRING_CJK

        filtered, removed, reasons = _prefilter_suggestion_candidates(candidate)
        if removed:
            diagnostics["deterministic_suggestions_removed"] += removed
            reason_counts = diagnostics["deterministic_suggestion_filter_reasons"]
            for reason in reasons:
                reason_counts[reason] = int(reason_counts.get(reason, 0)) + 1
            # Actor returns mutable dict; in-place updates expose same candidate to every subsequent review path.
            if isinstance(candidate, dict):
                candidate.clear()
                candidate.update(filtered)
                return candidate
        return filtered

    review_call_count = 0

    def review_budget_exhausted() -> bool:
        """Whether used review time reached turn limit; read accumulated diagnostics without extra model calls."""  # noqa: DOCSTRING_CJK

        return (
            float(diagnostics["timings_ms"].get("transition_judge_work", 0.0))
            >= NUMERIC_V2_REVIEW_BUDGET_SECONDS * 1000.0
        )

    def review_budget_effectively_exhausted() -> bool:
        """Treat the small timeout cushion as spent so a timed-out dispute cannot trigger another Actor call."""

        used_ms = float(diagnostics["timings_ms"].get("transition_judge_work", 0.0))
        return used_ms >= max(0.0, NUMERIC_V2_REVIEW_BUDGET_SECONDS - 0.5) * 1000.0

    async def review_transition_offer(
        candidate: dict[str, Any],
        *,
        defer_dispute_for_rewrite: bool = False,
        defer_offer_only_dispute: bool = False,
    ) -> NumericV2TransitionOfferReview:
        """Review visible proposals and accumulate costs; model failure retains conservative revocation semantics."""  # noqa: DOCSTRING_CJK

        nonlocal final_fixed_review, review_call_count
        final_fixed_review = None
        # Merge author buttons into same pending candidate first; preparation only, delivery still depends on current review.
        acceptance_inserted = False
        acceptance_preserved = False
        authored_acceptance = ""
        if outcome.ledger_event["from_node_id"] == outcome.ledger_event["to_node_id"]:
            route = runtime.engine.preview_route(outcome.session.current_node_id, outcome.session.metrics,
                                                 facts=outcome.session.story_state["facts"])
            contract = route.get("transition_contract") if isinstance(route, Mapping) else None
            if candidate.get("transition_offered") is True and isinstance(contract, Mapping):
                authored_acceptance = _project_authored_transition_text(
                    runtime.engine, outcome.session, str(contract.get("accept_input") or ""))
                prepared, acceptance_inserted = _insert_verified_offer_acceptance_suggestion(
                    candidate,
                    accept_input=authored_acceptance,
                    consumed_inputs=(turn.message,),
                )
                candidate.update(prepared)
            prepared, acceptance_preserved = _preserve_pending_acceptance_suggestion(
                candidate, current=current, player_input=turn.message,
                keep_pending=(current.session.transition_offered
                    and outcome.session.transition_offered and not invalidate_previous_offer
                    and candidate.get("transition_offered") is not True),
            )
            candidate.update(prepared)
        # Zero-call fact prescreen before model review avoids review wait on obvious future outcomes.
        candidate.update(apply_deterministic_suggestion_filter(candidate))
        if review_call_count and review_budget_exhausted():
            # Current candidate may be rewritten; prior draft's verdict cannot approve it for history.
            diagnostics["review_budget_skips"] += 1
            diagnostics["review_timeout_aborted"] = True
            trace_event("review.budget_exhausted", phase=(
                "transition" if outcome.ledger_event["from_node_id"] != outcome.ledger_event["to_node_id"]
                else "ordinary"
            ))
            raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
        review_call_count += 1
        transition_judge_started_at = time.monotonic()
        diagnostics["transition_judge_calls"] += 1
        try:
            prior_review_seconds = float(
                diagnostics["timings_ms"].get("transition_judge_work", 0.0)
            ) / 1000.0

            def remaining_review_seconds() -> float:
                """Return the budget left, including the current in-flight review call."""

                return min(
                    NUMERIC_V2_REVIEW_BUDGET_SECONDS - prior_review_seconds - (
                        time.monotonic() - transition_judge_started_at
                    ),
                    model_deadline - time.monotonic() - 0.5,
                )

            # With Actor-generated transition narration, review entire segment from source history; ordinary turns retain evidence/judgment.
            changed = outcome.ledger_event["from_node_id"] != outcome.ledger_event["to_node_id"]
            confirmed_acceptance = bool(
                changed and confirmed_acceptance_route_id
                and outcome.ledger_event.get("transition_intent") == "accept"
                and (outcome.route or {}).get("id") == confirmed_acceptance_route_id
                and outcome.ledger_event.get("accepted_offer_route_id") == confirmed_acceptance_route_id
            )
            review_kwargs = dict(
                engine=runtime.engine,
                # Ordinary drafts still review committed history; candidate's incremented turn number would omit first opening and falsely report missing history.
                # Restore only history watermark, preserving current values, address knowledge, and invitation state for exit/boundary review.
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
            if confirmed_acceptance:
                review_kwargs["confirmed_acceptance"] = True
                silent_receipt = _pending_silent_receipt(current)
                if silent_receipt is not None:
                    review_kwargs["confirmed_silent_acceptance"] = silent_receipt["accept_input"]
            if evaluator_fact_claims:
                review_kwargs["evaluator_fact_claims"] = evaluator_fact_claims
            if history_lookup_result is not None:
                review_kwargs["history_lookup"] = history_lookup_result
            if not changed and (diagnostics["transition_cancellations"] or invalidate_previous_offer):
                review_kwargs["cancelled_transition"] = True
                review_kwargs["invalidated_invitation"] = invalidate_previous_offer
            # Quick check, dispute, and post-rewrite review reuse verified originals rather than inferring public facts from author direction.
            if changed and outcome.ledger_event.get("transition_intent") == "initiate":
                review_kwargs["public_destination_quote"] = evaluation.public_destination_quote
            # Supplement only unrecognized ordinary proactive requests; exclude rejection, pending invitations, opening, and official transition.
            if (not changed and evaluation.transition_intent == "unclear"
                    and not current.session.transition_offered
                    and not diagnostics["missed_initiation_recoveries"]
                    and not diagnostics["transition_cancellations"]):
                review_kwargs["check_missed_initiation"] = True
            first_call_budget = remaining_review_seconds()
            # Always run first quick check, even with test budget 0; only later calls may be skipped by budget.
            if (model_deadline - time.monotonic() <= 0.55
                    or (review_call_count > 1 and first_call_budget <= 0.05)):
                diagnostics["review_budget_skips"] += 1
                diagnostics["review_timeout_aborted"] = True
                trace_event("review.budget_exhausted", phase="transition" if changed else "ordinary")
                raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
            review_kwargs["timeout_seconds"] = max(0.05, (
                model_deadline - time.monotonic() - 0.5
                if review_call_count == 1 and not prior_review_seconds
                else first_call_budget
            ))
            review = await _review_with_deadline(
                evaluator,  **review_kwargs,
            )
            player_action_projection = normalize_player_action_projection(
                outcome.ledger_event.get("player_action_projection")
            )

            def apply_confirmed_acceptance(
                result: NumericV2TransitionOfferReview,
            ) -> NumericV2TransitionOfferReview:
                """Derive acceptance for a verbatim authored click from the invitation verdict.

                Fast and dispute verdicts both pass through here. The click only proves the
                player accepted the invitation just shown: it neither proves that invitation
                valid (an author fallback offer included) nor exempts the candidate body. An
                explicit ``pending_invitation_invalid=True`` therefore withdraws the acceptance,
                while body-only errors keep it and go to the rewrite path.
                """

                if not confirmed_acceptance:
                    return result
                # Strict click on current invitation confirms acceptance only; invitation validity and correct narrative scene still need independent review.
                return replace(result, acceptance_authorized=result.pending_invitation_invalid is False)

            def correct_ordinary_review(
                result: NumericV2TransitionOfferReview,
            ) -> NumericV2TransitionOfferReview:
                """Apply the deterministic ordinary-turn corrections to any review verdict.

                Fast and dispute verdicts both pass through here, so an independent
                recheck cannot reintroduce a flag these zero-call checks already refuted.
                """

                if not changed and _review_denies_narration_only_offer(candidate, result):
                    # Narration merely displaying an exit identifier is not a character invitation to change acts. If structured offer_kind
                    # denies invitation, clear only the contradictory boolean, retaining narration and same-turn fact candidates.
                    diagnostics["narration_offer_flags_cleared"] += 1
                    trace_event(
                        "review.narration_offer_cleared",
                        offer_quote=result.offer_quote,
                        offer_kind=result.offer_kind,
                        failure_reason=result.failure_reason,
                    )
                    result = replace(
                        result,
                        offer_present=False,
                        valid=False,
                        offer_quote="",
                        failure_reason="",
                    )
                # 完成事实与邀请去向分别核对；共同词语不能推翻独立的邀请否决。
                # 无效邀请继续走现有争议/修稿/回退，不再由词片段相似度补认授权。
                if not changed and _review_mislabels_explicit_player_movement(result):
                    # Explicit player movement request authorizes only that move; infer no destination, create no act change, allow no additional actions here.
                    diagnostics["explicit_player_movement_flags_cleared"] += 1
                    trace_event(
                        "review.explicit_player_movement_cleared",
                        failure_reason=result.failure_reason,
                    )
                    result = replace(
                        result,
                        body_violations=(),
                        failure_reason="",
                    )
                return result

            def record_review(result: NumericV2TransitionOfferReview, mode: str) -> None:
                trace_event("review.result", mode=mode, phase="transition" if changed else "ordinary", result=result)
                # Retain both verdicts separately for diagnostics, outside story history; do not give initial reasons to independent recheck.
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
                    **({"body_issues": list(result.body_issues)} if result.body_issues else {}),
                    "missed_initiation": result.missed_initiation,
                    "initiation_authorized": result.initiation_authorized,
                    "acceptance_authorized": result.acceptance_authorized,
                    "pending_invitation_invalid": result.pending_invitation_invalid,
                    "delivery_matches_route": result.delivery_matches_route,
                    **({"fixed_narration_triggers": list(result.fixed_narration_triggers)}
                       if result.fixed_narration_triggers else {}),
                    **({"fact_candidates": list(result.fact_candidates)}
                       if result.fact_candidates else {}),
                })

            review = apply_confirmed_acceptance(review)
            record_review(review, "fast")
            review = correct_ordinary_review(review)
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
            retained_candidate, _ = _drop_reported_unsafe_suggestions(
                candidate, review.unsafe_suggestion_indexes, review=review,
            )
            local_scene_repair = (
                not changed and not current.session.transition_offered
                and not diagnostics["transition_cancellations"]
                and _safe_drop_invalid_scene_update(retained_candidate, review) is not None
            )
            high_confidence_body_violation = (
                local_scene_repair
                or projection_conflict
            )
            if high_confidence_body_violation:
                # Only program-verified conflict or safely trimmable location bypasses dispute; reason wording is not confidence evidence.
                diagnostics["dispute_review_skipped_high_confidence_body"] += 1
                trace_event("review.dispute_skipped", reason=(
                    "verified_scene_update_removal" if local_scene_repair else "high_confidence_body_violation"
                ))
            elif changed and review.offer_present and not review.valid and not review.body_violations:
                # Official transition's three parts already have Runtime-selected route and narrative review; invalid buttons require dropping recommendations,
                # not another costly dispute recheck.
                diagnostics["dispute_review_skipped_formal_offer"] += 1
                trace_event("review.dispute_skipped", reason="formal_offer_not_delivery_gate")
            elif (
                not review.body_violations
                and review.offer_present
                and not review.valid
                and review.unsafe_suggestion_indexes
            ):
                # Button cleanup leaves the narrative verdict unchanged, so it needs no second dispute call.
                diagnostics["dispute_review_skipped_unsafe_offer_buttons"] += 1
                trace_event("review.dispute_skipped", reason="unsafe_offer_buttons_only")
            elif (
                not review.body_violations
                and review.offer_present
                and not review.valid
                and review.offer_failure_code == "route_mismatch"
            ):
                # Exit contract determines direction; asking model again cannot change structured destination and only adds waiting.
                diagnostics["dispute_review_skipped_contract_offer"] += 1
                trace_event("review.dispute_skipped", reason="contract_offer_mismatch")
            elif (
                ((defer_dispute_for_rewrite
                    and not diagnostics["semantic_rewrite_attempts"]
                    and review.initiation_authorized is not False
                    and review.acceptance_authorized is not False
                ) or (
                    defer_offer_only_dispute and not changed
                    and not current.session.transition_offered and not review.body_violations
                ))
                and (review.body_violations or (
                    not changed and review.offer_present and not review.valid
                ))
            ):
                # Spend the existing rewrite/review allowance before optional dispute, including formal delivery.
                # Authorization denial cannot be repaired by changing prose; retain its independent recheck before cancellation.
                if not review.body_violations:
                    diagnostics["dispute_review_deferred_offer_repair"] += 1
                trace_event("review.dispute_deferred", reason="semantic_repair_first")
            elif dispute_review_enabled and not diagnostics["dispute_review_attempts"] and not review_budget_exhausted() and (
                review.body_violations or (review.offer_present and not review.valid and not changed)
            ):
                dispute_budget = remaining_review_seconds()
                if dispute_budget <= NUMERIC_V2_DISPUTE_TIMEOUT_CAP_SECONDS:
                    diagnostics["review_budget_skips"] += 1
                    trace_event("review.dispute_skipped", reason="insufficient_dispute_budget")
                else:
                    diagnostics["dispute_review_attempts"] += 1
                    diagnostics["transition_judge_calls"] += 1
                    try:
                        # Dispute recheck must use exactly the quick check's request and evidence (existing invariant);
                        # do not mark as rewrite recheck here; all judgments in current complete review protocol retain same evidence scope.
                        dispute_kwargs = dict(review_kwargs)
                        dispute_kwargs["dispute_review"] = True
                        dispute_kwargs["timeout_seconds"] = min(
                            NUMERIC_V2_DISPUTE_TIMEOUT_CAP_SECONDS,
                            max(0.05, dispute_budget - 0.05),
                        )
                        try:
                            # Packing and client cleanup consume the same remaining
                            # allowance as the request. Keep this conversion inside
                            # the existing degradation handler to retain the first verdict.
                            async with asyncio.timeout(dispute_kwargs["timeout_seconds"]):
                                reviewed = await evaluator.validate_transition_offer(**dispute_kwargs)
                        except TimeoutError as exc:
                            raise NumericV2EvaluatorError("numeric_v2_transition_judge_timeout") from exc
                    except NumericV2EvaluatorError as exc:
                        trace_event("review.failed", mode="dispute", error_code=str(exc))
                        # Separate from quick-review failure: ordinary-turn degradation must not erase existing violation evidence.
                        diagnostics["dispute_review_degraded"] = True
                        diagnostics["transition_review_results"].append({
                            "review_mode": "dispute", "degraded": True, "failure_reason": str(exc),
                        })
                    else:
                        reviewed = apply_confirmed_acceptance(reviewed)
                        record_review(reviewed, "dispute")
                        review = correct_ordinary_review(reviewed)
            if not changed:
                # Ordinary turns cannot portray target-act opening/bridge-exclusive time markers as current (issue 2.141 B3).
                # Deterministic check runs after model/dispute judgments, which cannot clear it.
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
            if (acceptance_inserted and not review.unsafe_suggestion_indexes
                    and not (review.offer_present and review.valid)
                    and not (current.session.transition_offered
                        and outcome.session.transition_offered and not invalidate_previous_offer)):
                # False Actor invitation flags must leave no provisional buttons; Runtime still retains old pending invitations.
                candidate["suggested_inputs"] = [
                    item for item in candidate.get("suggested_inputs") or []
                    if item != authored_acceptance
                ]
            if not review.unsafe_suggestion_indexes:
                if acceptance_inserted and review.offer_present and review.valid:
                    diagnostics["verified_offer_acceptance_suggestions_inserted"] += 1
                if acceptance_preserved:
                    diagnostics["pending_acceptance_suggestions_preserved"] += 1
            final_fixed_review = review
            return review
        except NumericV2EvaluatorError as exc:
            trace_event("review.failed", mode="fast", error_code=str(exc))
            diagnostics["transition_judge_degraded"] = True
            diagnostics["transition_review_results"].append({
                "degraded": True,
                "failure_reason": str(exc),
            })
            logger.warning(
                "Numeric v2 review failed; turn remains uncommitted: reason=%s session_id=%s revision=%s",
                str(exc),
                current.session.session_id,
                current.session.revision,
            )
            # Ordinary draft may already reach next location; incomplete review must not treat unknown as clean and pollute history.
            raise NumericV2ActorOutputError("numeric_v2_transition_review_failed") from exc
        finally:
            _add_elapsed_ms(
                diagnostics,
                "transition_judge_work",
                transition_judge_started_at,
            )

    confirmed_acceptance_route_id = _confirmed_authored_acceptance(
        runtime.engine, current, turn,
        require_program_invitation=not module_options.get("review") or not module_options.get("evaluator"),
    )
    combined_performance = None
    if not progress_review_due and _can_combine_ordinary_turn(runtime, current, turn, module_options):
        # Draft only in the current scene. No proposal can authorize a move or ending;
        # reuse the same body after deterministic metric settlement, without another call.
        provisional = prepare_turn(NumericV2EvaluationResult((), False))
        shared_source_combined = shared_requirement is not None and runtime.engine.node_has_completion_advance(source_node)
        if shared_source_combined:
            defer_shared_delivery(NumericV2EvaluationResult((), False))
        combined_performance = await generate_actor_turn(provisional, combined_evaluation=True)
        proposed = combined_performance.pop("_turn_evaluation")
        visible_history = combined_performance.pop("_turn_visible_history", [])
        visible_player_inputs = combined_performance.pop("_turn_visible_player_inputs", [])
        historical_route_evidence = _committed_player_route_evidence(
            runtime.engine, current.session, visible_player_inputs, message=turn.message,
        )
        # The combined response has two fact buckets. Route a misplaced quote
        # from this player's input through the existing Evaluator validator;
        # never pretend it was spoken/performed by the Actor or grant a move.
        proposed_candidates = list(proposed.get("fact_candidates", []))
        actor_candidates = []
        for candidate in combined_performance.get("fact_candidates", []):
            quote = candidate.get("evidence_quote")
            if isinstance(quote, str) and quote.strip() and quote in turn.message:
                if candidate not in proposed_candidates:
                    proposed_candidates.append(candidate)
            else:
                actor_candidates.append(candidate)
        combined_performance["fact_candidates"] = actor_candidates
        # Reuse the original Evaluator's current-visit evidence permissions. Quotes
        # must also occur in this Actor request's retained history, never author plans.
        _, committed_evidence = _committed_completion_evidence(runtime.engine, current.session)
        candidates = []
        for candidate in proposed_candidates:
            try:
                normalized, = _normalize_actor_fact_candidates(
                    [candidate], fact_contract=runtime.engine.fact_contract,
                    allowed_keys={item["key"] for item in _pending_completion_facts(runtime.engine, current.session)})
            except NumericV2RuntimeError:
                continue
            evidence = normalized["evidence"][0]
            if evidence["quote"] in turn.message:
                evidence["source"] = "player_input"
            elif evidence["quote"] in historical_route_evidence.get(normalized["key"], ()):
                evidence["source"] = "committed_player_input"
            elif not any(evidence["quote"] in text for text in visible_history):
                # A new performance quote may be misplaced in turn_evaluation.
                # Use the same visible source as the final Actor fact validator.
                # A quote can span adjacent action and dialogue in that source.
                if evidence["quote"] in _actor_fact_evidence_text(combined_performance):
                    if candidate not in actor_candidates:
                        actor_candidates.append(candidate)
                continue
            candidates.append(normalized)
        proposed["fact_candidates"] = candidates
        try:
            evaluation = _parse_evaluation_output(
                json.dumps(proposed), runtime.engine, turn.message, current.session, current.ledger_events,
                committed_evidence=committed_evidence,
                committed_player_route_evidence=historical_route_evidence)
        except NumericV2EvaluatorError:
            # Match the existing Evaluator's conservative failure behavior, preserving valid dialogue.
            evaluation = NumericV2EvaluationResult((), False)
            diagnostics["evaluator_degraded"] = True
        if shared_source_combined:
            key = shared_requirement["key"]
            if (diagnostics["evaluator_degraded"] or evaluation.transition_intent == "reject"
                    or any(operation["key"] == key and operation["value"] is not True
                           for operation in evaluation.fact_operations)):
                deferred_delivery_claims = ()
            # The same draft cannot approve its own agreement or prepare entry.
            # Only independent C may approve this prebound target from both originals.
            evaluation = replace(evaluation, natural_ending_ready=False,
                fact_operations=tuple(operation for operation in evaluation.fact_operations
                                      if operation["key"] != key or operation["value"] is not True),
                fact_audit=tuple(item for item in evaluation.fact_audit if not any(
                    operation["key"] == item["key"] == key and operation["value"] is True
                    for operation in evaluation.fact_operations)))
        evaluation = await approve_evaluator_progress(respect_player_handoffs(evaluation), confirmed_acceptance_route_id)
        diagnostics["ordinary_turn_combined"] = True
        trace_event("evaluator.combined", result=evaluation)
    else:
        evaluation = await approve_evaluator_progress(
            respect_player_handoffs(await evaluate_turn()), confirmed_acceptance_route_id,
        )
    if diagnostics["evaluator_degraded"]:
        confirmed_acceptance_route_id = _confirmed_authored_acceptance(
            runtime.engine, current, turn, require_program_invitation=True)
    acceptance_route = runtime.engine.preview_route(current.session.current_node_id, current.session.metrics,
                                                    facts=current.session.story_state["facts"])
    acceptance_contract = acceptance_route.get("transition_contract") if isinstance(acceptance_route, Mapping) else None
    acceptance_target = runtime.engine.nodes.get(str(acceptance_route.get("target_node_id") or "")) if acceptance_route else None
    acceptance_origin = pending_transition_record(
        current.session, ledger_events=current.ledger_events, include_withdrawn=True,
    )
    reviewed_offer_evidence = isinstance(acceptance_origin, Mapping) and any(
        block.get("type") == "dialogue" and str(block.get("text") or "").strip()
        for block in performance_content_blocks(acceptance_origin)
    )
    if isinstance(acceptance_origin, Mapping):
        offer_source = acceptance_origin.get("transition_offer_source")
        if offer_source is not None:
            reviewed_offer_evidence = offer_source == "reviewed"
        else:
            # Legacy records have no provenance marker; a deterministic button is not speech evidence.
            reviewed_offer_evidence = reviewed_offer_evidence and not any(
                isinstance(item, str) and "我同意前往" in item and "，安排是：" in item
                for item in acceptance_origin.get("suggested_inputs") or []
            )
    silent_acceptance_required = (
        _silent_exit_allowed(runtime.engine.nodes[current.session.current_node_id])
        and isinstance(acceptance_contract, Mapping)
        and acceptance_contract.get("advance_on_completion") is not True
        and not str(acceptance_contract.get("fallback_offer") or "").strip()
        and isinstance(acceptance_target, Mapping)
        and acceptance_target.get("type") != "ending" and acceptance_target.get("terminal") is not True
        # Reviewed dialogue, narration and action offers retain ordinary acceptance.
        and not reviewed_offer_evidence
    )
    # 默认模式仅承接最新已复核邀请下、本轮点选的已展示原按钮；旧记录不能按对白猜成审批。
    # 这只保留 Evaluator 的接受意图，Runtime 仍核对原邀请路线及本轮路线条件。
    fresh_reviewed_offer = (
        module_options.get("evaluator") and current.session.transition_offered
        and turn.input_source == "suggestion"
        and isinstance(acceptance_origin, Mapping)
        and acceptance_origin.get("transition_offer_source") == "reviewed"
        and acceptance_origin.get("revision") == current.session.revision
        and turn.message.strip() in (acceptance_origin.get("suggested_inputs") or [])
    )
    if ((numeric_v2_options.MODEL_REVIEWS_ENABLED and not module_options.get("review") and not fresh_reviewed_offer)
            or diagnostics["evaluator_degraded"]
            or silent_acceptance_required
            or _pending_silent_receipt(current, include_withdrawn=True) is not None):
        if evaluation.transition_intent == "accept" and not confirmed_acceptance_route_id:
            evaluation = replace(evaluation, transition_intent="unclear")
    if confirmed_acceptance_route_id:
        # Exact button selection no longer loses to upstream unclear; retain numeric, fact, and Runtime route checks.
        evaluation = replace(evaluation, transition_intent="accept", transition_reply_target="pending_transition")
        trace_event("transition.authored_acceptance_confirmed", route_id=confirmed_acceptance_route_id)
    fact_audit_by_key = {item["key"]: item for item in evaluation.fact_audit}
    independently_approved_facts = approved_completion_facts
    if not numeric_v2_options.MODEL_REVIEWS_ENABLED and not module_options.get("review"):
        # These facts already participate in this turn's deterministic routing.
        # Hand off only checked public completion/route evidence, without giving
        # it the provenance of an independent review or an earlier commit.
        public_targets = {
            target["key"] for target in runtime.engine.scene_fact_targets(current.session.current_node_id)
            if runtime.engine.fact_contract[target["key"]]["visibility"] == "public"
        }
        approved_completion_facts = tuple(
            {**operation, **fact_audit_by_key[operation["key"]], "validated_only": True,
             "evidence": [
                 {"source": "player_input", "quote_ref": "player_input"}
                 if item["source"] == "player_input" and item["quote"] == turn.message else item
                 for item in fact_audit_by_key[operation["key"]]["evidence"]
             ]}
            for operation in evaluation.fact_operations
            if operation["key"] in public_targets and operation["key"] in fact_audit_by_key
        )
    evaluator_fact_claims = tuple(
        {
            **operation,
            **fact_audit_by_key[operation["key"]],
            "description": runtime.engine.fact_contract[operation["key"]].get("description", ""),
        }
        for operation in evaluation.fact_operations
        if module_options.get("review") and operation["key"] in fact_audit_by_key
    )
    diagnostics["evaluator_fact_claims_deferred"] += len(evaluator_fact_claims)
    if evaluation.history_query and module_options.get("history_lookup"):
        # No extra ordinary-turn calls; fetch full record once only for evidence gaps, sharing failures too to prevent repeated rewrite lookups.
        lookup_started_at = time.monotonic()
        history_lookup_result = await lookup_history(config_manager, current.session, evaluation.history_query)
        trace_event("history.result", query=evaluation.history_query, result=history_lookup_result)
        diagnostics["history_lookup"] = {key: value for key, value in history_lookup_result.items() if key != "evidence"}
        # The provisional draft is still safe to use if lookup found nothing.
        # When it finds evidence, answer using that evidence before committing.
        if history_lookup_result.get("evidence"):
            combined_performance = None
        _add_elapsed_ms(diagnostics, "history_lookup_work", lookup_started_at)
    # Preserve model reasoning for misjudgment diagnosis, without passing to Actor/history or changing ending conditions.
    diagnostics["ending_reason"] = evaluation.ending_reason
    outcome = prepare_turn(evaluation)
    departure_attempt_observed = outcome.ledger_event.get("transition_intent") != "reject" and (
        evaluation.departure_attempt_observed or evaluation.transition_intent in {"initiate", "accept"}
        or (outcome.ledger_event.get("player_action_projection") or {}).get("player_left_current_scene") is True
    )
    # Freeze proof permissions from this blocked source stage, before later continuation can authorize a route.
    completion_source_restricted = bool(departure_attempt_observed and outcome.route is None
                                        and outcome.session.status == "active")
    if (departure_attempt_observed and evaluation.departure_attempt_quote
            and evaluation.departure_attempt_quote in turn.message):
        unconfirmed_departure_quote = evaluation.departure_attempt_quote
    departure_direction_reviewed = bool(
        outcome.route is None and outcome.session.status == "active"
        and (outcome.departure_blocker or unconfirmed_departure_quote)
        and departure_attempt_observed and module_options.get("evaluator"))
    if departure_direction_reviewed:
        guide = await review_completion_evidence((), review_progress=True, blocked_departure=True)
        progress_direction = guide.direction
        current_scene_task = guide.current_scene_task
        trace_event("story_evidence.blocked_departure_direction", direction=progress_direction)
    exit_mismatch_reason = evaluation.exit_mismatch_reason
    if (evaluation.wrong_exit_offer_quote and evaluation.transition_intent == "unclear"
            and acceptance_route is not None and acceptance_target is not None
            and acceptance_target.get("type") != "ending" and acceptance_target.get("terminal") is not True
            and outcome.route is None and outcome.session.status == "active"):
        settled_route = runtime.engine.preview_route(
            current.session.current_node_id, outcome.session.metrics, facts=outcome.session.story_state["facts"],
        )
        if settled_route is not None and settled_route["id"] == acceptance_route["id"]:
            wrong_exit_offer_quote = evaluation.wrong_exit_offer_quote
            trace_event("transition.wrong_exit_correction_context", route_id=settled_route["id"])
    invalidate_previous_offer = (
        outcome.ledger_event.get("transition_offer_invalidated") is True
        or (outcome.route is None and runtime.engine.has_placeholder_authored_invitation(current))
    )
    route_changed = (
        outcome.ledger_event["from_node_id"]
        != outcome.ledger_event["to_node_id"]
    )
    reviewed_transition_offered = False
    approved_recovery_fallback: tuple[
        TurnOutcomeV2, dict[str, Any], NumericV2TransitionOfferReview,
        NumericV2EvaluationResult,
    ] | None = None
    authored_recovery_receipt: tuple[TurnOutcomeV2, dict[str, Any]] | None = None
    authored_invitation_recovered = False
    cited_dialogue_recovered = False
    recovery_author_original_conflict = False

    def is_authored_recovery(candidate: Mapping[str, Any]) -> bool:
        # Private receipt covers every field, including suggestions and facts;
        # an Actor-controlled flag or a body-only fingerprint is insufficient.
        return bool(authored_recovery_receipt is not None
                    and outcome == authored_recovery_receipt[0]
                    and candidate == authored_recovery_receipt[1])

    def recover_authored_transition(reason: str) -> dict[str, Any] | None:
        nonlocal authored_recovery_receipt, final_fixed_review, actor_publishes_transition_offer
        nonlocal actor_draft_route, reviewed_transition_offered, outcome
        nonlocal authored_invitation_recovered, program_invitation_performance
        if (authored_recovery_receipt is not None or recovery_author_original_conflict
                or diagnostics.get("contract_check_calls", 0) >= 2):
            return None
        recovered = _authored_transition_recovery(runtime.engine, current, outcome,
            reviewed_source=reviewed_transition_source)
        if recovered is None:
            invitation = (_authored_invitation_recovery(runtime, current, outcome, player_input=turn.message)
                          if reviewed_transition_source is None else None)
            if invitation is None:
                return None
            outcome, recovered, contract = invitation
            authored_invitation_recovered = True
            program_invitation_performance = contract["offer"]
        authored_recovery_receipt = (deepcopy(outcome), deepcopy(recovered))
        final_fixed_review = None
        actor_publishes_transition_offer = reviewed_transition_offered = authored_invitation_recovered
        actor_draft_route = (runtime.engine.preview_route(outcome.session.current_node_id, outcome.session.metrics,
            facts=outcome.session.story_state["facts"]) if authored_invitation_recovered else None)
        diagnostics["authored_invitation_recoveries" if authored_invitation_recovered else "authored_transition_recoveries"] += 1
        trace_event("transition.authored_delivery_recovered", reason=reason,
                    route_id=(actor_draft_route or outcome.route)["id"], target_node_id=outcome.session.current_node_id,
                    invitation_only=authored_invitation_recovered)
        return recovered

    async def generate_transition_or_recover(**kwargs: Any) -> dict[str, Any]:
        try:
            return await generate_actor_turn(outcome, **kwargs)
        except NumericV2ActorError as exc:
            recovered = recover_authored_transition(str(exc))
            if recovered is None:
                raise
            return recovered

    performance = combined_performance if combined_performance is not None else await generate_transition_or_recover()
    performance = apply_deterministic_suggestion_filter(performance)

    def pending_source_delivery_batch(candidate):
        return bind_source_delivery(runtime.engine, deferred_delivery_claims,
            completion_packet(candidate), restrict_player_results=completion_source_restricted)

    def reviewed_source_completion(candidate):
        receipt = source_completion_reviews.get(performance_fingerprint(candidate))
        if receipt is None:
            return None
        batch, indexes = receipt
        fresh = pending_source_delivery_batch(candidate)
        if (not completion_evidence_matches(batch, fresh.packet)
                or batch.claims != fresh.claims or batch.bindings != fresh.bindings):
            return None
        return batch, indexes

    async def verify_contract_boundaries(**kwargs: Any) -> tuple[str, ...]:
        remaining = model_deadline - time.monotonic() - 0.5
        if remaining <= 0:
            raise NumericV2EvaluatorError("numeric_v2_contract_check_timeout")
        try:
            async with asyncio.timeout(remaining):
                collect = (
                    discovery_collection_open and not numeric_v2_options.MODEL_REVIEWS_ENABLED
                    and not cited_dialogue_recovered
                    and module_options.get("evaluator") and module_options.get("review_contract")
                    and not module_options.get("review") and kwargs.get("session") is current.session
                    and kwargs.get("target_node") is None and not kwargs.get("terminal_input_closed")
                    and not kwargs["actor_performance"].get("segments")
                )
                sink: list[CompletionFactBatch] = []
                approval_sink: list[tuple[int, ...]] = []
                bound_batch = None
                if collect:
                    packet = completion_packet(kwargs["actor_performance"])
                    if packet["targets"] and packet["sources"]:
                        kwargs.update(discovery_packet=packet, discovery_sink=sink)
                    if deferred_delivery_claims:
                        bound_batch = pending_source_delivery_batch(kwargs["actor_performance"])
                        if bound_batch.claims:
                            kwargs.update(bound_completion_batch=bound_batch, bound_completion_sink=approval_sink)
                problems = await evaluator.verify_contract_boundaries(**kwargs)
                if not problems:
                    reviewed_keys = set()
                    if bound_batch is not None and len(approval_sink) == 1:
                        # Validate returned indices against this exact pre-call batch.
                        bound_batch.select(approval_sink[0]).validate()
                        source_completion_reviews[performance_fingerprint(kwargs["actor_performance"])] = (
                            bound_batch, approval_sink[0])
                        reviewed_keys = {claim["key"] for claim in bound_batch.claims}
                    for batch in sink:
                        # A rejected bound claim must not be resampled as a new discovery.
                        batch = batch.select(index for index, claim in enumerate(batch.claims)
                                             if claim["key"] not in reviewed_keys)
                        discovered_completion_facts[batch.packet["binding"]["body_fingerprint"]] = batch
                return problems
        except asyncio.TimeoutError as exc:
            raise NumericV2EvaluatorError("numeric_v2_contract_check_timeout") from exc

    terminal_question_verdicts: dict[tuple[tuple[str, str], ...], tuple[str, ...]] = {}

    async def review_terminal_question_markers(candidate: Mapping[str, Any]) -> tuple[str, ...]:
        # This terminal-only interaction check stays active independently of
        # full body review; punctuation alone still cannot reject a greeting.
        can_review = any(module_options.get(key) for key in ("evaluator", "review", "review_contract"))
        markers = _terminal_new_question_markers(
            engine=runtime.engine, outcome=outcome, performance=candidate, include_task_cues=can_review)
        if not markers or not can_review:
            return markers
        # The narrow check sees only these ordered blocks, a fixed rule and
        # this turn's input. A byte-identical visible rewrite has the same verdict.
        visible_key = tuple((block["type"], block["text"]) for block in performance_content_blocks(candidate))
        if visible_key in terminal_question_verdicts:
            trace_event("transition.terminal_question_check_reused")
            return markers if terminal_question_verdicts[visible_key] else ()
        # 默认前置判定开启时也区分招呼与待答问题；全部辅助模型关闭仍保留原零调用拦截。
        # 只检查结局输入已关闭的交互合同，不启用完整动作、路线或作者边界复核。
        started_at = time.monotonic()
        diagnostics["terminal_question_review_calls"] += 1
        try:
            violated = await verify_contract_boundaries(
                node={"story_beat": {"must_not_happen": [
                    "不得在结局提出必须由玩家下一轮回答、选择或在收束后执行的新问题或任务；"
                    "认人招呼、修辞反问、自问自答及引用旧问题不需玩家回应时不受此限。"
                ]}},
                # This check covers the whole delivered ending, unlike the
                # next-scene evaluator's target-opening-only history projection.
                actor_performance=candidate,
                include_all_segments=True,
                terminal_input_closed=True,
                player_input=turn.message,
            )
        except NumericV2EvaluatorError as exc:
            # Incomplete review is not validity; do not add another Actor draft for timeout.
            diagnostics["terminal_question_review_degraded"] = True
            trace_event("transition.terminal_question_check_failed", error=str(exc))
            raise NumericV2ActorOutputError("numeric_v2_transition_review_failed") from exc
        finally:
            _add_elapsed_ms(diagnostics, "terminal_question_check_work", started_at)
        terminal_question_verdicts[visible_key] = violated
        return markers if violated else ()

    def transition_bridge_leaks(candidate: Mapping[str, Any]) -> tuple[str, ...]:
        """Return target-opening facts that the candidate's bridge segment already narrates.

        The bridge is checked against both the target node's authored opening and the
        target opening this draft actually delivers; clauses the authored bridge
        contract already permits stay exempt.
        """

        target_node = runtime.engine.nodes.get(str(outcome.ledger_event["to_node_id"]))
        target_beat = target_node.get("story_beat") if isinstance(target_node, Mapping) else None
        # Compare author text in the same pre-turn cast/address scope as Actor;
        # candidate text is already performed and must not be projected again.
        authored_opening = _project_authored_transition_text(
            runtime.engine, current.session,
            scene_opening_text(target_beat) if isinstance(target_beat, Mapping) else "",
        )
        transition_contract = outcome.transition_contract or {}
        candidate_segments = candidate.get("segments")
        bridge_text = "\n".join(
            str(segment.get("scene_narration") or "")
            for segment in candidate_segments
            if isinstance(segment, Mapping) and segment.get("phase") == "transition_bridge"
        ) if isinstance(candidate_segments, list) else ""
        target_opening_text = "\n".join(
            str(segment.get("scene_narration") or "")
            for segment in candidate_segments
            if isinstance(segment, Mapping) and segment.get("phase") == "target_opening"
        ) if isinstance(candidate_segments, list) else ""
        if not bridge_text:
            bridge_text = str(candidate.get("bridge_scene_narration") or "")
        if not target_opening_text:
            target_opening_text = str(candidate.get("target_scene_narration") or "")
        authored_bridge = _project_authored_transition_text(
            runtime.engine, current.session,
            str(transition_contract.get("bridge_scene_narration") or ""),
        )
        # Only the latest committed source narration can show that a numeric marker
        # is not unique to the target. Never use author plans, dialogue, older visits
        # or this uncommitted draft; a crossing contributes its target opening only.
        latest_source = (current.session.performance_history[-1]
                         if current.session.performance_history else current.session.opening_performance)
        source_parts = latest_source.get("segments")
        if isinstance(source_parts, list):
            source_parts = [part for part in source_parts
                            if isinstance(part, Mapping) and part.get("phase") == "target_opening"]
        else:
            source_parts = [latest_source]
        source_narration = "\n".join(scene_narration_text(part) for part in source_parts)
        # Author opening and opening actually delivered in this draft both count as target content; block verbatim preemption of either in bridge.
        return tuple(dict.fromkeys(
            marker
            for opening in (authored_opening, target_opening_text)
            for marker in transition_bridge_leak_markers(
                target_opening=opening,
                bridge_text=bridge_text,
                authored_bridge=authored_bridge,
                source_narration=source_narration,
            )
        ))

    async def verify_later_transition_draft(candidate: Mapping[str, Any], *, stage: str) -> None:
        """Re-run the deterministic transition checks on a regenerated formal draft.

        Only the first formal draft gets a repair attempt; any later draft (missed
        initiation recovery, contract or review rewrite) must already satisfy the
        checks, otherwise the turn rolls back as a retryable Actor failure.
        """

        leaks = transition_bridge_leaks(candidate)
        if leaks or "transition_bridge_leak_markers" in diagnostics:
            diagnostics["transition_bridge_leak_markers_after_rewrite"] = list(leaks)
        if leaks:
            diagnostics["transition_structure_rejected"] = True
            trace_event("transition.bridge_target_leak_unresolved", markers=list(leaks), stage=stage)
            raise NumericV2ActorOutputError("numeric_v2_transition_segment_overlap")
        questions = await review_terminal_question_markers(candidate)
        diagnostics["terminal_new_question_markers"] = list(questions)
        if questions:
            diagnostics["terminal_structure_rejected"] = True
            trace_event("transition.terminal_new_question_unresolved", markers=list(questions), stage=stage)
            raise NumericV2ActorOutputError("numeric_v2_terminal_new_question")

    if route_changed:
        # Inspect the same draft before spending the one semantic rewrite. A closing task may be present alongside bridge overlap.
        leak_markers = transition_bridge_leaks(performance)
        terminal_question_markers = await review_terminal_question_markers(performance)
        if leak_markers or terminal_question_markers:
            draft = _transition_repair_draft(performance,
                draft_provenance=actor_draft_provenance.get(performance_fingerprint(performance)))
            repair_hints = []
            repair_issues = []
            if leak_markers:
                diagnostics["transition_bridge_leak_markers"] = list(leak_markers)
                trace_event("transition.bridge_target_leak", markers=list(leak_markers))
                repair_hints.append(
                    "过渡桥段提前包含了目标幕开场独有内容：" + "、".join(leak_markers)
                    + "。修正bridge_scene_narration中命中的内容，保留合法来源回应和作者允许的过渡时空。"
                    "target_scene_narration单独交付目标开场，不把它挪进桥段或重演其独有事实。"
                )
                repair_issues.extend({"field": "bridge_scene_narration", "quote": marker} for marker in leak_markers)
            if terminal_question_markers:
                diagnostics["terminal_new_question_markers"] = list(terminal_question_markers)
                trace_event("transition.terminal_new_question", markers=list(terminal_question_markers))
                suspect_fields = [field for field, text in draft.items()
                                  if isinstance(text, str) and _has_terminal_request_cue(text, include_task_cues=True)]
                repair_hints.append(
                    "这是结局交付，不能留下需要玩家回答的新问题或尚待执行的新任务。"
                    "逐段核对来源回应 source_response、桥段 transition_bridge 和目标开场 target_opening。"
                    "在问题所在段删去待答或待做的要求，按已发生的事实收束，保留已授权行动和合法招呼。"
                    "不得追加新邀约、选择或等待玩家输入。"
                    + ("疑似字段：" + "、".join(suspect_fields) + "。招呼和已解决的问句可以保留。" if suspect_fields else "")
                )
                repair_issues.extend({"field": field} for field in suspect_fields)
            diagnostics["semantic_rewrite_attempts"] += 1
            performance = await generate_actor_turn(
                outcome, retry_hint="".join(repair_hints),
                draft_repair_context={"draft": draft, "issues": repair_issues},
            )
            await verify_later_transition_draft(performance, stage="structure_rewrite")
    contract_verdicts: dict[tuple[str, str, str | None, str | None], tuple[str, ...]] = {}
    contract_findings: dict[tuple[str, str, str | None, str | None], list[dict[str, Any]]] = {}
    departure_repairs: set[tuple[str, str, str | None, str | None]] = set()
    offer_verdicts: dict[tuple[str, str, str, str], bool] = {}
    contract_unsafe_suggestions: dict[tuple[str, str, str | None, str | None], set[str]] = {}
    contract_player_absence: dict[tuple[str, str, str | None, str | None], tuple[Mapping[str, Any], ...]] = {}
    claimed_completion_results: dict[str, dict[str, Any]] = {}
    player_fact_evidence = public_player_fact_evidence(current.session, runtime.engine.fact_contract)

    async def regenerate_blocked_departure():
        nonlocal progress_direction, current_scene_task, departure_direction_reviewed
        if module_options.get("evaluator") and not departure_direction_reviewed:
            guide = await review_completion_evidence((), review_progress=True, blocked_departure=True)
            progress_direction = guide.direction
            current_scene_task = guide.current_scene_task
            departure_direction_reviewed = True
            trace_event("story_evidence.blocked_departure_direction", direction=progress_direction)
        return await generate_actor_turn(outcome, departure_repair=True, retry_hint=(
            "从最近已提交的本幕状态和玩家本轮原话重新写。开头用本幕仍在场的对象或感官细节确定落点，"
            "承接原话中已经实施的合法幕内动作，别用玩家声称的离场结果开场。"
            "直接当前角色能交付的一项本幕新变化；需要玩家行动或决定时把下一步交给玩家。"
        ))

    def contract_key(candidate):
        blocked_route = outcome.blocked_departure_route or {}
        return (str(outcome.ledger_event["to_node_id"]), performance_fingerprint(candidate),
                outcome.departure_blocker, blocked_route.get("id"))

    async def delivery_contract_problems(
        candidate: Mapping[str, Any], *, offer_text: str = "", offer_route=None, offer_accept_input: str = "",
    ) -> tuple[str, ...]:
        nonlocal recovery_author_original_conflict
        target_id = str(outcome.ledger_event["to_node_id"])
        changed = target_id != str(outcome.ledger_event["from_node_id"])
        key = contract_key(candidate)
        pending_results = _pending_completion_facts(runtime.engine, outcome.session) if not changed else ()
        for claim in candidate.get("fact_candidates") or ():
            if not isinstance(claim, Mapping) or not isinstance(claim.get("key"), str):
                continue
            definition = runtime.engine.fact_contract.get(claim.get("key"), {})
            quote = claim.get("evidence_quote")
            target = next((row for row in pending_results if row["key"] == claim["key"]
                and type(row["value"]) is type(claim.get("value")) and row["value"] == claim.get("value")), None)
            if (target is not None and definition.get("evidence_source") == "performance"
                    and module_options.get("evaluator") and not module_options.get("review")):
                expanded = _expand_grouped_actor_fact_quote(claim, definition, candidate,
                    player_input=turn.message, published_offer=str(candidate.get("_published_transition_offer") or ""),
                    recover_formatting=not numeric_v2_options.MODEL_REVIEWS_ENABLED)
                try:
                    runtime.engine.finalize_actor_fact_candidates(current.session, outcome, candidates=[expanded],
                        evidence_sources={"actor_performance": _actor_fact_evidence_text(candidate)})
                except NumericV2RuntimeError as exc:
                    if str(exc) == "story_fact_candidate_evidence_anchor_missing":
                        # A literal current claim invites semantic delivery checking, never a state write.
                        claimed_completion_results[claim["key"]] = {
                            field: target[field] for field in ("key", "value", "description")}
            if (definition.get("evidence_source") == "player_input"
                    and isinstance(quote, str) and quote.strip() and quote not in turn.message
                    and quote in _actor_fact_evidence_text(candidate)):
                if any(
                    approved["key"] == claim["key"] and not approved.get("validated_only")
                    and type(approved.get("value")) is type(claim.get("value"))
                    and approved.get("value") == claim.get("value")
                    and any(evidence.get("source") == "player_input"
                            and isinstance(evidence.get("quote"), str) and evidence["quote"].strip()
                            and evidence["quote"] in turn.message for evidence in approved.get("evidence", ()))
                    for approved in independently_approved_facts
                ):
                    # Actor cannot supply this fact. The independently approved player original already does.
                    # Continue through the full body check so extra unsupported actions still fail.
                    trace_event("contract.player_fact_uses_approved_input", key=claim["key"])
                    continue
                rule = "玩家自主行动须来自玩家原话。只承接已授权动作的后果，删除本稿补演的玩家动作和相应事实候选"
                contract_findings[key] = [{"rule": rule, "quote": quote}]
                trace_event("contract.player_action_source_rejected", key=claim["key"], quote=quote)
                return (rule,)
        proposed = None
        offer_key = None
        offer_text = offer_text or str(candidate.get("_published_transition_offer") or "")
        if is_authored_recovery(candidate) and authored_invitation_recovered:
            offer_text = offer_text or str(program_invitation_performance or "")
        offer_route = offer_route or actor_draft_route
        if (not changed and not numeric_v2_options.MODEL_REVIEWS_ENABLED and offer_text and offer_route
                and _authored_offer_visible(candidate, offer_text)):
            proposed = {"text": offer_text, "exit_plan": str(offer_route["transition_contract"].get("reason") or "")}
            if offer_accept_input:
                proposed["accept_input"] = offer_accept_input
            offer_key = (key[1], offer_route["id"], offer_text, offer_accept_input)
        if key in contract_verdicts and (offer_key is None or offer_key in offer_verdicts):
            return contract_verdicts[key]
        if (authored_recovery_receipt is not None or cited_dialogue_recovered) and diagnostics.get("contract_check_calls", 0) >= 2:
            # Recovery shares the original draft/rewrite allowance. An edited
            # recovery cannot inherit a cached approval or add a third audit.
            raise NumericV2ActorOutputError("numeric_v2_contract_check_failed")
        problems: list[str] = []
        source = runtime.engine.nodes[str(outcome.ledger_event["from_node_id"])]
        blocked_route = outcome.blocked_departure_route if not changed else None
        unconfirmed_stage_claim = not changed and bool(unconfirmed_departure_quote)
        if changed and module_options.get("review_delivery"):
            missing = missing_contract_names(source, target_id, candidate, current.session,
                story=runtime.engine.story, delivery_session=outcome.session)
            diagnostics["contract_missing"] = list(missing)
            # Names alone do not prove ownership, delivery or an unauthorized action.
            # Keep this zero-call signal diagnostic; the semantic contract decides rejection.
        # A retained invitation is history, not a new draft with routing
        # consequences. Recheck edits only after a real contract check.
        requires_contract_check = (
            changed or candidate.get("transition_offer_presented") or candidate.get("_published_transition_offer")
            or (candidate.get("transition_offered") and not current.session.transition_offered)
            or candidate.get("fact_candidates") or candidate.get("scene_narration")
            or candidate.get("fixed_narrations") or proposed or contract_verdicts or unconfirmed_stage_claim
            or claimed_completion_results
        )
        author_boundaries_only = (
            not requires_contract_check and (blocked_route is not None or bool(contract_boundary_items(source))
                or bool((source["story_beat"].get("acting_contract") or {}).get("forbidden_behaviors")))
            and bool(performance_content_blocks(candidate))
        )
        if (module_options.get("review_contract") and not module_options.get("review")
                and (requires_contract_check or author_boundaries_only)):
            started = time.monotonic()
            diagnostics["contract_check_calls"] = diagnostics.get("contract_check_calls", 0) + 1
            cast = NumericV2CastProjection.from_story(runtime.engine.story,
                player_name=str(current.session.catgirl_binding.get("player_address") or "你")
                    if current.session.player_address_known else "你",
                catgirl_name=str(current.session.catgirl_binding.get("catgirl_name") or "当前角色"))
            target = runtime.engine.nodes[target_id] if changed else None
            mismatch = exit_mismatch_reason if not changed else ""
            departure_plan = None
            if unconfirmed_stage_claim:
                mismatch = "运行时本轮仍留在当前幕，玩家原话中的跨阶段声称未获阶段变化授权。"
            if blocked_route is not None:
                blocked_target = runtime.engine.nodes[str(blocked_route["target_node_id"])]
                pending = ("本幕必交结果尚未满足" if outcome.departure_blocker == "completion_pending"
                           else "本幕必显旁白尚未交付")
                direction = cast.text(str(blocked_route["transition_contract"].get("reason") or ""))
                opening = cast.text(scene_opening_text(blocked_target["story_beat"]))
                mismatch = f"{pending}，运行时本轮仍留来源幕。"
                departure_plan = {"transition_direction": direction[:180], "opening_reference": opening[:180]}
            check_diagnostics: dict[str, Any] = {}
            accepted_arrangement = ""
            if (changed and confirmed_acceptance_route_id and turn.input_source == "accept_invitation"
                    and outcome.ledger_event.get("transition_intent") == "accept"
                    and (outcome.route or {}).get("id") == confirmed_acceptance_route_id
                    and outcome.ledger_event.get("accepted_offer_route_id") == confirmed_acceptance_route_id):
                # The native control accepts the reviewed public proposal, without inventing player speech.
                receipt = current.ledger_events[-1].get("program_invitation") if current.ledger_events else None
                if isinstance(receipt, Mapping):
                    accepted_arrangement = str(receipt.get("offer") or "")
            scene_task = None
            if ((outcome.departure_blocker == "completion_pending" or unconfirmed_stage_claim)
                    and departure_attempt_observed and progress_direction):
                required_keys = {item.get("key") for item in source.get("completion_contract", {}).get("all", ())}
                scene_task = current_scene_task or {
                    "direction": progress_direction,
                    "pending_results": [row for row in _pending_completion_facts(runtime.engine, current.session)
                                        if row["key"] in required_keys],
                    **cast.value(_scene_delivery_plan(source["story_beat"])),
                }
            if claimed_completion_results:
                # Retain this turn's uncommitted claim scope when a repair drops its metadata.
                scene_task = {**(scene_task or {}), "claimed_results": list(claimed_completion_results.values())}
            try:
                problems.extend(await verify_contract_boundaries(
                    node={**source, "story_beat": cast.value(source["story_beat"])},
                    actor_performance=candidate, player_input=turn.message,
                    cast_scope={"player": list(dict.fromkeys(name for name in (cast.source_player_name, cast.player_name) if name)),
                        "active_catgirl": list(dict.fromkeys(name for name in (cast.source_catgirl_name, cast.catgirl_name) if name))},
                    session=current.session, include_all_segments=True,
                    target_node={**target, "story_beat": cast.value(target["story_beat"])} if target is not None else None,
                    **({"transition_contract": cast.value(outcome.transition_contract or {})} if changed else {}),
                    exit_mismatch_reason=mismatch,
                    **({"departure_plan": departure_plan} if departure_plan else {}),
                    **({"departure_blocker": outcome.departure_blocker} if blocked_route is not None else {}),
                    **({"departure_attempt_observed": True}
                       if (blocked_route is not None or unconfirmed_stage_claim) and departure_attempt_observed else {}),
                    **({"current_scene_task": scene_task} if scene_task else {}),
                    **({"accepted_arrangement": accepted_arrangement} if accepted_arrangement else {}),
                    author_boundaries_only=author_boundaries_only,
                    diagnostics=check_diagnostics,
                    **({"reviewed_player_absence": reviewed_transition_player_absence}
                       if changed and reviewed_transition_player_absence else {}),
                    **({"player_fact_evidence": player_fact_evidence} if player_fact_evidence else {}),
                    **({"proposed_offer": proposed} if proposed is not None else {})))
                if offer_key is not None:
                    offer_verdicts[offer_key] = (check_diagnostics.get("offer_actionable") is True
                                                 and not is_pressure_only_offer(offer_text))
                findings = check_diagnostics.get("findings", [])
                uneditable = [finding for finding in findings if (
                    (finding.get("candidate") or {}).get("program_fixed_original") is True
                    and not (finding.get("rule") == _FIXED_ORIGINAL_CAUSE_RULE
                        and str((finding.get("prior") or {}).get("id", "")).startswith("c")
                        and (finding.get("prior") or {}).get("program_fixed_original") is not True
                        and not (reviewed_transition_source is not None
                            and (finding.get("prior") or {}).get("phase") == "source_response")))]
                recovery_author_original_conflict = recovery_author_original_conflict or bool(problems and uneditable)
                if problems and uneditable:
                    diagnostics["uneditable_contract_conflicts"] = uneditable
                if (not changed and unconfirmed_departure_quote and problems) or (
                        blocked_route is not None and (check_diagnostics.get("departure_scope") == "uncertain"
                        or check_diagnostics.get("local_delivery_missing") or any(
                    (item.get("prior") or {}).get("id") == "d0" for item in findings
                ))):
                    # Draft quotes and target openings stay on the audit side for a fresh current-scene response.
                    departure_repairs.add(key)
                else:
                    contract_findings[key] = findings
                contract_unsafe_suggestions[key] = set(check_diagnostics.get("unsafe_suggestion_texts", ()))
                trace_event("contract.checked", **check_diagnostics)
            except NumericV2EvaluatorError as exc:
                diagnostics["contract_check_degraded"] = True
                trace_event("contract.check_failed", error_code=str(exc))
                raise NumericV2ActorOutputError("numeric_v2_contract_check_failed") from exc
            finally:
                _add_elapsed_ms(diagnostics, "contract_check_work", started)
            if (problems and reviewed_transition_source is not None and any(
                    (finding.get("candidate") or {}).get("phase") == "source_response"
                    for finding in check_diagnostics.get("findings", ()))):
                # Tail-only repair cannot change its already bound source.
                # A new whole-draft hash must not resample that confirmed violation away.
                raise NumericV2ActorOutputError("numeric_v2_contract_violation")
            contract_verdicts[key] = tuple(problems)
            blocks = performance_content_blocks(candidate)
            if (not changed and not problems and blocks
                    and check_diagnostics.get("player_authority_complete") is True
                    and check_diagnostics.get("player_authority_blocks") == {
                        f"c{index}": "none" for index in range(len(blocks))}):
                # Only a successful independent absence finding can be carried
                # into this turn's immutable source. No player grants are inferred.
                contract_player_absence[key] = tuple(deepcopy(blocks))
            return contract_verdicts[key]
        return ()

    async def checked_delivery(candidate: dict[str, Any]) -> tuple[dict[str, Any], tuple[str, ...]]:
        try:
            return candidate, await delivery_contract_problems(candidate)
        except NumericV2ActorOutputError as exc:
            if str(exc) != "numeric_v2_contract_check_failed":
                raise
            recovered = recover_authored_transition(str(exc.__cause__ or exc))
            if recovered is None:
                raise
            return recovered, await delivery_contract_problems(recovered)

    performance, problems = await checked_delivery(performance)
    if problems and recovery_author_original_conflict:
        # Actor cannot edit a program original. Retain the real conflict and
        # stop before spending a rewrite that must reproduce the same source.
        raise NumericV2ActorOutputError("numeric_v2_contract_violation")
    if problems and diagnostics["semantic_rewrite_attempts"]:
        recovered = recover_authored_transition("contract_rewrite_exhausted")
        if recovered is None:
            raise NumericV2ActorOutputError("numeric_v2_contract_violation")
        performance, problems = await checked_delivery(recovered)
        if problems:
            raise NumericV2ActorOutputError("numeric_v2_contract_violation")
    if problems:
        diagnostics["contract_violated"] = list(problems)
        diagnostics["semantic_rewrite_attempts"] += 1
        key = contract_key(performance)
        if key in departure_repairs:
            performance = await regenerate_blocked_departure()
        else:
            findings = contract_findings.get(key, [])
            repair_context = {
                "draft": _transition_repair_draft(performance,
                    draft_provenance=actor_draft_provenance.get(performance_fingerprint(performance)))
                    if route_changed else dict(performance),
                "issues": findings,
            } if findings else None
            performance = await generate_transition_or_recover(draft_repair_context=repair_context, retry_hint=(
                "本轮正文合同核对失败：" + "；".join(problems)
                + "。按所引原文、当前授权及每段边界删除或修正违规内容。即使玩家在等待该结果，也只交付本阶段获准的新进展。"
                "保留合法的已完成结果，不重置对象或追加解释矛盾的新设定。"
                + ("本轮关键结果漏交付时，从已提交状态和同一玩家原话编辑未提交稿，补足获授权的必要后果，"
                   "或修正无法成立的完成宣称及其不可逆后果；不补写过去事件、玩家动作或强行转幕。"
                   if claimed_completion_results else "")))
        await verify_later_transition_draft(performance, stage="contract_rewrite")
        performance, remaining_problems = await checked_delivery(performance)
        diagnostics["contract_violated_after_rewrite"] = list(remaining_problems)
        if (remaining_problems and contract_key(performance) == key and not route_changed
                and not module_options.get("review") and module_options.get("review_contract")
                and diagnostics.get("contract_check_calls", 0) < 2
                and authored_recovery_receipt is None and not current.session.transition_offered
                and outcome.ledger_event.get("scene_complete") is not True
                and not outcome.ledger_event.get("fact_operations") and not performance.get("fact_candidates")
                and reviewed_transition_source is None and not deferred_delivery_claims
                and not approved_delivery_claims and adopted_completion_facts is None):
            findings = contract_findings.get(key, [])
            if {row.get("rule") for row in findings} == set(remaining_problems):
                recovered = _cited_dialogue_recovery(performance, findings,
                    author_rules=contract_boundary_items(runtime.engine.nodes[current.session.current_node_id]),
                    dialogue_policy=outcome.session.dialogue_policy)
                if recovered is not None:
                    # A changed body earns no prior approval. Spend only the
                    # unused second audit; never send a third Actor draft.
                    cited_dialogue_recovered = True
                    actor_publishes_transition_offer = reviewed_transition_offered = False
                    performance, remaining_problems = await checked_delivery(recovered)
                    diagnostics["cited_dialogue_recoveries"] = int(not remaining_problems)
        if remaining_problems:
            recovered = recover_authored_transition("contract_rewrite_exhausted")
            if recovered is None:
                raise NumericV2ActorOutputError("numeric_v2_contract_violation")
            performance, problems = await checked_delivery(recovered)
            if problems:
                raise NumericV2ActorOutputError("numeric_v2_contract_violation")

    if not module_options.get("review"):
        # Review disabled: no review-model call, but deterministic zero-call scene boundaries still allow one Actor repair.
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
                    if outcome.departure_blocker or unconfirmed_departure_quote:
                        performance = await regenerate_blocked_departure()
                    else:
                        performance = await generate_actor_turn(outcome, retry_hint=(
                            "本轮可见旁白提前写出了目标幕独有事实：" + "、".join(leaked)
                            + "。目标幕尚未进入；请只保留当前幕可证实内容，邀请可以提出但不能把目标事实写成现在时。"))
        reviewed_transition_offered = performance.get("transition_offered") is True
    elif (
        not route_changed
        and (
            performance.get("transition_offered") is True
            or str(performance.get("performance") or "").strip()
            or str(performance.get("scene_narration") or "").strip()
        )
    ):
        # Old invitation latch does not prove current narrative safe; review in-act follow-ups/clarifications too, leaving invitation state to Runtime.
        # Narrative violations and invalid invitation share one rewrite; dispute only a still-rejected latest draft.
        for rewrite_attempt in range(2):
            transition_review = await review_transition_offer(
                performance,
                defer_dispute_for_rewrite=rewrite_attempt == 0,
            )
            if diagnostics["dispute_review_degraded"] and review_budget_effectively_exhausted():
                # If dispute consumed this turn's budget, add no equally costly Actor rewrite; let player retry,
                # avoiding incompletely reviewed repair entering history.
                diagnostics["review_timeout_aborted"] = True
                trace_event("review.rewrite_aborted", phase="ordinary", reason="review_budget_exhausted")
                raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
            # User allows missed-detection recovery into official transition; do not commit ordinary draft or use it as public-destination evidence.
            if (transition_review.missed_initiation and evaluation.transition_intent == "unclear"
                    and not current.session.transition_offered
                    and not diagnostics["missed_initiation_recoveries"]):
                approved_generation_fallback = (
                    (outcome, evaluation, deepcopy(performance), transition_review)
                    if final_fixed_review is transition_review
                    and not transition_review.body_violations
                    and (not transition_review.offer_present or transition_review.valid)
                    and not transition_review.fixed_narration_triggers
                    else None
                )
                recovered_evaluation = replace(
                    evaluation, transition_intent="initiate",
                    public_destination_quote=transition_review.public_destination_quote,
                    natural_ending_ready=False,
                )
                # prepare_turn always computes from current, never resettling already-scored outcome.session.
                recovered_outcome = prepare_turn(recovered_evaluation)
                if recovered_outcome.ledger_event["from_node_id"] != recovered_outcome.ledger_event["to_node_id"]:
                    # Stage only fully reviewed ordinary drafts. If official authorization later denies recovery, reuse the same
                    # staying-in-act transaction; candidates with new invitations/facts/fixed-narration dependencies use original rewrite flow.
                    if (
                        final_fixed_review is transition_review
                        and not transition_review.body_violations
                        and not transition_review.offer_present
                        and not transition_review.fixed_narration_triggers
                        # Review supplies facts here; Actor candidates do not commit. Invalid/duplicate
                        # Review candidates are ultimately rejected/ignored, requiring no regeneration of reviewed narrative.
                        and not _has_new_review_facts(
                            runtime.engine, current, outcome, performance, transition_review,
                        )
                    ):
                        approved_recovery_fallback = (
                            outcome, deepcopy(performance), transition_review,
                            evaluation,
                        )
                    diagnostics["missed_initiation_recoveries"] += 1
                    trace_event("transition.recovered", evaluation=recovered_evaluation)
                    evaluation, outcome = recovered_evaluation, recovered_outcome
                    route_changed = True
                    try:
                        performance = await generate_actor_turn(outcome)
                        await verify_later_transition_draft(performance, stage="missed_initiation_recovery")
                    except NumericV2ActorError as exc:
                        if approved_generation_fallback is None:
                            raise
                        (
                            outcome, evaluation, performance, final_fixed_review,
                        ) = approved_generation_fallback
                        performance, removed = _drop_reported_unsafe_suggestions(
                            performance, final_fixed_review.unsafe_suggestion_indexes, review=final_fixed_review,
                        )
                        diagnostics["unsafe_suggestions_removed"] += removed
                        reviewed_transition_offered = final_fixed_review.offer_present and final_fixed_review.valid
                        if performance.get("transition_offered") is True and not reviewed_transition_offered:
                            diagnostics["phantom_transition_flags_cleared"] += 1
                        performance = {**performance, "transition_offered": reviewed_transition_offered}
                        route_changed = False
                        diagnostics["recovered_ordinary_drafts_reused"] += 1
                        trace_event(
                            "transition.approved_ordinary_draft_reused",
                            reason="generation_failed", error_code=str(exc),
                        )
                    break
            performance, removed_suggestions = _drop_reported_unsafe_suggestions(
                performance, transition_review.unsafe_suggestion_indexes, review=transition_review,
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
                    body_issues=(),
                    fixed_narration_triggers=(),
                )
                final_fixed_review = transition_review
                diagnostics["player_action_projection_safe_degrades"] += 1
                trace_event(
                    "review.player_action_projection_safe_degrade",
                    removed_fields=["scene_narration", "fact_candidates", "fixed_narration_triggers"],
                )
            cropped_scene_update = (
                _safe_drop_invalid_scene_update(performance, transition_review)
                if final_fixed_review is transition_review
                and not current.session.transition_offered
                and not diagnostics["dispute_review_degraded"]
                and not diagnostics["transition_cancellations"]
                else None
            )
            if cropped_scene_update is not None:
                performance = cropped_scene_update
                transition_review = replace(
                    transition_review, body_violations=(), body_issues=(), failure_reason="",
                    scene_update_removal_safe=False,
                )
                final_fixed_review = transition_review
                diagnostics["invalid_scene_update_local_crops"] += 1
                trace_event("review.invalid_scene_update_local_crop")
            cropped_offer = (
                _safe_drop_invalid_offer(performance, transition_review)
                if final_fixed_review is transition_review
                and not current.session.transition_offered
                and not diagnostics["dispute_review_degraded"]
                else None
            )
            if cropped_offer is not None:
                performance = cropped_offer
                transition_review = replace(
                    transition_review, offer_present=False, valid=False, offer_quote="", failure_reason="",
                )
                final_fixed_review = transition_review
                diagnostics["invalid_offer_local_crops"] += 1
                trace_event("review.invalid_offer_local_crop")
            invalid_offer = (
                transition_review.offer_present and not transition_review.valid
            )
            if not transition_review.body_violations and not invalid_offer:
                # After removing entire invalid recommendation group, neither rereview approved narrative nor call model to fill count.
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
                if set(transition_review.body_violations) & {"scene_boundary", "target_opening_leak"}:
                    trace_event("review.ordinary_scene_delivery_rejected")
                    raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
                # User permits continued performance after persistent semantic rejection: atomically commit last draft to real history, not a separate display copy.
                diagnostics["semantic_review_fallback"] = True
                diagnostics["semantic_review_fallback_phase"] = "ordinary"
                # Fallback commits final narrative only, never latching reviewed-invalid destinations as pending invitations.
                # Runtime independently preserves prior public, still-valid invitations without renewed final-draft approval.
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
            if outcome.departure_blocker and "target_opening_leak" in transition_review.body_violations:
                performance = await regenerate_blocked_departure()
                continue
            # Wrong invitations already have next_scene; only leaked scene delivery needs full future boundaries.
            boundary_context = (
                _transition_boundary_repair_context(runtime, current, metrics=outcome.session.metrics,
                                                   facts=outcome.session.story_state["facts"])
                if "scene_boundary" in transition_review.body_violations
                else ""
            )
            # Ordinary turns respond afresh to same input and true history, avoiding rejected-draft false facts; retain reasons for checking.
            performance = await generate_actor_turn(
                outcome,
                retry_hint=(
                    "这是唯一一次正文与提议修复，上一稿未提交；从本轮原始上下文重新回应，不是继续扩写剧情。"
                    "以玩家实际输入、已提交历史和作者硬边界为准，保留获准回应，"
                    "不补出未表达的后续操作及正文、场景更新、推荐中依赖它的结果。"
                    "scene_update 只记录本轮新的可见变化；没有新变化就省略。"
                    "你可用自身反应、回答或明确未知承接玩家，不要求本轮推进剧情或产生外部结果。"
                    "不得为了交付结果、收束或兑现旧推荐补造操作、事实或下一阶段，也不能撤销玩家已做的合法动作。"
                    "只有正文已公开具体、合乎当前事实且与实际下一阶段一致的未来邀请时才设 transition_offered=true；"
                    "邀请停在执行前，按钮不能代替正文首次提出转场。没有合适出口就不提议，不追加前提清单。"
                    f"{_transition_review_failure_context(transition_review)}"
                    f"{boundary_context}"
                ),
            )
    if route_changed and not module_options.get("review"):
        # Review disabled: use Actor's three parts directly, without violation judgments, cancellation, or rewriting.
        diagnostics["review_skipped"] = True
        trace_event("review.skipped", phase="transition")
        reviewed_transition_offered = performance.get("transition_offered") is True
    elif route_changed:
        # Review three parts + initial buttons together once; only explicit narrative conflict gets one rewrite, never static-narration replacement or forced ending.
        diagnostics["route_suggestion_reviews"] += int(bool(performance.get("suggested_inputs")))
        for rewrite_attempt in range(2):
            review = await review_transition_offer(performance, defer_dispute_for_rewrite=rewrite_attempt == 0)
            if diagnostics["dispute_review_degraded"] and review_budget_effectively_exhausted():
                # Official transition rolls back directly on dispute timeout; never commit three-part records from incompletely reviewed rewrite.
                diagnostics["review_timeout_aborted"] = True
                trace_event("review.rewrite_aborted", phase="transition", reason="review_budget_exhausted")
                raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
            performance, removed = _drop_reported_unsafe_suggestions(
                performance, review.unsafe_suggestion_indexes, review=review,
            )
            diagnostics["unsafe_suggestions_removed"] += removed
            if not review.body_violations:
                break
            if (
                review.initiation_authorized is False
                or review.acceptance_authorized is False
            ) and rewrite_attempt == 0 and not diagnostics["transition_cancellations"]:
                # Destination authorization is a commit safety gate, independent of earlier bridge/delivery-contract rewrite allowance.
                # Post-semantic-rewrite recheck does not overturn first authorization again; after cancellation generate one staying-in-act draft only.
                # Reprepare from original snapshot and same scoring, never subtracting from transitioned candidate or accumulating scores twice.
                diagnostics["transition_cancellations"] += 1
                trace_event("transition.cancelled", review=review)
                # Remove only explicitly invalid invitations; unapproved moves caused solely by inquiry/hesitation retain valid original invitation.
                invalidate_previous_offer = review.pending_invitation_invalid is True
                evaluation = replace(evaluation, transition_intent="unclear",
                                     natural_ending_ready=False, public_destination_quote="")
                outcome = prepare_turn(evaluation)
                if invalidate_previous_offer:
                    # Share removal conclusion before rewrite; never keep rejected original text marked currently pending.
                    # Modify only uncommitted candidate here; technical failures still roll back to current.
                    outcome, _ = runtime.engine.finalize_transition_offer_state(
                        outcome, {}, new_offer=False, invalidate_previous_offer=True)
                route_changed = False
                if (
                    approved_recovery_fallback is not None
                    and review.initiation_authorized is False
                    and not invalidate_previous_offer
                    and outcome == approved_recovery_fallback[0]
                ):
                    # Complete transaction equality protects values, facts, invitations, and player-action projection while avoiding only regeneration;
                    # technical failures already rolled back, and unreviewed narratives/official transitions never reach here.
                    (
                        _, performance, approved_review,
                        evaluation,
                    ) = approved_recovery_fallback
                    performance, removed = _drop_reported_unsafe_suggestions(
                        performance, approved_review.unsafe_suggestion_indexes, review=approved_review,
                    )
                    diagnostics["unsafe_suggestions_removed"] += removed
                    if performance.get("transition_offered") is True:
                        diagnostics["phantom_transition_flags_cleared"] += 1
                    performance = {**performance, "transition_offered": False}
                    reviewed_transition_offered = False
                    final_fixed_review = approved_review
                    diagnostics["recovered_ordinary_drafts_reused"] += 1
                    trace_event("transition.approved_ordinary_draft_reused")
                    break
                diagnostics["semantic_rewrite_attempts"] += 1
                # Three parts and destination-comparison reasons are not staying-in-act draft material, avoiding another cross-act route becoming an instruction.
                # Review reasons remain diagnostic; Actor responds anew from original player input, committed history, and current exit.
                performance = await generate_actor_turn(outcome, retry_hint=(
                    "此前候选换幕因公开去向与玩家授权不符已取消，三段均未播放。"
                    # Review may invalidate the old invitation itself; staying-in-act rewrite must not enact another cross-act destination.
                    "本轮留在当前幕，已获准的幕内行动照常回应；留幕不代表改去另一个跨幕地点。"
                    "若旧邀请与 next_scene 不符，承认自己先前邀约有误，说明当前可行安排并保留玩家重新选择，"
                    "不要执行旧错误邀请，也不要把 next_scene 的不同安排说成玩家已经同意。"
                    "不要要求玩家重复输入，不执行已取消的下一幕安排，不新增额外任务。"
                ))
                # Shared rewrite allowance exhausted: review this staying-in-act draft only, disabling same-turn proactive-request recovery to prevent repeated transitions.
                review = await review_transition_offer(performance)
                performance, removed = _drop_reported_unsafe_suggestions(
                    performance, review.unsafe_suggestion_indexes, review=review,
                )
                diagnostics["unsafe_suggestions_removed"] += removed
                if review.body_violations or (review.offer_present and not review.valid):
                    if set(review.body_violations) & {"scene_boundary", "target_opening_leak"}:
                        trace_event("review.ordinary_scene_delivery_rejected")
                        raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
                    diagnostics["semantic_review_fallback"] = True
                    diagnostics["semantic_review_fallback_phase"] = "ordinary"
                # Staying-in-act drafts after transition cancellation also latch only reviewed-valid new invitations; preserving old ones is determined by
                # invalidate_previous_offer and Runtime, never prolonged through invalid new destinations.
                reviewed_transition_offered = review.offer_present and review.valid
                performance = {**performance, "transition_offered": reviewed_transition_offered}
                break
            if rewrite_attempt or diagnostics["semantic_rewrite_attempts"]:
                # Ordinary-draft recovery into transition shares rewrite allowance; when exhausted, use current complete three parts without further calls.
                diagnostics["semantic_review_fallback"] = True
                diagnostics["semantic_review_fallback_phase"] = "transition"
                break
            # Share diagnostic counts with ordinary turns to trace actual rewrite costs of added transition review.
            diagnostics["semantic_rewrite_attempts"] += 1
            performance = await generate_actor_turn(outcome, retry_hint=(
                (
                    "正式转场尚未提交，上一稿落点错误且未播放。保持当前路线，"
                    "从已提交历史和transition合同重新生成三段，尤其重建桥段及目标旁白，不能沿用旧场景。"
                    "目标旁白与目标表演须处于同一地点、时点和阶段，不新增玩家行动或前提。"
                    if review.delivery_matches_route is False else
                    "正式转场尚未提交，请修正指出的三段事实冲突，保留其余合法内容，不新增玩家行动或前提。"
                )
                + _transition_review_failure_context(review)
                + ("" if review.delivery_matches_route is False else _actor_rewrite_candidate_context(performance))
            ))
            await verify_later_transition_draft(performance, stage="review_rewrite")
        if route_changed and final_fixed_review is not None and final_fixed_review.delivery_matches_route is False:
            # Valid authorization does not prove correct narrative delivery; if still in wrong scene after shared rewrite budget, roll back rather than enter history.
            trace_event("review.transition_delivery_rejected")
            raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
    if not route_changed:
        # With review off, sole rewrite must pass deterministic scene check again; unrepaired cross-act facts cannot enter history as fallback.
        leaked = tuple(dict.fromkeys((
            *premature_target_markers(
                runtime.engine, current.session, outcome, performance, player_input=turn.message),
            *premature_target_scene_facts(
                runtime.engine, current.session, outcome, performance, player_input=turn.message),
        )))
        if leaked:
            trace_event("review.target_opening_leak_unresolved", markers=list(leaked))
            raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
    # 已用尽原有改稿预算仍代玩家行动时，整轮回滚；不能把错误结果交给下轮圆场。
    if final_fixed_review is not None and "player_action" in final_fixed_review.body_violations:
        trace_event("review.player_action_rejected", phase="transition" if route_changed else "ordinary")
        raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
    # Pending original-text turn with author-boundary conflict cannot use last-draft fallback; missing precise location means
    # unsafe to trim, not permission to pass. Successful trimming already cleared narrative violations.
    if final_fixed_review is not None and (
        any(issue.get("code") == "fixed_narration_content" for issue in final_fixed_review.body_issues)
        or ("author_boundary" in final_fixed_review.body_violations
            and review_candidates(runtime.engine.nodes[current.session.current_node_id], current.session))
    ):
        trace_event("review.fixed_content_rejected", phase="ordinary")
        raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
    # Without full Review, a new completion still needs independent causal approval before its body can enter history.
    # No initial candidate means no extra call. A semantic veto permits one bounded, independently reviewed repair; no narrative extraction.
    approved_completion_candidates: tuple[dict[str, Any], ...] = ()
    completion_invitation_review: NumericV2TransitionOfferReview | None = None
    completion_invitation_body = ""
    completion_invitation_route: tuple[str, str] | None = None
    completion_invitation_ready = False
    # Pause Actor completion/invitation audits too; deterministic fact checks below remain.
    if (numeric_v2_options.MODEL_REVIEWS_ENABLED and not module_options.get("review")
            and module_options.get("evaluator") and not route_changed):
        completion_review_session = replace(
            outcome.session, revision=current.session.revision,
            node_turn_count=current.session.node_turn_count,
        )
        completion_candidates = _actor_completion_fact_candidates(
            runtime.engine, performance,
            _pending_completion_facts(runtime.engine, completion_review_session),
        )
        reviewing_completion_facts = bool(completion_candidates)
        invitation_needs_review = (
            not diagnostics["evaluator_degraded"] and not current.session.transition_offered
            and evaluation.transition_intent != "reject"
            and pending_transition_record(current.session, ledger_events=current.ledger_events, include_withdrawn=True) is None
            and runtime.engine.completion_contract_satisfied(outcome.session) is True
            and performance.get("transition_offered") is True and performance.get("suggested_inputs")
        )
        if reviewing_completion_facts or invitation_needs_review:
            # Only an explicit semantic veto earns one repair. Transport/parser
            # failures still roll back, and no rejected text becomes history.
            for completion_attempt in range(2):
                started_at = time.monotonic()
                diagnostics["completion_fact_review_calls"] += int(reviewing_completion_facts)
                diagnostics["transition_judge_calls"] += 1
                try:
                    remaining = min(
                        NUMERIC_V2_TRANSITION_JUDGE_TIMEOUT_SECONDS,
                        NUMERIC_V2_REVIEW_BUDGET_SECONDS - diagnostics["timings_ms"]["transition_judge_work"] / 1000.0,
                        model_deadline - time.monotonic() - 0.5,
                    )
                    if remaining <= 0:
                        raise NumericV2EvaluatorError("numeric_v2_transition_judge_timeout")
                    async with asyncio.timeout(remaining):
                        completion_review = await evaluator.validate_transition_offer(
                            engine=runtime.engine, session=completion_review_session,
                            message=turn.message,
                            actor_performance={**performance, "fact_candidates": list(completion_candidates)},
                            scene_complete=evaluation.scene_complete,
                            player_action_projection=outcome.ledger_event.get("player_action_projection"),
                            completion_fact_review_only=True, timeout_seconds=remaining,
                            require_offer_acceptance=performance.get("transition_offered") is True,
                        )
                except (TimeoutError, NumericV2EvaluatorError) as exc:
                    trace_event("completion_fact_review.failed", error_code=str(exc))
                    raise NumericV2ActorOutputError("numeric_v2_completion_fact_review_failed"
                        if reviewing_completion_facts else "numeric_v2_transition_review_failed") from exc
                finally:
                    if reviewing_completion_facts:
                        _add_elapsed_ms(diagnostics, "completion_fact_review_work", started_at)
                    _add_elapsed_ms(diagnostics, "transition_judge_work", started_at)
                trace_event("completion_fact_review.result", result=completion_review)
                if not (completion_review.body_violations
                        or (reviewing_completion_facts
                            and completion_review.offer_present and not completion_review.valid)):
                    break
                if (completion_attempt or diagnostics["semantic_rewrite_attempts"]
                        or outcome.session.status != "active"
                        or review_budget_effectively_exhausted()
                        or model_deadline - time.monotonic() <= required_review_reserve):
                    raise NumericV2ActorOutputError("numeric_v2_completion_fact_review_rejected"
                        if reviewing_completion_facts else "numeric_v2_transition_review_failed")
                diagnostics["semantic_rewrite_attempts"] += 1
                diagnostics["completion_fact_rewrite_attempts"] += int(reviewing_completion_facts)
                trace_event("completion_fact_review.rewrite", violations=completion_review.body_violations,
                            invalid_offer=completion_review.offer_present and not completion_review.valid)
                # 即使Review由测试/其他调用构造，也重新绑定本稿字段与完整违规组；
                # 定位只帮助编辑，不把否决稿的未定位部分视为已批准。
                issues = _verified_body_issues(
                    list(completion_review.body_issues), list(completion_review.body_violations),
                    {"actor_performance": str(performance.get("performance") or ""),
                     "scene_update": str(performance.get("scene_narration") or "")},
                )
                draft_repair_context = None
                if issues and len(issues) <= 2 and all(len(issue["quote"]) <= 40 for issue in issues):
                    draft_repair_context = {
                        "draft": {key: deepcopy(performance[key]) for key in (
                            "performance", "scene_narration", "suggested_inputs", "transition_offered",
                        ) if key in performance},
                        "issues": list(issues),
                        "body_violations": list(completion_review.body_violations),
                    }
                performance = await generate_actor_turn(outcome, completion_repair=True,
                    draft_repair_context=draft_repair_context, retry_hint=(
                    "这是唯一一次复核后的修复，上一稿未提交，不是继续演出。"
                    "从同一本轮玩家输入与已提交历史重新接话，不为完成目标补写操作、未知成功或后续承诺。"
                    "若提供draft_repair，它是未提交原稿及本稿冲突定位，仅作编辑参考；"
                    "优先修正定位内容及其真实依赖，保留可与原始证据相容的答复，不把整段已给的回应重置为等待。"
                    "未定位部分不等于已批准，修稿后正文、候选和推荐仍须重新完整核对。"
                    "承接玩家已做的合法动作、实际持物与所在位置，保留明确拒绝和条件；"
                    "你可直接回答、作自身反应或说明未知，本轮不必完成剧情。"
                    "scene_update只写确有前因的新变化，没有就省略；不能重做已结束事件。"
                    "此修稿先交付本轮合法回应，暂不提出跨幕邀请，transition_offer填空字符串，推荐不包含接受换幕。"
                    "出口条件成立后可在后续回合正常邀请；不替玩家接受，推荐也不能依赖虚构结果。"
                    "fact_candidates仅报告本稿实际演出的合法新结果，没有就填空数组，不能靠删候选掩盖错误正文。"
                ))
                performance = apply_deterministic_suggestion_filter(performance)
                # Late regeneration cannot inherit the first draft's zero-call
                # scene checks or optional contract verdict. Never retry again.
                leaked = tuple(dict.fromkeys((
                    *premature_target_markers(
                        runtime.engine, current.session, outcome, performance, player_input=turn.message),
                    *premature_target_scene_facts(
                        runtime.engine, current.session, outcome, performance, player_input=turn.message),
                )))
                if leaked:
                    trace_event("completion_fact_review.repair_target_leak", markers=list(leaked))
                    raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
                if module_options.get("review_contract"):
                    try:
                        violated = await verify_contract_boundaries(
                            node=runtime.engine.nodes[current.session.current_node_id],
                            actor_performance=performance, player_input=turn.message)
                    except NumericV2EvaluatorError as exc:
                        raise NumericV2ActorOutputError("numeric_v2_completion_fact_review_failed") from exc
                    if violated:
                        raise NumericV2ActorOutputError("numeric_v2_completion_fact_review_rejected")
                completion_candidates = _actor_completion_fact_candidates(
                    runtime.engine, performance,
                    _pending_completion_facts(runtime.engine, completion_review_session),
                )
                # The second iteration always reviews the replacement body,
                # even with no fact candidates; deleting metadata is no escape.
            # The native parser may also return new Review-derived facts. Only this request's original candidates are eligible here.
            approved_completion_candidates = tuple(
                candidate for candidate in completion_candidates
                if candidate in completion_review.fact_candidates
            )
            performance, removed = _drop_reported_unsafe_suggestions(
                performance, completion_review.unsafe_suggestion_indexes, review=completion_review,
            )
            diagnostics["unsafe_suggestions_removed"] += removed
            # 窄核对已独立判断本稿邀请及整组原按钮，不能只消费它的否决而丢弃合法的正向结论。
            # 仅暂存同稿、同出口的审批；不复用为固定旁白的完整 Review 权限。
            if (completion_review.offer_present and completion_review.valid
                    and completion_review.offer_kind == "invitation"
                    and completion_review.approved_suggestion_texts
                    and not completion_review.unsafe_suggestion_indexes
                    and performance.get("suggested_inputs")
                    and not current.session.transition_offered
                    and not diagnostics["evaluator_degraded"]
                    and evaluation.transition_intent != "reject"
                    and outcome.session.dialogue_policy != "forbidden"
                    and pending_transition_record(
                        current.session, ledger_events=current.ledger_events, include_withdrawn=True,
                    ) is None):
                # 本次核验只有生成前实际可用的出口。若新事实首次打开出口，先结算事实，
                # 下一回合/显式重邀再审邀请，不能把核验没见过的出口事后补成已获批。
                reviewed_route = runtime.engine.preview_route(
                    completion_review_session.current_node_id, completion_review_session.metrics,
                    facts=completion_review_session.story_state["facts"],
                )
                reviewed_target = runtime.engine.nodes.get(reviewed_route["target_node_id"]) if reviewed_route else None
                if (reviewed_route and reviewed_target is not None and reviewed_target.get("type") != "ending"
                        and reviewed_target.get("terminal") is not True):
                    completion_invitation_review = completion_review
                    completion_invitation_body = performance_fingerprint(performance)
                    completion_invitation_route = (reviewed_route["id"], reviewed_route["target_node_id"])

    # Modules-off/degraded compatibility may retain author text; normal invitations use the generated reviewed draft.
    # No further Actor calls; actual act change still requires explicit player acceptance next turn.
    conservative_invitation = (
        # Pausing model audits must not discard Actor's visible offer or require
        # a review receipt that this mode can never issue. Evaluator still checks
        # the next input; Runtime retains route, fact and acceptance boundaries.
        (numeric_v2_options.MODEL_REVIEWS_ENABLED and not module_options.get("review"))
        or not module_options.get("evaluator")
        or diagnostics["evaluator_degraded"] or diagnostics["semantic_review_fallback"]
        or authored_invitation_recovered
        or _pending_silent_receipt(current, include_withdrawn=True) is not None
    )
    departure_projection = normalize_player_action_projection(
        outcome.ledger_event.get("player_action_projection")
    )
    scene_records, _ = current_scene_records(current.session)
    scene_revisions = {record.get("revision") for record in scene_records}
    scene_offer_repair_attempted = any(
        event.get("result_revision") in scene_revisions and event.get("offer_repair_attempted") is True
        for event in current.ledger_events
    )
    program_offer_already_issued = any(
        event.get("result_revision") in scene_revisions
        and isinstance(event.get("program_invitation"), Mapping)
        for event in current.ledger_events
    )

    delivered_authored_offer = ""

    async def apply_completion_fallback_offer(
        completion_session: ScriptSessionV2, *, route_just_opened: bool = False,
    ) -> None:
        nonlocal performance, program_invitation_performance, reviewed_transition_offered, final_fixed_review
        nonlocal delivered_authored_offer

        # The new wire explicitly publishes an invitation or chooses none.
        # A route opened by this draft's results was unavailable to the Actor.
        # Only that case can use the existing authored fallback after an empty field.
        if actor_publishes_transition_offer and not route_just_opened:
            return
        completion_status = runtime.engine.completion_contract_satisfied(completion_session)
        completion_ready = completion_status is True or (
            completion_status is None
            and not numeric_v2_options.MODEL_REVIEWS_ENABLED
            and not diagnostics["evaluator_degraded"]
            and evaluation.scene_complete is True
            and performance.get("transition_offered") is not True
        )
        # With audits paused, settled author facts can provide the existing
        # concrete invitation even if Actor omits or mislabels it. The guards
        # below still preserve refusal, departure and actual player acceptance.
        if (not module_options.get("review") and module_options.get("evaluator")
                and not diagnostics["evaluator_degraded"] and numeric_v2_options.MODEL_REVIEWS_ENABLED):
            return
        fallback_route = (
            runtime.engine.preview_route(current.session.current_node_id, outcome.session.metrics,
                                         facts=outcome.session.story_state["facts"])
            if completion_ready
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
            _project_authored_transition_text(
                runtime.engine, outcome.session,
                str(fallback_contract.get("fallback_offer") or "").strip(),
            )
            if isinstance(fallback_contract, Mapping)
            else ""
        )
        fallback_accept_input = (
            _project_authored_transition_text(
                runtime.engine, outcome.session, str(fallback_contract.get("accept_input") or ""),
            ).strip() if isinstance(fallback_contract, Mapping) else ""
        )
        final_authored_offer = _authored_offer_is_canonical_content(performance, fallback_offer)
        if (
            not route_changed
            # After recovery request cancellation, deliver reviewed ordinary draft only, without adding invitations during reuse.
            and not diagnostics["recovered_ordinary_drafts_reused"]
            # After local invalid-invitation removal, do not immediately add unreviewed replacement; advance naturally next turn.
            and not diagnostics["invalid_offer_local_crops"]
            and not diagnostics["invalid_scene_update_local_crops"]
            and not current.session.transition_offered
            # Players may accept deferred old invitations later, but code cannot automatically repeat them each turn.
            and pending_transition_record(
                current.session, ledger_events=current.ledger_events, include_withdrawn=True,
            ) is None
            and not departure_projection.get("player_left_current_scene")
            and not departure_projection.get("player_departure_pending")
            and evaluation.transition_intent != "reject"
            and isinstance(fallback_target, Mapping)
            and fallback_target.get("type") != "ending"
            and fallback_target.get("terminal") is not True
            and fallback_offer
            and not (scene_offer_repair_attempted and is_pressure_only_offer(fallback_offer))
            and not is_placeholder_accept_input(fallback_accept_input)
            and (not conservative_invitation or (
                not program_offer_already_issued
                and fallback_accept_input
                and turn.message.strip() != fallback_accept_input
                # Existing author words can be followed by a withdrawal. Their
                # presence only blocks automatic reissuance; it never authorizes.
                and (final_authored_offer or (
                    not _authored_offer_visible(performance, fallback_offer)
                    and fallback_offer not in str(performance.get("performance") or "")
                ))
            ))
            and ((not module_options.get("review")) or (
                module_options.get("review")
                and final_fixed_review is not None
                and (not final_fixed_review.offer_present or (
                    conservative_invitation and final_authored_offer and final_fixed_review.valid
                ))
                and not final_fixed_review.body_violations
            ))
        ):
            visible_performance = str(performance.get("performance") or "").rstrip()
            if (conservative_invitation and not final_authored_offer) or (
                not conservative_invitation and fallback_offer not in visible_performance
            ):
                visible_performance = "\n".join(
                    item for item in (visible_performance, fallback_offer) if item
                )
            fallback_candidate = {
                **performance,
                "performance": visible_performance,
                "transition_offered": True,
            }
            # Revalidate author text using commit's speech contract; skip fallback if concatenation violates muting, bracket balance, or block count,
            # otherwise commit inevitably fails repeatedly under unchanged conditions.
            if valid_mixed_performance_policy(
                fallback_candidate,
                str(
                    outcome.ledger_event.get("performance_dialogue_policy")
                    or outcome.session.dialogue_policy
                ),
            ):
                if not module_options.get("review"):
                    performance = fallback_candidate
                    delivered_authored_offer = fallback_offer
                    if conservative_invitation:
                        program_invitation_performance = visible_performance
                    reviewed_transition_offered = True
                    diagnostics["completion_fallback_offer_applied"] += 1
                    trace_event(
                        "completion.fallback_offer_applied",
                        route_id=fallback_route.get("id"),
                        target_node_id=fallback_route.get("target_node_id"),
                    )
                else:
                    approved_body_review = final_fixed_review
                    if fallback_candidate == performance:
                        fallback_review = approved_body_review
                    else:
                        fallback_review = await review_transition_offer(
                            fallback_candidate, defer_offer_only_dispute=True)
                    if fallback_review.offer_present and fallback_review.valid and not fallback_review.body_violations:
                        delivered_authored_offer = fallback_offer
                        performance, removed = _drop_reported_unsafe_suggestions(
                            fallback_candidate, fallback_review.unsafe_suggestion_indexes, review=fallback_review,
                        )
                        diagnostics["unsafe_suggestions_removed"] += removed
                        if conservative_invitation:
                            program_invitation_performance = visible_performance
                        reviewed_transition_offered = True
                        diagnostics["completion_fallback_offer_applied"] += 1
                        trace_event(
                            "completion.fallback_offer_applied",
                            route_id=fallback_route.get("id"),
                            target_node_id=fallback_route.get("target_node_id"),
                        )
                    else:
                        # If author text fails, retain prior reviewed narrative and never apply new candidate fact/recommendation judgments to old draft.
                        final_fixed_review = approved_body_review
                        diagnostics["completion_fallback_offer_skipped"] += 1
                        trace_event("completion.fallback_offer_skipped", reason="review_rejected")
            else:
                diagnostics["completion_fallback_offer_skipped"] += 1
                trace_event(
                    "completion.fallback_offer_skipped",
                    reason="performance_policy_invalid",
                    route_id=fallback_route.get("id"),
                )

    # Preserve review-enabled ordering and approval of the full combined draft.
    if module_options.get("review"):
        await apply_completion_fallback_offer(current.session)

    # Accept confirmation only from final reviewed draft; failure, skipped review, or nonconfirmation never promotes candidates.
    # Operations reference original Evaluator-validated values; Review may select, never rewrite values or forge evidence.
    # Approval uses only original player text / committed facts, independently of narrative violations; fallback still records completed player actions,
    # or narrative acknowledges action while completion contract remains unmet, forcing repeats. Narrative-derived candidates remain blocked below.
    if evaluator_fact_claims and final_fixed_review is not None:
        approved_indexes = set(final_fixed_review.approved_evaluator_fact_indexes)
        approved_operations = tuple(
            {key: claim[key] for key in ("op", "key", "value", "visibility")}
            for index, claim in enumerate(evaluator_fact_claims) if index in approved_indexes
        )
        if approved_operations:
            outcome = runtime.engine.finalize_fact_operations(
                current.session, outcome, operations=approved_operations,
            )
            diagnostics["evaluator_fact_claims_approved"] = len(approved_operations)
        trace_event("evaluator.fact_claims_reviewed", proposed=len(evaluator_fact_claims),
                    approved_operations=approved_operations)

    # Full Review owns its final candidates; otherwise only the narrow check's exact approved Actor originals may add facts.
    actor_fact_candidates = performance.pop("fact_candidates", [])
    published_offer = performance.pop("_published_transition_offer", "")
    possible_ending_targets = {
        gate["target_node_id"] for gate in source_node["route_gates"]
        if runtime.engine.nodes[gate["target_node_id"]].get("type") == "ending"
        or runtime.engine.nodes[gate["target_node_id"]].get("terminal") is True
    } if published_offer and not route_changed and runtime.engine.node_uses_fact_routes(source_node) else set()
    body_without_offer = None
    fact_evidence_performance = performance
    if published_offer and not route_changed:
        local_body = _without_standalone_transition_offer(
            str(performance.get("performance") or ""), published_offer,
            dialogue_policy=outcome.ledger_event["performance_dialogue_policy"],
        )
        if local_body is not None:
            body_without_offer = {**performance, "performance": local_body,
                                  "transition_offered": False, "suggested_inputs": []}
            # Do not accept evidence that could remove its own visible source.
            # A route eligible without facts survives Actor fact settlement.
            if (possible_ending_targets
                    or (actor_draft_route is not None and offer_verdicts.get(
                        (performance_fingerprint(performance), actor_draft_route["id"], published_offer, "")) is False)
                    or (_silent_exit_allowed(source_node) and any(
                        gate["transition_contract"].get("silent_choice") for gate in source_node["route_gates"]
                    ))
                    or runtime.engine.preview_route(source_node["id"], outcome.session.metrics) is None):
                fact_evidence_performance = body_without_offer
    review_fact_candidates = (
        list(final_fixed_review.fact_candidates)
        if final_fixed_review is not None and not final_fixed_review.body_violations
        else []
    )
    diagnostics["review_fact_candidates_proposed"] = len(review_fact_candidates)
    proposed_fact_candidates: list[Mapping[str, Any]] = []
    actor_completion_keys: set[str] = set()
    merged_discovery_approval: CompletionFactBatch | None = None
    selected_fact_candidates = (
        review_fact_candidates if module_options.get("review")
        else actor_fact_candidates if isinstance(actor_fact_candidates, list) else []
    )
    for candidate in selected_fact_candidates:
        if not isinstance(candidate, Mapping):
            continue
        proposed_fact_candidates.append(candidate)
    if body_without_offer is not None and fact_evidence_performance is performance:
        candidate_keys = {candidate.get("key") for candidate in proposed_fact_candidates}
        if any(row.get("type") == "fact_equals" and row.get("key") in candidate_keys
               for gate in source_node["route_gates"]
               for row in gate["conditions"].get("any", gate["conditions"].get("all", ()))):
            # A draft's unsupported invitation cannot justify the fact that reselects its own route.
            fact_evidence_performance = body_without_offer
    bound_review = reviewed_source_completion(fact_evidence_performance) if not route_changed else None
    if bound_review is not None:
        batch, indexes = bound_review
        approved = batch.select(indexes)
        if approved.claims:
            outcome = runtime.engine.finalize_fact_operations(current.session, outcome, operations=approved.operations)
            approved_completion_facts = (*approved_completion_facts, *approved.claims)
            approved_keys = {claim["key"] for claim in approved.claims}
            approved_delivery_claims = tuple(claim for claim in deferred_delivery_claims if claim["key"] in approved_keys)
            approved_delivery_fingerprint = performance_fingerprint(performance)
        proposed_fact_candidates = [candidate for candidate in proposed_fact_candidates if not any(
            candidate.get("key") == claim["key"] and type(candidate.get("value")) is type(claim["value"])
            and candidate.get("value") == claim["value"] for claim in batch.claims)]
        diagnostics["source_completion_reviews_merged"] = 1
        trace_event("story_evidence.source_delivery_reviewed", proposed=len(batch.claims),
                    approved_keys=[claim["key"] for claim in approved.claims], review_mode="contract_combined")
    if proposed_fact_candidates:
        # Every candidate still gets full allowlist/type/verbatim-evidence validation; one invalid candidate excludes only itself,
        # retaining valid facts in same batch. All accepted items remain in uncommitted outcome until one atomic turn commit.
        before_actor_facts = outcome
        independently_review_actor_facts = (
            not numeric_v2_options.MODEL_REVIEWS_ENABLED
            and not module_options.get("review") and bool(module_options.get("evaluator"))
        )
        actor_fact_audit: list[dict[str, Any]] = []
        committed_facts = current.session.story_state.get("facts")
        if not isinstance(committed_facts, Mapping):
            committed_facts = {}
        ending_targets = None
        if route_changed and not module_options.get("review"):
            # New terminal Actor candidates can cite only the final source response.
            # The ending already selected by Runtime stays fixed during settlement.
            ending_targets = {row["key"]: row["value"]
                              for row in runtime.engine.pending_natural_ending_fact_targets(outcome)}
            fact_evidence_performance = next(
                (segment for segment in performance.get("segments", ()) if segment.get("phase") == "source_response"),
                {},
            )
        actor_evidence = _actor_fact_evidence_text(fact_evidence_performance)
        expand_grouped_quote = (not route_changed and not module_options.get("review")
                                and not numeric_v2_options.MODEL_REVIEWS_ENABLED
                                and turn.input_source in {"freeform", "suggestion"})
        player_fact_targets = (
            _pending_completion_facts(runtime.engine, current.session)
            if (not route_changed and not diagnostics.get("ordinary_turn_combined")
                and module_options.get("evaluator") and not module_options.get("review")
                and not numeric_v2_options.MODEL_REVIEWS_ENABLED
                and turn.input_source in {"freeform", "suggestion"}) else ()
        )
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
            if expand_grouped_quote:
                candidate = _expand_grouped_actor_fact_quote(
                    candidate, runtime.engine.fact_contract.get(key, {}), fact_evidence_performance,
                    player_input=turn.message, published_offer=published_offer,
                    recover_formatting=independently_review_actor_facts and any(
                        target["key"] == key and type(target["value"]) is type(value) and target["value"] == value
                        for target in _pending_completion_facts(runtime.engine, current.session)))
            quote = candidate.get("evidence_quote")
            quote = quote.strip() if isinstance(quote, str) else ""
            try:
                if ending_targets is not None and (key not in ending_targets
                        or type(value) is not type(ending_targets[key]) or value != ending_targets[key]):
                    raise NumericV2RuntimeError("actor_fact_candidate_value_not_allowed")
                if (player_fact_targets and quote and quote in turn.message and quote not in actor_evidence
                        and current_turn_operation is None
                        and (not completion_source_restricted
                             or runtime.engine.fact_contract.get(key, {}).get("evidence_source") == "player_input")
                        and runtime.engine.fact_contract.get(key, {}).get("evidence_source") != "performance"
                        and any(target["key"] == key and type(target["value"]) is type(value)
                                and target["value"] == value for target in player_fact_targets)):
                    # A standalone draft can put current player evidence in its
                    # body-only bucket. Recover the fact without rerouting this turn.
                    normalized, = _normalize_actor_fact_candidates(
                        [candidate], fact_contract=runtime.engine.fact_contract, allowed_keys={key})
                    normalized["evidence"][0]["source"] = "player_input"
                    recovered = _parse_evaluation_output(json.dumps({
                        "scene_complete": False, "metric_changes": {}, "fact_candidates": [normalized],
                    }), runtime.engine, turn.message, current.session)
                    if not recovered.fact_operations:
                        diagnostics["fact_candidates_rejected"] += 1
                        continue
                    candidate_outcome = runtime.engine.finalize_fact_operations(
                        current.session, outcome, operations=recovered.fact_operations)
                    candidate_audit = recovered.fact_audit
                else:
                    try:
                        candidate_outcome, candidate_audit = runtime.engine.finalize_actor_fact_candidates(
                            current.session, outcome, candidates=[candidate],
                            evidence_sources={"actor_performance": actor_evidence},
                            defer_actor_completion_review=independently_review_actor_facts,
                        )
                    except NumericV2RuntimeError as exc:
                        definition = runtime.engine.fact_contract.get(key, {})
                        if (str(exc) != "story_fact_candidate_evidence_anchor_missing"
                                or not independently_review_actor_facts or route_changed
                                or definition.get("evidence_source") != "performance"
                                or not any(target["key"] == key and type(target["value"]) is type(value)
                                    and target["value"] == value
                                    for target in _pending_completion_facts(runtime.engine, current.session))):
                            raise
                        # Only the existing independent review may approve a semantic synonym.
                        # Recheck every other gate against a local copy; the author contract stays strict.
                        normalized = _normalize_actor_fact_candidates(
                            [candidate], fact_contract=runtime.engine.fact_contract, allowed_keys={key})
                        operations, candidate_audit = validate_fact_candidates(normalized,
                            fact_contract={"facts": {key: {field: item for field, item in definition.items()
                                                          if field != "evidence_anchor"}}},
                            evidence_sources={"actor_performance": actor_evidence})
                        candidate_outcome = runtime.engine.finalize_fact_operations(current.session, outcome, operations=operations)
                        trace_event("fact_candidates.semantic_admission", key=key, reason=str(exc))
            except (NumericV2RuntimeError, NumericV2EvaluatorError) as exc:
                # Rejection reasons log fields/error codes only, never candidate narrative in log summaries.
                diagnostics["fact_candidates_rejected"] += 1
                trace_event("fact_candidates.rejected", key=key, reason=str(exc))
            else:
                if (numeric_v2_options.MODEL_REVIEWS_ENABLED and not module_options.get("review")
                        and candidate not in approved_completion_candidates):
                    diagnostics["fact_candidates_rejected"] += 1
                    trace_event("fact_candidates.rejected", key=key, reason="completion_not_approved")
                    continue
                outcome = candidate_outcome
                actor_fact_audit.extend(candidate_audit)
                actor_completion_keys.update(item["key"] for item in candidate_audit)
        if actor_fact_audit and independently_review_actor_facts:
            # Validate the original candidates against the final draft and player evidence.
            # Keep the draft itself when only its proposed state writes lack proof.
            operations = {item["key"]: item for item in outcome.ledger_event.get("fact_operations", ())}
            claims = tuple({**item, **operations[item["key"]],
                            "description": runtime.engine.fact_contract[item["key"]]["description"]}
                           for item in actor_fact_audit)
            combined_batch = None
            omitted_batch = discovered_completion_facts.get(performance_fingerprint(fact_evidence_performance))
            if (not route_changed and omitted_batch is not None
                    and completion_evidence_matches(omitted_batch, completion_packet(fact_evidence_performance))):
                excluded = actor_completion_keys | set(operations)
                omitted_batch = omitted_batch.select(index for index, claim in enumerate(omitted_batch.claims)
                    if claim["key"] not in excluded)
                if omitted_batch.claims:
                    try:
                        combined_batch = combine_actor_claims(runtime.engine, omitted_batch, claims)
                    except NumericV2RuntimeError as exc:
                        # Unbindable or oversized candidates retain their existing separate reviews.
                        trace_event("fact_discovery.merge_skipped", reason=str(exc))
            try:
                review = await review_completion_evidence(
                    combined_batch.claims if combined_batch is not None else claims, actor_evidence=actor_evidence,
                    **({"evidence_batch": combined_batch, "separate_on_input_over_budget": True}
                       if combined_batch is not None else {}))
            except NumericV2EvaluatorError as exc:
                if str(exc) != "numeric_v2_story_evidence_input_over_budget":
                    raise
                trace_event("fact_discovery.merge_skipped", reason=str(exc))
                combined_batch = None
                review = await review_completion_evidence(claims, actor_evidence=actor_evidence)
            indexes = review.approved_indexes
            if combined_batch is not None:
                merged_discovery_approval = omitted_batch.select(index - len(claims) for index in indexes
                    if index >= len(claims))
                indexes = tuple(index for index in indexes if index < len(claims))
                diagnostics["completion_fact_reviews_merged"] = 1
            diagnostics["fact_candidates_rejected"] += len(actor_fact_audit) - len(indexes)
            actor_fact_audit = [item for index, item in enumerate(actor_fact_audit) if index in indexes]
            outcome = before_actor_facts
            if actor_fact_audit:
                outcome = runtime.engine.finalize_fact_operations(current.session, outcome,
                    operations=tuple(operations[item["key"]] for item in actor_fact_audit))
            trace_event("story_evidence.actor_facts_reviewed", proposed=len(claims), approved_indexes=indexes)
        if actor_fact_audit:
            diagnostics["fact_candidates_accepted"] += len(actor_fact_audit)
            trace_event("fact_candidates.accepted", audit=actor_fact_audit)
    discovery_collection_open = False
    if (not route_changed and not numeric_v2_options.MODEL_REVIEWS_ENABLED
            and module_options.get("evaluator") and not module_options.get("review")):
        batch = discovered_completion_facts.get(performance_fingerprint(fact_evidence_performance))
        excluded = actor_completion_keys | {row["key"] for row in outcome.ledger_event.get("fact_operations", ())}
        if batch is not None and completion_evidence_matches(batch, completion_packet(fact_evidence_performance)):
            batch = batch.select(index for index, claim in enumerate(batch.claims) if claim["key"] not in excluded)
            if batch.claims:
                if merged_discovery_approval is not None:
                    approved = merged_discovery_approval
                else:
                    review = await review_completion_evidence(batch.claims,
                        actor_evidence=_actor_fact_evidence_text(fact_evidence_performance), evidence_batch=batch)
                    approved = batch.select(review.approved_indexes)
                if approved.claims:
                    outcome = runtime.engine.finalize_fact_operations(current.session, outcome, operations=approved.operations)
                    adopted_completion_facts = approved
                trace_event("fact_discovery.reviewed", proposed=len(batch.claims), approved=approved.operations)
    if deferred_delivery_claims and not route_changed:
        existing = {row["key"] for row in outcome.ledger_event.get("fact_operations", ())}
        if bound_review is not None:
            existing.update(claim["key"] for claim in bound_review[0].claims)
        source_evidence = _actor_fact_evidence_text(performance)
        batch = bind_source_delivery(runtime.engine,
            tuple(claim for claim in deferred_delivery_claims if claim["key"] not in existing),
            completion_packet(performance), restrict_player_results=completion_source_restricted)
        claims = batch.claims
        if claims:
            review = await review_completion_evidence(claims, actor_evidence=source_evidence, evidence_batch=batch)
            approved = tuple(claim for index, claim in enumerate(claims) if index in review.approved_indexes)
            if approved:
                outcome = runtime.engine.finalize_fact_operations(current.session, outcome, operations=tuple(
                    {field: claim[field] for field in ("op", "key", "value", "visibility")} for claim in approved))
                approved_completion_facts = (*approved_completion_facts, *approved)
                approved_keys = {claim["key"] for claim in approved}
                approved_delivery_claims = tuple(claim for claim in deferred_delivery_claims
                                                 if claim["key"] in approved_keys)
                approved_delivery_fingerprint = performance_fingerprint(performance)
            trace_event("story_evidence.source_delivery_reviewed", proposed=len(claims),
                        approved_keys=[claim["key"] for claim in approved])
    # Source-first delivery can complete an automatic exit in this same user turn.
    # Recompute from the original revision, never apply metrics to the staged outcome twice.
    if (not route_changed and not numeric_v2_options.MODEL_REVIEWS_ENABLED
            and module_options.get("evaluator") and not module_options.get("review")
            and runtime.engine.node_has_completion_advance(source_node)
            and outcome.ledger_event.get("fact_operations")
            and evaluation.transition_intent != "reject"
            and runtime.engine.completion_contract_satisfied(outcome.session) is True):
        operations = tuple(outcome.ledger_event["fact_operations"])
        continued = prepare_turn(replace(evaluation, fact_operations=operations))
        if continued.route is not None:
            reviewed_transition_source = deepcopy(performance)
            reviewed_transition_player_absence = contract_player_absence.get(contract_key(performance), ())
            outcome = continued
            route_changed = True
            trace_event("runtime.source_delivery_prepared", state=trace_state(outcome.session),
                        ledger_event=outcome.ledger_event)
            performance = await generate_transition_or_recover(transition_source=reviewed_transition_source)
            structural_problems = transition_bridge_leaks(performance)
            terminal_problems = await review_terminal_question_markers(performance)
            performance, problems = await checked_delivery(performance)
            if is_authored_recovery(performance):
                structural_problems = transition_bridge_leaks(performance)
                terminal_problems = await review_terminal_question_markers(performance)
            if structural_problems or terminal_problems or problems:
                if diagnostics["semantic_rewrite_attempts"] or recovery_author_original_conflict:
                    raise NumericV2ActorOutputError("numeric_v2_contract_violation")
                diagnostics["semantic_rewrite_attempts"] += 1
                findings = contract_findings.get(contract_key(performance), [])
                performance = await generate_transition_or_recover(transition_source=reviewed_transition_source,
                    draft_repair_context={"draft": _transition_repair_draft(performance,
                        draft_provenance=actor_draft_provenance.get(performance_fingerprint(performance))), "issues": findings}
                        if findings else None,
                    retry_hint="只修正桥段和目标开场的阶段、主体与作者边界，已审来源段保持原文。")
                await verify_later_transition_draft(performance, stage="source_delivery_tail_rewrite")
                performance, problems = await checked_delivery(performance)
                if problems:
                    recovered = recover_authored_transition("source_tail_rewrite_exhausted")
                    if recovered is None:
                        raise NumericV2ActorOutputError("numeric_v2_contract_violation")
                    performance, problems = await checked_delivery(recovered)
                    if problems:
                        raise NumericV2ActorOutputError("numeric_v2_contract_violation")
    if outcome.session.status == "ended" and any(
        outcome.session.story_state["facts"].get(key, {}).get("value") is not True for key in required_handoff_keys
    ):
        raise NumericV2ActorOutputError("numeric_v2_actor_player_handoff_unverified")
    acceptance_route = runtime.engine.preview_route(
        outcome.session.current_node_id, outcome.session.metrics, facts=outcome.session.story_state["facts"],
    )
    acceptance_contract = acceptance_route["transition_contract"] if acceptance_route else {}
    silent_target = runtime.engine.nodes.get(acceptance_route["target_node_id"]) if acceptance_route else None
    silent_exit = (
        not route_changed and _silent_exit_allowed(source_node)
        and not acceptance_contract.get("fallback_offer")
        and isinstance(acceptance_contract.get("silent_choice"), Mapping)
        and isinstance(silent_target, Mapping) and silent_target.get("type") != "ending"
        and silent_target.get("terminal") is not True
    )
    ending_exit = bool(acceptance_route and acceptance_route["target_node_id"] in possible_ending_targets)
    superseded_actor_offer = (silent_exit or ending_exit) and body_without_offer is not None
    if superseded_actor_offer:
        performance = body_without_offer
        reviewed_transition_offered = False
    draft_route_reselected = (
        actor_draft_route is not None and acceptance_route is not None
        and actor_draft_route["id"] != acceptance_route["id"]
    )
    exact_final_offer = (
        published_offer and draft_route_reselected
        and published_offer == _project_authored_transition_text(
            runtime.engine, outcome.session, str(acceptance_contract.get("fallback_offer") or ""),
        ).strip()
    )
    repeated_entry_offer = (
        published_offer and not route_changed
        and any(published_offer in text for text in current_entry_fixed_narrations(current.session))
    )
    if not route_changed and (
        acceptance_route is None or (draft_route_reselected and not exact_final_offer) or repeated_entry_offer
    ):
        # The selected exit can disappear during same-turn fact settlement. Strip
        # only a proven standalone offer; an embedded quote may be valid narrative.
        invalid_offer = bool(published_offer or performance.get("transition_offered") is True or reviewed_transition_offered)
        if invalid_offer:
            performance = body_without_offer or {**performance, "transition_offered": False, "suggested_inputs": []}
            reviewed_transition_offered = False
        keep_local_suggestions = (
            acceptance_route is None and actor_draft_route is None and not invalid_offer
            and outcome.session.status == "active" and not current.session.transition_offered
            and _allows_local_suggestions_before_ending(runtime.engine, source_node, outcome.session.dialogue_policy)
            and runtime.engine.preview_route(
                source_node["id"], current.session.metrics, facts=current.session.story_state["facts"],
            ) is None
        )
        if not repeated_entry_offer and not keep_local_suggestions:
            performance = {**performance, "suggested_inputs": []}
        if acceptance_route is None or draft_route_reselected:
            invalidate_previous_offer = invalidate_previous_offer or current.session.transition_offered
    if not module_options.get("review"):
        # Admit facts from Actor's delivered body before appending author prose; no extra model call or early commit.
        completion_session = replace(outcome.session, performance_history=(
            *current.session.performance_history, performance))
        # 新事实可能改选出口。仅同稿且结算后仍为原出口时承接已审邀请，避免再拼一次作者邀约。
        completion_invitation_ready = bool(
            acceptance_route and completion_invitation_route == (acceptance_route["id"], acceptance_route["target_node_id"])
            and completion_invitation_body == performance_fingerprint(performance)
        )
        if not completion_invitation_ready:
            await apply_completion_fallback_offer(
                completion_session,
                route_just_opened=(actor_draft_route is None and acceptance_route is not None
                                   and actor_publishes_transition_offer),
            )
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

    # Latch new proposals per narrative review; final-draft fallback may commit narrative but never reviewed-invalid invitations.
    # Runtime already computed transition lifecycle from this Evaluator result, especially retaining old proposals on unclear;
    # Actor false must not overwrite Runtime true or next Evaluator loses visible proposal.
    # Workflow submits reviewed or explicit-fallback new-proposal signal; Runtime decides old-state retention and synchronization of all three records.
    filtered_performance = apply_deterministic_suggestion_filter(performance)
    new_offer = (
        performance.get("transition_offered") is True
        or reviewed_transition_offered
    )
    authored_accept_input = (
        _project_authored_transition_text(
            runtime.engine, outcome.session,
            str(acceptance_contract.get("accept_input") or "").strip(),
        )
        if isinstance(acceptance_contract, Mapping)
        else ""
    )
    authored_offer = _project_authored_transition_text(
        runtime.engine, outcome.session,
        str((acceptance_contract or {}).get("fallback_offer") or ""),
    ).strip()
    authored_offer_visible = _authored_offer_visible(filtered_performance, authored_offer)
    # 最终过滤后再次绑定正文和路线；后加旁白、改稿或换出口不能继承先前的邀请审批。
    completion_invitation_applied = bool(
        completion_invitation_ready and acceptance_route
        and completion_invitation_route == (acceptance_route["id"], acceptance_route["target_node_id"])
        and completion_invitation_body == performance_fingerprint(filtered_performance)
    )
    if completion_invitation_applied:
        conservative_invitation = False
        new_offer = reviewed_transition_offered = True
    if conservative_invitation:
        new_offer = bool(program_invitation_performance is not None and authored_accept_input
                         and filtered_performance.get("performance") == program_invitation_performance)
        reviewed_transition_offered = new_offer
        filtered_performance = {**filtered_performance, "transition_offered": new_offer}
        if new_offer:
            # Only the signed acceptance input is actionable in this contract;
            # Actor alternatives must not look like equivalent route consent.
            filtered_performance["suggested_inputs"] = [authored_accept_input]
        # A later free-form response may retract the old offer. Without a
        # successful semantic review it cannot carry that authorization forward.
        # A completed transition already consumed it; target suggestions are not old-offer acceptance buttons.
        if (module_options.get("review")
                or outcome.ledger_event["from_node_id"] == outcome.ledger_event["to_node_id"]):
            invalidate_previous_offer = invalidate_previous_offer or current.session.transition_offered
            if not new_offer and (current.session.transition_offered
                                  or performance.get("transition_offered") is True
                                  or authored_offer_visible):
                filtered_performance["suggested_inputs"] = []
    reviewed_acceptance: tuple[str, str, str] | None = None
    # Body approval and an actionable exit are separate. Retain a reviewed
    # future proposal when only its routing affordance is denied.
    if (new_offer and acceptance_route and not route_changed and module_options.get("review_contract")
            and not module_options.get("review") and not numeric_v2_options.MODEL_REVIEWS_ENABLED):
        final_offer = authored_offer if conservative_invitation else published_offer or authored_offer
        if final_offer and _authored_offer_visible(filtered_performance, final_offer):
            if await delivery_contract_problems(filtered_performance, offer_text=final_offer, offer_route=acceptance_route):
                raise NumericV2ActorOutputError("numeric_v2_contract_violation")
            offer_key = (performance_fingerprint(filtered_performance), acceptance_route["id"], final_offer, "")
            if offer_verdicts.get(offer_key) is False:
                trace_event("transition.offer_not_actionable", route_id=acceptance_route["id"], offer=final_offer)
                new_offer = reviewed_transition_offered = False
                filtered_performance["transition_offered"] = False
                local_body = _without_standalone_transition_offer(
                    str(filtered_performance.get("performance") or ""), final_offer,
                    dialogue_policy=outcome.ledger_event["performance_dialogue_policy"],
                )
                filtered_performance["suggested_inputs"] = []
                repair_budget = min(7.5, model_deadline - time.monotonic() - 0.5)
                if (
                    local_body is not None and not conservative_invitation and repair_budget > 0
                    and completion_status is True and evaluation.transition_intent != "reject"
                    and not current.session.transition_offered and not review_budget_effectively_exhausted()
                    and not acceptance_contract.get("advance_on_completion")
                    and turn.input_source in {"freeform", "suggestion"}
                    and not departure_projection.get("player_left_current_scene")
                    and not departure_projection.get("player_departure_pending")
                    and not required_pending(source_node, outcome.session, condition_triggers_enabled=False)
                    and not _adds_unconfirmed_prerequisite(turn.message, final_offer)
                    and not is_explicit_transition_deferral(turn.message)
                    and not scene_offer_repair_attempted
                ):
                    outcome = replace(outcome, ledger_event={**outcome.ledger_event, "offer_repair_attempted": True})
                    diagnostics["offer_repair_attempts"] = 1
                    repair_started = time.monotonic()
                    try:
                        async with asyncio.timeout(repair_budget):
                            route_plan = _project_authored_transition_text(runtime.engine, outcome.session,
                                str(acceptance_contract.get("reason") or ""))
                            repaired = await evaluator.repair_public_offer(
                                route_plan=route_plan, player_input=turn.message, visible_body=local_body,
                                rejected_offer=final_offer, timeout_seconds=repair_budget,
                                input_budget=numeric_v2_actor_budget(current.session.actor_budget_profile)["evaluator_input_max_tokens"],
                            )
                            offer, accept = repaired["offer"], repaired["accept_input"]
                            suggestions = list(dict.fromkeys(text for text in (accept, repaired["stay_input"])
                                if text and text != turn.message.strip() and not is_placeholder_accept_input(text)))
                            # Replace the cleared body only after this changed
                            # proposal and its acceptance are independently approved.
                            repaired_performance = {
                                **filtered_performance, "performance": local_body + "\n" + offer,
                                "suggested_inputs": suggestions, "transition_offered": True,
                            }
                            if (offer and offer != final_offer and offer not in local_body and not is_pressure_only_offer(offer)
                                    and suggestions and suggestions[0] == accept
                                    and valid_mixed_performance_policy(repaired_performance, outcome.ledger_event["performance_dialogue_policy"])):
                                for text in suggestions:
                                    TurnRequestV2.from_mapping({"client_turn_id": "offer_repair_validation",
                                        "base_revision": current.session.revision, "message": text, "input_source": "suggestion"})
                                repaired_key = (performance_fingerprint(repaired_performance), acceptance_route["id"], offer, accept)
                                if (not await delivery_contract_problems(repaired_performance, offer_text=offer,
                                        offer_route=acceptance_route, offer_accept_input=accept)
                                        and offer_verdicts.get(repaired_key) is True
                                        and accept not in contract_unsafe_suggestions.get(contract_key(repaired_performance), ())):
                                    filtered_performance = repaired_performance
                                    published_offer = offer
                                    reviewed_acceptance = (repaired_key[0], offer, accept)
                                    authored_offer_visible = False
                                    new_offer = reviewed_transition_offered = True
                                    diagnostics["offer_repair_published"] = 1
                                    trace_event("transition.offer_repair_published", route_id=acceptance_route["id"], offer=offer)
                    except (TimeoutError, NumericV2RuntimeError, NumericV2ActorOutputError, NumericV2EvaluatorError) as exc:
                        trace_event("transition.offer_repair_skipped", reason=str(exc))
                    finally:
                        _add_elapsed_ms(diagnostics, "offer_repair_work", repair_started)
    semantically_verified_offer = bool(
        module_options.get("review") and module_options.get("evaluator")
        and not diagnostics["evaluator_degraded"]
        and outcome.session.dialogue_policy != "forbidden"
    )
    if (not module_options.get("review") and not completion_invitation_applied and reviewed_transition_offered
            and (authored_offer_visible or semantically_verified_offer)):
        filtered_performance, acceptance_inserted = (
            _insert_verified_offer_acceptance_suggestion(
                filtered_performance,
                accept_input=authored_accept_input,
                consumed_inputs=(turn.message,),
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
        player_input=turn.message,
        keep_pending=(
            not module_options.get("review")
            and current.session.transition_offered
            # reject/unreachable acceptance makes Runtime clear old invitation; reuse only invitation buttons still pending this turn.
            and outcome.session.transition_offered
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
    if completion_invitation_applied and completion_invitation_review is not None:
        # 只交付这份审批明确允许、且最终仍存在的原按钮，不补未审的作者接受文本。
        filtered_performance, removed = _drop_reported_unsafe_suggestions(
            filtered_performance, completion_invitation_review.unsafe_suggestion_indexes,
            review=completion_invitation_review,
        )
        diagnostics["unsafe_suggestions_removed"] += removed
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
    if final_fixed_review is not None:
        performance, removed = _drop_undelivered_display_suggestions(
            performance, final_fixed_review, node_id=current.session.current_node_id,
        )
        diagnostics["unsafe_suggestions_removed"] += removed
        # Final concatenation cannot bypass recommendation review or resurrect rejected groups through author buttons.
        reviewed_texts = (
            set(final_fixed_review.approved_suggestion_texts) | {
                item["text"] for item in final_fixed_review.display_dependent_suggestions
            } if final_fixed_review.approved_suggestion_texts is not None and not final_fixed_review.unsafe_suggestion_indexes else set()
        )
        suggestions = list(performance.get("suggested_inputs") or [])
        performance["suggested_inputs"] = [item for item in suggestions if item in reviewed_texts]
        diagnostics["unsafe_suggestions_removed"] += len(suggestions) - len(performance["suggested_inputs"])
    # Fixed originals and local edits must not bypass the exact-body contract verdict.
    # A first recovery here could discard the visible source of facts already
    # accepted above. Only an earlier, exact private receipt may skip this audit.
    if await delivery_contract_problems(performance):
        raise NumericV2ActorOutputError("numeric_v2_contract_violation")
    unsafe = contract_unsafe_suggestions.get(contract_key(performance))
    if unsafe:
        suggestions = list(performance.get("suggested_inputs") or [])
        performance["suggested_inputs"] = [text for text in suggestions if text not in unsafe]
        diagnostics["unsafe_suggestions_removed"] += len(suggestions) - len(performance["suggested_inputs"])
    if performance.get("suggested_inputs"):
        suggestions = list(performance["suggested_inputs"])
        performance["suggested_inputs"] = _drop_completed_action_suggestions(suggestions, runtime.engine, outcome.session)
        removed = len(suggestions) - len(performance["suggested_inputs"])
        if removed:
            diagnostics["completed_action_suggestions_removed"] = removed
    if (not new_offer and not route_changed and performance.get("suggested_inputs")
            and any(block["type"] == "dialogue" and ("？" in block["text"] or "?" in block["text"])
                    for block in mixed_performance_blocks(turn.message))):
        # Do not prompt the player to submit the exact question just asked.
        performance["suggested_inputs"] = [
            suggestion for suggestion in performance["suggested_inputs"]
            if suggestion.strip() != turn.message.strip()
        ]
    if conservative_invitation and new_offer:
        visible_blocks = performance_content_blocks(performance)
        issued_blocks = mixed_performance_blocks(program_invitation_performance)
        if (performance.get("performance") != program_invitation_performance
                or authored_accept_input not in performance.get("suggested_inputs", [])
                or not issued_blocks or visible_blocks[-len(issued_blocks):] != issued_blocks):
            # Later fixed narration cannot inherit the program-issued receipt.
            ledger_event = {**outcome.ledger_event}
            ledger_event.pop("transition_offer_presented", None)
            outcome = replace(outcome, ledger_event=ledger_event)
            outcome, performance = runtime.engine.finalize_transition_offer_state(
                outcome, {**performance, "suggested_inputs": []},
                new_offer=False, invalidate_previous_offer=True)
        else:
            outcome = replace(outcome, ledger_event={
                **outcome.ledger_event,
                "program_invitation": {
                    "route_id": acceptance_route["id"], "offer": authored_offer,
                    "accept_input": authored_accept_input,
                    "performance": program_invitation_performance,
                    "visible_blocks": visible_blocks,
                },
            })
    # Persist the reviewed origin before a program button can change the offer flags.
    performance.pop("transition_offer_source", None)
    if (performance.get("transition_offer_presented") is True and (
            completion_invitation_applied or (final_fixed_review is not None
                and final_fixed_review.offer_present and final_fixed_review.valid
                and not final_fixed_review.body_violations))):
        performance["transition_offer_source"] = "reviewed"
        outcome = replace(outcome, ledger_event={**outcome.ledger_event, "transition_offer_source": "reviewed"})
    if (completion_invitation_applied and completion_invitation_review.offer_acceptance_valid
            and performance.get("suggested_inputs")
            and performance["suggested_inputs"][0] in (completion_invitation_review.approved_suggestion_texts or ())):
        reviewed_acceptance = (
            completion_invitation_body, completion_invitation_review.offer_quote, performance["suggested_inputs"][0],
        )
    if reviewed_acceptance is not None and performance.get("transition_offer_presented") is True:
        reviewed_body, offer_quote, accept_input = reviewed_acceptance
        # Fact settlement can open an exit this turn. Bind only the reviewed, delivered offer and first button to it.
        if (performance_fingerprint(performance) == reviewed_body
                and performance.get("suggested_inputs", [])[:1] == [accept_input]
                and _authored_offer_visible(performance, offer_quote)):
            outcome = replace(outcome, ledger_event={**outcome.ledger_event, "program_invitation": {
                "kind": "reviewed_dynamic", "revision": outcome.session.revision,
                "route_id": acceptance_route["id"], "target_node_id": acceptance_route["target_node_id"],
                "offer": offer_quote, "accept_input": accept_input,
                "performance": performance["performance"], "visible_blocks": performance_content_blocks(performance),
            }})
    # The button is a program choice, not new character prose or a model verdict.
    # Run after facts, narration and recommendation filtering are final.
    silent_completion_session = replace(outcome.session, performance_history=(
        *current.session.performance_history, performance))
    pending_origin = (
        pending_transition_record(current.session, ledger_events=current.ledger_events, include_withdrawn=True)
        if silent_exit else None
    )
    orphaned_gesture_offer = (
        current.session.transition_offered and current.session.dialogue_policy == "forbidden"
        and pending_origin is not None and not pending_origin.get("suggested_inputs")
        and not any(block.get("type") == "dialogue" for block in performance_content_blocks(pending_origin))
        and _pending_silent_receipt(current) is None
    )
    if (silent_exit and (not outcome.session.transition_offered or orphaned_gesture_offer)
            and runtime.engine.completion_contract_satisfied(silent_completion_session) is True
            and not program_offer_already_issued
            and (orphaned_gesture_offer or (not current.session.transition_offered and pending_origin is None))
            and not departure_projection.get("player_left_current_scene")
            and not departure_projection.get("player_departure_pending")
            and evaluation.transition_intent != "reject"
            and not diagnostics["recovered_ordinary_drafts_reused"]
            and not diagnostics["invalid_offer_local_crops"]
            and not diagnostics["invalid_scene_update_local_crops"]
            and not required_pending(source_node,
                                     silent_completion_session,
                                     condition_triggers_enabled=bool(module_options.get("review")))
            and (not module_options.get("review") or (final_fixed_review is not None
                 and not final_fixed_review.body_violations and (not final_fixed_review.offer_present
                     or (superseded_actor_offer and final_fixed_review.offer_quote == published_offer))))):
        accept = _silent_choice_input(runtime.engine, outcome.session, acceptance_contract)
        if turn.message.strip() == accept:
            accept = ""
        if accept:
            # Actor alternatives must not look like equivalent route consent.
            performance = {**performance, "suggested_inputs": [accept]}
            outcome, performance = runtime.engine.finalize_transition_offer_state(
                outcome, performance, new_offer=True)
            performance["transition_offer_source"] = "program_silent"
            outcome = replace(outcome, ledger_event={**outcome.ledger_event,
                "transition_offer_source": "program_silent", "program_invitation": {
                "route_id": acceptance_route["id"], "offer": "", "accept_input": accept,
                "silent": True, "revision": outcome.session.revision,
                "performance": str(performance.get("performance") or ""),
                "visible_blocks": performance_content_blocks(performance),
            }})
            trace_event("completion.silent_offer_issued", route_id=acceptance_route["id"])

    if reviewed_transition_source is not None:
        segments = performance.get("segments")
        source_segment = segments[0] if isinstance(segments, list) and len(segments) == 3 else {}
        fields = ("performance", "scene_narration", "fixed_narrations")
        expected_source = {field: reviewed_transition_source[field] for field in fields
                           if reviewed_transition_source.get(field)}
        actual_source = {field: source_segment[field] for field in fields if source_segment.get(field)}
        if actual_source != expected_source:
            raise NumericV2ActorOutputError("numeric_v2_source_delivery_evidence_changed")
    elif approved_delivery_claims and approved_delivery_fingerprint != performance_fingerprint(performance):
        final_batch = bind_source_delivery(runtime.engine, approved_delivery_claims,
            completion_packet(performance), restrict_player_results=completion_source_restricted)
        final_claims = final_batch.claims
        review = await review_completion_evidence(final_claims, actor_evidence=_actor_fact_evidence_text(performance),
                                                evidence_batch=final_batch)
        if (len(final_claims) != len(approved_delivery_claims)
                or set(review.approved_indexes) != set(range(len(final_claims)))):
            raise NumericV2ActorOutputError("numeric_v2_source_delivery_final_review_failed")
        trace_event("story_evidence.source_delivery_final_reviewed", approved_keys=[row["key"] for row in final_claims])
    if adopted_completion_facts is not None:
        packet = completion_packet(performance)
        if not completion_evidence_matches(adopted_completion_facts, packet):
            try:
                final_batch = rebind_completion_evidence(adopted_completion_facts, packet)
            except NumericV2RuntimeError as exc:
                raise NumericV2ActorOutputError("numeric_v2_discovery_final_evidence_invalid") from exc
            review = await review_completion_evidence(final_batch.claims,
                actor_evidence=_actor_fact_evidence_text(performance), evidence_batch=final_batch)
            if set(review.approved_indexes) != set(range(len(final_batch.claims))):
                raise NumericV2ActorOutputError("numeric_v2_discovery_final_review_failed")
            trace_event("fact_discovery.final_reviewed", approved=final_batch.operations)
    stored_performance = performance
    visible_body = str(performance.get("performance") or "")
    delivered_offer = published_offer or (
        delivered_authored_offer if (visible_body == delivered_authored_offer
                                     or visible_body.endswith("\n" + delivered_authored_offer)) else ""
    )
    offer_start = visible_body.rfind(delivered_offer) if delivered_offer else -1
    if offer_start >= 0 and performance.get("transition_offer_presented") is True:
        # Persist a location in the already delivered text, never a second copy or an authorization.
        stored_performance = {**performance, "transition_offer_start": offer_start,
                              "transition_offer_end": offer_start + len(delivered_offer)}
    # Model calls hold no lifecycle lock; serialize only identity revalidation, display refresh, and atomic commit with rename.
    trace_event("turn.finalized", state=trace_state(outcome.session), performance=performance,
                semantic_review_fallback=diagnostics["semantic_review_fallback"],
                semantic_review_fallback_phase=diagnostics["semantic_review_fallback_phase"])
    commit_started_at = time.monotonic()
    try:
        async with character_config_mutation_lock:
            # Character card may change during model call; successful output cannot commit to another neko's recovery slot.
            display_binding = ensure_current_binding(current.session)
            current_profile = actor._character_profile()
            same_display_name = str(display_binding.get("catgirl_name") or "") == str(
                generation_binding.get("catgirl_name") or ""
            )
            # Compare only what the Actor consumed; background persona updates to
            # facts outside that projection must not discard a finished turn.
            if actor_visible_profile(current_profile) != actor_visible_profile(generation_profile) or (
                same_display_name
                and str(display_binding.get("profile_hash") or "")
                != str(generation_binding.get("profile_hash") or "")
            ):
                # Same-name profile or actual persona text changed; old-persona output must not masquerade as a new-version commit.
                raise ValueError("catgirl_profile_changed_requires_retry")
            refreshed_binding = {
                str(key): str(value)
                for key, value in display_binding.items()
            }
            # This turn's Ledger uses pre-call address facts; refresh display fields only, preserving replay against concurrent address changes.
            refreshed_binding["player_address"] = str(
                outcome.session.catgirl_binding.get("player_address") or ""
            )
            outcome = replace(
                outcome,
                session=replace(
                    outcome.session,
                    # Immutable character ID validated; refresh name/persona version before commit so stale candidate cannot overwrite concurrent rename.
                    catgirl_binding=refreshed_binding,
                ),
            )
            # Character lock always precedes story lock, matching archive/forget lock order.
            async with runtime.story_session_guard():
                if before_commit is not None:
                    # Recheck cloud-save write fence after lengthy model calls, preventing commits if maintenance began during request.
                    await before_commit()
                stored = await runtime.commit_turn(outcome, stored_performance)
    finally:
        # Record commit-stage duration even for identity/fence/storage failures, aiding failed-sample diagnosis.
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
            "transition_judge_calls", "progress_review_calls", "dispute_review_attempts", "semantic_rewrite_attempts",
            "completion_fact_review_calls", "completion_fact_rewrite_attempts",
            "transition_cancellations", "missed_initiation_recoveries", "unsafe_suggestions_removed",
            "recovered_ordinary_drafts_reused",
            "authored_transition_recoveries",
            "authored_invitation_recoveries",
            "committed_ending_review_skips",
            "dispute_review_skipped_high_confidence_body",
            "dispute_review_skipped_unsafe_offer_buttons",
            "dispute_review_skipped_contract_offer",
            "dispute_review_deferred_offer_repair",
            "explicit_player_movement_flags_cleared",
            "actor_repeated_output_retry_aborted",
            "player_action_projection_conflicts", "player_action_projection_safe_degrades",
            "invalid_offer_local_crops", "invalid_scene_update_local_crops",
            "transition_judge_degraded", "dispute_review_degraded", "semantic_review_fallback",
            "evaluator_degraded", "completed",
            "ordinary_turn_combined",
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
    "invitation_recovery_contract",
    "generate_validated_opening",
]
