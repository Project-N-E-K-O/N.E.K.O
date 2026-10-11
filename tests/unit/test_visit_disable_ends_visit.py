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

"""Switching ``visitEnabled`` off ends the live visit (design §3.7.6, ``visit_disabled``).

The settings save route ends live visits only when this save turns a
persisted ``true`` into ``false``: ``visitEnabled`` defaults to off, so a
save that merely carries ``false`` along (every frontend save copies all
keys) must not cut a visit. ``runtime.end_visits_disabled`` finalizes every
live runtime with ``visit_disabled``.
"""

import json

import pytest

from main_routers.config_router import preferences as preferences_router
from main_routers.visit_router import runtime as rtm
from main_routers.visit_router import transport_ws
from tests.unit.test_preferences_concurrency import _Request, _use_preferences_file
from tests.unit.visit_runtime_harness import bring_up, teardown
from utils import visit_route_state


@pytest.fixture(autouse=True)
def _clean():
    rtm._reset_for_tests()
    transport_ws._reset_for_tests()
    visit_route_state._reset_for_tests()
    rtm.register_visit_route_kind()
    yield
    rtm._reset_for_tests()
    transport_ws._reset_for_tests()
    visit_route_state._reset_for_tests()


@pytest.fixture
def ended(monkeypatch):
    calls = []
    monkeypatch.setattr(rtm, "end_visits_disabled", lambda: calls.append(1) or 0)
    return calls


async def _save(body):
    response = await preferences_router.save_conversation_settings(_Request(body))
    assert response.status_code == 200, response.body
    return json.loads(response.body)


@pytest.mark.asyncio
async def test_switching_visits_off_ends_live_visits_once(monkeypatch, tmp_path, ended):
    _use_preferences_file(monkeypatch, tmp_path)
    await _save({"visitEnabled": True})
    assert ended == []
    await _save({"visitEnabled": False, "focusModeEnabled": True})
    assert ended == [1]
    # 之后再带着 false 存别的设置：已经是关，不再结束
    await _save({"visitEnabled": False, "focusModeEnabled": False})
    assert ended == [1]


@pytest.mark.asyncio
async def test_default_off_carried_along_does_not_end_visits(monkeypatch, tmp_path, ended):
    # 从没打开过（缺省为关）：前端每次保存都带 visitEnabled:false，不能把串门掐掉
    _use_preferences_file(monkeypatch, tmp_path)
    await _save({"visitEnabled": False, "slopFilterEnabled": True})
    assert ended == []


@pytest.mark.asyncio
async def test_saving_other_keys_while_on_keeps_the_visit(monkeypatch, tmp_path, ended):
    _use_preferences_file(monkeypatch, tmp_path)
    await _save({"visitEnabled": True})
    await _save({"slopFilterEnabled": False})
    await _save({"visitEnabled": True, "slopFilterEnabled": True})
    assert ended == []


@pytest.mark.asyncio
async def test_end_visits_disabled_finalizes_with_visit_disabled(tmp_path, monkeypatch):
    host, guest, wire, clock, _wall = await bring_up(tmp_path, monkeypatch)
    try:
        assert rtm.end_visits_disabled() == 2
        assert host.rt.finalize_reason == "visit_disabled"
        assert guest.rt.finalize_reason == "visit_disabled"
        assert rtm.end_visits_disabled() == 0          # 已在收尾的不再重起
    finally:
        await teardown(host, guest, wire=wire, clock=clock)
