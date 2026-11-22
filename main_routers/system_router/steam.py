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
import threading
from typing import Any
from urllib.parse import unquote
from fastapi import Request
from fastapi.responses import JSONResponse, Response
from ..shared_state import ensure_steamworks as get_steamworks
from ..shared_state import get_steamworks as get_cached_steamworks
from utils.workshop_utils import get_workshop_library_roots, get_workshop_path_async


# Progress Stat for timed achievements. Steamworks Partner must bind
# ACH_TIME_* achievements to this stat with matching thresholds; Steam unlocks
# them automatically when StoreStats syncs a value past the bound threshold.
_PLAYTIME_PROGRESS_STAT = "PLAY_TIME_SECONDS"
_PLAYTIME_PROGRESS_ACHIEVEMENTS: tuple[str, ...] = (
    "ACH_TIME_5MIN",
    "ACH_TIME_1HR",
    "ACH_TIME_100HR",
)


# Held while a background Steamworks reconnect runs, so concurrent playtime
# reports start at most one.
_steam_reconnect_guard = threading.Lock()


def _run_steam_reconnect() -> None:
    try:
        # get_steamworks is ensure_steamworks here: it may run the initializer
        # and keeps its own min-retry interval.
        get_steamworks()
    except Exception as e:
        logger.warning("后台重连 Steamworks 失败: %s", e)
    finally:
        _steam_reconnect_guard.release()


def _kick_steam_reconnect() -> None:
    """Retry Steamworks init off the request path, at most one at a time.

    Supports "start N.E.K.O first, open Steam later" without running
    SteamAPI_Init on the event loop. The thread is a daemon so a hung SDK call
    cannot block process exit.
    """
    if not _steam_reconnect_guard.acquire(blocking=False):
        return
    try:
        threading.Thread(target=_run_steam_reconnect, name="steam-reconnect", daemon=True).start()
    except Exception as e:
        _steam_reconnect_guard.release()
        logger.warning("启动 Steamworks 后台重连线程失败: %s", e)


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

    steamworks = get_cached_steamworks()
    if steamworks is None:
        _kick_steam_reconnect()
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
                    # Decode bytes to a string
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


# Helper functions

def _read_binary_file(path: str) -> bytes:
    """Synchronous binary read, called via asyncio.to_thread."""
    with open(path, 'rb') as f:
        return f.read()


