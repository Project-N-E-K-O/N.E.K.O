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


def _local_uid(cm) -> str:
    uid = get_character_uid(cm.load_characters()["猫娘"]["本地角色"])
    assert uid
    return uid


def test_full_snapshot_reports_the_removed_character_before_committing(pair):
    source_cm, target_cm = pair
    uid = _local_uid(target_cm)
    _stage_full_snapshot(source_cm, target_cm)
    seen = []

    def record(removed, kept_names):
        # 先于删除落盘：这时 characters.json 里还是本地角色
        seen.append((removed, kept_names, _names_on_disk(target_cm)))

    result = CloudSaveManager(target_cm).import_if_needed(reason="unit", force=True, on_characters_removed=record)
    assert result["action"] == "imported"
    assert seen == [([{"name": "本地角色", "character_uid": uid}], frozenset({"云端角色"}), {"本地角色"})]
    assert _names_on_disk(target_cm) == {"云端角色"}


def test_import_without_removals_does_not_call_back(pair):
    source_cm, target_cm = pair
    # 单角色导出只产出合并语义的快照：本地角色保留
    export_cloudsave_character_unit(source_cm, "云端角色")
    shutil.copytree(source_cm.cloudsave_dir, target_cm.cloudsave_dir, dirs_exist_ok=True)
    seen = []
    import_local_cloudsave_snapshot(target_cm, on_characters_removed=lambda removed, _kept: seen.append(removed))
    assert seen == []
    assert {"本地角色", "云端角色"} <= _names_on_disk(target_cm)


def test_a_failing_callback_does_not_stop_the_import(pair):
    source_cm, target_cm = pair
    _stage_full_snapshot(source_cm, target_cm)

    def broken(_removed, _kept_names):
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
    assert operations._removed_local_characters(target_cm, {}) == [{"name": "本地角色", "character_uid": uid}]
    # 同一个 uid 在快照里换了名字：是改名不是删除
    renamed = {"新名字": {"_reserved": {"character_uid": uid}}}
    assert operations._removed_local_characters(target_cm, renamed) == []
    assert operations._removed_local_characters(target_cm, {"本地角色": {}}) == []
    # 文件读不出：什么都不报（变异：退回 load_characters 的默认角色必红）
    path = Path(target_cm.get_runtime_config_path("characters.json"))
    path.write_text("{broken", encoding="utf-8")
    assert operations._removed_local_characters(target_cm, {}) == []
    # 合法 JSON 但嵌套过深（json.load 抛 RecursionError）：同样当读不出，不能让导入中止
    path.write_text("[" * 100000 + "]" * 100000, encoding="utf-8")
    assert operations._removed_local_characters(target_cm, {}) == []


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


def test_the_other_rename_name_is_not_retired_when_the_snapshot_keeps_it(tmp_path):
    import asyncio

    from main_logic.visit.subjects import set_roster_marker

    uid = "c" * 32
    asyncio.run(set_roster_marker(tmp_path, "pending_rename", {"old": "Old", "new": "New", "uid": uid}))
    removed = [{"name": "New", "character_uid": uid}]
    # 快照里有一个叫 Old 的角色：按名字退役会删掉它的条目（变异：不看 kept_names 必红）
    assert lc.record_removed_characters_sync(tmp_path, removed, frozenset({"Old"})) == 1
    peers = json.loads((tmp_path / "visit_peers.json").read_text(encoding="utf-8"))
    assert peers["pending_retire"] == [lc.retire_item("New", uid)]
