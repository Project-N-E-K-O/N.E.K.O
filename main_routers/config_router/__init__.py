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
Config Router package.

Formerly the monolithic ``main_routers/config_router.py``; now split by
route domain. All submodules register endpoints on the single shared
``APIRouter`` defined in ``_shared`` (imported below in the original
route-declaration order); every top-level name of the old module is
re-exported here so existing imports keep working.

Note for tests: ``monkeypatch.setattr`` must target the submodule that
*consumes* a helper (e.g. ``connectivity._test_openai_compatible``), not
this package facade -- re-exports are snapshots.

URL convention: routes declared WITHOUT trailing slash (no ``@router.get('/')``).
See ``main_routers/characters_router.py`` docstring or
``.agent/rules/neko-guide.md`` (section on no-trailing-slash API URLs) for the rationale;
enforced by ``scripts/check_api_trailing_slash.py``.
"""

from ._shared import (  # noqa: F401
    router,
    logger,
)
from .page_config import (  # noqa: F401
    VRM_STATIC_PATH,
    VRM_USER_PATH,
    MMD_STATIC_PATH,
    MMD_USER_PATH,
    PNGTUBER_USER_PATH,
    PNGTUBER_EXTENSIONS,
    _resolve_master_display_name,
    get_character_reserved_fields,
    _MMD_EXTENSIONS,
    _get_live3d_sub_type,
    _resolve_vrm_path,
    _resolve_mmd_path,
    _resolve_pngtuber_image_path,
    get_page_config,
)
from .preferences import (  # noqa: F401
    _apply_noise_reduction_to_active_sessions,
    get_preferences,
    save_preferences,
    set_preferred_model,
    get_conversation_settings,
    save_conversation_settings,
)
from .language import (  # noqa: F401
    get_steam_language,
    get_user_language_api,
)
from .core_config import (  # noqa: F401
    get_core_config_api,
    update_core_config,
    get_api_providers_config,
)
from .gptsovits import (  # noqa: F401
    list_gptsovits_voices,
    test_gptsovits_connectivity,
)
from .proxy import (  # noqa: F401
    _PROXY_LOCK,
    _proxy_snapshot,
    _sanitize_proxies,
    set_proxy_mode,
)
from .connectivity import (  # noqa: F401
    _MIMO_TOKEN_PLAN_HOSTS,
    ConnectivityTestRequest,
    ConnectivityTestResponse,
    _test_openai_compatible,
    _classify_openai_error,
    _test_anthropic,
    _classify_anthropic_error,
    _test_websocket,
    _test_vllm_omni_ws_handshake,
    _test_doubao_tts_connectivity,
    _normalize_provider_url_candidates,
    _looks_like_anthropic_messages_url,
    _normalize_provider_type,
    _test_connectivity_candidates,
    _get_save_provider_api_key,
    _build_save_connectivity_targets,
    _auto_resolve_provider_urls_for_save,
    test_connectivity,
    _identify_provider_label,
    _redact_url_for_log,
)