@router.get('/steam/proxy-image')
async def proxy_image(image_path: str):
    """Proxy access to local image files within trusted static, assets, and Steam Workshop roots."""
    try:
        if not image_path:
            return JSONResponse(content={"success": False, "error": "无效的图片路径"}, status_code=400)

        decoded_path = unquote(image_path)
        if not decoded_path or "\x00" in decoded_path:
            return JSONResponse(content={"success": False, "error": "无效的图片路径"}, status_code=400)

        if decoded_path.startswith(('http://', 'https://')):
            return JSONResponse(content={"success": False, "error": "暂不支持远程图片URL"}, status_code=400)

        if decoded_path.startswith(('\\\\', '//')):
            return JSONResponse(content={"success": False, "error": "文件不存在或无访问权限"}, status_code=404)

        base_dir = _get_app_root()
        static_dir = os.path.realpath(os.path.join(base_dir, 'static'))
        assets_dir = os.path.realpath(os.path.join(base_dir, 'assets'))
        allowed_dirs = [static_dir, assets_dir]

        real_workshop_dir: str | None = None
        workshop_roots: list[str] = []
        try:
            workshop_raw = await get_workshop_path_async()
            if workshop_raw:
                workshop_candidate = os.path.abspath(os.path.normpath(workshop_raw))
                if os.path.exists(workshop_candidate):
                    real_workshop_dir = os.path.realpath(workshop_candidate)
                    workshop_roots.append(real_workshop_dir)
                    if real_workshop_dir not in allowed_dirs:
                        allowed_dirs.append(real_workshop_dir)
        except Exception as e:
            logger.warning(f"无法添加默认创意工坊目录: {str(e)}")

        try:
            for ws_root in get_workshop_library_roots() or ():
                if ws_root:
                    ws_candidate = os.path.abspath(os.path.normpath(ws_root))
                    if os.path.exists(ws_candidate):
                        real_ws_root = os.path.realpath(ws_candidate)
                        if real_ws_root not in workshop_roots:
                            workshop_roots.append(real_ws_root)
                        if real_ws_root not in allowed_dirs:
                            allowed_dirs.append(real_ws_root)
        except Exception as e:
            logger.warning(f"无法添加额外创意工坊库目录: {str(e)}")

        final_path: str | None = None

        if decoded_path.startswith('../static/') or decoded_path.startswith('..\\static\\'):
            relative_part = decoded_path[len('../static/'):]
            if relative_part and '..' not in re.split(r"[/\\]", relative_part):
                clean_rel = relative_part.replace('/', os.sep).replace('\\', os.sep)
                candidate = os.path.realpath(os.path.join(static_dir, clean_rel))
                if _is_path_within_base(static_dir, candidate) and os.path.exists(candidate) and os.path.isfile(candidate):
                    final_path = candidate
        else:
            if '..' in re.split(r"[/\\]", decoded_path):
                return JSONResponse(content={"success": False, "error": "文件不存在或无访问权限"}, status_code=404)

            normalized_input = decoded_path.replace('/', '\\') if os.name == 'nt' else decoded_path
            candidate_inputs = [normalized_input]
            alt_input = decoded_path.replace('\\', '/')
            if alt_input not in candidate_inputs:
                candidate_inputs.append(alt_input)

            for candidate_raw in candidate_inputs:
                if os.path.isabs(candidate_raw):
                    real_path = os.path.realpath(candidate_raw)
                    if (
                        any(_is_path_within_base(allowed_dir, real_path) for allowed_dir in allowed_dirs)
                        and os.path.exists(real_path)
                        and os.path.isfile(real_path)
                    ):
                        final_path = real_path
                        break

            if final_path is None and workshop_roots and not os.path.isabs(normalized_input):
                for ws_root in workshop_roots:
                    rel_workshop = os.path.join(ws_root, normalized_input)
                    real_path = os.path.realpath(rel_workshop)
                    if (
                        _is_path_within_base(ws_root, real_path)
                        and os.path.exists(real_path)
                        and os.path.isfile(real_path)
                    ):
                        final_path = real_path
                        break

        if final_path is None:
            return JSONResponse(content={"success": False, "error": "文件不存在或无访问权限"}, status_code=404)

        image_extensions = ['.jpg', '.jpeg', '.png', '.gif', '.bmp', '.webp']
        if os.path.splitext(final_path)[1].lower() not in image_extensions:
            return JSONResponse(content={"success": False, "error": "不是有效的图片文件"}, status_code=400)

        MAX_IMAGE_SIZE = 50 * 1024 * 1024  # 50MB
        file_size = await asyncio.to_thread(os.path.getsize, final_path)
        if file_size > MAX_IMAGE_SIZE:
            logger.warning(f"图片文件大小超过限制: {file_size / 1024 / 1024:.2f}MB > 50MB")
            return JSONResponse(content={"success": False, "error": f"图片文件大小超过50MB限制 ({file_size / 1024 / 1024:.2f}MB)"}, status_code=413)

        image_data = await asyncio.to_thread(_read_binary_file, final_path)

        ext = os.path.splitext(final_path)[1].lower()
        mime_type = {
            '.jpg': 'image/jpeg',
            '.jpeg': 'image/jpeg',
            '.png': 'image/png',
            '.gif': 'image/gif',
            '.bmp': 'image/bmp',
            '.webp': 'image/webp'
        }.get(ext, 'application/octet-stream')

        return Response(content=image_data, media_type=mime_type)
    except Exception as e:
        logger.error(f"代理图片访问失败: {str(e)}")
        return JSONResponse(content={"success": False, "error": "访问图片失败"}, status_code=500)
