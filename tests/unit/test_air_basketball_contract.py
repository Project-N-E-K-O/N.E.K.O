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
    result = await pages_router.air_basketball(_FakeRequest())
    assert result["template_name"] == "templates/air_basketball.html"
    assert result["context"]["static_asset_version"] == "test-version"


@pytest.mark.unit
def test_air_basketball_mvp_interaction_contract():
    html = ROOT.joinpath("templates", "air_basketball.html").read_text(encoding="utf-8")
    game = ROOT.joinpath("static", "air-basketball", "game.js").read_text(encoding="utf-8")
    physics = ROOT.joinpath("static", "air-basketball", "physics.js").read_text(encoding="utf-8")
    i18n = ROOT.joinpath("static", "air-basketball", "i18n.js").read_text(encoding="utf-8")
    avatar = ROOT.joinpath("static", "air-basketball", "avatar.js").read_text(encoding="utf-8")
    avatar_host = ROOT.joinpath("static", "air-basketball", "avatar-host.js").read_text(encoding="utf-8")
    sdk_bootstrap = ROOT.joinpath("static", "air-basketball", "sdk-bootstrap.js").read_text(encoding="utf-8")
    arcade_css = ROOT.joinpath("static", "air-basketball", "arcade.css").read_text(encoding="utf-8")
    chat = ROOT.joinpath("static", "air-basketball", "chat-dock.js").read_text(encoding="utf-8")
    main_server = ROOT.joinpath("app", "main_server", "__init__.py").read_text(encoding="utf-8")
    character_names = ROOT.joinpath("utils", "character_name.py").read_text(encoding="utf-8")

    assert 'canvas id="player-court"' in html
    assert 'canvas id="neko-court"' in html
    assert '/static/air-basketball/game.js?v=' in html
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
    avatar_css = ROOT.joinpath("static", "air-basketball", "avatar.css").read_text(encoding="utf-8")
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
    assert html.count('<link rel="preload" as="image" href="/static/air-basketball/assets/') == 3
    assert "window.NekoMiniGame.connect" in sdk_bootstrap
    assert "import(`./avatar-host.js${assetVersion}`)" in sdk_bootstrap
    assert "NekoMiniGame audio host is unavailable" in sdk_bootstrap
    assert "transport.getCharacter(identity.name)" in sdk_bootstrap
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
    assert "/static/locales/${locale}.json" in i18n
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
    assert "if (trackingPlayerNative && !trackedGuestSuspended)" in game
    assert "Math.min(.033, (now - lastFrame)" not in game
    assert "if (!nekoAiFrozen && state.nextNekoDecision <= 0)" in game
    assert "pageParams.get('test_mode') === '1'" in game
    assert "prepareIsolatedCrossTest" in game
    assert "position: fixed" in arcade_css
    assert ".mode-picker input:focus-visible + span" in arcade_css
    assert "@media (max-height: 680px)" in arcade_css
    assert "overflow-y: auto" in arcade_css
    assert "@keyframes cross-flight" not in arcade_css
    assert 'id="player-power-fill"' in html
    assert 'id="player-fever-fill"' in html
    assert 'id="neko-stage"' in html
    assert "iframe id=\"game-chat-frame\"" in html
    assert "`/chat?${params}`" in chat
    assert "window.prepareAirBasketballChat = prepareChatBridge" in chat
    assert "airBasketballAudioBridgeReady" not in chat
    assert "AudioContext" not in game
    assert "AudioContext" not in sdk_bootstrap
    assert 'loading="lazy"' not in html
    assert 'class="game-chat is-closed"' in html
    assert "dock.classList.contains('is-closed')" in chat
    limited_pages = main_server.split("_MAIN_LIMITED_MODE_ALLOWED_PAGE_PATHS = {", 1)[1].split("}", 1)[0]
    assert '"/air_basketball"' not in limited_pages
    reserved_routes = character_names.split("RESERVED_ROUTE_NAMES = frozenset({", 1)[1].split("})", 1)[0]
    assert '"air_basketball"' in reserved_routes
    expected_keys = {
        "title", "gestureHint", "chaosBall", "opponentReady", "voiceOpening",
        "avatarUnavailable", "arenaLabel", "closeChat", "mouseStealCaught",
        "mouseStealEscape", "voiceMouseSteal",
    }
    for locale in ("en", "es", "ja", "ko", "pt", "ru", "zh-CN", "zh-TW"):
        payload = json.loads(
            ROOT.joinpath("static", "locales", f"{locale}.json").read_text(encoding="utf-8")
        )
        assert expected_keys <= payload["airBasketball"].keys()
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
