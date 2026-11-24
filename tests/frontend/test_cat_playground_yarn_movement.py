import pytest
from playwright.sync_api import Page

from .test_avatar_ui_buttons_split import playground_lifecycle_page
from .test_cat_playground_basic_interactions import basic_interaction_page, _cat_point
from .test_cat_playground_small_move import small_move_page, _enter_move, _assert_synced


@pytest.fixture
def yarn_movement_page(small_move_page: Page):
    page = small_move_page
    _enter_move(page, "visit")
    page.evaluate("""() => {
        window.yarnBody = session.bodies.get('yarn') || session.bodies.get('desktop-yarn');
        if (!yarnBody) {
            const element = document.createElement('div');
            Object.assign(element.style, {position:'fixed',width:'64px',height:'64px',left:'650px',top:'200px'});
            document.body.appendChild(element);
            yarnBody = _createNekoIdleCat1PlaygroundPhysicsBody('yarn', element);
            session.bodies.set(yarnBody.id, yarnBody);
        }
        session.yarnEnabled = true;
        window.walkResults = [];
        window.addEventListener('neko:cat-mind:observation', event => {
            if (event.detail.type === 'cat1_walk_done_near_chat') walkResults.push(event.detail.detail);
        });
        window.placeYarn = x => {
            _updateNekoIdleCat1PlaygroundBodyBounds(yarnBody);
            Object.assign(yarnBody,{vx:0,vy:0,angularVelocity:0,grounded:true,dragging:false});
            _setNekoIdleCat1PlaygroundBodyPosition(yarnBody,x,yarnBody.floorY);
        };
        window.positionCat = x => {
            _setNekoIdleCat1PlaygroundBodyPosition(catBody,x,catBody.floorY);
            catBody.vx = catBody.vy = 0; catBody.grounded = true;
        };
        window.beginYarnJourney = () => { Math.random = () => 0; _syncNekoIdleCat1Journey(cat); };
    }""")
    return page


@pytest.mark.parametrize("direction", ("left", "right"))
def test_real_page_walks_to_physical_yarn_then_keeps_one_local_result(yarn_movement_page: Page, direction):
    page = yarn_movement_page
    page.evaluate("positionCat", 800 if direction == "left" else 100)
    page.evaluate("placeYarn", 350 if direction == "left" else 650)
    start = page.evaluate("catBody.x")
    page.evaluate("beginYarnJourney()")
    page.wait_for_function("moveState().playgroundJourney?.playbackStarted", polling=10)
    page.evaluate("Math.random = () => .9")
    page.clock.run_for(1000)
    current = page.evaluate("catBody.x")
    assert current < start if direction == "left" else current > start
    assert page.evaluate("walkResults.length === 0 && moveState().playgroundJourney !== null")
    _assert_synced(page)
    page.clock.run_for(10000)
    assert page.evaluate("walkResults.length === 1 && !moveState().playgroundJourney")
    assert page.evaluate("moveState().walkFinishResolution") == "stretch"
    assert page.evaluate("walkResults[0].pathDistancePx") == pytest.approx(abs(page.evaluate("catBody.x") - start))
    page.evaluate("_finishNekoIdleCat1Walk(cat)")
    assert page.evaluate("walkResults.length") == 1
    assert page.evaluate("session.active && !session.released && session.bodies.get('cat') === catBody")


@pytest.mark.parametrize("interrupt", ("tier", "ball-removed", "support-lost", "exit"))
def test_real_page_yarn_walk_interruptions_preserve_partial_work_and_ignore_old_frames(yarn_movement_page: Page, interrupt):
    page = yarn_movement_page
    page.evaluate("positionCat", 100); page.evaluate("placeYarn", 650)
    page.evaluate("beginYarnJourney()")
    page.wait_for_function("moveState().playgroundJourney?.playbackStarted", polling=10)
    page.clock.run_for(400)
    path = page.evaluate("moveState().walkActivity.pathDistancePx")
    assert path > 0
    if interrupt == "tier":
        page.evaluate("changeTestTier", "cat2")
    elif interrupt == "ball-removed":
        page.evaluate("session.bodies.delete(yarnBody.id)")
    elif interrupt == "support-lost":
        page.evaluate("catBody.y -= 100; catBody.grounded = false")
    else:
        page.evaluate("nekoIdleCat1Playground.setGravityCatEnabled(false)")
    page.evaluate("_syncNekoIdleCat1PlaygroundJourney(cat)")
    page.clock.run_for(32)
    assert page.evaluate("!moveState().playgroundJourney && walkResults.length === 1")
    assert page.evaluate("walkResults[0].cancelled")
    assert page.evaluate("walkResults[0].pathDistancePx") == pytest.approx(path)
    page.evaluate("art.dispatchEvent(new Event('load'))")
    page.clock.run_for(500)
    assert page.evaluate("walkResults.length") == 1


