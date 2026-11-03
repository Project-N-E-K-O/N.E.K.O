"""Recent-memory projections, independent of immutable chat originals.

Entries contain only text currently shown by the memory browser. Removing an
entry retires its projection ID; it is never an instruction to erase an archive.
"""

from copy import deepcopy
import json
import re


MAX_REVISION = 9_007_199_254_740_991
MEMO_ID = "0" * 32


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def identity(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("invalid_recent_memory")
    return value


def entries(value):
    if not isinstance(value, list):
        raise ValueError("invalid_recent_memory")
    seen = set()
    for item in value:
        if (not isinstance(item, dict) or set(item) != {"id", "role", "text", "originalTime"}
                or item["role"] not in ("user", "assistant", "memo") or not isinstance(item["text"], str)
                or (item["originalTime"] is not None and (not isinstance(item["originalTime"], str)
                    or not re.fullmatch(r"[0-9]{1,16}", item["originalTime"])
                    or int(item["originalTime"]) > MAX_REVISION))):
            raise ValueError("invalid_recent_memory")
        key = identity(item["id"])
        if key in seen or (item["role"] == "memo") != (key == MEMO_ID):
            raise ValueError("invalid_recent_memory")
        item["text"].encode("utf-8")
        seen.add(key)
    return deepcopy(value)


def snapshot(value, owner, uid):
    if (not isinstance(value, dict) or set(value) != {"schemaVersion", "ownerId", "characterSyncId", "revision", "entries"}
            or value["ownerId"] != owner or value["characterSyncId"] != uid
            or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1
            or type(value["revision"]) is not int or not 0 <= value["revision"] <= MAX_REVISION):
        raise ValueError("invalid_recent_memory")
    return {**value, "entries": entries(value["entries"])}


def mutation(value):
    if not isinstance(value, dict) or set(value) != {"operationId", "upserts", "removedIds"}:
        raise ValueError("invalid_recent_memory")
    identity(value["operationId"])
    result = {**value, "upserts": entries(value["upserts"])}
    removed = value["removedIds"]
    if not isinstance(removed, list) or len(set(map(identity, removed))) != len(removed):
        raise ValueError("invalid_recent_memory")
    if {row["id"] for row in result["upserts"]} & set(removed):
        raise ValueError("invalid_recent_memory")
    return deepcopy(result)


def changes(before, after):
    """Diff by persisted entry identity, preserving equal-text siblings."""
    old = {row["id"]: row for row in entries(before)}
    new = {row["id"]: row for row in entries(after)}
    return {"upserts": [row for key, row in new.items() if old.get(key) != row],
            "removedIds": [key for key in old if key not in new]}


def merge(current, removed, change):
    """Merge concurrent additions; retired message IDs cannot be resurrected.

Memo edits and edits of a retained entry follow accepted server operation order.
An explicit memo replacement is allowed after clearing its previous value.
"""
    change = mutation(change)
    hidden = set(map(identity, removed)) | set(change["removedIds"])
    rows = {row["id"]: row for row in entries(current)}
    for key in change["removedIds"]:
        rows.pop(key, None)
    for row in change["upserts"]:
        if row['id'] in rows and rows[row['id']]['role'] != row['role']:
            raise ValueError('invalid_recent_memory')
        if row["id"] == MEMO_ID:
            hidden.discard(MEMO_ID)
        if row["id"] not in hidden:
            rows[row["id"]] = row
    return sorted(rows.values(), key=lambda row: row['role'] != 'memo'), sorted(hidden)
