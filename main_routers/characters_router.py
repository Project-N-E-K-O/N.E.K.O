# -*- coding: utf-8 -*-
"""
Characters Router

Handles character (catgirl) management endpoints including:
- Character CRUD operations
- Voice settings
- Microphone settings
"""

import json
import io
import logging
import asyncio
from datetime import datetime
import pathlib
import wave

from fastapi import APIRouter, Request, File, UploadFile, Form
from fastapi.responses import JSONResponse
import httpx
import dashscope
from dashscope.audio.tts_v2 import VoiceEnrollmentService

from .shared_state import get_config_manager, get_session_manager, get_initialize_character_data
from config import MEMORY_SERVER_PORT

router = APIRouter(tags=["characters"])
logger = logging.getLogger("Main")


@router.get('/api/characters')
async def get_characters():
    _config_manager = get_config_manager()
    return JSONResponse(content=_config_manager.load_characters())


@router.get('/api/characters/current_catgirl')
async def get_current_catgirl():
    """获取当前使用的猫娘名称"""
    _config_manager = get_config_manager()
    characters = _config_manager.load_characters()
    current_catgirl = characters.get('当前猫娘', '')
    return JSONResponse(content={'current_catgirl': current_catgirl})


@router.get('/api/characters/catgirl/{name}/voice_mode_status')
async def get_catgirl_voice_mode_status(name: str):
    """检查指定角色是否在语音模式下"""
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    characters = _config_manager.load_characters()
    is_current = characters.get('当前猫娘') == name
    
    if name not in session_manager:
        return JSONResponse({'is_voice_mode': False, 'is_current': is_current, 'is_active': False})
    
    mgr = session_manager[name]
    is_active = mgr.is_active if mgr else False
    
    is_voice_mode = False
    if is_active and mgr:
        from main_logic.omni_realtime_client import OmniRealtimeClient
        is_voice_mode = mgr.session and isinstance(mgr.session, OmniRealtimeClient)
    
    return JSONResponse({
        'is_voice_mode': is_voice_mode,
        'is_current': is_current,
        'is_active': is_active
    })


@router.post('/api/characters/current_catgirl')
async def set_current_catgirl(request: Request):
    """设置当前使用的猫娘"""
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    initialize_character_data = get_initialize_character_data()
    
    data = await request.json()
    catgirl_name = data.get('catgirl_name', '') if data else ''
    
    if not catgirl_name:
        return JSONResponse({'success': False, 'error': '猫娘名称不能为空'}, status_code=400)
    
    characters = _config_manager.load_characters()
    if catgirl_name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '指定的猫娘不存在'}, status_code=404)
    
    old_catgirl = characters.get('当前猫娘', '')
    
    # 检查当前角色是否有活跃的语音session
    if old_catgirl and old_catgirl in session_manager:
        mgr = session_manager[old_catgirl]
        if mgr.is_active:
            from main_logic.omni_realtime_client import OmniRealtimeClient
            is_voice_mode = mgr.session and isinstance(mgr.session, OmniRealtimeClient)
            
            if is_voice_mode:
                return JSONResponse({
                    'success': False, 
                    'error': '语音状态下无法切换角色，请先停止语音对话后再切换'
                }, status_code=400)
    
    characters['当前猫娘'] = catgirl_name
    _config_manager.save_characters(characters)
    
    if initialize_character_data:
        await initialize_character_data()
    
    # 通知所有WebSocket客户端
    notification_count = 0
    message = json.dumps({
        "type": "catgirl_switched",
        "new_catgirl": catgirl_name,
        "old_catgirl": old_catgirl
    })
    
    for lanlan_name, mgr in session_manager.items():
        ws = mgr.websocket
        if ws:
            try:
                await ws.send_text(message)
                notification_count += 1
                logger.info(f"✅ 已通过WebSocket通知 {lanlan_name}")
            except Exception as e:
                logger.warning(f"❌ 通知 {lanlan_name} 失败: {e}")
                if mgr.websocket == ws:
                    mgr.websocket = None
    
    logger.info(f"已通知 {notification_count} 个客户端")
    return {"success": True}


