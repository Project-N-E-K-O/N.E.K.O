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

"""``/api/visit`` history / details / report endpoints and the package router (visit design §4.6, PR-09a)."""

from __future__ import annotations

import json
import time

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import config.visit_settings as visit_settings
from main_routers.system_router import AUTOSTART_CSRF_TOKEN
from main_routers.visit_router import accounts, cloud_routes, memory_routes
from main_routers.visit_router import credentials as cr
from main_routers.visit_router import router as visit_router
from main_routers.visit_router import transcript_upload as tu
from main_routers.visit_router.local_context import CharacterContext
from tests.fastapi_routes import effective_path, iter_routes
from tests.unit.visit_memory_test_helpers import vid
from tests.unit.visit_servers_fake import BASE, FakeServers

ORIGIN = "http://testserver"
GOOD = {"Origin": ORIGIN, "X-CSRF-Token": AUTOSTART_CSRF_TOKEN}
OWN = "a" * 24
V1 = vid(1)
CHAR_UID = "c" * 32


@pytest.fixture
def env(tmp_path, monkeypatch):
    tu._reset_for_tests()
    fake = FakeServers()
    client_http = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    monkeypatch.setattr(cr, "get_external_http_client", lambda: client_http)
    state = {"account": "u1"}

    async def session():
        if state["account"] is None:
            raise cr.VisitLoginRequired()
        return cr._ServersSession(base_url=BASE, access_token="bearer-x", client_id="c1", account=state["account"])

    async def local_account():
        return state["account"]

    async def context():
        return CharacterContext(family_names=("小明",), cards={"A": "card", "Mimi": "card"})

    async def no_sleep(_s):
        return None

    monkeypatch.setattr(cr, "_servers_session", session)
    monkeypatch.setattr(accounts, "local_account", local_account)
    monkeypatch.setattr(accounts, "config_dir_provider", lambda: tmp_path)
    monkeypatch.setattr(tu, "config_dir_provider", lambda: tmp_path)
    monkeypatch.setattr(tu, "is_live", lambda _v: False)
    monkeypatch.setattr(tu, "_sleep", no_sleep)
    monkeypatch.setattr(cloud_routes, "load_character_context", context)
    monkeypatch.setattr(cloud_routes, "prompt_lang", lambda: "zh")
    monkeypatch.setattr(visit_settings, "VISIT_ENABLED", True)
    monkeypatch.setattr(visit_settings, "NEKO_VISIT_ALLOW_NONLOCAL", False)
    monkeypatch.delenv("NEKO_BEHIND_PROXY", raising=False)

    async def own_uid():
        return OWN

    memory_routes.configure_memory_routes(own_visit_uid=own_uid, config_dir=lambda: tmp_path)
    (tmp_path / "visit_accounts.json").write_text(json.dumps({"accounts": {"u1": OWN}}), encoding="utf-8")
    app = FastAPI()
    app.include_router(visit_router)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        yield client, fake, tmp_path, state
    tu._reset_for_tests()
    memory_routes.configure_memory_routes(own_visit_uid=memory_routes._no_account,
                                          config_dir=memory_routes._default_config_dir)


def _write_sealed(tmp_path, visit_id=V1):
    doc = {"v": 1, "own_visit_uid": OWN, "own_char_uid": CHAR_UID, "transport": "trtc", "request": {
        "visit_id": visit_id, "role": "host", "started_at": 1.0, "ended_at": 2.0, "finalized_reason": "wrap_up",
        "usage": {"duration_s": 1, "llm_input_tokens": 0, "llm_output_tokens": 0, "tts_requests": 0,
                  "tts_chars": 0},
        "lines": [{"lp": 1, "side": "host", "from": "own_cat", "ts": 1.5, "text": "hi", "truncated": False}],
        "anomalies": 4, "app_version": "1.2"}}
    path = tmp_path / "visit_spool" / f"{visit_id}.upload.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _report(client, **over):
    body = {"visit_id": V1, "reason": "harassment", "note": "rude", "include_transcript": False}
    body.update(over)
    return client.post("/api/visit/report", headers=GOOD, json=body)


def _queued(tmp_path, visit_id=V1):
    path = tmp_path / "visit_reports" / f"{visit_id}.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


