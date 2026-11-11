"""Three-way merge of portable learned-memory documents.

Pure functions over plain JSON values, so every client can apply the same
rules and run the same samples (tests/fixtures/long_term_merge_cases.json).
Nothing here reads a clock, a file or a native memory row.
"""

from copy import deepcopy
import math

SCHEMA_VERSION = 3
PROGRESS_MAX = 2000
MAX_INTEGER = 9_007_199_254_740_991

# A later lifecycle stage wins a contested wording; an equal one goes to the
# side the server already holds.
STATUS_RANK = {'pending': 0, 'active': 1, 'confirmed': 2, 'promote_blocked': 3, 'promoted': 4, 'archived': 5,
               'denied': 6, 'merged': 6, 'superseded': 6, 'retracted': 6}
ONLY_TURNS_TRUE = frozenset(('absorbed', 'archived', 'signalJudged'))
COUNTERS = frozenset(('reinforcement', 'disputation', 'userConfirmCount', 'userFactReinforceCount', 'importance'))
# A day both devices counted is still one day, so these never add up.
LATEST = frozenset(('subZeroDays', 'subZeroLastIncrementDate', 'reinLastSignalAt', 'dispLastSignalAt', 'updatedAt',
                    'promotedAt', 'confirmedAt', 'evidenceReviewedAt', 'evidenceReviewAttemptAt', 'sourceUpgradedAt',
                    'archivedAt', 'voidedAt', 'suppressedAt'))
ID_LISTS = {'sourceMessageIds': 200, 'sourceFactIds': 200, 'sourceReflectionIds': 200, 'mergedFromIds': 200,
            'countedFactIds': 200, 'signalFactIds': 1000}
_ABSENT = object()


def empty_evidence():
    return {'retirements': [], 'confirmations': []}


def upgraded(document):
    """Read an older document as schema 3: its facts were all judged by their writer."""
    if document is None:
        return None
    if document['schemaVersion'] == SCHEMA_VERSION:
        return document
    result = {'schemaVersion': SCHEMA_VERSION, 'completedAt': document['completedAt'],
              'entries': upgraded_entries(document['entries']),
              'evidence': deepcopy(document.get('evidence') or empty_evidence()), 'progress': {'a': [], 'b': []}}
    return result


def upgraded_entries(entries):
    return [{**row, 'metadata': {**row['metadata'], 'signalJudged': True}}
            if row['kind'] == 'fact' and 'signalJudged' not in row['metadata'] else row for row in entries]


def _key(row):
    return row['kind'], row['subject'], row['id']


def _corrections(row):
    return sum(1 for item in row['metadata'].get('versionHistory') or [] if item.get('reason') == 'correction')


def _user_decided(local, base):
    """A confirmation or correction made here outlives a deletion made elsewhere."""
    before = base['metadata'] if base else {}
    return (local['metadata'].get('userConfirmCount', 0) > before.get('userConfirmCount', 0)
            or _corrections(local) > (_corrections(base) if base else 0))


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _merge_ids(base_local, local, base_remote, remote, limit):
    removed = (set(base_local or ()) - set(local or ())) | (set(base_remote or ()) - set(remote or ()))
    merged = [item for item in dict.fromkeys([*(remote or ()), *(local or ())]) if item not in removed]
    return merged[-limit:]


def _merge_field(name, base_local, local, base_remote, remote, local_wins, has_base):
    if local is _ABSENT or remote is _ABSENT:
        held_by_local = remote is _ABSENT
        if (base_remote if held_by_local else base_local) is _ABSENT:
            # The other side never had the field, so nothing contests it.
            return local if held_by_local else remote
        # One side dropped the field while the other rewrote it: the contested-wording rule decides.
        return local if local_wins else remote
    if name in ONLY_TURNS_TRUE:
        return bool(local) or bool(remote)
    if name in ID_LISTS and isinstance(local, list) and isinstance(remote, list):
        return _merge_ids(None if base_local is _ABSENT else base_local, local,
                          None if base_remote is _ABSENT else base_remote, remote, ID_LISTS[name])
    if name in COUNTERS and _number(local) and _number(remote):
        if not has_base or (name == 'importance' and base_local is _ABSENT):
            value = max(local, remote)
        else:
            value = remote + (local - (0 if base_local is _ABSENT else base_local))
        if name == 'importance':
            value = min(10, max(1, value))
        elif name in ('userConfirmCount', 'userFactReinforceCount'):
            value = max(0, value)
        return value if abs(value) <= MAX_INTEGER else remote
    if name in LATEST and ((_number(local) and _number(remote)) or (isinstance(local, str) and isinstance(remote, str))):
        return max(local, remote)
    if name == 'status':
        return local if STATUS_RANK.get(local, 1) > STATUS_RANK.get(remote, 1) else remote
    return local if local_wins else remote


