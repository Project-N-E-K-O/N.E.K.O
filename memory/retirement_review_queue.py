"""Extracted facts waiting for a retired-assertion review that did not fit one extraction.

An extraction reviews a bounded number of (candidate, retired assertion) pairs.
A candidate past that budget is kept here with the provenance its messages
proved, and the next extraction of the same kind reviews it first. Nothing in
this file travels in a snapshot: until the fact is saved, the messages it came
from are not recorded as read either, so no device skips them.
"""

import json
import logging
from pathlib import Path
import re
import shutil
from uuid import uuid4

from utils.file_utils import atomic_write_json

FILE = 'retired_assertion_review_queue.json'
QUEUE_ID = '_retirement_review_queue_id'
logger = logging.getLogger(__name__)


def key(default_source, extraction_path, progress_pass):
    """Candidates come back only to an extraction that would have written them the same way."""
    return [default_source, extraction_path, progress_pass]


def _load(path):
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
    except FileNotFoundError:
        return [], True
    except ValueError:
        return [], False
    entries = value.get('entries') if isinstance(value, dict) else None
    if not isinstance(entries, list):
        return [], False
    return [row for row in entries if isinstance(row, dict) and isinstance(row.get('id'), str)
            and isinstance(row.get('key'), list) and isinstance(row.get('candidate'), dict)], True


def read(directory):
    return _load(Path(directory) / FILE)[0]


def candidates(entries, queue_key):
    """Copies of the waiting candidates for this kind of extraction, oldest first."""
    return [{**json.loads(json.dumps(row['candidate'])), QUEUE_ID: row['id']}
            for row in entries if row['key'] == queue_key]


def _shared_identity(identity):
    match = re.fullmatch(r'sync:([0-9a-f]{32})', identity) if isinstance(identity, str) else None
    return match.group(1) if match else None


def commit(directory, queue_key, brought_ids, held_back, added, proof_key):
    """Drop what this round settled, keep what still waits and add the new waiters.

    Returns (identities still waiting, shared identities of settled candidates).
    The caller holds the lock that guards the facts.
    """
    path = Path(directory) / FILE
    entries, readable = _load(path)
    still = {candidate.get(QUEUE_ID) for candidate in held_back if candidate.get(QUEUE_ID)}
    settled = [row for row in entries if row['id'] in brought_ids and row['id'] not in still]
    gone = {row['id'] for row in settled}
    kept = [row for row in entries if row['id'] not in gone]
    kept += [{'id': uuid4().hex, 'key': list(queue_key), 'candidate': candidate} for candidate in added]
    if gone or added:
        if not readable and path.exists():
            # Never overwrite what could not be read; keep it for a person to look at.
            shutil.copyfile(path, path.with_name(path.name + '.unreadable'))
            logger.warning('[RetirementReviewQueue] 待审队列文件无法读取，已另存后重建')
        if kept:
            atomic_write_json(path, {'version': 1, 'entries': kept}, ensure_ascii=False, indent=2)
        else:
            path.unlink(missing_ok=True)
    waiting = {identity for row in kept for identity in row['candidate'].get(proof_key, {}).get('source_message_ids', [])}
    # One message can yield several facts: it is read only once none of them still waits.
    done = [identity for row in settled
            for identity in map(_shared_identity, row['candidate'].get(proof_key, {}).get('source_message_ids', []))
            if identity and f'sync:{identity}' not in waiting]
    return waiting, list(dict.fromkeys(done))
