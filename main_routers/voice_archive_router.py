"""Read-only replay of locally archived speech, matched by immutable identity."""
from contextlib import contextmanager
import hashlib
import io
import json
import re
from pathlib import Path
import sqlite3
import wave

from pydantic import BaseModel, StrictBool

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from utils.config_manager import get_config_manager


async def local_access(request: Request):
    from main_routers.card_drop_router import _local_request_source_allowed
    from main_routers.local_access import is_local_oauth_status_request
    if not is_local_oauth_status_request(request) or not _local_request_source_allowed(request):
        raise HTTPException(403, 'local_access_required')


router = APIRouter(prefix='/api/voice-archive', dependencies=[Depends(local_access)])
HEADERS = {'Cache-Control': 'no-store'}
MAX_CLIP_BYTES = 64 * 1024 * 1024


@contextmanager
def database():
    path = Path(get_config_manager().app_docs_dir) / 'voice_archive' / 'archive.sqlite3'
    if not path.is_file():
        raise HTTPException(404, 'voice_unavailable', headers=HEADERS)
    db = None
    try:
        db = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=.1)
        db.row_factory = sqlite3.Row
        yield db
    except sqlite3.Error:
        raise HTTPException(503, 'voice_unavailable', headers=HEADERS) from None
    finally:
        if db is not None:
            db.close()


class RecordingSettings(BaseModel):
    enabled: StrictBool


@router.get('/settings')
def recording_settings():
    from utils.voice_archive_settings import load_settings
    try:
        data = load_settings(get_config_manager())
    except (OSError, ValueError):
        raise HTTPException(503, 'voice_settings_unavailable', headers=HEADERS) from None
    return Response(json.dumps(data), media_type='application/json', headers=HEADERS)


@router.post('/settings')
def update_recording_settings(settings: RecordingSettings):
    from utils.voice_archive_settings import save_settings
    try:
        data = save_settings(get_config_manager(), settings.enabled)
    except OSError:
        raise HTTPException(503, 'voice_settings_unavailable', headers=HEADERS) from None
    return Response(json.dumps(data), media_type='application/json', headers=HEADERS)


@router.get('/message')
def message_audio(character: str = Query(min_length=1, max_length=200),
                  record_id: str = Query(default='', max_length=200),
                  speech_id: str = Query(default='', max_length=200)):
    if not record_id and not speech_id:
        raise HTTPException(404, 'voice_unavailable', headers=HEADERS)
    with database() as db:
        db.execute('BEGIN')
        # Prefer durable history identity; live messages carry the server turn/speech ID.
        clips = list(db.execute('SELECT id,stream_complete,archive_error FROM clips '
                               'WHERE character=? AND record_id=? ORDER BY created_at,id',
                               (character, record_id))) if record_id else []
        if not clips and speech_id:
            clips = list(db.execute('SELECT id,stream_complete,archive_error FROM clips '
                                   'WHERE character=? AND speech_id=? ORDER BY created_at,id',
                                   (character, speech_id)))
        playlist = []
        for clip in clips:
            if not re.fullmatch(r'[a-f0-9]{64}', clip['id']):
                raise HTTPException(409, 'voice_archive_damaged', headers=HEADERS)
            formats = db.execute('SELECT seq,format,length(data) AS size FROM chunks WHERE clip_id=? ORDER BY seq',
                                 (clip['id'],)).fetchall()
            # Keep format switches separate, just as the archive exporter does.
            groups = []
            for part in formats:
                if not groups or groups[-1]['format'] != part['format']:
                    groups.append({'start': part['seq'], 'format': part['format']})
            for group in groups:
                playlist.append({'url': f"/api/voice-archive/clips/{clip['id']}/{group['start']}",
                                 'complete': bool(clip['stream_complete'] and not clip['archive_error'])})
    return Response(json.dumps({'clips': playlist}), media_type='application/json', headers=HEADERS)


@router.get('/clips/{clip_id}/{start}')
def clip_audio(clip_id: str, start: int):
    with database() as db:
        db.execute('BEGIN')
        first = db.execute('SELECT format FROM chunks WHERE clip_id=? AND seq=?', (clip_id, start)).fetchone()
        if first is None or start < 0:
            raise HTTPException(404, 'voice_unavailable', headers=HEADERS)
        boundary = db.execute('SELECT min(seq) FROM chunks WHERE clip_id=? AND seq>? AND format<>?',
                              (clip_id, start, first['format'])).fetchone()[0]
        where = 'clip_id=? AND seq>=?' + (' AND seq<?' if boundary is not None else '')
        args = (clip_id, start, boundary) if boundary is not None else (clip_id, start)
        size = db.execute('SELECT sum(length(data)) FROM chunks WHERE ' + where, args).fetchone()[0]
        if size > MAX_CLIP_BYTES:
            raise HTTPException(413, 'voice_too_large', headers=HEADERS)
        parts = db.execute('SELECT format,data,sha256 FROM chunks WHERE ' + where + ' ORDER BY seq', args).fetchall()
    if not parts or start < 0:
        raise HTTPException(404, 'voice_unavailable', headers=HEADERS)
    fmt = parts[0]['format']
    audio = bytearray()
    for part in parts:
        if part['format'] != fmt:
            break
        if hashlib.sha256(part['data']).hexdigest() != part['sha256']:
            raise HTTPException(409, 'voice_archive_damaged', headers=HEADERS)
        audio.extend(part['data'])
    if fmt == 'ogg':
        return Response(bytes(audio), media_type='audio/ogg', headers=HEADERS)
    if fmt != 'pcm_s16le_48000':
        raise HTTPException(415, 'voice_format_unsupported', headers=HEADERS)
    output = io.BytesIO()
    with wave.open(output, 'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(48000)
        wav.writeframes(audio)
    return Response(output.getvalue(), media_type='audio/wav', headers=HEADERS)
