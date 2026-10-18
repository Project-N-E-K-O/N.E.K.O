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

"""Framework-independent proactive-chat orchestration service."""

import asyncio
import random
from typing import Any
from uuid import uuid4

from config import (
    MEMORY_SERVER_PORT,
    MINI_GAME_INVITE_ENABLED,
    MINI_GAME_INVITE_FORCE_GAME_TYPE,
    PROACTIVE_EXTERNAL_PER_ITEM_MAX_TOKENS,
    PROACTIVE_EXTERNAL_TOTAL_MAX_TOKENS,
    PROACTIVE_PHASE1_FETCH_PER_SOURCE,
    PROACTIVE_PHASE1_TOTAL_TOPICS,
)
from config.prompts.prompts_proactive import (
    EXTERNAL_TOPIC_FOOTER,
    EXTERNAL_TOPIC_HEADER,
    MEME_SECTION_FOOTER,
    MEME_SECTION_HEADER,
    MUSIC_SECTION_FOOTER,
    MUSIC_SECTION_HEADER,
    PROACTIVE_SOURCE_LABELS,
    SCREEN_WINDOW_TITLE,
    get_meme_topic_line,
    get_proactive_format_sections,
    get_screen_img_hint,
    get_screen_section_footer,
    get_screen_section_header,
    normalize_proactive_prompt_locale,
)
from config.prompts.prompts_sys import _loc
from main_logic.omni_realtime_client import OmniRealtimeClient
from main_logic.proactive_chat.contracts import (
    PROACTIVE_REASON_CHAT_DELIVERED,
    PROACTIVE_REASON_DELIVERY_PREEMPTED,
    PROACTIVE_REASON_ERROR_INTERNAL,
    PROACTIVE_REASON_ERROR_SOURCE_FETCH_FAILED,
    PROACTIVE_REASON_ERROR_TIMEOUT,
    PROACTIVE_REASON_PASS_ACTIVITY_BUSY,
    PROACTIVE_REASON_PASS_BUSY,
    PROACTIVE_REASON_PASS_DISABLED,
    PROACTIVE_REASON_PASS_DUPLICATE,
    PROACTIVE_REASON_PASS_PRIVACY,
    ProactiveChatCommand,
    ProactiveChatResult,
    _proactive_chat_body,
    _proactive_error_body,
    _proactive_pass_body,
)
from main_logic.proactive_chat.decisions import (
    _decide_activity_schedule,
    _decide_busy_entry_guard,
    _decide_closed_activity_gate,
    _decide_empty_source_gate,
    _decide_game_route_entry_guard,
    _decide_manager_entry_guard,
    _decide_probabilistic_activity_gate,
    _select_source_modes,
    _select_weighted_sources,
    _should_fetch_activity_snapshot,
    _should_skip_source,
    _should_use_voice_fast_path,
)
from main_logic.proactive_chat.delivery import (
    CommittedDelivery,
    DeliveryCommit,
    ProactiveLifecycle,
    _commit_proactive_delivery,
    _record_committed_delivery,
)
from main_logic.proactive_chat.candidate_selection import (
    _format_phase1_link_candidate,
    _number_phase1_links_by_source,
    _phase1_linkless_modes,
    _round_robin_phase1_links,
)
from main_logic.proactive_chat.generation import (
    Phase2PromptContext,
    ProactiveModelConfig,
    _append_directives_section,
    _check_proactive_vision_consent,
    _decide_phase1_channels,
    _fetch_phase1_followups,
    _is_neko_community_phase1_source,
    _lookup_link_by_phase1_selection,
    _proactive_llm_retry_error_types,
    _proactive_vision_consent_checker,
    _run_phase2_generation,
    _run_unified_phase1,
)
from main_logic.proactive_chat.mini_game_invite import (
    _advance_mini_game_invite_entry,
    _build_mini_game_invite_options_payload,
    _last_user_message_at_from_activity,
    _mini_game_invite_count_post_response_chat,
    _mini_game_invite_get_state,
    _mini_game_invite_record_delivered,
    _pick_mini_game_type,
)
from main_logic.proactive_chat.music_recommendation import (
    _build_music_dynamic_context,
    _build_music_playing_hint,
    _select_music_recommendation,
)
from main_logic.proactive_chat.state import (
    _enter_proactive_phase2,
    _ensure_source_history_loaded,
    _format_recent_proactive_chats,
    _increment_proactive_chat_total,
    _record_invite_delivery_persistent,
    _record_source_used,
    _source_hash,
)
from main_logic.proactive_chat.sources import collect_proactive_sources
from utils.web_scraper.proactive_candidate import (
    SelectedWebCandidatePreempted,
    prepare_selected_web_candidate,
)
from utils.language_utils import (
    get_global_language,
    get_global_language_full,
    is_supported_language_code,
    normalize_language_code,
)
from utils.logger_config import get_module_logger
from utils.meme_moderation import moderate_meme_image_url
from utils.music_crawlers import mark_music_as_played
from .break_reminders import (
    _compose_break_system_prompt,
    _deliver_break_reminder_via_llm,
    _render_anti_slack_prompt,
    _render_work_break_game_invite_prompt,
    _render_work_break_prompt,
)

__all__ = [
    "CommittedDelivery",
    "DeliveryCommit",
    "ProactiveLifecycle",
    "_commit_proactive_delivery",
    "_record_committed_delivery",
    "handle_proactive_chat",
]


logger = get_module_logger(__name__, "Main")


def _consume_repeat_suppressed_break_reminder(
    mgr,
    *,
    lanlan_name: str,
    channel: str,
) -> None:
    """Consume a must-fire source that the repeat guard deliberately dropped."""
    marker_name = (
        "mark_anti_slack_used"
        if channel == "anti_slack"
        else "mark_work_break_used"
    )
    try:
        getattr(mgr._activity_tracker, marker_name)()
    except Exception as mark_err:
        logger.warning(
            "[%s] %s after repeat suppression failed: %s",
            lanlan_name,
            marker_name,
            mark_err,
        )


_MEME_PROXY_CANDIDATE_CHECK_LIMIT = 3
# Sources that read the screen or the active window share the vision consent gate.
_PROACTIVE_VISION_SOURCE_MODES = frozenset({"vision", "window"})


_PHASE1_FETCH_PER_SOURCE = (
    PROACTIVE_PHASE1_FETCH_PER_SOURCE  # Fixed Phase1 fetch count per source.
)


_PHASE1_TOTAL_TOPIC_TARGET = (
    PROACTIVE_PHASE1_TOTAL_TOPICS  # Target total candidates passed to Phase1 filtering model.
)


def _phase1_fallback_records(content_text: str) -> list[str]:
    """Group numbered fallback records with their continuation lines."""

    records: list[str] = []
    preamble: list[str] = []
    current: list[str] = []
    for raw_line in content_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        prefix, separator, remainder = line.partition(". ")
        is_record_start = bool(separator and prefix.isdigit() and remainder.strip())
        is_section_header = line.startswith(("[", "【"))
        if is_record_start:
            if current:
                records.append("\n".join(current))
            current = [*preamble, line]
            preamble = []
        elif is_section_header:
            if current:
                records.append("\n".join(current))
                current = []
            preamble.append(line)
        elif current:
            current.append(line)
        else:
            preamble.append(line)
    if current:
        records.append("\n".join(current))
    elif preamble:
        records.append("\n".join(preamble))
    return records


def _merge_phase1_parts_within_token_budget(
    parts: list[tuple[str, list[str]]], *, max_tokens: int
) -> str:
    """Keep complete Phase 1 candidates while fitting the aggregate budget."""

    from utils.tokenize import truncate_to_tokens

    kept_parts: list[str] = []
    for header, candidates in parts:
        kept_candidates: list[str] = []
        for candidate in candidates:
            section = header + "\n" + "\n".join([*kept_candidates, candidate])
            merged = "\n\n".join([*kept_parts, section])
            if truncate_to_tokens(merged, max_tokens) == merged:
                kept_candidates.append(candidate)
        if kept_candidates:
            kept_parts.append(header + "\n" + "\n".join(kept_candidates))
    return "\n\n".join(kept_parts)


def _open_threads_for_activity_state(
    activity_snapshot, fresh_open_threads
) -> list[str]:
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
    if getattr(activity_snapshot, "unfinished_thread", None) is not None:
        return []
    if getattr(activity_snapshot, "propensity", None) == "restricted_screen_only":
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
        text = clean_text(topic.get("text"))
        if not text or text in seen_texts:
            continue
        seen_texts.add(text)
        if topic.get("id"):
            surfaced_reflection_ids.append(topic["id"])
    return prompt, surfaced_reflection_ids


def _command_language_candidates(
    command_or_data: ProactiveChatCommand | dict,
) -> tuple[Any, Any, Any]:
    """Read locale aliases from the new command or the compatibility dict."""
    if isinstance(command_or_data, ProactiveChatCommand):
        return command_or_data.language_candidates
    return (
        command_or_data.get("language"),
        command_or_data.get("lang"),
        command_or_data.get("i18n_language"),
    )


def _command_render_language(command_or_data: ProactiveChatCommand | dict) -> Any:
    """Read a request-only template locale that must never become durable state."""
    if isinstance(command_or_data, ProactiveChatCommand):
        return command_or_data.render_language
    return command_or_data.get("render_language")


def _resolve_proactive_locale(
    data: ProactiveChatCommand | dict,
    mgr,
    *,
    fmt: str = "short",
) -> str:
    """Resolve the active user locale for proactive chat flows.

    An explicit request preference wins first, followed by an explicit manager
    preference. A request-only render locale may override only a non-explicit
    manager fallback; process-level global language remains the final fallback.
    This aligns render-only responses with the live UI without shadowing a
    durable per-character preference.

    Three output shapes, one precedence chain — which is the point of the parameter
    rather than a second resolver (issue #2500):

    - ``fmt="short"`` — a language family (``zh``). No room for a script, so
      ``zh-TW`` and ``zh-CN`` both come out ``zh``.
    - ``fmt="full"`` — a BCP-47 locale (``zh-CN`` / ``zh-TW``). This is the key
      scheme of ``static/locales/*.json``.
    - ``fmt="prompt"`` — a prompt-dict key (``zh`` / ``zh-TW``). This is the key
      scheme of every table in ``config/prompts``, and it is NOT the same as
      ``full``: those tables have no ``zh-CN`` row, so a full locale misses and
      degrades Simplified users to English on the plain ``dict.get`` lookups
      (``MUSIC_SEARCH_RESULT_TEXTS``, ``RECENT_PROACTIVE_TIME_LABELS``,
      ``WORK_BREAK_GENERIC_WORK_LABEL``, ...). Use this wherever the result ends
      up indexing a prompt dict; it keeps ``zh-TW`` reachable without stranding
      ``zh-CN``.
    """
    def _normalize(value: str) -> str:
        if fmt == "prompt":
            return normalize_proactive_prompt_locale(value)
        return normalize_language_code(value, format=fmt)

    request_lang = next(
        (value for value in _command_language_candidates(data) if value),
        None,
    )
    # Like ``main_routers/game_router._absorb_request_language``: third-party clients /
    # corrupted localStorage may send garbage such as ``'undefined'`` / ``'estonian'``;
    # ``normalize_language_code`` defaults unrecognized values to ``'en'``, so first reject with shared allowlist,
    # or proactive invitation text silently becomes English, bypassing session's actual language.
    if request_lang and is_supported_language_code(request_lang):
        normalized = _normalize(request_lang)
        if normalized:
            return normalized

    session_lang = getattr(mgr, "user_language", None)
    session_language_is_explicit = bool(
        getattr(mgr, "_user_language_explicit", False)
    )
    normalized_session_lang = None
    if session_lang and is_supported_language_code(session_lang):
        normalized_session_lang = _normalize(session_lang) or None

    if session_language_is_explicit and normalized_session_lang:
        return normalized_session_lang

    render_lang = _command_render_language(data)
    if render_lang and is_supported_language_code(render_lang):
        normalized = _normalize(render_lang)
        if normalized:
            return normalized

    if normalized_session_lang:
        return normalized_session_lang
    if fmt == "prompt":
        return normalize_proactive_prompt_locale(get_global_language_full()) or "en"
    if fmt == "full":
        return get_global_language_full() or "en"
    return get_global_language() or "en"


