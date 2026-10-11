"""Archive clocks and fiction budgets must not rewrite ordinary chat behavior."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, text

from config import TIME_ORIGINAL_TABLE_NAME
from memory.theater_budget import bound_theater_history
from memory.timeindex import TimeIndexedMemory
from services.theater.numeric_v2_archive import build_numeric_v2_memory_messages
from utils.llm_client import HumanMessage, SystemMessage, messages_to_dict
from utils.llm_client.history import SQLChatMessageHistory


def _capsule(session, content="short summary", **metadata):
    return SystemMessage(content=content, metadata={
        "source": "theater_numeric_v2", "memory_tier": "episode_summary",
        "story_id": "story", "session_id": session,
        "story_title": "Story", "episode_summary": content, **metadata,
    })


def _time_manager(tmp_path, monkeypatch):
    path = tmp_path / "time_indexed.db"
    url = f"sqlite:///{path}"
    SQLChatMessageHistory(connection_string=url, session_id="ordinary",
                          table_name=TIME_ORIGINAL_TABLE_NAME).add_message(HumanMessage(content="hello"))
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(text(f"ALTER TABLE {TIME_ORIGINAL_TABLE_NAME} ADD COLUMN timestamp DATETIME"))
        connection.execute(text(f"UPDATE {TIME_ORIGINAL_TABLE_NAME} SET timestamp = :timestamp"),
                           {"timestamp": datetime(2024, 1, 2, 12)})
    manager = TimeIndexedMemory(recent_history_manager=None)
    manager.engines["Role"] = engine
    manager.db_paths["Role"] = str(path)
    monkeypatch.setattr(manager, "_assert_timeindex_writable", lambda _name: None)
    monkeypatch.setattr(manager, "_ensure_engine_exists", lambda *a, **kw: True)
    return manager


def test_delayed_archive_and_retry_use_each_episodes_performance_clock(tmp_path, monkeypatch):
    manager = _time_manager(tmp_path, monkeypatch)
    earlier = _capsule("a", performed_at="2020-01-02T03:04:05")
    later = _capsule("b", performed_at="2023-05-06T07:08:09")
    events = {"story": ("theater-story-stable", [earlier, later])}
    for archive_time in [datetime(2030, 1, 1), datetime(2040, 1, 1)]:
        manager.reconcile_theater_conversations(events, "Role", timestamp=archive_time)
        assert manager.get_last_conversation_time("Role") == datetime(2024, 1, 2, 12)
        rows = manager.retrieve_original_by_timeframe("Role", datetime(2019, 1, 1), datetime(2023, 12, 31))
        assert [str(row[0])[:19] for row in rows] == ["2020-01-02 03:04:05", "2023-05-06 07:08:09"]
    manager.engines["Role"].dispose()


@pytest.mark.parametrize("clock", ["", "not-a-date"])
def test_unknown_performance_time_never_becomes_archive_time(tmp_path, monkeypatch, clock):
    manager = _time_manager(tmp_path, monkeypatch)
    manager.reconcile_theater_conversations(
        {"story": ("theater-story-stable", [_capsule("a", performed_at=clock)])}, "Role",
        timestamp=datetime(2030, 1, 1),
    )
    assert manager.get_last_conversation_time("Role") == datetime(2024, 1, 2, 12)
    with manager.engines["Role"].connect() as connection:
        assert connection.execute(text(
            f"SELECT timestamp FROM {TIME_ORIGINAL_TABLE_NAME} WHERE session_id='theater-story-stable'"
        )).scalar() is None
    manager.engines["Role"].dispose()


def test_legacy_capsule_without_clock_does_not_inherit_an_archive_date(tmp_path, monkeypatch):
    manager = _time_manager(tmp_path, monkeypatch)
    events = {"story": ("theater-story-stable", [_capsule("legacy")])}
    manager.reconcile_theater_conversations(events, "Role", timestamp=datetime(2030, 1, 1))
    assert manager.get_last_conversation_time("Role") == datetime(2030, 1, 1)
    manager.reconcile_theater_conversations(events, "Role")
    assert manager.get_last_conversation_time("Role") == datetime(2024, 1, 2, 12)
    manager.engines["Role"].dispose()


def test_memory_metadata_uses_archived_revision_not_latest_or_receipt_clock():
    session = SimpleNamespace(
        revision=2, story_package_id="story", session_id="session", status="active",
        opening_performed_at="2020-01-01T10:00:00", opening_performance={},
        performance_history=[
            {"revision": 1, "performance": "First", "performed_at": "2020-01-01T11:00:00"},
            {"revision": 2, "performance": "Later", "performed_at": "2020-01-02T12:00:00"},
        ],
    )
    result = build_numeric_v2_memory_messages(title="Story", session=session, ending=None,
                                             archive_through_revision=1)
    assert result[0]["metadata"]["performed_at"] == "2020-01-01T11:00:00"


def test_theater_budget_keeps_newest_capsules_without_touching_ordinary_rows(monkeypatch):
    from memory import theater_budget
    monkeypatch.setattr(theater_budget, "count_tokens", lambda _: 1)
    monkeypatch.setattr(theater_budget, "THEATER_MEMORY_BUDGET_TOKENS", 2)
    ordinary = [HumanMessage(content="before"), HumanMessage(content="after")]
    history = [ordinary[0], *[_capsule(str(i)) for i in range(4)], ordinary[1]]
    retained = bound_theater_history(history)
    assert retained == [ordinary[0], history[3], history[4], ordinary[1]]


def test_oversized_capsule_metadata_cannot_bypass_budget(monkeypatch):
    from memory import theater_budget
    monkeypatch.setattr(theater_budget, "count_tokens", len)
    monkeypatch.setattr(theater_budget, "THEATER_MEMORY_BUDGET_TOKENS", 500)
    history = [_capsule("small"), _capsule("huge", ending_titles_seen=["x" * 1000])]
    assert bound_theater_history(history) == [history[0]]


def test_theater_does_not_reduce_ordinary_hard_cap_retention(tmp_path, monkeypatch):
    from tests.unit.test_recent_compression_failure import _make_manager, _write_recent, _read_recent
    monkeypatch.setattr("memory.recent.RECENT_HARD_CAP_TOKENS", 80)
    monkeypatch.setattr("utils.tokenize.count_tokens", len)
    monkeypatch.setattr("memory.theater_budget.count_tokens", lambda _: 1)
    monkeypatch.setattr("memory.recent.assert_cloudsave_writable", lambda *a, **kw: None)
    manager, name = _make_manager(tmp_path)
    ordinary = [HumanMessage(content=str(i) * 20) for i in range(12)]
    _write_recent(manager.log_file_path[name], ordinary)
    asyncio.run(manager.enforce_hard_cap(name))
    expected = messages_to_dict(_read_recent(manager.log_file_path[name]))
    capsule = _capsule("a", content="t" * 1000)
    _write_recent(manager.log_file_path[name], [ordinary[0], capsule, *ordinary[1:]])
    asyncio.run(manager.enforce_hard_cap(name))
    final = _read_recent(manager.log_file_path[name])
    assert messages_to_dict([m for m in final if m.metadata.get("source") != "theater_numeric_v2"]) == expected
    assert messages_to_dict([m for m in final if m.metadata.get("source") == "theater_numeric_v2"]) == messages_to_dict([capsule])


def test_upsert_bounds_theater_metadata_without_pruning_chat():
    from memory.recent import _merge_theater_episode_summary
    from memory.theater_budget import THEATER_MEMORY_BUDGET_TOKENS, theater_capsule_cost
    ordinary = HumanMessage(content="chat " * 2000)
    history = [ordinary]
    for i in range(30):
        history, _ = _merge_theater_episode_summary(
            history, _capsule(str(i), "演绎摘要 " * 50, story_id=f"story_{i}"),
        )
    assert history[0] is ordinary
    theater = [m for m in history if m is not ordinary]
    assert 0 < len(theater) < 30
    cost = sum(theater_capsule_cost(m) for m in theater)
    assert cost <= THEATER_MEMORY_BUDGET_TOKENS


def test_prompt_budget_includes_rendered_metadata_and_preserves_ordinary(monkeypatch):
    from app.memory_server import routes
    monkeypatch.setattr("memory.theater_budget.THEATER_MEMORY_BUDGET_TOKENS", 10)
    monkeypatch.setattr("utils.tokenize.count_tokens", len)
    monkeypatch.setattr(routes, "get_theater_memory_context", lambda lang, **kw: kw["summary"])
    ordinary = HumanMessage(content="o" * 1000)
    old, new = _capsule("a", "older"), _capsule("b", "newer")
    rendered = list(routes._iter_theater_rendered_history([old, ordinary, new], lang="en", name="Role", master="Master"))
    assert rendered == [(ordinary, None), (new, "newer")]


def test_background_hard_cap_does_not_evict_indexed_theater_capsules(tmp_path, monkeypatch):
    from tests.unit.test_recent_compression_failure import _make_manager, _write_recent, _read_recent
    monkeypatch.setattr("memory.recent.RECENT_HARD_CAP_TOKENS", 80)
    monkeypatch.setattr("utils.tokenize.count_tokens", len)
    monkeypatch.setattr("memory.theater_budget.THEATER_MEMORY_BUDGET_TOKENS", 1)
    monkeypatch.setattr("memory.recent.assert_cloudsave_writable", lambda *a, **kw: None)
    manager, name = _make_manager(tmp_path)
    capsule = _capsule("indexed", content="old summary" * 100)
    ordinary = [HumanMessage(content=str(i) * 20) for i in range(12)]
    _write_recent(manager.log_file_path[name], [capsule, *ordinary])
    asyncio.run(manager.enforce_hard_cap(name))
    retained = _read_recent(manager.log_file_path[name])
    assert messages_to_dict(retained[:1]) == messages_to_dict([capsule])
    assert len(retained) < 13


def test_oversized_episode_upsert_rejects_before_changing_recent(tmp_path, monkeypatch):
    from tests.unit.test_recent_compression_failure import _make_manager, _write_recent, _read_recent
    monkeypatch.setattr("memory.theater_budget.THEATER_MEMORY_BUDGET_TOKENS", 1)
    monkeypatch.setattr("memory.recent.assert_cloudsave_writable", lambda *a, **kw: None)
    manager, name = _make_manager(tmp_path)
    previous = [HumanMessage(content="ordinary chat"), _capsule("existing")]
    _write_recent(manager.log_file_path[name], previous)
    with pytest.raises(ValueError, match="theater_episode_budget_exceeded"):
        asyncio.run(manager.upsert_theater_episode(_capsule("too-large", "summary" * 1000), name))
    assert messages_to_dict(_read_recent(manager.log_file_path[name])) == messages_to_dict(previous)


@pytest.mark.asyncio
async def test_live_clocks_survive_restore_and_actor_cannot_forge_turn_clock(tmp_path):
    from tests.unit.test_theater_numeric_v2_runtime import _binding, _opening, _branch_story
    from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime, TurnRequestV2
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), tmp_path)
    stored = await runtime.start_session(session_id="clock_session", catgirl_binding=_binding(), opening_performance=_opening())
    assert datetime.fromisoformat(stored.session.opening_performed_at).tzinfo is not None
    outcome = runtime.prepare_turn(stored, TurnRequestV2("clock_turn", 0, "hello"), (), scene_complete=False)
    committed = await runtime.commit_turn(outcome, {"performance": "Hello", "suggested_inputs": [], "performed_at": "forged"})
    clock = committed.session.performance_history[-1]["performed_at"]
    assert datetime.fromisoformat(clock).tzinfo is not None
    restored = await runtime.restore_session("clock_session")
    assert restored.session.opening_performed_at == stored.session.opening_performed_at
    assert restored.session.performance_history[-1]["performed_at"] == clock
