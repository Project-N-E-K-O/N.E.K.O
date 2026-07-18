"""分离自由输入路由、支线规划与猫娘演绎，并为各类模型调用提供安全回退。"""  # noqa: DOCSTRING_CJK

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from typing import Any

from config import (
    THEATER_BRANCH_ACTOR_OUTPUT_MAX_TOKENS,
    THEATER_BRANCH_HANDOFF_BACKGROUND_MAX_TOKENS,
    THEATER_BRANCH_HANDOFF_INTENT_MAX_TOKENS,
    THEATER_BRANCH_HANDOFF_OUTPUT_MAX_TOKENS,
    THEATER_BRANCH_HANDOFF_SCENE_TEXT_MAX_TOKENS,
    THEATER_BRANCH_HANDOFF_THEME_MAX_TOKENS,
    THEATER_BRANCH_HANDOFF_TITLE_MAX_TOKENS,
    THEATER_BRANCH_HANDOFF_USER_MESSAGE_MAX_TOKENS,
    THEATER_PLANNER_EVIDENCE_MAX_TOKENS,
    THEATER_PLANNER_INTENT_MAX_TOKENS,
    THEATER_PLANNER_OUTPUT_MAX_TOKENS,
    THEATER_PLANNER_TIMEOUT_SECONDS,
    THEATER_TURN_USER_MESSAGE_MAX_TOKENS,
)
from config.prompts.prompts_theater import (
    build_theater_branch_handoff_prompts,
    build_theater_branch_planner_prompts,
    build_theater_branch_turn_prompts,
    build_theater_route_prompts,
    build_theater_turn_prompts,
)
from utils.llm_client import HumanMessage, SystemMessage, create_chat_llm_async
from utils.logger_config import get_module_logger
from utils.token_tracker import set_call_type
from utils.tokenize import truncate_to_tokens

from . import intent_tracker, model_trace, observability
from .llm_context import (
    THEATER_BRANCH_RECALL_FIELD_MAX_TOKENS as THEATER_BRANCH_RECALL_FIELD_MAX_TOKENS,
    THEATER_BRANCH_RECALL_MAX_FACTS as THEATER_BRANCH_RECALL_MAX_FACTS,
    THEATER_BRANCH_RECALL_MAX_HISTORIES as THEATER_BRANCH_RECALL_MAX_HISTORIES,
    _bounded_completed_branch_recall as _bounded_completed_branch_recall,
    _complete_model_text as _complete_model_text,
    _load_character_profile as _load_character_profile,
    _public_state as _public_state,
    _recent_public_turns as _recent_public_turns,
)
from .llm_fallbacks import (
    _authored_performance_fallback as _authored_performance_fallback,
    _bounded_public_fallback_anchor as _bounded_public_fallback_anchor,
    _fallback_scene_prefix as _fallback_scene_prefix,
    fallback_branch_entry as fallback_branch_entry,
    fallback_branch_handoff as fallback_branch_handoff,
    fallback_branch_turn as fallback_branch_turn,
    fallback_turn as fallback_turn,
)
from .llm_performance_guard import (
    _INTERNAL_META_OUTPUT_PATTERNS as _INTERNAL_META_OUTPUT_PATTERNS,
    _PRIVATE_IDENTIFIER_FIELDS as _PRIVATE_IDENTIFIER_FIELDS,
    _SOFT_PERFORMANCE_REASONS as _SOFT_PERFORMANCE_REASONS,
    _active_story_forbidden_phrases as _active_story_forbidden_phrases,
    _assistant_echoes_user as _assistant_echoes_user,
    _claims_uncommitted_choice_result as _claims_uncommitted_choice_result,
    _dialogue_claims_player_completion as _dialogue_claims_player_completion,
    _dialogue_key as _dialogue_key,
    _exposes_internal_runtime_detail as _exposes_internal_runtime_detail,
    _introduces_ungrounded_named_destination as _introduces_ungrounded_named_destination,
    _mirrors_player_question as _mirrors_player_question,
    _narration_claims_player_action as _narration_claims_player_action,
    _performance_clauses as _performance_clauses,
    _performance_repair_reason as _performance_repair_reason,
    _persona_self_name as _persona_self_name,
    _private_runtime_identifiers as _private_runtime_identifiers,
    _reanswers_previous_question as _reanswers_previous_question,
    _repeats_recent_dialogue as _repeats_recent_dialogue,
    _same_narrative_fact as _same_narrative_fact,
    _semantic_text_anchors as _semantic_text_anchors,
    _story_forbidden_output_patterns as _story_forbidden_output_patterns,
    _violates_author_consent_boundary as _violates_author_consent_boundary,
)
from .llm_response_contracts import (
    THEATER_BRANCH_FACT_CANDIDATE_MAX_ITEMS as THEATER_BRANCH_FACT_CANDIDATE_MAX_ITEMS,
    THEATER_BRANCH_HANDOFF_CLASSIFICATIONS as THEATER_BRANCH_HANDOFF_CLASSIFICATIONS,
    THEATER_BRANCH_HANDOFF_CONTINUE_MIN_CONFIDENCE as THEATER_BRANCH_HANDOFF_CONTINUE_MIN_CONFIDENCE,
    THEATER_BRANCH_HANDOFF_EVIDENCE_MAX_CHARS as THEATER_BRANCH_HANDOFF_EVIDENCE_MAX_CHARS,
    THEATER_BRANCH_HANDOFF_MIN_CONFIDENCE as THEATER_BRANCH_HANDOFF_MIN_CONFIDENCE,
    THEATER_BRANCH_HANDOFF_SUMMARY_MAX_CHARS as THEATER_BRANCH_HANDOFF_SUMMARY_MAX_CHARS,
    THEATER_FREE_INTENT_MIN_CONFIDENCE as THEATER_FREE_INTENT_MIN_CONFIDENCE,
    THEATER_FREE_INTENT_RELATIONS as THEATER_FREE_INTENT_RELATIONS,
    THEATER_RESIDUAL_EVIDENCE_MAX_CHARS as THEATER_RESIDUAL_EVIDENCE_MAX_CHARS,
    THEATER_RESIDUAL_SUMMARY_MAX_CHARS as THEATER_RESIDUAL_SUMMARY_MAX_CHARS,
    THEATER_RESPONSE_FOCUS_EVIDENCE_MAX_CHARS as THEATER_RESPONSE_FOCUS_EVIDENCE_MAX_CHARS,
    THEATER_RESPONSE_FOCUS_TYPES as THEATER_RESPONSE_FOCUS_TYPES,
    _FORBIDDEN_OUTPUT_TERMS as _FORBIDDEN_OUTPUT_TERMS,
    _balanced_json_object_fragments as _balanced_json_object_fragments,
    _empty_route_result as _empty_route_result,
    _load_unique_model_json_object as _load_unique_model_json_object,
    _parse_branch_handoff_output as _parse_branch_handoff_output,
    _parse_branch_turn_output as _parse_branch_turn_output,
    _parse_output as _parse_output,
    _parse_planner_output as _parse_planner_output,
    _parse_residual_intent as _parse_residual_intent,
    _parse_route_output as _parse_route_output,
    _technical_branch_handoff_fallback as _technical_branch_handoff_fallback,
    _technical_route_fallback as _technical_route_fallback,
    verify_response_focus as verify_response_focus,
)


