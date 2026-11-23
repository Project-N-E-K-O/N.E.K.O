import asyncio
import hashlib
import io
import json
import os
import shutil
import tempfile
import threading
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.datastructures import UploadFile

from main_routers import jukebox_router


def _make_cm(tmp_path):
    app_docs_dir = tmp_path / "app_docs"
    app_docs_dir.mkdir(parents=True, exist_ok=True)
    return SimpleNamespace(app_docs_dir=app_docs_dir)


def _upload_file(filename: str, content: bytes) -> UploadFile:
    return UploadFile(filename=filename, file=io.BytesIO(content))


@pytest.mark.asyncio
async def test_f0020_upload_songs_concurrent_delete_and_visibility_not_lost(monkeypatch, tmp_path):
    cm = _make_cm(tmp_path)
    monkeypatch.setattr(jukebox_router, "get_config_manager", lambda: cm)

    # Seed an existing user song and hide/modify a builtin or existing song concurrently
    cfg = jukebox_router.JukeboxConfig(cm)
    existing_audio = cfg.songs_dir / "old_song.mp3"
    existing_audio.write_bytes(b"old-bytes")
    old_md5 = hashlib.md5(b"old-bytes").hexdigest()
    cfg.data["songs"]["old_song"] = {
        "id": "old_song",
        "name": "Old Song",
        "artist": "A",
        "audio": "songs/old_song.mp3",
        "audioMd5": old_md5,
        "visible": True,
    }
    cfg.data["songs"]["other_song"] = {
        "id": "other_song",
        "name": "Other Song",
        "artist": "B",
        "audio": "songs/other_song.mp3",
        "audioMd5": "other-md5",
        "visible": True,
    }
    cfg.data["md5Index"]["songs"][old_md5] = "old_song"
    cfg.save()

    meta_started = threading.Event()
    meta_release = threading.Event()

    def _blocking_extract(_path):
        meta_started.set()
        assert meta_release.wait(3)
        return {"name": "Uploaded Track", "artist": "Tester"}

    monkeypatch.setattr(jukebox_router, "extract_audio_metadata", _blocking_extract)

    upload_task = asyncio.create_task(
        jukebox_router.upload_songs(files=[_upload_file("fresh.mp3", b"fresh-audio-bytes")], metadata="[]")
    )
    assert await asyncio.to_thread(meta_started.wait, 3)

    # While upload_songs is paused in metadata extraction, delete old_song & hide other_song
    del_res = await jukebox_router.delete_song("old_song")
    vis_res = await jukebox_router.update_song_visibility("other_song", visible=False)
    assert del_res["success"] is True
    assert vis_res["success"] is True

    meta_release.set()
    up_res = await upload_task
    assert up_res["success"] is True
    assert up_res["song"]["name"] == "Uploaded Track"

    reloaded = jukebox_router.JukeboxConfig(cm)
    assert "old_song" not in reloaded.data["songs"]
    assert reloaded.data["songs"]["other_song"]["visible"] is False
    assert up_res["song"]["id"] in reloaded.data["songs"]


