"""Takeover ownership tokens and the callback hold (design doc §5 PR-02 / OD-24)."""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import Mock

import pytest

from main_logic.core.proactive import ProactiveMixin
from main_logic.core.takeover import (
    HoldToken,
    TakeoverMixin,
    TakeoverOwned,
    TakeoverToken,
)

from .game_route_test_helpers import TakeoverManagerDouble

_REPO_ROOT = Path(__file__).resolve().parents[2]
_TAKEOVER_ATTRS = ("_takeover_active", "_takeover_input_dispatcher", "_takeover_callback_sink")


def _takeover_state(manager):
    return tuple(getattr(manager, name) for name in _TAKEOVER_ATTRS)


async def _dispatcher(*_args, **_kwargs):
    return True


def _sink(_callback):
    return True


def test_acquire_sets_all_three_attributes_and_the_owner():
    manager = TakeoverManagerDouble()

    token = manager.acquire_takeover("game", _dispatcher, callback_sink=_sink)

    assert isinstance(token, TakeoverToken)
    assert token.owner == "game"
    assert _takeover_state(manager) == (True, _dispatcher, _sink)
    assert manager.takeover_owner() == "game"


def test_release_with_the_current_token_clears_all_three_attributes():
    manager = TakeoverManagerDouble()
    token = manager.acquire_takeover("game", _dispatcher, callback_sink=_sink)

    assert manager.release_takeover(token) is True
    assert _takeover_state(manager) == (False, None, None)
    assert manager.takeover_owner() is None


@pytest.mark.parametrize("stale", ["none", "released", "foreign"])
def test_release_with_a_non_current_token_changes_nothing(stale):
    # Mutation: release_takeover ignoring the token turns this red -- one
    # controller could then unmute the session another controller holds.
    manager = TakeoverManagerDouble()
    if stale == "released":
        old = manager.acquire_takeover("game", _dispatcher)
        manager.release_takeover(old)
        token = old
    elif stale == "foreign":
        token = TakeoverManagerDouble().acquire_takeover("game", _dispatcher)
    else:
        token = None
    current = manager.acquire_takeover("visit", _dispatcher, callback_sink=_sink)

    assert manager.release_takeover(token) is False
    assert _takeover_state(manager) == (True, _dispatcher, _sink)
    assert manager.takeover_owner() == "visit"
    assert manager.release_takeover(current) is True


def test_a_different_owner_cannot_take_an_owned_session():
    manager = TakeoverManagerDouble()
    manager.acquire_takeover("visit", _dispatcher, callback_sink=_sink)

    with pytest.raises(TakeoverOwned) as excinfo:
        manager.acquire_takeover("game", None)

    assert excinfo.value.current_owner == "visit"
    assert excinfo.value.requested_owner == "game"
    assert _takeover_state(manager) == (True, _dispatcher, _sink)
    assert manager.takeover_owner() == "visit"


def test_the_same_owner_reacquires_and_retires_the_previous_token():
    # A mini-game route superseding the previous one: the new route owns the
    # takeover and the old route's late release must not unmute it.
    manager = TakeoverManagerDouble()
    old = manager.acquire_takeover("game", None)
    new = manager.acquire_takeover("game", _dispatcher)

    assert new is not old
    assert manager.release_takeover(old) is False
    assert _takeover_state(manager) == (True, _dispatcher, None)
    assert manager.release_takeover(new) is True


def test_force_release_clears_a_takeover_it_does_not_own():
    manager = TakeoverManagerDouble()
    manager.acquire_takeover("game", _dispatcher, callback_sink=_sink)

    assert manager.release_takeover(None, force=True) is False
    assert _takeover_state(manager) == (False, None, None)
    assert manager.takeover_owner() is None


def test_callback_sink_is_only_set_through_the_current_token():
    manager = TakeoverManagerDouble()
    old = manager.acquire_takeover("game", _dispatcher)
    new = manager.acquire_takeover("game", _dispatcher)

    assert manager.set_takeover_callback_sink(old, _sink) is False
    assert manager._takeover_callback_sink is None
    assert manager.set_takeover_callback_sink(new, _sink) is True
    assert manager._takeover_callback_sink is _sink


def test_blank_owner_is_rejected():
    with pytest.raises(ValueError):
        TakeoverManagerDouble().acquire_takeover(" ", None)


