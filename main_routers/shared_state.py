# -*- coding: utf-8 -*-
"""
Shared State Module

This module provides access to shared state variables (session managers, etc.)
that are initialized in main_server.py but need to be accessed by routers.

Design: Routers import getters from this module, main_server.py sets the state
after initialization.

History
-------
Issue #857 / PR #855 review consolidated 6 parallel per-catgirl module-globals
in main_server.py (sync_message_queue / sync_shutdown_event / session_id /
sync_process / websocket_locks / session_manager) into a single
``role_state: dict[str, RoleState]`` container. To avoid touching dozens of
consumer call-sites in this PR, the legacy getters
(``get_sync_message_queue`` etc.) are kept and now return a thin
``_RoleStateFieldView`` adapter — a ``MutableMapping`` view over one field of
each ``RoleState`` entry. Consumers see the same dict-like API
(``[k]`` / ``in`` / ``del`` / ``.keys()`` / ``.items()`` / ``.get()`` / ``.pop()``).

A new ``get_role_state()`` getter exposes the underlying container directly
for code that wants to migrate off the per-field views.
"""

from collections.abc import MutableMapping
from typing import Dict

_UNSET = object()

# Global state containers (set by main_server.py)
_state = {
    'role_state': _UNSET,            # NEW canonical store (dict[str, RoleState])
    'sync_message_queue': _UNSET,    # _RoleStateFieldView adapter (legacy API)
    'sync_shutdown_event': _UNSET,   # _RoleStateFieldView adapter (legacy API)
    'session_manager': _UNSET,       # _RoleStateFieldView adapter (legacy API)
    'session_id': _UNSET,            # _RoleStateFieldView adapter (legacy API)
    'sync_process': _UNSET,          # _RoleStateFieldView adapter (legacy API)
    'websocket_locks': _UNSET,       # _RoleStateFieldView adapter (legacy API)
    'steamworks': None,
    'templates': _UNSET,
    'config_manager': _UNSET,
    'logger': _UNSET,
    'initialize_character_data': _UNSET,  # Function reference
    'switch_current_catgirl_fast': _UNSET,  # Fast path for current-catgirl switch
    'init_one_catgirl': _UNSET,             # Fast path for add/update single catgirl
    'remove_one_catgirl': _UNSET,           # Fast path for delete single catgirl
}


class _RoleStateFieldView(MutableMapping):
    """Dict-like view over a single field of every ``RoleState`` entry.

    Backward-compatibility shim for legacy ``get_sync_message_queue()`` /
    ``get_session_id()`` etc. callers. Holds a reference to the live
    ``role_state`` dict and a field name; reads/writes proxy to
    ``role_state[k].<field>``.

    Semantics
    ---------
    ``__contains__(k)`` returns True iff ``k in role_state`` AND the field
    value is not ``None``. This preserves the legacy "the dict has no entry
    for this catgirl" check used by code like::

        if lanlan_name not in session_id: ...
        if lanlan in session_manager: ...

    For optional fields (session_id / sync_process / session_manager) this
    matches the historical "dict had no key" state. For always-present fields
    (sync_message_queue / sync_shutdown_event / websocket_lock) the values are
    never ``None`` after ``_ensure_character_slots`` so contains is equivalent
    to ``k in role_state``.

    ``__delitem__(k)`` clears the field by setting it to ``None``; it does
    NOT remove the underlying ``role_state`` entry. Removing the whole
    catgirl goes through ``del role_state[k]`` in
    ``_cleanup_character_dicts``. This matches the only consumer ``del`` /
    ``pop`` site we have today (``session_id.pop(lanlan_name, None)`` in
    ``websocket_router``), which means "this catgirl no longer has a live
    session" — not "delete the catgirl".

    Note on ``websocket_lock``: external consumers must NEVER assign through
    this view (would strand any coroutine already waiting on the old Lock).
    No consumer does today. The view does not actively block writes — it
    relies on convention + code review.
    """

    __slots__ = ("_role_state", "_field")

    def __init__(self, role_state: dict, field: str):
        self._role_state = role_state
        self._field = field

    def __getitem__(self, key):
        rs = self._role_state.get(key)
        if rs is None:
            raise KeyError(key)
        value = getattr(rs, self._field)
        if value is None:
            raise KeyError(key)
        return value

    def __setitem__(self, key, value):
        rs = self._role_state.get(key)
        if rs is None:
            raise KeyError(
                f"role_state[{key!r}] not initialized; "
                f"call _ensure_character_slots before assigning {self._field!r}"
            )
        setattr(rs, self._field, value)

    def __delitem__(self, key):
        rs = self._role_state.get(key)
        if rs is None or getattr(rs, self._field) is None:
            raise KeyError(key)
        setattr(rs, self._field, None)

    def __contains__(self, key):
        rs = self._role_state.get(key)
        return rs is not None and getattr(rs, self._field, None) is not None

    def __iter__(self):
        field = self._field
        return (
            k for k, rs in self._role_state.items()
            if getattr(rs, field, None) is not None
        )

    def __len__(self):
        field = self._field
        return sum(
            1 for rs in self._role_state.values()
            if getattr(rs, field, None) is not None
        )

    def __repr__(self):
        return f"_RoleStateFieldView(field={self._field!r}, len={len(self)})"


