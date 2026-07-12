import io
import json
import zipfile
from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.datastructures import UploadFile

from main_routers import jukebox_router


class _FakeJukeboxConfig:
    def __init__(self, data, jukebox_dir):
        self.data = data
        self.jukebox_dir = jukebox_dir
        self.songs_dir = jukebox_dir / "songs"
        self.actions_dir = jukebox_dir / "actions"
        self.saved = False

    async def asave(self):
        self.saved = True


def _install_fake_config(monkeypatch, fake):
    monkeypatch.setattr(jukebox_router, "get_config_manager", lambda: object())
    monkeypatch.setattr(jukebox_router, "JukeboxConfig", lambda _config_mgr: fake)


def _make_save_config(tmp_path, data, builtin_songs, builtin_actions):
    config = object.__new__(jukebox_router.JukeboxConfig)
    config.config_file = tmp_path / "config.json"
    config.data = data
    config._load_builtin_resource_defaults = lambda: (builtin_songs, builtin_actions)
    return config


def test_config_summary_revision_is_stable_and_changes_with_songs():
    data = {
        "version": "1.0",
        "songs": {
            "song-1": {"name": "Song 1", "artist": "Artist", "visible": True},
            "song-2": {"name": "Song 2", "artist": "Artist", "visible": False},
        },
        "actions": {},
        "bindings": {},
    }

    first = jukebox_router.build_config_summary(data)
    reordered = jukebox_router.build_config_summary(
        {
            "bindings": {},
            "actions": {},
            "songs": {
                "song-2": {"visible": False, "artist": "Artist", "name": "Song 2"},
                "song-1": {"artist": "Artist", "name": "Song 1", "visible": True},
            },
            "version": "1.0",
        }
    )
    data["songs"]["song-3"] = {"name": "Song 3", "artist": "Artist", "visible": True}
    changed = jukebox_router.build_config_summary(data)

    assert first["configRevision"] == reordered["configRevision"]
    assert first["songCount"] == 2
    assert first["visibleSongCount"] == 1
    assert changed["configRevision"] != first["configRevision"]
    assert changed["songCount"] == 3
    assert changed["visibleSongCount"] == 2


@pytest.mark.asyncio
async def test_head_file_reports_jukebox_file_existence(monkeypatch, tmp_path):
    jukebox_dir = tmp_path / "jukebox"
    songs_dir = jukebox_dir / "songs"
    songs_dir.mkdir(parents=True)
    audio_path = songs_dir / "song.mp3"
    audio_path.write_bytes(b"audio")
    fake = _FakeJukeboxConfig(
        {"version": "1.0", "songs": {}, "actions": {}, "bindings": {}},
        jukebox_dir,
    )
    _install_fake_config(monkeypatch, fake)

    response = await jukebox_router.head_file("songs/song.mp3")

    assert response.status_code == 200
    assert response.headers["content-length"] == "5"
    with pytest.raises(HTTPException) as exc_info:
        await jukebox_router.head_file("songs/missing.mp3")
    assert exc_info.value.status_code == 404


def test_resolve_jukebox_file_prefers_user_storage_then_bundled_layouts(monkeypatch, tmp_path):
    jukebox_dir = tmp_path / "jukebox"
    user_songs_dir = jukebox_dir / "songs"
    user_songs_dir.mkdir(parents=True)
    user_song = user_songs_dir / "song_001.mp3"
    user_song.write_bytes(b"user")

    builtin_dir = tmp_path / "static" / "jukebox"
    builtin_songs_dir = builtin_dir / "songs"
    builtin_actions_dir = builtin_dir / "actions"
    builtin_songs_dir.mkdir(parents=True)
    builtin_actions_dir.mkdir(parents=True)
    nested_song = builtin_songs_dir / "song_002.mp3"
    nested_song.write_bytes(b"nested")
    flat_song = builtin_dir / "song_003.mp3"
    flat_song.write_bytes(b"flat-song")
    flat_action = builtin_dir / "song_001.vrma"
    flat_action.write_bytes(b"flat-action")

    fake = _FakeJukeboxConfig(
        {"version": "1.0", "songs": {}, "actions": {}, "bindings": {}},
        jukebox_dir,
    )
    _install_fake_config(monkeypatch, fake)
    monkeypatch.setattr(jukebox_router, "BUILTIN_JUKEBOX_DIR", builtin_dir)

    assert jukebox_router.resolve_jukebox_file_path("songs/song_001.mp3") == user_song
    assert jukebox_router.resolve_jukebox_file_path("songs/song_002.mp3") == nested_song
    assert jukebox_router.resolve_jukebox_file_path("songs/song_003.mp3") == flat_song
    assert jukebox_router.resolve_jukebox_file_path("actions/song_001.vrma") == flat_action
    with pytest.raises(HTTPException) as exc_info:
        jukebox_router.resolve_jukebox_file_path("../song_003.mp3")
    assert exc_info.value.status_code == 403


