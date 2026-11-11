"""Portable learned-memory snapshots; raw conversations and model caches are excluded."""

from copy import deepcopy
from datetime import datetime, timedelta
import math
import re

MAX_INTEGER = 9_007_199_254_740_991
MAX_ENTRIES = 20_000
SCHEMA_VERSION = 3
PROGRESS_MAX = 2000
ORGANIZER_RANKS = (1, 2, 3, 4)
_V3_METADATA = frozenset(("signalJudged", "userConfirmCount", "countedFactIds", "factSourceCounts"))
SUBJECTS = frozenset(("master", "neko", "relationship"))
KINDS = frozenset(("persona", "fact", "reflection"))
_NUMBER_FIELDS = frozenset(("createdAt", "updatedAt", "eventAt", "sourceCreatedAt", "importance",
    "reinforcement", "disputation", "reinLastSignalAt", "dispLastSignalAt", "userFactReinforceCount",
    "promotedAt", "voidedAt", "evidenceReviewedAt", "evidenceReviewAttemptAt", "eventStartAt", "eventEndAt", "sourceUpgradedAt", "confirmedAt", "archivedAt", "suppressedAt", "subZeroDays"))
_BOOL_FIELDS = frozenset(("protected", "absorbed", "archived", "autoConfirmed", "signalJudged"))
_ID_LIST_FIELDS = frozenset(("sourceMessageIds", "sourceFactIds", "sourceReflectionIds", "signalFactIds", "mergedFromIds", "countedFactIds"))
_TEXT_FIELDS = frozenset(("sourceSyncId", "supersededById", "absorbedInto", "relationType", "temporalScope", "subZeroLastIncrementDate", "synthesisBatchId"))
_STATUS = frozenset(("active", "pending", "confirmed", "promoted", "promote_blocked", "denied", "superseded", "retracted", "merged", "archived"))
_REVIEW = frozenset(("supported", "unsupported", "missing_sources"))
COLD_MAX = 20_000
_COLD_STATUS = {"fact": frozenset(("superseded", "retracted")),
                "reflection": frozenset(("archived",)), "persona": frozenset(("archived",))}
_COLD_FIELDS = frozenset(("kind", "subject", "id", "status", "archivedAt"))


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


def _timestamp(value):
    """Millisecond times must also convert to a calendar date, as clients parse them."""
    _number(value)
    try:
        datetime(1970, 1, 1) + timedelta(milliseconds=value)
    except OverflowError:
        raise ValueError("invalid_long_term_memory") from None


def _cold(value, active):
    """Cold-stored rows travel as identities and states only; their text stays on the device.

    Same shape the sync service checks: no row both cold and an entry, no
    repeated row, at most COLD_MAX rows.
    """
    if not isinstance(value, list) or len(value) > COLD_MAX:
        raise ValueError("invalid_long_term_memory")
    seen = set()
    for row in value:
        if (not isinstance(row, dict) or not isinstance(row.get("kind"), str) or row["kind"] not in KINDS
                or not isinstance(row.get("status"), str) or row["status"] not in _COLD_STATUS[row["kind"]]):
            raise ValueError("invalid_long_term_memory")
        superseded = row["kind"] == "fact" and row["status"] == "superseded"
        if (set(row) != _COLD_FIELDS | ({"supersededById"} if superseded and "supersededById" in row else set())
                or not isinstance(row["subject"], str) or row["subject"] not in SUBJECTS):
            raise ValueError("invalid_long_term_memory")
        identity = (row["kind"], row["subject"], _identifier(row["id"], 200))
        if identity in seen or identity in active:
            raise ValueError("invalid_long_term_memory")
        seen.add(identity)
        _timestamp(row["archivedAt"])
        if "supersededById" in row:
            _text(row["supersededById"], 200)


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


def _count(value):
    if type(value) is not int or not 0 <= value <= MAX_INTEGER:
        raise ValueError('invalid_long_term_memory')


def _progress(value):
    """Each pass lists the message identities it has already sent through extraction."""
    if not isinstance(value, dict) or set(value) != {'a', 'b'}:
        raise ValueError('invalid_long_term_memory')
    for items in value.values():
        if not isinstance(items, list) or len(items) > PROGRESS_MAX:
            raise ValueError('invalid_long_term_memory')
        for item in items:
            if not isinstance(item, str) or not re.fullmatch(r'[0-9a-f]{32}', item):
                raise ValueError('invalid_long_term_memory')
        if len(items) != len(set(items)):
            raise ValueError('invalid_long_term_memory')


