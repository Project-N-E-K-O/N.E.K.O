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

"""Steamworks UGC runtime: details cache, query batching, author/persona
resolution, warmup/background task handles, download requests and the
/subscribed-items endpoint (co-located with the rebindable task state).

Split out of the former monolithic ``main_routers/workshop_router.py``.
"""

from ._shared import logger, router
from .meta import _read_first_line
from .preview_cards import find_preview_image_in_folder
from .voice_manifest import _build_workshop_voice_reference_summary

import os
import json
import time
import asyncio
import threading
from datetime import datetime
from urllib.parse import quote
from fastapi.responses import JSONResponse
from ..shared_state import ensure_steamworks as get_steamworks
from utils.file_utils import read_json_async


# ─── UGC 查询结果缓存 ──────────────────────────────────────────────────
# Steam 的 k_UGCQueryHandleInvalid = 0xFFFFFFFFFFFFFFFF
_INVALID_UGC_QUERY_HANDLE = 0xFFFFFFFFFFFFFFFF


# 缓存 { publishedFileId(int): { title, description, ..., _cache_ts: float } }
# 每个条目带有独立的 _cache_ts 时间戳，用于按条目粒度判断 TTL
_ugc_details_cache: dict[int, dict] = {}


_UGC_CACHE_TTL = 300  # 缓存有效期 5 分钟


_ugc_warmup_task = None  # 后台预热任务


_ugc_sync_task = None    # 后台角色卡同步任务


# 全局互斥锁，用于序列化 UGC 批量查询（CreateQuery → SendQuery → 回调），
# 避免并发调用 override_callback=True 导致回调覆盖竞态
_ugc_query_lock = asyncio.Lock()


# ─── 创意工坊下载触发 ─────────────────────────────────────────────────
# SteamworksPy 包装库未导出 Workshop_DownloadItem，仅订阅不会触发 Steam
# 实际下载文件。我们通过 steamworks._native_ugc 桥到 libsteam_api 的
# SteamAPI_ISteamUGC_DownloadItem。这里只记录"已请求过下载"的物品集合，
# 避免每次列表刷新都重复打 INFO 日志；Steam 自己会去重。
_workshop_download_requested: set[int] = set()


# EItemState 位标志（与 steamworks/enums.py 的 EItemState 一致）
_ITEM_STATE_SUBSCRIBED = 1


_ITEM_STATE_INSTALLED = 4


_ITEM_STATE_NEEDS_UPDATE = 8


_ITEM_STATE_DOWNLOADING = 16


_ITEM_STATE_DOWNLOAD_PENDING = 32


class UnsupportedUGCDetailsError(RuntimeError):
    """Raised when the loaded Steamworks wrapper cannot query UGC item details."""


def _safe_get_workshop_install_folder(steamworks, item_id_int: int) -> str:
    """Safely read a subscribed item's install directory path.

    Consistent with the subscription list flow (``get_subscribed_workshop_items``):
    in the window where an item was just unsubscribed / its install directory was
    cleaned up by Steam, ``GetItemInstallInfo`` may raise ``FileNotFoundError`` /
    ``OSError``; degrade that to "not installed" instead of a 500, otherwise the
    frontend blows up randomly while polling download status.
    """
    if steamworks is None:
        return ''
    try:
        install_info = steamworks.Workshop.GetItemInstallInfo(item_id_int) or {}
    except (FileNotFoundError, OSError) as exc:
        logger.debug(f"GetItemInstallInfo({item_id_int}) 目录已不存在（可能刚取消订阅）: {exc}")
        return ''
    except Exception as exc:
        logger.warning(f"GetItemInstallInfo({item_id_int}) 失败: {exc}")
        return ''
    folder = install_info.get('folder') if isinstance(install_info, dict) else ''
    return folder if isinstance(folder, str) else ''


def _is_workshop_item_install_complete(item_state: int, installed_folder: str | None) -> bool:
    """Check whether a subscribed item is fully installed locally with no pending update.

    Both the INSTALLED bit of GetItemState and the installedFolder on disk must
    exist; Steam may still briefly report installed in the short window after
    unsubscribing, so the disk is authoritative.
    """
    if not installed_folder:
        return False
    try:
        if not os.path.isdir(installed_folder):
            return False
    except OSError:
        return False
    return bool(item_state & _ITEM_STATE_INSTALLED) and not bool(item_state & _ITEM_STATE_NEEDS_UPDATE)


def _request_workshop_item_download(
    steamworks,
    item_id: int,
    item_state: int,
    installed_folder: str | None = None,
    *,
    high_priority: bool = False,
) -> bool:
    """Trigger a Steam download on demand for subscribed items not yet installed / needing an update.

    The Steam client deduplicates and manages its own download queue, so repeated
    calls are safe. Returns True when a DownloadItem request was actually
    submitted to Steam this time.
    """
    if steamworks is None or item_id <= 0:
        return False
    # 仅订阅状态才允许下载；未订阅时 Steam 会拒绝。
    if not (item_state & _ITEM_STATE_SUBSCRIBED):
        return False
    if _is_workshop_item_install_complete(item_state, installed_folder):
        return False
    # 已经在下载或排队 → 不重复请求（除非显式 high_priority 提升优先级）。
    already_active = bool(item_state & (_ITEM_STATE_DOWNLOADING | _ITEM_STATE_DOWNLOAD_PENDING))
    if already_active and not high_priority:
        return False
    try:
        accepted = bool(steamworks.Workshop.DownloadItem(item_id, high_priority))
    except Exception as exc:
        logger.warning(
            f"触发创意工坊物品 {item_id} 下载失败: {exc}",
            exc_info=True,
        )
        return False
    if accepted:
        if item_id not in _workshop_download_requested:
            logger.info(
                "已向 Steam 请求下载创意工坊物品 %s (state=0x%x, high_priority=%s)",
                item_id, item_state, high_priority,
            )
            _workshop_download_requested.add(item_id)
        # 立即泵一次回调，让 Steam 尽快开始处理。
        try:
            steamworks.run_callbacks()
        except Exception:
            pass
    else:
        logger.warning(
            "Steam 拒绝了创意工坊物品 %s 的下载请求 (state=0x%x)",
            item_id, item_state,
        )
    return accepted


