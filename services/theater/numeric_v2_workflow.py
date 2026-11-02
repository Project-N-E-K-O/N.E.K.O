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
from uuid import uuid4

from utils.character_memory import character_config_mutation_lock

from .numeric_v2_actor import (
    NumericV2Actor,
    NumericV2ActorOutputError,
    actor_visible_profile,
)
from .numeric_v2_actor_output import NumericV2ActorError
from .numeric_v2_action_projection import (
    normalize_player_action_projection,
    project_player_action_result,
)
from .numeric_v2_cast import NumericV2CastProjection
from .numeric_v2_context import (
    current_scene_records,
    missing_contract_names,
    pending_transition_record,
    premature_target_markers,
    premature_target_scene_facts,
    scene_opening_text,
    transition_bridge_leak_markers,
)
from .numeric_v2_fixed_narration import apply_triggers, required_pending, review_candidates
from .numeric_v2_history import lookup_history
from .numeric_v2_narrative import make_narrative_record, narrative_body_fingerprint, parse_narrative_events
from .numeric_v2_narrative_proposer import NumericV2NarrativeProposer
from .numeric_v2_evaluator import (
    NumericV2EvaluationResult,
    NumericV2EvaluatorError,
    NumericV2MetricEvaluator,
    NumericV2TransitionOfferReview,
    OFFER_KIND_EXIT_MENTION_ONLY,
    PLAYER_ACTION_KIND_REQUESTED_MOVEMENT,
)
from .numeric_v2_options import aload_theater_module_options
from .numeric_v2_performance import (
    mixed_performance_blocks,
    performance_content_blocks,
    valid_mixed_performance_policy,
)
from .numeric_v2_runtime import (
    NumericV2Engine,
    NumericV2Runtime,
    NumericV2RuntimeError,
    ScriptSessionV2,
    TurnOutcomeV2,
    TurnRequestV2,
)
from .numeric_v2_store import NumericV2StoredSession
from .numeric_v2_trace import text_trace_scope, trace_event, trace_state


logger = logging.getLogger(__name__)

# Whole-turn review budget. Always run initial quick check; after exhaustion, add no dispute recheck or post-rewrite review.
# Never commit an unreviewed current draft; an old draft's verdict cannot approve rewritten narrative.
NUMERIC_V2_REVIEW_BUDGET_SECONDS = 20.0
# Revisions share one wait budget per turn; keep commit outside cancellable timeout context.
NUMERIC_V2_TURN_MODEL_BUDGET_SECONDS = 60.0
NUMERIC_V2_TURN_REVIEW_RESERVE_SECONDS = 8.5
NUMERIC_V2_NARRATIVE_BUDGET_SECONDS = 8.0
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


def _completion_action_terms(value: Any) -> set[str]:
    """Extract verbatim-checkable phrases for three-way intersection of current-act actions and completion facts."""  # noqa: DOCSTRING_CJK

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
    """Identify current-act actions fulfilling this turn's completion facts but misreported as exit invitations."""  # noqa: DOCSTRING_CJK

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
    # Longest fragment aids log tracing; short fragments only prove three originals refer to the same current-act object.
    return tuple(sorted(matches, key=lambda item: (-len(item), item))[:4])


def _review_denies_narration_only_offer(
    candidate: Mapping[str, Any],
    review: NumericV2TransitionOfferReview,
) -> bool:
    """Clear a narration-only offer flag only on the Guard's structured exit-mention code.

    The Guard reports ``offer_kind=exit_mention_only`` when its verified offer quote
    merely shows where the exit is and nobody invites the player. ``failure_reason``
    is diagnostic prose and is never parsed here: an absent or unknown kind keeps the
    offer flag (fail closed), and a quote the catgirl speaks in dialogue never clears.
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


def _normalized_suggestion_claim_text(value: Any) -> str:
    """Strip whitespace/punctuation to deterministically compare short result assertions in recommendations."""  # noqa: DOCSTRING_CJK

    return "".join(
        character
        for character in str(value or "")
        if character.isalnum() or character == "_"
    ).casefold()


def _suggestion_has_unproven_result_claim(suggestion: str, visible_text: str) -> bool:
    """Identify recommendations with explicit result markers and short assertions absent from current visible narrative."""  # noqa: DOCSTRING_CJK

    blocks = mixed_performance_blocks(suggestion)
    if [block.get("type") for block in blocks] != ["action", "dialogue"]:
        return False
    dialogue = str(blocks[1].get("text") or "").strip()
    # Questions request information rather than asserting known results; let model review decide overreach.
    if any(marker in dialogue for marker in ("？", "?", "吗", "什么", "怎么", "是否", "有没有")):
        return False
    normalized_visible = _normalized_suggestion_claim_text(visible_text)
    if not normalized_visible:
        return False
    for marker in _SUGGESTION_RESULT_CLAIM_MARKERS:
        marker_index = dialogue.find(marker)
        if marker_index < 0:
            continue
        # Take short windows around marker, requiring the entire fragment to appear in current visible content;
        # this blocks '读数显示蘑菇村' (reading shows Mushroom Village), without rejecting '让我看看读数' (let me see the reading).
        claim_window = dialogue[marker_index: marker_index + len(marker) + 6]
        if _normalized_suggestion_claim_text(claim_window) not in normalized_visible:
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
) -> tuple[dict[str, Any], int]:
    """Remove grouped recommendations on review errors, retaining narrative and other fields."""  # noqa: DOCSTRING_CJK

    result = dict(candidate)
    suggestions = candidate.get("suggested_inputs")
    if not isinstance(suggestions, list) or not unsafe_indexes:
        return result, 0
    # Grouped buttons may share premises absent from narrative; per-item missed detection does not prove others independently safe.
    # User accepts fewer recommendations; remove group without guessing dependencies, adding rewrites, or review calls.
    result["suggested_inputs"] = []
    trace_event("suggestions.filtered", reported_indexes=unsafe_indexes,
                reason="unsafe_batch", before=suggestions, after=[])
    return result, len(suggestions)


async def _prepare_narrative_candidates(
    proposer: NumericV2NarrativeProposer,
    candidate: dict[str, Any],
    *,
    session: ScriptSessionV2,
    message: str,
    cache: dict[str, list[dict[str, Any]] | None],
    opening: bool = False,
    failures: list[str] | None = None,
    timeout_seconds: float | None = None,
) -> bool:
    """Reuse proposals/gaps for the same draft; auxiliary failure still reaches narrative review without accepting Actor's self-reported events."""  # noqa: DOCSTRING_CJK

    fingerprint = narrative_body_fingerprint(candidate)
    candidate.pop("narrative_events", None)
    generated = fingerprint not in cache
    if generated:
        try:
            if timeout_seconds is not None and timeout_seconds <= 0:
                raise NumericV2ActorError("numeric_v2_actor_narrative_wait_budget_exhausted")
            async with asyncio.timeout(timeout_seconds):
                cache[fingerprint] = await proposer.propose(
                    performance=candidate, player_input=message, session=session, opening=opening,
                )
        except (NumericV2ActorError, asyncio.TimeoutError) as exc:
            error_code = ("numeric_v2_actor_narrative_timeout"
                          if isinstance(exc, asyncio.TimeoutError) else str(exc))
            # Missing table means unrecorded; empty table means successful no-change proposal and cannot impersonate failure.
            cache[fingerprint] = None
            if failures is not None:
                failures.append(error_code)
            trace_event("narrative.degraded", phase="opening" if opening else "turn",
                        error_code=error_code, body_fingerprint=fingerprint)
            logger.warning("Narrative proposal failed; continuing with body review: %s", error_code)
    if cache[fingerprint] is not None:
        candidate["narrative_events"] = deepcopy(cache[fingerprint])
    return generated


