"""Music candidates must be playable, varied, and deduplicated before delivery."""

import asyncio
import json
from collections import deque
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from main_logic.proactive_chat import delivery, music_recommendation, state
from utils import music_crawlers as music


@pytest.fixture
async def crawler(monkeypatch):
    monkeypatch.setattr(music.NeteaseCrawler, '_load_cookies', lambda self: None)
    monkeypatch.setattr(music.NeteaseCrawler, '_check_cookie_freshness', lambda self: None)
    monkeypatch.setattr(music.NeteaseCrawler, '_get_cookie_file_mtime', lambda self: 0.0)
    value = music.NeteaseCrawler()
    await value.client.aclose()
    yield value
    await value.close()


def song(song_id, fee=8, name=None):
    return {'id': song_id, 'name': name or f'Song {song_id}', 'fee': fee,
            'artists': [{'name': 'Singer'}], 'duration': 180000}


def track(song_id, name=None):
    return {'name': name or f'Song {song_id}', 'artist': 'Singer',
            'url': f'/api/music/play/netease/{song_id}'}


def authorized(song_id, **overrides):
    return {'id': song_id, 'code': 200, 'url': f'https://m7.music.126.net/{song_id}.mp3',
            'freeTrialInfo': None, 'br': 128000, **overrides}


def transport(crawler, handler):
    crawler.client = httpx.AsyncClient(transport=httpx.MockTransport(handler))


def selection(tracks, skipped=()):
    return music_recommendation._select_music_recommendation(
        {'formatted_content': 'candidate material', 'raw_data': {'success': True, 'data': tracks}},
        lang='en', source_hash=state._source_hash, should_skip_source=lambda key: key in skipped,
    )


@pytest.mark.asyncio
async def test_search_includes_fee_eight_and_checks_real_authorization(crawler):
    requested_ids = []

    def handle(request):
        if request.url.path == '/api/search/get/web':
            assert request.headers.get('cookie', '') == ''
            return httpx.Response(200, json={'code': 200, 'result': {
                'songs': [song(1, 0), song(2, 8), song(3, 1), song(4, 4)]}})
        assert request.url.path == '/api/song/enhance/player/url'
        data = dict(httpx.QueryParams(request.content.decode()))
        assert data['br'] == '320000'
        requested_ids.extend(json.loads(data['ids']))
        # The fee=0 song has no usable audio; fee=8 does.
        return httpx.Response(200, json={'code': 200, 'data': [
            authorized(1, url=None), authorized(2)]})

    transport(crawler, handle)
    result = await crawler.search('pop', limit=5)
    assert requested_ids == [1, 2]
    assert [t['url'] for t in result] == ['/api/music/play/netease/2']


@pytest.mark.asyncio
async def test_search_backfills_after_unplayable_first_result(crawler):
    def handle(request):
        if request.url.path.endswith('/search/get/web'):
            return httpx.Response(200, json={'code': 200, 'result': {
                'songs': [song(1), song(2), song(3)]}})
        return httpx.Response(200, json={'code': 200, 'data': [
            authorized(1, url=None), authorized(2, freeTrialInfo={'end': 30000}),
            authorized(3)]})

    transport(crawler, handle)
    result = await crawler.search('pop', limit=1)
    assert [t['name'] for t in result] == ['Song 3']


@pytest.mark.asyncio
async def test_vip_metadata_does_not_bypass_playback_permissions(crawler):
    crawler._is_vip = True
    def handle(request):
        if request.url.path.endswith('/search/get/web'):
            return httpx.Response(200, json={'code': 200, 'result': {
                'songs': [song(1, 1), song(2, 4)]}})
        return httpx.Response(200, json={'code': 200, 'data': [
            authorized(1), authorized(2, code=404, url=None)]})

    transport(crawler, handle)
    assert [t['name'] for t in await crawler.search('singer', limit=2)] == ['Song 1']


@pytest.mark.asyncio
@pytest.mark.parametrize('invalid', [
    {'url': None}, {'url': ''}, {'url': 'file:///song.mp3'},
    {'url': 'https://user:password@music.126.net/song.mp3'},
    {'code': 404}, {'freeTrialInfo': {}}, {'freeTrialInfo': {'end': 30000}},
])
async def test_unplayable_and_trial_tracks_are_excluded(crawler, invalid):
    transport(crawler, lambda request: httpx.Response(200, json={
        'code': 200, 'data': [authorized(1, **invalid)]}))
    assert await crawler.filter_playable_tracks([track(1)]) == []


