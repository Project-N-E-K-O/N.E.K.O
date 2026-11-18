"""Chat sync reaches its host only through services.chat_sync.host.

Every owner installs late-binding callables at import, so patching the owner
still reaches the chat sync consumer, and a missing capability fails closed.
Only synthetic configs, sessions and credentials are used; no real token, lock
file or profile is touched.
"""

import asyncio
import contextlib
import copy
import hashlib
import importlib
import itertools
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.chat_sync_writer_fixture import isolated_chat_sync_writer  # noqa: F401

from services.chat_sync import host
from services.chat_sync.profile_protocol import encode_character_profile
from services.chat_sync.profile_sync import DesktopProfileAdapter, export_profile
from services.chat_sync.runtime import _saved_session_commit_guard, _saved_session_presence


UID = "a" * 32
ORIGIN = "https://community.project-neko.cn"
ROOT = Path(__file__).resolve().parents[2]

OWNERS = {
    "_saved_session_snapshot": "main_routers.card_drop_router",
    "_saved_session_writer_fence": "main_routers.card_drop_router",
    "_resolve_saved_oauth_status": "main_routers.community_oauth",
    "_get_session_manager": "main_routers.shared_state",
    "_get_init_one_catgirl": "main_routers.shared_state",
    "_conversation_busy": "main_routers.shared_state",
    "_rename_character": "main_routers.characters_router.crud",
}


def fingerprint(token):
    """Mirror the runtime's session fingerprint for a synthetic bearer."""
    return hashlib.sha256(json.dumps({"token": token, "origin": ORIGIN}, sort_keys=True,
                                     separators=(",", ":")).encode()).hexdigest()


class Config:
    """One synthetic saved role; no singleton or real storage root."""

    def __init__(self):
        self.data = {"当前猫娘": "Synthetic", "猫娘": {"Synthetic": {
            "设定": "kept", "昵称": "nick", "_reserved": {"character_uid": UID}}}}
        self.saves = []

    async def aload_characters(self):
        return copy.deepcopy(self.data)

    def save_characters(self, data):
        self.data = copy.deepcopy(data)
        self.saves.append(copy.deepcopy(data))


def approved_status(config, new_name):
    """An approved remote head that differs from the saved role only by name."""
    current = export_profile("Synthetic", config.data["猫娘"]["Synthetic"])
    return {"local_hash": hashlib.sha256(encode_character_profile(current)).hexdigest(),
            "head": {**copy.deepcopy(current), "name": new_name}}


async def commit_now(operation, *args):
    """Apply a local write immediately; the real session fence is tested elsewhere."""
    return operation(*args)


async def production_runtime(tmp_path, monkeypatch, data=None):
    """Build the runtime through the production factory against disposable paths."""
    from services.chat_sync import runtime as module

    async def characters():
        return copy.deepcopy(data) if data is not None else {"猫娘": {}}

    config = SimpleNamespace(app_docs_dir=tmp_path / "selected", local_state_dir=tmp_path / "anchor",
                             aload_characters=characters)
    monkeypatch.setattr(module, "_runtimes", {})
    monkeypatch.setattr(module, "_runtime_lock", asyncio.Lock())
    return await module.get_desktop_chat_sync_runtime(config)


# ---- not installed -------------------------------------------------------------


@pytest.mark.parametrize("slot,use", [
    ("_saved_session_snapshot", host.saved_session_snapshot),
    ("_saved_session_writer_fence", host.saved_session_writer_fence),
    ("_resolve_saved_oauth_status", host.resolve_saved_oauth_status),
    ("_get_session_manager", host.get_session_manager),
    ("_get_init_one_catgirl", host.get_init_one_catgirl),
    ("_conversation_busy", lambda: host.conversation_busy(None)),
    ("_rename_character", lambda: host.rename_character("Old", "New", sync_commit=None)),
])
def test_every_missing_capability_raises_a_runtime_error_naming_it(monkeypatch, slot, use):
    monkeypatch.setattr(host, slot, None)
    with pytest.raises(host.CapabilityNotInstalled, match=slot.lstrip("_")) as raised:
        use()
    assert isinstance(raised.value, RuntimeError)


