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

"""``/api/visit`` runtime endpoints: rooms / join / accept / route end / state / transcript / invite preview (§4.6)."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from urllib.parse import parse_qs

import httpx
import pytest
from fastapi import FastAPI

import config.visit_settings as visit_settings
from main_logic.visit import local_chars
from main_logic.visit.forget import ClearingSentinels
from main_logic.visit.limits import Blocklist
from main_logic.visit.spool import VisitSpool
from main_logic.visit.subjects import derive_pair_id, derive_peer_char_id
from main_routers.system_router import AUTOSTART_CSRF_TOKEN
from main_routers.visit_router import accounts, http, memory_routes, persona, transport_ws
from main_routers.visit_router import credentials as cr
from main_routers.visit_router import router as visit_router
from main_routers.visit_router import runtime as rtm
from main_routers.visit_router import transcript_upload as tu
from main_routers.visit_router.local_context import CharacterContext
from main_routers.visit_router.persona import PersonaGate
from tests.unit.visit_runtime_harness import (
    GUEST_CHAR_UID,
    HOST_CHAR_UID,
    HOST_UID,
    HOST_VID,
    INVITE,
    NOW,
    VISIT_ID,
    FakeClock,
    Wire,
    make_side,
    settle,
    teardown,
    through_gate,
)
from tests.unit.visit_servers_fake import BASE, FakeServers
from utils import visit_route_state

ORIGIN = "http://testserver"
UA = "Mozilla/5.0 NEKO-test"
GOOD = {"Origin": ORIGIN, "X-CSRF-Token": AUTOSTART_CSRF_TOKEN, "User-Agent": UA}
OWN = "a" * 24
OTHER_OWN = "e" * 24


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    for module in (rtm, transport_ws, visit_route_state, http, cr, tu):
        module._reset_for_tests()
    rtm.register_visit_route_kind()
    monkeypatch.setattr(visit_settings, "VISIT_ENABLED", True)
    monkeypatch.setattr(visit_settings, "NEKO_VISIT_ALLOW_NONLOCAL", False)
    monkeypatch.delenv("NEKO_BEHIND_PROXY", raising=False)
    yield
    for module in (rtm, transport_ws, visit_route_state, http, cr, tu):
        module._reset_for_tests()


class Env:
    """Both sides (harness runtimes, files under ``tmp_path``), a fake Servers and an ASGI client."""

    def __init__(self, tmp_path, monkeypatch) -> None:
        self.clock, self.wall = FakeClock(1000.0), FakeClock(NOW)
        self.host = make_side(tmp_path, "host", clock=self.clock, wall=self.wall)
        self.guest = make_side(tmp_path, "guest", clock=self.clock, wall=self.wall)
        self.servers = FakeServers()
        self.preview_mode = "ok"
        self.preview_visit_id = VISIT_ID
        self.preview_expires = NOW + 600
        self.preview_calls: list[str] = []
        self.account: str | None = "acct"     # 与夹具签发的凭证同一个社区账号
        self.own_uid: str | None = OWN
        self.gate = PersonaGate(ok=True, state="ok", text="一只活泼的猫娘。")
        self.gate_wait: asyncio.Event | None = None
        self.gate_entered = asyncio.Event()
        self.uids = {"Host": HOST_CHAR_UID, "Guest": GUEST_CHAR_UID}
        self.app = FastAPI()
        self.app.include_router(visit_router)
        self._patch(monkeypatch)
        self.use(self.host)

    def _patch(self, monkeypatch) -> None:
        env = self
        outbound = httpx.AsyncClient(transport=httpx.MockTransport(self._handler))
        monkeypatch.setattr(cr, "get_external_http_client", lambda: outbound)

        async def session():
            if env.account is None:
                raise cr.VisitLoginRequired()
            return cr._ServersSession(base_url=BASE, access_token="bearer-x", client_id="c1", account=env.account)

        async def gate(name):
            env.gate_entered.set()
            if env.gate_wait is not None:
                await env.gate_wait.wait()
            return PersonaGate(ok=env.gate.ok, state=env.gate.state, character_uid=env.uids.get(name),
                               text=env.gate.text)

        async def local_account():
            return env.account

        async def lookup_visit_uid(account):
            return env.own_uid if account is not None and account == env.account else None

        async def resolve_uid(name):
            return env.uids.get(name)

        async def context():
            return CharacterContext(family_names=("小明",), cards={"Host": "card", "Guest": "card"})

        real_start = rtm.start_visit

        async def start(name, side, **kwargs):
            s = env.host if side == "host" else env.guest
            rt = await real_start(name, side, host=s.host, deps=s.deps, clock=env.clock, wall=env.wall, **kwargs)
            s.rt = rt
            return rt

        monkeypatch.setattr(cr, "_servers_session", session)
        monkeypatch.setattr(persona, "persona_gate", gate)
        monkeypatch.setattr(rtm, "_local_account", local_account)
        monkeypatch.setattr(rtm, "start_visit", start)
        monkeypatch.setattr(accounts, "local_account", local_account)
        monkeypatch.setattr(accounts, "lookup_visit_uid", lookup_visit_uid)
        monkeypatch.setattr(local_chars, "resolve_char_uid", resolve_uid)
        monkeypatch.setattr(http, "load_character_context", context)
        monkeypatch.setattr(http, "prompt_lang", lambda: "zh")
        monkeypatch.setattr(tu, "is_live", lambda _v: False)

    def _handler(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.startswith("/api/visit/invites/") and path.endswith("/preview"):
            self.preview_calls.append(path.split("/")[-2])
            replies = {
                "404": (404, {"code": "invite_invalid"}), "410": (410, {"code": "invite_expired"}),
                "403": (403, {"code": "banned"}), "429": (429, {"code": "rate_limited", "retry_after_s": 9}),
                "401": (401, {"code": "unauthenticated"}),
            }
            if self.preview_mode == "503":
                return httpx.Response(503)
            if self.preview_mode in replies:
                status, body = replies[self.preview_mode]
                return httpx.Response(status, json=body)
            return httpx.Response(200, json={
                "visit_id": self.preview_visit_id, "host_visit_uid": HOST_UID,
                "host_short_code": HOST_UID[:6].upper(), "cross_region": False, "expires_at": self.preview_expires,
                "host_display_name": "Mimi",
            })
        return self.servers.handler(request)

    def use(self, side) -> None:
        """Endpoints read the config dir of the runtime deps: point them at ``side``'s."""
        rtm.set_runtime_deps(side.deps)

    def client(self, addr=("127.0.0.1", 50000)) -> httpx.AsyncClient:
        transport = httpx.ASGITransport(app=self.app, client=addr)
        return httpx.AsyncClient(transport=transport, base_url=ORIGIN)


@pytest.fixture
async def env(tmp_path, monkeypatch):
    e = Env(tmp_path, monkeypatch)
    yield e
    await teardown(e.host, e.guest, clock=e.clock)


async def _rooms(env, catgirl="Host", **over):
    async with env.client() as c:
        return await c.post("/api/visit/rooms", headers=GOOD, json={"catgirl": catgirl, **over})


async def _join(env, visit_id=VISIT_ID, **over):
    body = {"catgirl": "Guest", "invite_code": INVITE, "confirm": True, **over}
    env.use(env.guest)
    async with env.client() as c:
        return await c.post(f"/api/visit/rooms/{visit_id}/join", headers=GOOD, json=body)


def _no_slot(name="Host") -> bool:
    return visit_route_state.get_visit_route_state(name) is None and rtm.get_runtime(name) is None


# ── 总闸与本机来源 ─────────────────────────────────────────────────────

START_CALLS = [
    ("POST", "/api/visit/rooms", {"catgirl": "Host"}),
    ("POST", f"/api/visit/rooms/{VISIT_ID}/join", {"catgirl": "Guest", "invite_code": INVITE, "confirm": True}),
    ("POST", f"/api/visit/rooms/{VISIT_ID}/accept", {"catgirl": "Host", "accept": True}),
    ("GET", f"/api/visit/invites/{INVITE}/preview", None),
]
DATA_CALLS = [
    ("POST", "/api/visit/route/end", {"lanlan_name": "Host", "visit_id": VISIT_ID, "reason": "route_end"}),
    ("GET", "/api/visit/state?catgirl=Host", None),
    ("GET", f"/api/visit/transcript?visit_id={VISIT_ID}", None),
]


async def _call(client, method, path, body, headers=GOOD):
    if method == "GET":
        return await client.get(path, headers=headers)
    return await client.post(path, headers=headers, json=body)


async def test_release_switch_closes_start_and_join_but_not_ending_state_or_export(env, monkeypatch):
    monkeypatch.setattr(visit_settings, "VISIT_ENABLED", False)
    async with env.client() as c:
        for method, path, body in START_CALLS:
            resp = await _call(c, method, path, body)
            assert resp.status_code == 404 and resp.json() == {"detail": "Not Found"}, path
        # 回滚关掉总闸后，仍要能结束在飞的串门、看状态、导出转录（这里的 404 是端点自己的答复）
        for method, path, body in DATA_CALLS:
            assert (await _call(c, method, path, body)).json() != {"detail": "Not Found"}, path
        assert (await c.get("/api/visit/state?catgirl=Host", headers=GOOD)).status_code == 200
    assert env.preview_calls == [] and _no_slot()


