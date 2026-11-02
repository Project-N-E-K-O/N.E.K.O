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
    # Visit personas are stored by uid under config_dir/visit_persona; point to the same temporary configuration directory
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


# ── Renaming ──────────────────────────────────────────────────────────────


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
    assert persona_path.exists()                                   # Personas are stored by uid and unchanged by rename


async def test_rename_migrates_while_another_character_is_visiting(env):
    cm, _path, config_dir = env
    roster, _old_uid, _persona = await _seed(cm, config_dir)
    # Another character's visit is writing spool, a normal occurrence after #3372
    from main_logic.visit.subjects import derive_pair_id, derive_peer_char_id
    from tests.unit.visit_memory_test_helpers import OWN_A, TAG_X

    live = VisitSpool(config_dir, vid(9))
    header = {"v": 1, "visit_id": vid(9), "role": "host", "own_uid": OWN_A, "own_char": "Other",
              "own_char_uid": _uid(cm, "Other"), "pair_id": derive_pair_id(OWN_A, PEER_X), "peer_uid": PEER_X,
              "peer_char_id": derive_peer_char_id(PEER_X, TAG_X), "peer_char_tag": TAG_X,
              "started_at": 1000.0, "lang": "zh"}
    await live.open(header, now=0.0)
    try:
        status, body = await _rename(cm, "Old", "New")
    finally:
        await live.close()
    # Mutation: reporting busy during another character's write causes partial_success and leftover markers, which must fail
    assert status == 200 and body["success"] is True and "partial_success" not in body
    assert await roster.get_char_entry(PEER_X, "New") is not None
    assert (await VisitSpool(config_dir, vid(1)).read_state())["own_char"] == "New"
    assert "pending_rename" not in _peers(config_dir)


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
        assert not task.done()                     # Finalization still completes normally
        release.set()
        # Mutation: dropping cancellation and returning normally must fail
        with pytest.raises(asyncio.CancelledError):
            await task
    data = _peers(config_dir)
    assert "pending_rename" not in data and "pending_retire" not in data


async def test_rename_refused_while_an_earlier_rename_cannot_be_reconciled(env):
    cm, path, config_dir = env
    await _seed(cm, config_dir)
    # Both names from the prior rename exist without uid, so direction is ambiguous; directly overwriting the old marker must fail
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


# ── Deletion ──────────────────────────────────────────────────────────────


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
    # Deletion committed without rollback, but retirement incomplete: retain the marker
    assert status == 200 and body["success"] is True and body["visit_data_retire_pending"] is True
    assert "Old" not in cm.load_characters()["猫娘"]
    assert _peers(config_dir)["pending_retire"] == [{"name": "Old", "character_uid": uid, "roster_done": True}]
    # While marked, creating the same name always returns 409; removing add_catgirl's check must fail mutation testing
    status, body = await _add(cm, "Old")
    assert status == 409 and body["error_code"] == "VISIT_DATA_BUSY"
    assert "Old" not in cm.load_characters()["猫娘"]
    # Retirement can now finish: complete it before allowing creation
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


# ── Character-card saving ────────────────────────────────────────────────────────


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
    # Existing-character saves remain unaffected; without markers, creation proceeds normally
    status, body = await _save_card(cm, "Other")
    assert status == 200 and body["success"] is True
    status, body = await _save_card(cm, "Fresh")
    assert status == 200 and body["success"] is True



# ── Workshop unsubscribe ──────────────────────────────────────────────────────────

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
    # Deletion uncommitted: remove markers according to configuration; failing to finalize markers on failure must fail mutation testing
    assert "pending_retire" not in _peers(config.config_dir)


async def test_unsubscribe_aborts_when_the_retire_marker_cannot_be_written(tmp_path, monkeypatch):
    config = _unsubscribe_config(tmp_path)
    await _seed_lan(config.config_dir, monkeypatch)
    (config.config_dir / "visit_peers.json").write_text("{broken", encoding="utf-8")
    before = json.loads(json.dumps(config.characters))
    (status, body), steam_calls = await _unsubscribe(monkeypatch, config)
    # If markers cannot persist, do not commit deletion or send Steam unsubscribe, or visit residue loses its owner
    assert status == 500 and body["code"] == "LOCAL_CONFIG_CLEANUP_FAILED"
    assert steam_calls == [] and config.saved == [] and config.characters == before
    assert await VisitSpool(config.config_dir, vid(1)).read_state() is not None


