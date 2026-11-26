from __future__ import annotations

import asyncio
import json
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

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
    plugin._tasks_recovery_incomplete = False
    plugin._scheduler_save_backoff_until = 0.0
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
    snap2 = plugin2._build_runtime_snapshot()
    assert snap2["degraded"] is True
    assert snap2["tasks_recovery_incomplete"] is True
    assert snap2["upload_gc_paused"] is True
    assert len(snap2["corrupt_state_backups"]) == 1


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_corrupt_backup_followed_by_startup_save_failure_still_blocks_gc_on_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin1 = _make_plugin(tmp_path)
    scheduler_starts = 0

    def _count_scheduler_start() -> None:
        nonlocal scheduler_starts
        scheduler_starts += 1

    monkeypatch.setattr(plugin1, "_ensure_scheduler_task", _count_scheduler_start)
    plugin1._upload_dir.mkdir(parents=True, exist_ok=True)
    audio_a = plugin1._upload_dir / "keep_across_crash.mp3"
    audio_a.write_bytes(b"ID3-CRASH")

    plugin1._upload_name_map = {
        "keep_across_crash.mp3": {
            "stored_filename": "keep_across_crash.mp3",
            "original_filename": "keep_across_crash.mp3",
            "title": "Crash Keep",
            "artist": "Artist",
            "duration_sec": 30,
            "deleted": True,
        }
    }
    plugin1._save_upload_name_map_locked()

    plugin1._state_file.parent.mkdir(parents=True, exist_ok=True)
    plugin1._state_file.write_text("{corrupt", encoding="utf-8")

    real_atomic_write_json = music_pusher_mod._atomic_write_json

    def _fail_state_save(path: Path, payload: object) -> None:
        if path == plugin1._state_file:
            raise OSError("disk full")
        real_atomic_write_json(path, payload)

    monkeypatch.setattr(music_pusher_mod, "_atomic_write_json", _fail_state_save)
    res1 = await plugin1.on_start()
    assert res1.is_ok()
    assert scheduler_starts == 1
    assert any("Failed to persist scheduler_state during startup" in msg for msg in plugin1.logger.errors)

    # Corrupt scheduler_state.json was backed up to .corrupt.*, leaving no scheduler_state.json on disk.
    assert not plugin1._state_file.exists()
    assert len(list(plugin1._state_file.parent.glob("scheduler_state.json.corrupt.*"))) == 1
    assert audio_a.is_file()

    # Boot 2: scheduler_state.json is absent, but existing .corrupt.* backup keeps GC paused.
    monkeypatch.setattr(music_pusher_mod, "_atomic_write_json", real_atomic_write_json)
    plugin2 = _make_plugin(tmp_path)
    monkeypatch.setattr(plugin2, "_ensure_scheduler_task", lambda: None)
    assert (await plugin2.on_start()).is_ok()
    assert audio_a.is_file()
    assert plugin2._tasks_recovery_incomplete is True
    snap2 = plugin2._build_runtime_snapshot()
    assert snap2["degraded"] is True
    assert snap2["tasks_recovery_incomplete"] is True
    assert snap2["upload_gc_paused"] is True
    assert len(snap2["corrupt_state_backups"]) == 1
    persisted2 = json.loads(plugin2._state_file.read_text(encoding="utf-8"))
    assert persisted2.get("tasks_recovery_incomplete") is True


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_removing_corrupt_state_backups_clears_recovery_incomplete_and_resumes_gc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin1 = _make_plugin(tmp_path)
    monkeypatch.setattr(plugin1, "_ensure_scheduler_task", lambda: None)
    plugin1._upload_dir.mkdir(parents=True, exist_ok=True)
    audio_a = plugin1._upload_dir / "tombstoned_orphan.mp3"
    audio_a.write_bytes(b"ID3-ORPHAN")

    plugin1._upload_name_map = {
        "tombstoned_orphan.mp3": {
            "stored_filename": "tombstoned_orphan.mp3",
            "original_filename": "tombstoned_orphan.mp3",
            "title": "Orphan",
            "artist": "Artist",
            "duration_sec": 30,
            "deleted": True,
        }
    }
    plugin1._save_upload_name_map_locked()
    plugin1._state_file.parent.mkdir(parents=True, exist_ok=True)
    plugin1._state_file.write_text("{corrupt", encoding="utf-8")

    # Boot 1 & Boot 2 while .corrupt.* backup remains -> GC stays paused.
    assert (await plugin1.on_start()).is_ok()
    assert audio_a.is_file()
    plugin2 = _make_plugin(tmp_path)
    monkeypatch.setattr(plugin2, "_ensure_scheduler_task", lambda: None)
    assert (await plugin2.on_start()).is_ok()
    assert audio_a.is_file()
    snap2 = plugin2._build_runtime_snapshot()
    assert snap2["degraded"] is True
    assert snap2["tasks_recovery_incomplete"] is True
    assert snap2["upload_gc_paused"] is True

    # Operator removes corrupt backup files after inspecting/recovering -> Boot 3 clears flag & resumes GC.
    for backup_file in plugin2._state_file.parent.glob("scheduler_state.json.corrupt.*"):
        backup_file.unlink()

    plugin3 = _make_plugin(tmp_path)
    monkeypatch.setattr(plugin3, "_ensure_scheduler_task", lambda: None)
    assert (await plugin3.on_start()).is_ok()
    assert not audio_a.exists()
    assert "tombstoned_orphan.mp3" not in plugin3._upload_name_map
    assert plugin3._tasks_recovery_incomplete is False
    snap3 = plugin3._build_runtime_snapshot()
    assert snap3["degraded"] is False
    assert snap3["tasks_recovery_incomplete"] is False
    assert snap3["upload_gc_paused"] is False
    assert snap3["corrupt_state_backups"] == []
    persisted3 = json.loads(plugin3._state_file.read_text(encoding="utf-8"))
    assert "tasks_recovery_incomplete" not in persisted3


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_unreadable_backup_listing_keeps_tasks_recovery_incomplete_paused(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin1 = _make_plugin(tmp_path)
    monkeypatch.setattr(plugin1, "_ensure_scheduler_task", lambda: None)
    plugin1._upload_dir.mkdir(parents=True, exist_ok=True)
    audio_a = plugin1._upload_dir / "keep_on_eperm.mp3"
    audio_a.write_bytes(b"ID3-EPERM")

    plugin1._upload_name_map = {
        "keep_on_eperm.mp3": {
            "stored_filename": "keep_on_eperm.mp3",
            "original_filename": "keep_on_eperm.mp3",
            "title": "Keep Eperm",
            "artist": "Artist",
            "duration_sec": 30,
            "deleted": True,
        }
    }
    plugin1._save_upload_name_map_locked()
    plugin1._state_file.parent.mkdir(parents=True, exist_ok=True)
    plugin1._state_file.write_text("{corrupt", encoding="utf-8")

    # Boot 1 creates .corrupt.* backup and persists tasks_recovery_incomplete=True.
    assert (await plugin1.on_start()).is_ok()
    assert audio_a.is_file()

    # Boot 2: directory listing fails with PermissionError; must fail closed and keep GC paused.
    real_iterdir = Path.iterdir
    data_dir = plugin1._state_file.parent

    def _deny_data_dir_iterdir(self_path: Path):
        if self_path == data_dir:
            raise PermissionError("permission denied")
        return real_iterdir(self_path)

    monkeypatch.setattr(Path, "iterdir", _deny_data_dir_iterdir)

    plugin2 = _make_plugin(tmp_path)
    monkeypatch.setattr(plugin2, "_ensure_scheduler_task", lambda: None)
    assert (await plugin2.on_start()).is_ok()
    assert audio_a.is_file()
    assert plugin2._tasks_recovery_incomplete is True
    snap2 = plugin2._build_runtime_snapshot()
    assert snap2["degraded"] is True
    assert snap2["tasks_recovery_incomplete"] is True
    assert snap2["upload_gc_paused"] is True
    assert snap2["corrupt_state_backups"] == []
    persisted2 = json.loads(plugin2._state_file.read_text(encoding="utf-8"))
    assert persisted2.get("tasks_recovery_incomplete") is True

    # Boot 2b: even if scheduler_state.json is absent while iterdir fails, keep GC paused.
    plugin2._state_file.unlink()
    plugin2b = _make_plugin(tmp_path)
    monkeypatch.setattr(plugin2b, "_ensure_scheduler_task", lambda: None)
    assert (await plugin2b.on_start()).is_ok()
    assert audio_a.is_file()
    assert plugin2b._tasks_recovery_incomplete is True

    # Restore directory readability and remove backup files -> Boot 3 clears flag & runs GC.
    monkeypatch.undo()
    for backup_file in data_dir.glob("scheduler_state.json.corrupt.*"):
        backup_file.unlink()

    plugin3 = _make_plugin(tmp_path)
    monkeypatch.setattr(plugin3, "_ensure_scheduler_task", lambda: None)
    assert (await plugin3.on_start()).is_ok()
    assert not audio_a.exists()
    assert plugin3._tasks_recovery_incomplete is False
    snap3 = plugin3._build_runtime_snapshot()
    assert snap3["degraded"] is False
    assert snap3["tasks_recovery_incomplete"] is False
    assert snap3["upload_gc_paused"] is False


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_dropped_invalid_task_or_queue_entry_backs_up_and_pauses_gc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Case A: tasks is a valid list, but one task has a non-list queue referencing a tombstoned upload.
    case_a_dir = tmp_path / "case_a"
    plugin_a = _make_plugin(case_a_dir)
    monkeypatch.setattr(plugin_a, "_ensure_scheduler_task", lambda: None)
    plugin_a._upload_dir.mkdir(parents=True, exist_ok=True)
    audio_a = plugin_a._upload_dir / "dropped_task_ref.mp3"
    audio_a.write_bytes(b"ID3-DROPPED")
    plugin_a._upload_name_map = {
        "dropped_task_ref.mp3": {
            "stored_filename": "dropped_task_ref.mp3",
            "original_filename": "dropped_task_ref.mp3",
            "title": "Dropped Ref",
            "artist": "Artist",
            "duration_sec": 30,
            "deleted": True,
        }
    }
    plugin_a._save_upload_name_map_locked()

    raw_state_a = {
        "music_items": [],
        "tasks": [
            {
                "task_id": "task-valid",
                "name": "Valid Task",
                "status": "pending",
                "queue": [{"url": "https://example.com/valid.mp3", "title": "Valid"}],
            },
            {
                "task_id": "task-bad-queue",
                "name": "Bad Queue",
                "status": "pending",
                "queue": {
                    "url": "http://127.0.0.1:48916/plugin/music_pusher/ui/uploads/dropped_task_ref.mp3"
                },
            },
        ],
    }
    plugin_a._state_file.parent.mkdir(parents=True, exist_ok=True)
    plugin_a._state_file.write_text(json.dumps(raw_state_a), encoding="utf-8")

    assert (await plugin_a.on_start()).is_ok()
    assert "task-valid" in plugin_a._tasks
    assert "task-bad-queue" not in plugin_a._tasks
    assert plugin_a._tasks_recovery_incomplete is True
    assert audio_a.is_file()
    backups_a = list(plugin_a._state_file.parent.glob("scheduler_state.json.corrupt.*"))
    assert len(backups_a) == 1
    assert json.loads(backups_a[0].read_text(encoding="utf-8")) == raw_state_a
    persisted_a = json.loads(plugin_a._state_file.read_text(encoding="utf-8"))
    assert persisted_a.get("tasks_recovery_incomplete") is True

    # Case B: task has one valid queue track and one invalid queue entry -> keeps valid track, backs up & pauses GC.
    case_b_dir = tmp_path / "case_b"
    plugin_b = _make_plugin(case_b_dir)
    monkeypatch.setattr(plugin_b, "_ensure_scheduler_task", lambda: None)
    plugin_b._upload_dir.mkdir(parents=True, exist_ok=True)
    audio_b = plugin_b._upload_dir / "dropped_track_ref.mp3"
    audio_b.write_bytes(b"ID3-TRACK")
    plugin_b._upload_name_map = {
        "dropped_track_ref.mp3": {
            "stored_filename": "dropped_track_ref.mp3",
            "original_filename": "dropped_track_ref.mp3",
            "title": "Dropped Track",
            "artist": "Artist",
            "duration_sec": 30,
            "deleted": True,
        }
    }
    plugin_b._save_upload_name_map_locked()

    plugin_b._state_file.parent.mkdir(parents=True, exist_ok=True)
    plugin_b._state_file.write_text(
        json.dumps(
            {
                "music_items": [],
                "tasks": [
                    {
                        "task_id": "task-partial",
                        "name": "Partial Task",
                        "status": "pending",
                        "queue": [
                            {"url": "https://example.com/ok.mp3", "title": "OK"},
                            {"url": "ftp://invalid.example/bad.mp3", "title": "Bad Scheme"},
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert (await plugin_b.on_start()).is_ok()
    assert len(plugin_b._tasks["task-partial"]["queue"]) == 1
    assert plugin_b._tasks_recovery_incomplete is True
    assert audio_b.is_file()
    assert len(list(plugin_b._state_file.parent.glob("scheduler_state.json.corrupt.*"))) == 1


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_legacy_non_finite_float_sanitized_and_startup_starts_scheduler(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _make_plugin(tmp_path)
    scheduler_starts = 0

    def _count_scheduler_start() -> None:
        nonlocal scheduler_starts
        scheduler_starts += 1

    monkeypatch.setattr(plugin, "_ensure_scheduler_task", _count_scheduler_start)
    plugin._state_file.parent.mkdir(parents=True, exist_ok=True)
    legacy_payload = {
        "music_items": [
            {
                "item_id": "itm-inf",
                "url": "https://example.com/inf.mp3",
                "title": "Inf Song",
                "artist": "Artist",
                "duration_sec": float("inf"),
                "legacy_metric": float("inf"),
            }
        ],
        "tasks": [
            {
                "task_id": "task-legacy",
                "name": "Legacy Task",
                "status": "pending",
                "trigger_at": (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat(),
                "legacy_metric": float("inf"),
                "nan_metric": float("nan"),
                "queue": [
                    {
                        "url": "https://example.com/inf.mp3",
                        "title": "Inf Song",
                        "duration_sec": 30,
                    }
                ],
            }
        ],
    }
    plugin._state_file.write_text(
        json.dumps(legacy_payload, allow_nan=True),
        encoding="utf-8",
    )

    res = await plugin.on_start()
    assert res.is_ok()
    assert scheduler_starts == 1
    assert "task-legacy" in plugin._tasks
    assert list(plugin._state_file.parent.glob("scheduler_state.json.corrupt.*")) == []

    def _reject_non_finite(token: str) -> Any:
        raise ValueError(f"Non-finite constant in persisted JSON: {token}")

    persisted = json.loads(
        plugin._state_file.read_text(encoding="utf-8"),
        parse_constant=_reject_non_finite,
    )
    assert persisted["music_items"][0]["legacy_metric"] is None
    assert persisted["tasks"][0]["legacy_metric"] is None
    assert persisted["tasks"][0]["nan_metric"] is None


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_push_music_url_checks_writability_before_push_and_reports_split_save_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _make_plugin(tmp_path)
    monkeypatch.setattr(plugin, "_ensure_scheduler_task", lambda: None)

    async def _allow_url(_url: str) -> bool:
        return True

    async def _no_duration(_url: str) -> int | None:
        return 42

    push_calls: list[dict[str, Any]] = []

    async def _record_push(**kwargs: Any) -> bool:
        push_calls.append(dict(kwargs))
        return True

    monkeypatch.setattr(plugin, "_allowlist_domains_for_url_async", _allow_url)
    monkeypatch.setattr(plugin, "_detect_audio_duration_from_url", _no_duration)
    monkeypatch.setattr(plugin, "_push_music_link_with_timeout", _record_push)

    # Case 1: scheduler_state.json is degraded and cannot back up -> add_to_library=True aborts BEFORE pushing.
    plugin._state_file.parent.mkdir(parents=True, exist_ok=True)
    plugin._state_file.write_text("{corrupt", encoding="utf-8")
    real_replace = music_pusher_mod.os.replace

    def _fail_backup_replace(src: Path, dst: Path) -> None:
        if ".corrupt." in dst.name:
            raise PermissionError("file locked")
        real_replace(src, dst)

    monkeypatch.setattr(music_pusher_mod.os, "replace", _fail_backup_replace)
    assert (await plugin.on_start()).is_ok()

    res_blocked = await plugin.push_music_url(
        url="https://example.com/preblocked.mp3",
        title="Blocked",
        auto_push=True,
        add_to_library=True,
        duration_sec=30,
    )
    assert res_blocked.is_err()
    assert "损坏" in str(res_blocked.error)
    assert push_calls == []

    # Case 2: add_to_library=False skips save check and still pushes cleanly.
    res_play_only = await plugin.push_music_url(
        url="https://example.com/playonly.mp3",
        title="Play Only",
        auto_push=True,
        add_to_library=False,
        duration_sec=30,
    )
    assert res_play_only.is_ok()
    assert res_play_only.value["pushed"] is True
    assert res_play_only.value["saved"] is False
    assert len(push_calls) == 1

    # Case 3: writable before push, then post-push save raises OSError -> rolls back in-memory state and splits outcome.
    monkeypatch.setattr(music_pusher_mod.os, "replace", real_replace)
    plugin._state_file.write_text("{}", encoding="utf-8")
    assert (await plugin.on_start()).is_ok()
    push_calls.clear()

    real_atomic_write_json = music_pusher_mod._atomic_write_json

    def _fail_state_write(path: Path, payload: object) -> None:
        if path == plugin._state_file:
            raise OSError("disk full")
        real_atomic_write_json(path, payload)

    monkeypatch.setattr(music_pusher_mod, "_atomic_write_json", _fail_state_write)
    res_split = await plugin.push_music_url(
        url="https://example.com/pushed_unsaved.mp3",
        title="Pushed Unsaved",
        auto_push=True,
        add_to_library=True,
        duration_sec=30,
        lyric_text="[00:00]hello",
    )
    assert res_split.is_ok()
    assert res_split.value["pushed"] is True
    assert res_split.value["saved"] is False
    assert "disk full" in res_split.value["save_error"]
    assert res_split.value["item_id"] == ""
    assert plugin._music_items == []
    assert plugin._lyrics_map == {}
    assert len(push_calls) == 1


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_execute_task_defers_when_running_save_blocked_and_recovers_on_oserror(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _make_plugin(tmp_path)
    monkeypatch.setattr(plugin, "_ensure_scheduler_task", lambda: None)

    push_calls: list[dict[str, Any]] = []

    async def _record_push(**kwargs: Any) -> bool:
        push_calls.append(dict(kwargs))
        return True

    monkeypatch.setattr(plugin, "_push_music_link_with_timeout", _record_push)

    past_due = (datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat()
    due_task = {
        "task_id": "t_due",
        "name": "Due Task",
        "status": "pending",
        "trigger_at": past_due,
        "queue": [{"url": "https://example.com/due.mp3", "title": "Due", "duration_sec": 1}],
    }

    # Case 1: valid task alongside invalid music_items while corrupt backup fails -> save_ok=False keeps pending & backs off.
    plugin._state_file.parent.mkdir(parents=True, exist_ok=True)
    plugin._state_file.write_text(
        json.dumps({"music_items": {}, "tasks": [due_task]}),
        encoding="utf-8",
    )
    real_replace = music_pusher_mod.os.replace

    def _fail_backup_replace(src: Path, dst: Path) -> None:
        if ".corrupt." in dst.name:
            raise PermissionError("file locked")
        real_replace(src, dst)

    monkeypatch.setattr(music_pusher_mod.os, "replace", _fail_backup_replace)
    assert (await plugin.on_start()).is_ok()
    assert "t_due" in plugin._tasks
    assert plugin._degraded_state["scheduler_state.json"]["backup_path"] == ""

    await plugin._execute_task("t_due")
    assert push_calls == []
    assert plugin._tasks["t_due"]["status"] == "pending"
    assert plugin._active_task_id is None
    assert plugin._active_execution_task is None
    assert plugin._scheduler_save_backoff_until > time.monotonic()

    spawned: list[str] = []
    monkeypatch.setattr(plugin, "_spawn_execute_task", lambda tid: spawned.append(tid))
    await plugin._execute_due_once()
    assert spawned == []

    # Case 2: state save raises OSError when persisting running -> rolls back to pending & releases active lock.
    monkeypatch.setattr(music_pusher_mod.os, "replace", real_replace)
    plugin._scheduler_save_backoff_until = 0.0
    plugin._degraded_state.clear()
    plugin._state_file.write_text(
        json.dumps({"music_items": [], "tasks": [due_task]}),
        encoding="utf-8",
    )
    assert (await plugin.on_start()).is_ok()

    real_atomic_write_json = music_pusher_mod._atomic_write_json
    monkeypatch.setattr(
        music_pusher_mod,
        "_atomic_write_json",
        lambda *_a, **_kw: (_ for _ in ()).throw(OSError("disk full")),
    )
    await plugin._execute_task("t_due")
    assert push_calls == []
    assert plugin._tasks["t_due"]["status"] == "pending"
    assert plugin._active_task_id is None
    assert plugin._active_execution_task is None
    assert plugin._scheduler_save_backoff_until > time.monotonic()

    # Case 3: once disk recovers and backoff elapses, the task runs to completion cleanly.
    monkeypatch.setattr(music_pusher_mod, "_atomic_write_json", real_atomic_write_json)
    plugin._scheduler_save_backoff_until = 0.0
    real_sleep = music_pusher_mod.asyncio.sleep

    async def _fast_sleep(_sec: float) -> None:
        await real_sleep(0)

    monkeypatch.setattr(music_pusher_mod.asyncio, "sleep", _fast_sleep)
    plugin._tasks["t_due"]["queue"][0]["duration_sec"] = 1
    await plugin._execute_task("t_due")
    assert len(push_calls) == 1
    assert plugin._tasks["t_due"]["status"] == "completed"
    assert plugin._active_task_id is None
    assert plugin._active_execution_task is None


@pytest.mark.plugin_unit
@pytest.mark.asyncio
async def test_update_and_delete_schedule_task_persist_state_before_gc_and_preserve_tombstone_on_map_save_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    plugin = _make_plugin(tmp_path)
    monkeypatch.setattr(plugin, "_ensure_scheduler_task", lambda: None)
    monkeypatch.setattr(
        plugin,
        "_validate_queue_urls_allowlistable",
        lambda _q: asyncio.sleep(0, result=""),
    )

    plugin._upload_dir.mkdir(parents=True, exist_ok=True)
    audio_file = plugin._upload_dir / "keep_before_save.mp3"
    audio_file.write_bytes(b"ID3-KEEP")
    audio_url = "http://127.0.0.1:48916/plugin/music_pusher/ui/uploads/keep_before_save.mp3"

    plugin._upload_name_map = {
        "keep_before_save.mp3": {
            "stored_filename": "keep_before_save.mp3",
            "original_filename": "keep_before_save.mp3",
            "title": "Keep Before Save",
            "artist": "Artist",
            "duration_sec": 30,
            "deleted": True,
        }
    }
    plugin._tasks = {
        "t_ref": {
            "task_id": "t_ref",
            "name": "Ref Task",
            "status": "pending",
            "trigger_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(),
            "queue": [{"url": audio_url, "title": "Keep Before Save", "duration_sec": 30}],
        }
    }
    plugin._save_upload_name_map_locked()
    plugin._save_state_locked()

    real_atomic_write_json = music_pusher_mod._atomic_write_json

    def _fail_state_save(path: Path, payload: object) -> None:
        if path == plugin._state_file:
            raise OSError("state write failed")
        real_atomic_write_json(path, payload)

    monkeypatch.setattr(music_pusher_mod, "_atomic_write_json", _fail_state_save)

    # 1. update_schedule_task failure must roll back task and MUST NOT GC keep_before_save.mp3.
    with pytest.raises(OSError, match="state write failed"):
        await plugin.update_schedule_task(
            task_id="t_ref",
            queue_items=[{"url": "https://example.com/replacement.mp3", "title": "New"}],
        )
    assert plugin._tasks["t_ref"]["queue"][0]["url"] == audio_url
    assert audio_file.is_file()
    assert "keep_before_save.mp3" in plugin._upload_name_map

    # 2. delete_schedule_task failure must restore task in memory and MUST NOT GC keep_before_save.mp3.
    with pytest.raises(OSError, match="state write failed"):
        await plugin.delete_schedule_task(task_id="t_ref")
    assert "t_ref" in plugin._tasks
    assert audio_file.is_file()
    assert "keep_before_save.mp3" in plugin._upload_name_map

    # 3. state save succeeds, upload_name_map save fails -> task deletion still succeeds and tombstone remains for retry.
    def _fail_map_save(path: Path, payload: object) -> None:
        if path == plugin._upload_name_map_file:
            raise OSError("map write failed")
        real_atomic_write_json(path, payload)

    monkeypatch.setattr(music_pusher_mod, "_atomic_write_json", _fail_map_save)
    del_res = await plugin.delete_schedule_task(task_id="t_ref")
    assert del_res.is_ok()
    assert del_res.value["deleted"] is True
    assert "t_ref" not in plugin._tasks
    assert "keep_before_save.mp3" in plugin._upload_name_map
    assert plugin._upload_name_map["keep_before_save.mp3"]["deleted"] is True
    assert any("Failed to persist upload_name_map" in msg for msg in plugin.logger.warnings)
