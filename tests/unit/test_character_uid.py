"""Stable character id ``_reserved.character_uid`` (design doc §5 PR-01).

New / imported characters get a fresh id, renames keep it, existing
characters get one once by the startup backfill and keep it across restarts.
"""
from __future__ import annotations

import ast
import importlib
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from main_routers.shared_state import init_shared_state
from utils.cloudsave_runtime import bootstrap_local_cloudsave_environment
from utils.config_manager import (
    ConfigManager,
    ensure_character_uids,
    get_character_uid,
    is_valid_character_uid,
    new_character_uid,
)


def _make_config_manager(tmp_root: Path) -> ConfigManager:
    with patch.object(ConfigManager, "_get_documents_directory", return_value=tmp_root), patch.object(
        ConfigManager,
        "_get_standard_data_directory_candidates",
        return_value=[tmp_root],
    ), patch.object(
        ConfigManager,
        "get_legacy_app_root_candidates",
        return_value=[],
    ), patch.object(
        ConfigManager,
        "_get_project_root",
        return_value=tmp_root,
    ):
        config_manager = ConfigManager("N.E.K.O")
    config_manager._get_standard_data_directory_candidates = lambda: [tmp_root]
    config_manager.get_legacy_app_root_candidates = lambda: []
    config_manager.project_memory_dir = tmp_root / "memory" / "store"
    return config_manager


