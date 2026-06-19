import json
import re
from pathlib import Path

import pytest

from config.prompts import prompts_game
from main_routers import game_router, pages_router


ROOT = Path(__file__).resolve().parents[2]
BADMINTON_TEMPLATE = ROOT / "templates" / "badminton_demo.html"
BADMINTON_RACKET_SPRITE = ROOT / "static" / "game" / "games" / "badminton" / "images" / "badminton-racket-sprite.svg"
LOCALES_DIR = ROOT / "static" / "locales"


def _badminton_html() -> str:
    return BADMINTON_TEMPLATE.read_text(encoding="utf-8")


class _FakePageRequest:
    def __init__(self, query_params: dict | None = None):
        self.query_params = query_params or {}


class _FakeTemplates:
    def TemplateResponse(self, template_name: str, context: dict):
        return {"template_name": template_name, "context": context}


def _get_nested(payload: dict, dotted_key: str):
    node = payload
    for part in dotted_key.split("."):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node


@pytest.mark.unit
def test_badminton_improvement_static_contract():
    html = _badminton_html()
    racket_sprite = BADMINTON_RACKET_SPRITE.read_text(encoding="utf-8")

    for expected in (
        "/static/i18n-i18next.js",
        "/static/game/system/game-audio-system.js",
        "/static/game/games/badminton/badminton-audio-config.js",
        'id="game-audio-controls"',
        'id="game-bgm-volume"',
        'id="game-sfx-volume"',
        'function _i18n(',
        "window.addEventListener('localechange'",
        'id="bd-debug-panel"',
        "data-debug-distance",
        "data-debug-event",
        "function updateDebugReadout()",
        "window.BadmintonDemo =",
        "onEvent: function",
        "offEvent: function",
        "DUEL_DIFFICULTY",
        "function setDuelDifficulty(",
        "MOODS =",
        "function setMood(",
        "function syncBgm(",
        "function _bdResolvePlaylist(",
        "function resetSyncKey(",
        "badmintonGameAudio.resetSyncKey()",
        "badmintonGameAudio.sync(",
        "function scheduleAudioPreload",
        "function autoAdjustMood(",
        "function getPressureLine(",
        "function applyPreGameContext(",
        "function _badmintonGameMemoryPolicyPayload(",
        "game_memory_enabled",
        "loadGeneratedQuickLines",
        "userReplyProtectedUntil",
        "function buildGameSummary()",
        "game_summary",
        'data-tab="duel"',
        "function getFilteredLeaderboard(",
        "function drawStreakEffect(",
        "function drawFireBorder(",
        "function drawBackspinBall(",
        "THEMES =",
        "function cycleTheme(",
        "function checkSeasonalEasterEggs(",
        "function recordShotHistory(",
        "function showStatsPanel(",
        "function emitCardEvent(",
        "card_eligible",
        "firstTutorialShotGuaranteed",
    ):
        assert expected in html

    assert 'data-mode="horse"' not in html
    assert 'data-i18n="badminton.mode.horse"' not in html
    assert "requestedMode === 'horse'" not in html
    assert "nextMode === 'horse'" not in html
    assert 'data-mode="timed"' not in html
    assert 'data-i18n="badminton.mode.timed"' not in html
    assert "requestedMode === 'timed'" not in html
    assert "nextMode === 'timed'" not in html
    assert 'data-mode="spectator"' not in html
    assert 'data-mode="shooter"' not in html
    assert "#mode-switcher" not in html
    assert "modeSwitcher" not in html
    assert "switchBadmintonMode" not in html
    assert "updateModeSwitcher" not in html
    assert "YUI_PASSIVE_LINES_SHOOTER" not in html
    assert "function shouldCallLLMShooter(" not in html
    assert "自由练习" not in html
    assert "挥拍挑战" not in html
    assert 'viewBox="0 0 78 168"' in racket_sprite
    assert '<clipPath id="headClip">' in racket_sprite
    assert 'clip-path="url(#headClip)"' in racket_sprite
    assert 'M39 8 C23 8 13 22 13 42' in racket_sprite
    assert 'M39 78 L39 129' in racket_sprite


@pytest.mark.unit
def test_badminton_timed_mode_is_removed_from_template_runtime():
    html = _badminton_html()

    for removed in (
        "TIME_ATTACK_DURATION",
        "function isTimeAttackMode()",
        "currentMode === 'timed'",
        "_i18n('result.timed'",
        "_i18n('hud.timedTitle'",
        "_i18n('leaderboard.mode.timed'",
        "timedRemaining",
        "timedDeadline",
        "timedTimerStarted",
    ):
        assert removed not in html


@pytest.mark.unit
def test_badminton_horse_mode_is_removed_from_template_runtime():
    html = _badminton_html()

    for removed in (
        "HORSE_WORD",
        "function isHorseMode()",
        "function buildHorseStatePayload()",
        "function buildHorseFinalScorePayload(",
        "function startHorseNekoChallenge()",
        "function finishHorseShot(",
        "function horseLetters(",
        "function endHorseIfNeeded()",
        "currentMode === 'horse'",
        "game.horse",
        "horse_phase",
        "_i18n('result.horse'",
        "_i18n('hud.horseTitle'",
        "_i18n('debug.readout.horse'",
        "_i18n('lines.horse.",
    ):
        assert removed not in html


@pytest.mark.unit
def test_badminton_invite_character_request_uses_invited_lanlan_name():
    html = _badminton_html()

    assert "window.__nekoBadmintonQueryLanlanName = queryLanlan || '';" in html
    assert "lanlan_name: queryLanlan || ''" in html
    assert "lanlan_name: queryLanlan || 'badminton_demo'" not in html
    assert "var requestedLanlanName = String(window.__nekoBadmintonQueryLanlanName || '').trim();" in html
    assert "characterPath += '?lanlan_name=' + encodeURIComponent(requestedLanlanName);" in html
    assert "var charResp = await fetch(characterPath);" in html
    assert "var live2dPath = charData.live2d_path || '/static/yui-origin/yui-origin.model3.json';" in html
    assert "window.lanlan_config.model_type = 'live2d';" in html
    assert "window.lanlan_config.live3d_sub_type = '';" in html
    assert "await initLive2DAvatar(live2dPath);" in html


@pytest.mark.unit
def test_badminton_i18n_placeholder_token_avoids_jinja_braces():
    html = _badminton_html()

    assert "{% raw %}" in html
    assert "{% endraw %}" in html
    assert "return s.replace('{{' + k + '}}', String(params[k]));" not in html
    assert "var token = '{' + '{' + k + '}' + '}';" in html
    assert "return s.split(token).join(String(params[k]));" in html


@pytest.mark.unit
def test_badminton_hidden_tab_keeps_route_alive():
    html = _badminton_html()

    assert "window.addEventListener('beforeunload', function () { endRoute(true); });" in html
    assert "var pageVisible = !document.hidden;" in html
    assert "visible: pageVisible" in html
    assert "pageVisible: pageVisible" in html
    assert "visibilityState: document.visibilityState || (pageVisible ? 'visible' : 'hidden')" in html
    assert "if (document.hidden) endRoute(true);" not in html


@pytest.mark.unit
def test_badminton_invite_launches_duel_mode_and_marks_started():
    html = _badminton_html()

    assert "launchedFromInvite" not in html
    assert "badmintonInviteRequired" not in html
    assert "var currentMode = 'duel';" in html
    assert "launchedFromInvite ? 'duel' : 'shooter'" not in html
    assert "gameStarted: true, game_started: true" in html


@pytest.mark.unit
def test_badminton_rejected_route_start_does_not_activate_frontend_route():
    html = _badminton_html()
    start = html.index("function startRoute() {")
    end = html.index("function startRouteAfterCharacterReady() {", start)
    start_route = html[start:end]

    assert "if (!res || !res.ok) {" in start_route
    assert "routeActive = false;" in start_route
    assert start_route.index("if (!res || !res.ok) {") < start_route.index("routeActive = true;")
    assert start_route.index("routeActive = true;") < start_route.index("heartbeatTimer = setInterval")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_badminton_demo_page_only_renders_shell_without_invite_gate(monkeypatch):
    monkeypatch.setattr(pages_router, "get_templates", lambda: _FakeTemplates())

    result = await pages_router.badminton_demo(_FakePageRequest())

    assert result["template_name"] == "templates/badminton_demo.html"


@pytest.mark.unit
def test_badminton_restart_rotates_route_session():
    html = _badminton_html()

    assert "function createBadmintonSessionId() {" in html
    assert "var sessionId = window.__nekoMiniGameInviteSessionId || createBadmintonSessionId();" in html
    assert "sessionId = createBadmintonSessionId();" in html
    assert "resetVoiceArbiter();" in html
    assert "switchBadmintonMode" not in html
    assert "url.searchParams.delete('session_id');" not in html
    assert "startRoute();" in html


@pytest.mark.unit
def test_badminton_personal_stats_ignore_neko_shots():
    html = _badminton_html()

    assert "if (resultEntry && resultEntry.shooter && resultEntry.shooter !== 'player') return;" in html
    assert "recordShotHistory(game.attemptsResults[game.attemptsResults.length - 1]);" in html


@pytest.mark.unit
def test_badminton_duel_applies_llm_difficulty_before_neko_shot():
    html = _badminton_html()

    assert "var pendingControl = game.duel.pendingVoiceControl || null;" in html
    assert "if (pendingControl && pendingControl.difficulty) setDuelDifficulty(pendingControl.difficulty);" in html
    assert "var shot = getNekoDuelShot();" in html
    assert "var shot = game.duel.pendingShot || getNekoDuelShot();" not in html


@pytest.mark.unit
def test_badminton_audio_config_contract():
    source = (ROOT / "static" / "game" / "games" / "badminton" / "badminton-audio-config.js")
    assert source.exists()
    text = source.read_text(encoding="utf-8")
    assert "gameSystem.badminton.audioConfig" in text
    assert "audioMix" in text
    assert "line_in" in text
    assert "net" in text
    assert "Battle_Theme_1_L.mp3" in text
    assert "badminton-racket-shuttlecock-0537.mp3" in text
    assert "badminton-racket-shuttlecock-single.mp3" in text
    for index in range(1, 5):
        assert f"badminton-racket-shuttlecock-hit-{index}.mp3" in text
    assert "zapsplat_sport_badminton_racket_fast_swing_whoosh_001_76396.mp3" in text
    assert "whoosh: [{ src: racketSwing" in text
    assert "var racketShuttleHits = [" in text
    assert "var racketShuttleSingle =" in text
    assert "shuttleContact: racketShuttleHits.concat([racketShuttleSingle]).map" in text


@pytest.mark.unit
def test_badminton_i18n_keys_are_registered_in_main_locales():
    required_keys = {
        "badminton.title",
        "badminton.modeSwitcher",
        "badminton.audio.controls",
        "badminton.memoryOption.label",
        "badminton.memoryOption.hint",
        "badminton.mode.spectator",
        "badminton.mode.duel",
        "badminton.hud.score",
        "badminton.hud.streak",
        "badminton.hud.record",
        "badminton.hud.duelScore",
        "badminton.hud.duelMisses",
        "badminton.hud.round",
        "badminton.hud.timer",
        "badminton.hud.practice",
        "badminton.hud.yourTurn",
        "badminton.hud.nekoTurn",
        "badminton.hud.unlimitedAttempts",
        "badminton.hud.chances",
        "badminton.hud.practiceTitle",
        "badminton.hud.attemptsTitle",
        "badminton.hud.duelTitle",
        "badminton.hud.on",
        "badminton.hud.off",
        "badminton.result.title",
        "badminton.result.leaderboard",
        "badminton.result.stats",
        "badminton.result.retry",
        "badminton.result.rating",
        "badminton.result.duel",
        "badminton.result.duelElimination",
        "badminton.result.practice",
        "badminton.result.summary",
        "badminton.result.attemptsSummary",
        "badminton.result.personalBest",
        "badminton.result.globalRank",
        "badminton.result.outcome.youWin",
        "badminton.result.outcome.nekoWin",
        "badminton.result.outcome.tie",
        "badminton.result.outcome.undecided",
        "badminton.leaderboard.title",
        "badminton.leaderboard.global",
        "badminton.leaderboard.local",
        "badminton.leaderboard.shooter",
        "badminton.leaderboard.duel",
        "badminton.leaderboard.empty",
        "badminton.leaderboard.totalPlayers",
        "badminton.leaderboard.yourBest",
        "badminton.leaderboard.recent",
        "badminton.leaderboard.loading",
        "badminton.leaderboard.mode.duel",
        "badminton.table.score",
        "badminton.table.bestStreak",
        "badminton.table.farthest",
        "badminton.table.mode",
        "badminton.table.date",
        "badminton.debug.title",
        "badminton.debug.collapse",
        "badminton.debug.hide",
        "badminton.debug.distance",
        "badminton.debug.power",
        "badminton.debug.event",
        "badminton.debug.streak",
        "badminton.debug.reset",
        "badminton.debug.guide",
        "badminton.debug.sweet",
        "badminton.debug.markers",
        "badminton.debug.readout.modeState",
        "badminton.debug.readout.distance",
        "badminton.debug.readout.streaks",
        "badminton.debug.readout.score",
        "badminton.debug.readout.difficulty",
        "badminton.debug.readout.duel",
        "badminton.state.ready",
        "badminton.state.in_flight",
        "badminton.state.game_over",
        "badminton.state.neko_thinking",
        "badminton.stats.title",
        "badminton.stats.close",
        "badminton.stats.totalShots",
        "badminton.stats.farRate",
        "badminton.stats.trend",
        "badminton.stats.none",
        "badminton.theme.next",
        "badminton.theme.current",
        "badminton.theme.changed",
        "badminton.theme.labels.default",
        "badminton.theme.labels.miami",
        "badminton.court.netFront",
        "badminton.court.serviceLine",
        "badminton.court.backCourt",
        "badminton.court.baseline",
        "badminton.toast.difficulty",
        "badminton.toast.nekoShoot",
        "badminton.toast.nekoThinking",
        "badminton.toast.nekoTurn",
        "badminton.toast.copyNeko",
        "badminton.toast.nekoFailed",
        "badminton.toast.yourSet",
        "badminton.toast.yourTurn",
        "badminton.toast.reset",
        "badminton.toast.featureToggled",
        "badminton.toast.feature.guide",
        "badminton.toast.feature.sweet",
        "badminton.toast.feature.bgm",
        "badminton.toast.feature.markers",
        "badminton.toast.state.on",
        "badminton.toast.state.off",
        "badminton.tutorial.aim",
        "badminton.tutorial.charge",
        "badminton.tutorial.release",
        "badminton.shot.line_in",
        "badminton.shot.net_touch",
        "badminton.shot.zone_in",
        "badminton.shot.out",
        "badminton.shot.net",
        "badminton.shot.unknown",
        "badminton.shot.attempt",
        "badminton.lines.fallback",
        "badminton.lines.default.line_in",
        "badminton.lines.shooter.line_in",
        "badminton.lines.duel.line_in",
        "badminton.lines.pressure.lastTied",
        "badminton.lines.pressure.lastAhead",
        "badminton.lines.pressure.lastBehind",
        "badminton.lines.pressure.playerAhead",
        "badminton.lines.pressure.playerBehind",
        "badminton.lines.duel.clutch",
        "badminton.lines.duel.excuse",
        "badminton.lines.mindGame",
        "badminton.lines.easterEgg.lateNight",
        "badminton.lines.easterEgg.xmas",
        "badminton.lines.easterEgg.newYear",
        "badminton.lines.easterEgg.lineIn3",
        "badminton.lines.easterEgg.lineIn5",
        "badminton.lines.easterEgg.net3",
        "badminton.mood.happySuffix",
        "badminton.mood.sadPrefix",
        "badminton.mood.surprisedPrefix",
        "badminton.close",
    }
    line_keys = {
        "line_in",
        "net_touch",
        "zone_in",
        "out",
        "net",
        "shot_missed",
        "game_over",
        "long_aim",
        "close_to_record",
        "new_record",
        "streak_5",
        "streak_10",
        "streak_15",
        "streak_20",
    }
    required_keys.update(
        f"badminton.lines.{group}.{line_key}"
        for group in ("default", "shooter", "duel")
        for line_key in line_keys
    )
    required_keys = {
        key.replace("badminton.", "badminton.", 1)
        for key in required_keys
    }

    for locale_path in LOCALES_DIR.glob("*.json"):
        payload = json.loads(locale_path.read_text(encoding="utf-8"))
        missing = sorted(key for key in required_keys if _get_nested(payload, key) is None)
        assert not missing, f"{locale_path.name} missing badminton i18n keys: {missing}"


