from pathlib import Path

import pytest
from playwright.sync_api import Page

from .test_avatar_ui_buttons_split import playground_lifecycle_page
from .test_cat_playground_basic_interactions import _cat_point, _enter, _settle, basic_interaction_page


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def small_move_page(basic_interaction_page: Page):
    page = basic_interaction_page
    page.evaluate("""() => {
        window.smallMoveScene = null;
        window.nekoDesktopWindowSensingContext = {
            getCurrent: () => smallMoveScene,
            subscribe(callback) { window.deliverSmallMoveScene = callback; return () => {}; }
        };
        window.movePhases = [];
        window.moveResults = [];
        window.addEventListener(_getNekoCatMindActionResultEventName(), event => {
            if (event.detail.actionId === 'cat1_small_move') moveResults.push(event.detail);
        });
        window.moveState = () => cat.__nekoIdleReturnSubactionState || cat.__nekoIdleCat1Journey;
        window.startSmallMove = () => _startNekoIdleCat1PairMove(cat, {
            source: 'cat_mind', requestId: 'small-move-browser',
            onAccepted() { movePhases.push('accepted'); },
            onStarted() { movePhases.push('started'); window.playingMovePlan = moveState().pairMovePlan; }
        });
        window.admitSmallMoveWindow = () => {
            const rect = {x: 100, y: 100, width: 900, height: 450};
            smallMoveScene = {status: 'current', sessionId: 'small-move-browser', revision: 1,
                timestamp: performance.now(), rect, changes: [], windows: [{key: 'window-1', rect}]};
            deliverSmallMoveScene(smallMoveScene);
            catBody.vx = catBody.vy = 0;
            catBody.grounded = true;
            _setNekoIdleCat1PlaygroundBodyPosition(catBody, 300,
                rect.y + rect.height - catBody.height * (1 - catBody.visibleInsetRatios.bottom));
            if (!NekoDesktopWindowGravity.requestAdmission(cat, catBody)) throw Error('window admission failed');
        };
    }""")
    page.add_script_tag(path=str(PROJECT_ROOT / "static/avatar/avatar-ui-buttons/idle-desktop-window-gravity.js"))
    return page


def _enter_move(page: Page, mode="visit"):
    _enter(page, "visit" if mode == "window" else mode)
    if mode == "window":
        page.evaluate("admitSmallMoveWindow()")
        _settle(page)
    page.clock.pause_at(page.evaluate("Date.now() + 1"))
    page.evaluate("Math.random = () => 0")


def _start_move(page: Page):
    assert page.evaluate("startSmallMove()")
    page.wait_for_function("movePhases.includes('started')", polling=10)
    assert page.evaluate("movePhases") == ["accepted", "started"]
    assert page.evaluate("art.complete && art.naturalWidth > 0")


def _assert_synced(page: Page):
    assert page.evaluate("""() => {
        const owner = NekoDesktopWindowGravity.getState();
        return Math.abs(parseFloat(container.style.left) - catBody.x) <= 0.51 &&
            Math.abs(parseFloat(container.style.top) - catBody.y) <= 0.51 &&
            (owner.phase === 'idle' || (owner.x === catBody.x && owner.y === catBody.y));
    }""")