def test_save_builtin_overrides_omits_unchanged_defaults(tmp_path):
    config = _make_save_config(
        tmp_path,
        {
            "version": "1.0",
            "songs": {
                "builtin-song": {
                    "name": "Builtin Song",
                    "artist": "Builtin Artist",
                    "defaultAction": "builtin-action",
                    "visible": True,
                    "isBuiltin": True,
                }
            },
            "actions": {
                "builtin-action": {
                    "name": "Builtin Action",
                    "visible": True,
                    "isBuiltin": True,
                }
            },
            "bindings": {},
            "md5Index": {"songs": {}, "actions": {}},
        },
        {
            "builtin-song": {
                "name": "Builtin Song",
                "artist": "Builtin Artist",
                "defaultAction": "builtin-action",
                "visible": True,
            }
        },
        {
            "builtin-action": {
                "name": "Builtin Action",
                "visible": True,
            }
        },
    )

    config.save()

    saved = json.loads(config.config_file.read_text(encoding="utf-8"))
    assert saved["builtinOverrides"] == {"songs": {}, "actions": {}}


def test_save_builtin_overrides_persists_changed_fields(tmp_path):
    config = _make_save_config(
        tmp_path,
        {
            "version": "1.0",
            "songs": {
                "builtin-song": {
                    "name": "Builtin Song",
                    "artist": "Builtin Artist",
                    "defaultAction": "",
                    "visible": False,
                    "isBuiltin": True,
                }
            },
            "actions": {
                "builtin-action": {
                    "name": "Renamed Action",
                    "visible": False,
                    "isBuiltin": True,
                }
            },
            "bindings": {},
            "md5Index": {"songs": {}, "actions": {}},
        },
        {
            "builtin-song": {
                "name": "Builtin Song",
                "artist": "Builtin Artist",
                "defaultAction": "builtin-action",
                "visible": True,
            }
        },
        {
            "builtin-action": {
                "name": "Builtin Action",
                "visible": True,
            }
        },
    )

    config.save()

    saved = json.loads(config.config_file.read_text(encoding="utf-8"))
    assert saved["builtinOverrides"] == {
        "songs": {
            "builtin-song": {
                "visible": False,
                "defaultAction": "",
            }
        },
        "actions": {
            "builtin-action": {
                "visible": False,
                "name": "Renamed Action",
            }
        },
    }


@pytest.mark.asyncio
async def test_upload_song_uses_audio_metadata_title_and_artist_when_form_metadata_empty(
    monkeypatch, tmp_path
):
    jukebox_dir = tmp_path / "jukebox"
    fake = _FakeJukeboxConfig(
        {
            "songs": {},
            "actions": {},
            "bindings": {},
            "md5Index": {"songs": {}, "actions": {}},
        },
        jukebox_dir,
    )
    fake.songs_dir.mkdir(parents=True)
    _install_fake_config(monkeypatch, fake)
    monkeypatch.setattr(
        jukebox_router,
        "extract_audio_metadata",
        lambda _path: {"name": "Metadata Title", "artist": "Metadata Artist"},
    )

    result = await jukebox_router.upload_songs(
        [UploadFile(filename="uploaded.mp3", file=io.BytesIO(b"audio-bytes"))],
        metadata=json.dumps([{}], ensure_ascii=False),
    )

    assert result["success"] is True
    assert result["song"]["name"] == "Metadata Title"
    assert result["song"]["artist"] == "Metadata Artist"
    assert fake.data["songs"][result["song"]["id"]]["name"] == "Metadata Title"
    assert fake.data["songs"][result["song"]["id"]]["artist"] == "Metadata Artist"
    assert (fake.songs_dir / "uploaded.mp3").exists()
    assert fake.saved is True


