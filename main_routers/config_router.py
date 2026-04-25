# -*- coding: utf-8 -*-
"""
Config Router

Handles configuration-related API endpoints including:
- User preferences
- API configuration (core and custom APIs)
- Steam language settings
- API providers
"""

import asyncio
import json
import os
import ssl
import threading
import urllib.parse
from typing import Optional

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .shared_state import get_config_manager, get_steamworks, get_session_manager, get_initialize_character_data
from .characters_router import get_current_live2d_model
from utils.file_utils import atomic_write_json_async, read_json_async
from utils.preferences import aload_user_preferences, update_model_preferences, validate_model_preferences, move_model_to_top, aload_global_conversation_settings, save_global_conversation_settings, GLOBAL_CONVERSATION_KEY
from utils.cloudsave_runtime import MaintenanceModeError
from utils.logger_config import get_module_logger
from utils.config_manager import get_reserved
from config import (
    AUTOSTART_CSRF_TOKEN,
    CHARACTER_SYSTEM_RESERVED_FIELDS,
    CHARACTER_WORKSHOP_RESERVED_FIELDS,
    CHARACTER_RESERVED_FIELDS,
)


router = APIRouter(prefix="/api/config", tags=["config"])


def _apply_noise_reduction_to_active_sessions(enabled: bool):
    """Apply noise reduction toggle to all active voice sessions immediately."""
    from main_logic.omni_realtime_client import OmniRealtimeClient
    try:
        session_manager = get_session_manager()
        for _name, mgr in session_manager.items():
            if not mgr.is_active or mgr.session is None:
                continue
            if not isinstance(mgr.session, OmniRealtimeClient):
                continue
            ap = getattr(mgr.session, '_audio_processor', None)
            if ap is not None:
                ap.set_enabled(enabled)
    except Exception as e:
        logger.warning(f"Failed to apply noise reduction to active sessions: {e}")


# --- proxy mode helpers ---
_PROXY_LOCK = threading.Lock()
_proxy_snapshot: dict[str, str] = {}
logger = get_module_logger(__name__, "Main")

# VRM 模型路径常量
VRM_STATIC_PATH = "/static/vrm"  # 项目目录下的 VRM 模型路径
VRM_USER_PATH = "/user_vrm"  # 用户文档目录下的 VRM 模型路径

# MMD 模型路径常量
MMD_STATIC_PATH = "/static/mmd"  # 项目目录下的 MMD 模型路径
MMD_USER_PATH = "/user_mmd"  # 用户文档目录下的 MMD 模型路径


def _resolve_master_display_name(master_basic_config: dict, fallback_name: str = "") -> str:
    nickname = str(master_basic_config.get('昵称', '') or '').strip()
    if nickname:
        first_nickname = nickname.split(',')[0].split('，')[0].strip()
        if first_nickname:
            return first_nickname
    profile_name = str(master_basic_config.get('档案名', '') or '').strip()
    if profile_name:
        return profile_name
    return str(fallback_name or '').strip()


@router.get("/character_reserved_fields")
async def get_character_reserved_fields():
    """返回角色档案保留字段配置（供前端与路由统一使用）。"""
    return {
        "success": True,
        "system_reserved_fields": list(CHARACTER_SYSTEM_RESERVED_FIELDS),
        "workshop_reserved_fields": list(CHARACTER_WORKSHOP_RESERVED_FIELDS),
        "all_reserved_fields": list(CHARACTER_RESERVED_FIELDS),
    }


# MMD 文件扩展名
_MMD_EXTENSIONS = {'.pmx', '.pmd'}


def _get_live3d_sub_type(catgirl_config: dict) -> str:
    """判断 Live3D 模式下应使用 VRM 还是 MMD 渲染器。
    优先使用持久化的子类型；缺失或失效时再按模型路径回退判断。"""
    stored_sub_type = str(
        get_reserved(
            catgirl_config,
            'avatar',
            'live3d_sub_type',
            default='',
            legacy_keys=('live3d_sub_type',),
        )
        or ''
    ).strip().lower()
    if stored_sub_type in {'mmd', 'vrm'}:
        return stored_sub_type

    mmd_path = get_reserved(catgirl_config, 'avatar', 'mmd', 'model_path', default='')
    if mmd_path:
        return 'mmd'
    vrm_path = get_reserved(catgirl_config, 'avatar', 'vrm', 'model_path', default='', legacy_keys=('vrm',))
    if vrm_path:
        return 'vrm'
    return ''


def _resolve_vrm_path(vrm_path: str, _config_manager, target_name: str) -> str:
    """解析 VRM 模型路径，验证文件存在性，返回可用 URL 或空字符串。"""
    if vrm_path.startswith('http://') or vrm_path.startswith('https://'):
        logger.debug(f"获取页面配置 - 角色: {target_name}, VRM模型HTTP路径: {vrm_path}")
        return vrm_path
    elif vrm_path.startswith('/'):
        _vrm_file_verified = False
        if vrm_path.startswith(VRM_USER_PATH + '/'):
            _fname = vrm_path[len(VRM_USER_PATH) + 1:]
            _vrm_file_verified = (_config_manager.vrm_dir / _fname).exists()
        elif vrm_path.startswith(VRM_STATIC_PATH + '/'):
            _fname = vrm_path[len(VRM_STATIC_PATH) + 1:]
            _vrm_file_verified = (_config_manager.project_root / 'static' / 'vrm' / _fname).exists()
        else:
            _vrm_file_verified = True
        if _vrm_file_verified:
            logger.debug(f"获取页面配置 - 角色: {target_name}, VRM模型绝对路径: {vrm_path}")
            return vrm_path
        else:
            logger.warning(f"获取页面配置 - 角色: {target_name}, VRM模型文件未找到: {vrm_path}")
            return ""
    else:
        from pathlib import PurePosixPath
        safe_rel = PurePosixPath(vrm_path)
        if safe_rel.is_absolute() or '..' in safe_rel.parts:
            logger.warning(f"获取页面配置 - 角色: {target_name}, VRM路径不合法: {vrm_path}")
            return ""
        project_vrm_path = _config_manager.project_root / 'static' / 'vrm' / str(safe_rel)
        if project_vrm_path.exists():
            result = f'{VRM_STATIC_PATH}/{safe_rel}'
            logger.debug(f"获取页面配置 - 角色: {target_name}, VRM模型在项目目录: {vrm_path} -> {result}")
            return result
        user_vrm_path = _config_manager.vrm_dir / str(safe_rel)
        if user_vrm_path.exists():
            result = f'{VRM_USER_PATH}/{safe_rel}'
            logger.debug(f"获取页面配置 - 角色: {target_name}, VRM模型在用户目录: {vrm_path} -> {result}")
            return result
        logger.warning(f"获取页面配置 - 角色: {target_name}, VRM模型文件未找到: {vrm_path}")
        return ""


