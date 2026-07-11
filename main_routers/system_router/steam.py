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

"""Steam achievements / playtime endpoints and the Steam image proxy.

Split out of the former monolithic ``main_routers/system_router.py``.
"""

from ._shared import _get_app_root, _is_path_within_base, _validate_local_mutation_request, logger, router
import os
import asyncio
import re
from typing import Any
from urllib.parse import unquote
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from ..shared_state import ensure_steamworks as get_steamworks
from utils.workshop_utils import get_workshop_path


# Progress Stat for timed achievements. Steamworks Partner must bind
# ACH_TIME_* achievements to this stat with matching thresholds; Steam unlocks
# them automatically when StoreStats syncs a value past the bound threshold.
_PLAYTIME_PROGRESS_STAT = "PLAY_TIME_SECONDS"
_PLAYTIME_PROGRESS_ACHIEVEMENTS: tuple[str, ...] = (
    "ACH_TIME_5MIN",
    "ACH_TIME_1HR",
    "ACH_TIME_100HR",
)


async def _prepare_steam_user_stats(steamworks: Any) -> None:
    steamworks.UserStats.RequestCurrentStats()
    for _ in range(10):
        steamworks.run_callbacks()
        await asyncio.sleep(0.1)


async def _unlock_steam_achievement(steamworks: Any, name: str) -> dict[str, Any]:
    """Unlock one Steam achievement. Returns a status dict (no HTTP response)."""
    await _prepare_steam_user_stats(steamworks)
    achievement_status = steamworks.UserStats.GetAchievement(name)
    logger.info("Achievement status: %s=%s", name, achievement_status)
    if achievement_status:
        return {
            "success": True,
            "achievement": name,
            "newlyUnlocked": False,
            "alreadyUnlocked": True,
            "message": f"成就 {name} 已经解锁",
        }

    result = steamworks.UserStats.SetAchievement(name)
    if not result:
        logger.warning("设置成就首次尝试失败，正在重试: %s", name)
        await asyncio.sleep(0.5)
        steamworks.run_callbacks()
        result = steamworks.UserStats.SetAchievement(name)

    if not result:
        logger.error("设置成就失败: %s，请确认成就ID在Steam后台已配置", name)
        return {
            "success": False,
            "achievement": name,
            "newlyUnlocked": False,
            "alreadyUnlocked": False,
            "error": f"设置成就失败: {name}，请确认成就ID在Steam后台已配置",
        }

    steamworks.UserStats.StoreStats()
    steamworks.run_callbacks()
    logger.info("成功设置成就: %s", name)
    return {
        "success": True,
        "achievement": name,
        "newlyUnlocked": True,
        "alreadyUnlocked": False,
        "message": f"成就 {name} 已解锁",
    }


def _read_progress_unlocked_achievements(steamworks: Any) -> list[str]:
    """Read which progress-stat achievements Steam has already unlocked."""
    unlocked: list[str] = []
    for achievement_name in _PLAYTIME_PROGRESS_ACHIEVEMENTS:
        try:
            if steamworks.UserStats.GetAchievement(achievement_name):
                unlocked.append(achievement_name)
        except Exception as exc:
            logger.debug("读取进度成就状态失败 %s: %s", achievement_name, exc)
    return unlocked