def test_only_the_takeover_mixin_writes_the_takeover_attributes():
    """Outside the mixin (and the manager's __init__) nothing assigns them."""
    offenders = []
    allowed = {
        _REPO_ROOT / "main_logic" / "core" / "takeover.py",
        _REPO_ROOT / "main_logic" / "core" / "manager.py",
    }
    watched = set(_TAKEOVER_ATTRS) | {"_takeover_token", "_callback_hold_sink", "_callback_hold_token"}
    for root in ("main_logic", "main_routers", "app", "utils", "plugin", "brain"):
        for path in (_REPO_ROOT / root).rglob("*.py"):
            if path in allowed or "node_modules" in path.parts:
                continue
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                targets = []
                if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)):
                    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                for target in targets:
                    if isinstance(target, ast.Attribute) and target.attr in watched:
                        offenders.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno}")
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "setattr"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and node.args[1].value in watched
                ):
                    offenders.append(f"{path.relative_to(_REPO_ROOT)}:{node.lineno}")
    assert offenders == []


# --- callback hold -----------------------------------------------------------


def _submitter(**attrs):
    manager = TakeoverManagerDouble(
        is_goodbye_silent=lambda: False,
        enqueue_agent_callback=Mock(),
        proactive_manager=Mock(submit=Mock(return_value=[])),
        _recompute_coalesce_latest=Mock(),
        **attrs,
    )
    return manager


def _submit(manager, summary="gift"):
    ProactiveMixin.submit_proactive_callback(manager, {"summary": summary}, priority=4)


def test_hold_parks_callbacks_after_the_takeover_is_released():
    parked = []
    manager = _submitter()
    token = manager.acquire_takeover("visit", _dispatcher, callback_sink=parked.append)
    hold = manager.hold_callbacks(lambda callback: parked.append(callback) or True, owner="visit")
    manager.release_takeover(token)

    _submit(manager)

    assert isinstance(hold, HoldToken)
    assert [callback["summary"] for callback in parked] == ["gift"]
    assert parked[0]["priority"] == 4
    manager.proactive_manager.submit.assert_not_called()


def test_releasing_the_hold_restores_ordinary_delivery():
    parked = []
    manager = _submitter()
    hold = manager.hold_callbacks(lambda callback: parked.append(callback) or True)

    assert manager.release_callback_hold(hold) is True
    _submit(manager)

    assert parked == []
    manager.proactive_manager.submit.assert_called_once()


def test_a_stale_hold_token_releases_nothing():
    parked = []
    manager = _submitter()
    first = manager.hold_callbacks(lambda callback: True)
    manager.hold_callbacks(lambda callback: parked.append(callback) or True)

    assert manager.release_callback_hold(first) is False
    assert manager.release_callback_hold(None) is False
    _submit(manager)

    assert len(parked) == 1
    manager.proactive_manager.submit.assert_not_called()


def test_takeover_sink_is_asked_before_the_hold_sink():
    seen = []
    manager = _submitter()
    manager.acquire_takeover("game", _dispatcher, callback_sink=lambda c: seen.append("takeover") or True)
    manager.hold_callbacks(lambda c: seen.append("hold") or True)

    _submit(manager)
    assert seen == ["takeover"]

    # A takeover sink that declines lets the hold sink take it.
    manager2 = _submitter()
    manager2.acquire_takeover("game", _dispatcher, callback_sink=lambda c: seen.append("declined") or False)
    manager2.hold_callbacks(lambda c: seen.append("hold") or True)
    _submit(manager2)
    assert seen[-2:] == ["declined", "hold"]
    manager2.proactive_manager.submit.assert_not_called()


@pytest.mark.parametrize("hold_result", [False, RuntimeError("broken")])
def test_a_hold_sink_that_does_not_keep_the_callback_falls_through(hold_result):
    manager = _submitter()
    sink = Mock(side_effect=hold_result) if isinstance(hold_result, Exception) else Mock(return_value=hold_result)
    manager.hold_callbacks(sink)

    _submit(manager)

    sink.assert_called_once()
    manager.proactive_manager.submit.assert_called_once()


def test_without_takeover_or_hold_delivery_is_unchanged():
    manager = _submitter()

    _submit(manager)

    manager.proactive_manager.submit.assert_called_once_with(
        {"summary": "gift"}, priority=4, coalesce_key=None,
    )


def test_hold_requires_a_callable_sink():
    with pytest.raises(TypeError):
        TakeoverManagerDouble().hold_callbacks(None)


def test_manager_initialises_every_takeover_attribute():
    """LLMSessionManager.__init__ is the single home of the mixin's state."""
    source = (_REPO_ROOT / "main_logic" / "core" / "manager.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    init = next(
        node for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "__init__"
    )
    assigned = {
        target.attr
        for node in ast.walk(init)
        if isinstance(node, (ast.Assign, ast.AnnAssign))
        for target in (node.targets if isinstance(node, ast.Assign) else [node.target])
        if isinstance(target, ast.Attribute)
    }
    expected = set(_TAKEOVER_ATTRS) | {"_takeover_token", "_callback_hold_sink", "_callback_hold_token"}
    assert expected <= assigned
    assert issubclass(
        __import__("main_logic.core", fromlist=["LLMSessionManager"]).LLMSessionManager,
        TakeoverMixin,
    )