def _resolve_mmd_path(mmd_path: str, _config_manager, target_name: str) -> str:
    """解析 MMD 模型路径，验证文件存在性，返回可用 URL 或空字符串。"""
    if mmd_path.startswith('http://') or mmd_path.startswith('https://'):
        logger.debug(f"获取页面配置 - 角色: {target_name}, MMD模型HTTP路径: {mmd_path}")
        return mmd_path
    elif mmd_path.startswith('/'):
        _mmd_file_verified = False
        if mmd_path.startswith(MMD_USER_PATH + '/'):
            _fname = mmd_path[len(MMD_USER_PATH) + 1:]
            _mmd_file_verified = (_config_manager.mmd_dir / _fname).exists()
        elif mmd_path.startswith(MMD_STATIC_PATH + '/'):
            _fname = mmd_path[len(MMD_STATIC_PATH) + 1:]
            _mmd_file_verified = (_config_manager.project_root / 'static' / 'mmd' / _fname).exists()
        else:
            _mmd_file_verified = True
        if _mmd_file_verified:
            logger.debug(f"获取页面配置 - 角色: {target_name}, MMD模型绝对路径: {mmd_path}")
            return mmd_path
        else:
            logger.warning(f"获取页面配置 - 角色: {target_name}, MMD模型文件未找到: {mmd_path}")
            return ""
    else:
        from pathlib import PurePosixPath
        safe_rel = PurePosixPath(mmd_path)
        if safe_rel.is_absolute() or '..' in safe_rel.parts:
            logger.warning(f"获取页面配置 - 角色: {target_name}, MMD路径不合法: {mmd_path}")
            return ""
        project_mmd_path = _config_manager.project_root / 'static' / 'mmd' / str(safe_rel)
        if project_mmd_path.exists():
            result = f'{MMD_STATIC_PATH}/{safe_rel}'
            logger.debug(f"获取页面配置 - 角色: {target_name}, MMD模型在项目目录: {mmd_path} -> {result}")
            return result
        user_mmd_path = _config_manager.mmd_dir / str(safe_rel)
        if user_mmd_path.exists():
            result = f'{MMD_USER_PATH}/{safe_rel}'
            logger.debug(f"获取页面配置 - 角色: {target_name}, MMD模型在用户目录: {mmd_path} -> {result}")
            return result
        logger.warning(f"获取页面配置 - 角色: {target_name}, MMD模型文件未找到: {mmd_path}")
        return ""


@router.get("/page_config")
async def get_page_config(response: Response, lanlan_name: str = ""):
    """获取页面配置(lanlan_name 和 model_path),支持Live2D、VRM和MMD(Live3D)模型"""
    try:
        response.headers["Cache-Control"] = "no-store"
        response.headers["Pragma"] = "no-cache"

        # 获取角色数据
        _config_manager = get_config_manager()
        master_name, her_name, master_basic_config, lanlan_basic_config, _, _, _, _, _ = await _config_manager.aget_character_data()
        master_display_name = _resolve_master_display_name(master_basic_config, master_name)
        
        # 如果提供了 lanlan_name 参数，使用它；否则使用当前角色
        target_name = lanlan_name if lanlan_name else her_name
        
        # 获取角色配置
        catgirl_config = lanlan_basic_config.get(target_name, {})
        model_type = get_reserved(catgirl_config, 'avatar', 'model_type', default='live2d', legacy_keys=('model_type',))
        # 归一化：旧配置中的 'vrm' 统一为 'live3d'
        if model_type == 'vrm':
            model_type = 'live3d'
        
        model_path = ""
        lighting = None
        # live3d_sub_type: 前端用于区分 Live3D 模式下加载 VRM 还是 MMD 渲染器
        live3d_sub_type = ""
        
        # 根据模型类型获取模型路径
        if model_type == 'live3d' and _get_live3d_sub_type(catgirl_config) == 'vrm':
            live3d_sub_type = 'vrm'
            # VRM模型：处理路径转换
            vrm_path = get_reserved(catgirl_config, 'avatar', 'vrm', 'model_path', default='', legacy_keys=('vrm',))
            if vrm_path:
                model_path = _resolve_vrm_path(vrm_path, _config_manager, target_name)
            else:
                logger.warning(f"角色 {target_name} 的VRM模型路径为空")
            saved_lighting = get_reserved(
                catgirl_config,
                'avatar',
                'vrm',
                'lighting',
                default=None,
                legacy_keys=('lighting',),
            )
            if isinstance(saved_lighting, dict):
                lighting = dict(saved_lighting)
        elif model_type == 'live3d' and _get_live3d_sub_type(catgirl_config) == 'mmd':
            live3d_sub_type = 'mmd'
            # MMD模型：处理路径转换
            mmd_path = get_reserved(catgirl_config, 'avatar', 'mmd', 'model_path', default='')
            if mmd_path:
                model_path = _resolve_mmd_path(mmd_path, _config_manager, target_name)
            else:
                logger.warning(f"角色 {target_name} 的MMD模型路径为空")
        elif model_type == 'live3d':
            # live3d 但无法判断子类型（两个路径都为空），返回空路径
            live3d_sub_type = ''
            logger.warning(f"角色 {target_name} 的Live3D模型路径均为空")
        else:
            # Live2D模型：使用原有逻辑
            live2d = get_reserved(catgirl_config, 'avatar', 'live2d', 'model_path', default='mao_pro', legacy_keys=('live2d',))
            live2d_item_id = get_reserved(
                catgirl_config,
                'avatar',
                'asset_source_id',
                default='',
                legacy_keys=('live2d_item_id', 'item_id'),
            )
            
            logger.debug(f"获取页面配置 - 角色: {target_name}, Live2D模型: {live2d}, item_id: {live2d_item_id}")
        
            model_response = await get_current_live2d_model(target_name, live2d_item_id)
            # 提取JSONResponse中的内容
            model_data = model_response.body.decode('utf-8')
            model_json = json.loads(model_data)
            model_info = model_json.get('model_info', {})
            model_path = model_info.get('path', '')
        
        result = {
            "success": True,
            "lanlan_name": target_name,
            "master_name": master_name or "",
            "master_profile_name": str(master_basic_config.get('档案名', '') or ''),
            "master_nickname": str(master_basic_config.get('昵称', '') or ''),
            "master_display_name": master_display_name or "",
            "autostart_csrf_token": AUTOSTART_CSRF_TOKEN,
            "model_path": model_path,
            "model_type": model_type,
            "lighting": lighting,
        }
        if model_type == 'live3d':
            result["live3d_sub_type"] = live3d_sub_type
        return result
    except Exception as e:
        logger.error(f"获取页面配置失败: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "lanlan_name": "",
            "master_name": "",
            "master_profile_name": "",
            "master_nickname": "",
            "master_display_name": "",
            "autostart_csrf_token": AUTOSTART_CSRF_TOKEN,
            "model_path": "",
            "model_type": ""
        }


@router.get("/preferences")
async def get_preferences():
    """获取用户偏好设置"""
    preferences = await aload_user_preferences()
    return preferences


@router.post("/preferences")
async def save_preferences(request: Request):
    """保存用户偏好设置"""
    try:
        data = await request.json()
        if not data:
            return {"success": False, "error": "无效的数据"}
        
        # 验证偏好数据
        if not validate_model_preferences(data):
            return {"success": False, "error": "偏好数据格式无效"}
        
        # 防止使用保留的全局对话设置键作为模型路径
        if data.get('model_path') == GLOBAL_CONVERSATION_KEY:
            return {"success": False, "error": "model_path 不能使用保留键"}
        
        # 获取参数（可选）
        parameters = data.get('parameters')
        # 获取显示器信息（可选，用于多屏幕位置恢复）
        display = data.get('display')
        # 获取旋转信息（可选，用于VRM模型朝向）
        rotation = data.get('rotation')
        # 获取视口信息（可选，用于跨分辨率位置和缩放归一化）
        viewport = data.get('viewport')
        # 获取相机位置信息（可选，用于恢复VRM滚轮缩放状态）
        camera_position = data.get('camera_position')

        # 验证和清理 viewport 数据
        if viewport is not None:
            if not isinstance(viewport, dict):
                viewport = None
            else:
                # 验证必需的数值字段
                width = viewport.get('width')
                height = viewport.get('height')
                if not (isinstance(width, (int, float)) and isinstance(height, (int, float)) and
                        width > 0 and height > 0):
                    viewport = None

        # 更新偏好（底层 atomic_write_json 会阻塞事件循环，offload 到线程池）
        ok = await asyncio.to_thread(
            update_model_preferences,
            data['model_path'], data['position'], data['scale'], parameters, display, rotation, viewport, camera_position,
        )
        if ok:
            return {"success": True, "message": "偏好设置已保存"}
        else:
            return {"success": False, "error": "保存失败"}
            
    except MaintenanceModeError:
        raise
    except Exception as e:
        return {"success": False, "error": str(e)}



