"""Confirmed archive recovery, durable run numbers and nonblocking list snapshots."""

import asyncio
import json
from unittest.mock import AsyncMock

import pytest

from tests.unit.test_theater_memory_drop_rollback import _manager, _capsule
from tests.unit.test_theater_memory_time_budget import _time_manager
from utils.llm_client import messages_to_dict


@pytest.fixture(autouse=True)
def _writable_memory(monkeypatch):
    monkeypatch.setattr("memory.recent.assert_cloudsave_writable", lambda *a, **kw: None)


@pytest.mark.asyncio
@pytest.mark.parametrize("through", [10, 12])
@pytest.mark.parametrize("delivered", [True, False])
async def test_declined_archive_restores_confirmed_recent_and_sqlite(
    tmp_path, monkeypatch, through, delivered,
):
    from app.memory_server import routes, runtime, post_turn, gates

    manager, name, path = _manager(tmp_path)
    index = _time_manager(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, "recent_history_manager", manager)
    monkeypatch.setattr(runtime, "time_manager", index)
    monkeypatch.setattr(routes, "_resolve_foreground_memory_language", AsyncMock(return_value="en"))
    monkeypatch.setattr(post_turn, "_spawn_outbox_post_turn_signals", AsyncMock())
    monkeypatch.setattr(gates, "_aclear_review_clean", AsyncMock())

    async def cache(message, request_id):
        return await routes.cache_conversation(routes.HistoryRequest(
            input_history=json.dumps(messages_to_dict([message])),
            idempotency_key=request_id, theater_archive_attempt=1,
        ), name)

    earlier = _capsule("story", "session")
    earlier.metadata["performed_at"] = "2020-01-01T12:00:00"
    assert (await cache(earlier, "confirmed"))["status"] == "cached"
    confirmed = messages_to_dict(await manager.aget_recent_history(name))
    later = _capsule("story", "session")
    later.content = "declined later summary"
    later.metadata.update(episode_summary=later.content, archive_through_revision=through,
                          performed_at="2021-01-01T12:00:00")
    if delivered:
        # /cache commits; its caller then times out or fails cold-archive commit.
        assert (await cache(later, "declined"))["status"] == "cached"
        assert messages_to_dict(await manager.aget_recent_history(name)) != confirmed
        # Retrying an unknown response must keep the original recovery copy.
        assert (await cache(later, "declined"))["status"] == "cached"
    request = routes.TheaterEpisodeRetractRequest(
        story_id="story", session_id="session", archive_through_revision=through,
        archive_request_id="declined", archive_attempt=1,
    )
    for _ in range(2):
        assert (await routes.retract_theater_episode(name, request))["ok"]
        assert messages_to_dict(await manager.aget_recent_history(name)) == confirmed
        events = routes._theater_index_events(name, await manager.aget_recent_history(name))
        from sqlalchemy import text
        from config import TIME_ORIGINAL_TABLE_NAME
        event_id = next(iter(events.values()))[0]
        with index.engines[name].connect() as connection:
            rows = connection.execute(text(
                f"SELECT timestamp FROM {TIME_ORIGINAL_TABLE_NAME} WHERE session_id=:session"
            ), {"session": event_id}).all()
        assert len(rows) == 1 and str(rows[0][0]).startswith("2020-01-01")
    assert (await cache(later, "declined"))["status"] == "retracted"
    with open(path, encoding="utf-8") as handle:
        assert json.load(handle) == confirmed
    index.engines[name].dispose()