async def _review_with_narrative_budget_fallback(
    evaluator: NumericV2MetricEvaluator,
    *,
    cache: dict[str, list[dict[str, Any]] | None],
    phase: str = "turn",
    diagnostics: dict[str, Any] | None = None,
    **review_kwargs: Any,
) -> NumericV2TransitionOfferReview:
    """Packaging failure may defer this draft's ledger write; identical complete narrative still requires native review."""  # noqa: DOCSTRING_CJK

    try:
        return await evaluator.validate_transition_offer(**review_kwargs)
    except NumericV2EvaluatorError as exc:
        candidate = review_kwargs["actor_performance"]
        if (str(exc) != "numeric_v2_transition_review_budget_exceeded"
                or "narrative_events" not in candidate):
            raise
        # This error occurs before SDK request only. Use missing, not empty table; same draft cannot regain proposal.
        fingerprint = narrative_body_fingerprint(candidate)
        candidate.pop("narrative_events")
        cache[fingerprint] = None
        if diagnostics is not None:
            diagnostics["narrative_review_budget_degrades"] += 1
        trace_event("narrative.degraded", phase=phase, stage="review_input_budget",
                    error_code=str(exc), body_fingerprint=fingerprint)
        logger.warning("Narrative candidates omitted for Review input budget; continuing body review")
    # Do not catch second exception; no-table budget overflow or actual review failure follows existing rollback.
    return await evaluator.validate_transition_offer(**review_kwargs)


async def generate_validated_opening(
    *,
    engine: NumericV2Engine,
    config_manager: Any,
    session_id: str,
    catgirl_binding: Mapping[str, Any],
    actor_budget_profile: str,
) -> dict[str, Any]:
    """Generate public opening; temporary opening boundaries require review before creating Session."""  # noqa: DOCSTRING_CJK

    def finish(candidate: Mapping[str, Any], review: Any = None) -> dict[str, Any]:
        result = dict(candidate)
        if review is None:
            result.pop("narrative_events", None)
        result["narrative_record"] = make_narrative_record(result, "", review, opening=True)
        result.pop("narrative_events", None)
        return result

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
        return finish(opening)

    if not opening_options.get("review"):
        # Review module disabled: deliver Actor opening directly without model review/rewrite.
        trace_event("review.skipped", phase="opening")
        return finish(opening)
    evaluator = NumericV2MetricEvaluator(config_manager)
    proposer = NumericV2NarrativeProposer(config_manager)
    narrative_proposals: dict[str, list[dict[str, Any]] | None] = {}
    for attempt in range(2):
        review_session = engine.create_session(
            session_id=session_id,
            catgirl_binding=catgirl_binding,
            opening_performance={"performance": "", "suggested_inputs": []},
            actor_budget_profile=actor_budget_profile,
        )
        await _prepare_narrative_candidates(
            proposer, opening, session=review_session, message="",
            cache=narrative_proposals, opening=True,
        )
        try:
            review = await _review_with_narrative_budget_fallback(
                evaluator, cache=narrative_proposals, phase="opening",
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
            opening, review.unsafe_suggestion_indexes,
        )
        if not review.body_violations and not review.offer_present:
            trace_event("opening.ready", performance=opening)
            return finish(opening, review)
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
) -> str:
    """Supply author bridge/next opening only for boundary rewrites, explicitly identifying where the scene must stop."""  # noqa: DOCSTRING_CJK

    session = current.session
    node = runtime.engine.nodes.get(session.current_node_id)
    if not isinstance(node, Mapping):
        return ""
    # Narrative and review already use this turn's settled values; rewrite cannot revert to old values and another branch.
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


