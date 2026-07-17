from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json

import pytest

from plugin.plugins.neko_live.core.contracts import ViewerIdentity
from plugin.plugins.neko_live.stores.viewer_store import ViewerStore


class _FakePlugin:
    def __init__(self, data_dir):
        self._data_dir = data_dir

    def data_path(self, *parts):
        return self._data_dir.joinpath(*parts) if parts else self._data_dir


@pytest.mark.asyncio
async def test_clear_profiles_resets_current_store_file(tmp_path):
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)
    await store.upsert_identity(ViewerIdentity(uid="1001", nickname="viewer"))
    await store.mark_roasted("1001", "first roast")

    result = await store.clear_profiles()

    assert result["cleared"] == 1
    assert result["path"] == str(tmp_path / "viewer_profiles.json")
    assert await store.recent_profiles() == []
    assert await store.has_roasted("1001") is False
    data = json.loads((tmp_path / "viewer_profiles.json").read_text(encoding="utf-8"))
    assert data == {}


@pytest.mark.asyncio
async def test_clear_profiles_rejects_active_fallback_store(tmp_path, monkeypatch):
    custom = tmp_path / "custom_here"
    default = tmp_path / "default"
    store = ViewerStore(_FakePlugin(default), audit=None, dir_provider=lambda: str(custom))
    original_write_json = store._write_json

    def _fail_custom(file, profiles):
        if file.parent == custom:
            return False
        return original_write_json(file, profiles)

    monkeypatch.setattr(store, "_write_json", _fail_custom)

    await store.upsert_identity(ViewerIdentity(uid="8", nickname="fallback viewer"))
    fallback_file = default / "viewer_profiles.json"
    assert fallback_file.exists()

    result = await store.clear_profiles()

    assert result["cleared"] == 0
    assert result["applied"] is False
    assert result["path"] == str(fallback_file)
    assert fallback_file.exists()
    restarted = ViewerStore(
        _FakePlugin(default), audit=None, dir_provider=lambda: str(custom)
    )
    assert (await restarted.recent_profiles())[0]["uid"] == "8"


@pytest.mark.asyncio
async def test_delete_profile_removes_only_target_uid(tmp_path):
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)
    await store.upsert_identity(ViewerIdentity(uid="1001", nickname="target"))
    await store.upsert_identity(ViewerIdentity(uid="1002", nickname="keeper"))

    result = await store.delete_profile("1001")

    assert result["uid"] == "1001"
    assert result["deleted"] is True
    recent = await store.recent_profiles()
    assert [item["uid"] for item in recent] == ["1002"]
    data = json.loads((tmp_path / "viewer_profiles.json").read_text(encoding="utf-8"))
    assert sorted(data) == ["1002"]


@pytest.mark.asyncio
async def test_reset_profile_impression_preserves_first_appearance_state(tmp_path):
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)
    identity = ViewerIdentity(uid="1001", nickname="viewer")
    await store.record_live_danmaku(identity, "AI plugin config?")
    await store.mark_roasted("1001", "first roast")

    result = await store.reset_profile_impression("1001")

    assert result["uid"] == "1001"
    assert result["reset"] is True
    assert result["preserved_first_appearance"] is True
    data = json.loads((tmp_path / "viewer_profiles.json").read_text(encoding="utf-8"))
    stored = data["1001"]
    assert stored["roast_count"] == 1
    assert stored["last_result"] == "first roast"
    assert stored["danmaku_count"] == 1
    assert stored["preference_tags"] == {}
    assert stored["favorite_topics"] == {}
    assert stored["running_jokes"] == {}
    assert stored["impression_summary"] == ""
    assert stored["avoid_guidance"] == ""


@pytest.mark.asyncio
async def test_reset_profile_impression_reports_missing_uid_without_creating_profile(tmp_path):
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)

    result = await store.reset_profile_impression("missing")

    assert result["uid"] == "missing"
    assert result["reset"] is False
    assert result["preserved_first_appearance"] is False
    assert await store.recent_profiles() == []


@pytest.mark.asyncio
async def test_disabled_viewer_memory_keeps_basic_profile_without_learning_preferences(tmp_path):
    memory_enabled = False
    store = ViewerStore(
        _FakePlugin(tmp_path),
        audit=None,
        memory_enabled_provider=lambda: memory_enabled,
    )
    identity = ViewerIdentity(uid="1001", nickname="viewer")

    profile = await store.record_live_danmaku(identity, "AI plugin config?")
    await store.mark_roasted("1001", "first roast")

    assert profile.danmaku_count == 1
    assert profile.preference_tags == {}
    assert profile.favorite_topics == {}
    assert profile.running_jokes == {}
    assert profile.impression_summary == ""
    assert await store.has_roasted("1001") is True


@pytest.mark.asyncio
async def test_disabling_viewer_memory_preserves_existing_impression_without_extending_it(tmp_path):
    memory_enabled = True
    store = ViewerStore(
        _FakePlugin(tmp_path),
        audit=None,
        memory_enabled_provider=lambda: memory_enabled,
    )
    identity = ViewerIdentity(uid="1001", nickname="viewer")
    learned = await store.record_live_danmaku(identity, "AI plugin config?")
    memory_enabled = False

    unchanged = await store.record_live_danmaku(identity, "bgm sounds good")

    assert unchanged.danmaku_count == learned.danmaku_count + 1
    assert unchanged.preference_tags == learned.preference_tags
    assert unchanged.favorite_topics == learned.favorite_topics
    assert unchanged.running_jokes == learned.running_jokes
    assert unchanged.impression_summary == learned.impression_summary


@pytest.mark.asyncio
async def test_prune_expired_profiles_deletes_only_profiles_older_than_ninety_days(tmp_path):
    old_at = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
    recent_at = (datetime.now(timezone.utc) - timedelta(days=89)).isoformat()
    profile_file = tmp_path / "viewer_profiles.json"
    profile_file.write_text(
        json.dumps(
            {
                "old": {"uid": "old", "nickname": "old", "last_seen_at": old_at},
                "recent": {
                    "uid": "recent",
                    "nickname": "recent",
                    "last_seen_at": recent_at,
                },
            }
        ),
        encoding="utf-8",
    )
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)

    assert [item["uid"] for item in await store.recent_profiles()] == ["recent"]

    result = await store.prune_expired_profiles()

    assert result == {"pruned": 1, "applied": True, "retention_days": 90}
    persisted = json.loads(profile_file.read_text(encoding="utf-8"))
    assert list(persisted) == ["recent"]


@pytest.mark.asyncio
async def test_retention_uses_most_recent_valid_activity_timestamp(tmp_path):
    old_at = (datetime.now(timezone.utc) - timedelta(days=91)).isoformat()
    recent_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    profile_file = tmp_path / "viewer_profiles.json"
    profile_file.write_text(
        json.dumps(
            {
                "1001": {
                    "uid": "1001",
                    "nickname": "viewer",
                    "last_interaction_at": old_at,
                    "last_seen_at": recent_at,
                }
            }
        ),
        encoding="utf-8",
    )
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)

    result = await store.prune_expired_profiles()

    assert result["pruned"] == 0
    assert [item["uid"] for item in await store.recent_profiles()] == ["1001"]
