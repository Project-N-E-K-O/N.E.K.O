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

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import json
import os
from pathlib import Path
import threading
import time
from typing import Dict, Any, Optional, List

import portalocker

from utils.config_manager import get_config_manager
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from utils.file_utils import atomic_write_json

# 初始化配置管理器
_config_manager = get_config_manager()
_PREFERENCES_THREAD_LOCK = threading.RLock()
_PREFERENCES_LOCK_TIMEOUT_SECONDS = 10


def _get_preferences_read_path() -> str:
    return str(_config_manager.get_config_path('user_preferences.json'))


def _get_preferences_write_path() -> str:
    return str(_config_manager.get_runtime_config_path('user_preferences.json'))


def _get_active_preferences_path() -> str:
    write_path = _get_preferences_write_path()
    if os.path.exists(write_path):
        return write_path
    return _get_preferences_read_path()


@contextmanager
def _locked_preferences_store():
    """Serialize every user_preferences.json read-modify-write."""
    write_path = Path(_get_preferences_write_path())
    write_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = write_path.with_name(f"{write_path.name}.lock")
    with _PREFERENCES_THREAD_LOCK, portalocker.Lock(
        str(lock_path),
        mode="a+",
        timeout=_PREFERENCES_LOCK_TIMEOUT_SECONDS,
    ):
        yield


# 用户偏好文件路径（从配置管理器获取）
PREFERENCES_FILE = _get_active_preferences_path()

def load_user_preferences() -> List[Dict[str, Any]]:
    """
    Load user preferences

    Returns:
        List[Dict[str, Any]]: list of per-model preference entries; empty list when the file is missing or unreadable
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
    """Async version of load_user_preferences: for async endpoints, avoiding sync open()+json.load() blocking the event loop.

    Shares load_user_preferences' dict→list compatibility handling.
    """
    def _sync_load():
        return load_user_preferences()
    return await asyncio.to_thread(_sync_load)


def _save_user_preferences_unlocked(preferences: List[Dict[str, Any]]) -> None:
    """Write an already-prepared preference list while the caller holds the lock."""
    global PREFERENCES_FILE
    PREFERENCES_FILE = _get_preferences_write_path()
    atomic_write_json(PREFERENCES_FILE, preferences, ensure_ascii=False, indent=2)


def save_user_preferences(preferences: List[Dict[str, Any]]) -> bool:
    """
    Save user preferences
    
    Args:
        preferences (List[Dict[str, Any]]): preference list to save
        
    Returns:
        bool: True on success, False on failure
    """
    try:
        assert_cloudsave_writable(_config_manager, operation="save", target="user_preferences.json")
        # 确保配置目录存在
        _config_manager.ensure_config_directory()
        with _locked_preferences_store():
            _save_user_preferences_unlocked(preferences)
        return True
    except MaintenanceModeError:
        raise
    except Exception as e:
        print(f"保存用户偏好失败: {e}")
        return False

def update_model_preferences(model_path: str, position: Dict[str, float], scale: Dict[str, float], parameters: Optional[Dict[str, float]] = None, display: Optional[Dict[str, float]] = None, rotation: Optional[Dict[str, float]] = None, viewport: Optional[Dict[str, float]] = None, camera_position: Optional[Dict[str, float]] = None) -> bool:
    """Update one model without racing another preference-file writer."""
    try:
        assert_cloudsave_writable(_config_manager, operation="save", target="user_preferences.json")
        _config_manager.ensure_config_directory()
        with _locked_preferences_store():
            return _update_model_preferences_unlocked(
                model_path, position, scale, parameters, display, rotation,
                viewport, camera_position,
            )
    except MaintenanceModeError:
        raise
    except Exception as e:
        print(f"更新模型偏好失败: {e}")
        return False


def _update_model_preferences_unlocked(model_path: str, position: Dict[str, float], scale: Dict[str, float], parameters: Optional[Dict[str, float]] = None, display: Optional[Dict[str, float]] = None, rotation: Optional[Dict[str, float]] = None, viewport: Optional[Dict[str, float]] = None, camera_position: Optional[Dict[str, float]] = None) -> bool:
    """
    Update preferences of the given model

    Args:
        model_path (str): model path
        position (Dict[str, float]): position info {'x': float, 'y': float, 'z': float}
        scale (Dict[str, float]): scale info {'x': float, 'y': float, 'z': float}
        parameters (Optional[Dict[str, float]]): model parameters {'paramId': value}
        display (Optional[Dict[str, float]]): display info {'screenX': float, 'screenY': float}, for multi-monitor position restore
        rotation (Optional[Dict[str, float]]): rotation info {'x': float, 'y': float, 'z': float}, for VRM model orientation
        viewport (Optional[Dict[str, float]]): viewport info {'width': float, 'height': float}, for cross-resolution position and scale normalization
        
    Returns:
        bool: True on success, False on failure
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
        
        # 保存更新后的偏好；外层持有覆盖 load→save 的锁。
        _save_user_preferences_unlocked(current_preferences)
        return True
    except Exception as e:
        if isinstance(e, MaintenanceModeError):
            raise
        print(f"更新模型偏好失败: {e}")
        return False

