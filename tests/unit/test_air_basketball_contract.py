import json
import math
from pathlib import Path

import pytest

from main_routers import pages_router


ROOT = Path(__file__).resolve().parents[2]


class _FakeTemplates:
    def TemplateResponse(self, template_name, context):
        return {"template_name": template_name, "context": context}


class _FakeRequest:
    pass


@pytest.mark.unit
@pytest.mark.asyncio
async def test_air_basketball_page_renders_game_shell(monkeypatch):
    monkeypatch.setattr(pages_router, "get_templates", lambda: _FakeTemplates())
    monkeypatch.setattr(
        pages_router,
        "_static_assets_ctx",
        lambda: {"static_asset_version": "test-version"},
    )
    monkeypatch.setattr(
        pages_router,
        "_air_basketball_assets_ctx",
        lambda: {"air_basketball_asset_version": "air-version"},
    )
    result = await pages_router.air_basketball(_FakeRequest())
    assert result["template_name"] == "templates/air_basketball.html"
    assert result["context"]["static_asset_version"] == "test-version"
    assert result["context"]["air_basketball_asset_version"] == "air-version"


@pytest.mark.unit
def test_air_basketball_assets_use_their_own_cache_version():
    # Editing a game file or its artwork must not bump the site-wide version.
    html = ROOT.joinpath("templates", "air_basketball.html").read_text(encoding="utf-8")
    game_lines = [line for line in html.splitlines() if "/static/game/games/air_basketball/" in line]
    assert game_lines
    assert all("air_basketball_asset_version" in line for line in game_lines)
    assert not any("static_asset_version |" in line for line in game_lines)
    assert not any(
        "air_basketball" in path.parts for path in pages_router._YUI_GUIDE_ASSET_VERSION_PATHS
    )
    own = {path.name for path in pages_router._AIR_BASKETBALL_ASSET_VERSION_PATHS}
    assert {"game.js", "physics.js", "arcade.css", "neko-hoop.png"} <= own


