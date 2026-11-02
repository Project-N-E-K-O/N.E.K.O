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
from main_routers.visit_router import local_guard, memory_routes
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

    # Do not touch characters.json in the real runtime directory
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
            "/api/visit/memory/forget_all", "/api/visit/memory/forget/discard_unreadable",
            "/api/visit/contacts/block"} <= paths
    assert not any(p.startswith("/api/visit/api/visit") or p.endswith("/") for p in paths)


@pytest.mark.parametrize("method,path,body", [
    ("get", "/api/visit/memory/peers?catgirl=A", None),
    ("post", "/api/visit/memory/forget", {"catgirl": "A", "peer_uid": PEER_X}),
    ("post", "/api/visit/memory/forget_all", {"catgirl": "A"}),
    ("post", "/api/visit/memory/forget/discard_unreadable", {"confirm": True}),
    ("post", "/api/visit/contacts/block", {"peer_uid": PEER_X, "blocked": True}),
])
def test_local_origin_gate(env, method, path, body):
    client, server, tmp_path, _state = env
    _seed(tmp_path)
    call = getattr(client, method)
    kwargs = {} if body is None else {"json": body}
    assert call(path, headers={"Origin": ORIGIN}, **kwargs).status_code == 403           # Missing token
    assert call(path, headers={"Origin": ORIGIN, "X-CSRF-Token": "wrong"}, **kwargs).status_code == 403
    assert call(path, headers={"Origin": "http://evil.example", "X-CSRF-Token": AUTOSTART_CSRF_TOKEN},
                **kwargs).status_code == 403
    assert call(path, headers={**GOOD, "X-Forwarded-For": "127.0.0.1"}, **kwargs).status_code == 403
    assert server.requests == []                                                        # Failure causes no side effects
    assert not (tmp_path / "visit_blocklist.json").exists()
    assert call(path, headers=GOOD, **kwargs).status_code == 200                         # Allowed Origin + token


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
    # Round trip: populate forget / block request bodies directly
    assert client.post("/api/visit/contacts/block", json={"peer_uid": row["peer_uid"], "blocked": True},
                       headers=GOOD).json()["ok"]
    assert client.get("/api/visit/memory/peers?catgirl=A", headers=GOOD).json()["peers"][0]["blocked"]
    resp = client.post("/api/visit/memory/forget", json={"catgirl": "A", "peer_uid": row["peer_uid"]},
                       headers=GOOD)
    assert resp.json() == {"ok": True, "forgotten": 1}
    assert client.get("/api/visit/memory/peers?catgirl=A", headers=GOOD).json()["peers"] == []
    # Blocking is not memory: forgetting does not alter the blocklist
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
    assert len(logs) == 2           # Revocation log + forget-intent sentinel, retained for startup reconciliation replay


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
    assert mine.jsonl_path.exists()                    # Do not delete transcript contents; retain them until settled or reclaimed after 7 days
    assert _run(other.read_state())["debrief_choice"] == "ask_later"


def test_ipv4_mapped_loopback_is_local(env):
    client, *_ = env
    assert local_guard.is_loopback_host("::ffff:127.0.0.1")
    assert not local_guard.is_loopback_host("::ffff:192.168.1.20")
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


@pytest.mark.parametrize("damaged", ["sentinel", "log"])
def test_unreadable_forget_record_answers_its_reason_not_retry(env, damaged):
    # Unreadable forget record: retry always fails, returning non-retryable 503 with a reason; the page then offers a discard action
    client, server, tmp_path, _state = env
    _seed(tmp_path)
    sentinel_dir = tmp_path / "visit_revocations"
    sentinel_dir.mkdir()
    name = f"clearing-{'0' * 32}.json" if damaged == "sentinel" else f"{'0' * 32}.json"
    (sentinel_dir / name).write_text("{torn", encoding="utf-8")
    for path, body in (("/api/visit/memory/forget", {"catgirl": "A", "peer_uid": PEER_X}),
                       ("/api/visit/memory/forget_all", {"catgirl": "A"}),
                       ("/api/visit/memory/forget_all", {})):
        resp = client.post(path, json=body, headers=GOOD)
        assert resp.status_code == 503, (path, resp.text)
        assert resp.json() == {"ok": False, "code": "forget_failed",
                               "reason": "forget_record_unreadable", "retry": False}
    assert server.requests == []
    # After discarding, the same forget operation completes normally
    assert client.post("/api/visit/memory/forget/discard_unreadable", json={"confirm": True},
                       headers=GOOD).json() == {"ok": True, "discarded": 1}
    resp = client.post("/api/visit/memory/forget", json={"catgirl": "A", "peer_uid": PEER_X}, headers=GOOD)
    assert resp.status_code == 200 and resp.json()["ok"] is True


