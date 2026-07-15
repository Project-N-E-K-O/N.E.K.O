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

"""Core API config endpoints (/core_api, /api_providers).

Split out of the former monolithic ``main_routers/config_router.py``.
"""

from ._shared import logger, router
from .connectivity import _auto_resolve_provider_urls_for_save

import asyncio
import json
from fastapi import Request
from ..shared_state import get_session_manager, get_initialize_character_data
from utils.file_utils import read_json_async
from utils.cloudsave_runtime import MaintenanceModeError
from utils.config_manager import ensure_default_yui_voice_for_free_api


@router.get("/core_api")
async def get_core_config_api():
    """Get the core config (API keys)."""
    try:
        # 尝试从core_config.json读取
        try:
            from utils.config_manager import get_config_manager
            config_manager = get_config_manager()
            core_config_path = str(config_manager.get_runtime_config_path('core_config.json'))
            core_cfg = await read_json_async(core_config_path)
            api_key = core_cfg.get('coreApiKey', '')
        except FileNotFoundError:
            # 如果文件不存在，返回当前配置中的CORE_API_KEY
            _config_manager = get_config_manager()
            core_config = await _config_manager.aget_core_config()
            api_key = core_config.get('CORE_API_KEY','')
            # 创建空的配置对象用于返回默认值
            core_cfg = {}
            runtime_core_api_provider = core_config.get('CORE_API_TYPE') or ''
            runtime_assist_api_provider = core_config.get('assistApi') or ''
        else:
            runtime_core_api_provider = ''
            runtime_assist_api_provider = ''
        
        # 旧版本 core_config.json 可能只有 coreApiKey 而没有各 assistApiKey* 字段，
        # 需要与 ConfigManager.get_core_config() 保持一致的回退逻辑，
        # 但只能回退到与 coreApi / assistApi 匹配的服务商，
        # 以免将不兼容的 API Key 填充到其他服务商。
        fallback_key = api_key if api_key != 'free-access' else ''
        _core_api_provider = core_cfg.get('coreApi') or runtime_core_api_provider or 'qwen'
        _assist_api_provider = core_cfg.get('assistApi') or runtime_assist_api_provider
        if not _assist_api_provider:
            _assist_api_provider = 'free' if _core_api_provider == 'free' else 'qwen'
        _fallback_providers = {_core_api_provider, _assist_api_provider}
        _doubao_tts_shared_key = ''
        if str(core_cfg.get('ttsModelProvider') or '').strip() == 'doubao_tts':
            _doubao_tts_shared_key = core_cfg.get('ttsModelApiKey', '')

        def _fb(provider: str) -> str:
            """Fall back to coreApiKey only when the provider matches the user-selected coreApi/assistApi."""
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
            "assistApiKeyKimiCode": core_cfg.get('assistApiKeyKimiCode', '') or _fb('kimi_code'),
            "assistApiKeyDeepseek": core_cfg.get('assistApiKeyDeepseek', '') or _fb('deepseek'),
            "assistApiKeyDoubao": core_cfg.get('assistApiKeyDoubao', '') or _fb('doubao'),
            "assistApiKeyDoubaoTts": core_cfg.get('assistApiKeyDoubaoTts', '') or _doubao_tts_shared_key,
            # MiniMax / MiMo 是 assist-only TTS provider，coreApiKey 不保证兼容；
            # 不 fallback，以免把无效 key 塞进 TTS 凭证槽位导致 401。
            "assistApiKeyMinimax": core_cfg.get('assistApiKeyMinimax', ''),
            "assistApiKeyMinimaxIntl": core_cfg.get('assistApiKeyMinimaxIntl', ''),
            "assistApiKeyMimo": core_cfg.get('assistApiKeyMimo', ''),
            "useMimoTokenPlan": core_cfg.get('useMimoTokenPlan', False) is True or str(core_cfg.get('useMimoTokenPlan', False)).lower() in ('true', '1', 'yes', 'on'),
            "assistApiKeyMimoTokenPlan": core_cfg.get('assistApiKeyMimoTokenPlan', ''),
            "assistApiKeyElevenlabs": core_cfg.get('assistApiKeyElevenlabs', ''),
            "assistApiKeyGrok": core_cfg.get('assistApiKeyGrok', '') or _fb('grok'),
            "assistApiKeyClaude": core_cfg.get('assistApiKeyClaude', '') or _fb('claude'),
            "assistApiKeyOpenrouter": core_cfg.get('assistApiKeyOpenrouter', '') or _fb('openrouter'),
            "mcpToken": core_cfg.get('mcpToken', ''),
            "openclawUrl": core_cfg.get('openclawUrl'),
            "openclawTimeout": core_cfg.get('openclawTimeout'),
            "openclawDefaultSenderId": core_cfg.get('openclawDefaultSenderId'),
            "enableCustomApi": core_cfg.get('enableCustomApi', False),
            "resolvedProviderUrls": core_cfg.get('resolvedProviderUrls', {}) if isinstance(core_cfg.get('resolvedProviderUrls'), dict) else {},
            # 自定义API相关字段（Provider / Url / Id / ApiKey per model type）
            **{
                f'{mt}Model{suffix}': core_cfg.get(f'{mt}Model{suffix}', '')
                for mt in ('conversation', 'summary', 'gameMain', 'gameSummary', 'correction', 'emotion',
                           'vision', 'agent', 'omni', 'tts')
                for suffix in ('Provider', 'Url', 'Id', 'ApiKey')
            },
            "gptsovitsEnabled": core_cfg.get('gptsovitsEnabled'),
            "ttsProvider": core_cfg.get('ttsProvider', ''),
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
    """Update the core config (API keys)."""
    try:
        data = await request.json()
        if not data:
            return {"success": False, "error": "无效的数据"}
        
        enable_custom_api = data.get('enableCustomApi', False)

        # 保存到core_config.json
        from utils.config_manager import get_config_manager
        config_manager = get_config_manager()
        
        # 构建配置对象：先加载旧配置，再按本次提交覆盖。
        # 这与前端 API 管理簿的行为保持一致，避免某个字段本次未提交时被意外清空。
        try:
            existing_core_cfg = await asyncio.to_thread(
                config_manager.load_json_config, 'core_config.json', {}
            )
        except Exception:
            existing_core_cfg = {}
        core_cfg = dict(existing_core_cfg) if isinstance(existing_core_cfg, dict) else {}

        def _incoming_provider(field, error_message):
            if field not in data:
                return None
            provider = data.get(field)
            if provider is not None and not isinstance(provider, str):
                raise TypeError(error_message)
            provider = (provider or "").strip()
            return provider or None

        def _stored_provider(field):
            provider = core_cfg.get(field)
            if not isinstance(provider, str):
                return None
            provider = provider.strip()
            return provider or None

        try:
            incoming_core_api = _incoming_provider('coreApi', 'coreApi must be a string')
            incoming_assist_api = _incoming_provider('assistApi', 'assistApi must be a string')
        except TypeError as exc:
            return {"success": False, "error": str(exc)}

        effective_core_api = incoming_core_api or _stored_provider('coreApi')
        core_uses_free_provider = effective_core_api == 'free'
        
        def _is_masked_secret(value) -> bool:
            if not isinstance(value, str):
                return False
            stripped = value.strip()
            return bool(stripped) and ('***' in stripped or set(stripped) == {'*'})

        def _normalize_core_api_key(value):
            if _is_masked_secret(value):
                return None
            if value is None:
                raise ValueError("API Key不能为null")
            if not isinstance(value, str):
                raise TypeError("API Key必须是字符串类型")
            return value.strip()

        # 只有在启用自定义API时，才允许不设置coreApiKey
        if enable_custom_api:
            # 启用自定义API时，coreApiKey是可选的
            if 'coreApiKey' in data:
                try:
                    api_key = _normalize_core_api_key(data['coreApiKey'])
                except (TypeError, ValueError) as exc:
                    return {"success": False, "error": str(exc)}
                if api_key is not None:
                    core_cfg['coreApiKey'] = api_key
        else:
            # 未启用自定义API时，必须设置coreApiKey
            if 'coreApiKey' not in data and not core_uses_free_provider:
                return {"success": False, "error": "缺少coreApiKey字段"}
            try:
                api_key = (
                    _normalize_core_api_key(data['coreApiKey'])
                    if 'coreApiKey' in data
                    else None
                )
            except (TypeError, ValueError) as exc:
                return {"success": False, "error": str(exc)}
            if not core_uses_free_provider and not api_key:
                return {"success": False, "error": "API Key不能为空"}
            if api_key is not None:
                core_cfg['coreApiKey'] = api_key
        # coreApi / assistApi 为空串 = 前端在配置尚未加载完成（下拉被清空）时提交。
        # 绝不能用空值覆盖已存的有效 provider——否则重新加载时空值会被兜底成别的服务商，
        # 把免费版用户悄悄切走。仅在非空时写入；空值保留 existing_core_cfg 里的旧值。
        if incoming_core_api:
            core_cfg['coreApi'] = incoming_core_api
        if incoming_assist_api:
            core_cfg['assistApi'] = incoming_assist_api
        if 'resolvedProviderUrls' in data:
            resolved_urls = data.get('resolvedProviderUrls')
            if not isinstance(resolved_urls, dict):
                return {"success": False, "error": "resolvedProviderUrls must be an object"}
            sanitized_resolved_urls = {}
            for key, value in resolved_urls.items():
                if not isinstance(key, str) or not isinstance(value, str):
                    continue
                normalized_key = key.strip()
                normalized_value = value.strip()
                if normalized_key and normalized_value:
                    sanitized_resolved_urls[normalized_key] = normalized_value
            core_cfg['resolvedProviderUrls'] = sanitized_resolved_urls
        _api_key_fields = [
            'assistApiKeyQwen', 'assistApiKeyQwenIntl', 'assistApiKeyOpenai', 'assistApiKeyDeepseek',
            'assistApiKeyGlm', 'assistApiKeyStep', 'assistApiKeySilicon',
            'assistApiKeyGemini', 'assistApiKeyKimi', 'assistApiKeyDoubao', 'assistApiKeyDoubaoTts',
            'assistApiKeyMinimax', 'assistApiKeyMinimaxIntl', 'assistApiKeyMimo',
            'assistApiKeyMimoTokenPlan', 'assistApiKeyElevenlabs', 'assistApiKeyGrok',
            'assistApiKeyClaude', 'assistApiKeyKimiCode', 'assistApiKeyOpenrouter',
        ]
        for field in _api_key_fields:
            if field in data:
                value = data[field]
                if isinstance(value, str) and '***' in value:
                    continue
                core_cfg[field] = value
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
        if 'useMimoTokenPlan' in data:
            if not isinstance(data['useMimoTokenPlan'], bool):
                return {"success": False, "error": "useMimoTokenPlan must be a boolean"}
            core_cfg['useMimoTokenPlan'] = data['useMimoTokenPlan']
        if 'gptsovitsEnabled' in data:
            core_cfg['gptsovitsEnabled'] = data['gptsovitsEnabled']
        for field in (
            'ttsProvider',
        ):
            if field in data:
                core_cfg[field] = data[field]
        if 'disableTts' in data:
            if not isinstance(data['disableTts'], bool):
                return {"success": False, "error": "disableTts must be a boolean"}
            core_cfg['disableTts'] = data['disableTts']

        # 自定义API配置（Provider / Url / Id / ApiKey per model type）
        _model_types = [
            'conversation', 'summary', 'gameMain', 'gameSummary', 'correction', 'emotion',
            'vision', 'agent', 'omni', 'tts',
        ]
        for mt in _model_types:
            for suffix in ['Provider', 'Url', 'Id', 'ApiKey']:
                field = f'{mt}Model{suffix}'
                if field in data:
                    core_cfg[field] = data[field]
        # gptsovitsEnabled 退役后的惰性迁移（save choke point，对偶 #1842 voice_id 思路）：
        # GSV 启用已收口到 ttsModelProvider=='gptsovits' 单一真相，旧 gptsovitsEnabled 仅作
        # pre-#1830 存量兜底。前端加载会把启用中的 GSV 下拉钉到 'gptsovits'，故任何提交了
        # 非 'gptsovits' 的 ttsModelProvider 都是用户显式切走 → 顺手把残留旧 flag 落 False，
        # 否则 get_core_config 的 follow_* 回落分支会把旧 true 兜回来（切到 follow_assist 也
        # 关不掉 GSV）。未提交 ttsModelProvider 的局部更新不碰旧 flag，保住从不重存的存量。
        _incoming_tts_provider = str(data.get('ttsModelProvider', '') or '').strip()
        if _incoming_tts_provider and _incoming_tts_provider != 'gptsovits':
            core_cfg['gptsovitsEnabled'] = False
        if _incoming_tts_provider == 'doubao_tts':
            doubao_key = str(core_cfg.get('assistApiKeyDoubaoTts') or '').strip()
            if doubao_key and '***' not in doubao_key:
                core_cfg['ttsModelApiKey'] = doubao_key
            else:
                core_cfg['ttsModelApiKey'] = ''
        if 'ttsVoiceId' in data:
            core_cfg['ttsVoiceId'] = data['ttsVoiceId']

        checked_resolved_urls = data.get('connectivityCheckedProviderUrls')
        if not isinstance(checked_resolved_urls, dict):
            checked_resolved_urls = {}
        save_connectivity = await _auto_resolve_provider_urls_for_save(core_cfg, checked_resolved_urls)
        
        # save_json_config 内部已调用 assert_cloudsave_writable + ensure_config_directory
        # + atomic_write_json，不需要再显式栅栏 / 手工拼 core_config_path
        await asyncio.to_thread(
            config_manager.save_json_config, 'core_config.json', core_cfg
        )

        await ensure_default_yui_voice_for_free_api(config_manager, core_cfg)

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
        return {
            "success": True,
            "message": "API Key已保存并重新加载配置",
            "sessions_ended": len(sessions_ended),
            "connectivity": save_connectivity,
            "resolvedProviderUrls": core_cfg.get('resolvedProviderUrls', {}),
        }
    except MaintenanceModeError:
        raise
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/api_providers")
async def get_api_providers_config():
    """Get the API provider config (for frontend use)."""
    try:
        from utils.api_config_loader import (
            get_config,
            get_core_api_providers_for_frontend,
            get_assist_api_providers_for_frontend,
        )

        full_config = get_config(force_reload=True)
        # API settings is an admin/config surface; prefer current provider metadata
        # over stale in-process cache so label/keybook changes show up immediately.
        core_providers = get_core_api_providers_for_frontend(force_reload=True)
        assist_providers = get_assist_api_providers_for_frontend(force_reload=True)

        # TTS provider 的前端驱动元数据：单一源来自 utils.tts.provider_registry，
        # 避免前端把「哪些 provider 只进 TTS 下拉 / 端点可编辑 / 支持哪些声音来源 /
        # 用哪种连通性探测」再硬编码一遍（见 api_key_settings.js）。
        try:
            from utils.tts import provider_registry
            # 触发 worker 侧注册副作用（adapter 在 tts_client 定义 worker 后 register）
            import main_logic.tts_client  # noqa: F401
            tts_providers = provider_registry.ui_metadata()
        except Exception as e:
            logger.warning(f"加载 TTS provider 元数据失败: {e}")
            tts_providers = []

        return {
            "success": True,
            "core_api_providers": core_providers,
            "assist_api_providers": assist_providers,
            "api_key_registry": full_config.get("api_key_registry", {}),
            "assist_api_providers_full": full_config.get("assist_api_providers", {}),
            "core_api_providers_full": full_config.get("core_api_providers", {}),
            "keybook_api_providers_full": full_config.get("keybook_api_providers", {}),
            "tts_providers": tts_providers,
        }
    except Exception as e:
        logger.error(f"获取API服务商配置失败: {e}")
        return {
            "success": False,
            "error": str(e),
            "core_api_providers": [],
            "assist_api_providers": [],
        }
