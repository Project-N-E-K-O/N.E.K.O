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


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_degraded_scheduler_state_preserves_valid_tasks_and_blocks_upload_gc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _make_plugin(tmp_path)
    monkeypatch.setattr(plugin, "_ensure_scheduler_task", lambda: None)
    plugin._upload_dir.mkdir(parents=True, exist_ok=True)
    audio_file = plugin._upload_dir / "keep_me.mp3"
    audio_file.write_bytes(b"ID3")
    plugin._upload_name_map = {
        "keep_me.mp3": {
            "stored_filename": "keep_me.mp3",
            "original_filename": "keep_me.mp3",
            "title": "Keep Me",
            "artist": "Artist",
            "duration_sec": 30,
            "deleted": True,
        }
    }
    plugin._save_upload_name_map_locked()

    # Mixed-validity scheduler_state.json: invalid music_items type ({}) alongside valid task referencing keep_me.mp3.
    plugin._state_file.parent.mkdir(parents=True, exist_ok=True)
    plugin._state_file.write_text(
        json.dumps(
            {
                "music_items": {},
                "tasks": [
                    {
                        "task_id": "task-1",
                        "name": "Scheduled",
                        "status": "pending",
                        "queue": [
                            {
                                "url": "http://127.0.0.1:48916/plugin/music_pusher/ui/uploads/keep_me.mp3",
                                "title": "Keep Me",
                                "artist": "Artist",
                                "duration_sec": 30,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    res = await plugin.on_start()
    assert res.is_ok()
    assert audio_file.is_file()
    assert "task-1" in plugin._tasks
    assert "scheduler_state.json" in plugin._degraded_state

    # After cleanly fixing scheduler_state.json and deleting the task, unreferenced tombstoned upload is GC'd.
    plugin._tasks.clear()
    plugin._degraded_state.clear()
    plugin._save_state_locked()
    res2 = await plugin.on_start()
    assert res2.is_ok()
    assert not audio_file.exists()


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_corrupt_backup_failure_skips_startup_overwrite(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _make_plugin(tmp_path)
    monkeypatch.setattr(plugin, "_ensure_scheduler_task", lambda: None)
    plugin._state_file.parent.mkdir(parents=True, exist_ok=True)
    corrupt_bytes = "{broken json"
    plugin._state_file.write_text(corrupt_bytes, encoding="utf-8")

    real_replace = music_pusher_mod.os.replace

    def _fail_backup_replace(src: Path, dst: Path) -> None:
        if ".corrupt." in dst.name:
            raise PermissionError("file in use")
        real_replace(src, dst)

    monkeypatch.setattr(music_pusher_mod.os, "replace", _fail_backup_replace)
    res = await plugin.on_start()

    assert res.is_ok()
    assert plugin._state_file.read_text(encoding="utf-8") == corrupt_bytes
    assert plugin._degraded_state["scheduler_state.json"]["backup_path"] == ""

    # Shutdown while backup still fails must STILL refuse to overwrite corrupt file.
    await plugin.on_shutdown()
    assert plugin._state_file.read_text(encoding="utf-8") == corrupt_bytes
    assert plugin._degraded_state["scheduler_state.json"]["backup_path"] == ""

    # Once file lock clears, subsequent save (e.g. on_shutdown) backs up corrupt file first.
    monkeypatch.setattr(music_pusher_mod.os, "replace", real_replace)
    await plugin.on_shutdown()
    backup_path_str = plugin._degraded_state["scheduler_state.json"]["backup_path"]
    assert backup_path_str
    assert Path(backup_path_str).read_text(encoding="utf-8") == corrupt_bytes
    saved = json.loads(plugin._state_file.read_text(encoding="utf-8"))
    assert "tasks" in saved


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_corrupt_backup_failure_rejects_runtime_mutations_without_partial_state_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _make_plugin(tmp_path)
    monkeypatch.setattr(plugin, "_ensure_scheduler_task", lambda: None)
    plugin._state_file.parent.mkdir(parents=True, exist_ok=True)
    corrupt_bytes = "{broken state"
    plugin._state_file.write_text(corrupt_bytes, encoding="utf-8")

    real_replace = music_pusher_mod.os.replace

    def _fail_backup_replace(src: Path, dst: Path) -> None:
        if ".corrupt." in dst.name:
            raise PermissionError("file locked")
        real_replace(src, dst)

    monkeypatch.setattr(music_pusher_mod.os, "replace", _fail_backup_replace)
    start_res = await plugin.on_start()
    assert start_res.is_ok()

    # Direct runtime save helpers raise OSError rather than silently returning success.
    with pytest.raises(OSError, match="failed to back up corrupt"):
        plugin._save_state_locked()
    assert plugin._state_file.read_text(encoding="utf-8") == corrupt_bytes

    # Public mutation entries return Err and leave in-memory state unmutated.
    prev_attach = plugin._attach_prompt_on_push
    settings_res = await plugin.set_push_settings(attach_prompt_on_push=not prev_attach)
    assert settings_res.is_err()
    assert plugin._attach_prompt_on_push is prev_attach

    from datetime import datetime, timedelta, timezone

    async def _allow_queue_urls(_queue: list[dict[str, Any]]) -> str:
        return ""

    monkeypatch.setattr(plugin, "_validate_queue_urls_allowlistable", _allow_queue_urls)
    assert "损坏" in str(settings_res.error)

    future_iso = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    create_res = await plugin.create_schedule_task(
        trigger_at=future_iso,
        name="blocked-task",
        queue_items=[{"url": "https://example.test/song.mp3", "title": "Song"}],
    )
    assert create_res.is_err()
    assert "损坏" in str(create_res.error)
    assert plugin._tasks == {}
    assert plugin._state_file.read_text(encoding="utf-8") == corrupt_bytes

    # Once file lock clears, mutation backs up corrupt file and persists cleanly.
    monkeypatch.setattr(music_pusher_mod.os, "replace", real_replace)
    ok_res = await plugin.set_push_settings(attach_prompt_on_push=not prev_attach)
    assert ok_res.is_ok()
    assert plugin._attach_prompt_on_push is (not prev_attach)
    assert Path(plugin._degraded_state["scheduler_state.json"]["backup_path"]).read_text(encoding="utf-8") == corrupt_bytes


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_upload_music_file_checks_state_writability_before_pushing_playback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import base64

    plugin = _make_plugin(tmp_path)
    monkeypatch.setattr(plugin, "_ensure_scheduler_task", lambda: None)
    plugin._state_file.parent.mkdir(parents=True, exist_ok=True)
    corrupt_bytes = "{broken state"
    plugin._state_file.write_text(corrupt_bytes, encoding="utf-8")

    real_replace = music_pusher_mod.os.replace

    def _fail_backup_replace(src: Path, dst: Path) -> None:
        if ".corrupt." in dst.name:
            raise PermissionError("file locked")
        real_replace(src, dst)

    monkeypatch.setattr(music_pusher_mod.os, "replace", _fail_backup_replace)
    assert (await plugin.on_start()).is_ok()

    push_calls: list[dict[str, Any]] = []

    async def _record_push(**kwargs: Any) -> bool:
        push_calls.append(dict(kwargs))
        return True

    monkeypatch.setattr(plugin, "_push_music_link_with_timeout", _record_push)
    audio_b64 = base64.b64encode(b"ID3testaudio").decode("ascii")
    res = await plugin.upload_music_file(
        audio_base64=audio_b64,
        filename="demo.mp3",
        auto_push=True,
    )
    assert res.is_err()
    assert "损坏" in str(res.error)
    assert push_calls == []
    assert list(plugin._upload_dir.glob("*")) == []

    # Case 2: State becomes unwritable during push after playback is accepted -> keep uploaded file alive.
    monkeypatch.setattr(music_pusher_mod.os, "replace", real_replace)
    plugin._state_file.write_text("{}", encoding="utf-8")
    assert (await plugin.on_start()).is_ok()

    async def _push_then_corrupt(**kwargs: Any) -> bool:
        push_calls.append(dict(kwargs))
        plugin._state_file.write_text("{corrupt mid-flight", encoding="utf-8")
        plugin._degraded_state[plugin._state_file.name] = {
            "reason": "corrupt",
            "backup_path": "",
        }
        monkeypatch.setattr(music_pusher_mod.os, "replace", _fail_backup_replace)
        return True

    monkeypatch.setattr(plugin, "_push_music_link_with_timeout", _push_then_corrupt)
    res2 = await plugin.upload_music_file(
        audio_base64=audio_b64,
        filename="kept_after_push.mp3",
        auto_push=True,
    )
    assert res2.is_err()
    assert "损坏" in str(res2.error)
    assert len(push_calls) == 1
    assert len(list(plugin._upload_dir.glob("*.mp3"))) == 1


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_invalid_tasks_preserves_tombstoned_audio_across_restarts_and_delete_music_item(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _make_plugin(tmp_path)
    monkeypatch.setattr(plugin, "_ensure_scheduler_task", lambda: None)
    plugin._upload_dir.mkdir(parents=True, exist_ok=True)
    audio_a = plugin._upload_dir / "task_ref_a.mp3"
    audio_b = plugin._upload_dir / "task_ref_b.mp3"
    audio_a.write_bytes(b"ID3-A")
    audio_b.write_bytes(b"ID3-B")

    plugin._upload_name_map = {
        "task_ref_a.mp3": {
            "stored_filename": "task_ref_a.mp3",
            "original_filename": "task_ref_a.mp3",
            "title": "A",
            "artist": "Artist",
            "duration_sec": 30,
            "deleted": True,
        },
        "task_ref_b.mp3": {
            "stored_filename": "task_ref_b.mp3",
            "original_filename": "task_ref_b.mp3",
            "title": "B",
            "artist": "Artist",
            "duration_sec": 30,
            "deleted": False,
        },
    }
    plugin._save_upload_name_map_locked()

    # Corrupt `tasks` (dict instead of list) + valid music_item referencing task_ref_b.mp3.
    plugin._state_file.parent.mkdir(parents=True, exist_ok=True)
    plugin._state_file.write_text(
        json.dumps(
            {
                "music_items": [
                    {
                        "item_id": "itm_b",
                        "url": "http://127.0.0.1:48916/plugin/music_pusher/ui/uploads/task_ref_b.mp3",
                        "title": "B",
                        "artist": "Artist",
                        "source": "upload",
                        "stored_filename": "task_ref_b.mp3",
                        "original_filename": "task_ref_b.mp3",
                        "duration_sec": 30,
                    }
                ],
                "tasks": {},
            }
        ),
        encoding="utf-8",
    )

    # Boot 1: backs up invalid scheduler_state.json, keeps audio_a despite tombstone.
    assert (await plugin.on_start()).is_ok()
    assert audio_a.is_file()
    persisted = json.loads(plugin._state_file.read_text(encoding="utf-8"))
    assert persisted.get("tasks_recovery_incomplete") is True

    # Deleting item_b while tasks_recovery_incomplete=True must tombstone upload without unlinking file.
    del_res = await plugin.delete_music_item(item_id="itm_b")
    assert del_res.is_ok()
    assert audio_b.is_file()
    assert plugin._upload_name_map["task_ref_b.mp3"]["deleted"] is True

    # Boot 2: scheduler_state.json has valid JSON now, but tasks_recovery_incomplete persists protection!
    plugin2 = _make_plugin(tmp_path)
    monkeypatch.setattr(plugin2, "_ensure_scheduler_task", lambda: None)
    assert (await plugin2.on_start()).is_ok()
    assert audio_a.is_file()
    assert audio_b.is_file()