THEATER_TURN_TIMEOUT_SECONDS = 10.0
THEATER_TURN_OUTPUT_MAX_TOKENS = 360
THEATER_CONTEXT_MAX_TOKENS = 500
logger = get_module_logger("services.theater.llm")


def _record_context_incomplete(*, responsibility: str, surface: str) -> None:
    """用固定低基数原因记录关键上下文不完整，不保存被截断正文。"""  # noqa: DOCSTRING_CJK
    observability.record_result(
        responsibility=responsibility,
        surface=surface,
        result_kind="generation",
        outcome="context_incomplete",
    )


async def route_free_input_async(
    *,
    config_manager: Any | None,
    story: dict[str, Any],
    scene: dict[str, Any],
    user_message: str,
    state: dict[str, Any],
    recent_turns: list[dict[str, Any]],
    choice_options: list[dict[str, Any]],
    latent_transitions: list[dict[str, Any]],
) -> dict[str, Any]:
    """用公开语境选择作者边或自由意图；失败时保守停留且不生成角色文案。"""  # noqa: DOCSTRING_CJK
    fallback = _technical_route_fallback()
    prompt_user_message = _complete_model_text(
        user_message,
        THEATER_TURN_USER_MESSAGE_MAX_TOKENS,
    )
    if prompt_user_message is None:
        # 只看玩家原话前缀可能漏掉句尾否定或转折；这种输入不能命中作者边或累计自由意图。
        _record_context_incomplete(
            responsibility="theater_router", surface="free_input"
        )
        return fallback
    api_config = _model_config(config_manager)
    if not api_config:
        logger.info("Theater route stays put: reason=model_config_missing")
        # 缺少配置属于可观察回退，但没有模型调用样本或 token 消耗。
        observability.record_result(
            responsibility="theater_router",
            surface="free_input",
            result_kind="generation",
            outcome="model_config_missing",
        )
        return fallback
    prompt_story = dict(story)
    prompt_story["background"] = truncate_to_tokens(
        str(story.get("background") or story.get("world_seed") or ""),
        THEATER_CONTEXT_MAX_TOKENS,
    )
    system_prompt, user_prompt = build_theater_route_prompts(
        story=prompt_story,
        scene=scene,
        user_message=prompt_user_message,
        public_state=_public_state(story, state),
        recent_turns=_recent_public_turns(recent_turns),
        choice_options=choice_options,
        latent_transitions=latent_transitions,
        current_dynamic_intent=(
            state.get("dynamic_intent")
            if isinstance(state.get("dynamic_intent"), dict)
            else {}
        ),
        current_pending_intent=(
            state.get("pending_intent")
            if isinstance(state.get("pending_intent"), dict)
            else {}
        ),
    )
    try:
        # Router 使用独立标签，避免自由意图判断与角色演绎的成本和失败率混在一起。
        result = await _invoke_model_once(
            api_config,
            system_prompt,
            user_prompt,
            call_type="theater_router",
            surface="free_input",
            timeout_seconds=THEATER_TURN_TIMEOUT_SECONDS,
            max_completion_tokens=THEATER_TURN_OUTPUT_MAX_TOKENS,
        )
    except Exception as exc:
        # 路由失败绝不能靠猜测推进；保留错误类型即可，不记录玩家原话和模型输出。
        logger.warning(
            "Theater route stays put: reason=model_call_failed error=%s",
            type(exc).__name__,
        )
        observability.record_result(
            responsibility="theater_router",
            surface="free_input",
            result_kind="generation",
            outcome="model_call_failed",
        )
        return fallback
    parsed = _parse_route_output(
        getattr(result, "content", ""),
        allowed_choice_ids={
            str(item.get("choice_id") or "") for item in choice_options
        },
        allowed_intent_ids={
            str(item.get("intent_id") or "") for item in latent_transitions
        },
        user_message=user_message,
    )
    if parsed is None:
        repair_prompt = (
            user_prompt
            + "\n格式修复：上一版 Router 输出不是合法 JSON。只返回一个完整 JSON 对象，字段固定为 "
            "route_kind、matched_choice_id、authored_intent_id、free_intent、residual_intent、response_focus；不得输出解释或 Markdown。"
        )
        try:
            # Router 结果尚未更新任何意图计数，坏格式可以在同一回合提交前修复一次。
            repaired_result = await _invoke_model_once(
                api_config,
                system_prompt,
                repair_prompt,
                call_type="theater_repair",
                surface="free_input",
                timeout_seconds=THEATER_TURN_TIMEOUT_SECONDS,
                max_completion_tokens=THEATER_TURN_OUTPUT_MAX_TOKENS,
            )
        except Exception as exc:
            logger.warning(
                "Theater route stays put: reason=repair_call_failed error=%s",
                type(exc).__name__,
            )
            observability.record_result(
                responsibility="theater_router",
                surface="free_input",
                result_kind="generation",
                outcome="repair_call_failed",
            )
            return fallback
        parsed = _parse_route_output(
            getattr(repaired_result, "content", ""),
            allowed_choice_ids={
                str(item.get("choice_id") or "") for item in choice_options
            },
            allowed_intent_ids={
                str(item.get("intent_id") or "") for item in latent_transitions
            },
            user_message=user_message,
        )
        if parsed is None:
            logger.warning("Theater route stays put: reason=repair_rejected")
            observability.record_result(
                responsibility="theater_router",
                surface="free_input",
                result_kind="generation",
                outcome="repair_rejected",
            )
            return fallback
    if str(
        parsed.get("route_kind") or ""
    ) == "free_intent" and not intent_tracker.evidence_message_fits(user_message):
        # 自由意图需要保存完整玩家证据；不能让 Router 看完长输入后只把前 240 字写入 Session。
        _record_context_incomplete(
            responsibility="theater_router", surface="free_input"
        )
        return fallback
    # Router 只记录结构化结果是否可用，不记录命中的 Choice 或自由意图内容。
    observability.record_result(
        responsibility="theater_router",
        surface="free_input",
        result_kind="generation",
        outcome="accepted",
    )
    return parsed


