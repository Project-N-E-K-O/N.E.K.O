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

"""Character/LLM info resolution and request-language absorption (keeps the globals()-coupled _get_character_info trio together).

Split out of the former monolithic ``main_routers/game_router.py``.
"""

from ._shared import logger

from typing import Any, Dict
from ..shared_state import get_config_manager, get_session_manager
from utils.language_utils import get_global_language, normalize_language_code, is_supported_language_code


def _extract_request_language_full(data: Any) -> str | None:
    if not isinstance(data, dict):
        return None
    raw = data.get('i18n_language') or data.get('language') or data.get('lang')
    if not raw or not is_supported_language_code(raw):
        return None
    try:
        return normalize_language_code(str(raw), format='full')
    except Exception:
        return None


def _absorb_request_language(data: Any, lanlan_name: str | None) -> str | None:
    """Extract the frontend i18n ground truth from the request body, writing it back to ``mgr.user_language`` along the way.

    Motivation: ``mgr.user_language`` gets overwritten by the global cache on the
    ``start_session`` path (see ``main_logic/core.py``), and the global cache is the
    product of a Steam SDK startup race failure. The frontend i18n has already
    fetched the correct value asynchronously from ``/api/config/steam_language``;
    piggybacking it on the request body lets us correct the session state
    immediately. Returns the normalized short code (``zh`` / ``en`` ...); returns
    ``None`` when unavailable, letting the caller follow the old fallback chain.
    """
    if not isinstance(data, dict):
        return None
    raw = data.get('i18n_language') or data.get('language') or data.get('lang')
    if not raw:
        return None
    # 前端 / 第三方客户端可能传 ``'undefined'`` / corrupted localStorage / 任意 garbage，
    # ``normalize_language_code`` 对未识别值默认回退 ``'en'``，会静默把 mgr.user_language
    # 翻成英文。回写 session 状态前先用公共白名单 helper 挡掉。
    if not is_supported_language_code(raw):
        return None
    try:
        normalized_short = normalize_language_code(str(raw), format='short')
    except Exception:
        return None
    if not normalized_short:
        return None
    try:
        name = str(lanlan_name or "").strip()
        if name:
            session_manager = get_session_manager()
            manager = session_manager.get(name) if hasattr(session_manager, "get") else None
            if manager is not None:
                normalized_full = normalize_language_code(str(raw), format='full')
                current = getattr(manager, "user_language", None)
                if normalized_full and current != normalized_full:
                    setter = getattr(manager, "set_user_language", None)
                    if callable(setter):
                        setter(str(raw))
    except Exception:
        logger.debug("🎮 absorb request language 失败 lanlan=%s", lanlan_name, exc_info=True)
    return normalized_short


def _resolve_game_prompt_language(
    lanlan_name: str | None = None,
    data: Any = None,
) -> str:
    """Resolve the user's current language for game-route LLM prompts.

    Priority (same shape as ``main_routers/system_router._resolve_proactive_locale``):
      1. ``i18n_language`` / ``language`` / ``lang`` in ``data`` (request body)
         — explicitly sent by the frontend, highest priority; the value is also
         written back to ``mgr.user_language`` so every downstream
         ``_resolve_game_prompt_language`` in this request without access to
         ``data`` hits it too.
      2. ``mgr.user_language`` — the session ground truth synced by the websocket
         greeting_check.
      3. ``get_global_language()`` — process-level cache, final fallback.

    Layer 1 covers a hole beyond PR #1150: in a Steam=zh / system=en environment,
    ``mgr.user_language`` gets overwritten by the (wrong, 'en') global cache at
    ``start_session``, and the soccer short-circuit fires before the frontend ws
    greeting_check pushes the right value up, so it could only see the wrong 'en'.
    Making the request body's i18n truth the top-priority source, combined with
    the self-healing write-back, closes this race.
    """
    request_lang = _absorb_request_language(data, lanlan_name)
    if request_lang:
        return request_lang
    try:
        name = str(lanlan_name or "").strip()
        session_manager = get_session_manager()
        manager = session_manager.get(name) if name and hasattr(session_manager, "get") else None
        language = getattr(manager, "user_language", None)
        if language:
            return normalize_language_code(str(language), format="short") or "en"
    except Exception:
        logger.debug(
            "🎮 赛后归档语言解析失败，使用默认 prompt 语言: lanlan=%s",
            lanlan_name,
            exc_info=True,
        )

    try:
        return normalize_language_code(get_global_language(), format="short") or "en"
    except Exception:
        return "en"


