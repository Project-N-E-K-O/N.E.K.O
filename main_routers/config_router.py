# -*- coding: utf-8 -*-
"""
Config Router

Handles configuration-related API endpoints including:
- User preferences
- API configuration (core and custom APIs)
- Steam language settings
- API providers
"""

import json
import logging

from fastapi import APIRouter, Request

from .shared_state import get_config_manager, get_steamworks, get_session_manager, get_initialize_character_data
from .characters_router import get_current_live2d_model
from utils.preferences import load_user_preferences, update_model_preferences, validate_model_preferences, move_model_to_top

router = APIRouter(prefix="/api/config", tags=["config"])
logger = logging.getLogger("Main")


@router.get("/page_config")
async def get_page_config(lanlan_name: str = ""):
    """获取页面配置（lanlan_name 和 model_path）"""
    try:
        # 获取角色数据
        _config_manager = get_config_manager()
        _, her_name, _, lanlan_basic_config, _, _, _, _, _, _ = _config_manager.get_character_data()
        
        # 如果提供了 lanlan_name 参数，使用它；否则使用当前角色
        target_name = lanlan_name if lanlan_name else her_name
        
        # 获取 live2d 和 live2d_item_id 字段
        live2d = lanlan_basic_config.get(target_name, {}).get('live2d', 'mao_pro')
        live2d_item_id = lanlan_basic_config.get(target_name, {}).get('live2d_item_id', '')
        
        logger.debug(f"获取页面配置 - 角色: {target_name}, 模型: {live2d}, item_id: {live2d_item_id}")
        
        # 使用 get_current_live2d_model 函数获取正确的模型信息
        # 第一个参数是角色名称，第二个参数是item_id
        model_response = await get_current_live2d_model(target_name, live2d_item_id)
        # 提取JSONResponse中的内容
        model_data = model_response.body.decode('utf-8')
        import json
        model_json = json.loads(model_data)
        model_info = model_json.get('model_info', {})
        model_path = model_info.get('path', '')
        
        return {
            "success": True,
            "lanlan_name": target_name,
            "model_path": model_path
        }
    except Exception as e:
        logger.error(f"获取页面配置失败: {str(e)}")
        return {
            "success": False,
            "error": str(e),
            "lanlan_name": "",
            "model_path": ""
        }


@router.get("/preferences")
async def get_preferences():
    """获取用户偏好设置"""
    preferences = load_user_preferences()
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
        
        # 获取参数（可选）
        parameters = data.get('parameters')
        
        # 更新偏好
        if update_model_preferences(data['model_path'], data['position'], data['scale'], parameters):
            return {"success": True, "message": "偏好设置已保存"}
        else:
            return {"success": False, "error": "保存失败"}
            
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