# ── New character creation during Workshop synchronization ──────────────────────────────────────────────────


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
    # Without retirement markers, add normally without creating visit files; with markers, skip this round (removing the check must fail)
    assert result["added"] == (0 if retiring else 1)
    assert ("Synced" in cm.load_characters()["猫娘"]) is not retiring
    assert (config_dir / "visit_peers.json").exists() is retiring


# ── Character-card import ────────────────────────────────────────────────────────


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


async def test_a_crashing_retry_answers_409_not_500(env, monkeypatch):
    cm, _path, config_dir = env
    await add_roster_marker_item(config_dir, "pending_retire", lc.retire_item("Gone", "e" * 32))

    async def broken(*_a, **_k):
        raise OSError("disk unavailable")

    monkeypatch.setattr(lc, "replay_retires", broken)
    # Mutation: unguarded retry produces 500 in creation and aborts the Workshop-sync loop; must fail
    status, body = await _add(cm, "Gone")
    assert status == 409 and body["error_code"] == "VISIT_DATA_BUSY"


# ── Same-name creation/deletion during incomplete rename migration ─────────────────────────────────


async def _failed_rename(cm, config_dir, monkeypatch):
    """Old -> New committed with the visit migration failing: the marker stays, Old is free in the config."""
    roster, uid, persona_path = await _seed(cm, config_dir)
    original = VisitSpool.rename_own_char

    async def locked(*_a, **_k):
        raise OSError("spool locked")

    monkeypatch.setattr(VisitSpool, "rename_own_char", locked)
    status, body = await _rename(cm, "Old", "New")
    assert status == 200 and body["visit_data_migration_pending"] is True
    assert _peers(config_dir)["pending_rename"] == {"old": "Old", "new": "New", "uid": uid}

    def unlock():
        monkeypatch.setattr(VisitSpool, "rename_own_char", original)

    return roster, uid, persona_path, unlock


async def test_creating_the_old_name_of_an_unsettled_rename_is_refused(env, monkeypatch):
    cm, _path, config_dir = env
    roster, uid, _persona, unlock = await _failed_rename(cm, config_dir, monkeypatch)
    # Migration still incomplete: new characters cannot occupy the old name; name_blocked ignoring rename markers must fail
    status, body = await _add(cm, "Old")
    assert status == 409 and body["error_code"] == "VISIT_DATA_BUSY"
    assert "Old" not in cm.load_characters()["猫娘"]
    status, body = await _rename(cm, "Other", "Old")
    assert status == 409 and body["error_code"] == "VISIT_DATA_BUSY"
    assert "Other" in cm.load_characters()["猫娘"]
    # Migration can now finish: reconcile under lock before creation; old peers follow New, while a new Old inherits none
    unlock()
    status, body = await _add(cm, "Old")
    assert status == 200 and body["success"] is True
    assert "pending_rename" not in _peers(config_dir)
    assert await roster.get_char_entry(PEER_X, "Old") is None
    assert await roster.get_char_entry(PEER_X, "New") is not None
    assert (await VisitSpool(config_dir, vid(1)).read_state())["own_char"] == "New"
    assert _uid(cm, "Old") != uid
    # This marker no longer blocks later renames
    status, body = await _rename(cm, "Other", "Other2")
    assert status == 200 and body["success"] is True


async def test_deleting_the_new_name_of_an_unsettled_rename_retires_both_names(env, monkeypatch):
    cm, _path, config_dir = env
    roster, _uid_old, persona_path, unlock = await _failed_rename(cm, config_dir, monkeypatch)
    # Migration still incomplete: deleting New would leave Old entries, so reject first; deletion without prior reconciliation must fail
    status, body = await _delete(cm, "New")
    assert status == 409 and body["error_code"] == "VISIT_DATA_BUSY"
    assert "New" in cm.load_characters()["猫娘"]
    assert "pending_retire" not in _peers(config_dir)
    unlock()
    status, body = await _delete(cm, "New")
    assert status == 200 and body["success"] is True and "partial_success" not in body
    assert "New" not in cm.load_characters()["猫娘"]
    data = _peers(config_dir)
    assert "pending_rename" not in data and "pending_retire" not in data
    assert await roster.get_char_entry(PEER_X, "Old") is None
    assert await roster.get_char_entry(PEER_X, "New") is None
    assert await VisitSpool(config_dir, vid(1)).read_state() is None
    assert not persona_path.exists()
    # Subsequent creation of Old inherits no old peers
    status, body = await _add(cm, "Old")
    assert status == 200 and await roster.get_char_entry(PEER_X, "Old") is None


