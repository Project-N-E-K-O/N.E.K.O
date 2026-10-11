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

"""End-to-end flows of two visit runtimes wired back to back (host ↔ guest)."""

from __future__ import annotations

import pytest

from main_routers.visit_router import runtime as rtm
from main_routers.visit_router import transport_ws
from tests.unit.visit_runtime_harness import bring_up, teardown, wait_for
from utils import visit_route_state


@pytest.fixture(autouse=True)
def _clean():
    rtm._reset_for_tests()
    transport_ws._reset_for_tests()
    visit_route_state._reset_for_tests()
    yield
    rtm._reset_for_tests()
    transport_ws._reset_for_tests()
    visit_route_state._reset_for_tests()


async def test_natural_visit_ends_with_wrap_up_on_both_sides(tmp_path, monkeypatch):
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch)
    try:
        await wait_for(lambda: host.rt.exit_task is not None and guest.rt.exit_task is not None, timeout=20)
        await wait_for(lambda: host.rt.exit_task.done() and guest.rt.exit_task.done(), timeout=40)
        assert guest.rt.finalize_reason == "wrap_up"
        assert host.rt.finalize_reason == "peer_left"
        assert host.rt.peer_reason == "home"
        assert "wrap_up" in wire.sent_types("host")
        leave = [p for p in wire.sent["guest"] if p.get("t") == "leave"]
        assert leave and leave[0]["reason"] == "home"
        # 两侧的转录都封存成了 .upload.json，流水已删
        for side in (host, guest):
            spool_dir = side.config_dir / "visit_spool"
            assert (spool_dir / f"{side.rt.visit_id}.upload.json").exists()
            assert not (spool_dir / f"{side.rt.visit_id}.upload.jsonl").exists()
    finally:
        await teardown(host, guest, wire=wire)
