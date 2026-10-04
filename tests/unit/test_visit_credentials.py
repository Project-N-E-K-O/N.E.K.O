# -*- coding: utf-8 -*-
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

"""Servers credentials client (design §5 PR-07, contract §4.7) against a fake Servers."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import config.visit_settings as vs
from main_logic.visit import identity as idm
from main_logic.visit.identity import (
    JtiWindow,
    KeyOutOfWindow,
    PubkeysStale,
    RevokedKid,
    b64url_encode,
    mint_ticket,
    verify_identity_ticket,
)
from main_routers.visit_router import credentials as cr
from tests.fake_clock import patch_module_clock

VISIT_ID = "AbCdEfGhIjKlMnOpQrStUv"
HOST_UID = "0123456789abcdef01234567"
GUEST_UID = "fedcba9876543210fedcba98"
HOST_VID = "h_" + "1" * 24
GUEST_VID = "g_" + "2" * 24
CHAR_TAG = "c" * 32
INVITE = "ABCDEFGHJK"
KID = "k2026a"
BASE = "https://servers.test"
BEARER = "bearer-SECRET-7f3a"
USER_SIG = "usersig-SECRET-91bd"
PRIVATE_MAP_KEY = "pmk-SECRET-55aa"
LIVEKIT_TOKEN = "lk-jwt-SECRET-c0de"
LIVEKIT_HOST = "lk.example.test"

# §4.7 POST /api/visit/credentials 的全部非 2xx / 非 5xx 变体（逐字抄契约）
CREDENTIALS_CONTRACT = {
    (401, "unauthenticated"),
    (403, "banned"),
    (403, "tier_not_entitled"),
    (403, "cross_region_unsupported"),
    (403, "invite_invalid"),
    (403, "invite_expired"),
    (403, "room_full"),
    (403, "self_invite"),
    (410, "room_ended"),
    (410, "invite_expiring"),
    (409, "role_taken"),
    (429, "quota_exceeded"),
}
# §4.7 GET /api/visit/invites/{code}/preview
PREVIEW_CONTRACT = {
    (401, "unauthenticated"),
    (404, "invite_invalid"),
    (410, "invite_expired"),
    (403, "banned"),
    (429, "rate_limited"),
}


def _pub_b64(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return b64url_encode(raw)


class FakeServers:
    """Minimal stateful Servers: signs tickets, binds invites, honours cancel."""

    def __init__(self) -> None:
        self.key = Ed25519PrivateKey.generate()
        self.requests: list[httpx.Request] = []
        self.scripted: dict[str, list[tuple[int, Any]]] = {}
        self.transport = "trtc"
        self.livekit_url = f"wss://{LIVEKIT_HOST}/rtc"
        self.host_ttl = vs.VISIT_HOST_CREDENTIAL_TTL_S
        self.guest_ttl = vs.VISIT_CREDENTIAL_TTL_S
        self.vendor_ttl = vs.VISIT_VENDOR_GRANT_TTL_S
        self.vendor_expires_ttl = None  # None = 与 vendor_ttl 相同
        self.cancelled: set[str] = set()
        self.pubkeys_status = 200
        self.key_window = (int(time.time()) - 86400, int(time.time()) + 30 * 86400)
        self.revoked: list[str] = []

    # —— 路由 ——
    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        for prefix, queue in self.scripted.items():
            if path.startswith(prefix) and queue:
                status, body = queue.pop(0)
                return httpx.Response(status, json=body)
        if path == "/api/visit/pubkeys":
            if self.pubkeys_status != 200:
                return httpx.Response(self.pubkeys_status, json={})
            nb, na = self.key_window
            return httpx.Response(200, json={
                "keys": [{"kid": KID, "alg": "Ed25519", "pub": _pub_b64(self.key), "not_before": nb, "not_after": na}],
                "revoked": list(self.revoked),
                "ttl_s": 86400,
            })
        if path == "/api/visit/credentials":
            return self._credentials(json.loads(request.content))
        if path.startswith("/api/visit/rooms/") and path.endswith("/cancel"):
            visit_id = path.split("/")[4]
            self.cancelled.add(visit_id)
            return httpx.Response(200, json={"ok": True})
        if path.startswith("/api/visit/invites/"):
            return httpx.Response(200, json={
                "visit_id": VISIT_ID,
                "host_display_name": "Mochi",
                "host_short_code": HOST_UID[:6].upper(),
                "cross_region": False,
                "expires_at": time.time() + 500,
                "host_visit_uid": HOST_UID,
            })
        return httpx.Response(404, json={"code": "not_found"})

    def ticket(self, *, role: str, iat: int, ttl: int, transport: str, char_tag: str) -> str:
        uid, vid = (HOST_UID, HOST_VID) if role == "host" else (GUEST_UID, GUEST_VID)
        return mint_ticket({
            "v": 1, "iss": "neko-servers", "aud": "neko-visit", "kid": KID, "sub": uid, "vid": vid,
            "visit_id": VISIT_ID, "role": role, "transport": transport, "char_tag": char_tag,
            "iat": iat, "exp": iat + ttl, "jti": "j" * 22,
        }, self.key)

    def _credentials(self, body: dict) -> httpx.Response:
        role = body["role"]
        if role == "guest" and body.get("visit_id") in self.cancelled:
            return httpx.Response(403, json={"code": "invite_invalid"})
        iat = int(time.time())
        ttl = self.host_ttl if role == "host" else self.guest_ttl
        uid, vid = (HOST_UID, HOST_VID) if role == "host" else (GUEST_UID, GUEST_VID)
        if self.transport == "trtc":
            vendor = {"trtc": {
                "sdk_app_id": 1400000001, "user_id": vid, "user_sig": USER_SIG,
                "private_map_key": PRIVATE_MAP_KEY, "str_room_id": body["visit_id"], "expire": self.vendor_ttl,
            }}
        else:
            vendor = {"livekit": {"url": self.livekit_url, "token": LIVEKIT_TOKEN, "ttl_s": self.vendor_ttl}}
        out = {
            "transport": self.transport,
            "expires_at": iat + ttl,
            "vendor_expires_at": iat + (self.vendor_expires_ttl or self.vendor_ttl),
            "vendor": vendor,
            "identity_ticket": self.ticket(
                role=role, iat=iat, ttl=ttl, transport=self.transport, char_tag=body["char_tag"],
            ),
            "visit_uid": uid,
            "vid": vid,
            "cross_region": False,
            "entitlement": {"tier": "sd600", "free_minutes_left_today": 70, "concurrent_rooms_left": 1},
        }
        if role == "guest":
            out["peer_vid"] = HOST_VID
        else:
            out["invite_code"] = INVITE
            out["invite_expires_at"] = iat + 600
        return httpx.Response(200, json=out)

    def bodies(self, path: str) -> list[dict]:
        return [json.loads(r.content) for r in self.requests if r.url.path == path and r.content]

    def count(self, path_part: str) -> int:
        return sum(1 for r in self.requests if path_part in r.url.path)


class _RegionCM:
    def __init__(self) -> None:
        self.waits = 0

    async def aensure_region_resolved(self, timeout: float = 1.5) -> bool:
        self.waits += 1
        return False


@pytest.fixture
def servers(monkeypatch):
    cr._reset_for_tests()
    fake = FakeServers()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    monkeypatch.setattr(cr, "get_external_http_client", lambda: client)
    monkeypatch.setattr(cr, "social_base_url", lambda: BASE)

    async def _session():
        return cr._ServersSession(base_url=BASE, access_token=BEARER, client_id="client-1", account="u1")

    monkeypatch.setattr(cr, "_servers_session", _session)

    from utils.config_manager import ConfigManager
    import utils.config_manager as cm_pkg

    monkeypatch.setattr(ConfigManager, "_region_cache", True)
    region_cm = _RegionCM()
    monkeypatch.setattr(cm_pkg, "get_config_manager", lambda: region_cm)
    fake.region_cm = region_cm

    def _forbidden(self):  # pragma: no cover - 被调用即失败
        raise AssertionError("_check_non_mainland must never be called by the visit client")

    monkeypatch.setattr(ConfigManager, "_check_non_mainland", _forbidden)
    # 内置表清空：每个用例自己决定
    monkeypatch.setattr(idm, "VISIT_SERVERS_PUBKEYS", {})
    monkeypatch.setattr(idm, "NEKO_VISIT_DEV_KEYFILE", "")
    yield fake
    cr._reset_for_tests()


async def _host(**kw) -> cr.VisitCredentials:
    return await cr.fetch_visit_credentials(role="host", visit_id=VISIT_ID, char_tag=CHAR_TAG, **kw)


async def _guest(**kw) -> cr.VisitCredentials:
    kw.setdefault("invite_code", INVITE)
    return await cr.fetch_visit_credentials(role="guest", visit_id=VISIT_ID, char_tag=CHAR_TAG, **kw)


# ── 请求体与 char_tag ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_host_request_body_and_headers(servers):
    creds = await _host(display_name="Mochi")
    req = next(r for r in servers.requests if r.url.path == "/api/visit/credentials")
    assert req.method == "POST"
    assert req.headers["authorization"] == f"Bearer {BEARER}"
    assert req.headers["x-client-id"] == "client-1"
    body = json.loads(req.content)
    assert body == {
        "role": "host", "visit_id": VISIT_ID, "char_tag": CHAR_TAG, "tier": "sd600",
        "region_hint": "global", "app_version": cr._app_version(), "display_name": "Mochi",
    }
    assert body["app_version"].count(".") == 1
    assert creds.invite_code == INVITE and creds.peer_vid is None
    assert creds.vid == HOST_VID and creds.visit_uid == HOST_UID


class _CharsCM:
    def __init__(self, catgirls: dict) -> None:
        self.data = {"猫娘": catgirls}
        self.backfills = 0

    async def aload_characters(self):
        return json.loads(json.dumps(self.data, ensure_ascii=False))

    async def abackfill_character_uids(self):
        self.backfills += 1
        for entry in self.data["猫娘"].values():
            entry.setdefault("_reserved", {})["character_uid"] = "d" * 32
        return True


@pytest.mark.asyncio
async def test_char_tag_is_the_stable_character_uid_across_rename(servers, monkeypatch):
    import utils.config_manager as cm_pkg

    cm = _CharsCM({"Mochi": {"_reserved": {"character_uid": CHAR_TAG}}})
    monkeypatch.setattr(cm_pkg, "get_config_manager", lambda: cm)
    tag_before = await cr.resolve_char_tag("Mochi")
    # 改名：同一份角色数据换了键名
    cm.data["猫娘"] = {"Momo": cm.data["猫娘"].pop("Mochi")}
    tag_after = await cr.resolve_char_tag("Momo")
    assert tag_before == tag_after == CHAR_TAG
    with pytest.raises(LookupError):
        await cr.resolve_char_tag("Mochi")
    monkeypatch.setattr(cm_pkg, "get_config_manager", lambda: servers.region_cm)
    await cr.fetch_visit_credentials(role="host", visit_id=VISIT_ID, char_tag=tag_after)
    assert servers.bodies("/api/visit/credentials")[-1]["char_tag"] == CHAR_TAG


@pytest.mark.asyncio
async def test_char_tag_missing_is_backfilled_first(monkeypatch):
    import utils.config_manager as cm_pkg

    cm = _CharsCM({"Mochi": {}})
    monkeypatch.setattr(cm_pkg, "get_config_manager", lambda: cm)
    assert await cr.resolve_char_tag("Mochi") == "d" * 32
    assert cm.backfills == 1


@pytest.mark.asyncio
async def test_char_tag_must_be_a_character_uid(servers):
    with pytest.raises(ValueError):
        await cr.fetch_visit_credentials(role="host", visit_id=VISIT_ID, char_tag="Mochi")
    assert servers.requests == []


# ── OAuth 会话 ─────────────────────────────────────────────────────────


@pytest.fixture
def oauth(monkeypatch):
    from main_routers import card_drop_router as card_drop
    from main_routers import community_oauth

    state: dict[str, Any] = {
        "status": {"logged_in": True},
        "saved": False,
        "snapshot": {"base_url": BASE, "access_token": BEARER, "local_user_id": "u1"},
        "client_id": "client-1",
    }

    async def _status():
        status = dict(state["status"])
        status.setdefault("snapshot", state["snapshot"])
        return status

    monkeypatch.setattr(community_oauth, "resolve_saved_oauth_status", _status)
    monkeypatch.setattr(community_oauth, "status_session_saved", lambda s: state["saved"])
    # 磁盘上已经换成别的账号：会话必须用 resolver 校验过的那份，不能重读
    monkeypatch.setattr(card_drop, "_desktop_session_snapshot",
                        lambda: {"base_url": BASE, "access_token": "other-account-token", "local_user_id": "u9"})
    monkeypatch.setattr(card_drop, "_get_client_id", lambda: state["client_id"])
    monkeypatch.setattr(cr, "social_base_url", lambda: BASE)
    return state


@pytest.mark.asyncio
async def test_session_requires_oauth_login(oauth):
    session = await cr._servers_session()
    assert session.headers() == {"Authorization": f"Bearer {BEARER}", "X-Client-Id": "client-1"}
    assert session.account == "u1"
    assert BEARER not in repr(session)

    oauth["status"] = {"logged_in": False}
    with pytest.raises(cr.VisitLoginRequired):
        await cr._servers_session()
    oauth["saved"] = True
    with pytest.raises(cr.VisitServersUnreachable):
        await cr._servers_session()


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot", [
    None,
    {"base_url": BASE, "access_token": BEARER, "local_user_id": None},
    {"base_url": BASE, "access_token": "", "local_user_id": "u1"},
    {"base_url": "https://other.community.test", "access_token": BEARER, "local_user_id": "u1"},
])
async def test_session_without_local_user_or_for_another_origin_is_login_required(oauth, snapshot):
    oauth["snapshot"] = snapshot
    with pytest.raises(cr.VisitLoginRequired):
        await cr._servers_session()


@pytest.mark.asyncio
async def test_no_oauth_session_maps_to_login_required_without_network(oauth, monkeypatch):
    calls = []
    monkeypatch.setattr(cr, "get_external_http_client", lambda: calls.append(1))
    oauth["status"] = {"logged_in": False}
    with pytest.raises(cr.VisitLoginRequired) as exc:
        await cr.fetch_visit_credentials(role="host", visit_id=VISIT_ID, char_tag=CHAR_TAG)
    assert exc.value.to_local_error() == (409, {"code": "VISIT_LOGIN_REQUIRED"})
    assert calls == []


# ── guest 邀请码 ───────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [None, "", "abcdefghjk", "ABCDEFGH1K", "ABCDEFGHJKL"])
async def test_guest_without_valid_invite_code_is_rejected_before_network(servers, code):
    with pytest.raises(cr.VisitInviteFormat):
        await cr.fetch_visit_credentials(role="guest", visit_id=VISIT_ID, char_tag=CHAR_TAG, invite_code=code)
    assert servers.requests == []


@pytest.mark.asyncio
async def test_guest_credentials_carry_peer_vid_and_invite(servers):
    creds = await _guest()
    assert servers.bodies("/api/visit/credentials")[-1]["invite_code"] == INVITE
    assert creds.peer_vid == HOST_VID and creds.invite_code is None and creds.invite_expires_at is None


# ── 错误分派 ───────────────────────────────────────────────────────────


def test_error_dispatch_tables_cover_exactly_the_contract():
    assert set(cr.CREDENTIALS_ERROR_CONTRACT) == CREDENTIALS_CONTRACT
    assert set(cr.PREVIEW_ERROR_CONTRACT) == PREVIEW_CONTRACT


_EXPECTED_CREDENTIALS = {
    (401, "unauthenticated"): (409, "VISIT_LOGIN_REQUIRED"),
    (403, "banned"): (403, "VISIT_BANNED"),
    (403, "tier_not_entitled"): (403, "tier_not_entitled"),
    (403, "cross_region_unsupported"): (403, "cross_region_unsupported"),
    (403, "invite_invalid"): (409, "VISIT_INVITE_INVALID"),
    (403, "invite_expired"): (409, "VISIT_INVITE_INVALID"),
    (403, "room_full"): (409, "VISIT_INVITE_INVALID"),
    (403, "self_invite"): (409, "VISIT_INVITE_INVALID"),
    (409, "role_taken"): (409, "VISIT_INVITE_INVALID"),
    (410, "invite_expiring"): (409, "VISIT_INVITE_INVALID"),
    (410, "room_ended"): (410, "room_ended"),
    (429, "quota_exceeded"): (429, "VISIT_QUOTA_EXCEEDED"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", sorted(CREDENTIALS_CONTRACT))
async def test_every_credentials_error_variant_maps(servers, status, code):
    body = {"code": code}
    if status == 429:
        body["retry_after_s"] = 321
    servers.scripted["/api/visit/credentials"] = [(status, body)]
    with pytest.raises(cr.VisitServersError) as exc:
        await _guest()
    local_status, local_body = exc.value.to_local_error()
    assert (local_status, local_body["code"]) == _EXPECTED_CREDENTIALS[(status, code)]
    if local_body["code"] == "VISIT_INVITE_INVALID":
        # toast 按原 code 选文案：失效 / 过期 / 房间已满 / 自己的邀请 / 位置已被占 / 即将过期
        assert local_body["details"] == {"reason": code}
    if status == 429:
        assert local_body["retry_after_s"] == 321
    if code == "room_ended":
        assert exc.value.finalize_reason == "kicked"
        assert not isinstance(exc.value, cr.VisitInviteInvalid)


@pytest.mark.asyncio
async def test_banned_is_cached_for_a_minute_per_account(servers):
    assert not cr.banned_recently("u1")
    servers.scripted["/api/visit/credentials"] = [(403, {"code": "banned"})]
    with pytest.raises(cr.VisitBanned):
        await _host()
    assert cr.banned_recently("u1")
    # 同一台机器换成别的账号不受牵连
    assert not cr.banned_recently("u2")


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["expires_at", "vendor_expires_at"])
async def test_already_expired_credentials_are_rejected(servers, field):
    original = servers._credentials

    def _stale(body):
        data = original(body).json()
        past = int(time.time()) - vs.VISIT_TICKET_CLOCK_TOLERANCE_S - 5
        if field == "expires_at":
            # 票本身也一起过期（exp 与 expires_at 一致），只让「已过期」这一条把它拒掉
            ttl = vs.VISIT_CREDENTIAL_TTL_S
            data["identity_ticket"] = servers.ticket(
                role="guest", iat=past - ttl, ttl=ttl, transport="trtc", char_tag=body["char_tag"],
            )
        data[field] = past
        return httpx.Response(200, json=data)

    servers._credentials = _stale
    with pytest.raises(cr.VisitServersUnreachable):
        await _guest()


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["trtc", "livekit"])
async def test_longest_accepted_grant_still_fits_one_credentials_message(servers, monkeypatch, transport):
    from main_routers.visit_router import transport_ws as tw

    monkeypatch.setattr(vs, "VISIT_LIVEKIT_HOSTS", frozenset({LIVEKIT_HOST}))
    servers.transport = transport
    original = servers._credentials

    def _longest(body):
        data = original(body).json()
        if transport == "trtc":
            data["vendor"]["trtc"]["user_sig"] = "s" * cr._TRTC_SIG_MAX_CHARS
            data["vendor"]["trtc"]["private_map_key"] = "k" * cr._TRTC_SIG_MAX_CHARS
        else:
            data["vendor"]["livekit"]["token"] = "t" * cr._LIVEKIT_TOKEN_MAX_CHARS
        return httpx.Response(200, json=data)

    servers._credentials = _longest
    creds = await _guest()
    msg = tw.build_credentials_message(creds, side="guest", crop="upper", codec="vp9")
    assert len(json.dumps(msg, ensure_ascii=False, separators=(",", ":")).encode()) <= tw.CREDENTIALS_MAX_BYTES


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [500, 502, 503])
async def test_5xx_is_servers_unreachable(servers, status):
    servers.scripted["/api/visit/credentials"] = [(status, {"code": "x"})]
    with pytest.raises(cr.VisitServersUnreachable) as exc:
        await _host()
    assert exc.value.to_local_error()[0] == 503
    assert exc.value.code == "servers_unreachable"


@pytest.mark.asyncio
async def test_network_error_is_servers_unreachable(servers, monkeypatch):
    def _boom(request):
        raise httpx.ConnectError("down", request=request)

    monkeypatch.setattr(cr, "get_external_http_client", lambda: httpx.AsyncClient(transport=httpx.MockTransport(_boom)))
    with pytest.raises(cr.VisitServersUnreachable):
        await _host()


@pytest.mark.asyncio
async def test_uncontracted_reply_is_unreachable_with_a_diagnostic(servers, caplog):
    servers.scripted["/api/visit/credentials"] = [(403, {"code": "brand_new_code"}), (418, {})]
    with caplog.at_level(logging.WARNING):
        with pytest.raises(cr.VisitServersUnreachable) as exc:
            await _host()
        assert exc.value.reason == "uncontracted_reply"
        with pytest.raises(cr.VisitServersUnreachable):
            await _host()
    assert "uncontracted reply status=403 code=brand_new_code" in caplog.text


@pytest.mark.asyncio
async def test_redirects_are_not_followed(servers, monkeypatch):
    seen: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == "servers.test" and request.url.path == "/api/visit/credentials":
            return httpx.Response(307, headers={"location": "https://elsewhere.test/collect"})
        return httpx.Response(200, json={})

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    monkeypatch.setattr(cr, "get_external_http_client", lambda: client)
    with pytest.raises(cr.VisitServersUnreachable):
        await _host()
    # bearer 只发给 Servers：跳转目标一次都没被请求
    assert not any(r.url.host == "elsewhere.test" for r in seen)


@pytest.mark.asyncio
@pytest.mark.parametrize("claim,value", [
    ("v", 2), ("iss", "someone-else"), ("aud", "other"), ("iat", None),
])
async def test_ticket_fixed_and_temporal_claims_are_checked(servers, claim, value):
    original = servers._credentials

    def _bad(body):
        data = original(body).json()
        claims = idm.peek_ticket_claims(data["identity_ticket"]).__dict__.copy()
        claims = {k: v for k, v in claims.items() if v is not None}
        if claim == "iat":
            # 未来签发：票面时长与 expires_at 都对得上，只有 iat 在未来
            shift = vs.VISIT_TICKET_CLOCK_TOLERANCE_S + 60
            claims["iat"] += shift
            claims["exp"] += shift
            data["expires_at"] = claims["exp"]
        else:
            claims[claim] = value
        data["identity_ticket"] = mint_ticket(claims, servers.key)
        return httpx.Response(200, json=data)

    servers._credentials = _bad
    with pytest.raises(cr.VisitServersUnreachable):
        await _host()


@pytest.mark.asyncio
async def test_invite_code_never_reaches_the_httpx_request_log(servers, caplog):
    with caplog.at_level(logging.INFO, logger="httpx"):
        logging.getLogger("httpx").info('HTTP Request: GET %s "HTTP/1.1 200 OK"',
                                        f"{BASE}/api/visit/invites/{INVITE}/preview")
        logging.getLogger("httpx").info('HTTP Request: GET %s "HTTP/1.1 200 OK"', f"{BASE}/api/visit/pubkeys")
    assert INVITE not in caplog.text
    assert "/api/visit/pubkeys" in caplog.text


# ── 区域提示 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("cached,expected,waits", [(True, "global", 0), (False, "cn", 0), (None, "unknown", 1)])
async def test_region_hint_reads_the_cache_only(servers, monkeypatch, cached, expected, waits):
    from utils.config_manager import ConfigManager

    monkeypatch.setattr(ConfigManager, "_region_cache", cached)
    await _host()
    assert servers.bodies("/api/visit/credentials")[-1]["region_hint"] == expected
    assert servers.region_cm.waits == waits


# ── LiveKit 白名单 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_livekit_url_host_must_be_allowlisted(servers, monkeypatch):
    servers.transport = "livekit"
    monkeypatch.setattr(vs, "VISIT_LIVEKIT_HOSTS", frozenset())
    with pytest.raises(cr.VisitLivekitHostRejected):
        await _host()
    monkeypatch.setattr(vs, "VISIT_LIVEKIT_HOSTS", frozenset({LIVEKIT_HOST}))
    creds = await _host()
    assert creds.vendor == {"livekit": {"url": servers.livekit_url, "token": LIVEKIT_TOKEN, "ttl_s": 600}}
    for url in (f"wss://evil.test/rtc", f"ws://{LIVEKIT_HOST}/rtc", f"wss://{LIVEKIT_HOST}.evil.test/"):
        servers.livekit_url = url
        with pytest.raises(cr.VisitLivekitHostRejected):
            await _host()


# ── 身份票与 vendor 凭证的有效期 ───────────────────────────────────────


def test_host_ticket_lifetime_covers_invite_wait_visit_and_margin():
    assert vs.VISIT_HOST_CREDENTIAL_TTL_S == vs.VISIT_INVITE_WAIT_S + vs.VISIT_MAX_DURATION_S + 600
    assert idm.ticket_ttl_s("host") == 3000 and idm.ticket_ttl_s("guest") == 2400


@pytest.mark.asyncio
async def test_ticket_lifetime_is_per_role_and_vendor_grant_is_ten_minutes(servers):
    for fetch, ttl in ((_guest, 2400), (_host, 3000)):
        creds = await fetch()
        claims = idm.peek_ticket_claims(creds.identity_ticket)
        assert claims.exp - claims.iat == ttl
        assert creds.expires_at == claims.exp
        # vendor 凭证与身份票分开：恒为 600，客户端原样收下不改写
        assert creds.vendor["trtc"]["expire"] == 600
        assert creds.vendor_expires_at - claims.iat == 600


@pytest.mark.asyncio
async def test_host_ticket_signed_for_guest_lifetime_is_rejected(servers):
    servers.host_ttl = vs.VISIT_CREDENTIAL_TTL_S
    with pytest.raises(cr.VisitServersUnreachable) as exc:
        await _host()
    assert exc.value.reason == "invalid_response"


@pytest.mark.asyncio
@pytest.mark.parametrize("transport", ["trtc", "livekit"])
async def test_vendor_grant_signed_for_ticket_lifetime_is_rejected(servers, monkeypatch, transport):
    monkeypatch.setattr(vs, "VISIT_LIVEKIT_HOSTS", frozenset({LIVEKIT_HOST}))
    servers.transport = transport
    servers.vendor_ttl = vs.VISIT_CREDENTIAL_TTL_S
    with pytest.raises(cr.VisitServersUnreachable):
        await _guest()


@pytest.mark.asyncio
@pytest.mark.parametrize("grant_ttl,expires_ttl", [(2400, 600), (600, 2400)])
async def test_each_vendor_lifetime_field_is_checked_on_its_own(servers, grant_ttl, expires_ttl):
    # vendor.trtc.expire 与 vendor_expires_at 各自校验：只改其中一个也要拒
    servers.vendor_ttl = grant_ttl
    servers.vendor_expires_ttl = expires_ttl
    with pytest.raises(cr.VisitServersUnreachable):
        await _guest()


@pytest.mark.asyncio
async def test_credentials_repr_and_logs_never_carry_secrets(servers, caplog):
    with caplog.at_level(logging.DEBUG):
        creds = await _host()
        servers.scripted["/api/visit/credentials"] = [(403, {"code": "room_full"})]
        with pytest.raises(cr.VisitServersError):
            await _host()
    text = repr(creds) + caplog.text
    for secret in (BEARER, USER_SIG, PRIVATE_MAP_KEY, creds.identity_ticket, INVITE):
        assert secret not in text


# ── 续期 ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_grant_renews_vendor_and_keeps_identity_ticket(servers):
    first = await _guest()
    grant = cr.VisitGrant(first, invite_code=INVITE)
    now = time.time()
    assert not grant.refresh_due(now)
    assert grant.refresh_due(first.vendor_expires_at - 119)
    assert not await grant.ensure_fresh(now)
    assert await grant.ensure_fresh(first.vendor_expires_at - 60)
    renewed = grant.current
    assert renewed.identity_ticket == first.identity_ticket
    assert renewed.expires_at == first.expires_at
    assert servers.count("/api/visit/credentials") == 2
    assert servers.bodies("/api/visit/credentials")[-1]["invite_code"] == INVITE


@pytest.mark.asyncio
async def test_host_renewal_after_the_invite_was_redeemed_keeps_the_invite(servers):
    first = await _host()
    grant = cr.VisitGrant(first)
    original = servers._credentials

    def _reissue(body):
        resp = original(body)
        data = resp.json()
        data.pop("invite_code")
        data.pop("invite_expires_at")
        data["peer_vid"] = GUEST_VID
        return httpx.Response(200, json=data)

    servers._credentials = _reissue
    renewed = await grant.renew()
    assert renewed.invite_code == INVITE and renewed.peer_vid is None
    assert servers.bodies("/api/visit/credentials")[-1].get("invite_code") is None


@pytest.mark.asyncio
async def test_host_malformed_invite_code_is_rejected(servers):
    original = servers._credentials

    def _bad(body):
        data = original(body).json()
        data["invite_code"] = "abc"
        return httpx.Response(200, json=data)

    servers._credentials = _bad
    with pytest.raises(cr.VisitServersUnreachable):
        await _host()


@pytest.mark.asyncio
async def test_grant_renewal_after_forced_end_is_room_ended(servers):
    grant = cr.VisitGrant(await _host())
    servers.scripted["/api/visit/credentials"] = [(410, {"code": "room_ended"})]
    with pytest.raises(cr.VisitRoomEnded) as exc:
        await grant.renew()
    assert exc.value.finalize_reason == "kicked"


# ── host 取消 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_host_cancel_is_sent_once_and_the_invite_dies(servers):
    host = await _host()
    assert await cr.cancel_visit_room(VISIT_ID, invite_expires_at=host.invite_expires_at)
    assert servers.count("/cancel") == 1
    with pytest.raises(cr.VisitInviteInvalid) as exc:
        await _guest()
    assert exc.value.reason == "invite_invalid"


@pytest.mark.asyncio
async def test_host_cancel_retries_5xx_with_backoff(servers, monkeypatch):
    sleeps: list[float] = []

    async def _sleep(d):
        sleeps.append(d)

    monkeypatch.setattr(cr, "_sleep", _sleep)
    servers.scripted["/api/visit/rooms/"] = [(503, {}), (503, {})]
    assert await cr.cancel_visit_room(VISIT_ID, invite_expires_at=time.time() + 500)
    assert servers.count("/cancel") == 3
    assert sleeps == [1, 2]


@pytest.mark.asyncio
async def test_host_cancel_retries_network_errors(servers, monkeypatch):
    async def _sleep(d):
        return None

    monkeypatch.setattr(cr, "_sleep", _sleep)
    failures = [2]

    def _handler(request: httpx.Request) -> httpx.Response:
        if failures[0] > 0:
            failures[0] -= 1
            raise httpx.ConnectError("down", request=request)
        return servers.handler(request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(_handler))
    monkeypatch.setattr(cr, "get_external_http_client", lambda: client)
    assert await cr.cancel_visit_room(VISIT_ID, invite_expires_at=time.time() + 500)
    assert servers.count("/cancel") == 1 and failures == [0]


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", [(409, "guest_joined"), (403, "invite_invalid")])
async def test_host_cancel_done_replies(servers, status, code):
    servers.scripted["/api/visit/rooms/"] = [(status, {"code": code})]
    assert await cr.cancel_visit_room(VISIT_ID, invite_expires_at=time.time() + 500)
    assert servers.count("/cancel") == 1


@pytest.mark.asyncio
async def test_host_cancel_gives_up_at_invite_expiry(servers, monkeypatch):
    async def _sleep(d):
        return None

    monkeypatch.setattr(cr, "_sleep", _sleep)
    servers.scripted["/api/visit/rooms/"] = [(503, {})] * 50
    assert not await cr.cancel_visit_room(VISIT_ID, invite_expires_at=time.time() + 5)
    assert 1 <= servers.count("/cancel") <= 4


# ── 公钥 ───────────────────────────────────────────────────────────────


def _ticket_for(servers, *, iat: int, role: str = "guest") -> str:
    return servers.ticket(role=role, iat=iat, ttl=idm.ticket_ttl_s(role), transport="trtc", char_tag=CHAR_TAG)


def _verify(ticket: str, pubkeys, now: float):
    return verify_identity_ticket(
        ticket, expect_visit_id=VISIT_ID, expect_role="guest", expect_vid=GUEST_VID, expect_transport="trtc",
        now=now, pubkeys=pubkeys, blocklist=_NoBlock(), jti_window=JtiWindow(),
    )


class _NoBlock:
    def is_blocked(self, uid: str) -> bool:
        return False


@pytest.mark.asyncio
async def test_pubkeys_are_cached_for_a_day(servers, monkeypatch):
    keys = await cr.fetch_pubkeys()
    assert KID in keys.keys and not keys.stale
    await cr.fetch_pubkeys()
    assert servers.count("/pubkeys") == 1
    await cr.fetch_pubkeys(force_refresh=True)
    assert servers.count("/pubkeys") == 2
    real = time.time()
    patch_module_clock(monkeypatch, cr, time=lambda: real + 86400 + 10)
    await cr.fetch_pubkeys()
    assert servers.count("/pubkeys") == 3


@pytest.mark.asyncio
async def test_credentials_fetch_refreshes_pubkeys(servers):
    await _host()
    await _guest()
    assert servers.count("/pubkeys") == 2


@pytest.mark.asyncio
async def test_pubkeys_fetch_failure_still_returns_builtin_table_but_stale(servers, monkeypatch):
    builtin_key = Ed25519PrivateKey.generate()
    monkeypatch.setattr(idm, "VISIT_SERVERS_PUBKEYS", {
        "k-builtin": {"pub": _pub_b64(builtin_key), "not_before": 0, "not_after": 2 ** 40},
    })
    servers.pubkeys_status = 503
    keys = await cr.fetch_pubkeys()
    assert "k-builtin" in keys.keys
    assert keys.stale
    # stale = 不知道最新吊销名单：内置钥匙签的票也一律拒
    with pytest.raises(PubkeysStale):
        _verify(_ticket_for(servers, iat=int(time.time())), keys, time.time())


@pytest.mark.asyncio
async def test_expired_cache_with_failed_refresh_fails_closed(servers, monkeypatch):
    keys = await cr.fetch_pubkeys()
    ticket = _ticket_for(servers, iat=int(time.time()))
    assert _verify(ticket, keys, time.time()).vid == GUEST_VID
    servers.pubkeys_status = 503
    real = time.time()
    patch_module_clock(monkeypatch, cr, time=lambda: real + 86400 + 10)
    stale = await cr.fetch_pubkeys(force_refresh=True)
    assert KID in stale.keys and stale.stale
    with pytest.raises(PubkeysStale):
        _verify(ticket, stale, real + 60)


@pytest.mark.asyncio
async def test_key_validity_window_bounds_the_ticket_iat(servers):
    now = int(time.time())
    # 钥匙已在 1 min 前下线：iat=now 的票不收
    servers.key_window = (now - 86400, now - 60)
    keys = await cr.fetch_pubkeys(force_refresh=True)
    assert keys.keys[KID].not_after == now - 60
    with pytest.raises(KeyOutOfWindow):
        _verify(_ticket_for(servers, iat=now), keys, now)
    # 下线前 1 min 签的票，在其 exp 内仍然有效
    old = _ticket_for(servers, iat=now - 120)
    assert _verify(old, keys, now).iat == now - 120


@pytest.mark.asyncio
async def test_revoked_kid_is_removed_even_from_builtin(servers, monkeypatch):
    monkeypatch.setattr(idm, "VISIT_SERVERS_PUBKEYS", {
        KID: {"pub": _pub_b64(servers.key), "not_before": 0, "not_after": 2 ** 40},
    })
    servers.revoked = [KID]
    keys = await cr.fetch_pubkeys(force_refresh=True)
    assert KID in keys.revoked
    with pytest.raises(RevokedKid):
        _verify(_ticket_for(servers, iat=int(time.time())), keys, time.time())


# ── 邀请预览 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["", "abcdefghjk", "ABCDEFGH01", "ABC"])
async def test_preview_rejects_bad_format_before_network(servers, code):
    with pytest.raises(cr.VisitInviteFormat) as exc:
        await cr.fetch_invite_preview(code, generic_label="Neko")
    assert exc.value.to_local_error() == (400, {"code": "invite_code_format", "details": {"reason": "invite_code_format"}})
    assert servers.requests == []


@pytest.mark.asyncio
async def test_preview_is_a_bodyless_get_and_hides_the_host_uid(servers):
    preview = await cr.fetch_invite_preview(INVITE, generic_label="Neko", protected_names=["Alice"])
    req = servers.requests[-1]
    assert req.method == "GET" and req.content == b""
    assert req.url.path == f"/api/visit/invites/{INVITE}/preview"
    assert req.headers["authorization"] == f"Bearer {BEARER}"
    assert preview.host_visit_uid == HOST_UID
    public = preview.to_public(locally_blocked=False)
    assert set(public) == {"visit_id", "host_display_name", "host_short_code", "cross_region", "expires_at",
                           "locally_blocked"}
    assert HOST_UID not in json.dumps(public)
    assert public["host_display_name"] == "Mochi" and public["host_short_code"] == HOST_UID[:6].upper()


@pytest.mark.asyncio
async def test_preview_blocklist_hit_is_locally_blocked(servers):
    preview = await cr.fetch_invite_preview(INVITE, generic_label="Neko")

    class _Block:
        def __init__(self, uids):
            self.uids = uids

        def is_blocked(self, uid):
            return uid in self.uids

    class _Broken:
        def is_blocked(self, uid):
            raise RuntimeError("unreadable")

    assert cr.is_locally_blocked(preview, _Block({HOST_UID}))
    assert not cr.is_locally_blocked(preview, _Block(set()))
    assert cr.is_locally_blocked(preview, _Broken())


@pytest.mark.asyncio
async def test_preview_display_name_is_cleaned(servers):
    servers.scripted["/api/visit/invites/"] = [(200, {
        "visit_id": VISIT_ID, "host_display_name": "Alice", "host_short_code": HOST_UID[:6].upper(),
        "cross_region": True, "expires_at": time.time() + 300, "host_visit_uid": HOST_UID,
    })]
    preview = await cr.fetch_invite_preview(INVITE, generic_label="Neko", protected_names=["Alice"])
    # 与本机亲人同名 → 换成通用标签 + 短码
    assert preview.host_display_name == f"Neko {HOST_UID[:6].upper()}"
    assert preview.cross_region is True


@pytest.mark.asyncio
async def test_preview_short_code_must_match_the_uid(servers):
    servers.scripted["/api/visit/invites/"] = [(200, {
        "visit_id": VISIT_ID, "host_display_name": "Mochi", "host_short_code": "ZZZZZZ",
        "cross_region": False, "expires_at": time.time() + 300, "host_visit_uid": HOST_UID,
    })]
    with pytest.raises(cr.VisitServersUnreachable):
        await cr.fetch_invite_preview(INVITE, generic_label="Neko")


_EXPECTED_PREVIEW = {
    (401, "unauthenticated"): (409, "VISIT_LOGIN_REQUIRED"),
    (404, "invite_invalid"): (404, "invite_invalid"),
    (410, "invite_expired"): (410, "invite_expired"),
    (403, "banned"): (403, "VISIT_BANNED"),
    (429, "rate_limited"): (429, "rate_limited"),
}


@pytest.mark.asyncio
@pytest.mark.parametrize("status,code", sorted(PREVIEW_CONTRACT) + [(503, "x")])
async def test_preview_error_mapping(servers, status, code):
    body = {"code": code}
    if status == 429:
        body["retry_after_s"] = 42
    servers.scripted["/api/visit/invites/"] = [(status, body)]
    with pytest.raises(cr.VisitServersError) as exc:
        await cr.fetch_invite_preview(INVITE, generic_label="Neko")
    local_status, local_body = exc.value.to_local_error()
    expected = _EXPECTED_PREVIEW.get((status, code), (503, "servers_unreachable"))
    assert (local_status, local_body["code"]) == expected
    if status == 429:
        assert local_body["retry_after_s"] == 42