# ── 包路由与总闸 ───────────────────────────────────────────────────────


def test_every_visit_route_sits_once_under_api_visit(env):
    client, *_ = env
    paths = [effective_path(r) for r in iter_routes(client.app.routes)]
    visit_paths = [p for p in paths if "visit" in p]
    assert visit_paths and all(p.startswith("/api/visit/") for p in visit_paths)
    assert "/api/visit/transport/ws" in visit_paths
    assert not any("/api/visit/api/visit" in p or p.endswith("/") for p in visit_paths)


def test_release_switch_closes_only_the_start_endpoints(env, monkeypatch):
    client, fake, tmp_path, _ = env
    monkeypatch.setattr(visit_settings, "VISIT_ENABLED", False)
    assert client.get("/api/visit/persona?catgirl=A", headers=GOOD).status_code == 404
    with pytest.raises(WebSocketDisconnect) as exc:
        with client.websocket_connect("/api/visit/transport/ws", headers={"Origin": ORIGIN}):
            pass
    assert exc.value.code == 4404
    # 数据管理照常：记忆列表、历史、举报、举报队列
    assert client.get("/api/visit/memory/peers?catgirl=A", headers=GOOD).status_code == 200
    assert client.get("/api/visit/history", headers=GOOD).status_code == 200
    assert _report(client).status_code == 200
    assert client.get("/api/visit/report/queue", headers=GOOD).status_code == 200


# ── 本机来源闸 ─────────────────────────────────────────────────────────

DATA_ENDPOINTS = [
    ("get", "/api/visit/history"),
    ("get", f"/api/visit/details/{V1}"),
    ("post", "/api/visit/report"),
    ("get", "/api/visit/report/queue"),
    ("post", f"/api/visit/report/queue/{V1}"),
    ("get", "/api/visit/persona?catgirl=A"),
]


def _call(client, method, path, headers):
    if method == "get":
        return client.get(path, headers=headers)
    return client.post(path, headers=headers, json={"visit_id": V1, "reason": "spam", "include_transcript": False,
                                                    "action": "abandon"})


@pytest.mark.parametrize("method,path", DATA_ENDPOINTS)
def test_without_csrf_or_from_another_host_is_403(env, method, path):
    client, *_ = env
    assert _call(client, method, path, {"Origin": ORIGIN}).status_code == 403
    for host in ("172.17.0.2", "192.168.1.20"):
        remote = TestClient(client.app, client=(host, 5000))
        assert _call(remote, method, path, GOOD).status_code == 403


@pytest.mark.parametrize("method,path", DATA_ENDPOINTS)
def test_forwarding_headers_and_proxy_mode_are_refused(env, method, path, monkeypatch):
    client, *_ = env
    for header in ("X-Forwarded-For", "Forwarded", "X-Real-IP"):
        assert _call(client, method, path, {**GOOD, header: "127.0.0.1"}).status_code == 403
    monkeypatch.setenv("NEKO_BEHIND_PROXY", "true")
    assert _call(client, method, path, {**GOOD, "X-Forwarded-For": "127.0.0.1"}).status_code == 403
    monkeypatch.setattr(visit_settings, "NEKO_VISIT_ALLOW_NONLOCAL", True)
    assert _call(client, method, path, {**GOOD, "X-Forwarded-For": "127.0.0.1"}).status_code != 403


@pytest.mark.parametrize("host", ["::1", "127.0.0.5"])
def test_any_loopback_address_passes(env, host):
    client, *_ = env
    local = TestClient(client.app, client=(host, 5000))
    assert local.get("/api/visit/report/queue", headers=GOOD).status_code == 200


# ── 历史 / 详情 ────────────────────────────────────────────────────────


