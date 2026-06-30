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
async def test_clear_profiles_resets_active_fallback_store(tmp_path, monkeypatch):
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
    assert (default / "viewer_profiles.json").exists()

    result = await store.clear_profiles()

    assert result["cleared"] == 1
    assert result["path"] == str(default / "viewer_profiles.json")
    assert await store.recent_profiles() == []
    assert json.loads((default / "viewer_profiles.json").read_text(encoding="utf-8")) == {}


@pytest.mark.asyncio
async def test_clear_profiles_raises_when_all_writes_fail(tmp_path, monkeypatch):
    store = ViewerStore(_FakePlugin(tmp_path), audit=None)
    await store.upsert_identity(ViewerIdentity(uid="9", nickname="blocked viewer"))
    monkeypatch.setattr(store, "_write_json", lambda _file, _profiles: False)

    with pytest.raises(OSError, match="failed to clear viewer profiles"):
        await store.clear_profiles()

    assert await store.has_roasted("9") is False
    assert (await store.recent_profiles())[0]["uid"] == "9"
