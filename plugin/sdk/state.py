"""
插件状态持久化机制

提供 __freezable__ 属性的序列化/反序列化支持。
统一管理插件状态的保存和恢复，支持：
- 自动保存（每次 entry 执行后）
- 手动保存（freeze 时）
- 启动恢复（检测到保存的状态时自动恢复）
- 扩展类型支持（datetime, Enum, dataclass 等）
"""

from pathlib import Path
from typing import Any, Dict, List, Optional, TYPE_CHECKING, Callable
from datetime import datetime, date, timedelta
from enum import Enum
import time

try:
    import ormsgpack as msgpack
    _USE_ORMSGPACK = True
except ImportError:
    import msgpack  # type: ignore
    _USE_ORMSGPACK = False

if TYPE_CHECKING:
    from loguru import Logger as LoguruLogger


# ========== 类型扩展系统 ==========

# 类型标记前缀（用于序列化时标识特殊类型）
_TYPE_TAG = "__neko_type__"
_TYPE_VALUE = "__neko_value__"


def _serialize_extended_type(value: Any) -> Any:
    """将扩展类型转换为可序列化的字典"""
    # datetime
    if isinstance(value, datetime):
        return {
            _TYPE_TAG: "datetime",
            _TYPE_VALUE: value.isoformat(),
        }
    # date
    if isinstance(value, date):
        return {
            _TYPE_TAG: "date",
            _TYPE_VALUE: value.isoformat(),
        }
    # timedelta
    if isinstance(value, timedelta):
        return {
            _TYPE_TAG: "timedelta",
            _TYPE_VALUE: value.total_seconds(),
        }
    # Enum
    if isinstance(value, Enum):
        return {
            _TYPE_TAG: "enum",
            "enum_class": f"{value.__class__.__module__}.{value.__class__.__name__}",
            _TYPE_VALUE: value.value,
        }
    # set -> list
    if isinstance(value, set):
        return {
            _TYPE_TAG: "set",
            _TYPE_VALUE: list(value),
        }
    # frozenset -> list
    if isinstance(value, frozenset):
        return {
            _TYPE_TAG: "frozenset",
            _TYPE_VALUE: list(value),
        }
    # Path
    if isinstance(value, Path):
        return {
            _TYPE_TAG: "path",
            _TYPE_VALUE: str(value),
        }
    return None  # 不是扩展类型


def _deserialize_extended_type(data: Dict[str, Any]) -> Any:
    """将序列化的字典转换回扩展类型"""
    type_tag = data.get(_TYPE_TAG)
    if not type_tag:
        return None  # 不是扩展类型标记
    
    value = data.get(_TYPE_VALUE)
    
    if type_tag == "datetime":
        return datetime.fromisoformat(value)
    if type_tag == "date":
        return date.fromisoformat(value)
    if type_tag == "timedelta":
        return timedelta(seconds=value)
    if type_tag == "set":
        return set(value) if isinstance(value, list) else set()
    if type_tag == "frozenset":
        return frozenset(value) if isinstance(value, list) else frozenset()
    if type_tag == "path":
        return Path(value)
    if type_tag == "enum":
        # 尝试恢复 Enum，如果失败则返回原始值
        try:
            enum_class_path = data.get("enum_class", "")
            if "." in enum_class_path:
                module_name, class_name = enum_class_path.rsplit(".", 1)
                import importlib
                module = importlib.import_module(module_name)
                enum_class = getattr(module, class_name)
                return enum_class(value)
        except Exception:
            pass
        return value  # 回退到原始值
    
    return None  # 未知类型


# 扩展类型列表（用于类型检查）
EXTENDED_TYPES = (datetime, date, timedelta, Enum, set, frozenset, Path)