async def cancel_background_tasks(*, timeout: float = 5.0) -> None:
    for task_attr in ("_ugc_warmup_task", "_ugc_sync_task"):
        task = globals().get(task_attr)
        if task is None:
            continue
        if task.done():
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("workshop %s finished with error during cleanup: %s", task_attr, exc, exc_info=True)
        else:
            task.cancel()
            try:
                await asyncio.wait_for(task, timeout=timeout)
            except asyncio.CancelledError:
                current_task = asyncio.current_task()
                if current_task is not None and current_task.cancelling():
                    raise
                logger.debug("workshop %s cancelled", task_attr)
            except asyncio.TimeoutError:
                logger.warning("workshop %s did not stop within %.1fs", task_attr, timeout)
            except Exception as exc:
                logger.debug("workshop %s cleanup failed: %s", task_attr, exc, exc_info=True)
        if globals().get(task_attr) is task:
            globals()[task_attr] = None


def _is_item_cache_valid(item_id: int) -> bool:
    """Check whether a single UGC cache entry is still within its validity period."""
    entry = _ugc_details_cache.get(item_id)
    if not entry:
        return False
    return (time.time() - entry.get('_cache_ts', 0)) < _UGC_CACHE_TTL


def _all_items_cache_valid(item_ids: list[int]) -> bool:
    """Check whether the cache entries for all given item IDs are within their validity period."""
    if not _ugc_details_cache:
        return False
    return all(_is_item_cache_valid(iid) for iid in item_ids)


def _steamworks_method_unavailable(method) -> bool:
    return bool(getattr(method, '_neko_steamworks_unavailable', False))


def _ugc_details_query_supported(steamworks) -> bool:
    required_methods = (
        'Workshop_CreateQueryUGCDetailsRequest',
        'Workshop_SetQueryCompletedCallback',
        'Workshop_SendQueryUGCRequest',
        'Workshop_GetQueryUGCResult',
    )
    for method_name in required_methods:
        method = getattr(steamworks, method_name, None)
        if method is None or _steamworks_method_unavailable(method):
            return False
    return True


async def _query_ugc_details_batch(steamworks, item_ids: list[int], max_retries: int = 2) -> dict[int, object]:
    """
    Batch-query UGC item details, with retry logic.
    
    Args:
        steamworks: Steamworks instance
        item_ids: list of item IDs (integers)
        max_retries: maximum number of retries
    
    Returns:
        dict: { publishedFileId(int): SteamUGCDetails_t }
    """
    if not item_ids:
        return {}

    if not _ugc_details_query_supported(steamworks):
        logger.info(
            "UGC 批量详情查询不可用：当前 Steamworks wrapper 缺少 Linux UGC query 桥接，"
            "将保留订阅/安装目录扫描并跳过标题、作者等详情预热"
        )
        raise UnsupportedUGCDetailsError(
            "Steamworks wrapper does not expose UGC details query methods"
        )
    
    for attempt in range(max_retries):
        try:
            # 在发送查询前先泵一次回调，清除可能的残留状态
            try:
                steamworks.run_callbacks()
            except Exception as e:
                logger.debug(f"run_callbacks (pre-query pump) 异常: {e}")
            
            # 序列化整个查询流程：CreateQuery → SendQuery(override_callback) → 等待回调 → 读取结果
            # 避免并发调用时 override_callback=True 导致前一次的回调被覆盖
            async with _ugc_query_lock:
                query_handle = steamworks.Workshop.CreateQueryUGCDetailsRequest(item_ids)
                
                # 检查无效 handle（0 或 k_UGCQueryHandleInvalid）
                if not query_handle or query_handle == _INVALID_UGC_QUERY_HANDLE:
                    logger.warning(f"UGC 批量查询: CreateQueryUGCDetailsRequest 返回无效 handle "
                                  f"(attempt {attempt + 1}/{max_retries})")
                    if attempt < max_retries - 1:
                        await asyncio.sleep(1.5 * (attempt + 1))
                    continue
                
                # 回调+轮询机制（每次迭代创建独立的 Event 和 dict，通过默认参数绑定避免闭包晚绑定）
                query_completed = threading.Event()
                query_result_info = {"success": False, "num_results": 0}
                
                def _make_callback(_info=query_result_info, _event=query_completed):
                    def on_query_completed(result):
                        try:
                            _info["success"] = (result.result == 1)
                            _info["num_results"] = int(result.numResultsReturned)
                            logger.info(f"UGC 查询回调: result={result.result}, numResults={result.numResultsReturned}")
                        except Exception as e:
                            logger.warning(f"UGC 查询回调处理出错: {e}")
                        finally:
                            _event.set()
                    return on_query_completed
                
                steamworks.Workshop.SendQueryUGCRequest(
                    query_handle, callback=_make_callback(), override_callback=True
                )
                
                # 轮询等待（10ms 间隔，最多 15 秒）
                start_time = time.time()
                timeout = 15
                while time.time() - start_time < timeout:
                    if query_completed.is_set():
                        break
                    try:
                        steamworks.run_callbacks()
                    except Exception as e:
                        logger.debug(f"run_callbacks (polling) 异常: {e}")
                    await asyncio.sleep(0.01)
            
            if not query_completed.is_set():
                logger.warning(f"UGC 批量查询超时 (attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(2 * (attempt + 1))
                continue
            
            if not query_result_info["success"]:
                logger.warning(f"UGC 批量查询失败: result_info={query_result_info} "
                              f"(attempt {attempt + 1}/{max_retries})")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1.5 * (attempt + 1))
                continue
            
            # 提取结果
            num_results = query_result_info["num_results"]
            results = {}
            for i in range(num_results):
                try:
                    res = steamworks.Workshop.GetQueryUGCResult(query_handle, i)
                    if res and res.publishedFileId:
                        results[int(res.publishedFileId)] = res
                except Exception as e:
                    logger.warning(f"获取第 {i} 个 UGC 查询结果失败: {e}")
            
            logger.info(f"UGC 批量查询成功: {len(results)}/{len(item_ids)} 个物品 "
                        f"(attempt {attempt + 1})")
            
            # 查询完成后泵一次回调，让 Steam 缓存 persona 数据
            try:
                steamworks.run_callbacks()
            except Exception as e:
                logger.debug(f"run_callbacks (post-query pump) 异常: {e}")
            
            return results

        except Exception as e:
            logger.warning(f"UGC 批量查询异常: {e} (attempt {attempt + 1}/{max_retries})")
            if attempt < max_retries - 1:
                await asyncio.sleep(1.5 * (attempt + 1))
    
    logger.error("UGC 批量查询在所有重试后仍失败")
    return {}


