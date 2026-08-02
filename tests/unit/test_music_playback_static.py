import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
MUSIC_UI_PATH = ROOT / "static" / "jukebox" / "music_ui.js"
MUSIC_UI_CSS_PATH = ROOT / "static" / "css" / "music_ui.css"
PROACTIVE_UI_PATH = ROOT / "static" / "app" / "app-proactive.js"
APP_CHAT_PATH = ROOT / "static" / "app" / "app-chat.js"
APP_WEBSOCKET_PATH = ROOT / "static" / "app" / "app-websocket.js"
WEBSOCKET_ROUTER_PATH = ROOT / "main_routers" / "websocket_router.py"
LOCALES_DIR = ROOT / "static" / "locales"
MUSIC_ROUTER_PATH = ROOT / "main_routers" / "music_router.py"
MUSIC_CRAWLERS_PATH = ROOT / "utils" / "music_crawlers.py"
DEFAULT_MUSIC_COVER_PATH = ROOT / "static" / "assets" / "music" / "music-cover-placeholder.png"
PAGES_ROUTER_PATH = ROOT / "main_routers" / "pages_router.py"
MUSIC_PLAYER_TEMPLATES = (ROOT / "templates" / "index.html", ROOT / "templates" / "chat.html")


def test_music_dispatch_waits_for_media_and_reports_real_failure():
    source = MUSIC_UI_PATH.read_text(encoding="utf-8")
    dispatch_source = APP_CHAT_PATH.read_text(encoding="utf-8")

    assert "waitForMusicMediaReady" in source
    assert "const result = await executePlay(" in source
    assert "window.sendMusicMessageDetailed" in source
    assert "window.sendMusicMessage = async function" in source
    assert "return result.ok === true" in source
    assert "canTryNextCandidate" in source
    assert "canTryNextMusicCandidate(mediaResult.reason)" in source
    retryable_failures = source.split("const canTryNextMusicCandidate", 1)[1].split("].includes(reason);", 1)[0]
    assert "'media_error'" in retryable_failures
    assert "'track_too_long'" in retryable_failures
    assert "'load_timeout'" in retryable_failures
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
    assert source.count("if (parsed.protocol !== 'https:')") == 2
    assert "['http:', 'https:'].includes(parsed.protocol)" not in source
    assert "trackInfo.url = toBackendMusicProxyUrl(originalUrl)" in source
    assert "trackInfo.url.includes('music.163.com')" not in source


def test_proactive_music_only_retries_candidate_specific_failures():
    source = PROACTIVE_UI_PATH.read_text(encoding="utf-8")

    assert "for (var musicIndex = 0; musicIndex < musicLinks.length; musicIndex++)" in source
    assert "window.dispatchMusicPlayDetailed(track, { source: 'proactive' })" in source
    assert "if (dispatchResult.ok === true)" in source
    assert "if (dispatchResult.canTryNextCandidate !== true)" in source
    assert "音乐派发因非候选错误停止" in source
    assert "音乐候选不可用，尝试下一条" in source
    assert "musicLinks = normalizedLinks.filter" in source
    assert "name: musicLink.title || '未知曲目'" not in source
    assert "artist: musicLink.artist || '未知艺术家'" not in source


def test_proactive_request_rechecks_music_state_before_search():
    source = PROACTIVE_UI_PATH.read_text(encoding="utf-8")
    player_source = MUSIC_UI_PATH.read_text(encoding="utf-8")

    assert "const isMusicOccupied = () =>" in player_source
    assert "localAudio && !localAudio.ended && !localPlayer._loadError" in player_source
    assert "mirrorBarLastState && mirrorBarLastState.track" in player_source
    assert "window.isMusicOccupied = isMusicOccupied" in player_source
    assert "var musicPlayingBeforeRequest" in source
    assert "var musicOccupiedBeforeRequest = isMusicOccupiedNow()" in source
    assert "var musicRateLimitedBeforeRequest" in source
    assert "requestBody.is_music_occupied = !!musicOccupiedBeforeRequest" in source
    assert (
        "requestBody.enabled_modes = requestBody.enabled_modes.filter(function (mode) "
        "{ return mode !== 'music'; });"
    ) in source
    assert source.index("var musicOccupiedBeforeRequest") < source.index(
        "var proactiveBody = JSON.stringify(requestBody)"
    )