@router.post('/api/characters/reload')
async def reload_character_config():
    """重新加载角色配置（热重载）"""
    try:
        initialize_character_data = get_initialize_character_data()
        if initialize_character_data:
            await initialize_character_data()
        return {"success": True, "message": "角色配置已重新加载"}
    except Exception as e:
        logger.error(f"重新加载角色配置失败: {e}")
        return JSONResponse(
            {'success': False, 'error': f'重新加载失败: {str(e)}'}, 
            status_code=500
        )


@router.post('/api/characters/master')
async def update_master(request: Request):
    _config_manager = get_config_manager()
    initialize_character_data = get_initialize_character_data()
    
    data = await request.json()
    if not data or not data.get('档案名'):
        return JSONResponse({'success': False, 'error': '档案名为必填项'}, status_code=400)
    
    characters = _config_manager.load_characters()
    characters['主人'] = {k: v for k, v in data.items() if v}
    _config_manager.save_characters(characters)
    
    if initialize_character_data:
        await initialize_character_data()
    return {"success": True}


@router.post('/api/characters/catgirl')
async def add_catgirl(request: Request):
    _config_manager = get_config_manager()
    initialize_character_data = get_initialize_character_data()
    
    data = await request.json()
    if not data or not data.get('档案名'):
        return JSONResponse({'success': False, 'error': '档案名为必填项'}, status_code=400)
    
    characters = _config_manager.load_characters()
    key = data['档案名']
    if key in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '该猫娘已存在'}, status_code=400)
    
    if '猫娘' not in characters:
        characters['猫娘'] = {}
    
    catgirl_data = {}
    for k, v in data.items():
        if k != '档案名':
            if k == 'voice_id' and v == '':
                continue
            elif v:
                catgirl_data[k] = v
    
    characters['猫娘'][key] = catgirl_data
    _config_manager.save_characters(characters)
    
    if initialize_character_data:
        await initialize_character_data()
    
    # 通知记忆服务器重新加载配置
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(f"http://localhost:{MEMORY_SERVER_PORT}/reload", timeout=5.0)
            if resp.status_code == 200:
                logger.info(f"✅ 已通知记忆服务器重新加载配置")
    except Exception as e:
        logger.warning(f"⚠️ 通知记忆服务器时出错: {e}")
    
    return {"success": True}


@router.put('/api/characters/catgirl/{name}')
async def update_catgirl(name: str, request: Request):
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    initialize_character_data = get_initialize_character_data()
    
    data = await request.json()
    if not data:
        return JSONResponse({'success': False, 'error': '无数据'}, status_code=400)
    
    characters = _config_manager.load_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
    
    old_voice_id = characters['猫娘'][name].get('voice_id', '')
    
    if 'voice_id' in data:
        voice_id = data['voice_id']
        if voice_id != '' and not _config_manager.validate_voice_id(voice_id):
            voices = _config_manager.get_voices_for_current_api()
            return JSONResponse({
                'success': False, 
                'error': f'voice_id "{voice_id}" 在当前API的音色库中不存在',
                'available_voices': list(voices.keys())
            }, status_code=400)
    
    # 更新字段
    removed_fields = [k for k in characters['猫娘'][name].keys() 
                      if k not in data and k not in ('档案名', 'system_prompt', 'voice_id', 'live2d')]
    for k in removed_fields:
        characters['猫娘'][name].pop(k)
    
    if 'voice_id' in data and data['voice_id'] == '':
        characters['猫娘'][name].pop('voice_id', None)
    
    for k, v in data.items():
        if k not in ('档案名', 'voice_id') and v:
            characters['猫娘'][name][k] = v
        elif k == 'voice_id' and v:
            characters['猫娘'][name][k] = v
    
    _config_manager.save_characters(characters)
    
    new_voice_id = characters['猫娘'][name].get('voice_id', '')
    voice_id_changed = (old_voice_id != new_voice_id)
    is_current_catgirl = (name == characters.get('当前猫娘', ''))
    session_ended = False
    
    if voice_id_changed and is_current_catgirl and name in session_manager:
        if session_manager[name].is_active:
            if session_manager[name].websocket:
                try:
                    await session_manager[name].websocket.send_text(json.dumps({
                        "type": "reload_page",
                        "message": "语音已更新，页面即将刷新"
                    }))
                except Exception as e:
                    logger.warning(f"通知前端刷新页面失败: {e}")
            
            try:
                await session_manager[name].end_session(by_server=True)
                session_ended = True
            except Exception as e:
                logger.error(f"结束session时出错: {e}")
    
    if voice_id_changed and is_current_catgirl and initialize_character_data:
        await initialize_character_data()
    
    return {"success": True, "voice_id_changed": voice_id_changed, "session_restarted": session_ended}