def _write_characters(cm: ConfigManager, catgirls: dict) -> Path:
    path = Path(cm.get_runtime_config_path("characters.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"当前猫娘": next(iter(catgirls)), "猫娘": catgirls}, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def _uid_on_disk(path: Path, name: str):
    data = json.loads(path.read_text(encoding="utf-8"))
    return get_character_uid(data["猫娘"][name])


class _DummyRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


# --- helpers -------------------------------------------------------------------


def test_new_uid_is_32_lowercase_hex():
    uid = new_character_uid()
    assert is_valid_character_uid(uid)
    assert len(uid) == 32 and uid == uid.lower()
    assert new_character_uid() != uid
    for bad in (None, "", "x" * 32, uid.upper(), uid[:-1], 123):
        assert not is_valid_character_uid(bad)


def test_ensure_backfills_missing_and_malformed_and_keeps_valid_ids():
    keep = new_character_uid()
    catgirls = {
        "A": {"_reserved": {"character_uid": keep}},
        "B": {},
        "C": {"_reserved": {"character_uid": "not-an-id"}},
    }

    assert ensure_character_uids(catgirls) is True

    assert get_character_uid(catgirls["A"]) == keep
    assert is_valid_character_uid(get_character_uid(catgirls["B"]))
    assert is_valid_character_uid(get_character_uid(catgirls["C"]))
    assert ensure_character_uids(catgirls) is False


def test_ensure_reissues_a_duplicated_id_for_the_later_character():
    shared = new_character_uid()
    catgirls = {
        "First": {"_reserved": {"character_uid": shared}},
        "Copy": {"_reserved": {"character_uid": shared}},
    }

    assert ensure_character_uids(catgirls) is True

    assert get_character_uid(catgirls["First"]) == shared
    assert get_character_uid(catgirls["Copy"]) not in (None, shared)


# --- startup backfill -------------------------------------------------------------


def _backfilled_manager(tmp_path, catgirls):
    cm = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(cm)
    path = _write_characters(cm, catgirls)
    cm.backfill_character_uids()
    return cm, path


def test_existing_characters_get_an_id_once_and_keep_it_across_restarts(tmp_path):
    # Mutation: re-issuing on every startup (or not persisting) turns this red.
    cm, path = _backfilled_manager(tmp_path, {"Old": {"昵称": "Old"}, "Other": {"昵称": "Other"}})
    first = cm.load_characters()["猫娘"]
    uid = get_character_uid(first["Old"])
    assert is_valid_character_uid(uid)
    assert _uid_on_disk(path, "Old") == uid

    restarted = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(restarted)
    assert restarted.backfill_character_uids() is False
    again = restarted.load_characters()["猫娘"]
    assert get_character_uid(again["Old"]) == uid
    assert get_character_uid(again["Other"]) == get_character_uid(first["Other"])
    assert _uid_on_disk(path, "Old") == uid


def test_visit_backfill_preserves_theater_identity_and_persona_hash(tmp_path):
    from services.theater.numeric_v2_identity import numeric_v2_catgirl_binding
    from utils.config_manager import ensure_catgirl_character_id, get_reserved
    from main_routers.characters_router.cards import _strip_local_character_identity

    profile = {'昵称': 'Old'}
    theater_id, _ = ensure_catgirl_character_id(profile)
    cm = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(cm)
    _write_characters(cm, {'Old': profile})
    before = numeric_v2_catgirl_binding(cm, 'Old')
    assert cm.backfill_character_uids()
    after = numeric_v2_catgirl_binding(cm, 'Old')
    saved = cm.load_characters()['猫娘']['Old']
    assert get_reserved(saved, 'character_id') == theater_id
    assert is_valid_character_uid(get_character_uid(saved))
    assert before == after
    exported = _strip_local_character_identity(json.loads(json.dumps(saved)))
    assert get_reserved(exported, 'character_id') is None
    assert get_character_uid(exported) is None

def test_backfill_migrates_legacy_reserved_fields_before_writing(tmp_path):
    """The backfill writes (and caches) the raw file: it must be migrated first.

    Mutation: skipping migrate_catgirl_reserved in the backfill turns this red
    -- the legacy top-level voice_id would be written back unmigrated.
    """
    cm, path = _backfilled_manager(tmp_path, {"Old": {"昵称": "Old", "voice_id": "legacy-voice"}})

    stored = json.loads(path.read_text(encoding="utf-8"))["猫娘"]["Old"]
    assert "voice_id" not in stored
    assert stored["_reserved"]["voice_id"] == "legacy-voice"
    assert is_valid_character_uid(stored["_reserved"]["character_uid"])


def test_loading_characters_never_writes_the_id(tmp_path):
    """The backfill is an explicit startup step, never a side effect of a load.

    During bootstrap a freshly seeded characters.json must stay byte-identical,
    or the legacy-root import treats it as user data and skips importing.
    Mutation: backfilling inside load_characters turns this red.
    """
    cm = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(cm)
    path = _write_characters(cm, {"Seeded": {"昵称": "Seeded"}})
    # The first load may run the pre-existing _reserved schema migration.
    cm.load_characters()
    before = path.read_bytes()

    loaded = cm.load_characters()

    assert path.read_bytes() == before
    assert _uid_on_disk(path, "Seeded") is None
    assert get_character_uid(loaded["猫娘"]["Seeded"]) is None


def test_backfill_without_a_characters_file_writes_nothing(tmp_path):
    cm = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(cm)
    path = Path(cm.get_runtime_config_path("characters.json"))
    assert not path.exists()

    assert cm.backfill_character_uids() is False

    assert not path.exists()


def test_startup_backfills_after_cloudsave_bootstrap_and_before_character_init():
    source = Path(__file__).resolve().parents[2] / "app" / "main_server" / "__init__.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    startup = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_ensure_main_server_runtime_initialized"
    )

    def _first_call_line(name):
        lines = [
            node.lineno for node in ast.walk(startup)
            if isinstance(node, ast.Call)
            and (
                (isinstance(node.func, ast.Name) and node.func.id == name)
                or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
            )
        ]
        assert lines, name
        return min(lines)

    backfill = _first_call_line("abackfill_character_uids")
    assert _first_call_line("bootstrap_local_cloudsave_environment") < backfill
    assert _first_call_line("_run_cloudsave_manager_action") < backfill
    # After initialize_character_data: on a fresh install it writes characters.json.
    assert _first_call_line("initialize_character_data") < backfill


# --- create / copy / import / rename / delete ------------------------------------


@pytest.mark.asyncio
async def test_new_character_gets_a_fresh_id_even_when_copied_from_another(monkeypatch):
    crud = importlib.import_module("main_routers.characters_router.crud")
    existing_uid = new_character_uid()
    saved = {}

    class _Config:
        async def aload_characters(self):
            return {"猫娘": {"Source": {"昵称": "Source", "_reserved": {"character_uid": existing_uid}}}}

    async def _save(_config, characters, *_names):
        saved.update(characters)
        return False

    monkeypatch.setattr(crud, "get_config_manager", lambda: _Config())
    monkeypatch.setattr(crud, "_get_new_catgirl_default_voice_id", lambda: "voice")
    monkeypatch.setattr(crud, "asave_characters_with_recent_activation", _save)
    monkeypatch.setattr(crud, "_mark_new_character_greeting_pending_safe", AsyncMock(return_value=(True, None)))
    monkeypatch.setattr(crud, "get_init_one_catgirl", AsyncMock)
    monkeypatch.setattr(crud, "notify_memory_server_reload", AsyncMock(return_value=True))

    # "Copying" a character re-submits its fields; a forged _reserved id is dropped.
    response = await crud.add_catgirl(_DummyRequest({
        "档案名": "Copy",
        "昵称": "Source",
        "_reserved": {"character_uid": existing_uid},
    }))

    assert response["success"] is True
    uid = get_character_uid(saved["猫娘"]["Copy"])
    assert is_valid_character_uid(uid)
    assert uid != existing_uid
    assert get_character_uid(saved["猫娘"]["Source"]) == existing_uid


_XOR_KEY = b"NEKOCHARA2024"


class _FakeUpload:
    def __init__(self, filename: str, payload: bytes):
        self.filename = filename
        self._buf = payload
        self._pos = 0

    async def read(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._buf) - self._pos
        chunk = self._buf[self._pos:self._pos + size]
        self._pos += len(chunk)
        return chunk


@pytest.mark.asyncio
async def test_imported_card_gets_a_new_id_and_drops_the_one_it_carries():
    # Mutation: keeping the card's id turns this red.
    from main_routers.characters_router import cards

    carried_uid = new_character_uid()
    character = {
        "档案名": "Imported",
        "昵称": "Imported",
        "_reserved": {"character_uid": carried_uid},
    }
    raw = json.dumps(character, ensure_ascii=False).encode("utf-8")
    payload = bytes(raw[i] ^ _XOR_KEY[i % len(_XOR_KEY)] for i in range(len(raw)))

    config_manager = MagicMock()
    config_manager.aload_characters = AsyncMock(return_value={"猫娘": {}})
    config_manager.asave_characters = AsyncMock()
    config_manager.ensure_card_faces_directory = MagicMock()
    config_manager.card_face_meta_path = MagicMock(return_value="unused-meta-path")

    with patch.object(cards, "get_config_manager", return_value=config_manager), \
         patch.object(cards, "get_initialize_character_data", return_value=None), \
         patch.object(
             cards,
             "_mark_new_character_greeting_pending_safe",
             new=AsyncMock(return_value=(True, None)),
         ), \
         patch.object(cards, "_write_card_meta", new=MagicMock()):
        response = await cards.import_character_card(
            zip_file=_FakeUpload("card.nekocfg", payload),
            card_image=None,
        )

    assert response.status_code == 200
    saved = config_manager.asave_characters.await_args.args[0]["猫娘"]["Imported"]
    uid = get_character_uid(saved)
    assert is_valid_character_uid(uid)
    assert uid != carried_uid


def _character_json_from_card(png: bytes) -> dict:
    import io
    import struct
    import zipfile

    assert png[1:4] == b"PNG"
    offset = 8
    while offset < len(png):
        (length,) = struct.unpack(">I", png[offset:offset + 4])
        chunk_type = png[offset + 4:offset + 8]
        data = png[offset + 8:offset + 8 + length]
        if chunk_type == b"neKo":
            with zipfile.ZipFile(io.BytesIO(data)) as zf:
                return json.loads(zf.read("character.json").decode("utf-8"))
        offset += 12 + length
    raise AssertionError("card has no neKo chunk")


def _export_config(tmp_path, uid):
    config_manager = MagicMock()
    config_manager.aload_characters = AsyncMock(return_value={"猫娘": {
        "Exported": {
            "昵称": "Exported",
            "_reserved": {"character_uid": uid, "avatar": {"model_type": "pngtuber"}},
        },
    }})
    config_manager.card_faces_dir = tmp_path
    # The export writes a sidecar meta file; keep it inside tmp_path.
    config_manager.card_face_meta_path = MagicMock(return_value=tmp_path / "card_meta.json")
    return config_manager


@pytest.mark.asyncio
async def test_card_export_does_not_carry_the_id(tmp_path):
    # Mutation: dropping the strip from the plain export turns this red.
    from main_routers.characters_router import cards

    uid = new_character_uid()
    with patch.object(cards, "get_config_manager", return_value=_export_config(tmp_path, uid)):
        response = await cards.export_catgirl_card("Exported")

    exported = _character_json_from_card(bytes(response.body))
    assert exported["昵称"] == "Exported"
    assert "character_uid" not in exported.get("_reserved", {})
    assert uid not in json.dumps(exported)


@pytest.mark.asyncio
async def test_portrait_card_export_does_not_carry_the_id(tmp_path):
    import io

    from PIL import Image

    from main_routers.characters_router import cards

    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color="#ffffff").save(buffer, format="PNG")
    uid = new_character_uid()
    with patch.object(cards, "get_config_manager", return_value=_export_config(tmp_path, uid)):
        response = await cards.export_catgirl_with_portrait(
            "Exported",
            portrait=_FakeUpload("portrait.png", buffer.getvalue()),
            include_model=False,
        )

    exported = _character_json_from_card(bytes(response.body))
    assert exported["昵称"] == "Exported"
    assert uid not in json.dumps(exported)


