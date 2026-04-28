import asyncio
import json
import os
from typing import Dict, Any, Optional, List
from utils.config_manager import get_config_manager
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from utils.file_utils import atomic_write_json

# 初始化配置管理器
_config_manager = get_config_manager()


def _get_preferences_read_path() -> str:
    return str(_config_manager.get_config_path('user_preferences.json'))


def _get_preferences_write_path() -> str:
    return str(_config_manager.get_runtime_config_path('user_preferences.json'))


def _get_active_preferences_path() -> str:
    write_path = _get_preferences_write_path()
    if os.path.exists(write_path):
        return write_path
    return _get_preferences_read_path()


# 用户偏好文件路径（从配置管理器获取）
PREFERENCES_FILE = _get_active_preferences_path()

def load_user_preferences() -> List[Dict[str, Any]]:
    """
    加载用户偏好设置

    Returns:
        List[Dict[str, Any]]: 用户偏好列表，每个元素对应一个模型的偏好设置，如果文件不存在或读取失败则返回空列表
    """
    try:
        global PREFERENCES_FILE
        PREFERENCES_FILE = _get_active_preferences_path()
        if os.path.exists(PREFERENCES_FILE):
            with open(PREFERENCES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 兼容旧格式：如果是字典格式，转换为列表格式
                if isinstance(data, dict):
                    if 'model_path' in data and 'position' in data and 'scale' in data:
                        return [data]  # 将旧格式转换为列表
                    else:
                        return []
                elif isinstance(data, list):
                    return data
                else:
                    return []
    except Exception as e:
        print(f"加载用户偏好失败: {e}")
    return []


async def aload_user_preferences() -> List[Dict[str, Any]]:
    """异步版 load_user_preferences：供 async endpoint 调用，避免同步 open()+json.load() 阻塞事件循环。

    共享 load_user_preferences 的 dict→list 兼容处理。
    """
    def _sync_load():
        return load_user_preferences()
    return await asyncio.to_thread(_sync_load)


def save_user_preferences(preferences: List[Dict[str, Any]]) -> bool:
    """
    保存用户偏好设置
    
    Args:
        preferences (List[Dict[str, Any]]): 要保存的偏好设置列表
        
    Returns:
        bool: 保存成功返回True，失败返回False
    """
    try:
        assert_cloudsave_writable(_config_manager, operation="save", target="user_preferences.json")
        # 确保配置目录存在
        _config_manager.ensure_config_directory()
        # 更新路径（可能已迁移）
        global PREFERENCES_FILE
        PREFERENCES_FILE = _get_preferences_write_path()
        
        atomic_write_json(PREFERENCES_FILE, preferences, ensure_ascii=False, indent=2)
        return True
    except MaintenanceModeError:
        raise
    except Exception as e:
        print(f"保存用户偏好失败: {e}")
        return False

def update_model_preferences(model_path: str, position: Dict[str, float], scale: Dict[str, float], parameters: Optional[Dict[str, float]] = None, display: Optional[Dict[str, float]] = None, rotation: Optional[Dict[str, float]] = None, viewport: Optional[Dict[str, float]] = None, camera_position: Optional[Dict[str, float]] = None) -> bool:
    """
    更新指定模型的偏好设置

    Args:
        model_path (str): 模型路径
        position (Dict[str, float]): 位置信息 {'x': float, 'y': float, 'z': float}
        scale (Dict[str, float]): 缩放信息 {'x': float, 'y': float, 'z': float}
        parameters (Optional[Dict[str, float]]): 模型参数 {'paramId': value}
        display (Optional[Dict[str, float]]): 显示器信息 {'screenX': float, 'screenY': float}，用于多屏幕位置恢复
        rotation (Optional[Dict[str, float]]): 旋转信息 {'x': float, 'y': float, 'z': float}，用于VRM模型朝向
        viewport (Optional[Dict[str, float]]): 视口信息 {'width': float, 'height': float}，用于跨分辨率位置和缩放归一化
        
    Returns:
        bool: 更新成功返回True，失败返回False
    """
    try:
        # 拒绝保留键作为模型路径，防止破坏全局对话设置条目
        if model_path == GLOBAL_CONVERSATION_KEY:
            print(f"拒绝更新模型偏好：model_path 不能使用保留键 '{GLOBAL_CONVERSATION_KEY}'")
            return False

        # 加载现有偏好
        current_preferences = load_user_preferences()
        
        # 查找是否已存在该模型的偏好（跳过哨兵）
        model_index = -1
        for i, pref in enumerate(current_preferences):
            if pref.get('model_path') != GLOBAL_CONVERSATION_KEY and pref.get('model_path') == model_path:
                model_index = i
                break
        
        # 创建新的模型偏好
        new_model_pref = {
            'model_path': model_path,
            'position': position,
            'scale': scale
        }
        
        # 如果有参数，添加到偏好中
        if parameters is not None:
            new_model_pref['parameters'] = parameters

        # 如果有显示器信息，添加到偏好中（用于多屏幕位置恢复）
        if display is not None:
            new_model_pref['display'] = display

        # 【新增】如果有旋转信息，添加到偏好中（用于VRM模型朝向）
        if rotation is not None:
            new_model_pref['rotation'] = rotation

        # 如果有视口信息，添加到偏好中（用于跨分辨率位置和缩放归一化）
        if viewport is not None:
            new_model_pref['viewport'] = viewport

        # 如果有相机位置信息，添加到偏好中（用于恢复VRM滚轮缩放状态）
        if camera_position is not None:
            new_model_pref['camera_position'] = camera_position
        
        if model_index >= 0:
            # 更新现有模型的偏好，保留已有的参数（如果新参数为None则不更新参数）
            existing_pref = current_preferences[model_index]
            if parameters is not None:
                existing_pref['parameters'] = parameters
            elif 'parameters' in existing_pref:
                # 保留已有参数
                new_model_pref['parameters'] = existing_pref['parameters']
            # 处理显示器信息
            if display is not None:
                pass  # 已在上面添加到 new_model_pref
            elif 'display' in existing_pref:
                # 保留已有显示器信息
                new_model_pref['display'] = existing_pref['display']
            # 【新增】处理旋转信息
            if rotation is not None:
                pass  # 已在上面添加到 new_model_pref
            elif 'rotation' in existing_pref:
                # 保留已有旋转信息
                new_model_pref['rotation'] = existing_pref['rotation']
            # 处理视口信息
            if viewport is not None:
                pass  # 已在上面添加到 new_model_pref
            elif 'viewport' in existing_pref:
                # 保留已有视口信息
                new_model_pref['viewport'] = existing_pref['viewport']
            # 处理相机位置信息
            if camera_position is not None:
                pass  # 已在上面添加到 new_model_pref
            elif 'camera_position' in existing_pref:
                # 保留已有相机位置信息
                new_model_pref['camera_position'] = existing_pref['camera_position']
            current_preferences[model_index] = new_model_pref
        else:
            # 添加新模型的偏好到列表开头（作为首选）
            current_preferences.insert(0, new_model_pref)
        
        # 保存更新后的偏好
        return save_user_preferences(current_preferences)
    except Exception as e:
        if isinstance(e, MaintenanceModeError):
            raise
        print(f"更新模型偏好失败: {e}")
        return False

def get_model_preferences(model_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    获取指定模型的偏好设置，如果不指定则返回首选模型（列表第一个）的偏好
    
    Args:
        model_path (str, optional): 模型路径，如果不指定则返回首选模型
        
    Returns:
        Optional[Dict[str, Any]]: 包含model_path, position, scale的字典，如果没有则返回None
    """
    preferences = load_user_preferences()
    
    if not preferences:
        return None
    
    if model_path:
        # 查找指定模型的偏好
        for pref in preferences:
            if pref.get('model_path') == model_path:
                return pref
        return None
    else:
        # 返回首选模型（列表第一个）的偏好，跳过哨兵
        for pref in preferences:
            if pref.get('model_path') != GLOBAL_CONVERSATION_KEY:
                return pref
        return None

def get_preferred_model_path() -> Optional[str]:
    """
    获取首选模型的路径
    
    Returns:
        Optional[str]: 首选模型的路径，如果没有则返回None
    """
    preferences = load_user_preferences()
    for pref in preferences:
        if pref.get('model_path') != GLOBAL_CONVERSATION_KEY:
            return pref.get('model_path')
    return None

def validate_model_preferences(preferences: Dict[str, Any]) -> bool:
    """
    验证模型偏好设置是否包含必要字段
    
    Args:
        preferences (Dict[str, Any]): 要验证的模型偏好设置
        
    Returns:
        bool: 验证通过返回True，失败返回False
    """
    required_fields = ['model_path', 'position', 'scale']
    
    # 检查必要字段是否存在
    for field in required_fields:
        if field not in preferences:
            return False
    
    # 检查position和scale是否包含必要的子字段
    if not isinstance(preferences.get('position'), dict) or 'x' not in preferences['position'] or 'y' not in preferences['position']:
        return False
    
    if not isinstance(preferences.get('scale'), dict) or 'x' not in preferences['scale'] or 'y' not in preferences['scale']:
        return False
    
    # parameters 是可选的，但如果存在，必须是字典
    if 'parameters' in preferences and not isinstance(preferences['parameters'], dict):
        return False
    
    return True

def move_model_to_top(model_path: str) -> bool:
    """
    将指定模型移动到列表顶部（设为首选）
    
    Args:
        model_path (str): 模型路径
        
    Returns:
        bool: 操作成功返回True，失败返回False
    """
    try:
        preferences = load_user_preferences()
        
        # 查找模型索引（跳过哨兵）
        model_index = -1
        for i, pref in enumerate(preferences):
            if pref.get('model_path') != GLOBAL_CONVERSATION_KEY and pref.get('model_path') == model_path:
                model_index = i
                break
        
        if model_index >= 0:
            # 将模型移动到顶部
            model_pref = preferences.pop(model_index)
            preferences.insert(0, model_pref)
            return save_user_preferences(preferences)
        else:
            # 如果模型不存在，返回False
            return False
    except Exception as e:
        if isinstance(e, MaintenanceModeError):
            raise
        print(f"移动模型到顶部失败: {e}")
        return False


# ========== 全局对话设置（用于 localStorage 同步备份）==========

GLOBAL_CONVERSATION_KEY = "__global_conversation__"

# 全局对话设置允许的字段（白名单）
_ALLOWED_CONVERSATION_SETTINGS = {
    'proactiveChatEnabled', 'proactiveVisionEnabled', 'proactiveVisionChatEnabled',
    'proactiveNewsChatEnabled', 'proactiveVideoChatEnabled', 'proactivePersonalChatEnabled',
    'proactiveMusicEnabled', 'proactiveMemeEnabled', 'mergeMessagesEnabled', 'focusModeEnabled',
    'avatarReactionBubbleEnabled',
    'proactiveChatInterval', 'proactiveVisionInterval', 'subtitleEnabled', 'userLanguage',
    'textGuardMaxLength', 'noiseReductionEnabled'
}


def load_global_conversation_settings() -> Dict[str, Any]:
    """
    加载全局对话设置（从 user_preferences.json 的全局条目中读取）
    直接读取文件，不经过 load_user_preferences()（后者会过滤掉哨兵）

    Returns:
        Dict[str, Any]: 对话设置字典，如果不存在则返回空字典
    """
    try:
        global PREFERENCES_FILE
        PREFERENCES_FILE = _get_active_preferences_path()
        if os.path.exists(PREFERENCES_FILE):
            with open(PREFERENCES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list):
                    for pref in data:
                        if pref.get('model_path') == GLOBAL_CONVERSATION_KEY:
                            # 提取对话设置：仅返回白名单字段，防止泄露无关数据
                            return {k: v for k, v in pref.items() if k in _ALLOWED_CONVERSATION_SETTINGS}
    except Exception as e:
        print(f"加载全局对话设置失败: {e}")
    return {}


async def aload_global_conversation_settings() -> Dict[str, Any]:
    """异步版 load_global_conversation_settings：供 async 路径调用，offload sync IO。"""
    return await asyncio.to_thread(load_global_conversation_settings)


def is_privacy_mode_enabled() -> bool:
    """前端"隐私模式"开关是否开启。

    内部存储字段为 ``proactiveVisionEnabled``（True=允许自主视觉），
    隐私模式是它的反面。未同步时默认 False（与前端首次启动行为一致：
    隐私模式默认关，自主视觉默认开）。
    """
    settings = load_global_conversation_settings()
    return not settings.get('proactiveVisionEnabled', True)


async def ais_privacy_mode_enabled() -> bool:
    """异步版 ``is_privacy_mode_enabled``。"""
    settings = await aload_global_conversation_settings()
    return not settings.get('proactiveVisionEnabled', True)


def save_global_conversation_settings(settings: Dict[str, Any]) -> bool:
    """
    保存全局对话设置（写入 user_preferences.json 的全局条目）
    使用白名单过滤，只保存允许的字段，model_path 固定为哨兵值

    Args:
        settings (Dict[str, Any]): 要保存的对话设置字典

    Returns:
        bool: 保存成功返回True，失败返回False
    """
    try:
        assert_cloudsave_writable(_config_manager, operation="save", target="user_preferences.json")
        # 确保配置目录存在，并使用最新路径（与 save_user_preferences 保持一致）
        _config_manager.ensure_config_directory()
        global PREFERENCES_FILE

        write_path = _get_preferences_write_path()
        read_path = _get_preferences_read_path()

        # 优先从写路径读取；若不存在则回退读路径（迁移旧版只读偏好数据）
        if os.path.exists(write_path):
            PREFERENCES_FILE = write_path
            with open(PREFERENCES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
        elif os.path.exists(read_path):
            with open(read_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            PREFERENCES_FILE = write_path
        else:
            PREFERENCES_FILE = write_path
            data = []

        if isinstance(data, dict):
            # 兼容旧 dict 格式：包装为列表
            data = [data]
        elif not isinstance(data, list):
            data = []

        # 查找全局对话设置条目的索引
        global_index = -1
        for i, pref in enumerate(data):
            if pref.get('model_path') == GLOBAL_CONVERSATION_KEY:
                global_index = i
                break

        # 白名单过滤：只保留允许的字段，防止恶意覆盖
        filtered_settings = {k: v for k, v in settings.items() if k in _ALLOWED_CONVERSATION_SETTINGS}

        # 值级别验证：确保字段类型和范围正确
        _BOOL_FIELDS = {
            'proactiveChatEnabled', 'proactiveVisionEnabled', 'proactiveVisionChatEnabled',
            'proactiveNewsChatEnabled', 'proactiveVideoChatEnabled', 'proactivePersonalChatEnabled',
            'proactiveMusicEnabled', 'proactiveMemeEnabled', 'mergeMessagesEnabled', 'focusModeEnabled',
            'avatarReactionBubbleEnabled', 'subtitleEnabled', 'noiseReductionEnabled'
        }
        _INT_INTERVAL_FIELDS = {'proactiveChatInterval', 'proactiveVisionInterval'}
        _STRING_FIELDS = {'userLanguage'}
        _INT_LIMIT_FIELDS = {'textGuardMaxLength'}

        validated = {}
        for k, v in filtered_settings.items():
            if k in _BOOL_FIELDS:
                if isinstance(v, bool):
                    validated[k] = v
            elif k in _INT_INTERVAL_FIELDS:
                if isinstance(v, int) and 1000 <= v <= 3600000:
                    validated[k] = v
            elif k in _STRING_FIELDS:
                if isinstance(v, str) and v:
                    validated[k] = v
            elif k in _INT_LIMIT_FIELDS:
                if isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 2000:
                    validated[k] = v
        filtered_settings = validated

        # 合并到现有全局对话设置条目（保留未传入的字段，model_path 固定不可篡改）
        if global_index >= 0:
            global_pref = data[global_index].copy()
        else:
            global_pref = {}
        global_pref['model_path'] = GLOBAL_CONVERSATION_KEY
        global_pref.update(filtered_settings)

        if global_index >= 0:
            # 更新现有条目（保留其他模型偏好不变）
            data[global_index] = global_pref
        else:
            # 添加新条目到列表末尾
            data.append(global_pref)

        atomic_write_json(PREFERENCES_FILE, data, ensure_ascii=False, indent=2)
        return True
    except MaintenanceModeError:
        raise
    except Exception as e:
        print(f"保存全局对话设置失败: {e}")
        return False 
