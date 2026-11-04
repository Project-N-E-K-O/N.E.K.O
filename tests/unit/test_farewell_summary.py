"""Exit farewell card summary: memory count, conversation count and flashback pick."""

import json
import random
import sqlite3
import sys
from datetime import date

import pytest

from main_logic import card_forge_facts as F
from main_logic import farewell_summary as S

pytestmark = pytest.mark.unit

TODAY = date(2026, 10, 28)


def memory(key, **overrides):
    return {
        "id": key,
        "hash": f"hash-{key}",
        "text": f"Memory {key}",
        "importance": 8,
        "created_at": "2026-01-01T12:00:00",
        **overrides,
    }


def pick(active, archive=(), seed=0):
    return S.select_flashback(list(active), list(archive), today=TODAY, rng=random.Random(seed))


def test_flashback_skips_unshareable_rows():
    hidden = [
        memory("private", private=True),
        memory("redacted", redacted=True),
        memory("visit", origin="neko_visit"),
        memory("log", text="session 2026-01-01T12:00 started"),
        memory("blank", text="   "),
    ]
    archived = [
        memory("subject", subject_archived_at="2026-02-01"),
        memory("arb", arbitration_reason="contradicted"),
        memory("arb2", arbitration_archived_at="2026-02-01"),
    ]
    assert pick(hidden, archived) is None
    for seed in range(20):
        assert pick([*hidden, memory("ok")], archived, seed)["id"] == "ok"


def test_flashback_needs_a_date_at_least_seven_days_old():
    assert pick([memory("recent", created_at="2026-10-22T09:00:00")]) is None
    assert pick([memory("undated", created_at=None)]) is None
    result = pick([memory("week", created_at="2026-10-21T23:59:00")])
    assert result == {"id": "week", "text": "Memory week", "date": "2026-10-21"}


def test_flashback_prefers_event_time_and_naive_local_dates():
    result = pick([memory("e", event_start_at="2025-05-05T00:00:00", created_at="2026-01-01T00:00:00")])
    # Naive timestamps are local time: no UTC shift of the calendar day.
    assert result["date"] == "2025-05-05"


def test_flashback_draws_from_oldest_quarter_and_prefers_important():
    old = [memory(f"old-{i}", created_at=f"2024-01-0{i + 1}T00:00:00") for i in range(2)]
    new = [memory(f"new-{i}", created_at="2026-06-01T00:00:00") for i in range(6)]
    trivial = memory("trivial", importance=1, created_at="2020-01-01T00:00:00")
    picked = {pick([*new, *old, trivial], seed=seed)["id"] for seed in range(40)}
    assert picked == {"old-0", "old-1"}
    # Without important memories the least important still qualifies.
    assert pick([trivial])["id"] == "trivial"


def test_flashback_active_row_shadows_archived_copy():
    active = [memory("same", private=True)]
    archive = [memory("same"), memory("other-id", hash="hash-same")]
    assert pick(active, archive) is None


def test_flashback_text_is_collapsed_and_bounded():
    long_text = "  line one\n\nline   two " + "x" * 400
    result = pick([memory("long", text=long_text)])
    assert result["text"].startswith("line one line two x")
    assert len(result["text"]) == S.FLASHBACK_TEXT_MAX_CHARS


def test_flashback_id_is_desktop_safe_for_non_string_ids():
    result = pick([memory(42)])
    assert result["id"].startswith(F._FORGE_WIRE_ID_PREFIX)
    assert all(ch.isalnum() or ch in "_:-" for ch in result["id"])


def make_time_db(path, messages):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE time_indexed_original (id INTEGER PRIMARY KEY, session_id TEXT, message TEXT, timestamp DATETIME)"
    )
    conn.executemany(
        "INSERT INTO time_indexed_original (session_id, message) VALUES ('s', ?)",
        [(m,) for m in messages],
    )
    conn.commit()
    conn.close()


def test_count_user_turns(tmp_path):
    db = tmp_path / "time_indexed.db"
    assert S.count_user_turns(db) is None
    human = json.dumps({"type": "human", "data": {"content": "hi"}})
    ai = json.dumps({"type": "ai", "data": {"content": "meow"}})
    make_time_db(db, [human, ai, human, ai, "{broken", human])
    assert S.count_user_turns(db) == 3
    assert not (tmp_path / "time_indexed.db-journal").exists()


def test_count_user_turns_without_table(tmp_path):
    db = tmp_path / "time_indexed.db"
    sqlite3.connect(db).close()
    assert S.count_user_turns(db) is None


class _FakeConfig:
    def __init__(self, time_store, memory_dir=None):
        self.time_store = time_store
        self.memory_dir = memory_dir

    def get_character_data(self):
        return ("Master", "Neko", {}, {}, {}, {"Neko": ""}, self.time_store, {}, {})