def get_model_preferences(model_path: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """
    Get preferences of the given model; without an argument, returns the preferred model's (first in the list)
    
    Args:
        model_path (str, optional): model path; defaults to the preferred model
        
    Returns:
        Optional[Dict[str, Any]]: dict containing model_path, position, scale; None when absent
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
    Get the preferred model's path
    
    Returns:
        Optional[str]: path of the preferred model, or None when absent
    """
    preferences = load_user_preferences()
    for pref in preferences:
        if pref.get('model_path') != GLOBAL_CONVERSATION_KEY:
            return pref.get('model_path')
    return None

def validate_model_preferences(preferences: Dict[str, Any]) -> bool:
    """
    Validate that model preferences contain the required fields
    
    Args:
        preferences (Dict[str, Any]): model preferences to validate
        
    Returns:
        bool: True when valid, False otherwise
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
    """Move a model while holding the same lock as conversation settings."""
    try:
        assert_cloudsave_writable(_config_manager, operation="save", target="user_preferences.json")
        _config_manager.ensure_config_directory()
        with _locked_preferences_store():
            return _move_model_to_top_unlocked(model_path)
    except MaintenanceModeError:
        raise
    except Exception as e:
        print(f"移动模型到顶部失败: {e}")
        return False


def _move_model_to_top_unlocked(model_path: str) -> bool:
    """
    Move the given model to the top of the list (make it preferred)
    
    Args:
        model_path (str): model path
        
    Returns:
        bool: True on success, False on failure
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
            _save_user_preferences_unlocked(preferences)
            return True
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
    'proactiveMusicEnabled', 'proactiveMemeEnabled', 'proactiveMiniGameInviteEnabled',
    'mergeMessagesEnabled', 'focusModeEnabled', 'focusCognitionEnabled',
    'avatarReactionBubbleEnabled', 'slopFilterEnabled',
    'proactiveChatInterval', 'proactiveVisionInterval', 'subtitleEnabled', 'userLanguage',
    'textGuardMaxLength', 'noiseReductionEnabled', 'independentAsrEnabled',
    'voiceInputResourceOptimizationEnabled'
}
_CONVERSATION_SETTINGS_REVISION_KEY = "_conversation_settings_revision"
_CONVERSATION_SETTINGS_ASR_DECISION_KEY = "_independent_asr_decision"
_LEGACY_ASR_DECISION_WRITER_ID = "server-legacy"
MAX_SAFE_ASR_WRITE_ID = 9_007_199_254_740_991


@dataclass(frozen=True)
class ConversationSettingsSnapshot:
    settings: Dict[str, Any]
    revision: int
    asr_decision: Optional[Dict[str, Any]]


@dataclass(frozen=True)
class ConversationSettingsWriteResult:
    success: bool
    conflict: bool
    snapshot: ConversationSettingsSnapshot


