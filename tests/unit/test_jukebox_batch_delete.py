from pathlib import Path

import pytest
from fastapi import HTTPException

from main_routers import jukebox_router


class _FakeJukeboxConfig:
    def __init__(self, data, jukebox_dir):
        self.data = data
        self.jukebox_dir = jukebox_dir
        self.saved = False

    async def asave(self):
        self.saved = True


def _install_fake_config(monkeypatch, fake):
    monkeypatch.setattr(jukebox_router, "get_config_manager", lambda: object())
    monkeypatch.setattr(jukebox_router, "JukeboxConfig", lambda _config_mgr: fake)


@pytest.mark.asyncio
async def test_batch_delete_removes_user_song_and_hides_builtin(monkeypatch, tmp_path):
    jukebox_dir = tmp_path / "jukebox"
    songs_dir = jukebox_dir / "songs"
    songs_dir.mkdir(parents=True)
    audio_file = songs_dir / "user.mp3"
    audio_file.write_bytes(b"audio")

    fake = _FakeJukeboxConfig(
        {
            "songs": {
                "user-song": {
                    "name": "User Song",
                    "audio": "songs/user.mp3",
                    "audioMd5": "user-md5",
                    "visible": True,
                },
                "builtin-song": {
                    "name": "Builtin Song",
                    "audio": "songs/builtin.mp3",
                    "audioMd5": "builtin-md5",
                    "visible": True,
                    "isBuiltin": True,
                },
            },
            "bindings": {
                "user-song": {"action-1": {"offset": 0}},
                "builtin-song": {"action-2": {"offset": 0}},
            },
            "md5Index": {
                "songs": {
                    "user-md5": "user-song",
                    "builtin-md5": "builtin-song",
                },
                "actions": {},
            },
        },
        jukebox_dir,
    )
    _install_fake_config(monkeypatch, fake)

    result = await jukebox_router.batch_delete_songs(
        jukebox_router.BatchDeleteSongsRequest(songIds=["user-song", "builtin-song"])
    )

    assert result["success"] is True
    assert result["partial"] is False
    assert result["deletedCount"] == 1
    assert result["hiddenCount"] == 1
    assert result["failedCount"] == 0
    assert not audio_file.exists()
    assert "user-song" not in fake.data["songs"]
    assert "user-song" not in fake.data["bindings"]
    assert "user-md5" not in fake.data["md5Index"]["songs"]
    assert fake.data["songs"]["builtin-song"]["visible"] is False
    assert fake.saved is True


@pytest.mark.asyncio
async def test_batch_delete_precheck_rejects_unknown_ids(monkeypatch, tmp_path):
    fake = _FakeJukeboxConfig(
        {
            "songs": {
                "known-song": {
                    "name": "Known",
                    "audio": "songs/known.mp3",
                    "visible": True,
                }
            },
            "bindings": {},
            "md5Index": {"songs": {}, "actions": {}},
        },
        tmp_path / "jukebox",
    )
    _install_fake_config(monkeypatch, fake)

    with pytest.raises(HTTPException) as exc_info:
        await jukebox_router.batch_delete_songs(
            jukebox_router.BatchDeleteSongsRequest(songIds=["known-song", "missing-song"])
        )

    assert exc_info.value.status_code == 404
    assert fake.data["songs"]["known-song"]["visible"] is True
    assert fake.saved is False


@pytest.mark.asyncio
async def test_batch_delete_reports_partial_failures(monkeypatch, tmp_path):
    jukebox_dir = tmp_path / "jukebox"
    songs_dir = jukebox_dir / "songs"
    songs_dir.mkdir(parents=True)
    failed_audio = songs_dir / "failed.mp3"
    ok_audio = songs_dir / "ok.mp3"
    failed_audio.write_bytes(b"failed")
    ok_audio.write_bytes(b"ok")

    fake = _FakeJukeboxConfig(
        {
            "songs": {
                "failed-song": {
                    "name": "Failed Song",
                    "audio": "songs/failed.mp3",
                    "audioMd5": "failed-md5",
                    "visible": True,
                },
                "ok-song": {
                    "name": "OK Song",
                    "audio": "songs/ok.mp3",
                    "audioMd5": "ok-md5",
                    "visible": True,
                },
            },
            "bindings": {},
            "md5Index": {
                "songs": {
                    "failed-md5": "failed-song",
                    "ok-md5": "ok-song",
                },
                "actions": {},
            },
        },
        jukebox_dir,
    )
    _install_fake_config(monkeypatch, fake)

    original_unlink = Path.unlink

    def fail_selected_unlink(self, *args, **kwargs):
        if self == failed_audio:
            raise PermissionError("locked")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_selected_unlink)

    result = await jukebox_router.batch_delete_songs(
        jukebox_router.BatchDeleteSongsRequest(songIds=["failed-song", "ok-song"])
    )

    assert result["success"] is False
    assert result["partial"] is True
    assert result["deletedCount"] == 1
    assert result["failedCount"] == 1
    assert result["failed"][0]["songId"] == "failed-song"
    assert "failed-song" in fake.data["songs"]
    assert "ok-song" not in fake.data["songs"]
    assert failed_audio.exists()
    assert not ok_audio.exists()
    assert fake.saved is True
