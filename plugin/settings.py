
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
    """获取插件配置根目录.

    - 默认：``plugin/plugins``（相对于当前 ``plugin`` 包所在目录）
    - Env: ``PLUGIN_CONFIG_ROOT``

    ``PLUGIN_CONFIG_ROOT`` 可以是绝对路径，也可以是相对路径/``~``，
    最终会被解析为绝对路径。
    """
    custom_path = os.getenv("PLUGIN_CONFIG_ROOT")
    if custom_path:
        # 支持 ~ 和相对路径，统一解析为绝对路径
        return Path(custom_path).expanduser().resolve()
    # 默认路径：相对于 plugin 目录
    return Path(__file__).parent / "plugins"


PLUGIN_CONFIG_ROOT = get_plugin_config_root()


# ========== 队列容量配置 ==========

# 事件队列最大容量
# Env: NEKO_EVENT_QUEUE_MAX, default=1000
# 用于主进程内部的事件派发队列（如插件生命周期事件等）。
EVENT_QUEUE_MAX = _get_int_env("NEKO_EVENT_QUEUE_MAX", 1000)

# 生命周期事件队列最大容量
# Env: NEKO_LIFECYCLE_QUEUE_MAX, default=1000
# 控制 "lifecycle" 相关事件（插件启动/停止等）的排队上限。
LIFECYCLE_QUEUE_MAX = _get_int_env("NEKO_LIFECYCLE_QUEUE_MAX", 1000)

# 消息总队列最大容量
# Env: NEKO_MESSAGE_QUEUE_MAX, default=1000
# 用于插件向主进程推送消息的总队列上限，避免无限堆积。
MESSAGE_QUEUE_MAX = _get_int_env("NEKO_MESSAGE_QUEUE_MAX", 1000)
EXPORT_INLINE_BINARY_MAX_BYTES = _get_int_env("NEKO_EXPORT_INLINE_BINARY_MAX_BYTES", 256 * 1024)

RUN_TOKEN_SECRET = os.getenv("NEKO_RUN_TOKEN_SECRET", "dev-insecure-run-token-secret")
RUN_TOKEN_TTL_SECONDS = _get_int_env("NEKO_RUN_TOKEN_TTL_SECONDS", 3600)
# 单次 Run 的最大执行时间（秒），超时后自动标记为 timeout
# Env: NEKO_RUN_EXECUTION_TIMEOUT, default=300.0 (5分钟)
RUN_EXECUTION_TIMEOUT = _get_float_env("NEKO_RUN_EXECUTION_TIMEOUT", 300.0)
# InMemoryRunStore 保留的已终止 Run 最大数量，超出后淘汰最旧的
# Env: NEKO_RUN_STORE_MAX_COMPLETED, default=500
RUN_STORE_MAX_COMPLETED = _get_int_env("NEKO_RUN_STORE_MAX_COMPLETED", 500)

BLOB_STORE_DIR = os.getenv("NEKO_BLOB_STORE_DIR", str((Path(__file__).parent / "store" / "blobs").resolve()))
BLOB_UPLOAD_MAX_BYTES = _get_int_env("NEKO_BLOB_UPLOAD_MAX_BYTES", 200 * 1024 * 1024)


# ========== 超时 & 轮询配置（秒） ==========

# 单次插件入口执行的最大允许时间
# Env: NEKO_PLUGIN_EXECUTION_TIMEOUT, default=30.0
# 用于 SDK 层对长时间运行入口的保护（例如 HTTP 触发的入口）。
PLUGIN_EXECUTION_TIMEOUT = _get_float_env("NEKO_PLUGIN_EXECUTION_TIMEOUT", 30.0)

# Host -> 插件进程 trigger 的等待超时
# Env: NEKO_PLUGIN_TRIGGER_TIMEOUT, default=10.0
# 影响 ``PluginProcessHost.trigger`` 的等待时间，超时后会返回错误。
PLUGIN_TRIGGER_TIMEOUT = _get_float_env("NEKO_PLUGIN_TRIGGER_TIMEOUT", 10.0)

