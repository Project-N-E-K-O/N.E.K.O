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

"""Per-item endpoints: status, download, download-status, path and
details.

Split out of the former monolithic ``main_routers/workshop_router.py``.
"""

from ._shared import logger, router
from .ugc import (
    UnsupportedUGCDetailsError,
    _ITEM_STATE_DOWNLOADING,
    _ITEM_STATE_DOWNLOAD_PENDING,
    _ITEM_STATE_INSTALLED,
    _ITEM_STATE_NEEDS_UPDATE,
    _ITEM_STATE_SUBSCRIBED,
    _extract_ugc_item_details,
    _is_item_cache_valid,
    _is_workshop_item_install_complete,
    _query_ugc_details_batch,
    _request_workshop_item_download,
    _resolve_author_name,
    _resolve_missing_author_names,
    _safe_get_workshop_install_folder,
    _ugc_details_cache,
)

import os
import time
import asyncio
from fastapi import Request
from fastapi.responses import JSONResponse
from ..shared_state import ensure_steamworks as get_steamworks


@router.get('/status')
async def get_steam_status():
    """Check whether Steamworks is initialized; used by the frontend at page load to determine Steam status."""
    steamworks = get_steamworks()
    return JSONResponse({
        "success": True,
        "steamworks_initialized": steamworks is not None
    })


