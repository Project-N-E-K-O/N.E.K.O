"""One-time cleanup of duplicate facts that earlier versions left behind.

Two kinds are nominated: facts extracted from the same chat lines (on two
devices, or twice on one), and facts that read alike, including ones a
reflection already absorbed (everyday dedup skips those). Nomination only
proposes a pair; the existing arbitration decides with the model whether the
two say the same thing, and a pair it calls different stays as it is. Sharing
a source never merges anything by itself. A merged-away fact is archived with
``arbitration_archived_at`` and ``superseded_by``, never deleted, and the whole
character directory is copied aside first.

The run is recorded twice: in ``maintenance.legacyDedup`` of the learned-memory
snapshot, so no other device repeats it, and in a file on this device, which
also serves a character that does not sync yet.
"""

import json
import os
from pathlib import Path
import re
import shutil
import sqlite3

VERSION = 1
MAINTENANCE_KEY = 'legacyDedup'
# Everyday dedup nominates from 0.85; the one-time pass reaches lower and lets
# the model turn the extra pairs down.
NOMINATE_COSINE = 0.80
MARKER = 'legacy_dedup.json'
# arbitration_reason on what this cleanup archives.
ARCHIVE_REASON = 'legacy_dedup'
BACKUP_DIR = 'backups'
BACKUP_PREFIX = 'legacy-dedup-'
# Fact id lists that say which facts a reflection or persona entry rests on.
# ``covered_fact_ids`` is an audit trail of skipped batches and stays as it was.
EVIDENCE_ID_FIELDS = ('source_fact_ids', 'counted_fact_ids')


def _normalized(text):
    return re.sub(r'[\s\W_]+', '', str(text or '')).casefold()


def _created(row):
    return str(row.get('created_at') or '')


def candidates(active, archive):
    """The private-chat facts the cleanup looks at, by id: live ones, and archived
    ones only when age moved them there after a reflection absorbed them."""
    from memory.scopes import is_legacy_private_entry
    rows = {}
    # An archived fact counts only when absorption put it there; one archived for
    # another reason (capacity, an import) is not the cleanup's to fold.
    for row in [*(row for row in archive if isinstance(row, dict) and row.get('absorbed')), *active]:
        if (isinstance(row, dict) and isinstance(row.get('id'), str) and row['id'] and isinstance(row.get('text'), str)
                and not row.get('arbitration_archived_at') and is_legacy_private_entry(row)):
            rows[row['id']] = row
    return rows


def nominate(rows, *, decode=None):
    """Pairs for the model to judge, newer fact as ``candidate`` as the prompt describes it.

    ``rows`` comes from ``candidates``. ``decode`` turns a row into its embedding
    vector (or None); without it only shared sources and identical wording count.
    """
    rows = list(rows.values()) if isinstance(rows, dict) else list(rows)
    vectors = {id(row): decode(row) if decode else None for row in rows}
    pairs = []
    for index, first in enumerate(rows):
        for second in rows[index + 1:]:
            if first['id'] == second['id'] or (first.get('entity') or 'master') != (second.get('entity') or 'master'):
                continue
            evidence = None
            if set(first.get('source_message_ids') or ()) & set(second.get('source_message_ids') or ()):
                # Same chat lines: always the model's call, whatever times were derived.
                evidence = 'extracted_from_same_messages'
            elif _distinct_days(first, second):
                # The same activity on different days (daily imports keep these apart on
                # purpose) is two events; the model would only see the identical words.
                continue
            elif _normalized(first['text']) == _normalized(second['text']):
                evidence = 'text_overlap=1.000'
            elif (vectors[id(first)] is not None and vectors[id(second)] is not None
                  and first.get('embedding_model_id') == second.get('embedding_model_id')):
                score = _cosine(vectors[id(first)], vectors[id(second)])
                if score is not None and score >= NOMINATE_COSINE:
                    evidence = f'cosine={score:.3f}'
            if evidence is None:
                continue
            older, newer = (first, second) if _created(first) <= _created(second) else (second, first)
            pairs.append({'candidate': newer['id'], 'existing': older['id'], 'evidence': evidence,
                          'fingerprints': {newer['id']: fingerprint(newer), older['id']: fingerprint(older)},
                          # Which side is the user's own words, as nominated: the fold keeps
                          # that side even when a rerun no longer sees the other one live.
                          'sources': {newer['id']: newer.get('source'), older['id']: older.get('source')}})
    return pairs


