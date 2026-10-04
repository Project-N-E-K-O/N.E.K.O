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
import json

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

    async def readable():
        return None

    # 不碰真实运行时根目录里的 characters.json
    monkeypatch.setattr(local_chars, "ensure_characters_readable", readable)
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


def test_ipv4_mapped_loopback_is_local(env):
    client, *_ = env
    assert memory_routes._is_loopback("::ffff:127.0.0.1")
    assert not memory_routes._is_loopback("::ffff:192.168.1.20")
    mapped = TestClient(client.app, client=("::ffff:127.0.0.1", 5000))
    assert mapped.get("/api/visit/memory/peers?catgirl=A", headers=GOOD).status_code == 200


def test_peers_survive_a_damaged_cat_record(env):
    client, _server, tmp_path, _state = env
    _seed(tmp_path)
    path = tmp_path / "visit_peers.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    chars = data["accounts"][OWN_A]["peers"][PEER_X]["by_char"]["A"]["chars"]
    chars[next(iter(chars))] = "broken"
    path.write_text(json.dumps(data), encoding="utf-8")
    resp = client.get("/api/visit/memory/peers?catgirl=A", headers=GOOD)
    assert resp.status_code == 200
    (row,) = resp.json()["peers"]
    assert row["chars"][0]["display_name"] == ""


def test_unreadable_sentinel_answers_retryable_503(env):
    client, server, tmp_path, _state = env
    _seed(tmp_path)
    sentinel_dir = tmp_path / "visit_revocations"
    sentinel_dir.mkdir()
    (sentinel_dir / f"clearing-{'0' * 32}.json").write_text("{torn", encoding="utf-8")
    for path, body in (("/api/visit/memory/forget", {"catgirl": "A", "peer_uid": PEER_X}),
                       ("/api/visit/memory/forget_all", {"catgirl": "A"})):
        resp = client.post(path, json=body, headers=GOOD)
        assert resp.status_code == 503 and resp.json()["retry"] is True
    assert server.requests == []


@pytest.mark.parametrize("path,body", [
    ("/api/visit/memory/forget", {"catgirl": "A", "peer_uid": PEER_X}),
    ("/api/visit/memory/forget_all", {"catgirl": "A"}),
])
def test_rename_landing_before_the_guard_is_taken_uses_the_new_name(env, monkeypatch, path, body):
    import contextlib

    client, server, tmp_path, _state = env
    _seed(tmp_path, own_char="C")                    # 改名迁移已把名册条目搬到新名字 C
    table = {"A": CHAR_UID_A}

    async def chars():
        return dict(table)

    monkeypatch.setattr(local_chars, "load_local_characters", chars)

    @contextlib.asynccontextmanager
    async def guard(_uids):
        table.clear()
        table["C"] = CHAR_UID_A                      # 改名恰在路由解析之后、守卫生效之前提交
        yield

    names = set()
    real_handler = server.handler

    async def handler(request):
        if request.url.path.endswith("/scoped_forget"):
            names.add(request.url.path.rsplit("/", 2)[-2])
        return await real_handler(request)

    server.handler = handler
    memory_routes.configure_memory_routes(lifecycle_guard=guard)
    try:
        resp = client.post(path, json=body, headers=GOOD)
    finally:
        memory_routes.configure_memory_routes(lifecycle_guard=None)
    assert resp.status_code == 200
    assert names == {"C"}
    assert _run(PeerRoster(tmp_path, own_uid=OWN_A).get_char_entry(PEER_X, "C")) is None


@pytest.mark.parametrize("path,body", [
    ("/api/visit/memory/forget", {"catgirl": "A", "peer_uid": PEER_X}),
    ("/api/visit/memory/forget_all", {"catgirl": "A"}),
])
def test_character_unresolvable_under_the_guard_is_a_retryable_503(env, monkeypatch, path, body):
    import contextlib

    client, server, tmp_path, _state = env
    _seed(tmp_path)
    table = {"A": CHAR_UID_A}

    async def chars():
        return dict(table)

    monkeypatch.setattr(local_chars, "load_local_characters", chars)

    @contextlib.asynccontextmanager
    async def guard(_uids):
        table.clear()                                # 拿到守卫时角色配置读不出（或刚被删）
        yield

    memory_routes.configure_memory_routes(lifecycle_guard=guard)
    try:
        resp = client.post(path, json=body, headers=GOOD)
    finally:
        memory_routes.configure_memory_routes(lifecycle_guard=None)
    # 不报成功、也不回 404：分不清「删了」与「一时读不出」，回可重试的 503
    assert resp.status_code == 503 and resp.json()["retry"] is True
    assert server.requests == []


