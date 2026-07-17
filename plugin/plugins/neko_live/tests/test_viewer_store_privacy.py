"""ViewerStore public persistence and privacy boundary tests."""

from __future__ import annotations

import json

import pytest

from plugin.plugins.neko_roast.core.contracts import ViewerIdentity
from plugin.plugins.neko_roast.stores.viewer_store import ViewerStore


class _FakePlugin:
    def __init__(self, data_dir):
        self._data_dir = data_dir

    def data_path(self, *parts):
        return self._data_dir.joinpath(*parts) if parts else self._data_dir


class _SecretLike:
    def __str__(self) -> str:
        return "token=must-not-leak"


@pytest.mark.asyncio
async def test_viewer_store_does_not_persist_invalid_object_uid(tmp_path):
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)

    profile = await store.upsert_identity(
        ViewerIdentity(
            uid=_SecretLike(),  # type: ignore[arg-type]
            nickname="Cookie: ttwid=must-not-leak",
            avatar_url=_SecretLike(),  # type: ignore[arg-type]
        )
    )

    assert profile.uid == ""
    assert "must-not-leak" not in json.dumps(profile.to_dict(), ensure_ascii=False)
    assert not (tmp_path / "viewer_profiles.json").exists()


@pytest.mark.asyncio
async def test_viewer_store_sanitizes_existing_json_before_public_recent_profiles(tmp_path):
    file = tmp_path / "viewer_profiles.json"
    file.write_text(
        json.dumps(
            {
                "douyin:42": {
                    "uid": "token=must-not-leak",
                    "nickname": "cookie=nick-secret",
                    "avatar_url": "signature=avatar-secret",
                    "first_seen_at": "2026-07-02T00:00:00+00:00",
                    "last_seen_at": "2026-07-02T00:01:00+00:00",
                    "roast_count": -3,
                    "last_roast_at": "authorization: bearer roast-secret",
                    "last_result": "ttwid=result-secret",
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)

    recent = await store.recent_profiles()

    rendered = json.dumps(recent, ensure_ascii=False, sort_keys=True)
    assert recent[0]["uid"] == "douyin:42"
    assert recent[0]["roast_count"] == 0
    assert "[redacted]" in rendered
    assert "must-not-leak" not in rendered
    assert "nick-secret" not in rendered
    assert "avatar-secret" not in rendered
    assert "roast-secret" not in rendered
    assert "result-secret" not in rendered


@pytest.mark.asyncio
async def test_viewer_store_mark_roasted_sanitizes_output_summary(tmp_path):
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)
    await store.upsert_identity(ViewerIdentity(uid="douyin:42", nickname="viewer"))

    await store.mark_roasted("douyin:42", "Authorization: Bearer output-secret")

    recent = await store.recent_profiles()
    rendered = json.dumps(recent, ensure_ascii=False, sort_keys=True)
    assert recent[0]["last_result"] == "[redacted]"
    assert "output-secret" not in rendered
