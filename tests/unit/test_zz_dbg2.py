import asyncio, pytest
from main_routers.visit_router import runtime as rtm
from main_routers.visit_router import transport_ws
from main_routers.visit_router.session_pool import sort_visit_history
from tests.unit.visit_runtime_harness import bring_up, teardown, wait_for, Replies, settle
from utils import visit_route_state

async def test_dbg(tmp_path, monkeypatch):
    rtm._reset_for_tests(); transport_ws._reset_for_tests(); visit_route_state._reset_for_tests()
    after = asyncio.Event()
    hr = Replies(queue=[["主人家开场。"]]); gr = Replies(queue=[["客人开场。"]])
    hr.default = [after, "后续"]; gr.default = [after, "后续"]
    host, guest, wire, clock, wall = await bring_up(tmp_path, monkeypatch, host_replies=hr, guest_replies=gr)
    await wait_for(lambda: len(host.rt.journal.lines()) >= 2 and len(guest.rt.journal.lines()) >= 2)
    await settle(100)
    for side in (host, guest):
        s = side.rt.session
        sort_visit_history(s)
        print(side.name, [(type(m).__name__, s.key_of(m), str(m.content)[:30].replace("\n"," ")) for m in s.history])
        print(side.name, "journal", side.rt.journal.lines())
    after.set()
    await teardown(host, guest, wire=wire, clock=clock)
