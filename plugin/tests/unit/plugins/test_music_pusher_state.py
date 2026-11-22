from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

import plugin.plugins.music_pusher as music_pusher_mod
from plugin.plugins.music_pusher import MusicPusherPlugin


class _Logger:
    def __init__(self) -> None:
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def info(self, *args: object, **kwargs: object) -> None:
        pass

    def warning(self, msg: str, *args: object) -> None:
        self.warnings.append(msg.format(*args) if args else msg)

    def error(self, msg: str, *args: object) -> None:
        self.errors.append(msg.format(*args) if args else msg)


def _make_plugin(tmp_path: Path) -> MusicPusherPlugin:
    plugin = MusicPusherPlugin.__new__(MusicPusherPlugin)
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    data_dir.mkdir(parents=True, exist_ok=True)
    (config_dir / "static").mkdir(parents=True, exist_ok=True)

    plugin.logger = _Logger()
    plugin.ctx = SimpleNamespace(
        plugin_id="music_pusher",
        logger=plugin.logger,
        push_message=lambda **_kwargs: {"submitted": True},
        data_path=lambda name: data_dir / name,
        config_path=config_dir / "plugin.toml",
    )
    plugin._host_ctx = plugin.ctx
    plugin.data_path = lambda name: data_dir / name  # type: ignore[method-assign]
    plugin.register_static_ui = lambda *_args, **_kwargs: True  # type: ignore[method-assign]
    plugin._notify_static_ui_registered = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    plugin._state_lock = asyncio.Lock()
    plugin._run_lock = asyncio.Lock()
    plugin._scheduler_stop = asyncio.Event()
    plugin._push_cancel_event = threading.Event()
    plugin._scheduler_task = None
    plugin._music_items = []
    plugin._tasks = {}
    plugin._upload_name_map = {}
    plugin._lyrics_map = {}
    plugin._degraded_state = {}
    plugin._active_task_id = None
    plugin._active_track = None
    plugin._active_track_started_at = None
    plugin._active_track_duration = 0
    plugin._active_control = None
    plugin._active_cancel_event = None
    plugin._active_execution_task = None
    plugin._original_push_message = None
    plugin._push_mapper_installed = False
    plugin._attach_prompt_on_push = True
    plugin._playback_state = {"status": "idle"}
    return plugin


@pytest.mark.plugin_unit
def test_atomic_save_failure_preserves_existing_files_and_cleans_tmp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _make_plugin(tmp_path)
    plugin._music_items = [{"item_id": "m1", "url": "https://example.com/a.mp3"}]
    plugin._upload_name_map = {"a.mp3": {"title": "Song A"}}
    plugin._lyrics_map = {"m1": {"lyric_text": "la la"}}
    plugin._save_state_locked()
    plugin._save_upload_name_map_locked()
    plugin._save_lyrics_map_locked()

    before_state = plugin._state_file.read_text(encoding="utf-8")
    before_upload = plugin._upload_name_map_file.read_text(encoding="utf-8")
    before_lyrics = plugin._lyrics_map_file.read_text(encoding="utf-8")

    def _boom(_src: Path, _dst: Path) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(music_pusher_mod.os, "replace", _boom)

    with pytest.raises(OSError, match="disk full"):
        plugin._save_state_locked()
    with pytest.raises(OSError, match="disk full"):
        plugin._save_upload_name_map_locked()
    with pytest.raises(OSError, match="disk full"):
        plugin._save_lyrics_map_locked()

    assert plugin._state_file.read_text(encoding="utf-8") == before_state
    assert plugin._upload_name_map_file.read_text(encoding="utf-8") == before_upload
    assert plugin._lyrics_map_file.read_text(encoding="utf-8") == before_lyrics
    assert not list(plugin._state_file.parent.glob("*.tmp"))


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_corrupt_state_files_are_backed_up_and_surfaced_as_degraded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _make_plugin(tmp_path)
    monkeypatch.setattr(plugin, "_ensure_scheduler_task", lambda: None)

    plugin._state_file.parent.mkdir(parents=True, exist_ok=True)
    plugin._state_file.write_text('{"tasks": [', encoding="utf-8")
    plugin._upload_name_map_file.write_text('["not-a-dict"]', encoding="utf-8")
    plugin._lyrics_map_file.write_text("{truncated", encoding="utf-8")

    res = await plugin.on_start()

    assert res.is_ok()
    assert len(list(plugin._state_file.parent.glob("scheduler_state.json.corrupt.*"))) == 1
    assert len(list(plugin._state_file.parent.glob("upload_name_map.json.corrupt.*"))) == 1
    assert len(list(plugin._state_file.parent.glob("lyrics_map.json.corrupt.*"))) == 1
    assert json.loads(plugin._state_file.read_text(encoding="utf-8"))["tasks"] == []

    snap = plugin._build_runtime_snapshot()
    assert snap["degraded"] is True
    assert set(snap["degraded_files"]) == {
        "scheduler_state.json",
        "upload_name_map.json",
        "lyrics_map.json",
    }
    assert any("degraded state" in msg for msg in plugin.logger.errors)


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_clean_startup_reports_not_degraded_and_preserves_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _make_plugin(tmp_path)
    monkeypatch.setattr(plugin, "_ensure_scheduler_task", lambda: None)

    plugin._music_items = [
        {
            "item_id": "item-1",
            "title": "Song",
            "artist": "Artist",
            "source": "url",
            "url": "https://example.com/song.mp3",
            "duration_sec": 60,
        }
    ]
    plugin._save_state_locked()
    plugin._save_upload_name_map_locked()
    plugin._save_lyrics_map_locked()

    res = await plugin.on_start()

    assert res.is_ok()
    snap = plugin._build_runtime_snapshot()
    assert snap["degraded"] is False
    assert snap["degraded_files"] == {}
    assert len(plugin._music_items) == 1
