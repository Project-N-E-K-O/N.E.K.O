# Copyright 2025-2026 Project N.E.K.O. Team
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""A startup cloudsave import that removes local characters records their visit retirement (OD-13, PR-09b).

Real snapshot export / import between two config managers rooted in
``tmp_path`` (never the real runtime root).
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from main_logic.cloudsave_visit_retire import removed_characters_recorder
from main_logic.visit import char_lifecycle as lc
from tests.unit.test_cloudsave_autocloud import _make_config_manager, _write_runtime_state
from tests.unit.visit_memory_test_helpers import seed_roster
from utils.cloudsave_autocloud import CloudSaveManager
from utils.cloudsave_runtime import (
    bootstrap_local_cloudsave_environment,
    export_cloudsave_character_unit,
    export_local_cloudsave_snapshot,
    import_local_cloudsave_snapshot,
)
from utils.cloudsave_runtime import operations
from utils.config_manager import get_character_uid


def _names_on_disk(cm) -> set[str]:
    path = Path(cm.get_runtime_config_path("characters.json"))
    return set(json.loads(path.read_text(encoding="utf-8"))["猫娘"])


@pytest.fixture
def pair(tmp_path):
    """``(source, target)``: the target holds one local character (with a uid), the source a cloud one."""
    source_cm = _make_config_manager(tmp_path / "source")
    target_cm = _make_config_manager(tmp_path / "target")
    bootstrap_local_cloudsave_environment(source_cm)
    bootstrap_local_cloudsave_environment(target_cm)
    _write_runtime_state(source_cm, character_name="云端角色")
    _write_runtime_state(target_cm, character_name="本地角色")
    target_cm.backfill_character_uids()
    return source_cm, target_cm


def _stage_full_snapshot(source_cm, target_cm) -> None:
    export_local_cloudsave_snapshot(source_cm)
    shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)


def _names_loader(names, uid_of):
    async def load():
        return set(names), dict(uid_of)

    return load


def _local_uid(cm) -> str:
    uid = get_character_uid(cm.load_characters()["猫娘"]["本地角色"])
    assert uid
    return uid


def test_full_snapshot_reports_the_removed_character_before_committing(pair):
    source_cm, target_cm = pair
    uid = _local_uid(target_cm)
    _stage_full_snapshot(source_cm, target_cm)
    seen = []

    def record(removed, kept_local_names, renamed, fresh_names, replaced):
        # Persist before deletion: characters.json still contains the local character at this point
        seen.append((removed, kept_local_names, renamed, fresh_names, _names_on_disk(target_cm)))

    result = CloudSaveManager(target_cm).import_if_needed(reason="unit", force=True, on_characters_removed=record)
    assert result["action"] == "imported"
    # The cloud character came from the newly downloaded snapshot, so it is not a "preserved local character"
    assert seen == [([{"name": "本地角色", "character_uid": uid}], frozenset(), [], {"云端角色": None},
                     {"本地角色"})]
    assert _names_on_disk(target_cm) == {"云端角色"}


def test_import_without_removals_reports_only_the_new_names(pair):
    source_cm, target_cm = pair
    # A single-character export produces only a snapshot with merged semantics: preserve local characters and treat cloud characters as newly imported
    export_cloudsave_character_unit(source_cm, "云端角色")
    shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)
    seen = []
    import_local_cloudsave_snapshot(
        target_cm,
        on_characters_removed=lambda removed, _kept, renamed, fresh_names, replaced: seen.append(
            (removed, renamed, fresh_names, replaced)),
    )
    assert seen == [([], [], {"云端角色": None}, [])]
    assert {"本地角色", "云端角色"} <= _names_on_disk(target_cm)


def test_a_recorder_without_any_visit_roster_skips_new_names_only(tmp_path):
    record = removed_characters_recorder(SimpleNamespace(config_dir=str(tmp_path)))
    # Only new characters were added and there is no peer roster: write nothing and do not load the peer module
    assert record([], frozenset(), [], frozenset({"云端角色"})) == 0
    assert not (tmp_path / "visit_peers.json").exists()


