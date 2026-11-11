"""Cold storage (memory/cold_store.py) and the fact, reflection, persona and sync code around it.

Contracts:
  1. Stubs never carry text, tags or vectors; they carry ``text_sha256``.
  2. Fact demotion (arbitration, subject archival, the legacy cleanup) writes
     to cold shards, cold first, and a retried commit never stores a row twice.
  3. A copy that is both cold and active (or in the searchable archive) is cold.
  4. The background migration moves demoted rows out of facts_archive.json,
     idempotently, and only from the archive window.
  5. Privacy erasure clears cold rows too, and fails whole on an unreadable shard.
  6. Restore: a private row comes back under a new id and stays cold; a group
     row keeps its id and leaves cold storage; the active file is written first.
  7. The sync ``cold`` list: protocol shape, pruned rows stay unlisted.
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

from memory import cold_store
from memory.scopes import MemorySubject
from utils.file_utils import atomic_write_json

NAME = "小天"
GROUP = MemorySubject.group_chat("qq", "111")


def _install(tmp_path):
    from tests.unit.test_subject_archive import _install as install
    return install(str(tmp_path))


def _fact(fid, text, **fields):
    return {"id": fid, "text": text, "entity": "master", "importance": 5, "tags": ["t"],
            "embedding": "AAAA", "hash": fid, "created_at": "2026-06-01T00:00:00",
            "signal_processed": True, **fields}


# ── the store itself ───────────────────────────────────────────────────


def test_stub_drops_the_wording_and_keeps_a_hash():
    row = _fact("f1", "likes tea", version_history=[{"text": "old"}], external_import={"day_fingerprint": "d"})
    stub = cold_store.stub(row)
    assert not {"text", "tags", "embedding", "version_history"} & stub.keys()
    assert stub["text_sha256"] == cold_store.text_sha256("likes tea")
    assert stub["external_import"] == {"day_fingerprint": "d"} and stub["id"] == "f1"
    legacy = cold_store.stub({"text": "no id", "entity": "neko"})
    assert legacy["legacy_id"] == cold_store.legacy_entry_id("neko", "no id")


def test_append_is_idempotent_by_identity_and_status_follows_writes(tmp_path):
    identity = lambda row: row.get("id")  # noqa: E731
    assert cold_store.append(str(tmp_path), [_fact("f1", "a"), _fact("f2", "b")], identity=identity) == 2
    assert cold_store.append(str(tmp_path), [_fact("f1", "a again"), _fact("f3", "c")], identity=identity) == 1
    assert [row["id"] for row in cold_store.status(str(tmp_path))] == ["f1", "f2", "f3"]
    assert all("text" not in row for row in cold_store.status(str(tmp_path)))
    # The cache follows a rewrite of the same shard.
    cold_store.rewrite_rows(str(tmp_path), lambda row: None if row["id"] == "f2" else row)
    assert [row["id"] for row in cold_store.status(str(tmp_path), ids=["f2", "f3"])] == ["f3"]


def test_rewrite_reads_every_shard_before_writing_any(tmp_path):
    cold_store.append(str(tmp_path), [_fact("f1", "a")], now=datetime(2026, 1, 1))
    folder = cold_store.cold_dir(str(tmp_path))
    (tmp_path / "fact_cold" / "2026-01-02_deadbeef.json").write_text("{broken", encoding="utf-8")
    before = {name: (tmp_path / "fact_cold" / name).read_bytes() for name in os.listdir(folder)}
    with pytest.raises(cold_store.ColdStoreUnreadable):
        cold_store.forget(str(tmp_path), lambda row: True)
    assert before == {name: (tmp_path / "fact_cold" / name).read_bytes() for name in os.listdir(folder)}


def test_restore_gives_private_rows_new_ids_and_moves_group_rows_back(tmp_path):
    group_row = _fact("g1", "group line", subject_archived_at="2026-07-01T00:00:00", **GROUP.as_entry_fields())
    private_row = _fact("p1", "private line", arbitration_archived_at="2026-07-01T00:00:00",
                        arbitration_reason="dedup_arbitration", superseded_by="p0", source_sync_id="desktop:p1")
    cold_store.append(str(tmp_path), [private_row, group_row])
    activated = []
    revived = cold_store.restore(str(tmp_path), lambda row: True, activate=activated.extend)
    by_origin = {row.get("restored_from") or row["id"]: row for row in revived}
    assert activated == revived
    assert by_origin["p1"]["id"] != "p1" and "superseded_by" not in by_origin["p1"]
    assert "source_sync_id" not in by_origin["p1"] and by_origin["p1"]["text"] == "private line"
    assert by_origin["g1"]["id"] == "g1" and "subject_archived_at" not in by_origin["g1"]
    # The private cold row stays; the group one left.
    assert [row["id"] for row in cold_store.read_full(str(tmp_path))] == ["p1"]


def test_restore_leaves_cold_storage_alone_when_activation_fails(tmp_path):
    cold_store.append(str(tmp_path), [_fact("g1", "group line", subject_archived_at="x", **GROUP.as_entry_fields())])

    def fail(rows):
        raise OSError("disk gone")
    with pytest.raises(OSError):
        cold_store.restore(str(tmp_path), lambda row: True, activate=fail)
    assert [row["id"] for row in cold_store.read_full(str(tmp_path))] == ["g1"]


# ── FactStore around it ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_arbitration_losers_go_cold_not_to_the_searchable_archive(tmp_path):
    _, fs, *_ = _install(tmp_path)
    fs._facts[NAME] = [_fact("keep", "likes tea"), _fact("lose", "likes tea a lot")]
    await fs.asave_facts(NAME)
    assert await fs.aarchive_arbitrated_facts(NAME, {"lose": {"reason": "dedup", "superseded_by": "keep"}}) == 1
    assert [row["id"] for row in await fs.aload_facts(NAME)] == ["keep"]
    assert not os.path.exists(fs._facts_archive_path(NAME))
    [cold] = cold_store.read_full(fs._character_dir(NAME))
    assert cold["id"] == "lose" and cold["superseded_by"] == "keep" and cold["arbitration_archived_at"]
    full = await fs.aload_facts_full(NAME)
    assert [row["id"] for row in full] == ["keep"]
    stubs = await fs.aload_facts_full(NAME, include_cold_stubs=True)
    assert [row["id"] for row in stubs] == ["keep", "lose"] and "text" not in stubs[1]


@pytest.mark.asyncio
async def test_a_copy_that_is_also_cold_reads_as_cold_and_leaves_at_the_next_save(tmp_path):
    _, fs, *_ = _install(tmp_path)
    directory = fs._character_dir(NAME)
    cold_store.append(directory, [_fact("old", "restored by a cloud save", arbitration_archived_at="2026-07-01T00:00:00")])
    # A cloud save brought back an older facts.json that still holds it.
    atomic_write_json(fs._facts_path(NAME), [_fact("old", "restored by a cloud save"), _fact("new", "fresh")])
    atomic_write_json(fs._facts_archive_path(NAME), [_fact("old", "restored by a cloud save", absorbed=True)])
    assert [row["id"] for row in await fs.aload_facts(NAME)] == ["new"]
    assert [row["id"] for row in await fs.aload_facts_full(NAME)] == ["new"]
    await fs.asave_facts(NAME)
    assert [row["id"] for row in json.load(open(fs._facts_path(NAME), encoding="utf-8"))] == ["new"]


@pytest.mark.asyncio
async def test_migration_moves_demoted_archive_rows_once(tmp_path):
    _, fs, *_ = _install(tmp_path)
    loser = _fact("lose", "folded away", arbitration_archived_at="2026-07-01T00:00:00", superseded_by="keep")
    subject_row = _fact("g1", "quiet group", subject_archived_at="2026-07-01T00:00:00", **GROUP.as_entry_fields())
    absorbed = _fact("abs", "absorbed and searchable", absorbed=True)
    atomic_write_json(fs._facts_archive_path(NAME), [loser, subject_row, absorbed])
    # An interrupted earlier run already wrote one of them to cold storage.
    cold_store.append(fs._character_dir(NAME), [dict(loser)])
    fs.load_facts(NAME)
    with fs._get_lock(NAME):
        assert fs._migrate_cold_rows_locked(NAME) == 2
        assert fs._migrate_cold_rows_locked(NAME) == 0
    assert [row["id"] for row in json.load(open(fs._facts_archive_path(NAME), encoding="utf-8"))] == ["abs"]
    assert sorted(row["id"] for row in cold_store.read_full(fs._character_dir(NAME))) == ["g1", "lose"]


@pytest.mark.asyncio
async def test_migration_runs_in_the_archive_window_of_a_save(tmp_path):
    _, fs, *_ = _install(tmp_path)
    loser = _fact("lose", "folded away", arbitration_archived_at="2026-07-01T00:00:00")
    atomic_write_json(fs._facts_archive_path(NAME), [loser])
    os.utime(fs._facts_archive_path(NAME), (0, 0))  # the 24h archive window is open
    fs._facts[NAME] = [_fact("live", "still here")]
    await fs.asave_facts(NAME)
    assert json.load(open(fs._facts_archive_path(NAME), encoding="utf-8")) == []
    assert [row["id"] for row in cold_store.read_full(fs._character_dir(NAME))] == ["lose"]


@pytest.mark.asyncio
async def test_privacy_erasure_clears_cold_rows_and_fails_whole_on_a_broken_shard(tmp_path):
    _, fs, *_ = _install(tmp_path)
    directory = fs._character_dir(NAME)
    group_row = _fact("g1", "quiet group", subject_archived_at="2026-07-01T00:00:00", **GROUP.as_entry_fields())
    other = _fact("p1", "private", arbitration_archived_at="2026-07-01T00:00:00")
    cold_store.append(directory, [group_row, other], now=datetime(2026, 1, 1))
    fs._facts[NAME] = [_fact("g2", "live group", **GROUP.as_entry_fields())]
    await fs.asave_facts(NAME)
    broken = os.path.join(cold_store.cold_dir(directory), "2026-01-02_deadbeef.json")
    with open(broken, "w", encoding="utf-8") as fh:
        fh.write("{broken")
    with pytest.raises(RuntimeError):
        await fs.aforget_subject(NAME, GROUP)
    assert [row["id"] for row in await fs.aload_facts(NAME)] == ["g2"]  # nothing erased yet
    os.remove(broken)
    stats = await fs.aforget_subject(NAME, GROUP)
    assert stats == {"facts": 1, "facts_archive": 1}
    assert [row["id"] for row in cold_store.read_full(directory)] == ["p1"]


@pytest.mark.asyncio
async def test_cold_restore_entry_adds_a_new_id_once(tmp_path):
    _, fs, *_ = _install(tmp_path)
    directory = fs._character_dir(NAME)
    cold_store.append(directory, [_fact("lose", "folded away", arbitration_archived_at="2026-07-01T00:00:00",
                                        superseded_by="keep")])
    fs._facts[NAME] = [_fact("keep", "kept")]
    await fs.asave_facts(NAME)
    [back] = await fs.arestore_cold_facts(NAME, lambda row: row["id"] == "lose")
    assert back["restored_from"] == "lose" and back["id"] != "lose"
    assert {row["id"] for row in await fs.aload_facts(NAME)} == {"keep", back["id"]}
    assert await fs.arestore_cold_facts(NAME, lambda row: row["id"] == "lose") == []
    assert [row["id"] for row in cold_store.read_full(directory)] == ["lose"]


@pytest.mark.asyncio
async def test_strict_loader_answers_keyed_writes_with_stubs(tmp_path):
    _, fs, *_ = _install(tmp_path)
    cold_store.append(fs._character_dir(NAME), [_fact("v1", "visited", origin="neko_visit", visit_id="v", effect_key="k1",
                                                      arbitration_archived_at="2026-07-01T00:00:00")])
    rows = await fs.aload_facts_strict(NAME, include_archive=True, include_cold_stubs=True)
    assert [(row["id"], row["text_sha256"]) for row in rows] == [("v1", cold_store.text_sha256("visited"))]
    assert "text" not in rows[0]
    assert await fs.aload_facts_strict(NAME, include_archive=True) == []
    assert fs._read_archived_effect_keys(NAME) == {"k1"}


@pytest.mark.asyncio
async def test_reflection_and_persona_loaders_prefer_the_archived_copy(tmp_path):
    from memory.archive_shards import append_to_shard_sync
    _, fs, pm, re, *_ = _install(tmp_path)
    directory = fs._character_dir(NAME)
    reflection = {"id": "r1", "text": "archived line", "entity": "master", "status": "confirmed",
                  "created_at": "2026-06-01T00:00:00", "source_fact_ids": []}
    group_reflection = {**reflection, "id": "rg", **GROUP.as_entry_fields()}
    atomic_write_json(os.path.join(directory, "reflections.json"), [reflection, group_reflection])
    append_to_shard_sync(cold_store.cold_dir(directory, "reflection"), [reflection, group_reflection])
    # Private copies that are also archived read as archived; group ones are restored under their ids.
    assert [row["id"] for row in await re.aload_reflections(NAME)] == ["rg"]
    assert [row["id"] for row in re.load_reflections(NAME)] == ["rg"]
    entry = {"id": "pe", "text": "archived trait", "source": "reflection"}
    atomic_write_json(os.path.join(directory, "persona.json"),
                      {"master": {"facts": [entry, {"id": "live", "text": "live trait"}]}})
    append_to_shard_sync(cold_store.cold_dir(directory, "persona"), [{**entry, "entity_key": "master"}])
    persona = await pm.aensure_persona(NAME)
    assert [row["id"] for row in persona["master"]["facts"]] == ["live"]


# ── sync ───────────────────────────────────────────────────────────────


def _document(entries=(), cold=None):
    value = {"schemaVersion": 3, "completedAt": 1000, "entries": list(entries),
             "evidence": {"retirements": [], "confirmations": []}, "progress": {"a": [], "b": []}}
    if cold is not None:
        value["cold"] = cold
    return value


@pytest.mark.parametrize("item,valid", [
    ({"kind": "fact", "subject": "master", "id": "f1", "status": "superseded", "archivedAt": 1, "supersededById": "f2"}, True),
    ({"kind": "fact", "subject": "master", "id": "f1", "status": "retracted", "archivedAt": 1.5}, True),
    ({"kind": "reflection", "subject": "neko", "id": "r1", "status": "archived", "archivedAt": 0}, True),
    ({"kind": "fact", "subject": "master", "id": "f1", "status": "retracted", "archivedAt": 1, "supersededById": "f2"}, False),
    ({"kind": "persona", "subject": "master", "id": "p1", "status": "superseded", "archivedAt": 1}, False),
    ({"kind": "fact", "subject": "master", "id": "f1", "status": "retracted", "archivedAt": 1, "text": "x"}, False),
    ({"kind": "fact", "subject": "master", "id": "f1", "status": "retracted"}, False),
    ({"kind": "fact", "subject": "group", "id": "f1", "status": "retracted", "archivedAt": 1}, False),
    ({"kind": "fact", "subject": "master", "id": " f1", "status": "retracted", "archivedAt": 1}, False),
    ({"kind": "fact", "subject": "master", "id": "f1", "status": "retracted", "archivedAt": 10 ** 20}, False),
])
def test_protocol_checks_cold_items_as_the_sync_service_does(item, valid):
    from utils.long_term_memory_protocol import document
    if valid:
        assert document(_document(cold=[item]))["cold"] == [item]
    else:
        with pytest.raises(ValueError):
            document(_document(cold=[item]))


def test_protocol_refuses_a_row_both_listed_and_carried_or_listed_twice():
    from utils.long_term_memory_protocol import COLD_MAX, document
    item = {"kind": "fact", "subject": "master", "id": "f1", "status": "retracted", "archivedAt": 1}
    entry = {"kind": "fact", "subject": "master", "id": "f1", "text": "x", "metadata": {}}
    with pytest.raises(ValueError):
        document(_document([entry], cold=[item]))
    with pytest.raises(ValueError):
        document(_document(cold=[item, dict(item)]))
    many = [{**item, "id": f"f{index}"} for index in range(COLD_MAX + 1)]
    with pytest.raises(ValueError):
        document(_document(cold=many))
    assert len(document(_document(cold=many[:COLD_MAX]))["cold"]) == COLD_MAX


def test_a_pruned_row_held_here_is_not_listed_again():
    from memory.long_term_sync import export_document, import_document
    from tests.unit.test_long_term_memory_sync import cold_views
    held = _fact("c1", "cold here", arbitration_archived_at="2026-07-01T00:00:00")
    other = _fact("c2", "cold too", arbitration_archived_at="2026-07-01T00:00:00")
    views = cold_views({"persona": {}, "facts": [], "archive": [], "reflections": []}, fact=[held, other])
    first = export_document(views, 2000)
    assert [item["id"] for item in first["cold"]] == ["c1", "c2"]
    applied = import_document(first, views, merged=True)
    # The organizer trims the list; the next published revision no longer names c1.
    trimmed = {**first, "completedAt": 3000, "cold": first["cold"][1:]}
    applied = import_document(trimmed, {**views, "sync": applied["sync"]}, merged=True)
    assert applied["sync"]["coldPruned"] == [["fact", "master", "c1"]]
    assert [item["id"] for item in export_document({**views, "sync": applied["sync"]}, 4000)["cold"]] == ["c2"]
    # A row that goes cold later is listed as usual.
    later = _fact("c3", "cold later", arbitration_archived_at="2026-08-01T00:00:00")
    views_later = cold_views({"persona": {}, "facts": [], "archive": [], "reflections": []}, fact=[held, other, later])
    assert [item["id"] for item in export_document({**views_later, "sync": applied["sync"]}, 5000)["cold"]] == ["c2", "c3"]


def test_prune_cold_trims_to_the_newest_and_leaves_short_lists_alone():
    from memory.long_term_merge import COLD_PRUNE_ABOVE, COLD_PRUNE_TO, prune_cold
    items = [{"kind": "fact", "subject": "master", "id": f"f{index}", "status": "retracted", "archivedAt": index}
             for index in range(COLD_PRUNE_ABOVE + 1)]
    assert prune_cold(items[:COLD_PRUNE_ABOVE]) is not None
    short = items[:COLD_PRUNE_ABOVE]
    assert prune_cold(short) is short
    assert prune_cold(items) == items[-COLD_PRUNE_TO:]


# ── review round 1 ─────────────────────────────────────────────────────


def test_stub_drops_earlier_wordings_carried_in_sync_metadata():
    row = _fact("p1", "now", _sync_metadata={"versionHistory": [{"text": "earlier wording"}], "sourceSyncId": "x"})
    stub = cold_store.stub(row)
    assert "earlier wording" not in json.dumps(stub, ensure_ascii=False)
    assert stub["_sync_metadata"] == {"sourceSyncId": "x"}
    assert row["_sync_metadata"]["versionHistory"]  # the stored row is untouched


@pytest.mark.asyncio
async def test_a_legacy_archived_persona_row_without_subject_still_wins(tmp_path):
    from memory.archive_shards import append_to_shard_sync
    _, fs, pm, *_ = _install(tmp_path)
    directory = fs._character_dir(NAME)
    entry = {"id": "pe", "text": "archived trait", "source": "reflection"}
    atomic_write_json(os.path.join(directory, "persona.json"),
                      {"relationship": {"facts": [entry, {"id": "live", "text": "live trait"}]}})
    append_to_shard_sync(cold_store.cold_dir(directory, "persona"), [dict(entry)])  # no entity_key
    persona = await pm.aensure_persona(NAME)
    assert [row["id"] for row in persona["relationship"]["facts"]] == ["live"]


@pytest.mark.asyncio
@pytest.mark.parametrize("listed,withdrawn", [("retracted", True), ("superseded", False)])
async def test_source_counting_reads_cold_facts_as_the_shared_list_states_them(tmp_path, listed, withdrawn):
    from memory.evidence import is_withdrawn_fact
    _, fs, *_ = _install(tmp_path)
    directory = fs._character_dir(NAME)
    held = "retracted" if listed == "superseded" else "superseded"
    cold_store.append(directory, [_fact("c1", "cold", arbitration_archived_at="2026-07-01T00:00:00",
                                       **({"superseded_by": "keep"} if held == "superseded" else {}))])
    item = {"kind": "fact", "subject": "master", "id": "c1", "status": listed, "archivedAt": 1,
            **({"supersededById": "keep"} if listed == "superseded" else {})}
    atomic_write_json(os.path.join(directory, "long_term_portable.json"), {"cold": [item]})
    [row] = [row for row in await fs.aload_facts_full(NAME, include_cold_stubs=True) if row["id"] == "c1"]
    assert is_withdrawn_fact(row) is withdrawn
    # The shard itself keeps what this device wrote.
    assert bool(cold_store.read_full(directory)[0].get("superseded_by")) is (held == "superseded")


def test_a_retried_group_restore_adds_each_row_once(tmp_path, monkeypatch):
    _, fs, *_ = _install(tmp_path)
    directory = fs._character_dir(NAME)
    row = _fact("g1", "quiet group", subject_archived_at="2026-07-01T00:00:00", **GROUP.as_entry_fields())
    cold_store.append(directory, [row])
    fs.load_facts(NAME)
    real = cold_store.rewrite_rows
    calls = []

    def flaky(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise OSError("cold shard busy")
        return real(*args, **kwargs)
    monkeypatch.setattr(cold_store, "rewrite_rows", flaky)
    with pytest.raises(OSError):
        fs._restore_subject_facts(NAME, GROUP, "2026-08-01T00:00:00")
    fs._restore_subject_facts(NAME, GROUP, "2026-08-01T00:00:00")
    assert [fact["id"] for fact in fs._facts[NAME]] == ["g1"]
    assert [fact["id"] for fact in json.load(open(fs._facts_path(NAME), encoding="utf-8"))] == ["g1"]