async def classify_active_branch_handoff_async(
    *,
    config_manager: Any | None,
    story: dict[str, Any],
    scene: dict[str, Any],
    user_message: str,
    state: dict[str, Any],
    recent_turns: list[dict[str, Any]],
    active_branch: dict[str, Any],
) -> dict[str, Any]:
    """识别活动支线转交候选；模型无权关闭支线或创建下一条支线。"""  # noqa: DOCSTRING_CJK
    fallback = _technical_branch_handoff_fallback()
    patch = active_branch.get("patch") if isinstance(active_branch, dict) else None
    if not isinstance(state, dict) or not isinstance(patch, dict):
        # 损坏的候选上下文不能交给模型补全；状态恢复和关闭策略仍由服务层决定。
        observability.record_result(
            responsibility="theater_router",
            surface="branch_handoff",
            result_kind="generation",
            outcome="invalid_context",
        )
        return fallback
    seed_intent = str(patch.get("seed_intent") or "").strip()
    objective = str(patch.get("objective") or "").strip()
    if not seed_intent or not objective:
        observability.record_result(
            responsibility="theater_router",
            surface="branch_handoff",
            result_kind="generation",
            outcome="invalid_context",
        )
        return fallback
    normalized_user_message = " ".join(str(user_message or "").strip().split())
    prompt_user_message = _complete_model_text(
        normalized_user_message,
        THEATER_BRANCH_HANDOFF_USER_MESSAGE_MAX_TOKENS,
    )
    prompt_seed_intent = _complete_model_text(
        seed_intent,
        THEATER_BRANCH_HANDOFF_INTENT_MAX_TOKENS,
    )
    prompt_objective = _complete_model_text(
        objective,
        THEATER_BRANCH_HANDOFF_INTENT_MAX_TOKENS,
    )
    if (
        prompt_user_message is None
        or prompt_seed_intent is None
        or prompt_objective is None
    ):
        # 玩家原话或当前活动语义不完整时，分类器无权决定继续旧支线还是转交。
        _record_context_incomplete(
            responsibility="theater_router", surface="branch_handoff"
        )
        return fallback
    api_config = _model_config(config_manager)
    if not api_config:
        logger.info(
            "Theater branch handoff stays uncertain: reason=model_config_missing"
        )
        observability.record_result(
            responsibility="theater_router",
            surface="branch_handoff",
            result_kind="generation",
            outcome="model_config_missing",
        )
        return fallback

    # 仅复制并裁剪公开语义；服务端身份、预算、计数、事实与 revision 均不进入提示词。
    prompt_story = {
        "title": truncate_to_tokens(
            str(story.get("title") or ""),
            THEATER_BRANCH_HANDOFF_TITLE_MAX_TOKENS,
        ),
        "theme": truncate_to_tokens(
            str(story.get("theme") or ""),
            THEATER_BRANCH_HANDOFF_THEME_MAX_TOKENS,
        ),
        "background": truncate_to_tokens(
            str(story.get("background") or story.get("world_seed") or ""),
            THEATER_BRANCH_HANDOFF_BACKGROUND_MAX_TOKENS,
        ),
    }
    prompt_scene = {
        "title": truncate_to_tokens(
            str(scene.get("title") or ""),
            THEATER_BRANCH_HANDOFF_TITLE_MAX_TOKENS,
        ),
        "text": truncate_to_tokens(
            str(scene.get("text") or ""),
            THEATER_BRANCH_HANDOFF_SCENE_TEXT_MAX_TOKENS,
        ),
    }
    prompt_branch = {
        "patch": {
            "seed_intent": prompt_seed_intent,
            "objective": prompt_objective,
        }
    }
    system_prompt, user_prompt = build_theater_branch_handoff_prompts(
        story=prompt_story,
        scene=prompt_scene,
        user_message=prompt_user_message,
        recent_turns=_recent_public_turns(recent_turns),
        active_branch=prompt_branch,
    )
    try:
        # 转交分类沿用 Router 责任标签，但使用独立 surface，不能与普通路由准确率混为一谈。
        result = await _invoke_model_once(
            api_config,
            system_prompt,
            user_prompt,
            call_type="theater_router",
            surface="branch_handoff",
            timeout_seconds=THEATER_TURN_TIMEOUT_SECONDS,
            max_completion_tokens=THEATER_BRANCH_HANDOFF_OUTPUT_MAX_TOKENS,
        )
    except Exception as exc:
        # 失败日志只记录异常类型；玩家原话、意图摘要和模型正文均不得进入日志或指标。
        logger.warning(
            "Theater branch handoff stays uncertain: reason=model_call_failed error=%s",
            type(exc).__name__,
        )
        observability.record_result(
            responsibility="theater_router",
            surface="branch_handoff",
            result_kind="generation",
            outcome="model_call_failed",
        )
        return fallback

    parsed = _parse_branch_handoff_output(
        getattr(result, "content", ""),
        user_message=normalized_user_message,
    )
    if parsed is None:
        # 分类结果不拥有状态权限，坏格式直接保守降级；不为它增加 Repair 调用。
        logger.warning(
            "Theater branch handoff stays uncertain: reason=invalid_model_output"
        )
        observability.record_result(
            responsibility="theater_router",
            surface="branch_handoff",
            result_kind="generation",
            outcome="invalid_model_output",
        )
        return fallback
    parsed["route_delivery"] = "accepted"
    observability.record_result(
        responsibility="theater_router",
        surface="branch_handoff",
        result_kind="generation",
        outcome="accepted",
    )
    return parsed