@pytest.fixture
def summary_env(tmp_path, monkeypatch):
    char_dir = tmp_path / "Neko"
    char_dir.mkdir()

    async def context(*_args, **_kwargs):
        return F.ActiveNekoContext(
            master_name="Master", lanlan_name="Neko", memory_dir=tmp_path,
            facts_path=char_dir / "facts.json", source="test",
        )

    monkeypatch.setattr(F, "resolve_active_neko_context", context)
    import utils.config_manager as cm

    config = _FakeConfig({}, memory_dir=tmp_path)
    monkeypatch.setattr(cm, "get_config_manager", lambda: config)
    return char_dir, config


@pytest.mark.asyncio
async def test_build_summary(summary_env):
    char_dir, _ = summary_env
    active = [memory("a"), memory("b", created_at="2025-02-02T00:00:00")]
    archive = [memory("a"), memory("c", text="log 2026-01-01T00:00 tick")]
    (char_dir / "facts.json").write_text(json.dumps(active), encoding="utf-8")
    (char_dir / "facts_archive.json").write_text(json.dumps(archive), encoding="utf-8")
    make_time_db(char_dir / "time_indexed.db", [json.dumps({"type": "human"})] * 4)

    payload = await S.build_farewell_summary()
    assert payload["memory_count"] == 2
    assert payload["conversation_count"] == 4
    assert payload["flashback"]["id"] in {"a", "b"}


@pytest.mark.asyncio
async def test_build_summary_honours_time_store_and_omits_missing_db(summary_env, tmp_path):
    char_dir, config = summary_env
    payload = await S.build_farewell_summary()
    assert payload == {"memory_count": 0, "flashback": None}

    custom = tmp_path / "elsewhere.db"
    make_time_db(custom, [json.dumps({"type": "human"})])
    config.time_store = {"Neko": str(custom)}
    payload = await S.build_farewell_summary()
    assert payload["conversation_count"] == 1


@pytest.mark.asyncio
async def test_build_summary_without_active_character(monkeypatch):
    async def context(*_args, **_kwargs):
        return F.ActiveNekoContext(
            master_name="", lanlan_name="", memory_dir=None, facts_path=None, source="unresolved",
        )

    monkeypatch.setattr(F, "resolve_active_neko_context", context)
    assert await S.build_farewell_summary() == {"flashback": None}


@pytest.mark.asyncio
async def test_route_swallows_failures(monkeypatch):
    from main_routers import memory_router

    async def boom():
        raise RuntimeError("x")

    monkeypatch.setattr(S, "build_farewell_summary", boom)
    assert await memory_router.get_farewell_summary() == {"flashback": None}


def test_flashback_visit_row_does_not_hide_ordinary_archived_copy():
    active = [memory("same", origin="neko_visit")]
    assert pick(active, [memory("same")])["id"] == "same"


@pytest.mark.parametrize(
    "folder_name",
    [
        "A#1 %20 猫",
        # Windows forbids "?" in file names.
        pytest.param("A?x", marks=pytest.mark.skipif(sys.platform == "win32", reason="'?' is not a valid Windows file name character")),
    ],
)
def test_count_user_turns_with_uri_special_characters_in_path(tmp_path, folder_name):
    folder = tmp_path / folder_name
    folder.mkdir()
    db = folder / "time_indexed.db"
    make_time_db(db, [json.dumps({"type": "human"})] * 2)
    assert S.count_user_turns(db) == 2


def test_flashback_ineligible_duplicate_does_not_hide_valid_copy():
    log_copy = memory("dup", text="imported 2026-01-01T00:00 row")
    assert pick([log_copy, memory("dup")])["id"] == "dup"
    archived = memory("dup2", subject_archived_at="2026-02-01")
    assert pick([], [archived, memory("dup2")])["id"] == "dup2"


@pytest.mark.asyncio
async def test_build_summary_reads_time_index_from_config_memory_dir(summary_env, tmp_path):
    _, config = summary_env
    # The facts context may follow NEKO_MEMORY_DIR; the time index writer does not.
    writer_dir = tmp_path / "writer"
    (writer_dir / "Neko").mkdir(parents=True)
    make_time_db(writer_dir / "Neko" / "time_indexed.db", [json.dumps({"type": "human"})] * 3)
    config.memory_dir = writer_dir
    assert (await S.build_farewell_summary())["conversation_count"] == 3

    # A registered but empty time_store path is unreadable, as for the writer.
    config.time_store = {"Neko": ""}
    assert "conversation_count" not in await S.build_farewell_summary()


@pytest.mark.asyncio
async def test_route_swallows_import_failure(monkeypatch):
    from main_routers import memory_router

    monkeypatch.setitem(sys.modules, "main_logic.farewell_summary", None)
    assert await memory_router.get_farewell_summary() == {"flashback": None}
