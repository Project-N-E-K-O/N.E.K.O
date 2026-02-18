"""
日志工具模块（向后兼容）

此模块已迁移到 plugin.logging_config，这里仅保留重导出。
"""
from plugin.logging_config import format_log_text

__all__ = ["format_log_text"]
