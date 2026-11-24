from pathlib import Path

import pytest
from playwright.sync_api import Page

from .test_avatar_ui_buttons_split import playground_lifecycle_page
from .test_cat_playground_basic_interactions import _cat_point, _enter, basic_interaction_page


@pytest.fixture
def yarn_action_page(basic_interaction_page: Page):
    page = basic_interaction_page
    page.evaluate("""() => {
        const overlay = document.createElement('div');
        overlay.id = 'react-chat-window-overlay';
        const shell = document.createElement('div');
        shell.id = 'react-chat-window-shell'; shell.className = 'is-minimized';
        Object.assign(shell.style, {position:'fixed',left:'400px',top:'200px',width:'58px',height:'58px'});
        const icon = document.createElement('img'); icon.className = 'react-chat-minimized-icon';
        icon.src = '/static/assets/neko-idle/chat-minimized-yarn-ball-compact-press-1x.png';
        Object.assign(icon.style, {display:'block',width:'58px',height:'58px'});
        shell.appendChild(icon); overlay.appendChild(shell); document.body.appendChild(overlay);
        window.playPhases = []; window.playResults = []; window.playVisibility = [];
        window.addEventListener('neko:cat-mind:action-result', event => {
            if (event.detail.actionId === 'cat1_play_yarn') playResults.push(event.detail);
        });
        window.addEventListener('neko:idle-cat1-play-yarn-visibility', event => playVisibility.push(event.detail));
        window.playState = () => _getNekoIdleCat1PlayActionState(cat);
        window.startYarnPlay = () => _playNekoIdleCat1PlayAction(cat, {
            source:'cat_mind',requestId:'browser-play-yarn',
            onAccepted() { playPhases.push('accepted'); },
            onStarted() { playPhases.push('started'); window.playStartedAt = Date.now(); }
        });
    }""")
    _enter(page, "gravity", audio=True)
    page.clock.pause_at(page.evaluate("Date.now() + 1"))
    page.evaluate("""() => {
        _cancelNekoIdleCat1Journey(cat,{resetArt:true,reason:'test-position-setup'});
        window.playYarn = session.bodies.get('yarn') || session.bodies.get('desktop-yarn');
        if (!playYarn) throw Error('real Playground yarn body missing');
        _updateNekoIdleCat1PlaygroundBodyBounds(playYarn);
        Object.assign(playYarn,{x:400,y:playYarn.floorY,vx:0,vy:0,grounded:true});
        _setNekoIdleCat1PlaygroundBodyPosition(playYarn,playYarn.x,playYarn.y,{force:true});
        const target = _getNekoIdleCat1PlaygroundYarnTarget(cat);
        if (!target) throw Error('safe yarn approach target missing');
        _setNekoIdleCat1PlaygroundBodyPosition(catBody,target.left,target.top,{force:true});
    }""")
    return page


def _wait_started(page: Page):
    page.wait_for_function("playPhases.includes('started')", polling=10)
    assert page.evaluate("playPhases") == ["accepted", "started"]


def _assert_ball_returned(page: Page):
    assert page.evaluate("playState().active === false && playState().playgroundYarnControl === null")
    assert page.evaluate("session.bodies.get(playYarn.id) === playYarn && !playYarn.actionControl")
    assert page.evaluate("getComputedStyle(playYarn.element).visibility !== 'hidden'")
    assert page.evaluate("session.active && !session.released && session.bodies.get('cat') === catBody")
    assert page.evaluate("playVisibility.length === 0")


def test_play_uses_the_loaded_full_cycle_then_restores_the_same_ball(yarn_action_page: Page):
    page = yarn_action_page
    assert page.evaluate("startYarnPlay()")
    _wait_started(page)
    assert page.evaluate("getComputedStyle(playYarn.element).visibility === 'hidden'")
    assert page.evaluate("session.bodies.get(playYarn.id) === playYarn")
    duration = page.evaluate("_getNekoIdleAnimationDurationMs(_NEKO_IDLE_CAT1_PLAY_ASSET_URL)")
    page.clock.run_for(duration - 1)
    assert page.evaluate("playState().active && playResults.length === 0")
    page.clock.run_for(2)
    assert page.evaluate("playResults.map(value => value.result)") == ["done"]
    _assert_ball_returned(page)


def test_real_image_failure_returns_ball_without_started_or_done(yarn_action_page: Page):
    page = yarn_action_page
    image_name = page.evaluate("_NEKO_IDLE_CAT1_PLAY_ASSET_URL.split('?')[0].split('/').pop()")
    page.route(f"**/{image_name}*", lambda route: route.abort())
    assert page.evaluate("startYarnPlay()")
    page.wait_for_function("playResults.length === 1", polling=10)
    assert page.evaluate("playPhases") == ["accepted"]
    assert page.evaluate("playResults[0].result !== 'done' && playResults[0].detail.durationMs === 0")
    _assert_ball_returned(page)


