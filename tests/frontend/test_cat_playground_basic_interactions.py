from pathlib import Path

import pytest
from playwright.sync_api import Page, expect

from .test_avatar_ui_buttons_split import playground_lifecycle_page


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def basic_interaction_page(playground_lifecycle_page: Page):
    page = playground_lifecycle_page
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.clock.install()
    page.emulate_media(reduced_motion="reduce")
    page.evaluate("""() => {
        window.manualFacts = [];
        window.bubblePops = [];
        window.captureCalls = [];
        window.audioPlays = [];
        window.pointerDowns = [];
        window.addEventListener('neko:return-ball-manual-move', event => {
            const {container: ignored, ...fact} = event.detail;
            manualFacts.push(fact);
        });
        window.addEventListener('neko:thought-bubble-pop', event => bubblePops.push(event.detail.tier));
        window.addEventListener('pointerdown', event => pointerDowns.push(event.pointerId), true);
        for (const element of [container, cat]) {
            const capture = element.setPointerCapture.bind(element);
            element.setPointerCapture = id => { captureCalls.push(id); return capture(id); };
        }
        window.Audio = class extends EventTarget {
            constructor(src) {
                super(); this.src = src; this.duration = 7.18; this.currentTime = 0;
                this.ended = false; this.paused = true; this.volume = 1;
            }
            play() { this.paused = false; audioPlays.push(this.src); return Promise.resolve(); }
            pause() { this.paused = true; }
        };
        window.enterBasicMode = ({mode, tier}) => {
            if (mode !== 'ordinary') {
                if (!_startNekoIdleCat1PlaygroundDrop(cat, {}, {catOnly: mode === 'visit'})) throw Error('entry failed');
                window.session = cat.__nekoIdleCat1PlaygroundDropState;
                window.catBody = session.bodies.get('cat');
            }
            changeTestTier(tier);
        };
        window.startBasicVoice = () => {
            const tier = cat.getAttribute('data-neko-idle-tier');
            const started = tier === 'cat1'
                ? _playNekoIdleCat1AmbientSound(_nekoIdleCat1AmbientSoundState.token)
                : _playNekoIdleSleepSound(tier, _nekoIdleSleepSoundState.token);
            if (!started) throw Error('voice runner rejected');
            window.oldVoice = tier === 'cat1' ? _nekoIdleCat1AmbientSoundState.audio : _nekoIdleSleepSoundState.audio;
        };
    }""")
    yield page
    assert errors == []


def _settle(page: Page):
    for _ in range(20):
        page.clock.run_for(200)
        if page.evaluate("_isNekoIdleCat1PlaygroundCatPresentationStable(cat)"):
            return
    assert page.evaluate("_isNekoIdleCat1PlaygroundCatPresentationStable(cat)")


def _enter(page: Page, mode: str = "visit", tier: str = "cat1", audio: bool = False):
    page.evaluate("enterBasicMode", {"mode": mode, "tier": tier})
    if mode != "ordinary":
        _settle(page)
    page.evaluate("setNekoIdleCatAudioEnabled", audio)
    assert page.evaluate("audioPlays.length") == 0, "qualification restoration must not play immediately"


def _cat_point(page: Page):
    box = page.locator("#live2d-btn-return").bounding_box()
    assert box is not None
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def _bubble_point(page: Page):
    bubble = page.locator(".neko-idle-thought-bubble")
    expect(bubble).to_be_visible()
    box = bubble.bounding_box()
    assert box is not None
    return box["x"] + box["width"] / 2, box["y"] + box["height"] / 2


def _bubble_cat_overlap_point(page: Page):
    point = page.evaluate("""() => {
        const bubble = cat.querySelector('.neko-idle-thought-bubble').getBoundingClientRect();
        const body = cat.getBoundingClientRect();
        const left = Math.max(bubble.left, body.left), right = Math.min(bubble.right, body.right);
        const top = Math.max(bubble.top, body.top), bottom = Math.min(bubble.bottom, body.bottom);
        if (right <= left || bottom <= top) throw Error('Bubble and cat must overlap for this input race');
        return {x: (left + right) / 2, y: top + (bottom - top) * 0.75};
    }""")
    return point["x"], point["y"]


