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

"""Catgirl visit HTTP / WebSocket routers (docs/design/visit-infrastructure.md §4.6, §5 PR-07 / PR-08 / PR-09a).

Sub-modules declare ``APIRouter()`` without a prefix and decorate RELATIVE
paths; :data:`router` (``prefix='/api/visit'``) includes them. Two groups:

* start / join a visit -- the transport WS and the visit persona (and, with
  the runtime, rooms / join / accept / invite preview): behind the
  ``NEKO_VISIT_ENABLED`` release switch (404; the WS handshake is refused
  before ``accept``);
* data management -- memory, history, details, reports (and, with the
  runtime, state / transcript / debrief): always available, so users can
  still export, clear or report after the switch was turned off.

Nothing here is mounted on the app until PR-09b includes :data:`router` in
``web_app.py``.
"""

from fastapi import APIRouter, Depends

from main_routers.visit_router import cloud_routes, memory_routes, persona, transport_ws
from main_routers.visit_router.local_guard import require_visit_enabled

router = APIRouter(prefix="/api/visit")

# 发起 / 进行串门的入口：总闸关着时不存在
_gated = [Depends(require_visit_enabled)]
router.include_router(transport_ws.router, dependencies=_gated)
router.include_router(persona.router, dependencies=_gated)

# 数据管理：不受总闸影响
router.include_router(memory_routes.router)
router.include_router(cloud_routes.router)

__all__ = ["router"]