def test_user_music_requests_retry_candidates_and_discard_stale_dispatches():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")

    assert "response.type === 'music_play_candidates'" in source
    assert (
        "source: 'user'," in source
    )
    assert "requestId: response.request_id" in source
    assert "dispatchResult.canTryNextCandidate !== true" in source
    assert "_musicCandidateDispatchEpoch" in source
    assert "_musicCandidateDispatchQueue" in source
    assert "catch (error)" in source
    assert "canTryNextCandidate: true" in source
    assert "没有可用的音乐派发接口" in source
    assert "if (accepted === 'queued')" in source
    assert "return 'queued';" in source
    assert "window._latestMusicCandidateRequestId" in source
    assert "if (!Number.isFinite(requestId) || requestId <= 0)" in source
    assert "window._pendingMusicCandidateRequestId" in source
    assert "requestId === latestRequestId && requestId !== pendingRequestId" in source
    assert "mediaCancelStatus === 'stale'" in source
    assert "queuedCancelStatus === 'stale'" in source
    candidate_dispatch = source.split(
        "async function dispatchMusicPlayCandidatesResponse", 1
    )[1].split("function queueMusicPlayCandidatesResponse", 1)[0]
    immutable_key = candidate_dispatch.index(
        "var candidateKey = getMusicPlayUrlClaimKey(track);"
    )
    claim = candidate_dispatch.index(
        "var candidateClaimToken = claimMusicPlayUrl(candidateKey);"
    )
    dispatch = candidate_dispatch.index("window.dispatchMusicPlayDetailed(track")
    assert immutable_key < claim < dispatch
    assert (
        "releaseMusicPlayUrlClaim(candidateKey, candidateClaimToken);"
        in candidate_dispatch
    )
    assert "claim.token === data.token" in source
    assert "claim.token !== token" in source
    assert "token: token" in source
    assert "window.cancelQueuedMusicDispatch(requestId);" in source
    invalid_guard = source.index("if (!Number.isFinite(requestId) || requestId <= 0)")
    cancel_call = source.index("window.cancelPendingMusicMediaReady(requestId);")
    epoch_update = source.index("window._latestMusicCandidateRequestId = requestId;")
    assert invalid_guard < cancel_call
    assert cancel_call < epoch_update
    queued_branch = source.split("if (accepted === 'queued')", 1)[1].split(
        "dispatchResult = {", 1
    )[0]
    assert "response._clientDispatchEpoch !== window._musicCandidateDispatchEpoch" in queued_branch
    assert "releaseMusicPlayUrlClaim(candidateKey, candidateClaimToken);" in queued_branch
    assert queued_branch.index("_musicCandidateDispatchEpoch") < queued_branch.index(
        "return 'queued';"
    )
    failure_handler = source.split(
        "function handleMusicRequestFailureResponse(response)", 1
    )[1].split("function readNewUserIcebreakerStore", 1)[0]
    assert "Number(response && response.request_id)" in failure_handler
    assert "window.cancelPendingMusicMediaReady(requestId);" in failure_handler
    assert "window.cancelQueuedMusicDispatch(requestId);" in failure_handler
    assert (
        failure_handler.index("window.cancelQueuedMusicDispatch(requestId);")
        < failure_handler.index("window._musicCandidateDispatchEpoch")
        < failure_handler.index("showMusicRequestFailure(response);")
    )
    cancellation_handler = source.split(
        "function handleMusicRequestCancelledResponse(response)", 1
    )[1].split("function readNewUserIcebreakerStore", 1)[0]
    assert "window.cancelPendingMusicMediaReady(requestId);" in cancellation_handler
    assert "window.cancelQueuedMusicDispatch(requestId);" in cancellation_handler
    assert "window.cancelActiveMusicPlayback();" in cancellation_handler
    assert "showMusicRequestFailure" not in cancellation_handler
    assert "response.type === 'music_request_cancelled'" in source

    player_source = MUSIC_UI_PATH.read_text(encoding="utf-8")
    active_cancel = player_source.split(
        "const cancelActiveMusicPlayback = () =>", 1
    )[1].split("// ---", 1)[0]
    assert "destroyMusicPlayer(true, true, true);" in active_cancel
    assert "broadcastBarCtrl('close');" in active_cancel
    assert "window.cancelActiveMusicPlayback = cancelActiveMusicPlayback;" in player_source
    started_handler = source.split(
        "function handleMusicRequestStartedResponse(response)", 1
    )[1].split("function handleMusicPlayCandidatesResponse(response)", 1)[0]
    assert "window.cancelPendingMusicMediaReady(requestId);" in started_handler
    assert "window.cancelQueuedMusicDispatch(requestId);" in started_handler
    assert "window._pendingMusicCandidateRequestId = requestId;" in started_handler
    assert "response.type === 'music_request_started'" in source


