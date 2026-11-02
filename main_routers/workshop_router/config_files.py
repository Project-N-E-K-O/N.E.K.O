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
import errno
import stat
import asyncio
from urllib.parse import unquote
from fastapi.responses import JSONResponse
from utils.workshop_utils import (
    get_workshop_path_async,
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


# Save Workshop configuration.

# Serialize the entire read -> merge -> persist -> mkdir transaction.
#
# All steps now run in workers: writes include unbounded fsync, while ensure can probe/create network or
# removable directories; on the event loop these block all coroutines. Threads, however, permit genuine
# request interleaving:
#
# * Two /config requests: ensure_workshop_folder_exists rereads configuration to determine
#   auto_create (utils/workshop_utils.py:53/:75). A saves auto_create=true and
#   directory A, B immediately saves auto_create=false, then A's ensure reads B's policy and refuses mkdir,
#   while A still returns success.
# * /config versus GET /config: load_workshop_config is not read-only; after storage
#   migration, _rebase_workshop_config_after_storage_migration persists self-repair.
#   GET can read before the transaction and write afterward, overwriting freshly submitted settings.
#
# Use ConfigManager's _workshop_config_lock via workshop_config_lock(),
# not a private local lock, because self-repair writes acquire that same lock and require shared exclusion.
# Its RLock permits calling load_workshop_config while held without self-deadlock.


@router.post('/config')
async def save_workshop_config_api(config_data: dict):
    try:
        # Import helpers through the same path as get_workshop_config for consistency.
        from utils.workshop_utils import load_workshop_config, save_workshop_config, ensure_workshop_folder_exists

        # Validate types before persistence. Unchecked frontend dict/list input would enter configuration,
        # with errors reported only when ensure_workshop_folder_exists reaches os.path.isabs(), after config
        # corruption. Subsequent get_workshop_path() returns that object unchanged and all Workshop
        # os.path.join() operations fail until users repair it manually.
        for key in ('default_workshop_folder', 'user_mod_folder'):
            if key not in config_data:
                continue
            value = config_data[key]
            if not isinstance(value, str):
                return {
                    "success": False,
                    "error": f"{key} 必须是字符串路径",
                }
            # Reject relative paths rather than guessing a base and normalizing. ensure resolves against user home
            # and reports folder_ready:true, but get_workshop_path() returns the relative string unchanged,
            # and later _assert_under_base resolves against service cwd. Those locations differ
            # despite reporting readiness. Require callers to provide absolute paths.
            # Empty strings officially clear overrides: get_workshop_path() checks
            # `if config.get("user_mod_folder"):` and falsy empty strings fall back to
            # Steam/cache/default paths (utils/config_manager/workshop.py:417). Previously rejecting empty strings
            # allowed setting/replacement but prevented API clearing, requiring manual JSON edits.
            # Still reject whitespace-only strings ("   "), which represent real paths rather than clearing.
            if value == "":
                if key == 'user_mod_folder':
                    continue
                return {
                    "success": False,
                    "error": "default_workshop_folder 不能为空",
                }
            if not value.strip():
                return {
                    "success": False,
                    "error": f"{key} 不能是空白（清除请传空字符串）",
                }
            if not os.path.isabs(value):
                return {
                    "success": False,
                    "error": f"{key} 必须是绝对路径",
                }
            # Validate positively that os.path can genuinely process the string. Embedded NUL passes
            # isabs's prefix-only check but makes later os.path operations raise ValueError after
            # persistence, merely reporting failure after corrupting config and poisoning every Workshop file
            # operation until users save again.
            #
            # Avoid enumerating invalid character classes such as NUL/control/overlength; let the OS decide usability,
            # eliminating the entire class through one rule instead of piecemeal fixes.

        if 'auto_create_folder' in config_data and not isinstance(
            config_data['auto_create_folder'], bool
        ):
            # String "false" is truthy; without validation, explicit refusal to create directories would create them
            # and persist the malformed value.
            return {
                "success": False,
                "error": "auto_create_folder 必须是布尔值",
            }

        def _apply_config_transaction() -> tuple[dict, bool | None]:
            # Probe OS acceptance through an actual syscall: pure path functions cannot detect embedded NUL,
            # since isdir swallows ValueError as False and normpath/realpath
            # return unchanged. Syscalls mean IO and may hang on slow UNC/network/removable
            # targets, so run only in workers, never the event loop.
            # Raise for the outer except to return {"success": false, ...}; nothing has been written yet.
            for probe_key in ('default_workshop_folder', 'user_mod_folder'):
                probe = config_data.get(probe_key)
                if not isinstance(probe, str) or probe == '':
                    continue
                try:
                    probe_stat = os.stat(probe)
                except ValueError as exc:
                    raise ValueError(f"{probe_key} 不是合法路径: {exc}") from exc
                except OSError as exc:
                    # Allow only syntactically valid but currently absent/inaccessible paths. Previously swallowing every
                    # OSError persisted ENAMETOOLONG/EINVAL paths, poisoning all subsequent
                    # Workshop calls until another user save.
                    allowed_errnos = {errno.ENOENT, errno.EACCES, errno.EPERM}
                    allowed_winerrors = {
                        2,    # ERROR_FILE_NOT_FOUND
                        3,    # ERROR_PATH_NOT_FOUND
                        5,    # ERROR_ACCESS_DENIED
                        21,   # ERROR_NOT_READY (removable drive)
                        53,   # ERROR_BAD_NETPATH
                        64,   # ERROR_NETNAME_DELETED
                        67,   # ERROR_BAD_NET_NAME
                        121,  # ERROR_SEM_TIMEOUT
                    }
                    if (
                        getattr(exc, 'errno', None) not in allowed_errnos
                        and getattr(exc, 'winerror', None) not in allowed_winerrors
                    ):
                        raise ValueError(
                            f"{probe_key} 不是合法路径: {exc}"
                        ) from exc
                else:
                    if not stat.S_ISDIR(probe_stat.st_mode):
                        raise ValueError(f"{probe_key} 必须指向目录")

            with get_config_manager().workshop_config_lock():
                # Lock reads too, or concurrent requests read the same old config and save separate merges,
                # making the later save overwrite the earlier fields entirely.
                merged = load_workshop_config() or {}
                for key in ('default_workshop_folder', 'auto_create_folder', 'user_mod_folder'):
                    if key in config_data:
                        merged[key] = config_data[key]
                save_workshop_config(merged)
                auto_create = bool(merged.get('auto_create_folder', True))
                # Prefer user_mod_folder, then default_workshop_folder.
                folder_path = ''
                if auto_create:
                    folder_path = merged.get('user_mod_folder') or merged.get('default_workshop_folder') or ''
            # Create directories outside the lock but inside the same worker.
            #
            # Outside the lock because network/removable exists/makedirs may be unboundedly slow;
            # event-loop handlers also call get_workshop_path() directly, so holding the lock would block
            # the entire loop. Policy, auto_create plus target path, was fixed under lock and passed explicitly,
            # preventing ensure from rereading someone else's config.
            #
            # Same worker because splitting into two to_thread calls permits cancellation between them:
            # asyncio.to_thread cannot stop an already running worker, but CancelledError prevents
            # the handler's second call, leaving config persisted without directory creation. Request timeout
            # and application shutdown both reach this path.
            folder_ready = None
            if auto_create and folder_path:
                folder_ready = bool(
                    ensure_workshop_folder_exists(folder_path, auto_create=True)
                )
            return merged, folder_ready

        workshop_config_data, folder_ready = await asyncio.to_thread(
            _apply_config_transaction
        )

        # ensure_workshop_folder_exists converts mkdir failures such as read-only disks/permissions into False.
        # Configuration was persisted, so success remains True, but do not claim directory readiness:
        # that path remains unusable. Report both outcomes separately.
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
        
        # Decode URL-encoded paths.
        decoded_path = unquote(path)
        decoded_path = _assert_under_base(
            decoded_path, await get_workshop_path_async()
        )
        logger.info(f"解码后的路径: {decoded_path}")
        
        # Check file existence.
        if not os.path.exists(decoded_path) or not os.path.isfile(decoded_path):
            logger.warning(f"文件不存在: {decoded_path}")
            return JSONResponse(content={"success": False, "error": "文件不存在"}, status_code=404)
        
        # Check file-size limits, e.g. 5 MB.
        MAX_FILE_SIZE = 5 * 1024 * 1024  # 5MB
        file_size = os.path.getsize(decoded_path)
        if file_size > MAX_FILE_SIZE:
            logger.warning(f"文件过大: {decoded_path} ({file_size / 1024 / 1024:.2f}MB > {MAX_FILE_SIZE / 1024 / 1024}MB)")
            return JSONResponse(content={"success": False, "error": "文件过大"}, status_code=413)
        
        # Detect file type and choose the appropriate read method.
        file_extension = os.path.splitext(decoded_path)[1].lower()
        is_binary = file_extension in ['.mp3', '.wav', '.png', '.jpg', '.jpeg', '.gif']
        
        if is_binary:
            # Read binary files and base64-encode them.
            import base64
            with open(decoded_path, 'rb') as f:
                binary_content = f.read()
            content = base64.b64encode(binary_content).decode('utf-8')
        else:
            # Read text files.
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
        
        # Decode URL-encoded paths.
        decoded_dir = _assert_under_base(
            unquote(directory), await get_workshop_path_async()
        )
        logger.info(f"解码后的目录路径: {decoded_dir}")
        
        # Check directory existence.
        if not os.path.exists(decoded_dir) or not os.path.isdir(decoded_dir):
            logger.warning(f"目录不存在: {decoded_dir}")
            return JSONResponse(content={"success": False, "error": "目录不存在"}, status_code=404)
        
        # Find all .chara.json files.
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
        
        # Decode URL-encoded paths and validate Workshop containment.
        decoded_dir = _assert_under_base(
            unquote(directory), await get_workshop_path_async()
        )
        logger.info(f"解码后的目录路径: {decoded_dir}")
        
        # Check directory existence.
        if not os.path.exists(decoded_dir) or not os.path.isdir(decoded_dir):
            logger.warning(f"目录不存在: {decoded_dir}")
            return JSONResponse(content={"success": False, "error": "目录不存在"}, status_code=404)
        
        # Find all audio files.
        audio_files = []
        for filename in os.listdir(decoded_dir):
            if filename.endswith(('.mp3', '.wav')):
                file_path = os.path.join(decoded_dir, filename)
                if os.path.isfile(file_path):
                    # Use filename stems without extensions as prefix.
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
