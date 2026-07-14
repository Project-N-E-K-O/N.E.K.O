"""Unit tests for Steam community-login PKCE (code_verifier / code_challenge).

Covers the NEKO side only: pending-marker verifier persist/load and authorize
URL assembly with ``code_challenge``. Cloud verification lives in
N.E.K.O.Servers ``tests/test_oauth_native.py``; full Steam OpenID round-trips
are e2e.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import stat

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import main_routers.card_drop_router as C


@pytest.fixture
def pending_path(tmp_path, monkeypatch):
    """Redirect the pending marker to a tmp file (avoid the real Documents tree)."""
    p = tmp_path / "community_steam_pending.json"
    monkeypatch.setattr(C, "_steam_pending_path", lambda: p)
    return p


def _challenge_for(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


@pytest.mark.unit
def test_pkce_pair_is_s256(pending_path):
    verifier, challenge = C._pkce_pair()
    # token_urlsafe(32) → 43 字符；S256 challenge 也恒为 43 字符（sha256=32 字节）
    assert len(verifier) == 43
    assert challenge == _challenge_for(verifier)
    assert len(challenge) == 43


@pytest.mark.unit
def test_mark_persists_verifier_and_returns_challenge(pending_path):
    out = C._mark_steam_pending()
    assert out is not None
    state, challenge = out
    data = json.loads(pending_path.read_text(encoding="utf-8"))
    assert data["state"] == state
    # verifier 落盘、challenge 不落盘（只随 URL 出门）；challenge 必须是落盘 verifier 的 S256
    assert "code_verifier" in data and isinstance(data["code_verifier"], str)
    assert "code_challenge" not in data
    assert challenge == _challenge_for(data["code_verifier"])
    if os.name != "nt":
        assert stat.S_IMODE(pending_path.stat().st_mode) == 0o600


@pytest.mark.unit
def test_consume_returns_verifier_on_match(pending_path):
    state, _challenge = C._mark_steam_pending()
    verifier = json.loads(pending_path.read_text(encoding="utf-8"))["code_verifier"]
    ok, got = C._consume_steam_pending(state)
    assert ok is True
    assert got == verifier
    assert not pending_path.exists()  # 一次性：消费即删


@pytest.mark.unit
def test_consume_wrong_state_keeps_file_and_returns_none(pending_path):
    C._mark_steam_pending()
    ok, got = C._consume_steam_pending("not-the-real-state")
    assert ok is False
    assert got is None
    assert pending_path.exists()  # state 不匹配：保留标记，合法回调仍可在 TTL 内成功


@pytest.mark.unit
def test_consume_backward_compat_no_verifier(pending_path):
    # 老 pending（无 code_verifier 字段）：仍校验 state，但 verifier 取回 None（云端不强制 PKCE）
    pending_path.write_text(
        json.dumps({"ts": __import__("time").time(), "state": "legacy-state-token"}),
        encoding="utf-8",
    )
    ok, got = C._consume_steam_pending("legacy-state-token")
    assert ok is True
    assert got is None
    assert not pending_path.exists()


@pytest.mark.unit
def test_steam_login_url_carries_code_challenge(pending_path):
    app = FastAPI()
    app.include_router(C.router)
    resp = TestClient(app).get("/api/card-drop/steam-login")
    assert resp.status_code == 200
    url = resp.json()["authorize_url"]
    # authorize URL must carry redirect_to + state + code_challenge (+ S256 method)
    assert "code_challenge=" in url
    assert "code_challenge_method=S256" in url
    assert "state=" in url
    assert "redirect_to=" in url
    # URL 里的 code_challenge 必须等于落盘 verifier 的 S256（端到端绑定）
    data = json.loads(pending_path.read_text(encoding="utf-8"))
    expected = _challenge_for(data["code_verifier"])
    assert f"code_challenge={expected}" in url


@pytest.mark.unit
def test_steam_login_rejects_cross_site_before_replacing_pending(pending_path):
    original = C._mark_steam_pending()
    assert original is not None
    original_data = pending_path.read_text(encoding="utf-8")
    app = FastAPI()
    app.include_router(C.router)
    client = TestClient(app, base_url="http://localhost:48911")

    evil_origin = client.get(
        "/api/card-drop/steam-login",
        headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
    )
    blind_navigation = client.get(
        "/api/card-drop/steam-login",
        headers={"Sec-Fetch-Site": "cross-site"},
    )

    assert evil_origin.status_code == 403
    assert evil_origin.json() == {"detail": "origin_not_allowed"}
    assert blind_navigation.status_code == 403
    assert blind_navigation.json() == {"detail": "origin_not_allowed"}
    assert pending_path.read_text(encoding="utf-8") == original_data

    same_origin = client.get(
        "/api/card-drop/steam-login",
        headers={
            "Origin": "http://localhost:48911",
            "Sec-Fetch-Site": "same-origin",
        },
    )
    assert same_origin.status_code == 200
    assert pending_path.read_text(encoding="utf-8") != original_data