@pytest.mark.asyncio
async def test_run_numbers_survive_eviction_restart_retry_and_forget(tmp_path, monkeypatch):
    from memory import theater_budget

    monkeypatch.setattr(theater_budget, "THEATER_MEMORY_BUDGET_TOKENS", 1)
    monkeypatch.setattr(theater_budget, "theater_capsule_cost", lambda _: 1)
    manager, name, _ = _manager(tmp_path)
    for session, expected in [("first", 1), ("second", 2)]:
        result = await manager.upsert_theater_episode(_capsule("story", session), name)
        assert result.metadata["run_index"] == expected
    await manager.upsert_theater_episode(_capsule("other", "other"), name)
    assert not any(m.metadata.get("story_id") == "story" for m in await manager.aget_recent_history(name))
    manager, name, _ = _manager(tmp_path)
    for session, expected in [("third", 3), ("third", 3), ("first", 1)]:
        result = await manager.upsert_theater_episode(_capsule("story", session), name)
        assert result.metadata["run_index"] == expected
        assert result.metadata["story_run_count"] == 3
    marker = await manager.record_theater_story_forget(name, "story")
    await manager.forget_theater_story("story", name)
    result = await manager.upsert_theater_episode(_capsule("story", "new"), name, forget_marker=marker)
    assert result.metadata["run_index"] == result.metadata["story_run_count"] == 1


@pytest.mark.asyncio
async def test_late_evicted_episode_and_aggregate_retry_preserve_newer_runs(tmp_path, monkeypatch):
    from app.memory_server import routes, runtime, post_turn, gates
    from sqlalchemy import text
    from config import TIME_ORIGINAL_TABLE_NAME

    manager, name, _ = _manager(tmp_path)
    index = _time_manager(tmp_path, monkeypatch)
    monkeypatch.setattr(runtime, 'recent_history_manager', manager)
    monkeypatch.setattr(runtime, 'time_manager', index)
    monkeypatch.setattr(routes, '_resolve_foreground_memory_language', AsyncMock(return_value='en'))
    monkeypatch.setattr(post_turn, '_spawn_outbox_post_turn_signals', AsyncMock())
    monkeypatch.setattr(gates, '_aclear_review_clean', AsyncMock())

    async def cache(session):
        message = _capsule('story', session)
        return await routes.cache_conversation(routes.HistoryRequest(
            input_history=json.dumps(messages_to_dict([message])),
        ), name)

    try:
        for session in ('A', 'B', 'C', 'D'):
            assert (await cache(session))['status'] == 'cached'
        def sessions(history):
            return [(m.metadata['session_id'], m.metadata['run_index']) for m in history]
        kept = [('B', 2), ('C', 3), ('D', 4)]
        assert sessions(await manager.aget_recent_history(name)) == kept
        # A was numbered before eviction. A late retry is acknowledged without
        # reinserting it or dropping any newer hot capsule/time-index content.
        assert (await cache('A'))['status'] == 'cached'
        assert sessions(await manager.aget_recent_history(name)) == kept
        # The story total has advanced since B was stored. Updating only that
        # aggregate must not move an otherwise identical B retry to the tail.
        assert (await cache('B'))['status'] == 'cached'
        history = await manager.aget_recent_history(name)
        assert sessions(history) == kept
        events = routes._theater_index_events(name, history)
        event_id, indexed = events['story']
        assert sessions(indexed) == kept
        with index.engines[name].connect() as connection:
            rows = connection.execute(text(
                f'SELECT message FROM {TIME_ORIGINAL_TABLE_NAME} WHERE session_id=:session ORDER BY id'
            ), {'session': event_id}).all()
        metadata = [json.loads(row[0])['data']['metadata'] for row in rows]
        assert [(item['session_id'], item['run_index']) for item in metadata] == kept
    finally:
        index.engines[name].dispose()


def test_summary_and_recovery_copy_are_not_charged_again():
    from memory.theater_budget import theater_capsule_cost

    capsule = _capsule("story", "session")
    expected = theater_capsule_cost(capsule)
    capsule.metadata["ending_summary"] = capsule.metadata["episode_summary"]
    capsule.metadata["_theater_previous_episode"] = {"content": "backup " * 10000}
    assert theater_capsule_cost(capsule) == expected
    capsule.metadata["ending_titles_seen"] = ["long title " * 1000]
    assert theater_capsule_cost(capsule) > expected


@pytest.mark.asyncio
async def test_memory_list_does_not_wait_for_settle_lock(tmp_path, monkeypatch):
    from app.memory_server import routes, runtime

    manager, name, _ = _manager(tmp_path)
    await manager.upsert_theater_episode(_capsule("deleted-story", "session"), name)
    monkeypatch.setattr(runtime, "recent_history_manager", manager)
    monkeypatch.setattr(runtime._config_manager, "aload_characters", AsyncMock(return_value={"猫娘": {name: {}}}))
    lock = runtime._get_settle_lock(name)
    async with lock:
        result = await asyncio.wait_for(routes.list_theater_memory_stories(name), timeout=1)
    assert result["stories"][0]["story_id"] == "deleted-story"


