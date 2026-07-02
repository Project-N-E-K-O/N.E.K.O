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
Workshop Router package.

Formerly the monolithic ``main_routers/workshop_router.py``; now split by
route domain. All submodules register endpoints on the single shared
``APIRouter`` defined in ``_shared``; every top-level name of the old
module is re-exported here so existing imports keep working.

Placement invariants (do not move casually):
- ``cancel_background_tasks`` accesses ``_ugc_warmup_task`` /
  ``_ugc_sync_task`` through ``globals()`` by name, so it must live in the
  same module (``ugc``) as both task handles.
- ``get_subscribed_workshop_items`` reads the rebindable
  ``_ugc_warmup_task``, so it lives in ``ugc`` next to the rebinder.

Note for tests: ``monkeypatch.setattr`` must target the submodule that
*consumes* a helper, not this package facade -- re-exports are snapshots.

URL convention: routes declared WITHOUT trailing slash; see
``main_routers/characters_router.py`` docstring; enforced by
``scripts/check_api_trailing_slash.py``.
"""

from ._shared import (  # noqa: F401
    router,
    logger,
)
from .voice_manifest import (  # noqa: F401
    WORKSHOP_VOICE_MANIFEST_NAME,
    WORKSHOP_REFERENCE_AUDIO_EXTENSIONS,
    WORKSHOP_REFERENCE_AUDIO_CONTENT_TYPES,
    WORKSHOP_REFERENCE_LANGUAGES,
    WORKSHOP_REFERENCE_PROVIDER_HINTS,
    _sanitize_voice_prefix,
    _normalize_workshop_voice_manifest,
    _resolve_workshop_voice_reference,
    _cleanup_workshop_voice_reference,
    _build_workshop_voice_reference_summary,
)
from .meta import (  # noqa: F401
    _session_deleted_names,
    mark_session_deleted_character_name,
    _read_first_line,
    _load_deleted_character_names,
    _remove_deleted_character_tombstones,
    _write_deleted_character_tombstone,
    _derive_workshop_origin_display_name,
    _normalize_workshop_model_ref,
    _build_subscriber_workshop_model_ref,
    _derive_workshop_model_binding,
    get_workshop_meta_path,
    read_workshop_meta,
    write_workshop_meta,
    calculate_content_hash,
    get_folder_size,
    get_workshop_meta,
)
from .preview_cards import (  # noqa: F401
    WORKSHOP_CARD_FACE_SIZE,
    WORKSHOP_CARD_FACE_PADDING,
    WORKSHOP_CARD_FACE_RATIO_TOLERANCE,
    WORKSHOP_CARD_FACE_MARKER_KEY,
    WORKSHOP_CARD_FACE_MARKER_VALUE,
    WORKSHOP_STANDARD_PREVIEW_STEMS,
    WORKSHOP_STANDARD_PREVIEW_EXTENSIONS,
    WORKSHOP_PREVIEW_IMAGE_NAMES,
    WORKSHOP_IMAGE_EXTENSIONS,
    WORKSHOP_MODEL_TEXTURE_DIR_NAMES,
    _collect_workshop_character_name_hints,
    _collect_workshop_model_image_references,
    _score_workshop_preview_candidate,
    find_preview_image_in_folder,
    _build_workshop_card_face_meta,
    _read_card_face_origin,
    _is_workshop_card_face_normalized,
    _should_refresh_workshop_card_face,
    _render_workshop_card_face_image,
    _has_workshop_card_face_marker,
    _is_matching_workshop_character,
    _ensure_workshop_card_face_from_preview,
    _ensure_workshop_card_face_meta,
    upload_preview_image,
)
from .ugc import (  # noqa: F401
    _INVALID_UGC_QUERY_HANDLE,
    _ugc_details_cache,
    _UGC_CACHE_TTL,
    _ugc_warmup_task,
    _ugc_sync_task,
    _ugc_query_lock,
    _workshop_download_requested,
    _ITEM_STATE_SUBSCRIBED,
    _ITEM_STATE_INSTALLED,
    _ITEM_STATE_NEEDS_UPDATE,
    _ITEM_STATE_DOWNLOADING,
    _ITEM_STATE_DOWNLOAD_PENDING,
    UnsupportedUGCDetailsError,
    _safe_get_workshop_install_folder,
    _is_workshop_item_install_complete,
    _request_workshop_item_download,
    cancel_background_tasks,
    _is_item_cache_valid,
    _all_items_cache_valid,
    _steamworks_method_unavailable,
    _ugc_details_query_supported,
    _query_ugc_details_batch,
    _local_steam_identity_cache,
    _local_steam_identity_cache_ts,
    _LOCAL_IDENTITY_TTL,
    _persona_web_cache,
    _PERSONA_WEB_TTL,
    _PERSONA_WEB_CONCURRENCY,
    _PERSONA_WEB_TOTAL_DEADLINE,
    _get_local_steam_identity,
    _resolve_author_name,
    _fetch_persona_via_steam_web,
    _resolve_missing_author_names,
    _safe_text,
    _extract_ugc_item_details,
    warmup_ugc_cache,
    _get_subscribed_items_payload,
    _find_subscribed_item_by_id,
    get_subscribed_workshop_items,
)
from .voice_refs import (  # noqa: F401
    upload_reference_audio,
    remove_reference_audio,
    get_workshop_voice_reference,
    get_workshop_voice_reference_audio,
)
from .items import (  # noqa: F401
    get_steam_status,
    trigger_workshop_item_download,
    get_workshop_item_download_status,
    _build_ugc_details_unsupported_item_response,
    _is_known_item_when_ugc_details_unsupported,
    get_workshop_item_path,
    get_workshop_item_details,
)
from .unsubscribe import (  # noqa: F401
    _collect_character_names_by_workshop_item_id,
    _scan_workshop_folder_character_names,
    _resolve_workshop_item_install_path,
    unsubscribe_workshop_item,
)
from .config_files import (  # noqa: F401
    get_workshop_config,
    save_workshop_config_api,
    _assert_under_base,
    read_workshop_file,
    list_chara_files,
    list_audio_files,
)
from .publish import (  # noqa: F401
    publish_lock,
    check_upload_status,
    _is_workshop_publish_native_crash_risk,
    prepare_workshop_upload,
    cleanup_temp_folder,
    publish_to_workshop,
    _publish_workshop_item,
)
from .sync_cards import (  # noqa: F401
    _ugc_sync_lock,
    sync_workshop_character_cards,
    api_sync_workshop_character_cards,
    api_sync_single_workshop_character_card,
)
