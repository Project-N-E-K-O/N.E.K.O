"""ViewerStore（本地 JSON 持久化）单测：落盘往返、自定义目录、计数、audit=None 容错。"""

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


@pytest.mark.asyncio
async def test_persists_to_json_in_default_dir(tmp_path):
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)
    await store.upsert_identity(ViewerIdentity(uid="1001", nickname="桃子"))

    file = tmp_path / "viewer_profiles.json"
    assert file.exists()
    data = json.loads(file.read_text(encoding="utf-8"))
    assert data["1001"]["nickname"] == "桃子"

    # 新实例从盘上读回 → 持久化生效（重启不丢）
    store2 = ViewerStore(_FakePlugin(tmp_path), audit=None)
    recent = await store2.recent_profiles()
    assert any(p["uid"] == "1001" for p in recent)


@pytest.mark.asyncio
async def test_custom_dir_is_used(tmp_path):
    custom = tmp_path / "custom_here"
    store = ViewerStore(_FakePlugin(tmp_path / "default"), audit=None, dir_provider=lambda: str(custom))
    await store.upsert_identity(ViewerIdentity(uid="42", nickname="阿四二"))

    assert (custom / "viewer_profiles.json").exists()
    assert not (tmp_path / "default" / "viewer_profiles.json").exists()
    status = store.storage_status()
    assert status["using_custom"] is True
    assert status["writable"] is True
    assert status["path"] == str(custom / "viewer_profiles.json")


@pytest.mark.asyncio
async def test_empty_custom_dir_falls_back_to_default(tmp_path):
    # dir_provider 返回空串 → 用默认目录（等价于未配置）
    store = ViewerStore(_FakePlugin(tmp_path), audit=None, dir_provider=lambda: "  ")
    await store.upsert_identity(ViewerIdentity(uid="9", nickname="九"))
    assert (tmp_path / "viewer_profiles.json").exists()
    assert store.storage_status()["using_custom"] is False


@pytest.mark.asyncio
async def test_custom_write_fallback_is_used_for_followup_reads(tmp_path, monkeypatch):
    custom = tmp_path / "custom_here"
    default = tmp_path / "default"
    store = ViewerStore(_FakePlugin(default), audit=None, dir_provider=lambda: str(custom))
    original_write_json = store._write_json

    def _fail_custom(file, profiles):
        if file.parent == custom:
            return False
        return original_write_json(file, profiles)

    monkeypatch.setattr(store, "_write_json", _fail_custom)

    await store.upsert_identity(ViewerIdentity(uid="8", nickname="八"))
    await store.mark_roasted("8", "fallback result")

    assert not (custom / "viewer_profiles.json").exists()
    assert (default / "viewer_profiles.json").exists()
    assert await store.has_roasted("8") is True

    restarted = ViewerStore(_FakePlugin(default), audit=None, dir_provider=lambda: str(custom))
    assert await restarted.has_roasted("8") is True


@pytest.mark.asyncio
async def test_mark_roasted_roundtrip(tmp_path):
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)
    await store.upsert_identity(ViewerIdentity(uid="7", nickname="七"))
    assert await store.has_roasted("7") is False

    assert await store.mark_roasted("7", "锐评内容") is True

    assert await store.has_roasted("7") is True
    recent = await store.recent_profiles()
    item = next(p for p in recent if p["uid"] == "7")
    assert item["roast_count"] == 1
    assert item["last_result"] == "锐评内容"


@pytest.mark.asyncio
async def test_record_live_danmaku_persists_count_and_preferences(tmp_path):
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)
    identity = ViewerIdentity(uid="42", nickname="提问观众")

    first = await store.record_live_danmaku(identity, "这个插件怎么配置？")
    second = await store.record_live_danmaku(identity, "还有教程吗？")
    await store.upsert_identity(ViewerIdentity(uid="42", nickname="新昵称"))

    assert first.danmaku_count == 1
    assert second.danmaku_count == 2
    restarted = ViewerStore(_FakePlugin(tmp_path), audit=None)
    item = next(profile for profile in await restarted.recent_profiles() if profile["uid"] == "42")
    assert item["nickname"] == "新昵称"
    assert item["danmaku_count"] == 2
    assert item["preference_tags"].get("question", 0) >= 2


