# Copyright 2025-2026 Project N.E.K.O. Team; SPDX-License-Identifier: Apache-2.0
"""Reversible subscription configuration. Private snapshots never leave the server."""
from copy import deepcopy
import hashlib
import hmac
import json
import secrets

STATE_KEY = 'chatgptSubscription'
_REVISION_KEY = secrets.token_bytes(32)
TEXT_SLOTS = frozenset(('conversation', 'summary', 'gameMain', 'gameSummary',
                        'correction', 'emotion', 'vision', 'agent'))


def supported_slots():
    from utils.api_config_loader import get_config
    declared = get_config()['assist_api_providers']['chatgpt']['supported_model_types']
    # Voice/image generation must never be enrolled by metadata alone.
    return [slot for slot in declared if slot in TEXT_SLOTS]


def fields(slots):
    return ['enableCustomApi', *[f'{slot}Model{suffix}' for slot in slots
                               for suffix in ('Provider', 'Url', 'Id', 'ApiKey')]]


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
    state = config.get(STATE_KEY) or {}
    if state and not state['before'].get('enableCustomApi', False):
        return False, state['slots']
    return config.get('enableCustomApi', False), []
