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
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
import json
import os
from pathlib import Path
import time
from typing import Dict, Any, Optional, List

from utils.config_manager import get_config_manager
from utils.cloudsave_runtime import MaintenanceModeError, assert_cloudsave_writable
from utils.conversation_settings_constants import (
    ALLOWED_CONVERSATION_SETTINGS as _ALLOWED_CONVERSATION_SETTINGS,
    ASR_WRITE_ID_MAX_FUTURE_SKEW_MS,
    CONVERSATION_SETTINGS_RESET_KEY,
    INDEPENDENT_ASR_PROVIDER_PREFERENCES as _INDEPENDENT_ASR_PROVIDER_PREFERENCES,
    MAX_SAFE_ASR_WRITE_ID,
    MAX_SAFE_CONVERSATION_SETTINGS_REVISION,
)
from utils.file_utils import atomic_write_json
from utils.preferences_store_lock import (
    PREFERENCES_THREAD_LOCK as _PREFERENCES_THREAD_LOCK,
    PREFERENCES_LOCK_TIMEOUT_SECONDS as _PREFERENCES_LOCK_TIMEOUT_SECONDS,
    preferences_store_lock,
)
from utils.preferences_audit import (
    JOURNAL_KEY as _SETTINGS_JOURNAL_KEY,
    attach_journal as _attach_settings_journal,
    install_settings_attribution_context,
    install_settings_capture_provider,
    prepare_atomic_preferences as _prepare_atomic_preferences,
    proactive_snapshot as _proactive_settings_snapshot,
    read_journal as _read_settings_journal,
    settings_attribution_context as _settings_attribution_context,
    without_journal as _without_settings_journal,
)

# Initialize the configuration manager
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


@contextmanager
def _locked_preferences_store(*, attribute=True, audit_origin="user"):
    """Serialize every user_preferences.json read-modify-write."""
    write_path = Path(_get_preferences_write_path())
    # Account fence must precede the preferences lock. Journal reads/acks do not
    # acquire account state, and cannot reattribute already committed changes.
    attribution = _settings_attribution_context(audit_origin) if attribute else nullcontext()
    with attribution, preferences_store_lock(
        write_path,
        timeout=_PREFERENCES_LOCK_TIMEOUT_SECONDS,
        thread_lock=_PREFERENCES_THREAD_LOCK,
    ):
        yield


# User-preferences file path from the configuration manager
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
                # Legacy compatibility: convert dictionary format to list format
                if isinstance(data, dict):
                    if 'model_path' in data and 'position' in data and 'scale' in data:
                        return _without_settings_journal([data])
                    else:
                        return []
                elif isinstance(data, list):
                    return _without_settings_journal(data)
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
    previous = _load_preferences_data_for_write_unlocked()
    committed = _prepare_atomic_preferences(previous, preferences)
    # Settings and their journal share one temp-file + os.replace commit.
    atomic_write_json(PREFERENCES_FILE, committed, ensure_ascii=False, indent=2)


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
        # Ensure the configuration directory exists
        _config_manager.ensure_config_directory()
        with _locked_preferences_store():
            _save_user_preferences_unlocked(preferences)
        return True
    except MaintenanceModeError:
        raise
    except Exception as e:
        print(f"保存用户偏好失败: {e}")
        return False

def update_model_preferences(model_path: str, position: Dict[str, float], scale: Dict[str, float], parameters: Optional[Dict[str, float]] = None, display: Optional[Dict[str, float]] = None, rotation: Optional[Dict[str, float]] = None, viewport: Optional[Dict[str, float]] = None, camera_position: Optional[Dict[str, float]] = None, *, parameters_mode: Optional[str] = None) -> bool:
    """Update one model without racing another preference-file writer."""
    try:
        assert_cloudsave_writable(_config_manager, operation="save", target="user_preferences.json")
        _config_manager.ensure_config_directory()
        with _locked_preferences_store():
            return _update_model_preferences_unlocked(
                model_path, position, scale, parameters, display, rotation,
                viewport, camera_position, parameters_mode=parameters_mode,
            )
    except MaintenanceModeError:
        raise
    except Exception as e:
        print(f"更新模型偏好失败: {e}")
        return False