@router.post('/item/{item_id}/download')
async def trigger_workshop_item_download(item_id: str, request: Request):
    """Proactively trigger a Steam download of the specified subscribed item.

    Body (optional JSON)::
        {
            "high_priority": false,  # raise the download priority
            "wait": false,           # wait for the download to finish (synchronous)
            "timeout": 60            # wait seconds when wait=True (default 60, max 600)
        }

    With ``wait=True`` the endpoint polls ``GetItemState`` / ``GetItemInstallInfo``
    until the item finishes installing or the timeout hits; the frontend can call
    it once before navigating to a workshop model to make sure the files really
    exist on disk. With ``wait=False`` it returns immediately and the frontend polls on its own.
    """
    steamworks = get_steamworks()
    if steamworks is None:
        return JSONResponse({
            "success": False,
            "error": "Steamworks未初始化",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)

    try:
        item_id_int = int(item_id)
    except (TypeError, ValueError):
        return JSONResponse({
            "success": False,
            "error": "无效的物品ID",
            "message": "物品ID必须是有效的数字"
        }, status_code=400)

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    high_priority = bool(body.get('high_priority') or body.get('highPriority') or False)
    should_wait = bool(body.get('wait', False))
    try:
        timeout_seconds = float(body.get('timeout', 60))
    except (TypeError, ValueError):
        timeout_seconds = 60.0
    timeout_seconds = max(1.0, min(timeout_seconds, 600.0))

    try:
        item_state = int(steamworks.Workshop.GetItemState(item_id_int))
    except Exception as exc:
        logger.warning(f"获取物品 {item_id_int} 状态失败: {exc}")
        item_state = 0

    if not (item_state & _ITEM_STATE_SUBSCRIBED):
        return JSONResponse({
            "success": False,
            "error": "未订阅",
            "message": f"物品 {item_id} 当前未被订阅，无法触发下载",
            "state": item_state,
        }, status_code=409)

    # Already installed without an update -> return success directly instead of misleading the frontend with downloading status.
    folder = _safe_get_workshop_install_folder(steamworks, item_id_int)
    if _is_workshop_item_install_complete(item_state, folder):
        return {
            "success": True,
            "item_id": str(item_id_int),
            "already_installed": True,
            "installed": True,
            "installedFolder": folder or None,
            "state": item_state,
        }

    accepted = await asyncio.to_thread(
        _request_workshop_item_download,
        steamworks,
        item_id_int,
        item_state,
        folder or None,
        high_priority=high_priority,
    )

    if not accepted and not (item_state & (_ITEM_STATE_DOWNLOADING | _ITEM_STATE_DOWNLOAD_PENDING)):
        # Reread state; another flow may have started it after
        # _is_workshop_item_install_complete. Report failure only when genuinely neither accepted nor queued.
        try:
            item_state = int(steamworks.Workshop.GetItemState(item_id_int))
        except Exception:
            pass
        if not (item_state & (_ITEM_STATE_DOWNLOADING | _ITEM_STATE_DOWNLOAD_PENDING | _ITEM_STATE_INSTALLED)):
            return JSONResponse({
                "success": False,
                "error": "Steam 拒绝下载请求",
                "message": "Steam 客户端未接受 DownloadItem，请检查 Steam 是否在线、是否已正确订阅",
                "state": item_state,
            }, status_code=502)

    if not should_wait:
        # Immediately return the latest progress.
        try:
            download_info = steamworks.Workshop.GetItemDownloadInfo(item_id_int) or {}
        except Exception:
            download_info = {}
        downloaded = int(download_info.get('downloaded', 0) or 0) if isinstance(download_info, dict) else 0
        total = int(download_info.get('total', 0) or 0) if isinstance(download_info, dict) else 0
        return {
            "success": True,
            "item_id": str(item_id_int),
            "requested": True,
            "installed": False,
            "state": item_state,
            "bytesDownloaded": downloaded,
            "bytesTotal": total,
        }

    # wait=True: poll until installation completes or times out.
    start_time = time.monotonic()
    poll_interval = 0.5
    last_state = item_state
    last_folder: str | None = None
    while time.monotonic() - start_time < timeout_seconds:
        try:
            steamworks.run_callbacks()
        except Exception:
            pass
        try:
            last_state = int(steamworks.Workshop.GetItemState(item_id_int))
        except Exception:
            pass
        folder_now = _safe_get_workshop_install_folder(steamworks, item_id_int)
        if folder_now:
            last_folder = folder_now
        if _is_workshop_item_install_complete(last_state, last_folder):
            return {
                "success": True,
                "item_id": str(item_id_int),
                "installed": True,
                "installedFolder": last_folder,
                "state": last_state,
            }
        await asyncio.sleep(poll_interval)

    # Timeout: return 202 with current progress so the frontend keeps polling.
    try:
        dinfo = steamworks.Workshop.GetItemDownloadInfo(item_id_int) or {}
    except Exception:
        dinfo = {}
    downloaded = int(dinfo.get('downloaded', 0) or 0) if isinstance(dinfo, dict) else 0
    total = int(dinfo.get('total', 0) or 0) if isinstance(dinfo, dict) else 0
    return JSONResponse({
        "success": False,
        "item_id": str(item_id_int),
        "installed": False,
        "timeout": True,
        "state": last_state,
        "bytesDownloaded": downloaded,
        "bytesTotal": total,
        "message": f"下载未在 {int(timeout_seconds)} 秒内完成，请稍后重试或继续轮询。",
    }, status_code=202)


@router.get('/item/{item_id}/download-status')
def get_workshop_item_download_status(item_id: str):
    """Poll a single subscribed item's download/install status; called by the frontend while waiting for a download."""
    steamworks = get_steamworks()
    if steamworks is None:
        return JSONResponse({
            "success": False,
            "error": "Steamworks未初始化",
        }, status_code=503)

    try:
        item_id_int = int(item_id)
    except (TypeError, ValueError):
        return JSONResponse({
            "success": False,
            "error": "无效的物品ID",
        }, status_code=400)

    try:
        item_state = int(steamworks.Workshop.GetItemState(item_id_int))
    except Exception as exc:
        logger.debug(f"GetItemState({item_id_int}) 失败: {exc}")
        item_state = 0

    folder = _safe_get_workshop_install_folder(steamworks, item_id_int)
    installed = _is_workshop_item_install_complete(item_state, folder)

    try:
        download_info = steamworks.Workshop.GetItemDownloadInfo(item_id_int) or {}
    except Exception as exc:
        logger.debug(f"GetItemDownloadInfo({item_id_int}) 失败: {exc}")
        download_info = {}
    if isinstance(download_info, dict):
        downloaded = int(download_info.get('downloaded', 0) or 0)
        total = int(download_info.get('total', 0) or 0)
    else:
        downloaded = total = 0

    return {
        "success": True,
        "item_id": str(item_id_int),
        "state": item_state,
        "subscribed": bool(item_state & _ITEM_STATE_SUBSCRIBED),
        "installed": installed,
        "installedFolder": folder if installed else None,
        "downloading": bool(item_state & _ITEM_STATE_DOWNLOADING) or (total > 0 and downloaded < total),
        "downloadPending": bool(item_state & _ITEM_STATE_DOWNLOAD_PENDING),
        "needsUpdate": bool(item_state & _ITEM_STATE_NEEDS_UPDATE),
        "bytesDownloaded": downloaded,
        "bytesTotal": total,
        "progress": (downloaded / total) if total > 0 else (1.0 if installed else 0.0),
    }


def _build_ugc_details_unsupported_item_response(steamworks, item_id_int: int, item_state: int):
    """Build an explicit partial detail response when UGC details are unsupported."""
    install_info = None
    installed = False
    folder = ''
    size = 0

    try:
        install_info = steamworks.Workshop.GetItemInstallInfo(item_id_int)
    except (FileNotFoundError, OSError) as exc:
        logger.debug(f"获取物品 {item_id_int} 安装信息失败（可能刚取消订阅）: {exc}")
    except Exception as exc:
        logger.warning(f"获取物品 {item_id_int} 安装信息失败: {exc}")

    if install_info and isinstance(install_info, dict):
        raw_folder = install_info.get('folder', '') or ''
        folder = str(raw_folder) if raw_folder else ''
        installed = bool(folder and os.path.isdir(folder))
        disk_size = install_info.get('disk_size')
        if installed and isinstance(disk_size, (int, float)):
            size = int(disk_size)
    elif isinstance(install_info, tuple) and len(install_info) >= 3:
        raw_installed, raw_folder, raw_size = install_info[:3]
        folder = str(raw_folder) if raw_folder and isinstance(raw_folder, (str, bytes)) else ''
        installed = bool(raw_installed) and bool(folder and os.path.isdir(folder))
        if installed and isinstance(raw_size, (int, float)):
            size = int(raw_size)

    try:
        download_info = steamworks.Workshop.GetItemDownloadInfo(item_id_int) or {}
    except Exception as exc:
        logger.debug(f"GetItemDownloadInfo({item_id_int}) 失败: {exc}")
        download_info = {}

    downloaded = 0
    total = 0
    progress = 0.0
    if isinstance(download_info, dict):
        downloaded = int(download_info.get("downloaded", 0) or 0)
        total = int(download_info.get("total", 0) or 0)
    elif isinstance(download_info, tuple) and len(download_info) >= 3:
        downloaded = int(download_info[0] or 0)
        total = int(download_info[1] or 0)
        progress = float(download_info[2] or 0.0)
    downloading = total > 0 and downloaded < total

    return {
        "success": True,
        "partial": True,
        "detailsAvailable": False,
        "detailsUnavailableReason": "ugc_details_query_unsupported",
        "item": {
            "publishedFileId": item_id_int,
            "title": f"未知物品_{item_id_int}",
            "description": "",
            "steamIDOwner": "",
            "authorName": None,
            "timeCreated": 0,
            "timeUpdated": 0,
            "previewImageUrl": "",
            "associatedUrl": "",
            "fileUrl": "",
            "fileSize": 0,
            "fileId": 0,
            "previewFileId": 0,
            "tags": [],
            "state": {
                "subscribed": bool(item_state & _ITEM_STATE_SUBSCRIBED),
                "legacyItem": bool(item_state & 2),
                "installed": installed,
                "needsUpdate": bool(item_state & _ITEM_STATE_NEEDS_UPDATE),
                "downloading": bool(item_state & _ITEM_STATE_DOWNLOADING) or downloading,
                "downloadPending": bool(item_state & _ITEM_STATE_DOWNLOAD_PENDING),
                "isWorkshopItem": bool(item_state & 128),
            },
            "installedFolder": folder if installed else None,
            "fileSizeOnDisk": size if installed else 0,
            "downloadProgress": {
                "bytesDownloaded": downloaded if downloading else 0,
                "bytesTotal": total if downloading else 0,
                "percentage": (progress * 100) if progress > 0 and downloading
                else ((downloaded / total * 100) if total > 0 and downloading else 0),
            },
        },
    }


def _is_known_item_when_ugc_details_unsupported(steamworks, item_id_int: int, item_state: int) -> bool:
    """Return whether an item is known without rich UGC details.

    Linux wrappers can lack UGC details query methods, but that degradation must
    not turn arbitrary numeric IDs into fake successful items. Only return a
    partial response when Steam still exposes local/subscription state for the
    item through the non-UGC-detail APIs.
    """
    known_state_bits = (
        _ITEM_STATE_SUBSCRIBED
        | _ITEM_STATE_INSTALLED
        | _ITEM_STATE_NEEDS_UPDATE
        | _ITEM_STATE_DOWNLOADING
        | _ITEM_STATE_DOWNLOAD_PENDING
    )
    if item_state & known_state_bits:
        return True

    try:
        subscribed_items = steamworks.Workshop.GetSubscribedItems()
        parsed_subscribed_items = set()
        for raw_item_id in subscribed_items or []:
            try:
                parsed_subscribed_items.add(int(raw_item_id))
            except (TypeError, ValueError):
                continue
        if item_id_int in parsed_subscribed_items:
            return True
    except Exception as exc:
        logger.debug(f"GetSubscribedItems fallback for {item_id_int} failed: {exc}")

    folder = _safe_get_workshop_install_folder(steamworks, item_id_int)
    if folder and os.path.isdir(folder):
        return True

    try:
        download_info = steamworks.Workshop.GetItemDownloadInfo(item_id_int) or {}
    except Exception as exc:
        logger.debug(f"GetItemDownloadInfo({item_id_int}) fallback failed: {exc}")
        download_info = {}
    if isinstance(download_info, dict):
        return int(download_info.get("total", 0) or 0) > 0
    if isinstance(download_info, tuple) and len(download_info) >= 2:
        return int(download_info[1] or 0) > 0
    return False


@router.get('/item/{item_id}/path')
def get_workshop_item_path(item_id: str):
    """
    Get the download path of a single Steam Workshop item.
    This API endpoint is dedicated to fetching an item's install path on the management page.
    """
    steamworks = get_steamworks()
    
    # Check Steamworks initialization.
    if steamworks is None:
        return JSONResponse({
            "success": False,
            "error": "Steamworks未初始化",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)
    
    try:
        # Convert item_id to an integer.
        item_id_int = int(item_id)
        
        # Get item installation information.
        install_info = steamworks.Workshop.GetItemInstallInfo(item_id_int)
        
        if not install_info:
            return JSONResponse({
                "success": False,
                "error": "物品未安装",
                "message": f"物品 {item_id} 尚未安装或安装信息不可用"
            }, status_code=404)
        
        # Extract the installation path, supporting both dict and tuple return formats.
        folder_path = ''
        size_on_disk: int | None = None
        
        if isinstance(install_info, dict):
            folder_path = install_info.get('folder', '') or ''
            disk_size = install_info.get('disk_size')
            if isinstance(disk_size, (int, float)):
                size_on_disk = int(disk_size)
        elif isinstance(install_info, tuple) and len(install_info) >= 3:
            folder, disk_size = install_info[1], install_info[2]
            if isinstance(folder, (str, bytes)):
                folder_path = str(folder)
            if isinstance(disk_size, (int, float)):
                size_on_disk = int(disk_size)
        
        # Build the response.
        response = {
            "success": True,
            "item_id": item_id,
            "installed": True,
            "path": folder_path,
            "full_path": folder_path  # Full path, consistent with path.
        }
        
        # Include disk-size information if available.
        if size_on_disk is not None:
            response['size_on_disk'] = size_on_disk
        
        return response
        
    except ValueError:
        return JSONResponse({
            "success": False,
            "error": "无效的物品ID",
            "message": "物品ID必须是有效的数字"
        }, status_code=400)
    except Exception as e:
        logger.error(f"获取物品 {item_id} 路径时出错: {e}")
        return JSONResponse({
            "success": False,
            "error": "获取路径失败",
            "message": str(e)
        }, status_code=500)


@router.get('/item/{item_id}')
async def get_workshop_item_details(item_id: str):
    """
    Get detailed info of a single Steam Workshop item.
    """
    steamworks = get_steamworks()
    
    # Check Steamworks initialization.
    if steamworks is None:
        return JSONResponse({
            "success": False,
            "error": "Steamworks未初始化",
            "message": "请确保Steam客户端已运行且已登录"
        }, status_code=503)
    
    try:
        # Convert item_id to an integer.
        item_id_int = int(item_id)
        
        # Get item state.
        item_state = steamworks.Workshop.GetItemState(item_id_int)
        
        # Query one item using the shared batch-query helper with retries.
        try:
            ugc_results = await _query_ugc_details_batch(steamworks, [item_id_int], max_retries=2)
        except UnsupportedUGCDetailsError:
            if not _is_known_item_when_ugc_details_unsupported(steamworks, item_id_int, item_state):
                return JSONResponse({
                    "success": False,
                    "error": "获取物品详情失败，未找到物品",
                    "detailsAvailable": False,
                    "detailsUnavailableReason": "ugc_details_query_unsupported",
                }, status_code=404)
            return _build_ugc_details_unsupported_item_response(steamworks, item_id_int, item_state)
        result = ugc_results.get(item_id_int)
        
        # On query failure, try cached data with per-entry TTL checks.
        if not result and _is_item_cache_valid(item_id_int):
            cached = _ugc_details_cache[item_id_int]
            # Build the response from cached data.
            use_cache = True
        else:
            use_cache = False
            
        if result or use_cache:
            # Get installation information, supporting dict/tuple/None returns.
            install_info = steamworks.Workshop.GetItemInstallInfo(item_id_int)
            installed = False
            folder = ''
            size = 0

            if install_info and isinstance(install_info, dict):
                installed = True
                folder = install_info.get('folder', '') or ''
                disk_size = install_info.get('disk_size')
                if isinstance(disk_size, (int, float)):
                    size = int(disk_size)
            elif isinstance(install_info, tuple) and len(install_info) >= 3:
                installed = bool(install_info[0])
                raw_folder = install_info[1]
                if isinstance(raw_folder, (str, bytes)):
                    folder = str(raw_folder)
                raw_size = install_info[2]
                if isinstance(raw_size, (int, float)):
                    size = int(raw_size)
            elif install_info:
                installed = True
            
            # Get item download information.
            download_info = steamworks.Workshop.GetItemDownloadInfo(item_id_int)
            downloading = False
            bytes_downloaded = 0
            bytes_total = 0
            
            # Process download information using the correct downloaded and total keys.
            if download_info:
                if isinstance(download_info, dict):
                    downloaded = int(download_info.get("downloaded", 0) or 0)
                    total = int(download_info.get("total", 0) or 0)
                    downloading = downloaded > 0 and downloaded < total
                    bytes_downloaded = downloaded
                    bytes_total = total
                elif isinstance(download_info, tuple) and len(download_info) >= 3:
                    # Support tuple format.
                    downloading, bytes_downloaded, bytes_total = download_info
            
            if use_cache:
                # Build results from cache.
                title = cached.get('title', f'未知物品_{item_id}')
                description = cached.get('description', '')
                owner_id_str = cached.get('steamIDOwner', '')
                author_name = cached.get('authorName')
                time_created = cached.get('timeCreated', 0)
                time_updated = cached.get('timeUpdated', 0)
                file_size = 0
                preview_url = cached.get('previewImageUrl', '')
                associated_url = ''
                file_url = ''
                file_id = 0
                preview_file_id = 0
                tags = cached.get('tags', [])
            else:
                # Decode bytes fields to strings to avoid JSON serialization errors.
                title = result.title.decode('utf-8', errors='replace') if hasattr(result, 'title') and isinstance(result.title, bytes) else getattr(result, 'title', '')
                description = result.description.decode('utf-8', errors='replace') if hasattr(result, 'description') and isinstance(result.description, bytes) else getattr(result, 'description', '')
                
                # Resolve steamIDOwner to the actual username.
                owner_id = int(result.steamIDOwner) if hasattr(result, 'steamIDOwner') and result.steamIDOwner else 0
                owner_id_str = str(owner_id) if owner_id else ''
                author_name = _resolve_author_name(steamworks, owner_id) if owner_id else None
                time_created = getattr(result, 'timeCreated', 0)
                time_updated = getattr(result, 'timeUpdated', 0)
                file_size = getattr(result, 'fileSize', 0)
                preview_url = getattr(result, 'previewImageUrl', '') or ''
                # Decode the associated webpage URL for optional client use.
                raw_url = getattr(result, 'URL', b'')
                if isinstance(raw_url, bytes):
                    raw_url = raw_url.decode('utf-8', errors='replace')
                associated_url = raw_url.strip('\x00').strip() if raw_url else ''
                # file handle and preview file handle are UGC file handles, not download URLs.
                file_url = ''
                file_id = getattr(result, 'file', 0)
                preview_file_id = getattr(result, 'previewFile', 0)
                tags = []
                if hasattr(result, 'tags') and result.tags:
                    try:
                        tags_str = result.tags.decode('utf-8', errors='replace')
                        if tags_str:
                            tags = [t.strip() for t in tags_str.split(',') if t.strip()]
                    except Exception as e:
                        logger.debug(f"解析物品 {item_id} 标签失败: {e}")
                
                # Update cache.
                _extract_ugc_item_details(steamworks, item_id_int, result, {
                    "publishedFileId": str(item_id_int),
                    "title": f"未知物品_{item_id}", "description": ""
                })
            
            # Build detailed item information.
            item_info = {
                "publishedFileId": item_id_int,
                "title": title,
                "description": description,
                "steamIDOwner": owner_id_str,
                "authorName": author_name,
                "timeCreated": time_created,
                "timeUpdated": time_updated,
                "previewImageUrl": preview_url,
                "associatedUrl": associated_url,
                "fileUrl": file_url,
                "fileSize": file_size,
                "fileId": file_id,
                "previewFileId": preview_file_id,
                "tags": tags,
                "state": {
                    "subscribed": bool(item_state & 1),
                    "legacyItem": bool(item_state & 2),
                    "installed": installed,
                    "needsUpdate": bool(item_state & 8),
                    "downloading": downloading,
                    "downloadPending": bool(item_state & 32),
                    "isWorkshopItem": bool(item_state & 128)
                },
                "installedFolder": folder if installed else None,
                "fileSizeOnDisk": size if installed else 0,
                "downloadProgress": {
                    "bytesDownloaded": bytes_downloaded if downloading else 0,
                    "bytesTotal": bytes_total if downloading else 0,
                    "percentage": (bytes_downloaded / bytes_total * 100) if bytes_total > 0 and downloading else 0
                }
            }

            # Use the Web API fallback to complete authorName; Friends API often returns fabricated values for nonfriend owners.
            try:
                await _resolve_missing_author_names([item_info])
            except Exception as fallback_err:
                logger.debug(f"Web API 补全单条 authorName 出错（忽略）: {fallback_err}")

            return {
                "success": True,
                "item": item_info
            }

        else:
            # Note: SteamWorkshop has no ReleaseQueryUGCRequest method.
            return JSONResponse({
                "success": False,
                "error": "获取物品详情失败，未找到物品"
            }, status_code=404)
            
    except ValueError:
        return JSONResponse({
            "success": False,
            "error": "无效的物品ID"
        }, status_code=400)
    except Exception as e:
        logger.error(f"获取物品 {item_id} 详情时出错: {e}")
        return JSONResponse({
            "success": False,
            "error": f"获取物品详情失败: {str(e)}"
        }, status_code=500)