def _resolve_topic_hook_locale(
    data: ProactiveChatCommand | dict,
    mgr,
    *,
    fallback: str,
) -> str:
    """Resolve the locale for topic-hook prompts without collapsing zh-TW."""
    declared = _resolve_declared_topic_hook_locale(data, mgr)
    if declared:
        return declared
    render_lang = _command_render_language(data)
    if render_lang and is_supported_language_code(render_lang):
        normalized = normalize_language_code(render_lang, format="full")
        if normalized:
            return normalized
    global_lang = normalize_language_code(get_global_language_full(), format="full")
    if global_lang:
        return global_lang
    return fallback


def _resolve_declared_topic_hook_locale(
    data: ProactiveChatCommand | dict,
    mgr,
) -> str | None:
    """Resolve only a locale explicitly declared by the request or session."""
    for raw_lang in _command_language_candidates(data):
        if raw_lang and is_supported_language_code(raw_lang):
            normalized = normalize_language_code(raw_lang, format="full")
            if normalized:
                return normalized
    if not getattr(mgr, "_user_language_explicit", False):
        return None
    raw_lang = getattr(mgr, "user_language", None)
    if raw_lang and is_supported_language_code(raw_lang):
        return normalize_language_code(raw_lang, format="full") or None
    return None


def _new_dialog_locale_params(
    data: ProactiveChatCommand | dict,
    mgr,
) -> dict[str, str] | None:
    """Pass durable and render-only locale evidence through separate fields."""
    params: dict[str, str] = {}
    declared = _resolve_declared_topic_hook_locale(data, mgr)
    if declared:
        params["language"] = declared
    render_language = _command_render_language(data)
    if render_language and is_supported_language_code(render_language):
        normalized_render_language = normalize_language_code(
            render_language,
            format="full",
        )
        if normalized_render_language:
            params["render_language"] = normalized_render_language
    return params or None


def _resolve_phase2_avatar_position(fresh, request_avatar_position):
    """Pick the avatar position to annotate a freshly fetched Phase 2 screenshot with.

    Who is allowed to place the annotation depends on how the image was produced:

    - ``'websocket'``: the frontend already judged THIS image. A position means
      "annotate here"; ``None`` means it deliberately decided not to annotate
      (window capture, camera, avatar collapsed, multi-monitor). Never substitute
      the request's older position for that ``None`` -- it is a verdict, not a
      missing value. Same rule as ``main_logic.core.streaming``, which refuses to
      fall back to the cached position for exactly this reason.
    - ``'backend_fallback'``: the frontend never saw this image, so it cannot have
      objected. The position carried by the original request is the only avatar
      information that exists for this path.

    Neither branch of ``request_fresh_screenshot`` draws the annotation itself;
    the caller does it with whatever this returns.
    """
    source = getattr(fresh, "source", "")
    if source == "backend_fallback":
        return request_avatar_position
    return getattr(fresh, "avatar_position", None)


async def _vision_consent_declined(vision_consent_check: Any) -> bool:
    """Strict vision-consent read; an unreadable consent counts as declined."""
    try:
        await _check_proactive_vision_consent(vision_consent_check)
    except Exception:
        return True
    return False


async def _withhold_revoked_vision_sources(
    sources: dict[str, dict], vision_consent_check: Any, *, lanlan_name: str, stage: str,
) -> bool:
    """Drop screen/window sources in place if consent is gone; return whether it did."""
    if not _PROACTIVE_VISION_SOURCE_MODES.intersection(sources):
        return False
    if not await _vision_consent_declined(vision_consent_check):
        return False
    logger.info("[%s] 视觉授权已撤回（%s），丢弃屏幕/窗口来源", lanlan_name, stage)
    for mode in _PROACTIVE_VISION_SOURCE_MODES:
        sources.pop(mode, None)
    return True


async def _collect_sources_under_vision_consent(
    *,
    command: ProactiveChatCommand,
    enabled_modes: list[str],
    lanlan_name: str,
    vision_consent_check: Any,
) -> tuple[list[str], dict[str, dict], bool]:
    """Collect enabled sources; screen and window sources only under vision consent.

    The frontend withdraws these modes at send time. A revocation that arrives
    while the request is in flight is enforced here: before collection the
    modes are dropped, so the screen and the active window are not read; after
    collection their results are discarded, because a read already started
    cannot be recalled. The flag reports whether consent withheld any source.
    """
    withheld = False
    if (
        _PROACTIVE_VISION_SOURCE_MODES.intersection(enabled_modes)
        and await _vision_consent_declined(vision_consent_check)
    ):
        logger.info("[%s] 视觉授权已撤回，本轮不读取屏幕/窗口来源", lanlan_name)
        enabled_modes = [
            mode for mode in enabled_modes if mode not in _PROACTIVE_VISION_SOURCE_MODES
        ]
        withheld = True
    sources = await collect_proactive_sources(
        command=command,
        enabled_modes=enabled_modes,
        lanlan_name=lanlan_name,
        log=logger,
    )
    if await _withhold_revoked_vision_sources(
        sources, vision_consent_check, lanlan_name=lanlan_name, stage="采集期间",
    ):
        withheld = True
    return enabled_modes, sources, withheld


