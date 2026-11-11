"""Chat messages another device said, kept until this device may extract facts from them.

The recent-memory projection holds only the last few messages, so what
arrives is copied here on arrival. Each arrival is its own file: the sync
process only ever adds files and the memory owner only ever removes them, so
the two never rewrite one another.
"""

import json
from pathlib import Path
import re
from uuid import uuid4

MAX_BATCHES = 400
_BATCH = re.compile(r'[0-9]{15}-[0-9a-f]{12}\.json')


def keep_arrivals(directory, entries, received_at):
    """Keep newly arrived user and character lines; received_at is this device's own clock."""
    from utils.file_utils import atomic_write_json
    rows = [{'id': row['id'], 'role': row['role'], 'text': row['text'], 'originalTime': row.get('originalTime')}
            for row in entries if row.get('role') in ('user', 'assistant') and isinstance(row.get('text'), str) and row['text'].strip()]
    directory = Path(directory)
    # A projection that could not be applied yet is offered again on the next
    # sync; lines already waiting here are not kept a second time.
    waiting = {entry['id'] for batch in batches(directory) for entry in batch['entries']}
    rows = [row for row in rows if row['id'] not in waiting]
    if not rows:
        return False
    directory.mkdir(parents=True, exist_ok=True)
    atomic_write_json(directory / f'{int(received_at):015d}-{uuid4().hex[:12]}.json',
                      {'receivedAt': int(received_at), 'entries': rows}, ensure_ascii=False)
    # A device that never extracts must not grow this without bound: the oldest
    # arrivals go first, and their facts are left to the device that said them.
    for stale in sorted(path for path in directory.iterdir() if _BATCH.fullmatch(path.name))[:-MAX_BATCHES]:
        stale.unlink(missing_ok=True)
    return True


def batches(directory):
    """Arrivals in the order they were received; an unreadable file is skipped, not fatal."""
    directory = Path(directory)
    if not directory.is_dir():
        return []
    result = []
    for path in sorted(path for path in directory.iterdir() if _BATCH.fullmatch(path.name)):
        try:
            value = json.loads(path.read_text(encoding='utf-8'))
            received_at, entries = int(value['receivedAt']), list(value['entries'])
        except (OSError, ValueError, KeyError, TypeError):
            continue
        result.append({'path': path, 'receivedAt': received_at, 'entries': entries})
    return result


def discard(paths):
    for path in paths:
        Path(path).unlink(missing_ok=True)


def message_dicts(entries):
    """The shape the extraction passes read, carrying the identity every device knows the line by."""
    return [{'type': 'human' if row['role'] == 'user' else 'ai',
             'data': {'content': row['text'],
                      'metadata': {'recent_memory_id': row['id'], 'recent_memory_time': row.get('originalTime')}}}
            for row in entries]
