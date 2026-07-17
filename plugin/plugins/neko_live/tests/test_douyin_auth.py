from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from plugin.plugins.neko_live.core.runtime import RoastRuntime
from plugin.plugins.neko_live.core import runtime_douyin_auth
from plugin.plugins.neko_live.core.runtime_douyin_auth import normalize_cookie
from plugin.plugins.neko_live.modules.douyin_live_ingest.webcast import DouyinWebcastInfo


class _ConfigApi:
    async def dump(self, timeout: float = 0) -> dict:
        return {"neko_live": {}}


class _Plugin:
    def __init__(self, data_dir: Path) -> None:
        self.config = _ConfigApi()
        self.ctx = None
        self.logger = None
        self._data_dir = data_dir
        self.pushed_messages: list[dict] = []
        self.output_channel_ready = True

    def data_path(self) -> Path:
        return self._data_dir

    def push_message(self, **kwargs):
        self.pushed_messages.append(kwargs)
        return None


def _contains_secret(value: object, secret: str) -> bool:
    return secret in json.dumps(value, ensure_ascii=False, sort_keys=True)


def test_normalize_cookie_accepts_browser_cookie_header():
    cookie = normalize_cookie("Cookie: ttwid=abc;\n odin_tt=def ; extra=value")

    assert cookie == "ttwid=abc; odin_tt=def; extra=value"


def test_normalize_cookie_rejects_non_cookie_header_lines():
    with pytest.raises(ValueError, match="unsupported header"):
        normalize_cookie("Cookie: ttwid=abc\r\nX-Bad: token=must-not-leak")


def test_normalize_cookie_rejects_empty_or_non_cookie_text():
    with pytest.raises(ValueError):
        normalize_cookie("")
    with pytest.raises(ValueError):
        normalize_cookie("not-a-cookie")


def test_normalize_cookie_rejects_non_string_without_stringifying():
    class _LooksLikeCookie:
        def __str__(self) -> str:
            return "ttwid=object-secret"

    with pytest.raises(ValueError, match="cookie must be text"):
        normalize_cookie(_LooksLikeCookie())


def test_douyin_auth_documentation_tracks_manual_cookie_boundary():
    root = Path(__file__).resolve().parents[1]
    source = (root / "docs" / "development.md").read_text(encoding="utf-8")

    assert "`runtime_douyin_auth`：抖音手动 cookie action" in source
    assert "混入 `X-...:` 等非 Cookie header 行必须拒绝" in source
    assert "非法 cookie action 必须返回结构化 `saved=False` 结果" in source
    assert "只在用户手动触发校验时读取当前房间元数据" in source
    assert "网页登录、二维码/手机号登录或浏览器自动化" in source


def test_douyin_auth_has_no_network_login_or_browser_automation():
    root = Path(__file__).resolve().parents[1]
    source = (root / "core" / "runtime_douyin_auth.py").read_text(encoding="utf-8")
    forbidden_imports = {
        "requests",
        "httpx",
        "aiohttp",
        "urllib",
        "socket",
        "webbrowser",
        "selenium",
        "playwright",
        "pyppeteer",
        "requests_html",
        "undetected_chromedriver",
        "execjs",
        "quickjs",
        "js2py",
    }
    forbidden_tokens = {
        "subprocess",
        "os.system",
        "popen(",
        "eval(",
        "exec(",
        "asyncio.create_task",
        "ensure_future",
        "login_v2",
        "scan_qrcode",
        "phone",
        "sms",
    }

    imports = {
        match.group(1)
        for match in re.finditer(r"^\s*(?:from|import)\s+([a-zA-Z0-9_.]+)", source, flags=re.MULTILINE)
    }
    import_hits = sorted(
        imported
        for imported in imports
        if any(imported == token or imported.startswith(f"{token}.") for token in forbidden_imports)
    )
    token_hits = sorted(token for token in forbidden_tokens if token in source)

    assert import_hits == []
    assert token_hits == []