async def handle_proactive_chat(
    command: ProactiveChatCommand,
    *,
    config_manager: Any,
    session_manager: Any,
    character_data: tuple[Any, ...],
    game_route_active_for: Any,
    break_config_manager_provider: Any,
    run_mini_game_invite_short_circuit: Any,
    push_mini_game_invite_options: Any,
    push_mini_game_invite_resolved: Any,
    meme_proxy_candidate_fetchable: Any | None = None,
) -> ProactiveChatResult:
    """
    Proactive chat: two-phase architecture — Phase 1 merged LLM (web screening + music/meme keywords, 1 call), Phase 2 persona-aware chat generation.
    """
    lifecycle: ProactiveLifecycle | None = None
    try:
        _config_manager = config_manager
        # Character data is intentionally fetched by the Router before JSON
        # parsing to preserve the endpoint's legacy exception priority.
        master_name_current, her_name_current, _, _, _, lanlan_prompt_map, _, _, _ = (
            character_data
        )
        lanlan_name = command.lanlan_name or her_name_current
        is_playing_music = command.is_playing_music
        is_music_occupied = command.is_music_occupied
        current_track = command.current_track
        music_cooldown = command.music_cooldown

        # Get session manager.
        mgr = session_manager.get(lanlan_name)
        vision_consent_epoch = getattr(mgr, "_proactive_vision_revocation_epoch", 0)
        manager_exists = bool(mgr)
        entry_result = _decide_manager_entry_guard(
            lanlan_name,
            manager_exists=manager_exists,
            goodbye_silent=(
                getattr(mgr, "is_goodbye_silent", lambda: False)()
                if manager_exists
                else False
            ),
        )
        if entry_result is not None:
            if entry_result.body.get("reason_code") == PROACTIVE_REASON_PASS_DISABLED:
                logger.info("[%s] 主动搭话本轮未发起：goodbye silent", lanlan_name)
            return ProactiveChatResult(
                body=entry_result.body,
                status_code=entry_result.status_code,
            )

        try:
            game_route_active = bool(game_route_active_for(lanlan_name))
        except Exception as game_route_err:
            logger.warning(
                "[%s] proactive game-route guard failed closed: %s",
                lanlan_name,
                game_route_err,
            )
            game_route_active = None
        entry_result = _decide_game_route_entry_guard(game_route_active)
        if entry_result is not None:
            if game_route_active:
                logger.info("[%s] 主动搭话本轮未发起：游戏路由 active", lanlan_name)
            return ProactiveChatResult(
                body=entry_result.body,
                status_code=entry_result.status_code,
            )

        state_memory_dir = getattr(_config_manager, "memory_dir", None)
        state_storage_kwargs = (
            {"memory_dir": state_memory_dir}
            if state_memory_dir is not None
            else {}
        )

        # Check whether a new proactive turn may start: state machine combines AI responding (_is_responding)
        # and another active proactive turn (phase != IDLE) into an O(1) decision.
        # Retain mgr.is_active only to check session instantiation.
        probe_session = mgr.session if mgr.is_active else None

        # ========== Voice mode fast path ==========
        # Voice mode bypasses Phase1/2 without claiming proactive phase; read-only
        # can_start_proactive suffices for 409 checks first.
        # Preserve the original short-circuit evaluation order: a text request
        # must not re-read manager/session properties solely to decide the voice
        # path, and an inactive manager must not read ``mgr.session`` here.
        if command.voice_mode:
            manager_active = mgr.is_active
            realtime_session = (
                isinstance(mgr.session, OmniRealtimeClient) if manager_active else False
            )
        else:
            manager_active = False
            realtime_session = False
        use_voice_fast_path = _should_use_voice_fast_path(
            voice_mode=command.voice_mode,
            manager_active=manager_active,
            realtime_session=realtime_session,
        )
        if use_voice_fast_path:
            # Advance mini-game invite state: voice fast path bypasses activity tracker,
            # so feed session-tracked last genuine user-message time directly to
            # advance_response. Otherwise voice-only users replying after mini-game invitation
            # leave pending forever and permanently suppress invitations (CodeRabbit Major review).
            #
            # Warning: use last_user_message_time (genuine nonempty nonecho user input), not
            # last_user_activity_time (unconditional top-level refresh including VAD noise and microphone
            # echoes of AI TTS). Reading invitation aloud contaminates the latter: after delivery,
            # echo immediately refreshes activity beyond delivered_at; next tick's implicit dismissal
            # wrongly sees user response, clears pending to later (5min), and removes buttons.
            # User then clicks 'do not want to play now', gets expired, never starts real5h decline cooldown, and invitation
            # repeats after5min. With genuine-message time, button-only activity never advances
            # past delivered_at, leaving pending until explicit button click / speech.
            _voice_entry_advance = _advance_mini_game_invite_entry(
                lanlan_name,
                getattr(mgr, "last_user_message_time", None),
            )
            # advance implicitly dismissed → push WS to clear prompt UI consistently across windows.
            # Codex P2: nonbutton paths omitted WS, leaving UI stuck.
            if _voice_entry_advance is not None:
                await push_mini_game_invite_resolved(
                    mgr,
                    session_id=_voice_entry_advance.session_id,
                    action=_voice_entry_advance.action,
                )
            can_start_proactive = mgr.state.can_start_proactive(session=probe_session)
            entry_result = _decide_busy_entry_guard(
                can_start_proactive,
                state_snapshot=(None if can_start_proactive else mgr.state.snapshot()),
            )
            if entry_result is not None:
                logger.info(
                    "[%s] 主动搭话本轮未发起：语音模式 AI 正在响应中（409）",
                    lanlan_name,
                )
                return ProactiveChatResult(
                    body=entry_result.body,
                    status_code=entry_result.status_code,
                )
            voice_language = _resolve_proactive_locale(command, mgr, fmt="full")
            delivered = await mgr.trigger_voice_proactive_nudge(
                language=voice_language,
            )
            if delivered:
                # Chat counter for1h+10 chats cooldown: voice nudge counts as proactive delivery,
                # symmetric with text path count after _record_proactive_chat.
                _mini_game_invite_count_post_response_chat(lanlan_name)
                # Persist cumulative successful proactive deliveries for force-first.
                await _increment_proactive_chat_total(
                    lanlan_name, **state_storage_kwargs
                )
            else:
                logger.info(
                    "[%s] 主动搭话本轮未发起：语音 nudge 被 guard 跳过", lanlan_name
                )
            # No Focus cooldown here: a voice nudge is realtime and never runs a
            # Focus thinking-on reply, so it is not a Focus proactive turn — the
            # cooldown is applied only at the text Phase-2 idle path (which is
            # where _focus_idle_thinking actually gates thinking-on).
            if delivered:
                return ProactiveChatResult(
                    body=_proactive_chat_body(
                        PROACTIVE_REASON_CHAT_DELIVERED,
                        message="voice proactive triggered",
                    )
                )
            return ProactiveChatResult(
                body=_proactive_pass_body(
                    PROACTIVE_REASON_PASS_BUSY,
                    message="voice proactive skipped (guard)",
                )
            )

        # ========== Text-mode proactive: atomic check + claim ==========
        # try_start_proactive holds _write_lock across can_start_proactive check,
        # IDLE→PHASE1, and subscription dispatch, preventing concurrent requests from both passing
        # then firing PROACTIVE_START into PHASE1 simultaneously.
        from main_logic.session_state import SessionEvent as _SE

        lifecycle = ProactiveLifecycle(
            mgr=mgr,
            state_event=_SE.PROACTIVE_DONE,
            lanlan_name=lanlan_name,
            log=logger,
            screenshot_request_id=uuid4().hex,
        )
        proactive_started = await mgr.state.try_start_proactive(
            session=probe_session, round_id=lifecycle.screenshot_request_id,
        )
        entry_result = _decide_busy_entry_guard(
            proactive_started,
            state_snapshot=(None if proactive_started else mgr.state.snapshot()),
        )
        if entry_result is not None:
            logger.info(
                "[%s] 主动搭话本轮未发起：AI 正在响应或已有一轮在跑（409）", lanlan_name
            )
            return ProactiveChatResult(
                body=entry_result.body,
                status_code=entry_result.status_code,
            )

        mgr._proactive_screenshot_request_id = lifecycle.screenshot_request_id

        async def _end_proactive(
            result: ProactiveChatResult,
        ) -> ProactiveChatResult:
            """Finalize every normal/short-circuit service exit."""
            return await lifecycle.finalize(result)

        def _round_preempted() -> bool:
            return not lifecycle.owns_round() or mgr.state.is_proactive_preempted()

        def _proactive_preempted_json(where: str) -> dict:
            # Fine-grained state snapshots stay DEBUG; unified _end_proactive emits troubleshooting
            # info with skipped-round reason from message (all these dicts return through it),
            # avoiding duplicate skip info in one round.
            logger.debug(
                "[%s] proactive %s preempted by user takeover (state=%s)",
                lanlan_name,
                where,
                mgr.state.snapshot(),
            )
            return {
                "success": True,
                "action": "pass",
                "reason_code": PROACTIVE_REASON_DELIVERY_PREEMPTED,
                "message": f"proactive {where} preempted by user takeover",
            }

        print(f"[{lanlan_name}] 开始主动搭话流程（两阶段架构）...")

        # ========== Fetch user-activity snapshot ==========
        # Fetch before enabled_modes parsing because propensity may narrow
        # enabled_modes to vision only (restricted_screen_only).
        # See docs/design/user-activity-tracker.md.
        #
        # Privacy mode temporarily disables all user-activity-tracker and
        # restores pre-PR#1015 unrestricted policy. Leave snapshot None; downstream gating
        # already designed fallbacks on snapshot is not None:
        #   - restricted_screen_only propensity narrowing → disabled.
        #   - reflection/reminiscence _allow_reminiscence → allowed by default.
        #   - state_section rendering → empty string.
        #   - mark_unfinished_thread_used → no counting.
        # Thus setting snapshot None suffices, equivalent to no tracker.
        from utils.preferences import ais_privacy_mode_enabled

        try:
            privacy_mode = await ais_privacy_mode_enabled()
        except Exception as _pm_err:
            # Fail closed: unreadable means privacy enabled. Ordinary disabled privacy makes
            # ais_privacy_mode_enabled return False without entering this except.
            logger.warning(
                f"[{lanlan_name}] privacy mode check failed, defaulting to enabled: {_pm_err}",
            )
            privacy_mode = True
        if not _should_fetch_activity_snapshot(privacy_mode):
            print(
                f"[{lanlan_name}] 隐私模式开启，跳过 activity tracker，按无限制策略搭话"
            )
            activity_snapshot = None
        else:
            try:
                activity_snapshot = await mgr._activity_tracker.get_snapshot()
                print(
                    f"[{lanlan_name}] activity snapshot: state={activity_snapshot.state} "
                    f"propensity={activity_snapshot.propensity} reasons={activity_snapshot.propensity_reasons} "
                    f"skip_prob={activity_snapshot.skip_probability:.2f} tone={activity_snapshot.tone}"
                )
            except Exception as _act_err:
                logger.warning(
                    f"[{lanlan_name}] activity snapshot fetch failed: {_act_err}; falling back to open propensity"
                )
                activity_snapshot = None

        # Immediately advance mini-game invite response state on proactive_chat entry:
        # even without a new invitation, prior pending invitation must mark responded after user speech,
        # or cooldown stays pending forever. Text derives last_user_msg_at from activity_snapshot;
        # voice fast path independently calls above in voice block
        # using mgr.last_user_activity_time, keeping symmetry.
        _text_last_user_msg_at = _last_user_message_at_from_activity(
            activity_snapshot,
        )
        _text_entry_advance = _advance_mini_game_invite_entry(
            lanlan_name,
            _text_last_user_msg_at,
        )
        # Push WS on implicit dismiss (symmetric voice fast path, codex P2).
        if _text_entry_advance is not None:
            await push_mini_game_invite_resolved(
                mgr,
                session_id=_text_entry_advance.session_id,
                action=_text_entry_advance.action,
            )

        # User toggle: frontend CHAT_MODE_CONFIG's ``proactiveMiniGameInviteEnabled``
        # passes through request body ``mini_game_invite_enabled``, default True for
        # legacy clients. Evaluate before _debug_force_invite so disabled user toggle with
        # enabled server debug flag retains upstream early-return gates (closed / skip_probability),
        # rather than flag bypassing suppression too.
        # CodeRabbit Major: original checked toggle only in _maybe_deliver_mini_game_invite,
        # after flag already bypassed upstream gates; toggle returned None there and caller
        # continued ordinary source picking, leaking through blocked contexts.
        _user_invite_toggle = command.mini_game_invite_enabled

        # Non-None ``MINI_GAME_INVITE_FORCE_GAME_TYPE`` bypasses all upstream
        # early-return gates (closed / skip_probability / restricted_screen_only),
        # ensuring ``_maybe_deliver_mini_game_invite`` receives the call each round:
        # enabling forces this specific mini-game. Local manual testing only; production
        # has ``MINI_GAME_INVITE_ENABLED`` master switch plus default-None flag as safeguards.
        # User toggle off disables flag, matching toggle check at _maybe_deliver_mini_game_invite
        # entry; frontend toggle is sole authority.
        # CodeRabbit Major: checking outside ``_maybe_deliver_mini_game_invite`` is necessary,
        # since upstream gates already passed inside, too late to guarantee forced delivery.
        _debug_force_invite = (
            MINI_GAME_INVITE_FORCE_GAME_TYPE is not None and _user_invite_toggle
        )

        # ========== Hard short-circuit: propensity=closed ==========
        # ``private`` state pins propensity to ``closed`` (see
        # main_logic/activity/snapshot.py). Skip everything — no LLM,
        # no source fetch, no prompt assembly. The user is in a
        # password manager / banking app / etc and we promised not to
        # look. Bypassed for the unfinished_thread override is
        # deliberate: if the AI just asked a question, hanging on it
        # mid-private is rude. closed > thread.
        activity_result = _decide_closed_activity_gate(
            activity_snapshot,
            debug_force_invite=_debug_force_invite,
        )
        if activity_result is not None:
            print(
                f"[{lanlan_name}] propensity=closed (state={activity_snapshot.state}), 跳过本轮 proactive"
            )
            return await _end_proactive(
                ProactiveChatResult(
                    body=activity_result.body,
                    status_code=activity_result.status_code,
                )
            )

        # ========== Screen-only: fixed interval + backend jitter ==========
        # During gaming / focused_work (restricted_screen_only), ordinary frontend
        # three-tier backoff exponentially increases intervals, conflicting with companionship:
        # longest user sessions become quietest. Instead:
        #   1. Frontend resets backoffLevel=0 and triggers evenly at baseInterval,
        #      notified by next_schedule_fixed_mode=True in response.
        #   2. Backend sleeps uniform(0,0.5*baseInterval) before LLM call,
        #      distributing actual intervals uniformly over [base,1.5*base].
        # Overall screen-state mean ≈1.25*base with natural random jitter.
        # Retain skip_probability independently (immersive_horror only,0.3).
        #
        # Warning: separate flag from sleep; anti_slack_pending / work_break_pending
        # are time-sensitive must-fire focused_work reminders (next section4425+),
        # so never delay with random jitter. Still set frontend fixed_mode,
        # or must-fire _end_proactive returns
        # next_schedule_fixed_mode=False, wrongly restoring tier backoff and
        # suppressing messages again after must-fire state ends.
        # Codex P2 + CodeRabbit Major review。
        activity_schedule = _decide_activity_schedule(
            activity_snapshot,
            base_interval_seconds=command.base_interval_seconds,
        )
        lifecycle.set_fixed_schedule(activity_schedule.fixed_mode)
        if activity_schedule.fixed_mode:
            if activity_schedule.has_must_fire:
                print(
                    f"[{lanlan_name}] propensity=restricted_screen_only 但有 must-fire 提醒待发，跳过本轮抖动 sleep"
                )
            elif activity_schedule.jitter_max > 0:
                _jitter = random.uniform(0.0, activity_schedule.jitter_max)
                print(
                    f"[{lanlan_name}] propensity=restricted_screen_only, "
                    f"后端注入 {_jitter:.2f}s 间隔抖动"
                    f"（base={activity_schedule.base_interval:.1f}s）"
                )
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
                # Break-reminder consumers are prompt dicts (reminder templates,
                # WORK_BREAK_* fallback labels, _loc separators), requiring prompt
                # keys instead of short codes or zh-TW rows are unreachable (issue#2500).
                _break_lang = _resolve_proactive_locale(command, mgr, fmt="prompt")
                # Invitation labels normalize again via normalize_mini_game_invite_locale;
                # full locale also resolves to zh-TW there, so retain full value.
                _break_invite_lang = _resolve_proactive_locale(
                    command, mgr, fmt="full"
                )
            except Exception:
                _break_lang = "zh"
                _break_invite_lang = "zh"

            # Resolve character_prompt up front and prepend it to every
            # break-reminder SystemMessage. Without this the model would
            # see only the env-notice template and lose its persona —
            # CodeRabbit Major review (PR #1226). Mirrors the
            # placeholder substitution the normal Phase 2 path does at
            # line ~5300 (LANLAN_NAME / MASTER_NAME).
            _break_character_prompt = lanlan_prompt_map.get(lanlan_name, "")
            if _break_character_prompt:
                _break_character_prompt = _break_character_prompt.replace(
                    "{LANLAN_NAME}", lanlan_name
                ).replace("{MASTER_NAME}", master_name_current)

            # Anti-slack first — single-behavior 'back to work' nudge.
            if activity_snapshot.anti_slack_pending is not None:
                anti_pending = activity_snapshot.anti_slack_pending
                anti_prompt = _render_anti_slack_prompt(
                    pending=anti_pending,
                    master_name=master_name_current,
                    lang=_break_lang,
                )
                reminder_delivery = await _deliver_break_reminder_via_llm(
                    lanlan_name=lanlan_name,
                    mgr=mgr,
                    config_manager=break_config_manager_provider(),
                    system_prompt=_compose_break_system_prompt(
                        _break_character_prompt,
                        anti_prompt,
                    ),
                    channel="anti_slack",
                    lang=_break_lang,
                    round_id=lifecycle.screenshot_request_id,
                )
                delivered_text = reminder_delivery.delivered_text
                if reminder_delivery.repeat_suppressed:
                    _consume_repeat_suppressed_break_reminder(
                        mgr,
                        lanlan_name=lanlan_name,
                        channel="anti_slack",
                    )
                    return await _end_proactive(
                        ProactiveChatResult(
                            body={
                                "success": True,
                                "action": "pass",
                                "reason_code": PROACTIVE_REASON_PASS_DUPLICATE,
                                "message": "anti-slack reminder suppressed as repeated",
                            }
                        )
                    )
                if delivered_text:
                    try:
                        mgr._activity_tracker.mark_anti_slack_used()
                    except Exception as _mark_err:
                        logger.warning(
                            "[%s] mark_anti_slack_used failed: %s",
                            lanlan_name,
                            _mark_err,
                        )
                    # Mini-game cooldown counter — same contract as the
                    # normal text proactive path at ~6253: any successful
                    # proactive emission counts as one of the "10 chats
                    # since user responded" gate. No-op when no prior
                    # invite is pending. Codex/CodeRabbit Minor: PR #1226.
                    _mini_game_invite_count_post_response_chat(lanlan_name)
                    await _increment_proactive_chat_total(
                        lanlan_name, **state_storage_kwargs
                    )
                    return await _end_proactive(
                        ProactiveChatResult(
                            body={
                                "success": True,
                                "action": "chat",
                                "reason_code": PROACTIVE_REASON_CHAT_DELIVERED,
                                "message": "anti-slack reminder delivered",
                                "channel": "anti_slack",
                            }
                        )
                    )
                # Delivery rejected (user took over / config issue).
                # Don't fall through to normal proactive — must-fire
                # semantics: leave pending armed for the next round.
                return await _end_proactive(
                    ProactiveChatResult(
                        body={
                            "success": True,
                            "action": "pass",
                            "reason_code": PROACTIVE_REASON_DELIVERY_PREEMPTED,
                            "message": "anti-slack reminder pending but delivery skipped",
                        }
                    )
                )

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
                from main_logic.activity.tracker import (
                    _WORK_BREAK_GAME_INVITE_PROBABILITY as _gi_prob_default,
                )

                _gi_prob = _gi_prob_default
            branch_game_invite = False
            chosen_game_type: str | None = None
            gi_prompt: str | None = None
            if MINI_GAME_INVITE_ENABLED and _user_invite_toggle and _gi_prob > 0:
                import random as _random

                if _random.random() < _gi_prob:
                    chosen_game_type = _pick_mini_game_type(lanlan_name, manager=mgr)
                    if chosen_game_type is not None:
                        gi_prompt = _render_work_break_game_invite_prompt(
                            pending=water_pending,
                            game_type=chosen_game_type,
                            master_name=master_name_current,
                            lang=_break_lang,
                        )
                        if gi_prompt is not None:
                            branch_game_invite = True

            if (
                branch_game_invite
                and chosen_game_type is not None
                and gi_prompt is not None
            ):
                reminder_delivery = await _deliver_break_reminder_via_llm(
                    lanlan_name=lanlan_name,
                    mgr=mgr,
                    config_manager=break_config_manager_provider(),
                    system_prompt=_compose_break_system_prompt(
                        _break_character_prompt,
                        gi_prompt,
                    ),
                    channel="work_break_game_invite",
                    lang=_break_lang,
                    round_id=lifecycle.screenshot_request_id,
                )
                delivered_text = reminder_delivery.delivered_text
                if reminder_delivery.repeat_suppressed:
                    _consume_repeat_suppressed_break_reminder(
                        mgr,
                        lanlan_name=lanlan_name,
                        channel="work_break_game_invite",
                    )
                    return await _end_proactive(
                        ProactiveChatResult(
                            body={
                                "success": True,
                                "action": "pass",
                                "reason_code": PROACTIVE_REASON_PASS_DUPLICATE,
                                "message": "work-break game invite suppressed as repeated",
                            }
                        )
                    )
                if delivered_text:
                    invite_session_id = str(uuid4())
                    _mini_game_invite_record_delivered(lanlan_name, invite_session_id)
                    _mini_game_invite_get_state(lanlan_name)["last_game_type"] = (
                        chosen_game_type
                    )
                    # Persist counter+1 + ever_delivered atomically (mini-game cooldown
                    # contract). Track success so we can fall back to the plain
                    # _increment_proactive_chat_total if persistence fails — otherwise
                    # the chat-total counter would skip this round entirely.
                    # CodeRabbit Major: don't double-count — the persistent record
                    # already does the +1, so plain counter is only the fallback.
                    _persist_ok = False
                    try:
                        await _record_invite_delivery_persistent(
                            lanlan_name, **state_storage_kwargs
                        )
                        _persist_ok = True
                    except Exception as _persist_err:
                        logger.warning(
                            "[%s] record_invite_delivery_persistent failed: %s",
                            lanlan_name,
                            _persist_err,
                        )
                    try:
                        from utils.instrument import counter as _instr_counter

                        # Share mini_game_invited with proactive channel, differentiated by channel;
                        # persistence success does not matter: delivered invitation UI counts.
                        _instr_counter(
                            "mini_game_invited",
                            game_type=str(chosen_game_type)[:24],
                            channel="work_break",
                            force_first=False,
                        )
                    except Exception:
                        # Telemetry is best effort; failure does not affect invitation delivery.
                        pass
                    options_payload = _build_mini_game_invite_options_payload(
                        invite_lang=_break_invite_lang,
                        game_type=chosen_game_type,
                        session_id=invite_session_id,
                    )
                    try:
                        await push_mini_game_invite_options(mgr, options_payload)
                    except Exception as _ws_err:
                        logger.warning(
                            "[%s] work_break+game_invite options WS push failed: %s",
                            lanlan_name,
                            _ws_err,
                        )
                    try:
                        mgr._activity_tracker.mark_work_break_used()
                    except Exception as _mark_err:
                        logger.warning(
                            "[%s] mark_work_break_used failed: %s",
                            lanlan_name,
                            _mark_err,
                        )
                    if not _persist_ok:
                        # Persistence path failed → counter wasn't bumped.
                        # Fall back to the plain in-memory increment so the
                        # round still counts toward proactive_chat totals.
                        await _increment_proactive_chat_total(
                            lanlan_name, **state_storage_kwargs
                        )
                    return await _end_proactive(
                        ProactiveChatResult(
                            body={
                                "success": True,
                                "action": "chat",
                                "reason_code": PROACTIVE_REASON_CHAT_DELIVERED,
                                "message": "work-break + game-invite delivered",
                                "channel": "work_break_game_invite",
                                "game_type": chosen_game_type,
                                "invite_session_id": invite_session_id,
                            }
                        )
                    )
                # Combo branch delivery failed → don't fall through to
                # regular drink branch (would double-charge the user's
                # attention). Pending stays armed for next round.
                return await _end_proactive(
                    ProactiveChatResult(
                        body={
                            "success": True,
                            "action": "pass",
                            "reason_code": PROACTIVE_REASON_DELIVERY_PREEMPTED,
                            "message": "work-break + game-invite pending but delivery skipped",
                        }
                    )
                )

            # Regular drink/stretch nudge branch
            wb_prompt, wb_seed = _render_work_break_prompt(
                pending=water_pending,
                master_name=master_name_current,
                lang=_break_lang,
            )
            reminder_delivery = await _deliver_break_reminder_via_llm(
                lanlan_name=lanlan_name,
                mgr=mgr,
                config_manager=break_config_manager_provider(),
                system_prompt=_compose_break_system_prompt(
                    _break_character_prompt,
                    wb_prompt,
                ),
                channel="work_break",
                lang=_break_lang,
                round_id=lifecycle.screenshot_request_id,
            )
            delivered_text = reminder_delivery.delivered_text
            if reminder_delivery.repeat_suppressed:
                _consume_repeat_suppressed_break_reminder(
                    mgr,
                    lanlan_name=lanlan_name,
                    channel="work_break",
                )
                return await _end_proactive(
                    ProactiveChatResult(
                        body={
                            "success": True,
                            "action": "pass",
                            "reason_code": PROACTIVE_REASON_PASS_DUPLICATE,
                            "message": "work-break reminder suppressed as repeated",
                        }
                    )
                )
            if delivered_text:
                try:
                    mgr._activity_tracker.mark_work_break_used()
                except Exception as _mark_err:
                    logger.warning(
                        "[%s] mark_work_break_used failed: %s",
                        lanlan_name,
                        _mark_err,
                    )
                # Same chats-since-response counter as anti_slack branch.
                _mini_game_invite_count_post_response_chat(lanlan_name)
                await _increment_proactive_chat_total(
                    lanlan_name, **state_storage_kwargs
                )
                return await _end_proactive(
                    ProactiveChatResult(
                        body={
                            "success": True,
                            "action": "chat",
                            "reason_code": PROACTIVE_REASON_CHAT_DELIVERED,
                            "message": "work-break reminder delivered",
                            "channel": "work_break",
                            "seed": wb_seed,
                        }
                    )
                )
            return await _end_proactive(
                ProactiveChatResult(
                    body={
                        "success": True,
                        "action": "pass",
                        "reason_code": PROACTIVE_REASON_DELIVERY_PREEMPTED,
                        "message": "work-break reminder pending but delivery skipped",
                    }
                )
            )

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
        activity_result = _decide_probabilistic_activity_gate(
            activity_snapshot,
            debug_force_invite=_debug_force_invite,
        )
        if activity_result is not None:
            print(
                f"[{lanlan_name}] skip_probability={activity_snapshot.skip_probability:.2f} "
                f"rolled (state={activity_snapshot.state} intensity={activity_snapshot.game_intensity} "
                f"genre={activity_snapshot.game_genre})，本轮跳过"
            )
            return await _end_proactive(
                ProactiveChatResult(
                    body=activity_result.body,
                    status_code=activity_result.status_code,
                )
            )

        # ========== Parse enabled_modes ==========
        # Legacy frontend: absent ``enabled_modes`` inferred from other fields; explicit ``[]``
        # from newer clients means all source toggles off, never invoking compatibility fallback
        # to home/trending (with mini-game toggle alone, a missed die roll would otherwise
        # activate home fallback against user toggle contract; codex P1).
        source_mode_selection = _select_source_modes(
            command,
            activity_snapshot,
            debug_force_invite=_debug_force_invite,
        )
        enabled_modes = source_mode_selection.enabled_modes
        _has_unfinished_thread = source_mode_selection.has_unfinished_thread

        # restricted_screen_only: gaming/focused_work permits screen channel only.
        # Narrow enabled_modes to vision; if frontend did not enable vision this round,
        # pass directly: no visuals and external topics forbidden, so nothing to do.
        # Exception: unfinished thread (AI question unanswered within5min) may proceed
        # without vision, as following up needs no external material.
        if source_mode_selection.restricted_to_vision:
            print(
                f"[{lanlan_name}] propensity=restricted_screen_only, 收紧 enabled_modes 到仅 vision"
            )
        elif source_mode_selection.text_only_followup:
            print(
                f"[{lanlan_name}] propensity=restricted_screen_only 但有未收尾话题，允许 text-only 跟进"
            )
        elif source_mode_selection.result is not None:
            return await _end_proactive(
                ProactiveChatResult(
                    body=source_mode_selection.result.body,
                    status_code=source_mode_selection.result.status_code,
                )
            )

        print(f"[{lanlan_name}] 启用的搭话模式: {enabled_modes}")

        # ========== Mini-game invitation short circuit ==========
        # After propensity / skip_probability / restricted_screen_only gates,
        # independently roll10%; on hit deliver static i18n invitation, skipping Phase1/2
        # LLM and source fetching. Response starts24h+10 chats cooldown,
        # during which no dice. activity_snapshot None (privacy / unavailable tracker)
        # conservatively sends nothing, unable to identify work state.
        try:
            # fmt="full": invitation and all three labels use prompt dicts with zh-TW rows;
            # short codes fold zh-TW into zh, losing those rows (issue#2500).
            invite_lang = _resolve_proactive_locale(command, mgr, fmt="full")
        except Exception:
            invite_lang = "zh"
        # _user_invite_toggle already computed before _debug_force_invite above:
        # disabled toggle also disables flag, preserving early-return gates.
        invite_short_circuit = await run_mini_game_invite_short_circuit(
            lanlan_name=lanlan_name,
            mgr=mgr,
            activity_snapshot=activity_snapshot,
            invite_lang=invite_lang,
            master_name=master_name_current,
            user_toggle_enabled=_user_invite_toggle,
            round_id=lifecycle.screenshot_request_id,
            **state_storage_kwargs,
        )
        if invite_short_circuit is not None:
            if invite_short_circuit.options_payload is not None:
                try:
                    await push_mini_game_invite_options(
                        mgr,
                        invite_short_circuit.options_payload,
                    )
                except Exception as _ws_err:
                    logger.warning(
                        "[%s] mini-game invite options WS push failed: %s",
                        lanlan_name,
                        _ws_err,
                    )
            return await _end_proactive(
                ProactiveChatResult(
                    body=invite_short_circuit.result.body,
                    status_code=invite_short_circuit.result.status_code,
                )
            )

        # All source toggles off (mini-game's independent toggle alone triggered request),
        # but invitation short circuit missed: nothing to discuss. Pass directly rather than
        # source-picking an empty list / 'all sources failed'500. Exception: unfinished
        # thread lets Phase2 use text-only follow-up, matching fallback with
        # sources={} and an existing thread. Codex P1: compatibility fallback already distinguishes
        # missing field from explicit[], so exit clearly for explicit empty.
        source_result = _decide_empty_source_gate(
            enabled_modes,
            has_unfinished_thread=_has_unfinished_thread,
        )
        if source_result is not None:
            print(
                f"[{lanlan_name}] enabled_modes 空 + mini-game miss + 无 unfinished_thread → pass"
            )
            return await _end_proactive(
                ProactiveChatResult(
                    body=source_result.body,
                    status_code=source_result.status_code,
                )
            )

        # Global source-decay history: ensure lazy memory load before picking (initial thread-pool
        # IO, later O(1) flag checks). Synchronous picker reads dict directly.
        await _ensure_source_history_loaded(**state_storage_kwargs)

        # ========== 0. Fetch all sources concurrently (no LLM) ==========
        vision_consent_check = _proactive_vision_consent_checker(mgr, vision_consent_epoch)
        enabled_modes, sources, vision_sources_withheld = (
            await _collect_sources_under_vision_consent(
                command=command,
                enabled_modes=enabled_modes,
                lanlan_name=lanlan_name,
                vision_consent_check=vision_consent_check,
            )
        )
        avatar_position = command.avatar_position

        if not sources:
            # Exception: unfinished-thread mode may already clear enabled_modes (restricted_screen_only
            # without vision), making sources empty; do not pass, let Phase2 use dialogue
            # history + state_section for text-only [CHAT] follow-up.
            if not _has_unfinished_thread and vision_sources_withheld:
                # Consent revocation removed every source: skip for privacy, not because of a technical failure.
                return await _end_proactive(
                    ProactiveChatResult(
                        body=_proactive_pass_body(
                            PROACTIVE_REASON_PASS_PRIVACY,
                            message="vision consent revoked; no other source",
                        ),
                    )
                )
            if not _has_unfinished_thread:
                return await _end_proactive(
                    ProactiveChatResult(
                        body=_proactive_pass_body(
                            PROACTIVE_REASON_ERROR_SOURCE_FETCH_FAILED,
                            success=False,
                            error="所有信息源获取失败",
                        ),
                        status_code=500,
                    )
                )
            print(
                f"[{lanlan_name}] sources 为空但有未收尾话题，进入 text-only 跟进路径"
            )

        # Phase1 preemption check after concurrent source fetch, before LLM.
        if _round_preempted():
            return await _end_proactive(
                ProactiveChatResult(body=_proactive_preempted_json("phase1_post_fetch"))
            )

        print(
            f"[{lanlan_name}] 成功获取 {len(sources)} 个信息源: {list(sources.keys())}"
        )

        # ========== 1. Fetch memory context (New Dialog) ==========
        # new_dialog response format:
        # ========以下是{name}的内心活动========
        # {Inner thoughts/Settings}...
        # Current time...recent events summarized.
        # Name | Content
        # ...

        raw_memory_context = ""
        try:
            # fmt="prompt": proactive_lang feeds Phase1/2 templates, screen/external/
            # music/meme sections, recent proactive records, action notes: all prompt dicts.
            proactive_lang = _resolve_proactive_locale(command, mgr, fmt="prompt")
        except Exception:
            proactive_lang = "zh"
        topic_hook_lang = _resolve_topic_hook_locale(
            command,
            mgr,
            fallback=proactive_lang,
        )
        try:
            from utils.internal_http_client import get_internal_http_client

            _pt_client = get_internal_http_client()
            resp = await _pt_client.get(
                f"http://127.0.0.1:{MEMORY_SERVER_PORT}/new_dialog/{lanlan_name}",
                params=_new_dialog_locale_params(command, mgr),
                timeout=5.0,
            )
            resp.raise_for_status()  # Check for HTTP errors explicitly
            if resp.status_code == 200:
                raw_memory_context = resp.text
            else:
                logger.warning(
                    f"[{lanlan_name}] 记忆服务返回非200状态: {resp.status_code}，使用空上下文"
                )
        except Exception as e:
            logger.warning(f"[{lanlan_name}] 获取记忆上下文失败，使用空上下文: {e}")

        # Parse new_dialog by separating inner thoughts from dialogue history.
        # Locale-independent split logic lives in prompts_memory.split_inner_thoughts_and_history,
        # based on multilingual INNER_THOUGHTS_DYNAMIC templates; no locale match returns
        # None, falling back here to all-history/empty-thoughts with warning instead of silent misalignment.
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

        # Phase1 preemption check: memory_server new_dialog is first large await in Phase1
        # (httpx5s timeout). User interruption during it waits until timeout for next check,
        # so add check here.
        if _round_preempted():
            return await _end_proactive(
                ProactiveChatResult(
                    body=_proactive_preempted_json("phase1_post_memory")
                )
            )

        # ========== 2. Choose language ==========
        # Same authority as mini-game short circuit: request body → mgr.user_language → global cache.
        # See _resolve_proactive_locale docstring.
        # ========== 3. Inject recent proactive records ==========
        proactive_chat_history_prompt = _format_recent_proactive_chats(
            lanlan_name, proactive_lang
        )

        # Compute open_threads concurrently with Phase1 unified LLM below.
        # Cache invalidates by user-message sequence; without new user speech return immediately. Phase2's
        # snapshot uses these results if ready, otherwise prior cache.
        try:
            mgr._activity_tracker.kickoff_open_threads_compute(lang=topic_hook_lang)
        except Exception as _ot_err:
            logger.debug(
                f"[{lanlan_name}] kickoff_open_threads_compute failed: {_ot_err}"
            )

        # ========== 3.5 Reflection + callback topics (memory_server API) ==========
        # Cognitive framework: Facts → Reflection(pending) → natural proactive mention → feedback → Persona.
        #
        # Avoid awkward reminiscence during gaming/focused_work; skip whole section,
        # also saving reflect POST's15s timeout risk. stale_returning welcomes reminiscence.
        followup_topics_prompt = ""
        _followup_topics = []
        _surfaced_reflection_ids = []  # Track pending reflections mentioned this turn.
        _allow_reminiscence = (
            activity_snapshot is None
            or activity_snapshot.propensity != "restricted_screen_only"
        )
        if not _allow_reminiscence:
            print(
                f"[{lanlan_name}] propensity=restricted_screen_only, 跳过反思/回忆话题获取"
            )
        # Reuse internal_http_client singleton; every proactive turn uses this path.
        # Read-only: fetch followup_topics candidates for current prompt injection.
        # Previously called POST /reflect/{name} first for automatic state transitions/synthesis;
        # removed after synthesis moved to ``_periodic_reflection_synthesis_loop``,
        # while ``_periodic_auto_promote_loop`` already runs auto_promote every180s. Keeping
        # mutation on proactive critical path delayed response~15s and coupled entire
        # reflection lifecycle to frontend setTimeout (no frontend means no synthesis).
        if _allow_reminiscence:
            try:
                from utils.internal_http_client import get_internal_http_client

                _mem_base = f"http://127.0.0.1:{MEMORY_SERVER_PORT}"
                _mem_client = get_internal_http_client()
                _topics_resp = await _mem_client.get(
                    f"{_mem_base}/followup_topics/{lanlan_name}",
                    timeout=5.0,
                )
                if _topics_resp.status_code == 200:
                    _followup_topics = _topics_resp.json().get("topics", [])
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
                            logger.debug(
                                f"[{lanlan_name}] followup topic prompt build failed: {_followup_prompt_err}"
                            )
                        print(
                            f"[{lanlan_name}] 回调话题候选: {len(_followup_topics)} 条"
                        )
            except Exception as e:
                logger.debug(f"[{lanlan_name}] 回调话题获取失败（不影响主流程）: {e}")

        # Phase1 preemption: followup GET(5s) may await long enough that interruption otherwise
        # still runs LLM configuration and later work until pre-LLM
        # check. Recheck here.
        if _round_preempted():
            return await _end_proactive(
                ProactiveChatResult(
                    body=_proactive_preempted_json("phase1_post_reflect")
                )
            )

        # ========== 4. Fetch LLM configuration ==========
        # Entire proactive pipeline (Phase1 selection /Phase2 generation /regen) uses conversation tier,
        # not correction: without thinking, correction models struggle to consistently
        # put source tag on first line and may output persona-constraint blocks. Conversation
        # main model follows format more reliably. Retain disable_thinking (vision+thinking times out).
        try:
            # One fresh snapshot shared by conversation/vision; separate reads let /core_api
            # saving between reads produce a torn pair (old conversation + new vision).
            _fresh_core_config = await _config_manager.aget_core_config()
            conversation_config = await _config_manager.aget_model_api_config(
                "conversation", core_config=_fresh_core_config
            )
            conversation_model = conversation_config.get("model")
            conversation_api_key = conversation_config.get("api_key")

            if not conversation_model or not conversation_api_key:
                logger.error("对话模型配置缺失: model或api_key未设置")
                return await _end_proactive(
                    ProactiveChatResult(
                        body={
                            "success": False,
                            "reason_code": PROACTIVE_REASON_ERROR_INTERNAL,
                            "error": "对话模型配置缺失",
                            "detail": "请在设置中配置对话模型的model和api_key",
                        },
                        status_code=500,
                    )
                )

            vision_config = await _config_manager.aget_model_api_config(
                "vision", core_config=_fresh_core_config
            )
            model_config = ProactiveModelConfig(
                conversation_model=conversation_model,
                conversation_base_url=conversation_config.get("base_url"),
                conversation_api_key=conversation_api_key,
                conversation_provider_type=conversation_config.get("provider_type"),
                vision_model=vision_config.get("model", ""),
                vision_base_url=vision_config.get("base_url", ""),
                vision_api_key=vision_config.get("api_key", ""),
                vision_provider_type=vision_config.get("provider_type"),
                vision_is_custom=bool(vision_config.get("is_custom")),
            )
            if not model_config.has_vision_model:
                logger.info("Vision 模型未配置，Phase 2 将退回使用对话模型")
        except Exception as e:
            logger.error(f"获取模型配置失败: {e}")
            return await _end_proactive(
                ProactiveChatResult(
                    body={
                        "success": False,
                        "reason_code": PROACTIVE_REASON_ERROR_INTERNAL,
                        "error": "模型配置异常",
                        "detail": str(e),
                    },
                    status_code=500,
                )
            )

        # ================================================================
        # Phase1: combined LLM call (web selection + music keywords + meme keywords).
        # Warning: never analyze screen in Phase1; Phase2 feeds screenshots directly to vision_model.
        # - Combine all text sources into one web-selection/music/meme-keyword LLM call.
        # - Dynamic source weights exclude low-weight channels before LLM.
        # At most one LLM call total.
        # ================================================================

        # Consent may be revoked while waiting for memory or follow-ups after collection; screen/window content must not enter any model call.
        await _withhold_revoked_vision_sources(
            sources, vision_consent_check, lanlan_name=lanlan_name, stage="Phase 1 前",
        )
        # This round uses screen/window content: recheck consent before every subsequent model call and the final commit.
        round_uses_vision_material = bool(_PROACTIVE_VISION_SOURCE_MODES.intersection(sources))
        vision_content = sources.get("vision")  # Retain only for Phase2; Phase1 does not process it.
        music_content = sources.get("music")
        meme_content = sources.get("meme")
        logger.debug(
            f"[{lanlan_name}] 主动搭话-音乐内容: type={type(music_content)}, success={music_content.get('success') if music_content else 'N/A'}"
        )
        logger.debug(
            f"[{lanlan_name}] 主动搭话-表情包内容: type={type(meme_content)}, success={meme_content.get('success') if meme_content else 'N/A'}"
        )

        all_web_links: list[dict] = []

        # Collect music links after Phase1 web selection.
        # Memes likewise bypass Phase1 LLM selection and add topics directly.
        web_modes = [m for m in sources if m not in ("vision", "music", "meme")]
        # Personal-feed context wins when the same URL also appears elsewhere.
        web_modes.sort(key=lambda mode: 0 if mode == "personal" else 1)

        merged_web_content = ""

        # Collect Phase1 results.
        phase1_topics: list[tuple[str, str]] = []  # [(channel, topic_summary), ...]
        source_links: list[dict] = []  # [{"title": ..., "url": ..., "source": ...}]
        selected_web_link = None
        selected_web_topic_key = None
        selected_music_link = None
        selected_music_topic_key = None
        selected_meme_link = None
        selected_meme_topic_key = None

        # Hardening: while music plays or cooldown active, clear music channel completely, skipping song search.
        if is_music_occupied or is_playing_music or music_cooldown:
            if music_content:
                reason = (
                    "音乐播放器已占用"
                    if is_music_occupied
                    else "音乐正在播放"
                    if is_playing_music
                    else "用户连续秒关，音乐冷却中"
                )
                logger.debug(f"[{lanlan_name}]-{reason}，强制屏蔽 Phase 1 搜歌逻辑")
            music_content = None
            sources.pop("music", None)

        # ============================================================
        # Dynamic source-weight filtering (vision / blocked music excluded from weighting).
        #
        # ``reminiscence`` is a virtual channel: if memory_server returned pending
        # follow-up topics, add it to weighted pool. Like web/news/music,
        # decay by usage frequency; repeated reminiscence enters suppressed
        # and skips followup_topics_prompt this turn (reflection.py separately handles
        # per-reflection cooldown; this is channel-level fallback).
        # ============================================================
        source_weight_selection = _select_weighted_sources(
            lanlan_name,
            enabled_modes,
            sources,
            has_reminiscence=bool(_surfaced_reflection_ids),
        )
        if source_weight_selection.weights:
            source_weights = source_weight_selection.weights
            suppressed = source_weight_selection.suppressed
            weight_str = " ".join(f"{ch}={w:.3f}" for ch, w in source_weights.items())
            logger.debug(
                f"[{lanlan_name}] 来源权重: {weight_str} | 剔除: {suppressed or '无'}"
            )

            for ch in suppressed:
                sources.pop(ch, None)
            if "music" in suppressed:
                music_content = None
            if "meme" in suppressed:
                meme_content = None
            if "reminiscence" in suppressed:
                # Reminiscence throttled: clear old reflections only.
                # Background deep-topic pool uses independent one-shot trigger, not proactive prompt consumption.
                if followup_topics_prompt:
                    print(
                        f"[{lanlan_name}] reminiscence channel suppressed by weight, dropping followup section"
                    )
                _followup_topics = []
                _surfaced_reflection_ids = []
                followup_topics_prompt = ""


        # Build once after source suppression so candidates are decayed only once.
        web_modes = [mode for mode in web_modes if mode in sources]
        if web_modes:
            from utils.tokenize import truncate_to_tokens as _ttt

            parts = []
            fallback_modes = _phase1_linkless_modes(web_modes, sources)
            selected_by_mode = _round_robin_phase1_links(
                web_modes,
                sources,
                total=max(
                    0, _PHASE1_TOTAL_TOPIC_TARGET - len(fallback_modes)
                ),
            )
            remaining_total = _PHASE1_TOTAL_TOPIC_TARGET - sum(
                len(items) for items in selected_by_mode.values()
            )
            remaining_fallback_modes = len(fallback_modes)
            phase1_source_positions: dict[str, int] = {}
            for mode in web_modes:
                src = sources[mode]
                label_map = PROACTIVE_SOURCE_LABELS.get(
                    proactive_lang, PROACTIVE_SOURCE_LABELS["en"]
                )
                label = label_map.get(mode, mode)
                selected_links = selected_by_mode.get(mode, [])

                if selected_links:
                    all_web_links.extend(selected_links)
                    lines = []
                    for index, item in _number_phase1_links_by_source(
                        selected_links, source_positions=phase1_source_positions
                    ):
                        rendered = _ttt(
                            _format_phase1_link_candidate(index, item),
                            PROACTIVE_EXTERNAL_PER_ITEM_MAX_TOKENS,
                        )
                        rendered_title = rendered.removeprefix(f"{index}. ").split(
                            " | ", 1
                        )[0].strip()
                        if rendered_title:
                            item["phase1_rendered_title"] = rendered_title
                        lines.append(rendered)
                    if lines:
                        parts.append((f"--- {label} ---", lines))
                        continue

                # Community cards only enter Phase 1 through selected links:
                # a formatted fallback would bypass cooldown and data escaping.
                if mode == "community":
                    continue
                content_text = src.get("formatted_content", "")
                if content_text and remaining_total > 0:
                    fallback_records = _phase1_fallback_records(content_text)
                    if fallback_records:
                        is_reserved_fallback = mode in fallback_modes
                        reserve_for_later = max(
                            0,
                            remaining_fallback_modes
                            - (1 if is_reserved_fallback else 0),
                        )
                        fallback_limit = remaining_total - reserve_for_later
                        if fallback_limit <= 0:
                            continue
                        fallback_lines = [
                            _ttt(record, PROACTIVE_EXTERNAL_PER_ITEM_MAX_TOKENS)
                            for record in fallback_records[:fallback_limit]
                        ]
                        parts.append((f"--- {label} ---", fallback_lines))
                        remaining_total -= len(fallback_lines)
                        if is_reserved_fallback:
                            remaining_fallback_modes -= 1

            # Keep source sections whole so any rendered title remains exactly
            # as visible to the Phase 1 model.
            merged_web_content = _merge_phase1_parts_within_token_budget(
                parts, max_tokens=PROACTIVE_EXTERNAL_TOTAL_MAX_TOKENS
            )

        # ============================================================
        # Combine Phase1 LLM: web selection + music/meme keywords.
        # One call does all tasks, reducing RPM.
        # ============================================================
        has_music_task = bool(music_content and music_content.get("placeholder"))
        has_meme_task = bool(meme_content and meme_content.get("placeholder"))
        has_web_task = bool(merged_web_content)

        enriched_memory_context = memory_context
        if followup_topics_prompt:
            enriched_memory_context = memory_context + "\n" + followup_topics_prompt

        if has_web_task or has_music_task or has_meme_task:
            # Phase1 preemption: final check before dialing. Expensive LLM call starts next;
            # later interruption can be detected only by fallback after stream ends.
            if _round_preempted():
                return await _end_proactive(
                    ProactiveChatResult(
                        body=_proactive_preempted_json("phase1_pre_llm")
                    )
                )
        unified_parsed = await _run_unified_phase1(
            model_config=model_config,
            proactive_lang=proactive_lang,
            lanlan_name=lanlan_name,
            master_name=master_name_current,
            merged_web_content=merged_web_content,
            memory_context=enriched_memory_context,
            recent_chats_section=proactive_chat_history_prompt,
            has_music_task=has_music_task,
            has_meme_task=has_meme_task,
            log=logger,
            live_consent_check=vision_consent_check,
            # Only window content enters the combined Phase 1 text; screenshots go only to Phase 2.
            vision_consent_required="window" in sources,
        )

        # ============================================================
        # Parse web output → match links → deduplicate.
        # ============================================================
        web_parsed = unified_parsed.get("web")
        if web_parsed and web_parsed.get("title"):
            matched = _lookup_link_by_phase1_selection(web_parsed, all_web_links)
            if matched is None and _is_neko_community_phase1_source(
                web_parsed.get("source")
            ):
                print(
                    f"[{lanlan_name}] Phase 1 社区卡牌选择未匹配，跳过: "
                    f"{web_parsed.get('title', '')[:60]}"
                )
            else:
                topic_key = _source_hash(
                    (matched.get("dedupe_key") or matched.get("url", ""))
                    if matched
                    else "",
                    web_parsed.get("title", ""),
                )
                # Matched links already passed _should_skip_source during picking;
                # rolling again yields p_skip=1-(1-p)^2, violating single half-life model.
                # Recheck only unmatched hallucinated title-only candidates.
                needs_recheck = bool(topic_key) and matched is None
                if needs_recheck and _should_skip_source(topic_key):
                    print(
                        f"[{lanlan_name}] Phase 1 title-only 话题命中衰减，跳过: {web_parsed.get('title', '')[:60]}"
                    )
                else:
                    if matched:
                        selected_web_link = dict(matched)
                        canonical_title = matched.get("title", "")
                        selected_title = (
                            canonical_title
                            if matched.get("mode") == "community"
                            else web_parsed.get("title", canonical_title)
                        )
                        selected_web_link.update(
                            {
                                "title": selected_title,
                                "url": matched["url"],
                                "source": (
                                    matched.get("source", "")
                                    if matched.get("mode") == "community"
                                    else web_parsed.get(
                                        "source", matched.get("source", "")
                                    )
                                ),
                                "mode": matched.get("mode", "web"),
                            }
                        )
                        print(
                            f"[{lanlan_name}] Phase 1 链接预匹配成功: {matched.get('title', '')[:60]}"
                        )
                    else:
                        print(
                            f"[{lanlan_name}] Phase 1 未在 web_links 中匹配到标题: {web_parsed.get('title', '')[:60]}"
                        )
                    # Retain topic_key for persistence after Phase2 regardless of matching;
                    # even title-only topics decay, preventing repeatedly surfacing identical titles.
                    selected_web_topic_key = topic_key
                    # Use web_parsed summary or original text as topic.
                    web_topic_text = web_parsed.get(
                        "summary", web_parsed.get("title", "")
                    )
                    phase1_topics.append(("web", web_topic_text.strip()))

        # ============================================================
        # Parallel post-fetch: music + memes using LLM-generated keywords.
        # ============================================================
        if has_music_task and unified_parsed.get("music_pass"):
            print(f"[{lanlan_name}] Phase 1 音乐通道明确 PASS，跳过后置 fetch")
        if has_meme_task and unified_parsed.get("meme_pass"):
            print(f"[{lanlan_name}] Phase 1 表情包通道明确 PASS，跳过后置 fetch")
        needs_phase1_followups = (
            has_music_task and not unified_parsed.get("music_pass")
        ) or (has_meme_task and not unified_parsed.get("meme_pass"))
        if needs_phase1_followups:
            # Phase1 preemption check after unified LLM, before music/meme post-fetch.
            if _round_preempted():
                return await _end_proactive(
                    ProactiveChatResult(
                        body=_proactive_preempted_json("phase1_post_llm")
                    )
                )
        music_content, meme_content = await _fetch_phase1_followups(
            parsed=unified_parsed,
            has_music_task=has_music_task,
            has_meme_task=has_meme_task,
            music_content=music_content,
            meme_content=meme_content,
            proactive_lang=proactive_lang,
            lanlan_name=lanlan_name,
            log=logger,
        )

        # ============================================================
        # Assemble music topics (iterate candidates → decay skip → stage link).
        # Symmetric web/meme: overfetch N, probabilistically skip each and proceed to next on hit.
        # If all skipped, clear music_content to degrade entire channel.
        # ============================================================
        music_selection = _select_music_recommendation(
            music_content,
            lang=proactive_lang,
            source_hash=_source_hash,
            should_skip_source=_should_skip_source,
            lanlan_name=lanlan_name,
        )
        music_content = music_selection.content
        if music_selection.link:
            music_topic = music_selection.topic
            selected_music_link = music_selection.link
            selected_music_topic_key = music_selection.topic_key
            logger.debug(
                f"[{lanlan_name}]- Phase 1 音乐话题已添加 "
                f"(topic_len={len(music_topic)})"
            )
            print(f"[{lanlan_name}]- Phase 1 音乐话题: {music_topic[:100]}")
            phase1_topics.append(("music", music_topic))

        # ============================================================
        # Assemble meme topics (iterate → deduplicate → one-image limit).
        # ============================================================
        if meme_content and meme_content.get("success") and meme_content.get("data"):
            meme_data = meme_content.get("data", [])
            if meme_data:
                proxy_checked_count = 0
                for candidate_meme in meme_data:
                    meme_title = candidate_meme.get("title", "")
                    meme_url = candidate_meme.get("url", "")
                    if not meme_url:
                        continue  # Skip candidates lacking URL.
                    meme_source = candidate_meme.get("source", "表情包")
                    meme_topic_key = _source_hash(meme_url, meme_title)
                    if meme_topic_key and _should_skip_source(meme_topic_key):
                        logger.debug(
                            f"[{lanlan_name}]- Phase 1 表情包候选去重命中，跳过: {meme_title[:30]}"
                        )
                        continue
                    if _round_preempted():
                        return await _end_proactive(
                            ProactiveChatResult(
                                body=_proactive_preempted_json(
                                    "phase1_pre_meme_moderation"
                                )
                            )
                        )
                    moderation = await moderate_meme_image_url(
                        meme_url, fail_closed=False
                    )
                    if _round_preempted():
                        return await _end_proactive(
                            ProactiveChatResult(
                                body=_proactive_preempted_json(
                                    "phase1_post_meme_moderation"
                                )
                            )
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
                            kind="image",
                            title=meme_title,
                            **state_storage_kwargs,
                        )
                        logger.info(
                            "[%s]- 已记录被 moderation 拦截的表情包 source 衰减历史: url_hash=%s",
                            lanlan_name,
                            meme_topic_key[:16],
                        )
                        continue
                    if meme_proxy_candidate_fetchable is not None:
                        if (
                            proxy_checked_count
                            >= _MEME_PROXY_CANDIDATE_CHECK_LIMIT
                        ):
                            logger.info(
                                "[%s]- Phase 1 表情包代理预检达到上限(%d)，"
                                "跳过本轮 meme 通道",
                                lanlan_name,
                                _MEME_PROXY_CANDIDATE_CHECK_LIMIT,
                            )
                            break
                        if _round_preempted():
                            return await _end_proactive(
                                ProactiveChatResult(
                                    body=_proactive_preempted_json(
                                        "phase1_pre_meme_proxy_check"
                                    )
                                )
                            )
                        proxy_checked_count += 1
                        proxy_ok, proxy_reason = (
                            await meme_proxy_candidate_fetchable(meme_url)
                        )
                        if _round_preempted():
                            return await _end_proactive(
                                ProactiveChatResult(
                                    body=_proactive_preempted_json(
                                        "phase1_post_meme_proxy_check"
                                    )
                                )
                            )
                        if not proxy_ok:
                            logger.info(
                                "[%s]- Phase 1 表情包代理不可取，跳过候选: "
                                "reason=%s title=%s url=%s",
                                lanlan_name,
                                proxy_reason,
                                meme_title[:30],
                                meme_url[:100],
                            )
                            continue
                    single_meme_topic = get_meme_topic_line(
                        proactive_lang,
                        keyword=meme_content.get("keyword", ""),
                        title=meme_title,
                        source=meme_source,
                    )
                    logger.debug(
                        f"[{lanlan_name}]- Phase 1 表情包话题已添加 (限额1张): {single_meme_topic}"
                    )
                    phase1_topics.append(("meme", single_meme_topic))
                    selected_meme_link = {
                        "title": meme_title,
                        "url": meme_url,
                        "source": meme_source,
                        "type": candidate_meme.get("type", "meme"),
                    }
                    selected_meme_topic_key = meme_topic_key
                    logger.debug(f"[{lanlan_name}] 预选表情包话题: {meme_title[:30]}")
                    break
                else:
                    logger.debug(
                        f"[{lanlan_name}]- Phase 1 未选出可用表情包候选，跳过表情包话题"
                    )
            else:
                logger.warning(
                    f"[{lanlan_name}] Phase 1 表情包数据为空，跳过表情包话题"
                )

        phase1_decision = _decide_phase1_channels(
            phase1_topics,
            vision_content,
            has_unfinished_thread=_has_unfinished_thread,
        )
        if phase1_decision.result is not None:
            print(f"[{lanlan_name}] Phase 1 所有通道均无可用话题")
            return await _end_proactive(
                ProactiveChatResult(body=phase1_decision.result.body)
            )
        if not phase1_topics and not vision_content:
            print(
                f"[{lanlan_name}] Phase 1 无话题但有未收尾话题，进入 text-only 跟进 Phase 2"
            )

        # Phase1 preemption check after topic assembly, before Phase2.
        if _round_preempted():
            return await _end_proactive(
                ProactiveChatResult(body=_proactive_preempted_json("phase1_pre_phase2"))
            )

        # Collect channel results.
        active_channels = phase1_decision.active_channels
        print(
            f"[{lanlan_name}] Phase 1 结果: phase1_topics={phase1_topics}, vision_content={'有' if vision_content else '无'}"
        )
        web_topic = phase1_decision.web_topic
        music_topic = phase1_decision.music_topic
        primary_channel = phase1_decision.primary_channel
        if selected_web_link:
            if _round_preempted():
                return await _end_proactive(
                    ProactiveChatResult(
                        body=_proactive_preempted_json(
                            "phase1_pre_candidate_preparation"
                        )
                    )
                )
            try:
                selected_web_link, web_topic = await prepare_selected_web_candidate(
                    selected_web_link,
                    fallback_topic=web_topic,
                    language=proactive_lang,
                    is_preempted=_round_preempted,
                )
            except SelectedWebCandidatePreempted:
                return await _end_proactive(
                    ProactiveChatResult(
                        body=_proactive_preempted_json(
                            "phase1_candidate_preparation"
                        )
                    )
                )
            if _round_preempted():
                return await _end_proactive(
                    ProactiveChatResult(
                        body=_proactive_preempted_json(
                            "phase1_post_candidate_preparation"
                        )
                    )
                )
        print(
            f"[{lanlan_name}] Phase 1 可用通道: {active_channels}，主通道: {primary_channel}"
        )

        # ================================================================
        # Phase2: persona + two-channel information → stream proactive dialogue.
        # Warning: use vision_model and freshest screenshot before call,
        # minimizing screen-reading latency.
        # Warning: never enable thinking (disable_thinking must True),
        # or vision_model + thinking times out.
        # Warning: no retry/rewrite. Streaming anomalies abort directly; failures pass until next attempt.
        # Flow: tokens → immediate TTS → deliver full text once complete → abort interrupts both ends.
        # ================================================================

        # Fetch complete persona and replace template variables.
        character_prompt = lanlan_prompt_map.get(lanlan_name, "")
        if not character_prompt:
            logger.warning(f"[{lanlan_name}] 未找到角色人设，使用空字符串")
        character_prompt = character_prompt.replace(
            "{LANLAN_NAME}", lanlan_name
        ).replace("{MASTER_NAME}", master_name_current)

        # --- Request fresh frontend screenshot, replacing Phase1's old image ---
        screenshot_b64_for_phase2 = ""
        if vision_content and model_config.has_vision_model:
            fresh = await mgr.request_fresh_screenshot(
                timeout=3.0,
                allow_backend_fallback=command.screenshot_allow_desktop_fallback,
                request_id=lifecycle.screenshot_request_id,
            )
            if _round_preempted():
                return await _end_proactive(ProactiveChatResult(
                    body=_proactive_preempted_json("phase1_post_screenshot"),
                ))
            if fresh.b64:
                fresh_b64 = fresh.b64
                # See _resolve_phase2_avatar_position: absent WS coordinates explicitly means
                # no overlay, never reuse old request coordinates; only pyautogui fallback uses Phase1 coordinates.
                # Warning: neither request_fresh_screenshot path overlays; overlay only here.
                # Warning: known limitation: a full LLM call separates Phase1→Phase2 (seconds), so fallback's
                # reused coordinates may already be stale.
                av_pos = _resolve_phase2_avatar_position(fresh, avatar_position)
                if av_pos and isinstance(av_pos, dict):
                    try:
                        from utils.screenshot_utils import overlay_avatar_annotation

                        fresh_b64 = await asyncio.to_thread(
                            overlay_avatar_annotation,
                            fresh_b64,
                            av_pos,
                            lanlan_name,
                            proactive_lang,
                        )
                    except Exception as ann_err:
                        logger.warning(
                            f"[{lanlan_name}] Phase 2 avatar annotation failed: {ann_err}"
                        )
                screenshot_b64_for_phase2 = fresh_b64
                print(
                    f"[{lanlan_name}] Phase 2 获取到最新截图 ({len(fresh_b64) // 1024}KB)"
                )
            # Technical failures may reuse an old image; explicit refusals must not restore this round's visual input through an old image.
            elif not fresh.declined:
                screenshot_b64_for_phase2 = vision_content.get("screenshot_b64", "")
                if screenshot_b64_for_phase2:
                    print(
                        f"[{lanlan_name}] Phase 2 刷新截图失败，退回使用 Phase 1 旧截图"
                    )

        # Build screen-content section (vision).
        screen_section = ""
        if screenshot_b64_for_phase2:
            sl = get_screen_section_header(master_name_current, proactive_lang)
            sf = get_screen_section_footer(master_name_current, proactive_lang)
            vision_window = (
                vision_content.get("window_title", "") if vision_content else ""
            )
            window_line = (
                _loc(SCREEN_WINDOW_TITLE, proactive_lang).format(window=vision_window)
                if vision_window
                else ""
            )
            hint = get_screen_img_hint(master_name_current, proactive_lang)
            screen_section = f"{sl}\n{window_line}{hint}\n{sf}"
            print(f"[{lanlan_name}] Phase 2 将使用 vision 模型直接看截图")
        else:
            print(f"[{lanlan_name}] Phase 2 无截图或无 vision 模型，跳过屏幕分析")

        # Build web-topic section.
        external_section = ""
        if web_topic:
            el = _loc(EXTERNAL_TOPIC_HEADER, proactive_lang)
            ef = _loc(EXTERNAL_TOPIC_FOOTER, proactive_lang)
            external_section = f"{el}\n{web_topic}\n{ef}"

        music_section = ""
        # Gate selected_music_link (actually selected playable track), not music_topic:
        # whenever Phase2 prompt contains music / output format lists [MUSIC], downstream has
        # a deliverable song, avoiding untranslatable [MUSIC]. Nonempty selected_music_link
        # implies music_topic, both created during Phase1 selection. While playing/cooling down,
        # upstream already cleared music_content / selected_music_link, naturally skipping branch.
        if (
            selected_music_link
            and not is_music_occupied
            and not is_playing_music
            and not music_cooldown
        ):
            # Optimization: separate identifiers stop model confusing music material with ordinary external web topics.
            msh = _loc(MUSIC_SECTION_HEADER, proactive_lang)
            msf = _loc(MUSIC_SECTION_FOOTER, proactive_lang)
            music_section = f"{msh}\n{music_topic}\n{msf}"
        elif is_playing_music:
            print(
                f"[{lanlan_name}] 正在播放音乐，已屏蔽音乐推荐素材（仅保留 playing_hint）"
            )
            music_section = ""

        # Build meme section.
        meme_section = ""
        meme_topic = None
        for channel, topic in phase1_topics:
            if channel == "meme":
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
        # Whether source tags are enabled: with side-effect web/music/meme channels,
        # get_proactive_format_sections uses _of_header requiring first-line [TAG]; with none,
        # _of_none explicitly requests untagged plain text, delivered using fallback source_tag='CHAT'.
        # Missing-tag gate applies only to former, or legitimate _of_none text would be
        # dropped as format leakage (Codex P1).
        _expects_source_tag = (
            bool(external_section) or bool(music_section) or bool(meme_section)
        )
        music_playing_hint = _build_music_playing_hint(
            is_playing_music=is_playing_music,
            current_track=current_track,
            master_name=master_name_current,
            lang=proactive_lang,
        )

        # Render activity snapshot as prompt; absent snapshot yields empty string, making decision-frame
        # A) current-state branch default to other states with all entry points available.
        #
        # Refresh tracker enrichment cache before rendering (activity_scores /
        # activity_guess / open_threads). kickoff_open_threads_compute launched fire-and-forget at
        # Phase1 start; results arrive gradually while Phase1 runs,
        # invisible to earlier activity_snapshot. Parallel computation is intended for
        # use this turn. Keep decision fields (state / propensity / propensity_reasons /
        # unfinished_thread) from early snapshot, preventing mid-Phase1 state changes from
        # making gates (e.g. restricted_screen_only narrowing enabled_modes) inconsistent with final
        # prompt.
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
                logger.debug(
                    f"[{lanlan_name}] fresh enrichment fetch failed: {_enrich_err}"
                )
                display_snap = activity_snapshot
            state_section = format_activity_state_section(display_snap, proactive_lang)
        else:
            display_snap = None
            state_section = ""

        # Separate static/dynamic content: generate_prompt is cacheable static SystemMessage,
        # added music/meme instructions dynamic HumanMessage context.
        # Use enriched_memory_context with reminiscence cues, not raw memory_context.
        # Keep open_threads in activity state section above, outside memory_context.
        phase2_memory_context = memory_context
        if followup_topics_prompt:
            phase2_memory_context = memory_context + "\n" + followup_topics_prompt

        # ── Explicit user topic bans ─────────────────────────────────
        # Phase2 builds its own system prompt, never _build_initial_prompt,
        # so previous 'do not mention X' instructions were entirely invisible to proactive chat:
        # ordinary replies obeyed while proactive speech still raised banned topics, the most
        # hurtful unsolicited path because AI chose the topic without user request.
        # Soft prompt constraint here; hard drop at output, see
        # generation.py _proactive_directive_hits. Neither level adds LLM calls.
        phase2_memory_context = _append_directives_section(
            phase2_memory_context, lanlan_name, proactive_lang,
        )

        phase2_prompt_context = Phase2PromptContext(
            music_playing_hint=music_playing_hint,
            character_prompt=character_prompt,
            inner_thoughts=inner_thoughts,
            state_section=state_section,
            memory_context=phase2_memory_context,
            recent_chats_section=proactive_chat_history_prompt,
            screen_section=screen_section,
            external_section=external_section,
            music_section=music_section,
            meme_section=meme_section,
            source_instruction=source_instruction,
            output_format_section=output_format_section,
        )
        dynamic_context_for_phase2 = _build_music_dynamic_context(
            selected_music_link=selected_music_link,
            music_content=music_content,
            is_playing_music=is_playing_music,
            master_name=master_name_current,
            lang=proactive_lang,
        )
        phase2_system_prompt = phase2_prompt_context.render(
            proactive_lang=proactive_lang,
            master_name=master_name_current,
        )
        # Do not inject strict_constraint during music_cooldown; frontend/backend already removed
        # music entirely, so expose no music instructions that could distort other-source selection.
        print(
            f"[{lanlan_name}] Phase 2 prompt 长度: {len(phase2_system_prompt)}, "
            f"动态上下文: {len(dynamic_context_for_phase2)} 字符"
        )
        # Phase1 final preemption check: request_fresh_screenshot awaits up to 3s,
        # the last interruptible window before prepare_proactive_delivery. If user took over,
        # continuing prepare would overwrite user-turn sid with internal `current_speech_id = uuid4()`,
        # even though SM PROACTIVE_CLAIM avoids writing proactive_sid under _preempted=True;
        # mgr.current_speech_id already physically changed, mislabeling user reply
        # TTS with an unrelated sid.
        if _round_preempted():
            return await _end_proactive(
                ProactiveChatResult(
                    body=_proactive_preempted_json("phase1_pre_prepare")
                )
            )

        # --- Preflight: user idle, WebSocket online, session available ---
        if not await mgr.prepare_proactive_delivery(
            min_idle_secs=10.0, round_id=lifecycle.screenshot_request_id,
        ):
            return await _end_proactive(
                ProactiveChatResult(
                    body={
                        "success": True,
                        "action": "pass",
                        "reason_code": PROACTIVE_REASON_PASS_ACTIVITY_BUSY,
                        "message": "主动搭话条件未满足（用户近期活跃或语音会话正在进行）",
                    }
                )
            )

        # Capture proactive starting speech_id; changed id at abort means user interrupted/took over,
        # so handle_new_message() would wrongly clear the user's legitimate reply TTS too.
        # prepare_proactive_delivery fired PROACTIVE_CLAIM with sid; advance state machine
        # to PHASE2, used by subsequent astream preemption checks.
        proactive_sid = mgr.current_speech_id
        if not await _enter_proactive_phase2(
            mgr,
            proactive_sid,
            log=logger,
            round_id=lifecycle.screenshot_request_id,
        ):
            return await _end_proactive(
                ProactiveChatResult(
                    body=_proactive_pass_body(
                        PROACTIVE_REASON_DELIVERY_PREEMPTED,
                        message="用户在 Phase 2 状态切换期间恢复互动",
                    )
                )
            )

        # Path B (idle) focus mode: this round is now committed to speaking
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
        lifecycle.mark_phase2(mgr.state.snapshot())

        guarded_output = await _run_phase2_generation(
            mgr=mgr,
            vision_consent_epoch=vision_consent_epoch,
            round_is_current=lifecycle.owns_round,
            vision_consent_required=round_uses_vision_material,
            proactive_sid=proactive_sid,
            model_config=model_config,
            lanlan_name=lanlan_name,
            proactive_lang=proactive_lang,
            master_name=master_name_current,
            system_prompt=phase2_system_prompt,
            dynamic_context=dynamic_context_for_phase2,
            screenshot_b64=screenshot_b64_for_phase2,
            focus_thinking=_focus_phase2_thinking,
            expects_source_tag=_expects_source_tag,
            active_channels=active_channels,
            selected_music_link=selected_music_link,
            selected_meme_link=selected_meme_link,
            music_content=music_content,
            meme_content=meme_content,
            is_playing_music=is_playing_music,
            music_cooldown=music_cooldown,
            log=logger,
        )
        if _round_preempted():
            return await _end_proactive(ProactiveChatResult(
                body=_proactive_preempted_json("phase2_post_generation"),
            ))
        # Consent was revoked during generation: the response uses screen/window content, and the screenshot would persist into the next round; do not commit it.
        if round_uses_vision_material and await _vision_consent_declined(vision_consent_check):
            logger.info("[%s] 视觉授权在生成期间撤回，放弃本轮投递", lanlan_name)
            return await _end_proactive(ProactiveChatResult(
                body=_proactive_pass_body(
                    PROACTIVE_REASON_PASS_PRIVACY,
                    message="vision consent revoked before delivery",
                ),
            ))
        if guarded_output.result is not None:
            return await _end_proactive(
                ProactiveChatResult(body=guarded_output.result.body)
            )
        response_text = guarded_output.response_text
        source_tag = guarded_output.source_tag
        selected_music_link = guarded_output.selected_music_link
        music_content = guarded_output.music_content
        is_music_used = guarded_output.is_music_used
        phase2_use_vision = bool(
            screenshot_b64_for_phase2 and model_config.has_vision_model
        )
        delivery_commit = await _commit_proactive_delivery(
            mgr=mgr,
            proactive_sid=proactive_sid,
            lanlan_name=lanlan_name,
            response_text=response_text,
            source_tag=source_tag,
            active_channels=active_channels,
            selected_web_link=selected_web_link,
            selected_music_link=selected_music_link,
            selected_meme_link=selected_meme_link,
            music_content=music_content,
            is_music_used=is_music_used,
            is_playing_music=is_playing_music,
            music_cooldown=music_cooldown,
            vision_content=vision_content,
            phase2_use_vision=phase2_use_vision,
            screenshot_b64=screenshot_b64_for_phase2,
            proactive_lang=proactive_lang,
            master_name=master_name_current,
            log=logger,
            round_id=lifecycle.screenshot_request_id,
        )
        if delivery_commit.result is not None:
            return await _end_proactive(
                ProactiveChatResult(body=delivery_commit.result.body)
            )
        committed_delivery = delivery_commit.delivery
        if committed_delivery is None:  # Defensive: the stage contract is exhaustive.
            raise RuntimeError("delivery commit returned neither result nor delivery")
        if (
            committed_delivery.is_music_used
            and committed_delivery.delivered_music_link
        ):
            delivered_music_link = committed_delivery.delivered_music_link
            mark_music_as_played(
                {
                    "name": delivered_music_link.get("title", ""),
                    "artist": delivered_music_link.get("artist", ""),
                    "url": delivered_music_link.get("url", ""),
                }
            )
        recorded_result = await _record_committed_delivery(
            mgr=mgr,
            delivery=committed_delivery,
            lanlan_name=lanlan_name,
            response_text=response_text,
            source_tag=source_tag,
            active_channels=active_channels,
            **state_storage_kwargs,
            has_unfinished_thread=_has_unfinished_thread,
            surfaced_reflection_ids=_surfaced_reflection_ids,
            selected_web_link=selected_web_link,
            selected_web_topic_key=selected_web_topic_key,
            web_parsed=web_parsed,
            selected_music_link=selected_music_link,
            selected_music_topic_key=selected_music_topic_key,
            selected_meme_link=selected_meme_link,
            selected_meme_topic_key=selected_meme_topic_key,
            meme_content=meme_content,
            memory_server_port=MEMORY_SERVER_PORT,
            log=logger,
        )
        return await _end_proactive(
            ProactiveChatResult(
                body=recorded_result.body,
                status_code=recorded_result.status_code,
            )
        )

    except asyncio.CancelledError:
        if lifecycle is not None:
            await lifecycle.safe_done()
        raise
    except asyncio.TimeoutError:
        logger.error("主动搭话超时")
        if lifecycle is not None:
            await lifecycle.safe_done()
        return ProactiveChatResult(
            body=_proactive_error_body(
                PROACTIVE_REASON_ERROR_TIMEOUT,
                error="AI处理超时",
            ),
            status_code=504,
        )
    except Exception as e:
        logger.error(f"主动搭话接口异常: {e}")
        if lifecycle is not None:
            await lifecycle.safe_done()
        return ProactiveChatResult(
            body=_proactive_error_body(
                PROACTIVE_REASON_ERROR_INTERNAL,
                error="服务器内部错误",
                detail=str(e),
            ),
            status_code=500,
        )
    finally:
        # Also retire the ID on cancellation; a delayed cleanup cannot clear
        # the ID of a newer round admitted after PROACTIVE_DONE.
        if lifecycle is not None:
            lifecycle.clear_screenshot_request()
