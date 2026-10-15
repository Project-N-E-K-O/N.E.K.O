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
        # 先于删除落盘：这时 characters.json 里还是本地角色
        seen.append((removed, kept_local_names, renamed, fresh_names, _names_on_disk(target_cm)))

    result = CloudSaveManager(target_cm).import_if_needed(reason="unit", force=True, on_characters_removed=record)
    assert result["action"] == "imported"
    # 云端角色是快照新带来的，不算「保留下来的本地角色」
    assert seen == [([{"name": "本地角色", "character_uid": uid}], frozenset(), [], {"云端角色": None},
                     {"本地角色"})]
    assert _names_on_disk(target_cm) == {"云端角色"}


def test_import_without_removals_reports_only_the_new_names(pair):
    source_cm, target_cm = pair
    # 单角色导出只产出合并语义的快照：本地角色保留，云端角色是新带来的
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
    # 只带来了新角色、没有串门名册：什么都不写、不加载串门模块
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
    # 没有串门数据：不建任何串门文件（变异：不看有没有串门数据必红）
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
    # 变异：去掉回调调用必红
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
    # 同一个 uid 在快照里换了名字：是改名不是删除，报给回调去迁移串门数据
    renamed = {"新名字": {"_reserved": {"character_uid": uid}}}
    assert operations._removed_local_characters(target_cm, renamed) == (
        [], frozenset(), [{"old": "本地角色", "new": "新名字", "uid": uid}],
    )
    # 旧名给了快照里一个本机原来没有的角色：旧名下的条目都还是这个角色的，照报改名，
    # 并报出交出去的名字（变异：不报改名 / 不报新名字必红）
    reused = {"本地角色": {"_reserved": {"character_uid": "d" * 32}}, "新名字": {"_reserved": {"character_uid": uid}}}
    assert operations._removed_local_characters(target_cm, reused) == (
        [], frozenset({"本地角色"}), [{"old": "本地角色", "new": "新名字", "uid": uid}],
    )
    # 新名字连同导入前的主人一起报出：这里是改名的这个角色自己
    assert operations._fresh_character_names(target_cm, reused) == {"本地角色": uid}
    assert operations._removed_local_characters(target_cm, {"本地角色": {}}) == ([], frozenset({"本地角色"}), [])
    # 文件读不出：什么都不报（变异：退回 load_characters 的默认角色必红）
    path = Path(target_cm.get_runtime_config_path("characters.json"))
    path.write_text("{broken", encoding="utf-8")
    assert operations._removed_local_characters(target_cm, {}) == nothing
    # 合法 JSON 但嵌套过深（json.load 抛 RecursionError）：同样当读不出，不能让导入中止
    path.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    assert operations._removed_local_characters(target_cm, {}) == nothing


async def test_a_removed_character_with_a_pending_rename_retires_both_names(pair):
    from main_logic.visit.subjects import set_roster_marker
    from tests.unit.visit_memory_test_helpers import PEER_X

    source_cm, target_cm = pair
    uid = _local_uid(target_cm)
    config_dir = Path(target_cm.config_dir)
    # 本地角色是上一次「旧名 -> 本地角色」改名的结果，迁移没做完：对端还在旧名下
    roster = await seed_roster(config_dir, own_char="旧名")
    await set_roster_marker(config_dir, "pending_rename", {"old": "旧名", "new": "本地角色", "uid": uid})
    _stage_full_snapshot(source_cm, target_cm)
    import_local_cloudsave_snapshot(target_cm, on_characters_removed=removed_characters_recorder(target_cm))
    peers = json.loads((config_dir / "visit_peers.json").read_text(encoding="utf-8"))
    # 变异：只记当前名必红（启动对账按「已删除」清掉改名标记后，旧名下的对端没人退役）
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
    # 快照带来一个也叫「旧名」的角色；本机的「本地角色」是「旧名 -> 本地角色」改名的结果、迁移没做完
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
    # 旧名在导入前不属于本机任何角色：它下面的条目只可能是被删角色的，照常退役
    # （变异：快照里有这个名字就跳过必红）
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
    # 本机另一个角色已叫 Old 并保留下来：两边条目分不开，按名字退役会删掉它的条目
    # （变异：不看保留下来的本地名字必红）
    assert lc.record_removed_characters_sync(tmp_path, removed, frozenset({"Old"})) == 1
    peers = json.loads((tmp_path / "visit_peers.json").read_text(encoding="utf-8"))
    assert peers["pending_retire"] == [lc.retire_item("New", uid)]