def test_other_forget_failures_stay_retryable_without_reason(env, monkeypatch):
    client, _server, tmp_path, _state = env
    _seed(tmp_path)

    async def broken(*_args, **_kwargs):
        raise OSError("disk hiccup")

    monkeypatch.setattr(memory_routes, "forget_person", broken)
    monkeypatch.setattr(memory_routes, "forget_all", broken)
    for path, body in (("/api/visit/memory/forget", {"catgirl": "A", "peer_uid": PEER_X}),
                       ("/api/visit/memory/forget_all", {"catgirl": "A"})):
        resp = client.post(path, json=body, headers=GOOD)
        assert resp.status_code == 503 and resp.json() == {"ok": False, "code": "forget_failed", "retry": True}


@pytest.mark.parametrize("path,body", [
    ("/api/visit/memory/forget", {"catgirl": "A", "peer_uid": PEER_X}),
    ("/api/visit/memory/forget_all", {"catgirl": "A"}),
])
def test_rename_landing_before_the_guard_is_taken_uses_the_new_name(env, monkeypatch, path, body):
    import contextlib

    client, server, tmp_path, _state = env
    _seed(tmp_path, own_char="C")                    # Rename migration already moved the roster entry to the new name C
    table = {"A": CHAR_UID_A}

    async def chars():
        return dict(table)

    monkeypatch.setattr(local_chars, "load_local_characters", chars)

    @contextlib.asynccontextmanager
    async def guard(_uids):
        table.clear()
        table["C"] = CHAR_UID_A                      # Rename commits immediately after route resolution and before the guard takes effect
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
        table.clear()                                # The character config is unreadable (or was just deleted) when the guard is acquired
        yield

    memory_routes.configure_memory_routes(lifecycle_guard=guard)
    try:
        resp = client.post(path, json=body, headers=GOOD)
    finally:
        memory_routes.configure_memory_routes(lifecycle_guard=None)
    # Do not report success or return 404: deletion and temporary unreadability cannot be distinguished, so return retryable 503
    assert resp.status_code == 503 and resp.json()["retry"] is True
    assert server.requests == []


def test_forget_all_with_unreadable_character_config_is_a_retryable_503(env, monkeypatch):
    client, server, tmp_path, _state = env
    _seed(tmp_path)

    async def unreadable():
        raise local_chars.CharactersUnreadable("characters.json unreadable")

    monkeypatch.setattr(local_chars, "ensure_characters_readable", unreadable)
    resp = client.post("/api/visit/memory/forget_all", json={}, headers=GOOD)
    # Normal loading silently falls back to the default character: do not report success as if the default represented the full scope
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
    ({"猫娘": {"A": _char(CHAR_UID_A), "B": "damaged"}}, False),          # Corrupted record
    ({"猫娘": {"A": _char(CHAR_UID_A), "B": _char()}}, False),             # Missing id
    ({"猫娘": {"A": _char(CHAR_UID_A), "B": _char("not-a-uid")}}, False),  # Invalid id
    ({"猫娘": {"A": _char(CHAR_UID_A), "B": _char(CHAR_UID_A)}}, False),   # Duplicate id
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
    # Treat a malformed pairs value as empty and continue listing the other peers
    assert resp.status_code == 200
    assert {row["peer_uid"] for row in resp.json()["peers"]} == {PEER_X, PEER_Z}


def test_forget_with_unreadable_character_config_is_a_retryable_503(env, monkeypatch):
    client, server, tmp_path, _state = env
    _seed(tmp_path)

    async def unreadable():
        raise local_chars.CharactersUnreadable("characters.json unreadable")

    monkeypatch.setattr(local_chars, "ensure_characters_readable", unreadable)
    resp = client.post("/api/visit/memory/forget", json={"catgirl": "A", "peer_uid": PEER_X}, headers=GOOD)
    # Return retryable 503 instead of an apparently permanent 404
    assert resp.status_code == 503 and resp.json()["retry"] is True
    assert server.requests == []