class PluginStatePersistence:
    """管理 __freezable__ 属性的状态持久化
    
    统一处理插件状态的保存和恢复，替代原来分离的 checkpoint 和 freeze 机制。
    
    支持的类型：
    - 基本类型：str, int, float, bool, None, bytes
    - 容器类型：list, dict, tuple
    - 扩展类型：datetime, date, timedelta, Enum, set, frozenset, Path
    
    插件可以通过实现 __freeze_serialize__ 和 __freeze_deserialize__ 方法
    来支持自定义类型的序列化。
    
    Note:
        此类用于自动管理运行时状态（freeze/unfreeze），适合保存计数器、缓存等。
        如需手动管理持久化数据，请使用 PluginDatabase.kv 或 PluginStore。
    """
    
    # 支持序列化的基本类型
    SERIALIZABLE_TYPES = (str, int, float, bool, type(None), list, dict, tuple, bytes)
    
    # 状态文件版本
    STATE_VERSION = 3  # 升级版本号，支持扩展类型
    
    def __init__(
        self,
        plugin_id: str,
        plugin_dir: Path,
        logger: Optional["LoguruLogger"] = None,
        backend: str = "off",  # "file", "memory", or "off"
    ):
        self.plugin_id = plugin_id
        self.plugin_dir = Path(plugin_dir)
        self.logger = logger
        self.backend = backend.lower() if backend else "off"
        
        # 状态文件路径
        self._state_path = self.plugin_dir / ".plugin_state"
        
        # 内存中的最新状态（用于快速访问）
        self._cached_state: Optional[bytes] = None
        self._cached_state_time: float = 0.0
        
        # 保存计数器（用于统计）
        self._save_count: int = 0
    
    def _is_serializable(self, value: Any, instance: Any = None) -> bool:
        """检查值是否可序列化
        
        Args:
            value: 要检查的值
            instance: 插件实例（用于检查自定义序列化方法）
        """
        # 基本类型
        if isinstance(value, self.SERIALIZABLE_TYPES):
            if isinstance(value, dict):
                return all(
                    isinstance(k, str) and self._is_serializable(v, instance)
                    for k, v in value.items()
                )
            if isinstance(value, (list, tuple)):
                return all(self._is_serializable(item, instance) for item in value)
            return True
        # 扩展类型
        if isinstance(value, EXTENDED_TYPES):
            return True
        # 检查插件是否有自定义序列化方法
        if instance and hasattr(instance, "__freeze_serialize__"):
            return True
        return False
    
    def _serialize(self, data: Dict[str, Any]) -> bytes:
        """序列化数据"""
        if _USE_ORMSGPACK:
            return msgpack.packb(data)
        return msgpack.packb(data, use_bin_type=True)
    
    def _deserialize(self, data: bytes) -> Dict[str, Any]:
        """反序列化数据"""
        if _USE_ORMSGPACK:
            return msgpack.unpackb(data)
        return msgpack.unpackb(data, raw=False)
    
    def _serialize_value(self, key: str, value: Any, instance: Any) -> Any:
        """序列化单个值（支持扩展类型和自定义序列化）"""
        # 1. 检查插件是否有自定义序列化方法
        if hasattr(instance, "__freeze_serialize__"):
            try:
                custom_result = instance.__freeze_serialize__(key, value)
                if custom_result is not None:
                    return custom_result
            except Exception:
                pass
        
        # 2. 检查是否是扩展类型
        extended = _serialize_extended_type(value)
        if extended is not None:
            return extended
        
        # 3. 递归处理容器类型
        if isinstance(value, dict):
            return {
                k: self._serialize_value(f"{key}.{k}", v, instance)
                for k, v in value.items()
            }
        if isinstance(value, (list, tuple)):
            serialized = [self._serialize_value(f"{key}[{i}]", v, instance) for i, v in enumerate(value)]
            return serialized if isinstance(value, list) else tuple(serialized)
        
        # 4. 基本类型直接返回
        return value
    
    def _deserialize_value(self, key: str, value: Any, instance: Any) -> Any:
        """反序列化单个值（支持扩展类型和自定义反序列化）"""
        # 1. 检查是否是扩展类型标记
        if isinstance(value, dict) and _TYPE_TAG in value:
            extended = _deserialize_extended_type(value)
            if extended is not None:
                # 检查插件是否有自定义反序列化方法
                if hasattr(instance, "__freeze_deserialize__"):
                    try:
                        custom_result = instance.__freeze_deserialize__(key, extended)
                        if custom_result is not None:
                            return custom_result
                    except Exception:
                        pass
                return extended
        
        # 2. 检查插件是否有自定义反序列化方法
        if hasattr(instance, "__freeze_deserialize__"):
            try:
                custom_result = instance.__freeze_deserialize__(key, value)
                if custom_result is not None:
                    return custom_result
            except Exception:
                pass
        
        # 3. 递归处理容器类型
        if isinstance(value, dict):
            return {
                k: self._deserialize_value(f"{key}.{k}", v, instance)
                for k, v in value.items()
            }
        if isinstance(value, list):
            return [self._deserialize_value(f"{key}[{i}]", v, instance) for i, v in enumerate(value)]
        
        # 4. 基本类型直接返回
        return value
    
    def collect_attrs(
        self,
        instance: Any,
        freezable_keys: List[str],
    ) -> Dict[str, Any]:
        """从插件实例收集 __freezable__ 声明的属性（支持扩展类型）"""
        snapshot = {}
        for key in freezable_keys:
            if not hasattr(instance, key):
                if self.logger:
                    self.logger.debug(
                        f"[State] Attribute '{key}' not found in plugin {self.plugin_id}"
                    )
                continue
            
            value = getattr(instance, key)
            if self._is_serializable(value, instance):
                # 使用扩展序列化
                snapshot[key] = self._serialize_value(key, value, instance)
            else:
                if self.logger:
                    self.logger.warning(
                        f"[State] Attribute '{key}' is not serializable, skipping"
                    )
        return snapshot
    
    def restore_attrs(
        self,
        instance: Any,
        snapshot: Dict[str, Any],
    ) -> int:
        """将 snapshot 中的属性恢复到插件实例（支持扩展类型）"""
        restored_count = 0
        for key, value in snapshot.items():
            try:
                # 使用扩展反序列化
                restored_value = self._deserialize_value(key, value, instance)
                setattr(instance, key, restored_value)
                restored_count += 1
            except Exception as e:
                if self.logger:
                    self.logger.warning(
                        f"[State] Failed to restore attribute '{key}': {e}"
                    )
        return restored_count
    
    def save(
        self,
        instance: Any,
        freezable_keys: List[str],
        reason: str = "manual",
    ) -> bool:
        """保存插件状态
        
        Args:
            instance: 插件实例
            freezable_keys: 需要保存的属性名列表
            reason: 保存原因（"auto", "manual", "freeze"）
        
        Returns:
            是否保存成功
        """
        # off 模式：不执行任何操作
        if self.backend == "off":
            return True
        
        try:
            snapshot = self.collect_attrs(instance, freezable_keys)
            if not snapshot:
                return True
            
            state_data = {
                "version": self.STATE_VERSION,
                "plugin_id": self.plugin_id,
                "saved_at": time.time(),
                "reason": reason,
                "data": snapshot,
            }
            
            data_bytes = self._serialize(state_data)
            self._save_count += 1
            
            if self.backend == "memory":
                # 保存到内存（通过 GlobalState）
                from plugin.core.state import state
                state.save_frozen_state_memory(self.plugin_id, data_bytes)
                # 同时缓存到本地
                self._cached_state = data_bytes
                self._cached_state_time = time.time()
                if self.logger:
                    self.logger.debug(
                        f"[State] Saved to memory ({reason}): {len(snapshot)} attrs, "
                        f"{len(data_bytes)} bytes"
                    )
            else:
                # 保存到文件
                self._state_path.write_bytes(data_bytes)
                self._cached_state = data_bytes
                self._cached_state_time = time.time()
                if self.logger:
                    self.logger.debug(
                        f"[State] Saved to file ({reason}): {len(snapshot)} attrs, "
                        f"{len(data_bytes)} bytes"
                    )
            
            return True
        except Exception as e:
            if self.logger:
                self.logger.exception(f"[State] Save failed: {e}")
            return False
    
    def load(self, instance: Any) -> bool:
        """加载并恢复插件状态
        
        Args:
            instance: 插件实例
        
        Returns:
            是否成功恢复
        """
        # off 模式：不执行任何操作
        if self.backend == "off":
            return False
        
        try:
            data_bytes = None
            
            if self.backend == "memory":
                # 从内存加载
                from plugin.core.state import state
                data_bytes = state.get_frozen_state_memory(self.plugin_id)
                if not data_bytes:
                    if self.logger:
                        self.logger.debug("[State] No saved state in memory")
                    return False
            else:
                # 从文件加载
                if not self._state_path.exists():
                    if self.logger:
                        self.logger.debug("[State] No saved state file found")
                    return False
                data_bytes = self._state_path.read_bytes()
            
            state_data = self._deserialize(data_bytes)
            
            # 版本检查（支持 v1 和 v2）
            version = state_data.get("version", 0)
            if version not in (1, 2):
                if self.logger:
                    self.logger.warning(
                        f"[State] Unknown state version: {version}"
                    )
                return False
            
            snapshot = state_data.get("data", {})
            restored = self.restore_attrs(instance, snapshot)
            
            source = "memory" if self.backend == "memory" else "file"
            reason = state_data.get("reason", "unknown")
            if self.logger:
                self.logger.info(
                    f"[State] Restored from {source} (saved by {reason}): {restored} attrs"
                )
            return True
        except Exception as e:
            if self.logger:
                self.logger.exception(f"[State] Load failed: {e}")
            return False
    
    def clear(self) -> bool:
        """清除保存的状态"""
        try:
            if self.backend == "memory":
                from plugin.core.state import state
                state.clear_frozen_state_memory(self.plugin_id)
            elif self.backend == "file":
                if self._state_path.exists():
                    self._state_path.unlink()
            
            self._cached_state = None
            self._cached_state_time = 0.0
            return True
        except Exception as e:
            if self.logger:
                self.logger.warning(f"[State] Clear failed: {e}")
            return False
    
    def has_saved_state(self) -> bool:
        """检查是否有保存的状态"""
        if self.backend == "off":
            return False
        if self.backend == "memory":
            from plugin.core.state import state
            return state.has_frozen_state_memory(self.plugin_id)
        return self._state_path.exists()
    
    def get_state_info(self) -> Optional[Dict[str, Any]]:
        """获取保存状态的元信息（不加载数据）"""
        if self.backend == "off":
            return None
        
        try:
            data_bytes = None
            if self.backend == "memory":
                from plugin.core.state import state
                data_bytes = state.get_frozen_state_memory(self.plugin_id)
            elif self._state_path.exists():
                data_bytes = self._state_path.read_bytes()
            
            if not data_bytes:
                return None
            
            state_data = self._deserialize(data_bytes)
            return {
                "version": state_data.get("version"),
                "plugin_id": state_data.get("plugin_id"),
                "saved_at": state_data.get("saved_at"),
                "reason": state_data.get("reason"),
                "data_keys": list(state_data.get("data", {}).keys()),
                "size_bytes": len(data_bytes),
            }
        except Exception:
            return None


# 向后兼容别名
StatePersistence = PluginStatePersistence
FreezableCheckpoint = PluginStatePersistence