def test_history_cleans_peer_display_names(env):
    client, fake, *_ = env
    fake.history_items = [
        {"visit_id": V1, "role": "host", "peer_display_name": "Nyan\x07" + "x" * 200, "peer_short_code": "ABCDEF",
         "started_at": 1.0, "ended_at": 2.0},
        {"visit_id": vid(2), "role": "guest", "peer_display_name": "小明", "peer_short_code": "123456",
         "started_at": 1.0, "ended_at": 2.0},
        {"visit_id": "../../visit_blocklist", "role": "host", "peer_display_name": "x"},
    ]
    body = client.get("/api/visit/history", headers=GOOD).json()
    names = [item["peer_display_name"] for item in body["items"]]
    assert len(body["items"]) == 2 and body["next_cursor"] == "page-2"
    assert "\x07" not in names[0] and len(names[0]) <= 64
    assert "小明" not in names[1] and names[1].endswith("123456")


def test_history_maps_401_to_login_required(env):
    client, fake, *_ = env
    fake.history_mode = "401"
    resp = client.get("/api/visit/history", headers=GOOD)
    assert resp.status_code == 409 and resp.json()["code"] == "VISIT_LOGIN_REQUIRED"
    fake.history_mode = "503"
    assert client.get("/api/visit/history", headers=GOOD).status_code == 503


def test_details_pass_the_cursor_through_page_by_page(env):
    client, fake, tmp_path, _ = env
    fake.details_lines = [{"lp": i, "side": "host", "host": {"text": f"l{i}"}, "guest": None, "status": "only_host"}
                          for i in range(1200)]
    cursors, rows = [""], 0
    for _ in range(10):     # 有界：丢掉 cursor 的实现会永远停在第 1 页
        url = f"/api/visit/details/{V1}?catgirl=A" + (f"&cursor={cursors[-1]}" if cursors[-1] else "")
        page = client.get(url, headers=GOOD).json()
        rows += len(page["lines"])
        if "next_cursor" not in page:
            break
        cursors.append(page["next_cursor"])
    assert rows == 1200 and cursors == ["", "p1", "p2"]
    sent = [httpx.URL(str(r.url)).params.get("cursor") for r in fake.requests if "/details/" in r.url.path]
    assert sent == [None, "p1", "p2"]
    assert not any(tmp_path.rglob("*.json")) or {p.name for p in tmp_path.rglob("*.json")} == {"visit_accounts.json"}


@pytest.mark.parametrize("mode,status,code", [("403", 403, "not_participant"), ("404", 404, "unknown_visit"),
                                              ("401", 409, "VISIT_LOGIN_REQUIRED")])
def test_details_errors_map_to_local_codes(env, mode, status, code):
    client, fake, *_ = env
    fake.details_mode = mode
    resp = client.get(f"/api/visit/details/{V1}", headers=GOOD)
    assert resp.status_code == status and resp.json()["code"] == code


@pytest.mark.parametrize("bad", ["..%5C..%5Cvisit_blocklist", "a" * 21, "visit%2e%2e0000000000000"])
def test_malformed_visit_ids_are_rejected(env, bad):
    client, fake, tmp_path, _ = env
    assert client.get(f"/api/visit/details/{bad}", headers=GOOD).status_code in (400, 404)
    resp = client.post("/api/visit/report", headers=GOOD,
                       json={"visit_id": bad, "reason": "spam", "include_transcript": False})
    assert resp.status_code == 400
    assert not (tmp_path / "visit_reports").exists() and not fake.requests


# ── 举报端点 ───────────────────────────────────────────────────────────


def test_report_without_transcript_goes_immediately_even_if_uploads_fail(env):
    client, fake, tmp_path, _ = env
    fake.transcript_mode = "budget"
    _write_sealed(tmp_path)
    resp = _report(client)
    assert resp.status_code == 200 and resp.json()["report_id"] == "r1"
    assert fake.count("/api/visit/reports") == 1 and fake.count("/api/visit/transcripts") == 0
    assert _queued(tmp_path) is None
    assert fake.reports[0]["anomalies"] == 4 and "peer_uid" not in json.dumps(fake.reports[0])


@pytest.mark.parametrize("mode", ["503", "network", "429"])
def test_report_failures_queue_it_and_keep_retrying(env, mode):
    client, fake, tmp_path, _ = env
    fake.report_mode = mode
    resp = _report(client)
    assert resp.status_code == 202 and resp.json() == {"queued": True}
    doc = _queued(tmp_path)
    assert set(doc) >= set(tu.REPORT_FIELDS) and doc["own_visit_uid"] == OWN and doc["note"] == "rude"
    fake.report_mode = "ok"
    client.portal.call(tu.retry_visit_once, V1)
    assert _queued(tmp_path) is None and fake.reports[-1]["reason"] == "harassment"


