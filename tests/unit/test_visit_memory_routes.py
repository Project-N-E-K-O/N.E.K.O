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

"""``/api/visit/memory/*`` and ``/api/visit/contacts/block`` (visit design PR-08, section 4.6)."""

from __future__ import annotations

import asyncio

import pytest
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from main_logic.visit import local_chars
from main_logic.visit.limits import Blocklist
from main_logic.visit.subjects import (
    PeerRoster,
    derive_pair_id,
    derive_peer_char_id,
    derive_person_id,
    group_chat_subject,
    group_participant_subject,
    participant_subject,
)
from main_routers.system_router import AUTOSTART_CSRF_TOKEN
from main_routers.visit_router import memory_routes
from tests.fastapi_routes import iter_routes
from tests.unit.visit_memory_test_helpers import (
    CHAR_UID_A,
    CHAR_UID_B,
    OWN_A,
    PEER_X,
    PEER_Y,
    TAG_X,
    TAG_Y,
    FakeMemoryServer,
    seed_roster,
    vid,
)

ORIGIN = "http://testserver"
GOOD = {"Origin": ORIGIN, "X-CSRF-Token": AUTOSTART_CSRF_TOKEN}
PAIR = derive_pair_id(OWN_A, PEER_X)
PEER_Z = "3" * 24


@pytest.fixture
def env(tmp_path, monkeypatch):
    server = FakeMemoryServer()
    state = {"active": set(), "blocked_calls": []}

    async def own_uid():
        return OWN_A

    async def on_blocked(peer_uid):
        state["blocked_calls"].append(peer_uid)

    memory_routes.configure_memory_routes(
        own_visit_uid=own_uid, is_visit_active=lambda name: name in state["active"],
        config_dir=lambda: tmp_path, client=server.client, on_blocked=on_blocked,
    )

    async def chars():
        return {"A": CHAR_UID_A, "B": CHAR_UID_B}

    monkeypatch.setattr(local_chars, "load_local_characters", chars)
    monkeypatch.delenv("NEKO_BEHIND_PROXY", raising=False)
    app = FastAPI()
    outer = APIRouter(prefix="/api/visit")
    outer.include_router(memory_routes.router)
    app.include_router(outer)
    client = TestClient(app, client=("127.0.0.1", 50000))
    yield client, server, tmp_path, state
    memory_routes.configure_memory_routes(
        own_visit_uid=memory_routes._no_account, is_visit_active=lambda _n: False,
        config_dir=memory_routes._default_config_dir, client=memory_routes.memory_bridge.default_client,
        on_blocked=None, admission_lock=None,
    )


def _run(coro):
    return asyncio.run(coro)


def _seed(tmp_path, **kw):
    return _run(seed_roster(tmp_path, **kw))


def test_routes_are_mounted_under_api_visit_without_trailing_slash(env):
    client, *_ = env
    paths = {route.path for route in iter_routes(client.app.routes)}
    assert {"/api/visit/memory/peers", "/api/visit/memory/forget",
            "/api/visit/memory/forget_all", "/api/visit/contacts/block"} <= paths
    assert not any(p.startswith("/api/visit/api/visit") or p.endswith("/") for p in paths)


@pytest.mark.parametrize("method,path,body", [
    ("get", "/api/visit/memory/peers?catgirl=A", None),
    ("post", "/api/visit/memory/forget", {"catgirl": "A", "peer_uid": PEER_X}),
    ("post", "/api/visit/memory/forget_all", {"catgirl": "A"}),
    ("post", "/api/visit/contacts/block", {"peer_uid": PEER_X, "blocked": True}),
])
def test_local_origin_gate(env, method, path, body):
    client, server, tmp_path, _state = env
    _seed(tmp_path)
    call = getattr(client, method)
    kwargs = {} if body is None else {"json": body}
    assert call(path, headers={"Origin": ORIGIN}, **kwargs).status_code == 403           # 缺 token
    assert call(path, headers={"Origin": ORIGIN, "X-CSRF-Token": "wrong"}, **kwargs).status_code == 403
    assert call(path, headers={"Origin": "http://evil.example", "X-CSRF-Token": AUTOSTART_CSRF_TOKEN},
                **kwargs).status_code == 403
    assert call(path, headers={**GOOD, "X-Forwarded-For": "127.0.0.1"}, **kwargs).status_code == 403
    assert server.requests == []                                                        # 失败不产生副作用
    assert not (tmp_path / "visit_blocklist.json").exists()
    assert call(path, headers=GOOD, **kwargs).status_code == 200                         # 允许的 Origin + token