@pytest.mark.asyncio
async def test_upload_song_uses_audio_metadata_artist_when_form_artist_missing(monkeypatch, tmp_path):
    jukebox_dir = tmp_path / "jukebox"
    fake = _FakeJukeboxConfig(
        {
            "songs": {},
            "actions": {},
            "bindings": {},
            "md5Index": {"songs": {}, "actions": {}},
        },
        jukebox_dir,
    )
    fake.songs_dir.mkdir(parents=True)
    _install_fake_config(monkeypatch, fake)
    monkeypatch.setattr(
        jukebox_router,
        "extract_audio_metadata",
        lambda _path: {"name": "Metadata Title", "artist": "Metadata Artist"},
    )

    result = await jukebox_router.upload_songs(
        [UploadFile(filename="uploaded.mp3", file=io.BytesIO(b"audio-bytes"))],
        metadata=json.dumps([{"name": "Manual Title"}], ensure_ascii=False),
    )

    assert result["success"] is True
    assert result["song"]["name"] == "Manual Title"
    assert result["song"]["artist"] == "Metadata Artist"
    assert fake.data["songs"][result["song"]["id"]]["artist"] == "Metadata Artist"
    assert (fake.songs_dir / "uploaded.mp3").exists()
    assert fake.saved is True


@pytest.mark.asyncio
async def test_upload_song_treats_unknown_form_artist_as_missing(monkeypatch, tmp_path):
    jukebox_dir = tmp_path / "jukebox"
    fake = _FakeJukeboxConfig(
        {
            "songs": {},
            "actions": {},
            "bindings": {},
            "md5Index": {"songs": {}, "actions": {}},
        },
        jukebox_dir,
    )
    fake.songs_dir.mkdir(parents=True)
    _install_fake_config(monkeypatch, fake)
    monkeypatch.setattr(
        jukebox_router,
        "extract_audio_metadata",
        lambda _path: {"name": "Metadata Title", "artist": "Metadata Artist"},
    )

    result = await jukebox_router.upload_songs(
        [UploadFile(filename="uploaded.mp3", file=io.BytesIO(b"audio-bytes"))],
        metadata=json.dumps([{"name": "Manual Title", "artist": "未知"}], ensure_ascii=False),
    )

    assert result["success"] is True
    assert result["song"]["artist"] == "Metadata Artist"


@pytest.mark.asyncio
async def test_upload_song_falls_back_when_audio_metadata_extraction_fails(
    monkeypatch, tmp_path
):
    jukebox_dir = tmp_path / "jukebox"
    fake = _FakeJukeboxConfig(
        {
            "songs": {},
            "actions": {},
            "bindings": {},
            "md5Index": {"songs": {}, "actions": {}},
        },
        jukebox_dir,
    )
    fake.songs_dir.mkdir(parents=True)
    _install_fake_config(monkeypatch, fake)

    def _raise_metadata_error(_path):
        raise RuntimeError("tag read failed")

    monkeypatch.setattr(jukebox_router, "extract_audio_metadata", _raise_metadata_error)

    result = await jukebox_router.upload_songs(
        [UploadFile(filename="fallback-title.mp3", file=io.BytesIO(b"audio-bytes"))],
        metadata=json.dumps([{}], ensure_ascii=False),
    )

    assert result["success"] is True
    assert result["song"]["name"] == "fallback-title"
    assert result["song"]["artist"] == "未知"
    assert fake.saved is True


