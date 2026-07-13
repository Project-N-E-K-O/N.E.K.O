"""CredentialStore（P5 登录态）单测：加解密往返、落盘为密文、退出登录删文件。"""

from __future__ import annotations

import pytest

from plugin.plugins.neko_roast.stores.credential_store import CredentialStore


class _FakePlugin:
    def __init__(self, data_dir):
        self._data_dir = data_dir

    def data_path(self, *parts):
        return self._data_dir.joinpath(*parts) if parts else self._data_dir


class _Audit:
    def __init__(self):
        self.events = []

    def record(self, op, message, **kwargs):
        self.events.append({"op": op, "message": message, **kwargs})


@pytest.mark.asyncio
async def test_credential_save_load_roundtrip_and_encrypted_at_rest(tmp_path):
    pytest.importorskip("cryptography")
    store = CredentialStore(_FakePlugin(tmp_path), audit=None)

    assert store.has_credential() is False
    assert await store.load() is None

    ok = await store.save(
        {"SESSDATA": "sess-secret", "bili_jct": "jct", "DedeUserID": "42", "buvid3": "buv", "extra": "drop-me"}
    )
    assert ok is True
    assert store.has_credential() is True

    data = await store.load()
    assert data["SESSDATA"] == "sess-secret"
    assert data["DedeUserID"] == "42"
    assert "extra" not in data  # 只保留已知字段

    # 落盘必须是密文：原始明文不出现在文件里
    enc_bytes = (tmp_path / "bili_credential.enc").read_bytes()
    assert b"sess-secret" not in enc_bytes


@pytest.mark.asyncio
async def test_credential_delete_removes_files(tmp_path):
    pytest.importorskip("cryptography")
    store = CredentialStore(_FakePlugin(tmp_path), audit=None)
    await store.save({"SESSDATA": "x", "bili_jct": "y", "DedeUserID": "1", "buvid3": "z"})
    assert store.has_credential() is True

    removed = await store.delete()

    assert "bili_credential.enc" in removed
    assert "bili_credential.key" in removed
    assert store.has_credential() is False
    assert await store.load() is None


@pytest.mark.asyncio
async def test_namespaced_credentials_are_isolated(tmp_path):
    pytest.importorskip("cryptography")
    plugin = _FakePlugin(tmp_path)
    audit = _Audit()
    bili = CredentialStore(plugin, audit=None)
    douyin = CredentialStore(
        plugin,
        audit=audit,
        namespace="douyin",
        fields=("sessionid", "ttwid", "uid"),
    )

    assert await bili.save({"SESSDATA": "bili-secret"}) is True
    assert await douyin.save(
        {"sessionid": "douyin-secret", "ttwid": "tw", "uid": "douyin-user"}
    ) is True

    assert (await bili.load())["SESSDATA"] == "bili-secret"
    assert (await douyin.load())["sessionid"] == "douyin-secret"
    assert (tmp_path / "bili_credential.enc").exists()
    assert (tmp_path / "douyin_credential.enc").exists()
    assert audit.events[-1]["detail"]["uid"] == "douyin-user"
    assert audit.events[-1]["level"] == "info"


@pytest.mark.asyncio
async def test_credential_save_marks_missing_audit_identity_as_unidentified(tmp_path):
    pytest.importorskip("cryptography")
    audit = _Audit()
    store = CredentialStore(
        _FakePlugin(tmp_path),
        audit=audit,
        namespace="douyin",
        fields=("cookie", "uid"),
    )

    assert await store.save({"cookie": "ttwid=secret-cookie", "uid": "  "}) is True
    assert (await store.load())["cookie"] == "ttwid=secret-cookie"
    assert audit.events[-1]["op"] == "douyin_credential_saved"
    assert audit.events[-1]["level"] == "warning"
    assert audit.events[-1]["detail"] == {"identity_status": "unidentified"}
    assert "secret-cookie" not in str(audit.events[-1])


def test_invalid_credential_namespace_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        CredentialStore(_FakePlugin(tmp_path), namespace="bili!!")


@pytest.mark.asyncio
async def test_credential_store_namespace_keeps_douyin_cookie_separate(tmp_path):
    pytest.importorskip("cryptography")
    store = CredentialStore(
        _FakePlugin(tmp_path),
        audit=None,
        namespace="douyin",
        fields=("cookie", "uid", "nickname", "saved_at"),
    )

    ok = await store.save(
        {
            "cookie": "ttwid=secret-cookie; odin_tt=hidden",
            "uid": "42",
            "nickname": "dy-user",
            "saved_at": "now",
            "SESSDATA": "drop-me",
        }
    )

    assert ok is True
    assert store.has_credential() is True
    assert (tmp_path / "douyin_credential.enc").exists()
    assert (tmp_path / "douyin_credential.key").exists()
    assert not (tmp_path / "bili_credential.enc").exists()
    data = await store.load()
    assert data == {
        "cookie": "ttwid=secret-cookie; odin_tt=hidden",
        "uid": "42",
        "nickname": "dy-user",
        "saved_at": "now",
    }
    assert b"secret-cookie" not in (tmp_path / "douyin_credential.enc").read_bytes()