# 单个插件优雅关闭的超时时间
# Env: NEKO_PLUGIN_SHUTDOWN_TIMEOUT, default=5.0
# 用于 ``host.shutdown``，超过后会进入更激进的终止流程。
PLUGIN_SHUTDOWN_TIMEOUT = _get_float_env("NEKO_PLUGIN_SHUTDOWN_TIMEOUT", 5.0)

# 所有插件整体关闭的最大等待时间（用于 server shutdown）
# Env: PLUGIN_SHUTDOWN_TOTAL_TIMEOUT 或 NEKO_PLUGIN_SHUTDOWN_TOTAL_TIMEOUT, default=30
_shutdown_total_timeout_str = os.getenv("PLUGIN_SHUTDOWN_TOTAL_TIMEOUT", os.getenv("NEKO_PLUGIN_SHUTDOWN_TOTAL_TIMEOUT", "30"))
try:
    PLUGIN_SHUTDOWN_TOTAL_TIMEOUT = int(_shutdown_total_timeout_str)
except ValueError:
    PLUGIN_SHUTDOWN_TOTAL_TIMEOUT = 30  # 默认值

# 队列操作超时（queue.get）
# Env: NEKO_QUEUE_GET_TIMEOUT, default=1.0
# 所有通过 ``Queue.get(timeout=...)`` 的阻塞等待都建议使用该配置。
QUEUE_GET_TIMEOUT = _get_float_env("NEKO_QUEUE_GET_TIMEOUT", 1.0)

# 插件 SDK 同步轮询响应间隔
# Env: NEKO_BUS_SDK_POLL_INTERVAL_SECONDS, default=0.002
# bus.*.get 等接口轮询共享 ``response_map`` 的时间间隔；
# - 调小：降低延迟抖动、提升吞吐，但会增加 CPU 占用；
# - 调大：降低 CPU，占用，但响应延迟波动增大。
BUS_SDK_POLL_INTERVAL_SECONDS = _get_float_env("NEKO_BUS_SDK_POLL_INTERVAL_SECONDS", 0.002)

# 状态消费器在 shutdown 时的最大等待时间
# Env: NEKO_STATUS_CONSUMER_SHUTDOWN_TIMEOUT, default=5.0
STATUS_CONSUMER_SHUTDOWN_TIMEOUT = _get_float_env("NEKO_STATUS_CONSUMER_SHUTDOWN_TIMEOUT", 5.0)

# 插件进程优雅关闭的最长等待时间
# Env: NEKO_PROCESS_SHUTDOWN_TIMEOUT, default=5.0
PROCESS_SHUTDOWN_TIMEOUT = _get_float_env("NEKO_PROCESS_SHUTDOWN_TIMEOUT", 5.0)

# 插件进程在被强制终止（terminate）后的 join 超时时间
# Env: NEKO_PROCESS_TERMINATE_TIMEOUT, default=1.0
PROCESS_TERMINATE_TIMEOUT = _get_float_env("NEKO_PROCESS_TERMINATE_TIMEOUT", 1.0)


# ========== 线程池配置 ==========

# 通信资源管理器的线程池最大工作线程数
# - 每个插件的通信管理器需要至少 3 个线程：
#   1. _consume_results - 持续读取结果队列
#   2. _consume_messages - 持续读取消息队列
#   3. _send_command_and_wait - 发送命令到插件
# - 公式：``max(8, (CPU核心数 or 1) + 4)``，确保多插件场景下有足够的线程
COMMUNICATION_THREAD_POOL_MAX_WORKERS = max(32, (os.cpu_count() or 1) + 8)


# ========== 消息拉取默认上限 ==========

# 获取消息时的默认 ``max_count``
# Env: NEKO_MESSAGE_QUEUE_DEFAULT_MAX_COUNT, default=100
# bus.messages.get / events.get / lifecycle.get 等接口在未显式指定 max_count 时使用该值。
MESSAGE_QUEUE_DEFAULT_MAX_COUNT = _get_int_env("NEKO_MESSAGE_QUEUE_DEFAULT_MAX_COUNT", 100)