@pytest.mark.parametrize("method,path,body", START_CALLS + DATA_CALLS)
async def test_every_endpoint_requires_the_local_gate(env, method, path, body):
    async with env.client() as c:
        no_token = {"Origin": ORIGIN, "User-Agent": UA}
        assert (await _call(c, method, path, body, headers=no_token)).status_code == 403
        forwarded = {**GOOD, "X-Forwarded-For": "127.0.0.1"}
        assert (await _call(c, method, path, body, headers=forwarded)).status_code == 403
    for addr in (("172.17.0.2", 5000), ("192.168.1.20", 5000)):
        async with env.client(addr) as c:
            resp = await _call(c, method, path, body)
            assert resp.status_code == 403 and resp.json()["code"] == "VISIT_E_UNAUTHORIZED"
    assert env.preview_calls == [] and _no_slot() and _no_slot("Guest")


# ── 建房 ───────────────────────────────────────────────────────────────


async def test_rooms_answers_202_with_only_the_visit_id(env):
    resp = await _rooms(env)
    assert resp.status_code == 202
    body = resp.json()
    assert set(body) == {"visit_id", "phase"} and body["phase"] == "pending"
    rt = rtm.get_runtime("Host")
    assert rt is not None and rt.visit_id == body["visit_id"] and rt.side == "host"
    await settle()
    assert env.host.host.frames_of("visit_state_change", "pending")
    assert env.host.creds_calls == []   # 能力门之前不去 Servers


async def test_rooms_rejects_bad_fields_before_reserving(env):
    assert (await _rooms(env, catgirl="")).status_code == 400
    assert (await _rooms(env, crop="giant")).json()["code"] == "crop_format"
    assert _no_slot()


async def test_cached_preflight_failure_refuses_synchronously(env, monkeypatch):
    class Session:
        lanlan_name, visit_id = "Nobody", VISIT_ID

    # 设置页 / 上一次建房的预检在同一页面环境里失败过：传输 WS 记下结论
    transport_ws._record_preflight(Session(), {"ua": UA, "reason": "no_webrtc"}, False)
    resp = await _rooms(env)
    assert resp.status_code == 409
    assert resp.json()["code"] == "VISIT_UNSUPPORTED_ON_THIS_MACHINE" and resp.json()["reason"] == "no_webrtc"
    assert _no_slot() and env.host.creds_calls == []
    join = await _join(env)
    assert join.status_code == 409 and join.json()["code"] == "VISIT_UNSUPPORTED_ON_THIS_MACHINE"
    assert env.preview_calls == [] and _no_slot("Guest")
    # 别的页面环境不受牵连
    async with env.client() as c:
        other = await c.post("/api/visit/rooms", headers={**GOOD, "User-Agent": "Other/1.0"},
                             json={"catgirl": "Host"})
    assert other.status_code == 202


async def test_preflight_cache_expires_and_a_pass_clears_it(env, monkeypatch):
    transport_ws.remember_preflight(UA, False, "foreign_websocket")
    monkeypatch.setattr(transport_ws, "VISIT_CAPS_CACHE_TTL_S", -1)
    assert (await _rooms(env)).status_code == 202
    await teardown(env.host, clock=env.clock)
    monkeypatch.setattr(transport_ws, "VISIT_CAPS_CACHE_TTL_S", 600)
    transport_ws.remember_preflight(UA, False, "foreign_websocket")
    transport_ws.remember_preflight(UA, True, None)
    assert transport_ws.cached_preflight_failure(UA) is None


async def _unsettled_spool(config_dir, visit_id="visitunsettled00000001", size=4096):
    spool_dir = config_dir / "visit_spool"
    spool_dir.mkdir(parents=True, exist_ok=True)
    (spool_dir / f"{visit_id}.jsonl").write_bytes(b"x" * size)


async def test_upload_backlog_counts_unsettled_spools_and_refuses(env, monkeypatch):
    monkeypatch.setattr(tu, "VISIT_UPLOAD_PENDING_CAP_BYTES", 4096)
    # 只有未结清的 spool（上传早已成功、memory_server 长期不可用）：也算进准入额度
    await _unsettled_spool(env.host.config_dir)
    resp = await _rooms(env)
    assert resp.status_code == 409 and resp.json()["code"] == "VISIT_UPLOAD_BACKLOG"
    assert _no_slot() and env.host.creds_calls == []
    await _unsettled_spool(env.guest.config_dir)
    join = await _join(env)
    assert join.status_code == 409 and join.json()["code"] == "VISIT_UPLOAD_BACKLOG"
    assert env.preview_calls == [] and _no_slot("Guest")


async def test_forget_in_progress_refuses_rooms_and_join_before_the_slot(env):
    await ClearingSentinels(env.host.config_dir).find_or_create(
        own_uid=OWN, scope="chars", own_char_uids=[HOST_CHAR_UID])
    resp = await _rooms(env)
    assert resp.status_code == 409 and resp.json()["code"] == "VISIT_FORGET_IN_PROGRESS"
    assert "reason" not in resp.json()
    assert _no_slot()
    await ClearingSentinels(env.guest.config_dir).find_or_create(
        own_uid=OWN, scope="person", own_char_uids=[GUEST_CHAR_UID], peer_uid=HOST_UID)
    join = await _join(env)
    assert join.status_code == 409 and join.json()["code"] == "VISIT_FORGET_IN_PROGRESS"
    assert _no_slot("Guest") and env.guest.creds_calls == []


async def test_forget_of_another_character_or_account_does_not_block(env):
    sentinels = ClearingSentinels(env.host.config_dir)
    await sentinels.find_or_create(own_uid=OWN, scope="chars", own_char_uids=[GUEST_CHAR_UID])
    await sentinels.find_or_create(own_uid=OTHER_OWN, scope="chars", own_char_uids=[HOST_CHAR_UID])
    assert (await _rooms(env)).status_code == 202


async def test_unknown_account_counts_every_accounts_forget(env):
    env.own_uid = None
    await ClearingSentinels(env.host.config_dir).find_or_create(
        own_uid=OTHER_OWN, scope="chars", own_char_uids=[HOST_CHAR_UID])
    assert (await _rooms(env)).json()["code"] == "VISIT_FORGET_IN_PROGRESS"


def _break_log(config_dir, name="0" * 32):
    logs = config_dir / "visit_revocations"
    logs.mkdir(parents=True, exist_ok=True)
    path = logs / f"{name}.json"
    path.write_text("{broken", encoding="utf-8")
    return path


async def test_unreadable_revocation_log_fails_closed(env):
    _break_log(env.host.config_dir)
    resp = await _rooms(env)
    assert resp.status_code == 409
    # 同一个码（旧前端照旧认得），另带 reason 说明是读不出的记录在挡，不泄露文件名 / uid
    assert resp.json() == {"ok": False, "code": "VISIT_FORGET_IN_PROGRESS", "reason": "forget_record_unreadable"}
    assert _no_slot()
    _break_log(env.guest.config_dir)
    join = await _join(env)
    assert join.status_code == 409
    assert join.json() == {"ok": False, "code": "VISIT_FORGET_IN_PROGRESS", "reason": "forget_record_unreadable"}
    assert _no_slot("Guest") and env.guest.creds_calls == []


async def test_unreadable_sentinel_without_scope_is_labelled_too(env):
    logs = env.host.config_dir / "visit_revocations"
    logs.mkdir(parents=True)
    (logs / f"clearing-{'0' * 32}.json").write_text("{torn", encoding="utf-8")
    assert (await _rooms(env)).json()["reason"] == "forget_record_unreadable"


async def test_real_open_log_is_the_plain_refusal_even_beside_a_damaged_one(env):
    from main_logic.visit.forget_runner import open_person_log
    from tests.unit.visit_memory_test_helpers import seed_roster

    await seed_roster(env.host.config_dir, own_uid=OWN, own_char="Host")
    await open_person_log(env.host.config_dir, own_uid=OWN, own_char="Host", own_char_uid=HOST_CHAR_UID,
                          peer_uid="1" * 24)
    resp = await _rooms(env)
    assert resp.status_code == 409 and resp.json() == {"ok": False, "code": "VISIT_FORGET_IN_PROGRESS"}
    # 真在清除时优先报真因：丢弃损坏记录帮不上忙，不引导用户去丢
    _break_log(env.host.config_dir)
    assert "reason" not in (await _rooms(env)).json()
    assert _no_slot()


async def test_discarding_unreadable_records_lets_admission_through(env):
    broken = _break_log(env.host.config_dir)
    assert (await _rooms(env)).json()["reason"] == "forget_record_unreadable"
    memory_routes.configure_memory_routes(config_dir=lambda: env.host.config_dir)
    try:
        async with env.client() as c:
            resp = await c.post("/api/visit/memory/forget/discard_unreadable", headers=GOOD,
                                json={"confirm": True})
    finally:
        memory_routes.configure_memory_routes(config_dir=memory_routes._default_config_dir)
    assert resp.json() == {"ok": True, "discarded": 1}
    assert not broken.exists()
    assert (await _rooms(env)).status_code == 202


async def test_rooms_waits_for_a_clearing_holding_the_admission_lock(env):
    held, release = asyncio.Event(), asyncio.Event()

    async def clearing():
        # 清除一侧：取同一把准入锁写哨兵（memory_routes 的 admission_lock 钩子就是它）
        async with http.char_admission_lock(HOST_CHAR_UID):
            held.set()
            await release.wait()
            await ClearingSentinels(env.host.config_dir).find_or_create(
                own_uid=OWN, scope="chars", own_char_uids=[HOST_CHAR_UID])

    task = asyncio.ensure_future(clearing())
    await held.wait()
    request = asyncio.ensure_future(_rooms(env))
    await settle(60)
    assert not request.done()
    release.set()
    await task
    resp = await request
    assert resp.json()["code"] == "VISIT_FORGET_IN_PROGRESS" and _no_slot()


