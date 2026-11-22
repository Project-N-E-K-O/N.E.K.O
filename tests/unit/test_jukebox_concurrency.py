import asyncio
import hashlib
import io
import json
import threading
import zipfile
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
    await asyncio.to_thread(meta_started.wait, 3)

    # While upload_songs is paused in metadata extraction, delete old_song & hide other_song
    del_res = await jukebox_router.delete_song("old_song")
    vis_res = await jukebox_router.update_song_visibility("other_song", visible=False)
    assert del_res["success"] is True
    assert vis_res["success"] is True

    meta_release.set()
    up_res = await upload_task
    assert up_res["success"] is True

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

    md5_started = threading.Event()
    md5_release = threading.Event()
    orig_md5 = jukebox_router.calculate_md5

    def _paused_md5(path):
        md5_started.set()
        assert md5_release.wait(3)
        return orig_md5(path)

    monkeypatch.setattr(jukebox_router, "calculate_md5", _paused_md5)

    import_task = asyncio.create_task(
        jukebox_router.import_config(UploadFile(filename="pack.zip", file=buf))
    )
    await asyncio.to_thread(md5_started.wait, 3)

    await jukebox_router.delete_song("drop_song")
    await jukebox_router.update_song_visibility("keep_song", visible=False)

    md5_release.set()
    res = await import_task
    assert res["success"] is True

    reloaded = jukebox_router.JukeboxConfig(cm)
    assert "drop_song" not in reloaded.data["songs"]
    assert reloaded.data["songs"]["keep_song"]["visible"] is False
    assert any(s.get("name") == "Imported" for s in reloaded.data["songs"].values())