@pytest.mark.asyncio
async def test_douyin_cookie_import_status_and_delete_are_redacted(tmp_path):
    pytest.importorskip("cryptography")
    runtime = RoastRuntime(_Plugin(tmp_path))

    result = await runtime.douyin_cookie_import(
        "Cookie: ttwid=secret-cookie; odin_tt=hidden-token",
        uid="douyin-user",
        nickname="viewer",
    )

    assert result["platform"] == "douyin"
    assert result["saved"] is True
    assert result["logged_in"] is True
    assert result["has_cookie"] is True
    assert result["uid"] == "douyin-user"
    assert not _contains_secret(result, "secret-cookie")
    assert (tmp_path / "douyin_credential.enc").exists()
    assert (tmp_path / "douyin_credential.key").exists()
    assert not (tmp_path / "bili_credential.enc").exists()
    assert b"secret-cookie" not in (tmp_path / "douyin_credential.enc").read_bytes()

    status = await runtime.douyin_cookie_status()
    assert status["logged_in"] is True
    assert not _contains_secret(status, "hidden-token")
    assert not _contains_secret(runtime.audit.recent(10), "secret-cookie")
    assert runtime.douyin_credential is not None
    assert runtime.douyin_credential["cookie"] == "ttwid=secret-cookie; odin_tt=hidden-token"

    deleted = await runtime.douyin_cookie_delete()

    assert deleted["logged_in"] is False
    assert "douyin_credential.enc" in deleted["removed"]
    assert "douyin_credential.key" in deleted["removed"]
    assert runtime.douyin_credential is None
    assert await runtime.douyin_cookie_status() == {
        "platform": "douyin",
        "logged_in": False,
        "has_cookie": False,
        "uid": "",
        "nickname": "",
        "saved_at": "",
    }


@pytest.mark.asyncio
async def test_douyin_cookie_import_invalid_input_returns_safe_failure(tmp_path):
    pytest.importorskip("cryptography")
    runtime = RoastRuntime(_Plugin(tmp_path))

    result = await runtime.douyin_cookie_import("Cookie: ttwid=secret-cookie\r\nX-Bad: token=hidden-token")

    assert result == {
        "platform": "douyin",
        "saved": False,
        "logged_in": False,
        "has_cookie": False,
        "message": "cookie contains unsupported header lines",
    }
    assert runtime.douyin_credential is None
    assert not (tmp_path / "douyin_credential.enc").exists()
    assert not _contains_secret(result, "secret-cookie")
    assert not _contains_secret(runtime.audit.recent(10), "secret-cookie")
    assert not _contains_secret(runtime.audit.recent(10), "hidden-token")


@pytest.mark.asyncio
async def test_douyin_cookie_import_rejects_object_cookie_without_leaking_str(tmp_path):
    pytest.importorskip("cryptography")
    runtime = RoastRuntime(_Plugin(tmp_path))

    class _LooksLikeCookie:
        def __str__(self) -> str:
            return "ttwid=object-secret"

    result = await runtime.douyin_cookie_import(_LooksLikeCookie())

    assert result == {
        "platform": "douyin",
        "saved": False,
        "logged_in": False,
        "has_cookie": False,
        "message": "cookie must be text",
    }
    assert runtime.douyin_credential is None
    assert not (tmp_path / "douyin_credential.enc").exists()
    assert not _contains_secret(result, "object-secret")
    assert not _contains_secret(runtime.audit.recent(10), "object-secret")


@pytest.mark.asyncio
async def test_douyin_cookie_import_keeps_short_safe_uid_shape(tmp_path):
    pytest.importorskip("cryptography")
    runtime = RoastRuntime(_Plugin(tmp_path))

    result = await runtime.douyin_cookie_import(
        "ttwid=real-cookie",
        uid="douyin:abc_123.-",
        nickname="viewer",
    )
    status = await runtime.douyin_cookie_status()

    assert result["uid"] == "douyin:abc_123.-"
    assert status["uid"] == "douyin:abc_123.-"


