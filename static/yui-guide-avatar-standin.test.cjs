const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const { readDirectorSource } = require('./yui-guide-director-test-parts.cjs');
const test = require('node:test');
const vm = require('node:vm');
const { readJsParts } = require('./app-part-test-utils.cjs');

const standIn = require('./tutorial/avatar/yui-standin.js');
const standInController = require('./tutorial/avatar/standin-controller.js');
const directorSource = readDirectorSource(__dirname);
const avatarStageSource = fs.readFileSync(path.join(__dirname, 'tutorial/avatar/yui-stage.js'), 'utf8');
const controllerSource = fs.readFileSync(path.join(__dirname, 'tutorial/avatar/standin-controller.js'), 'utf8');
const overlaySource = fs.readFileSync(path.join(__dirname, 'tutorial/yui-guide/overlay.js'), 'utf8');
const live2dInteractionSource = fs.readFileSync(path.join(__dirname, 'live2d', 'live2d-interaction.js'), 'utf8');
const live2dInitSource = fs.readFileSync(path.join(__dirname, 'live2d', 'live2d-init.js'), 'utf8');
const live2dButtonsSource = fs.readFileSync(path.join(__dirname, 'live2d', 'live2d-ui-buttons.js'), 'utf8');
const modelDisplaySource = fs.readFileSync(path.join(__dirname, 'app/app-ui/model-display.js'), 'utf8');
const appUiSource = readJsParts(path.join(__dirname, 'app/app-ui'));
const appInterpageSource = readJsParts(path.join(__dirname, 'app/app-interpage'));
const universalManagerSource = fs.readFileSync(path.join(__dirname, 'tutorial/core/universal-manager.js'), 'utf8');
const wakeupSource = fs.readFileSync(path.join(__dirname, 'tutorial/yui-guide/wakeup.js'), 'utf8');

function loadAvatarStageContext(options) {
    const normalizedOptions = options || {};
    const elements = normalizedOptions.elements || {};
    let now = Number.isFinite(Number(normalizedOptions.now)) ? Number(normalizedOptions.now) : 0;
    const window = {
        innerWidth: 1024,
        innerHeight: 768,
        requestAnimationFrame() {
            return 0;
        },
        cancelAnimationFrame() {},
        __setNow(value) {
            now = Number(value) || 0;
        }
    };
    if (typeof normalizedOptions.configureWindow === 'function') {
        normalizedOptions.configureWindow(window);
    }
    const context = vm.createContext({
        window,
        document: {
            getElementById(id) {
                return elements[id] || null;
            },
            querySelector() {
                return null;
            }
        },
        performance: {
            now() {
                return now;
            }
        },
        console
    });
    vm.runInContext(avatarStageSource, context, {
        filename: path.join(__dirname, 'tutorial/avatar/yui-stage.js')
    });
    return {
        api: window.YuiGuideAvatarStage,
        window
    };
}

function loadAvatarStageApi() {
    return loadAvatarStageContext().api;
}

function createCornerPeekSession(position, options) {
    const normalizedOptions = options || {};
    const context = loadAvatarStageContext(normalizedOptions);
    const api = context.api;
    const coreModel = normalizedOptions.coreModel || {};
    const model = {
        x: 512,
        y: 384,
        rotation: 0,
        alpha: 0.8,
        destroyed: false,
        scale: {
            x: 1,
            y: 1,
            set(x, y) {
                this.x = x;
                this.y = y;
            }
        },
        internalModel: {
            coreModel
        },
        getBounds() {
            return {
                x: 362,
                y: 84,
                width: 300,
                height: 600
            };
        }
    };
    const manager = {
        currentModel: model,
        pixi_app: {
            renderer: {
                screen: {
                    width: 1024,
                    height: 768
                }
            }
        }
    };
    const session = new api.Live2DAvatarCornerPeekSession({
        manager,
        model,
        coreModel
    }, {
        position,
        isCancelled: normalizedOptions.isCancelled,
        onPeekReady: normalizedOptions.onPeekReady,
        startFromHidden: normalizedOptions.startFromHidden,
        hideMs: normalizedOptions.hideMs,
        appearMs: normalizedOptions.appearMs,
        holdMs: normalizedOptions.holdMs,
        restoreMode: normalizedOptions.restoreMode,
        useCompositorOpacity: normalizedOptions.useCompositorOpacity,
        frameScale: normalizedOptions.frameScale,
        frameY: normalizedOptions.frameY,
        container: normalizedOptions.container || {
            style: {}
        }
    });
    session.initialModelFrame = {
        x: model.x,
        y: model.y,
        scaleX: 1,
        scaleY: 1,
        rotation: 0
    };
    session.initialAlpha = model.alpha;
    session.restoreModelTargetFrame = session.initialModelFrame;
    session.restoreAlpha = session.initialAlpha;
    session.initialBounds = model.getBounds();
    session.hiddenFrame = session.resolveHiddenFrame();
    session.peekRegionBounds = session.resolvePeekRegionBounds();
    session.cornerFrame = session.resolveCornerFrame();
    session.cornerHiddenFrame = session.resolveCornerHiddenFrame();
    return {
        session,
        model,
        window: context.window
    };
}

function createFrameMotionSession(options) {
    const normalizedOptions = options || {};
    const container = normalizedOptions.container || { style: {} };
    const canvas = normalizedOptions.canvas || { style: {} };
    const context = loadAvatarStageContext({
        elements: {
            'live2d-container': container,
            'live2d-canvas': canvas
        },
        configureWindow: normalizedOptions.configureWindow
    });
    const coreModel = {};
    const model = {
        x: 512,
        y: 384,
        rotation: 0,
        alpha: 0.8,
        destroyed: false,
        scale: {
            x: 1,
            y: 1,
            set(x, y) {
                this.x = x;
                this.y = y;
            }
        },
        internalModel: {
            coreModel
        },
        getBounds() {
            return {
                x: 362,
                y: 84,
                width: 300,
                height: 600
            };
        }
    };
    const manager = {
        currentModel: model,
        pixi_app: {
            renderer: {
                screen: {
                    width: 1024,
                    height: 768
                }
            }
        }
    };
    context.window.live2dManager = manager;
    const session = new context.api.Live2DFrameMotionSession({
        manager,
        model,
        coreModel
    }, {
        container,
        from: 'offscreen-bottom',
        to: 'close-up',
        enterMs: 2000,
        holdMs: 2500,
        motionFromAlpha: 0,
        motionToAlpha: 1,
        restoreMode: 'half-body',
        isCancelled: normalizedOptions.isCancelled,
        useCompositorOpacity: true
    });
    return {
        api: context.api,
        session,
        model,
        container,
        canvas,
        window: context.window
    };
}

