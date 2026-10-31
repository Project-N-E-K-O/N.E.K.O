"""Portable learned-memory snapshots; raw conversations and model caches are excluded."""

from copy import deepcopy
from datetime import datetime, timedelta
import math

MAX_INTEGER = 9_007_199_254_740_991
SUBJECTS = frozenset(("master", "neko", "relationship"))
KINDS = frozenset(("persona", "fact", "reflection"))
_NUMBER_FIELDS = frozenset(("createdAt", "updatedAt", "eventAt", "sourceCreatedAt", "importance",
    "reinforcement", "disputation", "reinLastSignalAt", "dispLastSignalAt", "userFactReinforceCount",
    "promotedAt", "voidedAt", "evidenceReviewedAt", "evidenceReviewAttemptAt", "eventStartAt", "eventEndAt", "sourceUpgradedAt", "confirmedAt", "archivedAt", "suppressedAt", "subZeroDays"))
_BOOL_FIELDS = frozenset(("protected", "absorbed", "archived", "autoConfirmed"))
_ID_LIST_FIELDS = frozenset(("sourceMessageIds", "sourceFactIds", "sourceReflectionIds", "signalFactIds", "mergedFromIds"))
_TEXT_FIELDS = frozenset(("sourceSyncId", "supersededById", "absorbedInto", "relationType", "temporalScope", "subZeroLastIncrementDate", "synthesisBatchId"))
_STATUS = frozenset(("active", "pending", "confirmed", "promoted", "promote_blocked", "denied", "superseded", "retracted", "merged", "archived"))
_REVIEW = frozenset(("supported", "unsupported", "missing_sources"))


def _text(value, limit):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("invalid_long_term_memory")
    value.encode("utf-8")
    return value


def _identifier(value, limit):
    _text(value, limit)
    if value != value.strip(): raise ValueError('invalid_long_term_memory')
    return value


def _number(value):
    if type(value) not in (int, float) or not math.isfinite(value) or abs(value) > MAX_INTEGER:
        raise ValueError('invalid_long_term_memory')


def _event_when(value):
    if value is None: return
    if not isinstance(value, dict) or set(value) != {'start', 'end'}: raise ValueError('invalid_long_term_memory')
    for row in value.values():
        if row is None: continue
        if not isinstance(row, dict) or set(row) != {'offset', 'unit'} or type(row['offset']) is not int or abs(row['offset']) > MAX_INTEGER or row['unit'] not in ('minute', 'hour', 'day', 'week', 'month', 'year'):
            raise ValueError('invalid_long_term_memory')


def _versions(value):
    if not isinstance(value, list) or len(value) > 5: raise ValueError('invalid_long_term_memory')
    for row in value:
        if not isinstance(row, dict) or set(row) != {'text', 'replacedAt', 'reason'} | ({'detail'} if 'detail' in row else set()): raise ValueError('invalid_long_term_memory')
        _text(row['text'], 16000); _number(row['replacedAt'])
        if row['reason'] not in ('correction', 'refine_merge', 'refine_split', 'refine_modify', 'refine_discard'): raise ValueError('invalid_long_term_memory')
        if 'detail' in row: _text(row['detail'], 300)


def _evidence(value):
    import re
    if not isinstance(value, dict) or set(value) != {'retirements', 'confirmations'}: raise ValueError('invalid_long_term_memory')
    for key in value:
        if not isinstance(value[key], list) or len(value[key]) > 20000: raise ValueError('invalid_long_term_memory')
    ids = set()
    for row in value['retirements']:
        if not isinstance(row, dict) or row.get('subject') not in SUBJECTS or not row.get('sourceMessageIds'): raise ValueError('invalid_long_term_memory')
        _identifier(row.get('id'), 200)
        if row['id'] in ids: raise ValueError('invalid_long_term_memory')
        ids.add(row['id'])
        for key, item in row.items():
            if key in ('id', 'subject'): continue
            if key == 'text': _text(item, 300)
            elif key in ('sourceMessageIds', 'retiredBySourceMessageIds'):
                if not isinstance(item, list) or len(item) > 20000: raise ValueError('invalid_long_term_memory')
                for identifier in item: _identifier(identifier, 200)
                if len(item) != len(set(item)): raise ValueError('invalid_long_term_memory')
            elif key in ('sourceCreatedAt', 'retiredThroughAt', 'eventAt', 'eventStartAt'): _number(item)
            elif key == 'eventEndAt':
                if item is not None: _number(item)
            elif key == 'retiredThroughAtFromCorrection':
                if item is not True or 'retiredThroughAt' not in row: raise ValueError('invalid_long_term_memory')
                _number(row['retiredThroughAt'])
            else: raise ValueError('invalid_long_term_memory')
    ids.clear()
    for row in value['confirmations']:
        if not isinstance(row, dict) or set(row) != {'fingerprint', 'factId'}: raise ValueError('invalid_long_term_memory')
        _identifier(row['factId'], 200)
        fp = _text(row['fingerprint'], 30)
        if not re.fullmatch(r'\d{1,8}:\d{1,10}:\d{1,10}', fp) or fp in ids or any(int(part) > (0xffffffff if i else 99999999) or i == 0 and int(part) == 0 for i, part in enumerate(fp.split(':'))): raise ValueError('invalid_long_term_memory')
        ids.add(fp)


