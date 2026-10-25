"""Portable learned-memory snapshots; raw conversations and model caches are excluded."""

from copy import deepcopy
from datetime import datetime, timedelta
import math

MAX_INTEGER = 9_007_199_254_740_991
SUBJECTS = frozenset(("master", "neko", "relationship"))
KINDS = frozenset(("persona", "fact", "reflection"))
_NUMBER_FIELDS = frozenset(("createdAt", "updatedAt", "eventAt", "sourceCreatedAt", "importance",
    "reinforcement", "disputation", "reinLastSignalAt", "dispLastSignalAt", "userFactReinforceCount",
    "promotedAt", "voidedAt", "evidenceReviewedAt", "evidenceReviewAttemptAt"))
_BOOL_FIELDS = frozenset(("protected", "absorbed", "archived"))
_ID_LIST_FIELDS = frozenset(("sourceMessageIds", "sourceFactIds", "sourceReflectionIds"))
_TEXT_FIELDS = frozenset(("sourceSyncId", "supersededById"))
_STATUS = frozenset(("active", "pending", "confirmed", "promoted", "denied", "superseded", "retracted", "merged", "archived"))
_REVIEW = frozenset(("supported", "unsupported", "missing_sources"))


def _text(value, limit):
    if not isinstance(value, str) or not value or len(value) > limit:
        raise ValueError("invalid_long_term_memory")
    value.encode("utf-8")
    return value


def document(value):
    """Validate only learned entries, without accepting arbitrary native file payloads."""
    if (not isinstance(value, dict) or set(value) != {"schemaVersion", "completedAt", "entries"}
            or type(value["schemaVersion"]) is not int or value["schemaVersion"] != 1
            or type(value["completedAt"]) is not int or not 0 < value["completedAt"] <= MAX_INTEGER
            or not isinstance(value["entries"], list) or len(value["entries"]) > 20_000):
        raise ValueError("invalid_long_term_memory")
    try:
        datetime(1970, 1, 1) + timedelta(milliseconds=value['completedAt'])
    except OverflowError:
        raise ValueError("invalid_long_term_memory") from None
    seen = set()
    for row in value["entries"]:
        if (not isinstance(row, dict) or set(row) != {"kind", "subject", "id", "text", "metadata"}
                or not isinstance(row["kind"], str) or row["kind"] not in KINDS
                or not isinstance(row["subject"], str) or row["subject"] not in SUBJECTS
                or not isinstance(row["metadata"], dict)):
            raise ValueError("invalid_long_term_memory")
        identity = (row["kind"], row["subject"], _text(row["id"], 200))
        if identity in seen:
            raise ValueError("invalid_long_term_memory")
        seen.add(identity)
        _text(row["text"], 16_000)
        for key, item in row["metadata"].items():
            if key in _NUMBER_FIELDS:
                if type(item) not in (int, float) or not math.isfinite(item) or abs(item) > MAX_INTEGER:
                    raise ValueError("invalid_long_term_memory")
                if key.endswith('At'):
                    try:
                        datetime(1970, 1, 1) + timedelta(milliseconds=item)
                    except OverflowError:
                        raise ValueError("invalid_long_term_memory") from None
            elif key in _BOOL_FIELDS:
                if type(item) is not bool:
                    raise ValueError("invalid_long_term_memory")
            elif key in _ID_LIST_FIELDS:
                if not isinstance(item, list) or len(item) > 200:
                    raise ValueError("invalid_long_term_memory")
                for identity in item:
                    _text(identity, 200)
            elif key in _TEXT_FIELDS:
                _text(item, 200)
            elif key == "status":
                if not isinstance(item, str) or item not in _STATUS:
                    raise ValueError("invalid_long_term_memory")
            elif key == "evidenceReview":
                if not isinstance(item, str) or item not in _REVIEW:
                    raise ValueError("invalid_long_term_memory")
            else:
                raise ValueError("invalid_long_term_memory")
    return deepcopy(value)


def mutation(value):
    if not isinstance(value, dict) or set(value) != {"operationId", "document"}:
        raise ValueError("invalid_long_term_memory")
    from utils.recent_memory_protocol import identity
    identity(value["operationId"])
    return {"operationId": value["operationId"], "document": document(value["document"])}


def head(value, owner, uid):
    expected = {"ownerId", "characterSyncId", "revision", "completedAt"}
    if isinstance(value, dict) and 'document' in value:
        expected.add('document')
    if (not isinstance(value, dict) or set(value) != expected or value['ownerId'] != owner
            or value['characterSyncId'] != uid or type(value['revision']) is not int
            or not 0 <= value['revision'] <= MAX_INTEGER
            or (value['completedAt'] is not None and (type(value['completedAt']) is not int
                or not 0 < value['completedAt'] <= MAX_INTEGER))
            or (value['revision'] == 0) != (value['completedAt'] is None)):
        raise ValueError('invalid_long_term_memory')
    if 'document' in value:
        if value['document'] is not None:
            payload = document(value['document'])
            if payload['completedAt'] != value['completedAt']:
                raise ValueError('invalid_long_term_memory')
        elif value['revision']:
            raise ValueError('invalid_long_term_memory')
    return deepcopy(value)