@router.delete('/api/characters/catgirl/{name}')
async def delete_catgirl(name: str):
    import shutil
    
    _config_manager = get_config_manager()
    initialize_character_data = get_initialize_character_data()
    
    characters = _config_manager.load_characters()
    if name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
    
    current_catgirl = characters.get('当前猫娘', '')
    if name == current_catgirl:
        return JSONResponse({'success': False, 'error': '不能删除当前正在使用的猫娘！'}, status_code=400)
    
    # 删除记忆文件
    try:
        memory_paths = [_config_manager.memory_dir, _config_manager.project_memory_dir]
        files_to_delete = [
            f'semantic_memory_{name}',
            f'time_indexed_{name}',
            f'settings_{name}.json',
            f'recent_{name}.json',
        ]
        
        for base_dir in memory_paths:
            for file_name in files_to_delete:
                file_path = base_dir / file_name
                if file_path.exists():
                    try:
                        if file_path.is_dir():
                            shutil.rmtree(file_path)
                        else:
                            file_path.unlink()
                        logger.info(f"已删除: {file_path}")
                    except Exception as e:
                        logger.warning(f"删除失败 {file_path}: {e}")
    except Exception as e:
        logger.error(f"删除记忆文件时出错: {e}")
    
    del characters['猫娘'][name]
    _config_manager.save_characters(characters)
    
    if initialize_character_data:
        await initialize_character_data()
    return {"success": True}


@router.post('/api/characters/catgirl/{old_name}/rename')
async def rename_catgirl(old_name: str, request: Request):
    _config_manager = get_config_manager()
    session_manager = get_session_manager()
    initialize_character_data = get_initialize_character_data()
    
    data = await request.json()
    new_name = data.get('new_name') if data else None
    if not new_name:
        return JSONResponse({'success': False, 'error': '新档案名不能为空'}, status_code=400)
    
    characters = _config_manager.load_characters()
    if old_name not in characters.get('猫娘', {}):
        return JSONResponse({'success': False, 'error': '原猫娘不存在'}, status_code=404)
    if new_name in characters['猫娘']:
        return JSONResponse({'success': False, 'error': '新档案名已存在'}, status_code=400)
    
    is_current_catgirl = characters.get('当前猫娘') == old_name
    
    if is_current_catgirl and old_name in session_manager:
        mgr = session_manager[old_name]
        if mgr.is_active:
            from main_logic.omni_realtime_client import OmniRealtimeClient
            is_voice_mode = mgr.session and isinstance(mgr.session, OmniRealtimeClient)
            if is_voice_mode:
                return JSONResponse({
                    'success': False, 
                    'error': '语音状态下无法修改角色名称'
                }, status_code=400)
    
    if is_current_catgirl and old_name in session_manager:
        message = json.dumps({
            "type": "catgirl_switched",
            "new_catgirl": new_name,
            "old_catgirl": old_name
        })
        ws = session_manager[old_name].websocket
        if ws:
            try:
                await ws.send_text(message)
            except Exception as e:
                logger.warning(f"发送重命名通知失败: {e}")
    
    characters['猫娘'][new_name] = characters['猫娘'].pop(old_name)
    if is_current_catgirl:
        characters['当前猫娘'] = new_name
    _config_manager.save_characters(characters)
    
    if initialize_character_data:
        await initialize_character_data()
    
    return {"success": True}


