"""Follow-up contracts for the per-character language preference endpoints.

Covers the four defects found while reviewing PR #2708 after it merged:
transaction scope, the unset-live-locale judgement, conflict reporting, and
request-body parsing outside the shared characters.json lock.
"""

import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.memory_server import locale_state
from main_logic.core import notify as core_notify
from utils import language_utils as core_language_utils
from main_routers.characters_router import cards as characters_cards
from main_routers.characters_router import language_preference as preference_router
from utils.character_memory import character_config_mutation_lock


PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _install_language_preference_stubs(
    monkeypatch,
    *,
    manager,
    changed: bool,
    durable_after: str | None = None,
):
    """Wire the minimal seams apply_character_language_preference depends on.

    ``durable_after`` is what the post-reconciliation freshness GET reports;
    ``None`` means "still whatever this request just wrote".
    """
    calls: list = []
    config_manager = SimpleNamespace(memory_dir="unused")

    async def load_character(name):
        calls.append(("load", name))
        return config_manager, {"猫娘": {name: {}}}

    async def request_locale(method, name, *, language=None):
        calls.append(("persist", method, name, language))
        if method == "GET":
            return {"success": True, "language": durable_after}
        return {
            "success": True,
            "language": language,
            "previous_language": "en" if changed else language,
            "changed": changed,
        }

    async def clear_recent(_config_manager, name, *, expected_generation):
        assert expected_generation
        calls.append(("clear_recent", name))

    class SessionManager:
        def get(self, _name):
            return manager

    monkeypatch.setattr(preference_router, "_load_existing_character", load_character)
    monkeypatch.setattr(preference_router, "_request_memory_prompt_locale", request_locale)
    monkeypatch.setattr(preference_router, "_clear_character_recent_history", clear_recent)
    monkeypatch.setattr(preference_router, "get_session_manager", SessionManager)
    return calls


class _IdleManager:
    """An idle manager that never received a locale of its own."""

    is_active = False
    is_starting = False
    session = None

    def __init__(self):
        self.user_language = None
        self._user_language_explicit = False
        self.settled = []

    def set_user_language(self, language):
        self.user_language = language
        self._user_language_explicit = True

    async def settle_session_memory_if_idle(self, callback):
        self.settled.append(callback)
        await callback()
        return True

    def reset_session_start_circuit(self):
        pass


async def test_unset_live_locale_never_forces_isolation(monkeypatch):
    """An unset manager locale carries no evidence about the rendered history.

    Startup builds a manager per character with ``user_language=None``; such a
    manager sends no locale to /new_dialog, so existing context was rendered in
    the character's own durable preference regardless of the process locale.
    Re-selecting the already-durable language must therefore stay side-effect
    free even when the global locale differs from it.
    """
    manager = _IdleManager()
    calls = _install_language_preference_stubs(
        monkeypatch, manager=manager, changed=False,
    )
    monkeypatch.setattr(
        core_language_utils, "get_global_language_full", lambda: "en",
    )

    result = await preference_router.apply_character_language_preference("Mimi", "ja")

    assert result["changed"] is False
    assert result["recent_history_cleared"] is False
    assert result["session_reset"] is False
    assert not any(call[0] == "clear_recent" for call in calls)
    assert manager.settled == []
    # Provenance-only promotion still happens.
    assert manager.user_language == "ja"
    assert manager._user_language_explicit is True


async def test_unset_live_locale_still_isolates_when_the_durable_value_changed(monkeypatch):
    manager = _IdleManager()
    calls = _install_language_preference_stubs(
        monkeypatch, manager=manager, changed=True,
    )

    result = await preference_router.apply_character_language_preference("Mimi", "ja")

    assert result["recent_history_cleared"] is True
    assert any(call[0] == "clear_recent" for call in calls)
    assert len(manager.settled) == 1


async def test_differing_live_locale_isolates_even_without_a_durable_change(monkeypatch):
    """A live session speaking another language is real evidence, and still counts."""
    manager = _IdleManager()
    manager.user_language = "en"
    manager._user_language_explicit = True
    calls = _install_language_preference_stubs(
        monkeypatch, manager=manager, changed=False,
    )

    result = await preference_router.apply_character_language_preference("Mimi", "ja")

    assert result["recent_history_cleared"] is True
    assert any(call[0] == "clear_recent" for call in calls)


async def test_session_reconciliation_runs_without_the_config_transaction(monkeypatch):
    """The connector round-trip must not run under the characters.json lock.

    Regression guard: while the lock was held across the memory barrier, a
    callback dispatched onto another task -- which is what cross_server does --
    could not acquire the same lock, so the request deadlocked until the
    barrier timeout.
    """
    observed = {}

    class ConnectorManager(_IdleManager):
        async def settle_session_memory_if_idle(self, callback):
            observed["locked_during_settle"] = character_config_mutation_lock.locked()
            self.settled.append(callback)
            # cross_server runs the callback on its own task, and that callback
            # takes the config transaction itself.
            await asyncio.create_task(callback())
            return True

    manager = ConnectorManager()
    calls = _install_language_preference_stubs(
        monkeypatch, manager=manager, changed=True,
    )

    result = await asyncio.wait_for(
        preference_router.apply_character_language_preference("Mimi", "ja"),
        timeout=5,
    )

    assert observed["locked_during_settle"] is False
    assert result["recent_history_cleared"] is True
    assert any(call[0] == "clear_recent" for call in calls)
    # The transaction is fully released once the request returns.
    assert character_config_mutation_lock.locked() is False