# 本地 Steam 用户身份缓存：(steam_id, persona_name)，TTL 5 分钟
# 用于检测 GetFriendPersonaName 返回值是否被 fallback 成本地用户名。
_local_steam_identity_cache: tuple[int | None, str | None] | None = None


_local_steam_identity_cache_ts: float = 0.0


_LOCAL_IDENTITY_TTL = 300


# Steam Community 公开 XML 接口的 persona name 缓存
# { steam_id(int): (name_or_empty, _cache_ts) }
# 缓存值用空串表示「200 OK 但没解析出名字」的 negative-hit；
# 瞬时失败（超时 / 非 200 / 异常）不写入此缓存。
_persona_web_cache: dict[int, tuple[str, float]] = {}


_PERSONA_WEB_TTL = 3600


# Steam Community Web 兜底的并发上限。订阅一多就一次性 fan-out 容易把
# 自己打超时或被对端限流，限制并发到 8 个比较稳。
_PERSONA_WEB_CONCURRENCY = 8


# Web 兜底整轮的总耗时上限（秒）。Steam Community 慢 / 抖动时，几十个
# 非好友 owner × 5s 单请求 × 8 并发批次会让 /subscribed-items 阻塞几十
# 秒。这里给整轮 fan-out 设个硬墙：超时直接收割已完成的结果，剩下的
# task 全部 cancel，让接口尽快返回；没补回来的下次刷新会重试（因为
# transient failure 不写缓存）。
_PERSONA_WEB_TOTAL_DEADLINE = 8.0


def _get_local_steam_identity(steamworks) -> tuple[int | None, str | None]:
    """Get the local Steam user's (steam_id, persona_name), with a short-lived cache.

    When called on a Steam ID never requested via RequestUserInformation,
    Steamworks' GetFriendPersonaName may fall back to returning the local user's
    persona name — making every non-friend workshop entry display as the local
    user (typical symptom: every card uploaded by the developer shows up as the
    publisher account itself). Read out the local user info here so upstream can
    do forgery detection.
    """
    global _local_steam_identity_cache, _local_steam_identity_cache_ts
    if (
        _local_steam_identity_cache is not None
        and time.time() - _local_steam_identity_cache_ts < _LOCAL_IDENTITY_TTL
    ):
        return _local_steam_identity_cache
    local_id: int | None = None
    local_name: str | None = None
    try:
        raw_id = steamworks.Users.GetSteamID()
        local_id = int(raw_id) if raw_id else None
    except Exception as e:
        logger.debug(f"读取本地 Steam ID 失败: {e}")
    try:
        raw_name = steamworks.Friends.GetPlayerName()
        if isinstance(raw_name, bytes):
            raw_name = raw_name.decode('utf-8', errors='replace')
        local_name = (raw_name or '').strip() or None
    except Exception as e:
        logger.debug(f"读取本地 Steam persona name 失败: {e}")
    _local_steam_identity_cache = (local_id, local_name)
    _local_steam_identity_cache_ts = time.time()
    return _local_steam_identity_cache


def _resolve_author_name(steamworks, owner_id: int) -> str | None:
    """
    Resolve a Steam ID to a display name (synchronous path, relying on the Friends API only).

    For non-friend Steam IDs not warmed up via RequestUserInformation,
    Steamworks' GetFriendPersonaName may return "[unknown]" or — worse — the
    local user's persona name. The latter would make every Workshop entry
    display as the developer themselves. Hard-filter here; when None is
    returned, ``_fetch_persona_via_steam_web`` falls back to the Web API.

    Returns:
        str | None: user name, or None (resolution failed / judged forged)
    """
    if not owner_id:
        return None
    try:
        persona_name = steamworks.Friends.GetFriendPersonaName(owner_id)
    except Exception as e:
        logger.debug(f"解析 Steam ID {owner_id} 名称失败: {e}")
        return None
    if isinstance(persona_name, bytes):
        persona_name = persona_name.decode('utf-8', errors='replace')
    persona_name = (persona_name or '').strip()
    if not persona_name:
        return None
    # 占位符与纯数字 ID 串
    if persona_name == '[unknown]' or persona_name == str(owner_id):
        return None
    # 伪造检测：返回值等于本地 persona，但 owner_id 不是本地 Steam ID
    local_id, local_name = _get_local_steam_identity(steamworks)
    if local_name and persona_name == local_name and local_id and owner_id != local_id:
        logger.debug(
            f"忽略 owner_id={owner_id} 的伪造 persona '{persona_name}' "
            f"(等于本地用户 {local_id}/{local_name})"
        )
        return None
    return persona_name


