# -*- coding: utf-8 -*-
"""
创意工坊路径管理工具模块
用于处理创意工坊路径的获取、配置和管理
所有配置路径统一从 config_manager 获取
"""

import os
import asyncio
import pathlib
import logging
from typing import Optional, Dict, Any

# 初始化日志记录器
logger = logging.getLogger(__name__)

# 从config_manager导入workshop配置相关功能
from utils.config_manager import (
    load_workshop_config,
    save_workshop_config,
    save_workshop_path,
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


def get_workshop_root(globals_dict: Optional[Dict[str, Any]] = None) -> str:
    """
    获取创意工坊根目录路径，并将路径保存到配置文件中
    
    Args:
        globals_dict: 全局变量字典，用于访问get_subscribed_workshop_items函数
        
    Returns:
        str: 创意工坊根目录路径
    """
    # 如果没有提供globals_dict，使用当前模块的globals
    if globals_dict is None:
        globals_dict = globals()
    
    workshop_path = None
    
    try:
        # 尝试获取get_subscribed_workshop_items函数引用
        subscribed_items_func = globals_dict.get('get_subscribed_workshop_items')
        if subscribed_items_func:
            # 使用asyncio.run()来运行异步函数
            workshop_items_result = asyncio.run(subscribed_items_func())
            if isinstance(workshop_items_result, dict) and workshop_items_result.get('success', False):
                items = workshop_items_result.get('items', [])
                if items:
                    first_item = items[0]
                    WORKSHOP_PATH_FIRST = first_item.get('installedFolder')
                    if WORKSHOP_PATH_FIRST:
                        logger.info(f"成功获取第一个创意工坊物品的安装目录: {WORKSHOP_PATH_FIRST}")
                        
                        p = pathlib.Path(WORKSHOP_PATH_FIRST)
                        # 确保目录存在
                        if p.parent.exists():
                            workshop_path = str(p.parent)
                        else:
                            logger.warning(f"计算得到的创意工坊根目录不存在: {p.parent}")
                    else:
                        logger.warning("第一个创意工坊物品没有安装目录")
                else:
                    logger.warning("未找到任何订阅的创意工坊物品")
            else:
                logger.error("获取订阅的创意工坊物品失败")
        else:
            logger.warning("get_subscribed_workshop_items函数尚未定义，使用默认路径")
    except Exception as e:
        logger.error(f"获取创意工坊物品列表时出错: {e}")
    
    # 如果未能从创意工坊获取路径，使用get_workshop_path获取配置中的路径
    if not workshop_path:
        workshop_path = get_workshop_path()
        logger.info(f"使用配置中的创意工坊路径: {workshop_path}")
    
    # 将获取到的路径保存到配置文件中（使用config_manager的函数）
    try:
        save_workshop_path(workshop_path)
    except Exception as e:
        error_msg = f"保存创意工坊路径到配置文件失败: {e}"
        logger.error(error_msg)
    
    # 确保路径存在
    ensure_workshop_folder_exists(workshop_path)
    return workshop_path