def test_a_failing_callback_does_not_stop_the_import(pair):
    source_cm, target_cm = pair
    _stage_full_snapshot(source_cm, target_cm)

    def broken(_removed, _kept_local_names, _renamed, **_kw):
        raise OSError("visit_peers.json locked")

    import_local_cloudsave_snapshot(target_cm, on_characters_removed=broken)
    assert _names_on_disk(target_cm) == {"云端角色"}


def test_import_without_callback_is_unchanged(pair):
    source_cm, target_cm = pair
    _stage_full_snapshot(source_cm, target_cm)
    import_local_cloudsave_snapshot(target_cm)
    assert _names_on_disk(target_cm) == {"云端角色"}
    assert not (Path(target_cm.config_dir) / "visit_peers.json").exists()


def test_recorder_writes_retire_items_only_with_visit_data(pair):
    source_cm, target_cm = pair
    _stage_full_snapshot(source_cm, target_cm)
    # No peer data: create no peer files (mutation: failing to check for peer data must fail)
    import_local_cloudsave_snapshot(target_cm, on_characters_removed=removed_characters_recorder(target_cm))
    assert not (Path(target_cm.config_dir) / "visit_peers.json").exists()


async def test_recorder_writes_retire_items_for_startup_recovery(pair):
    source_cm, target_cm = pair
    uid = _local_uid(target_cm)
    config_dir = Path(target_cm.config_dir)
    await seed_roster(config_dir, own_char="本地角色")
    _stage_full_snapshot(source_cm, target_cm)
    import_local_cloudsave_snapshot(target_cm, on_characters_removed=removed_characters_recorder(target_cm))
    peers = json.loads((config_dir / "visit_peers.json").read_text(encoding="utf-8"))
    # Mutation: removing the callback call must fail
    assert peers["pending_retire"] == [lc.retire_item("本地角色", uid)]


def test_recorder_needs_a_real_config_dir():
    assert removed_characters_recorder(SimpleNamespace()) is None
    assert removed_characters_recorder(SimpleNamespace(config_dir="")) is None


def test_removed_characters_come_from_the_runtime_file_only(pair, tmp_path):
    _source_cm, target_cm = pair
    uid = _local_uid(target_cm)
    nothing = ([], frozenset(), [])
    assert operations._removed_local_characters(target_cm, {}) == (
        [{"name": "本地角色", "character_uid": uid}], frozenset(), [],
    )
    # Same uid has a different name in the snapshot: this is a rename, not a deletion; report it to the callback to migrate peer data
    renamed = {"新名字": {"_reserved": {"character_uid": uid}}}
    assert operations._removed_local_characters(target_cm, renamed) == (
        [], frozenset(), [{"old": "本地角色", "new": "新名字", "uid": uid}],
    )
    # The snapshot gives the old name to a character that was not previously local: entries under the old name still belong to this character,
    # so report the rename and the handed-over name (mutation: omitting the rename or new name must fail)
    reused = {"本地角色": {"_reserved": {"character_uid": "d" * 32}}, "新名字": {"_reserved": {"character_uid": uid}}}
    assert operations._removed_local_characters(target_cm, reused) == (
        [], frozenset({"本地角色"}), [{"old": "本地角色", "new": "新名字", "uid": uid}],
    )
    # Report the new name together with the owner from before import: this is the character being renamed
    assert operations._fresh_character_names(target_cm, reused) == {"本地角色": uid}
    assert operations._removed_local_characters(target_cm, {"本地角色": {}}) == ([], frozenset({"本地角色"}), [])
    # File cannot be read: report nothing (mutation: falling back to load_characters' default character must fail)
    path = Path(target_cm.get_runtime_config_path("characters.json"))
    path.write_text("{broken", encoding="utf-8")
    assert operations._removed_local_characters(target_cm, {}) == nothing
    # Valid JSON but nested too deeply (json.load raises RecursionError): treat as unreadable too; import must not abort
    path.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    assert operations._removed_local_characters(target_cm, {}) == nothing


