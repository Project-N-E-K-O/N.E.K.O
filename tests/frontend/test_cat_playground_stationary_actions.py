from pathlib import Path

import pytest
from playwright.sync_api import Page

from .test_avatar_ui_buttons_split import playground_lifecycle_page
from .test_cat_playground_basic_interactions import (
    _bubble_point,
    _cat_point,
    _enter,
    _settle,
    basic_interaction_page,
)


PROJECT_ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def stationary_action_page(basic_interaction_page: Page):
    page = basic_interaction_page
    page.evaluate("""() => {
        window.visualPhases = [];
        window.visualResults = [];
        window.addEventListener(_getNekoCatMindActionResultEventName(), event => {
            if (event.detail.detail.runId === window.visualRunId) visualResults.push(event.detail);
        });
        window.startVisual = key => {
            const profile = _getNekoIdleCat1VisualActionProfile(key);
            const tier = _getNekoIdleVisualActionTier(profile);
            const actionId = tier === 'cat2' ? _NEKO_CAT_MIND_ACTION_IDS.CAT2_IDLE_RANDOM :
                tier === 'cat3' ? _NEKO_CAT_MIND_ACTION_IDS.CAT3_IDLE_RANDOM :
                _NEKO_CAT_MIND_ACTION_IDS.CAT1_IDLE_RANDOM;
            return _playNekoCatMindVisualAction(cat, actionId, key, 'stationary-browser-test', {
                onAccepted(run) { window.visualRunId = run.runId; visualPhases.push('accepted'); },
                onStarted() { visualPhases.push('started'); window.visualStartedAt = Date.now(); }
            });
        };
        window.readVisual = () => {
            const state = cat.__nekoIdleCat1VisualActionState;
            return {active: !!state?.active, key: state?.actionKey || '', timer: state?.timer || 0,
                entryTimer: state?.entryTimer || 0, audioTimer: state?.audioStartTimer || 0};
        };
        window.oiiaPlays = () => audioPlays.filter(src => src.includes('cat1-voice-oiia')).length;
    }""")
    return page


def _wait_started(page: Page):
    page.wait_for_function("visualPhases.includes('started')", polling=10)
    assert page.evaluate("visualPhases") == ["accepted", "started"]
    assert page.evaluate("art.complete && art.naturalWidth > 0")


def _assert_visual_cleared(page: Page):
    assert page.evaluate("readVisual()") == {
        "active": False, "key": "", "timer": 0, "entryTimer": 0, "audioTimer": 0,
    }
    assert page.evaluate("!cat.hasAttribute('data-neko-cat1-visual-layout') && !cat.classList.contains('is-cat1-visual-action')")


@pytest.mark.parametrize("mode,tier,key", (
    ("visit", "cat1", "banana"),
    ("gravity", "cat2", "cat2Turn1"),
    ("visit", "cat3", "cat3Turn2"),
    ("gravity", "cat1", "oiia"),
))
def test_stationary_visual_finishes_full_cycle_without_losing_playground(
    stationary_action_page: Page, mode, tier, key
):
    page = stationary_action_page
    _enter(page, mode, tier, audio=key == "oiia")
    assert page.evaluate("startVisual", key)
    _wait_started(page)
    duration = page.evaluate("""key => {
        const profile = _getNekoIdleCat1VisualActionProfile(key);
        return _getNekoIdleAnimationDurationMs(_getNekoIdleCat1VisualActionAssetUrlForSlot(profile.slot))
            .then(ms => ms * (profile.repeatCount || 1));
    }""", key)
    page.clock.run_for(duration - 10)
    assert page.evaluate("readVisual().active")
    assert page.evaluate("visualResults.length") == 0
    assert page.evaluate("_isNekoIdleAnimationRestartUrlForSource(art.src, _getNekoIdleCat1VisualActionAssetUrl(cat))")
    if key == "oiia":
        assert page.evaluate("oiiaPlays()") == 3
    page.clock.run_for(30)
    _assert_visual_cleared(page)
    assert page.evaluate("visualResults.map(result => result.result)") == ["done"]
    assert page.evaluate("session.active && !session.released && session.bodies.get('cat') === catBody")
    assert page.evaluate("cat.getAttribute('data-neko-idle-tier')") == tier
    assert page.evaluate("_isNekoIdleAnimationRestartUrlForSource(art.src, _getNekoIdleReturnCurrentArtUrl(cat))")


