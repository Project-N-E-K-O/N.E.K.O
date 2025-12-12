# -*- coding: utf-8 -*-
"""
Shared State Module

This module provides access to shared state variables (session managers, etc.)
that are initialized in main_server.py but need to be accessed by routers.

Design: Routers import getters from this module, main_server.py sets the state
after initialization.
"""

from typing import Dict


# Global state containers (set by main_server.py)
_state = {
    'sync_message_queue': {},
    'sync_shutdown_event': {},
    'session_manager': {},
    'session_id': {},
    'sync_process': {},
    'websocket_locks': {},
    'steamworks': None,
    'templates': None,
    'config_manager': None,
    'logger': None,
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


# Getters for all shared state
def get_sync_message_queue() -> Dict:
    return _state['sync_message_queue']


def get_sync_shutdown_event() -> Dict:
    return _state['sync_shutdown_event']


def get_session_manager() -> Dict:
    return _state['session_manager']


def get_session_id() -> Dict:
    return _state['session_id']


def get_sync_process() -> Dict:
    return _state['sync_process']


def get_websocket_locks() -> Dict:
    return _state['websocket_locks']


def get_steamworks():
    return _state['steamworks']


def get_templates():
    return _state['templates']


def get_config_manager():
    return _state['config_manager']


def get_logger():
    return _state['logger']


def get_initialize_character_data():
    """Get the initialize_character_data function reference"""
    return _state['initialize_character_data']


def get_session_id() -> Dict:
    """Get the session_id dictionary"""
    return _state['session_id']