# 获取状态消息时的默认 ``max_count``
# Env: NEKO_STATUS_MESSAGE_DEFAULT_MAX_COUNT, default=100
STATUS_MESSAGE_DEFAULT_MAX_COUNT = _get_int_env("NEKO_STATUS_MESSAGE_DEFAULT_MAX_COUNT", 100)


# ========== SDK 元数据属性 ==========

# 插件元数据属性名（用于标记插件类）
NEKO_PLUGIN_META_ATTR = "__neko_plugin_meta__"

# 插件标签（用于标记插件类）
NEKO_PLUGIN_TAG = "__neko_plugin__"


# ========== 其他运行时配置 ==========

# 状态消费任务的休眠间隔（秒）
# 固定值，主要影响 CPU/延迟折中；通常不需要修改。
STATUS_CONSUMER_SLEEP_INTERVAL = 0.1

# 消息消费任务的休眠间隔（秒）
MESSAGE_CONSUMER_SLEEP_INTERVAL = 0.1

# 结果消费任务的休眠间隔（秒）
RESULT_CONSUMER_SLEEP_INTERVAL = 0.1

# 是否打印插件消息转发日志（[MESSAGE FORWARD]）
# Env: NEKO_PLUGIN_LOG_MESSAGE_FORWARD, default=True
PLUGIN_LOG_MESSAGE_FORWARD = _get_bool_env("NEKO_PLUGIN_LOG_MESSAGE_FORWARD", True)

# 是否打印插件同步调用告警（"Sync call '...' may block ..."）
# Env: NEKO_PLUGIN_LOG_SYNC_CALL_WARNINGS, default=True
PLUGIN_LOG_SYNC_CALL_WARNINGS = _get_bool_env("NEKO_PLUGIN_LOG_SYNC_CALL_WARNINGS", True)

# 是否在订阅变更时打印 bus 订阅信息
# Env: NEKO_PLUGIN_LOG_BUS_SUBSCRIPTIONS, default=True
PLUGIN_LOG_BUS_SUBSCRIPTIONS = _get_bool_env("NEKO_PLUGIN_LOG_BUS_SUBSCRIPTIONS", True)

# 是否打印订阅请求日志
# Env: NEKO_PLUGIN_LOG_BUS_SUBSCRIBE_REQUESTS, default=True
PLUGIN_LOG_BUS_SUBSCRIBE_REQUESTS = _get_bool_env("NEKO_PLUGIN_LOG_BUS_SUBSCRIBE_REQUESTS", True)

# 是否在 SDK 调用超时时打印告警
# Env: NEKO_PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS, default=True
PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS = _get_bool_env("NEKO_PLUGIN_LOG_BUS_SDK_TIMEOUT_WARNINGS", True)

# 是否在 ctx.status.update 时打印日志
# Env: NEKO_PLUGIN_LOG_CTX_STATUS_UPDATE, default=True
PLUGIN_LOG_CTX_STATUS_UPDATE = _get_bool_env("NEKO_PLUGIN_LOG_CTX_STATUS_UPDATE", True)

# 是否在 ctx.push_message 时打印日志
# Env: NEKO_PLUGIN_LOG_CTX_MESSAGE_PUSH, default=True
PLUGIN_LOG_CTX_MESSAGE_PUSH = _get_bool_env("NEKO_PLUGIN_LOG_CTX_MESSAGE_PUSH", True)

# 是否打印服务端调试日志（更啰嗦）
# Env: NEKO_PLUGIN_LOG_SERVER_DEBUG, default=False
PLUGIN_LOG_SERVER_DEBUG = _get_bool_env("NEKO_PLUGIN_LOG_SERVER_DEBUG", False)

# ========== Message Schema 校验 ==========

# 是否对 message bus 的 payload 做严格字段/类型校验。
# Env: NEKO_MESSAGE_SCHEMA_STRICT, default=True
MESSAGE_SCHEMA_STRICT = _get_bool_env("NEKO_MESSAGE_SCHEMA_STRICT", True)

