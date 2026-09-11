import asyncio
import ast
import importlib
import inspect
import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from PIL import Image

from main_routers.shared_state import init_shared_state


@contextmanager
def _isolated_sidecar_stores(memory_dir):
    """Swap the three sidecar singletons for FRESH instances.

    Saving the module globals and restoring them is not enough. These tests
    mutate ``_cache`` and ``_retired`` on the EXISTING objects, and putting the
    same reference back leaves those mutations in place -- so an entry another
    test also uses is silently dropped and the suite becomes order-dependent.
    """
    import memory.anti_repeat as anti_repeat_module
    import memory.anti_repeat_effects as effects_module
    import memory.startup_greeting_history as greeting_module

    config_manager = SimpleNamespace(memory_dir=str(memory_dir))
    store = effects_module.AntiRepeatEffectStore()
    store._config_manager = config_manager
    corpus = anti_repeat_module.AntiRepeatCorpus()
    corpus._config_manager = config_manager
    greeting = greeting_module.StartupGreetingHistory(config_manager)

    previous = (
        effects_module._GLOBAL_STORE,
        anti_repeat_module._GLOBAL_CORPUS,
        greeting_module._GLOBAL_HISTORY,
    )
    effects_module._GLOBAL_STORE = store
    anti_repeat_module._GLOBAL_CORPUS = corpus
    greeting_module._GLOBAL_HISTORY = greeting
    try:
        yield (store, corpus, greeting)
    finally:
        (
            effects_module._GLOBAL_STORE,
            anti_repeat_module._GLOBAL_CORPUS,
            greeting_module._GLOBAL_HISTORY,
        ) = previous

def _make_role_state_for_test(session_managers: dict) -> dict:
    """Seed role_state with pre-existing session_managers (post-#855 + cross_server async).

    The legacy 6-dict layout (sync_message_queue / sync_shutdown_event /
    session_manager / session_id / sync_process / websocket_locks) was
    consolidated into RoleState on main. ``sync_shutdown_event`` /
    ``sync_process`` were further removed when cross_server moved from
    daemon thread to a main-loop ``asyncio.Task`` (now ``sync_task``).
    Tests that only care about seeding session_manager construct stub
    RoleState entries with live Queue / asyncio.Lock so adapters don't
    crash on attribute access.
    """
    # Import lazily to avoid circular import at module load time
    from app.main_server import RoleState, _SyncMessageQueue
    return {
        name: RoleState(
            sync_message_queue=_SyncMessageQueue(),
            websocket_lock=asyncio.Lock(),
            session_manager=session_manager,
        )
        for name, session_manager in session_managers.items()
    }
from utils.config_manager import ConfigManager
from utils.cloudsave_runtime import (
    CLOUDSAVE_DISABLED_ENV,
    MaintenanceModeError,
    ROOT_MODE_BOOTSTRAP_IMPORTING,
    bootstrap_local_cloudsave_environment,
)


def _make_config_manager(tmp_root: Path):
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


def reload_module(module_name: str):
    module = importlib.import_module(module_name)
    return importlib.reload(module)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cancelled_thread_call_returns_retained_lock_transaction():
    """Cancellation must wait for the worker and preserve its cleanup token."""
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        characters_router_module = reload_module("main_routers.characters_router.crud")
        from utils.character_memory import (
            begin_character_recent_transaction,
            release_character_recent_transaction,
        )

        worker_entered = threading.Event()
        release_worker = threading.Event()

        def _acquire_then_block():
            transaction = begin_character_recent_transaction(cm, "Old", "New")
            worker_entered.set()
            assert release_worker.wait(3)
            return transaction

        operation = asyncio.create_task(
            characters_router_module._await_thread_call_to_completion(
                _acquire_then_block,
            )
        )
        assert await asyncio.to_thread(worker_entered.wait, 3)
        operation.cancel()
        await asyncio.sleep(0.05)
        assert not operation.done()

        release_worker.set()
        transaction, cancelled = await operation
        assert cancelled is True
        locks = list(transaction["held_locks"])
        assert locks and all(lock.locked() for lock in locks)
        release_character_recent_transaction(transaction)
        assert all(not lock.locked() for lock in locks)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rename_cancellation_during_release_runs_admission_rollback(tmp_path):
    """Cancellation in the release response window must restore old admission."""
    cm = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(cm)

    async def _noop(*_args, **_kwargs):
        return None

    with patch("utils.config_manager._config_manager", cm):
        init_shared_state(
            role_state={},
            steamworks=None,
            templates=None,
            config_manager=cm,
            logger=None,
            initialize_character_data=_noop,
            switch_current_catgirl_fast=_noop,
            init_one_catgirl=_noop,
            remove_one_catgirl=_noop,
        )
        crud = reload_module("main_routers.characters_router.crud")
        characters = cm.load_characters()
        characters.setdefault("猫娘", {})["Old"] = {"昵称": "Old"}
        cm.save_characters(characters, bypass_write_fence=True)
        release_started = asyncio.Event()
        finish_release = asyncio.Event()
        rollback = AsyncMock(return_value="")

        async def _release(*_args, **_kwargs):
            release_started.set()
            await finish_release.wait()
            return True

        with patch.object(
            crud, "release_memory_server_character", side_effect=_release,
        ), patch.object(crud, "_rollback_character_operation", rollback):
            operation = asyncio.create_task(
                crud.rename_catgirl("Old", _DummyRequest({"new_name": "New"}))
            )
            await asyncio.wait_for(release_started.wait(), timeout=3)
            operation.cancel()
            await asyncio.sleep(0.05)
            assert not operation.done()
            finish_release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation

        claims = rollback.await_args.kwargs["release_derived_task_claims"]
        assert set(claims) == {"Old"}
        assert len(claims["Old"]) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_cancellation_during_release_runs_admission_rollback(tmp_path):
    """Delete must also compensate a completed release before cancellation."""
    cm = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(cm)

    async def _noop(*_args, **_kwargs):
        return None

    with patch("utils.config_manager._config_manager", cm):
        init_shared_state(
            role_state={},
            steamworks=None,
            templates=None,
            config_manager=cm,
            logger=None,
            initialize_character_data=_noop,
            switch_current_catgirl_fast=_noop,
            init_one_catgirl=_noop,
            remove_one_catgirl=_noop,
        )
        crud = reload_module("main_routers.characters_router.crud")
        characters = cm.load_characters()
        characters.setdefault("猫娘", {})["DeleteMe"] = {"昵称": "DeleteMe"}
        cm.save_characters(characters, bypass_write_fence=True)
        release_started = asyncio.Event()
        finish_release = asyncio.Event()
        rollback = AsyncMock(return_value="")

        async def _release(*_args, **_kwargs):
            release_started.set()
            await finish_release.wait()
            return True

        with patch.object(
            crud, "release_memory_server_character", side_effect=_release,
        ), patch.object(crud, "_rollback_character_operation", rollback):
            operation = asyncio.create_task(crud.delete_catgirl("DeleteMe"))
            await asyncio.wait_for(release_started.wait(), timeout=3)
            operation.cancel()
            await asyncio.sleep(0.05)
            assert not operation.done()
            finish_release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation

        claims = rollback.await_args.kwargs["release_derived_task_claims"]
        assert set(claims) == {"DeleteMe"}
        assert len(claims["DeleteMe"]) == 1


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("operation_kind", ["rename", "delete"])
async def test_release_false_compensation_preserves_cancellation(tmp_path, operation_kind):
    """Cancellation during release compensation must propagate after cleanup."""
    cm = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(cm)

    async def _noop(*_args, **_kwargs):
        return None

    name = "Old" if operation_kind == "rename" else "DeleteMe"
    with patch("utils.config_manager._config_manager", cm):
        init_shared_state(
            role_state={},
            steamworks=None,
            templates=None,
            config_manager=cm,
            logger=None,
            initialize_character_data=_noop,
            switch_current_catgirl_fast=_noop,
            init_one_catgirl=_noop,
            remove_one_catgirl=_noop,
        )
        crud = reload_module("main_routers.characters_router.crud")
        characters = cm.load_characters()
        characters.setdefault("猫娘", {})[name] = {"昵称": name}
        cm.save_characters(characters, bypass_write_fence=True)
        compensation_started = asyncio.Event()
        finish_compensation = asyncio.Event()
        rollback = AsyncMock(return_value="")

        async def _compensate(*_args, **_kwargs):
            compensation_started.set()
            await finish_compensation.wait()
            return ""

        with patch.object(
            crud,
            "release_memory_server_character",
            AsyncMock(return_value=False),
        ), patch.object(
            crud,
            "_resume_released_character_admission",
            side_effect=_compensate,
        ), patch.object(crud, "_rollback_character_operation", rollback):
            if operation_kind == "rename":
                operation = asyncio.create_task(
                    crud.rename_catgirl(name, _DummyRequest({"new_name": "New"}))
                )
            else:
                operation = asyncio.create_task(crud.delete_catgirl(name))

            await asyncio.wait_for(compensation_started.wait(), timeout=3)
            operation.cancel()
            await asyncio.sleep(0.05)
            assert not operation.done()
            finish_compensation.set()
            with pytest.raises(asyncio.CancelledError):
                await operation

        claims = rollback.await_args.kwargs["release_derived_task_claims"]
        assert set(claims) == {name}
        assert len(claims[name]) == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rename_backup_setup_failure_happens_before_release(tmp_path):
    """Backup setup errors must occur before derived-task admission is held."""
    cm = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(cm)

    async def _noop(*_args, **_kwargs):
        return None

    with patch("utils.config_manager._config_manager", cm):
        init_shared_state(
            role_state={},
            steamworks=None,
            templates=None,
            config_manager=cm,
            logger=None,
            initialize_character_data=_noop,
            switch_current_catgirl_fast=_noop,
            init_one_catgirl=_noop,
            remove_one_catgirl=_noop,
        )
        crud = reload_module("main_routers.characters_router.crud")
        characters = cm.load_characters()
        characters.setdefault("猫娘", {})["Old"] = {"昵称": "Old"}
        cm.save_characters(characters, bypass_write_fence=True)
        release_memory = AsyncMock(return_value=True)

        with patch.object(
            crud, "_create_character_operation_backup_dir", side_effect=OSError("disk full"),
        ), patch.object(
            crud, "release_memory_server_character", release_memory,
        ), pytest.raises(OSError, match="disk full"):
            await crud.rename_catgirl(
                "Old", _DummyRequest({"new_name": "New"}),
            )

        release_memory.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rename_cancellation_waits_for_config_worker_before_rollback(tmp_path):
    """A late config publish must not overwrite the cancellation rollback."""
    cm = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(cm)

    async def _noop(*args, **kwargs):
        return None

    with patch("utils.config_manager._config_manager", cm):
        init_shared_state(
            role_state={},
            steamworks=None,
            templates=None,
            config_manager=cm,
            logger=None,
            initialize_character_data=_noop,
            switch_current_catgirl_fast=_noop,
            init_one_catgirl=_noop,
            remove_one_catgirl=_noop,
        )
        crud = reload_module("main_routers.characters_router.crud")
        characters = cm.load_characters()
        characters.setdefault("猫娘", {})["Old"] = {"昵称": "Old"}
        cm.save_characters(characters, bypass_write_fence=True)
        old_recent = Path(cm.memory_dir) / "Old" / "recent.json"
        old_recent.parent.mkdir(parents=True, exist_ok=True)
        old_recent.write_text("[]", encoding="utf-8")

        entered = threading.Event()
        release = threading.Event()
        original_save = cm.save_characters

        def _delayed_save(data, character_json_path=None, *, bypass_write_fence=False):
            if not bypass_write_fence and "New" in (data.get("猫娘") or {}):
                entered.set()
                assert release.wait(3)
            return original_save(
                data,
                character_json_path=character_json_path,
                bypass_write_fence=bypass_write_fence,
            )

        with (
            patch.object(crud, "release_memory_server_character", AsyncMock(return_value=True)),
            patch.object(crud, "notify_memory_server_reload", AsyncMock(return_value=True)),
            patch.object(cm, "save_characters", side_effect=_delayed_save),
        ):
            operation = asyncio.create_task(
                crud.rename_catgirl("Old", _DummyRequest({"new_name": "New"}))
            )
            assert await asyncio.to_thread(entered.wait, 3)
            operation.cancel()
            await asyncio.sleep(0.05)
            assert not operation.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation

        saved = cm.load_characters()
        assert "Old" in saved.get("猫娘", {})
        assert "New" not in saved.get("猫娘", {})
        assert old_recent.is_file()
        assert not (Path(cm.memory_dir) / "New").exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_cancellation_waits_for_config_worker_before_rollback(tmp_path):
    """A late delete publish must not overwrite the cancellation rollback."""
    cm = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(cm)

    async def _noop(*args, **kwargs):
        return None

    with patch("utils.config_manager._config_manager", cm):
        init_shared_state(
            role_state={},
            steamworks=None,
            templates=None,
            config_manager=cm,
            logger=None,
            initialize_character_data=_noop,
            switch_current_catgirl_fast=_noop,
            init_one_catgirl=_noop,
            remove_one_catgirl=_noop,
        )
        crud = reload_module("main_routers.characters_router.crud")
        characters = cm.load_characters()
        characters.setdefault("猫娘", {})["DeleteMe"] = {"昵称": "DeleteMe"}
        cm.save_characters(characters, bypass_write_fence=True)
        old_recent = Path(cm.memory_dir) / "DeleteMe" / "recent.json"
        old_recent.parent.mkdir(parents=True, exist_ok=True)
        old_recent.write_text("[]", encoding="utf-8")

        entered = threading.Event()
        release = threading.Event()
        original_save = cm.save_characters

        def _delayed_save(data, character_json_path=None, *, bypass_write_fence=False):
            if not bypass_write_fence and "DeleteMe" not in (data.get("猫娘") or {}):
                entered.set()
                assert release.wait(3)
            return original_save(
                data,
                character_json_path=character_json_path,
                bypass_write_fence=bypass_write_fence,
            )

        with (
            patch.object(crud, "release_memory_server_character", AsyncMock(return_value=True)),
            patch.object(crud, "notify_memory_server_reload", AsyncMock(return_value=True)),
            patch.object(crud, "is_cloudsave_disabled", return_value=True),
            patch.object(cm, "save_characters", side_effect=_delayed_save),
        ):
            operation = asyncio.create_task(crud.delete_catgirl("DeleteMe"))
            assert await asyncio.to_thread(entered.wait, 3)
            operation.cancel()
            await asyncio.sleep(0.05)
            assert not operation.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation

        assert "DeleteMe" in cm.load_characters().get("猫娘", {})
        assert old_recent.is_file()


class _DummyRequest:
    def __init__(self, payload):
        self._payload = payload

    async def json(self):
        return self._payload


