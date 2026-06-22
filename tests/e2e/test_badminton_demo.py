import re
import time

import pytest
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeoutError, expect

from main_routers import system_router


def _install_badminton_test_hooks(page: Page) -> None:
    page.add_init_script(
        """
        (() => {
          if (sessionStorage.getItem('__badmintonE2ELocalStorageCleared') !== '1') {
            localStorage.clear();
            sessionStorage.setItem('__badmintonE2ELocalStorageCleared', '1');
          }
          window.__badmintonE2EEvents = [];
          window.AudioContext = window.AudioContext || function () {
            return { state: 'running', currentTime: 0, resume: async () => {},
              createOscillator: () => ({ type: 'sine', frequency: { setValueAtTime(){}, exponentialRampToValueAtTime(){} }, connect(){}, start(){}, stop(){} }),
              createGain: () => ({ gain: { setValueAtTime(){}, exponentialRampToValueAtTime(){}, linearRampToValueAtTime(){} }, connect(){} }),
              createBuffer: () => ({ getChannelData: () => new Float32Array(1) }),
              createBufferSource: () => ({ buffer: null, connect(){}, start(){} }),
              createBiquadFilter: () => ({ type: 'lowpass', frequency: { setValueAtTime(){}, exponentialRampToValueAtTime(){} }, Q: { setValueAtTime(){} }, gain: { setValueAtTime(){} }, connect(){} }),
              createDynamicsCompressor: () => ({ threshold: { setValueAtTime(){} }, knee: { setValueAtTime(){} }, ratio: { setValueAtTime(){} }, attack: { setValueAtTime(){} }, release: { setValueAtTime(){} }, connect(){} }),
              createDelay: () => ({ delayTime: { setValueAtTime(){} }, connect(){} }),
              destination: {}
            };
          };
          window.webkitAudioContext = window.AudioContext;
        })();
        """
    )


def _goto_badminton(
    page: Page,
    running_server: str,
    mode: str,
    debug: bool = True,
    wait_loading: bool = True,
    auto_start: bool = True,
) -> None:
    _install_badminton_test_hooks(page)
    lanlan_name = "e2e-yui"
    session_id = f"e2e-badminton-{mode}"
    state = system_router._mini_game_invite_get_state(lanlan_name)
    state["delivered_at"] = time.time() - 1
    state["responded_at"] = time.time()
    state["pending_session_id"] = session_id
    state["last_game_type"] = "badminton"
    debug_query = "&debug=1" if debug else ""
    page.goto(
        f"{running_server}/badminton_demo"
        f"?mode={mode}&lanlan_name={lanlan_name}&session_id={session_id}{debug_query}"
    )
    expect(page.locator("#game")).to_be_attached(timeout=15000)
    page.wait_for_function("window.BadmintonDemo && window.BadmintonDemo.getState")
    if not wait_loading:
        return
    page.wait_for_function(
        """() => {
          const loading = document.getElementById('badminton-loading');
          return !loading || window.__badmintonInitialLoadingHidden === true || loading.hidden === true;
        }""",
        timeout=10000,
    )
    if auto_start:
        start_button = page.locator("#badminton-start-button")
        try:
            start_button.wait_for(state="visible", timeout=3000)
            start_button.click()
            expect(page.locator("#badminton-start-overlay")).not_to_be_visible(timeout=3000)
        except PlaywrightTimeoutError:
            pass