def _assert_no_cat_gesture(page: Page):
    assert page.evaluate("manualFacts.length === 0 && captureCalls.length === 0 && returnClicks === 0")
    assert page.evaluate("session.active && !session.released && session.pointerBodyId === '' && session.draggingBodyId === ''")


@pytest.mark.parametrize("tier", ("cat1", "cat2", "cat3"))
@pytest.mark.parametrize("audio", (False, True))
def test_question_block_exit_restores_current_tier_feedback_without_replaying(
    basic_interaction_page: Page, tier, audio
):
    page = basic_interaction_page
    page.evaluate("""() => {
        _storeNekoIdleCat1PlaygroundQuestionBlockClone(cat,
            _createNekoIdleCat1PlaygroundQuestionBlockClone({left: 350, top: 120, width: 48, height: 48}, cat));
    }""")
    _enter(page, "gravity", tier, audio=audio)
    page.clock.run_for(2000)
    assert page.evaluate("session.bodies.get('question-block').grounded")
    if audio:
        page.evaluate("startBasicVoice()")
        _bubble_point(page)
    plays_before_exit = page.evaluate("audioPlays.length")
    page.locator(".neko-idle-cat1-playground-question-block").click()
    assert page.evaluate("session.released && !session.active && session.bodies.size === 0 && returnClicks === 0")
    assert page.evaluate("_nekoIdleCat1AmbientSoundState.active") is (audio and tier == "cat1")
    assert page.evaluate("_nekoIdleSleepSoundState.tier") == (tier if audio and tier != "cat1" else "none")
    assert page.evaluate("audioPlays.length") == plays_before_exit
    assert page.evaluate("!cat.classList.contains('is-thought-bubble-active')")
    if audio:
        page.evaluate("startBasicVoice()")
        _bubble_point(page)
        assert page.evaluate("audioPlays.length") == plays_before_exit + 1


@pytest.mark.parametrize("mode", ("ordinary", "gravity", "visit"))
@pytest.mark.parametrize("tier", ("cat1", "cat2", "cat3"))
def test_hover_uses_existing_cycle_in_both_modes_and_cat_only(basic_interaction_page: Page, mode, tier):
    page = basic_interaction_page
    _enter(page, mode, tier)
    x, y = _cat_point(page)
    page.mouse.move(x, y)
    hover = page.evaluate("({src: art.__nekoIdleHoverSrc, token: art.__nekoIdleHoverToken, startedAt: art.__nekoIdleHoverStartedAt})")
    assert hover["src"]
    assert page.evaluate("art.getAttribute('src')") == hover["src"]
    page.clock.run_for(100)
    assert page.evaluate("art.__nekoIdleHoverToken") == hover["token"]
    assert page.evaluate("art.__nekoIdleHoverStartedAt") == hover["startedAt"]
    page.mouse.move(8, 8)
    duration = page.evaluate("_getNekoIdleAnimationDurationMs(art.__nekoIdleHoverSrc)")
    page.clock.run_for(max(0, duration - 100) + 100)
    assert page.evaluate("!art.__nekoIdleHoverSrc && art.__nekoIdleHoverTimer === 0")
    assert page.evaluate("_isNekoIdleAnimationRestartUrlForSource(art.getAttribute('src'), _getNekoIdleReturnCurrentArtUrl(cat))")
    if mode != "ordinary":
        assert page.evaluate("session.active && !session.released && session.bodies.get('cat') === catBody")