@pytest.mark.unit
def test_badminton_runtime_visible_text_uses_i18n_helpers():
    html = _badminton_html()

    expected_i18n_references = (
        "_i18n('leaderboard.empty'",
        "_i18n('result.duelElimination'",
        "_i18n('result.summary'",
        "_i18n('theme.current'",
        "_i18n('toast.nekoShoot'",
        "_i18n('tutorial.aim'",
        "_i18nArray('lines.mindGame'",
    )
    for expected in expected_i18n_references:
        assert expected in html

    expected_static_hooks = (
        'data-i18n="badminton.leaderboard.title"',
        'data-i18n="badminton.leaderboard.global"',
        'data-i18n="badminton.leaderboard.local"',
        'data-i18n="badminton.leaderboard.duel"',
        'data-i18n="badminton.table.score"',
        'data-i18n="badminton.table.bestStreak"',
        'data-i18n="badminton.table.farthest"',
        'data-i18n="badminton.table.mode"',
        'data-i18n="badminton.table.date"',
        'data-i18n="badminton.close"',
    )
    for expected in expected_static_hooks:
        assert expected in html

    forbidden_direct_visible_text = (
        "showAssistHint('Neko出手')",
        "showAssistHint('已重置')",
        "leaderboardMeta.textContent = '加载中...'",
        "emptyCell.textContent = '暂无记录'",
        "resultStats.textContent = '对战结果：'",
        "if (themeButton) themeButton.textContent = '主题：'",
        "updateTutorial('上下移动鼠标调整挥拍角度')",
    )
    for snippet in forbidden_direct_visible_text:
        assert snippet not in html


@pytest.mark.unit
def test_badminton_backspin_trigger_is_more_forgiving_than_perfect_shot():
    html = _badminton_html()

    assert "function getBackspinRate(" in html
    assert "function buildShuttleSpinRate(launchAngle, power, direction, impulse) {" in html
    assert "var angleTolerance = 6;" in html
    assert "var powerPadding = 5;" in html
    assert "return 4 + quality * 5;" in html
    assert "var baseSpinRate = direction * getBackspinRate(launchAngle, power, game.distance);" in html
    assert "var contactSpinRate = impulse.incomingSpeed ? direction * (10 + impulse.quality * 7 + clamp((impulse.incomingSpeed || 0) / 150, 0, 6)) : 0;" in html
    assert "var rawSpinRate = baseSpinRate + incomingSpinRate + contactSpinRate + smashSpinRate;" in html
    assert "var maxSpinRate = direction > 0 ? 10 : 24;" in html
    assert "return clamp(rawSpinRate, -maxSpinRate, maxSpinRate);" in html
    assert "yuiReturnSpinRate" not in html

    perfect_start = html.index("function isPerfect(")
    perfect_end = html.index("function isDuelMode(")
    perfect_section = html[perfect_start:perfect_end]
    assert "<= 2" in perfect_section
    assert "powerPadding" not in perfect_section


@pytest.mark.unit
def test_badminton_court_distances_use_badminton_line_calibration():
    html = _badminton_html()

    for expected in (
        "var BADMINTON_COURT_METERS =",
        "netFront: 1.22",
        "serviceLine: 4.19",
        "backCourt: 7.24",
        "baseline: 12.73",
        "var PX_PER_METER = BADMINTON_COURT_METERS.pxPerMeter",
        "function metersToShotPx(",
        "var COURT_DISTANCES =",
        "serviceLine: metersToShotPx(BADMINTON_COURT_METERS.serviceLine)",
        "backCourt: metersToShotPx(BADMINTON_COURT_METERS.backCourt)",
        "baseline: metersToShotPx(BADMINTON_COURT_METERS.baseline)",
        "netFront: metersToShotPx(BADMINTON_COURT_METERS.netFront)",
        "var COURT_DISTANCE_MARKS =",
        "data-debug-distance-key=\"netFront\"",
        "data-debug-distance-key=\"serviceLine\"",
        "data-debug-distance-key=\"backCourt\"",
        "data-debug-distance-key=\"baseline\"",
        "function refreshCourtDistanceButtons(",
    ):
        assert expected in html

    court_start = html.index("function drawCourt(")
    court_end = html.index("function drawStreakEffect(")
    court_section = html[court_start:court_end]
    for stale_legacy_court_marker in (
        "var laneLeftX = hoopCenterX - 252;",
        "var threeX = hoopCenterX - 405;",
        "var midCourtX = Math.max(86, threeX - 260);",
        "var restrictedRadiusX = 74;",
        "ctx.ellipse(hoopCenterX, BASE_H - 3, 405, 76",
        "hoopCenterX",
        "freeThrow",
        "threePoint",
        "midCourt",
        "restricted",
    ):
        assert stale_legacy_court_marker not in court_section

    for stale_legacy_court_color in (
        "#ffc36b",
        "#ff8f3d",
        "#c94f1e",
    ):
        assert stale_legacy_court_color not in court_section

    for stale_debug_distance in (
        'data-debug-distance="150"',
        'data-debug-distance="300"',
        'data-debug-distance="450"',
        'data-debug-distance="600"',
    ):
        assert stale_debug_distance not in html


@pytest.mark.unit
def test_badminton_shot_distance_caps_one_step_beyond_baseline():
    html = _badminton_html()

    assert "var POST_BASELINE_DISTANCE_STEP = 45;" in html
    assert (
        "var MAX_PLAYABLE_SHOT_DISTANCE = COURT_DISTANCES.baseline + POST_BASELINE_DISTANCE_STEP;"
        in html
    )
    assert "function getMaxPlayableShotDistance() {" in html
    assert "return MAX_PLAYABLE_SHOT_DISTANCE;" in html
    assert "function advanceShotDistance(step) {" in html
    assert "return Math.min(getMaxPlayableShotDistance(), game.distance + (Number(step) || 0));" in html
    assert "game.distance = advanceShotDistance(nextDistanceStep());" in html
    assert "game.distance = advanceShotDistance(POST_BASELINE_DISTANCE_STEP);" in html
    assert "clamp(Number(distance) || 200, 80, getMaxPlayableShotDistance())" in html
    assert "clamp(Number(px) || 200, 80, getMaxPlayableShotDistance())" in html
    assert "game.distance += nextDistanceStep();" not in html
    assert "Math.min(620, game.distance + 45)" not in html


@pytest.mark.unit
def test_badminton_llm_control_contract_accepts_mood_and_difficulty():
    parsed = game_router._parse_control_instructions(
        '认真点喵\n{"mood":"angry","expression":"tease","intensity":"high","difficulty":"max"}',
        game_type="badminton",
    )

    assert parsed == {
        "line": "认真点喵",
        "control": {
            "mood": "angry",
            "expression": "tease",
            "intensity": "high",
            "difficulty": "max",
        },
    }


@pytest.mark.unit
def test_badminton_duel_prompt_mentions_difficulty_control():
    prompt = prompts_game.get_badminton_system_prompt("zh", mode="duel")

    assert "difficulty" in prompt
    assert "max, lv2, lv3, lv4" in prompt


@pytest.mark.unit
@pytest.mark.parametrize("mode", ("spectator", "shooter", "timed", "horse"))
@pytest.mark.parametrize("lang", ("zh", "en", "ja", "ko", "ru", "es", "pt"))
def test_badminton_non_duel_prompts_do_not_advertise_difficulty_control(lang, mode):
    prompt = prompts_game.get_badminton_system_prompt(lang, mode=mode)

    assert '"difficulty"' not in prompt
    assert "max, lv2, lv3, lv4" not in prompt


@pytest.mark.unit
@pytest.mark.parametrize("lang", ("zh", "en", "ja", "ko", "ru", "es", "pt"))
def test_badminton_horse_system_prompt_does_not_inherit_duel_rules(lang):
    prompt = prompts_game.get_badminton_system_prompt(lang, mode="horse")

    assert "event.mode=horse" in prompt
    assert "event.mode=duel" not in prompt
    assert "player_duel_shot" not in prompt
    assert "neko_duel_shot" not in prompt
    assert "neko_duel_turn" not in prompt
    assert "duel.player_score" not in prompt
    assert "duel.neko_score" not in prompt


@pytest.mark.unit
def test_badminton_horse_system_prompt_uses_horse_end_contract():
    zh = prompts_game.get_badminton_system_prompt("zh", mode="horse")
    en = prompts_game.get_badminton_system_prompt("en", mode="horse")

    assert "本局共有三次失误机会" not in zh
    assert "三次机会用完" not in zh
    assert "attempts_remaining" not in zh
    assert "HORSE 字母已经结算出胜负" in zh
    assert "the run has three miss chances" not in en
    assert "all three chances are gone" not in en
    assert "attempts_remaining" not in en
    assert "HORSE letters have decided the result" in en


@pytest.mark.unit
def test_badminton_horse_system_prompt_matches_chat_event_payload():
    zh = prompts_game.get_badminton_system_prompt("zh", mode="horse")
    en = prompts_game.get_badminton_system_prompt("en", mode="horse")

    assert "只有复刻失败的一方吃到 HORSE 字母" in zh
    assert "出题失败只是换对方出题" in zh
    assert "只有复刻失败才描述谁吃到字母" in zh
    assert "currentState.attempts_results 最后一条的 horse_phase" in zh
    assert "不要用 event.horse.phase 判断" in zh
    assert "结合 winner" not in zh
    assert "winner 字段" in zh
    assert "only a side that fails a copy attempt takes a HORSE letter" in en
    assert "failed setup just passes setup to the other side" in en
    assert "mention a letter only for failed copy attempts" in en
    assert "last currentState.attempts_results entry's horse_phase" in en
    assert "do not infer it from event.horse.phase" in en
    assert "summarize with winner" not in en
    assert "do not rely on a winner field" in en


@pytest.mark.unit
@pytest.mark.parametrize("lang", ("zh", "en", "ja", "ko", "ru", "es", "pt"))
def test_badminton_quick_lines_mode_prompts_are_distinct_and_localized(lang):
    spectator = prompts_game.get_badminton_quick_lines_prompt(lang, mode="spectator")

    for mode in ("duel", "shooter", "timed", "horse"):
        prompt = prompts_game.get_badminton_quick_lines_prompt(lang, mode=mode)
        assert prompt != spectator
        if lang != "en":
            assert "Current mode is" not in prompt


@pytest.mark.unit
def test_badminton_english_quick_lines_do_not_mix_mode_suffixes():
    timed = prompts_game.get_badminton_quick_lines_prompt("en", mode="timed")
    horse = prompts_game.get_badminton_quick_lines_prompt("en", mode="horse")

    assert "Current mode is timed" in timed
    assert "Current mode is shooter" not in timed
    assert "Current mode is HORSE" in horse
    assert "Current mode is duel" not in horse


@pytest.mark.unit
def test_badminton_zh_horse_quick_lines_do_not_inherit_duel_prompt():
    prompt = prompts_game.get_badminton_quick_lines_prompt("zh", mode="horse")

    assert "当前模式是 HORSE" in prompt
    assert "羽毛球对战回合" not in prompt
    assert "轮流出手" not in prompt
    assert "比分和对战节奏" not in prompt
    assert "duel" not in prompt


@pytest.mark.unit
def test_badminton_prompt_localizations_do_not_fallback_to_english():
    english_spectator = prompts_game.get_badminton_system_prompt("en", mode="spectator")
    english_duel = prompts_game.get_badminton_system_prompt("en", mode="duel")
    english_shooter = prompts_game.get_badminton_system_prompt("en", mode="shooter")
    english_timed = prompts_game.get_badminton_system_prompt("en", mode="timed")
    english_horse = prompts_game.get_badminton_system_prompt("en", mode="horse")
    english_quick = prompts_game.get_badminton_quick_lines_prompt("en", mode="spectator")
    english_pregame = prompts_game.get_badminton_pregame_context_prompt("en")

    assert english_timed != english_spectator
    assert english_horse != english_spectator
    assert english_timed != english_shooter
    assert english_horse != english_duel
    assert "event.mode=timed" in english_timed
    assert "event.mode=horse" in english_horse
    assert prompts_game.get_badminton_quick_lines_prompt("zh", mode="timed") != (
        prompts_game.get_badminton_quick_lines_prompt("zh", mode="shooter")
    )
    assert prompts_game.get_badminton_quick_lines_prompt("zh", mode="horse") != (
        prompts_game.get_badminton_quick_lines_prompt("zh", mode="duel")
    )

    for lang in ("zh", "ja", "ko", "ru", "es", "pt"):
        assert prompts_game.get_badminton_system_prompt(lang, mode="spectator") != english_spectator
        assert prompts_game.get_badminton_system_prompt(lang, mode="duel") != english_duel
        assert prompts_game.get_badminton_system_prompt(lang, mode="shooter") != english_shooter
        assert prompts_game.get_badminton_system_prompt(lang, mode="timed") != english_timed
        assert prompts_game.get_badminton_system_prompt(lang, mode="horse") != english_horse
        assert prompts_game.get_badminton_quick_lines_prompt(lang, mode="spectator") != english_quick
        assert prompts_game.get_badminton_pregame_context_prompt(lang) != english_pregame


@pytest.mark.unit
def test_badminton_pregame_context_normalize_and_prompt_injection():
    context, invalid = game_router._normalize_badminton_pregame_context(
        {
            "gameStance": "competitive",
            "initialMood": "happy",
            "initialExpression": "hype",
            "initialIntensity": "high",
            "initialDifficulty": "max",
            "openingLine": "来比一局",
            "expressionPolicy": "更兴奋地盯着比分",
        },
        mode="duel",
    )

    assert invalid is False
    assert context["initialExpression"] == "hype"
    assert context["initialIntensity"] == "high"
    assert context["expressionPolicy"] == "更兴奋地盯着比分"

    prompt = game_router._build_game_prompt(
        "badminton",
        "Neko",
        "傲娇猫娘",
        pre_game_context=context,
        language="zh",
        mode="duel",
    )
    assert "羽毛球开局上下文" in prompt
    assert "来比一局" in prompt
    assert "对战难度控制补充" in prompt


@pytest.mark.unit
def test_badminton_pregame_opening_line_keeps_spec_length_cap():
    context, invalid = game_router._normalize_badminton_pregame_context(
        {
            "openingLine": "1234567890123456",
        },
        mode="spectator",
    )

    assert invalid is True
    assert context["openingLine"] == ""