def test_ordinary_public_visual_keeps_existing_finish_and_input_behavior(stationary_action_page: Page):
    page = stationary_action_page
    _enter(page, "ordinary")
    assert page.evaluate("""() => NekoCatIdlePresentation.requestCat1Banana({
        button: cat, onTerminal: result => visualResults.push({result})
    })""")
    page.wait_for_function("readVisual().timer !== 0", polling=10)
    page.clock.run_for(5060)
    _assert_visual_cleared(page)
    assert page.evaluate("visualResults.map(result => result.result)") == ["done"]
    assert page.evaluate("!cat.__nekoIdleCat1PlaygroundDropState?.active")
    x, y = _cat_point(page)
    page.mouse.click(x, y)
    assert page.evaluate("returnClicks") == 1


@pytest.mark.parametrize("exit_before_load", (False, True))
def test_playground_reports_started_only_after_real_image_load(
    stationary_action_page: Page, exit_before_load
):
    page = stationary_action_page
    _enter(page)
    held = []

    def hold_image(route):
        held.append(route)
        page.evaluate("window.visualImageHeld = true")

    page.route("**/action/cat-idle-cat1-banana.webp?*", hold_image)
    with page.expect_request("**/action/cat-idle-cat1-banana.webp?*"):
        assert page.evaluate("startVisual", "banana")
    page.wait_for_function("window.visualImageHeld === true", polling=10)
    assert held
    assert page.evaluate("visualPhases") == ["accepted"]
    assert page.evaluate("readVisual().active && !art.complete && readVisual().timer === 0")
    if exit_before_load:
        page.evaluate("nekoIdleCat1Playground.setGravityCatEnabled(false)")
        _assert_visual_cleared(page)
    for route in held:
        route.fulfill(path=str(PROJECT_ROOT / "static/assets/cat-resources/appearance/dev_neko/action/cat-idle-cat1-banana.webp"))
    if exit_before_load:
        page.clock.run_for(8000)
        assert page.evaluate("visualPhases") == ["accepted"]
        assert page.evaluate("visualResults.length") == 1
        assert page.evaluate("visualResults[0].result") != "done"
        _assert_visual_cleared(page)
        assert page.evaluate("session.released && !session.active && oiiaPlays() === 0")
    else:
        _wait_started(page)
        page.clock.run_for(5060)
        _assert_visual_cleared(page)
        assert page.evaluate("visualResults.map(result => result.result)") == ["done"]


@pytest.mark.parametrize("interrupt", ("drag", "tier", "support", "exit"))
def test_oiia_interruption_stops_pending_music_and_cannot_revive(stationary_action_page: Page, interrupt):
    page = stationary_action_page
    _enter(page, audio=True)
    assert page.evaluate("startVisual", "oiia")
    _wait_started(page)
    page.clock.run_for(530)
    assert page.evaluate("oiiaPlays()") == 1
    page.evaluate("window.oldActionAudio = cat.__nekoIdleCat1VisualActionState.audio")
    if interrupt == "drag":
        x, y = _cat_point(page)
        page.mouse.move(x, y)
        page.mouse.down()
        page.clock.run_for(100)
        assert page.evaluate("readVisual().active"), "pending press must keep the active cycle"
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
        page.evaluate("nekoIdleCat1Playground.setGravityCatEnabled(false)")
    _assert_visual_cleared(page)
    assert page.evaluate("oldActionAudio.paused && visualResults.length === 1")
    assert page.evaluate("visualResults[0].result") != "done"
    page.evaluate("""() => {
        art.dispatchEvent(new Event('load'));
        oldActionAudio.dispatchEvent(new Event('playing'));
        oldActionAudio.dispatchEvent(new Event('ended'));
        oldActionAudio.dispatchEvent(new Event('error'));
    }""")
    if interrupt == "drag":
        page.mouse.up()
    page.clock.run_for(8000)
    _assert_visual_cleared(page)
    assert page.evaluate("oiiaPlays() === 1 && visualResults.length === 1 && returnClicks === 0")
    assert page.evaluate("session.released === true && session.active === false" if interrupt == "exit" else "session.active && !session.released")