def test_peers_skip_a_malformed_peer_id(env):
    client, _server, tmp_path, _state = env
    _seed(tmp_path)
    path = tmp_path / "visit_peers.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    peers = data["accounts"][OWN_A]["peers"]
    peers[""] = json.loads(json.dumps(peers[PEER_X]))       # Invalid peer ID
    path.write_text(json.dumps(data), encoding="utf-8")
    resp = client.get("/api/visit/memory/peers?catgirl=A", headers=GOOD)
    # Skip the entry with the invalid ID and continue listing the other peers
    assert resp.status_code == 200
    assert [row["peer_uid"] for row in resp.json()["peers"]] == [PEER_X]


@pytest.mark.parametrize("damage", ["pair", "char"])
def test_peers_skip_a_malformed_pair_or_char_id(env, damage):
    client, _server, tmp_path, _state = env
    _seed(tmp_path)
    path = tmp_path / "visit_peers.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    entry = data["accounts"][OWN_A]["peers"][PEER_X]["by_char"]["A"]
    if damage == "pair":
        entry["pairs"].append("")
    else:
        entry["chars"][""] = {"display_name": "x"}
    path.write_text(json.dumps(data), encoding="utf-8")
    resp = client.get("/api/visit/memory/peers?catgirl=A", headers=GOOD)
    # Skip only the pair ID that cannot form a subject
    assert resp.status_code == 200 and [row["peer_uid"] for row in resp.json()["peers"]] == [PEER_X]


@pytest.mark.parametrize("path,body", [
    ("/api/visit/memory/forget", {"catgirl": "A", "peer_uid": PEER_X}),
    ("/api/visit/memory/forget_all", {"catgirl": "A"}),
])
def test_forget_waits_for_a_pending_rename_to_be_reconciled(env, path, body):
    client, server, tmp_path, _state = env
    _seed(tmp_path)
    peers_path = tmp_path / "visit_peers.json"
    data = json.loads(peers_path.read_text(encoding="utf-8"))
    data["pending_rename"] = {"old": "A0", "new": "A"}       # Rename failed before roster migration
    peers_path.write_text(json.dumps(data), encoding="utf-8")
    resp = client.post(path, json=body, headers=GOOD)
    # Do not expand the new name into an empty entry and report success: return retryable 503 while startup reconciliation catches up
    assert resp.status_code == 503 and resp.json()["retry"] is True
    assert server.requests == []
    assert not list((tmp_path / "visit_revocations").glob("*.json"))      # No sentinel or log was written



def test_peers_show_blocked_regardless_of_uid_case(env):
    client, _server, tmp_path, _state = env
    mixed = "AbCdEf" + "1" * 18
    _seed(tmp_path, peer_uid=mixed)
    assert client.post("/api/visit/contacts/block", json={"peer_uid": mixed, "blocked": True},
                       headers=GOOD).status_code == 200
    rows = client.get("/api/visit/memory/peers?catgirl=A", headers=GOOD).json()["peers"]
    # The blocklist stores lowercase values while the roster preserves original case: let Blocklist normalize before comparing
    assert [row["blocked"] for row in rows if row["peer_uid"] == mixed] == [True]


def test_block_reports_success_when_ending_the_live_visit_fails(env):
    client, _server, tmp_path, _state = env
    _seed(tmp_path)

    async def failing(_peer_uid):
        raise RuntimeError("runtime gone")

    memory_routes.configure_memory_routes(on_blocked=failing)
    resp = client.post("/api/visit/contacts/block", json={"peer_uid": PEER_X, "blocked": True}, headers=GOOD)
    # The block is already persisted: do not return 500; accurately report that the active peer session could not be ended
    assert resp.status_code == 200 and resp.json() == {"ok": True, "changed": True, "ended": False}
    assert Blocklist.load(tmp_path).is_blocked(PEER_X)