async def _fetch_persona_via_steam_web(owner_id: int) -> str | None:
    """Fetch the persona name via the public steamcommunity.com XML endpoint.

    Fallback for when the Steamworks Friends API cannot resolve because
    RequestUserInformation was never run. The endpoint is accessible for every
    public profile, no API key needed; a 1-hour module-level cache avoids
    repeatedly requesting the same owner.

    Only deterministic results (HTTP 200 + full parse) are cached — cache the
    name when one is obtained; cache an empty string as a negative hit when a
    200 response has no name in the XML (private profile / deleted account);
    transient failures (timeout / non-200 / connection errors) are not cached,
    so one hiccup does not black-hole that owner's fallback path for an hour.

    Returns:
        str | None: persona name; transient failure / private profile / parse failure → None
    """
    if not owner_id:
        return None
    cached = _persona_web_cache.get(owner_id)
    if cached is not None and time.time() - cached[1] < _PERSONA_WEB_TTL:
        return cached[0] or None
    name: str | None = None
    cacheable = False
    try:
        import re as _re
        import httpx as _httpx
        async with _httpx.AsyncClient(timeout=5.0, follow_redirects=True) as client:
            resp = await client.get(
                f"https://steamcommunity.com/profiles/{owner_id}/",
                params={"xml": "1"},
                headers={"User-Agent": "Mozilla/5.0 N.E.K.O Workshop"},
            )
            if resp.status_code == 200:
                cacheable = True
                match = _re.search(
                    r"<steamID>\s*<!\[CDATA\[(.*?)\]\]>\s*</steamID>",
                    resp.text,
                    _re.DOTALL,
                )
                if match:
                    candidate = match.group(1).strip()
                    if candidate:
                        name = candidate
    except Exception as e:
        logger.debug(f"Steam Web 获取 persona name 失败 (owner_id={owner_id}): {e}")
    if cacheable:
        _persona_web_cache[owner_id] = (name or '', time.time())
    return name


async def _resolve_missing_author_names(items_info: list[dict]) -> None:
    """For entries in items_info missing authorName, backfill concurrently via the Web API fallback.

    Modifies items_info in place; also writes the resolved names back into
    ``_ugc_details_cache`` so the next list request does not fall onto the same
    fallback path again.
    """
    missing: list[tuple[dict, int]] = []
    for it in items_info:
        if it.get('authorName'):
            continue
        raw_owner = it.get('steamIDOwner') or ''
        try:
            owner_id = int(raw_owner) if raw_owner else 0
        except (TypeError, ValueError):
            owner_id = 0
        if owner_id:
            missing.append((it, owner_id))
    if not missing:
        return
    unique_owners = list({owner_id for _, owner_id in missing})
    semaphore = asyncio.Semaphore(_PERSONA_WEB_CONCURRENCY)

    async def _bounded(oid: int) -> tuple[int, str | None]:
        async with semaphore:
            try:
                return (oid, await _fetch_persona_via_steam_web(oid))
            except Exception:
                return (oid, None)

    tasks = [asyncio.create_task(_bounded(oid)) for oid in unique_owners]
    name_by_owner: dict[int, str] = {}
    try:
        done, pending = await asyncio.wait(
            tasks, timeout=_PERSONA_WEB_TOTAL_DEADLINE
        )
    except Exception as e:
        logger.debug(f"Web 兜底 wait 异常: {e}")
        done, pending = set(), set(tasks)
    if pending:
        for t in pending:
            t.cancel()
        # 把取消的 task 收割掉，避免 "Task was destroyed but it is pending!"
        await asyncio.gather(*pending, return_exceptions=True)
        logger.info(
            f"Web 兜底超过 {_PERSONA_WEB_TOTAL_DEADLINE}s 总预算，"
            f"已收割 {len(done)} 个、取消 {len(pending)} 个；剩余 owner 下次刷新重试"
        )
    for t in done:
        try:
            oid, name = t.result()
        except Exception:
            continue
        if name:
            name_by_owner[oid] = name
    if not name_by_owner:
        return
    for it, owner_id in missing:
        name = name_by_owner.get(owner_id)
        if not name:
            continue
        it['authorName'] = name
        try:
            item_id_int = int(it.get('publishedFileId') or 0)
        except (TypeError, ValueError):
            item_id_int = 0
        if item_id_int and item_id_int in _ugc_details_cache:
            _ugc_details_cache[item_id_int]['authorName'] = name


def _safe_text(value) -> str:
    """Convert bytes/str/None uniformly into a safe UTF-8 string."""
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode('utf-8', errors='replace')
    return str(value)