def test_oiia_cue_rechecks_support_before_the_next_physics_frame(stationary_action_page: Page):
    page = stationary_action_page
    _enter(page, audio=True)
    assert page.evaluate("startVisual", "oiia")
    _wait_started(page)
    page.evaluate("""() => {
        // Leave the cue timer live while the next physics frame has not run.
        if (session.frame) cancelAnimationFrame(session.frame);
        session.frame = 0;
        catBody.grounded = false;
    }""")
    page.clock.run_for(530)
    assert page.evaluate("oiiaPlays()") == 0
    _assert_visual_cleared(page)
    assert page.evaluate("visualResults.length === 1 && visualResults[0].result !== 'done'")


@pytest.mark.parametrize("mode,item,roll,expected", (
    ("gravity", "fish", 0.25, "eat"),
    ("visit", "catnip", 0.25, "catnip1"),
    ("visit", "catnip", 0.75, "catnip2"),
))
def test_bubble_pop_runs_original_food_or_catnip_follow_up(
    stationary_action_page: Page, mode, item, roll, expected
):
    page = stationary_action_page
    _enter(page, mode, audio=True)
    page.evaluate("startBasicVoice()")
    page.evaluate("""({item, roll}) => {
        const src = item === 'fish' ? _NEKO_IDLE_THOUGHT_BUBBLE_FISH_COOKIE_URL : _NEKO_IDLE_THOUGHT_BUBBLE_CATNIP_POUCH_URL;
        cat.__nekoIdleThoughtBubbleItemAssetUrl = src;
        cat.querySelector('.neko-idle-thought-bubble-item').src = _getNekoIdleThoughtBubbleItemAssetUrl(src);
        window.originalRandom = Math.random;
        Math.random = () => roll;
    }""", {"item": item, "roll": roll})
    x, y = _bubble_point(page)
    page.mouse.click(x, y)
    assert page.evaluate("bubblePops.length") == 1
    assert page.evaluate("!_isNekoIdleCat1EatActionActive(cat) && !readVisual().active")
    page.clock.run_for(600)
    page.evaluate("Math.random = originalRandom")
    page.wait_for_function("_isNekoIdleCat1EatActionActive(cat)" if expected == "eat" else "readVisual().key === '" + expected + "'", polling=10)
    page.wait_for_function("cat.__nekoIdleCat1EatActionState.timer !== 0" if expected == "eat" else "readVisual().timer !== 0", polling=10)
    assert page.evaluate("oldVoice.paused && oldVoice.volume === 0 && !cat.__nekoIdleThoughtBubblePop")
    assert page.evaluate("session.active && !session.released && returnClicks === 0 && manualFacts.length === 0")
    # The shared fixture's voice lasts 7.18s; eating must wait for that audio
    # fallback as well as its 4.17s animation. Catnip owns one visual cycle.
    page.clock.run_for(7600 if expected == "eat" else 6100)
    assert page.evaluate("!_isNekoIdleCat1EatActionActive(cat) && !readVisual().active")
    assert page.evaluate("session.active && !session.released && !_isNekoIdleCat1PlayActionActive(cat)")


