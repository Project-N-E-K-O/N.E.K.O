# -*- coding: utf-8 -*-
"""
创意工坊路径管理工具模块
用于处理创意工坊路径的获取、配置和管理
所有配置路径统一从 config_manager 获取

依赖层次: utils层 -> config层 (单向依赖，不依赖main层)
"""

import os
import pathlib
from typing import Optional, List, Dict, Any

from utils.logger_config import get_module_logger

# 初始化日志记录器
logger = get_module_logger(__name__)

# 从config_manager导入workshop配置相关功能
from utils.config_manager import (
    load_workshop_config,
    save_workshop_path,
    persist_user_workshop_folder,
    get_workshop_path
)

def ensure_workshop_folder_exists(folder_path: Optional[str] = None) -> bool:
    """
    确保本地mod文件夹（原创意工坊文件夹）存在，如果不存在则自动创建
    
    Args:
        folder_path: 指定的文件夹路径，如果为None则使用配置中的默认路径
        
    Returns:
        bool: 文件夹是否存在或创建成功
    """
    # 确定目标文件夹路径
    config = load_workshop_config()
    # 使用get_workshop_path()函数获取路径，该函数已更新为优先使用user_mod_folder
    raw_folder = folder_path or get_workshop_path()
    
    # 确保路径是绝对路径，如果不是则转换
    if not os.path.isabs(raw_folder):
        # 如果是相对路径，尝试以用户主目录为基础
        base_dir = os.path.expanduser('~')
        target_folder = os.path.join(base_dir, raw_folder)
    else:
        target_folder = raw_folder
    
    # 标准化路径
    target_folder = os.path.normpath(target_folder)
    
    logger.info(f'ensure_workshop_folder_exists - 最终处理的目标文件夹: {target_folder}')
    
    # 如果文件夹存在，直接返回True
    if os.path.exists(target_folder):
        return True
    
    # 如果文件夹不存在，检查是否允许自动创建
    auto_create = config.get("auto_create_folder", True)
    
    # 如果不允许自动创建，明确返回False
    if not auto_create:
        return False
    
    # 如果允许自动创建，尝试创建文件夹
    try:
        # 使用exist_ok=True确保即使中间目录不存在也能创建
        os.makedirs(target_folder, exist_ok=True)
        return True
    except Exception as e:
        logger.error(f"创建创意工坊文件夹失败: {e}")
        return False


def extract_workshop_root_from_items(items: List[Dict[str, Any]]) -> Optional[str]:
    """
    从创意工坊物品列表中提取根目录路径
    
    这是一个纯函数，不依赖任何外部状态或模块。
    由上层（main_server）获取物品列表后传入。
    
    Args:
        items: 创意工坊物品列表，每个物品包含 installedFolder 字段
        
    Returns:
        str | None: 创意工坊根目录路径，如果无法提取则返回None
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
    # 创意工坊根目录是物品安装目录的父目录
    if p.parent.exists():
        return str(p.parent)
    else:
        logger.warning(f"计算得到的创意工坊根目录不存在: {p.parent}")
        return None


def get_workshop_root(subscribed_items: Optional[List[Dict[str, Any]]] = None) -> str:
    """
    获取创意工坊根目录路径，并将路径保存到配置文件中
    
    设计原则：
    - 此函数不依赖 main_server 层
    - 上层负责获取 subscribed_items 并传入
    - 如果未传入物品列表，则仅使用配置中的路径
    
    Args:
        subscribed_items: 已获取的创意工坊订阅物品列表（由上层传入）
        
    Returns:
        str: 创意工坊根目录路径
    """
    workshop_path = None
    from_steam = False
    
    # 如果提供了物品列表，尝试从中提取根目录
    if subscribed_items:
        workshop_path = extract_workshop_root_from_items(subscribed_items)
        if workshop_path:
            from_steam = True
    
    # 如果未能从物品列表获取路径，使用配置中的路径
    if not workshop_path:
        workshop_path = get_workshop_path()
        logger.info(f"使用配置中的创意工坊路径: {workshop_path}")
    
    # 将获取到的路径保存到运行时变量
    try:
        save_workshop_path(workshop_path)
    except Exception as e:
        logger.error(f"保存创意工坊路径到运行时变量失败: {e}")
    
    # 首次从Steam成功获取时，持久化到配置文件作为后续回退
    if from_steam:
        try:
            persist_user_workshop_folder(workshop_path)
        except Exception as e:
            logger.error(f"持久化Steam创意工坊路径失败: {e}")
    
    # 确保路径存在
    ensure_workshop_folder_exists(workshop_path)
    return workshop_path


def get_default_workshop_folder() -> Optional[str]:
    """
    获取创意工坊目录路径（用于 monitor 等独立进程的静态文件挂载）。

    与 get_workshop_path() 使用同一优先级链，在 Steam 未运行时
    会自动回退到上次缓存的 user_workshop_folder 或本地 default_workshop_folder。
    """
    path = get_workshop_path()
    if path and os.path.isdir(path):
        return path
    return None