@pytest.mark.asyncio
async def test_douyin_cookie_import_rejects_unsafe_uid_shape(tmp_path):
    pytest.importorskip("cryptography")
    runtime = RoastRuntime(_Plugin(tmp_path))

    result = await runtime.douyin_cookie_import(
        "ttwid=real-cookie",
        uid="../bad<script>",
        nickname="viewer",
    )
    status = await runtime.douyin_cookie_status()

    assert result["uid"] == ""
    assert status["uid"] == ""
    assert not _contains_secret(result, "bad")
    assert not _contains_secret(status, "bad")
    assert not _contains_secret(runtime.audit.recent(10), "bad")


@pytest.mark.asyncio
async def test_douyin_cookie_import_redacts_cookie_shaped_uid_and_nickname(tmp_path):
    pytest.importorskip("cryptography")
    runtime = RoastRuntime(_Plugin(tmp_path))

    result = await runtime.douyin_cookie_import(
        "ttwid=real-cookie; odin_tt=real-token",
        uid="Cookie: ttwid=uid-secret",
        nickname="odin_tt=nickname-secret",
    )
    status = await runtime.douyin_cookie_status()

    assert result["logged_in"] is True
    assert result["uid"] == ""
    assert result["nickname"] == ""
    assert status["uid"] == ""
    assert status["nickname"] == ""
    assert not _contains_secret(result, "uid-secret")
    assert not _contains_secret(status, "nickname-secret")
    assert not _contains_secret(runtime.audit.recent(10), "uid-secret")
    assert not _contains_secret(runtime.audit.recent(10), "nickname-secret")
    assert runtime.douyin_credential is not None
    assert runtime.douyin_credential["cookie"] == "ttwid=real-cookie; odin_tt=real-token"


@pytest.mark.asyncio
async def test_douyin_cookie_import_redacts_cross_platform_credential_shapes(tmp_path):
    pytest.importorskip("cryptography")
    runtime = RoastRuntime(_Plugin(tmp_path))

    result = await runtime.douyin_cookie_import(
        "ttwid=real-cookie",
        uid="SESSDATA=bili-secret",
        nickname="foo=bar; generic-secret=value",
    )

    assert result["uid"] == ""
    assert result["nickname"] == ""
    assert not _contains_secret(result, "bili-secret")
    assert not _contains_secret(runtime.audit.recent(10), "generic-secret")


@pytest.mark.asyncio
async def test_douyin_cookie_import_redacts_generic_token_shapes(tmp_path):
    pytest.importorskip("cryptography")
    runtime = RoastRuntime(_Plugin(tmp_path))

    result = await runtime.douyin_cookie_import(
        "ttwid=real-cookie",
        uid="token=uid-secret",
        nickname="viewer signature=nickname-secret",
    )
    status = await runtime.douyin_cookie_status()

    assert result["uid"] == ""
    assert result["nickname"] == ""
    assert status["uid"] == ""
    assert status["nickname"] == ""
    assert not _contains_secret(result, "uid-secret")
    assert not _contains_secret(status, "nickname-secret")
    assert not _contains_secret(runtime.audit.recent(10), "uid-secret")
    assert not _contains_secret(runtime.audit.recent(10), "nickname-secret")


@pytest.mark.asyncio
async def test_douyin_cookie_import_does_not_stringify_uid_or_nickname_objects(tmp_path):
    pytest.importorskip("cryptography")
    runtime = RoastRuntime(_Plugin(tmp_path))

    class _LooksLikePublicText:
        def __str__(self) -> str:
            return "viewer-object-secret"

    result = await runtime.douyin_cookie_import(
        "ttwid=real-cookie",
        uid=_LooksLikePublicText(),
        nickname=_LooksLikePublicText(),
    )
    status = await runtime.douyin_cookie_status()

    assert result["logged_in"] is True
    assert result["uid"] == ""
    assert result["nickname"] == ""
    assert status["uid"] == ""
    assert status["nickname"] == ""
    assert not _contains_secret(result, "viewer-object-secret")
    assert not _contains_secret(status, "viewer-object-secret")
    assert not _contains_secret(runtime.audit.recent(10), "viewer-object-secret")