def test_report_with_transcript_waits_for_the_upload(env):
    client, fake, tmp_path, _ = env
    fake.transcript_mode = "429"
    _write_sealed(tmp_path)
    resp = _report(client, include_transcript=True)
    assert resp.status_code == 202 and fake.count("/api/visit/reports") == 0
    assert _queued(tmp_path) is not None
    fake.transcript_mode = "ok"
    client.portal.call(tu.retry_visit_once, V1)
    assert fake.transcript_seen_at_report == [True] and _queued(tmp_path) is None


def test_report_with_transcript_after_a_terminal_rejection_goes_with_the_reason(env):
    client, fake, tmp_path, _ = env
    fake.transcript_mode = "parts"
    _write_sealed(tmp_path)
    resp = _report(client, include_transcript=True)
    assert resp.status_code == 200
    assert fake.reports[0]["transcript_unavailable"] == "parts_out_of_range"


def test_report_with_transcript_of_an_uploaded_visit_goes_at_once(env):
    client, fake, tmp_path, _ = env
    assert _report(client, include_transcript=True).status_code == 200
    assert fake.count("/api/visit/transcripts") == 0


def test_report_of_a_live_visit_is_queued(env, monkeypatch):
    client, fake, tmp_path, _ = env
    monkeypatch.setattr(tu, "is_live", lambda v: v == V1)
    assert _report(client, include_transcript=True).status_code == 202 and fake.count("/api/visit/reports") == 0


def test_second_report_while_queued_is_409(env):
    client, fake, tmp_path, _ = env
    fake.report_mode = "503"
    assert _report(client).status_code == 202
    resp = _report(client)
    assert resp.status_code == 409 and resp.json()["code"] == "already_queued"


def test_unknown_visit_is_404_and_kept_for_the_user(env):
    client, fake, tmp_path, _ = env
    fake.report_mode = "404"
    assert _report(client).status_code == 404
    # 只有受理或用户放弃才删：留在队列里、标记被拒，UI 当场给「重试 / 放弃」
    assert _queued(tmp_path)["rejected"] == "unknown_visit"
    items = client.get("/api/visit/report/queue", headers=GOOD).json()["items"]
    assert items[0]["rejected"] == "unknown_visit"
    fake.report_mode = "ok"
    resp = client.post(f"/api/visit/report/queue/{V1}", headers=GOOD, json={"action": "retry"})
    assert resp.json() == {"ok": True, "delivered": True} and _queued(tmp_path) is None


def test_report_with_an_expired_login_is_queued_and_asks_to_sign_in(env, monkeypatch):
    client, fake, tmp_path, _ = env

    async def expired():
        raise cr.VisitLoginRequired()

    monkeypatch.setattr(cr, "_servers_session", expired)
    resp = _report(client)
    assert resp.status_code == 409 and resp.json()["code"] == "VISIT_LOGIN_REQUIRED"
    assert _queued(tmp_path) is not None


def test_report_after_the_upload_still_carries_the_anomaly_count(env):
    client, fake, tmp_path, _ = env
    _write_sealed(tmp_path)
    client.portal.call(tu.retry_visit_once, V1)
    assert not (tmp_path / "visit_spool" / f"{V1}.upload.json").exists()
    assert _report(client).status_code == 200 and fake.reports[0]["anomalies"] == 4


def test_report_needs_a_login(env):
    client, fake, tmp_path, state = env
    state["account"] = None
    resp = _report(client)
    assert resp.status_code == 409 and resp.json()["code"] == "VISIT_LOGIN_REQUIRED"
    assert _queued(tmp_path) is None