@pytest.mark.parametrize("mode", ("gravity", "visit", "window"))
def test_solo_move_runs_full_cycle_and_keeps_body_position_and_owner(small_move_page: Page, mode):
    page = small_move_page
    _enter_move(page, mode)
    page.evaluate("window.moveBodyIds = [...session.bodies.keys()]")
    _start_move(page)
    assert page.evaluate("playingMovePlan.chatMode === 'solo' && playingMovePlan.dy === 0")
    duration = page.evaluate("playingMovePlan.durationMs")
    page.clock.run_for(duration // 2)
    assert page.evaluate("_isNekoIdleCat1PlaygroundMoveActive(cat)")
    assert page.evaluate("catBody.x > playingMovePlan.catStartLeft && catBody.x < playingMovePlan.catStartLeft + playingMovePlan.dx")
    assert page.evaluate("_isNekoIdleAnimationRestartUrlForSource(art.src, moveState().profile.assets.walking())")
    assert page.evaluate("moveResults.length") == 0
    _assert_synced(page)
    page.clock.run_for(duration - duration // 2 + 40)
    assert page.evaluate("!moveState().pairMovePlan && !moveState().pairMoveFrame")
    assert page.evaluate("moveResults.map(value => value.result)") == ["done"]
    assert page.evaluate("Math.abs(moveResults[0].detail.pathDistancePx - playingMovePlan.dx) < 0.01")
    assert page.evaluate("moveResults[0].detail.activityId === moveResults[0].detail.runId")
    assert page.evaluate("session.active && !session.released && session.bodies.get('cat') === catBody")
    assert page.evaluate("[...session.bodies.keys()]") == page.evaluate("moveBodyIds")
    _assert_synced(page)
    settled_x = page.evaluate("catBody.x")
    page.evaluate("_startNekoIdleCat1PlaygroundPhysics(cat)")
    page.clock.run_for(50)
    assert page.evaluate("catBody.x") == settled_x
    assert page.evaluate("audioPlays.length") == 0


@pytest.mark.parametrize("outcome", ("loaded", "error", "timeout", "exit"))
def test_move_waits_for_actual_walking_image_and_cleans_late_callbacks(small_move_page: Page, outcome):
    page = small_move_page
    _enter_move(page)
    held = []

    def hold_image(route):
        if route.request.resource_type != "image":
            route.fallback()
            return
        held.append(route)
        page.evaluate("window.moveImageHeld = true")

    page.route("**/cat-idle-cat4-1.gif*", hold_image)
    assert page.evaluate("startSmallMove()")
    page.wait_for_function("window.moveImageHeld === true", polling=10)
    assert page.evaluate("movePhases") == ["accepted"]
    page.clock.run_for(1200)
    assert page.evaluate("movePhases") == ["accepted"]
    assert page.evaluate("moveState().pairMovePlan.activityPathDistancePx === 0 && moveResults.length === 0")
    if outcome == "timeout":
        page.clock.run_for(4000)
    elif outcome == "exit":
        page.evaluate("nekoIdleCat1Playground.setGravityCatEnabled(false)")
    for route in held:
        if outcome == "error":
            route.abort()
        else:
            route.fulfill(path=str(PROJECT_ROOT / "static/assets/cat-resources/appearance/dev_neko/movement/cat-idle-cat4-1.gif"))
    if outcome == "loaded":
        page.wait_for_function("movePhases.includes('started')", polling=10)
        duration = page.evaluate("playingMovePlan.durationMs")
        page.clock.run_for(duration + 40)
        assert page.evaluate("moveResults.map(value => value.result)") == ["done"]
    else:
        page.wait_for_function("!moveState().pairMovePlan", polling=10)
        page.clock.run_for(10000)
        assert page.evaluate("movePhases") == ["accepted"]
        assert page.evaluate("moveResults.length === 1 && moveResults[0].result !== 'done'")
        assert page.evaluate("moveResults[0].detail.pathDistancePx") == 0
    assert page.evaluate("!moveState().pairMovePlan && !moveState().pairMoveFrame && audioPlays.length === 0")


@pytest.mark.parametrize("interrupt", ("tier", "support", "off", "return"))
def test_move_interruption_reports_actual_path_once_and_does_not_rewind(small_move_page: Page, interrupt):
    page = small_move_page
    _enter_move(page)
    _start_move(page)
    page.clock.run_for(450)
    assert page.evaluate("playingMovePlan.activityPathDistancePx > 0")
    if interrupt == "tier":
        page.evaluate("changeTestTier", "cat2")
    elif interrupt == "support":
        page.evaluate("""() => {
            catBody.grounded = false;
            _setNekoIdleCat1PlaygroundBodyPosition(catBody, catBody.x, catBody.y - 140);
            _startNekoIdleCat1PlaygroundPhysics(cat);
        }""")
        page.clock.run_for(32)
    elif interrupt == "off":
        page.evaluate("nekoIdleCat1Playground.setGravityCatEnabled(false)")
    else:
        page.evaluate("_handleNekoIdleCat1PlaygroundCatClick(cat, null)")
    page.wait_for_function("moveResults.length === 1", polling=10)
    assert page.evaluate("moveResults[0].result !== 'done'")
    assert page.evaluate("moveResults[0].detail.pathDistancePx > 0 && moveResults[0].detail.pathDistancePx < Math.abs(playingMovePlan.dx)")
    page.evaluate("art.dispatchEvent(new Event('load'))")
    page.clock.run_for(10000)
    assert page.evaluate("moveResults.length === 1 && !moveState().pairMovePlan && !moveState().pairMoveFrame")
    assert page.evaluate("returnClicks") == (1 if interrupt == "return" else 0)
    assert page.evaluate("session.released && !session.active" if interrupt in ("off", "return") else "session.active && !session.released")


def test_hover_keeps_its_existing_interruption_behavior_during_move(small_move_page: Page):
    page = small_move_page
    _enter_move(page)
    _start_move(page)
    page.clock.run_for(300)
    x, y = _cat_point(page)
    page.mouse.move(x, y)
    assert page.evaluate("!moveState().pairMovePlan && !!art.__nekoIdleHoverSrc")
    page.wait_for_function("moveResults.length === 1", polling=10)
    assert page.evaluate("moveResults[0].result !== 'done' && session.active && !session.released")
    page.mouse.move(8, 8)
    duration = page.evaluate("_getNekoIdleAnimationDurationMs(art.__nekoIdleHoverSrc)")
    page.clock.run_for(duration + 50)
    assert page.evaluate("!art.__nekoIdleHoverSrc && _isNekoIdleAnimationRestartUrlForSource(art.src, _getNekoIdleReturnCurrentArtUrl(cat))")


@pytest.mark.browser_context_args(has_touch=True)
def test_touch_pending_freezes_move_then_real_drag_interrupts(small_move_page: Page):
    page = small_move_page
    _enter_move(page, "window")
    _start_move(page)
    page.clock.run_for(300)
    x, y = _cat_point(page)
    cdp = page.context.new_cdp_session(page)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y, "id": 1}]})
    assert page.evaluate("session.pointerBodyId === 'cat' && !session.draggingBodyId")
    held_x = page.evaluate("catBody.x")
    page.clock.run_for(400)
    assert page.evaluate("!!moveState().pairMovePlan")
    assert page.evaluate("catBody.x") == held_x
    cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x + 30, "y": y - 40, "id": 1}]})
    assert page.evaluate("session.draggingBodyId === 'cat' && !moveState().pairMovePlan")
    cdp.send("Input.dispatchTouchEvent", {"type": "touchEnd", "touchPoints": []})
    page.wait_for_function("moveResults.length === 1", polling=10)
    assert page.evaluate("moveResults[0].result === 'interrupted' && returnClicks === 0")