async def test_admission_lock_is_held_until_the_runtime_is_registered(env):
    env.gate_wait = asyncio.Event()
    request = asyncio.ensure_future(_rooms(env))
    await asyncio.wait_for(env.gate_entered.wait(), 5)   # 建房已持锁、停在人设闸里

    async def clearing_sees_visit():
        async with http.char_admission_lock(HOST_CHAR_UID):
            return rtm.is_visit_route_active("Host")

    probe = asyncio.ensure_future(clearing_sees_visit())
    await settle(60)
    assert not probe.done()        # 建房还在人设闸里，清除拿不到锁
    env.gate_wait.set()
    assert (await request).status_code == 202
    assert await probe is True     # 清除拿到锁时，这场串门已登记为进行中


def test_forget_endpoints_share_the_admission_lock():
    # 导入包时把同一把锁交给清除端点（PR-09b 之前就生效：两边都在这个包里）
    import importlib

    importlib.reload(importlib.import_module("main_routers.visit_router"))
    try:
        assert memory_routes._hooks.admission_lock is http.char_admission_lock
    finally:
        memory_routes.configure_memory_routes(admission_lock=http.char_admission_lock)


async def test_runtime_refusals_pass_through_and_release_the_slot(env):
    env.gate = PersonaGate(ok=False, state="unreviewed")
    resp = await _rooms(env)
    assert resp.status_code == 409 and resp.json()["code"] == "VISIT_PERSONA_UNREVIEWED"
    assert resp.json()["state"] == "unreviewed" and _no_slot()
    env.gate = PersonaGate(ok=True, state="ok", text="猫娘")
    env.account = None
    resp = await _rooms(env)
    assert resp.status_code == 409 and resp.json()["code"] == "VISIT_LOGIN_REQUIRED" and _no_slot()
    env.account = "acct"
    env.host.host.precondition = "voice_session_active"
    resp = await _rooms(env)
    assert resp.status_code == 409 and resp.json()["reason"] == "voice_session_active" and _no_slot()


async def test_character_changed_during_admission_is_refused(env):
    real = local_chars.resolve_char_uid

    async def stale(name):
        return "9" * 32        # 查清除时认到的是另一个角色（期间改名 / 名字被占）

    local_chars.resolve_char_uid = stale
    try:
        resp = await _rooms(env)
    finally:
        local_chars.resolve_char_uid = real
    assert resp.status_code == 409 and resp.json()["reason"] == "busy"
    rt = env.host.rt
    assert rt is not None and rt.finalize_reason == "busy"


# ── 邀请预览与入房 ─────────────────────────────────────────────────────


async def _preview(env, code=INVITE):
    env.use(env.guest)
    async with env.client() as c:
        return await c.get(f"/api/visit/invites/{code}/preview", headers=GOOD)


async def test_preview_returns_the_dialog_fields_without_the_host_uid(env):
    resp = await _preview(env)
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"visit_id": VISIT_ID, "host_display_name": "Mimi", "host_short_code": HOST_UID[:6].upper(),
                    "cross_region": False, "expires_at": NOW + 600, "locally_blocked": False}
    assert HOST_UID not in resp.text
    # 只读：不占位、不建 iframe、不领凭证，之后同一邀请码照样能入房
    assert _no_slot("Guest") and env.guest.creds_calls == []
    assert (await _join(env)).status_code == 202


async def test_preview_marks_a_locally_blocked_host(env):
    await Blocklist(env.guest.config_dir).ablock(HOST_UID, display_name_at_block="Mimi")
    body = (await _preview(env)).json()
    assert body["locally_blocked"] is True and "host_visit_uid" not in body


@pytest.mark.parametrize("mode,status,code", [
    ("404", 404, "invite_invalid"), ("410", 410, "invite_expired"), ("403", 403, "VISIT_BANNED"),
    ("429", 429, "rate_limited"), ("401", 409, "VISIT_LOGIN_REQUIRED"), ("503", 503, "servers_unreachable"),
])
async def test_preview_maps_servers_errors(env, mode, status, code):
    env.preview_mode = mode
    resp = await _preview(env)
    assert resp.status_code == status and resp.json()["code"] == code
    if mode == "429":
        assert resp.json()["retry_after_s"] == 9
    assert _no_slot("Guest")


async def test_preview_rejects_a_malformed_code_before_any_network(env):
    resp = await _preview(env, code="abcdefghij")
    assert resp.status_code == 400 and resp.json()["code"] == "invite_code_format"
    assert env.preview_calls == []


async def test_join_answers_202_and_reuses_a_recent_preview(env):
    await _preview(env)
    resp = await _join(env)
    assert resp.status_code == 202
    assert resp.json() == {"ok": True, "visit_id": VISIT_ID, "phase": "pending"}
    assert env.preview_calls == [INVITE]      # 60 s 内的预览结果直接复用
    rt = rtm.get_runtime("Guest")
    assert rt is not None and rt.side == "guest" and rt.invite_code == INVITE


async def test_join_fetches_the_preview_when_none_is_recent(env, monkeypatch):
    await _preview(env)
    monkeypatch.setattr(http, "VISIT_INVITE_PREVIEW_REUSE_S", -1)
    assert (await _join(env)).status_code == 202
    assert env.preview_calls == [INVITE, INVITE]


async def test_join_refuses_a_blocked_host_before_the_slot_and_credentials(env):
    await Blocklist(env.guest.config_dir).ablock(HOST_UID, display_name_at_block="Mimi")
    resp = await _join(env)
    assert resp.status_code == 409
    assert resp.json()["code"] == "VISIT_INVITE_INVALID" and resp.json()["details"] == {"reason": "peer_blocked"}
    assert _no_slot("Guest") and env.guest.creds_calls == []


async def test_join_with_a_reused_preview_still_reads_the_current_blocklist(env):
    await _preview(env)
    await Blocklist(env.guest.config_dir).ablock(HOST_UID, display_name_at_block="Mimi")
    assert (await _join(env)).json()["details"] == {"reason": "peer_blocked"}


async def test_join_refuses_a_preview_of_another_visit(env):
    env.preview_visit_id = "ZzZzZzZzZzZzZzZzZzZzZz"
    resp = await _join(env)
    assert resp.status_code == 409 and resp.json()["details"] == {"reason": "invite_invalid"}
    assert _no_slot("Guest")


@pytest.mark.parametrize("mode,status,reason", [
    ("404", 409, "invite_invalid"), ("410", 409, "invite_expired"), ("503", 503, None), ("403", 403, None),
])
async def test_join_maps_preview_failures(env, mode, status, reason):
    env.preview_mode = mode
    resp = await _join(env)
    assert resp.status_code == status
    if reason:
        assert resp.json()["code"] == "VISIT_INVITE_INVALID" and resp.json()["details"] == {"reason": reason}
    assert _no_slot("Guest") and env.guest.creds_calls == []


@pytest.mark.parametrize("bad", ["..%2F..%2Fvisit_blocklist", "a.b.c.d.e.f.g.h.i.j.k.l", "short", "x" * 23])
async def test_join_rejects_a_malformed_visit_id_without_touching_disk(env, bad):
    before = sorted(p.name for p in env.guest.config_dir.rglob("*"))
    resp = await _join(env, visit_id=bad)
    assert resp.status_code in (400, 404)
    assert sorted(p.name for p in env.guest.config_dir.rglob("*")) == before
    assert env.preview_calls == [] and _no_slot("Guest")


async def test_join_format_gates(env):
    assert (await _join(env, invite_code="abc")).json()["code"] == "invite_code_format"
    assert (await _join(env, confirm=False)).json()["code"] == "confirm_required"
    body = {"catgirl": "Guest", "invite_code": INVITE}
    env.use(env.guest)
    async with env.client() as c:
        resp = await c.post(f"/api/visit/rooms/{VISIT_ID}/join", headers=GOOD, json=body)
    assert resp.status_code == 400 and resp.json()["code"] == "confirm_required"
    assert env.preview_calls == [] and _no_slot("Guest")


# ── 邀请码不进日志 ─────────────────────────────────────────────────────


async def test_invite_code_is_redacted_on_both_log_hops(env, caplog):
    from uvicorn.logging import AccessFormatter

    caplog.set_level(logging.INFO, logger="httpx")
    caplog.set_level(logging.INFO, logger="uvicorn.access")
    await _preview(env)   # 出站：httpx 的请求日志带着 Servers 预览 URL
    # 入站：uvicorn 访问日志的路径是第 3 个参数
    logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET",
                                             f"/api/visit/invites/{INVITE}/preview", "1.1", 200)
    hops = {r.name for r in caplog.records if "/invites/***/preview" in r.getMessage()}
    assert hops == {"httpx", "uvicorn.access"}
    assert INVITE not in caplog.text
    access = [r for r in caplog.records if r.name == "uvicorn.access"][-1]
    # uvicorn 的访问日志格式化器按位置拆参数：改写后参数个数不变、照常能格式化
    assert "/invites/***/preview" in AccessFormatter('%(request_line)s %(status_code)s').format(access)
    # 不带 /preview 的探测 / 打错的路径一样带着可兑换的邀请码：照样改写
    logging.getLogger("uvicorn.access").info('%s - "%s %s HTTP/%s" %d', "127.0.0.1:5000", "GET",
                                             f"/api/visit/invites/{INVITE}", "1.1", 404)
    assert any(r.getMessage().endswith('/invites/*** HTTP/1.1" 404') for r in caplog.records)
    assert INVITE not in caplog.text


# ── 接待 / 结束 ────────────────────────────────────────────────────────


