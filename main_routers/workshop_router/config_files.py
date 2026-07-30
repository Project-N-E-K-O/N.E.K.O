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

"""Workshop config get/save and sandboxed file listing/reading
endpoints.

Split out of the former monolithic ``main_routers/workshop_router.py``.
"""

from ._shared import logger, router

import os
import asyncio
import threading
from urllib.parse import unquote
from fastapi.responses import JSONResponse
from utils.workshop_utils import (
    get_workshop_path,
)


@router.get('/config')
async def get_workshop_config():
    try:
        from utils.workshop_utils import load_workshop_config
        workshop_config_data = await asyncio.to_thread(load_workshop_config)
        return {"success": True, "config": workshop_config_data}
    except Exception as e:
        logger.error(f"获取创意工坊配置失败: {str(e)}")
        return {"success": False, "error": str(e)}


# 保存创意工坊配置

# 整个「读 → 合并 → 落盘 → 建目录」是一次事务，必须串行。
#
# 这几步现在都跑在 worker 线程上（落盘含无上界 fsync，ensure 还可能对着网络盘或
# 可移动盘做 exists / makedirs，留在事件循环上会卡住所有协程）。但一挪进线程，两个
# 并发的 /config 请求就能真交错，而 ensure_workshop_folder_exists 会**重新读一次
# 配置文件**来决定 auto_create（utils/workshop_utils.py:53 与 :75）：A 存下
# auto_create=true + 目录 A，B 紧接着存下 auto_create=false，A 的 ensure 读到的是
# B 的配置于是拒绝建目录，而 A 照样返回 success —— 用户看到"保存成功"但目录没建。
# 改动前这一整段同步跑在循环线程上，物理上交错不了。
#
# 锁覆盖到 ensure 之内，所以它那次重读看到的一定是本次事务自己刚写的配置，不需要
# 去改 ensure_workshop_folder_exists 这个公共 util 的签名。
_WORKSHOP_CONFIG_TRANSACTION_LOCK = threading.Lock()


@router.post('/config')
async def save_workshop_config_api(config_data: dict):
    try:
        # 导入与get_workshop_config相同路径的函数，保持一致性
        from utils.workshop_utils import load_workshop_config, save_workshop_config, ensure_workshop_folder_exists

        def _apply_config_transaction() -> dict:
            with _WORKSHOP_CONFIG_TRANSACTION_LOCK:
                # 读也放进锁里：不然两个请求各自读到同一份旧配置、各写各的合并结果，
                # 后写的那次会把前一次的字段整份盖掉。
                merged = load_workshop_config() or {}
                for key in ('default_workshop_folder', 'auto_create_folder', 'user_mod_folder'):
                    if key in config_data:
                        merged[key] = config_data[key]
                save_workshop_config(merged)
                if merged.get('auto_create_folder', True):
                    # 优先使用user_mod_folder，如果没有则使用default_workshop_folder
                    folder_path = merged.get('user_mod_folder') or merged.get('default_workshop_folder')
                    if folder_path:
                        ensure_workshop_folder_exists(folder_path)
                return merged

        workshop_config_data = await asyncio.to_thread(_apply_config_transaction)

        return {"success": True, "config": workshop_config_data}
    except Exception as e:
        logger.error(f"保存创意工坊配置失败: {str(e)}")
        return {"success": False, "error": str(e)}


def _assert_under_base(path: str, base: str) -> str:
    full = os.path.realpath(os.path.normpath(path))
    base_full = os.path.realpath(os.path.normpath(base))
    if os.path.commonpath([full, base_full]) != base_full:
        raise PermissionError("path not allowed")
    return full