def _extract_ugc_item_details(steamworks, item_id_int: int, result, item_info: dict) -> None:
    """
    Extract item details from a UGC query result (SteamUGCDetails_t) into the item_info dict.
    Also updates the global cache (timestamps recorded at per-entry granularity).
    """
    global _ugc_details_cache
    
    try:
        if hasattr(result, 'title') and result.title:
            item_info['title'] = _safe_text(result.title)
        if hasattr(result, 'description') and result.description:
            item_info['description'] = _safe_text(result.description)
        # timeAddedToUserList 是用户订阅时间，timeCreated 是物品创建时间，分开存储避免语义混淆
        if hasattr(result, 'timeCreated') and result.timeCreated:
            item_info['timeCreated'] = int(result.timeCreated)
        if hasattr(result, 'timeAddedToUserList') and result.timeAddedToUserList:
            item_info['timeAdded'] = int(result.timeAddedToUserList)
        if hasattr(result, 'timeUpdated') and result.timeUpdated:
            item_info['timeUpdated'] = int(result.timeUpdated)
        if hasattr(result, 'steamIDOwner') and result.steamIDOwner:
            owner_id = int(result.steamIDOwner)
            item_info['steamIDOwner'] = str(owner_id)
            author_name = _resolve_author_name(steamworks, owner_id)
            if author_name:
                item_info['authorName'] = author_name
        if hasattr(result, 'fileSize') and result.fileSize:
            item_info['fileSizeOnDisk'] = int(result.fileSize)
        # 提取标签
        if hasattr(result, 'tags') and result.tags:
            try:
                tags_str = _safe_text(result.tags)
                if tags_str:
                    item_info['tags'] = [t.strip() for t in tags_str.split(',') if t.strip()]
            except Exception as e:
                logger.debug(f"解析 UGC 物品 {item_id_int} 标签失败: {e}")
        
        # 更新缓存
        cache_entry = {}
        for key in ('title', 'description', 'timeCreated', 'timeAdded', 'timeUpdated',
                     'steamIDOwner', 'authorName', 'tags'):
            if key in item_info:
                cache_entry[key] = item_info[key]
        if cache_entry:
            cache_entry['_cache_ts'] = time.time()
            _ugc_details_cache[item_id_int] = cache_entry
        
        logger.debug(f"提取物品 {item_id_int} 详情: title={item_info.get('title', '?')}")
    except Exception as detail_error:
        logger.warning(f"提取物品 {item_id_int} 详情时出错: {detail_error}")


async def warmup_ugc_cache() -> None:
    """
    Warm up the UGC cache in the background at server startup.
    
    Fetches all subscribed item IDs and runs one batch UGC query, storing the
    results in the cache. The frontend's first /subscribed-items request can then
    hit the cache directly without waiting on a Steam network query.
    """
    global _ugc_warmup_task
    
    steamworks = get_steamworks()
    if steamworks is None:
        return
    
    try:
        num_items = steamworks.Workshop.GetNumSubscribedItems()
        if num_items == 0:
            logger.info("UGC 缓存预热: 没有订阅物品，跳过")
            return
        
        subscribed_ids = steamworks.Workshop.GetSubscribedItems()
        all_item_ids = []
        for sid in subscribed_ids:
            try:
                all_item_ids.append(int(sid))
            except (ValueError, TypeError):
                continue
        
        if not all_item_ids:
            return
        
        logger.info(f"UGC 缓存预热: 开始查询 {len(all_item_ids)} 个物品...")
        try:
            ugc_results = await _query_ugc_details_batch(steamworks, all_item_ids, max_retries=3)
        except UnsupportedUGCDetailsError:
            logger.info("UGC 缓存预热: 当前平台不支持详情查询，跳过预热")
            return
        
        if ugc_results:
            # 将结果写入缓存
            for item_id_int, result in ugc_results.items():
                dummy_info = {"publishedFileId": str(item_id_int),
                              "title": f"未知物品_{item_id_int}", "description": ""}
                _extract_ugc_item_details(steamworks, item_id_int, result, dummy_info)
            
            logger.info(f"UGC 缓存预热完成: {len(_ugc_details_cache)} 个物品已缓存")
        else:
            logger.warning("UGC 缓存预热: 批量查询无结果")
    except Exception as e:
        logger.warning(f"UGC 缓存预热失败（不影响正常使用）: {e}")
    finally:
        _ugc_warmup_task = None


async def _get_subscribed_items_payload() -> dict:
    result = await get_subscribed_workshop_items()
    if isinstance(result, JSONResponse):
        try:
            return json.loads(result.body.decode('utf-8'))
        except Exception:
            return {'success': False, 'error': '无法解析订阅物品响应'}
    if isinstance(result, dict):
        return result
    return {'success': False, 'error': '获取订阅物品响应异常'}


async def _find_subscribed_item_by_id(item_id: str) -> dict | None:
    payload = await _get_subscribed_items_payload()
    if not payload.get('success'):
        error = payload.get('error') or '获取订阅物品失败'
        raise RuntimeError(error)

    for item in payload.get('items', []):
        if str(item.get('publishedFileId')) == str(item_id):
            return item
    return None