def _source_side_delivery(performance: Mapping[str, Any]) -> Mapping[str, Any]:
    """Source-act sections of transition candidate: source response and transitional bridge.

    Target opening is author-written next-act narrative, naturally carrying target facts; checking against source prohibitions
    misclassifies normal transitions (issue2.143 run-D counterexample), so boundary checks use only the two source parts.
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
    """Find ending candidates containing question marks; with review enabled also check whether next-turn answer is truly needed."""  # noqa: DOCSTRING_CJK

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
        catgirl_name=str(session.catgirl_binding.get("catgirl_name") or "当前猫娘"),
    ).text(text)


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


def _silent_exit_allowed(node: Mapping[str, Any]) -> bool:
    """Match the compiler's author-only exemption without changing live policy."""
    if not isinstance(node.get("completion_contract"), Mapping):
        return False
    beat = node.get("story_beat") or {}
    policy = (beat.get("acting_contract") or {}).get("dialogue_policy")
    for goal in beat.get("goals") or []:
        effects = (goal.get("delivery") or {}).get("state_effects") or {}
        policy = effects.get("dialogue_policy", policy)
    return policy == "forbidden"


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
    if not session.transition_offered or turn.input_source != "suggestion":
        return ""
    origin = pending_transition_record(session, ledger_events=current.ledger_events)
    if not isinstance(origin, Mapping):
        return ""
    route = engine.preview_route(session.current_node_id, session.metrics)
    contract = route.get("transition_contract") if route else None
    if not isinstance(contract, Mapping):
        return ""
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
    offered_route = engine.preview_route(session.current_node_id, event["after_metrics"]) if event else None
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
    if not acceptance:
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
    **kwargs: Any,
) -> dict[str, Any]:
    """Control Actor retries by switch; source-reuse detection during official acceptance retains one extra narrow retry."""  # noqa: DOCSTRING_CJK

    last_error_code = ""
    required_retry_hint = str(kwargs.get("retry_hint") or "").strip()
    # Direct callers (existing integrations/tests) default four attempts; workflow passes module switches explicitly.
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
                # Official scene acceptance retains one narrow retry even with generic narrative retries disabled,
                # only for two repetition guards detecting source-section reuse of historical narrative.
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


def invitation_recovery_contract(runtime: NumericV2Runtime, current: NumericV2StoredSession, *, condition_narrations_enabled: bool = True) -> dict[str, Any] | None:
    """Project a new invitation only when the current state can authorize it."""
    session = current.session
    if session.status != "active" or session.transition_offered or runtime.engine.completion_contract_satisfied(session) is not True:
        return None
    if required_pending(runtime.engine.nodes[session.current_node_id], session,
            condition_triggers_enabled=condition_narrations_enabled):
        return None
    route = runtime.engine.preview_route(session.current_node_id, session.metrics)
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
    if (not acceptance or (not silent and (
            not offer or not _authored_offer_visible({"performance": offer}, offer)
            or not valid_mixed_performance_policy({"performance": offer}, session.dialogue_policy)))):
        return None
    try:
        TurnRequestV2.from_mapping({"client_turn_id": "reinvitation_validation",
            "base_revision": session.revision, "message": acceptance, "input_source": "suggestion"})
    except NumericV2RuntimeError:
        return None
    return {"route_id": str(route["id"]), "offer": offer, "accept_input": acceptance,
            **({"silent": True} if silent else {})}