@pytest.mark.asyncio
async def test_f0020_concurrent_duplicate_md5_and_same_filename_uploads(monkeypatch, tmp_path):
    cm = _make_cm(tmp_path)
    monkeypatch.setattr(jukebox_router, "get_config_manager", lambda: cm)
    monkeypatch.setattr(jukebox_router, "extract_audio_metadata", lambda _p: {})

    # 1. Two concurrent uploads with identical bytes -> second detects MD5 duplicate and cleans temp file
    barrier = threading.Barrier(2)
    orig_md5 = jukebox_router.calculate_md5

    def _sync_md5(path):
        barrier.wait(timeout=3)
        return orig_md5(path)

    monkeypatch.setattr(jukebox_router, "calculate_md5", _sync_md5)

    r1, r2 = await asyncio.gather(
        jukebox_router.upload_songs(files=[_upload_file("dup.mp3", b"same-content")], metadata="[]"),
        jukebox_router.upload_songs(files=[_upload_file("dup.mp3", b"same-content")], metadata="[]"),
    )
    outcomes = [r1["success"], r2["success"]]
    assert outcomes.count(True) == 1
    assert outcomes.count(False) == 1
    cfg = jukebox_router.JukeboxConfig(cm)
    winner, duplicate = (r1, r2) if r1["success"] else (r2, r1)
    winner_id = winner["song"]["id"]
    assert duplicate["error"] == f"歌曲已存在: {winner_id}"
    assert cfg.data["md5Index"]["songs"][hashlib.md5(b"same-content").hexdigest()] == winner_id
    assert not list(cfg.songs_dir.glob(".tmp_upload_*"))

    # 2. Two concurrent action uploads with same filename but different bytes -> distinct IDs and files
    barrier2 = threading.Barrier(2)

    def _sync_md5_actions(path):
        barrier2.wait(timeout=3)
        return orig_md5(path)

    monkeypatch.setattr(jukebox_router, "calculate_md5", _sync_md5_actions)
    a1, a2 = await asyncio.gather(
        jukebox_router.upload_actions(files=[_upload_file("dance.vmd", b"vmd-1")], metadata="[]"),
        jukebox_router.upload_actions(files=[_upload_file("dance.vmd", b"vmd-2")], metadata="[]"),
    )
    assert a1["success"] is True and a2["success"] is True
    assert a1["action"]["id"] != a2["action"]["id"]
    assert a1["action"]["file"] != a2["action"]["file"]
    reloaded = jukebox_router.JukeboxConfig(cm)
    assert a1["action"]["id"] in reloaded.data["actions"]
    assert a2["action"]["id"] in reloaded.data["actions"]


@pytest.mark.asyncio
async def test_f0020_import_config_does_not_clobber_concurrent_delete(monkeypatch, tmp_path):
    cm = _make_cm(tmp_path)
    monkeypatch.setattr(jukebox_router, "get_config_manager", lambda: cm)

    cfg = jukebox_router.JukeboxConfig(cm)
    (cfg.songs_dir / "keep.mp3").write_bytes(b"keep")
    (cfg.songs_dir / "drop.mp3").write_bytes(b"drop")
    cfg.data["songs"]["keep_song"] = {
        "id": "keep_song",
        "name": "Keep",
        "audio": "songs/keep.mp3",
        "audioMd5": hashlib.md5(b"keep").hexdigest(),
        "visible": True,
    }
    cfg.data["songs"]["drop_song"] = {
        "id": "drop_song",
        "name": "Drop",
        "audio": "songs/drop.mp3",
        "audioMd5": hashlib.md5(b"drop").hexdigest(),
        "visible": True,
    }
    cfg.save()

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("songs/imported.mp3", b"imported-bytes")
        zf.writestr(
            "config.json",
            json.dumps(
                {
                    "songs": {
                        "imp_1": {
                            "id": "imp_1",
                            "name": "Imported",
                            "audio": "songs/imported.mp3",
                        }
                    },
                    "actions": {},
                    "bindings": {},
                }
            ),
        )
    buf.seek(0)

    copy_started = threading.Event()
    copy_release = threading.Event()
    orig_copy = shutil.copy2

    def _paused_copy(src, dst):
        copy_started.set()
        assert copy_release.wait(3)
        return orig_copy(src, dst)

    monkeypatch.setattr(shutil, "copy2", _paused_copy)

    import_task = asyncio.create_task(
        jukebox_router.import_config(UploadFile(filename="pack.zip", file=buf))
    )
    assert await asyncio.to_thread(copy_started.wait, 3)

    await jukebox_router.delete_song("drop_song")
    await jukebox_router.update_song_visibility("keep_song", visible=False)

    copy_release.set()
    res = await import_task
    assert res["success"] is True

    reloaded = jukebox_router.JukeboxConfig(cm)
    assert "drop_song" not in reloaded.data["songs"]
    assert reloaded.data["songs"]["keep_song"]["visible"] is False
    assert any(s.get("name") == "Imported" for s in reloaded.data["songs"].values())


