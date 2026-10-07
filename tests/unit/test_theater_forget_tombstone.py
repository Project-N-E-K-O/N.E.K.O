# -*- coding: utf-8 -*-
"""A forgotten theater story must stay forgotten even when an archive write lands late.

The theater gives up on ``/cache`` after a few seconds while the memory server
may still process that request. When the player then forgets the whole story,
the memory server records a story-level tombstone with a fresh random forget
marker and returns it; the theater persists the marker and attaches it only to
archive requests it issues after the forget. Any write of that story without
the current marker (issued before the forget) is dropped, even after a
memory-server restart, while an archive the player starts after the forget
lands. No clock is compared, so a system clock stepping backwards cannot
reject a legitimate archive.
"""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace
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


def _manager(root, name="Role"):
    (root / name).mkdir(parents=True, exist_ok=True)
    recent_path = str(root / name / "recent.json")
    mgr = object.__new__(CompressedRecentHistoryManager)
    mgr._config_manager = _FakeConfig(name, recent_path)
    mgr.max_history_length = 4
    mgr.compress_threshold = 5
    mgr.log_file_path = {name: recent_path}
    mgr.name_mapping = {"human": "Master", "ai": name, "system": "SYSTEM_MESSAGE"}
    mgr.user_histories = {}
    return mgr, name, recent_path


def _capsule(story_id="story_rain", session_id="session_rain"):
    return SystemMessage(content="两人保住了共同的住处。", metadata={
        "source": "theater_numeric_v2",
        "memory_tier": "episode_summary",
        "message_kind": "episode_summary",
        "story_id": story_id,
        "session_id": session_id,
        "archive_through_revision": 5,
        "episode_summary": "两人保住了共同的住处。",
    })


def _disk_keys(recent_path):
    try:
        with open(recent_path, encoding="utf-8") as handle:
            messages = messages_from_dict(json.load(handle))
    except FileNotFoundError:
        return []
    return sorted(
        (message.metadata.get("story_id"), message.metadata.get("session_id"))
        for message in messages
    )


def _cache_request(memory_server, message, request_id, *, attempt=1, forget_marker=None):
    return memory_server.HistoryRequest(
        input_history=json.dumps([{
            "role": "system",
            "content": message.content,
            "metadata": dict(message.metadata),
        }], ensure_ascii=False),
        idempotency_key=request_id,
        theater_archive_attempt=attempt,
        theater_forget_marker=forget_marker,
    )


def _sidecar(root, name="Role"):
    with open(root / name / "theater_retractions.json", encoding="utf-8") as handle:
        return json.load(handle)


@pytest.mark.unit
def test_story_forget_tombstone_drops_writes_without_its_marker_and_survives_restart(tmp_path):
    mgr, name, recent_path = _manager(tmp_path)
    marker = asyncio.run(mgr.record_theater_story_forget(name, "story_rain"))
    assert isinstance(marker, str) and len(marker) >= 32

    restarted, _, _ = _manager(tmp_path)
    for manager, carried in ((mgr, None), (restarted, None), (restarted, "stale_marker")):
        with pytest.raises(TheaterEpisodeRetracted):
            asyncio.run(manager.upsert_theater_episode(
                _capsule(), name, archive_request_id="late", archive_attempt=1,
                forget_marker=carried,
            ))
    assert _disk_keys(recent_path) == []

    # Other stories are untouched, and an archive issued after the forget lands.
    asyncio.run(restarted.upsert_theater_episode(
        _capsule(story_id="story_other"), name, archive_request_id="other", archive_attempt=1,
    ))
    asyncio.run(restarted.upsert_theater_episode(
        _capsule(session_id="session_new"), name, archive_request_id="new",
        archive_attempt=1, forget_marker=marker,
    ))
    assert _disk_keys(recent_path) == [("story_other", "session_rain"), ("story_rain", "session_new")]