def test_rename_marker_of_another_character_does_not_block_forget(env):
    client, server, tmp_path, _state = env
    _seed(tmp_path)
    peers_path = tmp_path / "visit_peers.json"
    data = json.loads(peers_path.read_text(encoding="utf-8"))
    data["pending_rename"] = {"old": "Z0", "new": "Z1"}       # Rename unrelated to character A
    peers_path.write_text(json.dumps(data), encoding="utf-8")
    resp = client.post("/api/visit/memory/forget", json={"catgirl": "A", "peer_uid": PEER_X}, headers=GOOD)
    # The rename gate blocks forgetting only when it involves either of those two names
    assert resp.status_code == 200 and server.calls("scoped_forget")


@pytest.mark.parametrize("peer_uid", [" ", "\u3000", f" {PEER_X}"])
def test_blank_or_padded_peer_ids_are_rejected(env, peer_uid):
    client, server, tmp_path, state = env
    _seed(tmp_path)
    resp = client.post("/api/visit/contacts/block", json={"peer_uid": peer_uid, "blocked": True}, headers=GOOD)
    # Blocking strips first: all-whitespace becomes empty and raises 500, while a value with surrounding whitespace becomes a different ID from forget
    assert resp.status_code == 400 and state["blocked_calls"] == []
    resp = client.post("/api/visit/memory/forget", json={"catgirl": "A", "peer_uid": peer_uid}, headers=GOOD)
    assert resp.status_code == 400 and server.requests == []


@pytest.mark.parametrize("first_seen", [float("nan"), 10 ** 400], ids=["nan", "huge_int"])
def test_peers_survive_non_finite_timestamps(env, first_seen):
    client, _server, tmp_path, _state = env
    _seed(tmp_path)
    path = tmp_path / "visit_peers.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    peer = data["accounts"][OWN_A]["peers"][PEER_X]
    peer["first_seen"] = first_seen
    peer["last_seen"] = float("inf")
    chars = peer["by_char"]["A"]["chars"]
    chars[next(iter(chars))]["last_seen"] = float("-inf")
    path.write_text(json.dumps(data), encoding="utf-8")         # Write NaN / Infinity literals
    resp = client.get("/api/visit/memory/peers?catgirl=A", headers=GOOD)
    # Return null for an invalid timestamp so the entire list can still be serialized
    assert resp.status_code == 200
    (row,) = resp.json()["peers"]
    assert row["first_seen"] is None and row["last_seen"] is None
    assert row["chars"][0]["last_visit_at"] is None


@pytest.mark.parametrize("marker", [{"old": ["A"], "new": "Z1"}, {"old": "Z0"}, {"old": 5, "new": "Z1"}],
                         ids=["unhashable", "missing_field", "not_string"])
def test_malformed_rename_marker_blocks_forget_without_500(env, marker):
    client, server, tmp_path, _state = env
    _seed(tmp_path)
    peers_path = tmp_path / "visit_peers.json"
    data = json.loads(peers_path.read_text(encoding="utf-8"))
    data["pending_rename"] = marker
    peers_path.write_text(json.dumps(data), encoding="utf-8")
    resp = client.post("/api/visit/memory/forget", json={"catgirl": "A", "peer_uid": PEER_X}, headers=GOOD)
    # Could not determine which two names are involved: neither allow it as unrelated nor raise TypeError and return 500; return retryable 503
    assert resp.status_code == 503 and resp.json()["retry"] is True
    assert server.calls("scoped_forget") == []


def test_block_ends_the_live_visit_by_the_canonical_uid(env):
    client, _server, tmp_path, state = env
    peer = "abcdef012345abcdef012345"                          # Alphabetic uid, where case variants differ
    _seed(tmp_path, peer_uid=peer)
    resp = client.post("/api/visit/contacts/block", json={"peer_uid": peer.upper(), "blocked": True}, headers=GOOD)
    assert resp.status_code == 200 and resp.json()["changed"] is True
    # Blocklist stores lowercase; use the same canonical form to end an active peer session, or that session cannot be found
    assert state["blocked_calls"] == [peer]
    assert Blocklist.load(tmp_path).get(peer).display_name_at_block == "Xiaoming"