class _BlockingUploadStream(io.BytesIO):
    def __init__(self, data: bytes, started: threading.Event, release: threading.Event):
        super().__init__(data)
        self.started = started
        self.release = release
        self.read_finished = threading.Event()
        self.closed_before_read_finished = False
        self._blocked_once = False

    def read(self, size: int = -1):
        if not self._blocked_once:
            self._blocked_once = True
            self.started.set()
            assert self.release.wait(5)
        chunk = super().read(size)
        if not chunk:
            self.read_finished.set()
        return chunk

    def close(self):
        if not self.read_finished.is_set():
            self.closed_before_read_finished = True
        super().close()


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["songs", "actions"])
async def test_f0020_cancelled_error_mid_calculate_md5_cleans_up_temp_files(monkeypatch, tmp_path, kind):
    cm = _make_cm(tmp_path)
    monkeypatch.setattr(jukebox_router, "get_config_manager", lambda: cm)
    monkeypatch.setattr(jukebox_router, "extract_audio_metadata", lambda _p: {})

    cfg = jukebox_router.JukeboxConfig(cm)
    target_dir = cfg.songs_dir if kind == "songs" else cfg.actions_dir
    ext = ".mp3" if kind == "songs" else ".vmd"

    md5_calls = 0
    second_md5_started = threading.Event()
    second_md5_release = threading.Event()
    orig_md5 = jukebox_router.calculate_md5

    def _paused_on_second_md5(path):
        nonlocal md5_calls
        md5_calls += 1
        if md5_calls == 2:
            second_md5_started.set()
            assert second_md5_release.wait(5)
        return orig_md5(path)

    monkeypatch.setattr(jukebox_router, "calculate_md5", _paused_on_second_md5)

    files = [
        _upload_file(f"first{ext}", b"first-bytes"),
        _upload_file(f"second{ext}", b"second-bytes"),
    ]
    coro = (
        jukebox_router.upload_songs(files=files, metadata="[]")
        if kind == "songs"
        else jukebox_router.upload_actions(files=files, metadata="[]")
    )
    upload_task = asyncio.create_task(coro)
    await asyncio.to_thread(second_md5_started.wait, 5)
    assert len(list(target_dir.glob(".tmp_upload_*"))) == 2

    upload_task.cancel()
    await asyncio.sleep(0)
    second_md5_release.set()

    with pytest.raises(asyncio.CancelledError):
        await upload_task

    assert list(target_dir.glob(".tmp_upload_*")) == []
    assert all(f.file.closed for f in files)


@pytest.mark.asyncio
async def test_f0020_cancelled_error_mid_extract_audio_metadata_cleans_up_temp_files(monkeypatch, tmp_path):
    cm = _make_cm(tmp_path)
    monkeypatch.setattr(jukebox_router, "get_config_manager", lambda: cm)
    cfg = jukebox_router.JukeboxConfig(cm)

    meta_started = threading.Event()
    meta_release = threading.Event()

    def _paused_extract(_path):
        meta_started.set()
        assert meta_release.wait(5)
        return {"name": "Track"}

    monkeypatch.setattr(jukebox_router, "extract_audio_metadata", _paused_extract)

    upload_file = _upload_file("meta_cancel.mp3", b"audio-bytes")
    upload_task = asyncio.create_task(
        jukebox_router.upload_songs(files=[upload_file], metadata="[]")
    )
    await asyncio.to_thread(meta_started.wait, 5)
    assert len(list(cfg.songs_dir.glob(".tmp_upload_*"))) == 1

    upload_task.cancel()
    await asyncio.sleep(0)
    meta_release.set()

    with pytest.raises(asyncio.CancelledError):
        await upload_task

    assert list(cfg.songs_dir.glob(".tmp_upload_*")) == []
    assert upload_file.file.closed


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["songs", "actions"])
async def test_f0020_cancelled_error_mid_stage_upload_waits_for_retirement_and_cleans_up(
    monkeypatch, tmp_path, kind
):
    cm = _make_cm(tmp_path)
    monkeypatch.setattr(jukebox_router, "get_config_manager", lambda: cm)
    monkeypatch.setattr(jukebox_router, "extract_audio_metadata", lambda _p: {})
    cfg = jukebox_router.JukeboxConfig(cm)

    target_dir = cfg.songs_dir if kind == "songs" else cfg.actions_dir
    ext = ".mp3" if kind == "songs" else ".vmd"

    stage_started = threading.Event()
    stage_release = threading.Event()
    stream = _BlockingUploadStream(b"staged-stream-content", stage_started, stage_release)
    upload_file = UploadFile(filename=f"cancel_stage{ext}", file=stream)

    coro = (
        jukebox_router.upload_songs(files=[upload_file], metadata="[]")
        if kind == "songs"
        else jukebox_router.upload_actions(files=[upload_file], metadata="[]")
    )
    upload_task = asyncio.create_task(coro)
    await asyncio.to_thread(stage_started.wait, 5)
    assert len(list(target_dir.glob(".tmp_upload_*"))) == 1

    upload_task.cancel()
    await asyncio.sleep(0.02)

    assert not upload_task.done()
    assert not stream.closed
    assert not stream.closed_before_read_finished

    stage_release.set()
    with pytest.raises(asyncio.CancelledError):
        await upload_task

    assert stream.read_finished.is_set()
    assert stream.closed
    assert not stream.closed_before_read_finished
    assert list(target_dir.glob(".tmp_upload_*")) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["songs", "actions"])