def fingerprint(row):
    """What the model was shown of a fact; a decision holds only while it is unchanged."""
    import hashlib
    # The source counts too: a shared-source pair whose sources changed was judged on evidence that is gone.
    # Its event window too, as instants: a later recheck moving it may make it a
    # separate event, while an import that only rewrites the same time is no change.
    def instant(value):
        if not value:
            return None
        try:
            from memory.long_term_sync import timestamp
            return timestamp(value)
        except (TypeError, ValueError, OverflowError, OSError, ImportError):
            return str(value)
    return hashlib.sha256(json.dumps([row.get('entity') or 'master', row.get('text'), row.get('source'),
                                      sorted(str(item) for item in row.get('source_message_ids') or ()),
                                      instant(row.get('event_start_at')), instant(row.get('event_end_at')),
                                      # What decides whether that window is explicit (see _has_distinct_event_windows).
                                      bool(row.get('event_when_raw')), instant(row.get('created_at'))],
                                     ensure_ascii=False).encode()).hexdigest()[:16]


def owned(row, ours, stamps):
    """Whether an archived row was archived by this cleanup.

    Its own reason says so until a snapshot import rewrites it; the archive time,
    which this cleanup chooses before archiving and an import keeps, says so after.
    """
    if not isinstance(row, dict) or not row.get('arbitration_archived_at'):
        return False
    if row.get('arbitration_reason') == ARCHIVE_REASON:
        return True
    if row.get('id') not in ours:
        return False
    from memory.long_term_sync import timestamp
    try:
        return timestamp(row['arbitration_archived_at']) in {timestamp(stamp) for stamp in stamps}
    except (TypeError, ValueError, OverflowError, OSError):
        return False


def still_valid(folded, pairs, rows, archive, ours=(), stamps=()):
    """The folds whose two facts are still what the model judged.

    A fold already applied (its fact archived by the cleanup, pointing at the
    same survivor) stays valid; one whose fact was deleted, rewritten or
    archived otherwise since the model answered is dropped, so both stay.
    """
    seen = {}
    for pair in pairs:
        seen.update(pair.get('fingerprints') or {})
    # Only this cleanup's own archiving counts as done by it; everyday dedup may have
    # folded the same fact meanwhile, and that is not this run's to record or restore.
    # ``ours`` and ``stamps`` are what this run recorded before archiving (see ``owned``).
    ours = set(ours)
    applied = {row.get('id'): row.get('superseded_by') for row in archive if owned(row, ours, stamps)}

    final = survivors(folded)

    def unchanged(fact_id):
        row = rows.get(fact_id)
        if row is not None:
            return fingerprint(row) == seen.get(fact_id)
        # Gone from the live set only because this cleanup already folded it: the
        # archive names the survivor it was folded into, or the final one of a chain.
        return fact_id in folded and applied.get(fact_id) in (folded[fact_id], final.get(fact_id))

    def executed(loser):
        # Already archived by this cleanup: it happened, so it stays on record (and
        # restorable) whatever its survivor has become since.
        return rows.get(loser) is None and loser in folded and applied.get(loser) in (folded[loser], final.get(loser))

    # An executed fold keeps the target the archive recorded, not one recomputed from
    # a chain that has since lost a link.
    return {loser: (applied[loser] if executed(loser) else survivor) for loser, survivor in folded.items()
            if executed(loser) or (unchanged(loser) and unchanged(survivor))}


def _distinct_days(first, second):
    """Two separate events: explicit windows on different days, or both fully bounded
    and not overlapping. Start times a few minutes apart (one remark extracted twice)
    are one event."""
    try:
        from memory.fact_dedup import _has_distinct_event_windows
    except ImportError:
        return False
    if not _has_distinct_event_windows(first, second):
        return False

    from datetime import datetime
    from memory.long_term_sync import timestamp

    def instant(value):
        # Compared as instants: two records may carry different UTC offsets.
        try:
            return timestamp(value) if value else None
        except (TypeError, ValueError, OverflowError, OSError):
            return None

    def days(row):
        return {datetime.fromtimestamp(at / 1000).date() for at in
                (instant(row.get(key)) for key in ('event_start_at', 'event_end_at')) if at is not None}
    # Two whole windows decide by overlap, even across midnight; only otherwise the day.
    bounds = [(instant(row.get('event_start_at')), instant(row.get('event_end_at'))) for row in (first, second)]
    if all(start is not None and end is not None for start, end in bounds):
        (first_start, first_end), (second_start, second_end) = bounds
        return first_end < second_start or second_end < first_start
    return days(first) != days(second)