async def test_a_removed_character_with_a_pending_rename_retires_both_names(pair):
    from main_logic.visit.subjects import set_roster_marker
    from tests.unit.visit_memory_test_helpers import PEER_X

    source_cm, target_cm = pair
    uid = _local_uid(target_cm)
    config_dir = Path(target_cm.config_dir)
    # The local character is the result of a previous "old name -> local character" rename, and migration is incomplete: the peer remains under the old name
    roster = await seed_roster(config_dir, own_char="旧名")
    await set_roster_marker(config_dir, "pending_rename", {"old": "旧名", "new": "本地角色", "uid": uid})
    _stage_full_snapshot(source_cm, target_cm)
    import_local_cloudsave_snapshot(target_cm, on_characters_removed=removed_characters_recorder(target_cm))
    peers = json.loads((config_dir / "visit_peers.json").read_text(encoding="utf-8"))
    # Mutation: recording only the current name must fail (startup reconciliation treats it as "deleted" and clears the rename marker, leaving no one to retire old-name peers)
    assert peers["pending_retire"] == [lc.retire_item("本地角色", uid), lc.retire_item("旧名", uid)]
    names, uid_of = {"云端角色"}, {}
    assert await lc.reconcile_rename(config_dir, names, uid_of) == frozenset()
    for item in peers["pending_retire"]:
        assert await lc.settle_retire(config_dir, item, names=names, uid_of=uid_of, retire_persona=None)
    assert await roster.get_char_entry(PEER_X, "旧名") is None


async def test_a_snapshot_character_taking_the_old_name_does_not_inherit_its_entries(tmp_path):
    from main_logic.visit.subjects import set_roster_marker
    from tests.unit.visit_memory_test_helpers import PEER_X

    source_cm = _make_config_manager(tmp_path / "source")
    target_cm = _make_config_manager(tmp_path / "target")
    bootstrap_local_cloudsave_environment(source_cm)
    bootstrap_local_cloudsave_environment(target_cm)
    # Snapshot includes a character also named "old name"; the local "local character" is the result of an "old name -> local character" rename and migration is incomplete
    _write_runtime_state(source_cm, character_name="旧名")
    _write_runtime_state(target_cm, character_name="本地角色")
    target_cm.backfill_character_uids()
    uid = _local_uid(target_cm)
    config_dir = Path(target_cm.config_dir)
    roster = await seed_roster(config_dir, own_char="旧名")
    await set_roster_marker(config_dir, "pending_rename", {"old": "旧名", "new": "本地角色", "uid": uid})
    _stage_full_snapshot(source_cm, target_cm)
    import_local_cloudsave_snapshot(target_cm, on_characters_removed=removed_characters_recorder(target_cm))
    peers = json.loads((config_dir / "visit_peers.json").read_text(encoding="utf-8"))
    # The old name belonged to no local character before import: its entries can only belong to a deleted character, so retire it normally
    # (mutation: skipping it because that name appears in the snapshot must fail)
    assert peers["pending_retire"] == [lc.retire_item("本地角色", uid), lc.retire_item("旧名", uid)]
    names = {"旧名"}
    uid_of = {"旧名": get_character_uid(target_cm.load_characters()["猫娘"]["旧名"]) or "d" * 32}
    assert uid_of["旧名"] != uid
    for item in peers["pending_retire"]:
        assert await lc.settle_retire(config_dir, item, names=names, uid_of=uid_of, retire_persona=None)
    assert await roster.get_char_entry(PEER_X, "旧名") is None


def test_the_other_rename_name_is_not_retired_when_another_local_character_holds_it(tmp_path):
    import asyncio

    from main_logic.visit.subjects import set_roster_marker

    uid = "c" * 32
    asyncio.run(set_roster_marker(tmp_path, "pending_rename", {"old": "Old", "new": "New", "uid": uid}))
    removed = [{"name": "New", "character_uid": uid}]
    # Another local character already has the name Old and is preserved: entries for both cannot be distinguished, and retiring by name would delete its entries
    # (mutation: failing to consider preserved local names must fail)
    assert lc.record_removed_characters_sync(tmp_path, removed, frozenset({"Old"})) == 1
    peers = json.loads((tmp_path / "visit_peers.json").read_text(encoding="utf-8"))
    assert peers["pending_retire"] == [lc.retire_item("New", uid)]


