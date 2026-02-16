"""
路由模块

提供所有 HTTP 和 WebSocket 路由端点。
"""
from plugin.server.routes.health import router as health_router
from plugin.server.routes.plugins import router as plugins_router
from plugin.server.routes.runs import router as runs_router
from plugin.server.routes.messages import router as messages_router
from plugin.server.routes.metrics import router as metrics_router
from plugin.server.routes.config import router as config_router
from plugin.server.routes.logs import router as logs_router
from plugin.server.routes.frontend import router as frontend_router
from plugin.server.routes.websocket import router as websocket_router
from plugin.server.routes.plugin_ui import router as plugin_ui_router

__all__ = [
    'health_router',
    'plugins_router',
    'runs_router',
    'messages_router',
    'metrics_router',
    'config_router',
    'logs_router',
    'frontend_router',
    'websocket_router',
    'plugin_ui_router',
]