def test_switching_playing_yarn_to_visit_restores_chat_shell_before_removing_body(yarn_action_page: Page):
    page = yarn_action_page
    original_style = page.evaluate("({visibility: playYarn.element.style.visibility, pointerEvents: playYarn.element.style.pointerEvents})")
    assert page.evaluate("startYarnPlay()")
    _wait_started(page)
    assert page.evaluate("playYarn.element.style.visibility === 'hidden' && !!playYarn.actionControl")
    assert page.evaluate("_switchNekoIdleCat1PlaygroundToVisitMode()")
    assert page.evaluate("({visibility: playYarn.element.style.visibility, pointerEvents: playYarn.element.style.pointerEvents})") == original_style
    assert page.evaluate("getComputedStyle(playYarn.element).visibility !== 'hidden'")
    assert page.evaluate("session.active && !session.released && !session.yarnEnabled && session.bodies.size === 1 && session.bodies.get('cat') === catBody")
    assert page.evaluate("!playState().active && !playState().playgroundYarnControl && !playYarn.actionControl")
    assert page.evaluate("playResults.length === 1 && playResults[0].result !== 'done' && playVisibility.length === 0")
    page.clock.run_for(10000)
    assert page.evaluate("playResults.length === 1 && !playState().active && !session.yarnEnabled")
    assert page.evaluate("({visibility: playYarn.element.style.visibility, pointerEvents: playYarn.element.style.pointerEvents})") == original_style


@pytest.mark.browser_context_args(has_touch=True)
def test_pending_touch_during_image_load_keeps_the_accepted_play(yarn_action_page: Page):
    page = yarn_action_page
    held = []
    asset_path = page.evaluate("_NEKO_IDLE_CAT1_PLAY_ASSET_URL.split('?')[0]")

    def hold_image(route):
        held.append(route)
        page.evaluate("window.playImageHeld = true")

    page.route(f"**/{asset_path.split('/')[-1]}*", hold_image)
    assert page.evaluate("startYarnPlay()")
    page.wait_for_function("window.playImageHeld === true", polling=10)
    x, y = _cat_point(page)
    cdp = page.context.new_cdp_session(page)
    cdp.send("Input.dispatchTouchEvent", {"type":"touchStart","touchPoints":[{"x":x,"y":y,"id":1}]})
    page.clock.run_for(500)
    assert page.evaluate("playPhases") == ["accepted"]
    for route in held:
        route.fulfill(path=str(Path(__file__).resolve().parents[2] / asset_path.lstrip('/')))
    _wait_started(page)
    duration = page.evaluate("_getNekoIdleAnimationDurationMs(_NEKO_IDLE_CAT1_PLAY_ASSET_URL)")
    page.clock.run_for(duration - 1)
    assert page.evaluate("playState().active && playResults.length === 0")
    cdp.send("Input.dispatchTouchEvent", {"type":"touchCancel","touchPoints":[]})
    page.clock.run_for(2)
    assert page.evaluate("playResults.map(value => value.result)") == ["done"]
    _assert_ball_returned(page)


@pytest.mark.browser_context_args(has_touch=True)
def test_native_pending_touch_and_audio_off_preserve_existing_play(yarn_action_page: Page):
    page = yarn_action_page
    assert page.evaluate("startYarnPlay()")
    _wait_started(page)
    x, y = _cat_point(page)
    cdp = page.context.new_cdp_session(page)
    cdp.send("Input.dispatchTouchEvent", {"type":"touchStart","touchPoints":[{"x":x,"y":y,"id":1}]})
    assert page.evaluate("session.pointerBodyId === 'cat' && !session.draggingBodyId")
    page.evaluate("setNekoIdleCatAudioEnabled(false)")
    duration = page.evaluate("_getNekoIdleAnimationDurationMs(_NEKO_IDLE_CAT1_PLAY_ASSET_URL)")
    page.clock.run_for(duration - 1)
    assert page.evaluate("playState().active && playResults.length === 0")
    cdp.send("Input.dispatchTouchEvent", {"type":"touchCancel","touchPoints":[]})
    page.clock.run_for(2)
    assert page.evaluate("playResults.map(value => value.result)") == ["done"]
    _assert_ball_returned(page)
    assert page.evaluate("returnClicks") == 0


@pytest.mark.parametrize("interrupt", ("tier", "drag", "ball-removed", "exit"))
def test_play_interrupt_cannot_restore_a_lost_ball_or_revive_old_image(yarn_action_page: Page, interrupt):
    page = yarn_action_page
    assert page.evaluate("startYarnPlay()")
    _wait_started(page)
    if interrupt == "tier":
        page.evaluate("changeTestTier('cat2')")
    elif interrupt == "drag":
        x, y = _cat_point(page)
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 35, y - 40)
        page.mouse.up()
    elif interrupt == "ball-removed":
        page.evaluate("_removeNekoIdleCat1PlaygroundYarnBody(cat)")
    else:
        page.evaluate("nekoIdleCat1Playground.setGravityCatEnabled(false)")
    page.wait_for_function("playResults.length === 1", polling=10)
    assert page.evaluate("playResults[0].result !== 'done'")
    page.evaluate("art.dispatchEvent(new Event('load'))")
    page.clock.run_for(10000)
    assert page.evaluate("playResults.length === 1 && !playState().active && !playState().timer && !playState().entryTimer")
    if interrupt == "ball-removed":
        assert page.evaluate("!session.bodies.has(playYarn.id)")
    if interrupt == "exit":
        assert page.evaluate("playYarn.element.style.visibility !== 'hidden' && playYarn.element.style.pointerEvents !== 'none'")
    if interrupt in ("tier", "drag"):
        _assert_ball_returned(page)
