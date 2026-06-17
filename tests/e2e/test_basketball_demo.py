import re
import time

import pytest
from playwright.sync_api import Page, expect

from main_routers import system_router


def _install_basketball_test_hooks(page: Page) -> None:
    page.add_init_script(
        """
        (() => {
          localStorage.clear();
          window.__basketballE2EEvents = [];
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


def _goto_basketball(page: Page, running_server: str, mode: str) -> None:
    _install_basketball_test_hooks(page)
    lanlan_name = "e2e-yui"
    session_id = f"e2e-basketball-{mode}"
    state = system_router._mini_game_invite_get_state(lanlan_name)
    state["delivered_at"] = time.time() - 1
    state["responded_at"] = time.time()
    state["pending_session_id"] = session_id
    state["last_game_type"] = "basketball"
    page.goto(
        f"{running_server}/basketball_demo"
        f"?mode={mode}&lanlan_name={lanlan_name}&session_id={session_id}&debug=1"
    )
    expect(page.locator("#game")).to_be_attached(timeout=15000)
    page.wait_for_function("window.BasketballDemo && window.BasketballDemo.getState")


def _force_shot_result(page: Page, scored: bool = True) -> None:
    before_results = page.evaluate("window.BasketballDemo.getState().attemptsResults.length")
    page.evaluate(
        """(scored) => {
          const api = window.BasketballDemo;
          api._debugFinishShot(scored, scored ? 'swish' : 'rim_out');
        }""",
        scored,
    )
    page.wait_for_function(
        """(beforeResults) => {
          const state = window.BasketballDemo && window.BasketballDemo.getState();
          return state && state.attemptsResults.length > beforeResults;
        }""",
        arg=before_results,
    )
    page.wait_for_function(
        """() => {
          const state = window.BasketballDemo && window.BasketballDemo.getState();
          return state && (state.state === 'ready' || state.state === 'game_over');
        }"""
    )


@pytest.mark.e2e
def test_basketball_spectator_open_to_shot_completion(mock_page: Page, running_server: str):
    page = mock_page
    _goto_basketball(page, running_server, "spectator")

    canvas = page.locator("#game")
    box = canvas.bounding_box()
    assert box is not None
    page.mouse.move(box["x"] + box["width"] * 0.35, box["y"] + box["height"] * 0.42)
    page.mouse.down()
    page.wait_for_timeout(120)
    page.mouse.up()

    page.wait_for_function("window.BasketballDemo.getState().state === 'ready'")
    state = page.evaluate("window.BasketballDemo.getState()")
    assert state["mode"] == "spectator"
    assert "streak" in state


@pytest.mark.e2e
def test_basketball_shooter_three_attempts_and_restart(mock_page: Page, running_server: str):
    page = mock_page
    _goto_basketball(page, running_server, "shooter")

    for _ in range(3):
        _force_shot_result(page, False)

    expect(page.locator("#result-panel")).to_have_class(re.compile(r"\bshow\b"))
    state = page.evaluate("window.BasketballDemo.getState()")
    assert state["state"] == "game_over"
    assert state["attemptsRemaining"] == 0
    assert state["score"] == 0
    page.locator("#restart-button").click()
    page.wait_for_function("window.BasketballDemo.getState().score === 0")


@pytest.mark.e2e
def test_basketball_mode_switcher_changes_url(mock_page: Page, running_server: str):
    page = mock_page
    _goto_basketball(page, running_server, "shooter")

    page.locator('[data-mode="spectator"]').click()
    expect(page).to_have_url(re.compile(r"mode=spectator"))
    page.locator('[data-mode="duel"]').click()
    expect(page).to_have_url(re.compile(r"mode=duel"))


@pytest.mark.e2e
def test_basketball_local_leaderboard_records_game(mock_page: Page, running_server: str):
    page = mock_page
    _goto_basketball(page, running_server, "shooter")

    _force_shot_result(page, True)
    for _ in range(3):
        _force_shot_result(page, False)

    page.locator("#leaderboard-button").click()
    expect(page.locator("#leaderboard-panel")).to_have_class(re.compile(r"\bshow\b"))
    page.locator('.lb-tab[data-tab="local"]').click()
    expect(page.locator("#leaderboard-body tr")).to_have_count(1)


@pytest.mark.e2e
def test_basketball_public_api_and_debug_panel(mock_page: Page, running_server: str):
    page = mock_page
    _goto_basketball(page, running_server, "duel")

    state = page.evaluate("window.BasketballDemo.getState()")
    assert {"mode", "state", "score", "streak", "distance"} <= set(state)
    assert page.evaluate("window.BasketballDemo.setDuelDifficulty('max'); window.BasketballDemo.getDifficulty()") == "max"
    assert page.evaluate("window.BasketballDemo.setMood('happy'); window.BasketballDemo.getMood()") == "happy"
    expect(page.locator("#bb-debug-panel")).to_be_visible()