# 是否允许插件通过 payload 标记 unsafe 来跳过严格校验（用于高性能场景）。
# Env: NEKO_MESSAGE_SCHEMA_ALLOW_UNSAFE, default=True
MESSAGE_SCHEMA_ALLOW_UNSAFE = _get_bool_env("NEKO_MESSAGE_SCHEMA_ALLOW_UNSAFE", True)

# 是否对出现未知字段（schema 外字段）打印 warning。
# Env: NEKO_MESSAGE_SCHEMA_WARN_UNKNOWN_FIELDS, default=True
MESSAGE_SCHEMA_WARN_UNKNOWN_FIELDS = _get_bool_env("NEKO_MESSAGE_SCHEMA_WARN_UNKNOWN_FIELDS", True)

# 是否启用 ZeroMQ IPC 管道（插件进程 <-> 主进程）
# Env: NEKO_PLUGIN_ZMQ_IPC_ENABLED, default=True
PLUGIN_ZMQ_IPC_ENABLED = _get_bool_env("NEKO_PLUGIN_ZMQ_IPC_ENABLED", True)

# ZeroMQ IPC 端点地址
# Env: NEKO_PLUGIN_ZMQ_IPC_ENDPOINT, default="tcp://127.0.0.1:38765"
PLUGIN_ZMQ_IPC_ENDPOINT = os.getenv("NEKO_PLUGIN_ZMQ_IPC_ENDPOINT", "tcp://127.0.0.1:38765")

# [MESSAGE FORWARD] 日志去重窗口（秒）
# Env: NEKO_PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS, default=1.0
PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS = _get_float_env(
    "NEKO_PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS", 1.0
)

# bus 变更日志去重窗口（秒）
# Env: NEKO_PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS, default=1.0
PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS = _get_float_env(
    "NEKO_PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS", 1.0
)

# ========== Message Plane (High-Frequency Bus) ==========

# Message plane 后端实现选择
# - python: 使用当前 Python message_plane 实现（默认）
# - rust: 使用外部 Rust message_plane 可执行文件
# Env: NEKO_MESSAGE_PLANE_BACKEND, default="python"
MESSAGE_PLANE_BACKEND = os.getenv("NEKO_MESSAGE_PLANE_BACKEND", "python").strip().lower()
if MESSAGE_PLANE_BACKEND in ("wheel", "rust-wheel", "rust_wheel"):
    MESSAGE_PLANE_BACKEND = "rust"
if MESSAGE_PLANE_BACKEND not in ("python", "rust"):
    MESSAGE_PLANE_BACKEND = "python"

# Rust message_plane 可执行文件路径（当 MESSAGE_PLANE_BACKEND=rust 时使用）
# Env: NEKO_MESSAGE_PLANE_RUST_BIN, default="neko-message-plane"
MESSAGE_PLANE_RUST_BIN = os.getenv("NEKO_MESSAGE_PLANE_RUST_BIN", "neko-message-plane").strip()

# Rust message_plane 工作线程数（当 MESSAGE_PLANE_BACKEND=rust 时使用）
# - 0: 自动检测 CPU 核心数（默认，最少 4 个）
# - >0: 手动指定工作线程数
# Env: NEKO_MESSAGE_PLANE_WORKERS, default=0
MESSAGE_PLANE_WORKERS = _get_int_env("NEKO_MESSAGE_PLANE_WORKERS", 0)

# Message plane ZeroMQ RPC 端点（用于高频 bus 的请求/响应，例如 get/reload/filter 等）
# 使用 TCP 回环（127.0.0.1），在某些系统上比 IPC 更快
# Env: NEKO_MESSAGE_PLANE_ZMQ_RPC_ENDPOINT, default="tcp://127.0.0.1:38865"
MESSAGE_PLANE_ZMQ_RPC_ENDPOINT = os.getenv(
    "NEKO_MESSAGE_PLANE_ZMQ_RPC_ENDPOINT",
    os.getenv("NEKO_MESSAGE_PLANE_RPC", "tcp://127.0.0.1:38865"),
)

