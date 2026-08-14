import os
import sys
import pytest
import asyncio
import json
from unittest.mock import patch, MagicMock, AsyncMock
from typing import List, Dict, Any
import httpx

# 添加项目根目录到 sys.path，确保可以导入 utils
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../")))

from utils.music_crawlers import (
    NeteaseCrawler, QQMusicCrawler, iTunesCrawler, SoundCloudCrawler,
    MusopenCrawler, FMACrawler, BandcampCrawler, MusicCache, fetch_music_content,
    music_cache, close_all_crawlers, _select_requested_song,
    _sample_distinct_background_sources,
)

# ==========================================
# 辅助函数 (Helpers)
# ==========================================

@pytest.fixture(autouse=True)
async def clear_music_caches():
    """每个测试前清理全局缓存，防止干扰"""
    # 清理去重缓存
    music_cache.cache = []
    # 关闭并重置爬虫实例缓存
    await close_all_crawlers()
    yield
    # 测试后再次清理
    await close_all_crawlers()

# ==========================================
# 模拟数据 (Mock Data)
# ==========================================

MOCK_NETEASE_JSON = {
    "code": 200,
    "result": {
        "songs": [
            {
                "id": 12345,
                "name": "Netease Song",
                "artists": [
                    {"name": "Netease Artist"},
                    {"name": "Featured Artist"},
                ],
                "fee": 0,
                "album": {"picUrl": "http://p1.music.126.net/cover.jpg"}
            }
        ]
    }
}

MOCK_ITUNES_JSON = {
    "results": [
        {
            "trackName": "iTunes Song",
            "artistName": "iTunes Artist",
            "previewUrl": "http://preview.url/1",
            "artworkUrl100": "http://artwork.url/100x100bb.jpg"
        }
    ]
}

MOCK_QQMUSIC_SEARCH_JSON = {
    "music.search.SearchCgiService": {
        "data": {
            "body": {
                "song": {
                    "list": [
                        {
                            "mid": "song_mid_1",
                            "name": "QQ Music Song",
                            "interval": 180,
                            "singer": [{"name": "QQ Artist"}, {"name": "Featured Artist"}],
                            "album": {"mid": "album_mid_1"},
                            "file": {"media_mid": "media_mid_1"},
                        },
                        {
                            "mid": "too_long",
                            "name": "Long DJ Set",
                            "interval": 601,
                            "singer": [{"name": "DJ"}],
                            "album": {"mid": "album_mid_2"},
                        },
                    ]
                }
            }
        }
    }
}

MOCK_FMA_HTML = """
<html>
<body>
    <div data-track-info='{"title": "FMA Song", "artistName": "FMA Artist", "playbackUrl": "http://fma.url/1", "image": "http://fma.img/1"}'></div>
</body>
</html>
"""

MOCK_MUSOPEN_HTML = """
<html>
<body>
    <meta property="og:image" content="http://musopen.img/1">
    <a href="http://musopen.url/piano.mp3?filename=Test.mp3">Download</a>
</body>
</html>
"""

# ==========================================
# 1. MusicCache 测试
# ==========================================

@pytest.mark.unit
def test_music_cache_deduplication():
    cache = MusicCache(expire_seconds=10)
    track = {"url": "http://test.url", "name": "Test", "artist": "Artist"}
    
    # 初始状态不重复
    assert not cache.is_duplicate(track['url'], track['name'], track['artist'])
    
    # 添加后重复
    cache.add(track)
    assert cache.is_duplicate(track['url'], track['name'], track['artist'])
    assert cache.is_duplicate("http://test.url", "", "")
    assert cache.is_duplicate("", "Test", "Artist")

@pytest.mark.unit
def test_music_cache_diversity():
    cache = MusicCache()
    tracks = [
        {"name": "Song 1", "artist": "Artist A"},
        {"name": "Song 2", "artist": "Artist B"},
        {"name": "Lofi Track", "artist": "Artist A"}
    ]
    score = cache.get_diversity_score(tracks)
    assert score['unique_artists'] == 2
    assert "放松氛围" in score['style_notes']
    assert score['score'] > 0

# ==========================================
# 2. 爬虫单元测试 (Mocked)
# ==========================================

@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_crawler_parsing():
    crawler = NeteaseCrawler()
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = MOCK_NETEASE_JSON
    
    with patch.object(
        httpx.AsyncClient,
        'post',
        new=AsyncMock(return_value=mock_response),
    ) as post:
        results: List[Dict[str, Any]] = await crawler.search("test", limit=1)
        assert len(results) == 1
        assert results[0]['name'] == "Netease Song"
        assert results[0]['artist'] == "Netease Artist / Featured Artist"
        assert "12345" in results[0]['url']
        assert results[0]['cover'] == "https://p1.music.126.net/cover.jpg"
        assert post.await_args.kwargs['headers'] == {'Cookie': ''}
        assert post.await_args.kwargs['data']['limit'] == 5
        assert post.await_args.kwargs['timeout'] == 5.0
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_qqmusic_crawler_returns_only_resolved_playable_tracks():
    crawler = QQMusicCrawler()
    search_response = MagicMock(status_code=200)
    search_response.json.return_value = MOCK_QQMUSIC_SEARCH_JSON
    search_response.raise_for_status.return_value = None

    with (
        patch.object(httpx.AsyncClient, 'post', new=AsyncMock(return_value=search_response)) as post,
        patch.object(
            crawler,
            '_resolve_playable_url',
            new=AsyncMock(return_value='https://dl.stream.qqmusic.qq.com/C400song_mid_1.m4a'),
        ) as resolve,
    ):
        results = await crawler.search('测试歌曲', limit=2)

    assert len(results) == 1
    assert results[0]['name'] == 'QQ Music Song'
    assert results[0]['artist'] == 'QQ Artist / Featured Artist'
    assert results[0]['url'].startswith('https://dl.stream.qqmusic.qq.com/')
    assert results[0]['cover'].endswith('album_mid_1.jpg')
    assert results[0]['duration'] == 180
    assert resolve.await_args.args == ('song_mid_1', 'media_mid_1')
    assert post.await_args.kwargs['json']['music.search.SearchCgiService']['module'] == 'music.search.SearchCgiService'
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_qqmusic_crawler_resolves_https_stream_url():
    crawler = QQMusicCrawler()
    stream_response = MagicMock(status_code=200)
    stream_response.raise_for_status.return_value = None
    stream_response.json.return_value = {
        'req_0': {
            'data': {
                'sip': ['https://dl.stream.qqmusic.qq.com/'],
                'midurlinfo': [{'purl': 'C400song_mid_1.m4a?vkey=temporary'}],
            }
        }
    }

    with patch.object(httpx.AsyncClient, 'post', new=AsyncMock(return_value=stream_response)) as post:
        result = await crawler._resolve_playable_url('song_mid_1', 'media_mid_1')

    assert result == 'https://dl.stream.qqmusic.qq.com/C400song_mid_1.m4a?vkey=temporary'
    assert post.await_args.kwargs['json']['req_0']['module'] == 'vkey.GetVkeyServer'
    assert post.await_args.kwargs['json']['req_0']['param']['filename'] == ['C400media_mid_1.m4a']
    await crawler.close()


@pytest.mark.unit
def test_netease_library_track_upgrades_trusted_cover_to_https():
    track = NeteaseCrawler._normalize_library_track({
        'id': 123,
        'name': 'Song',
        'ar': [{'name': 'Artist'}],
        'al': {'picUrl': '//p2.music.126.net/library-cover.jpg'},
        'dt': 180000,
    })

    assert track is not None
    assert track['cover'] == 'https://p2.music.126.net/library-cover.jpg'


