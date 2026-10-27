import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from main_logic import music_requests
from main_logic.proactive_chat import music_pool as pool, state

real_owner = pool._owner


def tracks(count=20):
    return {'success': True, 'data': [
        {'name': f'Song {i}', 'artist': 'Singer', 'url': f'/api/music/play/netease/{i}'}
        for i in range(count)
    ]}


@pytest.fixture(autouse=True)
def isolate(monkeypatch):
    monkeypatch.setattr(pool, '_pools', pool.OrderedDict())
    monkeypatch.setattr(pool, '_pool_lock', asyncio.Lock())
    monkeypatch.setattr(pool, '_owner', AsyncMock(return_value='account-one'))
    monkeypatch.setattr(pool, '_should_skip_source', lambda key: False)
    monkeypatch.setattr(music_requests, '_recent_music_queries', {})


@pytest.mark.asyncio
async def test_reuses_pool_during_query_cooldown_without_refetching(monkeypatch):
    fetch = AsyncMock(return_value=tracks())
    request = music_requests.MusicRequest()
    first = await pool.fetch_candidates('cat', request, fetch)
    used = {state._source_hash('', f"{first['data'][0]['name']} - Singer")}
    monkeypatch.setattr(pool, '_should_skip_source', lambda key: key in used)
    second = await pool.fetch_candidates('cat', request, fetch)
    assert len(second['data']) == 5
    assert first['data'][0]['name'] not in [t['name'] for t in second['data']]
    fetch.assert_awaited_once_with(20)


@pytest.mark.asyncio
async def test_consumes_twenty_distinct_songs_then_waits_instead_of_repeating(monkeypatch):
    used = set()
    monkeypatch.setattr(pool, '_should_skip_source', lambda key: key in used)
    fetch = AsyncMock(return_value=tracks())
    request = music_requests.MusicRequest()
    names = []
    for _ in range(20):
        result = await pool.fetch_candidates('cat', request, fetch)
        row = result['data'][0]
        names.append(row['name'])
        used.add(state._source_hash('', f"{row['name']} - Singer"))
    assert len(set(names)) == 20
    assert await pool.fetch_candidates('cat', request, fetch) is None
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_requests_share_one_refill_and_return_independent_data():
    started, release = asyncio.Event(), asyncio.Event()
    async def fetch(limit):
        started.set()
        await release.wait()
        return tracks()
    provider = AsyncMock(side_effect=fetch)
    request = music_requests.MusicRequest()
    one = asyncio.create_task(pool.fetch_candidates('cat', request, provider))
    await started.wait()
    two = asyncio.create_task(pool.fetch_candidates('cat', request, provider))
    release.set()
    a, b = await asyncio.gather(one, two)
    a['data'][0]['name'] = 'modified'
    assert all(t['name'] != 'modified' for t in b['data'])
    provider.assert_awaited_once()


