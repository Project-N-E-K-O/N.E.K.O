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

"""Steam language and user language endpoints.

Split out of the former monolithic ``main_routers/config_router.py``.
"""

import asyncio

from fastapi import Request
from fastapi.responses import JSONResponse

from ._shared import logger, router

from ..shared_state import ensure_steamworks
from ..shared_state import get_config_manager
from ..system_router._shared import _validate_local_mutation_request
from utils.preferences import (
    aload_ui_language_override,
    asave_ui_language_override,
)


_UI_LANGUAGE_SYNC_LOCK = asyncio.Lock()


async def _rollback_ui_language(previous_language: str | None) -> bool:
    """Best-effort rollback that never hides the original sync failure."""
    try:
        return bool(await asave_ui_language_override(previous_language))
    except Exception:
        logger.exception("界面语言回滚异常")
        return False


def _ui_language_sync_failure_payload(
    *,
    error: str,
    normalized: str,
    previous_ui_language: str | None,
    rollback_succeeded: bool,
    character_name: str | None = None,
    conversation_sync: dict | None = None,
) -> dict:
    """Describe both persisted sides when UI rollback could not be completed."""
    payload = {
        "success": False,
        "error": error,
        "language": normalized,
        "character_name": character_name,
        "conversation_sync": conversation_sync,
        "ui_language": previous_ui_language if rollback_succeeded else normalized,
        "ui_language_rollback_succeeded": rollback_succeeded,
    }
    if not rollback_succeeded:
        payload.update({
            "partial_success": True,
            "partial_persistence": True,
            "error": f"{error}，且界面语言回滚失败",
        })
    return payload


@router.get("/steam_language")
async def get_steam_language():
    """Return Steam language and GeoIP hints for frontend locale setup.

    Response fields:
    - success: whether the lookup succeeded
    - uiLanguage: manual UI language override with no frontend producer
    - steam_language: raw Steam language setting
    - i18n_language: normalized i18n language code
    - ip_country: country code from the user's IP, such as "CN"
    - is_mainland_china: whether the user is in mainland China

    Decision rules:
    - When a Steam language exists, check GeoIP as well
    - When the IP country code is "CN", mark the user as mainland China
    - When no Steam language exists, default to non-mainland behavior
    """
    from utils.language_utils import normalize_language_code, refresh_global_language, is_supported_language_code

    ui_language = None
    try:
        try:
            ui_language = await aload_ui_language_override()
        except Exception:
            logger.debug("读取 UI 语言覆盖失败", exc_info=True)
            ui_language = None

        steamworks = ensure_steamworks()
        
        if steamworks is None:
            # Without Steam, default to a non-mainland user.
            return {
                "success": False,
                "error": "Steamworks 未初始化",
                "uiLanguage": ui_language,
                "steam_language": None,
                "i18n_language": None,
                "ip_country": None,
                "is_mainland_china": False  # No Steam environment: default non-mainland.
            }
        
        # Get Steam's current game language.
        steam_language = steamworks.Apps.GetCurrentGameLanguage()
        # Steam API may return bytes; decode to a string.
        if isinstance(steam_language, bytes):
            steam_language = steam_language.decode('utf-8')
        
        # Use language_utils normalization for consistent mappings.
        # format='full' returns 'zh-CN', 'zh-TW', 'en', 'ja', 'ko' (frontend i18n).
        i18n_language = normalize_language_code(steam_language, format='full')

        # Write this Steam ground truth back to process-global cache: initialize_global_language reads Steam SDK only once
        # at startup, locking in the system locale if it loses a race. If frontend bootstrap now gets
        # the correct schinese -> zh-CN, cache it too so every downstream get_global_language()
        # benefits (mini-game prompts / memory / reflection / tts...). The function's unchanged-is-noop
        # guard prevents repeated frontend refreshes from spamming.
        # Validate **raw steam_language**, not normalized i18n_language: normalization defaults empty/unknown
        # input to 'en', which passes refresh's allowlist and wrongly overwrites an already
        # correct global cache (from startup / prior valid refresh). Frontend i18n's English fallback
        # is unaffected (i18n_language still returns normally).
        if is_supported_language_code(steam_language):
            try:
                refresh_global_language(steam_language)
            except Exception:
                logger.debug("refresh_global_language 失败", exc_info=True)

        # Get the user's IP country to identify mainland China users.
        ip_country = None
        is_mainland_china = False
        
        try:
            # Use Steam Utils API to retrieve the IP country.
            raw_ip_country = steamworks.Utils.GetIPCountry()
            
            if isinstance(raw_ip_country, bytes):
                ip_country = raw_ip_country.decode('utf-8')
            else:
                ip_country = raw_ip_country
            
            if ip_country:
                ip_country = ip_country.upper()
                is_mainland_china = (ip_country == "CN")
            
            if not getattr(get_steam_language, '_logged', False) or not get_steam_language._logged:
                get_steam_language._logged = True
                logger.info(f"[GeoIP] 用户 IP 地区: {ip_country}, 是否大陆: {is_mainland_china}")
            # Write Steam result to ConfigManager's steam-specific cache.
            # Write back only a real country code: empty GetIPCountry() means temporarily unknown (Steam
            # may just have started without networking), while is_mainland_china remains False;
            # writing directly asserts overseas from not False without data, incorrectly routing to overseas nodes.
            if ip_country:
                try:
                    from utils.config_manager import ConfigManager
                    ConfigManager._steam_check_cache = not is_mainland_china
                    # Clear the merged cache to trigger recalculation. Steam is only a fallback signal: if IP probing
                    # already resolved, retain its decision; this update decides routing only while IP remains unresolved.
                    ConfigManager._region_cache = None
                except Exception:
                    # Writeback only supplies a regional fallback signal; failure must not affect this endpoint's main
                    # job of returning Steam language. IP probing still resolves on its own schedule.
                    logger.debug("[GeoIP] Steam 区域回写失败，忽略", exc_info=True)
        except Exception as geo_error:
            get_steam_language._logged = False
            logger.warning(f"[GeoIP] 获取用户 IP 地区失败: {geo_error}，默认为非大陆用户")
            ip_country = None
            is_mainland_china = False
        
        return {
            "success": True,
            "uiLanguage": ui_language,
            "steam_language": steam_language,
            "i18n_language": i18n_language,
            "ip_country": ip_country,
            "is_mainland_china": is_mainland_china
        }
        
    except Exception as e:
        logger.error(f"获取 Steam 语言设置失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "uiLanguage": ui_language,
            "steam_language": None,
            "i18n_language": None,
            "ip_country": None,
            "is_mainland_china": False  # On errors, default non-mainland.
        }


