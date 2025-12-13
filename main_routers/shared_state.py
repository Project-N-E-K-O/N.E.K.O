# -*- coding: utf-8 -*-
"""
Shared State Module

This module provides access to shared state variables (session managers, etc.)
that are initialized in main_server.py but need to be accessed by routers.

Design: Routers import getters from this module, main_server.py sets the state
after initialization.
"""

from typing import Dict
_UNSET = object()

# Global state containers (set by main_server.py)
_state = {
    'sync_message_queue': _UNSET,
    'sync_shutdown_event': _UNSET,
    'session_manager': _UNSET,
    'session_id': _UNSET,
    'sync_process': _UNSET,
    'websocket_locks': _UNSET,
    'steamworks': None,
    'templates': _UNSET,
    'config_manager': _UNSET,
    'logger': _UNSET,
    'initialize_character_data': None,  # Function reference
}


def init_shared_state(
    sync_message_queue: Dict,
    sync_shutdown_event: Dict,
    session_manager: Dict,
    session_id: Dict,
    sync_process: Dict,
    websocket_locks: Dict,
    steamworks,
    templates,
    config_manager,
    logger,
    initialize_character_data=None,
):
    """Initialize shared state from main_server.py"""
    _state['sync_message_queue'] = sync_message_queue
    _state['sync_shutdown_event'] = sync_shutdown_event
    _state['session_manager'] = session_manager
    _state['session_id'] = session_id
    _state['sync_process'] = sync_process
    _state['websocket_locks'] = websocket_locks
    _state['steamworks'] = steamworks
    _state['templates'] = templates
    _state['config_manager'] = config_manager
    _state['logger'] = logger
    _state['initialize_character_data'] = initialize_character_data

def _check_initialized(key: str) -> None:
    """Validate that a state key has been initialized via init_shared_state."""
    value = _state.get(key)
    if value is _UNSET:
        raise RuntimeError(
            f"Shared state '{key}' is not initialized. "
            "Call init_shared_state() from main_server.py before accessing shared state."
        )

# Getters for all shared state
def get_sync_message_queue() -> Dict:
    """Get the sync_message_queue dictionary."""
    _check_initialized('sync_message_queue')
    return _state['sync_message_queue']


def get_sync_shutdown_event() -> Dict:
    """Get the sync_shutdown_event dictionary."""
    _check_initialized('sync_shutdown_event')
    return _state['sync_shutdown_event']


def get_session_manager() -> Dict:
    """Get the session_manager dictionary."""
    _check_initialized('session_manager')
    return _state['session_manager']


def get_session_id() -> Dict:
    """Get the session_id dictionary."""
    _check_initialized('session_id')
    return _state['session_id']


def get_sync_process() -> Dict:
    """Get the sync_process dictionary."""
    _check_initialized('sync_process')
    return _state['sync_process']


def get_websocket_locks() -> Dict:
    """Get the websocket_locks dictionary."""
    _check_initialized('websocket_locks')
    return _state['websocket_locks']


def get_steamworks():
    """Get the steamworks instance.
    
    Note: This may return None if Steamworks failed to initialize
    (e.g., Steam client not running). Callers must handle None gracefully.
    We do NOT call _check_initialized here because None is a valid value.
    """
    return _state['steamworks']


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