def pair_key(pair):
    return f"{pair['candidate']}|{pair['existing']}"


def losers(pairs, decisions, rows=None):
    """Which fact each merge or replace folds away, and into which; the earlier decision wins a conflict.

    When one side is the user's own words and the other only the character's
    account, the user's stays: folding it away would leave its dependents
    backed by the character alone.
    """
    rows = rows or {}
    result = {}
    for pair in pairs:
        sources = pair.get('sources') or {}

        def source_of(fact_id):
            # An older fact without a source counts as the user's own words, as FactStore reads it.
            value = sources[fact_id] if fact_id in sources else (rows.get(fact_id) or {}).get('source')
            return value or 'user_observation'
        action = decisions.get(pair_key(pair))
        if action == 'merge':
            loser, survivor = pair['candidate'], pair['existing']
        elif action == 'replace':
            loser, survivor = pair['existing'], pair['candidate']
        else:
            continue
        if source_of(loser) == 'user_observation' and source_of(survivor) == 'ai_disclosure':
            loser, survivor = survivor, loser
        if loser in result or survivor in result:
            continue
        result[loser] = survivor
    return result


def _cosine(left, right):
    try:
        if len(left) != len(right) or not len(left):
            return None
        dot = sum(float(a) * float(b) for a, b in zip(left, right))
        norm = (sum(float(a) * float(a) for a in left) ** 0.5) * (sum(float(b) * float(b) for b in right) ** 0.5)
        return dot / norm if norm else None
    except (TypeError, ValueError):
        return None


def survivors(folded):
    """Follow each folded fact to the one that finally stands for it."""
    result = {}
    for loser in folded:
        target, seen = folded[loser], {loser}
        while target in folded and target not in seen:
            # Folded into a fact that was itself folded further in the same run.
            seen.add(target)
            target = folded[target]
        result[loser] = target
    return result


def repoint(entry, mapping):
    """Point an entry's evidence lists at the surviving facts; True when it changed."""
    changed = False
    for field in EVIDENCE_ID_FIELDS:
        ids = entry.get(field)
        if not isinstance(ids, list):
            continue
        moved = list(dict.fromkeys(mapping.get(fid, fid) for fid in ids))
        if moved != ids:
            entry[field] = moved
            changed = True
    return changed


def read_marker(directory):
    try:
        value = json.loads((Path(directory) / MARKER).read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def published_version(directory):
    """The cleanup version the learned-memory snapshot records for this character."""
    try:
        state = json.loads((Path(directory) / 'long_term_portable.json').read_text(encoding='utf-8'))
    except (FileNotFoundError, ValueError):
        return 0
    maintenance = state.get('maintenance') if isinstance(state, dict) else None
    value = maintenance.get(MAINTENANCE_KEY) if isinstance(maintenance, dict) else None
    return value if type(value) is int else 0


def local_version(directory):
    marker = read_marker(directory)
    value = marker.get('version') if marker and marker.get('stage') == 'done' else None
    return value if type(value) is int else 0


def backup(directory, stamp):
    """Copy the whole character directory aside, earlier backups excepted. Returns the copy's path."""
    source = Path(directory)
    target = source / BACKUP_DIR / f'{BACKUP_PREFIX}{stamp}'
    if target.exists():
        return target
    partial = target.with_name(target.name + '.partial')
    shutil.rmtree(partial, ignore_errors=True)
    databases = []

    def ignore(folder, names):
        skipped = {name for name in names if Path(folder) == source and name == BACKUP_DIR}
        for name in names:
            if name.endswith(('.db-wal', '.db-shm', '-journal')):
                skipped.add(name)
            elif name.endswith('.db') and os.path.isfile(os.path.join(folder, name)):
                skipped.add(name)
                databases.append(Path(folder) / name)
        return skipped

    shutil.copytree(source, partial, ignore=ignore)
    for database in databases:
        # A live database is copied through SQLite, never as torn bytes.
        copy = partial / database.relative_to(source)
        copy.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(f'file:{database}?mode=ro', uri=True) as reader, sqlite3.connect(copy) as writer:
            reader.backup(writer)
        writer.close()
        reader.close()
    os.replace(partial, target)
    return target