@pytest.mark.unit
def test_air_basketball_mvp_interaction_contract():
    html = ROOT.joinpath("templates", "air_basketball.html").read_text(encoding="utf-8")
    game = ROOT.joinpath("static", "game", "games", "air_basketball", "game.js").read_text(encoding="utf-8")
    physics = ROOT.joinpath("static", "game", "games", "air_basketball", "physics.js").read_text(encoding="utf-8")
    i18n = ROOT.joinpath("static", "game", "games", "air_basketball", "i18n.js").read_text(encoding="utf-8")
    avatar = ROOT.joinpath("static", "game", "games", "air_basketball", "avatar.js").read_text(encoding="utf-8")
    avatar_host = ROOT.joinpath("static", "game", "games", "air_basketball", "avatar-host.js").read_text(encoding="utf-8")
    sdk_bootstrap = ROOT.joinpath("static", "game", "games", "air_basketball", "sdk-bootstrap.js").read_text(encoding="utf-8")
    arcade_css = ROOT.joinpath("static", "game", "games", "air_basketball", "arcade.css").read_text(encoding="utf-8")
    main_server = ROOT.joinpath("app", "main_server", "__init__.py").read_text(encoding="utf-8")
    character_names = ROOT.joinpath("utils", "character_name.py").read_text(encoding="utf-8")

    assert 'canvas id="player-court"' in html
    assert 'canvas id="neko-court"' in html
    assert '/static/game/games/air_basketball/game.js?v=' in html
    assert "playerLane.canvas.addEventListener('pointerdown'" in game
    assert "nekoLane.releaseAutoShot" in game
    assert "const STAGE_THRESHOLDS = [0, 12, 30, 54]" in game
    assert "const FEVER_HITS = 5" in game
    assert "const FEVER_SECONDS = 7" in game
    assert "stageForScore" in game
    assert "setFever" in game
    assert "new URL(import.meta.url).search" in game
    assert "import(`./i18n.js${assetVersion}`)" in game
    assert "import(`./physics.js${assetVersion}`)" in game
    assert "import(`./avatar.js${assetVersion}`)" in game
    assert "import(`./sdk-bootstrap.js${assetVersion}`)" in game
    assert "if (lane === nekoLane && ball.owner === 'player' && !ball.scored) missed('player')" in game
    assert "score:{ player:state.player.score, ai:state.neko.score }" in game
    assert "gameStarted:true" in game
    assert "game_started:true" in game
    assert "gameStartedElapsedMs:0" in game
    assert "* lane.width / Math.max(1, rect.width)" in game
    assert "const requestedCost = Math.hypot(dx, dy) * ACTION_BALANCE.JAM_DRAG_SCALE" in game
    assert game.index("spendFocus('player', cost)") < game.index("lane.interfere(appliedDx, appliedDy, appliedPoint)")
    assert "class ShotLane" in physics
    assert "const assetVersion = new URL(import.meta.url).search" in physics
    assert "versionedAsset('./assets/neko-basketball.png')" in physics
    assert "ball.vx *= scaleX" in physics
    assert "ball.vy *= scaleY" in physics
    assert "this.onMiss?.({ owner:b.owner })" in physics
    assert "collideRim" in physics
    assert "getAimTelemetry" in physics
    assert "getHoopPose" in physics
    assert "const flight = .98 + Math.random() * .06" in physics
    assert "(1 - difficulty) * 360 + stagePenalty" in physics
    assert "STAGE_RULES" in physics
    assert 'id="air-neko-avatar"' in html
    assert 'id="air-neko-live2d"' in html
    assert 'id="air-neko-vrm"' in html
    assert 'id="air-neko-mmd"' not in html
    assert 'id="air-neko-pngtuber"' not in html
    assert html.count('class="avatar-hit-zone ') == 3
    assert "MMD" not in avatar_host
    assert "PNGTuber" not in avatar_host
    assert "game.avatar.mount" in avatar
    assert "createAirBasketballAvatarHost" in avatar_host
    assert "function waitForVrmModules(signal" in avatar_host
    assert "function waitForVrmModules(signal, timeoutMs = 15000) {\n  throwIfAborted(signal);" in avatar_host
    assert "window.NekoMiniGameAvatarHost.create" in avatar_host
    assert "window.live2dManager" in avatar_host
    assert "window.VRMManager" in avatar_host
    assert "/static/yui-origin/yui-origin.model3.json" not in avatar
    assert "initNekoAvatar" in game
    assert "nekoLane.interfere" in game
    assert "throwChaosBall(playerLane, 'neko'" in game
    assert "const ACTION_BALANCE = Object.freeze" in game
    assert "focus:ACTION_BALANCE.MAX_FOCUS" in game
    assert "function spendFocus(side, cost)" in game
    assert "recoverFocus('player', dt)" in game
    assert "recoverFocus('neko', dt)" in game
    assert 'id="player-focus-fill"' in html
    assert 'id="neko-focus-fill"' in html
    assert 'id="player-combo-burst"' in html
    assert 'id="neko-combo-burst"' in html
    assert "function showCombo" in game
    assert "nekoAvatar.addEventListener('pointerdown'" in game
    assert "reactNeko('hit', direction)" in game
    assert "nekoHitCount" in game
    assert "nekoBallHitCount" in game
    assert "function checkPlayerBallNekoHit" in game
    assert "function beginCourtInterference" in game
    assert "beginCourtInterference(playerLane, event, true)" in game
    assert "function avatarCollisionZones()" in game
    assert "circleTouchesEllipse" in game
    assert "allowOuterExit:playerTransfer" in game
    assert "function containTrackedBallAtViewportEdge" in game
    assert "if (!ball.allowOuterExit) return false" in game
    assert "ball.owner === 'player'" in game
    assert "avatarIsReady()" in game
    assert "crossBall.addEventListener('pointerdown'" in game
    assert "crossBall.classList.contains('is-interactive')" in game
    assert "!b.allowOuterExit && b.x + b.r > this.width" in physics
    avatar_css = ROOT.joinpath("static", "game", "games", "air_basketball", "avatar.css").read_text(encoding="utf-8")
    assert "pointer-events: none" in avatar_css
    assert ".neko-avatar.is-ready .avatar-hit-zone" in avatar_css
    assert "hitNeko(1, 'ball')" in game
    assert "ball.vx = -Math.max(Math.abs(ball.vx) * .68, 360)" in game
    assert "this.side === 'player' ? 4.15 : 3.55" in physics
    assert "if (b.pageOverlay)" in physics
    assert "guest.pageOverlay" in physics
    assert "containOuterEdge" in physics
    assert "containGuestEdges" in physics
    assert "b.y - b.r > this.height" in physics
    assert "!state.nekoCounterPending" in game
    assert "function chooseNekoAction(" in game
    assert "function planNekoIntent" in game
    assert "canMouse && roll < .10 ? NEKO_ACTION.MOUSE" in game
    assert "canPrank && roll < .30 ? NEKO_ACTION.PLAYER" in game
    assert "pendingMouse:false, pendingPrank:false" in game
    assert "attention.aimSeconds >= NEKO_ATTENTION.AIM_SECONDS" in game
    assert "state.player.combo >= NEKO_ATTENTION.COMBO_THREAT" in game
    assert "state.nekoAttention.revenge = NEKO_ATTENTION.REVENGE_WINDOW" in game
    assert "state.nekoAttention.crossThreat = NEKO_ATTENTION.CROSS_WINDOW" in game
    assert "state.nekoCalmActions" not in game
    assert "state.nextNekoDecision <= 0" in game
    assert "const canPrank = state.nextNekoInterference <= 0" in game
    assert "nekoLane.ball.flying || nekoLane.ball.inTransit" in game
    assert "state.nextNekoDecision = Math.max(state.nextNekoDecision, NEKO_SHOT_DELAY.PRANK_RECOVERY)" in game
    assert "type === 'hit'" in avatar
    assert 'id="cross-ball"' in html
    assert 'id="mouse-steal-layer"' in html
    assert 'id="stolen-cursor"' in html
    assert "/static/assets/tutorial/highlight/cat-paw.png" in html
    assert "receiveGuestBall" in physics
    assert "collideBalls" in physics
    assert "playerLane.onCross" in game
    assert "nekoLane.onCross" in game
    assert "crossScored" in game
    assert "throwChaosBall" in game
    assert "crossCount" in game
    assert "ballClashCount" in game
    assert "nativeScored" in game
    assert "nekoCounterPending" in game
    assert "beginNekoPrank" in game
    assert "ownershipChanged" in physics
    assert "drawOwnershipMarker" in physics
    assert "Math.min(960, speed * 1.06)" in physics
    assert "inTransit:true" in physics
    assert "function crossTransitState" in game
    assert "function animateAuxiliaryCross" in game
    assert "const playerTransfer = data.owner === 'player'" in game
    assert "trackedGuestSuspended = true" in game
    assert "trackGuestBall(playerLane.ball, playerLane)" in game
    assert "trackingPlayerNative = trackedGuestBall === playerLane.ball" in game
    assert "lane === playerLane && ball !== playerLane.ball" in game
    assert "!this.ball?.flying && !this.ball?.inTransit" in physics
    assert "z-index: 60" in arcade_css
    assert "Math.abs(gap) / horizontalScreenSpeed * 1000" in game
    assert "vx:data.vx * sourceScaleX / targetScaleX" in game
    assert "vy:(data.vy + COURT_GRAVITY * seconds)" in game
    assert "vx:transit.vx" in game
    assert "vy:transit.vy" in game
    assert "x:entersFromLeft ? 0 : targetLane.width" in game
    assert "nextX > this.width ? 'right'" in physics
    assert "nextX < 0 ? 'left'" in physics
    assert "const crossingRatio = crossingEdge" in physics
    assert "const simulatedDt = dt * crossingRatio" in physics
    assert "stepRemainder:Math.max(0, dt - simulatedDt)" in physics
    assert "if (crossingEdge) b.x = boundaryX" in physics
    assert "data.stepRemainder || 0" in game
    assert "sourceLane.resetBall()" in game
    assert "getGuestMotion" in physics
    assert "scaleX(" not in game
    assert "drawAimFeedback" in physics
    assert "drawMotionBlur" in physics
    assert "drawTrajectory" not in physics
    assert "neko-arcade-lane-v2.webp" in physics
    assert "neko-basketball.png" in physics
    assert "neko-hoop.png" in physics
    assert "b.rotation = (b.rotation || 0)" in physics
    assert 'name="match-mode" value="timed" checked' in html
    assert 'name="match-mode" value="endless"' in html
    assert 'id="stop-match"' in html
    assert "state.mode === 'timed'" in game
    assert "formatElapsed(state.elapsed)" in game
    assert "stopMatchButton.addEventListener('click', finishMatch)" in game
    assert '"air-basketball"' in html
    assert "/static/game/sdk/neko-minigame-sdk.js" in html
    assert "/static/game/sdk/neko-minigame-same-origin-bootstrap.js" in html
    assert '"adapterUrl":"/static/game/sdk/neko-minigame-same-origin-host.js?v=' in html
    assert "/static/game/sdk/neko-minigame-avatar-host.js" in html
    assert "/static/game/sdk/neko-minigame-audio-host.js" in html
    assert "/static/game/sdk/neko-minigame-audio-host.js?v=" in html
    assert 'three/addons/loaders/GLTFLoader.js": "/static/libs/three/addons/loaders/GLTFLoader.js?v=' in html
    assert "user-scalable=no" not in html
    assert html.count('<link rel="preload" as="image" href="/static/game/games/air_basketball/assets/') == 3
    assert "window.NekoMiniGame.connect" in sdk_bootstrap
    assert "import(`./avatar-host.js${assetVersion}`)" in sdk_bootstrap
    assert "NekoMiniGame audio host is unavailable" in sdk_bootstrap
    # The trusted bootstrap only consumes avatar providers registered on the
    # launch node before it runs; constructor `avatarHost` injection and raw
    # `getCharacter()` were removed from the shared host (#3108).
    # live2d-interaction.js setupTouchZoom() needs NekoModelTouchGestures (#3124).
    touch_gestures = "/static/avatar/avatar-touch-gestures.js?v="
    assert html.index(touch_gestures) < html.index("/static/live2d/live2d-interaction.js?v=")
    registration = '/static/game/games/air_basketball/air-basketball-neko-host-registration.js?v='
    assert html.index(registration) < html.index("/static/game/sdk/neko-minigame-same-origin-bootstrap.js")
    assert "window.createAirBasketballAvatarHost = createAirBasketballAvatarHost" in sdk_bootstrap
    assert "avatarHost," not in sdk_bootstrap
    assert "transport.getCharacter(" not in sdk_bootstrap
    assert "game.runtime.bindCharacter(requestedName || undefined)" in sdk_bootstrap
    # A replay re-validates the same character through the public SDK API.
    assert "bindRuntimeCharacter" not in sdk_bootstrap
    # The binding survives the replay reset synchronously (no unbound window), and
    # the kept name is checked with read-only discovery before the route starts.
    assert "game.runtime.reset({ newSession:true, keepCharacter:true });" in sdk_bootstrap
    assert "retainAvatars" not in sdk_bootstrap
    assert "const character = await game.avatar.getCharacter(identity.name);" in sdk_bootstrap
    # A previous route left `degraded` by a failed end() is ended before a new start.
    assert "if (game.runtime.state === 'degraded') {" in sdk_bootstrap
    assert "await game.runtime.end(lastEndPayload || {}).catch(() => undefined);" in sdk_bootstrap
    assert "  lastEndPayload = payload;" in sdk_bootstrap
    assert "requiredCapabilities:['runtime', 'logging', 'avatar-renderer', 'audio', 'speech-output']" in sdk_bootstrap
    assert "game.audio.mount" in sdk_bootstrap
    assert "audio.playSfx" in sdk_bootstrap
    assert "game.speech.preload" in sdk_bootstrap
    assert "game.speech.speak" in sdk_bootstrap
    assert "game.runtime.configure" in sdk_bootstrap
    assert "game.runtime.start" in sdk_bootstrap
    assert "game.runtime.end" in sdk_bootstrap
    assert "game.logger.enableAfterRuntimeStart" in sdk_bootstrap
    assert "game.dispose()" in sdk_bootstrap
    assert "window.addEventListener('pagehide', disposeGameSdk" in game
    # Registered whether or not runtime configuration succeeds.
    assert ").then(() => {\n    window.addEventListener('pagehide'" not in game
    assert "window.addEventListener('pageshow'" in game
    assert "event?.persisted" in game
    assert "/api/" not in game
    assert "/api/" not in avatar
    assert "prewarmNekoVoice(opponentName);" in game
    assert "prewarmNekoVoice(identity?.name)" in game
    assert "speakNekoSpeech" in game
    assert "reuseSynthesizedAudio:true" in game
    assert "applyOpponentName(identity?.name)" in game
    assert "function beginMouseSteal()" in game
    assert "function endMouseSteal(escaped = false)" in game
    assert "const NEKO_ACTION = Object.freeze" in game
    assert "NEKO_BALL_POOL = Object.freeze({ TOTAL:2, READY:1, MAX_ACTIVE:2, MAX_AIRBORNE:2 })" in game
    assert "NEKO_SHOT_DELAY = Object.freeze({ MIN:1.9, MAX:2.5, BUSY_BONUS:.45, PRANK_RECOVERY:1.4, RETURN_RETRY:.32 })" in game
    assert "function nekoActiveBallCount()" in game
    assert "Math.min(transitBalls, representedTwice)" in game
    assert "nekoBallInventoryReady()" in game
    assert "function nekoPrankInventoryReady()" in game
    assert "return !nekoLane.ball.flying" in game
    assert "&& !nekoLane.ball.inTransit" in game
    assert "if (!nekoPrankInventoryReady())" in game
    assert "countActiveBalls({ owner = null, nativeShot = null } = {})" in physics
    assert "guest.nativeShot && !this.ball.flying" in physics
    assert "BASE_ACCURACY:.76" in game
    assert "MISS_RECOVERY:.04" in game
    assert "MAX_ACCURACY:.88" in game
    assert "OFFENSE_DROUGHT:3.2" in game
    assert "state.neko.shotMissStreak * NEKO_STRENGTH.MISS_RECOVERY" in game
    assert "state.nekoOffenseIdle >= NEKO_STRENGTH.OFFENSE_DROUGHT" in game
    assert "ACTION_BALANCE.MOUSE_COST + NEKO_STRENGTH.SHOT_RESERVE" in game
    assert "ACTION_BALANCE.PRANK_COST + NEKO_STRENGTH.SHOT_RESERVE" in game
    assert "scoreGap *" not in game
    assert "const targetBall = targetLane.ball.inTransit ? null : targetLane.ball" in game
    assert "prankNeko:beginNekoPrank" in game
    assert "state.nekoAction !== NEKO_ACTION.IDLE" in game
    assert "beginNekoAction(NEKO_ACTION.HOOP)" in game
    assert "beginNekoAction(NEKO_ACTION.PLAYER)" in game
    assert "const shot = nekoLane.releaseAutoShot(difficulty)" in game
    assert "endNekoAction(NEKO_ACTION.HOOP)" in game
    assert "throwChaosBall(playerLane, 'neko', 'right');\n    endNekoAction(NEKO_ACTION.PLAYER)" in game
    assert "releaseAutoShot(difficulty = .72)" in physics
    assert "getHoopPoseAt(this.stageClock + flight)" in physics
    assert "const horizontalTravel = (1 - Math.exp(-dampingRate * flight)) / dampingRate" in physics
    assert "const rimClearance = Math.min(10, this.ball.r * .5)" in physics
    assert "nativeShot:true" in physics
    assert "countActiveGuestBalls({ owner = null, nativeShot = null } = {})" in physics
    assert "this.guests.shift()" not in physics
    assert "beginNekoAction(NEKO_ACTION.MOUSE)" in game
    assert "hasFocus('neko', ACTION_BALANCE.MOUSE_COST + NEKO_STRENGTH.SHOT_RESERVE)" in game
    assert "hasFocus('neko', ACTION_BALANCE.PRANK_COST + NEKO_STRENGTH.SHOT_RESERVE)" in game
    assert "hasFocus('neko', ACTION_BALANCE.SHOT_COST)" in game
    assert "hasFocus('player', ACTION_BALANCE.SHOT_COST)" in game
    assert "Math.min(5, basePoints" in game
    assert "FOCUS_REGEN_DELAY:.6" in game
    assert "MOUSE_MAX_PER_ROUND:2" in game
    assert "state.neko.hitGrace > 0" in game
    assert "state.neko.stagger = ACTION_BALANCE.STAGGER" in game
    assert "MOUSE_STEAL_DURATION = Object.freeze({ MIN:1800, MAX:3200 })" in game
    assert "MOUSE_STEAL_COOLDOWN = Object.freeze({ MIN:15, MAX:23 })" in game
    assert "function mouseEscapeTarget()" in game
    assert "mouseSteal.struggle >= mouseEscapeTarget()" in game
    assert "voiceMouseSteal" in game
    assert "stealMouse:beginMouseSteal" in game
    assert "t('chaosBall', { name:opponentName })" in game
    assert "猫娘丢来一颗球" not in i18n
    assert "角色资源暂不可用" not in avatar
    # Locale loading, language detection and cache-bust belong to the shared bootstrap.
    assert "/static/i18n-i18next.js?v=" in html
    assert "fetch(" not in i18n
    assert "/static/locales/" not in i18n
    assert "window.i18n" in i18n
    assert "returnObjects:true" in i18n
    assert "aspect-ratio: 8 / 11" in arcade_css
    assert "document.body.appendChild(crossBall)" in game
    assert "--cross-size" in game
    assert "requestAnimationFrame(step)" in game
    assert "onArrive?.()" in game
    assert "Keep the overlay for one more paint" in game
    assert "const MAX_PHYSICS_STEP_SECONDS = .033" in game
    assert "const MAX_PHYSICS_STEPS_PER_FRAME = 8" in game
    assert "const MAX_PHYSICS_FRAME_DELTA_SECONDS = .25" in game
    assert "function planPhysicsSteps(frameSeconds)" in game
    assert "Math.ceil(simulatedSeconds / MAX_PHYSICS_STEP_SECONDS)" in game
    assert "step < plan.steps; step += 1) update(plan.stepSeconds)" in game
    assert "function advanceMatchClock(frameSeconds)" in game
    assert "timerAccumulator += realSeconds" in game
    assert "timerAccumulator += dt" not in game
    assert "function playablePhysicsSeconds(frameSeconds)" in game
    assert "state.remaining - timerAccumulator" in game
    assert "planPhysicsSteps(playablePhysicsSeconds(frameSeconds))" in game
    assert "trackedCrossTransit?.restart()" in game
    assert "transit.duration / 1000 * trackedTransit.progress" in game
    assert "function nativeMissed(laneSide, data)" in game
    assert "missed(owner)" in game
    assert "currentMatch !== matchSequence" in game
    assert ").then(() => {" in game
    assert "}, 160)" not in game
    assert "function translatedText(key, params)" in i18n
    assert "if (value !== null) node.textContent = value" in i18n
    assert "Math.min(.033, (now - lastFrame)" not in game
    assert "if (!nekoAiFrozen && state.nextNekoDecision <= 0)" in game
    # Automation hooks that bypass focus/cooldown rules exist only on test pages.
    assert "if (pageParams.get('test_mode') === '1') window.AirBasketballMVP = Object.freeze({" in game
    assert game.count("window.AirBasketballMVP =") == 1
    # A failed SDK bootstrap must leave the page wired and report it on the start card.
    assert "sdkContext = await Promise.race([\n    airBasketballSdkReady," in game
    assert "function showSdkUnavailable()" in game
    assert "t('sdkUnavailable')" in game
    assert "airBasketballSdkReady.catch(() => undefined);" in sdk_bootstrap
    # Physics sub-steps read layout through a per-frame cache; the overlay syncs once per frame.
    assert "stepLayoutCache = new Map();" in game
    update_body = game.split("function update(dt) {", 1)[1].split("\n}\n", 1)[0]
    assert "settleTrackedGuestBall();" in update_body
    assert "syncTrackedGuestBall();" not in update_body
    assert "document.addEventListener('visibilitychange'" in game
    assert "const releasesTrackedBall = trackedGuestBall === sourceLane.ball;" in game
    # A stalled bootstrap is bounded like a failed one.
    assert "const SDK_BOOTSTRAP_TIMEOUT_MS = 20000;" in game
    assert "void airBasketballSdkReady.then(() => disposeGameSdk(), () => undefined);" in game
    # The backend route ends shortly after the result line, not after its 60 s timeout.
    assert "{ after:latestSpeechPromise }" not in game
    assert "const RESULT_SPEECH_END_MAX_WAIT_MS = 12000;" in game
    # ...but it waits for actual playback: route finalize cancels speech still playing.
    assert "waitForNekoSpeechPlayback(resultSpeechRequestId, RESULT_SPEECH_END_MAX_WAIT_MS)" in game
    assert "export async function waitForNekoSpeechPlayback(requestId, maxMs)" in sdk_bootstrap
    # Only this request's playback counts: start first, then stop.
    assert "const ours = state?.requestId === requestId;" in sdk_bootstrap
    assert "if (ours && playing) started = true;" in sdk_bootstrap
    assert "waitForNekoSpeechIdle" not in game + sdk_bootstrap
    # An interrupting line (the result) is not swallowed by the voice cooldown.
    assert "if (!interrupt && now < voiceGuardUntil) return false;" in game
    speak_body = game.split("function speakNeko(", 1)[1].split("\n}\n", 1)[0]
    assert "return requestId;" in speak_body
    reset_body = game.split("function resetMatch() {", 1)[1].split("const currentMatch", 1)[0]
    assert "skipResultSpeechWait?.();" in reset_body
    finish_body = game.split("function finishMatch() {", 1)[1].split("\n}\n", 1)[0]
    assert "stopMatchPlay();" in finish_body
    stop_body = game.split("function stopMatchPlay() {", 1)[1].split("\n}\n", 1)[0]
    assert "cancelPlayerAction();" in stop_body
    # A replay waits for a pending Avatar mount, and a match never runs without its route.
    assert "avatarMountSettled" not in game + avatar
    assert "abortMatchWithoutRuntime(currentMatch);" in game
    # A short result line that already finished does not stall the route end.
    assert "else if (ours) finish();" in sdk_bootstrap
    # A held aim survives a resize; the resting ball is re-seated only on a real size change.
    assert "if (sizeChanged && !this.ball?.flying && !this.ball?.inTransit) {" in physics
    assert "this.aim = aim;" in physics
    # A VRM loaded after cancellation is disposed before it is dropped.
    assert "vrmModule.VRMUtils?.deepDispose?.(gltf.scene);" in avatar_host
    assert "if (game.runtime.session.characterName !== identity.name) {" in sdk_bootstrap
    # runtime.start() settles without throwing on a rejected/inactive route.
    assert "if (game.runtime.state !== 'running') {" in sdk_bootstrap
    assert "t('runtimeStartFailed')" in game
    # A bootstrap failing after connect() releases the client and tone URLs.
    assert "game.dispose();\n    revokeToneUrls();\n    throw error;" in sdk_bootstrap
    # The Live2D opponent keeps a fixed renderer size.
    assert "{ resizeMode:'fixed', width:320, height:440 }" in avatar_host
    # Retained Avatars must declare their character.
    assert "characterName:identity.name," in avatar
    # The fallback label is re-rendered on language change, never re-prefixed.
    assert "data-i18n-label" not in html
    assert "setAvatarUnavailableLabel(t('avatarUnavailable'));" in game
    assert "fallbackType?.dataset.label" not in avatar
    # Physics steps mark HUD/focus updates; the frame writes them once.
    recover_body = game.split("function recoverFocus(side, dt) {", 1)[1].split("\n}\n", 1)[0]
    fever_body = game.split("function updateFever(side, dt) {", 1)[1].split("\n}\n", 1)[0]
    assert "syncFocus(" not in recover_body and "frameSync.focus.add(side);" in recover_body
    assert "syncHud(" not in fever_body and "frameSync.hud.add(side);" in fever_body
    frame_body = game.split("function frame(now) {", 1)[1].split("\n}\n", 1)[0]
    assert "flushFrameSync();" in frame_body
    release_body = game.split("function releasePlayerShot() {", 1)[1].split("\n}\n", 1)[0]
    assert "if (!state.running) {" in release_body
    # Runtime-written elements are re-rendered from state, not by data-i18n.
    for element_id in ("neko-status-text", "clock-label", "clock-unit", "start-copy", "start-button"):
        tag = html.split(f'id="{element_id}"', 1)[1].split(">", 1)[0]
        assert "data-i18n" not in tag, element_id
    assert "function renderLocalizedState()" in game
    assert "byId('neko-status-text').textContent = opponentStatus(key);" in game
    assert game.count("byId('neko-status-text')") == 1
    # Avatar expressions are cosmetic; VRM uses the shared perspective fit.
    assert "avatarController?.setEmotion(name)).catch(() => undefined)" in avatar
    assert "fitThreeModel" not in avatar_host
    assert "window.NekoMiniGameAvatarHost.fitPerspectiveModel(" in avatar_host
    assert "autoShoot(" not in physics
    assert "prepareIsolatedCrossTest" in game
    assert "position: fixed" in arcade_css
    assert ".mode-picker input:focus-visible + span" in arcade_css
    assert "@media (max-height: 680px)" in arcade_css
    assert "overflow-y: auto" in arcade_css
    assert "@keyframes cross-flight" not in arcade_css
    assert 'id="player-power-fill"' in html
    assert 'id="player-fever-fill"' in html
    assert 'id="neko-stage"' in html
    # The embedded /chat iframe ran a second, independent chat client for the
    # same character (proactive election, websocket, music owner claim).
    assert not ROOT.joinpath("static", "game", "games", "air_basketball", "chat-dock.js").exists()
    assert "game-chat" not in html
    assert "/chat?" not in html
    assert "game-chat" not in game
    assert "AudioContext" not in game
    assert "AudioContext" not in sdk_bootstrap
    assert 'loading="lazy"' not in html
    limited_pages = main_server.split("_MAIN_LIMITED_MODE_ALLOWED_PAGE_PATHS = {", 1)[1].split("}", 1)[0]
    assert '"/air_basketball"' in limited_pages
    reserved_routes = character_names.split("RESERVED_ROUTE_NAMES = frozenset({", 1)[1].split("})", 1)[0]
    assert '"air_basketball"' in reserved_routes
    expected_keys = {
        "title", "gestureHint", "chaosBall", "opponentReady", "voiceOpening",
        "avatarUnavailable", "arenaLabel", "sdkUnavailable", "runtimeStartFailed", "mouseStealCaught",
        "mouseStealEscape", "voiceMouseSteal",
    }
    for locale in ("en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW"):
        payload = json.loads(
            ROOT.joinpath("static", "locales", f"{locale}.json").read_text(encoding="utf-8")
        )
        assert expected_keys <= payload["airBasketball"].keys()
        assert not {"nekoReady", "nekoAiming", "nekoFever", "lose", "disrupted"} & payload["airBasketball"].keys()
        assert payload["airBasketball"]["feverOn"].endswith("+1")


@pytest.mark.unit
@pytest.mark.parametrize(
    ("fps", "expected_steps", "expect_clamped"),
    ((60, 1, False), (30, 2, False), (20, 2, False), (15, 3, False), (2, 8, True)),
)
def test_air_basketball_physics_substeps_preserve_frame_time(
    fps,
    expected_steps,
    expect_clamped,
):
    frame_seconds = 1 / fps
    simulated_seconds = min(frame_seconds, 0.25)
    steps = min(8, math.ceil(simulated_seconds / 0.033))
    step_seconds = simulated_seconds / steps

    assert steps == expected_steps
    assert step_seconds <= 0.033
    assert step_seconds * steps == pytest.approx(simulated_seconds)
    assert (simulated_seconds < frame_seconds) is expect_clamped