def _update_model_preferences_unlocked(model_path: str, position: Dict[str, float], scale: Dict[str, float], parameters: Optional[Dict[str, float]] = None, display: Optional[Dict[str, float]] = None, rotation: Optional[Dict[str, float]] = None, viewport: Optional[Dict[str, float]] = None, camera_position: Optional[Dict[str, float]] = None, *, parameters_mode: Optional[str] = None) -> bool:
    """
    Update preferences of the given model

    Args:
        model_path (str): model path
        position (Dict[str, float]): position info {'x': float, 'y': float, 'z': float}
        scale (Dict[str, float]): scale info {'x': float, 'y': float, 'z': float}
        parameters (Optional[Dict[str, float]]): model parameters {'paramId': value}
        parameters_mode (Optional[str]): 'replace' makes parameters the complete override snapshot
        display (Optional[Dict[str, float]]): display info {'screenX': float, 'screenY': float}, for multi-monitor position restore
        rotation (Optional[Dict[str, float]]): rotation info {'x': float, 'y': float, 'z': float}, for VRM model orientation
        viewport (Optional[Dict[str, float]]): viewport info {'width': float, 'height': float}, for cross-resolution position and scale normalization
        
    Returns:
        bool: True on success, False on failure
    """
    try:
        # Reject reserved keys as model paths to avoid corrupting the global conversation-settings entry
        if model_path == GLOBAL_CONVERSATION_KEY:
            print(f"拒绝更新模型偏好：model_path 不能使用保留键 '{GLOBAL_CONVERSATION_KEY}'")
            return False

        # Load existing preferences
        current_preferences = load_user_preferences()
        
        # Find existing preferences for this model, skipping the sentinel
        model_index = -1
        for i, pref in enumerate(current_preferences):
            if pref.get('model_path') != GLOBAL_CONVERSATION_KEY and pref.get('model_path') == model_path:
                model_index = i
                break
        
        # Create new model preferences
        new_model_pref = {
            'model_path': model_path,
            'position': position,
            'scale': scale
        }
        
        # Add parameters if present
        if parameters is not None:
            new_model_pref['parameters'] = parameters
        if parameters_mode is not None:
            new_model_pref['parameters_mode'] = parameters_mode

        # Add monitor information if present, for multi-monitor position restoration
        if display is not None:
            new_model_pref['display'] = display

        # [New] Add rotation information if present, for VRM model orientation
        if rotation is not None:
            new_model_pref['rotation'] = rotation

        # Add viewport information if present, for position/scale normalization across resolutions
        if viewport is not None:
            new_model_pref['viewport'] = viewport

        # Add camera position if present, for restoring VRM wheel-zoom state
        if camera_position is not None:
            new_model_pref['camera_position'] = camera_position
        
        if model_index >= 0:
            # Update existing model preferences, retaining parameters when the new parameters are None
            existing_pref = current_preferences[model_index]
            if parameters is not None:
                existing_pref['parameters'] = parameters
            elif 'parameters' in existing_pref:
                # Retain existing parameters
                new_model_pref['parameters'] = existing_pref['parameters']
            if parameters_mode is None and 'parameters_mode' in existing_pref:
                # Position/scale saves must not revive the legacy directory mirror.
                new_model_pref['parameters_mode'] = existing_pref['parameters_mode']
            # Handle monitor information
            if display is not None:
                pass  # Already added to new_model_pref above
            elif 'display' in existing_pref:
                # Retain existing monitor information
                new_model_pref['display'] = existing_pref['display']
            # [New] Handle rotation information
            if rotation is not None:
                pass  # Already added to new_model_pref above
            elif 'rotation' in existing_pref:
                # Retain existing rotation information
                new_model_pref['rotation'] = existing_pref['rotation']
            # Handle viewport information
            if viewport is not None:
                pass  # Already added to new_model_pref above
            elif 'viewport' in existing_pref:
                # Retain existing viewport information
                new_model_pref['viewport'] = existing_pref['viewport']
            # Handle camera-position information
            if camera_position is not None:
                pass  # Already added to new_model_pref above
            elif 'camera_position' in existing_pref:
                # Retain existing camera-position information
                new_model_pref['camera_position'] = existing_pref['camera_position']
            current_preferences[model_index] = new_model_pref
        else:
            # Add new model preferences to the beginning of the list as the preferred model
            current_preferences.insert(0, new_model_pref)
        
        # Save updated preferences; the outer scope holds the lock across load→save.
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
        # Find preferences for the specified model
        for pref in preferences:
            if pref.get('model_path') == model_path:
                return pref
        return None
    else:
        # Return preferences for the preferred model (first in the list), skipping the sentinel
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
    
    # Check required fields
    for field in required_fields:
        if field not in preferences:
            return False
    
    # Check that position and scale contain required subfields
    if not isinstance(preferences.get('position'), dict) or 'x' not in preferences['position'] or 'y' not in preferences['position']:
        return False
    
    if not isinstance(preferences.get('scale'), dict) or 'x' not in preferences['scale'] or 'y' not in preferences['scale']:
        return False
    
    # parameters is optional, but must be a dictionary when present
    if 'parameters' in preferences and not isinstance(preferences['parameters'], dict):
        return False

    if 'parameters_mode' in preferences and (
        preferences['parameters_mode'] != 'replace' or 'parameters' not in preferences
    ):
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
        
        # Find the model index, skipping the sentinel
        model_index = -1
        for i, pref in enumerate(preferences):
            if pref.get('model_path') != GLOBAL_CONVERSATION_KEY and pref.get('model_path') == model_path:
                model_index = i
                break
        
        if model_index >= 0:
            # Move the model to the top
            model_pref = preferences.pop(model_index)
            preferences.insert(0, model_pref)
            _save_user_preferences_unlocked(preferences)
            return True
        else:
            # Return False if the model does not exist
            return False
    except Exception as e:
        if isinstance(e, MaintenanceModeError):
            raise
        print(f"移动模型到顶部失败: {e}")
        return False