@pytest.mark.asyncio
async def test_douyin_cookie_status_treats_non_string_cookie_as_logged_out(tmp_path):
    pytest.importorskip("cryptography")
    runtime = RoastRuntime(_Plugin(tmp_path))

    class _LooksLikeCookie:
        def __str__(self) -> str:
            return "ttwid=status-secret"

    runtime.douyin_credential = {"cookie": _LooksLikeCookie(), "uid": "douyin:42", "nickname": "viewer"}

    status = await runtime.douyin_cookie_status()

    assert status == {
        "platform": "douyin",
        "logged_in": False,
        "has_cookie": False,
        "uid": "",
        "nickname": "",
        "saved_at": "",
    }
    assert not _contains_secret(status, "status-secret")


@pytest.mark.asyncio
async def test_douyin_cookie_validate_fetches_room_metadata_without_leaking_cookie(monkeypatch, tmp_path):
    pytest.importorskip("cryptography")
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.douyin_credential = {"cookie": "ttwid=secret-cookie", "uid": "douyin:42", "nickname": "viewer"}
    runtime.config.live_room_ref = "https://live.douyin.com/room-42?cookie=must-not-leak"
    calls: list[dict[str, str]] = []

    def fake_fetch(room_ref: str, *, cookie: str = "", timeout: float = 8.0) -> DouyinWebcastInfo:
        calls.append({"room_ref": room_ref, "cookie": cookie, "timeout": str(timeout)})
        return DouyinWebcastInfo(
            ok=True,
            room_ref=room_ref,
            webcast_room_id="7390000000000000000",
            live_status="live",
            message="douyin room metadata found",
        )

    monkeypatch.setattr(runtime_douyin_auth, "fetch_webcast_info", fake_fetch)

    result = await runtime.douyin_cookie_validate()

    assert result["valid"] is True
    assert result["room_ref"] == "room-42"
    assert result["live_status"] == "live"
    assert calls == [{"room_ref": "room-42", "cookie": "ttwid=secret-cookie", "timeout": "8.0"}]
    assert not _contains_secret(result, "secret-cookie")
    assert not _contains_secret(result, "must-not-leak")
    assert not _contains_secret(runtime.audit.recent(10), "secret-cookie")


@pytest.mark.asyncio
async def test_douyin_cookie_validate_requires_string_cookie_without_stringifying(tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))

    class _LooksLikeCookie:
        def __str__(self) -> str:
            return "ttwid=secret-cookie"

    runtime.douyin_credential = {"cookie": _LooksLikeCookie()}
    runtime.config.live_room_ref = "room-42"

    result = await runtime.douyin_cookie_validate()

    assert result["valid"] is False
    assert result["has_cookie"] is False
    assert result["message"] == "douyin cookie is required before validation"
    assert not _contains_secret(result, "secret-cookie")
    assert not _contains_secret(runtime.audit.recent(10), "secret-cookie")


@pytest.mark.asyncio
async def test_douyin_cookie_validate_redacts_fetch_errors(monkeypatch, tmp_path):
    runtime = RoastRuntime(_Plugin(tmp_path))
    runtime.douyin_credential = {"cookie": "ttwid=secret-cookie"}

    def fake_fetch(room_ref: str, *, cookie: str = "", timeout: float = 8.0) -> DouyinWebcastInfo:
        raise RuntimeError("Cookie: ttwid=secret-cookie")

    monkeypatch.setattr(runtime_douyin_auth, "fetch_webcast_info", fake_fetch)

    result = await runtime.douyin_cookie_validate("room-42")

    assert result["valid"] is False
    assert result["message"] == "douyin cookie validation failed: RuntimeError"
    assert result["room_ref"] == "room-42"
    assert not _contains_secret(result, "secret-cookie")
    assert not _contains_secret(runtime.audit.recent(10), "secret-cookie")