async def test_accept_forwards_to_the_matching_host_runtime(env):
    calls = []

    class Stub:
        visit_id, creds, admitted_account = VISIT_ID, None, "acct"

        async def accept(self, accept):
            calls.append(accept)
            return 200, {"ok": True}

    rtm._runtimes["Host"] = Stub()
    try:
        async with env.client() as c:
            ok = await c.post(f"/api/visit/rooms/{VISIT_ID}/accept", headers=GOOD,
                              json={"catgirl": "Host", "accept": False})
            other = await c.post("/api/visit/rooms/ZzZzZzZzZzZzZzZzZzZzZz/accept", headers=GOOD,
                                 json={"catgirl": "Host", "accept": True})
            bad = await c.post(f"/api/visit/rooms/{VISIT_ID}/accept", headers=GOOD,
                               json={"catgirl": "Host", "accept": "yes"})
    finally:
        del rtm._runtimes["Host"]
    assert ok.status_code == 200 and calls == [False]
    assert other.status_code == 404 and other.json()["error"] == "no_pending_invite"
    assert bad.status_code == 400


async def test_accept_before_the_guest_arrived_is_404(env):
    visit_id = (await _rooms(env)).json()["visit_id"]
    async with env.client() as c:
        resp = await c.post(f"/api/visit/rooms/{visit_id}/accept", headers=GOOD,
                            json={"catgirl": "Host", "accept": True})
    assert resp.status_code == 404 and resp.json()["error"] == "no_pending_invite"


async def test_route_end_ends_a_waiting_visit_and_validates_its_body(env):
    visit_id = (await _rooms(env)).json()["visit_id"]
    async with env.client() as c:
        for body, code in (
            ({"lanlan_name": "Host", "visit_id": "../x", "reason": "recall"}, "visit_id_format"),
            ({"lanlan_name": "Host", "visit_id": visit_id, "reason": "later"}, "invalid_reason"),
            ({"visit_id": visit_id, "reason": "recall"}, "lanlan_name_required"),
        ):
            resp = await c.post("/api/visit/route/end", headers=GOOD, json=body)
            assert resp.status_code == 400 and resp.json()["code"] == code
        unknown = await c.post("/api/visit/route/end", headers=GOOD,
                               json={"lanlan_name": "Host", "visit_id": VISIT_ID, "reason": "recall"})
        assert unknown.status_code == 404
        resp = await c.post("/api/visit/route/end", headers=GOOD,
                            json={"lanlan_name": "Host", "visit_id": visit_id, "reason": "recall"})
    assert resp.status_code == 200 and resp.json()["mode"] == "finalize"
    assert env.host.rt.finalize_reason == "recall"


# ── 状态 ───────────────────────────────────────────────────────────────


def _keys(obj) -> set:
    if isinstance(obj, dict):
        return set(obj) | {k for v in obj.values() for k in _keys(v)}
    if isinstance(obj, list):
        return {k for v in obj for k in _keys(v)}
    return set()


async def test_state_never_carries_the_invite_code(env):
    await _rooms(env)
    rt = env.host.rt
    Wire().attach(rt, None, HOST_VID)
    await through_gate(rt)
    await rt.on_transport_state({"state": "joined", "peer_present": False})
    await settle()
    assert rt.phase == "invite_ready"
    async with env.client() as c:
        resp = await c.get("/api/visit/state?catgirl=Host", headers=GOOD)
    assert resp.status_code == 200 and resp.json()["phase"] == "invite_ready"
    assert "invite_code" not in _keys(resp.json()) and INVITE not in resp.text


async def test_state_of_an_idle_character_has_the_same_keys(env):
    async with env.client() as c:
        idle = (await c.get("/api/visit/state?catgirl=Host", headers=GOOD)).json()
        await c.post("/api/visit/rooms", headers=GOOD, json={"catgirl": "Host"})
        live = (await c.get("/api/visit/state?catgirl=Host", headers=GOOD)).json()
        bad = await c.get("/api/visit/state", headers=GOOD)
    assert idle["active"] is False and idle["phase"] is None
    assert set(idle) == set(live) and live["active"] is True and live["phase"] == "pending"
    assert bad.status_code == 400


# ── 转录导出 ───────────────────────────────────────────────────────────

LINES = [
    {"ln": "h:1", "lp": 1, "side": "host", "ts": 1001.0, "from": "own_cat", "text": "你好", "truncated": False},
    {"ln": "g:1", "lp": 1, "side": "guest", "ts": 1002.0, "from": "peer_cat", "text": "喵", "truncated": False},
    {"ln": "g:2", "lp": 2, "side": "guest", "ts": 1003.0, "from": "peer_human", "text": "hi", "truncated": True},
]


async def _write_spool(config_dir, visit_id=VISIT_ID, peer_uid=HOST_UID):
    pair = derive_pair_id(OWN, peer_uid)
    spool = VisitSpool(config_dir, visit_id)
    await spool.open({
        "v": 1, "visit_id": visit_id, "role": "host", "own_uid": OWN, "own_char": "Host",
        "own_char_uid": HOST_CHAR_UID, "pair_id": pair, "peer_uid": peer_uid,
        "peer_char_id": derive_peer_char_id(peer_uid, "f" * 32), "peer_char_tag": "f" * 32,
        "started_at": NOW, "lang": "zh-CN",
    }, now=0.0)
    for line in LINES:
        await spool.append(line)
    await spool.close()
    return spool


def _upload_doc(visit_id=VISIT_ID, role="host"):
    lines = [{k: v for k, v in line.items() if k != "ln"} for line in LINES]
    return {"v": 1, "own_visit_uid": OWN, "own_char_uid": HOST_CHAR_UID, "transport": "trtc", "request": {
        "visit_id": visit_id, "role": role, "started_at": NOW, "ended_at": NOW + 5, "finalized_reason": "wrap_up",
        "usage": {"duration_s": 5, "llm_input_tokens": 0, "llm_output_tokens": 0, "tts_requests": 0,
                  "tts_chars": 0},
        "lines": lines, "anomalies": 2, "app_version": "1.2"}}


def _write_sealed(config_dir, visit_id=VISIT_ID):
    path = config_dir / "visit_spool" / f"{visit_id}.upload.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_upload_doc(visit_id)), encoding="utf-8")
    return path


def _write_stream(config_dir, visit_id=VISIT_ID):
    path = config_dir / "visit_spool" / f"{visit_id}.upload.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [{"kind": "header", "visit_id": visit_id, "role": "guest", "own_visit_uid": OWN,
                "started_at": NOW, "own_char_uid": HOST_CHAR_UID, "app_version": "1.2", "transport": "trtc"}]
    records += [{"kind": "line", **{k: v for k, v in line.items() if k != "ln"}} for line in LINES]
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


async def _transcript(env, visit_id=VISIT_ID):
    async with env.client() as c:
        return await c.get(f"/api/visit/transcript?visit_id={visit_id}", headers=GOOD)


LOCAL_KEYS = {"visit_id", "peer_short_id", "peer_uid", "started_at", "transport", "lines", "anomalies", "source"}
LOCAL_LINE_KEYS = {"line_id", "lp", "side", "speaker_kind", "addressee", "ts", "text", "truncated", "i_done"}
CLOUD_LINE_KEYS = {"lp", "side", "from", "ts", "text", "truncated"}


async def test_transcript_reads_the_local_spool(env):
    await _write_spool(env.host.config_dir)
    resp = await _transcript(env)
    body = resp.json()
    assert resp.status_code == 200 and body["source"] == "spool"
    assert set(body) == LOCAL_KEYS and all(set(line) == LOCAL_LINE_KEYS for line in body["lines"])
    assert body["peer_uid"] == HOST_UID and body["peer_short_id"] == HOST_UID[:6].upper()
    assert [(line["line_id"], line["speaker_kind"], line["text"]) for line in body["lines"]] == [
        ("h:1", "cat", "你好"), ("g:1", "cat", "喵"), ("g:2", "human", "hi")]
    assert body["lines"][2]["truncated"] is True
    # 待传文件已结清：拿云端那份对了一遍（这里云端是空的，什么都没并进来）
    assert env.servers.count(f"/api/visit/details/{VISIT_ID}") == 1


async def test_transcript_after_forget_has_no_peer_identity(env):
    spool = await _write_spool(env.host.config_dir)
    await spool.delete_peer_fields()
    body = (await _transcript(env)).json()
    assert body["source"] == "spool" and body["peer_uid"] is None and body["peer_short_id"] is None
    assert len(body["lines"]) == 3      # 「清除这个人」只抹身份、不删正文


async def test_transcript_reads_memory_when_the_spool_is_gone(env, monkeypatch):
    class Peer:
        uid, short_id = HOST_UID, HOST_UID[:6].upper()

    class Creds:
        transport, visit_uid, account = "livekit", OWN, "acct"

    class Recent:
        visit_id, peer, creds, started_at_wall, ended_at_mono = VISIT_ID, Peer(), Creds(), NOW, 50.0

        def transcript_records(self):
            return [{k: v for k, v in line.items() if k != "ln"} for line in LINES]

        def visit_line_payload_from_record(self, record):
            return {"line_id": f"{record['side'][0]}:{record['lp']}", "addressee": {"side": "guest", "kind": "cat"},
                    "i_done": 2}

        def anomaly_count(self):
            return 3

        def wall(self):
            return NOW + 100

        def clock(self):
            return 60.0

    monkeypatch.setattr(rtm, "recent_runtime", lambda visit_id: Recent() if visit_id == VISIT_ID else None)
    _write_sealed(env.host.config_dir)   # 内存优先于待传文件
    body = (await _transcript(env)).json()
    assert body["source"] == "memory" and set(body) == LOCAL_KEYS | {"ended_at"}
    assert body["transport"] == "livekit" and body["anomalies"] == 3 and body["ended_at"] == NOW + 90
    assert body["lines"][0]["addressee"] == {"side": "guest", "kind": "cat"} and body["lines"][0]["i_done"] == 2