@router.post('/steam/set-achievement-status/{name}')
async def set_achievement_status(name: str, request: Request):
    """
    Set Steam achievement status endpoint.
    func:
    - receives the achievement name as a path parameter and sets the achievement via the Steamworks API
    - first requests current stats and runs callbacks to ensure the data is loaded
    - checks the achievement's current state; if already unlocked, returns success directly
    - if not unlocked, tries to set it; returns success if it works, otherwise waits and retries once
    """
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    steamworks = get_steamworks()
    if steamworks is None:
        return JSONResponse(content={"success": False, "error": "Steamworks未初始化"}, status_code=503)

    try:
        result = await _unlock_steam_achievement(steamworks, name)
        if not result.get("success"):
            return JSONResponse(content=result, status_code=500)
        return JSONResponse(content=result)
    except Exception as e:
        logger.error("设置成就失败: %s", e)
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@router.post('/steam/update-playtime')
async def update_playtime(request: Request):
    """
    Accumulate PLAY_TIME_SECONDS progress stat and StoreStats.

    Timed achievements (ACH_TIME_*) must be bound to this Progress Stat in
    Steamworks Partner. Steam unlocks them automatically when the synced value
    crosses the configured threshold — this endpoint never calls SetAchievement.
    """
    validation_error = _validate_local_mutation_request(request)
    if validation_error is not None:
        return validation_error

    steamworks = get_steamworks()
    if steamworks is None:
        return JSONResponse(content={"success": False, "error": "Steamworks未初始化"}, status_code=503)

    try:
        data = await request.json()
    except Exception:
        data = {}
    if not isinstance(data, dict):
        data = {}

    seconds_to_add = data.get("seconds", 10)
    try:
        seconds_to_add = int(seconds_to_add)
        if seconds_to_add < 0:
            return JSONResponse(
                content={"success": False, "error": "seconds must be non-negative"},
                status_code=400,
            )
    except (ValueError, TypeError, OverflowError):
        # OverflowError: json.loads accepts bare Infinity/-Infinity, which
        # int() cannot convert — treat it as invalid input, not a 500.
        return JSONResponse(
            content={"success": False, "error": "seconds must be a valid integer"},
            status_code=400,
        )

    # Cap a single report to 1 hour to limit abuse / clock-jump spikes.
    seconds_to_add = min(seconds_to_add, 3600)

    try:
        # Ensure Steam has delivered current stats before read/modify/write;
        # otherwise GetStatInt may return 0 and StoreStats would clobber progress.
        await _prepare_steam_user_stats(steamworks)

        try:
            current_playtime = steamworks.UserStats.GetStatInt(_PLAYTIME_PROGRESS_STAT)
        except Exception as e:
            logger.warning("获取 %s 失败，从 0 开始: %s", _PLAYTIME_PROGRESS_STAT, e)
            current_playtime = 0

        new_playtime = int(current_playtime) + seconds_to_add

        try:
            result = steamworks.UserStats.SetStat(_PLAYTIME_PROGRESS_STAT, new_playtime)
        except Exception as stat_error:
            logger.warning(
                "设置 Steam 进度统计失败: %s - 统计可能未在 Steamworks 后台配置",
                stat_error,
            )
            return JSONResponse(content={
                "success": True,
                "totalPlayTime": new_playtime,
                "added": seconds_to_add,
                "stat": _PLAYTIME_PROGRESS_STAT,
                "warning": "Steam progress stat not configured",
                "progressUnlocked": [],
            })

        if not result:
            logger.debug(
                "SetStat 返回 False - %s 统计可能未在 Steamworks 后台配置",
                _PLAYTIME_PROGRESS_STAT,
            )
            return JSONResponse(content={
                "success": True,
                "totalPlayTime": new_playtime,
                "added": seconds_to_add,
                "stat": _PLAYTIME_PROGRESS_STAT,
                "warning": "Steam progress stat not configured",
                "progressUnlocked": [],
            })

        steamworks.UserStats.StoreStats()
        # Give Steam a short window to apply Progress Stat → achievement unlocks.
        for _ in range(5):
            steamworks.run_callbacks()
            await asyncio.sleep(0.05)

        progress_unlocked = _read_progress_unlocked_achievements(steamworks)
        logger.debug(
            "游戏时长进度已更新: %ss -> %ss (+%ss); progressUnlocked=%s",
            current_playtime,
            new_playtime,
            seconds_to_add,
            progress_unlocked,
        )
        return JSONResponse(content={
            "success": True,
            "totalPlayTime": new_playtime,
            "added": seconds_to_add,
            "stat": _PLAYTIME_PROGRESS_STAT,
            "progressUnlocked": progress_unlocked,
        })
    except Exception as e:
        logger.error("更新游戏时长失败: %s", e)
        return JSONResponse(content={"success": False, "error": str(e)}, status_code=500)


@router.get('/steam/list-achievements')
async def list_achievements():
    """
    List all achievements configured in the Steam backend (for debugging).
    """
    steamworks = get_steamworks()
    if steamworks is not None:
        try:
            steamworks.UserStats.RequestCurrentStats()
            for _ in range(10):
                steamworks.run_callbacks()
                await asyncio.sleep(0.1)
            
            num_achievements = steamworks.UserStats.GetNumAchievements()
            achievements = []
            for i in range(num_achievements):
                name = steamworks.UserStats.GetAchievementName(i)
                if name:
                    # 如果是bytes类型，解码为字符串
                    if isinstance(name, bytes):
                        name = name.decode('utf-8')
                    status = steamworks.UserStats.GetAchievement(name)
                    achievements.append({"name": name, "unlocked": status})
            
            logger.info(f"Steam后台已配置 {num_achievements} 个成就: {achievements}")
            return JSONResponse(content={"count": num_achievements, "achievements": achievements})
        except Exception as e:
            logger.error(f"获取成就列表失败: {e}")
            return JSONResponse(content={"error": str(e)}, status_code=500)
    else:
        return JSONResponse(content={"error": "Steamworks未初始化"}, status_code=500)


# 辅助函数

def _read_binary_file(path: str) -> bytes:
    """Synchronous binary read, called via asyncio.to_thread."""
    with open(path, 'rb') as f:
        return f.read()