def test_non_loopback_peer_is_rejected_even_with_token(env, tmp_path):
    client, *_ = env
    remote = TestClient(client.app, client=("192.168.1.20", 5000))
    assert remote.get("/api/visit/memory/peers?catgirl=A", headers=GOOD).status_code == 403


def test_proxy_mode_rejects_everything(env, monkeypatch):
    client, *_ = env
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    assert client.get("/api/visit/memory/peers?catgirl=A", headers=GOOD).status_code == 403


def test_peers_lists_only_people_of_that_character_with_full_uid(env):
    client, server, tmp_path, _state = env
    _seed(tmp_path)
    _seed(tmp_path, peer_uid=PEER_Y, own_char="B")
    cat_x = derive_peer_char_id(PEER_X, TAG_X)
    server.subjects = [
        {"subject_kind": "participant", "subject_id": f"neko_visit:{derive_person_id(OWN_A, PEER_X)}",
         "facts": 3, "reflections": 1},
        {"subject_kind": "group_participant", "subject_id": f"neko_visit:{PAIR}:{cat_x}", "facts": 2,
         "reflections": 0},
    ]
    resp = client.get("/api/visit/memory/peers?catgirl=A", headers=GOOD)
    assert resp.status_code == 200
    peers = resp.json()["peers"]
    assert [p["peer_uid"] for p in peers] == [PEER_X]
    row = peers[0]
    assert row["short_id"] == PEER_X[:6].upper() and len(row["peer_uid"]) == 24
    assert row["display_name"] == "Xiaoming" and row["blocked"] is False
    assert row["fact_count"] == 5 and row["reflection_count"] == 1
    assert row["chars"] == [{"peer_char_id": cat_x, "display_name": "Mimi", "pair_id": PAIR,
                             "last_visit_at": 100.0, "fact_count": 2}]
    assert "last_summary" not in str(row)
    # 往返：直接回填 forget / block 请求体
    assert client.post("/api/visit/contacts/block", json={"peer_uid": row["peer_uid"], "blocked": True},
                       headers=GOOD).json()["ok"]
    assert client.get("/api/visit/memory/peers?catgirl=A", headers=GOOD).json()["peers"][0]["blocked"]
    resp = client.post("/api/visit/memory/forget", json={"catgirl": "A", "peer_uid": row["peer_uid"]},
                       headers=GOOD)
    assert resp.json() == {"ok": True, "forgotten": 1}
    assert client.get("/api/visit/memory/peers?catgirl=A", headers=GOOD).json()["peers"] == []
    # 拉黑不是记忆：清除不动黑名单
    assert Blocklist.load(tmp_path).is_blocked(PEER_X)


def test_forget_clears_both_cats_then_the_roster_entry(env):
    client, server, tmp_path, _state = env
    roster = _seed(tmp_path)
    _seed(tmp_path, tag=TAG_Y)
    _run(roster.set_last_summary(PEER_X, "A", visit_id=vid(1), ended_at=1.0, text="s", pair_id=PAIR))
    resp = client.post("/api/visit/memory/forget", json={"catgirl": "A", "peer_uid": PEER_X}, headers=GOOD)
    assert resp.status_code == 200
    subjects = [c["subject"] for c in server.calls("scoped_forget")]
    assert subjects == [
        group_chat_subject(PAIR),
        group_participant_subject(PAIR, derive_peer_char_id(PEER_X, TAG_X)),
        group_participant_subject(PAIR, derive_peer_char_id(PEER_X, TAG_Y)),
        participant_subject(derive_person_id(OWN_A, PEER_X)),
    ]
    assert _run(PeerRoster(tmp_path, own_uid=OWN_A).get_peer(PEER_X)) is None
    assert not list((tmp_path / "visit_revocations").glob("*.json"))


def test_forget_only_touches_that_character(env):
    client, server, tmp_path, _state = env
    _seed(tmp_path)
    _seed(tmp_path, own_char="B")
    assert client.post("/api/visit/memory/forget", json={"catgirl": "A", "peer_uid": PEER_X},
                       headers=GOOD).status_code == 200
    assert server.calls("scoped_forget")
    assert {path for path, _body in server.requests} == {"scoped_forget"}
    peer = _run(PeerRoster(tmp_path, own_uid=OWN_A).get_peer(PEER_X))
    assert set(peer["by_char"]) == {"B"}


def test_forget_is_refused_during_a_visit(env):
    client, server, tmp_path, state = env
    _seed(tmp_path)
    state["active"].add("A")
    resp = client.post("/api/visit/memory/forget", json={"catgirl": "A", "peer_uid": PEER_X}, headers=GOOD)
    assert resp.status_code == 409 and resp.json()["code"] == "visit_active"
    assert server.requests == [] and not (tmp_path / "visit_revocations").exists()