# ── 快照里同一个 uid 换了名字（另一台设备上改过名） ──────────────────


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
    # 变异：不记改名必红（旧名下的对端没人迁，以后同名新角色会继承）
    assert peers["pending_rename"] == {"old": "本地角色", "new": "新名字", "uid": uid, "source": "cloudsave"}
    assert "pending_retire" not in peers
    # 启动对账按 uid 正向迁移
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
    # 导入回滚：这个 uid 仍叫本地角色
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
    # 标记只有一个位置：已有一笔没对完账的改名时不覆盖（变异：覆盖必红），排进队列（变异：只记日志必红）
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
    # 变异：不按队列依次处理必红（第二个角色的条目留在旧名下）
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
    # 排队中的改名同样算作这个角色的另一个名字（变异：只看位置必红）
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
    # 同一个角色 A -> B 还没对完账，快照又把它改成 C：标记位不覆盖，对账见它到了第三个名字再一并迁
    with caplog.at_level("INFO", logger=lc.logger.name):
        assert lc.record_renamed_characters_sync(config_dir, [{"old": "B", "new": "C", "uid": uid}]) == 1
    assert not [r for r in caplog.records if r.levelname == "WARNING"]
    # 新名字 C 记进标记，对账前同样算占用（变异：不记必红：覆盖下载 C 会换掉 uid）
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
    # 链式改名 A -> C（旧名 A 接着归了下一个本机角色，handed_over）落在这个角色自己没对完账的
    # 标记上：chained 与 handed_over 一起补进去（变异：丢掉 handed_over 必红）
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
    # 两步互不连累（变异：第一步失败就不记改名必红），失败照样交给导入方记日志
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
    # 快照里只有 uid 换到「新名字」下的本地角色：原来叫「新名字」的那个角色没了。它的名字被本机
    # 另一个角色接过去，不是同名换 uid：记它退役（变异：当成保留必红），并照报改名——启动对账
    # 先退役它、再迁移（变异：照旧跳过改名必红）
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
    # 本地角色（uid U）的「旧名 -> 本地角色」改名还没对完账；快照里的「本地角色」换了一个新 uid
    kept = {"本地角色": {"_reserved": {"character_uid": "d" * 32}}}
    assert operations._removed_local_characters(target_cm, kept)[0] == []      # 同名换 uid：已知行为，不算删除
    replaced = operations._replaced_local_characters(target_cm, kept)
    assert replaced == [{"name": "本地角色", "character_uid": uid}]
    roster = await seed_roster(config_dir, own_char="旧名")
    await seed_roster(config_dir, peer_uid=PEER_Y, tag=TAG_Y, own_char="本地角色")
    await set_roster_marker(config_dir, "pending_rename", {"old": "旧名", "new": "本地角色", "uid": uid})
    record = removed_characters_recorder(target_cm)
    record([], frozenset({"本地角色"}), [], frozenset({"本地角色"}), replaced=replaced)
    peers = json.loads((config_dir / "visit_peers.json").read_text(encoding="utf-8"))
    # 旧 uid 没了：「旧名」下它的条目没人认领，随它退役（变异：不报 / 不处理 replaced 必红）；
    # 「本地角色」下的条目照已知行为留给同名角色，不退役
    assert peers["pending_retire"] == [lc.retire_item("旧名", uid)]
    assert await lc.replay_retires(config_dir, _names_loader({"本地角色"}, {"本地角色": "d" * 32}),
                                   retire_persona=None)
    assert await roster.get_char_entry(PEER_X, "旧名") is None
    assert await roster.get_char_entry(PEER_Y, "本地角色") is not None


