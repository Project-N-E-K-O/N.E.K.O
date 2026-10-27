# Copyright 2025-2026 Project N.E.K.O. Team
"""Bounded audio checks shared by recommendation and playback."""

import asyncio
from collections import OrderedDict
import time
from urllib.parse import urljoin, urlparse

import httpx

# ==================================================
# Shared domain pool for whitelisting and crawlers.
# Manage domains for every music source centrally here.
# ==================================================
MUSIC_SOURCE_DOMAINS = {
    # NetEase Cloud Music.
    'music.163.com',
    'p1.music.126.net', 'p2.music.126.net', 'p3.music.126.net',
    'm7.music.126.net', 'm8.music.126.net', 'm9.music.126.net',
    'music.126.net',
    # SoundCloud
    'soundcloud.com', 'api.soundcloud.com', 'sndcdn.com',
    'playback.media-streaming.soundcloud.cloud',
    # iTunes/Apple Music
    'itunes.apple.com', 'audio-ssl.itunes.apple.com',
    'a.scdn.co', 'i.scdn.co', 'p.scdn.co',
    # QQ Music.
    'y.qq.com', 'u.y.qq.com', 'dl.stream.qqmusic.com', 'dl.stream.qqmusic.qq.com',
    'isure.stream.qqmusic.qq.com',
    # Kugou / others.
    'kugou.com', 'stream.kugou.com',
    # FMA (Free Music Archive)
    'freemusicarchive.org', 'freemusicarchive.imgix.net',
    # Musopen
    'musopen.org', 'imslp.org',
    # Bandcamp
    'bandcamp.com', 'bcbits.com',
    # General images.
    'i.imgur.com',
    # Bilibili, for some music.
    'hdslb.com', 'bilivideo.com',
    'gg.spriteapp.cn', 'mmusic.spriteapp.cn',
}


MAX_AUDIO_BYTES = 50 * 1024 * 1024
_availability = OrderedDict()
_probe_slots = asyncio.Semaphore(3)
_unavailable_hosts = OrderedDict()


def mark_audio_unavailable(url, *, connection_failure=False, candidate_url=None):
    """Let real playback failures override a recent successful preflight."""
    if not is_allowed_audio_url(url):
        return
    for key in list(_availability):
        if key[0] == url:
            _availability[key] = (time.monotonic() + 60, False)
    # Record failures even when a concurrent preflight has not cached anything yet.
    _availability[(url, None)] = (time.monotonic() + 60, False)
    _availability.move_to_end((url, None))
    if candidate_url and is_netease_candidate_url(candidate_url):
        _availability[(candidate_url, None)] = (time.monotonic() + 60, False)
        _availability.move_to_end((candidate_url, None))
    while len(_availability) > 256:
        _availability.popitem(last=False)
    if connection_failure:
        host = urlparse(url).hostname
        _unavailable_hosts[host] = time.monotonic() + 300
        _unavailable_hosts.move_to_end(host)
        while len(_unavailable_hosts) > 128:
            _unavailable_hosts.popitem(last=False)


def _playback_failed_recently(url):
    now = time.monotonic()
    failed = _availability.get((url, None))
    return _unavailable_hosts.get(urlparse(url).hostname, 0) > now or bool(
        failed and failed[0] > now and not failed[1]
    )


def is_netease_candidate_url(url):
    prefix = '/api/music/play/netease/'
    song_id = url.removeprefix(prefix)
    return url.startswith(prefix) and 0 < len(song_id) <= 20 and song_id.isascii() and song_id.isdecimal()


def is_allowed_audio_url(url):
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or '').lower()
        return parsed.scheme == 'https' and parsed.port in (None, 443) and not parsed.username and not parsed.password and any(
            host == domain or host.endswith('.' + domain) for domain in MUSIC_SOURCE_DOMAINS
        )
    except ValueError:
        return False


def is_audio_content_type(value):
    value = (value or '').split(';', 1)[0].strip().lower()
    return value.startswith(('audio/', 'video/')) or value in {'application/octet-stream', 'binary/octet-stream'}