def test_installers_reject_non_callables_without_partial_installation(monkeypatch):
    for slot in OWNERS:
        monkeypatch.setattr(host, slot, getattr(host, slot))
    before = {slot: getattr(host, slot) for slot in OWNERS}
    with pytest.raises(TypeError, match="saved_session_writer_fence"):
        host.install_saved_session_store(saved_session_snapshot=lambda: None, saved_session_writer_fence=None)
    with pytest.raises(TypeError, match="conversation_busy"):
        host.install_conversation_registry(get_session_manager=dict, get_init_one_catgirl=AsyncMock, conversation_busy=True)
    with pytest.raises(TypeError, match="resolve_saved_oauth_status"):
        host.install_oauth_status_resolver(resolve_saved_oauth_status={"logged_in": True})
    with pytest.raises(TypeError, match="rename_character"):
        host.install_rename_transaction(rename_character={"success": True})
    assert {slot: getattr(host, slot) for slot in OWNERS} == before


@pytest.mark.asyncio
async def test_missing_saved_session_snapshot_reads_as_no_observable_login(monkeypatch):
    monkeypatch.setattr(host, "_saved_session_snapshot", lambda: {"access_token": "synthetic-a", "base_url": ORIGIN})
    assert await _saved_session_presence() == fingerprint("synthetic-a")
    monkeypatch.setattr(host, "_saved_session_snapshot", None)
    assert await _saved_session_presence() is None


@pytest.mark.parametrize("missing", ["_saved_session_writer_fence", "_saved_session_snapshot"])
def test_missing_saved_session_capability_fails_the_commit_guard_closed(monkeypatch, missing):
    fenced = []
    monkeypatch.setattr(host, "_saved_session_writer_fence", lambda: contextlib.nullcontext(fenced.append("fence")))
    monkeypatch.setattr(host, "_saved_session_snapshot", lambda: {"access_token": "synthetic-a", "base_url": ORIGIN})
    with _saved_session_commit_guard(fingerprint("synthetic-a")):
        assert fenced == ["fence"]
    monkeypatch.setattr(host, missing, None)
    with pytest.raises(ValueError, match="^chat_sync_session_unavailable$"):
        with _saved_session_commit_guard(fingerprint("synthetic-a")):
            pytest.fail("A local commit ran without the credential store owner")


@pytest.mark.asyncio
async def test_missing_oauth_resolver_is_retryable_never_a_logout(tmp_path, monkeypatch):
    runtime = await production_runtime(tmp_path, monkeypatch)
    try:
        monkeypatch.setattr(host, "_resolve_saved_oauth_status", None)
        with pytest.raises(ValueError, match="^chat_sync_session_unavailable$"):
            await runtime.session_provider()
        with pytest.raises(ValueError, match="^chat_sync_session_unavailable$"):
            await runtime._session()
    finally:
        await runtime.close()


@pytest.mark.parametrize("missing", ["_get_session_manager", "_conversation_busy"])
def test_busy_never_reports_idle_without_the_conversation_registry(monkeypatch, missing):
    monkeypatch.setattr(host, "_get_session_manager", lambda: {})
    monkeypatch.setattr(host, "_conversation_busy", lambda manager: False)
    adapter = DesktopProfileAdapter(Config())
    assert adapter.busy("Synthetic") is False
    monkeypatch.setattr(host, missing, None)
    with pytest.raises(RuntimeError, match="is not installed"):
        adapter.busy("Synthetic")


@pytest.mark.asyncio
async def test_history_hint_without_the_session_registry_raises_for_the_notifier_to_absorb(tmp_path, monkeypatch):
    runtime = await production_runtime(tmp_path, monkeypatch, {"猫娘": {"Synthetic": {"_reserved": {"character_uid": UID}}}})
    try:
        monkeypatch.setattr(host, "_get_session_manager", None)
        with pytest.raises(RuntimeError, match="is not installed"):
            await runtime.history_changed(UID)
    finally:
        await runtime.close()


@pytest.mark.asyncio
async def test_profile_refresh_without_the_runtime_refresh_raises_like_uninitialized_shared_state(monkeypatch):
    monkeypatch.setattr(host, "_get_session_manager", lambda: {})
    monkeypatch.setattr(host, "_conversation_busy", lambda manager: False)
    monkeypatch.setattr(host, "_get_init_one_catgirl", None)
    config = Config()
    with pytest.raises(RuntimeError, match="is not installed"):
        await DesktopProfileAdapter(config).apply(UID, approved_status(config, "Synthetic"), commit_now)