async def test_transcript_reads_the_pending_upload_before_the_cloud(env):
    _write_sealed(env.host.config_dir)
    body = (await _transcript(env)).json()
    assert body == {"source": "upload", "visit_id": VISIT_ID, "role": "host",
                    "lines": _upload_doc()["request"]["lines"]}
    assert env.servers.count(f"/api/visit/details/{VISIT_ID}") == 0


async def test_spool_and_pending_upload_are_read_once(env, monkeypatch):
    await _write_spool(env.host.config_dir)
    _write_sealed(env.host.config_dir)
    reads = []
    real = http.read_pending_upload_doc_sync
    monkeypatch.setattr(http, "read_pending_upload_doc_sync",
                        lambda *a: reads.append(a) or real(*a))
    body = (await _transcript(env)).json()
    # 崩溃场次的待传文件要从流水重建：一次请求只读一遍，spool 的 transport 也取自这一份
    assert body["source"] == "spool" and body["transport"] == "trtc" and len(reads) == 1


async def test_spool_anomalies_ignore_another_accounts_pending_upload(env):
    await _write_spool(env.host.config_dir)
    doc = _upload_doc()
    doc["own_visit_uid"] = OTHER_OWN      # 共用电脑上另一个账号那一侧的待传文件
    path = env.host.config_dir / "visit_spool" / f"{VISIT_ID}.upload.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    body = (await _transcript(env)).json()
    assert body["source"] == "spool" and body["anomalies"] == 0 and body["transport"] is None


async def test_transcript_rebuilds_a_crashed_upload_stream_without_writing(env):
    path = _write_stream(env.host.config_dir)
    before = path.read_bytes()
    body = (await _transcript(env)).json()
    assert body["source"] == "upload" and body["role"] == "guest" and len(body["lines"]) == 3
    assert path.read_bytes() == before
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]   # 只读：不封存、不删流水


def _details_rows(n, role="host"):
    rows = []
    for i in range(n):
        half = {"from": "own_cat", "ts": 1000.0 + i, "text": f"line {i}", "truncated": False}
        rows.append({"lp": i, "side": "host", role: half, ("guest" if role == "host" else "host"): None,
                     "status": "only_" + role})
    rows.reverse()       # 云端顺序不可信：返回前按 (lp, side_rank) 排
    return rows


def _cloud_rows_covering(extra: int = 2):
    """Cloud details rows (requester = host) holding every line of ``LINES`` plus ``extra`` more."""
    rows = [{"lp": line["lp"], "side": line["side"], "status": "agree", "guest": None,
             "host": {"from": line["from"], "ts": line["ts"], "text": line["text"], "truncated": line["truncated"]}}
            for line in LINES]
    rows += [{"lp": 10 + i, "side": "host", "status": "only_host", "guest": None,
              "host": {"from": "own_cat", "ts": 2000.0 + i, "text": f"extra {i}", "truncated": False}}
             for i in range(extra)]
    return rows


async def test_transcript_falls_back_to_every_cloud_page(env):
    env.servers.details_lines = _details_rows(1200)
    resp = await _transcript(env)
    body = resp.json()
    assert resp.status_code == 200 and body["source"] == "cloud" and body["role"] == "host"
    assert set(body) == {"source", "visit_id", "role", "lines"}
    assert all(set(line) == CLOUD_LINE_KEYS for line in body["lines"])
    assert [line["lp"] for line in body["lines"]] == list(range(1200))
    details = [r for r in env.servers.requests if r.url.path == f"/api/visit/details/{VISIT_ID}"]
    queries = [parse_qs(r.url.query.decode()) for r in details]
    assert len(details) == 3 and all(q["limit"] == ["500"] for q in queries)
    assert [q.get("cursor") for q in queries] == [None, ["p1"], ["p2"]]


async def test_cloud_transcript_takes_this_sides_half(env, monkeypatch):
    rows = [{"lp": 1, "side": "host", "host": {"from": "own_cat", "ts": 1.0, "text": "主", "truncated": False},
             "guest": {"from": "peer_cat", "ts": 1.5, "text": "客看到的", "truncated": False}, "status": "differ"},
            {"lp": 2, "side": "guest", "host": None,
             "guest": {"from": "own_cat", "ts": 2.0, "text": "客", "truncated": False}, "status": "only_guest"},
            {"lp": 3, "side": "guest", "host": None, "guest": {"from": "own_cat", "ts": "bad"}, "status": "x"}]
    real = env.servers._details

    def as_guest(request):
        resp = real(request)
        body = json.loads(resp.content)
        body["requester_role"] = "guest"
        return httpx.Response(resp.status_code, json=body)

    monkeypatch.setattr(env.servers, "_details", as_guest)
    env.servers.details_lines = rows
    body = (await _transcript(env)).json()
    assert body["role"] == "guest" and [line["text"] for line in body["lines"]] == ["客看到的", "客"]


async def test_cloud_transcript_past_the_page_cap_is_502_without_lines(env):
    # 满页一直翻到上限之后还有 next_cursor
    env.servers.details_lines = _details_rows(500 * (visit_settings.VISIT_DETAILS_MAX_PAGES + 1))
    resp = await _transcript(env)
    assert resp.status_code == 502 and resp.json()["code"] == "cloud_transcript_incomplete"
    assert "lines" not in resp.json()
    assert env.servers.count(f"/api/visit/details/{VISIT_ID}") == visit_settings.VISIT_DETAILS_MAX_PAGES


async def test_cloud_transcript_exactly_at_the_page_cap_is_complete(env):
    env.servers.details_lines = _details_rows(500 * visit_settings.VISIT_DETAILS_MAX_PAGES)
    body = (await _transcript(env)).json()
    assert body["source"] == "cloud" and len(body["lines"]) == 500 * visit_settings.VISIT_DETAILS_MAX_PAGES


@pytest.mark.parametrize("setup", ["page_fail", "offline", "not_participant"])
async def test_transcript_gone_everywhere_is_404(env, setup):
    env.servers.details_lines = _details_rows(1200)
    if setup == "page_fail":
        env.servers.details_fail_page = 1
    elif setup == "offline":
        env.account = None
    else:
        env.servers.details_mode = "403"
    resp = await _transcript(env)
    assert resp.status_code == 404 and resp.json()["code"] == "transcript_gone_local"


async def test_transcript_rejects_a_malformed_visit_id(env):
    resp = await _transcript(env, visit_id="..%2F..%2Fvisit_blocklist")
    assert resp.status_code == 400 and env.servers.requests == []


async def test_cloud_rows_with_a_broken_half_or_huge_timestamp_are_dropped_not_500(env, monkeypatch):
    from main_logic.visit import memory_bridge

    diags = []
    monkeypatch.setattr(memory_bridge, "diag", lambda event, **f: diags.append((event, f)))
    good = {"from": "own_cat", "ts": 1.0, "text": "好", "truncated": False}
    env.servers.details_lines = [
        {"lp": 1, "side": "host", "host": good, "guest": None, "status": "only_host"},
        {"lp": 2, "side": "host", "host": [], "guest": None, "status": "only_host"},
        {"lp": 3, "side": "host", "host": {**good, "ts": 10 ** 400}, "guest": None, "status": "only_host"},
        {"lp": 4, "side": "guest", "host": None, "guest": good, "status": "only_guest"},
    ]
    resp = await _transcript(env)
    assert resp.status_code == 200 and [line["lp"] for line in resp.json()["lines"]] == [1]
    assert ("cloud_transcript_rows_rejected", {"count": 2}) in diags


async def test_cloud_cursor_that_does_not_advance_is_a_failure(env, monkeypatch):
    full_page = _details_rows(500)

    def stuck(request):
        return httpx.Response(200, json={"visit_id": VISIT_ID, "lines": full_page, "requester_role": "host",
                                         "next_cursor": "same"})

    monkeypatch.setattr(env.servers, "_details", stuck)
    resp = await _transcript(env)
    assert resp.status_code == 404 and resp.json()["code"] == "transcript_gone_local"
    assert env.servers.count(f"/api/visit/details/{VISIT_ID}") == 2


async def test_account_switch_during_admission_is_refused(env, monkeypatch):
    seen = iter(["u1", "u2"])   # 占位前一次、登记后一次

    async def switching():
        return next(seen, "u2")

    monkeypatch.setattr(accounts, "local_account", switching)
    resp = await _rooms(env)
    # 清除检查按 u1 查的，这一场却已是 u2 的：作废
    assert resp.status_code == 409 and resp.json()["reason"] == "busy"
    assert env.host.rt.finalize_reason == "busy"


async def test_a_preview_past_its_invite_deadline_is_not_reused(env):
    env.preview_expires = time.time() - 1
    await _preview(env)
    env.preview_mode = "410"
    resp = await _join(env)
    assert env.preview_calls == [INVITE, INVITE]
    assert resp.status_code == 409 and resp.json()["details"] == {"reason": "invite_expired"}
    assert _no_slot("Guest")


async def test_local_copies_of_another_account_are_not_served(env, monkeypatch):
    await _write_spool(env.host.config_dir)
    _write_sealed(env.host.config_dir)
    env.own_uid = OTHER_OWN
    env.servers.details_lines = _details_rows(3)
    body = (await _transcript(env)).json()
    # 本机三份都属于另一个社区账号：只走云端（Servers 按参与者判定）
    assert body["source"] == "cloud"
    env.account = None
    assert (await _transcript(env)).json()["code"] == "transcript_gone_local"