@pytest.mark.asyncio
async def test_expired_or_exhausted_pool_refills_after_query_cooldown(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr(pool, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    monkeypatch.setattr(music_requests, 'time', SimpleNamespace(monotonic=lambda: clock[0]))
    fetch = AsyncMock(return_value=tracks())
    request = music_requests.MusicRequest()
    await pool.fetch_candidates('cat', request, fetch)
    clock[0] += pool.POOL_TTL_SECONDS + 1
    assert await pool.fetch_candidates('cat', request, fetch)
    assert fetch.await_count == 2
    monkeypatch.setattr(pool, '_should_skip_source', lambda key: True)
    clock[0] += 301
    assert await pool.fetch_candidates('cat', request, fetch) is None
    assert fetch.await_count == 3


@pytest.mark.asyncio
async def test_switching_account_during_refill_does_not_cache_or_publish(monkeypatch):
    monkeypatch.setattr(pool, '_owner', AsyncMock(side_effect=['old', 'new']))
    assert await pool.fetch_candidates('cat', music_requests.MusicRequest(), AsyncMock(return_value=tracks())) is None
    assert not pool._pools
    assert not music_requests._recent_music_queries


@pytest.mark.asyncio
async def test_switching_account_discards_old_cached_pool(monkeypatch):
    request = music_requests.MusicRequest()
    await pool.fetch_candidates('cat', request, AsyncMock(return_value=tracks()))
    monkeypatch.setattr(pool, '_owner', AsyncMock(return_value='new'))
    fetch = AsyncMock()
    assert await pool.fetch_candidates('cat', request, fetch) is None
    assert not pool._pools
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_failure_backoff_and_bounded_capacity(monkeypatch):
    failed = AsyncMock(return_value=None)
    request = music_requests.MusicRequest()
    assert await pool.fetch_candidates('failed', request, failed) is None
    assert await pool.fetch_candidates('failed', request, failed) is None
    failed.assert_awaited_once()
    fetch = AsyncMock(return_value=tracks())
    for i in range(pool.MAX_POOLS + 1):
        await pool.fetch_candidates(str(i), request, fetch)
    assert len(pool._pools) == pool.MAX_POOLS


@pytest.mark.asyncio
async def test_cancellation_releases_refill_and_does_not_publish():
    entered = asyncio.Event()
    async def fetch(limit):
        entered.set()
        await asyncio.Event().wait()
    task = asyncio.create_task(pool.fetch_candidates('cat', music_requests.MusicRequest(), fetch))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not pool._pools and not pool._pool_lock.locked()
    assert await pool.fetch_candidates('cat', music_requests.MusicRequest(), AsyncMock(return_value=tracks()))


@pytest.mark.asyncio
async def test_real_owner_snapshot_tracks_cookie_and_file_changes(monkeypatch):
    crawler = SimpleNamespace(_check_cookie_freshness=lambda: None, _cookies={'MUSIC_U':'one'},
                              _get_cookie_file_mtime=lambda: 1, _is_vip=False)
    monkeypatch.setattr(pool.music_crawlers, 'get_music_crawlers', lambda: {'netease':crawler})
    monkeypatch.setattr(pool, '_owner', real_owner)
    original = await pool._owner()
    assert await pool._owner() == original
    crawler._cookies = {'MUSIC_U':'two'}
    assert await pool._owner() != original
    original = await pool._owner()
    crawler._get_cookie_file_mtime = lambda: 2
    assert await pool._owner() != original
    original = await pool._owner()
    crawler._is_vip = True
    assert await pool._owner() == original


@pytest.mark.asyncio
async def test_refill_timeout_releases_lock_and_allows_retry(monkeypatch):
    real_timeout = asyncio.timeout
    monkeypatch.setattr(pool.asyncio, 'timeout', lambda seconds: real_timeout(0.01))
    async def stalled(limit):
        await asyncio.Event().wait()
    with pytest.raises(TimeoutError):
        await pool.fetch_candidates('cat', music_requests.MusicRequest(), stalled)
    assert not pool._pool_lock.locked() and not pool._pools
    monkeypatch.setattr(pool.asyncio, 'timeout', real_timeout)
    assert await pool.fetch_candidates('cat', music_requests.MusicRequest(), AsyncMock(return_value=tracks()))


@pytest.mark.asyncio
async def test_unplayable_cached_candidate_is_removed_without_refetching(monkeypatch):
    import httpx
    from utils import music_audio as audio
    real_client = httpx.AsyncClient
    requests = []
    clock = [100.0]
    monkeypatch.setattr(audio,'_availability',audio.OrderedDict())
    monkeypatch.setattr(audio,'time',SimpleNamespace(monotonic=lambda:clock[0]))
    def handler(request):
        requests.append(request)
        return httpx.Response(206,headers={'content-type':'audio/mpeg'},content=b'ID3'+b'\0'*300) if len(requests)==1 else httpx.Response(403)
    monkeypatch.setattr(audio.httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(handler),**kwargs))
    fetch=AsyncMock(return_value={'success':True,'data':[{'name':'Chill','artist':'Artist','url':'https://freemusicarchive.org/chill.mp3'}]})
    request=music_requests.MusicRequest()
    assert await pool.fetch_candidates('cat',request,fetch)
    clock[0]+=301
    assert await pool.fetch_candidates('cat',request,fetch) is None
    assert not next(iter(pool._pools.values()))[1]['data']
    assert await pool.fetch_candidates('cat',request,fetch) is None
    assert len(requests)==2
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_account_change_during_audio_probe_does_not_publish(monkeypatch):
    import httpx
    from utils import music_audio as audio
    real_client=httpx.AsyncClient
    current=['one']
    monkeypatch.setattr(pool,'_owner',AsyncMock(side_effect=lambda:current[0]))
    monkeypatch.setattr(audio,'_availability',audio.OrderedDict())
    def handler(request):
        current[0]='two'
        return httpx.Response(206,headers={'content-type':'audio/mpeg'},content=b'ID3'+b'\0'*300)
    monkeypatch.setattr(audio.httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(handler),**kwargs))
    fetch=AsyncMock(return_value={'success':True,'data':[{'name':'Chill','url':'https://freemusicarchive.org/owner.mp3'}]})
    assert await pool.fetch_candidates('cat',music_requests.MusicRequest(),fetch) is None