@pytest.mark.asyncio
async def test_rename_without_the_owner_transaction_fails_closed_before_any_mutation(monkeypatch):
    import utils.external_route_registry as routes

    refresh = AsyncMock()
    monkeypatch.setattr(host, "_get_session_manager", lambda: {})
    monkeypatch.setattr(host, "_conversation_busy", lambda manager: False)
    monkeypatch.setattr(host, "_get_init_one_catgirl", lambda: refresh)
    monkeypatch.setattr(host, "_rename_character", None)
    fences = []
    monkeypatch.setattr(routes, "begin_character_mutation", lambda *names: fences.append(("begin", names)))
    monkeypatch.setattr(routes, "end_character_mutation", lambda *names: fences.append(("end", names)))
    config, commit = Config(), AsyncMock()
    adapter = DesktopProfileAdapter(config)
    # Name validation still comes first.
    with pytest.raises(ValueError, match="^character_profile_name_invalid$"):
        await adapter.apply(UID, approved_status(config, "../unsafe"), commit, confirm_rename=True)
    with pytest.raises(ValueError, match="^character_profile_rename_failed$"):
        await adapter.apply(UID, approved_status(config, "Renamed"), commit, confirm_rename=True)
    assert fences == []
    commit.assert_not_called()
    refresh.assert_not_awaited()
    assert not config.saves and "Renamed" not in config.data["猫娘"]


# ---- installed by the owners, late-bound ---------------------------------------


@pytest.mark.asyncio
async def test_saved_session_capabilities_follow_patches_of_the_card_drop_router(monkeypatch):
    from main_routers import card_drop_router as credentials

    assert host._saved_session_snapshot.__module__ == credentials.__name__
    assert host._saved_session_writer_fence.__module__ == credentials.__name__
    snapshot = {"access_token": "synthetic-a", "base_url": ORIGIN}
    fenced = []

    @contextlib.contextmanager
    def locks(paths):
        fenced.append(("enter", paths))
        yield
        fenced.append(("exit", paths))

    monkeypatch.setattr(credentials, "_desktop_session_snapshot", lambda: dict(snapshot))
    monkeypatch.setattr(credentials, "_social_session_locks", locks)
    monkeypatch.setattr(credentials, "_social_session_paths", lambda: ["synthetic-session-path"])
    assert await _saved_session_presence() == fingerprint("synthetic-a")
    with _saved_session_commit_guard(fingerprint("synthetic-a")):
        assert fenced == [("enter", ["synthetic-session-path"])]
    assert fenced[-1] == ("exit", ["synthetic-session-path"])
    snapshot["access_token"] = "synthetic-b"
    with pytest.raises(ValueError, match="^chat_sync_session_changed$"):
        with _saved_session_commit_guard(fingerprint("synthetic-a")):
            pytest.fail("A changed saved session allowed an old response commit")


@pytest.mark.asyncio
async def test_session_provider_joins_the_patched_community_oauth_resolver(tmp_path, monkeypatch):
    from main_routers import community_oauth

    assert host._resolve_saved_oauth_status.__module__ == community_oauth.__name__
    status = {"logged_in": True, "snapshot": {"access_token": "synthetic-a", "base_url": ORIGIN}}
    resolve = AsyncMock(return_value=status)
    monkeypatch.setattr(community_oauth, "resolve_saved_oauth_status", resolve)
    runtime = await production_runtime(tmp_path, monkeypatch)
    try:
        assert await runtime.session_provider() is status
        assert await runtime._session() == ("synthetic-a", fingerprint("synthetic-a"))
        assert resolve.await_count == 2
    finally:
        await runtime.close()