@router.get('/steam/proxy-image')
async def proxy_image(image_path: str):
    """
    Proxy access to local image files, supporting absolute and relative paths, notably the Steam Workshop directory.
    """

    try:
        logger.info(f"代理图片请求，原始路径: {image_path}")
        
        # 解码URL编码的路径（处理双重编码情况）
        decoded_path = unquote(image_path)
        # 再次解码以处理可能的双重编码
        decoded_path = unquote(decoded_path)
        
        logger.info(f"解码后的路径: {decoded_path}")
        
        # 检查是否是远程URL，如果是则直接返回错误（目前只支持本地文件）
        if decoded_path.startswith(('http://', 'https://')):
            return JSONResponse(content={"success": False, "error": "暂不支持远程图片URL"}, status_code=400)
        
        # 获取基础目录和允许访问的目录列表
        base_dir = _get_app_root()
        allowed_dirs = [
            os.path.realpath(os.path.join(base_dir, 'static')),
            os.path.realpath(os.path.join(base_dir, 'assets'))
        ]
        
        
        # 添加get_workshop_path()返回的路径作为允许目录，支持相对路径解析
        try:
            workshop_base_dir = os.path.abspath(os.path.normpath(get_workshop_path()))
            if os.path.exists(workshop_base_dir):
                real_workshop_dir = os.path.realpath(workshop_base_dir)
                if real_workshop_dir not in allowed_dirs:
                    allowed_dirs.append(real_workshop_dir)
                    logger.info(f"添加允许的默认创意工坊目录: {real_workshop_dir}")
        except Exception as e:
            logger.warning(f"无法添加默认创意工坊目录: {str(e)}")
        
        # 动态添加路径到允许列表：如果请求的路径包含创意工坊相关标识，则允许访问
        try:
            # 检查解码后的路径是否包含创意工坊相关路径标识
            if ('steamapps\\workshop' in decoded_path.lower() or 
                'steamapps/workshop' in decoded_path.lower()):
                
                # 获取创意工坊父目录
                workshop_related_dir = None
                
                # 方法1：如果路径存在，获取文件所在目录或直接使用目录路径
                if os.path.exists(decoded_path):
                    if os.path.isfile(decoded_path):
                        workshop_related_dir = os.path.dirname(decoded_path)
                    else:
                        workshop_related_dir = decoded_path
                
                # 方法2：尝试从路径中提取创意工坊相关部分
                if not workshop_related_dir:
                    match = re.search(r'(.*?steamapps[/\\]workshop)', decoded_path, re.IGNORECASE)
                    if match:
                        workshop_related_dir = match.group(1)
                
                # 方法3：如果是Steam创意工坊内容路径，获取content目录
                if not workshop_related_dir:
                    content_match = re.search(r'(.*?steamapps[/\\]workshop[/\\]content)', decoded_path, re.IGNORECASE)
                    if content_match:
                        workshop_related_dir = content_match.group(1)
                
                # 方法4：如果是Steam创意工坊内容路径，添加整个steamapps/workshop目录
                if not workshop_related_dir:
                    steamapps_match = re.search(r'(.*?steamapps)', decoded_path, re.IGNORECASE)
                    if steamapps_match:
                        workshop_related_dir = os.path.join(steamapps_match.group(1), 'workshop')
                
                # 如果找到了相关目录，添加到允许列表
                if workshop_related_dir:
                    # 确保目录存在
                    if os.path.exists(workshop_related_dir):
                        real_workshop_dir = os.path.realpath(workshop_related_dir)
                        if real_workshop_dir not in allowed_dirs:
                            allowed_dirs.append(real_workshop_dir)
                            logger.info(f"动态添加允许的创意工坊相关目录: {real_workshop_dir}")
                    else:
                        # 如果目录不存在，尝试直接添加steamapps/workshop路径
                        workshop_match = re.search(r'(.*?steamapps[/\\]workshop)', decoded_path, re.IGNORECASE)
                        if workshop_match:
                            potential_dir = workshop_match.group(0)
                            if os.path.exists(potential_dir):
                                real_workshop_dir = os.path.realpath(potential_dir)
                                if real_workshop_dir not in allowed_dirs:
                                    allowed_dirs.append(real_workshop_dir)
                                    logger.info(f"动态添加允许的创意工坊目录: {real_workshop_dir}")
        except Exception as e:
            logger.warning(f"动态添加创意工坊路径失败: {str(e)}")
        
        logger.info(f"当前允许的目录列表: {allowed_dirs}")

        # Windows路径处理：确保路径分隔符正确
        if os.name == 'nt':  # Windows系统
            # 替换可能的斜杠为反斜杠，确保Windows路径格式正确
            decoded_path = decoded_path.replace('/', '\\')
            # 处理可能的双重编码问题
            if decoded_path.startswith('\\\\'):
                decoded_path = decoded_path[2:]  # 移除多余的反斜杠前缀
        
        # 尝试解析路径
        final_path = None
        
        # 特殊处理：如果路径包含steamapps/workshop，直接检查文件是否存在
        if ('steamapps\\workshop' in decoded_path.lower() or 'steamapps/workshop' in decoded_path.lower()):
            if os.path.exists(decoded_path) and os.path.isfile(decoded_path):
                final_path = decoded_path
                logger.info(f"直接允许访问创意工坊文件: {final_path}")
        
        # 尝试作为绝对路径
        if final_path is None:
            if os.path.exists(decoded_path) and os.path.isfile(decoded_path):
                # 规范化路径以防止路径遍历攻击
                real_path = os.path.realpath(decoded_path)
                # 检查路径是否在允许的目录内 - 使用 commonpath 防止前缀攻击
                if any(_is_path_within_base(allowed_dir, real_path) for allowed_dir in allowed_dirs):
                    final_path = real_path
        
        # 尝试备选路径格式
        if final_path is None:
            alt_path = decoded_path.replace('\\', '/')
            if os.path.exists(alt_path) and os.path.isfile(alt_path):
                real_path = os.path.realpath(alt_path)
                # 使用 commonpath 防止前缀攻击
                if any(_is_path_within_base(allowed_dir, real_path) for allowed_dir in allowed_dirs):
                    final_path = real_path
        
        # 尝试相对路径处理 - 相对于static目录
        if final_path is None:
            # 对于以../static开头的相对路径，尝试直接从static目录解析
            if decoded_path.startswith('..\\static') or decoded_path.startswith('../static'):
                # 提取static后面的部分
                relative_part = decoded_path.split('static')[1]
                if relative_part.startswith(('\\', '/')):
                    relative_part = relative_part[1:]
                # 构建完整路径
                relative_path = os.path.join(allowed_dirs[0], relative_part)  # static目录
                if os.path.exists(relative_path) and os.path.isfile(relative_path):
                    real_path = os.path.realpath(relative_path)
                    # 使用 commonpath 防止前缀攻击
                    if any(_is_path_within_base(allowed_dir, real_path) for allowed_dir in allowed_dirs):
                        final_path = real_path
        
        # 尝试相对于默认创意工坊目录的路径处理
        if final_path is None:
            try:
                workshop_base_dir = os.path.abspath(os.path.normpath(get_workshop_path()))
                
                # 尝试将解码路径作为相对于创意工坊目录的路径
                rel_workshop_path = os.path.join(workshop_base_dir, decoded_path)
                rel_workshop_path = os.path.normpath(rel_workshop_path)
                
                logger.info(f"尝试相对于创意工坊目录的路径: {rel_workshop_path}")
                
                if os.path.exists(rel_workshop_path) and os.path.isfile(rel_workshop_path):
                    real_path = os.path.realpath(rel_workshop_path)
                    # 确保路径在允许的目录内 - 使用 commonpath 防止前缀攻击
                    if _is_path_within_base(workshop_base_dir, real_path):
                        final_path = real_path
                        logger.info(f"找到相对于创意工坊目录的图片: {final_path}")
            except Exception as e:
                logger.warning(f"处理相对于创意工坊目录的路径失败: {str(e)}")
        
        # 如果仍未找到有效路径，返回错误
        if final_path is None:
            return JSONResponse(content={"success": False, "error": f"文件不存在或无访问权限: {decoded_path}"}, status_code=404)
        
        # 检查文件扩展名是否为图片
        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
        if os.path.splitext(final_path)[1].lower() not in image_extensions:
            return JSONResponse(content={"success": False, "error": "不是有效的图片文件"}, status_code=400)
        
        # 检查文件大小是否超过50MB限制
        MAX_IMAGE_SIZE = 50 * 1024 * 1024  # 50MB
        file_size = await asyncio.to_thread(os.path.getsize, final_path)
        if file_size > MAX_IMAGE_SIZE:
            logger.warning(f"图片文件大小超过限制: {final_path} ({file_size / 1024 / 1024:.2f}MB > 50MB)")
            return JSONResponse(content={"success": False, "error": f"图片文件大小超过50MB限制 ({file_size / 1024 / 1024:.2f}MB)"}, status_code=413)

        # 读取图片文件 —— 最多 50MB，事件循环上同步 read 会卡几十毫秒
        image_data = await asyncio.to_thread(_read_binary_file, final_path)
        
        # 根据文件扩展名设置MIME类型
        ext = os.path.splitext(final_path)[1].lower()
        mime_type = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.webp': 'image/webp'
        }.get(ext, 'application/octet-stream')
        
        # 返回图片数据
        return Response(content=image_data, media_type=mime_type)
    except Exception as e:
        logger.error(f"代理图片访问失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": f"访问图片失败: {str(e)}"}, status_code=500)
