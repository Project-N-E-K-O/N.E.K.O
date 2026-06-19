import re
import time

import pytest
from playwright.sync_api import Page, expect

from main_routers import system_router


def _install_badminton_test_hooks(page: Page) -> None:
    page.add_init_script(
        """
        (() => {
          localStorage.clear();
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


def _goto_badminton(page: Page, running_server: str, mode: str) -> None:
    _install_badminton_test_hooks(page)
    lanlan_name = "e2e-yui"
    session_id = f"e2e-badminton-{mode}"
    state = system_router._mini_game_invite_get_state(lanlan_name)
    state["delivered_at"] = time.time() - 1
    state["responded_at"] = time.time()
    state["pending_session_id"] = session_id
    state["last_game_type"] = "badminton"
    page.goto(
        f"{running_server}/badminton_demo"
        f"?mode={mode}&lanlan_name={lanlan_name}&session_id={session_id}&debug=1"
    )
    expect(page.locator("#game")).to_be_attached(timeout=15000)
    page.wait_for_function("window.BadmintonDemo && window.BadmintonDemo.getState")


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
def test_badminton_duel_three_player_misses_and_restart(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "shooter")

    for _ in range(3):
        _force_shot_result(page, False)

    expect(page.locator("#result-panel")).to_have_class(re.compile(r"\bshow\b"))
    state = page.evaluate("window.BadmintonDemo.getState()")
    assert state["state"] == "game_over"
    assert state["mode"] == "duel"
    assert state["duel"]["player_misses"] == 3
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
    for _ in range(3):
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
def test_badminton_player_serve_hint_draws_shuttle_above_player(mock_page: Page, running_server: str):
    page = mock_page
    _goto_badminton(page, running_server, "duel")

    page.wait_for_function("window.BadmintonDemo.getState().state === 'ready' && window.BadmintonDemo.getState().heldShuttleVisible")
    bright_pixels = page.evaluate(
        """() => {
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
          return bright;
        }"""
    )
    assert bright_pixels > 12


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
            incomingReturnInReach: state.incomingReturnInReach,
            attemptsResultsLength: state.attemptsResults.length,
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
    assert netted["incomingReturnInReach"] is False
    assert netted["attemptsResultsLength"] == 0
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
    assert resolved["attemptsResults"][-1]["shot_type"] in ("net_touch", "net", "out")


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
def test_badminton_blocks_player_input_during_yui_turn(mock_page: Page, running_server: str):
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

    page.evaluate(
        """() => {
          const canvas = document.getElementById('game');
          canvas.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, clientY: 12 }));
          canvas.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
          window.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
          window.BadmintonDemo.shoot();
        }"""
    )

    state = page.evaluate("window.BadmintonDemo.getState()")
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
    assert page.evaluate("typeof window.BadmintonDemo.getState") == "function"
    assert page.evaluate("typeof window.BadmintonDemo.shoot") == "function"
    state = page.evaluate("window.BadmintonDemo.getState()")
    assert state["mode"] == "duel"