@pytest.mark.parametrize("direction", ("left", "right"))
def test_real_page_pair_move_uses_two_physical_positions_and_existing_result(yarn_movement_page: Page, direction):
    page = yarn_movement_page
    page.evaluate("positionCat", 400)
    page.evaluate("placeYarn", 520)
    # Use the actual side target before requesting the existing Cat Mind runner.
    page.evaluate("""() => {
        const target = _getNekoIdleCat1PlaygroundYarnTarget(cat);
        _setNekoIdleCat1PlaygroundBodyPosition(catBody,target.left,target.top);
    }""")
    page.evaluate("Math.random = () => " + (".5" if direction == "left" else "0"))
    assert page.evaluate("startSmallMove()")
    page.wait_for_function("movePhases.includes('started')", polling=10)
    assert page.evaluate("playingMovePlan.chatMode") == "playground"
    start = page.evaluate("({cat:catBody.x,yarn:yarnBody.x,duration:playingMovePlan.durationMs})")
    page.clock.run_for(start["duration"] // 2)
    movement = page.evaluate("({cat:catBody.x,yarn:yarnBody.x})")
    assert movement["cat"] - start["cat"] == pytest.approx(movement["yarn"] - start["yarn"])
    assert abs(movement["cat"] - start["cat"]) > 40
    page.clock.run_for(start["duration"] - start["duration"] // 2 + 40)
    assert page.evaluate("moveResults.map(value => value.result)") == ["done"]
    assert page.evaluate("moveResults[0].detail.pathDistancePx") == pytest.approx(abs(page.evaluate("playingMovePlan.dx")))
    _assert_synced(page)


@pytest.mark.browser_context_args(has_touch=True)
@pytest.mark.parametrize("movement", ("journey", "pair"))
def test_real_touch_pending_freezes_yarn_movement_and_cancel_resumes_its_cycle(yarn_movement_page: Page, movement):
    page = yarn_movement_page
    page.evaluate("positionCat", 100 if movement == "journey" else 400)
    page.evaluate("placeYarn", 650 if movement == "journey" else 520)
    if movement == "journey":
        page.evaluate("beginYarnJourney()")
        page.wait_for_function("moveState().playgroundJourney?.playbackStarted", polling=10)
        page.evaluate("window.ownedMovement = moveState().playgroundJourney")
    else:
        page.evaluate("""() => {
            const target = _getNekoIdleCat1PlaygroundYarnTarget(cat);
            _setNekoIdleCat1PlaygroundBodyPosition(catBody,target.left,target.top);
        }""")
        assert page.evaluate("startSmallMove()")
        page.wait_for_function("movePhases.includes('started')", polling=10)
        page.evaluate("window.ownedMovement = moveState().pairMovePlan")
    page.clock.run_for(200)
    x, y = _cat_point(page)
    cdp = page.context.new_cdp_session(page)
    cdp.send("Input.dispatchTouchEvent", {"type":"touchStart","touchPoints":[{"x":x,"y":y,"id":1}]})
    page.clock.run_for(32)
    held = page.evaluate("({cat:catBody.x,yarn:yarnBody.x})")
    page.clock.run_for(900)
    assert page.evaluate("({cat:catBody.x,yarn:yarnBody.x})") == held
    assert page.evaluate("moveState().playgroundJourney === ownedMovement || moveState().pairMovePlan === ownedMovement")
    cdp.send("Input.dispatchTouchEvent", {"type":"touchCancel","touchPoints":[]})
    page.evaluate("Math.random = () => .9")
    page.clock.run_for(12000)
    if movement == "journey":
        assert page.evaluate("walkResults.length === 1 && !moveState().playgroundJourney")
    else:
        assert page.evaluate("moveResults.map(value => value.result)") == ["done"]
        assert page.evaluate("moveResults[0].detail.durationMs <= ownedMovement.durationMs + 40")
    assert page.evaluate("returnClicks") == 0


def test_real_mouse_hover_keeps_original_walking_response_and_resumes_after_leave(yarn_movement_page: Page):
    page = yarn_movement_page
    page.evaluate("positionCat",100); page.evaluate("placeYarn",650)
    start_x = page.evaluate("catBody.x")
    page.evaluate("beginYarnJourney()")
    page.wait_for_function("moveState().playgroundJourney?.playbackStarted",polling=10)
    page.clock.run_for(300)
    page.evaluate("window.hoverOwnedApproach = moveState().playgroundJourney")
    x,y = _cat_point(page); page.mouse.move(x,y)
    page.wait_for_function("moveState().paused && art.__nekoIdleHoverSrc",polling=10)
    held = page.evaluate("({x:catBody.x,src:art.src})")
    page.clock.run_for(400)
    assert page.evaluate("({x:catBody.x,src:art.src})") == held
    assert page.evaluate("moveState().playgroundJourney === hoverOwnedApproach")
    page.mouse.move(0,0); page.evaluate("Math.random = () => .9")
    # Duration metadata is fetched asynchronously; advance only after the
    # existing hover completion timer has actually been registered.
    page.wait_for_function("art.__nekoIdleHoverTimer", polling=10)
    page.clock.run_for(12000)
    assert page.evaluate("!moveState().paused && !moveState().playgroundJourney && walkResults.length === 1")
    assert page.evaluate("walkResults[0].cancelled !== true")
    assert page.evaluate("walkResults[0].pathDistancePx") == pytest.approx(abs(page.evaluate("catBody.x") - start_x))


@pytest.mark.browser_context_args(has_touch=True)
@pytest.mark.parametrize("movement", ("journey", "pair"))
def test_real_touch_hold_before_a_delayed_frame_excludes_the_whole_pause(yarn_movement_page: Page, movement):
    page = yarn_movement_page
    page.evaluate("positionCat", 100 if movement == "journey" else 400)
    page.evaluate("placeYarn", 650 if movement == "journey" else 520)
    if movement == "journey":
        page.evaluate("beginYarnJourney()")
        page.wait_for_function("moveState().playgroundJourney?.playbackStarted", polling=10)
    else:
        page.evaluate("""() => {
            const target = _getNekoIdleCat1PlaygroundYarnTarget(cat);
            _setNekoIdleCat1PlaygroundBodyPosition(catBody,target.left,target.top);
        }""")
        assert page.evaluate("startSmallMove()")
        page.wait_for_function("movePhases.includes('started')", polling=10)
    page.clock.run_for(300)
    before = page.evaluate("""movement => {
        const activity = movement === 'journey' ? moveState().walkActivity : moveState().pairMovePlan;
        return {path: movement === 'journey' ? activity.pathDistancePx : activity.activityPathDistancePx,
            duration: Date.now() - (movement === 'journey' ? activity.startedAt : activity.activityStartedAt)};
    }""", movement)
    assert before["path"] > 0
    x, y = _cat_point(page)
    cdp = page.context.new_cdp_session(page)
    cdp.send("Input.dispatchTouchEvent", {"type":"touchStart","touchPoints":[{"x":x,"y":y,"id":1}]})
    assert page.evaluate("session.pointerBodyId === 'cat' && !session.draggingBodyId")
    # Coalesce pending animation frames instead of feeding one immediately at press.
    page.clock.fast_forward(1000)
    page.evaluate("_cancelNekoIdleCat1Journey(cat,{resetArt:true,reason:'held-clock-cancelled'})")
    result = page.evaluate("walkResults[0]" if movement == "journey" else "moveResults[0].detail")
    assert result["pathDistancePx"] == pytest.approx(before["path"])
    assert result["durationMs"] == pytest.approx(before["duration"], abs=1)
    cdp.send("Input.dispatchTouchEvent", {"type":"touchCancel","touchPoints":[]})
    assert page.evaluate("returnClicks") == 0


def test_loading_hover_resumes_owned_walking_image_and_cycle(yarn_movement_page: Page):
    page = yarn_movement_page
    held = []
    page.evaluate('window.heldWalkingRequestCount = 0')
    def hold_walking(route):
        if route.request.resource_type != 'image':
            route.fallback()
            return
        held.append(route)
        page.evaluate('window.heldWalkingRequestCount = (window.heldWalkingRequestCount || 0) + 1')
    page.route('**/cat-idle-cat4-1.gif*', hold_walking)
    page.evaluate('positionCat', 100)
    page.evaluate('placeYarn', 650)
    page.evaluate('beginYarnJourney()')
    page.wait_for_function('heldWalkingRequestCount >= 1 && moveState().playgroundJourney?.loading', polling=10)
    page.evaluate('window.loadingHoverApproach = moveState().playgroundJourney')
    x = page.evaluate('catBody.x')
    px, py = _cat_point(page)
    page.mouse.move(px, py)
    page.wait_for_function('moveState().paused && !!art.__nekoIdleHoverSrc && art.complete && art.naturalWidth > 0', polling=10)
    duration = page.evaluate('_getNekoIdleAnimationDurationMs(art.__nekoIdleHoverSrc)')
    page.mouse.move(0, 0)
    page.clock.run_for(duration + 32)
    page.wait_for_function('heldWalkingRequestCount >= 2', polling=10)
    assert page.evaluate('moveState().playgroundJourney === loadingHoverApproach && !moveState().paused')
    assert page.evaluate('walkResults.length === 0 && !loadingHoverApproach.playbackStarted')
    assert page.evaluate('catBody.x') == x
    page.clock.run_for(3000)
    assert page.evaluate('moveState().playgroundJourney === loadingHoverApproach && !loadingHoverApproach.playbackStarted')
    assert page.evaluate('walkResults.length') == 0
    held[-1].fallback()
    page.wait_for_function('loadingHoverApproach.playbackStarted && art.complete && art.naturalWidth > 0', polling=10)
    page.evaluate('Math.random = () => .9')
    page.clock.run_for(10000)
    assert page.evaluate('walkResults.length === 1 && !moveState().playgroundJourney')
    assert page.evaluate('walkResults[0].pathDistancePx') > 200
    assert page.evaluate('walkResults[0].durationMs') < 10000
    assert page.evaluate("session.active && !session.released && session.bodies.get('cat') === catBody")
    _assert_synced(page)