@router.get("/steam_language")
async def get_steam_language():
    """获取 Steam 客户端的语言设置，用于前端 i18n 初始化"""
    steamworks = get_steamworks()
    
    # Steam 语言代码到 i18n 语言代码的映射
    # 参考: https://partner.steamgames.com/doc/store/localization/languages
    STEAM_TO_I18N_MAP = {
        'schinese': 'zh-CN',      # 简体中文
        'tchinese': 'zh-CN',      # 繁体中文（映射到简体中文，因为目前只支持 zh-CN）
        'english': 'en',          # 英文
        # 其他语言默认映射到英文
    }
    
    try:
        if steamworks is None:
            return {
                "success": False,
                "error": "Steamworks 未初始化",
                "steam_language": None,
                "i18n_language": None
            }
        
        # 获取 Steam 当前游戏语言
        steam_language = steamworks.Apps.GetCurrentGameLanguage()
        # Steam API 可能返回 bytes，需要解码为字符串
        if isinstance(steam_language, bytes):
            steam_language = steam_language.decode('utf-8')
        
        # 映射到 i18n 语言代码
        i18n_language = STEAM_TO_I18N_MAP.get(steam_language, 'en')  # 默认英文
        logger.info(f"[i18n] Steam 语言映射: '{steam_language}' -> '{i18n_language}'")
        
        return {
            "success": True,
            "steam_language": steam_language,
            "i18n_language": i18n_language
        }
        
    except Exception as e:
        logger.error(f"获取 Steam 语言设置失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "steam_language": None,
            "i18n_language": None
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
            with open(core_config_path, 'r', encoding='utf-8') as f:
                core_cfg = json.load(f)
                api_key = core_cfg.get('coreApiKey', '')
        except FileNotFoundError:
            # 如果文件不存在，返回当前配置中的CORE_API_KEY
            _config_manager = get_config_manager()
            core_config = _config_manager.get_core_config()
            api_key = core_config.get('CORE_API_KEY','')
            # 创建空的配置对象用于返回默认值
            core_cfg = {}
        
        return {
            "api_key": api_key,
            "coreApi": core_cfg.get('coreApi', 'qwen'),
            "assistApi": core_cfg.get('assistApi', 'qwen'),
            "assistApiKeyQwen": core_cfg.get('assistApiKeyQwen', ''),
            "assistApiKeyOpenai": core_cfg.get('assistApiKeyOpenai', ''),
            "assistApiKeyGlm": core_cfg.get('assistApiKeyGlm', ''),
            "assistApiKeyStep": core_cfg.get('assistApiKeyStep', ''),
            "assistApiKeySilicon": core_cfg.get('assistApiKeySilicon', ''),
            "mcpToken": core_cfg.get('mcpToken', ''),  # 添加mcpToken字段
            "enableCustomApi": core_cfg.get('enableCustomApi', False),  # 添加enableCustomApi字段
            # 自定义API相关字段
            "summaryModelProvider": core_cfg.get('summaryModelProvider', ''),
            "summaryModelUrl": core_cfg.get('summaryModelUrl', ''),
            "summaryModelId": core_cfg.get('summaryModelId', ''),
            "summaryModelApiKey": core_cfg.get('summaryModelApiKey', ''),
            "correctionModelProvider": core_cfg.get('correctionModelProvider', ''),
            "correctionModelUrl": core_cfg.get('correctionModelUrl', ''),
            "correctionModelId": core_cfg.get('correctionModelId', ''),
            "correctionModelApiKey": core_cfg.get('correctionModelApiKey', ''),
            "emotionModelProvider": core_cfg.get('emotionModelProvider', ''),
            "emotionModelUrl": core_cfg.get('emotionModelUrl', ''),
            "emotionModelId": core_cfg.get('emotionModelId', ''),
            "emotionModelApiKey": core_cfg.get('emotionModelApiKey', ''),
            "visionModelProvider": core_cfg.get('visionModelProvider', ''),
            "visionModelUrl": core_cfg.get('visionModelUrl', ''),
            "visionModelId": core_cfg.get('visionModelId', ''),
            "visionModelApiKey": core_cfg.get('visionModelApiKey', ''),
            "omniModelProvider": core_cfg.get('omniModelProvider', ''),
            "omniModelUrl": core_cfg.get('omniModelUrl', ''),
            "omniModelId": core_cfg.get('omniModelId', ''),
            "omniModelApiKey": core_cfg.get('omniModelApiKey', ''),
            "ttsModelProvider": core_cfg.get('ttsModelProvider', ''),
            "ttsModelUrl": core_cfg.get('ttsModelUrl', ''),
            "ttsModelId": core_cfg.get('ttsModelId', ''),
            "ttsModelApiKey": core_cfg.get('ttsModelApiKey', ''),
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
        from pathlib import Path
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        core_config_path = str(config_manager.get_config_path('core_config.json'))
        # 确保配置目录存在
        Path(core_config_path).parent.mkdir(parents=True, exist_ok=True)
        
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
        if 'assistApiKeyQwen' in data:
            core_cfg['assistApiKeyQwen'] = data['assistApiKeyQwen']
        if 'assistApiKeyOpenai' in data:
            core_cfg['assistApiKeyOpenai'] = data['assistApiKeyOpenai']
        if 'assistApiKeyGlm' in data:
            core_cfg['assistApiKeyGlm'] = data['assistApiKeyGlm']
        if 'assistApiKeyStep' in data:
            core_cfg['assistApiKeyStep'] = data['assistApiKeyStep']
        if 'assistApiKeySilicon' in data:
            core_cfg['assistApiKeySilicon'] = data['assistApiKeySilicon']
        if 'mcpToken' in data:
            core_cfg['mcpToken'] = data['mcpToken']
        if 'enableCustomApi' in data:
            core_cfg['enableCustomApi'] = data['enableCustomApi']
        
        # 添加用户自定义API配置
        if 'summaryModelProvider' in data:
            core_cfg['summaryModelProvider'] = data['summaryModelProvider']
        if 'summaryModelUrl' in data:
            core_cfg['summaryModelUrl'] = data['summaryModelUrl']
        if 'summaryModelId' in data:
            core_cfg['summaryModelId'] = data['summaryModelId']
        if 'summaryModelApiKey' in data:
            core_cfg['summaryModelApiKey'] = data['summaryModelApiKey']
        if 'correctionModelProvider' in data:
            core_cfg['correctionModelProvider'] = data['correctionModelProvider']
        if 'correctionModelUrl' in data:
            core_cfg['correctionModelUrl'] = data['correctionModelUrl']
        if 'correctionModelId' in data:
            core_cfg['correctionModelId'] = data['correctionModelId']
        if 'correctionModelApiKey' in data:
            core_cfg['correctionModelApiKey'] = data['correctionModelApiKey']
        if 'emotionModelProvider' in data:
            core_cfg['emotionModelProvider'] = data['emotionModelProvider']
        if 'emotionModelUrl' in data:
            core_cfg['emotionModelUrl'] = data['emotionModelUrl']
        if 'emotionModelId' in data:
            core_cfg['emotionModelId'] = data['emotionModelId']
        if 'emotionModelApiKey' in data:
            core_cfg['emotionModelApiKey'] = data['emotionModelApiKey']
        if 'visionModelProvider' in data:
            core_cfg['visionModelProvider'] = data['visionModelProvider']
        if 'visionModelUrl' in data:
            core_cfg['visionModelUrl'] = data['visionModelUrl']
        if 'visionModelId' in data:
            core_cfg['visionModelId'] = data['visionModelId']
        if 'visionModelApiKey' in data:
            core_cfg['visionModelApiKey'] = data['visionModelApiKey']
        if 'omniModelProvider' in data:
            core_cfg['omniModelProvider'] = data['omniModelProvider']
        if 'omniModelUrl' in data:
            core_cfg['omniModelUrl'] = data['omniModelUrl']
        if 'omniModelId' in data:
            core_cfg['omniModelId'] = data['omniModelId']
        if 'omniModelApiKey' in data:
            core_cfg['omniModelApiKey'] = data['omniModelApiKey']
        if 'ttsModelProvider' in data:
            core_cfg['ttsModelProvider'] = data['ttsModelProvider']
        if 'ttsModelUrl' in data:
            core_cfg['ttsModelUrl'] = data['ttsModelUrl']
        if 'ttsModelId' in data:
            core_cfg['ttsModelId'] = data['ttsModelId']
        if 'ttsModelApiKey' in data:
            core_cfg['ttsModelApiKey'] = data['ttsModelApiKey']
        
        with open(core_config_path, 'w', encoding='utf-8') as f:
            json.dump(core_cfg, f, indent=2, ensure_ascii=False)
        
        # API配置更新后，需要先通知所有客户端，再关闭session，最后重新加载配置
        logger.info("API配置已更新，准备通知客户端并重置所有session...")
        
        # 1. 先通知所有连接的客户端即将刷新（WebSocket还连着）
        notification_count = 0
        session_manager = get_session_manager()
        for lanlan_name, mgr in session_manager.items():
            if mgr.is_active and mgr.websocket:
                try:
                    await mgr.websocket.send_text(json.dumps({
                        "type": "reload_page",
                        "message": "API配置已更新，页面即将刷新"
                    }))
                    notification_count += 1
                    logger.info(f"已通知 {lanlan_name} 的前端刷新页面")
                except Exception as e:
                    logger.warning(f"通知 {lanlan_name} 的WebSocket失败: {e}")
        
        logger.info(f"已通知 {notification_count} 个客户端")
        
        # 2. 立刻关闭所有活跃的session（这会断开所有WebSocket）
        sessions_ended = []
        for lanlan_name, mgr in session_manager.items():
            if mgr.is_active:
                try:
                    await mgr.end_session(by_server=True)
                    sessions_ended.append(lanlan_name)
                    logger.info(f"{lanlan_name} 的session已结束")
                except Exception as e:
                    logger.error(f"结束 {lanlan_name} 的session时出错: {e}")
        
        # 3. 重新加载配置并重建session manager
        logger.info("正在重新加载配置...")
        try:
            initialize_character_data = get_initialize_character_data()
            await initialize_character_data()
            logger.info("配置重新加载完成，新的API配置已生效")
        except Exception as reload_error:
            logger.error(f"重新加载配置失败: {reload_error}")
            return {"success": False, "error": f"配置已保存但重新加载失败: {str(reload_error)}"}
        
        logger.info(f"已通知 {notification_count} 个连接的客户端API配置已更新")
        return {"success": True, "message": "API Key已保存并重新加载配置", "sessions_ended": len(sessions_ended)}
    except Exception as e:
        return {"success": False, "error": str(e)}



@router.get("/api_providers")
async def get_api_providers_config():
    """获取API服务商配置（供前端使用）"""
    try:
        from utils.api_config_loader import (
            get_core_api_providers_for_frontend,
            get_assist_api_providers_for_frontend,
        )
        
        # 使用缓存加载配置（性能更好，配置更新后需要重启服务）
        core_providers = get_core_api_providers_for_frontend()
        assist_providers = get_assist_api_providers_for_frontend()
        
        return {
            "success": True,
            "core_api_providers": core_providers,
            "assist_api_providers": assist_providers,
        }
    except Exception as e:
        logger.error(f"获取API服务商配置失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "core_api_providers": [],
            "assist_api_providers": [],
        }