async def test_memory_transcript_after_forget_has_no_peer_identity(env, monkeypatch):
    from main_logic.visit.spool import new_state

    class Peer:
        uid, short_id = HOST_UID, HOST_UID[:6].upper()

    class Creds:
        transport, visit_uid, account = "trtc", OWN, "acct"

    class Recent:
        visit_id, peer, creds, started_at_wall, ended_at_mono = VISIT_ID, Peer(), Creds(), NOW, None

        def transcript_records(self):
            return []

        def anomaly_count(self):
            return 0

    monkeypatch.setattr(rtm, "recent_runtime", lambda visit_id: Recent())
    state = new_state(own_uid=OWN, own_char="Host", own_char_uid=HOST_CHAR_UID,
                      pair_id=derive_pair_id(OWN, HOST_UID), peer_uid=HOST_UID,
                      peer_char_id=derive_peer_char_id(HOST_UID, "f" * 32), memory_enabled=False)
    spool = VisitSpool(env.host.config_dir, VISIT_ID)
    await spool.write_state(state)
    assert (await _transcript(env)).json()["peer_uid"] == HOST_UID
    # 「清除这个人」抹掉了 state.json 的对端字段
    await spool.write_state({**state, "peer_uid": None, "pair_id": None, "peer_char_id": None})
    body = (await _transcript(env)).json()
    assert body["source"] == "memory" and body["peer_uid"] is None and body["peer_short_id"] is None
    assert HOST_UID not in json.dumps(body)
    # state.json 已被回收（激活时一定写过）：同样不给身份
    spool.state_path.unlink()
    body = (await _transcript(env)).json()
    assert body["source"] == "memory" and body["peer_uid"] is None and len(body["lines"]) == 0


async def test_a_spool_that_lost_lines_gives_way_to_a_complete_source(env):
    spool = await _write_spool(env.host.config_dir)
    with open(spool.jsonl_path, "ab") as handle:
        handle.write(b'{"lp": 9, "side": "host", "ts"')      # 崩溃留下的半行
    _write_sealed(env.host.config_dir)
    env.servers.details_lines = _cloud_rows_covering()
    # 待传文件补不回这半行：合并后仍不完整，先找云端；云端多出的行并进来，但证明不了丢的那条回来了
    body = (await _transcript(env)).json()
    assert body["source"] == "spool" and len(body["lines"]) == 5 and body["dropped_lines"] == 1
    (env.host.config_dir / "visit_spool" / f"{VISIT_ID}.upload.json").unlink()
    env.servers.details_mode = "503"   # 云端也取不到：退回这份不完整的，并如实标出丢了几行
    body = (await _transcript(env)).json()
    assert body["source"] == "spool" and body["dropped_lines"] == 1 and len(body["lines"]) == 3


async def test_memory_copy_of_another_account_is_not_served(env, monkeypatch):
    class Creds:
        transport, visit_uid, account = "trtc", OTHER_OWN, "someone-else"

    class Recent:
        visit_id, peer, creds, started_at_wall, ended_at_mono = VISIT_ID, None, Creds(), NOW, None

        def transcript_records(self):
            return [{k: v for k, v in line.items() if k != "ln"} for line in LINES]

        def visit_line_payload_from_record(self, record):
            return {}

        def anomaly_count(self):
            return 0

    monkeypatch.setattr(rtm, "recent_runtime", lambda visit_id: Recent())
    env.servers.details_mode = "403"     # 云端按参与者判定：这一场不是当前账号的
    resp = await _transcript(env)
    assert resp.status_code == 404 and resp.json()["code"] == "transcript_gone_local"


async def test_state_of_another_accounts_visit_only_says_busy(env):
    await _rooms(env)
    async with env.client() as c:
        mine = (await c.get("/api/visit/state?catgirl=Host", headers=GOOD)).json()
        env.account = "someone-else"     # 共用电脑上换了社区账号
        other = (await c.get("/api/visit/state?catgirl=Host", headers=GOOD)).json()
    assert mine["visit_id"] is not None
    assert other == {**http.IDLE_STATE, "active": True, "phase": "pending"}


async def test_an_upload_stream_that_lost_records_gives_way_to_the_cloud(env):
    path = _write_stream(env.host.config_dir)
    with open(path, "ab") as handle:
        handle.write(b'{"kind": "line", "lp": 9')       # 崩溃留下的半行
    env.servers.details_lines = _cloud_rows_covering()
    body = (await _transcript(env)).json()
    assert body["source"] == "upload" and len(body["lines"]) == 5 and body["dropped_lines"] == 1
    env.servers.details_mode = "503"
    body = (await _transcript(env)).json()
    assert body["source"] == "upload" and body["dropped_lines"] == 1 and len(body["lines"]) == 3


async def test_cloud_text_that_cannot_be_utf8_is_a_dropped_row_not_500(env, monkeypatch):
    good = {"from": "own_cat", "ts": 1.0, "text": "好", "truncated": False}
    env.servers.details_lines = [
        {"lp": 1, "side": "host", "host": good, "guest": None, "status": "only_host"},
        {"lp": 2, "side": "host", "host": {**good, "text": "a@@SURROGATE@@"}, "guest": None, "status": "only_host"},
    ]
    real = env.servers._details

    def with_lone_surrogate(request):
        # Servers 在 JSON 里转义出一个孤立代理字符（解析得出字符串，却编不成 UTF-8）
        resp = real(request)
        escaped = bytes((92,)) + b"ud800"
        return httpx.Response(resp.status_code, content=resp.content.replace(b"@@SURROGATE@@", escaped),
                              headers={"content-type": "application/json"})

    monkeypatch.setattr(env.servers, "_details", with_lone_surrogate)
    resp = await _transcript(env)
    assert resp.status_code == 200 and [line["lp"] for line in resp.json()["lines"]] == [1]


async def test_admission_compares_the_account_the_runtime_recorded(env, monkeypatch):
    async def runtime_saw(*_a):
        return "someone-else"     # start_visit 读本机账号那一刻是别的账号（中途换走又换回）

    monkeypatch.setattr(rtm, "_local_account", runtime_saw)
    resp = await _rooms(env)
    assert resp.status_code == 409 and resp.json()["reason"] == "busy"
    assert env.host.rt.finalize_reason == "busy"


async def test_a_live_runtime_wins_over_a_spool_that_missed_a_line(env, monkeypatch):
    spool = VisitSpool(env.host.config_dir, VISIT_ID)
    await _write_spool(env.host.config_dir)

    class Creds:
        transport, visit_uid, account = "trtc", OWN, "acct"

    class Live:
        visit_id, peer, creds, started_at_wall, ended_at_mono = VISIT_ID, None, Creds(), NOW, None

        def transcript_records(self):
            extra = {"lp": 3, "side": "host", "ts": 1004.0, "from": "own_cat", "text": "spool 没写进去的那句",
                     "truncated": False}
            return [{k: v for k, v in line.items() if k != "ln"} for line in LINES] + [extra]

        def visit_line_payload_from_record(self, record):
            return {}

        def anomaly_count(self):
            return 0

    monkeypatch.setattr(rtm, "get_runtime_by_visit", lambda visit_id: Live())
    body = (await _transcript(env)).json()
    assert spool.jsonl_path.exists()
    assert body["source"] == "memory" and len(body["lines"]) == 4


async def test_a_legacy_upload_without_owner_is_attributed_from_its_state(env):
    from main_logic.visit.spool import new_state

    path = env.host.config_dir / "visit_spool" / f"{VISIT_ID}.upload.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({**_upload_doc(), "own_visit_uid": None}), encoding="utf-8")
    env.servers.details_mode = "503"
    assert (await _transcript(env)).json()["code"] == "transcript_gone_local"
    await VisitSpool(env.host.config_dir, VISIT_ID).write_state(new_state(
        own_uid=OWN, own_char="Host", own_char_uid=HOST_CHAR_UID, pair_id=derive_pair_id(OWN, HOST_UID),
        peer_uid=HOST_UID, peer_char_id=derive_peer_char_id(HOST_UID, "f" * 32), memory_enabled=False))
    body = (await _transcript(env)).json()
    assert body["source"] == "upload" and len(body["lines"]) == 3


async def test_backlog_entry_that_cannot_be_statted_refuses(env, monkeypatch):
    from main_logic.visit import spool as spool_mod

    class Locked:
        name = f"{VISIT_ID}.upload.json"

        def stat(self):
            raise PermissionError("sharing violation")

    monkeypatch.setattr(spool_mod, "_list_names", lambda _d: [(VISIT_ID, ".upload.json", Locked())])
    resp = await _rooms(env)
    assert resp.status_code == 409 and resp.json()["code"] == "VISIT_UPLOAD_BACKLOG" and _no_slot()


async def test_another_account_cannot_accept_or_recall_but_can_end(env):
    visit_id = (await _rooms(env)).json()["visit_id"]
    rt = env.host.rt
    env.account = "someone-else"         # 共用电脑上换了社区账号
    async with env.client() as c:
        accept = await c.post(f"/api/visit/rooms/{visit_id}/accept", headers=GOOD,
                              json={"catgirl": "Host", "accept": False})
        recall = await c.post("/api/visit/route/end", headers=GOOD,
                              json={"lanlan_name": "Host", "visit_id": visit_id, "reason": "recall"})
        assert accept.status_code == 404 and recall.status_code == 404
        assert rt.finalize_reason is None and rt.accepted is None
        # 硬结束照样可以：换了账号的人也要能把角色腾出来
        end = await c.post("/api/visit/route/end", headers=GOOD,
                           json={"lanlan_name": "Host", "visit_id": visit_id, "reason": "route_end"})
    assert end.status_code == 200 and rt.finalize_reason == "route_end"


