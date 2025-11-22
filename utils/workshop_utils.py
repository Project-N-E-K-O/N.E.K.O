# -*- coding: utf-8 -*-
"""
创意工坊路径管理工具模块
用于处理创意工坊路径的获取、配置和管理
"""

import os
import json
import asyncio
import pathlib
from typing import Optional, Dict, Any

# 默认的创意工坊物品文件夹路径
DEFAULT_WORKSHOP_FOLDER = os.path.join(os.path.expanduser('~'), 'Documents', 'Xiao8', 'live2d')

# 配置文件名
WORKSHOP_CONFIG_FILE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config', 'workshop_config.json')

# 配置数据
workshop_config: Dict[str, Any] = {}

def load_workshop_config() -> None:
    """
    加载创意工坊配置
    """
    global workshop_config
    try:
        if os.path.exists(WORKSHOP_CONFIG_FILE):
            with open(WORKSHOP_CONFIG_FILE, 'r', encoding='utf-8') as f:
                workshop_config = json.load(f)
                print(f"成功加载创意工坊配置: {workshop_config}")
        else:
            # 如果配置文件不存在，创建默认配置
            workshop_config = {
                "default_workshop_folder": DEFAULT_WORKSHOP_FOLDER,
                "auto_create_folder": True
            }
            save_workshop_config()
            print(f"创建默认创意工坊配置: {workshop_config}")
    except Exception as e:
        error_msg = f"加载创意工坊配置失败: {e}"
        print(error_msg)
        # 尝试使用logger记录错误
        try:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(error_msg)
        except Exception:
            pass
        # 使用默认配置
        workshop_config = {
            "default_workshop_folder": DEFAULT_WORKSHOP_FOLDER,
            "auto_create_folder": True
        }

def save_workshop_config() -> None:
    """
    保存创意工坊配置
    """
    try:
        # 确保配置目录存在
        os.makedirs(os.path.dirname(WORKSHOP_CONFIG_FILE), exist_ok=True)
        
        # 保存配置
        with open(WORKSHOP_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(workshop_config, f, indent=4, ensure_ascii=False)
        
        print(f"成功保存创意工坊配置: {workshop_config}")
        
        # 尝试使用logger记录
        try:
            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"成功保存创意工坊配置: {workshop_config}")
        except Exception:
            pass
    except Exception as e:
        error_msg = f"保存创意工坊配置失败: {e}"
        print(error_msg)
        # 尝试使用logger记录错误
        try:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(error_msg)
        except Exception:
            pass

def ensure_workshop_folder_exists(folder_path: Optional[str] = None) -> bool:
    """
    确保创意工坊文件夹存在，如果不存在则自动创建
    
    Args:
        folder_path: 指定的文件夹路径，如果为None则使用配置中的默认路径
        
    Returns:
        bool: 文件夹是否存在或创建成功
    """
    # 确定目标文件夹路径
    raw_folder = folder_path or workshop_config.get("default_workshop_folder", DEFAULT_WORKSHOP_FOLDER)
    
    # 确保路径是绝对路径，如果不是则转换
    if not os.path.isabs(raw_folder):
        # 如果是相对路径，尝试以用户主目录为基础
        base_dir = os.path.expanduser('~')
        target_folder = os.path.join(base_dir, raw_folder)
    else:
        target_folder = raw_folder
    
    # 标准化路径
    target_folder = os.path.normpath(target_folder)
    
    # 尝试使用logger记录
    try:
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f'ensure_workshop_folder_exists - 最终处理的目标文件夹: {target_folder}')
    except Exception:
        pass
    
    # 如果文件夹存在，直接返回True
    if os.path.exists(target_folder):
        return True
    
    # 如果文件夹不存在，检查是否允许自动创建
    auto_create = workshop_config.get("auto_create_folder", True)
    
    # 如果不允许自动创建，明确返回False
    if not auto_create:
        return False
    
    # 如果允许自动创建，尝试创建文件夹
    try:
        # 使用exist_ok=True确保即使中间目录不存在也能创建
        os.makedirs(target_folder, exist_ok=True)
        return True
    except Exception as e:
        error_msg = f"创建创意工坊文件夹失败: {e}"
        print(error_msg)
        # 尝试使用logger记录错误
        try:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(error_msg)
        except Exception:
            pass
        return False

def get_workshop_root(globals_dict: Optional[Dict[str, Any]] = None) -> str:
    """
    获取创意工坊根目录路径
    
    Args:
        globals_dict: 全局变量字典，用于访问get_subscribed_workshop_items函数
        
    Returns:
        str: 创意工坊根目录路径
    """
    # 如果没有提供globals_dict，使用当前模块的globals
    if globals_dict is None:
        globals_dict = globals()
    
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
                        # 尝试使用logger记录
                        try:
                            import logging
                            logger = logging.getLogger(__name__)
                            logger.info(f"成功获取第一个创意工坊物品的安装目录: {WORKSHOP_PATH_FIRST}")
                        except Exception:
                            print(f"成功获取第一个创意工坊物品的安装目录: {WORKSHOP_PATH_FIRST}")
                        
                        p = pathlib.Path(WORKSHOP_PATH_FIRST)
                        # 确保目录存在
                        if p.parent.exists():
                            return str(p.parent)
                        else:
                            # 尝试使用logger记录
                            try:
                                import logging
                                logger = logging.getLogger(__name__)
                                logger.warning(f"计算得到的创意工坊根目录不存在: {p.parent}")
                            except Exception:
                                print(f"计算得到的创意工坊根目录不存在: {p.parent}")
                    else:
                        # 尝试使用logger记录
                        try:
                            import logging
                            logger = logging.getLogger(__name__)
                            logger.warning("第一个创意工坊物品没有安装目录")
                        except Exception:
                            print("第一个创意工坊物品没有安装目录")
                else:
                    # 尝试使用logger记录
                    try:
                        import logging
                        logger = logging.getLogger(__name__)
                        logger.warning("未找到任何订阅的创意工坊物品")
                    except Exception:
                        print("未找到任何订阅的创意工坊物品")
            else:
                # 尝试使用logger记录
                try:
                    import logging
                    logger = logging.getLogger(__name__)
                    logger.error("获取订阅的创意工坊物品失败")
                except Exception:
                    print("获取订阅的创意工坊物品失败")
        else:
            # 尝试使用logger记录
            try:
                import logging
                logger = logging.getLogger(__name__)
                logger.warning("get_subscribed_workshop_items函数尚未定义，使用默认路径")
            except Exception:
                print("get_subscribed_workshop_items函数尚未定义，使用默认路径")
    except Exception as e:
        error_msg = f"获取创意工坊物品列表时出错: {e}"
        # 尝试使用logger记录
        try:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(error_msg)
        except Exception:
            print(error_msg)
    
    # 返回默认的创意工坊文件夹路径作为后备
    default_path = workshop_config.get("default_workshop_folder", DEFAULT_WORKSHOP_FOLDER)
    # 尝试使用logger记录
    try:
        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"使用默认创意工坊路径: {default_path}")
    except Exception:
        print(f"使用默认创意工坊路径: {default_path}")
    
    # 确保默认路径存在
    ensure_workshop_folder_exists(default_path)
    return default_path

# 初始化时加载配置
load_workshop_config()