"""Text projection of the existing recent-memory file; archives are never opened."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from uuid import uuid4

from memory.message_sources import is_theater_memory_message
from utils.recent_memory_protocol import MEMO_ID, canonical, entries, identity
from utils.character_memory import list_character_recent_paths
from utils.cloudsave_runtime import assert_cloudsave_writable
from utils.llm_client import message_metadata, messages_to_dict
from utils.recent_file import (
    get_recent_pending_unlocked, read_recent_text_unlocked, recent_file_access,
    set_recent_pending_unlocked, write_recent_payload_unlocked,
)


ID_KEY = "recent_memory_id"
TIME_KEY = "recent_memory_time"


def _payload(path):
    try:
        rows = json.loads(read_recent_text_unlocked(path))
    except FileNotFoundError:
        rows = []
    if not isinstance(rows, list):
        raise ValueError("invalid_recent_memory")
    return rows + messages_to_dict(get_recent_pending_unlocked(path))


def _fingerprint(rows):
    return hashlib.sha256(canonical(rows)).hexdigest()


def _data(row):
    return row.get('data') if 'type' in row else row


def _writable_metadata(row):
    if isinstance(row.get('metadata'), dict):
        return row['metadata']
    data = _data(row)
    if not isinstance(data.get('metadata'), dict):
        data['metadata'] = {}
    return data['metadata']


def _text(data):
    content = data.get('content')
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return '\n'.join(block if isinstance(block, str) else str(block['text'])
                         for block in content if isinstance(block, str) or
                         (isinstance(block, dict) and block.get('type') == 'text' and block.get('text') is not None))
    return '' if content is None else str(content)


def _project(rows, *, assign=False):
    result, indexes, seen = [], {}, set()
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or is_theater_memory_message(row):
            continue
        role = {"human": "user", "ai": "assistant", "system": "memo", "user": "user", "assistant": "assistant"}.get(row.get("type", row.get('role')))
        data = _data(row)
        if role is None or not isinstance(data, dict):
            continue
        if role == "memo" and MEMO_ID in seen:
            continue
        metadata = message_metadata(row)
        proof = metadata.get("chat_original_source")
        proof = proof if isinstance(proof, dict) else {}
        key = MEMO_ID if role == "memo" else metadata.get(ID_KEY) or proof.get("messageId")
        try:
            identity(key)
            if key in seen or (role != "memo" and key == MEMO_ID):
                raise ValueError("duplicate_recent_memory_id")
        except ValueError:
            if not assign:
                raise ValueError("recent_memory_identity_missing") from None
            key = uuid4().hex
        if assign and role != "memo":
            _writable_metadata(row)[ID_KEY] = key
        original_time = metadata.get(TIME_KEY, proof.get("originalTime")) if role != 'memo' else None
        if (not isinstance(original_time, str) or not original_time.isascii() or not original_time.isdecimal()
                or len(original_time) > 16 or int(original_time) > 9_007_199_254_740_991):
            original_time = None
        if assign and role != 'memo':
            _writable_metadata(row)[TIME_KEY] = original_time
        result.append({"id": key, "role": role, "text": _text(data), "originalTime": original_time})
        seen.add(key)
        indexes[key] = index
    return entries(sorted(result, key=lambda row: row['role'] != 'memo')), indexes


class RecentMemoryAdapter:
    def __init__(self, config_manager):
        self.config = config_manager

    def path(self, name):
        candidates = list_character_recent_paths(self.config, name)
        return next((Path(path) for path in candidates if Path(path).is_file()), Path(candidates[0]))

    def read(self, name):
        path = self.path(name)
        with recent_file_access(path) as resolved:
            rows = _payload(resolved)
            before = deepcopy(rows)
            projected, _ = _project(rows, assign=True)
            if rows != before:
                assert_cloudsave_writable(self.config, operation="sync", target=f"memory/{name}/recent.json")
                write_recent_payload_unlocked(resolved, rows)
                set_recent_pending_unlocked(resolved, [])
            return {"entries": projected, "fingerprint": _fingerprint(rows)}

    def apply(self, name, incoming, expected_fingerprint):
        incoming = entries(incoming)
        path = self.path(name)
        with recent_file_access(path) as resolved:
            rows = _payload(resolved)
            if _fingerprint(rows) != expected_fingerprint:
                return False
            previous, indexes = _project(rows)
            if previous == incoming:
                return True
            assert_cloudsave_writable(self.config, operation="sync", target=f"memory/{name}/recent.json")
            projected = []
            for entry in incoming:
                original = rows[indexes[entry["id"]]] if entry["id"] in indexes else None
                row = deepcopy(original) if original is not None else {
                    "type": {"user": "human", "assistant": "ai", "memo": "system"}[entry["role"]],
                    "data": {"metadata": {ID_KEY: entry["id"]}},
                }
                data = _data(row)
                if _text(data) != entry['text']:
                    data['content'] = entry['text']
                _writable_metadata(row).update({ID_KEY: entry["id"], TIME_KEY: entry['originalTime']})
                projected.append(row)
            # Local theater capsules and non-chat entries stay local and intact.
            owned = set(indexes.values())
            for index, row in enumerate(rows):
                if index not in owned:
                    projected.insert(min(index, len(projected)), row)
            write_recent_payload_unlocked(resolved, projected)
            set_recent_pending_unlocked(resolved, [])
            return True
