import asyncio
from types import SimpleNamespace

import httpx
import pytest

from utils import music_audio as audio

URL = 'https://freemusicarchive.org/track/chill/stream/'
MP3 = b'ID3' + b'\0' * 600


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    monkeypatch.setattr(audio, '_availability', audio.OrderedDict())
    monkeypatch.setattr(audio, '_probe_slots', asyncio.Semaphore(3))
    monkeypatch.setattr(audio, '_unavailable_hosts', audio.OrderedDict())


def transport(monkeypatch, handler):
    clients = []
    real_client = httpx.AsyncClient
    def factory(**kwargs):
        client = real_client(transport=httpx.MockTransport(handler), **kwargs)
        clients.append(client)
        return client
    monkeypatch.setattr(audio.httpx, 'AsyncClient', factory)
    return clients


@pytest.mark.asyncio
async def test_fma_redirect_reads_real_audio_and_reuses_check(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        assert request.headers['range'] == 'bytes=0-1023'
        if request.url.host == 'freemusicarchive.org':
            return httpx.Response(302, headers={'location':'https://files.freemusicarchive.org/chill.mp3'})
        return httpx.Response(206, headers={'content-type':'audio/mpeg','content-range':'bytes 0-602/100000'}, content=MP3)
    clients = transport(monkeypatch, handler)
    assert await audio.probe_audio_url(URL)
    assert await audio.probe_audio_url(URL)
    assert len(requests) == 2 and all(c.is_closed for c in clients)


@pytest.mark.asyncio
@pytest.mark.parametrize('status,mime,body', [
    (403,'audio/mpeg',MP3), (404,'audio/mpeg',MP3),
    (200,'text/html',b'<html>login</html>'),
    (200,'audio/mpeg',b'<html>not actually audio</html>'),
    (200,'audio/mpeg',b'ID3'),
    (200,'application/octet-stream',b'RIFF0000WEBP'+b'\0'*300),
    (200,'audio/mpeg',b''), (200,'application/json',b'{}'),
])
async def test_bad_sources_fail_closed_and_negative_checks_are_cached(monkeypatch,status,mime,body):
    seen = []
    def handler(request):
        seen.append(request)
        return httpx.Response(status,headers={'content-type':mime},content=body)
    clients = transport(monkeypatch,handler)
    assert not await audio.probe_audio_url(URL)
    assert not await audio.probe_audio_url(URL)
    assert len(seen) == 1 and all(c.is_closed for c in clients)


@pytest.mark.asyncio
@pytest.mark.parametrize('location', ['http://127.0.0.1/private','https://evil.example/audio.mp3',None])
async def test_redirect_cannot_escape_allowed_music_domains(monkeypatch,location):
    seen=[]
    def handler(request):
        seen.append(request)
        return httpx.Response(302,headers={'location':location} if location else {})
    transport(monkeypatch,handler)
    assert not await audio.probe_audio_url(URL)
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_redirect_loop_is_rejected(monkeypatch):
    count=0
    def loop(request):
        nonlocal count
        count+=1
        return httpx.Response(302,headers={'location':URL})
    transport(monkeypatch,loop)
    assert not await audio.probe_audio_url(URL)
    assert count == 10


@pytest.mark.asyncio
async def test_declared_large_file_is_rejected(monkeypatch):
    transport(monkeypatch,lambda req: httpx.Response(206,headers={
        'content-type':'audio/mpeg','content-range':f'bytes 0-1023/{audio.MAX_AUDIO_BYTES+1}'},content=MP3))
    assert not await audio.probe_audio_url(URL)


@pytest.mark.asyncio
async def test_connection_retry_is_bounded_and_cancellation_is_not_retried(monkeypatch):
    calls=0
    def transient(request):
        nonlocal calls
        calls+=1
        if calls==1: raise httpx.ConnectError('temporary',request=request)
        return httpx.Response(206,headers={'content-type':'audio/mpeg'},content=MP3)
    transport(monkeypatch,transient)
    assert await audio.probe_audio_url(URL)
    assert calls==2
    request=httpx.Request('GET',URL)
    from unittest.mock import AsyncMock
    client=SimpleNamespace(send=AsyncMock(side_effect=httpx.ConnectError('offline')))
    with pytest.raises(httpx.ConnectError):
        await audio.send_audio_request(client,request)
    assert client.send.await_count==2
    client.send=AsyncMock(side_effect=asyncio.CancelledError)
    with pytest.raises(asyncio.CancelledError):
        await audio.send_audio_request(client,request)
    client.send.assert_awaited_once()


class AudioStream(httpx.AsyncByteStream):
    def __init__(self,stall=False):
        self.closed=False
        self.stall=stall
        self.entered=asyncio.Event()
        self.remainder_read=False
    async def __aiter__(self):
        self.entered.set()
        if self.stall: await asyncio.Event().wait()
        yield MP3[:256]
        self.remainder_read=True
        yield MP3[256:]
    async def aclose(self): self.closed=True


@pytest.mark.asyncio
async def test_probe_reads_only_a_prefix_and_closes_stream(monkeypatch):
    stream=AudioStream()
    clients=transport(monkeypatch,lambda req:httpx.Response(206,headers={'content-type':'application/octet-stream'},stream=stream))
    assert await audio.probe_audio_url(URL)
    assert not stream.remainder_read and stream.closed and all(c.is_closed for c in clients)


@pytest.mark.asyncio
@pytest.mark.parametrize('cancel',[True,False])
async def test_stalled_body_is_bounded_and_cleanup_always_runs(monkeypatch,cancel):
    stream=AudioStream(stall=True)
    clients=transport(monkeypatch,lambda req:httpx.Response(206,headers={'content-type':'audio/mpeg'},stream=stream))
    if not cancel:
        timeout=asyncio.timeout
        monkeypatch.setattr(audio.asyncio,'timeout',lambda seconds:timeout(0.1))
    task=asyncio.create_task(audio.probe_audio_url(URL))
    await stream.entered.wait()
    if cancel:
        task.cancel()
        with pytest.raises(asyncio.CancelledError): await task
        assert not audio._availability
    else: assert not await task
    assert stream.closed and all(c.is_closed for c in clients)


@pytest.mark.asyncio
async def test_cache_expiration_and_capacity(monkeypatch):
    clock=[100.0]
    monkeypatch.setattr(audio,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    requests=[]
    def handler(req):
        requests.append(req)
        return httpx.Response(206,headers={'content-type':'audio/mpeg'},content=MP3)
    transport(monkeypatch,handler)
    assert await audio.probe_audio_url(URL)
    clock[0]+=301
    assert await audio.probe_audio_url(URL)
    assert len(requests)==2
    for i in range(256):
        await audio.probe_audio_url(f'https://freemusicarchive.org/{i}.mp3')
    assert len(audio._availability)==256


@pytest.mark.parametrize('header',[b'ID3',b'fLaC',b'OggS',b'RIFF0000WAVE',b'\xff\xfb',b'\xff\xf1',b'0000ftyp',b'\x1a\x45\xdf\xa3'])
def test_recognized_audio_headers(header): assert audio.has_audio_header(header)


@pytest.mark.parametrize('url',['http://freemusicarchive.org/a.mp3','https://evil.example/a.mp3','https://user:pass@freemusicarchive.org/a.mp3','https://[bad'])
def test_unsafe_urls_are_rejected(url): assert not audio.is_allowed_audio_url(url)


@pytest.mark.asyncio
async def test_netease_authorized_url_still_requires_actual_audio(monkeypatch):
    transport(monkeypatch,lambda req:httpx.Response(403))
    row={'name':'Song','url':'/api/music/play/netease/1','_audio_url':'https://m7.music.126.net/denied.mp3'}
    assert await audio.filter_playable_candidates([None,row,{'url':'/api/music/play/netease/not-an-id'}])==[]


@pytest.mark.asyncio
async def test_netease_probe_uses_https_without_rewriting_signed_query(monkeypatch):
    seen=[]
    def handler(request):
        seen.append(request)
        assert request.url.scheme=='https'
        assert request.url.params['hint']=='http://keep-original'
        assert request.headers['referer']=='https://music.163.com/'
        return httpx.Response(206,headers={'content-type':'audio/mpeg'},content=MP3)
    transport(monkeypatch,handler)
    row={'url':'/api/music/play/netease/1','_audio_url':'http://m7.music.126.net/song.mp3?hint=http%3A%2F%2Fkeep-original'}
    assert await audio.filter_playable_candidates([row])==[row]
    assert not await audio.probe_audio_url('http://evil.example/anything')
    assert len(seen)==1


@pytest.mark.asyncio
async def test_real_playback_failure_overrides_positive_probe_and_isolates_host(monkeypatch):
    clock=[100.0]
    monkeypatch.setattr(audio,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    requests=[]
    def handler(request):
        requests.append(request)
        return httpx.Response(206,headers={'content-type':'audio/mpeg'},content=MP3)
    transport(monkeypatch,handler)
    assert await audio.probe_audio_url(URL)
    audio.mark_audio_unavailable(URL,connection_failure=True)
    assert not await audio.probe_audio_url(URL)
    assert not await audio.probe_audio_url('https://freemusicarchive.org/another-track.mp3')
    assert await audio.probe_audio_url('https://dl.stream.qqmusic.qq.com/working.m4a')
    assert len(requests)==2
    clock[0]+=301
    assert await audio.probe_audio_url(URL)
    assert len(requests)==3


def test_connection_failure_history_is_bounded():
    for i in range(130):
        audio.mark_audio_unavailable(f'https://s{i}.music.126.net/song.mp3',connection_failure=True)
    assert len(audio._unavailable_hosts)==128


@pytest.mark.asyncio
@pytest.mark.parametrize('connection_failure', [False, True])
async def test_playback_failure_wins_over_an_older_pending_probe(monkeypatch, connection_failure):
    entered, release = asyncio.Event(), asyncio.Event()
    async def handler(request):
        entered.set()
        await release.wait()
        return httpx.Response(206, headers={'content-type': 'audio/mpeg'}, content=MP3)
    transport(monkeypatch, handler)
    pending = asyncio.create_task(audio.probe_audio_url(URL, referer='https://music.163.com/'))
    try:
        await entered.wait()
        audio.mark_audio_unavailable(URL, connection_failure=connection_failure)
        release.set()
        assert not await pending
        assert not await audio.probe_audio_url(URL, referer='https://music.163.com/')
    finally:
        release.set()
        await asyncio.gather(pending, return_exceptions=True)


@pytest.mark.asyncio
async def test_proxy_html_failure_invalidates_a_successful_preflight(monkeypatch):
    from main_routers import music_router
    from starlette.requests import Request
    calls = []
    def handler(request):
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(206, headers={'content-type': 'audio/mpeg'}, content=MP3)
        return httpx.Response(200, headers={'content-type': 'text/html'}, content=b'<html>Unavailable</html>')
    transport(monkeypatch, handler)
    assert await audio.probe_audio_url(URL)
    music_router.MUSIC_PROXY_CACHE.clear()
    response = await music_router.proxy_music(URL, Request({'type': 'http', 'headers': []}))
    assert response.status_code == 502
    assert not await audio.probe_audio_url(URL)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_queue_timeout_does_not_poison_an_unprobed_song(monkeypatch):
    monkeypatch.setattr(audio, '_probe_slots', asyncio.Semaphore(0))
    timeout = asyncio.timeout
    monkeypatch.setattr(audio, 'asyncio', SimpleNamespace(**{**vars(asyncio), 'timeout': lambda seconds: timeout(0.02)}))
    monkeypatch.setattr(audio.httpx, 'AsyncClient', lambda **kwargs: pytest.fail('queued probe must not open a client'))
    assert await audio.probe_audio_url(URL) is None
    assert (URL, None) not in audio._availability


@pytest.mark.asyncio
async def test_refreshed_netease_playback_failure_wins_over_late_old_token_probe(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    row = {'url': '/api/music/play/netease/1', '_audio_url': 'https://m7.music.126.net/old.mp3'}
    async def handler(request):
        entered.set()
        await release.wait()
        return httpx.Response(206, headers={'content-type': 'audio/mpeg'}, content=MP3)
    transport(monkeypatch, handler)
    task = asyncio.create_task(audio.filter_playable_candidates([row]))
    try:
        await entered.wait()
        audio.mark_audio_unavailable('https://m7.music.126.net/new.mp3', candidate_url=row['url'])
        release.set()
        assert await task == []
    finally:
        release.set()
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize('port', ['444', '0', '65536', 'invalid'])
async def test_nonstandard_port_cannot_poison_default_https_host(monkeypatch, port):
    invalid = f'https://freemusicarchive.org:{port}/failed.mp3'
    assert not audio.is_allowed_audio_url(invalid)
    audio.mark_audio_unavailable(invalid, connection_failure=True)
    assert not audio._unavailable_hosts
    transport(monkeypatch, lambda request: httpx.Response(206, headers={'content-type': 'audio/mpeg'}, content=MP3))
    assert await audio.probe_audio_url(URL)
    assert audio.is_allowed_audio_url('https://freemusicarchive.org:443/audio.mp3')