# Message plane ZeroMQ PUB 端点（用于高频 bus 的订阅/推送，例如 watcher、export progress 等）
# 使用 TCP 回环（127.0.0.1），在某些系统上比 IPC 更快
# Env: NEKO_MESSAGE_PLANE_ZMQ_PUB_ENDPOINT, default="tcp://127.0.0.1:38866"
MESSAGE_PLANE_ZMQ_PUB_ENDPOINT = os.getenv(
    "NEKO_MESSAGE_PLANE_ZMQ_PUB_ENDPOINT",
    os.getenv("NEKO_MESSAGE_PLANE_PUB", "tcp://127.0.0.1:38866"),
)

# Message plane 运行模式
# - embedded: 作为主进程的后台线程启动（默认）
# - external: 由主进程启动独立子进程（独立解释器），用于隔离控制面与数据面负载
# Env: NEKO_MESSAGE_PLANE_RUN_MODE, default="external"
MESSAGE_PLANE_RUN_MODE = os.getenv("NEKO_MESSAGE_PLANE_RUN_MODE", "external").strip().lower()
if MESSAGE_PLANE_RUN_MODE not in ("embedded", "external"):
    MESSAGE_PLANE_RUN_MODE = "external"

MESSAGE_PLANE_VALIDATE_MODE = os.getenv("NEKO_MESSAGE_PLANE_VALIDATE_MODE", "strict").lower()
if MESSAGE_PLANE_VALIDATE_MODE not in ("off", "warn", "strict"):
    MESSAGE_PLANE_VALIDATE_MODE = "strict"

MESSAGE_PLANE_TOPIC_MAX = _get_int_env("NEKO_MESSAGE_PLANE_TOPIC_MAX", 2000)
MESSAGE_PLANE_TOPIC_NAME_MAX_LEN = _get_int_env("NEKO_MESSAGE_PLANE_TOPIC_NAME_MAX_LEN", 128)
MESSAGE_PLANE_PAYLOAD_MAX_BYTES = _get_int_env("NEKO_MESSAGE_PLANE_PAYLOAD_MAX_BYTES", 256 * 1024)
MESSAGE_PLANE_STORE_MAXLEN = _get_int_env("NEKO_MESSAGE_PLANE_STORE_MAXLEN", 20000)
MESSAGE_PLANE_GET_RECENT_MAX_LIMIT = _get_int_env("NEKO_MESSAGE_PLANE_GET_RECENT_MAX_LIMIT", 1000)

MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT = os.getenv(
    "NEKO_MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT",
    os.getenv("NEKO_MESSAGE_PLANE_INGEST", "tcp://127.0.0.1:38867"),
)
MESSAGE_PLANE_INGEST_RCVHWM = _get_int_env("NEKO_MESSAGE_PLANE_INGEST_RCVHWM", 10000)

MESSAGE_PLANE_INGEST_STATS_LOG_ENABLED = _get_bool_env("NEKO_MESSAGE_PLANE_INGEST_STATS_LOG_ENABLED", True)
MESSAGE_PLANE_INGEST_STATS_LOG_INFO = _get_bool_env("NEKO_MESSAGE_PLANE_INGEST_STATS_LOG_INFO", True)
MESSAGE_PLANE_INGEST_STATS_LOG_VERBOSE = _get_bool_env("NEKO_MESSAGE_PLANE_INGEST_STATS_LOG_VERBOSE", False)
MESSAGE_PLANE_INGEST_STATS_INTERVAL_SECONDS = _get_float_env("NEKO_MESSAGE_PLANE_INGEST_STATS_INTERVAL_SECONDS", 1.0)
MESSAGE_PLANE_INGEST_BACKPRESSURE_SLEEP_SECONDS = _get_float_env("NEKO_MESSAGE_PLANE_INGEST_BACKPRESSURE_SLEEP_SECONDS", 0.0)