@pytest.mark.unit
def test_netease_library_track_builds_cover_from_pic_id():
    track = NeteaseCrawler._normalize_library_track({
        'id': 246935,
        'name': 'Song',
        'ar': [{'name': 'Artist'}],
        'al': {'picId': 130841883718261},
        'dt': 180000,
    })

    assert track is not None
    assert track['cover'] == (
        'https://p2.music.126.net/ykvStv36gO8D1JlW14Vr9A=='
        '/130841883718261.jpg?param=130y130'
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_crawler_skips_ten_minute_candidates_and_backfills_limit():
    crawler = NeteaseCrawler()
    crawler._vip_checked = True
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = {
        "code": 200,
        "result": {
            "songs": [
                {
                    "id": 1,
                    "name": "Long DJ Mix",
                    "artists": [{"name": "DJ"}],
                    "fee": 0,
                    "duration": 10 * 60 * 1000,
                    "album": {"picUrl": "http://pic.url/long"},
                },
                {
                    "id": 2,
                    "name": "Normal Song",
                    "artists": [{"name": "Singer"}],
                    "fee": 0,
                    "duration": 4 * 60 * 1000,
                    "album": {"picUrl": "http://pic.url/normal"},
                },
            ]
        },
    }

    with patch.object(httpx.AsyncClient, 'post', new=AsyncMock(return_value=mock_response)):
        results = await crawler.search("test", limit=1)

    assert [track['name'] for track in results] == ["Normal Song"]
    assert results[0]['duration'] == 4 * 60
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_crawler_scales_candidate_count_with_requested_limit():
    crawler = NeteaseCrawler()
    crawler._vip_checked = True
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = {'code': 200, 'result': {'songs': []}}

    with patch.object(
        httpx.AsyncClient,
        'post',
        new=AsyncMock(return_value=mock_response),
    ) as post:
        await crawler.search('test', limit=8)

    assert post.await_args.kwargs['data']['limit'] == 10
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_crawler_backfills_free_candidates_after_paid_first_page():
    crawler = NeteaseCrawler()
    crawler._vip_checked = True
    paid_response = MagicMock(status_code=200)
    paid_response.json.return_value = {
        'code': 200,
        'result': {
            'songs': [
                {'id': index, 'name': f'Paid {index}', 'fee': 1}
                for index in range(10)
            ],
        },
    }
    free_response = MagicMock(status_code=200)
    free_response.json.return_value = {
        'code': 200,
        'result': {
            'songs': [{
                'id': 11,
                'name': 'Free Song',
                'artists': [{'name': 'Singer'}],
                'fee': 0,
            }],
        },
    }

    with patch.object(
        httpx.AsyncClient,
        'post',
        new=AsyncMock(side_effect=[paid_response, free_response]),
    ) as post:
        results = await crawler.search('test', limit=5)

    assert [track['name'] for track in results] == ['Free Song']
    assert post.await_count == 2
    assert post.await_args_list[1].kwargs['data']['offset'] == 10
    assert post.await_args_list[1].kwargs['data']['limit'] == 90
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_crawler_keeps_primary_results_when_backfill_fails():
    crawler = NeteaseCrawler()
    crawler._vip_checked = True
    primary_response = MagicMock(status_code=200)
    primary_response.json.return_value = {
        'code': 200,
        'result': {
            'songs': [
                {
                    'id': 1,
                    'name': 'Free Song',
                    'artists': [{'name': 'Singer'}],
                    'fee': 0,
                },
                *(
                    {'id': index, 'name': f'Paid {index}', 'fee': 1}
                    for index in range(2, 11)
                ),
            ],
        },
    }

    with patch.object(
        httpx.AsyncClient,
        'post',
        new=AsyncMock(side_effect=[primary_response, httpx.TimeoutException('timeout')]),
    ) as post:
        results = await crawler.search('test', limit=5)

    assert [track['name'] for track in results] == ['Free Song']
    assert post.await_count == 2
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_personalized_recommendations_rotates_all_sources():
    crawler = NeteaseCrawler()
    snapshot = {
        'user_id': 7,
        'liked_tracks': [{'id': 1, 'recommendation_source': 'liked'}],
        'liked_track_ids': {1},
        'playlists': [],
        'subscribed_artists': [],
    }
    daily = [{'id': 2, 'recommendation_source': 'daily'}]
    daily_playlist = [{
        'id': 3,
        'recommendation_source': 'daily_playlist',
    }]
    artist = [{'id': 4, 'recommendation_source': 'artist'}]

    with (
        patch.object(crawler, 'get_taste_snapshot', new=AsyncMock(return_value=snapshot)),
        patch.object(
            crawler,
            'get_daily_recommendations',
            new=AsyncMock(side_effect=lambda *_: [dict(item) for item in daily]),
        ),
        patch.object(
            crawler,
            'get_daily_playlist_recommendations',
            new=AsyncMock(
                side_effect=lambda *_: [dict(item) for item in daily_playlist]
            ),
        ),
        patch.object(
            crawler,
            '_fetch_exploration_tracks',
            new=AsyncMock(side_effect=lambda *_: [dict(item) for item in artist]),
        ),
        patch('utils.music_crawlers.random.shuffle', side_effect=lambda items: None),
    ):
        sources = [
            (await crawler.personalized_recommendations('', limit=1))[0][
                'recommendation_source'
            ]
            for _ in range(6)
        ]

    assert sources == [
        'liked', 'daily_playlist', 'liked', 'daily', 'liked', 'artist',
    ]
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_personalized_recommendations_rotates_to_artist_lead():
    crawler = NeteaseCrawler()
    familiar = [
        {
            'id': index,
            'name': f'Familiar {index}',
            'artist': 'Known Artist',
            'url': f'/api/music/play/netease/{index}',
            'cover': 'cover',
            'theme': '#44b7fe',
        }
        for index in range(1, 6)
    ]
    snapshot = {
        'user_id': 7,
        'liked_tracks': familiar,
        'liked_track_ids': {item['id'] for item in familiar},
        'playlists': [],
        'subscribed_artists': [{'id': 99, 'name': 'Favorite Artist'}],
    }
    exploration = [{
        'id': 100,
        'name': 'Exploration',
        'artist': 'Favorite Artist',
        'url': '/api/music/play/netease/100',
        'cover': 'cover',
        'theme': '#44b7fe',
    }]

    with (
        patch.object(crawler, 'get_taste_snapshot', new=AsyncMock(return_value=snapshot)),
        patch.object(crawler, 'get_daily_recommendations', new=AsyncMock(return_value=[])),
        patch.object(
            crawler,
            'get_daily_playlist_recommendations',
            new=AsyncMock(return_value=[]),
        ),
        patch.object(crawler, '_fetch_exploration_tracks', new=AsyncMock(return_value=exploration)),
        patch('utils.music_crawlers.random.shuffle', side_effect=lambda items: None),
    ):
        crawler._personalization_source_index = 5
        results = await crawler.personalized_recommendations('', limit=5)

    assert len(results) == 5
    assert results[0]['id'] == 100
    assert sum(item['id'] < 100 for item in results) == 4
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_personalized_recommendations_prioritizes_artist_hint():
    crawler = NeteaseCrawler()
    snapshot = {
        'user_id': 7,
        'liked_tracks': [{
            'id': 1,
            'name': 'Familiar',
            'artist': 'Known Artist',
            'url': '/api/music/play/netease/1',
        }],
        'liked_track_ids': {1},
        'playlists': [],
        'subscribed_artists': [{'id': 99, 'name': 'Favorite Artist'}],
    }
    exploration = [{
        'id': 100,
        'name': 'Exploration',
        'artist': 'Favorite Artist',
        'url': '/api/music/play/netease/100',
    }]

    with (
        patch.object(crawler, 'get_taste_snapshot', new=AsyncMock(return_value=snapshot)),
        patch.object(crawler, 'get_daily_recommendations', new=AsyncMock(return_value=[])),
        patch.object(
            crawler,
            'get_daily_playlist_recommendations',
            new=AsyncMock(return_value=[]),
        ),
        patch.object(crawler, '_fetch_exploration_tracks', new=AsyncMock(return_value=exploration)),
        patch('utils.music_crawlers.random.shuffle', side_effect=lambda items: None),
    ):
        results = await crawler.personalized_recommendations('Favorite Artist', limit=1)

    assert [item['id'] for item in results] == [100]
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_personalized_recommendations_backfill_from_liked_pool():
    crawler = NeteaseCrawler()
    familiar = [
        {
            'id': index,
            'name': f'Familiar {index}',
            'artist': 'Known Artist',
            'url': f'/api/music/play/netease/{index}',
            'cover': 'cover',
            'theme': '#44b7fe',
        }
        for index in range(1, 7)
    ]
    snapshot = {
        'user_id': 7,
        'liked_tracks': familiar,
        'liked_track_ids': {item['id'] for item in familiar},
        'playlists': [],
        'subscribed_artists': [],
    }

    with (
        patch.object(crawler, 'get_taste_snapshot', new=AsyncMock(return_value=snapshot)),
        patch.object(crawler, 'get_daily_recommendations', new=AsyncMock(return_value=[])),
        patch.object(
            crawler,
            'get_daily_playlist_recommendations',
            new=AsyncMock(return_value=[]),
        ),
        patch.object(crawler, '_fetch_exploration_tracks', new=AsyncMock(return_value=[])) as explore,
        patch('utils.music_crawlers.random.shuffle', side_effect=lambda items: None),
    ):
        results = await crawler.personalized_recommendations('', limit=5)

    assert [item['id'] for item in results] == [1, 2, 3, 4, 5]
    explore.assert_awaited_once_with(snapshot, '', 5)
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_personalized_recommendations_can_restrict_to_liked():
    crawler = NeteaseCrawler()
    liked = [{
        'id': 1,
        'name': 'Liked Song',
        'artist': 'Known Artist',
        'url': '/api/music/play/netease/1',
        'recommendation_source': 'liked',
    }]
    playlists = [{'id': 55, 'name': 'Liked', 'special_type': 5}]

    with (
        patch.object(
            crawler,
            '_get_personalization_user_id',
            new=AsyncMock(return_value=7),
        ),
        patch.object(
            crawler,
            '_fetch_visible_playlists',
            new=AsyncMock(return_value=playlists),
        ),
        patch.object(
            crawler,
            '_fetch_playlist_tracks',
            new=AsyncMock(return_value=liked),
        ),
        patch.object(crawler, 'get_taste_snapshot', new=AsyncMock()) as snapshot,
        patch.object(crawler, 'get_daily_recommendations', new=AsyncMock()) as daily,
        patch.object(
            crawler,
            'get_daily_playlist_recommendations',
            new=AsyncMock(),
        ) as daily_playlist,
        patch.object(crawler, '_fetch_exploration_tracks', new=AsyncMock()) as artist,
        patch('utils.music_crawlers.random.shuffle', side_effect=lambda items: None),
    ):
        results = await crawler.personalized_recommendations(
            limit=5,
            personalization_source='liked',
        )

    assert results == liked
    snapshot.assert_not_awaited()
    daily.assert_not_awaited()
    daily_playlist.assert_not_awaited()
    artist.assert_not_awaited()
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_personalized_recommendations_can_restrict_to_daily():
    crawler = NeteaseCrawler()
    daily_tracks = [{
        'id': 2,
        'name': 'Daily Song',
        'artist': 'Daily Artist',
        'url': '/api/music/play/netease/2',
        'recommendation_source': 'daily',
    }]
    daily_playlist_tracks = [
        dict(daily_tracks[0], recommendation_source='daily_playlist'),
        {
            'id': 3,
            'name': 'Daily Playlist Song',
            'artist': 'Playlist Artist',
            'url': '/api/music/play/netease/3',
            'recommendation_source': 'daily_playlist',
        },
    ]
    with (
        patch.object(
            crawler,
            '_get_personalization_user_id',
            new=AsyncMock(return_value=7),
        ),
        patch.object(
            crawler,
            'get_daily_recommendations',
            new=AsyncMock(return_value=daily_tracks),
        ),
        patch.object(
            crawler,
            'get_daily_playlist_recommendations',
            new=AsyncMock(return_value=daily_playlist_tracks),
        ),
        patch.object(crawler, 'get_taste_snapshot', new=AsyncMock()) as snapshot,
        patch.object(crawler, '_fetch_exploration_tracks', new=AsyncMock()) as artist,
        patch('utils.music_crawlers.random.shuffle', side_effect=lambda items: None),
    ):
        results = await crawler.personalized_recommendations(
            limit=5,
            personalization_source='daily',
        )

    assert [item['id'] for item in results] == [2, 3]
    snapshot.assert_not_awaited()
    artist.assert_not_awaited()
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_daily_source_survives_playlist_failure():
    crawler = NeteaseCrawler()
    daily_tracks = [{
        'id': 2,
        'name': 'Daily Song',
        'recommendation_source': 'daily',
    }]

    with (
        patch.object(
            crawler,
            '_get_personalization_user_id',
            new=AsyncMock(return_value=7),
        ),
        patch.object(
            crawler,
            'get_daily_recommendations',
            new=AsyncMock(return_value=daily_tracks),
        ),
        patch('pyncm_async.apis.WeapiCryptoRequest', side_effect=lambda func: func),
        patch.object(
            crawler,
            '_personalization_api_call',
            new=AsyncMock(side_effect=RuntimeError('upstream unavailable')),
        ),
        patch('utils.music_crawlers.random.shuffle', side_effect=lambda items: None),
    ):
        results = await crawler.personalized_recommendations(
            limit=5,
            personalization_source='daily',
        )

    assert results == daily_tracks
    assert crawler._personalization_error_code == ''
    await crawler.close()


@pytest.mark.unit
def test_requested_song_accepts_one_character_typo_and_checks_artist():
    candidates = [
        {'name': '淋雨一直走', 'artist': '张韶涵', 'url': 'correct'},
        {'name': '淋雨一起走', 'artist': 'Other Artist', 'url': 'wrong'},
    ]

    assert _select_requested_song('淋雨一起走', '张韶涵', candidates) == candidates[0]
    assert _select_requested_song('淋雨一起走', '不存在的歌手', candidates) is None


@pytest.mark.unit
def test_requested_song_combines_title_and_artist_similarity():
    requested = {
        'name': '丑马（翻自 乐正绫）',
        'artist': 'Akie秋绘',
        'url': 'correct',
    }
    unrelated = {
        'name': '我很丑，可是我很温柔',
        'artist': '赵传',
        'url': 'wrong',
    }

    assert _select_requested_song('丑马', '秋绘', [unrelated, requested]) == requested
    assert _select_requested_song('丑马', '秋绘', [unrelated]) is None


@pytest.mark.unit
def test_requested_song_rejects_single_character_artist_substring():
    candidate = {'name': 'Hello', 'artist': 'A', 'url': 'wrong'}

    assert _select_requested_song('Hello', 'Adele', [candidate]) is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_taste_snapshot_failure_uses_retry_cooldown():
    crawler = NeteaseCrawler()
    crawler._has_cookies = True

    with (
        patch.object(crawler, '_check_cookie_freshness'),
        patch.object(
            crawler,
            '_build_taste_snapshot',
            new=AsyncMock(side_effect=RuntimeError('rate limited')),
        ) as build_snapshot,
    ):
        assert await crawler.get_taste_snapshot() is None
        assert await crawler.get_taste_snapshot() is None

    assert build_snapshot.await_count == 1
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_exploration_tracks_are_cached():
    crawler = NeteaseCrawler()
    snapshot = {
        'subscribed_artists': [{'id': 99, 'name': 'Favorite Artist'}],
        'liked_track_ids': set(),
    }
    payload = {
        'code': 200,
        'songs': [{
            'id': 100,
            'name': 'Exploration',
            'ar': [{'name': 'Favorite Artist'}],
            'al': {},
            'dt': 180000,
            'fee': 0,
        }],
    }

    with (
        patch.object(
            crawler,
            '_personalization_api_call',
            new=AsyncMock(return_value=payload),
        ) as api_call,
        patch('utils.music_crawlers.random.shuffle', side_effect=lambda items: None),
    ):
        first = await crawler._fetch_exploration_tracks(snapshot, '', 1)
        second = await crawler._fetch_exploration_tracks(snapshot, '', 1)

    assert [item['id'] for item in first] == [100]
    assert [item['id'] for item in second] == [100]
    assert api_call.await_count == 1
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_exploration_cache_is_scoped_by_keyword():
    crawler = NeteaseCrawler()
    snapshot = {
        'subscribed_artists': [
            {'id': 1, 'name': 'First Artist'},
            {'id': 2, 'name': 'Second Artist'},
        ],
        'liked_track_ids': set(),
    }

    def payload(track_id, artist):
        return {
            'code': 200,
            'songs': [{
                'id': track_id,
                'name': f'{artist} Track',
                'ar': [{'name': artist}],
                'al': {},
                'dt': 180000,
                'fee': 0,
            }],
        }

    with (
        patch.object(
            crawler,
            '_personalization_api_call',
            new=AsyncMock(side_effect=[
                payload(101, 'First Artist'),
                payload(202, 'Second Artist'),
            ]),
        ) as api_call,
        patch('utils.music_crawlers.random.shuffle', side_effect=lambda items: None),
    ):
        first = await crawler._fetch_exploration_tracks(snapshot, 'First Artist', 1)
        second = await crawler._fetch_exploration_tracks(snapshot, 'Second Artist', 1)
        second_cached = await crawler._fetch_exploration_tracks(snapshot, 'Second Artist', 1)

    assert [item['id'] for item in first] == [101]
    assert [item['id'] for item in second] == [202]
    assert [item['id'] for item in second_cached] == [202]
    assert api_call.await_count == 2
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_daily_recommendations_are_requested_once_per_day():
    crawler = NeteaseCrawler()
    payload = {
        'code': 200,
        'data': {
            'dailySongs': [{
                'id': 201,
                'name': 'Daily Song',
                'ar': [{'name': 'Daily Artist'}],
                'al': {},
                'dt': 180000,
                'fee': 0,
            }],
        },
    }

    with (
        patch('pyncm_async.apis.WeapiCryptoRequest', side_effect=lambda func: func),
        patch.object(
            crawler,
            '_personalization_api_call',
            new=AsyncMock(return_value=payload),
        ) as api_call,
    ):
        first = await crawler.get_daily_recommendations(7)
        second = await crawler.get_daily_recommendations(7)

    assert first == second
    assert first[0]['recommendation_source'] == 'daily'
    assert api_call.await_count == 1
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_daily_playlist_uses_first_playable_playlist_once_per_day():
    crawler = NeteaseCrawler()
    payload = {
        'code': 200,
        'recommend': [
            {'name': 'Missing ID'},
            {'id': 88, 'name': 'Daily Mix'},
            {'id': 99, 'name': 'Later Mix'},
        ],
    }
    playlist_tracks = [
        {'id': index, 'name': f'Track {index}'}
        for index in range(1, 7)
    ]

    async def fetch_playlist_tracks(playlist_id):
        return playlist_tracks if playlist_id == 99 else []

    async def request_daily_playlists(call):
        assert call() == ('/api/v1/discovery/recommend/resource', {})
        return payload

    with (
        patch('pyncm_async.apis.WeapiCryptoRequest', side_effect=lambda func: func),
        patch.object(
            crawler,
            '_personalization_api_call',
            new=AsyncMock(side_effect=request_daily_playlists),
        ) as api_call,
        patch.object(
            crawler,
            '_fetch_playlist_tracks',
            new=AsyncMock(side_effect=fetch_playlist_tracks),
        ) as fetch_playlist,
    ):
        first = await crawler.get_daily_playlist_recommendations(7)
        second = await crawler.get_daily_playlist_recommendations(7)

    assert first == second
    assert [item['id'] for item in first] == [1, 2, 3, 4, 5]
    assert all(item['recommendation_source'] == 'daily_playlist' for item in first)
    assert all(item['playlist_id'] == 99 for item in first)
    assert all(item['playlist_name'] == 'Later Mix' for item in first)
    assert api_call.await_count == 1
    assert [item.args for item in fetch_playlist.await_args_list] == [(88,), (99,)]
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_empty_daily_playlists_retry_after_cooldown():
    crawler = NeteaseCrawler()
    payload = {'code': 200, 'recommend': [{'id': 88, 'name': 'Daily Mix'}]}

    with (
        patch('pyncm_async.apis.WeapiCryptoRequest', side_effect=lambda func: func),
        patch.object(
            crawler,
            '_personalization_api_call',
            new=AsyncMock(return_value=payload),
        ) as api_call,
        patch.object(
            crawler,
            '_fetch_playlist_tracks',
            new=AsyncMock(return_value=[]),
        ) as fetch_playlist,
    ):
        first = await crawler.get_daily_playlist_recommendations(7)
        during_cooldown = await crawler.get_daily_playlist_recommendations(7)
        crawler._daily_playlist_retry_after = 0.0
        after_cooldown = await crawler.get_daily_playlist_recommendations(7)

    assert first == during_cooldown == after_cooldown == []
    assert crawler._daily_playlist_date == ''
    assert crawler._daily_playlist_error_code == 'source_empty'
    assert api_call.await_count == 2
    assert fetch_playlist.await_count == 2
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_daily_playlist_skips_failed_candidate():
    crawler = NeteaseCrawler()
    payload = {
        'code': 200,
        'recommend': [
            {'id': 88, 'name': 'Broken Mix'},
            {'id': 99, 'name': 'Working Mix'},
        ],
    }
    playlist_tracks = [{'id': 1, 'name': 'Playable'}]

    async def fetch_playlist_tracks(playlist_id):
        if playlist_id == 88:
            raise RuntimeError('upstream failed')
        return playlist_tracks

    with (
        patch('pyncm_async.apis.WeapiCryptoRequest', side_effect=lambda func: func),
        patch.object(
            crawler,
            '_personalization_api_call',
            new=AsyncMock(return_value=payload),
        ),
        patch.object(
            crawler,
            '_fetch_playlist_tracks',
            new=AsyncMock(side_effect=fetch_playlist_tracks),
        ) as fetch_playlist,
    ):
        results = await crawler.get_daily_playlist_recommendations(7)

    assert [item['id'] for item in results] == [1]
    assert results[0]['playlist_id'] == 99
    assert [item.args for item in fetch_playlist.await_args_list] == [(88,), (99,)]
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_visible_playlists_are_cached():
    crawler = NeteaseCrawler()
    payload = {
        'code': 200,
        'playlist': [
            {'id': 88, 'name': 'Night Loop', 'specialType': 0},
        ],
    }

    with (
        patch.object(
            crawler,
            '_personalization_api_call',
            new=AsyncMock(return_value=payload),
        ) as api_call,
    ):
        first = await crawler._fetch_visible_playlists(7)
        second = await crawler._fetch_visible_playlists(7)

    assert first == second == [
        {'id': 88, 'name': 'Night Loop', 'special_type': 0},
    ]
    assert api_call.await_count == 1
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_taste_snapshot_uses_special_type_five_as_liked_playlist():
    crawler = NeteaseCrawler()
    crawler._has_cookies = True
    crawler._vip_checked = True
    crawler._account_profile = {'user_id': 7, 'nickname': 'Tester'}
    liked_tracks = [{
        'id': 501,
        'name': 'Liked Song',
        'artist': 'Artist',
        'url': '/api/music/play/netease/501',
        'cover': '',
        'theme': '#44b7fe',
    }]

    with (
        patch.object(
            crawler,
            '_fetch_visible_playlists',
            new=AsyncMock(return_value=[
                {'id': 88, 'name': 'Other', 'special_type': 0},
                {'id': 55, 'name': 'Liked Songs', 'special_type': 5},
            ]),
        ),
        patch.object(
            crawler,
            '_fetch_playlist_tracks',
            new=AsyncMock(return_value=liked_tracks),
        ) as fetch_playlist,
        patch.object(
            crawler,
            '_personalization_api_call',
            new=AsyncMock(return_value={'data': []}),
        ),
    ):
        snapshot = await crawler._build_taste_snapshot()

    assert snapshot['liked_playlist_id'] == 55
    assert snapshot['liked_tracks'][0]['recommendation_source'] == 'liked'
    fetch_playlist.assert_awaited_once_with(55)
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_named_playlist_restricts_personalized_results():
    crawler = NeteaseCrawler()
    playlists = [{'id': 88, 'name': 'Night Loop'}]
    playlist_tracks = [{
        'id': 301,
        'name': 'Playlist Song',
        'artist': 'Artist',
        'url': '/api/music/play/netease/301',
        'cover': '',
        'theme': '#44b7fe',
    }]

    with (
        patch.object(
            crawler,
            '_get_personalization_user_id',
            new=AsyncMock(return_value=7),
        ),
        patch.object(
            crawler,
            '_fetch_visible_playlists',
            new=AsyncMock(return_value=playlists),
        ),
        patch.object(
            crawler,
            '_fetch_playlist_tracks',
            new=AsyncMock(return_value=playlist_tracks),
        ) as fetch_playlist,
        patch.object(crawler, 'get_taste_snapshot', new=AsyncMock()) as snapshot,
        patch('utils.music_crawlers.random.shuffle', side_effect=lambda items: None),
    ):
        results = await crawler.personalized_recommendations(
            limit=5,
            playlist_name='Night Loop',
        )

    assert [item['id'] for item in results] == [301]
    assert results[0]['playlist_id'] == 88
    assert results[0]['recommendation_source'] == 'playlist'
    fetch_playlist.assert_awaited_once_with(88)
    snapshot.assert_not_awaited()
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_unknown_named_playlist_uses_daily_playlist_fallback():
    crawler = NeteaseCrawler()
    daily_tracks = [{
        'id': 302,
        'name': 'Daily Playlist Song',
        'artist': 'Artist',
        'url': '/api/music/play/netease/302',
        'recommendation_source': 'daily_playlist',
    }]

    with (
        patch.object(
            crawler,
            '_get_personalization_user_id',
            new=AsyncMock(return_value=7),
        ),
        patch.object(
            crawler,
            '_fetch_visible_playlists',
            new=AsyncMock(return_value=[{'id': 11, 'name': '其他歌单'}]),
        ) as visible_playlists,
        patch.object(
            crawler,
            'get_daily_playlist_recommendations',
            new=AsyncMock(return_value=daily_tracks),
        ) as daily_playlist,
        patch.object(
            crawler,
            '_fetch_playlist_tracks',
            new=AsyncMock(),
        ) as fetch_playlist,
    ):
        results = await crawler.personalized_recommendations(
            limit=5,
            playlist_name='夜间循环',
        )

    assert results == daily_tracks
    visible_playlists.assert_awaited_once_with(7)
    daily_playlist.assert_awaited_once_with(7)
    fetch_playlist.assert_not_awaited()
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_netease_ambiguous_named_playlist_does_not_use_daily_fallback():
    crawler = NeteaseCrawler()

    with (
        patch.object(
            crawler,
            '_get_personalization_user_id',
            new=AsyncMock(return_value=7),
        ),
        patch.object(
            crawler,
            '_fetch_visible_playlists',
            new=AsyncMock(return_value=[
                {'id': 1, 'name': 'Same Name'},
                {'id': 2, 'name': 'Same Name'},
            ]),
        ),
        patch.object(
            crawler,
            'get_daily_playlist_recommendations',
            new=AsyncMock(),
        ) as daily_playlist,
        patch.object(
            crawler,
            '_fetch_playlist_tracks',
            new=AsyncMock(),
        ) as fetch_playlist,
    ):
        results = await crawler.personalized_recommendations(
            limit=5,
            playlist_name='Same Name',
        )

    assert results == []
    assert crawler._personalization_error_code == 'playlist_ambiguous'
    daily_playlist.assert_not_awaited()
    fetch_playlist.assert_not_awaited()
    await crawler.close()


@pytest.mark.unit
def test_netease_playlist_name_must_be_unique():
    snapshot = {
        'playlists': [
            {'id': 1, 'name': 'Same Name'},
            {'id': 2, 'name': 'Same Name'},
        ],
    }

    assert NeteaseCrawler._resolve_playlist(snapshot, None, 'Same Name') is None
    assert NeteaseCrawler._resolve_playlist(snapshot, 2, '')['id'] == 2


@pytest.mark.unit
def test_netease_library_track_normalization():
    track = NeteaseCrawler._normalize_library_track({
        'id': 123,
        'name': 'Library Song',
        'ar': [{'name': 'Artist A'}, {'name': 'Artist B'}],
        'al': {'picUrl': 'https://cover.example/123.jpg'},
        'dt': 240000,
        'fee': 0,
    })

    assert track == {
        'id': 123,
        'name': 'Library Song',
        'artist': 'Artist A / Artist B',
        'url': '/api/music/play/netease/123',
        'cover': 'https://cover.example/123.jpg',
        'theme': '#44b7fe',
        'duration': 240,
        'fee': 0,
    }

    track_without_artist = NeteaseCrawler._normalize_library_track({
        'id': 124,
        'name': 'Instrumental',
        'dt': 180000,
        'fee': 0,
    })
    assert track_without_artist['artist'] == ''

@pytest.mark.unit
@pytest.mark.asyncio
async def test_itunes_crawler_parsing():
    crawler = iTunesCrawler()
    mock_response = MagicMock(status_code=200)
    mock_response.json.return_value = MOCK_ITUNES_JSON
    
    with patch.object(httpx.AsyncClient, 'get', new=AsyncMock(return_value=mock_response)):
        results: List[Dict[str, Any]] = await crawler.search("test", limit=1)
        assert len(results) == 1
        assert results[0]['name'] == "iTunes Song"
        assert results[0]['cover'].endswith("600x600bb.jpg")
    await crawler.close()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_fma_crawler_parsing():
    crawler = FMACrawler()
    mock_response = MagicMock(status_code=200, text=MOCK_FMA_HTML)
    
    with patch.object(httpx.AsyncClient, 'get', new=AsyncMock(return_value=mock_response)):
        results: List[Dict[str, Any]] = await crawler.search("test", limit=1)
        assert len(results) == 1
        assert results[0]['name'] == "FMA Song"
    await crawler.close()

@pytest.mark.unit
@pytest.mark.asyncio
async def test_musopen_crawler_parsing():
    crawler = MusopenCrawler()
    mock_response = MagicMock(status_code=200, text=MOCK_MUSOPEN_HTML)
    
    with patch.object(httpx.AsyncClient, 'get', new=AsyncMock(return_value=mock_response)):
        results: List[Dict[str, Any]] = await crawler.search("Chopin", limit=1)
        assert len(results) == 1
        assert "Test" in results[0]['name']
        assert results[0]['cover'] == "http://musopen.img/1"
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_musopen_crawler_uses_public_player_api():
    crawler = MusopenCrawler()
    search_response = MagicMock(status_code=200)
    search_response.json.return_value = {
        'results': [{'id': 108, 'entity': 'piece', 'title': 'Nocturnes, Op. 9'}],
    }
    recordings_response = MagicMock(status_code=200)
    recordings_response.json.return_value = {
        'results': [{
            'title': 'Nocturne in B flat minor, Op. 9 no. 1',
            'length': 345,
            'fileurl': 'https://dl.musopen.org/recordings/nocturne.mp3',
            'performer': {'name': 'Test Performer'},
        }],
    }

    with patch.object(
        httpx.AsyncClient,
        'get',
        new=AsyncMock(side_effect=[search_response, recordings_response]),
    ):
        results = await crawler.search('Chopin', limit=1)

    assert [track['name'] for track in results] == ['Nocturne in B flat minor, Op. 9 no. 1']
    assert results[0]['artist'] == 'Test Performer'
    assert results[0]['url'] == 'https://dl.musopen.org/recordings/nocturne.mp3'
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_musopen_crawler_falls_back_when_api_recordings_are_unusable():
    crawler = MusopenCrawler()
    search_response = MagicMock(status_code=200)
    search_response.json.return_value = {
        'results': [{'id': 108, 'entity': 'piece', 'title': 'Nocturnes, Op. 9'}],
    }
    recordings_response = MagicMock(status_code=200)
    recordings_response.json.return_value = {
        'results': [{'title': 'Unavailable', 'length': 345, 'fileurl': ''}],
    }
    page_response = MagicMock(status_code=200, text=MOCK_MUSOPEN_HTML)

    with patch.object(
        httpx.AsyncClient,
        'get',
        new=AsyncMock(side_effect=[search_response, recordings_response, page_response]),
    ):
        results = await crawler.search('Chopin', limit=1)

    assert len(results) == 1
    assert 'Test' in results[0]['name']
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_musopen_crawler_falls_back_when_api_search_fails():
    crawler = MusopenCrawler()
    failed_search = MagicMock(status_code=503)
    failed_search.raise_for_status.side_effect = httpx.HTTPStatusError(
        'service unavailable',
        request=httpx.Request('GET', 'https://api.musopen.org/v2/search/'),
        response=httpx.Response(503),
    )
    page_response = MagicMock(status_code=200, text=MOCK_MUSOPEN_HTML)

    with patch.object(
        httpx.AsyncClient,
        'get',
        new=AsyncMock(side_effect=[failed_search, page_response]),
    ):
        results = await crawler.search('Chopin', limit=1)

    assert len(results) == 1
    assert 'Test' in results[0]['name']
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_musopen_crawler_skips_failed_piece_recordings():
    crawler = MusopenCrawler()
    search_response = MagicMock(status_code=200)
    search_response.json.return_value = {
        'results': [
            {'id': 108, 'entity': 'piece', 'title': 'Unavailable Piece'},
            {'id': 109, 'entity': 'piece', 'title': 'Playable Piece'},
        ],
    }
    failed_response = MagicMock(status_code=500)
    failed_response.raise_for_status.side_effect = httpx.HTTPStatusError(
        'server error',
        request=httpx.Request('GET', 'https://api.musopen.org/v2/pieces/108/recordings/'),
        response=httpx.Response(500),
    )
    playable_response = MagicMock(status_code=200)
    playable_response.json.return_value = {
        'results': [{
            'title': 'Playable Recording',
            'length': 345,
            'fileurl': 'https://dl.musopen.org/recordings/playable.mp3',
            'performer': {'name': 'Test Performer'},
        }],
    }

    with patch.object(
        httpx.AsyncClient,
        'get',
        new=AsyncMock(side_effect=[search_response, failed_response, playable_response]),
    ):
        results = await crawler.search('Chopin', limit=1)

    assert [track['name'] for track in results] == ['Playable Recording']
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bandcamp_crawler_uses_autocomplete_when_html_search_is_challenged():
    crawler = BandcampCrawler()
    autocomplete = MagicMock(status_code=200)
    autocomplete.json.return_value = {
        'results': [{
            'type': 'a',
            'url': 'https://artist.bandcamp.comhttps://artist.bandcamp.com/album/test',
        }],
    }
    track_page = MagicMock(
        status_code=200,
        text='''<script data-tralbum='{
            "artist": "Bandcamp Artist",
            "trackinfo": [{
                "title": "Bandcamp Song",
                "duration": 180,
                "file": {"mp3-128": "https://audio.example/song.mp3"}
            }]
        }'></script>''',
    )

    get_mock = AsyncMock(side_effect=[autocomplete, track_page])
    with patch.object(httpx.AsyncClient, 'get', new=get_mock):
        results = await crawler.search('lofi', limit=1)

    assert [track['name'] for track in results] == ['Bandcamp Song']
    assert get_mock.await_args_list[1].args[0] == 'https://artist.bandcamp.com/album/test'
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_bandcamp_crawler_falls_back_when_autocomplete_fails():
    crawler = BandcampCrawler()
    html_search = MagicMock(
        status_code=200,
        text='''<div class="heading">
            <a href="https://artist.bandcamp.com/track/test">Test</a>
        </div>''',
    )
    track_page = MagicMock(
        status_code=200,
        text='''<script data-tralbum='{
            "artist": "Bandcamp Artist",
            "trackinfo": [{
                "title": "Fallback Song",
                "duration": 180,
                "file": {"mp3-128": "https://audio.example/fallback.mp3"}
            }]
        }'></script>''',
    )
    get_mock = AsyncMock(side_effect=[
        httpx.ConnectError(
            'connection failed',
            request=httpx.Request(
                'GET',
                'https://bandcamp.com/api/fuzzysearch/2/app_autocomplete',
            ),
        ),
        html_search,
        track_page,
    ])

    with patch.object(httpx.AsyncClient, 'get', new=get_mock):
        results = await crawler.search('lofi', limit=1)

    assert [track['name'] for track in results] == ['Fallback Song']
    assert get_mock.await_args_list[1].args[0] == 'https://bandcamp.com/search'
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_soundcloud_crawler_token_logic():
    crawler = SoundCloudCrawler()
    
    # 模拟首页 HTML 以提取 JS 链接
    mock_home = MagicMock(status_code=200, text='<script src="test.js"></script>')
    # 模拟 JS 内容以提取 client_id
    mock_js = MagicMock(status_code=200, text='client_id:"12345678901234567890123456789012"')
    
    # 模拟搜索响应
    mock_search = MagicMock(status_code=200)
    mock_search.json.return_value = {"collection": [{"title": "SC Song", "media": {"transcodings": [{
        "url": "http://sc.url/stream",
        "format": {"protocol": "progressive", "mime_type": "audio/mpeg"},
    }]}}]}
    
    # 模拟音频流 URL 响应
    mock_stream = MagicMock(status_code=200)
    mock_stream.json.return_value = {"url": "http://sc.real/audio.mp3"}

    # 按顺序触发不同的 get 请求
    with patch.object(httpx.AsyncClient, 'get', new=AsyncMock(side_effect=[mock_home, mock_js, mock_search, mock_stream])):
        results: List[Dict[str, Any]] = await crawler.search("test", limit=1)
        assert len(results) == 1
        assert results[0]['url'] == "http://sc.real/audio.mp3"
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_soundcloud_crawler_skips_ten_minute_candidates_before_stream_resolution():
    crawler = SoundCloudCrawler()
    crawler.client_id = "a" * 32

    mock_search = MagicMock(status_code=200)
    mock_search.json.return_value = {
        "collection": [
            {
                "title": "Long DJ Set",
                "duration": 10 * 60 * 1000,
                "media": {"transcodings": [{
                    "url": "http://sc.url/long",
                    "format": {"protocol": "progressive", "mime_type": "audio/mpeg"},
                }]},
            },
            {
                "title": "Normal Track",
                "duration": 5 * 60 * 1000,
                "media": {"transcodings": [{
                    "url": "http://sc.url/normal",
                    "format": {"protocol": "progressive", "mime_type": "audio/mpeg"},
                }]},
            },
        ]
    }
    mock_stream = MagicMock(status_code=200)
    mock_stream.json.return_value = {"url": "http://sc.real/normal.mp3"}

    get_mock = AsyncMock(side_effect=[mock_search, mock_stream])
    with patch.object(httpx.AsyncClient, 'get', new=get_mock):
        results = await crawler.search("test", limit=1)

    assert [track['name'] for track in results] == ["Normal Track"]
    assert results[0]['duration'] == 5 * 60
    assert get_mock.await_count == 2
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_soundcloud_crawler_prefers_progressive_mp3_over_hls():
    crawler = SoundCloudCrawler()
    crawler.client_id = "a" * 32

    mock_search = MagicMock(status_code=200)
    mock_search.json.return_value = {
        "collection": [{
            "title": "Playable Track",
            "duration": 3 * 60 * 1000,
            "media": {"transcodings": [
                {
                    "url": "https://api.soundcloud.com/hls",
                    "format": {"protocol": "hls", "mime_type": "audio/mpeg"},
                },
                {
                    "url": "https://api.soundcloud.com/progressive",
                    "format": {"protocol": "progressive", "mime_type": "audio/mpeg"},
                },
            ]},
        }]}
    mock_stream = MagicMock(status_code=200)
    mock_stream.json.return_value = {"url": "https://cf-media.sndcdn.com/track.mp3"}

    get_mock = AsyncMock(side_effect=[mock_search, mock_stream])
    with patch.object(httpx.AsyncClient, 'get', new=get_mock):
        results = await crawler.search("test", limit=1)

    assert [track['name'] for track in results] == ["Playable Track"]
    assert "progressive" in str(get_mock.await_args_list[1].args[0])
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_soundcloud_crawler_rejects_hls_only_candidates():
    crawler = SoundCloudCrawler()
    crawler.client_id = "a" * 32

    mock_search = MagicMock(status_code=200)
    mock_search.json.return_value = {
        "collection": [{
            "title": "Encrypted HLS Track",
            "duration": 3 * 60 * 1000,
            "media": {"transcodings": [{
                "url": "https://api.soundcloud.com/hls",
                "format": {"protocol": "hls", "mime_type": "audio/mp4"},
            }]},
        }]}
    get_mock = AsyncMock(return_value=mock_search)

    with patch.object(httpx.AsyncClient, 'get', new=get_mock):
        results = await crawler.search("test", limit=1)

    assert results == []
    assert get_mock.await_count == 1
    await crawler.close()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_soundcloud_crawler_rejects_progressive_endpoint_resolving_to_hls():
    crawler = SoundCloudCrawler()
    crawler.client_id = "a" * 32

    mock_search = MagicMock(status_code=200)
    mock_search.json.return_value = {
        "collection": [{
            "title": "Misreported Stream",
            "duration": 3 * 60 * 1000,
            "media": {"transcodings": [{
                "url": "https://api.soundcloud.com/progressive",
                "format": {"protocol": "progressive", "mime_type": "audio/mpeg"},
            }]},
        }]}
    mock_stream = MagicMock(status_code=200)
    mock_stream.json.return_value = {
        "url": "https://playback.media-streaming.soundcloud.cloud/cbcs/track/playlist.m3u8?Policy=x"
    }
    get_mock = AsyncMock(side_effect=[mock_search, mock_stream])

    with patch.object(httpx.AsyncClient, 'get', new=get_mock):
        results = await crawler.search("test", limit=1)

    assert results == []
    await crawler.close()

# ==========================================
# 3. 调度逻辑测试
# ==========================================

@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_music_content_orchestration():
    """验证主调度函数是否根据不同参数正确聚合结果 (Mocked Crawlers)"""
    
    # 使用更健壮的 Mock 策略：直接 Patch 工厂加载器，避免单例缓存 _crawlers_cache 污染
    mock_netease = MagicMock()
    mock_itunes = MagicMock()
    
    # 定义异步 Mock 返回
    async def mock_netease_search(*args, **kwargs):
        return [{"name": "Mock Netease", "url": "url1", "artist": "A1"}]
        
    async def mock_itunes_search(*args, **kwargs):
        return [{"name": "Mock iTunes", "url": "url2", "artist": "A2"}]

    mock_netease.search = mock_netease_search
    mock_itunes.search = mock_itunes_search
    
    async def mock_close(*args, **kwargs):
        pass

    mock_netease.close = mock_close
    mock_itunes.close = mock_close
    
    with patch('utils.music_crawlers.get_music_crawlers', return_value={'netease': mock_netease, 'itunes': mock_itunes}):
        with patch('utils.music_crawlers.is_china_region', return_value=True):
            # 在中国区域，应该包含网易云
            response = await fetch_music_content("keyword", limit=1)
            assert response['success'] is True
            assert any(r['name'] == "Mock Netease" for r in response['data'])


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_music_content_tries_qqmusic_before_open_fallbacks():
    """QQ Music is the first Chinese fallback, not one more race participant."""
    mock_netease = MagicMock()
    mock_netease._cookie_invalid = False
    mock_netease.search = AsyncMock(return_value=[])
    mock_qqmusic = MagicMock()
    mock_qqmusic.search = AsyncMock(return_value=[{
        'name': 'QQ Fallback',
        'artist': 'QQ Artist',
        'url': 'https://dl.stream.qqmusic.qq.com/free-track.m4a',
        'duration': 180,
    }])
    mock_fma = MagicMock()
    mock_fma.search = AsyncMock(return_value=[])
    mock_soundcloud = MagicMock()
    mock_soundcloud.search = AsyncMock(return_value=[])
    mock_bandcamp = MagicMock()
    mock_bandcamp.search = AsyncMock(return_value=[])

    with (
        patch(
            'utils.music_crawlers.get_music_crawlers',
            return_value={
                'netease': mock_netease,
                'qqmusic': mock_qqmusic,
                'fma': mock_fma,
                'soundcloud': mock_soundcloud,
                'bandcamp': mock_bandcamp,
            },
        ),
        patch('utils.music_crawlers.is_china_region', return_value=True),
    ):
        response = await fetch_music_content(
            'fallback query', limit=1, bypass_recommendation_dedupe=True,
        )

    assert response['success'] is True
    assert response['data'][0]['name'] == 'QQ Fallback'
    mock_qqmusic.search.assert_awaited_once_with('fallback query', 1)
    mock_fma.search.assert_not_awaited()
    mock_soundcloud.search.assert_not_awaited()
    mock_bandcamp.search.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_music_content_skips_qqmusic_fallback_outside_china():
    """Global searches keep the existing open-source fallback behavior."""
    mock_netease = MagicMock()
    mock_netease._cookie_invalid = False
    mock_netease.search = AsyncMock(return_value=[])
    mock_qqmusic = MagicMock()
    mock_qqmusic.search = AsyncMock(return_value=[])
    mock_itunes = MagicMock()
    mock_itunes.search = AsyncMock(return_value=[])
    mock_fma = MagicMock()
    mock_fma.search = AsyncMock(return_value=[])
    mock_soundcloud = MagicMock()
    mock_soundcloud.search = AsyncMock(return_value=[])
    mock_bandcamp = MagicMock()
    mock_bandcamp.search = AsyncMock(return_value=[])

    with (
        patch(
            'utils.music_crawlers.get_music_crawlers',
            return_value={
                'netease': mock_netease,
                'qqmusic': mock_qqmusic,
                'itunes': mock_itunes,
                'fma': mock_fma,
                'soundcloud': mock_soundcloud,
                'bandcamp': mock_bandcamp,
            },
        ),
        patch('utils.music_crawlers.source_region_from_locale', return_value='non-china'),
    ):
        await fetch_music_content(
            'unmatched global query',
            limit=1,
            source_locale='en-US',
            bypass_recommendation_dedupe=True,
        )

    mock_qqmusic.search.assert_not_awaited()
    mock_fma.search.assert_awaited_once_with('unmatched global query', 1)
    mock_bandcamp.search.assert_awaited_once_with('unmatched global query', 1)


@pytest.mark.unit
def test_background_music_samples_distinct_providers():
    style_options = [
        ('netease', '华语'),
        ('netease', '流行'),
        ('musopen', None),
        ('fma', 'lofi'),
    ]

    with (
        patch(
            'utils.music_crawlers.random.sample',
            side_effect=lambda population, count: population[:count],
        ),
        patch(
            'utils.music_crawlers.random.choice',
            side_effect=lambda choices: choices[0],
        ),
    ):
        selected = _sample_distinct_background_sources(style_options)

    assert selected == [
        ('netease', '华语'),
        ('musopen', None),
        ('fma', 'lofi'),
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_background_music_interleaves_distinct_provider_fallbacks():
    """The first playback candidates must not all fail with one provider."""
    mock_musopen = MagicMock()
    mock_musopen.search = AsyncMock(return_value=[
        {'name': 'Musopen 1', 'url': 'https://dl.musopen.org/m1.mp3', 'artist': 'M'},
        {'name': 'Musopen 2', 'url': 'https://dl.musopen.org/m2.mp3', 'artist': 'M'},
        {'name': 'Musopen 3', 'url': 'https://dl.musopen.org/m3.mp3', 'artist': 'M'},
    ])
    mock_netease = MagicMock()
    mock_netease._cookie_invalid = False
    mock_netease.search = AsyncMock(return_value=[
        {'name': 'Netease 1', 'url': '/api/music/play/netease/n1', 'artist': 'N1'},
        {'name': 'Netease 2', 'url': '/api/music/play/netease/n2', 'artist': 'N2'},
        {'name': 'Netease 3', 'url': '/api/music/play/netease/n3', 'artist': 'N3'},
    ])
    mock_fma = MagicMock()
    mock_fma.search = AsyncMock(return_value=[
        {'name': 'FMA 1', 'url': 'https://freemusicarchive.org/f1.mp3', 'artist': 'F1'},
        {'name': 'FMA 2', 'url': 'https://freemusicarchive.org/f2.mp3', 'artist': 'F2'},
        {'name': 'FMA 3', 'url': 'https://freemusicarchive.org/f3.mp3', 'artist': 'F3'},
    ])

    with (
        patch(
            'utils.music_crawlers.get_music_crawlers',
            return_value={
                'musopen': mock_musopen,
                'netease': mock_netease,
                'fma': mock_fma,
            },
        ),
        patch('utils.music_crawlers.is_china_region', return_value=True),
        patch(
            'utils.music_crawlers._sample_distinct_background_sources',
            return_value=[
                ('musopen', None),
                ('netease', '流行'),
                ('fma', 'chill'),
            ],
        ),
    ):
        response = await fetch_music_content(
            '',
            limit=5,
            bypass_recommendation_dedupe=True,
        )

    assert response['success'] is True
    assert [item['name'] for item in response['data']] == [
        'Musopen 1',
        'Netease 1',
        'FMA 1',
        'Musopen 2',
        'Netease 2',
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_music_content_propagates_cancellation_to_primary_search():
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def blocked_search(*args, **kwargs):
        started.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    mock_netease = MagicMock()
    mock_netease.search = blocked_search

    with (
        patch(
            'utils.music_crawlers.get_music_crawlers',
            return_value={'netease': mock_netease},
        ),
        patch('utils.music_crawlers.is_china_region', return_value=True),
    ):
        task = asyncio.create_task(fetch_music_content("keyword", limit=1))
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert cancelled.is_set()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_music_content_strict_liked_source_does_not_blind_fallback():
    mock_netease = MagicMock()
    mock_netease._cookie_invalid = False
    mock_netease.personalized_recommendations = AsyncMock(return_value=[])

    with patch(
        'utils.music_crawlers.get_music_crawlers',
        return_value={'netease': mock_netease},
    ):
        response = await fetch_music_content(
            "",
            limit=5,
            personalized=True,
            personalization_source='liked',
        )

    assert response['success'] is False
    mock_netease.personalized_recommendations.assert_awaited_once_with(
        keyword='',
        limit=5,
        playlist_id=None,
        playlist_name='',
        personalization_source='liked',
    )


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize(
    'strict_kwargs',
    [
        {'personalization_source': 'liked'},
        {'playlist_id': 88},
    ],
)
async def test_fetch_music_content_strict_personalization_with_keyword_does_not_fallback(
    strict_kwargs,
):
    mock_netease = MagicMock()
    mock_netease._cookie_invalid = False
    mock_netease._personalization_error_code = 'source_empty'
    mock_netease.personalized_recommendations = AsyncMock(return_value=[])
    mock_netease.search = AsyncMock(return_value=[{
        'name': 'Public Track',
        'artist': 'Public Artist',
        'url': '/api/music/play/netease/9',
    }])

    with (
        patch(
            'utils.music_crawlers.get_music_crawlers',
            return_value={'netease': mock_netease},
        ),
        patch('utils.music_crawlers.is_china_region', return_value=True),
    ):
        response = await fetch_music_content(
            '周杰伦',
            limit=5,
            personalized=True,
            **strict_kwargs,
        )

    assert response['success'] is False
    assert response['error_code'] == 'source_empty'
    mock_netease.search.assert_not_awaited()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_music_content_honors_personalized_keyword_request():
    mock_netease = MagicMock()
    mock_netease._cookie_invalid = False
    mock_netease._personalization_error_code = ''
    mock_netease.personalized_recommendations = AsyncMock(return_value=[{
        'name': 'Account Track',
        'artist': 'Favorite Artist',
        'url': '/api/music/play/netease/7',
    }])

    with patch(
        'utils.music_crawlers.get_music_crawlers',
        return_value={'netease': mock_netease},
    ):
        response = await fetch_music_content(
            'Favorite Artist',
            limit=5,
            personalized=True,
            personalization_source='liked',
        )

    assert response['success'] is True
    assert [item['name'] for item in response['data']] == ['Account Track']
    mock_netease.personalized_recommendations.assert_awaited_once_with(
        keyword='Favorite Artist',
        limit=5,
        playlist_id=None,
        playlist_name='',
        personalization_source='liked',
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ordinary_personalized_keyword_uses_public_search():
    mock_netease = MagicMock()
    mock_netease._cookie_invalid = False
    mock_netease.personalized_recommendations = AsyncMock(return_value=[{
        'name': 'Unrelated Daily Track',
        'artist': 'Other Artist',
        'url': '/api/music/play/netease/daily',
    }])
    mock_netease.search = AsyncMock(return_value=[{
        'name': 'Yellow',
        'artist': 'Coldplay',
        'url': '/api/music/play/netease/yellow',
    }])

    with (
        patch(
            'utils.music_crawlers.get_music_crawlers',
            return_value={'netease': mock_netease},
        ),
        patch('utils.music_crawlers.is_china_region', return_value=True),
    ):
        response = await fetch_music_content(
            'Yellow',
            limit=5,
            personalized=True,
        )

    assert response['success'] is True
    assert [item['name'] for item in response['data']] == ['Yellow']
    mock_netease.personalized_recommendations.assert_not_awaited()
    mock_netease.search.assert_awaited_once_with('Yellow', 5)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_music_content_matches_requested_song_with_typo():
    mock_netease = MagicMock()
    mock_netease._cookie_invalid = False
    mock_netease.search = AsyncMock(return_value=[{
        'name': '淋雨一直走',
        'artist': '张韶涵',
        'url': '/api/music/play/netease/1',
    }])

    with (
        patch(
            'utils.music_crawlers.get_music_crawlers',
            return_value={'netease': mock_netease},
        ),
        patch('utils.music_crawlers.is_china_region', return_value=True),
    ):
        response = await fetch_music_content(
            '淋雨一起走 张韶涵',
            limit=5,
            personalized=True,
            requested_song='淋雨一起走',
            requested_artist='张韶涵',
        )

    assert response['success'] is True
    assert [item['name'] for item in response['data']] == ['淋雨一直走']


@pytest.mark.unit
@pytest.mark.asyncio
async def test_strict_request_waits_for_matching_provider_result():
    unrelated = {
        'name': 'Yellow Submarine',
        'artist': 'The Beatles',
        'url': 'https://soundcloud.example/yellow-submarine',
    }
    requested = {
        'name': 'Yellow',
        'artist': 'Coldplay',
        'url': 'https://itunes.example/yellow',
    }
    mock_soundcloud = MagicMock()
    mock_soundcloud.search = AsyncMock(return_value=[unrelated])
    mock_itunes = MagicMock()

    async def slower_exact_result(*_args, **_kwargs):
        await asyncio.sleep(0.01)
        return [requested]

    mock_itunes.search = AsyncMock(side_effect=slower_exact_result)
    mock_netease = MagicMock()
    mock_netease._cookie_invalid = False

    with (
        patch(
            'utils.music_crawlers.get_music_crawlers',
            return_value={
                'netease': mock_netease,
                'soundcloud': mock_soundcloud,
                'itunes': mock_itunes,
            },
        ),
        patch('utils.music_crawlers.source_region_from_locale', return_value='global'),
    ):
        response = await fetch_music_content(
            'Yellow Coldplay',
            limit=5,
            source_locale='en-US',
            personalized=True,
            requested_song='Yellow',
            requested_artist='Coldplay',
        )

    assert response['success'] is True
    assert response['data'] == [requested]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_explicit_playback_bypasses_recommendation_dedupe():
    track = {
        'name': 'Yellow',
        'artist': 'Coldplay',
        'url': '/api/music/play/netease/yellow',
    }
    music_cache.mark_as_played([track])
    mock_netease = MagicMock()
    mock_netease._cookie_invalid = False
    mock_netease.search = AsyncMock(return_value=[track])

    with (
        patch(
            'utils.music_crawlers.get_music_crawlers',
            return_value={'netease': mock_netease},
        ),
        patch('utils.music_crawlers.is_china_region', return_value=True),
    ):
        response = await fetch_music_content(
            'Yellow',
            limit=5,
            personalized=True,
            requested_song='Yellow',
            bypass_recommendation_dedupe=True,
        )

    assert response['success'] is True
    assert response['data'] == [track]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fetch_music_content_rejects_unrelated_requested_song():
    mock_netease = MagicMock()
    mock_netease._cookie_invalid = False
    mock_netease.search = AsyncMock(return_value=[{
        'name': '我很丑，可是我很温柔',
        'artist': '赵传',
        'url': '/api/music/play/netease/2',
    }])
    empty_fallback = MagicMock()
    empty_fallback.search = AsyncMock(return_value=[])

    with (
        patch(
            'utils.music_crawlers.get_music_crawlers',
            return_value={
                'netease': mock_netease,
                'fma': empty_fallback,
                'soundcloud': empty_fallback,
                'bandcamp': empty_fallback,
            },
        ),
        patch('utils.music_crawlers.is_china_region', return_value=True),
    ):
        response = await fetch_music_content(
            '丑马 秋绘',
            limit=5,
            personalized=True,
            requested_song='丑马',
            requested_artist='秋绘',
        )

    assert response['success'] is False
    assert response['error_code'] == 'track_not_found'


@pytest.mark.manual
@pytest.mark.asyncio
async def test_real_itunes_integration():
    """集成测试：验证真实 iTunes API 连接性"""
    crawler = iTunesCrawler()
    try:
        results: List[Dict[str, Any]] = await crawler.search("lofi", limit=1)
        assert len(results) > 0
        assert "http" in results[0]['url']
    except (httpx.ConnectError, httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError) as e:
        pytest.skip(f"iTunes 集成测试跳过 (网络错误): {e}")
    except Exception as e:
        # 非网络错误（如 AssertionError）应该让测试失败，而不是跳过
        raise e
    finally:
        await crawler.close()

if __name__ == "__main__":
    pytest.main([__file__])