# ========== Global conversation settings (localStorage synchronized backup) ==========

GLOBAL_CONVERSATION_KEY = "__global_conversation__"

_CONVERSATION_SETTINGS_REVISION_KEY = "_conversation_settings_revision"
_CONVERSATION_SETTINGS_ASR_DECISION_KEY = "_independent_asr_decision"
_LEGACY_ASR_DECISION_WRITER_ID = "server-legacy"
@dataclass(frozen=True)
class ConversationSettingsSnapshot:
    settings: Dict[str, Any]
    revision: int
    asr_decision: Optional[Dict[str, Any]]
    reset: bool = False


@dataclass(frozen=True)
class ConversationSettingsWriteResult:
    success: bool
    conflict: bool
    snapshot: ConversationSettingsSnapshot
    # Pre-save snapshot read under the same lock (available only after a successful write), allowing callers to detect switch transitions
    previous: Optional[ConversationSettingsSnapshot] = None


def _normalize_conversation_settings_revision(value: Any) -> int:
    if (
        isinstance(value, int)
        and not isinstance(value, bool)
        and 0 <= value <= MAX_SAFE_CONVERSATION_SETTINGS_REVISION
    ):
        return value
    return 0


def _normalize_asr_decision(value: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(value, dict):
        return None
    write_id = value.get("writeId")
    writer_id = value.get("writerId")
    decision_value = value.get("value")
    max_accepted_write_id = min(
        MAX_SAFE_ASR_WRITE_ID - 1,
        time.time_ns() // 1_000_000 + ASR_WRITE_ID_MAX_FUTURE_SKEW_MS,
    )
    if (
        not isinstance(write_id, int)
        or isinstance(write_id, bool)
        or write_id < 0
        or write_id > max_accepted_write_id
        or not isinstance(writer_id, str)
        or not writer_id
        or len(writer_id) > 128
        or not writer_id.isascii()
        or not writer_id.isprintable()
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
) -> Optional[Dict[str, Any]]:
    current_write_id = current["writeId"] if current is not None else -1
    now_ms = time.time_ns() // 1_000_000
    max_accepted_write_id = min(
        MAX_SAFE_ASR_WRITE_ID - 1,
        now_ms + ASR_WRITE_ID_MAX_FUTURE_SKEW_MS,
    )
    decision = {
        "writeId": min(
            max(now_ms, current_write_id + 1),
            max_accepted_write_id,
        ),
        "writerId": _LEGACY_ASR_DECISION_WRITER_ID,
        "value": value,
    }
    if current is not None and _asr_decision_key(decision) <= _asr_decision_key(current):
        return None
    return decision


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
                    reset=pref.get(CONVERSATION_SETTINGS_RESET_KEY) is True,
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
    choice_fields = {
        'independentAsrProviderPreference': _INDEPENDENT_ASR_PROVIDER_PREFERENCES,
    }
    bool_fields = _ALLOWED_CONVERSATION_SETTINGS - (
        int_interval_fields | string_fields | int_limit_fields | set(choice_fields)
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
        elif key in choice_fields:
            if isinstance(value, str) and value in choice_fields[key]:
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


def save_ui_language_override(language: Optional[str]) -> bool:
    """Persist the optional UI locale without mixing it into conversation settings.

    ``uiLanguage`` deliberately lives beside the global conversation entry rather
    than inside its validated settings payload.  This keeps UI copy selection
    independent from the per-character prompt locale while still making the
    desktop tray choice available to every renderer on the next navigation.
    Passing ``None`` removes the manual override.
    """
    try:
        assert_cloudsave_writable(
            _config_manager,
            operation="save",
            target="user_preferences.json",
        )
        _config_manager.ensure_config_directory()
        normalized = str(language or "").strip() or None
        with _locked_preferences_store():
            data = _load_preferences_data_for_write_unlocked()
            global_index = -1
            for index, pref in enumerate(data):
                if (
                    isinstance(pref, dict)
                    and pref.get("model_path") == GLOBAL_CONVERSATION_KEY
                ):
                    global_index = index
                    break

            global_pref = data[global_index].copy() if global_index >= 0 else {
                "model_path": GLOBAL_CONVERSATION_KEY,
            }
            if normalized is None:
                global_pref.pop("uiLanguage", None)
            else:
                global_pref["uiLanguage"] = normalized

            if global_index >= 0:
                data[global_index] = global_pref
            else:
                data.append(global_pref)
            _save_user_preferences_unlocked(data)
        return True
    except MaintenanceModeError:
        raise
    except Exception as e:
        print(f"保存 UI 语言覆盖失败: {e}")
        return False


async def asave_ui_language_override(language: Optional[str]) -> bool:
    """Async wrapper for ``save_ui_language_override``."""
    return await asyncio.to_thread(save_ui_language_override, language)


async def aload_ui_language_override() -> Optional[str]:
    """Async wrapper for ``load_ui_language_override``."""
    return await asyncio.to_thread(load_ui_language_override)


def load_global_entry_flags() -> Dict[str, Any]:
    """Read every raw field stored beside the global conversation entry in one file read.

    Small UI switches live next to ``model_path`` rather than inside the validated
    conversation payload, so loading them must not pay one file read per key.
    """

    try:
        global PREFERENCES_FILE
        PREFERENCES_FILE = _get_active_preferences_path()
        if os.path.exists(PREFERENCES_FILE):
            with open(PREFERENCES_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                for pref in data:
                    if isinstance(pref, dict) and pref.get("model_path") == GLOBAL_CONVERSATION_KEY:
                        return {k: v for k, v in pref.items() if k not in {"model_path", _SETTINGS_JOURNAL_KEY}}
    except Exception as e:
        print(f"加载全局开关集合失败: {e}")
    return {}


def save_global_entry_flags(values: Dict[str, Any]) -> bool:
    """Merge the given flags into the global conversation entry in a single locked write."""

    try:
        assert_cloudsave_writable(_config_manager, operation="save", target="user_preferences.json")
        _config_manager.ensure_config_directory()
        with _locked_preferences_store():
            data = _load_preferences_data_for_write_unlocked()
            global_index = -1
            for index, pref in enumerate(data):
                if isinstance(pref, dict) and pref.get("model_path") == GLOBAL_CONVERSATION_KEY:
                    global_index = index
                    break
            global_pref = data[global_index].copy() if global_index >= 0 else {"model_path": GLOBAL_CONVERSATION_KEY}
            for key, value in values.items():
                if value is None:
                    global_pref.pop(key, None)
                else:
                    global_pref[key] = value
            if global_index >= 0:
                data[global_index] = global_pref
            else:
                data.append(global_pref)
            _save_user_preferences_unlocked(data)
        return True
    except MaintenanceModeError:
        raise
    except Exception as e:
        print(f"保存全局开关集合失败: {e}")
        return False


async def aload_global_entry_flags() -> Dict[str, Any]:
    """Async wrapper for ``load_global_entry_flags``."""

    return await asyncio.to_thread(load_global_entry_flags)


async def asave_global_entry_flags(values: Dict[str, Any]) -> bool:
    """Async wrapper for ``save_global_entry_flags``."""

    return await asyncio.to_thread(save_global_entry_flags, values)


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
    full_snapshot: bool = False,
    audit_origin: str = "user",
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

        with _locked_preferences_store(audit_origin=audit_origin):
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
            if (
                full_snapshot
                and global_pref.pop(CONVERSATION_SETTINGS_RESET_KEY, None) is not None
            ):
                changed = True
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
                    if legacy_decision is None:
                        return ConversationSettingsWriteResult(
                            success=False,
                            conflict=True,
                            snapshot=current,
                        )
                    if (
                        global_pref.get(_CONVERSATION_SETTINGS_ASR_DECISION_KEY)
                        != legacy_decision
                    ):
                        changed = True
                    global_pref[_CONVERSATION_SETTINGS_ASR_DECISION_KEY] = legacy_decision

            global_pref["model_path"] = GLOBAL_CONVERSATION_KEY
            if changed:
                if current.revision >= MAX_SAFE_CONVERSATION_SETTINGS_REVISION:
                    return ConversationSettingsWriteResult(
                        success=False,
                        conflict=True,
                        snapshot=current,
                    )
                global_pref[_CONVERSATION_SETTINGS_REVISION_KEY] = current.revision + 1
                if global_index >= 0:
                    data[global_index] = global_pref
                else:
                    data.append(global_pref)
                _save_user_preferences_unlocked(data)

            snapshot = _snapshot_from_preferences_data(data) if changed else current
            return ConversationSettingsWriteResult(
                success=True, conflict=False, snapshot=snapshot, previous=current
            )
    except MaintenanceModeError:
        raise
    except Exception as e:
        print(f"保存全局对话设置失败: {e}")
        return ConversationSettingsWriteResult(
            success=False, conflict=False, snapshot=empty_snapshot
        )


def save_global_conversation_settings(settings: Dict[str, Any], *, audit_origin: str = "user") -> bool:
    """Backward-compatible unconditional wrapper for internal writers."""
    return save_global_conversation_settings_versioned(settings, audit_origin=audit_origin).success


def pending_settings_changes(limit: int = 100) -> List[Dict[str, Any]]:
    """Read committed changes for all original owners; never bind anonymous history."""
    if type(limit) is not int or not 1 <= limit <= 4096:
        raise ValueError("Settings journal limit must be between 1 and 4096")
    with _locked_preferences_store(attribute=False):
        return _read_settings_journal(_load_preferences_data_for_write_unlocked())["entries"][:limit]


def settings_capture_status() -> Dict[str, Any]:
    """Minimal local diagnostic; no raw entries or values leave this boundary."""
    with _locked_preferences_store(attribute=False):
        journal = _read_settings_journal(_load_preferences_data_for_write_unlocked())
        return {"scope": "all_local_accounts", "statistics_incomplete": bool(journal.get("incomplete")),
                "protocol_version": journal.get("protocol_version", 1)}


def reserve_current_settings_snapshot(owner, capture_provider) -> Dict[str, Any]:
    """Reserve the shared revision without changing any current setting value.

    Composition must already hold the credential attribution fence. This uses
    only the preferences lock, preserving credential -> preferences -> SQLite
    ordering. The snapshot has no custody promise until Store admission.
    """
    assert_cloudsave_writable(_config_manager, operation="save", target="user_preferences.json")
    with _locked_preferences_store(attribute=False):
        data = _load_preferences_data_for_write_unlocked()
        journal = _read_settings_journal(data)
        if journal["revision"] >= 2**53 - 1:
            raise ValueError("Settings revision exhausted")
        context = capture_provider(owner)
        if not isinstance(context, dict) or context.get("owner_key") != owner.get("key"):
            raise ValueError("Current settings capture unavailable")
        snapshot = _proactive_settings_snapshot(data)
        journal["revision"] += 1
        committed = _attach_settings_journal(data, journal)
        atomic_write_json(_get_preferences_write_path(), committed, ensure_ascii=False, indent=2)
        return {"context": context, "payload": {"revision": journal["revision"], "origin": "baseline",
                "occurred_at": time.time(), "previous": snapshot, "settings": snapshot}}


def acknowledge_settings_changes(ids) -> int:
    """Remove only IDs already durably handed to an outbox, or confirmed by cloud.

    This function is synchronous; asynchronous workers must offload it to a
    thread. A failed atomic write propagates and leaves the original journal.
    """
    confirmed = set(ids)
    if not confirmed or any(not isinstance(value, str) for value in confirmed):
        if confirmed:
            raise ValueError("Settings acknowledgement IDs must be strings")
        return 0
    assert_cloudsave_writable(_config_manager, operation="save", target="user_preferences.json")
    with _locked_preferences_store(attribute=False):
        data = _load_preferences_data_for_write_unlocked()
        journal = _read_settings_journal(data)
        remaining = [entry for entry in journal["entries"] if entry["id"] not in confirmed]
        count = len(journal["entries"]) - len(remaining)
        if count:
            journal["entries"] = remaining
            committed = _attach_settings_journal(data, journal)
            atomic_write_json(_get_preferences_write_path(), committed, ensure_ascii=False, indent=2)
        return count


async def apending_settings_changes(limit: int = 100) -> List[Dict[str, Any]]:
    return await asyncio.to_thread(pending_settings_changes, limit)


async def aacknowledge_settings_changes(ids) -> int:
    return await asyncio.to_thread(acknowledge_settings_changes, ids)


async def asave_global_conversation_settings(settings: Dict[str, Any], *, audit_origin: str = "user") -> bool:
    return await asyncio.to_thread(save_global_conversation_settings, settings, audit_origin=audit_origin)


async def asave_global_conversation_settings_versioned(settings: Dict[str, Any], **kwargs) -> ConversationSettingsWriteResult:
    return await asyncio.to_thread(save_global_conversation_settings_versioned, settings, **kwargs)
