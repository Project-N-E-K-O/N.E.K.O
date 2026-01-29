# -*- coding: utf-8 -*-
"""
Main Routers Package

This package contains all API routers split from main_server.py by functionality.
"""

from .config_router import router as config_router
from .characters_router import router as characters_router
from .live2d_router import router as live2d_router
from .vrm_router import router as vrm_router
from .workshop_router import router as workshop_router
from .memory_router import router as memory_router
from .pages_router import router as pages_router
from .websocket_router import router as websocket_router
from .agent_router import router as agent_router
from .system_router import router as system_router
from .ip_qrcode_router import router as ip_qrcode_router

__all__ = [
    'config_router',
    'characters_router',
    'live2d_router',
    'vrm_router',
    'workshop_router',
    'memory_router',
    'pages_router',
    'websocket_router',
    'agent_router',
    'system_router',
    'ip_qrcode_router',
]