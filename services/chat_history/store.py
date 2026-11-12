"""Bounded cursor reads; never creates, migrates or rewrites a memory database."""
from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path


class HistoryUnavailable(Exception):
    pass


def message(row):
    try:
        item = json.loads(row[2])
        data = item['data']
        meta = data.get('metadata') or {}
        if not isinstance(meta, dict):
            return None
        if any(meta.get(k) for k in ('subject', 'subjects', 'memory_subject', 'memory_subjects', 'subject_kind', 'subject_id')):
            return None
        if meta.get('scope') not in (None, '', 'legacy_private', 'private-chat'):
            return None
        if meta.get('source') in ('avatar-drop', 'theater_numeric_v2') or 'avatar-drop' in (meta.get('sources') or []):
            return None
        proof = meta.get('chat_original_source') or {}
        if not isinstance(proof, dict) or proof.get('origin') == 'system':
            return None
        role = {'human': 'user', 'ai': 'assistant'}.get(item.get('type'))
        content = data.get('content', '')
        if isinstance(content, list):
            content = '\n'.join(p['text'] for p in content if isinstance(p, dict)
                                and p.get('type') == 'text' and isinstance(p.get('text'), str))
        if not role or not isinstance(content, str) or not content.strip():
            return None
        content = re.sub(r'^\[\d{8} [^\]\s]+ \d{2}:\d{2}\] ?', '', content)
        entry = dict(id=row[0], session=row[1], text=content, role=role, time=str(row[3] or ''))
        record_id = proof.get('recordId')
        if role == 'assistant' and isinstance(record_id, str) and 0 < len(record_id) <= 200:
            entry['recordId'] = record_id
        return entry
    except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return None


def browse(root, character, *, day=None, query='', role='all', cursor=0,
           limit=30, order='desc', anchor=None, chronological=False, after_time=None):
    root = Path(root).resolve()
    if not character or character in ('.', '..') or any(c in character for c in '/\\\0'):
        raise ValueError('invalid_character')
    directory = root / character
    path = directory / 'time_indexed.db'
    if directory.is_symlink() or path.is_symlink() or path.resolve().parent != directory or directory.parent != root:
        raise ValueError('invalid_character')
    if not path.exists():
        return dict(messages=[], nextCursor=None, scanned=0, available=False)
    deadline = time.monotonic() + 2
    try:
        with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True, timeout=.1)) as db:
            db.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
            # Snapshot protects anchor and page from concurrent archive deletion.
            db.execute('BEGIN')
            if anchor is not None:
                target = db.execute('SELECT id,session_id,substr(message,1,262144),timestamp FROM time_indexed_original WHERE id=?', (anchor,)).fetchone()
                if not target or not message(target):
                    raise LookupError('message_not_found')
                rows = db.execute('SELECT id,session_id,substr(message,1,262144),timestamp FROM time_indexed_original WHERE session_id=? AND id<=? ORDER BY id DESC LIMIT 11', (target[1], anchor)).fetchall()[::-1]
                rows += db.execute('SELECT id,session_id,substr(message,1,262144),timestamp FROM time_indexed_original WHERE session_id=? AND id>? ORDER BY id LIMIT 10', (target[1], anchor)).fetchall()
                return dict(messages=[m for r in rows if (m := message(r))], nextCursor=None, scanned=len(rows), available=True)
            where, args = [], []
            if day:
                # Archive timestamps are local ISO dates. Keep the column bare
                # so SQLite can narrow old/empty days with its timestamp index.
                selected = date.fromisoformat(day)
                # A lexical upper bound also keeps date.max queryable.
                end = (selected + timedelta(days=1)).isoformat() if selected < date.max else '9999-12-32'
                where.append('timestamp>=? AND timestamp<?')
                args.extend((day, end))
            if chronological:
                where.append("timestamp IS NOT NULL AND timestamp<>''")
                if cursor and after_time is not None:
                    where.append('(timestamp,id)>(?,?)')
                    args.extend((after_time, cursor))
            elif cursor:
                where.append('id' + ('<' if order == 'desc' else '>') + '?')
                args.append(cursor)
            sql = 'SELECT id,session_id,substr(message,1,262144),timestamp FROM time_indexed_original'
            sql += (' WHERE ' + ' AND '.join(where)) if where else ''
            sql += (' ORDER BY timestamp ASC,id ASC' if chronological else ' ORDER BY id ' + ('DESC' if order == 'desc' else 'ASC')) + ' LIMIT 5001'
            rows = iter(db.execute(sql, args))
            hits, scanned, last, last_time = [], 0, None, None
            def result(more):
                value = dict(messages=hits, nextCursor=last if more else None, scanned=min(scanned, 5000), available=True)
                if chronological:
                    value['nextTime'] = last_time if more else None
                return value
            for row in rows:
                scanned += 1
                if scanned > 5000:
                    return result(True)
                last = row[0]
                last_time = row[3]
                entry = message(row)
                if entry and (role == 'all' or role == entry['role']) and query.casefold() in entry['text'].casefold():
                    hits.append(entry)
                if len(hits) == limit:
                    more = next(rows, None) is not None
                    return result(more)
            return result(False)
    except sqlite3.Error as exc:
        raise HistoryUnavailable('history_unavailable') from exc
