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

"""Display-socket wiring of the visit route (design §5 PR-09b): progress, goodbye, mount, hooks."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from main_routers import websocket_router
from main_routers.visit_router import display_socket, runtime
from tests.fastapi_routes import effective_path, iter_routes
from tests.unit.test_visit_socket_bind import (  # noqa: F401 - _env 是 autouse 夹具
    FakeRuntime,
    VisitSocket,
    _env,
    bind,
    install,
    run,
)
from tests.unit.test_websocket_binary_audio import _ProtocolManager
from utils import external_route_registry as registry

ROUTER_SOURCE = Path(websocket_router.__file__).read_text(encoding="utf-8")
PROGRESS = {"action": "visit_speech_progress", "visit_id": "v" * 22, "speech_id": "sp-1",
            "played_ms": 300, "ended": False}


# ── visit_speech_progress → 注册表 ─────────────────────────────────────


async def test_progress_from_a_bound_socket_reaches_the_visit(monkeypatch):
    manager = _ProtocolManager()
    calls = install(monkeypatch, manager, visit_active=True)
    socket = VisitSocket([bind(), PROGRESS])
    await run(socket, manager)
    assert [m["speech_id"] for m in calls["signal"]] == ["sp-1"]
    assert manager.statuses == [] and socket.statuses() == []


async def test_progress_from_an_unbound_socket_while_visiting_is_refused(monkeypatch):
    manager = _ProtocolManager()
    calls = install(monkeypatch, manager, visit_active=True)
    socket = VisitSocket([PROGRESS])
    await run(socket, manager)
    assert calls["signal"] == []
    assert socket.statuses() == ["VISIT_E_UNAUTHORIZED"]


async def test_progress_after_the_visit_still_reaches_the_handoff(monkeypatch):
    # 串门已结束（路由不再活动）：仪式句 / 简述的进度照常交给注册表（交还表认领）
    manager = _ProtocolManager()
    calls = install(monkeypatch, manager, visit_active=False)
    socket = VisitSocket([PROGRESS])
    await run(socket, manager)
    assert [m["speech_id"] for m in calls["signal"]] == ["sp-1"]
    assert socket.statuses() == []


async def test_progress_with_only_the_game_kind_is_ignored(monkeypatch):
    manager = _ProtocolManager()
    install(monkeypatch, manager)
    registry._reset_for_tests()

    async def route_none(_name, _message):
        return False

    async def finalize_none(_name):
        return 0

    registry.register_external_route_kind(registry.ExternalRouteKind(
        kind="game", is_active=lambda _n: True, route_stream_message=route_none, on_start_session=None,
        finalize_for_character=finalize_none, current_instance=lambda _n: "g", audio_passthrough=True,
    ))
    socket = VisitSocket([PROGRESS])
    await run(socket, manager)
    assert manager.statuses == [] and socket.statuses() == []


# ── goodbye → finalize('goodbye')（OD-25） ────────────────────────────


async def test_goodbye_while_visiting_finalizes_with_goodbye(monkeypatch):
    manager = _ProtocolManager()
    rt = FakeRuntime([])
    install(monkeypatch, manager, visit_active=True, rt=rt)
    socket = VisitSocket([bind(), {"action": "goodbye_state", "active": True, "reason": "goodbye"}])
    await run(socket, manager)
    assert rt.finalized == ["goodbye"]
    assert ("goodbye", (True, "goodbye")) in manager.calls


async def test_goodbye_from_an_unbound_connection_keeps_the_visit(monkeypatch):
    # 未绑定连接不能结束串门；普通告别处理（静默）照旧
    manager = _ProtocolManager()
    rt = FakeRuntime([])
    install(monkeypatch, manager, visit_active=True, rt=rt)
    await run(VisitSocket([{"action": "goodbye_state", "active": True, "reason": "goodbye"}]), manager)
    assert rt.finalized == []
    assert ("goodbye", (True, "goodbye")) in manager.calls


async def test_goodbye_without_a_visit_is_unchanged(monkeypatch):
    manager = _ProtocolManager()
    rt = FakeRuntime([])
    install(monkeypatch, manager, visit_active=False, rt=rt)
    socket = VisitSocket([{"action": "goodbye_state", "active": True, "reason": "goodbye"},
                          {"action": "goodbye_state", "active": False}])
    await run(socket, manager)
    assert rt.finalized == []
    assert [c for c in manager.calls if c[0] == "goodbye"] == [("goodbye", (True, "goodbye")),
                                                                ("goodbye", (False, "return"))]


async def test_return_while_visiting_does_not_finalize(monkeypatch):
    manager = _ProtocolManager()
    rt = FakeRuntime([])
    install(monkeypatch, manager, visit_active=True, rt=rt)
    await run(VisitSocket([{"action": "goodbye_state", "active": False}]), manager)
    assert rt.finalized == []


async def test_display_socket_disconnect_never_finalizes_the_visit(monkeypatch):
    # 唯一的宽限源是 transport WS（PR-07）：display socket 断开不收尾、不计时
    manager = _ProtocolManager()
    rt = FakeRuntime([])
    install(monkeypatch, manager, visit_active=True, rt=rt)
    await run(VisitSocket([bind()]), manager)
    assert rt.finalized == []


def test_router_binary_branch_carries_no_visit_frames():
    assert "NKVF" not in ROUTER_SOURCE and "visit_frame" not in ROUTER_SOURCE


def test_goodbye_block_finalizes_only_on_active_goodbye():
    block = ROUTER_SOURCE.split('if action == "goodbye_state":', 1)[1].split('if action == "start_session":', 1)[0]
    assert "if active and _visit_owns_input(lanlan_name) and _visit_socket_bound(websocket):" in block
    assert "finalize_on_goodbye(lanlan_name)" in block


# ── 拉黑结束在飞串门 ──────────────────────────────────────────────────


async def test_blocking_a_peer_ends_only_its_live_visits(monkeypatch):
    a = FakeRuntime([])
    a.peer = SimpleNamespace(uid="Peer-X")
    b = FakeRuntime([])
    b.peer = SimpleNamespace(uid="peer-y")
    c = FakeRuntime([])      # 还没核验对端
    monkeypatch.setattr(runtime, "live_runtimes", lambda: [a, b, c])
    assert await display_socket.end_visits_with_peer("PEER-x") == 1
    assert a.finalized == ["peer_blocked"] and b.finalized == [] and c.finalized == []


# ── 挂载与钩子 ────────────────────────────────────────────────────────


def test_visit_router_is_mounted_before_the_pages_fallback():
    from app.main_server import web_app

    routes = list(iter_routes(web_app.app.routes))
    paths = [effective_path(route) for route in routes]
    for path in ("/api/visit/transport/ws", "/api/visit/persona", "/api/visit/memory/peers",
                 "/api/visit/memory/forget", "/api/visit/contacts/block"):
        assert path in paths, path
    pages = [i for i, route in enumerate(routes) if getattr(route, "endpoint", None)
             and route.endpoint.__module__.startswith("main_routers.pages_router")]
    visit = [i for i, path in enumerate(paths) if path.startswith("/api/visit/")]
    assert pages and visit and max(visit) < min(pages)


def test_memory_route_hooks_are_wired_to_the_runtime(monkeypatch):
    import copy

    from main_routers.visit_router import _wire_memory_routes, accounts, memory_routes

    # 包导入时即接线（模块级调用）；别的测试会改钩子，这里在副本上重跑一次再核对
    source = (Path(memory_routes.__file__).parent / "__init__.py").read_text(encoding="utf-8")
    assert "_wire_memory_routes()" in source.splitlines()
    monkeypatch.setattr(memory_routes, "_hooks", copy.copy(memory_routes._hooks))
    _wire_memory_routes()
    hooks = memory_routes._hooks
    assert hooks.own_visit_uid is accounts.own_visit_uid
    assert hooks.is_visit_active is runtime.is_visit_route_locked
    assert hooks.on_blocked is display_socket.end_visits_with_peer
