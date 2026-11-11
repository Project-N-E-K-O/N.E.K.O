"""Which chat messages each fact-extraction pass has already sent to the model.

The lists travel in the learned-memory snapshot, so a device that receives a
message another device already extracted from does not extract from it again.
Pass ``a`` reads what the user said; pass ``b`` reads both sides.
"""

import json
from pathlib import Path
import re

from utils.long_term_memory_protocol import PROGRESS_MAX

PASSES = ('a', 'b')
STATE_FILE = 'long_term_portable.json'


def message_identity(message):
    """The identity both devices know a message by, or None for one that never left this device."""
    from memory.long_term_sync import trusted_message_source
    identity = trusted_message_source(message)[0]
    if isinstance(identity, str) and re.fullmatch(r'sync:[0-9a-f]{32}', identity):
        return identity[5:]
    return None


def identities(messages):
    return list(dict.fromkeys(identity for identity in map(message_identity, messages or ()) if identity))


def unprocessed(messages, done):
    """Messages a pass still has to read; one without a shared identity is always read."""
    return [message for message in messages if message_identity(message) not in done]


def read(directory):
    try:
        state = json.loads((Path(directory) / STATE_FILE).read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return {name: [] for name in PASSES}
    progress = state.get('progress') if isinstance(state, dict) else None
    progress = progress if isinstance(progress, dict) else {}
    return {name: [item for item in progress.get(name) or [] if isinstance(item, str)] for name in PASSES}


def record(directory, passage, processed):
    """Append newly processed identities; the caller holds the lock that guards facts.

    Returns whether anything was written. The oldest entries drop first, in
    the order the published list already has, so every device drops the same.
    """
    from utils.file_utils import atomic_write_json
    if passage not in PASSES:
        raise ValueError('invalid_extraction_pass')
    path = Path(directory) / STATE_FILE
    try:
        state = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        state = {}
    if not isinstance(state, dict):
        raise ValueError('invalid_long_term_memory')
    progress = state.get('progress') if isinstance(state.get('progress'), dict) else {}
    current = [item for item in progress.get(passage) or [] if isinstance(item, str)]
    known = set(current)
    added = [identity for identity in dict.fromkeys(processed) if identity not in known]
    if not added:
        return False
    state['progress'] = {**{name: list(progress.get(name) or []) for name in PASSES},
                         passage: (current + added)[-PROGRESS_MAX:]}
    atomic_write_json(path, state, ensure_ascii=False, indent=2)
    return True
