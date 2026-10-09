# -*- coding: utf-8 -*-
"""A declined theater archive must stay declined even when its write lands late.

The theater times out on ``/cache`` after a few seconds while the memory server
may still be processing that request. If the player then chooses "skip", the
retract can run before the late write; the tombstone it leaves must drop that
write, survive a memory-server restart, and still let a later explicit archive
attempt of the same receipt through.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memory.recent import CompressedRecentHistoryManager, TheaterEpisodeRetracted
from utils import recent_file
from utils.llm_client import SystemMessage, messages_from_dict


@pytest.fixture(autouse=True)
def _isolated_recent_state(monkeypatch):
    registries = (
        recent_file._LOCKS,
        recent_file._PENDING,
        recent_file._REDIRECTS,
        recent_file._DELETED,
        recent_file._GENERATIONS,
        recent_file._CONTENT_VERSIONS,
    )
    for registry in registries:
        registry.clear()
    monkeypatch.setattr("memory.recent.assert_cloudsave_writable", lambda *a, **kw: None)
    yield
    for registry in registries:
        registry.clear()


class _FakeConfig:
    def __init__(self, name: str, recent_path: str):
        self._name = name
        self._recent_path = recent_path

    async def aget_character_data(self):
        return (None, None, None, None, {}, None, None, None, {self._name: self._recent_path})


def _manager(tmp_path, name="Role"):
    recent_path = str(tmp_path / name / "recent.json")
    (tmp_path / name).mkdir(parents=True, exist_ok=True)
    mgr = object.__new__(CompressedRecentHistoryManager)
    mgr._config_manager = _FakeConfig(name, recent_path)
    mgr.max_history_length = 4
    mgr.compress_threshold = 5
    mgr.log_file_path = {name: recent_path}
    mgr.name_mapping = {"human": "Master", "ai": name, "system": "SYSTEM_MESSAGE"}
    mgr.user_histories = {}
    return mgr, name, recent_path


_METADATA = {
    "source": "theater_numeric_v2",
    "memory_tier": "episode_summary",
    "message_kind": "episode_summary",
    "story_id": "story_rain",
    "session_id": "session_rain",
    "archive_through_revision": 5,
    "episode_summary": "两人保住了共同的住处。",
}
_REQUEST_ID = "theater_archive_declined"


def _capsule():
    return SystemMessage(content="两人保住了共同的住处。", metadata=dict(_METADATA))


def _disk_sessions(recent_path):
    try:
        with open(recent_path, encoding="utf-8") as handle:
            messages = messages_from_dict(json.load(handle))
    except FileNotFoundError:
        return []
    return [message.metadata.get("session_id") for message in messages]


def _record(mgr, name, attempt=1):
    asyncio.run(mgr.record_theater_retraction(
        name,
        story_id="story_rain",
        session_id="session_rain",
        archive_through_revision=5,
        archive_request_id=_REQUEST_ID,
        archive_attempt=attempt,
    ))


@pytest.mark.unit
def test_tombstone_drops_late_attempts_and_survives_restart(tmp_path):
    mgr, name, recent_path = _manager(tmp_path)
    _record(mgr, name, attempt=2)

    # Attempts issued before the decline (1 and 2) never land, not even after a
    # memory-server restart (a fresh manager reading the same directory).
    restarted, _, _ = _manager(tmp_path)
    for manager, attempt in ((mgr, 1), (restarted, 2), (restarted, None)):
        with pytest.raises(TheaterEpisodeRetracted):
            asyncio.run(manager.upsert_theater_episode(
                _capsule(), name, archive_request_id=_REQUEST_ID, archive_attempt=attempt,
            ))
    assert _disk_sessions(recent_path) == []

    # Other archives are unaffected, and a later explicit attempt still lands.
    asyncio.run(restarted.upsert_theater_episode(
        SystemMessage(content="另一周目", metadata={**_METADATA, "session_id": "other", "episode_summary": "另一周目"}),
        name,
        archive_request_id="theater_archive_other",
        archive_attempt=1,
    ))
    asyncio.run(restarted.upsert_theater_episode(
        _capsule(), name, archive_request_id=_REQUEST_ID, archive_attempt=3,
    ))
    assert sorted(_disk_sessions(recent_path)) == ["other", "session_rain"]


@pytest.mark.unit
def test_repeated_retraction_keeps_the_highest_fenced_attempt(tmp_path):
    mgr, name, _ = _manager(tmp_path)
    _record(mgr, name, attempt=3)
    _record(mgr, name, attempt=1)
    with pytest.raises(TheaterEpisodeRetracted):
        asyncio.run(mgr.upsert_theater_episode(
            _capsule(), name, archive_request_id=_REQUEST_ID, archive_attempt=3,
        ))
    with open(tmp_path / name / "theater_retractions.json", encoding="utf-8") as handle:
        entries = json.load(handle)["entries"]
    assert [entry["through_attempt"] for entry in entries] == [3]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_retract_before_late_cache_write_keeps_summary_declined(tmp_path):
    """Retract runs first (nothing to remove yet); the late /cache must not resurrect it."""
    from app import memory_server

    mgr, name, recent_path = _manager(tmp_path)
    fake_time = MagicMock()
    fake_time.areconcile_theater_conversations = AsyncMock(return_value={"removed": 0, "stored": 1})
    fake_spawn = AsyncMock()
    payload = json.dumps([{
        "role": "system",
        "content": "两人保住了共同的住处。",
        "metadata": dict(_METADATA),
    }], ensure_ascii=False)

    with patch.object(memory_server.runtime, "recent_history_manager", mgr), \
         patch.object(memory_server.runtime, "time_manager", fake_time), \
         patch.object(memory_server.post_turn, "_spawn_outbox_post_turn_signals", fake_spawn):
        retracted = await memory_server.retract_theater_episode(
            name,
            memory_server.TheaterEpisodeRetractRequest(
                story_id="story_rain",
                session_id="session_rain",
                archive_through_revision=5,
                archive_request_id=_REQUEST_ID,
                archive_attempt=1,
            ),
        )
        assert retracted == {"ok": True, "removed_recent": 0, "removed_time_index": 0}

        late = await memory_server.cache_conversation(
            memory_server.HistoryRequest(
                input_history=payload,
                idempotency_key=_REQUEST_ID,
                theater_archive_attempt=1,
            ),
            name,
        )
        assert late == {"status": "retracted", "count": 0}
        assert _disk_sessions(recent_path) == []
        fake_time.areconcile_theater_conversations.assert_not_awaited()
        fake_spawn.assert_not_awaited()

        # The player later explicitly chooses to remember the same receipt.
        chosen = await memory_server.cache_conversation(
            memory_server.HistoryRequest(
                input_history=payload,
                idempotency_key=_REQUEST_ID,
                theater_archive_attempt=2,
            ),
            name,
        )
    assert chosen == {"status": "cached", "count": 1}
    assert _disk_sessions(recent_path) == ["session_rain"]