async def plan_runtime_branch_async(
    *,
    config_manager: Any | None,
    story: dict[str, Any],
    scene: dict[str, Any],
    current_node_id: str,
    current_node: dict[str, Any],
    state: dict[str, Any],
    dynamic_intent: dict[str, Any],
    recent_turns: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """生成一次 Runtime Branch Patch 候选；失败时不猜测、不重试且不写入状态。"""  # noqa: DOCSTRING_CJK
    intent_summary = str(dynamic_intent.get("intent_summary") or "")
    evidence_messages = [
        str(item)
        for item in dynamic_intent.get("evidence_messages") or []
        if str(item).strip()
    ][-3:]
    prompt_intent_summary = _complete_model_text(
        intent_summary,
        THEATER_PLANNER_INTENT_MAX_TOKENS,
    )
    prompt_evidence_messages = [
        _complete_model_text(item, THEATER_PLANNER_EVIDENCE_MAX_TOKENS)
        for item in evidence_messages
    ]
    if prompt_intent_summary is None or any(
        item is None for item in prompt_evidence_messages
    ):
        # Planner 会把意图直接转成可执行 Patch；任一证据被截断都必须停止，而不是规划一个片段意图。
        _record_context_incomplete(
            responsibility="theater_planner", surface="branch_entry"
        )
        return None
    api_config = _model_config(config_manager)
    if not api_config:
        logger.info("Theater branch planner stops: reason=model_config_missing")
        observability.record_result(
            responsibility="theater_planner",
            surface="branch_entry",
            result_kind="generation",
            outcome="model_config_missing",
        )
        return None

    prompt_story = dict(story)
    # 长篇背景只保留世界约束所需摘要；World Contract、Goal 与 Ending Domain 必须完整保留供确定性校验对齐。
    prompt_story["background"] = truncate_to_tokens(
        str(story.get("background") or story.get("world_seed") or ""),
        THEATER_CONTEXT_MAX_TOKENS,
    )
    # Planner 只接收自由意图的公开语义和最近证据，服务端 intent_key、origin 与 streak 不得透传。
    prompt_intent = {
        "intent_summary": prompt_intent_summary,
        "evidence_messages": [str(item) for item in prompt_evidence_messages],
    }
    system_prompt, user_prompt = build_theater_branch_planner_prompts(
        story=prompt_story,
        scene=scene,
        current_node_id=current_node_id,
        current_node=current_node,
        public_state=_public_state(story, state),
        dynamic_intent=prompt_intent,
        recent_turns=_recent_public_turns(recent_turns),
        # 这些是作者稳定 Goal ID，只用于阻止 Planner 再次选择已完成汇流出口。
        completed_goal_ids=list(state.get("completed_goal_ids") or []),
    )
    try:
        # Planner 独立计量且不自动重试，避免同一阈值事件生成多个互相竞争的 Patch。
        result = await _invoke_model_once(
            api_config,
            system_prompt,
            user_prompt,
            call_type="theater_planner",
            surface="branch_entry",
            timeout_seconds=THEATER_PLANNER_TIMEOUT_SECONDS,
            max_completion_tokens=THEATER_PLANNER_OUTPUT_MAX_TOKENS,
        )
    except Exception as exc:
        # 日志只保留错误类型，不记录玩家证据、提示词或模型原始候选。
        logger.warning(
            "Theater branch planner stops: reason=model_call_failed error=%s",
            type(exc).__name__,
        )
        observability.record_result(
            responsibility="theater_planner",
            surface="branch_entry",
            result_kind="generation",
            outcome="model_call_failed",
        )
        return None
    parsed = _parse_planner_output(getattr(result, "content", ""))
    if parsed is None:
        logger.warning("Theater branch planner stops: reason=invalid_model_output")
        observability.record_result(
            responsibility="theater_planner",
            surface="branch_entry",
            result_kind="generation",
            outcome="invalid_model_output",
        )
        return None
    # 合同层会另记 Patch 接受/拒绝；此处只表示 Planner JSON 可解析。
    observability.record_result(
        responsibility="theater_planner",
        surface="branch_entry",
        result_kind="generation",
        outcome="accepted",
    )
    return parsed


async def generate_branch_entry_async(
    *,
    config_manager: Any | None,
    lanlan_name: str,
    story: dict[str, Any],
    scene: dict[str, Any],
    node: dict[str, Any],
    user_message: str,
    state: dict[str, Any],
    recent_turns: list[dict[str, Any]],
    patch: dict[str, Any],
    response_focus: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """严格生成支线入口演出；合法 Patch 遇到模型故障时改用确定性安全演出。"""  # noqa: DOCSTRING_CJK
    entry_callback = (
        str(patch.get("entry_callback") or "").strip()
        if isinstance(patch, dict)
        else ""
    )
    if not entry_callback:
        # 缺少入口锚点的对象不是可执行 Patch，即使上游误传也不能激活支线。
        return None
    # 安全演出只复用合同已验证的公开锚点，不解释用户内容，也不创建任何剧本专属事实。
    fallback = fallback_branch_entry(
        scene_title=str(scene.get("title") or ""),
        activity_summary=str(patch.get("seed_intent") or ""),
        private_identifiers=_private_runtime_identifiers(
            patch,
            story.get("world_contract"),
            state,
        ),
    )
    prompt_user_message = _complete_model_text(
        user_message,
        THEATER_TURN_USER_MESSAGE_MAX_TOKENS,
    )
    if prompt_user_message is None:
        # 合法 Patch 仍可用已验证入口锚点激活，但不让截断后的玩家原话进入人格演绎。
        _record_context_incomplete(
            responsibility="theater_actor", surface="branch_entry"
        )
        return fallback
    api_config = _model_config(config_manager)
    if not api_config:
        logger.info(
            "Theater branch entry uses safe fallback: reason=model_config_missing"
        )
        # 配置缺失没有模型调用样本，但仍需计入入口演出的回退率。
        observability.record_result(
            responsibility="theater_actor",
            surface="branch_entry",
            result_kind="generation",
            outcome="model_config_missing",
        )
        return fallback

    prompt_story = dict(story)
    # 与普通 Actor 使用同一背景预算，避免入口回合因附加 Patch 放大不相关世界文本。
    prompt_story["background"] = truncate_to_tokens(
        str(story.get("background") or story.get("world_seed") or ""),
        THEATER_CONTEXT_MAX_TOKENS,
    )
    # 人格文件只读取一次并复用于 Prompt 与输出护栏，避免入口调用产生不一致的角色快照。
    character_profile = _load_character_profile(config_manager, lanlan_name)
    verified_response_focus = verify_response_focus(
        response_focus,
        user_message=user_message,
    )
    system_prompt, user_prompt = build_theater_turn_prompts(
        lanlan_name=lanlan_name,
        story=prompt_story,
        scene=scene,
        node=node,
        user_message=prompt_user_message,
        progress_kind="branch_entry",
        # Planner 的 entry_callback 不再进入公开演出或 Actor Prompt；它只保留旧 Patch 结构兼容。
        callback="",
        public_state=_public_state(story, state),
        recent_turns=_recent_public_turns(recent_turns),
        character_profile=character_profile,
        choice_options=[],
        runtime_branch_patch=patch,
        response_focus=verified_response_focus,
    )
    try:
        # 入口首版仍计入 Actor；只有尚未展示、尚未写状态的坏格式结果才允许下方修复一次。
        result = await _invoke_model_once(
            api_config,
            system_prompt,
            user_prompt,
            call_type="theater_actor",
            surface="branch_entry",
            timeout_seconds=THEATER_TURN_TIMEOUT_SECONDS,
            max_completion_tokens=THEATER_TURN_OUTPUT_MAX_TOKENS,
        )
    except Exception as exc:
        # 不记录玩家原话、Patch 或模型原文，只记录可观测的供应商错误类型。
        logger.warning(
            "Theater branch entry uses safe fallback: reason=model_call_failed error=%s",
            type(exc).__name__,
        )
        observability.record_result(
            responsibility="theater_actor",
            surface="branch_entry",
            result_kind="generation",
            outcome="model_call_failed",
        )
        return fallback

    def _entry_output_check(raw: Any) -> tuple[dict[str, Any] | None, str]:
        """解析一次入口候选并返回脱敏错误码，供首版与单次 Repair 共用。"""  # noqa: DOCSTRING_CJK
        candidate = _parse_output(
            raw,
            progress_kind="branch_entry",
        )
        check = _performance_repair_reason(
            candidate,
            progress_kind="branch_entry",
            user_message=user_message,
            node=node,
            character_profile=character_profile,
            story=story,
            state=state,
            choice_options=[],
            private_identifiers=_private_runtime_identifiers(
                patch,
                story.get("world_contract"),
                state,
            ),
        )
        if (
            not check
            and candidate is not None
            and _repeats_recent_dialogue(
                str(candidate.get("dialogue") or ""), recent_turns
            )
        ):
            # 复读同样不能激活支线，但允许在原子提交前按同一 Patch 修复一次。
            check = "recent_dialogue_repeated"
        return candidate, check or ("invalid_model_output" if candidate is None else "")

    parsed, repair_reason = _entry_output_check(getattr(result, "content", ""))
    if repair_reason:
        repair_prompt = (
            user_prompt
            + "\n格式修复：上一版入口输出未通过检查（"
            + repair_reason
            + "）。只返回一个完整 JSON 对象，字段固定为 narration、dialogue、choice_rewrites；"
            "choice_rewrites 必须为空数组，narration 必须为空字符串，dialogue 直接回应玩家本轮意图；"
            "不得输出 Markdown、解释、内部字段或纠错过程。"
        )
        try:
            # 首版结果从未公开也未写入 Session；Repair 仍在同一原子提交前，不会产生两条竞争支线。
            repaired_result = await _invoke_model_once(
                api_config,
                system_prompt,
                repair_prompt,
                call_type="theater_repair",
                surface="branch_entry",
                timeout_seconds=THEATER_TURN_TIMEOUT_SECONDS,
                max_completion_tokens=THEATER_TURN_OUTPUT_MAX_TOKENS,
            )
        except Exception as exc:
            # 修复调用同样只记录错误类型，不能把坏输出、Patch 或玩家原话写入日志。
            logger.warning(
                "Theater branch entry uses safe fallback: reason=repair_call_failed check=%s error=%s",
                repair_reason,
                type(exc).__name__,
            )
            observability.record_result(
                responsibility="theater_actor",
                surface="branch_entry",
                result_kind="generation",
                outcome="repair_call_failed",
            )
            return fallback
        parsed, remaining_reason = _entry_output_check(
            getattr(repaired_result, "content", "")
        )
        if remaining_reason:
            # 两次坏输出都不能公开；使用权威锚点与无事实台词，避免合法自由支线被静默丢弃。
            logger.warning(
                "Theater branch entry uses safe fallback: reason=repair_rejected first=%s second=%s",
                repair_reason,
                remaining_reason,
            )
            observability.record_result(
                responsibility="theater_actor",
                surface="branch_entry",
                result_kind="generation",
                outcome="repair_rejected",
            )
            return fallback
    if parsed is None:
        # 类型收窄保护：意外空候选也只能降级为无事实演出，绝不提交未校验模型文本。
        return fallback
    # Planner 文本只有结构合同，没有事实提交证明；入口旁白保持为空，避免它绕过 Actor 护栏制造双重现实。
    parsed["narration"] = ""
    parsed["choice_rewrites"] = []
    # 是否经过 Repair 可由独立 repair 调用数推导，此处统一记录最终可提交入口。
    observability.record_result(
        responsibility="theater_actor",
        surface="branch_entry",
        result_kind="generation",
        outcome="accepted",
    )
    return parsed


async def generate_branch_turn_async(
    *,
    config_manager: Any | None,
    lanlan_name: str,
    story: dict[str, Any],
    scene: dict[str, Any],
    user_message: str,
    state: dict[str, Any],
    recent_turns: list[dict[str, Any]],
    active_branch: dict[str, Any],
    branch_facts: list[dict[str, Any]],
    node: dict[str, Any] | None = None,
    response_focus: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成一次活动支线演出与事实候选；模型失败时返回无事实的安全回应。"""  # noqa: DOCSTRING_CJK
    patch = active_branch.get("patch") if isinstance(active_branch, dict) else None
    fallback = fallback_branch_turn(
        lanlan_name=lanlan_name,
        scene=scene,
        user_message=user_message,
        activity_summary=(
            str(patch.get("seed_intent") or "") if isinstance(patch, dict) else ""
        ),
        has_committed_progress=bool(branch_facts),
        private_identifiers=_private_runtime_identifiers(
            active_branch,
            patch,
            branch_facts,
            story.get("world_contract"),
            state,
        ),
    )
    if not isinstance(patch, dict):
        return fallback
    verified_response_focus = verify_response_focus(
        response_focus,
        user_message=user_message,
    )
    prompt_user_message = _complete_model_text(
        user_message,
        THEATER_TURN_USER_MESSAGE_MAX_TOKENS,
    )
    if prompt_user_message is None:
        # 支线 Actor 的事实候选拥有提交入口；只要本轮原话不完整，就必须走无事实、无预算降级。
        _record_context_incomplete(
            responsibility="theater_actor", surface="branch_turn"
        )
        return fallback
    api_config = _model_config(config_manager)
    if not api_config:
        logger.info(
            "Theater branch turn uses safe fallback: reason=model_config_missing"
        )
        observability.record_result(
            responsibility="theater_actor",
            surface="branch_turn",
            result_kind="generation",
            outcome="model_config_missing",
        )
        return fallback

    prompt_story = dict(story)
    # 支线 Actor 沿用普通演绎背景预算，Patch 与已提交事实仅提供当前短支线所需增量。
    prompt_story["background"] = truncate_to_tokens(
        str(story.get("background") or story.get("world_seed") or ""),
        THEATER_CONTEXT_MAX_TOKENS,
    )
    character_profile = _load_character_profile(config_manager, lanlan_name)
    system_prompt, user_prompt = build_theater_branch_turn_prompts(
        lanlan_name=lanlan_name,
        story=prompt_story,
        scene=scene,
        user_message=prompt_user_message,
        public_state=_public_state(story, state),
        recent_turns=_recent_public_turns(recent_turns),
        character_profile=character_profile,
        patch=patch,
        branch_facts=branch_facts,
        node=node,
        response_focus=verified_response_focus,
    )
    try:
        # 普通支线回合只调用 Actor，不再重复调用 Planner；事实权威仍由 branch_runtime 决定。
        result = await _invoke_model_once(
            api_config,
            system_prompt,
            user_prompt,
            call_type="theater_actor",
            surface="branch_turn",
            timeout_seconds=THEATER_TURN_TIMEOUT_SECONDS,
            max_completion_tokens=THEATER_BRANCH_ACTOR_OUTPUT_MAX_TOKENS,
        )
    except Exception as exc:
        logger.warning(
            "Theater branch turn uses safe fallback: reason=model_call_failed error=%s",
            type(exc).__name__,
        )
        observability.record_result(
            responsibility="theater_actor",
            surface="branch_turn",
            result_kind="generation",
            outcome="model_call_failed",
        )
        return fallback
    parsed = _parse_branch_turn_output(getattr(result, "content", ""))
    # 单回合最多进行一种 Repair；格式修复成功后不再叠加进度复核。
    format_repaired = False
    if parsed is None:
        repair_prompt = (
            user_prompt
            + "\n格式修复：上一版活动支线输出不是合法合同。只返回一个完整 JSON 对象，"
            "顶层字段固定为 narration、dialogue、fact_candidates；不得输出 Markdown、解释或内部字段。"
            "fact_candidates 只能描述本轮旁白或对白已经公开发生的结果，不能补做玩家未实施的动作。"
        )
        try:
            # 首版坏格式尚未公开、未写事实也未消耗预算，因此只允许一次同回合 Repair。
            repaired_result = await _invoke_model_once(
                api_config,
                system_prompt,
                repair_prompt,
                call_type="theater_repair",
                surface="branch_turn",
                timeout_seconds=THEATER_TURN_TIMEOUT_SECONDS,
                max_completion_tokens=THEATER_BRANCH_ACTOR_OUTPUT_MAX_TOKENS,
            )
        except Exception as exc:
            logger.warning(
                "Theater branch turn uses safe fallback: reason=repair_call_failed error=%s",
                type(exc).__name__,
            )
            observability.record_result(
                responsibility="theater_actor",
                surface="branch_turn",
                result_kind="generation",
                outcome="repair_call_failed",
            )
            return fallback
        parsed = _parse_branch_turn_output(getattr(repaired_result, "content", ""))
        if parsed is None:
            logger.warning(
                "Theater branch turn uses safe fallback: reason=repair_rejected"
            )
            observability.record_result(
                responsibility="theater_actor",
                surface="branch_turn",
                result_kind="generation",
                outcome="repair_rejected",
            )
            return fallback
        format_repaired = True
    existing_fact_roles = {
        str(item.get("fact_role") or "")
        for item in branch_facts
        if isinstance(item, dict) and str(item.get("fact_role") or "")
    }
    allowed_fact_roles = {
        str(item.get("fact_role") or "")
        for item in patch.get("allowed_new_facts") or []
        if isinstance(item, dict) and str(item.get("fact_role") or "")
    }
    new_candidate_roles = {
        str(item.get("fact_role") or "")
        for item in parsed.get("fact_candidates") or []
        if isinstance(item, dict)
    } & (allowed_fact_roles - existing_fact_roles)
    if (
        not format_repaired
        and verified_response_focus.get("focus_type") == "action"
        and verified_response_focus.get("requires_state_change") is True
        and allowed_fact_roles - existing_fact_roles
        and not new_candidate_roles
    ):
        progress_repair_prompt = (
            user_prompt
            + "\n进度复核：上一版没有提出任何尚未提交的事实候选。请重新逐条核对“当前待推进Beat”、"
            "“尚未提交事实合同”和玩家本轮原话；若玩家已明确实施可观察行动，旁白必须承认并返回对应候选；"
            "若只是询问、提议或计划，fact_candidates 仍返回空数组。只返回完整 Actor JSON。"
        )
        try:
            # 复核只在首版没有任何新事实角色时发生；失败或仍无进度时保留首版安全演出。
            progress_repair_result = await _invoke_model_once(
                api_config,
                system_prompt,
                progress_repair_prompt,
                call_type="theater_repair",
                surface="branch_turn",
                timeout_seconds=THEATER_TURN_TIMEOUT_SECONDS,
                max_completion_tokens=THEATER_BRANCH_ACTOR_OUTPUT_MAX_TOKENS,
            )
        except Exception:
            progress_repair_result = None
        repaired_progress = (
            _parse_branch_turn_output(getattr(progress_repair_result, "content", ""))
            if progress_repair_result is not None
            else None
        )
        repaired_roles = {
            str(item.get("fact_role") or "")
            for item in (repaired_progress or {}).get("fact_candidates") or []
            if isinstance(item, dict)
        } & (allowed_fact_roles - existing_fact_roles)
        if repaired_progress is not None and repaired_roles:
            # 只有确实补出尚未提交白名单角色时才替换首版，避免复核降低原有有效回应质量。
            parsed = repaired_progress
    performance = {
        "narration": parsed["narration"],
        "dialogue": parsed["dialogue"],
        "choice_rewrites": [],
    }
    guard_reason = _performance_repair_reason(
        performance,
        progress_kind="branch_turn",
        user_message=user_message,
        # 活动支线仍位于当前作者节点内，必须继承同一组节点级禁用对白和输出护栏。
        node=node if isinstance(node, dict) else {},
        character_profile=character_profile,
        story=story,
        state=state,
        private_identifiers=_private_runtime_identifiers(
            active_branch,
            patch,
            branch_facts,
            story.get("world_contract"),
            state,
        ),
    )
    if guard_reason or _repeats_recent_dialogue(parsed["dialogue"], recent_turns):
        # 演绎护栏失败时连同事实候选一起丢弃，避免展示内容与权威事实产生分叉。
        logger.warning(
            "Theater branch turn uses safe fallback: reason=actor_output_rejected check=%s",
            guard_reason or "recent_dialogue_repeated",
        )
        observability.record_result(
            responsibility="theater_actor",
            surface="branch_turn",
            result_kind="generation",
            outcome="actor_output_rejected",
        )
        return fallback
    # 事实候选仍需服务端合同层裁决，这里仅表示演绎与结构护栏通过。
    observability.record_result(
        responsibility="theater_actor",
        surface="branch_turn",
        result_kind="generation",
        outcome="accepted",
    )
    return parsed


async def generate_turn_async(
    *,
    config_manager: Any | None,
    lanlan_name: str,
    story: dict[str, Any],
    scene: dict[str, Any],
    node: dict[str, Any],
    user_message: str,
    progress_kind: str,
    callback: str,
    state: dict[str, Any],
    recent_turns: list[dict[str, Any]],
    choice_options: list[dict[str, Any]] | None = None,
    pullback_intent_summary: str = "",
    completed_branch_recall: list[dict[str, Any]] | None = None,
    response_focus: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成一次结构化演绎；配置缺失、超时或坏输出时使用作者文本。"""  # noqa: DOCSTRING_CJK
    # History 召回先按 token 截断，既供 Prompt 使用，也让技术回退只判断“是否有已确认上下文”。
    bounded_branch_recall = _bounded_completed_branch_recall(completed_branch_recall)
    fallback = fallback_turn(
        lanlan_name=lanlan_name,
        scene=scene,
        node=node,
        user_message=user_message,
        progress_kind=progress_kind,
        callback=callback,
        has_scene_notes=bool(state.get("scene_notes")),
        recent_turns=recent_turns,
        choice_options=list(choice_options or []),
        completed_branch_recall=bounded_branch_recall,
    )
    prompt_user_message = _complete_model_text(
        user_message,
        THEATER_TURN_USER_MESSAGE_MAX_TOKENS,
    )
    if prompt_user_message is None:
        # 普通 Actor 没有状态提交权，但仍不能基于半句玩家原话生成会被写入公开历史的回应。
        _record_context_incomplete(
            responsibility="theater_actor", surface=progress_kind
        )
        return fallback
    api_config = _model_config(config_manager)
    if not api_config:
        logger.info(
            "Theater turn uses author fallback: reason=model_config_missing progress=%s node=%s catgirl=%s",
            progress_kind,
            str(node.get("node_id") or ""),
            lanlan_name,
        )
        # 配置缺失不产生调用样本，但必须计入该演出场景的回退率。
        observability.record_result(
            responsibility="theater_actor",
            surface=progress_kind,
            result_kind="generation",
            outcome="model_config_missing",
        )
        return fallback

    # Scene Note 只辅助无权威普通演绎；Router、Planner 与 Branch Actor 不读取这类可截断笔记。
    public_state = _public_state(story, state, include_scene_notes=True)
    prompt_story = dict(story)
    prompt_story["background"] = truncate_to_tokens(
        str(story.get("background") or story.get("world_seed") or ""),
        THEATER_CONTEXT_MAX_TOKENS,
    )
    character_profile = _load_character_profile(config_manager, lanlan_name)
    system_prompt, user_prompt = build_theater_turn_prompts(
        lanlan_name=lanlan_name,
        story=prompt_story,
        scene=scene,
        node=node,
        user_message=prompt_user_message,
        progress_kind=progress_kind,
        callback=truncate_to_tokens(callback, 120),
        public_state=public_state,
        recent_turns=_recent_public_turns(recent_turns),
        character_profile=character_profile,
        choice_options=list(choice_options or []),
        pullback_intent_summary=pullback_intent_summary,
        completed_branch_recall=bounded_branch_recall,
        response_focus=(
            dict(response_focus) if isinstance(response_focus, dict) else {}
        ),
    )
    try:
        # Actor 标签只覆盖首版角色演绎；纠错调用使用单独 Repair 标签。
        result = await _invoke_model_once(
            api_config,
            system_prompt,
            user_prompt,
            call_type="theater_actor",
            surface=progress_kind,
            timeout_seconds=THEATER_TURN_TIMEOUT_SECONDS,
            max_completion_tokens=THEATER_TURN_OUTPUT_MAX_TOKENS,
        )
    except Exception as exc:
        # 不记录提示词、玩家输入或模型原文，只记录可定位的失败类型，避免下一次只能从固定台词反推原因。
        logger.warning(
            "Theater turn uses author fallback: reason=model_call_failed error=%s progress=%s node=%s catgirl=%s",
            type(exc).__name__,
            progress_kind,
            str(node.get("node_id") or ""),
            lanlan_name,
        )
        observability.record_result(
            responsibility="theater_actor",
            surface=progress_kind,
            result_kind="generation",
            outcome="model_call_failed",
        )
        return fallback
    parsed = _parse_output(
        getattr(result, "content", ""),
        progress_kind=progress_kind,
    )
    if parsed is not None:
        parsed["choice_rewrites"] = []
    authored_performance = progress_kind in {"opening", "graph_progress"}
    grounding_text = json.dumps(
        {
            "background": story.get("background") or story.get("world_seed") or "",
            "scene": scene,
            "public_state": public_state,
            "recent_turns": _recent_public_turns(recent_turns),
            "choice_options": list(choice_options or []),
            "completed_branch_recall": bounded_branch_recall,
            "user_message": user_message,
        },
        ensure_ascii=False,
    )
    private_identifiers = _private_runtime_identifiers(
        node,
        list(choice_options or []),
        state,
    )
    performance_repair_reason = _performance_repair_reason(
        parsed,
        progress_kind=progress_kind,
        user_message=user_message,
        node=node,
        character_profile=character_profile,
        story=story,
        state=state,
        grounding_text=grounding_text,
        choice_options=list(choice_options or []),
        private_identifiers=private_identifiers,
        recent_turns=recent_turns,
        response_focus=(
            dict(response_focus) if isinstance(response_focus, dict) else {}
        ),
    )
    # 普通 Actor 的软语义只影响观感，不拥有服务端状态，也不触发额外模型调用。
    soft_performance = performance_repair_reason in _SOFT_PERFORMANCE_REASONS
    if parsed is not None and (not performance_repair_reason or soft_performance):
        repair_reason = ""
    else:
        # 只有解析失败、明确抢跑或作者/世界/关系硬边界才获得一次 Repair。
        repair_reason = performance_repair_reason
    if repair_reason:
        # 只对可机械判定的结构与权威边界重试一次；开放式文风和按钮新鲜度不进入循环评判。
        if progress_kind == "roleplay_response":
            correction = (
                "\n纠错重试：上一版输出未通过检查（"
                + repair_reason
                + "）。请重新输出完整 JSON，不得提及纠错过程；choice_rewrites 必须为空数组。"
                "不得使用目标节点列出的禁用对白词。"
                "玩家本轮提出问题时必须先直接回答，不得把同一个问题换种说法反问玩家。"
                "上一轮问题已经回答后，本轮若是评价或态度，只回应当前评价，不得重新解释上一轮主题。"
                "当前推荐项仍是未执行候选，不得感谢玩家完成它、不得把作者回调或目标结果写成既成事实。"
                "不得编造公开上下文中没有出现的命名地点；若去向尚未确定，只回答已经公开的最近目的地。"
                "内部规则只能执行，不能在旁白、对白或推荐项里解释、承诺或换一种说法复述。"
            )
        else:
            correction = (
                "\n纠错重试：上一版输出未通过检查（"
                + repair_reason
                + "）。请重新输出完整 JSON，不得提及纠错过程。优先采用作者对白原文，"
                "不得复述玩家，不得增加口癖、命令、强迫或单方批准，也不得使用目标节点列出的禁用对白词。"
                "故事输出硬边界同时约束旁白和对白；内部规则只能执行，不能由猫娘说给玩家。"
            )
        try:
            # Repair 独立计量，便于区分首版质量问题与供应商调用故障。
            repaired_result = await _invoke_model_once(
                api_config,
                system_prompt,
                user_prompt + correction,
                call_type="theater_repair",
                surface=progress_kind,
                timeout_seconds=THEATER_TURN_TIMEOUT_SECONDS,
                max_completion_tokens=THEATER_TURN_OUTPUT_MAX_TOKENS,
            )
        except Exception as exc:
            logger.warning(
                "Theater turn uses author fallback: reason=repair_call_failed repair=%s error=%s progress=%s node=%s catgirl=%s",
                repair_reason,
                type(exc).__name__,
                progress_kind,
                str(node.get("node_id") or ""),
                lanlan_name,
            )
            observability.record_result(
                responsibility="theater_actor",
                surface=progress_kind,
                result_kind="generation",
                outcome="repair_call_failed",
            )
            return _authored_performance_fallback(fallback, node, progress_kind)
        repaired = _parse_output(
            getattr(repaired_result, "content", ""),
            progress_kind=progress_kind,
        )
        if repaired is not None:
            repaired["choice_rewrites"] = []
        remaining_performance_reason = _performance_repair_reason(
            repaired,
            progress_kind=progress_kind,
            user_message=user_message,
            node=node,
            character_profile=character_profile,
            story=story,
            state=state,
            grounding_text=grounding_text,
            choice_options=list(choice_options or []),
            private_identifiers=private_identifiers,
            recent_turns=recent_turns,
            response_focus=(
                dict(response_focus) if isinstance(response_focus, dict) else {}
            ),
        )
        remaining_hard_reason = (
            remaining_performance_reason
            if remaining_performance_reason not in _SOFT_PERFORMANCE_REASONS
            else ""
        )
        if remaining_hard_reason:
            logger.warning(
                "Theater turn uses author fallback: reason=repair_rejected first=%s second=%s progress=%s node=%s catgirl=%s",
                repair_reason,
                remaining_hard_reason,
                progress_kind,
                str(node.get("node_id") or ""),
                lanlan_name,
            )
            observability.record_result(
                responsibility="theater_actor",
                surface=progress_kind,
                result_kind="generation",
                outcome="repair_rejected",
            )
            return _authored_performance_fallback(fallback, node, progress_kind)
        # Repair 已越过硬边界后直接采用正文；软语义不再触发第三层裁决。
        parsed = dict(repaired or {})
    if parsed and authored_performance and str(callback or "").strip():
        # 开场 Scene 或 Choice callback 都是作者已确认的公开演出；模型只能增强猫娘回应，不能改写或抢跑旁白。
        parsed["narration"] = str(callback).strip()
    if parsed is None:
        logger.warning(
            "Theater turn uses author fallback: reason=invalid_model_output progress=%s node=%s catgirl=%s",
            progress_kind,
            str(node.get("node_id") or ""),
            lanlan_name,
        )
        observability.record_result(
            responsibility="theater_actor",
            surface=progress_kind,
            result_kind="generation",
            outcome="invalid_model_output",
        )
        return fallback
    # 最终演出已通过结构与权威硬边界；文风、复述和人格表达由模型自由完成。
    observability.record_result(
        responsibility="theater_actor",
        surface=progress_kind,
        result_kind="generation",
        outcome="accepted",
    )
    return parsed


async def _invoke_model_once(
    api_config: dict[str, Any],
    system_prompt: str,
    user_prompt: str,
    *,
    call_type: str,
    surface: str,
    timeout_seconds: float,
    max_completion_tokens: int,
) -> Any:
    """按职责标签和显式预算执行一次结构化请求；是否再次调用由上层决定。"""  # noqa: DOCSTRING_CJK
    # 标签在创建客户端前写入当前 token 追踪上下文，使每种职责都能独立观测。
    set_call_type(call_type)
    # 单调时钟只用于脱敏耗时统计，不写入 Prompt、用户输入或模型输出。
    started_at = observability.start_timer()
    client = await create_chat_llm_async(
        api_config["model"],
        api_config["base_url"],
        api_config.get("api_key"),
        provider_type=api_config.get("provider_type"),
        timeout=timeout_seconds,
        max_retries=0,
        max_completion_tokens=max_completion_tokens,
    )
    try:
        async with client:
            response = await asyncio.wait_for(
                client.ainvoke(  # noqa: LLM_INPUT_BUDGET  # Router、Planner 与 Actor 的用户文本、背景和历史均在各调用方按 THEATER_* token 常量截断；本 helper 只统一发送已构造消息。
                    [
                        SystemMessage(content=system_prompt),
                        HumanMessage(content=user_prompt),
                    ]
                ),
                timeout=timeout_seconds,
            )
    except asyncio.TimeoutError:
        # 超时单独归类，便于区分供应商错误与预算不足；异常仍原样交给上层安全回退。
        model_trace.record_model_return(
            call_type=call_type,
            surface=surface,
            status="timeout",
            model=str(api_config.get("model") or ""),
            provider_type=str(api_config.get("provider_type") or ""),
            error_type="TimeoutError",
        )
        observability.record_model_call(
            call_type=call_type,
            surface=surface,
            started_at=started_at,
            status="timeout",
        )
        raise
    except Exception as exc:
        # 私有诊断记录只保存异常类型，不保存可能夹带请求正文或密钥的异常消息。
        model_trace.record_model_return(
            call_type=call_type,
            surface=surface,
            status="error",
            model=str(api_config.get("model") or ""),
            provider_type=str(api_config.get("provider_type") or ""),
            error_type=type(exc).__name__,
        )
        # 指标观测失败不能遮蔽原始模型异常；这里只发固定 error 状态。
        with suppress(Exception):
            observability.record_model_call(
                call_type=call_type,
                surface=surface,
                started_at=started_at,
                status="error",
            )
        raise
    # 所有职责都经过这个入口，因此 Router、Planner、Actor 和 Repair 的原始返回会按调用顺序采集。
    model_trace.record_model_return(
        call_type=call_type,
        surface=surface,
        status="success",
        model=str(api_config.get("model") or ""),
        provider_type=str(api_config.get("provider_type") or ""),
        content=getattr(response, "content", ""),
    )
    # 成功响应只提取供应商 usage 数值，正文不会进入观测样本。
    observability.record_model_call(
        call_type=call_type,
        surface=surface,
        started_at=started_at,
        status="success",
        response=response,
    )
    return response


def _model_config(config_manager: Any | None) -> dict[str, Any]:
    """读取 summary 档模型配置；不完整时返回空配置。"""  # noqa: DOCSTRING_CJK
    if config_manager is None:
        return {}
    try:
        config = dict(config_manager.get_model_api_config("summary") or {})
    except Exception:
        return {}
    if (
        not str(config.get("model") or "").strip()
        or not str(config.get("base_url") or "").strip()
    ):
        return {}
    config["model"] = str(config["model"]).strip()
    config["base_url"] = str(config["base_url"]).strip()
    return config