async def test_cloud_rows_with_an_unknown_speaker_are_dropped(env):
    good = {"from": "own_cat", "ts": 1.0, "text": "好", "truncated": False}
    env.servers.details_lines = [
        {"lp": 1, "side": "host", "host": good, "guest": None, "status": "only_host"},
        {"lp": 2, "side": "host", "host": {**good, "from": "narrator"}, "guest": None, "status": "only_host"},
    ]
    body = (await _transcript(env)).json()
    assert [line["lp"] for line in body["lines"]] == [1]


async def test_another_account_cannot_answer_a_waiting_guest(env):
    calls = []

    class Awaiting:
        visit_id, creds, admitted_account = VISIT_ID, None, "acct"

        async def accept(self, accept):
            calls.append(accept)
            return 200, {"ok": True}

    rtm._runtimes["Host"] = Awaiting()
    env.account = "someone-else"
    try:
        async with env.client() as c:
            resp = await c.post(f"/api/visit/rooms/{VISIT_ID}/accept", headers=GOOD,
                                json={"catgirl": "Host", "accept": True})
    finally:
        del rtm._runtimes["Host"]
    assert resp.status_code == 404 and calls == []


async def test_a_spool_that_silently_missed_a_line_gives_way_to_the_pending_upload(env):
    spool = VisitSpool(env.host.config_dir, VISIT_ID)
    await spool.open({
        "v": 1, "visit_id": VISIT_ID, "role": "host", "own_uid": OWN, "own_char": "Host",
        "own_char_uid": HOST_CHAR_UID, "pair_id": derive_pair_id(OWN, HOST_UID), "peer_uid": HOST_UID,
        "peer_char_id": derive_peer_char_id(HOST_UID, "f" * 32), "peer_char_tag": "f" * 32,
        "started_at": NOW, "lang": "zh-CN",
    }, now=0.0)
    for line in LINES[:2]:          # 第三行写 spool 失败（只记了日志），上传流水里有
        await spool.append(line)
    await spool.close()
    _write_sealed(env.host.config_dir)
    body = (await _transcript(env)).json()
    # 两份本机副本取并集：spool 漏的那句从上传流水补回来
    assert body["source"] == "spool" and len(body["lines"]) == 3 and "dropped_lines" not in body
    assert body["lines"][2]["text"] == "hi"


async def test_spool_and_upload_each_missing_a_different_line_are_merged(env):
    spool = VisitSpool(env.host.config_dir, VISIT_ID)
    await spool.open({
        "v": 1, "visit_id": VISIT_ID, "role": "host", "own_uid": OWN, "own_char": "Host",
        "own_char_uid": HOST_CHAR_UID, "pair_id": derive_pair_id(OWN, HOST_UID), "peer_uid": HOST_UID,
        "peer_char_id": derive_peer_char_id(HOST_UID, "f" * 32), "peer_char_tag": "f" * 32,
        "started_at": NOW, "lang": "zh-CN",
    }, now=0.0)
    for line in (LINES[0], LINES[2]):           # spool 漏了第 2 句
        await spool.append(line)
    await spool.close()
    doc = _upload_doc()
    doc["request"]["lines"] = doc["request"]["lines"][:2]      # 上传流水漏了第 3 句
    path = env.host.config_dir / "visit_spool" / f"{VISIT_ID}.upload.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    body = (await _transcript(env)).json()
    assert [line["text"] for line in body["lines"]] == ["你好", "喵", "hi"] and "dropped_lines" not in body


async def test_a_recovery_sealed_upload_keeps_its_drop_count(env):
    from main_logic.visit import recovery

    path = _write_stream(env.host.config_dir)
    with open(path, "ab") as handle:
        handle.write(b'{"kind": "line", "lp": 9')       # 崩溃留下的半行
    # 启动补录把流水封成 .upload.json、删掉流水
    doc = recovery._seal_stream_sync(env.host.config_dir / "visit_spool", VISIT_ID, None)
    assert doc["dropped_records"] == 1 and not path.exists()
    env.servers.details_lines = _cloud_rows_covering()
    body = (await _transcript(env)).json()           # 不完整：先找云端，多出的行并进来、丢行提示照留
    assert body["source"] == "upload" and len(body["lines"]) == 5 and body["dropped_lines"] == 1
    env.servers.details_mode = "503"
    body = (await _transcript(env)).json()
    assert body["source"] == "upload" and body["dropped_lines"] == 1


async def test_a_cloud_copy_with_fewer_lines_does_not_replace_a_partial_one(env):
    spool = await _write_spool(env.host.config_dir)
    with open(spool.jsonl_path, "ab") as handle:
        handle.write(b'{"lp": 9, "side": "host", "ts"')      # 崩溃留下的半行
    # 本侧转录还没传上去：云端只有另一行，不是本机副本的超集
    env.servers.details_lines = _details_rows(1)
    body = (await _transcript(env)).json()
    # 不拿它顶掉本机还剩的行：并进来，丢行数照报
    assert body["source"] == "spool" and body["dropped_lines"] == 1 and len(body["lines"]) == 4
    assert [line["text"] for line in body["lines"]][:2] == ["line 0", "你好"]


async def test_cloud_rows_past_the_wire_bounds_are_dropped(env):
    good = {"from": "own_cat", "ts": 1.0, "text": "好", "truncated": False}
    env.servers.details_lines = [
        {"lp": 1, "side": "host", "host": good, "guest": None, "status": "only_host"},
        {"lp": visit_settings.VISIT_LP_MAX + 1, "side": "host", "host": good, "guest": None, "status": "x"},
        {"lp": 2, "side": "host", "host": {**good, "text": "长" * visit_settings.VISIT_TEXT_MAX_BYTES},
         "guest": None, "status": "only_host"},
    ]
    assert [line["lp"] for line in (await _transcript(env)).json()["lines"]] == [1]


async def test_a_preview_is_reused_only_by_the_account_that_fetched_it(env):
    await _preview(env)
    env.account = "someone-else"      # 共用电脑上换了账号：要用自己的账号再问一次 Servers
    env.preview_mode = "403"
    resp = await _join(env)
    assert env.preview_calls == [INVITE, INVITE]
    assert resp.status_code == 403 and resp.json()["code"] == "VISIT_BANNED" and _no_slot("Guest")


async def test_a_preview_fetched_across_an_account_switch_is_not_cached(env, monkeypatch):
    epochs = iter([7, 9])     # 请求途中有过一次登出 / 换账号（A→B→A 也一样会让代数变）
    monkeypatch.setattr(rtm, "account_epoch", lambda: next(epochs, 9))
    await http._fetch_preview(INVITE, "acct")
    assert http._previews == {}
    # 期间没有账号变更：照常缓存
    monkeypatch.setattr(rtm, "account_epoch", lambda: 9)
    await http._fetch_preview(INVITE, "acct")
    assert list(http._previews) == [("acct", INVITE)]
    # 缓存之后发生账号变更：不再复用
    monkeypatch.setattr(rtm, "account_epoch", lambda: 10)
    assert http._recent_preview("acct", INVITE) is None


async def test_join_refuses_when_the_account_changes_after_the_preview(env, monkeypatch):
    seen = iter(["acct", "someone-else"])   # 查预览时一个账号、准入时另一个

    async def switching():
        return next(seen, "someone-else")

    monkeypatch.setattr(accounts, "local_account", switching)
    resp = await _join(env)
    assert resp.status_code == 409 and resp.json()["reason"] == "busy"
    assert _no_slot("Guest") and env.guest.creds_calls == []


async def test_join_refuses_a_freshly_fetched_preview_that_already_expired(env):
    env.preview_expires = time.time() - 1
    resp = await _join(env)
    assert resp.status_code == 409 and resp.json()["details"] == {"reason": "invite_expired"}
    assert _no_slot("Guest") and env.guest.creds_calls == []


async def test_cloud_cursor_cycles_are_rejected_at_once(env, monkeypatch):
    calls = []
    full_page = _details_rows(500)

    def cycling(request):
        cursor = parse_qs(request.url.query.decode()).get("cursor", [""])[0]
        calls.append(cursor)
        nxt = {"": "p1", "p1": "p2", "p2": "p1"}[cursor]
        return httpx.Response(200, json={"visit_id": VISIT_ID, "lines": full_page, "requester_role": "host",
                                         "next_cursor": nxt})

    monkeypatch.setattr(env.servers, "_details", cycling)
    resp = await _transcript(env)
    assert resp.status_code == 404 and calls == ["", "p1", "p2"]


async def test_an_owned_runtime_is_served_while_the_account_map_is_unwritten(env, monkeypatch):
    env.own_uid = None        # 领到凭证了、账号映射还没写成（后台补写中）

    class Creds:
        transport, visit_uid, account = "trtc", OWN, "acct"

    class Live:
        visit_id, peer, creds, started_at_wall, ended_at_mono = VISIT_ID, None, Creds(), NOW, None

        def transcript_records(self):
            return [{k: v for k, v in line.items() if k != "ln"} for line in LINES]

        def visit_line_payload_from_record(self, record):
            return {}

        def anomaly_count(self):
            return 0

    monkeypatch.setattr(rtm, "get_runtime_by_visit", lambda visit_id: Live())
    body = (await _transcript(env)).json()
    assert body["source"] == "memory" and len(body["lines"]) == 3