@pytest.mark.asyncio
async def test_strict_proactive_request_filters_failed_audio(monkeypatch):
    import httpx
    from utils import music_audio as audio
    from main_logic.proactive_chat import music_recommendation as recommendation
    real_client=httpx.AsyncClient
    monkeypatch.setattr(audio,'_availability',audio.OrderedDict())
    monkeypatch.setattr(audio.httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(lambda req:httpx.Response(403)),**kwargs))
    monkeypatch.setattr(recommendation,'fetch_music_content',AsyncMock(return_value={'success':True,'data':[{'name':'Chill','url':'https://freemusicarchive.org/strict.mp3'}]}))
    assert await recommendation._fetch_music_with_fallback('song:Chill',lanlan_name='cat') is None
    assert not music_requests._recent_music_queries


@pytest.mark.asyncio
async def test_bad_first_batch_uses_other_cached_tracks_without_another_search(monkeypatch):
    import httpx
    from utils import music_audio as audio
    real_client=httpx.AsyncClient
    monkeypatch.setattr(audio,'_availability',audio.OrderedDict())
    monkeypatch.setattr(pool.random,'shuffle',lambda rows:None)
    requests=[]
    def handler(request):
        requests.append(request)
        if 'bad' in request.url.path: return httpx.Response(403)
        return httpx.Response(206,headers={'content-type':'audio/mpeg'},content=b'ID3'+b'\0'*300)
    monkeypatch.setattr(audio.httpx,'AsyncClient',lambda **kwargs:real_client(transport=httpx.MockTransport(handler),**kwargs))
    candidates=[{'name':f'{kind}{i}','url':f'https://freemusicarchive.org/{kind}{i}.mp3'} for kind in ['bad','good'] for i in range(5)]
    fetch=AsyncMock(return_value={'success':True,'data':candidates})
    result=await pool.fetch_candidates('cat',music_requests.MusicRequest(),fetch)
    assert len(result['data'])==5 and all(t['name'].startswith('good') for t in result['data'])
    assert len(requests)==10
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_unprobed_candidates_stay_in_pool_without_starting_query_cooldown(monkeypatch):
    from utils import music_audio as audio
    monkeypatch.setattr(audio, 'probe_audio_url', AsyncMock(return_value=None))
    row = {'name': 'Queued', 'artist': 'Artist', 'url': 'https://freemusicarchive.org/queued.mp3'}
    fetch = AsyncMock(return_value={'success': True, 'data': [row]})
    request = music_requests.MusicRequest()
    assert await pool.fetch_candidates('queued', request, fetch) is None
    assert next(iter(pool._pools.values()))[1]['data'] == [row]
    assert not music_requests.was_music_request_recent('queued', request)
    assert await pool.fetch_candidates('queued', request, fetch) is None
    fetch.assert_awaited_once()


@pytest.mark.asyncio
async def test_all_failed_audio_does_not_mark_a_successful_query(monkeypatch):
    from utils import music_audio as audio
    monkeypatch.setattr(audio, 'probe_audio_url', AsyncMock(return_value=False))
    row = {'name': 'Broken', 'url': 'https://freemusicarchive.org/broken.mp3'}
    request = music_requests.MusicRequest()
    assert await pool.fetch_candidates('broken', request, AsyncMock(return_value={'success': True, 'data': [row]})) is None
    assert not music_requests.was_music_request_recent('broken', request)
