# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""The /proactive_chat endpoint (phase 1 screening + phase 2 generation)
plus locale resolution and follow-up topic hooks.

Split out of the former monolithic ``main_routers/system_router.py``.
"""

from ._shared import _validate_local_mutation_request, logger, router
from .break_reminders import (
    _deliver_break_reminder_via_llm,
    _render_anti_slack_prompt,
    _render_work_break_game_invite_prompt,
    _render_work_break_prompt,
)
from .mini_game_invite import (
    _build_mini_game_invite_options_payload,
    _maybe_deliver_mini_game_invite,
    _mini_game_invite_advance_response,
    _mini_game_invite_count_post_response_chat,
    _mini_game_invite_get_state,
    _mini_game_invite_record_delivered,
    _pick_mini_game_type,
    _push_mini_game_invite_resolved,
)
from .proactive_content import (
    _append_music_recommendations,
    _format_music_content,
    _log_music_content,
    _log_news_content,
    _log_personal_dynamics,
    _log_trending_content,
    _log_video_content,
)
from .proactive_history import (
    _PROACTIVE_SIMILARITY_THRESHOLD,
    _clear_channel_from_proactive_history,
    _format_recent_proactive_chats,
    _increment_proactive_chat_total,
    _is_recent_proactive_material,
    _is_similar_to_recent_proactive_chat,
    _proactive_material_key,
    _record_invite_delivery_persistent,
    _record_proactive_chat,
    _record_proactive_material,
    _record_reminiscence_usage,
)
from .proactive_parsing import (
    PROACTIVE_REASON_CHAT_DELIVERED,
    PROACTIVE_REASON_DELIVERY_FAILED,
    PROACTIVE_REASON_DELIVERY_PREEMPTED,
    PROACTIVE_REASON_ERROR_CHARACTER_NOT_FOUND,
    PROACTIVE_REASON_ERROR_INTERNAL,
    PROACTIVE_REASON_ERROR_SOURCE_FETCH_FAILED,
    PROACTIVE_REASON_ERROR_TIMEOUT,
    PROACTIVE_REASON_PASS_ACTIVITY_BUSY,
    PROACTIVE_REASON_PASS_BUSY,
    PROACTIVE_REASON_PASS_DISABLED,
    PROACTIVE_REASON_PASS_DUPLICATE,
    PROACTIVE_REASON_PASS_GENERATION_EMPTY,
    PROACTIVE_REASON_PASS_MODEL_PASS,
    PROACTIVE_REASON_PASS_PRIVACY,
    PROACTIVE_REASON_PASS_RESTRICTED_SCREEN_ONLY,
    PROACTIVE_REASON_PASS_ROUTE_ACTIVE,
    PROACTIVE_REASON_PASS_SOURCE_EMPTY,
    PROACTIVE_REASON_PASS_THROTTLED,
    _ensure_proactive_reason_code,
    _extract_links_from_raw,
    _lookup_link_by_title,
    _parse_unified_phase1_result,
    _proactive_chat_body,
    _proactive_error_body,
    _proactive_pass_body,
    _strip_proactive_intent_label_leak,
    _strip_proactive_screen_tag_leak,
    _text_is_pass_sentinel,
)
from .proactive_sources import (
    _compute_source_weights,
    _ensure_source_history_loaded,
    _filter_sources_by_weight,
    _record_source_used,
    _should_skip_source,
    _source_hash,
)
import asyncio
import json
import random
import re
import time
from typing import Any
from uuid import uuid4
from fastapi import Request
from fastapi.responses import JSONResponse
from utils.llm_client import (
    SystemMessage,
    HumanMessage,
    ThinkingStreamStripper,
    chat_retry_error_types,
    create_chat_llm_async,
)
# Phase 2 proactive output ceiling. The model occasionally runs off; this
# fence cuts the stream and aborts TTS once the running output exceeds the
# token budget. We use sync `count_tokens` here on purpose:
#   - At fence time `full_text` is < 1 KB (we abort at 300 tokens ≈ 400 CJK
#     chars); tiktoken Rust encode of that size is sub-millisecond.
#   - tiktoken's Rust core releases the GIL inside `encode`, so a sync call
#     does NOT block other coroutines' IO callbacks for any meaningful time.
#   - `asyncio.to_thread` adds ~0.1 ms scheduling overhead per call (warmed
#     thread pool) — 3-4× the actual encode work. Across a 30-chunk stream
#     that's a few milliseconds saved per turn, but more importantly avoids
#     the cold-start case where the first thread hop can take much longer.
from utils.tokenize import count_tokens
from ..shared_state import get_config_manager, get_session_manager
from main_logic.omni_realtime_client import OmniRealtimeClient
from config import (
    MEMORY_SERVER_PORT,
    focus_extra_body,
    leaks_thinking_in_content,
    PROACTIVE_PHASE1_FETCH_PER_SOURCE,
    PROACTIVE_PHASE1_TOTAL_TOPICS,
    PROACTIVE_EXTERNAL_PER_ITEM_MAX_TOKENS,
    PROACTIVE_EXTERNAL_TOTAL_MAX_TOKENS,
    PROACTIVE_PHASE2_OUTPUT_MAX_TOKENS as PHASE2_OUTPUT_MAX_TOKENS,
    PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
    PROACTIVE_PHASE1_UNIFIED_MAX_TOKENS,
    ANTI_REPEAT_DROP_THRESHOLD,
    ANTI_REPEAT_INJECT_TOP_K,
    ANTI_REPEAT_REGEN_THRESHOLD,
    ANTI_REPEAT_EXEMPT_SOURCE_TAGS,
    MINI_GAME_INVITE_ENABLED,
    MINI_GAME_INVITE_FORCE_GAME_TYPE,
)
from config.prompts.prompts_sys import _loc
from config.prompts.prompts_directives import render_regen_avoid_instruction, render_format_fix_instruction
from config.prompts.prompts_proactive import (
    get_proactive_generate_prompt, get_proactive_music_playing_hint,
    get_proactive_music_unknown_track_name,
    get_proactive_music_failsafe_hint,
    get_proactive_music_strict_constraint,
    get_proactive_format_sections,
    get_screen_section_header,
    get_screen_section_footer, get_screen_img_hint, BEGIN_GENERATE,
    SCREEN_WINDOW_TITLE, EXTERNAL_TOPIC_HEADER,
    EXTERNAL_TOPIC_FOOTER, MUSIC_SECTION_HEADER,
    MUSIC_SECTION_FOOTER,
    MEME_SECTION_HEADER,
    MEME_SECTION_FOOTER, get_meme_topic_line,
    PROACTIVE_SOURCE_LABELS, PROACTIVE_MUSIC_TAG_INSTRUCTIONS,
    build_proactive_action_note,
)
from utils.screenshot_utils import (
    decode_and_compress_screenshot_b64,
    COMPRESS_TARGET_HEIGHT,
    COMPRESS_JPEG_QUALITY,
)
from utils.language_utils import normalize_language_code, get_global_language, get_global_language_full, is_supported_language_code
from utils.web_scraper import (
    fetch_trending_content, format_trending_content,
    fetch_window_context_content, format_window_context_content,
    fetch_video_content, format_video_content,
    fetch_news_content, format_news_content,
    fetch_personal_dynamics, format_personal_dynamics,
)
from utils.music_crawlers import fetch_music_content
from utils.meme_fetcher import fetch_meme_content
from utils.meme_moderation import moderate_meme_image_url


# 惰性缓存（None = 尚未构建）：openai/anthropic SDK 已从启动 import 链移除（见
# utils/llm_client），模块级立即展开会把两个 SDK 拉回 main_server 端口就绪路径。
# 首次求值发生在 LLM 调用的异常处理处，彼时 SDK 必已随 client 构造加载。
_PROACTIVE_LLM_RETRY_ERROR_TYPES: tuple[type[BaseException], ...] | None = None


def _proactive_llm_retry_error_types() -> tuple[type[BaseException], ...]:
    global _PROACTIVE_LLM_RETRY_ERROR_TYPES
    if _PROACTIVE_LLM_RETRY_ERROR_TYPES is None:
        _PROACTIVE_LLM_RETRY_ERROR_TYPES = (
            asyncio.TimeoutError,
            *chat_retry_error_types(),
        )
    return _PROACTIVE_LLM_RETRY_ERROR_TYPES


async def _safe_fire_proactive_done(scope: dict) -> None:
    """Safely reset the state machine from proactive_chat's exception-handling path.

    The exception may occur before PROACTIVE_START (mgr unbound, _SE not imported)
    or after it; look everything up via the locals() dict to avoid NameError. The
    state-machine fire itself is idempotent: when the state is already IDLE,
    PROACTIVE_DONE is just a no-op.
    """
    mgr = scope.get("mgr")
    se = scope.get("_SE")
    emitted = scope.get("_proactive_done_emitted", False)
    if mgr is None or se is None or emitted:
        return
    try:
        await mgr.state.fire(se.PROACTIVE_DONE)
    except Exception as err:  # 状态机不该抛，但兜底 swallow
        logger.warning("safe_fire_proactive_done 异常: %s", err)


_PHASE1_FETCH_PER_SOURCE = PROACTIVE_PHASE1_FETCH_PER_SOURCE  # Phase 1 每个信息源固定抓取条数


_PHASE1_TOTAL_TOPIC_TARGET = PROACTIVE_PHASE1_TOTAL_TOPICS  # Phase 1 输入给筛选模型的总候选目标条数


def _open_threads_for_activity_state(activity_snapshot, fresh_open_threads) -> list[str]:
    """Return semantic open_threads that should render in activity state.

    ``unfinished_thread`` is a stronger, rule-based continuation signal (the
    previous AI question is still hanging and may bypass normal propensity).
    When it exists, suppress softer LLM-enriched open_threads so Phase 2 sees a
    single follow-up surface. Also suppress open_threads during
    ``restricted_screen_only`` states: those rounds allow screen-derived chatter
    only, with unfinished_thread as the explicit text-only continuation
    exception. Otherwise keep open_threads in activity state, where they sit
    next to live state/tone rather than old reminiscence.
    """
    if activity_snapshot is None:
        return list(fresh_open_threads or [])
    if getattr(activity_snapshot, 'unfinished_thread', None) is not None:
        return []
    if getattr(activity_snapshot, 'propensity', None) == 'restricted_screen_only':
        return []
    return list(fresh_open_threads or [])


def _render_followup_topic_hooks(
    proactive_lang: str,
    followup_topics: list[dict[str, Any]],
) -> tuple[str, list[Any]]:
    """Render follow-up topic hooks and return the surfaced reflection ids.

    Only reflections whose text actually survives build_topic_hook_prompt's
    blank/duplicate filter are reported as surfaced. Otherwise a blank or
    duplicate followup inside the first three would still be recorded via
    /record_surfaced and pushed into cooldown even though the model never saw
    it. Semantic open_threads intentionally do not flow through this helper:
    they render inside the activity-state section, where the live state/tone
    and decision rules can arbitrate them separately from old reminiscence.
    """
    if not followup_topics:
        return "", []

    from main_logic.topic.common import clean_text
    from main_logic.topic.hooks import build_topic_hook_prompt

    rendered_followup_topics = followup_topics[:3]
    prompt = build_topic_hook_prompt(
        proactive_lang,
        followup_topics=rendered_followup_topics,
    )
    if not prompt:
        return "", []

    # Mirror _iter_followup_texts: drop blanks/duplicates so the surfaced ids
    # match exactly what the prompt rendered.
    surfaced_reflection_ids: list[Any] = []
    seen_texts: set[str] = set()
    for topic in rendered_followup_topics:
        text = clean_text(topic.get('text'))
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)
        if topic.get('id'):
            surfaced_reflection_ids.append(topic['id'])
    return prompt, surfaced_reflection_ids


def _resolve_proactive_locale(data: dict, mgr) -> str:
    """Resolve the active user locale for proactive chat flows.

    Request data wins first, websocket session language is the second source of
    truth, and the process-level global language is only a final fallback. This
    keeps proactive invite copy and Phase 1-2 LLM output aligned with the live
    session whenever frontend i18n has already reported the user's language.
    """
    request_lang = data.get('language') or data.get('lang') or data.get('i18n_language')
    # 与 ``main_routers/game_router._absorb_request_language`` 同形：第三方客户端 /
    # corrupted localStorage 可能传 ``'undefined'`` / ``'estonian'`` 等 garbage，
    # ``normalize_language_code`` 对未识别值默认回退 ``'en'``——必须先用公共白名单
    # 挡掉，否则 proactive 邀请文案会被静默短路成英文，错过本应命中的 session 真值。
    if request_lang and is_supported_language_code(request_lang):
        normalized = normalize_language_code(request_lang, format='short')
        if normalized:
            return normalized
    session_lang = getattr(mgr, 'user_language', None)
    if session_lang:
        normalized = normalize_language_code(session_lang, format='short')
        if normalized:
            return normalized
    return get_global_language() or 'en'


def _resolve_topic_hook_locale(data: dict, mgr, *, fallback: str) -> str:
    """Resolve the locale for topic-hook prompts without collapsing zh-TW."""
    for raw_lang in (
        data.get('language'),
        data.get('lang'),
        data.get('i18n_language'),
        getattr(mgr, 'user_language', None),
    ):
        if raw_lang and is_supported_language_code(raw_lang):
            normalized = normalize_language_code(raw_lang, format='full')
            if normalized:
                return normalized
    global_lang = normalize_language_code(get_global_language_full(), format='full')
    if global_lang:
        return global_lang
    return fallback


# ================================================================
# 主动搭话响应构建 (Response builder pure function)
# ================================================================
def build_proactive_response(source_tag: str, ctx: dict) -> tuple[str, list]:
    primary_channel = 'unknown'
    source_links = []
    lan_name = ctx.get('lanlan_name', 'System')
    
    match source_tag:
        case 'CHAT':
            primary_channel = 'chat'
        case 'WEB':
            # 使用细粒度 web 子通道（news/video/home/personal），fallback 到 'web'
            web_link = ctx.get('selected_web_link')
            primary_channel = web_link.get('mode', 'web') if web_link else 'web'
            if web_link:
                source_links.append(web_link)
                logger.debug(f"[{lan_name}] Phase 2 确定选择 WEB (子通道: {primary_channel})，已添加链接")
        case 'MUSIC':
            primary_channel = 'music'
            if ctx.get('selected_music_link'):
                source_links.append(ctx['selected_music_link'])
                logger.debug(f"[{lan_name}] Phase 2 确定选择 MUSIC，已添加链接")
        case 'MEME':
            primary_channel = 'meme'
            if ctx.get('selected_meme_link'):
                source_links.append(ctx['selected_meme_link'])
                logger.debug(f"[{lan_name}] Phase 2 确定选择 MEME，已添加相关链接")
            else:
                logger.warning(f"[{lan_name}] Phase 2 AI 选择 MEME 但无可用表情包链接，回退处理")
                if ctx.get('selected_web_link'):
                    primary_channel = ctx['selected_web_link'].get('mode', 'web')
                    source_links.append(ctx['selected_web_link'])
                    logger.debug(f"[{lan_name}] Phase 2 回退到 WEB 通道 (子通道: {primary_channel})")
                elif ctx.get('vision_content'):
                    primary_channel = 'vision'
                    logger.debug(f"[{lan_name}] Phase 2 回退到 VISION 通道")
                else:
                    logger.debug(f"[{lan_name}] Phase 2 MEME 无表情包且无回退通道，将跳过链接展示")
    return primary_channel, source_links


@router.post('/proactive_chat')
async def proactive_chat(request: Request):
    """
    Proactive chat: two-phase architecture — Phase 1 merged LLM (web screening + music/meme keywords, 1 call), Phase 2 persona-aware chat generation.
    """
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    try:
        _config_manager = get_config_manager()
        session_manager = get_session_manager()
        # 获取当前角色数据（包括完整人设）
        master_name_current, her_name_current, _, _, _, lanlan_prompt_map, _, _, _ = await _config_manager.aget_character_data()
        
        data = await request.json()
        lanlan_name = data.get('lanlan_name') or her_name_current
        is_playing_music = data.get('is_playing_music', False)
        current_track = data.get('current_track', None)
        music_cooldown = data.get('music_cooldown', False)
        
        # 获取session manager
        mgr = session_manager.get(lanlan_name)
        if not mgr:
            return JSONResponse(
                _proactive_error_body(
                    PROACTIVE_REASON_ERROR_CHARACTER_NOT_FOUND,
                    error=f"角色 {lanlan_name} 不存在",
                ),
                status_code=404,
            )

        if getattr(mgr, "is_goodbye_silent", lambda: False)():
            logger.info("[%s] 主动搭话本轮未发起：goodbye silent", lanlan_name)
            return JSONResponse(_proactive_pass_body(
                PROACTIVE_REASON_PASS_DISABLED,
                message="goodbye silent; proactive skipped",
            ))

        try:
            from main_routers.game_router import is_game_route_active
            if is_game_route_active(lanlan_name):
                logger.info("[%s] 主动搭话本轮未发起：游戏路由 active", lanlan_name)
                return JSONResponse(_proactive_pass_body(
                    PROACTIVE_REASON_PASS_ROUTE_ACTIVE,
                    message="game route active; ordinary proactive skipped",
                ))
        except Exception as game_route_err:
            logger.warning("[%s] proactive game-route guard failed closed: %s", lanlan_name, game_route_err)
            return JSONResponse(_proactive_pass_body(
                PROACTIVE_REASON_PASS_ROUTE_ACTIVE,
                message="game route guard unavailable; ordinary proactive skipped",
            ))
        
        # 检查能否发起新一轮主动搭话：状态机统一把 "AI 正在响应"（_is_responding）、
        # "另一轮 proactive 在跑"（phase != IDLE）两个信号收拢到 O(1) 判定。
        # mgr.is_active 仅用于判断 session 是否已实例化，故仍需保留。
        probe_session = mgr.session if mgr.is_active else None

        # ========== Voice mode fast path ==========
        # 语音模式下不走 Phase1/Phase2，不占 SM 的 proactive phase；先用只读
        # can_start_proactive 做 409 判定即可。
        if data.get('voice_mode') and mgr.is_active and isinstance(mgr.session, OmniRealtimeClient):
            # Mini-game invite 状态机推进：voice fast path 不走 activity tracker，
            # 直接用 session 自己跟踪的「用户最后一次真实消息时间」喂给
            # advance_response。否则纯 voice 用户收到 mini-game 邀请回应后，
            # pending 永远翻不掉，邀请会被永久抑制；CodeRabbit Major review 指出。
            #
            # ⚠️ 用 last_user_message_time（仅真实非空非 echo 用户输入）而非
            # last_user_activity_time（顶部无条件刷新，含 VAD 空噪声 + 麦克风录回
            # AI 自己 TTS 的回声）。后者会被 AI 念邀请台词的回声污染：邀请投递后
            # 回声立刻把 activity 刷到 > delivered_at，下一个 tick 的隐式 dismiss
            # 误判「用户已回应」→ 把 pending 邀请清成 'later'（5min）+ 撤掉按钮，
            # 用户随后点「现在不想玩」落到 expired、真正的 5h decline 起不来、邀请
            # 5min 后反复重来。改用真消息时间戳后，纯点按钮（不说话）的用户活动
            # 时间不会越过 delivered_at，pending 一直留到用户显式点按钮 / 说话。
            _voice_advance_outcome = _mini_game_invite_advance_response(
                lanlan_name, getattr(mgr, 'last_user_message_time', None),
            )
            # advance 触发了隐式 dismiss → 推 WS 让前端清掉 prompt UI（cross-window
            # 一致性）。codex P2 指出非按钮路径漏推 WS 让 UI 挂着。
            if _voice_advance_outcome and _voice_advance_outcome.get('session_id'):
                await _push_mini_game_invite_resolved(
                    mgr,
                    session_id=_voice_advance_outcome['session_id'],
                    action=_voice_advance_outcome.get('action', 'suppress'),
                )
            if not mgr.state.can_start_proactive(session=probe_session):
                logger.info("[%s] 主动搭话本轮未发起：语音模式 AI 正在响应中（409）", lanlan_name)
                return JSONResponse(
                    _proactive_error_body(
                        PROACTIVE_REASON_PASS_BUSY,
                        error="AI正在响应中，无法主动搭话",
                        message="请等待当前响应完成",
                        state=mgr.state.snapshot(),
                    ),
                    status_code=409,
                )
            delivered = await mgr.trigger_voice_proactive_nudge()
            if delivered:
                # 1h+10 chats 冷却的 chat counter：voice nudge 也算一次主动搭话，
                # 与 text path 在 _record_proactive_chat 之后调 count 对称。
                _mini_game_invite_count_post_response_chat(lanlan_name)
                # 持久化"累计成功投递的主动搭话总数"，给 force-first 用。
                await _increment_proactive_chat_total(lanlan_name)
            else:
                logger.info("[%s] 主动搭话本轮未发起：语音 nudge 被 guard 跳过", lanlan_name)
            # No Focus cooldown here: a voice nudge is realtime and never runs a
            # Focus thinking-on reply, so it is not a Focus proactive turn — the
            # cooldown is applied only at the text Phase-2 idle path (which is
            # where _focus_idle_thinking actually gates thinking-on).
            if delivered:
                return JSONResponse(_proactive_chat_body(
                    PROACTIVE_REASON_CHAT_DELIVERED,
                    message="voice proactive triggered",
                ))
            return JSONResponse(_proactive_pass_body(
                PROACTIVE_REASON_PASS_BUSY,
                message="voice proactive skipped (guard)",
            ))

        # ========== Text-mode proactive：原子 "检查 + 占坑" ==========
        # try_start_proactive 在 _write_lock 内完成 can_start_proactive 判定 + 翻
        # IDLE→PHASE1 + 订阅派发，避免并发请求双双通过 can_start_proactive 后
        # 各自 fire(PROACTIVE_START) 导致两路 proactive 同时进入 PHASE1。
        from main_logic.session_state import SessionEvent as _SE
        if not await mgr.state.try_start_proactive(session=probe_session):
            logger.info("[%s] 主动搭话本轮未发起：AI 正在响应或已有一轮在跑（409）", lanlan_name)
            return JSONResponse(
                _proactive_error_body(
                    PROACTIVE_REASON_PASS_BUSY,
                    error="AI正在响应中，无法主动搭话",
                    message="请等待当前响应完成",
                    state=mgr.state.snapshot(),
                ),
                status_code=409,
            )
        _proactive_done_emitted = False
        # Set after activity snapshot fetch — tells the frontend scheduler
        # to skip the regular tier backoff and use a flat baseInterval on
        # the next round (the backend will then inject a uniform
        # [0, 0.5*baseInterval] sleep to provide the jitter). See the
        # screen-only delay block further down and the matching
        # ``S.proactiveFixedScheduleMode`` branch in static/app/app-proactive.js.
        _next_schedule_fixed_mode = False

        # Focus idle cooldown bookkeeping (read by _end_proactive via closure).
        # Set only when the flow reaches the Phase-2 idle Focus decision, so
        # short-circuit replies (mini-game invite, break-reminder, must-fire)
        # that return before Phase 2 never spend Focus charge. The episode token
        # pins the decay to the episode the thinking decision observed.
        _focus_phase2_reached = False
        _focus_episode_token = None
        _focus_turn_token = None

        async def _end_proactive(resp: JSONResponse) -> JSONResponse:
            """Wraps every normal/short-circuit proactive exit: idempotently fires PROACTIVE_DONE.

            Also injects ``next_schedule_fixed_mode`` into the response body; the
            frontend reads it to decide whether the next round of scheduling uses
            tier backoff or the fixed base interval. The injection happens at this
            unified exit, so newly added response paths need no individual changes.
            """
            nonlocal _proactive_done_emitted
            if not _proactive_done_emitted:
                _proactive_done_emitted = True
                try:
                    await mgr.state.fire(_SE.PROACTIVE_DONE)
                except Exception as _done_err:
                    logger.warning("[%s] PROACTIVE_DONE fire 异常: %s", lanlan_name, _done_err)
            try:
                body = json.loads(resp.body)
            except Exception:
                return resp
            if not isinstance(body, dict):
                return resp
            body = _ensure_proactive_reason_code(body)
            # text-mode 占坑后的所有出口都经过这里。本轮最终没把话说出来
            # （action != "chat"：各种 guard/skip/内容为空/被用户接管）就在
            # info 留一条带原因的日志，原因取响应体 message（无则 error）。
            # 散落各分支无需各自记；排查"她这轮为什么没主动说话"看这条即可。
            # 占坑前的早退（游戏路由 / voice 与 text 的 409 并发拒绝）不经过
            # 本出口，各自就地补了同前缀（"主动搭话本轮未发起："）的 info。
            _replied = body.get("action") == "chat"
            if not _replied:
                logger.info(
                    "[%s] 主动搭话本轮未发起：%s",
                    lanlan_name,
                    body.get("message") or body.get("error") or "(无原因说明)",
                )
            # Idle Focus cooldown — only for turns that reached the Phase-2 idle
            # Focus decision (short-circuit replies never set the flag, so they
            # don't spend Focus). A proactive turn never raises the charge; it
            # decays — faster when it delivered a reply (_replied) than when
            # Phase 2 produced nothing. count_turn=False + episode-token guard
            # live inside _focus_idle_cooldown.
            if _focus_phase2_reached:
                try:
                    await mgr._focus_idle_cooldown(
                        replied=_replied, episode_token=_focus_episode_token,
                        turn_token=_focus_turn_token,
                    )
                except Exception as _focus_err:
                    logger.debug("[%s] focus idle cooldown failed: %s", lanlan_name, _focus_err)
            body.setdefault('next_schedule_fixed_mode', _next_schedule_fixed_mode)
            return JSONResponse(body, status_code=resp.status_code)

        def _proactive_preempted_json(where: str) -> dict:
            # 细粒度的 state 快照留 debug；面向排查的"本轮未发起 + 原因"由统一
            # 出口 _end_proactive 按 message 打 info（这些 dict 全部经它返回），
            # 避免同一轮 skip 打出两条重复 info。
            logger.debug(
                "[%s] proactive %s preempted by user takeover (state=%s)",
                lanlan_name, where, mgr.state.snapshot(),
            )
            return {
                "success": True,
                "action": "pass",
                "reason_code": PROACTIVE_REASON_DELIVERY_PREEMPTED,
                "message": f"proactive {where} preempted by user takeover",
            }

        print(f"[{lanlan_name}] 开始主动搭话流程（两阶段架构）...")

        # ========== 拉用户活动快照 ==========
        # 在 enabled_modes 解析之前拉一次，因为 propensity 可能需要把
        # enabled_modes 收紧到只剩 vision（restricted_screen_only 状态）。
        # 详见 docs/design/user-activity-tracker.md。
        #
        # 隐私模式：用户开了"隐私模式"开关 → 临时禁用整个 user-activity-tracker，
        # 回退到 PR #1015 之前的无限制策略。snapshot 留 None，下游所有 gating
        # 都已在 PR #1015 设计时按 "snapshot is not None" 写过 fallback：
        #   - propensity 收紧（restricted_screen_only）→ 不触发
        #   - 反思/回忆 _allow_reminiscence → 默认放开
        #   - state_section 渲染 → 输出空串
        #   - mark_unfinished_thread_used → 不计数
        # 所以这里把 snapshot 直接设 None 就够，等价于"tracker 不存在"。
        from utils.preferences import ais_privacy_mode_enabled
        try:
            privacy_mode = await ais_privacy_mode_enabled()
        except Exception as _pm_err:
            # fail-closed：读不出来按隐私开启处理。正常"用户没开隐私"是
            # ais_privacy_mode_enabled 返回 False，不进这个 except。
            logger.warning(
                f"[{lanlan_name}] privacy mode check failed, defaulting to enabled: {_pm_err}",
            )
            privacy_mode = True
        if privacy_mode:
            print(f"[{lanlan_name}] 隐私模式开启，跳过 activity tracker，按无限制策略搭话")
            activity_snapshot = None
        else:
            try:
                activity_snapshot = await mgr._activity_tracker.get_snapshot()
                print(f"[{lanlan_name}] activity snapshot: state={activity_snapshot.state} "
                      f"propensity={activity_snapshot.propensity} reasons={activity_snapshot.propensity_reasons} "
                      f"skip_prob={activity_snapshot.skip_probability:.2f} tone={activity_snapshot.tone}")
            except Exception as _act_err:
                logger.warning(f"[{lanlan_name}] activity snapshot fetch failed: {_act_err}; falling back to open propensity")
                activity_snapshot = None

        # 进 proactive_chat 后第一时间推进 mini-game invite 的"已回应"判定：
        # 即便本轮不发邀请，pending 的上一次邀请也得在用户已说话时翻成已回应，
        # 否则 cooldown 永远卡在 pending。Text path 从 activity_snapshot 反推
        # last_user_msg_at；voice fast path 在上面的 voice block 内独立调一次
        # （用 mgr.last_user_activity_time），两边对称。
        _text_last_user_msg_at: float | None = None
        if activity_snapshot is not None:
            _secs = getattr(activity_snapshot, 'seconds_since_user_msg', None)
            if _secs is not None:
                _text_last_user_msg_at = time.time() - float(_secs)
        _text_advance_outcome = _mini_game_invite_advance_response(
            lanlan_name, _text_last_user_msg_at,
        )
        # 隐式 dismiss 推 WS（同 voice fast path 对称，codex P2）
        if _text_advance_outcome and _text_advance_outcome.get('session_id'):
            await _push_mini_game_invite_resolved(
                mgr,
                session_id=_text_advance_outcome['session_id'],
                action=_text_advance_outcome.get('action', 'suppress'),
            )

        # 用户级 toggle：前端 CHAT_MODE_CONFIG 里的 ``proactiveMiniGameInviteEnabled``
        # 通过 request body 的 ``mini_game_invite_enabled`` 字段透传。缺省 True 兼容
        # 旧客户端。提到 _debug_force_invite 计算之前——把 user toggle 关同时
        # 服务端开了调试旗标的场景下，下游早退 gate（closed / skip_probability）
        # 也维持原有抑制语义；不能因为旗标开了就把 gate 一并 bypass 掉。
        # CodeRabbit Major review 指出原版只在 _maybe_deliver_mini_game_invite
        # 入口拦 user toggle，旗标已经把上游 gate 绕过 → 进 _maybe_deliver
        # 又被 toggle 拦 None → caller 走普通 source picking，封禁场景仍然漏过。
        _user_invite_toggle = bool(data.get('mini_game_invite_enabled', True))

        # 调试旗标 ``MINI_GAME_INVITE_FORCE_GAME_TYPE`` 非 None 时绕开本函数所有
        # 上游早退 gate（closed / skip_probability / restricted_screen_only），
        # 让 ``_maybe_deliver_mini_game_invite`` 能稳定接到本轮调用——契约是
        # "开启后主动搭话必定触发特定小游戏"。仅本地手测使用；生产
        # ``MINI_GAME_INVITE_ENABLED`` 总开关 + 旗标默认 None 双保险。
        # 用户 toggle 关时旗标无效（与 _maybe_deliver_mini_game_invite 入口
        # 的 toggle 检查同语义，单一事实源在前端 toggle）。
        # CodeRabbit Major 指出：这条不在 ``_maybe_deliver_mini_game_invite``
        # 内部加是因为那时已经过了上游 gate，旗标做不到"必定"。
        _debug_force_invite = (
            MINI_GAME_INVITE_FORCE_GAME_TYPE is not None
            and _user_invite_toggle
        )

        # ========== Hard short-circuit: propensity=closed ==========
        # ``private`` state pins propensity to ``closed`` (see
        # main_logic/activity/snapshot.py). Skip everything — no LLM,
        # no source fetch, no prompt assembly. The user is in a
        # password manager / banking app / etc and we promised not to
        # look. Bypassed for the unfinished_thread override is
        # deliberate: if the AI just asked a question, hanging on it
        # mid-private is rude. closed > thread.
        if (
            not _debug_force_invite
            and activity_snapshot is not None
            and activity_snapshot.propensity == 'closed'
        ):
            print(f"[{lanlan_name}] propensity=closed (state={activity_snapshot.state}), 跳过本轮 proactive")
            return await _end_proactive(JSONResponse({
                "success": True,
                "action": "pass",
                "reason_code": PROACTIVE_REASON_PASS_PRIVACY,
                "message": f"user state={activity_snapshot.state} → closed (privacy lockdown)",
            }))

        # ========== Screen-only：固定间隔 + 后端抖动 ==========
        # 用户处于 gaming / focused_work（propensity=restricted_screen_only）
        # 时，常规的前端 3-tier 退避会让搭话间隔指数级增长，跟陪伴产品
        # 命题冲突（用户最长会话段反而最安静）。改用：
        #   1. 前端 reset backoffLevel=0 并按 baseInterval 等间隔触发
        #      （由响应里的 next_schedule_fixed_mode=True 通知前端切换）
        #   2. 后端在 LLM 调用前 sleep uniform(0, 0.5 * baseInterval)，把每轮
        #      实际间隔从 base 抹成 [base, 1.5*base] 的均匀分布
        # 总效果：屏幕态平均间隔 ≈ 1.25*base，且有自然的随机抖动。
        # skip_probability（仅 immersive_horror=0.3）作为正交机制保留。
        #
        # ⚠️ 标志位 vs sleep 拆开：anti_slack_pending / work_break_pending
        # 是 focused_work 下的 must-fire 提醒（紧跟在下一段 4425+），本身
        # 时间敏感，不能被这里的随机抖动延后。但前端 fixed_mode 标志位
        # 仍然要设——否则 must-fire 走 _end_proactive 时响应里会带回
        # next_schedule_fixed_mode=False，前端误切回 tier backoff，让用户
        # 离开 must-fire 状态后又被退避机制吞掉一段时间。
        # Codex P2 + CodeRabbit Major review。
        if (
            activity_snapshot is not None
            and activity_snapshot.propensity == 'restricted_screen_only'
        ):
            _next_schedule_fixed_mode = True
            _has_must_fire = (
                activity_snapshot.anti_slack_pending is not None
                or activity_snapshot.work_break_pending is not None
            )
            if _has_must_fire:
                print(f"[{lanlan_name}] propensity=restricted_screen_only 但有 must-fire 提醒待发，跳过本轮抖动 sleep")
            else:
                try:
                    _base_interval_raw = data.get('base_interval_seconds')
                    _base_interval = float(_base_interval_raw) if _base_interval_raw is not None else 0.0
                except (TypeError, ValueError):
                    _base_interval = 0.0
                # 上限兜底：base 过大时把 0.5*base 截到 60s，避免极端配置
                # （比如 user 把 proactiveChatInterval 调到 300s）让后端
                # 单请求占连接十分钟。
                if _base_interval > 0:
                    _jitter_max = min(_base_interval * 0.5, 60.0)
                    _jitter = random.uniform(0.0, _jitter_max)
                    print(f"[{lanlan_name}] propensity=restricted_screen_only, 后端注入 {_jitter:.2f}s 间隔抖动（base={_base_interval:.1f}s）")
                    await asyncio.sleep(_jitter)

        # ========== Must-fire: break-reminder branches ==========
        # Anti-slack outranks water-break (transition trigger more
        # time-sensitive than the cumulative one). Both bypass Phase 1
        # entirely and run via _deliver_break_reminder_via_llm — see
        # the helper docstring above. ``private`` state already cleared
        # both pendings inside the tracker, so reaching here implies
        # not-private. Debug-force-invite still takes precedence so the
        # mini-game force flag keeps its "guaranteed mini-game" contract.
        if (
            not _debug_force_invite
            and activity_snapshot is not None
            and (
                activity_snapshot.anti_slack_pending is not None
                or activity_snapshot.work_break_pending is not None
            )
        ):
            try:
                _break_lang = _resolve_proactive_locale(data, mgr)
            except Exception:
                _break_lang = 'zh'

            # Resolve character_prompt up front and prepend it to every
            # break-reminder SystemMessage. Without this the model would
            # see only the env-notice template and lose its persona —
            # CodeRabbit Major review (PR #1226). Mirrors the
            # placeholder substitution the normal Phase 2 path does at
            # line ~5300 (LANLAN_NAME / MASTER_NAME).
            _break_character_prompt = lanlan_prompt_map.get(lanlan_name, '')
            if _break_character_prompt:
                _break_character_prompt = (
                    _break_character_prompt
                    .replace('{LANLAN_NAME}', lanlan_name)
                    .replace('{MASTER_NAME}', master_name_current)
                )

            def _compose_break_system_prompt(env_notice: str) -> str:
                if not _break_character_prompt:
                    return env_notice
                return f'{_break_character_prompt}\n\n{env_notice}'

            # Anti-slack first — single-behavior 'back to work' nudge.
            if activity_snapshot.anti_slack_pending is not None:
                anti_pending = activity_snapshot.anti_slack_pending
                anti_prompt = _render_anti_slack_prompt(
                    pending=anti_pending,
                    master_name=master_name_current,
                    lang=_break_lang,
                )
                delivered_text, _proactive_sid_unused = await _deliver_break_reminder_via_llm(
                    lanlan_name=lanlan_name,
                    mgr=mgr,
                    system_prompt=_compose_break_system_prompt(anti_prompt),
                    channel='anti_slack',
                    lang=_break_lang,
                )
                if delivered_text:
                    try:
                        mgr._activity_tracker.mark_anti_slack_used()
                    except Exception as _mark_err:
                        logger.warning(
                            "[%s] mark_anti_slack_used failed: %s",
                            lanlan_name, _mark_err,
                        )
                    # Mini-game cooldown counter — same contract as the
                    # normal text proactive path at ~6253: any successful
                    # proactive emission counts as one of the "10 chats
                    # since user responded" gate. No-op when no prior
                    # invite is pending. Codex/CodeRabbit Minor: PR #1226.
                    _mini_game_invite_count_post_response_chat(lanlan_name)
                    await _increment_proactive_chat_total(lanlan_name)
                    return await _end_proactive(JSONResponse({
                        "success": True,
                        "action": "chat",
                        "reason_code": PROACTIVE_REASON_CHAT_DELIVERED,
                        "message": "anti-slack reminder delivered",
                        "channel": "anti_slack",
                    }))
                # Delivery rejected (user took over / config issue).
                # Don't fall through to normal proactive — must-fire
                # semantics: leave pending armed for the next round.
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "pass",
                    "reason_code": PROACTIVE_REASON_DELIVERY_PREEMPTED,
                    "message": "anti-slack reminder pending but delivery skipped",
                }))

            # Water-break — 50% pivots to a rest+game-invite combo
            # (gated on mini-game cooldown / user toggle / global
            # kill switch / existence of a valid game_type). Any of
            # those gates failing falls through to the regular
            # drink/stretch nudge instead of breaking the must-fire.
            water_pending = activity_snapshot.work_break_pending
            prefs_for_break = mgr._activity_tracker._sm._prefs
            _gi_prob = prefs_for_break.work_break_game_invite_probability
            if _gi_prob is None:
                # Resolved at import time — see tracker.py defaults.
                from main_logic.activity.tracker import _WORK_BREAK_GAME_INVITE_PROBABILITY as _gi_prob_default
                _gi_prob = _gi_prob_default
            branch_game_invite = False
            chosen_game_type: str | None = None
            gi_prompt: str | None = None
            if (
                MINI_GAME_INVITE_ENABLED
                and _user_invite_toggle
                and _gi_prob > 0
            ):
                import random as _random
                if _random.random() < _gi_prob:
                    chosen_game_type = _pick_mini_game_type(lanlan_name)
                    if chosen_game_type is not None:
                        gi_prompt = _render_work_break_game_invite_prompt(
                            pending=water_pending,
                            game_type=chosen_game_type,
                            master_name=master_name_current,
                            lang=_break_lang,
                        )
                        if gi_prompt is not None:
                            branch_game_invite = True

            if branch_game_invite and chosen_game_type is not None and gi_prompt is not None:
                delivered_text, _proactive_sid_unused = await _deliver_break_reminder_via_llm(
                    lanlan_name=lanlan_name,
                    mgr=mgr,
                    system_prompt=_compose_break_system_prompt(gi_prompt),
                    channel='work_break_game_invite',
                    lang=_break_lang,
                )
                if delivered_text:
                    invite_session_id = str(uuid4())
                    _mini_game_invite_record_delivered(lanlan_name, invite_session_id)
                    _mini_game_invite_get_state(lanlan_name)['last_game_type'] = chosen_game_type
                    # Persist counter+1 + ever_delivered atomically (mini-game cooldown
                    # contract). Track success so we can fall back to the plain
                    # _increment_proactive_chat_total if persistence fails — otherwise
                    # the chat-total counter would skip this round entirely.
                    # CodeRabbit Major: don't double-count — the persistent record
                    # already does the +1, so plain counter is only the fallback.
                    _persist_ok = False
                    try:
                        await _record_invite_delivery_persistent(lanlan_name)
                        _persist_ok = True
                    except Exception as _persist_err:
                        logger.warning(
                            "[%s] record_invite_delivery_persistent failed: %s",
                            lanlan_name, _persist_err,
                        )
                    try:
                        from utils.instrument import counter as _instr_counter
                        # 与 proactive 通道共用 mini_game_invited，channel 维度区分；
                        # 不计 persist 成败——邀请 UI 已投递给用户即算一次邀请。
                        _instr_counter(
                            "mini_game_invited",
                            game_type=str(chosen_game_type)[:24],
                            channel="work_break",
                            force_first=False,
                        )
                    except Exception:
                        # 埋点 best-effort，失败不影响邀请投递
                        pass
                    options_payload = _build_mini_game_invite_options_payload(
                        invite_lang=_break_lang,
                        game_type=chosen_game_type,
                        session_id=invite_session_id,
                    )
                    try:
                        if mgr.websocket and hasattr(mgr.websocket, 'send_json'):
                            client_state = getattr(mgr.websocket, 'client_state', None)
                            if client_state is None or client_state == client_state.CONNECTED:
                                await mgr.websocket.send_json(options_payload)
                    except Exception as _ws_err:
                        logger.warning(
                            "[%s] work_break+game_invite options WS push failed: %s",
                            lanlan_name, _ws_err,
                        )
                    try:
                        mgr._activity_tracker.mark_work_break_used()
                    except Exception as _mark_err:
                        logger.warning(
                            "[%s] mark_work_break_used failed: %s",
                            lanlan_name, _mark_err,
                        )
                    if not _persist_ok:
                        # Persistence path failed → counter wasn't bumped.
                        # Fall back to the plain in-memory increment so the
                        # round still counts toward proactive_chat totals.
                        await _increment_proactive_chat_total(lanlan_name)
                    return await _end_proactive(JSONResponse({
                        "success": True,
                        "action": "chat",
                        "reason_code": PROACTIVE_REASON_CHAT_DELIVERED,
                        "message": "work-break + game-invite delivered",
                        "channel": "work_break_game_invite",
                        "game_type": chosen_game_type,
                        "invite_session_id": invite_session_id,
                    }))
                # Combo branch delivery failed → don't fall through to
                # regular drink branch (would double-charge the user's
                # attention). Pending stays armed for next round.
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "pass",
                    "reason_code": PROACTIVE_REASON_DELIVERY_PREEMPTED,
                    "message": "work-break + game-invite pending but delivery skipped",
                }))

            # Regular drink/stretch nudge branch
            wb_prompt, wb_seed = _render_work_break_prompt(
                pending=water_pending,
                master_name=master_name_current,
                lang=_break_lang,
            )
            delivered_text, _proactive_sid_unused = await _deliver_break_reminder_via_llm(
                lanlan_name=lanlan_name,
                mgr=mgr,
                system_prompt=_compose_break_system_prompt(wb_prompt),
                channel='work_break',
                lang=_break_lang,
            )
            if delivered_text:
                try:
                    mgr._activity_tracker.mark_work_break_used()
                except Exception as _mark_err:
                    logger.warning(
                        "[%s] mark_work_break_used failed: %s",
                        lanlan_name, _mark_err,
                    )
                # Same chats-since-response counter as anti_slack branch.
                _mini_game_invite_count_post_response_chat(lanlan_name)
                await _increment_proactive_chat_total(lanlan_name)
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "chat",
                    "reason_code": PROACTIVE_REASON_CHAT_DELIVERED,
                    "message": "work-break reminder delivered",
                    "channel": "work_break",
                    "seed": wb_seed,
                }))
            return await _end_proactive(JSONResponse({
                "success": True,
                "action": "pass",
                "reason_code": PROACTIVE_REASON_DELIVERY_PREEMPTED,
                "message": "work-break reminder pending but delivery skipped",
            }))

        # ========== Probabilistic skip (intensity-driven gate) ==========
        # ``skip_probability`` is rolled BEFORE we burn LLM cost.
        # Default 0 for non-gaming and varied gaming, so this only
        # kicks in for tagged competitive / immersive-horror gaming
        # — or whatever combos the user has dialed up via
        # preferences.json::skip_probability_overrides.
        #
        # The unfinished_thread guard means open threads still get
        # follow-ups even at skip=1.0: if the AI promised to come
        # back to something, we honour that promise regardless of
        # how silenced the user wanted us. The thread mechanism's
        # 2-followup hard cap already prevents harassment.
        if (
            not _debug_force_invite
            and activity_snapshot is not None
            and activity_snapshot.skip_probability > 0
            and activity_snapshot.unfinished_thread is None
        ):
            import random as _random
            if _random.random() < activity_snapshot.skip_probability:
                print(
                    f"[{lanlan_name}] skip_probability={activity_snapshot.skip_probability:.2f} "
                    f"rolled (state={activity_snapshot.state} intensity={activity_snapshot.game_intensity} "
                    f"genre={activity_snapshot.game_genre})，本轮跳过"
                )
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "pass",
                    "reason_code": PROACTIVE_REASON_PASS_THROTTLED,
                    "message": (
                        f"probabilistic skip: state={activity_snapshot.state} "
                        f"intensity={activity_snapshot.game_intensity} "
                        f"skip_prob={activity_snapshot.skip_probability:.2f}"
                    ),
                }))

        # ========== 解析 enabled_modes ==========
        # 兼容旧版前端：``enabled_modes`` 字段缺席 → 根据其它字段推断；显式传 ``[]``
        # 表示新版客户端"用户把所有 source toggle 都关了"，不能再走 BC fallback
        # 退化到 home/trending（否则 mini-game 邀请 toggle 单独开启的场景下 dice
        # miss 会让 home 兜底打破 toggle 契约——codex P1）。
        if 'enabled_modes' in data:
            enabled_modes = data.get('enabled_modes') or []
        else:
            content_type = data.get('content_type', None)
            screenshot_data = data.get('screenshot_data')
            if screenshot_data and isinstance(screenshot_data, str):
                enabled_modes = ['vision']
            elif data.get('use_window_search', False):
                enabled_modes = ['window']
            elif content_type == 'news':
                enabled_modes = ['news']
            elif content_type == 'video':
                enabled_modes = ['video']
            elif data.get('use_personal_dynamic', False):
                enabled_modes = ['personal']
            else:
                enabled_modes = ['home']

        # 是否有 5 分钟内未收尾话题。若有，restricted_screen_only / sources 空
        # 这两个早退分支都让步——AI 能基于 conversation history 接续旧话题，
        # 不需要任何外部素材。
        _has_unfinished_thread = (
            activity_snapshot is not None
            and activity_snapshot.unfinished_thread is not None
        )

        # restricted_screen_only：用户处于 gaming / focused_work，仅允许屏幕通道。
        # 把 enabled_modes 收紧到只剩 vision。如果前端这一轮根本没启用 vision，
        # 直接 pass —— 没东西可看，又不让聊外部，没必要继续。
        # 例外：有未收尾话题（5min 内 AI 提的问题用户还没回）→ 即使没 vision
        # 也允许跑下去，跟进上一个问题不需要外部素材。
        if (
            not _debug_force_invite
            and activity_snapshot is not None
            and activity_snapshot.propensity == 'restricted_screen_only'
        ):
            if 'vision' in enabled_modes:
                enabled_modes = ['vision']
                print(f"[{lanlan_name}] propensity=restricted_screen_only, 收紧 enabled_modes 到仅 vision")
            elif _has_unfinished_thread:
                enabled_modes = []
                print(f"[{lanlan_name}] propensity=restricted_screen_only 但有未收尾话题，允许 text-only 跟进")
            else:
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "pass",
                    "reason_code": PROACTIVE_REASON_PASS_RESTRICTED_SCREEN_ONLY,
                    "message": f"user state={activity_snapshot.state} restricts proactive to screen-only, but vision not enabled this round",
                }))

        print(f"[{lanlan_name}] 启用的搭话模式: {enabled_modes}")

        # ========== Mini-game 邀请短路 ==========
        # 过完 propensity / skip_probability / restricted_screen_only 这几道门后，
        # 独立掷一次 10% 骰子；命中即用静态 i18n 模板直投递邀请，跳过 Phase 1/2
        # LLM 与 source fetching。一次邀请被回应后 24h+10 chats cooldown，期间
        # 不再掷骰。activity_snapshot is None（隐私模式 / tracker 不可用）保守
        # 不发——无法判断是否在工作状态。
        try:
            invite_lang = _resolve_proactive_locale(data, mgr)
        except Exception:
            invite_lang = 'zh'
        # _user_invite_toggle 已经在上面 _debug_force_invite 计算前算过——把
        # toggle 关时旗标也连带禁用，保证早退 gate 不被绕过。
        invite_outcome = await _maybe_deliver_mini_game_invite(
            lanlan_name=lanlan_name,
            mgr=mgr,
            activity_snapshot=activity_snapshot,
            invite_lang=invite_lang,
            master_name=master_name_current,
            user_toggle_enabled=_user_invite_toggle,
        )
        if invite_outcome is not None:
            return await _end_proactive(JSONResponse(invite_outcome))

        # 用户把所有 source toggle 都关了（仅留 mini-game 邀请独立 toggle 触发本轮
        # 请求），mini-game 短路又没命中：没什么可聊。直接 pass 而不是落到下面源
        # picking 走空 list / 撞 "所有信息源获取失败" 500 分支。例外：仍然有未收尾
        # 话题 → 让 Phase 2 走 text-only 跟进路径（与 sources={} 但 thread 在的兜
        # 底语义对齐）。codex P1 指出：BC fallback 已经按 "字段缺席 vs 显式 []" 分
        # 流，这里对显式空清晰退出。
        if not enabled_modes and not _has_unfinished_thread:
            print(f"[{lanlan_name}] enabled_modes 空 + mini-game miss + 无 unfinished_thread → pass")
            return await _end_proactive(JSONResponse({
                "success": True,
                "action": "pass",
                "reason_code": PROACTIVE_REASON_PASS_SOURCE_EMPTY,
                "message": "no source modes enabled and mini-game invite did not fire",
            }))

        # 全局 source 衰减历史：进入 picking 前确保已惰性加载到内存（首次为线程池
        # IO，后续是 O(1) flag 检查）。同步 picking loop 后续直接读 dict。
        await _ensure_source_history_loaded()

        # ========== 0. 并行获取所有信息源内容（无 LLM） ==========
        screenshot_data = data.get('screenshot_data')
        has_screenshot = bool(screenshot_data) and isinstance(screenshot_data, str)
        # Avatar 位置元数据（前端截图时捕获的归一化坐标）
        avatar_position = data.get('avatar_position')
        
        async def _fetch_source(mode: str) -> tuple:
            """
            Fetch a single source; returns (mode, content_dict) or raises an exception.
            """
            if mode == 'vision':
                if not has_screenshot:
                    raise ValueError("无截图数据（screenshot_data 为空或类型不正确）")
                window_title = data.get('window_title', '')
                # ⚠️ Phase 1 不调用 vision_model 分析截图！
                # 截图将在 Phase 2 由 vision_model 直接读取原图，这里只做压缩。
                compressed_b64 = ''
                try:
                    b64_raw = screenshot_data.split(',', 1)[1] if ',' in screenshot_data else screenshot_data
                    compressed_b64 = await asyncio.to_thread(
                        decode_and_compress_screenshot_b64,
                        b64_raw,
                        COMPRESS_TARGET_HEIGHT,
                        COMPRESS_JPEG_QUALITY,
                    )
                    # 叠加 Avatar 文字注解（_fetch_source 内部 proactive_lang
                    # 尚未解析，Phase 1 使用全局语言；Phase 2 会用请求级别的 proactive_lang）
                    if avatar_position and isinstance(avatar_position, dict):
                        try:
                            from utils.screenshot_utils import overlay_avatar_annotation
                            from utils.language_utils import get_global_language_full
                            compressed_b64 = await asyncio.to_thread(
                                overlay_avatar_annotation,
                                compressed_b64, avatar_position, lanlan_name,
                                get_global_language_full(),
                            )
                        except Exception as ann_err:
                            logger.warning(f"[{lanlan_name}] Phase 1 avatar annotation failed: {ann_err}")
                    jpg_size_kb = len(compressed_b64) * 3 // 4 // 1024
                    print(f"[{lanlan_name}] Vision 通道: 截图压缩完成 {jpg_size_kb}KB (Phase 2 将直接分析)")
                except Exception as compress_err:
                    logger.warning(f"[{lanlan_name}] 截图压缩失败（Phase 2 将无法使用截图）: {compress_err}")
                return (mode, {'window_title': window_title, 'screenshot_b64': compressed_b64})
            
            elif mode == 'news':
                news_content = await fetch_news_content(limit=_PHASE1_FETCH_PER_SOURCE)
                if not news_content['success']:
                    raise ValueError(f"获取新闻失败: {news_content.get('error')}")
                formatted = format_news_content(news_content)
                _log_news_content(lanlan_name, news_content)
                # 提取链接信息
                links = _extract_links_from_raw(mode, news_content)
                return (mode, {'formatted_content': formatted, 'raw_data': news_content, 'links': links})
            
            elif mode == 'video':
                video_content = await fetch_video_content(limit=_PHASE1_FETCH_PER_SOURCE)
                if not video_content['success']:
                    raise ValueError(f"获取视频失败: {video_content.get('error')}")
                formatted = format_video_content(video_content)
                _log_video_content(lanlan_name, video_content)
                links = _extract_links_from_raw(mode, video_content)
                return (mode, {'formatted_content': formatted, 'raw_data': video_content, 'links': links})
            
            elif mode == 'window':
                window_context_content = await fetch_window_context_content(limit=5)
                if not window_context_content['success']:
                    raise ValueError(f"获取窗口上下文失败: {window_context_content.get('error')}")
                formatted = format_window_context_content(window_context_content)
                raw_title = window_context_content.get('window_title', '')
                sanitized_title = raw_title[:30] + '...' if len(raw_title) > 30 else raw_title
                print(f"[{lanlan_name}] 成功获取窗口上下文: {sanitized_title}")
                return (mode, {'formatted_content': formatted, 'raw_data': window_context_content, 'links': []})
            
            elif mode == 'home':
                trending_content = await fetch_trending_content(
                    bilibili_limit=_PHASE1_FETCH_PER_SOURCE,
                    weibo_limit=_PHASE1_FETCH_PER_SOURCE
                )
                if not trending_content['success']:
                    raise ValueError(f"获取首页推荐失败: {trending_content.get('error')}")
                formatted = format_trending_content(trending_content)
                _log_trending_content(lanlan_name, trending_content)
                links = _extract_links_from_raw(mode, trending_content)
                return (mode, {'formatted_content': formatted, 'raw_data': trending_content, 'links': links})

            elif mode == 'personal':
                personal_dynamics = await fetch_personal_dynamics(limit=_PHASE1_FETCH_PER_SOURCE)
                if not personal_dynamics['success']:
                    raise ValueError(f"获取个人动态失败: {personal_dynamics.get('error')}")
                formatted = format_personal_dynamics(personal_dynamics)
                _log_personal_dynamics(lanlan_name, personal_dynamics)
                links = _extract_links_from_raw(mode, personal_dynamics)
                return (mode, {'formatted_content': formatted, 'raw_data': personal_dynamics, 'links': links})
            
            elif mode == 'music':
                return (mode, {'placeholder': True, 'note': '关键词将在 Phase 1 开始前生成'})
            
            elif mode == 'meme':
                # meme 关键词将由合并 LLM 调用生成，此处仅占位
                return (mode, {'placeholder': True, 'note': '关键词将由合并 Phase 1 LLM 生成'})

            else:
                raise ValueError(f"未知模式: {mode}")
        
        # 并行获取所有信息源
        fetch_tasks = [
            _fetch_source(m)
            for m in enabled_modes
        ]
        fetch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)
        
        # 收集成功的信息源
        sources: dict[str, dict] = {}
        for i, result in enumerate(fetch_results):
            if isinstance(result, Exception):
                failed_mode = enabled_modes[i]
                logger.warning(f"[{lanlan_name}] 信息源 [{failed_mode}] 获取失败: {result}")
                continue
            mode, content = result
            sources[mode] = content
        
        if not sources:
            # 例外：未收尾话题模式下 enabled_modes 可能本就被清空（restricted_screen_only
            # + 无 vision），sources 必定为空但不应当 pass —— 让 Phase 2 拿对话
            # 历史 + state_section 跑 text-only [CHAT] 跟进。
            if not _has_unfinished_thread:
                return await _end_proactive(JSONResponse(
                    _proactive_pass_body(
                        PROACTIVE_REASON_ERROR_SOURCE_FETCH_FAILED,
                        success=False,
                        error="所有信息源获取失败",
                    ),
                    status_code=500,
                ))
            print(f"[{lanlan_name}] sources 为空但有未收尾话题，进入 text-only 跟进路径")

        # Phase 1 preempt check：信息源并行 fetch 完，正式进入 LLM 前先瞄一眼
        if mgr.state.is_proactive_preempted():
            return await _end_proactive(JSONResponse(_proactive_preempted_json("phase1_post_fetch")))

        print(f"[{lanlan_name}] 成功获取 {len(sources)} 个信息源: {list(sources.keys())}")

        # ========== 1. 获取记忆上下文 (New Dialog) ==========
        # new_dialog 返回格式：
        # ========以下是{name}的内心活动========
        # {内心活动/Settings}...
        # 现在时间...整理了近期发生的事情。
        # Name | Content
        # ...
        
        raw_memory_context = ""
        try:
            from utils.internal_http_client import get_internal_http_client
            _pt_client = get_internal_http_client()
            resp = await _pt_client.get(
                f"http://127.0.0.1:{MEMORY_SERVER_PORT}/new_dialog/{lanlan_name}",
                timeout=5.0,
            )
            resp.raise_for_status()  # Check for HTTP errors explicitly
            if resp.status_code == 200:
                raw_memory_context = resp.text
            else:
                logger.warning(f"[{lanlan_name}] 记忆服务返回非200状态: {resp.status_code}，使用空上下文")
        except Exception as e:
            logger.warning(f"[{lanlan_name}] 获取记忆上下文失败，使用空上下文: {e}")
        
        # 解析 new_dialog 响应：把"内心活动"与"对话历史"切开。
        # 切分逻辑（locale 无关）集中在 prompts_memory.split_inner_thoughts_and_history，
        # 以 INNER_THOUGHTS_DYNAMIC 的多语言模板为准；任一 locale 都匹配不到时返回
        # None，这里兜底为"全部当历史、内心活动留空"并打 warning（不再静默错位）。
        def _parse_new_dialog(text: str) -> tuple[str, str]:
            if not text:
                return "", ""
            from config.prompts.prompts_memory import split_inner_thoughts_and_history
            split = split_inner_thoughts_and_history(text)
            if split is None:
                logger.warning(
                    "[%s] new_dialog 未匹配到内心活动分隔句（任一 locale），"
                    "整段归入对话历史，当前内心留空",
                    lanlan_name,
                )
                return text, ""
            inner_thoughts_part, history_part = split
            return history_part, inner_thoughts_part

        memory_context, inner_thoughts = _parse_new_dialog(raw_memory_context)

        # Phase 1 preempt check：memory_server new_dialog 是 phase1 里首次大 await
        # （httpx timeout 5s）。用户在这期间打断只能等超时才有下一次 check，
        # 这里补一刀。
        if mgr.state.is_proactive_preempted():
            return await _end_proactive(JSONResponse(_proactive_preempted_json("phase1_post_memory")))

        # ========== 2. 选择语言 ==========
        # 与 mini-game 邀请短路同源：request body → mgr.user_language → 全局缓存。
        # 见 _resolve_proactive_locale 的 docstring。
        try:
            proactive_lang = _resolve_proactive_locale(data, mgr)
        except Exception:
            proactive_lang = 'zh'
        topic_hook_lang = _resolve_topic_hook_locale(data, mgr, fallback=proactive_lang)
        
        # ========== 3. 注入近期搭话记录 ==========
        proactive_chat_history_prompt = _format_recent_proactive_chats(lanlan_name, proactive_lang)

        # 趁机把 open_threads 计算起来——和下面 Phase 1 unified LLM 调用并行。
        # 缓存按用户消息序号失效；没新用户发言就 no-op 直接返回。Phase 2 读
        # snapshot 时会拿到这次的结果（如果赶上了）；赶不上就用上一次的缓存。
        try:
            mgr._activity_tracker.kickoff_open_threads_compute(lang=topic_hook_lang)
        except Exception as _ot_err:
            logger.debug(f"[{lanlan_name}] kickoff_open_threads_compute failed: {_ot_err}")

        # ========== 3.5 反思 + 回调话题（通过 memory_server API） ==========
        # 认知框架：Facts → Reflection(pending) → 主动搭话自然提及 → 用户反馈 → Persona
        #
        # 用户在 gaming / focused_work 状态下不应自然回忆——会很尬。直接跳过整段
        # （也省 reflect POST 的 15s timeout 风险）。stale_returning 反而欢迎回忆。
        followup_topics_prompt = ""
        _followup_topics = []
        _surfaced_reflection_ids = []  # 记录本次搭话提及了哪些 pending 反思
        _allow_reminiscence = (
            activity_snapshot is None
            or activity_snapshot.propensity != 'restricted_screen_only'
        )
        if not _allow_reminiscence:
            print(f"[{lanlan_name}] propensity=restricted_screen_only, 跳过反思/回忆话题获取")
        # 复用 internal_http_client 单例：proactive_chat 每次主动搭话都走此路径。
        # 仅 read：取 followup_topics 候选用于本轮 prompt 注入。
        # 历史上这一段还前置调过 POST /reflect/{name}（"自动状态迁移 + 反思合成"），
        # 已删除——合成迁到 ``_periodic_reflection_synthesis_loop`` 后端循环、
        # auto_promote 早就由 ``_periodic_auto_promote_loop`` 每 180s 跑。把
        # mutation 留在 proactive 关键路径上既拖延 ~15s response、又让整个
        # reflection 生命周期跟前端 setTimeout 强耦合（前端不开 → 永不合成）。
        if _allow_reminiscence:
            try:
                from utils.internal_http_client import get_internal_http_client
                _mem_base = f"http://127.0.0.1:{MEMORY_SERVER_PORT}"
                _mem_client = get_internal_http_client()
                _topics_resp = await _mem_client.get(
                    f"{_mem_base}/followup_topics/{lanlan_name}", timeout=5.0,
                )
                if _topics_resp.status_code == 200:
                    _followup_topics = _topics_resp.json().get('topics', [])
                    if _followup_topics:
                        try:
                            (
                                followup_topics_prompt,
                                _surfaced_reflection_ids,
                            ) = _render_followup_topic_hooks(
                                topic_hook_lang,
                                _followup_topics,
                            )
                        except Exception as _followup_prompt_err:
                            logger.debug(f"[{lanlan_name}] followup topic prompt build failed: {_followup_prompt_err}")
                        print(f"[{lanlan_name}] 回调话题候选: {len(_followup_topics)} 条")
            except Exception as e:
                logger.debug(f"[{lanlan_name}] 回调话题获取失败（不影响主流程）: {e}")

        # Phase 1 preempt check：followup GET(5s) 是一段可能拖久的 await，
        # 整段裸跑会让用户打断后继续跑完 LLM 配置和后续步骤，再到 pre-LLM
        # check 才识破。这里补一刀。
        if mgr.state.is_proactive_preempted():
            return await _end_proactive(JSONResponse(_proactive_preempted_json("phase1_post_reflect")))

        # ========== 4. 获取 LLM 配置 ==========
        # 主动搭话全链路（Phase1 筛选 / Phase2 生成 / regen）用 conversation tier
        # 而非 correction tier：correction（纠错）模型在不开思考时较难稳定遵循
        # "第一行写来源标签" 的格式，容易把人设约束块当正文吐出来；conversation
        # 是主对话主力模型，格式遵循更稳。仍保持 disable_thinking（vision+思考必超时）。
        try:
            conversation_config = _config_manager.get_model_api_config('conversation')
            conversation_model = conversation_config.get('model')
            conversation_base_url = conversation_config.get('base_url')
            conversation_api_key = conversation_config.get('api_key')
            conversation_provider_type = conversation_config.get('provider_type')

            if not conversation_model or not conversation_api_key:
                logger.error("对话模型配置缺失: model或api_key未设置")
                return await _end_proactive(JSONResponse({
                    "success": False,
                    "reason_code": PROACTIVE_REASON_ERROR_INTERNAL,
                    "error": "对话模型配置缺失",
                    "detail": "请在设置中配置对话模型的model和api_key"
                }, status_code=500))

            vision_config = _config_manager.get_model_api_config('vision')
            vision_model_name = vision_config.get('model', '')
            vision_base_url = vision_config.get('base_url', '')
            vision_api_key = vision_config.get('api_key', '')
            vision_provider_type = vision_config.get('provider_type')
            has_vision_model = bool(vision_model_name and vision_api_key)
            if not has_vision_model:
                logger.info("Vision 模型未配置，Phase 2 将退回使用对话模型")
        except Exception as e:
            logger.error(f"获取模型配置失败: {e}")
            return await _end_proactive(JSONResponse({
                "success": False,
                "reason_code": PROACTIVE_REASON_ERROR_INTERNAL,
                "error": "模型配置异常",
                "detail": str(e)
            }, status_code=500))

        async def _make_llm(temperature: float = 1.0,
                            max_completion_tokens: int = PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
                            use_vision: bool = False, disable_thinking: bool = True):
            """
            Create an LLM instance. use_vision=True uses the vision model;
            when disable_thinking=False (Focus thinking-on) the provider's
            thinking-disable extras are stripped while other auto-resolved
            extras (e.g. web_search) are preserved.
            """
            if use_vision and has_vision_model:
                m, bu, ak = vision_model_name, vision_base_url, vision_api_key
                provider_type = vision_provider_type
            else:
                m, bu, ak = conversation_model, conversation_base_url, conversation_api_key
                provider_type = conversation_provider_type
            from config import DIALOG_LLM_STREAM_TIMEOUT_SECONDS
            kw: dict = dict(
                temperature=temperature,
                max_completion_tokens=max_completion_tokens,
                streaming=True,
                timeout=DIALOG_LLM_STREAM_TIMEOUT_SECONDS,  # hang-guard for the streaming call
                provider_type=provider_type,
            )
            if not disable_thinking:
                # Focus thinking-on: strip ONLY the thinking-disable keys from
                # the provider's auto-resolved extra_body, KEEP the rest. Setting
                # extra_body=None would skip all auto-resolved extras and
                # silently drop e.g. step-2-mini's built-in web_search on focused
                # proactive Phase-2 generations (对偶 inline path
                # OmniOfflineClient._focus_stream_overrides → focus_extra_body).
                kw["extra_body"] = focus_extra_body(m)
            return await create_chat_llm_async(m, bu, ak, **kw)  # noqa: LLM_OUTPUT_BUDGET  # budget + timeout set in kw above (splat invisible to the lint).

        async def _llm_call_with_retry(
            system_prompt: str, label: str, *,
            temperature: float = 1.0,
            max_completion_tokens: int = PROACTIVE_PHASE1_UNIFIED_MAX_TOKENS,
            timeout: float = 16.0,
            use_vision: bool = False, disable_thinking: bool = True,
            image_b64: str = '',
            dynamic_context: str = '',
        ) -> str:
            """
            LLM call with retry. When image_b64 is non-empty, the screenshot is sent multimodally.
            dynamic_context: dynamic context injected into the HumanMessage so the SystemMessage stays cacheable.
            """
            begin_text = _loc(BEGIN_GENERATE, proactive_lang)
            human_text = f"{dynamic_context}\n\n{begin_text}" if dynamic_context else begin_text
            if image_b64:
                human_content = [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                    {"type": "text", "text": human_text},
                ]
            else:
                human_content = human_text
            messages = [SystemMessage(content=system_prompt), HumanMessage(content=human_content)]

            from utils.token_tracker import set_call_type
            set_call_type("proactive")
            max_retries = 3
            retry_delays = [1, 2]
            for attempt in range(max_retries):
                try:
                    # 使用 async with 确保 ChatOpenAI (AsyncOpenAI) 实例被正确关闭
                    async with (await _make_llm(temperature=temperature,
                                                max_completion_tokens=max_completion_tokens,
                                                use_vision=use_vision,
                                                disable_thinking=disable_thinking)) as llm:
                        response = await asyncio.wait_for(
                            llm.ainvoke(messages),
                            timeout=timeout
                        )
                        # [临时调试]
                        print(f"\n[PROACTIVE-DEBUG] LLM output [{label}]: {response.content[:500]}...\n")
                        return response.content.strip()
                except _proactive_llm_retry_error_types() as e:
                    if attempt < max_retries - 1:
                        logger.warning(f"[{lanlan_name}] LLM [{label}] 调用失败 (尝试 {attempt + 1}/{max_retries}): {e}")
                        await asyncio.sleep(retry_delays[attempt])
                    else:
                        logger.error(f"[{lanlan_name}] LLM [{label}] 调用失败，已达最大重试: {e}")
                        raise
            raise RuntimeError("Unexpected")
        
        # ================================================================
        # Phase 1: 合并 LLM 调用（web 筛选 + music 关键词 + meme 关键词）
        # ⚠️ 一阶段一定不要分析屏幕！截图会在二阶段由 vision_model 直接 feed in。
        # - 所有文本源合并 → 1 次 LLM 同时完成 web 筛选、music/meme 关键词生成
        # - 来源动态权重系统在 LLM 调用前剔除低权重通道
        # 总计最多 1 次 LLM 调用
        # ================================================================
        
        vision_content = sources.get('vision')  # 仅保留给 Phase 2 使用，Phase 1 不处理
        music_content = sources.get('music')
        meme_content = sources.get('meme')
        logger.debug(f"[{lanlan_name}] 主动搭话-音乐内容: type={type(music_content)}, success={music_content.get('success') if music_content else 'N/A'}")
        logger.debug(f"[{lanlan_name}] 主动搭话-表情包内容: type={type(meme_content)}, success={meme_content.get('success') if meme_content else 'N/A'}")
        
        all_web_links: list[dict] = []
        
        # 收集音乐链接（在 Phase 1 Web 筛选完成后）
        # meme 也不经过 Phase 1 LLM 筛选，直接添加话题
        web_modes = [m for m in sources if m not in ('vision', 'music', 'meme')]
        
        merged_web_content = ""
        if web_modes:
            parts = []
            seen_topic_keys: set[str] = set()
            remaining_total = _PHASE1_TOTAL_TOPIC_TARGET
            for m in web_modes:
                if remaining_total <= 0:
                    break
                src = sources[m]
                label_map = PROACTIVE_SOURCE_LABELS.get(proactive_lang, PROACTIVE_SOURCE_LABELS['en'])
                label = label_map.get(m, m)
                links = src.get('links', []) or []

                selected_links: list[dict] = []
                for link in links:
                    title = link.get('title', '')
                    url = link.get('url', '')
                    key = _source_hash(url, title)
                    if key:
                        # 跨会话衰减 skip：5h 硬窗口，之后按 web 半衰期概率瞬移到下一条
                        if key in seen_topic_keys or _should_skip_source(key):
                            continue
                        seen_topic_keys.add(key)
                    # 给 link 打上来源 mode 标记，用于细粒度 channel 记录
                    if 'mode' not in link:
                        link['mode'] = m
                    selected_links.append(link)
                    if len(selected_links) >= remaining_total:
                        break

                if selected_links:
                    all_web_links.extend(selected_links)
                    remaining_total -= len(selected_links)
                    lines = []
                    for idx, item in enumerate(selected_links, start=1):
                        from utils.tokenize import truncate_to_tokens as _ttt
                        title = item.get('title', '').strip()
                        if not title:
                            continue
                        source = item.get('source', '').strip()
                        url = item.get('url', '').strip()
                        suffix = []
                        if source:
                            suffix.append(f"来源: {source}")
                        if url:
                            suffix.append(f"URL: {url}")
                        ext = (" | " + " | ".join(suffix)) if suffix else ""
                        # 单条外部内容截到 PROACTIVE_EXTERNAL_PER_ITEM_MAX_TOKENS，
                        # 防止个别 title/url 异常长撑爆 prompt。
                        item_line = _ttt(f"{idx}. {title}{ext}", PROACTIVE_EXTERNAL_PER_ITEM_MAX_TOKENS)
                        lines.append(item_line)
                    if lines:
                        parts.append(f"--- {label} ---\n" + "\n".join(lines))
                        continue

                content_text = src.get('formatted_content', '')
                if content_text:
                    compact_lines = [ln.strip() for ln in content_text.splitlines() if ln.strip()]
                    if compact_lines:
                        fallback_lines = compact_lines[:remaining_total]
                        if fallback_lines:
                            from utils.tokenize import truncate_to_tokens as _ttt
                            fallback_lines = [
                                _ttt(ln, PROACTIVE_EXTERNAL_PER_ITEM_MAX_TOKENS)
                                for ln in fallback_lines
                            ]
                            parts.append(f"--- {label} ---\n" + "\n".join(fallback_lines))
                            remaining_total -= len(fallback_lines)
            from utils.tokenize import truncate_to_tokens as _ttt
            # 兜底总和截断：防止 20 source × 200 token = 4k 超过 2k 总预算
            merged_web_content = _ttt(
                "\n\n".join(parts), PROACTIVE_EXTERNAL_TOTAL_MAX_TOKENS
            )
        
        # Phase 1 结果收集
        phase1_topics: list[tuple[str, str]] = []  # [(channel, topic_summary), ...]
        source_links: list[dict] = []  # [{"title": ..., "url": ..., "source": ...}]
        selected_web_link = None
        selected_web_topic_key = None
        selected_music_link = None
        selected_music_topic_key = None
        selected_meme_link = None
        selected_meme_topic_key = None

        # 【加固】如果正在放歌或处于冷却期，强制清空 music 通道，彻底跳过搜歌逻辑
        if is_playing_music or music_cooldown:
            if music_content:
                reason = "音乐正在播放" if is_playing_music else "用户连续秒关，音乐冷却中"
                logger.debug(f"[{lanlan_name}]-{reason}，强制屏蔽 Phase 1 搜歌逻辑")
            music_content = None
            sources.pop('music', None)

        # ============================================================
        # 来源动态权重过滤（vision / 已屏蔽的 music 不参与权重计算）
        #
        # ``reminiscence`` 作为虚拟 channel：当本轮已经从 memory_server 取到
        # pending followup topics 时，把它放进权重计算池。和 web/news/music
        # 一样按使用频率衰减——AI 连续多次"回忆"会让 reminiscence 进入
        # suppressed 集合，本轮就跳过 followup_topics_prompt（per-reflection
        # cooldown 在 reflection.py 那侧另算，这里是 channel 级别的兜底）。
        # ============================================================
        non_vision_modes = [m for m in enabled_modes if m != 'vision' and m in sources]
        weight_candidates = list(non_vision_modes)
        if _surfaced_reflection_ids:
            weight_candidates.append('reminiscence')
        if weight_candidates:
            source_weights = _compute_source_weights(lanlan_name, weight_candidates)
            suppressed = _filter_sources_by_weight(source_weights)
            weight_str = ' '.join(f"{ch}={w:.3f}" for ch, w in source_weights.items())
            logger.debug(f"[{lanlan_name}] 来源权重: {weight_str} | 剔除: {suppressed or '无'}")

            for ch in suppressed:
                sources.pop(ch, None)
            if 'music' in suppressed:
                music_content = None
            if 'meme' in suppressed:
                meme_content = None
            if 'reminiscence' in suppressed:
                # 回忆 channel 被 throttle：只清空旧 reflection。
                # 后台深话题池走独立 one-shot 触发，不在 proactive prompt 里消费。
                if followup_topics_prompt:
                    print(f"[{lanlan_name}] reminiscence channel suppressed by weight, dropping followup section")
                _followup_topics = []
                _surfaced_reflection_ids = []
                followup_topics_prompt = ""

            # 被剔除的 web 子通道不参与 merged_web_content（sources 已弹出，
            # 但 merged_web_content 已经构建完毕，需要重新构建）
            if suppressed & set(web_modes):
                # 重新构建 merged_web_content，排除被剔除的通道
                remaining_web_modes = [m for m in web_modes if m not in suppressed]
                if remaining_web_modes:
                    # 先从 all_web_links 中移除被剔除通道的链接
                    all_web_links = [lk for lk in all_web_links if lk.get('mode') not in suppressed]
                    parts = []
                    seen_topic_keys_2: set[str] = set()
                    remaining_total_2 = _PHASE1_TOTAL_TOPIC_TARGET
                    for m in remaining_web_modes:
                        if remaining_total_2 <= 0:
                            break
                        src = sources.get(m)
                        if not src:
                            continue
                        label_map = PROACTIVE_SOURCE_LABELS.get(proactive_lang, PROACTIVE_SOURCE_LABELS['en'])
                        label = label_map.get(m, m)
                        links = src.get('links', []) or []
                        selected_links_2: list[dict] = []
                        for link in links:
                            title = link.get('title', '')
                            url = link.get('url', '')
                            key = _source_hash(url, title)
                            if key:
                                if key in seen_topic_keys_2 or _should_skip_source(key):
                                    continue
                                seen_topic_keys_2.add(key)
                            if 'mode' not in link:
                                link['mode'] = m
                            selected_links_2.append(link)
                            if len(selected_links_2) >= remaining_total_2:
                                break
                        if selected_links_2:
                            remaining_total_2 -= len(selected_links_2)
                            lines = []
                            from utils.tokenize import truncate_to_tokens as _ttt2
                            for idx, item in enumerate(selected_links_2, start=1):
                                t = item.get('title', '').strip()
                                if not t:
                                    continue
                                s = item.get('source', '').strip()
                                u = item.get('url', '').strip()
                                suffix = []
                                if s:
                                    suffix.append(f"来源: {s}")
                                if u:
                                    suffix.append(f"URL: {u}")
                                ext = (" | " + " | ".join(suffix)) if suffix else ""
                                # 同上路径，单条 cap
                                lines.append(_ttt2(
                                    f"{idx}. {t}{ext}",
                                    PROACTIVE_EXTERNAL_PER_ITEM_MAX_TOKENS,
                                ))
                            if lines:
                                parts.append(f"--- {label} ---\n" + "\n".join(lines))
                    from utils.tokenize import truncate_to_tokens as _ttt3
                    merged_web_content = _ttt3(
                        "\n\n".join(parts), PROACTIVE_EXTERNAL_TOTAL_MAX_TOKENS
                    )
                else:
                    merged_web_content = ""
                    all_web_links = []

        # ============================================================
        # 合并 Phase 1 LLM 调用：web 筛选 + music 关键词 + meme 关键词
        # 一次 LLM 调用完成所有任务，降低 RPM
        # ============================================================
        has_music_task = bool(music_content and music_content.get('placeholder'))
        has_meme_task = bool(meme_content and meme_content.get('placeholder'))
        has_web_task = bool(merged_web_content)

        # 只要有至少一个任务就发起 LLM 调用
        unified_parsed: dict = {'web': None, 'music_keyword': None, 'meme_keyword': None}
        # 先定义 enriched_memory_context 保证后续引用不报 UnboundLocalError
        enriched_memory_context = memory_context
        if followup_topics_prompt:
            enriched_memory_context = memory_context + "\n" + followup_topics_prompt

        if has_web_task or has_music_task or has_meme_task:
            # Phase 1 preempt check：拨号前最后一次检查。大头 LLM 调用即将开始，
            # 此后等待期间用户抢占只能靠流结束后的兜底识别。
            if mgr.state.is_proactive_preempted():
                return await _end_proactive(JSONResponse(_proactive_preempted_json("phase1_pre_llm")))
            try:
                from config.prompts.prompts_proactive import build_unified_phase1_prompt
                unified_prompt = build_unified_phase1_prompt(
                    proactive_lang,
                    merged_content=merged_web_content if has_web_task else None,
                    memory_context=enriched_memory_context,
                    recent_chats_section=proactive_chat_history_prompt,
                    music_ctx={'lanlan_name': lanlan_name, 'master_name': master_name_current} if has_music_task else None,
                    meme_enabled=has_meme_task,
                    lanlan_name=lanlan_name,
                    master_name=master_name_current,
                )
                unified_result_text = await _llm_call_with_retry(unified_prompt, "unified_phase1")
                print(f"[{lanlan_name}] Phase 1 合并 LLM 结果: {unified_result_text[:500]}")
                unified_parsed = _parse_unified_phase1_result(unified_result_text)
                logger.debug(f"[{lanlan_name}] Phase 1 解析: web={'有' if unified_parsed.get('web') else '无'}, "
                           f"music_kw={unified_parsed.get('music_keyword', 'N/A')}, "
                           f"meme_kw={unified_parsed.get('meme_keyword', 'N/A')}")
            except Exception as e:
                logger.warning(f"[{lanlan_name}] Phase 1 合并 LLM 调用异常: {type(e).__name__}: {e}，降级处理")
                # LLM 失败：各通道降级
                unified_parsed = {'web': None, 'music_keyword': None, 'meme_keyword': None}

        # ============================================================
        # 解析 web 结果 → 链接匹配 → 去重
        # ============================================================
        web_parsed = unified_parsed.get('web')
        if web_parsed and web_parsed.get('title'):
            matched = _lookup_link_by_title(web_parsed.get('title', ''), all_web_links)
            topic_key = _source_hash(
                matched.get('url', '') if matched else '',
                web_parsed.get('title', ''),
            )
            # matched 的链接已经在 picking 阶段过了一次 _should_skip_source，
            # 这里再 roll 等于让等效 p_skip = 1-(1-p)^2，违背单次半衰期模型。
            # 仅对未匹配（LLM 幻觉的 title-only 候选）兜底再判一次。
            needs_recheck = bool(topic_key) and matched is None
            if needs_recheck and _should_skip_source(topic_key):
                print(f"[{lanlan_name}] Phase 1 title-only 话题命中衰减，跳过: {web_parsed.get('title','')[:60]}")
            else:
                if matched:
                    selected_web_link = {
                        'title': web_parsed.get('title', matched.get('title', '')),
                        'url': matched['url'],
                        'source': web_parsed.get('source', matched.get('source', '')),
                        'mode': matched.get('mode', 'web'),  # 保留细粒度 mode
                    }
                    print(f"[{lanlan_name}] Phase 1 链接预匹配成功: {matched.get('title','')[:60]}")
                else:
                    print(f"[{lanlan_name}] Phase 1 未在 web_links 中匹配到标题: {web_parsed.get('title','')[:60]}")
                # 不论 matched 与否，都把 topic_key 留下来供 Phase 2 后落盘 ——
                # 哪怕只有 title 也参与衰减历史，避免同样的标题被反复 surface
                selected_web_topic_key = topic_key
                # 用 web_parsed 的 summary 或原始文本作为 topic
                web_topic_text = web_parsed.get('summary', web_parsed.get('title', ''))
                phase1_topics.append(('web', web_topic_text.strip()))

        # ============================================================
        # 并行后置 fetch：music + meme（使用 LLM 生成的关键词）
        # ============================================================
        music_keyword = unified_parsed.get('music_keyword')
        meme_keyword = unified_parsed.get('meme_keyword')

        async def _fetch_music_with_fallback(kw: str):
            """Search music with the LLM keyword; falls back to a random recommendation on failure."""
            try:
                raw = await fetch_music_content(keyword=kw, limit=5)
                if raw and raw.get('success'):
                    return raw
            except Exception as e:
                logger.warning(f"[{lanlan_name}] 音乐关键词 '{kw}' 搜索异常: {e}")
            logger.warning(f"[{lanlan_name}] 音乐关键词 '{kw}' 搜索失败，尝试随机推荐")
            try:
                return await fetch_music_content(keyword="", limit=5)
            except Exception:
                return None

        async def _fetch_meme_with_fallback(kw: str):
            """Search memes with the LLM keyword; falls back to random hot words on failure.

            ``effective_keyword`` marks the search term actually in effect this
            time: on a keyword hit it is kw (it describes the meme content, and
            the downstream topic carries it); on the random hot-word fallback it
            is blanked, to avoid falsely claiming "this image is about X".
            """
            try:
                raw = await asyncio.wait_for(
                    fetch_meme_content(keyword=kw, limit=_PHASE1_FETCH_PER_SOURCE),
                    timeout=12.0
                )
                if raw and raw.get('success'):
                    raw['effective_keyword'] = kw
                    return raw
            except Exception as e:
                logger.warning(f"[{lanlan_name}] 表情包关键词 '{kw}' 搜索异常: {e}")
            logger.warning(f"[{lanlan_name}] 表情包关键词 '{kw}' 搜索失败，尝试随机热词")
            try:
                raw = await asyncio.wait_for(
                    fetch_meme_content(keyword="", limit=_PHASE1_FETCH_PER_SOURCE),
                    timeout=12.0
                )
                if raw:
                    raw['effective_keyword'] = ""
                return raw
            except Exception:
                return None

        fetch_tasks_p1: list = []
        fetch_labels: list[str] = []

        if has_music_task and not unified_parsed.get('music_pass'):
            kw = music_keyword or ""
            fetch_tasks_p1.append(_fetch_music_with_fallback(kw))
            fetch_labels.append('music')
        elif has_music_task:
            print(f"[{lanlan_name}] Phase 1 音乐通道明确 PASS，跳过后置 fetch")
        if has_meme_task and not unified_parsed.get('meme_pass'):
            kw = meme_keyword or ""
            fetch_tasks_p1.append(_fetch_meme_with_fallback(kw))
            fetch_labels.append('meme')
        elif has_meme_task:
            print(f"[{lanlan_name}] Phase 1 表情包通道明确 PASS，跳过后置 fetch")

        if fetch_tasks_p1:
            # Phase 1 preempt check：unified LLM 刚回，music/meme 后置 fetch 前再瞄
            if mgr.state.is_proactive_preempted():
                return await _end_proactive(JSONResponse(_proactive_preempted_json("phase1_post_llm")))
            fetch_results_p1 = await asyncio.gather(*fetch_tasks_p1, return_exceptions=True)
            for label_p1, result_p1 in zip(fetch_labels, fetch_results_p1):
                if isinstance(result_p1, Exception):
                    logger.warning(f"[{lanlan_name}] Phase 1 后置 fetch [{label_p1}] 异常: {result_p1}")
                    continue
                if label_p1 == 'music' and result_p1 and result_p1.get('success'):
                    _log_music_content(lanlan_name, result_p1)
                    music_content = {
                        'formatted_content': _format_music_content(result_p1, proactive_lang),
                        'raw_data': result_p1,
                    }
                elif label_p1 == 'meme' and result_p1 and result_p1.get('success'):
                    meme_content = {
                        'success': True,
                        'data': result_p1.get('data', []),
                        'raw_data': result_p1,
                        'source': result_p1.get('source', '表情包'),
                        'keyword': result_p1.get('effective_keyword', ''),
                    }
                    print(f"[{lanlan_name}] 成功获取 {len(result_p1.get('data', []))} 个表情包 (来源: {result_p1.get('source', '?')})")

        # ============================================================
        # 音乐话题组装（遍历候选 → 衰减 skip → 暂存链接）
        # 与 web/meme 对偶：超取 N 条后逐条概率 skip，遇命中瞬移到下一条。
        # 全部命中则清空 music_content 让通道整体降级。
        # ============================================================
        if music_content and music_content.get('formatted_content'):
            music_topic = music_content['formatted_content']
            if music_topic:
                music_tracks = music_content.get('raw_data', {}).get('data', [])
                if music_tracks:
                    picked_track: dict | None = None
                    picked_key: str = ''
                    for candidate_track in music_tracks:
                        track_url = candidate_track.get('url', '')
                        track_name = candidate_track.get('name', '')
                        track_artist = candidate_track.get('artist', '')
                        candidate_key = _source_hash(
                            track_url, f"{track_name} - {track_artist}"
                        )
                        if candidate_key and _should_skip_source(candidate_key):
                            print(f"[{lanlan_name}]- Phase 1 音乐候选去重命中，跳过: {track_name}")
                            continue
                        picked_track = candidate_track
                        picked_key = candidate_key
                        break
                    if picked_track is None:
                        print(f"[{lanlan_name}]- Phase 1 所有音乐候选均被衰减 skip，整体清空通道")
                        music_content = None
                    else:
                        # 选中非首条时，把 raw_data['data'] 砍到 picked 起始位置并重 format —
                        # 否则 music_topic 文本仍以被 skip 掉的首条为头条，与
                        # selected_music_link 的归因脱节，下游 _append_music_recommendations
                        # 也会把已 skip 的首条作为推荐项暴露给前端。
                        picked_idx = music_tracks.index(picked_track)
                        if picked_idx > 0:
                            raw = music_content.get('raw_data') or {}
                            raw_trimmed = {**raw, 'data': music_tracks[picked_idx:]}
                            new_topic = _format_music_content(raw_trimmed, proactive_lang)
                            if new_topic:
                                music_topic = new_topic
                                music_content['formatted_content'] = music_topic
                                music_content['raw_data'] = raw_trimmed
                        track_name = picked_track.get('name', '')
                        track_artist = picked_track.get('artist', '')
                        track_url = picked_track.get('url', '')
                        track_cover = picked_track.get('cover', '')
                        logger.debug(f"[{lanlan_name}]- Phase 1 音乐话题已添加 (topic_len={len(music_topic)})")
                        print(f"[{lanlan_name}]- Phase 1 音乐话题: {music_topic[:100]}")
                        selected_music_link = {
                            'title': track_name,
                            'artist': track_artist,
                            'url': track_url,
                            'cover': track_cover,
                            'source': '音乐推荐',
                            'type': 'music'
                        }
                        selected_music_topic_key = picked_key
                        phase1_topics.append(('music', music_topic))
                else:
                    # formatted_content 非空时 _format_music_content 必已输出至少一条
                    # 曲目，所以这里实际不可达；保留为防御兜底，并与上面 picked_track
                    # is None 路径对偶：没有可播曲目就不进 active_channels，守住
                    # "music ∈ active_channels ⟺ selected_music_link 非空" 这条不变量，
                    # 避免 Phase 2 出现音乐素材却无歌可投（发了 [MUSIC] 转译不出）。
                    logger.debug(f"[{lanlan_name}] Phase 1 音乐 formatted_content 非空但无曲目数据，跳过音乐通道")
                    music_content = None

        # ============================================================
        # 表情包话题组装（遍历候选 → 去重 → 限1张）
        # ============================================================
        if meme_content and meme_content.get('success') and meme_content.get('data'):
            meme_data = meme_content.get('data', [])
            if meme_data:
                for candidate_meme in meme_data:
                    meme_title = candidate_meme.get('title', '')
                    meme_url = candidate_meme.get('url', '')
                    if not meme_url:
                        continue  # 跳过无 URL 的候选
                    meme_source = candidate_meme.get('source', '表情包')
                    meme_topic_key = _source_hash(meme_url, meme_title)
                    if meme_topic_key and _should_skip_source(meme_topic_key):
                        logger.debug(f"[{lanlan_name}]- Phase 1 表情包候选去重命中，跳过: {meme_title[:30]}")
                        continue
                    if mgr.state.is_proactive_preempted():
                        return await _end_proactive(
                            JSONResponse(_proactive_preempted_json("phase1_pre_meme_moderation"))
                        )
                    moderation = await moderate_meme_image_url(meme_url, fail_closed=False)
                    if mgr.state.is_proactive_preempted():
                        return await _end_proactive(
                            JSONResponse(_proactive_preempted_json("phase1_post_meme_moderation"))
                        )
                    if not moderation.allowed:
                        logger.info(
                            "[%s]- Phase 1 meme candidate moderation blocked: reason=%s cached=%s url_hash=%s title=%s",
                            lanlan_name,
                            moderation.reason,
                            moderation.cached,
                            moderation.url_hash,
                            meme_title[:30],
                        )
                        await _record_source_used(
                            url=meme_url,
                            kind='image',
                            title=meme_title,
                        )
                        logger.info(
                            "[%s]- 已记录被 moderation 拦截的表情包 source 衰减历史: url_hash=%s",
                            lanlan_name,
                            meme_topic_key[:16],
                        )
                        continue
                    single_meme_topic = get_meme_topic_line(
                        proactive_lang,
                        keyword=meme_content.get('keyword', ''),
                        title=meme_title,
                        source=meme_source,
                    )
                    logger.debug(f"[{lanlan_name}]- Phase 1 表情包话题已添加 (限额1张): {single_meme_topic}")
                    phase1_topics.append(('meme', single_meme_topic))
                    selected_meme_link = {
                        'title': meme_title,
                        'url': meme_url,
                        'source': meme_source,
                        'type': candidate_meme.get('type', 'meme')
                    }
                    selected_meme_topic_key = meme_topic_key
                    logger.debug(f"[{lanlan_name}] 预选表情包话题: {meme_title[:30]}")
                    break
                else:
                    logger.debug(f"[{lanlan_name}]- Phase 1 未选出可用表情包候选，跳过表情包话题")
            else:
                logger.warning(f"[{lanlan_name}] Phase 1 表情包数据为空，跳过表情包话题")
        
        if not phase1_topics and not vision_content:
            if not _has_unfinished_thread:
                print(f"[{lanlan_name}] Phase 1 所有通道均无可用话题")
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "pass",
                    "reason_code": PROACTIVE_REASON_PASS_MODEL_PASS,
                    "message": "所有信息源筛选后均不值得搭话"
                }))
            print(f"[{lanlan_name}] Phase 1 无话题但有未收尾话题，进入 text-only 跟进 Phase 2")

        # Phase 1 preempt check：topic assembly 完，进入 Phase 2 前最后一次瞄
        if mgr.state.is_proactive_preempted():
            return await _end_proactive(JSONResponse(_proactive_preempted_json("phase1_pre_phase2")))
        
        # 收集各通道结果
        active_channels = [ch for ch, _ in phase1_topics]
        print(f"[{lanlan_name}] Phase 1 结果: phase1_topics={phase1_topics}, vision_content={'有' if vision_content else '无'}")
        web_topic = None
        music_topic = None
        for channel, topic in phase1_topics:
            if channel == 'web':
                web_topic = topic
            elif channel == 'music':
                music_topic = topic
        if vision_content:
            active_channels.append('vision')
        primary_channel = 'vision' if vision_content else (active_channels[0] if active_channels else 'unknown')
        print(f"[{lanlan_name}] Phase 1 可用通道: {active_channels}，主通道: {primary_channel}")
        
        # ================================================================
        # Phase 2: 结合人设 + 双通道信息 → 流式生成搭话
        # ⚠️ 二阶段一定要用 vision_model，在调用前使用最新截图。
        #    只有这样才能减少 vision_model 读屏幕的延迟。
        # ⚠️ 二阶段一定不要打开思考 (disable_thinking 必须为 True)，
        #    否则 vision_model + thinking 一定会超时。
        # ⚠️ 不重试、不改写。流式拦截到异常直接 abort，失败即 pass 等下一次。
        # 流程：tokens → TTS 即时生成 → 全文完成后一次性投递文本 → abort 时中断两端
        # ================================================================
        
        # 获取角色完整人设，替换模板变量
        character_prompt = lanlan_prompt_map.get(lanlan_name, '')
        if not character_prompt:
            logger.warning(f"[{lanlan_name}] 未找到角色人设，使用空字符串")
        character_prompt = character_prompt.replace('{LANLAN_NAME}', lanlan_name).replace('{MASTER_NAME}', master_name_current)
        
        # --- 向前端请求最新截图，替换 Phase 1 时拿到的旧截图 ---
        screenshot_b64_for_phase2 = ''
        if vision_content and has_vision_model:
            fresh_b64 = await mgr.request_fresh_screenshot(timeout=3.0)
            if fresh_b64:
                # 如果 request_fresh_screenshot 走了 WebSocket 路径，screenshot_response
                # 已经在 websocket_router 中更新了 mgr._avatar_position，这里用最新的位置叠加。
                # 如果走了 pyautogui 路径，overlay 已在 request_fresh_screenshot 内部完成。
                # 为安全起见：若 WS 路径返回的 fresh_b64 尚未叠加，在此补叠。
                av_pos = getattr(mgr, '_avatar_position', None) or avatar_position
                if av_pos and isinstance(av_pos, dict):
                    try:
                        from utils.screenshot_utils import overlay_avatar_annotation
                        fresh_b64 = await asyncio.to_thread(
                            overlay_avatar_annotation,
                            fresh_b64, av_pos, lanlan_name,
                            proactive_lang,
                        )
                    except Exception as ann_err:
                        logger.warning(f"[{lanlan_name}] Phase 2 avatar annotation failed: {ann_err}")
                screenshot_b64_for_phase2 = fresh_b64
                print(f"[{lanlan_name}] Phase 2 获取到最新截图 ({len(fresh_b64)//1024}KB)")
            else:
                screenshot_b64_for_phase2 = vision_content.get('screenshot_b64', '')
                if screenshot_b64_for_phase2:
                    print(f"[{lanlan_name}] Phase 2 刷新截图失败，退回使用 Phase 1 旧截图")
        
        # 构建屏幕内容段（vision 通道）
        screen_section = ""
        if screenshot_b64_for_phase2:
            sl = get_screen_section_header(master_name_current, proactive_lang)
            sf = get_screen_section_footer(master_name_current, proactive_lang)
            vision_window = vision_content.get('window_title', '') if vision_content else ''
            window_line = _loc(SCREEN_WINDOW_TITLE, proactive_lang).format(window=vision_window) if vision_window else ""
            hint = get_screen_img_hint(master_name_current, proactive_lang)
            screen_section = f"{sl}\n{window_line}{hint}\n{sf}"
            print(f"[{lanlan_name}] Phase 2 将使用 vision 模型直接看截图")
        else:
            print(f"[{lanlan_name}] Phase 2 无截图或无 vision 模型，跳过屏幕分析")
        
        # 构建网络话题段（web 通道）
        external_section = ""
        if web_topic:
            el = _loc(EXTERNAL_TOPIC_HEADER, proactive_lang)
            ef = _loc(EXTERNAL_TOPIC_FOOTER, proactive_lang)
            external_section = f"{el}\n{web_topic}\n{ef}"
        
        music_section = ""
        # gate 钉在 selected_music_link（本轮真选中、可播的曲目）而非 music_topic：
        # 保证 Phase 2 prompt 一旦出现音乐素材 / output-format 列出 [MUSIC]，下游必有
        # 歌可投递，不会"发了 [MUSIC] 却转译不出"。selected_music_link 非空时
        # music_topic 必非空（同生于 Phase 1 选曲）。正在放歌 / 冷却期时
        # music_content / selected_music_link 已在上游清空，此分支自然不命中。
        if selected_music_link and not is_playing_music and not music_cooldown:
            # 【优化】使用独立的标识符，防止模型将音乐素材误认为普通的外部 WEB 话题
            msh = _loc(MUSIC_SECTION_HEADER, proactive_lang)
            msf = _loc(MUSIC_SECTION_FOOTER, proactive_lang)
            music_section = f"{msh}\n{music_topic}\n{msf}"
        elif is_playing_music:
            print(f"[{lanlan_name}] 正在播放音乐，已屏蔽音乐推荐素材（仅保留 playing_hint）")
            music_section = ""
        
        # 构建表情包段（meme 通道）
        meme_section = ""
        meme_topic = None
        for channel, topic in phase1_topics:
            if channel == 'meme':
                meme_topic = topic
                break
        if meme_topic:
            meh = _loc(MEME_SECTION_HEADER, proactive_lang)
            mef = _loc(MEME_SECTION_FOOTER, proactive_lang)
            meme_section = f"{meh}\n{meme_topic}\n{mef}"
        
        source_instruction, output_format_section = get_proactive_format_sections(
            has_screen=bool(screen_section),
            has_web=bool(external_section),
            has_music=bool(music_section),
            has_meme=bool(meme_section),
            lang=proactive_lang,
        )
        # 本轮是否启用"来源标签系统"：有 web/music/meme 副作用通道时，
        # get_proactive_format_sections 用 _of_header（要求第一行写 [TAG]）；三者全无
        # 时用 _of_none（明确要求纯文本、无 tag，下游靠 source_tag='CHAT' 兜底投递）。
        # 无 tag gate 只在前者生效，否则会把 _of_none 模式的合法纯文本搭话误判为
        # 格式泄漏 drop（Codex P1）。
        _expects_source_tag = bool(external_section) or bool(music_section) or bool(meme_section)
        music_playing_hint = ""
        if is_playing_music and current_track:
            track_name = current_track.get('name') or get_proactive_music_unknown_track_name(proactive_lang)
            music_playing_hint = get_proactive_music_playing_hint(track_name, master_name_current, proactive_lang)

        # 把活动快照渲染成 prompt 段。snapshot 缺失时退化为空串——decision frame
        # 里的 A) 看「用户当前状态」分支会自动走到"其它状态：所有切入点都可用"。
        #
        # 重要：渲染前重拉一次 tracker enrichment 缓存（activity_scores /
        # activity_guess / open_threads）。kickoff_open_threads_compute 是在
        # Phase 1 起点 fire-and-forget 跑的，结果会在 Phase 1 进行中陆续落到
        # 缓存里——早期捕获的 activity_snapshot 看不到这些更新。专门并行起来
        # 就是为了本轮就用。决策性字段（state / propensity / propensity_reasons /
        # unfinished_thread）仍取自早期 snapshot，避免 Phase 1 中途 state 变化
        # 导致 gating 决策（restricted_screen_only 收紧 enabled_modes 等）和最终
        # prompt 不一致。
        # Freshest enrichment for the proactive prompt — Phase 1 (source fetch +
        # memory + LLM) just elapsed, so activity scores / open threads moved on.
        # Falls back to the entry snapshot if the refresh fails / is unavailable.
        # (The idle Focus decision no longer consumes a snapshot — it is a pure
        # charge cooldown — so this block only feeds the prompt now.)
        if activity_snapshot is not None:
            from dataclasses import replace as _dc_replace
            from main_logic.activity import format_activity_state_section
            try:
                fresh_enrich = await mgr._activity_tracker.get_snapshot()
                # restricted_screen_only deliberately strips semantic open_threads
                # so gaming / focused-work prompts stay screen-only — render the
                # prompt with that filtered set.
                _filtered_open_threads = _open_threads_for_activity_state(
                    activity_snapshot,
                    fresh_enrich.open_threads,
                )
                display_snap = _dc_replace(
                    activity_snapshot,
                    activity_scores=fresh_enrich.activity_scores,
                    activity_guess=fresh_enrich.activity_guess,
                    open_threads=_filtered_open_threads,
                )
            except Exception as _enrich_err:
                logger.debug(f"[{lanlan_name}] fresh enrichment fetch failed: {_enrich_err}")
                display_snap = activity_snapshot
            state_section = format_activity_state_section(display_snap, proactive_lang)
        else:
            display_snap = None
            state_section = ''

        # 静动分离：generate_prompt 作为静态 SystemMessage（可被缓存），
        # 追加的音乐/表情包指令作为动态上下文注入 HumanMessage
        # 使用 enriched_memory_context（含回忆线索）而非原始 memory_context。
        # open_threads 保持在上方 activity state section，不混进 memory_context。
        phase2_memory_context = memory_context
        if followup_topics_prompt:
            phase2_memory_context = memory_context + "\n" + followup_topics_prompt

        generate_prompt = get_proactive_generate_prompt(
            proactive_lang, music_playing_hint,
            has_music=bool(music_section), has_meme=bool(meme_section),
            master_name=master_name_current,
        ).format(
            character_prompt=character_prompt,
            inner_thoughts=inner_thoughts,
            state_section=state_section,
            memory_context=phase2_memory_context,
            recent_chats_section=proactive_chat_history_prompt,
            screen_section=screen_section,
            external_section=external_section,
            music_section=music_section,
            meme_section=meme_section,
            master_name=master_name_current,
            source_instruction=source_instruction,
            output_format_section=output_format_section,
        )
        dynamic_context_for_phase2 = ""
        # 同 music_section：[MUSIC] tag 强制指令只在真有可播曲目时注入。
        if selected_music_link:
            dynamic_context_for_phase2 += PROACTIVE_MUSIC_TAG_INSTRUCTIONS.get(
                proactive_lang,
                PROACTIVE_MUSIC_TAG_INSTRUCTIONS.get('en', PROACTIVE_MUSIC_TAG_INSTRUCTIONS['zh']),
            )
            raw_data = music_content.get('raw_data', {}) if music_content else {}
            if raw_data.get('best_match', {}).get('status') == 'fuzzy':
                dynamic_context_for_phase2 += get_proactive_music_failsafe_hint(master_name_current, proactive_lang)

        if is_playing_music:
            dynamic_context_for_phase2 += get_proactive_music_strict_constraint(proactive_lang)
        # music_cooldown 时不再注入 strict_constraint —— 此时 music 通道已被前端/后端
        # 完全剔除，不应向模型暴露任何音乐相关指令，以免干扰其他 source 的选择。
        print(f"[{lanlan_name}] Phase 2 prompt 长度: {len(generate_prompt)}, 动态上下文: {len(dynamic_context_for_phase2)} 字符")

        # Phase 1 preempt check (final)：request_fresh_screenshot 最多 await 3s，
        # 是 prepare_proactive_delivery 之前唯一剩下的可打断窗口。若此处用户已
        # 接管，继续走 prepare 会让其内部的 `current_speech_id = uuid4()` 覆盖
        # 用户轮次的 sid —— 即使 SM 的 PROACTIVE_CLAIM 在 _preempted=True 时不
        # 回写 proactive_sid，mgr.current_speech_id 已经被物理换掉，用户的
        # 回复 TTS 会被错贴上一个陌生 sid。
        if mgr.state.is_proactive_preempted():
            return await _end_proactive(JSONResponse(_proactive_preempted_json("phase1_pre_prepare")))

        # --- 前置检查：用户是否空闲、WebSocket 是否在线、session 是否可用 ---
        if not await mgr.prepare_proactive_delivery(min_idle_secs=10.0):
            return await _end_proactive(JSONResponse({
                "success": True,
                "action": "pass",
                "reason_code": PROACTIVE_REASON_PASS_ACTIVITY_BUSY,
                "message": "主动搭话条件未满足（用户近期活跃或语音会话正在进行）"
            }))

        # 记录本轮主动搭话起始的 speech_id；abort 时若该 id 已变，说明用户已打断并接管，
        # 此时再调 handle_new_message() 会把用户正常回复的 TTS 也一起清掉。
        # prepare_proactive_delivery 已经 fire(PROACTIVE_CLAIM, sid=...)；这里把
        # 状态机翻到 PHASE2，后续 astream 循环的抢占检查基于此阶段。
        proactive_sid = mgr.current_speech_id
        await mgr.state.fire(_SE.PROACTIVE_PHASE2)

        # Path B (idle) Focus 凝神：this round is now committed to speaking
        # (PHASE2 fired). Read-only: does this proactive reply run thinking-on?
        # (= the session is already in Focus, inline-driven). A proactive turn
        # never raises the charge; the charge cooldown happens after the turn in
        # _end_proactive (it needs to know whether we actually spoke). Dominates
        # all three Phase-2 generate sites below (main stream / format-fix regen
        # / BM25 anti-repeat regen).
        _focus_phase2_thinking = mgr._focus_idle_thinking()
        # Mark that this turn reached the Phase-2 idle Focus decision and pin the
        # focus state it observed (episode id + turn count) — _end_proactive
        # applies the cooldown only for such turns, and only if still in this
        # exact episode/turn (race guard: a no-op if inline moved it since).
        _focus_phase2_reached = True
        _focus_phase2_snap = mgr.state.snapshot()
        _focus_episode_token = _focus_phase2_snap.get("focus_episode_id")
        _focus_turn_token = _focus_phase2_snap.get("focus_turn_count")

        # --- 构建 LLM + messages (static/dynamic 分离) ---
        phase2_use_vision = bool(screenshot_b64_for_phase2 and has_vision_model)
        # Vision guard: a vision model + thinking reliably times out (see the
        # Phase-2 注释 above), so Focus thinking-on is suppressed whenever this
        # round feeds a screenshot. Single source of truth for all three
        # Phase-2 generate sites.
        phase2_disable_thinking = phase2_use_vision or not _focus_phase2_thinking

        begin_text = _loc(BEGIN_GENERATE, proactive_lang)
        human_text = f"{dynamic_context_for_phase2}\n\n{begin_text}" if dynamic_context_for_phase2 else begin_text
        if phase2_use_vision:
            human_content = [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64_for_phase2}"}},
                {"type": "text", "text": human_text},
            ]
        else:
            human_content = human_text
        messages = [SystemMessage(content=generate_prompt), HumanMessage(content=human_content)]

        actual_model = (vision_model_name if phase2_use_vision else conversation_model)
        print(f"\n{'='*60}\n[PROACTIVE-DEBUG] Phase 2 STREAM: model={actual_model} | vision={phase2_use_vision} | img={'yes' if phase2_use_vision else 'no'}\n{'='*60}\n{generate_prompt}\n{'='*60}\n")

        # --- 流式调用 + 在线拦截 ---
        from utils.token_tracker import set_call_type
        set_call_type("proactive")
        buffer = ""
        tag_parsed = False
        source_tag = ""
        full_text = ""
        pipe_count = 0
        aborted = False
        abort_reason_code: str | None = None
        # 滚动尾部缓冲区：保留最近 5 个字符以检测跨 chunk 的 "[PASS]"（长度 6）
        pass_probe = ""
        _PASS_PROBE_LEN = 5  # len("[PASS]") - 1

        def _abort(reason_code: str) -> None:
            nonlocal aborted, abort_reason_code
            aborted = True
            # User takeover is the most important telemetry signal. If a later
            # cleanup path also notices empty/invalid output, keep the takeover
            # reason so the final pass is classified as delivery preemption.
            if (
                abort_reason_code is None
                or reason_code == PROACTIVE_REASON_DELIVERY_PREEMPTED
            ):
                abort_reason_code = reason_code

        async def _emit_safe(text: str) -> bool:
            """Send to TTS after passing the fence/length checks. Returns True when we should abort."""
            nonlocal pipe_count, full_text
            if not text:
                return False
            # 状态机 preempt check：O(1) 读 sticky flag + sid 比较。用户抢占
            # （handle_new_message 或 text stream_text 入口）会 fire USER_INPUT，
            # 在 PHASE2 阶段 sticky 把 _preempted 翻到 True；同时 current_speech_id
            # 被轮换，proactive_sid != 新 sid 兜底覆盖竞态窗口。
            # TTS 不在流式阶段输出：先缓冲全文，等相似度/数据级硬拦截都通过后
            # 再一次性 feed。否则重复文本会在 guard 命中前已经被用户听到。
            if mgr.state.is_proactive_preempted(proactive_sid):
                print(f"[{lanlan_name}] Phase 2 检测到用户接管（state 抢占），abort")
                _abort(PROACTIVE_REASON_DELIVERY_PREEMPTED)
                return True
            for ch in text:
                if ch in ('|', '｜'):
                    pipe_count += 1
                    if pipe_count >= 2:
                        print(f"[{lanlan_name}] Phase 2 fence 触发 (pipe_count={pipe_count})，abort")
                        _abort(PROACTIVE_REASON_PASS_GENERATION_EMPTY)
                        return True
            # sync count_tokens — see PHASE2_OUTPUT_MAX_TOKENS docstring
            n_tokens = count_tokens(full_text + text)
            if n_tokens > PHASE2_OUTPUT_MAX_TOKENS:
                print(f"[{lanlan_name}] Phase 2 长度超限 ({n_tokens} > {PHASE2_OUTPUT_MAX_TOKENS} tokens)，abort")
                _abort(PROACTIVE_REASON_PASS_GENERATION_EMPTY)
                return True
            full_text += text
            return False
        
        # Focus 凝神: idle/proactive counterpart to OmniOfflineClient's inline
        # stripper — text-mode Phase-2 also streams thinking-on (disable_thinking
        # False). Strip leaked <think> CoT before it reaches TTS/UI for leak-prone
        # models (qwen3.5/3.6/3.7 hybrids). Symmetric with the inline path; None
        # (no wrapping) for clean providers or thinking-off turns → zero impact.
        _p2_strip = (
            ThinkingStreamStripper()
            if (not phase2_disable_thinking) and leaks_thinking_in_content(conversation_model)
            else None
        )
        try:
            async with asyncio.timeout(25.0):
                # 使用 async with 确保 ChatOpenAI 正确关闭
                async with (await _make_llm(temperature=1.0,
                                            max_completion_tokens=PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
                                            use_vision=phase2_use_vision,
                                            disable_thinking=phase2_disable_thinking)) as llm:
                    async for chunk in llm.astream(messages):
                        # Phase 2 preempt check：每 chunk 顶端做 O(1) 状态机读，
                        # 用户抢占立刻跳出；_emit_safe 里还有一次保险。
                        if mgr.state.is_proactive_preempted(proactive_sid):
                            print(f"[{lanlan_name}] Phase 2 astream chunk 前检测到抢占，abort")
                            _abort(PROACTIVE_REASON_DELIVERY_PREEMPTED)
                            break
                        content = chunk.content if hasattr(chunk, 'content') else ''
                        if _p2_strip is not None and content:
                            # Holds CoT until the first </think>; returns "" while
                            # buffering so the skip below drops the held chunk.
                            content = _p2_strip.feed(content)
                        if not content:
                            continue

                        if not tag_parsed:
                            buffer += content
                            # 缓冲前 ~80 字符，解析 "主动搭话" 前缀和来源标签
                            if len(buffer) < 80 and '\n' not in buffer[min(len(buffer)-1, 10):]:
                                continue
                            # 清理 "主动搭话" 前缀
                            cleaned = buffer
                            m = re.search(r'主动搭话\s*\n', cleaned)
                            if m:
                                cleaned = cleaned[m.end():]
                            # 解析 [PASS] / [CHAT] / [WEB] / [MUSIC] / [MEME]
                            # 先 lstrip：模型偶尔先吐换行/空格再吐 [CHAT]，不去前导空白
                            # 会让 ^\[ 匹配失败、source_tag 误留空被当成无 tag（Codex P2）。
                            cleaned = cleaned.lstrip()
                            tag_match = re.match(r'^\[(CHAT|WEB|PASS|MUSIC|MEME)\]\s*', cleaned, re.IGNORECASE)
                            if tag_match:
                                source_tag = tag_match.group(1).upper()
                                cleaned = cleaned[tag_match.end():]
                            else:
                                cleaned, _leak_tag = _strip_proactive_screen_tag_leak(cleaned)
                                if _leak_tag:
                                    source_tag = _leak_tag
                            tag_parsed = True

                            # 模型本该输出带括号的 [PASS]，但偶尔吐裸 PASS：tag 正则
                            # 认不出 → source_tag 空、'[PASS]' 也不在 cleaned 里。再补
                            # 一道整段哨兵判定（fullmatch，方括号可选），裸 PASS 与
                            # [PASS] 一视同仁 abort；fullmatch 不会误伤正文里的 "pass"。
                            if (source_tag == 'PASS' or '[PASS]' in cleaned.upper()
                                    or _text_is_pass_sentinel(cleaned)):
                                print(f"[{lanlan_name}] Phase 2 流式检测到 PASS，abort")
                                _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
                                break
                            
                            # 缓冲中剩余的文本经由 pass_probe 逻辑输出
                            if cleaned.strip():
                                combined = pass_probe + cleaned
                                if '[PASS]' in combined.upper():
                                    print(f"[{lanlan_name}] Phase 2 流式检测到 [PASS]，abort")
                                    _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
                                    break
                                safe_text = combined[:-_PASS_PROBE_LEN] if len(combined) > _PASS_PROBE_LEN else ''
                                pass_probe = combined[-_PASS_PROBE_LEN:] if len(combined) >= _PASS_PROBE_LEN else combined
                                if await _emit_safe(safe_text):
                                    break
                            continue
                        
                        # --- 在线拦截: [PASS]（含跨 chunk 检测）---
                        combined = pass_probe + content
                        if '[PASS]' in combined.upper():
                            print(f"[{lanlan_name}] Phase 2 流式检测到内嵌 [PASS]，abort")
                            _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
                            break
                        # 将本次 chunk 的尾部保留到 pass_probe，可安全输出的部分为去掉尾部的前段
                        safe_text = combined[:-_PASS_PROBE_LEN] if len(combined) > _PASS_PROBE_LEN else ''
                        pass_probe = combined[-_PASS_PROBE_LEN:] if len(combined) >= _PASS_PROBE_LEN else combined
                        
                        if safe_text and await _emit_safe(safe_text):
                            break
        
        except (asyncio.TimeoutError, Exception) as e:
            logger.warning(f"[{lanlan_name}] Phase 2 流式调用异常: {type(e).__name__}: {e}")
            _abort(PROACTIVE_REASON_PASS_GENERATION_EMPTY)
        
        # --- 流结束后：flush pass_probe 残留 ---
        if pass_probe and not aborted:
            if '[PASS]' in pass_probe.upper():
                _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
            else:
                await _emit_safe(pass_probe)
        pass_probe = ""

        # Focus: flush the stripper's held answer. Non-empty only when no
        # </think> ever arrived (the model didn't think this turn) — which means
        # the tag was never parsed and nothing flowed through pass_probe, so feed
        # it into `buffer` and let the unparsed-buffer block below tag-parse +
        # emit it (symmetric with the inline path's prefix_buffer flush).
        if _p2_strip is not None and not aborted:
            _p2_residual = _p2_strip.flush()
            if _p2_residual:
                buffer += _p2_residual

        # --- 流结束后 buffer 未 flush 的兜底处理 ---
        if not tag_parsed and buffer and not aborted:
            cleaned = buffer
            m = re.search(r'主动搭话\s*\n', cleaned)
            if m:
                cleaned = cleaned[m.end():]
            cleaned = cleaned.lstrip()  # 同上：去前导空白再匹配 tag（Codex P2）
            tag_match = re.match(r'^\[(CHAT|WEB|PASS|MUSIC|MEME)\]\s*', cleaned, re.IGNORECASE)
            if tag_match:
                source_tag = tag_match.group(1).upper()
                cleaned = cleaned[tag_match.end():]
            else:
                cleaned, _leak_tag = _strip_proactive_screen_tag_leak(cleaned)
                if _leak_tag:
                    source_tag = _leak_tag
            # 短 bare-PASS 回复（如整段就 "PASS"，4 字 < 80 无换行）流式期一直
            # 在 buffer 里 continue、tag_parsed 始终 False，最终落到这里兜底。
            # 同样补整段哨兵判定，裸 PASS 与 [PASS] 一视同仁 abort。
            if (source_tag == 'PASS' or '[PASS]' in cleaned.upper()
                    or _text_is_pass_sentinel(cleaned)):
                _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
            elif cleaned.strip():
                await _emit_safe(cleaned)
        
        # 没有解析到合法来源标签（[CHAT]/[WEB]/[MUSIC]/[MEME]）→ 输出不符合格式。
        # 弱模型（free-model）常把人设里的 Format / 约束块当正文吐出来——线上见过
        # "No Markdown: Yes."、"* No stage directions/parentheses"、"完全不同的角度或主题"
        # 这类脚手架泄漏。合法搭话必然以 tag 起头，缺 tag 即判格式泄漏，drop 整轮，
        # 不要把脚手架念给博士听。（TTS 在本函数后段才真正投递，此处 abort 安全。）
        if not aborted and full_text.strip() and not source_tag and _expects_source_tag:
            # 没解析到合法来源标签——多半是模型把人设 Format/约束块当正文吐了出来。
            # （仅在本轮启用 tag 系统时才判泄漏；_of_none 纯文本模式无 tag 是合法的，
            #  不进此分支，留给后面的 source_tag='CHAT' 兜底正常投递。）
            # 不直接 drop，先给一次"格式纠正"regen 自救：重建 Human turn（fix 指令 +
            # 原 human_text，末尾仍是 BEGIN 触发句），ainvoke 重跑一次再解析 tag。
            # 解析到合法非 PASS tag → 用自救结果接回主流程（下游 is_duplicate / BM25
            # 照常生效）；仍无 tag / [PASS] / 空 → 才判格式泄漏 drop。preempt 时放弃。
            print(f"[{lanlan_name}] Phase 2 输出无合法来源标签，尝试格式自救 regen")
            if mgr.state.is_proactive_preempted(proactive_sid):
                _abort(PROACTIVE_REASON_DELIVERY_PREEMPTED)
            else:
                _fix_human_text = f"{render_format_fix_instruction(proactive_lang, master_name_current)}\n\n{human_text}"
                if phase2_use_vision:
                    _fix_human_content = [
                        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64_for_phase2}"}},
                        {"type": "text", "text": _fix_human_text},
                    ]
                else:
                    _fix_human_content = _fix_human_text
                _fix_text = ""
                try:
                    async with asyncio.timeout(20.0):
                        async with (await _make_llm(
                            temperature=1.0,
                            max_completion_tokens=PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
                            use_vision=phase2_use_vision,
                            disable_thinking=phase2_disable_thinking,
                        )) as _fix_llm:
                            _fix_resp = await _fix_llm.ainvoke(
                                [messages[0], HumanMessage(content=_fix_human_content)]
                            )
                            _fix_text = (_fix_resp.content if hasattr(_fix_resp, "content") else "") or ""
                except Exception as _fix_exc:
                    logger.warning("[%s] Phase 2 格式自救 regen 失败: %s", lanlan_name, _fix_exc)
                    _fix_text = ""
                _fc = (_fix_text or "").strip()
                _fm = re.search(r"主动搭话\s*\n", _fc)
                if _fm:
                    _fc = _fc[_fm.end():]
                _fc = _fc.lstrip()
                _fix_tag = ""
                _ftm = re.match(r"^\[(CHAT|WEB|PASS|MUSIC|MEME)\]\s*", _fc, re.IGNORECASE)
                if _ftm:
                    _fix_tag = _ftm.group(1).upper()
                    _fc = _fc[_ftm.end():]
                else:
                    _fc, _leak_tag = _strip_proactive_screen_tag_leak(_fc)
                    if _leak_tag:
                        _fix_tag = _leak_tag
                if _fix_tag and _fix_tag != "PASS" and _fc.strip() and "[PASS]" not in _fc.upper():
                    source_tag = _fix_tag
                    full_text = _fc.strip()
                    print(f"[{lanlan_name}] Phase 2 格式自救成功 tag={source_tag}")
                else:
                    print(f"[{lanlan_name}] Phase 2 格式自救仍无合法 tag，drop")
                    if (
                        _fix_tag == "PASS"
                        or "[PASS]" in _fc.upper()
                        or _text_is_pass_sentinel(_fc)
                    ):
                        _abort(PROACTIVE_REASON_PASS_MODEL_PASS)
                    else:
                        _abort(PROACTIVE_REASON_PASS_GENERATION_EMPTY)

        # --- 结果处理 ---
        # buffer 是流前 ~80 字符的原始累积（含 [TAG]\n 前缀和正文头部），
        # full_text 是去标签后真正投递给 TTS / send_lanlan_response 的内容。
        # 两者拼起来打印会让正文头部"复读"一遍，看着像 bug 实际不是。
        # 调试只需要 tag + 实际投递文本即可。
        print(f"\n[PROACTIVE-DEBUG] Phase 2 STREAM output (aborted={aborted}, tag={source_tag}): {full_text[:300]}\n")
        if aborted or not full_text.strip():
            final_abort_reason_code = abort_reason_code or PROACTIVE_REASON_PASS_GENERATION_EMPTY
            # 只有当用户没接管时才调 handle_new_message 清 TTS —— 否则会把
            # 用户正常回复的 TTS 也清掉（PR #862 修的 bug）。状态机的
            # is_proactive_preempted 是权威信号，sid 比较作为最后一道兜底。
            if not mgr.state.is_proactive_preempted(proactive_sid):
                await mgr.handle_new_message()
                logger.debug(f"[{lanlan_name}] Phase 2 abort，已中断 TTS + 前端音频")
            else:
                logger.info(f"[{lanlan_name}] Phase 2 abort 但用户已接管 (state preempted)，跳过 TTS 清理避免误伤正常回复")
            return await _end_proactive(JSONResponse({
                "success": True,
                "action": "pass",
                "reason_code": final_abort_reason_code,
                "message": "Phase 2 流式输出被拦截或为空"
            }))
        
        full_text, _leak_tag = _strip_proactive_screen_tag_leak(full_text)
        if _leak_tag and not source_tag:
            source_tag = _leak_tag
        response_text = full_text.strip()
        # 剥掉模型偶尔把活动状态里的「口吻 / 回忆线索」等内部引导标签当成首行小标题
        # 念出来的泄漏（前端 realistic 模式会按换行切成单独一个气泡）。必须在下方
        # 重复度 / BM25 防复读判定**之前**剥：否则被泄漏标签做前缀的复读句会因前缀
        # 稀释相似度而绕过 dedup。这些标签纯脚手架，绝不该进 TTS / 历史。
        response_text = _strip_proactive_intent_label_leak(response_text)
        # 不要把 proactive 原文写进 logger（会进日志文件 / 遥测）；只记元数据。
        # 完整原文通过 print 给开发者本地查看。
        logger.debug(f"[{lanlan_name}] Phase 2 流式完成 (vision={phase2_use_vision}, len={len(response_text)} chars)")
        print(f"\n[PROACTIVE-DEBUG] Phase 2 STREAM output: {response_text[:200]}...\n")

        # 素材推送类 channel（MUSIC/MEME）的复读按"素材本身"去重而非台词：本轮
        # 素材（曲目 / 搜索关键词）与近期不雷同时，台词级硬拦截（字面相似度 +
        # 下面的 BM25 regen/drop）一律豁免，免得模板化 intro 被误判为复读、把自
        # 发推歌/推图压到极低频。素材雷同（反复推同一曲目 / 同一关键词）才回落
        # 到正常台词判定。一次算清，下面两道门共用。
        #
        # 归类按"真实投递 channel"而非模型原始 source_tag——gate 在
        # build_proactive_response 之前，用 Phase-1 已定的 selected_*/active_channels
        # 预测最终投递（Codex P2）：
        # - music-only 且已选中曲目 → 无论模型出 [CHAT] 还是 [MUSIC]，下面的
        #   should_try_music_fallback 都会挂上曲目，本轮等于一次音乐投递，fresh
        #   曲目不该被 CHAT 文案的字面相似度 / BM25 连带 drop/regen。
        # - 模型出 [MEME] 但没选中表情包（selected_meme_link 为空）→ 最终
        #   build_proactive_response 回退 web/vision/plain、meme 没真发出，按非豁免
        #   走正常台词判定（不能凭模型 tag 就豁免）。
        _music_only_pending = (
            'music' in active_channels and selected_music_link is not None
            and not is_playing_music and not music_cooldown
            and not any(ch in ('vision', 'web', 'meme') for ch in active_channels)
        )
        if _music_only_pending and source_tag != 'MUSIC':
            _dedup_tag = 'MUSIC'
        elif source_tag == 'MEME' and selected_meme_link is None:
            _dedup_tag = 'CHAT'
        else:
            _dedup_tag = source_tag
        _material_key = _proactive_material_key(_dedup_tag, selected_music_link, meme_content)
        _exempt_text_dedup = (
            _dedup_tag in ANTI_REPEAT_EXEMPT_SOURCE_TAGS
            and not _is_recent_proactive_material(lanlan_name, _dedup_tag, _material_key)
        )
        if _exempt_text_dedup:
            logger.info(
                "[%s] proactive text-dedup exempt: tag=%s (model_tag=%s) material=%r (fresh material, skip similarity+BM25)",
                lanlan_name, _dedup_tag, source_tag, _material_key or "(none)",
            )

        is_duplicate, similarity_score = (False, 0.0)
        if not _exempt_text_dedup:
            is_duplicate, similarity_score = _is_similar_to_recent_proactive_chat(lanlan_name, response_text)
        if is_duplicate:
            logger.info(
                "[%s] proactive repeat guard blocked Phase 2 output (similarity=%.3f threshold=%.2f)",
                lanlan_name, similarity_score, _PROACTIVE_SIMILARITY_THRESHOLD,
            )
            print(
                f"[{lanlan_name}] 主动搭话重复度过高，已拦截 "
                f"(similarity={similarity_score:.3f}, threshold={_PROACTIVE_SIMILARITY_THRESHOLD:.2f})"
            )
            if not mgr.state.is_proactive_preempted(proactive_sid):
                await mgr.handle_new_message()
            else:
                logger.info("[%s] repeat guard hit but user already took over; skip TTS cleanup", lanlan_name)
            return await _end_proactive(JSONResponse({
                "success": True,
                "action": "pass",
                "reason_code": PROACTIVE_REASON_PASS_DUPLICATE,
                "message": "主动搭话重复度过高，已拦截",
                "similarity": similarity_score,
                "threshold": _PROACTIVE_SIMILARITY_THRESHOLD,
            }))

        # ── BM25 防复读硬拦截（regen / drop）─────────────────────────
        # 上面的 ``_is_similar_to_recent_proactive_chat`` 是字面相似度，只能抓
        # "几乎一字不差的复读"。BM25 走 ngram + IDF，能命中"换种说法但还在同
        # topic 上打转"——high-IDF 的 unique topic 词在最近 5 条里反复出现就
        # 触发。命中 REGEN 阈值给 LLM 一次纠正机会（ainvoke 单 shot，注入
        # avoidance 指令）；纠正后仍 >= DROP 则放弃本次投递。
        # corpus 在 ``mgr.finish_proactive_delivery`` 里写入；首次调用 / 新角色
        # 时 corpus 为空，score_draft 直接返回 0，整段无副作用。
        # 常量 + render helper 走模块顶部 import（``ANTI_REPEAT_*`` /
        # ``PROACTIVE_PHASE2_GENERATE_MAX_TOKENS`` / ``render_regen_avoid_instruction``）；
        # 这里 try 仅包 corpus 单例与评分本身——若把常量 import 也塞进 try，
        # except 后下面的 ``>= ANTI_REPEAT_DROP_THRESHOLD`` 会 NameError（codex P1）。
        # 素材推送类 channel（推歌/推图）的开场白天生模板化、台词长一个样而素材
        # （曲目 / 搜索关键词）却不同，用台词 BM25 判复读属于天生误杀（博士连点几
        # 首后 FG 窗被音乐 intro 占满，分数爆表，后续自发推歌全被 drop → "放音乐
        # 频率极低"）。本轮素材与近期不雷同时（_exempt_text_dedup，已在上方字面
        # 相似度门一并算好）跳过整段评分 + regen/drop；录入 corpus 时也豁免（见
        # finish_proactive_delivery），免得模板化 intro 污染 FG 窗。素材雷同时
        # 回落到正常台词 BM25（台词没雷同仍可发）。
        if _exempt_text_dedup:
            _bm25_total, _bm25_terms = 0.0, {}
            _ar_corpus = None
        else:
            try:
                from memory.anti_repeat import get_anti_repeat_corpus
                _ar_corpus = get_anti_repeat_corpus()
                _bm25_total, _bm25_terms = _ar_corpus.score_draft(lanlan_name, response_text)
            except Exception as _ar_exc:  # pragma: no cover - defensive
                logger.debug("[AntiRepeat] BM25 score skipped: %s", _ar_exc)
                _bm25_total, _bm25_terms = 0.0, {}
                _ar_corpus = None

        # ANTI_REPEAT_DROP_THRESHOLD 仅在 regen 之后才生效：初稿超 DROP 也得
        # 给 LLM 一次纠正机会，跑完再用同阈值二判。之前的版本初稿 ≥ DROP
        # 直接 drop 把潜在可救的输出短路掉，与设计文档"regen then drop"相违
        # （codex P2）。代价是一次 ainvoke，比静默 drop 整轮投递有价值。
        if _bm25_total >= ANTI_REPEAT_REGEN_THRESHOLD:
            # 记下进入 regen 前的初稿 source_tag，下面在改 tag 后判定是否要撤销
            # 原 music 候选状态（CodeRabbit Major：MUSIC → CHAT regen 后，若不清
            # selected_music_link / music_content，should_try_music_fallback 仍
            # 会把刚避开的复读话题对应曲目塞回 source_links）。
            _initial_source_tag = source_tag
            avoid_terms = list(_bm25_terms.keys())[:ANTI_REPEAT_INJECT_TOP_K]
            logger.info(
                "[%s] proactive BM25 regen (score=%.2f threshold=%.2f avoid=%s)",
                lanlan_name, _bm25_total, ANTI_REPEAT_REGEN_THRESHOLD, avoid_terms,
            )
            print(
                f"[{lanlan_name}] 主动搭话 BM25 触发 regen "
                f"(score={_bm25_total:.2f} >= {ANTI_REPEAT_REGEN_THRESHOLD}, 避开={avoid_terms})"
            )
            avoid_msg = render_regen_avoid_instruction(
                avoid_terms, proactive_lang, master_name_current,
            )
            # 不再把 avoid 指令作为独立的最后一条 HumanMessage 追加在 12.5k 末尾
            # （弱模型容易把这条 meta 指令的原文/脚手架当正文吐出来）。改为**重建
            # 同一个 Human turn**：avoid 约束在前，后接原始 human_text。human_text 本身
            # = dynamic_context_for_phase2 + BEGIN 触发句，所以一来保留了音乐 tag、
            # 模糊匹配披露、"正在放歌时禁止再推歌"等运行时约束（否则 regen 可能回出被
            # 禁止的内容，Codex P1 / CodeRabbit），二来它仍以 BEGIN 句结尾，模型看到的
            # 最后一句还是中性的"请开始"而非可照抄的指令文本。System 段原样复用；vision 图保留。
            regen_human_text = f"{avoid_msg}\n\n{human_text}"
            if phase2_use_vision:
                regen_human_content = [
                    {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{screenshot_b64_for_phase2}"}},
                    {"type": "text", "text": regen_human_text},
                ]
            else:
                regen_human_content = regen_human_text
            regen_messages = [messages[0], HumanMessage(content=regen_human_content)]
            regen_text = ""
            # 进入 regen 前再读一次 sticky preempt：与上方流式循环 / Phase1 各
            # 长 await 入口保持一致——用户在初稿出来到这里之间接管的话，免去
            # 一次最长 20s 的 ainvoke 白烧 token（CodeRabbit Minor）。
            if mgr.state.is_proactive_preempted(proactive_sid):
                logger.info(
                    "[%s] proactive BM25 regen aborted: user preempted before ainvoke",
                    lanlan_name,
                )
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "pass",
                    "reason_code": PROACTIVE_REASON_DELIVERY_PREEMPTED,
                    "message": "BM25 regen 前用户已接管",
                }))
            try:
                async with asyncio.timeout(20.0):
                    async with (await _make_llm(
                        temperature=1.0,
                        max_completion_tokens=PROACTIVE_PHASE2_GENERATE_MAX_TOKENS,
                        use_vision=phase2_use_vision,
                        disable_thinking=phase2_disable_thinking,
                    )) as _regen_llm:
                        _regen_resp = await _regen_llm.ainvoke(regen_messages)
                        regen_text = (
                            _regen_resp.content if hasattr(_regen_resp, "content") else ""
                        ) or ""
            except Exception as _regen_exc:
                logger.warning(
                    "[%s] proactive BM25 regen LLM call failed: %s",
                    lanlan_name, _regen_exc,
                )
                regen_text = ""

            # regen 输出可能仍带 "主动搭话\n[TAG]\n" 前缀；轻量剥一下。失败就
            # 用原文（mismatch 不至于致命）。
            # ⚠️ regen 用**独立**的 ``regen_source_tag`` 解析，避免沿用初稿的
            # ``source_tag``：若初稿是 [MUSIC]、regen 返回纯文本，沿用 MUSIC 会
            # 让下面的 "MUSIC→非MUSIC clear" 不触发、music 候选继续注入 → 复读
            # 又出去（CodeRabbit Major）。规则：
            #   regen 解析出 tag → 用该 tag
            #   regen 非空但没 tag → drop（与初稿同款格式泄漏防护：弱模型常把人设
            #     Format/约束块当正文吐出来，缺 tag 一律判泄漏，不再当成 CHAT 投递）
            #   regen 空 / [PASS] → 上面 drop 分支拦掉
            _cleaned = (regen_text or "").strip()
            regen_source_tag = ""
            _m = re.search(r"主动搭话\s*\n", _cleaned)
            if _m:
                _cleaned = _cleaned[_m.end():]
            _tag_m = re.match(
                r"^\[(CHAT|WEB|PASS|MUSIC|MEME)\]\s*", _cleaned, re.IGNORECASE,
            )
            if _tag_m:
                regen_source_tag = _tag_m.group(1).upper()
                _cleaned = _cleaned[_tag_m.end():]
            else:
                _cleaned, _leak_tag = _strip_proactive_screen_tag_leak(_cleaned)
                if _leak_tag:
                    regen_source_tag = _leak_tag
            # 同初稿：把泄漏的内部引导标签从 regen 产出里剥掉，且必须在下面两道
            # regen 复读复判（score_draft / 字面相似度）**之前**剥——否则带标签前缀
            # 的复读会稀释分数绕过 drop。_cleaned 在此一次性规范化，复判与投递共用。
            _cleaned = _strip_proactive_intent_label_leak(_cleaned)
            # regen 输出 [PASS] / 空 → 等价于"模型放弃了"，drop 而不是退回原文。
            # 显式把 ``regen_source_tag == 'PASS'`` 也算 drop（前面剥过 [TAG] 前缀，
            # _cleaned 已不含字面 "[PASS]"，但 regen_source_tag 记下了是 PASS）。
            # 无 tag 是否算 drop 与初稿 gate 同款守卫：仅当本轮启用 tag 系统
            # (_expects_source_tag) 时，无 tag 才判格式泄漏 drop；_of_none 纯文本模式
            # 无 tag 是合法的，留空交给下游 source_tag='CHAT' 兜底（Codex P2）。
            if (
                regen_source_tag == "PASS"
                or (_expects_source_tag and not regen_source_tag)
                or not _cleaned.strip()
                or "[PASS]" in _cleaned.upper()
            ):
                logger.info("[%s] proactive BM25 regen returned empty/PASS/untagged, drop", lanlan_name)
                if not mgr.state.is_proactive_preempted(proactive_sid):
                    await mgr.handle_new_message()
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "pass",
                    "reason_code": PROACTIVE_REASON_PASS_DUPLICATE,
                    "message": "BM25 regen 失败，已 drop",
                }))

            # 再 score 一次：仍 >= DROP 则真 drop
            try:
                _regen_total, _ = _ar_corpus.score_draft(lanlan_name, _cleaned)
            except Exception:
                _regen_total = 0.0
            if _regen_total >= ANTI_REPEAT_DROP_THRESHOLD:
                logger.info(
                    "[%s] proactive BM25 regen still over drop (score=%.2f)",
                    lanlan_name, _regen_total,
                )
                if not mgr.state.is_proactive_preempted(proactive_sid):
                    await mgr.handle_new_message()
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "pass",
                    "reason_code": PROACTIVE_REASON_PASS_DUPLICATE,
                    "message": "BM25 regen 后仍超阈值，已 drop",
                    "bm25_score": _regen_total,
                }))
            # regen 文本也跑一次字面相似度检查——BM25 抓"换种说法但同 topic"，
            # 字面相似度抓"几乎一字不差"，两条独立信号；regen 在 BM25 上过关
            # 不代表没撞上最近原话（model 偶尔会沿用语序）。CodeRabbit Major
            # 指出。
            _regen_dup, _regen_sim = _is_similar_to_recent_proactive_chat(
                lanlan_name, _cleaned,
            )
            if _regen_dup:
                logger.info(
                    "[%s] proactive BM25 regen still literal-dup (similarity=%.3f)",
                    lanlan_name, _regen_sim,
                )
                if not mgr.state.is_proactive_preempted(proactive_sid):
                    await mgr.handle_new_message()
                return await _end_proactive(JSONResponse({
                    "success": True,
                    "action": "pass",
                    "reason_code": PROACTIVE_REASON_PASS_DUPLICATE,
                    "message": "BM25 regen 后字面相似度仍超阈值，已 drop",
                    "similarity": _regen_sim,
                    "threshold": _PROACTIVE_SIMILARITY_THRESHOLD,
                }))
            # _expects_source_tag 时 regen_source_tag 必为合法非 PASS tag；_of_none
            # 模式可能为空（合法无 tag），留空交给下游 source_tag='CHAT' 兜底。
            source_tag = regen_source_tag
            # regen 后只要最终不是 MUSIC，就清掉本轮 music 候选。
            # 之前的版本只在 _initial_source_tag == "MUSIC" 时清，但 tagless
            # 初稿（_initial 为空）+ phase1 只有 music topic 的场景下，
            # should_try_music_fallback 仍会把原曲目塞回 source_links，等于
            # 把刚 regen 避开的内容又带回去（CodeRabbit Major）。
            # 仅当 regen 显式落到 MUSIC 才保留候选（initial 即 MUSIC、regen
            # 也仍选 MUSIC 的少数情形）。
            if source_tag != "MUSIC":
                if selected_music_link is not None or music_content is not None:
                    logger.info(
                        "[%s] proactive BM25 regen final tag=%s (initial=%s); cleared music candidate",
                        lanlan_name, source_tag, _initial_source_tag or "(none)",
                    )
                selected_music_link = None
                music_content = None
            # 采用 regen 文本接着走下游 source_tag / TTS 投递（_cleaned 已在上方
            # 落定时剥过泄漏标签，复读复判与投递共用同一份干净文本）。
            response_text = _cleaned
            full_text = _cleaned

        has_music_topic = 'music' in active_channels

        # 【加固】数据级锁：如果正在播放音乐，哪怕 AI 产生了音乐标签，也强制降级/忽略
        is_music_used = has_music_topic and source_tag == 'MUSIC'
        ai_wants_music = source_tag == 'MUSIC'

        if is_playing_music and ai_wants_music:
            print(f"[{lanlan_name}] 数据级锁触发：播放中尝试推荐新歌，已强制拦截并清空曲目列表")
            is_music_used = False
            music_content = None
            source_tag = 'PASS'
            aborted = True
        elif music_cooldown and ai_wants_music:
            # 冷却期：music 通道本不应出现在上下文中，但模型仍输出了 [MUSIC] 标签。
            # 降级为普通 CHAT 而非 abort 整轮搭话，避免浪费其他 source 的有效内容。
            print(f"[{lanlan_name}] 音乐冷却期模型输出 [MUSIC]，降级为 CHAT（不中止搭话）")
            is_music_used = False
            music_content = None
            source_tag = 'CHAT'
        
        # 【加固补齐】如果触发了降级拦截（aborted），立即返回
        if aborted:
            if not mgr.state.is_proactive_preempted(proactive_sid):
                await mgr.handle_new_message()
            else:
                logger.info(f"[{lanlan_name}] 降级拦截 abort 但用户已接管 (state preempted)，跳过 TTS 清理")
            return await _end_proactive(JSONResponse({
                "success": True,
                "action": "pass",
                "reason_code": PROACTIVE_REASON_PASS_MODEL_PASS,
                "message": f"[{lanlan_name}] 播放中推荐拦截触发，动作已取消"
            }))

        # _of_none output-format 路径明确指示 AI"不带 source tag"，所以 AI 真正
        # 跟进 unfinished thread 时输出可能完全没有标签。落到这里又非 abort/empty,
        # 说明 Phase 2 实际产出了文本——按 CHAT 兜底，让下游 build_proactive_response
        # 把 primary_channel 设为 'chat'，否则 mark_unfinished_thread_used 会把这一
        # 类合法跟进当作"没用 override"漏掉，2 次配额被静默绕过。
        if not source_tag and full_text.strip():
            source_tag = 'CHAT'

        # 使用纯函数构建响应
        primary_channel, source_links = build_proactive_response(source_tag, {
            'lanlan_name': lanlan_name,
            'is_music_used': is_music_used,
            'selected_web_link': selected_web_link,
            'selected_music_link': selected_music_link,
            'selected_meme_link': selected_meme_link,
            'vision_content': vision_content
        })

        # 兜底：当最终主通道已经落到 music，或当前实际上只剩音乐通道时，
        # 【逻辑加固】如果 active_channels 里包含 meme 且 primary_channel 是 meme，不触发 fallback
        should_try_music_fallback = not is_playing_music and not music_cooldown and (
            primary_channel == 'music'
            or (has_music_topic and not any(ch in ('vision', 'web', 'meme') for ch in active_channels))
        )
        if should_try_music_fallback:
            if source_links is None:
                source_links = []
            if _append_music_recommendations(source_links, music_content) > 0:
                is_music_used = True

        if is_music_used:
            # 此处不再二次调用，因为 should_try_music_fallback 已经处理了 append
            # 或者如果 is_music_used 为 True 但 haven't appended yet, do it.
            # 实际上 supports_music_fallback 已经 append 了。
            # 为了稳妥，我们只在尚未 append 时调用。
            music_already_appended = any(link.get('source') == '音乐推荐' for link in source_links)
            if not music_already_appended:
                _append_music_recommendations(source_links, music_content)

        # anti-repeat / 素材去重按"真实投递的 channel"归类，而非模型原始 source_tag
        # （此处 primary_channel 已由 build_proactive_response 按实际 source_links 定下，
        # 比 gate 的 Phase-1 预测更准）（Codex P2）：
        # - is_music_used（含模型出 [CHAT] 但 should_try_music_fallback 追加了曲目）
        #   或 primary_channel=='music' → 实际投递音乐 → MUSIC：否则模板 intro 会被
        #   按 CHAT 录进 BM25 corpus、且曲目 key 不记，重新引入 fallback 推歌污染。
        # - 仅当 primary_channel=='meme' 且确有表情包链接（selected_meme_link 非空，
        #   build_proactive_response 此时才真 append 图）才算 MEME 投递；模型出 [MEME]
        #   但选空时它已回退别的 channel（甚至 primary 仍是 'meme' 但无链接），不能按
        #   MEME 记——否则模板文案漏录 corpus，且把没发出的关键词记成已投递，害得之后
        #   同关键词的真表情包被当复读跳过。
        # - 其余落到非豁免 CHAT（WEB/vision 同样非豁免，对 anti-repeat 等价）。
        if is_music_used or primary_channel == 'music':
            _delivered_tag = 'MUSIC'
        elif primary_channel == 'meme' and selected_meme_link is not None:
            _delivered_tag = 'MEME'
        else:
            _delivered_tag = 'CHAT'
        # 曲目优先取 selected_music_link；regen 把 tag 降级 CHAT 时它已被清空，则从已
        # 追加的 source_links（source=='音乐推荐'）里取首条。
        _delivered_music_link = selected_music_link
        if _delivered_tag == 'MUSIC' and not _delivered_music_link:
            _delivered_music_link = next(
                (l for l in (source_links or []) if isinstance(l, dict) and l.get('source') == '音乐推荐'),
                None,
            )

        # 一次性投递完整文本 + 记录历史 + TTS end + turn end
        # 传 proactive_sid：若 Phase 2 流结束到这里之间用户已打断（换了 sid），
        # finish 内部会跳过所有写入，避免 proactive 文本污染用户当前轮次。
        # action_note：把"放了什么歌 / 分享了哪条内容 / 来源"作为元数据追加到
        # AIMessage 历史，否则下一轮被反问"刚才放的什么"时 LLM 完全无从作答
        # （只看得到自己说过的话，看不到自己实际投递了什么素材）。模板里对人
        # 的称呼一律用 master_name 实名展开，不写"主人"这类物化称呼。
        action_note = build_proactive_action_note(
            primary_channel=primary_channel,
            source_links=source_links,
            language=proactive_lang,
            master_name=master_name_current,
        )
        # 只要本轮后端拿到了截图、且有可用 vision 模型（phase2_use_vision 同时
        # 蕴含 screenshot_b64_for_phase2 非空），就缓存最后这张主动搭话截图，等
        # 用户下一条 text 回复时注入——不按最终投递通道筛（哪怕这轮文案落到了
        # music/web，屏幕仍是这轮看过的画面，留着供用户追问）。截图在
        # finish_proactive_delivery 内 commit 成功后才真正落 session：新一轮主动
        # 搭话产生即覆盖/清掉旧缓存（非 vision 轮传 None 清），session 侧再用 2
        # 分钟 TTL 兜底过期。
        _stage_vision_screenshot = screenshot_b64_for_phase2 if phase2_use_vision else None
        try:
            await mgr.feed_tts_chunk(response_text, expected_speech_id=proactive_sid)
            committed = await mgr.finish_proactive_delivery(
                response_text,
                expected_speech_id=proactive_sid,
                action_note=action_note,
                source_tag=_delivered_tag,
                vision_screenshot_b64=_stage_vision_screenshot,
            )
        except Exception as exc:
            logger.warning("[%s] buffered proactive delivery failed: %s", lanlan_name, exc)
            if not mgr.state.is_proactive_preempted(proactive_sid):
                await mgr.handle_new_message()
            else:
                logger.info("[%s] buffered delivery failed after user takeover; skip TTS cleanup", lanlan_name)
            return await _end_proactive(JSONResponse({
                "success": True,
                "action": "pass",
                "reason_code": PROACTIVE_REASON_DELIVERY_FAILED,
                "message": "Phase 2 buffered delivery failed",
            }))
        if not committed:
            # Proactive 内容未真正落库（用户已接管本轮），所有下游副作用必须跳过：
            # 否则 _record_proactive_chat 会把未送达内容计入去重历史、topic usage
            # 会误记已用，前端拿到 "chat" action 会以为搭话成功。
            logger.info(
                "[%s] 主动搭话被用户接管，短路下游写入（topic/memory/response）",
                lanlan_name,
            )
            return await _end_proactive(JSONResponse({
                "success": True,
                "action": "pass",
                "reason_code": PROACTIVE_REASON_DELIVERY_PREEMPTED,
                "message": "proactive delivery skipped: user took over turn",
                "lanlan_name": lanlan_name,
                "turn_id": mgr.current_speech_id,
            }))

        # 记录主动搭话
        _record_proactive_chat(lanlan_name, response_text, primary_channel)
        # 记录本轮实际投递的"素材标识"（曲目 / 搜索关键词），供下次同 channel 的
        # 素材级去重。按"真实投递 channel"_delivered_tag/_delivered_music_link 归类
        # （含模型出 CHAT 但 fallback 追加了曲目的情形），key 为空则不记录。
        _record_proactive_material(
            lanlan_name,
            _delivered_tag,
            _proactive_material_key(_delivered_tag, _delivered_music_link, meme_content),
        )
        # Mini-game 邀请冷却 counter 推进：spec 是"被回应后再 10 次搭话才解禁"，
        # 任何 channel 的成功投递都算一次，pending 期间（responded_at=None）函数
        # 内部自然 no-op，不靠"邀请自身"提前耗 counter。
        _mini_game_invite_count_post_response_chat(lanlan_name)
        # 持久化"累计成功投递的主动搭话总数"，给 force-first 用——新用户在第 N
        # 次成功投递时强制走 mini-game 邀请，跨重启计数。
        await _increment_proactive_chat_total(lanlan_name)
        # Reminiscence usage：本轮 surfaced 了 pending reflection（不管 AI 最终
        # 用了什么标签，followup 都出现在 prompt 里）→ 记一次 reminiscence 用量。
        # 用独立 buffer (_reminiscence_usage_history) 而不是把同一条 message
        # 二次写进 _proactive_chat_history——后者还驱动 _format_recent_proactive_chats
        # 和 _is_similar_to_recent_proactive_chat，二次写会让 dedup / 相似度
        # 检查把这条 proactive 跟自己撞上、虚高 score。_compute_source_weights
        # 直接读这个独立 buffer 把 reminiscence 当一档 channel 衰减。
        if _surfaced_reflection_ids:
            _record_reminiscence_usage(lanlan_name)

        # Unfinished-thread 跟进计数：仅当 AI 本轮真的产出 [CHAT]（即没有选
        # WEB/MUSIC/MEME 这类外部素材）时才 +1。早先版本是"snapshot 里有未收尾
        # 话题就计数"，理由是想防"AI 反复忽略 override 也烧光配额"——但
        # UNFINISHED_THREAD_WINDOW_SECONDS=300 的自动过期已经兜底了 thread 的总
        # 暴露时间，再多算曝光只会让两次外部素材轮把真正的续接配额提前烧光。
        # source_tag == 'CHAT' / primary_channel == 'chat' 是 build_proactive_response
        # 后唯一可靠的 "AI 走了文本路径" 信号；无 tag 但出过文本时上游会兜底
        # 设成 CHAT。[PASS] 已在 4079 早 return，不会走到这里。
        if _has_unfinished_thread and (source_tag == 'CHAT' or primary_channel == 'chat'):
            try:
                mgr._activity_tracker.mark_unfinished_thread_used()
                print(f"[{lanlan_name}] 跟进未收尾话题：mark_used")
            except Exception as _ut_err:
                logger.warning(f"[{lanlan_name}] mark_unfinished_thread_used failed: {_ut_err}")

        # 后台长期记忆维护（通过 memory_server API）：复用 internal_http_client 单例
        try:
            from utils.internal_http_client import get_internal_http_client
            _mem_base = f"http://127.0.0.1:{MEMORY_SERVER_PORT}"
            _mem_client = get_internal_http_client()
            # 保存本次搭话实际提及的 pending 反思 ID（供下次 /process 做反馈检查）
            if _surfaced_reflection_ids:
                await _mem_client.post(
                    f"{_mem_base}/record_surfaced/{lanlan_name}",
                    json={"reflection_ids": _surfaced_reflection_ids},
                    timeout=5.0,
                )
                print(f"[{lanlan_name}] 记录 surfaced 反思: {len(_surfaced_reflection_ids)} 条")

            # 记录 persona 提及次数（疲劳跟踪） — persona 文件由 memory_server 管理
            # record_mentions 已在 memory_server 的 _run_post_turn_signals 中调用
        except Exception as e:
            logger.debug(f"[{lanlan_name}] 长期记忆后处理失败（不影响主流程）: {e}")

        # 【逻辑优化】精准的话题去重记录：仅当链接真正被加入 source_links 时才记录已使用
        def _is_link_selected(selected_link):
            if not selected_link:
                return False

            target_url = (selected_link.get('url') or '').strip()
            if target_url:
                # 存在有效 URL 时，按 URL 对比
                return any((link.get('url') or '').strip() == target_url for link in source_links if link)

            # URL 为空（如音乐降级记录），按元数据签名对比
            target_sig = (
                (selected_link.get('title') or '').strip(),
                (selected_link.get('artist') or '').strip(),
                (selected_link.get('source') or '').strip(),
            )
            return any(
                (
                    (link.get('title') or '').strip(),
                    (link.get('artist') or '').strip(),
                    (link.get('source') or '').strip(),
                ) == target_sig
                for link in source_links if link
            )

        # title-only 的 web topic（LLM 在 over-fetch 列表外编出来的标题）也写入衰减历史，
        # 否则下一轮可能再次被 surface。matched 时仍按链接是否成功登卡（_is_link_selected）
        # 把关；非 matched 时绕过链接卡片检查。
        if selected_web_topic_key and (
            selected_web_link is None or _is_link_selected(selected_web_link)
        ):
            _wl = selected_web_link or {}
            _web_title_dbg = (
                _wl.get('title', '')
                or (web_parsed.get('title', '') if web_parsed else '')
            )
            await _record_source_used(
                url=_wl.get('url', '') or '',
                kind='web',
                title=_web_title_dbg,
            )
            print(f"[{lanlan_name}] 已记录 Web source 衰减历史: {selected_web_topic_key[:16]}")

        if selected_music_topic_key and (is_music_used or _is_link_selected(selected_music_link)):
            _ml = selected_music_link or {}
            _music_title_dbg = f"{_ml.get('title', '')} - {_ml.get('artist', '')}".strip(' -')
            await _record_source_used(
                url=_ml.get('url', '') or '',
                kind='music',
                title=_music_title_dbg,
            )
            print(f"[{lanlan_name}] 已记录音乐 source 衰减历史: {selected_music_topic_key[:16]}")

        if selected_meme_topic_key and _is_link_selected(selected_meme_link):
            await _record_source_used(
                url=(selected_meme_link or {}).get('url', '') or '',
                kind='image',
                title=(selected_meme_link or {}).get('title', '') or '',
            )
            print(f"[{lanlan_name}] 已记录表情包 source 衰减历史: {selected_meme_topic_key[:16]}")

        return await _end_proactive(JSONResponse({
            "success": True,
            "action": "chat",
            "reason_code": PROACTIVE_REASON_CHAT_DELIVERED,
            "message": "主动搭话已发送",
            "lanlan_name": lanlan_name,
            "source_mode": primary_channel.lower(),
            "source_tag": source_tag or "unknown",
            "active_channels": active_channels,
            "source_links": source_links,
            "turn_id": mgr.current_speech_id
        }))

    except asyncio.TimeoutError:
        logger.error("主动搭话超时")
        await _safe_fire_proactive_done(locals())
        return JSONResponse(
            _proactive_error_body(
                PROACTIVE_REASON_ERROR_TIMEOUT,
                error="AI处理超时",
            ),
            status_code=504,
        )
    except Exception as e:
        logger.error(f"主动搭话接口异常: {e}")
        await _safe_fire_proactive_done(locals())
        return JSONResponse(
            _proactive_error_body(
                PROACTIVE_REASON_ERROR_INTERNAL,
                error="服务器内部错误",
                detail=str(e),
            ),
            status_code=500,
        )


@router.post('/proactive/music_played_through')
async def proactive_music_played_through(request: Request):
    """Record that the user finished a recommended song.

    Completed playback is strong positive feedback for the music channel, so
    matching proactive history entries are cleared from the channel-specific
    decay calculation.
    """
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}
    try:
        config_manager = get_config_manager()
        _, her_name_default, _, _, _, _, _, _, _ = await config_manager.aget_character_data()
    except Exception:
        her_name_default = ''
    lanlan_name = (data.get('lanlan_name') or her_name_default or '').strip()
    if not lanlan_name:
        return JSONResponse({"success": False, "error": "lanlan_name missing"}, status_code=400)
    cleared = _clear_channel_from_proactive_history(lanlan_name, 'music')
    if cleared:
        logger.info(f"[{lanlan_name}] 音乐完整播放，重置 music 通道权重衰减（清空 {cleared} 条）")
    return JSONResponse({"success": True, "cleared": cleared, "lanlan_name": lanlan_name})