@pytest.mark.parametrize("tier", ("cat1", "cat2", "cat3"))
def test_bubble_hold_and_light_motion_do_not_capture_or_drag_cat(basic_interaction_page: Page, tier):
    page = basic_interaction_page
    _enter(page, tier=tier, audio=True)
    page.evaluate("startBasicVoice()")
    x, y = _bubble_point(page)
    page.mouse.move(x, y)
    page.mouse.down()
    page.clock.run_for(100)
    _assert_no_cat_gesture(page)
    page.mouse.move(x + 2, y + 2)
    _assert_no_cat_gesture(page)
    page.mouse.up()
    assert page.evaluate("bubblePops.length") == 1
    _assert_no_cat_gesture(page)
    page.locator(".neko-idle-thought-bubble").dispatch_event("click")
    assert page.evaluate("bubblePops.length") == 1
    page.clock.run_for(600)
    assert page.evaluate("!cat.classList.contains('is-thought-bubble-active') && oldVoice.paused && oldVoice.volume === 0")
    follow_ups = page.evaluate("Number(_isNekoIdleCat1EatActionActive(cat)) + Number(_isNekoIdleCat1VisualActionActive(cat))")
    assert follow_ups == (1 if tier == "cat1" else 0)
    assert page.evaluate("!_isNekoIdleCat1PlayActionActive(cat)")
    assert page.evaluate("session.active && !session.released")


@pytest.mark.browser_context_args(has_touch=True)
@pytest.mark.parametrize("kind", ("touch", "keyboard", "cancel"))
def test_bubble_alternate_inputs_keep_playground_owner(basic_interaction_page: Page, kind):
    page = basic_interaction_page
    _enter(page, audio=True)
    page.evaluate("startBasicVoice()")
    x, y = _bubble_point(page)
    if kind == "touch":
        page.touchscreen.tap(x, y)
        assert page.evaluate("pointerDowns.length") >= 1
    elif kind == "keyboard":
        page.locator(".neko-idle-thought-bubble").focus()
        page.keyboard.press("Enter")
    else:
        page.mouse.move(x, y)
        page.mouse.down()
        page.locator(".neko-idle-thought-bubble").dispatch_event(
            "pointercancel", {"pointerId": page.evaluate("pointerDowns.at(-1)"), "pointerType": "mouse"}
        )
        page.mouse.move(8, 8)
        page.mouse.up()
    _assert_no_cat_gesture(page)
    assert page.evaluate("bubblePops.length") == (0 if kind == "cancel" else 1)


@pytest.mark.parametrize("tier", ("cat1", "cat2", "cat3"))
@pytest.mark.parametrize("expiry", ("audio-ended", "timer"))
def test_bubble_hold_expiry_cannot_retarget_release_to_cat(basic_interaction_page: Page, tier, expiry):
    page = basic_interaction_page
    _enter(page, tier=tier, audio=True)
    page.evaluate("startBasicVoice()")
    _bubble_point(page)
    x, y = _bubble_cat_overlap_point(page)
    page.mouse.move(x, y)
    page.mouse.down()
    _assert_no_cat_gesture(page)
    if expiry == "audio-ended":
        page.evaluate("oldVoice.ended = true; oldVoice.dispatchEvent(new Event('ended'))")
    else:
        page.clock.run_for(8000)
    assert page.evaluate("!cat.classList.contains('is-thought-bubble-active')")
    # The release still belongs to the bubble even though Chromium now hits
    # the cat button underneath it. No capture is used to force a target.
    page.mouse.up()
    _assert_no_cat_gesture(page)
    assert page.evaluate("bubblePops.length") == 0
    assert page.evaluate("cat.getAttribute('data-neko-idle-tier')") == tier


@pytest.mark.parametrize("terminal", ("expired-release", "cancel"))
def test_bubble_press_cleanup_keeps_next_real_cat_click_working(basic_interaction_page: Page, terminal):
    page = basic_interaction_page
    _enter(page, audio=True)
    page.evaluate("startBasicVoice()")
    _bubble_point(page)
    x, y = _bubble_cat_overlap_point(page)
    page.mouse.move(x, y)
    page.mouse.down()
    if terminal == "expired-release":
        page.evaluate("oldVoice.ended = true; oldVoice.dispatchEvent(new Event('ended'))")
    else:
        page.locator(".neko-idle-thought-bubble").dispatch_event(
            "pointercancel", {"pointerId": page.evaluate("pointerDowns.at(-1)"), "pointerType": "mouse"}
        )
        page.mouse.move(8, 8)
    page.mouse.up()
    _assert_no_cat_gesture(page)
    # A new genuine press clears the previous bubble's click suppression; no
    # timeout is advanced here to make this positive control pass.
    x, y = _cat_point(page)
    page.mouse.click(x, y)
    assert page.evaluate("returnClicks") == 1
    assert page.evaluate("session.released && !session.active && session.releaseReason === 'cat-click'")


