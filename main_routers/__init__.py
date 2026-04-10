# -*- coding: utf-8 -*-
"""
Main Routers Package

Expose router submodules as modules so ``import main_routers.foo_router as x``
keeps returning the real module instead of the APIRouter object.
"""

from . import agent_router
from . import characters_router
from . import cloudsave_router
from . import config_router
from . import jukebox_router
from . import live2d_router
from . import memory_router
from . import mmd_router
from . import music_router
from . import pages_router
from . import system_router
from . import vrm_router
from . import websocket_router
from . import workshop_router

__all__ = [
    'agent_router',
    'characters_router',
    'cloudsave_router',
    'config_router',
    'jukebox_router',
    'live2d_router',
    'memory_router',
    'mmd_router',
    'music_router',
    'pages_router',
    'system_router',
    'vrm_router',
    'websocket_router',
    'workshop_router',
]