@pytest.mark.asyncio
async def test_batch_authorization_matches_ids_not_provider_response_order(crawler):
    requests = []
    def handle(request):
        requests.append(json.loads(dict(httpx.QueryParams(request.content.decode()))['ids']))
        return httpx.Response(200, json={'code': 200, 'data': [authorized(2), authorized(1)]})

    transport(crawler, handle)
    result = await crawler.filter_playable_tracks([track(1), track(1), track(2)])
    assert requests == [[1, 2]]
    assert result == [{**track(1), '_audio_url': authorized(1)['url']}, {**track(2), '_audio_url': authorized(2)['url']}]


@pytest.mark.asyncio
async def test_authorization_is_bounded_and_fails_closed(crawler):
    requests = []
    def handle(request):
        ids = json.loads(dict(httpx.QueryParams(request.content.decode()))['ids'])
        requests.append(ids)
        raise httpx.ReadTimeout('provider timeout', request=request)

    transport(crawler, handle)
    assert await crawler.filter_playable_tracks([track(i) for i in range(200)]) == []
    assert len(requests) == 1 and len(requests[0]) == 100


@pytest.mark.asyncio
async def test_cancellation_stops_authorization_request(crawler):
    started, stopped = asyncio.Event(), asyncio.Event()
    async def handle(request):
        started.set()
        try:
            await asyncio.Future()
        finally:
            stopped.set()

    transport(crawler, handle)
    task = asyncio.create_task(crawler.filter_playable_tracks([track(1)]))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert stopped.is_set()


@pytest.mark.asyncio
async def test_unresponsive_authorization_has_a_total_deadline(crawler):
    stopped = asyncio.Event()
    async def handle(request):
        try:
            await asyncio.Future()
        finally:
            stopped.set()
    transport(crawler, handle)
    result = await asyncio.wait_for(crawler.filter_playable_tracks([track(1)]), timeout=8.0)
    assert result == [] and stopped.is_set()


@pytest.mark.asyncio
async def test_cookie_change_during_authorization_discards_late_result(crawler):
    crawler._cookies = {'MUSIC_U': 'old-account'}
    async def handle(request):
        assert request.headers['cookie'] == 'MUSIC_U=old-account'
        crawler._cookies = {'MUSIC_U': 'new-account'}
        return httpx.Response(200, json={'code': 200, 'data': [authorized(1)]})

    transport(crawler, handle)
    assert await crawler.filter_playable_tracks([track(1)]) == []


@pytest.mark.asyncio
async def test_cookie_file_change_during_authorization_discards_late_result(crawler, monkeypatch):
    times = iter([0.0, 1.0])
    monkeypatch.setattr(crawler, '_get_cookie_file_mtime', lambda: next(times))
    transport(crawler, lambda request: httpx.Response(200, json={
        'code': 200, 'data': [authorized(1)]}))
    assert await crawler.filter_playable_tracks([track(1)]) == []


@pytest.mark.asyncio
@pytest.mark.parametrize('payload', [None, [], {'code': 403}, {'code': 200, 'data': None}])
async def test_malformed_authorization_payload_fails_closed(crawler, payload):
    transport(crawler, lambda request: httpx.Response(200, json=payload))
    assert await crawler.filter_playable_tracks([track(1)]) == []


@pytest.mark.asyncio
async def test_one_bad_provider_row_does_not_drop_another_playable_song(crawler):
    transport(crawler, lambda request: httpx.Response(200, json={'code': 200, 'data': [
        None, authorized(1, id=[]), authorized(1, url='https://[invalid'), authorized(2)]}))
    assert await crawler.filter_playable_tracks([track(1), track(2)]) == [{**track(2), '_audio_url': authorized(2)['url']}]


@pytest.mark.asyncio
async def test_invalid_candidate_urls_do_not_trigger_authorization(crawler):
    def unexpected(request):
        pytest.fail('no usable candidate ID should reach the network')
    transport(crawler, unexpected)
    assert await crawler.filter_playable_tracks([
        None, {'url': 'https://[invalid'}, {'url': '/not-a-netease-song'},
        {'url': '/api/music/play/netease/' + '1' * 100},
    ]) == []