def test_ordinary_solo_move_keeps_its_original_immediate_start(small_move_page: Page):
    page = small_move_page
    _enter(page, "ordinary")
    page.evaluate("""() => {
        const shell = document.createElement('div');
        shell.id = 'react-chat-window-shell';
        Object.assign(shell.style, {position: 'fixed', left: '700px', top: '20px', width: '300px', height: '100px'});
        document.body.appendChild(shell);
        const state = _getNekoIdleCat1Journey(cat);
        state.actionSettled = true;
        Math.random = () => 0;
    }""")
    assert page.evaluate("startSmallMove()")
    assert page.evaluate("movePhases") == ["accepted", "started"]
    duration = page.evaluate("playingMovePlan.durationMs")
    page.clock.run_for(duration + 50)
    assert page.evaluate("moveResults.map(value => value.result)") == ["done"]
    assert page.evaluate("!cat.__nekoIdleCat1PlaygroundDropState?.active && !moveState().pairMovePlan && audioPlays.length === 0")


@pytest.mark.parametrize("change,elapsed_ms", (("floor", 400), ("lateral", 1400)))
def test_window_correction_cancels_move_without_counting_passive_distance(
    small_move_page: Page, change, elapsed_ms
):
    page = small_move_page
    _enter_move(page, "window")
    _start_move(page)
    page.clock.run_for(elapsed_ms)
    path_before = page.evaluate("playingMovePlan.activityPathDistancePx")
    assert path_before > 0
    passive = page.evaluate("""change => {
        const before = {x: catBody.x, y: catBody.y};
        const rect = change === 'floor'
            ? {...smallMoveScene.rect, height: smallMoveScene.rect.height - 2}
            : {...smallMoveScene.rect, x: 600, width: 400};
        smallMoveScene = {...smallMoveScene, revision: smallMoveScene.revision + 1,
            timestamp: performance.now(), rect, windows: [{key: 'window-1', rect}]};
        deliverSmallMoveScene(smallMoveScene);
        return {dx: catBody.x - before.x, dy: catBody.y - before.y,
            vx: catBody.vx, vy: catBody.vy, grounded: catBody.grounded,
            x: catBody.x, left: catBody.x + catBody.width * catBody.visibleInsetRatios.left,
            foot: catBody.y + catBody.height * (1 - catBody.visibleInsetRatios.bottom)};
    }""", change)
    assert abs(passive["dx"]) + abs(passive["dy"]) > 0.5
    assert passive["grounded"] and passive["vx"] == 0 and passive["vy"] == 0
    page.clock.run_for(32)
    page.wait_for_function("moveResults.length === 1", polling=10)
    assert page.evaluate("moveResults[0].result !== 'done' && !moveState().pairMovePlan && !moveState().pairMoveFrame")
    assert page.evaluate("moveResults[0].detail.pathDistancePx") == path_before
    assert page.evaluate("playingMovePlan.activityPathDistancePx") == path_before
    # Changing walking art back to idle may move its transparent layout box;
    # the visible support point must stay where the physical owner put it.
    assert page.evaluate("""passive => {
        const foot = catBody.y + catBody.height * (1 - catBody.visibleInsetRatios.bottom);
        return Math.abs(foot - passive.foot) < 0.01;
    }""", passive)
    if change == "floor":
        assert page.evaluate("catBody.x") == passive["x"]
    else:
        assert page.evaluate("""passive => Math.abs(
            catBody.x + catBody.width * catBody.visibleInsetRatios.left - passive.left
        ) < 0.01""", passive)
    _assert_synced(page)
    page.clock.run_for(4000)
    assert page.evaluate("moveResults.length === 1 && !moveState().pairMovePlan && !moveState().pairMoveFrame")
    assert page.evaluate("moveResults[0].detail.pathDistancePx") == path_before
    assert page.evaluate("session.active && !session.released && session.bodies.get('cat') === catBody")
    _assert_synced(page)