def test_conversation_registry_follows_shared_state_and_session_state(monkeypatch):
    from main_logic import session_state
    from main_routers import shared_state

    for slot in ("_get_session_manager", "_get_init_one_catgirl", "_conversation_busy"):
        assert getattr(host, slot).__module__ == shared_state.__name__
    adapter = DesktopProfileAdapter(Config())
    # Before init_shared_state() chat sync sees the registry's own error.
    monkeypatch.setitem(shared_state._state, "session_manager", shared_state._UNSET)
    monkeypatch.setitem(shared_state._state, "init_one_catgirl", shared_state._UNSET)
    with pytest.raises(RuntimeError, match="'session_manager' is not initialized"):
        adapter.busy("Synthetic")
    with pytest.raises(RuntimeError, match="'init_one_catgirl' is not initialized"):
        host.get_init_one_catgirl()
    manager = SimpleNamespace(is_starting=True)
    asked = []
    monkeypatch.setattr(shared_state, "get_session_manager", lambda: {"Synthetic": manager})
    assert adapter.busy("Synthetic") is True
    assert adapter.busy("Absent") is False
    monkeypatch.setattr(session_state, "conversation_busy", lambda value: asked.append(value) or True)
    assert adapter.busy("Synthetic") is True and adapter.busy("Absent") is True
    assert asked == [manager, None]
    refresh = AsyncMock()
    monkeypatch.setattr(shared_state, "get_init_one_catgirl", lambda: refresh)
    assert host.get_init_one_catgirl() is refresh


@pytest.mark.asyncio
async def test_confirmed_rename_reaches_the_patched_crud_transaction_and_refresh(monkeypatch):
    from main_routers import shared_state
    from main_routers.characters_router import crud

    assert host._rename_character.__module__ == crud.__name__
    config, refresh, calls = Config(), AsyncMock(), []

    async def rename(old_name, new_name, *, sync_commit):
        calls.append((old_name, new_name, sync_commit))

        def persist():
            moved = copy.deepcopy(config.data)
            moved["猫娘"][new_name] = moved["猫娘"].pop(old_name)
            config.save_characters(moved)
        await sync_commit(persist)
        return {"success": True}

    monkeypatch.setattr(shared_state, "get_session_manager", lambda: {})
    monkeypatch.setattr(shared_state, "get_init_one_catgirl", lambda: refresh)
    monkeypatch.setattr(crud, "_rename_catgirl_serialized", rename)
    assert await DesktopProfileAdapter(config).apply(UID, approved_status(config, "Renamed"), commit_now,
                                                     confirm_rename=True) is True
    assert calls == [("Synthetic", "Renamed", commit_now)]
    assert list(config.data["猫娘"]) == ["Renamed"]
    refresh.assert_awaited_once_with("Renamed", is_new=False)


@pytest.mark.asyncio
async def test_rename_capability_survives_reloading_crud(monkeypatch):
    crud = importlib.reload(importlib.import_module("main_routers.characters_router.crud"))
    assert host._rename_character.__module__ == crud.__name__
    rename = AsyncMock(return_value={"success": True})
    monkeypatch.setattr(crud, "_rename_catgirl_serialized", rename)
    marker = object()
    assert await host.rename_character("Synthetic", "Renamed", sync_commit=marker) == {"success": True}
    rename.assert_awaited_once_with("Synthetic", "Renamed", sync_commit=marker)


_WIRING_PROBE = '''
import json
import os
import sys
import tempfile
from pathlib import Path

# Same isolation as tests/conftest.py, installed before any product import: the
# app import would otherwise run storage migration against the real runtime root.
os.environ.pop("NEKO_STORAGE_SELECTED_ROOT", None)
from utils import logger_config
logger_config.RobustLoggerConfig._get_documents_directory = lambda self: Path(tempfile.mkdtemp())
from utils.config_manager import ConfigManager
from tests import real_root_isolation
real_root_isolation.install(ConfigManager)

owners = json.loads(sys.argv[1])
preloaded = sorted(module for module in set(owners.values()) if module in sys.modules)
from app.main_server import web_app  # noqa: F401
from services.chat_sync import host
installed = {slot: getattr(getattr(host, slot), "__module__", None) for slot in owners}
print(json.dumps({"preloaded": preloaded, "installed": installed}))
'''