@pytest.mark.asyncio
async def test_changed_cookie_file_prevents_authorization(crawler, monkeypatch):
    monkeypatch.setattr(crawler, '_get_cookie_file_mtime', lambda: 1.0)
    transport(crawler, lambda request: pytest.fail('stale credentials must not be sent'))
    assert await crawler.filter_playable_tracks([track(1)]) == []


@pytest.mark.asyncio
async def test_first_vip_lookup_keeps_candidates_for_the_same_account(crawler, monkeypatch):
    from main_logic import music_requests
    from main_logic.proactive_chat import music_pool as pool
    monkeypatch.setattr(pool, '_pools', pool.OrderedDict())
    monkeypatch.setattr(pool, '_pool_lock', asyncio.Lock())
    monkeypatch.setattr(pool, '_should_skip_source', lambda key: False)
    monkeypatch.setattr(music_requests, '_recent_music_queries', {})
    monkeypatch.setattr(music, 'get_music_crawlers', lambda: {'netease': crawler})
    crawler._has_cookies = True
    crawler._cookies = {'MUSIC_U': 'same-account'}
    transport(crawler, lambda request: httpx.Response(200, json={
        'code': 200, 'profile': {'userId': 7, 'vipType': 1}}))

    async def fetch(limit):
        assert await crawler._get_personalization_user_id() == 7
        return {'success': True, 'data': [track(1)]}

    result = await pool.fetch_candidates('vip-listener', music_requests.MusicRequest(), fetch)
    assert result and result['data'] == [track(1)]
    assert crawler._is_vip


@pytest.mark.asyncio
@pytest.mark.parametrize('payload,vip,checked,invalid', [
    ({'code': 200, 'profile': {'userId': 7, 'vipType': 1}}, True, True, False),
    ({'code': 200, 'data': {'userId': 7, 'associator': {'isVip': True}}}, True, True, False),
    ({'code': 200, 'account': {'id': 7}}, False, True, False),
    ({'code': 301}, False, True, True),
    ({'code': 500}, False, False, False),
])
async def test_candidate_fee_permissions_follow_account_status(crawler, monkeypatch, payload, vip, checked, invalid):
    response = httpx.Response(200, json=payload)
    get = AsyncMock(return_value=response)
    monkeypatch.setattr(crawler, '_personalization_http_get', get)
    await crawler._check_vip_status()
    assert (crawler._is_vip, crawler._vip_checked, crawler._cookie_invalid) == (vip, checked, invalid)
    if checked:
        await crawler._check_vip_status()
        assert get.await_count == 1


@pytest.mark.asyncio
async def test_account_probe_failure_stays_retryable(crawler, monkeypatch):
    monkeypatch.setattr(crawler, '_personalization_http_get', AsyncMock(side_effect=httpx.ReadTimeout('timeout')))
    await crawler._check_vip_status()
    assert crawler._vip_checked is False


@pytest.mark.asyncio
async def test_playlist_metadata_admits_fee_eight_and_caches_candidates(crawler, monkeypatch):
    api = AsyncMock(side_effect=[{'playlist': {'trackIds': [{'id': 1}, {'id': 2}]}},
                                {'songs': [song(1, 8), song(2, 1)]}])
    monkeypatch.setattr(crawler, '_personalization_api_call', api)
    result = await crawler._fetch_playlist_tracks(10)
    assert [t['id'] for t in result] == [1]
    assert result[0]['fee'] == 8
    assert await crawler._fetch_playlist_tracks(10) == result
    assert api.await_count == 2


@pytest.mark.asyncio
async def test_daily_metadata_keeps_fee_eight_and_excludes_paid_duplicates(crawler, monkeypatch):
    api = AsyncMock(return_value={'data': {'dailySongs': [song(1, 8), song(1, 8), song(2, 1)]}})
    monkeypatch.setattr(crawler, '_personalization_api_call', api)
    result = await crawler.get_daily_recommendations(7)
    assert [t['id'] for t in result] == [1]
    assert await crawler.get_daily_recommendations(7) == result
    assert api.await_count == 1


@pytest.mark.asyncio
async def test_artist_exploration_admits_fee_eight_and_skips_familiar_tracks(crawler, monkeypatch):
    api = AsyncMock(return_value={'songs': [song(1, 8), song(2, 1), song(3, 8)]})
    monkeypatch.setattr(crawler, '_personalization_api_call', api)
    snapshot = {'subscribed_artists': [{'id': 7, 'name': 'Singer'}], 'liked_track_ids': [3]}
    result = await crawler._fetch_exploration_tracks(snapshot, 'Singer', 5)
    assert [t['id'] for t in result] == [1]