def _hold_small_move_image(page: Page):
    held = []

    def hold_image(route):
        if route.request.resource_type != "image":
            route.fallback()
            return
        held.append(route)
        page.evaluate("window.pendingMoveImageHeld = true")

    page.route("**/cat-idle-cat4-1.gif*", hold_image)
    assert page.evaluate("startSmallMove()")
    page.wait_for_function("window.pendingMoveImageHeld === true", polling=10)
    page.evaluate("window.pendingReadyPlan = moveState().pairMovePlan")
    return held


def _load_held_small_move_image(page: Page, held):
    for route in held:
        route.fulfill(path=str(PROJECT_ROOT / "static/assets/cat-resources/appearance/dev_neko/movement/cat-idle-cat4-1.gif"))
    page.evaluate("""() => art.complete ? undefined : new Promise(resolve => {
        art.addEventListener('load', resolve, {once: true});
    })""")


@pytest.mark.browser_context_args(has_touch=True)
@pytest.mark.parametrize("mode", ("visit", "window"))
def test_pending_during_image_load_keeps_accepted_plan_until_pointercancel(small_move_page: Page, mode):
    page = small_move_page
    _enter_move(page, mode)
    held = _hold_small_move_image(page)
    x, y = _cat_point(page)
    cdp = page.context.new_cdp_session(page)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y, "id": 1}]})
    assert page.evaluate("session.pointerBodyId === 'cat' && !session.draggingBodyId")
    held_x = page.evaluate("catBody.x")
    _load_held_small_move_image(page, held)
    page.clock.run_for(600)
    assert page.evaluate("moveState().pairMovePlan === pendingReadyPlan && !pendingReadyPlan.playbackStarted")
    assert page.evaluate("movePhases") == ["accepted"]
    assert page.evaluate("catBody.x") == held_x
    assert page.evaluate("pendingReadyPlan.activityPathDistancePx === 0 && moveResults.length === 0")
    cdp.send("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []})
    page.clock.run_for(32)
    assert page.evaluate("moveState().pairMovePlan === pendingReadyPlan && pendingReadyPlan.playbackStarted")
    assert page.evaluate("movePhases") == ["accepted", "started"]
    page.clock.run_for(page.evaluate("pendingReadyPlan.durationMs") + 40)
    assert page.evaluate("moveResults.map(value => value.result)") == ["done"]
    assert page.evaluate("Math.abs(moveResults[0].detail.pathDistancePx - Math.abs(pendingReadyPlan.dx)) < .01")
    assert page.evaluate("!moveState().pairMovePlan && !moveState().pairMoveFrame && returnClicks === 0")
    _assert_synced(page)


@pytest.mark.browser_context_args(has_touch=True)
@pytest.mark.parametrize("interrupt", ("drag", "tier", "exit"))
def test_ready_image_waiting_for_pending_cannot_revive_after_interrupt(small_move_page: Page, interrupt):
    page = small_move_page
    _enter_move(page)
    held = _hold_small_move_image(page)
    x, y = _cat_point(page)
    cdp = page.context.new_cdp_session(page)
    cdp.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y, "id": 1}]})
    _load_held_small_move_image(page, held)
    page.clock.run_for(32)
    assert page.evaluate("moveState().pairMovePlan === pendingReadyPlan && moveState().pairMoveFrame !== 0")
    assert page.evaluate("movePhases") == ["accepted"]
    if interrupt == "drag":
        cdp.send("Input.dispatchTouchEvent", {"type": "touchMove", "touchPoints": [{"x": x + 30, "y": y - 40, "id": 1}]})
    elif interrupt == "tier":
        page.evaluate("changeTestTier", "cat2")
    else:
        page.evaluate("nekoIdleCat1Playground.setGravityCatEnabled(false)")
    cdp.send("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []})
    page.wait_for_function("moveResults.length === 1", polling=10)
    assert page.evaluate("moveResults[0].result !== 'done' && moveResults[0].detail.pathDistancePx === 0")
    page.evaluate("art.dispatchEvent(new Event('load'))")
    page.clock.run_for(10000)
    assert page.evaluate("movePhases") == ["accepted"]
    assert page.evaluate("moveResults.length === 1 && !moveState().pairMovePlan && !moveState().pairMoveFrame && returnClicks === 0")



@pytest.mark.parametrize("axis,shift", (("screenX", 100), ("screenY", 80)))
def test_solo_position_waits_for_synchronized_renderer_origin(small_move_page: Page, axis, shift):
    page = small_move_page
    _enter_move(page, "window")
    snapshot = page.evaluate("""() => ({x:catBody.x,y:catBody.y,left:container.style.left,
        top:container.style.top,owner:NekoDesktopWindowGravity.getState()})""")
    page.evaluate("""({axis,shift}) => Object.defineProperty(window,axis,
        {configurable:true,value:window[axis]+shift})""", {"axis": axis, "shift": shift})
    assert page.evaluate("_getNekoIdleCat1PlaygroundMoveTarget(cat,{left:100000,top:catBody.y})") is None
    assert not page.evaluate("_setNekoIdleCat1ContainerPosition(container,catBody.x+40,catBody.y)")
    assert page.evaluate("""() => ({x:catBody.x,y:catBody.y,left:container.style.left,
        top:container.style.top,owner:NekoDesktopWindowGravity.getState()})""") == snapshot
    page.evaluate("""() => {
        smallMoveScene = {...smallMoveScene,revision:smallMoveScene.revision+1,timestamp:performance.now()};
        deliverSmallMoveScene(smallMoveScene);
    }""")
    assert page.evaluate("""() => {
        const target = _getNekoIdleCat1PlaygroundMoveTarget(cat,{left:100000,top:catBody.y});
        return !!target && _setNekoIdleCat1ContainerPosition(container,target.left,target.top);
    }""")
    assert page.evaluate("""() => {
        const right = window.screenX+catBody.x+catBody.width*(1-catBody.visibleInsetRatios.right);
        const foot = window.screenY+catBody.y+catBody.height*(1-catBody.visibleInsetRatios.bottom);
        return right <= smallMoveScene.rect.x+smallMoveScene.rect.width+.5 &&
            Math.abs(foot-smallMoveScene.rect.y-smallMoveScene.rect.height) <= .5;
    }""")
    _assert_synced(page)
    assert page.evaluate("moveResults.length === 0 && session.active && !session.released && session.bodies.get('cat') === catBody")
