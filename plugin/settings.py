"""
Plugin System Configuration

统一管理插件系统的所有配置项，包括：
- 队列配置
- 超时配置
- 路径配置
- SDK元数据属性
- 线程池配置
- 消息队列配置
"""
import os
from pathlib import Path
from typing import Dict, Any


def _get_bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.lower() in ("true", "1", "yes", "on")


def _get_int_env(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except Exception:
        return default


def _get_float_env(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except Exception:
        return default


# ========== 路径配置 ==========

def get_plugin_config_root() -> Path:
    """
    获取插件配置根目录
    
    默认路径：plugin/plugins
    可以通过环境变量 PLUGIN_CONFIG_ROOT 覆盖
    """
    custom_path = os.getenv("PLUGIN_CONFIG_ROOT")
    if custom_path:
        # 支持 ~ 和相对路径，统一解析为绝对路径
        return Path(custom_path).expanduser().resolve()
    # 默认路径：相对于 plugin 目录
    return Path(__file__).parent / "plugins"


PLUGIN_CONFIG_ROOT = get_plugin_config_root()


# ========== 队列配置 ==========

# 事件队列最大容量
EVENT_QUEUE_MAX = _get_int_env("NEKO_EVENT_QUEUE_MAX", 1000)

# 生命周期队列最大容量
LIFECYCLE_QUEUE_MAX = _get_int_env("NEKO_LIFECYCLE_QUEUE_MAX", 1000)

# 消息队列最大容量
MESSAGE_QUEUE_MAX = _get_int_env("NEKO_MESSAGE_QUEUE_MAX", 1000)


# ========== 超时配置（秒） ==========

# 插件执行超时（trigger_plugin）
PLUGIN_EXECUTION_TIMEOUT = _get_float_env("NEKO_PLUGIN_EXECUTION_TIMEOUT", 30.0)

# 插件触发超时（host.trigger）
PLUGIN_TRIGGER_TIMEOUT = _get_float_env("NEKO_PLUGIN_TRIGGER_TIMEOUT", 10.0)

# 插件关闭超时（shutdown）
PLUGIN_SHUTDOWN_TIMEOUT = _get_float_env("NEKO_PLUGIN_SHUTDOWN_TIMEOUT", 5.0)

# 插件全局关闭超时（秒）
_shutdown_total_timeout_str = os.getenv("PLUGIN_SHUTDOWN_TOTAL_TIMEOUT", os.getenv("NEKO_PLUGIN_SHUTDOWN_TOTAL_TIMEOUT", "30"))
try:
    PLUGIN_SHUTDOWN_TOTAL_TIMEOUT = int(_shutdown_total_timeout_str)
except ValueError:
    PLUGIN_SHUTDOWN_TOTAL_TIMEOUT = 30  # 默认值

# 队列操作超时（queue.get）
QUEUE_GET_TIMEOUT = _get_float_env("NEKO_QUEUE_GET_TIMEOUT", 1.0)

# 插件 SDK 同步轮询响应间隔（秒）
# bus.*.get 等接口使用该间隔轮询共享 response_map；调小可降低延迟抖动、提升吞吐，但会增加 CPU。
BUS_SDK_POLL_INTERVAL_SECONDS = _get_float_env("NEKO_BUS_SDK_POLL_INTERVAL_SECONDS", 0.002)

# 状态消费关闭超时
STATUS_CONSUMER_SHUTDOWN_TIMEOUT = _get_float_env("NEKO_STATUS_CONSUMER_SHUTDOWN_TIMEOUT", 5.0)

# 进程关闭超时
PROCESS_SHUTDOWN_TIMEOUT = _get_float_env("NEKO_PROCESS_SHUTDOWN_TIMEOUT", 5.0)

# 进程强制终止超时
PROCESS_TERMINATE_TIMEOUT = _get_float_env("NEKO_PROCESS_TERMINATE_TIMEOUT", 1.0)


# ========== 线程池配置 ==========

# 通信资源管理器的线程池最大工作线程数
# 根据CPU核心数动态设置，适合I/O密集型操作
# 公式：min(4, CPU核心数 + 2)，确保至少有足够的并发能力
COMMUNICATION_THREAD_POOL_MAX_WORKERS = min(4, (os.cpu_count() or 1) + 2)


# ========== 消息队列配置 ==========

# 获取消息时的默认最大数量
MESSAGE_QUEUE_DEFAULT_MAX_COUNT = _get_int_env("NEKO_MESSAGE_QUEUE_DEFAULT_MAX_COUNT", 100)

# 状态消息获取时的默认最大数量
STATUS_MESSAGE_DEFAULT_MAX_COUNT = _get_int_env("NEKO_STATUS_MESSAGE_DEFAULT_MAX_COUNT", 100)


# ========== SDK 元数据属性 ==========

# 插件元数据属性名（用于标记插件类）
NEKO_PLUGIN_META_ATTR = "__neko_plugin_meta__"

# 插件标签（用于标记插件类）
NEKO_PLUGIN_TAG = "__neko_plugin__"

# 事件元数据属性名（用于标记事件处理器）
EVENT_META_ATTR = "__neko_event_meta__"


# ========== 其他配置 ==========

# 状态消费任务的休眠间隔（秒）
STATUS_CONSUMER_SLEEP_INTERVAL = 0.1

# 消息消费任务的休眠间隔（秒）
MESSAGE_CONSUMER_SLEEP_INTERVAL = 0.1

# 结果消费任务的休眠间隔（秒）
RESULT_CONSUMER_SLEEP_INTERVAL = 0.1

# 是否打印插件消息转发日志（[MESSAGE FORWARD]）
PLUGIN_LOG_MESSAGE_FORWARD = _get_bool_env("NEKO_PLUGIN_LOG_MESSAGE_FORWARD", True)
# 是否打印插件同步调用告警（Sync call '... may block ...'）
PLUGIN_LOG_SYNC_CALL_WARNINGS = _get_bool_env("NEKO_PLUGIN_LOG_SYNC_CALL_WARNINGS", True)

PLUGIN_LOG_BUS_SUBSCRIPTIONS = _get_bool_env("NEKO_PLUGIN_LOG_BUS_SUBSCRIPTIONS", True)
PLUGIN_LOG_BUS_SUBSCRIBE_REQUESTS = _get_bool_env("NEKO_PLUGIN_LOG_BUS_SUBSCRIBE_REQUESTS", True)
PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS = _get_bool_env("NEKO_PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS", True)
PLUGIN_LOG_CTX_STATUS_UPDATE = _get_bool_env("NEKO_PLUGIN_LOG_CTX_STATUS_UPDATE", True)
PLUGIN_LOG_CTX_MESSAGE_PUSH = _get_bool_env("NEKO_PLUGIN_LOG_CTX_MESSAGE_PUSH", True)
PLUGIN_LOG_SERVER_DEBUG = _get_bool_env("NEKO_PLUGIN_LOG_SERVER_DEBUG", False)
PLUGIN_ZMQ_IPC_ENABLED = _get_bool_env("NEKO_PLUGIN_ZMQ_IPC_ENABLED", False)
PLUGIN_ZMQ_IPC_ENDPOINT = os.getenv("NEKO_PLUGIN_ZMQ_IPC_ENDPOINT", "tcp://127.0.0.1:38765")

PLUGIN_ZMQ_MESSAGE_PUSH_SYNC_TIMEOUT = float(os.getenv("NEKO_PLUGIN_ZMQ_MESSAGE_PUSH_SYNC_TIMEOUT", "3600.0") or 3600.0)
PLUGIN_ZMQ_MESSAGE_PUSH_ENDPOINT = os.getenv("NEKO_PLUGIN_ZMQ_MESSAGE_PUSH_ENDPOINT", "tcp://127.0.0.1:38766")
PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE = _get_int_env("NEKO_PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE", 256)
PLUGIN_ZMQ_MESSAGE_PUSH_FLUSH_INTERVAL_MS = _get_int_env("NEKO_PLUGIN_ZMQ_MESSAGE_PUSH_FLUSH_INTERVAL_MS", 5)

PLUGIN_LOG_HTTP_PLUGIN_TRIGGER = _get_bool_env("NEKO_PLUGIN_LOG_HTTP_PLUGIN_TRIGGER", False)

# 同步调用在 handler 中的全局策略（warn / reject）
_sync_policy = os.getenv("NEKO_PLUGIN_SYNC_CALL_POLICY", "warn").lower()
if _sync_policy not in ("warn", "reject"):
    _sync_policy = "warn"
SYNC_CALL_IN_HANDLER_POLICY = _sync_policy

# ========== 插件Logger配置 ==========

# 插件文件日志默认配置（使用loguru）
# 默认日志级别（字符串格式，loguru使用）
PLUGIN_LOG_LEVEL = "INFO"

# 单个日志文件最大大小（字节），默认5MB
PLUGIN_LOG_MAX_BYTES = 5 * 1024 * 1024

# 保留的备份文件数量，默认10个
PLUGIN_LOG_BACKUP_COUNT = 10

# 最多保留的日志文件总数（包括当前和备份），默认20个
PLUGIN_LOG_MAX_FILES = 20


# ========== 插件加载配置 ==========

# 是否启用依赖检查（默认：True）
# 如果设置为 False，将跳过所有插件依赖检查，允许加载不满足依赖的插件
PLUGIN_ENABLE_DEPENDENCY_CHECK = os.getenv("PLUGIN_ENABLE_DEPENDENCY_CHECK", "false").lower() in ("true", "1", "yes")

# 是否启用 ID 冲突检查（默认：True）
# 如果设置为 False，将跳过所有插件 ID 冲突检查，允许使用相同 ID 的插件（可能导致不可预期行为）
PLUGIN_ENABLE_ID_CONFLICT_CHECK = os.getenv("PLUGIN_ENABLE_ID_CONFLICT_CHECK", "false").lower() in ("true", "1", "yes")


# ========== 配置验证 ==========

def validate_config() -> None:
    """
    验证配置的有效性
    
    硬校验：模块导入时即验证并抛出异常，避免启动后才发现配置非法。
    如未来改为运行时可配置，请同步调整校验时机和策略。
    
    Raises:
        ValueError: 如果配置无效
    """
    if EVENT_QUEUE_MAX <= 0:
        raise ValueError("EVENT_QUEUE_MAX must be positive")
    if EVENT_QUEUE_MAX > 1000000:
        raise ValueError("EVENT_QUEUE_MAX is unreasonably large (max: 1000000)")

    if LIFECYCLE_QUEUE_MAX <= 0:
        raise ValueError("LIFECYCLE_QUEUE_MAX must be positive")
    if LIFECYCLE_QUEUE_MAX > 1000000:
        raise ValueError("LIFECYCLE_QUEUE_MAX is unreasonably large (max: 1000000)")
    
    if MESSAGE_QUEUE_MAX <= 0:
        raise ValueError("MESSAGE_QUEUE_MAX must be positive")
    if MESSAGE_QUEUE_MAX > 1000000:
        raise ValueError("MESSAGE_QUEUE_MAX is unreasonably large (max: 1000000)")
    
    if PLUGIN_EXECUTION_TIMEOUT <= 0:
        raise ValueError("PLUGIN_EXECUTION_TIMEOUT must be positive")
    if PLUGIN_EXECUTION_TIMEOUT > 3600:
        raise ValueError("PLUGIN_EXECUTION_TIMEOUT is unreasonably large (max: 3600s)")
    
    if PLUGIN_TRIGGER_TIMEOUT <= 0:
        raise ValueError("PLUGIN_TRIGGER_TIMEOUT must be positive")
    if PLUGIN_TRIGGER_TIMEOUT > 3600:
        raise ValueError("PLUGIN_TRIGGER_TIMEOUT is unreasonably large (max: 3600s)")
    
    if PLUGIN_SHUTDOWN_TIMEOUT <= 0:
        raise ValueError("PLUGIN_SHUTDOWN_TIMEOUT must be positive")
    if PLUGIN_SHUTDOWN_TIMEOUT > 300:
        raise ValueError("PLUGIN_SHUTDOWN_TIMEOUT is unreasonably large (max: 300s)")
    
    if PLUGIN_SHUTDOWN_TOTAL_TIMEOUT <= 0:
        raise ValueError("PLUGIN_SHUTDOWN_TOTAL_TIMEOUT must be positive")
    if PLUGIN_SHUTDOWN_TOTAL_TIMEOUT > 300:
        raise ValueError("PLUGIN_SHUTDOWN_TOTAL_TIMEOUT is unreasonably large (max: 300s)")

    if QUEUE_GET_TIMEOUT <= 0:
        raise ValueError("QUEUE_GET_TIMEOUT must be positive")
    if QUEUE_GET_TIMEOUT > 60:
        raise ValueError("QUEUE_GET_TIMEOUT is unreasonably large (max: 60s)")

    if BUS_SDK_POLL_INTERVAL_SECONDS < 0:
        raise ValueError("BUS_SDK_POLL_INTERVAL_SECONDS must be >= 0")
    if BUS_SDK_POLL_INTERVAL_SECONDS > 1:
        raise ValueError("BUS_SDK_POLL_INTERVAL_SECONDS is unreasonably large (max: 1s)")

    if STATUS_CONSUMER_SHUTDOWN_TIMEOUT <= 0:
        raise ValueError("STATUS_CONSUMER_SHUTDOWN_TIMEOUT must be positive")
    if STATUS_CONSUMER_SHUTDOWN_TIMEOUT > 300:
        raise ValueError("STATUS_CONSUMER_SHUTDOWN_TIMEOUT is unreasonably large (max: 300s)")

    if PROCESS_SHUTDOWN_TIMEOUT <= 0:
        raise ValueError("PROCESS_SHUTDOWN_TIMEOUT must be positive")
    if PROCESS_SHUTDOWN_TIMEOUT > 300:
        raise ValueError("PROCESS_SHUTDOWN_TIMEOUT is unreasonably large (max: 300s)")

    if PROCESS_TERMINATE_TIMEOUT <= 0:
        raise ValueError("PROCESS_TERMINATE_TIMEOUT must be positive")
    if PROCESS_TERMINATE_TIMEOUT > 60:
        raise ValueError("PROCESS_TERMINATE_TIMEOUT is unreasonably large (max: 60s)")
    
    if COMMUNICATION_THREAD_POOL_MAX_WORKERS <= 0:
        raise ValueError("COMMUNICATION_THREAD_POOL_MAX_WORKERS must be positive")
    if COMMUNICATION_THREAD_POOL_MAX_WORKERS > 100:
        raise ValueError("COMMUNICATION_THREAD_POOL_MAX_WORKERS is unreasonably large (max: 100)")
    
    if MESSAGE_QUEUE_DEFAULT_MAX_COUNT <= 0:
        raise ValueError("MESSAGE_QUEUE_DEFAULT_MAX_COUNT must be positive")
    if MESSAGE_QUEUE_DEFAULT_MAX_COUNT > 10000:
        raise ValueError("MESSAGE_QUEUE_DEFAULT_MAX_COUNT is unreasonably large (max: 10000)")
    
    if STATUS_MESSAGE_DEFAULT_MAX_COUNT <= 0:
        raise ValueError("STATUS_MESSAGE_DEFAULT_MAX_COUNT must be positive")
    if STATUS_MESSAGE_DEFAULT_MAX_COUNT > 10000:
        raise ValueError("STATUS_MESSAGE_DEFAULT_MAX_COUNT is unreasonably large (max: 10000)")


# 在模块加载时验证配置
validate_config()


# ========== 导出 ==========

__all__ = [
    # 路径配置
    "PLUGIN_CONFIG_ROOT",
    "get_plugin_config_root",
    
    # 队列配置
    "EVENT_QUEUE_MAX",
    "LIFECYCLE_QUEUE_MAX",
    "MESSAGE_QUEUE_MAX",
    
    # 超时配置
    "PLUGIN_EXECUTION_TIMEOUT",
    "PLUGIN_TRIGGER_TIMEOUT",
    "PLUGIN_SHUTDOWN_TIMEOUT",
    "PLUGIN_SHUTDOWN_TOTAL_TIMEOUT",
    "QUEUE_GET_TIMEOUT",
    "BUS_SDK_POLL_INTERVAL_SECONDS",
    "STATUS_CONSUMER_SHUTDOWN_TIMEOUT",
    "PROCESS_SHUTDOWN_TIMEOUT",
    "PROCESS_TERMINATE_TIMEOUT",
    
    # 线程池配置
    "COMMUNICATION_THREAD_POOL_MAX_WORKERS",
    
    # 消息队列配置
    "MESSAGE_QUEUE_DEFAULT_MAX_COUNT",
    "STATUS_MESSAGE_DEFAULT_MAX_COUNT",
    
    # SDK 元数据属性
    "NEKO_PLUGIN_META_ATTR",
    "NEKO_PLUGIN_TAG",
    "EVENT_META_ATTR",
    
    # 其他配置
    "STATUS_CONSUMER_SLEEP_INTERVAL",
    "MESSAGE_CONSUMER_SLEEP_INTERVAL",
    "RESULT_CONSUMER_SLEEP_INTERVAL",
    "PLUGIN_LOG_MESSAGE_FORWARD",
    "PLUGIN_LOG_SYNC_CALL_WARNINGS",
    "PLUGIN_LOG_BUS_SUBSCRIPTIONS",
    "PLUGIN_LOG_BUS_SUBSCRIBE_REQUESTS",
    "PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS",
    "PLUGIN_LOG_CTX_STATUS_UPDATE",
    "PLUGIN_LOG_CTX_MESSAGE_PUSH",
    "PLUGIN_LOG_SERVER_DEBUG",
    "PLUGIN_LOG_HTTP_PLUGIN_TRIGGER",
    "SYNC_CALL_IN_HANDLER_POLICY",
    
    # 插件Logger配置
    "PLUGIN_LOG_LEVEL",
    "PLUGIN_LOG_MAX_BYTES",
    "PLUGIN_LOG_BACKUP_COUNT",
    "PLUGIN_LOG_MAX_FILES",
    
    # 验证函数
    "validate_config",
]