@pytest.fixture
def providers(monkeypatch):
    values = {name: SimpleNamespace(search=AsyncMock(return_value=[]), _cookie_invalid=False,
                                   _personalization_error_code='', personalized_recommendations=AsyncMock(return_value=[]))
              for name in ('netease', 'qqmusic', 'fma', 'soundcloud', 'bandcamp', 'musopen', 'itunes')}
    monkeypatch.setattr(music, 'get_music_crawlers', lambda: values)
    monkeypatch.setattr(music, 'is_china_region', lambda: True)
    monkeypatch.setattr(music.music_cache, 'cache', [])
    return values


@pytest.mark.asyncio
@pytest.mark.parametrize('keyword,source', [('classical piano', 'musopen'), ('lofi', 'bandcamp')])
async def test_recommendation_routing_preserves_genre_intent(providers, keyword, source):
    providers[source].search.return_value = [track(1)]
    result = await music.fetch_music_content(keyword, limit=1, personalized=True)
    assert result['success'] is True
    assert result['data'] == [track(1)]
    providers['netease'].search.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_errors_fall_back_without_recommending_unplayable_material(providers):
    for name in ('netease', 'qqmusic', 'fma'):
        providers[name].search.side_effect = httpx.ReadTimeout('provider unavailable')
    providers['soundcloud'].search.return_value = [track(2)]
    result = await music.fetch_music_content('pop', limit=1, personalized=True)
    assert result['success'] is True and result['data'] == [track(2)]


@pytest.mark.asyncio
async def test_cancellation_stops_all_open_fallback_sources(providers):
    started, stopped = asyncio.Event(), []
    async def blocked(*args):
        started.set()
        try:
            await asyncio.Future()
        finally:
            stopped.append(True)
    for name in ('fma', 'soundcloud', 'bandcamp'):
        providers[name].search.side_effect = blocked
    task = asyncio.create_task(music.fetch_music_content('pop', personalized=True))
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(stopped) == 3


@pytest.mark.asyncio
async def test_strict_liked_failure_does_not_replace_user_source(providers):
    providers['netease']._cookie_invalid = True
    providers['netease'].personalized_recommendations.side_effect = RuntimeError('expired credentials')
    result = await music.fetch_music_content('', personalized=True, personalization_source='liked')
    assert result['success'] is False and result['error_code'] == 'cookie_invalid'
    for provider in providers.values():
        provider.search.assert_not_awaited()


@pytest.mark.asyncio
async def test_search_cache_removes_repeated_recommendations_without_random_retry(providers):
    providers['netease'].search.return_value = [track(1)]
    assert (await music.fetch_music_content('pop'))['success'] is True
    assert (await music.fetch_music_content('pop'))['success'] is False


@pytest.mark.asyncio
async def test_liked_recommendations_also_check_playability(crawler, monkeypatch):
    monkeypatch.setattr(crawler, '_get_personalization_user_id', AsyncMock(return_value=1))
    monkeypatch.setattr(crawler, '_fetch_visible_playlists', AsyncMock(return_value=[
        {'id': 10, 'special_type': 5}]))
    monkeypatch.setattr(crawler, '_fetch_playlist_tracks', AsyncMock(return_value=[track(1), track(2)]))
    transport(crawler, lambda request: httpx.Response(200, json={'code': 200, 'data': [
        authorized(1, url=None), authorized(2)]}))
    result = await crawler.personalized_recommendations(personalization_source='liked', limit=1)
    assert result == [{**track(2), 'recommendation_source': 'liked', '_audio_url': authorized(2)['url']}]


