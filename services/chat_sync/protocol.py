"""Strict v1 chat-original values and portable UTF-8 bytes.

The matching JSON Schema and golden fixtures live with the mobile codec. This
module validates representation only: callers still need verified provenance,
authenticated owner/character binding and deletion checks before using a record.
No model projections, filesystem paths or arbitrary metadata are accepted.
"""

from __future__ import annotations

import json
import re


MAX_ORIGIN_SEQ = 9_007_199_254_740_991  # Exact in both Python and JavaScript.
_OWNER_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
_SYNC_ID = re.compile(r"[0-9a-f]{32}\Z")
_FIELDS = frozenset((
    "schemaVersion", "ownerId", "characterSyncId", "channel", "scope",
    "messageId", "originStreamId", "originSeq", "role", "text",
    "originalTime", "timezone", "conversationId", "replyToMessageId",
))


def validate_sync_domain(owner_id: str, character_uid: str, channel: str, scope: str) -> None:
    """Require the server UUID, persisted desktop UID and first-release scope."""
    if not isinstance(owner_id, str) or not _OWNER_ID.fullmatch(owner_id):
        raise ValueError("invalid_owner_id")
    if not isinstance(character_uid, str) or not _SYNC_ID.fullmatch(character_uid):
        raise ValueError("invalid_character_uid")
    if channel != "chat" or scope != "private-chat":
        raise ValueError("unsupported_sync_domain")


def _valid_string(value: object, *, nonempty: bool = False) -> bool:
    """Reject invalid Unicode without normalizing even one original character."""
    if not isinstance(value, str) or (nonempty and not value):
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _validate_original(value: object) -> None:
    """Use an exact whitelist; extra derived or attachment fields are errors."""
    if not isinstance(value, dict) or value.keys() != _FIELDS:
        raise ValueError("invalid_chat_original")
    try:
        validate_sync_domain(value["ownerId"], value["characterSyncId"], value["channel"], value["scope"])
    except ValueError:
        raise ValueError("invalid_chat_original") from None
    if type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1:
        raise ValueError("invalid_chat_original")
    if type(value["originSeq"]) is not int or not 1 <= value["originSeq"] <= MAX_ORIGIN_SEQ:
        raise ValueError("invalid_chat_original")
    for field in ("messageId", "originStreamId"):
        if not isinstance(value[field], str) or not _SYNC_ID.fullmatch(value[field]):
            raise ValueError("invalid_chat_original")
    if value["role"] not in ("user", "assistant") or not _valid_string(value["text"]):
        raise ValueError("invalid_chat_original")
    for field in ("originalTime", "timezone", "conversationId"):
        if value[field] is not None and not _valid_string(value[field], nonempty=True):
            raise ValueError("invalid_chat_original")
    reply_id = value["replyToMessageId"]
    if reply_id is not None and (not isinstance(reply_id, str) or not _SYNC_ID.fullmatch(reply_id)):
        raise ValueError("invalid_chat_original")
    if value["timezone"] is not None and value["originalTime"] is None:
        raise ValueError("invalid_chat_original")


def encode_chat_original(value: object) -> bytes:
    """Encode only fixed ASCII keys and string/null/safe-integer values.

    These restrictions make Python's compact sorted JSON equal to JavaScript's
    sorted-field JSON.stringify output. This is not a general JSON canonicalizer.
    Hash these bytes, never a reconstructed projection or a compressed envelope.
    """
    _validate_original(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def decode_chat_original(payload: bytes) -> dict:
    """Reject noncanonical bytes, including duplicate keys and alternate escapes."""
    if not isinstance(payload, bytes):
        raise ValueError("invalid_chat_original")
    try:
        record = json.loads(payload.decode("utf-8"))
    except (ValueError, UnicodeError):
        raise ValueError("invalid_chat_original") from None
    if encode_chat_original(record) != payload:
        raise ValueError("non_canonical_chat_original")
    return record