@pytest.mark.unit
def test_badminton_duel_balance_hint_and_anger_cap():
    hint = game_router._build_badminton_duel_balance_hint(
        {"duel": {"player_score": 1, "neko_score": 6, "round": 2, "max_rounds": 8}}
    )
    assert hint["state"] == "neko_leading"
    assert hint["diff"] == 5
    assert hint["remainingPoints"] == 12

    final_pending = game_router._build_badminton_duel_balance_hint(
        {"duel": {"player_score": 6, "neko_score": 4, "round": 5, "max_rounds": 5, "active_shooter": "neko"}}
    )
    assert final_pending["state"] == "player_leading"
    assert final_pending["remainingRounds"] == 0
    assert final_pending["remainingPoints"] == 2

    miss_pressure = game_router._build_badminton_duel_balance_hint(
        {"duel": {"player_score": 4, "neko_score": 6, "round": 6, "player_misses": 2, "neko_misses": 1, "max_misses": 3}}
    )
    assert miss_pressure["playerMissesLeft"] == 1
    assert miss_pressure["nekoMissesLeft"] == 2
    assert miss_pressure["maxMisses"] == 3

    current_state_fallback = game_router._build_badminton_duel_balance_hint(
        {"currentState": {"duel": {"playerScore": 1, "nekoScore": 5, "playerMisses": 2, "nekoMisses": 0, "maxMisses": 3}}}
    )
    assert current_state_fallback["state"] == "neko_leading"
    assert current_state_fallback["diff"] == 4
    assert current_state_fallback["playerMissesLeft"] == 1

    merged_current_state = game_router._build_badminton_duel_balance_hint(
        {
            "duel": {"player_score": 4},
            "currentState": {"duel": {"playerScore": 1, "nekoScore": 6, "playerMisses": 2, "nekoMisses": 1, "maxMisses": 3}},
        }
    )
    assert merged_current_state["diff"] == 2
    assert merged_current_state["playerMissesLeft"] == 1
    assert merged_current_state["nekoMissesLeft"] == 2

    miss_elimination_ignores_round_decider = game_router._build_badminton_duel_balance_hint(
        {"duel": {"player_score": 0, "neko_score": 9, "round": 5, "max_rounds": 5, "player_misses": 1, "neko_misses": 1, "max_misses": 3}}
    )
    assert miss_elimination_ignores_round_decider["state"] == "neko_leading"

    route_state = {
        "preGameContext": {"gameStance": "punishing", "initialMood": "angry"},
        "anger_pressure_accumulated": 24,
    }
    event = {
        "kind": "shot_missed",
        "mode": "duel",
        "label": "player_duel_shot",
        "difficulty": "max",
        "duel": {"player_score": 1, "neko_score": 6, "round": 5, "max_rounds": 8},
    }
    cap = game_router._build_badminton_duel_anger_pressure_cap(event, route_state)
    assert cap["reached"] is True

    result = game_router._apply_badminton_anger_pressure_cap(
        {"line": "继续", "control": {"difficulty": "max"}},
        {**event, "angerPressureCap": cap},
    )
    assert result["control"]["difficulty"] == "lv3"
    assert result["anger_pressure_cap"]["adjusted"] is True


@pytest.mark.unit
def test_game_memory_generic_keys_update_legacy_policy_fields():
    state = {}
    game_router._update_game_memory_enabled_from_payload(
        state,
        {
            "game_memory_enabled": True,
            "game_memory_player_interaction_enabled": False,
            "game_memory_event_reply_enabled": True,
            "game_memory_archive_enabled": False,
            "game_memory_postgame_context_enabled": True,
        },
    )

    assert state["soccer_game_memory_enabled"] is True
    assert state["soccer_game_memory_player_interaction_enabled"] is False
    assert state["soccer_game_memory_event_reply_enabled"] is True
    assert state["soccer_game_memory_archive_enabled"] is False
    assert state["soccer_game_memory_postgame_context_enabled"] is True
    assert state["game_memory_enabled"] is True
    assert state["game_memory_archive_enabled"] is False


@pytest.mark.unit
def test_badminton_completed_result_records_score_before_returning():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert "function persistCompletedResult() {" in html
    assert "if (game.resultRecorded || isPracticeMode()) return null;" in html
    assert "var entry = recordGame(game.bestStreak, getRunMaxDistancePx(), game.totalScore, game.shotTypeCount);" in html
    assert "persistCompletedResult();" in html


@pytest.mark.unit
def test_badminton_scoring_waits_for_route_end_and_records_run_max():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert "function getRunMaxDistancePx() {" in html
    assert "var routeEndPromise = null;" in html
    assert "var completedSessionId = sessionId;" in html
    assert "var completedLanlanName = getRouteLanlanName();" in html
    assert "var routeEndReady = endedRoute && routeEndPromise ? routeEndPromise.catch(function () {}) : Promise.resolve();" in html
    assert "if (routeEndResult && routeEndResult.state) applyRouteIdentity(routeEndResult.state);" in html
    assert "var scoreLanlanName = completedLanlanName || getRouteLanlanName();" in html
    assert "session_id: completedSessionId," in html
    assert "lanlan_name: scoreLanlanName," in html
    assert "var entry = recordGame(game.bestStreak, getRunMaxDistancePx(), game.totalScore, game.shotTypeCount);" in html
    assert "routeEndPromise = fetch(url, { method: 'POST'" in html
    assert "return res.json().catch(function () { return { ok: res.ok }; });" in html

    session_capture_index = html.index("var completedSessionId = sessionId;")
    route_ready_index = html.index("var routeEndReady = endedRoute && routeEndPromise ? routeEndPromise.catch(function () {}) : Promise.resolve();")
    persist_index = html.index("function persistCompletedResult() {")
    record_index = html.index("var entry = recordGame(", persist_index)
    assert session_capture_index < route_ready_index
    assert route_ready_index < record_index


@pytest.mark.unit
def test_badminton_reset_abandons_active_route_before_rotating_session():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")
    reset_start = html.index("function resetGame() {")
    reset_section = html[reset_start:html.index("function updateHud()", reset_start)]
    end_route_start = html.index("function endRoute(")
    end_route_section = html[end_route_start:html.index("function cycleTheme()", end_route_start)]

    assert "var shouldRestartRoute = endedRoute || routeActive || heartbeatTimer || drainTimer;" in reset_section
    assert "if (!endedRoute) endRoute(false);" in reset_section
    assert "sessionId = createBadmintonSessionId();" in reset_section
    assert "startRoute();" in reset_section
    assert reset_section.index("if (!endedRoute) endRoute(false);") < reset_section.index("sessionId = createBadmintonSessionId();")
    assert "routeActive = false;" in end_route_section
    assert "var routeEndSessionId = sessionId;" in end_route_section
    assert "if (res && res.state && sessionId === routeEndSessionId) applyRouteIdentity(res.state);" in end_route_section


@pytest.mark.unit
def test_badminton_generated_quick_lines_override_static_i18n_lines():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert "var generatedQuickLines = {};" in html
    assert "var generated = generatedQuickLines[key] || [];" in html
    assert "if (generated.length) return generated[Math.floor(Math.random() * generated.length)] || '';" in html
    assert "generatedQuickLines[key] = pool;" in html


@pytest.mark.unit
def test_badminton_route_end_payload_contains_archive_score():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert "finalScore: {" in html
    assert "player: game.totalScore," in html
    assert "ai: isDuelMode() ? game.duel.nekoScore : 0," in html
    assert "var roundCompleted = game.state === 'game_over';" in html
    assert "reason: roundCompleted ? 'badminton_game_over' : 'badminton_abandoned'," in html
    assert "roundCompleted: roundCompleted," in html
    assert "round_completed: roundCompleted," in html
    assert "postgameProactive: roundCompleted," in html
    assert "state: game.state," in html
    assert "currentState: {\n        game: 'badminton',\n        state: game.state,\n        mode: currentMode,\n        score: {" in html
    assert "max_distance_px: getRunMaxDistancePx()," in html


@pytest.mark.unit
def test_badminton_chat_replies_are_ignored_after_session_or_mode_changes():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")
    send_event_start = html.index("function sendGameEvent(")
    send_event = html[send_event_start:html.index("function loadLocalLeaderboard(", send_event_start)]

    stale_reply_guard = "if (event.session_id !== sessionId || event.mode !== currentMode) return;"
    assert stale_reply_guard in send_event
    guard_index = send_event.index(stale_reply_guard)
    control_index = send_event.index("if (res && res.control) {")
    line_index = send_event.index("if (res && res.line) speakLine(")
    assert guard_index < control_index
    assert guard_index < line_index
    catch_index = send_event.index(".catch(function () {")
    catch_guard_index = send_event.index(stale_reply_guard, catch_index)
    assert catch_index < catch_guard_index


@pytest.mark.unit
def test_badminton_route_start_timeout_covers_backend_pregame_generation():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert "_badmintonGameMemoryPolicyPayload()), 22000).then(function (res) {" in html


@pytest.mark.unit
def test_badminton_heartbeat_sends_live_current_state():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")
    heartbeat_index = html.index("post('/route/heartbeat'")
    heartbeat_section = html[max(0, heartbeat_index - 500):heartbeat_index + 500]
    current_state_start = html.index("function buildBadmintonCurrentStatePayload() {")
    current_state_section = html[current_state_start:html.index("function sendGameEvent(", current_state_start)]

    assert "post('/route/heartbeat'" in heartbeat_section
    assert "currentState: buildBadmintonCurrentStatePayload()" in heartbeat_section
    assert re.search(r"score:\s*{\s*player:\s*game\.totalScore", current_state_section)
    assert re.search(
        r"ai:\s*isDuelMode\(\)\s*\?\s*game\.duel\.nekoScore\s*:\s*0",
        current_state_section,
    )
    assert re.search(r"total_score:\s*game\.totalScore", current_state_section)


@pytest.mark.unit
def test_badminton_memory_toggle_does_not_auto_enable_from_history():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")
    init_start = html.index("function _initBadmintonGameMemoryToggle() {")
    init_section = html[init_start:html.index("function getAudioCtx()", init_start)]

    assert "gameMemoryToggle.checked = false;" in init_section
    assert "_hasHistoricalBadmintonRecord" not in html
    assert "bd_record_distance" not in init_section
    assert "bd_leaderboard" not in init_section


@pytest.mark.unit
def test_badminton_duel_player_shots_update_recorded_stats():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")
    finish_duel = html[html.index("function finishDuelShot("):html.index("function finishShot(", html.index("function finishDuelShot("))]

    assert "if (shooter === 'player') {" in finish_duel
    assert "game.streak += 1;" in finish_duel
    assert "game.madeCount += 1;" in finish_duel
    assert "game.bestStreak = Math.max(game.bestStreak, game.streak);" in finish_duel
    assert "if (game.shotTypeCount[shotType] != null) game.shotTypeCount[shotType] += 1;" in finish_duel
    assert "newRecord = previousDistance > game.recordDistance;" in finish_duel
    assert "game.recordDistance = previousDistance;" in finish_duel
    assert "localStorage.setItem('bd_record_distance', String(Math.round(game.recordDistance)));" in finish_duel
    assert "kind: newRecord ? 'new_record' : (scored ? 'shot_result' : 'shot_missed')," in finish_duel
    assert "is_new_record: newRecord," in finish_duel
    assert "game.streak = 0;" in finish_duel


@pytest.mark.unit
def test_badminton_duel_uses_three_miss_elimination_instead_of_five_round_cap():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")
    finish_duel = html[html.index("function finishDuelShot("):html.index("function finishShot(", html.index("function finishDuelShot("))]

    assert "playerMisses: 0," in html
    assert "nekoMisses: 0," in html
    assert "maxMisses: 3," in html
    assert "game.duel.playerMisses = 0;" in html
    assert "game.duel.nekoMisses = 0;" in html
    assert "point = 1;" in finish_duel
    assert "pointWinner = scored ? shooter : (shooter === 'player' ? 'neko' : 'player');" in finish_duel
    assert "if (shooter === 'player') game.duel.nekoMisses += 1;" in finish_duel
    assert "if (shooter === 'player') game.duel.playerMisses += 1;" in finish_duel
    assert "else game.duel.nekoMisses += 1;" in finish_duel
    duel_finished = "var duelFinished = game.duel.playerMisses >= game.duel.maxMisses || game.duel.nekoMisses >= game.duel.maxMisses;"
    assert duel_finished in finish_duel
    assert finish_duel.index(duel_finished) < finish_duel.index("kind: newRecord ? 'new_record' : (scored ? 'shot_result' : 'shot_missed'),")
    assert "if (!duelFinished) {\n      sendGameEvent({" in finish_duel
    assert "} else if (pointWinner === 'neko') {" in finish_duel
    assert "player_misses: game.duel.playerMisses" in html
    assert "neko_misses: game.duel.nekoMisses" in html
    assert "max_misses: game.duel.maxMisses" in html
    assert "result: scored ? 'scored' : 'missed'" in finish_duel
    assert "duel_outcome: didPlayerWinDuel() ? 'player_win' : 'neko_win'" in finish_duel
    assert "game.duel.playerScore > game.duel.nekoScore" not in html
    assert "isDuelMode() && isDuelEliminated() && didPlayerWinDuel()" in html
    assert "_i18n('hud.duelMisses'" in html
    assert "_i18n('result.duelElimination'" in html
    assert "maxRounds: 5" not in html
    assert "game.duel.round >= game.duel.maxRounds" not in html
    assert "max_rounds: game.duel.maxRounds" not in html


@pytest.mark.unit
def test_badminton_normal_chat_request_carries_client_timeout_for_memory_guard():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")
    chat_start = html.index("var chatClientTimeoutMs = 6500;")
    chat_section = html[chat_start:html.index(".then(function (res) {", chat_start)]

    assert "client_timeout_ms: chatClientTimeoutMs" in chat_section
    assert "}), chatClientTimeoutMs)" in chat_section


@pytest.mark.unit
def test_badminton_user_reply_voice_deadline_survives_inflight_guard():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")
    speak_start = html.index("function speakLine(line, control, event) {")
    speak_section = html[speak_start:html.index("function getActiveAvatarContainer()", speak_start)]

    assert "if (isUserReply) {" in speak_section
    assert "voiceArbiter.inFlight.expiresAt + VOICE_ARBITER_DEFAULTS.tailWaitMs" in speak_section
    assert "entry.expiresAt = Math.max(" in speak_section


@pytest.mark.unit
def test_badminton_drain_reads_nested_result_line():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert "var result = item && typeof item.result === 'object' ? item.result : null;" in html
    assert "(result && (result.line || result.text || result.content))" in html
    assert "var control = (item && item.control) || (result && result.control) || {};" in html
    assert "speakLine(line, control, Object.assign({" in html
    assert "kind: 'user_reply'," in html


@pytest.mark.unit
def test_badminton_duel_voice_request_carries_client_timeout_for_memory_guard():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")
    voice_start = html.index("function buildNekoDuelTurnEvent() {")
    voice_section = html[voice_start:html.index("function queueNekoDuelTurnVoice()", voice_start)]

    assert "client_timeout_ms: 2200" in voice_section