async def test_durable_write_still_runs_inside_the_config_transaction(monkeypatch):
    """Narrowing the critical section must not drop it entirely."""
    manager = _IdleManager()
    observed = {}

    _install_language_preference_stubs(
        monkeypatch, manager=manager, changed=False,
    )

    async def persist_locale(method, name, *, language=None):
        observed["locked_during_persist"] = character_config_mutation_lock.locked()
        return {
            "success": True,
            "language": language,
            "previous_language": language,
            "changed": False,
        }

    monkeypatch.setattr(preference_router, "_request_memory_prompt_locale", persist_locale)

    await preference_router.apply_character_language_preference("Mimi", "ja")

    assert observed["locked_during_persist"] is True


class _StubResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload
        self.raised = False

    def raise_for_status(self):
        if self.status_code >= 400:
            self.raised = True
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _install_stub_client(monkeypatch, response):
    class _Client:
        async def put(self, *_args, **_kwargs):
            return response

        async def get(self, *_args, **_kwargs):
            return response

    monkeypatch.setattr(preference_router, "get_internal_http_client", lambda: _Client())


async def test_memory_server_conflict_becomes_a_typed_error(monkeypatch):
    response = _StubResponse(
        409,
        {"detail": {"error_code": "language_preference_superseded"}},
    )
    _install_stub_client(monkeypatch, response)

    with pytest.raises(preference_router.LanguagePreferenceConflictError):
        await preference_router._request_memory_prompt_locale(
            "PUT", "Mimi", language="ja",
        )
    assert response.raised is False, "冲突必须在 raise_for_status 之前分类"


@pytest.mark.parametrize(
    "payload",
    [
        # Cloudsave maintenance fence.
        {"success": False, "code": "cloudsave_maintenance", "retryable": True},
        # Storage-limited startup.
        {"ok": False, "error_code": "storage_startup_blocked", "limited_mode": True},
        # A 409 whose body we cannot parse into a known shape.
        {"detail": "something else entirely"},
    ],
)
async def test_unrelated_409s_are_not_reported_as_superseded(monkeypatch, payload):
    """Only this endpoint's causal-order conflict means "re-read the state".

    A maintenance fence or blocked startup persisted nothing, so reporting it as
    superseded would send the client off to re-read a value that never changed
    instead of surfacing a retryable failure.
    """
    _install_stub_client(monkeypatch, _StubResponse(409, payload))

    with pytest.raises(Exception) as excinfo:
        await preference_router._request_memory_prompt_locale(
            "PUT", "Mimi", language="ja",
        )
    assert not isinstance(
        excinfo.value, preference_router.LanguagePreferenceConflictError
    )


async def test_late_response_is_not_reported_as_a_successful_save(monkeypatch):
    """A preference replaced during reconciliation must not return 200.

    Reconciliation runs outside the transaction, so a second window can commit a
    newer locale meanwhile.  Returning 200 with the older language would let a
    late response overwrite the frontend's shared cache with a value the server
    no longer holds.
    """
    manager = _IdleManager()
    _install_language_preference_stubs(
        monkeypatch, manager=manager, changed=True, durable_after="en",
    )

    with pytest.raises(preference_router.LanguagePreferenceConflictError):
        await preference_router.apply_character_language_preference("Mimi", "ja")


async def test_unreadable_freshness_check_still_reports_the_save(monkeypatch):
    """Fail soft: a write we merely cannot re-read is not a superseded write."""
    manager = _IdleManager()
    _install_language_preference_stubs(monkeypatch, manager=manager, changed=True)

    original = preference_router._request_memory_prompt_locale

    async def flaky(method, name, *, language=None):
        if method == "GET":
            raise RuntimeError("memory server unreachable")
        return await original(method, name, language=language)

    monkeypatch.setattr(preference_router, "_request_memory_prompt_locale", flaky)

    result = await preference_router.apply_character_language_preference("Mimi", "ja")

    assert result["success"] is True
    assert result["language"] == "ja"


async def test_conflict_is_reported_as_409_not_503(monkeypatch):
    async def conflicting(*_args, **_kwargs):
        raise preference_router.LanguagePreferenceConflictError("superseded")

    async def read_payload(_request):
        return {"language": "ja"}, None

    monkeypatch.setattr(
        preference_router, "apply_character_language_preference", conflicting,
    )
    monkeypatch.setattr(preference_router, "_read_json_object_or_400", read_payload)
    monkeypatch.setattr(
        preference_router,
        "_validate_local_mutation_request",
        lambda *_args, **_kwargs: None,
    )

    response = await preference_router.set_character_language_preference(
        "Mimi", SimpleNamespace(),
    )

    assert response.status_code == 409
    assert json.loads(response.body)["error_code"] == "language_preference_superseded"