# ── Same uid has a different name in the snapshot (renamed on another device) ────────────


def _stage_renamed_snapshot(tmp_path, target_name="本地角色", new_name="新名字"):
    """``(target_cm, uid)``: the target holds ``target_name``; the staged full snapshot holds it as ``new_name``."""
    source_cm = _make_config_manager(tmp_path / "source")
    target_cm = _make_config_manager(tmp_path / "target")
    bootstrap_local_cloudsave_environment(source_cm)
    bootstrap_local_cloudsave_environment(target_cm)
    _write_runtime_state(target_cm, character_name=target_name)
    target_cm.backfill_character_uids()
    uid = _local_uid(target_cm) if target_name == "本地角色" else get_character_uid(
        target_cm.load_characters()["猫娘"][target_name])
    _write_runtime_state(source_cm, character_name=new_name)
    characters = source_cm.load_characters()
    characters["猫娘"][new_name].setdefault("_reserved", {})["character_uid"] = uid
    source_cm.save_characters(characters, bypass_write_fence=True)
    _stage_full_snapshot(source_cm, target_cm)
    return target_cm, uid


async def test_a_snapshot_rename_moves_the_visit_data_to_the_new_name(tmp_path):
    from main_logic.visit.spool import VisitSpool
    from tests.unit.visit_memory_test_helpers import PEER_X, ln, make_visit, vid

    target_cm, uid = _stage_renamed_snapshot(tmp_path)
    config_dir = Path(target_cm.config_dir)
    roster = await seed_roster(config_dir, own_char="本地角色")
    await make_visit(config_dir, vid(1), [ln(1)], own_char="本地角色", own_char_uid=uid)
    import_local_cloudsave_snapshot(target_cm, on_characters_removed=removed_characters_recorder(target_cm))
    peers = json.loads((config_dir / "visit_peers.json").read_text(encoding="utf-8"))
    # Mutation: failing to record the rename must fail (nobody migrates peers under the old name, and a future same-name character would inherit them)
    assert peers["pending_rename"] == {"old": "本地角色", "new": "新名字", "uid": uid, "source": "cloudsave"}
    assert "pending_retire" not in peers
    # Startup reconciliation migrates forward by uid
    assert await lc.reconcile_rename(config_dir, {"新名字"}, {"新名字": uid}) == frozenset()
    assert await roster.get_char_entry(PEER_X, "本地角色") is None
    assert await roster.get_char_entry(PEER_X, "新名字") is not None
    assert (await VisitSpool(config_dir, vid(1)).read_state())["own_char"] == "新名字"


async def test_a_snapshot_rename_that_rolled_back_leaves_the_data_under_the_old_name(tmp_path):
    from tests.unit.visit_memory_test_helpers import PEER_X

    target_cm, uid = _stage_renamed_snapshot(tmp_path)
    config_dir = Path(target_cm.config_dir)
    roster = await seed_roster(config_dir, own_char="本地角色")
    lc.record_renamed_characters_sync(config_dir, [{"old": "本地角色", "new": "新名字", "uid": uid}])
    # Import rollback: this uid is still named local character
    assert await lc.reconcile_rename(config_dir, {"本地角色"}, {"本地角色": uid}) == frozenset()
    assert await roster.get_char_entry(PEER_X, "本地角色") is not None
    assert await roster.get_char_entry(PEER_X, "新名字") is None
    assert "pending_rename" not in json.loads((config_dir / "visit_peers.json").read_text(encoding="utf-8"))


def test_a_snapshot_rename_without_visit_data_writes_nothing(tmp_path):
    target_cm, _uid = _stage_renamed_snapshot(tmp_path)
    import_local_cloudsave_snapshot(target_cm, on_characters_removed=removed_characters_recorder(target_cm))
    assert not (Path(target_cm.config_dir) / "visit_peers.json").exists()