@router.get("/user_language")
async def get_user_language_api():
    """
    Get the user language setting (used by the frontend subtitle module).
    
    Priority: Steam settings > system settings
    Returns a normalized language code ('zh', 'en', 'ja').
    """
    from utils.language_utils import get_global_language

    try:
        # Use language_utils global language management to handle Steam/system priority automatically.
        # ⚠ Deliberately keep short codes (#2500 step 2 review): this endpoint's return value is a frontend
        # API contract; the docstring above specifies 'zh' / 'en' / 'ja', and callers may branch
        # on those codes. Full codes change the contract and require auditing all frontend callers,
        # outside this locale-migration batch.
        language = get_global_language()
        
        return {
            "success": True,
            "language": language
        }
        
    except Exception as e:
        logger.error(f"获取用户语言设置失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "language": "zh"  # Default Chinese.
        }


async def _persist_ui_language_and_sync(normalized: str):
    """Serialize one UI write, character sync, and any compensating rollback."""
    previous_ui_language = await aload_ui_language_override()
    if not await asave_ui_language_override(normalized):
        return JSONResponse(
            {"success": False, "error": "保存界面语言失败"},
            status_code=500,
        )

    try:
        config_manager = get_config_manager()
        characters = await config_manager.aload_characters()
        current_name = str(characters.get("当前猫娘") or "").strip()
        conversation_sync = None
        if current_name and current_name in (characters.get("猫娘") or {}):
            from ..characters_router.language_preference import (
                apply_character_language_preference,
            )

            conversation_sync = await apply_character_language_preference(
                current_name,
                normalized,
            )
            if conversation_sync.get("success") is not True:
                # The memory-server write happens before best-effort context
                # isolation.  If it committed, rolling back only the UI value
                # would split the two settings again.  Keep both languages in
                # sync and surface the isolation failure as a partial warning.
                locale_was_synced = (
                    conversation_sync.get("partial_success") is True
                    and conversation_sync.get("language") == normalized
                )
                if not locale_was_synced:
                    rollback_succeeded = await _rollback_ui_language(
                        previous_ui_language,
                    )
                    return JSONResponse(
                        _ui_language_sync_failure_payload(
                            error=conversation_sync.get("error")
                            or "同步语言偏好失败",
                            normalized=normalized,
                            previous_ui_language=previous_ui_language,
                            rollback_succeeded=rollback_succeeded,
                            character_name=current_name,
                            conversation_sync=conversation_sync,
                        ),
                        status_code=500,
                    )

        return {
            "success": True,
            "language": normalized,
            "character_name": current_name or None,
            "partial_success": bool(
                conversation_sync and conversation_sync.get("partial_success")
            ),
            "conversation_sync": conversation_sync,
        }
    except Exception:
        rollback_succeeded = await _rollback_ui_language(previous_ui_language)
        logger.exception("同步界面语言和语言偏好失败")
        return JSONResponse(
            _ui_language_sync_failure_payload(
                error="同步语言设置失败",
                normalized=normalized,
                previous_ui_language=previous_ui_language,
                rollback_succeeded=rollback_succeeded,
            ),
            status_code=503,
        )


@router.put("/ui-language")
async def set_ui_language_api(request: Request):
    """Persist the desktop UI locale and sync the current character preference."""
    from utils.language_utils import (
        is_supported_language_code,
        normalize_language_code,
    )

    try:
        payload = await request.json()
    except Exception:
        payload = None
    validation_error = _validate_local_mutation_request(
        request,
        payload=payload if isinstance(payload, dict) else None,
    )
    if validation_error is not None:
        return validation_error

    language = payload.get("language") if isinstance(payload, dict) else None
    if not is_supported_language_code(language):
        return JSONResponse(
            {"success": False, "error": "不支持的语言"},
            status_code=400,
        )

    normalized = normalize_language_code(language, format="full")
    async with _UI_LANGUAGE_SYNC_LOCK:
        return await _persist_ui_language_and_sync(normalized)
