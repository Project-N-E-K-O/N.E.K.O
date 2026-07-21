import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MUSIC_UI_PATH = ROOT / "static" / "jukebox" / "music_ui.js"
PROACTIVE_UI_PATH = ROOT / "static" / "app" / "app-proactive.js"
APP_CHAT_PATH = ROOT / "static" / "app" / "app-chat.js"
LOCALES_DIR = ROOT / "static" / "locales"
MUSIC_ROUTER_PATH = ROOT / "main_routers" / "music_router.py"


def test_music_dispatch_waits_for_media_and_reports_real_failure():
    source = MUSIC_UI_PATH.read_text(encoding="utf-8")
    dispatch_source = APP_CHAT_PATH.read_text(encoding="utf-8")

    assert "waitForMusicMediaReady" in source
    assert "const accepted = await executePlay(" in source
    assert "return accepted === true" in source
    assert "MAX_RECOMMENDED_TRACK_DURATION_SECONDS = 10 * 60" in source
    assert "duration >= MAX_RECOMMENDED_TRACK_DURATION_SECONDS" in source
    assert "playbackOptions.source === 'proactive'" in source
    assert "sendMusicMessage(trackInfo, true, options)" in dispatch_source
    assert "return new Promise(function (resolve)" in dispatch_source
    assert "finish(accepted)" in dispatch_source
    assert "return 'queued'" not in dispatch_source
    assert "isUnsupportedMusicStream" in source
    assert "endsWith('.m3u8')" in source


def test_proactive_music_retries_candidates_until_one_loads():
    source = PROACTIVE_UI_PATH.read_text(encoding="utf-8")

    assert "for (var musicIndex = 0; musicIndex < musicLinks.length; musicIndex++)" in source
    assert "var dispatchResult = await window.dispatchMusicPlay" in source
    assert "if (dispatchResult === true)" in source
    assert "音乐候选加载失败，尝试下一条" in source
    assert "musicLinks = normalizedLinks.filter" in source
    assert "name: musicLink.title || '未知曲目'" not in source
    assert "artist: musicLink.artist || '未知艺术家'" not in source


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


def test_large_music_stream_does_not_advertise_probe_content_length():
    source = MUSIC_ROUTER_PATH.read_text(encoding="utf-8")
    large_stream_branch = source.split(
        "if declared_size >= STREAMING_SIZE_THRESHOLD:", 1
    )[1].split("# 小文件：", 1)[0]

    assert "headers['Content-Length'] = content_length" not in large_stream_branch
    assert "_stream_music(current_url, request_headers, MAX_MUSIC_SIZE)" in large_stream_branch