async def test_a_snapshot_rename_queues_behind_an_earlier_pending_rename(tmp_path):
    from main_logic.visit.subjects import set_roster_marker

    config_dir = tmp_path
    earlier = {"old": "X", "new": "Y", "uid": "e" * 32}
    await set_roster_marker(config_dir, "pending_rename", earlier)
    item = {"old": "A", "new": "C", "uid": "c" * 32}
    # There is only one marker slot: do not overwrite an un-reconciled rename; queue it instead (mutation: overwriting must fail; mutation: logging without queuing must fail)
    assert lc.record_renamed_characters_sync(config_dir, [item]) == 1
    peers = json.loads((config_dir / "visit_peers.json").read_text(encoding="utf-8"))
    assert peers["pending_rename"] == earlier
    assert peers["pending_rename_queue"] == [{**item, "source": "cloudsave"}]
    assert await lc.name_marker_state(config_dir, "A") == lc.NAME_RENAMING
    assert await lc.name_marker_state(config_dir, "C") == lc.NAME_RENAMING


async def test_a_snapshot_renaming_two_characters_migrates_both(tmp_path):
    from tests.unit.visit_memory_test_helpers import PEER_X, PEER_Y, TAG_Y

    config_dir = tmp_path
    uid_a, uid_b = "a" * 32, "b" * 32
    roster = await seed_roster(config_dir, own_char="A")
    await seed_roster(config_dir, peer_uid=PEER_Y, tag=TAG_Y, own_char="B")
    renamed = [{"old": "A", "new": "A2", "uid": uid_a}, {"old": "B", "new": "B2", "uid": uid_b}]
    assert lc.record_renamed_characters_sync(config_dir, renamed) == 2
    unsettled = await lc.reconcile_renames(config_dir, {"A2", "B2"}, {"A2": uid_a, "B2": uid_b})
    # Mutation: failing to process the queue in order must fail (the second character's entries remain under its old name)
    assert unsettled == frozenset()
    assert await roster.get_char_entry(PEER_X, "A2") is not None
    assert await roster.get_char_entry(PEER_Y, "B2") is not None
    assert await roster.get_char_entry(PEER_Y, "B") is None
    peers = json.loads((config_dir / "visit_peers.json").read_text(encoding="utf-8"))
    assert "pending_rename" not in peers and "pending_rename_queue" not in peers


async def test_a_removed_character_with_a_queued_rename_retires_its_other_name(tmp_path):
    from main_logic.visit.subjects import set_roster_marker

    config_dir = tmp_path
    uid = "c" * 32
    await seed_roster(config_dir, own_char="A")
    await set_roster_marker(config_dir, "pending_rename", {"old": "X", "new": "Y", "uid": "e" * 32})
    lc.record_renamed_characters_sync(config_dir, [{"old": "A", "new": "C", "uid": uid}])
    # A queued rename also counts as another name for this character (mutation: checking only the current slot must fail)
    lc.record_removed_characters_sync(config_dir, [{"name": "C", "character_uid": uid}])
    peers = json.loads((config_dir / "visit_peers.json").read_text(encoding="utf-8"))
    assert {item["name"] for item in peers["pending_retire"]} == {"C", "A"}


async def test_a_snapshot_rename_of_the_same_character_chains_onto_its_pending_rename(tmp_path, caplog):
    from main_logic.visit.subjects import set_roster_marker
    from tests.unit.visit_memory_test_helpers import PEER_X

    config_dir = tmp_path
    uid = "c" * 32
    roster = await seed_roster(config_dir, own_char="A")
    earlier = {"old": "A", "new": "B", "uid": uid}
    await set_roster_marker(config_dir, "pending_rename", earlier)
    # Character A -> B has not been fully reconciled, then the snapshot renames it to C: do not overwrite the marker; reconciliation sees the third name and migrates all at once
    with caplog.at_level("INFO", logger=lc.logger.name):
        assert lc.record_renamed_characters_sync(config_dir, [{"old": "B", "new": "C", "uid": uid}]) == 1
    assert not [r for r in caplog.records if r.levelname == "WARNING"]
    # Record new name C in the marker; it also counts as occupied before reconciliation (mutation: failing to record it must fail, since overwrite download of C would replace the uid)
    peers = json.loads((config_dir / "visit_peers.json").read_text(encoding="utf-8"))
    assert peers["pending_rename"] == {**earlier, "chained": ["C"]}
    assert await lc.name_marker_state(config_dir, "C") == lc.NAME_RENAMING
    assert await lc.reconcile_rename(config_dir, {"C"}, {"C": uid}) == frozenset()
    assert await lc.name_marker_state(config_dir, "C") == lc.NAME_FREE
    assert await roster.get_char_entry(PEER_X, "A") is None
    assert await roster.get_char_entry(PEER_X, "C") is not None