@pytest.mark.unit
def test_story_forget_and_attempt_tombstones_share_the_sidecar(tmp_path):
    mgr, name, recent_path = _manager(tmp_path)

    first = asyncio.run(mgr.record_theater_story_forget(name, "story_rain"))
    # Recording an attempt tombstone keeps the story tombstone, and vice versa.
    asyncio.run(mgr.record_theater_retraction(
        name, story_id="story_rain", session_id="session_rain",
        archive_through_revision=5, archive_request_id="declined", archive_attempt=1,
    ))
    [story_entry] = _sidecar(tmp_path)["forgotten_stories"]
    assert story_entry["story_id"] == "story_rain" and story_entry["forget_marker"] == first

    # A repeated forget issues a new marker: writes carrying the old one were
    # issued before this forget and are dropped as well.
    second = asyncio.run(mgr.record_theater_story_forget(name, "story_rain"))
    assert second != first
    payload = _sidecar(tmp_path)
    assert [entry["archive_request_id"] for entry in payload["entries"]] == ["declined"]
    assert [entry["forget_marker"] for entry in payload["forgotten_stories"]] == [second]
    with pytest.raises(TheaterEpisodeRetracted):
        asyncio.run(mgr.upsert_theater_episode(
            _capsule(session_id="session_mid"), name, archive_request_id="mid",
            archive_attempt=1, forget_marker=first,
        ))
    asyncio.run(mgr.upsert_theater_episode(
        _capsule(session_id="session_new"), name, archive_request_id="new",
        archive_attempt=1, forget_marker=second,
    ))
    assert _disk_keys(recent_path) == [("story_rain", "session_new")]


@pytest.mark.unit
def test_story_forget_tombstone_is_unaffected_by_a_clock_stepping_back(tmp_path, monkeypatch):
    """Forget at T; the clock steps back an hour; a new archive still lands."""
    import memory.recent as recent_module
    from tests.fake_clock import patch_module_clock

    clock = {"now": 1_900_000_000.0}
    patch_module_clock(monkeypatch, recent_module, time=lambda: clock["now"])
    mgr, name, recent_path = _manager(tmp_path)
    marker = asyncio.run(mgr.record_theater_story_forget(name, "story_rain"))

    clock["now"] -= 3600
    with pytest.raises(TheaterEpisodeRetracted):
        asyncio.run(mgr.upsert_theater_episode(
            _capsule(), name, archive_request_id="late", archive_attempt=1,
        ))
    asyncio.run(mgr.upsert_theater_episode(
        _capsule(session_id="session_new"), name, archive_request_id="new",
        archive_attempt=1, forget_marker=marker,
    ))
    assert _disk_keys(recent_path) == [("story_rain", "session_new")]


@pytest.mark.unit
def test_story_forget_tombstone_lapses_after_the_retention_window(tmp_path, monkeypatch):
    """A theater that lost its marker is not locked out of the story forever."""
    import memory.recent as recent_module
    from tests.fake_clock import patch_module_clock

    clock = {"now": 1_900_000_000.0}
    patch_module_clock(monkeypatch, recent_module, time=lambda: clock["now"])
    mgr, name, recent_path = _manager(tmp_path)
    asyncio.run(mgr.record_theater_story_forget(name, "story_rain"))

    clock["now"] += recent_module.THEATER_RETRACTION_TTL_SECONDS - 1
    with pytest.raises(TheaterEpisodeRetracted):
        asyncio.run(mgr.upsert_theater_episode(
            _capsule(), name, archive_request_id="late", archive_attempt=1,
        ))
    clock["now"] += 2
    asyncio.run(mgr.upsert_theater_episode(
        _capsule(), name, archive_request_id="unmarked", archive_attempt=1,
    ))
    assert _disk_keys(recent_path) == [("story_rain", "session_rain")]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_forget_endpoint_drops_late_cache_write_and_admits_a_later_archive(tmp_path):
    from app import memory_server

    mgr, name, recent_path = _manager(tmp_path)
    fake_time = MagicMock()
    fake_time.areconcile_theater_conversations = AsyncMock(return_value={"removed": 0, "stored": 1})
    fake_spawn = AsyncMock()

    with patch.object(memory_server.runtime, "recent_history_manager", mgr), \
         patch.object(memory_server.runtime, "time_manager", fake_time), \
         patch.object(memory_server.post_turn, "_spawn_outbox_post_turn_signals", fake_spawn):
        forgotten = await memory_server.forget_theater_memory(
            name, memory_server.TheaterMemoryForgetRequest(story_id="story_rain"),
        )
        assert forgotten["ok"] is True
        marker = forgotten["forget_marker"]

        # The archive request was issued (and timed out on the theater side)
        # before the forget, so it cannot carry the forget's marker.
        late = await memory_server.cache_conversation(
            _cache_request(memory_server, _capsule(), "timed_out"), name,
        )
        assert late == {"status": "retracted", "count": 0}
        assert _disk_keys(recent_path) == []
        fake_time.areconcile_theater_conversations.assert_awaited_once()
        fake_spawn.assert_not_awaited()

        assert _sidecar(tmp_path, name)["forgotten_stories"][0]["forget_marker"] == marker
        chosen = await memory_server.cache_conversation(
            _cache_request(
                memory_server, _capsule(session_id="session_new"), "new_run",
                forget_marker=marker,
            ),
            name,
        )
    assert chosen == {"status": "cached", "count": 1}
    assert _disk_keys(recent_path) == [("story_rain", "session_new")]