async def test_a_shorter_upload_does_not_replace_a_longer_partial_spool(env):
    spool = await _write_spool(env.host.config_dir)
    with open(spool.jsonl_path, "ab") as handle:
        handle.write(b'{"lp": 9, "side": "host", "ts"')       # spool 丢了一行半行
    doc = _upload_doc()
    doc["request"]["lines"] = doc["request"]["lines"][:1]      # 上传流水静默少了两行
    path = env.host.config_dir / "visit_spool" / f"{VISIT_ID}.upload.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    env.servers.details_mode = "503"
    body = (await _transcript(env)).json()
    assert body["source"] == "spool" and body["dropped_lines"] == 1 and len(body["lines"]) == 3


def _epochs(monkeypatch, *values):
    seq = iter(values)
    last = values[-1]
    monkeypatch.setattr(rtm, "account_epoch", lambda: next(seq, last))


async def test_join_refuses_a_preview_fetched_across_an_account_change(env, monkeypatch):
    _epochs(monkeypatch, 3, 4)            # 取预览前后代数不同（A→B→A 也一样）
    resp = await _join(env)
    assert resp.status_code == 409 and resp.json()["reason"] == "busy"
    assert _no_slot("Guest") and env.guest.creds_calls == []


async def test_join_refuses_an_account_change_between_preview_and_admission(env, monkeypatch):
    _epochs(monkeypatch, 3, 3, 4)         # 预览按代数 3 取到，准入时已是 4
    resp = await _join(env)
    assert resp.status_code == 409 and resp.json()["reason"] == "busy" and _no_slot("Guest")


async def test_rooms_refuse_while_an_account_change_is_in_progress(env, monkeypatch):
    monkeypatch.setattr(rtm, "account_epoch", lambda: None)
    resp = await _rooms(env)
    assert resp.status_code == 409 and resp.json()["reason"] == "busy" and _no_slot()


async def test_local_transcript_read_across_an_account_change_falls_back_to_the_cloud(env, monkeypatch):
    await _write_spool(env.host.config_dir)
    env.servers.details_lines = _details_rows(2)
    _epochs(monkeypatch, 5, 6)            # 读本机副本的过程中有过登出 / 换账号
    body = (await _transcript(env)).json()
    assert body["source"] == "cloud"


async def test_dropped_spool_records_are_not_offset_by_merged_rows(env):
    spool = await _write_spool(env.host.config_dir)
    with open(spool.jsonl_path, "ab") as handle:
        handle.write(b'{"lp": 9, "side": "host", "ts"')      # spool 坏了一条（认不出是哪一行）
    doc = _upload_doc()
    doc["request"]["lines"] = doc["request"]["lines"] + [
        {"lp": 3, "side": "host", "from": "own_cat", "ts": 1005.0, "text": "spool 漏写的另一句", "truncated": False}]
    path = env.host.config_dir / "visit_spool" / f"{VISIT_ID}.upload.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    env.servers.details_mode = "503"
    body = (await _transcript(env)).json()
    # 补进来一行不代表坏掉的那条找回来了：仍报不完整
    assert len(body["lines"]) == 4 and body["dropped_lines"] == 1


async def test_cloud_rows_missing_a_local_row_are_merged_not_swapped(env):
    spool = await _write_spool(env.host.config_dir)
    with open(spool.jsonl_path, "ab") as handle:
        handle.write(b'{"lp": 9, "side": "host", "ts"')
    rows = _cloud_rows_covering(extra=3)
    del rows[1]                       # 云端恰好漏了本机有的那一行（独立写入）
    env.servers.details_lines = rows
    body = (await _transcript(env)).json()
    assert body["source"] == "spool" and len(body["lines"]) == 6 and body["dropped_lines"] == 1


async def test_an_account_change_during_the_cloud_fetch_returns_nothing(env, monkeypatch):
    env.servers.details_lines = _cloud_rows_covering()
    _epochs(monkeypatch, 5, 5, 5, 6)      # 本机读取前后不变，云端请求途中变了
    resp = await _transcript(env)
    assert resp.status_code == 409 and resp.json()["reason"] == "account_change" and "lines" not in resp.json()


async def test_a_cloud_copy_with_exactly_the_local_rows_keeps_the_drop_notice(env):
    spool = await _write_spool(env.host.config_dir)
    with open(spool.jsonl_path, "ab") as handle:
        handle.write(b'{"lp": 9, "side": "host", "ts"')      # 两边都丢了最后一条
    env.servers.details_lines = _cloud_rows_covering(extra=0)
    body = (await _transcript(env)).json()
    assert body["source"] == "spool" and body["dropped_lines"] == 1 and len(body["lines"]) == 3


async def test_a_settled_spool_that_silently_missed_a_line_is_completed_from_the_cloud(env):
    spool = VisitSpool(env.host.config_dir, VISIT_ID)
    await spool.open({
        "v": 1, "visit_id": VISIT_ID, "role": "host", "own_uid": OWN, "own_char": "Host",
        "own_char_uid": HOST_CHAR_UID, "pair_id": derive_pair_id(OWN, HOST_UID), "peer_uid": HOST_UID,
        "peer_char_id": derive_peer_char_id(HOST_UID, "f" * 32), "peer_char_tag": "f" * 32,
        "started_at": NOW, "lang": "zh-CN",
    }, now=0.0)
    for line in LINES[:2]:          # 第 3 句写 spool 失败（只记日志），上传早已结清、待传文件已删
        await spool.append(line)
    await spool.close()
    env.servers.details_lines = _cloud_rows_covering(extra=0)
    body = (await _transcript(env)).json()
    assert [line["text"] for line in body["lines"]] == ["你好", "喵", "hi"]
    # 缺的那一行从云端并进来，整份仍是本机形态：对端身份、line_id 与同一场之前的导出一致
    assert body["source"] == "spool" and set(body) == LOCAL_KEYS and body["peer_uid"] == HOST_UID
    assert all(set(line) == LOCAL_LINE_KEYS for line in body["lines"])
    assert [line["line_id"] for line in body["lines"]][:2] == ["h:1", "g:1"]
    env.servers.details_mode = "503"       # 云端取不到：只给 spool
    body = (await _transcript(env)).json()
    assert body["source"] == "spool" and len(body["lines"]) == 2


async def test_preview_fetched_across_an_account_change_is_not_returned(env, monkeypatch):
    _epochs(monkeypatch, 3, 3, 4)          # 取预览时代数没变，读黑名单的时候变了
    resp = await _preview(env)
    assert resp.status_code == 409 and resp.json()["reason"] == "account_change"
    assert "host_display_name" not in resp.text
    _epochs(monkeypatch, 3, 4)             # 取预览途中就变了
    assert (await _preview(env)).status_code == 409


async def test_state_read_across_an_account_change_only_says_busy(env, monkeypatch):
    await _rooms(env)
    _epochs(monkeypatch, 7, 8)       # 读属主的过程中有过登出 / 换账号
    async with env.client() as c:
        body = (await c.get("/api/visit/state?catgirl=Host", headers=GOOD)).json()
    assert body == {**http.IDLE_STATE, "active": True, "phase": "pending"}


async def test_preview_that_expired_on_the_way_is_410(env):
    env.preview_expires = time.time() - 1
    resp = await _preview(env)
    assert resp.status_code == 410 and resp.json()["code"] == "invite_expired"
    assert "host_display_name" not in resp.text


async def test_join_refuses_an_invite_that_expires_while_reading_the_blocklist(env, monkeypatch):
    clock = {"now": NOW - 100.0}
    real_monotonic = time.monotonic

    class FakeTime:
        @staticmethod
        def time():
            return clock["now"]

        monotonic = staticmethod(real_monotonic)

    monkeypatch.setattr(http, "time", FakeTime)
    env.preview_expires = NOW - 50.0
    real = Blocklist.aload

    async def slow(config_dir):
        clock["now"] = NOW              # 读黑名单期间邀请到期
        return await real(config_dir)

    monkeypatch.setattr(Blocklist, "aload", slow)
    resp = await _join(env)
    assert resp.status_code == 409 and resp.json()["details"] == {"reason": "invite_expired"}
    assert _no_slot("Guest") and env.guest.creds_calls == []


async def test_a_failed_character_lookup_refuses_before_the_slot(env, monkeypatch):
    async def broken(name):
        raise OSError("characters.json locked")

    monkeypatch.setattr(local_chars, "resolve_char_uid", broken)
    resp = await _rooms(env)
    assert resp.status_code == 409 and resp.json()["reason"] == "busy"
    assert _no_slot() and env.host.rt is None


async def test_an_unknown_character_is_left_to_the_persona_gate(env, monkeypatch):
    async def none(name):
        return None

    monkeypatch.setattr(local_chars, "resolve_char_uid", none)
    env.gate = PersonaGate(ok=False, state="missing")
    resp = await _rooms(env)
    assert resp.status_code == 409 and resp.json() == {"ok": False, "code": "VISIT_PERSONA_UNREVIEWED", "state": "missing"}


@pytest.mark.parametrize("rows,next_cursor", [(499, "p1"), (501, None), (501, "p1")])
async def test_cloud_pages_off_the_size_contract_are_rejected_at_once(env, monkeypatch, rows, next_cursor):
    calls = []
    page = _details_rows(rows)

    def off_contract(request):
        calls.append(request)
        body = {"visit_id": VISIT_ID, "lines": page, "requester_role": "host"}
        if next_cursor:
            body["next_cursor"] = next_cursor
        return httpx.Response(200, json=body)

    monkeypatch.setattr(env.servers, "_details", off_contract)
    resp = await _transcript(env)
    assert resp.status_code == 404 and resp.json()["code"] == "transcript_gone_local" and len(calls) == 1
