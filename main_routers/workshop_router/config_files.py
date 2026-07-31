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
from ..shared_state import get_config_manager

import os
import asyncio
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
# 并发请求就能真交错：
#
# * 两个 /config 之间 —— ensure_workshop_folder_exists 会**重新读一次配置文件**来
#   决定 auto_create（utils/workshop_utils.py:53 与 :75）：A 存下 auto_create=true +
#   目录 A，B 紧接着存下 auto_create=false，A 的 ensure 读到 B 的配置于是拒绝建目录，
#   而 A 照样返回 success。
# * /config 与 GET /config 之间 —— load_workshop_config 那条路径**不是只读**：存储
#   迁移之后 _rebase_workshop_config_after_storage_migration 会把自愈结果 save 回去。
#   GET 可以「事务之前读、事务之后写」，把用户刚提交的设置整份盖掉。
#
# 所以用的是 ConfigManager 自己那把 _workshop_config_lock（经 workshop_config_lock()
# 拿），而不是本模块私有的一把 —— 自愈写走的就是它，两边必须是同一把才挡得住。
# 它是 RLock，所以持着它再调 load_workshop_config 不会自死锁。


@router.post('/config')
async def save_workshop_config_api(config_data: dict):
    try:
        # 导入与get_workshop_config相同路径的函数，保持一致性
        from utils.workshop_utils import load_workshop_config, save_workshop_config, ensure_workshop_folder_exists

        # 落盘前先校验类型。前端塞个 {} 或 list 进来的话，这里不拦就直接写进配置文件，
        # 之后 ensure_workshop_folder_exists 在 os.path.isabs() 上抛出来才报错 —— 配置
        # 已经被写坏了，后续 get_workshop_path() 会把这个对象原样返回，凡是拿它去
        # os.path.join() 的 workshop 调用全部失败，直到用户手工修好。
        for key in ('default_workshop_folder', 'user_mod_folder'):
            if key not in config_data:
                continue
            value = config_data[key]
            if not isinstance(value, str):
                return {
                    "success": False,
                    "error": f"{key} 必须是字符串路径",
                }
            # 相对路径直接拒，不做「猜一个 base 再normalize」。ensure 会按用户主目录
            # 解析它并报 folder_ready: true，而 get_workshop_path() 原样返回那个相对
            # 串、后续 _assert_under_base 又按服务进程的工作目录解析 —— 两边指向不同
            # 的地方，而我们已经告诉用户「建好了」。宁可让调用方给绝对路径。
            if value.strip() and not os.path.isabs(value):
                return {
                    "success": False,
                    "error": f"{key} 必须是绝对路径",
                }
        if 'auto_create_folder' in config_data and not isinstance(
            config_data['auto_create_folder'], bool
        ):
            # 字符串 "false" 是 truthy —— 不拦就会在用户明确说「别建」的时候建目录，
            # 而且把这个畸形值留在盘上。
            return {
                "success": False,
                "error": "auto_create_folder 必须是布尔值",
            }

        def _apply_config_transaction() -> tuple[dict, bool | None]:
            with get_config_manager().workshop_config_lock():
                # 读也放进锁里：不然两个请求各自读到同一份旧配置、各写各的合并结果，
                # 后写的那次会把前一次的字段整份盖掉。
                merged = load_workshop_config() or {}
                for key in ('default_workshop_folder', 'auto_create_folder', 'user_mod_folder'):
                    if key in config_data:
                        merged[key] = config_data[key]
                save_workshop_config(merged)
                auto_create = bool(merged.get('auto_create_folder', True))
                # 优先使用user_mod_folder，如果没有则使用default_workshop_folder
                folder_path = ''
                if auto_create:
                    folder_path = merged.get('user_mod_folder') or merged.get('default_workshop_folder') or ''
            # ⚠️ 建目录在**锁外、但仍在同一个 worker 里**做。
            #
            # 锁外：它可能是网络盘 / 可移动盘上的 exists + makedirs，慢得没有上界；
            # 而事件循环上还有 handler 裸调 get_workshop_path()，持锁做这件事就是把
            # 整条循环挂在那儿。策略（auto_create + 目标路径）已经在锁内定死并显式
            # 传进去，所以 ensure 不会重读到别人的配置。
            #
            # 同一个 worker：拆成两次 to_thread 的话，取消会落在两者之间 ——
            # asyncio.to_thread 不会停掉已经开跑的 worker，但 CancelledError 会让
            # handler 再也走不到第二次调用，于是「配置写了、目录没建」。请求超时和
            # 应用关闭都会走到这条路径。
            folder_ready = None
            if auto_create and folder_path:
                folder_ready = bool(
                    ensure_workshop_folder_exists(folder_path, auto_create=True)
                )
            return merged, folder_ready

        workshop_config_data, folder_ready = await asyncio.to_thread(
            _apply_config_transaction
        )

        # ensure_workshop_folder_exists 把创建失败（只读盘、权限不足）吞成返回 False。
        # 配置确实存下来了，所以 success 仍然是 True —— 但不能因此告诉用户目录也准备
        # 好了：那条路径接下来根本用不了。两件事分开报。
        response = {"success": True, "config": workshop_config_data}
        if folder_ready is not None:
            response["folder_ready"] = folder_ready
            if not folder_ready:
                response["warning"] = "配置已保存，但指定的工坊目录无法创建（路径只读或权限不足）"
        return response
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