def has_audio_header(data):
    return (
        data.startswith((b'ID3', b'fLaC', b'OggS', b'\x1a\x45\xdf\xa3'))
        or data.startswith(b'RIFF') and data[8:12] == b'WAVE'
        or len(data) >= 2 and data[0] == 0xff and data[1] & 0xe0 == 0xe0
        or len(data) >= 8 and data[4:8] == b'ftyp'
    )


async def send_audio_request(client, request):
    """Retry one failed GET connection before a response has been committed."""
    try:
        return await client.send(request, stream=True)
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.RemoteProtocolError):
        await asyncio.sleep(0.15)
        return await client.send(request, stream=True)


async def filter_playable_candidates(tracks, *, deferred_urls=None):
    async def playable(track):
        url = str(track.get('url') or '')
        prefix = '/api/music/play/netease/'
        if url.startswith(prefix):
            # This trusted candidate path already passed NetEase's batch authorization.
            if not is_netease_candidate_url(url) or _playback_failed_recently(url):
                return False
            audio_url = track.get('_audio_url')
            if audio_url:
                audio_url = str(audio_url)
                if audio_url.startswith('http://'):
                    audio_url = 'https://' + audio_url[7:]
                checked = await probe_audio_url(audio_url, referer='https://music.163.com/')
                return checked if not _playback_failed_recently(url) else False
            return True
        return await probe_audio_url(url)
    tracks = [track for track in tracks[:20] if isinstance(track, dict)]
    checks = await asyncio.gather(*(playable(track) for track in tracks))
    if deferred_urls is not None:
        deferred_urls.update(track.get('url') for track, ok in zip(tracks, checks) if ok is None)
    return [track for track, ok in zip(tracks, checks) if ok]


async def probe_audio_url(url, *, referer=None):
    """Require a permitted redirect chain, audio headers and real audio bytes."""
    if not is_allowed_audio_url(url):
        return False
    if _playback_failed_recently(url):
        return False
    cached = _availability.get((url, referer))
    if cached and cached[0] > time.monotonic():
        return cached[1]
    playable = False
    acquired = False
    try:
        async with asyncio.timeout(8):
            async with _probe_slots:
                acquired = True
                client = await asyncio.to_thread(httpx.AsyncClient, timeout=httpx.Timeout(4, connect=2), follow_redirects=False)
                async with client:
                    current = url
                    headers = {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/122 Safari/537.36',
                        'Referer': referer or f'https://{urlparse(url).hostname}/',
                        'Accept': '*/*', 'Accept-Encoding': 'identity', 'Range': 'bytes=0-1023',
                    }
                    for _ in range(10):
                        if not is_allowed_audio_url(current):
                            break
                        response = await send_audio_request(client, client.build_request('GET', current, headers=headers))
                        try:
                            if response.status_code in (301, 302, 303, 307, 308):
                                location = response.headers.get('location')
                                if not location:
                                    break
                                current = urljoin(current, location)
                                continue
                            if response.status_code not in (200, 206) or not is_audio_content_type(response.headers.get('content-type')):
                                break
                            sizes = [response.headers.get('content-range', '').rsplit('/', 1)[-1], response.headers.get('content-length', '')]
                            if any(size.isdecimal() and int(size) > MAX_AUDIO_BYTES for size in sizes):
                                break
                            sample = await anext(response.aiter_bytes(chunk_size=256), b'')
                            playable = len(sample) >= 12 and has_audio_header(sample)
                            break
                        finally:
                            await response.aclose()
    except (httpx.HTTPError, TimeoutError, ValueError):
        if not acquired:
            # Queue pressure says nothing about this song's availability.
            return None
        playable = False
    # A real playback failure while this request was awaiting wins over its late result.
    if _playback_failed_recently(url):
        return False
    # Failed checks are remembered briefly, avoiding repeated calls to a broken source.
    _availability[(url, referer)] = (time.monotonic() + (300 if playable else 60), playable)
    _availability.move_to_end((url, referer))
    while len(_availability) > 256:
        _availability.popitem(last=False)
    return playable