@pytest.mark.parametrize("kind", ("eat", "stretch"))
@pytest.mark.parametrize("mode", ("gravity", "visit"))
def test_eat_and_stretch_wait_for_image_then_keep_the_full_original_cycle(
    stationary_action_page: Page, kind, mode
):
    page = stationary_action_page
    _enter(page, mode, audio=True)
    page.clock.pause_at(page.evaluate("Date.now() + 1"))
    page.evaluate("""() => {
        window.stationaryImageHeld = new Promise(resolve => { window.resolveStationaryImageHeld = resolve; });
    }""")
    held = []
    pattern = "**/cat-idle-cat1-eat*.webp*" if kind == "eat" else "**/cat-idle-cat4-2.gif*"

    def hold_image(route):
        # Stretch also fetches the GIF for its duration; delay only decoding.
        if route.request.resource_type == "image":
            held.append(route)
            page.evaluate("resolveStationaryImageHeld()")
        else:
            route.fallback()

    page.route(pattern, hold_image)
    state = "cat.__nekoIdleCat1EatActionState" if kind == "eat" else "cat.__nekoIdleCat1StretchActionState"
    call = "_playNekoIdleCat1EatAction(cat)" if kind == "eat" else "_requestNekoIdleCat1HissStretchPresentation()"
    with page.expect_request(pattern):
        assert page.evaluate(call)
    page.evaluate("stationaryImageHeld")
    assert held
    page.clock.run_for(1500)
    assert page.evaluate(f"{state}.active && !art.complete && {state}.timer === 0 && {state}.entryTimer !== 0")
    assert page.evaluate("audioPlays.length") == 0

    for route in held:
        asset = PROJECT_ROOT / route.request.url.split("http://neko.test/", 1)[1].split("?", 1)[0]
        route.fulfill(path=str(asset))
    page.evaluate("""() => art.complete ? undefined : new Promise(resolve => {
        art.addEventListener('load', resolve, {once: true});
    })""")
    duration = page.evaluate(
        "_getNekoIdleAnimationDurationMs(_getNekoIdleCat1EatActionAssetUrl(cat))" if kind == "eat"
        else "_getNekoIdleAnimationDurationMs(_getNekoIdleCat1StretchAssetUrl())"
    )
    assert page.evaluate(f"art.naturalWidth > 0 && {state}.timer > 0 && {state}.entryTimer === 0")
    assert page.evaluate("audioPlays.length") == 1
    full_cycle = duration if kind == "eat" else duration + 700
    page.clock.run_for(full_cycle - 1)
    assert page.evaluate(f"{state}.active")
    if kind == "eat":
        # Finishing the voice still cannot bypass the animation's final frame.
        page.evaluate(f"{state}.audio.dispatchEvent(new Event('ended'))")
        assert page.evaluate(f"{state}.active")
    page.clock.run_for(2)
    assert page.evaluate(f"!{state}.active && !{state}.timer && !{state}.entryTimer && !{state}.sourceLoadCleanup")
    assert page.evaluate("session.active && !session.released && session.bodies.get('cat') === catBody")
    assert page.evaluate("_isNekoIdleAnimationRestartUrlForSource(art.src, _getNekoIdleReturnCurrentArtUrl(cat))")


@pytest.mark.parametrize("kind", ("eat", "stretch"))
def test_eat_and_stretch_image_failure_releases_action_without_starting_sound(
    stationary_action_page: Page, kind
):
    page = stationary_action_page
    _enter(page, "gravity", audio=True)
    pattern = "**/cat-idle-cat1-eat*.webp*" if kind == "eat" else "**/cat-idle-cat4-2.gif*"
    page.route(pattern, lambda route: route.abort())
    state = "cat.__nekoIdleCat1EatActionState" if kind == "eat" else "cat.__nekoIdleCat1StretchActionState"
    assert page.evaluate("_playNekoIdleCat1EatAction(cat)" if kind == "eat" else "_requestNekoIdleCat1HissStretchPresentation()")
    page.wait_for_function(f"!{state}.active", polling=10)
    assert page.evaluate(f"{state}.timer === 0 && {state}.entryTimer === 0 && !{state}.sourceLoadCleanup")
    assert page.evaluate("audioPlays.length === 0 && session.active && !session.released && session.bodies.get('cat') === catBody")