@pytest.mark.asyncio
async def test_upsert_identity_without_nickname_preserves_existing_nickname(tmp_path):
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)
    await store.upsert_identity(ViewerIdentity(uid="42", nickname="known viewer"))

    profile = await store.upsert_identity(ViewerIdentity(uid="42", nickname=""))

    assert profile.nickname == "known viewer"


@pytest.mark.asyncio
async def test_record_live_danmaku_without_nickname_preserves_existing_nickname(tmp_path):
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)
    await store.upsert_identity(ViewerIdentity(uid="42", nickname="known viewer"))

    profile = await store.record_live_danmaku(ViewerIdentity(uid="42", nickname=""), "hello")

    assert profile.nickname == "known viewer"


@pytest.mark.asyncio
async def test_profile_management_preserves_identity_and_supports_deletion(tmp_path):
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)
    identity = ViewerIdentity(uid="42", nickname="viewer")
    await store.record_live_danmaku(identity, "question tutorial")
    await store.mark_roasted("42", "result")

    reset = await store.reset_profile_impression("42")
    profile = (await store.recent_profiles())[0]

    assert reset["reset"] is True
    assert reset["preserved_first_appearance"] is True
    assert profile["nickname"] == "viewer"
    assert profile["roast_count"] == 1
    assert profile["preference_tags"] == {}

    deleted = await store.delete_profile("42")
    assert deleted["deleted"] is True
    assert await store.recent_profiles() == []


@pytest.mark.asyncio
async def test_clear_profiles_reports_count(tmp_path):
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)
    await store.upsert_identity(ViewerIdentity(uid="1", nickname="one"))
    await store.upsert_identity(ViewerIdentity(uid="2", nickname="two"))

    cleared = await store.clear_profiles()

    assert cleared["cleared"] == 2
    assert cleared["applied"] is True
    assert await store.recent_profiles() == []


@pytest.mark.asyncio
async def test_profile_management_reports_failed_persistence(tmp_path, monkeypatch):
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)
    identity = ViewerIdentity(uid="42", nickname="viewer")
    await store.record_live_danmaku(identity, "question tutorial")

    monkeypatch.setattr(store, "_write_json", lambda _file, _profiles: False)

    cleared = await store.clear_profiles()
    deleted = await store.delete_profile("42")
    reset = await store.reset_profile_impression("42")

    assert cleared["cleared"] == 0
    assert cleared["applied"] is False
    assert deleted["deleted"] is False
    assert deleted["applied"] is False
    assert reset["reset"] is False
    assert reset["applied"] is False
    restarted = ViewerStore(_FakePlugin(tmp_path), audit=None)
    assert (await restarted.recent_profiles())[0]["uid"] == "42"


@pytest.mark.asyncio
async def test_records_viewer_preferences_without_persisting_raw_danmaku(tmp_path):
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)
    identity = ViewerIdentity(uid="1001", nickname="技术姥爷")

    profile = await store.record_live_danmaku(
        identity, "这个 AI 插件怎么配置？token=must-not-leak"
    )

    assert profile.danmaku_count == 1
    assert profile.preference_tags["tech_ai"] == 1
    assert profile.preference_tags["questions"] == 1
    assert profile.favorite_topics["tech_ai"] == 1
    assert profile.running_jokes["short_helper_mode"] == 1
    assert profile.interaction_style == "question"
    assert "answer first" in profile.response_preference
    assert "answer before teasing" in profile.avoid_guidance
    assert "likes tech/AI" in profile.impression_summary

    raw = (tmp_path / "viewer_profiles.json").read_text(encoding="utf-8")
    assert "这个 AI 插件怎么配置" not in raw
    assert "must-not-leak" not in raw

    recent = await store.recent_profiles()
    item = next(p for p in recent if p["uid"] == "1001")
    assert item["danmaku_count"] == 1
    assert item["preference_tags"]["tech_ai"] == 1
    assert item["favorite_topics"]["tech_ai"] == 1
    assert item["running_jokes"]["short_helper_mode"] == 1
    assert item["last_interaction_summary"] == "likes tech/AI, often asks questions"
    assert item["impression_summary"]
    assert item["avoid_guidance"] == "answer before teasing; do not dodge the question"