def _normalize_conversation_settings_revision(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _normalize_asr_decision(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    write_id = value.get("writeId")
    writer_id = value.get("writerId")
    decision_value = value.get("value")
    if (
        not isinstance(write_id, int)
        or isinstance(write_id, bool)
        or write_id < 0
        or write_id > MAX_SAFE_ASR_WRITE_ID
        or not isinstance(writer_id, str)
        or not writer_id
        or len(writer_id) > 128
        or not isinstance(decision_value, bool)
    ):
        return None
    return {
        "writeId": write_id,
        "writerId": writer_id,
        "value": decision_value,
    }


def is_valid_asr_decision(value: Any) -> bool:
    return _normalize_asr_decision(value) is not None


def _asr_decision_key(decision: Dict[str, Any]) -> tuple[int, str]:
    return decision["writeId"], decision["writerId"]


def _next_legacy_asr_decision(
    current: Optional[Dict[str, Any]],
    value: bool,
) -> Dict[str, Any]:
    current_write_id = current["writeId"] if current is not None else -1
    return {
        "writeId": max(time.time_ns() // 1_000_000, current_write_id + 1),
        "writerId": _LEGACY_ASR_DECISION_WRITER_ID,
        "value": value,
    }


def _snapshot_from_preferences_data(data: Any) -> ConversationSettingsSnapshot:
    if isinstance(data, dict):
        data = [data]
    if isinstance(data, list):
        for pref in data:
            if isinstance(pref, dict) and pref.get("model_path") == GLOBAL_CONVERSATION_KEY:
                settings = {
                    key: value
                    for key, value in pref.items()
                    if key in _ALLOWED_CONVERSATION_SETTINGS
                }
                decision = _normalize_asr_decision(
                    pref.get(_CONVERSATION_SETTINGS_ASR_DECISION_KEY)
                )
                if (
                    decision is not None
                    and settings.get("independentAsrEnabled") != decision["value"]
                ):
                    decision = None
                return ConversationSettingsSnapshot(
                    settings=settings,
                    revision=_normalize_conversation_settings_revision(
                        pref.get(_CONVERSATION_SETTINGS_REVISION_KEY)
                    ),
                    asr_decision=decision,
                )
    return ConversationSettingsSnapshot(settings={}, revision=0, asr_decision=None)


def _validate_conversation_settings(settings: Dict[str, Any]) -> Dict[str, Any]:
    filtered_settings = {
        key: value
        for key, value in settings.items()
        if key in _ALLOWED_CONVERSATION_SETTINGS
    }
    int_interval_fields = {'proactiveChatInterval', 'proactiveVisionInterval'}
    string_fields = {'userLanguage'}
    int_limit_fields = {'textGuardMaxLength'}
    bool_fields = _ALLOWED_CONVERSATION_SETTINGS - (
        int_interval_fields | string_fields | int_limit_fields
    )

    validated = {}
    for key, value in filtered_settings.items():
        if key in bool_fields:
            if isinstance(value, bool):
                validated[key] = value
        elif key in int_interval_fields:
            if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 3600:
                validated[key] = value
        elif key in string_fields:
            if isinstance(value, str) and value:
                validated[key] = value
        elif key in int_limit_fields:
            if isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 2000:
                validated[key] = value
    return validated


def load_global_conversation_settings_snapshot(
    *, strict: bool = False
) -> ConversationSettingsSnapshot:
    """Load settings together with the server revision and ASR decision token."""
    try:
        global PREFERENCES_FILE
        PREFERENCES_FILE = _get_active_preferences_path()
        if os.path.exists(PREFERENCES_FILE):
            with open(PREFERENCES_FILE, 'r', encoding='utf-8') as f:
                return _snapshot_from_preferences_data(json.load(f))
    except Exception as e:
        if strict:
            raise
        print(f"加载全局对话设置失败: {e}")
    return ConversationSettingsSnapshot(settings={}, revision=0, asr_decision=None)


def load_global_conversation_settings(*, strict: bool = False) -> Dict[str, Any]:
    """
    Load global conversation settings (from the global entry of user_preferences.json)
    Reads the file directly, not via load_user_preferences() (which filters out the sentinel)

    Args:
        strict (bool): re-raise a genuine read/parse failure instead of
            reporting it as "no settings". Callers whose default is *weaker*
            than the persisted choice need this: swallowing an unreadable or
            malformed file returns the same ``{}`` as a file that legitimately
            has no settings yet, so the caller silently picks the default and
            overrides the user's stored preference. An ABSENT file (or one with
            no global entry) is not a failure and still returns ``{}`` under
            strict, so a first run keeps defaulting normally.

    Returns:
        Dict[str, Any]: conversation settings dict; empty dict when absent
    """
    return load_global_conversation_settings_snapshot(strict=strict).settings


async def aload_global_conversation_settings(*, strict: bool = False) -> Dict[str, Any]:
    """Async version of load_global_conversation_settings: for async paths, offloading sync IO."""
    return await asyncio.to_thread(load_global_conversation_settings, strict=strict)


async def aload_global_conversation_settings_snapshot(
    *, strict: bool = False
) -> ConversationSettingsSnapshot:
    """Async wrapper for the versioned conversation-settings read."""
    return await asyncio.to_thread(
        load_global_conversation_settings_snapshot,
        strict=strict,
    )


def load_ui_language_override() -> Optional[str]:
    """Load the optional UI language override from the raw global settings entry."""
    try:
        global PREFERENCES_FILE
        PREFERENCES_FILE = _get_active_preferences_path()
        if os.path.exists(PREFERENCES_FILE):
            with open(PREFERENCES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                for pref in data:
                    if pref.get('model_path') == GLOBAL_CONVERSATION_KEY:
                        value = pref.get('uiLanguage')
                        if isinstance(value, str):
                            value = value.strip()
                            return value or None
    except Exception as e:
        print(f"加载 UI 语言覆盖失败: {e}")
    return None


async def aload_ui_language_override() -> Optional[str]:
    """Async wrapper for ``load_ui_language_override``."""
    return await asyncio.to_thread(load_ui_language_override)


def is_privacy_mode_enabled() -> bool:
    """Whether the frontend "privacy mode" switch is on.

    The internally stored field is ``proactiveVisionEnabled`` (True=allow autonomous
    vision); privacy mode is its inverse. Defaults to False when not yet synced
    (matching the frontend's first-launch behavior: privacy mode off, autonomous
    vision on by default).
    """
    settings = load_global_conversation_settings()
    return not settings.get('proactiveVisionEnabled', True)


async def ais_privacy_mode_enabled() -> bool:
    """Async version of ``is_privacy_mode_enabled``."""
    settings = await aload_global_conversation_settings()
    return not settings.get('proactiveVisionEnabled', True)


def _load_preferences_data_for_write_unlocked() -> List[Dict[str, Any]]:
    """Load the latest write-root snapshot while the caller holds the lock."""
    global PREFERENCES_FILE
    write_path = _get_preferences_write_path()
    read_path = _get_preferences_read_path()
    if os.path.exists(write_path):
        PREFERENCES_FILE = write_path
        with open(write_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    elif os.path.exists(read_path):
        with open(read_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        PREFERENCES_FILE = write_path
    else:
        PREFERENCES_FILE = write_path
        data = []

    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data
    return []


def save_global_conversation_settings_versioned(
    settings: Dict[str, Any],
    *,
    expected_revision: Optional[int] = None,
    asr_decision: Optional[Dict[str, Any]] = None,
) -> ConversationSettingsWriteResult:
    """Compare-and-set a partial conversation-settings update."""
    empty_snapshot = ConversationSettingsSnapshot(
        settings={}, revision=0, asr_decision=None
    )
    try:
        assert_cloudsave_writable(_config_manager, operation="save", target="user_preferences.json")
        _config_manager.ensure_config_directory()
        validated = _validate_conversation_settings(settings)
        incoming_decision = _normalize_asr_decision(asr_decision)
        if (
            incoming_decision is not None
            and validated.get("independentAsrEnabled") != incoming_decision["value"]
        ):
            incoming_decision = None

        with _locked_preferences_store():
            data = _load_preferences_data_for_write_unlocked()
            current = _snapshot_from_preferences_data(data)
            if expected_revision is not None and expected_revision != current.revision:
                return ConversationSettingsWriteResult(
                    success=False, conflict=True, snapshot=current
                )

            if incoming_decision is not None and current.asr_decision is not None:
                incoming_key = _asr_decision_key(incoming_decision)
                current_key = _asr_decision_key(current.asr_decision)
                if incoming_key < current_key or (
                    incoming_key == current_key
                    and incoming_decision["value"] != current.asr_decision["value"]
                ):
                    return ConversationSettingsWriteResult(
                        success=False, conflict=True, snapshot=current
                    )

            global_index = -1
            for index, pref in enumerate(data):
                if isinstance(pref, dict) and pref.get("model_path") == GLOBAL_CONVERSATION_KEY:
                    global_index = index
                    break
            global_pref = data[global_index].copy() if global_index >= 0 else {}
            previous_asr_value = global_pref.get("independentAsrEnabled")
            changed = global_index < 0
            for key, value in validated.items():
                if global_pref.get(key) != value:
                    changed = True
                global_pref[key] = value

            if "independentAsrEnabled" in validated:
                if incoming_decision is not None:
                    if (
                        global_pref.get(_CONVERSATION_SETTINGS_ASR_DECISION_KEY)
                        != incoming_decision
                    ):
                        changed = True
                    global_pref[_CONVERSATION_SETTINGS_ASR_DECISION_KEY] = incoming_decision
                elif previous_asr_value != validated["independentAsrEnabled"]:
                    legacy_decision = _next_legacy_asr_decision(
                        current.asr_decision,
                        validated["independentAsrEnabled"],
                    )
                    if (
                        global_pref.get(_CONVERSATION_SETTINGS_ASR_DECISION_KEY)
                        != legacy_decision
                    ):
                        changed = True
                    global_pref[_CONVERSATION_SETTINGS_ASR_DECISION_KEY] = legacy_decision

            global_pref["model_path"] = GLOBAL_CONVERSATION_KEY
            if changed:
                global_pref[_CONVERSATION_SETTINGS_REVISION_KEY] = current.revision + 1
                if global_index >= 0:
                    data[global_index] = global_pref
                else:
                    data.append(global_pref)
                _save_user_preferences_unlocked(data)

            snapshot = _snapshot_from_preferences_data(data) if changed else current
            return ConversationSettingsWriteResult(
                success=True, conflict=False, snapshot=snapshot
            )
    except MaintenanceModeError:
        raise
    except Exception as e:
        print(f"保存全局对话设置失败: {e}")
        return ConversationSettingsWriteResult(
            success=False, conflict=False, snapshot=empty_snapshot
        )


def save_global_conversation_settings(settings: Dict[str, Any]) -> bool:
    """Backward-compatible unconditional wrapper for internal writers."""
    return save_global_conversation_settings_versioned(settings).success