def _organizer(value):
    """The holder's own clock only makes a renewal differ; no reader compares it with theirs."""
    if (not isinstance(value, dict) or set(value) != {'deviceId', 'renewedAt', 'rank'}
            or not isinstance(value['deviceId'], str) or not re.fullmatch(r'[0-9a-f]{32}', value['deviceId'])
            or type(value['renewedAt']) is not int or not 0 < value['renewedAt'] <= MAX_INTEGER
            or type(value['rank']) is not int or value['rank'] not in ORGANIZER_RANKS):
        raise ValueError('invalid_long_term_memory')


def _maintenance(value):
    if not isinstance(value, dict) or len(value) > 16:
        raise ValueError('invalid_long_term_memory')
    for key, item in value.items():
        if (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9]{0,39}', key)
                or type(item) is not int or not 1 <= item <= 1000):
            raise ValueError('invalid_long_term_memory')


def _top_level(value):
    version = value.get("schemaVersion")
    if version == 3:
        required = {"schemaVersion", "completedAt", "entries", "evidence", "progress"}
        return required <= set(value) <= required | {"organizer", "maintenance", "lineage", "cold"}
    return set(value) == ({"schemaVersion", "completedAt", "entries", "evidence"} if version == 2
                          else {"schemaVersion", "completedAt", "entries"})


def document(value):
    """Validate only learned entries, without accepting arbitrary native file payloads."""
    if (not isinstance(value, dict) or not _top_level(value)
            or type(value["schemaVersion"]) is not int or value["schemaVersion"] not in (1, 2, 3)
            or type(value["completedAt"]) is not int or not 0 < value["completedAt"] <= MAX_INTEGER
            or not isinstance(value["entries"], list) or len(value["entries"]) > MAX_ENTRIES):
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
            if key in _V3_METADATA and value["schemaVersion"] != 3:
                raise ValueError("invalid_long_term_memory")
            if key == "eventEndAt" and item is None:
                continue
            if key == "userConfirmCount":
                _count(item)
            elif key == "factSourceCounts":
                if not isinstance(item, dict) or set(item) != {"user", "ai"}:
                    raise ValueError("invalid_long_term_memory")
                for count in item.values():
                    _count(count)
            elif key in _NUMBER_FIELDS:
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
    if value['schemaVersion'] >= 2:
        _evidence(value['evidence'])
    if value['schemaVersion'] == 3:
        _progress(value['progress'])
        if 'organizer' in value:
            _organizer(value['organizer'])
        if 'maintenance' in value:
            _maintenance(value['maintenance'])
        # Raised by a writer that could not read the revision it replaces, so readers
        # holding that revision do not mistake the missing entries for deletions.
        if 'lineage' in value and (type(value['lineage']) is not int or not 1 <= value['lineage'] <= MAX_INTEGER):
            raise ValueError('invalid_long_term_memory')
        if 'cold' in value:
            _cold(value['cold'], seen)
    return deepcopy(value)


def mutation(value):
    """A base revision makes the write conditional; schema 3 is never written without one."""
    if (not isinstance(value, dict)
            or not {"operationId", "document"} <= set(value) <= {"operationId", "baseRevision", "document"}):
        raise ValueError("invalid_long_term_memory")
    from utils.recent_memory_protocol import identity
    identity(value["operationId"])
    if "baseRevision" in value:
        _count(value["baseRevision"])
    checked = document(value["document"])
    if checked['schemaVersion'] == 3 and "baseRevision" not in value:
        raise ValueError("invalid_long_term_memory")
    return {**value, "document": checked}


def receipt(value, owner, uid, operation):
    """A refused conditional write names the revision to merge with instead of being accepted."""
    expected = {'ownerId', 'characterSyncId', 'operationId', 'revision', 'accepted'}
    if (not isinstance(value, dict) or not expected <= set(value) <= expected | {'conflict'}
            or value['ownerId'] != owner or value['characterSyncId'] != uid or value['operationId'] != operation
            or type(value['accepted']) is not bool or type(value['revision']) is not int
            or ('conflict' in value and (value['conflict'] is not True or value['accepted']))
            or not (1 if value['accepted'] else 0) <= value['revision'] <= MAX_INTEGER):
        raise ValueError('invalid_long_term_memory')
    return dict(value)


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
