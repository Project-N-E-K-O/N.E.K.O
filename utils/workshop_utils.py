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

"""
Workshop path management utility module
Handles fetching, configuring and managing Workshop paths
All configured paths come uniformly from config_manager

Dependency hierarchy: utils layer -> config layer (one-way; no dependency on the main layer)
"""

import asyncio
import os
import pathlib
from typing import Optional, List, Dict, Any

from utils.logger_config import get_module_logger

# Initialize the logger.
logger = get_module_logger(__name__)

# Import Workshop configuration helpers from config_manager.
from utils.config_manager import (
    load_workshop_config,
    save_workshop_config,
    save_workshop_path,
    persist_user_workshop_folder,
    get_workshop_path
)


async def get_workshop_path_async() -> str:
    """Resolve the configured workshop root without blocking the event loop."""
    return await asyncio.to_thread(get_workshop_path)


def ensure_workshop_folder_exists(
    folder_path: Optional[str] = None,
    *,
    auto_create: Optional[bool] = None,
) -> bool:
    """
    Ensure the local mod folder (formerly the Workshop folder) exists, creating it if missing
    
    Args:
        folder_path: the folder path; uses the configured default when None
        
    Returns:
        bool: whether the folder exists or was created successfully
    """
    # Do not reread config when the caller supplies auto_create: a read-modify-write transaction could see a concurrent transaction's
    # just-written config and decide using someone else's policy (Codex P2). Keeping that reread under the transaction lock also
    # makes event-loop get_workshop_path() wait on the same propagated lock trap.
    config = {} if auto_create is not None else load_workshop_config()
    # Get the path through get_workshop_path(), now preferring user_mod_folder.
    raw_folder = folder_path or get_workshop_path()
    
    # Ensure an absolute path, converting if necessary.
    if not os.path.isabs(raw_folder):
        # Resolve relative paths against the user's home directory.
        base_dir = os.path.expanduser('~')
        target_folder = os.path.join(base_dir, raw_folder)
    else:
        target_folder = raw_folder
    
    # Normalize the path.
    target_folder = os.path.normpath(target_folder)
    
    logger.info(f'ensure_workshop_folder_exists - 最终处理的目标文件夹: {target_folder}')
    
    # Use isdir, not exists: a regular file also satisfies exists, falsely reporting
    # the directory ready and storing that file as the Workshop root; every subsequent join with
    # WorkshopExport then fails. Treat files as not ready; makedirs below raises
    # FileExistsError, caught as False, the intended result.
    if os.path.isdir(target_folder):
        return True
    
    # If the directory is missing, check whether automatic creation is allowed.
    if auto_create is None:
        auto_create = config.get("auto_create_folder", True)
    
    # Return False explicitly when automatic creation is disabled.
    if not auto_create:
        return False
    
    # Try creating the directory if allowed.
    try:
        # Use exist_ok=True so missing intermediate directories can also be created.
        os.makedirs(target_folder, exist_ok=True)
        return True
    except Exception as e:
        logger.error(f"创建创意工坊文件夹失败: {e}")
        return False


def extract_workshop_root_from_items(items: List[Dict[str, Any]]) -> Optional[str]:
    """
    Extract the root directory path from a list of Workshop items
    
    A pure function with no external state or module dependencies.
    The upper layer (main_server) fetches the item list and passes it in.
    
    Args:
        items: list of Workshop items; each contains an installedFolder field
        
    Returns:
        str | None: the Workshop root directory path, or None when it cannot be extracted
    """
    if not items:
        logger.warning("未找到任何订阅的创意工坊物品")
        return None
    
    first_item = items[0]
    installed_folder = first_item.get('installedFolder')
    
    if not installed_folder:
        logger.warning("第一个创意工坊物品没有安装目录")
        return None
    
    logger.info(f"成功获取第一个创意工坊物品的安装目录: {installed_folder}")
    
    p = pathlib.Path(installed_folder)
    # The Workshop root is the parent of item install directories.
    if p.parent.exists():
        return str(p.parent)
    else:
        logger.warning(f"计算得到的创意工坊根目录不存在: {p.parent}")
        return None


def get_workshop_root(subscribed_items: Optional[List[Dict[str, Any]]] = None) -> str:
    """
    Get the Workshop root directory path and save it into the config file
    
    Design principles:
    - this function does not depend on the main_server layer
    - the upper layer is responsible for fetching subscribed_items and passing them in
    - when no item list is passed, only the configured path is used
    
    Args:
        subscribed_items: the fetched list of subscribed Workshop items (passed by the upper layer)
        
    Returns:
        str: the Workshop root directory path
    """
    workshop_path = None
    from_steam = False
    
    # If an item list is provided, try extracting the root from it.
    if subscribed_items:
        workshop_path = extract_workshop_root_from_items(subscribed_items)
        if workshop_path:
            from_steam = True
    
    # Fall back to the configured path if extraction fails.
    if not workshop_path:
        workshop_path = get_workshop_path()
        logger.info(f"使用配置中的创意工坊路径: {workshop_path}")
    
    # Save the obtained path in the runtime variable.
    try:
        save_workshop_path(workshop_path)
    except Exception as e:
        logger.error(f"保存创意工坊路径到运行时变量失败: {e}")
    
    # Persist the first successfully retrieved Steam path as a subsequent fallback.
    if from_steam:
        try:
            persist_user_workshop_folder(workshop_path)
        except Exception as e:
            logger.error(f"持久化Steam创意工坊路径失败: {e}")
    
    # Ensure the path exists.
    ensure_workshop_folder_exists(workshop_path)
    return workshop_path


async def get_workshop_root_async(
    subscribed_items: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Resolve and persist the workshop root without blocking the event loop."""
    return await asyncio.to_thread(get_workshop_root, subscribed_items)


def get_default_workshop_folder() -> Optional[str]:
    """
    Get the Workshop directory path (for static file mounting in standalone processes like monitor).

    Uses the same priority chain as get_workshop_path(); when Steam is not running it
    automatically falls back to the last cached user_workshop_folder or the local default_workshop_folder.
    """
    path = get_workshop_path()
    if path and os.path.isdir(path):
        return path
    return None