def test_forget_finds_the_person_by_the_canonical_uid(env):
    client, server, tmp_path, _state = env
    peer = "abcdef012345abcdef012345"                          # Alphabetic uid, where case variants differ
    _seed(tmp_path, peer_uid=peer)
    resp = client.post("/api/visit/memory/forget", json={"catgirl": "A", "peer_uid": peer.upper()}, headers=GOOD)
    # Match people case-insensitively, like the blocklist: an uppercase variant must still find and forget this roster entry, not clear an unrelated subject
    assert resp.status_code == 200 and resp.json()["forgotten"] == 1
    assert _run(PeerRoster(tmp_path, own_uid=OWN_A).get_peer(peer)) is None
    assert server.calls("scoped_forget")


def test_forget_of_someone_never_visited_writes_nothing(env):
    client, server, tmp_path, _state = env
    _seed(tmp_path)                                   # The roster contains only PEER_X
    server.fail_always.add("scoped_forget")           # No sentinel is left behind when memory_server is unavailable
    resp = client.post("/api/visit/memory/forget", json={"catgirl": "A", "peer_uid": PEER_Z}, headers=GOOD)
    # Misspelled / never chatted with this person: accurately return 0; write no sentinel, log nothing, and send no scoped_forget
    assert resp.status_code == 200 and resp.json() == {"ok": True, "forgotten": 0}
    assert server.requests == []
    assert not list((tmp_path / "visit_revocations").glob("*.json"))


def test_forget_of_someone_only_left_in_a_visit_still_runs(env):
    from tests.unit.visit_memory_test_helpers import ln, make_visit

    client, server, tmp_path, _state = env
    # The roster entry is gone, but an active peer session still points to this pair: complete the forget normally (remove the peer identity from the spool)
    _run(make_visit(tmp_path, vid(5), [ln(0, "你好")]))
    resp = client.post("/api/visit/memory/forget", json={"catgirl": "A", "peer_uid": PEER_X}, headers=GOOD)
    assert resp.status_code == 200 and resp.json()["forgotten"] == 1
    assert server.calls("scoped_forget")


def test_forget_of_an_unknown_peer_with_a_damaged_roster_is_retryable(env):
    client, server, tmp_path, _state = env
    (tmp_path / "visit_peers.json").write_text('{"accounts": {', encoding="utf-8")
    resp = client.post("/api/visit/memory/forget", json={"catgirl": "A", "peer_uid": PEER_Z}, headers=GOOD)
    # If the roster cannot be read, do not treat it as "person not found" and return 0
    assert resp.status_code == 503 and resp.json()["retry"] is True


def test_peers_survive_lone_surrogates_in_display_names(env):
    client, _server, tmp_path, _state = env
    _seed(tmp_path)
    path = tmp_path / "visit_peers.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    peer = data["accounts"][OWN_A]["peers"][PEER_X]
    lone = chr(0xD800)
    peer["display_name"] = "Xiao" + lone + "ming"
    chars = peer["by_char"]["A"]["chars"]
    chars[next(iter(chars))]["display_name"] = lone + "Mimi"
    path.write_text(json.dumps(data, ensure_ascii=True), encoding="utf-8")   # Write an escaped form that json.load reads back as a lone surrogate
    resp = client.get("/api/visit/memory/peers?catgirl=A", headers=GOOD)
    # Remove characters that cannot be encoded so the entire list does not return 500
    assert resp.status_code == 200
    (row,) = resp.json()["peers"]
    assert row["display_name"] == "Xiaoming" and row["chars"][0]["display_name"] == "Mimi"


# ── Discard unreadable forget records ────────────────────────────────────

DISCARD = "/api/visit/memory/forget/discard_unreadable"


def _damage(tmp_path):
    directory = tmp_path / "visit_revocations"
    directory.mkdir(exist_ok=True)
    log = directory / f"{'0' * 32}.json"
    log.write_text("{torn", encoding="utf-8")
    sentinel = directory / f"clearing-{'0' * 32}.json"
    sentinel.write_text(json.dumps({"v": 99}), encoding="utf-8")       # A new format that cannot be read after downgrade
    return log, sentinel


def test_discard_needs_explicit_confirmation(env):
    client, _server, tmp_path, _state = env
    log, sentinel = _damage(tmp_path)
    for body in ({}, {"confirm": False}, {"confirm": "true"}, {"confirm": 1}):
        resp = client.post(DISCARD, json=body, headers=GOOD)
        assert resp.status_code == 400 and resp.json()["code"] == "confirm_required"
    assert log.exists() and sentinel.exists()


