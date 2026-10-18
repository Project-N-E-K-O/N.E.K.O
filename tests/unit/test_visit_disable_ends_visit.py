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
    # Later saving other settings with false is already disabled; do not end again.
    await _save({"visitEnabled": False, "focusModeEnabled": False})
    assert ended == [1]


@pytest.mark.asyncio
async def test_default_off_carried_along_does_not_end_visits(monkeypatch, tmp_path, ended):
    # Never enabled (default off): frontend saves always include visitEnabled:false, which must not terminate visits.
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


def test_an_older_voice_push_never_overwrites_a_newer_one(monkeypatch):
    # Concurrent saves resume outside the lock: discard the older revision's push (CodeRabbit).
    pushed = []
    monkeypatch.setattr(rtm, "apply_voice_setting", lambda enabled: pushed.append(enabled) or 0)
    monkeypatch.setattr(preferences_router, "_visit_voice_pushed_revision", -1)
    preferences_router._apply_visit_voice(True, 8)       # B (newer) resumes first.
    preferences_router._apply_visit_voice(False, 7)      # A (older) resumes later: discard it.
    preferences_router._apply_visit_voice(False, 9)
    assert pushed == [True, False]


@pytest.mark.asyncio
async def test_a_pending_admission_is_refused_when_visits_are_switched_off(tmp_path, monkeypatch):
    # User disables visits while entry is suspended on await (runtime not yet registered): do not register on waking (CodeRabbit).
    import asyncio

    from tests.unit.visit_runtime_harness import VISIT_ID, FakeClock, make_side, patch_admission

    patch_admission(monkeypatch)
    gate = asyncio.Event()

    async def slow_account():
        await gate.wait()
        return "acct"

    monkeypatch.setattr(rtm, "_local_account", slow_account)
    clock, wall = FakeClock(1000.0), FakeClock(1.7e9)
    side = make_side(tmp_path, "host", clock=clock, wall=wall)
    task = asyncio.ensure_future(rtm.start_visit("Host", "host", visit_id=VISIT_ID, host=side.host, deps=side.deps,
                                                 clock=clock, wall=wall))
    for _ in range(20):
        await asyncio.sleep(0)
    assert rtm.end_visits_disabled() == 0                  # Not yet registered: ending cannot reach it.
    gate.set()
    with pytest.raises(rtm.VisitRefused) as refused:
        await task
    assert refused.value.body.get("reason") == "visit_disabled"
    assert rtm.get_runtime("Host") is None and not rtm._pending_visits


@pytest.mark.asyncio
async def test_saving_the_voice_switch_pushes_it_to_live_visits(monkeypatch, tmp_path):
    monkeypatch.setattr(preferences_router, "_visit_voice_pushed_revision", -1)
    pushed = []
    monkeypatch.setattr(rtm, "apply_voice_setting", lambda enabled: pushed.append(enabled) or 0)
    _use_preferences_file(monkeypatch, tmp_path)
    await _save({"visitVoiceEnabled": True})               # Default -> enabled: still push once; harmless.
    await _save({"visitVoiceEnabled": True, "slopFilterEnabled": False})   # Unchanged: no push.
    await _save({"visitVoiceEnabled": False})
    await _save({"visitVoiceEnabled": True})
    assert pushed == [True, False, True]


@pytest.mark.asyncio
async def test_failing_visit_hooks_do_not_fail_the_saved_settings(monkeypatch, tmp_path):
    def boom(*_args):
        raise RuntimeError("runtime unavailable")

    monkeypatch.setattr(rtm, "end_visits_disabled", boom)
    monkeypatch.setattr(rtm, "apply_voice_setting", boom)
    _use_preferences_file(monkeypatch, tmp_path)
    await _save({"visitEnabled": True, "visitVoiceEnabled": True})
    body = await _save({"visitEnabled": False, "visitVoiceEnabled": False})
    assert body["success"] is True and body["settings"]["visitEnabled"] is False


@pytest.mark.asyncio
async def test_end_visits_disabled_finalizes_with_visit_disabled(tmp_path, monkeypatch):
    host, guest, wire, clock, _wall = await bring_up(tmp_path, monkeypatch)
    try:
        assert rtm.end_visits_disabled() == 2
        assert host.rt.finalize_reason == "visit_disabled"
        assert guest.rt.finalize_reason == "visit_disabled"
        assert rtm.end_visits_disabled() == 0          # Do not restart visits already ending.
    finally:
        await teardown(host, guest, wire=wire, clock=clock)


def test_write_result_carries_the_snapshot_read_under_the_same_lock(monkeypatch, tmp_path):
    # Old and new values come from one locked write: another save cannot intervene between read and write (CodeRabbit: concurrent-save missed transition).
    from utils import preferences

    _use_preferences_file(monkeypatch, tmp_path)
    first = preferences.save_global_conversation_settings_versioned({"visitEnabled": True})
    assert first.success and first.previous is not None and "visitEnabled" not in first.previous.settings
    second = preferences.save_global_conversation_settings_versioned({"visitEnabled": False})
    assert second.previous.settings["visitEnabled"] is True and second.snapshot.settings["visitEnabled"] is False
    assert second.previous.revision + 1 == second.snapshot.revision
    stale = preferences.save_global_conversation_settings_versioned({"visitEnabled": True}, expected_revision=0)
    assert stale.conflict and stale.previous is None