def _merge_entry(base_local, local, base_remote, remote):
    """Both sides hold the entry and at least one field differs on each."""
    has_base = base_local is not None or base_remote is not None
    old_local = {**(base_local['metadata'] if base_local else {}), **({'text': base_local['text']} if base_local else {})}
    old_remote = {**(base_remote['metadata'] if base_remote else {}), **({'text': base_remote['text']} if base_remote else {})}
    mine, theirs = {**local['metadata'], 'text': local['text']}, {**remote['metadata'], 'text': remote['text']}

    def resolve(name, local_wins):
        ours, yours = mine.get(name, _ABSENT), theirs.get(name, _ABSENT)
        before_ours, before_yours = old_local.get(name, _ABSENT), old_remote.get(name, _ABSENT)
        if has_base and ours == before_ours:
            return yours
        if has_base and yours == before_yours:
            return ours
        if ours == yours and (name not in COUNTERS or not has_base):
            # Two equal counters reached from one base are still two separate increments.
            return yours
        return _merge_field(name, before_ours, ours, before_yours, yours, local_wins, has_base)

    # The lifecycle stage is settled first; a wording both sides rewrote then
    # follows the side that reached that stage, or the published side when both did.
    stage = {name: resolve(name, False) for name in ('status', 'archived')}
    reached = stage
    local_wins = (all(mine.get(name, _ABSENT) == reached[name] for name in reached)
                  and any(theirs.get(name, _ABSENT) != reached[name] for name in reached))
    merged = {}
    for name in dict.fromkeys([*theirs, *mine]):
        value = stage[name] if name in stage else resolve(name, local_wins)
        if value is not _ABSENT:
            merged[name] = deepcopy(value)
    text = merged.pop('text')
    return {'kind': remote['kind'], 'subject': remote['subject'], 'id': remote['id'], 'text': text,
            'metadata': {name: merged[name] for name in sorted(merged)}}


def merge_retirement(previous, incoming):
    """Join two records of one retired fact; a correction neither clock covers reopens the cutoff."""
    old_proof = bool(previous.get('retiredBySourceMessageIds') or previous.get('retiredThroughAtFromCorrection'))
    new_proof = bool(incoming.get('retiredBySourceMessageIds') or incoming.get('retiredThroughAtFromCorrection'))
    times = [item['retiredThroughAt'] for item, proof in [(previous, old_proof), (incoming, new_proof)]
             if 'retiredThroughAt' in item and (proof or not (old_proof or new_proof))]
    row = {**previous, **incoming,
           'sourceMessageIds': list(dict.fromkeys(previous['sourceMessageIds'] + incoming['sourceMessageIds']))}
    corrections = list(dict.fromkeys(previous.get('retiredBySourceMessageIds', []) + incoming.get('retiredBySourceMessageIds', [])))
    if corrections:
        row['retiredBySourceMessageIds'] = corrections
    covered = {identity for item in (previous, incoming) if 'retiredThroughAt' in item
               for identity in item.get('retiredBySourceMessageIds', [])}
    if times and not set(corrections) - covered:
        row['retiredThroughAt'] = max(times)
        if old_proof or new_proof:
            row['retiredThroughAtFromCorrection'] = True
    else:
        # A correction whose time is still unknown may be later than the known ones.
        row.pop('retiredThroughAt', None)
        row.pop('retiredThroughAtFromCorrection', None)
    return row


def _merge_records(base, local, remote, key, combine):
    """Keyed three-way merge keeping the published order, then what only this device adds."""
    before = {row[key]: row for row in base}
    mine = {row[key]: row for row in local}
    result, seen = [], set()
    for row in remote:
        identity = row[key]
        seen.add(identity)
        ours, old = mine.get(identity), before.get(identity)
        if ours is None:
            if old is None or row != old:
                result.append(deepcopy(row))
            # Removed here and untouched there stays removed.
        elif ours == old or ours == row:
            result.append(deepcopy(row))
        elif row == old:
            result.append(deepcopy(ours))
        else:
            result.append(combine(ours, row))
    for row in local:
        identity = row[key]
        if identity not in seen and (identity not in before or row != before[identity]):
            result.append(deepcopy(row))
    return result