@pytest.mark.unit
def test_badminton_voice_entries_freeze_route_identity():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert "function resetVoiceArbiter() {" in html
    assert "voiceArbiter.pending = null;" in html
    assert "voiceArbiter.inFlight = null;" in html
    assert "function _voiceEntryMatchesCurrentSession(entry) {" in html
    assert "if (!_voiceEntryMatchesCurrentSession(entry)) return Promise.resolve();" in html
    assert "if (!_voiceEntryMatchesCurrentSession(pending)) {" in html
    assert "var entrySessionId = String((event && event.session_id) || sessionId || '');" in html
    assert "var entryLanlanName = String((event && (event.lanlan_name || event.lanlanName)) || getRouteLanlanName() || lanlanName || '');" in html
    assert "sessionId: entrySessionId," in html
    assert "lanlanName: entryLanlanName," in html
    assert "var entrySessionId = entry.sessionId || sessionId;" in html
    assert "session_id: entrySessionId," in html
    assert "lanlan_name: entryLanlanName," in html


@pytest.mark.unit
def test_badminton_delayed_results_are_bound_to_session():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert "var resultTimer = 0;" in html
    assert "function scheduleShowResult(delayMs) {" in html
    assert "var resultSessionId = sessionId;" in html
    assert "persistCompletedResult();" in html
    assert "if (sessionId !== resultSessionId || game.state !== 'game_over') return;" in html
    assert "clearTimeout(resultTimer);" in html
    assert "setTimeout(showResult, 900);" not in html
    assert "setTimeout(showResult, 500);" not in html


@pytest.mark.unit
def test_badminton_starts_route_after_character_resolution_before_avatar_loading():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert "initNekoAvatar().finally(function () { startRoute(); });" not in html
    assert "var badmintonCharacterPromise = null;" in html
    assert "return badmintonCharacterPromise;" in html
    assert "function startRouteAfterCharacterReady() {" in html
    assert "loadBadmintonCharacter().finally(function () { startRoute(); });" in html
    assert "var routeLanlanName = getRouteLanlanName();" in html
    assert "var routeSessionId = sessionId;" in html
    assert "lanlan_name: routeLanlanName" in html
    assert "if (sessionId !== routeSessionId || endedRoute || game.state === 'game_over') return res;" in html
    assert "applyRouteIdentity(res.state);" in html
    startup = html[html.rindex("startRouteAfterCharacterReady();"):]
    assert startup.index("startRouteAfterCharacterReady();") < startup.index("initNekoAvatar();")


@pytest.mark.unit
def test_badminton_vrm_waits_for_three_before_resolving_modules():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")
    wait_start = html.index("function waitForVRMModules() {")
    wait_section = html[wait_start:html.index("function fitVRMToContainer(", wait_start)]

    assert "function waitForThreeModule() {" in wait_section
    assert "if (window.THREE) return Promise.resolve();" in wait_section
    assert "window.addEventListener('three-ready', resolve, { once: true });" in wait_section
    assert "return Promise.all([vrmModulesReady, waitForThreeModule()]).then(function () {});" in wait_section