@router.post('/api/characters/catgirl/{name}/unregister_voice')
async def unregister_voice(name: str):
    """解除猫娘的声音注册"""
    _config_manager = get_config_manager()
    initialize_character_data = get_initialize_character_data()
    
    try:
        characters = _config_manager.load_characters()
        if name not in characters.get('猫娘', {}):
            return JSONResponse({'success': False, 'error': '猫娘不存在'}, status_code=404)
        
        if not characters['猫娘'][name].get('voice_id'):
            return JSONResponse({'success': False, 'error': '该猫娘未注册声音'}, status_code=400)
        
        if 'voice_id' in characters['猫娘'][name]:
            characters['猫娘'][name].pop('voice_id')
        _config_manager.save_characters(characters)
        
        if initialize_character_data:
            await initialize_character_data()
        
        return {"success": True, "message": "声音注册已解除"}
        
    except Exception as e:
        logger.error(f"解除声音注册时出错: {e}")
        return JSONResponse({'success': False, 'error': f'解除注册失败: {str(e)}'}, status_code=500)


@router.post('/api/characters/clear_voice_ids')
async def clear_voice_ids():
    """清除所有角色的本地Voice ID记录"""
    _config_manager = get_config_manager()
    initialize_character_data = get_initialize_character_data()
    
    try:
        characters = _config_manager.load_characters()
        cleared_count = 0
        
        if '猫娘' in characters:
            for name in characters['猫娘']:
                if 'voice_id' in characters['猫娘'][name] and characters['猫娘'][name]['voice_id']:
                    characters['猫娘'][name]['voice_id'] = ''
                    cleared_count += 1
        
        _config_manager.save_characters(characters)
        if initialize_character_data:
            await initialize_character_data()
        
        return JSONResponse({
            'success': True, 
            'message': f'已清除 {cleared_count} 个角色的Voice ID记录',
            'cleared_count': cleared_count
        })
    except Exception as e:
        return JSONResponse({
            'success': False, 
            'error': f'清除Voice ID记录时出错: {str(e)}'
        }, status_code=500)


@router.post('/api/characters/set_microphone')
async def set_microphone(request: Request):
    _config_manager = get_config_manager()
    initialize_character_data = get_initialize_character_data()
    
    try:
        data = await request.json()
        microphone_id = data.get('microphone_id')
        
        characters_data = _config_manager.load_characters()
        characters_data['当前麦克风'] = microphone_id
        _config_manager.save_characters(characters_data)
        
        if initialize_character_data:
            await initialize_character_data()
        
        return {"success": True}
    except Exception as e:
        logger.error(f"保存麦克风选择失败: {e}")
        return JSONResponse(status_code=500, content={"success": False, "error": str(e)})


@router.get('/api/characters/get_microphone')
async def get_microphone():
    _config_manager = get_config_manager()
    try:
        characters_data = _config_manager.load_characters()
        microphone_id = characters_data.get('当前麦克风')
        return {"microphone_id": microphone_id}
    except Exception as e:
        logger.error(f"获取麦克风选择失败: {e}")
        return {"microphone_id": None}


@router.get('/api/voices')
async def get_voices():
    """获取当前API key对应的所有已注册音色"""
    _config_manager = get_config_manager()
    return {"voices": _config_manager.get_voices_for_current_api()}


@router.post('/api/voices')
async def register_voice(request: Request):
    """注册新音色"""
    _config_manager = get_config_manager()
    try:
        data = await request.json()
        voice_id = data.get('voice_id')
        voice_data = data.get('voice_data')
        
        if not voice_id or not voice_data:
            return JSONResponse({
                'success': False,
                'error': '缺少必要参数'
            }, status_code=400)
        
        complete_voice_data = {
            **voice_data,
            'voice_id': voice_id,
            'created_at': datetime.now().isoformat()
        }
        
        try:
            _config_manager.save_voice_for_current_api(voice_id, complete_voice_data)
        except Exception as e:
            return JSONResponse({
                'success': False,
                'error': f'保存音色配置失败: {str(e)}'
            }, status_code=500)
            
        return {"success": True, "message": "音色注册成功"}
    except Exception as e:
        return JSONResponse({
            'success': False,
            'error': str(e)
        }, status_code=500)