async def test_character_card_save_parses_body_outside_the_transaction(monkeypatch):
    observed = {}

    class _Request:
        async def json(self):
            observed["locked_during_parse"] = character_config_mutation_lock.locked()
            return {"charaData": {"档案名": "Mimi"}, "character_card_name": "Mimi"}

    async def serialized(data):
        observed["locked_during_save"] = character_config_mutation_lock.locked()
        observed["payload"] = data
        return {"success": True}

    monkeypatch.setattr(characters_cards, "_save_character_card_serialized", serialized)

    result = await characters_cards.save_character_card(_Request())

    assert result == {"success": True}
    # The body is read from the client socket before the global lock is taken,
    # and the transaction still wraps the actual characters.json work.
    assert observed["locked_during_parse"] is False
    assert observed["locked_during_save"] is True
    assert observed["payload"]["character_card_name"] == "Mimi"


def test_prompt_locale_read_does_not_create_the_character_directory(tmp_path, monkeypatch):
    import utils.config_manager as config_manager_module

    memory_dir = tmp_path / "memory"
    memory_dir.mkdir()
    monkeypatch.setattr(
        config_manager_module,
        "get_config_manager",
        lambda: SimpleNamespace(memory_dir=str(memory_dir)),
    )
    locale_state._locale_cache.clear()
    locale_state._subject_locale_cache.clear()

    assert locale_state.get_character_prompt_locale("GhostName") is None
    # A read for a deleted/renamed character must not resurrect its directory.
    assert not (memory_dir / "GhostName").exists()


def _unsubscribe_function_node():
    source = (
        PROJECT_ROOT / "main_routers" / "workshop_router" / "unsubscribe.py"
    ).read_text(encoding="utf-8")
    for node in ast.walk(ast.parse(source)):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == "_unsubscribe_workshop_item"
        ):
            return node
    raise AssertionError("_unsubscribe_workshop_item 已改名，请同步更新测试")


def test_unsubscribe_releases_the_transaction_before_the_steam_rpc():
    # Scoped to the function itself rather than the first textual match, so a
    # release() added elsewhere in the module cannot make this pass by accident.
    target = _unsubscribe_function_node()
    release_lines = [
        node.lineno
        for node in ast.walk(target)
        if isinstance(node, ast.Attribute)
        and node.attr == "release"
        and isinstance(node.value, ast.Name)
        and node.value.id == "character_config_mutation_lock"
    ]
    rpc_lines = [
        node.lineno
        for node in ast.walk(target)
        if isinstance(node, ast.Attribute) and node.attr == "UnsubscribeItem"
    ]

    assert len(rpc_lines) == 1, "Steam 退订 RPC 调用点不唯一，请同步更新测试"
    assert len(release_lines) >= 2, "finally 兜底释放不能被删掉"
    assert min(release_lines) < rpc_lines[0], (
        "characters.json 事务必须在 Steam 退订 RPC 之前释放"
    )


def test_no_suspension_point_between_lock_release_and_the_steam_rpc():
    """Releasing early must not widen the unsubscribe/re-import race window.

    ``perform_cleanup`` (the rmtree) runs on the Steam callback thread or on the
    5s fallback daemon thread, so it never held this asyncio lock in the first
    place.  What keeps the early release equivalent to the old ``finally``
    release is that no coroutine suspends in between: without an ``await`` the
    workshop-sync task cannot be scheduled before the unsubscribe request goes
    out.  Adding one here would genuinely open the window, so pin it.
    """
    target = _unsubscribe_function_node()
    release_lines = [
        node.lineno
        for node in ast.walk(target)
        if isinstance(node, ast.Attribute)
        and node.attr == "release"
        and isinstance(node.value, ast.Name)
        and node.value.id == "character_config_mutation_lock"
    ]
    rpc_line = next(
        node.lineno
        for node in ast.walk(target)
        if isinstance(node, ast.Attribute) and node.attr == "UnsubscribeItem"
    )
    early_release = min(release_lines)

    suspensions = [
        node.lineno
        for node in ast.walk(target)
        if isinstance(node, ast.Await) and early_release < node.lineno <= rpc_line
    ]
    assert suspensions == [], (
        f"提前释放锁与 Steam 退订 RPC 之间不得有挂起点，实际出现在 {suspensions}"
    )


def test_repeated_render_language_does_not_resync_tools():
    class _Manager(core_notify.NotifyMixin):
        def __init__(self):
            self.user_language = None
            self._user_language_explicit = False
            self._conversation_render_language = None
            self._conversation_turn_language = None
            self.registrations = 0
            self.syncs = 0

        def _set_conversation_turn_language(self, _language):
            pass

        def _register_builtin_tools(self):
            self.registrations += 1

        def _fire_task(self, coro):
            coro.close()
            self.syncs += 1

        async def _sync_tools_to_active_session(self):
            pass

    manager = _Manager()
    manager.set_render_language("ja")
    assert (manager.registrations, manager.syncs) == (1, 1)

    manager.set_render_language("ja")
    assert (manager.registrations, manager.syncs) == (1, 1)

    manager.set_render_language("en")
    assert (manager.registrations, manager.syncs) == (2, 2)
