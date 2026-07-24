import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MUSIC_UI_PATH = ROOT / "static" / "jukebox" / "music_ui.js"
PROACTIVE_UI_PATH = ROOT / "static" / "app" / "app-proactive.js"
APP_CHAT_PATH = ROOT / "static" / "app" / "app-chat.js"
LOCALES_DIR = ROOT / "static" / "locales"
MUSIC_ROUTER_PATH = ROOT / "main_routers" / "music_router.py"
MUSIC_CRAWLERS_PATH = ROOT / "utils" / "music_crawlers.py"
DEFAULT_MUSIC_COVER_PATH = ROOT / "static" / "assets" / "music" / "music-cover-placeholder.png"


def test_music_dispatch_waits_for_media_and_reports_real_failure():
    source = MUSIC_UI_PATH.read_text(encoding="utf-8")
    dispatch_source = APP_CHAT_PATH.read_text(encoding="utf-8")

    assert "waitForMusicMediaReady" in source
    assert "const result = await executePlay(" in source
    assert "window.sendMusicMessageDetailed" in source
    assert "window.sendMusicMessage = async function" in source
    assert "return result.ok === true" in source
    assert "canTryNextCandidate" in source
    assert "isPermanentCandidateFailure(mediaResult.reason)" in source
    permanent_failures = source.split("const isPermanentCandidateFailure", 1)[1].split("].includes(reason);", 1)[0]
    assert "'media_error'" in permanent_failures
    assert "'track_too_long'" in permanent_failures
    assert "'load_timeout'" not in permanent_failures
    assert "musicPlayResult(false, 'unsupported_stream', true)" in source
    assert "musicPlayResult(false, 'unsafe_url', true)" in source
    assert "MAX_RECOMMENDED_TRACK_DURATION_SECONDS = 10 * 60" in source
    assert "duration >= MAX_RECOMMENDED_TRACK_DURATION_SECONDS" in source
    assert "playbackOptions.source === 'proactive'" in source
    assert "window.dispatchMusicPlayDetailed" in dispatch_source
    assert "window.dispatchMusicPlay = async function" in dispatch_source
    assert "sendMusicMessageDetailed(trackInfo, true, options)" in dispatch_source
    assert "return new Promise(function (resolve)" in dispatch_source
    assert "musicDispatchResult(false, 'ui_not_ready', false)" in dispatch_source
    assert "result.ok === true && options.source === 'proactive'" in dispatch_source
    assert "return 'queued'" not in dispatch_source
    assert "isUnsupportedMusicStream" in source
    assert "endsWith('.m3u8')" in source
    assert "const backendProxyDomains = new Set(MUSIC_CONFIG.allowlist)" in source
    assert "const toBackendMusicProxyUrl = (url) =>" in source
    assert "trackInfo.url = toBackendMusicProxyUrl(originalUrl)" in source
    assert "trackInfo.url.includes('music.163.com')" not in source


def test_proactive_music_only_retries_permanent_candidate_failures():
    source = PROACTIVE_UI_PATH.read_text(encoding="utf-8")

    assert "for (var musicIndex = 0; musicIndex < musicLinks.length; musicIndex++)" in source
    assert "window.dispatchMusicPlayDetailed(track, { source: 'proactive' })" in source
    assert "if (dispatchResult.ok === true)" in source
    assert "if (dispatchResult.canTryNextCandidate !== true)" in source
    assert "音乐派发因非候选错误停止" in source
    assert "音乐候选存在永久错误，尝试下一条" in source
    assert "musicLinks = normalizedLinks.filter" in source
    assert "name: musicLink.title || '未知曲目'" not in source
    assert "artist: musicLink.artist || '未知艺术家'" not in source


def test_missing_music_cover_stays_out_of_data_and_uses_frontend_placeholder():
    player_source = MUSIC_UI_PATH.read_text(encoding="utf-8")
    crawler_source = MUSIC_CRAWLERS_PATH.read_text(encoding="utf-8")

    assert "'cover': cover or ''" in crawler_source
    assert "dummyimage.com" not in crawler_source
    assert "defaultCoverPath: '/static/assets/music/music-cover-placeholder.png'" in player_source
    assert "const getMusicCoverUrl = (cover) =>" in player_source
    assert "applyMusicCover(coverImg, trackInfo.cover)" in player_source
    assert "thumbnailUrl: displayCoverUrl" in player_source
    assert "music-bar-fallback" not in player_source
    assert "dummyimage.com" not in player_source
    assert DEFAULT_MUSIC_COVER_PATH.stat().st_size > 0


def test_all_locales_define_music_player_labels_and_failures():
    required = {
        "unknownTrack",
        "unknownArtist",
        "unknownSource",
        "volumeControl",
        "closePlayer",
        "trackTooLong",
        "loadTimeout",
        "loading",
        "playError",
        "loadError",
    }

    for locale_path in sorted(LOCALES_DIR.glob("*.json")):
        data = json.loads(locale_path.read_text(encoding="utf-8"))
        assert required <= set(data["music"]), locale_path.name


def test_music_proxy_streams_one_upstream_response_and_tees_small_cache():
    source = MUSIC_ROUTER_PATH.read_text(encoding="utf-8")

    assert "StreamingResponse(" in source
    assert "_stream_music_response(" in source
    assert "async def _stream_music(" not in source
    assert "cache_body = bytearray() if cache_key else None" in source
    assert "if cache_key and cache_body is not None:" in source
