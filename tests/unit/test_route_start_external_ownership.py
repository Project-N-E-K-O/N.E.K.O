"""Route start ownership checks and takeover tokens on the game / icebreaker
routes (design doc §5 PR-02 / OD-24)."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_routers import icebreaker_router
from main_routers.game_router import runtime as gr_runtime
from main_routers.game_router.route_lifecycle import (
    _TAKEOVER_CALLBACK_INBOX_KEY,
    _TAKEOVER_TOKEN_KEY,
)
from main_routers.system_router import AUTOSTART_CSRF_TOKEN
from utils import external_route_registry as registry
from utils import icebreaker_route_state
from utils.external_route_registry import ExternalRouteKind

from .game_route_test_helpers import (
    TakeoverManagerDouble,
    gr_patch_all,
    reset_game_route_state,
)


class _FakeRequest:
    def __init__(self, payload):
        self._payload = payload
        self.base_url = "http://127.0.0.1:8000/"
        self.url = SimpleNamespace(path="/api/test")
        self.method = "POST"
        self.headers = {
            "origin": "http://127.0.0.1:8000",
            "X-CSRF-Token": AUTOSTART_CSRF_TOKEN,
        }

    async def json(self):
        return self._payload


async def _no_routes(_name: str) -> int:
    return 0


async def _unclaimed(_name: str, _message: dict) -> bool:
    return False


def _register_visit(*, active: bool, locked: bool | None = None) -> None:
    registry.register_external_route_kind(ExternalRouteKind(
        kind="visit",
        is_active=lambda name: active and name == "Lan",
        route_stream_message=_unclaimed,
        on_start_session=None,
        finalize_for_character=_no_routes,
        is_locked=None if locked is None else (lambda name: locked and name == "Lan"),
        current_instance=lambda _name: "test-instance",
        audio_passthrough=True,
    ))


async def _start(game_type: str = "drawing_guess", session_id: str = "dg-1"):
    return await gr_runtime.game_route_start(
        game_type, _FakeRequest({"lanlan_name": "Lan", "session_id": session_id}),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("active", "locked"),
    [(True, None), (False, True)],
    ids=["active", "still-finishing"],
)
async def test_game_route_start_refuses_a_character_owned_by_another_route(monkeypatch, active, locked):
    manager = TakeoverManagerDouble()
    gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": manager})
    _register_visit(active=active, locked=locked)
    with reset_game_route_state():
        result = await _start()

        assert result == {"ok": False, "reason": "route_owned_by_external"}
        assert gr_runtime._get_active_game_route_state("Lan") is None
        assert manager.takeover_owner() is None


@pytest.mark.asyncio
async def test_game_route_start_refuses_while_another_owner_holds_the_takeover(monkeypatch):
    manager = TakeoverManagerDouble()
    sink = AsyncMock()
    manager.acquire_takeover("visit", AsyncMock(), callback_sink=sink)
    gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": manager})
    with reset_game_route_state():
        result = await _start()

        assert result == {"ok": False, "reason": "route_owned_by_external"}
        assert gr_runtime._get_active_game_route_state("Lan") is None
        assert manager.takeover_owner() == "visit"
        assert manager._takeover_callback_sink is sink


@pytest.mark.asyncio
async def test_game_route_start_ignores_kinds_that_are_neither_active_nor_locked(monkeypatch):
    manager = TakeoverManagerDouble()
    gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": manager})
    _register_visit(active=False, locked=False)
    with reset_game_route_state():
        result = await _start()

        assert result["ok"] is True
        state = gr_runtime._get_active_game_route_state("Lan", "drawing_guess")
        assert state[_TAKEOVER_TOKEN_KEY].owner == "game"
        assert manager.takeover_owner() == "game"
        # The inbox sink went in through the route's own token.
        assert manager._takeover_callback_sink.__self__ is state[_TAKEOVER_CALLBACK_INBOX_KEY]


@pytest.mark.asyncio
async def test_route_exit_releases_the_takeover_and_clears_all_three_attributes(monkeypatch):
    manager = TakeoverManagerDouble()
    gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": manager})
    gr_patch_all(monkeypatch, "_push_game_window_state_change", AsyncMock())
    gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", AsyncMock(return_value={"ok": True}))
    with reset_game_route_state():
        assert (await _start())["ok"] is True
        state = gr_runtime._get_active_game_route_state("Lan", "drawing_guess")

        await gr_runtime._finalize_game_route_state(state, reason="test_end")

        assert manager._takeover_active is False
        assert manager._takeover_input_dispatcher is None
        assert manager._takeover_callback_sink is None
        assert manager.takeover_owner() is None
        assert _TAKEOVER_TOKEN_KEY not in state


@pytest.mark.asyncio
async def test_route_exit_leaves_a_takeover_another_owner_holds_by_then(monkeypatch):
    # Mutation: postgame clearing the attributes unconditionally (the old
    # behavior) turns this red -- a finished game would unmute a visit.
    manager = TakeoverManagerDouble()
    gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": manager})
    gr_patch_all(monkeypatch, "_push_game_window_state_change", AsyncMock())
    gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", AsyncMock(return_value={"ok": True}))
    with reset_game_route_state():
        assert (await _start())["ok"] is True
        state = gr_runtime._get_active_game_route_state("Lan", "drawing_guess")
        manager.release_takeover(state[_TAKEOVER_TOKEN_KEY])
        visit_sink = AsyncMock()
        manager.acquire_takeover("visit", AsyncMock(), callback_sink=visit_sink)

        await gr_runtime._finalize_game_route_state(state, reason="test_end")

        assert manager.takeover_owner() == "visit"
        assert manager._takeover_active is True
        assert manager._takeover_callback_sink is visit_sink


@pytest.mark.asyncio
async def test_a_mini_game_still_replaces_another_mini_game(monkeypatch):
    manager = TakeoverManagerDouble()
    gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": manager})
    gr_patch_all(monkeypatch, "_push_game_window_state_change", AsyncMock())
    gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", AsyncMock(return_value={"ok": True}))
    with reset_game_route_state():
        assert (await _start(session_id="dg-old"))["ok"] is True
        old_state = gr_runtime._get_active_game_route_state("Lan", "drawing_guess")

        result = await _start(session_id="dg-new")

        assert result["ok"] is True
        new_state = gr_runtime._get_active_game_route_state("Lan", "drawing_guess")
        assert new_state is not old_state
        assert old_state["game_route_active"] is False
        assert manager.takeover_owner() == "game"
        assert manager._takeover_active is True
        assert new_state[_TAKEOVER_TOKEN_KEY] is not old_state.get(_TAKEOVER_TOKEN_KEY)


@pytest.fixture
def _icebreaker_clean(monkeypatch):
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {})
    states = dict(icebreaker_route_state._icebreaker_route_states)
    icebreaker_route_state._icebreaker_route_states.clear()
    yield
    icebreaker_route_state._icebreaker_route_states.clear()
    icebreaker_route_state._icebreaker_route_states.update(states)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("active", "locked"),
    [(True, None), (False, True)],
    ids=["active", "still-finishing"],
)
async def test_icebreaker_route_start_refuses_a_character_owned_by_another_route(
    _icebreaker_clean, active, locked,
):
    _register_visit(active=active, locked=locked)

    result = await icebreaker_router.icebreaker_route_start(
        _FakeRequest({"lanlan_name": "Lan", "session_id": "icebreaker-day1"})
    )

    assert result == {"ok": False, "reason": "route_owned_by_external"}
    assert icebreaker_route_state._get_active_icebreaker_route_state("Lan") is None


@pytest.mark.asyncio
async def test_icebreaker_restore_replaces_its_own_route_while_another_route_is_active(
    _icebreaker_clean,
):
    """The tutorial restores itself with a new session id over its own route.

    That is not a new claim on the slot; refusing it would strand the tutorial.
    Mutation: dropping the "already in an icebreaker route" exemption turns this red.
    """
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1")
    _register_visit(active=True)

    result = await icebreaker_router.icebreaker_route_start(
        _FakeRequest({"lanlan_name": "Lan", "session_id": "icebreaker-day1-restored"})
    )

    assert result["ok"] is True
    state = icebreaker_route_state._get_active_icebreaker_route_state("Lan")
    assert state["session_id"] == "icebreaker-day1-restored"


@pytest.mark.asyncio
async def test_icebreaker_restore_leaves_the_language_of_the_route_holding_the_slot(
    _icebreaker_clean, monkeypatch,
):
    # Mutation: absorbing the request language on a restore while another
    # route holds the slot turns this red.
    manager = _LanguageManager()
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": manager})
    icebreaker_route_state.activate_icebreaker_route("Lan", "icebreaker-day1")
    _register_visit(active=True)

    result = await icebreaker_router.icebreaker_route_start(_FakeRequest({
        "lanlan_name": "Lan",
        "session_id": "icebreaker-day1-restored",
        "i18n_language": "ja",
        "render_language": "ja",
    }))

    assert result["ok"] is True
    assert manager.language_updates == []
    assert manager.render_updates == []


@pytest.mark.asyncio
@pytest.mark.parametrize("current_owner_token", [True, False], ids=["own-token", "superseded-token"])
async def test_dropping_an_expired_route_state_releases_its_takeover_token(
    _icebreaker_clean, monkeypatch, current_owner_token,
):
    """An exit flow stuck past the timeout still holds its token; the sweep
    must release it (by value, so a newer owner's takeover survives) and finish
    what the exit flow does after that: hand the parked callbacks back (or
    decline them under a newer owner) and resume the voice input it held.

    Mutation: popping the state without releasing the token, skipping the
    inbox / voice steps, or force-releasing (superseded case) turns this red.
    """
    from unittest.mock import Mock

    from main_logic.proactive_delivery import DELIVERY_ACK_FUTURE_KEY

    manager = TakeoverManagerDouble(
        submit_proactive_callback=Mock(),
        _resume_independent_voice_input_after_game=AsyncMock(),
        _voice_lease_owner="game",
    )
    gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": manager})
    from main_logic.watch_together.live import LiveInbox

    inbox = LiveInbox()
    token = manager.acquire_takeover("game", AsyncMock(), callback_sink=inbox.accept)
    cue = _parked_cue()
    assert inbox.accept(cue) is True
    if not current_owner_token:
        manager.acquire_takeover("game", AsyncMock())  # a newer route took over
    with reset_game_route_state():
        state = {
            "lanlan_name": "Lan",
            "game_type": "drawing_guess",
            "game_route_active": False,
            "exit_started_at": 0.0,
            _TAKEOVER_TOKEN_KEY: token,
            _TAKEOVER_CALLBACK_INBOX_KEY: inbox,
        }
        gr_runtime._game_route_states[("Lan", "drawing_guess")] = state

        await gr_runtime._drop_expired_route_states(now=10**9)

        assert ("Lan", "drawing_guess") not in gr_runtime._game_route_states
    if current_owner_token:
        assert manager.takeover_owner() is None
        manager.submit_proactive_callback.assert_called_once()
        manager._resume_independent_voice_input_after_game.assert_awaited_once()
    else:
        assert manager.takeover_owner() == "game"
        manager.submit_proactive_callback.assert_not_called()
        assert cue[DELIVERY_ACK_FUTURE_KEY].result() is False
        manager._resume_independent_voice_input_after_game.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_failing_expired_route_state_is_kept_and_does_not_stop_the_others(
    _icebreaker_clean, monkeypatch,
):
    """A release that raises keeps that state *and its token* for the next
    sweep, which then releases it; it does not stop the sweep over the
    remaining states either.

    Mutation: dropping the state before releasing, removing the token before
    the release succeeds, or letting the error escape the loop turns this red.
    """
    flaky = TakeoverManagerDouble(lanlan_name="Flaky")
    flaky_token = flaky.acquire_takeover("game", AsyncMock())
    real_release = flaky.release_takeover
    flaky.release_takeover = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))
    healthy = TakeoverManagerDouble()
    gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Flaky": flaky, "Lan": healthy})
    healthy_token = healthy.acquire_takeover("game", AsyncMock())
    with reset_game_route_state():
        for lanlan, token in (("Flaky", flaky_token), ("Lan", healthy_token)):
            gr_runtime._game_route_states[(lanlan, "drawing_guess")] = {
                "lanlan_name": lanlan,
                "game_type": "drawing_guess",
                "game_route_active": False,
                "exit_started_at": 0.0,
                _TAKEOVER_TOKEN_KEY: token,
            }

        await gr_runtime._drop_expired_route_states(now=10**9)

        kept = gr_runtime._game_route_states.get(("Flaky", "drawing_guess"))
        assert kept is not None and kept.get(_TAKEOVER_TOKEN_KEY) is flaky_token
        assert ("Lan", "drawing_guess") not in gr_runtime._game_route_states
        assert healthy.takeover_owner() is None

        # The next sweep, once the release works again, finishes the job.
        flaky.release_takeover = real_release
        await gr_runtime._drop_expired_route_states(now=10**9)

        assert ("Flaky", "drawing_guess") not in gr_runtime._game_route_states
    assert flaky.takeover_owner() is None


@pytest.mark.asyncio
async def test_a_state_replaced_during_the_sweep_is_left_alone(_icebreaker_clean, monkeypatch):
    """While one expired state's voice resume awaits, a /route/start replaces
    another expired key with a live state; the sweep must leave that one (and
    its takeover) untouched.

    Mutation: re-reading the state by key instead of using the one found
    expired turns this red.
    """
    first = TakeoverManagerDouble(lanlan_name="First", _voice_lease_owner="game")
    second = TakeoverManagerDouble()
    first_token = first.acquire_takeover("game", AsyncMock())
    old_second_token = second.acquire_takeover("game", AsyncMock())
    gr_patch_all(monkeypatch, "get_session_manager", lambda: {"First": first, "Lan": second})

    async def _new_route_starts_meanwhile():
        new_token = second.acquire_takeover("game", AsyncMock())
        gr_runtime._game_route_states[("Lan", "drawing_guess")] = {
            "lanlan_name": "Lan",
            "game_type": "drawing_guess",
            "game_route_active": True,
            _TAKEOVER_TOKEN_KEY: new_token,
        }

    first._resume_independent_voice_input_after_game = _new_route_starts_meanwhile
    with reset_game_route_state():
        for lanlan, token in (("First", first_token), ("Lan", old_second_token)):
            gr_runtime._game_route_states[(lanlan, "drawing_guess")] = {
                "lanlan_name": lanlan,
                "game_type": "drawing_guess",
                "game_route_active": False,
                "exit_started_at": 0.0,
                _TAKEOVER_TOKEN_KEY: token,
            }

        await gr_runtime._drop_expired_route_states(now=10**9)

        live = gr_runtime._game_route_states.get(("Lan", "drawing_guess"))
        assert live is not None and live["game_route_active"] is True
        assert _TAKEOVER_TOKEN_KEY in live
        assert ("First", "drawing_guess") not in gr_runtime._game_route_states
    assert first.takeover_owner() is None
    assert second.takeover_owner() == "game"


@pytest.mark.asyncio
async def test_a_state_whose_release_keeps_failing_is_dropped_after_the_retry_limit(
    _icebreaker_clean, monkeypatch,
):
    """Mutation: retrying forever (no attempt limit) turns this red."""
    broken = TakeoverManagerDouble()
    broken.release_takeover = lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom"))
    gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": broken})
    with reset_game_route_state():
        gr_runtime._game_route_states[("Lan", "drawing_guess")] = {
            "lanlan_name": "Lan",
            "game_type": "drawing_guess",
            "game_route_active": False,
            "exit_started_at": 0.0,
            _TAKEOVER_TOKEN_KEY: object(),
        }
        for _ in range(gr_runtime._EXPIRED_ROUTE_RELEASE_MAX_ATTEMPTS - 1):
            await gr_runtime._drop_expired_route_states(now=10**9)
            assert ("Lan", "drawing_guess") in gr_runtime._game_route_states

        await gr_runtime._drop_expired_route_states(now=10**9)

        assert ("Lan", "drawing_guess") not in gr_runtime._game_route_states


class _LanguageManager:
    def __init__(self):
        self.user_language = "zh-CN"
        self._user_language_explicit = True
        self.language_updates = []
        self.render_updates = []

    def set_user_language(self, language):
        self.language_updates.append(language)

    def set_render_language(self, language):
        self.render_updates.append(language)


@pytest.mark.asyncio
async def test_refused_icebreaker_start_does_not_change_the_session_language(
    _icebreaker_clean, monkeypatch,
):
    # Mutation: absorbing the request language before the ownership check turns
    # this red -- a refused start would switch the owning route's language.
    manager = _LanguageManager()
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": manager})
    _register_visit(active=True)

    result = await icebreaker_router.icebreaker_route_start(_FakeRequest({
        "lanlan_name": "Lan",
        "session_id": "icebreaker-day1",
        "i18n_language": "ja",
        "render_language": "ja",
    }))

    assert result == {"ok": False, "reason": "route_owned_by_external"}
    assert manager.language_updates == []
    assert manager.render_updates == []

    _register_visit(active=False, locked=False)
    result = await icebreaker_router.icebreaker_route_start(_FakeRequest({
        "lanlan_name": "Lan",
        "session_id": "icebreaker-day1",
        "i18n_language": "ja",
        "render_language": "ja",
    }))
    assert result["ok"] is True
    assert manager.language_updates == ["ja"]
    assert manager.render_updates == ["ja"]


@pytest.mark.asyncio
async def test_icebreaker_route_start_is_unchanged_without_another_route(_icebreaker_clean):
    _register_visit(active=False, locked=False)

    result = await icebreaker_router.icebreaker_route_start(
        _FakeRequest({"lanlan_name": "Lan", "session_id": "icebreaker-day1"})
    )

    assert result["ok"] is True
    assert icebreaker_route_state._get_active_icebreaker_route_state("Lan") is not None


@pytest.mark.asyncio
async def test_game_slot_stays_locked_until_its_exit_flow_releases_the_takeover(
    _icebreaker_clean, monkeypatch,
):
    """/route/end flips the route inactive before releasing the takeover.

    In between, the slot must still count as taken: an icebreaker start lands
    as refused, and is accepted once the takeover is released. Mutation:
    registering the game kind without ``is_locked`` turns this red.
    """
    import asyncio

    manager = TakeoverManagerDouble()
    gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": manager})
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": manager})
    gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", AsyncMock(return_value={"ok": True}))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _slow_window_close(*_args, action="", **_kwargs):
        if action != "closed":
            return
        entered.set()
        await release.wait()

    gr_patch_all(monkeypatch, "_push_game_window_state_change", _slow_window_close)
    with reset_game_route_state():
        assert (await _start())["ok"] is True
        state = gr_runtime._get_active_game_route_state("Lan", "drawing_guess")
        finalize = asyncio.create_task(
            gr_runtime._finalize_game_route_state(state, reason="test_end")
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        assert state["game_route_active"] is False
        assert manager.takeover_owner() == "game"

        refused = await icebreaker_router.icebreaker_route_start(
            _FakeRequest({"lanlan_name": "Lan", "session_id": "icebreaker-day1"})
        )
        assert refused == {"ok": False, "reason": "route_owned_by_external"}

        release.set()
        await asyncio.wait_for(finalize, timeout=5)
        assert manager.takeover_owner() is None
        accepted = await icebreaker_router.icebreaker_route_start(
            _FakeRequest({"lanlan_name": "Lan", "session_id": "icebreaker-day1"})
        )
        assert accepted["ok"] is True


@pytest.mark.asyncio
async def test_game_slot_lock_survives_a_manager_replaced_mid_teardown(
    _icebreaker_clean, monkeypatch,
):
    """The lock follows the route's token, not whichever manager is current.

    Mutation: deriving the lock from the current manager's takeover owner turns
    this red -- the replacement manager owns nothing, so the slot opened early.
    """
    import asyncio

    managers = {"Lan": TakeoverManagerDouble()}
    gr_patch_all(monkeypatch, "get_session_manager", lambda: managers)
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: managers)
    gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", AsyncMock(return_value={"ok": True}))
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _slow_window_close(*_args, action="", **_kwargs):
        if action != "closed":
            return
        entered.set()
        await release.wait()

    gr_patch_all(monkeypatch, "_push_game_window_state_change", _slow_window_close)
    with reset_game_route_state():
        assert (await _start())["ok"] is True
        state = gr_runtime._get_active_game_route_state("Lan", "drawing_guess")
        finalize = asyncio.create_task(
            gr_runtime._finalize_game_route_state(state, reason="test_end")
        )
        await asyncio.wait_for(entered.wait(), timeout=2)
        managers["Lan"] = TakeoverManagerDouble()  # profile refresh replaced the manager

        refused = await icebreaker_router.icebreaker_route_start(
            _FakeRequest({"lanlan_name": "Lan", "session_id": "icebreaker-day1"})
        )
        assert refused == {"ok": False, "reason": "route_owned_by_external"}

        release.set()
        await asyncio.wait_for(finalize, timeout=5)
        assert gr_runtime.is_game_route_locked("Lan") is False



def _parked_cue():
    import asyncio

    from main_logic.proactive_delivery import DELIVERY_ACK_FUTURE_KEY

    cue = {"origin": "event", "status": "completed", "summary": "gift", "detail": "gift",
           "source_kind": "plugin", "source_name": "neko_live", "priority": 0,
           "coalesce_key": "", "media_images": []}
    cue[DELIVERY_ACK_FUTURE_KEY] = asyncio.get_running_loop().create_future()
    return cue


@pytest.mark.asyncio
@pytest.mark.parametrize("still_owner", [True, False], ids=["own-token", "superseded-token"])
async def test_exit_hands_parked_cues_back_only_when_nobody_else_owns_the_takeover(
    monkeypatch, still_owner,
):
    """A superseded route's parked cues must not land in the new owner's sink,
    nor does it hand the newer route's voice lease back to core.

    Mutation: handing the inbox back, or resuming voice input, unconditionally
    turns the superseded case red.
    """
    from unittest.mock import Mock

    from main_logic.proactive_delivery import DELIVERY_ACK_FUTURE_KEY

    manager = TakeoverManagerDouble(
        submit_proactive_callback=Mock(),
        _resume_independent_voice_input_after_game=AsyncMock(),
        _voice_lease_owner="game",
    )
    gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": manager})
    gr_patch_all(monkeypatch, "_push_game_window_state_change", AsyncMock())
    gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", AsyncMock(return_value={"ok": True}))
    with reset_game_route_state():
        assert (await _start())["ok"] is True
        state = gr_runtime._get_active_game_route_state("Lan", "drawing_guess")
        cue = _parked_cue()
        assert state[_TAKEOVER_CALLBACK_INBOX_KEY].accept(cue) is True
        new_sink = Mock(return_value=True)
        if not still_owner:
            # A newer route of the same owner took the takeover over meanwhile.
            manager.acquire_takeover("game", AsyncMock(), callback_sink=new_sink)

        await gr_runtime._finalize_game_route_state(state, reason="test_end")

    if still_owner:
        manager.submit_proactive_callback.assert_called_once()
        assert manager.takeover_owner() is None
        manager._resume_independent_voice_input_after_game.assert_awaited_once()
        assert state["realtime_restore"]["reason"] == "voice_input_resumed"
    else:
        manager.submit_proactive_callback.assert_not_called()
        new_sink.assert_not_called()
        assert cue[DELIVERY_ACK_FUTURE_KEY].result() is False
        assert manager.takeover_owner() == "game"
        manager._resume_independent_voice_input_after_game.assert_not_awaited()
        assert state["realtime_restore"]["reason"] == "takeover_held_by_newer_route"


@pytest.mark.asyncio
@pytest.mark.parametrize("failing_step", ["window_close", "context_snapshot"])
async def test_exit_releases_the_token_even_when_teardown_raises(
    _icebreaker_clean, monkeypatch, failing_step,
):
    """A teardown step that raises must not leave the slot locked forever --
    including the postgame context snapshot, the first step of the exit flow.

    Mutation: releasing outside a ``finally``, or taking the snapshot before
    the ``try``, turns this red.
    """
    manager = TakeoverManagerDouble()
    gr_patch_all(monkeypatch, "get_session_manager", lambda: {"Lan": manager})
    monkeypatch.setattr(icebreaker_router, "get_session_manager", lambda: {"Lan": manager})
    gr_patch_all(monkeypatch, "_submit_game_archive_to_memory", AsyncMock(return_value={"ok": True}))

    window_actions = []

    async def _window(*_args, action="", **_kwargs):
        window_actions.append(action)
        if action == "closed" and failing_step == "window_close":
            raise RuntimeError("teardown failed")

    gr_patch_all(monkeypatch, "_push_game_window_state_change", _window)
    if failing_step == "context_snapshot":
        def _failing_snapshot(_state):
            raise RuntimeError("teardown failed")

        gr_patch_all(monkeypatch, "_build_postgame_context_snapshot", _failing_snapshot)
    with reset_game_route_state():
        assert (await _start())["ok"] is True
        state = gr_runtime._get_active_game_route_state("Lan", "drawing_guess")
        if failing_step == "window_close":
            with pytest.raises(RuntimeError, match="teardown failed"):
                await gr_runtime._finalize_game_route_state(state, reason="test_end")
        else:
            # A failed snapshot only loses the postgame context; the rest of
            # the exit flow (window close included) still runs. Mutation:
            # letting the snapshot error escape turns this red.
            await gr_runtime._finalize_game_route_state(state, reason="test_end")
            assert "closed" in window_actions

        assert state.get("game_route_active") is False
        assert _TAKEOVER_TOKEN_KEY not in state
        assert manager.takeover_owner() is None
        assert gr_runtime.is_game_route_locked("Lan") is False
        accepted = await icebreaker_router.icebreaker_route_start(
            _FakeRequest({"lanlan_name": "Lan", "session_id": "icebreaker-day1"})
        )
        assert accepted["ok"] is True