async def test_deleting_another_character_ignores_an_unsettled_rename(env, monkeypatch):
    cm, _path, config_dir = env
    _roster, uid, _persona, _unlock = await _failed_rename(cm, config_dir, monkeypatch)
    status, body = await _delete(cm, "Other")
    assert status == 200 and body["success"] is True
    assert _peers(config_dir)["pending_rename"] == {"old": "Old", "new": "New", "uid": uid}


async def _queue_behind(config_dir, slot):
    """Move the current ``pending_rename`` into ``pending_rename_queue`` and put ``slot`` in its place."""
    data = _peers(config_dir)
    data["pending_rename_queue"] = [data["pending_rename"]]
    data["pending_rename"] = slot
    (config_dir / "visit_peers.json").write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


async def test_a_queued_rename_is_reconciled_before_its_name_is_reused(env, monkeypatch):
    cm, _path, config_dir = env
    roster, _uid_old, _persona, unlock = await _failed_rename(cm, config_dir, monkeypatch)
    # Cloud snapshot renamed several characters: Old -> New follows a rename for an already-deleted character
    await _queue_behind(config_dir, {"old": "Gone", "new": "Gone2", "uid": "f" * 32})
    unlock()
    # Mutation: reconciling only the front entry must fail, since the queue keeps Old occupied
    status, body = await _add(cm, "Old")
    assert status == 200 and body["success"] is True
    data = _peers(config_dir)
    assert "pending_rename" not in data and "pending_rename_queue" not in data
    assert await roster.get_char_entry(PEER_X, "Old") is None
    assert await roster.get_char_entry(PEER_X, "New") is not None


async def test_a_name_queued_behind_an_ambiguous_rename_is_busy_not_ambiguous(env, monkeypatch):
    cm, _path, config_dir = env
    _roster, _uid_old, _persona, unlock = await _failed_rename(cm, config_dir, monkeypatch)
    # The front rename is permanently ambiguous (Other and New belong to different characters); Old -> New follows it sharing New:
    # wait for that entry, since migrating first would mix both characters' data
    ambiguous = {"old": "Other", "new": "New", "uid": _uid(cm, "Current")}
    await _queue_behind(config_dir, ambiguous)
    unlock()
    # Old is blocked by someone else's ambiguity: respond 'retry later', not advice to delete Other/New; ignoring names must fail
    assert await character_hooks.name_block_reason(cm, "Old") == (character_hooks.NAME_BUSY, frozenset())
    assert await character_hooks.name_block_reason(cm, "Other") == (
        character_hooks.NAME_AMBIGUOUS, frozenset({"Other", "New"}))


async def test_a_rename_queued_behind_an_unrelated_ambiguous_rename_still_settles(env, monkeypatch):
    cm, _path, config_dir = env
    roster, _uid_old, _persona, unlock = await _failed_rename(cm, config_dir, monkeypatch)
    # The front rename belongs to another character and is permanently ambiguous (Other occupied), sharing no names with Old -> New
    ambiguous = {"old": "Other", "new": "Current", "uid": _uid(cm, "Current")}
    await _queue_behind(config_dir, ambiguous)
    unlock()
    # Move that ambiguous entry to the tail and finish Old -> New normally; stopping at a blocked head must fail
    assert await character_hooks.name_block_reason(cm, "Old") is None
    assert await roster.get_char_entry(PEER_X, "New") is not None
    assert await roster.get_char_entry(PEER_X, "Old") is None
    assert (await VisitSpool(config_dir, vid(1)).read_state())["own_char"] == "New"
    data = _peers(config_dir)
    assert data["pending_rename"] == ambiguous and "pending_rename_queue" not in data
    assert await character_hooks.name_block_reason(cm, "Other") == (
        character_hooks.NAME_AMBIGUOUS, frozenset({"Other", "Current"}))


