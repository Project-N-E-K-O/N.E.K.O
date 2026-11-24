# Copyright 2025-2026 Project N.E.K.O. Team; SPDX-License-Identifier: Apache-2.0
"""Reversible subscription configuration. Private snapshots never leave the server."""
from copy import deepcopy
import hashlib
import hmac
import json
import secrets

STATE_KEY = 'chatgptSubscription'
_REVISION_KEY = secrets.token_bytes(32)
# Chat-completion slots only. The decision slot speaks its own protocol, and the
# vision slot is retired (images go to the chat model).
TEXT_SLOTS = frozenset(('conversation', 'memory', 'gameMain', 'gameSummary', 'recognition',
                        'gameRecognition', 'agent', 'agentSmall', 'agentAdvanced'))
# Provider value an absent ``<slot>ModelProvider`` field stands for; the settings
# page's MODEL_DEFAULT_PROVIDER table.
_DEFAULT_SLOT_PROVIDERS = {
    'image': 'disabled', 'omni': 'follow_core',
    'gameMain': 'follow_conversation', 'gameSummary': 'follow_memory',
    'gameRecognition': 'follow_recognition', 'agentSmall': 'follow_recognition',
    'agentAdvanced': 'follow_agent',
}


def default_slot_provider(slot):
    return _DEFAULT_SLOT_PROVIDERS.get(slot, 'follow_assist')


def runtime_model_type(slot):
    """Map a settings slot prefix (``gameMain``) to its runtime model type (``game_main``)."""
    return ''.join(f'_{char.lower()}' if char.isupper() else char for char in slot)


def supported_slots():
    from utils.api_config_loader import get_config
    declared = get_config()['assist_api_providers']['chatgpt']['supported_model_types']
    # Voice/image generation must never be enrolled by metadata alone.
    return [slot for slot in declared if slot in TEXT_SLOTS]


def fields(slots):
    return ['enableCustomApi', *[f'{slot}Model{suffix}' for slot in slots
                               for suffix in ('Provider', 'Url', 'Id', 'ApiKey')]]


def restricted_slots(config, available_slots):
    """List independent slots that stayed dormant when the subscription started."""
    custom_api_enabled, slots = runtime_custom_api(config)
    if config.get(STATE_KEY) and not custom_api_enabled:
        return [slot for slot in available_slots if slot not in slots]
    return []


def revision(config, slots):
    snapshot = {key: config[key] for key in fields(slots) + [STATE_KEY] if key in config}
    return hmac.new(_REVISION_KEY, json.dumps(snapshot, sort_keys=True).encode(), hashlib.sha256).hexdigest()


def preview(config, slots):
    state = config.get(STATE_KEY) or {}
    return {'enabled': bool(state), 'slots': slots, 'revision': revision(config, slots),
            'overwritten': [slot for slot in slots
                            if config.get(f'{slot}ModelProvider') not in (None, '', 'chatgpt')],
            'model': state.get('model', '')}


def enable(config, slots, model, expected_revision):
    if not hmac.compare_digest(revision(config, slots), expected_revision):
        raise ValueError('configurationChanged')
    if config.get(STATE_KEY):
        return
    config[STATE_KEY] = {
        'before': {key: deepcopy(config[key]) for key in fields(slots) if key in config},
        'fields': fields(slots), 'slots': slots, 'model': model,
    }
    config['enableCustomApi'] = True
    for slot in slots:
        config.update({f'{slot}ModelProvider': 'chatgpt', f'{slot}ModelId': model,
                       f'{slot}ModelUrl': 'https://api.openai.com/v1', f'{slot}ModelApiKey': ''})


def disable(config):
    state = config.get(STATE_KEY)
    if not state:
        return False
    # Restore field presence as well as values; unrelated settings keep their latest values.
    for key in state['fields']:
        if key in state['before']:
            config[key] = deepcopy(state['before'][key])
        else:
            config.pop(key, None)
    del config[STATE_KEY]
    return True


def runtime_custom_api(config):
    """Enabling text slots must not activate dormant custom speech/image settings."""
    from utils.config_manager._shared import _as_bool

    state = config.get(STATE_KEY) or {}
    # Normalize on read, including legacy snapshots, so disable can still
    # restore the original value and field presence exactly.
    if state and not _as_bool(state['before'].get('enableCustomApi', False)):
        return False, state['slots']
    return _as_bool(config.get('enableCustomApi', False)), []