@router.post("/preferences/set-preferred")
async def set_preferred_model(request: Request):
    """设置首选模型"""
    try:
        data = await request.json()
        if not data or 'model_path' not in data:
            return {"success": False, "error": "无效的数据"}
        
        if move_model_to_top(data['model_path']):
            return {"success": True, "message": "首选模型已更新"}
        else:
            return {"success": False, "error": "模型不存在或更新失败"}
            
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/conversation-settings")
async def get_conversation_settings():
    """获取全局对话设置（从 user_preferences.json 同步备份中读取）"""
    try:
        settings = await aload_global_conversation_settings()
        return {"success": True, "settings": settings}
    except Exception as e:
        logger.exception(f"获取对话设置失败: {e}")
        return {"success": False, "error": "Internal server error", "settings": {}}


@router.post("/conversation-settings")
async def save_conversation_settings(request: Request):
    """保存全局对话设置（同步到 user_preferences.json 备份）"""
    try:
        data = await request.json()
        if not isinstance(data, dict):
            return {"success": False, "error": "请求体必须为对象"}

        if not await asyncio.to_thread(save_global_conversation_settings, data):
            return {"success": False, "error": "保存失败"}

        if 'noiseReductionEnabled' in data:
            _apply_noise_reduction_to_active_sessions(data['noiseReductionEnabled'])

        return {"success": True, "message": "对话设置已保存"}
    except MaintenanceModeError:
        raise
    except Exception as e:
        logger.exception(f"保存对话设置失败: {e}")
        return {"success": False, "error": "Internal server error"}