@pytest.mark.asyncio
async def test_recent_profiles_include_derived_viewer_profile_guidance(tmp_path):
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)
    identity = ViewerIdentity(uid="1001", nickname="技术姥爷")

    await store.record_live_danmaku(identity, "这个 AI 插件怎么配置？")
    await store.record_live_danmaku(identity, "AI 模型和插件还能怎么调？")
    await store.record_live_danmaku(identity, "有没有代码示例？")
    await store.record_live_danmaku(identity, "这个配置为什么不生效？")

    recent = await store.recent_profiles()
    item = next(p for p in recent if p["uid"] == "1001")

    assert item["viewer_stage"] == "returning_viewer"
    assert item["profile_confidence"] == "medium"
    assert item["reply_guidance"] == "answer first, then add one light follow-up"
    assert item["profile_summary"].startswith("likes tech/AI, often asks questions")
    assert item["impression_summary"].startswith("likes tech/AI, often asks questions")
    assert item["avoid_guidance"] == "answer before teasing; do not dodge the question"
    tags = {tag["tag"]: tag["count"] for tag in item["top_preference_tags"]}
    assert tags["question"] == 4
    assert tags["questions"] == 3
    assert tags["tech_ai"] == 4
    favorite_topics = {tag["tag"]: tag["count"] for tag in item["top_favorite_topics"]}
    assert favorite_topics["tech_ai"] == 4
    running_jokes = {tag["tag"]: tag["count"] for tag in item["top_running_jokes"]}
    assert running_jokes["short_helper_mode"] == 4

    raw = json.loads((tmp_path / "viewer_profiles.json").read_text(encoding="utf-8"))
    stored = raw["1001"]
    assert stored["favorite_topics"]["tech_ai"] == 4
    assert stored["running_jokes"]["short_helper_mode"] == 4
    assert stored["impression_summary"].startswith("likes tech/AI, often asks questions")
    assert stored["avoid_guidance"] == "answer before teasing; do not dodge the question"
    assert "viewer_stage" not in stored
    assert "profile_confidence" not in stored
    assert "top_preference_tags" not in stored
    assert "top_favorite_topics" not in stored
    assert "top_running_jokes" not in stored
    assert "reply_guidance" not in stored
    assert "profile_summary" not in stored


@pytest.mark.asyncio
async def test_profile_management_rejects_fallback_only_persistence(
    tmp_path, monkeypatch
):
    custom = tmp_path / "custom"
    default = tmp_path / "default"
    custom.mkdir()
    configured_file = custom / "viewer_profiles.json"
    configured_file.write_text(
        json.dumps(
            {
                "42": {
                    "uid": "42",
                    "nickname": "viewer",
                    "roast_count": 1,
                    "preference_tags": {"question": 1},
                }
            }
        ),
        encoding="utf-8",
    )
    store = ViewerStore(
        _FakePlugin(default), audit=None, dir_provider=lambda: str(custom)
    )
    original_write_json = store._write_json

    def _fail_custom(file, profiles):
        if file.parent == custom:
            return False
        return original_write_json(file, profiles)

    monkeypatch.setattr(store, "_write_json", _fail_custom)

    cleared = await store.clear_profiles()
    deleted = await store.delete_profile("42")
    reset = await store.reset_profile_impression("42")

    assert cleared["applied"] is False
    assert deleted["applied"] is False
    assert reset["applied"] is False
    assert not (default / "viewer_profiles.json").exists()
    restarted = ViewerStore(
        _FakePlugin(default), audit=None, dir_provider=lambda: str(custom)
    )
    profile = (await restarted.recent_profiles())[0]
    assert profile["uid"] == "42"
    assert profile["preference_tags"] == {"question": 1}


@pytest.mark.asyncio
async def test_mark_roasted_reports_failed_persistence(tmp_path, monkeypatch):
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)
    await store.upsert_identity(ViewerIdentity(uid="42", nickname="viewer"))
    monkeypatch.setattr(store, "_write_json", lambda _file, _profiles: False)

    assert await store.mark_roasted("42", "result") is False

    restarted = ViewerStore(_FakePlugin(tmp_path), audit=None)
    profile = (await restarted.recent_profiles())[0]
    assert profile["roast_count"] == 0
