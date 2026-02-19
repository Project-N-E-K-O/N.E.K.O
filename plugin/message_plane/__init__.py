from __future__ import annotations

import sys
from loguru import logger
from plugin.logging_config import get_plugin_format_console

# 在导入子模块之前配置日志格式
logger.remove()
logger.add(
    sys.stdout,
    format=get_plugin_format_console("message_plane"),
    level="INFO",
    colorize=True,
)

__all__ = [
    "run_message_plane",
]

from .main import run_message_plane
