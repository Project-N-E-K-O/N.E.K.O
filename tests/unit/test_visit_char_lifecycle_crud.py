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

"""Character rename / delete / create endpoints carry the visit data along (OD-13, PR-09b).

Real ``characters_router.crud`` on a config manager rooted in ``tmp_path``
(never the real runtime root).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from main_logic.visit import char_lifecycle as lc
from main_logic.visit.spool import VisitSpool
from main_logic.visit.subjects import add_roster_marker_item, set_roster_marker
from main_routers.visit_router import character_hooks, persona, runtime
from tests.unit.test_character_uid import _backfilled_manager, _DummyRequest, _init_router_state
from tests.unit.visit_memory_test_helpers import PEER_X, ln, make_visit, seed_roster, vid
from utils import character_memory
from utils.config_manager import get_character_uid


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    runtime._reset_for_tests()
    monkeypatch.setattr(character_memory, "character_config_mutation_lock", asyncio.Lock())
    yield
    runtime._reset_for_tests()


@pytest.fixture
def env(tmp_path, monkeypatch):
    cm, path = _backfilled_manager(tmp_path, {"Current": {"昵称": "Current"}, "Old": {"昵称": "Old"},
                                              "Other": {"昵称": "Other"}})
    config_dir = Path(cm.config_dir)
    # 串门人设按 uid 存在 config_dir/visit_persona 下：指向同一个 tmp 配置目录
    monkeypatch.setattr(persona._hooks, "config_dir", lambda: config_dir)
    with patch("utils.config_manager._config_manager", cm):
        yield cm, path, config_dir


def _uid(cm, name):
    return get_character_uid(cm.load_characters()["猫娘"][name])


def _result(response):
    status = getattr(response, "status_code", 200)
    body = response if isinstance(response, dict) else json.loads(bytes(response.body))
    return status, body


def _crud(cm, reload_ok=True):
    crud = _init_router_state(cm)
    return crud, (patch.object(crud, "release_memory_server_character", AsyncMock(return_value=True)),
                  patch.object(crud, "notify_memory_server_reload", AsyncMock(return_value=reload_ok)))


async def _rename(cm, old, new, reload_ok=True):
    crud, (a, b) = _crud(cm, reload_ok)
    with a, b:
        return _result(await crud.rename_catgirl(old, _DummyRequest({"new_name": new})))


async def _delete(cm, name, reload_ok=True):
    crud, (a, b) = _crud(cm, reload_ok)
    with a, b:
        return _result(await crud.delete_catgirl(name))


async def _add(cm, name):
    crud, (a, b) = _crud(cm)
    with a, b:
        return _result(await crud.add_catgirl(_DummyRequest({"档案名": name, "昵称": name})))


def _peers(config_dir) -> dict:
    return json.loads((config_dir / "visit_peers.json").read_text(encoding="utf-8"))


async def _seed(cm, config_dir, name="Old"):
    uid = _uid(cm, name)
    roster = await seed_roster(config_dir, own_char=name)
    await make_visit(config_dir, vid(1), [ln(1)], own_char=name, own_char_uid=uid)
    persona_path = config_dir / "visit_persona" / f"{uid}.json"
    persona_path.parent.mkdir(parents=True, exist_ok=True)
    persona_path.write_text("{}", encoding="utf-8")
    return roster, uid, persona_path


# ── 改名 ──────────────────────────────────────────────────────────────


async def test_rename_without_visit_data_is_unchanged(env):
    cm, _path, config_dir = env
    status, body = await _rename(cm, "Old", "New")
    assert status == 200 and body["success"] is True and "partial_success" not in body
    assert not (config_dir / "visit_peers.json").exists()


async def test_rename_migrates_roster_and_spools(env):
    cm, _path, config_dir = env
    roster, uid, persona_path = await _seed(cm, config_dir)
    status, body = await _rename(cm, "Old", "New")
    assert status == 200 and body["success"] is True and "partial_success" not in body
    assert await roster.get_char_entry(PEER_X, "Old") is None
    assert await roster.get_char_entry(PEER_X, "New") is not None
    assert (await VisitSpool(config_dir, vid(1)).read_state())["own_char"] == "New"
    assert (await VisitSpool(config_dir, vid(1)).read_header())["own_char"] == "New"
    assert "pending_rename" not in _peers(config_dir)
    assert persona_path.exists()                                   # 人设按 uid 存，改名不动


async def test_rolled_back_rename_leaves_visit_data_under_the_old_name(env):
    cm, _path, config_dir = env
    roster, _old_uid, _persona = await _seed(cm, config_dir)
    status, _body = await _rename(cm, "Old", "New", reload_ok=False)
    assert status == 500 and "Old" in cm.load_characters()["猫娘"]
    assert await roster.get_char_entry(PEER_X, "Old") is not None
    assert (await VisitSpool(config_dir, vid(1)).read_state())["own_char"] == "Old"
    assert "pending_rename" not in _peers(config_dir)


async def test_failed_migration_keeps_the_marker_and_reports_partial(env, monkeypatch):
    cm, _path, config_dir = env
    _roster, uid, _persona = await _seed(cm, config_dir)

    async def locked(*_a, **_k):
        raise OSError("spool locked")

    monkeypatch.setattr(VisitSpool, "rename_own_char", locked)
    status, body = await _rename(cm, "Old", "New")
    assert status == 200 and body["success"] is True
    assert body["partial_success"] is True and body["visit_data_migration_pending"] is True
    assert _peers(config_dir)["pending_rename"] == {"old": "Old", "new": "New", "uid": uid}


@pytest.mark.parametrize("op", ["rename", "delete"])
async def test_cancellation_during_settlement_is_propagated(env, monkeypatch, op):
    cm, _path, config_dir = env
    await _seed(cm, config_dir)
    entered, release = asyncio.Event(), asyncio.Event()
    name = "settle_rename" if op == "rename" else "settle_retire"
    real = getattr(character_hooks, name)

    async def slow(*a, **k):
        entered.set()
        await release.wait()
        return await real(*a, **k)

    monkeypatch.setattr(character_hooks, name, slow)
    crud, (a, b) = _crud(cm)
    with a, b:
        call = (crud.rename_catgirl("Old", _DummyRequest({"new_name": "New"})) if op == "rename"
                else crud.delete_catgirl("Old"))
        task = asyncio.ensure_future(call)
        await asyncio.wait_for(entered.wait(), 3)
        task.cancel()
        await asyncio.sleep(0.02)
        assert not task.done()                     # 收尾照常做完
        release.set()
        # 变异：丢掉取消标志、正常返回必红
        with pytest.raises(asyncio.CancelledError):
            await task
    data = _peers(config_dir)
    assert "pending_rename" not in data and "pending_retire" not in data


async def test_rename_refused_while_an_earlier_rename_cannot_be_reconciled(env):
    cm, path, config_dir = env
    await _seed(cm, config_dir)
    # 上一次改名的两个名字都在、又没有 uid：方向判不出来（变异：直接覆盖旧标记必红）
    stale = {"old": "Old", "new": "Other"}
    await set_roster_marker(config_dir, "pending_rename", stale)
    before = path.read_bytes()
    status, body = await _rename(cm, "Current", "Renamed")
    assert status == 409 and body["error_code"] == "VISIT_DATA_BUSY"
    assert path.read_bytes() == before and _peers(config_dir)["pending_rename"] == stale


async def test_rename_into_a_name_still_being_retired_is_refused(env, monkeypatch):
    cm, path, config_dir = env
    await seed_roster(config_dir, own_char="Gone")
    await add_roster_marker_item(config_dir, "pending_retire", lc.retire_item("Gone", "e" * 32))

    async def still_failing(_uid):
        raise OSError("persona locked")

    monkeypatch.setattr(character_hooks, "retire_persona", still_failing)
    before = path.read_bytes()
    status, body = await _rename(cm, "Old", "Gone")
    assert status == 409 and body["error_code"] == "VISIT_DATA_BUSY"
    assert path.read_bytes() == before


# ── 删除 ──────────────────────────────────────────────────────────────


async def test_delete_without_visit_data_is_unchanged(env):
    cm, _path, config_dir = env
    status, body = await _delete(cm, "Old")
    assert status == 200 and body["success"] is True and "partial_success" not in body
    assert not (config_dir / "visit_peers.json").exists()


async def test_delete_retires_the_visit_data(env):
    cm, _path, config_dir = env
    roster, _old_uid, persona_path = await _seed(cm, config_dir)
    other = await make_visit(config_dir, vid(2), [ln(1)], own_char="Other", own_char_uid=_uid(cm, "Other"))
    status, body = await _delete(cm, "Old")
    assert status == 200 and body["success"] is True and "partial_success" not in body
    assert await VisitSpool(config_dir, vid(1)).read_state() is None
    assert await other.read_state() is not None
    assert await roster.get_char_entry(PEER_X, "Old") is None
    assert not persona_path.exists()
    assert "pending_retire" not in _peers(config_dir)


async def test_rolled_back_delete_keeps_the_visit_data(env):
    cm, _path, config_dir = env
    roster, _old_uid, persona_path = await _seed(cm, config_dir)
    status, _body = await _delete(cm, "Old", reload_ok=False)
    assert status == 500 and "Old" in cm.load_characters()["猫娘"]
    assert await VisitSpool(config_dir, vid(1)).read_state() is not None
    assert await roster.get_char_entry(PEER_X, "Old") is not None
    assert persona_path.exists()
    assert "pending_retire" not in _peers(config_dir)


async def test_failed_retirement_blocks_the_name_until_it_is_finished(env, monkeypatch):
    cm, _path, config_dir = env
    _roster, uid, persona_path = await _seed(cm, config_dir)
    real = character_hooks.retire_persona

    async def locked(_uid):
        raise OSError("persona locked")

    monkeypatch.setattr(character_hooks, "retire_persona", locked)
    status, body = await _delete(cm, "Old")
    # 删除已提交、不回滚；退役没做完：标记保留
    assert status == 200 and body["success"] is True and body["visit_data_retire_pending"] is True
    assert "Old" not in cm.load_characters()["猫娘"]
    assert _peers(config_dir)["pending_retire"] == [{"name": "Old", "character_uid": uid}]
    # 标记在时同名新建一律 409（变异：去掉 add_catgirl 的检查必红）
    status, body = await _add(cm, "Old")
    assert status == 409 and body["error_code"] == "VISIT_DATA_BUSY"
    assert "Old" not in cm.load_characters()["猫娘"]
    # 退役能做完了：新建时先补完退役，再放行
    monkeypatch.setattr(character_hooks, "retire_persona", real)
    status, body = await _add(cm, "Old")
    assert status == 200 and body["success"] is True
    assert not persona_path.exists() and "pending_retire" not in _peers(config_dir)
    assert _uid(cm, "Old") != uid


async def test_add_without_visit_data_is_unchanged(env):
    cm, _path, config_dir = env
    status, body = await _add(cm, "Fresh")
    assert status == 200 and body["success"] is True
    assert not (config_dir / "visit_peers.json").exists()


# ── 角色卡保存 ────────────────────────────────────────────────────────


async def _save_card(cm, name):
    from main_routers.characters_router import cards

    with patch.object(cards, "get_config_manager", lambda: cm), \
         patch.object(cards, "_refresh_catgirl_context_after_profile_change", AsyncMock(return_value={})), \
         patch.object(cards, "notify_memory_server_reload", AsyncMock(return_value=True)), \
         patch.object(cards, "_mark_new_character_greeting_pending_safe", AsyncMock(return_value=(True, ""))):
        return _result(await cards.save_character_card(_DummyRequest({
            "character_card_name": name, "charaData": {"档案名": name, "昵称": name},
        })))


async def test_card_save_of_a_new_character_honours_a_pending_retirement(env, monkeypatch):
    cm, _path, config_dir = env
    _init_router_state(cm)
    await add_roster_marker_item(config_dir, "pending_retire", lc.retire_item("Gone", "e" * 32))

    async def locked(_uid):
        raise OSError("persona locked")

    monkeypatch.setattr(character_hooks, "retire_persona", locked)
    status, body = await _save_card(cm, "Gone")
    assert status == 409 and body["error_code"] == "VISIT_DATA_BUSY"
    assert "Gone" not in cm.load_characters()["猫娘"]
    # 已有角色的保存不受影响；没有标记时新建照常
    status, body = await _save_card(cm, "Other")
    assert status == 200 and body["success"] is True
    status, body = await _save_card(cm, "Fresh")
    assert status == 200 and body["success"] is True



# ── 工坊退订 ──────────────────────────────────────────────────────────

LAN_UID = "a1" * 16
OTHER_UID = "b2" * 16


def _unsubscribe_config(tmp_path, *, with_config_dir=True):
    from tests.unit.test_workshop_unsubscribe_theater_cascade import _Config, _workshop_character

    lan = _workshop_character("character_11111111111111111111111111111111")
    lan["_reserved"]["character_uid"] = LAN_UID
    config = _Config(tmp_path, {
        "当前猫娘": "Other",
        "猫娘": {"Lan": lan, "Other": {"_reserved": {"character_uid": OTHER_UID}}},
    })
    if with_config_dir:
        config.config_dir = tmp_path / "config"
        config.config_dir.mkdir()
    return config


async def _unsubscribe(monkeypatch, config):
    from tests.unit.test_workshop_unsubscribe_theater_cascade import ITEM_ID, _install_unsubscribe

    unsubscribe, steam_calls = _install_unsubscribe(monkeypatch, config, candidate="Lan")
    result = await unsubscribe._unsubscribe_workshop_item(
        _DummyRequest({"item_id": str(ITEM_ID)}), asyncio.Event(),
    )
    return _result(result), steam_calls


async def _seed_lan(config_dir, monkeypatch):
    roster = await seed_roster(config_dir, own_char="Lan")
    await make_visit(config_dir, vid(1), [ln(1)], own_char="Lan", own_char_uid=LAN_UID)
    await make_visit(config_dir, vid(2), [ln(1)], own_char="Other", own_char_uid=OTHER_UID)
    persona_path = config_dir / "visit_persona" / f"{LAN_UID}.json"
    persona_path.parent.mkdir(parents=True, exist_ok=True)
    persona_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(persona._hooks, "config_dir", lambda: config_dir)
    return roster, persona_path


async def test_unsubscribe_retires_the_visit_data_of_its_characters(tmp_path, monkeypatch):
    config = _unsubscribe_config(tmp_path)
    roster, persona_path = await _seed_lan(config.config_dir, monkeypatch)
    (status, body), steam_calls = await _unsubscribe(monkeypatch, config)
    assert status == 200 and body["success"] is True, body
    assert steam_calls and "Lan" not in config.characters["猫娘"]
    assert await VisitSpool(config.config_dir, vid(1)).read_state() is None
    assert await VisitSpool(config.config_dir, vid(2)).read_state() is not None
    assert await roster.get_char_entry(PEER_X, "Lan") is None
    assert not persona_path.exists()
    assert "pending_retire" not in _peers(config.config_dir)


async def test_unsubscribe_without_visit_data_writes_no_visit_file(tmp_path, monkeypatch):
    config = _unsubscribe_config(tmp_path)
    (status, body), steam_calls = await _unsubscribe(monkeypatch, config)
    assert status == 200 and body["success"] is True, body
    assert steam_calls
    assert not (config.config_dir / "visit_peers.json").exists()


async def test_unsubscribe_whose_config_write_fails_keeps_the_visit_data(tmp_path, monkeypatch):
    config = _unsubscribe_config(tmp_path)
    roster, persona_path = await _seed_lan(config.config_dir, monkeypatch)

    async def broken(_characters):
        raise OSError("disk full")

    config.asave_characters = broken
    (status, body), steam_calls = await _unsubscribe(monkeypatch, config)
    assert status == 500 and body["code"] == "LOCAL_CONFIG_CLEANUP_FAILED" and steam_calls == []
    assert await VisitSpool(config.config_dir, vid(1)).read_state() is not None
    assert await roster.get_char_entry(PEER_X, "Lan") is not None and persona_path.exists()
    # 删除没提交：标记按配置撤掉（变异：失败路径不收口标记必红）
    assert "pending_retire" not in _peers(config.config_dir)


async def test_unsubscribe_aborts_when_the_retire_marker_cannot_be_written(tmp_path, monkeypatch):
    config = _unsubscribe_config(tmp_path)
    await _seed_lan(config.config_dir, monkeypatch)
    (config.config_dir / "visit_peers.json").write_text("{broken", encoding="utf-8")
    before = json.loads(json.dumps(config.characters))
    (status, body), steam_calls = await _unsubscribe(monkeypatch, config)
    # 记不上标记就不提交删除（否则串门残留没人认领），也不发 Steam 退订
    assert status == 500 and body["code"] == "LOCAL_CONFIG_CLEANUP_FAILED"
    assert steam_calls == [] and config.saved == [] and config.characters == before
    assert await VisitSpool(config.config_dir, vid(1)).read_state() is not None


# ── 工坊同步新建角色 ──────────────────────────────────────────────────


async def _sync_card(cm, tmp_path, name):
    from tests.unit.test_character_memory_regression import reload_module

    _init_router_state(cm)
    sync_cards = reload_module("main_routers.workshop_router.sync_cards")
    folder = tmp_path / f"workshop_{name}"
    folder.mkdir()
    (folder / "card.chara.json").write_text(json.dumps({"档案名": name, "昵称": name}, ensure_ascii=False),
                                            encoding="utf-8")
    items = {"success": True, "items": [{"publishedFileId": "123456", "installedFolder": str(folder)}]}
    with patch.object(sync_cards, "get_subscribed_workshop_items", AsyncMock(return_value=items)), \
         patch("main_routers.characters_router.notify_memory_server_reload", AsyncMock(return_value=True)):
        return await sync_cards.sync_workshop_character_cards(target_item_id="123456")


@pytest.mark.parametrize("retiring", [False, True])
async def test_workshop_sync_waits_for_a_pending_retirement_of_the_name(env, tmp_path, monkeypatch, retiring):
    cm, _path, config_dir = env
    if retiring:
        await add_roster_marker_item(config_dir, "pending_retire", lc.retire_item("Synced", "e" * 32))

        async def locked(_uid):
            raise OSError("persona locked")

        monkeypatch.setattr(character_hooks, "retire_persona", locked)
    result = await _sync_card(cm, tmp_path, "Synced")
    # 没有退役标记时照常添加、也不建串门文件；有标记时这轮跳过（变异：去掉检查必红）
    assert result["added"] == (0 if retiring else 1)
    assert ("Synced" in cm.load_characters()["猫娘"]) is not retiring
    assert (config_dir / "visit_peers.json").exists() is retiring


# ── 导入角色卡 ────────────────────────────────────────────────────────


@pytest.mark.parametrize("retiring", [False, True])
async def test_card_import_honours_a_pending_retirement_of_the_name(tmp_path, monkeypatch, retiring):
    from unittest.mock import MagicMock

    from main_routers.characters_router import cards
    from tests.unit.test_character_uid import _XOR_KEY, _FakeUpload

    raw = json.dumps({"档案名": "Imported", "昵称": "Imported"}, ensure_ascii=False).encode("utf-8")
    payload = bytes(raw[i] ^ _XOR_KEY[i % len(_XOR_KEY)] for i in range(len(raw)))
    config_manager = MagicMock()
    config_manager.config_dir = tmp_path
    config_manager.aload_characters = AsyncMock(return_value={"猫娘": {}})
    config_manager.asave_characters = AsyncMock()
    config_manager.card_face_meta_path = MagicMock(return_value="unused-meta-path")
    if retiring:
        await add_roster_marker_item(tmp_path, "pending_retire", lc.retire_item("Imported", "e" * 32))

        async def locked(_uid):
            raise OSError("persona locked")

        monkeypatch.setattr(character_hooks, "retire_persona", locked)
    with patch.object(cards, "get_config_manager", return_value=config_manager), \
         patch.object(cards, "get_initialize_character_data", return_value=None), \
         patch.object(cards, "_mark_new_character_greeting_pending_safe", new=AsyncMock(return_value=(True, None))), \
         patch.object(cards, "_write_card_meta", new=MagicMock()):
        response = await cards.import_character_card(zip_file=_FakeUpload("card.nekocfg", payload), card_image=None)
    status, body = _result(response)
    if retiring:
        assert status == 409 and body["error_code"] == "VISIT_DATA_BUSY"
        config_manager.asave_characters.assert_not_awaited()
    else:
        assert status == 200
        assert not (tmp_path / "visit_peers.json").exists()