@router.get('/read-file')
async def read_workshop_file(path: str):
    """Read workshop file content."""
    try:
        logger.info(f"读取创意工坊文件请求，路径: {path}")
        
        # 解码URL编码的路径
        decoded_path = unquote(path)
        decoded_path = _assert_under_base(decoded_path, get_workshop_path())
        logger.info(f"解码后的路径: {decoded_path}")
        
        # 检查文件是否存在
        if not os.path.exists(decoded_path) or not os.path.isfile(decoded_path):
            logger.warning(f"文件不存在: {decoded_path}")
            return JSONResponse(content={"success": False, "error": "文件不存在"}, status_code=404)
        
        # 检查文件大小限制（例如5MB）
        MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
        file_size = os.path.getsize(decoded_path)
        if file_size > MAX_FILE_SIZE:
            logger.warning(f"文件过大: {decoded_path} ({file_size / 1024 / 1024:.2f}MB > {MAX_FILE_SIZE / 1024 / 1024}MB)")
            return JSONResponse(content={"success": False, "error": "文件过大"}, status_code=413)
        
        # 尝试判断文件类型并选择合适的读取方式
        file_extension = os.path.splitext(decoded_path)[1].lower()
        is_binary = file_extension in ['.mp3', '.wav', '.png', '.jpg', '.jpeg', '.gif']
        
        if is_binary:
            # 以二进制模式读取文件并进行base64编码
            import base64
            with open(decoded_path, 'rb') as f:
                binary_content = f.read()
            content = base64.b64encode(binary_content).decode('utf-8')
        else:
            # 以文本模式读取文件
            with open(decoded_path, 'r', encoding='utf-8') as f:
                content = f.read()
        
        logger.info(f"成功读取文件: {decoded_path}, 是二进制文件: {is_binary}")
        return JSONResponse(content={"success": True, "content": content, "is_binary": is_binary})
    except Exception as e:
        logger.error(f"读取文件失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": f"读取文件失败: {str(e)}"}, status_code=500)


@router.get('/list-chara-files')
async def list_chara_files(directory: str):
    """List all .chara.json files under the given directory."""
    try:
        logger.info(f"列出创意工坊目录下的角色卡文件请求，目录: {directory}")
        
        # 解码URL编码的路径
        decoded_dir = _assert_under_base(unquote(directory), get_workshop_path())
        logger.info(f"解码后的目录路径: {decoded_dir}")
        
        # 检查目录是否存在
        if not os.path.exists(decoded_dir) or not os.path.isdir(decoded_dir):
            logger.warning(f"目录不存在: {decoded_dir}")
            return JSONResponse(content={"success": False, "error": "目录不存在"}, status_code=404)
        
        # 查找所有.chara.json文件
        chara_files = []
        for filename in os.listdir(decoded_dir):
            if filename.endswith('.chara.json'):
                file_path = os.path.join(decoded_dir, filename)
                if os.path.isfile(file_path):
                    chara_files.append({
                        'name': filename,
                        'path': file_path
                    })
        
        logger.info(f"成功列出目录下的角色卡文件: {decoded_dir}, 找到 {len(chara_files)} 个文件")
        return JSONResponse(content={"success": True, "files": chara_files})
    except Exception as e:
        logger.error(f"列出角色卡文件失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": f"列出角色卡文件失败: {str(e)}"}, status_code=500)


@router.get('/list-audio-files')
async def list_audio_files(directory: str):
    """List all audio files (.mp3, .wav) under the given directory."""
    try:
        logger.info(f"列出创意工坊目录下的音频文件请求，目录: {directory}")
        
        # 解码URL编码的路径并验证是否在workshop目录下
        decoded_dir = _assert_under_base(unquote(directory), get_workshop_path())
        logger.info(f"解码后的目录路径: {decoded_dir}")
        
        # 检查目录是否存在
        if not os.path.exists(decoded_dir) or not os.path.isdir(decoded_dir):
            logger.warning(f"目录不存在: {decoded_dir}")
            return JSONResponse(content={"success": False, "error": "目录不存在"}, status_code=404)
        
        # 查找所有音频文件
        audio_files = []
        for filename in os.listdir(decoded_dir):
            if filename.endswith(('.mp3', '.wav')):
                file_path = os.path.join(decoded_dir, filename)
                if os.path.isfile(file_path):
                    # 提取文件名前缀（不含扩展名）作为prefix
                    prefix = os.path.splitext(filename)[0]
                    audio_files.append({
                        'name': filename,
                        'path': file_path,
                        'prefix': prefix
                    })
        
        logger.info(f"成功列出目录下的音频文件: {decoded_dir}, 找到 {len(audio_files)} 个文件")
        return JSONResponse(content={"success": True, "files": audio_files})
    except Exception as e:
        logger.error(f"列出音频文件失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": f"列出音频文件失败: {str(e)}"}, status_code=500)