async def test_f0020_cancelled_error_mid_os_replace_after_partial_batch_move(
    monkeypatch, tmp_path, kind
):
    cm = _make_cm(tmp_path)
    monkeypatch.setattr(jukebox_router, "get_config_manager", lambda: cm)
    monkeypatch.setattr(jukebox_router, "extract_audio_metadata", lambda _p: {})
    cfg = jukebox_router.JukeboxConfig(cm)

    target_dir = cfg.songs_dir if kind == "songs" else cfg.actions_dir
    ext = ".mp3" if kind == "songs" else ".vmd"

    replace_calls = 0
    second_started = threading.Event()
    second_release = threading.Event()
    second_finished = threading.Event()
    second_error = None
    orig_replace = os.replace

    def _paused_second_replace(src, dst):
        nonlocal replace_calls, second_error
        replace_calls += 1
        if replace_calls == 2:
            second_started.set()
            assert second_release.wait(5)
            try:
                return orig_replace(src, dst)
            except BaseException as exc:
                second_error = exc
                raise
            finally:
                second_finished.set()
        return orig_replace(src, dst)

    monkeypatch.setattr(jukebox_router.os, "replace", _paused_second_replace)

    original_unlink = Path.unlink
    locked_once = False

    def transient_lock(path, *args, **kwargs):
        nonlocal locked_once
        if path == target_dir / f"part2{ext}" and not locked_once:
            locked_once = True
            raise PermissionError("file temporarily locked")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", transient_lock)

    files = [
        _upload_file(f"part1{ext}", b"part1-bytes"),
        _upload_file(f"part2{ext}", b"part2-bytes"),
        _upload_file(f"part3{ext}", b"part3-bytes"),
    ]
    coro = (
        jukebox_router.upload_songs(files=files, metadata="[]")
        if kind == "songs"
        else jukebox_router.upload_actions(files=files, metadata="[]")
    )
    upload_task = asyncio.create_task(coro)
    await asyncio.to_thread(second_started.wait, 5)

    upload_task.cancel()
    await asyncio.sleep(0.02)
    assert not upload_task.done()

    second_release.set()
    with pytest.raises(asyncio.CancelledError):
        await upload_task

    assert locked_once
    assert second_finished.is_set()
    assert second_error is None
    assert list(target_dir.glob(".tmp_upload_*")) == []

    disk_files = sorted(p.name for p in target_dir.iterdir() if p.is_file())
    reloaded = jukebox_router.JukeboxConfig(cm)
    collection = reloaded.data["songs"] if kind == "songs" else reloaded.data["actions"]
    field = "audio" if kind == "songs" else "file"
    config_files = sorted(
        Path(entry[field]).name
        for entry in collection.values()
        if not entry.get("isBuiltin", False)
    )
    assert disk_files == [f"part1{ext}"]
    assert config_files == [f"part1{ext}"]


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["songs", "actions"])
async def test_f0020_cancelled_error_mid_dup_unlink_after_partial_batch_move(
    monkeypatch, tmp_path, kind
):
    cm = _make_cm(tmp_path)
    monkeypatch.setattr(jukebox_router, "get_config_manager", lambda: cm)
    monkeypatch.setattr(jukebox_router, "extract_audio_metadata", lambda _p: {})
    cfg = jukebox_router.JukeboxConfig(cm)

    target_dir = cfg.songs_dir if kind == "songs" else cfg.actions_dir
    ext = ".mp3" if kind == "songs" else ".vmd"
    existing_bytes = b"already-present"
    existing_md5 = hashlib.md5(existing_bytes).hexdigest()
    (target_dir / f"existing{ext}").write_bytes(existing_bytes)
    if kind == "songs":
        cfg.data["songs"]["existing"] = {
            "id": "existing",
            "name": "Existing",
            "audio": f"songs/existing{ext}",
            "audioMd5": existing_md5,
            "visible": True,
        }
        cfg.data["md5Index"]["songs"][existing_md5] = "existing"
    else:
        cfg.data["actions"]["existing"] = {
            "id": "existing",
            "name": "Existing",
            "file": f"actions/existing{ext}",
            "fileMd5": existing_md5,
            "visible": True,
        }
        cfg.data["md5Index"]["actions"][existing_md5] = "existing"
    cfg.save()

    dup_unlink_started = threading.Event()
    dup_unlink_release = threading.Event()
    dup_unlink_finished = threading.Event()
    dup_unlink_error = None
    orig_unlink = Path.unlink
    unlink_intercepted = False

    def _paused_dup_unlink(self, *args, **kwargs):
        nonlocal unlink_intercepted, dup_unlink_error
        if (
            not unlink_intercepted
            and self.parent == target_dir
            and self.name.startswith(".tmp_upload_")
        ):
            unlink_intercepted = True
            dup_unlink_started.set()
            assert dup_unlink_release.wait(5)
            try:
                return orig_unlink(self, *args, **kwargs)
            except BaseException as exc:
                dup_unlink_error = exc
                raise
            finally:
                dup_unlink_finished.set()
        return orig_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", _paused_dup_unlink)

    files = [
        _upload_file(f"new_part1{ext}", b"brand-new-1"),
        _upload_file(f"dup_existing{ext}", existing_bytes),
        _upload_file(f"new_part3{ext}", b"brand-new-3"),
    ]
    coro = (
        jukebox_router.upload_songs(files=files, metadata="[]")
        if kind == "songs"
        else jukebox_router.upload_actions(files=files, metadata="[]")
    )
    upload_task = asyncio.create_task(coro)
    await asyncio.to_thread(dup_unlink_started.wait, 5)

    upload_task.cancel()
    await asyncio.sleep(0.02)
    assert not upload_task.done()

    dup_unlink_release.set()
    with pytest.raises(asyncio.CancelledError):
        await upload_task

    assert dup_unlink_finished.is_set()
    assert dup_unlink_error is None
    assert list(target_dir.glob(".tmp_upload_*")) == []

    disk_files = sorted(p.name for p in target_dir.iterdir() if p.is_file())
    reloaded = jukebox_router.JukeboxConfig(cm)
    collection = reloaded.data["songs"] if kind == "songs" else reloaded.data["actions"]
    field = "audio" if kind == "songs" else "file"
    config_files = sorted(
        Path(entry[field]).name
        for entry in collection.values()
        if not entry.get("isBuiltin", False)
    )
    assert disk_files == sorted([f"existing{ext}", f"new_part1{ext}"])
    assert config_files == sorted([f"existing{ext}", f"new_part1{ext}"])


