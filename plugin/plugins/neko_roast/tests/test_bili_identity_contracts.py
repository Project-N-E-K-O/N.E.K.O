from types import SimpleNamespace

import pytest

from plugin.plugins.neko_roast.adapters.bili_auth_service import BiliAuthService
from plugin.plugins.neko_roast.core.contracts import ViewerEvent
from plugin.plugins.neko_roast.modules.bili_identity import BiliIdentityModule

@pytest.mark.asyncio
async def test_bili_login_check_none_state_stays_waiting():
    class Events:
        NONE = object()
        SCAN = object()
        CONF = object()
        TIMEOUT = object()
        DONE = object()

    class Session:
        async def check_state(self):
            return Events.NONE

    service = BiliAuthService(
        credential_provider=lambda: None,
        credential_saver=lambda _payload: True,
        credential_reloader=lambda: None,
    )
    service._login_session = Session()
    service._login_generated_at = 0.0
    service._require_login_sdk = lambda: (object, Events)

    result = await service.login_check()

    assert result["status"] == "waiting"

@pytest.mark.asyncio
async def test_bili_login_check_clears_session_when_credential_save_fails():
    class Events:
        NONE = object()
        SCAN = object()
        CONF = object()
        TIMEOUT = object()
        DONE = object()

    class Credential:
        sessdata = "sess"
        bili_jct = "jct"
        dedeuserid = "42"
        buvid3 = "buvid"

    class Session:
        async def check_state(self):
            return Events.DONE

        def get_credential(self):
            return Credential()

    cleanup_calls = 0

    async def save_fails(_payload):
        return False

    async def no_credential():
        return None

    async def reload_unused():
        raise AssertionError("credential reload should not run after save failure")

    def cleanup():
        nonlocal cleanup_calls
        cleanup_calls += 1

    service = BiliAuthService(
        credential_provider=no_credential,
        credential_saver=save_fails,
        credential_reloader=reload_unused,
        cleanup_callback=cleanup,
    )
    service._login_session = Session()
    service._require_login_sdk = lambda: (object, Events)

    with pytest.raises(RuntimeError):
        await service.login_check()

    assert service._login_session is None
    assert cleanup_calls == 1

@pytest.mark.asyncio
async def test_bili_identity_avatar_fetch_tolerates_ctx_release():
    module = BiliIdentityModule()

    class Cache:
        def get(self, _key):
            return None

        def put(self, _key, _data, _mime):
            raise AssertionError("cache should not be accessed after ctx release")

    module.ctx = SimpleNamespace(
        avatar_cache=Cache(),
        config=SimpleNamespace(avatar_fetch_timeout_seconds=1),
        audit=SimpleNamespace(record=lambda *args, **kwargs: None),
    )

    def _fetch_avatar(_url, _timeout):
        module.ctx = None
        return b"avatar", "image/png"

    module._fetch_avatar = _fetch_avatar
    module._inspect_avatar = lambda _data: (True, False)

    identity = await module.resolve(ViewerEvent(uid="7", nickname="七", avatar_url="https://example.test/a.png"))

    assert identity.avatar_bytes == b"avatar"
    assert identity.avatar_mime == "image/png"

@pytest.mark.asyncio
async def test_bili_identity_ignores_undecodable_avatar_bytes():
    module = BiliIdentityModule()
    module.ctx = SimpleNamespace(
        avatar_cache=SimpleNamespace(get=lambda _key: None, put=lambda *_args: None),
        config=SimpleNamespace(avatar_fetch_timeout_seconds=1),
        audit=SimpleNamespace(record=lambda *args, **kwargs: None),
    )
    module._fetch_avatar = lambda _url, _timeout: (b"<html>not image</html>", "text/html")

    identity = await module.resolve(ViewerEvent(uid="7", nickname="viewer", avatar_url="https://example.test/a.png"))

    assert identity.avatar_bytes is None
    assert identity.avatar_vision_ok is False
    assert "avatar_fetch_failed: ValueError" in identity.error

def test_bili_identity_rejects_private_avatar_url():
    with pytest.raises(ValueError):
        BiliIdentityModule._fetch_avatar("http://127.0.0.1/avatar.png", timeout=1)

def test_bili_identity_avatar_fetch_uses_validated_resolved_ip(monkeypatch):
    opened = {}

    def fake_getaddrinfo(host, port, type=0):
        assert host == "cdn.example.test"
        assert port == 8443
        return [(None, None, None, "", ("8.8.8.8", port))]

    class Response:
        status = 200

        def read(self, _limit):
            return b"png"

        def getheader(self, name):
            return "image/png" if name == "content-type" else ""

    class Connection:
        def request(self, method, path, headers):
            opened["method"] = method
            opened["path"] = path
            opened["host"] = headers["Host"]

        def getresponse(self):
            return Response()

        def close(self):
            opened["closed"] = True

    def fake_open(parsed, resolved_ip, port, timeout):
        opened["hostname"] = parsed.hostname
        opened["resolved_ip"] = resolved_ip
        opened["port"] = port
        opened["timeout"] = timeout
        return Connection()

    monkeypatch.setattr("plugin.plugins.neko_roast.modules.bili_identity.socket.getaddrinfo", fake_getaddrinfo)
    monkeypatch.setattr(BiliIdentityModule, "_open_avatar_connection", staticmethod(fake_open))

    data, mime = BiliIdentityModule._fetch_avatar("https://cdn.example.test:8443/avatar.png?size=small", timeout=3)

    assert data == b"png"
    assert mime == "image/png"
    assert opened == {
        "hostname": "cdn.example.test",
        "resolved_ip": "8.8.8.8",
        "port": 8443,
        "timeout": 3,
        "method": "GET",
        "path": "/avatar.png?size=small",
        "host": "cdn.example.test:8443",
        "closed": True,
    }
