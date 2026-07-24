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

from ._shared import logger, router

from ..shared_state import ensure_steamworks
from utils.preferences import aload_ui_language_override


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
            # 没有 Steam 环境，默认为非大陆用户
            return {
                "success": False,
                "error": "Steamworks 未初始化",
                "uiLanguage": ui_language,
                "steam_language": None,
                "i18n_language": None,
                "ip_country": None,
                "is_mainland_china": False  # 无 Steam 环境，默认非大陆
            }
        
        # 获取 Steam 当前游戏语言
        steam_language = steamworks.Apps.GetCurrentGameLanguage()
        # Steam API 可能返回 bytes，需要解码为字符串
        if isinstance(steam_language, bytes):
            steam_language = steam_language.decode('utf-8')
        
        # 使用 language_utils 的归一化函数，统一映射逻辑
        # format='full' 返回 'zh-CN', 'zh-TW', 'en', 'ja', 'ko' 格式（用于前端 i18n）
        i18n_language = normalize_language_code(steam_language, format='full')

        # 把这一次 Steam 真值回写到进程全局缓存：``initialize_global_language`` 在启动
        # 时只读一次 Steam SDK，race 失败就锁死系统 locale；前端 bootstrap 这次能拿到
        # 对的 schinese → zh-CN，把它顺手塞回缓存，下游 ``get_global_language()``
        # 全部受益（mini-game prompt / memory / reflection / tts ...）。函数自己有
        # "无变化即 no-op" 的守卫，前端反复刷新也不会刷屏。
        # 注意校验**原始 steam_language**而非 normalize 后的 i18n_language——后者对空 /
        # 未知输入会默认回退 'en'，那是一个合法值能通过 refresh 内部白名单，会把已经
        # 正确的全局缓存（来自 startup init / 上一次有效刷新）误覆盖成 en；前端 i18n
        # 兜底用 'en' 不受影响（i18n_language 仍正常返回）。
        if is_supported_language_code(steam_language):
            try:
                refresh_global_language(steam_language)
            except Exception:
                logger.debug("refresh_global_language 失败", exc_info=True)

        # 获取用户 IP 所在国家（用于判断是否为中国大陆用户）
        ip_country = None
        is_mainland_china = False
        
        try:
            # 使用 Steam Utils API 获取用户 IP 所在国家
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
            # 仅在真的拿到国家码时回写：GetIPCountry() 返回空是"暂时不知道"（Steam
            # 刚起来还没连上网络时就会这样），而 is_mainland_china 此时保持 False，
            # 直接回写等于用 not False 断言"海外"——凭无数据把线路推向海外节点。
            if ip_country:
                try:
                    from utils.config_manager import ConfigManager
                    ConfigManager._steam_check_cache = not is_mainland_china
                    # 清合并缓存触发重算。注意 Steam 只是兜底票：重算时如果 IP 探测
                    # 已有结论，仍按 IP 走，这次回写只在 IP 始终无结论时才决定线路。
                    ConfigManager._region_cache = None
                except Exception:
                    pass
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
            "is_mainland_china": False  # 发生错误时，默认非大陆
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
        # 使用 language_utils 的全局语言管理，自动处理 Steam/系统语言优先级
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
            "language": "zh"  # 默认中文
        }