def test_music_request_scope_resets_on_same_character_reconnect():
    source = APP_WEBSOCKET_PATH.read_text(encoding="utf-8")
    connect_source = source.split("function connectWebSocket()", 1)[1].split(
        "mod.connectWebSocket = connectWebSocket", 1
    )[0]

    assert "function resetMusicCandidateRequestScope(scope, force)" in source
    assert "if (!force && window._musicCandidateRequestScope === nextScope) return;" in source
    assert "resetMusicCandidateRequestScope(currentLanlanName, true);" in connect_source
    idempotent_guard = connect_source.index(
        "S.socket && S.socket.readyState === WebSocket.OPEN && S.socket.url === wsUrl"
    )
    reset_call = connect_source.index(
        "resetMusicCandidateRequestScope(currentLanlanName, true);"
    )
    assert idempotent_guard < reset_call


def test_new_track_cancels_pending_media_readiness_wait():
    source = MUSIC_UI_PATH.read_text(encoding="utf-8")
    send_source = source.split(
        "window.sendMusicMessageDetailed = async function", 1
    )[1].split("window.sendMusicMessage = async function", 1)[0]

    assert "let pendingMusicMediaReadyCancel = null;" in source
    assert "cancelWait = () => finish(false, 'superseded');" in source
    assert "if (pendingMusicMediaReadyCancel) pendingMusicMediaReadyCancel();" in send_source
    assert send_source.index("++latestMusicRequestToken") < send_source.index(
        "pendingMusicMediaReadyCancel()"
    )
    allowlist_wait = send_source.index("await new Promise((resolve) => {")
    stale_guard = send_source.index("if (currentToken !== latestMusicRequestToken) {")
    assert send_source.index("const currentToken = ++latestMusicRequestToken;") < allowlist_wait
    assert allowlist_wait < stale_guard < send_source.index("isUnsupportedMusicStream")
    assert "cancelWait.requestId = requestId ?? null;" in source
    assert "window.cancelPendingMusicMediaReady = (requestId) =>" in source
    assert "return 'invalid';" in source
    assert "return 'no_pending';" in source
    assert "return 'stale';" in source
    assert "return 'cancelled';" in source
    assert "nextRequestId < pendingRequestId" in source
    no_pending_branch = source.split(
        "if (!pendingMusicMediaReadyCancel) {", 1
    )[1].split("const pendingRequestId", 1)[0]
    assert "latestMusicRequestToken++;" in no_pending_branch
    assert "!localPlayer && currentPlayingTrack" in no_pending_branch
    assert "updateMusicCard('ended', currentPlayingTrack);" in no_pending_branch
    assert "destroyMusicPlayer(true, false, false);" in no_pending_branch
    assert "return 'no_pending';" in no_pending_branch
    assert "window.cancelPendingMusicMediaReady(requestId);" in APP_WEBSOCKET_PATH.read_text(
        encoding="utf-8"
    )


