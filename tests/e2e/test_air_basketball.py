import re

import pytest
from playwright.sync_api import Page, expect


WATCHED_SCRIPT_PATHS = ("/static/air-basketball/", "/static/game/sdk/", "/air_basketball")


def _stub_unavailable_air_basketball_avatar(page: Page):
    page.route(
        "**/api/game/air-basketball/character*",
        lambda route: route.fulfill(
            json={
                "lanlan_name": "SDK Test Neko",
                "model_type": "unavailable",
                "live3d_sub_type": "",
                "live2d_path": "",
                "vrm_path": "",
            }
        ),
    )


@pytest.mark.e2e
def test_air_basketball_neko_ball_budget(page: Page, running_server: str):
    page.goto(
        f"{running_server}/air_basketball",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    page.wait_for_function("window.AirBasketballMVP && window.AirBasketballMVP.getState")
    page.evaluate("window.AirBasketballMVP.start()")

    # Releasing a shot ends the action immediately, so a second ball may be
    # launched before the first lands. The shared budget blocks a third.
    assert page.evaluate("window.AirBasketballMVP.shootNeko()") is True
    assert page.evaluate("window.AirBasketballMVP.shootNeko()") is True
    assert page.evaluate("window.AirBasketballMVP.shootNeko()") is False
    page.wait_for_timeout(50)
    state = page.evaluate("window.AirBasketballMVP.getState()")
    assert state["nekoNativeShotBalls"] == 2
    assert state["nekoActiveBalls"] == 2
    assert state["actionBalance"]["SHOT_COST"] == 18
    assert state["actionBalance"]["AVATAR_HIT_COST"] == 24
    assert state["actionBalance"]["PRANK_COST"] == 36
    assert state["actionBalance"]["MOUSE_COST"] == 60
    assert state["actionBalance"]["MOUSE_MAX_PER_ROUND"] == 2
    assert 64 <= state["neko"]["focus"] < 66
    # Regression: native shots used to spawn on the ready ball and knock that
    # ball into flight too, turning one visible release into two basketballs.
    assert state["nekoReadyBallFlying"] is False

    peak_active = state["nekoActiveBalls"]
    peak_state = state
    for _ in range(80):
        page.wait_for_timeout(50)
        sample = page.evaluate("window.AirBasketballMVP.getState()")
        if sample["nekoActiveBalls"] > peak_active:
            peak_active = sample["nekoActiveBalls"]
            peak_state = sample
    assert peak_active <= 2, peak_state

    # Neko's prank now targets the player's idle ready ball instead of falling
    # back to the hoop. This makes the same initial-ball collision available to
    # both sides without allowing a third Neko-owned ball.
    page.evaluate("window.AirBasketballMVP.start()")
    assert page.evaluate("window.AirBasketballMVP.prankNeko()") is True
    page.wait_for_function(
        "window.AirBasketballMVP.getState().playerBallFlying === true",
        timeout=3500,
    )
    prank_state = page.evaluate("window.AirBasketballMVP.getState()")
    assert prank_state["playerReadyBallOwner"] == "neko"
    assert prank_state["nekoActiveBalls"] <= 2

    # The player pays the same shot cost as Neko; only the control method is
    # different. Failed/repeated shots do not consume focus twice.
    page.evaluate("window.AirBasketballMVP.start()")
    assert page.evaluate("window.AirBasketballMVP.shootPlayer(0, -700)") is True
    player_focus = page.evaluate("window.AirBasketballMVP.getState().player.focus")
    assert 81.5 <= player_focus <= 82.5
    page.wait_for_timeout(400)
    delayed_focus = page.evaluate("window.AirBasketballMVP.getState().player.focus")
    assert delayed_focus <= player_focus + 0.5
    page.wait_for_timeout(500)
    assert page.evaluate("window.AirBasketballMVP.getState().player.focus") > delayed_focus + 2
    assert page.evaluate("window.AirBasketballMVP.shootPlayer(0, -700)") is False
    assert page.evaluate("window.AirBasketballMVP.getState().player.focus") >= player_focus


@pytest.mark.e2e
def test_air_basketball_neko_attention_retimes_special_slots(
    page: Page,
    running_server: str,
):
    page.goto(
        f"{running_server}/air_basketball?test_mode=1",
        wait_until="domcontentloaded",
    )
    page.wait_for_function(
        "() => Boolean(window.AirBasketballMVP?.test?.planNekoIntent)"
    )

    def plan(*, roll, context, pending=None, can_shoot=True):
        return page.evaluate(
            """
            args => window.AirBasketballMVP.test.planNekoIntent({
              roll:args.roll,
              canMouse:true,
              canPrank:true,
              canShoot:args.canShoot,
              context:args.context,
              pending:args.pending || { mouse:false, prank:false }
            })
            """,
            {
                "roll": roll,
                "context": context,
                "pending": pending,
                "canShoot": can_shoot,
            },
        )

    quiet = {"mouse": False, "prank": False}
    mouse_slot = plan(roll=0.05, context=quiet)
    assert mouse_slot["action"] == "hoop"
    assert mouse_slot["pendingMouse"] is True
    assert mouse_slot["pendingPrank"] is False

    prank_slot = plan(roll=0.20, context=quiet)
    assert prank_slot["action"] == "hoop"
    assert prank_slot["pendingPrank"] is True

    retimed_mouse = plan(
        roll=0.80,
        context={"mouse": True, "prank": False},
        pending={"mouse": True, "prank": False},
    )
    assert retimed_mouse["action"] == "mouse"
    assert retimed_mouse["source"] == "pending"
    assert retimed_mouse["pendingMouse"] is False

    retimed_prank = plan(
        roll=0.80,
        context={"mouse": False, "prank": True},
        pending={"mouse": False, "prank": True},
    )
    assert retimed_prank["action"] == "player"
    assert retimed_prank["source"] == "pending"
    assert retimed_prank["pendingPrank"] is False

    # Holding the ready ball is real game input, not a test-only state patch.
    page.evaluate("window.AirBasketballMVP.start()")
    player_box = page.locator("#player-court").bounding_box()
    assert player_box
    page.mouse.move(
        player_box["x"] + player_box["width"] * 0.5,
        player_box["y"] + player_box["height"] * 0.82,
    )
    page.mouse.down()
    page.wait_for_timeout(1050)
    aim_context = page.evaluate(
        "window.AirBasketballMVP.test.nekoAttentionContext()"
    )
    page.mouse.up()
    assert aim_context["mouse"] is True


@pytest.mark.e2e
def test_air_basketball_post_shot_interference_and_avatar_input_zones(
    page: Page,
    running_server: str,
):
    page.goto(
        f"{running_server}/air_basketball?test_mode=1",
        wait_until="domcontentloaded",
    )
    page.wait_for_function("window.AirBasketballMVP && window.AirBasketballMVP.getState")
    page.evaluate("window.AirBasketballMVP.start()")

    assert page.evaluate("window.AirBasketballMVP.shootPlayer(0, -620)") is True
    page.wait_for_function("window.AirBasketballMVP.getState().playerBallMotion !== null")
    player_box = page.locator("#player-court").bounding_box()
    motion = page.evaluate("window.AirBasketballMVP.getState().playerBallMotion")
    assert player_box and motion

    page.mouse.move(player_box["x"] + motion["x"], player_box["y"] + motion["y"])
    page.mouse.down()
    page.mouse.move(
        player_box["x"] + motion["x"] + 24,
        player_box["y"] + motion["y"] - 12,
        steps=3,
    )
    page.mouse.up()
    page.wait_for_function("window.AirBasketballMVP.getState().interferenceCount > 0")

    page.wait_for_selector("#air-neko-avatar.is-ready", timeout=20000)
    transparent_target = page.evaluate(
        """
        () => {
          const avatar = document.getElementById('air-neko-avatar').getBoundingClientRect();
          const court = document.getElementById('neko-court').getBoundingClientRect();
          const x = Math.min(court.right - 4, avatar.left + avatar.width * .08);
          const y = Math.min(court.bottom - 4, avatar.top + avatar.height * .55);
          const target = document.elementFromPoint(x, y);
          return { id:target?.id || '', className:String(target?.className || '') };
        }
        """
    )
    assert transparent_target["id"] == "neko-court"

    zone_input = page.evaluate(
        """
        () => {
          const zone = document.querySelector('.avatar-hit-zone-torso');
          const rect = zone.getBoundingClientRect();
          const target = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
          return {
            targetClass:String(target?.className || ''),
            pointerEvents:getComputedStyle(zone).pointerEvents
          };
        }
        """
    )
    assert "avatar-hit-zone" in zone_input["targetClass"]
    assert zone_input["pointerEvents"] == "auto"


@pytest.mark.e2e
def test_air_basketball_dual_arcade_match(page: Page, running_server: str):
    console_errors = []
    runtime_errors = []
    page.on(
        "console",
        lambda message: console_errors.append(
            f"{message.text} @ {message.location.get('url', 'unknown')}"
        )
        if message.type == "error"
        and any(
            path in message.location.get("url", "")
            for path in WATCHED_SCRIPT_PATHS
        )
        else None,
    )
    page.on("pageerror", lambda error: runtime_errors.append(str(error)))
    page.add_init_script("localStorage.setItem('i18nextLng', 'zh-CN')")
    page.route(
        "**/api/game/air-basketball/character*",
        lambda route: route.fulfill(
            json={
                "lanlan_name": "SDK Live2D Test",
                "model_type": "live2d",
                "live3d_sub_type": "",
                "live2d_path": "/static/mao_pro/mao_pro.model3.json",
                "vrm_path": "",
            }
        ),
    )

    page.goto(f"{running_server}/air_basketball")
    page.wait_for_load_state("networkidle")
    page.wait_for_function("window.AirBasketballMVP && window.AirBasketballMVP.getState")
    page.wait_for_selector("#air-neko-avatar.is-ready", timeout=20000)
    expect(page.locator("#air-neko-avatar")).to_have_attribute("data-renderer", "live2d")

    expect(page.locator(".rule-chips span")).to_have_text(
        ["角度力度", "四段篮筐", "连续爆发"]
    )
    module_urls = page.evaluate(
        """performance.getEntriesByType('resource')
        .map(entry => entry.name)
        .filter(url => /air-basketball[/](i18n|physics|avatar|sdk-bootstrap|avatar-host)[.]js/.test(url))"""
    )
    assert len(module_urls) == 5
    assert all("?v=" in url for url in module_urls)
    assert len({url.split("?v=", 1)[1] for url in module_urls}) == 1

    expect(page.locator(".arcade-machine")).to_have_count(2)
    expect(page.locator("#player-court")).to_be_visible()
    expect(page.locator("#neko-court")).to_be_visible()
    expect(page.locator("#air-neko-avatar")).to_be_visible()
    expect(page.locator("#start-overlay")).to_be_visible()
    player_box = page.locator("#player-court").bounding_box()
    neko_box = page.locator("#neko-court").bounding_box()
    assert player_box and player_box["width"] > 200 and player_box["height"] > 300
    assert neko_box and neko_box["width"] > 200 and neko_box["height"] > 300

    page.locator("#start-button").click()
    expect(page.locator("#start-overlay")).not_to_be_visible()
    assert page.evaluate("window.AirBasketballMVP.getState().running") is True

    assert page.evaluate("window.AirBasketballMVP.stealMouse()") is True
    page.wait_for_function("window.AirBasketballMVP.getState().mouseStealActive === true")
    stolen_state = page.evaluate("window.AirBasketballMVP.getState()")
    assert stolen_state["nekoAction"] == "mouse"
    assert 1800 <= stolen_state["mouseStealDurationMs"] <= 3200
    assert page.evaluate("window.AirBasketballMVP.shootNeko()") is False
    expect(page.locator("#mouse-steal-layer")).to_be_visible()
    for x in (100, 520, 110, 530, 120, 540, 130):
        page.mouse.move(x, 110)
    page.wait_for_function("window.AirBasketballMVP.getState().mouseStealActive === false")
    escaped_state = page.evaluate("window.AirBasketballMVP.getState()")
    assert escaped_state["mouseStealCount"] == 1
    assert escaped_state["mouseStealEscapes"] == 1
    assert escaped_state["nekoAction"] == "idle"
    assert 14.5 <= escaped_state["nextMouseSteal"] <= 23

    page.locator("#chat-toggle").click()
    expect(page.locator("#game-chat")).to_be_visible()
    expect(page.locator("#game-chat-frame")).to_have_attribute("src", re.compile(r"^/chat\?"))
    page.locator("#chat-close").click()
    expect(page.locator("#game-chat")).not_to_be_visible()

    page.evaluate("""
      () => {
        window.__airBallVisibility = { activeFrames:0, hiddenFrames:0, minZ:Infinity, watching:true };
        const sample = () => {
          const report = window.__airBallVisibility;
          const state = window.AirBasketballMVP.getState();
          const active = state.playerBallFlying || state.playerBallInTransit
            || state.nekoGuestMotion?.owner === 'player';
          if (active) {
            const ball = document.getElementById('cross-ball');
            const style = getComputedStyle(ball);
            const rect = ball.getBoundingClientRect();
            report.activeFrames += 1;
            report.minZ = Math.min(report.minZ, Number(style.zIndex));
            if (style.visibility !== 'visible' || style.display === 'none'
                || Number(style.opacity) < 1 || rect.width < 1 || rect.height < 1) {
              report.hiddenFrames += 1;
            }
          }
          if (report.watching) requestAnimationFrame(sample);
        };
        requestAnimationFrame(sample);
      }
    """)
    outcome_before_cross = page.evaluate("window.AirBasketballMVP.getState()")
    page.evaluate("window.AirBasketballMVP.shootPlayer(900, -180)")
    page.wait_for_function("document.getElementById('cross-ball').classList.contains('is-crossing')", timeout=3000)
    cross_layer = page.evaluate("""
      () => {
        const ball = document.getElementById('cross-ball');
        const style = getComputedStyle(ball);
        const rect = ball.getBoundingClientRect();
        return {
          parent: ball.parentElement.tagName,
          position: style.position,
          zIndex: Number(style.zIndex),
          opacity: Number(style.opacity),
          visibility: style.visibility,
          animationName: style.animationName,
          width: rect.width,
        };
      }
    """)
    assert cross_layer["parent"] == "BODY"
    assert cross_layer["position"] == "fixed"
    assert cross_layer["zIndex"] >= 60
    assert cross_layer["opacity"] == 1
    assert cross_layer["visibility"] == "visible"
    assert cross_layer["animationName"] == "none"
    assert cross_layer["width"] >= 38
    page.wait_for_function("window.AirBasketballMVP.getState().crossCount > 0", timeout=3000)
    page.wait_for_function("window.AirBasketballMVP.getState().nekoGuestMotion?.owner === 'player'", timeout=3000)
    assert page.evaluate("window.AirBasketballMVP.getState().playerBallInTransit") is True
    cross_motion = page.evaluate("window.AirBasketballMVP.getState().nekoGuestMotion")
    assert cross_motion["owner"] == "player"
    assert 0 <= cross_motion["xRatio"] < 0.35
    assert cross_motion["yRatio"] > 0.55
    assert cross_motion["vx"] > 0
    assert abs(cross_motion["vy"]) < 300
    assert cross_motion["speed"] > 800

    # Live AI may block, steal, or leave the cross-court ball alone. Every one
    # of those is a legal match result, so this flow only requires a complete,
    # visible transfer followed by a clean return to the ready state.
    page.wait_for_function("window.AirBasketballMVP.getState().playerBallInTransit === false", timeout=7000)
    cross_outcome = page.evaluate("window.AirBasketballMVP.getState()")
    assert (
        cross_outcome["nekoGuestMotion"] is None
        or cross_outcome["nekoGuestMotion"]["owner"] != "player"
    )
    assert cross_outcome["crossCount"] > outcome_before_cross["crossCount"]
    assert cross_outcome["nekoBallHitCount"] >= outcome_before_cross["nekoBallHitCount"]
    assert cross_outcome["ballClashCount"] >= outcome_before_cross["ballClashCount"]
    assert cross_outcome["player"]["score"] >= outcome_before_cross["player"]["score"]
    expect(page.locator("#cross-ball")).to_be_visible()
    visibility_report = page.evaluate("""
      () => {
        window.__airBallVisibility.watching = false;
        return window.__airBallVisibility;
      }
    """)
    assert visibility_report["activeFrames"] > 5
    assert visibility_report["hiddenFrames"] == 0
    assert visibility_report["minZ"] >= 60

    # Neko's normal auto-shots must not starve the cross-court prank throw.
    # Start from a deterministic fresh budget instead of waiting for the
    # intentionally probabilistic 20% action-selection branch.
    page.evaluate("window.AirBasketballMVP.start()")
    assert page.evaluate("window.AirBasketballMVP.prankNeko()") is True
    player_attack_state = page.evaluate("window.AirBasketballMVP.getState()")
    assert player_attack_state["nekoAction"] == "player"
    assert player_attack_state["mouseStealActive"] is False
    page.wait_for_function(
        "window.AirBasketballMVP.getState().playerGuestBalls > 0",
        timeout=3000,
    )
    neko_attack = page.evaluate("window.AirBasketballMVP.getState().playerGuestMotion")
    assert neko_attack["owner"] == "neko"
    active_attack_state = page.evaluate("window.AirBasketballMVP.getState()")
    assert active_attack_state["nekoAction"] == "idle"
    page.wait_for_function(
        "window.AirBasketballMVP.getState().playerGuestBalls === 0",
        timeout=8000,
    )
    page.wait_for_function(
        "!window.AirBasketballMVP.getState().playerBallFlying && "
        "!window.AirBasketballMVP.getState().playerBallInTransit",
        timeout=8000,
    )

    start_x = player_box["x"] + player_box["width"] * 0.5
    start_y = player_box["y"] + player_box["height"] * 0.82
    end_x = player_box["x"] + player_box["width"] * 0.5
    end_y = player_box["y"] + player_box["height"] * 0.38
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(end_x, end_y, steps=8)
    page.mouse.up()
    page.wait_for_function("window.AirBasketballMVP.getState().playerBallFlying === true")

    jam_start_x = neko_box["x"] + neko_box["width"] * 0.5
    jam_start_y = neko_box["y"] + neko_box["height"] * 0.82
    page.mouse.move(jam_start_x, jam_start_y)
    page.mouse.down()
    page.mouse.move(jam_start_x + 45, jam_start_y - 28, steps=5)
    page.mouse.up()
    page.wait_for_function("window.AirBasketballMVP.getState().interferenceCount > 0")

    page.wait_for_function("window.AirBasketballMVP.getState().remaining < 60", timeout=3000)
    state = page.evaluate("window.AirBasketballMVP.getState()")
    assert state["player"]["time"] == state["remaining"]
    assert state["neko"]["time"] == state["remaining"]
    assert state["player"]["stage"] == 1
    assert state["neko"]["stage"] == 1
    page.wait_for_function(
        "!window.AirBasketballMVP.getState().playerBallFlying && "
        "!window.AirBasketballMVP.getState().playerBallInTransit",
        timeout=7000,
    )
    expect(page.locator("#cross-ball")).to_be_visible()
    assert console_errors == []
    assert runtime_errors == []


@pytest.mark.e2e
def test_air_basketball_narrow_canvas_input_uses_lane_coordinates(
    page: Page,
    running_server: str,
):
    page.set_viewport_size({"width": 390, "height": 700})
    page.goto(
        f"{running_server}/air_basketball?test_mode=1",
        wait_until="domcontentloaded",
    )
    page.wait_for_function("window.AirBasketballMVP && window.AirBasketballMVP.getState")
    page.evaluate("window.AirBasketballMVP.test.prepareIsolatedCrossTest()")

    box = page.locator("#player-court").bounding_box()
    assert box and box["width"] < 240
    start_x = box["x"] + box["width"] * 0.5
    start_y = box["y"] + box["height"] * 0.82
    page.mouse.move(start_x, start_y)
    page.mouse.down()
    page.mouse.move(start_x, start_y - box["height"] * 0.28, steps=6)
    page.mouse.up()

    page.wait_for_function("window.AirBasketballMVP.getState().playerBallFlying === true")


@pytest.mark.e2e
def test_air_basketball_runtime_start_snapshot_and_wall_clock_contract(
    page: Page,
    running_server: str,
):
    start_payloads = []

    def capture_start(request):
        if request.url.endswith("/api/game/air-basketball/route/start"):
            start_payloads.append(request.post_data_json)

    _stub_unavailable_air_basketball_avatar(page)
    page.on("request", capture_start)
    page.goto(
        f"{running_server}/air_basketball?test_mode=1",
        wait_until="domcontentloaded",
    )
    page.wait_for_function(
        "() => Boolean(window.AirBasketballMVP?.test?.advanceMatchClock)"
    )

    result = page.evaluate(
        """
        () => {
          window.AirBasketballMVP.start();
          const before = window.AirBasketballMVP.getState();
          const physics = window.AirBasketballMVP.test.planPhysicsSteps(.5);
          window.AirBasketballMVP.test.advanceMatchClock(.5);
          window.AirBasketballMVP.test.advanceMatchClock(2);
          const after = window.AirBasketballMVP.getState();
          return {
            before,
            after,
            physics,
            snapshot:window.AirBasketballMVP.test.runtimeSnapshot()
          };
        }
        """
    )
    for _ in range(40):
        if start_payloads:
            break
        page.wait_for_timeout(50)

    assert start_payloads
    start_payload = start_payloads[-1]
    assert start_payload["gameStarted"] is True
    assert start_payload["game_started"] is True
    assert start_payload["gameStartedElapsedMs"] == 0
    assert start_payload["currentState"]["score"] == {"player": 0, "ai": 0}
    assert "neko" not in start_payload["currentState"]["score"]

    assert result["physics"]["steps"] == 8
    assert result["physics"]["stepSeconds"] * result["physics"]["steps"] == pytest.approx(.25)
    assert result["after"]["elapsed"] - result["before"]["elapsed"] == 2
    assert result["before"]["remaining"] - result["after"]["remaining"] == 2
    assert result["snapshot"]["score"] == {"player": 0, "ai": 0}


@pytest.mark.e2e
def test_air_basketball_resize_keeps_cross_transit_suspended(
    page: Page,
    running_server: str,
):
    _stub_unavailable_air_basketball_avatar(page)
    page.set_viewport_size({"width": 1280, "height": 720})
    page.goto(
        f"{running_server}/air_basketball?test_mode=1",
        wait_until="domcontentloaded",
    )
    page.wait_for_function(
        "() => Boolean(window.AirBasketballMVP?.test?.prepareIsolatedCrossTest)"
    )
    page.evaluate(
        """
        () => {
          document.querySelector('.arcade-floor').style.gap = '260px';
          window.AirBasketballMVP.test.prepareIsolatedCrossTest();
        }
        """
    )
    assert page.evaluate("window.AirBasketballMVP.shootPlayer(900, -180)") is True
    page.wait_for_function(
        "window.AirBasketballMVP.getState().trackedGuestSuspended === true",
        timeout=3000,
    )

    page.set_viewport_size({"width": 1180, "height": 700})
    suspended = page.evaluate("window.AirBasketballMVP.getState()")
    assert suspended["trackedGuestSuspended"] is True
    expect(page.locator("#cross-ball")).to_have_class(re.compile(r"\bis-crossing\b"))

    page.wait_for_function(
        "window.AirBasketballMVP.getState().trackedGuestSuspended === false",
        timeout=4000,
    )


@pytest.mark.e2e
def test_air_basketball_new_session_restores_character_identity(
    page: Page,
    running_server: str,
):
    character_requests = []

    def fulfill_character(route):
        character_requests.append(route.request.url)
        route.fulfill(
            json={
                "lanlan_name": "Round Two Lolita",
                "model_type": "unavailable",
                "live3d_sub_type": "",
                "live2d_path": "",
                "vrm_path": "",
            }
        )

    page.route("**/api/game/air-basketball/character*", fulfill_character)
    page.goto(
        f"{running_server}/air_basketball?lanlan_name=Round%20Two%20Lolita",
        wait_until="domcontentloaded",
    )
    page.wait_for_function("window.AirBasketballMVP && window.AirBasketballMVP.getState")
    expect(page.locator("#opponent-name")).to_have_text("Round Two Lolita")

    page.locator(".mode-picker label").nth(1).click()
    page.locator("#start-button").click()
    page.wait_for_timeout(400)
    assert len(character_requests) == 1

    page.locator("#stop-match").click()
    expect(page.locator("#start-overlay")).to_be_visible()
    page.locator("#start-button").click()
    for _ in range(60):
        if len(character_requests) >= 2:
            break
        page.wait_for_timeout(50)
    assert len(character_requests) >= 2
    assert "lanlan_name=Round+Two+Lolita" in character_requests[-1]


@pytest.mark.e2e
def test_air_basketball_resize_preserves_active_ball_state(
    page: Page,
    running_server: str,
):
    page.goto(f"{running_server}/air_basketball", wait_until="domcontentloaded")
    result = page.evaluate(
        """
        async () => {
          const version = new URL(
            document.querySelector('script[src*="/air-basketball/game.js"]').src
          ).search;
          const { ShotLane } = await import(`/static/air-basketball/physics.js${version}`);
          const canvas = document.createElement('canvas');
          Object.assign(canvas.style, {
            position:'fixed', left:'0', top:'0', width:'480px', height:'720px'
          });
          document.body.appendChild(canvas);
          const lane = new ShotLane({
            canvas,
            side:'player',
            onScore:() => {},
            onMiss:() => {},
            onCross:() => false,
            onGuestScore:() => {},
            onBallClash:() => {}
          });
          lane.shoot(420, -250);
          const guest = lane.receiveGuestBall({
            x:120, y:310, r:20, vx:180, vy:-90, owner:'neko'
          });
          const before = [lane.ball, guest].map(ball => ({
            x:ball.x / lane.width,
            y:ball.y / lane.height,
            vx:ball.vx / lane.width,
            vy:ball.vy / lane.height,
            r:ball.r / lane.width
          }));
          canvas.style.width = '300px';
          canvas.style.height = '450px';
          lane.resize();
          const after = [lane.ball, guest].map(ball => ({
            x:ball.x / lane.width,
            y:ball.y / lane.height,
            vx:ball.vx / lane.width,
            vy:ball.vy / lane.height,
            r:ball.r / lane.width
          }));
          canvas.remove();
          return { before, after };
        }
        """
    )
    for before, after in zip(result["before"], result["after"]):
        for key in ("x", "y", "vx", "vy", "r"):
            assert after[key] == pytest.approx(before[key], abs=1e-9)


@pytest.mark.e2e
def test_air_basketball_fixed_cross_shot_hits_neko_without_ai_interference(
    page: Page,
    running_server: str,
):
    page.goto(
        f"{running_server}/air_basketball?test_mode=1",
        wait_until="domcontentloaded",
    )
    page.wait_for_function("window.AirBasketballMVP && window.AirBasketballMVP.getState")
    page.wait_for_selector("#air-neko-avatar.is-ready", timeout=20000)
    page.evaluate("window.AirBasketballMVP.test.prepareIsolatedCrossTest()")

    # The test-only setup freezes Neko decisions, clears both guest-ball
    # collections, and suspends the idle ready ball outside collision handling.
    isolated_state = page.evaluate("window.AirBasketballMVP.getState()")
    assert isolated_state["nekoAction"] == "idle"
    assert isolated_state["playerGuestBalls"] == 0
    assert isolated_state["nekoGuestBalls"] == 0
    assert isolated_state["nekoActiveBalls"] == 0

    # This player-reachable trajectory crosses the lower-body hit zone. The old
    # (900, -180) probe fell below the visible avatar and only passed because the
    # former lower hitbox extended far into the cabinet.
    assert page.evaluate("window.AirBasketballMVP.shootPlayer(800, -400)") is True
    page.wait_for_function(
        "window.AirBasketballMVP.getState().nekoBallHitCount === 1",
        timeout=1600,
    )
    hit_state = page.evaluate("window.AirBasketballMVP.getState()")
    assert hit_state["nekoHitCount"] == 1
    assert hit_state["nekoBallHitCount"] == 1
    assert hit_state["nekoAttention"]["crossThreat"] > 0
    assert hit_state["nekoAttention"]["revenge"] > 0
    assert hit_state["ballClashCount"] == 0
    assert hit_state["neko"]["attempts"] == 0
    assert hit_state["nekoGuestMotion"] is not None
    assert hit_state["nekoGuestMotion"]["owner"] == "player"
    assert hit_state["nekoGuestMotion"]["vx"] < 0


@pytest.mark.e2e
def test_air_basketball_cross_shot_has_no_wall_above_neko(
    page: Page,
    running_server: str,
):
    page.goto(
        f"{running_server}/air_basketball?test_mode=1",
        wait_until="domcontentloaded",
    )
    page.wait_for_function("window.AirBasketballMVP && window.AirBasketballMVP.getState")
    page.wait_for_selector("#air-neko-avatar.is-ready", timeout=20000)
    page.evaluate("window.AirBasketballMVP.test.prepareIsolatedCrossTest()")

    # A deliberately high diagnostic shot clears both the hoop and the avatar.
    # It must pass the cabinet edge before the viewport safety edge returns it.
    assert page.evaluate("window.AirBasketballMVP.shootPlayer(900, -800)") is True
    page.wait_for_function(
        """
        () => {
          const state = window.AirBasketballMVP.getState();
          return state.nekoGuestMotion?.xRatio > 1.01
            && state.nekoGuestMotion.vx > 0;
        }
        """,
        timeout=2200,
    )
    outside_state = page.evaluate("window.AirBasketballMVP.getState()")
    assert outside_state["nekoBallHitCount"] == 0
    assert outside_state["nekoGuestMotion"]["yRatio"] < 0.4

    page.wait_for_function(
        """
        () => {
          const motion = window.AirBasketballMVP.getState().nekoGuestMotion;
          return motion?.xRatio > 1 && motion.vx < 0;
        }
        """,
        timeout=1200,
    )


@pytest.mark.e2e
def test_air_basketball_cross_ball_remains_interactive_outside_neko_court(
    page: Page,
    running_server: str,
):
    page.goto(
        f"{running_server}/air_basketball?test_mode=1",
        wait_until="domcontentloaded",
    )
    page.wait_for_function("window.AirBasketballMVP && window.AirBasketballMVP.getState")
    page.evaluate("window.AirBasketballMVP.test.prepareIsolatedCrossTest()")
    assert page.evaluate("window.AirBasketballMVP.shootPlayer(900, -800)") is True
    page.wait_for_function(
        "window.AirBasketballMVP.getState().nekoGuestMotion !== null",
        timeout=2200,
    )
    assert page.evaluate(
        """
        window.AirBasketballMVP.test.setTrackedGuestForTest({
          xRatio:1.04, yRatio:.12, vx:0, vy:0, owner:'player'
        })
        """
    ) is True
    page.wait_for_function("window.AirBasketballMVP.getState().crossBallInteractive")

    ball_box = page.locator("#cross-ball").bounding_box()
    court_box = page.locator("#neko-court").bounding_box()
    assert ball_box and court_box
    ball_x = ball_box["x"] + ball_box["width"] / 2
    ball_y = ball_box["y"] + ball_box["height"] / 2
    assert ball_x > court_box["x"] + court_box["width"]

    before = page.evaluate("window.AirBasketballMVP.getState().interferenceCount")
    page.mouse.move(ball_x, ball_y)
    page.mouse.down()
    page.mouse.move(ball_x - 28, ball_y - 14, steps=4)
    page.mouse.up()
    page.wait_for_function(
        f"window.AirBasketballMVP.getState().interferenceCount > {before}"
    )


@pytest.mark.e2e
def test_air_basketball_tracked_cross_ball_keeps_viewport_boundary_after_owner_change(
    page: Page,
    running_server: str,
):
    page.goto(
        f"{running_server}/air_basketball?test_mode=1",
        wait_until="domcontentloaded",
    )
    page.wait_for_function("window.AirBasketballMVP && window.AirBasketballMVP.getState")
    page.evaluate("window.AirBasketballMVP.test.prepareIsolatedCrossTest()")
    assert page.evaluate("window.AirBasketballMVP.shootPlayer(900, -800)") is True
    page.wait_for_function(
        "window.AirBasketballMVP.getState().nekoGuestMotion !== null",
        timeout=2200,
    )
    assert page.evaluate(
        """
        window.AirBasketballMVP.test.setTrackedGuestForTest({
          xRatio:.98, yRatio:.08, vx:900, vy:0, owner:'neko'
        })
        """
    ) is True
    page.wait_for_function(
        """
        () => {
          const state = window.AirBasketballMVP.getState();
          return state.nekoGuestMotion?.owner === 'neko'
            && state.nekoGuestMotion.xRatio > 1
            && state.nekoGuestMotion.vx < 0;
        }
        """,
        timeout=1200,
    )
    state = page.evaluate("window.AirBasketballMVP.getState()")
    assert state["nekoGuestBalls"] == 1
    assert state["crossBallInteractive"] is True
    expect(page.locator("#cross-ball")).to_be_visible()


@pytest.mark.e2e
def test_air_basketball_avatar_has_no_input_or_ball_collision_before_ready(
    page: Page,
    running_server: str,
):
    page.goto(
        f"{running_server}/air_basketball?test_mode=1",
        wait_until="domcontentloaded",
    )
    page.wait_for_function("window.AirBasketballMVP && window.AirBasketballMVP.getState")
    page.wait_for_selector("#air-neko-avatar.is-ready", timeout=20000)
    page.evaluate(
        """
        () => {
          const avatar = document.getElementById('air-neko-avatar');
          avatar.classList.remove('is-ready');
          delete avatar.dataset.avatarReady;
          window.AirBasketballMVP.test.prepareIsolatedCrossTest();
        }
        """
    )
    zone_pointer_events = page.locator(".avatar-hit-zone-torso").evaluate(
        "zone => getComputedStyle(zone).pointerEvents"
    )
    assert zone_pointer_events == "none"
    assert page.evaluate("window.AirBasketballMVP.shootPlayer(800, -400)") is True
    page.wait_for_timeout(1700)
    state = page.evaluate("window.AirBasketballMVP.getState()")
    assert state["avatarReady"] is False
    assert state["nekoHitCount"] == 0
    assert state["nekoBallHitCount"] == 0


@pytest.mark.e2e
def test_air_basketball_cross_boundary_state_is_frame_rate_independent(
    page: Page,
    running_server: str,
):
    page.goto(
        f"{running_server}/air_basketball?test_mode=1",
        wait_until="domcontentloaded",
    )
    page.wait_for_function("window.AirBasketballMVP?.test?.planPhysicsSteps")
    boundaries = page.evaluate(
        """
        async () => {
          const version = new URL(
            document.querySelector('script[src*="/air-basketball/game.js"]').src
          ).search;
          const { ShotLane } = await import(`/static/air-basketball/physics.js${version}`);
          return [60, 30, 20, 15].map(fps => {
            const canvas = document.createElement('canvas');
            Object.assign(canvas.style, {
              position:'fixed', left:'0', top:'0', width:'512px', height:'525px'
            });
            document.body.appendChild(canvas);
            let crossed = null;
            const lane = new ShotLane({
              canvas,
              side:'player',
              onScore:() => {},
              onMiss:() => {},
              onCross:data => { crossed = data; return true; },
              onGuestScore:() => {},
              onBallClash:() => {}
            });
            lane.shoot(900, -180);
            const frameSeconds = 1 / fps;
            const plan = window.AirBasketballMVP.test.planPhysicsSteps(frameSeconds);
            for (let frame = 0; frame < fps * 2 && !crossed; frame += 1) {
              for (let step = 0; step < plan.steps && !crossed; step += 1) {
                lane.update(plan.stepSeconds, true);
              }
            }
            canvas.remove();
            return { fps, ...crossed };
          });
        }
        """,
    )
    by_fps = {boundary["fps"]: boundary for boundary in boundaries}
    for boundary in boundaries:
        assert boundary["edge"] == "right"
        assert boundary["x"] == pytest.approx(boundary["sourceWidth"], abs=1e-9)
        assert 0 <= boundary["stepRemainder"] < 0.033
        assert boundary["owner"] == "player"

    baseline = by_fps[60]
    assert by_fps[30]["y"] == pytest.approx(baseline["y"], abs=1e-9)
    assert by_fps[30]["vx"] == pytest.approx(baseline["vx"], abs=1e-9)
    assert by_fps[30]["vy"] == pytest.approx(baseline["vy"], abs=1e-9)
    for fps in (20, 15):
        assert by_fps[fps]["y"] == pytest.approx(baseline["y"], abs=1.0)
        assert by_fps[fps]["vx"] == pytest.approx(baseline["vx"], abs=0.1)
        assert by_fps[fps]["vy"] == pytest.approx(baseline["vy"], abs=0.2)


@pytest.mark.e2e
def test_air_basketball_neko_auto_shot_has_stable_scoring_window(
    page: Page,
    running_server: str,
):
    page.goto(
        f"{running_server}/air_basketball?test_mode=1",
        wait_until="domcontentloaded",
    )
    page.wait_for_function("window.AirBasketballMVP?.test?.planPhysicsSteps")
    results = page.evaluate(
        """
        async () => {
          const version = new URL(
            document.querySelector('script[src*="/air-basketball/game.js"]').src
          ).search;
          const { ShotLane } = await import(`/static/air-basketball/physics.js${version}`);
          const originalRandom = Math.random;
          const seededRandom = seed => {
            let value = seed >>> 0;
            return () => ((value = (value * 1664525 + 1013904223) >>> 0) / 4294967296);
          };
          try {
            return [60, 30, 20, 15].map(fps => {
              Math.random = seededRandom(20260911);
              const canvas = document.createElement('canvas');
              Object.assign(canvas.style, {
                position:'fixed', left:'0', top:'0', width:'480px', height:'720px'
              });
              document.body.appendChild(canvas);
              let makes = 0;
              let misses = 0;
              let aimMissStreak = 0;
              let currentMissStreak = 0;
              let longestMissStreak = 0;
              const lane = new ShotLane({
                canvas,
                side:'neko',
                onScore:() => {},
                onGuestScore:() => {
                  makes += 1;
                  aimMissStreak = 0;
                  currentMissStreak = 0;
                },
                onMiss:() => {
                  misses += 1;
                  aimMissStreak = Math.min(3, aimMissStreak + 1);
                  currentMissStreak += 1;
                  longestMissStreak = Math.max(longestMissStreak, currentMissStreak);
                },
                onCross:() => false,
                onBallClash:() => {}
              });
              for (let shot = 0; shot < 400; shot += 1) {
                lane.clearGuests();
                lane.releaseAutoShot(Math.min(.88, .76 + aimMissStreak * .04));
                const frameSeconds = 1 / fps;
                const plan = window.AirBasketballMVP.test.planPhysicsSteps(frameSeconds);
                for (let frame = 0; frame < fps * 6 && lane.guests.length; frame += 1) {
                  for (let step = 0; step < plan.steps; step += 1) lane.update(plan.stepSeconds, true);
                }
              }
              canvas.remove();
              return {
                fps,
                makes,
                misses,
                rate:makes / (makes + misses),
                longestMissStreak
              };
            });
          } finally {
            Math.random = originalRandom;
          }
        }
        """,
    )
    by_fps = {result["fps"]: result for result in results}
    assert 0.72 <= by_fps[60]["rate"] <= 0.86
    assert by_fps[30]["rate"] == pytest.approx(by_fps[60]["rate"], abs=1e-9)
    for fps in (20, 15):
        assert by_fps[fps]["rate"] >= 0.68
        assert by_fps[fps]["rate"] == pytest.approx(by_fps[60]["rate"], abs=0.08)
    assert max(result["longestMissStreak"] for result in results) <= 4


@pytest.mark.e2e
def test_air_basketball_sdk_avatar_supports_vrm(page: Page, running_server: str):
    runtime_errors = []
    page.on("pageerror", lambda error: runtime_errors.append(str(error)))
    page.route(
        "**/api/game/air-basketball/character*",
        lambda route: route.fulfill(
            json={
                "lanlan_name": "SDK VRM Test",
                "model_type": "live3d",
                "live3d_sub_type": "vrm",
                "live2d_path": "",
                "vrm_path": "/static/vrm/sister1.0.vrm",
            }
        ),
    )

    page.goto(f"{running_server}/air_basketball", wait_until="domcontentloaded")
    page.wait_for_function("window.AirBasketballMVP && window.AirBasketballMVP.getState")
    page.wait_for_selector("#air-neko-avatar.is-ready", timeout=30000)

    expect(page.locator("#air-neko-avatar")).to_have_attribute("data-renderer", "vrm")
    expect(page.locator("#air-neko-vrm")).to_be_visible()
    assert runtime_errors == []