async def test_a_chain_extending_a_pending_rename_keeps_its_handed_over_name(tmp_path):
    from main_logic.visit.subjects import set_roster_marker

    uid = "c" * 32
    await seed_roster(tmp_path, own_char="A")
    earlier = {"old": "X", "new": "A", "uid": uid}
    await set_roster_marker(tmp_path, "pending_rename", earlier)
    # Chained rename A -> C (old name A is then assigned to another local character, handed_over) lands on this character's unreconciled
    # marker: add chained and handed_over together (mutation: dropping handed_over must fail)
    lc.record_renamed_characters_sync(tmp_path, [{"old": "A", "new": "C", "uid": uid, "handed_over": ["A"]}])
    peers = json.loads((tmp_path / "visit_peers.json").read_text(encoding="utf-8"))
    assert peers["pending_rename"] == {**earlier, "chained": ["C"], "handed_over": ["A"]}


def test_a_failing_retire_record_still_records_the_renames(tmp_path, monkeypatch):
    record = removed_characters_recorder(SimpleNamespace(config_dir=str(tmp_path)))
    renamed = [{"old": "A", "new": "C", "uid": "c" * 32}]
    calls = []

    def broken(*_a, **_k):
        raise OSError("visit_peers.json locked")

    monkeypatch.setattr(lc, "record_removed_characters_sync", broken)
    monkeypatch.setattr(lc, "record_renamed_characters_sync", lambda _d, items: calls.append(items) or 1)
    # The two steps are independent (mutation: failing the rename record after step one fails must fail); let the importer log failures as usual
    with pytest.raises(OSError):
        record([{"name": "B", "character_uid": "b" * 32}], frozenset(), renamed)
    assert calls == [renamed]


def test_a_snapshot_rename_onto_the_name_of_a_removed_local_character_retires_it_first(pair):
    _source_cm, target_cm = pair
    uid = _local_uid(target_cm)
    other = "d" * 32
    characters = target_cm.load_characters()
    characters["猫娘"]["新名字"] = {"_reserved": {"character_uid": other}}
    target_cm.save_characters(characters, bypass_write_fence=True)
    # Snapshot contains only the local character with the uid now under "new name"; the character previously named "new name" is gone. Its name is taken by
    # another local character, not a same-name uid replacement: record its retirement (mutation: treating it as preserved must fail) and report the rename; startup reconciliation
    # retires it first, then migrates it (mutation: continuing to skip the rename as before must fail)
    kept = {"新名字": {"_reserved": {"character_uid": uid}}}
    removed, _kept_local, renamed = operations._removed_local_characters(target_cm, kept)
    assert removed == [{"name": "新名字", "character_uid": other}]
    assert renamed == [{"old": "本地角色", "new": "新名字", "uid": uid}]