@pytest.mark.asyncio
@pytest.mark.parametrize('corruption', ['{', '', '[]', '{"story":{"sessions":{},"total":0}}'])
async def test_run_state_migrates_known_total_and_quarantines_corruption(tmp_path, corruption):
    from pathlib import Path
    from utils import recent_file
    from memory.recent import THEATER_RUNS_FILENAME

    manager, name, path = _manager(tmp_path)
    old = _capsule("story", "old")
    old.metadata.update(run_index=1, story_run_count=10)
    recent_file.write_recent_payload(path, messages_to_dict([old]))
    result = await manager.upsert_theater_episode(_capsule("story", "new"), name)
    assert result.metadata["run_index"] == 11
    before = Path(path).read_bytes()
    runs_path = Path(recent_file.recent_sidecar_path(path, THEATER_RUNS_FILENAME))
    runs_path.write_text(corruption, encoding="utf-8")
    result = await manager.upsert_theater_episode(_capsule("story", "next"), name)
    assert result.metadata["run_index"] == 12
    backups = list(runs_path.parent.glob("theater_runs.json.*.corrupt"))
    assert len(backups) == 1 and backups[0].read_text(encoding="utf-8") == corruption
    assert Path(path).read_bytes() != before
    # Corruption must not make explicit forgetting impossible either.
    runs_path.write_text(corruption, encoding="utf-8")
    await manager.record_theater_story_forget(name, "story")
    await manager.forget_theater_story("story", name)
    assert not await manager.aget_recent_history(name)


@pytest.mark.asyncio
async def test_legacy_capsules_share_the_durable_number_allocator(tmp_path):
    from utils import recent_file

    manager, name, path = _manager(tmp_path)
    old = _capsule("story", "old")
    old.metadata["ending_title"] = "Old ending"
    recent_file.write_recent_payload(path, messages_to_dict([old]))
    result = await manager.upsert_theater_episode(_capsule("story", "new"), name)
    assert result.metadata["run_index"] == 2
    assert [m.metadata["run_index"] for m in await manager.aget_recent_history(name)] == [1, 2]
    old.metadata["archive_through_revision"] = 10
    result = await manager.upsert_theater_episode(old, name)
    assert result.metadata["run_index"] == 1
    assert "Old ending" in result.metadata["ending_titles_seen"]


@pytest.mark.asyncio
async def test_unreadable_run_state_does_not_publish_a_forget_marker(tmp_path, monkeypatch):
    import builtins
    from pathlib import Path

    manager, name, path = _manager(tmp_path)
    await manager.upsert_theater_episode(_capsule("story", "session"), name)
    before = Path(path).read_bytes()
    original_open = builtins.open

    def locked_open(file, *args, **kwargs):
        if str(file).endswith('theater_runs.json'):
            raise PermissionError('sharing violation')
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, 'open', locked_open)
    with pytest.raises(PermissionError):
        await manager.record_theater_story_forget(name, 'story')
    assert Path(path).read_bytes() == before
    assert not (Path(path).parent / 'theater_retractions.json').exists()


def test_run_numbers_are_included_in_character_cloud_snapshot(tmp_path):
    from pathlib import Path
    from tests.unit.test_cloudsave_runtime import _make_config_manager, _write_runtime_state
    from utils.cloudsave_runtime import export_local_cloudsave_snapshot
    from utils.file_utils import atomic_write_json

    config = _make_config_manager(tmp_path)
    _write_runtime_state(config, character_name="Role")
    payload = {"story": {"total": 3, "sessions": {"session": 3}}}
    atomic_write_json(Path(config.memory_dir) / "Role" / "theater_runs.json", payload)
    export_local_cloudsave_snapshot(config)
    staged = config.cloudsave_dir / "characters" / "Role" / "memory" / "theater_runs.json"
    assert json.loads(staged.read_text(encoding="utf-8")) == payload