@pytest.mark.unit
@pytest.mark.asyncio
async def test_add_catgirl_activates_reused_recent_name_before_config_publish(
    tmp_path, monkeypatch,
):
    """A reused recent identity is active before config publication and rolls back on failure."""
    from utils import recent_file
    from utils.character_memory import list_character_recent_paths

    reused_name = "Reused"
    former_target = tmp_path / "Former" / "recent.json"

    class _Config:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

        async def aload_characters(self):
            return {"猫娘": {}}

        async def asave_characters(self, _characters):
            for path in list_character_recent_paths(self, reused_name):
                key = recent_file._lock_key(path)
                with recent_file._LOCKS_GUARD:
                    assert recent_file._resolve_key_unlocked(key) == key
            raise RuntimeError("simulated config publish failure")

    config = _Config()
    reused_paths = list_character_recent_paths(config, reused_name)
    recent_file.redirect_recent_paths(reused_paths, former_target)
    characters_router_module = reload_module("main_routers.characters_router.crud")
    monkeypatch.setattr(characters_router_module, "get_config_manager", lambda: config)
    monkeypatch.setattr(
        characters_router_module, "_get_new_catgirl_default_voice_id", lambda: "voice",
    )

    with pytest.raises(RuntimeError, match="simulated config publish failure"):
        await characters_router_module.add_catgirl(_DummyRequest({"档案名": reused_name}))

    with recent_file._LOCKS_GUARD:
        target_key = recent_file._lock_key(former_target)
        assert all(
            recent_file._resolve_key_unlocked(recent_file._lock_key(path)) == target_key
            for path in reused_paths
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_publish_cancellation_waits_for_save_and_keeps_activation(
    tmp_path,
):
    """Cancellation cannot roll identity back after the config publish succeeds."""
    from utils import recent_file
    from utils.character_memory import (
        asave_characters_with_recent_activation,
        list_character_recent_paths,
    )

    entered = asyncio.Event()
    release = asyncio.Event()

    class _Config:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

        async def asave_characters(self, _characters):
            entered.set()
            await release.wait()

    config = _Config()
    names = ("First", "Second")
    for name in names:
        recent_file.redirect_recent_paths(
            list_character_recent_paths(config, name),
            tmp_path / f"Former-{name}" / "recent.json",
        )

    operation = asyncio.create_task(asave_characters_with_recent_activation(
        config, {"猫娘": {}}, *names,
    ))
    await entered.wait()
    operation.cancel()
    await asyncio.sleep(0.05)
    assert not operation.done()

    release.set()
    assert await operation is True

    with recent_file._LOCKS_GUARD:
        for name in names:
            for path in list_character_recent_paths(config, name):
                key = recent_file._lock_key(path)
                assert recent_file._resolve_key_unlocked(key) == key
        assert all(not lock.locked() for lock in recent_file._LOCKS.values())


@pytest.mark.unit
@pytest.mark.asyncio
async def test_add_cancellation_finishes_post_publish_initialization(tmp_path):
    """A committed add must initialize runtime services before cancellation escapes."""
    cm = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(cm)
    entered = threading.Event()
    release = threading.Event()
    initialized = asyncio.Event()
    original_save = cm.save_characters

    def _delayed_save(data, character_json_path=None, *, bypass_write_fence=False):
        if not bypass_write_fence and "Deferred" in (data.get("猫娘") or {}):
            entered.set()
            assert release.wait(3)
        return original_save(
            data,
            character_json_path=character_json_path,
            bypass_write_fence=bypass_write_fence,
        )

    async def _init_one(name, *, is_new=False):
        assert name == "Deferred"
        assert is_new is True
        initialized.set()

    async def _noop(*args, **kwargs):
        return None

    with patch("utils.config_manager._config_manager", cm):
        init_shared_state(
            role_state={},
            steamworks=None,
            templates=None,
            config_manager=cm,
            logger=None,
            initialize_character_data=_noop,
            switch_current_catgirl_fast=_noop,
            init_one_catgirl=_init_one,
            remove_one_catgirl=_noop,
        )
        crud = reload_module("main_routers.characters_router.crud")
        reload_memory = AsyncMock(return_value=True)
        with (
            patch.object(cm, "save_characters", side_effect=_delayed_save),
            patch.object(
                crud,
                "_mark_new_character_greeting_pending_safe",
                AsyncMock(return_value=(True, "")),
            ),
            patch.object(crud, "notify_memory_server_reload", reload_memory),
            patch.object(crud, "_get_new_catgirl_default_voice_id", return_value="voice"),
        ):
            operation = asyncio.create_task(
                crud.add_catgirl(_DummyRequest({"档案名": "Deferred"}))
            )
            assert await asyncio.to_thread(entered.wait, 3)
            operation.cancel()
            await asyncio.sleep(0.05)
            assert not operation.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation

    assert initialized.is_set()
    reload_memory.assert_awaited_once()
    assert "Deferred" in cm.load_characters().get("猫娘", {})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_save_character_card_activates_reused_name_before_publish(
    tmp_path, monkeypatch,
):
    """Character-card creation publishes only after its reused identity is active."""
    from utils import recent_file
    from utils.character_memory import list_character_recent_paths

    reused_name = "Card-Reused"

    class _Config:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

        async def aload_characters(self):
            return {"猫娘": {}}

        async def asave_characters(self, _characters):
            for path in list_character_recent_paths(self, reused_name):
                key = recent_file._lock_key(path)
                with recent_file._LOCKS_GUARD:
                    assert recent_file._resolve_key_unlocked(key) == key

    config = _Config()
    recent_file.redirect_recent_paths(
        list_character_recent_paths(config, reused_name),
        tmp_path / "Former-Card" / "recent.json",
    )
    cards_module = reload_module("main_routers.characters_router.cards")
    monkeypatch.setattr(cards_module, "get_config_manager", lambda: config)
    monkeypatch.setattr(
        cards_module,
        "_mark_new_character_greeting_pending_safe",
        AsyncMock(return_value=(True, "")),
    )
    monkeypatch.setattr(
        cards_module,
        "_refresh_catgirl_context_after_profile_change",
        AsyncMock(return_value={"context_refreshed": True}),
    )
    reload_memory = AsyncMock(return_value=True)
    monkeypatch.setattr(
        cards_module,
        "notify_memory_server_reload",
        reload_memory,
    )

    result = await cards_module.save_character_card(_DummyRequest({
        "character_card_name": reused_name,
        "charaData": {"档案名": reused_name, "昵称": "Card"},
    }))

    assert result["success"] is True
    assert result["context_refreshed"] is True
    reload_memory.assert_awaited_once_with(
        reason=f"角色卡保存新角色: {reused_name}",
        resume_derived_task_names=(reused_name,),
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_rename_delegates_to_canonical_character_lifecycle(monkeypatch):
    """The compatibility route must reuse the canonical rename transaction."""
    memory_router = reload_module("main_routers.memory_router")

    class _Config:
        async def aload_characters(self):
            return {"猫娘": {"Old": {}}}

    monkeypatch.setattr(
        "utils.config_manager.get_config_manager", lambda: _Config(),
    )
    canonical_result = {"success": True, "memory_renamed": True}
    canonical_rename = AsyncMock(return_value=canonical_result)
    monkeypatch.setattr(
        "main_routers.characters_router.crud.rename_catgirl",
        canonical_rename,
    )
    request = _DummyRequest({"old_name": "Old", "new_name": "New"})

    result = await memory_router.update_catgirl_name(request)

    assert result is canonical_result
    canonical_rename.assert_awaited_once_with("Old", request)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_rename_is_idempotent_after_canonical_rename(monkeypatch):
    """Legacy follow-up calls should succeed after the canonical rename committed."""
    memory_router = reload_module("main_routers.memory_router")

    class _Config:
        async def aload_characters(self):
            return {"猫娘": {"New": {}}}

    monkeypatch.setattr(
        "utils.config_manager.get_config_manager", lambda: _Config(),
    )
    monkeypatch.setattr(
        memory_router,
        "character_memory_exists",
        lambda _config, name: name == "New",
    )
    canonical_rename = AsyncMock()
    monkeypatch.setattr(
        "main_routers.characters_router.crud.rename_catgirl",
        canonical_rename,
    )

    result = await memory_router.update_catgirl_name(
        _DummyRequest({"old_name": "Old", "new_name": "New"})
    )

    assert result == {
        "success": True,
        "changed": False,
        "exists_after": True,
        "already_renamed": True,
    }
    canonical_rename.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_rename_rejects_published_name_with_old_storage(monkeypatch):
    """An idempotent response must not hide a partially migrated storage tree."""
    memory_router = reload_module("main_routers.memory_router")

    class _Config:
        async def aload_characters(self):
            return {"猫娘": {"New": {}}}

    monkeypatch.setattr(
        "utils.config_manager.get_config_manager", lambda: _Config(),
    )
    monkeypatch.setattr(
        memory_router,
        "character_memory_exists",
        lambda _config, name: name == "Old",
    )
    canonical_rename = AsyncMock()
    monkeypatch.setattr(
        "main_routers.characters_router.crud.rename_catgirl",
        canonical_rename,
    )

    result = await memory_router.update_catgirl_name(
        _DummyRequest({"old_name": "Old", "new_name": "New"})
    )

    assert result.status_code == 409
    payload = json.loads(result.body.decode("utf-8"))
    assert payload["success"] is False
    canonical_rename.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_abort_releases_owned_claims_for_active_and_deleted_names():
    """Exact old tokens must be retired before a deleted name can be reused."""
    unsubscribe = reload_module("main_routers.workshop_router.unsubscribe")

    class _Config:
        async def aload_characters(self):
            return {"猫娘": {"StillHere": {}, "Unrelated": {}}}

    reload_memory = AsyncMock(return_value=True)
    with patch(
        "main_routers.characters_router.notify_memory_server_reload",
        reload_memory,
    ):
        resumed = await unsubscribe._resume_released_derived_tasks(
            _Config(), {"StillHere": "claim-a", "Deleted": "claim-b"}, 42,
        )

    assert resumed is True
    reload_memory.assert_awaited_once()
    assert reload_memory.await_args.kwargs["release_derived_task_claims"] == {
        "StillHere": ("claim-a",),
        "Deleted": ("claim-b",),
    }
    assert "仍存在=['StillHere']" in reload_memory.await_args.kwargs["reason"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_unsubscribe_revalidates_origin_after_config_lock_wait(
    monkeypatch,
):
    """A stale origin-index hit must not delete a same-name replacement."""
    unsubscribe = reload_module("main_routers.workshop_router.unsubscribe")
    from main_routers import characters_router as characters_router_package

    item_id = 42
    character_name = "ReusedName"
    target_character = {
        "_reserved": {
            "character_origin": {"source": "steam_workshop", "source_id": str(item_id)},
        },
    }
    local_replacement = {
        "_reserved": {
            "character_origin": {"source": "local", "source_id": ""},
        },
    }

    class _Config:
        def __init__(self):
            self.characters = {"当前猫娘": "Other", "猫娘": {
                character_name: target_character,
            }}
            self.saved = []

        async def aload_characters(self):
            return self.characters

        async def asave_characters(self, characters):
            self.saved.append(characters)
            self.characters = characters

    config = _Config()
    discovery_finished = threading.Event()
    mutation_lock = asyncio.Lock()
    monkeypatch.setattr(
        unsubscribe, "character_config_mutation_lock", mutation_lock,
    )

    def _discover(_config, discovered_item_id):
        assert discovered_item_id == item_id
        assert _config.characters["猫娘"][character_name] is target_character
        discovery_finished.set()
        return [character_name]

    unsubscribe_calls = []

    class _Workshop:
        def UnsubscribeItem(self, requested_item_id, *, callback, override_callback):
            assert override_callback is True
            unsubscribe_calls.append(requested_item_id)
            callback(SimpleNamespace(publishedFileId=requested_item_id, result=1))

        def GetItemState(self, _requested_item_id):
            return 0

    workshop = _Workshop()
    steamworks = SimpleNamespace(Workshop=workshop)
    release_character = AsyncMock(return_value=True)
    reload_memory = AsyncMock(return_value=True)

    class _NoopThread:
        def __init__(self, **_kwargs):
            pass

        def start(self):
            pass

    monkeypatch.setattr(unsubscribe, "get_config_manager", lambda: config)
    monkeypatch.setattr(unsubscribe, "get_steamworks", lambda: steamworks)
    monkeypatch.setattr(
        unsubscribe, "_collect_character_names_by_workshop_item_id", _discover,
    )
    monkeypatch.setattr(
        unsubscribe, "_resolve_workshop_item_install_path", lambda *_args: None,
    )
    monkeypatch.setattr(
        unsubscribe, "_scan_workshop_folder_character_names", lambda _path: [],
    )
    monkeypatch.setattr(
        unsubscribe,
        "threading",
        SimpleNamespace(Event=threading.Event, Lock=threading.Lock, Thread=_NoopThread),
    )
    monkeypatch.setattr(
        characters_router_package, "create_derived_task_claim_token",
        lambda: "unsubscribe-claim",
    )
    monkeypatch.setattr(
        characters_router_package, "release_memory_server_character", release_character,
    )
    monkeypatch.setattr(
        characters_router_package, "notify_memory_server_reload", reload_memory,
    )

    await mutation_lock.acquire()
    operation = asyncio.create_task(
        unsubscribe._unsubscribe_workshop_item(
            _DummyRequest({"item_id": str(item_id)}), asyncio.Event(),
        )
    )
    assert await asyncio.to_thread(discovery_finished.wait, 3)
    # Replace the discovered identity while unsubscribe waits for the config lock.
    config.characters = {
        "当前猫娘": "Other",
        "猫娘": {character_name: local_replacement},
    }
    mutation_lock.release()
    result = await operation

    assert result["success"] is True
    assert result["cleanup_summary"]["cleaned_characters"] == []
    assert result["cleanup_summary"]["skipped_unverified_characters"] == [character_name]
    assert config.characters["猫娘"] == {character_name: local_replacement}
    assert config.saved == []
    assert unsubscribe_calls == [item_id]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_abort_surfaces_derived_task_resume_failure():
    """A failed reload must not masquerade as a completed rollback."""
    unsubscribe = reload_module("main_routers.workshop_router.unsubscribe")

    class _Config:
        async def aload_characters(self):
            return {"猫娘": {"StillHere": {}}}

    with patch(
        "main_routers.characters_router.notify_memory_server_reload",
        AsyncMock(return_value=False),
    ), pytest.raises(RuntimeError, match="notify_memory_server_reload returned False"):
        await unsubscribe._resume_released_derived_tasks(
            _Config(), {"StillHere": "claim-a"}, 42,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_resume_exception_is_not_swallowed():
    """Transport exceptions must remain visible on a non-cancelled request."""
    unsubscribe = reload_module("main_routers.workshop_router.unsubscribe")

    class _Config:
        async def aload_characters(self):
            return {"猫娘": {"StillHere": {}}}

    with patch(
        "main_routers.characters_router.notify_memory_server_reload",
        AsyncMock(side_effect=OSError("reload unavailable")),
    ), pytest.raises(OSError, match="reload unavailable"):
        await unsubscribe._resume_released_derived_tasks(
            _Config(), {"StillHere": "claim-a"}, 42,
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_resume_failure_retries_owned_claim_until_acknowledged(
    monkeypatch,
):
    """A transient reload failure must retain and retry the owned claim token."""
    unsubscribe = reload_module("main_routers.workshop_router.unsubscribe")

    class _Config:
        async def aload_characters(self):
            return {"猫娘": {"StillHere": {}}}

    monkeypatch.setattr(
        unsubscribe,
        "_RESUME_RETRY_INITIAL_DELAY_SECONDS",
        0,
    )
    reload_memory = AsyncMock(side_effect=[False, True])
    with patch(
        "main_routers.characters_router.notify_memory_server_reload",
        reload_memory,
    ):
        with pytest.raises(
            RuntimeError,
            match="notify_memory_server_reload returned False",
        ):
            await unsubscribe._resume_released_derived_tasks(
                _Config(), {"StillHere": "claim-a"}, 42,
            )

        tasks = list(unsubscribe._released_derived_task_resume_tasks.values())
        await asyncio.gather(*tasks)

    assert reload_memory.await_count == 2
    for call in reload_memory.await_args_list:
        assert call.kwargs["release_derived_task_claims"] == {
            "StillHere": ("claim-a",),
        }
    assert unsubscribe._released_derived_task_resume_tasks == {}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_release_timeout_retains_claim_for_reconciliation():
    """An indeterminate HTTP timeout must keep the client-owned claim token."""
    unsubscribe = reload_module("main_routers.workshop_router.unsubscribe")
    release_started = asyncio.Event()
    received_tokens = {}

    async def _release(name, **kwargs):
        received_tokens[name] = kwargs["derived_task_claim_token"]
        release_started.set()
        await asyncio.Event().wait()

    claims = {}
    results = await unsubscribe._release_workshop_character_handles(
        ["StillHere"],
        42,
        _release,
        lambda: "claim-timeout",
        claims,
        per_call_timeout=0.01,
        overall_timeout=0.1,
    )

    assert release_started.is_set()
    assert claims == {"StillHere": "claim-timeout"}
    assert received_tokens == claims
    assert results[0][0:2] == ("StillHere", False)


@pytest.mark.unit
def test_workshop_recent_delete_finalizes_inside_each_iteration():
    """Workshop must not retain one character lock while acquiring the next."""
    unsubscribe = reload_module("main_routers.workshop_router.unsubscribe")
    tree = ast.parse(inspect.getsource(unsubscribe))
    delete_helpers = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_delete_memory_with_retry_sync"
    ]
    assert len(delete_helpers) == 1
    helper_calls = {
        node.func.id
        for node in ast.walk(delete_helpers[0])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }
    assert "finalize_character_recent_delete" in helper_calls
    assert "recent_delete_transactions" not in inspect.getsource(unsubscribe)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_thread_transaction_finishes_before_cancellation_returns():
    """Cancellation cannot abandon or outlive a thread-owned recent transaction."""
    unsubscribe = reload_module("main_routers.workshop_router.unsubscribe")
    started = threading.Event()
    finish = threading.Event()

    def _worker():
        started.set()
        assert finish.wait(timeout=2)
        return "done"

    operation = asyncio.create_task(
        unsubscribe._await_thread_call_to_completion(_worker)
    )
    while not started.is_set():
        await asyncio.sleep(0)
    operation.cancel()
    await asyncio.sleep(0)
    assert not operation.done()
    finish.set()

    result, cancelled = await operation
    assert result == "done"
    assert cancelled is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_thread_failure_cannot_replace_caller_cancellation():
    """A worker failure after cancellation remains diagnostic, not terminal."""
    unsubscribe = reload_module("main_routers.workshop_router.unsubscribe")
    started = threading.Event()
    finish = threading.Event()

    def _worker():
        started.set()
        assert finish.wait(timeout=2)
        raise RuntimeError("worker failed after cancellation")

    operation = asyncio.create_task(
        unsubscribe._await_thread_call_to_completion(_worker)
    )
    while not started.is_set():
        await asyncio.sleep(0)
    operation.cancel()
    finish.set()

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await operation
    assert isinstance(exc_info.value.__cause__, RuntimeError)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_resume_failure_cannot_replace_active_cancellation():
    """A failing finally recovery remains the cause of request cancellation."""
    unsubscribe = reload_module("main_routers.workshop_router.unsubscribe")
    started = asyncio.Event()
    entered = asyncio.Event()
    finish = asyncio.Event()

    async def _resume():
        entered.set()
        await finish.wait()
        raise RuntimeError("resume failed in finally")

    async def _operation():
        try:
            started.set()
            await asyncio.Event().wait()
        finally:
            await unsubscribe._finish_resume_preserving_cancellation(_resume())

    operation = asyncio.create_task(_operation())
    await started.wait()
    operation.cancel()
    await entered.wait()
    finish.set()

    with pytest.raises(asyncio.CancelledError) as exc_info:
        await operation
    assert isinstance(exc_info.value.__cause__, RuntimeError)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_memory_reload_failure_still_resumes_requested_admission():
    """Reload failure releases only the requested claim, preserving its peer."""
    from app.memory_server import review, runtime

    name = "ReloadFailureClaimOwner"
    await review.resume_character_derived_task_admission(name)
    await review.cancel_character_derived_tasks(
        name,
        hold_until_publication=True,
        claim_token="rename-claim",
    )
    await review.cancel_character_derived_tasks(
        name,
        claim_token="workshop-claim",
    )
    with patch.object(
        runtime,
        "CompressedRecentHistoryManager",
        side_effect=RuntimeError("reload failed"),
    ):
        reloaded = await runtime.reload_memory_components(
            release_derived_task_claims={name: {"workshop-claim"}},
        )

    assert reloaded is False
    assert review._derived_task_admission_claims[name] == {
        "rename-claim": (True, 0),
    }
    assert name in review._retired_derived_task_names
    assert name in review._publication_held_derived_task_names
    await review.resume_character_derived_task_admission(name)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_request_cancellation_survives_late_resume_failure(monkeypatch):
    """A late transaction error must not replace caller cancellation."""
    unsubscribe = reload_module("main_routers.workshop_router.unsubscribe")
    inner_started = asyncio.Event()
    finish_inner = asyncio.Event()

    async def _failing_operation(_request, commit_started):
        commit_started.set()
        inner_started.set()
        await finish_inner.wait()
        raise RuntimeError("resume failed")

    monkeypatch.setattr(unsubscribe, "_unsubscribe_workshop_item", _failing_operation)
    operation = asyncio.create_task(unsubscribe.unsubscribe_workshop_item(object()))
    await asyncio.wait_for(inner_started.wait(), timeout=3)
    operation.cancel()
    await asyncio.sleep(0.05)
    assert not operation.done()

    finish_inner.set()
    with pytest.raises(asyncio.CancelledError):
        await operation


class _DummyGetRequest:
    def __init__(self, query_params=None, headers=None):
        self.query_params = query_params or {}
        self.headers = headers or {}


class _FakeTranslationService:
    async def translate_dict(self, data, target_lang, fields_to_translate=None):
        result = dict(data)
        for field in fields_to_translate or []:
            value = result.get(field)
            if isinstance(value, str) and value:
                result[field] = f"{target_lang}:{value}"
        return result


@pytest.mark.unit
def test_character_router_profile_name_validation_maps_dot_error_codes():
    router_module = reload_module("main_routers.characters_router.crud")

    assert "点号" in router_module._validate_profile_name(".")
    assert "点号" in router_module._validate_profile_name("foo.")
    assert "路径分隔符" in router_module._validate_profile_name("..")
    assert "点号" in router_module._validate_profile_name("N.E.K.O")
    assert "保留" in router_module._validate_profile_name("api")

    assert router_module._validate_existing_character_path_name(".") is not None
    assert router_module._validate_existing_character_path_name("foo.") is not None
    assert router_module._validate_existing_character_path_name("..") is not None
    assert router_module._validate_existing_character_path_name("N.E.K.O") is None
    assert router_module._validate_existing_character_path_name("api") is None


@pytest.mark.unit
def test_profile_rename_event_prompt_i18n_is_complete_and_first_person():
    from config.prompts.prompts_memory import (
        PROFILE_RENAME_EVENT_FIELD,
        PROFILE_RENAME_EVENT_TEXT,
        render_profile_rename_event_context,
    )

    expected_langs = {"zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}
    assert set(PROFILE_RENAME_EVENT_FIELD) == expected_langs
    assert set(PROFILE_RENAME_EVENT_TEXT) == expected_langs

    zh_label, zh_text = render_profile_rename_event_context("zh-CN", "旧角色", "新角色")
    assert zh_label == "我的改名记录"
    assert "我曾用名" in zh_text
    assert "旧角色" in zh_text
    assert "新角色" in zh_text
    assert "只代表改名前的历史称呼" not in zh_text

    en_label, en_text = render_profile_rename_event_context("en", "Old", "New")
    assert en_label == "My Profile Rename Record"
    assert "formerly known as" in en_text
    assert "Old" in en_text
    assert "New" in en_text
    assert "historical name before the rename" not in en_text


@pytest.mark.unit
def test_profile_rename_event_master_is_person_neutral():
    """主人改名记录进的是猫娘 persona 的 master section，读者是猫娘、
    改名的是用户。第一人称会让猫娘误以为是自己改名，所以这里去掉人称、
    用中性陈述，既不能出现「我」也不带「你」。"""
    from config.prompts.prompts_memory import (
        PROFILE_RENAME_EVENT_FIELD_MASTER,
        PROFILE_RENAME_EVENT_TEXT_MASTER,
        render_profile_rename_event_context,
    )

    expected_langs = {"zh", "zh-TW", "en", "ja", "ko", "ru", "es", "pt"}
    assert set(PROFILE_RENAME_EVENT_FIELD_MASTER) == expected_langs
    assert set(PROFILE_RENAME_EVENT_TEXT_MASTER) == expected_langs

    zh_label, zh_text = render_profile_rename_event_context("zh-CN", "旧名", "新名", entity="master")
    assert zh_label == "改名记录"
    assert "旧名" in zh_text and "新名" in zh_text
    # 去人称：既无第一人称「我」也无第二人称「你」。
    assert "我" not in zh_text
    assert "你" not in zh_text

    en_label, en_text = render_profile_rename_event_context("en", "Old", "New", entity="master")
    assert en_label == "Profile Rename Record"
    assert "Old" in en_text and "New" in en_text
    assert "My " not in en_text and "Your " not in en_text

    # 缺省（neko）仍是第一人称，主人变体不影响默认行为。
    _, neko_text = render_profile_rename_event_context("zh-CN", "旧名", "新名")
    assert "我曾用名" in neko_text


@pytest.mark.unit
def test_master_effective_payload_rename_context_is_person_neutral(monkeypatch):
    monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: "zh-CN")
    from utils.config_manager import _build_effective_character_payload

    payload = {
        "档案名": "新主人名",
        "_reserved": {
            "ai_context": {
                "rename_events": [
                    {"type": "profile_rename", "old_name": "旧主人名", "new_name": "新主人名"},
                ]
            }
        },
    }
    effective = _build_effective_character_payload(payload, entity="master")
    context = effective["__ai_context.profile_rename_events"]
    assert "旧主人名" in context and "新主人名" in context
    assert "我" not in context
    assert "你" not in context


@pytest.mark.unit
def test_profile_rename_event_uses_collision_safe_synthetic_key(monkeypatch):
    monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: "zh-CN")
    from utils.config_manager import _build_effective_character_payload

    payload = {
        "档案名": "新角色",
        "我的改名记录": "用户自己写的字段",
        "_reserved": {
            "ai_context": {
                "rename_events": [
                    {
                        "type": "profile_rename",
                        "old_name": "旧角色",
                        "new_name": "临时角色",
                    },
                    {
                        "type": "profile_rename",
                        "old_name": "临时角色",
                        "new_name": "新角色",
                    }
                ]
            }
        },
    }
    effective = _build_effective_character_payload(payload)

    assert effective["我的改名记录"] == "用户自己写的字段"
    hidden_context = effective["__ai_context.profile_rename_events"]
    assert "我的改名记录" in hidden_context
    assert "我曾用名" in hidden_context
    assert "旧角色" in hidden_context
    assert "临时角色" in hidden_context
    assert "新角色" in hidden_context
    assert hidden_context.count("我的改名记录") == 1

    payload["__ai_context.profile_rename_events"] = "用户内部命名字段"
    effective_with_internal_collision = _build_effective_character_payload(payload)
    assert effective_with_internal_collision["__ai_context.profile_rename_events"] == "用户内部命名字段"
    collision_values = [
        value
        for key, value in effective_with_internal_collision.items()
        if key.startswith("__ai_context.profile_rename_events.")
    ]
    assert any("我曾用名" in str(value) for value in collision_values)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_management_and_recent_save_regression():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        # Simulate a crashed import run and verify bootstrap can recover on next start.
        root_state = cm.load_root_state()
        root_state["mode"] = ROOT_MODE_BOOTSTRAP_IMPORTING
        cm.save_root_state(root_state)
        bootstrap_local_cloudsave_environment(cm)
        assert cm.load_root_state()["mode"] == "normal"

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            memory_router_module = reload_module("main_routers.memory_router")
            initial_name = next(iter(cm.load_characters().get("猫娘", {}).keys()))

            fake_response = type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"status": "success"}},
            )()
            fake_client = AsyncMock()
            fake_client.__aenter__.return_value = fake_client
            fake_client.__aexit__.return_value = False
            fake_client.post.return_value = fake_response

            with patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client):
                add_result = await characters_router_module.add_catgirl(
                    _DummyRequest({"档案名": "测试角色"})
                )
            assert add_result["success"] is True
            assert "测试角色" in cm.load_characters().get("猫娘", {})

            switch_result = await characters_router_module.set_current_catgirl(
                _DummyRequest({"catgirl_name": "测试角色"})
            )
            assert switch_result["success"] is True
            assert cm.load_characters()["当前猫娘"] == "测试角色"

            from utils.recent_file import write_recent_payload

            recent_path = Path(cm.memory_dir) / "测试角色" / "recent.json"
            write_recent_payload(recent_path, [])
            recent_snapshot = await memory_router_module.get_recent_file(
                "recent_测试角色.json"
            )
            save_recent_result = await memory_router_module.save_recent_file(
                _DummyRequest(
                    {
                        "filename": "recent_测试角色.json",
                        "chat": [{"role": "user", "text": "你好"}],
                        "fingerprint": recent_snapshot["fingerprint"],
                        "identity_token": recent_snapshot["identity_token"],
                    }
                )
            )
            assert save_recent_result["success"] is True
            assert recent_path.is_file()

            switch_back_result = await characters_router_module.set_current_catgirl(
                _DummyRequest({"catgirl_name": initial_name})
            )
            assert switch_back_result["success"] is True
            assert cm.load_characters()["当前猫娘"] == initial_name

            with patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client):
                delete_result = await characters_router_module.delete_catgirl("测试角色")
            assert delete_result["success"] is True
            assert "测试角色" not in cm.load_characters().get("猫娘", {})
            assert not (Path(cm.memory_dir) / "测试角色").exists()
            tombstones = cm.load_character_tombstones_state().get("tombstones") or []
            assert any(entry.get("character_name") == "测试角色" for entry in tombstones)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_add_catgirl_rejects_unsafe_dot_profile_name():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            response = await characters_router_module.add_catgirl(_DummyRequest({"档案名": "."}))

            assert response.status_code == 400
            payload = json.loads(response.body.decode("utf-8"))
            assert payload["success"] is False
            assert "点号" in payload["error"]
            assert "." not in cm.load_characters().get("猫娘", {})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_body_delete_rejects_non_object_json_payload():
    characters_router_module = reload_module("main_routers.characters_router.crud")

    response = await characters_router_module.delete_catgirl_by_body(_DummyRequest(["."]))

    assert response.status_code == 400
    payload = json.loads(response.body.decode("utf-8"))
    assert payload["success"] is False
    assert "JSON" in payload["error"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_body_delete_rescues_unsafe_dot_character_without_touching_memory_paths():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters = cm.load_characters()
            characters.setdefault("猫娘", {})["正常角色"] = {"昵称": "正常角色"}
            characters.setdefault("猫娘", {})["."] = {"昵称": "坏角色"}
            characters["当前猫娘"] = "正常角色"
            cm.save_characters(characters, bypass_write_fence=True)

            sentinel = Path(cm.memory_dir) / "sentinel.txt"
            sentinel.parent.mkdir(parents=True, exist_ok=True)
            sentinel.write_text("keep", encoding="utf-8")

            characters_router_module = reload_module("main_routers.characters_router.crud")
            mock_notify_reload = AsyncMock(return_value=True)
            with (
                patch.object(characters_router_module, "notify_memory_server_reload", mock_notify_reload),
                patch.object(characters_router_module, "delete_character_memory_storage") as mock_delete_memory,
            ):
                result = await characters_router_module.delete_catgirl_by_body(_DummyRequest({"name": "."}))

            assert result["success"] is True
            assert result["unsafe_name_rescue"] is True
            assert result["memory_deleted"] is False
            mock_notify_reload.assert_awaited_once()
            assert "." not in cm.load_characters().get("猫娘", {})
            assert sentinel.read_text(encoding="utf-8") == "keep"
            mock_delete_memory.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe_name", [".", "a/b"])
async def test_unsafe_name_rescue_retires_the_sidecar_stores(unsafe_name):
    """A rescued deletion must end the identity, not just its configuration.

    Every other end-of-identity path retires the sidecar stores; this branch
    returned without doing so, and a snapshot staged while the removal was in
    flight then flushed afterwards -- writing ``anti_repeat_effects.json``
    straight into the memory ROOT for ".", and creating a phantom "a/b/" tree
    for a name carrying historical separators. ``facts_sync`` enumerates any
    directory under ``memory/`` as a character, so the artifact outlives the
    deletion.

    The assertion is on the DISK, deliberately, not on the retirement flag: for
    "." the flag can be set while the file still lands, because ``memory/.`` is
    the root and therefore already exists, so ``_write_file_path`` still returns
    a real path. What actually suppresses the write is the sequence fence, and
    only a disk assertion sees that.
    """
    from memory import anti_repeat_effects

    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters = cm.load_characters()
            characters.setdefault("猫娘", {})["正常角色"] = {"昵称": "正常角色"}
            characters.setdefault("猫娘", {})[unsafe_name] = {"昵称": "坏角色"}
            characters["当前猫娘"] = "正常角色"
            cm.save_characters(characters, bypass_write_fence=True)

            memory_root = Path(cm.memory_dir)
            memory_root.mkdir(parents=True, exist_ok=True)
            before = {path.name for path in memory_root.iterdir()}

            store = anti_repeat_effects.AntiRepeatEffectStore()
            store._config_manager = cm
            with patch.object(anti_repeat_effects, "_GLOBAL_STORE", store):
                # Staged while the removal is in flight, flushed after it lands.
                staged = store.stage_decision(
                    unsafe_name,
                    anti_repeat_effects.AntiRepeatDecision(
                        source="proactive",
                        reasons=("bm25",),
                        action="block",
                        outcome="blocked_initial",
                    ),
                )

                crud = reload_module("main_routers.characters_router.crud")
                with (
                    patch.object(
                        crud, "notify_memory_server_reload", AsyncMock(return_value=True)
                    ),
                    patch.object(crud, "delete_character_memory_storage"),
                ):
                    result = await crud.delete_catgirl_by_body(
                        _DummyRequest({"name": unsafe_name})
                    )

                assert result["success"] is True
                assert result["unsafe_name_rescue"] is True

                store._flush_snapshot(*staged)
                # A SECOND write after the deletion pins the fence rather than
                # the flag: a name whose directory happens to exist would
                # otherwise keep writing.
                store._flush_snapshot(
                    *store.stage_decision(
                        unsafe_name,
                        anti_repeat_effects.AntiRepeatDecision(
                            source="proactive",
                            reasons=("bm25",),
                            action="block",
                            outcome="blocked_initial",
                        ),
                    )
                )

            after = {path.name for path in memory_root.iterdir()}
            assert after == before, (
                f"the rescued deletion left {sorted(after - before)} under memory/"
            )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_read_endpoints_disable_caching():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")

            characters_response = await characters_router_module.get_characters(
                _DummyGetRequest(headers={"Accept-Language": "zh-CN"})
            )
            current_response = await characters_router_module.get_current_catgirl()

            assert characters_response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate, max-age=0"
            assert characters_response.headers["Pragma"] == "no-cache"
            assert current_response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate, max-age=0"
            assert current_response.headers["Pragma"] == "no-cache"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_characters_preserves_profile_names_when_translating_display_fields():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)
        characters = cm.load_characters()
        characters["主人"] = {"档案名": "主人原名", "昵称": "主人昵称"}
        characters["猫娘"] = {
            "猫娘原名": {
                "档案名": "猫娘原名",
                "昵称": "猫娘昵称",
                "性别": "女",
            }
        }
        characters["当前猫娘"] = "猫娘原名"
        cm.save_characters(characters)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm), patch(
            "utils.language_utils.get_translation_service",
            return_value=_FakeTranslationService(),
        ):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            response = await characters_router_module.get_characters(
                _DummyGetRequest(headers={"Accept-Language": "en-US"})
            )
            payload = json.loads(response.body.decode("utf-8"))

            assert payload["主人"]["档案名"] == "主人原名"
            assert payload["主人"]["昵称"] == "en:主人昵称"
            assert "猫娘原名" in payload["猫娘"]
            assert payload["猫娘"]["猫娘原名"]["档案名"] == "猫娘原名"
            assert payload["猫娘"]["猫娘原名"]["昵称"] == "en:猫娘昵称"
            assert payload["猫娘"]["猫娘原名"]["性别"] == "en:女"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rename_catgirl_moves_runtime_and_legacy_memory_storage(monkeypatch):
    monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: "zh-CN")
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            memory_router_module = reload_module("main_routers.memory_router")

            fake_response = type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"status": "success"}},
            )()
            fake_client = AsyncMock()
            fake_client.__aenter__.return_value = fake_client
            fake_client.__aexit__.return_value = False
            fake_client.post.return_value = fake_response

            with patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client):
                add_result = await characters_router_module.add_catgirl(
                    _DummyRequest({"档案名": "旧角色"})
                )
            assert add_result["success"] is True

            old_memory_dir = Path(cm.memory_dir) / "旧角色"
            old_memory_dir.mkdir(parents=True, exist_ok=True)
            (Path(cm.project_memory_dir)).mkdir(parents=True, exist_ok=True)

            (old_memory_dir / "persona.json").write_text('{"traits":["温柔"]}', encoding="utf-8")
            (old_memory_dir / "recent.json").write_text(
                json.dumps(
                    [
                        {
                            "speaker": "旧角色",
                            "data": {"content": "旧角色说：你好"},
                        }
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            (Path(cm.project_memory_dir) / "facts_旧角色.json").write_text(
                '[{"id":"fact-1","text":"旧记忆"}]',
                encoding="utf-8",
            )

            with patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client):
                rename_result = await characters_router_module.rename_catgirl(
                    "旧角色",
                    _DummyRequest({"new_name": "新角色"}),
                )

            assert rename_result["success"] is True
            assert rename_result["memory_renamed"] is True
            saved_characters = cm.load_characters()
            assert "新角色" in saved_characters.get("猫娘", {})
            assert "旧角色" not in saved_characters.get("猫娘", {})
            saved_profile = saved_characters["猫娘"]["新角色"]
            assert "我的改名记录" not in saved_profile
            rename_events = saved_profile["_reserved"]["ai_context"]["rename_events"]
            assert rename_events[-1]["old_name"] == "旧角色"
            assert rename_events[-1]["new_name"] == "新角色"
            assert "text" not in rename_events[-1]

            _, _, _, effective_character_data, _, _, _, _, _ = cm.get_character_data()
            hidden_context = effective_character_data["新角色"]["__ai_context.profile_rename_events"]
            assert "我的改名记录" in hidden_context
            assert "我曾用名" in hidden_context
            assert "旧角色" in hidden_context
            assert "新角色" in hidden_context
            from memory.persona import PersonaManager
            persona_md = PersonaManager().render_persona_markdown("新角色")
            # 合成字段的内部裸键不能泄漏进渲染给模型的 persona 文本，只保留本地化标签。
            assert "__ai_context.profile_rename_events" not in persona_md
            assert "我的改名记录" in persona_md
            assert "我曾用名" in persona_md
            assert "旧角色" in persona_md
            assert "新角色" in persona_md
            assert not (Path(cm.memory_dir) / "旧角色").exists()
            assert (Path(cm.memory_dir) / "新角色" / "persona.json").is_file()
            assert (Path(cm.memory_dir) / "新角色" / "facts.json").is_file()

            recent_payload = json.loads(
                (Path(cm.memory_dir) / "新角色" / "recent.json").read_text(encoding="utf-8")
            )
            assert recent_payload[0]["speaker"] == "新角色"
            assert recent_payload[0]["data"]["content"].startswith("新角色说：")

            memory_rename_result = await memory_router_module.update_catgirl_name(
                _DummyRequest({"old_name": "旧角色", "new_name": "新角色"})
            )
            assert memory_rename_result["success"] is True
            assert (Path(cm.memory_dir) / "新角色" / "recent.json").is_file()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rename_master_adds_hidden_ai_context_and_master_save_preserves_it(monkeypatch):
    monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: "zh-CN")
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            old_master_name = cm.load_characters()["主人"]["档案名"]
            current_catgirl = cm.load_characters()["当前猫娘"]

            rename_result = await characters_router_module.rename_master(
                old_master_name,
                _DummyRequest({"new_name": "新主人"}),
            )

            assert rename_result["success"] is True
            saved_master = cm.load_characters()["主人"]
            assert "我的改名记录" not in saved_master
            rename_events = saved_master["_reserved"]["ai_context"]["rename_events"]
            assert rename_events[-1]["old_name"] == old_master_name
            assert rename_events[-1]["new_name"] == "新主人"
            assert "text" not in rename_events[-1]

            _, _, master_basic_config, _, _, _, _, _, _ = cm.get_character_data()
            hidden_context = master_basic_config["__ai_context.profile_rename_events"]
            # 主人改名记录进的是猫娘 persona 的 master section，去掉人称用中性陈述，
            # 既不能第一人称「我」（否则猫娘会以为是自己改了名），也不带第二人称「你」。
            assert "改名记录" in hidden_context
            assert "我" not in hidden_context
            assert "你" not in hidden_context
            assert old_master_name in hidden_context
            assert "新主人" in hidden_context

            from memory.persona import PersonaManager
            persona_md = PersonaManager().render_persona_markdown(current_catgirl)
            # 同上：裸键不泄漏，且主人段无人称。
            assert "__ai_context.profile_rename_events" not in persona_md
            assert "改名记录" in persona_md
            assert old_master_name in persona_md
            assert "新主人" in persona_md

            update_result = await characters_router_module.update_master(
                _DummyRequest({"档案名": "新主人", "昵称": "柚希"})
            )
            assert update_result["success"] is True
            saved_after_update = cm.load_characters()["主人"]
            assert saved_after_update["档案名"] == "新主人"
            assert saved_after_update["_reserved"]["ai_context"]["rename_events"][-1]["new_name"] == "新主人"
            initial_count = len(saved_after_update["_reserved"]["ai_context"]["rename_events"])

            bypass_result = await characters_router_module.update_master(
                _DummyRequest({"档案名": "绕过改名", "昵称": "柚希"})
            )
            assert bypass_result["success"] is True
            saved_after_bypass = cm.load_characters()["主人"]
            assert saved_after_bypass["档案名"] == "新主人"
            assert saved_after_bypass["_reserved"]["ai_context"]["rename_events"][-1]["new_name"] == "新主人"
            assert len(saved_after_bypass["_reserved"]["ai_context"]["rename_events"]) == initial_count

            same_name_result = await characters_router_module.rename_master(
                "新主人",
                _DummyRequest({"new_name": "新主人"}),
            )
            assert same_name_result["success"] is True
            saved_after_same_name = cm.load_characters()["主人"]
            assert len(saved_after_same_name["_reserved"]["ai_context"]["rename_events"]) == initial_count

            legacy_conflict_characters = cm.load_characters()
            legacy_conflict_characters.setdefault("猫娘", {})["新主人"] = {"档案名": "新主人"}
            cm.save_characters(legacy_conflict_characters)
            legacy_conflict_update = await characters_router_module.update_master(
                _DummyRequest({"昵称": "柚希2"})
            )
            assert legacy_conflict_update["success"] is True
            assert cm.load_characters()["主人"]["档案名"] == "新主人"
            empty_update = await characters_router_module.update_master(_DummyRequest({}))
            assert empty_update["success"] is True
            saved_after_empty_update = cm.load_characters()["主人"]
            assert saved_after_empty_update["档案名"] == "新主人"
            assert "昵称" not in saved_after_empty_update
            assert len(saved_after_empty_update["_reserved"]["ai_context"]["rename_events"]) == initial_count

            rename_conflict_characters = cm.load_characters()
            rename_conflict_characters.setdefault("猫娘", {})["主人同名猫娘"] = {"档案名": "主人同名猫娘"}
            cm.save_characters(rename_conflict_characters)
            cross_namespace_rename = await characters_router_module.rename_master(
                "新主人",
                _DummyRequest({"new_name": "主人同名猫娘"}),
            )
            assert cross_namespace_rename["success"] is True
            saved_after_cross_namespace_rename = cm.load_characters()["主人"]
            assert saved_after_cross_namespace_rename["档案名"] == "主人同名猫娘"
            assert saved_after_cross_namespace_rename["_reserved"]["ai_context"]["rename_events"][-1]["new_name"] == "主人同名猫娘"

            conflict_characters = cm.load_characters()
            conflict_characters["主人"] = {}
            conflict_characters.setdefault("猫娘", {})["占用名"] = {"档案名": "占用名"}
            cm.save_characters(conflict_characters)
            conflict_result = await characters_router_module.update_master(
                _DummyRequest({"档案名": "占用名", "昵称": "柚希"})
            )
            assert conflict_result["success"] is True
            assert cm.load_characters()["主人"]["档案名"] == "占用名"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_master_body_rename_fallback_repairs_legacy_path_name(monkeypatch):
    monkeypatch.setattr("utils.language_utils.get_global_language_full", lambda: "zh-CN")
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            legacy_characters = cm.load_characters()
            legacy_characters["主人"]["档案名"] = "旧/主人"
            legacy_characters["主人"]["昵称"] = "旧昵称"
            cm.save_characters(legacy_characters)

            repair_result = await characters_router_module.update_master(
                _DummyRequest({"档案名": "修复主人", "昵称": "柚希"})
            )
            assert repair_result["success"] is True
            saved_after_repair = cm.load_characters()["主人"]
            assert saved_after_repair["档案名"] == "修复主人"
            assert saved_after_repair["昵称"] == "柚希"
            rename_events = saved_after_repair["_reserved"]["ai_context"]["rename_events"]
            assert rename_events[-1]["old_name"] == "旧/主人"
            assert rename_events[-1]["new_name"] == "修复主人"
            initial_count = len(rename_events)

            bypass_result = await characters_router_module.update_master(
                _DummyRequest({"档案名": "再次绕过", "昵称": "柚希2"})
            )
            assert bypass_result["success"] is True
            saved_after_bypass = cm.load_characters()["主人"]
            assert saved_after_bypass["档案名"] == "修复主人"
            assert saved_after_bypass["昵称"] == "柚希2"
            assert len(saved_after_bypass["_reserved"]["ai_context"]["rename_events"]) == initial_count


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rename_catgirl_rolls_back_memory_and_suppresses_switch_notice_on_persist_failure():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        websocket = AsyncMock()

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state=_make_role_state_for_test({
                    "旧角色": SimpleNamespace(is_active=False, websocket=websocket, session=None),
                }),
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")

            fake_response = type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"status": "success"}},
            )()
            fake_client = AsyncMock()
            fake_client.__aenter__.return_value = fake_client
            fake_client.__aexit__.return_value = False
            fake_client.post.return_value = fake_response

            with patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client):
                add_result = await characters_router_module.add_catgirl(
                    _DummyRequest({"档案名": "旧角色"})
                )
            assert add_result["success"] is True

            characters = cm.load_characters()
            characters["当前猫娘"] = "旧角色"
            cm.save_characters(characters, bypass_write_fence=True)

            old_memory_dir = Path(cm.memory_dir) / "旧角色"
            old_memory_dir.mkdir(parents=True, exist_ok=True)
            (old_memory_dir / "recent.json").write_text(
                json.dumps(
                    [
                        {
                            "speaker": "旧角色",
                            "data": {"content": "旧角色说：你好"},
                        }
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            original_save_characters = cm.save_characters

            def _fail_primary_save(data, character_json_path=None, *, bypass_write_fence=False):
                if not bypass_write_fence and "新角色" in (data.get("猫娘") or {}):
                    raise OSError("disk full")
                return original_save_characters(
                    data,
                    character_json_path=character_json_path,
                    bypass_write_fence=bypass_write_fence,
                )

            with patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client), patch.object(
                cm,
                "save_characters",
                side_effect=_fail_primary_save,
            ):
                rename_result = await characters_router_module.rename_catgirl(
                    "旧角色",
                    _DummyRequest({"new_name": "新角色"}),
                )

            assert rename_result.status_code == 500
            payload = json.loads(rename_result.body.decode("utf-8"))
            assert payload["success"] is False
            assert "disk full" in payload["error"]

            current_characters = cm.load_characters()
            assert "旧角色" in current_characters.get("猫娘", {})
            assert "新角色" not in current_characters.get("猫娘", {})
            assert current_characters["当前猫娘"] == "旧角色"
            assert old_memory_dir.exists()
            assert not (Path(cm.memory_dir) / "新角色").exists()

            restored_recent_payload = json.loads((old_memory_dir / "recent.json").read_text(encoding="utf-8"))
            assert restored_recent_payload[0]["speaker"] == "旧角色"
            websocket.send_text.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rename_catgirl_returns_503_and_keeps_disk_unchanged_when_memory_release_fails():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        websocket = AsyncMock()

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state=_make_role_state_for_test({
                    "旧角色": SimpleNamespace(is_active=False, websocket=websocket, session=None),
                }),
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")

            fake_response = type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"status": "success"}},
            )()
            fake_client = AsyncMock()
            fake_client.__aenter__.return_value = fake_client
            fake_client.__aexit__.return_value = False
            fake_client.post.return_value = fake_response

            with patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client):
                add_result = await characters_router_module.add_catgirl(
                    _DummyRequest({"档案名": "旧角色"})
                )
            assert add_result["success"] is True

            characters = cm.load_characters()
            characters["当前猫娘"] = "旧角色"
            cm.save_characters(characters, bypass_write_fence=True)

            old_memory_dir = Path(cm.memory_dir) / "旧角色"
            old_memory_dir.mkdir(parents=True, exist_ok=True)
            (old_memory_dir / "recent.json").write_text(
                json.dumps(
                    [
                        {
                            "speaker": "旧角色",
                            "data": {"content": "旧角色说：你好"},
                        }
                    ],
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            resume_memory = AsyncMock(return_value=True)
            with patch.object(
                characters_router_module,
                "release_memory_server_character",
                AsyncMock(return_value=False),
            ) as mock_release, patch.object(
                characters_router_module,
                "notify_memory_server_reload",
                resume_memory,
            ):
                rename_result = await characters_router_module.rename_catgirl(
                    "旧角色",
                    _DummyRequest({"new_name": "新角色"}),
                )

            assert rename_result.status_code == 503
            payload = json.loads(rename_result.body.decode("utf-8"))
            assert payload["success"] is False
            assert payload["code"] == "MEMORY_SERVER_RELEASE_FAILED"
            mock_release.assert_awaited_once()
            claim_token = mock_release.await_args.kwargs[
                "derived_task_claim_token"
            ]
            resume_memory.assert_awaited_once_with(
                reason="角色重命名 release 失败补偿: 旧角色 -> 新角色",
                release_derived_task_claims={
                    "旧角色": (claim_token,),
                },
            )

            current_characters = cm.load_characters()
            assert "旧角色" in current_characters.get("猫娘", {})
            assert "新角色" not in current_characters.get("猫娘", {})
            assert current_characters["当前猫娘"] == "旧角色"
            assert old_memory_dir.exists()
            assert (old_memory_dir / "recent.json").is_file()
            assert not (Path(cm.memory_dir) / "新角色").exists()
            websocket.send_text.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rename_catgirl_maintenance_error_preserves_original_exception_type_when_rollback_reports_string():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            characters = cm.load_characters()
            characters.setdefault("猫娘", {})["维护重命名角色"] = {"昵称": "维护重命名角色"}
            cm.save_characters(characters, bypass_write_fence=True)

            maintenance_error = MaintenanceModeError(
                "maintenance_readonly",
                operation="rename",
                target="characters/维护重命名角色 -> 新角色",
            )
            original_save_characters = cm.save_characters

            def _raise_maintenance_on_primary_save(data, character_json_path=None, *, bypass_write_fence=False):
                if not bypass_write_fence and "新角色" in (data.get("猫娘") or {}):
                    raise maintenance_error
                return original_save_characters(
                    data,
                    character_json_path=character_json_path,
                    bypass_write_fence=bypass_write_fence,
                )

            with (
                patch.object(
                    characters_router_module,
                    "release_memory_server_character",
                    AsyncMock(return_value=True),
                ),
                patch.object(cm, "save_characters", side_effect=_raise_maintenance_on_primary_save),
                patch.object(
                    characters_router_module,
                    "_rollback_character_operation",
                    AsyncMock(return_value="notify_memory_server_reload failed: returned False"),
                ),
            ):
                with pytest.raises(MaintenanceModeError) as exc_info:
                    await characters_router_module.rename_catgirl(
                        "维护重命名角色",
                        _DummyRequest({"new_name": "新角色"}),
                    )

            assert exc_info.value is maintenance_error
            assert isinstance(exc_info.value.__cause__, RuntimeError)
            assert "notify_memory_server_reload failed: returned False" in str(exc_info.value.__cause__)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rename_error_rollback_finishes_when_request_is_cancelled(tmp_path):
    """Cancellation arriving in an error handler must not detach rollback."""
    cm = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(cm)

    async def _noop(*_args, **_kwargs):
        return None

    with patch("utils.config_manager._config_manager", cm):
        init_shared_state(
            role_state={},
            steamworks=None,
            templates=None,
            config_manager=cm,
            logger=None,
            initialize_character_data=_noop,
            switch_current_catgirl_fast=_noop,
            init_one_catgirl=_noop,
            remove_one_catgirl=_noop,
        )
        crud = reload_module("main_routers.characters_router.crud")
        characters = cm.load_characters()
        characters.setdefault("猫娘", {})["Old"] = {"昵称": "Old"}
        cm.save_characters(characters, bypass_write_fence=True)

        rollback_entered = asyncio.Event()
        release_rollback = asyncio.Event()

        async def _rollback(*_args, **_kwargs):
            rollback_entered.set()
            await release_rollback.wait()
            return ""

        with patch.object(
            crud,
            "release_memory_server_character",
            AsyncMock(return_value=True),
        ), patch.object(
            cm,
            "save_characters",
            side_effect=OSError("primary publish failed"),
        ), patch.object(
            crud,
            "_rollback_character_operation",
            side_effect=_rollback,
        ):
            operation = asyncio.create_task(crud.rename_catgirl(
                "Old", _DummyRequest({"new_name": "New"})
            ))
            await asyncio.wait_for(rollback_entered.wait(), timeout=3)
            operation.cancel()
            await asyncio.sleep(0.05)
            assert not operation.done()

            release_rollback.set()
            with pytest.raises(asyncio.CancelledError):
                await operation


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_sync_imports_legacy_dotted_name_but_rejects_unsafe_names():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")
            installed_folder = Path(td) / "legacy_dotted_name_workshop_item"
            installed_folder.mkdir(parents=True, exist_ok=True)

            card_payloads = {
                "legacy.chara.json": {"档案名": "N.E.K.O", "昵称": "历史点号角色"},
                "traversal.chara.json": {"档案名": "..", "昵称": "路径穿越"},
                "trailing-dot.chara.json": {"档案名": "角色.", "昵称": "尾随点号"},
                "separator.chara.json": {"档案名": "角色/子目录", "昵称": "路径分隔符"},
            }
            for filename, payload in card_payloads.items():
                (installed_folder / filename).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

            reload_memory = AsyncMock(return_value=True)
            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ), patch(
                "main_routers.characters_router.notify_memory_server_reload",
                reload_memory,
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards(
                    target_item_id="123456",
                )

            assert sync_result["added"] == 1
            assert sync_result["found_character_names"] == ["N.E.K.O"]
            assert sync_result["added_character_names"] == ["N.E.K.O"]
            current_catgirls = cm.load_characters().get("猫娘", {})
            assert "N.E.K.O" in current_catgirls
            assert ".." not in current_catgirls
            assert "角色." not in current_catgirls
            assert "角色/子目录" not in current_catgirls
            reload_memory.assert_awaited_once_with(
                reason="创意工坊角色卡同步",
                resume_derived_task_names=["N.E.K.O"],
            )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_deleted_workshop_character_casefold_variant_is_not_restored_by_startup_sync():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            characters = cm.load_characters()
            initial_name = next(iter(characters.get("猫娘", {})))
            characters["猫娘"]["N.E.K.O"] = {"昵称": "会复活吗"}
            cm.save_characters(characters, bypass_write_fence=True)

            fake_response = type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"status": "success"}},
            )()
            fake_client = AsyncMock()
            fake_client.__aenter__.return_value = fake_client
            fake_client.__aexit__.return_value = False
            fake_client.post.return_value = fake_response

            with patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client):
                delete_result = await characters_router_module.delete_catgirl("N.E.K.O")
            assert delete_result["success"] is True
            assert "N.E.K.O" not in cm.load_characters().get("猫娘", {})

            installed_folder = Path(td) / "mock_workshop_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": "n.e.k.o", "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result["added"] == 0
            assert sync_result["skipped"] >= 1
            current_characters = cm.load_characters()
            assert "n.e.k.o" not in current_characters.get("猫娘", {})
            assert current_characters["当前猫娘"] == initial_name


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workshop_sync_skips_casefold_conflicting_dotted_names():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")
            installed_folder = Path(td) / "casefold_conflict_workshop_item"
            installed_folder.mkdir(parents=True, exist_ok=True)

            for filename, payload in {
                "upper.chara.json": {"档案名": "N.E.K.O", "昵称": "历史点号角色"},
                "lower.chara.json": {"档案名": "n.e.k.o", "昵称": "大小写冲突角色"},
            }.items():
                (installed_folder / filename).write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards(
                    target_item_id="123456",
                )

            assert sync_result["added"] == 1
            assert sync_result["skipped"] >= 1
            current_catgirls = cm.load_characters().get("猫娘", {})
            assert len(current_catgirls) == len(
                {name.casefold() for name in current_catgirls}
            )
            imported_names = {"N.E.K.O", "n.e.k.o"} & set(current_catgirls)
            assert len(imported_names) == 1
            assert sync_result["existing_character_names"] == [
                next(iter(imported_names))
            ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_catgirl_skips_tombstone_state_when_cloudsave_local_state_is_unavailable(monkeypatch):
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")
            importlib.import_module('main_routers.workshop_router.meta')._session_deleted_names.clear()
            characters = cm.load_characters()
            initial_name = next(iter(characters.get("猫娘", {})))
            characters["猫娘"]["禁用云存档删除角色"] = {"昵称": "禁用云存档删除角色"}
            characters["当前猫娘"] = initial_name
            cm.save_characters(characters, bypass_write_fence=True)

            fake_response = type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"status": "success"}},
            )()
            fake_client = AsyncMock()
            fake_client.__aenter__.return_value = fake_client
            fake_client.__aexit__.return_value = False
            fake_client.post.return_value = fake_response

            monkeypatch.setenv(CLOUDSAVE_DISABLED_ENV, "local_state_unavailable")
            with (
                patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client),
                patch.object(
                    cm,
                    "load_character_tombstones_state",
                    side_effect=AssertionError("disabled cloudsave delete path should not load tombstones"),
                ),
                patch.object(
                    cm,
                    "save_character_tombstones_state",
                    side_effect=AssertionError("disabled cloudsave delete path should not save tombstones"),
                ),
                patch.object(
                    characters_router_module,
                    "_build_character_tombstones_state",
                    side_effect=AssertionError("disabled cloudsave delete path should not build tombstones"),
                ),
            ):
                delete_result = await characters_router_module.delete_catgirl("禁用云存档删除角色")

            assert delete_result["success"] is True
            current_characters = cm.load_characters()
            assert "禁用云存档删除角色" not in current_characters.get("猫娘", {})
            assert current_characters["当前猫娘"] == initial_name

            installed_folder = Path(td) / "disabled_cloudsave_workshop_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": "禁用云存档删除角色", "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result["added"] == 0
            assert sync_result["skipped"] >= 1
            current_characters = cm.load_characters()
            assert "禁用云存档删除角色" not in current_characters.get("猫娘", {})
            importlib.import_module('main_routers.workshop_router.meta')._session_deleted_names.clear()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_manual_workshop_character_sync_restores_deleted_character_and_clears_tombstone():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            deleted_name = "N.E.K.O"
            deleted_alias = "n.e.k.o"
            restored_name = "N.e.k.o"
            cm.save_character_tombstones_state({
                "version": cm.CHARACTER_TOMBSTONES_STATE_VERSION,
                "tombstones": [
                    {
                        "character_name": deleted_name,
                        "deleted_at": "2026-05-25T00:00:00Z",
                        "sequence_number": 1,
                    },
                    {
                        "character_name": deleted_alias,
                        "deleted_at": "2026-05-25T00:00:01Z",
                        "sequence_number": 2,
                    },
                ],
            })

            installed_folder = Path(td) / "mock_workshop_manual_restore_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": restored_name, "昵称": "来自手动恢复"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            subscribed_items_mock = AsyncMock(
                return_value={
                    "success": True,
                    "items": [
                        {
                            "publishedFileId": "123456",
                            "installedFolder": str(installed_folder),
                        }
                    ],
                }
            )
            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                subscribed_items_mock,
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards(
                    target_item_id="123456",
                    restore_deleted=True,
                )
                second_result = await workshop_router_module.sync_workshop_character_cards(
                    target_item_id="123456",
                    restore_deleted=True,
                )

            assert sync_result["added"] == 1
            assert sync_result["added_character_names"] == [restored_name]
            assert sync_result["restored_deleted_names"] == [restored_name]
            current_characters = cm.load_characters()
            assert restored_name in current_characters.get("猫娘", {})
            tombstones = cm.load_character_tombstones_state().get("tombstones") or []
            assert not any(
                str(entry.get("character_name") or "").casefold()
                == restored_name.casefold()
                for entry in tombstones
            )

            assert second_result["added"] == 0
            assert second_result["existing_character_names"] == [restored_name]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_manual_workshop_character_sync_clears_tombstone_for_existing_character():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            existing_name = "N.E.K.O"
            restored_name = "n.e.k.o"
            characters = cm.load_characters()
            characters.setdefault("猫娘", {})[existing_name] = {
                "昵称": "已存在",
                "_reserved": {
                    "character_origin": {
                        "source": "steam_workshop",
                        "source_id": "123456",
                    }
                },
            }
            cm.save_characters(characters, bypass_write_fence=True)
            cm.save_character_tombstones_state({
                "version": cm.CHARACTER_TOMBSTONES_STATE_VERSION,
                "tombstones": [
                    {
                        "character_name": restored_name,
                        "deleted_at": "2026-05-25T00:00:00Z",
                        "sequence_number": 1,
                    }
                ],
            })

            installed_folder = Path(td) / "mock_workshop_existing_restore_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": restored_name, "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards(
                    target_item_id="123456",
                    restore_deleted=True,
                )

            assert sync_result["added"] == 0
            assert sync_result["existing_character_names"] == [existing_name]
            assert sync_result["restored_deleted_names"] == [existing_name]
            current_characters = cm.load_characters().get("猫娘", {})
            assert existing_name in current_characters
            assert restored_name not in current_characters
            tombstones = cm.load_character_tombstones_state().get("tombstones") or []
            assert not any(entry.get("character_name") == restored_name for entry in tombstones)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_manual_workshop_character_sync_clears_tombstone_for_avatar_only_bound_character():
    # 回归：旧数据 / 半迁移数据可能只有 avatar.asset_source 绑定（例如 live2d_item_id
    # 迁移只写 avatar.asset_source_id，或用户在模型设置里手动绑定 Workshop 模型），
    # 没有 character_origin。退订路径已按 avatar 命中删除它并打 tombstone，恢复路径
    # 也必须按 avatar 命中并清理 tombstone，否则该角色会永远卡在 409。
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            restored_name = "仅头像绑定角色"
            characters = cm.load_characters()
            characters.setdefault("猫娘", {})[restored_name] = {
                "昵称": "已存在",
                "_reserved": {
                    "avatar": {
                        "asset_source": "steam_workshop",
                        "asset_source_id": "123456",
                    }
                },
            }
            cm.save_characters(characters, bypass_write_fence=True)
            cm.save_character_tombstones_state({
                "version": cm.CHARACTER_TOMBSTONES_STATE_VERSION,
                "tombstones": [
                    {
                        "character_name": restored_name,
                        "deleted_at": "2026-05-25T00:00:00Z",
                        "sequence_number": 1,
                    }
                ],
            })

            installed_folder = Path(td) / "mock_workshop_avatar_only_restore_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": restored_name, "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards(
                    target_item_id="123456",
                    restore_deleted=True,
                )

            assert sync_result["added"] == 0
            assert sync_result["existing_character_names"] == [restored_name]
            assert sync_result["restored_deleted_names"] == [restored_name]
            tombstones = cm.load_character_tombstones_state().get("tombstones") or []
            assert not any(entry.get("character_name") == restored_name for entry in tombstones)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_manual_workshop_character_sync_keeps_tombstone_for_nonmatching_existing_character():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            restored_name = "同名本地角色"
            characters = cm.load_characters()
            characters.setdefault("猫娘", {})[restored_name] = {"昵称": "本地角色"}
            cm.save_characters(characters, bypass_write_fence=True)
            cm.save_character_tombstones_state({
                "version": cm.CHARACTER_TOMBSTONES_STATE_VERSION,
                "tombstones": [
                    {
                        "character_name": restored_name,
                        "deleted_at": "2026-05-25T00:00:00Z",
                        "sequence_number": 1,
                    }
                ],
            })

            installed_folder = Path(td) / "mock_workshop_nonmatching_restore_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": restored_name, "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards(
                    target_item_id="123456",
                    restore_deleted=True,
                )

            assert sync_result["added"] == 0
            assert sync_result["existing_character_names"] == [restored_name]
            assert sync_result["restored_deleted_names"] == []
            tombstones = cm.load_character_tombstones_state().get("tombstones") or []
            assert any(entry.get("character_name") == restored_name for entry in tombstones)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_manual_workshop_character_sync_defers_tombstone_cleanup_after_successful_save():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            restored_name = "延后清理墓碑角色"
            characters = cm.load_characters()
            characters.setdefault("猫娘", {})[restored_name] = {
                "昵称": "已存在",
                "_reserved": {
                    "character_origin": {
                        "source": "steam_workshop",
                        "source_id": "123456",
                    }
                },
            }
            cm.save_characters(characters, bypass_write_fence=True)
            cm.save_character_tombstones_state({
                "version": cm.CHARACTER_TOMBSTONES_STATE_VERSION,
                "tombstones": [
                    {
                        "character_name": restored_name,
                        "deleted_at": "2026-05-25T00:00:00Z",
                        "sequence_number": 1,
                    }
                ],
            })

            installed_folder = Path(td) / "mock_workshop_deferred_restore_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "existing.chara.json").write_text(
                json.dumps({"档案名": restored_name, "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (installed_folder / "new.chara.json").write_text(
                json.dumps({"档案名": "新工坊角色", "昵称": "新角色"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with (
                patch.object(
                    workshop_router_module,
                    "get_subscribed_workshop_items",
                    AsyncMock(
                        return_value={
                            "success": True,
                            "items": [
                                {
                                    "publishedFileId": "123456",
                                    "installedFolder": str(installed_folder),
                                }
                            ],
                        }
                    ),
                ),
                patch.object(workshop_router_module, "_ensure_workshop_card_face_from_preview", return_value=False),
                patch.object(workshop_router_module, "_ensure_workshop_card_face_meta", return_value=False),
                patch.object(
                    workshop_router_module,
                    "is_write_fence_active",
                    side_effect=[False, False, False, False, False, False, True],
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards(
                    target_item_id="123456",
                    restore_deleted=True,
                )

            assert sync_result["added"] == 1
            assert sync_result["tombstone_cleanup_deferred"] is True
            assert "新工坊角色" in cm.load_characters().get("猫娘", {})
            tombstones = cm.load_character_tombstones_state().get("tombstones") or []
            assert any(entry.get("character_name") == restored_name for entry in tombstones)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_single_workshop_character_card_treats_restored_existing_as_success():
    workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")
    sync_result = {
        "added": 0,
        "backfilled_faces": 0,
        "skipped": 1,
        "errors": 0,
        "target_found": True,
        "found_character_names": ["恢复角色"],
        "existing_character_names": ["恢复角色"],
        "restored_deleted_names": ["恢复角色"],
    }

    with patch.object(
        workshop_router_module,
        "sync_workshop_character_cards",
        AsyncMock(return_value=sync_result),
    ):
        response = await workshop_router_module.api_sync_single_workshop_character_card("123456")

    assert response["success"] is True
    assert response["restored_deleted_names"] == ["恢复角色"]
    assert response["message"] == "已加入角色卡：恢复角色"
    # 前端成功提示只读 added_character_names，仅恢复场景也必须带上恢复角色名，
    # 否则会被 formatWorkshopCharacterNameList 回退成“未知角色卡”。
    assert response["added_character_names"] == ["恢复角色"]


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("sync_result", "expected_status", "expected_code"),
    [
        (
            {
                "added": 0,
                "backfilled_faces": 0,
                "skipped": 0,
                "errors": 0,
                "target_found": False,
                "code": "WORKSHOP_ITEM_NOT_FOUND",
            },
            404,
            "WORKSHOP_ITEM_NOT_FOUND",
        ),
        (
            {
                "added": 0,
                "backfilled_faces": 0,
                "skipped": 1,
                "errors": 0,
                "target_found": True,
                "found_character_names": ["已存在角色"],
                "existing_character_names": ["已存在角色"],
            },
            409,
            "WORKSHOP_CHARACTER_ALREADY_EXISTS",
        ),
        (
            {
                "added": 0,
                "backfilled_faces": 0,
                "skipped": 0,
                "errors": 0,
                "target_found": True,
                "found_character_names": [],
                "existing_character_names": [],
            },
            404,
            "WORKSHOP_CHARACTER_NOT_FOUND",
        ),
        (
            {
                "added": 0,
                "backfilled_faces": 0,
                "skipped": 0,
                "errors": 0,
                "target_found": True,
                "found_character_names": ["未加入角色"],
                "existing_character_names": [],
            },
            422,
            "WORKSHOP_CHARACTER_NOT_ADDED",
        ),
        (
            # 真实后端异常被显式标记为 WORKSHOP_SYNC_FAILED 时，必须回 500，
            # 不能因 target_found / found_character_names 的残留值被误判成业务态。
            {
                "added": 0,
                "backfilled_faces": 0,
                "skipped": 0,
                "errors": 1,
                "target_found": True,
                "found_character_names": [],
                "existing_character_names": [],
                "code": "WORKSHOP_SYNC_FAILED",
            },
            500,
            "WORKSHOP_SYNC_FAILED",
        ),
    ],
)
async def test_sync_single_workshop_character_card_uses_error_status_codes(
    sync_result,
    expected_status,
    expected_code,
):
    workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

    with patch.object(
        workshop_router_module,
        "sync_workshop_character_cards",
        AsyncMock(return_value=sync_result),
    ):
        response = await workshop_router_module.api_sync_single_workshop_character_card("123456")

    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == expected_status
    assert payload["success"] is False
    assert payload["code"] == expected_code
    assert "error" in payload


@pytest.mark.unit
@pytest.mark.asyncio
async def test_batch_workshop_character_sync_reports_subscription_unavailable():
    workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")
    sync_result = {
        "added": 0,
        "backfilled_faces": 0,
        "skipped": 0,
        "errors": 1,
        "code": "WORKSHOP_SUBSCRIPTIONS_UNAVAILABLE",
    }

    with patch.object(
        workshop_router_module,
        "sync_workshop_character_cards",
        AsyncMock(return_value=sync_result),
    ):
        response = await workshop_router_module.api_sync_workshop_character_cards()

    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 503
    assert payload["success"] is False
    assert payload["code"] == "WORKSHOP_SUBSCRIPTIONS_UNAVAILABLE"
    assert payload["errors"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_batch_workshop_character_sync_reports_internal_failure_as_500():
    # 后端异常被标记为 WORKSHOP_SYNC_FAILED 时，批量入口也要回 500，
    # 不能伪装成 success 的“同步完成”。
    workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")
    sync_result = {
        "added": 0,
        "backfilled_faces": 0,
        "skipped": 0,
        "errors": 1,
        "code": "WORKSHOP_SYNC_FAILED",
    }

    with patch.object(
        workshop_router_module,
        "sync_workshop_character_cards",
        AsyncMock(return_value=sync_result),
    ):
        response = await workshop_router_module.api_sync_workshop_character_cards()

    payload = json.loads(response.body.decode("utf-8"))
    assert response.status_code == 500
    assert payload["success"] is False
    assert payload["code"] == "WORKSHOP_SYNC_FAILED"
    assert payload["errors"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_workshop_character_cards_skips_save_when_maintenance_fence_turns_on():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            installed_folder = Path(td) / "mock_workshop_maintenance_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": "维护态工坊角色", "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            maintenance_error = MaintenanceModeError(
                "maintenance_readonly",
                operation="save",
                target="characters.json",
            )
            assert_saved_mock = AsyncMock(side_effect=maintenance_error)

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ), patch.object(cm, "asave_characters", assert_saved_mock):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result == {
                "added": 0,
                "backfilled_faces": 0,
                "skipped": 0,
                "errors": 0,
                "blocked_by_write_fence": True,
            }
            assert_saved_mock.assert_awaited_once()
            assert "维护态工坊角色" not in cm.load_characters().get("猫娘", {})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_workshop_character_cards_preserves_persona_override_written_during_scan():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            installed_folder = Path(td) / "mock_workshop_persona_race_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": "启动竞态工坊角色", "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            current_name = cm.load_characters()["当前猫娘"]

            def _write_persona_override_during_scan(_installed_folder, _chara_name=None, _chara_file_stem=None):
                latest = cm.load_characters()
                latest["猫娘"][current_name].setdefault("_reserved", {})["persona_override"] = {
                    "preset_id": "classic_genki",
                    "source": "onboarding",
                    "selected_at": "2026-05-08T12:00:00Z",
                    "prompt_guidance": "保持测试人格",
                    "profile": {
                        "性格原型": "经典元气猫娘",
                    },
                }
                cm.save_characters(latest, bypass_write_fence=True)
                return None

            with (
                patch.object(
                    workshop_router_module,
                    "get_subscribed_workshop_items",
                    AsyncMock(
                        return_value={
                            "success": True,
                            "items": [
                                {
                                    "publishedFileId": "123456",
                                    "installedFolder": str(installed_folder),
                                }
                            ],
                        }
                    ),
                ),
                patch.object(
                    workshop_router_module,
                    "find_preview_image_in_folder",
                    side_effect=_write_persona_override_during_scan,
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result["added"] == 1
            saved_characters = cm.load_characters()
            assert "启动竞态工坊角色" in saved_characters.get("猫娘", {})
            saved_override = saved_characters["猫娘"][current_name].get("_reserved", {}).get("persona_override")
            assert saved_override["preset_id"] == "classic_genki"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_workshop_character_cards_does_not_write_orphan_face_when_pending_add_is_skipped():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            installed_folder = Path(td) / "mock_workshop_orphan_face_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": "并发工坊角色", "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            duplicate_dir = installed_folder / "duplicate"
            duplicate_dir.mkdir()
            (duplicate_dir / "重复角色卡.chara.json").write_text(
                json.dumps({"档案名": "并发工坊角色", "昵称": "重复工坊卡"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            preview_path = installed_folder / "preview.png"
            Image.new("RGBA", (1024, 1024), (80, 160, 220, 255)).save(preview_path)

            def _create_same_character_during_scan(_installed_folder, _chara_name=None, _chara_file_stem=None):
                latest = cm.load_characters()
                latest.setdefault("猫娘", {})["并发工坊角色"] = {"昵称": "并发创建"}
                cm.save_characters(latest, bypass_write_fence=True)
                return str(preview_path)

            with (
                patch.object(
                    workshop_router_module,
                    "get_subscribed_workshop_items",
                    AsyncMock(
                        return_value={
                            "success": True,
                            "items": [
                                {
                                    "publishedFileId": "123456",
                                    "installedFolder": str(installed_folder),
                                }
                            ],
                        }
                    ),
                ),
                patch.object(
                    workshop_router_module,
                    "find_preview_image_in_folder",
                    side_effect=_create_same_character_during_scan,
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result["added"] == 0
            assert sync_result["skipped"] >= 1
            saved_characters = cm.load_characters()
            assert saved_characters["猫娘"]["并发工坊角色"]["昵称"] == "并发创建"
            assert not (cm.card_faces_dir / "并发工坊角色.png").exists()
            assert not cm.card_face_meta_path("并发工坊角色").exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_workshop_character_cards_aborts_when_latest_catgirl_map_is_malformed():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            installed_folder = Path(td) / "mock_workshop_bad_latest_characters"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": "坏结构保护角色", "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            initial_characters = cm.load_characters()
            malformed_latest = {**initial_characters, "猫娘": []}
            save_mock = AsyncMock()

            with (
                patch.object(
                    workshop_router_module,
                    "get_subscribed_workshop_items",
                    AsyncMock(
                        return_value={
                            "success": True,
                            "items": [
                                {
                                    "publishedFileId": "123456",
                                    "installedFolder": str(installed_folder),
                                }
                            ],
                        }
                    ),
                ),
                patch.object(cm, "aload_characters", AsyncMock(side_effect=[initial_characters, malformed_latest])),
                patch.object(cm, "asave_characters", save_mock),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result["added"] == 0
            assert sync_result["errors"] == 1
            save_mock.assert_not_awaited()
            assert "坏结构保护角色" not in cm.load_characters().get("猫娘", {})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_workshop_character_cards_skips_face_writes_when_maintenance_fence_turns_on_mid_scan():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            installed_folder = Path(td) / "mock_workshop_face_fence_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": "围栏封面角色", "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            Image.new("RGBA", (1024, 1024), (80, 160, 220, 255)).save(installed_folder / "preview.png")

            fence_states = iter([False, True])

            def _fake_write_fence(_config_mgr):
                return next(fence_states, True)

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ), patch.object(workshop_router_module, "is_write_fence_active", side_effect=_fake_write_fence):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result == {
                "added": 0,
                "backfilled_faces": 0,
                "skipped": 0,
                "errors": 0,
                "blocked_by_write_fence": True,
            }
            assert "围栏封面角色" not in cm.load_characters().get("猫娘", {})
            assert not (cm.card_faces_dir / "围栏封面角色.png").exists()
            assert not cm.card_face_meta_path("围栏封面角色").exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_workshop_character_cards_counts_errors_when_new_face_backfill_fails():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            installed_folder = Path(td) / "mock_workshop_face_error_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": "封面失败角色", "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ), patch.object(
                workshop_router_module,
                "_ensure_workshop_card_face_from_preview",
                side_effect=RuntimeError("preview render failed"),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result["added"] == 1
            assert sync_result["errors"] == 1
            assert "封面失败角色" in cm.load_characters().get("猫娘", {})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_workshop_character_cards_counts_errors_when_existing_face_backfill_fails():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            characters = cm.load_characters()
            characters.setdefault("猫娘", {})["已有工坊角色"] = {
                "昵称": "已存在",
                "_reserved": {
                    "character_origin": {
                        "source": "steam_workshop",
                        "source_id": "123456",
                    }
                },
            }
            cm.save_characters(characters, bypass_write_fence=True)

            installed_folder = Path(td) / "mock_workshop_existing_face_error_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps({"档案名": "已有工坊角色", "昵称": "来自工坊"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ), patch.object(
                workshop_router_module,
                "_ensure_workshop_card_face_from_preview",
                side_effect=RuntimeError("preview render failed"),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result["added"] == 0
            assert sync_result["skipped"] >= 1
            assert sync_result["errors"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_workshop_character_cards_uses_character_specific_preview_in_multi_card_item():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            installed_folder = Path(td) / "mock_workshop_multi_card_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "Alice.chara.json").write_text(
                json.dumps({"档案名": "Alice", "昵称": "from workshop"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            (installed_folder / "Bob.chara.json").write_text(
                json.dumps({"档案名": "Bob", "昵称": "from workshop"}, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            Image.new("RGBA", (1024, 1024), (80, 160, 220, 255)).save(installed_folder / "Alice.png")
            Image.new("RGBA", (1024, 1024), (120, 80, 180, 255)).save(installed_folder / "Bob.png")

            preview_by_character = {}

            def _capture_preview(_config_mgr, chara_name, preview_image_path, _item):
                preview_by_character[chara_name] = Path(preview_image_path).name if preview_image_path else None
                return True

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "123456",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ), patch.object(
                workshop_router_module,
                "_ensure_workshop_card_face_from_preview",
                side_effect=_capture_preview,
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

            assert sync_result["added"] == 2
            assert preview_by_character == {
                "Alice": "Alice.png",
                "Bob": "Bob.png",
            }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sync_workshop_character_cards_persists_character_origin_metadata():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            installed_folder = Path(td) / "mock_workshop_origin_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps(
                    {
                        "档案名": "工坊同步角色",
                        "昵称": "来自创意工坊",
                        "model_type": "live2d",
                        "live2d": "Blue cat",
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "3671939765",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

        assert sync_result["added"] == 1

        from utils.config_manager import get_reserved

        current_characters = cm.load_characters()
        payload = current_characters.get("猫娘", {}).get("工坊同步角色")
        assert isinstance(payload, dict)
        assert payload["昵称"] == "来自创意工坊"
        assert get_reserved(payload, "avatar", "asset_source", default="") == "steam_workshop"
        assert get_reserved(payload, "avatar", "asset_source_id", default="") == "3671939765"
        assert get_reserved(payload, "avatar", "live2d", "model_path", default="") == "/workshop/3671939765/Blue cat/Blue cat.model3.json"
        assert get_reserved(payload, "character_origin", "source", default="") == "steam_workshop"
        assert get_reserved(payload, "character_origin", "source_id", default="") == "3671939765"
        assert get_reserved(payload, "character_origin", "display_name", default="") == "Blue cat"
        assert get_reserved(payload, "character_origin", "model_ref", default="") == "/workshop/3671939765/Blue cat/Blue cat.model3.json"


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("card_payload", "expected_model_field", "expected_model_ref", "expected_display_name"),
    (
        (
            {
                "档案名": "工坊VRM角色",
                "昵称": "来自创意工坊 VRM",
                "model_type": "vrm",
                "vrm": "/workshop/3671939765/avatar/BlueCat.vrm",
            },
            "vrm",
            "/workshop/3671939765/avatar/BlueCat.vrm",
            "BlueCat",
        ),
        (
            {
                "档案名": "工坊MMD角色",
                "昵称": "来自创意工坊 MMD",
                "model_type": "mmd",
                "mmd": "/workshop/3671939765/miku/Miku.pmx",
            },
            "mmd",
            "/workshop/3671939765/miku/Miku.pmx",
            "Miku",
        ),
    ),
)
async def test_sync_workshop_character_cards_persists_live3d_workshop_origin_metadata(
    card_payload,
    expected_model_field,
    expected_model_ref,
    expected_display_name,
):
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            workshop_router_module = reload_module("main_routers.workshop_router.sync_cards")

            installed_folder = Path(td) / "mock_workshop_live3d_item"
            installed_folder.mkdir(parents=True, exist_ok=True)
            (installed_folder / "角色卡.chara.json").write_text(
                json.dumps(card_payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            with patch.object(
                workshop_router_module,
                "get_subscribed_workshop_items",
                AsyncMock(
                    return_value={
                        "success": True,
                        "items": [
                            {
                                "publishedFileId": "3671939765",
                                "installedFolder": str(installed_folder),
                            }
                        ],
                    }
                ),
            ):
                sync_result = await workshop_router_module.sync_workshop_character_cards()

        assert sync_result["added"] == 1

        from utils.config_manager import get_reserved

        current_characters = cm.load_characters()
        payload = current_characters.get("猫娘", {}).get(card_payload["档案名"])
        assert isinstance(payload, dict)
        assert get_reserved(payload, "avatar", "asset_source", default="") == "steam_workshop"
        assert get_reserved(payload, "avatar", "asset_source_id", default="") == "3671939765"
        assert get_reserved(payload, "avatar", "model_type", default="") == "live3d"
        assert get_reserved(payload, "avatar", expected_model_field, "model_path", default="") == expected_model_ref
        assert get_reserved(payload, "character_origin", "source", default="") == "steam_workshop"
        assert get_reserved(payload, "character_origin", "source_id", default="") == "3671939765"
        assert get_reserved(payload, "character_origin", "display_name", default="") == expected_display_name
        assert get_reserved(payload, "character_origin", "model_ref", default="") == expected_model_ref


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_catgirl_returns_error_when_memory_cleanup_fails():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")

            characters = cm.load_characters()
            characters.setdefault("猫娘", {})["删除失败角色"] = {"昵称": "删除失败角色"}
            cm.save_characters(characters, bypass_write_fence=True)

            fake_response = type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"status": "success"}},
            )()
            fake_client = AsyncMock()
            fake_client.__aenter__.return_value = fake_client
            fake_client.__aexit__.return_value = False
            fake_client.post.return_value = fake_response

            with (
                patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client),
                patch(
                    "main_routers.characters_router.crud.delete_character_memory_storage",
                    side_effect=OSError("time_indexed.db is locked"),
                ),
            ):
                delete_result = await characters_router_module.delete_catgirl("删除失败角色")

            assert delete_result.status_code == 500
            payload = json.loads(delete_result.body.decode("utf-8"))
            assert payload["success"] is False
            assert "time_indexed.db is locked" in payload["error"]
            assert payload["memory_server_released"] is True
            assert "删除失败角色" in cm.load_characters().get("猫娘", {})


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_catgirl_returns_503_when_memory_handle_release_fails_before_disk_changes():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            characters = cm.load_characters()
            characters.setdefault("猫娘", {})["删除句柄失败角色"] = {"昵称": "删除句柄失败角色"}
            cm.save_characters(characters, bypass_write_fence=True)

            with (
                patch.object(
                    characters_router_module,
                    "release_memory_server_character",
                    AsyncMock(return_value=False),
                ),
                patch.object(
                    characters_router_module,
                    "notify_memory_server_reload",
                    AsyncMock(return_value=True),
                ),
                patch.object(characters_router_module, "delete_character_memory_storage") as mock_delete_memory,
            ):
                delete_result = await characters_router_module.delete_catgirl("删除句柄失败角色")

            assert delete_result.status_code == 503
            payload = json.loads(delete_result.body.decode("utf-8"))
            assert payload["success"] is False
            assert payload["memory_server_released"] is False
            assert "删除句柄失败角色" in cm.load_characters().get("猫娘", {})
            mock_delete_memory.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_catgirl_rolls_back_tombstone_and_memory_when_persist_failure_occurs():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")

            characters = cm.load_characters()
            characters.setdefault("猫娘", {})["删除回滚角色"] = {"昵称": "删除回滚角色"}
            cm.save_characters(characters, bypass_write_fence=True)

            memory_dir = Path(cm.memory_dir) / "删除回滚角色"
            memory_dir.mkdir(parents=True, exist_ok=True)
            (memory_dir / "recent.json").write_text(
                json.dumps([{"speaker": "删除回滚角色", "content": "你好"}], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            fake_response = type(
                "Resp",
                (),
                {"status_code": 200, "json": lambda self: {"status": "success"}},
            )()
            fake_client = AsyncMock()
            fake_client.__aenter__.return_value = fake_client
            fake_client.__aexit__.return_value = False
            fake_client.post.return_value = fake_response

            original_save_characters = cm.save_characters

            def _fail_primary_save(data, character_json_path=None, *, bypass_write_fence=False):
                if not bypass_write_fence and "删除回滚角色" not in (data.get("猫娘") or {}):
                    raise OSError("disk full")
                return original_save_characters(
                    data,
                    character_json_path=character_json_path,
                    bypass_write_fence=bypass_write_fence,
                )

            with patch("main_routers.characters_router.notify.httpx.AsyncClient", return_value=fake_client), patch.object(
                cm,
                "save_characters",
                side_effect=_fail_primary_save,
            ):
                delete_result = await characters_router_module.delete_catgirl("删除回滚角色")

            assert delete_result.status_code == 500
            payload = json.loads(delete_result.body.decode("utf-8"))
            assert payload["success"] is False
            assert "disk full" in payload["error"]
            assert payload["memory_server_released"] is True
            assert "删除回滚角色" in cm.load_characters().get("猫娘", {})
            assert (memory_dir / "recent.json").is_file()
            tombstones = cm.load_character_tombstones_state().get("tombstones") or []
            assert not any(entry.get("character_name") == "删除回滚角色" for entry in tombstones)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_catgirl_rolls_back_when_notify_reload_returns_false():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            characters = cm.load_characters()
            characters.setdefault("猫娘", {})["删除重载失败角色"] = {"昵称": "删除重载失败角色"}
            cm.save_characters(characters, bypass_write_fence=True)

            memory_dir = Path(cm.memory_dir) / "删除重载失败角色"
            memory_dir.mkdir(parents=True, exist_ok=True)
            recent_path = memory_dir / "recent.json"
            recent_path.write_text("[]", encoding="utf-8")

            with (
                patch.object(
                    characters_router_module,
                    "release_memory_server_character",
                    AsyncMock(return_value=True),
                ),
                patch.object(
                    characters_router_module,
                    "notify_memory_server_reload",
                    AsyncMock(side_effect=[False, True]),
                ),
            ):
                delete_result = await characters_router_module.delete_catgirl("删除重载失败角色")

            assert delete_result.status_code == 500
            payload = json.loads(delete_result.body.decode("utf-8"))
            assert payload["success"] is False
            assert "notify_memory_server_reload returned False" in payload["error"]
            assert payload["memory_server_released"] is True

            reloaded_characters = cm.load_characters()
            assert "删除重载失败角色" in reloaded_characters.get("猫娘", {})
            assert recent_path.is_file()
            tombstones = cm.load_character_tombstones_state().get("tombstones") or []
            assert not any(entry.get("character_name") == "删除重载失败角色" for entry in tombstones)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_delete_catgirl_maintenance_error_preserves_original_exception_type_when_rollback_reports_string():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            characters = cm.load_characters()
            characters.setdefault("猫娘", {})["维护删除角色"] = {"昵称": "维护删除角色"}
            cm.save_characters(characters, bypass_write_fence=True)

            maintenance_error = MaintenanceModeError(
                "maintenance_readonly",
                operation="delete",
                target="characters/维护删除角色",
            )
            original_save_characters = cm.save_characters

            def _raise_maintenance_on_primary_save(data, character_json_path=None, *, bypass_write_fence=False):
                if not bypass_write_fence and "维护删除角色" not in (data.get("猫娘") or {}):
                    raise maintenance_error
                return original_save_characters(
                    data,
                    character_json_path=character_json_path,
                    bypass_write_fence=bypass_write_fence,
                )

            with (
                patch.object(
                    characters_router_module,
                    "release_memory_server_character",
                    AsyncMock(return_value=True),
                ),
                patch.object(cm, "save_characters", side_effect=_raise_maintenance_on_primary_save),
                patch.object(
                    characters_router_module,
                    "_rollback_character_operation",
                    AsyncMock(return_value="tombstones restore failed: readonly"),
                ),
            ):
                with pytest.raises(MaintenanceModeError) as exc_info:
                    await characters_router_module.delete_catgirl("维护删除角色")

            assert exc_info.value is maintenance_error
            assert isinstance(exc_info.value.__cause__, RuntimeError)
            assert "tombstones restore failed: readonly" in str(exc_info.value.__cause__)


@pytest.mark.unit
def test_resolve_live2d_model_binding_keeps_manual_external_url_without_catalog_rebind():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")

            with patch.object(
                importlib.import_module('main_routers.characters_router.live2d_models'),
                "find_models",
                side_effect=AssertionError("manual_external should skip local model lookup"),
            ):
                model_ref = "https://example.com/live2d/neko/neko.model3.json"
                model_path, source_id, source = importlib.import_module('main_routers.characters_router.live2d_models')._resolve_live2d_model_binding(model_ref)

            assert model_path == model_ref
            assert source == "manual_external"
            assert source_id == ""


@pytest.mark.unit
def test_character_memory_regression_fixture_isolates_project_memory_dir(tmp_path):
    cm = _make_config_manager(tmp_path)

    assert cm.project_memory_dir == tmp_path / "memory" / "store"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_update_catgirl_l2d_marks_builtin_live2d_as_builtin():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            characters = cm.load_characters()
            characters["当前猫娘"] = "测试内置模型"
            characters["猫娘"]["测试内置模型"] = json.loads(
                json.dumps(characters["猫娘"][next(iter(characters["猫娘"]))], ensure_ascii=False)
            )
            cm.save_characters(characters, bypass_write_fence=True)

            with patch.object(
                importlib.import_module('main_routers.characters_router.live2d_models'),
                "find_models",
                return_value=[
                    {
                        "name": "mao_pro",
                        "path": "/static/mao_pro/mao_pro.model3.json",
                        "source": "static",
                    }
                ],
            ):
                response = await importlib.import_module('main_routers.characters_router.live2d_models').update_catgirl_l2d(
                    "测试内置模型",
                    _DummyRequest({"live2d": "mao_pro", "model_type": "live2d"}),
                )

            assert response.status_code == 200

            from utils.config_manager import get_reserved

            payload = cm.load_characters()["猫娘"]["测试内置模型"]
            assert get_reserved(payload, "avatar", "live2d", "model_path", default="") == "mao_pro/mao_pro.model3.json"
            assert get_reserved(payload, "avatar", "asset_source", default="") == "builtin"
            assert get_reserved(payload, "avatar", "asset_source_id", default="") == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_character_rollback_reports_notify_reload_false_as_failure():
    with TemporaryDirectory() as td:
        cm = _make_config_manager(Path(td))
        bootstrap_local_cloudsave_environment(cm)

        async def _noop_init():
            return None

        async def _noop_any(*args, **kwargs):
            return None

        with patch("utils.config_manager._config_manager", cm):
            init_shared_state(
                role_state={},
                steamworks=None,
                templates=None,
                config_manager=cm,
                logger=None,
                initialize_character_data=_noop_init,
                switch_current_catgirl_fast=_noop_any,
                init_one_catgirl=_noop_any,
                remove_one_catgirl=_noop_any,
            )

            characters_router_module = reload_module("main_routers.characters_router.crud")
            characters_snapshot = cm.load_characters()

            with patch.object(
                characters_router_module,
                "notify_memory_server_reload",
                AsyncMock(return_value=False),
            ):
                rollback_error = await characters_router_module._rollback_character_operation(
                    cm,
                    characters_snapshot=characters_snapshot,
                    memory_snapshot_records=[],
                    reason="unit-test rollback",
                )

        assert "notify_memory_server_reload failed: returned False" in rollback_error


@pytest.mark.unit
def test_rewrite_recent_file_character_name_does_not_rewrite_role_fields(tmp_path):
    from utils.character_memory import rewrite_recent_file_character_name

    recent_path = tmp_path / "recent.json"
    recent_path.write_text(
        json.dumps(
            [
                {
                    "role": "旧角色",
                    "speaker": "旧角色",
                    "data": {
                        "role": "旧角色",
                        "speaker": "旧角色",
                        "content": "旧角色说：你好",
                    },
                }
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    assert rewrite_recent_file_character_name(recent_path, "旧角色", "新角色") is True

    payload = json.loads(recent_path.read_text(encoding="utf-8"))
    assert payload[0]["role"] == "旧角色"
    assert payload[0]["speaker"] == "新角色"
    assert payload[0]["data"]["role"] == "旧角色"
    assert payload[0]["data"]["speaker"] == "新角色"
    assert payload[0]["data"]["content"].startswith("新角色说：")


@pytest.mark.unit
def test_move_path_raises_when_target_file_exists(tmp_path):
    from utils.character_memory import _move_path

    source_path = tmp_path / "source.json"
    target_path = tmp_path / "target.json"
    source_path.write_text("source", encoding="utf-8")
    target_path.write_text("target", encoding="utf-8")

    with pytest.raises(FileExistsError):
        _move_path(source_path, target_path)

    assert source_path.is_file()
    assert target_path.is_file()


@pytest.mark.unit
def test_timeindexed_dispose_engine_also_clears_sql_chat_engine_cache(monkeypatch):
    from memory.timeindex import TimeIndexedMemory
    from utils.llm_client import SQLChatMessageHistory

    class _DummyEngine:
        def __init__(self):
            self.dispose_calls = 0

        def dispose(self):
            self.dispose_calls += 1

    primary_engine = _DummyEngine()
    cached_engine = _DummyEngine()
    normalized_path = os.path.abspath("D:/tmp/test-time-indexed.db").replace("\\", "/")
    connection_string = f"sqlite:///{normalized_path}"

    original_cache = dict(SQLChatMessageHistory._engine_cache)
    try:
        monkeypatch.setitem(SQLChatMessageHistory._engine_cache, connection_string, cached_engine)

        fake_config_manager = SimpleNamespace(
            get_character_data=lambda: ({}, {}, {}, {}, {}, {}, {}, {}, {}),
        )
        monkeypatch.setattr("memory.timeindex.get_config_manager", lambda: fake_config_manager)

        manager = TimeIndexedMemory(recent_history_manager=None)
        manager.engines = {"测试角色": primary_engine}
        manager.db_paths = {"测试角色": "D:/tmp/test-time-indexed.db"}

        released = manager.dispose_engine("测试角色")

        assert released is True
        assert primary_engine.dispose_calls == 1
        assert cached_engine.dispose_calls == 1
        assert "测试角色" not in manager.engines
        assert "测试角色" not in manager.db_paths
        assert connection_string not in SQLChatMessageHistory._engine_cache
        assert manager.dispose_engine("不存在角色") is False
    finally:
        SQLChatMessageHistory._engine_cache.clear()
        SQLChatMessageHistory._engine_cache.update(original_cache)


@pytest.mark.unit
def test_timeindexed_engine_admission_fence_prevents_lazy_recreation(monkeypatch):
    from memory.timeindex import CharacterEngineAdmissionError, TimeIndexedMemory

    manager = TimeIndexedMemory(
        recent_history_manager=None,
        engine_admission_check=lambda _name: False,
    )
    monkeypatch.setattr(
        manager,
        "_resolve_expected_db_path",
        lambda *_args, **_kwargs: pytest.fail(
            "fenced identity must not resolve or create storage"
        ),
    )

    with pytest.raises(CharacterEngineAdmissionError):
        manager._ensure_engine_exists("正在删除角色")
    with pytest.raises(CharacterEngineAdmissionError):
        manager._ensure_engine_exists("正在删除角色", readonly=True)
    with pytest.raises(CharacterEngineAdmissionError):
        manager.retrieve_original_by_timeframe("正在删除角色", None, None)


@pytest.mark.unit
def test_timeindexed_engine_init_failure_disposes_engine_and_clears_temp_cache(monkeypatch, tmp_path):
    from memory.timeindex import TimeIndexedMemory
    from utils.llm_client import SQLChatMessageHistory

    class _DummyEngine:
        def __init__(self):
            self.dispose_calls = 0

        def dispose(self):
            self.dispose_calls += 1

    created_engine = _DummyEngine()
    cached_engine = _DummyEngine()
    db_path = (tmp_path / "time_indexed.db").resolve()
    connection_string = f"sqlite:///{db_path.as_posix()}"

    original_cache = dict(SQLChatMessageHistory._engine_cache)
    try:
        fake_config_manager = SimpleNamespace(
            get_character_data=lambda: ({}, {}, {}, {}, {}, {}, {}, {}, {}),
        )
        monkeypatch.setattr("memory.timeindex.get_config_manager", lambda: fake_config_manager)
        monkeypatch.setattr("memory.timeindex.create_engine", lambda _connection_string: created_engine)

        manager = TimeIndexedMemory(recent_history_manager=None)
        monkeypatch.setattr(manager, "_assert_timeindex_writable", lambda _lanlan_name: None)

        def _explode_after_cache(_engine, _connection_string, _lanlan_name):
            SQLChatMessageHistory._engine_cache[_connection_string] = cached_engine
            raise RuntimeError("force init failure")

        monkeypatch.setattr(manager, "_ensure_tables_exist_with", _explode_after_cache)

        assert manager._ensure_engine_exists("测试角色", db_path=str(db_path), readonly=False) is False
        assert created_engine.dispose_calls == 1
        assert cached_engine.dispose_calls == 1
        assert connection_string not in SQLChatMessageHistory._engine_cache
        assert "测试角色" not in manager.engines
        assert "测试角色" not in manager.db_paths
    finally:
        SQLChatMessageHistory._engine_cache.clear()
        SQLChatMessageHistory._engine_cache.update(original_cache)


@pytest.mark.unit
def test_timeindexed_readonly_open_still_runs_writable_bootstrap_on_first_write(monkeypatch, tmp_path):
    from memory.timeindex import TimeIndexedMemory

    class _DummyEngine:
        def __init__(self, name):
            self.name = name
            self.dispose_calls = 0

        def dispose(self):
            self.dispose_calls += 1

    db_path = (tmp_path / "time_indexed.db").resolve()
    db_path.write_text("", encoding="utf-8")
    readonly_engine = _DummyEngine("readonly")
    writable_engine = _DummyEngine("writable")
    created_engines = [readonly_engine, writable_engine]
    ensure_calls = []
    migrate_calls = []

    fake_config_manager = SimpleNamespace(
        get_character_data=lambda: ({}, {}, {}, {}, {}, {}, {}, {}, {}),
    )
    monkeypatch.setattr("memory.timeindex.get_config_manager", lambda: fake_config_manager)
    monkeypatch.setattr("memory.timeindex.create_engine", lambda _connection_string: created_engines.pop(0))

    manager = TimeIndexedMemory(recent_history_manager=None)
    monkeypatch.setattr(manager, "_assert_timeindex_writable", lambda _lanlan_name: None)
    monkeypatch.setattr(
        manager,
        "_ensure_tables_exist_with",
        lambda _engine, _connection_string, _lanlan_name: ensure_calls.append((_lanlan_name, _engine)),
    )
    monkeypatch.setattr(
        manager,
        "_check_and_migrate_schema",
        lambda _engine, _lanlan_name: migrate_calls.append((_lanlan_name, _engine)),
    )

    assert manager._ensure_engine_exists("测试角色", db_path=str(db_path), readonly=True) is True
    assert ensure_calls == []
    assert migrate_calls == []
    assert manager.engines["测试角色"] is readonly_engine
    assert manager._engine_readonly_flags["测试角色"] is True

    assert manager._ensure_engine_exists("测试角色", db_path=str(db_path), readonly=False) is True
    assert ensure_calls == [("测试角色", writable_engine)]
    assert migrate_calls == [("测试角色", writable_engine)]
    assert readonly_engine.dispose_calls == 1
    assert manager.engines["测试角色"] is writable_engine
    assert manager._engine_readonly_flags["测试角色"] is False

    assert manager._ensure_engine_exists("测试角色", db_path=str(db_path), readonly=False) is True
    assert ensure_calls == [("测试角色", writable_engine)]
    assert migrate_calls == [("测试角色", writable_engine)]


def test_timeindexed_dispose_and_rebuild_when_memory_dir_drifts(monkeypatch, tmp_path):
    """``TimeIndexedMemory.db_paths`` 是 per-character path cache，cache 命中后
    短路 return 不会重新校核当前 ``memory_dir``。罕见但可能：``/reload``
    期间底层 ``storage_policy`` 被改写，或测试 monkeypatch 了 memory_dir，
    cached 路径就和实际目标分叉。老 SQLAlchemy engine 还连着旧文件，新
    数据全飘到老位置——``/process`` 的 ``except Exception`` 又把 SQL
    错误吞掉，表象是 db 永远不更新（time perception 错乱）。

    本用例验证 ``_ensure_engine_exists`` 检测到 cached vs expected 漂移
    后会 dispose 旧 engine + 用 expected 路径重建。
    """
    from memory.timeindex import TimeIndexedMemory

    class _DummyEngine:
        def __init__(self, name):
            self.name = name
            self.dispose_calls = 0

        def dispose(self):
            self.dispose_calls += 1

    old_db_path = (tmp_path / "old" / "测试角色" / "time_indexed.db").resolve()
    old_db_path.parent.mkdir(parents=True, exist_ok=True)
    old_db_path.write_text("", encoding="utf-8")
    new_db_path = (tmp_path / "new" / "测试角色" / "time_indexed.db").resolve()
    new_db_path.parent.mkdir(parents=True, exist_ok=True)
    new_db_path.write_text("", encoding="utf-8")

    old_engine = _DummyEngine("old")
    new_engine = _DummyEngine("new")
    created_engines = [old_engine, new_engine]
    ensure_calls: list = []
    migrate_calls: list = []

    # 受控的 time_store——第一次返 old，第二次返 new，模拟 memory_dir 漂移。
    current_time_store = {"测试角色": str(old_db_path)}

    def _fake_character_data():
        return ({}, {}, {}, {}, {}, {}, dict(current_time_store), {}, {})

    fake_config_manager = SimpleNamespace(get_character_data=_fake_character_data)
    monkeypatch.setattr("memory.timeindex.get_config_manager", lambda: fake_config_manager)
    monkeypatch.setattr(
        "memory.timeindex.create_engine",
        lambda _connection_string: created_engines.pop(0),
    )

    manager = TimeIndexedMemory(recent_history_manager=None)
    monkeypatch.setattr(manager, "_assert_timeindex_writable", lambda _lanlan_name: None)
    monkeypatch.setattr(
        manager,
        "_ensure_tables_exist_with",
        lambda _engine, _connection_string, _lanlan_name: ensure_calls.append((_lanlan_name, _engine)),
    )
    monkeypatch.setattr(
        manager,
        "_check_and_migrate_schema",
        lambda _engine, _lanlan_name: migrate_calls.append((_lanlan_name, _engine)),
    )

    # 第一次初始化：从 time_store 解析到 old_db_path，engine 缓存
    assert manager._ensure_engine_exists("测试角色") is True
    assert manager.engines["测试角色"] is old_engine
    assert os.path.normcase(str(manager.db_paths["测试角色"])) == os.path.normcase(str(old_db_path))
    assert old_engine.dispose_calls == 0

    # 模拟 memory_dir 漂移——time_store 现在指向 new_db_path
    current_time_store["测试角色"] = str(new_db_path)

    # 第二次 _ensure_engine_exists：cache 命中但 expected 已变；应 dispose + 重建
    assert manager._ensure_engine_exists("测试角色") is True
    assert old_engine.dispose_calls == 1
    assert manager.engines["测试角色"] is new_engine
    assert os.path.normcase(str(manager.db_paths["测试角色"])) == os.path.normcase(str(new_db_path))
    # 新 engine 走完整 writable 初始化（确保表结构在新文件里就位）
    assert ensure_calls[-1] == ("测试角色", new_engine)
    assert migrate_calls[-1] == ("测试角色", new_engine)


def test_timeindexed_short_circuits_when_memory_dir_unchanged(monkeypatch, tmp_path):
    """对偶用例：cached 与 expected 一致时 drift 检测不该误伤——cache 命中
    应仍然短路，不重建 engine。
    """  # noqa: DOCSTRING_CJK
    from memory.timeindex import TimeIndexedMemory

    class _DummyEngine:
        def __init__(self):
            self.dispose_calls = 0

        def dispose(self):
            self.dispose_calls += 1

    db_path = (tmp_path / "测试角色" / "time_indexed.db").resolve()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_text("", encoding="utf-8")

    engine = _DummyEngine()
    created_engines = [engine]
    create_calls: list = []
    ensure_calls: list = []
    migrate_calls: list = []

    fake_config_manager = SimpleNamespace(
        get_character_data=lambda: ({}, {}, {}, {}, {}, {}, {"测试角色": str(db_path)}, {}, {}),
    )
    monkeypatch.setattr("memory.timeindex.get_config_manager", lambda: fake_config_manager)

    def _fake_create_engine(connection_string):
        create_calls.append(connection_string)
        return created_engines.pop(0)

    monkeypatch.setattr("memory.timeindex.create_engine", _fake_create_engine)

    manager = TimeIndexedMemory(recent_history_manager=None)
    monkeypatch.setattr(manager, "_assert_timeindex_writable", lambda _lanlan_name: None)
    monkeypatch.setattr(
        manager,
        "_ensure_tables_exist_with",
        lambda _engine, _connection_string, _lanlan_name: ensure_calls.append(_lanlan_name),
    )
    monkeypatch.setattr(
        manager,
        "_check_and_migrate_schema",
        lambda _engine, _lanlan_name: migrate_calls.append(_lanlan_name),
    )

    assert manager._ensure_engine_exists("测试角色") is True
    assert manager._ensure_engine_exists("测试角色") is True
    assert manager._ensure_engine_exists("测试角色") is True
    # 仅第一次创建 engine + bootstrap，后续短路
    assert len(create_calls) == 1
    assert ensure_calls == ["测试角色"]
    assert migrate_calls == ["测试角色"]
    assert engine.dispose_calls == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_publishing_an_identity_lifts_sidecar_retirement(tmp_path):
    """Creating a character is the explicit "this identity is live" event.

    A name deleted earlier in the process stays retired, and a freshly created
    profile has no memory/<name>/ yet -- so without this lift the sidecar writers
    would drop its startup greeting and early proactive decisions instead of
    creating the directory, and they would stay dropped until some unrelated
    memory writer happened to make the directory.
    """
    from utils.character_memory import (
        asave_characters_with_recent_activation,
        retire_character_runtime_caches,
    )

    class _Config:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

        async def asave_characters(self, _characters):
            return None

    config = _Config()
    with _isolated_sidecar_stores(tmp_path) as stores:
        retire_character_runtime_caches("Reborn")
        assert all("Reborn" in store._retired for store in stores)

        await asave_characters_with_recent_activation(
            config, {"猫娘": {"Reborn": {}}}, "Reborn",
        )

        assert all("Reborn" not in store._retired for store in stores), (
            "publishing an identity must reactivate its sidecar storage"
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_rolled_back_delete_reactivates_the_sidecar_stores(tmp_path):
    """A restored character is live again, so its retirement must be lifted.

    The delete path retires the name in every sidecar store BEFORE removing
    anything. If the operation then rolls back -- a failed memory-server reload,
    a cancellation, maintenance mode -- the files and characters.json come back
    through save_characters, which never reaches the activation helper that
    lifts retirement. A character that had no memory directory yet would then
    keep dropping its startup greeting and anti-repeat decisions, because a
    retired name is refused the lazy directory creation every sibling writer
    gets.
    """
    from main_routers.characters_router import crud
    from utils.character_memory import retire_character_runtime_caches

    class _Config:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

        def save_characters(self, _characters, **_kwargs):
            return None

        def save_character_tombstones_state(self, _state):
            return None

    with _isolated_sidecar_stores(tmp_path) as stores:
        retire_character_runtime_caches("Restored")
        assert all("Restored" in store._retired for store in stores)

        with (
            patch.object(crud, "get_initialize_character_data",
                         return_value=AsyncMock()),
            patch.object(crud, "notify_memory_server_reload",
                         AsyncMock(return_value=True)),
        ):
            errors = await crud._rollback_character_operation(
                _Config(),
                characters_snapshot={"猫娘": {"Restored": {}}},
                memory_snapshot_records=[],
                restored_live_character_names=("Restored",),
                reason="test rollback",
            )

        assert errors == "", errors
        assert all("Restored" not in store._retired for store in stores), (
            "a rolled-back delete left the live character retired"
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_failed_restore_leaves_the_character_retired(tmp_path):
    """The lift is scoped to a restore that actually succeeded.

    If characters.json could not be put back, the name is NOT live again --
    lifting retirement there would let a decision staged mid-delete recreate the
    directory that was just removed, which is the orphan the retirement exists
    to prevent.
    """
    from main_routers.characters_router import crud
    from utils.character_memory import retire_character_runtime_caches

    class _Config:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

        def save_characters(self, _characters, **_kwargs):
            raise OSError("disk full")

        def save_character_tombstones_state(self, _state):
            return None

    with _isolated_sidecar_stores(tmp_path) as stores:
        retire_character_runtime_caches("Doomed")

        with (
            patch.object(crud, "get_initialize_character_data",
                         return_value=AsyncMock()),
            patch.object(crud, "notify_memory_server_reload",
                         AsyncMock(return_value=True)),
        ):
            errors = await crud._rollback_character_operation(
                _Config(),
                characters_snapshot={"猫娘": {"Doomed": {}}},
                memory_snapshot_records=[],
                restored_live_character_names=("Doomed",),
                reason="test rollback",
            )

        assert "characters restore failed" in errors
        assert all("Doomed" in store._retired for store in stores), (
            "retirement was lifted for a character that was never restored"
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_rollback_before_retirement_does_not_evict(tmp_path):
    """Eviction is not a harmless no-op on a name that was never retired.

    The rollback block is shared with failures that happen before the storage
    op ran. Evicting there pops the cache AND advances the sequence fence, so a
    decision recorded while the operation was in flight is destroyed rather
    than merely delayed -- it never reaches disk and it is gone from memory.
    The route passes an EMPTY tuple until the retiring op has returned.
    """
    import memory.anti_repeat_effects as effects_module
    from main_routers.characters_router import crud

    class _Config:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

        def save_characters(self, _characters, **_kwargs):
            return None

        def save_character_tombstones_state(self, _state):
            return None

    previous = effects_module._GLOBAL_STORE
    try:
        store = effects_module.AntiRepeatEffectStore()
        store._config_manager = _Config()
        effects_module._GLOBAL_STORE = store
        (tmp_path / "Busy").mkdir()
        store.record_decision(
            "Busy",
            effects_module.AntiRepeatDecision(
                source="proactive",
                reasons=("bm25",),
                action="block",
                outcome="blocked_initial",
            ),
            now=1_700_000_000.0,
        )
        assert "Busy" in store._cache

        with (
            patch.object(crud, "get_initialize_character_data",
                         return_value=AsyncMock()),
            patch.object(crud, "notify_memory_server_reload",
                         AsyncMock(return_value=True)),
        ):
            await crud._rollback_character_operation(
                _Config(),
                characters_snapshot={"猫娘": {"Busy": {}}},
                memory_snapshot_records=[],
                restored_live_character_names=(),
                reason="rollback before the storage op ran",
            )

        assert "Busy" in store._cache, (
            "a rollback that retired nothing still evicted the live cache"
        )
    finally:
        effects_module._GLOBAL_STORE = previous


@pytest.mark.unit
@pytest.mark.asyncio
async def test_early_delete_failure_tells_the_rollback_nothing_was_retired(tmp_path):
    """The CALL SITE must pass an empty tuple, not just the helper honour one.

    Cancelling during the release happens before delete_character_memory_storage
    has retired anything, so the rollback must not evict: eviction pops the
    cache and advances the sequence fence, destroying a decision recorded while
    the operation was in flight.
    """
    cm = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(cm)

    async def _noop(*_args, **_kwargs):
        return None

    with patch("utils.config_manager._config_manager", cm):
        init_shared_state(
            role_state={},
            steamworks=None,
            templates=None,
            config_manager=cm,
            logger=None,
            initialize_character_data=_noop,
            switch_current_catgirl_fast=_noop,
            init_one_catgirl=_noop,
            remove_one_catgirl=_noop,
        )
        crud = reload_module("main_routers.characters_router.crud")
        characters = cm.load_characters()
        characters.setdefault("猫娘", {})["DeleteMe"] = {"昵称": "DeleteMe"}
        cm.save_characters(characters, bypass_write_fence=True)
        release_started = asyncio.Event()
        finish_release = asyncio.Event()
        rollback = AsyncMock(return_value="")

        async def _release(*_args, **_kwargs):
            release_started.set()
            await finish_release.wait()
            return True

        with patch.object(
            crud, "release_memory_server_character", side_effect=_release,
        ), patch.object(crud, "_rollback_character_operation", rollback):
            operation = asyncio.create_task(crud.delete_catgirl("DeleteMe"))
            await asyncio.wait_for(release_started.wait(), timeout=3)
            operation.cancel()
            await asyncio.sleep(0.05)
            finish_release.set()
            with pytest.raises(asyncio.CancelledError):
                await operation

    assert rollback.await_args.kwargs["restored_live_character_names"] == (), (
        "a rollback from before the storage op claimed a name had been retired"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_rename_rollback_restores_both_sides_of_the_retirement(tmp_path):
    """A rolled-back rename restores the source AND un-does the target.

    The storage op retires the source name and EVICTS the target, lifting any
    retirement an earlier delete of that same name had installed. Undoing the
    rename makes the target not-live again, so it has to go back to retired --
    otherwise a late sidecar flush recreates its directory for an identity that
    does not exist.
    """
    cm = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(cm)

    async def _noop(*_args, **_kwargs):
        return None

    with patch("utils.config_manager._config_manager", cm):
        init_shared_state(
            role_state={},
            steamworks=None,
            templates=None,
            config_manager=cm,
            logger=None,
            initialize_character_data=_noop,
            switch_current_catgirl_fast=_noop,
            init_one_catgirl=_noop,
            remove_one_catgirl=_noop,
        )
        crud = reload_module("main_routers.characters_router.crud")
        characters = cm.load_characters()
        characters.setdefault("猫娘", {})["OldName"] = {"昵称": "OldName"}
        cm.save_characters(characters, bypass_write_fence=True)
        rollback = AsyncMock(return_value="")

        request = SimpleNamespace(
            json=AsyncMock(return_value={"new_name": "NewName"}),
        )
        with patch.object(
            crud, "notify_memory_server_reload", AsyncMock(return_value=False),
        ), patch.object(
            crud, "release_memory_server_character", AsyncMock(return_value=True),
        ), patch.object(crud, "_rollback_character_operation", rollback):
            response = await crud.rename_catgirl("OldName", request)

    assert rollback.await_args is not None, (
        "the route never reached the rollback: %s" % getattr(response, "body", response)
    )

    kwargs = rollback.await_args.kwargs
    assert kwargs["restored_live_character_names"] == ("OldName",)
    assert kwargs["reretired_absent_character_names"] == ("NewName",), (
        "the rename target stayed live after the rename was undone"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_the_rollback_actually_re_retires_the_absent_name(tmp_path):
    """The helper must DO the re-retirement, not merely accept the list.

    Paired with the call-site test that pins which names are passed: this one
    pins that they take effect, so the two together fail if either half is
    removed.
    """
    from main_routers.characters_router import crud

    class _Config:
        memory_dir = tmp_path
        project_memory_dir = tmp_path

        def save_characters(self, _characters, **_kwargs):
            return None

        def save_character_tombstones_state(self, _state):
            return None

    with _isolated_sidecar_stores(tmp_path) as stores:
        assert all("Gone" not in store._retired for store in stores)

        with (
            patch.object(crud, "get_initialize_character_data",
                         return_value=AsyncMock()),
            patch.object(crud, "notify_memory_server_reload",
                         AsyncMock(return_value=True)),
        ):
            await crud._rollback_character_operation(
                _Config(),
                characters_snapshot={"猫娘": {"Back": {}}},
                memory_snapshot_records=[],
                restored_live_character_names=("Back",),
                reretired_absent_character_names=("Gone",),
                reason="rename rollback",
            )

        assert all("Gone" in store._retired for store in stores), (
            "the undone rename target was left live in a sidecar store"
        )
        assert all("Back" not in store._retired for store in stores)


@pytest.mark.unit
def test_a_failed_rename_restores_the_cache_lifecycle(tmp_path):
    """A raise partway through must not strand either name.

    The helper retires the source and evicts the target as its first act. If a
    file move then raises, the caller never fills its rollback tuples -- it
    fills them from this function RETURNING -- so without an inverse here the
    live source stays retired (every later sidecar write dropped, since a
    retired name never creates its directory) and the absent target stays
    reactivated (a late write can recreate an identity never committed).
    """
    import utils.character_memory as character_memory

    cm = _make_config_manager(tmp_path)
    with _isolated_sidecar_stores(tmp_path) as stores:
        # The target name was deleted earlier, so it is retired.
        character_memory.retire_character_runtime_caches("Target")

        def _boom(*_args, **_kwargs):
            raise OSError("cross-device move")

        with patch.object(character_memory, "activate_recent_paths", _boom):
            with pytest.raises(OSError):
                character_memory.rename_character_memory_storage(
                    cm, "Source", "Target",
                )

        assert all("Source" not in store._retired for store in stores), (
            "the live source was left retired after a failed rename"
        )
        assert all("Target" in store._retired for store in stores), (
            "the uncommitted target was left reactivated after a failed rename"
        )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_a_raising_delete_still_tells_the_rollback_it_retired(tmp_path):
    """The storage helper retires as its first act, so a raise still retired.

    The rollback restores the files and the config entry, making the name live
    again -- and a live name left retired drops every later sidecar write. The
    route therefore has to record the retirement on the raise, not only on the
    return.
    """
    cm = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(cm)

    async def _noop(*_args, **_kwargs):
        return None

    with patch("utils.config_manager._config_manager", cm):
        init_shared_state(
            role_state={},
            steamworks=None,
            templates=None,
            config_manager=cm,
            logger=None,
            initialize_character_data=_noop,
            switch_current_catgirl_fast=_noop,
            init_one_catgirl=_noop,
            remove_one_catgirl=_noop,
        )
        crud = reload_module("main_routers.characters_router.crud")
        characters = cm.load_characters()
        characters.setdefault("猫娘", {})["Boom"] = {"昵称": "Boom"}
        cm.save_characters(characters, bypass_write_fence=True)
        rollback = AsyncMock(return_value="")

        def _boom(*_args, **_kwargs):
            raise OSError("file in use")

        with patch.object(
            crud, "release_memory_server_character", AsyncMock(return_value=True),
        ), patch.object(
            crud, "delete_character_memory_storage", _boom,
        ), patch.object(crud, "_rollback_character_operation", rollback):
            response = await crud.delete_catgirl("Boom")

    assert rollback.await_args is not None, (
        "the route never reached the rollback: %s" % getattr(response, "body", response)
    )
    assert rollback.await_args.kwargs["restored_live_character_names"] == ("Boom",), (
        "a delete that raised after retiring told the rollback nothing was retired"
    )


@pytest.mark.unit
def test_sidecar_isolation_does_not_touch_the_process_singletons(tmp_path):
    """The helper must swap instances, not mutate the shared ones.

    Saving the module globals and restoring the same references leaves any
    ``_cache`` / ``_retired`` mutation in place, so a name another test also
    uses gets silently dropped and the suite turns order-dependent. This pins
    the property directly: what the helper yields is NOT the process singleton,
    and writing through it leaves the singleton untouched.
    """
    import memory.anti_repeat as anti_repeat_module
    import memory.anti_repeat_effects as effects_module
    import memory.startup_greeting_history as greeting_module

    outer = (
        effects_module.get_anti_repeat_effect_store(),
        anti_repeat_module.get_anti_repeat_corpus(),
        greeting_module.get_startup_greeting_history(),
    )
    outer_retired = [set(store._retired) for store in outer]
    outer_cached = [set(store._cache) for store in outer]

    with _isolated_sidecar_stores(tmp_path) as stores:
        assert all(
            inner is not shared for inner, shared in zip(stores, outer)
        ), "the helper handed back the process singletons"
        for store in stores:
            store._retired.add("PollutionCanary")
            store._cache["PollutionCanary"] = {}

    assert [set(store._retired) for store in outer] == outer_retired
    assert [set(store._cache) for store in outer] == outer_cached
    assert effects_module._GLOBAL_STORE is outer[0]
    assert anti_repeat_module._GLOBAL_CORPUS is outer[1]
    assert greeting_module._GLOBAL_HISTORY is outer[2]


def test_every_selectable_legacy_root_file_is_also_migrated(tmp_path):
    """A name the panel offers has to have its history moved where readers look.

    Every reader looks in ``memory/<name>/``, and the startup migration is
    the only thing that puts a pre-layout file there. Its map was a second
    copy that had fallen three entries behind the rename path's -- the
    dotted database and both archive files -- so those characters kept a
    history nothing could reach.

    Driven off the DECODER's patterns rather than the migration map, so a
    name the decoder recognises but the migration cannot move fails here.
    Each runs in its own root because two of the patterns share a
    destination. The unconfigured arm is the one that used to be skipped.
    """
    import memory as memory_pkg
    from memory import (
        _LEGACY_ROOT_ENTRY_PATTERNS,
        _legacy_root_entry_owner as legacy_root_entry_owner,
    )

    # Directories are not decoded at all -- see
    # ``test_the_migration_never_moves_a_directory`` -- so the pattern
    # table is already files only.
    file_patterns = list(_LEGACY_ROOT_ENTRY_PATTERNS)
    assert file_patterns, "no selectable file patterns -- the test is inert"

    for index, pattern in enumerate(file_patterns):
        root = tmp_path / f"root{index}"
        root.mkdir()
        legacy = root / pattern.replace("{name}", "Carol")
        legacy.write_text("legacy", encoding="utf-8")

        assert legacy_root_entry_owner(legacy.name) == "Carol", (
            f"the selector does not decode {legacy.name} -- this pattern is "
            "not actually offerable, so the loop is testing the wrong thing"
        )

        # EMPTY names: an owner absent from characters.json is exactly the
        # case the migration used to skip, leaving the file flat forever.
        memory_pkg.migrate_to_character_dirs(str(root), [])

        target = memory_pkg._MIGRATION_MAP.get(pattern)
        assert target is not None, (
            f"{pattern} is offered as Carol's history but the startup "
            "migration has no rule for it"
        )
        moved = root / "Carol" / target
        assert moved.exists(), f"{pattern} was offered but never migrated"
        assert not legacy.exists(), f"{pattern} was copied, not moved"


@pytest.mark.unit
def test_a_rename_fences_the_target_against_its_deleted_identity(tmp_path):
    """A previously deleted target stays fenced until the rename publishes.

    The helper used to EVICT the target as its first act, which lifts the
    retirement left by an earlier delete. Anything still holding that name
    -- an in-flight proactive turn, a session bound to it before the delete
    -- was then free to write for the duration of the move, and both
    outcomes were bad. Its flush creates memory/<target>/, so
    _merge_directories hits its colliding-child pre-flight and raises,
    aborting the rename and stranding an orphan directory; or it stages
    first and flushes after the move, and since staging copies the WHOLE
    payload rather than a delta, the merged history is replaced by that one
    record.

    Retiring instead holds the name shut for the window -- a retired name
    never creates a directory -- and the lift moves to the end, where the
    rename has actually committed.

    The lift has to be the EVICTING one. Publication drops the cache, and
    that is what makes the next write re-read the merged file instead of
    flushing whatever was staged during the window over the top of it.
    """
    import json

    import memory.anti_repeat_effects as effects_module
    import utils.character_memory as character_memory

    cm = _make_config_manager(tmp_path)
    memory_root = Path(cm.memory_dir)

    def detected(path):
        if not path.exists():
            return 0
        payload = json.loads(path.read_text(encoding="utf-8"))
        return sum(
            bucket["counters"]["detected"]
            for bucket in payload["daily_buckets"].values()
        )

    def days(path):
        """Which day buckets are on disk -- the IDENTITY of the content.

        Counting alone cannot tell the merged history from the same number
        of late writes replacing it: staging copies the whole payload, so a
        replacement can carry an identical entry count. The source records
        and the injected ones are deliberately a day apart.
        """
        if not path.exists():
            return set()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return set(payload["daily_buckets"])

    with _isolated_sidecar_stores(memory_root) as (store, corpus, greeting):
        # The fixture hands the stores a SimpleNamespace carrying only
        # memory_dir, while the write path reads app_docs_dir -- and the
        # store swallows the AttributeError, so nothing reaches disk and
        # every assertion below would pass over a file that was never
        # written. Rebind to the real config manager, and prove it took.
        for sidecar in (store, corpus, greeting):
            sidecar._config_manager = cm

        decision = effects_module.AntiRepeatDecision(
            source="proactive",
            reasons=("bm25",),
            action="block",
            outcome="blocked_initial",
        )

        (memory_root / "Source").mkdir(parents=True, exist_ok=True)
        for tick in range(4):
            store.record_decision(
                "Source", decision, now=1_700_000_000.0 + tick * 60
            )
        source_file = memory_root / "Source" / "anti_repeat_effects.json"
        assert detected(source_file) == 4, (
            "the source history never reached disk, so this test would be "
            "asserting over a file that does not exist"
        )

        # The target name was deleted earlier, so it is retired and has no
        # directory of its own.
        character_memory.retire_character_runtime_caches("Target")
        assert not (memory_root / "Target").exists()

        # A turn belonging to the DELETED target identity is still running
        # and records inside the window -- after the lifecycle calls, before
        # the move.
        real_merge = character_memory._merge_directories
        fired = []

        def _record_inside_the_window(source_dir, target_dir):
            # ONCE, and before any root has been merged. There are two
            # memory roots, so this hook runs twice; letting it write on the
            # second pass would be testing something else entirely, because
            # by then the first merge has created the directory and a
            # retired name is allowed to write into one that exists. This
            # test pins the window BEFORE the directory exists, which is the
            # half retirement can cover on its own; the pass after it is
            # what the write fence covers, and
            # ``test_the_rename_fence_covers_the_whole_merge`` pins that.
            if not fired:
                fired.append(True)
                store.record_decision(
                    "Target", decision, now=1_700_100_000.0
                )
            return real_merge(source_dir, target_dir)

        with patch.object(
            character_memory, "_merge_directories", _record_inside_the_window
        ):
            result = character_memory.rename_character_memory_storage(
                cm, "Source", "Target",
            )

        assert fired, "the in-window write never happened"
        assert result["exists_after"]
        target_file = memory_root / "Target" / "anti_repeat_effects.json"
        assert detected(target_file) == 4, (
            "the renamed character lost its merged history to a write from "
            "the identity that used to own the name"
        )
        assert days(target_file) == {"2023-11-14"}, (
            "the entry COUNT survived but the content did not -- a whole-"
            "payload write replaced the merged history with its own"
        )

        # The dual, so the fix cannot pass by stranding the target retired:
        # publication lifts it, and the next write appends to the merged
        # file rather than replacing it.
        assert "Target" not in store._retired
        store.record_decision("Target", decision, now=1_700_200_000.0)
        assert detected(target_file) == 5, (
            "after publication the target either could not write at all, or "
            "flushed a stale cache over the merged file"
        )
        assert days(target_file) == {"2023-11-14", "2023-11-17"}, (
            "the post-publication write replaced the merged history rather "
            "than adding to it"
        )


@pytest.mark.unit
def test_the_rename_fence_covers_the_whole_merge(tmp_path):
    """Retirement stops short of the moment the merge creates the directory.

    A retired name may not CREATE its directory but may write into one that
    exists, so the first merge opens the door for everything after it: a
    write from the identity that used to own the name lands on the history
    just moved in, and staging copies the whole payload, so it replaces it.
    Measured before the fence: four merged entries became two.

    The injected write therefore fires on EVERY merge call, not just the
    first -- there are two memory roots, and the second one is the pass that
    used to get through.
    """
    import json

    import memory.anti_repeat_effects as effects_module
    import utils.character_memory as character_memory

    cm = _make_config_manager(tmp_path)
    memory_root = Path(cm.memory_dir)

    def detected(path):
        if not path.exists():
            return 0
        payload = json.loads(path.read_text(encoding="utf-8"))
        return sum(
            bucket["counters"]["detected"]
            for bucket in payload["daily_buckets"].values()
        )

    def days(path):
        """Which day buckets are on disk -- the IDENTITY of the content.

        Counting alone cannot tell the merged history from the same number
        of late writes replacing it: staging copies the whole payload, so a
        replacement can carry an identical entry count. The source records
        and the injected ones are deliberately a day apart.
        """
        if not path.exists():
            return set()
        payload = json.loads(path.read_text(encoding="utf-8"))
        return set(payload["daily_buckets"])

    with _isolated_sidecar_stores(memory_root) as (store, corpus, greeting):
        for sidecar in (store, corpus, greeting):
            sidecar._config_manager = cm

        decision = effects_module.AntiRepeatDecision(
            source="proactive",
            reasons=("bm25",),
            action="block",
            outcome="blocked_initial",
        )
        (memory_root / "Source").mkdir(parents=True, exist_ok=True)
        for tick in range(4):
            store.record_decision(
                "Source", decision, now=1_700_000_000.0 + tick * 60
            )
        assert detected(memory_root / "Source" / "anti_repeat_effects.json") == 4

        character_memory.retire_character_runtime_caches("Target")

        real_merge = character_memory._merge_directories
        calls = []

        def _record_on_every_pass(source_dir, target_dir):
            calls.append(True)
            store.record_decision(
                "Target", decision, now=1_700_100_000.0 + len(calls)
            )
            return real_merge(source_dir, target_dir)

        with patch.object(
            character_memory, "_merge_directories", _record_on_every_pass
        ):
            character_memory.rename_character_memory_storage(
                cm, "Source", "Target",
            )

        assert len(calls) > 1, (
            "only one merge pass ran, so the pass this test exists for "
            "never happened"
        )
        target_file = memory_root / "Target" / "anti_repeat_effects.json"
        assert detected(target_file) == 4, (
            "a write after the merge created the directory replaced the "
            "history that had just been moved into it"
        )
        assert days(target_file) == {"2023-11-14"}, (
            "the entry COUNT survived but the content did not -- a whole-"
            "payload write replaced the merged history with its own"
        )

        # And the fence is down afterwards, so the character still persists.
        store.record_decision("Target", decision, now=1_700_200_000.0)
        assert detected(target_file) == 5
        assert days(target_file) == {"2023-11-14", "2023-11-17"}, (
            "the post-publication write replaced the merged history rather "
            "than adding to it"
        )


@pytest.mark.unit
def test_a_failed_rename_releases_the_write_fence(tmp_path):
    """A fence that survived a raise would silence the character for good.

    It is process-wide and has no expiry, so a leak is permanent: every
    later sidecar write for that name returns None and the character stops
    persisting, with nothing on disk to show why. That is worse than the
    write the fence exists to stop, which is why the release is in a
    ``finally`` rather than after the last statement of the try.

    Asserted through behaviour and not only the flag: the name is retired by
    the rollback, so this lifts that the way a later publication would and
    then checks a real write lands.
    """
    import json

    import memory.anti_repeat_effects as effects_module
    import utils.character_memory as character_memory
    from utils.character_memory import is_character_write_fenced

    cm = _make_config_manager(tmp_path)
    memory_root = Path(cm.memory_dir)

    with _isolated_sidecar_stores(memory_root) as (store, corpus, greeting):
        for sidecar in (store, corpus, greeting):
            sidecar._config_manager = cm
        (memory_root / "Source").mkdir(parents=True, exist_ok=True)

        def _boom(*_args, **_kwargs):
            raise OSError("cross-device move")

        with patch.object(character_memory, "_merge_directories", _boom):
            with pytest.raises(OSError):
                character_memory.rename_character_memory_storage(
                    cm, "Source", "Target",
                )

        assert not is_character_write_fenced("Target"), (
            "the fence survived the failure and would silence this name for "
            "the life of the process"
        )

        # Behaviour, not just the flag: lift the rollback's retirement the
        # way a later publication would, and a real write has to land.
        character_memory.evict_character_runtime_caches("Target")
        decision = effects_module.AntiRepeatDecision(
            source="proactive",
            reasons=("bm25",),
            action="block",
            outcome="blocked_initial",
        )
        store.record_decision("Target", decision, now=1_700_300_000.0)
        target_file = memory_root / "Target" / "anti_repeat_effects.json"
        assert target_file.exists(), (
            "the character could not persist after a failed rename"
        )
        payload = json.loads(target_file.read_text(encoding="utf-8"))
        assert sum(
            bucket["counters"]["detected"]
            for bucket in payload["daily_buckets"].values()
        ) == 1


@pytest.mark.unit
def test_a_late_source_write_does_not_leave_the_old_directory_behind(tmp_path):
    """The source needs the fence for the same reason the target does.

    Retirement refuses to CREATE a directory but permits writing into one
    that exists -- and the source directory exists for the whole merge. On
    the child-by-child path a write for the old name can recreate a file
    after the children are moved and before ``_merge_directories`` rmdir()s
    the source. That rmdir swallows its failure, so the rename reports
    success while memory/<old_name>/ survives with content and the
    renamed-away identity still looks like it has memory.

    The target must already exist, or the merge takes the whole-directory
    move instead and never reaches the rmdir at all.
    """
    import memory.anti_repeat_effects as effects_module
    import utils.character_memory as character_memory

    cm = _make_config_manager(tmp_path)
    memory_root = Path(cm.memory_dir)

    with _isolated_sidecar_stores(memory_root) as (store, corpus, greeting):
        for sidecar in (store, corpus, greeting):
            sidecar._config_manager = cm

        decision = effects_module.AntiRepeatDecision(
            source="proactive",
            reasons=("bm25",),
            action="block",
            outcome="blocked_initial",
        )

        # The source carries one file, and the TARGET already exists with a
        # non-colliding one, which is what forces the child-by-child path.
        (memory_root / "Source").mkdir(parents=True)
        (memory_root / "Source" / "facts.json").write_text(
            "[1]", encoding="utf-8"
        )
        (memory_root / "Target").mkdir()
        (memory_root / "Target" / "persona.json").write_text(
            "[2]", encoding="utf-8"
        )

        real_move = character_memory._move_path
        fired = []

        def _write_for_the_old_name_after_the_move(source_path, target_path):
            moved = real_move(source_path, target_path)
            if not fired:
                fired.append(True)
                # The old identity is still in flight and records now --
                # after its children moved, before the rmdir.
                store.record_decision(
                    "Source", decision, now=1_700_000_000.0
                )
            return moved

        with patch.object(
            character_memory, "_move_path", _write_for_the_old_name_after_the_move
        ):
            character_memory.rename_character_memory_storage(
                cm, "Source", "Target",
            )

        assert fired, "the in-flight write never happened"
        assert not (memory_root / "Source").exists(), (
            "the renamed-away identity kept a directory, so it still looks "
            "like it has memory: %s"
            % sorted(
                q.name for q in (memory_root / "Source").iterdir()
            )
        )
        # The dual: the rename still did its job.
        assert (memory_root / "Target" / "facts.json").read_text(
            encoding="utf-8"
        ) == "[1]"
        assert (memory_root / "Target" / "persona.json").read_text(
            encoding="utf-8"
        ) == "[2]"

        # The fence has to come DOWN for the source too. After the rename it
        # is retired, so its writes are refused for that reason alone and a
        # leak hides -- until the name is rescued back, when a fence still up
        # would silence it for the life of the process.
        from utils.character_memory import is_character_write_fenced

        assert not is_character_write_fenced("Source")
        character_memory.evict_character_runtime_caches("Source")
        (memory_root / "Source").mkdir(exist_ok=True)
        store.record_decision("Source", decision, now=1_700_100_000.0)
        assert (
            memory_root / "Source" / "anti_repeat_effects.json"
        ).exists(), (
            "a rescued source name could not persist -- the rename left its "
            "write fence up"
        )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("restored_names", [("Restored",), ()],
                         ids=["retired-something", "retired-nothing"])
async def test_a_stale_flush_cannot_overwrite_what_the_rollback_just_restored(
    tmp_path, restored_names,
):
    """Retirement does not cover the window the restore opens.

    A retired name may write into a directory that already exists -- it only
    refuses to CREATE one. The rollback recreates memory/<name>/ while the name
    is still retired, and the ``save_characters`` after it is a real await, so a
    snapshot staged before the delete lands on the freshly restored file and
    replaces the whole history with the single decision it was holding.

    The flush is invoked through ``_flush_snapshot`` directly, which is where
    the detached path (``flush_staged_detached`` -> ``aflush_staged`` ->
    ``to_thread``) ends up, so the ordering is deterministic rather than racing
    a worker thread.

    Both arms of the parametrization matter. "retired-nothing" is what
    every caller passes when the storage op raised before retiring
    anything -- the commonest way this rollback is reached -- and scoping
    the fence to the lifecycle tuples made it inert on exactly that path.
    """
    import shutil

    import memory.anti_repeat_effects as effects_module
    from main_routers.characters_router import crud
    from utils.character_memory import (
        is_character_write_fenced,
        retire_character_runtime_caches,
    )

    cm = _make_config_manager(tmp_path)
    bootstrap_local_cloudsave_environment(cm)
    memory_root = Path(cm.memory_dir)

    with _isolated_sidecar_stores(memory_root) as (store, corpus, greeting):
        for sidecar_store in (store, corpus, greeting):
            sidecar_store._config_manager = cm

        decision = effects_module.AntiRepeatDecision(
            source="proactive",
            reasons=("bm25",),
            action="block",
            outcome="blocked_initial",
        )
        sidecar = memory_root / "Restored" / effects_module._SIDECAR_FILENAME

        (memory_root / "Restored").mkdir(parents=True)
        for tick in range(3):
            store.record_decision(
                "Restored", decision, now=1_700_000_000.0 + tick,
            )
        restored_bytes = sidecar.read_bytes()

        # The delete, in the order delete_character_memory_storage uses.
        retire_character_runtime_caches("Restored")
        shutil.rmtree(memory_root / "Restored")

        # A turn already in flight stages now. The cache was dropped and there
        # is no file to read, so this snapshot carries ONE decision -- which is
        # what makes it destructive rather than merely redundant.
        staged = store.stage_decision(
            "Restored", decision, now=1_700_000_009.0,
        )
        assert staged is not None

        def _restore(_records):
            (memory_root / "Restored").mkdir(parents=True)
            sidecar.write_bytes(restored_bytes)

        flushed = []

        def _save_characters(_characters, **_kwargs):
            # Inside the await, after the restore and before the lifecycle
            # calls -- exactly where a detached flush lands.
            store._flush_snapshot(*staged)
            flushed.append(True)

        with (
            patch.object(cm, "save_characters", _save_characters),
            patch.object(crud, "_restore_snapshot_paths", _restore),
            patch.object(
                crud, "get_initialize_character_data", return_value=AsyncMock()
            ),
            patch.object(
                crud, "notify_memory_server_reload", AsyncMock(return_value=True)
            ),
        ):
            errors = await crud._rollback_character_operation(
                cm,
                characters_snapshot={"CAT": {"Restored": {}}},
                memory_snapshot_records=[],
                restored_live_character_names=restored_names,
                fenced_character_names=("Restored",),
                reason="restore then lifecycle",
            )

        assert flushed, "the stale flush never ran, so this proves nothing"
        assert sidecar.read_bytes() == restored_bytes, (
            "a snapshot staged before the delete overwrote the restored history"
        )
        # The rollback still did its job, and did not report the refusal as a
        # failure of its own.
        assert errors == "", errors

        # The fence comes down again, so persistence resumes right after.
        # Without this the guard would also pass if the fence LEAKED, which the
        # fence's own docstring calls worse than the write it prevents.
        assert not is_character_write_fenced("Restored")
        store.record_decision("Restored", decision, now=1_700_000_020.0)
        assert sidecar.read_bytes() != restored_bytes, (
            "writes never resumed after the rollback -- the fence leaked"
        )