@pytest.mark.asyncio
async def test_upload_song_uses_filename_when_audio_metadata_title_is_missing(
    monkeypatch, tmp_path
):
    jukebox_dir = tmp_path / "jukebox"
    fake = _FakeJukeboxConfig(
        {
            "songs": {},
            "actions": {},
            "bindings": {},
            "md5Index": {"songs": {}, "actions": {}},
        },
        jukebox_dir,
    )
    fake.songs_dir.mkdir(parents=True)
    _install_fake_config(monkeypatch, fake)
    monkeypatch.setattr(
        jukebox_router,
        "extract_audio_metadata",
        lambda _path: {"name": "", "artist": "Metadata Artist"},
    )

    result = await jukebox_router.upload_songs(
        [UploadFile(filename="fallback-title.mp3", file=io.BytesIO(b"audio-bytes"))],
        metadata=json.dumps([{}], ensure_ascii=False),
    )

    assert result["success"] is True
    assert result["song"]["name"] == "fallback-title"
    assert result["song"]["artist"] == "Metadata Artist"


def test_extract_audio_metadata_picks_common_artist_tags(monkeypatch, tmp_path):
    audio_path = tmp_path / "tagged.mp3"
    audio_path.write_bytes(b"not-real-audio")

    class _FakeStream:
        type = "audio"
        metadata = {"PERFORMER": " Stream Artist "}

    class _FakeContainer:
        metadata = {"title": " Meta Title "}
        streams = [_FakeStream()]

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _FakeAv:
        @staticmethod
        def open(_path):
            return _FakeContainer()

    monkeypatch.setitem(__import__("sys").modules, "av", _FakeAv)

    assert jukebox_router.extract_audio_metadata(audio_path) == {
        "name": "Meta Title",
        "artist": "Stream Artist",
    }


def test_extract_audio_metadata_ignores_track_number_as_title(monkeypatch, tmp_path):
    audio_path = tmp_path / "track-only.mp3"
    audio_path.write_bytes(b"not-real-audio")

    class _FakeContainer:
        metadata = {"track": "03/12", "artist": "Metadata Artist"}
        streams = []

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _FakeAv:
        @staticmethod
        def open(_path):
            return _FakeContainer()

    monkeypatch.setitem(__import__("sys").modules, "av", _FakeAv)

    assert jukebox_router.extract_audio_metadata(audio_path) == {
        "name": "",
        "artist": "Metadata Artist",
    }


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
async def test_delete_builtin_song_hides_and_preserves_bindings(monkeypatch, tmp_path):
    fake = _FakeJukeboxConfig(
        {
            "songs": {
                "builtin-song": {
                    "name": "Builtin Song",
                    "audio": "songs/builtin.mp3",
                    "audioMd5": "builtin-md5",
                    "visible": True,
                    "isBuiltin": True,
                }
            },
            "bindings": {
                "builtin-song": {"action-1": {"offset": 0}},
            },
            "md5Index": {
                "songs": {"builtin-md5": "builtin-song"},
                "actions": {},
            },
        },
        tmp_path / "jukebox",
    )
    _install_fake_config(monkeypatch, fake)

    result = await jukebox_router.delete_song("builtin-song")

    assert result == {"success": True, "message": "内置歌曲已隐藏", "hidden": True}
    assert fake.data["songs"]["builtin-song"]["visible"] is False
    assert fake.data["bindings"] == {"builtin-song": {"action-1": {"offset": 0}}}
    assert fake.data["md5Index"]["songs"] == {"builtin-md5": "builtin-song"}
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
    assert exc_info.value.detail == "歌曲不存在: missing-song"
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