@pytest.mark.unit
def test_badminton_scene_uses_compact_avatars_and_net():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert "var YUI_SHOOTER_W = 244;" in html
    assert "var YUI_SHOOTER_H = 366;" in html
    assert "var YUI_SHOOTER_SCALE = 0.86;" in html
    assert "var PLAYER_AVATAR_W = 292;" in html
    assert "var PLAYER_AVATAR_H = 396;" in html
    assert "var PLAYER_FIGURE_SCALE = 1.02;" in html
    assert "var BALL_R = 18;" in html
    assert "var SHUTTLE_VISUAL_R = 12;" in html
    assert "var SENSEI_VRM_PATH = '/static/vrm/sensei.vrm';" in html
    assert "window.badmintonPlayerVrmManager = window.badmintonPlayerVrmManager || null;" in html
    assert "var visibleH = h * 1.28;" in html
    assert "cam.near = 0.01;" in html
    assert "cam.far = 100;" in html
    assert "document.getElementById('player-sensei-vrm-container')" in html
    assert "document.getElementById('player-sensei-vrm-canvas')" in html
    assert "await waitForVRMModules();" in html
    assert "await manager.core.init('player-sensei-vrm-canvas', 'player-sensei-vrm-container', null, { embed: true });" in html
    assert "var senseiProbe = await fetch(SENSEI_VRM_PATH, { cache: 'no-store' });" in html
    assert "if (!senseiProbe.ok) throw new Error('Sensei VRM model missing: ' + SENSEI_VRM_PATH);" in html
    assert "loader.load(SENSEI_VRM_PATH, resolve, null, reject);" in html
    assert "manager.currentModel = { vrm: vrm, gltf: gltf, scene: vrm.scene, url: SENSEI_VRM_PATH };" in html
    assert "await manager.playVRMAAnimation('/static/vrm/animation/wait03.vrma', {" in html
    assert "isIdle: true" in html
    assert "playIdleAnimation" not in html
    assert "SENSEI_LIVE2D_PATH" not in html
    assert "badmintonPlayerLive2DManager" not in html
    assert "_i18n('hud.nekoTurn', 'Yui 挥拍')" in html
    assert "_i18n('hud.duelTitle', 'Yui 对拉模式：轮流挥拍，谁先失误 3 球谁输')" in html
    assert "_i18n('result.outcome.nekoWin', 'Yui 赢了')" in html
    assert "Neko挥拍" not in html
    assert "Neko 对拉模式" not in html
    assert "courtLeft: 70," in html
    assert "courtRight: 850," in html
    assert "netX: 460," in html
    assert "netTop: 250," in html
    assert "netBottom: 400," in html
    assert "var NET_VISUAL_LINE_WIDTH = 4;" in html
    assert "var NET_VISUAL_Z_HEIGHT = 116;" in html
    assert "var NET_VISUAL_BOTTOM_CLEARANCE = 18;" in html
    assert "var NET_COLLISION_HALF_WIDTH = 24;" in html
    assert "var NET_SPRING_K = 0.18;" in html
    assert "var NET_DAMPING = 0.84;" in html
    assert "var NET_GRAVITY = 0;" in html
    assert "var NET_BALL_FORCE = 0.22;" in html
    assert "var NET_BALL_FRICTION = 0.018;" in html
    assert "var NET_COLS = 17;" in html
    assert "var NET_ROWS = 7;" in html
    assert "var NET_CONTACT_RADIUS = 42;" in html
    assert "var NET_CONTACT_IMPULSE = 240;" in html
    assert "var NET_CONTACT_HOLD_MS = 180;" in html
    assert "function getSideViewCourtTop() {" in html
    assert "return FLOOR_Y - 62;" in html
    assert "function getSideViewCourtBottom() {" in html
    assert "function getNetSurfacePoint(t, v) {" in html
    assert "return getNetBottomY(t) - NET_VISUAL_Z_HEIGHT;" in html
    assert "return FLOOR_Y - NET_VISUAL_BOTTOM_CLEARANCE;" in html
    assert "var lineT = clamp(Number(t) || 0, 0, 1);" in html
    assert "var heightT = clamp(Number(v) || 0, 0, 1);" in html
    assert "x: BADMINTON.netX + (lineT - 0.5) * NET_VISUAL_LINE_WIDTH" in html
    assert "y: topY + (bottomY - topY) * heightT" in html
    assert "var NET_VISUAL_THICKNESS = 28;" not in html
    assert "var NET_VISUAL_SIDE_INSET = 34;" not in html
    assert "NET_VISUAL_Y_SPAN" not in html
    assert "NET_VISUAL_PERSPECTIVE_X_SPREAD" not in html
    assert "var netWidth = 0;" not in html
    assert "var netHalfWidth = NET_COLLISION_HALF_WIDTH;" in html
    assert "pinned: row === 0 || row === NET_ROWS - 1 || col === 0 || col === NET_COLS - 1" in html
    assert "var topLeft = netNodes[0][0];" in html
    assert "var topRight = netNodes[NET_COLS - 1][0];" in html
    assert "var bottomLeft = netNodes[0][NET_ROWS - 1];" in html
    assert "var bottomRight = netNodes[NET_COLS - 1][NET_ROWS - 1];" in html
    assert "meshGrad.addColorStop" not in html
    assert "var rippleY = (rowT - 0.5) * 0.12 * NET_CONTACT_IMPULSE * impact * weight;" in html
    assert "var dropY = NET_CONTACT_IMPULSE * impact * weight;" not in html
    assert "for (var row = 0; row < NET_ROWS; row++) {" in html
    assert "for (var meshCol = 0; meshCol < NET_COLS; meshCol += 2) {" in html
    assert "var tapeGrad = ctx.createLinearGradient(topLeft.x, topLeft.y, topRight.x, topRight.y);" in html
    assert "ctx.moveTo(topLeft.x - 1, topLeft.y + 1);" in html
    assert "ctx.lineTo(topRight.x + 1, topRight.y + 1);" in html
    assert "ctx.lineTo(bottomLeft.x, bottomLeft.y - 1);" in html
    assert "ctx.lineTo(bottomRight.x, bottomRight.y - 1);" in html
    assert "ctx.lineTo(bottom.x + Math.sin" not in html
    assert "var wallGrad = ctx.createLinearGradient(0, 0, 0, 178);" in html
    assert "var glow = ctx.createRadialGradient(lx, 76, 1, lx, 76, 96);" in html
    assert "var rafters = [" in html
    assert "for (var truss = 80; truss < BASE_W; truss += 150)" in html
    assert "var acousticPanels = [" in html
    assert "var spectatorShade = ctx.createLinearGradient(0, 144, 0, 188);" in html
    assert "var sponsorBoards = [" in html
    assert "var wallScoreboardX = BASE_W / 2 - 42;" in html
    assert "ctx.fillRect(wallScoreboardX, wallScoreboardY, 84, 34);" in html
    assert "var apronGrad = ctx.createLinearGradient(0, outerTop, 0, outerBottom);" in html
    assert "var theme = THEMES[currentThemeKey] || THEMES.default;" in html
    assert "label: '羽毛球场'" in html
    assert "scenic: 'indoor'" in html
    assert "sunset: {" not in html
    assert "night: {" not in html
    assert "miami: {" in html
    assert "label: '迈阿密落日'" in html
    assert "floor: ['#f5d99f', '#d7b56d', '#9d7847']" in html
    assert "courtLine: 'rgba(255,255,248,.96)'" in html
    assert "scenic: 'miami'" in html
    assert '<link rel="preload" as="image" href="/static/game/games/badminton/images/indoor-badminton-arena-bg-v4.jpg?v=20260618g">' in html
    assert '<link rel="preload" as="image" href="/static/game/games/badminton/images/miami-beach-sunset-bg-v2.webp?v=20260618b">' in html
    assert "var INDOOR_BADMINTON_ARENA_BACKGROUND_SRC = '/static/game/games/badminton/images/indoor-badminton-arena-bg-v4.jpg?v=20260618g';" in html
    assert "var MIAMI_BEACH_BACKGROUND_SRC = '/static/game/games/badminton/images/miami-beach-sunset-bg-v2.webp?v=20260618b';" in html
    assert "indoor: INDOOR_BADMINTON_ARENA_BACKGROUND_SRC" in html
    assert "miami: MIAMI_BEACH_BACKGROUND_SRC" in html
    assert "function preloadScenicBackgrounds() {" in html
    assert "function getLoadedScenicBackground(key) {" in html
    assert "var scenicBackgroundImage = getLoadedScenicBackground(theme.scenic);" in html
    assert "ctx.drawImage(scenicBackgroundImage, 0, 0, BASE_W, BASE_H);" in html
    assert "if (!scenicBackgroundImage) {" in html
    assert "wallGrad.addColorStop(0, sky[0] || '#183647');" in html
    assert "if (theme.scenic === 'miami') {" in html
    assert "var sunsetGlow = ctx.createRadialGradient(BASE_W * 0.72, 76, 4, BASE_W * 0.72, 76, 190);" in html
    assert "var oceanGrad = ctx.createLinearGradient(0, 116, 0, 178);" in html
    assert "var beachHorizonGrad = ctx.createLinearGradient(0, 160, 0, 214);" in html
    assert "for (var palm = 0; palm < 4; palm++)" in html
    assert "var palmLean = palm % 2 ? -10 : 12;" in html
    assert "ctx.bezierCurveTo(palmLean * 0.16, -18, palmLean * 0.45, -42, palmLean, -66);" in html
    assert "for (var trunkBand = 0; trunkBand < 5; trunkBand++)" in html
    assert "for (var palmLeaf = 0; palmLeaf < 9; palmLeaf++)" in html
    assert "ctx.strokeStyle = palmLeaf % 2 ? 'rgba(180,167,92,.38)' : 'rgba(77,124,76,.56)';" in html
    assert "if (theme.scenic !== 'miami') {" in html
    assert "var beachUmbrellas = [" in html
    assert "floorGrad.addColorStop(0, floor[0] || '#1b6a68');" in html
    assert "var wetSandGrad = ctx.createLinearGradient(0, 158, 0, 286);" in html
    assert "for (var foam = 0; foam < 3; foam++)" in html
    assert "var drySandGrad = ctx.createLinearGradient(0, 214, 0, BASE_H);" in html
    assert "for (var beachSandLine = 0; beachSandLine < 14; beachSandLine++)" in html
    assert "for (var sandDot = 0; sandDot < 120; sandDot++)" in html
    assert "for (var sandSpeck = 0; sandSpeck < 260; sandSpeck++)" in html
    assert "for (var windStreak = 0; windStreak < 18; windStreak++)" in html
    assert "for (var beachFoot = 0; beachFoot < 8; beachFoot++)" in html
    assert "for (var ropePost = 0; ropePost < 6; ropePost++)" in html
    assert "for (var apronRipple = 0; apronRipple < 7; apronRipple++)" in html
    assert "courtGrad.addColorStop(0, 'rgba(250,221,159,.98)');" in html
    assert "for (var sandRipple = 0; sandRipple < 9; sandRipple++)" in html
    assert "for (var courtSandGrain = 0; courtSandGrain < 150; courtSandGrain++)" in html
    assert "var courtFootprints = [" in html
    assert "var sunSandReflection = ctx.createRadialGradient(BASE_W * 0.72, 192, 8, BASE_W * 0.72, 192, 270);" in html
    assert "var courtLineShadow = theme.scenic === 'miami' ? 'rgba(114,76,36,.24)' : 'rgba(6,58,51,.22)';" in html
    assert "var backWallShadow = ctx.createLinearGradient(0, 158, 0, 236);" in html
    assert "var floorSheen = ctx.createRadialGradient(BASE_W * 0.5, court.courtTop + 12, 10, BASE_W * 0.5, court.courtTop + 12, 420);" in html
    assert "var lightReflection = ctx.createLinearGradient(0, court.courtTop - 2, 0, court.courtTop + 90);" in html
    assert "var groundTop = getSideViewCourtTop();" in html
    assert "var groundBottom = getSideViewCourtBottom();" in html
    assert "var zoneGlow = ctx.createLinearGradient(court.courtLeft, 0, court.courtRight, 0);" in html
    assert "var safetyPadGrad = ctx.createLinearGradient(0, outerTop, 0, outerBottom);" in html
    assert "ctx.fillRect(court.courtLeft - outerPad, outerTop, outerPad - 6, outerBottom - outerTop);" in html
    assert "var gearItems = [" in html
    assert "var chairX = court.netX + 28;" in html
    assert "ctx.fillRect(chairX + 8, chairY + 8, 22, 10);" in html
    assert "var shuttleRackX = court.courtLeft - 32;" in html
    assert "for (var tube = 0; tube < 3; tube++)" in html
    assert "var matTexture = ctx.createLinearGradient(court.courtLeft, groundTop, court.courtRight, groundTop);" in html
    assert "for (var matGrain = court.courtLeft + 18; matGrain < court.courtRight; matGrain += 28)" in html
    assert "ctx.fillRect(court.courtLeft + 2, groundTop + 4, court.netX - court.courtLeft - 4, groundBottom - groundTop - 8);" in html
    assert "var serviceWearY = groundTop + (groundBottom - groundTop) * 0.58;" in html
    assert "var serviceWearGrad = ctx.createRadialGradient(court.netX - 135, serviceWearY, 12, court.netX - 135, serviceWearY, 116);" in html
    assert "for (var scuff = 0; scuff < 10; scuff++)" in html
    assert "ctx.ellipse(court.netX, serviceWearY, 88, 21, 0, 0, Math.PI * 2);" in html
    assert "var shortServiceOffset = halfCourtLength * (1.98 / 6.70);" in html
    assert "var doublesLongServiceInset = halfCourtLength * (0.76 / 6.70);" in html
    assert "ctx.moveTo(leftShortServiceX, groundTop + 6);" in html
    assert "ctx.moveTo(rightShortServiceX, groundTop + 6);" in html
    assert "ctx.moveTo(leftDoublesLongServiceX, groundTop + 6);" in html
    assert "ctx.moveTo(rightDoublesLongServiceX, groundTop + 6);" in html
    assert "var netTop = getNetSurfacePoint(0.5, 0);" in html
    assert "var netBottom = getNetSurfacePoint(0.5, 1);" in html
    assert "var leftTop = getNetSurfacePoint(0, 0);" in html
    assert "var rightBottom = getNetSurfacePoint(1, 1);" in html
    assert "var sidePostGrad = ctx.createLinearGradient(leftTop.x, netTop.y, rightTop.x, netBottom.y);" in html
    assert "ctx.strokeStyle = sidePostGrad;" in html
    assert "ctx.lineTo(leftBottom.x, leftBottom.y + 6);" in html
    assert "ctx.ellipse(leftBottom.x, leftBottom.y + 7, 5, 2.4, 0, 0, Math.PI * 2);" in html
    assert "postFoot" not in html
    assert "ctx.rect(leftBottom.x - 4" not in html
    assert "ctx.moveTo(topLeft.x, topLeft.y + 5);" in html
    assert "ctx.lineTo(topRight.x, topRight.y + 5);" in html
    assert "var postX = BADMINTON.netX;" not in html
    assert "var clampGrad = ctx.createLinearGradient(postX - 5, topClampY, postX + 5, topClampY);" not in html
    assert "ctx.roundRect(postX - 5, topClampY - 2.2, 10, 4.4, 2.1);" not in html
    assert "ctx.strokeStyle = 'rgba(255,68,68,.78)';" not in html
    assert "ctx.strokeStyle = 'rgba(255,68,68,.62)';" not in html
    assert "for (var speck = 0;" not in html
    assert "ctx.rect(BADMINTON.courtLeft, groundTop - 8, BADMINTON.courtRight - BADMINTON.courtLeft, groundBottom - groundTop + 18);" in html
    assert "ctx.fillRect(court.courtLeft, groundTop, court.courtRight - court.courtLeft, groundBottom - groundTop);" in html
    assert "BADMINTON.courtTop + (BADMINTON.courtBottom - BADMINTON.courtTop) * t" not in html
    assert "ctx.rect(BADMINTON.courtLeft, BADMINTON.courtTop, BADMINTON.courtRight - BADMINTON.courtLeft, BADMINTON.courtBottom - BADMINTON.courtTop);" not in html
    assert "ctx.fillRect(court.courtLeft, court.courtTop, court.courtRight - court.courtLeft, court.courtBottom - court.courtTop);" not in html
    assert "var shadowAlpha = clamp(0.30 - shuttleZ / 760, 0.06, 0.30);" in html
    assert "ctx.ellipse(ball.x, FLOOR_Y + 3, SHUTTLE_VISUAL_R * 1.35 * shadowScale" in html
    assert "var DISTANCE_MARKERS_STORAGE_KEY = 'bd_badminton_distance_markers_enabled';" in html
    assert "var distanceMarkersEnabled = readJson(DISTANCE_MARKERS_STORAGE_KEY, false) === true;" in html
    assert "ctx.fillRect(court.targetLeft" not in html
    assert "ctx.strokeRect(court.targetLeft" not in html
    assert "plank:" not in html
    assert "threeLine:" not in html
    assert "laneFill:" not in html
    assert "playerSenseiContainer.style.width = PLAYER_AVATAR_W + 'px';" in html
    assert "playerSenseiContainer.style.height = PLAYER_AVATAR_H + 'px';" in html
    assert "container.style.width = YUI_SHOOTER_W + 'px';" in html
    assert "container.style.height = YUI_SHOOTER_H + 'px';" in html
    assert "container.dataset.courtAvatar = 'opponent';" in html
    assert "var baseX = getYuiX();" in html
    assert "function getYuiShotOrigin() {" in html
    assert "var origin = impulse.contact || getRacketContactPoint(shotShooter);" in html
    assert "var direction = shotShooter === 'neko' ? -1 : 1;" in html
    assert "shuttle.vx = direction * v * Math.cos(radians) + contactDeflection;" in html
    assert "function screenYToCourtZ(screenY) {" in html
    assert "function courtZToScreenY(z) {" in html
    assert "function getMidcourtNetY() {" in html
    assert "return screenYToCourtZ(getNetBottomY(0.5));" in html
    assert "function getNetTopZ() {" in html
    assert "return screenYToCourtZ(getNetTopY(0.5));" in html
    assert "function syncShuttleCourtCoordinates(shuttle) {" in html
    assert "function didShuttleCrossMidcourtNet(shuttle, direction) {" in html
    assert "function getMidcourtNetCrossing(shuttle) {" in html
    assert "function isShuttleInsideNetZ(shuttle, crossing) {" in html
    assert "shuttle.courtY = origin.x;" in html
    assert "shuttle.z = screenYToCourtZ(origin.y);" in html
    assert "shuttle.vCourtY = shuttle.vx;" in html
    assert "shuttle.vz = -shuttle.vy;" in html
    assert "var crossedNet = didShuttleCrossMidcourtNet(ball, direction);" in html
    assert "var netCrossing = getMidcourtNetCrossing(ball);" in html
    assert "if (isShuttleInsideNetZ(ball, netCrossing)) {" in html
    assert "z >= getNetBottomZ() - shuttleRadius * 0.18" in html
    assert "z <= getNetTopZ() + shuttleRadius * 0.12" in html
    assert "function applyNetContactImpulse(ball, crossing) {" in html
    assert "var incomingSpeed = typeof ball.speed === 'function'" in html
    assert "var impact = clamp(incomingSpeed / 360, 0.55, 1.35);" in html
    assert "node.vx += recoilX;" in html
    assert "node.vy += rippleY;" in html
    assert "function applyMidcourtNetContact(ball, crossing) {" in html
    assert "ball.x = crossing.screenX;" in html
    assert "ball.y = crossing.screenY;" in html
    assert "ball.netTouched = true;" in html
    assert "ball.netContactHoldUntil = ball.netContactAt + NET_CONTACT_HOLD_MS;" in html
    assert "applyNetContactImpulse(ball, crossing);" in html
    assert "ball.vx = direction * clamp(Math.abs(ball.vx || 0) * 0.22, 32, 130);" in html
    assert "ball.vy = Math.max(Math.abs(ball.vy || 0) * 0.18 + 115, 115);" in html
    assert "applyMidcourtNetContact(ball, netCrossing);" in html
    assert "var targetLeft = direction > 0 ? BADMINTON.netX + ballRadius * 0.5 : BADMINTON.courtLeft;" in html
    assert "var targetRight = direction > 0 ? BADMINTON.courtRight : BADMINTON.netX - ballRadius * 0.5;" in html
    assert "var outPastBackLine = direction > 0" in html
    assert "var baseX = getPlayerX() + 10;" not in html
    assert "container.dataset.courtAvatar = 'shooter';" not in html
    assert "playerSenseiContainer.style.opacity = nekoVisible ? '0' : '0.96';" not in html
    assert "var featherCone = ctx.createLinearGradient(0, -radius * 2.05, 0, radius * 0.06);" in html
    assert "ctx.bezierCurveTo(-radius * 0.62, -radius * 2.05, radius * 0.62, -radius * 2.05, radius * 0.92, -radius * 1.70);" in html
    assert "for (var spine = -4; spine <= 4; spine++)" in html
    assert "var spinStripe = ctx.createLinearGradient(radius * 0.18, -radius * 1.52, radius * 0.34, radius * 0.10);" in html
    assert "spinStripe.addColorStop(0, 'rgba(91,198,214,.58)');" in html
    assert "spinStripe.addColorStop(0.58, 'rgba(255,226,142,.72)');" in html
    assert "spinStripe.addColorStop(1, 'rgba(40,117,136,.62)');" in html
    assert "ctx.lineWidth = 2.2;" in html
    assert "ctx.moveTo(radius * 0.12, -radius * 1.50);" in html
    assert "ctx.quadraticCurveTo(radius * 0.54, -radius * 0.70, radius * 0.25, radius * 0.08);" in html
    assert "ctx.fillStyle = '#11191b';" in html
    assert "ctx.ellipse(0, radius * 0.16, radius * 0.42, radius * 0.095" in html
    assert "cork.addColorStop(0, '#fffdf5');" in html
    assert "ctx.bezierCurveTo(-radius * 0.39, radius * 0.34, -radius * 0.29, radius * 0.58, 0, radius * 0.62);" in html
    assert "var skirt =" not in html
    assert "for (var seam = -1; seam <= 1; seam++)" not in html
    assert "var collar =" not in html
    assert "cork.addColorStop(0.58, '#e4b76c');" not in html
    assert "ctx.ellipse(0, radius * 0.34, radius * 0.50, radius * 0.34" not in html
    assert "var trailGrad = ctx.createLinearGradient(start.x, start.y, ball.x, ball.y);" in html
    assert "rgba(182,231,255,0)" in html
    assert "ctx.strokeStyle = 'rgba(255,143,61,.34)';" not in html
    assert "drawShuttlecock(ball.x, ball.y, SHUTTLE_VISUAL_R, ball.spinAngle || 0);" in html
    assert "if (ball.y < -SHUTTLE_VISUAL_R * 2) {" in html
    assert "var markerX = clamp(ball.x, BADMINTON.courtLeft + 12, BADMINTON.courtRight - 12);" in html
    assert "function drawPlayerServeShuttleHint() {" in html
    assert "if (playerSenseiReady && playerSenseiContainer && playerSenseiContainer.dataset.heldShuttle3d === 'ready') return;" in html
    assert "if (playerSenseiReady) {\n      drawPlayerServeShuttleHint();\n      return;\n    }" in html
    assert "var hintX = px + 44;" in html
    assert "var hintY = py - 108;" in html
    assert "drawShuttlecock(hintX, hintY, SHUTTLE_VISUAL_R * 0.92, -0.28);" in html
    assert "drawShuttlecock(heldBall.x, heldBall.y, SHUTTLE_VISUAL_R);" not in html
    assert "drawShuttlecock(ball.x, ball.y, BALL_R" not in html
    assert "drawShuttlecock(heldBall.x, heldBall.y, BALL_R" not in html
    assert "var avatarW = yuiShooterLayout ? YUI_SHOOTER_W : 180;" in html
    assert "var avatarH = yuiShooterLayout ? YUI_SHOOTER_H : 270;" in html
    assert "Math.round(sx - PLAYER_AVATAR_W / 2)" in html
    assert "Math.round(sy - PLAYER_AVATAR_H)" in html
    assert '<canvas id="yui-live2d-racket-canvas" aria-hidden="true"></canvas>' in html
    assert "#yui-live2d-racket-canvas {" in html
    assert '#neko-l2d-container[data-live2d="ready"] #yui-live2d-racket-canvas' in html
    assert "var yuiLive2dRacketCanvas = document.getElementById('yui-live2d-racket-canvas');" in html
    assert "var yuiLive2dRacketCtx = yuiLive2dRacketCanvas ? yuiLive2dRacketCanvas.getContext('2d') : null;" in html
    assert "var BADMINTON_RACKET_SPRITE_SRC = '/static/game/games/badminton/images/badminton-racket-sprite.svg?v=20260618a';" in html
    assert "function preloadBadmintonRacketSprite() {" in html
    assert "function isBadmintonRacketSpriteReady() {" in html
    assert "try { preloadBadmintonRacketSprite(); } catch (_) { badmintonRacketSpriteImage = null; }" in html
    draw_start = html.index("function drawPlayer() {")
    draw_section = html[draw_start:html.index("function drawAiming()", draw_start)]
    assert "var s = PLAYER_FIGURE_SCALE;" in draw_section
    assert "ctx.arc(px, py - 62 * s, 16 * s" in draw_section
    assert "ctx.lineWidth = 8 * s;" in draw_section
    assert "function drawBadmintonRacket(cx, cy, scale, rotation, mirror) {" in html
    assert "function drawBadmintonRacketOnContext(renderCtx, cx, cy, scale, rotation, mirror) {" in html
    assert "if (isBadmintonRacketSpriteReady()) {" in html
    assert "renderCtx.drawImage(badmintonRacketSpriteImage, -30 * s, -116 * s, 60 * s, 168 * s);" in html
    assert "function getBadmintonRacketGripAnchor(handX, handY, scale, rotation, mirror) {" in html
    assert "var localGripX = isBadmintonRacketSpriteReady() ? 0 : -23 * s;" in html
    assert "var localGripY = isBadmintonRacketSpriteReady() ? 32 * s : 28 * s;" in html
    assert "var gripX = side * (localGripX * cos - localGripY * sin);" in html
    assert "var gripY = localGripX * sin + localGripY * cos;" in html
    assert "function renderYuiLive2dRacket() {" in html
    assert "function getYuiLive2dModelFrame(rect) {" in html
    assert "function getYuiLive2dFallbackHandPoint(modelFrame, shooting, charging) {" in html
    assert "x: modelFrame.left + modelFrame.width * (shooting ? 0.62 : (charging ? 0.60 : 0.58))" in html
    assert "function normalizeYuiLive2dDrawableRect(entry, domRect, layoutW, layoutH) {" in html
    assert "function getYuiLive2dHandAnchorFromDrawables(domRect, modelFrame, layoutW, layoutH, shooting, charging) {" in html
    assert "manager._getRenderableDrawableScreenRects(null, null, true);" in html
    assert "var inViewportSpace = domRect &&" in html
    assert "scaleX = layoutW / domRect.width;" in html
    assert "var minX = modelFrame.left + modelFrame.width * 0.64;" in html
    assert "var maxX = modelFrame.left + modelFrame.width * 0.94;" in html
    assert "function setYuiLive2dParameter(id, value) {" in html
    assert "function applyYuiLive2dRacketPose(shooting, charging) {" in html
    assert "drawBadmintonRacketOnContext(renderCtx, anchorX, anchorY, racketScale, rotation, racketMirror);" in html
    assert "renderYuiLive2dRacket();" in html
    assert "var shooting = nekoL2dContainer.classList.contains('shooting');" in html
    assert "var charging = nekoL2dContainer.classList.contains('charging');" in html
    assert "var layoutW = yuiLive2dRacketCanvas.clientWidth || nekoL2dContainer.offsetWidth || rect.width;" in html
    assert "var modelFrame = getYuiLive2dModelFrame({ width: layoutW, height: layoutH });" in html
    assert "var fallbackHand = getYuiLive2dFallbackHandPoint(modelFrame, shooting, charging);" in html
    assert "var drawableHand = getYuiLive2dHandAnchorFromDrawables(rect, modelFrame, layoutW, layoutH, shooting, charging);" in html
    assert "var handX = drawableHand ? drawableHand.x : fallbackHand.x;" in html
    assert "var handY = drawableHand ? drawableHand.y : fallbackHand.y;" in html
    assert "var racketScale = Math.max(0.98, Math.min(1.08, modelFrame.height / 320));" in html
    assert "handX += modelFrame.width * 0.08;" in html
    assert "handY += modelFrame.height * 0.055;" in html
    assert "var rotation = -0.10 + (shooting ? swing * 0.14 : (charging ? -0.02 : 0));" in html
    assert "var racketMirror = 1;" in html
    assert "var racketAnchor = getBadmintonRacketGripAnchor(handX, handY, racketScale, rotation, racketMirror);" in html
    assert "var anchorX = racketAnchor.x;" in html
    assert "var anchorY = racketAnchor.y;" in html
    assert "window.__badmintonYuiRacketDebug = {" in html
    assert "source: drawableHand ? drawableHand.source : 'fallback'," in html
    assert "if (debugMode && modeParams && modeParams.get('racket_anchor') === '1') {" in html
    assert "renderCtx.arc(handX, handY, 4.5, 0, Math.PI * 2);" in html
    assert "setYuiLive2dParameter('Param75', 0);" in html
    assert "setYuiLive2dParameter('Param90', 0);" in html
    assert "setYuiLive2dParameter('Param95', 0);" in html
    assert "setYuiLive2dParameter('Param77', active ? 1 : 0);" in html
    assert "setYuiLive2dParameter('Param91', active ? pose : 0);" in html
    assert "setYuiLive2dParameter('Param96', active ? pose : 0);" in html
    assert "#player-sensei-vrm-container::after" not in html
    assert "#player-sensei-vrm-container::before" not in html
    assert '.neko-avatar-container[data-court-avatar="opponent"]::after' not in html
    assert '.neko-avatar-container[data-court-avatar="opponent"]::before' not in html
    assert "sensei-racket-head-sweep" not in html
    assert "sensei-racket-handle-sweep" not in html
    assert "yui-racket-head-sweep" not in html
    assert "yui-racket-handle-sweep" not in html
    assert "using CSS fallback" not in html
    assert "function createVrmBadmintonRacket(name, options) {" in html
    assert "function createPlayerVrmRacket() {" in html
    assert "function createVrmHeldShuttlecock(name, options) {" in html
    assert "function drawVrmHeldShuttlecockTexture(ctx, size) {" in html
    assert "function drawBadmintonShuttlecockOnContext(renderCtx, x, y, radius, rotation) {" in html
    assert "function getPlayerVrmHeldShuttleCanvasPoint() {" in html
    assert "function getPlayerHeldShuttleServeHandCanvasPoint() {" in html
    assert 'id="player-sensei-held-shuttle-canvas"' in html
    assert "#player-sensei-held-shuttle-canvas { position: absolute; inset: 0; z-index: 2; pointer-events: none; }" in html
    assert "function drawPlayerVrmHeldShuttleOverlay() {" in html
    assert "return game.heldShuttleVisible && !game.ball && !game.pendingSwing && game.state === 'ready' && isPlayerTurn();" in html
    held_shuttle_texture_start = html.index("function drawVrmHeldShuttlecockTexture(ctx, size) {")
    held_shuttle_texture_section = html[held_shuttle_texture_start:html.index("function createVrmHeldShuttlecock(name, options) {", held_shuttle_texture_start)]
    assert "drawBadmintonShuttlecockOnContext(ctx, size * 0.50, size * 0.58, size * 0.23, -0.28);" in held_shuttle_texture_section
    assert "ctx.ellipse(-43, 0, 12, 43, 0, 0, Math.PI * 2);" not in held_shuttle_texture_section
    assert "drawHeldShuttleTieRing" not in held_shuttle_texture_section
    draw_shuttle_start = html.index("function drawShuttlecock(x, y, radius, rotation) {")
    draw_shuttle_section = html[draw_shuttle_start:html.index("function drawBackspinBall(ball) {", draw_shuttle_start)]
    assert "drawBadmintonShuttlecockOnContext(ctx, x, y, radius, rotation);" in draw_shuttle_section
    held_shuttle_create_start = html.index("function createVrmHeldShuttlecock(name, options) {")
    held_shuttle_create_section = html[held_shuttle_create_start:html.index("function syncPlayerHeldShuttleVisibility() {", held_shuttle_create_start)]
    assert "new THREE.CanvasTexture(textureCanvas)" in held_shuttle_create_section
    assert "drawVrmHeldShuttlecockTexture(textureCtx, 256);" in held_shuttle_create_section
    assert "new THREE.SpriteMaterial({" in held_shuttle_create_section
    assert "held-shuttle-vrm-hand-sprite" in held_shuttle_create_section
    assert "sprite.scale.set(0.34, 0.34, 1);" in held_shuttle_create_section
    assert "held-shuttle-shared-2d-style-sprite" not in held_shuttle_create_section
    assert "held-shuttle-2d-style-cork-depth" not in held_shuttle_create_section
    assert "new THREE.CylinderGeometry(0.012, 0.014, 0.018, 18)" not in held_shuttle_create_section
    assert "held-shuttle-realistic-model" not in held_shuttle_create_section
    assert "held-shuttle-feather-plane" not in held_shuttle_create_section
    assert "held-shuttle-feather-rib" not in held_shuttle_create_section
    assert "new THREE.BufferGeometry()" not in held_shuttle_create_section
    assert "var featherPlaneCount" not in held_shuttle_create_section
    assert "new THREE.ConeGeometry" not in held_shuttle_create_section
    held_shuttle_overlay_start = html.index("function getPlayerVrmHeldShuttleCanvasPoint() {")
    held_shuttle_overlay_section = html[held_shuttle_overlay_start:html.index("function drawAiming()", held_shuttle_overlay_start)]
    assert "playerVrmHeldShuttle.localToWorld(heldPoint);" in held_shuttle_overlay_section
    assert "heldPoint.clone().project(window.badmintonPlayerVrmManager.camera);" in held_shuttle_overlay_section
    assert "var hand = getPlayerVrmBone(vrm, 'leftHand');" in held_shuttle_overlay_section
    assert "localX = (projected.x + 1) * 0.5 * rect.width + rect.width * 0.012;" in held_shuttle_overlay_section
    assert "localY = (1 - (projected.y + 1) * 0.5) * rect.height + rect.height * 0.195;" in held_shuttle_overlay_section
    assert "source = 'player-vrm-held-shuttle-left-hand';" in held_shuttle_overlay_section
    assert "source = 'player-vrm-held-shuttle-serve-hand-fallback';" in held_shuttle_overlay_section
    assert "window.__badmintonPlayerHeldShuttleDebug = {" in held_shuttle_overlay_section
    assert "window.__badmintonPlayerHeldShuttleDebug = { source: 'player-vrm-held-shuttle-mesh' };" in held_shuttle_overlay_section
    assert "function clearPlayerHeldShuttleOverlayCanvas() {" in held_shuttle_overlay_section
    assert "playerSenseiHeldShuttleCtx.clearRect(0, 0, rect.width, rect.height);" in held_shuttle_overlay_section
    assert "var point = getPlayerHeldShuttleServeHandCanvasPoint() || getPlayerVrmHeldShuttleCanvasPoint();" in held_shuttle_overlay_section
    assert "var corkOffsetX = -Math.sin(heldRotation) * heldRadius * 0.42;" in held_shuttle_overlay_section
    assert "drawBadmintonShuttlecockOnContext(playerSenseiHeldShuttleCtx, shuttleX, shuttleY, heldRadius, heldRotation);" in held_shuttle_overlay_section
    render_start = html.index("function render() {")
    render_section = html[render_start:html.index("function loop(ts) {", render_start)]
    assert "drawBall();\n    drawPlayerVrmHeldShuttleOverlay();" in render_section
    assert "function attachPlayerHeldShuttleToHand(vrm) {" in html
    assert "function syncPlayerHeldShuttleVisibility() {" in html
    assert "function attachPlayerRacketToHand(vrm) {" in html
    held_shuttle_attach_start = html.index("function attachPlayerHeldShuttleToHand(vrm) {")
    held_shuttle_attach_section = html[held_shuttle_attach_start:html.index("function attachYuiRacketToHand(vrm) {", held_shuttle_attach_start)]
    assert "var hand = getPlayerVrmAttachmentBone(vrm, 'leftHand');" in held_shuttle_attach_section
    assert "rightHand" not in held_shuttle_attach_section
    assert "shuttle.renderOrder = 10;" in html
    assert "x: 0.040,\n      y: 0.000,\n      z: 0.135," in html
    assert "function attachYuiRacketToHand(vrm) {" in html
    assert "function getPlayerVrmRacketContactPoint() {" in html
    assert "var sweetSpot = new THREE.Vector3(0, 0.475, 0.002);" in html
    assert "playerVrmRacket.localToWorld(sweetSpot);" in html
    assert "sweetSpot.clone().project(window.badmintonPlayerVrmManager.camera);" in html
    assert "clientX / Math.max(1, window.innerWidth) * BASE_W" in html
    assert "window.__badmintonPlayerRacketDebug = {" in html
    assert "var live2dPath = charData.live2d_path || '/static/yui-origin/yui-origin.model3.json';" in html
    assert "window.lanlan_config.model_type = 'live2d';" in html
    assert "window.lanlan_config.live3d_sub_type = '';" in html
    assert "await initLive2DAvatar(live2dPath);" in html
    assert "await initVRMAvatar(vrmPath);" not in html
    assert "var hand = getPlayerVrmAttachmentBone(vrm, 'rightHand');" in html
    assert "hand.add(playerVrmRacket);" in html
    assert "hand.add(playerVrmHeldShuttle);" in html
    assert "hand.add(yuiVrmRacket);" in html
    assert "playerSenseiContainer.dataset.racket3d = 'ready';" in html
    assert "playerSenseiContainer.dataset.heldShuttle3d = 'ready';" in html
    assert "nekoVrmContainer.dataset.racket3d = 'ready';" in html
    assert "if (!attachPlayerRacketToHand(vrm)) {" in html
    assert "if (!attachPlayerHeldShuttleToHand(vrm)) {" in html
    assert "if (!attachYuiRacketToHand(vrm)) {" in html
    assert "delete nekoVrmContainer.dataset.racket3d;" in html
    assert "playerVrmHeldShuttle.visible = !!shouldDrawHeldShuttle();" in html
    assert "syncPlayerHeldShuttleVisibility();" in html
    assert "game.heldShuttleVisible = false;" in html
    player_init_start = html.index("manager.currentModel = { vrm: vrm, gltf: gltf, scene: vrm.scene, url: SENSEI_VRM_PATH };")
    player_init_section = html[player_init_start:html.index("if (manager.renderer && manager.renderer.domElement)", player_init_start)]
    assert player_init_section.index("if (!attachPlayerRacketToHand(vrm)) {") < player_init_section.index("fitVRMToContainer(manager, vrm, playerSenseiContainer);")
    yui_vrm_start = html.index("manager.currentModel = { vrm: vrm, gltf: gltf, scene: vrm.scene, url: vrmPath };")
    yui_vrm_section = html[yui_vrm_start:html.index("if (manager.renderer && manager.renderer.domElement)", yui_vrm_start)]
    assert yui_vrm_section.index("if (!attachYuiRacketToHand(vrm)) {") < yui_vrm_section.index("fitVRMToAudience(manager, vrm);")
    assert "new THREE.CylinderGeometry(0.0088, 0.0108, 0.16, 12)" in html
    assert "butt.name = 'racket-butt-cap';" in html
    assert "gripWrap.name = 'racket-grip-wrap';" in html
    assert "shaft.name = 'racket-shaft';" in html
    assert "new THREE.TorusGeometry(0.082, 0.0042, 10, 56)" in html
    assert "head.scale.set(0.68, 1.28, 1);" in html
    assert "racket.scale.setScalar(options.scale == null ? 1.18 : options.scale);" in html
    assert "node.frustumCulled = false;" in html
    assert "scale: 1.26," in html
    assert "scale: 1.20," in html
    assert "x: 0.006," in html
    assert "y: 0.002," in html
    assert "filter: saturate(1.14);" in html
    assert "#player-sensei-vrm-container.shooting { animation: sensei-shooting .42s ease-out; }" not in html
    assert "#player-sensei-vrm-container.charging { animation: sensei-charging .72s ease-in-out infinite; }" not in html
    assert "function applyPlayerVrmPoseFrame(action, progress) {" in html
    assert "function setPlayerVrmBonePose(vrm, name, x, y, z, weight) {" in html
    assert "startPlayerVrmPose(action, duration);" in html
    assert "playerVrmRestPose = capturePlayerVrmRestPose(vrm);" in html
    pose_start = html.index("function applyPlayerVrmPoseFrame(action, progress) {")
    pose_section = html[pose_start:html.index("function startPlayerVrmPose(", pose_start)]
    assert "rightUpperArm" not in pose_section
    assert "rightLowerArm" not in pose_section
    assert "leftUpperArm" not in pose_section
    assert "rightHand" not in pose_section
    assert "drawBadmintonRacket(heldBall.x + 8 * s, heldBall.y - 4 * s, s" in draw_section
    assert "ctx.arc(heldBall.x + 8 * s, heldBall.y - 4 * s, 46 * s" in draw_section


