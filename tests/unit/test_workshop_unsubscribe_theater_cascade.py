"""Steam Workshop unsubscribe must cascade theater data like an ordinary character delete."""

from __future__ import annotations

import asyncio
import hashlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.theater.numeric_v2_archive import NumericV2ArchiveStore, quarantined_file_name
from services.theater.numeric_v2_runtime import NumericV2Engine, NumericV2Runtime
from tests.unit.test_character_memory_regression import _DummyRequest, reload_module
from tests.unit.test_theater_numeric_v2_runtime import _binding, _branch_story, _opening

ITEM_ID = 4242


class _Config:
    def __init__(self, app_docs_dir: Path, characters: dict):
        self.app_docs_dir = str(app_docs_dir)
        self.characters = characters
        self.saved: list[dict] = []

    async def aload_characters(self):
        return json.loads(json.dumps(self.characters))

    async def asave_characters(self, characters):
        self.saved.append(characters)
        self.characters = characters


def _workshop_character(character_id: str) -> dict:
    return {
        "_reserved": {
            "character_id": character_id,
            "character_origin": {"source": "steam_workshop", "source_id": str(ITEM_ID)},
        },
    }


def _install_unsubscribe(monkeypatch, config, *, candidate: str):
    unsubscribe = reload_module("main_routers.workshop_router.unsubscribe")
    from main_routers import characters_router as characters_router_package
    import utils.character_memory as character_memory

    steam_calls: list[int] = []

    class _Workshop:
        def UnsubscribeItem(self, requested_item_id, *, callback, override_callback):
            steam_calls.append(requested_item_id)
            callback(SimpleNamespace(publishedFileId=requested_item_id, result=1))

        def GetItemState(self, _requested_item_id):
            return 0

    class _NoopThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(unsubscribe, "character_config_mutation_lock", asyncio.Lock())
    monkeypatch.setattr(unsubscribe, "get_config_manager", lambda: config)
    monkeypatch.setattr(
        unsubscribe, "get_steamworks", lambda: SimpleNamespace(Workshop=_Workshop()),
    )
    monkeypatch.setattr(
        unsubscribe, "_collect_character_names_by_workshop_item_id", lambda *_: [candidate],
    )
    monkeypatch.setattr(unsubscribe, "_resolve_workshop_item_install_path", lambda *_: None)
    monkeypatch.setattr(unsubscribe, "_scan_workshop_folder_character_names", lambda _p: [])
    monkeypatch.setattr(unsubscribe, "_write_deleted_character_tombstone", lambda *_: None)
    monkeypatch.setattr(
        unsubscribe,
        "threading",
        SimpleNamespace(Event=threading.Event, Lock=threading.Lock, Thread=_NoopThread),
    )
    monkeypatch.setattr(characters_router_package, "create_derived_task_claim_token", lambda: "t")
    monkeypatch.setattr(
        characters_router_package, "release_memory_server_character", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(
        characters_router_package, "notify_memory_server_reload", AsyncMock(return_value=True),
    )
    monkeypatch.setattr(character_memory, "begin_character_recent_transaction", lambda *_: None)
    monkeypatch.setattr(
        character_memory, "delete_character_memory_storage", lambda *_a, **_k: ([], None),
    )
    monkeypatch.setattr(character_memory, "finalize_character_recent_delete", lambda *_: None)
    monkeypatch.setattr(character_memory, "release_character_recent_transaction", lambda *_: None)
    import main_routers.shared_state as shared_state

    monkeypatch.setattr(shared_state, "get_remove_one_catgirl", lambda: None)
    return unsubscribe, steam_calls


async def _seed_theater(theater: Path, binding: dict):
    runtime = NumericV2Runtime(NumericV2Engine.from_mapping(_branch_story()), theater)
    stored = await runtime.start_session(
        session_id=f"runtime_{binding['catgirl_name']}",
        catgirl_binding=binding,
        opening_performance=_opening(),
    )
    archive_store = NumericV2ArchiveStore(theater)
    archive_store.create_or_get(stored.session)
    archive_store.write_public_archive(title="t", session=stored.session, ending=None)
    return runtime.store._path(stored.session.session_id), archive_store


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_unsubscribe_cascades_theater_data(tmp_path, monkeypatch):
    theater = tmp_path / "theater"
    binding = _binding()
    other_binding = {
        **binding,
        "character_id": "character_22222222222222222222222222222222",
        "catgirl_id": "catgirl:character_22222222222222222222222222222222",
        "catgirl_name": "Other",
    }
    session_path, archive_store = await _seed_theater(theater, binding)
    other_session_path, _ = await _seed_theater(theater, other_binding)
    quarantine = archive_store.public_archive_quarantine_root
    quarantine.mkdir(parents=True)
    own_quarantined = quarantine / quarantined_file_name("invalid", f"{hashlib.sha256(b'x').hexdigest()}.json")
    own_quarantined.write_text(
        json.dumps({"story_id": "s", "character_id": binding["character_id"]}), encoding="utf-8",
    )
    config = _Config(tmp_path, {
        "当前猫娘": "Other",
        "猫娘": {
            "Lan": _workshop_character(binding["character_id"]),
            "Other": {"_reserved": {"character_id": other_binding["character_id"]}},
        },
    })
    unsubscribe, steam_calls = _install_unsubscribe(monkeypatch, config, candidate="Lan")

    result = await unsubscribe._unsubscribe_workshop_item(
        _DummyRequest({"item_id": str(ITEM_ID)}), asyncio.Event(),
    )

    assert result["success"] is True, result
    assert result["cleanup_summary"]["cleaned_characters"] == ["Lan"]
    assert result["cleanup_summary"]["errors"] == []
    assert "Lan" not in config.characters["猫娘"]
    assert steam_calls == [ITEM_ID]
    assert not session_path.exists()
    assert archive_store.receipt_paths_for_scope(character_id=binding["character_id"]) == []
    assert archive_store.list_public_archives(character_id=binding["character_id"]) == []
    assert not own_quarantined.exists()
    # The other character's theater data is untouched.
    assert other_session_path.is_file()
    assert archive_store.list_public_archives(character_id=other_binding["character_id"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_unsubscribe_aborts_before_commit_when_theater_preflight_fails(
    tmp_path, monkeypatch,
):
    theater = tmp_path / "theater"
    binding = _binding()
    session_path, archive_store = await _seed_theater(theater, binding)
    corrupt_receipt = archive_store.root / ("theater_end_" + "0" * 40 + ".json")
    corrupt_receipt.write_text("{invalid", encoding="utf-8")
    config = _Config(tmp_path, {
        "当前猫娘": "",
        "猫娘": {"Lan": _workshop_character(binding["character_id"])},
    })
    unsubscribe, steam_calls = _install_unsubscribe(monkeypatch, config, candidate="Lan")

    response = await unsubscribe._unsubscribe_workshop_item(
        _DummyRequest({"item_id": str(ITEM_ID)}), asyncio.Event(),
    )

    assert response.status_code == 500
    payload = json.loads(response.body)
    assert payload["code"] == "LOCAL_CONFIG_CLEANUP_FAILED"
    assert [err["stage"] for err in payload["cleanup_summary"]["errors"]] == ["theater_preflight"]
    assert config.saved == []
    assert "Lan" in config.characters["猫娘"]
    assert steam_calls == []
    assert session_path.is_file()
    assert corrupt_receipt.read_text(encoding="utf-8") == "{invalid"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_unsubscribe_commits_nothing_when_one_candidate_preflight_fails(
    tmp_path, monkeypatch,
):
    from main_routers.characters_router import crud
    from services.theater.numeric_v2_store import NumericV2StoreError

    config = _Config(tmp_path, {
        "当前猫娘": "",
        "猫娘": {
            "Lan": _workshop_character("character_" + "1" * 32),
            "Mia": _workshop_character("character_" + "3" * 32),
        },
    })
    unsubscribe, steam_calls = _install_unsubscribe(monkeypatch, config, candidate="Lan")
    monkeypatch.setattr(
        unsubscribe, "_collect_character_names_by_workshop_item_id", lambda *_: ["Lan", "Mia"],
    )
    original_collect = crud.collect_numeric_v2_character_purge

    async def collect(root, *, character_id, legacy_catgirl_name, **kwargs):
        if legacy_catgirl_name == "Mia":
            raise NumericV2StoreError("numeric_session_read_failed")
        return await original_collect(
            root, character_id=character_id, legacy_catgirl_name=legacy_catgirl_name, **kwargs,
        )

    monkeypatch.setattr(crud, "collect_numeric_v2_character_purge", collect)

    response = await unsubscribe._unsubscribe_workshop_item(
        _DummyRequest({"item_id": str(ITEM_ID)}), asyncio.Event(),
    )

    assert response.status_code == 500
    assert config.saved == []
    assert set(config.characters["猫娘"]) == {"Lan", "Mia"}
    assert steam_calls == []


def _real_config_with_workshop_character(tmp_path: Path):
    """A real config manager holding workshop "Lan" and an unrelated "Other"."""
    from tests.unit.test_character_memory_regression import _make_config_manager
    from utils.cloudsave_runtime import bootstrap_local_cloudsave_environment

    cm = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(cm)
    binding = _binding()
    characters = cm.load_characters()
    characters["猫娘"] = {
        "Lan": _workshop_character(binding["character_id"]),
        "Other": {"_reserved": {"character_id": "character_" + "2" * 32}},
    }
    characters["当前猫娘"] = "Other"
    cm.save_characters(characters, bypass_write_fence=True)
    return cm, binding


def _corrupt_unrelated_public_archive(theater: Path) -> Path:
    # Ownership is only known after parsing, so this file blocks every strict scan.
    path = theater / "numeric_v2" / "public_archives" / f"{'e' * 64}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"{broken-json")
    return path


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_unsubscribe_repairs_an_unrelated_corrupt_theater_file_once(
    tmp_path, monkeypatch,
):
    """Like the ordinary delete, a corrupt file is quarantined and the preflight retried."""
    from main_routers.characters_router import crud
    from services.theater.paths import theater_root

    cm, binding = _real_config_with_workshop_character(tmp_path)
    theater = theater_root(cm)
    session_path, _ = await _seed_theater(theater, binding)
    corrupt = _corrupt_unrelated_public_archive(theater)
    unsubscribe, steam_calls = _install_unsubscribe(monkeypatch, cm, candidate="Lan")
    maintain = crud.maintain_numeric_v2_storage_once
    calls = []

    def counted(*args, **kwargs):
        calls.append(args[0])
        return maintain(*args, **kwargs)

    monkeypatch.setattr(crud, "maintain_numeric_v2_storage_once", counted)

    result = await unsubscribe._unsubscribe_workshop_item(
        _DummyRequest({"item_id": str(ITEM_ID)}), asyncio.Event(),
    )

    assert result["success"] is True, result
    assert result["cleanup_summary"]["errors"] == []
    assert result["cleanup_summary"]["cleaned_characters"] == ["Lan"]
    assert calls == [theater]
    assert steam_calls == [ITEM_ID]
    assert set(cm.load_characters()["猫娘"]) == {"Other"}
    assert not session_path.exists()
    # The repair quarantined the file; its owner is unknown, so the cascade then
    # erases that copy along with the deleted character's data.
    assert not corrupt.exists()
    assert not list((theater / "numeric_v2" / "quarantine_public_archives").glob("*.json"))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_unsubscribe_never_repairs_a_transient_theater_read_failure(
    tmp_path, monkeypatch,
):
    """An OSError may be transient: no repair, and the unsubscribe still fails closed."""
    from main_routers.characters_router import crud
    from services.theater.paths import theater_root

    cm, binding = _real_config_with_workshop_character(tmp_path)
    theater = theater_root(cm)
    session_path, _ = await _seed_theater(theater, binding)
    unsubscribe, steam_calls = _install_unsubscribe(monkeypatch, cm, candidate="Lan")
    maintain_calls = []
    monkeypatch.setattr(
        crud, "maintain_numeric_v2_storage_once", lambda *a, **k: maintain_calls.append(a),
    )

    def locked(*_args, **_kwargs):
        raise PermissionError("locked by antivirus")

    monkeypatch.setattr(crud, "list_numeric_v2_sessions", locked)

    response = await unsubscribe._unsubscribe_workshop_item(
        _DummyRequest({"item_id": str(ITEM_ID)}), asyncio.Event(),
    )

    assert response.status_code == 500
    payload = json.loads(response.body)
    assert [err["stage"] for err in payload["cleanup_summary"]["errors"]] == ["theater_preflight"]
    assert maintain_calls == []
    assert steam_calls == []
    assert set(cm.load_characters()["猫娘"]) == {"Lan", "Other"}
    assert session_path.is_file()


def _purge_intents(theater: Path) -> list[Path]:
    return sorted((theater / "numeric_v2" / "purge_intents").glob("purge_*.json"))


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_unsubscribe_purge_failure_is_retried_by_startup_maintenance(
    tmp_path, monkeypatch,
):
    """characters.json is committed first; a failed purge must not orphan transcripts."""
    from main_routers.characters_router import crud
    from services.theater import numeric_v2_maintenance

    theater = tmp_path / "theater"
    binding = _binding()
    other_binding = {
        **binding,
        "character_id": "character_22222222222222222222222222222222",
        "catgirl_id": "catgirl:character_22222222222222222222222222222222",
        "catgirl_name": "Other",
    }
    session_path, archive_store = await _seed_theater(theater, binding)
    other_session_path, _ = await _seed_theater(theater, other_binding)
    config = _Config(tmp_path, {
        "当前猫娘": "Other",
        "猫娘": {
            "Lan": _workshop_character(binding["character_id"]),
            "Other": {"_reserved": {"character_id": other_binding["character_id"]}},
        },
    })
    unsubscribe, steam_calls = _install_unsubscribe(monkeypatch, config, candidate="Lan")

    async def failing_purge(_purge):
        raise OSError("disk unavailable")

    monkeypatch.setattr(crud, "purge_numeric_v2_character_data", failing_purge)

    result = await unsubscribe._unsubscribe_workshop_item(
        _DummyRequest({"item_id": str(ITEM_ID)}), asyncio.Event(),
    )

    assert result["success"] is True, result
    assert [err["stage"] for err in result["cleanup_summary"]["errors"]] == ["delete_theater"]
    assert "Lan" not in config.characters["猫娘"]
    assert steam_calls == [ITEM_ID]
    # The transcripts are still on disk, but a durable intent lists them.
    assert session_path.is_file()
    assert archive_store.list_public_archives(character_id=binding["character_id"])
    assert len(_purge_intents(theater)) == 1

    # Next process start: maintenance retries the committed purge from its intent.
    recovered = numeric_v2_maintenance.recover_character_purge_intents(
        theater, {"Other": other_binding["character_id"]},
    )

    assert recovered == {"purge_intents_applied": 1, "purge_intents_discarded": 0}
    assert _purge_intents(theater) == []
    assert not session_path.exists()
    assert archive_store.list_public_archives(character_id=binding["character_id"]) == []
    assert archive_store.receipt_paths_for_scope(character_id=binding["character_id"]) == []
    # Nothing outside the intent is touched.
    assert other_session_path.is_file()
    assert archive_store.list_public_archives(character_id=other_binding["character_id"])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_unsubscribe_drops_the_intent_once_the_purge_succeeds(tmp_path, monkeypatch):
    theater = tmp_path / "theater"
    binding = _binding()
    session_path, _ = await _seed_theater(theater, binding)
    config = _Config(tmp_path, {
        "当前猫娘": "",
        "猫娘": {"Lan": _workshop_character(binding["character_id"])},
    })
    unsubscribe, _ = _install_unsubscribe(monkeypatch, config, candidate="Lan")

    result = await unsubscribe._unsubscribe_workshop_item(
        _DummyRequest({"item_id": str(ITEM_ID)}), asyncio.Event(),
    )

    assert result["success"] is True, result
    assert not session_path.exists()
    assert _purge_intents(theater) == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_unsubscribe_aborts_before_commit_when_purge_intent_cannot_be_written(
    tmp_path, monkeypatch,
):
    from main_routers.characters_router import crud

    theater = tmp_path / "theater"
    binding = _binding()
    session_path, _ = await _seed_theater(theater, binding)
    config = _Config(tmp_path, {
        "当前猫娘": "",
        "猫娘": {"Lan": _workshop_character(binding["character_id"])},
    })
    unsubscribe, steam_calls = _install_unsubscribe(monkeypatch, config, candidate="Lan")

    def failing_write(*_args, **_kwargs):
        raise OSError("read-only theater root")

    monkeypatch.setattr(crud, "write_character_purge_intent", failing_write)

    response = await unsubscribe._unsubscribe_workshop_item(
        _DummyRequest({"item_id": str(ITEM_ID)}), asyncio.Event(),
    )

    assert response.status_code == 500
    payload = json.loads(response.body)
    assert [err["stage"] for err in payload["cleanup_summary"]["errors"]] == ["theater_preflight"]
    assert config.saved == []
    assert "Lan" in config.characters["猫娘"]
    assert steam_calls == []
    assert session_path.is_file()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_unsubscribe_discards_the_intent_when_the_commit_fails(tmp_path, monkeypatch):
    """A character that stays configured must never be purged later from a stale intent."""
    theater = tmp_path / "theater"
    binding = _binding()
    session_path, _ = await _seed_theater(theater, binding)
    config = _Config(tmp_path, {
        "当前猫娘": "",
        "猫娘": {"Lan": _workshop_character(binding["character_id"])},
    })
    unsubscribe, steam_calls = _install_unsubscribe(monkeypatch, config, candidate="Lan")

    async def failing_save(_characters):
        raise OSError("characters.json not writable")

    config.asave_characters = failing_save

    response = await unsubscribe._unsubscribe_workshop_item(
        _DummyRequest({"item_id": str(ITEM_ID)}), asyncio.Event(),
    )

    assert response.status_code == 500
    assert steam_calls == []
    assert session_path.is_file()
    assert _purge_intents(theater) == []


async def test_workshop_unsubscribe_refuses_a_character_held_by_a_route(tmp_path, monkeypatch):
    # 与改名 / 删除同一个守卫（OD-13）：串门 / 小游戏在用、或串门后台还在按它的名字写数据时不删它
    from utils import external_route_registry as registry

    config = _Config(tmp_path, {
        "当前猫娘": "Other",
        "猫娘": {
            "Lan": _workshop_character("character_11111111111111111111111111111111"),
            "Other": {"_reserved": {"character_id": "character_22222222222222222222222222222222"}},
        },
    })
    unsubscribe, steam_calls = _install_unsubscribe(monkeypatch, config, candidate="Lan")
    monkeypatch.setattr(registry, "is_character_lifecycle_locked", lambda name: name == "Lan")
    before = json.loads(json.dumps(config.characters))

    result = await unsubscribe._unsubscribe_workshop_item(
        _DummyRequest({"item_id": str(ITEM_ID)}), asyncio.Event(),
    )

    body = json.loads(bytes(result.body)) if hasattr(result, "body") else result
    assert body["success"] is False and body["code"] == "EXTERNAL_ROUTE_ACTIVE"
    assert body["character_name"] == "Lan"
    assert steam_calls == [] and config.saved == [] and config.characters == before
    assert registry._mutating_characters == set()


async def test_workshop_unsubscribe_holds_its_characters_until_the_config_is_written(tmp_path, monkeypatch):
    # 检查通过之后到写完 characters.json：这些角色不能再开串门 / 小游戏（与改名 / 删除同一做法）
    from utils import external_route_registry as registry

    config = _Config(tmp_path, {
        "当前猫娘": "Other",
        "猫娘": {
            "Lan": _workshop_character("character_11111111111111111111111111111111"),
            "Other": {"_reserved": {"character_id": "character_22222222222222222222222222222222"}},
        },
    })
    unsubscribe, _steam_calls = _install_unsubscribe(monkeypatch, config, candidate="Lan")
    seen: list[bool] = []
    real_save = config.asave_characters

    async def save(characters):
        seen.append(registry.is_external_route_locked("Lan"))
        await real_save(characters)

    config.asave_characters = save
    result = await unsubscribe._unsubscribe_workshop_item(
        _DummyRequest({"item_id": str(ITEM_ID)}), asyncio.Event(),
    )
    assert result["success"] is True, result
    assert seen and all(seen)
    assert not registry.is_external_route_locked("Lan")
