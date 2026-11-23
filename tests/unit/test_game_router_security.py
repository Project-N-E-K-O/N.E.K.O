# -*- coding: utf-8 -*-
"""Security tests for game_router URL escaping, character-name validation, and CSRF guards (F0017, F0018, F0019)."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from main_routers.system_router import _shared as system_shared


def _csrf_token() -> str:
    return system_shared.AUTOSTART_CSRF_TOKEN


pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# F0017: URL quoting and lanlan_name validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_archive_and_pregame_quote_lanlan_name_in_memory_urls(monkeypatch):
    from main_routers.game_router import archive as gr_archive
    from main_routers.game_router import pregame as gr_pregame

    captured_urls: list[str] = []

    class _FakeResponse:
        is_success = True
        status_code = 200
        content = b'{"status": "ok"}'
        text = "history"

        def json(self):
            return {"status": "ok"}

    class _FakeClient:
        async def post(self, url, **_kwargs):
            captured_urls.append(url)
            return _FakeResponse()

        async def get(self, url, **_kwargs):
            captured_urls.append(url)
            return _FakeResponse()

    import utils.internal_http_client as ihc
    monkeypatch.setattr(ihc, "get_internal_http_client", lambda: _FakeClient())
    monkeypatch.setattr(gr_archive, "_ensure_game_archive_memory_highlights", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        gr_archive,
        "_build_game_archive_memory_messages",
        lambda _arch: [{"role": "user", "content": "match summary"}],
    )

    result = await gr_archive._submit_game_archive_to_memory(
        archive={
            "lanlan_name": "../reflect/evil?x=1",
            "game_type": "soccer",
            "session_id": "s1",
            "soccer_game_memory_enabled": True,
            "soccer_game_memory_archive_enabled": True,
        },
    )
    assert result["ok"] is True
    await gr_pregame._fetch_recent_history_for_pregame("../reflect/evil#frag")

    assert len(captured_urls) == 2
    assert "/cache/..%2Freflect%2Fevil%3Fx%3D1" in captured_urls[0]
    assert "/get_recent_history/..%2Freflect%2Fevil%23frag" in captured_urls[1]


@pytest.mark.parametrize("bad_name", ["../evil", "a/b", "a\\b", "name?x=1", "name#frag"])
def test_route_start_and_drawing_guess_round_start_reject_invalid_lanlan_name(bad_name):
    from main_routers import game_router
    from main_routers.game_router import drawing_guess as dg_router

    app = FastAPI()
    app.include_router(game_router.router)
    app.include_router(dg_router.router)
    headers = {"Origin": "http://testserver", "X-CSRF-Token": _csrf_token()}
    with TestClient(app, headers=headers) as client:
        r1 = client.post(
            "/api/game/soccer/route/start",
            json={"lanlan_name": bad_name, "session_id": "s1"},
        )
        assert r1.status_code == 200
        assert r1.json() == {"ok": False, "reason": "invalid_lanlan_name"}

        r2 = client.post(
            "/api/game/drawing_guess/round/start",
            json={"lanlan_name": bad_name, "session_id": "s1"},
        )
        assert r2.status_code == 200
        assert r2.json() == {"ok": False, "reason": "invalid_lanlan_name"}


# ---------------------------------------------------------------------------
# F0018 & F0019: CSRF / Origin / Content-Type guards on all game POST routes
# ---------------------------------------------------------------------------

_GAME_POST_ENDPOINTS = [
    "/api/game/drawing_guess/round/start",
    "/api/game/drawing_guess/ai-draw",
    "/api/game/drawing_guess/ai-draw/review",
    "/api/game/drawing_guess/input",
    "/api/game/drawing_guess/live",
    "/api/game/drawing_guess/choose-word",
    "/api/game/drawing_guess/timeout",
    "/api/game/drawing_guess/vision-guess",
    "/api/game/soccer/chat",
    "/api/game/soccer/passive-guard",
    "/api/game/soccer/route/start",
    "/api/game/soccer/route/drain",
    "/api/game/soccer/protocol",
    "/api/game/soccer/context/read",
    "/api/game/soccer/memory/submit",
    "/api/game/soccer/route/voice-transcript",
    "/api/game/soccer/route/heartbeat",
    "/api/game/soccer/route/end",
    "/api/game/soccer/mirror-assistant",
    "/api/game/soccer/speak",
    "/api/game/soccer/speech/preload",
    "/api/game/soccer/realtime-context",
    "/api/game/soccer/end",
    "/api/game/soccer/quick-lines",
    "/api/game/badminton/leaderboard",
]


@pytest.mark.parametrize("endpoint", _GAME_POST_ENDPOINTS)
def test_all_game_mutation_endpoints_enforce_csrf_and_json_content_type(endpoint):
    from main_routers import game_router
    from main_routers.game_router import drawing_guess as dg_router

    app = FastAPI()
    app.include_router(game_router.router)
    app.include_router(dg_router.router)
    with TestClient(app) as client:
        r_unauth = client.post(endpoint, json={"session_id": "s1"})
        assert r_unauth.status_code == 403, f"{endpoint}: {r_unauth.text}"
        assert r_unauth.json()["error_code"] == "csrf_validation_failed"

        r_evil = client.post(
            endpoint,
            json={"session_id": "s1"},
            headers={"Origin": "https://evil.example", "X-CSRF-Token": _csrf_token()},
        )
        assert r_evil.status_code == 403

        r_wrong = client.post(
            endpoint,
            json={"session_id": "s1"},
            headers={"Origin": "http://testserver", "X-CSRF-Token": "wrong-token"},
        )
        assert r_wrong.status_code == 403

        r_plain = client.post(
            endpoint,
            content='{"session_id": "s1"}',
            headers={
                "Content-Type": "text/plain",
                "Origin": "http://testserver",
                "X-CSRF-Token": _csrf_token(),
            },
        )
        assert r_plain.status_code == 415


def test_game_route_end_accepts_beacon_body_csrf_token_fallback(monkeypatch):
    from main_routers import game_router
    from main_routers.game_router import runtime as gr_runtime

    monkeypatch.setattr(
        gr_runtime,
        "_complete_game_end_from_payload",
        AsyncMock(return_value={"ok": True, "closed": True}),
    )
    app = FastAPI()
    app.include_router(game_router.router)
    with TestClient(app) as client:
        resp = client.post(
            "/api/game/soccer/route/end",
            json={"session_id": "s1", "_csrf_token": _csrf_token()},
            headers={"Origin": "http://testserver"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True, "closed": True}