@pytest.mark.asyncio
async def test_f0020_import_config_cancelled_error_mid_copy2_retires_and_rolls_back(
    monkeypatch, tmp_path
):
    cm = _make_cm(tmp_path)
    monkeypatch.setattr(jukebox_router, "get_config_manager", lambda: cm)
    cfg = jukebox_router.JukeboxConfig(cm)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("songs/imp1.mp3", b"imp1-bytes")
        zf.writestr("songs/imp2.mp3", b"imp2-bytes")
        zf.writestr("actions/act1.vmd", b"act1-bytes")
        zf.writestr(
            "config.json",
            json.dumps(
                {
                    "songs": {
                        "imp1": {"id": "imp1", "name": "Imp1", "audio": "songs/imp1.mp3"},
                        "imp2": {"id": "imp2", "name": "Imp2", "audio": "songs/imp2.mp3"},
                    },
                    "actions": {
                        "act1": {"id": "act1", "name": "Act1", "file": "actions/act1.vmd"},
                    },
                    "bindings": {},
                }
            ),
        )
    buf.seek(0)

    copy2_calls = 0
    second_started = threading.Event()
    second_release = threading.Event()
    second_finished = threading.Event()
    second_error = None
    orig_copy2 = shutil.copy2

    def _paused_second_copy2(src, dst, *args, **kwargs):
        nonlocal copy2_calls, second_error
        copy2_calls += 1
        if copy2_calls == 2:
            second_started.set()
            assert second_release.wait(5)
            try:
                return orig_copy2(src, dst, *args, **kwargs)
            except BaseException as exc:
                second_error = exc
                raise
            finally:
                second_finished.set()
        return orig_copy2(src, dst, *args, **kwargs)

    monkeypatch.setattr(jukebox_router.shutil, "copy2", _paused_second_copy2)

    import_task = asyncio.create_task(
        jukebox_router.import_config(UploadFile(filename="pack.zip", file=buf))
    )
    await asyncio.to_thread(second_started.wait, 5)

    import_task.cancel()
    await asyncio.sleep(0.02)
    assert not import_task.done()

    second_release.set()
    with pytest.raises(asyncio.CancelledError):
        await import_task

    assert second_finished.is_set()
    assert second_error is None
    assert [p for p in cfg.songs_dir.iterdir() if p.is_file()] == []
    assert [p for p in cfg.actions_dir.iterdir() if p.is_file()] == []
    reloaded = jukebox_router.JukeboxConfig(cm)
    assert all(s.get("isBuiltin", False) for s in reloaded.data["songs"].values())
    assert all(a.get("isBuiltin", False) for a in reloaded.data["actions"].values())