def test_report_persist_failure_is_500(env, monkeypatch):
    client, *_ = env

    async def broken(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(tu, "queue_report", broken)
    resp = _report(client)
    assert resp.status_code == 500 and resp.json()["code"] == "report_persist_failed"


@pytest.mark.parametrize("body", [
    {"reason": "nope"}, {"note": "x" * 501}, {"include_transcript": "yes"}, {"include_transcript": None},
])
def test_report_validation(env, body):
    client, *_ = env
    assert _report(client, **body).status_code == 400


def test_queued_report_retry_and_abandon(env):
    client, fake, tmp_path, _ = env
    fake.report_mode = "503"
    _report(client)
    doc = _queued(tmp_path)
    doc["queued_at"] = time.time() - 8 * 86400
    (tmp_path / "visit_reports" / f"{V1}.json").write_text(json.dumps(doc), encoding="utf-8")
    items = client.get("/api/visit/report/queue", headers=GOOD).json()["items"]
    assert items[0]["visit_id"] == V1 and items[0]["stale"] is True
    resp = client.post(f"/api/visit/report/queue/{V1}", headers=GOOD, json={"action": "retry"})
    assert resp.json() == {"ok": True, "delivered": False} and _queued(tmp_path) is not None
    fake.report_mode = "ok"
    resp = client.post(f"/api/visit/report/queue/{V1}", headers=GOOD, json={"action": "retry"})
    assert resp.json() == {"ok": True, "delivered": True} and _queued(tmp_path) is None
    fake.report_mode = "503"
    _report(client, visit_id=vid(2))
    resp = client.post(f"/api/visit/report/queue/{vid(2)}", headers=GOOD, json={"action": "abandon"})
    assert resp.json() == {"ok": True, "removed": True} and _queued(tmp_path, vid(2)) is None
    assert client.post(f"/api/visit/report/queue/{vid(2)}", headers=GOOD,
                       json={"action": "abandon"}).status_code == 404


def test_queued_reports_are_scoped_to_the_signed_in_account(env):
    client, fake, tmp_path, state = env
    fake.report_mode = "503"
    _report(client)
    state["account"] = "u2"
    assert client.get("/api/visit/report/queue", headers=GOOD).json()["items"] == []
    resp = client.post(f"/api/visit/report/queue/{V1}", headers=GOOD, json={"action": "abandon"})
    assert resp.status_code == 404 and _queued(tmp_path) is not None
    state["account"] = "u1"
    assert [i["visit_id"] for i in client.get("/api/visit/report/queue", headers=GOOD).json()["items"]] == [V1]


def test_abandon_waits_for_an_in_flight_submission(env):
    import threading

    client, fake, tmp_path, _ = env
    fake.report_mode = "503"
    _report(client)
    doc = _queued(tmp_path)
    lock = tu.visit_lock(V1)
    holding, release = client.portal.call(_make_events)

    async def submit_in_flight():
        async with lock:
            holding.set()
            await release.wait()
            # 后台提交在锁内受理并删掉了文件；随后另一账号为同一场排了自己的举报
            await tu.delete_report(tmp_path, V1)
            await tu.queue_report(tmp_path, {**doc, "own_account": "u2", "own_visit_uid": "b" * 24})

    client.portal.start_task_soon(submit_in_flight)
    client.portal.call(holding.wait)
    result = {}
    worker = threading.Thread(target=lambda: result.setdefault(
        "resp", client.post(f"/api/visit/report/queue/{V1}", headers=GOOD, json={"action": "abandon"})))
    worker.start()
    try:
        worker.join(0.3)
        blocked = worker.is_alive()     # 放弃在等那次提交结束，没有抢先报 removed
    finally:
        client.portal.call(release.set)
        worker.join(5)
    assert blocked and result["resp"].status_code == 404
    assert _queued(tmp_path)["own_account"] == "u2"      # 别人的那份没被删


async def _make_events():
    import asyncio

    return asyncio.Event(), asyncio.Event()


def test_report_survives_clearing_the_person(env):
    client, fake, tmp_path, _ = env
    fake.report_mode = "503"
    _report(client)
    before = (tmp_path / "visit_reports" / f"{V1}.json").read_bytes()
    # 「清除这个人」只动名册 / spool / 记忆：举报文件与之无关（此处名册为空也照常）
    client.post("/api/visit/memory/forget", headers=GOOD, json={"catgirl": "A", "peer_uid": "1" * 24})
    assert (tmp_path / "visit_reports" / f"{V1}.json").read_bytes() == before
    assert "peer_uid" not in before.decode("utf-8")