async def test_deleting_a_character_whose_rename_is_stuck_in_the_queue_retires_its_other_name(env, monkeypatch):
    cm, _path, config_dir = env
    roster, uid, _persona, unlock = await _failed_rename(cm, config_dir, monkeypatch)
    await _queue_behind(config_dir, {"old": "Other", "new": "New", "uid": _uid(cm, "Current")})
    unlock()
    # The queued rename cannot proceed because the ambiguous head also occupies New; deletion of New must not block forever, retiring old Old with it (omitting Old retirement must fail)
    status, body = await _delete(cm, "New")
    assert status == 200 and body["success"] is True
    assert "New" not in cm.load_characters()["猫娘"]
    assert _peers(config_dir)["pending_retire"] == [lc.retire_item("Old", uid)]
    assert await roster.get_char_entry(PEER_X, "New") is None
    assert await VisitSpool(config_dir, vid(1)).read_state() is None
    # Complete Old retirement before recreating it; inherit no entries
    status, body = await _add(cm, "Old")
    assert status == 200 and await roster.get_char_entry(PEER_X, "Old") is None
    assert "pending_retire" not in _peers(config_dir)


async def test_reconciling_on_demand_refuses_a_fallback_character_config(env, monkeypatch):
    cm, path, config_dir = env
    _roster, _uid_old, _persona, unlock = await _failed_rename(cm, config_dir, monkeypatch)
    unlock()
    marker = _peers(config_dir)["pending_rename"]
    original = Path(path).read_text(encoding="utf-8")
    # Corrupt configuration: ordinary reads fall back to cache/defaults, potentially treating New as deleted and removing markers without migration
    Path(path).write_text("{broken", encoding="utf-8")
    try:
        # Mutation: reconciling fallback configuration and allowing progress without a strict read must fail
        assert await character_hooks.name_block_reason(cm, "Old") == (character_hooks.NAME_BUSY, frozenset())
        assert _peers(config_dir)["pending_rename"] == marker
        assert (await VisitSpool(config_dir, vid(1)).read_state())["own_char"] == "Old"
    finally:
        Path(path).write_text(original, encoding="utf-8")


async def test_reconciling_on_demand_refuses_a_missing_runtime_character_config(env, monkeypatch):
    cm, _path, config_dir = env
    _roster, _uid_old, _persona, unlock = await _failed_rename(cm, config_dir, monkeypatch)
    unlock()
    marker = _peers(config_dir)["pending_rename"]
    runtime_path = Path(cm.get_runtime_config_path("characters.json"))
    original = runtime_path.read_text(encoding="utf-8")
    runtime_path.unlink()
    try:
        # Markers prove this machine previously had characters; missing runtime files do not mean fresh installation, and fallback seed characters cannot reconcile state
        # Mutation: allowing absent runtime files must fail
        assert await character_hooks.name_block_reason(cm, "Old") == (character_hooks.NAME_BUSY, frozenset())
        assert _peers(config_dir)["pending_rename"] == marker
    finally:
        runtime_path.write_text(original, encoding="utf-8")


async def test_deleting_a_character_whose_rename_waits_for_a_retirement_retires_its_other_name(env):
    cm, _path, config_dir = env
    uid = _uid(cm, "Old")
    # Prev -> Old committed; migration awaits retirement of a deleted character under Old, while entries remain under Prev
    roster = await seed_roster(config_dir, own_char="Prev")
    await make_visit(config_dir, vid(1), [ln(1)], own_char="Prev", own_char_uid=uid)
    await set_roster_marker(config_dir, "pending_rename", {"old": "Prev", "new": "Old", "uid": uid})
    await add_roster_marker_item(config_dir, "pending_retire", lc.retire_item("Old", "f" * 32))
    status, body = await _delete(cm, "Old")
    assert status == 200 and body["success"] is True
    # Not permanent ambiguity: Prev is unoccupied by other characters, so retire it too; looking only at the queue leaves Prev peers for future Prev and must fail
    assert lc.retire_item("Prev", uid) in _peers(config_dir)["pending_retire"]
    status, body = await _add(cm, "Prev")
    assert status == 200 and await roster.get_char_entry(PEER_X, "Prev") is None