def test_importing_the_main_server_app_installs_every_capability_from_its_owner():
    # Host slots are process-global and other test modules import the owners at
    # collection time, so only a fresh interpreter proves web_app's own wiring.
    result = subprocess.run([sys.executable, "-c", _WIRING_PROBE, json.dumps(OWNERS)],
                            cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr[-4000:]
    report = json.loads(result.stdout.strip().splitlines()[-1])
    assert report["preloaded"] == []
    assert report["installed"] == OWNERS


# ---- the busy predicate moved verbatim -----------------------------------------


def _inline_busy(manager):
    """The expression DesktopProfileAdapter.busy evaluated inline before the host port."""
    from main_logic.session_state import ProactivePhase, session_reply_in_progress

    state = getattr(manager, "state", None)
    return bool(manager and (getattr(manager, "is_starting", False)
                             or session_reply_in_progress(getattr(manager, "session", None))
                             or getattr(manager, "_is_voice_playing", lambda: False)()
                             or getattr(state, "phase", ProactivePhase.IDLE) is not ProactivePhase.IDLE))


def _managers():
    """Every combination of the four signals, including absent attributes."""
    from main_logic.session_state import ProactivePhase

    absent = object()
    sessions = [absent, None, SimpleNamespace(_is_responding=False), SimpleNamespace(_is_responding=True),
                SimpleNamespace(_is_responding=False, _active_response_generation=3),
                SimpleNamespace(_is_responding=False, _completion_pending_generation=0),
                SimpleNamespace(_is_responding=False, _active_response_generation="3")]
    states = [absent, None, SimpleNamespace(), *(SimpleNamespace(phase=phase) for phase in ProactivePhase)]
    yield None
    yield {}
    for starting, session, voice, state in itertools.product([absent, False, True], sessions, [absent, False, True], states):
        manager = SimpleNamespace()
        if starting is not absent:
            manager.is_starting = starting
        if session is not absent:
            manager.session = session
        if voice is not absent:
            manager._is_voice_playing = lambda voice=voice: voice
        if state is not absent:
            manager.state = state
        yield manager


def test_conversation_busy_truth_table_equals_the_old_inline_expression():
    from main_logic import session_state

    assert "conversation_busy" in session_state.__all__
    outcomes = []
    for manager in _managers():
        expected = _inline_busy(manager)
        assert session_state.conversation_busy(manager) is expected, manager
        outcomes.append(expected)
    assert True in outcomes and False in outcomes


@pytest.mark.asyncio
@pytest.mark.parametrize("selection", ["other", "current", "last", "last-same-name"])
async def test_remote_deletion_reuses_crud_and_recovers_a_valid_local_selection(monkeypatch, selection):
    crud = importlib.import_module("main_routers.characters_router.crud")
    config = Config()
    config.asave_characters = AsyncMock(side_effect=config.save_characters)
    config.get_default_characters = lambda: {"猫娘": {"Default": {"设定": "default only", "_reserved": {}}}}
    if selection == "last-same-name":
        config.data["猫娘"]["Default"] = config.data["猫娘"].pop("Synthetic")
        config.data["当前猫娘"] = "Default"
    if selection in {"other", "current"}:
        config.data["猫娘"]["Other"] = {"_reserved": {"character_uid": "b" * 32}}
    if selection == "other":
        config.data["当前猫娘"] = "Other"
    old_name = "Default" if selection == "last-same-name" else "Synthetic"
    monkeypatch.setattr(crud, "get_config_manager", lambda: config)
    socket = SimpleNamespace(send_text=AsyncMock())
    managers = {old_name: SimpleNamespace(websocket=socket)}
    monkeypatch.setattr(crud, "get_session_manager", lambda: managers)
    monkeypatch.setattr(host, "_conversation_busy", lambda _manager: False)
    monkeypatch.setattr(crud, "is_character_lifecycle_locked", lambda _name: False)
    switch, init = AsyncMock(), AsyncMock()
    monkeypatch.setattr(crud, "get_switch_current_catgirl_fast", lambda: switch)
    monkeypatch.setattr(crud, "get_init_one_catgirl", lambda: init)
    monkeypatch.setattr(crud, "force_disable_agent_for_character_switch", AsyncMock())
    deleted = []
    async def delete(name):
        assert config.data["当前猫娘"] != name
        deleted.append(name)
        managers.pop(name)
        config.data["猫娘"].pop(name)
        return {"success": True}
    monkeypatch.setattr(crud, "_delete_catgirl_by_name_serialized", delete)
    guard = AsyncMock()
    assert await DesktopProfileAdapter(config).delete(UID, guard) is True
    assert deleted == [old_name]
    assert config.data["当前猫娘"] in config.data["猫娘"]
    assert len(config.data["猫娘"]) == 1
    if selection != "other":
        socket.send_text.assert_awaited_once()
        assert json.loads(socket.send_text.call_args.args[0]) == {"type": "catgirl_switched",
            "old_catgirl": old_name, "new_catgirl": config.data["当前猫娘"]}
    else:
        socket.send_text.assert_not_awaited()
    assert guard.await_count >= 2
    if selection.startswith("last"):
        raw = config.data["猫娘"][config.data["当前猫娘"]]
        from utils.config_manager import get_character_uid
        assert get_character_uid(raw) != UID
        assert get_character_uid(raw) is not None
        assert raw["设定"] == "default only"
        init.assert_awaited_once_with(config.data["当前猫娘"], is_new=False)
    else:
        init.assert_not_awaited()


@pytest.mark.asyncio
async def test_remote_deletion_waits_for_busy_role_and_rechecks_authority_inside_lifecycle_lock(monkeypatch):
    crud = importlib.import_module("main_routers.characters_router.crud")
    config = Config()
    monkeypatch.setattr(crud, "get_config_manager", lambda: config)
    monkeypatch.setattr(crud, "get_session_manager", dict)
    monkeypatch.setattr(host, "_conversation_busy", lambda _manager: True)
    delete = AsyncMock()
    monkeypatch.setattr(crud, "_delete_catgirl_by_name_serialized", delete)
    assert await host.delete_character(UID, AsyncMock()) is False
    delete.assert_not_awaited()
    assert config.saves == []
    with pytest.raises(ValueError, match="session_changed"):
        await host.delete_character(UID, AsyncMock(side_effect=ValueError("session_changed")))
    delete.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["result", "error", "cancel", "cleanup"])