async def _noop(*_args, **_kwargs):
    return None


def _init_router_state(cm):
    init_shared_state(
        role_state={},
        steamworks=None,
        templates=None,
        config_manager=cm,
        initialize_character_data=_noop,
        switch_current_catgirl_fast=_noop,
        init_one_catgirl=_noop,
        remove_one_catgirl=_noop,
    )
    return importlib.reload(importlib.import_module("main_routers.characters_router.crud"))


@pytest.mark.asyncio
async def test_rename_keeps_the_id(tmp_path):
    # Mutation: issuing a new id on rename turns this red.
    cm, _path = _backfilled_manager(tmp_path, {"Current": {"昵称": "Current"}, "Old": {"昵称": "Old"}})
    uid = get_character_uid(cm.load_characters()["猫娘"]["Old"])
    assert is_valid_character_uid(uid)

    with patch("utils.config_manager._config_manager", cm):
        crud = _init_router_state(cm)
        with patch.object(crud, "release_memory_server_character", AsyncMock(return_value=True)), \
             patch.object(crud, "notify_memory_server_reload", AsyncMock(return_value=True)):
            response = await crud.rename_catgirl("Old", _DummyRequest({"new_name": "New"}))

    body = response if isinstance(response, dict) else json.loads(bytes(response.body))
    assert body.get("success") is True, body
    catgirls = cm.load_characters()["猫娘"]
    assert "Old" not in catgirls
    assert get_character_uid(catgirls["New"]) == uid