@router.post('/api/voice_clone')
async def voice_clone(file: UploadFile = File(...), prefix: str = Form(...)):
    """
    上传音频进行音色克隆
    1. 接收前端上传的音频文件
    2. 上传到临时文件服务器（tfLink）获得直链
    3. 调用音色注册API完成注册
    """
    _config_manager = get_config_manager()
    
    try:
        # 1. 检查文件类型和大小
        allowed_types = ['audio/wav', 'audio/mp3', 'audio/mpeg', 'audio/ogg', 'audio/m4a', 'audio/x-m4a', 
                        'audio/webm', 'audio/aac', 'audio/flac', 'audio/x-wav', 'audio/wave']
        
        mime_type = file.content_type or 'audio/wav'
        
        # 读取文件内容并检查大小
        file_content = await file.read()
        file_size = len(file_content)
        max_size = 50 * 1024 * 1024  # 50MB
        
        logger.info(f"收到音频文件上传请求: 文件名={file.filename}, 大小={file_size} bytes, MIME类型={mime_type}")
        
        if file_size > max_size:
            return JSONResponse({'error': f'文件太大，最大支持50MB，当前文件大小: {file_size / 1024 / 1024:.2f}MB'}, status_code=400)
        
        if file_size == 0:
            return JSONResponse({'error': '文件内容为空'}, status_code=400)
        
        # 创建BytesIO对象用于上传
        file_buffer = io.BytesIO(file_content)
        
        # 2. 上传到tfLink获取直链
        files = {'file': (file.filename, file_buffer, mime_type)}
        headers = {'Accept': 'application/json'}
        
        logger.info(f"正在上传文件到tfLink，文件名: {file.filename}, 大小: {file_size} bytes, MIME类型: {mime_type}")
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post('http://47.101.214.205:8000/api/upload', files=files, headers=headers)

            if resp.status_code != 200:
                logger.error(f"上传到tfLink失败，状态码: {resp.status_code}, 响应内容: {resp.text}")
                return JSONResponse({'error': f'上传到tfLink失败，状态码: {resp.status_code}, 详情: {resp.text[:200]}'}, status_code=500)
            
            try:
                data = resp.json()
                logger.info(f"tfLink原始响应: {data}")
                
                tmp_url = None
                possible_keys = ['downloadLink', 'download_link', 'url', 'direct_link', 'link', 'download_url']
                for key in possible_keys:
                    if key in data:
                        tmp_url = data[key]
                        logger.info(f"找到下载链接键: {key}")
                        break
                
                if not tmp_url:
                    logger.error(f"无法从响应中提取URL: {data}")
                    return JSONResponse({'error': f'上传成功但无法从响应中提取URL'}, status_code=500)
                
                if not tmp_url.startswith(('http://', 'https://')):
                    logger.error(f"无效的URL格式: {tmp_url}")
                    return JSONResponse({'error': f'无效的URL格式: {tmp_url}'}, status_code=500)
                    
                # 测试URL是否可访问
                test_resp = await client.head(tmp_url, timeout=10)
                if test_resp.status_code >= 400:
                    logger.error(f"生成的URL无法访问: {tmp_url}, 状态码: {test_resp.status_code}")
                    return JSONResponse({'error': f'生成的临时URL无法访问，请重试'}, status_code=500)
                    
                logger.info(f"成功获取临时URL并验证可访问性: {tmp_url}")
                
            except ValueError:
                raw_text = resp.text
                logger.error(f"上传成功但响应格式无法解析: {raw_text}")
                return JSONResponse({'error': f'上传成功但响应格式无法解析: {raw_text[:200]}'}, status_code=500)
        
        # 3. 用直链注册音色
        tts_config = _config_manager.get_model_api_config('tts_custom')
        audio_api_key = tts_config.get('api_key', '')
        
        if not audio_api_key:
            logger.error("未配置 AUDIO_API_KEY")
            return JSONResponse({
                'error': '未配置音频API密钥，请在设置中配置AUDIO_API_KEY',
                'suggestion': '请前往设置页面配置音频API密钥'
            }, status_code=400)
        
        dashscope.api_key = audio_api_key
        service = VoiceEnrollmentService()
        target_model = "cosyvoice-v3-plus"
        
        # 重试配置
        max_retries = 3
        retry_delay = 3
        
        for attempt in range(max_retries):
            try:
                logger.info(f"开始音色注册（尝试 {attempt + 1}/{max_retries}），使用URL: {tmp_url}")
                
                voice_id = service.create_voice(target_model=target_model, prefix=prefix, url=tmp_url)
                    
                logger.info(f"音色注册成功，voice_id: {voice_id}")
                voice_data = {
                    'voice_id': voice_id,
                    'prefix': prefix,
                    'file_url': tmp_url,
                    'created_at': datetime.now().isoformat()
                }
                try:
                    _config_manager.save_voice_for_current_api(voice_id, voice_data)
                    logger.info(f"voice_id已保存到音色库: {voice_id}")
                    
                    await asyncio.sleep(0.1)
                    
                    validation_success = False
                    for validation_attempt in range(3):
                        if _config_manager.validate_voice_id(voice_id):
                            validation_success = True
                            logger.info(f"voice_id保存验证成功: {voice_id} (尝试 {validation_attempt + 1})")
                            break
                        if validation_attempt < 2:
                            await asyncio.sleep(0.1)
                    
                    if not validation_success:
                        logger.warning(f"voice_id保存后验证失败，但可能已成功保存: {voice_id}")
                    
                except Exception as save_error:
                    logger.error(f"保存voice_id到音色库失败: {save_error}")
                    return JSONResponse({
                        'error': f'音色注册成功但保存到音色库失败: {str(save_error)}',
                        'voice_id': voice_id,
                        'file_url': tmp_url
                    }, status_code=500)
                    
                return JSONResponse({
                    'voice_id': voice_id,
                    'request_id': service.get_last_request_id(),
                    'file_url': tmp_url,
                    'message': '音色注册成功并已保存到音色库'
                })
                
            except Exception as e:
                logger.error(f"音色注册失败（尝试 {attempt + 1}/{max_retries}）: {str(e)}")
                error_detail = str(e)
                
                is_timeout = ("ResponseTimeout" in error_detail or 
                             "response timeout" in error_detail.lower() or
                             "timeout" in error_detail.lower())
                
                is_download_failed = ("download audio failed" in error_detail or 
                                     "415" in error_detail)
                
                if (is_timeout or is_download_failed) and attempt < max_retries - 1:
                    logger.warning(f"检测到{'超时' if is_timeout else '文件下载失败'}错误，等待 {retry_delay} 秒后重试...")
                    await asyncio.sleep(retry_delay)
                    continue
                
                if is_timeout:
                    return JSONResponse({
                        'error': f'音色注册超时，已尝试{max_retries}次',
                        'detail': error_detail,
                        'file_url': tmp_url,
                        'suggestion': '请检查您的网络连接，或稍后再试。如果问题持续，可能是服务器繁忙。'
                    }, status_code=408)
                elif is_download_failed:
                    return JSONResponse({
                        'error': f'音色注册失败: 无法下载音频文件，已尝试{max_retries}次',
                        'detail': error_detail,
                        'file_url': tmp_url,
                        'suggestion': '请检查文件URL是否可访问，或稍后重试'
                    }, status_code=415)
                else:
                    return JSONResponse({
                        'error': f'音色注册失败: {error_detail}',
                        'file_url': tmp_url,
                        'attempt': attempt + 1,
                        'max_retries': max_retries
                    }, status_code=500)
    except Exception as e:
        tmp_url = locals().get('tmp_url', '未获取到URL')
        logger.error(f"注册音色时发生未预期的错误: {str(e)}")
        return JSONResponse({'error': f'注册音色时发生错误: {str(e)}', 'file_url': tmp_url}, status_code=500)