async def test_remote_deletion_failure_restores_current_role_and_discards_only_its_new_default(monkeypatch, failure):
    crud = importlib.import_module("main_routers.characters_router.crud")
    config = Config()
    original = copy.deepcopy(config.data)
    restoring, release = asyncio.Event(), asyncio.Event()
    async def save(data):
        if failure == "cancel" and data.get("当前猫娘") == "Synthetic":
            restoring.set()
            await release.wait()
        config.save_characters(data)
    config.asave_characters = AsyncMock(side_effect=save)
    config.get_default_characters = lambda: {"猫娘": {"Default": {"_reserved": {}}}}
    monkeypatch.setattr(crud, "get_config_manager", lambda: config)
    monkeypatch.setattr(crud, "get_session_manager", dict)
    monkeypatch.setattr(host, "_conversation_busy", lambda _manager: False)
    monkeypatch.setattr(crud, "is_character_lifecycle_locked", lambda _name: False)
    monkeypatch.setattr(crud, "get_switch_current_catgirl_fast", lambda: AsyncMock())
    monkeypatch.setattr(crud, "get_init_one_catgirl", lambda: AsyncMock())
    remove = AsyncMock(side_effect=RuntimeError("secondary cleanup failure") if failure == "cleanup" else None)
    monkeypatch.setattr(crud, "get_remove_one_catgirl", lambda: remove)
    monkeypatch.setattr(crud, "force_disable_agent_for_character_switch", AsyncMock())
    error = asyncio.CancelledError() if failure == "cancel" else RuntimeError("original deletion failure")
    monkeypatch.setattr(crud, "_delete_catgirl_by_name_serialized", AsyncMock(
        return_value={"success": False}, side_effect=None if failure == "result" else error))
    task = asyncio.create_task(host.delete_character(UID, AsyncMock()))
    if failure == "cancel":
        await asyncio.wait_for(restoring.wait(), timeout=2)
        for _ in range(2):
            task.cancel()
            await asyncio.sleep(0)
        release.set()
    if failure == "result":
        assert await task is False
    else:
        with pytest.raises(type(error), match=None if failure == "cancel" else "original deletion failure"):
            await task
    assert config.data == original
    remove.assert_awaited_once_with("Default")