def _step_clocks(monkeypatch, clock):
    """Drive the memory server's and (if it has one) the router's wall clock."""
    import memory.recent as recent_module
    from main_routers import numeric_theater_router
    from tests.fake_clock import _ScopedTime

    fake = _ScopedTime(time=lambda: clock["now"])
    monkeypatch.setattr(recent_module, "time", fake)
    # Builds that stamped archive requests with the router's clock read it here.
    monkeypatch.setattr(numeric_theater_router, "time", fake, raising=False)


@pytest.mark.unit
@pytest.mark.parametrize("clock_step", [0, -3600], ids=["steady_clock", "clock_stepped_back"])
def test_router_forget_after_archive_timeout_fences_the_late_write(tmp_path, monkeypatch, clock_step):
    """End to end: archive times out, forget completes, the late /cache is dropped.

    With ``clock_stepped_back`` the system clock moves an hour into the past
    right after the forget; the next run of the story must still be archived.
    """
    from app import memory_server
    from tests.unit.test_theater_numeric_v2_router import _client, _ended_archive_payload

    clock = {"now": time.time()}
    _step_clocks(monkeypatch, clock)
    catgirl = "测试猫娘"
    mgr, _, recent_path = _manager(tmp_path / "memory", catgirl)
    fake_time = MagicMock()
    fake_time.areconcile_theater_conversations = AsyncMock(return_value={"removed": 0, "stored": 1})
    monkeypatch.setattr(memory_server.runtime, "_settle_locks", {})
    monkeypatch.setattr(memory_server.runtime, "recent_history_manager", mgr)
    monkeypatch.setattr(memory_server.runtime, "time_manager", fake_time)
    monkeypatch.setattr(memory_server.post_turn, "_spawn_outbox_post_turn_signals", AsyncMock())
    calls = []
    in_flight = []
    mode = {"cache": "timeout"}

    async def post(url, **kwargs):
        body = kwargs.get("json")
        calls.append((url, body))
        if "/cache/" in url:
            request = memory_server.HistoryRequest(**body)
            if mode["cache"] == "timeout":
                # The memory server keeps the request; the theater gives up.
                in_flight.append(request)
                raise TimeoutError("memory service slow")
            data = await memory_server.cache_conversation(request, catgirl)
            return SimpleNamespace(is_success=True, content=b"{}", json=lambda: data)
        if url.endswith("/theater/retract"):
            # The per-request fence fails; the story tombstone alone must hold.
            return SimpleNamespace(is_success=False, content=b"{}", json=lambda: {})
        if url.endswith("/theater/forget"):
            data = await memory_server.forget_theater_memory(
                catgirl, memory_server.TheaterMemoryForgetRequest(**body),
            )
            return SimpleNamespace(is_success=True, content=b"{}", json=lambda: data)
        raise AssertionError(url)

    monkeypatch.setattr("utils.internal_http_client.get_internal_http_client", lambda: SimpleNamespace(post=post))
    scope = {"story_id": "numeric_v2_contract", "character_id": "character_" + "1" * 32}
    with _client(tmp_path, monkeypatch) as client:
        payload = _ended_archive_payload(client)
        assert client.post("/api/theater-numeric/session/archive", json=payload).status_code == 502
        forgot = client.post("/api/theater-numeric/memory/forget", json=scope)
        assert forgot.status_code == 200, forgot.text
        clock["now"] += clock_step

        # The timed-out write finally reaches the memory server.
        assert len(in_flight) == 1
        late = asyncio.run(memory_server.cache_conversation(in_flight[0], catgirl))
        assert late == {"status": "retracted", "count": 0}
        assert _disk_keys(recent_path) == []

        # Forget also tried to fence the unresolved attempt by request id.
        retracts = [body for url, body in calls if url.endswith("/theater/retract")]
        assert retracts == [{
            "story_id": "numeric_v2_contract",
            "session_id": "gap_session",
            "archive_through_revision": 0,
            "archive_request_id": payload["archive_request_id"],
            "archive_attempt": 1,
        }]

        # A new run of the story, archived after the forget, is remembered.
        mode["cache"] = "deliver"
        new_scope = {"story_id": "numeric_v2_contract", "session_id": "after_forget"}
        started = client.post("/api/theater-numeric/session/start", json={
            **new_scope, "replace_existing": True,
        })
        assert started.status_code == 200, started.text
        ended = client.post("/api/theater-numeric/session/end", json={
            **new_scope, "base_revision": 0, "base_lifecycle_revision": 0,
        }).json()
        archived = client.post("/api/theater-numeric/session/archive", json={
            **new_scope, "revision": 0, "end_receipt_id": ended["end_receipt_id"],
            "archive_request_id": ended["archive_request_id"],
        })
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "written"
    assert _disk_keys(recent_path) == [("numeric_v2_contract", "after_forget")]
    # Only the request issued after the forget carried the forget's marker.
    marker = _sidecar(tmp_path / "memory", catgirl)["forgotten_stories"][0]["forget_marker"]
    cache_markers = [body.get("theater_forget_marker") for url, body in calls if "/cache/" in url]
    assert cache_markers == [None, marker]