def test_memory_server_down_keeps_the_log_for_replay(env):
    client, server, tmp_path, _state = env
    _seed(tmp_path)
    server.fail_always.add("scoped_forget")
    resp = client.post("/api/visit/memory/forget", json={"catgirl": "A", "peer_uid": PEER_X}, headers=GOOD)
    assert resp.status_code == 503 and resp.json()["retry"] is True
    logs = list((tmp_path / "visit_revocations").glob("*.json"))
    assert len(logs) == 2           # 撤销日志 + 清除意图哨兵，留给补录重放


def test_forget_all_clears_everyone_under_the_character(env):
    client, server, tmp_path, _state = env
    _seed(tmp_path)
    _seed(tmp_path, peer_uid=PEER_Y)
    _seed(tmp_path, peer_uid=PEER_Y, own_char="B")
    _seed(tmp_path, peer_uid=PEER_Z, own_char="B")
    resp = client.post("/api/visit/memory/forget_all", json={"catgirl": "A"}, headers=GOOD)
    assert resp.json() == {"ok": True, "forgotten": 2}
    z_person = participant_subject(derive_person_id(OWN_A, PEER_Z))
    assert z_person not in [c["subject"] for c in server.calls("scoped_forget")]
    peers = _run(PeerRoster(tmp_path, own_uid=OWN_A).list_peers())
    assert sorted(peers) == [PEER_Y, PEER_Z] and set(peers[PEER_Y]["by_char"]) == {"B"}
    resp = client.post("/api/visit/memory/forget_all", json={}, headers=GOOD)
    assert resp.json() == {"ok": True, "forgotten": 2}
    assert _run(PeerRoster(tmp_path, own_uid=OWN_A).list_peers()) == {}


def test_block_toggles_and_reacts_in_visit(env):
    client, _server, tmp_path, state = env
    _seed(tmp_path)
    assert client.post("/api/visit/contacts/block", json={"peer_uid": PEER_X, "blocked": True},
                       headers=GOOD).json() == {"ok": True, "changed": True}
    entry = Blocklist.load(tmp_path).get(PEER_X)
    assert entry.display_name_at_block == "Xiaoming"
    assert state["blocked_calls"] == [PEER_X]
    assert client.post("/api/visit/contacts/block", json={"peer_uid": PEER_X, "blocked": False},
                       headers=GOOD).json() == {"ok": True, "changed": True}
    assert not Blocklist.load(tmp_path).is_blocked(PEER_X)
    assert client.post("/api/visit/contacts/block", json={"peer_uid": PEER_X, "blocked": "yes"},
                       headers=GOOD).status_code == 400


def test_unknown_account_lists_nothing_and_refuses_changes(env):
    client, server, tmp_path, _state = env
    _seed(tmp_path)

    async def none():
        return None

    memory_routes.configure_memory_routes(own_visit_uid=none)
    assert client.get("/api/visit/memory/peers?catgirl=A", headers=GOOD).json() == {"peers": []}
    resp = client.post("/api/visit/memory/forget", json={"catgirl": "A", "peer_uid": PEER_X}, headers=GOOD)
    assert resp.status_code == 409 and resp.json()["code"] == "VISIT_LOGIN_REQUIRED"
    assert server.requests == []


def test_forget_voids_unwritten_debriefs_of_that_person(env):
    client, _server, tmp_path, _state = env
    from tests.unit.visit_memory_test_helpers import ln, make_visit

    _seed(tmp_path)
    _seed(tmp_path, peer_uid=PEER_Y)
    mine = _run(make_visit(tmp_path, vid(1), [ln(0)], debrief_choice="preview:diary",
                           debrief_pending={"diary": "d", "facts": []}, debrief_chip_pending=True))
    other = _run(make_visit(tmp_path, vid(2), [ln(0)], peer_uid=PEER_Y, debrief_choice="ask_later"))
    assert client.post("/api/visit/memory/forget", json={"catgirl": "A", "peer_uid": PEER_X},
                       headers=GOOD).status_code == 200
    state = _run(mine.read_state())
    assert state["debrief_choice"] == "forget" and state["debrief_pending"] is None
    assert state["peer_uid"] is None and state["debrief_chip_pending"] is False
    assert mine.jsonl_path.exists()                    # 转录正文不删，等结清或 7 天回收
    assert _run(other.read_state())["debrief_choice"] == "ask_later"