@pytest.mark.unit
def test_badminton_mouse_input_is_gated_to_player_controlled_shots():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert "function canPlayerControlShot() {" in html
    assert "if (game.state !== 'ready' || game.pendingSwing || !isPlayerTurn()) return false;" in html
    assert "if (isPlayerReceivingReturn()) return canPlayerReturnIncomingShuttle();" in html
    assert "return !isPositionTransitioning;" in html
    assert "var yuiSwinging = game.pendingSwing && game.pendingSwing.shooter === 'neko';" in html
    assert "return isDuelMode() && (canPlayerControlShot() || incomingYuiBall || yuiSwinging);" in html
    assert "function isIncomingPlayerShuttleInReach(ball) {" in html
    assert "function canPlayerReturnIncomingShuttle() {" in html

    assert "function shouldIgnoreBadmintonPointerEvent(ev) {" in html
    assert "#utility-controls, #result-panel, #leaderboard-panel, #stats-panel, #game-audio-controls, #bd-debug-panel" in html
    mousemove = html[
        html.index("function handleBadmintonPointerMove(ev) {"):
        html.index("function handleBadmintonPointerDown(ev) {")
    ]
    assert "if (shouldIgnoreBadmintonPointerEvent(ev)) return;" in mousemove
    assert "if (canPlayerMoveCourt()) updatePlayerCourtTarget(ev.clientX, ev.clientY);" in mousemove
    assert "if (!canPlayerControlShot()) return;" in mousemove
    assert mousemove.index("if (!canPlayerControlShot()) return;") < mousemove.index("game.aimAngle =")

    mousedown = html[
        html.index("function handleBadmintonPointerDown(ev) {"):
        html.index("window.addEventListener('mouseup'")
    ]
    assert "canvas.addEventListener('mousemove', handleBadmintonPointerMove);" in mousedown
    assert "canvas.addEventListener('mousedown', handleBadmintonPointerDown);" in mousedown
    assert "window.addEventListener('mousemove', function (ev) {" in mousedown
    assert "window.addEventListener('mousedown', function (ev) {" in mousedown
    assert "handleBadmintonPointerDown(ev);" in mousedown
    assert "if (!canPlayerControlShot()) return;" in mousedown
    assert "if (game.state !== 'ready') return;" not in mousedown
    assert "if (!isPlayerTurn()) return;" not in mousedown

    mouseup = html[
        html.index("window.addEventListener('mouseup'"):
        html.index("window.addEventListener('keydown'")
    ]
    assert "if (game.charging && canPlayerControlShot()) shoot();" in mouseup
    assert "game.charging = false;" in mouseup