function createControlledNonOpeningFramePlayback() {
    const frameCallbacks = [];
    let cancelled = false;
    const context = createFrameMotionSession({
        container: { style: { opacity: '1', transition: 'opacity 900ms ease' } },
        canvas: { style: { opacity: '1', transition: 'opacity 900ms ease' } },
        configureWindow(targetWindow) {
            targetWindow.requestAnimationFrame = (callback) => {
                frameCallbacks.push(callback);
                return frameCallbacks.length;
            };
            targetWindow.cancelAnimationFrame = () => {};
        }
    });
    const playback = context.api.playAvatarMotion({
        preset: 'bottom-rise',
        narrationBudgeted: true,
        isOpeningProbe: false,
        isCancelled: () => cancelled
    });

    return Object.assign({}, context, {
        playback,
        cancel() {
            cancelled = true;
        },
        async flushFrameAt(now) {
            await Promise.resolve();
            await Promise.resolve();
            context.window.__setNow(now);
            frameCallbacks.splice(0).forEach((callback) => callback());
            await Promise.resolve();
            await Promise.resolve();
        }
    });
}

function createHeadAnchoredCornerPeekSession(position) {
    const api = loadAvatarStageApi();
    const coreModel = {};
    const model = {
        x: 512,
        y: 384,
        rotation: 0,
        alpha: 0.8,
        destroyed: false,
        scale: {
            x: 1,
            y: 1,
            set(x, y) {
                this.x = x;
                this.y = y;
            }
        },
        internalModel: {
            coreModel
        },
        getBounds() {
            return {
                x: 362,
                y: 84,
                width: 300,
                height: 600
            };
        }
    };
    const headRect = {
        left: 438,
        top: 104,
        right: 586,
        bottom: 252,
        width: 148,
        height: 148,
        centerX: 512,
        centerY: 178
    };
    const bodyRect = {
        left: 414,
        top: 250,
        right: 610,
        bottom: 560,
        width: 196,
        height: 310,
        centerX: 512,
        centerY: 405
    };
    const manager = {
        currentModel: model,
        getHeadScreenRectInfo() {
            return { rect: headRect };
        },
        getBodyScreenRectInfo() {
            return { rect: bodyRect };
        },
        pixi_app: {
            renderer: {
                screen: {
                    width: 1024,
                    height: 768
                }
            }
        }
    };
    const session = new api.Live2DAvatarCornerPeekSession({
        manager,
        model,
        coreModel
    }, {
        position,
        container: {
            style: {}
        }
    });
    session.initialModelFrame = {
        x: model.x,
        y: model.y,
        scaleX: 1,
        scaleY: 1,
        rotation: 0
    };
    session.initialAlpha = model.alpha;
    session.initialBounds = model.getBounds();
    session.hiddenFrame = session.resolveHiddenFrame();
    session.peekRegionBounds = session.resolvePeekRegionBounds();
    session.cornerFrame = session.resolveCornerFrame();
    session.cornerHiddenFrame = session.resolveCornerHiddenFrame();
    return {
        session,
        model,
        headRect,
        bodyRect
    };
}

test('returns fixed Live2D corner peek cues without image resources', () => {
    assert.equal(standIn.getCue(2, 'day2_tool_toggle_intro'), null);
    assert.equal(
        standIn.getCue(2, 'day2_avatar_tools'),
        null,
        'day2_avatar_tools stays disabled because it sits too close to the opening motion'
    );
    assert.deepEqual(standIn.getCue(2, 'day2_galgame_entry'), {
        delay: 900,
        duration: 5000,
        position: 'top-right'
    });
    assert.deepEqual(standIn.getCue(3, 'day3_proactive_chat'), {
        delay: 900,
        duration: 5000,
        position: 'top-left'
    });
    assert.deepEqual(standIn.getCue(4, 'day4_privacy_mode'), {
        delay: 900,
        duration: 5000,
        position: 'bottom-right'
    });
    assert.equal(standIn.getCue(5, 'day5_character_settings'), null);
    assert.equal(standIn.getCue(7, 'day7_memory_review'), null);
    assert.equal(standIn.getCue(7, 'day7_wrap'), null);
    assert.equal(typeof standIn.getResourcePath, 'undefined');
});

test('exports all fixed day two through seven cue positions', () => {
    const cues = standIn.getAllCues();
    const allowedPositions = new Set(['bottom-right', 'bottom-left', 'top-right', 'top-left']);
    const expectedCueCounts = {
        2: 1,
        3: 1,
        4: 2,
        5: 0,
        6: 2,
        7: 0
    };

    assert.equal(Object.keys(cues).length, 6);
    for (const day of [2, 3, 4, 5, 6, 7]) {
        assert.equal(Object.keys(cues[day]).length, expectedCueCounts[day]);
        Object.values(cues[day]).forEach((cue) => {
            assert.equal(cue.duration, 5000);
            assert.equal(Object.prototype.hasOwnProperty.call(cue, 'durationMs'), false);
            assert.equal(Object.prototype.hasOwnProperty.call(cue, 'delayMs'), false);
            assert.equal(allowedPositions.has(cue.position), true);
            assert.equal(Object.prototype.hasOwnProperty.call(cue, 'resource'), false);
        });
    }
});

test('does not schedule Live2D corner peek on final wrap-adjacent scenes', () => {
    assert.equal(standIn.getCue(2, 'day2_tool_toggle_intro'), null);
    assert.equal(
        standIn.getCue(2, 'day2_avatar_tools'),
        null,
        'day2_avatar_tools intentionally remains outside the legacy stand-in cue table'
    );
    assert.equal(standIn.getCue(2, 'day2_galgame_choices'), null);
    assert.equal(standIn.getCue(4, 'day4_return_home'), null);
    assert.equal(standIn.getCue(5, 'day5_character_settings'), null);
    assert.equal(standIn.getCue(5, 'day5_memory_entry'), null);
    assert.equal(standIn.getCue(6, 'day6_wrap_cleanup'), null);
    assert.equal(standIn.getCue(7, 'day7_memory_review'), null);
    assert.equal(standIn.getCue(7, 'day7_memory_control'), null);
    assert.equal(standIn.getCue(7, 'day7_graduation_wrap'), null);
});