@pytest.mark.e2e
def test_badminton_start_tutorial_memory_and_never_prompt(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel", auto_start=False)

    expect(page.locator("#badminton-start-overlay")).to_be_visible(timeout=3000)
    expect(page.locator("#badminton-start-tutorial")).to_be_visible()
    duel_rule = page.locator('#badminton-start-tutorial [data-i18n="badminton.startTutorial.duel11"]')
    expect(duel_rule).to_contain_text("11")
    expect(duel_rule).not_to_contain_text("3")
    expect(page.locator("#badminton-start-overlay #game-memory-option")).to_be_visible()
    expect(page.locator("#badminton-start-never-option")).to_be_visible()

    assert page.evaluate("window.BadmintonDemo.getState().canControlShot") is False
    page.locator("#bd-game-memory-toggle").check()
    page.locator("#bd-start-never-toggle").check()
    page.locator("#badminton-start-button").click()
    expect(page.locator("#badminton-start-overlay")).not_to_be_visible(timeout=3000)
    page.wait_for_function("window.BadmintonDemo.getState().canControlShot === true", timeout=5000)
    assert page.evaluate("localStorage.getItem('bd_start_tutorial_dismissed')") == "1"

    page.reload()
    page.wait_for_function(
        """() => {
          const loading = document.getElementById('badminton-loading');
          return window.__badmintonInitialLoadingHidden === true || (loading && loading.hidden === true);
        }""",
        timeout=10000,
    )
    expect(page.locator("#badminton-start-overlay")).not_to_be_visible()
    page.wait_for_function("window.BadmintonDemo.getState().canControlShot === true", timeout=5000)


def _force_shot_result(page: Page, scored: bool = True) -> None:
    before_results = page.evaluate("window.BadmintonDemo.getState().attemptsResults.length")
    page.evaluate(
        """(scored) => {
          const api = window.BadmintonDemo;
          api._debugFinishShot(scored, scored ? 'line_in' : 'out');
        }""",
        scored,
    )
    page.wait_for_function(
        """(beforeResults) => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          return state && state.attemptsResults.length > beforeResults;
        }""",
        arg=before_results,
    )
    page.wait_for_function(
        """() => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          return state && (state.state === 'ready' || state.state === 'neko_thinking' || state.state === 'game_over');
        }"""
    )


@pytest.mark.e2e
def test_badminton_legacy_modes_fall_back_to_duel(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "spectator")

    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready'")
    state = page.evaluate("window.BadmintonDemo.getState()")
    assert state["mode"] == "duel"
    expect(page.locator('[data-mode="spectator"]')).to_have_count(0)
    expect(page.locator('[data-mode="shooter"]')).to_have_count(0)


@pytest.mark.e2e
def test_badminton_duel_eleven_player_misses_and_restart(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "shooter")

    for _ in range(11):
        _force_shot_result(page, False)

    expect(page.locator("#result-panel")).to_have_class(re.compile(r"\bshow\b"))
    state = page.evaluate("window.BadmintonDemo.getState()")
    assert state["state"] == "game_over"
    assert state["mode"] == "duel"
    assert state["duel"]["player_misses"] == 11
    assert state["score"] == 0
    page.locator("#restart-button").click()
    page.wait_for_function("window.BadmintonDemo.getState().score === 0")


@pytest.mark.e2e
def test_badminton_mode_switcher_is_removed(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "shooter")

    expect(page.locator("#mode-switcher")).to_have_count(0)
    state = page.evaluate("window.BadmintonDemo.getState()")
    assert state["mode"] == "duel"


@pytest.mark.e2e
def test_badminton_local_leaderboard_records_game(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    _force_shot_result(page, True)
    for _ in range(11):
        _force_shot_result(page, False)

    page.locator("#leaderboard-button").click()
    expect(page.locator("#leaderboard-panel")).to_have_class(re.compile(r"\bshow\b"))
    page.locator('.lb-tab[data-tab="local"]').click()
    expect(page.locator("#leaderboard-body tr")).to_have_count(1)


@pytest.mark.e2e
def test_badminton_public_api_and_debug_panel(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    state = page.evaluate("window.BadmintonDemo.getState()")
    assert {"mode", "state", "score", "streak", "distance"} <= set(state)
    assert page.evaluate("window.BadmintonDemo.setDuelDifficulty('max'); window.BadmintonDemo.getDifficulty()") == "max"
    assert page.evaluate("window.BadmintonDemo.setMood('happy'); window.BadmintonDemo.getMood()") == "happy"
    expect(page.locator("#bd-debug-panel")).to_be_visible()


@pytest.mark.e2e
def test_badminton_player_swing_hides_held_shuttle_then_launches_physical_shuttle(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready' && window.BadmintonDemo.getState().canControlShot")
    initial = page.evaluate("window.BadmintonDemo.getState()")
    assert initial["heldShuttleVisible"] is True
    assert initial["pendingSwing"] is None
    assert initial["currentShuttle"] is None

    page.evaluate("window.BadmintonDemo.shoot()")
    swinging = page.evaluate("window.BadmintonDemo.getState()")
    assert swinging["state"] == "swinging"
    assert swinging["heldShuttleVisible"] is False
    assert swinging["pendingSwing"]["shooter"] == "player"
    assert swinging["currentShuttle"] is None

    page.wait_for_function(
        """() => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          return state && state.state === 'in_flight' && state.currentShuttle;
        }"""
    )
    launched = page.evaluate("window.BadmintonDemo.getState()")
    shuttle = launched["currentShuttle"]
    assert launched["heldShuttleVisible"] is False
    assert launched["pendingSwing"] is None
    assert shuttle["id"] >= 1
    assert shuttle["radius"] == 18
    assert shuttle["diameter"] == 36
    assert shuttle["massKg"] == 0.005
    assert shuttle["dragPerSecond"] == 0.42
    assert shuttle["swingForce"] >= 0
    assert shuttle["shooter"] == "player"


@pytest.mark.e2e
def test_badminton_player_can_move_while_own_shuttle_is_in_flight(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready' && window.BadmintonDemo.getState().canControlShot")
    before_shot = page.evaluate("window.BadmintonDemo.getState().playerCourt")
    page.evaluate("window.BadmintonDemo.shoot()")
    page.wait_for_function(
        """() => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          return state && state.state === 'in_flight' &&
            state.currentShuttle && state.currentShuttle.shooter === 'player';
        }"""
    )

    viewport = page.viewport_size or {"width": 1280, "height": 720}
    page.mouse.move(viewport["width"] - 8, 24)
    page.wait_for_function(
        """(beforeX) => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          return state && state.playerCourt &&
            state.playerCourt.targetX > beforeX + 16 &&
            state.playerCourt.x > beforeX + 16;
        }""",
        arg=before_shot["targetX"],
    )

    moved = page.evaluate("window.BadmintonDemo.getState()")
    assert moved["state"] == "in_flight"
    assert moved["currentShuttle"]["shooter"] == "player"
    assert moved["playerCourt"]["targetX"] > before_shot["targetX"]
    assert moved["playerCourt"]["x"] > before_shot["x"]
    assert moved["charging"] is False
    assert moved["pendingSwing"] is None


@pytest.mark.e2e
def test_badminton_shuttle_flight_decelerates_then_drops(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready' && window.BadmintonDemo.getState().canControlShot")
    page.evaluate("window.BadmintonDemo.shoot()")
    page.wait_for_function(
        """() => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          return state && state.state === 'in_flight' && state.currentShuttle;
        }"""
    )
    launched = page.evaluate("window.BadmintonDemo.getState().currentShuttle")
    initial_vx = abs(launched["vx"])
    initial_vy = launched["vy"]
    assert initial_vx > 100

    page.wait_for_timeout(220)
    midflight = page.evaluate("window.BadmintonDemo.getState().currentShuttle")
    assert midflight is not None
    assert abs(midflight["vx"]) < initial_vx * 0.97
    assert abs(midflight["vx"]) > initial_vx * 0.68

    page.wait_for_timeout(140)
    lateflight = page.evaluate("window.BadmintonDemo.getState().currentShuttle")
    assert lateflight is not None
    assert lateflight["vy"] > max(initial_vy, midflight["vy"]) + 45


@pytest.mark.e2e
def test_badminton_space_jump_turns_air_swing_into_smash(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready' && window.BadmintonDemo.getState().canControlShot")
    page.evaluate(
        """() => {
          window.dispatchEvent(new KeyboardEvent('keydown', {
            key: ' ',
            code: 'Space',
            bubbles: true,
            cancelable: true
          }));
        }"""
    )
    page.wait_for_function(
        """() => {
          const api = window.BadmintonDemo;
          const state = api && api.getState();
          if (!state || !state.playerJump || !state.playerJump.smashReady || !state.canControlShot) return false;
          window.__badmintonSmashReadySnapshot = state.playerJump;
          api.shoot();
          return true;
        }""",
        timeout=2000,
    )
    jumping = page.evaluate("window.__badmintonSmashReadySnapshot")
    assert jumping["smashReady"] is True

    swinging = page.evaluate("window.BadmintonDemo.getState()")
    assert swinging["state"] in {"swinging", "in_flight"}
    if swinging["state"] == "swinging":
        assert swinging["pendingSwing"]["shooter"] == "player"
        assert swinging["pendingSwing"]["isSmash"] is True
        assert swinging["pendingSwing"]["smashQuality"] > 0
    else:
        assert swinging["currentShuttle"]["shooter"] == "player"
        assert swinging["currentShuttle"]["isSmash"] is True
        assert swinging["currentShuttle"]["smashQuality"] > 0

    page.wait_for_function(
        """() => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          return state && state.state === 'in_flight' && state.currentShuttle && state.currentShuttle.isSmash;
        }""",
        timeout=3000,
    )
    launched = page.evaluate("window.BadmintonDemo.getState()")
    assert launched["currentShuttle"]["shooter"] == "player"
    assert launched["currentShuttle"]["isSmash"] is True
    assert launched["currentShuttle"]["smashQuality"] > 0
    assert launched["currentShuttle"]["vy"] > 0
    initial_screen_y = launched["currentShuttle"]["screenY"]

    page.wait_for_timeout(180)
    descending = page.evaluate("window.BadmintonDemo.getState()")
    assert descending["currentShuttle"]["isSmash"] is True
    assert descending["currentShuttle"]["screenY"] > initial_screen_y + 8


@pytest.mark.e2e
def test_badminton_player_serve_hint_draws_or_attaches_shuttle_above_player(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready' && window.BadmintonDemo.getState().heldShuttleVisible")
    page.wait_for_function(
        """() => {
          const container = document.getElementById('player-sensei-vrm-container');
          if (container && container.dataset.heldShuttle3d === 'ready') {
            window.__bdHeldShuttleSample = { heldShuttle3d: true, brightPixels: 0 };
            return true;
          }
          const state = window.BadmintonDemo.getState();
          const canvas = document.getElementById('game');
          const ctx = canvas.getContext('2d');
          const scaleX = canvas.width / 900;
          const scaleY = canvas.height / 500;
          const hintX = state.playerCourt.x + 44;
          const hintY = state.playerCourt.y - 108;
          const sx = Math.max(0, Math.round((hintX - 22) * scaleX));
          const sy = Math.max(0, Math.round((hintY - 24) * scaleY));
          const sw = Math.round(44 * scaleX);
          const sh = Math.round(48 * scaleY);
          const data = ctx.getImageData(sx, sy, sw, sh).data;
          let bright = 0;
          for (let i = 0; i < data.length; i += 4) {
              if (data[i] > 210 && data[i + 1] > 210 && data[i + 2] > 210 && data[i + 3] > 120) bright++;
            }
          window.__bdHeldShuttleSample = { heldShuttle3d: false, brightPixels: bright };
          return bright > 12;
        }""",
        timeout=2000,
    )
    sample = page.evaluate("window.__bdHeldShuttleSample")
    assert sample["heldShuttle3d"] is True or sample["brightPixels"] > 12


@pytest.mark.e2e
def test_badminton_player_court_position_follows_mouse_x_axis_only(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready' && window.BadmintonDemo.getState().canControlShot")
    box = page.locator("#game").bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] * 0.12, box["y"] + box["height"] * 0.42)
    page.wait_for_timeout(350)
    left_state = page.evaluate("window.BadmintonDemo.getState()")
    page.mouse.move(box["x"] + box["width"] * 0.55, box["y"] + box["height"] * 0.42)
    page.wait_for_function(
        """(leftCourt) => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          return state && state.playerCourt &&
            state.playerCourt.targetX > leftCourt.targetX + 16 &&
            state.playerCourt.x > leftCourt.x + 16 &&
            Math.abs(state.playerCourt.targetY - leftCourt.targetY) < 0.001 &&
            Math.abs(state.playerCourt.y - leftCourt.y) < 0.001;
        }""",
        arg=left_state["playerCourt"],
        timeout=3000,
    )
    moved = page.evaluate("window.BadmintonDemo.getState()")
    assert moved["playerCourt"]["targetX"] > left_state["playerCourt"]["targetX"]
    assert moved["playerCourt"]["x"] > left_state["playerCourt"]["x"]
    assert abs(moved["playerCourt"]["targetY"] - left_state["playerCourt"]["targetY"]) < 0.001
    assert abs(moved["playerCourt"]["y"] - left_state["playerCourt"]["y"]) < 0.001


@pytest.mark.e2e
def test_badminton_yui_swings_back_when_player_shuttle_reaches_her_side(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready'")
    box = page.locator("#game").bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] * 0.45, box["y"] + box["height"] * 0.44)
    page.mouse.down()
    page.wait_for_timeout(980)
    page.mouse.up()

    page.wait_for_function(
        """() => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          return state && (
            (state.pendingSwing && state.pendingSwing.shooter === 'neko') ||
            (state.currentShuttle && state.currentShuttle.shooter === 'neko')
          );
        }""",
        timeout=5000,
    )
    page.wait_for_function(
        """() => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          if (!state || state.state !== 'in_flight' || !state.currentShuttle || state.currentShuttle.shooter !== 'neko') return false;
          window.__bdYuiReturnSample = {
            id: state.currentShuttle.id,
            shooter: state.currentShuttle.shooter,
            spinRate: state.currentShuttle.spinRate,
            spinAngle: state.currentShuttle.spinAngle
          };
          return Math.abs(state.currentShuttle.spinRate) >= 8;
        }""",
        timeout=5000,
    )
    returned = page.evaluate("window.__bdYuiReturnSample")
    assert returned["shooter"] == "neko"
    assert abs(returned["spinRate"]) >= 8
    page.wait_for_function(
        """(sample) => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          if (!state || !state.currentShuttle || state.currentShuttle.id !== sample.id) return false;
          const angleDelta = Math.abs(state.currentShuttle.spinAngle - sample.spinAngle);
          if (state.currentShuttle.hitNet) {
            window.__bdYuiSpinSample = {
              id: state.currentShuttle.id,
              spinAngle: state.currentShuttle.spinAngle,
              angleDelta,
              hitNet: true
            };
            return true;
          }
          if (angleDelta < 0.5) return false;
          window.__bdYuiSpinSample = {
            id: state.currentShuttle.id,
            spinAngle: state.currentShuttle.spinAngle,
            angleDelta,
            hitNet: false
          };
          return true;
        }""",
        arg=returned,
        timeout=1000,
    )
    spinning = page.evaluate("window.__bdYuiSpinSample")
    assert spinning["id"] == returned["id"]
    if not spinning["hitNet"]:
        assert spinning["angleDelta"] >= 0.5


@pytest.mark.e2e
def test_badminton_duel_yui_valid_landing_scores_for_yui(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready'")
    page.evaluate("window.BadmintonDemo._debugFinishShot(true, 'line_in', { shooter: 'neko' })")
    page.wait_for_function(
        """() => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          return state && state.duel && state.duel.neko_score === 1 &&
            state.duel.player_misses === 1;
        }""",
        timeout=4000,
    )
    scored = page.evaluate("window.BadmintonDemo.getState()")
    assert scored["duel"]["neko_score"] == 1
    assert scored["duel"]["player_misses"] == 1
    assert scored["duel"]["neko_misses"] == 0
    assert scored["attemptsResults"][-1]["point_winner"] == "neko"


@pytest.mark.e2e
def test_badminton_duel_yui_backline_out_scores_for_player(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready'")
    page.evaluate(
        """() => {
          window.BadmintonDemo._debugSetAwaitingPlayerReturnBall({
            id: 901,
            x: 25,
            y: 430,
            prevX: 30,
            prevY: 430,
            courtY: 25,
            prevCourtY: 30,
            z: 20,
            prevZ: 20,
            vx: -24,
            vy: 0,
            vCourtY: -24,
            vz: 0,
            radius: 18,
            shooter: 'neko',
            direction: -1,
            crossedNet: true,
            resolved: false,
            awaitingReturnBy: 'player',
            returnDeadlineAt: performance.now() + 2400,
            groundedReturnAt: 0,
            angle: 54,
            power: 56,
            trail: []
          });
        }"""
    )

    page.wait_for_function(
        """() => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          return state && state.duel && state.duel.player_score === 1;
        }""",
        timeout=4000,
    )
    scored = page.evaluate("window.BadmintonDemo.getState()")
    assert scored["duel"]["player_score"] == 1
    assert scored["duel"]["neko_score"] == 0
    assert scored["duel"]["neko_misses"] == 1
    assert scored["duel"]["player_misses"] == 0
    assert scored["attemptsResults"][-1]["shooter"] == "neko"
    assert scored["attemptsResults"][-1]["shot_type"] == "out"
    assert scored["attemptsResults"][-1]["point_winner"] == "player"


@pytest.mark.e2e
def test_badminton_yui_awaiting_return_still_hits_midcourt_net(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready'")
    page.evaluate(
        """() => {
          window.BadmintonDemo._debugSetAwaitingPlayerReturnBall({
            id: 902,
            x: 466,
            y: 330,
            prevX: 470,
            prevY: 330,
            courtY: 466,
            prevCourtY: 470,
            z: 120,
            prevZ: 120,
            vx: -360,
            vy: 0,
            vCourtY: -360,
            vz: 0,
            radius: 18,
            shooter: 'neko',
            direction: -1,
            crossedNet: false,
            resolved: false,
            awaitingReturnBy: 'player',
            returnDeadlineAt: performance.now() + 2400,
            groundedReturnAt: 0,
            angle: 43,
            power: 52,
            trail: []
          });
        }"""
    )

    page.wait_for_function(
        """() => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          if (!state || !state.currentShuttle || !state.currentShuttle.hitNet) return false;
          window.__bdYuiAwaitingNetSample = {
            hitNet: state.currentShuttle.hitNet,
            crossedNet: state.currentShuttle.crossedNet,
            receivingReturn: state.receivingReturn,
            incomingReturnInReach: state.incomingReturnInReach,
            canControlShot: state.canControlShot,
            attemptsResultsLength: state.attemptsResults.length,
            netEffect: window.__badmintonNetEffectDebug || null,
            vx: state.currentShuttle.vx,
            vy: state.currentShuttle.vy
          };
          return true;
        }""",
        timeout=2000,
    )
    netted = page.evaluate("window.__bdYuiAwaitingNetSample")
    assert netted["hitNet"] is True
    assert netted["crossedNet"] is True
    assert netted["receivingReturn"] is True
    assert netted["incomingReturnInReach"] is False
    assert netted["canControlShot"] is False
    assert netted["attemptsResultsLength"] == 0
    assert netted["netEffect"] is not None
    assert netted["netEffect"]["count"] >= 1
    assert netted["netEffect"]["strength"] > 0
    assert abs(netted["vx"]) < 360
    assert netted["vy"] > 0

    page.wait_for_timeout(180)
    after_net_contact = page.evaluate("window.BadmintonDemo.getState()")
    assert after_net_contact["attemptsResults"] == []
    assert after_net_contact["currentShuttle"] is not None
    assert after_net_contact["currentShuttle"]["hitNet"] is True

    page.wait_for_function(
        """() => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          return state && state.attemptsResults.length > 0;
        }""",
        timeout=3500,
    )
    resolved = page.evaluate("window.BadmintonDemo.getState()")
    assert resolved["attemptsResults"][-1]["shooter"] == "neko"
    assert resolved["attemptsResults"][-1]["shot_type"] == "net"
    assert resolved["attemptsResults"][-1]["point_winner"] == "player"
    assert resolved["duel"]["player_score"] == 1
    assert resolved["duel"]["neko_score"] == 0
    assert resolved["duel"]["neko_misses"] == 1
    assert resolved["duel"]["player_misses"] == 0

    page.evaluate("window.BadmintonDemo.resetGame()")
    page.wait_for_function(
        """() => {
          return window.__badmintonNetEffectDebug
            && window.__badmintonNetEffectDebug.count === 0;
        }"""
    )


@pytest.mark.e2e
def test_badminton_net_effect_debug_global_stays_debug_only(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel", debug=False, wait_loading=False)

    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready'")
    page.evaluate(
        """() => {
          window.BadmintonDemo._debugSetAwaitingPlayerReturnBall({
            id: 904,
            x: 466,
            y: 330,
            prevX: 470,
            prevY: 330,
            courtY: 466,
            prevCourtY: 470,
            z: 120,
            prevZ: 120,
            vx: -360,
            vy: 0,
            vCourtY: -360,
            vz: 0,
            radius: 18,
            shooter: 'neko',
            direction: -1,
            crossedNet: false,
            resolved: false,
            awaitingReturnBy: 'player',
            returnDeadlineAt: performance.now() + 2400,
            groundedReturnAt: 0,
            angle: 43,
            power: 52,
            trail: []
          });
        }"""
    )

    page.wait_for_function(
        """() => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          return state && state.currentShuttle && state.currentShuttle.hitNet;
        }""",
        timeout=2000,
    )
    assert page.evaluate("typeof window.__badmintonNetEffectDebug") == "undefined"


@pytest.mark.e2e
def test_badminton_yui_return_above_visible_net_does_not_hit_midcourt_net(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready'")
    page.evaluate(
        """() => {
          window.BadmintonDemo._debugSetAwaitingPlayerReturnBall({
            id: 903,
            x: 466,
            y: 280,
            prevX: 470,
            prevY: 280,
            courtY: 466,
            prevCourtY: 470,
            z: 170,
            prevZ: 170,
            vx: -360,
            vy: 0,
            vCourtY: -360,
            radius: 18,
            shooter: 'neko',
            direction: -1,
            crossedNet: false,
            resolved: false,
            awaitingReturnBy: 'player',
            returnDeadlineAt: performance.now() + 2400,
            groundedReturnAt: 0,
            angle: 43,
            power: 52,
            trail: []
          });
        }"""
    )

    page.wait_for_function(
        """() => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          return state && state.currentShuttle && state.currentShuttle.crossedNet;
        }""",
        timeout=2000,
    )
    above_net = page.evaluate("window.BadmintonDemo.getState()")
    assert above_net["currentShuttle"]["hitNet"] is False
    assert above_net["currentShuttle"]["screenY"] < 316


@pytest.mark.e2e
def test_badminton_player_return_requires_shuttle_to_enter_character_reach(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready'")
    page.evaluate(
        """() => {
          const state = window.BadmintonDemo.getState();
          const contact = state.playerRacketContact || { x: state.playerCourt.x + 42, y: state.playerCourt.y - 76 };
          window.BadmintonDemo._debugSetAwaitingPlayerReturnBall({
            x: contact.x + 180,
            y: contact.y - 180,
            prevX: contact.x + 184,
            prevY: contact.y - 180,
            vx: 0,
            vy: 0,
            returnDeadlineAt: performance.now() + 5000
          });
        }"""
    )

    far = page.evaluate("window.BadmintonDemo.getState()")
    assert far["receivingReturn"] is True
    assert far["incomingReturnInReach"] is False
    assert far["canControlShot"] is False

    page.evaluate("window.BadmintonDemo.shoot()")
    blocked = page.evaluate("window.BadmintonDemo.getState()")
    assert blocked["state"] == "ready"
    assert blocked["pendingSwing"] is None
    assert blocked["currentShuttle"]["awaitingReturnBy"] == "player"

    page.evaluate(
        """() => {
          const state = window.BadmintonDemo.getState();
          const contact = state.playerRacketContact || { x: state.playerCourt.x + 42, y: state.playerCourt.y - 76 };
          window.BadmintonDemo._debugSetAwaitingPlayerReturnBall({
            x: contact.x,
            y: contact.y,
            prevX: contact.x + 4,
            prevY: contact.y,
            vx: -360,
            vy: -80,
            returnDeadlineAt: performance.now() + 5000
          });
        }"""
    )
    near = page.evaluate("window.BadmintonDemo.getState()")
    assert near["receivingReturn"] is True
    assert near["incomingReturnInReach"] is True
    assert near["canControlShot"] is True

    viewport = page.viewport_size or {"width": 1280, "height": 720}
    page.mouse.move(viewport["width"] * 0.5, viewport["height"] * 0.9)
    page.evaluate("window.BadmintonDemo.shoot()")
    swinging = page.evaluate("window.BadmintonDemo.getState()")
    assert swinging["state"] in {"swinging", "in_flight"}
    if swinging["state"] == "swinging":
        assert swinging["pendingSwing"]["shooter"] == "player"
    page.wait_for_function(
        """() => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          if (!state || state.state !== 'in_flight' || !state.currentShuttle || state.currentShuttle.shooter !== 'player') return false;
          window.__bdPlayerReturnSpinSample = {
            id: state.currentShuttle.id,
            spinRate: state.currentShuttle.spinRate,
            angle: state.currentShuttle.angle
          };
          return true;
        }""",
        timeout=2000,
    )
    player_return = page.evaluate("window.__bdPlayerReturnSpinSample")
    assert abs(player_return["spinRate"]) <= 10
    assert player_return["angle"] <= 48


@pytest.mark.e2e
def test_badminton_player_return_hit_cue_only_draws_inside_reach(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    count_cue_pixels = """
      () => {
        const canvas = document.getElementById('aiming-canvas');
        if (!canvas) return 0;
        const ctx = canvas.getContext('2d');
        const data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
        let lit = 0;
        for (let i = 3; i < data.length; i += 4) {
          if (data[i] > 20) lit++;
        }
        return lit;
      }
    """

    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready'")
    page.evaluate(
        """() => {
          const state = window.BadmintonDemo.getState();
          const contact = state.playerRacketContact || { x: state.playerCourt.x + 42, y: state.playerCourt.y - 76 };
          window.BadmintonDemo._debugSetAwaitingPlayerReturnBall({
            x: contact.x + 180,
            y: contact.y - 180,
            prevX: contact.x + 184,
            prevY: contact.y - 180,
            vx: 0,
            vy: 0,
            returnDeadlineAt: performance.now() + 5000
          });
        }"""
    )
    page.wait_for_timeout(80)
    assert page.evaluate(count_cue_pixels) == 0

    page.evaluate(
        """() => {
          const state = window.BadmintonDemo.getState();
          const contact = state.playerRacketContact || { x: state.playerCourt.x + 42, y: state.playerCourt.y - 76 };
          window.BadmintonDemo._debugSetAwaitingPlayerReturnBall({
            x: contact.x,
            y: contact.y,
            prevX: contact.x + 4,
            prevY: contact.y,
            vx: -360,
            vy: -80,
            returnDeadlineAt: performance.now() + 5000
          });
        }"""
    )
    page.wait_for_function(f"({count_cue_pixels})() > 12", timeout=2000)
    assert page.evaluate("window.BadmintonDemo.getState().incomingReturnInReach") is True


@pytest.mark.e2e
def test_badminton_vrm_overlay_does_not_block_player_swing(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready' && window.BadmintonDemo.getState().canControlShot")
    page.wait_for_selector("#player-sensei-vrm-container:not([hidden])")
    box = page.locator("#player-sensei-vrm-container").bounding_box()
    assert box is not None

    page.mouse.move(box["x"] + 10, box["y"] + 10)
    page.mouse.down()
    charging = page.evaluate("window.BadmintonDemo.getState()")
    assert charging["charging"] is True

    page.mouse.up()
    swinging = page.evaluate("window.BadmintonDemo.getState()")
    assert swinging["state"] in {"swinging", "in_flight"}
    if swinging["state"] == "swinging":
        assert swinging["pendingSwing"]["shooter"] == "player"
    else:
        assert swinging["currentShuttle"]["shooter"] == "player"


@pytest.mark.e2e
def test_badminton_allows_player_movement_and_jump_but_blocks_shot_during_yui_turn(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready'")
    page.evaluate("window.BadmintonDemo._debugFinishShot(false, 'out')")
    page.wait_for_function(
        """() => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          return state && state.state === 'neko_thinking' && state.duel.active_shooter === 'neko';
        }"""
    )
    before_move = page.evaluate("window.BadmintonDemo.getState().playerCourt")

    viewport = page.viewport_size or {"width": 1280, "height": 720}
    page.mouse.move(viewport["width"] - 8, 12)
    page.evaluate(
        """() => {
          window.dispatchEvent(new KeyboardEvent('keydown', {
            key: ' ',
            code: 'Space',
            bubbles: true,
            cancelable: true
          }));
        }"""
    )
    page.wait_for_function(
        """() => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          if (!state || !state.playerJump || !state.playerJump.active || state.playerJump.offset <= 0) return false;
          window.__bdYuiTurnJumpSnapshot = state.playerJump;
          return true;
        }""",
        timeout=2000,
    )
    page.mouse.down()
    page.mouse.up()
    page.evaluate("window.BadmintonDemo.shoot()")
    page.wait_for_function(
        """(beforeX) => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          return state && state.playerCourt &&
            state.playerCourt.targetX > beforeX + 16 &&
            state.playerCourt.x > beforeX + 16;
        }""",
        arg=before_move["targetX"],
    )

    state = page.evaluate("window.BadmintonDemo.getState()")
    jump = page.evaluate("window.__bdYuiTurnJumpSnapshot")
    assert jump["active"] is True
    assert jump["offset"] > 0
    assert state["playerCourt"]["targetX"] > before_move["targetX"]
    assert state["playerCourt"]["x"] > before_move["x"]
    assert state["charging"] is False
    assert not (state["pendingSwing"] and state["pendingSwing"]["shooter"] == "player")
    assert not (state["currentShuttle"] and state["currentShuttle"]["shooter"] == "player")


@pytest.mark.e2e
def test_badminton_duel_valid_landing_scores_for_shooter(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready'")
    page.evaluate("window.BadmintonDemo._debugFinishShot(true, 'line_in')")
    page.wait_for_function(
        """() => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          return state && state.state === 'ready' && state.duel.active_shooter === 'player' &&
            state.duel.player_score === 1;
        }"""
    )
    returned = page.evaluate("window.BadmintonDemo.getState()")
    assert returned["score"] == 1
    assert returned["lastShotScore"] == 1
    assert returned["duel"]["player_score"] == 1
    assert returned["duel"]["neko_score"] == 0
    assert returned["duel"]["neko_misses"] == 1
    assert returned["duel"]["rally_hits"] == 0

    page.evaluate("window.BadmintonDemo.resetGame()")
    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready'")
    page.evaluate("window.BadmintonDemo._debugFinishShot(false, 'out')")
    page.wait_for_function(
        """() => {
          const state = window.BadmintonDemo && window.BadmintonDemo.getState();
          return state && state.state === 'neko_thinking' && state.duel.active_shooter === 'neko';
        }"""
    )
    missed = page.evaluate("window.BadmintonDemo.getState()")
    assert missed["score"] == 0
    assert missed["duel"]["player_score"] == 0
    assert missed["duel"]["neko_score"] == 1
    assert missed["duel"]["player_misses"] == 1
    assert missed["duel"]["rally_hits"] == 0


@pytest.mark.e2e
def test_badminton_route_and_public_api(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    expect(page).to_have_url(re.compile(r"/badminton_demo"))
    expect(page).to_have_title(re.compile("羽毛球挑战"))
    expect(page.locator("#badminton-loading")).to_have_count(1)
    page.wait_for_function(
        """() => {
          const loading = document.getElementById('badminton-loading');
          return window.__badmintonInitialLoadingHidden === true ||
            (loading && loading.hidden === true);
        }""",
        timeout=10000,
    )
    expect(page.locator("#badminton-loading")).not_to_be_visible()
    assert page.evaluate("typeof window.BadmintonDemo.getState") == "function"
    assert page.evaluate("typeof window.BadmintonDemo.shoot") == "function"
    page.evaluate(
        """() => {
          const loading = document.getElementById('badminton-loading');
          loading.hidden = false;
          loading.classList.remove('hide');
          window.__badmintonInitialLoadingHidden = false;
        }"""
    )
    blocked_before = page.evaluate("window.BadmintonDemo.getState()")
    viewport = page.viewport_size or {"width": 1280, "height": 720}
    page.mouse.move(viewport["width"] - 8, 18)
    page.mouse.down()
    page.mouse.up()
    page.evaluate("window.BadmintonDemo.shoot()")
    blocked_after = page.evaluate("window.BadmintonDemo.getState()")
    assert blocked_after["canControlShot"] is False
    assert blocked_after["playerCourt"]["targetX"] == blocked_before["playerCourt"]["targetX"]
    assert blocked_after["pendingSwing"] is None
    assert blocked_after["currentShuttle"] is None
    page.evaluate(
        """() => {
          const loading = document.getElementById('badminton-loading');
          loading.classList.add('hide');
          loading.hidden = true;
          window.__badmintonInitialLoadingHidden = true;
        }"""
    )
    state = page.evaluate("window.BadmintonDemo.getState()")
    assert state["mode"] == "duel"
