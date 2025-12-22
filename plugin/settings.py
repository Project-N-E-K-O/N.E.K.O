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
EVENT_QUEUE_MAX = 1000

# 消息队列最大容量
MESSAGE_QUEUE_MAX = 1000


# ========== 超时配置（秒） ==========

# 插件执行超时（trigger_plugin）
PLUGIN_EXECUTION_TIMEOUT = 30.0

# 插件触发超时（host.trigger）
PLUGIN_TRIGGER_TIMEOUT = 10.0

# 插件关闭超时（shutdown）
PLUGIN_SHUTDOWN_TIMEOUT = 5.0

# 队列操作超时（queue.get）
QUEUE_GET_TIMEOUT = 1.0

# 状态消费关闭超时
STATUS_CONSUMER_SHUTDOWN_TIMEOUT = 5.0

# 进程关闭超时
PROCESS_SHUTDOWN_TIMEOUT = 5.0

# 进程强制终止超时
PROCESS_TERMINATE_TIMEOUT = 1.0


# ========== 线程池配置 ==========

# 通信资源管理器的线程池最大工作线程数
# 根据CPU核心数动态设置，适合I/O密集型操作
# 公式：min(4, CPU核心数 + 2)，确保至少有足够的并发能力
COMMUNICATION_THREAD_POOL_MAX_WORKERS = min(4, (os.cpu_count() or 1) + 2)


# ========== 消息队列配置 ==========

# 获取消息时的默认最大数量
MESSAGE_QUEUE_DEFAULT_MAX_COUNT = 100

# 状态消息获取时的默认最大数量
STATUS_MESSAGE_DEFAULT_MAX_COUNT = 100


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
    "MESSAGE_QUEUE_MAX",
    
    # 超时配置
    "PLUGIN_EXECUTION_TIMEOUT",
    "PLUGIN_TRIGGER_TIMEOUT",
    "PLUGIN_SHUTDOWN_TIMEOUT",
    "QUEUE_GET_TIMEOUT",
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
    
    # 插件Logger配置
    "PLUGIN_LOG_LEVEL",
    "PLUGIN_LOG_MAX_BYTES",
    "PLUGIN_LOG_BACKUP_COUNT",
    "PLUGIN_LOG_MAX_FILES",
    
    # 验证函数
    "validate_config",
]