@pytest.mark.asyncio
async def test_recommendations_shuffle_after_dedup_but_explicit_requests_keep_priority(monkeypatch):
    tracks = [track(1), track(2), {**track(1), 'url': 'https://other.example/same-song'}]
    provider = SimpleNamespace(search=AsyncMock(return_value=tracks), _cookie_invalid=False)
    monkeypatch.setattr(music, 'get_music_crawlers', lambda: {'netease': provider})
    monkeypatch.setattr(music, 'is_china_region', lambda: True)
    monkeypatch.setattr(music.music_cache, 'cache', [])
    shuffle_inputs = []
    def reverse(items):
        shuffle_inputs.append(list(items))
        items.reverse()
    monkeypatch.setattr(music.random, 'shuffle', reverse)

    result = await music.fetch_music_content('pop', limit=2, personalized=True)
    assert len(shuffle_inputs) == 1 and len(shuffle_inputs[0]) == 2
    assert [t['name'] for t in result['data']] == ['Song 2', 'Song 1']
    shuffle_inputs.clear()
    result = await music.fetch_music_content('Song 1', limit=2, requested_song='Song 1',
                                             bypass_recommendation_dedupe=True)
    assert result['data'][0]['name'] == 'Song 1'
    assert shuffle_inputs == []


def test_selector_removes_all_suppressed_tracks_from_prompt_and_delivery():
    tracks = [track(1), track(2), track(3)]
    skipped = {state._source_hash('', 'Song 2 - Singer')}
    result = selection(tracks, skipped)
    assert result.link['title'] == 'Song 1'
    assert [t['name'] for t in result.content['raw_data']['data']] == ['Song 1', 'Song 3']
    assert 'Song 2' not in result.topic
    links = [result.link]
    music_recommendation._append_music_recommendations(links, result.content)
    assert [link['title'] for link in links] == ['Song 1', 'Song 3']


def test_selector_deduplicates_same_song_across_urls_and_honors_legacy_history():
    duplicate = {**track(1), 'url': 'https://other.example/song?vkey=new'}
    result = selection([track(1), duplicate, track(2)])
    assert result.content['raw_data']['data'] == [track(1), track(2)]
    assert selection([track(1)], {state._source_hash(track(1)['url'])}).link is None


@pytest.mark.asyncio
@pytest.mark.parametrize('mode', ['success', 'no_tts', 'preempted', 'failed', 'late_failure', 'commit_rejected'])
async def test_music_delivery_failure_and_takeover_do_not_publish_a_music_result(mode):
    mgr = SimpleNamespace(current_speech_id='s', last_user_engagement_time=0,
                          state=SimpleNamespace(proactive_sid='s', is_proactive_preempted=lambda sid: False),
                          handle_new_message=AsyncMock())
    async def feed(*args, **kwargs):
        if mode == 'preempted':
            mgr.last_user_engagement_time = 1
            return False
        if mode in ('failed', 'late_failure'):
            if mode == 'late_failure':
                mgr.current_speech_id = 'replacement'
            raise RuntimeError('delivery failed')
        return mode != 'no_tts'
    mgr.feed_tts_chunk = feed
    mgr.finish_proactive_delivery = AsyncMock(return_value=mode != 'commit_rejected')
    result = await delivery._commit_proactive_delivery(
        mgr=mgr, proactive_sid='s', lanlan_name='test', response_text='Would you like to listen?',
        source_tag='CHAT', active_channels=['music'], selected_web_link=None,
        selected_music_link=None, selected_meme_link=None,
        music_content={'raw_data': {'success': True, 'data': [track(1)]}}, is_music_used=False,
        is_playing_music=False, music_cooldown=False, vision_content=None, phase2_use_vision=False,
        screenshot_b64=None, proactive_lang='en', master_name='User',
    )
    if mode in ('success', 'no_tts'):
        assert result.result is None and result.delivery.is_music_used
        assert result.delivery.delivered_music_link['title'] == 'Song 1'
    else:
        assert result.delivery is None and result.result.body['action'] == 'pass'
    if mode == 'late_failure':
        mgr.handle_new_message.assert_not_awaited()
    elif mode not in ('success', 'no_tts'):
        mgr.handle_new_message.assert_awaited_once()


@pytest.mark.parametrize('selected,links,expected', [
    (None, [], False),
    ({'url': '/song'}, [None, {'url': '/song'}], True),
    ({'url': '/song'}, [{'url': '/different'}], False),
    ({'title': 'Song', 'artist': 'Singer'}, [{'title': 'Song', 'artist': 'Singer'}], True),
    ({'title': 'Song'}, [{'title': 'Different'}], False),
])
def test_only_delivered_links_are_eligible_for_history(selected, links, expected):
    assert delivery._is_link_selected(selected, links) is expected


