"""CredentialStore tests: encryption roundtrip, deletion, and namespace isolation."""

from __future__ import annotations

import pytest

from plugin.plugins.neko_roast.stores.credential_store import CredentialStore


class _FakePlugin:
    def __init__(self, data_dir):
        self._data_dir = data_dir

    def data_path(self, *parts):
        return self._data_dir.joinpath(*parts) if parts else self._data_dir


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


@pytest.mark.asyncio
async def test_credential_store_namespace_is_sanitized_for_file_names(tmp_path):
    pytest.importorskip("cryptography")
    store = CredentialStore(
        _FakePlugin(tmp_path),
        audit=None,
        namespace="../douyin/../../evil",
        fields=("cookie",),
    )

    ok = await store.save({"cookie": "ttwid=secret-cookie"})

    assert ok is True
    assert store.namespace == "douyinevil"
    assert (tmp_path / "douyinevil_credential.enc").exists()
    assert (tmp_path / "douyinevil_credential.key").exists()
    assert not any(path.name == "evil" and path.is_dir() for path in tmp_path.rglob("*"))
    assert b"secret-cookie" not in (tmp_path / "douyinevil_credential.enc").read_bytes()