@pytest.mark.parametrize("kind", ("stretch", "visual"))
def test_releasing_animated_entry_cannot_commit_its_late_preload(
    stationary_action_page: Page, kind
):
    page = stationary_action_page
    _enter(page, "gravity", audio=True)
    page.emulate_media(reduced_motion="no-preference")
    page.clock.pause_at(page.evaluate("Date.now() + 1"))
    page.evaluate("""() => {
        window.stationaryEntryHeld = new Promise(resolve => { window.resolveStationaryEntryHeld = resolve; });
    }""")
    held = []

    def hold_preload(route):
        held.append(route)
        page.evaluate("resolveStationaryEntryHeld()")

    pattern = "**/cat-idle-cat4-2.gif?*entry=*" if kind == "stretch" else "**/cat-idle-cat1-banana.webp?*entry=*"
    page.route(pattern, hold_preload)
    assert page.evaluate(
        "_requestNekoIdleCat1HissStretchPresentation()" if kind == "stretch"
        else "_playNekoIdleCat1VisualAction(cat, 'banana', {animateEntry: true})"
    )
    page.evaluate("stationaryEntryHeld")
    assert held
    assert page.evaluate("art.__nekoIdleTransitionTo && art.__nekoIdleTransitionTimer && art.__nekoIdleTransitionNext")
    original_src = page.evaluate("art.src")
    page.evaluate("""() => {
        window.oldEntryPreload = art.__nekoIdleTransitionPreload;
        _releaseAllNekoIdleCat1PlaygroundDropLifecycles('pagehide', {restorePresentation: false});
    }""")
    assert page.evaluate("session.released && !session.active && !_isNekoIdleCat1StretchActionActive(cat) && !readVisual().active")
    assert page.evaluate("!art.__nekoIdleTransitionTo && !art.__nekoIdleTransitionTimer && !art.__nekoIdleTransitionNext")
    for route in held:
        asset = PROJECT_ROOT / route.request.url.split("http://neko.test/", 1)[1].split("?", 1)[0]
        route.fulfill(path=str(asset))
    page.evaluate("""() => oldEntryPreload.complete ? undefined : new Promise(resolve => {
        oldEntryPreload.addEventListener('load', resolve, {once: true});
    })""")
    page.clock.run_for(1000)
    assert page.evaluate("art.src") == original_src
    assert page.evaluate("audioPlays.length === 0 && !_isNekoIdleCat1StretchActionActive(cat) && !readVisual().active")
    assert page.locator(".neko-idle-return-art-next").count() == 0


@pytest.mark.parametrize("motion", ("reduce", "no-preference"))
@pytest.mark.parametrize("outcome", ("loaded", "error", "timeout", "cancel", "exit"))
def test_visual_animated_entry_waits_for_image_before_starting_its_cycle(
    stationary_action_page: Page, motion, outcome
):
    page = stationary_action_page
    _enter(page, "gravity")
    page.emulate_media(reduced_motion=motion)
    page.clock.pause_at(page.evaluate("Date.now() + 1"))
    page.evaluate("""() => {
        window.entryStarts = [];
        window.entryTerminals = [];
        window.entryRequestHeld = new Promise(resolve => { window.resolveEntryRequestHeld = resolve; });
    }""")
    held = []

    def hold_entry_image(route):
        held.append(route)
        page.evaluate("resolveEntryRequestHeld()")

    page.route("**/cat-idle-cat1-scared.webp?*entry=*", hold_entry_image)
    assert page.evaluate("""() => NekoCatIdlePresentation.requestCat1Scared({
        button: cat, animateEntry: true,
        onPlaybackStarted: () => entryStarts.push({complete: art.complete, width: art.naturalWidth}),
        onTerminal: result => entryTerminals.push(result)
    })""")
    page.evaluate("entryRequestHeld")
    assert held
    page.clock.run_for(2000)
    assert page.evaluate("entryStarts.length === 0 && entryTerminals.length === 0 && readVisual().active && readVisual().timer === 0")
    if motion == "reduce":
        assert page.evaluate("!art.complete"), "reduced motion writes the main src before its response arrives"

    if outcome == "error":
        for route in held:
            route.abort()
    else:
        if outcome == "timeout":
            page.clock.run_for(3100)
        elif outcome == "cancel":
            page.evaluate("_cancelNekoIdleCat1VisualAction(cat, {reason: 'return-click'})")
        elif outcome == "exit":
            page.evaluate("nekoIdleCat1Playground.setGravityCatEnabled(false)")
        for route in held:
            asset = PROJECT_ROOT / route.request.url.split("http://neko.test/", 1)[1].split("?", 1)[0]
            route.fulfill(path=str(asset))

    if outcome == "loaded":
        page.wait_for_function("entryStarts.length === 1", polling=10)
        assert page.evaluate("entryStarts[0].complete && entryStarts[0].width > 0")
        assert page.evaluate("readVisual().entryTimer === 0 && !cat.__nekoIdleCat1VisualActionState.sourceLoadCleanup")
        duration = page.evaluate("_getNekoIdleAnimationDurationMs(_getNekoIdleCat1VisualActionAssetUrl(cat))")
        page.clock.run_for(duration - 1)
        assert page.evaluate("readVisual().active && entryTerminals.length === 0")
        page.clock.run_for(1)
        _assert_visual_cleared(page)
        assert page.evaluate("entryTerminals") == ["done"]
    else:
        page.wait_for_function("entryTerminals.length === 1", polling=10)
        assert page.evaluate("entryStarts.length === 0 && entryTerminals[0] !== 'done'")
        page.evaluate("art.dispatchEvent(new Event('load'))")
        page.clock.run_for(8000)
        _assert_visual_cleared(page)
        assert page.evaluate("entryStarts.length === 0 && entryTerminals.length === 1 && audioPlays.length === 0")
    assert page.evaluate("session.released && !session.active" if outcome == "exit" else "session.active && !session.released")