def test_empty_music_material_never_creates_a_recommendation():
    assert music_recommendation._select_music_recommendation(
        None, lang='en', source_hash=state._source_hash, should_skip_source=lambda key: False,
    ).link is None
    assert selection([]).link is None
    assert music_recommendation._format_music_content({'success': False}, 'en') == ''
    assert 'Album' in music_recommendation._format_music_content({
        'success': True, 'data': [{'name': 'Song', 'artist': 'Singer', 'album': 'Album'}]}, 'en')


def test_current_playback_context_forbids_replacing_the_current_song():
    hint = music_recommendation._build_music_playing_hint(
        is_playing_music=True, current_track={'name': 'Current Song'}, master_name='User', lang='en')
    assert 'Current Song' in hint and 'DO NOT' in hint
    unknown = music_recommendation._build_music_playing_hint(
        is_playing_music=True, current_track={'artist': 'Singer'}, master_name='User', lang='en')
    assert unknown and 'None' not in unknown
    constraint = music_recommendation._build_music_dynamic_context(
        selected_music_link=None, music_content=None, is_playing_music=True, master_name='User', lang='en')
    assert constraint


def test_completed_playback_resets_channel_decay_but_keeps_song_dedup(monkeypatch):
    key = state._source_hash('', 'Song 1 - Singer')
    entry = {'kind': 'music', 'ts': 100.0, 'title': 'Song 1 - Singer'}
    monkeypatch.setattr(state, '_source_history', {key: entry})
    monkeypatch.setattr(state, '_proactive_chat_history', {'listener': deque([
        (100.0, 'Music offer', 'music'), (101.0, 'Other topic', 'web')])})
    assert music_recommendation._record_music_played_through('listener') == 1
    assert list(state._proactive_chat_history['listener']) == [
        (100.0, 'Music offer', ''), (101.0, 'Other topic', 'web')]
    assert state._source_history[key] == entry


def test_song_identity_without_artist_matches_the_persisted_title():
    result = selection([{**track(1), 'artist': ''}])
    assert result.topic_key == state._source_hash('', 'Song 1')


@pytest.mark.asyncio
async def test_song_identity_survives_url_change_and_history_reload(tmp_path, monkeypatch):
    link = {'title': 'Song 1', 'artist': 'Singer', 'url': track(1)['url']}
    monkeypatch.setattr(state, '_source_history', {})
    monkeypatch.setattr(state, '_source_history_loaded', False)
    monkeypatch.setattr(state, '_source_history_authoritative', False)
    monkeypatch.setattr(state, '_proactive_chat_totals_loaded', False)
    # Source dedup must still persist when optional memory feedback is unavailable.
    feedback = AsyncMock(side_effect=httpx.ReadTimeout('memory feedback unavailable'))
    monkeypatch.setattr(delivery, '_get_internal_http_client', lambda: SimpleNamespace(post=feedback))
    committed = delivery.CommittedDelivery('test-turn', 'music', [link], 'MUSIC', link, True, '', None)
    await delivery._record_committed_delivery(
        mgr=SimpleNamespace(), delivery=committed, lanlan_name='music-test', response_text='caption',
        source_tag='MUSIC', active_channels=['music'], has_unfinished_thread=False,
        surfaced_reflection_ids=[17], selected_web_link=None, selected_web_topic_key=None,
        web_parsed=None, selected_music_link=link,
        selected_music_topic_key=state._source_hash('', 'Song 1 - Singer'),
        selected_meme_link=None, selected_meme_topic_key=None, meme_content=None, memory_dir=tmp_path,
    )
    saved = json.loads((tmp_path / 'proactive_source_history.json').read_text(encoding='utf-8'))
    feedback.assert_awaited_once()
    key = state._source_hash('', 'Song 1 - Singer')
    assert saved['entries'][key]['kind'] == 'music'
    monkeypatch.setattr(state, '_source_history', {})
    monkeypatch.setattr(state, '_source_history_loaded', False)
    await state._ensure_source_history_loaded(memory_dir=tmp_path)
    from main_logic.proactive_chat.decisions import _should_skip_source
    changed_url = {**track(1), 'url': 'https://other.example/song?vkey=new'}
    result = music_recommendation._select_music_recommendation(
        {'formatted_content': 'candidate', 'raw_data': {'success': True, 'data': [changed_url]}},
        lang='en', source_hash=state._source_hash, should_skip_source=_should_skip_source,
    )
    assert result.link is None