@pytest.mark.parametrize("mode", ("gravity", "visit"))
@pytest.mark.parametrize("tier", ("cat1", "cat2", "cat3"))
def test_bubble_press_keeps_its_release_when_gravity_is_disabled(basic_interaction_page: Page, mode, tier):
    page = basic_interaction_page
    _enter(page, mode=mode, tier=tier, audio=True)
    page.evaluate("startBasicVoice()")
    _bubble_point(page)
    x, y = _bubble_cat_overlap_point(page)
    page.mouse.move(x, y)
    page.mouse.down()
    _assert_no_cat_gesture(page)
    page.evaluate("nekoIdleCat1Playground.setGravityCatEnabled(false)")
    assert page.evaluate("session.released && !session.active && !cat.classList.contains('is-thought-bubble-active')")
    assert page.evaluate("""({x, y}) => !!document.elementFromPoint(x, y)?.closest('.neko-idle-return-btn')""", {"x": x, "y": y})
    page.mouse.up()
    assert page.evaluate("returnClicks === 0 && manualFacts.length === 0 && captureCalls.length === 0")
    assert page.evaluate("session.thoughtBubblePointerIds.size") == 0
    # The old bubble release is suppressed, not the next ordinary cat gesture.
    x, y = _cat_point(page)
    page.mouse.click(x, y)
    assert page.evaluate("returnClicks") == 1


@pytest.mark.parametrize("mode", ("gravity", "visit"))
def test_bubble_press_survives_immediate_playground_reentry(basic_interaction_page: Page, mode):
    page = basic_interaction_page
    _enter(page, mode=mode, audio=True)
    page.evaluate("startBasicVoice()")
    _bubble_point(page)
    x, y = _bubble_cat_overlap_point(page)
    page.mouse.move(x, y)
    page.mouse.down()
    page.evaluate("""catOnly => {
        nekoIdleCat1Playground.setGravityCatEnabled(false);
        if (!_startNekoIdleCat1PlaygroundDrop(cat, {}, {catOnly})) throw Error('reentry failed');
        window.catBody = session.bodies.get('cat');
    }""", mode == "visit")
    assert page.evaluate("""({x, y}) => !!document.elementFromPoint(x, y)?.closest('.neko-idle-return-btn')""", {"x": x, "y": y})
    page.mouse.up()
    _assert_no_cat_gesture(page)
    assert page.evaluate("session.thoughtBubblePointerIds.size") == 0
    # No clock advance: a genuine new down must clear this bubble's guard.
    x, y = _cat_point(page)
    page.mouse.click(x, y)
    assert page.evaluate("returnClicks") == 1


def test_disabled_gravity_bubble_release_outside_cat_keeps_next_click_working(basic_interaction_page: Page):
    page = basic_interaction_page
    _enter(page, audio=True)
    page.evaluate("startBasicVoice()")
    _bubble_point(page)
    x, y = _bubble_cat_overlap_point(page)
    page.mouse.move(x, y)
    page.mouse.down()
    page.evaluate("nekoIdleCat1Playground.setGravityCatEnabled(false)")
    page.mouse.move(8, 8)
    page.mouse.up()
    assert page.evaluate("returnClicks === 0 && manualFacts.length === 0 && captureCalls.length === 0")
    assert page.evaluate("session.thoughtBubblePointerIds.size") == 0
    x, y = _cat_point(page)
    page.mouse.click(x, y)
    assert page.evaluate("returnClicks") == 1


