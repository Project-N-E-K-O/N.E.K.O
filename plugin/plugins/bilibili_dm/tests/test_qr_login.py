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
    assert start["session_id"]
    assert await login.poll() == {"status": "waiting", "message": "等待扫码…"}

    done = await login.poll()
    assert done == {"status": "done", "message": "登录成功，配置已自动保存", "has_buvid3": True}
    assert saved["sesdata"] == "session-secret"
    assert saved["ac_time_value"] == "new-refresh-token"
    assert "session-secret" not in str(done)
    assert "new-refresh-token" not in str(done)
    assert login._session is None


def test_qr_login_only_clears_the_session_matched_by_cancel_request():
    login = BiliDMQrLogin(credential_saver=lambda _: None)
    current_session = object()
    login._session = current_session
    login._session_id = "current-session"

    assert login.clear(session_id="old-session") is False
    assert login._session is current_session
    assert login.clear(session_id="current-session") is True
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
    login._session_id = "session-id"
    login._require_sdk = lambda: (object, FakeEvents)

    assert (await login.poll())["status"] == "done"
    assert saved["ac_time_value"] == ""


@pytest.mark.asyncio
async def test_qr_login_maps_scan_confirm_and_done_states():
    saved: dict[str, str] = {}
    credential = SimpleNamespace(
        sessdata="session-secret",
        bili_jct="csrf-secret",
        buvid3="",
        dedeuserid="42",
    )
    session = FakeSession([FakeEvents.SCAN, FakeEvents.CONF, FakeEvents.DONE], credential)

    async def save(values: dict[str, str]) -> bool:
        saved.update(values)
        return True

    login = BiliDMQrLogin(credential_saver=save)
    login._session = session
    login._session_id = "session-id"
    login._require_sdk = lambda: (object, FakeEvents)

    assert await login.poll_session("session-id") == {"status": "waiting", "message": "等待扫码…"}
    assert await login.poll_session("session-id") == {"status": "scanned", "message": "已扫码，请在手机上确认…"}
    assert (await login.poll_session("session-id"))["status"] == "done"
    assert saved["sesdata"] == "session-secret"


@pytest.mark.asyncio
async def test_qr_login_clears_completed_session_when_credential_save_fails():
    credential = SimpleNamespace(
        sessdata="session-secret",
        bili_jct="csrf-secret",
        buvid3="",
        dedeuserid="42",
    )
    async def save(_):
        return False

    login = BiliDMQrLogin(credential_saver=save)
    login._session = FakeSession([FakeEvents.DONE], credential)
    login._session_id = "session-id"
    login._require_sdk = lambda: (object, FakeEvents)

    with pytest.raises(RuntimeError, match="保存插件配置失败"):
        await login.poll_session("session-id")
    assert login._session is None


@pytest.mark.asyncio
async def test_qr_login_clears_completed_session_when_credential_save_raises():
    credential = SimpleNamespace(
        sessdata="session-secret",
        bili_jct="csrf-secret",
        buvid3="",
        dedeuserid="42",
    )

    async def save(_):
        raise OSError("disk full")

    login = BiliDMQrLogin(credential_saver=save)
    login._session = FakeSession([FakeEvents.DONE], credential)
    login._session_id = "session-id"
    login._require_sdk = lambda: (object, FakeEvents)

    with pytest.raises(OSError, match="disk full"):
        await login.poll_session("session-id")
    assert login._session is None


@pytest.mark.asyncio
async def test_qr_login_rejects_poll_for_a_replaced_session():
    async def save(_):
        return False

    login = BiliDMQrLogin(credential_saver=save)
    login._session = FakeSession([FakeEvents.DONE])
    login._session_id = "current-session"

    assert await login.poll_session("old-session") == {
        "status": "no_session",
        "message": "没有进行中的登录，请重新获取二维码",
    }


@pytest.mark.asyncio
async def test_qr_login_reports_expiry_and_clears_the_session():
    login = BiliDMQrLogin(credential_saver=lambda _: None)
    session = FakeSession([FakeEvents.TIMEOUT])
    login._session = session
    login._session_id = "session-id"
    login._require_sdk = lambda: (object, FakeEvents)

    assert await login.poll() == {"status": "expired", "message": "二维码已过期，请刷新二维码"}
    assert login._session is None