async def test_a_rename_waiting_for_a_retirement_is_busy_not_ambiguous(env, monkeypatch):
    cm, _path, config_dir = env
    uid = _uid(cm, "Old")
    await set_roster_marker(config_dir, "pending_rename", {"old": "Gone", "new": "Old", "uid": uid})
    await add_roster_marker_item(config_dir, "pending_retire", lc.retire_item("Old", "f" * 32))
    replays = []

    async def still_failing(*_a, **_k):
        replays.append(True)
        return False

    monkeypatch.setattr(lc, "replay_retires", still_failing)
    # Rename awaits target-name retirement completion, retaining markers like permanent ambiguity; it is merely waiting its turn, so respond 'retry later',
    # not advice to delete a character (ignoring retirement must fail); attempt retirement completion before deciding (omitting that attempt must fail)
    assert await character_hooks.name_block_reason(cm, "Gone") == (character_hooks.NAME_BUSY, frozenset())
    assert replays


async def test_unsubscribe_refuses_a_character_whose_rename_cannot_be_settled(tmp_path, monkeypatch):
    config = _unsubscribe_config(tmp_path)
    await _seed_lan(config.config_dir, monkeypatch)
    # Lan results from an earlier Lan0 -> Lan rename whose migration remains incomplete
    marker = {"old": "Lan0", "new": "Lan", "uid": LAN_UID}
    await set_roster_marker(config.config_dir, "pending_rename", marker)

    async def locked(*_a, **_k):
        raise OSError("spool locked")

    monkeypatch.setattr(VisitSpool, "rename_own_char", locked)
    before = json.loads(json.dumps(config.characters))
    (status, body), steam_calls = await _unsubscribe(monkeypatch, config)
    # Same path as marker-write failure: abort entirely without writing characters.json or sending Steam unsubscribe
    assert status == 500 and body["code"] == "LOCAL_CONFIG_CLEANUP_FAILED"
    assert steam_calls == [] and config.saved == [] and config.characters == before
    assert _peers(config.config_dir)["pending_rename"] == marker


async def test_an_ambiguous_rename_reports_only_its_own_names(env, monkeypatch):
    cm, _path, config_dir = env
    await _seed(cm, config_dir)
    status, _body = await _add(cm, "Extra")
    assert status == 200
    # Two unrelated renames are permanently ambiguous because their other names belong to different characters
    first = {"old": "Other", "new": "Current", "uid": _uid(cm, "Current")}
    second = {"old": "Extra", "new": "Old", "uid": _uid(cm, "Old")}
    await set_roster_marker(config_dir, "pending_rename", first)
    await add_roster_marker_item(config_dir, "pending_rename_queue", second)
    # Report only the entry involving this name, never advise deleting unrelated characters in the other entry; reporting all pending names must fail
    assert await character_hooks.name_block_reason(cm, "Old") == (
        character_hooks.NAME_AMBIGUOUS, frozenset({"Extra", "Old"}))
    assert await character_hooks.name_block_reason(cm, "Current") == (
        character_hooks.NAME_AMBIGUOUS, frozenset({"Other", "Current"}))


async def test_an_ambiguous_rename_is_reported_while_its_name_also_has_a_roster_done_retirement(env, monkeypatch):
    cm, _path, config_dir = env
    await _seed(cm, config_dir)
    ambiguous = {"old": "Other", "new": "Current", "uid": _uid(cm, "Current")}
    await set_roster_marker(config_dir, "pending_rename", ambiguous)
    # Current has a retirement entry with cleared roster but repeatedly failing uid-based steps
    await add_roster_marker_item(config_dir, "pending_retire", {**lc.retire_item("Current", "e" * 32), "roster_done": True})

    async def stuck(*_a, **_k):
        return False

    monkeypatch.setattr(lc, "settle_retire", stuck)
    # Still block the name, but permanent ambiguity requires 'delete one first', not endless 'retry later'; stopping after the retirement check must fail
    assert await character_hooks.name_block_reason(cm, "Current") == (
        character_hooks.NAME_AMBIGUOUS, frozenset({"Other", "Current"}))