@pytest.mark.browser_context_args(has_touch=True)
def test_disabled_gravity_native_touch_cancel_releases_bubble_identity(basic_interaction_page: Page):
    page = basic_interaction_page
    _enter(page, audio=True)
    page.evaluate("startBasicVoice()")
    _bubble_point(page)
    x, y = _bubble_cat_overlap_point(page)
    native_input = page.context.new_cdp_session(page)
    native_input.send("Input.dispatchTouchEvent", {"type": "touchStart", "touchPoints": [{"x": x, "y": y, "id": 11}]})
    _assert_no_cat_gesture(page)
    page.evaluate("nekoIdleCat1Playground.setGravityCatEnabled(false)")
    native_input.send("Input.dispatchTouchEvent", {"type": "touchCancel", "touchPoints": []})
    assert page.evaluate("returnClicks === 0 && manualFacts.length === 0 && captureCalls.length === 0")
    assert page.evaluate("session.thoughtBubblePointerIds.size") == 0
    x, y = _cat_point(page)
    page.mouse.click(x, y)
    assert page.evaluate("returnClicks") == 1


def test_repeated_gravity_off_keeps_bubble_handoff_until_its_terminal(basic_interaction_page: Page):
    page = basic_interaction_page
    _enter(page, audio=True)
    page.evaluate("startBasicVoice()")
    _bubble_point(page)
    x, y = _bubble_cat_overlap_point(page)
    page.mouse.move(x, y)
    page.mouse.down()
    page.evaluate("nekoIdleCat1Playground.setGravityCatEnabled(false)")
    assert page.evaluate("session.thoughtBubblePointerIds.size") == 1
    page.evaluate("nekoIdleCat1Playground.setGravityCatEnabled(false)")
    assert page.evaluate("session.thoughtBubblePointerIds.size") == 1
    page.mouse.up()
    assert page.evaluate("returnClicks === 0 && manualFacts.length === 0 && captureCalls.length === 0")
    assert page.evaluate("session.thoughtBubblePointerIds.size") == 0
    x, y = _cat_point(page)
    page.mouse.click(x, y)
    assert page.evaluate("returnClicks") == 1


@pytest.mark.parametrize("cleanup", ("container-remove", "pagehide"))
def test_bubble_handoff_is_disposed_when_its_page_or_container_is_removed(basic_interaction_page: Page, cleanup):
    page = basic_interaction_page
    _enter(page, audio=True)
    page.evaluate("startBasicVoice()")
    _bubble_point(page)
    x, y = _bubble_cat_overlap_point(page)
    page.mouse.move(x, y)
    page.mouse.down()
    page.evaluate("nekoIdleCat1Playground.setGravityCatEnabled(false)")
    if cleanup == "container-remove":
        page.evaluate("_removeFloatingButtonsElement(container)")
        assert page.evaluate("!container.isConnected")
    else:
        page.evaluate("window.dispatchEvent(new Event('pagehide'))")
    assert page.evaluate("session.thoughtBubblePointerIds.size === 0 && session.suppressClickTimer === 0")
    # Real page teardown delivers no further input to the old UI. Release in
    # the background here to verify that its detached handoff cannot revive.
    page.mouse.move(8, 8)
    page.mouse.up()
    page.clock.run_for(1000)
    assert page.evaluate("returnClicks === 0 && manualFacts.length === 0 && captureCalls.length === 0")


@pytest.mark.parametrize("interrupt", ("drag", "tier", "support", "audio"))
def test_basic_feedback_interrupts_without_late_audio_or_bubble_revival(basic_interaction_page: Page, interrupt):
    page = basic_interaction_page
    _enter(page, audio=True)
    page.evaluate("startBasicVoice()")
    _bubble_point(page)
    if interrupt == "drag":
        x, y = _cat_point(page)
        page.mouse.move(x, y)
        page.mouse.down()
        page.mouse.move(x + 30, y - 60)
    elif interrupt == "tier":
        page.evaluate("changeTestTier", "cat2")
    elif interrupt == "support":
        page.evaluate("""() => {
            catBody.grounded = false;
            _setNekoIdleCat1PlaygroundBodyPosition(catBody, catBody.x, catBody.y - 100, {force: true});
            _startNekoIdleCat1PlaygroundPhysics(cat);
        }""")
        page.clock.run_for(32)
    else:
        page.evaluate("setNekoIdleCatAudioEnabled", False)
    assert page.evaluate("oldVoice.paused && !cat.classList.contains('is-thought-bubble-active')")
    before = page.evaluate("audioPlays.length")
    page.evaluate("""() => {
        oldVoice.dispatchEvent(new Event('playing'));
        oldVoice.ended = true;
        oldVoice.dispatchEvent(new Event('ended'));
        oldVoice.dispatchEvent(new Event('error'));
    }""")
    if interrupt == "drag":
        page.mouse.up()
    page.clock.run_for(8000)
    assert page.evaluate("audioPlays.length") == before
    assert page.evaluate("!cat.classList.contains('is-thought-bubble-active') && !cat.__nekoIdleThoughtBubblePop")
    assert page.evaluate("session.active && !session.released && returnClicks === 0")