@pytest.mark.asyncio
async def test_deleting_a_character_leaves_no_id_behind(tmp_path):
    cm, path = _backfilled_manager(tmp_path, {"Current": {"昵称": "Current"}, "Gone": {"昵称": "Gone"}})
    loaded = cm.load_characters()["猫娘"]
    gone_uid = get_character_uid(loaded["Gone"])
    kept_uid = get_character_uid(loaded["Current"])

    with patch("utils.config_manager._config_manager", cm):
        crud = _init_router_state(cm)
        with patch.object(crud, "release_memory_server_character", AsyncMock(return_value=True)), \
             patch.object(crud, "notify_memory_server_reload", AsyncMock(return_value=True)):
            response = await crud.delete_catgirl("Gone")

    body = response if isinstance(response, dict) else json.loads(bytes(response.body))
    assert body.get("success") is True, body
    assert gone_uid not in path.read_text(encoding="utf-8")
    assert get_character_uid(cm.load_characters()["猫娘"]["Current"]) == kept_uid


@pytest.mark.parametrize(
    "broken",
    ['{"猫娘": {"Mine": {"昵称": "Mine"', '["not", "an", "object"]'],
    ids=["truncated-json", "non-object"],
)
def test_backfill_never_overwrites_an_unreadable_characters_file(tmp_path, broken):
    """A broken file must survive: load_characters would fall back to defaults.

    Mutation: backfilling from load_characters without checking the file
    first turns this red -- the defaults (with fresh ids) overwrite the
    user's profiles.
    """
    cm = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(cm)
    path = Path(cm.get_runtime_config_path("characters.json"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(broken, encoding="utf-8")
    before = path.read_bytes()

    assert cm.backfill_character_uids() is False

    assert path.read_bytes() == before


def test_backfill_writes_back_what_it_parsed_not_a_second_read(tmp_path, monkeypatch):
    """A second read that silently fell back to defaults must not be saved.

    Mutation: backfilling from load_characters() again turns this red.
    """
    cm = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(cm)
    path = _write_characters(cm, {"Mine": {"昵称": "Mine"}})
    monkeypatch.setattr(cm, "load_characters", lambda *a, **k: cm.get_default_characters())

    assert cm.backfill_character_uids() is True

    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert set(on_disk["猫娘"]) == {"Mine"}
    assert is_valid_character_uid(get_character_uid(on_disk["猫娘"]["Mine"]))