@pytest.mark.unit
def test_router_forget_interrupted_before_persisting_the_marker_recovers_on_retry(tmp_path, monkeypatch):
    """Memory forget succeeds but the theater dies before storing the marker.

    The forget intent stays pending (archiving stays blocked), the retried
    forget issues and adopts a fresh marker, the late pre-forget write is still
    dropped, and the next run of the story is archived.
    """
    from app import memory_server
    from services.theater.numeric_v2_archive import NumericV2ArchiveStore
    from tests.unit.test_theater_numeric_v2_router import _client, _ended_archive_payload

    catgirl = "测试猫娘"
    mgr, _, recent_path = _manager(tmp_path / "memory", catgirl)
    fake_time = MagicMock()
    fake_time.areconcile_theater_conversations = AsyncMock(return_value={"removed": 0, "stored": 1})
    monkeypatch.setattr(memory_server.runtime, "_settle_locks", {})
    monkeypatch.setattr(memory_server.runtime, "recent_history_manager", mgr)
    monkeypatch.setattr(memory_server.runtime, "time_manager", fake_time)
    monkeypatch.setattr(memory_server.post_turn, "_spawn_outbox_post_turn_signals", AsyncMock())
    in_flight = []
    markers = []
    cache_markers = []
    mode = {"cache": "timeout"}

    async def post(url, **kwargs):
        body = kwargs.get("json")
        if "/cache/" in url:
            request = memory_server.HistoryRequest(**body)
            cache_markers.append(request.theater_forget_marker)
            if mode["cache"] == "timeout":
                in_flight.append(request)
                raise TimeoutError("memory service slow")
            data = await memory_server.cache_conversation(request, catgirl)
            return SimpleNamespace(is_success=True, content=b"{}", json=lambda: data)
        if url.endswith("/theater/retract"):
            return SimpleNamespace(is_success=False, content=b"{}", json=lambda: {})
        if url.endswith("/theater/forget"):
            data = await memory_server.forget_theater_memory(
                catgirl, memory_server.TheaterMemoryForgetRequest(**body),
            )
            markers.append(data["forget_marker"])
            return SimpleNamespace(is_success=True, content=b"{}", json=lambda: data)
        raise AssertionError(url)

    monkeypatch.setattr("utils.internal_http_client.get_internal_http_client", lambda: SimpleNamespace(post=post))
    real_record = NumericV2ArchiveStore.record_forget_marker
    crashes = {"left": 1}

    def record_or_crash(self, *args, **kwargs):
        if crashes["left"]:
            crashes["left"] -= 1
            raise OSError("process died before the marker reached disk")
        return real_record(self, *args, **kwargs)

    monkeypatch.setattr(NumericV2ArchiveStore, "record_forget_marker", record_or_crash)
    scope = {"story_id": "numeric_v2_contract", "character_id": "character_" + "1" * 32}
    store = NumericV2ArchiveStore(tmp_path / "theater")
    with _client(tmp_path, monkeypatch) as client:
        payload = _ended_archive_payload(client)
        assert client.post("/api/theater-numeric/session/archive", json=payload).status_code == 502
        first = client.post("/api/theater-numeric/memory/forget", json=scope)
        assert first.status_code == 502
        assert store.forget_marker(scope["story_id"], scope["character_id"]) == ""
        # The unfinished forget keeps archiving blocked until it is retried.
        blocked = client.post("/api/theater-numeric/session/archive", json=payload)
        assert blocked.status_code == 409
        assert blocked.json()["reason"] == "numeric_theater_memory_forget_pending"

        retried = client.post("/api/theater-numeric/memory/forget", json=scope)
        assert retried.status_code == 200, retried.text
        assert len(markers) == 2 and markers[0] != markers[1]
        assert store.forget_marker(scope["story_id"], scope["character_id"]) == markers[1]

        late = asyncio.run(memory_server.cache_conversation(in_flight[0], catgirl))
        assert late == {"status": "retracted", "count": 0}

        mode["cache"] = "deliver"
        new_scope = {"story_id": "numeric_v2_contract", "session_id": "after_forget"}
        assert client.post("/api/theater-numeric/session/start", json={
            **new_scope, "replace_existing": True,
        }).status_code == 200
        ended = client.post("/api/theater-numeric/session/end", json={
            **new_scope, "base_revision": 0, "base_lifecycle_revision": 0,
        }).json()
        archived = client.post("/api/theater-numeric/session/archive", json={
            **new_scope, "revision": 0, "end_receipt_id": ended["end_receipt_id"],
            "archive_request_id": ended["archive_request_id"],
        })
    assert archived.status_code == 200, archived.text
    assert archived.json()["status"] == "written"
    assert cache_markers == [None, markers[1]]
    assert _disk_keys(recent_path) == [("numeric_v2_contract", "after_forget")]