def test_new_request_cancels_queued_player_dispatch():
    dispatch_source = APP_CHAT_PATH.read_text(encoding="utf-8")

    assert "let _queuedMusicDispatchCancel = null;" in dispatch_source
    assert "cancelQueuedDispatch.requestId = options.requestId ?? null;" in dispatch_source
    assert "window.cancelQueuedMusicDispatch = function (requestId)" in dispatch_source
    assert "nextRequestId < pendingRequestId" in dispatch_source
    assert "_queuedMusicDispatchCancel();" in dispatch_source
    assert "musicDispatchResult(false, 'superseded', false)" in dispatch_source


def test_music_player_reports_confirmed_state_to_backend():
    player_source = MUSIC_UI_PATH.read_text(encoding="utf-8")
    router_source = WEBSOCKET_ROUTER_PATH.read_text(encoding="utf-8")

    assert "function reportMusicPlaybackState(state, track, playbackContext)" in player_source
    assert "function createMusicPlaybackReportContext(playbackId, options, track, token)" in player_source
    assert "function getOwnedMusicPlaybackReportContext(player, state)" in player_source
    assert "function normalizeMusicEventTimestamp(event)" in player_source
    assert "action: 'music_playback_state'" in player_source
    assert "playback_window_id: MUSIC_COORD_SENDER_ID" in player_source
    assert "playback_started_at: context.lifecycleStartedAt" in player_source
    assert "localPlayer._musicPlaybackReportContext = playbackReportContext" in player_source
    ownership_source = player_source.split(
        "function getOwnedMusicPlaybackReportContext(player, state)", 1
    )[1].split("// ---", 1)[0]
    assert "context.token !== player._latestToken" in ownership_source
    assert "context.playbackId !== getCurrentMusicPlaybackId()" in ownership_source
    assert "latestMusicRequestToken" not in ownership_source
    assert "getOwnedMusicPlaybackReportContext(boundPlayer, 'playing')" in player_source
    assert "getOwnedMusicPlaybackReportContext(boundPlayer, playbackState)" in player_source
    assert "getOwnedMusicPlaybackReportContext(boundPlayer, 'ended')" in player_source
    assert "getOwnedMusicPlaybackReportContext(boundPlayer, 'error')" in player_source
    assert ") !== reportContext" in player_source
    assert "reportMusicPlaybackState('playing', null, reportContext)" in player_source
    assert "reportMusicPlaybackState('ended', null, reportContext)" in player_source
    assert "reportMusicPlaybackState('error', null, reportContext)" in player_source
    assert "localPlayer === boundPlayer && boundPlayer._latestToken === tokenAtEvent" in player_source
    assert 'elif action == "music_playback_state":' in router_source
    assert "handle_music_playback_state(" in router_source
    superseded_gate = router_source.split(
        "if session_id.get(lanlan_name) != this_session_id:", 1
    )[1].split("action = message.get(\"action\")", 1)[0]
    assert "if _is_music_playback_state_message(message):" in superseded_gate
    assert superseded_gate.index("_is_music_playback_state_message") < superseded_gate.index(
        "await websocket.close()"
    )
    assert 'mgr._music_playback_websockets = music_websockets' in router_source
    assert 'music_websockets.add(websocket)' in router_source
    assert 'music_websockets.discard(websocket)' in router_source