@router.get('/subscribed-items')
async def get_subscribed_workshop_items():
    """
    Get the list of the user's subscribed Steam Workshop items.
    Returns JSON containing item IDs, basic info and status.
    """
    steamworks = get_steamworks()
    
    # 检查Steamworks是否初始化成功
    if steamworks is None:
        return JSONResponse({
            "success": False,
            "error": "Steamworks未初始化",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)
    
    try:
        # 获取订阅物品数量
        num_subscribed_items = steamworks.Workshop.GetNumSubscribedItems()
        
        # 如果没有订阅物品，返回空列表
        if num_subscribed_items == 0:
            return {
                "success": True,
                "items": [],
                "total": 0
            }
        
        # 获取订阅物品ID列表
        subscribed_items = steamworks.Workshop.GetSubscribedItems()
        
        # 存储处理后的物品信息
        items_info = []
        
        # 批量查询所有物品的详情（带重试+缓存）
        ugc_results = {}
        try:
            # 转换所有ID为整数
            all_item_ids = []
            for sid in subscribed_items:
                try:
                    all_item_ids.append(int(sid))
                except (ValueError, TypeError):
                    continue
            
            if all_item_ids:
                # 优先使用缓存（如果所有条目都存在且各自在有效期内）
                if _all_items_cache_valid(all_item_ids):
                    logger.debug(f"使用 UGC 缓存（{len(all_item_ids)} 个物品）")
                elif _ugc_warmup_task is not None and not _ugc_warmup_task.done():
                    # 预热任务仍在运行，等待它完成而非发起重复查询
                    logger.info("等待 UGC 缓存预热任务完成...")
                    try:
                        await asyncio.wait_for(asyncio.shield(_ugc_warmup_task), timeout=20)
                    except asyncio.TimeoutError:
                        logger.info("等待 UGC 缓存预热超时（20s），将回退到直接查询")
                    except Exception as e:
                        logger.warning(f"UGC 缓存预热任务异常: {e}", exc_info=True)
                    # 预热完成后按条目粒度检查缓存
                    if not _all_items_cache_valid(all_item_ids):
                        logger.info(f'预热后缓存不完整，重新批量查询 {len(all_item_ids)} 个物品')
                        ugc_results = await _query_ugc_details_batch(steamworks, all_item_ids, max_retries=2)
                else:
                    logger.info(f'批量查询 {len(all_item_ids)} 个物品的详细信息')
                    ugc_results = await _query_ugc_details_batch(steamworks, all_item_ids, max_retries=2)
        except UnsupportedUGCDetailsError:
            logger.info("UGC 详情查询不可用，订阅列表将使用安装目录/默认信息降级返回")
        except Exception as batch_error:
            logger.warning(f"批量查询物品详情失败: {batch_error}")
        
        # 为每个物品获取基本信息和状态
        for item_id in subscribed_items:
            try:
                # 确保item_id是整数类型
                if isinstance(item_id, str):
                    try:
                        item_id = int(item_id)
                    except ValueError:
                        logger.error(f"无效的物品ID: {item_id}")
                        continue
                
                logger.debug(f'正在处理物品ID: {item_id}')
                
                # 获取物品状态
                item_state = steamworks.Workshop.GetItemState(item_id)
                logger.debug(f'物品 {item_id} 状态: {item_state}')
                
                # 初始化基本物品信息（确保所有字段都有默认值）
                # 确保publishedFileId始终为字符串类型，避免前端toString()错误
                item_info = {
                    "publishedFileId": str(item_id),
                    "title": f"未知物品_{item_id}",
                    "description": "无法获取详细描述",
                    "tags": [],
                    "state": {
                        "subscribed": bool(item_state & 1),  # EItemState.SUBSCRIBED
                        "legacyItem": bool(item_state & 2),
                        "installed": False,
                        "needsUpdate": bool(item_state & 8),  # EItemState.NEEDS_UPDATE
                        "downloading": False,
                        "downloadPending": bool(item_state & 32),  # EItemState.DOWNLOAD_PENDING
                        "isWorkshopItem": bool(item_state & 128)  # EItemState.IS_WORKSHOP_ITEM
                    },
                    "installedFolder": None,
                    "fileSizeOnDisk": 0,
                    "downloadProgress": {
                        "bytesDownloaded": 0,
                        "bytesTotal": 0,
                        "percentage": 0
                    },
                    # 添加额外的时间戳信息 - 使用datetime替代time模块避免命名冲突
                    "timeAdded": int(datetime.now().timestamp()),
                    "timeUpdated": int(datetime.now().timestamp())
                }
                
                # 尝试获取物品安装信息（如果已安装）
                try:
                    logger.debug(f'获取物品 {item_id} 的安装信息')
                    result = steamworks.Workshop.GetItemInstallInfo(item_id)
                    
                    # 检查返回值的结构 - 支持字典格式（根据日志显示）
                    # GetItemInstallInfo 即使在物品已被退订后仍可能短暂返回成功，
                    # 必须用 os.path.isdir(folder) 二次确认目录仍存在才能标记
                    # installed=True，否则前端会展示"已安装但目录不存在"的幽灵态。
                    if result and isinstance(result, dict):
                        logger.debug(f'物品 {item_id} 安装信息字典: {result}')

                        raw_folder = result.get('folder', '')
                        folder_path = str(raw_folder) if raw_folder else ''
                        if folder_path and os.path.isdir(folder_path):
                            item_info["state"]["installed"] = True
                            item_info["installedFolder"] = folder_path
                            disk_size = result.get('disk_size', 0)
                            item_info["fileSizeOnDisk"] = (
                                int(disk_size) if isinstance(disk_size, (int, float)) else 0
                            )
                        else:
                            item_info["state"]["installed"] = False
                            item_info["installedFolder"] = None
                            item_info["fileSizeOnDisk"] = 0
                            logger.debug(
                                f'物品 {item_id} Steam 报告已安装但安装目录不存在，'
                                f'按未安装处理: {folder_path!r}'
                            )
                        logger.debug(f'物品 {item_id} 的安装路径: {item_info["installedFolder"]}')
                    # 也支持元组格式作为备选
                    elif isinstance(result, tuple) and len(result) >= 3:
                        installed, folder, size = result
                        logger.debug(f'物品 {item_id} 安装状态: 已安装={installed}, 路径={folder}, 大小={size}')

                        folder_str = (
                            str(folder) if folder and isinstance(folder, (str, bytes)) else ''
                        )
                        folder_ok = bool(folder_str) and os.path.isdir(folder_str)
                        item_info["state"]["installed"] = bool(installed) and folder_ok
                        item_info["installedFolder"] = folder_str if item_info["state"]["installed"] else None

                        if item_info["state"]["installed"] and isinstance(size, (int, float)):
                            item_info["fileSizeOnDisk"] = int(size)
                        else:
                            item_info["fileSizeOnDisk"] = 0
                    else:
                        logger.warning(f'物品 {item_id} 的安装信息返回格式未知: {type(result)} - {result}')
                        item_info["state"]["installed"] = False
                except (FileNotFoundError, OSError) as e:
                    # 取消订阅后的短窗内 Steam 仍可能返回该 item，但本地 install
                    # folder 已被删 → 预期的 race，降级为 debug 避免日志噪音。
                    logger.debug(f'获取物品 {item_id} 安装信息失败（可能刚取消订阅）: {e}')
                    item_info["state"]["installed"] = False
                except Exception as e:
                    logger.warning(f'获取物品 {item_id} 安装信息失败: {e}')
                    item_info["state"]["installed"] = False
                
                # 尝试获取物品下载信息（如果正在下载）
                try:
                    logger.debug(f'获取物品 {item_id} 的下载信息')
                    result = steamworks.Workshop.GetItemDownloadInfo(item_id)
                    
                    # 检查返回值的结构 - 支持字典格式（与安装信息保持一致）
                    if isinstance(result, dict):
                        logger.debug(f'物品 {item_id} 下载信息字典: {result}')
                        
                        # 使用正确的键名获取下载信息
                        downloaded = result.get('downloaded', 0)
                        total = result.get('total', 0)
                        progress = result.get('progress', 0.0)
                        
                        # 根据total和downloaded确定是否正在下载
                        item_info["state"]["downloading"] = total > 0 and downloaded < total
                        
                        # 设置下载进度信息
                        if downloaded > 0 or total > 0:
                            item_info["downloadProgress"] = {
                                "bytesDownloaded": int(downloaded),
                                "bytesTotal": int(total),
                                "percentage": progress * 100 if isinstance(progress, (int, float)) else 0
                            }
                    # 也支持元组格式作为备选
                    elif isinstance(result, tuple) and len(result) >= 3:
                        # 元组中应该包含下载状态、已下载字节数和总字节数
                        downloaded, total, progress = result if len(result) >= 3 else (0, 0, 0.0)
                        logger.debug(f'物品 {item_id} 下载状态: 已下载={downloaded}, 总计={total}, 进度={progress}')
                        
                        # 根据total和downloaded确定是否正在下载
                        item_info["state"]["downloading"] = total > 0 and downloaded < total
                        
                        # 设置下载进度信息
                        if downloaded > 0 or total > 0:
                            # 处理可能的类型转换
                            try:
                                downloaded_value = int(downloaded.value) if hasattr(downloaded, 'value') else int(downloaded)
                                total_value = int(total.value) if hasattr(total, 'value') else int(total)
                                progress_value = float(progress.value) if hasattr(progress, 'value') else float(progress)
                            except: # noqa
                                downloaded_value, total_value, progress_value = 0, 0, 0.0
                                
                            item_info["downloadProgress"] = {
                                "bytesDownloaded": downloaded_value,
                                "bytesTotal": total_value,
                                "percentage": progress_value * 100
                            }
                    else:
                        logger.warning(f'物品 {item_id} 的下载信息返回格式未知: {type(result)} - {result}')
                        item_info["state"]["downloading"] = False
                except Exception as e:
                    logger.warning(f'获取物品 {item_id} 下载信息失败: {e}')
                    item_info["state"]["downloading"] = False
                
                # 从批量查询结果或缓存中提取物品详情
                item_id_int = int(item_id)
                if item_id_int in ugc_results:
                    _extract_ugc_item_details(steamworks, item_id_int, ugc_results[item_id_int], item_info)
                elif _is_item_cache_valid(item_id_int):
                    # 使用缓存数据填充（仅在该条目 TTL 有效时）
                    cached = _ugc_details_cache[item_id_int]
                    for key in ('title', 'description', 'timeCreated', 'timeAdded', 'timeUpdated',
                                'steamIDOwner', 'authorName', 'tags'):
                        if key in cached:
                            item_info[key] = cached[key]
                    logger.debug(f"从缓存填充物品 {item_id} 详情: title={item_info.get('title', '?')}")
                
                # 作为备选方案，如果本地有安装路径，尝试从本地文件获取信息
                if item_info['title'].startswith('未知物品_') or not item_info['description']:
                    install_folder = item_info.get('installedFolder')
                    if install_folder and os.path.exists(install_folder):
                        logger.debug(f'尝试从安装文件夹获取物品信息: {install_folder}')
                        # 查找可能的配置文件来获取更多信息
                        config_files = [
                            os.path.join(install_folder, "config.json"),
                            os.path.join(install_folder, "package.json"),
                            os.path.join(install_folder, "info.json"),
                            os.path.join(install_folder, "manifest.json"),
                            os.path.join(install_folder, "README.md"),
                            os.path.join(install_folder, "README.txt")
                        ]
                        
                        for config_path in config_files:
                            if os.path.exists(config_path):
                                try:
                                    if config_path.endswith('.json'):
                                        config_data = await read_json_async(config_path)
                                        # 尝试从配置文件中提取标题和描述
                                        if "title" in config_data and config_data["title"]:
                                            item_info["title"] = config_data["title"]
                                        elif "name" in config_data and config_data["name"]:
                                            item_info["title"] = config_data["name"]
                                        # description 作为 title/name 的同级分支，不应嵌在 elif name 下
                                        if "description" in config_data and config_data["description"]:
                                            item_info["description"] = config_data["description"]
                                    else:
                                        # README.md / README.txt：把首行当标题（offload sync IO）
                                        first_line = (await asyncio.to_thread(_read_first_line, config_path)).strip()
                                        if first_line and item_info['title'].startswith('未知物品_'):
                                            item_info['title'] = first_line[:100]  # 限制长度
                                    logger.debug(f"从本地文件 {os.path.basename(config_path)} 成功获取物品 {item_id} 的信息")
                                    break
                                except Exception as file_error:
                                    logger.warning(f"读取配置文件 {config_path} 时出错: {file_error}")
                # 移除了没有对应try块的except语句
                
                # 确保publishedFileId是字符串类型
                item_info['publishedFileId'] = str(item_info['publishedFileId'])
                
                # 尝试获取预览图信息 - 优先从本地文件夹查找
                # 多道防御：先用 isdir 双重检查（比 exists 更明确排除"存在但不是目录"），
                # 再吞 FileNotFoundError（取消订阅后遍历期间目录被删的 race）。
                preview_url = None
                install_folder = item_info.get('installedFolder')
                if install_folder and os.path.isdir(install_folder):
                    try:
                        # 使用辅助函数查找预览图
                        preview_image_path = find_preview_image_in_folder(install_folder)
                        if preview_image_path:
                            # 为前端提供代理访问的路径格式
                            # 需要将路径标准化，确保可以通过proxy-image API访问
                            if os.name == 'nt':
                                # Windows路径处理
                                proxy_path = preview_image_path.replace('\\', '/')
                            else:
                                proxy_path = preview_image_path
                            preview_url = f"/api/steam/proxy-image?image_path={quote(proxy_path)}"
                            logger.debug(f'为物品 {item_id} 找到本地预览图: {preview_url}')
                    except (FileNotFoundError, OSError) as preview_error:
                        logger.debug(
                            f'查找物品 {item_id} 预览图时目录已消失（可能刚取消订阅）: {preview_error}'
                        )
                    except Exception as preview_error:
                        logger.warning(f'查找物品 {item_id} 预览图时出错: {preview_error}')
                
                # 添加预览图URL到物品信息
                if preview_url:
                    item_info['previewUrl'] = preview_url

                # 若该订阅物品尚未安装（或需要更新），主动触发 Steam 下载。
                # 这是修复"订阅后模型列表显示但点击无法切换"的关键：
                # SteamworksPy 未导出 DownloadItem，仅订阅不会让 Steam 下载文件。
                try:
                    _request_workshop_item_download(
                        steamworks,
                        int(item_id),
                        int(item_state),
                        item_info.get("installedFolder"),
                    )
                except Exception as kick_err:
                    logger.debug(
                        f"物品 {item_id} 自动触发下载时出错（忽略）: {kick_err}"
                    )

                voice_reference_summary = None
                if install_folder and os.path.isdir(install_folder):
                    try:
                        voice_reference_summary = await asyncio.to_thread(
                            _build_workshop_voice_reference_summary,
                            install_folder,
                        )
                    except (FileNotFoundError, OSError) as voice_error:
                        logger.debug(
                            f'构建物品 {item_id} voice reference 时目录已消失（可能刚取消订阅）: {voice_error}'
                        )
                    except Exception as voice_error:
                        logger.warning(f'构建物品 {item_id} voice reference 失败: {voice_error}')
                item_info['voiceReferenceAvailable'] = bool(voice_reference_summary)
                if voice_reference_summary:
                    item_info['voiceReference'] = voice_reference_summary
                
                # 添加物品信息到结果列表
                items_info.append(item_info)
                logger.debug(f'物品 {item_id} 信息已添加到结果列表: {item_info["title"]}')
                
            except Exception as item_error:
                logger.error(f"获取物品 {item_id} 信息时出错: {item_error}")
                # 即使出错，也添加一个最基本的物品信息到列表中
                try:
                    basic_item_info = {
                        "publishedFileId": str(item_id),  # 确保是字符串类型
                        "title": f"未知物品_{item_id}",
                        "description": "无法获取详细信息",
                        "state": {
                            "subscribed": True,
                            "installed": False,
                            "downloading": False,
                            "needsUpdate": False,
                            "error": True
                        },
                        "error_message": str(item_error)
                    }
                    items_info.append(basic_item_info)
                    logger.debug(f'已添加物品 {item_id} 的基本信息到结果列表')
                except Exception as basic_error:
                    logger.error(f"添加基本物品信息也失败了: {basic_error}")
                # 继续处理下一个物品
                continue

        # 对于 Friends API 没能解析出 authorName 的物品（典型是
        # GetFriendPersonaName 把非好友 owner 误回成本地用户名，被
        # _resolve_author_name 判伪丢弃），走 Steam Community 公开 XML
        # 接口兜底，并发查询并写回 items / 缓存。
        try:
            await _resolve_missing_author_names(items_info)
        except Exception as fallback_err:
            logger.debug(f"Web API 补全 authorName 时出错（忽略）: {fallback_err}")

        return {
            "success": True,
            "items": items_info,
            "total": len(items_info)
        }

    except Exception as e:
        logger.error(f"获取订阅物品列表时出错: {e}")
        return JSONResponse({
            "success": False,
            "error": f"获取订阅物品失败: {str(e)}"
        }, status_code=500)
