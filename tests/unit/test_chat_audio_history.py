import asyncio
import hashlib
import io
import json
import sqlite3
import time
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient

from main_routers import chat_audio_router
from services import chat_audio_history
from services.chat_audio_history import ChatAudioHistory
from utils.config_manager.reserved_schema import get_character_uid


PCM = b"\x34\x12" * 8_000
YUI = "a" * 32
OTHER = "b" * 32


def test_recording_recovers_across_restart_and_deletion_keeps_receipt(tmp_path, monkeypatch):
    history = ChatAudioHistory(tmp_path, YUI)
    digest = hashlib.sha256(PCM).digest()
    deadline = time.time() + 900
    assert history.reserve("recording", PCM, digest, deadline)
    assert history.page(None, 1)["messages"][0]["state"] == "sending"
    monkeypatch.setattr(chat_audio_history, "_PROCESS_ID", "after-restart")
    restarted = ChatAudioHistory(tmp_path, YUI)
    assert restarted.page(None, 1)["messages"][0]["state"] == "unconfirmed"
    assert not restarted.reserve("recording", PCM, digest, deadline)
    assert not restarted.update("recording", transcript="stale")
    assert restarted.reserve("new", PCM, digest, deadline)
    assert restarted.update("new", state="sent")
    assert restarted.update("new", state="unconfirmed", transcript="late transcript")
    page = restarted.page(None, 1)
    assert page["messages"][0]["state"] == "sent"
    assert page["messages"][0]["transcript"] == "late transcript"
    assert restarted.page(page["next_before"], 1)["messages"][0]["request_id"] == "recording"
    with wave.open(io.BytesIO(restarted.read_audio("new")), "rb") as audio:
        assert (audio.getframerate(), audio.getnchannels(), audio.getsampwidth()) == (16_000, 1, 2)
        assert audio.readframes(audio.getnframes()) == PCM
    restarted.delete("new")
    assert restarted.read_audio("new") is None
    assert not restarted.update("new", transcript="arrived after deletion")
    assert not restarted.reserve("new", PCM, digest, deadline)
    assert restarted.receipt("new")["state"] == "deleted"


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["Gone", "."])
@pytest.mark.parametrize("commits", [True, False])
async def test_role_deletion_purges_its_audio_only_after_commit(tmp_path, name, commits):
    from tests.unit.test_character_uid import _backfilled_manager, _init_router_state

    config, _ = _backfilled_manager(tmp_path, {"Current": {"昵称": "Current"}, name: {"昵称": name}})
    characters = config.load_characters()["猫娘"]
    histories = [
        ChatAudioHistory(config.app_docs_dir, get_character_uid(characters[character]))
        for character in (name, "Current")
    ]
    removed, kept = histories
    digest = hashlib.sha256(PCM).digest()
    submitted_at = time.time()
    for history in histories:
        assert history.reserve("old", PCM, digest, submitted_at + 900)
        assert history.update("old", state="completed", transcript="original")

    async def reload_before_commit(**_kwargs):
        assert removed.read_audio("old").endswith(PCM)
        return commits

    with patch("utils.config_manager._config_manager", config):
        crud = _init_router_state(config)
        with patch.object(crud, "release_memory_server_character", AsyncMock(return_value=True)), patch.object(
            crud, "notify_memory_server_reload", reload_before_commit,
        ):
            result = await crud.delete_catgirl(name)

    body = result if isinstance(result, dict) else json.loads(result.body)
    assert body["success"] is commits
    assert kept.read_audio("old").endswith(PCM)
    if commits:
        assert "partial_success" not in body
        assert removed.read_audio("old") is None
        assert removed.receipt("old") is None
        assert not removed.reserve("queued", PCM, digest, submitted_at + 900, submitted_at=submitted_at)
        assert not removed.update("old", transcript="late")
    else:
        assert removed.read_audio("old").endswith(PCM)
        assert removed.receipt("old")["transcript"] == "original"
        chat_audio_history._recover_retirements(
            config.app_docs_dir, config.get_runtime_config_path("characters.json"),
        )
        assert removed.read_audio("old").endswith(PCM)
        assert not chat_audio_history._retirement_marker(config.app_docs_dir, removed.character_uid).exists()