def test_music_player_rejects_errors_queued_before_the_current_source_lifecycle():
    player_source = MUSIC_UI_PATH.read_text(encoding="utf-8")
    readiness_handler = player_source.split(
        "const waitForMusicMediaReady = (", 1
    )[1].split("const getMusicPlayerInstance", 1)[0]
    error_handler = player_source.split(
        "boundPlayer.on('error', (err) => {",
        1,
    )[1].split("// 进度条与播放按钮点击", 1)[0]

    assert "const sourceLifecycleStartedAt = getMusicLifecycleTimestamp();" in readiness_handler
    assert "const eventTimestamp = normalizeMusicEventTimestamp(event);" in readiness_handler
    assert "eventTimestamp < sourceLifecycleStartedAt" in readiness_handler
    assert "window.queueMicrotask(onError)" not in readiness_handler
    assert "if (!audio.error && audio.readyState >= 1)" in readiness_handler
    assert "lifecycleStartedAt: getMusicLifecycleTimestamp()" in player_source
    assert "mediaReady: false" in player_source
    assert "const eventTimestamp = normalizeMusicEventTimestamp(err);" in error_handler
    assert "eventTimestamp < reportContext.lifecycleStartedAt" in error_handler
    assert "eventTimestamp === null && reportContext.mediaReady !== true" in error_handler
    delayed_handler = error_handler.split("setTimeout(() => {", 1)[1]
    assert "getOwnedMusicPlaybackReportContext(boundPlayer, 'error') !== reportContext" in delayed_handler
    assert delayed_handler.index("getOwnedMusicPlaybackReportContext") < delayed_handler.index(
        "boundPlayer._loadError = true;"
    )
    assert "playbackReportContext.mediaReady = true;" in player_source


def test_same_track_retry_refreshes_context_and_rebuilds_loading_player():
    player_source = MUSIC_UI_PATH.read_text(encoding="utf-8")
    duplicate_path = player_source.split(
        "// 5秒去重逻辑", 1
    )[1].split("if (isSameTrack(trackInfo) && !isPlayerInDOM())", 1)[0]

    assert "duplicateAudio.readyState >= 2" in duplicate_path
    assert "setMusicPlaybackContext(playbackOptions);" in duplicate_path
    assert "duplicatePlayer._musicPlaybackReportContext = duplicateReportContext;" in duplicate_path
    assert "reportMusicPlaybackState('playing', null, duplicateReportContext);" in duplicate_path

    fast_path = player_source.split(
        "if (isSameTrack(trackInfo) && isPlayerInDOM()) {",
        1,
    )[1].split("// A single <audio> cannot identify", 1)[0]
    assert "player.audio.readyState < 2" in fast_path
    assert fast_path.index("player.audio.readyState < 2") < fast_path.index(
        "setMusicPlaybackContext(playbackOptions);"
    )
    assert "player._latestToken = latestMusicRequestToken;" in fast_path
    assert fast_path.index("player._latestToken = latestMusicRequestToken;") < fast_path.index(
        "player._musicPlaybackReportContext = playbackReportContext;"
    )


def test_same_track_fast_path_rebuilds_missing_player_instance():
    player_source = MUSIC_UI_PATH.read_text(encoding="utf-8")

    fast_path = player_source.split(
        "if (isSameTrack(trackInfo) && isPlayerInDOM()) {",
        1,
    )[1].split("// A single <audio> cannot identify", 1)[0]
    assert "if (!player) {" in fast_path
    assert "destroyMusicPlayer(true, false, false);" in fast_path
    assert fast_path.index("if (!player) {") < fast_path.index(
        "player._musicPlaybackReportContext = playbackReportContext;"
    )