@pytest.mark.parametrize("owner", ("eat", "stretch", "visual"))
def test_release_terminal_callbacks_reject_stationary_reentry_until_off_completes(
    stationary_action_page: Page, owner
):
    page = stationary_action_page
    _enter(page, "gravity", audio=True)
    page.evaluate("""owner => {
        window.releaseAttempts = [];
        window.releaseTerminals = [];
        window.releaseCallbackStates = [];
        const attemptReentry = () => {
            releaseTerminals.push(owner);
            releaseCallbackStates.push({active: session.active, released: session.released});
            const options = {source: 'cat_mind', requestId: 'must-not-reenter-release'};
            releaseAttempts.push(
                _playNekoIdleCat1EatAction(cat, options),
                _playNekoIdleCat1StretchAction(cat),
                _playNekoIdleCat1VisualAction(cat, 'banana'),
                NekoCatIdlePresentation.requestCat1HissStretch(),
                NekoCatIdlePresentation.requestCat1Banana(),
                _playNekoCatMindIdleRandomVisualAction(cat, options),
                _playNekoCatMindInactiveSadAction(cat, options)
            );
        };
        if (owner === 'eat') {
            window.addEventListener(_getNekoCatMindActionResultEventName(), event => {
                if (event.detail.detail.requestId === 'release-owner') attemptReentry();
            });
            window.releaseOwnerStarted = _playNekoIdleCat1EatAction(cat,
                {source: 'cat_mind', requestId: 'release-owner'});
        } else if (owner === 'stretch') {
            window.releaseOwnerStarted = _playNekoIdleCat1StretchAction(cat, {
                onTerminal: attemptReentry,
                onPlaybackStarted: () => _playNekoIdleSound(_getNekoIdleCat1StretchActionState(cat),
                    _getNekoCatVoiceUrl('cat1.hiss'), 0.12)
            });
        } else {
            window.releaseOwnerStarted = _playNekoIdleCat1VisualAction(cat, 'oiia',
                {onTerminal: attemptReentry});
        }
    }""", owner)
    assert page.evaluate("releaseOwnerStarted")
    state = {
        "eat": "cat.__nekoIdleCat1EatActionState",
        "stretch": "cat.__nekoIdleCat1StretchActionState",
        "visual": "cat.__nekoIdleCat1VisualActionState",
    }[owner]
    page.wait_for_function(f"{state}.timer > 0", polling=10)
    if owner == "visual":
        page.clock.run_for(530)
    page.evaluate(f"window.releaseOldAudio = {state}.audio")
    assert page.evaluate("releaseOldAudio && !releaseOldAudio.paused && audioPlays.length === 1")
    page.evaluate("nekoIdleCat1Playground.setGravityCatEnabled(false)")
    assert page.evaluate("releaseCallbackStates") == [{"active": True, "released": True}]
    assert page.evaluate("releaseAttempts") == [False] * 7
    assert page.evaluate("releaseTerminals") == [owner]
    assert page.evaluate("!session.active && session.released && releaseOldAudio.paused")
    page.evaluate("""() => {
        releaseOldAudio.dispatchEvent(new Event('ended'));
        releaseOldAudio.dispatchEvent(new Event('error'));
        art.dispatchEvent(new Event('load'));
    }""")
    page.clock.run_for(30000)
    assert page.evaluate("""() => [
        _getNekoIdleCat1EatActionState(cat),
        _getNekoIdleCat1StretchActionState(cat),
        _getNekoIdleCat1VisualActionState(cat)
    ].every(state => !state.active && !state.timer && !state.entryTimer &&
        !state.audioTimer && !state.audioStartTimer && !state.sourceLoadCleanup && !state.audio)""")
    assert page.evaluate("releaseTerminals") == [owner]
    assert page.evaluate("audioPlays.length") == 1

    # Positive controls: OFF has finished, so ordinary public interactions work.
    assert page.evaluate("""() => {
        window.ordinaryReleaseResults = [];
        return NekoCatIdlePresentation.requestCat1Banana({
            onTerminal: result => ordinaryReleaseResults.push(result)
        });
    }""")
    page.wait_for_function("readVisual().timer > 0", polling=10)
    page.clock.run_for(5060)
    assert page.evaluate("ordinaryReleaseResults") == ["done"]
    assert page.evaluate("!readVisual().active && !session.active")
    assert page.evaluate("NekoCatIdlePresentation.requestCat1HissStretch()")
    page.wait_for_function("cat.__nekoIdleCat1StretchActionState.timer > 0", polling=10)
    assert page.evaluate("""() => {
        window.ordinaryReleaseHiss = cat.__nekoIdleCat1StretchActionState.audio;
        return ordinaryReleaseHiss && !ordinaryReleaseHiss.paused;
    }""")
    duration = page.evaluate("_getNekoIdleAnimationDurationMs(_getNekoIdleCat1StretchAssetUrl())")
    page.clock.run_for(duration + 750)
    assert page.evaluate("!cat.__nekoIdleCat1StretchActionState.active && ordinaryReleaseHiss.paused && !session.active")