@pytest.mark.asyncio
async def test_batch_delete_actions_removes_user_action_and_hides_builtin(monkeypatch, tmp_path):
    jukebox_dir = tmp_path / "jukebox"
    actions_dir = jukebox_dir / "actions"
    actions_dir.mkdir(parents=True)
    action_file = actions_dir / "user.vmd"
    action_file.write_bytes(b"action")

    fake = _FakeJukeboxConfig(
        {
            "songs": {
                "song-1": {"name": "Song 1", "defaultAction": "user-action"},
                "song-2": {"name": "Song 2", "defaultAction": "builtin-action"},
            },
            "actions": {
                "user-action": {
                    "name": "User Action",
                    "file": "actions/user.vmd",
                    "fileMd5": "user-action-md5",
                },
                "builtin-action": {
                    "name": "Builtin Action",
                    "file": "actions/builtin.vmd",
                    "fileMd5": "builtin-action-md5",
                    "isBuiltin": True,
                    "visible": True,
                },
            },
            "bindings": {
                "song-1": {"user-action": {"offset": 0}, "builtin-action": {"offset": 0}},
                "song-2": {"builtin-action": {"offset": 0}},
            },
            "md5Index": {
                "songs": {},
                "actions": {
                    "user-action-md5": "user-action",
                    "builtin-action-md5": "builtin-action",
                },
            },
        },
        jukebox_dir,
    )
    _install_fake_config(monkeypatch, fake)

    result = await jukebox_router.batch_delete_actions(
        jukebox_router.BatchDeleteActionsRequest(actionIds=["user-action", "builtin-action"])
    )

    assert result["success"] is True
    assert result["partial"] is False
    assert result["deletedCount"] == 1
    assert result["hiddenCount"] == 1
    assert result["failedCount"] == 0
    assert not action_file.exists()
    assert "user-action" not in fake.data["actions"]
    assert "builtin-action" in fake.data["actions"]
    assert fake.data["actions"]["builtin-action"]["visible"] is False
    assert "user-action-md5" not in fake.data["md5Index"]["actions"]
    assert fake.data["bindings"] == {
        "song-1": {"builtin-action": {"offset": 0}},
        "song-2": {"builtin-action": {"offset": 0}},
    }
    assert fake.data["songs"]["song-1"]["defaultAction"] == ""
    assert fake.data["songs"]["song-2"]["defaultAction"] == "builtin-action"
    assert fake.saved is True


@pytest.mark.asyncio
async def test_update_action_visibility_persists_hidden_state(monkeypatch, tmp_path):
    fake = _FakeJukeboxConfig(
        {
            "songs": {},
            "actions": {
                "builtin-action": {
                    "name": "Builtin Action",
                    "file": "actions/builtin.vmd",
                    "fileMd5": "builtin-action-md5",
                    "isBuiltin": True,
                    "visible": True,
                }
            },
            "bindings": {},
            "md5Index": {"songs": {}, "actions": {}},
        },
        tmp_path / "jukebox",
    )
    _install_fake_config(monkeypatch, fake)

    result = await jukebox_router.update_action_visibility("builtin-action", visible=False)

    assert result == {"success": True}
    assert fake.data["actions"]["builtin-action"]["visible"] is False
    assert fake.saved is True


