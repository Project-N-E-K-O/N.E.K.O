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

* start / join a visit -- the transport WS, the visit persona, rooms / join /
  accept / invite preview: behind the ``NEKO_VISIT_ENABLED`` release switch
  (404; the WS handshake is refused before ``accept``);
* data management -- memory, history, details, reports, route end, state,
  transcript (and, with PR-14, debrief): always available, so users can still
  end, export, clear or report after the switch was turned off.

Importing the package registers the ``neko_visit`` external route kind
(:func:`runtime.register_visit_route_kind`) and wires the runtime hooks of
the memory endpoints (:func:`_wire_memory_routes`, including the per-character
admission lock rooms / join share with the forget endpoints). ``web_app.py``
includes :data:`router`; the display-socket side lives in :mod:`.display_socket`.
"""

from fastapi import APIRouter, Depends

from main_routers.visit_router import cloud_routes, http, memory_routes, persona, runtime, transport_ws
from main_routers.visit_router.local_guard import require_visit_enabled

router = APIRouter(prefix="/api/visit")

# 发起 / 进行串门的入口：总闸关着时不存在
_gated = [Depends(require_visit_enabled)]
router.include_router(transport_ws.router, dependencies=_gated)
router.include_router(persona.router, dependencies=_gated)
router.include_router(http.router, dependencies=_gated)

# 数据管理（含结束、状态、转录导出）：不受总闸影响
router.include_router(http.data_router)
router.include_router(memory_routes.router)
router.include_router(cloud_routes.router)

runtime.register_visit_route_kind()


def _wire_memory_routes() -> None:
    # 记忆管理端点的运行时钩子：本机登录账号的 visit_uid（#3312 的映射）、角色是否正在串门
    # （占位到退出流程结束都算）、清除期间挡住改名 / 删除、拉黑时结束与此人的在飞串门；
    # 清除写哨兵与建房 / 入房的清除检查取同一把每角色准入锁（§3.2.1 第 0 步）
    from main_routers.visit_router.accounts import own_visit_uid
    from main_routers.visit_router.display_socket import end_visits_with_peer

    memory_routes.configure_memory_routes(
        own_visit_uid=own_visit_uid,
        is_visit_active=runtime.is_visit_route_locked,
        lifecycle_guard=runtime.hold_character_lifecycle,
        on_blocked=end_visits_with_peer,
        admission_lock=http.char_admission_lock,
    )


_wire_memory_routes()

__all__ = ["router"]