test('director routes avatar stand-ins through Live2D corner peek, not overlay images', () => {
    assert.match(controllerSource, /class AvatarStandInController/);
    assert.match(directorSource, /this\.avatarStandInController = new TutorialVisualControllers\.AvatarStandInController\(this\);/);
    assert.match(directorSource, /this\.startAvatarCornerPeekPerformance\({[\s\S]*position: cue\.position/);
    assert.match(controllerSource, /Number\.isFinite\(Number\(cue\.delay\)\)/);
    assert.match(directorSource, /Number\.isFinite\(Number\(cue\.duration\)\)/);
    assert.match(directorSource, /startAvatarCornerPeek[\s\S]*useCompositorOpacity: true/);
    assert.match(directorSource, /await this\.stopAvatarCornerPeekPerformance\(handle,\s*reason \|\| 'avatar_standin_clear'\);/);
    assert.doesNotMatch(directorSource, /overlay\.showAvatarStandIn/);
    assert.doesNotMatch(overlaySource, /showAvatarStandIn/);
    assert.doesNotMatch(overlaySource, /avatarStandIn:\s*\{/);
});

test('avatar stage exposes generic Live2D corner peek while keeping plugin-dashboard entry', () => {
    assert.match(avatarStageSource, /class Live2DAvatarCornerPeekSession/);
    assert.match(avatarStageSource, /async function startAvatarCornerPeek\(options\)/);
    assert.match(avatarStageSource, /startAvatarCornerPeek: startAvatarCornerPeek/);
    assert.match(avatarStageSource, /startPluginDashboardCornerPeek: startPluginDashboardCornerPeek/);
    assert.match(avatarStageSource, /Live2DPluginDashboardCornerSession: Live2DAvatarCornerPeekSession/);
});

test('avatar stage exposes reusable motion core and preset playback entry', () => {
    assert.match(avatarStageSource, /class Live2DMotionBaseSession/);
    assert.match(avatarStageSource, /class Live2DFrameMotionSession extends Live2DMotionBaseSession/);
    assert.match(avatarStageSource, /async function playAvatarMotion\(options\)/);
    assert.match(avatarStageSource, /playAvatarMotion: playAvatarMotion/);
    assert.match(avatarStageSource, /Live2DMotionBaseSession: Live2DMotionBaseSession/);
    assert.match(avatarStageSource, /Live2DFrameMotionSession: Live2DFrameMotionSession/);
});

test('day two through seven avatar probes keep fixed phases and move short-line cues earlier', () => {
    assert.match(avatarStageSource, /const TUTORIAL_AVATAR_PROBE_FADE_OUT_MS = 1500;/);
    assert.match(avatarStageSource, /const TUTORIAL_AVATAR_PROBE_APPROACH_MS = 2000;/);
    assert.match(avatarStageSource, /const TUTORIAL_AVATAR_PROBE_HOLD_MS = 2500;/);
    assert.match(avatarStageSource, /TUTORIAL_AVATAR_PROBE_TIMING: TUTORIAL_AVATAR_PROBE_TIMING/);
    assert.match(controllerSource, /root && root\.YuiGuideAvatarStage/);
    assert.match(avatarStageSource, /startFromHidden: isOpeningProbe/);
    assert.match(avatarStageSource, /holdMs: TUTORIAL_AVATAR_PROBE_HOLD_MS/);
    assert.match(directorSource, /isOpeningProbe: tutorialDay >= 2[\s\S]*resolveOnReveal/);

    const resolvedCue = standInController.resolveCueTiming(
        { delay: 900, duration: 5000, position: 'top-right' },
        { voiceKey: 'day2_galgame_entry', text: 'test' },
        { getAvatarFloatingNarrationDurationMs: () => 4500 }
    );
    assert.equal(resolvedCue.delay, 0);
    assert.equal(resolvedCue.hideMs, 1500);
    assert.equal(resolvedCue.appearMs, 2000);
    assert.equal(resolvedCue.holdMs, 2500);
    assert.equal(resolvedCue.totalDurationMs, 6000);
    assert.equal(resolvedCue.fullDurationMs, 9500);

    const longLineCue = standInController.resolveCueTiming(
        { delay: 900, duration: 5000, position: 'top-right' },
        { voiceKey: 'day2_galgame_entry', text: 'test' },
        { getAvatarFloatingNarrationDurationMs: () => 12000 }
    );
    assert.equal(longLineCue.delay, 900);

    const previousAvatarStage = globalThis.YuiGuideAvatarStage;
    globalThis.YuiGuideAvatarStage = {
        TUTORIAL_AVATAR_PROBE_TIMING: {
            fadeOutMs: 1400,
            approachMs: 1900,
            holdMs: 2400,
            returnMs: 1800
        }
    };
    try {
        const sharedTimingCue = standInController.resolveCueTiming(
            { delay: 0, position: 'top-right' },
            { voiceKey: 'day2_galgame_entry', text: 'test' },
            { getAvatarFloatingNarrationDurationMs: () => 10000 }
        );
        assert.equal(sharedTimingCue.hideMs, 1400);
        assert.equal(sharedTimingCue.appearMs, 1900);
        assert.equal(sharedTimingCue.holdMs, 2400);
        assert.equal(sharedTimingCue.fullDurationMs, 8900);
    } finally {
        if (typeof previousAvatarStage === 'undefined') {
            delete globalThis.YuiGuideAvatarStage;
        } else {
            globalThis.YuiGuideAvatarStage = previousAvatarStage;
        }
    }
});

test('bottom-rise intro avatar motion approaches and holds the first-day half-body frame', () => {
    assert.match(avatarStageSource, /from:\s*'offscreen-bottom'/);
    assert.match(avatarStageSource, /to:\s*'close-up'/);
    assert.match(avatarStageSource, /frameScale[\s\S]*:\s*INTRO_GREETING_HUG_CLOSE_SCALE/);
    assert.match(avatarStageSource, /frameY[\s\S]*resolveIntroGreetingHugFrameShift\(this\.container\)/);
    assert.match(avatarStageSource, /frameY[\s\S]*:\s*undefined/);
    assert.match(avatarStageSource, /restoreMode[\s\S]*normalizedOptions\.restore[\s\S]*\|\|\s*'half-body'/);
    assert.match(avatarStageSource, /this\.restoreMode === 'half-body'[\s\S]*this\.applyFrame\(this\.toFrame, this\.toAlpha\)/);
    assert.match(avatarStageSource, /initialAlphaOverride/);
    assert.match(avatarStageSource, /motionFromAlpha/);
    assert.match(avatarStageSource, /const originalFrame = readIntroGreetingHugModelFrame\(context\.model\)/);
    assert.match(avatarStageSource, /baseFrame: originalFrame \|\| session\.initialModelFrame/);
});

test('corner and top peek intro avatar motions settle on the first-day half-body frame', () => {
    assert.match(avatarStageSource, /function applyAvatarMotionHalfBodyPlacement\(options\)/);
    assert.match(avatarStageSource, /INTRO_GREETING_HUG_CLOSE_SCALE/);
    assert.match(avatarStageSource, /const container = getLive2DContainer/);
    assert.match(avatarStageSource, /resolveIntroGreetingHugFrameShift\(container\)/);
    assert.match(avatarStageSource, /restoreMode: normalizedOptions\.restore \|\| normalizedOptions\.restoreMode \|\| 'half-body'/);
    assert.match(avatarStageSource, /await handle\.stop\('avatar_motion_complete'\);/);
    assert.match(avatarStageSource, /applyAvatarMotionHalfBodyPlacement\(normalizedOptions\)/);
});

test('corner peek keeps floating buttons frozen by default but intro motion can opt out', () => {
    assert.match(avatarStageSource, /this\.freezeFloatingButtons = normalizedOptions\.freezeFloatingButtons !== false;/);
    assert.match(avatarStageSource, /if \(this\.freezeFloatingButtons\) \{[\s\S]*this\.freezeFloatingButtonsPosition\(\);[\s\S]*\}/);
    assert.match(avatarStageSource, /freezeFloatingButtons: normalizedOptions\.freezeFloatingButtons/);
    assert.match(avatarStageSource, /function showAvatarMotionFloatingButtons\(options\)/);
    assert.match(avatarStageSource, /showAvatarMotionFloatingButtons\(normalizedOptions\)/);
    assert.match(directorSource, /freezeFloatingButtons: performance\.freezeFloatingButtons === false \? false : undefined/);
});

test('corner peek can rotate floating buttons only when intro motion opts in', () => {
    assert.match(avatarStageSource, /this\.rotateFloatingButtons = normalizedOptions\.rotateFloatingButtons === true;/);
    assert.match(avatarStageSource, /this\.syncFloatingButtonsRotation\(frame\)/);
    assert.match(avatarStageSource, /manager\._floatingButtonsRotationRadians = rotation;/);
    assert.match(live2dButtonsSource, /const rotation = Number\(this\._floatingButtonsRotationRadians\) \|\| 0;/);
    assert.match(live2dButtonsSource, /const nextTransform = `scale\(\$\{scale\}\)\$\{rotateTransform\}`;/);
    assert.match(live2dButtonsSource, /buttonsContainer\.style\.transform = nextTransform;/);
    assert.match(directorSource, /rotateFloatingButtons: performance\.rotateFloatingButtons === true/);
});

test('corner and top peek intro avatar motions fade in the half-body handoff', () => {
    assert.match(avatarStageSource, /const AVATAR_MOTION_HALF_BODY_FADE_IN_MS = 900;/);
    assert.match(avatarStageSource, /const AVATAR_MOTION_HALF_BODY_FADE_OUT_MS = 420;/);
    assert.match(avatarStageSource, /function collectAvatarMotionVisibleOpacityTargets\(context, options\)/);
    assert.match(avatarStageSource, /function writeAvatarMotionVisibleOpacity\(context, targets, modelAlpha, displayAlpha\)/);
    assert.match(avatarStageSource, /async function fadeOutAvatarMotionVisibleLayer\(options\)/);
    assert.match(avatarStageSource, /async function fadeInAvatarMotionHalfBodyPlacement\(options\)/);
    assert.match(avatarStageSource, /const targetAlpha = 1;/);
    assert.match(avatarStageSource, /const targetDisplayAlpha = 1;/);
    assert.match(avatarStageSource, /await fadeOutAvatarMotionVisibleLayer\(/);
    assert.match(avatarStageSource, /writeAvatarMotionVisibleOpacity\(context, targets, 0, 0\)/);
    assert.match(avatarStageSource, /writeAvatarMotionVisibleOpacity\(context, targets, lerp\(0, targetAlpha, eased\), displayAlpha\)/);
    assert.match(avatarStageSource, /await fadeInAvatarMotionHalfBodyPlacement\(/);
});

test('soft approach intro avatar motion uses the first-day half-body scale', () => {
    assert.doesNotMatch(avatarStageSource, /preset === 'soft-approach'\s*\?\s*1\.08/);
    assert.match(avatarStageSource, /const frameScale = INTRO_GREETING_HUG_CLOSE_SCALE;/);
});

test('Live2D corner peek keeps center model still while fading and uses one second phases', () => {
    const { session, model } = createCornerPeekSession('bottom-right');

    assert.equal(session.hideMs, 1000);
    assert.equal(session.appearMs, 1000);
    assert.equal(session.totalDurationMs, 2000);
    assert.equal(session.exitDurationMs, 2000);

    session.tickEnter(500);
    assert.equal(model.x, 512);
    assert.equal(model.y, 384);
    assert.equal(model.alpha, 0.4);

    session.tickEnter(1500);
    assert.notEqual(model.x, 512);
    assert.notEqual(model.y, 384);
    assert.ok(model.alpha > 0);
    assert.ok(model.alpha < 1);

    session.tickExit(500);
    assert.equal(model.x, session.cornerFrame.x);
    assert.equal(model.y, session.cornerFrame.y);
    assert.equal(model.alpha, 0.5);

    session.tickExit(1500);
    assert.equal(model.x, 512);
    assert.equal(model.y, 384);
    assert.ok(model.alpha > 0);
    assert.ok(model.alpha < 0.8);
});

test('Live2D corner peek notifies reveal only after preparing the corner-hidden frame', () => {
    let readyFrame = null;
    let readyAlpha = null;
    const { session, model } = createCornerPeekSession('bottom-left', {
        onPeekReady: () => {
            readyFrame = {
                x: model.x,
                y: model.y,
                rotation: model.rotation
            };
            readyAlpha = model.alpha;
        }
    });

    assert.equal(session.start(), true);
    assert.equal(readyFrame, null);

    session.tickEnter(500);
    assert.equal(readyFrame, null);

    session.tickEnter(1001);
    assert.deepEqual(readyFrame, {
        x: session.cornerHiddenFrame.x,
        y: session.cornerHiddenFrame.y,
        rotation: session.cornerHiddenFrame.rotation
    });
    assert.equal(readyAlpha, 0);
});

test('Live2D corner peek can start from the screen-out frame while fully transparent', () => {
    let readyFrame = null;
    let readyAlpha = null;
    const { session, model } = createCornerPeekSession('bottom-left', {
        startFromHidden: true,
        onPeekReady: () => {
            readyFrame = {
                x: model.x,
                y: model.y,
                rotation: model.rotation
            };
            readyAlpha = model.alpha;
        }
    });

    assert.equal(session.start(), true);
    assert.equal(model.x, session.cornerHiddenFrame.x);
    assert.equal(model.y, session.cornerHiddenFrame.y);
    assert.equal(model.alpha, 0);

    session.tickEnter(0);
    assert.deepEqual(readyFrame, {
        x: session.cornerHiddenFrame.x,
        y: session.cornerHiddenFrame.y,
        rotation: session.cornerHiddenFrame.rotation
    });
    assert.equal(readyAlpha, 0);

    session.tickEnter(500);
    assert.notEqual(model.x, session.cornerHiddenFrame.x);
    assert.notEqual(model.y, session.cornerHiddenFrame.y);
    assert.ok(model.alpha > 0);
    assert.ok(model.alpha < 1);
});

test('Live2D opening corner peek uses one compositor layer and completes the full return fade', () => {
    const canvas = { style: { opacity: '0' } };
    const container = { style: { opacity: '0' } };
    const { session, model } = createCornerPeekSession('bottom-left', {
        startFromHidden: true,
        hideMs: 1500,
        appearMs: 2000,
        restoreMode: 'half-body',
        container,
        elements: {
            'live2d-canvas': canvas
        }
    });

    assert.equal(session.totalDurationMs, 2000);
    assert.equal(session.exitDurationMs, 3500);
    assert.equal(session.start(), true);
    container.style.transition = '';
    canvas.style.transition = '';

    session.tickEnter(1000);
    assert.equal(model.alpha, 1);
    assert.ok(Number(container.style.opacity) > 0);
    assert.ok(Number(container.style.opacity) < 1);
    assert.equal(canvas.style.opacity, '1');
    assert.equal(container.style.transition, 'none');
    assert.equal(canvas.style.transition, 'none');

    session.tickExit(750);
    assert.equal(model.alpha, 1);
    assert.ok(Number(container.style.opacity) > 0);
    assert.ok(Number(container.style.opacity) < 1);
    assert.equal(canvas.style.opacity, '1');

    session.tickExit(2000);
    assert.equal(session.finished, false);
    assert.equal(model.x, session.restoreModelTargetFrame.x);
    assert.equal(model.y, session.restoreModelTargetFrame.y);
    assert.equal(model.scale.x, session.initialModelFrame.scaleX * 1.38);
    assert.equal(model.scale.y, session.initialModelFrame.scaleY * 1.38);
    assert.equal(model.rotation, session.initialModelFrame.rotation);
    assert.equal(model.alpha, 1);
    assert.ok(Number(container.style.opacity) > 0);
    assert.ok(Number(container.style.opacity) < 1);
    assert.equal(canvas.style.opacity, '1');

    session.tickExit(3500);
    assert.equal(session.finished, false);
    assert.equal(model.alpha, session.restoreAlpha);

    session.tickExit(3501);
    assert.equal(session.finished, true);
    assert.equal(model.x, session.restoreModelTargetFrame.x);
    assert.equal(model.y, session.restoreModelTargetFrame.y);
    assert.equal(model.scale.x, session.initialModelFrame.scaleX * 1.38);
    assert.equal(model.scale.y, session.initialModelFrame.scaleY * 1.38);
    assert.equal(model.alpha, session.restoreAlpha);
    assert.equal(container.style.opacity, '1');
    assert.equal(canvas.style.opacity, '1');
});

test('Live2D cancelled hidden opening corner peek preserves the prepared opacity snapshot', () => {
    const canvas = { style: { opacity: '0', transition: 'opacity 900ms ease' } };
    const container = { style: { opacity: '0', transition: 'opacity 900ms ease' } };
    const { session } = createCornerPeekSession('bottom-left', {
        startFromHidden: true,
        restoreMode: 'half-body',
        container,
        elements: {
            'live2d-canvas': canvas
        }
    });

    assert.equal(session.start(), true);
    session.cancel('cancelled');

    assert.equal(session.finished, true);
    assert.equal(session.result, 'cancelled');
    assert.equal(container.style.opacity, '0');
    assert.equal(canvas.style.opacity, '0');
    assert.equal(container.style.transition, 'opacity 900ms ease');
    assert.equal(canvas.style.transition, 'opacity 900ms ease');
});

test('Live2D non-opening corner peek uses one compositor layer for the full probe cycle', () => {
    const canvas = { style: { opacity: '1', transition: 'opacity 900ms ease' } };
    const container = { style: { opacity: '1', transition: 'opacity 900ms ease' } };
    const { session, model } = createCornerPeekSession('top-left', {
        hideMs: 1500,
        appearMs: 2000,
        holdMs: 2500,
        useCompositorOpacity: true,
        container,
        elements: {
            'live2d-canvas': canvas
        }
    });
    model.alpha = 1;

    assert.equal(session.totalDurationMs, 3500);
    assert.equal(session.holdMs, 2500);
    assert.equal(session.exitDurationMs, 3500);
    assert.equal(session.start(), true);
    container.style.transition = '';
    canvas.style.transition = '';

    session.tickEnter(750);
    assert.equal(model.alpha, 1);
    assert.equal(container.style.opacity, '0.5');
    assert.equal(canvas.style.opacity, '1');
    assert.equal(container.style.transition, 'none');
    assert.equal(canvas.style.transition, 'none');

    session.tickEnter(2500);
    assert.notEqual(model.x, session.initialModelFrame.x);
    assert.notEqual(model.y, session.initialModelFrame.y);
    assert.ok(Number(container.style.opacity) > 0);
    assert.ok(Number(container.style.opacity) < 1);
    assert.equal(canvas.style.opacity, '1');

    session.tickExit(750);
    assert.equal(model.alpha, 1);
    assert.equal(container.style.opacity, '0.5');
    assert.equal(canvas.style.opacity, '1');

    session.tickExit(1750);
    assert.equal(model.x, session.initialModelFrame.x);
    assert.equal(model.y, session.initialModelFrame.y);
    assert.ok(Number(container.style.opacity) > 0);
    assert.ok(Number(container.style.opacity) < 1);
    assert.equal(canvas.style.opacity, '1');

    session.tickExit(3501);
    assert.equal(session.finished, true);
    assert.equal(model.x, session.initialModelFrame.x);
    assert.equal(model.y, session.initialModelFrame.y);
    assert.equal(model.alpha, 1);
    assert.equal(container.style.opacity, '1');
    assert.equal(canvas.style.opacity, '1');
    assert.equal(container.style.transition, 'opacity 900ms ease');
    assert.equal(canvas.style.transition, 'opacity 900ms ease');
});

test('Live2D opening frame motion reuses the corner peek compositor fade', () => {
    const { session, model, container, canvas, window } = createFrameMotionSession();

    assert.equal(session.start(), true);
    assert.equal(window.nekoYuiGuideAvatarCornerPeekActive, true);
    const hiddenY = model.y;
    assert.equal(model.alpha, 1);
    assert.equal(container.style.opacity, '0');
    assert.equal(canvas.style.opacity, '1');

    container.style.transition = '';
    canvas.style.transition = '';
    window.__setNow(1000);
    session.tick();

    assert.equal(model.alpha, 1);
    assert.ok(model.y < hiddenY);
    assert.ok(Number(container.style.opacity) > 0);
    assert.ok(Number(container.style.opacity) < 1);
    assert.equal(canvas.style.opacity, '1');
    assert.equal(container.style.transition, 'none');
    assert.equal(canvas.style.transition, 'none');

    window.__setNow(4501);
    session.tick();
    assert.equal(session.finished, true);
    assert.equal(model.alpha, 1);
    assert.equal(model.scale.x, 1.38);
    assert.equal(model.scale.y, 1.38);
    assert.equal(container.style.opacity, '1');
    assert.equal(canvas.style.opacity, '1');
    assert.equal(window.nekoYuiGuideAvatarCornerPeekActive, false);
});

test('Live2D opening frame motion restores its original state when reveal fallback cancels it', () => {
    let cancelled = false;
    const { session, model, container, window } = createFrameMotionSession({
        isCancelled: () => cancelled
    });
    const originalFrame = {
        x: model.x,
        y: model.y,
        scaleX: model.scale.x,
        scaleY: model.scale.y,
        alpha: model.alpha
    };

    assert.equal(session.start(), true);
    cancelled = true;
    session.tick();

    assert.equal(session.finished, true);
    assert.equal(session.result, 'cancelled');
    assert.equal(model.x, originalFrame.x);
    assert.equal(model.y, originalFrame.y);
    assert.equal(model.scale.x, originalFrame.scaleX);
    assert.equal(model.scale.y, originalFrame.scaleY);
    assert.equal(model.alpha, originalFrame.alpha);
    assert.equal(container.style.opacity, '1');
    assert.equal(window.nekoYuiGuideAvatarCornerPeekActive, false);
});

test('Live2D non-opening frame motion restores display opacity when cancelled', async () => {
    const context = createControlledNonOpeningFramePlayback();
    const { model, container, canvas, playback } = context;

    await context.flushFrameAt(1500);
    assert.equal(container.style.opacity, '0');

    context.cancel();
    await context.flushFrameAt(1600);

    const result = await playback;
    assert.equal(result.result, 'cancelled');
    assert.equal(model.alpha, 0.8);
    assert.equal(container.style.opacity, '1');
    assert.equal(canvas.style.opacity, '1');
    assert.equal(container.style.transition, 'opacity 900ms ease');
    assert.equal(canvas.style.transition, 'opacity 900ms ease');
});

test('Live2D non-opening frame motion restores half-body visibility when final fade is cancelled', async () => {
    const context = createControlledNonOpeningFramePlayback();
    const { model, container, canvas, playback } = context;

    await context.flushFrameAt(1500);
    await context.flushFrameAt(6001);
    context.cancel();
    await context.flushFrameAt(6100);

    const result = await playback;
    assert.equal(result.result, 'cancelled');
    assert.equal(result.reason, 'avatar_motion_fade_out_cancelled');
    assert.equal(model.alpha, 1);
    assert.equal(model.scale.x, 1.38);
    assert.equal(model.scale.y, 1.38);
    assert.equal(container.style.opacity, '1');
    assert.equal(canvas.style.opacity, '1');
    assert.equal(container.style.transition, 'opacity 900ms ease');
    assert.equal(canvas.style.transition, 'opacity 900ms ease');
});

test('tutorial frame motion keeps one visible compositor and opening motion skips the fade handoff', () => {
    const frameMotionSource = avatarStageSource
        .split('async function playFrameAvatarMotion(options, preset) {')[1]
        .split('async function playAvatarMotion(options) {')[0];
    assert.match(frameMotionSource, /useCompositorOpacity:\s*true/);
    assert.doesNotMatch(frameMotionSource, /useCompositorOpacity:\s*isOpeningProbe/);
    assert.match(
        frameMotionSource,
        /if\s*\(\s*isOpeningProbe\s*\)\s*\{[\s\S]{0,160}result:\s*'played'/
    );
});

test('Live2D corner peek fades only the model and centers look-at during playback', () => {
    const acquiredLocks = [];
    const releasedLocks = [];
    const paramWrites = [];
    const canvas = { style: {} };
    const container = { style: {} };
    const coreModel = {
        setParameterValueById(id, value) {
            paramWrites.push({ id, value });
        }
    };
    const { session, window } = createCornerPeekSession('bottom-right', {
        container,
        coreModel,
        elements: {
            'live2d-canvas': canvas
        },
        configureWindow(testWindow) {
            testWindow.AvatarPerformance = {
                getDefaultCoordinator() {
                    return {
                        acquire(request) {
                            acquiredLocks.push(request);
                            return { id: 'corner-peek-lock' };
                        },
                        release(sessionRecord, reason) {
                            releasedLocks.push({ sessionRecord, reason });
                            return true;
                        }
                    };
                }
            };
        }
    });

    assert.equal(session.start(), true);
    assert.equal(window.nekoYuiGuideAvatarCornerPeekActive, true);
    assert.equal(window.nekoYuiGuideFaceForwardLock, true);
    assert.deepEqual(Array.from(acquiredLocks[0].capabilities), ['frame', 'lookAt']);
    assert.deepEqual(paramWrites.slice(-4), [
        { id: 'ParamAngleX', value: 0 },
        { id: 'ParamAngleY', value: 0 },
        { id: 'ParamEyeBallX', value: 0 },
        { id: 'ParamEyeBallY', value: 0 }
    ]);

    session.tickEnter(500);
    assert.equal(session.model.alpha, 0.4);
    assert.equal(container.style.opacity, '1');
    assert.equal(canvas.style.opacity, '1');

    session.finish('test_complete');
    assert.equal(window.nekoYuiGuideAvatarCornerPeekActive, false);
    assert.equal(window.nekoYuiGuideFaceForwardLock, false);
    assert.equal(container.style.opacity, '');
    assert.equal(canvas.style.opacity, '');
    assert.equal(releasedLocks.length, 1);
});

test('Live2D corner peek disables opacity transitions while it owns the visible layer', () => {
    const canvas = { style: { opacity: '1', transition: 'opacity 0.28s ease' } };
    const container = { style: { opacity: '1', transition: 'opacity 0.28s ease' } };
    const { session } = createCornerPeekSession('bottom-right', {
        container,
        elements: {
            'live2d-canvas': canvas
        }
    });

    assert.equal(session.start(), true);
    session.tickEnter(500);

    assert.equal(container.style.opacity, '1');
    assert.equal(canvas.style.opacity, '1');
    assert.equal(container.style.transition, 'none');
    assert.equal(canvas.style.transition, 'none');

    session.finish('test_complete');
    assert.equal(container.style.opacity, '1');
    assert.equal(canvas.style.opacity, '1');
    assert.equal(container.style.transition, 'opacity 0.28s ease');
    assert.equal(canvas.style.transition, 'opacity 0.28s ease');
});

test('Live2D corner peek does not self-cancel its return fade after stand-in token changes', () => {
    let cancelled = false;
    const { session, model, window } = createCornerPeekSession('bottom-right', {
        isCancelled: () => cancelled
    });

    assert.equal(session.start(), true);
    window.__setNow(2500);
    session.tick();
    assert.equal(session.phase, 'hold');

    session.stop('avatar_standin_clear');
    cancelled = true;
    window.__setNow(3000);
    session.tick();

    assert.equal(session.phase, 'exit');
    assert.equal(model.x, session.cornerFrame.x);
    assert.equal(model.y, session.cornerFrame.y);
    assert.equal(model.alpha, 0.5);
});

test('Live2D visibility recovery preserves opacity while avatar corner peek is active', () => {
    const showLive2dSource = modelDisplaySource
        .split('I.showLive2d = function showLive2d() {')[1]
        .split('I.mod.showLive2d = I.showLive2d;')[0];
    const wakeupRevealSource = wakeupSource
        .split('function revealPreparedTutorialLive2D(reason) {')[1]
        .split('function normalizeDuration(value, fallback) {')[0];
    const restoreDisplaySurfaceSource = modelDisplaySource
        .split('function restoreLive2DDisplaySurface(reason) {')[1]
        .split('function activateLive2DRenderForDisplay(reason) {')[0];
    const preparingCommentIndex = showLive2dSource.indexOf('// 教程准备/探身演出期间');
    const preparingFastPathIndex = showLive2dSource.lastIndexOf(
        'if (preserveYuiGuidePreparing || preserveYuiGuideAvatarMotion)',
        preparingCommentIndex
    );
    assert.ok(showLive2dSource.indexOf('if (window._goodbyeHideTimerId)') < preparingFastPathIndex);
    assert.ok(showLive2dSource.indexOf('if (window._returnFadeTimer)') < preparingFastPathIndex);
    assert.match(live2dInitSource, /nekoYuiGuideAvatarCornerPeekActive[\s\S]{0,120}return;/);
    assert.match(live2dInitSource, /nekoYuiGuideLive2dPreparing[\s\S]{0,220}yui-guide-live2d-preparing/);
    assert.match(live2dInitSource, /neko:yui-guide:live2d-prepared-revealed/);
    assert.match(live2dInitSource, /new MutationObserver\(revealAfterPreparing\)/);
    assert.match(wakeupRevealSource, /window\.nekoYuiGuideLive2dPreparing = false;/);
    assert.ok(
        wakeupRevealSource.indexOf('window.nekoYuiGuideLive2dPreparing = false;')
        < wakeupRevealSource.indexOf('neko:yui-guide:live2d-prepared-revealed')
    );
    assert.match(appUiSource, /preserveAvatarCornerPeekOpacity[\s\S]{0,240}model\.alpha = 1;/);
    assert.match(appInterpageSource, /preserveAvatarCornerPeekOpacity[\s\S]{0,240}currentModel\.alpha = 1;/);
    assert.match(universalManagerSource, /preserveAvatarCornerPeekOpacity[\s\S]{0,360}restoreTutorialLive2dDisplayState/);
    assert.match(universalManagerSource, /preserveOpacity[\s\S]{0,360}live2dCanvas\.style\.setProperty\('opacity', '1', 'important'\)/);
    assert.match(universalManagerSource, /preserveAvatarMotionOpacity[\s\S]{0,500}id !== 'live2d-container'[\s\S]{0,120}id !== 'live2d-canvas'/);
    assert.match(
        restoreDisplaySurfaceSource,
        /if \(!preserveYuiGuidePreparing && !preserveAvatarCornerPeekOpacity\) \{\s*restoreYuiGuideLive2DPreparingControls\(\);/
    );
});

test('daily opening reveal keeps a bounded fallback while preserving explicit overrides', () => {
    assert.match(directorSource, /normalizedOptions\.revealReadyFallbackMs/);
    assert.match(directorSource, /resolveOnReveal \? 3000/);
    assert.match(directorSource, /daily-intro-avatar-reveal-timeout/);
});

test('cancelled opening corner peek stays hidden when the Live2D context is unavailable', async () => {
    const api = loadAvatarStageApi();
    let revealCount = 0;

    const result = await api.playAvatarMotion({
        preset: 'corner-peek',
        isOpeningProbe: true,
        readyWaitMs: 0,
        isCancelled: () => true,
        revealPrepared() {
            revealCount += 1;
        }
    });

    assert.equal(result.result, 'cancelled');
    assert.equal(result.reason, 'cancelled');
    assert.equal(revealCount, 0);
});

test('Live2D corner peek can continue across scene boundaries until its cue duration ends', () => {
    const showBlock = directorSource
        .split('        showAvatarStandIn(cue, token) {')[1]
        .split('        clearAvatarStandIn(options) {')[0];
    assert.doesNotMatch(showBlock, /sceneRunId !== this\.sceneRunId/);
    assert.match(showBlock, /isCancelled: \(\) => token !== this\.avatarStandInToken[\s\S]*this\.isStopping\(\)[\s\S]*this\.destroyed/);
});

test('Live2D interaction skips cursor focus while YUI face-forward lock is active', () => {
    assert.match(live2dInteractionSource, /nekoYuiGuideFaceForwardLock/);
    assert.match(live2dInteractionSource, /ParamAngleX/);
    assert.match(live2dInteractionSource, /ParamEyeBallY/);
    assert.match(live2dInteractionSource, /isYuiGuideFaceForwardLocked[\s\S]*model\.focus\(pointer\.x,\s*pointer\.y\)/);
});

test('top-left Live2D corner peek keeps enough of the model visible', () => {
    const { session } = createCornerPeekSession('top-left');
    const visibleWidth = Math.min(1024, session.cornerFrame.x + 150) - Math.max(0, session.cornerFrame.x - 150);
    const visibleHeight = Math.min(768, session.cornerFrame.y + 300) - Math.max(0, session.cornerFrame.y - 300);

    assert.ok(visibleWidth >= 120);
    assert.ok(visibleHeight >= 240);
});

test('Live2D corner peek uses corner-specific head-first rotation angles', () => {
    const expectedDegrees = {
        'bottom-right': -45,
        'bottom-left': 45,
        'top-right': -135,
        'top-left': 135
    };

    Object.keys(expectedDegrees).forEach((position) => {
        const { session } = createHeadAnchoredCornerPeekSession(position);
        const degrees = Math.round(session.cornerFrame.rotation * 180 / Math.PI);
        assert.equal(degrees, expectedDegrees[position], position);
    });
});

function transformRectFromFrame(rect, fromFrame, toFrame) {
    const rotation = (toFrame.rotation || 0) - (fromFrame.rotation || 0);
    const cos = Math.cos(rotation);
    const sin = Math.sin(rotation);
    const points = [
        { x: rect.x, y: rect.y },
        { x: rect.x + rect.width, y: rect.y },
        { x: rect.x, y: rect.y + rect.height },
        { x: rect.x + rect.width, y: rect.y + rect.height }
    ].map((point) => {
        const dx = point.x - fromFrame.x;
        const dy = point.y - fromFrame.y;
        return {
            x: toFrame.x + dx * cos - dy * sin,
            y: toFrame.y + dx * sin + dy * cos
        };
    });
    const xs = points.map((point) => point.x);
    const ys = points.map((point) => point.y);
    const left = Math.min(...xs);
    const top = Math.min(...ys);
    const right = Math.max(...xs);
    const bottom = Math.max(...ys);
    return {
        left,
        top,
        right,
        bottom,
        width: right - left,
        height: bottom - top
    };
}

function intersectionArea(rect, viewport) {
    const left = Math.max(rect.left, viewport.left);
    const top = Math.max(rect.top, viewport.top);
    const right = Math.min(rect.right, viewport.right);
    const bottom = Math.min(rect.bottom, viewport.bottom);
    return Math.max(0, right - left) * Math.max(0, bottom - top);
}

test('Live2D corner peek anchors the head and upper chest from every corner', () => {
    const viewport = { left: 0, top: 0, right: 1024, bottom: 768 };
    for (const position of ['bottom-right', 'bottom-left', 'top-right', 'top-left']) {
        const { session } = createHeadAnchoredCornerPeekSession(position);
        const visiblePeekRect = transformRectFromFrame(
            session.peekRegionBounds,
            session.initialModelFrame,
            session.cornerFrame
        );
        const visibleModelRect = transformRectFromFrame(
            session.initialBounds,
            session.initialModelFrame,
            session.cornerFrame
        );
        const peekArea = visiblePeekRect.width * visiblePeekRect.height;
        const modelArea = visibleModelRect.width * visibleModelRect.height;
        const peekVisibleRatio = intersectionArea(visiblePeekRect, viewport) / peekArea;
        const modelVisibleRatio = intersectionArea(visibleModelRect, viewport) / modelArea;

        assert.ok(peekVisibleRatio >= 0.8, position);
        assert.ok(modelVisibleRatio <= 0.62, position);
        if (position === 'top-left' || position === 'top-right') {
            assert.ok(visiblePeekRect.top >= 40, position);
            assert.ok(visiblePeekRect.top <= 80, position);
            assert.ok(visiblePeekRect.bottom < 360, position);
        } else {
            assert.ok(visiblePeekRect.bottom >= 688, position);
            assert.ok(visiblePeekRect.bottom <= 728, position);
            assert.ok(visiblePeekRect.top > 400, position);
        }
        if (position === 'top-left' || position === 'bottom-left') {
            assert.ok(visiblePeekRect.left >= 40, position);
            assert.ok(visiblePeekRect.left <= 80, position);
        } else {
            assert.ok(visiblePeekRect.right >= 944, position);
            assert.ok(visiblePeekRect.right <= 984, position);
        }
    }
});
