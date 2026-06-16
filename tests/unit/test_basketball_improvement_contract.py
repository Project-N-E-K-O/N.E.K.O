import json
import re
from pathlib import Path

import pytest

from config.prompts import prompts_game
from main_routers import game_router, pages_router


ROOT = Path(__file__).resolve().parents[2]
BASKETBALL_TEMPLATE = ROOT / "templates" / "basketball_demo.html"
LOCALES_DIR = ROOT / "static" / "locales"


def _basketball_html() -> str:
    return BASKETBALL_TEMPLATE.read_text(encoding="utf-8")


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
def test_basketball_improvement_static_contract():
    html = _basketball_html()

    for expected in (
        "/static/i18n-i18next.js",
        "/static/game/system/game-audio-system.js",
        "/static/game/games/basketball/basketball-audio-config.js",
        'id="game-audio-controls"',
        'id="game-bgm-volume"',
        'id="game-sfx-volume"',
        'function _i18n(',
        "window.addEventListener('localechange'",
        'id="bb-debug-panel"',
        "data-debug-distance",
        "data-debug-event",
        "function updateDebugReadout()",
        "window.BasketballDemo =",
        "onEvent: function",
        "offEvent: function",
        "DUEL_DIFFICULTY",
        "function setDuelDifficulty(",
        "MOODS =",
        "function setMood(",
        "function syncBgm(",
        "function _bbResolvePlaylist(",
        "function resetSyncKey(",
        "basketballGameAudio.resetSyncKey()",
        "basketballGameAudio.sync(",
        "function scheduleAudioPreload",
        "function autoAdjustMood(",
        "function getPressureLine(",
        "function applyPreGameContext(",
        "function _basketballGameMemoryPolicyPayload(",
        "game_memory_enabled",
        "loadGeneratedQuickLines",
        "userReplyProtectedUntil",
        "function buildGameSummary()",
        "game_summary",
        'data-tab="shooter"',
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
    assert 'data-i18n="basketball.mode.horse"' not in html
    assert "requestedMode === 'horse'" not in html
    assert "nextMode === 'horse'" not in html
    assert 'data-mode="timed"' not in html
    assert 'data-i18n="basketball.mode.timed"' not in html
    assert "requestedMode === 'timed'" not in html
    assert "nextMode === 'timed'" not in html


@pytest.mark.unit
def test_basketball_timed_mode_is_removed_from_template_runtime():
    html = _basketball_html()

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
def test_basketball_horse_mode_is_removed_from_template_runtime():
    html = _basketball_html()

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
def test_basketball_invite_character_request_uses_invited_lanlan_name():
    html = _basketball_html()

    assert "window.__nekoBasketballQueryLanlanName = queryLanlan || '';" in html
    assert "lanlan_name: queryLanlan || ''" in html
    assert "lanlan_name: queryLanlan || 'basketball_demo'" not in html
    assert (
        "var requestedLanlanName = String(window.__nekoBasketballQueryLanlanName || '').trim();"
        in html
    )
    assert "characterPath += '?lanlan_name=' + encodeURIComponent(requestedLanlanName);" in html
    assert "var charResp = await fetch(characterPath);" in html
    assert "var live2dPath = charData.live2d_path || '/static/yui-origin/yui-origin.model3.json';" in html
    assert "await initLive2DAvatar(live2dPath);" in html


@pytest.mark.unit
def test_basketball_i18n_placeholder_token_avoids_jinja_braces():
    html = _basketball_html()

    assert "{% raw %}" in html
    assert "{% endraw %}" in html
    assert "return s.replace('{{' + k + '}}', String(params[k]));" not in html
    assert "var token = '{' + '{' + k + '}' + '}';" in html
    assert "return s.split(token).join(String(params[k]));" in html


@pytest.mark.unit
def test_basketball_hidden_tab_keeps_route_alive():
    html = _basketball_html()

    assert "window.addEventListener('beforeunload', function () { endRoute(true); });" in html
    assert "var pageVisible = !document.hidden;" in html
    assert "visible: pageVisible" in html
    assert "pageVisible: pageVisible" in html
    assert "visibilityState: document.visibilityState || (pageVisible ? 'visible' : 'hidden')" in html
    assert "if (document.hidden) endRoute(true);" not in html


@pytest.mark.unit
def test_basketball_invite_launches_challenge_mode_and_marks_started():
    html = _basketball_html()

    assert "var launchedFromInvite = !!(modeParams && modeParams.get('session_id'));" in html
    assert "basketballInviteRequired" not in html
    assert "var currentMode = requestedMode === 'shooter' ? 'shooter' : 'spectator';" in html
    assert "if (requestedMode === 'duel') currentMode = 'duel';" in html
    assert "launchedFromInvite ? 'duel' : 'shooter'" not in html
    assert "gameStarted: true, game_started: true" in html


@pytest.mark.unit
def test_basketball_rejected_route_start_does_not_activate_frontend_route():
    html = _basketball_html()
    start = html.index("function startRoute() {")
    end = html.index("function startRouteAfterCharacterReady() {", start)
    start_route = html[start:end]

    assert "if (!res || !res.ok) {" in start_route
    assert "routeActive = false;" in start_route
    assert start_route.index("if (!res || !res.ok) {") < start_route.index("routeActive = true;")
    assert start_route.index("routeActive = true;") < start_route.index("heartbeatTimer = setInterval")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_basketball_demo_page_only_renders_shell_without_invite_gate(monkeypatch):
    monkeypatch.setattr(pages_router, "get_templates", lambda: _FakeTemplates())

    result = await pages_router.basketball_demo(_FakePageRequest())

    assert result["template_name"] == "templates/basketball_demo.html"


@pytest.mark.unit
def test_basketball_restart_rotates_route_session():
    html = _basketball_html()

    assert "function createBasketballSessionId() {" in html
    assert "var sessionId = window.__nekoMiniGameInviteSessionId || createBasketballSessionId();" in html
    assert "sessionId = createBasketballSessionId();" in html
    assert "resetVoiceArbiter();" in html
    assert "url.searchParams.delete('session_id');" in html
    assert "startRoute();" in html


@pytest.mark.unit
def test_basketball_personal_stats_ignore_neko_shots():
    html = _basketball_html()

    assert "if (resultEntry && resultEntry.shooter && resultEntry.shooter !== 'player') return;" in html
    assert "recordShotHistory(game.attemptsResults[game.attemptsResults.length - 1]);" in html


@pytest.mark.unit
def test_basketball_duel_applies_llm_difficulty_before_neko_shot():
    html = _basketball_html()

    assert "var pendingControl = game.duel.pendingVoiceControl || null;" in html
    assert "if (pendingControl && pendingControl.difficulty) setDuelDifficulty(pendingControl.difficulty);" in html
    assert "var shot = getNekoDuelShot();" in html
    assert "var shot = game.duel.pendingShot || getNekoDuelShot();" not in html


@pytest.mark.unit
def test_basketball_audio_config_contract():
    source = (ROOT / "static" / "game" / "games" / "basketball" / "basketball-audio-config.js")
    assert source.exists()
    text = source.read_text(encoding="utf-8")
    assert "gameSystem.basketball.audioConfig" in text
    assert "audioMix" in text
    assert "swish" in text
    assert "airBall" in text
    assert "Battle_Theme_1_L.mp3" in text
    assert "hitboll.mp3" in text


@pytest.mark.unit
def test_basketball_i18n_keys_are_registered_in_main_locales():
    required_keys = {
        "basketball.title",
        "basketball.modeSwitcher",
        "basketball.audio.controls",
        "basketball.memoryOption.label",
        "basketball.memoryOption.hint",
        "basketball.mode.spectator",
        "basketball.mode.shooter",
        "basketball.mode.duel",
        "basketball.hud.score",
        "basketball.hud.streak",
        "basketball.hud.record",
        "basketball.hud.duelScore",
        "basketball.hud.duelMisses",
        "basketball.hud.round",
        "basketball.hud.timer",
        "basketball.hud.practice",
        "basketball.hud.yourTurn",
        "basketball.hud.nekoTurn",
        "basketball.hud.unlimitedAttempts",
        "basketball.hud.chances",
        "basketball.hud.practiceTitle",
        "basketball.hud.attemptsTitle",
        "basketball.hud.duelTitle",
        "basketball.hud.assist",
        "basketball.hud.on",
        "basketball.hud.off",
        "basketball.result.title",
        "basketball.result.leaderboard",
        "basketball.result.stats",
        "basketball.result.retry",
        "basketball.result.rating",
        "basketball.result.duel",
        "basketball.result.duelElimination",
        "basketball.result.practice",
        "basketball.result.summary",
        "basketball.result.attemptsSummary",
        "basketball.result.personalBest",
        "basketball.result.globalRank",
        "basketball.result.outcome.youWin",
        "basketball.result.outcome.nekoWin",
        "basketball.result.outcome.tie",
        "basketball.result.outcome.undecided",
        "basketball.leaderboard.title",
        "basketball.leaderboard.global",
        "basketball.leaderboard.local",
        "basketball.leaderboard.shooter",
        "basketball.leaderboard.duel",
        "basketball.leaderboard.empty",
        "basketball.leaderboard.totalPlayers",
        "basketball.leaderboard.yourBest",
        "basketball.leaderboard.recent",
        "basketball.leaderboard.loading",
        "basketball.leaderboard.mode.shooter",
        "basketball.leaderboard.mode.duel",
        "basketball.leaderboard.mode.spectator",
        "basketball.table.score",
        "basketball.table.bestStreak",
        "basketball.table.farthest",
        "basketball.table.mode",
        "basketball.table.date",
        "basketball.debug.title",
        "basketball.debug.collapse",
        "basketball.debug.hide",
        "basketball.debug.distance",
        "basketball.debug.power",
        "basketball.debug.event",
        "basketball.debug.streak",
        "basketball.debug.reset",
        "basketball.debug.guide",
        "basketball.debug.sweet",
        "basketball.debug.markers",
        "basketball.debug.readout.modeState",
        "basketball.debug.readout.distance",
        "basketball.debug.readout.streaks",
        "basketball.debug.readout.score",
        "basketball.debug.readout.difficulty",
        "basketball.debug.readout.duel",
        "basketball.state.ready",
        "basketball.state.in_flight",
        "basketball.state.game_over",
        "basketball.state.neko_thinking",
        "basketball.stats.title",
        "basketball.stats.close",
        "basketball.stats.totalShots",
        "basketball.stats.farRate",
        "basketball.stats.trend",
        "basketball.stats.none",
        "basketball.theme.next",
        "basketball.theme.current",
        "basketball.theme.changed",
        "basketball.theme.labels.default",
        "basketball.theme.labels.sunset",
        "basketball.theme.labels.night",
        "basketball.court.restrictedArc",
        "basketball.court.freeThrowLine",
        "basketball.court.threePointLine",
        "basketball.court.midCourtLine",
        "basketball.toast.difficulty",
        "basketball.toast.nekoShoot",
        "basketball.toast.nekoThinking",
        "basketball.toast.nekoTurn",
        "basketball.toast.copyNeko",
        "basketball.toast.nekoFailed",
        "basketball.toast.yourSet",
        "basketball.toast.yourTurn",
        "basketball.toast.reset",
        "basketball.toast.featureToggled",
        "basketball.toast.feature.guide",
        "basketball.toast.feature.sweet",
        "basketball.toast.feature.bgm",
        "basketball.toast.feature.markers",
        "basketball.toast.state.on",
        "basketball.toast.state.off",
        "basketball.tutorial.aim",
        "basketball.tutorial.charge",
        "basketball.tutorial.release",
        "basketball.shot.swish",
        "basketball.shot.bank",
        "basketball.shot.rim_in",
        "basketball.shot.rim_out",
        "basketball.shot.air_ball",
        "basketball.shot.unknown",
        "basketball.shot.attempt",
        "basketball.lines.fallback",
        "basketball.lines.default.swish",
        "basketball.lines.shooter.swish",
        "basketball.lines.duel.swish",
        "basketball.lines.pressure.lastTied",
        "basketball.lines.pressure.lastAhead",
        "basketball.lines.pressure.lastBehind",
        "basketball.lines.pressure.playerAhead",
        "basketball.lines.pressure.playerBehind",
        "basketball.lines.duel.clutch",
        "basketball.lines.duel.excuse",
        "basketball.lines.mindGame",
        "basketball.lines.easterEgg.lateNight",
        "basketball.lines.easterEgg.xmas",
        "basketball.lines.easterEgg.newYear",
        "basketball.lines.easterEgg.swish3",
        "basketball.lines.easterEgg.swish5",
        "basketball.lines.easterEgg.airball3",
        "basketball.mood.happySuffix",
        "basketball.mood.sadPrefix",
        "basketball.mood.surprisedPrefix",
        "basketball.close",
    }
    line_keys = {
        "swish",
        "bank",
        "rim_in",
        "rim_out",
        "air_ball",
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
        f"basketball.lines.{group}.{line_key}"
        for group in ("default", "shooter", "duel")
        for line_key in line_keys
    )

    for locale_path in LOCALES_DIR.glob("*.json"):
        payload = json.loads(locale_path.read_text(encoding="utf-8"))
        missing = sorted(key for key in required_keys if _get_nested(payload, key) is None)
        assert not missing, f"{locale_path.name} missing basketball i18n keys: {missing}"


@pytest.mark.unit
def test_basketball_runtime_visible_text_uses_i18n_helpers():
    html = _basketball_html()

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

    forbidden_direct_visible_text = (
        "showAssistHint('Neko出手')",
        "showAssistHint('已重置')",
        "leaderboardMeta.textContent = '加载中...'",
        "emptyCell.textContent = '暂无记录'",
        "resultStats.textContent = '对战结果：'",
        "if (themeButton) themeButton.textContent = '主题：'",
        "updateTutorial('上下移动鼠标调整投篮角度')",
    )
    for snippet in forbidden_direct_visible_text:
        assert snippet not in html


@pytest.mark.unit
def test_basketball_backspin_trigger_is_more_forgiving_than_perfect_shot():
    html = _basketball_html()

    assert "function getBackspinRate(" in html
    assert "var angleTolerance = 6;" in html
    assert "var powerPadding = 5;" in html
    assert "spinRate: getBackspinRate(angle, power, game.distance)," in html

    perfect_start = html.index("function isPerfect(")
    perfect_end = html.index("function isDuelMode(")
    perfect_section = html[perfect_start:perfect_end]
    assert "<= 2" in perfect_section
    assert "powerPadding" not in perfect_section


@pytest.mark.unit
def test_basketball_court_distances_use_nba_line_calibration():
    html = _basketball_html()

    for expected in (
        "var NBA_COURT_FEET =",
        "rimFromBaseline: 5.25",
        "midCourtFromBaseline: 47",
        "freeThrowFromBaseline: 19",
        "threePointBreak: 23.75",
        "restrictedArcRadius: 4",
        "var PX_PER_FOOT = 12",
        "var PX_PER_METER = PX_PER_FOOT * NBA_COURT_FEET.feetPerMeter",
        "function feetToShotPx(",
        "function rimDistanceFeetFromBaseline(",
        "var COURT_DISTANCES =",
        "freeThrowLine: feetToShotPx(rimDistanceFeetFromBaseline(NBA_COURT_FEET.freeThrowFromBaseline))",
        "threePointLine: feetToShotPx(NBA_COURT_FEET.threePointBreak)",
        "midCourtLine: feetToShotPx(rimDistanceFeetFromBaseline(NBA_COURT_FEET.midCourtFromBaseline))",
        "restrictedArc: feetToShotPx(NBA_COURT_FEET.restrictedArcRadius)",
        "var COURT_DISTANCE_MARKS =",
        "data-debug-distance-key=\"restrictedArc\"",
        "data-debug-distance-key=\"freeThrowLine\"",
        "data-debug-distance-key=\"threePointLine\"",
        "data-debug-distance-key=\"midCourtLine\"",
        "function refreshCourtDistanceButtons(",
    ):
        assert expected in html

    court_start = html.index("function drawCourt(")
    court_end = html.index("function drawStreakEffect(")
    court_section = html[court_start:court_end]
    for hardcoded_distance in (
        "var laneLeftX = hoopCenterX - 252;",
        "var threeX = hoopCenterX - 405;",
        "var midCourtX = Math.max(86, threeX - 260);",
        "var restrictedRadiusX = 74;",
        "ctx.ellipse(hoopCenterX, BASE_H - 3, 405, 76",
    ):
        assert hardcoded_distance not in court_section

    for stale_debug_distance in (
        'data-debug-distance="150"',
        'data-debug-distance="300"',
        'data-debug-distance="450"',
        'data-debug-distance="600"',
    ):
        assert stale_debug_distance not in html


@pytest.mark.unit
def test_basketball_shot_distance_caps_one_step_beyond_midcourt():
    html = _basketball_html()

    assert "var POST_MIDCOURT_DISTANCE_STEP = 45;" in html
    assert (
        "var MAX_PLAYABLE_SHOT_DISTANCE = COURT_DISTANCES.midCourtLine + POST_MIDCOURT_DISTANCE_STEP;"
        in html
    )
    assert "function getMaxPlayableShotDistance() {" in html
    assert "return MAX_PLAYABLE_SHOT_DISTANCE;" in html
    assert "function advanceShotDistance(step) {" in html
    assert "return Math.min(getMaxPlayableShotDistance(), game.distance + (Number(step) || 0));" in html
    assert "game.distance = advanceShotDistance(nextDistanceStep());" in html
    assert "game.distance = advanceShotDistance(POST_MIDCOURT_DISTANCE_STEP);" in html
    assert "clamp(Number(distance) || 200, 80, getMaxPlayableShotDistance())" in html
    assert "clamp(Number(px) || 200, 80, getMaxPlayableShotDistance())" in html
    assert "game.distance += nextDistanceStep();" not in html
    assert "Math.min(620, game.distance + 45)" not in html


@pytest.mark.unit
def test_basketball_llm_control_contract_accepts_mood_and_difficulty():
    parsed = game_router._parse_control_instructions(
        '认真点喵\n{"mood":"angry","expression":"tease","intensity":"high","difficulty":"max"}',
        game_type="basketball",
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
def test_basketball_duel_prompt_mentions_difficulty_control():
    prompt = prompts_game.get_basketball_system_prompt("zh", mode="duel")

    assert "difficulty" in prompt
    assert "max, lv2, lv3, lv4" in prompt


@pytest.mark.unit
@pytest.mark.parametrize("mode", ("spectator", "shooter", "timed", "horse"))
@pytest.mark.parametrize("lang", ("zh", "en", "ja", "ko", "ru", "es", "pt"))
def test_basketball_non_duel_prompts_do_not_advertise_difficulty_control(lang, mode):
    prompt = prompts_game.get_basketball_system_prompt(lang, mode=mode)

    assert '"difficulty"' not in prompt
    assert "max, lv2, lv3, lv4" not in prompt


@pytest.mark.unit
@pytest.mark.parametrize("lang", ("zh", "en", "ja", "ko", "ru", "es", "pt"))
def test_basketball_horse_system_prompt_does_not_inherit_duel_rules(lang):
    prompt = prompts_game.get_basketball_system_prompt(lang, mode="horse")

    assert "event.mode=horse" in prompt
    assert "event.mode=duel" not in prompt
    assert "player_duel_shot" not in prompt
    assert "neko_duel_shot" not in prompt
    assert "neko_duel_turn" not in prompt
    assert "duel.player_score" not in prompt
    assert "duel.neko_score" not in prompt


@pytest.mark.unit
def test_basketball_horse_system_prompt_uses_horse_end_contract():
    zh = prompts_game.get_basketball_system_prompt("zh", mode="horse")
    en = prompts_game.get_basketball_system_prompt("en", mode="horse")

    assert "本局共有三次失误机会" not in zh
    assert "三次机会用完" not in zh
    assert "attempts_remaining" not in zh
    assert "HORSE 字母已经结算出胜负" in zh
    assert "the run has three miss chances" not in en
    assert "all three chances are gone" not in en
    assert "attempts_remaining" not in en
    assert "HORSE letters have decided the result" in en


@pytest.mark.unit
def test_basketball_horse_system_prompt_matches_chat_event_payload():
    zh = prompts_game.get_basketball_system_prompt("zh", mode="horse")
    en = prompts_game.get_basketball_system_prompt("en", mode="horse")

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
def test_basketball_quick_lines_mode_prompts_are_distinct_and_localized(lang):
    spectator = prompts_game.get_basketball_quick_lines_prompt(lang, mode="spectator")

    for mode in ("duel", "shooter", "timed", "horse"):
        prompt = prompts_game.get_basketball_quick_lines_prompt(lang, mode=mode)
        assert prompt != spectator
        if lang != "en":
            assert "Current mode is" not in prompt


@pytest.mark.unit
def test_basketball_english_quick_lines_do_not_mix_mode_suffixes():
    timed = prompts_game.get_basketball_quick_lines_prompt("en", mode="timed")
    horse = prompts_game.get_basketball_quick_lines_prompt("en", mode="horse")

    assert "Current mode is timed" in timed
    assert "Current mode is shooter" not in timed
    assert "Current mode is HORSE" in horse
    assert "Current mode is duel" not in horse


@pytest.mark.unit
def test_basketball_zh_horse_quick_lines_do_not_inherit_duel_prompt():
    prompt = prompts_game.get_basketball_quick_lines_prompt("zh", mode="horse")

    assert "当前模式是 HORSE" in prompt
    assert "篮球对战回合" not in prompt
    assert "轮流出手" not in prompt
    assert "比分和对战节奏" not in prompt
    assert "duel" not in prompt


@pytest.mark.unit
def test_basketball_prompt_localizations_do_not_fallback_to_english():
    english_spectator = prompts_game.get_basketball_system_prompt("en", mode="spectator")
    english_duel = prompts_game.get_basketball_system_prompt("en", mode="duel")
    english_shooter = prompts_game.get_basketball_system_prompt("en", mode="shooter")
    english_timed = prompts_game.get_basketball_system_prompt("en", mode="timed")
    english_horse = prompts_game.get_basketball_system_prompt("en", mode="horse")
    english_quick = prompts_game.get_basketball_quick_lines_prompt("en", mode="spectator")
    english_pregame = prompts_game.get_basketball_pregame_context_prompt("en")

    assert english_timed != english_spectator
    assert english_horse != english_spectator
    assert english_timed != english_shooter
    assert english_horse != english_duel
    assert "event.mode=timed" in english_timed
    assert "event.mode=horse" in english_horse
    assert prompts_game.get_basketball_quick_lines_prompt("zh", mode="timed") != (
        prompts_game.get_basketball_quick_lines_prompt("zh", mode="shooter")
    )
    assert prompts_game.get_basketball_quick_lines_prompt("zh", mode="horse") != (
        prompts_game.get_basketball_quick_lines_prompt("zh", mode="duel")
    )

    for lang in ("zh", "ja", "ko", "ru", "es", "pt"):
        assert prompts_game.get_basketball_system_prompt(lang, mode="spectator") != english_spectator
        assert prompts_game.get_basketball_system_prompt(lang, mode="duel") != english_duel
        assert prompts_game.get_basketball_system_prompt(lang, mode="shooter") != english_shooter
        assert prompts_game.get_basketball_system_prompt(lang, mode="timed") != english_timed
        assert prompts_game.get_basketball_system_prompt(lang, mode="horse") != english_horse
        assert prompts_game.get_basketball_quick_lines_prompt(lang, mode="spectator") != english_quick
        assert prompts_game.get_basketball_pregame_context_prompt(lang) != english_pregame


@pytest.mark.unit
def test_basketball_pregame_context_normalize_and_prompt_injection():
    context, invalid = game_router._normalize_basketball_pregame_context(
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
        "basketball",
        "Neko",
        "傲娇猫娘",
        pre_game_context=context,
        language="zh",
        mode="duel",
    )
    assert "投篮开局上下文" in prompt
    assert "来比一局" in prompt
    assert "对战难度控制补充" in prompt


@pytest.mark.unit
def test_basketball_pregame_opening_line_keeps_spec_length_cap():
    context, invalid = game_router._normalize_basketball_pregame_context(
        {
            "openingLine": "1234567890123456",
        },
        mode="spectator",
    )

    assert invalid is True
    assert context["openingLine"] == ""


@pytest.mark.unit
def test_basketball_duel_balance_hint_and_anger_cap():
    hint = game_router._build_basketball_duel_balance_hint(
        {"duel": {"player_score": 1, "neko_score": 6, "round": 2, "max_rounds": 8}}
    )
    assert hint["state"] == "neko_leading"
    assert hint["diff"] == 5
    assert hint["remainingPoints"] == 12

    final_pending = game_router._build_basketball_duel_balance_hint(
        {"duel": {"player_score": 6, "neko_score": 4, "round": 5, "max_rounds": 5, "active_shooter": "neko"}}
    )
    assert final_pending["state"] == "player_leading"
    assert final_pending["remainingRounds"] == 0
    assert final_pending["remainingPoints"] == 2

    miss_pressure = game_router._build_basketball_duel_balance_hint(
        {"duel": {"player_score": 4, "neko_score": 6, "round": 6, "player_misses": 2, "neko_misses": 1, "max_misses": 3}}
    )
    assert miss_pressure["playerMissesLeft"] == 1
    assert miss_pressure["nekoMissesLeft"] == 2
    assert miss_pressure["maxMisses"] == 3

    current_state_fallback = game_router._build_basketball_duel_balance_hint(
        {"currentState": {"duel": {"playerScore": 1, "nekoScore": 5, "playerMisses": 2, "nekoMisses": 0, "maxMisses": 3}}}
    )
    assert current_state_fallback["state"] == "neko_leading"
    assert current_state_fallback["diff"] == 4
    assert current_state_fallback["playerMissesLeft"] == 1

    merged_current_state = game_router._build_basketball_duel_balance_hint(
        {
            "duel": {"player_score": 4},
            "currentState": {"duel": {"playerScore": 1, "nekoScore": 6, "playerMisses": 2, "nekoMisses": 1, "maxMisses": 3}},
        }
    )
    assert merged_current_state["diff"] == 2
    assert merged_current_state["playerMissesLeft"] == 1
    assert merged_current_state["nekoMissesLeft"] == 2

    miss_elimination_ignores_round_decider = game_router._build_basketball_duel_balance_hint(
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
    cap = game_router._build_basketball_duel_anger_pressure_cap(event, route_state)
    assert cap["reached"] is True

    result = game_router._apply_basketball_anger_pressure_cap(
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
def test_basketball_completed_result_records_score_before_returning():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")

    assert "function persistCompletedResult() {" in html
    assert "if (game.resultRecorded || isPracticeMode()) return null;" in html
    assert "var entry = recordGame(game.bestStreak, getRunMaxDistancePx(), game.totalScore, game.shotTypeCount);" in html
    assert "persistCompletedResult();" in html


@pytest.mark.unit
def test_basketball_scoring_waits_for_route_end_and_records_run_max():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")

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
def test_basketball_reset_abandons_active_route_before_rotating_session():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")
    reset_start = html.index("function resetGame() {")
    reset_section = html[reset_start:html.index("function updateHud()", reset_start)]
    end_route_start = html.index("function endRoute(")
    end_route_section = html[end_route_start:html.index("function cycleTheme()", end_route_start)]

    assert "var shouldRestartRoute = endedRoute || routeActive || heartbeatTimer || drainTimer;" in reset_section
    assert "if (!endedRoute) endRoute(false);" in reset_section
    assert "sessionId = createBasketballSessionId();" in reset_section
    assert "startRoute();" in reset_section
    assert reset_section.index("if (!endedRoute) endRoute(false);") < reset_section.index("sessionId = createBasketballSessionId();")
    assert "routeActive = false;" in end_route_section
    assert "var routeEndSessionId = sessionId;" in end_route_section
    assert "if (res && res.state && sessionId === routeEndSessionId) applyRouteIdentity(res.state);" in end_route_section


@pytest.mark.unit
def test_basketball_generated_quick_lines_override_static_i18n_lines():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")

    assert "var generatedQuickLines = {};" in html
    assert "var generated = generatedQuickLines[key] || [];" in html
    assert "if (generated.length) return generated[Math.floor(Math.random() * generated.length)] || '';" in html
    assert "generatedQuickLines[key] = pool;" in html


@pytest.mark.unit
def test_basketball_route_end_payload_contains_archive_score():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")

    assert "finalScore: {" in html
    assert "player: game.totalScore," in html
    assert "ai: isDuelMode() ? game.duel.nekoScore : 0," in html
    assert "var roundCompleted = game.state === 'game_over';" in html
    assert "reason: roundCompleted ? 'basketball_game_over' : 'basketball_abandoned'," in html
    assert "roundCompleted: roundCompleted," in html
    assert "round_completed: roundCompleted," in html
    assert "postgameProactive: roundCompleted," in html
    assert "state: game.state," in html
    assert "currentState: {\n        game: 'basketball',\n        state: game.state,\n        mode: currentMode,\n        score: {" in html
    assert "max_distance_px: getRunMaxDistancePx()," in html


@pytest.mark.unit
def test_basketball_chat_replies_are_ignored_after_session_or_mode_changes():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")
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
def test_basketball_route_start_timeout_covers_backend_pregame_generation():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")

    assert "_basketballGameMemoryPolicyPayload()), 22000).then(function (res) {" in html


@pytest.mark.unit
def test_basketball_heartbeat_sends_live_current_state():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")
    heartbeat_index = html.index("post('/route/heartbeat'")
    heartbeat_section = html[max(0, heartbeat_index - 500):heartbeat_index + 500]
    current_state_start = html.index("function buildBasketballCurrentStatePayload() {")
    current_state_section = html[current_state_start:html.index("function sendGameEvent(", current_state_start)]

    assert "post('/route/heartbeat'" in heartbeat_section
    assert "currentState: buildBasketballCurrentStatePayload()" in heartbeat_section
    assert re.search(r"score:\s*{\s*player:\s*game\.totalScore", current_state_section)
    assert re.search(
        r"ai:\s*isDuelMode\(\)\s*\?\s*game\.duel\.nekoScore\s*:\s*0",
        current_state_section,
    )
    assert re.search(r"total_score:\s*game\.totalScore", current_state_section)


@pytest.mark.unit
def test_basketball_memory_toggle_does_not_auto_enable_from_history():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")
    init_start = html.index("function _initBasketballGameMemoryToggle() {")
    init_section = html[init_start:html.index("function getAudioCtx()", init_start)]

    assert "gameMemoryToggle.checked = false;" in init_section
    assert "_hasHistoricalBasketballRecord" not in html
    assert "bb_record_distance" not in init_section
    assert "bb_leaderboard" not in init_section


@pytest.mark.unit
def test_basketball_duel_player_shots_update_recorded_stats():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")
    finish_duel = html[html.index("function finishDuelShot("):html.index("function finishShot(", html.index("function finishDuelShot("))]

    assert "if (shooter === 'player') {" in finish_duel
    assert "game.streak += 1;" in finish_duel
    assert "game.madeCount += 1;" in finish_duel
    assert "game.bestStreak = Math.max(game.bestStreak, game.streak);" in finish_duel
    assert "if (game.shotTypeCount[shotType] != null) game.shotTypeCount[shotType] += 1;" in finish_duel
    assert "newRecord = previousDistance > game.recordDistance;" in finish_duel
    assert "game.recordDistance = previousDistance;" in finish_duel
    assert "localStorage.setItem('bb_record_distance', String(Math.round(game.recordDistance)));" in finish_duel
    assert "kind: newRecord ? 'new_record' : (scored ? 'shot_result' : 'shot_missed')," in finish_duel
    assert "is_new_record: newRecord," in finish_duel
    assert "game.streak = 0;" in finish_duel


@pytest.mark.unit
def test_basketball_duel_uses_three_miss_elimination_instead_of_five_round_cap():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")
    finish_duel = html[html.index("function finishDuelShot("):html.index("function finishShot(", html.index("function finishDuelShot("))]

    assert "playerMisses: 0," in html
    assert "nekoMisses: 0," in html
    assert "maxMisses: 3," in html
    assert "game.duel.playerMisses = 0;" in html
    assert "game.duel.nekoMisses = 0;" in html
    assert "if (!scored) {" in finish_duel
    assert "if (shooter === 'player') game.duel.playerMisses += 1;" in finish_duel
    assert "else game.duel.nekoMisses += 1;" in finish_duel
    duel_finished = "var duelFinished = game.duel.playerMisses >= game.duel.maxMisses || game.duel.nekoMisses >= game.duel.maxMisses;"
    assert duel_finished in finish_duel
    assert finish_duel.index(duel_finished) < finish_duel.index("kind: newRecord ? 'new_record' : (scored ? 'shot_result' : 'shot_missed'),")
    assert "if (!duelFinished) {\n      sendGameEvent({" in finish_duel
    assert "} else if (shooter === 'player') {" in finish_duel
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
def test_basketball_normal_chat_request_carries_client_timeout_for_memory_guard():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")
    chat_start = html.index("var chatClientTimeoutMs = 6500;")
    chat_section = html[chat_start:html.index(".then(function (res) {", chat_start)]

    assert "client_timeout_ms: chatClientTimeoutMs" in chat_section
    assert "}), chatClientTimeoutMs)" in chat_section


@pytest.mark.unit
def test_basketball_user_reply_voice_deadline_survives_inflight_guard():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")
    speak_start = html.index("function speakLine(line, control, event) {")
    speak_section = html[speak_start:html.index("function getActiveAvatarContainer()", speak_start)]

    assert "if (isUserReply) {" in speak_section
    assert "voiceArbiter.inFlight.expiresAt + VOICE_ARBITER_DEFAULTS.tailWaitMs" in speak_section
    assert "entry.expiresAt = Math.max(" in speak_section


@pytest.mark.unit
def test_basketball_drain_reads_nested_result_line():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")

    assert "var result = item && typeof item.result === 'object' ? item.result : null;" in html
    assert "(result && (result.line || result.text || result.content))" in html
    assert "var control = (item && item.control) || (result && result.control) || {};" in html
    assert "speakLine(line, control, Object.assign({" in html
    assert "kind: 'user_reply'," in html


@pytest.mark.unit
def test_basketball_duel_voice_request_carries_client_timeout_for_memory_guard():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")
    voice_start = html.index("function buildNekoDuelTurnEvent() {")
    voice_section = html[voice_start:html.index("function queueNekoDuelTurnVoice()", voice_start)]

    assert "client_timeout_ms: 2200" in voice_section


@pytest.mark.unit
def test_basketball_voice_entries_freeze_route_identity():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")

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
def test_basketball_delayed_results_are_bound_to_session():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")

    assert "var resultTimer = 0;" in html
    assert "function scheduleShowResult(delayMs) {" in html
    assert "var resultSessionId = sessionId;" in html
    assert "persistCompletedResult();" in html
    assert "if (sessionId !== resultSessionId || game.state !== 'game_over') return;" in html
    assert "clearTimeout(resultTimer);" in html
    assert "setTimeout(showResult, 900);" not in html
    assert "setTimeout(showResult, 500);" not in html


@pytest.mark.unit
def test_basketball_starts_route_after_character_resolution_before_avatar_loading():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")

    assert "initNekoAvatar().finally(function () { startRoute(); });" not in html
    assert "var basketballCharacterPromise = null;" in html
    assert "return basketballCharacterPromise;" in html
    assert "function startRouteAfterCharacterReady() {" in html
    assert "loadBasketballCharacter().finally(function () { startRoute(); });" in html
    assert "var routeLanlanName = getRouteLanlanName();" in html
    assert "var routeSessionId = sessionId;" in html
    assert "lanlan_name: routeLanlanName" in html
    assert "if (sessionId !== routeSessionId || endedRoute || game.state === 'game_over') return res;" in html
    assert "applyRouteIdentity(res.state);" in html
    startup = html[html.rindex("startRouteAfterCharacterReady();"):]
    assert startup.index("startRouteAfterCharacterReady();") < startup.index("initNekoAvatar();")


@pytest.mark.unit
def test_basketball_vrm_waits_for_three_before_resolving_modules():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")
    wait_start = html.index("function waitForVRMModules() {")
    wait_section = html[wait_start:html.index("function fitVRMToContainer(", wait_start)]

    assert "function waitForThreeModule() {" in wait_section
    assert "if (window.THREE) return Promise.resolve();" in wait_section
    assert "window.addEventListener('three-ready', resolve, { once: true });" in wait_section
    assert "return Promise.all([vrmModulesReady, waitForThreeModule()]).then(function () {});" in wait_section


@pytest.mark.unit
def test_basketball_duel_avatars_use_enlarged_layout_constants():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")

    assert "var YUI_SHOOTER_W = 320;" in html
    assert "var YUI_SHOOTER_H = 480;" in html
    assert "var PLAYER_AVATAR_W = 292;" in html
    assert "var PLAYER_AVATAR_H = 476;" in html
    assert "var PLAYER_FIGURE_SCALE = 1.38;" in html
    assert "playerSenseiContainer.style.width = PLAYER_AVATAR_W + 'px';" in html
    assert "playerSenseiContainer.style.height = PLAYER_AVATAR_H + 'px';" in html
    assert "container.style.width = YUI_SHOOTER_W + 'px';" in html
    assert "container.style.height = YUI_SHOOTER_H + 'px';" in html
    assert "var avatarW = yuiShooterLayout ? YUI_SHOOTER_W : 180;" in html
    assert "var avatarH = yuiShooterLayout ? YUI_SHOOTER_H : 270;" in html
    assert "Math.round(sx - PLAYER_AVATAR_W / 2)" in html
    assert "Math.round(sy - PLAYER_AVATAR_H)" in html
    draw_start = html.index("function drawPlayer() {")
    draw_section = html[draw_start:html.index("function drawAiming()", draw_start)]
    assert "var s = PLAYER_FIGURE_SCALE;" in draw_section
    assert "ctx.arc(px, py - 62 * s, 16 * s" in draw_section
    assert "ctx.lineWidth = 8 * s;" in draw_section


@pytest.mark.unit
def test_basketball_mouse_input_is_gated_to_player_controlled_shots():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")

    assert "function canPlayerControlShot() {" in html
    assert "return game.state === 'ready' && !isPositionTransitioning && isPlayerTurn();" in html

    mousemove = html[
        html.index("canvas.addEventListener('mousemove'"):
        html.index("canvas.addEventListener('mousedown'")
    ]
    assert "if (!canPlayerControlShot()) return;" in mousemove
    assert mousemove.index("if (!canPlayerControlShot()) return;") < mousemove.index("game.aimAngle =")

    mousedown = html[
        html.index("canvas.addEventListener('mousedown'"):
        html.index("window.addEventListener('mouseup'")
    ]
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
def test_basketball_first_tutorial_swish_is_practice_only():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")

    assert "var shouldGuaranteeFirstTutorialShot = firstTutorialShotGuaranteed && isPracticeMode() && game.tutorialStep === 3;" in html


@pytest.mark.unit
def test_basketball_unload_can_retry_pending_route_end_with_beacon():
    html = BASKETBALL_TEMPLATE.read_text(encoding="utf-8")

    assert "var routeEndFetchPending = false;" in html
    assert "if (endedRoute && !(useBeacon && routeEndFetchPending && !routeEndBeaconDelivered)) return;" in html
    assert "routeEndFetchPending = true;" in html
    assert "routeEndBeaconDelivered = true;" in html