def test_discard_is_denied_without_token_through_proxy_from_docker_and_lan(env, monkeypatch):
    client, _server, tmp_path, _state = env
    log, sentinel = _damage(tmp_path)
    body = {"confirm": True}
    assert client.post(DISCARD, json=body, headers={"Origin": ORIGIN}).status_code == 403
    assert client.post(DISCARD, json=body, headers={**GOOD, "X-Forwarded-For": "127.0.0.1"}).status_code == 403
    for addr in (("172.17.0.2", 5000), ("192.168.1.20", 5000)):
        resp = TestClient(client.app, client=addr).post(DISCARD, json=body, headers=GOOD)
        assert resp.status_code == 403 and resp.json()["code"] == "VISIT_E_UNAUTHORIZED"
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    assert client.post(DISCARD, json=body, headers=GOOD).status_code == 403
    assert log.exists() and sentinel.exists()
    assert not (tmp_path / "visit_revocations" / "quarantine").exists()


def test_discard_quarantines_only_unreadable_records(env, caplog):
    from main_logic.visit.forget import ClearingSentinels, RevocationLog
    from main_logic.visit.forget_runner import open_person_log

    client, _server, tmp_path, _state = env
    _seed(tmp_path)
    rev_id = _run(open_person_log(tmp_path, own_uid=OWN_A, own_char="A", own_char_uid=CHAR_UID_A,
                                  peer_uid=PEER_X))
    good_sentinel = _run(ClearingSentinels(tmp_path).create(own_uid=OWN_A, scope="chars",
                                                            own_char_uids=[CHAR_UID_A]))
    directory = tmp_path / "visit_revocations"
    readable = {p.name: p.read_bytes() for p in directory.glob("*.json")}
    log, sentinel = _damage(tmp_path)
    with caplog.at_level("WARNING"):
        resp = client.post(DISCARD, json={"confirm": True}, headers=GOOD)
    assert resp.status_code == 200 and resp.json() == {"ok": True, "discarded": 2}
    # Leave readable logs and sentinels unchanged
    assert {p.name: p.read_bytes() for p in directory.glob("*.json")} == readable
    assert f"{rev_id}.json" in readable and f"{good_sentinel['op_id']}.json" in readable
    # Move corrupt files to quarantine (do not delete them), with one diagnostic per file
    moved = sorted(p.name for p in (directory / "quarantine").iterdir())
    assert len(moved) == 2 and moved[0].startswith(log.name) and moved[1].startswith(sentinel.name)
    assert sum("forget_record_discarded" in r.getMessage() for r in caplog.records) == 2
    assert _run(RevocationLog.list_all_open(tmp_path))[0]["id"] == rev_id
    # Discard again: nothing unreadable remains
    assert client.post(DISCARD, json={"confirm": True}, headers=GOOD).json() == {"ok": True, "discarded": 0}


def test_startup_replay_ignores_quarantined_records(env):
    from main_logic.visit.forget import RevocationLog
    from main_logic.visit.forget_runner import replay_forgets
    from tests.unit.visit_memory_test_helpers import resolver

    client, server, tmp_path, _state = env
    _damage(tmp_path)
    assert _run(replay_forgets(tmp_path, resolve_char_name=resolver(), client=server.client())) is False
    assert client.post(DISCARD, json={"confirm": True}, headers=GOOD).json()["discarded"] == 2
    quarantined = sorted((tmp_path / "visit_revocations" / "quarantine").iterdir())
    assert _run(RevocationLog.list_all_open(tmp_path)) == []
    assert _run(replay_forgets(tmp_path, resolve_char_name=resolver(), client=server.client())) is True
    assert sorted((tmp_path / "visit_revocations" / "quarantine").iterdir()) == quarantined
    assert server.requests == []


def test_discard_with_nothing_on_disk_is_a_no_op(env):
    client, _server, tmp_path, _state = env
    assert client.post(DISCARD, json={"confirm": True}, headers=GOOD).json() == {"ok": True, "discarded": 0}
    assert not (tmp_path / "visit_revocations").exists()