def document(value):
    """Validate only learned entries, without accepting arbitrary native file payloads."""
    if (not isinstance(value, dict) or set(value) != ({"schemaVersion", "completedAt", "entries", "evidence"} if value.get("schemaVersion") == 2 else {"schemaVersion", "completedAt", "entries"})
            or type(value["schemaVersion"]) is not int or value["schemaVersion"] not in (1, 2)
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
        identity = (row["kind"], row["subject"], _identifier(row["id"], 200))
        if identity in seen:
            raise ValueError("invalid_long_term_memory")
        seen.add(identity)
        _text(row["text"], 16_000)
        for key, item in row["metadata"].items():
            if key == "eventEndAt" and item is None:
                continue
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
                if not isinstance(item, list) or len(item) > (1000 if key == "signalFactIds" else 200):
                    raise ValueError("invalid_long_term_memory")
                for identity in item:
                    _identifier(identity, 200)
            elif key in _TEXT_FIELDS:
                if item is not None or key not in ('relationType', 'temporalScope'):
                    _text(item, 200)
            elif key == 'source':
                if item not in ('chat', 'user_observation', 'ai_disclosure'): raise ValueError('invalid_long_term_memory')
            elif key == 'sourceAttribution':
                if item != 'batch': raise ValueError('invalid_long_term_memory')
            elif key == 'arbitrationAction':
                if item not in ('merge', 'replace'): raise ValueError('invalid_long_term_memory')
            elif key == 'archiveReason':
                if item not in ('negative_evidence', 'capacity', 'refinement', 'age'): raise ValueError('invalid_long_term_memory')
            elif key == 'eventWhen':
                _event_when(item)
            elif key == 'versionHistory':
                _versions(item)
            elif key == 'recentMentions':
                if not isinstance(item, list) or len(item) > 3: raise ValueError('invalid_long_term_memory')
                for row in item:
                    if not isinstance(row, dict) or set(row) != {'messageId', 'at'}: raise ValueError('invalid_long_term_memory')
                    _text(row['messageId'], 160); _number(row['at'])
            elif key == "status":
                if not isinstance(item, str) or item not in _STATUS:
                    raise ValueError("invalid_long_term_memory")
            elif key == "evidenceReview":
                if not isinstance(item, str) or item not in _REVIEW:
                    raise ValueError("invalid_long_term_memory")
            else:
                raise ValueError("invalid_long_term_memory")
    if value['schemaVersion'] == 2: _evidence(value['evidence'])
    return deepcopy(value)


def mutation(value):
    if not isinstance(value, dict) or set(value) != {"operationId", "document"}:
        raise ValueError("invalid_long_term_memory")
    from utils.recent_memory_protocol import identity
    identity(value["operationId"])
    return {"operationId": value["operationId"], "document": document(value["document"])}


def head(value, owner, uid):
    expected = {"ownerId", "characterSyncId", "revision", "completedAt"}
    if isinstance(value, dict) and 'expired' in value:
        expected.add('expired')
        if value['expired'] is not True or not value.get('revision') or value.get('document') is not None:
            raise ValueError('invalid_long_term_memory')
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
        elif value['revision'] and not value.get('expired'):
            raise ValueError('invalid_long_term_memory')
    return deepcopy(value)
