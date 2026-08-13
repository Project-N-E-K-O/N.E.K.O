from __future__ import annotations

from types import SimpleNamespace

import pytest

from plugin.plugins.bilibili_dm.qr_login import BiliDMQrLogin


class FakeEvents:
    NONE = "none"
    SCAN = "scan"
    CONF = "confirm"
    TIMEOUT = "timeout"
    DONE = "done"


class FakeSession:
    def __init__(self, states: list[str], credential=None):
        self.states = states
        self.credential = credential
        self.generated = False

    async def generate_qrcode(self):
        self.generated = True

    def get_qrcode_picture(self):
        return SimpleNamespace(content=b"png-bytes")

    async def check_state(self):
        return self.states.pop(0)

    def get_credential(self):
        return self.credential


@pytest.mark.asyncio
async def test_qr_login_returns_image_and_saves_credentials_without_returning_them():
    saved: dict[str, str] = {}
    credential = SimpleNamespace(
        sessdata="session-secret",
        bili_jct="csrf-secret",
        buvid3="buvid-secret",
        dedeuserid="42",
        ac_time_value="new-refresh-token",
    )
    session = FakeSession([FakeEvents.NONE, FakeEvents.DONE], credential)

    async def save(values: dict[str, str]) -> bool:
        saved.update(values)
        return True

    login = BiliDMQrLogin(credential_saver=save)
    login._require_sdk = lambda: (lambda: session, FakeEvents)

    start = await login.start()
    assert start["status"] == "qrcode_ready"
    assert start["qrcode_image"] == "data:image/png;base64,cG5nLWJ5dGVz"
    assert await login.poll() == {"status": "waiting", "message": "等待扫码…"}

    done = await login.poll()
    assert done == {"status": "done", "message": "登录成功，配置已自动保存", "has_buvid3": True}
    assert saved["sesdata"] == "session-secret"
    assert saved["ac_time_value"] == "new-refresh-token"
    assert "session-secret" not in str(done)
    assert "new-refresh-token" not in str(done)
    assert login._session is None


@pytest.mark.asyncio
async def test_qr_login_clears_an_old_refresh_token_when_sdk_does_not_provide_one():
    saved = {"ac_time_value": "old-refresh-token"}
    credential = SimpleNamespace(
        sessdata="session-secret",
        bili_jct="csrf-secret",
        buvid3="",
        dedeuserid="42",
    )
    session = FakeSession([FakeEvents.DONE], credential)

    async def save(values: dict[str, str]) -> bool:
        saved.update(values)
        return True

    login = BiliDMQrLogin(credential_saver=save)
    login._session = session
    login._require_sdk = lambda: (object, FakeEvents)

    assert (await login.poll())["status"] == "done"
    assert saved["ac_time_value"] == ""


@pytest.mark.asyncio
async def test_qr_login_reports_expiry_and_clears_the_session():
    login = BiliDMQrLogin(credential_saver=lambda _: None)
    session = FakeSession([FakeEvents.TIMEOUT])
    login._session = session
    login._require_sdk = lambda: (object, FakeEvents)

    assert await login.poll() == {"status": "expired", "message": "二维码已过期，请刷新二维码"}
    assert login._session is None