@pytest.mark.unit
def test_forget_markers_are_scoped_validated_and_purged_with_their_character(tmp_path):
    from main_routers.characters_router.crud import collect_numeric_v2_character_purge
    from services.theater import numeric_v2_maintenance as maintenance
    from services.theater.numeric_v2_archive import NumericV2ArchiveError, NumericV2ArchiveStore

    theater = tmp_path / "theater"
    store = NumericV2ArchiveStore(theater)
    assert store.forget_marker("story_rain", "character_lan") == ""
    store.record_forget_marker("story_rain", "character_lan", "marker_lan")
    store.record_forget_marker("story_rain", "character_other", "marker_other")
    assert store.forget_marker("story_rain", "character_lan") == "marker_lan"
    assert store.forget_marker("story_other", "character_lan") == ""
    with pytest.raises(NumericV2ArchiveError):
        store.record_forget_marker("story_rain", "character_lan", " ")

    [lan_marker] = store.forget_marker_paths_for_character("character_lan")
    purge = asyncio.run(collect_numeric_v2_character_purge(
        theater, character_id="character_lan", legacy_catgirl_name="Lan",
    ))
    assert lan_marker in purge.purge_targets()
    # The durable purge intent may name the marker directory.
    intent = maintenance.write_character_purge_intent(
        theater, character_id="character_lan", legacy_catgirl_name="Lan",
        targets=purge.purge_targets(),
    )
    result = maintenance.recover_character_purge_intents(theater, {})
    assert result == {"purge_intents_applied": 1, "purge_intents_discarded": 0}
    assert not intent.exists() and not lan_marker.exists()
    assert store.forget_marker("story_rain", "character_other") == "marker_other"

    lan_marker.parent.mkdir(parents=True, exist_ok=True)
    lan_marker.write_text(json.dumps({"schema": "wrong"}), encoding="utf-8")
    with pytest.raises(NumericV2ArchiveError):
        store.forget_marker("story_rain", "character_lan")