def test_desktop_windows_collector_covers_only_live_playground_bubble(basic_interaction_page: Page):
    pc = PROJECT_ROOT.parent / "N.E.K.O.-PC"
    bridge_path = pc / "src/preload/bridges/pet-input-region-bridge.js"
    helper_path = pc / "src/preload/shared/windows-touch-regions.js"
    if not bridge_path.is_file() or not helper_path.is_file():
        pytest.skip("Companion desktop checkout is unavailable")
    source = bridge_path.read_text()
    helper = helper_path.read_text()

    def function(text, name, indent=""):
        start = text.index(f"function {name}(")
        close = "\n" + indent + "}"
        return text[start:text.index(close, start) + len(close)]

    start = source.index("const CAT1_PLAYGROUND_THOUGHT_BUBBLE_SELECTOR =")
    end = source.index("\n    ];", start) + len("\n    ];")
    page = basic_interaction_page
    _enter(page, audio=True)
    page.evaluate("startBasicVoice()")
    _bubble_point(page)
    # Native collectors intentionally ignore zero-opacity controls during the
    # first frame of the existing bubble fade-in.
    page.clock.run_for(400)
    expect(page.locator(".neko-idle-thought-bubble")).to_have_css("opacity", "1")
    page.add_script_tag(content="""(() => {
        const STORAGE_COMPLETION_CARD_INPUT_SELECTOR = '.storage-card';
        const VISIT_CAT_INPUT_ACTOR_SELECTOR = '.visit-actor';
        const VISIT_CAT_INPUT_DOCK_SELECTOR = '.visit-dock';
        const VISIT_CAT_INPUT_OBJECT_SELECTOR = '.visit-object';
        function isBlankPage(){return false;} function isHomePage(){return true;}
        function hasVisibleX11FullWindowSurface(){return false;}
        function isVRMGuidedMovementTargetActive(){return false;}
        function isCurrentModelLocked(){return false;}
        function isCurrentModelInGoodbyeMode(){return true;}
        function shouldSkipYuiGuideResidualShapeElement(){return false;}
    """ + source[start:end] + "\n" + function(helper, "visibleTouchRegion") + "\n" +
        function(source, "isCat1PlaygroundSurfaceTakingPointer", "    ") + "\n" +
        "var CAT1_PLAYGROUND_SURFACE_SELECTOR = '.neko-idle-cat1-playground-question-block, .neko-idle-cat1-playground-desktop-yarn-mirror';\n" +
        function(source, "collectWindowsPetTouchRegions", "    ") +
        "\nwindow.collectNativeBubbleRegions = collectWindowsPetTouchRegions;})();")
    point = page.locator(".neko-idle-thought-bubble").bounding_box()
    assert point is not None
    probe = {"x": point["x"] + point["width"] / 2, "y": point["y"] + 3}
    covered = "point => collectNativeBubbleRegions().some(r => point.x >= r.x && point.x <= r.x+r.width && point.y >= r.y && point.y <= r.y+r.height)"
    assert page.evaluate(covered, probe), page.evaluate("""() => {
        const bubble = cat.querySelector('.neko-idle-thought-bubble');
        const css = getComputedStyle(bubble);
        return {regions: collectNativeBubbleRegions(), classes: container.className, catClasses: cat.className,
            pointerEvents: css.pointerEvents, opacity: css.opacity, visible: bubble.checkVisibility({checkOpacity:true,checkVisibilityCSS:true})};
    }""")
    page.locator(".neko-idle-thought-bubble").click()
    assert not page.evaluate(covered, probe), "popping pointer-events:none must release the native bubble region"