@pytest.mark.asyncio
async def test_export_ignore_hidden_skips_hidden_actions_and_bindings(monkeypatch, tmp_path):
    jukebox_dir = tmp_path / "jukebox"
    songs_dir = jukebox_dir / "songs"
    actions_dir = jukebox_dir / "actions"
    songs_dir.mkdir(parents=True)
    actions_dir.mkdir(parents=True)
    (songs_dir / "song.mp3").write_bytes(b"audio")
    (actions_dir / "visible.vmd").write_bytes(b"visible")
    (actions_dir / "hidden.vmd").write_bytes(b"hidden")

    fake = _FakeJukeboxConfig(
        {
            "version": "1.0",
            "songs": {
                "song-1": {
                    "name": "Song 1",
                    "audio": "songs/song.mp3",
                    "audioMd5": "song-md5",
                    "visible": True,
                    "defaultAction": "hidden-action",
                }
            },
            "actions": {
                "visible-action": {
                    "name": "Visible Action",
                    "file": "actions/visible.vmd",
                    "fileMd5": "visible-md5",
                    "visible": True,
                },
                "hidden-action": {
                    "name": "Hidden Action",
                    "file": "actions/hidden.vmd",
                    "fileMd5": "hidden-md5",
                    "visible": False,
                },
            },
            "bindings": {
                "song-1": {
                    "visible-action": {"offset": 1},
                    "hidden-action": {"offset": 2},
                }
            },
            "md5Index": {"songs": {}, "actions": {}},
        },
        jukebox_dir,
    )
    _install_fake_config(monkeypatch, fake)

    response = await jukebox_router.export_config(songIds=None, actionIds=None, includeHidden=False)
    archive_bytes = b"".join([chunk async for chunk in response.body_iterator])

    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
        exported = json.loads(archive.read("config.json").decode("utf-8"))
        archive_names = archive.namelist()

    assert set(exported["actions"]) == {"visible-action"}
    assert "hidden-action" not in exported["actions"]
    assert exported["songs"]["song-1"]["defaultAction"] == ""
    assert fake.data["songs"]["song-1"]["defaultAction"] == "hidden-action"
    assert exported["bindings"] == {
        "song-md5": {
            "visible-md5": {"offset": 1},
        }
    }
    assert "actions/visible.vmd" in archive_names
    assert "actions/hidden.vmd" not in archive_names

    if response.background:
        await response.background()


@pytest.mark.asyncio
async def test_batch_delete_actions_precheck_rejects_unknown_ids(monkeypatch, tmp_path):
    fake = _FakeJukeboxConfig(
        {
            "songs": {},
            "actions": {
                "known-action": {
                    "name": "Known",
                    "file": "actions/known.vmd",
                }
            },
            "bindings": {},
            "md5Index": {"songs": {}, "actions": {}},
        },
        tmp_path / "jukebox",
    )
    _install_fake_config(monkeypatch, fake)

    with pytest.raises(HTTPException) as exc_info:
        await jukebox_router.batch_delete_actions(
            jukebox_router.BatchDeleteActionsRequest(actionIds=["known-action", "missing-action"])
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "动画不存在: missing-action"
    assert "known-action" in fake.data["actions"]
    assert fake.saved is False


@pytest.mark.asyncio
async def test_batch_delete_actions_reports_partial_failures(monkeypatch, tmp_path):
    jukebox_dir = tmp_path / "jukebox"
    actions_dir = jukebox_dir / "actions"
    actions_dir.mkdir(parents=True)
    failed_file = actions_dir / "failed.vmd"
    ok_file = actions_dir / "ok.vmd"
    failed_file.write_bytes(b"failed")
    ok_file.write_bytes(b"ok")

    fake = _FakeJukeboxConfig(
        {
            "songs": {},
            "actions": {
                "failed-action": {
                    "name": "Failed Action",
                    "file": "actions/failed.vmd",
                    "fileMd5": "failed-action-md5",
                },
                "ok-action": {
                    "name": "OK Action",
                    "file": "actions/ok.vmd",
                    "fileMd5": "ok-action-md5",
                },
            },
            "bindings": {},
            "md5Index": {
                "songs": {},
                "actions": {
                    "failed-action-md5": "failed-action",
                    "ok-action-md5": "ok-action",
                },
            },
        },
        jukebox_dir,
    )
    _install_fake_config(monkeypatch, fake)

    original_unlink = Path.unlink

    def fail_selected_unlink(self, *args, **kwargs):
        if self == failed_file:
            raise PermissionError("locked")
        return original_unlink(self, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", fail_selected_unlink)

    result = await jukebox_router.batch_delete_actions(
        jukebox_router.BatchDeleteActionsRequest(actionIds=["failed-action", "ok-action"])
    )

    assert result["success"] is False
    assert result["partial"] is True
    assert result["deletedCount"] == 1
    assert result["failedCount"] == 1
    assert result["failed"][0]["actionId"] == "failed-action"
    assert "failed-action" in fake.data["actions"]
    assert "ok-action" not in fake.data["actions"]
    assert failed_file.exists()
    assert not ok_file.exists()
    assert fake.saved is True
