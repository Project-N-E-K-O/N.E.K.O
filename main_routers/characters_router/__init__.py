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
Characters Router package.

Formerly the monolithic ``main_routers/characters_router.py``; now split by
route domain. All submodules register endpoints on the single shared
``APIRouter`` defined in ``_shared``; every top-level name of the old
module is re-exported here so existing imports keep working.

Note for tests: ``monkeypatch.setattr`` must target the submodule that
*consumes* a helper, not this package facade -- re-exports are snapshots.

URL convention: routes declared WITHOUT trailing slash (no ``@router.get('/')``,
except the collection root which is declared as ``@router.get('')``); enforced
by ``scripts/check_api_trailing_slash.py``.
"""

from ._shared import (  # noqa: F401
    router,
    logger,
    CHARACTER_RESERVED_FIELD_SET,
    _json_no_store_response,
    _read_json_object_or_400,
    _profile_name_units,
    _validate_profile_name,
    _is_safe_profile_name,
    _validate_existing_character_path_name,
    _profile_name_contains_path_separator,
    MAX_UPLOAD_SIZE,
    MAX_CARD_FACE_SIZE,
    _UploadTooLargeError,
    _read_limited_stream,
)
from .notify import (  # noqa: F401
    _resolve_reload_page_notice_code,
    send_reload_page_notice,
    notify_memory_server_reload,
    release_memory_server_character,
)
from .pngtuber_assets import (  # noqa: F401
    _PNGTUBER_CARD_MODEL_DIR,
    _PNGTUBER_IMAGE_KEYS,
    _PNGTUBER_PACKABLE_KEYS,
    _strip_url_suffix,
    _pngtuber_user_rel_from_url,
    _collect_pngtuber_user_asset_refs,
    _pngtuber_package_roots_from_refs,
    _with_pngtuber_model_path_rewrites,
    _add_pngtuber_assets_to_character_zip,
    _rewrite_imported_pngtuber_refs,
    _restore_imported_pngtuber_avatar_config,
    _copy_imported_pngtuber_assets,
)
from .direct_link import (  # noqa: F401
    _DIRECT_LINK_MAX_REDIRECTS,
    _DIRECT_LINK_REDIRECT_STATUSES,
    DirectLinkSecurityError,
    DirectLinkValidatedTarget,
    _DirectLinkPinnedResolver,
    _DirectLinkProbeResponse,
    _direct_link_hostname,
    _direct_link_port,
    _assert_direct_link_addresses_safe,
    _validate_direct_link_target,
    _redirect_target_from_response,
    _open_pinned_direct_link_session,
    _request_direct_link_follow_redirects,
    _download_direct_link_audio,
)
from .voice_providers import (  # noqa: F401
    COSYVOICE_VOICE_DESIGN_DEFAULT_MEDIA_TYPE,
    COSYVOICE_VOICE_DESIGN_PREFIX_MAX,
    COSYVOICE_VOICE_DESIGN_PREFIX_PATTERN,
    COSYVOICE_VOICE_DESIGN_PROMPT_MAX,
    ElevenLabsUpstreamError,
    _build_minimax_request_prefix,
    _get_elevenlabs_base_url,
    _config_value_is_enabled,
    _prefixed_elevenlabs_voice_id,
    _raw_elevenlabs_voice_id,
    _raise_for_elevenlabs_response,
    _elevenlabs_clone_voice,
    ELEVENLABS_VOICE_DESIGN_DESC_MIN,
    ELEVENLABS_VOICE_DESIGN_DESC_MAX,
    ELEVENLABS_VOICE_DESIGN_PREVIEW_TEXT,
    _cosyvoice_customization_url,
    _cosyvoice_design_language_hints,
    _cosyvoice_design_voice,
    _first_nested_value,
    _minimax_voice_design_url,
    _minimax_design_voice,
    _elevenlabs_design_previews,
    _elevenlabs_create_voice_from_preview,
    _is_local_voice_clone_tts_config,
    _local_voice_clone_tts_base_url,
    _elevenlabs_synthesize_preview,
)
from .live2d_models import (  # noqa: F401
    _derive_live2d_model_name,
    _normalize_live2d_catalog_path,
    _is_same_live2d_catalog_model_path,
    _derive_live2d_asset_source,
    _derive_model_asset_binding,
    _find_live2d_model_catalog_entry,
    _resolve_live2d_model_binding,
    get_current_live2d_model,
    update_catgirl_l2d,
    update_catgirl_touch_set,
    update_catgirl_lighting,
    update_catgirl_mmd_settings,
    get_catgirl_mmd_settings,
)
from .persona import (  # noqa: F401
    _build_persona_selection_payload,
    _normalize_persona_request_language,
    _get_persona_request_language,
    _get_persona_payload_request_language,
    _has_generated_persona_selection_prompt,
    _clear_stale_generated_persona_prompt,
    _rollback_character_persona_selection_change,
    list_persona_presets_route,
    get_persona_onboarding_state,
    set_persona_onboarding_state,
    request_current_character_persona_reselect,
    clear_current_character_persona_reselect,
    get_character_persona_selection,
    update_character_persona_selection,
    clear_character_persona_selection,
)
from .crud import (  # noqa: F401
    DEFAULT_NEW_CATGIRL_FREE_VOICE_ID,
    _get_new_catgirl_default_voice_id,
    _mark_new_character_greeting_pending_safe,
    _build_profile_rename_event,
    _append_profile_rename_event,
    _clear_character_recent_history,
    _normalize_prompt_synced_field_value,
    _prompt_synced_catgirl_fields,
    _catgirl_prompt_fields_changed,
    _refresh_catgirl_context_after_profile_change,
    _filter_mutable_catgirl_fields,
    _normalize_catgirl_field_order,
    _extract_catgirl_field_order_payload,
    _sync_catgirl_field_order,
    _flatten_catgirl_for_response,
    _snapshot_existing_paths,
    _create_character_operation_backup_dir,
    _restore_snapshot_paths,
    _build_character_tombstones_state,
    _rollback_character_operation,
    get_characters,
    rename_catgirl,
    get_current_catgirl,
    set_current_catgirl,
    reload_character_config,
    update_master,
    rename_master,
    add_catgirl,
    update_catgirl,
    delete_catgirl_by_body,
    delete_catgirl,
    _delete_catgirl_by_name,
    set_microphone,
    get_microphone,
)
from .voice_registry import (  # noqa: F401
    VOICE_SESSION_STARTING_ERROR,
    _voice_session_starting_response,
    _is_current_catgirl_voice_session_starting,
    update_catgirl_voice_id,
    get_catgirl_voice_mode_status,
    unregister_voice,
    clear_voice_ids,
    list_custom_tts_voices_for_characters,
    register_voice,
    delete_voice,
)
from .voice_preview import (  # noqa: F401
    VOICE_PREVIEW_TEXTS,
    _normalize_voice_preview_language,
    _get_voice_preview_language,
    _is_free_preset_voice_id,
    _get_active_native_preview_provider,
    _is_unpreviewable_selected_preset_voice,
    _read_wav_payload,
    _build_wav_payload,
    _synthesize_step_voice_preview,
    _synthesize_free_voice_preview,
    _synthesize_gemini_native_voice_preview,
    _build_free_intl_voice_pins,
    get_voices,
    get_voice_preview,
)
from .voice_cloning import (  # noqa: F401
    _cosyvoice_design_default_preview_text,
    _voice_design_preview_language,
    _voice_design_preview_text,
    _provider_supports_voice_design,
    _normalize_doubao_voice_clone_speaker_id,
    _trim_tasks,
    analyze_silence,
    trim_silence_endpoint,
    get_trim_progress,
    cancel_trim_task,
    voice_clone,
    voice_design,
    _validate_voice_design_description,
    voice_design_preview,
    voice_design_create,
    voice_clone_direct,
)
from .cards import (  # noqa: F401
    _embed_zip_in_png_chunk,
    get_character_cards,
    save_catgirl_to_model_folder,
    save_character_card,
    export_catgirl_card,
    export_catgirl_settings_only,
    import_character_card,
    _default_card_meta,
    _read_card_meta,
    _write_card_meta,
    _detect_card_origin_from_character,
    list_card_faces,
    list_card_metas,
    get_card_meta,
    put_card_meta,
    _strip_legacy_card_face_header,
    get_card_face,
    put_card_face,
    _InvalidPortraitError,
    export_catgirl_with_portrait,
)
