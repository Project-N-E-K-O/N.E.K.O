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
    for row in [*archive, *active]:
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
                evidence = 'extracted_from_same_messages'
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
            pairs.append({'candidate': newer['id'], 'existing': older['id'], 'evidence': evidence})
    return pairs


def pair_key(pair):
    return f"{pair['candidate']}|{pair['existing']}"


def losers(pairs, decisions):
    """Which fact each merge or replace folds away, and into which; the earlier decision wins a conflict."""
    result = {}
    for pair in pairs:
        action = decisions.get(pair_key(pair))
        if action == 'merge':
            loser, survivor = pair['candidate'], pair['existing']
        elif action == 'replace':
            loser, survivor = pair['existing'], pair['candidate']
        else:
            continue
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