async def test_handed_over_skips_names_another_local_character_held_before_the_import(tmp_path):
    from main_logic.visit.subjects import set_roster_marker

    marker = {"old": "A", "new": "B", "uid": "c" * 32}
    await set_roster_marker(tmp_path, "pending_rename", marker)
    # A 导入前归本机另一个角色 V（标记因此本就分不开）；快照把 V 换成新角色 W：
    # A 下的条目有 V 的，不能整个迁给这笔改名（变异：不看导入前的主人必红）
    assert lc.record_handed_over_names_sync(tmp_path, {"A": "v" * 32}, []) == 0
    assert json.loads((tmp_path / "visit_peers.json").read_text(encoding="utf-8"))["pending_rename"] == marker
    # 导入前没人用、或就是这个角色自己的：照记
    assert lc.record_handed_over_names_sync(tmp_path, {"A": "c" * 32}, []) == 1


async def test_a_removed_uid_less_character_does_not_hide_unrelated_uid_less_markers(tmp_path):
    from main_logic.visit.subjects import set_roster_marker

    # 启动导入早于补发 uid：被删的角色可能没有 uid。它不该把所有没 uid 的改名标记都当成自己的
    # （变异：按 uid 比对 None 必红）
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
    # 两个本机角色互换名字：两边的条目都按名字存、分不开，不报改名也不退役
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
    # 另一台设备上：先把「新名字」(other) 改成「第三个名字」，再把「本地角色」(uid) 改成「新名字」。
    # 不成环：先迁 other 再迁 uid，两边的条目都分得开（变异：照旧当成交换跳过 / 不排顺序必红）
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
    assert await roster.get_char_entry(PEER_Y, "第三个名字") is not None      # other 的条目跟着它走
    assert await roster.get_char_entry(PEER_X, "新名字") is not None          # uid 的条目跟着它走
    assert await roster.get_char_entry(PEER_Y, "新名字") is None              # uid 没继承 other 的
    assert await roster.get_char_entry(PEER_X, "本地角色") is None


async def test_a_rename_whose_old_name_goes_to_a_new_character_still_migrates(tmp_path):
    from tests.unit.visit_memory_test_helpers import PEER_X

    config_dir = tmp_path
    uid, newcomer = "c" * 32, "d" * 32
    roster = await seed_roster(config_dir, own_char="A")
    # 快照把这个角色改名为 C，又把 A 给了一个本机原来没有的角色：A 下的条目此刻都还是它的
    record = removed_characters_recorder(SimpleNamespace(config_dir=str(config_dir)))
    record([], frozenset(), [{"old": "A", "new": "C", "uid": uid}], frozenset({"A"}))
    peers = json.loads((config_dir / "visit_peers.json").read_text(encoding="utf-8"))
    assert peers["pending_rename"] == {"old": "A", "new": "C", "uid": uid, "source": "cloudsave",
                                       "handed_over": ["A"]}
    unsettled = await lc.reconcile_renames(config_dir, {"A", "C"}, {"A": newcomer, "C": uid})
    # 变异：不记 handed_over / 对账不认 handed_over 必红（新角色 A 继承原角色的对端）
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
    # A -> B 迁了一半；快照把它改成 C，又把 B 给了新角色
    await set_roster_marker(config_dir, "pending_rename", {"old": "A", "new": "B", "uid": uid})
    record = removed_characters_recorder(SimpleNamespace(config_dir=str(config_dir)))
    record([], frozenset(), [{"old": "B", "new": "C", "uid": uid}], frozenset({"B"}))
    peers = json.loads((config_dir / "visit_peers.json").read_text(encoding="utf-8"))
    assert peers["pending_rename"]["handed_over"] == ["B"]
    unsettled = await lc.reconcile_renames(config_dir, {"B", "C"}, {"B": newcomer, "C": uid})
    # 变异：不给已有标记补 handed_over 必红（B 下迁了一半的条目留给新角色 B）
    assert unsettled == frozenset()
    assert await roster.get_char_entry(PEER_X, "C") is not None
    assert await roster.get_char_entry(PEER_Y, "C") is not None
    assert await roster.get_char_entry(PEER_Y, "B") is None
