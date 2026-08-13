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


_UNCHANGED = object()


def _install_language_preference_stubs(
    monkeypatch,
    *,
    manager,
    changed: bool,
    durable_after=_UNCHANGED,
):
    """Wire the minimal seams apply_character_language_preference depends on.

    ``durable_after`` is what the post-reconciliation freshness GET reports.
    The default keeps whatever this request wrote; pass a different locale to
    simulate a competing window, or ``None`` to simulate the sidecar vanishing
    with a deleted/renamed character.
    """
    calls: list = []
    config_manager = SimpleNamespace(memory_dir="unused")
    # Mirrors the memory server: every write gets a monotonically increasing
    # causal order, and a read reports the order of whatever is durable.
    written: dict = {}
    orders = {"next": 0}

    async def load_character(name):
        calls.append(("load", name))
        return config_manager, {"猫娘": {name: {}}}

    async def request_locale(method, name, *, language=None):
        calls.append(("persist", method, name, language))
        if method == "GET":
            if durable_after is _UNCHANGED:
                return {
                    "success": True,
                    "language": written.get("language"),
                    "order": written.get("order"),
                }
            # A competing window committed after this request's write, so it
            # carries a strictly newer order.
            orders["next"] += 1
            return {
                "success": True,
                "language": durable_after,
                "order": orders["next"],
            }
        orders["next"] += 1
        written["language"] = language
        written["order"] = orders["next"]
        return {
            "success": True,
            "language": language,
            "order": orders["next"],
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


async def test_vanished_durable_locale_is_not_reported_as_a_successful_save(monkeypatch):
    """A deleted/renamed character takes prompt_locale.json with it.

    The freshness read then answers successfully with an empty locale.  That is
    not the benign case: returning 200 would let the card manager cache this
    language after the deletion cleanup, and a later reuse of the same name
    would inherit it.
    """
    manager = _IdleManager()
    _install_language_preference_stubs(
        monkeypatch, manager=manager, changed=True, durable_after=None,
    )

    with pytest.raises(preference_router.LanguagePreferenceConflictError):
        await preference_router.apply_character_language_preference("Mimi", "ja")


async def test_unverifiable_freshness_is_neither_success_nor_conflict(monkeypatch):
    """A read failure means "unknown", and must be reported as exactly that.

    Claiming plain success would publish an unverified language into the shared
    cross-window cache; claiming a conflict would assert something we never
    observed.  The response says the write landed but is unverified, and the
    clear must fail closed rather than delete a possibly-live conversation.
    """
    manager = _IdleManager()
    calls = _install_language_preference_stubs(
        monkeypatch, manager=manager, changed=True,
    )

    original = preference_router._request_memory_prompt_locale

    async def flaky(method, name, *, language=None):
        if method == "GET":
            raise RuntimeError("memory server unreachable")
        return await original(method, name, language=language)

    monkeypatch.setattr(preference_router, "_request_memory_prompt_locale", flaky)

    result = await preference_router.apply_character_language_preference("Mimi", "ja")

    assert result["success"] is False
    assert result["partial_success"] is True
    assert result["freshness_unverified"] is True
    assert result["language"] == "ja"
    # Fail closed on the destructive side: an unverifiable owner check must not
    # delete recent history.
    assert not any(call[0] == "clear_recent" for call in calls)


async def test_freshness_check_revalidates_identity_after_the_read(monkeypatch):
    """A matching locale does not prove the character still exists.

    A delete or rename can commit after the memory server read the sidecar but
    before its response arrives, so the freshness value still matches an
    identity that is already gone.
    """
    state = {"exists": True}
    manager = _IdleManager()
    _install_language_preference_stubs(monkeypatch, manager=manager, changed=True)

    original_load = preference_router._load_existing_character
    original_request = preference_router._request_memory_prompt_locale

    async def load_character(name):
        if not state["exists"]:
            raise LookupError("角色不存在")
        return await original_load(name)

    async def request_locale(method, name, *, language=None):
        payload = await original_request(method, name, language=language)
        if method == "GET":
            # The delete lands while this response is on the wire.
            state["exists"] = False
        return payload

    monkeypatch.setattr(preference_router, "_load_existing_character", load_character)
    monkeypatch.setattr(preference_router, "_request_memory_prompt_locale", request_locale)

    with pytest.raises(LookupError):
        await preference_router.apply_character_language_preference("Mimi", "ja")


async def test_every_character_load_runs_under_the_config_transaction(monkeypatch):
    """No character load may run unlocked, including the closing revalidation.

    ``ConfigManager.load_characters`` migrates a legacy reserved-field schema
    and writes it back, so an unlocked load can overwrite a concurrent
    save/delete/rename with its own stale snapshot.  That makes "is this load
    inside the transaction" a correctness property, not a style one.
    """
    manager = _IdleManager()
    lock_states = []
    config_manager = SimpleNamespace(memory_dir="unused")

    async def load_character(name):
        lock_states.append(character_config_mutation_lock.locked())
        return config_manager, {"猫娘": {name: {}}}

    written: dict = {}
    orders = {"next": 0}

    async def request_locale(method, _name, *, language=None):
        if method == "GET":
            return {
                "success": True,
                "language": written.get("language"),
                "order": written.get("order"),
            }
        orders["next"] += 1
        written["language"] = language
        written["order"] = orders["next"]
        return {
            "success": True,
            "language": language,
            "order": orders["next"],
            "previous_language": "en",
            "changed": True,
        }

    async def clear_recent(_config_manager, _name, *, expected_generation):
        assert expected_generation

    class SessionManager:
        def get(self, _name):
            return manager

    monkeypatch.setattr(preference_router, "_load_existing_character", load_character)
    monkeypatch.setattr(preference_router, "_request_memory_prompt_locale", request_locale)
    monkeypatch.setattr(preference_router, "_clear_character_recent_history", clear_recent)
    monkeypatch.setattr(preference_router, "get_session_manager", SessionManager)

    result = await preference_router.apply_character_language_preference("Mimi", "ja")

    assert result["success"] is True
    assert len(lock_states) >= 3, "至少覆盖写前校验、清理内校验、收尾复核"
    assert all(lock_states), f"存在未持锁的角色配置读取: {lock_states}"
    assert character_config_mutation_lock.locked() is False


async def test_closing_freshness_and_identity_share_one_transaction(monkeypatch):
    """Reading the locale outside the final lock reopens the race it closes.

    If the freshness GET runs unlocked and only *then* the lock is taken, a
    second PUT can commit in between: the GET still reports this request's
    locale, and the closing check -- seeing only the name -- publishes 200 for a
    language the server no longer holds.
    """
    manager = _IdleManager()
    lock_during_final_reads = []
    config_manager = SimpleNamespace(memory_dir="unused")
    written: dict = {}
    orders = {"next": 0}
    reconciled = {"done": False}

    async def load_character(name):
        if reconciled["done"]:
            lock_during_final_reads.append(
                ("load", character_config_mutation_lock.locked())
            )
        return config_manager, {"猫娘": {name: {}}}

    async def request_locale(method, _name, *, language=None):
        if method == "GET":
            if reconciled["done"]:
                lock_during_final_reads.append(
                    ("freshness", character_config_mutation_lock.locked())
                )
            return {
                "success": True,
                "language": written.get("language"),
                "order": written.get("order"),
            }
        orders["next"] += 1
        written["language"] = language
        written["order"] = orders["next"]
        return {
            "success": True,
            "language": language,
            "order": orders["next"],
            "previous_language": "en",
            "changed": True,
        }

    async def clear_recent(_config_manager, _name, *, expected_generation):
        assert expected_generation
        # Everything after the clear belongs to the closing sequence.
        reconciled["done"] = True

    class SessionManager:
        def get(self, _name):
            return manager

    monkeypatch.setattr(preference_router, "_load_existing_character", load_character)
    monkeypatch.setattr(preference_router, "_request_memory_prompt_locale", request_locale)
    monkeypatch.setattr(preference_router, "_clear_character_recent_history", clear_recent)
    monkeypatch.setattr(preference_router, "get_session_manager", SessionManager)

    result = await preference_router.apply_character_language_preference("Mimi", "ja")

    assert result["success"] is True
    kinds = [kind for kind, _ in lock_during_final_reads]
    assert kinds == ["freshness", "load"], (
        f"收尾应是「新鲜度读 → 身份复核」，实际 {kinds}"
    )
    assert all(locked for _, locked in lock_during_final_reads), (
        f"收尾两步必须在同一个事务内，实际 {lock_during_final_reads}"
    )


async def test_same_language_rewrite_revokes_the_stale_callbacks_ownership(monkeypatch):
    """Equal locale strings are not ownership.

    A second window saving the *same* language gets the fast ``changed: false``
    path and may start a new conversation.  A value-only fence sees its own
    locale and clears that conversation; only the causal write order can tell
    the two writes apart.
    """
    manager = _IdleManager()
    cleared = []
    config_manager = SimpleNamespace(memory_dir="unused")

    async def load_character(name):
        return config_manager, {"猫娘": {name: {}}}

    async def request_locale(method, _name, *, language=None):
        if method == "GET":
            # Same language, newer write: a second window re-saved 'ja' while
            # this request was settling.
            return {"success": True, "language": "ja", "order": 2}
        return {
            "success": True,
            "language": language,
            "order": 1,
            "previous_language": "en",
            "changed": True,
        }

    async def clear_recent(_config_manager, name, *, expected_generation):
        assert expected_generation
        cleared.append(name)

    class SessionManager:
        def get(self, _name):
            return manager

    monkeypatch.setattr(preference_router, "_load_existing_character", load_character)
    monkeypatch.setattr(preference_router, "_request_memory_prompt_locale", request_locale)
    monkeypatch.setattr(preference_router, "_clear_character_recent_history", clear_recent)
    monkeypatch.setattr(preference_router, "get_session_manager", SessionManager)

    result = await preference_router.apply_character_language_preference("Mimi", "ja")

    assert cleared == [], "同语言的更新写入之后，陈旧回调不得清空 recent.json"
    # The response still says success: the server really does hold this
    # language, so reporting a conflict would describe something that did not
    # happen. Only the destructive step is fenced by the write order.
    assert result["success"] is True
    assert result["language"] == "ja"
    assert result["recent_history_cleared"] is False


async def test_ordinary_new_dialog_write_is_not_reported_as_a_conflict(monkeypatch):
    """Session activity advances the write order without changing the language.

    ``/new_dialog`` re-persists the same explicit locale on every session start,
    hot swap and proactive turn, and it does so outside this server's lock.  The
    closing check therefore must not compare write orders: doing so reports "a
    newer language preference superseded this request" for a preference nobody
    touched, and pushes the card manager into a distrust-and-rehydrate cycle.
    """
    manager = _IdleManager()
    config_manager = SimpleNamespace(memory_dir="unused")
    durable = {"language": None, "order": 0}
    cleared = []

    async def load_character(name):
        return config_manager, {"猫娘": {name: {}}}

    async def request_locale(method, _name, *, language=None):
        if method == "GET":
            return {
                "success": True,
                "language": durable["language"],
                "order": durable["order"],
            }
        durable["order"] += 1
        durable["language"] = language
        return {
            "success": True,
            "language": language,
            "order": durable["order"],
            "previous_language": "en",
            "changed": True,
        }

    async def clear_recent(_config_manager, name, *, expected_generation):
        cleared.append(name)
        # A proactive turn / session start hits /new_dialog with the same
        # explicit language right after the clear, advancing the order only.
        durable["order"] += 1

    monkeypatch.setattr(preference_router, "_load_existing_character", load_character)
    monkeypatch.setattr(preference_router, "_request_memory_prompt_locale", request_locale)
    monkeypatch.setattr(preference_router, "_clear_character_recent_history", clear_recent)
    monkeypatch.setattr(
        preference_router,
        "get_session_manager",
        lambda: type("S", (), {"get": lambda _s, _n: manager})(),
    )

    result = await preference_router.apply_character_language_preference("Mimi", "ja")

    assert result["success"] is True, "普通会话活动不得被报成偏好冲突"
    assert result["language"] == "ja"
    assert result.get("freshness_unverified") is not True
    assert cleared == ["Mimi"]


async def test_missing_write_order_fails_closed(monkeypatch):
    """Ownership that cannot be established must not license a destructive step."""
    manager = _IdleManager()
    cleared = []
    config_manager = SimpleNamespace(memory_dir="unused")

    async def load_character(name):
        return config_manager, {"猫娘": {name: {}}}

    async def request_locale(method, _name, *, language=None):
        # An order-less response (truncated payload / pre-field durable state).
        if method == "GET":
            return {"success": True, "language": "ja"}
        return {
            "success": True,
            "language": language,
            "previous_language": "en",
            "changed": True,
        }

    async def clear_recent(_config_manager, name, *, expected_generation):
        cleared.append(name)

    class SessionManager:
        def get(self, _name):
            return manager

    monkeypatch.setattr(preference_router, "_load_existing_character", load_character)
    monkeypatch.setattr(preference_router, "_request_memory_prompt_locale", request_locale)
    monkeypatch.setattr(preference_router, "_clear_character_recent_history", clear_recent)
    monkeypatch.setattr(preference_router, "get_session_manager", SessionManager)

    result = await preference_router.apply_character_language_preference("Mimi", "ja")

    assert cleared == [], "写序缺失时不得执行破坏性清理"
    # Unprovable ownership blocks the clear, but the language itself is still
    # ours, so the response must not claim a conflict.
    assert result["success"] is True
    assert result["recent_history_cleared"] is False


async def test_late_clear_is_fenced_by_durable_ownership(monkeypatch):
    """A superseded reconciliation must not delete the newer session's history.

    Two PUTs can interleave so the older one is still settling while the newer
    one commits and lets a fresh conversation start.  The recent-generation
    token only moves on identity changes, so ownership has to be re-checked
    inside the same transaction that performs the clear -- checking afterwards
    would only convert the response, long after the rows were deleted.
    """
    manager = _IdleManager()
    calls = _install_language_preference_stubs(
        monkeypatch, manager=manager, changed=True, durable_after="en",
    )

    with pytest.raises(preference_router.LanguagePreferenceConflictError):
        await preference_router.apply_character_language_preference("Mimi", "ja")

    assert not any(call[0] == "clear_recent" for call in calls), (
        "被更新请求取代之后不得再清空 recent.json"
    )


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


async def test_language_preference_get_runs_under_the_config_transaction(monkeypatch):
    """The existence check is not read-only, so it must stay transactional.

    ``ConfigManager.load_characters`` migrates a legacy reserved-field schema
    and saves it back, so an unlocked check can overwrite a concurrent
    save/delete/rename with its own stale snapshot.  Holding the lock also
    removes the delete-and-recreate window a name-only check cannot detect.
    """
    observed = {}

    async def load_character(name):
        observed.setdefault("locked_during_load", character_config_mutation_lock.locked())
        return SimpleNamespace(memory_dir="unused"), {"猫娘": {name: {}}}

    async def request_locale(_method, _name, *, language=None):
        observed["locked_during_read"] = character_config_mutation_lock.locked()
        return {"success": True, "language": "ja", "order": 1}

    monkeypatch.setattr(preference_router, "_load_existing_character", load_character)
    monkeypatch.setattr(preference_router, "_request_memory_prompt_locale", request_locale)
    monkeypatch.setattr(
        preference_router, "aload_ui_language_override", lambda: _async_value("en"),
    )

    payload = await preference_router.get_character_language_preference("Mimi")

    assert payload["language"] == "ja"
    assert observed["locked_during_load"] is True
    assert observed["locked_during_read"] is True
    assert character_config_mutation_lock.locked() is False


async def test_missing_character_is_reported_as_404(monkeypatch):
    async def load_character(_name):
        raise LookupError("角色不存在")

    monkeypatch.setattr(preference_router, "_load_existing_character", load_character)

    response = await preference_router.get_character_language_preference("Gone")

    assert getattr(response, "status_code", 200) == 404


def _async_value(value):
    async def _inner():
        return value

    return _inner()


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


class _RenderLanguageManager(core_notify.NotifyMixin):
    def __init__(self):
        self.user_language = None
        self._user_language_explicit = False
        self._conversation_render_language = None
        self._conversation_turn_language = None
        self.registrations = 0
        self.syncs = 0
        self.fail_registration = False

    def _set_conversation_turn_language(self, _language):
        pass

    def _register_builtin_tools(self):
        if self.fail_registration:
            raise RuntimeError("tool registry unavailable")
        self.registrations += 1

    def _fire_task(self, coro):
        coro.close()
        self.syncs += 1

    async def _sync_tools_to_active_session(self):
        pass


def test_render_language_always_reapplies_the_tool_definitions():
    """No local "already applied" shortcut is allowed here.

    The fields are assigned before the registry call and the wire push is
    fire-and-forget with suppressed errors, so nothing cheap can prove the tools
    were applied; a wrong skip would strand stale definitions permanently.
    """
    manager = _RenderLanguageManager()
    manager.set_render_language("ja")
    assert (manager.registrations, manager.syncs) == (1, 1)

    manager.set_render_language("ja")
    assert (manager.registrations, manager.syncs) == (2, 2)

    manager.set_render_language("en")
    assert (manager.registrations, manager.syncs) == (3, 3)