@pytest.mark.asyncio
async def test_audio_retirement_recovers_a_committed_delete_after_a_locked_db(tmp_path):
    from tests.unit.test_character_uid import _backfilled_manager, _init_router_state

    config, _ = _backfilled_manager(tmp_path, {"Current": {"昵称": "Current"}, "Gone": {"昵称": "Gone"}})
    history = await chat_audio_history.history_for_character(config, "Gone")
    digest = hashlib.sha256(PCM).digest()
    assert history.reserve("old", PCM, digest, time.time() + 900)
    assert history.update("old", transcript="saved transcript")
    marker = chat_audio_history._retirement_marker(config.app_docs_dir, history.character_uid)

    async def before_commit(**_kwargs):
        assert marker.is_file()
        return True

    writer = sqlite3.connect(history.path)
    writer.execute("BEGIN IMMEDIATE")
    try:
        with patch("utils.config_manager._config_manager", config):
            crud = _init_router_state(config)
            with patch.object(crud, "release_memory_server_character", AsyncMock(return_value=True)), patch.object(
                crud, "notify_memory_server_reload", before_commit,
            ):
                result = await crud.delete_catgirl("Gone")
        assert result["success"] and result["chat_audio_cleanup_failed"]
        assert marker.is_file()
    finally:
        writer.close()
    assert await chat_audio_history.history_for_character(config, "Gone") is None
    assert history.read_audio("old").endswith(PCM)

    # A freshly constructed worker uses only the committed config and the persisted marker.
    recovery = chat_audio_history._AudioRetirementRecovery()
    recovery.start(config, asyncio.Lock())
    try:
        async with asyncio.timeout(4):
            while marker.exists():
                await asyncio.sleep(0.02)
        assert history.read_audio("old") is None
        assert history.receipt("old") is None
    finally:
        await recovery.stop()


def test_unreadable_retirement_marker_does_not_block_other_characters(tmp_path, monkeypatch):
    from pathlib import Path
    from utils.file_utils import atomic_write_json

    registry = tmp_path / "characters.json"
    atomic_write_json(registry, {"猫娘": {"Kept": {"_reserved": {"character_uid": "c" * 32}}}})
    broken = chat_audio_history._retirement_marker(tmp_path, YUI)
    broken.mkdir(parents=True)
    valid = chat_audio_history._retirement_marker(tmp_path, OTHER)
    atomic_write_json(valid, {"character_uid": OTHER})
    history = ChatAudioHistory(tmp_path, OTHER)
    assert history.reserve("old", PCM, hashlib.sha256(PCM).digest(), time.time() + 900)
    original_iterdir = Path.iterdir

    def ordered(path):
        entries = original_iterdir(path)
        return iter(sorted(entries)) if path == valid.parent else entries

    monkeypatch.setattr(Path, "iterdir", ordered)
    with pytest.raises(OSError):
        chat_audio_history._recover_retirements(tmp_path, registry)
    assert broken.is_dir()
    assert not valid.exists()
    assert history.read_audio("old") is None


def test_local_playback_and_clear_are_scoped_to_character(tmp_path, monkeypatch):
    app = FastAPI()
    app.include_router(chat_audio_router.router)
    config = SimpleNamespace(app_docs_dir=tmp_path, aload_characters=AsyncMock(return_value={
        "猫娘": {
            "Yui": {"_reserved": {"character_uid": YUI}},
            "Other": {"_reserved": {"character_uid": OTHER}},
        },
    }))
    monkeypatch.setattr(chat_audio_router, "get_config_manager", lambda: config)
    monkeypatch.setattr(chat_audio_router, "get_session_manager", lambda: {})
    digest = hashlib.sha256(PCM).digest()
    for uid in (YUI, OTHER):
        assert ChatAudioHistory(tmp_path, uid).reserve("same-id", PCM, digest, time.time() + 900)
    headers = {"Origin": "http://localhost:48911", "Sec-Fetch-Site": "same-origin"}
    with TestClient(app, base_url="http://localhost:48911", client=("127.0.0.1", 45678)) as client:
        history_url = "/api/chat-audio?character=Yui"
        playback_url = "/api/chat-audio/same-id?character=Yui"
        listing = client.get(history_url, headers=headers)
        assert listing.json()["messages"][0]["duration_ms"] == 500
        assert "wav" not in listing.json()["messages"][0]
        response = client.get(playback_url, headers=headers)
        assert response.headers["Content-Type"] == "audio/wav"
        assert response.headers["Cache-Control"] == "no-store"
        assert response.content.endswith(PCM)
        assert client.delete(history_url, headers={"Origin": "http://elsewhere.test"}).status_code == 403
        assert client.delete(history_url, headers=headers).status_code == 204
        assert client.get(playback_url, headers=headers).status_code == 404
        assert client.get("/api/chat-audio/same-id?character=Other", headers=headers).status_code == 200
    with TestClient(app, base_url="http://localhost:48911", client=("192.0.2.4", 45678)) as remote:
        assert remote.get("/api/chat-audio?character=Other", headers=headers).status_code == 403


def test_websocket_history_checks_browser_origin():
    scope = {
        "type": "websocket", "scheme": "ws", "path": "/ws/Yui",
        "server": ("localhost", 48911), "client": ("127.0.0.1", 45678),
    }
    local = WebSocket({**scope, "headers": [(b"origin", b"http://localhost:48911")]}, AsyncMock(), AsyncMock())
    other = WebSocket({**scope, "headers": [(b"origin", b"http://localhost:48912")]}, AsyncMock(), AsyncMock())
    assert chat_audio_router.local_audio_history_allowed(local)
    assert not chat_audio_router.local_audio_history_allowed(other)