def merge_progress(base, local, remote):
    """The published order is the order every device shares, so the oldest entries drop alike everywhere."""
    result = {}
    for name in ('a', 'b'):
        published = list(remote.get(name, []))
        known = set(published) | set(base.get(name, []))
        merged = published + [item for item in local.get(name, []) if item not in known]
        result[name] = merged[-PROGRESS_MAX:]
    return result


def merge_organizer(base, local, remote):
    """Whoever the server already names stays unless this device outranks it."""
    if local == base:
        return deepcopy(remote)
    if remote == base:
        return deepcopy(local)
    if local is not None and local['rank'] > (remote['rank'] if remote else 0):
        return deepcopy(local)
    return deepcopy(remote)


def merge_documents(base, local, remote, completed_at):
    """Merge this device's document with a newer published one.

    base is None, or {'document': the published document both started from,
    'nativeEntries': those entries as this device exports them}. A base on
    another lineage was never read by the remote writer, so it proves nothing
    about deletions and is ignored.
    """
    narrow = remote['schemaVersion'] == 1
    remote = upgraded(remote)
    if base is not None and (base['document'].get('lineage', 0) != remote.get('lineage', 0)):
        base = None
    published = upgraded(base['document']) if base else None
    before_remote = {_key(row): row for row in published['entries']} if base else {}
    before_local = {_key(row): row for row in upgraded_entries(base['nativeEntries'])} if base else {}
    mine = {_key(row): row for row in local['entries']}
    theirs = {_key(row): row for row in remote['entries']}
    retired = {(row['subject'], row['id']) for row in remote['evidence']['retirements']}
    entries = {}
    for key in mine.keys() | theirs.keys() | before_local.keys() | before_remote.keys():
        ours, yours = mine.get(key), theirs.get(key)
        old_ours, old_yours = before_local.get(key), before_remote.get(key)
        if (narrow and yours is None and ours is not None and key[0] == 'reflection'
                and ours['metadata'].get('status') not in ('confirmed', 'promoted')):
            # Schema 1 never carried these, so their absence is not a deletion.
            chosen = ours
        elif ours == old_ours or ours == yours:
            chosen = yours
        elif yours == old_yours:
            chosen = ours
            if yours is None and old_ours is None and key[0] == 'fact' and key[1:] in retired:
                # Deleted elsewhere before this device ever shared a base with the deleter.
                chosen = None
        elif ours is None:
            chosen = yours
        elif yours is None:
            chosen = ours if _user_decided(ours, old_ours) else None
        else:
            chosen = _merge_entry(old_ours, ours, old_yours, yours)
        if chosen is not None:
            entries[key] = deepcopy(chosen)
    old = published or {'evidence': empty_evidence(), 'progress': {'a': [], 'b': []}}
    result = {
        'schemaVersion': SCHEMA_VERSION, 'completedAt': completed_at,
        'entries': [entries[key] for key in sorted(entries)],
        'evidence': {
            'retirements': _merge_records(old['evidence']['retirements'], local['evidence']['retirements'],
                                          remote['evidence']['retirements'], 'id', merge_retirement),
            'confirmations': _merge_records(old['evidence']['confirmations'], local['evidence']['confirmations'],
                                            remote['evidence']['confirmations'], 'fingerprint', lambda ours, yours: deepcopy(yours)),
        },
        'progress': merge_progress(old['progress'], local['progress'], remote['progress']),
    }
    organizer = merge_organizer(old.get('organizer'), local.get('organizer'), remote.get('organizer'))
    if organizer is not None:
        result['organizer'] = organizer
    maintenance = {name: max(local.get('maintenance', {}).get(name, 0), remote.get('maintenance', {}).get(name, 0))
                   for name in sorted({*local.get('maintenance', {}), *remote.get('maintenance', {})})}
    if maintenance:
        result['maintenance'] = maintenance
    lineage = max(local.get('lineage', 0), remote.get('lineage', 0))
    if lineage:
        result['lineage'] = lineage
    return result


def same_content(left, right):
    """Whether two documents differ only in when they were completed."""
    def body(document):
        current = upgraded(document)
        return {**{name: value for name, value in current.items() if name not in ('completedAt', 'entries')},
                'entries': sorted(current['entries'], key=_key)}
    return body(left) == body(right)