async def test_a_same_name_uid_replacement_retires_the_other_name_of_its_pending_rename(pair):
    from main_logic.visit.subjects import set_roster_marker
    from tests.unit.visit_memory_test_helpers import PEER_X, PEER_Y, TAG_Y

    _source_cm, target_cm = pair
    uid = _local_uid(target_cm)
    config_dir = Path(target_cm.config_dir)
    # The local character (uid U) has an unreconciled "old name -> local character" rename; the snapshot's "local character" has a new uid
    kept = {"本地角色": {"_reserved": {"character_uid": "d" * 32}}}
    assert operations._removed_local_characters(target_cm, kept)[0] == []      # Same name with a new uid: known behavior, not considered deleted
    replaced = operations._replaced_local_characters(target_cm, kept)
    assert replaced == [{"name": "本地角色", "character_uid": uid}]
    roster = await seed_roster(config_dir, own_char="旧名")
    await seed_roster(config_dir, peer_uid=PEER_Y, tag=TAG_Y, own_char="本地角色")
    await set_roster_marker(config_dir, "pending_rename", {"old": "旧名", "new": "本地角色", "uid": uid})
    record = removed_characters_recorder(target_cm)
    record([], frozenset({"本地角色"}), [], frozenset({"本地角色"}), replaced=replaced)
    peers = json.loads((config_dir / "visit_peers.json").read_text(encoding="utf-8"))
    # Old uid is gone: nobody owns its entries under "old name," so retire it (mutation: failing to report or process replaced must fail);
    # Keep entries under "local character" for the same-name character per known behavior; do not retire them
    assert peers["pending_retire"] == [lc.retire_item("旧名", uid)]
    assert await lc.replay_retires(config_dir, _names_loader({"本地角色"}, {"本地角色": "d" * 32}),
                                   retire_persona=None)
    assert await roster.get_char_entry(PEER_X, "旧名") is None
    assert await roster.get_char_entry(PEER_Y, "本地角色") is not None


async def test_handed_over_skips_names_another_local_character_held_before_the_import(tmp_path):
    from main_logic.visit.subjects import set_roster_marker

    marker = {"old": "A", "new": "B", "uid": "c" * 32}
    await set_roster_marker(tmp_path, "pending_rename", marker)
    # Before import, A belonged to another local character V (so the marker was already ambiguous); the snapshot renames V to new character W:
    # some entries under A belong to V, so they cannot all be migrated as part of this rename (mutation: ignoring the pre-import owner must fail)
    assert lc.record_handed_over_names_sync(tmp_path, {"A": "v" * 32}, []) == 0
    assert json.loads((tmp_path / "visit_peers.json").read_text(encoding="utf-8"))["pending_rename"] == marker
    # If nobody used the name before import, or it belonged to this character, record it normally
    assert lc.record_handed_over_names_sync(tmp_path, {"A": "c" * 32}, []) == 1


async def test_a_removed_uid_less_character_does_not_hide_unrelated_uid_less_markers(tmp_path):
    from main_logic.visit.subjects import set_roster_marker

    # Startup import may run before uid backfill, so a deleted character may have no uid. It must not treat every rename marker without a uid as its own
    # (mutation: comparing uid to None must fail)
    await set_roster_marker(tmp_path, "pending_rename", {"old": "A", "new": "B"})
    assert lc.record_handed_over_names_sync(
        tmp_path, {"A": None}, [{"name": "Z", "character_uid": None}]) == 1


def test_two_local_characters_trading_names_are_not_migrated(pair):
    _source_cm, target_cm = pair
    uid = _local_uid(target_cm)
    other = "d" * 32
    characters = target_cm.load_characters()
    characters["猫娘"]["新名字"] = {"_reserved": {"character_uid": other}}
    target_cm.save_characters(characters, bypass_write_fence=True)
    # Two local characters swap names: both sets of entries are stored by name and cannot be distinguished; report no rename and retire neither
    kept = {"新名字": {"_reserved": {"character_uid": uid}}, "本地角色": {"_reserved": {"character_uid": other}}}
    removed, _kept_local, renamed = operations._removed_local_characters(target_cm, kept)
    assert removed == [] and renamed == []


