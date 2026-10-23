"""Neutral, bounded settings journal stored in the same atomic JSON as settings.

No session or domain module is imported here. The application supplies an
attribution fence; callers acquire it before the preferences file lock.
"""

from contextlib import contextmanager, nullcontext
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
import time
from uuid import uuid4

JOURNAL_KEY = "_settings_mutation_journal"
MAX_PENDING_CHANGES = 4096
_GLOBAL_KEY = "__global_conversation__"
# Explicit v1 upload protocol: adding a local preference never silently expands
# data collection or sends fields an older cloud receiver cannot interpret.
_PROACTIVE_BOOL_KEYS = frozenset({
    "proactiveChatEnabled", "proactiveVisionEnabled", "proactiveVisionChatEnabled",
    "proactiveNewsChatEnabled", "proactiveCommunityChatEnabled", "proactiveVideoChatEnabled",
    "proactivePersonalChatEnabled", "proactiveMusicEnabled", "proactiveMemeEnabled",
    "proactiveMiniGameInviteEnabled",
})
_PROACTIVE_INTERVAL_KEYS = frozenset({"proactiveChatInterval", "proactiveVisionInterval"})
_attribution_factory = None
_attribution = ContextVar("preferences_settings_attribution", default=None)
_capture_provider = None
_capture_protocol = 1
MAX_PENDING_BYTES = 4 * 1024 * 1024


def install_settings_capture_provider(provider, *, protocol_version=1):
    """Assembly injects a short in-memory capture read; no domain imports here."""
    if protocol_version not in (1, 2):
        raise ValueError("Unsupported settings capture protocol")
    global _capture_provider, _capture_protocol
    _capture_provider, _capture_protocol = provider, protocol_version


def install_settings_attribution_context(factory):
    """Install a sync context factory yielding {key, user_id, community}, or reset.

    The factory must hold its account/session writer fence until context exit.
    Installation is application assembly, performed before worker startup.
    """
    global _attribution_factory
    _attribution_factory = factory


@contextmanager
def settings_attribution_context(origin="user"):
    if origin not in {"user", "system", "baseline"}:
        raise ValueError("Invalid settings audit origin")
    anonymous = {"key": hashlib.sha256(b"\n").hexdigest(), "user_id": None, "community": None}
    factory = _attribution_factory
    with factory() if factory is not None else nullcontext(anonymous) as owner:
        if not isinstance(owner, dict) or not isinstance(owner.get("key"), str):
            raise ValueError("Settings attribution did not provide an owner")
        token = _attribution.set((deepcopy(owner), origin))
        try:
            yield
        finally:
            _attribution.reset(token)


def _global_entry(data):
    return next((p for p in data if isinstance(p, dict) and p.get("model_path") == _GLOBAL_KEY), {})


def read_journal(data):
    raw = _global_entry(data).get(JOURNAL_KEY)
    if raw is None:
        return {"version": 1, "revision": 0, "entries": []}
    if (
        not isinstance(raw, dict) or raw.get("version") != 1
        or type(raw.get("revision")) is not int or raw["revision"] < 0
        or not isinstance(raw.get("entries"), list)
        or len(raw["entries"]) > MAX_PENDING_CHANGES
        or any(not isinstance(e, dict) or not isinstance(e.get("id"), str)
               or not isinstance(e.get("owner"), dict) or not isinstance(e.get("payload"), dict)
               for e in raw["entries"])
    ):
        raise ValueError("Unrecognized or damaged settings audit journal")
    return deepcopy(raw)


def proactive_snapshot(data):
    return {
        k: v for k, v in _global_entry(data).items()
        if (k in _PROACTIVE_BOOL_KEYS and type(v) is bool)
        or (k in _PROACTIVE_INTERVAL_KEYS and type(v) is int and 1 <= v <= 3600)
    }


def attach_journal(data, journal):
    """Copy before modifying; caller-owned snapshots are unchanged on write failure."""
    result = deepcopy(data)
    for entry in result:
        if isinstance(entry, dict):
            entry.pop(JOURNAL_KEY, None)
    if journal["revision"] or journal["entries"]:
        entry = _global_entry(result)
        if not entry:
            entry = {"model_path": _GLOBAL_KEY}
            result.append(entry)
        entry[JOURNAL_KEY] = deepcopy(journal)
    return result


def prepare_atomic_preferences(previous, proposed):
    journal = read_journal(previous)
    if _capture_protocol == 2:
        # First upgrade and late legacy members keep current settings but do
        # not acquire a new capture merely because the worker imports them.
        journal["entries"] = [entry for entry in journal["entries"] if isinstance(entry.get("context"), dict)]
        journal["protocol_version"] = 2
    before, after = proactive_snapshot(previous), proactive_snapshot(proposed)
    if before != after:
        if _capture_protocol == 1 and len(journal["entries"]) >= MAX_PENDING_CHANGES:
            raise ValueError("Settings audit journal is full; synchronize before changing settings")
        attribution = _attribution.get()
        if attribution is None:
            raise RuntimeError("Settings write has no attribution fence")
        owner, origin = attribution
        journal["revision"] += 1
        entry = {
            "id": str(uuid4()),
            "owner": deepcopy(owner),
            "payload": {
                "revision": journal["revision"],
                "origin": "baseline" if not before and origin == "user" else origin,
                "occurred_at": time.time(),
                "previous": before,
                "settings": after,
            },
        }
        if _capture_protocol == 2:
            capture = _capture_provider(owner) if _capture_provider is not None else None
            # An official new mutation without server authority is partial,
            # distinct from a legacy entry with no context field whatsoever.
            entry["context"] = deepcopy(capture) if isinstance(capture, dict) else {"version": "desktop-feedback-capture-v2", "owner_key": owner["key"]}
            encoded = json.dumps(journal["entries"] + [entry], ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
            if len(journal["entries"]) >= MAX_PENDING_CHANGES or len(encoded) > MAX_PENDING_BYTES:
                journal["incomplete"] = True
            else:
                journal["entries"].append(entry)
        else:
            journal["entries"].append(entry)
    return attach_journal(proposed, journal)


def without_journal(data):
    result = deepcopy(data)
    for entry in result:
        if isinstance(entry, dict):
            entry.pop(JOURNAL_KEY, None)
    return result


def merge_local_journals(previous, current):
    """Preserve local-root migration facts by ID, with original owners intact.

    This is only for migrating local runtime directories, never archive import.
    Conflicts/capacity fail closed so neither local copy is silently discarded.
    """
    older, newer = read_journal(previous), read_journal(current)
    entries = {}
    for entry in older["entries"] + newer["entries"]:
        existing = entries.get(entry["id"])
        if existing is not None and existing != entry:
            raise ValueError("Conflicting settings audit entry during local migration")
        entries[entry["id"]] = entry
    if len(entries) > MAX_PENDING_CHANGES:
        raise ValueError("Settings audit capacity exceeded during local migration")
    merged = {"version": 1, "revision": max(older["revision"], newer["revision"]), "entries": list(entries.values())}
    # These are local journal state, not imported setting changes. Moving the
    # runtime directory must not hide earlier capacity loss or downgrade v2.
    if older.get("protocol_version") == 2 or newer.get("protocol_version") == 2:
        merged["protocol_version"] = 2
    elif older.get("protocol_version") == 1 or newer.get("protocol_version") == 1:
        merged["protocol_version"] = 1
    if "incomplete" in older or "incomplete" in newer:
        merged["incomplete"] = bool(older.get("incomplete") or newer.get("incomplete"))
    return merged