@pytest.mark.unit
def test_badminton_removed_stale_assist_hud_and_hotkeys():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert 'id="assist-label"' not in html
    assert "G/S/M 辅助" not in html
    assert "G{{guide}} / S{{sweet}} / M{{music}}" not in html
    assert "G {{guide}} / S {{sweet}} / M {{music}}" not in html
    keydown = html[
        html.index("window.addEventListener('keydown'"):
        html.index("if (bgmVolumeInput)", html.index("window.addEventListener('keydown'"))
    ]
    assert "key === 'g'" not in keydown
    assert "key === 's'" not in keydown
    assert "showToggleHint('guide'" not in keydown
    assert "showToggleHint('sweet'" not in keydown
    assert "key === 'm'" in keydown


@pytest.mark.unit
def test_badminton_racket_swing_applies_physics_to_shuttle():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert "var SWING_IMPACT_DELAY_MS = 120;" in html
    assert "var SHUTTLE_MASS_KG = 0.005;" in html
    assert "var SHUTTLE_DRAG_PER_SECOND = 0.42;" in html
    assert "heldShuttleVisible: true," in html
    assert "pendingSwing: null," in html
    assert "shuttleSeq: 0," in html

    assert "function buildSwingImpulse(angle, power, shooter, incomingBall) {" in html
    assert "var contact = incomingBall ? { x: incomingBall.x, y: incomingBall.y } : getRacketContactPoint(shooter);" in html
    assert "var minTimingQuality = shooter === 'neko' ? 0.62 : 0.34;" in html
    assert "var timingQuality = incomingBall ? clamp(1 - contactError / 118, minTimingQuality, 1) : 1;" in html
    assert "incomingSpeed: incomingSpeed," in html
    assert "incomingVx: incomingVx," in html
    assert "incomingVy: incomingVy," in html
    assert "incomingBallId: incomingBall ? incomingBall.id : 0," in html
    assert "incomingBall: incomingBall || null," in html
    assert "speed: 275 + force * 600 + quality * 82 + Math.min(100, incomingSpeed * 0.10) + smashSpeedBonus," in html
    assert "contact: contact" in html
    assert "function shotNoise(seed, salt) {" in html
    assert "function buildShuttleAerodynamics(launchAngle, power, direction, impulse, shuttleId, hitCount) {" in html
    assert "var playerReturnWobble = impulse.incomingSpeed && direction > 0 ? 1 : 0;" in html
    assert "sliceAccel: direction * sliceBase * (0.35 + force * 0.65 + incoming * 0.35 + playerReturnWobble * 0.22)," in html
    assert "floatLift: (20 + quality * 34 + spinBias * 26) * (impulse.isSmash ? 0.22 : 1)," in html
    assert "lateDrop: 84 + force * 74 + incoming * 42 + playerReturnWobble * 34 + (impulse.isSmash ? 160 + (impulse.smashQuality || 0) * 100 : 0)," in html
    assert "velocityBrake: 0.36 + force * 0.30 + highAngle * 0.18 + incoming * 0.16 + playerReturnWobble * 0.08 - smashBias * 0.12," in html
    assert "brakeDelay: impulse.isSmash ? 0.08 : 0.16 + highAngle * 0.10 - playerReturnWobble * 0.03," in html
    assert "apexDrop: 50 + highAngle * 42 + force * 32 + incoming * 24 + playerReturnWobble * 34 + smashBias * 90," in html
    assert "glideDrift: direction * (shotNoise(seed, 5) - 0.5) * (18 + imperfect * 40 + flatShot * 28 + playerReturnWobble * 18)," in html
    assert "flutterStrength: (10 + imperfect * 34 + incoming * 16 + playerReturnWobble * 12) * (impulse.isSmash ? 0.55 : 1)," in html
    assert "function queueRacketSwing(angle, power, shooter, options) {" in html
    assert "var incomingBall = options && options.incomingBall ? options.incomingBall : null;" in html
    assert "game.state = 'swinging';" in html
    assert "game.heldShuttleVisible = false;" in html
    assert "if (game.ball && game.ball.shooter !== shotShooter && !incomingBall) game.ball = null;" in html
    assert "playShotWhoosh();" in html
    assert "launchShot(swing.angle, swing.power, swing.shooter, swing.impulse);" in html
    assert "}, SWING_IMPACT_DELAY_MS);" in html

    assert "function launchShot(angle, power, shooter, swingImpulse) {" in html
    assert "var hitShuttle = impulse.incomingBall || null;" in html
    assert "var shuttleId = hitShuttle ? hitShuttle.id : ++game.shuttleSeq;" in html
    assert "var shuttle = hitShuttle || {};" in html
    assert "shuttle.id = shuttleId;" in html
    assert "shuttle.radius = BALL_R;" in html
    assert "shuttle.diameter = BALL_R * 2;" in html
    assert "shuttle.massKg = SHUTTLE_MASS_KG;" in html
    assert "shuttle.dragPerSecond = SHUTTLE_DRAG_PER_SECOND;" in html
    assert "shuttle.swingForce = impulse.force;" in html
    assert "shuttle.swingQuality = impulse.quality;" in html
    assert "shuttle.timingQuality = impulse.timingQuality;" in html
    assert "shuttle.incomingSpeed = impulse.incomingSpeed || 0;" in html
    assert "shuttle.returnedFromShuttleId = impulse.incomingBallId || 0;" in html
    assert "shuttle.hitCount = (hitShuttle ? (hitShuttle.hitCount || 1) : 0) + 1;" in html
    assert "shuttle.aero = buildShuttleAerodynamics(launchAngle, power, direction, impulse, shuttle.id, shuttle.hitCount);" in html
    assert "shuttle.awaitingReturnBy = '';" in html
    assert "shuttle.groundedReturnAt = 0;" in html
    assert "var lastShuttleContactToken = '';" in html
    assert "var lastShuttleContactAt = 0;" in html
    assert "function playShuttleContact(quality, token) {" in html
    assert "if (contactToken && contactToken === lastShuttleContactToken) return;" in html
    assert "if (nowMs - lastShuttleContactAt < 90) return;" in html
    contact_section = html[html.index("function playShuttleContact(quality, token) {"):html.index("function playShotResultSound", html.index("function playShuttleContact(quality, token) {"))]
    result_section = html[html.index("function playShotResultSound(shotType, scored) {"):html.index("function playShuttleContactSound", html.index("function playShotResultSound(shotType, scored) {"))]
    assert "badmintonGameAudio.playSfx('shuttleContact'" in contact_section
    assert "badmintonGameAudio.playSfx('shot." not in result_section
    assert "playShuttleContact(impulse.quality, shuttle.id + ':' + shuttle.hitCount);" in html
    assert "var contactDeflection = impulse.incomingSpeed ? (1 - impulse.quality) * (shotShooter === 'neko' ? -24 : 24) : 0;" in html
    assert "var verticalDeflection = impulse.incomingSpeed ? clamp((impulse.incomingVy || 0) * -0.10, -55, 55) : 0;" in html
    assert "var aiReturnLift = 0;" in html
    assert "var baseVy = -v * Math.sin(radians) * impulse.lift + verticalDeflection + aiReturnLift;" in html
    assert "var smashDownVelocity = impulse.isSmash ? 250 + impulse.smashQuality * 210 + impulse.force * 90 : 0;" in html
    assert "shuttle.vy = impulse.isSmash ? smashDownVelocity : baseVy;" in html
    assert "function buildShuttleSpinRate(launchAngle, power, direction, impulse) {" in html
    assert "var baseSpinRate = direction * getBackspinRate(launchAngle, power, game.distance);" in html
    assert "var incomingSpinRate = impulse.incomingSpeed ? direction * clamp(impulse.incomingSpeed / 42, 0, 26) * (1.15 - impulse.quality * 0.35) : 0;" in html
    assert "var contactSpinRate = impulse.incomingSpeed ? direction * (10 + impulse.quality * 7 + clamp((impulse.incomingSpeed || 0) / 150, 0, 6)) : 0;" in html
    assert "var smashSpinRate = impulse.isSmash ? direction * (12 + impulse.smashQuality * 18) : 0;" in html
    assert "var rawSpinRate = baseSpinRate + incomingSpinRate + contactSpinRate + smashSpinRate;" in html
    assert "var maxSpinRate = direction > 0 ? 10 : 24;" in html
    assert "return clamp(rawSpinRate, -maxSpinRate, maxSpinRate);" in html
    assert "shuttle.spinRate = buildShuttleSpinRate(launchAngle, power, direction, impulse);" in html
    assert "angle: game.ball.angle," in html
    assert "yuiReturnSpinRate" not in html
    assert "if (shotShooter === 'neko' && Math.abs(shuttle.spinRate) < 9)" not in html
    assert "var drag = clamp((ball.dragPerSecond || 0) * subDt, 0, 0.18);" in html
    assert "var playerReturnWobble = impulse.incomingSpeed && direction > 0 ? 1 : 0;" in html
    assert "var playerReturnAngle = clamp(game.aimAngle - 10, 34, 48);" in html
    assert "queueRacketSwing(playerReturnAngle, 53, 'player', { incomingBall: game.ball });" in html
    assert "var aero = ball.aero || null;" in html
    assert "var flutter = Math.sin((aero.flutterPhase || 0) + aero.age * (aero.flutterRate || 0)) * (aero.flutterStrength || 0);" in html
    assert "var slice = (aero.sliceAccel || 0) * Math.exp(-aero.age * 2.2);" in html
    assert "var floatLift = (aero.floatLift || 0) * Math.exp(-aero.age * 4.1) * (0.35 + speedFactor * 0.75);" in html
    assert "var lateDrop = (aero.lateDrop || 0) * clamp((aero.age - 0.12) / 0.46, 0, 1) * (ball.vy > -120 ? 1 : 0.42);" in html
    assert "var brakeReady = clamp((aero.age - (aero.brakeDelay || 0)) / 0.24, 0, 1);" in html
    assert "var brakeFactor = clamp(speed / 360, 0.20, 1);" in html
    assert "var velocityBrake = clamp((aero.velocityBrake || 0) * brakeFactor * brakeFactor * brakeReady * subDt, 0, 0.12);" in html
    assert "var dropSnap = (aero.apexDrop || 0) * brakeReady * clamp((ball.vy + 150) / 430, 0, 1);" in html
    assert "var glide = (aero.glideDrift || 0) * clamp((aero.age - 0.18) / 0.52, 0, 1) * (1 - speedFactor * 0.55);" in html
    assert "var dragPulse = 1 + speedFactor * 0.85 + (aero.dragPulse || 0) * speedFactor * clamp((aero.age - 0.08) / 0.46, 0, 1);" in html
    assert "ball.vx += (slice + flutter + glide) * subDt;" in html
    assert "ball.vy += (-floatLift + lateDrop + dropSnap) * subDt;" in html
    assert "ball.vx *= (1 - velocityBrake);" in html
    assert "ball.vy *= (1 - velocityBrake * 0.55);" in html
    assert "function stepAwaitingPlayerReturn(dt) {" in html
    assert "stepBallPhysics(ball, subDt);" in html
    assert "var ballRadius = ball.radius || BALL_R;" in html
    assert "function drawPlayerServeShuttleHint() {" in html
    assert "if (!shouldDrawHeldShuttle()) return;" in html
    assert "window.__badmintonPlayerHeldShuttleDebug = null;" in html
    assert "drawPlayerServeShuttleHint();" in html

    state_start = html.index("getState: function () {")
    state_section = html[state_start:html.index("resetGame: resetGame", state_start)]
    assert "heldShuttleVisible: game.heldShuttleVisible," in state_section
    assert "pendingSwing: game.pendingSwing ?" in state_section
    assert "currentShuttle: game.ball ?" in state_section
    assert "diameter: game.ball.diameter," in state_section
    assert "dragPerSecond: game.ball.dragPerSecond," in state_section
    assert "spinAngle: game.ball.spinAngle || 0," in state_section
    assert "spinRate: game.ball.spinRate || 0," in state_section
    assert "timingQuality: game.ball.timingQuality," in state_section
    assert "incomingSpeed: game.ball.incomingSpeed," in state_section
    assert "returnedFromShuttleId: game.ball.returnedFromShuttleId," in state_section
    assert "y: getShuttleCourtY(game.ball, false)," in state_section
    assert "z: getShuttleCourtZ(game.ball, false)," in state_section
    assert "screenX: game.ball.x," in state_section
    assert "screenY: game.ball.y," in state_section


@pytest.mark.unit
def test_badminton_duel_scores_on_valid_landings_inside_lines():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert "rallyHits: 0," in html
    assert "rally_hits: game.duel.rallyHits," in html
    assert "var pointWinner = '';" in html
    assert "pointWinner = scored ? shooter : (shooter === 'player' ? 'neko' : 'player');" in html
    assert "if (pointWinner === 'player') {\n      game.duel.playerScore += point;" in html
    assert "} else {\n      game.duel.nekoScore += point;" in html
    assert "if (scored) {\n      if (shooter === 'player') game.duel.nekoMisses += 1;" in html
    assert "result: scored ? 'scored' : 'missed'," in html
    assert "point_winner: pointWinner," in html
    assert "rally_hits: game.duel.rallyHits," in html
    assert "} else if (pointWinner === 'neko') {\n      game.duel.round += 1;\n      scheduleNekoDuelTurn();" in html
    assert "showAssistHint(_i18n('toast.nekoThinking', 'Yui 准备回球'));" in html
    assert "var point = scored ? (previousDistance >= 405 ? 2 : 1) : 0;" not in html
    assert "if (shooter === 'player') game.duel.playerScore += point;" not in html
    assert "else game.duel.nekoScore += point;" not in html


