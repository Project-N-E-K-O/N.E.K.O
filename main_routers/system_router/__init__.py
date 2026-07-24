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

"""
System Router package.

Formerly the monolithic ``main_routers/system_router.py`` (8.3k lines); now
split by route domain. All submodules register their endpoints on the single
shared ``APIRouter`` defined in ``_shared`` (imported below in the original
route-declaration order), and every top-level name of the old module is
re-exported here so existing imports keep working.

Note for tests: ``monkeypatch.setattr`` must target the submodule that
*consumes* a helper (e.g. ``screenshot._is_loopback_request``), not this
package facade -- re-exports are snapshots, they do not rebind submodule
globals.

URL convention: routes declared WITHOUT trailing slash (no ``@router.get('/')``).
See ``main_routers/characters_router.py`` docstring or
``.agent/rules/neko-guide.md`` (section on no-trailing-slash API URLs) for the rationale;
enforced by ``scripts/check_api_trailing_slash.py``.
"""

from ..shared_state import ensure_steamworks as get_steamworks, get_config_manager  # noqa: F401
from config import AUTOSTART_CSRF_TOKEN  # noqa: F401

from ._shared import (  # noqa: F401
    router,
    logger,
    _AUTOSTART_CSRF_HEADER,
    _set_no_store_headers,
    _is_loopback_request,
    _is_remote_backend_deployment,
    _json_no_store_response,
    _build_public_error_response,
    _normalize_origin_value,
    _get_request_origin,
    _get_system_config_manager,
    _get_allowed_local_origins,
    _validate_local_mutation_request,
    _read_json_object,
    _is_path_within_base,
    _get_app_root,
)
from .yui_handoff import (  # noqa: F401
    _YUI_GUIDE_HANDOFF_TOKEN_VERSION,
    _YUI_GUIDE_HANDOFF_FLOW_ID,
    _YUI_GUIDE_HANDOFF_TTL_SECONDS,
    _YUI_GUIDE_HANDOFF_MAX_RECORDS,
    _YUI_GUIDE_HANDOFF_SECRET,
    _yui_guide_handoff_lock,
    _yui_guide_handoff_tokens,
    _normalize_yui_handoff_text,
    _build_yui_handoff_signature,
    _public_yui_handoff_record,
    _prune_yui_handoff_records,
    create_yui_guide_handoff,
    consume_yui_guide_handoff,
)
from .status import (  # noqa: F401
    _DEFAULT_NEKO_SOCIAL_BASE_URL,
    _derive_system_lifecycle_state,
    get_system_client_id,
    get_system_social_config,
    get_system_status,
    get_token_usage,
    get_pending_notices,
    ack_pending_notices,
)
from .prompt_flows import (  # noqa: F401
    get_seven_day_tutorial_state,
    put_seven_day_tutorial_state,
    get_autostart_prompt_state,
    post_autostart_prompt_heartbeat,
    post_autostart_prompt_shown,
    post_autostart_prompt_decision,
)
from .changelog_survey import (  # noqa: F401
    get_changelog,
    _LOCALE_RE,
    _safe_locale,
    _load_survey_for_version,
    _sanitize_survey_answers,
    _resolve_survey_for_request,
    get_survey,
    submit_survey,
)
from .emotion import (  # noqa: F401
    _EMOTION_LABEL_ALIASES,
    _EMOTION_CANONICAL_LABELS,
    _EMOTION_NORMALIZED_ALIAS_LOOKUP,
    _EMOTION_COMPACT_ALIAS_LOOKUP,
    _EMOTION_FUZZY_ALIAS_KEYS,
    _EMOTION_FUZZY_COMPACT_KEYS,
    _ASCII_EMOTION_ALIAS_RE,
    _EMOTION_NEGATION_WORDS,
    _EMOTION_NEGATION_PREFIXES,
    _EMOTION_NEGATION_SUFFIXES,
    _EMOTION_TOKEN_RE,
    _EMOTION_NEGATION_COMPACT_PREFIXES,
    _EMOTION_NEGATION_COMPACT_SUFFIXES,
    _EMOTION_NEGATION_CONTEXT_WINDOW,
    _looks_like_emotion_compact_candidate,
    _has_negated_emotion_phrase,
    _EMOTION_KEYWORDS,
    _SAD_VULNERABLE_PATTERNS,
    _ANGRY_ATTACK_PATTERNS,
    _HAPPY_PLAYFUL_PATTERNS,
    _normalize_emotion_label,
    _push_emotion_update,
    _emotion_response,
    _coerce_emotion_confidence,
    _HEURISTIC_NEGATION_TOKENS,
    _HEURISTIC_TIGHT_NEGATION_TOKENS,
    _HEURISTIC_NEGATION_BLOCKLIST,
    _HEURISTIC_CONTRAST_CONJUNCTIONS,
    _HEURISTIC_NEGATION_LOOKBACK,
    _HEURISTIC_TIGHT_NEGATION_LOOKBACK,
    _HEURISTIC_CLAUSE_DELIMITERS,
    _has_heuristic_negation_before,
    _ASCII_WORD_KEYWORD_RE_CACHE,
    _is_ascii_word_keyword,
    _count_keyword_hits,
    _infer_emotion_from_text,
    _resolve_emotion_prompt_language,
    emotion_analysis,
)
from .steam import (  # noqa: F401
    _PLAYTIME_PROGRESS_STAT,
    _PLAYTIME_PROGRESS_ACHIEVEMENTS,
    _prepare_steam_user_stats,
    _unlock_steam_achievement,
    _read_progress_unlocked_achievements,
    set_achievement_status,
    update_playtime,
    list_achievements,
    _read_binary_file,
    proxy_image,
)
from .files import (  # noqa: F401
    check_file_exists,
    find_first_image,
)
from .meme_proxy import (  # noqa: F401
    MEME_PROXY_CACHE,
    proxy_meme_image,
)
from .screenshot import (  # noqa: F401
    _run_macos_interactive_screenshot,
    _image_path_to_jpeg_data_url,
    _is_interactive_screenshot_canceled,
    _format_backend_screenshot_error,
    get_window_title_api,
    backend_screenshot,
    backend_interactive_screenshot,
)
from .activity_signal import (  # noqa: F401
    _ACTIVITY_SIGNAL_THROTTLE,
    _ACTIVITY_SIGNAL_THROTTLE_MAX_ENTRIES,
    _activity_signal_validate_float,
    _activity_signal_validate_str,
    push_activity_signal,
)
from .proactive_history import (  # noqa: F401
    _proactive_chat_history,
    _proactive_material_history,
    _PROACTIVE_MATERIAL_HISTORY_MAX,
    _PROACTIVE_CHAT_TOTALS_FILENAME,
    _PROACTIVE_CHAT_TOTALS_SCHEMA_VERSION,
    _proactive_chat_totals,
    _invite_ever_delivered,
    _proactive_chat_totals_lock,
    _proactive_chat_totals_loaded,
    _RECENT_CHAT_MAX_AGE_SECONDS,
    _PROACTIVE_SIMILARITY_THRESHOLD,
    _format_recent_proactive_chats,
    _REMINISCENCE_USAGE_MAX,
    _reminiscence_usage_history,
    _record_reminiscence_usage,
    _record_proactive_chat,
    _normalize_material_key,
    _proactive_material_key,
    _is_recent_proactive_material,
    _record_proactive_material,
    _proactive_chat_totals_path,
    _ensure_proactive_chat_totals_loaded,
    _get_proactive_chat_total,
    _was_invite_ever_delivered,
    _persist_totals_unlocked,
    _increment_proactive_chat_total,
    _mark_invite_ever_delivered,
    _record_invite_delivery_persistent,
    _clear_channel_from_proactive_history,
    _normalize_text_for_similarity,
    _is_similar_to_recent_proactive_chat,
)
from .proactive_sources import (  # noqa: F401
    _SOURCE_HISTORY_FILENAME,
    _SOURCE_HISTORY_SCHEMA_VERSION,
    _source_history,
    _source_history_lock,
    _source_history_loaded,
    _source_history_path,
    _source_hash,
    _half_life_for,
    _source_skip_probability,
    _should_skip_source,
    _ensure_source_history_loaded,
    _record_source_used,
    _SOURCE_WEIGHT_DECAY_LAMBDA,
    _SOURCE_WEIGHT_K,
    _SOURCE_WEIGHT_FLOOR,
    _compute_source_weights,
    _filter_sources_by_weight,
    _SOURCE_WEIGHT_WINDOW,
)
from .proactive_parsing import (  # noqa: F401
    _extract_links_from_raw,
    _parse_web_screening_result,
    _text_is_pass_sentinel,
    _parse_unified_phase1_result,
    _PROACTIVE_LEGAL_SOURCE_TAGS,
    _PROACTIVE_SCREEN_TAG_LEAKS,
    _PROACTIVE_BRACKET_TAG_RE,
    _PROACTIVE_LEGAL_TAG_RE,
    _strip_proactive_screen_tag_leak,
    _INTENT_LABEL_DECOR,
    _strip_proactive_intent_label_leak,
    _lookup_link_by_title,
    PROACTIVE_REASON_CHAT_DELIVERED,
    PROACTIVE_REASON_PASS_BUSY,
    PROACTIVE_REASON_PASS_ACTIVITY_BUSY,
    PROACTIVE_REASON_PASS_DELIVERY_BUSY,
    PROACTIVE_REASON_PASS_DISABLED,
    PROACTIVE_REASON_PASS_ROUTE_ACTIVE,
    PROACTIVE_REASON_PASS_PRIVACY,
    PROACTIVE_REASON_PASS_RESTRICTED_SCREEN_ONLY,
    PROACTIVE_REASON_PASS_THROTTLED,
    PROACTIVE_REASON_PASS_SOURCE_EMPTY,
    PROACTIVE_REASON_PASS_MODEL_PASS,
    PROACTIVE_REASON_PASS_GENERATION_EMPTY,
    PROACTIVE_REASON_PASS_DUPLICATE,
    PROACTIVE_REASON_DELIVERY_PREEMPTED,
    PROACTIVE_REASON_DELIVERY_FAILED,
    PROACTIVE_REASON_ERROR_TIMEOUT,
    PROACTIVE_REASON_ERROR_INTERNAL,
    PROACTIVE_REASON_ERROR_CHARACTER_NOT_FOUND,
    PROACTIVE_REASON_ERROR_SOURCE_FETCH_FAILED,
    PROACTIVE_REASON_PASS_UNSPECIFIED,
    PROACTIVE_STAGE_ENTRY_GUARD,
    PROACTIVE_STAGE_ACTIVITY_GATE,
    PROACTIVE_STAGE_SOURCE_SELECTION,
    PROACTIVE_STAGE_MODEL_DECISION,
    PROACTIVE_STAGE_GENERATION,
    PROACTIVE_STAGE_DEDUP,
    PROACTIVE_STAGE_DELIVERY,
    PROACTIVE_STAGE_RUNTIME_ERROR,
    PROACTIVE_STAGE_UNKNOWN,
    _PROACTIVE_REASON_STAGE,
    _proactive_stage_for_reason,
    _proactive_response_body,
    _proactive_pass_body,
    _proactive_chat_body,
    _proactive_error_body,
    _ensure_proactive_reason_code,
)
from .proactive_content import (  # noqa: F401
    _log_news_content,
    _log_video_content,
    _log_trending_content,
    _log_music_content,
    _format_music_content,
    _append_music_recommendations,
    _log_personal_dynamics,
)
from .break_reminders import (  # noqa: F401
    _resolve_break_reminder_label,
    _render_work_break_prompt,
    _render_anti_slack_prompt,
    _render_work_break_game_invite_prompt,
    _deliver_break_reminder_via_llm,
)
from .proactive_chat_flow import (  # noqa: F401
    _proactive_llm_retry_error_types,
    _safe_fire_proactive_done,
    _PHASE1_FETCH_PER_SOURCE,
    _PHASE1_TOTAL_TOPIC_TARGET,
    _open_threads_for_activity_state,
    _render_followup_topic_hooks,
    _resolve_proactive_locale,
    _resolve_topic_hook_locale,
    build_proactive_response,
    proactive_chat,
    proactive_music_played_through,
)
from .mini_game_invite import (  # noqa: F401
    _mini_game_invite_state,
    _mini_game_invite_get_state,
    _mini_game_invite_advance_response,
    _mini_game_invite_in_cooldown,
    _mini_game_invite_record_delivered,
    _mini_game_invite_count_post_response_chat,
    _mini_game_invite_record_response_cooldown,
    _mini_game_launch_url,
    _pick_mini_game_type,
    _maybe_deliver_mini_game_invite,
    _build_mini_game_invite_options_payload,
    _apply_mini_game_invite_choice,
    mini_game_invite_respond,
    _push_mini_game_invite_resolved,
    _LETTER_ONLY_KW_RE,
    _KEYWORD_PATTERN_CACHE,
    _keyword_matches,
    _match_mini_game_invite_keyword,
    _maybe_apply_mini_game_invite_keyword,
)
from .translate import (  # noqa: F401
    translate_text_api,
)