# Plugin -> message_plane ingest PUSH send timeout (ms). Prevents plugin thread from blocking indefinitely
# under heavy backpressure.
# Env: NEKO_MESSAGE_PLANE_INGEST_SNDTIMEO_MS, default=1000
MESSAGE_PLANE_INGEST_SNDTIMEO_MS = _get_int_env("NEKO_MESSAGE_PLANE_INGEST_SNDTIMEO_MS", 1000)

MESSAGE_PLANE_PUB_ENABLED = _get_bool_env("NEKO_MESSAGE_PLANE_PUB_ENABLED", True)
MESSAGE_PLANE_VALIDATE_PAYLOAD_BYTES = _get_bool_env("NEKO_MESSAGE_PLANE_VALIDATE_PAYLOAD_BYTES", True)

MESSAGE_PLANE_PUSH_BATCHER_MAX_QUEUE = _get_int_env("NEKO_MESSAGE_PLANE_PUSH_BATCHER_MAX_QUEUE", 100000)
MESSAGE_PLANE_PUSH_BATCHER_REJECT_RATIO = _get_float_env("NEKO_MESSAGE_PLANE_PUSH_BATCHER_REJECT_RATIO", 0.9)
MESSAGE_PLANE_PUSH_BATCHER_ENQUEUE_TIMEOUT_SECONDS = _get_float_env(
    "NEKO_MESSAGE_PLANE_PUSH_BATCHER_ENQUEUE_TIMEOUT_SECONDS",
    0.01,
)

MESSAGE_PLANE_BRIDGE_ENABLED = _get_bool_env("NEKO_MESSAGE_PLANE_BRIDGE_ENABLED", True)

# PUSH 批量大小（条数）
# Env: NEKO_PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE, default=256
PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE = _get_int_env("NEKO_PLUGIN_ZMQ_MESSAGE_PUSH_BATCH_SIZE", 256)

# PUSH 刷新间隔（毫秒），小批量高频发送或大批量低频发送的折中参数
# Env: NEKO_PLUGIN_ZMQ_MESSAGE_PUSH_FLUSH_INTERVAL_MS, default=5
PLUGIN_ZMQ_MESSAGE_PUSH_FLUSH_INTERVAL_MS = _get_int_env("NEKO_PLUGIN_ZMQ_MESSAGE_PUSH_FLUSH_INTERVAL_MS", 5)

# 同步调用在 handler 中的全局策略（"warn" / "reject"）
# Env: NEKO_PLUGIN_SYNC_CALL_POLICY, default="warn"
_sync_policy = os.getenv("NEKO_PLUGIN_SYNC_CALL_POLICY", "warn").lower()
if _sync_policy not in ("warn", "reject"):
    _sync_policy = "warn"
SYNC_CALL_IN_HANDLER_POLICY = _sync_policy

# ========== 插件 Logger 文件配置 ==========

# 插件文件日志默认配置（使用 loguru 创建的进程内 file handler）
# 默认日志级别（字符串格式，loguru 使用）
PLUGIN_LOG_LEVEL = "INFO"

# 单个日志文件最大大小（字节），默认 5MB
PLUGIN_LOG_MAX_BYTES = 5 * 1024 * 1024

# 轮转备份文件数量，默认 10 个
PLUGIN_LOG_BACKUP_COUNT = 10

# 最多保留的日志文件总数（包括当前和备份），默认 20 个
PLUGIN_LOG_MAX_FILES = 20


# ========== 插件状态持久化配置 ==========

# 插件状态持久化后端（统一管理 freeze 和自动保存）
# - "off": 禁用持久化（默认）
# - "memory": 保存到内存（主进程重启后丢失）
# - "file": 保存到文件（持久化）
# Env: NEKO_PLUGIN_STATE_BACKEND, default="off"
PLUGIN_STATE_BACKEND_DEFAULT = os.getenv("NEKO_PLUGIN_STATE_BACKEND", "off").strip().lower()
if PLUGIN_STATE_BACKEND_DEFAULT not in ("off", "memory", "file"):
    PLUGIN_STATE_BACKEND_DEFAULT = "off"