def test_same_url_replacement_uses_a_fresh_audio_element():
    player_source = MUSIC_UI_PATH.read_text(encoding="utf-8")
    send_source = player_source.split(
        "window.sendMusicMessageDetailed = async function", 1
    )[1].split("window.sendMusicMessage = async function", 1)[0]
    same_url_guard = send_source.split(
        "const currentAudioForRequest = localPlayer && localPlayer.audio;", 1
    )[1].split("try {", 1)[0]

    assert "currentAudioForRequest.currentSrc || currentAudioForRequest.src" in same_url_guard
    assert "resolveMusicUrl(currentAudioUrl) === resolveMusicUrl(trackInfo.url)" in same_url_guard
    assert "destroyMusicPlayer(true, false, false);" in same_url_guard

    teardown_source = player_source.split(
        "const destroyMusicPlayer =", 1
    )[1].split("const cancelActiveMusicPlayback", 1)[0]
    revoke_context = teardown_source.index(
        "localPlayer._musicPlaybackReportContext = null;"
    )
    pause_player = teardown_source.index("localPlayer.pause();")
    assert revoke_context < pause_player


def test_stale_remote_owner_cannot_hold_music_occupancy_forever():
    player_source = MUSIC_UI_PATH.read_text(encoding="utf-8")
    occupancy = player_source.split(
        "const isMusicOccupied = () => {", 1
    )[1].split("const getMusicCurrentTrack", 1)[0]

    assert "const remoteOccupied = isRemoteMusicActive();" in occupancy
    assert "!remoteMusicSenders.has(mirrorBarLeaderSender)" in occupancy
    assert "teardownMirrorBar(false);" in occupancy
    assert "setMirrorBarLeader(null);" in occupancy
    assert occupancy.index("teardownMirrorBar(false);") < occupancy.index(
        "setMirrorBarLeader(null);"
    )
    assert occupancy.index("isRemoteMusicActive()") < occupancy.index(
        "const mirrorOccupied"
    )


def test_missing_music_cover_stays_out_of_data_and_uses_frontend_placeholder():
    player_source = MUSIC_UI_PATH.read_text(encoding="utf-8")
    player_style = MUSIC_UI_CSS_PATH.read_text(encoding="utf-8")
    crawler_source = MUSIC_CRAWLERS_PATH.read_text(encoding="utf-8")

    assert "'cover': cover or ''" in crawler_source
    assert "dummyimage.com" not in crawler_source
    assert "defaultCoverPath: '/static/assets/music/music-cover-placeholder.png'" in player_source
    assert "const normalizeMusicCoverUrl = (cover) =>" in player_source
    assert "hostname.endsWith('.music.126.net')" in player_source
    assert "parsed.protocol = 'https:'" in player_source
    assert "const normalizedCover = normalizeMusicCoverUrl(cover)" in player_source
    assert "thumbnailUrl: displayCoverUrl" in player_source
    assert "applyMusicCover" not in player_source
    assert player_source.count('class="music-bar-equalizer"') == 2
    assert player_source.count('class="music-bar-equalizer-bar"') == 6
    assert ".music-player-bar.is-playing .music-bar-equalizer-bar" in player_style
    assert "@keyframes musicBarEqualizer" in player_style
    assert "music-bar-fallback" not in player_source
    assert "dummyimage.com" not in player_source
    assert DEFAULT_MUSIC_COVER_PATH.stat().st_size > 0


def test_music_player_assets_are_versioned_with_the_page():
    pages_source = PAGES_ROUTER_PATH.read_text(encoding="utf-8")

    assert '_PROJECT_ROOT / "static/jukebox/music_ui.js"' in pages_source
    assert '_PROJECT_ROOT / "static/css/music_ui.css"' in pages_source
    assert '_PROJECT_ROOT / "static/assets/music/music-cover-placeholder.png"' in pages_source
    for template_path in MUSIC_PLAYER_TEMPLATES:
        template_source = template_path.read_text(encoding="utf-8")
        assert '/static/css/music_ui.css?v={{ static_asset_version }}' in template_source
        assert '/static/jukebox/music_ui.js?v={{ static_asset_version }}' in template_source


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
        "loginRequired",
        "playlistAmbiguous",
        "sourceEmpty",
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