@pytest.mark.unit
def test_badminton_yui_returns_incoming_shuttle_before_landing():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert "function maybeYuiReturnIncomingShuttle(ball) {" in html
    assert "if (ball.shooter !== 'player' || ball.direction !== 1 || !ball.crossedNet) return false;" in html
    assert "var contact = getYuiShotOrigin();" in html
    assert "var shuttleCourtY = getShuttleCourtY(ball, false);" in html
    assert "var previousShuttleCourtY = getShuttleCourtY(ball, true);" in html
    assert "var shuttleZ = getShuttleCourtZ(ball, false);" in html
    assert "var yuiContactTopZ = screenYToCourtZ(contact.y - 92);" in html
    assert "var yuiContactBottomZ = screenYToCourtZ(FLOOR_Y - 18);" in html
    assert "var yuiReachLeft = contact.x - 54;" in html
    assert "var yuiReachRight = contact.x + 122;" in html
    assert "var crossesYuiReach = Math.max(previousShuttleCourtY, shuttleCourtY) >= yuiReachLeft" in html
    assert "&& Math.min(previousShuttleCourtY, shuttleCourtY) <= yuiReachRight;" in html
    assert "var inYuiReach = crossesYuiReach" in html
    assert "&& shuttleZ <= yuiContactTopZ" in html
    assert "&& shuttleZ >= yuiContactBottomZ" in html
    assert "game.duel.activeShooter = 'neko';" in html
    assert "game.duel.rallyHits += 1;" in html
    assert "shot.angle = clamp(43 + Math.random() * 5 - Math.min(3, game.duel.rallyHits * 0.18), 38, 50);" in html
    assert "shot.power = clamp(50 + Math.random() * 5 + Math.min(4, game.duel.rallyHits * 0.25), 46, 56);" in html
    assert "showAssistHint(_i18n('toast.nekoReturn', 'Yui 回球'));" in html
    assert "queueRacketSwing(shot.angle, shot.power, 'neko', { incomingBall: ball });" in html

    update_start = html.index("function update(dt) {")
    update_section = html[update_start:html.index("function drawDistanceMarkers()", update_start)]
    assert "stepBallPhysics(b, subDt);" in update_section
    assert "if (b.resolved && canonicalShotType(b.pendingShotType) === 'net') {" in update_section
    assert update_section.index("if (b.resolved && canonicalShotType(b.pendingShotType) === 'net') {") < update_section.index("stepBallPhysics(b, subDt);")
    assert "if (maybeYuiReturnIncomingShuttle(b)) break;" in update_section
    assert update_section.index("checkShuttleLanding(b);") < update_section.index("if (maybeYuiReturnIncomingShuttle(b)) break;")
    assert update_section.index("if (maybeYuiReturnIncomingShuttle(b)) break;") < update_section.index("if (maybePlayerReceiveIncomingShuttle(b)) break;")
    landing_start = html.index("function checkShuttleLanding(ball) {")
    landing_section = html[landing_start:html.index("function maybeYuiReturnIncomingShuttle(ball)", landing_start)]
    assert "returnedFromShuttleId" not in landing_section
    assert "if (isShuttleInsideNetZ(ball, netCrossing)) {" in landing_section
    assert "if (ball.netContactHoldUntil && performance.now() < ball.netContactHoldUntil) return;" in landing_section
    net_contact_section = landing_section[
        landing_section.index("if (isShuttleInsideNetZ(ball, netCrossing)) {"):
        landing_section.index("var ballRadius = ball.radius || BALL_R;")
    ]
    assert "applyMidcourtNetContact(ball, netCrossing);" in net_contact_section
    assert "queueShotResolution(ball, false, 'net'" not in net_contact_section
    assert "function applyMidcourtNetContact(ball, crossing) {" in html


@pytest.mark.unit
def test_badminton_duel_avatars_move_to_receive_shuttles():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert "var PLAYER_MOVE_SPEED = 1040;" in html
    assert "var YUI_MOVE_SPEED = 360;" in html
    assert "var PLAYER_COURT_X_MAX = BADMINTON.netX - 58;" in html
    assert "transition: opacity .24s ease;" in html
    assert "transition: transform .3s ease, opacity .24s ease;" not in html
    assert "playerCourt: { x: PLAYER_START_X, y: PLAYER_START_Y, targetX: PLAYER_START_X, targetY: PLAYER_START_Y }," in html
    assert "yuiCourt: { x: YUI_START_X, y: YUI_START_Y, targetX: YUI_START_X, targetY: YUI_START_Y }," in html
    assert "function updatePlayerCourtTarget(clientX, clientY) {" in html
    assert "var normX = clamp(clientX / Math.max(1, window.innerWidth), 0, 1);" in html
    assert "game.playerCourt.targetX = clamp(normX * BASE_W, PLAYER_COURT_X_MIN, PLAYER_COURT_X_MAX);" in html
    assert "game.playerCourt.x = game.playerCourt.targetX;" not in html
    assert "var playerMoveRange = PLAYER_COURT_X_MAX - PLAYER_COURT_X_MIN;" not in html
    assert "game.playerCourt.targetY = PLAYER_START_Y;" in html
    assert "var normY = clamp(clientY / Math.max(1, window.innerHeight), 0, 1);" not in html
    assert "function updateYuiCourtTarget() {" in html
    assert "if (ball && !ball.resolved && ball.shooter === 'player' && ball.direction === 1) {" in html
    assert "targetX = clamp(ball.x + 42, YUI_COURT_X_MIN, YUI_COURT_X_MAX);" in html
    assert "targetY = clamp(ball.y + 76, YUI_COURT_Y_MIN, YUI_COURT_Y_MAX);" in html
    assert "function updateCourtMovement(dt) {" in html
    assert "game.playerCourt.x = moveToward(game.playerCourt.x, game.playerCourt.targetX, PLAYER_MOVE_SPEED, dt);" in html
    assert "game.playerCourt.targetY = PLAYER_START_Y;" in html
    assert "game.playerCourt.y = PLAYER_START_Y;" in html
    assert "game.playerCourt.y = moveToward(game.playerCourt.y, game.playerCourt.targetY, PLAYER_MOVE_SPEED * 0.55, dt);" not in html
    assert "game.yuiCourt.x = moveToward(game.yuiCourt.x, game.yuiCourt.targetX, YUI_MOVE_SPEED, dt);" in html
    assert "function canPlayerMoveCourt() {" in html
    assert "var incomingYuiBall = game.ball && game.ball.shooter === 'neko' && game.ball.direction === -1 && !game.ball.resolved;" in html
    assert "if (canPlayerMoveCourt()) updatePlayerCourtTarget(ev.clientX, ev.clientY);" in html
    assert "if (isDuelMode()) updateCourtMovement(dt);" in html
    assert "playerCourt: Object.assign({}, game.playerCourt)," in html
    assert "var playerRacketContact = getRacketContactPoint('player');" in html
    assert "playerRacketContact: playerRacketContact ? {" in html
    assert "source: playerRacketContact.source || 'fallback'" in html
    assert "yuiCourt: Object.assign({}, game.yuiCourt)," in html
    assert "y: getYuiEyeY() - 10" in html


@pytest.mark.unit
def test_badminton_player_can_receive_and_return_yui_shuttle():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert "function isPlayerReceivingReturn() {" in html
    assert "return !!(game.ball && game.ball.awaitingReturnBy === 'player');" in html
    assert "return getPlayerVrmRacketContactPoint() || getShotOrigin();" in html
    assert "return isPlayerReceivingReturn() && !game.ball.resolved && isIncomingPlayerShuttleInReach(game.ball);" in html
    assert "function maybePlayerReceiveIncomingShuttle(ball) {" in html
    assert "if (ball.shooter !== 'neko' || ball.direction !== -1 || ball.awaitingReturnBy) return false;" in html
    assert "var shuttleCourtY = getShuttleCourtY(ball, false);" in html
    assert "var shuttleZ = getShuttleCourtZ(ball, false);" in html
    assert "var hasReachedPlayerSide = ball.crossedNet || shuttleCourtY <= getMidcourtNetY() + 24;" in html
    assert "if (!hasReachedPlayerSide) return false;" in html
    assert "var inPlayerReach = shuttleCourtY <= getMidcourtNetY() + 24" in html
    assert "&& shuttleZ <= screenYToCourtZ(-520)" in html
    assert "&& shuttleZ >= screenYToCourtZ(FLOOR_Y - 8);" in html
    assert "game.duel.activeShooter = 'player';" in html
    assert "game.state = 'ready';" in html
    assert "ball.awaitingReturnBy = 'player';" in html
    assert "ball.returnDeadlineAt = performance.now() + 2400;" in html
    assert "showAssistHint(_i18n('toast.playerReturn', '接住 Yui 的回球'));" in html
    assert "if (game.ball && game.ball.shooter !== shotShooter && !incomingBall) game.ball = null;" in html
    assert "var receivingReturn = isPlayerReceivingReturn();" in html
    assert "var shotPower = receivingReturn ? 56 : game.power;" in html
    assert "queueRacketSwing(game.aimAngle, shotPower, 'player', { forceScore: shouldGuaranteeFirstTutorialShot, incomingBall: receivingReturn ? game.ball : null });" in html

    update_start = html.index("function update(dt) {")
    update_section = html[update_start:html.index("function drawDistanceMarkers()", update_start)]
    assert "if (awaitingPlayerReturn && stepAwaitingPlayerReturn(dt)) return;" in update_section
    assert "function stepAwaitingPlayerReturn(dt) {" in html
    awaiting_start = html.index("function stepAwaitingPlayerReturn(dt) {")
    awaiting_section = html[awaiting_start:html.index("function queueShotResolution", awaiting_start)]
    assert awaiting_section.index("stepBallPhysics(ball, subDt);") < awaiting_section.index("checkShuttleLanding(ball);")
    assert awaiting_section.index("checkShuttleLanding(ball);") < awaiting_section.index("updateNetPhysics(subDt);")
    assert "if (performance.now() > ball.returnDeadlineAt) {" in html
    assert "if (!ball.groundedReturnAt) ball.groundedReturnAt = performance.now();" in html
    assert "if (performance.now() - ball.groundedReturnAt < 1100) continue;" in html
    assert "finishShot(false, 'out', ball);" in html
    assert "if (maybePlayerReceiveIncomingShuttle(b)) break;" in update_section
    assert "b.y < -520" not in update_section
    assert "b.x < -50 || b.x > 950 || b.y > 550" in update_section
    assert update_section.index("checkShuttleLanding(b);") < update_section.index("if (maybePlayerReceiveIncomingShuttle(b)) break;")

    draw_start = html.index("function drawAiming() {")
    draw_section = html[draw_start:html.index("function drawBall()", draw_start)]
    assert "aimingCtx.clearRect(0, 0, BASE_W, BASE_H);" in draw_section
    assert "quadraticCurveTo" not in draw_section
    assert "var meterX" not in draw_section
    assert "fillRect(meterX" not in draw_section

    mousedown = html[
        html.index("function handleBadmintonPointerDown(ev) {"):
        html.index("window.addEventListener('mouseup'")
    ]
    assert "if (isPlayerReceivingReturn()) {\n      game.power = 56;\n      shoot();\n      return;\n    }" in mousedown
    assert mousedown.index("if (isPlayerReceivingReturn())") < mousedown.index("game.charging = true;")


@pytest.mark.unit
def test_badminton_first_tutorial_line_in_is_practice_only():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert "function isPracticeMode() {\n    return false;\n  }" in html
    assert "var shouldGuaranteeFirstTutorialShot = firstTutorialShotGuaranteed && isPracticeMode() && game.tutorialStep === 3;" in html


@pytest.mark.unit
def test_badminton_space_jump_enables_air_smash():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert "var PLAYER_JUMP_SPEED = 430;" in html
    assert "var PLAYER_JUMP_GRAVITY = 1350;" in html
    assert "var PLAYER_SMASH_MIN_JUMP_OFFSET = 28;" in html
    assert "var PLAYER_SMASH_CONTACT_LIFT = 56;" in html
    assert "var PLAYER_SMASH_SWEET_REACH_X = 58;" in html
    assert "var PLAYER_SMASH_SWEET_REACH_Y = 68;" in html
    assert "var PLAYER_SMASH_FORWARD_BIAS_X = 18;" in html
    assert "var PLAYER_SMASH_UPWARD_BIAS_Y = -26;" in html
    assert "playerJump: { offset: 0, velocity: 0, active: false, cooldownUntil: 0, lastSmashAt: 0 }," in html
    assert "function startPlayerJump() {" in html
    assert "function updatePlayerJump(dt) {" in html
    assert "function getPlayerSmashQuality(incomingBall) {" in html
    assert "function getPlayerSmashSweetSpot() {" in html
    assert "function isPlayerSmashReady(incomingBall) {" in html
    assert "return clamp(game.playerCourt.y - getPlayerJumpOffset(), PLAYER_COURT_Y_MIN - PLAYER_JUMP_MAX_OFFSET, PLAYER_COURT_Y_MAX);" in html
    assert "game.playerJump.offset += game.playerJump.velocity * dt;" in html
    assert "game.playerJump.velocity -= PLAYER_JUMP_GRAVITY * dt;" in html
    assert "updatePlayerJump(dt);" in html

    swing_section = html[
        html.index("function buildSwingImpulse(angle, power, shooter, incomingBall) {"):
        html.index("function launchShot(angle, power, shooter, swingImpulse) {")
    ]
    assert "var swingOptions = arguments.length > 4 && arguments[4] ? arguments[4] : {};" in swing_section
    assert "var isSmash = shooter !== 'neko' && !!swingOptions.smash;" in swing_section
    assert "var smashQuality = isSmash ? getPlayerSmashQuality(incomingBall) : 0;" in swing_section
    assert "if (isSmash && smashQuality <= 0) isSmash = false;" in swing_section
    assert "var smashSpeedBonus = isSmash ? 130 + smashQuality * 150 : 0;" in swing_section
    assert "isSmash: isSmash," in swing_section
    assert "smashQuality: smashQuality," in swing_section

    queue_section = html[
        html.index("function queueRacketSwing(angle, power, shooter, options) {"):
        html.index("function returnIncomingPlayerShuttle() {")
    ]
    assert "var smash = shotShooter === 'player' && (options && options.smash || isPlayerSmashReady(incomingBall));" in queue_section
    assert "impulse: buildSwingImpulse(angle, power, shotShooter, incomingBall, { smash: smash })," in queue_section
    assert "showAssistHint(_i18n('toast.smash', '跳杀！'));" in queue_section

    launch_section = html[
        html.index("function launchShot(angle, power, shooter, swingImpulse) {"):
        html.index("function queueRacketSwing(angle, power, shooter, options) {")
    ]
    assert "var launchAngle = impulse.isSmash ? clamp(angle - (20 + impulse.smashQuality * 12), 12, 48) : angle;" in launch_section
    assert "y: origin.y - PLAYER_SMASH_CONTACT_LIFT * (0.7 + impulse.smashQuality * 0.6)" in launch_section
    assert "var smashDownVelocity = impulse.isSmash ? 250 + impulse.smashQuality * 210 + impulse.force * 90 : 0;" in launch_section
    assert "shuttle.vy = impulse.isSmash ? smashDownVelocity : baseVy;" in launch_section
    assert "shuttle.isSmash = !!impulse.isSmash;" in launch_section
    assert "shuttle.smashQuality = impulse.smashQuality || 0;" in launch_section

    keydown = html[
        html.index("window.addEventListener('keydown'"):
        html.index("if (bgmVolumeInput)", html.index("window.addEventListener('keydown'"))
    ]
    assert "key === ' ' || ev.code === 'Space'" in keydown
    assert "if (!ev.repeat) startPlayerJump();" in keydown
    assert "jump: startPlayerJump," in html
    assert "playerJump: {" in html
    assert "isSmash: !!(game.pendingSwing.impulse && game.pendingSwing.impulse.isSmash)," in html
    assert "isSmash: !!game.ball.isSmash," in html


@pytest.mark.unit
def test_badminton_unload_can_retry_pending_route_end_with_beacon():
    html = BADMINTON_TEMPLATE.read_text(encoding="utf-8")

    assert "var routeEndFetchPending = false;" in html
    assert "if (endedRoute && !(useBeacon && routeEndFetchPending && !routeEndBeaconDelivered)) return;" in html
    assert "routeEndFetchPending = true;" in html
    assert "routeEndBeaconDelivered = true;" in html