async def _execute_reinvitation(*, runtime, current, turn, ensure_current_binding, before_commit):
    # Explicit control action: no Actor/Evaluator, no metrics or scene movement.
    modules = await aload_theater_module_options()
    contract = invitation_recovery_contract(runtime, current, condition_narrations_enabled=bool(modules.get("review")))
    if contract is None:
        raise NumericV2RuntimeError("numeric_reinvitation_not_available")
    outcome = runtime.prepare_turn(current, turn, (), condition_narrations_enabled=False)
    performance = {"performance": contract["offer"], "suggested_inputs": [contract["accept_input"]]}
    outcome, performance = runtime.engine.finalize_transition_offer_state(
        outcome, performance, new_offer=True, invalidate_previous_offer=True)
    if contract.get("silent") is True:
        performance["transition_offer_source"] = "program_silent"
        outcome = replace(outcome, ledger_event={**outcome.ledger_event, "transition_offer_source": "program_silent"})
    outcome = replace(outcome, ledger_event={**outcome.ledger_event, "program_invitation": {
        **contract, "performance": contract["offer"], "visible_blocks": performance_content_blocks(performance),
        **({"revision": outcome.session.revision} if contract.get("silent") else {})}})
    async with character_config_mutation_lock:
        binding = ensure_current_binding(current.session)
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
    background_narrative: bool = False,
) -> NumericV2TurnWorkflowResult:
    """Trace one attempt without changing the workflow, retry policy or public result."""
    diagnostics = diagnostics_sink if diagnostics_sink is not None else {}
    if background_narrative:
        from .numeric_v2_background_narrative import settle_before_input
        current = await settle_before_input(runtime, current)
    if turn.input_source == "reinvite":
        return await _execute_reinvitation(runtime=runtime, current=current, turn=turn,
            ensure_current_binding=ensure_current_binding, before_commit=before_commit)
    with text_trace_scope("turn", state_before=trace_state(current.session), turn=turn):
        try:
            result = await _execute_numeric_v2_turn(
                config_manager=config_manager, runtime=runtime, current=current, turn=turn,
                ensure_current_binding=ensure_current_binding, before_commit=before_commit,
                diagnostics_sink=diagnostics,
                background_narrative=background_narrative,
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
    background_narrative: bool = False,
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
            "narrative_work": 0.0,
            "transition_judge_work": 0.0,
            "commit_work": 0.0,
            "total_wall": 0.0,
        },
        "evaluator_model_attempts": 0,
        "actor_generation_attempts": 0,
        "actor_repeated_output_guards": {},
        "actor_repeated_output_retry_aborted": 0,
        "actor_provider_calls": 0,
        "narrative_model_attempts": 0,
        "narrative_proposal_reuses": 0,
        "narrative_proposal_failures": [],
        "narrative_review_budget_degrades": 0,
        "actor_suggestion_fill_attempts": 0,
        "actor_suggestion_fill_provider_calls": 0,
        "actor_suggestion_refill_after_review_attempts": 0,
        "actor_suggestion_fill_reasons": {},
        "actor_base_suggestion_parse_counts": {},
        "actor_base_fact_candidate_parse_counts": {},
        "transition_judge_calls": 0,
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
        # Count model-reported exit invitations cleared by three-way verbatim evidence proving player-authorized current-act completion.
        "current_scene_offer_flags_cleared": 0,
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
        "author_fallback_invitation_protected": 0,
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
    })
    evaluator = NumericV2MetricEvaluator(config_manager)
    actor = NumericV2Actor(config_manager)
    proposer = NumericV2NarrativeProposer(config_manager)
    narrative_proposals: dict[str, list[dict[str, Any]] | None] = {}
    # Every model call except reply is an optional module, all disabled by default to save wait and tokens.
    module_options = await aload_theater_module_options()
    diagnostics["theater_module_options"] = dict(module_options)
    # Retain legacy diagnostic keys: dispute recheck is now in module table.
    dispute_review_enabled = bool(module_options.get("dispute"))
    diagnostics["dispute_review_enabled"] = dispute_review_enabled
    # Original results belong only to this workflow, shared across narrative retries/reviews without writing Session/Ledger.
    history_lookup_result: dict[str, Any] | None = None
    invalidate_previous_offer = False
    final_fixed_review: NumericV2TransitionOfferReview | None = None
    program_invitation_performance: str | None = None
    # Freeze actual persona input before narrative resampling; recommendations degrade internally, but final narrative must use same character generation.
    generation_binding = ensure_current_binding(current.session)
    generation_profile = actor._character_profile()

    async def evaluate_turn() -> NumericV2EvaluationResult:
        """Run Evaluator once, conservatively degrading model failures to unchanged state."""  # noqa: DOCSTRING_CJK

        started_at = time.monotonic()
        if not module_options.get("evaluator"):
            # Evaluation module disabled: no model calls, guessed values, or intents.
            diagnostics["evaluator_skipped"] = True
            trace_event("evaluator.skipped")
            try:
                return _evaluation_without_evaluator(current, turn, engine=runtime.engine)
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

    def prepare_turn(evaluation: NumericV2EvaluationResult) -> TurnOutcomeV2:
        """Perform deterministic settlement and accumulate synchronous Runtime duration."""  # noqa: DOCSTRING_CJK

        started_at = time.monotonic()
        try:
            prepared = runtime.prepare_turn(
                current,
                turn,
                evaluation.metric_changes,
                scene_complete=evaluation.scene_complete,
                transition_intent=evaluation.transition_intent,
                # Same evaluation supplies ending-readiness signal; default/degrade false without another confirmation or model call.
                natural_ending_ready=getattr(evaluation, "natural_ending_ready", False),
                # Valid fields/provenance do not prove semantics. Stage proposals during review, never let
                # Actor treat them as committed early or Review skip facts as already satisfied.
                fact_operations=() if module_options.get("review") else evaluation.fact_operations,
                # Conditional fixed narration triggers only through review; with review disabled it must not lock exits.
                condition_narrations_enabled=bool(module_options.get("review")),
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
        """Generate Actor narrative through official path, guiding pacing only by soft hints within same call."""  # noqa: DOCSTRING_CJK

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
                "input_source": turn.input_source,
                # Locate original proposal with same committed Ledger as Evaluator, including all format/semantic retries.
                "recent_ledger_events": current.ledger_events,
                "diagnostics": diagnostics,
                "allow_suggestion_fill": bool(module_options.get("suggestion_fill")),
            }
            if history_lookup_result is not None:
                generation_kwargs["history_lookup"] = history_lookup_result
            remaining = model_deadline - time.monotonic() - NUMERIC_V2_TURN_REVIEW_RESERVE_SECONDS
            if remaining <= 0:
                raise NumericV2ActorError("numeric_v2_actor_timeout")
            try:
                async with asyncio.timeout(remaining):
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
            except asyncio.TimeoutError as exc:
                trace_event("actor.wait_budget_exhausted")
                raise NumericV2ActorError("numeric_v2_actor_timeout") from exc
            generated.pop("narrative_events", None)
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
    preserved_player_events: list[dict[str, Any]] = []

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
            route = runtime.engine.preview_route(outcome.session.current_node_id, outcome.session.metrics)
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
        narrative_started_at = time.monotonic()
        try:
            fingerprint = narrative_body_fingerprint(candidate)
            narrative_timeout = min(
                NUMERIC_V2_NARRATIVE_BUDGET_SECONDS - diagnostics["timings_ms"]["narrative_work"] / 1000.0,
                model_deadline - time.monotonic() - NUMERIC_V2_TURN_REVIEW_RESERVE_SECONDS,
            )
            if not background_narrative and fingerprint not in narrative_proposals and narrative_timeout > 0:
                diagnostics["narrative_model_attempts"] += 1
            if background_narrative:
                candidate.pop("narrative_events", None)
            else:
                generated = await _prepare_narrative_candidates(
                    proposer, candidate, session=current.session, message=turn.message,
                    cache=narrative_proposals, failures=diagnostics["narrative_proposal_failures"],
                    timeout_seconds=narrative_timeout,
                )
                if not generated:
                    diagnostics["narrative_proposal_reuses"] += 1
        finally:
            _add_elapsed_ms(diagnostics, "narrative_work", narrative_started_at)
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
            review = await _review_with_narrative_budget_fallback(
                evaluator, cache=narrative_proposals, diagnostics=diagnostics, **review_kwargs,
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
                current_scene_evidence = ()
                if (
                    not changed
                    and evaluation.transition_intent == "unclear"
                    and result.offer_present
                    and not result.valid
                    and not result.body_violations
                ):
                    current_scene_evidence = _current_scene_completion_offer_evidence(
                        engine=runtime.engine,
                        session=current.session,
                        player_input=turn.message,
                        review=result,
                    )
                if current_scene_evidence:
                    # Three independent originals identifying one completion fact prove an already authorized in-act player action,
                    # not an exit awaiting another decision; retain narrative/fact candidates and clear invitation judgment only.
                    diagnostics["current_scene_offer_flags_cleared"] += 1
                    trace_event(
                        "review.current_scene_offer_cleared",
                        evidence=list(current_scene_evidence),
                        fact_keys=[
                            str(candidate.get("key") or "")
                            for candidate in result.fact_candidates
                            if isinstance(candidate, Mapping)
                        ],
                    )
                    result = replace(
                        result,
                        offer_present=False,
                        valid=False,
                        offer_quote="",
                        failure_reason="",
                    )
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
            retained_candidate, _ = _drop_reported_unsafe_suggestions(
                candidate, review.unsafe_suggestion_indexes,
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
                # Narrative has no violations, only buttons exceed bounds; removing whole group leaves narrative verdict unchanged, so skip second dispute.
                diagnostics["dispute_review_skipped_unsafe_offer_buttons"] += 1
                trace_event("review.dispute_skipped", reason="unsafe_offer_buttons_only")
            elif (
                not review.body_violations
                and review.offer_present
                and not review.valid
                and "next_scene_direction" in failure_reason
                and any(marker in failure_reason for marker in ("不符", "不符合", "不一致"))
            ):
                # Exit contract determines direction; asking model again cannot change structured destination and only adds waiting.
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
                # Ordinary first draft with invalid invitation alone can use its one existing Actor rewrite directly.
                # Repair then verify; arbitrate only if repair still rejected, avoiding needless wait after success.
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
                        # Dispute recheck must use exactly the quick check's request and evidence (existing invariant);
                        # do not mark as rewrite recheck here; all judgments in current complete review protocol retain same evidence scope.
                        dispute_kwargs = dict(review_kwargs)
                        dispute_kwargs["dispute_review"] = True
                        dispute_kwargs["timeout_seconds"] = min(
                            NUMERIC_V2_DISPUTE_TIMEOUT_CAP_SECONDS,
                            max(0.05, dispute_budget - 0.05),
                        )
                        reviewed = await evaluator.validate_transition_offer(**dispute_kwargs)
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
            review = replace(review, reviewed_suggestion_texts=tuple(candidate.get("suggested_inputs") or []))
            if not review.unsafe_suggestion_indexes:
                if acceptance_inserted and review.offer_present and review.valid:
                    diagnostics["verified_offer_acceptance_suggestions_inserted"] += 1
                if acceptance_preserved:
                    diagnostics["pending_acceptance_suggestions_preserved"] += 1
            reviewed_player_pairs = {
                (change["entity"], change["attribute"])
                for event in parse_narrative_events(list(review.reviewed_narrative_events)) or []
                if event["phase"] == "player_input" and event["source"] == "player_input"
                for change in event["changes"]
            }
            # A later verdict replaces earlier approval of these player attributes,
            # even if another rewrite subsequently loses its auxiliary event table.
            preserved_player_events[:] = [
                event for event in preserved_player_events
                if all((change["entity"], change["attribute"]) not in reviewed_player_pairs
                       for change in event["changes"])
            ]
            for event in make_narrative_record(
                candidate, turn.message, review, body_confirmed=False,
                state=current.session.narrative_state,
            )["events"]:
                if event not in preserved_player_events:
                    preserved_player_events.append(event)
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
    evaluation = await evaluate_turn()
    if diagnostics["evaluator_degraded"]:
        confirmed_acceptance_route_id = _confirmed_authored_acceptance(
            runtime.engine, current, turn, require_program_invitation=True)
    acceptance_route = runtime.engine.preview_route(current.session.current_node_id, current.session.metrics)
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
        and not str(acceptance_contract.get("fallback_offer") or "").strip()
        and isinstance(acceptance_target, Mapping)
        and acceptance_target.get("type") != "ending" and acceptance_target.get("terminal") is not True
        # Reviewed dialogue, narration and action offers retain ordinary acceptance.
        and not reviewed_offer_evidence
    )
    if (not module_options.get("review") or diagnostics["evaluator_degraded"]
            or silent_acceptance_required
            or _pending_silent_receipt(current, include_withdrawn=True) is not None):
        if evaluation.transition_intent == "accept" and not confirmed_acceptance_route_id:
            evaluation = replace(evaluation, transition_intent="unclear")
    if confirmed_acceptance_route_id:
        # Exact button selection no longer loses to upstream unclear; retain numeric, fact, and Runtime route checks.
        evaluation = replace(evaluation, transition_intent="accept", transition_reply_target="pending_transition")
        trace_event("transition.authored_acceptance_confirmed", route_id=confirmed_acceptance_route_id)
    fact_audit_by_key = {item["key"]: item for item in evaluation.fact_audit}
    evaluator_fact_claims = tuple(
        {
            **operation,
            **fact_audit_by_key[operation["key"]],
            "description": runtime.engine.fact_contract[operation["key"]].get("description", ""),
        }
        for operation in evaluation.fact_operations
        if module_options.get("review") and operation["key"] in fact_audit_by_key
    )
    diagnostics["evaluator_fact_claims_deferred"] = len(evaluator_fact_claims)
    if evaluation.history_query and module_options.get("history_lookup"):
        # No extra ordinary-turn calls; fetch full record once only for evidence gaps, sharing failures too to prevent repeated rewrite lookups.
        lookup_started_at = time.monotonic()
        history_lookup_result = await lookup_history(config_manager, current.session, evaluation.history_query)
        trace_event("history.result", query=evaluation.history_query, result=history_lookup_result)
        diagnostics["history_lookup"] = {key: value for key, value in history_lookup_result.items() if key != "evidence"}
        _add_elapsed_ms(diagnostics, "history_lookup_work", lookup_started_at)
    # Preserve model reasoning for misjudgment diagnosis, without passing to Actor/history or changing ending conditions.
    diagnostics["ending_reason"] = evaluation.ending_reason
    outcome = prepare_turn(evaluation)
    invalidate_previous_offer = outcome.ledger_event.get("transition_offer_invalidated") is True
    performance = await generate_actor_turn(outcome)
    performance = apply_deterministic_suggestion_filter(performance)
    route_changed = (
        outcome.ledger_event["from_node_id"]
        != outcome.ledger_event["to_node_id"]
    )
    reviewed_transition_offered = False
    approved_recovery_fallback: tuple[
        TurnOutcomeV2, dict[str, Any], NumericV2TransitionOfferReview,
        NumericV2EvaluationResult,
    ] | None = None

    async def verify_contract_boundaries(**kwargs: Any) -> bool:
        remaining = model_deadline - time.monotonic() - 0.5
        if remaining <= 0:
            raise NumericV2EvaluatorError("numeric_v2_contract_check_timeout")
        try:
            async with asyncio.timeout(remaining):
                return await evaluator.verify_contract_boundaries(**kwargs)
        except asyncio.TimeoutError as exc:
            raise NumericV2EvaluatorError("numeric_v2_contract_check_timeout") from exc

    async def review_terminal_question_markers(candidate: Mapping[str, Any]) -> tuple[str, ...]:
        markers = _terminal_new_question_markers(
            engine=runtime.engine, outcome=outcome, performance=candidate)
        if not markers or not (module_options.get("review") or module_options.get("review_contract")):
            return markers
        # Question marks alone do not prove new interaction; reuse narrow boundary check while retaining full action/route/state review.
        started_at = time.monotonic()
        diagnostics["terminal_question_review_calls"] += 1
        try:
            violated = await verify_contract_boundaries(
                node={"story_beat": {"must_not_happen": [
                    "不得在结局提出必须由玩家下一轮回答或选择的新问题或任务；"
                    "认人招呼、修辞反问、自问自答及引用旧问题不需玩家回应时不受此限。"
                ]}},
                # This check covers the whole delivered ending, unlike the
                # next-scene evaluator's target-opening-only history projection.
                actor_performance=candidate,
                include_all_segments=True,
                player_input=turn.message,
            )
        except NumericV2EvaluatorError as exc:
            # Incomplete review is not validity; do not add another Actor draft for timeout.
            diagnostics["terminal_question_review_degraded"] = True
            trace_event("transition.terminal_question_check_failed", error=str(exc))
            raise NumericV2ActorOutputError("numeric_v2_transition_review_failed") from exc
        finally:
            _add_elapsed_ms(diagnostics, "terminal_question_check_work", started_at)
        return markers if violated else ()

    def transition_bridge_leaks(candidate: Mapping[str, Any]) -> tuple[str, ...]:
        """Return target-opening facts that the candidate's bridge segment already narrates.

        The bridge is checked against both the target node's authored opening and the
        target opening this draft actually delivers; clauses the authored bridge
        contract already permits stay exempt.
        """

        target_node = runtime.engine.nodes.get(str(outcome.ledger_event["to_node_id"]))
        target_beat = target_node.get("story_beat") if isinstance(target_node, Mapping) else None
        authored_opening = scene_opening_text(target_beat) if isinstance(target_beat, Mapping) else ""
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
        authored_bridge = str(transition_contract.get("bridge_scene_narration") or "")
        # Author opening and opening actually delivered in this draft both count as target content; block verbatim preemption of either in bridge.
        return tuple(dict.fromkeys(
            marker
            for opening in (authored_opening, target_opening_text)
            for marker in transition_bridge_leak_markers(
                target_opening=opening,
                bridge_text=bridge_text,
                authored_bridge=authored_bridge,
            )
        ))

    async def verify_later_transition_draft(candidate: Mapping[str, Any], *, stage: str) -> None:
        """Re-run the deterministic transition checks on a regenerated formal draft.

        Only the first formal draft gets a repair attempt; any later draft (missed
        initiation recovery, contract or review rewrite) must already satisfy the
        checks, otherwise the turn rolls back as a retryable Actor failure.
        """

        leaks = transition_bridge_leaks(candidate)
        if leaks:
            diagnostics["transition_bridge_leak_markers_after_rewrite"] = list(leaks)
            diagnostics["transition_structure_rejected"] = True
            trace_event("transition.bridge_target_leak_unresolved", markers=list(leaks), stage=stage)
            raise NumericV2ActorOutputError("numeric_v2_transition_segment_overlap")
        questions = await review_terminal_question_markers(candidate)
        diagnostics["terminal_new_question_markers"] = list(questions)
        if questions:
            diagnostics["terminal_new_question_markers"] = list(questions)
            diagnostics["terminal_structure_rejected"] = True
            trace_event("transition.terminal_new_question_unresolved", markers=list(questions), stage=stage)
            raise NumericV2ActorOutputError("numeric_v2_terminal_new_question")

    if route_changed:
        # Target opening is delivered next; bridge cannot perform its complete facts ahead of time.
        # Deterministic sentence/time provenance checks only; existing Actor handles semantic rewriting, with rollback on persistent conflict.
        leak_markers = transition_bridge_leaks(performance)
        if leak_markers:
            diagnostics["transition_bridge_leak_markers"] = list(leak_markers)
            trace_event("transition.bridge_target_leak", markers=list(leak_markers))
            diagnostics["semantic_rewrite_attempts"] += 1
            performance = await generate_actor_turn(outcome, retry_hint=(
                "过渡桥段提前包含了目标幕开场独有内容：" + "、".join(leak_markers)
                + "。只保留来源回应和作者允许的过渡时空；目标幕开场会在下一段单独交付，"
                "不得在 transition_bridge 中重复写出目标幕的到达、时点或独有事实。"))
            remaining_leaks = transition_bridge_leaks(performance)
            diagnostics["transition_bridge_leak_markers_after_rewrite"] = list(remaining_leaks)
            if remaining_leaks:
                diagnostics["transition_structure_rejected"] = True
                trace_event("transition.bridge_target_leak_unresolved", markers=list(remaining_leaks))
                raise NumericV2ActorOutputError("numeric_v2_transition_segment_overlap")
        terminal_question_markers = await review_terminal_question_markers(performance)
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
                # Check rewritten draft's questions once, retaining bridge provenance checks.
                await verify_later_transition_draft(performance, stage="terminal_rewrite")
                terminal_question_markers = ()
            if terminal_question_markers:
                diagnostics["terminal_structure_rejected"] = True
                trace_event(
                    "transition.terminal_new_question_unresolved",
                    markers=list(terminal_question_markers),
                )
                raise NumericV2ActorOutputError("numeric_v2_terminal_new_question")
    if route_changed and (module_options.get("review_delivery") or module_options.get("review_contract")):
        # Pre-transition contracts: explicit delivery validation is zero-call code; boundary validation is one narrow judgment only during transitions.
        # Both share one rewrite allowance; failures only leave diagnostics, not block commit.
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
            # Target opening in transition delivery is author-written next-act narrative, naturally carrying target facts;
            # comparing to source prohibitions would misclassify normal transitions; review only source response and bridge.
            boundary_view = _source_side_delivery(performance)
            try:
                violated = await verify_contract_boundaries(
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
            await verify_later_transition_draft(performance, stage="contract_rewrite")
            if module_options.get("review_delivery"):
                still_missing = missing_contract_names(source_node, target_node_id, performance, current.session)
                diagnostics["contract_missing_after_rewrite"] = list(still_missing)
                if still_missing:
                    diagnostics["contract_missing_fallback"] = True
            if module_options.get("review_contract") and not module_options.get("review"):
                try:
                    still_violated = await verify_contract_boundaries(
                        node=source_node, actor_performance=_source_side_delivery(performance),
                        player_input=turn.message)
                except NumericV2EvaluatorError:
                    still_violated = ()
                diagnostics["contract_violated_after_rewrite"] = list(still_violated)
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
                    performance = await generate_actor_turn(outcome, retry_hint=(
                        "本轮可见旁白提前写出了目标幕独有事实：" + "、".join(leaked)
                        + "。目标幕尚未进入；请只保留当前幕可证实内容，邀请可以提出但不能把目标事实写成现在时。"))
        if module_options.get("review_contract") and not route_changed:
            # Staying-in-act turns must check author prohibitions too: premature arrival must be blocked this turn,
            # not at next transition after delivery to player. Share the transition's one rewrite allowance.
            stay_node = runtime.engine.nodes[str(outcome.ledger_event["from_node_id"])]
            try:
                violated = await verify_contract_boundaries(
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
                        still_violated = await verify_contract_boundaries(
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
        # Old invitation latch does not prove current narrative safe; review in-act follow-ups/clarifications too, leaving invitation state to Runtime.
        # Narrative violations and invalid invitation share one rewrite; rewrite only if first dispute still rejects, not once per error type.
        for rewrite_attempt in range(2):
            transition_review = await review_transition_offer(
                performance,
                defer_offer_only_dispute=rewrite_attempt == 0,
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
                    (outcome, evaluation, deepcopy(performance), transition_review,
                     deepcopy(preserved_player_events))
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
                            saved_player_events,
                        ) = approved_generation_fallback
                        preserved_player_events[:] = saved_player_events
                        performance, removed = _drop_reported_unsafe_suggestions(
                            performance, final_fixed_review.unsafe_suggestion_indexes,
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
            boundary_context = (
                _transition_boundary_repair_context(runtime, current, metrics=outcome.session.metrics)
                if "scene_boundary" in transition_review.body_violations or invalid_offer
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
                    "猫娘可用自身反应、回答或明确未知承接玩家，不要求本轮推进剧情或产生外部结果。"
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
            review = await review_transition_offer(performance)
            if diagnostics["dispute_review_degraded"] and review_budget_effectively_exhausted():
                # Official transition rolls back directly on dispute timeout; never commit three-part records from incompletely reviewed rewrite.
                diagnostics["review_timeout_aborted"] = True
                trace_event("review.rewrite_aborted", phase="transition", reason="review_budget_exhausted")
                raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
            performance, removed = _drop_reported_unsafe_suggestions(performance, review.unsafe_suggestion_indexes)
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
                        performance, approved_review.unsafe_suggestion_indexes,
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
                performance, removed = _drop_reported_unsafe_suggestions(performance, review.unsafe_suggestion_indexes)
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
    # Pending original-text turn with author-boundary conflict cannot use last-draft fallback; missing precise location means
    # unsafe to trim, not permission to pass. Successful trimming already cleared narrative violations.
    if final_fixed_review is not None and (
        any(issue.get("code") == "fixed_narration_content" for issue in final_fixed_review.body_issues)
        or ("author_boundary" in final_fixed_review.body_violations
            and review_candidates(runtime.engine.nodes[current.session.current_node_id], current.session))
    ):
        trace_event("review.fixed_content_rejected", phase="ordinary")
        raise NumericV2ActorOutputError("numeric_v2_transition_review_failed")
    # Safe narrative without invitation may prepare author invitation; full combined candidate still needs review within shared budget.
    # No further Actor calls; actual act change still requires explicit player acceptance next turn.
    conservative_invitation = (
        not module_options.get("review") or not module_options.get("evaluator")
        or diagnostics["evaluator_degraded"] or diagnostics["semantic_review_fallback"]
        or _pending_silent_receipt(current, include_withdrawn=True) is not None
    )
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
        _project_authored_transition_text(
            runtime.engine, outcome.session,
            str(fallback_contract.get("fallback_offer") or "").strip(),
        )
        if isinstance(fallback_contract, Mapping)
        else ""
    )
    departure_projection = normalize_player_action_projection(
        outcome.ledger_event.get("player_action_projection")
    )
    fallback_accept_input = (
        _project_authored_transition_text(
            runtime.engine, outcome.session, str(fallback_contract.get("accept_input") or ""),
        ).strip() if isinstance(fallback_contract, Mapping) else ""
    )
    scene_records, _ = current_scene_records(current.session)
    scene_revisions = {record.get("revision") for record in scene_records}
    program_offer_already_issued = any(
        event.get("result_revision") in scene_revisions
        and isinstance(event.get("program_invitation"), Mapping)
        for event in current.ledger_events
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
                    performance, removed = _drop_reported_unsafe_suggestions(
                        fallback_candidate, fallback_review.unsafe_suggestion_indexes)
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

    # Review enabled: unconfirmed Actor candidates cannot bypass through union; disabled retains original call/admission path.
    actor_fact_candidates = performance.pop("fact_candidates", [])
    review_fact_candidates = (
        list(final_fixed_review.fact_candidates)
        if final_fixed_review is not None and not final_fixed_review.body_violations
        else []
    )
    diagnostics["review_fact_candidates_proposed"] = len(review_fact_candidates)
    proposed_fact_candidates: list[Mapping[str, Any]] = []
    selected_fact_candidates = (
        review_fact_candidates if module_options.get("review")
        else actor_fact_candidates if isinstance(actor_fact_candidates, list) else []
    )
    for candidate in selected_fact_candidates:
        if not isinstance(candidate, Mapping):
            continue
        proposed_fact_candidates.append(candidate)
    if proposed_fact_candidates:
        # Every candidate still gets full allowlist/type/verbatim-evidence validation; one invalid candidate excludes only itself,
        # retaining valid facts in same batch. All accepted items remain in uncommitted outcome until one atomic turn commit.
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
                # Rejection reasons log fields/error codes only, never candidate narrative in log summaries.
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

    # Latch new proposals per narrative review; final-draft fallback may commit narrative but never reviewed-invalid invitations.
    # Runtime already computed transition lifecycle from this Evaluator result, especially retaining old proposals on unclear;
    # Actor false must not overwrite Runtime true or next Evaluator loses visible proposal.
    # Workflow submits reviewed or explicit-fallback new-proposal signal; Runtime decides old-state retention and synchronization of all three records.
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
        invalidate_previous_offer = invalidate_previous_offer or current.session.transition_offered
        if not new_offer and (current.session.transition_offered
                              or performance.get("transition_offered") is True
                              or authored_offer_visible):
            filtered_performance["suggested_inputs"] = []
    semantically_verified_offer = bool(
        module_options.get("review") and module_options.get("evaluator")
        and not diagnostics["evaluator_degraded"]
        and outcome.session.dialogue_policy != "forbidden"
    )
    if (not module_options.get("review") and reviewed_transition_offered
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
            set(final_fixed_review.reviewed_suggestion_texts)
            if not final_fixed_review.unsafe_suggestion_indexes else set()
        )
        suggestions = list(performance.get("suggested_inputs") or [])
        performance["suggested_inputs"] = [item for item in suggestions if item in reviewed_texts]
        diagnostics["unsafe_suggestions_removed"] += len(suggestions) - len(performance["suggested_inputs"])
    narrative_record = make_narrative_record(
        performance, turn.message, final_fixed_review,
        body_confirmed=(final_fixed_review is not None and not final_fixed_review.body_violations),
        state=current.session.narrative_state,
        preserved_player_events=tuple(preserved_player_events),
    )
    performance.pop("narrative_events", None)
    outcome = replace(outcome, ledger_event={**outcome.ledger_event, "narrative_record": narrative_record})
    if (background_narrative and module_options.get("review")
            and final_fixed_review is not None and not final_fixed_review.body_violations):
        outcome = replace(outcome, ledger_event={**outcome.ledger_event, "narrative_pending": {
            "job_id": uuid4().hex,
            "body": narrative_body_fingerprint(performance),
            "lifecycle_revision": current.session.lifecycle_revision,
            "forgotten_through_revision": current.session.forgotten_through_revision,
        }})
    diagnostics["narrative_events_approved"] = len(narrative_record["events"])
    diagnostics["narrative_coverage"] = narrative_record["coverage"]
    trace_event("narrative.finalized", record=narrative_record)
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
    if (performance.get("transition_offer_presented") is True and final_fixed_review is not None
            and final_fixed_review.offer_present and final_fixed_review.valid
            and not final_fixed_review.body_violations):
        performance["transition_offer_source"] = "reviewed"
        outcome = replace(outcome, ledger_event={**outcome.ledger_event, "transition_offer_source": "reviewed"})
    # The button is a program choice, not new character prose or a model verdict.
    # Run after facts, narration and recommendation filtering are final.
    silent_route = runtime.engine.preview_route(current.session.current_node_id, outcome.session.metrics)
    silent_contract = silent_route.get("transition_contract") if silent_route else None
    silent_target = runtime.engine.nodes.get(str(silent_route.get("target_node_id") or "")) if silent_route else None
    silent_completion_session = replace(outcome.session, performance_history=(
        *current.session.performance_history, performance))
    if (not route_changed and not outcome.session.transition_offered
            and runtime.engine.completion_contract_satisfied(silent_completion_session) is True
            and _silent_exit_allowed(runtime.engine.nodes[current.session.current_node_id])
            and isinstance(silent_contract, Mapping) and not silent_contract.get("fallback_offer")
            and isinstance(silent_target, Mapping) and silent_target.get("type") != "ending"
            and silent_target.get("terminal") is not True
            and not current.session.transition_offered and not program_offer_already_issued
            and pending_transition_record(current.session, ledger_events=current.ledger_events,
                                          include_withdrawn=True) is None
            and not departure_projection.get("player_left_current_scene")
            and not departure_projection.get("player_departure_pending")
            and evaluation.transition_intent != "reject"
            and not diagnostics["recovered_ordinary_drafts_reused"]
            and not diagnostics["invalid_offer_local_crops"]
            and not diagnostics["invalid_scene_update_local_crops"]
            and not required_pending(runtime.engine.nodes[current.session.current_node_id],
                                     silent_completion_session,
                                     condition_triggers_enabled=bool(module_options.get("review")))
            and (not module_options.get("review") or (final_fixed_review is not None
                 and not final_fixed_review.body_violations and not final_fixed_review.offer_present))):
        accept = _silent_choice_input(runtime.engine, outcome.session, silent_contract)
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
                "route_id": silent_route["id"], "offer": "", "accept_input": accept,
                "silent": True, "revision": outcome.session.revision,
                "performance": str(performance.get("performance") or ""),
                "visible_blocks": performance_content_blocks(performance),
            }})
            trace_event("completion.silent_offer_issued", route_id=silent_route["id"])

    # Model calls hold no lifecycle lock; serialize only identity revalidation, display refresh, and atomic commit with rename.
    trace_event("turn.finalized", state=trace_state(outcome.session), performance=performance,
                semantic_review_fallback=diagnostics["semantic_review_fallback"],
                semantic_review_fallback_phase=diagnostics["semantic_review_fallback_phase"])
    commit_started_at = time.monotonic()
    try:
        async with character_config_mutation_lock:
            # Character card may change during model call; successful output cannot commit to another catgirl's recovery slot.
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
                stored = await runtime.commit_turn(outcome, performance)
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
            "transition_judge_calls", "dispute_review_attempts", "semantic_rewrite_attempts",
            "transition_cancellations", "missed_initiation_recoveries", "unsafe_suggestions_removed",
            "recovered_ordinary_drafts_reused",
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
        )
        if type(diagnostics.get(key)) in (int, bool)
    }
    log_diagnostics["timings_ms"] = {
        key: value for key in (
            "evaluator_work", "runtime_prepare_work", "actor_work", "narrative_work", "transition_judge_work",
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
    if "narrative_pending" in stored.ledger_events[-1]:
        from .numeric_v2_background_narrative import schedule_narrative
        schedule_narrative(
            config_manager=config_manager, runtime=runtime, previous=current,
            stored=stored, before_commit=before_commit,
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
