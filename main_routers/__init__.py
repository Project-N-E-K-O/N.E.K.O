# -*- coding: utf-8 -*-
# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Main Routers Package

Expose router submodules as modules so ``import main_routers.foo_router as x``
keeps returning the real module instead of the APIRouter object.
"""

from . import agent_router
from . import avatar_drop_router
from . import capture_router
from . import characters_router
from . import cloudsave_router
from . import config_router
from . import jukebox_router
from . import live2d_router
from . import memory_router
from . import mmd_router
from . import music_router
from . import pages_router
from . import storage_location_router
from . import system_router
from . import vrm_router
from . import websocket_router
from . import workshop_router

__all__ = [
    'agent_router',
    'avatar_drop_router',
    'capture_router',
    'characters_router',
    'cloudsave_router',
    'config_router',
    'jukebox_router',
    'live2d_router',
    'memory_router',
    'mmd_router',
    'music_router',
    'pages_router',
    'storage_location_router',
    'system_router',
    'vrm_router',
    'websocket_router',
    'workshop_router',
]
