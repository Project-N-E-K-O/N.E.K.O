"""Batch exact-identity joins to the optional local speech archive; no audio reads."""
from contextlib import closing
from pathlib import Path
import sqlite3


def attach_voice(page, app_docs_dir, character):
    entries = page['messages']
    ids = list({entry['recordId'] for entry in entries if entry.get('recordId')})
    path = Path(app_docs_dir) / 'voice_archive' / 'archive.sqlite3'
    page['voiceArchiveAvailable'] = path.is_file()
    from utils.voice_archive_purge import has_pending_character_voice_archive_purge
    if has_pending_character_voice_archive_purge(app_docs_dir, character):
        # A same-name replacement must not inherit the deleted identity's
        # recordings while its durable purge intent is being retried.
        page['voiceArchiveAvailable'] = False
        page['voiceUnavailable'] = True
        return page
    if not ids or not page['voiceArchiveAvailable']:
        return page
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.1)) as db:
            placeholders = ','.join('?' for _ in ids)
            voiced = {row[0] for row in db.execute(
                f'SELECT DISTINCT record_id FROM clips WHERE character=? AND record_id IN ({placeholders}) '
                'AND EXISTS (SELECT 1 FROM chunks WHERE clip_id=clips.id)', [character, *ids])}
        for entry in entries:
            if entry.get('recordId') in voiced:
                entry['voice'] = {'recordId': entry['recordId']}
    except sqlite3.Error:
        # A missing/unavailable optional voice archive never hides text history.
        page['voiceUnavailable'] = True
    return page