# 向后兼容：旧的 PLUGIN_FREEZE_BACKEND_DEFAULT 映射到新配置
PLUGIN_FREEZE_BACKEND_DEFAULT = PLUGIN_STATE_BACKEND_DEFAULT

# ========== Store 配置 ==========
# Store 默认后端：sqlite/memory/off (默认 off，需要开发者显式启用)
PLUGIN_STORE_BACKEND_DEFAULT = os.getenv("NEKO_PLUGIN_STORE_BACKEND", "off")


# ========== 插件加载行为配置 ==========

# 是否启用插件依赖检查
# Env: PLUGIN_ENABLE_DEPENDENCY_CHECK, default=False
# - False：跳过依赖检查，允许加载不满足依赖关系的插件（仅建议开发/调试环境使用）；
# - True：严格检查依赖，不满足则拒绝加载。
PLUGIN_ENABLE_DEPENDENCY_CHECK = os.getenv("PLUGIN_ENABLE_DEPENDENCY_CHECK", "false").lower() in ("true", "1", "yes")

# 是否启用插件 ID 冲突检查
# Env: PLUGIN_ENABLE_ID_CONFLICT_CHECK, default=False
# - False：跳过 ID 冲突检查，允许多个插件声明相同 ID（可能导致不可预期行为，仅建议调试使用）；
# - True：启用严格 ID 冲突检测和重命名逻辑。
PLUGIN_ENABLE_ID_CONFLICT_CHECK = os.getenv("PLUGIN_ENABLE_ID_CONFLICT_CHECK", "false").lower() in ("true", "1", "yes")


# ========== 主进程 loguru 配置 ==========

# 主进程 loguru 日志等级（仅影响主进程；插件子进程会各自配置 loguru）
# Env: NEKO_LOGURU_LEVEL, default="INFO"
NEKO_LOGURU_LEVEL = os.getenv("NEKO_LOGURU_LEVEL", "INFO")


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

    if PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS < 0:
        raise ValueError("PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS must be >= 0")
    if PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS > 3600:
        raise ValueError("PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS is unreasonably large (max: 3600s)")

    if PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS < 0:
        raise ValueError("PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS must be >= 0")
    if PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS > 3600:
        raise ValueError("PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS is unreasonably large (max: 3600s)")

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
    
    # Message schema 校验
    "MESSAGE_SCHEMA_STRICT",
    "MESSAGE_SCHEMA_ALLOW_UNSAFE",
    "MESSAGE_SCHEMA_WARN_UNKNOWN_FIELDS",
    
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
    "PLUGIN_MESSAGE_FORWARD_LOG_DEDUP_WINDOW_SECONDS",
    "PLUGIN_BUS_CHANGE_LOG_DEDUP_WINDOW_SECONDS",
    "SYNC_CALL_IN_HANDLER_POLICY",

    # Message plane backend
    "MESSAGE_PLANE_BACKEND",
    "MESSAGE_PLANE_RUST_BIN",
    "MESSAGE_PLANE_WORKERS",
    "MESSAGE_PLANE_RUN_MODE",
    "MESSAGE_PLANE_ZMQ_RPC_ENDPOINT",
    "MESSAGE_PLANE_ZMQ_PUB_ENDPOINT",
    "MESSAGE_PLANE_ZMQ_INGEST_ENDPOINT",
    "MESSAGE_PLANE_VALIDATE_MODE",
    
    # 插件Logger配置
    "PLUGIN_LOG_LEVEL",
    "PLUGIN_LOG_MAX_BYTES",
    "PLUGIN_LOG_BACKUP_COUNT",
    "PLUGIN_LOG_MAX_FILES",
    
    # 状态持久化配置
    "PLUGIN_STATE_BACKEND_DEFAULT",
    
    # Run 配置
    "RUN_EXECUTION_TIMEOUT",
    "RUN_STORE_MAX_COMPLETED",
    
    # 验证函数
    "validate_config",
]