@pytest.mark.parametrize("tier,key", (
    ("cat2", "cat2Back1_1"),
    ("cat3", "cat3Back1_1"),
    ("cat3", "cat3Back1_2"),
))
def test_sleep_back_finishes_through_real_tier_controller_without_leaving_gravity(
    stationary_action_page: Page, tier, key
):
    page = stationary_action_page
    page.evaluate("""() => {
        window.live2dManager = manager;
        manager._goodbyeClicked = true;
        container.setAttribute('data-neko-return-visible', 'true');
        window.__appUiParts = {
            normalizeNekoGoodbyeIdleAppearance: value => value === 'ball' ? 'ball' : 'cat',
            getVisibleIdleReturnBallContainer: () => container,
            NEKO_GOODBYE_IDLE_APPEARANCE_BALL: 'ball',
            NEKO_GOODBYE_IDLE_APPEARANCE_ATTR: 'data-neko-goodbye-idle-appearance'
        };
    }""")
    page.add_script_tag(path=str(PROJECT_ROOT / "static/app/app-auto-goodbye.js"))
    page.wait_for_function("nekoAutoGoodbye.getState().started", polling=10)
    page.evaluate("""tier => {
        nekoAutoGoodbye.clearTimers('back-action-browser-test');
        if (!enterPlayground()) throw Error('entry failed');
        nekoAutoGoodbye.setVisualTier(tier, {source: 'back-action-browser-test'});
        window.backSession = session;
        window.backBody = catBody;
        window.backTerminals = [];
    }""", tier)
    _settle(page)
    page.clock.pause_at(page.evaluate("Date.now() + 1"))
    assert page.evaluate("""key => {
        const profile = _getNekoIdleCat1VisualActionProfile(key);
        return NekoCatIdlePresentation[profile.requestName]({
            button: cat, onTerminal: result => backTerminals.push(result)
        });
    }""", key)
    page.wait_for_function("readVisual().timer !== 0", polling=10)
    duration = page.evaluate("_getNekoIdleAnimationDurationMs(_getNekoIdleCat1VisualActionAssetUrl(cat))")
    page.clock.run_for(duration - 1)
    assert page.evaluate("readVisual().active && backTerminals.length === 0")
    assert page.evaluate("nekoAutoGoodbye.getState().visualTier") == tier
    page.clock.run_for(1)
    _assert_visual_cleared(page)
    assert page.evaluate("backTerminals") == ["done"]
    assert page.evaluate("cat.getAttribute('data-neko-idle-tier')") == "cat1"
    assert page.evaluate("nekoAutoGoodbye.getState().visualTier") == "cat1"
    assert page.evaluate("session === backSession && session.active && !session.released && session.bodies.get('cat') === backBody")