@pytest.mark.asyncio
async def test_f0020_import_config_cancelled_error_mid_copyfileobj_retires_before_temp_cleanup(
    monkeypatch, tmp_path
):
    cm = _make_cm(tmp_path)
    monkeypatch.setattr(jukebox_router, "get_config_manager", lambda: cm)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("songs/imp1.mp3", b"imp1-bytes")
        zf.writestr("config.json", json.dumps({"songs": {}, "actions": {}, "bindings": {}}))

    copy_started = threading.Event()
    copy_release = threading.Event()
    stream = _BlockingUploadStream(buf.getvalue(), copy_started, copy_release)

    import_task = asyncio.create_task(
        jukebox_router.import_config(UploadFile(filename="pack.zip", file=stream))
    )
    assert await asyncio.to_thread(copy_started.wait, 5)

    import_task.cancel()
    await asyncio.sleep(0.02)
    assert not import_task.done()

    copy_release.set()
    with pytest.raises(asyncio.CancelledError):
        await import_task

    assert stream.read_finished.is_set()


@pytest.mark.asyncio
async def test_f0020_import_config_cancelled_error_mid_zip_extract_copyfileobj_retires(
    monkeypatch, tmp_path
):
    cm = _make_cm(tmp_path)
    monkeypatch.setattr(jukebox_router, "get_config_manager", lambda: cm)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("songs/imp1.mp3", b"imp1-bytes")
        zf.writestr("config.json", json.dumps({"songs": {}, "actions": {}, "bindings": {}}))
    buf.seek(0)

    copy_calls = 0
    extract_started = threading.Event()
    extract_release = threading.Event()
    extract_finished = threading.Event()
    extract_error = None
    orig_copyfileobj = shutil.copyfileobj

    def _paused_second_copyfileobj(src, dst, *args, **kwargs):
        nonlocal copy_calls, extract_error
        copy_calls += 1
        if copy_calls == 2:
            extract_started.set()
            assert extract_release.wait(5)
            try:
                return orig_copyfileobj(src, dst, *args, **kwargs)
            except BaseException as exc:
                extract_error = exc
                raise
            finally:
                extract_finished.set()
        return orig_copyfileobj(src, dst, *args, **kwargs)

    monkeypatch.setattr(jukebox_router.shutil, "copyfileobj", _paused_second_copyfileobj)

    import_task = asyncio.create_task(
        jukebox_router.import_config(UploadFile(filename="pack.zip", file=buf))
    )
    assert await asyncio.to_thread(extract_started.wait, 5)

    import_task.cancel()
    await asyncio.sleep(0.02)
    assert not import_task.done()

    extract_release.set()
    with pytest.raises(asyncio.CancelledError):
        await import_task

    assert extract_finished.is_set()
    assert extract_error is None


