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

"""Character rename / delete guard and the clearing lifecycle guard (visit design OD-13, PR-09b)."""

from __future__ import annotations

import asyncio
import copy
import json
from unittest.mock import AsyncMock, patch

import pytest

from main_logic.visit import local_chars
from main_routers.visit_router import runtime
from tests.unit.test_character_uid import _backfilled_manager, _DummyRequest, _init_router_state
from utils import external_route_registry as registry
from utils.character_memory import character_config_mutation_lock
from utils.config_manager import get_character_uid


@pytest.fixture(autouse=True)
def _clean():
    runtime._reset_for_tests()
    yield
    runtime._reset_for_tests()


def _names(monkeypatch, mapping):
    async def resolve(uid):
        return mapping.get(uid)

    monkeypatch.setattr(local_chars, "resolve_char_name", resolve)


# ── 清除期间的生命周期守卫 ─────────────────────────────────────────────


async def test_guard_locks_the_character_only_while_held(monkeypatch):
    _names(monkeypatch, {"uid-a": "A"})
    assert not registry.is_character_lifecycle_locked("A")
    async with runtime.hold_character_lifecycle(["uid-a"]):
        assert runtime.has_visit_background_tasks("A")
        assert registry.is_character_lifecycle_locked("A")
        assert not registry.is_character_lifecycle_locked("B")
        # 只挡改名 / 删除，不占串门槽位：照样能建新串门、开小游戏
        assert not registry.is_external_route_locked("A")
    assert not registry.is_character_lifecycle_locked("A")
    assert runtime._visit_bg_tasks == {}


async def test_guard_waits_for_a_rename_already_in_progress(monkeypatch):
    names = {"uid-a": "Old"}
    _names(monkeypatch, names)
    entered = asyncio.Event()

    async def clearing():
        async with runtime.hold_character_lifecycle(["uid-a"]):
            entered.set()
            await asyncio.sleep(0.05)

    async with character_config_mutation_lock:      # 改名事务进行中
        task = asyncio.ensure_future(clearing())
        await asyncio.sleep(0.02)
        assert not entered.is_set()
        names["uid-a"] = "New"                       # 事务提交
    await asyncio.wait_for(entered.wait(), 1.0)
    # 守卫拿到的是改名之后的名字：挡的是新名字
    assert registry.is_character_lifecycle_locked("New")
    assert await task is None


async def test_guard_survives_cancellation_by_shutdown(monkeypatch):
    _names(monkeypatch, {"uid-a": "A"})
    async with runtime.hold_character_lifecycle(["uid-a"]):
        for bucket in list(runtime._visit_bg_tasks.values()):
            for fut in list(bucket):
                fut.cancel()                         # stop_all 取消后台任务
    assert runtime._visit_bg_tasks == {}


def test_memory_routes_use_this_guard():
    from main_routers.visit_router import _wire_memory_routes, memory_routes

    saved = memory_routes._hooks
    memory_routes._hooks = copy.copy(saved)
    try:
        _wire_memory_routes()
        assert memory_routes._hooks.lifecycle_guard is runtime.hold_character_lifecycle
    finally:
        memory_routes._hooks = saved


# ── crud 改名 / 删除守卫 ───────────────────────────────────────────────


def _result(response):
    status = getattr(response, "status_code", 200)
    body = response if isinstance(response, dict) else json.loads(bytes(response.body))
    return status, body


def _patched(crud):
    return (patch.object(crud, "release_memory_server_character", AsyncMock(return_value=True)),
            patch.object(crud, "notify_memory_server_reload", AsyncMock(return_value=True)))


async def _rename(cm, old, new):
    crud = _init_router_state(cm)
    a, b = _patched(crud)
    with a, b:
        return _result(await crud.rename_catgirl(old, _DummyRequest({"new_name": new})))


async def _delete(cm, name):
    crud = _init_router_state(cm)
    a, b = _patched(crud)
    with a, b:
        return _result(await crud.delete_catgirl(name))


async def _none(*_a, **_k):
    return False


async def _zero(_name):
    return 0


def _register(kind, **kw):
    registry.register_external_route_kind(registry.ExternalRouteKind(
        kind=kind, route_stream_message=_none, finalize_for_character=_zero, **kw,
    ))


@pytest.mark.parametrize("locked,background", [({"Old"}, set()), (set(), {"Old"})])
async def test_rename_is_refused_while_the_route_or_its_background_holds_it(tmp_path, locked, background):
    cm, path = _backfilled_manager(tmp_path, {"Current": {"昵称": "Current"}, "Old": {"昵称": "Old"}})
    before = path.read_bytes()
    _register("neko_visit", is_active=lambda _n: False, on_start_session=_none, current_instance=lambda _n: None,
              is_locked=lambda n: n in locked, has_background_tasks=lambda n: n in background)
    with patch("utils.config_manager._config_manager", cm):
        status, body = await _rename(cm, "Old", "New")
    assert status == 400 and body["error_code"] == "EXTERNAL_ROUTE_ACTIVE"
    assert path.read_bytes() == before


async def test_rename_of_a_character_in_a_mini_game_is_refused(tmp_path):
    cm, path = _backfilled_manager(tmp_path, {"Current": {"昵称": "Current"}, "Old": {"昵称": "Old"}})
    before = path.read_bytes()
    _register("game", is_active=lambda n: n == "Old", on_start_session=None, current_instance=lambda _n: "g",
              audio_passthrough=True)
    with patch("utils.config_manager._config_manager", cm):
        status, body = await _rename(cm, "Old", "New")
    assert status == 400 and body["error_code"] == "EXTERNAL_ROUTE_ACTIVE"
    assert path.read_bytes() == before


async def test_delete_is_refused_while_held_and_succeeds_after(tmp_path, monkeypatch):
    cm, path = _backfilled_manager(tmp_path, {"Current": {"昵称": "Current"}, "Gone": {"昵称": "Gone"}})
    before = path.read_bytes()
    uid = get_character_uid(cm.load_characters()["猫娘"]["Gone"])
    _names(monkeypatch, {uid: "Gone"})
    with patch("utils.config_manager._config_manager", cm):
        async with runtime.hold_character_lifecycle([uid]):
            status, body = await _delete(cm, "Gone")
            assert status == 400 and body["error_code"] == "EXTERNAL_ROUTE_ACTIVE"
            assert path.read_bytes() == before
        status, body = await _delete(cm, "Gone")
    assert body.get("success") is True, body
    assert "Gone" not in cm.load_characters()["猫娘"]


async def test_rename_without_any_route_is_unchanged(tmp_path):
    cm, _path = _backfilled_manager(tmp_path, {"Current": {"昵称": "Current"}, "Old": {"昵称": "Old"}})
    with patch("utils.config_manager._config_manager", cm):
        status, body = await _rename(cm, "Old", "New")
    assert status == 200 and body.get("success") is True, body