async def test_an_acyclic_chain_of_local_renames_migrates_in_dependency_order(pair):
    from tests.unit.visit_memory_test_helpers import PEER_X, PEER_Y, TAG_Y

    _source_cm, target_cm = pair
    uid, other = _local_uid(target_cm), "d" * 32
    config_dir = Path(target_cm.config_dir)
    characters = target_cm.load_characters()
    characters["猫娘"]["新名字"] = {"_reserved": {"character_uid": other}}
    target_cm.save_characters(characters, bypass_write_fence=True)
    # On another device: first rename "new name" (other) to "third name", then rename "local character" (uid) to "new name".
    # This is not a cycle: migrate other first, then uid, so both sets of entries remain attributable (mutation: treating it as a swap / failing to order must fail)
    kept = {"新名字": {"_reserved": {"character_uid": uid}}, "第三个名字": {"_reserved": {"character_uid": other}}}
    removed, _kept_local, renamed = operations._removed_local_characters(target_cm, kept)
    assert removed == []
    assert renamed == [{"old": "新名字", "new": "第三个名字", "uid": other, "handed_over": ["新名字"]},
                       {"old": "本地角色", "new": "新名字", "uid": uid}]
    roster = await seed_roster(config_dir, own_char="本地角色")
    await seed_roster(config_dir, peer_uid=PEER_Y, tag=TAG_Y, own_char="新名字")
    lc.record_renamed_characters_sync(config_dir, renamed)
    unsettled = await lc.reconcile_renames(config_dir, {"新名字", "第三个名字"}, {"新名字": uid, "第三个名字": other})
    assert unsettled == frozenset()
    assert await roster.get_char_entry(PEER_Y, "第三个名字") is not None      # other’s entries follow it
    assert await roster.get_char_entry(PEER_X, "新名字") is not None          # uid’s entries follow it
    assert await roster.get_char_entry(PEER_Y, "新名字") is None              # uid did not inherit other’s entries
    assert await roster.get_char_entry(PEER_X, "本地角色") is None


async def test_a_rename_whose_old_name_goes_to_a_new_character_still_migrates(tmp_path):
    from tests.unit.visit_memory_test_helpers import PEER_X

    config_dir = tmp_path
    uid, newcomer = "c" * 32, "d" * 32
    roster = await seed_roster(config_dir, own_char="A")
    # Snapshot renames this character to C and gives A to a character that was not previously local: entries under A still belong to this character at this point
    record = removed_characters_recorder(SimpleNamespace(config_dir=str(config_dir)))
    record([], frozenset(), [{"old": "A", "new": "C", "uid": uid}], frozenset({"A"}))
    peers = json.loads((config_dir / "visit_peers.json").read_text(encoding="utf-8"))
    assert peers["pending_rename"] == {"old": "A", "new": "C", "uid": uid, "source": "cloudsave",
                                       "handed_over": ["A"]}
    unsettled = await lc.reconcile_renames(config_dir, {"A", "C"}, {"A": newcomer, "C": uid})
    # Mutation: failing to record handed_over / reconciliation ignoring handed_over must fail (new character A would inherit the old character's peers)
    assert unsettled == frozenset()
    assert await roster.get_char_entry(PEER_X, "A") is None
    assert await roster.get_char_entry(PEER_X, "C") is not None


async def test_a_pending_rename_whose_name_goes_to_a_new_character_is_marked_handed_over(tmp_path):
    from main_logic.visit.subjects import set_roster_marker
    from tests.unit.visit_memory_test_helpers import PEER_X, PEER_Y, TAG_Y

    config_dir = tmp_path
    uid, newcomer = "c" * 32, "d" * 32
    roster = await seed_roster(config_dir, own_char="A")
    await seed_roster(config_dir, peer_uid=PEER_Y, tag=TAG_Y, own_char="B")
    # A -> B was partially migrated; the snapshot renames it to C and gives B to a new character
    await set_roster_marker(config_dir, "pending_rename", {"old": "A", "new": "B", "uid": uid})
    record = removed_characters_recorder(SimpleNamespace(config_dir=str(config_dir)))
    record([], frozenset(), [{"old": "B", "new": "C", "uid": uid}], frozenset({"B"}))
    peers = json.loads((config_dir / "visit_peers.json").read_text(encoding="utf-8"))
    assert peers["pending_rename"]["handed_over"] == ["B"]
    unsettled = await lc.reconcile_renames(config_dir, {"B", "C"}, {"B": newcomer, "C": uid})
    # Mutation: failing to add handed_over to the existing marker must fail (partially migrated entries under B would be left for the new character B)
    assert unsettled == frozenset()
    assert await roster.get_char_entry(PEER_X, "C") is not None
    assert await roster.get_char_entry(PEER_Y, "C") is not None
    assert await roster.get_char_entry(PEER_Y, "B") is None