@router.get("/steam_language")
async def get_steam_language():
    """获取 Steam 客户端的语言设置和 GeoIP 信息，用于前端 i18n 初始化和区域检测
    
    返回字段：
    - success: 是否成功
    - steam_language: Steam 原始语言设置
    - i18n_language: 归一化的 i18n 语言代码
    - ip_country: 用户 IP 所在国家代码（如 "CN"）
    - is_mainland_china: 是否为中国大陆用户（基于语言设置存在 + IP 为 CN）
    
    判断逻辑：
    - 如果存在 Steam 语言设置（即有 Steam 环境），则检查 GeoIP
    - 如果 IP 国家代码为 "CN"，则标记为中国大陆用户
    - 如果不存在 Steam 语言设置（无 Steam 环境），默认为非大陆用户
    """
    from utils.language_utils import normalize_language_code
    
    try:
        steamworks = get_steamworks()
        
        if steamworks is None:
            # 没有 Steam 环境，默认为非大陆用户
            return {
                "success": False,
                "error": "Steamworks 未初始化",
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
            # Write Steam result to ConfigManager's steam-specific cache
            try:
                from utils.config_manager import ConfigManager
                ConfigManager._steam_check_cache = not is_mainland_china
                ConfigManager._region_cache = None  # reset combined cache for recomputation
            except Exception:
                pass
        except Exception as geo_error:
            get_steam_language._logged = False
            logger.warning(f"[GeoIP] 获取用户 IP 地区失败: {geo_error}，默认为非大陆用户")
            ip_country = None
            is_mainland_china = False
        
        return {
            "success": True,
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
            "steam_language": None,
            "i18n_language": None,
            "ip_country": None,
            "is_mainland_china": False  # 发生错误时，默认非大陆
        }


@router.get("/user_language")
async def get_user_language_api():
    """
    获取用户语言设置（供前端字幕模块使用）
    
    优先级：Steam设置 > 系统设置
    返回归一化的语言代码（'zh', 'en', 'ja'）
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



@router.get("/core_api")
async def get_core_config_api():
    """获取核心配置（API Key）"""
    try:
        # 尝试从core_config.json读取
        try:
            from utils.config_manager import get_config_manager
            config_manager = get_config_manager()
            core_config_path = str(config_manager.get_config_path('core_config.json'))
            core_cfg = await read_json_async(core_config_path)
            api_key = core_cfg.get('coreApiKey', '')
        except FileNotFoundError:
            # 如果文件不存在，返回当前配置中的CORE_API_KEY
            _config_manager = get_config_manager()
            core_config = await _config_manager.aget_core_config()
            api_key = core_config.get('CORE_API_KEY','')
            # 创建空的配置对象用于返回默认值
            core_cfg = {}
        
        # 旧版本 core_config.json 可能只有 coreApiKey 而没有各 assistApiKey* 字段，
        # 需要与 ConfigManager.get_core_config() 保持一致的回退逻辑，
        # 但只能回退到与 coreApi / assistApi 匹配的服务商，
        # 以免将不兼容的 API Key 填充到其他服务商。
        fallback_key = api_key or ''
        _core_api_provider = core_cfg.get('coreApi') or 'qwen'
        _assist_api_provider = core_cfg.get('assistApi') or 'qwen'
        _fallback_providers = {_core_api_provider, _assist_api_provider}

        def _fb(provider: str) -> str:
            """仅当 provider 与用户选择的 coreApi/assistApi 一致时才回退到 coreApiKey"""
            return fallback_key if provider in _fallback_providers else ''

        return {
            "api_key": api_key,
            "coreApi": _core_api_provider,
            "assistApi": _assist_api_provider,
            "assistApiKeyQwen": core_cfg.get('assistApiKeyQwen', '') or _fb('qwen'),
            "assistApiKeyQwenIntl": core_cfg.get('assistApiKeyQwenIntl', '') or _fb('qwen_intl'),
            "assistApiKeyOpenai": core_cfg.get('assistApiKeyOpenai', '') or _fb('openai'),
            "assistApiKeyGlm": core_cfg.get('assistApiKeyGlm', '') or _fb('glm'),
            "assistApiKeyStep": core_cfg.get('assistApiKeyStep', '') or _fb('step'),
            "assistApiKeySilicon": core_cfg.get('assistApiKeySilicon', '') or _fb('silicon'),
            "assistApiKeyGemini": core_cfg.get('assistApiKeyGemini', '') or _fb('gemini'),
            "assistApiKeyKimi": core_cfg.get('assistApiKeyKimi', '') or _fb('kimi'),
            "assistApiKeyDeepseek": core_cfg.get('assistApiKeyDeepseek', '') or _fb('deepseek'),
            "assistApiKeyDoubao": core_cfg.get('assistApiKeyDoubao', '') or _fb('doubao'),
            # MiniMax 是 assist-only（TTS 专用），不在 coreApi 候选集里，
            # coreApiKey 永远不是 minimax 兼容的；不 fallback，以免把无效 key
            # 塞进 TTS 凭证槽位导致 401，掩盖"未配置 minimax key"的真实提示。
            "assistApiKeyMinimax": core_cfg.get('assistApiKeyMinimax', ''),
            "assistApiKeyMinimaxIntl": core_cfg.get('assistApiKeyMinimaxIntl', ''),
            "assistApiKeyGrok": core_cfg.get('assistApiKeyGrok', '') or _fb('grok'),
            "assistApiKeyClaude": core_cfg.get('assistApiKeyClaude', '') or _fb('claude'),
            "assistApiKeyOpenrouter": core_cfg.get('assistApiKeyOpenrouter', '') or _fb('openrouter'),
            "mcpToken": core_cfg.get('mcpToken', ''),
            "openclawUrl": core_cfg.get('openclawUrl'),
            "openclawTimeout": core_cfg.get('openclawTimeout'),
            "openclawDefaultSenderId": core_cfg.get('openclawDefaultSenderId'),
            "enableCustomApi": core_cfg.get('enableCustomApi', False),
            # 自定义API相关字段（Provider / Url / Id / ApiKey per model type）
            **{
                f'{mt}Model{suffix}': core_cfg.get(f'{mt}Model{suffix}', '')
                for mt in ('conversation', 'summary', 'correction', 'emotion',
                           'vision', 'agent', 'omni', 'tts')
                for suffix in ('Provider', 'Url', 'Id', 'ApiKey')
            },
            "gptsovitsEnabled": core_cfg.get('gptsovitsEnabled'),
            "ttsVoiceId": core_cfg.get('ttsVoiceId', ''),
            "disableTts": core_cfg.get('disableTts', False) is True or str(core_cfg.get('disableTts', False)).lower() in ('true', '1', 'yes', 'on'),
            "success": True
        }
    except Exception as e:
        return {
            "success": False,
            "error": str(e)
        }



@router.post("/core_api")
async def update_core_config(request: Request):
    """更新核心配置（API Key）"""
    try:
        data = await request.json()
        if not data:
            return {"success": False, "error": "无效的数据"}
        
        # 检查是否启用了自定义API
        enable_custom_api = data.get('enableCustomApi', False)
        
        # 如果启用了自定义API，不需要强制检查核心API key
        if not enable_custom_api:
            # 检查是否为免费版配置
            is_free_version = data.get('coreApi') == 'free' or data.get('assistApi') == 'free'
            
            if 'coreApiKey' not in data:
                return {"success": False, "error": "缺少coreApiKey字段"}
            
            api_key = data['coreApiKey']
            if api_key is None:
                return {"success": False, "error": "API Key不能为null"}
            
            if not isinstance(api_key, str):
                return {"success": False, "error": "API Key必须是字符串类型"}
            
            api_key = api_key.strip()
            
            # 免费版允许使用 'free-access' 作为API key，不进行空值检查
            if not is_free_version and not api_key:
                return {"success": False, "error": "API Key不能为空"}
        
        # 保存到core_config.json
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        
        # 构建配置对象
        core_cfg = {}
        
        # 只有在启用自定义API时，才允许不设置coreApiKey
        if enable_custom_api:
            # 启用自定义API时，coreApiKey是可选的
            if 'coreApiKey' in data:
                api_key = data['coreApiKey']
                if api_key is not None and isinstance(api_key, str):
                    core_cfg['coreApiKey'] = api_key.strip()
        else:
            # 未启用自定义API时，必须设置coreApiKey
            api_key = data.get('coreApiKey', '')
            if api_key is not None and isinstance(api_key, str):
                core_cfg['coreApiKey'] = api_key.strip()
        if 'coreApi' in data:
            core_cfg['coreApi'] = data['coreApi']
        if 'assistApi' in data:
            core_cfg['assistApi'] = data['assistApi']
        _api_key_fields = [
            'assistApiKeyQwen', 'assistApiKeyQwenIntl', 'assistApiKeyOpenai', 'assistApiKeyDeepseek',
            'assistApiKeyGlm', 'assistApiKeyStep', 'assistApiKeySilicon',
            'assistApiKeyGemini', 'assistApiKeyKimi', 'assistApiKeyDoubao',
            'assistApiKeyMinimax', 'assistApiKeyMinimaxIntl', 'assistApiKeyGrok',
            'assistApiKeyClaude', 'assistApiKeyOpenrouter',
        ]
        for field in _api_key_fields:
            if field in data:
                core_cfg[field] = data[field]
        if 'mcpToken' in data:
            core_cfg['mcpToken'] = data['mcpToken']
        if 'openclawUrl' in data:
            core_cfg['openclawUrl'] = data['openclawUrl']
        if 'openclawTimeout' in data:
            core_cfg['openclawTimeout'] = data['openclawTimeout']
        if 'openclawDefaultSenderId' in data:
            core_cfg['openclawDefaultSenderId'] = data['openclawDefaultSenderId']
        if 'enableCustomApi' in data:
            core_cfg['enableCustomApi'] = data['enableCustomApi']
        if 'gptsovitsEnabled' in data:
            core_cfg['gptsovitsEnabled'] = data['gptsovitsEnabled']
        if 'disableTts' in data:
            if not isinstance(data['disableTts'], bool):
                return {"success": False, "error": "disableTts must be a boolean"}
            core_cfg['disableTts'] = data['disableTts']

        # 自定义API配置（Provider / Url / Id / ApiKey per model type）
        _model_types = [
            'conversation', 'summary', 'correction', 'emotion',
            'vision', 'agent', 'omni', 'tts',
        ]
        for mt in _model_types:
            for suffix in ['Provider', 'Url', 'Id', 'ApiKey']:
                field = f'{mt}Model{suffix}'
                if field in data:
                    core_cfg[field] = data[field]
        if 'ttsVoiceId' in data:
            core_cfg['ttsVoiceId'] = data['ttsVoiceId']
        
        # save_json_config 内部已调用 assert_cloudsave_writable + ensure_config_directory
        # + atomic_write_json，不需要再显式栅栏 / 手工拼 core_config_path
        await asyncio.to_thread(
            config_manager.save_json_config, 'core_config.json', core_cfg
        )


        # API配置更新后，需要先通知所有客户端，再关闭session，最后重新加载配置
        logger.info("API配置已更新，准备通知客户端并重置所有session...")
        
        # 1. 并行通知所有连接的客户端即将刷新（WebSocket还连着）
        # 重要：snapshot (name, mgr, session) 三元组，让 notify 和 end_session 两阶段
        # 操作同一组 mgr **+** 同一份 session：
        # - mgr 维度防新 mgr 被加入第二阶段误杀
        # - session 维度防同一 mgr 在两阶段之间已 rotate 到新 session 被误杀
        #   （前端 reload 后立即重连 → 触发新 session → 第二阶段不应关掉新 session）
        # end_session 内部已有 expected_session stale guard（core.py:3013/3026），
        # 这里把 snapshot 时的 session 传下去即可触发该 guard。
        session_manager = get_session_manager()
        mgr_snapshot = [
            (name, mgr, getattr(mgr, "session", None))
            for name, mgr in session_manager.items()
        ]
        reload_payload = json.dumps({
            "type": "reload_page",
            "message": "API配置已更新，页面即将刷新"
        })

        async def _notify(lanlan_name, mgr):
            if not (mgr.is_active and mgr.websocket):
                return False
            try:
                await mgr.websocket.send_text(reload_payload)
                logger.info(f"已通知 {lanlan_name} 的前端刷新页面")
                return True
            except Exception as e:
                logger.warning(f"通知 {lanlan_name} 的WebSocket失败: {e}")
                return False

        _notify_results = await asyncio.gather(
            *(_notify(n, m) for n, m, _session in mgr_snapshot),
            return_exceptions=True,
        )
        notification_count = sum(1 for r in _notify_results if r is True)
        logger.info(f"已通知 {notification_count} 个客户端")

        # 2. 并行关闭所有活跃的 session（每个 end_session ≈ 1s，串行 N 秒，gather 后 ≈ 1s）
        # 复用上一阶段的 (mgr, session) snapshot，确保不会误杀重连进来的新 mgr，
        # 也不会误杀同一 mgr 在中途 rotate 出来的新 session。
        async def _end(lanlan_name, mgr, expected_session):
            if not mgr.is_active or expected_session is None:
                return None
            try:
                await mgr.end_session(by_server=True, expected_session=expected_session)
                logger.info(f"{lanlan_name} 的session已结束")
                return lanlan_name
            except Exception as e:
                logger.error(f"结束 {lanlan_name} 的session时出错: {e}")
                return None

        _end_results = await asyncio.gather(
            *(_end(n, m, s) for n, m, s in mgr_snapshot),
            return_exceptions=True,
        )
        sessions_ended = [r for r in _end_results if isinstance(r, str)]
        
        # 3. 重新加载配置并重建session manager
        logger.info("正在重新加载配置...")
        try:
            initialize_character_data = get_initialize_character_data()
            await initialize_character_data()
            logger.info("配置重新加载完成，新的API配置已生效")
        except Exception as reload_error:
            logger.error(f"重新加载配置失败: {reload_error}")
            return {"success": False, "error": f"配置已保存但重新加载失败: {str(reload_error)}"}
        
        # 4. Notify agent_server to rebuild CUA adapter with fresh config
        # per-call AsyncClient: 用户保存 API key 才触发，冷路径
        try:
            import httpx
            from config import TOOL_SERVER_PORT
            async with httpx.AsyncClient(timeout=5, proxy=None, trust_env=False) as client:
                await client.post(f"http://127.0.0.1:{TOOL_SERVER_PORT}/notify_config_changed")
            logger.info("已通知 agent_server 刷新 CUA 适配器")
        except Exception as notify_err:
            logger.warning(f"通知 agent_server 刷新 CUA 失败 (非致命): {notify_err}")

        logger.info(f"已通知 {notification_count} 个连接的客户端API配置已更新")
        return {"success": True, "message": "API Key已保存并重新加载配置", "sessions_ended": len(sessions_ended)}
    except MaintenanceModeError:
        raise
    except Exception as e:
        return {"success": False, "error": str(e)}



@router.get("/api_providers")
async def get_api_providers_config():
    """获取API服务商配置（供前端使用）"""
    try:
        from utils.api_config_loader import (
            get_config,
            get_core_api_providers_for_frontend,
            get_assist_api_providers_for_frontend,
        )

        full_config = get_config()
        # 使用缓存加载配置（性能更好，配置更新后需要重启服务）
        core_providers = get_core_api_providers_for_frontend()
        assist_providers = get_assist_api_providers_for_frontend()

        return {
            "success": True,
            "core_api_providers": core_providers,
            "assist_api_providers": assist_providers,
            "api_key_registry": full_config.get("api_key_registry", {}),
            "assist_api_providers_full": full_config.get("assist_api_providers", {}),
            "core_api_providers_full": full_config.get("core_api_providers", {}),
        }
    except Exception as e:
        logger.error(f"获取API服务商配置失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "core_api_providers": [],
            "assist_api_providers": [],
        }


@router.post("/gptsovits/list_voices")
async def list_gptsovits_voices(request: Request):
    """代理请求到 GPT-SoVITS v3 API 获取可用语音配置列表"""
    import aiohttp
    from urllib.parse import urlparse
    import ipaddress
    try:
        data = await request.json()
        api_url = data.get("api_url", "").rstrip("/")

        if not api_url:
            return JSONResponse({"success": False, "error": "TTS_GPT_SOVITS_URL_REQUIRED", "code": "TTS_GPT_SOVITS_URL_REQUIRED"}, status_code=400)

        # SSRF 防护: 限制 api_url 只能是 localhost
        parsed = urlparse(api_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return JSONResponse({"success": False, "error": "TTS_GPT_SOVITS_URL_INVALID", "code": "TTS_GPT_SOVITS_URL_INVALID"}, status_code=400)
        host = parsed.hostname
        try:
            if not ipaddress.ip_address(host).is_loopback:
                return JSONResponse({"success": False, "error": "TTS_CUSTOM_URL_LOCALHOST_ONLY", "code": "TTS_CUSTOM_URL_LOCALHOST_ONLY"}, status_code=400)
        except ValueError:
            if host not in ("localhost",):
                return JSONResponse({"success": False, "error": "TTS_CUSTOM_URL_LOCALHOST_ONLY", "code": "TTS_CUSTOM_URL_LOCALHOST_ONLY"}, status_code=400)

        endpoint = f"{api_url}/api/v3/voices"
        async with aiohttp.ClientSession() as session:
            async with session.get(endpoint, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                try:
                    result = await resp.json(content_type=None)
                except Exception:
                    text = await resp.text()
                    logger.error(f"GPT-SoVITS v3 API 返回非 JSON 响应 (HTTP {resp.status}): {text[:200]}")
                    return {"success": False, "error": "Upstream TTS service error", "code": "TTS_CONNECTION_FAILED"}
                if resp.status == 200:
                    return {"success": True, "voices": result}
                logger.error(f"GPT-SoVITS v3 API 返回错误状态 HTTP {resp.status}: {str(result)[:200]}")
                return {"success": False, "error": "Upstream TTS service error", "code": "TTS_CONNECTION_FAILED"}
    except aiohttp.ClientError as e:
        logger.error(f"GPT-SoVITS v3 API 请求失败: {e}")
        return {"success": False, "error": "Internal TTS connection error", "code": "TTS_CONNECTION_FAILED"}
    except Exception as e:
        logger.error(f"获取 GPT-SoVITS 语音列表失败: {e}")
        return {"success": False, "error": "Internal TTS connection error", "code": "TTS_CONNECTION_FAILED"}


@router.post("/gptsovits/test_connectivity")
async def test_gptsovits_connectivity(request: Request):
    """测试 GPT-SoVITS 完整链路：WebSocket 连接 → init → ready → 发送短文本 → 收到响应。

    不播放音频，只验证服务可达且语音合成引擎正常工作。
    """
    import websockets as _ws
    import json as _json
    from urllib.parse import urlparse
    import ipaddress

    try:
        data = await request.json()
        api_url = (data.get("api_url", "") or "http://127.0.0.1:9881").rstrip("/")
        voice_id = (data.get("voice_id", "") or "init").strip()
        # i18n test text
        test_text = data.get("test_text", "") or "连通性测试"

        # SSRF protection: localhost only
        parsed = urlparse(api_url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return {"success": False, "error": "URL 格式无效", "error_code": "missing_params"}
        host = parsed.hostname
        try:
            if not ipaddress.ip_address(host).is_loopback:
                return {"success": False, "error": "GPT-SoVITS 仅支持本地服务", "error_code": "connection_refused"}
        except ValueError:
            if host not in ("localhost",):
                return {"success": False, "error": "GPT-SoVITS 仅支持本地服务", "error_code": "connection_refused"}

        # Convert HTTP URL to WebSocket URL
        if api_url.startswith("http://"):
            ws_base = "ws://" + api_url[7:]
        elif api_url.startswith("https://"):
            ws_base = "wss://" + api_url[8:]
        else:
            ws_base = "ws://" + api_url
        ws_url = f"{ws_base}/api/v3/tts/stream-input"

        # Strip gsv: prefix and parse advanced params (same as gptsovits_tts_worker)
        if voice_id.startswith("gsv:"):
            voice_id = voice_id[4:].strip() or "init"
        extra_params = {}
        if '|' in voice_id:
            parts = voice_id.split('|', 1)
            voice_id = parts[0].strip() or "init"
            try:
                extra_params = _json.loads(parts[1])
                if not isinstance(extra_params, dict):
                    extra_params = {}
            except (_json.JSONDecodeError, IndexError, TypeError, ValueError):
                extra_params = {}

        async with asyncio.timeout(10):
            async with _ws.connect(ws_url, ping_interval=None, max_size=10 * 1024 * 1024) as ws:
                # Step 1: Send init (merge advanced params, filter reserved fields)
                safe_params = {k: v for k, v in extra_params.items() if k not in ("cmd", "voice_id")}
                init_msg = {"cmd": "init", "voice_id": voice_id, **safe_params}
                await ws.send(_json.dumps(init_msg))

                # Step 2: Wait for ready
                ready_msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                ready_data = _json.loads(ready_msg)
                if ready_data.get("type") != "ready":
                    error_detail = str(ready_data.get("message", ready_data))[:200]
                    return {"success": False, "error": f"init 失败: {error_detail}", "error_code": "unknown"}

                # Step 3: Send test text (use "append" command, same as gptsovits_tts_worker)
                await ws.send(_json.dumps({"cmd": "append", "data": test_text}))
                # Small delay to let GSV process the text before sending end
                await asyncio.sleep(0.1)
                await ws.send(_json.dumps({"cmd": "end"}))

                # Step 4: Wait for first response
                first_response = await asyncio.wait_for(ws.recv(), timeout=10.0)

                # Collect responses for verification
                audio_chunks = []
                got_sentence = False
                gsv_error = ""

                if isinstance(first_response, bytes):
                    audio_chunks.append(first_response)
                    logger.info(f"[GSV Test] First response: binary {len(first_response)} bytes")
                else:
                    logger.info(f"[GSV Test] First response: {first_response[:200]}")
                    try:
                        first_data = _json.loads(first_response)
                        if first_data.get("type") == "sentence":
                            got_sentence = True
                    except _json.JSONDecodeError:
                        pass

                # Continue receiving until done or timeout
                try:
                    while True:
                        msg = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        if isinstance(msg, bytes):
                            audio_chunks.append(msg)
                            logger.debug(f"[GSV Test] Audio chunk: {len(msg)} bytes")
                        else:
                            logger.info(f"[GSV Test] JSON msg: {msg[:200]}")
                            msg_data = _json.loads(msg)
                            if msg_data.get("type") == "sentence":
                                got_sentence = True
                            if msg_data.get("type") == "error":
                                gsv_error = str(msg_data.get("message", ""))[:200]
                                logger.warning(f"[GSV Test] GSV error: {gsv_error}")
                                break
                            if msg_data.get("type") == "done":
                                break
                except asyncio.TimeoutError:
                    logger.info(f"[GSV Test] Receive timeout, collected {len(audio_chunks)} audio chunks")
                except Exception as e:
                    logger.info(f"[GSV Test] Receive ended: {e}")

                # Success if we got a "sentence" event (text was accepted) or audio data
                result = {"success": got_sentence or len(audio_chunks) > 0}
                if not result["success"]:
                    result["error"] = gsv_error if gsv_error else "GSV 服务未返回有效响应"
                    result["error_code"] = "unknown"

                if result["success"]:
                    logger.info(f"[ConnectivityTest] GPT-SoVITS → ✅ 收到 {len(audio_chunks)} 个音频块")
                else:
                    logger.info("[ConnectivityTest] GPT-SoVITS → ❌ 未收到有效响应")

                # --- 以下为编写连通测试时使用的音频播放验证代码，已确认可行（2026-04-22） ---
                # --- 保留供后续调试使用，正常运行时不启用 ---
                # import base64
                # raw_pcm = b""
                # sample_rate = 0
                # for chunk in audio_chunks:
                #     if len(chunk) >= 44:
                #         sr = int.from_bytes(chunk[24:28], 'little')
                #         if sample_rate == 0:
                #             sample_rate = sr
                #         pcm = chunk[44:]
                #         if len(pcm) >= 2:
                #             if len(pcm) % 2 != 0:
                #                 pcm = pcm[:-1]
                #             raw_pcm += pcm
                # if raw_pcm:
                #     result["audio_data"] = base64.b64encode(raw_pcm).decode('ascii')
                #     result["sample_rate"] = sample_rate
                #     result["audio_length_ms"] = int(len(raw_pcm) / 2 / sample_rate * 1000) if sample_rate else 0

                return result

    except (TimeoutError, asyncio.TimeoutError):
        return {"success": False, "error": "请求超时（10秒）", "error_code": "timeout"}
    except OSError as e:
        err_str = str(e).lower()
        if "connection refused" in err_str or "connect call failed" in err_str:
            return {"success": False, "error": "无法连接到 GPT-SoVITS 服务", "error_code": "connection_refused"}
        return {"success": False, "error": f"连接失败: {e}", "error_code": "connection_refused"}
    except Exception as e:
        logger.error(f"GPT-SoVITS 连通测试失败: {e}")
        return {"success": False, "error": str(e)[:200], "error_code": "unknown"}


def _sanitize_proxies(proxies: dict[str, str]) -> dict[str, str]:
    """Remove credentials from proxy URLs before returning to the client."""
    sanitized: dict[str, str] = {}
    for scheme, url in proxies.items():
        try:
            parsed = urllib.parse.urlparse(url)
            if parsed.username or parsed.password:
                # Rebuild without credentials
                netloc = parsed.hostname or ""
                if parsed.port:
                    netloc += f":{parsed.port}"
                sanitized[scheme] = urllib.parse.urlunparse(
                    (parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment)
                )
            else:
                sanitized[scheme] = url
        except Exception:
            sanitized[scheme] = "<redacted>"
    return sanitized


@router.post("/set_proxy_mode")
async def set_proxy_mode(request: Request):
    """运行时热切换代理模式。

    body: { "direct": true }   → 直连（禁用代理）
    body: { "direct": false }  → 恢复系统代理
    """
    try:
        data = await request.json()
        raw_direct = data.get("direct", False)
        if isinstance(raw_direct, bool):
            direct = raw_direct
        elif isinstance(raw_direct, str):
            direct = raw_direct.lower() in ("true", "1", "yes")
        else:
            direct = bool(raw_direct)

        # 代理相关环境变量 key 列表
        proxy_keys = [
            'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY',
            'http_proxy', 'https_proxy', 'all_proxy',
        ]

        global _proxy_snapshot
        all_keys = proxy_keys + ['NO_PROXY', 'no_proxy']
        with _PROXY_LOCK:
            if direct:
                # 仅在首次切换到直连时保存快照，避免重复调用覆盖原始值
                if not _proxy_snapshot:
                    _proxy_snapshot = {k: os.environ[k] for k in all_keys if k in os.environ}
                # 设置 NO_PROXY=* 使 httpx/aiohttp/urllib 跳过 Windows 注册表系统代理
                os.environ['NO_PROXY'] = '*'
                os.environ['no_proxy'] = '*'
                for key in proxy_keys:
                    os.environ.pop(key, None)
                logger.info("[ProxyMode] 已切换到直连模式 (NO_PROXY=*)")
            else:
                if _proxy_snapshot:
                    # 从快照恢复所有代理相关环境变量（含 NO_PROXY）
                    for k in all_keys:
                        if k in _proxy_snapshot:
                            os.environ[k] = _proxy_snapshot[k]
                        else:
                            os.environ.pop(k, None)
                    _proxy_snapshot = {}
                    logger.info("[ProxyMode] 已恢复系统代理模式")
                else:
                    logger.info("[ProxyMode] 无快照可恢复，保持当前环境变量")

        import urllib.request
        proxies_after = _sanitize_proxies(urllib.request.getproxies())
        return {"success": True, "direct": direct, "proxies_after": proxies_after}
    except Exception:
        logger.exception("[ProxyMode] 切换失败")
        return JSONResponse({"success": False, "error": "切换失败，服务器内部错误"}, status_code=500)


# ---------------------------------------------------------------------------
# Connectivity Test Models & Endpoint
# ---------------------------------------------------------------------------

class ConnectivityTestRequest(BaseModel):
    """Request model for connectivity testing.

    Two modes:
    1. Built-in provider: provide provider_key + provider_scope + api_key.
       Backend resolves url/model/provider_type from api_providers.json.
    2. Custom API: provide url + api_key + model (+ provider_type).
       Frontend passes all details directly.
    """
    # Built-in provider mode
    provider_key: Optional[str] = None       # e.g. "qwen", "openai", "glm"
    provider_scope: Optional[str] = None     # "core" or "assist"
    # Custom / fallback mode
    url: Optional[str] = ""
    api_key: Optional[str] = ""
    model: Optional[str] = ""
    provider_type: Optional[str] = "openai_compatible"
    is_free: Optional[bool] = False


class ConnectivityTestResponse(BaseModel):
    success: bool
    error: Optional[str] = None
    error_code: Optional[str] = None


async def _test_openai_compatible(url: str, api_key: str, model: str = "gpt-3.5-turbo", is_free: bool = False) -> dict:
    """Test an OpenAI-compatible REST API endpoint.

    Uses the project's ChatOpenAI client (same as actual conversations) to send
    a minimal chat completion request (max_tokens=1). This ensures the test
    exercises the exact same auth and request path as real usage.

    Args:
        url: Base URL for the API endpoint.
        api_key: API key for authentication (optional for keyless services).
        model: Model name to use for the test request. For built-in providers,
               this comes from api_providers.json (e.g. "qwen3.6-plus", "glm-4.5-air").
               For custom APIs, this comes from the frontend.
        is_free: If True, 400 Bad Request is treated as success.

    Future: TTS/GPT-SoVITS connectivity testing is not yet supported here;
    those use different protocols and will need dedicated test paths.
    """
    from utils.llm_client import ChatOpenAI as _ChatOpenAI

    try:
        client = _ChatOpenAI(
            model=model,
            base_url=url,
            api_key=api_key or "sk-placeholder",
            max_tokens=1,
            timeout=10.0,
            max_retries=0,
        )
        try:
            await client.ainvoke([{"role": "user", "content": "hi"}])
            return {"success": True}
        finally:
            await client.aclose()

    except Exception as e:
        return _classify_openai_error(e, is_free=is_free)


def _classify_openai_error(e: Exception, is_free: bool = False) -> dict:
    """Classify an OpenAI client exception into a connectivity test result."""
    import httpx
    from openai import AuthenticationError, APITimeoutError, APIConnectionError, APIStatusError, RateLimitError

    # Auth errors (401, 403)
    if isinstance(e, AuthenticationError):
        return {"success": False, "error": "API Key无效或已过期", "error_code": "auth_failed"}

    # Rate limit (429) — key is valid but temporarily throttled, treat as success
    if isinstance(e, RateLimitError):
        return {"success": True}

    # Timeout
    if isinstance(e, (APITimeoutError, TimeoutError, asyncio.TimeoutError)):
        return {"success": False, "error": "请求超时（10秒）", "error_code": "timeout"}

    # Connection errors (DNS, refused, etc.)
    if isinstance(e, APIConnectionError):
        err_str = str(e).lower()
        if "getaddrinfo" in err_str or "name or service not known" in err_str or "nodename nor servname" in err_str:
            return {"success": False, "error": "域名解析失败", "error_code": "dns_error"}
        if "connection refused" in err_str or "connect call failed" in err_str:
            return {"success": False, "error": "无法连接到目标服务器", "error_code": "connection_refused"}
        return {"success": False, "error": f"连接失败: {e}", "error_code": "connection_refused"}

    # SSL errors
    if isinstance(e, ssl.SSLError):
        return {"success": False, "error": "SSL证书验证失败", "error_code": "ssl_error"}

    # HTTP status errors (400, 500, etc.)
    if isinstance(e, APIStatusError):
        status = e.status_code
        if status in (401, 403):
            return {"success": False, "error": "API Key无效或已过期", "error_code": "auth_failed"}
        # 免费版 API：400 = 服务可达，Key 未被拒绝
        if is_free and status == 400:
            return {"success": True}
        return {"success": False, "error": f"HTTP {status}", "error_code": "unknown"}

    # Fallback
    return {"success": False, "error": str(e), "error_code": "unknown"}


async def _test_websocket(url: str, api_key: str, model: str = "") -> dict:
    """Test a WebSocket endpoint by performing a handshake AND a minimal session.update.

    Mirrors the project's OmniRealtimeClient.connect() behavior:
    - Appends ?model={model} to the URL (same as omni_realtime_client.py)
    - Sends Authorization header with Bearer token
    - After handshake, sends a minimal session.update and waits for server response
    - If server responds with any non-error event → key is valid and model is accessible
    - If server responds with error or closes connection → key/model issue

    This goes beyond a simple handshake to verify key permissions at the model level,
    ensuring "green = 100% usable, red = 100% not usable".
    """
    import websockets
    import json as _json

    try:
        # Build WebSocket URL with model parameter (same as OmniRealtimeClient.connect)
        ws_url = url.rstrip("/")
        if model and model.lower() != "free-model":
            separator = "&" if "?" in ws_url else "?"
            ws_url = f"{ws_url}{separator}model={urllib.parse.quote(model, safe='')}"

        # Authorization header only (same as OmniRealtimeClient.connect — no api_key in URL)
        if api_key:
            extra_headers = {"Authorization": f"Bearer {api_key}"}
        else:
            extra_headers = {}

        async with asyncio.timeout(10):
            async with websockets.connect(
                ws_url,
                additional_headers=extra_headers,
                open_timeout=10,
                close_timeout=5,
            ) as ws:
                # For free-model: handshake-only test is sufficient
                # (key is pre-configured "free-access", no need to verify permissions)
                if model and model.lower() == "free-model":
                    return {"success": True}

                # For paid models: send a minimal session.update to verify
                # key permissions at the model level (same as OmniRealtimeClient)
                session_update = {
                    "type": "session.update",
                    "session": {
                        "modalities": ["text"],
                        "instructions": "connectivity test",
                    }
                }
                await ws.send(_json.dumps(session_update))

                # Wait for first server response (with 5s inner timeout)
                try:
                    response = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    event = _json.loads(response)
                    event_type = event.get("type", "")

                    if event_type == "error":
                        # Realtime protocol: event["error"] can be dict or string
                        raw_error = event.get("error", "")
                        if isinstance(raw_error, dict):
                            error_code = str(raw_error.get("code", "")).lower()
                            error_message = str(raw_error.get("message", ""))
                            error_msg = error_message or str(raw_error)
                        else:
                            error_code = ""
                            error_msg = str(raw_error)
                            error_message = error_msg
                        error_lower = (error_code + " " + error_message).lower()
                        if any(kw in error_lower for kw in ("401", "403", "auth", "unauthorized", "invalid api key", "invalid key", "api key", "invalid_api_key", "authentication_error")):
                            return {"success": False, "error": "API Key无效或已过期", "error_code": "auth_failed"}
                        return {"success": False, "error": f"服务端错误: {error_msg[:200]}", "error_code": "unknown"}

                    # Any non-error response (session.created, session.updated, etc.) = success
                    return {"success": True}

                except asyncio.TimeoutError:
                    # Handshake succeeded but no response to session.update within 5s
                    # This is still a partial success — service is reachable
                    return {"success": True}

    except (TimeoutError, asyncio.TimeoutError):
        return {"success": False, "error": "请求超时（10秒）", "error_code": "timeout"}
    except ssl.SSLError:
        return {"success": False, "error": "SSL证书验证失败", "error_code": "ssl_error"}
    except OSError as e:
        err_str = str(e).lower()
        if "getaddrinfo" in err_str or "name or service not known" in err_str or "nodename nor servname" in err_str:
            return {"success": False, "error": "域名解析失败", "error_code": "dns_error"}
        if "connection refused" in err_str or "connect call failed" in err_str:
            return {"success": False, "error": "无法连接到目标服务器", "error_code": "connection_refused"}
        return {"success": False, "error": f"WebSocket连接失败: {e}", "error_code": "ws_error"}
    except Exception as e:
        err_str = str(e).lower()
        # websockets library raises InvalidStatus for HTTP 401/403 during handshake
        # websockets 15.0.1: status code is at e.response.status_code, not e.status_code
        status_code = getattr(e, "status_code", None)
        if status_code is None:
            _resp = getattr(e, "response", None)
            status_code = getattr(_resp, "status_code", None)
        if status_code in (401, 403):
            return {"success": False, "error": "API Key无效或已过期", "error_code": "auth_failed"}
        return {"success": False, "error": f"WebSocket连接失败: {e}", "error_code": "ws_error"}


@router.post("/test_connectivity")
async def test_connectivity(req: ConnectivityTestRequest) -> dict:
    """测试 API 连通性。

    两种模式：
    1. 内置供应商：提供 provider_key + provider_scope + api_key，
       后端从 api_providers.json 读取 url/model/provider_type。
    2. 自定义 API：提供 url + api_key + model (+ provider_type)，
       前端传完整参数，后端直接使用。

    根据 provider_type 选择测试策略：
    - openai_compatible（默认）：通过 ChatOpenAI 发送最小 chat completion 请求（max_tokens=1）
    - websocket：WebSocket 握手，成功后立即关闭

    所有请求 10 秒超时。端点为 async，天然支持并发请求不阻塞。
    """
    api_key_stripped = (req.api_key or "").strip()

    # --- Mode 1: Built-in provider (resolve config from api_providers.json) ---
    if req.provider_key and req.provider_scope:
        from utils.api_config_loader import get_config as _get_api_config

        api_config = _get_api_config()
        provider_key = req.provider_key.strip()
        scope = req.provider_scope.strip().lower()

        if scope == "core":
            providers = api_config.get("core_api_providers", {})
            profile = providers.get(provider_key, {})
            url_stripped = profile.get("core_url", "")
            model = profile.get("core_model", "")
            provider_type = "websocket"
            is_free = profile.get("is_free_version", False)
            _source_label = profile.get("name", provider_key)
        elif scope == "assist":
            providers = api_config.get("assist_api_providers", {})
            profile = providers.get(provider_key, {})
            url_stripped = profile.get("openrouter_url", "")
            # Use conversation_model as the test model (most representative)
            model = profile.get("conversation_model", "")
            provider_type = "openai_compatible"
            is_free = profile.get("is_free_version", False)
            _source_label = profile.get("name", provider_key)
        else:
            return {"success": False, "error": "无效的 provider_scope", "error_code": "missing_params"}

        if not url_stripped:
            # Provider has no core_url (e.g. Gemini uses SDK, not raw WebSocket).
            # Fall back to the assist profile's OpenAI-compatible endpoint to verify the key.
            assist_providers = api_config.get("assist_api_providers", {})
            assist_profile = assist_providers.get(provider_key, {})
            fallback_url = assist_profile.get("openrouter_url", "")
            fallback_model = assist_profile.get("conversation_model", "")
            if fallback_url and fallback_model:
                url_stripped = fallback_url
                model = fallback_model
                provider_type = "openai_compatible"
                _source_label = assist_profile.get("name", profile.get("name", provider_key)) + "（通过辅助端点验证）"
            else:
                return {"success": False, "error": f"供应商 {_source_label} 暂不支持连通测试", "error_code": "missing_params"}

    # --- Mode 2: Custom API (use frontend-provided params directly) ---
    else:
        if not req.url or not req.url.strip():
            return {"success": False, "error": "缺少必要参数", "error_code": "missing_params"}

        url_stripped = req.url.strip()
        model = (req.model or "gpt-3.5-turbo").strip()
        provider_type = (req.provider_type or "openai_compatible").strip().lower()
        is_free = bool(req.is_free)
        _source_label = _identify_provider_label(url_stripped, is_free)

    try:
        if provider_type == "websocket":
            result = await _test_websocket(url_stripped, api_key_stripped, model=model)
        else:
            result = await _test_openai_compatible(url_stripped, api_key_stripped, model=model, is_free=is_free)
    except Exception as e:
        logger.exception("[ConnectivityTest] 未预期的异常")
        result = {"success": False, "error": str(e), "error_code": "unknown"}

    # 单条结果日志：供应商/自定义 + 成功/失败
    if result.get("success"):
        logger.info("[ConnectivityTest] %s → ✅ 连通", _source_label)
    else:
        logger.info("[ConnectivityTest] %s → ❌ %s", _source_label, result.get("error_code", "unknown"))

    return result


def _identify_provider_label(url: str, is_free: bool) -> str:
    """根据 URL 识别是哪个供应商，返回人类可读的标签。
    已知供应商显示名称，自定义的显示完整 URL。
    """
    _KNOWN_PROVIDERS = {
        "lanlan.tech": "免费版",
        "dashscope.aliyuncs.com": "阿里百炼",
        "dashscope-intl.aliyuncs.com": "阿里国际版",
        "api.openai.com": "OpenAI",
        "open.bigmodel.cn": "智谱",
        "api.stepfun.com": "阶跃星辰",
        "api.siliconflow.cn": "硅基流动",
        "generativelanguage.googleapis.com": "Gemini",
        "api.moonshot.cn": "Kimi",
    }
    url_lower = url.lower()
    for domain, name in _KNOWN_PROVIDERS.items():
        if domain in url_lower:
            if is_free:
                return f"{name}(免费)"
            return name
    # 自定义 URL：脱敏后显示（移除敏感 query 参数）
    return f"自定义({_redact_url_for_log(url)})"


def _redact_url_for_log(url: str) -> str:
    """Redact sensitive query parameters and userinfo before logging a custom endpoint URL."""
    try:
        parsed = urllib.parse.urlsplit(url)

        # Redact userinfo (https://user:pass@host/ → https://***:***@host/)
        netloc = parsed.netloc
        if '@' in netloc:
            host_part = netloc.split('@', 1)[1]
            netloc = f"***:***@{host_part}"

        # Redact sensitive query parameters
        sensitive_keys = {
            "api_key", "apikey", "key", "token", "access_token", "authorization",
            "signature", "sig", "client_secret", "password", "jwt", "bearer",
        }
        if parsed.query:
            query_pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            redacted_pairs = [
                (k, "***" if k.lower() in sensitive_keys else v)
                for k, v in query_pairs
            ]
            redacted_query = urllib.parse.urlencode(redacted_pairs)
        else:
            redacted_query = ""

        return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, redacted_query, parsed.fragment))
    except Exception:
        return url