@pytest.mark.asyncio
async def test_f0020_export_config_cancelled_error_retires_copy2_and_cleans_temp_dir(
    monkeypatch, tmp_path
):
    cm = _make_cm(tmp_path)
    monkeypatch.setattr(jukebox_router, "get_config_manager", lambda: cm)
    cfg = jukebox_router.JukeboxConfig(cm)
    (cfg.songs_dir / "track.mp3").write_bytes(b"audio-bytes")
    cfg.data["songs"]["track"] = {
        "id": "track",
        "name": "Track",
        "audio": "songs/track.mp3",
        "audioMd5": hashlib.md5(b"audio-bytes").hexdigest(),
        "visible": True,
    }
    cfg.save()

    created_temp_dirs = []
    orig_mkdtemp = tempfile.mkdtemp

    def _spy_mkdtemp(*args, **kwargs):
        d = orig_mkdtemp(*args, **kwargs)
        created_temp_dirs.append(Path(d))
        return d

    monkeypatch.setattr(jukebox_router.tempfile, "mkdtemp", _spy_mkdtemp)

    copy2_started = threading.Event()
    copy2_release = threading.Event()
    copy2_finished = threading.Event()
    copy2_error = None
    orig_copy2 = shutil.copy2

    def _paused_copy2(src, dst, *args, **kwargs):
        nonlocal copy2_error
        copy2_started.set()
        assert copy2_release.wait(5)
        try:
            return orig_copy2(src, dst, *args, **kwargs)
        except BaseException as exc:
            copy2_error = exc
            raise
        finally:
            copy2_finished.set()

    monkeypatch.setattr(jukebox_router.shutil, "copy2", _paused_copy2)

    export_task = asyncio.create_task(
        jukebox_router.export_config(songIds=None, actionIds=None, includeHidden=True)
    )
    assert await asyncio.to_thread(copy2_started.wait, 5)

    export_task.cancel()
    await asyncio.sleep(0.02)
    assert not export_task.done()

    copy2_release.set()
    with pytest.raises(asyncio.CancelledError):
        await export_task

    assert copy2_finished.is_set()
    assert copy2_error is None
    assert len(created_temp_dirs) == 1
    assert not created_temp_dirs[0].exists()


@pytest.mark.asyncio
async def test_f0020_pack_folder_cancelled_error_retires_copyfileobj_and_cleans_temp_dir(
    monkeypatch, tmp_path
):
    created_temp_dirs = []
    orig_mkdtemp = tempfile.mkdtemp

    def _spy_mkdtemp(*args, **kwargs):
        d = orig_mkdtemp(*args, **kwargs)
        created_temp_dirs.append(Path(d))
        return d

    monkeypatch.setattr(jukebox_router.tempfile, "mkdtemp", _spy_mkdtemp)

    copy_started = threading.Event()
    copy_release = threading.Event()
    stream = _BlockingUploadStream(b"folder-file-bytes", copy_started, copy_release)

    pack_task = asyncio.create_task(
        jukebox_router.pack_folder(files=[UploadFile(filename="sub/file.txt", file=stream)])
    )
    await asyncio.to_thread(copy_started.wait, 5)

    pack_task.cancel()
    await asyncio.sleep(0.02)
    assert not pack_task.done()

    copy_release.set()
    with pytest.raises(asyncio.CancelledError):
        await pack_task

    assert stream.read_finished.is_set()
    assert len(created_temp_dirs) == 1
    assert not created_temp_dirs[0].exists()



@pytest.mark.asyncio
@pytest.mark.parametrize("kind,ext", [("songs", "mp3"), ("actions", "vmd")])
async def test_upload_rolls_back_every_new_file_on_config_save_failure(monkeypatch, tmp_path, kind, ext):
    cm = _make_cm(tmp_path)
    monkeypatch.setattr(jukebox_router, "get_config_manager", lambda: cm)
    monkeypatch.setattr(jukebox_router, "extract_audio_metadata", lambda _path: {})
    cfg = jukebox_router.JukeboxConfig(cm)
    folder = getattr(cfg, f"{kind}_dir")
    existing = folder / f"original.{ext}"
    existing.write_bytes(b"keep")

    async def fail_save(self):
        assert (folder / f"first.{ext}").is_file()
        assert (folder / f"second.{ext}").is_file()
        raise OSError("config save failed")

    monkeypatch.setattr(jukebox_router.JukeboxConfig, "asave", fail_save)
    upload = jukebox_router.upload_songs if kind == "songs" else jukebox_router.upload_actions
    with pytest.raises(OSError, match="config save failed"):
        await upload(files=[_upload_file(f"{name}.{ext}", name.encode()) for name in ("first", "second")], metadata="[]")
    assert list(folder.iterdir()) == [existing]