def _get_character_info(lanlan_name: str | None = None) -> Dict[str, Any]:
    """Get the specified character's info from shared_state; uses the current character when unspecified."""
    try:
        config_manager = get_config_manager()
    except RuntimeError:
        # Unit tests historically monkeypatch _get_current_character_info()
        # without bootstrapping shared_state. Keep that seam usable while the
        # production path below supports explicit lanlan_name lookup.
        current_getter = globals().get("_get_current_character_info")
        if getattr(current_getter, "__name__", "") != "_get_current_character_info":
            info = dict(current_getter())
            if lanlan_name:
                info.setdefault("lanlan_name", str(lanlan_name or "").strip())
            info.setdefault("user_language", _resolve_game_prompt_language(info.get("lanlan_name")))
            return info
        raise
    characters = config_manager.load_characters()
    current_name = str(lanlan_name or characters.get('当前猫娘', '') or '').strip()

    master_data = characters.get('主人', {})
    # 显式 str 归一化：'档案名' 来自用户编辑的角色配置 JSON，可能是 None / 数字
    # / 其他非字符串。下面 .replace 的第二个参数必须是 str，且 master_name 还会
    # 直接进入返回 dict 给下游消费，统一在源头收口。
    master_name = str(master_data.get('档案名', '玩家') or '玩家')

    # 获取角色人格 prompt
    # Why: lanlan_prompt_map 存的是带 {LANLAN_NAME} / {MASTER_NAME} 占位符的原始
    # 模板（普通会话路径在 main_server 写入 SessionManager 时才替换）。Game 流程
    # 直接从 config_manager 拿，必须在源头补这一步替换，否则下游 _build_game_prompt
    # / quick_lines / pregame context AI 拼出来的 prompt 会含字面占位符，触发
    # llm_prompt_leak_check 警告并污染人设。
    # 模板本身也用 str() 兜底：极端情况下 lanlan_prompt_map 里的值可能是 None。
    _, _, _, _, _, lanlan_prompt_map, _, _, _ = config_manager.get_character_data()
    lanlan_prompt = str(lanlan_prompt_map.get(current_name, '') or '') \
        .replace('{LANLAN_NAME}', current_name) \
        .replace('{MASTER_NAME}', master_name)

    # 获取小游戏主模型配置；默认跟随文本对话模型，用户可在 API 设置中独立覆盖。
    conversation_config = config_manager.get_model_api_config('game_main')

    return {
        'lanlan_name': current_name,
        'master_name': master_name,
        'lanlan_prompt': lanlan_prompt,
        'model': conversation_config.get('model', ''),
        'base_url': conversation_config.get('base_url', ''),
        'api_type': conversation_config.get('api_type', ''),
        'provider_type': conversation_config.get('provider_type', ''),
        'api_key': conversation_config.get('api_key', ''),
        'user_language': _resolve_game_prompt_language(current_name),
    }


def _get_current_character_info() -> Dict[str, Any]:
    """Get the current character's info from shared_state."""
    return _get_character_info()


def _get_game_route_summary_llm_info(lanlan_name: str | None = None) -> Dict[str, Any]:
    """Resolve character metadata but use the summary model tier for helper calls."""
    info = dict(_get_character_info(lanlan_name))
    try:
        summary_config = get_config_manager().get_model_api_config("game_summary") or {}
    except RuntimeError:
        return info
    model = str(summary_config.get("model") or "").strip()
    base_url = str(summary_config.get("base_url") or "").strip()
    api_key = str(summary_config.get("api_key") or "").strip()
    if not (model and base_url):
        return info
    info.update({
        "model": model,
        "base_url": base_url,
        "api_type": summary_config.get("api_type") or info.get("api_type", ""),
        "provider_type": summary_config.get("provider_type") or info.get("provider_type", ""),
        "api_key": api_key,
    })
    return info
