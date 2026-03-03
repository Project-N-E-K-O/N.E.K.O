"""
ZeroMQ IPC 模块（已弃用）

此模块已迁移到 plugin.utils.zeromq_ipc，这里仅保留重导出。

Usage:
    # 旧方式（已弃用）
    from plugin.zeromq_ipc import ZmqIpcClient
    
    # 新方式（推荐）
    from plugin.utils.zeromq_ipc import ZmqIpcClient
"""
import warnings

warnings.warn(
    "plugin.zeromq_ipc is deprecated, use plugin.utils.zeromq_ipc instead",
    DeprecationWarning,
    stacklevel=2
)

# 从新位置重导出
from plugin.utils.zeromq_ipc import (
    ZmqIpcClient,
    ZmqIpcServer,
    MessagePlaneIngestBatcher,
)

__all__ = [
    "ZmqIpcClient",
    "ZmqIpcServer",
    "MessagePlaneIngestBatcher",
]