def init_shared_state(
    role_state: Dict,
    steamworks,
    templates,
    config_manager,
    logger,
    initialize_character_data=None,
    switch_current_catgirl_fast=None,
    init_one_catgirl=None,
    remove_one_catgirl=None,
):
    """Initialize shared state from main_server.py.

    Builds adapter views over ``role_state`` so legacy getters
    (``get_sync_message_queue`` etc.) keep their old observable behavior.
    The adapters hold a live reference to ``role_state`` — future mutations
    in main_server.py are reflected without a re-init step.
    """
    _state['role_state'] = role_state
    _state['steamworks'] = steamworks
    _state['templates'] = templates
    _state['config_manager'] = config_manager
    _state['logger'] = logger
    _state['initialize_character_data'] = initialize_character_data
    _state['switch_current_catgirl_fast'] = switch_current_catgirl_fast
    _state['init_one_catgirl'] = init_one_catgirl
    _state['remove_one_catgirl'] = remove_one_catgirl

    # Pre-build adapter views (legacy API).
    # Note: legacy dict was named "websocket_locks" (plural) but the consolidated
    # field is "websocket_lock" (singular, one per role).
    _state['sync_message_queue'] = _RoleStateFieldView(role_state, 'sync_message_queue')
    _state['sync_shutdown_event'] = _RoleStateFieldView(role_state, 'sync_shutdown_event')
    _state['session_manager'] = _RoleStateFieldView(role_state, 'session_manager')
    _state['session_id'] = _RoleStateFieldView(role_state, 'session_id')
    _state['sync_process'] = _RoleStateFieldView(role_state, 'sync_process')
    _state['websocket_locks'] = _RoleStateFieldView(role_state, 'websocket_lock')


def _check_initialized(key: str) -> None:
    """Validate that a state key has been initialized via init_shared_state."""
    value = _state.get(key)
    if value is _UNSET:
        raise RuntimeError(
            f"Shared state '{key}' is not initialized. "
            "Call init_shared_state() from main_server.py before accessing shared state."
        )

# Getters for all shared state
def get_role_state() -> Dict:
    """Get the canonical role_state dict (``dict[str, RoleState]``).

    New code should prefer this over the per-field legacy getters.
    """
    _check_initialized('role_state')
    return _state['role_state']


def get_sync_message_queue() -> Dict:
    """Get a dict-like view of per-role sync_message_queue.

    Backed by ``role_state`` via ``_RoleStateFieldView``.
    """
    _check_initialized('sync_message_queue')
    return _state['sync_message_queue']


def get_sync_shutdown_event() -> Dict:
    """Get a dict-like view of per-role sync_shutdown_event."""
    _check_initialized('sync_shutdown_event')
    return _state['sync_shutdown_event']


def get_session_manager() -> Dict:
    """Get a dict-like view of per-role session_manager."""
    _check_initialized('session_manager')
    return _state['session_manager']


def get_session_id() -> Dict:
    """Get a dict-like view of per-role session_id."""
    _check_initialized('session_id')
    return _state['session_id']


def get_sync_process() -> Dict:
    """Get a dict-like view of per-role sync_process."""
    _check_initialized('sync_process')
    return _state['sync_process']


def get_websocket_locks() -> Dict:
    """Get a dict-like view of per-role websocket_lock.

    Note: legacy plural name preserved for API compatibility; the underlying
    field on RoleState is ``websocket_lock`` (singular).
    """
    _check_initialized('websocket_locks')
    return _state['websocket_locks']


def get_steamworks():
    """Get the steamworks instance.

    Note: This may return None if Steamworks failed to initialize
    (e.g., Steam client not running). Callers must handle None gracefully.
    We do NOT call _check_initialized here because None is a valid value.
    """
    return _state['steamworks']


def set_steamworks(steamworks):
    """Set the steamworks instance (called during startup event)."""
    _state['steamworks'] = steamworks


def get_templates():
    """Get the templates dictionary."""
    _check_initialized('templates')
    return _state['templates']


def get_config_manager():
    """Get the config_manager dictionary."""
    _check_initialized('config_manager')
    return _state['config_manager']


def get_logger():
    """Get the logger dictionary."""
    _check_initialized('logger')
    return _state['logger']


def get_initialize_character_data():
    """Get the initialize_character_data function reference"""
    _check_initialized('initialize_character_data')
    return _state['initialize_character_data']


def get_switch_current_catgirl_fast():
    """Fast path: current-catgirl switch (no per-k work, just refresh globals)."""
    _check_initialized('switch_current_catgirl_fast')
    return _state['switch_current_catgirl_fast']


def get_init_one_catgirl():
    """Fast path: add / update a single catgirl (per-k init without scanning all)."""
    _check_initialized('init_one_catgirl')
    return _state['init_one_catgirl']


def get_remove_one_catgirl():
    """Fast path: delete a single catgirl (stop its thread, clean dicts)."""
    _check_initialized('remove_one_catgirl')
    return _state['remove_one_catgirl']