def test_forget_all_with_unreadable_character_config_is_a_retryable_503(env, monkeypatch):
    client, server, tmp_path, _state = env
    _seed(tmp_path)

    async def unreadable():
        raise local_chars.CharactersUnreadable("characters.json unreadable")

    monkeypatch.setattr(local_chars, "ensure_characters_readable", unreadable)
    resp = client.post("/api/visit/memory/forget_all", json={}, headers=GOOD)
    # 常规加载会静默换成默认角色：不能把默认角色当成完整范围报成功
    assert resp.status_code == 503 and resp.json()["retry"] is True
    assert server.requests == []
    assert _run(PeerRoster(tmp_path, own_uid=OWN_A).get_peer(PEER_X)) is not None


def _char(uid=None):
    from utils.config_manager.reserved_schema import set_reserved

    data: dict = {}
    if uid is not None:
        set_reserved(data, "character_uid", uid)
    return data


@pytest.mark.parametrize("body, ok", [
    (None, True),
    ('{"猫娘": {}}', True),
    ("{torn", False),
    ("[]", False),
    ({"猫娘": {"A": _char(CHAR_UID_A), "B": _char(CHAR_UID_B)}}, True),
    ({"猫娘": {"A": _char(CHAR_UID_A), "B": "damaged"}}, False),          # 记录坏了
    ({"猫娘": {"A": _char(CHAR_UID_A), "B": _char()}}, False),             # 缺 id
    ({"猫娘": {"A": _char(CHAR_UID_A), "B": _char("not-a-uid")}}, False),  # id 坏了
    ({"猫娘": {"A": _char(CHAR_UID_A), "B": _char(CHAR_UID_A)}}, False),   # id 重复
], ids=["missing", "empty", "torn", "list", "valid", "bad_record", "no_uid", "bad_uid", "dup_uid"])
def test_character_config_check(tmp_path, body, ok):
    path = tmp_path / "characters.json"
    if body is not None:
        path.write_text(body if isinstance(body, str) else json.dumps(body, ensure_ascii=False),
                        encoding="utf-8")
    if ok:
        local_chars._check_characters_file(str(path))
    else:
        with pytest.raises(local_chars.CharactersUnreadable):
            local_chars._check_characters_file(str(path))


def test_peers_survive_a_damaged_pairs_list(env):
    client, _server, tmp_path, _state = env
    _seed(tmp_path)
    _seed(tmp_path, peer_uid=PEER_Z)
    path = tmp_path / "visit_peers.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["accounts"][OWN_A]["peers"][PEER_X]["by_char"]["A"]["pairs"] = 1
    path.write_text(json.dumps(data), encoding="utf-8")
    resp = client.get("/api/visit/memory/peers?catgirl=A", headers=GOOD)
    # 一条坏了的 pairs 只当作空，其余对端照常列出
    assert resp.status_code == 200
    assert {row["peer_uid"] for row in resp.json()["peers"]} == {PEER_X, PEER_Z}


def test_forget_with_unreadable_character_config_is_a_retryable_503(env, monkeypatch):
    client, server, tmp_path, _state = env
    _seed(tmp_path)

    async def unreadable():
        raise local_chars.CharactersUnreadable("characters.json unreadable")

    monkeypatch.setattr(local_chars, "ensure_characters_readable", unreadable)
    resp = client.post("/api/visit/memory/forget", json={"catgirl": "A", "peer_uid": PEER_X}, headers=GOOD)
    # 不回看似永久的 404：回可重试的 503
    assert resp.status_code == 503 and resp.json()["retry"] is True
    assert server.requests == []