def test_failed_staging_frees_partial_file_before_return(monkeypatch, tmp_path):
    def fail_copy(src, dst):
        dst.write(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(shutil, "copyfileobj", fail_copy)
    with pytest.raises(OSError, match="disk full"):
        jukebox_router._stage_upload_to_temp(io.BytesIO(), tmp_path, ".mp3", [])
    assert not list(tmp_path.iterdir())


@pytest.mark.asyncio
async def test_upload_and_import_staging_fail_fast_on_permission_error(monkeypatch, tmp_path):
    cm = _make_cm(tmp_path)
    monkeypatch.setattr(jukebox_router, "get_config_manager", lambda: cm)
    cfg = jukebox_router.JukeboxConfig(cm)

    real_open = os.open
    real_mkdir = os.mkdir
    open_attempts = 0
    mkdir_attempts = 0

    def deny_open(path, flags, mode=0o777, *args, **kwargs):
        nonlocal open_attempts
        if Path(path).parent == cfg.songs_dir:
            open_attempts += 1
            raise PermissionError(13, "access denied", os.fspath(path))
        return real_open(path, flags, mode, *args, **kwargs)

    def deny_mkdir(path, mode=0o777, *args, **kwargs):
        nonlocal mkdir_attempts
        if Path(path).parent == cfg.songs_dir:
            mkdir_attempts += 1
            raise PermissionError(13, "access denied", os.fspath(path))
        return real_mkdir(path, mode, *args, **kwargs)

    monkeypatch.setattr(jukebox_router.os, "open", deny_open)
    monkeypatch.setattr(jukebox_router.os, "mkdir", deny_mkdir)

    # 1. Direct upload staging and upload_songs fail immediately on PermissionError (1 attempt)
    with pytest.raises(PermissionError, match="access denied"):
        jukebox_router._stage_upload_to_temp(io.BytesIO(b"audio"), cfg.songs_dir, ".mp3", [])
    assert open_attempts == 1

    res = await asyncio.wait_for(
        jukebox_router.upload_songs(files=[_upload_file("denied.mp3", b"audio")], metadata="[]"),
        timeout=2,
    )
    assert res["success"] is False
    assert "access denied" in res["error"]
    assert open_attempts == 2

    # 2. Import staging directory fails immediately on PermissionError (1 attempt)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("songs/imported.mp3", b"imported-bytes")
        zf.writestr(
            "config.json",
            json.dumps(
                {
                    "songs": {"s1": {"id": "s1", "name": "Song", "audio": "songs/imported.mp3"}},
                    "actions": {},
                    "bindings": {},
                }
            ),
        )

    with pytest.raises(PermissionError, match="access denied"):
        await asyncio.wait_for(
            jukebox_router.import_config(file=_upload_file("pkg.zip", buf.getvalue())),
            timeout=2,
        )
    assert mkdir_attempts == 1

    # 3. FileExistsError collisions respect the fixed retry cap
    open_collisions = 0
    mkdir_collisions = 0

    def always_exists_open(path, flags, mode=0o777, *args, **kwargs):
        nonlocal open_collisions
        if Path(path).parent == cfg.songs_dir:
            open_collisions += 1
            raise FileExistsError(17, "exists", os.fspath(path))
        return real_open(path, flags, mode, *args, **kwargs)

    def always_exists_mkdir(path, mode=0o777, *args, **kwargs):
        nonlocal mkdir_collisions
        if Path(path).parent == cfg.songs_dir:
            mkdir_collisions += 1
            raise FileExistsError(17, "exists", os.fspath(path))
        return real_mkdir(path, mode, *args, **kwargs)

    monkeypatch.setattr(jukebox_router.os, "open", always_exists_open)
    monkeypatch.setattr(jukebox_router.os, "mkdir", always_exists_mkdir)

    with pytest.raises(FileExistsError):
        jukebox_router._create_staging_temp_file(cfg.songs_dir, ".mp3")
    assert open_collisions == jukebox_router._STAGING_CREATE_MAX_TRIES

    with pytest.raises(FileExistsError):
        with jukebox_router._staging_temp_dir(cfg.songs_dir):
            pass
    assert mkdir_collisions == jukebox_router._STAGING_CREATE_MAX_TRIES
